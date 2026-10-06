#!/usr/bin/env python3
# TeX Templates
# Copyright (C) 2026 Giulio Salvi
#
# This is free software: you can redistribute it and/or modify it under the
# terms of the GNU General Public License as published by the Free Software
# Foundation, either version 3 of the License, or (at your option) any later
# version. This software is distributed WITHOUT ANY WARRANTY; without even
# the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.
# You should have received a copy of the GNU General Public License along with
# this program. If not, see <https://www.gnu.org/licenses/>.
#
# SPDX-FileCopyrightText: 2026 Giulio Salvi
# SPDX-License-Identifier: GPL-3.0-or-later

"""Prepare Docker inputs from published assets; delegate registry publication."""
from __future__ import annotations

import argparse
import base64
import hashlib
import importlib.util
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile


def load_module(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(filename))
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


release = load_module("library_release", "library-release.py")
SLUG = re.compile(r"[A-Za-z0-9-]+/[A-Za-z0-9_.-]+\Z")


def validate_lock(lock: dict, version: str, commit: str) -> list[dict]:
    expected = {"schema": 1, "library": "tex-templates", "version": version,
                "commit": commit, "preview": False}
    if (not isinstance(lock, dict) or type(lock.get("schema")) is not int or lock.get("preview") is not False
            or any(lock.get(key) != value for key, value in expected.items())):
        raise release.ReleaseError("The release lock does not match the published library tag.")
    records = lock.get("templates")
    if not isinstance(records, list) or not records:
        raise release.ReleaseError("The released library contains no template records.")
    seen = set()
    for record in records:
        if not isinstance(record, dict):
            raise release.ReleaseError("Invalid template record in the release lock.")
        for field, pattern in (("id", release.ID), ("repository", SLUG),
                               ("commit", release.COMMIT), ("sha256", release.SHA256),
                               ("tag", release.VERSION)):
            if not isinstance(record.get(field), str) or not pattern.fullmatch(record[field]):
                raise release.ReleaseError(f"Invalid template {field} in the release lock.")
        if record["id"] in seen or not record["tag"].startswith("v"):
            raise release.ReleaseError("Duplicate template ID or invalid release tag.")
        seen.add(record["id"])
        child_version = release.version_text(release.version(record["tag"]))
        if record.get("archive") != f'{record["id"]}-{child_version}.tar.gz':
            raise release.ReleaseError("Template archive name does not match its version.")
    return records


def verify_assets(directory: Path, version: str, commit: str) -> tuple[Path, list[dict], str]:
    archive = directory / f"tex-templates-{version}.tar.gz"
    lock_path = directory / "templates.lock.json"
    sums = {}
    for line in (directory / "SHA256SUMS").read_text(encoding="utf-8").splitlines():
        match = re.fullmatch(r"([0-9a-f]{64})  ([A-Za-z0-9_.-]+)", line)
        if not match or match[2] in sums:
            raise release.ReleaseError("Invalid or duplicate entry in release SHA256SUMS.")
        sums[match[2]] = match[1]
    if set(sums) != {archive.name, lock_path.name}:
        raise release.ReleaseError("Release SHA256SUMS must cover its archive and lock.")
    for path in (archive, lock_path):
        if release.file_hash(path) != sums[path.name]:
            raise release.ReleaseError(f"Release checksum mismatch: {path.name}.")
    lock = json.loads(lock_path.read_text(encoding="utf-8"))
    records = validate_lock(lock, version, commit)
    extracted = directory / "expanded"
    release.safe_extract(archive, extracted, "tex-templates")
    tree = extracted / "tex-templates"
    if json.loads((tree / "templates.lock.json").read_text()) != lock:
        raise release.ReleaseError("Embedded and published release locks differ.")
    allowed = {"bin", "README.md", "LICENSE", "requirements.txt", "templates.lock.json", "templates"}
    if {item.name for item in tree.iterdir()} != allowed:
        raise release.ReleaseError("The library archive contains unexpected source material.")
    if {item.name for item in (tree / "templates").iterdir()} != {item["id"] for item in records}:
        raise release.ReleaseError("Installed templates do not match the release lock.")
    for record in records:
        release.validate_template(tree / "templates" / record["id"])
    for name in ("generate-pdf.sh", "template-list.sh", "template-use.sh"):
        script = tree / "bin" / name
        if not script.is_file() or not script.stat().st_mode & 0o111:
            raise release.ReleaseError(f"Missing executable library command: {name}.")
    original_requirements = (tree / "requirements.txt").read_bytes()
    release.aggregate_requirements(tree)
    if (tree / "requirements.txt").read_bytes() != original_requirements:
        raise release.ReleaseError("Combined requirements differ from the template requirements.")
    return tree, records, sums[archive.name]


def fixture(client, record: dict, destination: Path, scratch: Path) -> None:
    repository, commit = record["repository"], record["commit"]
    if client.tag_commit(repository, record["tag"]) != commit:
        raise release.ReleaseError("The child release tag no longer matches the library lock.")
    # GitHub source archives honor export-ignore, which deliberately excludes
    # usage_example. Read the committed tree/blobs directly instead.
    tree = client.api(f"repos/{repository}/git/trees/{commit}?recursive=1")
    if tree.get("truncated") is not False or not isinstance(tree.get("tree"), list):
        raise release.ReleaseError("Cannot read the complete pinned template source tree.")
    example = scratch / f'{record["id"]}-example'
    seen = set()
    for item in tree["tree"]:
        name = item.get("path", "")
        if not isinstance(name, str) or not name.startswith("usage_example/") or item.get("type") == "tree":
            continue
        path = PurePosixPath(name)
        if (".." in path.parts or "\\" in name or path.is_absolute() or name in seen
                or item.get("type") != "blob" or item.get("mode") not in ("100644", "100755")
                or not isinstance(item.get("sha"), str) or not release.COMMIT.fullmatch(item["sha"])):
            raise release.ReleaseError("Unsafe file in the pinned usage_example tree.")
        seen.add(name)
        blob = client.api(f'repos/{repository}/git/blobs/{item["sha"]}')
        if blob.get("encoding") != "base64":
            raise release.ReleaseError("Expected a base64-encoded Git fixture blob.")
        contents = base64.b64decode("".join(blob["content"].split()), validate=True)
        header = f"blob {len(contents)}\0".encode()
        if hashlib.sha1(header + contents).hexdigest() != item["sha"]:
            raise release.ReleaseError("Fixture contents do not match their committed Git blob.")
        target = example.joinpath(*path.parts[1:])
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(contents)
        target.chmod(0o755 if item["mode"] == "100755" else 0o644)
    if not example.is_dir() or not any((example / name).is_file() for name in
                                      ("pandoc.yaml", "pandoc.yml", "defaults.yaml", "defaults.yml")):
        raise release.ReleaseError(f'Template {record["id"]} has no usable usage_example fixture.')
    shutil.copytree(example, destination / record["id"] / "usage_example")


def recipe_hash(docker_dir: Path) -> str:
    digest = hashlib.sha256()
    for name in ("Dockerfile", "install-texlive.sh", ".dockerignore"):
        digest.update(name.encode() + b"\0" + (docker_dir / name).read_bytes() + b"\0")
    return digest.hexdigest()


def prepare(version: str, output: Path, repository: str, github_output: Path | None = None,
            client=None, docker_dir: Path | None = None) -> dict:
    version = release.version_text(release.version(version))
    if not SLUG.fullmatch(repository):
        raise release.ReleaseError("Expected a GitHub owner/repository name.")
    output = output.absolute()
    if output.is_symlink() or (output.exists() and (not output.is_dir() or any(output.iterdir()))):
        raise release.ReleaseError("The prepared context destination must be empty.")
    output.parent.mkdir(parents=True, exist_ok=True)
    docker_dir = docker_dir or Path(__file__).resolve().parents[1] / "docker"
    client = client or release.GitHub()
    tag = f"v{version}"
    published = client.api(f"repos/{repository}/releases/tags/{tag}")
    if published.get("draft") is not False or published.get("prerelease") is not False or published.get("tag_name") != tag:
        raise release.ReleaseError("Docker images require a published stable library release.")
    commit = client.tag_commit(repository, tag)
    with tempfile.TemporaryDirectory(prefix="docker-release-", dir=output.parent) as temporary:
        scratch = Path(temporary)
        downloads = scratch / "downloads"
        for name in (f"tex-templates-{version}.tar.gz", "templates.lock.json", "SHA256SUMS"):
            client.download(repository, tag, name, downloads)
        tree, records, archive_sha256 = verify_assets(downloads, version, commit)
        context = scratch / "context"
        context.mkdir()
        shutil.move(str(tree), context / "library")
        for name in ("Dockerfile", "install-texlive.sh", ".dockerignore"):
            shutil.copy2(docker_dir / name, context / name)
        for record in records:
            fixture(client, record, context / "fixtures", scratch)
        metadata = {"version": version, "tag": tag, "commit": commit,
                    "archive_sha256": archive_sha256, "recipe_sha256": recipe_hash(docker_dir)}
        (context / "metadata.json").write_bytes(release.json_bytes(metadata))
        if output.exists():
            output.rmdir()
        context.rename(output)
    if github_output:
        with github_output.open("a", encoding="utf-8") as stream:
            for key, value in metadata.items():
                stream.write(f"{key}={value}\n")
    return metadata


def main() -> None:
    if len(sys.argv) > 1 and sys.argv[1] == "publish-images":
        raise SystemExit(subprocess.call([sys.executable, str(Path(__file__).with_name("docker-images.py")), *sys.argv[2:]]))
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    command = sub.add_parser("prepare")
    command.add_argument("--version", required=True)
    command.add_argument("--output", required=True, type=Path)
    command.add_argument("--repository", default=os.environ.get("GITHUB_REPOSITORY", "GiulioSalvi/TexTemplates"))
    command.add_argument("--github-output", type=Path)
    args = parser.parse_args()
    try:
        result = prepare(args.version, args.output, args.repository, args.github_output)
    except (release.ReleaseError, OSError, ValueError, tarfile.TarError) as error:
        parser.exit(1, f"docker-release: {error}\n")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()

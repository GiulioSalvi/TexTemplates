#!/usr/bin/env python3
# TeX Templates Library
# Copyright (C) 2026 Giulio Salvi
#
# This is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This software is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program. If not, see <https://www.gnu.org/licenses/>.
#
# SPDX-FileCopyrightText: 2026 Giulio Salvi
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

import argparse
from dataclasses import dataclass
import gzip
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile


ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]*\Z")
COMMIT = re.compile(r"[0-9a-f]{40}\Z")
SHA256 = re.compile(r"[0-9a-f]{64}\Z")
VERSION = re.compile(r"v?(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\Z")
SERIES = re.compile(r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\Z")
PACKAGE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*\Z")


class ReleaseError(Exception):
    pass


def run(*args: str, cwd: Path | None = None, binary: bool = False) -> str | bytes:
    result = subprocess.run(args, cwd=cwd, capture_output=True)
    if result.returncode:
        message = result.stderr.decode(errors="replace").strip()
        raise ReleaseError(f"{args[0]} failed: {message or result.returncode}")
    return result.stdout if binary else result.stdout.decode().strip()


def git(repo: Path, *args: str, binary: bool = False) -> str | bytes:
    return run("git", "-C", str(repo), *args, binary=binary)


def json_bytes(value: object) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True) + "\n").encode()


def version(value: str) -> tuple[int, int, int]:
    match = VERSION.fullmatch(value)
    if not match:
        raise ReleaseError(f"Invalid version {value!r}; expected X.Y.Z with no leading zeros.")
    return tuple(int(part) for part in match.groups())


def version_text(value: tuple[int, int, int]) -> str:
    return ".".join(map(str, value))


def repository_slug(url: str) -> str:
    match = re.fullmatch(
        r"(?:https://github\.com/|git@github\.com:|ssh://git@github\.com/)"
        r"([A-Za-z0-9-]+/[A-Za-z0-9_.-]+?)(?:\.git)?/?", url
    )
    if not match:
        raise ReleaseError(f"Expected a GitHub submodule URL, got {url!r}.")
    return match.group(1)


@dataclass(frozen=True)
class Template:
    id: str
    path: str
    url: str
    repository: str
    commit: str


def templates(repo: Path, ref: str = "HEAD") -> list[Template]:
    raw = git(repo, "config", "--blob", f"{ref}:.gitmodules", "--null",
              "--get-regexp", r"^submodule\..*\.path$", binary=True)
    selected = []
    seen = set()
    for record in raw.decode().split("\0"):
        if not record:
            continue
        key, path = record.split("\n", 1)
        template_id = path.removeprefix("templates/")
        if path != f"templates/{template_id}" or not ID.fullmatch(template_id):
            raise ReleaseError(f"Invalid template submodule path: {path!r}.")
        if template_id in seen:
            raise ReleaseError(f"Duplicate template ID: {template_id}.")
        seen.add(template_id)
        name = key[len("submodule."):-len(".path")]
        url = git(repo, "config", "--blob", f"{ref}:.gitmodules", "--get",
                  f"submodule.{name}.url")
        entry = git(repo, "ls-tree", ref, "--", path)
        fields = entry.split("\t", 1)[0].split()
        if len(fields) != 3 or fields[:2] != ["160000", "commit"] or not COMMIT.fullmatch(fields[2]):
            raise ReleaseError(f"No committed gitlink exists at {path}.")
        selected.append(Template(template_id, path, url, repository_slug(url), fields[2]))
    if not selected:
        raise ReleaseError("No template submodules are registered in committed .gitmodules.")
    return sorted(selected, key=lambda item: item.id)


def file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def safe_extract(source: Path | io.BytesIO, destination: Path, prefix: str | None = None) -> None:
    """Extract only directories, regular files, and internal leaf symlinks."""
    options = {"fileobj": source} if isinstance(source, io.BytesIO) else {"name": source}
    with tarfile.open(mode="r:*", **options) as archive:
        members = archive.getmembers()
        paths = {}
        symlinks = set()
        for member in members:
            path = PurePosixPath(member.name)
            if (not member.name or path.is_absolute() or "\\" in member.name
                    or ".." in path.parts or not path.parts
                    or (prefix and path.parts[0] != prefix)):
                raise ReleaseError(f"Unsafe archive path: {member.name!r}.")
            if path in paths:
                raise ReleaseError(f"Duplicate archive path: {member.name!r}.")
            paths[path] = member
            if not (member.isdir() or member.isfile() or member.issym()):
                raise ReleaseError(f"Unsupported archive entry: {member.name!r}.")
            if member.issym():
                if prefix:
                    raise ReleaseError(f"Template archives must not contain symlinks: {member.name!r}.")
                target = PurePosixPath(member.linkname)
                if target.is_absolute() or not member.linkname or "\\" in member.linkname:
                    raise ReleaseError(f"Unsafe symlink target: {member.linkname!r}.")
                resolved = list(path.parent.parts)
                boundary = 1 if prefix else 0
                for part in target.parts:
                    if part == "..":
                        if len(resolved) <= boundary:
                            raise ReleaseError(f"Symlink leaves its archive root: {member.name!r}.")
                        resolved.pop()
                    elif part != ".":
                        resolved.append(part)
                if prefix and (not resolved or resolved[0] != prefix):
                    raise ReleaseError(f"Symlink leaves its template root: {member.name!r}.")
                symlinks.add(path)
        for path in paths:
            if any(parent in symlinks for parent in path.parents):
                raise ReleaseError(f"Archive writes through a symlink: {str(path)!r}.")
        for path, member in sorted(paths.items(), key=lambda pair: (len(pair[0].parts), str(pair[0]))):
            if member.issym():
                continue
            target = destination.joinpath(*path.parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            if member.isdir():
                target.mkdir(exist_ok=True)
            else:
                stream = archive.extractfile(member)
                if stream is None:
                    raise ReleaseError(f"Cannot read archive member: {member.name}.")
                with stream, target.open("wb") as output:
                    shutil.copyfileobj(stream, output)
                target.chmod(0o755 if member.mode & 0o111 else 0o644)
        for path in sorted(symlinks, key=str):
            target = destination.joinpath(*path.parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.symlink_to(paths[path].linkname)
        for path in symlinks:
            target = destination.joinpath(*path.parts)
            try:
                resolved = target.resolve(strict=True)
            except (OSError, RuntimeError) as error:
                raise ReleaseError(f"Unresolvable archive symlink: {str(path)!r}.") from error
            if not resolved.is_relative_to(destination.resolve()):
                raise ReleaseError(f"Archive symlink resolves outside its root: {str(path)!r}.")


def validate_template(directory: Path) -> None:
    for name in ("template.tex", "requirements.txt", "README.md", "LICENSE"):
        if not (directory / name).is_file():
            raise ReleaseError(f"Template release is missing {directory.name}/{name}.")
    if not any((directory / name).is_file() for name in ("defaults.yaml", "defaults.yml")):
        raise ReleaseError(f"Template release is missing defaults.yaml/defaults.yml: {directory.name}.")
    excluded = (".git", ".gitignore", ".gitattributes", ".github", "scripts", "tests", "usage_example")
    for name in excluded:
        if (directory / name).exists() or (directory / name).is_symlink():
            raise ReleaseError(f"Template release contains excluded source material: {directory.name}/{name}.")


class GitHub:
    def __init__(self):
        self.release_cache = {}
        self.commit_cache = {}

    def api(self, endpoint: str) -> object:
        return json.loads(run("gh", "api", endpoint))

    def status(self, endpoint: str) -> dict | None:
        response = subprocess.run(["gh", "api", "--include", endpoint], capture_output=True,
                                  text=True, encoding="utf-8", errors="replace")
        headers, separator, body = response.stdout.partition("\n\n")
        first_line = headers.splitlines()[0] if headers else ""
        match = re.fullmatch(r"HTTP/\S+ (\d{3})(?: .*)?", first_line)
        if not match:
            raise ReleaseError(f"Cannot determine GitHub HTTP status: {response.stderr.strip()}")
        if match.group(1) == "404":
            return None
        if match.group(1) != "200" or response.returncode or not separator:
            raise ReleaseError(f"GitHub API request failed (HTTP {match.group(1)}): {response.stderr.strip()}")
        return json.loads(body)

    def releases(self, repository: str, include_drafts: bool = False) -> list[dict]:
        if repository not in self.release_cache:
            pages = json.loads(run("gh", "api", f"repos/{repository}/releases?per_page=100",
                                   "--paginate", "--slurp"))
            self.release_cache[repository] = [release for page in pages for release in page]
        return [item for item in self.release_cache[repository]
                if not item["prerelease"] and (include_drafts or not item["draft"])]

    def tag_commit(self, repository: str, tag: str) -> str:
        key = (repository, tag)
        if key not in self.commit_cache:
            obj = self.api(f"repos/{repository}/git/ref/tags/{tag}")["object"]
            for _ in range(16):
                if obj["type"] == "commit":
                    break
                if obj["type"] != "tag":
                    raise ReleaseError(f"Tag {repository}@{tag} does not point to a commit.")
                obj = self.api(f"repos/{repository}/git/tags/{obj['sha']}")["object"]
            else:
                raise ReleaseError(f"Excessive annotated-tag nesting: {repository}@{tag}.")
            if not COMMIT.fullmatch(obj["sha"]):
                raise ReleaseError(f"Invalid commit for {repository}@{tag}.")
            self.commit_cache[key] = obj["sha"]
        return self.commit_cache[key]

    def matching_release(self, template: Template, required: bool = True) -> dict | None:
        releases = [item for item in self.releases(template.repository)
                    if item["tag_name"].startswith("v") and VERSION.fullmatch(item["tag_name"])]
        for release in sorted(releases, key=lambda item: version(item["tag_name"]), reverse=True):
            if self.tag_commit(template.repository, release["tag_name"]) == template.commit:
                return release
        if required:
            raise ReleaseError(f"No published template release matches {template.id} at {template.commit}. "
                               "Publish a version tag and its archive/JSON assets in the child repository first.")
        return None

    def download(self, repository: str, tag: str, name: str, destination: Path) -> Path:
        destination.mkdir(parents=True, exist_ok=True)
        run("gh", "release", "download", tag, "--repo", repository, "--pattern", name,
            "--dir", str(destination))
        path = destination / name
        if not path.is_file() or path.is_symlink():
            raise ReleaseError(f"Missing release asset: {repository}@{tag}/{name}.")
        return path

    def payload(self, template: Template, release: dict, destination: Path) -> tuple[Path, dict]:
        tag = release["tag_name"]
        suffix = version_text(version(tag))
        archive_name = f"{template.id}-{suffix}.tar.gz"
        metadata_name = f"{template.id}-{suffix}.json"
        names = {asset["name"] for asset in release["assets"]}
        if not {archive_name, metadata_name, "SHA256SUMS"}.issubset(names):
            raise ReleaseError(f"Release assets are not ready for {template.repository}@{tag}; "
                               "retry after the child packaging workflow completes.")
        metadata_file = self.download(template.repository, tag, metadata_name, destination)
        metadata = json.loads(metadata_file.read_text())
        expected = {"schema": 1, "id": template.id, "repository": template.repository,
                    "tag": tag, "commit": template.commit, "archive": archive_name}
        if type(metadata.get("schema")) is not int or any(metadata.get(key) != value for key, value in expected.items()):
            raise ReleaseError(f"Release metadata does not match the selected gitlink: {template.id}@{tag}.")
        if not isinstance(metadata.get("sha256"), str) or not SHA256.fullmatch(metadata["sha256"]):
            raise ReleaseError(f"Invalid archive SHA256 in {metadata_name}.")
        if self.tag_commit(template.repository, tag) != template.commit:
            raise ReleaseError(f"Release tag does not match the selected gitlink: {template.id}@{tag}.")
        archive = self.download(template.repository, tag, archive_name, destination)
        if file_hash(archive) != metadata["sha256"]:
            raise ReleaseError(f"Archive SHA256 mismatch: {template.id}@{tag}.")
        sums = self.download(template.repository, tag, "SHA256SUMS", destination).read_text()
        checksums = {}
        for line in sums.splitlines():
            fields = line.split(maxsplit=1)
            if len(fields) == 2:
                checksums[fields[1].lstrip(" *")] = fields[0]
        if checksums.get(archive_name) != metadata["sha256"] or checksums.get(metadata_name) != file_hash(metadata_file):
            raise ReleaseError(f"SHA256SUMS does not match the archive and metadata: {template.id}@{tag}.")
        return archive, expected | {"sha256": metadata["sha256"]}


def base_tree(repo: Path, destination: Path, ref: str = "HEAD") -> None:
    # git archive honors the committed .gitattributes. Submodules are expanded
    # separately from verified release assets, never from the source checkout.
    paths = ["bin", "README.md", "LICENSE"]
    # Historical releases/drafts predate shared filters. Export them only when
    # tracked in the selected commit, never from the current working tree.
    if git(repo, "ls-tree", "--name-only", ref, "--", "filters"):
        paths.append("filters")
    contents = git(repo, "archive", "--format=tar", ref, "--", *paths, binary=True)
    safe_extract(io.BytesIO(contents), destination)
    for name in ("bin", "README.md", "LICENSE"):
        if not (destination / name).exists():
            raise ReleaseError(f"The committed parent distribution is missing {name}.")
    if "filters" in paths and not (destination / "filters" / "include.lua").is_file():
        raise ReleaseError("The committed library is missing filters/include.lua.")


def aggregate_requirements(tree: Path) -> None:
    packages = set()
    for path in sorted((tree / "templates").glob("*/requirements.txt")):
        for line in path.read_text().splitlines():
            value = line.partition("#")[0].strip()
            if not value:
                continue
            if not PACKAGE.fullmatch(value):
                raise ReleaseError(f"Invalid TeX Live package name in {path}: {value!r}.")
            packages.add(value)
    (tree / "requirements.txt").write_text("# Combined TeX Live / tlmgr package names.\n" +
                                           "".join(f"{name}\n" for name in sorted(packages)))


def fingerprint(tree: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(tree.rglob("*"), key=lambda item: item.relative_to(tree).as_posix()):
        if path.is_dir() and not path.is_symlink():
            continue
        relative = path.relative_to(tree).as_posix()
        if path.is_symlink():
            description = [relative, "symlink", os.readlink(path)]
        else:
            description = [relative, "executable" if path.stat().st_mode & 0o111 else "file", file_hash(path)]
        digest.update(json_bytes(description))
    return digest.hexdigest()


def pack(tree: Path, archive_path: Path, epoch: int) -> None:
    with archive_path.open("wb") as raw:
        with gzip.GzipFile(filename="", fileobj=raw, mode="wb", mtime=epoch) as compressed:
            with tarfile.open(fileobj=compressed, mode="w", format=tarfile.PAX_FORMAT) as archive:
                for path in [tree, *sorted(tree.rglob("*"), key=lambda item: item.relative_to(tree).as_posix())]:
                    relative = path.relative_to(tree).as_posix()
                    name = "tex-templates" if relative == "." else f"tex-templates/{relative}"
                    info = archive.gettarinfo(str(path), arcname=name)
                    info.uid = info.gid = 0
                    info.uname = info.gname = ""
                    info.mtime = epoch
                    info.pax_headers = {}
                    info.mode = 0o755 if info.isdir() or (info.isfile() and info.mode & 0o111) else 0o644
                    if info.isfile():
                        with path.open("rb") as stream:
                            archive.addfile(info, stream)
                    else:
                        archive.addfile(info)


def assemble(repo: Path, output: Path, chosen_version: str, preview: bool = False,
             source_commit: str | None = None) -> dict:
    chosen_version = version_text(version(chosen_version))
    output = output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    if source_commit and not COMMIT.fullmatch(source_commit):
        raise ReleaseError("--source-commit must be a full lowercase 40-character commit.")
    ref = source_commit or "HEAD"
    commit = git(repo, "rev-parse", f"{ref}^{{commit}}")
    client = GitHub()
    with tempfile.TemporaryDirectory(prefix="tex-library-") as scratch:
        scratch_path = Path(scratch)
        tree = scratch_path / "library"
        tree.mkdir()
        base_tree(repo, tree, ref)
        records = []
        for template in templates(repo, ref):
            if preview:
                source = repo / template.path
                contents = git(source, "archive", "--format=tar", f"--prefix={template.id}/", template.commit, binary=True)
                archive = io.BytesIO(contents)
                record = {"id": template.id, "repository": template.repository,
                          "commit": template.commit, "tag": None, "source_preview": True}
            else:
                release = client.matching_release(template)
                archive, record = client.payload(template, release, scratch_path / "downloads" / template.id)
            safe_extract(archive, tree / "templates", template.id)
            validate_template(tree / "templates" / template.id)
            records.append(record)
        aggregate_requirements(tree)
        collection_fingerprint = hashlib.sha256(json_bytes({
            "runtime": fingerprint(tree), "templates": records,
        })).hexdigest()
        lock = {"schema": 1, "library": "tex-templates", "version": chosen_version,
                "commit": commit, "preview": preview,
                "fingerprint": collection_fingerprint, "templates": records}
        lock_path = output / "templates.lock.json"
        lock_path.write_bytes(json_bytes(lock))
        (tree / "templates.lock.json").write_bytes(json_bytes(lock))
        archive_path = output / f"tex-templates-{chosen_version}.tar.gz"
        epoch = int(git(repo, "show", "-s", "--format=%ct", ref))
        pack(tree, archive_path, epoch)
        sums_path = output / "SHA256SUMS"
        sums_path.write_text(f"{file_hash(archive_path)}  {archive_path.name}\n"
                             f"{file_hash(lock_path)}  {lock_path.name}\n")
    return {"package_path": str(archive_path), "lock_path": str(lock_path),
            "checksums_path": str(sums_path), "fingerprint": lock["fingerprint"], "version": chosen_version}


def update(repo: Path, event_path: Path) -> dict:
    if git(repo, "status", "--porcelain", "--untracked-files=no"):
        raise ReleaseError("The parent checkout must be clean before updating a template.")
    event = json.loads(event_path.read_text())
    payload = event.get("client_payload", {})
    template_id, tag, commit = (payload.get(key) for key in ("id", "tag", "commit"))
    if not isinstance(template_id, str) or not ID.fullmatch(template_id):
        raise ReleaseError("Dispatch payload has an invalid template ID.")
    if not isinstance(tag, str) or not tag.startswith("v") or not VERSION.fullmatch(tag):
        raise ReleaseError("Dispatch payload must contain a stable vX.Y.Z tag.")
    if not isinstance(commit, str) or not COMMIT.fullmatch(commit):
        raise ReleaseError("Dispatch payload must contain the full lowercase 40-character commit.")
    selected = next((item for item in templates(repo) if item.id == template_id), None)
    if selected is None:
        raise ReleaseError(f"Dispatch template is not registered in .gitmodules: {template_id}.")
    client = GitHub()
    release = next((item for item in client.releases(selected.repository) if item["tag_name"] == tag), None)
    if release is None:
        raise ReleaseError(f"The dispatched release is not published: {selected.repository}@{tag}.")
    candidate = Template(selected.id, selected.path, selected.url, selected.repository, commit)
    with tempfile.TemporaryDirectory(prefix="tex-dispatch-") as scratch:
        archive, _ = client.payload(candidate, release, Path(scratch) / "assets")
        safe_extract(archive, Path(scratch) / "payload", candidate.id)
        validate_template(Path(scratch) / "payload" / candidate.id)
    result = {"changed": commit != selected.commit, "template": template_id, "tag": tag, "commit": commit}
    if commit == selected.commit:
        return result
    previous = client.matching_release(selected, required=False)
    source = repo / selected.path
    if not (source / ".git").exists():
        raise ReleaseError("Initialize submodules with git submodule update --init before processing a dispatch.")
    if git(source, "rev-parse", "--is-shallow-repository") == "true":
        git(source, "fetch", "--unshallow", "--no-recurse-submodules", selected.url)
    git(source, "fetch", "--no-tags", "--no-recurse-submodules", selected.url, f"refs/tags/{tag}")
    fetched = git(source, "rev-parse", "FETCH_HEAD^{commit}")
    if fetched != commit:
        raise ReleaseError("The fetched release tag differs from the dispatch commit.")
    if previous and version(tag) <= version(previous["tag_name"]):
        stale = subprocess.run(["git", "-C", str(source), "merge-base", "--is-ancestor", commit,
                                selected.commit], capture_output=True)
        if stale.returncode == 0:
            return result | {"changed": False, "ignored": True,
                             "reason": f"Stale notification; {previous['tag_name']} is already selected."}
        if stale.returncode != 1:
            raise ReleaseError(f"Cannot check stale notification ancestry: {stale.stderr.decode().strip()}")
        raise ReleaseError(f"Refusing a template version downgrade: {previous['tag_name']} -> {tag}.")
    ancestry = subprocess.run(["git", "-C", str(source), "merge-base", "--is-ancestor", selected.commit, commit], capture_output=True)
    if ancestry.returncode == 1:
        raise ReleaseError("Refusing a non-descendant template update; review and commit this change manually.")
    if ancestry.returncode:
        raise ReleaseError(f"Cannot check template ancestry: {ancestry.stderr.decode().strip()}")
    git(source, "checkout", "--detach", commit)
    git(repo, "add", "--", selected.path)
    return result


def release_series(repo: Path) -> tuple[int, int] | None:
    # Use the selected commit, just as assembly does. Working-tree edits and
    # untracked files must not change the version of a committed collection.
    if not git(repo, "ls-tree", "--name-only", "HEAD", "--", "release-series"):
        return None
    value = git(repo, "show", "HEAD:release-series")
    match = SERIES.fullmatch(value)
    if not match:
        raise ReleaseError(f"Invalid release-series {value!r}; expected X.Y with no leading zeros.")
    return tuple(int(part) for part in match.groups())


def next_version(repo: Path, override: str | None) -> str:
    existing = [version(tag) for tag in git(repo, "tag", "--list").splitlines() if VERSION.fullmatch(tag)]
    highest = max(existing) if existing else None
    if override:
        chosen = version(override)
        if highest and chosen <= highest:
            raise ReleaseError(f"Version must be greater than {version_text(highest)}.")
    else:
        selected = release_series(repo)
        if selected is not None and highest and selected < highest[:2]:
            raise ReleaseError(f"Configured release-series {selected[0]}.{selected[1]} is older than "
                               f"the highest tagged series {highest[0]}.{highest[1]}.")
        if selected is not None and (highest is None or selected > highest[:2]):
            chosen = (*selected, 0)
        elif highest:
            chosen = (highest[0], highest[1], highest[2] + 1)
        else:
            # Historical source checkouts have no release-series file.
            chosen = (0, 1, 0)
    return version_text(chosen)


def published(repo: Path, lock_path: Path) -> dict:
    lock = json.loads(lock_path.read_text())
    if lock.get("preview") or not SHA256.fullmatch(str(lock.get("fingerprint", ""))):
        raise ReleaseError("Publication checks require a verified, non-preview release lock.")
    repository = os.environ.get("GITHUB_REPOSITORY")
    if not repository:
        repository = repository_slug(git(repo, "remote", "get-url", "origin"))
    client = GitHub()
    with tempfile.TemporaryDirectory(prefix="tex-published-") as scratch:
        draft = None
        for release in client.releases(repository, include_drafts=True):
            if not VERSION.fullmatch(release["tag_name"]):
                continue
            if "templates.lock.json" not in {asset["name"] for asset in release["assets"]}:
                # A draft may have been created before its first asset upload.
                if (release["draft"] and release.get("target_commitish") == lock.get("commit")
                        and COMMIT.fullmatch(str(lock.get("commit", "")))):
                    draft = {"draft_tag": release["tag_name"],
                             "draft_version": version_text(version(release["tag_name"])),
                             "draft_commit": lock["commit"]}
                continue
            path = client.download(repository, release["tag_name"], "templates.lock.json",
                                   Path(scratch) / release["tag_name"])
            previous = json.loads(path.read_text())
            if previous.get("schema") == 1 and previous.get("fingerprint") == lock["fingerprint"]:
                if not release["draft"]:
                    names = {asset["name"] for asset in release["assets"]}
                    expected_archive = f"tex-templates-{version_text(version(release['tag_name']))}.tar.gz"
                    if not {expected_archive, "SHA256SUMS"}.issubset(names):
                        raise ReleaseError(f"Published library release has incomplete assets: {release['tag_name']}.")
                    return {"already_published": True, "published_tag": release["tag_name"]}
                if COMMIT.fullmatch(str(previous.get("commit", ""))):
                    if release.get("target_commitish") != previous["commit"]:
                        raise ReleaseError(f"Draft source differs from its lock: {release['tag_name']}.")
                    draft = {"draft_tag": release["tag_name"],
                             "draft_version": version_text(version(release["tag_name"])),
                             "draft_commit": previous["commit"]}
    return {"already_published": False, "published_tag": "",
            "draft_tag": "", "draft_version": "", "draft_commit": ""} | (draft or {})


def publish(repo: Path, lock_path: Path, package_path: Path, sums_path: Path,
            notes_path: Path, draft_tag: str) -> dict:
    lock = json.loads(lock_path.read_text())
    if (type(lock.get("schema")) is not int or lock["schema"] != 1 or lock.get("library") != "tex-templates"
            or lock.get("preview") is not False or not COMMIT.fullmatch(str(lock.get("commit", "")))
            or not SHA256.fullmatch(str(lock.get("fingerprint", "")))):
        raise ReleaseError("Only verified release locks with a full source commit can be published.")
    chosen_version = version_text(version(lock["version"]))
    tag = f"v{chosen_version}"
    if package_path.name != f"tex-templates-{chosen_version}.tar.gz":
        raise ReleaseError("Package filename does not match the library version.")
    expected_sums = f"{file_hash(package_path)}  {package_path.name}\n{file_hash(lock_path)}  {lock_path.name}\n"
    if sums_path.read_text() != expected_sums:
        raise ReleaseError("Parent package/lock checksums do not match SHA256SUMS.")
    repository = os.environ.get("GITHUB_REPOSITORY") or repository_slug(git(repo, "remote", "get-url", "origin"))
    client = GitHub()
    existing = client.status(f"repos/{repository}/releases/tags/{tag}")
    ref = client.status(f"repos/{repository}/git/ref/tags/{tag}")
    if ref is not None and client.tag_commit(repository, tag) != lock["commit"]:
        raise ReleaseError(f"Existing library tag points to a different source commit: {tag}.")
    if existing is None:
        run("gh", "release", "create", tag, str(package_path), str(lock_path), str(sums_path),
            "--repo", repository, "--target", lock["commit"], "--title", f"TeX Templates {chosen_version}",
            "--notes-file", str(notes_path), "--draft")
    else:
        if not existing["draft"] or draft_tag != tag:
            raise ReleaseError(f"Refusing to overwrite an unrelated or published release: {tag}.")
        if existing.get("target_commitish") != lock["commit"]:
            raise ReleaseError(f"Draft target differs from the assembled source: {tag}.")
        if "templates.lock.json" in {asset["name"] for asset in existing["assets"]}:
            with tempfile.TemporaryDirectory(prefix="tex-draft-check-") as scratch:
                previous = json.loads(client.download(repository, tag, "templates.lock.json", Path(scratch)).read_text())
            if previous.get("fingerprint") != lock.get("fingerprint") or previous.get("commit") != lock["commit"]:
                raise ReleaseError(f"Draft collection differs from the assembled release: {tag}.")
        run("gh", "release", "upload", tag, str(package_path), str(lock_path), str(sums_path),
            "--repo", repository, "--clobber")
        run("gh", "release", "edit", tag, "--repo", repository, "--title", f"TeX Templates {chosen_version}",
            "--notes-file", str(notes_path))
    run("gh", "release", "edit", tag, "--repo", repository, "--draft=false")
    return {"published": True, "tag": tag}


def emit(result: dict, destination: Path | None) -> None:
    print(json.dumps(result, sort_keys=True))
    if destination:
        with destination.open("a") as stream:
            for key, value in result.items():
                value = str(value).lower() if isinstance(value, bool) else str(value)
                if "\n" in value or "\r" in value:
                    raise ReleaseError("Unsafe multiline GitHub Actions output.")
                stream.write(f"{key}={value}\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path.cwd(), help="Parent Git checkout (default: cwd).")
    commands = parser.add_subparsers(dest="command", required=True)
    choose = commands.add_parser("next-version", help="Start the committed release-series or increment its patch; historical checkouts retain automatic versioning.")
    choose.add_argument("--override", help="Explicit version, strictly newer than all existing SemVer tags.")
    refresh = commands.add_parser("update", help="Validate a template-released event and stage its exact gitlink.")
    refresh.add_argument("--event", type=Path, required=True)
    for name in ("assemble", "preview"):
        command = commands.add_parser(name, help="Assemble release assets." if name == "assemble" else "Assemble local source archives for credential-free CI only.")
        command.add_argument("--output", type=Path, required=True)
        if name == "assemble":
            command.add_argument("--version", required=True)
            command.add_argument("--source-commit", help="Rebuild an interrupted draft from its original commit.")
    check = commands.add_parser("published", help="Check whether this runtime collection was already published.")
    check.add_argument("--lock", type=Path, required=True)
    send = commands.add_parser("publish", help="Publish a validated package or safely resume its reserved draft.")
    for option in ("lock", "package", "checksums", "notes"):
        send.add_argument(f"--{option}", type=Path, required=True)
    send.add_argument("--draft-tag", default="", help="Exact matching draft reported by the published command.")
    for command in (refresh, commands.choices["assemble"], commands.choices["preview"], check, send):
        command.add_argument("--github-output", type=Path)
    args = parser.parse_args()
    try:
        repo = Path(git(args.repo.resolve(), "rev-parse", "--show-toplevel"))
        if args.command == "next-version":
            print(next_version(repo, args.override))
        elif args.command == "update":
            emit(update(repo, args.event), args.github_output)
        elif args.command in ("assemble", "preview"):
            preview = args.command == "preview"
            emit(assemble(repo, args.output, "0.0.0" if preview else args.version, preview,
                          None if preview else args.source_commit), args.github_output)
        elif args.command == "published":
            emit(published(repo, args.lock), args.github_output)
        else:
            emit(publish(repo, args.lock, args.package, args.checksums, args.notes, args.draft_tag), args.github_output)
        return 0
    except (ReleaseError, OSError, ValueError, KeyError, TypeError) as error:
        print(f"library-release.py: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

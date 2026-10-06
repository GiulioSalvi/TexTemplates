#!/usr/bin/env python3
# TeX Templates
# Copyright (C) 2026 Giulio Salvi
#
# This is free software: you can redistribute it and/or modify it under the
# terms of the GNU General Public License as published by the Free Software
# Foundation, either version 3 of the License, or (at your option) any later
# version. This software is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU General
# Public License for more details. You should have received a copy of the GNU
# General Public License along with this program. If not, see
# <https://www.gnu.org/licenses/>.
#
# SPDX-FileCopyrightText: 2026 Giulio Salvi
# SPDX-License-Identifier: GPL-3.0-or-later

"""Publish tested native manifests without replacing existing version tags."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import subprocess
import sys


VERSION = re.compile(r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)")
COMMIT = re.compile(r"[0-9a-f]{40}")
SHA256 = re.compile(r"[0-9a-f]{64}")
DIGEST = re.compile(r"sha256:[0-9a-f]{64}")
IMAGE = re.compile(r"(?:ghcr\.io|docker\.io)/[a-z0-9]+(?:[._-][a-z0-9]+)*/tex-templates")
PLATFORMS = {"amd64", "arm64"}


class ImageError(RuntimeError):
    """An unsafe or unsuccessful registry operation."""


def docker(*args: str, allow_missing: bool = False) -> str | None:
    result = subprocess.run(["docker", "buildx", "imagetools", *args],
                            capture_output=True, text=True)
    if result.returncode:
        error = result.stderr.strip() or result.stdout.strip()
        if allow_missing and re.search(r"manifest unknown|(?:\b404\b.*)?not found", error, re.I):
            return None
        raise ImageError(f"Docker registry operation failed: {error}")
    return result.stdout


def json_result(text: str | None) -> dict:
    try:
        value = json.loads(text or "")
    except json.JSONDecodeError as exc:
        raise ImageError("The registry did not return valid manifest JSON.") from exc
    if not isinstance(value, dict):
        raise ImageError("The registry returned an invalid image object.")
    return value


def verify_config(reference: str, arch: str, labels: dict[str, str]) -> None:
    config = json_result(docker("inspect", reference, "--format", "{{json .Image}}"))
    if config.get("os") != "linux" or config.get("architecture") != arch:
        raise ImageError(f"{reference} is not a linux/{arch} image.")
    settings = config.get("config")
    actual = settings.get("Labels") if isinstance(settings, dict) else None
    if not isinstance(actual, dict):
        raise ImageError(f"{reference} has no valid image metadata labels.")
    for key, expected in labels.items():
        if actual.get(key) != expected:
            raise ImageError(f"{reference} has different {key}; an existing version tag must not be replaced. "
                             "Use an explicit version suffix such as -r1 for another image recipe.")


def inspect_existing(image: str, tag: str, labels: dict[str, str],
                     expected_digests: dict[str, str] | None = None) -> dict[str, str] | None:
    reference = f"{image}:{tag}"
    contents = docker("inspect", reference, "--raw", allow_missing=True)
    if contents is None:
        return None
    manifest = json_result(contents)
    descriptors = manifest.get("manifests")
    if not isinstance(descriptors, list) or len(descriptors) != len(PLATFORMS):
        raise ImageError(f"{reference} must contain exactly linux/amd64 and linux/arm64.")
    seen: set[str] = set()
    native: dict[str, str] = {}
    for item in descriptors:
        if not isinstance(item, dict):
            raise ImageError(f"{reference} contains an invalid platform descriptor.")
        platform = item.get("platform", {})
        if not isinstance(platform, dict):
            raise ImageError(f"{reference} contains an invalid platform descriptor.")
        arch = platform.get("architecture")
        digest = item.get("digest", "")
        if (platform.get("os") != "linux" or arch not in PLATFORMS or arch in seen
                or not isinstance(digest, str) or not DIGEST.fullmatch(digest)):
            raise ImageError(f"{reference} does not contain the two expected native Linux platforms.")
        seen.add(arch)
        native[arch] = digest
        if expected_digests is not None and digest != expected_digests[arch]:
            raise ImageError(f"{reference} does not point to the tested {arch} digest.")
        verify_config(f"{image}@{digest}", arch, labels)
    return native


def publish_images(version: str, commit: str, archive_sha256: str, recipe_sha256: str,
                   digests: Path, images: list[str], image_tag: str | None = None,
                   summary: Path | None = None) -> dict:
    version = version.removeprefix("v")
    if not VERSION.fullmatch(version) or not COMMIT.fullmatch(commit):
        raise ImageError("The library version or commit is invalid.")
    if not SHA256.fullmatch(archive_sha256) or not SHA256.fullmatch(recipe_sha256):
        raise ImageError("The library and recipe hashes must be full lowercase SHA-256 values.")
    image_tag = image_tag or version
    # Version suffixes permit a new recipe for an existing library release.
    suffix = r"(?:-[a-z0-9]+(?:[._-][a-z0-9]+)*)?"
    if len(image_tag) > 128 or not re.fullmatch(re.escape(version) + suffix, image_tag):
        raise ImageError("The image tag must be the library version with an optional suffix, e.g. 0.1.0-r1.")
    if not images or len(set(images)) != len(images) or any(not IMAGE.fullmatch(image) for image in images):
        raise ImageError("Images must be unique ghcr.io or docker.io account/tex-templates repositories.")
    labels = {
        "org.opencontainers.image.version": version,
        "org.opencontainers.image.revision": commit,
        "org.tex-templates.library.sha256": archive_sha256,
        "org.tex-templates.recipe.sha256": recipe_sha256,
    }
    native: dict[str, str] = {}
    for arch in sorted(PLATFORMS):
        try:
            digest = (digests / arch).read_text().strip()
        except OSError as exc:
            raise ImageError(f"Missing tested image digest for {arch}.") from exc
        if not DIGEST.fullmatch(digest):
            raise ImageError(f"Invalid tested image digest for {arch}.")
        native[arch] = digest

    # Complete every guard before assigning the first public version tag.
    # A partial publish can then be resumed without modifying successful targets.
    existing = {image: inspect_existing(image, image_tag, labels) for image in images}
    published = [selected for selected in existing.values() if selected is not None]
    if published and any(selected != published[0] for selected in published):
        raise ImageError("Existing registry tags select different platform digests; neither tag will be replaced.")
    # Both registries received the native digests before the first tag was assigned.
    # Reuse those exact digests after an interrupted publication, even if a later
    # rebuild changed timestamps or Linux package versions.
    selected_native = published[0] if published else native
    for image in images:
        if existing[image]:
            continue
        for arch, digest in selected_native.items():
            verify_config(f"{image}@{digest}", arch, labels)
    results = []
    for image in images:
        reference = f"{image}:{image_tag}"
        if existing[image]:
            status = "already published; preserved"
        else:
            docker("create", "--tag", reference,
                   *(f"{image}@{selected_native[arch]}" for arch in sorted(PLATFORMS)))
            if not inspect_existing(image, image_tag, labels, selected_native):
                raise ImageError(f"Published tag {reference} could not be verified.")
            status = "published"
        results.append({"image": reference, "status": status})
        if summary:
            with summary.open("a", encoding="utf-8") as output:
                output.write(f"- `{reference}`: {status}; linux/amd64 and linux/arm64.\n")
    return {"version": version, "image_tag": image_tag, "images": results}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", required=True)
    parser.add_argument("--commit", required=True)
    parser.add_argument("--archive-sha256", required=True)
    parser.add_argument("--recipe-sha256", required=True)
    parser.add_argument("--digests", type=Path, required=True)
    parser.add_argument("--image", action="append", dest="images", required=True)
    parser.add_argument("--image-tag")
    parser.add_argument("--github-summary", type=Path, dest="summary")
    args = parser.parse_args()
    try:
        print(json.dumps(publish_images(**vars(args))))
    except (ImageError, OSError, KeyError, TypeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

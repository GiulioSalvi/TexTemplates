#!/usr/bin/env bash
#
# TeX Templates
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

set -euo pipefail

if [[ $# -ne 2 || "$1" == -* ]]; then
    printf 'Usage: %s IMAGE FIXTURES_DIR\n' "$0" >&2
    exit 2
fi
image=$1
fixtures=$(cd -- "$2" && pwd -P)

# Exercise the configured image account and internal caches. Fixtures remain
# read-only on the host; each project is copied into the container's /tmp.
docker run --rm --network none --interactive \
    --mount "type=bind,source=${fixtures},target=/fixtures,readonly" \
    --entrypoint /bin/bash "$image" -se <<'SH'
set -euo pipefail

if [[ $(id -u) -eq 0 ]]; then
    printf 'The runtime image must use a non-root account by default.\n' >&2
    exit 1
fi
if [[ ! -d "$HOME" || ! -w "$HOME" ]]; then
    printf 'The runtime account needs a writable home directory.\n' >&2
    exit 1
fi
for variable in TEXMFVAR TEXMFCACHE TEXMFCONFIG; do
    directory=${!variable:?The image must configure writable TeX caches}
    mkdir -p "$directory"
    probe=$(mktemp "${directory}/smoke-cache.XXXXXXXX")
    rm "$probe"
done

pandoc --version | sed -n '1p'
lualatex --version | sed -n '1p'
template-list.sh

workspace=$(mktemp -d /tmp/template-image-smoke.XXXXXXXX)
trap 'rm -rf "$workspace"' EXIT
tested=0
for template in "${TEX_TEMPLATES_HOME:?}"/templates/*; do
    [[ -d "$template" ]] || continue
    id=${template##*/}
    if [[ ! "$id" =~ ^[A-Za-z0-9][A-Za-z0-9_-]*$ ]]; then
        printf 'Invalid installed template ID: %s\n' "$id" >&2
        exit 1
    fi
    source_fixture="/fixtures/${id}/usage_example"
    if [[ ! -d "$source_fixture" ]]; then
        printf 'Missing smoke-test fixture for template: %s\n' "$id" >&2
        exit 1
    fi
    project="${workspace}/${id}/usage_example"
    mkdir -p "$project"
    cp -R "$source_fixture/." "$project/"
    chmod -R u+rwX "$project"
    mkdir -p "$project/build"
    rm -f "$project/build/smoke.pdf"
    if ! generate-pdf.sh --project "$project" --template "$id" -- --verbose -o build/smoke.pdf; then
        cat "$project/build/pandoc-generation.log" >&2
        exit 1
    fi
    python3 - "$project/build/smoke.pdf" <<'PY'
import pathlib
import sys

pdf = pathlib.Path(sys.argv[1])
if not pdf.is_file() or pdf.stat().st_size < 100:
    raise SystemExit(f"The image did not produce a nonempty PDF: {pdf}")
with pdf.open("rb") as source:
    if source.read(5) != b"%PDF-":
        raise SystemExit(f"The image output has no PDF header: {pdf}")
print(f"Verified PDF: {pdf.stat().st_size} bytes")
PY
    tested=$((tested + 1))
done
if [[ "$tested" -eq 0 ]]; then
    printf 'The runtime image contains no templates to test.\n' >&2
    exit 1
fi
printf 'Compiled %s template(s) with the non-root runtime account and no network.\n' "$tested"
SH

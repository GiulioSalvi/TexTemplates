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

if [[ $# -ne 4 ]]; then
    printf 'Usage: %s TARGETARCH REPOSITORY INSTALLER_SHA512 REQUIREMENTS\n' "$0" >&2
    exit 2
fi

architecture=$1
repository=$2
installer_checksum=$3
requirements=$4

case "$architecture" in
    amd64) platform=x86_64-linux ;;
    arm64) platform=aarch64-linux ;;
    *) printf 'Unsupported architecture: %s\n' "$architecture" >&2; exit 2 ;;
esac
if [[ "$repository" != https://* || ! "$installer_checksum" =~ ^[0-9a-f]{128}$ ]]; then
    printf 'TeX Live requires an HTTPS repository and a pinned SHA-512 installer checksum.\n' >&2
    exit 2
fi

workspace=$(mktemp -d /tmp/texlive-install.XXXXXXXX)
trap 'rm -rf "$workspace"' EXIT

# Parse data as data: never evaluate requirements as shell commands or options.
python3 - "$requirements" > "$workspace/packages.txt" <<'PY'
import pathlib
import re
import sys

source = pathlib.Path(sys.argv[1])
packages = {"latex-bin", "luatex"}
for number, original in enumerate(source.read_text(encoding="utf-8").splitlines(), 1):
    package = original.partition("#")[0].strip()
    if not package:
        continue
    if not re.fullmatch(r"[a-z0-9][a-z0-9+._-]*", package):
        raise SystemExit(f"{source}:{number}: expected one TeX Live package name")
    packages.add(package)
print("\n".join(sorted(packages)))
PY
mapfile -t packages < "$workspace/packages.txt"

curl --fail --silent --show-error --location --retry 3 \
    "${repository%/}/install-tl-unx.tar.gz" -o "$workspace/install-tl-unx.tar.gz"
printf '%s  %s\n' "$installer_checksum" "$workspace/install-tl-unx.tar.gz" | sha512sum --check -
tar -xzf "$workspace/install-tl-unx.tar.gz" -C "$workspace"
installers=("$workspace"/install-tl-*/install-tl)
if [[ ${#installers[@]} -ne 1 || ! -f "${installers[0]}" ]]; then
    printf 'The verified TeX Live installer has an unexpected layout.\n' >&2
    exit 1
fi

texdir=/opt/texlive/2026
cat > "$workspace/texlive.profile" <<EOF
selected_scheme scheme-minimal
binary_${platform} 1
TEXDIR ${texdir}
TEXMFLOCAL /opt/texlive/texmf-local
TEXMFSYSVAR ${texdir}/texmf-var
TEXMFSYSCONFIG ${texdir}/texmf-config
TEXMFHOME ~/texmf
TEXMFVAR ~/.texlive2026/texmf-var
TEXMFCONFIG ~/.texlive2026/texmf-config
instopt_adjustpath 0
instopt_adjustrepo 0
instopt_letter 0
instopt_portable 0
instopt_write18_restricted 1
tlpdbopt_autobackup 0
tlpdbopt_create_formats 1
tlpdbopt_install_docfiles 0
tlpdbopt_install_srcfiles 0
EOF

perl "${installers[0]}" -profile "$workspace/texlive.profile" -repository "$repository"
export PATH="${texdir}/bin/${platform}:$PATH"
tlmgr option repository "$repository"
tlmgr option autobackup 0
tlmgr option docfiles 0
tlmgr option srcfiles 0
tlmgr --repository "$repository" install "${packages[@]}"

# Keep a stable PATH across the two supported native TeX Live platforms.
ln -s "2026/bin/${platform}" /opt/texlive/bin
test -x /opt/texlive/bin/lualatex
lualatex --version

# Retain exact installed revisions for diagnosing or reproducing an image.
install -d /usr/local/share/tex-templates
tlmgr info --all --only-installed --data name,localrev \
    > /usr/local/share/tex-templates/texlive-packages.txt
cp "$workspace/texlive.profile" /usr/local/share/tex-templates/texlive.profile

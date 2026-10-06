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

# The publishing workflow prepares library/ from a verified release archive.
# Its Docker context contains only this directory and the Docker build files.
ARG UBUNTU_IMAGE=ubuntu:24.04@sha256:534baea6a22c03a63003dbc8dbe78fe34bc0d7e595d9a9dc9834884ff530eb55
FROM ${UBUNTU_IMAGE}

ARG TARGETARCH
ARG PANDOC_VERSION=3.12
ARG PANDOC_SHA256_AMD64=67d7d011fed8c8543306022b985b9b2499ab9b74818df91d8727c7e9ebc5ba06
ARG PANDOC_SHA256_ARM64=6cefcf7100e23a99447c26f89d1ff5b253f3407fcef99a9e27ae06f3ed16cb82
ARG TEXLIVE_REPOSITORY=https://texlive.info/tlnet-archive/2026/10/01/tlnet
ARG TEXLIVE_INSTALLER_SHA512=e98515abe9bb07f9d93baab1483cfcd6339613ccf3cd0e438921109559a9871b237a53545b13e787d9435b63e5c3b39598edc1e969ba58182f9f00de8e23299e

SHELL ["/bin/bash", "-o", "pipefail", "-c"]

RUN apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        bash ca-certificates curl fontconfig fonts-dejavu-core ghostscript git \
        inkscape less libgmp10 openssh-client perl python3 sudo xz-utils \
    && rm -rf /var/lib/apt/lists/*

RUN set -eu; \
    case "$TARGETARCH" in \
        amd64) checksum="$PANDOC_SHA256_AMD64" ;; \
        arm64) checksum="$PANDOC_SHA256_ARM64" ;; \
        *) printf 'Unsupported architecture: %s\n' "$TARGETARCH" >&2; exit 2 ;; \
    esac; \
    [[ "$checksum" =~ ^[0-9a-f]{64}$ ]]; \
    archive="pandoc-${PANDOC_VERSION}-linux-${TARGETARCH}.tar.gz"; \
    curl --fail --silent --show-error --location --retry 3 \
        "https://github.com/jgm/pandoc/releases/download/${PANDOC_VERSION}/${archive}" \
        -o /tmp/pandoc.tar.gz; \
    printf '%s  /tmp/pandoc.tar.gz\n' "$checksum" | sha256sum --check -; \
    tar -xzf /tmp/pandoc.tar.gz -C /usr/local --strip-components=1; \
    rm /tmp/pandoc.tar.gz; \
    pandoc --version

COPY library/requirements.txt /tmp/tex-requirements.txt
COPY install-texlive.sh /tmp/install-texlive.sh
RUN bash /tmp/install-texlive.sh "$TARGETARCH" "$TEXLIVE_REPOSITORY" \
        "$TEXLIVE_INSTALLER_SHA512" /tmp/tex-requirements.txt \
    && rm /tmp/install-texlive.sh /tmp/tex-requirements.txt

COPY library/ /opt/tex-templates/
ARG LIBRARY_VERSION
ARG LIBRARY_COMMIT
ARG LIBRARY_SHA256
ARG RECIPE_SHA256
RUN python3 - "$LIBRARY_VERSION" "$LIBRARY_COMMIT" "$LIBRARY_SHA256" "$RECIPE_SHA256" <<'PY'
import json
import pathlib
import re
import sys

version, commit, archive_checksum, recipe_checksum = sys.argv[1:]
if not re.fullmatch(r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)", version):
    raise SystemExit("LIBRARY_VERSION must be a stable version without the v prefix")
if not re.fullmatch(r"[0-9a-f]{40}", commit):
    raise SystemExit("LIBRARY_COMMIT must be a complete release commit")
for name, checksum in (("LIBRARY_SHA256", archive_checksum), ("RECIPE_SHA256", recipe_checksum)):
    if not re.fullmatch(r"[0-9a-f]{64}", checksum):
        raise SystemExit(f"{name} must be a complete SHA-256 checksum")
library = pathlib.Path("/opt/tex-templates")
lock = json.loads((library / "templates.lock.json").read_text(encoding="utf-8"))
if lock.get("version") != version or lock.get("commit") != commit or lock.get("preview") is not False:
    raise SystemExit("The prepared library does not match the requested published version")
if not lock.get("templates") or not (library / "requirements.txt").is_file():
    raise SystemExit("The prepared library has no templates or TeX requirements")
for command in ("generate-pdf.sh", "template-list.sh", "template-use.sh"):
    if not (library / "bin" / command).is_file():
        raise SystemExit(f"The prepared library is missing {command}")
PY

RUN chmod -R a+rX /opt/tex-templates \
    && chmod a+rx /opt/tex-templates/bin/*.sh

# Ubuntu 24.04 includes an unused ubuntu account with the same default UID.
RUN if id ubuntu >/dev/null 2>&1; then userdel --remove ubuntu; fi \
    && if getent group ubuntu >/dev/null 2>&1; then groupdel ubuntu; fi \
    && groupadd --gid 1000 vscode \
    && useradd --uid 1000 --gid vscode --create-home --shell /bin/bash vscode \
    && printf 'vscode ALL=(ALL) NOPASSWD:ALL\n' > /etc/sudoers.d/vscode \
    && chmod 0440 /etc/sudoers.d/vscode \
    && install -d -o vscode -g vscode /workspaces \
        /home/vscode/.cache /home/vscode/.cache/texlive /home/vscode/.cache/texlive/texmf-var \
        /home/vscode/.config /home/vscode/.config/texlive \
    && ln -s /opt/texlive/2026/texmf-dist/fonts/opentype /usr/local/share/fonts/texlive-opentype \
    && ln -s /opt/texlive/2026/texmf-dist/fonts/truetype /usr/local/share/fonts/texlive-truetype \
    && fc-cache -f

LABEL org.opencontainers.image.title="TeX Templates" \
      org.opencontainers.image.description="Pandoc and LuaLaTeX environment with a versioned TeX template library" \
      org.opencontainers.image.source="https://github.com/GiulioSalvi/TexTemplates" \
      org.opencontainers.image.version="${LIBRARY_VERSION}" \
      org.opencontainers.image.revision="${LIBRARY_COMMIT}" \
      org.tex-templates.library.sha256="${LIBRARY_SHA256}" \
      org.tex-templates.recipe.sha256="${RECIPE_SHA256}" \
      org.opencontainers.image.licenses="GPL-3.0-or-later"

ENV LANG=C.UTF-8 \
    LC_ALL=C.UTF-8 \
    HOME=/home/vscode \
    TEX_TEMPLATES_HOME=/opt/tex-templates \
    TEXMFVAR=/home/vscode/.cache/texlive/texmf-var \
    TEXMFCACHE=/home/vscode/.cache/texlive/texmf-var \
    TEXMFCONFIG=/home/vscode/.config/texlive \
    PATH=/opt/tex-templates/bin:/opt/texlive/bin:${PATH}

USER vscode
WORKDIR /workspaces
CMD ["bash"]

#!/bin/sh
# TeX Templates - Copyright (C) 2026 Giulio Salvi
# GNU GPL version 3 or later; without warranty. See LICENSE.
# SPDX-License-Identifier: GPL-3.0-or-later
set -eu
library=${TEX_TEMPLATES_HOME:-$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)}
exec bash "$library/tools/mermaid/setup.sh" "$@"

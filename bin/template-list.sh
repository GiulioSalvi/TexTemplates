#!/bin/sh
#
# HandoutsTexTemplate
# Copyright (C) 2026 Giulio Salvi
#
# This is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This software is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.
#
# SPDX-FileCopyrightText: 2026 Giulio Salvi
# SPDX-License-Identifier: GPL-3.0-or-later

set -eu
LC_ALL=C
export LC_ALL

usage() {
    cat <<'EOF'
Usage: template-list.sh [--help]

List installed templates. The effective global default is marked "(default)".
Only directories containing template.tex and a valid template ID are listed.

The default comes from the configuration file, then an installed "handouts"
template, then the sole installed template. With several templates and no
default, the list has no default marker.

Environment:
  TEX_TEMPLATES_HOME         Library root (defaults to the parent of bin/).
  TEX_TEMPLATES_CONFIG_HOME  Configuration directory.
  XDG_CONFIG_HOME            Configuration uses $XDG_CONFIG_HOME/tex-templates
                            or, if unset, $HOME/.config/tex-templates.
EOF
}

fail() {
    printf 'template-list.sh: %s\n' "$*" >&2
    exit 1
}

valid_id() {
    case "$1" in
        ''|[!A-Za-z0-9]*|*[!A-Za-z0-9_-]*) return 1 ;;
        *) return 0 ;;
    esac
}

case "$#" in
    0) ;;
    1)
        case "$1" in
            --help|-h) usage; exit 0 ;;
            *) usage >&2; fail "Unexpected argument: $1" ;;
        esac
        ;;
    *) usage >&2; fail 'Too many arguments.' ;;
esac

if [ -n "${TEX_TEMPLATES_HOME:-}" ]; then
    library_path=$TEX_TEMPLATES_HOME
else
    script_dir=$(CDPATH= cd "$(dirname "$0")" && pwd) ||
        fail 'Cannot locate the script directory.'
    library_path=$script_dir/..
fi
case "$library_path" in
    /*) ;;
    *) library_path=./$library_path ;;
esac
library_root=$(CDPATH= cd "$library_path" 2>/dev/null && pwd) ||
    fail "Library directory does not exist: $library_path"

config_dir=${TEX_TEMPLATES_CONFIG_HOME:-${XDG_CONFIG_HOME:-$HOME/.config}/tex-templates}
default_file=$config_dir/default-template
template_count=0
sole_template=
for template_dir in "$library_root"/templates/*; do
    [ -d "$template_dir" ] || continue
    template_id=${template_dir##*/}
    valid_id "$template_id" || continue
    [ -f "$template_dir/template.tex" ] || continue
    template_count=$((template_count + 1))
    sole_template=$template_id
done

default_template=
if [ -f "$default_file" ]; then
    default_template=$(cat < "$default_file") ||
        fail "Cannot read the default template file: $default_file"
    valid_id "$default_template" ||
        fail "Invalid template ID in $default_file. IDs must start with a letter or digit and contain only letters, digits, underscores, or hyphens."
    [ -f "$library_root/templates/$default_template/template.tex" ] ||
        fail "The configured default template is not installed: $default_template"
elif [ -d "$default_file" ]; then
    fail "The default template path is a directory: $default_file"
elif [ -f "$library_root/templates/handouts/template.tex" ]; then
    default_template=handouts
elif [ "$template_count" -eq 1 ]; then
    default_template=$sole_template
fi

[ "$template_count" -gt 0 ] ||
    fail "No templates containing template.tex are installed in $library_root/templates."

for template_dir in "$library_root"/templates/*; do
    [ -d "$template_dir" ] || continue
    template_id=${template_dir##*/}
    valid_id "$template_id" || continue
    [ -f "$template_dir/template.tex" ] || continue
    if [ "$template_id" = "$default_template" ]; then
        printf '%s (default)\n' "$template_id"
    else
        printf '%s\n' "$template_id"
    fi
done

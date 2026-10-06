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
Usage: template-use.sh NAME
       template-use.sh --project DIR NAME
       template-use.sh NAME --project DIR
       template-use.sh --show
       template-use.sh --help

Choose an installed template as the global default, or record a template for
an existing project in DIR/.tex-template. Project selection does not change
the global default. IDs must start with a letter or digit and contain only
letters, digits, underscores, or hyphens.

--show prints the effective global default: the configured template, then
an installed "handouts" template, then the sole installed template.

Environment:
  TEX_TEMPLATES_HOME         Library root (defaults to the parent of bin/).
  TEX_TEMPLATES_CONFIG_HOME  Configuration directory.
  XDG_CONFIG_HOME            Configuration uses $XDG_CONFIG_HOME/tex-templates
                            or, if unset, $HOME/.config/tex-templates.

The configuration directory is created only when selecting a global default.
EOF
}

fail() {
    printf 'template-use.sh: %s\n' "$*" >&2
    exit 1
}

valid_id() {
    case "$1" in
        ''|[!A-Za-z0-9]*|*[!A-Za-z0-9_-]*) return 1 ;;
        *) return 0 ;;
    esac
}

mode=global
project_path=
template_id=
case "$#" in
    1)
        case "$1" in
            --help|-h) usage; exit 0 ;;
            --show) mode=show ;;
            *) template_id=$1 ;;
        esac
        ;;
    3)
        if [ "$1" = --project ]; then
            mode=project
            project_path=$2
            template_id=$3
        elif [ "$2" = --project ]; then
            mode=project
            template_id=$1
            project_path=$3
        else
            usage >&2
            fail 'Expected NAME or --project DIR NAME.'
        fi
        ;;
    *) usage >&2; fail 'Expected NAME, --project DIR NAME, or --show.' ;;
esac

if [ "$mode" != show ]; then
    valid_id "$template_id" ||
        fail 'Invalid template ID. IDs must start with a letter or digit and contain only letters, digits, underscores, or hyphens.'
fi

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

if [ "$mode" = show ]; then
    default_file=$config_dir/default-template
    if [ -f "$default_file" ]; then
        template_id=$(cat < "$default_file") ||
            fail "Cannot read the default template file: $default_file"
        valid_id "$template_id" ||
            fail "Invalid template ID in $default_file. IDs must start with a letter or digit and contain only letters, digits, underscores, or hyphens."
        [ -f "$library_root/templates/$template_id/template.tex" ] ||
            fail "The configured default template is not installed: $template_id"
    elif [ -d "$default_file" ]; then
        fail "The default template path is a directory: $default_file"
    elif [ -f "$library_root/templates/handouts/template.tex" ]; then
        template_id=handouts
    else
        template_count=0
        for template_dir in "$library_root"/templates/*; do
            [ -d "$template_dir" ] || continue
            candidate_id=${template_dir##*/}
            valid_id "$candidate_id" || continue
            [ -f "$template_dir/template.tex" ] || continue
            template_count=$((template_count + 1))
            template_id=$candidate_id
        done
        case "$template_count" in
            0) fail "No templates containing template.tex are installed in $library_root/templates." ;;
            1) ;;
            *) fail 'No global default is selected. Run template-use.sh NAME to choose one.' ;;
        esac
    fi
    printf '%s\n' "$template_id"
    exit 0
fi

[ -f "$library_root/templates/$template_id/template.tex" ] ||
    fail "Template is not installed or has no template.tex: $template_id"

if [ "$mode" = project ]; then
    [ -n "$project_path" ] || fail 'The project directory cannot be empty.'
    case "$project_path" in
        /*) ;;
        *) project_path=./$project_path ;;
    esac
    destination_dir=$(CDPATH= cd "$project_path" 2>/dev/null && pwd) ||
        fail "Project directory does not exist: $project_path"
    destination_file=$destination_dir/.tex-template
else
    case "$config_dir" in
        /*) ;;
        *) config_dir=./$config_dir ;;
    esac
    mkdir -p "$config_dir" ||
        fail "Cannot create the configuration directory: $config_dir"
    destination_dir=$(CDPATH= cd "$config_dir" 2>/dev/null && pwd) ||
        fail "Cannot access the configuration directory: $config_dir"
    destination_file=$destination_dir/default-template
fi

[ ! -d "$destination_file" ] ||
    fail "The selection path is a directory: $destination_file"
temporary_file=$destination_file.tmp.$$
if ! (umask 077; set -C; printf '%s\n' "$template_id" > "$temporary_file"); then
    fail "Cannot create the temporary selection file: $temporary_file"
fi
trap 'rm -f "$temporary_file"' 0
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM
mv -f "$temporary_file" "$destination_file" ||
    fail "Cannot save the template selection: $destination_file"

if [ "$mode" = project ]; then
    printf 'Project template set to %s: %s\n' "$template_id" "$destination_file"
else
    printf 'Global default template set to %s.\n' "$template_id"
fi

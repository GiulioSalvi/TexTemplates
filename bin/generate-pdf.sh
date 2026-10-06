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
Usage: generate-pdf.sh [--project DIR] [--template NAME] [--defaults FILE]
                       [--] [PANDOC_OPTIONS_AND_INPUTS...]

Build a document using the selected template and a project's Pandoc defaults.
Put the script options before Pandoc options and input files. Relative paths
passed to Pandoc are resolved from the project directory.

Options:
  --project DIR    Project directory (default: current directory).
  --template NAME  Template ID for this invocation.
  --defaults FILE Project defaults, relative to DIR unless absolute.
  --help          Show this help.

The project defaults are discovered in this order: pandoc.yaml, pandoc.yml,
defaults.yaml, defaults.yml. The selected template's defaults.yaml or
defaults.yml is loaded first; project settings and Pandoc options follow.

Template selection: --template, then DIR/.tex-template, then the global
default reported by template-use.sh --show. The selected template's
environment.sh is sourced only within this build process.

Declare input-file/input-files and output-file in the project defaults, or
pass an input file and -o OUTPUT. To use stdin, pass an explicit "-" input.
Output directories are created automatically. Diagnostic .tex output is
also supported. The build log is DIR/build/pandoc-generation.log.

Examples:
  generate-pdf.sh
  generate-pdf.sh --project "/path/to/course" --template handouts
  generate-pdf.sh --template handouts notes.md -o build/notes.pdf
  generate-pdf.sh --project usage_example -- -o build/example.tex

Environment:
  TEX_TEMPLATES_HOME         Library root (defaults to the parent of bin/).
  TEX_TEMPLATES_CONFIG_HOME  Directory holding the global default-template.
  XDG_CONFIG_HOME            Standard configuration directory fallback.

Pandoc and the selected template's TeX engine must already be installed.
EOF
}

fail() {
    printf 'generate-pdf.sh: %s\n' "$*" >&2
    exit 1
}

valid_id() {
    case "$1" in
        ''|[!A-Za-z0-9]*|*[!A-Za-z0-9_-]*) return 1 ;;
        *) return 0 ;;
    esac
}

need_value() {
    [ "$2" -ge 2 ] && [ -n "$3" ] || fail "Missing value for $1."
}

project_path=.
project_defaults=
template_id=
while [ "$#" -gt 0 ]; do
    case "$1" in
        --help|-h) usage; exit 0 ;;
        --project)
            need_value "$1" "$#" "${2:-}"
            project_path=$2
            shift 2
            ;;
        --project=*) project_path=${1#*=}; shift ;;
        --template)
            need_value "$1" "$#" "${2:-}"
            template_id=$2
            shift 2
            ;;
        --template=*)
            template_id=${1#*=}
            [ -n "$template_id" ] || fail 'Missing value for --template.'
            shift
            ;;
        --defaults)
            need_value "$1" "$#" "${2:-}"
            project_defaults=$2
            shift 2
            ;;
        --defaults=*)
            project_defaults=${1#*=}
            [ -n "$project_defaults" ] || fail 'Missing value for --defaults.'
            shift
            ;;
        --) shift; break ;;
        *) break ;;
    esac
done

for argument in "$@"; do
    case "$argument" in
        --dump-args|--ignore-args)
            fail "$argument is reserved for argument processing and cannot be used for generation."
            ;;
    esac
done

[ -n "$project_path" ] || fail 'The project directory cannot be empty.'
script_dir=$(CDPATH= cd "$(dirname "$0")" && pwd) ||
    fail 'Cannot locate the script directory.'
library_path=${TEX_TEMPLATES_HOME:-$script_dir/..}
case "$library_path" in
    /*) ;;
    *) library_path=./$library_path ;;
esac
TEX_TEMPLATES_HOME=$(CDPATH= cd "$library_path" 2>/dev/null && pwd) ||
    fail "Library directory does not exist: $library_path"
export TEX_TEMPLATES_HOME

case "$project_path" in
    /*) ;;
    *) project_path=./$project_path ;;
esac
project_dir=$(CDPATH= cd "$project_path" 2>/dev/null && pwd) ||
    fail "Project directory does not exist: $project_path"

if [ -z "$template_id" ]; then
    if [ -f "$project_dir/.tex-template" ]; then
        template_id=$(cat < "$project_dir/.tex-template") ||
            fail "Cannot read $project_dir/.tex-template."
    elif [ -d "$project_dir/.tex-template" ]; then
        fail "The template selection path is a directory: $project_dir/.tex-template"
    else
        [ -x "$script_dir/template-use.sh" ] ||
            fail "Missing executable: $script_dir/template-use.sh"
        template_id=$("$script_dir/template-use.sh" --show) || exit "$?"
    fi
fi
valid_id "$template_id" || fail "Invalid template ID: $template_id"
TEX_TEMPLATE=$template_id
TEX_TEMPLATE_HOME=$TEX_TEMPLATES_HOME/templates/$template_id
[ -f "$TEX_TEMPLATE_HOME/template.tex" ] ||
    fail "Template is not installed or has no template.tex: $template_id"
TEX_TEMPLATE_HOME=$(CDPATH= cd "$TEX_TEMPLATE_HOME" && pwd)
TEX_TEMPLATE_PATH=$TEX_TEMPLATE_HOME/template.tex
TEX_PROJECT_HOME=$project_dir
export TEX_TEMPLATE TEX_TEMPLATE_HOME TEX_TEMPLATE_PATH TEX_PROJECT_HOME

template_defaults=
for candidate in defaults.yaml defaults.yml; do
    if [ -f "$TEX_TEMPLATE_HOME/$candidate" ]; then
        template_defaults=$TEX_TEMPLATE_HOME/$candidate
        break
    fi
done
[ -n "$template_defaults" ] ||
    fail "Template defaults.yaml or defaults.yml not found: $TEX_TEMPLATE_HOME"

if [ -n "$project_defaults" ]; then
    case "$project_defaults" in
        /*) ;;
        *) project_defaults=$project_dir/$project_defaults ;;
    esac
else
    for candidate in pandoc.yaml pandoc.yml defaults.yaml defaults.yml; do
        if [ -f "$project_dir/$candidate" ]; then
            project_defaults=$project_dir/$candidate
            break
        fi
    done
fi
[ -n "$project_defaults" ] && [ -f "$project_defaults" ] ||
    fail "Project defaults not found in $project_dir. Use --defaults FILE."
defaults_dir=$(CDPATH= cd "$(dirname "$project_defaults")" && pwd)
project_defaults=$defaults_dir/$(basename "$project_defaults")

# Each build starts with the project, selected template, and standard TeX paths.
# The template's hook can add further directories when it needs them.
TEXINPUTS=$project_dir:$TEX_TEMPLATE_HOME:
export TEXINPUTS
if [ -f "$TEX_TEMPLATE_HOME/environment.sh" ]; then
    . "$TEX_TEMPLATE_HOME/environment.sh"
fi

command -v pandoc >/dev/null 2>&1 || fail 'Pandoc is not installed or is not on PATH.'
cd "$project_dir"
mkdir -p build
log_path=$project_dir/build/pandoc-generation.log

run_pandoc() {
    pandoc \
        --defaults="$template_defaults" \
        --defaults="$project_defaults" \
        --template="$TEX_TEMPLATE_PATH" \
        "$@"
}

# Let Pandoc parse YAML, expand paths, and report the effective inputs/output.
# A probe operand prevents Pandoc from inventing an implicit stdin input when
# no inputs were configured. The probe is never read or passed to the build.
input_probe=__tex_templates_input_probe_$$__
if resolved_arguments=$(run_pandoc --dump-args "$@" "$input_probe" 2> "$log_path"); then
    :
else
    result=$?
    cat "$log_path" >&2
    exit "$result"
fi
output_file=$(printf '%s\n' "$resolved_arguments" | sed -n '1p')
[ "$(printf '%s\n' "$resolved_arguments" | sed -n '$p')" = "$input_probe" ] ||
    fail 'Pandoc options that discard or change input arguments are unsupported.'
inputs=$(printf '%s\n' "$resolved_arguments" | sed '1d;$d')
[ -n "$inputs" ] ||
    fail 'No input file specified. Set input-file/input-files in the project defaults, or pass an input file ("-" for stdin).'
case "$output_file" in
    ''|-) fail 'No output file specified. Set output-file in the project defaults, or pass -o OUTPUT.' ;;
esac
mkdir -p "$(dirname "$output_file")"

printf 'Template: %s\nProject: %s\nOutput: %s\n' \
    "$template_id" "$project_dir" "$output_file"
if run_pandoc "$@" >> "$log_path" 2>&1; then
    printf 'Generation completed. Log: %s\n' "$log_path"
else
    result=$?
    cat "$log_path" >&2
    printf '\nGeneration failed. Log: %s\n' "$log_path" >&2
    exit "$result"
fi

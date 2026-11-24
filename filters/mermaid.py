#!/usr/bin/env python3
# TeX Templates - Copyright (C) 2026 Giulio Salvi
# GNU GPL version 3 or later; without warranty. See LICENSE.
# SPDX-License-Identifier: GPL-3.0-or-later
"""Render inline Mermaid code blocks for Pandoc's Beamer/LaTeX writers."""

import hashlib
import json
import mimetypes
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import base64

PROJECT = Path.cwd()
TOOLS = Path(__file__).resolve().parent.parent / "tools" / "mermaid"
RUNTIME = Path(os.environ.get("TEX_TEMPLATES_MERMAID_HOME", str(Path.home() / ".local/share/tex-templates/mermaid")))


def embed_assets(source, base=None):
    """Resolve local HTML images and Mermaid image nodes from the project root."""
    base = Path(base) if base is not None else PROJECT
    def replace(match):
        prefix, quote, target = match.groups()
        if re.match(r"(?:[a-zA-Z][a-zA-Z0-9+.-]*:|//)", target):
            return match.group(0)
        path = (base / target).resolve()
        if not path.is_file():
            raise ValueError(f"Mermaid image not found: {target} ({path})")
        media_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        encoded = base64.b64encode(path.read_bytes()).decode("ascii")
        return f"{prefix}{quote}data:{media_type};base64,{encoded}{quote}"
    source = re.sub(r"(\bsrc\s*=\s*)(['\"])(.*?)\2", replace, source)
    return re.sub(r"(\bimg\s*:\s*)(['\"])(.*?)\2", replace, source)


def render(source, base=None):
    source = embed_assets(source, base)
    launcher = RUNTIME / "node_modules/.bin/mmdc"
    browser = RUNTIME / "puppeteer.json"
    config = TOOLS / "config.json"
    if not launcher.is_file() or not browser.is_file():
        raise ValueError("Mermaid tools are missing. Run the library command: setup-mermaid.sh")
    signature = source.encode() + config.read_bytes() + (TOOLS / "package-lock.json").read_bytes() + Path(__file__).read_bytes()
    key = hashlib.sha256(signature).hexdigest()[:24]
    cache = PROJECT / "build/mermaid"
    cache.mkdir(parents=True, exist_ok=True)
    output = cache / f"{key}.pdf"
    if not output.is_file() or not output.read_bytes().startswith(b"%PDF-"):
        with tempfile.TemporaryDirectory(prefix=".render-", dir=cache) as temporary:
            working = Path(temporary)
            input_file = working / "diagram.mmd"
            rendered = working / "diagram.pdf"
            input_file.write_text(source, encoding="utf-8")
            result = subprocess.run([
                str(launcher), "--input", str(input_file), "--output", str(rendered),
                "--pdfFit", "--quiet", "--configFile", str(config),
                "--puppeteerConfigFile", str(browser),
            ], capture_output=True, text=True)
            if result.returncode:
                raise ValueError("Mermaid rendering failed:\n" + result.stderr.strip())
            if not rendered.is_file() or not rendered.read_bytes().startswith(b"%PDF-"):
                raise ValueError("Mermaid did not generate a valid PDF")
            rendered.replace(output)
    return str(output)


def walk(value):
    if isinstance(value, list):
        return [walk(item) for item in value]
    if isinstance(value, dict):
        if value.get("t") == "CodeBlock":
            attributes, source = value["c"]
            if "mermaid" in attributes[1]:
                options = dict(attributes[2])
                base = options.get("data-mermaid-base")
                target = render(source, base) if base else render(source)
                image = {"t": "Image", "c": [
                    ["", [], [["width", "95%"]]],
                    [{"t": "Str", "c": "Diagram"}], [target, ""],
                ]}
                return {"t": "Div", "c": [["", [], []], [
                    {"t": "RawBlock", "c": ["latex", "\\begingroup\\centering"]},
                    {"t": "Para", "c": [image]},
                    {"t": "RawBlock", "c": ["latex", "\\par\\endgroup"]},
                ]]}
        return {key: walk(item) for key, item in value.items()}
    return value


def main():
    document = json.load(sys.stdin)
    if len(sys.argv) > 1 and sys.argv[1] in ("beamer", "latex"):
        document = walk(document)
    json.dump(document, sys.stdout)


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError) as error:
        print(f"mermaid.py: {error}", file=sys.stderr)
        sys.exit(1)

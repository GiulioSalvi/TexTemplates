#!/usr/bin/env bash
# TeX Templates - Copyright (C) 2026 Giulio Salvi
# GNU GPL v3 or later, without warranty. See LICENSE.
# SPDX-License-Identifier: GPL-3.0-or-later
set -euo pipefail

tools_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
runtime="${TEX_TEMPLATES_MERMAID_HOME:-${HOME}/.local/share/tex-templates/mermaid}"
if ! command -v node >/dev/null 2>&1 || ! command -v npm >/dev/null 2>&1 || \
   ! node -e 'process.exit(Number(process.versions.node.split(".")[0]) < 20 ? 1 : 0)'; then
    printf 'Install Node.js 20 or later before running setup-mermaid.sh.\n' >&2
    exit 1
fi
# Published images already contain a complete read-only renderer. Reuse it.
if [ -x "$runtime/node_modules/.bin/mmdc" ] && \
   [ -f "$runtime/puppeteer.json" ] && \
   cmp -s "$tools_dir/package-lock.json" "$runtime/package-lock.json" && \
   node - "$runtime/puppeteer.json" <<'JS'
const fs = require('node:fs');
try {
  const config = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
  fs.accessSync(config.executablePath, fs.constants.X_OK);
} catch (_) { process.exit(1); }
JS
then
    printf 'Mermaid renderer is already installed in %s.\n' "$runtime"
    exit 0
fi
mkdir -p "$runtime"
export PLAYWRIGHT_BROWSERS_PATH="$runtime/browsers"
cp "$tools_dir/package.json" "$tools_dir/package-lock.json" "$runtime/"
PUPPETEER_SKIP_DOWNLOAD=true PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1 \
    npm ci --prefix "$runtime" --omit=dev --no-audit --no-fund

# Playwright supplies Chromium on both amd64 and arm64 Linux, without Snap.
"$runtime/node_modules/.bin/playwright" install --with-deps chromium
node - "$runtime" <<'JS'
const fs = require('node:fs');
const path = require('node:path');
const runtime = process.argv[2];
const {chromium} = require(path.join(runtime, 'node_modules/playwright'));
fs.writeFileSync(path.join(runtime, 'puppeteer.json'), JSON.stringify({
  executablePath: chromium.executablePath(),
  headless: true,
  args: ['--no-sandbox', '--disable-dev-shm-usage']
}, null, 2) + '\n');
JS

chmod -R a+rX "$runtime"

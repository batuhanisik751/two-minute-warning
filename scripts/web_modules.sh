#!/usr/bin/env bash
# Install (or repair) the website's npm packages OUTSIDE iCloud's reach, keeping every path the same.
#
# Why: this checkout lives in ~/Desktop, which iCloud Drive syncs. iCloud copies node_modules (about 570 MB)
# and, when it gets confused, makes " 2" conflict copies of thousands of package files or of the whole
# folder, which breaks `tsc` and `next build`. iCloud never syncs a folder whose name ends in ".nosync".
#
# Layout: web/node_modules -> web/node_modules.nosync/node_modules (a symlink). The packages' real path
# still contains a "node_modules" segment, so Next/Turbopack treats them as packages (a plain
# web/node_modules.nosync folder without that segment made Next compile its own internals: 113 warnings).
#
# Usage: scripts/web_modules.sh   (idempotent; run it after changing web/package.json, instead of a plain
# `npm ci` in web/, which would replace the symlink with a real folder inside iCloud)
set -euo pipefail
WEB="$(cd "$(dirname "$0")/../web" && pwd)"
NOSYNC="$WEB/node_modules.nosync"
mkdir -p "$NOSYNC"
cp "$WEB/package.json" "$WEB/package-lock.json" "$NOSYNC/"
(cd "$NOSYNC" && npm ci --no-audit --no-fund)
if [ -L "$WEB/node_modules" ]; then
  ln -sfn node_modules.nosync/node_modules "$WEB/node_modules"
elif [ -e "$WEB/node_modules" ]; then
  trash="$HOME/.Trash/twm-web-node_modules-$(date +%Y%m%d-%H%M%S)"
  mv "$WEB/node_modules" "$trash"   # a real folder inside iCloud: moved to the Trash, not deleted
  echo "moved the old web/node_modules to $trash"
  ln -s node_modules.nosync/node_modules "$WEB/node_modules"
else
  ln -s node_modules.nosync/node_modules "$WEB/node_modules"
fi
echo "web/node_modules -> $(readlink "$WEB/node_modules")"

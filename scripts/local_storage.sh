#!/usr/bin/env bash
# Keep the virtualenv, the raw data cache, the Waiver Radar and streamer datasets, the
# predictions store, the warehouse and the trained models OUTSIDE a cloud-synced folder, with
# symlinks left in the project so every path in the code stays the same.
#
# Why: this checkout lives in ~/Desktop, which iCloud Drive syncs. iCloud marks every
# dot-folder (.venv) "hidden", and Python 3.13 skips hidden .pth files, so `import twm`
# breaks at random. It also uploads ~1.4 GB of rebuildable files and creates "name 2"
# conflict copies. Nothing here is tracked by git.
#
# Usage:  scripts/local_storage.sh            (idempotent; safe to re-run)
# Store:  $TWM_LOCAL_STORE, default ~/.local/share/two-minute-warning
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
STORE="${TWM_LOCAL_STORE:-$HOME/.local/share/two-minute-warning}"
PYTHON="${TWM_PYTHON:-/opt/homebrew/bin/python3.13}"
mkdir -p "$STORE" "$ROOT/data"

# Move a file or directory into the store (unless it is already there) and link it back.
relocate() {
  local rel="$1" kind="$2" p="$ROOT/$1" s="$STORE/$(basename "$1")"
  if [ -L "$p" ]; then
    echo "ok     $rel -> $(readlink "$p")"
    return
  fi
  if [ -e "$p" ] && [ -e "$s" ]; then
    echo "stop   both $p and $s exist; move one away by hand" >&2
    exit 1
  fi
  if [ -e "$p" ]; then
    mv "$p" "$s"
    echo "moved  $rel -> $s"
  elif [ "$kind" = dir ]; then
    mkdir -p "$s"
  fi
  ln -s "$s" "$p"
  echo "linked $rel -> $s"
}

# The virtualenv is rebuilt rather than moved (its scripts embed their own path).
if [ ! -L "$ROOT/.venv" ]; then
  rm -rf "$ROOT/.venv"
  rm -rf "$STORE/venv"
  uv venv --quiet --python "$PYTHON" "$STORE/venv"
  ln -s "$STORE/venv" "$ROOT/.venv"
  echo "linked .venv -> $STORE/venv"
else
  echo "ok     .venv -> $(readlink "$ROOT/.venv")"
fi
(cd "$ROOT" && uv sync --quiet --all-extras --locked)

relocate data/raw dir
relocate data/waiver_radar dir   # the Waiver Radar dataset (C3)
relocate data/streamer dir       # the K and D/ST streamer dataset (S1c)
relocate data/decisions dir      # G1: WP backtest fold predictions and summaries
relocate data/regression_watch dir  # H6-b: own-xFP folds and per-play expectations
relocate data/hot_seat dir       # H3: features and provisional backtests
relocate data/board dir          # I1b: the Cliff & Breakout dataset
relocate models dir              # trained production models (C6: models/waiver_radar/*.joblib)
# The predictions store (C4) is opened in place by DuckDB, so a symlinked file works.
if [ -e "$ROOT/data/predictions.duckdb" ] || [ -L "$ROOT/data/predictions.duckdb" ]; then
  relocate data/predictions.duckdb file
fi
# The warehouse is a file; twm build follows the link and rebuilds the target in place.
if [ -e "$ROOT/data/warehouse.duckdb" ] || [ -L "$ROOT/data/warehouse.duckdb" ]; then
  relocate data/warehouse.duckdb file
  rm -f "$ROOT/data/warehouse.duckdb.build.lock"  # the lock now lives next to the target
fi
# My League's data (F2; local only, never published) is linked even before the first sync:
# DuckDB follows the dangling link and creates the file in the store.
relocate data/league.duckdb file

(cd "$ROOT" && uv run python -c "import twm; print('import twm ok:', twm.__version__)")

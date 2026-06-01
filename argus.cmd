#!/usr/bin/env bash
# Thin wrapper that points uv at the iCloud-safe venv and forces a
# non-editable project install. Run this instead of `uv run argus` if
# you want zero env-var bookkeeping.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
export UV_PROJECT_ENVIRONMENT="${ARGUS_VENV:-$HOME/Library/Caches/argus/venv}"
export UV_NO_EDITABLE=1
exec uv run --project "$HERE" argus "$@"

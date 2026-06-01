#!/usr/bin/env bash
# Post-sync fixup. Two things every sync needs on macOS:
#
#   1. Write a non-underscore `argus.pth` pointing at the project root.
#      Hatchling's editable backend produces `_editable_impl_argus.pth`,
#      which Python 3.13 silently skips when its UF_HIDDEN flag is set.
#
#   2. Strip the UF_HIDDEN flag from every `.pth` file in site-packages.
#      uv applies that flag to every file it writes into `.venv/`; Python
#      3.13 skips any `.pth` file that carries it. (uv's choice is purely
#      cosmetic — Finder doesn't clutter when venv files are hidden — but
#      it collides head-on with CPython 3.13's site.py.)
#
# Idempotent. Run anytime after `uv sync` or a stray `uv pip install`.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
VENV="${UV_PROJECT_ENVIRONMENT:-${ARGUS_VENV:-$ROOT/.venv}}"

SITE="$(find "$VENV/lib" -maxdepth 2 -type d -name site-packages 2>/dev/null | head -1)"

if [[ -z "$SITE" ]]; then
  echo "  ✗ no site-packages found under $VENV — run uv sync (or bootstrap.sh) first" >&2
  exit 1
fi

# 1. Inject the non-underscore pth shim.
PTH="$SITE/argus.pth"
echo "$ROOT" > "$PTH"

# 2. Make every .pth file un-hidden so Python 3.13 will actually process them.
#    -h means "don't follow symlinks"; both directives are no-ops if the flag
#    is already absent.
find "$SITE" -maxdepth 1 -name "*.pth" -print0 |
  xargs -0 -I {} chflags -h nohidden "{}"

echo "  ⟨◇⟩  wrote $PTH"
echo "  ⟨◇⟩  unhid every .pth under $SITE"

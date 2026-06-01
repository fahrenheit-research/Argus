#!/usr/bin/env bash
# ARGUS Desktop — first-run installer
#
# Idempotent: safe to re-run. Bootstraps every dependency ARGUS needs:
#   1. Homebrew                (if missing)
#   2. Python 3.11+            (via Homebrew if missing)
#   3. uv                      (via Homebrew or astral installer)
#   4. ffmpeg                  (Telegram voice bubbles)
#   5. ARGUS source            (cloned or copied to ~/Library/Application Support/Argus)
#   6. ARGUS venv + voice deps (uv sync --extra voice)
#
# On every step we print [✓] / [→] / [✗] with brand colors. On any failure
# we exit non-zero with a clear remedy.

set -euo pipefail

# ── Brand ANSI palette ────────────────────────────────────────────────────────
MAGENTA=$'\033[38;2;255;56;209m'
GOLD=$'\033[38;2;255;194;71m'
CYAN=$'\033[38;2;66;232;245m'
DIM=$'\033[38;2;120;120;120m'
ERR=$'\033[38;2;255;92;92m'
BOLD=$'\033[1m'
RESET=$'\033[0m'

ARGUS_HOME="${HOME}/Library/Application Support/Argus"
ARGUS_SRC="${ARGUS_HOME}/src"
ARGUS_VENV="${HOME}/Library/Caches/argus/venv"

ok()   { printf "  ${GOLD}[✓]${RESET} %s\n" "$1"; }
step() { printf "  ${CYAN}[→]${RESET} %s\n" "$1"; }
fail() { printf "  ${ERR}[✗]${RESET} %s\n" "$1"; exit 1; }

header() {
  cat <<EOF

${BOLD}${MAGENTA}╭──────────────────────────────────────────╮${RESET}
${BOLD}${MAGENTA}│${RESET}  ${GOLD}⟨◇⟩${RESET}  ${BOLD}ARGUS Desktop Installer${RESET}        ${BOLD}${MAGENTA}│${RESET}
${BOLD}${MAGENTA}╰──────────────────────────────────────────╯${RESET}
  ${DIM}— the watchful agent that grows with you —${RESET}

EOF
}

header
mkdir -p "${ARGUS_HOME}"

# ── 1. Homebrew ──────────────────────────────────────────────────────────────
if ! command -v brew >/dev/null 2>&1; then
  step "Homebrew not found — installing…"
  if ! /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"; then
    fail "Homebrew install failed. Visit https://brew.sh and try the manual path."
  fi
  # Source brew shellenv for this shell
  if [[ -d "/opt/homebrew/bin" ]]; then
    eval "$(/opt/homebrew/bin/brew shellenv)"
  elif [[ -d "/usr/local/bin/brew" ]]; then
    eval "$(/usr/local/bin/brew shellenv)"
  fi
  ok "Homebrew installed."
else
  ok "Homebrew present ($(brew --version | head -1))."
fi

# ── 2. Python 3.11+ ──────────────────────────────────────────────────────────
PY_CMD=""
for cmd in python3.13 python3.12 python3.11; do
  if command -v "$cmd" >/dev/null 2>&1; then
    PY_CMD="$cmd"
    break
  fi
done

if [[ -z "$PY_CMD" ]]; then
  step "Python 3.11+ not found — installing python@3.13 via Homebrew…"
  brew install python@3.13 || fail "Python install failed."
  PY_CMD="python3.13"
fi
ok "Python ready ($PY_CMD)."

# ── 3. uv (Astral) ───────────────────────────────────────────────────────────
if ! command -v uv >/dev/null 2>&1; then
  step "uv not found — installing…"
  if ! curl -LsSf https://astral.sh/uv/install.sh | sh; then
    fail "uv install failed. Visit https://docs.astral.sh/uv/"
  fi
  # uv installer drops into ~/.local/bin
  export PATH="${HOME}/.local/bin:${PATH}"
fi
ok "uv ready ($(uv --version))."

# ── 4. ffmpeg (Telegram voice bubbles) ───────────────────────────────────────
if ! command -v ffmpeg >/dev/null 2>&1; then
  step "ffmpeg not found — installing (needed for Telegram voice bubbles)…"
  brew install ffmpeg || {
    printf "  ${DIM}ffmpeg install failed; continuing — TTS will fall back to send_audio.${RESET}\n"
  }
fi
if command -v ffmpeg >/dev/null 2>&1; then
  ok "ffmpeg ready."
fi

# ── 5. ARGUS source ─────────────────────────────────────────────────────────
if [[ ! -d "${ARGUS_SRC}/.git" && ! -f "${ARGUS_SRC}/pyproject.toml" ]]; then
  step "Locating ARGUS source…"
  # If we're running from inside the .app bundle, the source is alongside us
  BUNDLE_SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")/../Resources/argus-src" 2>/dev/null && pwd)" || BUNDLE_SRC=""
  if [[ -d "${BUNDLE_SRC}" && -f "${BUNDLE_SRC}/pyproject.toml" ]]; then
    cp -R "${BUNDLE_SRC}/" "${ARGUS_SRC}/"
    ok "Copied source from bundle → ${ARGUS_SRC}"
  elif [[ -d "${HOME}/Desktop/Argus" && -f "${HOME}/Desktop/Argus/pyproject.toml" ]]; then
    cp -R "${HOME}/Desktop/Argus/" "${ARGUS_SRC}/"
    ok "Copied source from ~/Desktop/Argus → ${ARGUS_SRC}"
  else
    fail "Cannot locate ARGUS source. Place it at ${ARGUS_SRC} and re-run."
  fi
fi

# ── 6. ARGUS venv + dependencies ─────────────────────────────────────────────
step "Syncing ARGUS dependencies (this can take a couple of minutes the first time)…"
cd "${ARGUS_SRC}"
export UV_NO_EDITABLE=1
if ! uv sync --extra voice; then
  fail "uv sync failed. Check ${ARGUS_SRC}/uv.lock and try:  cd ${ARGUS_SRC} && uv sync --extra voice"
fi
ok "Dependencies installed."

# ── 7. Sanity check ─────────────────────────────────────────────────────────
step "Running self-test…"
if ! "${ARGUS_VENV}/bin/argus" --version >/dev/null 2>&1; then
  fail "argus binary not callable. Try:  source ${ARGUS_VENV}/bin/activate && argus --version"
fi
ok "ARGUS self-test passed: $("${ARGUS_VENV}/bin/argus" --version)"

cat <<EOF

  ${GOLD}╭──────────────────────────────────────────╮${RESET}
  ${GOLD}│${RESET}  ${BOLD}Installation complete.${RESET}                  ${GOLD}│${RESET}
  ${GOLD}╰──────────────────────────────────────────╯${RESET}

  Next step: launch ${BOLD}Argus.app${RESET} from your Applications folder,
  or run ${CYAN}argus${RESET} from the terminal.

  First-time setup:
    ${DIM}\$${RESET} ${CYAN}argus setup${RESET}

EOF

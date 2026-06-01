#!/usr/bin/env bash
# ARGUS Desktop launcher — invoked by Argus.app/Contents/MacOS/Argus
#
# Flow:
#   1. Check whether ARGUS is installed (venv + argus binary exist)
#   2. If not, run the installer in a Terminal window first
#   3. Open a new Terminal window with a custom dark profile + launch argus
#
# Designed to be runnable standalone for testing:  ./desktop/scripts/launcher.sh

set -euo pipefail

ARGUS_VENV="${HOME}/Library/Caches/argus/venv"
ARGUS_BIN="${ARGUS_VENV}/bin/argus"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INSTALLER="${SCRIPT_DIR}/install.sh"

# ── First-run install (in a visible Terminal window) ─────────────────────────
if [[ ! -x "${ARGUS_BIN}" ]]; then
  if [[ ! -f "${INSTALLER}" ]]; then
    osascript -e 'display alert "ARGUS Installer Missing" message "Could not locate the installer script. Please reinstall." as critical'
    exit 1
  fi

  # Run the installer in a fresh Terminal window so the user sees progress.
  osascript <<EOF
tell application "Terminal"
  activate
  set newTab to do script "clear && bash '${INSTALLER}' && echo && echo 'Press Return to launch ARGUS…' && read && exit"
  set custom title of newTab to "ARGUS Installer"
end tell
EOF

  # Wait for the binary to appear (poll every 2s, max 10 min)
  for _ in {1..300}; do
    if [[ -x "${ARGUS_BIN}" ]]; then
      break
    fi
    sleep 2
  done

  if [[ ! -x "${ARGUS_BIN}" ]]; then
    osascript -e 'display alert "ARGUS install timed out" message "Re-run Argus.app once the installer Terminal window finishes." as warning'
    exit 1
  fi
fi

# ── Launch ARGUS chat in a new Terminal window with our dark profile ────────
# Create our custom Terminal profile if it doesn't exist yet (one-time).
"${SCRIPT_DIR}/install-terminal-profile.sh" 2>/dev/null || true

# Compose the command. `exec` so closing Terminal kills argus cleanly.
LAUNCH_CMD="clear && exec '${ARGUS_BIN}'"

osascript <<EOF
tell application "Terminal"
  activate
  -- Try our custom profile first; fall back to Basic if it's missing.
  try
    set newTab to do script "${LAUNCH_CMD}" with profile "Argus"
  on error
    set newTab to do script "${LAUNCH_CMD}"
  end try
  set custom title of newTab to "ARGUS ⟨◇⟩"
end tell
EOF

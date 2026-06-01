#!/usr/bin/env bash
# ARGUS — one-shot VPS installer (Ubuntu/Debian, optionally CentOS).
#
# What it does:
#   1. Creates a dedicated `argus` user (no shell login).
#   2. Installs system deps: python3.11+, ffmpeg, curl.
#   3. Installs uv to /home/argus/.local/bin
#   4. Clones (or pulls) ARGUS source to /opt/argus
#   5. uv sync --extra voice (Supertonic + edge-tts + soundfile + lameenc)
#   6. Installs the systemd unit and enables it.
#   7. Bootstraps an empty ~/.argus and prints next steps.
#
# Usage:
#   curl -fsSL https://raw.githubusercontent.com/...install-vps.sh | sudo bash
#   # or
#   sudo bash deploy/install-vps.sh
#
# Idempotent — re-running is safe.

set -euo pipefail

ARGUS_USER="argus"
ARGUS_HOME="/home/${ARGUS_USER}"
ARGUS_SRC="/opt/argus"
ARGUS_DATA="/var/lib/argus"
ARGUS_REPO="${ARGUS_REPO:-https://github.com/fahrenheit-research/argus.git}"

# ── ANSI palette ──
M=$'\033[38;2;255;56;209m'; G=$'\033[38;2;255;194;71m'; C=$'\033[38;2;66;232;245m'
D=$'\033[38;2;120;120;120m'; E=$'\033[38;2;255;92;92m'; B=$'\033[1m'; R=$'\033[0m'
ok()   { printf "  ${G}[✓]${R} %s\n" "$1"; }
step() { printf "  ${C}[→]${R} %s\n" "$1"; }
fail() { printf "  ${E}[✗]${R} %s\n" "$1"; exit 1; }

cat <<EOF

${B}${M}╭──────────────────────────────────────────────╮${R}
${B}${M}│${R}  ${G}⟨◇⟩${R}  ${B}ARGUS — VPS Installer${R}              ${B}${M}│${R}
${B}${M}╰──────────────────────────────────────────────╯${R}
  ${D}— the watchful agent that grows with you —${R}

EOF

# 0. Must be root
[ "$(id -u)" -eq 0 ] || fail "Run as root: sudo bash deploy/install-vps.sh"

# 1. System deps
step "Installing system dependencies…"
if command -v apt-get >/dev/null 2>&1; then
  apt-get update -qq
  DEBIAN_FRONTEND=noninteractive apt-get install -y -qq \
    python3 python3-venv python3-pip curl git ffmpeg \
    build-essential libffi-dev libssl-dev >/dev/null
elif command -v dnf >/dev/null 2>&1; then
  dnf install -y -q python3 python3-pip curl git ffmpeg gcc openssl-devel libffi-devel >/dev/null
else
  fail "Unsupported distro — install python3.11+, ffmpeg, curl, git manually then re-run."
fi
ok "System deps installed."

# 2. Dedicated user
if ! id "${ARGUS_USER}" >/dev/null 2>&1; then
  step "Creating user '${ARGUS_USER}'…"
  useradd --create-home --shell /usr/sbin/nologin --comment "ARGUS agent" "${ARGUS_USER}"
fi
mkdir -p "${ARGUS_DATA}" "${ARGUS_SRC}"
chown -R "${ARGUS_USER}:${ARGUS_USER}" "${ARGUS_DATA}" "${ARGUS_SRC}" "${ARGUS_HOME}"
ok "User '${ARGUS_USER}' ready."

# 3. uv
if ! sudo -u "${ARGUS_USER}" -H bash -c 'command -v ~/.local/bin/uv' >/dev/null 2>&1; then
  step "Installing uv (Astral)…"
  sudo -u "${ARGUS_USER}" -H bash -c 'curl -LsSf https://astral.sh/uv/install.sh | sh' >/dev/null
fi
UV_BIN="${ARGUS_HOME}/.local/bin/uv"
[ -x "${UV_BIN}" ] || fail "uv not found at ${UV_BIN} after install."
ok "uv ready: $(sudo -u "${ARGUS_USER}" "${UV_BIN}" --version)"

# 4. Source — clone or pull
if [ -d "${ARGUS_SRC}/.git" ]; then
  step "Updating existing ARGUS source at ${ARGUS_SRC}…"
  sudo -u "${ARGUS_USER}" git -C "${ARGUS_SRC}" pull --ff-only --quiet
else
  step "Cloning ARGUS to ${ARGUS_SRC}…"
  sudo -u "${ARGUS_USER}" git clone --quiet "${ARGUS_REPO}" "${ARGUS_SRC}"
fi
ok "Source ready."

# 5. Sync deps
step "Syncing Python deps (uv sync --extra voice — first run is ~3 min)…"
sudo -u "${ARGUS_USER}" -H bash -c "cd ${ARGUS_SRC} && UV_NO_EDITABLE=1 ${UV_BIN} sync --extra voice" >/dev/null
ok "Dependencies installed."

# 6. systemd unit
UNIT_SRC="${ARGUS_SRC}/deploy/argus-gateway.service"
UNIT_DST="/etc/systemd/system/argus-gateway.service"
if [ -f "${UNIT_SRC}" ]; then
  cp "${UNIT_SRC}" "${UNIT_DST}"
  systemctl daemon-reload
  ok "systemd unit installed."
else
  printf "  ${D}!${R}  Unit file missing at ${UNIT_SRC} — install manually later.\n"
fi

# 7. Print next steps
cat <<EOF

  ${G}╭──────────────────────────────────────────────╮${R}
  ${G}│${R}  ${B}Install complete.${R}                          ${G}│${R}
  ${G}╰──────────────────────────────────────────────╯${R}

  Next steps:

  1. Configure your provider + Telegram bot interactively:
     ${C}sudo -u ${ARGUS_USER} -H bash -c "cd ${ARGUS_SRC} && ${UV_BIN} run argus setup"${R}

  2. Start the gateway:
     ${C}sudo systemctl enable --now argus-gateway${R}

  3. Watch the logs:
     ${C}journalctl -u argus-gateway -f${R}

EOF

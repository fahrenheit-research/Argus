#!/usr/bin/env bash
# Build Argus.app — a self-contained macOS app bundle that launches ARGUS
# in a custom Terminal.app window with the brand profile.
#
# Output:  desktop/build/Argus.app
#
# Steps:
#   1. Wipe + recreate desktop/build/Argus.app/Contents/{MacOS,Resources}
#   2. Write Info.plist with the bundle metadata
#   3. Copy launcher + installer scripts into MacOS/
#   4. Copy the icon (assets/Argus.icns; generated from assets/icon.png if needed)
#   5. Optionally bundle ARGUS source under Resources/argus-src (for offline install)
#   6. Sign with ad-hoc signature so Gatekeeper doesn't quarantine

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
DESKTOP="${ROOT}/desktop"
BUILD="${DESKTOP}/build"
APP="${BUILD}/Argus.app"
ASSETS="${DESKTOP}/assets"

CYAN=$'\033[38;2;66;232;245m'
GOLD=$'\033[38;2;255;194;71m'
RESET=$'\033[0m'

echo "${CYAN}⟨◇⟩${RESET}  Building Argus.app at ${APP}"

# ── 1. Clean ────────────────────────────────────────────────────────────────
rm -rf "${APP}"
mkdir -p "${APP}/Contents/MacOS"
mkdir -p "${APP}/Contents/Resources"

# ── 2. Info.plist ───────────────────────────────────────────────────────────
cat > "${APP}/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleName</key>                 <string>Argus</string>
  <key>CFBundleDisplayName</key>          <string>Argus</string>
  <key>CFBundleIdentifier</key>           <string>co.fr.argus.desktop</string>
  <key>CFBundleVersion</key>              <string>0.1.0</string>
  <key>CFBundleShortVersionString</key>   <string>0.1.0</string>
  <key>CFBundlePackageType</key>          <string>APPL</string>
  <key>CFBundleSignature</key>            <string>ARGS</string>
  <key>CFBundleExecutable</key>           <string>Argus</string>
  <key>CFBundleIconFile</key>             <string>Argus</string>
  <key>NSHighResolutionCapable</key>      <true/>
  <key>LSMinimumSystemVersion</key>       <string>11.0</string>
  <key>LSApplicationCategoryType</key>    <string>public.app-category.developer-tools</string>
  <key>NSHumanReadableCopyright</key>     <string>© Fahrenheit Research</string>
  <key>NSAppleEventsUsageDescription</key>
    <string>Argus uses AppleEvents to launch the chat session in a Terminal window with its custom brand profile.</string>
</dict>
</plist>
PLIST

# ── 3. Launcher executable ───────────────────────────────────────────────────
cp "${DESKTOP}/scripts/launcher.sh"                   "${APP}/Contents/MacOS/Argus"
cp "${DESKTOP}/scripts/install.sh"                    "${APP}/Contents/MacOS/install.sh"
cp "${DESKTOP}/scripts/install-terminal-profile.sh"   "${APP}/Contents/MacOS/install-terminal-profile.sh"
chmod +x "${APP}/Contents/MacOS/Argus" \
         "${APP}/Contents/MacOS/install.sh" \
         "${APP}/Contents/MacOS/install-terminal-profile.sh"

# ── 4. Icon ─────────────────────────────────────────────────────────────────
if [[ -f "${ASSETS}/Argus.icns" ]]; then
  cp "${ASSETS}/Argus.icns" "${APP}/Contents/Resources/Argus.icns"
  echo "  ${GOLD}✓${RESET}  Copied existing Argus.icns"
elif [[ -f "${ASSETS}/icon.png" ]]; then
  echo "  ${CYAN}→${RESET}  Generating Argus.icns from icon.png…"
  "${DESKTOP}/scripts/make_icns.sh" "${ASSETS}/icon.png" "${APP}/Contents/Resources/Argus.icns"
else
  echo "  ${CYAN}!${RESET}  No icon found at ${ASSETS}/icon.png — bundle will use the generic icon."
fi

# ── 5. Optionally bundle ARGUS source for offline install ───────────────────
if [[ "${BUNDLE_SOURCE:-0}" == "1" ]]; then
  echo "  ${CYAN}→${RESET}  Bundling ARGUS source under Resources/argus-src…"
  rsync -a --exclude='__pycache__' --exclude='.git' --exclude='.venv' \
        --exclude='desktop/build' --exclude='node_modules' \
        "${ROOT}/" "${APP}/Contents/Resources/argus-src/"
fi

# ── 6. Ad-hoc codesign so Gatekeeper is happy on first launch ───────────────
if command -v codesign >/dev/null 2>&1; then
  # Clear extended attributes that block codesign on copied files.
  xattr -cr "${APP}" 2>/dev/null || true
  if codesign --force --deep --sign - "${APP}" >/dev/null 2>&1; then
    echo "  ${GOLD}✓${RESET}  Ad-hoc signed."
  else
    echo "  !  Ad-hoc sign failed (bundle still usable; Gatekeeper may quarantine it)."
  fi
fi

echo ""
echo "  ${GOLD}✓${RESET}  ${APP}"
echo "       ${CYAN}open '${APP}'${RESET}   to launch"
echo "       ${CYAN}cp -R '${APP}' /Applications/${RESET}   to install"

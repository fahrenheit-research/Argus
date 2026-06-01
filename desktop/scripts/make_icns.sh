#!/usr/bin/env bash
# Convert a square PNG → .icns icon set ready for a Mac .app bundle.
#
# Usage:  make_icns.sh <input.png> <output.icns>
# Needs:  sips + iconutil (both ship with macOS — no install required)

set -euo pipefail

IN="${1:?usage: make_icns.sh <input.png> <output.icns>}"
OUT="${2:?usage: make_icns.sh <input.png> <output.icns>}"

if [[ ! -f "${IN}" ]]; then
  echo "✗  Input PNG not found: ${IN}" >&2
  exit 1
fi

TMP="$(mktemp -d)/Argus.iconset"
mkdir -p "${TMP}"

# All required sizes for a fully Retina-ready icon set.
for sz in 16 32 64 128 256 512 1024; do
  sips -z $sz $sz "${IN}" --out "${TMP}/icon_${sz}x${sz}.png" >/dev/null
done

# @2x variants required by macOS — duplicate the 2x size into the 1x slot's @2x.
cp "${TMP}/icon_32x32.png"     "${TMP}/icon_16x16@2x.png"
cp "${TMP}/icon_64x64.png"     "${TMP}/icon_32x32@2x.png"
cp "${TMP}/icon_256x256.png"   "${TMP}/icon_128x128@2x.png"
cp "${TMP}/icon_512x512.png"   "${TMP}/icon_256x256@2x.png"
cp "${TMP}/icon_1024x1024.png" "${TMP}/icon_512x512@2x.png"

# Remove intermediate sizes that iconutil doesn't want
rm -f "${TMP}/icon_64x64.png" "${TMP}/icon_1024x1024.png"

iconutil -c icns "${TMP}" -o "${OUT}"

echo "✓  Wrote ${OUT} ($(du -h "${OUT}" | awk '{print $1}'))"

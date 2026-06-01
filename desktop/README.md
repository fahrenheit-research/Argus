# ARGUS Desktop — macOS `.app` bundle

A drop-in `.app` that mirrors the ARGUS CLI inside a custom Terminal
window. Double-clicking the icon launches the same chat REPL you get
from typing `argus` in a shell — but with the brand profile (dark
background, brand colors, 120×40 geometry, Menlo 14pt) and a one-shot
auto-installer that bootstraps every dependency the first time.

## Quick start

```bash
# Build the bundle
bash desktop/scripts/make_app.sh

# Try it in place
open desktop/build/Argus.app

# Install for real
cp -R desktop/build/Argus.app /Applications/
```

## What's in the bundle

```
Argus.app/
└── Contents/
    ├── Info.plist                       # Bundle metadata + icon ref
    ├── MacOS/
    │   ├── Argus                        # ← double-click entry point (launcher.sh)
    │   ├── install.sh                   # First-run installer
    │   └── install-terminal-profile.sh  # Imports the Argus Terminal profile
    └── Resources/
        └── Argus.icns                   # Multi-resolution icon (your PNG)
```

## What the installer does (idempotent — safe to re-run)

1. **Homebrew** — installs if missing
2. **Python 3.11+** — installs `python@3.13` via Homebrew if missing
3. **uv** — Astral's package manager (auto-installed)
4. **ffmpeg** — needed for Telegram voice bubbles (graceful fallback if it can't install)
5. **ARGUS source** — copied to `~/Library/Application Support/Argus/src`
6. **venv + dependencies** — `uv sync --extra voice` (installs supertonic, edge-tts, etc.)

Total time on a fresh Mac: 3–5 minutes. On a Mac with Homebrew + Python already installed: under 30 seconds.

## The launcher flow (`Argus.app/Contents/MacOS/Argus`)

1. Look for `~/Library/Caches/argus/venv/bin/argus`.
2. If missing → run the installer in a visible Terminal window so the user can see what's happening; poll for the binary to appear (max 10 min).
3. Once installed → ensure the **Argus** Terminal profile is registered.
4. Open a new Terminal window with that profile and `exec argus`.

Closing the Terminal window kills `argus` cleanly (the `exec` ensures no
zombie process).

## Customizing the icon

The build uses `desktop/assets/icon.png` if present. Replace that file
with any square PNG (1024×1024 recommended), then re-run
`make_app.sh` — `make_icns.sh` regenerates the multi-resolution
`.icns` automatically via `sips` + `iconutil` (both ship with macOS;
no extra tooling needed).

## Bundling source for offline installs

By default the installer expects to find ARGUS source either at
`~/Desktop/Argus` or copied into the bundle's `Resources/argus-src/`.
For a fully self-contained `.app` you can distribute (no internet
required), build with:

```bash
BUNDLE_SOURCE=1 bash desktop/scripts/make_app.sh
```

That copies the ARGUS tree (minus `.git`, `__pycache__`, etc.) into
`Argus.app/Contents/Resources/argus-src/`, which the installer then
copies to `~/Library/Application Support/Argus/src` on first launch.

## Code signing & notarization

The build ad-hoc signs the bundle (`codesign --sign -`) so Gatekeeper
won't immediately quarantine it. For distribution outside your own
Mac, you'll need a Developer ID certificate + `notarytool` submission.
That's out of scope for this build script.

## Uninstall

```bash
rm -rf /Applications/Argus.app
rm -rf ~/Library/Application\ Support/Argus
rm -rf ~/Library/Caches/argus
```

Your config (`~/.argus`) and workspace (`~/argus-workspace`) survive
the uninstall — wipe those too if you want a fully clean state.

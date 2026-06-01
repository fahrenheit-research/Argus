#!/usr/bin/env bash
# Set terminal font size to 16 for ARGUS.
# Run once; re-run any time you want to apply it.
set -euo pipefail

SIZE=16

echo "  ⟨◇⟩  Setting terminal font size to ${SIZE}pt …"

if [[ "$OSTYPE" == "darwin"* ]]; then

  # ── iTerm2 ──────────────────────────────────────────────────────
  if pgrep -x iTerm2 >/dev/null 2>&1 || pgrep -x iTerm >/dev/null 2>&1; then
    echo "  ⟨◇⟩  Applying to iTerm2 current session …"
    osascript -e "
      tell application \"iTerm2\"
        tell current session of current window
          set font size to ${SIZE}
        end tell
      end tell
    " 2>/dev/null && echo "  ✓  iTerm2 session updated" || echo "  ⚠  iTerm2 script failed — set manually via Settings → Profiles → Text → Font"
  fi

  # ── Apple Terminal ───────────────────────────────────────────────
  if pgrep -x Terminal >/dev/null 2>&1; then
    echo "  ⟨◇⟩  Applying to Apple Terminal current window …"
    osascript -e "
      tell application \"Terminal\"
        set the font size of the selected tab of the front window to ${SIZE}
      end tell
    " 2>/dev/null && echo "  ✓  Terminal updated" || echo "  ⚠  Terminal script failed — set manually via Settings → Profiles → Text → Font"
  fi

  # ── VS Code integrated terminal ──────────────────────────────────
  VSCODE_SETTINGS="$HOME/Library/Application Support/Code/User/settings.json"
  if [[ -f "$VSCODE_SETTINGS" ]]; then
    if command -v python3 >/dev/null 2>&1; then
      python3 - <<PYEOF
import json, re, pathlib

path = pathlib.Path("${VSCODE_SETTINGS}")
text = path.read_text()
# strip trailing commas so json.loads doesn't fail
text_clean = re.sub(r",(\s*[}\]])", r"\1", text)
try:
    data = json.loads(text_clean)
except json.JSONDecodeError:
    data = {}

data["terminal.integrated.fontSize"] = ${SIZE}
path.write_text(json.dumps(data, indent=2))
print("  ✓  VS Code settings.json updated")
PYEOF
    fi
  fi

  # ── Ghostty ──────────────────────────────────────────────────────
  GHOSTTY_CFG="$HOME/.config/ghostty/config"
  if [[ -f "$GHOSTTY_CFG" ]]; then
    if grep -q "^font-size" "$GHOSTTY_CFG"; then
      sed -i.bak "s/^font-size.*/font-size = ${SIZE}/" "$GHOSTTY_CFG" && rm "${GHOSTTY_CFG}.bak"
    else
      echo "font-size = ${SIZE}" >> "$GHOSTTY_CFG"
    fi
    echo "  ✓  Ghostty config updated (restart Ghostty to apply)"
  fi

  # ── Alacritty ────────────────────────────────────────────────────
  for ALACRITTY_CFG in \
    "$HOME/.config/alacritty/alacritty.toml" \
    "$HOME/.alacritty.toml" \
    "$HOME/.config/alacritty/alacritty.yml"; do
    if [[ -f "$ALACRITTY_CFG" ]]; then
      if grep -q "size" "$ALACRITTY_CFG"; then
        sed -i.bak "s/size *= *[0-9.]*/size = ${SIZE}/" "$ALACRITTY_CFG" && rm "${ALACRITTY_CFG}.bak"
      fi
      echo "  ✓  Alacritty config updated (restart to apply)"
      break
    fi
  done

else
  echo "  ⚠  Non-macOS platform — set font size manually in your terminal."
fi

cat <<EOF

  Font size set to ${SIZE}pt.

  Manual fallback if automation failed:
    iTerm2:  Settings → Profiles → Text → Font size → ${SIZE}
    Terminal: Settings → Profiles → Text → Font size → ${SIZE}
    VS Code:  "terminal.integrated.fontSize": ${SIZE}
    Ghostty:  font-size = ${SIZE}  in ~/.config/ghostty/config
    Alacritty: size = ${SIZE}  under [font] in alacritty.toml

  Restart your terminal for all changes to take effect.
EOF

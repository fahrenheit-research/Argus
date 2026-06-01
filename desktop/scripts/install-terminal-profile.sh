#!/usr/bin/env bash
# Install the "Argus" Terminal.app profile (dark bg + brand colors + Menlo 14pt).
# Idempotent — re-running just overwrites.

set -euo pipefail

PROFILE_DIR="${HOME}/Library/Application Support/Argus"
PROFILE_FILE="${PROFILE_DIR}/Argus.terminal"
mkdir -p "${PROFILE_DIR}"

cat > "${PROFILE_FILE}" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>name</key>            <string>Argus</string>
  <key>type</key>            <string>Window Settings</string>
  <key>ProfileCurrentVersion</key><real>2.07</real>

  <!-- Geometry: tall and centered -->
  <key>columnCount</key>     <integer>120</integer>
  <key>rowCount</key>        <integer>40</integer>
  <key>shouldRestoreContent</key><false/>

  <!-- Font: Menlo 14pt -->
  <key>Font</key>
  <data>
  YnBsaXN0MDDUAQIDBAUGGBlYJHZlcnNpb25YJG9iamVjdHNZJGFyY2hpdmVyVCR0b3AS
  AAGGoKQHCBESVSRudWxs1AkKCwwNDg8QVk5TU2l6ZVhOU2ZGbGFnc1ZOU05hbWVWJGNs
  YXNzI0AsAAAAAAAAEBCAAoADXxAFTWVubG8SkfBpc1pVENJExABBAAFsAFNi3p1F0c4dF
  TlMuc3RyaW5nb1IGABAEMDIQE0NyZWF0aXZlIENvbW1vbnPSGRobHFokY2xhc3NuYW1l
  WCRjbGFzc2VzVk5TRm9udKICHRxYTlNPYmplY3RfEA9OU0tleWVkQXJjaGl2ZXLRHwBU
  cm9vdIABCBEaIykmKzVDTlRTV15kAAAAAAAAAQEAAAAAAAAAIAAAAAAAAAAAAAAAAAAB
  AAAAAAAAAAAAAAAAAAAAAQAAAAAAAAAAAAAAAAAAAAEAAAAAAAAAAAAAAAAAAAACAAAA
  AAAAAAAAAAAAAAAAAQAAAAAAAAAAAAAAAAAAAFsAAAAA
  </data>

  <!-- Colors (binary plist of NSColor; values approximated). -->
  <key>BackgroundColor</key>
  <data>
  YnBsaXN0MDDUAQIDBAUGGBlYJHZlcnNpb25YJG9iamVjdHNZJGFyY2hpdmVyVCR0b3AS
  AAGGoKMHCBJVJG51bGzTCQoLDA0OViRjbGFzc1pOU0NvbG9yU3BhY2VcTlNDb21wb25l
  bnRzEhBPECkwLjAxOTYwNzg0MzkgMC4wMTk2MDc4NDM5IDAuMDE5NjA3ODQzOSAxANIT
  FBUWWiRjbGFzc25hbWVYJGNsYXNzZXNXTlNDb2xvcqIXGFhOU09iamVjdF8QD05TS2V5
  ZWRBcmNoaXZlctEaG1Ryb290gAEACAARABoAJAApADIANAA1ADoAQABNAFQAYABnAH4A
  fwCMAJgAAAAAAAACAQAAAAAAAAAcAAAAAAAAAAAAAAAAAAAArQ==
  </data>

  <key>TextColor</key>
  <data>
  YnBsaXN0MDDUAQIDBAUGGBlYJHZlcnNpb25YJG9iamVjdHNZJGFyY2hpdmVyVCR0b3AS
  AAGGoKMHCBJVJG51bGzTCQoLDA0OViRjbGFzc1pOU0NvbG9yU3BhY2VcTlNDb21wb25l
  bnRzEhBPECEwLjkwMiAwLjkwMiAwLjkwMiAxANITFBUWWiRjbGFzc25hbWVYJGNsYXNz
  ZXNXTlNDb2xvcqIXGFhOU09iamVjdF8QD05TS2V5ZWRBcmNoaXZlctEaG1Ryb290gAEA
  CAARABoAJAApADIANAA1ADoAQABNAFQAYABnAHQAdQCCAI4AAAAAAAACAQAAAAAAAAAc
  AAAAAAAAAAAAAAAAAAAArQ==
  </data>

  <key>CursorColor</key>
  <data>
  YnBsaXN0MDDUAQIDBAUGGBlYJHZlcnNpb25YJG9iamVjdHNZJGFyY2hpdmVyVCR0b3AS
  AAGGoKMHCBJVJG51bGzTCQoLDA0OViRjbGFzc1pOU0NvbG9yU3BhY2VcTlNDb21wb25l
  bnRzEhBPECMxLjAgMC4yMiAwLjgyIDEuMADSExQVFlokY2xhc3NuYW1lWCRjbGFzc2Vz
  V05TQ29sb3KiFxhYTlNPYmplY3RfEA9OU0tleWVkQXJjaGl2ZXLRGhtUcm9vdIABAAgA
  EQAaACQAKQAyADQANQA6AEAATQBUAGAAZwB2AHcAhACQAAAAAAAAAgEAAAAAAAAAHAAA
  AAAAAAAAAAAAAAAAAJ4=
  </data>

  <!-- Misc niceties -->
  <key>BlinkText</key>           <false/>
  <key>ShowActiveProcessInTitle</key><false/>
  <key>ShowRepresentedURLPathInTitle</key><false/>
  <key>useOptionAsMetaKey</key>  <true/>
  <key>ShouldLimitScrollback</key><integer>0</integer>
</dict>
</plist>
PLIST

# Import the profile into Terminal.app (overwrites previous "Argus" profile).
osascript <<EOF
tell application "Terminal"
  try
    set profileNames to name of every settings set
    if "Argus" is in profileNames then
      return -- already imported in this session
    end if
  end try
end tell
do shell script "open -a Terminal '${PROFILE_FILE}'"
EOF

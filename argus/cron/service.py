"""Generate a launchd plist (macOS) so the cron daemon auto-starts on login.

Linux users get a systemd --user unit. Both files are written but NOT loaded
automatically; the CLI prints the one command needed to enable.
"""
from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path
from textwrap import dedent


def _argus_bin() -> str:
    """Resolve the absolute path to the argus binary."""
    # Prefer the venv's argus binary (so the service uses the same env)
    venv = os.environ.get("UV_PROJECT_ENVIRONMENT", "")
    cands = []
    if venv:
        cands.append(Path(venv) / "bin" / "argus")
    cands.extend([
        Path.home() / "Library/Caches/argus/venv/bin/argus",
        Path.home() / ".local/bin/argus",
        Path(shutil.which("argus") or "/usr/local/bin/argus"),
    ])
    for c in cands:
        if c.is_file() and os.access(c, os.X_OK):
            return str(c)
    return "argus"


def write_launchd_plist() -> Path:
    """macOS only — write ~/Library/LaunchAgents/ai.argus.cron.plist."""
    if sys.platform != "darwin":
        raise RuntimeError("launchd is macOS only — use install-systemd-service on Linux")

    plist_dir = Path.home() / "Library/LaunchAgents"
    plist_dir.mkdir(parents=True, exist_ok=True)
    plist_path = plist_dir / "ai.argus.cron.plist"

    argus_path = _argus_bin()
    log_dir    = Path.home() / ".argus/logs"
    log_dir.mkdir(parents=True, exist_ok=True)

    plist = dedent(f"""\
        <?xml version="1.0" encoding="UTF-8"?>
        <!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
          "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
        <plist version="1.0">
        <dict>
            <key>Label</key>
            <string>ai.argus.cron</string>
            <key>ProgramArguments</key>
            <array>
                <string>{argus_path}</string>
                <string>cron</string>
                <string>daemon</string>
            </array>
            <key>RunAtLoad</key>
            <true/>
            <key>KeepAlive</key>
            <true/>
            <key>StandardOutPath</key>
            <string>{log_dir}/cron-daemon.log</string>
            <key>StandardErrorPath</key>
            <string>{log_dir}/cron-daemon.err</string>
            <key>EnvironmentVariables</key>
            <dict>
                <key>PATH</key>
                <string>/usr/local/bin:/usr/bin:/bin:/opt/homebrew/bin</string>
            </dict>
        </dict>
        </plist>
    """)
    plist_path.write_text(plist)
    return plist_path


def write_systemd_unit() -> Path:
    """Linux only — write ~/.config/systemd/user/argus-cron.service."""
    if sys.platform == "darwin":
        raise RuntimeError("systemd is Linux only — use install-launchd-service on macOS")

    unit_dir = Path.home() / ".config/systemd/user"
    unit_dir.mkdir(parents=True, exist_ok=True)
    unit_path = unit_dir / "argus-cron.service"
    argus_path = _argus_bin()

    unit = dedent(f"""\
        [Unit]
        Description=ARGUS Cron Daemon — scheduled jobs with multi-agent swarming
        After=network-online.target

        [Service]
        Type=simple
        ExecStart={argus_path} cron daemon
        Restart=always
        RestartSec=10
        StandardOutput=append:%h/.argus/logs/cron-daemon.log
        StandardError=append:%h/.argus/logs/cron-daemon.err

        [Install]
        WantedBy=default.target
    """)
    unit_path.write_text(unit)
    return unit_path

---
name: safe-shell
category: system
description: "Shell command execution wrapper — explicit approval, audit log, never destructive without two-step confirm."
triggers: [run, execute, shell, command, cli]
tools: [shell, memory]
author: ARGUS · Fahrenheit Research
version: 1.0.0
---

# Safe shell

Fires when the user asks to run any shell command.

Steps:
1. Propose the exact command in a code block; DO NOT call `run_command` yet.
2. Ask for explicit `/approve <command>` from the user. (The `shell` tool
   will return DENIED otherwise.)
3. For destructive operations (`rm`, `mv`, `dd`, `chmod`, anything that
   touches `/`, `~/`, or `*`), require a second confirmation: explain
   *what files will be lost* in one line before executing.
4. After execution, summarize stdout (first 2000 chars) and exit code.
5. If exit code is non-zero, propose the next action (rerun with `-v`,
   check a file, etc.) but do not auto-rerun.
6. Append a one-line audit entry to MEMORY.md when the command altered state.

Never silently swallow stderr. Never chain destructive commands.

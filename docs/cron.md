# ARGUS Cron

Persistent scheduled prompts with multi-agent swarming.

A cron job in ARGUS is a **cron expression + a prompt**. On schedule, the
prompt runs through the normal agent loop with all tools available. If the
prompt looks like a multi-task workflow (e.g. "research X **and** email Y"),
the **Constellation auto-swarm** engages so multiple roles work in parallel.

## Install

```bash
uv sync --extra cron
```

This adds:
- `apscheduler` — battle-tested scheduling primitives
- `croniter` — cron expression parsing
- `tzlocal` — local-timezone-aware fire times

## Add a job

```bash
argus cron add "0 9 * * 1-5"  "Brief me on overnight market moves and post the summary to Slack"
argus cron add "*/30 * * * *" "Check Gmail and triage any urgent items"
argus cron add "0 17 * * *"   "Daily standup — summarise what I shipped today and email it to me"
```

`add` validates the cron expression up-front. On success it prints the new
job's id and the schedule.

### Cron expression cheatsheet

```
 ┌── minute       (0-59)
 │ ┌── hour       (0-23)
 │ │ ┌── day      (1-31)
 │ │ │ ┌── month  (1-12)
 │ │ │ │ ┌── day of week (0-6, Sun=0)
 │ │ │ │ │
 * * * * *
```

| Expression | Meaning |
|---|---|
| `0 9 * * *` | Every day at 09:00 |
| `*/15 * * * *` | Every 15 minutes |
| `0 9 * * 1-5` | Weekdays at 09:00 |
| `0 0 1 * *` | First of the month at midnight |
| `30 18 * * 5` | Fridays at 18:30 |

## Swarm modes

By default, ARGUS auto-detects multi-task prompts and swarms when needed.
You can override per-job:

```bash
argus cron add "..." "..." --swarm always    # force Constellation
argus cron add "..." "..." --swarm never     # always one-shot agent
argus cron add "..." "..." --swarm auto      # default
```

Multi-task detection rules (see `argus/swarm/auto.py`): cross-domain verbs
(research + write + email = 3 domains), connective conjunctions ("and",
"then"), task-segment counters, prompt length. Threshold: 0.50.

## Run a job once (for testing)

```bash
argus cron run <job-id>
```

Fires immediately, prints the result + duration + token count. The schedule
is unaffected.

## Daemon

Jobs only fire when the daemon is running. Two modes:

```bash
argus cron daemon                  # foreground (Ctrl-C to stop)
argus cron install-service         # background — auto-starts on login
```

### macOS — launchd

`install-service` writes `~/Library/LaunchAgents/ai.argus.cron.plist` and
prints the command to enable it:

```bash
launchctl load -w ~/Library/LaunchAgents/ai.argus.cron.plist
```

### Linux — systemd --user

Writes `~/.config/systemd/user/argus-cron.service`:

```bash
systemctl --user daemon-reload
systemctl --user enable --now argus-cron.service
```

Logs land in `~/.argus/logs/cron-daemon.{log,err}`.

## Inspect runs

```bash
argus cron list                 # all jobs + run counts + enabled status
argus cron logs                 # last 15 runs across all jobs
argus cron logs --id abc123     # only this job
argus cron logs -n 50           # last 50 runs
```

## Pause / resume

```bash
argus cron disable abc123       # keep the job, stop firing
argus cron enable  abc123
argus cron remove  abc123       # delete forever
```

## Storage

- `~/.argus/cron.db` — jobs + run history (you should back this up)
- `~/.argus/apscheduler.db` — APScheduler's own bookkeeping (regenerated on start)

Reset cron without losing the rest of `~/.argus/`:

```bash
rm ~/.argus/cron.db ~/.argus/apscheduler.db
```

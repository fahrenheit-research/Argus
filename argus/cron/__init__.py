"""ARGUS Cron — persistent, swarm-aware scheduled jobs.

A job is anything you can phrase as a prompt to ARGUS. Add one with a cron
expression and ARGUS will fire it on schedule, route it through the agent
loop (auto-swarming multi-task prompts), and store the result.

  argus cron add "0 9 * * 1-5"  "Brief me on overnight market moves"
  argus cron add "*/30 * * * *" "Check email and triage urgent"
  argus cron list
  argus cron run <id>            # fire once, now, for testing
  argus cron daemon              # run the scheduler in the foreground
  argus cron install-service     # generate launchd plist so it auto-starts

Storage: ~/.argus/cron.db (SQLite). One file. Survives reboots. Backups
trivial. No service dependency.
"""
from argus.cron.store   import CronStore, CronJob, JobRun
from argus.cron.runner  import run_job_once, build_scheduler

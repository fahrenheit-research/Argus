"""Cron job execution — routes scheduled prompts through ARGUS.

A scheduled prompt becomes a normal agent turn with one extra twist: if
the prompt looks like a multi-task workflow (e.g. "research X AND email Y")
the Constellation auto-swarm is engaged so multiple roles work in parallel.

This module is lazy-import-friendly so installing the cron extra without
the api/voice/office extras still works.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Optional

from argus.cron.store import CronStore, CronJob, JobRun

log = logging.getLogger("argus.cron.runner")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


async def run_job_once(job: CronJob, *, store: Optional[CronStore] = None) -> JobRun:
    """Execute a single job's prompt and return the JobRun record.

    Routing logic:
      1. If job.use_swarm == 1   → Constellation (forced).
      2. If job.use_swarm == 0   → normal one-shot agent loop.
      3. If job.use_swarm == -1  → auto-detect via argus.swarm.auto.should_swarm.
    """
    if store is None:
        store = CronStore()

    run = store.begin_run(job.id)
    log.info("cron run start: %s (%s) — %r", job.name, job.id, job.prompt[:80])

    try:
        from argus import config as _config
        from argus.swarm.auto import should_swarm
        cfg = _config.load()

        # Decide whether to swarm
        if job.use_swarm == 1:
            use_swarm = True
            reason    = "forced"
        elif job.use_swarm == 0:
            use_swarm = False
            reason    = "disabled"
        else:
            use_swarm, conf, reason = should_swarm(job.prompt)

        if use_swarm:
            from argus.swarm.constellation import Constellation
            con    = Constellation(cfg=cfg, goal=job.prompt)
            result = await con.run()
            run.result      = (result.get("final") or "")[:2000]
            run.tokens_used = int(result.get("tokens", 0))
            run.used_swarm  = True
            log.info("cron run done (swarm/%s): %s", reason, run.tokens_used)
        else:
            from argus.loop import run_turn, Conversation
            from argus.tools.registry import register_defaults
            register_defaults()
            convo = Conversation()
            parts: list[str] = []
            async for evt in run_turn(cfg, convo, job.prompt):
                if type(evt).__name__ == "TextEvent" and getattr(evt, "text", None):
                    parts.append(evt.text)
            run.result      = "".join(parts)[:2000]
            run.used_swarm  = False
            log.info("cron run done (one-shot/%s)", reason)

        run.status = "ok"

    except Exception as e:  # noqa: BLE001
        run.status = "error"
        run.error  = f"{type(e).__name__}: {e}"
        log.exception("cron run failed: %s", job.id)

    store.finish_run(run)
    store.mark_run(job.id)
    return run


# ── APScheduler integration ──────────────────────────────────────────────────

def _trigger_for(schedule: str):
    """Convert a cron expression into an APScheduler CronTrigger.

    Accepts standard 5-field cron (minute hour dom month dow). Raises
    ValueError with a helpful message on malformed input.
    """
    try:
        from apscheduler.triggers.cron import CronTrigger
    except ImportError as e:
        raise RuntimeError("APScheduler not installed. Run: uv sync --extra cron") from e

    fields = schedule.strip().split()
    if len(fields) != 5:
        raise ValueError(
            f"cron schedule must be 5 fields (minute hour dom month dow), "
            f"got {len(fields)}: {schedule!r}\n"
            f"Examples:\n"
            f"  '0 9 * * *'      every day at 09:00\n"
            f"  '*/15 * * * *'   every 15 minutes\n"
            f"  '0 9 * * 1-5'    weekdays at 09:00\n"
            f"  '0 0 1 * *'      first of the month at midnight"
        )
    minute, hour, day, month, dow = fields
    return CronTrigger(minute=minute, hour=hour, day=day, month=month,
                        day_of_week=dow)


def build_scheduler() -> "AsyncIOScheduler":
    """Build the APScheduler instance with persistent SQLite jobstore.

    The jobstore is independent of our cron_jobs table — it stores
    APScheduler's bookkeeping (next-fire times, misfires). Our table
    is the source of truth for "what jobs exist"; APScheduler reads
    from it on startup and registers triggers.
    """
    try:
        from apscheduler.schedulers.asyncio import AsyncIOScheduler
        from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
    except ImportError as e:
        raise RuntimeError("APScheduler not installed. Run: uv sync --extra cron") from e

    import os
    from pathlib import Path
    apscheduler_db = Path(os.path.expanduser("~/.argus/apscheduler.db"))
    apscheduler_db.parent.mkdir(parents=True, exist_ok=True)

    jobstores = {
        "default": SQLAlchemyJobStore(url=f"sqlite:///{apscheduler_db}")
    }
    return AsyncIOScheduler(jobstores=jobstores, timezone="local")


async def run_daemon() -> None:
    """Run the cron daemon forever. Re-syncs jobs from the store every 30s."""
    store     = CronStore()
    scheduler = build_scheduler()

    def _make_callable(job_id: str):
        async def _run():
            job = store.get(job_id)
            if not job or not job.enabled:
                return
            await run_job_once(job, store=store)
        return _run

    def _sync():
        """Register/refresh APScheduler jobs from our cron_jobs table."""
        registered: set[str] = {j.id for j in scheduler.get_jobs()}
        in_store:   dict[str, CronJob] = {j.id: j for j in store.list(enabled_only=True)}

        # Add or update
        for jid, cj in in_store.items():
            try:
                trig = _trigger_for(cj.schedule)
            except ValueError as e:
                log.warning("skipping job %s (%s): %s", cj.id, cj.name, e)
                continue
            if jid in registered:
                scheduler.reschedule_job(jid, trigger=trig)
            else:
                scheduler.add_job(_make_callable(jid), trigger=trig, id=jid,
                                  name=cj.name, replace_existing=True)

        # Remove jobs that are no longer in the store (or are disabled)
        for jid in registered - set(in_store):
            try: scheduler.remove_job(jid)
            except Exception: pass

    _sync()
    scheduler.start()
    log.info("cron daemon started — %d jobs scheduled", len(scheduler.get_jobs()))

    # Periodic resync so adding a job via `argus cron add` takes effect
    # without restarting the daemon.
    try:
        while True:
            await asyncio.sleep(30)
            _sync()
    except (KeyboardInterrupt, asyncio.CancelledError):
        log.info("cron daemon shutting down")
    finally:
        scheduler.shutdown(wait=False)

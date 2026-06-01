"""SQLite-backed cron job store.

Schema:

  CREATE TABLE cron_jobs (
      id           TEXT PRIMARY KEY,        -- 8-char ulid-ish
      name         TEXT NOT NULL,           -- human-readable label
      schedule     TEXT NOT NULL,           -- cron expression e.g. "0 9 * * *"
      prompt       TEXT NOT NULL,           -- what to run (free-form English)
      enabled      INTEGER NOT NULL DEFAULT 1,
      use_swarm    INTEGER NOT NULL DEFAULT -1,  -- -1=auto, 0=never, 1=always
      created_at   TEXT NOT NULL,
      last_run_at  TEXT,
      next_run_at  TEXT,
      run_count    INTEGER NOT NULL DEFAULT 0
  );

  CREATE TABLE cron_runs (
      id           INTEGER PRIMARY KEY AUTOINCREMENT,
      job_id       TEXT NOT NULL REFERENCES cron_jobs(id) ON DELETE CASCADE,
      started_at   TEXT NOT NULL,
      finished_at  TEXT,
      status       TEXT NOT NULL,           -- running | ok | error
      result       TEXT,                    -- truncated final output
      error        TEXT,
      tokens_used  INTEGER DEFAULT 0,
      used_swarm   INTEGER DEFAULT 0,
      duration_ms  INTEGER
  );

This file has zero external dependencies (stdlib sqlite3 + dataclasses).
"""
from __future__ import annotations

import os
import sqlite3
import uuid
from dataclasses import dataclass, asdict, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


DB_PATH = Path(os.path.expanduser("~/.argus/cron.db"))


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class CronJob:
    id:           str
    name:         str
    schedule:     str
    prompt:       str
    enabled:      bool             = True
    use_swarm:    int              = -1   # -1=auto, 0=never, 1=always
    created_at:   str              = ""
    last_run_at:  Optional[str]    = None
    next_run_at:  Optional[str]    = None
    run_count:    int              = 0


@dataclass
class JobRun:
    job_id:       str
    started_at:   str
    finished_at:  Optional[str]    = None
    status:       str              = "running"
    result:       Optional[str]    = None
    error:        Optional[str]    = None
    tokens_used:  int              = 0
    used_swarm:   bool             = False
    duration_ms:  int              = 0
    id:           Optional[int]    = None


_SCHEMA = """
CREATE TABLE IF NOT EXISTS cron_jobs (
    id           TEXT PRIMARY KEY,
    name         TEXT NOT NULL,
    schedule     TEXT NOT NULL,
    prompt       TEXT NOT NULL,
    enabled      INTEGER NOT NULL DEFAULT 1,
    use_swarm    INTEGER NOT NULL DEFAULT -1,
    created_at   TEXT NOT NULL,
    last_run_at  TEXT,
    next_run_at  TEXT,
    run_count    INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS cron_runs (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id       TEXT NOT NULL,
    started_at   TEXT NOT NULL,
    finished_at  TEXT,
    status       TEXT NOT NULL,
    result       TEXT,
    error        TEXT,
    tokens_used  INTEGER DEFAULT 0,
    used_swarm   INTEGER DEFAULT 0,
    duration_ms  INTEGER,
    FOREIGN KEY (job_id) REFERENCES cron_jobs(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_cron_runs_job ON cron_runs(job_id, started_at DESC);
"""


class CronStore:
    """Thread-safe sqlite-backed cron store. Auto-creates the DB."""

    def __init__(self, path: Path = DB_PATH) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self._init()

    def _init(self) -> None:
        with self._conn() as c:
            c.executescript(_SCHEMA)
            c.commit()

    def _conn(self) -> sqlite3.Connection:
        # check_same_thread=False because APScheduler may call from worker threads.
        c = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA foreign_keys = ON")
        return c

    # ── Job CRUD ────────────────────────────────────────────────────────────

    def add(self, name: str, schedule: str, prompt: str, *,
            use_swarm: int = -1) -> CronJob:
        job = CronJob(
            id          = uuid.uuid4().hex[:8],
            name        = name,
            schedule    = schedule,
            prompt      = prompt,
            use_swarm   = use_swarm,
            created_at  = _now_iso(),
        )
        with self._conn() as c:
            c.execute(
                """INSERT INTO cron_jobs (id, name, schedule, prompt, enabled,
                       use_swarm, created_at, run_count)
                   VALUES (?, ?, ?, ?, 1, ?, ?, 0)""",
                (job.id, job.name, job.schedule, job.prompt,
                 job.use_swarm, job.created_at))
        return job

    def list(self, *, enabled_only: bool = False) -> list[CronJob]:
        with self._conn() as c:
            q = "SELECT * FROM cron_jobs"
            if enabled_only:
                q += " WHERE enabled = 1"
            q += " ORDER BY created_at DESC"
            return [CronJob(**dict(r)) for r in c.execute(q).fetchall()]

    def get(self, job_id: str) -> Optional[CronJob]:
        with self._conn() as c:
            r = c.execute("SELECT * FROM cron_jobs WHERE id = ?", (job_id,)).fetchone()
            return CronJob(**dict(r)) if r else None

    def update(self, job_id: str, **fields) -> bool:
        if not fields:
            return False
        cols = ", ".join(f"{k} = ?" for k in fields)
        with self._conn() as c:
            cur = c.execute(f"UPDATE cron_jobs SET {cols} WHERE id = ?",
                            (*fields.values(), job_id))
            return cur.rowcount > 0

    def delete(self, job_id: str) -> bool:
        with self._conn() as c:
            cur = c.execute("DELETE FROM cron_jobs WHERE id = ?", (job_id,))
            return cur.rowcount > 0

    def set_enabled(self, job_id: str, enabled: bool) -> bool:
        return self.update(job_id, enabled=1 if enabled else 0)

    def mark_run(self, job_id: str, when: Optional[str] = None,
                  next_when: Optional[str] = None) -> None:
        """Update last_run_at + next_run_at + bump run_count."""
        with self._conn() as c:
            c.execute(
                """UPDATE cron_jobs
                      SET last_run_at = ?, next_run_at = ?,
                          run_count   = run_count + 1
                    WHERE id = ?""",
                (when or _now_iso(), next_when, job_id))

    # ── Run history ─────────────────────────────────────────────────────────

    def begin_run(self, job_id: str) -> JobRun:
        run = JobRun(job_id=job_id, started_at=_now_iso())
        with self._conn() as c:
            cur = c.execute(
                """INSERT INTO cron_runs (job_id, started_at, status)
                       VALUES (?, ?, 'running')""",
                (job_id, run.started_at))
            run.id = cur.lastrowid
        return run

    def finish_run(self, run: JobRun) -> None:
        run.finished_at  = _now_iso()
        try:
            dt = datetime.fromisoformat(run.finished_at) - datetime.fromisoformat(run.started_at)
            run.duration_ms = int(dt.total_seconds() * 1000)
        except Exception:
            run.duration_ms = 0
        with self._conn() as c:
            c.execute(
                """UPDATE cron_runs
                      SET finished_at = ?, status = ?, result = ?,
                          error = ?, tokens_used = ?, used_swarm = ?,
                          duration_ms = ?
                    WHERE id = ?""",
                (run.finished_at, run.status, run.result, run.error,
                 run.tokens_used, 1 if run.used_swarm else 0,
                 run.duration_ms, run.id))

    def recent_runs(self, job_id: Optional[str] = None,
                     limit: int = 20) -> list[JobRun]:
        with self._conn() as c:
            q = "SELECT * FROM cron_runs"
            params: tuple = ()
            if job_id:
                q += " WHERE job_id = ?"
                params = (job_id,)
            q += " ORDER BY started_at DESC LIMIT ?"
            params = (*params, limit)
            rows = c.execute(q, params).fetchall()
            out = []
            for r in rows:
                d = dict(r)
                d["used_swarm"] = bool(d.get("used_swarm"))
                out.append(JobRun(**d))
            return out

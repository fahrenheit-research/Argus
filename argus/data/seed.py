"""Canned content for the design build — sessions, skills, memory.

Plausible, on-brand content so every viewer screen has something real to
render. When SQLite goes live, this gets replaced by real reads; the shape
matches the §13.2 schema so the swap is mechanical.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone


@dataclass(frozen=True)
class SessionRow:
    id: str
    title: str
    provider: str
    model: str
    last_active: datetime
    messages: int
    tokens_in: int
    tokens_out: int
    parent_id: str | None = None
    source: str = "cli"  # "cli" | "telegram"


_now = datetime(2026, 5, 30, 22, 13, tzinfo=timezone.utc)

SESSIONS: list[SessionRow] = [
    SessionRow(
        id="20260530_211522_a1b2c3",
        title="refactor auth module",
        provider="groq",
        model="llama-3.3-70b-versatile",
        last_active=_now - timedelta(minutes=4),
        messages=42,
        tokens_in=18_402,
        tokens_out=9_881,
        source="cli",
    ),
    SessionRow(
        id="20260530_182201_d4e5f6",
        title="PR triage — argus repo",
        provider="anthropic",
        model="claude-opus-4-7",
        last_active=_now - timedelta(hours=4),
        messages=17,
        tokens_in=7_209,
        tokens_out=4_410,
        source="telegram",
    ),
    SessionRow(
        id="20260529_093015_77aabb",
        title="trip itinerary — kyoto",
        provider="groq",
        model="llama-3.1-8b-instant",
        last_active=_now - timedelta(days=1, hours=13),
        messages=9,
        tokens_in=1_204,
        tokens_out=2_115,
        source="telegram",
    ),
    SessionRow(
        id="20260528_140044_99ccdd",
        title="kubernetes pod debug",
        provider="anthropic",
        model="claude-sonnet-4-6",
        last_active=_now - timedelta(days=2, hours=8),
        messages=31,
        tokens_in=22_991,
        tokens_out=11_004,
        parent_id="20260527_220511_88eeff",
        source="cli",
    ),
    SessionRow(
        id="20260527_220511_88eeff",
        title="kubernetes pod debug (root)",
        provider="anthropic",
        model="claude-sonnet-4-6",
        last_active=_now - timedelta(days=2, hours=23),
        messages=58,
        tokens_in=44_120,
        tokens_out=19_330,
        source="cli",
    ),
]


@dataclass(frozen=True)
class Skill:
    name: str
    description: str
    triggers: list[str]
    tools: list[str]
    enabled: bool = True
    license: str = "MIT"


SKILLS: list[Skill] = [
    Skill(
        name="github-pr-review",
        description="Review an open PR — fetch diff, summarise, suggest changes.",
        triggers=["pull request", "PR", "code review"],
        tools=["github", "files"],
    ),
    Skill(
        name="daily-summary",
        description="End-of-day Telegram summary: open PRs, calendar, reminders.",
        triggers=["wrap up", "end of day", "summary"],
        tools=["memory", "github", "calendar"],
    ),
    Skill(
        name="voice-todo",
        description="Capture a TODO from a Telegram voice memo into MEMORY.md.",
        triggers=["voice", "todo", "reminder"],
        tools=["memory", "files"],
    ),
    Skill(
        name="vps-bootstrap",
        description="Bring up a fresh VPS — firewall, swap, docker, argus install.",
        triggers=["new server", "bootstrap", "VPS"],
        tools=["shell", "files"],
        enabled=False,
    ),
]


USER_MD_SAMPLE = """\
# User profile

- **Name:** Aniket
- **Timezone:** IST (UTC+5:30)
- **Primary editor:** Neovim with LazyVim
- **Languages:** Python, Go, TypeScript (in that order of frequency)
- **Working on:** ARGUS (this product), an Indian-context payments side-project
- **Communication:** terse over chat, prefers diffs over prose
- **Coffee:** filter, milk, no sugar
"""

MEMORY_MD_SAMPLE = """\
# Long-term memory

## Pinned
- ARGUS PRD is the source of truth — re-read §8 before touching the setup wizard.
- Telegram is the **only** gateway in v1.0. Do not even scaffold Discord/Slack.
- Magenta + cyan never sit adjacent at small sizes (PRD §3.3).

## Open threads
- Decide whether to bundle a Whisper model with the install, or download on demand.
- Benchmark Groq vs Anthropic first-token latency on a 3G connection.
- Sketch the v0.3 Ink TUI even though it's deferred — early feedback is cheap.

## Decisions (with date)
- 2026-05-21 — Picked Apache-2.0 over MIT for licence (patent grant matters).
- 2026-05-24 — `argus ⟨◇⟩ ▸ ` prompt locked. Stop bikeshedding.
- 2026-05-28 — Default to `--dry-run` for the wizard until v0.2 ships.
"""

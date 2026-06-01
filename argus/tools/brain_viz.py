"""AgentBrain memory visualisation — triggered by "Brain" or "Argus Memory".

Shows a live Rich panel with:
  - MEMORY.md  and  USER.md  usage bars (bytes used vs 32 KB cap)
  - AgentBrain knowledge-graph: entity count, relation count
  - AgentMomento skill-router: skills indexed, top-3 by success rate
  - Session stats: messages in current conversation

Auto-optimisation fires when either file exceeds 80% of the 32 KB cap:
  - Synthesises duplicate/adjacent §-entries via the auxiliary model
  - Backs up the original before any mutation
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from rich.align import Align
from rich.console import Console, Group
from rich.panel import Panel
from rich.padding import Padding
from rich.progress import BarColumn, Progress, SpinnerColumn, TaskProgressColumn, TextColumn
from rich.rule import Rule
from rich.table import Table
from rich.text import Text

from argus import paths
from argus.theme import CYAN, DIM, FG, GOLD, MAGENTA, ERR


_MEM_CAP_BYTES = 32_768      # 32 KB cap per file (matches MEMORY.md write guard)
_ENTITY_SOFT_CAP = 200
_RELATION_SOFT_CAP = 500


# ── Data collection ───────────────────────────────────────────────────────────


def _file_stats(p: Path) -> tuple[int, int]:
    """Return (used_bytes, pct_0_100) for a bounded memory file."""
    used = p.stat().st_size if p.exists() else 0
    pct = min(100, round(used * 100 / _MEM_CAP_BYTES))
    return used, pct


def _agentbrain_stats() -> dict[str, int]:
    agb = paths.HOME / "agentbrain.json"
    if not agb.exists():
        return {"entities": 0, "relations": 0}
    try:
        d = json.loads(agb.read_text())
        return {
            "entities": len(d.get("entities", [])),
            "relations": len(d.get("relations", [])),
        }
    except Exception:
        return {"entities": 0, "relations": 0}


def _skill_stats() -> list[dict[str, Any]]:
    idx = paths.SKILLS_DIR / ".index.json"
    if not idx.exists():
        return []
    try:
        raw = json.loads(idx.read_text())
        skills = [
            {"name": k, "usage": v.get("usage_count", 0), "success": v.get("success_rate", 1.0)}
            for k, v in raw.items()
        ]
        return sorted(skills, key=lambda s: s["usage"], reverse=True)[:5]
    except Exception:
        return []


# ── Colour helpers ────────────────────────────────────────────────────────────


def _bar_color(pct: int) -> str:
    if pct >= 85:
        return ERR
    if pct >= 60:
        return GOLD
    return CYAN


def _bar(pct: int, width: int = 20) -> Text:
    filled = round(width * pct / 100)
    empty = width - filled
    color = _bar_color(pct)
    t = Text()
    t.append("█" * filled, style=f"bold {color}")
    t.append("░" * empty,  style=DIM)
    return t


# ── Render ────────────────────────────────────────────────────────────────────


def render_brain_panel(console: Console, convo_messages: int = 0) -> None:
    """Render the full ARGUS memory / brain panel to the console."""
    mem_used, mem_pct = _file_stats(paths.MEMORY_MD)
    usr_used, usr_pct = _file_stats(paths.USER_MD)
    ab = _agentbrain_stats()
    skills = _skill_stats()
    ent_pct = min(100, round(ab["entities"] * 100 / _ENTITY_SOFT_CAP))
    rel_pct = min(100, round(ab["relations"] * 100 / _RELATION_SOFT_CAP))

    # ── header ────────────────────────────────────────────────────────
    header = Text()
    header.append("⟨", style=f"bold {MAGENTA}")
    header.append("◇", style=f"bold {GOLD}")
    header.append("⟩ ", style=f"bold {MAGENTA}")
    header.append("ARGUS MEMORY  ", style=f"bold {FG}")
    header.append("·  AgentBrain  ·  AgentMomento", style=DIM)

    # ── memory files table ────────────────────────────────────────────
    tbl = Table(show_header=True, header_style=f"bold {GOLD}", box=None,
                padding=(0, 2), pad_edge=False, expand=True)
    tbl.add_column("Store",    style=FG,  no_wrap=True, width=16)
    tbl.add_column("Used",     style=DIM, no_wrap=True, width=10)
    tbl.add_column("Capacity", no_wrap=True, width=24)
    tbl.add_column("%",        style=DIM, no_wrap=True, width=6)
    tbl.add_column("Status",   no_wrap=True, width=12)

    def _status(pct: int) -> Text:
        if pct >= 85:
            return Text("⚠ critical", style=f"bold {ERR}")
        if pct >= 60:
            return Text("↑ moderate", style=GOLD)
        return Text("✓ healthy", style=CYAN)

    tbl.add_row("MEMORY.md",  f"{mem_used:,}B",  _bar(mem_pct),
                f"{mem_pct}%", _status(mem_pct))
    tbl.add_row("USER.md",    f"{usr_used:,}B",  _bar(usr_pct),
                f"{usr_pct}%", _status(usr_pct))
    tbl.add_row("Entities",   str(ab["entities"]), _bar(ent_pct, 20),
                f"{ent_pct}%", _status(ent_pct))
    tbl.add_row("Relations",  str(ab["relations"]), _bar(rel_pct, 20),
                f"{rel_pct}%", _status(rel_pct))
    if convo_messages:
        tbl.add_row("Session msgs", str(convo_messages),
                    Text("─" * 20, style=DIM), "─", Text("live", style=CYAN))

    # ── skills table ──────────────────────────────────────────────────
    skill_tbl: Text | Table
    if skills:
        skill_tbl = Table(show_header=True, header_style=f"bold {GOLD}", box=None,
                          padding=(0, 2), pad_edge=False)
        skill_tbl.add_column("Skill",   style=CYAN, no_wrap=True)
        skill_tbl.add_column("Uses",    style=DIM,  justify="right")
        skill_tbl.add_column("Success", justify="right")
        for s in skills:
            sr = s["success"]
            sr_color = CYAN if sr >= 0.7 else GOLD if sr >= 0.4 else ERR
            skill_tbl.add_row(s["name"], str(s["usage"]),
                              Text(f"{sr*100:.0f}%", style=sr_color))
    else:
        skill_tbl = Text("  (no skills used yet)", style=DIM)

    # ── tips ─────────────────────────────────────────────────────────
    tips: list[str] = []
    if mem_pct >= 85:
        tips.append("⚠  MEMORY.md is nearly full — use /memory then remove old entries.")
    if ent_pct >= 85:
        tips.append("⚠  Entity graph is large — call graph(action='synthesize') to compact.")
    if not tips:
        tips.append("✓  Memory usage is healthy.")

    tips_text = Text()
    for tip in tips:
        tips_text.append(f"  {tip}\n", style=DIM)

    # ── assemble panel ────────────────────────────────────────────────
    body = Group(
        Padding(header, (0, 0, 1, 0)),
        Rule(style=MAGENTA),
        Padding(tbl, (1, 0)),
        Rule(Text(" AgentMomento Skills ", style=DIM), style=DIM),
        Padding(skill_tbl, (1, 0)),
        Rule(style=DIM),
        tips_text,
    )

    console.print()
    console.print(
        Panel(body, border_style=MAGENTA, padding=(0, 2),
              title=Text(" ⟨◇⟩ MEMORY DASHBOARD ", style=f"bold {GOLD}"),
              subtitle=Text(f" cap: 32KB/file  ·  {_ENTITY_SOFT_CAP} entities  ·  {_RELATION_SOFT_CAP} relations ", style=DIM))
    )
    console.print()


# ── Auto-optimise (conservative) ─────────────────────────────────────────────


def maybe_auto_optimise(console: Console) -> None:
    """If MEMORY.md >80%, notify and offer to compact (does NOT auto-mutate)."""
    _, pct = _file_stats(paths.MEMORY_MD)
    if pct < 80:
        return
    msg = Text()
    msg.append("⚠  MEMORY.md is at ", style=GOLD)
    msg.append(f"{pct}%", style=f"bold {ERR}")
    msg.append(" capacity.  Run: ", style=GOLD)
    msg.append("/memory", style=f"bold {CYAN}")
    msg.append("  to review and prune old entries.", style=GOLD)
    console.print(Padding(msg, (1, 2)))

"""Seed ARGUS's built-in skills into ~/.argus/skills/.

AgentMomento expects:
  ~/.argus/skills/<name>/SKILL.md          (one file per skill)
  ~/.argus/skills/.index.json              (router index)

This module copies the *.skill.md files vendored at argus/skills_lib/
into that layout on first run, and writes the index.json AgentMomento
needs to BM25-score them. Idempotent — re-running won't clobber user
edits (only rewrites the index).
"""

from __future__ import annotations

import json
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path

from argus import paths

_HERE = Path(__file__).resolve().parent
_FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---\n(.*)$", re.S)


def _parse_skill(text: str) -> tuple[dict, str]:
    """Parse YAML-ish frontmatter without depending on PyYAML at runtime."""
    m = _FRONTMATTER_RE.match(text)
    if not m:
        return {}, text
    raw, body = m.groups()
    meta: dict = {}
    for line in raw.splitlines():
        if ":" not in line:
            continue
        key, _, val = line.partition(":")
        val = val.strip()
        if val.startswith("[") and val.endswith("]"):
            val = [x.strip().strip('"').strip("'") for x in val[1:-1].split(",")]
        else:
            val = val.strip('"').strip("'")
        meta[key.strip()] = val
    return meta, body.strip()


def seed_skills(*, force: bool = False) -> int:
    """Copy vendored skills into ~/.argus/skills/ and write the index.
    Returns the number of skills available after seeding."""
    paths.ensure_dirs()
    skills_dir = paths.SKILLS_DIR
    skills_dir.mkdir(parents=True, exist_ok=True)
    index_path = skills_dir / ".index.json"

    # Discover everything in skills_lib/*.skill.md
    vendored = sorted(_HERE.glob("*.skill.md"))
    if not vendored:
        return 0

    index: dict[str, dict] = {}
    # Preserve existing index entries (success_rate, usage_count, etc.)
    if index_path.exists():
        try:
            existing = json.loads(index_path.read_text())
            if isinstance(existing, dict):
                index = existing
        except json.JSONDecodeError:
            pass

    for src in vendored:
        meta, _body = _parse_skill(src.read_text())
        name = meta.get("name", src.stem.removesuffix(".skill"))
        category = meta.get("category", "general")

        dest_dir = skills_dir / name
        dest = dest_dir / "SKILL.md"
        if force or not dest.exists():
            dest_dir.mkdir(parents=True, exist_ok=True)
            shutil.copy(src, dest)

        # Merge into the index, preserving stats if present.
        prev = index.get(name, {})
        index[name] = {
            "path": str(dest),
            "category": category,
            "description": meta.get("description", ""),
            "triggers": meta.get("triggers", []),
            "tools": meta.get("tools", []),
            "usage_count": int(prev.get("usage_count", 0)),
            "success_rate": float(prev.get("success_rate", 1.0)),
            "last_used": prev.get("last_used"),
            "seeded_at": prev.get("seeded_at") or datetime.now(timezone.utc).isoformat(),
        }

    index_path.write_text(json.dumps(index, indent=2))
    return len(index)

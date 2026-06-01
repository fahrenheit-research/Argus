"""AgentMomento (vendored) — two-stage skill router.

Source: https://github.com/fahrenheit-research/AgentMomento
License: MIT (bundled).

Used inside ARGUS to pick the top-K most relevant skills for each user
turn before building the system prompt — instead of dumping every
installed skill into context and praying the model picks the right one.

Stage 1: keyword-rule classifier into one of 9 task categories.
Stage 2: BM25 over the skills in that category, with a small
success-rate boost from prior outcomes (logged to
~/.argus/logs/agentmomento.log).
"""

from argus.agentmomento.integration import (
    format_skills_for_prompt,
    get_relevant_skills,
    get_router,
    record_outcome,
)
from argus.agentmomento.skill_router import SkillRouter

__all__ = [
    "SkillRouter",
    "get_router",
    "get_relevant_skills",
    "format_skills_for_prompt",
    "record_outcome",
]

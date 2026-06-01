"""
AgentMomento Integration for Hermes Agent

This module provides the production integration point between
AgentMomento v1 and the main Hermes agent execution loop.

Usage:
    1. Import this module in your agent runner
    2. Call `get_relevant_skills(user_message)` before building the prompt
    3. Call `record_outcome(skill_name, success)` after task completion

Created by Fahrenheit Research
"""

import os
import sys
from typing import List, Dict, Optional

# Ensure we can import the router
# (patched: vendored path)
from .skill_router import SkillRouter

_router: Optional[SkillRouter] = None


def get_router() -> SkillRouter:
    """Singleton access to the SkillRouter."""
    global _router
    if _router is None:
        _router = SkillRouter()
    return _router


def get_relevant_skills(task: str, top_k: int = 3) -> List[Dict]:
    """
    Main integration function.
    Returns the top-k most relevant skills for the given task.
    """
    router = get_router()
    return router.route(task, top_k=top_k)


def record_outcome(skill_name: str, success: bool):
    """
    Call this after every agent task to update skill statistics.
    This is critical for the success_rate boosting to work.
    """
    router = get_router()
    router.update_skill_stats(skill_name, success=success)


def format_skills_for_prompt(skills: List[Dict]) -> str:
    """
    Formats selected skills into a clean section for the system prompt.
    """
    if not skills:
        return ""

    lines = ["\n## Relevant Skills (AgentMomento Selected)\n"]
    for skill in skills:
        name = skill["name"]
        category = skill["meta"].get("category", "general")
        score = skill.get("bm25_score", 0)
        success = skill.get("success_rate", 1.0)

        lines.append(
            f"- **{name}** (category: {category}, relevance: {score:.2f}, success_rate: {success:.2f})"
        )

    return "\n".join(lines)


# ===================== Example Integration =====================
"""
Example usage in your main agent loop (e.g. run_agent.py):

from agent.integration import get_relevant_skills, record_outcome, format_skills_for_prompt

# Before building the system prompt
relevant_skills = get_relevant_skills(user_message, top_k=3)
skill_section = format_skills_for_prompt(relevant_skills)

system_prompt = base_system_prompt + skill_section

# ... run the agent ...

# After task completion (you need to determine success)
record_outcome(relevant_skills[0]["name"], success=True)
"""
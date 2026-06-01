"""
AgentMomento v1 - Production-Grade Two-Stage Skill Router for Hermes Agent

A category-first, BM25-powered skill retrieval system that significantly
reduces retrieval noise and improves agent task success rates.

Created by Fahrenheit Research
"""

import os
import json
import re
import logging
from datetime import datetime, timezone
from typing import List, Dict
from rank_bm25 import BM25Okapi

# ===================== Configuration =====================
CATEGORIES = [
    "coding", "devops", "research", "writing", "data",
    "system", "web", "api", "general"
]

SKILLS_DIR = os.path.expanduser("~/.argus/skills")
INDEX_PATH = os.path.join(SKILLS_DIR, ".index.json")
ROUTER_LOG = os.path.expanduser("~/.argus/logs/agentmomento.log")

os.makedirs(os.path.dirname(ROUTER_LOG), exist_ok=True)

logging.basicConfig(
    filename=ROUTER_LOG,
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)
logger = logging.getLogger("AgentMomento")


class SkillRouter:
    """
    AgentMomento v1 - Two-Stage Skill Router

    Stage 1: Task Classification (high-precision rules)
    Stage 2: Filtered BM25 Retrieval within category
    """

    def __init__(self):
        self.index = self._load_index()

    # ===================== Index Management =====================

    def _load_index(self) -> Dict:
        if os.path.exists(INDEX_PATH):
            try:
                with open(INDEX_PATH, "r") as f:
                    return json.load(f)
            except Exception:
                return {}
        return {}

    def _save_index(self):
        try:
            with open(INDEX_PATH, "w") as f:
                json.dump(self.index, f, indent=2, sort_keys=True)
        except Exception as e:
            logger.error(f"Index save failed: {e}")

    def update_skill_stats(self, skill_name: str, success: bool = True):
        """Update usage_count and success_rate after task completion."""
        if skill_name not in self.index:
            return

        meta = self.index[skill_name]
        meta["usage_count"] = meta.get("usage_count", 0) + 1

        current = meta.get("success_rate", 1.0)
        total = meta["usage_count"]
        alpha = 0.25
        new_rate = success * 1.0 if total == 1 else (alpha * success + (1 - alpha) * current)

        meta["success_rate"] = round(new_rate, 4)
        meta["last_used"] = datetime.now(timezone.utc).isoformat()

        self._save_index()
        logger.info(f"Stats updated → {skill_name} | rate={meta['success_rate']:.3f}")

    # ===================== STAGE 1: Improved Classification =====================

    def classify_task(self, task: str) -> str:
        """High-precision classification with improved rules."""
        t = task.lower()

        # Priority order matters — more specific categories first
        if any(kw in t for kw in [
            "arxiv", "research paper", "literature review", "survey paper",
            "benchmark study", "academic paper"
        ]):
            return "research"

        if any(kw in t for kw in [
            "rest api", "graphql", "fastapi", "openapi", "endpoint",
            "api documentation", "restful"
        ]):
            return "api"

        if any(kw in t for kw in [
            "hermes", "gateway", "agent config", "skill system",
            "memory provider", "agent loop"
        ]):
            return "system"

        if any(kw in t for kw in [
            "python", "async", "function", "class", "bug", "refactor",
            "debug", "script", "unit test", "write a function"
        ]):
            return "coding"

        if any(kw in t for kw in [
            "deploy", "docker", "kubernetes", "cron", "infrastructure",
            "ci/cd", "pipeline", "server setup"
        ]):
            return "devops"

        if any(kw in t for kw in [
            "paper", "arxiv", "benchmark", "study", "literature", "survey"
        ]):
            return "research"

        if any(kw in t for kw in [
            "email", "report", "blog", "document", "summary", "draft", "write"
        ]):
            return "writing"

        if any(kw in t for kw in [
            "pandas", "csv", "analysis", "visualization", "sql", "dataframe"
        ]):
            return "data"

        if any(kw in t for kw in [
            "scrape", "browser", "selenium", "crawl", "http request", "web"
        ]):
            return "web"

        if any(kw in t for kw in [
            "rest", "graphql", "api", "endpoint", "openapi"
        ]):
            return "api"

        if any(kw in t for kw in [
            "memory", "config", "hermes", "gateway", "agent", "skill"
        ]):
            return "system"

        return "general"

    # ===================== STAGE 2: BM25 Retrieval =====================

    def _tokenize(self, text: str) -> List[str]:
        text = re.sub(r"[^a-zA-Z0-9\s]", " ", text.lower())
        return [t for t in text.split() if len(t) > 2]

    def load_skills_by_category(self, category: str) -> List[Dict]:
        results = []
        for name, meta in self.index.items():
            if meta.get("category") == category:
                path = meta.get("path")
                if path and os.path.exists(path):
                    with open(path, "r", encoding="utf-8") as f:
                        results.append({
                            "name": name,
                            "path": path,
                            "content": f.read(),
                            "meta": meta
                        })
        return results

    def score_skills_bm25(self, query: str, skills: List[Dict]) -> List[Dict]:
        if not skills:
            return []

        corpus = [self._tokenize(s["content"]) for s in skills]
        bm25 = BM25Okapi(corpus, k1=1.5, b=0.75)
        scores = bm25.get_scores(self._tokenize(query))

        scored = []
        for i, skill in enumerate(skills):
            meta = skill["meta"]
            score = float(scores[i])
            boost = 1.0 + (meta.get("success_rate", 1.0) - 0.5) * 0.1
            final_score = round(score * boost, 4)

            scored.append({
                **skill,
                "bm25_score": final_score,
                "usage_count": meta.get("usage_count", 0),
                "success_rate": meta.get("success_rate", 1.0)
            })

        scored.sort(key=lambda x: (x["bm25_score"], x["success_rate"]), reverse=True)
        return scored

    # ===================== Main Routing =====================

    def route(self, task: str, top_k: int = 3) -> List[Dict]:
        category = self.classify_task(task)
        logger.info(f"Route | category={category} | task='{task[:60]}...'")

        skills = self.load_skills_by_category(category)

        if len(skills) < 2:
            logger.warning("Insufficient category skills — falling back")
            skills = []
            for name, meta in self.index.items():
                path = meta.get("path")
                if path and os.path.exists(path):
                    with open(path, "r", encoding="utf-8") as f:
                        skills.append({
                            "name": name,
                            "path": path,
                            "content": f.read(),
                            "meta": meta
                        })

        results = self.score_skills_bm25(task, skills)[:top_k]

        for r in results:
            logger.info(f"Selected: {r['name']} (BM25={r['bm25_score']:.3f})")

        return results


# ===================== CLI =====================

def get_router():
    return SkillRouter()


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("Usage: python skill_router.py \"task description\"")
        sys.exit(1)

    task = " ".join(sys.argv[1:])
    router = get_router()
    results = router.route(task)

    print("\n=== AgentMomento v1 Routing Results ===\n")
    for i, r in enumerate(results, 1):
        print(f"{i}. {r['name']}")
        print(f"   BM25: {r['bm25_score']:.3f} | Success: {r['success_rate']:.2f} | Used: {r['usage_count']}")
        print(f"   Category: {r['meta'].get('category', 'general')}\n")
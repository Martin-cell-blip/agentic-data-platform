"""Task decomposition.

Two planners behind one interface:
  * LLM planner  - flexible, handles free-form natural-language tasks.
  * Deterministic - rule-based; builds a plan from structured ``hints`` or simple
                    keyword parsing. Guarantees the platform works with no API key.

``Planner.plan`` prefers the LLM and falls back to deterministic on any failure,
so a flaky/absent model never blocks a run.
"""
from __future__ import annotations

import logging
import re
from pathlib import Path

from .llm import LLM
from .monitoring import get_logger, log

_log = get_logger("adp.planner")
_DEFAULT_KEYS = ["county", "quarter"]
_FILE_RE = re.compile(r"[\w./\\:-]+\.(?:csv|tsv|parquet|pq|json|ndjson)", re.IGNORECASE)


class Planner:
    def __init__(self, llm: LLM):
        self.llm = llm

    def plan(self, task: str, tool_specs: list[dict], catalog: list[dict], hints: dict | None = None) -> tuple[list[dict], str]:
        if self.llm.available and not hints:
            try:
                steps = self.llm.plan(task, tool_specs, catalog)
                if steps:
                    return steps, "llm"
            except Exception as exc:  # noqa: BLE001
                log(_log, logging.WARNING, "llm_plan_failed_fallback", error=str(exc))
        return self._deterministic(task, hints or {}), "deterministic"

    def _deterministic(self, task: str, hints: dict) -> list[dict]:
        steps: list[dict] = []
        ingested: list[str] = []

        # 1. ingest
        for item in hints.get("ingest", []):
            name = item.get("name") or Path(item["path"]).stem
            steps.append({"tool": "ingest_file", "args": {"path": item["path"], "name": name}, "why": "load source"})
            ingested.append(name)
        if not hints.get("ingest"):
            for path in _FILE_RE.findall(task or ""):
                name = Path(path).stem
                steps.append({"tool": "ingest_file", "args": {"path": path, "name": name}, "why": "load source (parsed)"})
                ingested.append(name)

        # 2. panel
        panel = hints.get("panel")
        if panel:
            sources = panel.get("sources") or ingested
            steps.append({
                "tool": "build_panel",
                "args": {"name": panel["name"], "sources": sources, "keys": panel.get("keys", _DEFAULT_KEYS)},
                "why": "integrate sources into a keyed panel",
            })
        elif re.search(r"\b(panel|join|integrate)\b", task or "", re.IGNORECASE) and ingested:
            steps.append({
                "tool": "build_panel",
                "args": {"name": "panel", "sources": ingested, "keys": _DEFAULT_KEYS},
                "why": "integrate sources (parsed)",
            })

        # 3. ad-hoc SQL transform
        sql = hints.get("sql")
        if sql:
            steps.append({
                "tool": "create_dataset",
                "args": {k: sql[k] for k in ("name", "sql") if k in sql} | ({"layer": sql["layer"]} if "layer" in sql else {}),
                "why": "sql transform",
            })

        # 4. validate
        validate = hints.get("validate")
        if validate:
            steps.append({"tool": "validate_dataset", "args": dict(validate), "why": "data-quality gate"})

        # 5. profile
        profile = hints.get("profile")
        if profile:
            steps.append({"tool": "profile_dataset", "args": {"name": profile}, "why": "profile"})

        if not steps:
            steps.append({"tool": "list_catalog", "args": {}, "why": "no actionable intent; report catalog"})
        return steps

"""Anthropic Claude wrapper used for planning and SQL self-repair.

The platform treats the LLM as an *optional accelerator*: when a key is present
it produces flexible plans and repairs broken SQL; when absent, callers fall
back to the deterministic planner (see planner.py). This graceful degradation
keeps CI, tests and the demo fully runnable offline.
"""
from __future__ import annotations

import json
import logging
import re

from .config import Settings
from .monitoring import METRICS, get_logger, log

_log = get_logger("adp.llm")


class LLMUnavailable(RuntimeError):
    """Raised when an LLM operation is requested but no client is configured."""


def _extract_json_array(text: str) -> list:
    text = text.strip()
    match = re.search(r"\[.*\]", text, re.DOTALL)
    if not match:
        raise ValueError(f"no JSON array in LLM output: {text[:160]}")
    return json.loads(match.group(0))


def _strip_code_fence(text: str) -> str:
    text = text.strip()
    text = re.sub(r"^```[a-zA-Z]*\n?", "", text)
    text = re.sub(r"\n?```$", "", text)
    return text.strip()


class LLM:
    def __init__(self, settings: Settings):
        self.settings = settings
        self._client = None
        if settings.llm_enabled:
            try:
                import anthropic

                self._client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
            except Exception as exc:  # noqa: BLE001
                log(_log, logging.WARNING, "anthropic_init_failed", error=str(exc))
                self._client = None

    @property
    def available(self) -> bool:
        return self._client is not None

    def _message(self, system: str, user: str, max_tokens: int | None = None) -> str:
        if not self.available:
            raise LLMUnavailable("no Anthropic client configured")
        with METRICS.timer("llm.call"):
            resp = self._client.messages.create(
                model=self.settings.model,
                max_tokens=max_tokens or self.settings.llm_max_tokens,
                system=system,
                messages=[{"role": "user", "content": user}],
            )
        METRICS.incr("llm.calls")
        try:
            METRICS.incr("llm.input_tokens", resp.usage.input_tokens)
            METRICS.incr("llm.output_tokens", resp.usage.output_tokens)
        except Exception:  # usage shape changes shouldn't break the call
            pass
        return "".join(b.text for b in resp.content if getattr(b, "type", None) == "text")

    def plan(self, task: str, tool_specs: list[dict], catalog: list[dict]) -> list[dict]:
        system = (
            "You are a data-platform planning agent. Decompose the user's task into an ordered "
            "JSON plan of steps. Each step is an object: "
            '{"tool": "<tool name>", "args": {<arguments>}, "why": "<one line>"}. '
            "Use ONLY the provided tools and their argument schemas. Reference only datasets that "
            "exist in the catalog or are produced by earlier steps. Return ONLY a JSON array."
        )
        user = json.dumps({"task": task, "tools": tool_specs, "catalog": catalog}, ensure_ascii=False)
        return _extract_json_array(self._message(system, user))

    def repair_sql(self, sql: str, error: str, schema_context: list[dict]) -> str:
        system = "You fix broken DuckDB SQL. Return ONLY the corrected SQL statement, no prose, no code fence."
        user = f"-- broken SQL --\n{sql}\n\n-- error --\n{error}\n\n-- available relations/columns --\n{json.dumps(schema_context, ensure_ascii=False)}"
        return _strip_code_fence(self._message(system, user))

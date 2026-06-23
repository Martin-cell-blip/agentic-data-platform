"""The agent runtime: a single, centralized plan -> execute -> observe loop.

This is the one place that enforces retries, self-correction, circuit-breaking,
and run/lineage bookkeeping — the recommended pattern from production agent
frameworks (one runtime owns the loop). The agent:

  1. decomposes the task into a plan (Planner),
  2. executes each step via the ToolRegistry,
  3. on a transient failure, retries with backoff and — for SQL steps — asks the
     LLM to repair the statement before the next attempt (Reflexion-style),
  4. trips a per-tool circuit breaker after repeated failures,
  5. records the run, its steps, and lineage to persistent memory.
"""
from __future__ import annotations

import json
import logging

from .config import Settings
from .llm import LLM
from .memory import Memory
from .monitoring import METRICS, get_logger, log
from .planner import Planner
from .retry import CircuitBreaker, retry_call
from .tools import Context, ToolRegistry

_log = get_logger("adp.agent")
_SQL_TOOLS = {"run_sql", "create_dataset"}


class Agent:
    def __init__(self, ctx: Context, registry: ToolRegistry, planner: Planner, settings: Settings, llm: LLM):
        self.ctx = ctx
        self.mem: Memory = ctx.mem
        self.registry = registry
        self.planner = planner
        self.settings = settings
        self.llm = llm
        self._breakers: dict[str, CircuitBreaker] = {}

    # --- public API ---
    def run(self, task: str, hints: dict | None = None) -> dict:
        plan, planner_mode = self.planner.plan(task, self.registry.specs(), self.mem.catalog(), hints)
        run_id = self.mem.start_run(task, planner_mode, plan)
        log(_log, logging.INFO, "run_start", run_id=run_id, planner=planner_mode, steps=len(plan), task=task)

        steps_log: list[dict] = []
        status = "success"
        error: str | None = None
        for idx, step in enumerate(plan):
            result = self._exec_step(idx, step)
            steps_log.append(result)
            if not result["ok"]:
                status = "failed"
                error = result.get("error")
                break

        self.mem.finish_run(run_id, status, steps_log, error)
        METRICS.incr(f"agent.run.{status}")
        log(_log, logging.INFO, "run_end", run_id=run_id, status=status, executed=len(steps_log))
        return {
            "run_id": run_id,
            "status": status,
            "planner": planner_mode,
            "plan": plan,
            "steps": steps_log,
            "error": error,
        }

    # --- internals ---
    def _schema_context(self) -> list[dict]:
        out = []
        for d in self.mem.catalog():
            ds = self.mem.get_dataset(d["name"])
            cols = []
            if ds and ds.get("schema_json"):
                try:
                    cols = [c["column_name"] for c in json.loads(ds["schema_json"])]
                except Exception:
                    cols = []
            out.append({"name": d["name"], "layer": d["layer"], "columns": cols})
        return out

    def _exec_step(self, idx: int, step: dict) -> dict:
        name = step.get("tool", "")
        args = dict(step.get("args", {}))  # copy so self-correction can mutate it
        why = step.get("why", "")

        try:
            tool = self.registry.get(name)
        except KeyError as exc:
            METRICS.incr("agent.unknown_tool")
            return {"i": idx, "tool": name, "args": args, "ok": False, "why": why, "error": str(exc)}

        breaker = self._breakers.setdefault(
            name, CircuitBreaker(self.settings.circuit_fail_threshold, self.settings.circuit_reset_s)
        )
        if not breaker.allow():
            METRICS.incr(f"tool.{name}.circuit_open")
            return {"i": idx, "tool": name, "args": args, "ok": False, "why": why, "error": "circuit_open"}

        def call():
            return tool.fn(**args)

        def on_retry(attempt: int, exc: BaseException) -> None:
            # Self-correction: ask the LLM to repair broken SQL before the next try.
            if name in _SQL_TOOLS and self.llm.available and args.get("sql"):
                fixed = self.llm.repair_sql(args["sql"], str(exc), self._schema_context())
                if fixed and fixed.strip():
                    args["sql"] = fixed
                    METRICS.incr("agent.sql_repair")
                    log(_log, logging.INFO, "sql_repaired", tool=name, attempt=attempt)

        try:
            with METRICS.timer(f"tool.{name}"):
                result = retry_call(
                    call,
                    retries=self.settings.max_retries,
                    base_delay=self.settings.retry_base_delay,
                    on_retry=on_retry,
                    label=name,
                )
            breaker.record_success()
            METRICS.incr(f"tool.{name}.ok")
            return {"i": idx, "tool": name, "args": args, "ok": True, "why": why, "result": result}
        except Exception as exc:  # noqa: BLE001
            breaker.record_failure()
            METRICS.incr(f"tool.{name}.fail")
            log(_log, logging.ERROR, "step_failed", tool=name, error=str(exc))
            return {"i": idx, "tool": name, "args": args, "ok": False, "why": why, "error": str(exc)}

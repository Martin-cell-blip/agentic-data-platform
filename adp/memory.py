"""Persistent platform memory: dataset catalog, run history, and data lineage.

All three live in the warehouse's ``meta`` schema so they survive restarts and
can be queried with plain SQL — the agent's long-term memory is itself a
governed dataset.
"""
from __future__ import annotations

import json
import time
import uuid

from .warehouse import Warehouse


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


class Memory:
    def __init__(self, wh: Warehouse):
        self.wh = wh
        self._init_tables()

    def _init_tables(self) -> None:
        self.wh.execute(
            """CREATE TABLE IF NOT EXISTS meta.datasets (
                name VARCHAR PRIMARY KEY, layer VARCHAR, source VARCHAR,
                n_rows BIGINT, n_cols INTEGER, schema_json VARCHAR,
                description VARCHAR, created_at VARCHAR)"""
        )
        self.wh.execute(
            """CREATE TABLE IF NOT EXISTS meta.runs (
                run_id VARCHAR PRIMARY KEY, task VARCHAR, status VARCHAR,
                planner VARCHAR, plan_json VARCHAR, steps_json VARCHAR,
                error VARCHAR, started_at VARCHAR, finished_at VARCHAR)"""
        )
        self.wh.execute(
            """CREATE TABLE IF NOT EXISTS meta.lineage (
                output VARCHAR, input VARCHAR, transform VARCHAR, created_at VARCHAR)"""
        )

    # --- dataset catalog ---
    def register_dataset(
        self, name: str, layer: str, source: str, n_rows: int, schema: list[dict], description: str = ""
    ) -> None:
        self.wh.execute("DELETE FROM meta.datasets WHERE name = ?", [name])
        self.wh.execute(
            "INSERT INTO meta.datasets VALUES (?,?,?,?,?,?,?,?)",
            [name, layer, source, n_rows, len(schema), json.dumps(schema), description, _now()],
        )

    def catalog(self) -> list[dict]:
        return self.wh.query(
            "SELECT name, layer, source, n_rows, n_cols, description, created_at "
            "FROM meta.datasets ORDER BY created_at, name"
        )

    def get_dataset(self, name: str) -> dict | None:
        rows = self.wh.query("SELECT * FROM meta.datasets WHERE name = ?", [name])
        return rows[0] if rows else None

    # --- run history ---
    def start_run(self, task: str, planner: str, plan: list[dict]) -> str:
        run_id = uuid.uuid4().hex[:12]
        self.wh.execute(
            "INSERT INTO meta.runs (run_id, task, status, planner, plan_json, steps_json, started_at) "
            "VALUES (?,?,?,?,?,?,?)",
            [run_id, task, "running", planner, json.dumps(plan), "[]", _now()],
        )
        return run_id

    def finish_run(self, run_id: str, status: str, steps: list[dict], error: str | None = None) -> None:
        self.wh.execute(
            "UPDATE meta.runs SET status = ?, steps_json = ?, error = ?, finished_at = ? WHERE run_id = ?",
            [status, json.dumps(steps, default=str), error, _now(), run_id],
        )

    def runs(self, limit: int = 50) -> list[dict]:
        return self.wh.query(
            f"SELECT run_id, task, status, planner, started_at, finished_at "
            f"FROM meta.runs ORDER BY started_at DESC LIMIT {int(limit)}"
        )

    def get_run(self, run_id: str) -> dict | None:
        rows = self.wh.query("SELECT * FROM meta.runs WHERE run_id = ?", [run_id])
        return rows[0] if rows else None

    # --- lineage ---
    def add_lineage(self, output: str, inputs: list[str], transform: str) -> None:
        for inp in inputs:
            self.wh.execute("INSERT INTO meta.lineage VALUES (?,?,?,?)", [output, inp, transform, _now()])

    def lineage_of(self, name: str) -> list[dict]:
        """Return all upstream edges feeding ``name`` (transitively)."""
        edges = self.wh.query("SELECT output, input, transform FROM meta.lineage")
        upstream: dict[str, list[dict]] = {}
        for e in edges:
            upstream.setdefault(e["output"], []).append(e)
        seen: set[str] = set()
        stack = [name]
        result: list[dict] = []
        while stack:
            node = stack.pop()
            for edge in upstream.get(node, []):
                key = (edge["output"], edge["input"])
                if key in seen:
                    continue
                seen.add(key)
                result.append(edge)
                stack.append(edge["input"])
        return result

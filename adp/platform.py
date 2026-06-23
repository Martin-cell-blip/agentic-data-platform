"""Wiring: assemble warehouse, memory, tools, planner, LLM and agent.

A single ``Platform`` object is the composition root used by the CLI, the demo,
the eval harness and the API.
"""
from __future__ import annotations

from .agent import Agent
from .config import Settings, get_settings
from .llm import LLM
from .memory import Memory
from .monitoring import setup_logging
from .planner import Planner
from .tools import Context, build_registry
from .warehouse import Warehouse


class Platform:
    def __init__(self, settings: Settings | None = None):
        self.settings = settings or get_settings()
        setup_logging(self.settings.log_level)
        self.wh = Warehouse(self.settings.db_path)
        self.mem = Memory(self.wh)
        self.ctx = Context(self.wh, self.mem, self.settings)
        self.registry = build_registry(self.ctx)
        self.llm = LLM(self.settings)
        self.planner = Planner(self.llm)
        self.agent = Agent(self.ctx, self.registry, self.planner, self.settings, self.llm)

    # --- convenience accessors used by the API / CLI ---
    def serve_dataset(self, name: str, limit: int = 100, offset: int = 0) -> dict:
        ds = self.mem.get_dataset(name)
        if ds is None:
            raise KeyError(name)
        layer = ds["layer"]
        rows = self.wh.query(
            f'SELECT * FROM {layer}."{name}" LIMIT {int(limit)} OFFSET {int(offset)}'
        )
        return {"dataset": name, "layer": layer, "limit": limit, "offset": offset, "rows": rows}

    def dataset_schema(self, name: str) -> dict:
        ds = self.mem.get_dataset(name)
        if ds is None:
            raise KeyError(name)
        return {"dataset": name, "layer": ds["layer"], "schema": self.wh.table_schema(ds["layer"], name)}

    def close(self) -> None:
        self.wh.close()

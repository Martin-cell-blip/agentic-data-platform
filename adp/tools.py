"""Tool layer: the typed capabilities the agent can call.

Each Tool carries a JSON-Schema (so the LLM can call it) and a Python impl that
does the real work against the warehouse. Every tool that writes a relation also
records it in the catalog and the lineage graph, so provenance is automatic.

Guardrails (treat the agent as untrusted):
  * run_sql is SELECT/WITH-only (read path).
  * ingest paths are resolved and existence-checked.
  * write tools use CREATE OR REPLACE for idempotency (safe to retry).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from .config import Settings
from .memory import Memory
from .monitoring import METRICS, get_logger, log
from .warehouse import Warehouse, is_numeric_type

_log = get_logger("adp.tools")


@dataclass
class Tool:
    name: str
    description: str
    parameters: dict  # JSON schema for the LLM tool-use interface
    fn: Callable[..., dict]


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool:
        if name not in self._tools:
            raise KeyError(f"unknown tool: {name!r} (known: {', '.join(self._tools)})")
        return self._tools[name]

    def specs(self) -> list[dict]:
        return [
            {"name": t.name, "description": t.description, "input_schema": t.parameters}
            for t in self._tools.values()
        ]

    def names(self) -> list[str]:
        return list(self._tools)


@dataclass
class Context:
    wh: Warehouse
    mem: Memory
    settings: Settings


def _q(ident: str) -> str:
    """Quote a SQL identifier, escaping embedded quotes."""
    return '"' + ident.replace('"', '""') + '"'


# --------------------------------------------------------------------------- #
# Tool implementations
# --------------------------------------------------------------------------- #
def _ingest_file(ctx: Context, path: str, name: str, layer: str = "raw", fmt: str | None = None) -> dict:
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"ingest source not found: {path}")
    ext = (fmt or p.suffix.lstrip(".")).lower()
    safe = str(p.resolve()).replace("'", "''")
    if ext in ("csv", "tsv", "txt"):
        reader = f"read_csv_auto('{safe}')"
    elif ext in ("parquet", "pq"):
        reader = f"read_parquet('{safe}')"
    elif ext in ("json", "ndjson"):
        reader = f"read_json_auto('{safe}')"
    else:
        raise ValueError(f"unsupported ingest format: {ext!r}")
    ctx.wh.execute(f"CREATE OR REPLACE TABLE {layer}.{_q(name)} AS SELECT * FROM {reader}")
    schema = ctx.wh.table_schema(layer, name)
    n = ctx.wh.row_count(layer, name)
    ctx.mem.register_dataset(name, layer, f"file:{p.name}", n, schema, f"ingested from {p.name}")
    ctx.mem.add_lineage(name, [f"source:{p.name}"], "ingest")
    log(_log, logging.INFO, "ingested", dataset=name, rows=n, cols=len(schema))
    return {"dataset": name, "layer": layer, "rows": n, "columns": [c["column_name"] for c in schema]}


def _run_sql(ctx: Context, sql: str, limit: int | None = None) -> dict:
    head = sql.strip().lower()
    if not (head.startswith("select") or head.startswith("with")):
        raise ValueError("run_sql only permits read-only SELECT/WITH statements")
    rows = ctx.wh.query(sql)
    cap = limit or ctx.settings.max_rows
    return {"row_count": len(rows), "rows": rows[:cap], "truncated": len(rows) > cap}


def _infer_inputs(sql: str, mem: Memory) -> list[str]:
    lowered = sql.lower()
    return [d["name"] for d in mem.catalog() if d["name"].lower() in lowered]


def _create_dataset(
    ctx: Context, name: str, sql: str, layer: str = "marts", inputs: list[str] | None = None
) -> dict:
    ctx.wh.execute(f"CREATE OR REPLACE TABLE {layer}.{_q(name)} AS {sql}")
    schema = ctx.wh.table_schema(layer, name)
    n = ctx.wh.row_count(layer, name)
    ctx.mem.register_dataset(name, layer, "sql", n, schema, "created via SQL transform")
    ctx.mem.add_lineage(name, inputs or _infer_inputs(sql, ctx.mem) or ["sql:adhoc"], "sql")
    return {"dataset": name, "layer": layer, "rows": n, "columns": [c["column_name"] for c in schema]}


def _build_panel(ctx: Context, name: str, sources: list[str], keys: list[str]) -> dict:
    """Deterministically join several raw sources into one keyed panel.

    For each source we aggregate its numeric columns by the shared keys (mean),
    prefixing output columns with the source name to avoid collisions, then inner
    join the per-source aggregates on the keys. This mirrors building a
    region x quarter economic panel from heterogeneous sources.
    """
    if len(sources) < 1:
        raise ValueError("build_panel needs at least one source")
    src_cols = {s: ctx.wh.table_schema("raw", s) for s in sources}
    for s, cols in src_cols.items():
        if not cols:
            raise ValueError(f"source raw.{s} not found or empty")
    usable_keys = [k for k in keys if all(any(c["column_name"] == k for c in src_cols[s]) for s in sources)]
    if not usable_keys:
        raise ValueError(f"no common key columns {keys} across sources {sources}")

    ctes: list[str] = []
    produced: list[tuple[int, list[str]]] = []
    for i, s in enumerate(sources):
        numeric = [
            c["column_name"]
            for c in src_cols[s]
            if is_numeric_type(c["data_type"]) and c["column_name"] not in usable_keys
        ]
        keysel = ", ".join(_q(k) for k in usable_keys)
        if numeric:
            aggs = ", ".join(f"avg({_q(c)}) AS {_q(f'{s}_{c}')}" for c in numeric)
            cols_out = [f"{s}_{c}" for c in numeric]
        else:
            aggs = f"count(*) AS {_q(f'{s}_rowcount')}"
            cols_out = [f"{s}_rowcount"]
        ctes.append(f"stg_{i} AS (SELECT {keysel}, {aggs} FROM raw.{_q(s)} GROUP BY {keysel})")
        produced.append((i, cols_out))

    join = "stg_0 t0"
    for i in range(1, len(sources)):
        on = " AND ".join(f"t0.{_q(k)} = t{i}.{_q(k)}" for k in usable_keys)
        join += f" INNER JOIN stg_{i} t{i} ON {on}"

    select_cols = [f"t0.{_q(k)} AS {_q(k)}" for k in usable_keys]
    for i, cols_out in produced:
        select_cols.extend(f"t{i}.{_q(c)} AS {_q(c)}" for c in cols_out)

    final_sql = f"WITH {', '.join(ctes)} SELECT {', '.join(select_cols)} FROM {join}"
    ctx.wh.execute(f"CREATE OR REPLACE TABLE marts.{_q(name)} AS {final_sql}")
    schema = ctx.wh.table_schema("marts", name)
    n = ctx.wh.row_count("marts", name)
    ctx.mem.register_dataset(name, "marts", "panel", n, schema, f"panel keyed by {usable_keys} from {sources}")
    ctx.mem.add_lineage(name, list(sources), "build_panel")
    log(_log, logging.INFO, "panel_built", dataset=name, rows=n, keys=usable_keys, sources=sources)
    return {"dataset": name, "rows": n, "keys": usable_keys, "columns": [c["column_name"] for c in schema]}


def _validate_dataset(
    ctx: Context,
    name: str,
    min_rows: int = 1,
    unique_key: list[str] | None = None,
    not_null: list[str] | None = None,
    expected_columns: list[str] | None = None,
) -> dict:
    ds = ctx.mem.get_dataset(name)
    if ds is None:
        raise ValueError(f"unknown dataset: {name}")
    layer = ds["layer"]
    rel = f"{layer}.{_q(name)}"
    checks: list[dict] = []

    n = ctx.wh.row_count(layer, name)
    checks.append({"check": "min_rows", "passed": n >= min_rows, "detail": f"{n} rows (>= {min_rows})"})

    cols = [c["column_name"] for c in ctx.wh.table_schema(layer, name)]
    if expected_columns:
        missing = [c for c in expected_columns if c not in cols]
        checks.append({"check": "expected_columns", "passed": not missing, "detail": f"missing={missing}"})

    for col in not_null or []:
        nulls = ctx.wh.query(f"SELECT count(*) AS n FROM {rel} WHERE {_q(col)} IS NULL")[0]["n"]
        checks.append({"check": f"not_null[{col}]", "passed": nulls == 0, "detail": f"{nulls} nulls"})

    if unique_key:
        keysel = ", ".join(_q(k) for k in unique_key)
        dups = ctx.wh.query(
            f"SELECT count(*) AS n FROM (SELECT {keysel} FROM {rel} GROUP BY {keysel} HAVING count(*) > 1)"
        )[0]["n"]
        checks.append({"check": f"unique_key{unique_key}", "passed": dups == 0, "detail": f"{dups} duplicate keys"})

    passed = all(c["passed"] for c in checks)
    METRICS.incr("validate.passed" if passed else "validate.failed")
    return {"dataset": name, "passed": passed, "checks": checks, "rows": n}


def _profile_dataset(ctx: Context, name: str) -> dict:
    ds = ctx.mem.get_dataset(name)
    if ds is None:
        raise ValueError(f"unknown dataset: {name}")
    layer = ds["layer"]
    rel = f"{layer}.{_q(name)}"
    n = ctx.wh.row_count(layer, name)
    profile = []
    for c in ctx.wh.table_schema(layer, name):
        col, dtype = c["column_name"], c["data_type"]
        stats = ctx.wh.query(
            f"SELECT count({_q(col)}) AS non_null, count(DISTINCT {_q(col)}) AS distinct_n FROM {rel}"
        )[0]
        entry = {"column": col, "type": dtype, "nulls": n - stats["non_null"], "distinct": stats["distinct_n"]}
        if is_numeric_type(dtype):
            mm = ctx.wh.query(f"SELECT min({_q(col)}) AS lo, max({_q(col)}) AS hi, avg({_q(col)}) AS mean FROM {rel}")[0]
            entry.update({"min": mm["lo"], "max": mm["hi"], "mean": mm["mean"]})
        profile.append(entry)
    return {"dataset": name, "rows": n, "profile": profile}


def _list_catalog(ctx: Context) -> dict:
    return {"datasets": ctx.mem.catalog()}


# --------------------------------------------------------------------------- #
# Registry assembly
# --------------------------------------------------------------------------- #
def build_registry(ctx: Context) -> ToolRegistry:
    reg = ToolRegistry()

    reg.register(Tool(
        "ingest_file",
        "Ingest a local CSV/Parquet/JSON file into the raw layer and register it in the catalog.",
        {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "local file path"},
                "name": {"type": "string", "description": "dataset name to register"},
                "layer": {"type": "string", "default": "raw"},
                "fmt": {"type": "string", "description": "csv|parquet|json (else inferred from extension)"},
            },
            "required": ["path", "name"],
        },
        lambda **kw: _ingest_file(ctx, **kw),
    ))

    reg.register(Tool(
        "run_sql",
        "Run a read-only SELECT/WITH query and return rows.",
        {
            "type": "object",
            "properties": {"sql": {"type": "string"}, "limit": {"type": "integer"}},
            "required": ["sql"],
        },
        lambda **kw: _run_sql(ctx, **kw),
    ))

    reg.register(Tool(
        "create_dataset",
        "Create/replace a dataset from a SELECT query (a transform). Records lineage.",
        {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "sql": {"type": "string", "description": "a SELECT query"},
                "layer": {"type": "string", "default": "marts"},
                "inputs": {"type": "array", "items": {"type": "string"}, "description": "upstream dataset names"},
            },
            "required": ["name", "sql"],
        },
        lambda **kw: _create_dataset(ctx, **kw),
    ))

    reg.register(Tool(
        "build_panel",
        "Join several raw sources into one panel keyed by shared columns, aggregating numeric columns. Records lineage.",
        {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "sources": {"type": "array", "items": {"type": "string"}},
                "keys": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["name", "sources", "keys"],
        },
        lambda **kw: _build_panel(ctx, **kw),
    ))

    reg.register(Tool(
        "validate_dataset",
        "Run data-quality checks (min rows, expected columns, not-null, unique key) and return a report.",
        {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "min_rows": {"type": "integer", "default": 1},
                "unique_key": {"type": "array", "items": {"type": "string"}},
                "not_null": {"type": "array", "items": {"type": "string"}},
                "expected_columns": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["name"],
        },
        lambda **kw: _validate_dataset(ctx, **kw),
    ))

    reg.register(Tool(
        "profile_dataset",
        "Compute per-column profile statistics (nulls, distinct, min/max/mean) for a dataset.",
        {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]},
        lambda **kw: _profile_dataset(ctx, **kw),
    ))

    reg.register(Tool(
        "list_catalog",
        "List all registered datasets.",
        {"type": "object", "properties": {}},
        lambda **kw: _list_catalog(ctx, **kw),
    ))

    return reg

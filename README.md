# Agentic Data Platform

An LLM **agent** that builds and serves a small **data platform**: it ingests
heterogeneous external sources, integrates them into clean keyed datasets,
validates them, tracks lineage, and serves them over an API — driven by a
plan → execute → observe loop with retry, self-correction, evaluation and
monitoring built in.

It is intentionally a *bridge* project: it exercises **data-platform
engineering** (ingest/transform/validate/serve, medallion layers, lineage,
DuckDB, an HTTP data API) **and** the **production-agent** concerns (task
decomposition, tool calling, memory, failure retry, eval, monitoring,
stability) in one dependency-light codebase you can read in an afternoon.

```
status: ✓ 13/13 unit tests   ✓ 3/3 eval tasks   ✓ runs fully offline (no API key required)
```

---

## Why this design

The platform treats the LLM as an **optional accelerator**, not a hard
dependency. With `ANTHROPIC_API_KEY` set, Claude does flexible planning and
repairs broken SQL between retries. Without a key, a **deterministic planner**
produces the same plans from structured hints — so CI, tests and the demo are
hermetic and reproducible. This "graceful degradation" is a deliberate
reliability choice, and it is the difference between a demo and a system.

## Architecture

```mermaid
flowchart LR
    U[Task: NL or hints] --> PL[Planner]
    PL -->|LLM or deterministic| PLAN[Ordered plan]
    PLAN --> AG[Agent runtime\nplan→execute→observe]
    AG -->|retry + SQL self-repair\n+ circuit breaker| TR[Tool registry]
    TR --> IN[ingest_file]
    TR --> BP[build_panel]
    TR --> SQ[run_sql / create_dataset]
    TR --> VA[validate_dataset]
    IN & BP & SQ & VA --> WH[(DuckDB warehouse\nraw · staging · marts · meta)]
    AG --> MEM[Memory: catalog · runs · lineage]
    WH --> API[FastAPI: serve data / lineage / metrics]
    AG -. metrics/logs .-> MON[Monitoring]
    MON --> API
```

Medallion layers in DuckDB: `raw` (verbatim ingest) → `staging`/`marts`
(transformed, served) → `meta` (the platform's own catalog, run history and
lineage — the agent's long-term memory is itself a governed dataset).

## The seven production concerns — and where each lives

| Concern | Where | What it does |
|---|---|---|
| **Task decomposition** | [`planner.py`](adp/planner.py) | LLM or deterministic planner turns a task into an ordered tool plan |
| **Tool calling** | [`agent.py`](adp/agent.py), [`tools.py`](adp/tools.py) | one centralized loop calls typed tools (ingest, panel, SQL, validate, profile) |
| **Memory** | [`memory.py`](adp/memory.py) | persistent dataset catalog, run history and lineage in `meta.*` tables |
| **Failure retry / self-correction** | [`retry.py`](adp/retry.py), [`agent.py`](adp/agent.py) | exponential backoff; on retry the LLM repairs broken SQL (Reflexion-style) |
| **Evaluation** | [`eval_harness.py`](adp/eval_harness.py), [`eval/tasks.yaml`](eval/tasks.yaml) | config-driven, execution-based assertions; gates CI |
| **Monitoring** | [`monitoring.py`](adp/monitoring.py) | structured JSON logs + counters & latency percentiles via `GET /metrics` |
| **Stability** | `retry.py` (circuit breaker), `tools.py` (SELECT-only SQL, idempotent `CREATE OR REPLACE`, path checks) | treats the agent as untrusted; fails fast and stays up |

## Quickstart

```bash
uv venv --python 3.13
uv pip install -e ".[dev]"

uv run adp demo      # ingest 3 sources → build county-quarter panel → validate
uv run pytest        # 13 tests
uv run adp eval      # evaluation suite (non-zero exit on failure)
uv run adp serve     # FastAPI on http://127.0.0.1:8000  (docs at /docs)
uv run adp ask "Ingest data/samples/*.csv and build a county-quarter panel"
```

Enable LLM planning/self-repair: `cp .env.example .env` and set `ANTHROPIC_API_KEY`.

## What the demo does

Generates three synthetic, deterministic sources keyed by `(county, quarter)` —
flood-insurance policies, property sales, mortgage records — then runs one agent
task that ingests all three, aggregates each source's numeric columns by the
shared keys, inner-joins them into a **200-row `county_quarter_panel`**
(10 counties × 20 quarters), and runs a data-quality gate (row count, unique
key, not-null). Catalog, lineage and metrics are persisted and served.

## API

| Method | Path | Purpose |
|---|---|---|
| GET | `/health` | liveness + capability summary |
| GET | `/metrics` | counters + latency percentiles |
| GET | `/catalog` | registered datasets |
| GET | `/datasets/{name}?limit=&offset=` | **serve dataset rows** |
| GET | `/datasets/{name}/schema` | column schema |
| GET | `/lineage/{name}` | upstream provenance edges |
| GET | `/runs`, `/runs/{id}` | agent run history |
| POST | `/agent/run` | run a task `{task, hints}` |

## Design decisions

- **Dependency-light on purpose.** The agent loop, retry, breaker, metrics and
  planner are ~600 lines of readable Python rather than a framework, so the
  engineering is legible. The patterns are borrowed from the references below.
- **Idempotent writes.** Every write tool uses `CREATE OR REPLACE`, so a retried
  step is safe — a precondition for safe retries.
- **Provenance by construction.** Catalog + lineage are written by the tools
  themselves, not bolted on, so every served dataset is traceable to its sources.
- **Validate-before-trust.** A dataset isn't "done" until its DQ gate passes; the
  eval harness consumes the same validation signal.

## How this maps to the role (Anthropic — Research Engineer, Economic Research Data Platform)

- *"Build the data pipelines that turn raw … data into clean, reusable datasets"* → `ingest_file` + `build_panel` + medallion layers.
- *"Build self-serve workflows to ingest and integrate external data sources so they're interoperable"* → multi-source ingest + keyed panel integration.
- *"Develop the APIs, libraries, and interfaces that serve data to researchers"* → the FastAPI service.
- *"Ensure data reliability, integrity… across all… data infrastructure"* → validation gates, lineage, idempotency, monitoring.
- *Bonus: building systems on top of LLMs; data lineage/governance tooling; econometrics/quant-social-science framing.*

## Reference projects (researched, real)

- [motherduckdb/analytics-agent-duckdb-workshop](https://github.com/motherduckdb/analytics-agent-duckdb-workshop) — DuckDB text-to-SQL agent; closest stack match.
- [anthropics/claude-cookbooks](https://github.com/anthropics/claude-cookbooks) — canonical agent workflow patterns on a tiny LLM-call util.
- [microsoft/autogen](https://github.com/microsoft/autogen) (Magentic-One) — dual-ledger planning / progress reflection.
- [pydantic/pydantic-ai](https://github.com/pydantic/pydantic-ai) — "validation error = retry signal".
- [vanna-ai/vanna](https://github.com/vanna-ai/vanna) · [Canner/WrenAI](https://github.com/Canner/WrenAI) — text-to-SQL + governed semantic layer.
- [great-expectations](https://github.com/great-expectations/great_expectations) / [pandera](https://github.com/unionai-oss/pandera) — declarative validation as an objective eval signal.

## Limitations & roadmap (honest v1 scope)

This is a **single-node, batch** platform built around a local DuckDB file. It is
deliberately *not* yet: distributed/streaming, cloud-deployed at scale, or
backed by an orchestration framework. Natural next steps, in order:

1. Swap the warehouse for a cloud target (BigQuery/Snowflake) behind the same `Warehouse` interface.
2. Add `dbt` for versioned, tested transforms; emit OpenTelemetry spans for tracing.
3. Per-stage checkpointing for crash-resume on long ingests.
4. Privacy-preserving aggregation (k-anonymity / differential privacy) on served datasets.

## License

MIT

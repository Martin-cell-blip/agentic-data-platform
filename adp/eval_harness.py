"""Evaluation harness.

Treats evals as unit tests with objective, execution-based assertions (does the
dataset exist, with the right shape, lineage and passing DQ checks) rather than
string/LLM-judge matching. Tasks are declared in ``eval/tasks.yaml`` (config
driven), with an embedded fallback so it always runs. ``run_eval`` returns a
report and the CLI exits non-zero on any failure, so it gates CI.
"""
from __future__ import annotations

import os
from pathlib import Path

from .config import Settings
from .platform import Platform
from .samples import generate_samples

_EMBEDDED_TASKS = [
    {
        "id": "ingest_sources",
        "task": "Ingest the three economic data sources and register them.",
        "ingest": ["fema_policies", "property_sales", "hmda_loans"],
        "expect": {"status": "success", "catalog_has": ["fema_policies", "property_sales", "hmda_loans"]},
    },
    {
        "id": "build_and_validate_panel",
        "task": "Integrate the sources into a county-quarter panel and validate it.",
        "ingest": ["fema_policies", "property_sales", "hmda_loans"],
        "panel": {"name": "county_quarter_panel", "keys": ["county", "quarter"]},
        "validate": {"name": "county_quarter_panel", "min_rows": 100, "unique_key": ["county", "quarter"]},
        "expect": {
            "status": "success",
            "dataset_exists": "county_quarter_panel",
            "min_rows": {"dataset": "county_quarter_panel", "n": 200},
            "lineage_min_inputs": {"dataset": "county_quarter_panel", "n": 3},
            "validate_passed": "county_quarter_panel",
        },
    },
    {
        "id": "missing_source_is_graceful",
        "task": "Ingest a file that does not exist.",
        "ingest_raw": [{"path": "data/samples/__does_not_exist__.csv", "name": "ghost"}],
        "expect": {"status": "failed"},
    },
]


def _load_tasks() -> list[dict]:
    repo_root = Path(__file__).resolve().parents[1]
    yaml_path = Path(os.environ.get("ADP_EVAL_TASKS", str(repo_root / "eval" / "tasks.yaml")))
    if yaml_path.exists():
        try:
            import yaml

            data = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
            if data:
                return data
        except Exception:
            pass
    return _EMBEDDED_TASKS


def _hints_from_task(task: dict, samples: dict[str, str]) -> dict:
    hints: dict = {}
    ingest = []
    for name in task.get("ingest", []):
        ingest.append({"path": samples[name], "name": name})
    ingest.extend(task.get("ingest_raw", []))
    if ingest:
        hints["ingest"] = ingest
    for key in ("panel", "validate", "sql", "profile"):
        if key in task:
            hints[key] = task[key]
    return hints


def _validate_step_passed(run: dict, dataset: str) -> bool:
    for step in run["steps"]:
        if step["tool"] == "validate_dataset" and step.get("args", {}).get("name") == dataset:
            return bool(step.get("ok") and step.get("result", {}).get("passed"))
    return False


def _check(platform: Platform, expect: dict, run: dict) -> list[dict]:
    checks: list[dict] = []

    def add(name, passed, detail=""):
        checks.append({"check": name, "passed": bool(passed), "detail": str(detail)})

    if "status" in expect:
        add("status", run["status"] == expect["status"], f"{run['status']} == {expect['status']}")
    for name in expect.get("catalog_has", []):
        ds = platform.mem.get_dataset(name)
        add(f"catalog_has[{name}]", ds is not None and ds["n_rows"] > 0, ds["n_rows"] if ds else "missing")
    if "dataset_exists" in expect:
        name = expect["dataset_exists"]
        add(f"dataset_exists[{name}]", platform.mem.get_dataset(name) is not None)
    if "min_rows" in expect:
        spec = expect["min_rows"]
        ds = platform.mem.get_dataset(spec["dataset"])
        n = ds["n_rows"] if ds else 0
        add(f"min_rows[{spec['dataset']}]", n >= spec["n"], f"{n} >= {spec['n']}")
    if "lineage_min_inputs" in expect:
        spec = expect["lineage_min_inputs"]
        inputs = {e["input"] for e in platform.mem.lineage_of(spec["dataset"])}
        add(f"lineage_min_inputs[{spec['dataset']}]", len(inputs) >= spec["n"], f"{len(inputs)} >= {spec['n']}")
    if "validate_passed" in expect:
        name = expect["validate_passed"]
        add(f"validate_passed[{name}]", _validate_step_passed(run, name))

    return checks


def run_eval(settings: Settings | None = None) -> dict:
    settings = settings or Settings(db_path=Path("data/eval.duckdb"))
    # fresh state for deterministic, isolated evaluation
    for suffix in ("", ".wal"):
        f = Path(str(settings.db_path) + suffix)
        if f.exists():
            f.unlink()

    platform = Platform(settings)
    samples = generate_samples(platform.settings.data_dir / "samples")
    tasks = _load_tasks()

    results = []
    for task in tasks:
        run = platform.agent.run(task["task"], _hints_from_task(task, samples))
        checks = _check(platform, task.get("expect", {}), run)
        passed = all(c["passed"] for c in checks) and bool(checks)
        results.append({"id": task["id"], "passed": passed, "status": run["status"], "checks": checks})

    platform.close()
    n_pass = sum(r["passed"] for r in results)
    return {"total": len(results), "passed": n_pass, "failed": len(results) - n_pass, "results": results}

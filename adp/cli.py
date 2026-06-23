"""Command-line entrypoint: `adp <command>`.

Commands:
  demo      generate samples and run the full ingest -> panel -> validate pipeline
  ask       run a single agent task ("adp ask 'build a panel ...'")
  eval      run the evaluation suite (exits non-zero on failure)
  catalog   list registered datasets
  serve     run the FastAPI service (uvicorn)
"""
from __future__ import annotations

import argparse
import json
import sys

from .platform import Platform
from .samples import generate_samples


def _print(obj) -> None:
    print(json.dumps(obj, indent=2, ensure_ascii=False, default=str))


def cmd_demo(args) -> int:
    p = Platform()
    paths = generate_samples(p.settings.data_dir / "samples")
    task = "Ingest the three economic data sources, integrate them into a county-quarter panel, and validate it."
    hints = {
        "ingest": [{"path": paths[n], "name": n} for n in paths],
        "panel": {"name": "county_quarter_panel", "sources": list(paths), "keys": ["county", "quarter"]},
        "validate": {
            "name": "county_quarter_panel",
            "min_rows": 100,
            "unique_key": ["county", "quarter"],
            "not_null": ["county", "quarter"],
        },
    }
    result = p.agent.run(task, hints)
    print(f"\n=== RUN {result['run_id']} : {result['status']} (planner={result['planner']}) ===")
    for s in result["steps"]:
        mark = "OK " if s["ok"] else "ERR"
        detail = s.get("result", s.get("error"))
        print(f"  [{mark}] {s['tool']:<16} {json.dumps(detail, default=str)[:110]}")
    print("\n--- catalog ---")
    _print(p.mem.catalog())
    print("\n--- lineage(county_quarter_panel) ---")
    _print(p.mem.lineage_of("county_quarter_panel"))
    print("\n--- sample rows ---")
    _print(p.serve_dataset("county_quarter_panel", limit=3))
    from .monitoring import METRICS
    print("\n--- metrics ---")
    _print(METRICS.snapshot())
    p.close()
    return 0 if result["status"] == "success" else 1


def cmd_ask(args) -> int:
    p = Platform()
    result = p.agent.run(args.task)
    _print(result)
    p.close()
    return 0 if result["status"] == "success" else 1


def cmd_eval(args) -> int:
    from .eval_harness import run_eval
    report = run_eval()
    _print(report)
    return 0 if report["failed"] == 0 else 1


def cmd_catalog(args) -> int:
    p = Platform()
    _print(p.mem.catalog())
    p.close()
    return 0


def cmd_serve(args) -> int:
    import uvicorn
    uvicorn.run("adp.api:create_app", factory=True, host=args.host, port=args.port, reload=False)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="adp", description="Agentic Data Platform")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("demo", help="run the end-to-end demo pipeline").set_defaults(func=cmd_demo)

    ask = sub.add_parser("ask", help="run a single agent task")
    ask.add_argument("task")
    ask.set_defaults(func=cmd_ask)

    sub.add_parser("eval", help="run the evaluation suite").set_defaults(func=cmd_eval)
    sub.add_parser("catalog", help="list datasets").set_defaults(func=cmd_catalog)

    serve = sub.add_parser("serve", help="run the API server")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)
    serve.set_defaults(func=cmd_serve)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())

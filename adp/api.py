"""FastAPI service exposing the platform to researchers and the public.

Endpoints:
  GET  /health             liveness + capability summary
  GET  /metrics            operational metrics (counters + latency percentiles)
  GET  /catalog            registered datasets
  GET  /datasets/{name}    serve dataset rows (limit/offset)  <- "serve data"
  GET  /datasets/{name}/schema
  GET  /lineage/{name}     upstream provenance edges
  GET  /runs               agent run history
  GET  /runs/{run_id}
  POST /agent/run          run an agent task {task, hints}
"""
from __future__ import annotations

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from . import __version__
from .monitoring import METRICS
from .platform import Platform


class AgentRunRequest(BaseModel):
    task: str = Field(..., description="natural-language task for the agent")
    hints: dict | None = Field(default=None, description="optional structured plan hints")


def create_app(platform: Platform | None = None) -> FastAPI:
    P = platform or Platform()
    app = FastAPI(title="Agentic Data Platform", version=__version__)

    @app.get("/health")
    def health() -> dict:
        return {
            "status": "ok",
            "version": __version__,
            "llm_enabled": P.llm.available,
            "datasets": len(P.mem.catalog()),
        }

    @app.get("/metrics")
    def metrics() -> dict:
        return METRICS.snapshot()

    @app.get("/catalog")
    def catalog() -> dict:
        return {"datasets": P.mem.catalog()}

    @app.get("/datasets/{name}")
    def get_dataset(name: str, limit: int = 100, offset: int = 0) -> dict:
        try:
            return P.serve_dataset(name, limit=min(limit, P.settings.max_rows), offset=offset)
        except KeyError:
            raise HTTPException(status_code=404, detail=f"unknown dataset: {name}")

    @app.get("/datasets/{name}/schema")
    def get_schema(name: str) -> dict:
        try:
            return P.dataset_schema(name)
        except KeyError:
            raise HTTPException(status_code=404, detail=f"unknown dataset: {name}")

    @app.get("/lineage/{name}")
    def lineage(name: str) -> dict:
        return {"dataset": name, "edges": P.mem.lineage_of(name)}

    @app.get("/runs")
    def runs(limit: int = 50) -> dict:
        return {"runs": P.mem.runs(limit=limit)}

    @app.get("/runs/{run_id}")
    def run_detail(run_id: str) -> dict:
        run = P.mem.get_run(run_id)
        if run is None:
            raise HTTPException(status_code=404, detail=f"unknown run: {run_id}")
        return run

    @app.post("/agent/run")
    def agent_run(req: AgentRunRequest) -> dict:
        return P.agent.run(req.task, req.hints)

    return app


# Run with the factory (no I/O at import):  uvicorn adp.api:create_app --factory

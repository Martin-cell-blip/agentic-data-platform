from fastapi.testclient import TestClient

from adp.api import create_app


def test_api_endpoints(platform, panel_hints):
    platform.agent.run("Build the panel.", panel_hints)
    client = TestClient(create_app(platform))

    health = client.get("/health").json()
    assert health["status"] == "ok"
    assert health["datasets"] >= 4

    catalog = client.get("/catalog").json()
    assert any(d["name"] == "county_quarter_panel" for d in catalog["datasets"])

    served = client.get("/datasets/county_quarter_panel", params={"limit": 3}).json()
    assert len(served["rows"]) == 3

    assert client.get("/datasets/__nope__").status_code == 404

    lineage = client.get("/lineage/county_quarter_panel").json()
    assert {e["input"] for e in lineage["edges"]} >= {"fema_policies", "property_sales", "hmda_loans"}

    metrics = client.get("/metrics").json()
    assert "counters" in metrics and "latency" in metrics


def test_api_agent_run(platform, samples):
    client = TestClient(create_app(platform))
    body = {
        "task": "Ingest sources and build a panel.",
        "hints": {
            "ingest": [{"path": samples[n], "name": n} for n in samples],
            "panel": {"name": "p", "sources": list(samples), "keys": ["county", "quarter"]},
        },
    }
    resp = client.post("/agent/run", json=body)
    assert resp.status_code == 200
    assert resp.json()["status"] == "success"

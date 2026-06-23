def test_demo_pipeline_succeeds(platform, panel_hints):
    result = platform.agent.run("Build and validate the county-quarter panel.", panel_hints)

    assert result["status"] == "success"
    assert result["planner"] == "deterministic"
    # ingest(3) + build_panel + validate
    assert len(result["steps"]) == 5
    assert all(s["ok"] for s in result["steps"])


def test_panel_shape_and_columns(platform, panel_hints):
    platform.agent.run("Build the panel.", panel_hints)

    ds = platform.mem.get_dataset("county_quarter_panel")
    assert ds is not None
    assert ds["n_rows"] == 200  # 10 counties x 20 quarters

    cols = [c["column_name"] for c in platform.dataset_schema("county_quarter_panel")["schema"]]
    assert "county" in cols and "quarter" in cols
    # numeric columns get aggregated and source-prefixed
    assert "fema_policies_total_premium" in cols
    assert "hmda_loans_loan_count" in cols


def test_lineage_records_three_sources(platform, panel_hints):
    platform.agent.run("Build the panel.", panel_hints)
    inputs = {e["input"] for e in platform.mem.lineage_of("county_quarter_panel")}
    assert {"fema_policies", "property_sales", "hmda_loans"} <= inputs


def test_validation_gate_passes(platform, panel_hints):
    result = platform.agent.run("Build and validate.", panel_hints)
    validate_steps = [s for s in result["steps"] if s["tool"] == "validate_dataset"]
    assert validate_steps and validate_steps[0]["result"]["passed"]


def test_missing_source_fails_gracefully(platform):
    result = platform.agent.run(
        "Ingest a missing file.",
        {"ingest": [{"path": "does/not/exist.csv", "name": "ghost"}]},
    )
    assert result["status"] == "failed"
    assert result["steps"][-1]["ok"] is False
    # platform stays usable after a failed run
    assert platform.agent.run("list", {}).get("status") == "success"


def test_run_sql_rejects_writes(platform, panel_hints):
    platform.agent.run("Build the panel.", panel_hints)
    tool = platform.registry.get("run_sql")
    import pytest

    with pytest.raises(ValueError):
        tool.fn(sql="DROP TABLE marts.county_quarter_panel")

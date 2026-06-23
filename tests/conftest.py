from __future__ import annotations

import pytest

from adp.config import Settings
from adp.platform import Platform
from adp.samples import generate_samples


@pytest.fixture
def platform(tmp_path):
    # Force offline (deterministic planner) for hermetic tests, regardless of env.
    settings = Settings(anthropic_api_key=None, db_path=tmp_path / "wh.duckdb", data_dir=tmp_path)
    p = Platform(settings)
    try:
        yield p
    finally:
        p.close()


@pytest.fixture
def samples(tmp_path):
    return generate_samples(tmp_path / "samples")


@pytest.fixture
def panel_hints(samples):
    return {
        "ingest": [{"path": samples[n], "name": n} for n in samples],
        "panel": {"name": "county_quarter_panel", "sources": list(samples), "keys": ["county", "quarter"]},
        "validate": {"name": "county_quarter_panel", "min_rows": 100, "unique_key": ["county", "quarter"]},
    }

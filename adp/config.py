"""Runtime configuration, loaded from environment / .env (12-factor style)."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="ADP_", extra="ignore")

    # --- storage ---
    data_dir: Path = Field(default=Path("data"))
    db_path: Path = Field(default=Path("data/warehouse.duckdb"))

    # --- LLM (optional: platform degrades gracefully to a deterministic planner) ---
    # Accept the conventional ANTHROPIC_API_KEY as well as ADP_ANTHROPIC_API_KEY.
    anthropic_api_key: str | None = Field(
        default=None,
        validation_alias=AliasChoices("ADP_ANTHROPIC_API_KEY", "ANTHROPIC_API_KEY"),
    )
    model: str = Field(default="claude-sonnet-4-6")
    llm_max_tokens: int = Field(default=2048)

    # --- reliability ---
    max_retries: int = Field(default=3)
    retry_base_delay: float = Field(default=0.2)
    circuit_fail_threshold: int = Field(default=5)
    circuit_reset_s: float = Field(default=30.0)

    # --- serving ---
    max_rows: int = Field(default=10_000)
    log_level: str = Field(default="INFO")

    @property
    def llm_enabled(self) -> bool:
        return bool(self.anthropic_api_key)


@lru_cache
def get_settings() -> Settings:
    s = Settings()
    s.data_dir.mkdir(parents=True, exist_ok=True)
    s.db_path.parent.mkdir(parents=True, exist_ok=True)
    return s

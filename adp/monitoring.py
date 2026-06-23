"""Observability: structured JSON logging + an in-process metrics registry.

Every tool call, LLM call, retry and SQL execution is counted and timed here so
the platform can answer "is it healthy, how fast, how often does it retry" — the
operational questions a data platform must expose. Surfaced via GET /metrics.
"""
from __future__ import annotations

import json
import logging
import sys
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from threading import Lock


class _JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(record.created)),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        extra = getattr(record, "extra_fields", None)
        if extra:
            payload.update(extra)
        return json.dumps(payload, ensure_ascii=False, default=str)


def setup_logging(level: str = "INFO") -> None:
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(_JsonFormatter())
    root = logging.getLogger("adp")
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level.upper())
    root.propagate = False


def get_logger(name: str = "adp") -> logging.Logger:
    return logging.getLogger(name)


def log(logger: logging.Logger, level: int, msg: str, **fields) -> None:
    """Structured log helper: log(logger, logging.INFO, "event", key=value)."""
    logger.log(level, msg, extra={"extra_fields": fields})


def _percentile(values: list[float], pct: float) -> float:
    s = sorted(values)
    if not s:
        return 0.0
    idx = min(len(s) - 1, int(round((pct / 100.0) * (len(s) - 1))))
    return s[idx]


@dataclass
class Metrics:
    counters: dict[str, int] = field(default_factory=dict)
    timers_ms: dict[str, list[float]] = field(default_factory=dict)
    _lock: Lock = field(default_factory=Lock, repr=False)

    def incr(self, key: str, n: int = 1) -> None:
        with self._lock:
            self.counters[key] = self.counters.get(key, 0) + n

    def observe(self, key: str, ms: float) -> None:
        with self._lock:
            self.timers_ms.setdefault(key, []).append(round(ms, 3))

    @contextmanager
    def timer(self, key: str):
        t0 = time.perf_counter()
        try:
            yield
        finally:
            self.observe(key, (time.perf_counter() - t0) * 1000.0)

    def snapshot(self) -> dict:
        with self._lock:
            latency = {
                k: {
                    "count": len(v),
                    "p50_ms": _percentile(v, 50),
                    "p95_ms": _percentile(v, 95),
                    "max_ms": max(v),
                }
                for k, v in self.timers_ms.items()
                if v
            }
            return {"counters": dict(self.counters), "latency": latency}

    def reset(self) -> None:
        with self._lock:
            self.counters.clear()
            self.timers_ms.clear()


# Process-wide singleton.
METRICS = Metrics()

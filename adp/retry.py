"""Reliability primitives: exponential-backoff retry + a circuit breaker.

`retry_call` powers the agent's failure-retry and self-correction: the optional
`on_retry` hook lets the agent rewrite a step's arguments (e.g. repair broken
SQL via the LLM) between attempts.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Callable, TypeVar

from .monitoring import METRICS, get_logger, log

T = TypeVar("T")
_log = get_logger("adp.retry")


class TransientError(Exception):
    """Raised for errors that are worth retrying."""


class CircuitOpenError(Exception):
    """Raised when a circuit breaker is open and refuses the call."""


def retry_call(
    fn: Callable[[], T],
    *,
    retries: int = 3,
    base_delay: float = 0.2,
    max_delay: float = 5.0,
    exceptions: tuple[type[BaseException], ...] = (Exception,),
    on_retry: Callable[[int, BaseException], None] | None = None,
    sleep: Callable[[float], None] = time.sleep,
    label: str = "op",
) -> T:
    """Call ``fn`` up to ``retries`` extra times with exponential backoff.

    ``on_retry(attempt, error)`` runs before each re-attempt — used for
    self-correction (mutating closed-over args) before the next try.
    """
    attempt = 0
    while True:
        try:
            return fn()
        except exceptions as exc:  # noqa: BLE001 - deliberately broad, caller scopes it
            attempt += 1
            METRICS.incr(f"retry.{label}.attempt")
            if attempt > retries:
                METRICS.incr(f"retry.{label}.exhausted")
                log(_log, logging.ERROR, "retries_exhausted", label=label, attempts=attempt, error=str(exc))
                raise
            delay = min(max_delay, base_delay * (2 ** (attempt - 1)))
            log(_log, logging.WARNING, "retrying", label=label, attempt=attempt, delay=round(delay, 3), error=str(exc))
            if on_retry is not None:
                try:
                    on_retry(attempt, exc)
                except Exception as hook_exc:  # a failing self-correction shouldn't abort the retry loop
                    log(_log, logging.WARNING, "on_retry_failed", label=label, error=str(hook_exc))
            sleep(delay)


@dataclass
class CircuitBreaker:
    """Trips open after ``fail_threshold`` consecutive failures; auto half-opens
    after ``reset_after_s`` so a recovered dependency is retried."""

    fail_threshold: int = 5
    reset_after_s: float = 30.0
    _failures: int = 0
    _opened_at: float | None = None

    def allow(self) -> bool:
        if self._opened_at is None:
            return True
        if (time.time() - self._opened_at) >= self.reset_after_s:
            # half-open: allow one trial through
            self._opened_at = None
            self._failures = 0
            return True
        return False

    def record_success(self) -> None:
        self._failures = 0
        self._opened_at = None

    def record_failure(self) -> None:
        self._failures += 1
        if self._failures >= self.fail_threshold:
            self._opened_at = time.time()
            METRICS.incr("circuit.opened")

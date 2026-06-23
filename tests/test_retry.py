import pytest

from adp.retry import CircuitBreaker, retry_call

_no_sleep = lambda *_: None  # noqa: E731


def test_retry_succeeds_after_transient_failures():
    calls = {"n": 0}

    def fn():
        calls["n"] += 1
        if calls["n"] < 3:
            raise ValueError("transient")
        return "ok"

    assert retry_call(fn, retries=5, base_delay=0, sleep=_no_sleep) == "ok"
    assert calls["n"] == 3


def test_retry_raises_after_exhaustion():
    def fn():
        raise ValueError("permanent")

    with pytest.raises(ValueError):
        retry_call(fn, retries=2, base_delay=0, sleep=_no_sleep)


def test_on_retry_self_correction_recovers():
    """The on_retry hook can mutate closed-over state (the SQL-repair pattern)."""
    state = {"sql": "BAD"}

    def fn():
        if state["sql"] == "BAD":
            raise ValueError("syntax error")
        return state["sql"]

    def repair(attempt, exc):
        state["sql"] = "GOOD"

    assert retry_call(fn, retries=3, base_delay=0, on_retry=repair, sleep=_no_sleep) == "GOOD"


def test_circuit_breaker_trips_open():
    cb = CircuitBreaker(fail_threshold=2, reset_after_s=1000)
    assert cb.allow()
    cb.record_failure()
    cb.record_failure()
    assert cb.allow() is False


def test_circuit_breaker_half_opens_after_reset():
    cb = CircuitBreaker(fail_threshold=1, reset_after_s=0)
    cb.record_failure()
    assert cb.allow() is True  # reset window already elapsed

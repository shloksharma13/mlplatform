"""Safeguard tests for retrain triggering (P8.8).

Every branch of the trigger decision is covered: insufficient samples, breach
counting, active-Job suppression, cooldown suppression, and firing with a
counter reset (no retrain loop after the new version deploys).
"""

from __future__ import annotations

from drift_monitor.main import job_terminal
from drift_monitor.trigger import decide


def _decide(**overrides: object):  # type: ignore[no-untyped-def]
    """Decide with sane defaults and selective overrides."""
    params: dict[str, object] = {
        "breached": True,
        "consecutive_breaches": 0,
        "last_trigger_epoch": None,
        "now_epoch": 1_000.0,
        "retrain_active": False,
        "samples_ok": True,
        "consecutive_required": 2,
        "cooldown_seconds": 300,
    }
    params.update(overrides)
    return decide(**params)  # type: ignore[arg-type]


def test_no_fire_on_insufficient_samples_and_counter_resets() -> None:
    """Thin windows never trigger and clear any partial breach count."""
    decision = _decide(samples_ok=False, consecutive_breaches=1)

    assert decision.fire is False
    assert decision.reason == "insufficient_data"
    assert decision.consecutive_breaches == 0


def test_clean_window_resets_the_counter() -> None:
    """A non-breaching evaluation clears accumulated breaches."""
    decision = _decide(breached=False, consecutive_breaches=1)

    assert decision.fire is False
    assert decision.reason == "no_breach"
    assert decision.consecutive_breaches == 0


def test_first_breach_only_counts() -> None:
    """One breach arms the trigger without firing it."""
    decision = _decide(consecutive_breaches=0)

    assert decision.fire is False
    assert decision.reason == "breach_counting"
    assert decision.consecutive_breaches == 1


def test_second_consecutive_breach_fires_and_resets() -> None:
    """The Nth consecutive breach fires and restarts the count (no loop)."""
    decision = _decide(consecutive_breaches=1)

    assert decision.fire is True
    assert decision.reason == "firing"
    assert decision.consecutive_breaches == 0


def test_active_job_suppresses_but_keeps_count() -> None:
    """No second Job while one is active; the breach count is preserved."""
    decision = _decide(consecutive_breaches=1, retrain_active=True)

    assert decision.fire is False
    assert decision.reason == "retrain_active"
    assert decision.consecutive_breaches == 2


def test_cooldown_suppresses_but_keeps_count() -> None:
    """A recent trigger suppresses refires inside the cooldown window."""
    decision = _decide(
        consecutive_breaches=1, last_trigger_epoch=900.0, now_epoch=1_000.0
    )

    assert decision.fire is False
    assert decision.reason == "cooldown"
    assert decision.consecutive_breaches == 2


def test_elapsed_cooldown_fires_again() -> None:
    """After the cooldown, a sustained breach triggers the next cycle."""
    decision = _decide(
        consecutive_breaches=5, last_trigger_epoch=100.0, now_epoch=1_000.0
    )

    assert decision.fire is True
    assert decision.consecutive_breaches == 0


def test_failed_job_without_completion_time_is_terminal() -> None:
    """A failed Job never blocks future triggers, even without completionTime."""
    assert job_terminal(active=None, failed=1, completion_time=None) is True


def test_fresh_job_without_status_is_in_flight() -> None:
    """A just-created Job with no status yet suppresses a second trigger."""
    assert job_terminal(active=None, failed=None, completion_time=None) is False


def test_running_job_is_in_flight() -> None:
    """An active Job suppresses overlapping releases (helm lock safety)."""
    assert job_terminal(active=1, failed=1, completion_time=None) is False


def test_completed_job_is_terminal() -> None:
    """A completed Job never blocks future triggers."""
    assert job_terminal(active=None, failed=None, completion_time="2026-10-03") is True

"""Retrain trigger safeguards (P8.3/P8.8).

Pure decision function: fire only after ``consecutive_required`` back-to-back
breaches, with the cooldown elapsed since the last trigger and no retrain Job
already active. Every safeguard has a unit test in
``tests/test_drift_safeguards.py``.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TriggerDecision:
    """Verdict for one evaluation cycle plus the counter to persist."""

    fire: bool
    consecutive_breaches: int
    reason: str


def decide(
    *,
    breached: bool,
    consecutive_breaches: int,
    last_trigger_epoch: float | None,
    now_epoch: float,
    retrain_active: bool,
    samples_ok: bool,
    consecutive_required: int,
    cooldown_seconds: int,
) -> TriggerDecision:
    """Decide whether this cycle triggers a retrain Job."""
    if not samples_ok:
        return TriggerDecision(
            fire=False,
            consecutive_breaches=0,
            reason="insufficient_data",
        )
    if not breached:
        return TriggerDecision(
            fire=False,
            consecutive_breaches=0,
            reason="no_breach",
        )
    consecutive = consecutive_breaches + 1
    if consecutive < consecutive_required:
        return TriggerDecision(
            fire=False,
            consecutive_breaches=consecutive,
            reason="breach_counting",
        )
    if retrain_active:
        return TriggerDecision(
            fire=False,
            consecutive_breaches=consecutive,
            reason="retrain_active",
        )
    if (
        last_trigger_epoch is not None
        and now_epoch - last_trigger_epoch < cooldown_seconds
    ):
        return TriggerDecision(
            fire=False,
            consecutive_breaches=consecutive,
            reason="cooldown",
        )
    return TriggerDecision(
        fire=True,
        consecutive_breaches=0,
        reason="firing",
    )

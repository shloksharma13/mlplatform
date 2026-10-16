"""Unit tests for the release gate logic (P7.1) with synthetic predictions."""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from gate import ABS_FLOOR, evaluate_gate
from ml.data.schemas import FEATURE_COLUMNS


def _synthetic() -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Labels plus good candidate/champion probability arrays."""
    rng = np.random.RandomState(7)
    labels = (rng.rand(500) < 0.12).astype(int)
    good = np.clip(labels * 0.7 + rng.rand(500) * 0.3, 0.01, 0.99)
    champion = np.clip(good - 0.02, 0.01, 0.99)
    return labels, good, champion


def _dtypes() -> dict[str, object]:
    """Serving-compatible dtypes for every feature."""
    return {
        name: (np.int64 if name == "num_late_payments_90d" else np.float64)
        for name in FEATURE_COLUMNS
    }


def _report(**overrides: object):  # type: ignore[no-untyped-def]
    """Evaluate the gate on synthetic data with selective overrides."""
    labels, good, champion = _synthetic()
    params: dict[str, object] = {
        "model_name": "loan-default",
        "candidate_version": "2",
        "champion_version": "1",
        "candidate_proba": good,
        "champion_proba": champion,
        "labels": labels,
        "candidate_features": list(FEATURE_COLUMNS),
        "candidate_dtypes": _dtypes(),
    }
    params.update(overrides)
    return evaluate_gate(**params)  # type: ignore[arg-type]


def _check(report: object, name: str) -> bool:  # type: ignore[no-untyped-def]
    """Return the verdict of one named check."""
    for check in report.checks:  # type: ignore[union-attr]
        if check.name == name:
            return check.passed
    raise AssertionError(f"missing check {name}")


def test_gate_passes_for_good_candidate() -> None:
    """A better model on the same hold-out clears every check."""
    report = _report()

    assert report.passed
    assert report.candidate_auc >= ABS_FLOOR
    assert report.champion_auc is not None


def test_gate_rejects_below_absolute_floor() -> None:
    """A coin-flip model (AUC ~0.5) fails the floor even with no other issue."""
    _labels, _good, _champion = _synthetic()
    rng = np.random.RandomState(0)
    report = _report(candidate_proba=rng.rand(500))

    assert not report.passed
    assert not _check(report, "absolute_floor")


def test_gate_rejects_regression_beyond_tolerance() -> None:
    """A candidate 5 points worse than champion fails the regression check."""
    labels, _good, _champion = _synthetic()
    rng = np.random.RandomState(1)
    better_champion = np.clip(labels * 0.85 + rng.rand(500) * 0.1, 0.01, 0.99)
    noisy_candidate = np.clip(labels * 0.4 + rng.rand(500) * 0.6, 0.01, 0.99)
    report = _report(
        candidate_proba=noisy_candidate,
        champion_proba=better_champion,
    )

    assert not report.passed
    assert not _check(report, "no_regression")
    assert _check(report, "absolute_floor")


def test_gate_rejects_missing_feature() -> None:
    """A model trained without one serving feature fails signature compat."""
    features = [name for name in FEATURE_COLUMNS if name != FEATURE_COLUMNS[0]]
    report = _report(candidate_features=features)

    assert not report.passed
    assert not _check(report, "signature_compatible")


def test_gate_rejects_insane_positive_rate() -> None:
    """Perfect ranking with everything above threshold still fails sanity."""
    _labels, good, _champion = _synthetic()
    report = _report(candidate_proba=np.clip(good + 0.6, 0.01, 0.99))

    assert not report.passed
    assert not _check(report, "positive_rate_sane")
    assert _check(report, "absolute_floor")


def test_gate_rejects_too_few_scored_rows() -> None:
    """Fewer than 100 scored rows fails the load-and-predict check."""
    labels, good, champion = _synthetic()
    report = _report(
        candidate_proba=good[:50],
        champion_proba=champion[:50],
        labels=labels[:50],
    )

    assert not report.passed
    assert not _check(report, "loads_and_predicts")


def test_gate_requires_explicit_opt_in_without_champion() -> None:
    """Bootstrap without a champion fails closed unless explicitly allowed."""
    _labels, _good, _champion = _synthetic()
    denied = _report(
        champion_version=None,
        champion_proba=[],
        allow_no_champion=False,
    )
    allowed = _report(
        champion_version=None,
        champion_proba=[],
        allow_no_champion=True,
    )

    assert not denied.passed
    assert not _check(denied, "no_regression")
    assert allowed.passed

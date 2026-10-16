"""Unit tests for the drift mathematics (P8.1)."""

from __future__ import annotations

import numpy as np
import pandas as pd
from mlp_common.drift import DriftReport, feature_drift_report, ks, psi
from mlp_common.schemas import MODEL_FEATURES


def test_psi_is_zero_for_identical_samples() -> None:
    """The same distribution scores (almost) no instability."""
    rng = np.random.RandomState(0)
    sample = rng.lognormal(10.8, 0.5, 5_000)

    assert psi(sample, sample) == 0.0


def test_psi_exceeds_threshold_on_mean_shift() -> None:
    """A visible covariate shift scores above the 0.25 drift threshold."""
    rng = np.random.RandomState(1)
    reference = rng.normal(0.0, 1.0, 5_000)
    shifted = rng.normal(1.5, 1.0, 5_000)

    assert psi(reference, shifted) > 0.25


def test_psi_matches_hand_computed_example() -> None:
    """PSI equals a manually computed three-bin value."""
    reference = np.array([0.0] * 50 + [1.0] * 25 + [2.0] * 25)
    current = np.array([0.0] * 34 + [1.0] * 33 + [2.0] * 33)

    value = psi(reference, current, bins=3)

    # Reference quantile edges split [0,1), [1,2), [2,inf):
    # expected shares (0.50, 0.25, 0.25), actual (0.34, 0.33, 0.33).
    pairs = [(0.34, 0.50), (0.33, 0.25), (0.33, 0.25)]
    expected = sum((a - e) * np.log(a / e) for a, e in pairs)
    assert abs(value - expected) < 1e-9


def test_psi_handles_constant_features() -> None:
    """A constant reference scores 0 without change, 1.0 on any shift."""
    reference = np.zeros(1_000)

    assert psi(reference, np.zeros(500)) == 0.0
    assert psi(reference, np.ones(500)) == 1.0


def test_psi_rejects_empty_samples() -> None:
    """Empty inputs fail loudly instead of returning a number."""
    try:
        psi(np.array([]), np.array([1.0]))
    except ValueError as exc:
        assert "non-empty" in str(exc)
    else:
        raise AssertionError("expected ValueError")


def test_ks_detects_shift_with_significant_p() -> None:
    """KS reports a large D and tiny p for a shifted distribution."""
    rng = np.random.RandomState(2)
    reference = rng.normal(0.0, 1.0, 2_000)
    shifted = rng.normal(1.0, 1.0, 2_000)

    statistic, pvalue = ks(reference, shifted)

    assert statistic > 0.2
    assert pvalue < 0.01


def test_report_flags_drift_only_above_min_features() -> None:
    """One drifted feature is not enough when two are required."""
    rng = np.random.RandomState(3)
    size = 2_000
    ref = pd.DataFrame({name: rng.normal(0.0, 1.0, size) for name in MODEL_FEATURES})
    cur = ref.copy()
    f0, f1 = MODEL_FEATURES[0], MODEL_FEATURES[1]
    cur[f0] = rng.normal(2.0, 1.0, size)

    single = feature_drift_report(ref, cur, MODEL_FEATURES, min_features=2)
    assert isinstance(single, DriftReport)
    assert single.drifted_features == [f0]
    assert single.drifted is False

    cur[f1] = rng.normal(2.0, 1.0, size)
    double = feature_drift_report(ref, cur, MODEL_FEATURES, min_features=2)
    assert set(double.drifted_features) == {f0, f1}
    assert double.drifted is True


def test_report_is_quiet_on_baseline_traffic() -> None:
    """Two windows from the same generator stay below every threshold."""
    rng = np.random.RandomState(4)
    ref = pd.DataFrame({name: rng.normal(0.0, 1.0, 2_000) for name in MODEL_FEATURES})
    cur = pd.DataFrame({name: rng.normal(0.0, 1.0, 2_000) for name in MODEL_FEATURES})

    report = feature_drift_report(ref, cur, MODEL_FEATURES)

    assert report.drifted is False
    assert report.drifted_features == []


def test_psi_handles_discrete_features() -> None:
    """Discrete binary features do not false-positive on small sample variance."""
    rng = np.random.RandomState(5)
    # Binary categories e.g. 1.0 and 2.0 (like sex)
    ref = rng.choice([1.0, 2.0], size=5_000, p=[0.4, 0.6])
    cur = rng.choice([1.0, 2.0], size=1_000, p=[0.4, 0.6])

    val = psi(ref, cur)
    assert val < 0.05

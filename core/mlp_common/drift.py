"""Population Stability Index and Kolmogorov-Smirnov drift math (P8.1).

Pure functions over NumPy arrays; heavily unit-tested in
``tests/test_drift.py``. Threshold semantics follow SPEC 5.4/8.1: a feature
is drifted when ``psi > 0.25`` OR (``ks_d > 0.2`` AND ``ks_p < 0.01``), and
the population is drifted when at least ``DRIFT_MIN_FEATURES`` features
are drifted.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
from scipy.stats import ks_2samp


@dataclass
class FeatureDrift:
    """Drift verdict and statistics for one feature."""

    feature: str
    psi: float
    ks_d: float
    ks_p: float
    drifted: bool


@dataclass
class DriftReport:
    """Overall verdict across every tracked feature."""

    drifted: bool
    drifted_features: list[str]
    features: list[FeatureDrift] = field(default_factory=list)


def psi(
    reference: np.ndarray,
    current: np.ndarray,
    bins: int = 10,
    eps: float = 1e-4,
) -> float:
    """Compute the Population Stability Index with reference quantile bins."""
    ref = np.asarray(reference, dtype=float).ravel()
    cur = np.asarray(current, dtype=float).ravel()
    ref = ref[~np.isnan(ref)]
    cur = cur[~np.isnan(cur)]
    if len(ref) == 0 or len(cur) == 0:
        raise ValueError("psi requires non-empty reference and current samples")
    unique_ref = np.sort(np.unique(ref))
    if len(unique_ref) < 2:
        # Truly constant reference: any mean shift is total drift, else none.
        return 0.0 if np.allclose(ref.mean(), cur.mean()) else 1.0
    edges = np.unique(np.quantile(ref, np.linspace(0, 1, bins + 1)))
    if len(edges) < 3:
        # Discrete feature with few distinct values: bin by midpoints
        # between unique values.
        midpoints = (unique_ref[:-1] + unique_ref[1:]) / 2.0
        edges = np.concatenate(([-np.inf], midpoints, [np.inf]))
    else:
        edges[0], edges[-1] = -np.inf, np.inf
    expected = np.histogram(ref, edges)[0] / len(ref)
    actual = np.histogram(cur, edges)[0] / len(cur)
    expected = np.clip(expected, eps, None)
    actual = np.clip(actual, eps, None)
    return float(np.sum((actual - expected) * np.log(actual / expected)))


def ks(reference: np.ndarray, current: np.ndarray) -> tuple[float, float]:
    """Return the two-sample KS statistic D and its p-value."""
    ref = np.asarray(reference, dtype=float).ravel()
    cur = np.asarray(current, dtype=float).ravel()
    result = ks_2samp(ref, cur)
    return float(result.statistic), float(result.pvalue)


def feature_drift_report(
    ref_df: object,
    cur_df: object,
    features: list[str],
    *,
    psi_threshold: float = 0.25,
    ks_d_threshold: float = 0.2,
    ks_p_threshold: float = 0.01,
    min_features: int = 2,
) -> DriftReport:
    """Score every feature and decide whether the population drifted."""
    import pandas as pd

    ref = pd.DataFrame(ref_df)
    cur = pd.DataFrame(cur_df)
    drifts: list[FeatureDrift] = []
    for name in features:
        psi_value = psi(ref[name].to_numpy(), cur[name].to_numpy())
        ks_d, ks_p = ks(ref[name].to_numpy(), cur[name].to_numpy())
        drifted = (psi_value > psi_threshold) or (
            ks_d > ks_d_threshold and ks_p < ks_p_threshold
        )
        drifts.append(
            FeatureDrift(
                feature=name,
                psi=psi_value,
                ks_d=ks_d,
                ks_p=ks_p,
                drifted=drifted,
            )
        )
    drifted_names = [item.feature for item in drifts if item.drifted]
    return DriftReport(
        drifted=len(drifted_names) >= min_features,
        drifted_features=drifted_names,
        features=drifts,
    )

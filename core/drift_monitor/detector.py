"""One drift evaluation cycle (P8.2)."""

from __future__ import annotations

import pandas as pd
from mlp_common.drift import DriftReport, feature_drift_report
from mlp_common.schemas import MODEL_FEATURES


def evaluate_window(
    reference: pd.DataFrame,
    current: pd.DataFrame,
    *,
    psi_threshold: float,
    ks_d_threshold: float,
    ks_p_threshold: float,
    min_features: int,
) -> DriftReport:
    """Score the current prediction window against the model reference."""
    return feature_drift_report(
        reference,
        current,
        MODEL_FEATURES,
        psi_threshold=psi_threshold,
        ks_d_threshold=ks_d_threshold,
        ks_p_threshold=ks_p_threshold,
        min_features=min_features,
    )

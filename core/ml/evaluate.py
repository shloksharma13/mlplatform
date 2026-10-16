"""Consistent binary-classification evaluation for training and release gates."""

from __future__ import annotations

from typing import Protocol

import numpy as np
import pandas as pd
from sklearn.metrics import (
    accuracy_score,
    average_precision_score,
    brier_score_loss,
    log_loss,
    roc_auc_score,
)


class ProbabilityModel(Protocol):
    """Minimum interface needed to evaluate a binary classifier."""

    def predict_proba(self, features: pd.DataFrame) -> np.ndarray:
        """Return class probabilities for each row."""
        ...


def evaluate_binary_classifier(
    model: ProbabilityModel,
    features: pd.DataFrame,
    labels: pd.Series,
) -> dict[str, float]:
    """Compute the complete metric contract for one labeled data split."""
    probabilities = model.predict_proba(features)[:, 1]
    predictions = (probabilities >= 0.5).astype("int8")
    return {
        "auc": float(roc_auc_score(labels, probabilities)),
        "pr_auc": float(average_precision_score(labels, probabilities)),
        "log_loss": float(log_loss(labels, probabilities)),
        "brier": float(brier_score_loss(labels, probabilities)),
        "accuracy_at_0_5": float(accuracy_score(labels, predictions)),
        "positive_rate": float(np.mean(labels)),
    }

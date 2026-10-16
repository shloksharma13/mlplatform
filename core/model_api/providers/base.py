"""Stable feature and model interfaces shared by local and platform providers."""

from __future__ import annotations

from typing import Protocol

import numpy as np
import pandas as pd


class FeatureSource(Protocol):
    """Retrieve serving features without exposing the backing store."""

    def get(self, ids: list[int]) -> pd.DataFrame:
        """Return known IDs indexed by customer ID in canonical feature order."""
        ...

    def ping(self) -> bool:
        """Return whether the source can currently serve feature requests."""
        ...


class ModelSource(Protocol):
    """Score canonical feature frames using one immutable model version."""

    version: str
    feature_names: list[str]

    def predict_proba(self, features: pd.DataFrame) -> np.ndarray:
        """Return a positive-class probability for every input row."""
        ...

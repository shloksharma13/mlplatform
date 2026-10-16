"""In-memory Parquet and joblib providers for laptop phases P1-P5."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import joblib
import numpy as np
import pandas as pd
from mlp_common.schemas import MODEL_FEATURES

ENTITY_COLUMN = "customer_id"
FEATURE_COLUMNS = MODEL_FEATURES


class ParquetFeatureSource:
    """Load the latest feature snapshot once and serve it from memory."""

    def __init__(self, path: Path) -> None:
        """Load and validate the serving Parquet file."""
        frame = pd.read_parquet(path)
        missing_columns = set([ENTITY_COLUMN, *FEATURE_COLUMNS]) - set(frame.columns)
        if missing_columns:
            raise ValueError(
                f"feature file is missing columns: {sorted(missing_columns)}"
            )
        if frame[ENTITY_COLUMN].duplicated().any():
            raise ValueError("latest feature file contains duplicate customer IDs")
        self._features = frame.set_index(ENTITY_COLUMN)[FEATURE_COLUMNS]

    def get(self, ids: list[int]) -> pd.DataFrame:
        """Return all known requested IDs while preserving request order."""
        known_ids = [
            customer_id for customer_id in ids if customer_id in self._features.index
        ]
        return self._features.loc[known_ids].copy()

    def ping(self) -> bool:
        """Return true while the in-memory frame is available."""
        return not self._features.empty


class JoblibModelSource:
    """Serve probabilities from a locally persisted scikit-learn model."""

    def __init__(self, path: Path, version: str = "file-v1") -> None:
        """Load the model and derive its required feature order."""
        self._model: Any = joblib.load(path)
        self.version = version
        names = getattr(self._model, "feature_names_in_", FEATURE_COLUMNS)
        self.feature_names = [str(name) for name in names]

    def predict_proba(self, features: pd.DataFrame) -> np.ndarray:
        """Score input rows after enforcing the training feature order."""
        probabilities = self._model.predict_proba(features[self.feature_names])
        return np.asarray(probabilities, dtype="float64")

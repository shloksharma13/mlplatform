"""MLflow model provider (P6.5): loads one pinned registered version."""

from __future__ import annotations

from datetime import UTC, datetime

import numpy as np
import pandas as pd


class MlflowModelSource:
    """Score canonical feature frames with a pinned MLflow model version."""

    def __init__(self, model_name: str, version: str, tracking_uri: str) -> None:
        """Download ``models:/{name}/{version}`` and record its registry metadata."""
        import mlflow

        mlflow.set_tracking_uri(tracking_uri)
        self._model = mlflow.sklearn.load_model(f"models:/{model_name}/{version}")
        self.version = str(version)
        names = getattr(self._model, "feature_names_in_", None)
        if names is None:
            signature = getattr(self._model, "metadata", None)
            signature = getattr(signature, "signature", None)
            names = [item.name for item in signature.inputs] if signature else []
        self.feature_names = [str(name) for name in names]
        if not self.feature_names:
            raise ValueError(
                f"MLflow model {model_name}/{version} exposes no feature names"
            )
        metadata = mlflow.tracking.MlflowClient().get_model_version(
            model_name, self.version
        )
        self.run_id: str = metadata.run_id or ""
        self.registered_at: str = datetime.fromtimestamp(
            (metadata.creation_timestamp or 0) / 1000, tz=UTC
        ).isoformat()

    def predict_proba(self, features: pd.DataFrame) -> np.ndarray:
        """Score input rows after enforcing the training feature order."""
        probabilities = self._model.predict_proba(features[self.feature_names])
        return np.asarray(probabilities, dtype="float64")

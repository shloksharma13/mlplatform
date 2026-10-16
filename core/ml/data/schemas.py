"""Column contracts for the UCI Taiwan loan-default data set."""

from __future__ import annotations

import pandas as pd
from mlp_common.schemas import MODEL_FEATURES

ENTITY_COLUMN = "customer_id"
TIMESTAMP_COLUMN = "event_timestamp"
LABEL_COLUMN = "default_next_90d"

FEATURE_COLUMNS = list(MODEL_FEATURES)

FEATURE_DTYPES = {name: "float64" for name in FEATURE_COLUMNS}


def validate_features(frame: pd.DataFrame) -> None:
    """Raise ``ValueError`` when a generated feature frame violates its contract."""
    required = [ENTITY_COLUMN, TIMESTAMP_COLUMN, *FEATURE_COLUMNS]
    if list(frame.columns) != required:
        msg = f"feature columns must be {required}, got {list(frame.columns)}"
        raise ValueError(msg)
    if frame[required].isna().any().any():
        raise ValueError("feature data contains missing values")
    if frame[ENTITY_COLUMN].duplicated().all():
        raise ValueError("feature data contains no distinct customers")


def validate_labels(frame: pd.DataFrame) -> None:
    """Raise ``ValueError`` when a generated label frame violates its contract."""
    required = [ENTITY_COLUMN, TIMESTAMP_COLUMN, LABEL_COLUMN]
    if list(frame.columns) != required:
        msg = f"label columns must be {required}, got {list(frame.columns)}"
        raise ValueError(msg)
    if frame[required].isna().any().any():
        raise ValueError("label data contains missing values")
    if not frame[LABEL_COLUMN].isin([0, 1]).all():
        raise ValueError("labels must be binary")

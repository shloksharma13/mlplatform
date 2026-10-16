"""Tests for file-mode temporal model training."""

import datetime as dt
import json
from pathlib import Path

import joblib
import pandas as pd
from ml.data.generate import generate_dataset, write_dataset
from ml.data.schemas import ENTITY_COLUMN, FEATURE_COLUMNS, TIMESTAMP_COLUMN
from ml.train import (
    build_entity_frame,
    load_training_frame,
    temporal_split,
    train_file_model,
)


def test_temporal_split_is_ordered(tmp_path: Path) -> None:
    """No later row appears before an earlier row in the three partitions."""
    data_directory = tmp_path / "data"
    write_dataset(
        generate_dataset(customers=200, end_date=dt.date(2026, 10, 1)),
        data_directory,
    )
    splits = temporal_split(load_training_frame(data_directory))

    assert len(splits.train) == 560
    assert len(splits.validation) == 120
    assert len(splits.test) == 120
    assert (
        splits.train["event_timestamp"].max()
        <= splits.validation["event_timestamp"].min()
    )
    assert (
        splits.validation["event_timestamp"].max()
        <= splits.test["event_timestamp"].min()
    )


def test_file_training_writes_complete_artifacts(tmp_path: Path) -> None:
    """Training produces a usable model and all three metadata artifacts."""
    data_directory = tmp_path / "data"
    artifacts_directory = tmp_path / "artifacts"
    write_dataset(
        generate_dataset(customers=5_000, end_date=dt.date(2026, 10, 1)),
        data_directory,
    )

    metrics = train_file_model(data_directory, artifacts_directory)

    assert 0.75 <= metrics["test"]["auc"] <= 1.0
    assert set(path.name for path in artifacts_directory.iterdir()) == {
        "feature_schema.json",
        "metrics.json",
        "model.joblib",
        "reference.parquet",
    }
    assert len(pd.read_parquet(artifacts_directory / "reference.parquet")) <= 10_000
    model = joblib.load(artifacts_directory / "model.joblib")
    assert len(model.feature_names_in_) == len(FEATURE_COLUMNS)
    schema = json.loads((artifacts_directory / "feature_schema.json").read_text())
    assert [item["name"] for item in schema["features"]] == FEATURE_COLUMNS


def test_build_entity_frame_projects_join_keys(tmp_path: Path) -> None:
    """The Feast entity frame carries one ordered row per labeled event."""
    data_directory = tmp_path / "data"
    write_dataset(
        generate_dataset(customers=200, end_date=dt.date(2026, 10, 1)),
        data_directory,
    )
    labels = pd.read_parquet(data_directory / "labels.parquet")

    entity_df = build_entity_frame(labels)

    assert list(entity_df.columns) == [ENTITY_COLUMN, TIMESTAMP_COLUMN]
    assert len(entity_df) == len(labels)
    assert entity_df[TIMESTAMP_COLUMN].is_monotonic_increasing

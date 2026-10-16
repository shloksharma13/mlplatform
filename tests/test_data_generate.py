"""Acceptance tests for deterministic UCI Taiwan data generation."""

import datetime as dt

import pandas as pd
from ml.data.generate import generate_dataset
from ml.data.schemas import FEATURE_COLUMNS, LABEL_COLUMN

FIXED_END_DATE = dt.date(2026, 10, 1)


def test_generated_data_has_expected_shapes_and_ranges() -> None:
    """The default data set has 120k snapshots, 30k latest rows, and no gaps."""
    dataset = generate_dataset(end_date=FIXED_END_DATE)

    assert dataset.features.shape == (120_000, 25)
    assert dataset.labels.shape == (120_000, 3)
    assert dataset.latest_features.shape == (30_000, 25)
    assert not dataset.features.isna().any().any()
    assert not dataset.labels.isna().any().any()
    assert dataset.latest_features["customer_id"].is_unique
    assert 0.20 <= dataset.labels[LABEL_COLUMN].mean() <= 0.25


def test_generation_is_deterministic_for_seed_and_end_date() -> None:
    """The same seed and date produce byte-equivalent in-memory values."""
    first = generate_dataset(seed=7, customers=100, end_date=FIXED_END_DATE)
    second = generate_dataset(seed=7, customers=100, end_date=FIXED_END_DATE)

    pd.testing.assert_frame_equal(first.features, second.features)
    pd.testing.assert_frame_equal(first.labels, second.labels)
    pd.testing.assert_frame_equal(first.latest_features, second.latest_features)


def test_feature_directions_are_valid() -> None:
    """Generated values stay within the UCI Taiwan documented physical ranges."""
    features = generate_dataset(customers=1_000, end_date=FIXED_END_DATE).features

    assert list(features.columns[-len(FEATURE_COLUMNS) :]) == FEATURE_COLUMNS
    assert (features["limit_bal"] > 0).all()
    assert features["sex"].isin([1, 2]).all()
    assert features["education"].isin([0, 1, 2, 3, 4, 5, 6]).all()
    assert features["marriage"].isin([0, 1, 2, 3]).all()
    assert features["age"].between(18, 100).all()
    assert features["pay_0"].between(-2, 10).all()

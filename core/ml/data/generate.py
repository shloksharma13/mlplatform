"""Generate deterministic loan-default data from the UCI Taiwan credit dataset."""

from __future__ import annotations

import argparse
import datetime as dt
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from ml.data.schemas import (
    ENTITY_COLUMN,
    FEATURE_COLUMNS,
    LABEL_COLUMN,
    TIMESTAMP_COLUMN,
    validate_features,
    validate_labels,
)

DEFAULT_CUSTOMERS = 30_000
DEFAULT_SNAPSHOTS = 4
TARGET_POSITIVE_RATE = 0.2212
UCI_ZIP_URL = (
    "https://archive.ics.uci.edu/static/public/350/default+of+credit+card+clients.zip"
)


@dataclass(frozen=True)
class GeneratedData:
    """Feature, label, and latest-feature frames written by the CLI."""

    features: pd.DataFrame
    labels: pd.DataFrame
    latest_features: pd.DataFrame


def _ensure_raw_uci_taiwan() -> pd.DataFrame:
    """Load or download the official UCI Taiwan credit card default dataset."""
    parquet_path = Path("data/raw/uci_taiwan.parquet")
    if parquet_path.exists():
        df = pd.read_parquet(parquet_path)
    else:
        local_xls = Path("data/raw/default_of_credit_card_clients.xls")
        tmp_xls = Path("/tmp/uci_taiwan/default of credit card clients.xls")

        source_xls: Path | None = None
        if local_xls.exists():
            source_xls = local_xls
        elif tmp_xls.exists():
            source_xls = tmp_xls
        else:
            # Download from official UCI repository
            raw_dir = Path("data/raw")
            raw_dir.mkdir(parents=True, exist_ok=True)
            zip_path = raw_dir / "uci_taiwan.zip"
            urllib.request.urlretrieve(UCI_ZIP_URL, zip_path)
            with zipfile.ZipFile(zip_path, "r") as zf:
                zf.extractall(raw_dir)
            candidates = list(raw_dir.glob("*.xls"))
            if candidates:
                source_xls = candidates[0]
            else:
                raise FileNotFoundError(
                    "Failed to locate extracted UCI Taiwan .xls file"
                )

        df = pd.read_excel(source_xls, header=1)
        df.columns = [str(c).strip().lower() for c in df.columns]
        rename_map = {
            "id": ENTITY_COLUMN,
            "default payment next month": LABEL_COLUMN,
        }
        df.rename(columns=rename_map, inplace=True)

    # Cast types: entity as int64, label as int8, features as float64
    df[ENTITY_COLUMN] = df[ENTITY_COLUMN].astype("int64")
    df[LABEL_COLUMN] = df[LABEL_COLUMN].astype("int8")
    for col in FEATURE_COLUMNS:
        df[col] = df[col].astype("float64")

    # Cache as parquet for instant loading
    parquet_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(parquet_path, index=False)
    return df


def generate_dataset(
    *,
    seed: int = 42,
    customers: int = DEFAULT_CUSTOMERS,
    snapshots: int = DEFAULT_SNAPSHOTS,
    end_date: dt.date | None = None,
) -> GeneratedData:
    """Generate all snapshots, labels, and the latest serving feature view."""
    if customers < 1 or snapshots < 1:
        raise ValueError("customers and snapshots must both be positive")

    raw_data = _ensure_raw_uci_taiwan()
    if customers <= len(raw_data):
        selected = raw_data.iloc[:customers].copy().reset_index(drop=True)
    else:
        rng = np.random.default_rng(seed)
        indices = rng.choice(len(raw_data), size=customers, replace=True)
        selected = raw_data.iloc[indices].copy().reset_index(drop=True)
        selected[ENTITY_COLUMN] = np.arange(1, customers + 1, dtype="int64")

    effective_end = end_date or (dt.datetime.now(dt.UTC).date() - dt.timedelta(days=1))

    feature_frames: list[pd.DataFrame] = []
    label_frames: list[pd.DataFrame] = []

    for weeks_ago in reversed(range(snapshots)):
        timestamp = effective_end - dt.timedelta(days=7 * weeks_ago)
        ts_val = pd.Timestamp(timestamp, tz="UTC")

        feat_frame = selected[[ENTITY_COLUMN, *FEATURE_COLUMNS]].copy()
        feat_frame.insert(1, TIMESTAMP_COLUMN, ts_val)
        feature_frames.append(feat_frame)

        lbl_frame = selected[[ENTITY_COLUMN, LABEL_COLUMN]].copy()
        lbl_frame.insert(1, TIMESTAMP_COLUMN, ts_val)
        label_frames.append(lbl_frame)

    features = pd.concat(feature_frames, ignore_index=True)
    features = features[[ENTITY_COLUMN, TIMESTAMP_COLUMN, *FEATURE_COLUMNS]]

    labels = pd.concat(label_frames, ignore_index=True)
    labels = labels[[ENTITY_COLUMN, TIMESTAMP_COLUMN, LABEL_COLUMN]]

    latest = features.loc[
        features[TIMESTAMP_COLUMN] == features[TIMESTAMP_COLUMN].max()
    ].reset_index(drop=True)

    validate_features(features)
    validate_features(latest)
    validate_labels(labels)
    return GeneratedData(features=features, labels=labels, latest_features=latest)


def write_dataset(dataset: GeneratedData, output_directory: Path) -> None:
    """Write the generated frames atomically enough for repeatable local use."""
    output_directory.mkdir(parents=True, exist_ok=True)
    dataset.features.to_parquet(output_directory / "features.parquet", index=False)
    dataset.labels.to_parquet(output_directory / "labels.parquet", index=False)
    dataset.latest_features.to_parquet(
        output_directory / "features_latest.parquet", index=False
    )


def parse_args() -> argparse.Namespace:
    """Parse command-line options for the data generator."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=Path("data"))
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument(
        "--customers",
        type=int,
        default=DEFAULT_CUSTOMERS,
        help="Number of customers to include",
    )
    parser.add_argument(
        "--end-date",
        type=dt.date.fromisoformat,
        default=None,
        help="UTC date for the latest snapshot (default: yesterday)",
    )
    return parser.parse_args()


def main() -> None:
    """Generate and report the three local Parquet assets."""
    args = parse_args()
    dataset = generate_dataset(
        seed=args.seed, customers=args.customers, end_date=args.end_date
    )
    write_dataset(dataset, args.out)
    positive_rate = dataset.labels[LABEL_COLUMN].mean()
    print(
        f"generated rows={len(dataset.features)} customers="
        f"{dataset.features[ENTITY_COLUMN].nunique()} "
        f"positive_rate={positive_rate:.4f} out={args.out}"
    )


if __name__ == "__main__":
    main()

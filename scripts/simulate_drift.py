"""Simulate drift scenarios for the self-healing demo (P8.7, SPEC 6.4).

Scenarios: ``covariate`` (shifted feature distributions), ``concept`` (new
label mechanism), ``both`` (default demo: shifted features + new mechanism).

The script appends one new snapshot for every customer, uploads both Parquet
files to MinIO, and runs incremental materialization so the online store and
subsequent prediction traffic carry the drift. The champion-AUC-drop
assertion (SPEC: >= 0.07 on new data) uses the local file model, whose
weights are bit-identical to the registered champion (proven in P6.7), and
applies to scenarios with a concept change.
"""

from __future__ import annotations

import argparse
import subprocess
from pathlib import Path

import joblib
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
from ml.evaluate import evaluate_binary_classifier
from mlp_common.schemas import MODEL_FEATURES

DATA_DIR = Path("data")
TARGET_POSITIVE_RATE = 0.2212


def shifted_snapshot(latest: pd.DataFrame, timestamp: pd.Timestamp) -> pd.DataFrame:
    """Copy the latest snapshot and apply covariate shifts to UCI Taiwan features."""
    frame = latest.copy()
    frame[TIMESTAMP_COLUMN] = timestamp
    frame["pay_0"] = (frame["pay_0"] + 3.0).clip(upper=8.0)
    frame["limit_bal"] = (frame["limit_bal"] * 0.55).round()
    frame["bill_amt1"] = (frame["bill_amt1"] * 1.6).round()
    frame["pay_amt1"] = (frame["pay_amt1"] * 0.35).round()
    return frame[[ENTITY_COLUMN, TIMESTAMP_COLUMN, *FEATURE_COLUMNS]]


def concept_labels(features: pd.DataFrame, *, seed: int = 11) -> pd.DataFrame:
    """Generate labels for new rows under the drifted concept."""
    rng = np.random.default_rng(seed)
    signal = (
        (features["pay_0"] * 0.8)
        - (features["limit_bal"] / 60000.0)
        + (features["bill_amt1"] / 50000.0)
        - (features["pay_amt1"] / 20000.0)
        + rng.normal(0, 1.0, len(features))
    )
    threshold = float(np.percentile(signal, (1 - TARGET_POSITIVE_RATE) * 100))
    outcomes = (signal > threshold).astype("int8")
    return features[[ENTITY_COLUMN, TIMESTAMP_COLUMN]].assign(
        **{LABEL_COLUMN: outcomes}
    )


def parse_args() -> argparse.Namespace:
    """Parse simulation CLI options."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--scenario", choices=["covariate", "concept", "both"], default="both"
    )
    parser.add_argument("--data-dir", type=Path, default=DATA_DIR)
    return parser.parse_args()


def main() -> int:
    """Append the drifted snapshot, publish it, and materialize it."""
    args = parse_args()
    features = pd.read_parquet(args.data_dir / "features.parquet")
    labels = pd.read_parquet(args.data_dir / "labels.parquet")
    latest = pd.read_parquet(args.data_dir / "features_latest.parquet")

    # Incremental materialization only covers (cursor, now]: a future-dated
    # snapshot would never reach the online store, so cap the new timestamp
    # at the current time (weekly cadence when the data is stale).
    # If features already contains drifted snapshots, reset back to the 4 baseline
    # snapshots so rerun is always idempotent and advances past the data window.
    unique_ts = sorted(features[TIMESTAMP_COLUMN].unique())
    if len(unique_ts) > 4:
        baseline_cutoff = unique_ts[3]
        features = features[features[TIMESTAMP_COLUMN] <= baseline_cutoff]
        labels = labels[labels[TIMESTAMP_COLUMN] <= baseline_cutoff]

    max_timestamp = pd.Timestamp(features[TIMESTAMP_COLUMN].max())
    now = pd.Timestamp.now(tz="UTC")
    new_timestamp = min(max_timestamp + pd.Timedelta(days=7), now)
    if new_timestamp <= max_timestamp:
        print("ERROR: clock did not advance past the data window")
        return 1
    if args.scenario == "concept":
        new_features = latest.copy()
        new_features[TIMESTAMP_COLUMN] = new_timestamp
    else:
        new_features = shifted_snapshot(latest, new_timestamp)
    if args.scenario == "covariate":
        # Same concept on shifted features: reuse the customer's baseline labels.
        new_labels = labels.loc[labels[TIMESTAMP_COLUMN] == max_timestamp].copy()
        new_labels[TIMESTAMP_COLUMN] = new_timestamp
    else:
        new_labels = concept_labels(new_features)
    validate_features(new_features)
    validate_labels(new_labels)

    model = joblib.load("artifacts/model.joblib")
    baseline = labels.loc[labels[TIMESTAMP_COLUMN] == labels[TIMESTAMP_COLUMN].max()]
    baseline_features = features.merge(
        baseline[[ENTITY_COLUMN, TIMESTAMP_COLUMN]],
        on=[ENTITY_COLUMN, TIMESTAMP_COLUMN],
    )
    baseline_auc = evaluate_binary_classifier(
        model, baseline_features[MODEL_FEATURES], baseline[LABEL_COLUMN]
    )["auc"]
    new_auc = evaluate_binary_classifier(
        model, new_features[MODEL_FEATURES], new_labels[LABEL_COLUMN]
    )["auc"]
    drop = baseline_auc - new_auc
    print(
        f"champion baseline_auc={baseline_auc:.4f} "
        f"new_data_auc={new_auc:.4f} drop={drop:.4f}"
    )
    if args.scenario in ("concept", "both") and drop < 0.07:
        print("ERROR: concept drift must drop champion AUC by >= 0.07")
        return 1

    updated_features = pd.concat([features, new_features], ignore_index=True)
    updated_labels = pd.concat([labels, new_labels], ignore_index=True)
    updated_features.to_parquet(args.data_dir / "features.parquet", index=False)
    updated_labels.to_parquet(args.data_dir / "labels.parquet", index=False)
    new_features.to_parquet(args.data_dir / "features_latest.parquet", index=False)
    print(
        f"simulate_drift scenario={args.scenario} "
        f"new_timestamp={new_timestamp.date()} rows={len(new_features)}"
    )

    repo_root = Path(__file__).resolve().parents[1]
    publish_sh = repo_root / "scripts" / "publish_feast_data.sh"
    run_job_sh = repo_root / "scripts" / "run_job.sh"
    mat_yaml = repo_root / "infra" / "jobs" / "feast-materialize-job.yaml"

    subprocess.run(["bash", str(publish_sh)], check=True)
    subprocess.run(
        ["bash", str(run_job_sh), str(mat_yaml)],
        check=True,
    )
    print("simulate_drift published + materialized")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

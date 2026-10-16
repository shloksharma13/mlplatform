"""Train the loan-default model; file mode for P1, Feast+MLflow mode for P6.4."""

from __future__ import annotations

import argparse
import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import joblib
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier

from ml.data.schemas import (
    ENTITY_COLUMN,
    FEATURE_COLUMNS,
    LABEL_COLUMN,
    TIMESTAMP_COLUMN,
)
from ml.evaluate import evaluate_binary_classifier


@dataclass(frozen=True)
class TemporalSplits:
    """Chronologically ordered train, validation, and test partitions."""

    train: pd.DataFrame
    validation: pd.DataFrame
    test: pd.DataFrame


def load_training_frame(data_directory: Path) -> pd.DataFrame:
    """Join generated features to labels using both entity and event timestamp."""
    features = pd.read_parquet(data_directory / "features.parquet")
    labels = pd.read_parquet(data_directory / "labels.parquet")
    frame = features.merge(
        labels,
        on=[ENTITY_COLUMN, TIMESTAMP_COLUMN],
        how="inner",
        validate="one_to_one",
    )
    if len(frame) != len(features) or len(frame) != len(labels):
        raise ValueError("feature/label join dropped rows")
    return frame.sort_values([TIMESTAMP_COLUMN, ENTITY_COLUMN]).reset_index(drop=True)


def temporal_split(frame: pd.DataFrame) -> TemporalSplits:
    """Split sorted observations 70%/15%/15% without shuffling future into past."""
    if len(frame) < 20:
        raise ValueError("at least 20 rows are required for a temporal split")
    ordered = frame.sort_values([TIMESTAMP_COLUMN, ENTITY_COLUMN]).reset_index(
        drop=True
    )
    train_end = int(len(ordered) * 0.70)
    validation_end = int(len(ordered) * 0.85)
    return TemporalSplits(
        train=ordered.iloc[:train_end].copy(),
        validation=ordered.iloc[train_end:validation_end].copy(),
        test=ordered.iloc[validation_end:].copy(),
    )


def build_model() -> HistGradientBoostingClassifier:
    """Create the pinned deterministic gradient-boosted classifier."""
    return HistGradientBoostingClassifier(
        learning_rate=0.08,
        max_iter=180,
        max_leaf_nodes=31,
        l2_regularization=0.2,
        random_state=42,
    )


def _feature_schema(frame: pd.DataFrame) -> dict[str, Any]:
    """Build the serving schema artifact in canonical feature order."""
    return {
        "features": [
            {"name": column, "dtype": str(frame[column].dtype)}
            for column in FEATURE_COLUMNS
        ],
        "entity": ENTITY_COLUMN,
        "timestamp": TIMESTAMP_COLUMN,
        "label": LABEL_COLUMN,
    }


def train_file_model(data_directory: Path, artifacts_directory: Path) -> dict[str, Any]:
    """Train, evaluate, and persist the complete P1 artifact set."""
    frame = load_training_frame(data_directory)
    splits = temporal_split(frame)
    model = build_model()
    model.fit(splits.train[FEATURE_COLUMNS], splits.train[LABEL_COLUMN])

    metrics: dict[str, Any] = {
        "validation": evaluate_binary_classifier(
            model,
            splits.validation[FEATURE_COLUMNS],
            splits.validation[LABEL_COLUMN],
        ),
        "test": evaluate_binary_classifier(
            model, splits.test[FEATURE_COLUMNS], splits.test[LABEL_COLUMN]
        ),
        "rows": {
            "train": len(splits.train),
            "validation": len(splits.validation),
            "test": len(splits.test),
        },
    }

    artifacts_directory.mkdir(parents=True, exist_ok=True)
    joblib.dump(model, artifacts_directory / "model.joblib")
    (artifacts_directory / "metrics.json").write_text(
        json.dumps(metrics, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    reference = splits.train[FEATURE_COLUMNS].sample(
        n=min(10_000, len(splits.train)), random_state=42
    )
    reference.to_parquet(artifacts_directory / "reference.parquet", index=False)
    (artifacts_directory / "feature_schema.json").write_text(
        json.dumps(_feature_schema(frame), indent=2) + "\n", encoding="utf-8"
    )
    return metrics


def build_entity_frame(labels: pd.DataFrame) -> pd.DataFrame:
    """Project the Feast entity frame from a labels frame.

    The point-in-time join needs one row per (customer, event timestamp);
    the label itself is merged back after retrieval to avoid leakage.
    """
    required = [ENTITY_COLUMN, TIMESTAMP_COLUMN]
    missing = [column for column in required if column not in labels.columns]
    if missing:
        raise ValueError(f"labels frame is missing columns: {missing}")
    return (
        labels[required]
        .sort_values([TIMESTAMP_COLUMN, ENTITY_COLUMN])
        .reset_index(drop=True)
    )


def _model_hyperparameters() -> dict[str, Any]:
    """Return the pinned hyperparameter contract shared by every training mode."""
    return {
        "learning_rate": 0.08,
        "max_iter": 180,
        "max_leaf_nodes": 31,
        "l2_regularization": 0.2,
        "random_state": 42,
    }


def train_mlflow_model(
    *,
    data_directory: Path,
    feast_repo: Path,
    tracking_uri: str,
    experiment: str,
    model_name: str,
    feature_service_name: str,
    trigger: str,
    git_sha: str,
    shuffle_labels: bool = False,
) -> dict[str, Any]:
    """Train on Feast point-in-time features and register the model in MLflow.

    Runs point-in-time-correct retrieval through the Feast feature service,
    trains the pinned classifier on the temporal train split, and logs one
    MLflow run with params, metrics, ``reference.parquet`` (train sample),
    ``eval_set.parquet`` (temporal test split with labels), and the sklearn
    model with signature plus input example. The registered version receives
    alias ``champion`` when no champion exists, else ``candidate``.
    """
    import mlflow
    import mlflow.sklearn
    from feast import FeatureStore
    from mlflow.models import infer_signature
    from mlflow.tracking import MlflowClient

    labels = pd.read_parquet(data_directory / "labels.parquet")
    entity_df = build_entity_frame(labels)
    store = FeatureStore(repo_path=str(feast_repo))
    service = store.get_feature_service(feature_service_name)
    retrieved = store.get_historical_features(
        entity_df=entity_df, features=service
    ).to_df()
    frame = (
        retrieved.merge(
            labels,
            on=[ENTITY_COLUMN, TIMESTAMP_COLUMN],
            how="inner",
            validate="one_to_one",
        )
        .sort_values([TIMESTAMP_COLUMN, ENTITY_COLUMN])
        .reset_index(drop=True)
    )
    if len(frame) != len(labels):
        raise ValueError("feast retrieval dropped label rows")
    if shuffle_labels:
        frame = frame.copy()
        frame[LABEL_COLUMN] = (
            frame[LABEL_COLUMN].sample(frac=1.0, random_state=7).to_numpy()
        )
    splits = temporal_split(frame)

    hyperparameters = _model_hyperparameters()
    model = HistGradientBoostingClassifier(**hyperparameters)
    model.fit(splits.train[FEATURE_COLUMNS], splits.train[LABEL_COLUMN])
    validation_metrics = evaluate_binary_classifier(
        model, splits.validation[FEATURE_COLUMNS], splits.validation[LABEL_COLUMN]
    )
    test_metrics = evaluate_binary_classifier(
        model, splits.test[FEATURE_COLUMNS], splits.test[LABEL_COLUMN]
    )

    data_start = str(labels[TIMESTAMP_COLUMN].min())
    data_end = str(labels[TIMESTAMP_COLUMN].max())
    reference = splits.train[FEATURE_COLUMNS].sample(
        n=min(10_000, len(splits.train)), random_state=42
    )
    eval_set = splits.test[
        [ENTITY_COLUMN, TIMESTAMP_COLUMN, *FEATURE_COLUMNS, LABEL_COLUMN]
    ]

    mlflow.set_tracking_uri(tracking_uri)
    mlflow.set_experiment(experiment)
    with mlflow.start_run() as run:
        mlflow.log_params(
            {
                **hyperparameters,
                "data_start": data_start,
                "data_end": data_end,
                "feast_feature_service": feature_service_name,
                "git_sha": git_sha,
            }
        )
        mlflow.log_metrics(
            {f"validation_{name}": value for name, value in validation_metrics.items()}
            | {f"test_{name}": value for name, value in test_metrics.items()}
        )
        mlflow.set_tags(
            {
                "feast_feature_service": feature_service_name,
                "data_start": data_start,
                "data_end": data_end,
                "git_sha": git_sha,
                "trigger": trigger,
            }
        )
        with tempfile.TemporaryDirectory() as tmp:
            reference_path = Path(tmp) / "reference.parquet"
            reference.to_parquet(reference_path, index=False)
            mlflow.log_artifact(str(reference_path), artifact_path="")
            eval_path = Path(tmp) / "eval_set.parquet"
            eval_set.to_parquet(eval_path, index=False)
            mlflow.log_artifact(str(eval_path), artifact_path="")
        signature = infer_signature(
            splits.train[FEATURE_COLUMNS].iloc[:100],
            model.predict(splits.train[FEATURE_COLUMNS].iloc[:100]),
        )
        # cloudpickle (not skops): skops refuses to persist
        # HistGradientBoosting's TreePredictor without an explicit trusted-types
        # list at both save and load time. We train and serve only our own
        # registered versions, and cloudpickle round-trips the estimator with
        # no trust gate on either side.
        mlflow.sklearn.log_model(
            model,
            artifact_path="model",
            serialization_format="cloudpickle",
            signature=signature,
            input_example=splits.train[FEATURE_COLUMNS].iloc[:5],
            registered_model_name=model_name,
        )
        run_id = run.info.run_id

    client = MlflowClient()
    versions = client.search_model_versions(f"name='{model_name}'")
    if not versions:
        raise RuntimeError(f"no versions found for registered model {model_name!r}")
    version = str(max(int(item.version) for item in versions))
    try:
        client.get_model_version_by_alias(model_name, "champion")
        alias = "candidate"
    except Exception:
        alias = "champion"
    client.set_registered_model_alias(model_name, alias, version)
    return {
        "run_id": run_id,
        "version": version,
        "alias": alias,
        "validation": validation_metrics,
        "test": test_metrics,
        "rows": {
            "train": len(splits.train),
            "validation": len(splits.validation),
            "test": len(splits.test),
        },
    }


def parse_args() -> argparse.Namespace:
    """Parse training CLI options."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=["file", "mlflow"], default="file")
    parser.add_argument("--data-dir", type=Path, default=Path("data"))
    parser.add_argument("--artifacts-dir", type=Path, default=Path("artifacts"))
    parser.add_argument("--feast-repo", type=Path, default=Path("feature_repo"))
    parser.add_argument(
        "--tracking-uri",
        default=os.environ.get("MLFLOW_TRACKING_URI", "http://localhost:5000"),
    )
    parser.add_argument("--experiment", default="loan-default")
    parser.add_argument("--model-name", default="loan-default")
    parser.add_argument("--feature-service", default="loan_default_v1")
    parser.add_argument("--trigger", default=os.environ.get("TRIGGER", "manual"))
    parser.add_argument("--git-sha", default=os.environ.get("GIT_SHA", "unknown"))
    parser.add_argument(
        "--shuffle-labels",
        action="store_true",
        help=(
            "drill-only: randomly permute labels before the split, destroying "
            "all signal so the candidate scores AUC~=0.5 (P7.4 drill b)"
        ),
    )
    return parser.parse_args()


def main() -> None:
    """Run the selected training mode and print its acceptance metrics."""
    args = parse_args()
    if args.mode != "file":
        result = train_mlflow_model(
            data_directory=args.data_dir,
            feast_repo=args.feast_repo,
            tracking_uri=args.tracking_uri,
            experiment=args.experiment,
            model_name=args.model_name,
            feature_service_name=args.feature_service,
            trigger=args.trigger,
            git_sha=args.git_sha,
            shuffle_labels=args.shuffle_labels,
        )
        print(
            f"mlflow trained run_id={result['run_id']} "
            f"version={result['version']} alias={result['alias']} "
            f"validation_auc={result['validation']['auc']:.4f} "
            f"test_auc={result['test']['auc']:.4f}"
        )
        return
    metrics = train_file_model(args.data_dir, args.artifacts_dir)
    print(
        f"trained rows={metrics['rows']} "
        f"validation_auc={metrics['validation']['auc']:.4f} "
        f"test_auc={metrics['test']['auc']:.4f}"
    )


if __name__ == "__main__":
    main()

"""Automated retraining entry point (P8.4).

Runs as a Kubernetes Job created by drift-monitor: pulls the most recent
labeled window through the same Feast feature service training always uses,
retrains the pinned classifier, registers the new version as ``candidate``,
and dispatches the release. Any failure exits non-zero so the Job — and the
``RetrainFailed`` alert — fires visibly.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from datetime import timedelta
from pathlib import Path

import pandas as pd
from ml.data.schemas import TIMESTAMP_COLUMN
from ml.train import train_mlflow_model
from mlp_common.config import ServiceSettings
from mlp_common.logging import configure_logging

from retrain import dispatch as dispatch_module
from retrain import register as register_module

LOGGER = logging.getLogger(__name__)

#: Retraining looks back this far from the newest label (SPEC P8.4).
TRAINING_WINDOW_DAYS = 60


class RetrainSettings(ServiceSettings):
    """Environment contract for the retrain Job."""

    service_name: str = "retrain"
    model_name: str = "loan-default"
    feature_service: str = "loan_default_v1"
    experiment: str = "loan-default"
    feast_repo: Path = Path("feature_repo")
    mlflow_tracking_uri: str = "http://mlflow.data.svc:5000"
    parent_champion: str = ""
    drift_report: str = ""
    trigger: str = "drift"
    git_sha: str = "unknown"


def download_labels(endpoint: str, destination: Path) -> Path:
    """Fetch ``labels.parquet`` from the MinIO feast-offline bucket."""
    import pyarrow.fs
    import pyarrow.parquet

    filesystem = pyarrow.fs.S3FileSystem(
        access_key=os.environ.get("AWS_ACCESS_KEY_ID"),
        secret_key=os.environ.get("AWS_SECRET_ACCESS_KEY"),
        endpoint_override=endpoint,
        scheme="http",
        region="us-east-1",
    )
    table = pyarrow.parquet.read_table(
        "feast-offline/labels.parquet", filesystem=filesystem
    )
    destination.parent.mkdir(parents=True, exist_ok=True)
    pyarrow.parquet.write_table(table, destination)
    return destination


def latest_label_window(
    labels_path: Path, *, window_days: int = TRAINING_WINDOW_DAYS
) -> pd.DataFrame:
    """Return labels from the most recent ``window_days``-long window."""
    labels = pd.read_parquet(labels_path)
    end = labels[TIMESTAMP_COLUMN].max()
    start = end - timedelta(days=window_days)
    window = labels[labels[TIMESTAMP_COLUMN] >= start].reset_index(drop=True)
    if window.empty:
        raise ValueError("no labels inside the retraining window")
    return window


def post_parent_window(labels: pd.DataFrame, data_end: str) -> pd.DataFrame:
    """Return labels newer than the parent champion's training window.

    Training on the full 60-day window mixes the old and new concepts, which
    scores ~0.68 on drifted hold-outs and can never clear the release floor.
    Post-parent data carries one concept, so the retrained model can learn it.
    """
    cutoff = pd.Timestamp(data_end)
    window = labels[labels[TIMESTAMP_COLUMN] > cutoff].reset_index(drop=True)
    if len(window) < 1_000:
        raise ValueError(f"only {len(window)} labels past the parent window; need 1000")
    return window


def main() -> int:
    """Retrain, register the candidate, and dispatch the release."""
    settings = RetrainSettings()
    configure_logging(settings.service_name, settings.log_level)
    LOGGER.info(
        "retrain starting",
        extra={
            "parent_champion": settings.parent_champion,
            "trigger": settings.trigger,
        },
    )

    try:
        import mlflow

        mlflow.set_tracking_uri(settings.mlflow_tracking_uri)
        client = mlflow.tracking.MlflowClient()

        with tempfile.TemporaryDirectory() as tmp:
            labels_path = os.environ.get("LABELS_PATH")
            if labels_path is None:
                labels_path = str(
                    download_labels(
                        os.environ.get(
                            "FEAST_S3_ENDPOINT_URL", "http://minio.data.svc:9000"
                        ),
                        Path(tmp) / "full" / "labels.parquet",
                    )
                )
            window_path = Path(tmp) / "labels.parquet"
            full_labels = pd.read_parquet(Path(labels_path))
            if settings.parent_champion:
                parent = client.get_model_version(
                    settings.model_name, settings.parent_champion
                )
                parent_run = client.get_run(parent.run_id or "")
                data_end = parent_run.data.params.get("data_end", "")
                LOGGER.info(
                    "using post-parent window",
                    extra={
                        "parent": settings.parent_champion,
                        "after": data_end,
                    },
                )
                window = post_parent_window(full_labels, data_end)
            else:
                window = latest_label_window(Path(labels_path))
            window.to_parquet(window_path, index=False)
            data_directory = Path(tmp)
            result = train_mlflow_model(
                data_directory=data_directory,
                feast_repo=settings.feast_repo,
                tracking_uri=settings.mlflow_tracking_uri,
                experiment=settings.experiment,
                model_name=settings.model_name,
                feature_service_name=settings.feature_service,
                trigger=settings.trigger,
                git_sha=settings.git_sha,
            )
            version = result["version"]
            register_module.tag_version(
                client,
                settings.model_name,
                version,
                {
                    "trigger": settings.trigger,
                    "parent_champion": settings.parent_champion,
                },
            )
            if settings.drift_report:
                report_path = Path(tmp) / "drift_report.json"
                report_path.write_text(settings.drift_report, encoding="utf-8")
                client.log_artifact(result["run_id"], str(report_path), "drift")
            LOGGER.info(
                "candidate registered",
                extra={"version": version, "test_auc": result["test"]["auc"]},
            )
            mode = dispatch_module.dispatch_candidate(version)
            LOGGER.info("dispatch complete", extra={"mode": mode})
            print(json.dumps({"version": version, "dispatch": mode}))
            return 0
    except Exception:
        LOGGER.exception("retrain failed")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

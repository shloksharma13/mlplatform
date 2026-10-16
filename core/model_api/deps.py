"""Settings and provider construction for the serving application."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from pathlib import Path
from typing import TypeVar

from mlp_common.config import ServiceSettings

from model_api.providers.base import FeatureSource, ModelSource
from model_api.providers.feast_features import FeastFeatureSource
from model_api.providers.mlflow_model import MlflowModelSource
from model_api.providers.stub import JoblibModelSource, ParquetFeatureSource

LOGGER = logging.getLogger(__name__)

#: Startup budget for platform providers (SPEC P6.5: exit 1 after 60s).
STARTUP_TIMEOUT_SECONDS = 60.0
STARTUP_RETRY_SECONDS = 2.0

SourceT = TypeVar("SourceT", bound=FeatureSource | ModelSource)


class ModelApiSettings(ServiceSettings):
    """Environment contract for the model API."""

    service_name: str = "model-api"
    model_name: str = "loan-default"
    model_version: str = "file-v1"
    model_source: str = "file"
    feature_source: str = "parquet"
    model_path: Path = Path("artifacts/model.joblib")
    feature_path: Path = Path("data/features_latest.parquet")
    feature_service: str = "loan_default_v1"
    mlflow_tracking_uri: str = "http://mlflow.data.svc:5000"
    redis_url: str = "redis://redis.data.svc:6379/0"
    database_url: str | None = None
    enable_chaos: bool = False


def _with_startup_retries(label: str, factory: Callable[[], SourceT]) -> SourceT:
    """Run a provider factory until it succeeds or the startup budget ends."""
    deadline = time.monotonic() + STARTUP_TIMEOUT_SECONDS
    attempt = 0
    while True:
        attempt += 1
        try:
            return factory()
        except Exception as exc:
            if time.monotonic() >= deadline:
                raise RuntimeError(
                    f"{label} did not become ready within "
                    f"{STARTUP_TIMEOUT_SECONDS:.0f}s"
                ) from exc
            LOGGER.warning(
                "provider not ready; retrying",
                extra={"provider": label, "attempt": attempt},
            )
            time.sleep(STARTUP_RETRY_SECONDS)


def build_feature_source(settings: ModelApiSettings) -> FeatureSource:
    """Construct the configured feature provider or fail loudly."""
    if settings.feature_source == "parquet":
        return ParquetFeatureSource(settings.feature_path)
    if settings.feature_source == "feast":
        return _with_startup_retries(
            "feast",
            lambda: FeastFeatureSource.from_url(settings.redis_url),
        )
    raise ValueError(f"unsupported FEATURE_SOURCE={settings.feature_source}")


def build_model_source(settings: ModelApiSettings) -> ModelSource:
    """Construct the configured model provider or fail loudly."""
    if settings.model_source == "file":
        return JoblibModelSource(settings.model_path, version=settings.model_version)
    if settings.model_source == "mlflow":
        return _with_startup_retries(
            "mlflow",
            lambda: MlflowModelSource(
                settings.model_name,
                settings.model_version,
                settings.mlflow_tracking_uri,
            ),
        )
    raise ValueError(f"unsupported MODEL_SOURCE={settings.model_source}")

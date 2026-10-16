"""Tests for local file-backed provider implementations."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import joblib
import numpy as np
import pandas as pd
import pytest
from ml.data.schemas import ENTITY_COLUMN, FEATURE_COLUMNS
from model_api import deps as deps_module
from model_api.deps import ModelApiSettings, build_feature_source, build_model_source
from model_api.providers.stub import JoblibModelSource, ParquetFeatureSource
from sklearn.dummy import DummyClassifier


def feature_frame() -> pd.DataFrame:
    """Build two canonical feature rows."""
    return pd.DataFrame(
        {
            ENTITY_COLUMN: [1, 2],
            **{
                name: np.array([offset + 1, offset + 2], dtype="float64")
                for offset, name in enumerate(FEATURE_COLUMNS)
            },
        }
    )


def test_parquet_source_preserves_known_request_order(tmp_path: Path) -> None:
    """Unknown IDs are omitted for the API layer to report."""
    path = tmp_path / "features.parquet"
    feature_frame().to_parquet(path, index=False)
    source = ParquetFeatureSource(path)

    result = source.get([2, 999, 1])

    assert result.index.tolist() == [2, 1]
    assert result.columns.tolist() == FEATURE_COLUMNS
    assert source.ping() is True


def test_parquet_source_rejects_duplicate_entities(tmp_path: Path) -> None:
    """Serving files must contain one latest row per customer."""
    path = tmp_path / "duplicate.parquet"
    frame = feature_frame()
    frame.loc[1, ENTITY_COLUMN] = 1
    frame.to_parquet(path, index=False)
    with pytest.raises(ValueError, match="duplicate customer IDs"):
        ParquetFeatureSource(path)


def test_joblib_source_enforces_training_feature_order(tmp_path: Path) -> None:
    """The persisted estimator supplies its exact input schema."""
    frame = feature_frame().set_index(ENTITY_COLUMN)
    classifier = DummyClassifier(strategy="prior").fit(frame, [0, 1])
    path = tmp_path / "model.joblib"
    joblib.dump(classifier, path)

    source = JoblibModelSource(path, version="42")

    assert source.version == "42"
    assert source.feature_names == FEATURE_COLUMNS
    assert source.predict_proba(frame).shape == (2, 2)


def test_provider_factories_fail_loudly_for_unknown_modes() -> None:
    """Unsupported provider names cannot silently fall back to local files."""
    with pytest.raises(ValueError, match="FEATURE_SOURCE"):
        build_feature_source(ModelApiSettings(feature_source="unknown"))
    with pytest.raises(ValueError, match="MODEL_SOURCE"):
        build_model_source(ModelApiSettings(model_source="unknown"))


class _FailingClient:
    """Redis surface that always reports the store unreachable."""

    def ping(self) -> bool:
        """Raise like a refused connection."""
        from redis.exceptions import RedisError

        raise RedisError("connection refused")


def test_feast_source_ping_is_false_when_redis_is_down(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Readiness flips false while the online store is unreachable."""
    from model_api.providers.feast_features import FeastFeatureSource

    source = FeastFeatureSource(_FailingClient())  # type: ignore[arg-type]

    assert source.ping() is False


def test_startup_retries_then_fails_loudly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A provider that never becomes ready exhausts its budget and raises."""
    monkeypatch.setattr(deps_module, "STARTUP_TIMEOUT_SECONDS", 0.0)
    monkeypatch.setattr(deps_module, "STARTUP_RETRY_SECONDS", 0.0)

    def always_fails() -> Any:
        raise ConnectionError("store is down")

    with pytest.raises(RuntimeError, match="did not become ready"):
        deps_module._with_startup_retries("probe", always_fails)


def test_startup_retry_succeeds_after_transient_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One failure is tolerated when the provider recovers within budget."""
    monkeypatch.setattr(deps_module, "STARTUP_TIMEOUT_SECONDS", 30.0)
    monkeypatch.setattr(deps_module, "STARTUP_RETRY_SECONDS", 0.0)
    attempts = 0

    def flaky() -> str:
        nonlocal attempts
        attempts += 1
        if attempts < 2:
            raise ConnectionError("warming up")
        return "ready"

    assert deps_module._with_startup_retries("probe", flaky) == "ready"
    assert attempts == 2


class _FakeModelVersion:
    """Registry metadata returned for a pinned model version."""

    run_id = "run-123"
    creation_timestamp = 1_700_000_000_000


class _FakeMlflowClient:
    """MlflowClient surface limited to version metadata lookup."""

    def get_model_version(self, name: str, version: str) -> _FakeModelVersion:
        """Return canned metadata regardless of the requested version."""
        assert (name, version) == ("loan-default", "1")
        return _FakeModelVersion()


class _FakeSklearnModule:
    """Stand-in for ``mlflow.sklearn`` that returns a prebuilt estimator."""

    def __init__(self, estimator: object) -> None:
        """Bind the estimator to serve."""
        self._estimator = estimator

    def load_model(self, uri: str) -> object:
        """Return the prebuilt estimator for the pinned URI."""
        assert uri == "models:/loan-default/1"
        return self._estimator


class _FakeMlflowModule:
    """Stand-in for the ``mlflow`` package (no server needed)."""

    def __init__(self, estimator: object) -> None:
        """Bind the sklearn surface and tracking client."""
        self.sklearn = _FakeSklearnModule(estimator)
        self.tracking = type("tracking", (), {"MlflowClient": _FakeMlflowClient})

    def set_tracking_uri(self, uri: str) -> None:
        """Accept the tracking URI without connecting."""
        assert uri == "http://mlflow.test:5000"


def test_mlflow_source_loads_pinned_version(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The MLflow provider pins the version and exposes registry metadata."""
    import sys
    import types

    from model_api.providers.mlflow_model import MlflowModelSource

    frame = feature_frame().set_index(ENTITY_COLUMN)
    estimator = DummyClassifier(strategy="prior").fit(frame, [0, 1])
    fake = _FakeMlflowModule(estimator)
    module = types.ModuleType("mlflow")
    module.sklearn = fake.sklearn  # type: ignore[attr-defined]
    module.tracking = fake.tracking  # type: ignore[attr-defined]
    module.set_tracking_uri = fake.set_tracking_uri  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "mlflow", module)

    source = MlflowModelSource("loan-default", "1", "http://mlflow.test:5000")

    assert source.version == "1"
    assert source.feature_names == FEATURE_COLUMNS
    assert source.run_id == "run-123"
    assert source.registered_at.startswith("2023-11-14")
    assert source.predict_proba(frame).shape == (2, 2)

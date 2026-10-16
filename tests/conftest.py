"""Reusable fixtures for the consolidated test suite."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from model_api.deps import ModelApiSettings
from model_api.main import create_app

from tests.fakes import FakeFeatureSource, FakeModelSource


@pytest.fixture(scope="module")
def api_client() -> Iterator[TestClient]:
    """Run one application lifespan for the endpoint suite."""
    settings = ModelApiSettings(model_version="test-v7")
    with TestClient(
        create_app(
            settings,
            feature_source=FakeFeatureSource(),
            model_source=FakeModelSource(),
        )
    ) as client:
        yield client

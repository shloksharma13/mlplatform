"""Shared configuration for local-cluster end-to-end tests."""

from __future__ import annotations

import os

import pytest


def pytest_collection_modifyitems(
    config: pytest.Config, items: list[pytest.Item]
) -> None:
    """Skip tests that require Kubernetes during the regular unit-test run."""
    del config
    if os.environ.get("MLP_RUN_E2E") == "1":
        return
    skip = pytest.mark.skip(reason="set MLP_RUN_E2E=1 or run `make e2e`")
    for item in items:
        if "/tests/e2e/" in str(item.fspath):
            item.add_marker(skip)

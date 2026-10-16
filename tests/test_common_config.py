"""Tests for shared service configuration."""

import pytest
from mlp_common.config import ServiceSettings
from pydantic import ValidationError


def test_settings_load_case_insensitive_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Environment names map to lowercase settings fields."""
    monkeypatch.setenv("SERVICE_NAME", "unit-test")
    monkeypatch.setenv("LOG_LEVEL", "debug")

    settings = ServiceSettings()

    assert settings.service_name == "unit-test"
    assert settings.log_level == "DEBUG"


def test_settings_reject_unknown_log_level() -> None:
    """Invalid logging levels fail during settings construction."""
    with pytest.raises(ValidationError, match="unsupported log level"):
        ServiceSettings(log_level="verbose")

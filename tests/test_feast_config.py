"""Tests for the Feast configuration renderer (P6.2)."""

from __future__ import annotations

from pathlib import Path

import pytest
from feature_repo.render_config import REQUIRED_VARIABLES, render

REPO_ROOT = Path(__file__).resolve().parents[1]
_candidate_tmpl = REPO_ROOT / "core" / "feature_repo" / "feature_store.yaml.tmpl"
TEMPLATE = (
    _candidate_tmpl
    if _candidate_tmpl.exists()
    else REPO_ROOT / "feature_repo" / "feature_store.yaml.tmpl"
)

ENVIRON = {
    "POSTGRES_USER": "mlp",
    "POSTGRES_PASSWORD": "s3cret",
    "POSTGRES_HOST": "postgres.data.svc",
    "POSTGRES_PORT": "5432",
    "FEAST_POSTGRES_DB": "feast",
    "REDIS_HOST": "redis.data.svc",
    "REDIS_PORT": "6379",
}


def test_template_contains_placeholders_and_no_credentials() -> None:
    """Only placeholders are versioned; no credential literal may appear."""
    text = TEMPLATE.read_text(encoding="utf-8")
    assert "${POSTGRES_PASSWORD}" in text
    assert "s3cret" not in text
    assert "changeme" not in text


def test_render_builds_the_spec_configuration() -> None:
    """Rendered output matches P6.2: SQL registry, Redis online store, file offline."""
    rendered = render(TEMPLATE, environ=ENVIRON)
    assert "registry_type: sql" in rendered
    assert "postgresql+psycopg://mlp:s3cret@postgres.data.svc:5432/feast" in rendered
    assert "cache_ttl_seconds: 60" in rendered
    assert "type: redis" in rendered
    assert "connection_string: redis.data.svc:6379" in rendered
    assert "entity_key_serialization_version: 3" in rendered
    assert "${" not in rendered


def test_render_requires_every_variable() -> None:
    """A missing variable fails loudly instead of emitting a broken file."""
    for name in REQUIRED_VARIABLES:
        incomplete = {key: value for key, value in ENVIRON.items() if key != name}
        with pytest.raises(ValueError, match=name):
            render(TEMPLATE, environ=incomplete)

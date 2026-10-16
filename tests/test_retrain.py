"""Unit tests for retraining helpers (P8.4/P8.8)."""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import pandas as pd
import pytest
from ml.data.generate import generate_dataset, write_dataset
from ml.data.schemas import TIMESTAMP_COLUMN
from retrain import dispatch as dispatch_module
from retrain import register as register_module
from retrain.main import latest_label_window, post_parent_window


class _FakeRegistry:
    """In-memory stand-in for the MLflow registry client."""

    def __init__(self) -> None:
        """Start with no aliases and no tags."""
        self.aliases: dict[str, str] = {}
        self.tags: dict[tuple[str, str], dict[str, str]] = {}

    def get_model_version_by_alias(self, name: str, alias: str) -> object:
        """Return the aliased version or raise like MLflow does."""
        if alias not in self.aliases:
            raise ValueError("no such alias")
        record = type("Version", (), {})()
        record.version = self.aliases[alias]
        return record

    def delete_registered_model_alias(self, name: str, alias: str) -> None:
        """Remove one alias binding."""
        del self.aliases[alias]

    def set_registered_model_alias(self, name: str, alias: str, version: str) -> None:
        """Bind one alias."""
        self.aliases[alias] = version

    def set_model_version_tag(
        self, name: str, version: str, key: str, value: str
    ) -> None:
        """Attach one tag."""
        self.tags.setdefault((name, version), {})[key] = value


def test_set_alias_moves_stale_binding() -> None:
    """Promoting a version rebinds the alias instead of stacking it."""
    client = _FakeRegistry()

    register_module.set_alias(client, "loan-default", "champion", "1")
    register_module.set_alias(client, "loan-default", "champion", "2")

    assert client.aliases == {"champion": "2"}


def test_tag_version_records_trigger_metadata() -> None:
    """Trigger and parentage tags land on the new version."""
    client = _FakeRegistry()

    register_module.tag_version(
        client, "loan-default", "5", {"trigger": "drift", "parent_champion": "2"}
    )

    assert client.tags[("loan-default", "5")] == {
        "trigger": "drift",
        "parent_champion": "2",
    }


def test_latest_label_window_covers_sixty_days(tmp_path: Path) -> None:
    """The retraining window ends at the newest label and spans 60 days."""
    data_directory = tmp_path / "data"
    write_dataset(
        generate_dataset(customers=200, end_date=dt.date(2026, 10, 1)),
        data_directory,
    )

    window = latest_label_window(data_directory / "labels.parquet", window_days=60)

    assert window[TIMESTAMP_COLUMN].max() == pd.Timestamp("2026-10-01", tz="UTC")
    span = window[TIMESTAMP_COLUMN].max() - window[TIMESTAMP_COLUMN].min()
    assert span <= pd.Timedelta(days=60)


def test_post_parent_window_selects_only_new_labels() -> None:
    """Retraining sees one concept: everything past the parent's window."""
    frame = pd.DataFrame(
        {
            "customer_id": list(range(2000)) + list(range(2000)),
            TIMESTAMP_COLUMN: [pd.Timestamp("2026-10-01", tz="UTC")] * 2000
            + [pd.Timestamp("2026-10-03", tz="UTC")] * 2000,
            "default_next_90d": [0] * 4000,
        }
    )

    window = post_parent_window(frame, "2026-10-01 00:00:00+00:00")

    assert len(window) == 2000
    assert (window[TIMESTAMP_COLUMN] > pd.Timestamp("2026-10-01", tz="UTC")).all()


def test_post_parent_window_fails_loudly_when_empty() -> None:
    """No new labels since the parent means no retraining signal."""
    frame = pd.DataFrame(
        {
            "customer_id": [1, 2],
            TIMESTAMP_COLUMN: [pd.Timestamp("2026-10-01", tz="UTC")] * 2,
            "default_next_90d": [0, 1],
        }
    )

    with pytest.raises(ValueError, match="need 1000"):
        post_parent_window(frame, "2026-10-01 00:00:00+00:00")


def test_dispatch_github_mode_requires_credentials() -> None:
    """GitHub dispatch without repo/token fails loudly, never silently."""
    with pytest.raises(ValueError, match="GITHUB_REPO"):
        dispatch_module.dispatch_candidate(
            "9", trigger_mode="github", github_repo="", github_token=""
        )


def test_dispatch_local_runs_the_release_script(tmp_path: Path) -> None:
    """Local mode shells out to the release script with the version."""
    stub = tmp_path / "release.sh"
    stub.write_text('#!/usr/bin/env bash\necho "releasing $1"\n')
    stub.chmod(0o755)
    mode = dispatch_module.dispatch_candidate(
        "9", release_script=str(stub), workdir=None
    )

    assert mode == "local"

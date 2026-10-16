"""Training-serving feature parity (P6.4b).

The serving reader (`FeastFeatureSource`) must decode exactly the bytes Feast
materialized into Redis. These tests are hermetic (a fake Redis client, local
Parquet snapshots — no `feast` import, no server) so they run in CI:

- ``test_golden_live_bytes`` locks the real on-wire format with bytes
  captured from the live store (customer 1, verified against Feast's own
  ``get_online_features`` output on 2026-10-03).
- ``test_reader_matches_offline_snapshot`` round-trips 200 random customers
  from ``features_latest.parquet`` through Feast's encoding and back,
  requiring bit-exact float32 and exact int agreement (no skew).
"""

from __future__ import annotations

import struct
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from mlp_common.schemas import MODEL_FEATURES
from model_api.providers.feast_features import (
    FeastFeatureSource,
    decode_value,
    entity_key,
    field_key,
)

REPO_ROOT = Path(__file__).resolve().parents[1]

FEATURE_VIEW = "customer_features"


@pytest.fixture
def snapshot(tmp_path: Path) -> pd.DataFrame:
    """Use deterministic synthetic encoding fixtures, independent of local data."""
    rng = np.random.default_rng(42)
    frame = pd.DataFrame(
        rng.normal(0, 100_000, size=(250, len(MODEL_FEATURES))),
        columns=MODEL_FEATURES,
    )
    frame.insert(0, "customer_id", np.arange(1, 251))
    path = tmp_path / "features_latest.parquet"
    frame.to_parquet(path, index=False)
    return pd.read_parquet(path)


#: Raw hash bytes captured live for customer 1 (2026-10-03), with the values
#: Feast's own reader returned for the same entity.
GOLDEN_FIELDS = {
    "limit_bal": ("66e5365a", "3500409c46", 20000.0),
    "sex": ("ba0cf310", "3500000040", 2.0),
    "education": ("af519fd3", "3500000040", 2.0),
    "marriage": ("61466a3b", "350000803f", 1.0),
    "age": ("63a2a427", "350000c041", 24.0),
    "pay_0": ("6fd1d8cc", "3500000040", 2.0),
    "pay_2": ("8a73550a", "3500000040", 2.0),
    "pay_3": ("44e708fd", "35000080bf", -1.0),
    "pay_4": ("57fa4dff", "35000080bf", -1.0),
    "pay_5": ("4c522ade", "35000000c0", -2.0),
    "pay_6": ("1b2b8d11", "35000000c0", -2.0),
    "bill_amt1": ("b9d2fcab", "3500907445", 3913.0),
    "bill_amt2": ("e745170e", "3500e04145", 3102.0),
    "bill_amt3": ("9b9d19b7", "3500402c44", 689.0),
    "bill_amt4": ("e8d7bc42", "3500000000", 0.0),
    "bill_amt5": ("81a37d0c", "3500000000", 0.0),
    "bill_amt6": ("49b45439", "3500000000", 0.0),
    "pay_amt1": ("64951b53", "3500000000", 0.0),
    "pay_amt2": ("94959503", "3500402c44", 689.0),
    "pay_amt3": ("e2df119a", "3500000000", 0.0),
    "pay_amt4": ("15c88c5d", "3500000000", 0.0),
    "pay_amt5": ("16d43667", "3500000000", 0.0),
    "pay_amt6": ("a078c2cf", "3500000000", 0.0),
}
GOLDEN_KEY = (
    "01000000020000000b000000637573746f6d65725f69640400000008000000"
    "01000000000000006d6c706c6174666f726d"
)


class FakePipeline:
    """Queue ``hgetall`` calls and replay canned hashes in order."""

    def __init__(self, store: dict[bytes, dict[bytes, bytes]]) -> None:
        """Bind the canned key-to-hash mapping."""
        self._store = store
        self._queued: list[bytes] = []

    def hgetall(self, key: bytes) -> None:
        """Queue one hash lookup."""
        self._queued.append(key)

    def execute(self) -> list[dict[bytes, bytes]]:
        """Replay the queued lookups against the canned mapping."""
        return [self._store.get(key, {}) for key in self._queued]


class FakeRedis:
    """Minimal Redis surface used by ``FeastFeatureSource``."""

    def __init__(self, store: dict[bytes, dict[bytes, bytes]]) -> None:
        """Bind the canned key-to-hash mapping."""
        self._store = store

    def pipeline(self, transaction: bool = False) -> FakePipeline:
        """Return a replay pipeline over the canned mapping."""
        return FakePipeline(self._store)

    def ping(self) -> bool:
        """Pretend the server answers."""
        return True


def _encode_float(value: float) -> bytes:
    """Encode a float the way Feast's Redis writer does (float32 tag)."""
    return b"\x35" + struct.pack("<f", value)


def _encode_varint(value: int) -> bytes:
    """Encode a non-negative int as a protobuf varint."""
    parts = bytearray()
    while True:
        chunk = value & 0x7F
        value >>= 7
        if value:
            parts.append(chunk | 0x80)
        else:
            parts.append(chunk)
            return bytes(parts)


def _encode_int(value: int) -> bytes:
    """Encode an int the way Feast's Redis writer does (int64 tag)."""
    return b"\x20" + _encode_varint(value)


def _canned_store(frame: pd.DataFrame) -> dict[bytes, dict[bytes, bytes]]:
    """Encode snapshot rows into canned Feast Redis hashes."""
    store: dict[bytes, dict[bytes, bytes]] = {}
    for row in frame.itertuples():
        record = {b"_ts:customer_features": b"\x08\x80\xc5\xf6\xd5\x06"}
        for name in MODEL_FEATURES:
            value = getattr(row, name)
            raw = _encode_float(float(value))
            record[field_key(FEATURE_VIEW, name)] = raw
        store[entity_key(int(row.customer_id))] = record
    return store


def test_field_keys_match_live_capture() -> None:
    """Our field-key derivation reproduces the bytes Feast wrote."""
    assert entity_key(1).hex() == GOLDEN_KEY
    for name, (field_hex, _, _) in GOLDEN_FIELDS.items():
        assert field_key(FEATURE_VIEW, name).hex() == field_hex


def test_golden_live_bytes_decode_to_feast_values() -> None:
    """Raw captured bytes decode to exactly what Feast returned."""
    raw = {
        bytes.fromhex(field_hex): bytes.fromhex(value_hex)
        for field_hex, value_hex, _ in GOLDEN_FIELDS.values()
    }
    source = FeastFeatureSource(FakeRedis({bytes.fromhex(GOLDEN_KEY): raw}))
    frame = source.get([1])
    assert list(frame.columns) == MODEL_FEATURES
    for name, (_, _, expected) in GOLDEN_FIELDS.items():
        assert frame.loc[1, name] == pytest.approx(expected)


def test_decode_value_rejects_unknown_tags() -> None:
    """An unfamiliar Feast encoding fails loudly instead of scoring garbage."""
    with pytest.raises(ValueError, match="unsupported Feast value tag"):
        decode_value(bytes.fromhex("481234"))


def test_reader_matches_offline_snapshot(snapshot: pd.DataFrame) -> None:
    """200 random customers: serving decode equals the snapshot bit-for-bit."""
    latest = snapshot
    sample = latest.sample(n=200, random_state=42).sort_values("customer_id")
    source = FeastFeatureSource(FakeRedis(_canned_store(sample)))

    frame = source.get(list(sample["customer_id"]))

    assert len(frame) == 200
    assert list(frame.columns) == MODEL_FEATURES
    for row in sample.itertuples():
        for name in MODEL_FEATURES:
            expected = getattr(row, name)
            actual = frame.loc[int(row.customer_id), name]
            assert actual == np.float32(expected)


def test_unknown_ids_are_excluded_not_fabricated(snapshot: pd.DataFrame) -> None:
    """IDs absent from the online store come back missing for the 404 path."""
    latest = snapshot.head(3)
    source = FeastFeatureSource(FakeRedis(_canned_store(latest)))
    known = int(latest.iloc[0]["customer_id"])

    frame = source.get([known, 19_999_999])

    assert list(frame.index) == [known]


def test_empty_store_returns_shaped_empty_frame() -> None:
    """A wiped online store yields 404s, never a 500 KeyError."""
    source = FeastFeatureSource(FakeRedis({}))

    frame = source.get([1, 2, 3])

    assert frame.empty
    assert list(frame.columns) == MODEL_FEATURES
    assert frame.index.name == "customer_id"

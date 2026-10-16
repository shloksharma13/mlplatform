"""Feast online (Redis) feature provider (P6.5).

Reads the exact bytes Feast 0.66 writes to its Redis online store, without
importing ``feast`` (which requires ``pandas<3`` and conflicts with the
serving stack). Layout, verified byte-for-byte against the live store:

- Key (``entity_key_serialization_version=3``, single ``customer_id`` int64)::
      <I entity_count=1, <I STRING=2, <I len("customer_id"), name,
      <I INT64=4, <I 8, <q customer_id, project
- Fields: ``_mmh3("{feature_view}:{feature}")`` (murmur3 x86_32 of the
  qualified name, first 4 bytes little-endian) plus ``_ts:{view}``.
- Values: Feast ``Value``-proto encodings — float32 as tag ``0x35`` + 4
  little-endian bytes, int64 as tag ``0x20`` + varint. Anything else fails
  loudly so a future Feast layout change breaks visibly, never silently.
"""

from __future__ import annotations

import struct
from collections.abc import Mapping

import mmh3
import pandas as pd
from mlp_common.schemas import MODEL_FEATURES
from redis import Redis
from redis.exceptions import RedisError

ENTITY_COLUMN = "customer_id"
FEATURE_VIEW = "customer_features"
PROJECT = "mlplatform"

#: Feast ``Value``-proto tags this reader decodes (float32, float64, int32,
#: int64, bool, string). New tags must be added deliberately, never guessed.
_TAG_FLOAT32 = 0x35
_TAG_FLOAT64 = 0x41
_TAG_INT32 = 0x18
_TAG_INT64 = 0x20
_TAG_BOOL = 0x38
_TAG_STRING = 0x12


def _decode_varint(body: bytes) -> tuple[int, int]:
    """Decode a protobuf varint, returning (value, bytes consumed)."""
    result = 0
    shift = 0
    for consumed, byte in enumerate(body, start=1):
        result |= (byte & 0x7F) << shift
        if not byte & 0x80:
            return result, consumed
        shift += 7
        if shift >= 64:
            break
    raise ValueError(f"malformed varint: {body.hex()}")


def decode_value(raw: bytes) -> int | float | str | bool:
    """Decode one Feast ``Value``-proto byte string into a Python scalar."""
    if not raw:
        raise ValueError("empty Feast value")
    tag, body = raw[0], raw[1:]
    if tag == _TAG_FLOAT32:
        if len(body) != 4:
            raise ValueError(f"bad float32 length: {raw.hex()}")
        return struct.unpack("<f", body)[0]
    if tag == _TAG_FLOAT64:
        if len(body) != 8:
            raise ValueError(f"bad float64 length: {raw.hex()}")
        return struct.unpack("<d", body)[0]
    if tag in (_TAG_INT32, _TAG_INT64):
        value, consumed = _decode_varint(body)
        if consumed != len(body):
            raise ValueError(f"trailing bytes in int value: {raw.hex()}")
        return value
    if tag == _TAG_BOOL:
        value, consumed = _decode_varint(body)
        if consumed != len(body):
            raise ValueError(f"trailing bytes in bool value: {raw.hex()}")
        return bool(value)
    if tag == _TAG_STRING:
        length, consumed = _decode_varint(body)
        text = body[consumed : consumed + length]
        if len(text) != length:
            raise ValueError(f"truncated string value: {raw.hex()}")
        return text.decode("utf-8")
    raise ValueError(f"unsupported Feast value tag {tag:#x}: {raw.hex()}")


def entity_key(customer_id: int, project: str = PROJECT) -> bytes:
    """Build the exact Redis key Feast uses for one customer entity."""
    name = ENTITY_COLUMN.encode("utf-8")
    return (
        struct.pack("<I", 1)
        + struct.pack("<I", 2)
        + struct.pack("<I", len(name))
        + name
        + struct.pack("<I", 4)
        + struct.pack("<I", 8)
        + struct.pack("<q", customer_id)
        + project.encode("utf-8")
    )


def field_key(feature_view: str, feature: str) -> bytes:
    """Build the exact Redis hash field Feast uses for one feature."""
    return struct.pack("<I", mmh3.hash(f"{feature_view}:{feature}", signed=False))


class FeastFeatureSource:
    """Serve features from the Feast Redis online store via a raw client."""

    def __init__(
        self,
        client: Redis,
        *,
        project: str = PROJECT,
        feature_view: str = FEATURE_VIEW,
        features: list[str] | None = None,
    ) -> None:
        """Bind a client; the caller owns connection configuration."""
        self._client = client
        self._project = project
        self._feature_view = feature_view
        self._features = (
            list(features) if features is not None else list(MODEL_FEATURES)
        )
        self._fields = {name: field_key(feature_view, name) for name in self._features}

    @classmethod
    def from_url(
        cls,
        url: str,
        *,
        timeout_seconds: float = 2.0,
        **kwargs: object,
    ) -> FeastFeatureSource:
        """Connect to Redis from a URL (``redis://host:port/db``)."""
        client = Redis.from_url(
            url,
            socket_timeout=timeout_seconds,
            socket_connect_timeout=timeout_seconds,
        )
        return cls(client, **kwargs)

    @property
    def feature_names(self) -> list[str]:
        """Return the canonical feature order served by this source."""
        return list(self._features)

    def get(self, ids: list[int]) -> pd.DataFrame:
        """Return known IDs indexed by customer ID in canonical order."""
        pipe = self._client.pipeline(transaction=False)
        for customer_id in ids:
            pipe.hgetall(entity_key(customer_id, self._project))
        rows: dict[int, dict[str, object]] = {}
        for customer_id, raw in zip(ids, pipe.execute(), strict=True):
            if not raw:
                continue
            rows[customer_id] = self._decode_record(customer_id, raw)
        known_ids = [customer_id for customer_id in ids if customer_id in rows]
        if not known_ids:
            empty = pd.DataFrame(columns=self._features)
            empty.index.name = ENTITY_COLUMN
            return empty
        frame = pd.DataFrame.from_dict(rows, orient="index")
        frame.index.name = ENTITY_COLUMN
        return frame.loc[known_ids, self._features].copy()

    def _decode_record(
        self, customer_id: int, raw: Mapping[bytes, bytes]
    ) -> dict[str, object]:
        """Decode one entity hash, failing loudly on missing fields."""
        record: dict[str, object] = {}
        for name in self._features:
            field = self._fields[name]
            if field not in raw:
                raise ValueError(
                    f"online store is missing {name!r} for customer {customer_id}"
                )
            record[name] = decode_value(raw[field])
        return record

    def ping(self) -> bool:
        """Return whether the Redis online store currently answers."""
        try:
            return bool(self._client.ping())
        except RedisError:
            return False

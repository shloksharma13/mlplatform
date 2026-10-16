"""Tests for bounded, asynchronous prediction logging."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

from model_api.metrics import create_metrics
from model_api.predlog import PredictionLogger, PredictionRecord


def make_record(customer_id: int = 1) -> PredictionRecord:
    """Build one stable prediction record."""
    return PredictionRecord(
        ts=datetime(2026, 10, 2, tzinfo=UTC),
        request_id="request-1",
        model_version="1",
        customer_id=customer_id,
        features={"limit_bal": 20000.0},
        score=0.25,
    )


def test_record_serializes_features_for_jsonb() -> None:
    """Database mappings encode feature dictionaries as compact JSON."""
    mapping = make_record().database_mapping()
    assert mapping["features"] == '{"limit_bal":20000.0}'


def test_enqueue_never_blocks_and_rejects_when_full() -> None:
    """The request path receives an immediate false result at capacity."""
    logger = PredictionLogger(None, create_metrics(), max_size=2)
    assert logger.enqueue(make_record(1)) is True
    assert logger.enqueue(make_record(2)) is True
    assert logger.enqueue(make_record(3)) is False
    assert logger.size == 2


def test_no_database_worker_flushes_and_stops() -> None:
    """No-DB mode drains a full batch and terminates cleanly."""

    async def scenario() -> None:
        logger = PredictionLogger(
            None,
            create_metrics(),
            max_size=10,
            batch_size=2,
            flush_interval=0.01,
        )
        logger.start()
        logger.enqueue(make_record(1))
        logger.enqueue(make_record(2))
        await asyncio.sleep(0.02)
        assert logger.size == 0
        await logger.stop()
        assert logger._task is None

    asyncio.run(scenario())

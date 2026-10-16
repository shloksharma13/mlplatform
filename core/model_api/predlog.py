"""Bounded asynchronous prediction logging with optional PostgreSQL delivery."""

from __future__ import annotations

import asyncio
import json
import logging
from contextlib import suppress
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import Engine, create_engine, text

from model_api.metrics import ApiMetrics

LOGGER = logging.getLogger(__name__)

INSERT_PREDICTION = text(
    """
    INSERT INTO prediction_log
        (ts, request_id, model_version, customer_id, features, score)
    VALUES
        (:ts, :request_id, :model_version, :customer_id,
         CAST(:features AS JSONB), :score)
    """
)


@dataclass(frozen=True)
class PredictionRecord:
    """One auditable model decision queued after its response is computed."""

    ts: datetime
    request_id: str
    model_version: str
    customer_id: int
    features: dict[str, Any]
    score: float

    def database_mapping(self) -> dict[str, Any]:
        """Return a SQLAlchemy mapping with JSON serialized explicitly."""
        mapping = asdict(self)
        mapping["features"] = json.dumps(self.features, separators=(",", ":"))
        return mapping


class PredictionLogger:
    """Flush a bounded queue in the background without delaying predictions."""

    def __init__(
        self,
        database_url: str | None,
        metrics: ApiMetrics,
        *,
        max_size: int = 10_000,
        batch_size: int = 200,
        flush_interval: float = 2.0,
    ) -> None:
        """Configure buffering and create a lazy database engine when requested."""
        self._queue: asyncio.Queue[PredictionRecord | None] = asyncio.Queue(
            maxsize=max_size
        )
        self._metrics = metrics
        self._batch_size = batch_size
        self._flush_interval = flush_interval
        self._engine: Engine | None = (
            create_engine(database_url, pool_pre_ping=True) if database_url else None
        )
        self._task: asyncio.Task[None] | None = None
        self._stopping = asyncio.Event()

    @property
    def size(self) -> int:
        """Return the current number of buffered rows."""
        return self._queue.qsize()

    def start(self) -> None:
        """Start the single background flush worker."""
        if self._task is None:
            self._task = asyncio.create_task(self._run(), name="prediction-log-flush")

    async def stop(self) -> None:
        """Drain buffered rows and stop the worker during graceful shutdown."""
        self._stopping.set()
        with suppress(asyncio.QueueFull):
            self._queue.put_nowait(None)
        if self._task is not None:
            await self._task
            self._task = None
        if self._engine is not None:
            await asyncio.to_thread(self._engine.dispose)

    def enqueue(self, record: PredictionRecord) -> bool:
        """Queue a record immediately, returning false if the buffer is full."""
        try:
            self._queue.put_nowait(record)
        except asyncio.QueueFull:
            LOGGER.error(
                "prediction log buffer full; dropping row",
                extra={"customer_id": record.customer_id},
            )
            return False
        self._metrics.predlog_buffer_size.set(self._queue.qsize())
        return True

    async def _run(self) -> None:
        """Collect timed batches, retry failures, and drain on shutdown."""
        backoff_seconds = 1.0
        while not self._stopping.is_set() or not self._queue.empty():
            batch = await self._next_batch()
            if not batch:
                continue
            try:
                await self._flush(batch)
                backoff_seconds = 1.0
            except Exception:
                self._metrics.predlog_flush_failures.inc()
                LOGGER.exception(
                    "prediction log flush failed", extra={"rows": len(batch)}
                )
                if not self._stopping.is_set():
                    self._requeue(batch)
                else:
                    LOGGER.error(
                        "dropping prediction rows during failed shutdown flush",
                        extra={"rows": len(batch)},
                    )
                with suppress(TimeoutError):
                    await asyncio.wait_for(
                        self._stopping.wait(), timeout=backoff_seconds
                    )
                backoff_seconds = min(backoff_seconds * 2, 30.0)
            finally:
                self._metrics.predlog_buffer_size.set(self._queue.qsize())

    async def _next_batch(self) -> list[PredictionRecord]:
        """Flush when 200 rows arrive or two seconds pass after the first row."""
        try:
            first = await asyncio.wait_for(
                self._queue.get(), timeout=self._flush_interval
            )
        except TimeoutError:
            return []
        if first is None:
            return []
        batch = [first]
        deadline = asyncio.get_running_loop().time() + self._flush_interval
        while len(batch) < self._batch_size:
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                break
            try:
                record = await asyncio.wait_for(self._queue.get(), remaining)
                if record is None:
                    break
                batch.append(record)
            except TimeoutError:
                break
        return batch

    async def _flush(self, records: list[PredictionRecord]) -> None:
        """Write a batch to PostgreSQL or structured stdout in no-DB mode."""
        if self._engine is None:
            for record in records:
                LOGGER.info("prediction", extra=asdict(record))
            return
        await asyncio.to_thread(self._flush_sync, records)

    def _flush_sync(self, records: list[PredictionRecord]) -> None:
        """Execute one atomic PostgreSQL batch outside the event loop."""
        if self._engine is None:
            raise RuntimeError("database engine is not configured")
        with self._engine.begin() as connection:
            connection.execute(
                INSERT_PREDICTION,
                [record.database_mapping() for record in records],
            )

    def _requeue(self, records: list[PredictionRecord]) -> None:
        """Return failed rows to the queue without ever blocking the worker."""
        for record in records:
            try:
                self._queue.put_nowait(record)
            except asyncio.QueueFull:
                LOGGER.error(
                    "prediction log retry buffer full; dropping row",
                    extra={"customer_id": record.customer_id},
                )

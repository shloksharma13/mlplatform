"""Explicitly opt-in local failure controls used by resilience demonstrations."""

from __future__ import annotations

import asyncio
import os


class ChaosController:
    """Hold pod-local artificial latency state."""

    def __init__(self) -> None:
        """Start with latency injection disabled."""
        self._latency_ms = 0

    @property
    def latency_ms(self) -> int:
        """Return the current artificial delay in milliseconds."""
        return self._latency_ms

    def set_latency(self, milliseconds: int) -> None:
        """Set a non-negative artificial prediction delay."""
        if milliseconds < 0:
            raise ValueError("latency must be non-negative")
        self._latency_ms = milliseconds

    async def delay(self) -> None:
        """Sleep for the configured delay without blocking the event loop."""
        if self._latency_ms:
            await asyncio.sleep(self._latency_ms / 1_000)


def crash_process() -> None:
    """Terminate the current pod immediately for a Kubernetes restart drill."""
    os._exit(1)

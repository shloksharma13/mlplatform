"""Async prediction traffic generator for load and chaos drills.

Sends POST /predict batches at a target rate, then prints a latency summary.
Exits non-zero when the observed error rate exceeds --max-error-rate.
"""

from __future__ import annotations

import argparse
import asyncio
import random
import statistics
import sys
import time

import httpx

MAX_CUSTOMER_ID = 19999


async def one_batch(
    client: httpx.AsyncClient, url: str, rng: random.Random, out: list
) -> None:
    """Send one 5-id batch; append (latency_s, ok) to out."""
    ids = rng.sample(range(MAX_CUSTOMER_ID + 1), 5)
    started = time.perf_counter()
    try:
        response = await client.post(
            f"{url}/predict", json={"customer_ids": ids}, timeout=10.0
        )
        ok = response.status_code == 200
    except httpx.HTTPError:
        ok = False
    out.append((time.perf_counter() - started, ok))


async def run(rps: int, duration: int, url: str, seed: int) -> list[tuple[float, bool]]:
    results: list[tuple[float, bool]] = []
    interval = 1.0 / rps
    async with httpx.AsyncClient() as client:
        deadline = time.perf_counter() + duration
        rng = random.Random(seed)
        while time.perf_counter() < deadline:
            tick = time.perf_counter()
            await one_batch(client, url, rng, results)
            delay = interval - (time.perf_counter() - tick)
            if delay > 0:
                await asyncio.sleep(delay)
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rps", type=int, default=10)
    parser.add_argument("--duration", type=int, default=30, help="seconds")
    parser.add_argument("--pool", choices=["baseline", "drifted"], default="baseline")
    parser.add_argument("--url", default="http://localhost:8080")
    parser.add_argument("--max-error-rate", type=float, default=0.01)
    parser.add_argument("--seed", type=int, default=7)
    args = parser.parse_args()

    results = asyncio.run(run(args.rps, args.duration, args.url, args.seed))
    latencies = sorted(latency for latency, _ in results)
    errors = sum(1 for _, ok in results if not ok)
    error_rate = errors / len(results) if results else 1.0

    def pct(p: float) -> float:
        return statistics.quantiles(latencies, n=100)[int(p) - 1] if latencies else 0.0

    print(
        f"traffic: n={len(results)} errors={errors} "
        f"error_rate={error_rate:.4f} "
        f"p50={pct(50):.3f}s p95={pct(95):.3f}s "
        f"max={max(latencies) if latencies else 0:.3f}s"
    )
    if error_rate > args.max_error_rate:
        print(
            f"traffic FAIL: error_rate {error_rate:.4f} > {args.max_error_rate}",
            file=sys.stderr,
        )
        return 1
    print("traffic PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

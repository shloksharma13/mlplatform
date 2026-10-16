#!/usr/bin/env python3
"""Staging load check (P7.2): sustained requests must stay fast and clean.

Sends ``--total`` POST /predict requests at ``--rps`` to ``--url`` and fails
when p95 latency exceeds 150ms or the error rate exceeds 0.5% (SPEC P7.2).
Prints a JSON summary for release job tables. Standard library only so it
runs on the runner with no extra installs.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import random
import sys
import time
import urllib.request

P95_BUDGET_MS = 150.0
ERROR_BUDGET = 0.005


def _one(url: str, customer_id: int, timeout: float) -> tuple[float, bool]:
    """Send one prediction request; return (latency_ms, ok)."""
    body = json.dumps({"customer_ids": [customer_id]}).encode()
    started = time.perf_counter()
    try:
        request = urllib.request.Request(
            f"{url}/predict", data=body, headers={"content-type": "application/json"}
        )
        with urllib.request.urlopen(request, timeout=timeout) as response:
            ok = response.status == 200
    except Exception:
        ok = False
    return (time.perf_counter() - started) * 1000.0, ok


def run_loadcheck(url: str, total: int, rps: float, seed: int = 7) -> dict[str, float]:
    """Fire ``total`` requests at ``rps`` and summarize latency/errors."""
    rng = random.Random(seed)
    ids = [rng.randint(1, 30000) for _ in range(total)]
    latencies: list[float] = []
    errors = 0
    deadline = time.perf_counter()
    with concurrent.futures.ThreadPoolExecutor(max_workers=64) as pool:
        futures = []
        for customer_id in ids:
            deadline += 1.0 / rps
            futures.append(pool.submit(_one, url, customer_id, 10.0))
            delay = deadline - time.perf_counter()
            if delay > 0:
                time.sleep(delay)
        for future in concurrent.futures.as_completed(futures):
            latency_ms, ok = future.result()
            latencies.append(latency_ms)
            errors += not ok
    latencies.sort()
    p50 = latencies[len(latencies) // 2] if latencies else 0.0
    p95 = latencies[min(len(latencies) - 1, int(len(latencies) * 0.95))]
    return {
        "total": float(total),
        "error_rate": errors / total if total else 1.0,
        "p50_ms": p50,
        "p95_ms": p95,
    }


def parse_args() -> argparse.Namespace:
    """Parse load-check CLI options."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://localhost:8081")
    parser.add_argument("--total", type=int, default=2000)
    parser.add_argument("--rps", type=float, default=20.0)
    return parser.parse_args()


def main() -> int:
    """Run the check and exit 0 inside both budgets, 1 outside."""
    args = parse_args()
    summary = run_loadcheck(args.url, args.total, args.rps)
    print(json.dumps(summary, indent=2))
    ok = summary["p95_ms"] <= P95_BUDGET_MS and summary["error_rate"] <= ERROR_BUDGET
    print(f"LOADCHECK verdict={'PASS' if ok else 'FAIL'}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

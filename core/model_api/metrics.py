"""Per-application Prometheus collectors for model serving."""

from __future__ import annotations

from dataclasses import dataclass

from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram


@dataclass(frozen=True)
class ApiMetrics:
    """All metric collectors owned by one FastAPI application instance."""

    registry: CollectorRegistry
    requests: Counter
    request_duration: Histogram
    predictions: Counter
    model: Gauge
    feature_fetch_duration: Histogram
    predlog_buffer_size: Gauge
    predlog_flush_failures: Counter


def create_metrics() -> ApiMetrics:
    """Create an isolated registry so test applications never collide."""
    registry = CollectorRegistry()
    return ApiMetrics(
        registry=registry,
        requests=Counter(
            "http_requests_total",
            "HTTP requests handled by the model API.",
            ["method", "path", "status"],
            registry=registry,
        ),
        request_duration=Histogram(
            "http_request_duration_seconds",
            "HTTP request latency in seconds.",
            ["method", "path"],
            buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5),
            registry=registry,
        ),
        predictions=Counter(
            "predictions_total",
            "Predictions returned by model version and label.",
            ["model_version", "label"],
            registry=registry,
        ),
        model=Gauge(
            "model_info",
            "Loaded immutable model identity.",
            ["model_name", "model_version"],
            registry=registry,
        ),
        feature_fetch_duration=Histogram(
            "feature_fetch_duration_seconds",
            "Feature retrieval latency in seconds.",
            registry=registry,
        ),
        predlog_buffer_size=Gauge(
            "predlog_buffer_size",
            "Rows waiting in the asynchronous prediction log buffer.",
            registry=registry,
        ),
        predlog_flush_failures=Counter(
            "predlog_flush_failures_total",
            "Failed prediction log flush attempts.",
            registry=registry,
        ),
    )

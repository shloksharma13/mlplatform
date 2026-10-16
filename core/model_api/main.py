"""FastAPI application serving the immutable loan-default model."""

from __future__ import annotations

import logging
import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime

from fastapi import BackgroundTasks, FastAPI, Query, Request
from fastapi.responses import JSONResponse, Response
from mlp_common.logging import bind_request_id, configure_logging, reset_request_id
from mlp_common.schemas import (
    HealthResponse,
    ModelInfo,
    Prediction,
    PredictRequest,
    PredictResponse,
)
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from model_api.chaos import ChaosController, crash_process
from model_api.deps import (
    ModelApiSettings,
    build_feature_source,
    build_model_source,
)
from model_api.metrics import create_metrics
from model_api.predlog import PredictionLogger, PredictionRecord
from model_api.providers.base import FeatureSource, ModelSource

LOGGER = logging.getLogger(__name__)


@dataclass
class RuntimeState:
    """Mutable providers and metadata owned by one application process."""

    feature_source: FeatureSource | None = None
    model_source: ModelSource | None = None
    loaded_at: datetime | None = None
    prediction_logger: PredictionLogger | None = None


def create_app(
    settings: ModelApiSettings | None = None,
    *,
    feature_source: FeatureSource | None = None,
    model_source: ModelSource | None = None,
) -> FastAPI:
    """Build an application with injectable providers for isolated tests."""
    config = settings or ModelApiSettings()
    configure_logging(config.service_name, config.log_level)
    metrics = create_metrics()
    runtime = RuntimeState()
    chaos = ChaosController()

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        runtime.feature_source = feature_source or build_feature_source(config)
        runtime.model_source = model_source or build_model_source(config)
        runtime.loaded_at = datetime.now(UTC)
        runtime.prediction_logger = PredictionLogger(config.database_url, metrics)
        runtime.prediction_logger.start()
        metrics.model.labels(
            model_name=config.model_name,
            model_version=runtime.model_source.version,
        ).set(1)
        LOGGER.info(
            "model loaded", extra={"model_version": runtime.model_source.version}
        )
        try:
            yield
        finally:
            await runtime.prediction_logger.stop()

    application = FastAPI(
        title="MLPlatform model API", version="0.1.0", lifespan=lifespan
    )
    application.state.runtime = runtime
    application.state.metrics = metrics
    application.state.settings = config
    application.state.chaos = chaos

    @application.middleware("http")
    async def observe_request(request: Request, call_next):  # type: ignore[no-untyped-def]
        request_id = request.headers.get("x-request-id", str(uuid.uuid4()))
        request.state.request_id = request_id
        token = bind_request_id(request_id)
        started = time.perf_counter()
        status = 500
        path = request.url.path
        try:
            response = await call_next(request)
            status = response.status_code
            response.headers["x-request-id"] = request_id
            return response
        finally:
            elapsed = time.perf_counter() - started
            metrics.requests.labels(request.method, path, str(status)).inc()
            metrics.request_duration.labels(request.method, path).observe(elapsed)
            LOGGER.info(
                "request complete",
                extra={"method": request.method, "path": path, "status": status},
            )
            reset_request_id(token)

    @application.get("/healthz", response_model=HealthResponse)
    async def healthz() -> HealthResponse:
        return HealthResponse(status="ok")

    @application.get("/readyz", response_model=HealthResponse)
    async def readyz() -> HealthResponse | JSONResponse:
        ready = (
            runtime.model_source is not None
            and runtime.feature_source is not None
            and runtime.feature_source.ping()
        )
        if not ready:
            return JSONResponse(status_code=503, content={"status": "not_ready"})
        return HealthResponse(status="ok")

    @application.get("/metrics")
    async def prometheus_metrics() -> Response:
        return Response(
            content=generate_latest(metrics.registry), media_type=CONTENT_TYPE_LATEST
        )

    @application.get("/model-info", response_model=ModelInfo)
    async def model_info() -> ModelInfo | JSONResponse:
        if runtime.model_source is None or runtime.loaded_at is None:
            return JSONResponse(status_code=503, content={"status": "not_ready"})
        return ModelInfo(
            name=config.model_name,
            version=runtime.model_source.version,
            source=config.model_source,
            loaded_at=runtime.loaded_at,
            features=runtime.model_source.feature_names,
            mlflow_run_id=getattr(runtime.model_source, "run_id", None),
            feature_service=(
                config.feature_service if config.feature_source == "feast" else None
            ),
            registered_at=getattr(runtime.model_source, "registered_at", None),
        )

    @application.post("/predict", response_model=PredictResponse)
    async def predict(payload: PredictRequest, request: Request) -> PredictResponse:
        # P4.5 trivial change (branch-to-deployment demo): no behavior change.
        if runtime.model_source is None or runtime.feature_source is None:
            return JSONResponse(status_code=503, content={"status": "not_ready"})
        await chaos.delay()
        with metrics.feature_fetch_duration.time():
            features = runtime.feature_source.get(payload.customer_ids)
        unknown = [
            customer_id
            for customer_id in payload.customer_ids
            if customer_id not in features.index
        ]
        if unknown:
            return JSONResponse(
                status_code=404,
                content={"error": "unknown_customers", "ids": unknown},
            )
        probabilities = runtime.model_source.predict_proba(features)[:, 1]
        predictions = []
        for customer_id, probability in zip(
            payload.customer_ids, probabilities, strict=True
        ):
            label = int(probability >= 0.5)
            metrics.predictions.labels(runtime.model_source.version, str(label)).inc()
            predictions.append(
                Prediction(
                    customer_id=customer_id,
                    default_probability=float(probability),
                    label=label,
                )
            )
            if runtime.prediction_logger is not None:
                runtime.prediction_logger.enqueue(
                    PredictionRecord(
                        ts=datetime.now(UTC),
                        request_id=request.state.request_id,
                        model_version=runtime.model_source.version,
                        customer_id=customer_id,
                        features={
                            name: _json_number(features.loc[customer_id, name])
                            for name in runtime.model_source.feature_names
                        },
                        score=float(probability),
                    )
                )
        return PredictResponse(
            request_id=request.state.request_id,
            model_version=runtime.model_source.version,
            predictions=predictions,
        )

    if config.enable_chaos:

        @application.post("/debug/crash", status_code=202)
        async def debug_crash(background_tasks: BackgroundTasks) -> dict[str, str]:
            background_tasks.add_task(crash_process)
            return {"status": "crashing"}

        @application.post("/debug/latency")
        async def debug_latency(ms: int = Query(ge=0, le=30_000)) -> dict[str, int]:
            chaos.set_latency(ms)
            return {"latency_ms": chaos.latency_ms}

    return application


def _json_number(value: object) -> int | float:
    """Convert NumPy/Pandas numeric scalars into JSON-compatible primitives."""
    item_method = getattr(value, "item", None)
    if callable(item_method):
        value = item_method()
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int | float):
        return value
    raise TypeError(f"feature value is not numeric: {value!r}")


app = create_app()

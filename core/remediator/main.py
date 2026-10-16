"""Alert remediation webhook (P5.6).

Receives Alertmanager webhook v4 JSON at POST /alert and executes the action
map. Idempotent via per-fingerprint debounce (2 min). Exposes Prometheus
metrics at /metrics and liveness/readiness probes.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from fastapi import FastAPI, Request, Response
from kubernetes import client as k8s_client
from kubernetes import config as k8s_config
from mlp_common.logging import configure_logging
from prometheus_client import (
    CONTENT_TYPE_LATEST,
    CollectorRegistry,
    Counter,
    generate_latest,
)
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from remediator import actions as remediation

LOGGER = logging.getLogger(__name__)


class RemediatorSettings(BaseSettings):
    """Environment contract for the remediator."""

    model_config = SettingsConfigDict(
        env_file=".env", extra="ignore", case_sensitive=False
    )

    service_name: str = "remediator"
    log_level: str = "INFO"
    grafana_url: str = "http://kube-prometheus-stack-grafana.observability.svc:80"
    grafana_user: str = "admin"
    grafana_password: str = ""


class Alert(BaseModel):
    """Subset of an Alertmanager v4 alert used for routing."""

    status: str = "firing"
    fingerprint: str = ""
    labels: dict[str, str] = Field(default_factory=dict)
    annotations: dict[str, str] = Field(default_factory=dict)


class Webhook(BaseModel):
    """Alertmanager webhook v4 payload."""

    alerts: list[Alert] = Field(default_factory=list)


def load_k8s_clients() -> tuple[k8s_client.AppsV1Api, k8s_client.AutoscalingV2Api]:
    """Load in-cluster config (fall back to kubeconfig for local dev)."""
    try:
        k8s_config.load_incluster_config()
    except k8s_config.ConfigException:
        k8s_config.load_kube_config()
    api = k8s_client.ApiClient()
    return k8s_client.AppsV1Api(api), k8s_client.AutoscalingV2Api(api)


def create_app(
    settings: RemediatorSettings | None = None,
    apps_api: k8s_client.AppsV1Api | None = None,
    autoscaling_api: k8s_client.AutoscalingV2Api | None = None,
) -> FastAPI:
    """Build the remediator application with injectable clients for tests."""
    config = settings or RemediatorSettings()
    configure_logging(config.service_name, config.log_level)

    registry = CollectorRegistry()
    actions_total = Counter(
        "remediator_actions_total",
        "Remediation actions executed by alert, action, and result.",
        ["alert", "action", "result"],
        registry=registry,
    )

    seen: dict[str, float] = {}
    clients: dict[str, Any] = {"apps": apps_api, "autoscaling": autoscaling_api}

    def get_clients() -> tuple[k8s_client.AppsV1Api, k8s_client.AutoscalingV2Api]:
        if clients["apps"] is None or clients["autoscaling"] is None:
            loaded_apps, loaded_autoscaling = load_k8s_clients()
            clients["apps"] = clients["apps"] or loaded_apps
            clients["autoscaling"] = clients["autoscaling"] or loaded_autoscaling
        return clients["apps"], clients["autoscaling"]

    def annotate(text: str, tags: list[str]) -> str:
        try:
            annotation_id = remediation.create_grafana_annotation(
                config.grafana_url,
                config.grafana_user,
                config.grafana_password,
                text,
                tags,
            )
            return f"annotated:{annotation_id}"
        except Exception as exc:
            LOGGER.warning("grafana annotation failed", extra={"error": str(exc)})
            return "annotate_failed"

    def handle(alert: Alert) -> tuple[str, str]:
        """Execute the action map; return (action, result)."""
        name = alert.labels.get("alertname", "unknown")
        fingerprint = alert.fingerprint or f"{name}:{alert.status}"
        now = time.monotonic()
        previous = seen.get(fingerprint)
        if previous is not None and now - previous < remediation.DEBOUNCE_SECONDS:
            return "debounced", "skipped"
        seen[fingerprint] = now

        apps_api_client, autoscaling_api_client = get_clients()
        summary = alert.annotations.get("summary", name)

        if name == "HighP95Latency" and alert.status == "firing":
            try:
                outcome = remediation.patch_hpa_min_replicas(autoscaling_api_client)
                old, new = outcome["old_min"], outcome["new_min"]
                annotation = annotate(
                    f"HighP95Latency: HPA floor {old}→{new}",
                    ["remediator", "HighP95Latency"],
                )
                return "scale_hpa_floor", f"min={outcome['new_min']};{annotation}"
            except Exception as exc:
                LOGGER.error("hpa patch failed", extra={"error": str(exc)})
                return "scale_hpa_floor", "failed"
        if name in ("PodCrashLooping", "ModelApiDown"):
            annotation = annotate(f"{name}: {summary}", ["remediator", name])
            if name == "ModelApiDown" and alert.status == "firing":
                try:
                    stamp = remediation.rollout_restart_deployment(apps_api_client)
                    return "restart_and_annotate", f"restarted_at={stamp};{annotation}"
                except Exception as exc:
                    LOGGER.error("rollout restart failed", extra={"error": str(exc)})
                    return "restart_and_annotate", f"restart_failed;{annotation}"
            return "annotate", annotation
        if name == "ModelDriftDetected":
            # Retraining is already automatic (P8); annotation only.
            return "annotate", annotate(f"{name}: {summary}", ["remediator", name])
        LOGGER.info(
            "no action for alert", extra={"alert": name, "status": alert.status}
        )
        return "none", "ignored"

    application = FastAPI(title="MLPlatform remediator", version="0.1.0")

    @application.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @application.get("/readyz")
    async def readyz() -> dict[str, str]:
        return {"status": "ready"}

    @application.get("/metrics")
    async def prometheus_metrics() -> Response:
        return Response(
            content=generate_latest(registry), media_type=CONTENT_TYPE_LATEST
        )

    @application.post("/alert")
    async def alert(webhook: Webhook, request: Request) -> dict[str, Any]:
        request_id = request.headers.get("x-request-id", "-")
        outcomes = []
        for item in webhook.alerts:
            action, result = handle(item)
            actions_total.labels(
                alert=item.labels.get("alertname", "unknown"),
                action=action,
                result=result.split(";")[0].split("=")[0][:32] or result[:32],
            ).inc()
            LOGGER.info(
                "alert handled",
                extra={
                    "request_id": request_id,
                    "alert": item.labels.get("alertname"),
                    "action": action,
                    "result": result,
                },
            )
            outcomes.append(
                {
                    "alert": item.labels.get("alertname"),
                    "action": action,
                    "result": result,
                }
            )
        return {"handled": len(outcomes), "outcomes": outcomes}

    return application


app = create_app()

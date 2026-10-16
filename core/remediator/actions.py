"""Kubernetes and Grafana actions executed by the remediator (P5.6)."""

from __future__ import annotations

import datetime as dt
import logging

import httpx
from kubernetes import client as k8s_client

LOGGER = logging.getLogger(__name__)

SERVING_NAMESPACE = "serving"
MODEL_API_DEPLOYMENT = "model-api"
MODEL_API_HPA = "model-api"
LATENCY_SCALE_STEP = 2
DEBOUNCE_SECONDS = 120


def patch_hpa_min_replicas(
    autoscaling: k8s_client.AutoscalingV2Api,
    name: str = MODEL_API_HPA,
    namespace: str = SERVING_NAMESPACE,
    step: int = LATENCY_SCALE_STEP,
) -> dict[str, int]:
    """Raise an HPA's minReplicas by step, capped at its maxReplicas.

    HPA owns Deployment replicas, so a manual scale would fight the
    controller; raising the HPA floor is the conflict-free equivalent
    (documented in docs/DECISIONS.md).
    """
    hpa = autoscaling.read_namespaced_horizontal_pod_autoscaler(name, namespace)
    current_min = hpa.spec.min_replicas or 1
    maximum = hpa.spec.max_replicas
    new_min = min(current_min + step, maximum)
    body = {"spec": {"minReplicas": new_min}}
    autoscaling.patch_namespaced_horizontal_pod_autoscaler(name, namespace, body)
    LOGGER.info(
        "hpa floor raised",
        extra={"hpa": name, "old_min": current_min, "new_min": new_min, "max": maximum},
    )
    return {"old_min": current_min, "new_min": new_min, "max": maximum}


def rollout_restart_deployment(
    apps: k8s_client.AppsV1Api,
    name: str = MODEL_API_DEPLOYMENT,
    namespace: str = SERVING_NAMESPACE,
) -> str:
    """Trigger a Deployment rollout restart via the restartedAt annotation."""
    stamp = dt.datetime.now(dt.UTC).isoformat(timespec="seconds")
    body = {
        "spec": {
            "template": {
                "metadata": {
                    "annotations": {"kubectl.kubernetes.io/restartedAt": stamp}
                }
            }
        }
    }
    apps.patch_namespaced_deployment(name, namespace, body)
    LOGGER.info("deployment restarted", extra={"deployment": name, "at": stamp})
    return stamp


def create_grafana_annotation(
    grafana_url: str,
    user: str,
    password: str,
    text: str,
    tags: list[str] | None = None,
) -> int:
    """Create a Grafana annotation; return the annotation id."""
    response = httpx.post(
        f"{grafana_url.rstrip('/')}/api/annotations",
        auth=(user, password),
        json={"text": text, "tags": tags or ["remediator"]},
        timeout=10,
    )
    response.raise_for_status()
    annotation_id = int(response.json().get("id", 0))
    LOGGER.info("grafana annotation created", extra={"id": annotation_id, "text": text})
    return annotation_id

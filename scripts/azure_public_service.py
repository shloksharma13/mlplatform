"""Prepare a Kubernetes Service for one Azure public endpoint."""

from __future__ import annotations

import json
import os
import sys
from typing import Any


def prepare_service(service: dict[str, Any], port: int) -> dict[str, Any]:
    """Bind a Service to the shared Azure IP and expose only its main port."""
    service["metadata"].setdefault("annotations", {}).update(
        {
            "service.beta.kubernetes.io/azure-pip-name": os.environ["PUBLIC_IP_NAME"],
            "service.beta.kubernetes.io/azure-load-balancer-resource-group": os.environ[
                "AZURE_RESOURCE_GROUP"
            ],
        }
    )
    spec = service["spec"]
    spec["type"] = "LoadBalancer"
    spec["loadBalancerSourceRanges"] = [
        os.environ["AZURE_ALLOWED_CIDR"],
        os.environ["RUNNER_CIDR"],
    ]
    spec["ports"] = [spec["ports"][0]]
    spec["ports"][0]["port"] = port
    return service


def main() -> None:
    """Read a Service from stdin and write the Azure-configured Service."""
    service = json.load(sys.stdin)
    configured = prepare_service(service, int(os.environ["PUBLIC_PORT"]))
    json.dump(configured, sys.stdout)


if __name__ == "__main__":
    main()

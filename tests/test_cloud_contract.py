"""Cloud migration regressions that run without Azure credentials or a cluster."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest
import yaml
from retrain.dispatch import dispatch_candidate

from scripts.azure_public_service import prepare_service

ROOT = Path(__file__).resolve().parents[1]


def test_public_service_exposes_only_configured_port(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Shared Azure IP Services must not retain conflicting chart ports."""
    monkeypatch.setenv("PUBLIC_IP_NAME", "mlp-test-ingress")
    monkeypatch.setenv("AZURE_RESOURCE_GROUP", "mlp-test-rg")
    monkeypatch.setenv("AZURE_ALLOWED_CIDR", "203.0.113.8/32")
    monkeypatch.setenv("RUNNER_CIDR", "198.51.100.4/32")
    service = {
        "metadata": {"annotations": {}},
        "spec": {
            "ports": [
                {"port": 80, "targetPort": 9093, "name": "http"},
                {"port": 8080, "targetPort": 8080, "name": "metrics"},
            ]
        },
    }

    prepared = prepare_service(service, 9093)

    assert prepared["spec"]["type"] == "LoadBalancer"
    assert prepared["spec"]["ports"] == [
        {"port": 9093, "targetPort": 9093, "name": "http"}
    ]
    assert prepared["spec"]["loadBalancerSourceRanges"] == [
        "203.0.113.8/32",
        "198.51.100.4/32",
    ]


def test_github_oidc_session_refreshes_for_long_azure_provisioning(
    tmp_path: Path,
) -> None:
    """A fresh GitHub assertion must renew Azure CLI auth after Terraform apply."""
    binaries = tmp_path / "bin"
    binaries.mkdir()
    fake_curl = binaries / "curl"
    fake_curl.write_text('#!/bin/sh\nprintf \'{"value":"fresh-oidc-token"}\\n\'\n')
    fake_curl.chmod(0o755)
    fake_az = binaries / "az"
    fake_az.write_text('#!/bin/sh\nprintf "%s\\n" "$*" >> "$AZ_CALLS"\n')
    fake_az.chmod(0o755)
    calls = tmp_path / "azure-calls.txt"
    environment = {
        **os.environ,
        "PATH": f"{binaries}:{os.environ['PATH']}",
        "AZ_CALLS": str(calls),
        "ACTIONS_ID_TOKEN_REQUEST_URL": "https://token.actions.test/oidc",
        "ACTIONS_ID_TOKEN_REQUEST_TOKEN": "github-request-token",
        "ARM_CLIENT_ID": "client",
        "ARM_TENANT_ID": "tenant",
        "ARM_SUBSCRIPTION_ID": "subscription",
    }
    result = subprocess.run(
        [
            "bash",
            "-c",
            f'source "{ROOT}/scripts/azure_oidc.sh"; refresh_azure_oidc_login',
        ],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert calls.read_text().splitlines() == [
        "login --service-principal --username client --tenant tenant "
        "--federated-token fresh-oidc-token --output none",
        "account set --subscription subscription",
    ]
    assert "refresh_azure_oidc_login" in (ROOT / "scripts/azure.sh").read_text()


def check(action: str, **overrides: str) -> subprocess.CompletedProcess[str]:
    """Run the configuration gate only; never initialize or mutate Azure."""
    environment = {
        **os.environ,
        "ARM_CLIENT_ID": "test-client",
        "ARM_TENANT_ID": "test-tenant",
        "ARM_SUBSCRIPTION_ID": "test-subscription",
        "TF_STATE_ACCOUNT": "teststate",
        "TF_VAR_kubernetes_version": "1.36.4",
        "TF_VAR_budget_email": "owner@example.com",
        "AZURE_ALLOWED_CIDR": "203.0.113.10/32",
        "ACTION": action,
        "CONFIRMATION": "",
        **overrides,
    }
    return subprocess.run(
        ["bash", "scripts/azure.sh", "check"],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.mark.parametrize("action", ["deploy", "wake", "destroy"])
def test_billable_actions_require_explicit_confirmation(action: str) -> None:
    """Cloud configuration cannot pass accidentally without the human's choice."""
    result = check(action)
    assert result.returncode != 0
    assert f"confirmation must be {action.upper()}" in result.stderr
    assert check(action, CONFIRMATION=action.upper()).returncode == 0


def test_plan_needs_configuration_but_no_billing_confirmation() -> None:
    """Plan works without approval, while absent identity/state fails before login."""
    assert check("plan").returncode == 0
    result = check("plan", TF_STATE_ACCOUNT="")
    assert result.returncode != 0
    assert "TF_STATE_ACCOUNT" in result.stderr


def test_dashboard_action_is_allowed_without_widening_the_source_allowlist() -> None:
    """Dashboard port migration is a CI-gated, allowlisted operation."""
    assert check("dashboard").returncode == 0
    manifest = (ROOT / "infra/dashboard.yaml").read_text()
    workflow = (ROOT / ".github/workflows/cd.yml").read_text()
    deploy_script = (ROOT / "scripts/azure.sh").read_text()
    assert "loadBalancerSourceRanges: [__ALLOWED_CIDR__, __RUNNER_CIDR__]" in manifest
    assert "port: 443, targetPort: https" in manifest
    assert "inputs.action == 'deploy' || inputs.action == 'dashboard'" in workflow
    assert "https://${PUBLIC_HOST}/healthz" in deploy_script
    assert "--connect-timeout 10 --max-time 15" in deploy_script
    assert (
        'RUNNER_CIDR="$(curl --fail --silent --show-error https://api.ipify.org)/32"'
        in deploy_script
    )
    assert "loadBalancerSourceRanges" in deploy_script


def test_release_rejects_shell_payloads() -> None:
    """Candidate identifiers cannot become executable shell syntax in a Job."""
    assert check("release", VERSION="7").returncode == 0
    assert check("release", VERSION="7; touch /tmp/cloud-payload").returncode != 0


def test_make_cloud_training_requires_an_explicit_cloud_image() -> None:
    """Make must preserve the shell guard instead of erasing it as a Make variable."""
    environment = dict(os.environ)
    environment.pop("TRAINING_IMAGE", None)
    result = subprocess.run(
        ["make", "train-azure"],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode != 0
    assert "set TRAINING_IMAGE to the deployed ACR" in result.stderr


def test_cloud_retrain_keeps_acr_images_and_immutable_tag() -> None:
    """The in-cluster release must reuse the deployed cloud images at every step."""
    output = subprocess.run(
        [
            "helm",
            "template",
            "retrain",
            "infra/charts/retrain",
            "--set",
            "image.repository=demo.azurecr.io/mlp-retrain,image.tag=abc123",
            "--set",
            "env.MODEL_API_REPOSITORY=demo.azurecr.io/mlp-model-api",
            "--set",
            "env.TRAINING_IMAGE=demo.azurecr.io/mlp-training:abc123",
            "--set",
            "env.IMAGE_TAG=abc123,env.DEPLOY_TARGET=azure",
        ],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    config = next(
        doc for doc in yaml.safe_load_all(output) if doc["kind"] == "ConfigMap"
    )
    container = json.loads(config["data"]["pod-spec.json"])["containers"][0]
    assert container["image"] == "demo.azurecr.io/mlp-retrain:abc123"
    environment = {item["name"]: item.get("value") for item in container["env"]}
    assert environment["TRAINING_IMAGE"] == "demo.azurecr.io/mlp-training:abc123"
    assert environment["MODEL_API_REPOSITORY"] == "demo.azurecr.io/mlp-model-api"
    assert environment["IMAGE_TAG"] == "abc123"
    assert environment["DEPLOY_TARGET"] == "azure"


def test_dispatch_forwards_cloud_image_tag(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An Azure retrain release must not fall back to the absent dev image tag."""
    monkeypatch.setenv("IMAGE_TAG", "abc123")
    script = tmp_path / "release.sh"
    result = tmp_path / "result.txt"
    script.write_text('printf "%s\\n" "$1" "$2" > result.txt\n')
    dispatch_candidate("9", release_script=str(script), workdir=str(tmp_path))
    assert result.read_text().splitlines() == ["9", "abc123"]


def test_dashboard_serves_page_and_runs_commands_from_repository(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Moving the UI must preserve its template and command working directory."""
    from fastapi.testclient import TestClient

    from ui.app import ACTION_COMMANDS, app

    monkeypatch.chdir(tmp_path)
    monkeypatch.setitem(ACTION_COMMANDS, "verify-layout", "make help")
    with TestClient(app) as client:
        page = client.get("/")
        assert page.status_code == 200
        assert "<html" in page.text.lower()
        output = client.get("/api/stream/verify-layout")
        assert "MLPlatform targets:" in output.text
        assert "COMPLETED SUCCESSFULLY (EXIT 0)" in output.text


@pytest.mark.parametrize(
    "scenario",
    [
        "normal",
        "partial",
        "unowned",
        "delete_failure",
        "budget_delete_failure",
        "list_failure",
        "expired",
        "not_expired",
        "bad_expiry",
        "orphaned_nodes",
        "startup_unowned",
    ],
)
def test_full_kill_is_state_independent_and_checks_ownership(
    scenario: str, tmp_path: Path
) -> None:
    """Exercise real kill control flow against a fake Azure CLI, never Azure."""
    state = tmp_path / "groups.json"
    owner = "JaiiMukundh/mlplatform"
    groups = {
        "test-app": {"repository": owner},
        "test-state": {"repository": owner},
        "test-nodes": {
            "managedBy": "/subscriptions/test-sub/resourceGroups/test-app/"
            "providers/Microsoft.ContainerService/managedClusters/test-cluster"
        },
    }
    if scenario == "partial":
        groups.pop("test-app")
    if scenario == "orphaned_nodes":
        groups.pop("test-app")
        groups.pop("test-state")
    if scenario in {"unowned", "startup_unowned"}:
        groups["test-state"] = {"repository": "another/repository"}
    for group in groups.values():
        group["deadline"] = (
            "3000-01-01T00:00:00Z"
            if scenario == "not_expired"
            else "invalid"
            if scenario == "bad_expiry"
            else "2000-01-01T00:00:00Z"
        )
    state.write_text(
        json.dumps(
            {
                "groups": groups,
                "deleted": [],
                "budget": {
                    "id": "/subscriptions/test-sub/resourceGroups/test-app/"
                    "providers/Microsoft.Consumption/budgets/mlp-test-monthly"
                },
            }
        )
    )
    cli = tmp_path / "az"
    cli.write_text(
        "#!/usr/bin/env python3\n"
        "import json, os, sys\n"
        "from pathlib import Path\n"
        "path = Path(os.environ['FAKE_AZ_STATE'])\n"
        "state = json.loads(path.read_text())\n"
        "args = sys.argv[1:]\n"
        "if args[:2] == ['account', 'set']:\n"
        "    sys.exit(0)\n"
        "if args[:2] == ['aks', 'list']:\n"
        "    if os.environ['SCENARIO'] == 'list_failure': sys.exit(3)\n"
        "    print('test-nodes'); sys.exit(0)\n"
        "if args[:3] == ['consumption', 'budget', 'show']:\n"
        "    print((state.get('budget') or {}).get('id', '')); sys.exit(0)\n"
        "if args[:3] == ['consumption', 'budget', 'delete']:\n"
        "    if os.environ['SCENARIO'] == 'budget_delete_failure': sys.exit(6)\n"
        "    state['budget'] = None; path.write_text(json.dumps(state)); sys.exit(0)\n"
        "group = args[args.index('--name') + 1]\n"
        "if args[:2] == ['group', 'exists']:\n"
        "    print(str(group in state['groups']).lower())\n"
        "elif args[:2] == ['group', 'show']:\n"
        "    if 'tags.ExpiresAt' in args: print(state['groups'][group]['deadline'])\n"
        "    elif 'tags.Repository' in args:\n"
        "        print(state['groups'][group].get('repository',''))\n"
        "    else: print(json.dumps(state['groups'][group]))\n"
        "elif args[:2] == ['group', 'delete']:\n"
        "    if os.environ['SCENARIO'] == 'delete_failure': sys.exit(4)\n"
        "    state['deleted'].append(group)\n"
        "    del state['groups'][group]\n"
        "    path.write_text(json.dumps(state))\n"
        "else: sys.exit(5)\n"
    )
    cli.chmod(0o755)
    gh = tmp_path / "gh"
    gh.write_text("#!/bin/sh\nexit 0\n")
    gh.chmod(0o755)
    terraform = tmp_path / "terraform"
    terraform.write_text("#!/bin/sh\nexit 95\n")
    terraform.chmod(0o755)
    environment = {
        **os.environ,
        "PATH": f"{tmp_path}:{os.environ['PATH']}",
        "ARM_SUBSCRIPTION_ID": "test-sub",
        "AZURE_RESOURCE_GROUP": "test-app",
        "AZURE_NODE_RESOURCE_GROUP": "test-nodes",
        "TF_STATE_RESOURCE_GROUP": "test-state",
        "CONFIRMATION": "DEPLOY" if scenario == "startup_unowned" else "KILL",
        "ARM_CLIENT_ID": "test-client",
        "ARM_TENANT_ID": "test-tenant",
        "TF_STATE_ACCOUNT": "teststate",
        "TF_VAR_kubernetes_version": "1.36.4",
        "TF_VAR_budget_email": "owner@example.com",
        "TF_VAR_location": "test-region",
        "AZURE_ALLOWED_CIDR": "192.0.2.1/32",
        "GITHUB_REPOSITORY": owner,
        "GITHUB_STEP_SUMMARY": "",
        "GH_TOKEN": "",
        "FAKE_AZ_STATE": str(state),
        "SCENARIO": scenario,
    }
    action = (
        "expired"
        if scenario in {"expired", "not_expired", "bad_expiry", "orphaned_nodes"}
        else "kill"
    )
    if scenario == "startup_unowned":
        action = "deploy"
    command = ["bash", "scripts/azure.sh", action]
    result = subprocess.run(
        command, cwd=ROOT, env=environment, capture_output=True, text=True
    )
    record = json.loads(state.read_text())
    if scenario in {
        "unowned",
        "delete_failure",
        "budget_delete_failure",
        "list_failure",
        "bad_expiry",
        "startup_unowned",
    }:
        assert result.returncode != 0
        assert record["deleted"] == []
        if scenario == "startup_unowned":
            assert "Backend group belongs to another project" in result.stderr
    elif scenario == "not_expired":
        assert result.returncode == 0, result.stderr
        assert record["groups"] == groups
        assert record["deleted"] == []
    else:
        assert result.returncode == 0, result.stderr
        assert "KILL VERIFIED" in result.stdout
        assert record["budget"] is None
        assert record["groups"] == {}
        again = subprocess.run(
            command, cwd=ROOT, env=environment, capture_output=True, text=True
        )
        assert again.returncode == 0
        assert json.loads(state.read_text())["deleted"] == record["deleted"]


def test_kill_requires_explicit_confirmation_but_no_terraform_backend() -> None:
    """The emergency path remains usable when backend configuration is absent."""
    variables = {
        "ARM_SUBSCRIPTION_ID": "test-sub",
        "AZURE_RESOURCE_GROUP": "test-app",
        "TF_STATE_RESOURCE_GROUP": "test-state",
        "TF_STATE_ACCOUNT": "",
    }
    assert check("kill", **variables).returncode != 0
    assert check("kill", CONFIRMATION="KILL", **variables).returncode == 0


def test_dashboard_requires_configured_login_and_keeps_health_public(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cloud operations must be authenticated while Kubernetes probes stay usable."""
    from fastapi.testclient import TestClient

    from ui.app import app

    monkeypatch.setenv("DASHBOARD_PASSWORD", "demo-test-password")
    with TestClient(app) as client:
        assert client.get("/healthz").status_code == 200
        assert client.get("/").status_code == 401
        assert client.get("/", auth=("admin", "wrong")).status_code == 401
        assert client.get("/", auth=("admin", "demo-test-password")).status_code == 200


def test_dashboard_prediction_uses_cloud_service_endpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Predictions go to the configured service rather than dashboard localhost."""
    import httpx
    from fastapi.testclient import TestClient

    from ui import app as dashboard

    original = httpx.AsyncClient
    calls = []

    def predict(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(
            200,
            json={
                "model_version": "1",
                "predictions": [{"default_probability": 0.2, "label": 0}],
            },
        )

    monkeypatch.setattr(dashboard, "MODEL_API_URL", "http://model-api.serving.svc:8080")
    monkeypatch.setattr(
        dashboard.httpx,
        "AsyncClient",
        lambda **kwargs: original(transport=httpx.MockTransport(predict), **kwargs),
    )
    with TestClient(dashboard.app) as client:
        result = client.post(
            "/api/predict", json={"customer_id": "7", "env": "retrained"}
        )
        assert result.status_code == 200
        assert result.json()["status"] == "success"
        assert result.json()["default_probability"] == 0.2
    assert calls == ["http://model-api.serving.svc:8080/predict"]

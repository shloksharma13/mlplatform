# Azure through GitHub Actions

Status: live test deployment verified 2026-10-15 through GitHub Actions run
[38715945979](https://github.com/JaiiMukundh/mlplatform/actions/runs/38715945979)
at commit `f76587ed3fe1b1987addbf837ab48e137ed368ab`. This is a temporary
two-hour test environment; full deletion is scheduled for 2026-10-15 12:25 UTC.
The local environment remains the functional test harness. Azure and
GitHub-hosted runners handle cloud operation; no laptop runner is required.

Live endpoints:

- Dashboard: <https://mlp-test-b3e0c60e.indiasouthcentral.cloudapp.azure.com>
  (login `admin`; self-signed test certificate; tester network allowlist applies).
- Model API: <http://172.198.48.202:8080> (`/readyz`, `/model-info`, and
  `POST /predict`).
- MLflow: <http://172.198.48.202:5000>; Grafana: <http://172.198.48.202:3000>.
- MinIO S3/API: <http://172.198.48.202:9000>.

The Actions deployment verified the public model readiness, model metadata, and
prediction endpoints, then verified the dashboard status API. Feast apply,
materialize, and verification jobs completed; MLflow training registered model
version 1 and promoted it to `champion` (validation AUC 0.8808, test AUC 0.8970).
The dataset and metrics are synthetic demo data. Drift monitor, retrain,
remediator, and model API workloads are running in AKS. Use the credentials in
the cluster secret `grafana-admin`; do not store them in GitHub or this repo.

## Two workflows, one cloud script

- `ci.yml`: pushes, pull requests, or manual runs; Python lint/format/tests,
  coverage, Terraform validation, Helm schemas, Dockerfile lint, workflow lint,
  secret scanning, and critical dependency vulnerability scanning.
- `cd.yml` (Actions name **azure**): select an action on **main** in GitHub's
  Run workflow dialog. `plan` is the default. `deploy`, `wake`, and `destroy`
  require their uppercase action name in the confirmation input. Deploy also
  requires successful CI for the exact selected commit.
- `scripts/azure.sh`: the single implementation behind `make azure`;
  initializes locked Terraform against persistent Blob state, then runs the
  selected operation. Concurrency serializes cloud operations.
- Optional `model-candidate-ready` repository dispatches select `release` only.
  The normal autonomous retraining loop releases in-cluster and needs no
  GitHub token. Both release paths run the same gate, staging load check,
  alias promotion, rollout, and production verification scripts on AKS.

The private repository's current GitHub plan does not support required
reviewer protection rules (GitHub returned HTTP 422 on 2026-10-13). Manual
workflow selection plus explicit confirmation is the available human approval
mechanism. Add required reviewers to the `azure` environment if the account
plan later supports them. The user explicitly authorized this test deployment on 2026-10-13; see the
scoped AGENTS.md exception.

## One-time Azure setup — next cloud phase

Use Azure Portal or Azure Cloud Shell for the bootstrap; no laptop is needed.
Create a storage account with a private `tfstate` container for Terraform state,
separate from the application resource group so a platform destroy retains it.
This bootstrap creates billable storage and belongs to the human Azure phase.
If resources already exist in local Terraform state, migrate that state to the
backend before the first Actions deployment rather than deploying duplicates.

Create an Entra application/service principal for GitHub OIDC. Its federated
credential must use:

| Field | Value |
|---|---|
| Issuer | `https://token.actions.githubusercontent.com` |
| Subject | `repo:JaiiMukundh/mlplatform:environment:azure` |
| Audience | `api://AzureADTokenExchange` |

For this small demo, grant Contributor plus Role Based Access Control
Administrator at the provisioning scope, and Storage Blob Data Contributor on
the state account. Terraform creates the ACR pull role assignment,
so Contributor alone is insufficient. A subscription scope is the simple
bootstrap option when Terraform must create the app resource group. This is
broad demo access; tighten it if the platform becomes production software.

Set these **GitHub environment `azure` variables**, never Git secrets or files:

| Variable | Meaning |
|---|---|
| `AZURE_CLIENT_ID` | OIDC service principal application ID |
| `AZURE_TENANT_ID` | Entra tenant ID |
| `AZURE_SUBSCRIPTION_ID` | Your student subscription ID |
| `TF_STATE_ACCOUNT` | Bootstrap state storage account name |
| `AZURE_KUBERNETES_VERSION` | Exact supported AKS patch version in your region |
| `AZURE_BUDGET_EMAIL` | Real budget notification address |
| `AZURE_LOCATION` | Optional; defaults to `indiasouthcentral` |
| `AZURE_VM_SIZE` | Optional; defaults to `Standard_B2as_v2` |

Determine current AKS version and node availability from the region before
setting those values. The old `1.30` scaffold default is deliberately removed:
Actions must not silently select an obsolete release. The in-image kubectl
version must be within one minor of the selected AKS version.

No password or client secret is needed for OIDC. Terraform state is sensitive:
keep the state container private; never publish plan/state artifacts. Database
and object-store credentials live in Kubernetes Secrets and are reused on reruns.

## Cloud topology and current limits

Terraform provisions the AKS network, AKS, ACR, and budget notifications. AKS
uses one supported 4-vCPU/16-GiB Standard_D4as_v4 system node and one
Standard_B2s user node for platform services in this test subscription. Model
API retains two replicas with 512 MiB requests and 1 GiB limits. VM-family
quota and regional availability must be checked for the actual subscription;
`make azure-preflight` is read-only when authenticated.

The deployment builds all seven named targets from `infra/Dockerfile`,
pushes commit-tagged ACR images, installs the same Helm charts as the proven
local environment, and installs Prometheus/Grafana/Loki/Alloy before ServiceMonitors.
It seeds Feast and trains the initial MLflow champion only when no champion
exists. Reruns preserve model lineage and existing drifted data. A readiness,
model-info, and real prediction check must succeed before deployment succeeds.

Model API has the public Azure LoadBalancer on port 8080. Redis, MLflow,
MinIO, PostgreSQL, and monitoring stay in the cluster. Offline features and
model artifacts currently use MinIO; prediction logs and MLflow metadata use
in-cluster PostgreSQL. Unused managed Blob/PostgreSQL resources and workload identities have been
removed. Application storage remains the same proven MinIO/PostgreSQL setup;
the separate Blob account for Terraform state is still required.

The dashboard runs inside AKS over HTTPS on port 8050 with admin login; its
public load balancer listens on standard HTTPS port 443.
Its temporary certificate is self-signed. Native tool ports share the same
static public IP, each Service exposes only its assigned port, and access is
restricted to the configured tester network and the GitHub Actions runner.
Local dashboard/demo operation remains available as a functional harness. The dataset is real UCI Taiwan credit data; repeated
snapshot timestamps and injected drift are demonstrations, not real outcomes
observed over time.

## Operations and validation

Prefer GitHub Actions' **azure** workflow buttons for cloud operations. The
same repeatable interface is available through Make when authenticated:

| Target | Effect |
|---|---|
| `make azure ACTION=plan` | Read-only infrastructure plan |
| `make azure ACTION=deploy CONFIRMATION=DEPLOY` | Human-approved provisioning and deployment |
| `make azure ACTION=verify` | Readiness, model metadata, and prediction checks |
| `make azure ACTION=release VERSION=7` | Run candidate gate/release in an AKS Job |
| `make azure ACTION=sleep` | Stop AKS nodes; disks and managed services still bill |
| `make azure ACTION=wake CONFIRMATION=WAKE` | Restart AKS nodes |
| `make azure ACTION=destroy CONFIRMATION=DESTROY` | Destroy application infrastructure; retain bootstrap state storage |
| `make lint test check-infra check-actions check-charts secret-scan` | Verify repository without cloud deployment |
| `make check-images` | Build and import-smoke all seven container targets without Azure |

A budget alert is a notification, not an automatic spending stop. Destroy only
after the demo/data are no longer needed; in-cluster PVCs and lineage are lost
with the cluster. Sleep retains them but does not eliminate all charges.

## Complete kill switch and automatic expiry

Use Actions → azure → Run workflow → action **kill**, confirmation **KILL**.
This cancels an active cloud operation, validates repository ownership, deletes
the exact project budget, app and AKS node resource groups, plus the separately
owned state-storage resource group, and verifies all are absent. It works without Terraform state,
including after partial deployment. `make azure-kill CONFIRMATION=KILL` exposes
the same operation. This deletes all test datasets, disks, artifacts, and model
history; the next startup provisions a fresh environment. Unrelated subscription
resources are never part of the kill switch.

The user chose a two-hour test session. Startup tags its deadline; azure-expiry
checks every 15 minutes and invokes the same full teardown when due. GitHub
schedules can be delayed, so use the manual kill switch as soon as testing ends.
The expiry workflow disables itself after cleanup and startup re-enables it.
The OIDC app and repository remain: neither is a running Azure resource.

Stopping AKS keeps disks and other charges. Terraform `destroy` retains the
state storage account. Only full `kill` removes all project-owned billable
resources; charges already incurred can appear later in Cost Management.

Dashboard credentials are the `grafana-admin` Kubernetes secret (username admin).
The deployment records its URL and deadline in the GitHub Actions summary; it
does not print the password. The initial tester network is AZURE_ALLOWED_CIDR
in GitHub environment azure; update it when testing from a different network.

The university tenant prohibits Entra application registration. The connected
Owner instead bootstraps a user-assigned managed identity with the same GitHub
OIDC issuer/subject/audience in `mlp-github-identity-rg`. It remains after kill
to allow startup from Actions. The identity-only group contains no VM, disks,
registry, or storage; managed identities have no additional cost.

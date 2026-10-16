# Decisions

## 2026-10-15 — Serve the Azure dashboard on standard HTTPS

- **Decision:** Keep the dashboard container on HTTPS port 8050, but expose its
  Azure LoadBalancer Service on port 443. Add a targeted GitHub Actions action
  to update this Service and check the public health URL, with the existing
  tester and runner source ranges unchanged.
- **Reason:** The dashboard pod and node-port health checks were healthy, while
  connections to the public `:8050` endpoint timed out. Port 443 avoids client
  networks that block uncommon ports without broadening the service allowlist.

## 2026-10-15 — Give Azure platform services a quota-supported node and isolate public ports

- **Decision:** Keep the D4as_v4 system node for the two model API replicas and
  add one B2s user pool for platform services. Raise the Azure model API
  container memory to a 512 MiB request and 1 GiB limit, lower MLflow's
  scheduling request to 256 MiB, and expose exactly one port per Service on the
  shared public IP.
- **Reason:** The first live run required an auxiliary B2s node to schedule the
  platform and failed serving while Alertmanager retained a conflicting 8080
  Service port. The exact single-port rule prevents a second Azure LoadBalancer
  rule from claiming the model API port; codifying the observed node and memory
  settings makes clean deployments repeatable.

## 2026-10-15 — Preserve or remove Azure budgets across full cleanup

- **Decision:** On startup, import an existing budget only when its full Azure
  resource ID matches this project's app resource group. On full kill, delete
  that exact cost budget after validating group ownership and before deleting
  the groups.
- **Reason:** Azure leaves Cost Management budget objects behind when their
  resource groups are removed. A fresh Terraform backend then collides with the
  old budget name. Import supports repeatable restart, while full kill removes
  the orphaned alert rather than accumulating project leftovers.

## 2026-10-15 — Refresh GitHub OIDC after Azure provisioning

- **Decision:** Request a fresh job-scoped GitHub OIDC token and reauthenticate
  Azure CLI immediately before AKS access after Terraform completes.
- **Reason:** AKS provisioning can take longer than the five-minute validity of
  the original federated assertion. Infrastructure succeeded, but the expired
  assertion prevented `az aks get-credentials` from obtaining a user token.

## 2026-10-13 — Compact infrastructure and finish GitHub before Azure execution

The human now authorizes repository edits, commits, and GitHub pushes, and asks for fewer files without losing functionality. Keep each Terraform module in one `main.tf` (at most 221 lines), retaining all module interfaces and resource addresses. Retain local functional tests and the local environment as the proven test harness; cloud operation must never depend on a laptop runner. Existing uncommitted Azure migration changes are part of the requested publication; exclude the unrelated synopsis document. Verify and commit this compaction before rewriting CI/CD. Record new stable tool/provider pins from official registries; commit Terraform dependency locks. Azure provisioning remains a human-approved operation under AGENTS.md rule 12.

Architecture and implementation decisions are recorded chronologically.

## 2026-10-04 — Make E2E runs include the model self-healing demo

- **Decision:** `make e2e` sets `MLP_E2E_RUN_DEMO=1`, so the model self-healing
  test resets the local platform, injects synthetic drift, and waits for the
  unattended retrain/release loop before asserting the converged state.
- **Reason:** P9.1 explicitly makes the P8.7 self-healing timeline part of the
  E2E acceptance. Keeping the orchestration in the test makes one repeatable
  Makefile command cover serving, pod recovery, and model recovery; the test
  can still be run as a fast state assertion by omitting that environment flag.

## 2026-10-02 — Flat, single-project repository layout

- **Decision:** Replace the nested `libs/<pkg>/src/<pkg>/` and `services/<svc>/src/<svc>/`
  layout (uv workspace with three `pyproject.toml` files and per-package test dirs) with
  a flat mono-repo: one root `pyproject.toml`, one top-level package per Python import
  root (`mlp_common/`, `ml/`, `model_api/`, `drift_monitor/`, `retrain/`, `remediator/`),
  a single `tests/` tree, and a single `docker/` directory for image definitions. Empty
  `.gitkeep`-only chart, tofu-module, and per-service test directories were removed;
  they are recreated in their owning phases.
- **Reason:** human-directed simplification ("too many repos and sub-repos for nothing").
  The nested `src` layout and multi-package workspace added depth and duplicated tooling
  without any benefit at this scale. The new layout keeps the import roots named in
  Section 5.5 stable, so no `import` statements changed, and `make` remains the command
  interface. `docs/SPEC.md` Sections 1 and 4 were updated to match.
- **Scope:** `ml/` keeps its `ml/data/` subpackage (matches Section 4 and avoids a
  gratuitous rename). The coverage gate covers `mlp_common/` and `model_api/`; the offline
  `ml/` training code is validated by its own tests rather than the coverage floor.

## 2026-10-02 — Host networking for image builds on rootless Docker (superseded)

- **Superseded** by the "Use the real Docker daemon" decision below.
- **Decision:** `make build` passes `--network=host` to `docker build` by default,
  overridable with `make build DOCKER_BUILD_NETWORK=default`.
- **Reason:** On this laptop the rootless Docker daemon's default bridge has no working
  DNS resolution — the container `/etc/resolv.conf` points at the host LAN resolver
  `172.22.47.212`, which is unreachable from the (slirp4netns) container network, and even
  explicit `--dns 10.0.2.3 / 8.8.8.8 / 1.1.1.1` fail. Host networking uses the host network
  namespace, so `pip` can reach PyPI during the build. The produced image is unaffected.
- **Evidence:** `docker run --network=host ... pip download hatchling==1.32.4` succeeded;
  default-bridge DNS lookups failed with `Temporary failure in name resolution`.

## 2026-10-02 — Use the real Docker daemon; no build-network override

- **Decision:** `make build` runs a plain `docker build` against the active Docker daemon
  (the standard rootful engine at `unix:///var/run/docker.sock`). The `--network=host`
  override was removed.
- **Reason:** human directive — use real Docker, not the rootless host-network workaround.
  A normal rootful engine has working container DNS, so no override is needed.
- **Status:** the rootful Docker Engine is not installed on this machine yet — there is no
  `/var/run/docker.sock`, no `docker.service`, no `docker` group, and `dockerd`/`docker`
  exist only under `~/.local/bin`. See BLK-003 for the human install steps. Image builds
  cannot be re-verified until the daemon is available.

## 2026-10-02 — P3 data-layer image choices

- **Decision:** Postgres uses the official `postgres:16.15-bookworm` image; MinIO uses
  `bitnamilegacy/minio` + `bitnamilegacy/minio-client` (2025.7.23 lineage); Redis stays
  on the last BSD-licensed `7.2.x` line at exactly `7.2.16-alpine3.21`.
- **Reason:** SPEC 2 requires checking MinIO availability/licensing at implementation
  time (LocalStack free tier is restricted, so it is not used). The official
  `minio/minio` pull requires authentication from this host, while the
  `bitnamilegacy` fork (community continuation after the Bitnami catalog changes)
  pulls cleanly by digest — same S3 API, so service code is unaffected. Our own Helm
  charts are used throughout, so the "avoid Bitnami charts" rule still holds.
  The `7.2-alpine3.22` redis tag does not exist upstream (alpine3.22 was never
  published for 7.2.x); `7.2.16-alpine3.21` is the latest 7.2 patch and its digest
  matches the pulled image.
- **Evidence:** `docker images --digests` confirms postgres `efedf359…`, minio
  `953d489a…`, minio-client `7a6b4b93…`, redis `29e8589c…`; `redis-server --version`
  reports `v=7.2.16`.

## 2026-10-02 — Drive kind via CLI from OpenTofu, not the kind provider

- **Decision:** `infra/modules/cluster_kind` renders `kind-config.yaml` from its
  variables and manages the cluster with `terraform_data` + `local-exec` running
  `kind create/delete cluster` (same imperative pattern as the registry module),
  instead of the `tehcyx/kind` provider's `kind_cluster` resource.
- **Reason:** provider v0.11.0 (latest) bundles kind 0.31.0, whose kubeadm cannot
  init the pinned Kubernetes v1.37.0 nodes (`kubeadm init exit status 1`,
  reproduced twice on a clean slate), while the kind v0.33.0 CLI creates the
  identical config cleanly (proven with a probe cluster). Downgrading K8s to suit
  the provider would diverge CLI and IaC; driving the verified CLI keeps one
  Kubernetes version everywhere. Module inputs/outputs are unchanged, so
  `infra/envs/local` is unaffected.
- **Consequence:** `make up` applies in two phases (cluster targets first, then
  everything): the kubernetes/helm providers stat the kubeconfig file at
  configuration time, so they can only be configured after the cluster has
  written it. Both phases are no-ops when converged.

## 2026-10-02 — Harden data-layer init jobs for cold starts

- **Decision:** the postgres init job waits for `pg_isready` and the minio buckets
  job waits for `mc alias set` to succeed (10-minute budget) before doing work;
  the buckets job runs under `/bin/bash` with a writable `$HOME` emptyDir.
- **Reason:** on a fresh cluster the nodes pull ~800MB of images while the helm
  hooks already run; without waits the jobs burn their backoff budget. Separately,
  the buckets job died instantly twice: `/bin/sh` in the minio-client image is
  dash (no `pipefail`), and `mc` needs a writable `$HOME` under the read-only
  root filesystem. Diagnosed by replicating the container with debug pods.

## 2026-10-02 — AWS parity module choices (plan-only)

- **Decision:** VPC and EKS use the community modules (`terraform-aws-modules/vpc`
  6.7.3, `.../eks` 21.26.0) as SPEC P3.6 names; S3/RDS/ECR/IAM/budget are raw
  `aws_*` resources. AZ names derive from the region string (`<region>a/b`) and
  the RDS engine pins major `"16"` only.
- **Reason:** wiring the two big modules blind is already risky; raw resources keep
  the rest exactly reviewable. `data.aws_availability_zones` and exact RDS minors
  need live AWS APIs, which `plan` with dummy credentials cannot reach — derived
  AZs and a major-version pin keep `validate`/`plan` offline-capable; the exact
  minor is pinned at the P9.6 human apply. IRSA roles mirror the local static
  creds (model-api read-only; retrain/mlflow read-write).

## 2026-10-02 — Upstream charts vendored (P5)

- **Decision:** `infra/vendor/` commits the pinned `.tgz` files
  (`kube-prometheus-stack-91.8.2`, `loki-7.3.0`, `alloy-1.13.0`,
  `metrics-server-3.14.0`) and tofu installs from those local files instead
  of the helm repositories.
- **Reason:** the upstream asset host (`release-assets.githubusercontent.com`)
  consistently times out for helm/tofu chart downloads on this laptop, while a
  direct `curl` of the same pinned URLs succeeds and the files are byte-identical
  valid gzip. Vendoring keeps every apply reproducible and offline-capable.

## 2026-10-02 — Remediator scales the HPA floor, not Deployment replicas (P5.6)

- **Decision:** on `HighP95Latency` the remediator patches the `model-api`
  HPA `minReplicas` +2 (capped at HPA max) instead of scaling the Deployment.
- **Reason:** the HPA controller owns Deployment replicas; a manual
  `scale` would be reverted within seconds (controller fight). Raising the HPA
  floor is the conflict-free equivalent and self-documents in the HPA spec.
  Verified by the P5.7 drill (floor 2→4, Grafana annotation, then restore).

## 2026-10-03 — Persist the inotify limits and enforce them with a preflight (P6)

- **Decision:** ship `infra/sysctl/99-kind-inotify.conf` (the drop-in the human
  installs once as root) and `scripts/check_host.sh`, which fails loudly when
  `fs.inotify.max_user_instances < 512` or `fs.inotify.max_user_watches < 524288`.
  The check runs as `make preflight` and as the first step of `make up`.
- **Reason:** BLK-002 was fixed with a runtime-only `sysctl -w`, so the host
  reboot on 2026-10-03 silently reverted the limits to the default 128, which
  killed kube-proxy and CoreDNS and took down all in-cluster DNS (BLK-004). A
  persisted drop-in makes the fix durable, and a preflight turns a confusing
  cluster-wide outage into an immediate, actionable error before `make up`
  builds anything. This follows the operating contract: fail loudly, and keep
  `make` a reliable interface.
- **Scope:** values are read from `/proc/sys` so the check needs no extra tools
  and no privileges; `make bootstrap` is deliberately left untouched because it
  regenerates `docs/VERSIONS.md` from scratch and would drop the phase-specific
  pins appended in later phases.

## 2026-10-03 — Feast config rendering and offline filename (P6.2)

- **Decision:** `feature_repo/feature_store.yaml` is not committed. The versioned
  artifact is `feature_repo/feature_store.yaml.tmpl` plus `feature_repo/render_config.py`,
  which substitutes `${POSTGRES_*}`, `FEAST_POSTGRES_DB`, and `${REDIS_*}`
  placeholders from the environment and fails loudly when any value is missing.
- **Reason:** SPEC P6.2 requires a SQL registry whose path embeds the Postgres
  password and explicitly says to render the file from a template with env vars
  rather than commit creds. Keeping the rendered file out of git and out of the
  image (`.gitignore` + `.dockerignore`) satisfies the "no secrets in git" rule;
  in-cluster the values arrive from the `postgres-auth` Secret.
- **Offline filename:** the generator writes `data/features.parquet`, while SPEC
  P6.2 names the FileSource `s3://feast-offline/customer_features.parquet`. The
  setup Job publishes `features.parquet` to MinIO under the spec's name, so
  `definitions.py` can use the exact path P6.2 states without changing the data
  tooling. `labels.parquet` is uploaded unchanged for the P6.4 entity frame.

- 2026-10-03: Training uses s3fs 2026.9.0 and boto3 1.43.106. PyPI aiobotocore 3.9.2 requires botocore <1.43.107; newer boto3 cannot resolve. Preserve the newest compatible exact pin for Feast S3 retrieval.

## 2026-10-03 — Serving reads Feast-online Redis directly; models via mlflow-skinny (P6.5)

- **Decision:** `model_api` serves Feast online features with a hand-rolled
  reader (`redis` + `mmh3` only) and loads models with `mlflow-skinny` +
  `skops`. The `feast` package itself is never installed in the serving
  image or the root test env.
- **Reason:** `feast 0.66.0` requires `pandas<3` while the serving stack pins
  `pandas 3.0.6` (`pip install --dry-run` confirms the conflict is
  unresolvable). Feast's Redis layout was reverse-engineered from the Feast
  0.66 source and verified byte-for-byte against the live store: entity keys
  are the v3 struct packing (`<I count, <I STRING, <I len, name, <I INT64,
  <I 8, <q id` + project), fields are `_mmh3("{view}:{feature}")` (murmur3
  x86_32, first 4 bytes little-endian), values are Feast `Value`-proto
  encodings (float32 tag `0x35`, int64-varint tag `0x20`). The reader decodes
  exactly those encodings and fails loudly on anything else, so a future
  Feast upgrade that changes the layout breaks visibly, not silently.
  `mlflow-skinny 3.16.1` + `redis 8.1.0` + `mmh3 5.3.1` resolve cleanly
  alongside pandas 3 (dry-run verified; an earlier `skops` pin was dropped).
  Models are logged with `serialization_format="cloudpickle"` because skops
  refuses to persist HistGradientBoosting's TreePredictor without an explicit
  trusted-types list at both save and load time; we train and serve only our
  own registered versions, and cloudpickle round-trips with no trust gate.
  Artifact proxying
  (`--serve-artifacts` on the MLflow server) means the serving pods need no
  S3 credentials for model download.
- **Consequence:** `tests/test_parity.py` compares the serving reader
  (against the materialized Redis data, via local Parquet snapshots) with a
  pure-pandas point-in-time join, with no `feast` import; `verify.py`
  (in-cluster, real Feast both sides) covers Feast's own offline==online
  consistency. Together they prove training==serving with no skew.
- **Presigned artifact URLs are cluster-internal (P6.4/P7):** MLflow serves
  model artifacts via presigned URLs embedding `MLFLOW_S3_ENDPOINT_URL`
  (`http://minio.data.svc:9000`), which resolves only inside the cluster. A
  host-networked client hangs on DNS; in-cluster download of
  `models:/loan-default/1` takes ~4s. Consequence: anything that loads model
  bytes (`gate.py` scoring, release verification) runs as an in-cluster Job
  (training image), orchestrated from the runner/workflow via `make` targets
  — never directly on the laptop. Run artifacts (`eval_set.parquet`,
  `reference.parquet`) remain fetchable from the host through the tracking
  server's artifact-proxy endpoint.

## 2026-10-03 — Retraining platform shape (P8)

- **Decision:** one toolbox image (`localhost:5001/retrain:dev`, extending
  the training image with kubectl 1.37.1 + helm 3.22.0, both checksum
  verified) runs retrain Jobs; the Job executes the SAME release scripts the
  workflow uses (`model_release_local.sh`, `deploy.sh`, `smoke.sh`,
  `loadcheck.py`, `run_gate.sh` + charts), so local and GitHub releases stay
  behaviorally identical per SPEC P8.4. The retrain ServiceAccount's release
  powers (helm-equivalent: deployments/services/secrets/HPAs in
  serving+staging, jobs/pods/logs in data, namespaces get/create) are
  namespace-scoped and demo-only by design.
- **Namespaces:** SPEC 5.2 names an `mlops` namespace, but MLflow already
  lives in `data` (P6.1) and every secret the Jobs need lives there too.
  Locally, drift-monitor runs in `observability` (metrics neighbor) and all
  Jobs run in `data`; no `mlops` namespace is created and no secret is
  mirrored twice. The AWS port (P9.5) may introduce `mlops` if cross-account
  boundaries require it.
- **Redis is ephemeral, the registry cursor is not:** Redis runs with
  `--appendonly no` and no volume (rebuildable by design), while Feast's
  incremental cursor lives in the SQL registry. After a Redis wipe,
  `materialize-incremental` sees an already-covered window and writes
  nothing — recovery is `make feast-rematerialize` (full window). The
  serving reader returns a shaped empty frame on a wiped store, so the API
  answers 404 (`unknown_customers`), never a 500.

## 2026-10-13 — Terraform-only IaC, Azure-only cloud, local kept

- **Terraform can provision locally (proven):** `terraform` v1.16.1 is
  installed, every `.tf` file is provider-source-agnostic HCL (`hashicorp/*`
  shorthand, no `tofu`-only syntax), the Makefile already shelled out to
  `terraform`, and `terraform init/validate` succeeds on `infra/envs/local`.
  So the whole repo was converted OpenTofu -> Terraform.
- **State note:** `infra/envs/local/terraform.tfstate` was written by
  `tofu apply`, so its provider addresses read
  `registry.opentofu.org/hashicorp/*` and the (gitignored) lockfile keeps
  both registry stanzas until the next apply. Terraform reads OpenTofu state
  natively (same format, byte-identical providers); the next
  `terraform apply` migrates those addresses automatically. State files were
  deliberately NOT deleted — the local deployment stays live.
- **Azure-only cloud:** `infra/envs/aws/` and its six AWS-only modules
  (`cluster_eks`, `network_vpc`, `database`, `object_storage`,
  `image_registry`, `iam`) were deleted, as were the empty
  `deploy_aws.sh`/`destroy_aws.sh` stubs, `values-aws.yaml`, and
  `docs/aws-port.md`. The six unused `*_azure` AKS-parity modules were
  also deleted: the documented Azure design (`docs/azure-port.md`) is a
  self-contained VM host in `infra/envs/azure` that runs the identical
  kind-based stack via `make up`, so the modules had zero references.
  Replacement: `values-azure.yaml` overlay documenting the VM-host parity.
- **Compaction:** deleted the untracked byte-identical `deploy/` duplicate
  of `infra/deploy/` (it was masking a real bug: `platform_services` and
  `observability` modules pointed chart paths at `../../../deploy/...`,
  which only resolved because the duplicate existed — fixed to
  `../../deploy/...`), the `scripts/` symlink farm, the root duplicate of
  the human guide, and stale `.dockerignore` exceptions. `SPEC.md`,
  `archive/`, viva prep, and `PROGRESS.md` history intentionally keep their
  OpenTofu/AWS wording as build records.

## 2026-10-13 — Straight AKS port, VM-host design retired

- **Correction:** the "Azure VM running kind" design preserved zero cloud
  architecture (a VM pretending to be a laptop) and contradicted the
  SPEC P3.6/P9.5 straight-port intent. Retired: the VM `main.tf`,
  `setup_azure_vm.sh`, and the rsync-based `deploy_azure.sh`.
- **Restored the six `*_azure` modules** deleted in the earlier compaction
  sweep (`network_azure`, `cluster_aks`, `object_storage_azure`,
  `database_azure`, `image_registry_azure`, `iam_azure`) and composed them
  in `infra/envs/azure` mirroring the old AWS env shape: VNet (aks/db/
  public subnets + NSG) → ACR → AKS (OIDC + workload identity, AcrPull) →
  Blob account (mlflow-artifacts/feast-offline/ml-data) → Postgres Flexible
  B1ms (private DNS, generated password) → workload identities (Blob
  Reader/Contributor) + $50 consumption budget with email alerts.
- **Honest seam:** MinIO stays in-cluster on AKS for the Feast offline
  store (Feast has no native Blob backend) and MLflow artifacts move to
  Blob as a follow-up (needs the Azure artifact extras in the mlflow
  image). Everything else is native. Chart extended minimally with
  `serviceAccount.labels` so the workload-identity `use: "true"` label
  renders; `values-azure.yaml` carries ACR repo, LoadBalancer, WI
  annotation, and the Flexible-Server secret.
- **State note:** the live `infra/envs/azure` state still tracks the old
  deallocated VM RG. The next human `apply` will destroy those orphaned
  resources and create the AKS stack (`mlp-dev-rg`) — review the plan
  before approving.

## 2026-10-13 — Clean-room startup fixes: hadolint DL4006 + reset_demo baseline fallback

- **Decision 1:** add `# hadolint ignore=DL4006` (with reason) to the
  curl-into-tar `RUN` in `infra/docker/retrain.Dockerfile` instead of
  restructuring the command. A curl failure yields empty stdin, so tar
  exits non-zero and the `&&` chain stops — failure already propagates,
  making the rule a false positive here. Follows the file's existing
  ignore-comment convention.
- **Reason 1:** `scripts/deploy_monitor.sh` runs hadolint as a gate;
  the newer local hadolint exits 1 on DL4006, which broke `make deploy-all`
  at step 6 on a clean checkout.
- **Decision 2:** `scripts/reset_demo.py` default baseline back to `"2"`
  (matches its own docstring) plus a `_resolve_version()` fallback: if the
  requested version is absent from the registry (404), reset uses the live
  `champion` version. On a clean-room rebuild the registry holds only v1+v2,
  so the previously hardcoded `v4` 404'd and broke `make deploy-all` step 7.
- **Reason 2:** keeps `make down && make up && make deploy-all` green from
  zero state (P9.2 acceptance) without changing reset semantics on iterated
  registries, where v2 still exists and is used as before.

## 2026-10-13 — Azure move: training on AKS, 4-vCPU sizing, kill-switch

- **Decision:** full Azure path runs the SAME charts/Jobs as Kind on AKS;
  only image references change (`localhost:5001/*` → ACR via new
  `values-azure.yaml` overlays for mlflow/retrain/drift-monitor plus the
  existing model-api one). New `infra/jobs/train-job-azure.yaml`
  sizes training for 2-vCPU student nodes (500m/1Gi req, 1900m/3Gi lim;
  local 4/4Gi limits would overcommit a B2s). New scripts:
  `azure_preflight.sh` (read-only quota/SKU/login gate),
  `deploy_azure_full.sh` (infra → ACR images → data layer → Feast →
  train v1+v2 on AKS → serving + monitor), `train_azure.sh`,
  `azure_compute.sh sleep|wake` (`az aks stop/start`).
- **Reason:** human directive — local Kind proved functionality; the cloud
  is the target, and training must happen on AKS, not the laptop.
- **Kill-switch semantics:** `sleep` (stop) zeroes VM billing and keeps
  EVERYTHING (PVCs hold postgres/MLflow lineage, MinIO artifacts, Redis;
  ACR/Blob/Flexible persist) — this is the "registry stays" answer. Destroy
  `-target=module.cluster` is the deeper cut (in-cluster state lost,
  rehydrate via full deploy); `destroy_azure.sh` zeroes all billing.
- **Edu sizing:** 2 nodes × 2 vCPU = 4 vCPU cap exactly; terraform
  validates `node_count` 1–2 and bans 1 GB SKUs (B2ats_v2/B2pts_v2 break
  AKS kubelets — verified via upstream issue). B2s default with B2as_v2
  (2/8GB) documented fallback for retiring regions. Student quotas cannot
  be raised, so preflight fails fast instead.

## 2026-10-13 — One deployment implementation and cloud-hosted automation

Replace three laptop workflows with two GitHub-hosted workflows. One 148-line
Azure script replaces five separate lifecycle/deployment scripts; one Dockerfile
reuses common stages for six image targets; one training Job and script support
both registries. Remove three image-only Azure overlays and the duplicate train
manifest. Keep cloud release images/tag/config explicit in retrain Jobs so
staging/prod never reset to localhost registries or NodePort production service.
The local UI/demo harness remains functional and cloud UI hosting is the next
Azure phase. Consolidate duplicate Azure architecture/runbook material into the
port guide, preserving topology, state/identity setup, and honest storage seams.

GitHub's private-repository plan rejected required-reviewer protection (HTTP
422). Use a main-only environment and manual workflow selection with explicit
uppercase confirmation for deploy/wake/destroy; never claim reviewer protection
exists. Deployment requires successful CI for the exact commit. Optional model
repository dispatches only launch the existing in-cluster release path. Terraform
uses private Blob backend state rather than ephemeral runner files or public
artifacts. Environment identities/backend still require the human Azure bootstrap.

Fix parity tests' dependence on an ignored laptop data file by using deterministic
synthetic Parquet encoding fixtures; retain captured real Feast bytes and all
bit-exact assertions. All real-data training tests remain unchanged. Remove unused
private synthetic-label generation helpers; real UCI data generation is retained.
Runtime environments install from frozen locks. Helm 3 is retained deliberately
for the proven atomic-deploy semantics while other new CI tools/actions use latest
stable versions verified from official release/registry sources.

A clean checkout also exposed a pre-existing ignore-rule bug: `data/` hid
`core/ml/data/`, leaving its three source files untracked. Anchor generated-data
ignores at repository root and narrow Ruff's generated-directory exclusions so
the package is both versioned and linted. Vendor stable Kubernetes JSON schemas
in one compressed bundle to keep strict checks independent of live schema
downloads; operator schemas still come from the exact chart being installed.

The model-api image scan identified five critical Debian 12 findings, including
unfixed SQLite/zlib packages. Use the official digest-pinned Python 3.11.17 slim
Trixie image (Debian 13), whose base scan has zero critical findings, and retain
the model-api image vulnerability gate. Do not add blanket vulnerability ignores
or relax the security acceptance check. All six targets must rebuild/import-smoke
and model-api must pass the full image scan on the new base. Python remains 3.11.

Keep the mandatory ACR attachment's count static so a fresh Terraform plan does
not depend on a registry ID that is only known after creation. Preserve its
indexed resource address. Give AKS a service CIDR outside the VNet address space
instead of relying on overlapping Azure defaults. Azure execution is still
unverified and reserved for the next cloud phase.

Actual GitHub CI exposed a startup-clock bug in the remediator. A missing alert
fingerprint must not be represented as a timestamp of zero: fresh nodes with
monotonic uptime below 120 seconds otherwise ignore their first alerts. Require
a previous entry explicitly; test zero uptime, repeated alerts, and exact debounce
expiry without altering the existing action expectations. This fixes runtime
behavior, rather than waiting for runner uptime or skipping the failing tests.

## 2026-10-13 — Delete obsolete files and remove wrapper directories

Decision: delete eight archived planning documents, the redundant code map,
obsolete act config, unused performance placeholder, duplicate Kind config, and
three managed Azure storage/identity modules. None supports the working runtime.
Keep active demo evidence, the human-provided viva guide, tests, locks, and the
local functional harness. Git history retains deleted material.

Move docs/scripts/UI to their named root directories, charts to infra/charts,
job manifests to infra/jobs, vendored archives to infra/vendor, and Dockerfile
to infra/Dockerfile. Keep service/package boundaries and required Helm layouts.
Use the same release-script/chart paths in containers and the repository, which
lets us delete obsolete fallback logic rather than maintain two layouts.

Reason: the user requested deletion and fewer directory layers while preserving
functionality, not additional file concatenation. Managed Blob/PostgreSQL and
workload identities were never connected to the runtime; dropping their planned
resources preserves MinIO/PostgreSQL behavior and avoids redundant provisioning.
No Azure apply is run; existing local state and user documents are untouched.

## 2026-10-13 — Full test-session teardown and two-hour expiry

The user explicitly overrides AGENTS rule 12 for this Azure test deployment
and chooses automatic deletion after two hours. Implement `kill` before any
Terraform initialization, validate repository ownership of every resource group,
include the AKS node group and separately owned state storage group, and require
all groups to be absent before reporting success. Retain `destroy` as an app-only
operation; it cannot promise zero ongoing project cost because it retains state.

Use one small scheduled workflow for expiry (every 15 minutes, best effort).
It disables itself when no session exists or teardown finishes; startup re-enables
it. Start a deadline at provisioning and renew it to two hours after successful
startup, so failed startup is also covered. Retain the manual kill operation
for immediate teardown. Deletion permanently loses test data/models/history.
Keep unrelated Azure resources and the no-cost GitHub OIDC identity untouched.

## 2026-10-13 — AKS dashboard and supported test-node topology

Use one Standard_D4as_v4 system node (4 vCPU/16 GiB): live regional SKU and
quota APIs confirm availability and exactly four family vCPUs. The old B-series
plan is unsupported for AKS system nodes. Keep two model API replicas; this
temporary single-node session provides pod resilience, not VM-level HA. Pin
AKS to the latest creatable stable regional patch 1.36.4. Use Azure CNI overlay
with Cilium and grant the actual control-plane identity Network Contributor
before cluster creation, including access to the shared public IP.

Serve the existing dashboard as one extra target of the same Dockerfile, with
all dependencies frozen. Fetch its datasets and baseline model from MinIO/MLflow
at startup; no model/data baked into images. Preserve demo commands with
in-cluster endpoints and cloud image tags. Require dashboard login over HTTPS
with a temporary self-signed certificate; scope external tools to the configured
tester network and deployment runner. Share one static public IP across distinct
service ports. One compact dashboard manifest holds its required Kubernetes
resources; validate it alongside chart renders. Local operation remains available.

## 2026-10-13 — GitHub identity under university tenant restrictions

The connected Owner account was denied Entra application registration by the
university tenant. Use an Azure user-assigned managed identity with a GitHub
federated credential instead (supported for subscription Contributor/Owner).
Keep it in a separately tagged identity-only group so full kill retains the
no-cost login identity and future GitHub startup works. Microsoft states managed
identities have no additional cost. Billable app/node/state groups are deleted.
No client password or long-lived GitHub Azure secret is created.

# MLPlatform — Implementation Spec (Open-Source First, AWS Last)

> **Audience:** an autonomous coding agent (opencode / Codex / Antigravity / any free model) and the human supervising it.
> **Goal:** build the entire MLPlatform project locally using only open-source software, prove every acceptance test, and only then port to AWS.
> **Style of this document:** imperative, numbered, verifiable. Every task has an ID and an acceptance check. If a task has no passing check, it is not done.

---

## 0. How to use this document

**Human:**
1. Create an empty private GitHub repo named `mlplatform`. (Private matters: a self-hosted CI runner on a public repo is a security risk.)
2. Clone it, drop this file at `docs/SPEC.md`.
3. Install prerequisites (Section 5.1).
4. Paste the **Kickoff Prompt** below into your agent. Let it run. Check `PROGRESS.md` periodically.
5. Anything that costs money or needs your credentials (AWS apply, GitHub tokens) is marked **[HUMAN]**. The agent must stop and ask at those points and continue with other tasks meanwhile.

### Kickoff Prompt (paste verbatim)

```
You are the sole engineer on the repo in the current directory. Your specification is docs/SPEC.md.
1. Read Sections 1, 2, 3, 5 and 6 of docs/SPEC.md fully. Then copy Section 1 verbatim into AGENTS.md at repo root.
2. Create TASKS.md containing every task ID from Section 7 as an unchecked checkbox, grouped by phase. Create PROGRESS.md, BLOCKERS.md, docs/DECISIONS.md, docs/VERSIONS.md (empty with headings).
3. Execute phases strictly in order (P0 -> P10). For each phase read ONLY that phase's section when you start it.
4. A task is done only when its Acceptance check passes and you have pasted the real command output summary into PROGRESS.md. Never claim a pass you did not observe.
5. Commit after every task (Conventional Commits). Never commit secrets.
6. If blocked for more than 2 attempts, write the problem to BLOCKERS.md and move to the next unblocked task. Stop only for [HUMAN] steps or when all remaining tasks are blocked.
7. Continue until every box in TASKS.md is checked or blocked, then print the Definition-of-Done checklist (Section 8) with evidence.
Begin with P0.
```

---

## 1. Agent Operating Contract (copy to `AGENTS.md`)

1. **Spec is law.** If the spec is ambiguous, choose the simplest option that satisfies the acceptance check, record it in `docs/DECISIONS.md` (date, decision, reason), and proceed. Do not ask the human for things you can decide.
2. **One task at a time.** Finish, verify, commit, tick the box in `TASKS.md`, append to `PROGRESS.md`, then start the next.
3. **Verify, don't assume.** Run the acceptance command. Paste a 3–10 line summary of real output into `PROGRESS.md`. If the command fails, fix it; do not edit the acceptance check to make it pass.
4. **Pin everything.** At implementation time look up the latest *stable* version of each tool/chart/image/Python package from its official source, pin it exactly (no `latest`, no floating ranges in lockfiles), and record it in `docs/VERSIONS.md`. Your training data about versions may be stale; the registries are the truth.
5. **Makefile is the interface.** Every repeatable action is a `make` target. README documents targets, not raw commands.
6. **Idempotent and re-runnable.** `make up` twice must not break. Scripts must be safe to rerun. `make down && make up` must rebuild everything from scratch with zero manual steps (this is itself a P3 acceptance test).
7. **No secrets in git.** Credentials come from `.env` (gitignored) → OpenTofu `random_password` / Kubernetes Secrets. Provide `.env.example`. Add a secret-scan step (gitleaks) to CI.
8. **Small files, clear names, type hints, docstrings** on public functions. Python 3.11. Lint with `ruff`, format with `ruff format`, test with `pytest`. Target ≥ 80% line coverage on the runtime Python packages (`mlp_common/`, `model_api/`, and later `drift_monitor/`, `retrain/`, `remediator/`).
9. **Structured logging.** All Python services log JSON lines to stdout with fields: `ts, level, service, request_id, msg` plus context fields.
10. **Fail loudly.** No bare `except:`; no swallowed errors. Services must exit non-zero on unrecoverable startup errors so Kubernetes can restart them.
11. **Resource-conscious.** Target machine: 8 CPU / 16 GB RAM laptop. Set requests/limits on every pod. Prefer single-replica for infra components locally. If the cluster is starved, reduce replicas of infra components, not of `model-api`.
12. **Cost safety.** The agent MUST NOT run `tofu apply` / `terraform apply` against AWS or any command that creates billable resources. Only `init`, `validate`, `plan` are allowed for `infra/envs/aws`. Applying is **[HUMAN]**.
13. **Context hygiene.** If your context is getting long: update `PROGRESS.md` with "Next action: …", then continue. On resume, read `AGENTS.md`, `TASKS.md`, the tail of `PROGRESS.md`, and the current phase only.
14. **Don't gold-plate.** Anything marked *stretch* is done only after all must-haves in all phases pass.
15. **Honesty over polish.** If something is simulated (e.g., synthetic data, simulated drift), say so in docs. Never present simulated results as real.

---

## 2. Scope and Open-Source Substitution Table

The synopsis names AWS services. Phase P0–P8 use the open-source column. P9 ports to AWS using the same interfaces.

| Synopsis (AWS)            | Local open-source substitute (default)                                  | Why / fallback |
|---------------------------|--------------------------------------------------------------------------|----------------|
| EKS                       | **kind** (Kubernetes-in-Docker), 1 control-plane + 2 workers             | Multi-node needed to show rescheduling. Fallback: minikube. |
| Terraform                 | **OpenTofu** (open-source Terraform fork, same HCL)                      | Terraform is BSL-licensed; OpenTofu is MPL/Linux Foundation. Commands identical (`tofu`). |
| ECR                       | **kind-registry** (Docker `registry:2` on `localhost:5001`)              | Follow kind's local-registry docs. Fallback: `kind load docker-image`. |
| S3                        | **MinIO** (S3-compatible)                                                | Same S3 API → same code works on AWS by removing the endpoint override. *Check MinIO image availability/licensing at implementation time; fallback SeaweedFS (S3 gateway) or Garage.* LocalStack is mentioned in the synopsis but its free tier has been restricted — document this in DECISIONS.md. |
| RDS PostgreSQL            | **PostgreSQL 16** official image, StatefulSet via our own small Helm chart | Avoid Bitnami charts (catalog changes). |
| ElastiCache (optional)    | **Redis 7.2-alpine** (last BSD-licensed line) — or Valkey as drop-in      | Feast Redis online store. |
| IAM / IRSA                | Kubernetes ServiceAccounts + RBAC + static MinIO creds                   | AWS port swaps static creds for IRSA. |
| GitHub Actions (hosted)   | **GitHub Actions + self-hosted runner on your laptop** (runner is OSS) + `act` for quick local dry-runs | Hosted runners cannot reach a laptop cluster. |
| CloudWatch (not used)     | Prometheus, Grafana, Loki, Alertmanager, Grafana Alloy                    | Promtail is deprecated/EOL → use **Grafana Alloy** as the log shipper. |
| Load balancer             | kind `extraPortMappings` + NodePort                                      | **Do not use ingress-nginx** (project retired). |

**Non-goals:** multi-region, service mesh, GPU serving, hyperparameter search, a UI frontend. A minimal CLI/`curl` demo is enough.

---

## 3. Architecture

### 3.1 Component diagram

```
                       ┌────────────────────── GitHub (private repo) ──────────────────────┐
                       │  ci.yml (hosted)   cd.yml (self-hosted)   model-release.yml        │
                       └───────────▲──────────────────────────▲─────────────────▲──────────┘
                                   │push/PR                    │ repository_dispatch (model-candidate-ready)
                                   │                           │                 │
 Developer ──git push──────────────┘                           │                 │
                                                               ▼                 │
                                         Self-hosted runner (laptop: docker, kubectl, helm, python)
                                               │ build/push            │ helm upgrade
                                               ▼                       ▼
                                    localhost:5001 registry     ┌───────────── kind cluster ──────────────┐
                                                                │ ns serving       ns mlops       ns data   │
 curl / traffic.py ──NodePort 8080──► model-api (2..6 pods,HPA) │ model-api ──►    mlflow  ◄──┐   postgres   │
                                       │  ▲  │ pred-log batches │ model-api-stg    drift-monitor│  minio(S3)  │
                                       │  │  └──────────────────┼────────────────────►│        │  redis      │
                                       │  └─Feast online (redis)│  retrain Job ◄──────┘        │            │
                                       │                        │   ▲ creates Job (k8s API)     │            │
                                       ▼                        │   └── drift-monitor           │            │
                                  /metrics  JSON logs           │ ns observability                           │
                                       │        │               │ prometheus, alertmanager, grafana, loki,   │
                                       └────────┴──────────────►│ alloy, metrics-server, remediator          │
                                                                └────────────────────────────────────────────┘
```

### 3.2 Three key flows

**Flow A — Serving (request path).** Client → `POST /predict {customer_ids}` → `model-api` fetches online features from Feast (Redis) → runs the MLflow-registered model (loaded at pod start from a *pinned version*) → returns probabilities → enqueues `(request_id, ts, model_version, customer_id, features, score)` to an in-memory buffer flushed in batches to Postgres `prediction_log`. Prometheus scrapes `/metrics`; Alloy ships JSON logs to Loki.

**Flow B — Infrastructure self-healing.** Pod crash / node loss → ReplicaSet controller recreates pods (Kubernetes reconciliation). Load spike → HPA adds replicas. Alert rule fires (e.g., `HighP95Latency`) → Alertmanager webhook → `remediator` service performs a visible action (scale up, Grafana annotation, log line) and records it.

**Flow C — Model self-healing (closed loop).**
1. `drift-monitor` periodically compares recent `prediction_log` features against the **reference distribution stored in the serving model's MLflow run** (PSI + KS).
2. Breach for N consecutive evaluations (and cooldown elapsed, no retrain running) → creates a Kubernetes **retrain Job**.
3. Retrain Job: pulls training data with Feast point-in-time-correct `get_historical_features`, trains, evaluates on a temporal hold-out, logs everything to MLflow, registers new version with alias `candidate`, then fires GitHub `repository_dispatch` `model-candidate-ready {version}`.
4. `model-release.yml` (same CI/CD system as code): **gate** (AUC floor, no regression vs champion on same hold-out, schema compatibility) → deploy to staging release → latency/smoke test → promote alias `champion` → helm-upgrade prod with `model.version=<N>` → verify → auto-rollback on failure.
5. New version's reference distribution matches new data → drift metric returns to normal → loop closes.

### 3.3 Design decisions to implement as stated

- **Models are pinned by version number in Helm values**, not floating on an alias. The alias `champion` records intent; the Deployment is reproducible. (Viva-worthy: immutable deploys.)
- **Reference distribution is tied to model version** (stored as artifact `reference.parquet` in the model's run). This prevents retrain loops.
- **Interfaces first.** `FeatureSource` and `ModelSource` protocols let Phase 1 run with a Parquet/joblib stub and Phase 6 swap in Feast/MLflow with no API changes.
- **Same CI/CD path for code and models.** Both end in the same `deploy.sh` (helm upgrade + verify + rollback).
- **MLflow aliases, not stages** (stages are deprecated). Use aliases `champion`, `candidate`.

---

## 4. Repository Layout

Flat, single-project mono-repo: one `pyproject.toml`, one top-level package per Python
import root, one `tests/` tree, and one `docker/` directory. Import roots stay
`mlp_common`, `ml`, `model_api`, `drift_monitor`, `retrain`, `remediator` (Section 5.5).
Helm charts and OpenTofu modules are created in their owning phases.

```
mlplatform/
├─ AGENTS.md  TASKS.md  PROGRESS.md  BLOCKERS.md  README.md  Makefile  .env.example  .gitignore
├─ pyproject.toml              # single project: dependencies, ruff, pytest, coverage
├─ docs/  SPEC.md  DECISIONS.md  VERSIONS.md  architecture.md  runbook.md  git-workflow.md  demo/  viva/
├─ mlp_common/  {__init__,logging,config,schemas,drift,s3}.py   # shared logging/config/schemas/drift/s3
├─ ml/                         # model development
│   ├─ data/{generate,schemas}.py   # synthetic data generator (seeded) + column contracts
│   ├─ train.py                # file mode in P1, MLflow+Feast mode in P6
│   └─ evaluate.py
├─ model_api/       {main,deps,predlog,metrics,chaos}.py  providers/{base,stub,mlflow_model,feast_features}.py
├─ drift_monitor/   {main,detector,trigger,perf}.py
├─ retrain/         {main,register,dispatch}.py
├─ remediator/      {main,actions}.py
├─ feature_repo/    feature_store.yaml  definitions.py  materialize.py   # Feast repo (P6)
├─ docker/          {model_api,drift_monitor,retrain,remediator,mlflow}.Dockerfile
├─ deploy/charts/   # Helm charts, created in their owning phases
├─ infra/           # OpenTofu: modules/ + envs/{local,aws}/
├─ scripts/ {registry.sh, bootstrap.sh, deploy.sh, gate.py, traffic.py, simulate_drift.py, reset_demo.py, smoke.sh, chaos_pod.sh, chaos_load.sh}
├─ tests/  # unit tests + e2e/ {test_serving.py, test_selfheal_infra.py, test_selfheal_model.py}
└─ .github/workflows/ {ci.yml, cd.yml, model-release.yml}
```

---

## 5. Global Conventions

### 5.1 Prerequisites (human installs; agent verifies in P0)
Docker (≥ 24, with ≥ 10 GB RAM available to it), `kind`, `kubectl`, `helm` (3.x), `tofu` (OpenTofu), `python3.11` + `uv` or `pip`, `make`, `git`, `gh` CLI (optional), `hey` or `k6` (load tool; `hey` is simplest), `act` (optional), `jq`, `yq`.

### 5.2 Namespaces
`data` (postgres, minio, redis) · `mlops` (mlflow, drift-monitor, retrain jobs) · `serving` (model-api, model-api-staging) · `observability` (prom stack, loki, alloy, remediator).

### 5.3 Host ports (kind `extraPortMappings`, NodePort → localhost)
| Service | NodePort | Host URL |
|---|---|---|
| model-api (prod) | 30080 | http://localhost:8080 |
| model-api-staging | 30081 | http://localhost:8081 |
| Grafana | 30030 | http://localhost:3000 |
| MLflow | 30500 | http://localhost:5000 |
| MinIO console | 30901 | http://localhost:9001 |
| MinIO S3 API | 30900 | http://localhost:9000 |
| Prometheus | 30090 | http://localhost:9090 |
| Alertmanager | 30093 | http://localhost:9093 |

Registry: `localhost:5001` from host, `kind-registry:5000` from inside cluster nodes (use containerd `hosts.toml` per kind docs). Image refs in charts use `localhost:5001/<name>:<tag>`.

### 5.4 Environment variable contract (all services read via `mlp_common.config`, pydantic-settings)
| Var | Used by | Example / default |
|---|---|---|
| `SERVICE_NAME` | all | `model-api` |
| `LOG_LEVEL` | all | `INFO` |
| `MODEL_NAME` | api, retrain, gate | `loan-default` |
| `MODEL_VERSION` | api | `1` (pinned; required in P6+) |
| `MODEL_SOURCE` | api | `file` (P1–P5) / `mlflow` (P6+) |
| `FEATURE_SOURCE` | api | `parquet` / `feast` |
| `MLFLOW_TRACKING_URI` | api, retrain, gate | `http://mlflow.mlops.svc:5000` |
| `MLFLOW_S3_ENDPOINT_URL` | mlflow clients | `http://minio.data.svc:9000` |
| `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` | S3 clients | from Secret (MinIO creds locally) |
| `FEAST_S3_ENDPOINT_URL` | feast | same as MinIO endpoint |
| `DATABASE_URL` | api, drift, retrain | `postgresql+psycopg://mlp:***@postgres.data.svc:5432/mlp` |
| `REDIS_URL` | feast | `redis.data.svc:6379` |
| `ENABLE_CHAOS` | api | `false` (true only in local/demo) |
| `DRIFT_INTERVAL_SECONDS` | drift | `60` |
| `DRIFT_WINDOW_SIZE` | drift | `1000` |
| `DRIFT_MIN_SAMPLES` | drift | `500` |
| `DRIFT_PSI_THRESHOLD` | drift | `0.25` |
| `DRIFT_KS_D_THRESHOLD` / `DRIFT_KS_P` | drift | `0.2` / `0.01` |
| `DRIFT_MIN_FEATURES` | drift | `2` |
| `DRIFT_CONSECUTIVE` | drift | `2` |
| `RETRAIN_COOLDOWN_SECONDS` | drift | `300` (demo) |
| `GITHUB_REPO`, `GITHUB_DISPATCH_TOKEN` | retrain | `user/mlplatform`, fine-grained PAT (**[HUMAN]** creates; contents:write) |
| `TRIGGER_MODE` | retrain | `github` or `local` (local = call release script directly; fallback) |

### 5.5 Naming
Helm releases: `model-api`, `model-api-staging`, `drift-monitor`, `remediator`, `mlflow`, `postgres`, `minio`, `redis`. Images: `localhost:5001/{model-api,drift-monitor,retrain,remediator,mlflow}:<git-sha>`. Python import roots: `model_api`, `drift_monitor`, `retrain`, `remediator`, `mlp_common`.

---

## 6. Domain Specification (the ML problem)

The synopsis says "reuse existing models". Default to this **synthetic loan-default** problem because it gives full control over drift. If the team has its own model, swap it in behind the same interfaces (document in DECISIONS.md) but keep the synthetic generator for drift simulation.

### 6.1 Entities & features
Entity `customer` (join key `customer_id`, int, 0–19999). Feature view `customer_features` with `event_timestamp`:

| Feature | Distribution (baseline) | Note |
|---|---|---|
| `income` | lognormal(μ=10.8, σ=0.5) | annual |
| `debt_to_income` | beta(2,5)·1.0 | 0–1 |
| `credit_history_months` | gamma(shape=4, scale=30) clipped 6–480 | |
| `num_late_payments_90d` | poisson(0.6) | int |
| `avg_monthly_balance` | lognormal(8.0, 0.8) | |
| `utilization_ratio` | beta(2,3) | 0–1 |

Generate 20,000 customers × 4 weekly snapshots (80k rows), timestamps ending yesterday (relative to generation time; seeded RNG, seed=42).

### 6.2 Label
`default_next_90d ∈ {0,1}` from logistic: `z = b0 + w·standardize(features) + ε`, ε~N(0,0.5). Choose coefficients `w` so that (a) base rate ≈ 10–14% and (b) a gradient-boosted model reaches **hold-out AUC between 0.82 and 0.92**. Directions: income −, debt_to_income +, credit_history −, late_payments +, balance −, utilization +. Unit test asserts both properties. Labels table: `(customer_id, event_timestamp, default_next_90d)`.

### 6.3 Model
`sklearn` `HistGradientBoostingClassifier` (random_state fixed). Temporal split: oldest 70% train, next 15% validation, newest 15% test. Metrics logged: AUC, PR-AUC, log-loss, Brier, accuracy@0.5, positive rate. Model signature and input example are logged to MLflow.

### 6.4 Drift scenarios (`scripts/simulate_drift.py --scenario {covariate,concept,both}`)
- **covariate:** shift feature distributions: income × 0.75, debt_to_income + 0.15 (clip 1), late_payments λ 0.6→1.4, utilization + 0.15. Writes a new snapshot (timestamp = now) for all customers to offline Parquet, materializes to Redis.
- **concept:** change label mechanism (e.g., flip sign/weaken `credit_history`, strengthen `utilization`) for newly generated labels, so the old model's AUC visibly drops.
- **both (default demo):** both of the above. Champion AUC on new data should drop by ≥ 0.07 (assert in script output).
- `scripts/reset_demo.py`: restores baseline snapshot, sets `champion` back to v1, redeploys v1, truncates `prediction_log`, clears drift state. Required so the demo can be rerun.

### 6.5 API contract (`model-api`, FastAPI)
- `POST /predict` body `{"customer_ids":[int,…]}` (1–100). 200 → `{"request_id":str,"model_version":str,"predictions":[{"customer_id":int,"default_probability":float,"label":0|1}]}`. 404 `{"error":"unknown_customers","ids":[…]}` if features missing. 422 on validation. 503 if not ready.
- `GET /healthz` (process alive; used by liveness) · `GET /readyz` (model loaded AND feature source reachable; readiness) · `GET /metrics` (Prometheus) · `GET /model-info` (name, version, source, loaded_at, feature list).
- Chaos (only when `ENABLE_CHAOS=true`): `POST /debug/crash` (calls `os._exit(1)` after responding 202), `POST /debug/latency?ms=N` (adds artificial sleep to /predict for this pod until reset with `ms=0`).
- Metrics: `http_requests_total{method,path,status}`, `http_request_duration_seconds` histogram (buckets 5ms…2.5s), `predictions_total{model_version,label}`, `model_info{model_name,model_version} 1`, `feature_fetch_duration_seconds`, `predlog_buffer_size`, `predlog_flush_failures_total`.

---

## 7. Phases and Tasks

Legend: **[M]** must-have · **[S]** stretch · **[HUMAN]** requires human. Mapping to synopsis timeline in parentheses.

---

### P0 — Foundations (Week 1)
**Goal:** repo, tooling, quality gates, shared library skeleton.

- **P0.1 [M]** Create repo layout (Section 4) with placeholder files, `.gitignore` (python, `.env`, `*.tfstate*`, `.terraform/`, `.tofu*`, `data/*.parquet`, `mlruns/`), `.env.example`, `README.md` stub.
- **P0.2 [M]** `Makefile` with targets: `help, bootstrap, lint, fmt, test, build, up, down, deploy, forward, seed-data, train, traffic, demo-infra, demo-model, reset-demo, tofu-local-apply, tofu-local-destroy`. Unimplemented targets echo "TODO(P#)" and exit 0.
- **P0.3 [M]** `scripts/bootstrap.sh`: verifies every prerequisite binary and prints versions into `docs/VERSIONS.md`; exits non-zero listing missing tools.
- **P0.4 [M]** `libs/mlp_common`: `logging.py` (JSON formatter, request-id contextvar), `config.py` (pydantic-settings base), `schemas.py` (pydantic models for API contract). Packaged with `pyproject.toml`; installed editable by every service.
- **P0.5 [M]** Tooling config: `ruff` (lint+format), `pytest`, `pre-commit` (ruff, gitleaks, end-of-file-fixer).
- **P0.6 [M]** Git workflow: branches `main` (protected by convention), feature branches `feat/<phase>-<topic>`; Conventional Commits; PR template `.github/pull_request_template.md`.

**Acceptance:** `make bootstrap` exit 0 · `make lint test` exit 0 (smoke tests exist for `mlp_common`) · `git log --oneline` shows conventional commits.

---

### P1 — Data, Model, Serving Stub, Containers (Week 1)
**Goal:** a containerized FastAPI service serving a real trained model, using stub providers.

- **P1.1 [M]** `ml/data/generate.py` implementing Section 6.1–6.2. CLI: `python -m ml.data.generate --out data/ --seed 42`. Outputs `data/features.parquet`, `data/labels.parquet`, `data/features_latest.parquet` (latest snapshot per customer). Unit tests: shapes, no NaNs, base rate range, determinism with same seed.
- **P1.2 [M]** `ml/train.py --mode file`: temporal split, train, evaluate, write `artifacts/model.joblib`, `artifacts/metrics.json`, `artifacts/reference.parquet` (≤10k sampled training feature rows), `artifacts/feature_schema.json`. Test asserts AUC in [0.82, 0.92].
- **P1.3 [M]** `model_api` with provider protocols:
  ```python
  class FeatureSource(Protocol):
      def get(self, ids: list[int]) -> pd.DataFrame: ...   # index customer_id, columns = feature order
      def ping(self) -> bool: ...
  class ModelSource(Protocol):
      version: str
      def predict_proba(self, X: pd.DataFrame) -> np.ndarray: ...
  ```
  Implement `ParquetFeatureSource` (loads `features_latest.parquet` into memory) and `JoblibModelSource`. Wire by `FEATURE_SOURCE`/`MODEL_SOURCE`. Implement all endpoints in 6.5 (chaos included), metrics, request-id middleware, JSON logging.
- **P1.4 [M]** Prediction log: `predlog.py` — in-memory bounded queue (max 10k) + background task flushing batches (every 2 s or 200 rows) to Postgres via SQLAlchemy; **no-DB mode** (when `DATABASE_URL` unset) logs to stdout so P1–P2 work without Postgres. Never block the request path; on failure increment `predlog_flush_failures_total` and retry with backoff.
- **P1.5 [M]** Tests (pytest + FastAPI TestClient): happy path, 404 unknown ids, 422 bad payload, readiness false before model loaded, metrics exposed, chaos endpoints disabled by default (404) and enabled by env.
- **P1.6 [M]** `services/model_api/Dockerfile`: multi-stage, `python:3.11-slim`, non-root user, `HEALTHCHECK` omitted (K8s probes used), model/data baked **only in P1–P5** via `COPY artifacts/`; uvicorn with 1 worker per container (scale with pods), `--no-access-log` (we log ourselves).
- **P1.7 [M]** `make seed-data train build` produce an image `localhost:5001/model-api:dev` (tag locally even if registry not up yet). `hadolint` clean.

**Acceptance:**
```
make seed-data train build
docker run --rm -p 8080:8080 -e MODEL_SOURCE=file -e FEATURE_SOURCE=parquet localhost:5001/model-api:dev &
curl -s localhost:8080/readyz                 # 200
curl -s -XPOST localhost:8080/predict -H 'content-type: application/json' -d '{"customer_ids":[1,2,3]}' | jq .predictions   # 3 items, probs in [0,1]
curl -s localhost:8080/metrics | grep -c http_requests_total   # >=1
make test   # all green, coverage >= 80% for services/model_api
```

---

### P2 — Kubernetes, HPA, Fault Tolerance (Week 2)
**Goal:** the container runs on a multi-node cluster with autoscaling and demonstrable self-healing. (Cluster is created imperatively here; P3 re-creates it with IaC.)

- **P2.1 [M]** `scripts/registry.sh`: idempotently create `kind-registry` container (`registry:2`, `localhost:5001`), attach to `kind` network, publish `local-registry-hosting` ConfigMap — follow kind's *local registry* doc.
- **P2.2 [M]** `infra/kind-config.yaml` (temporary; later owned by OpenTofu): 1 control-plane + 2 workers, containerd registry patch, `extraPortMappings` per 5.3 (on the control-plane node; NodePorts are reachable cluster-wide).
- **P2.3 [M]** Install **metrics-server** (helm) with `--kubelet-insecure-tls` (required on kind). Verify `kubectl top nodes` works.
- **P2.4 [M]** Helm chart `deploy/charts/model-api`: Deployment (replicas 2; requests `cpu 100m, mem 256Mi`; limits `cpu 500m, mem 512Mi`), **startupProbe** `/healthz` (period 2s, failureThreshold 30), **livenessProbe** `/healthz`, **readinessProbe** `/readyz`, `securityContext` non-root + read-only root FS (tmp as emptyDir), preferred pod anti-affinity by hostname, `topologySpreadConstraints` across nodes, **PodDisruptionBudget** (`minAvailable: 1`), **HPA** (autoscaling/v2; CPU 60%, min 2, max 6, scaleDown stabilization 60s for demo), Service type NodePort (30080), optional `ServiceMonitor` (`serviceMonitor.enabled=false` for now), values for `env`, `model.version`, `image.tag`, `chaos.enabled`.
- **P2.5 [M]** `scripts/deploy.sh <release> <namespace> <image-tag> [extra helm args]`: `helm upgrade --install --atomic --wait --timeout 5m`, then `scripts/smoke.sh` (readyz + one predict). Reused by CI later. On failure exits non-zero (atomic helm rolls back).
- **P2.6 [M]** `scripts/chaos_pod.sh`: deletes a random model-api pod while `traffic.py` runs; prints observed error count and time-to-recovery. `scripts/chaos_load.sh`: runs `hey -z 3m -c 50 -m POST -T application/json -d '{"customer_ids":[1,2,3,4,5]}' http://localhost:8080/predict`, and watches `kubectl get hpa -w`.
- **P2.7 [M]** `scripts/traffic.py --rps N --duration S --pool baseline|drifted --url …`: asyncio + httpx, random customer ids, prints p50/p95/error-rate summary, exits non-zero if error rate > threshold (flag).
- **P2.8 [M]** Verify rolling update behavior: `strategy.rollingUpdate maxUnavailable=0, maxSurge=1`. Test: change image tag during traffic; error rate must be 0.

**Acceptance:**
```
kubectl get nodes                       # 3 Ready
make deploy                             # releases model-api in ns serving
curl localhost:8080/readyz              # 200 via NodePort
scripts/chaos_pod.sh                    # recovery < 30s; failed requests <= 2 (document number)
scripts/chaos_load.sh                   # HPA scales 2 -> >=4 within 3 min; scales back after
curl -XPOST localhost:8080/debug/crash  # (chaos enabled) pod restarts; restartCount increments; service stays available
```
Record a 60–90 s terminal capture (asciinema or screen recording) → `docs/infra-selfheal.*` (re-record in P9).

---

### P3 — Infrastructure as Code with OpenTofu (Week 3)
**Goal:** `make up` builds the entire local platform (cluster, registry, data layer) from scratch; AWS equivalent is written and plan-validated.

- **P3.1 [M]** `infra/modules/cluster_kind`: `tehcyx/kind` provider `kind_cluster` (node image pinned, 3 nodes, port mappings and containerd patch from P2.2). Outputs: `kubeconfig_path`, `cluster_name`.
- **P3.2 [M]** `infra/modules/registry`: `terraform_data` + `local-exec` running `scripts/registry.sh` (document why imperative; `triggers_replace` on script hash). Output `registry_url`.
- **P3.3 [M]** Own Helm charts `deploy/charts/{postgres,minio,redis}`:
  - postgres: StatefulSet (`postgres:16`), PVC 5Gi, Secret-fed creds, init SQL creating DBs `mlp` (app), `mlflow`, `feast`; table `prediction_log(id bigserial, ts timestamptz, request_id text, model_version text, customer_id int, features jsonb, score real)` + index `(model_version, ts)`; table `drift_state`; table `labels_log` (optional).
  - minio: Deployment (pinned image), PVC 5Gi, Secret creds, Service ClusterIP + NodePorts 30900/30901; **post-install Job** creating buckets: `mlflow-artifacts`, `feast-offline`, `ml-data`.
  - redis: Deployment `redis:7.2-alpine`, `--appendonly no` (online store is rebuildable), small resources.
- **P3.4 [M]** `infra/modules/platform_services`: `helm_release` for metrics-server, the three own charts (namespace `data`), with `random_password` resources feeding `kubernetes_secret`s. Order with `depends_on`.
- **P3.5 [M]** `infra/envs/local`: composes modules; `versions.tf` pins providers (`tehcyx/kind`, `hashicorp/helm`, `hashicorp/kubernetes`, `hashicorp/random`); outputs the contract: `{s3_endpoint, buckets, db_host, redis_host, registry_url, kubeconfig}`. State: local backend (gitignored). `make up` = `tofu -chdir=infra/envs/local init && apply -auto-approve` + `scripts/bootstrap-post.sh` (waits for readiness). `make down` = destroy + remove registry container.
- **P3.6 [M]** Module interfaces for AWS parity — create `infra/modules/{cluster_eks,object_storage,database,iam}` with the **same input/output names** as their local counterparts, implemented with the AWS provider and well-known community modules (`terraform-aws-modules/vpc`, `.../eks`): VPC (2 AZ, public+private subnets, **single NAT gateway** for cost), EKS (managed node group 2× `t3.medium`, IRSA enabled, EBS CSI addon), S3 buckets (versioned, SSE, block public access), RDS PostgreSQL (`db.t4g.micro`, single-AZ, private subnets, deletion protection off for demo teardown), ECR repositories (scan on push), IAM roles for IRSA (least privilege: model-api read-only on buckets; retrain/mlflow read-write), AWS Budget alarm. `infra/envs/aws` composes these. **Only `init -backend=false`, `validate`, and `plan` (with dummy credentials flag `-lock=false` if no creds) are allowed.** Run `tflint`/`tofu validate` in CI.
- **P3.7 [M]** `tofu fmt -check` and `tofu validate` for both envs in `ci.yml` (P4).
- **P3.8 [S]** `checkov` or `trivy config` scan of `infra/` in CI (report-only).

**Acceptance:**
```
make down; make up                      # from nothing to green, zero manual steps, < 15 min
kubectl get pods -A                     # data ns: postgres, minio, redis Running; bucket Job Completed
make tofu-local-apply                   # second run: "No changes"
tofu -chdir=infra/envs/aws validate     # Success
tofu -chdir=infra/envs/aws plan         # succeeds or fails ONLY for missing credentials; no syntax/type errors
psql (port-forward) -c '\dt'            # prediction_log exists
```

---

### P4 — CI/CD with GitHub Actions (Week 4)
**Goal:** every push is tested and built; main deploys automatically to the local cluster.

- **P4.1 [HUMAN]** Install a **self-hosted runner** on the laptop (Settings → Actions → Runners), label it `mlp-local`. Add repo secret `GITHUB_DISPATCH_TOKEN` later (P8). Agent writes `docs/runner-setup.md` with exact steps.
- **P4.2 [M]** `.github/workflows/ci.yml` (`runs-on: ubuntu-latest`; triggers PR + push): jobs `lint` (ruff, hadolint, yamllint), `test` (pytest + coverage gate 80%), `iac` (tofu fmt/validate both envs, tflint), `chart` (`helm lint` + `helm template | kubeconform`), `security` (gitleaks, `trivy fs` — fail on CRITICAL; `trivy image` after build in cd). Cache pip. Upload coverage artifact.
- **P4.3 [M]** `.github/workflows/cd.yml` (`runs-on: [self-hosted, mlp-local]`; trigger push to `main`, `needs` CI by re-running quick checks or via `workflow_run`): steps — checkout, build images changed (`docker buildx`, tag `${GITHUB_SHA::8}`), `trivy image` scan, push to `localhost:5001`, `scripts/deploy.sh model-api-staging serving <tag>` → smoke → `scripts/deploy.sh model-api serving <tag>` → smoke → print rollout summary. `concurrency: cd-main` (no overlapping deploys). On failure: `helm rollback` (atomic already), upload logs as artifact.
- **P4.4 [M]** Make model artifacts **not** baked into the image from here on: for P4–P5 mount from a `ConfigMap`/`emptyDir`+init container pulling from MinIO `ml-data` (`scripts/publish_model_file.sh` uploads `artifacts/` to MinIO). Replace the P1 `COPY artifacts/` path. (This prepares P6.)
- **P4.5 [M]** Branch discipline demo: open a PR changing a trivial thing → CI passes; merge → CD deploys; `kubectl get deploy model-api -o jsonpath='{.spec.template.spec.containers[0].image}'` shows new SHA.
- **P4.6 [M]** Failure-path test: intentionally break readiness in a branch (e.g., raise on startup) → CD staging deploy fails → prod untouched and still serving. Capture evidence in `docs/cicd-failure-path.md`.
- **P4.7 [S]** `act` config to dry-run `ci.yml` locally.

**Acceptance:** green `ci.yml` run URL recorded in PROGRESS.md · a merge to `main` produces a green `cd.yml` run and updated image tag · failure-path evidence shows prod unaffected.

---

### P5 — Observability and Automated Response (Weeks 5–6)
**Goal:** metrics, logs, dashboards, alerts, and a visible automated response.

- **P5.1 [M]** Install **kube-prometheus-stack** into `observability` via OpenTofu `helm_release` (values file `deploy/charts/observability/values-prometheus.yaml`): Grafana NodePort 30030, Prometheus 30090, Alertmanager 30093; reduced resources; retention 2d; `serviceMonitorSelectorNilUsesHelmValues=false` so ServiceMonitors from any namespace are discovered; Grafana sidecar enabled to load dashboards/datasources from ConfigMaps labeled `grafana_dashboard=1`. Admin password from Secret.
- **P5.2 [M]** Install **Loki** (grafana/loki chart, SingleBinary mode, filesystem storage, `auth_enabled: false`, 1 replica, small PVC) and **Grafana Alloy** (DaemonSet) shipping pod logs with labels `namespace, pod, container, app`. Add Loki as Grafana datasource. *Do not use Promtail (deprecated).*
- **P5.3 [M]** Enable `serviceMonitor.enabled=true` in `model-api` chart; verify target `UP` in Prometheus.
- **P5.4 [M]** Dashboards (JSON, provisioned via ConfigMap, version-controlled in `deploy/charts/observability/dashboards/`):
  1. **Service Health:** RPS, error rate, p50/p95/p99 latency, pod count, HPA desired/current, restarts, CPU/mem.
  2. **Logs:** Loki panel with `{namespace="serving"} | json` filtered by level and request_id; error-rate-from-logs panel.
  3. **ML Health** (stubs now; populated P8): model version in service, predictions by label, PSI per feature, drift flag, retrain count, candidate/champion AUC (from MLflow metrics via the retrain service gauge — or simple table).
- **P5.5 [M]** Alert rules (`PrometheusRule` CRD in `deploy/charts/observability/alerts/`):
  - `ModelApiDown`: `sum(up{job="model-api"})==0` for 1m → critical.
  - `PodCrashLooping`: `increase(kube_pod_container_status_restarts_total{namespace="serving"}[10m]) > 2`.
  - `HighErrorRate`: 5xx ratio > 5% for 2m.
  - `HighP95Latency`: `histogram_quantile(0.95, sum by (le)(rate(http_request_duration_seconds_bucket{path="/predict"}[2m]))) > 0.3` for 1m.
  - `ModelDriftDetected` (P8): `max(model_drift_detected) == 1` for 1m.
  - `RetrainFailed` (P8).
  Each has `severity`, `summary`, `runbook` annotation pointing to `docs/runbook.md#<alert>`.
- **P5.6 [M]** **Remediator service** (`services/remediator`, FastAPI): receives Alertmanager webhook v4 JSON at `POST /alert`. Action map:
  - `HighP95Latency` → scale `model-api` Deployment replicas +2 (cap = HPA max) *and* annotate Grafana; (note: HPA owns replicas — instead patch HPA `minReplicas` upward to enforce the floor; document the HPA-vs-manual-scale conflict in DECISIONS.md).
  - `PodCrashLooping` / `ModelApiDown` → create Grafana annotation, log `ACTION`, and if `ModelApiDown`, `kubectl rollout restart` equivalent via API.
  - `ModelDriftDetected` → annotation only (retrain is already automatic).
  Uses in-cluster ServiceAccount with minimal RBAC (patch deployments/hpa in `serving`). Idempotent; debounce (ignore same alert fingerprint within 2 min). Exposes `remediator_actions_total{alert,action,result}`. Alertmanager config routes all `severity` alerts to `http://remediator.observability.svc:8080/alert` (via `AlertmanagerConfig` or helm values).
  - Unit tests with mocked Kubernetes client.
- **P5.7 [M]** End-to-end alert drill `scripts/chaos_alert.sh`: enable latency chaos `POST /debug/latency?ms=500` on pods → traffic → `HighP95Latency` fires within ~3 min → remediator action executes → Grafana annotation visible on dashboard → reset latency. Document expected timeline.
- **P5.8 [S]** Loki-based alert (log pattern `level=ERROR` rate) via Loki ruler.

**Acceptance:**
```
Prometheus: all targets UP (model-api, kube-state-metrics, node-exporter)
Grafana (localhost:3000): 3 dashboards load with live data; Loki logs queryable by request_id
scripts/chaos_alert.sh: alert Firing in Alertmanager -> remediator log shows ACTION -> annotation on dashboard -> HPA min increased
kubectl logs deploy/remediator -n observability | grep ACTION   # evidence
```
Evidence screenshots → `docs/`.

---

### P6 — Feature Store and Model Registry (Week 7)
**Goal:** Feast guarantees identical feature computation for training and serving; MLflow tracks everything and serves model versions.

- **P6.1 [M]** **MLflow server**: `services/mlflow/Dockerfile` (`python:3.11-slim`, pip install pinned `mlflow`, `psycopg2-binary`, `boto3`); chart `deploy/charts/mlflow` (Deployment, Service NodePort 30500), args: `--backend-store-uri postgresql://…/mlflow --artifacts-destination s3://mlflow-artifacts --serve-artifacts --host 0.0.0.0` (artifact proxying means clients need no S3 creds), env `MLFLOW_S3_ENDPOINT_URL`, `AWS_*`. Deploy via OpenTofu (`platform_services`).
- **P6.2 [M]** **Feast repo** `feature_repo/`:
  - `feature_store.yaml`: `project: mlplatform`, `provider: local`, **registry: SQL registry on Postgres** (`registry_type: sql`, `path: postgresql+psycopg://…/feast`, `cache_ttl_seconds: 60`), **online store: redis** (`connection_string: redis.data.svc:6379`), **offline store: file** (Parquet on S3/MinIO via `FileSource(path="s3://feast-offline/customer_features.parquet", s3_endpoint_override=<MinIO>)`; set `FEAST_S3_ENDPOINT_URL`), `entity_key_serialization_version: 3`. Render this file from a template with env vars (don't commit creds).
  - `definitions.py`: `Entity(customer)`, `FileSource(timestamp_field="event_timestamp")`, `FeatureView("customer_features", entities=[customer], ttl=timedelta(days=365), schema=[Field(...)×6], source=...)`, `FeatureService("loan_default_v1", features=[customer_features])`.
  - `make feast-apply` (`feast apply`) and `make feast-materialize` (`feast materialize-incremental $(date -u +%FT%T)`), wrapped by a K8s Job for in-cluster use.
  - **Fallback** if S3 offline source proves flaky: offline Parquet on a PVC mounted into jobs; record in DECISIONS.md (S3 parity restored in P9).
- **P6.3 [M]** Upload `features.parquet` + `labels.parquet` to MinIO; `feast apply`; materialize; verify with a script that calls `get_online_features` for ids 1..5 and `get_historical_features` for a small entity_df.
- **P6.4 [M]** **Training via Feast + MLflow** (`ml/train.py --mode mlflow`): builds `entity_df` from `labels.parquet` (`customer_id, event_timestamp, label`), calls `store.get_historical_features(entity_df, features=FeatureService)` (**point-in-time correct join** — no leakage), temporal split, trains, then in one MLflow run logs: params (hyperparams, data window, feast feature service, git SHA), metrics, `reference.parquet` and `eval_set.parquet` artifacts, model via `mlflow.sklearn.log_model` with signature + input example, and registers as `loan-default` (alias `champion` for v1; `candidate` for later runs). Tags: `feast_feature_service`, `data_start`, `data_end`, `git_sha`, `trigger` (`manual|drift`).
- **P6.4b [M]** **Training–serving parity test** `tests/test_parity.py`: for N=200 random customers, features obtained via `get_historical_features` at the latest timestamp must equal `get_online_features` values (tolerance 1e-6). Failing this test = skew. (Core viva evidence.)
- **P6.5 [M]** `model_api` providers: `FeastFeatureSource` (online features, `ping()` = cheap Redis/registry check), `MlflowModelSource` (loads `models:/{MODEL_NAME}/{MODEL_VERSION}` at startup — *pinned version*; copy feature order from signature). Reads `MODEL_SOURCE=mlflow`, `FEATURE_SOURCE=feast`. Image now contains **no model and no data**. Readiness false until both loaded. Feast repo config mounted via ConfigMap + env creds. Fail startup (exit 1) after 60s of retries.
- **P6.6 [M]** Add `/model-info` fields: `mlflow_run_id`, `feature_service`, `registered_at`.
- **P6.7 [M]** Deploy with `model.version=1`; confirm Flow A works end-to-end (Feast→model→response→prediction_log rows appear).
- **P6.8 [M]** Lineage doc `docs/lineage.md`: how to trace a prediction → `model_version` → MLflow run → data window + feature service + git SHA. Include a worked example with real IDs.

**Acceptance:**
```
MLflow UI localhost:5000: model 'loan-default' v1 with alias champion, run has metrics + artifacts (reference.parquet, eval_set.parquet)
pytest tests/test_parity.py        # PASS
curl -XPOST localhost:8080/predict -d '{"customer_ids":[1,2,3]}'   # model_version "1"
psql: select count(*), model_version from prediction_log group by 2;   # rows present
kubectl delete pod -l app=redis -n data ; model-api readyz flips false then true after Redis returns (document behavior)
```

---

### P7 — Model-Aware CI/CD (Week 8)
**Goal:** a new model version can only reach production by passing automated gates through the same pipeline.

- **P7.1 [M]** `scripts/gate.py --candidate <version>`: loads candidate and current champion from MLflow; loads candidate run's `eval_set.parquet`; computes AUC for both on that **same** set; checks:
  1. `candidate_auc >= ABS_FLOOR` (0.75),
  2. `candidate_auc >= champion_auc - 0.01` (no regression),
  3. **signature compatibility**: candidate input feature names/dtypes ⊇ what the API/Feast service provides,
  4. positive-rate sanity (0.01 ≤ rate ≤ 0.5),
  5. model loads and predicts on 100 rows without error.
  Emits `gate_report.json` (all numbers + pass/fail) as artifact; exit code 0/1. Unit tests with synthetic models cover each failure mode.
- **P7.2 [M]** `scripts/loadcheck.py` (or `hey` wrapper): against staging URL, N=2000 requests @ 20 rps; fails if p95 > 150 ms or error rate > 0.5%.
- **P7.3 [M]** `.github/workflows/model-release.yml`: triggers `repository_dispatch: [model-candidate-ready]` (payload `version`) and `workflow_dispatch` (input `version`). `concurrency: model-release`. Self-hosted. Steps:
  1. checkout, setup python, install deps;
  2. `gate.py` → upload `gate_report.json`; on fail: tag version `gate_status=rejected` in MLflow, remove alias `candidate`, stop;
  3. `deploy.sh model-api-staging serving <current-image-tag> --set model.version=<N>`;
  4. smoke + `loadcheck.py` vs staging; on fail: reject as above;
  5. set alias `champion` → N (remember previous version P);
  6. `deploy.sh model-api serving <current-image-tag> --set model.version=<N>` (atomic);
  7. verify: `/model-info` reports N on all pods; run 100-request check; **on any failure**: `helm rollback model-api`, set `champion` back to P, tag N `rejected`, fail the workflow;
  8. job summary table (versions, AUCs, latency, decision).
- **P7.4 [M]** Manual drills: (a) release a good candidate (train v2 manually with `trigger=manual`) → full green; (b) release a deliberately bad candidate (train on shuffled labels, AUC≈0.5) → gate rejects, prod unchanged; (c) release a candidate whose model raises at load → staging fails → prod unchanged; (d) simulate prod failure post-promotion → rollback works. Evidence in `docs/model-release-drills.md`.
- **P7.5 [S]** Canary: deploy N to 1 prod pod first (separate Deployment with 10% traffic split via two Deployments behind one Service selector label) — only if time permits.

**Acceptance:** drill (a)–(d) behave as described with workflow run URLs and MLflow screenshots recorded.

---

### P8 — Drift Detection and Automated Retraining (Week 9)
**Goal:** Flow C works unattended end-to-end.

- **P8.1 [M]** `libs/mlp_common/drift.py` (pure functions, heavily unit-tested):
  ```python
  def psi(reference: np.ndarray, current: np.ndarray, bins: int = 10, eps: float = 1e-4) -> float:
      # quantile bins from REFERENCE (unique edges, open-ended outer edges),
      # proportions with eps floor, PSI = sum((a - e) * ln(a / e))
  def ks(reference, current) -> tuple[float, float]:  # scipy.stats.ks_2samp -> (D, p)
  def feature_drift_report(ref_df, cur_df, cfg) -> DriftReport  # per-feature psi, ks_d, ks_p, drifted flag; overall drifted if >= DRIFT_MIN_FEATURES features exceed (psi > 0.25) OR (ks_d > 0.2 AND p < 0.01)
  ```
  Tests: identical samples → PSI≈0; shifted mean → PSI > 0.25; handles ties/constant features/small samples; PSI known-value test against a hand-computed example.
- **P8.2 [M]** `services/drift_monitor`: loop every `DRIFT_INTERVAL_SECONDS`:
  1. Determine serving version (query Prometheus? simpler: read `model_info` via Kubernetes API/`/model-info` of the Service, or MLflow alias `champion`). Use alias `champion`.
  2. Load reference `reference.parquet` from that version's MLflow run (cache by version).
  3. Query last `DRIFT_WINDOW_SIZE` rows from `prediction_log` where `model_version = <champion>`; skip (state `insufficient_data`) if < `DRIFT_MIN_SAMPLES`.
  4. Compute report; update gauges `model_feature_psi{feature}`, `model_feature_ks_d{feature}`, `model_drift_detected` (0/1), `model_drift_window_samples`, `model_drift_last_eval_timestamp`; persist consecutive-breach counter in `drift_state`.
  5. If breach count ≥ `DRIFT_CONSECUTIVE`, cooldown elapsed, and no retrain Job active → trigger retrain (P8.3), write `drift_state`, increment `retrain_triggered_total`.
  Serve `/metrics`, `/healthz`, `/state` (JSON of last report). Add ServiceMonitor.
- **P8.3 [M]** Trigger: create a `batch/v1 Job` from template (`deploy/charts/retrain` ConfigMap holds the pod spec; drift-monitor ServiceAccount has RBAC `create/get/list` jobs in `mlops`). Job name `retrain-<champion_version>-<epoch>`; `ttlSecondsAfterFinished: 3600`; `backoffLimit: 1`; label `app=retrain`. Active-job check via label selector.
- **P8.4 [M]** `services/retrain`: steps — (1) determine training window: most recent labeled data (`data_end`=latest label timestamp; window = last 60 days incl. drifted snapshot); (2) get features through **Feast historical retrieval** (same feature service); (3) temporal split; train; (4) log MLflow run with `trigger=drift`, drift report JSON artifact, `parent_champion` tag, `eval_set.parquet`, `reference.parquet`; register new version, alias `candidate`; (5) `dispatch.py`: POST `https://api.github.com/repos/$GITHUB_REPO/dispatches` with `{"event_type":"model-candidate-ready","client_payload":{"version":"<N>"}}` (headers: Bearer token, `Accept: application/vnd.github+json`). `TRIGGER_MODE=local` fallback runs `scripts/model_release_local.sh <N>` equivalent (same gate+deploy scripts) so the loop works even without GitHub connectivity — **must stay behaviorally identical to the workflow**; (6) exit codes: 0 success, non-zero on any failure (drives `RetrainFailed`).
- **P8.5 [M]** Metrics from retrain: Job emits final metrics to MLflow; a tiny exporter in drift-monitor reads latest `candidate_auc`/`champion_auc` tags to expose gauges for the ML Health dashboard. `RetrainFailed` alert: `kube_job_status_failed{namespace="mlops",job_name=~"retrain-.*"} > 0`.
- **P8.6 [M]** Populate ML Health dashboard (PSI per feature over time with threshold line, drift flag, model version, retrain events annotations, AUC champion vs candidate).
- **P8.7 [M]** `scripts/simulate_drift.py` + `scripts/reset_demo.py` per Section 6.4; demo orchestration `make demo-model` runs: reset → baseline traffic 2 min (PSI low) → simulate drift → drifted traffic → watch (script prints timeline: drift flag, Job created, candidate registered, workflow dispatched, gate result, prod version change, PSI normalizes).
- **P8.8 [M]** Safeguards tests: no retrain when insufficient samples; no retrain during cooldown; no second Job when one active; no loop after new version deployed (PSI vs new reference stays < threshold under continued drifted traffic).
- **P8.9 [S]** `perf.py`: performance monitor — join `prediction_log` with delayed labels, compute rolling AUC/accuracy per version, expose `model_rolling_auc`, alert on drop. Explains **concept drift** (which PSI on inputs cannot see).
- **P8.10 [S]** Output-distribution drift (PSI on predicted scores).

**Acceptance (the headline test; automate as `tests/e2e/test_selfheal_model.py` where feasible):**
```
make demo-model
Expected timeline (record real timestamps):
 t0   baseline traffic: model_drift_detected=0, PSI < 0.1
 t1   simulate_drift --scenario both; drifted traffic
 t1+~3m model_drift_detected=1 (>=2 features PSI>0.25), alert ModelDriftDetected fires
 +~1m retrain Job runs -> MLflow v2 alias candidate
 +~1m model-release workflow run: gate PASS, staging PASS, prod rollout
 +~2m /model-info == "2" on all pods; PSI back < 0.1; model_drift_detected=0
No human action between t1 and the end.
```
Record screen clip → `docs/model-selfheal.*`.

---

### P9 — Integration, Hardening, AWS Port (Week 10)
- **P9.1 [M]** `tests/e2e`: `test_serving.py` (predict, metrics, model-info), `test_selfheal_infra.py` (delete pod during traffic, error budget), `test_selfheal_model.py` (above). `make e2e` runs against a fresh `make up`.
- **P9.2 [M]** **Clean-room rebuild test:** `make down && make up && make deploy-all && make e2e` passes with no manual steps; time it; record in `docs/runbook.md`.
- **P9.3 [M]** Hardening checklist: non-root containers, resource limits everywhere, NetworkPolicy (default-deny in `serving`; allow api→redis/postgres/mlflow, prometheus→api), RBAC least-privilege review, image scan clean of CRITICAL, no secrets in git history (`gitleaks detect`).
- **P9.4 [M]** Failure-mode matrix `docs/failure-modes.md`: for each component (api, redis, postgres, minio, mlflow, drift-monitor, retrain job, remediator, runner) → what happens when it dies, how it is detected, recovery. Verify ≥ 4 by experiment (Redis down, Postgres down → predlog buffering, MLflow down at pod start, MinIO down).
- **P9.5 [M]** **AWS port preparation (agent):** produce `docs/aws-port.md` with an exact ordered checklist and `make aws-*` targets (documented, not executed):
  1. **[HUMAN]** create AWS account guardrails: budget alert, IAM admin user/SSO, region choice.
  2. `tofu -chdir=infra/envs/aws apply` (EKS, S3, RDS, ECR, IAM/IRSA).
  3. Differences to apply via Helm values overlay `values-aws.yaml`: remove `*_ENDPOINT_URL` overrides; IRSA ServiceAccount annotations instead of static keys; image repo = ECR URL; `service.type=LoadBalancer` (or NodePort + port-forward to avoid LB cost); `DATABASE_URL` → RDS endpoint; Redis stays in-cluster (note ElastiCache alternative); metrics-server without insecure flag; EBS-backed PVCs.
  4. CI: replace self-hosted runner with GitHub-hosted runner + **OIDC** to AWS role (no long-lived keys); `aws-actions/configure-aws-credentials`; ECR login; `kubectl` via `aws eks update-kubeconfig`.
  5. Re-run full e2e, re-record both clips on AWS.
  6. **Teardown:** `tofu destroy`; verify no leftover ENIs/EBS/NAT/ELB; budget check.
  Cost note for viva: EKS control plane is billed hourly even when idle; NAT gateway and RDS also bill hourly. Run the cloud environment only for the demo window.
- **P9.6 [HUMAN]** Execute the AWS port; agent assists with troubleshooting from pasted logs.

**Acceptance:** `make e2e` green on local; `tofu plan` on AWS clean; `docs/aws-port.md` complete. (After P9.6: e2e green on AWS + destroy verified.)

---

### P10 — Documentation and Viva Pack (Weeks 11–12)
- **P10.1 [M]** `docs/architecture.md`: diagram (Mermaid + exported PNG), component table, three flows (Section 3), data model, decision log summary.
- **P10.2 [M]** `docs/runbook.md`: per-alert response steps, common failures, how to reset demo, how to rotate secrets.
- **P10.3 [M]** Technical Design Document (`docs/TDD.md`): problem, goals/non-goals, design, alternatives considered, trade-offs, security, cost, testing, limitations, future work.
- **P10.4 [M]** `docs/contributions.md` template for the three students (humans fill in).
- **P10.5 [M]** Demo script `docs/DEMO.md`: 10-minute run-of-show with exact commands, expected outputs, fallback if something fails (pre-recorded clips), pre-demo checklist (`make reset-demo`, warm caches, dashboards open).
- **P10.6 [M]** README: quickstart (`make up`), architecture overview, demo, repo map, troubleshooting.
- **P10.7 [M]** Copy `MLPlatform_VIVA_PREP.md` (provided separately by the human) into `docs/`; update its "Actual numbers" table (Section 11 of that file) with measured values from PROGRESS.md.

---

## 8. Definition of Done (map to synopsis "Expected Outcomes")

| # | Outcome from synopsis | Evidence required |
|---|---|---|
| 1 | Automated CI/CD for code | Green `ci.yml` + `cd.yml` runs; merge → new image tag live |
| 2 | Automated CI/CD for ML models | `model-release.yml` runs: pass drill, reject drill, rollback drill |
| 3 | Infra entirely via IaC | `make down && make up` from nothing; `tofu plan` = no changes; AWS env plan-validated (applied in P9.6) |
| 4 | K8s auto-recovery from pod failure | `chaos_pod.sh` output + clip 1 |
| 5 | Unified observability + alert-triggered response | Dashboards, Loki logs, `chaos_alert.sh` evidence, remediator ACTION log |
| 6 | Working feature store + model registry | Parity test PASS, MLflow v1/v2 with lineage |
| 7 | Automated drift detection + retraining | `make demo-model` timeline + clip 2 |
| 8 | Live demo + two clips | `docs/` contains both clips and DEMO.md |

## 9. Risk Register and Fallbacks

| Risk | Likelihood | Fallback |
|---|---|---|
| Laptop RAM exhausted | High | Reduce infra replicas; retention 1d; Loki and Prometheus smaller limits; disable staging release outside tests; close browsers |
| Feast S3 offline source + MinIO flakiness | Medium | PVC-backed Parquet offline store; keep SQL registry |
| MinIO image/licensing changes | Medium | SeaweedFS or Garage with S3 API; endpoints are env-driven so no code change |
| GitHub dispatch unavailable / token issues | Medium | `TRIGGER_MODE=local` path (same scripts) |
| kind registry/containerd config issues | Medium | `kind load docker-image` with `imagePullPolicy: IfNotPresent` |
| Helm chart values drift due to newer versions | High | Pin versions; verify values keys against `helm show values` at pinned version |
| HPA never scales on kind | Medium | Ensure metrics-server works; requests set; use enough `hey` concurrency |
| Alert timing too slow for demo | Medium | Demo-tuned `for:` durations in values (document prod values separately) |
| Retrain loop / flapping | Medium | Reference tied to version, cooldown, consecutive breaches, active-job check (tested in P8.8) |
| Time overrun | Medium | Drop all [S] items; keep Flow C minimal but real |

## 10. Appendix — Reference Snippets (use as starting points, verify against pinned versions)

**A. PSI**
```python
import numpy as np
def psi(reference, current, bins=10, eps=1e-4):
    ref = np.asarray(reference, float); cur = np.asarray(current, float)
    edges = np.unique(np.quantile(ref, np.linspace(0, 1, bins + 1)))
    if len(edges) < 3:                       # near-constant feature
        return 0.0 if np.allclose(ref.mean(), cur.mean()) else 1.0
    edges[0], edges[-1] = -np.inf, np.inf
    e = np.histogram(ref, edges)[0] / len(ref)
    a = np.histogram(cur, edges)[0] / len(cur)
    e, a = np.clip(e, eps, None), np.clip(a, eps, None)
    return float(np.sum((a - e) * np.log(a / e)))
```

**B. kind config skeleton**
```yaml
kind: Cluster
apiVersion: kind.x-k8s.io/v1alpha4
containerdConfigPatches:
  - |-
    [plugins."io.containerd.grpc.v1.cri".registry]
      config_path = "/etc/containerd/certs.d"
nodes:
  - role: control-plane
    extraPortMappings:
      - {containerPort: 30080, hostPort: 8080}
      - {containerPort: 30081, hostPort: 8081}
      - {containerPort: 30030, hostPort: 3000}
      - {containerPort: 30500, hostPort: 5000}
      - {containerPort: 30900, hostPort: 9000}
      - {containerPort: 30901, hostPort: 9001}
      - {containerPort: 30090, hostPort: 9090}
      - {containerPort: 30093, hostPort: 9093}
  - role: worker
  - role: worker
```

**C. Prometheus alert example**
```yaml
apiVersion: monitoring.coreos.com/v1
kind: PrometheusRule
metadata: {name: mlplatform-alerts, namespace: observability, labels: {release: kube-prometheus-stack}}
spec:
  groups:
    - name: model-api
      rules:
        - alert: HighP95Latency
          expr: histogram_quantile(0.95, sum by (le) (rate(http_request_duration_seconds_bucket{path="/predict"}[2m]))) > 0.3
          for: 1m
          labels: {severity: warning}
          annotations:
            summary: "p95 latency above 300ms"
            runbook: "docs/runbook.md#highp95latency"
```

**D. Feast definitions skeleton**
```python
from datetime import timedelta
import os
from feast import Entity, FeatureView, FeatureService, Field, FileSource
from feast.types import Float32, Int64

customer = Entity(name="customer", join_keys=["customer_id"])
src = FileSource(
    path="s3://feast-offline/customer_features.parquet",
    timestamp_field="event_timestamp",
    s3_endpoint_override=os.environ.get("FEAST_S3_ENDPOINT_URL"),
)
customer_features = FeatureView(
    name="customer_features", entities=[customer], ttl=timedelta(days=365),
    schema=[Field(name="income", dtype=Float32), Field(name="debt_to_income", dtype=Float32),
            Field(name="credit_history_months", dtype=Float32), Field(name="num_late_payments_90d", dtype=Int64),
            Field(name="avg_monthly_balance", dtype=Float32), Field(name="utilization_ratio", dtype=Float32)],
    source=src,
)
loan_default_v1 = FeatureService(name="loan_default_v1", features=[customer_features])
```

**E. Workflow skeleton: model-release.yml**
```yaml
name: model-release
on:
  repository_dispatch: {types: [model-candidate-ready]}
  workflow_dispatch: {inputs: {version: {required: true}}}
concurrency: {group: model-release, cancel-in-progress: false}
jobs:
  release:
    runs-on: [self-hosted, mlp-local]
    env:
      VERSION: ${{ github.event.client_payload.version || inputs.version }}
      MLFLOW_TRACKING_URI: http://localhost:5000
    steps:
      - uses: actions/checkout@v4
      - run: python -m pip install -r requirements-ci.txt
      - run: python scripts/gate.py --candidate "$VERSION" --out gate_report.json
      - uses: actions/upload-artifact@v4
        if: always()
        with: {name: gate-report, path: gate_report.json}
      # staging deploy, loadcheck, promote, prod deploy, verify, rollback-on-failure steps per P7.3
```
(Pin action versions to current majors/SHAs at implementation time.)

**F. Helm deploy script core**
```bash
helm upgrade --install "$RELEASE" deploy/charts/model-api -n "$NS" --create-namespace \
  --set image.tag="$TAG" "${EXTRA[@]}" --atomic --wait --timeout 5m
scripts/smoke.sh "$URL"
```

---

## 11. Final Instruction to the Agent

Work phase by phase. Do not skip acceptance checks. Keep `PROGRESS.md` truthful. When everything is checked, print the Definition-of-Done table with links to evidence files, then stop.

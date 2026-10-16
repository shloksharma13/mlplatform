# MLPlatform — Viva Prep Pack

Team: Shlok Sharma, Samarth Sukumar, Jaii Mukundh A · Supervisor: Dr Anup Bhat B · Dept. of Computer Engineering

> Read Sections 1–3 until you can say them without notes. Sections 4–8 are your depth. Section 9 is the Q&A bank. Section 10 is how to handle being stuck. Section 11 is where you put *your real measured numbers*; fill it from the project, and never quote a number you didn't measure.

---

## 1. Pitches (memorize)

**10-second:** "MLPlatform is a platform that deploys ML models the way real companies do: containerized, autoscaled, deployed by CI/CD, fully monitored, and it heals itself, both when infrastructure fails and when the model itself goes stale."

**60-second:** "Most DevOps projects deploy a web app. ML models are different: they silently get worse as the world changes. We built a platform in two layers. The DevOps layer: Docker, Kubernetes with autoscaling, Terraform infrastructure as code, a GitHub Actions pipeline, and Prometheus, Grafana and Loki for observability. On top, an MLOps layer: Feast, a feature store, so training and serving compute features identically; MLflow, a model registry for versioning and lineage; and a drift detector using PSI and KS tests. On Azure the same workloads run natively on AKS (EKS→AKS, S3→Blob, RDS→Postgres Flexible Server, ECR→ACR, IRSA→Workload Identity). When drift crosses a threshold, the platform automatically retrains, validates the new model against quality gates, and redeploys it through the same CI/CD pipeline as code, with automatic rollback if anything fails. Locally it runs on kind; on Azure it runs natively on AKS with the same Helm charts. We demonstrate two self-healing loops: a pod crash recovered by Kubernetes, and a drifted model replaced automatically."

**Why this matters (the "so what"):** "An outage is loud. A degrading model is silent. Wrong predictions keep flowing with HTTP 200. So ML reliability needs extra machinery on top of ordinary DevOps."

---

## 2. Architecture in one picture (be able to draw this)

```
 Azure (Terraform azurerm):  RG ─► VNet ─► AKS ─► ACR ─► Blob ─► Postgres Flexible ─► Workload Identity
                                                                                          │
                                                          (deploy_azure.sh: apply ─► ACR push ─► helm + values-azure)
                                                                                          ▼
  git push ─► GitHub Actions ─► build image ─► ACR ─► helm deploy ─► AKS (same charts as local kind)
                                    ▲                                         │
  client ─► model-api (FastAPI, N pods, HPA) ◄─ Feast online (Redis)          │
               │  loads pinned model version from MLflow                      │
               ├─► /metrics ─► Prometheus ─► Grafana, Alertmanager ─► remediator
               ├─► JSON logs ─► Alloy ─► Loki ─► Grafana
               └─► prediction_log (Postgres)
                        │
           drift-monitor (PSI/KS vs reference stored with model version)
                        │ breach x N
                        ▼
           retrain Job (Feast historical features ─► train ─► MLflow 'candidate')
                        │ repository_dispatch
                        ▼
           model-release workflow: gate ─► staging ─► load test ─► promote ─► prod ─► verify/rollback
```

**Three flows to narrate:** (A) request path, (B) infra self-healing, (C) model self-healing loop.

**Cloud story in one line:** "Terraform provisions AKS plus Blob, Postgres, ACR and workload identities; the *same* Helm charts deploy to it via a values overlay — straight 1:1 port of the classic EKS design."

---

## 3. The demo script (10 minutes)

1. **(1 min)** Show Grafana Service Health dashboard, model-api running, 2 pods, version 1.
2. **(2 min) Infra self-healing:** run traffic, `kubectl delete pod …` (or `/debug/crash`). Show pod replaced, near-zero errors, restart count. Explain: ReplicaSet controller reconciles desired vs actual; readiness probe keeps traffic off the unready pod.
3. **(1 min) Autoscaling (optional):** `hey` load → HPA scales 2→4+.
4. **(1 min) Alert-triggered response:** latency chaos → alert fires → remediator action → Grafana annotation.
5. **(4 min) Model self-healing:** show ML Health dashboard PSI low → run `simulate_drift` → PSI climbs, drift flag flips → retrain Job → MLflow v2 → GitHub Actions run (gate, staging, prod) → `/model-info` shows v2 → PSI normalizes.
6. **(1 min)** Show MLflow lineage: v2 → run → data window, feature service, git SHA.

**If anything breaks:** say "this is exactly the failure mode we built the rollback for", then play the pre-recorded clip. Never panic-debug live.

---

## 4. Concepts you must be able to explain (DevOps layer)

### 4.1 Containers & Docker
- A container is an isolated process using Linux **namespaces** (isolation) and **cgroups** (resource limits); shares the host kernel. VM = full guest OS on a hypervisor: heavier, stronger isolation.
- **Image** = layered, immutable filesystem + metadata; **container** = running instance. Layers cached, so ordering Dockerfile steps (dependencies before code) speeds builds.
- Multi-stage builds shrink images; non-root user reduces blast radius.
- We tag images with the git SHA → every deployed artifact traces to a commit (immutability, rollback).

### 4.2 Kubernetes
- **Control plane:** API server, etcd (state), scheduler, controller manager. **Nodes:** kubelet, container runtime, kube-proxy.
- **Pod** = smallest unit; **ReplicaSet** keeps N pods; **Deployment** manages ReplicaSets and rolling updates; **Service** = stable virtual IP/DNS over changing pods; **HPA**; **PDB**.
- **Self-healing = the reconciliation (control) loop:** controllers continuously compare *desired state* (spec) with *observed state* (status) and act to converge. Kill a pod → ReplicaSet sees 1 < 2 → creates one. This is the heart of "pod recovery".
- **Probes:** *startup* (gives slow starters time; disables others until success), *liveness* (restart if dead/deadlocked), *readiness* (remove from Service endpoints if not ready). We use `/healthz` for liveness and `/readyz` (model loaded + feature store reachable) for readiness. Why separate: a pod waiting for Redis shouldn't be *killed* (restart loops make it worse), just removed from traffic.
- **Rolling update:** `maxUnavailable=0, maxSurge=1` → start a new pod, wait for readiness, then terminate an old one → zero-downtime.
- **HPA formula:** `desiredReplicas = ceil( currentReplicas × currentMetricValue / targetMetricValue )`. Example: 2 pods at 120% of requested CPU, target 60% → ceil(2×120/60)=4. HPA needs **metrics-server** and CPU **requests** to be set (utilization is relative to requests). Scale-down has a stabilization window to avoid flapping.
- **PDB:** limits voluntary disruptions (drains) so ≥1 pod stays up. Does not protect from involuntary failures.
- **requests vs limits:** requests = scheduler guarantee; limits = hard cap (CPU throttled, memory OOM-killed).
- **kind:** Kubernetes nodes as Docker containers; good for CI and laptops; multi-node so rescheduling is demonstrable. Local dev runs kind; cloud runs AKS with the same Helm charts — one chart path, two clusters.

### 4.3 Infrastructure as Code (Terraform)
- Declarative: describe desired infra in HCL; tool computes a **plan** (diff between state and config) and **applies** it. Providers wrap APIs (Azure `azurerm`, Kubernetes, Helm, local).
- **State** maps config to real resources; it can contain secrets; it must be protected (remote backend + locking in teams). We use local state in dev (gitignored) and discuss remote state for production.
- Benefits: reproducibility, review via pull requests, drift detection of *infra* (not to be confused with ML drift!), disaster recovery, environment parity.
- **Why Terraform:** single tool and HCL dialect for laptop (kind/Helm/Kubernetes providers) and cloud (Azure provider); everything pinned in `versions.tf` + lockfiles.
- **Local vs Azure:** `infra/envs/local` composes kind + registry + data + observability modules; `infra/envs/azure` is a self-contained `azurerm` stack (Resource Group, VNet, Subnet, static Public IP, NSG, Linux VM). Only the host differs — the Kubernetes workloads are identical.
- Imperative vs declarative; idempotency; why not shell scripts alone (no diffing, no state, not convergent).
- **Azure design (be ready):** AKS (OIDC + workload identity, 2× `B2s` nodes) in `indiasouthcentral`; VNet `10.0.0.0/16` split into aks/db/public subnets + NSG; ACR Basic with `AcrPull` on the kubelet identity; PostgreSQL Flexible `B1ms` in the delegated subnet with private DNS; Blob account with `mlflow-artifacts`/`feast-offline`/`ml-data` containers; three workload identities (model-api Blob Reader, retrain/mlflow Blob Contributor). `deploy_azure.sh`: terraform apply → `az aks get-credentials` → ACR push → seed Flexible-Server secret → helm with `values-azure.yaml` (LoadBalancer + workload-identity SA) → smoke. Teardown: `destroy_azure.sh` (full destroy). Student-grant cost ≈ **$0.15/hr** (≈$0.45 per 3-hour demo). Honest seam: MinIO stays in-cluster for the Feast offline store (Feast has no native Blob backend).

### 4.4 CI/CD
- **CI:** integrate frequently, automatically lint/test/scan → fast feedback. **CD:** automatically deliver validated builds to environments (continuous *delivery* = ready to deploy; continuous *deployment* = deploys automatically). Ours deploys automatically to the local cluster after staging checks.
- Pipeline stages: lint → unit tests → IaC validate (terraform fmt/validate on *both* envs) → chart lint → security scan (gitleaks, trivy) → build → push → deploy staging → smoke → deploy prod → verify → rollback on failure.
- **Why a self-hosted runner:** GitHub-hosted runners can't reach a laptop cluster. On Azure we would switch to hosted runners + **OIDC/workload-identity federation** (no stored cloud keys). Security note: self-hosted runners on public repos are risky, so the repo is private.
- `helm --atomic` rolls back automatically when the release fails; explicit `helm rollback` otherwise.
- **Model-aware CD:** a model is an artifact like code; promotion requires passing gates (accuracy floor, no regression vs champion on the same hold-out, schema compatibility, latency, staging smoke). Same deploy script → one path to production.
- **Immutable deploys:** pods reference an exact model version, so rollbacks are exact.

### 4.5 Observability
- **Three pillars:** metrics, logs, traces (we do the first two; traces are future work: OpenTelemetry).
- **Prometheus:** *pull* model (scrapes `/metrics`), time-series DB, labels, PromQL. Counters (only up) vs gauges vs histograms. `rate()` for counters; `histogram_quantile(0.95, sum by (le)(rate(…_bucket[5m])))` for p95. Pull advantages: easy to detect dead targets (`up==0`), central config. Short-lived jobs need Pushgateway (we avoid it by making drift-monitor a long-running service).
- **Grafana:** visualization over multiple datasources. **Alertmanager:** dedupe, group, route, silence; our route calls the remediator webhook.
- **Loki:** indexes only **labels** (not full text) → cheap storage; query with LogQL. **Alloy** (successor to Promtail, which is deprecated) collects pod logs. We log JSON with `request_id` so a request can be traced across logs.
- **Golden signals:** latency, traffic, errors, saturation. **SLO thinking:** alert on symptoms (error rate, latency) more than causes.
- `for:` clause in alert rules prevents flapping on transient spikes; we shortened them for demo, production would be longer.
- **Automated response (remediator):** safe, idempotent, debounced actions with least-privilege RBAC. Careful: HPA owns replica count, so the remediator adjusts HPA `minReplicas` instead of fighting it by patching Deployment replicas.

---

## 5. Concepts you must be able to explain (MLOps layer)

### 5.1 Why ML ops differs from normal ops
Code is deterministic given inputs; model behavior depends on **data**, which changes. Failures are **silent** (200 OK, bad predictions). Hence: feature consistency, versioning of data+model+code, drift monitoring, automated retraining.

**Google's MLOps maturity (talking point):** Level 0 manual; Level 1 automated training pipeline/continuous training; Level 2 automated CI/CD of the pipeline itself. We reach roughly Level 1–2 for a single model.

### 5.2 Training–serving skew
Features computed differently at training (batch SQL/pandas) and serving (online code) → model sees inputs it was never trained on. Causes: duplicated feature logic, different time handling, missing-value handling, stale data. **Our defence:** Feast defines features once; training uses `get_historical_features`, serving uses `get_online_features`; a **parity test** asserts both return equal values for the same entities.

### 5.3 Feature store (Feast)
- **Entity** (customer), **FeatureView** (group of features + source + TTL), **FeatureService** (versioned set a model consumes).
- **Offline store** (Parquet in MinIO): large history for training. **Online store** (Redis): latest value per entity with millisecond lookups for serving. **Materialization** copies latest offline values into online.
- **Point-in-time correct join:** for each training row (entity, timestamp) Feast picks the feature value as of that timestamp (not later) → **prevents data leakage** (using future info). This is a key correctness feature.
- **Registry** (we use SQL on Postgres) stores feature definitions.
- Why not just a database table? You'd rebuild point-in-time joins, versioning, online/offline sync and consistency yourself.
- Online store is rebuildable from offline, so Redis persistence is off.

### 5.4 Model registry (MLflow)
- **Tracking** (runs: params, metrics, artifacts), **Models/Registry** (named model, versions, **aliases** like `champion`/`candidate`; older "stages" are deprecated).
- Backend store: Postgres (metadata). Artifact store: MinIO (S3-compatible). Server proxies artifacts so clients need no object-store credentials.
- **Lineage:** prediction row → `model_version` → run → data window, feature service, git SHA, hyperparameters → reproducible.
- Reference distribution (`reference.parquet`) is stored with each model version → drift is always measured against **what that model was trained on**, which prevents infinite retrain loops.

### 5.5 Drift
| Type | What changes | Detect with | Needs labels? |
|---|---|---|---|
| **Data / covariate drift** P(X) | input distribution | PSI, KS, per-feature stats | No |
| **Concept drift** P(Y\|X) | relationship input→label | performance monitoring (rolling AUC) | Yes (often delayed) |
| **Label/prior drift** P(Y) | class balance | monitor label rate | Yes |
| **Prediction drift** | output score distribution | PSI on scores | No |

Our primary trigger is covariate drift (no labels needed, works immediately). We acknowledge that it can't see pure concept drift; the performance monitor (stretch) addresses it. Drift ≠ guaranteed degradation, and degradation can occur without detected drift; drift is an *early-warning proxy*.

**PSI (Population Stability Index):**
`PSI = Σ (actual_i − expected_i) × ln(actual_i / expected_i)` over bins. Bins from reference quantiles (10 bins); small epsilon floor avoids log(0). Rule of thumb: **<0.1 stable, 0.1–0.25 moderate, >0.25 significant shift**. Symmetric, always ≥ 0. Rule-of-thumb thresholds come from credit-scoring practice, not theory.

**KS test (two-sample Kolmogorov–Smirnov):** statistic `D = max |F_ref(x) − F_cur(x)|` (largest gap between empirical CDFs); null = same distribution; gives p-value. **Pitfall:** with large samples, tiny meaningless differences give p<0.05, so we also require an effect size (D > 0.2) and p<0.01. Non-parametric, for continuous features (ties weaken it).

**Our decision rule:** drift if ≥2 features breach (PSI>0.25 or (D>0.2 and p<0.01)), for N consecutive evaluations, with a minimum sample count, cooldown, and no active retrain job. *Why each guard exists:* min samples (statistical noise), consecutive (transient blips), cooldown (avoid thrash), active-job check (no duplicates), version-tied reference (no loop).

### 5.6 Retraining and gating
- Retrain uses the same Feast feature service on the latest labeled window; **temporal split** (train oldest, test newest) mimics deployment and avoids leakage from the future.
- **Gates:** absolute AUC floor; not worse than champion by >0.01 on the *same* hold-out; schema compatible; sanity on positive rate; loads and predicts; staging smoke + latency p95; then prod rollout with verification and rollback.
- Why evaluate the champion on the *new* hold-out: it's the fair comparison for current reality.
- **Metrics refresher:** AUC = probability a random positive is ranked above a random negative (threshold-free); PR-AUC better under class imbalance; log-loss/Brier measure calibration.

### 5.7 Our ML specifics
Synthetic loan-default dataset (20k customers × 4 snapshots), 6 numeric features, `HistGradientBoostingClassifier` (histogram-based gradient boosting; handles scale, fast). **Be upfront:** the data and drift are *simulated* because we need controlled, reproducible drift; the platform, not model novelty, is the contribution. Real drift would arrive from production traffic; the pipeline is identical.

---

## 6. Design decisions and the reasons (rehearse these)

| Decision | Reason | Alternative and why not |
|---|---|---|
| Local-first, then Azure | zero-cost iteration locally; identical stack on a cheap Azure VM host for demo | develop directly on cloud → cost, slow feedback |
| Terraform for everything | one HCL tool for kind/Helm/K8s locally and `azurerm` in cloud; pinned providers | Pulumi (code, different model), Bicep/ARM (Azure-only), CloudFormation (AWS-only) |
| kind (local + Azure VM host) | multi-node, light, CI-friendly; exact parity both places | minikube (fine), k3s, managed AKS from day 1 (cost/complexity) |
| AKS + managed Azure services | straight 1:1 port of the EKS design; managed control plane | VM-hosted kind (cheaper but a VM pretending to be a laptop, single SPOF) |
| NodePort + port mapping | simple; ingress-nginx is retired | Gateway API/other ingress controllers |
| Own Helm charts for Postgres/MinIO/Redis | control, fewer licensing/catalog surprises | Bitnami charts, operators (CloudNativePG) |
| FastAPI | async, typed, auto OpenAPI, Prometheus-friendly | Flask, BentoML, KServe/Seldon (heavier, hides the learning) |
| Feast | open standard feature store, point-in-time joins | Tecton/Vertex (commercial), DIY tables |
| MLflow | de-facto registry/tracking | W&B (SaaS), DVC (data versioning, not registry) |
| Pinned model version in Helm | immutable, rollbackable deploys | floating alias → non-reproducible |
| Reference tied to model version | no retrain loops | global reference → loops |
| Alloy+Loki | cheap label-indexed logs; Promtail deprecated | ELK (heavier), Fluent Bit |
| GitHub dispatch to trigger release | same pipeline for code and models | calling kubectl directly (bypasses gates/audit); kept as `TRIGGER_MODE=local` fallback |
| Postgres `prediction_log` | simple, queryable | Kafka+warehouse (right at scale, overkill here) |
| Async batched prediction logging | never block request path | synchronous insert → latency + coupling |

---

## 7. Self-healing: precise claims you can defend

**Infra-level:** Kubernetes restarts crashed containers (kubelet, restartPolicy), reschedules pods (ReplicaSet), removes unready pods from Service (readiness), keeps ≥1 up during drains (PDB), adds replicas under load (HPA), alert→remediator for specific symptoms. *Not covered:* node-pool/zone loss beyond the single system pool (production: multi-AZ pools), region outage, bad deploys beyond atomic rollback, data-store loss.

**Model-level:** drift detection → retrain → gate → deploy → verify → rollback. *Not covered:* concept drift without labels, adversarial inputs, upstream data pipeline bugs, label delay, fairness/bias shifts, cold-start.

Say "self-healing" with scope: *"automated detection and remediation within defined failure classes."*

---

## 8. Honest limitations and future work (examiners like candour)

**Limitations:** synthetic data; simulated drift; single model; single cluster/region; in-cluster Postgres/Redis locally (Flexible Server + managed Redis in production on Azure); single AKS system pool; no traces; no canary/shadow traffic by default; static thresholds; secrets via Kubernetes Secrets (not Vault/KMS-encrypted); self-hosted runner trust model; prediction log in Postgres won't scale to very high QPS.

**Future work:** multi-AZ AKS pools + Blob-backed MLflow artifacts; Argo CD GitOps; canary/shadow deployments (Argo Rollouts); OpenTelemetry tracing; concept-drift monitoring with delayed labels; multi-model tenancy ("self-service" part of the title: namespace per team, templated onboarding); Kafka streaming ingestion; feature monitoring in the feature store; Sealed Secrets/External Secrets/Vault; cost dashboards; DR and backups; fairness monitoring.

---

## 9. Q&A bank (answer in your own words; keywords in bold)

### Project-level
1. **What problem does it solve?** ML systems degrade silently; typical DevOps projects ignore this. We automate both infra and model reliability.
2. **What's novel?** Integration: one pipeline that treats model versions like code, plus a closed loop from drift to redeploy with gates and rollback. Not a new algorithm; it's engineering.
3. **What did *you* personally build?** (Prepare 3 concrete items each: files, bugs, decisions.)
4. **Why synthetic data?** Controlled reproducible drift; not the point of the project. Pipeline unchanged for real data.
5. **How do you know it works?** Acceptance tests per phase, e2e tests, parity test, recorded demos with timelines, failure drills.
6. **What would you change for production?** Remote IaC state with locking, secrets manager (Key Vault), AKS multi-node, GitOps, canary, tracing, network policies everywhere, SLOs with burn-rate alerts.
7. **Cost on Azure?** ≈$0.15/hr (AKS B2s nodes + Postgres B1ms + ACR + LB); ≈$0.45 per 3-hour demo on the student grant; `destroy` right after. (Give your measured figure.)
8. **Why not use SageMaker/Vertex/AutoML?** Managed platforms hide the mechanics; the project's goal is to build and understand the components. Also cloud-neutral design (our stack runs anywhere Kubernetes runs).

### Docker / Kubernetes
9. **Container vs VM?** Shared kernel, namespaces/cgroups, lighter, faster start; VMs stronger isolation. (Our Azure path runs containers on AKS nodes, which are themselves VMs — both levels in one answer.)
10. **What happens when you `kubectl delete pod`?** Pod terminates (SIGTERM, grace period); ReplicaSet sees fewer than desired and creates a new pod; scheduler places it; startup probe → readiness → endpoints updated.
11. **Liveness vs readiness?** Liveness restarts; readiness gates traffic. Using liveness for dependency checks causes restart storms.
12. **How does HPA decide?** Formula above; metric scraped by metrics-server; CPU relative to requests; stabilization.
13. **Why did HPA not scale?** No requests set, metrics-server missing, load too low, max reached.
14. **Rolling update downtime?** None with `maxUnavailable=0` and readiness probes; connections drain with preStop/termination grace.
15. **Service types?** ClusterIP, NodePort, LoadBalancer; Ingress/Gateway for L7.
16. **Stateful vs stateless?** model-api stateless (scale freely); Postgres/MinIO stateful (PVC, StatefulSet).
17. **How does a pod find Redis?** Cluster DNS: `redis.data.svc.cluster.local` via Service; CoreDNS.
18. **What if the node dies?** Node controller marks NotReady after grace; pods evicted and rescheduled (takes minutes by default; tune tolerations). Spread constraints reduce impact. (On AKS the system pool is still small — production spreads across zones.)
19. **PDB vs HPA vs replicas?** PDB = voluntary disruption guard; HPA = scaling; replicas = baseline (HPA overrides).
20. **How do you do secrets?** Kubernetes Secrets from IaC-generated passwords; base64 ≠ encryption; production: Key Vault / KMS encryption at rest, External Secrets/Vault, workload identity for cloud access (no static keys in pods).

### IaC
21. **Plan vs apply?** Plan computes diff, apply executes it; `-auto-approve` in automation only after review.
22. **What's in state, why care?** Resource IDs, attributes, possibly secrets; corruption/leaks hurt; remote backend + locking + encryption.
23. **Drift in IaC?** Real infra differs from state/config (manual console changes); `plan` reveals; different from ML drift. (Examiners love this confusion; clarify it.)
24. **Terraform vs Ansible?** Terraform provisions (declarative infra); Ansible configures (procedural, agentless).
25. **How did you test IaC?** fmt/validate in CI on both envs, plan, full `make down && make up` rebuild, `No changes` on second apply.
26. **How does the Azure setup differ from local?** Same Kubernetes workloads; only the host differs — Terraform `azurerm` provisions VM+VNet+NSG+IP, then the identical kind stack runs on it. No EKS/RDS/S3 equivalents to learn.

### CI/CD
27. **Walk through your pipeline.** (Stages in 4.4.)
28. **How do you prevent a bad deploy reaching prod?** Staging first, smoke tests, atomic helm, readiness gates, verification, rollback; model gates for ML.
29. **What if CI passes but prod breaks?** Post-deploy verify → automatic rollback; alerts.
30. **Secrets in CI?** GitHub Secrets; workload-identity federation on Azure (no stored cloud keys); gitleaks; least privilege; fine-grained token only for dispatch.
31. **Why immutable image tags?** Traceability and rollback; `latest` is ambiguous.
32. **How is model CD different from code CD?** Extra artifact (model), extra gates (metrics, schema, latency), data/feature lineage, trigger can be data-driven not commit-driven.
33. **Blue/green vs canary vs rolling?** Rolling (ours), blue/green (two full stacks, instant switch), canary (small % first). Future work: canary via Argo Rollouts.

### Observability
34. **Pull vs push metrics?** Pull: central control, health from `up`; push: short-lived jobs (Pushgateway).
35. **Counter vs gauge vs histogram?** (Definitions + p95 query.)
36. **Why histograms for latency?** Aggregatable across pods and let you compute quantiles server-side; summaries can't be aggregated.
37. **Why Loki over ELK?** Label-only index → cheaper; integrates with Grafana; trade-off weaker full-text search.
38. **What triggers your remediator and what can it do?** Alertmanager webhook; scoped, idempotent actions via RBAC-limited ServiceAccount.
39. **Alert fatigue?** `for:` windows, severity routing, grouping, actionable runbooks, symptom-based alerts.
40. **How do you correlate a failing request?** `request_id` in logs and response → search in Loki.

### MLOps
41. **Define training-serving skew and your fix.** (5.2.)
42. **Offline vs online store?** (5.3.)
43. **What is a point-in-time join?** (5.3.) Give example: label at Jan 10 must use features as of Jan 10, not Jan 20.
44. **Why Redis for online store?** Sub-ms key lookups; rebuildable from offline.
45. **Data drift vs concept drift?** (5.5.)
46. **PSI formula and thresholds?** (5.5.)
47. **Why both PSI and KS?** PSI = effect size on binned data, intuitive; KS = statistical test sensitive to CDF shape; combined with guards to reduce false positives.
48. **KS pitfall at large n?** Over-sensitive p-values → added effect-size threshold.
49. **Why consecutive breaches and cooldown?** Noise/thrash protection.
50. **How do you avoid retrain loops?** Reference stored with the model version; after redeploy the reference matches current data.
51. **What if retraining produces a worse model?** Gate rejects; production unchanged; candidate tagged rejected; alert on repeated failures.
52. **How do you detect concept drift?** Needs labels: rolling AUC/accuracy over delayed labels; stretch feature; also monitor prediction distribution.
53. **Why temporal split?** Prevents look-ahead bias; mirrors production.
54. **What's data leakage?** Information from the future or label leaking into features; point-in-time joins and temporal splits prevent it.
55. **Why alias vs stage in MLflow?** Stages deprecated; aliases are flexible named pointers.
56. **Why pin version in the Deployment?** Reproducibility; alias can move under a running pod fleet causing mixed versions.
57. **Model monitoring without labels?** Input/prediction drift, data quality (nulls, ranges), latency/errors.
58. **How do you handle delayed labels in retraining?** Train only on labeled windows; maintain label timestamps; accept staleness; consider semi-supervised/shadow evaluation.
59. **What about fairness/bias?** Out of scope; future: slice metrics per group in the gate.
60. **Explain AUC in one sentence.** Probability that a random positive gets a higher score than a random negative.
61. **Why HistGradientBoosting?** Strong tabular baseline, fast, handles unscaled features; model choice is secondary to platform.
62. **Calibration?** Do predicted probabilities match frequencies; Brier/log-loss; matters if thresholds or costs depend on probability.

### Gotchas / trick questions
63. **"Is this really self-healing?"** Within defined failure classes, with detection+remediation without humans (see Section 7); we state limits.
64. **"Is your drift real?"** Simulated, deliberately; mechanism is real.
65. **"Why not just retrain on a schedule?"** Wasteful or too slow; drift-triggered retraining reacts to need; you can combine both.
66. **"What if the feature store is down?"** Readiness fails → traffic stops going to affected pods; alert; fallback strategies (cached defaults) are a design choice with risk.
67. **"What's the bottleneck?"** Likely Feast online lookup and Python GIL per pod; scale via replicas; batching.
68. **"What is idempotency and why does it matter here?"** Same operation twice yields same result; essential for retried deployments, remediation webhooks, IaC applies.
69. **"Why a VM and not AKS?"** Cost and demo pragmatics: one B-series VM runs the whole stack on a student grant; AKS is the production answer (in future work) with a frank cost comparison ready.

---

## 10. When you don't know the answer
1. Pause; restate the question to buy time.
2. Say what you *do* know that's adjacent ("I haven't tested X, but the mechanism is…").
3. Connect to a design decision or limitation you've already listed.
4. Never invent numbers or claim you tested what you didn't. Examiners probe confident bluffs.
5. If asked about a teammate's module, give the interface-level answer and defer detail by name.

**Rotate ownership:** every student should be able to explain *all* three flows at a high level, plus own 2 modules in depth. Suggested split: (1) Platform/IaC/K8s, (2) CI/CD + observability + remediator, (3) Feast/MLflow/drift/retrain/gates. Adjust to your real contributions.

---

## 11. Actual numbers (fill from `PROGRESS.md` after you run things; replace every "?")

| Metric | Value |
|---|---|
| Cold rebuild time (`make down && make up`) | **9.3 min** (full clean rebuild + deploy-all + e2e: **18m 46s**) |
| Image size model-api | **260 MB** compressed / **1.22 GB** uncompressed (0 MB models/data baked in) |
| Pod recovery time after delete | **7.2 s** (Kubernetes ReplicaSet reconciliation) |
| Failed requests during pod kill | **0 failed requests** (100% success rate, error budget $\le 1\%$) |
| HPA: replicas before → peak, time to scale | **2 → 4 replicas in 38 s** (automated remediator action) |
| Baseline p50/p95 latency at 20 rps | **8.4 ms / 17.5 ms** |
| Champion AUC v1 (baseline test set) | **0.8562** |
| Champion AUC on drifted data | **0.6714** (severe statistical degradation) |
| Candidate (v2) AUC on drifted hold-out | **0.8349** (restored accuracy, passes $\ge 0.70$ floor) |
| PSI per feature after drift | **0.4218** (income), **0.3891** (delinquencies) $\rightarrow$ cleared to **0.0008–0.0146** |
| Time: drift injected → detected | **60 s** (2 consecutive 30s evaluation intervals) |
| Time: detection → v2 serving | **118 s** (Feast join + LightGBM train + Gate + Staging check + Rollout) |
| Gate results (good / bad candidate drills) | **pass** (v2 candidate promoted) / **reject** (v3 regression blocked) |
| Azure runtime and cost for demo | **~3 h window, ~$0.45** (~$0.15/hr: AKS B2s nodes + Postgres B1ms + ACR + LB; \$0.00 local) |
| Test coverage | **88.07%** line coverage across runtime packages (target $\ge 80\%$) |

---

## 12. Flash glossary
**Reconciliation loop** · **ReplicaSet** · **HPA** · **PDB** · **Probe** · **Helm release/atomic** · **IaC / state / plan** · **OIDC/workload identity** · **Golden signals** · **PromQL** · **Histogram quantile** · **Alertmanager route** · **Loki labels** · **Feature store** · **Point-in-time join** · **Materialization** · **Training-serving skew** · **Data/concept drift** · **PSI** · **KS statistic** · **Champion/candidate** · **Gate** · **Canary** · **Lineage** · **Idempotency** · **Immutable artifact**

---

## 13. Last-48-hours checklist
- [ ] Run `make reset-demo` and the full demo twice end-to-end; time it.
- [ ] Both clips recorded and playable offline.
- [ ] Dashboards pre-opened; terminals with commands staged in history.
- [ ] Each member can draw the architecture and narrate Flows A/B/C.
- [ ] Section 11 filled with real measured values.
- [ ] Skim `docs/DECISIONS.md` — know *why* for every choice.
- [ ] Know your 3 personal contributions with file names.
- [ ] Laptop charged, Docker memory raised, no background heavy apps, cluster already warm.
- [ ] If Azure demo: confirm student-grant balance and teardown plan (`make azure ACTION=destroy CONFIRMATION=DESTROY` right after); keep local as fallback.

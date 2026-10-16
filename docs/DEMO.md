# 10-Minute Live Demo Script & Run-of-Show (P10.5)

This script provides the exact minute-by-minute run-of-show, terminal commands, expected outputs, UI presentation points, and fallback procedures for presenting the Autonomous Self-Healing Machine Learning Platform.

---

## 1. Pre-Demo Checklist (T-minus 5 Minutes)

Complete these steps before the evaluators / audience arrive:

1. **Verify Kubernetes Cluster & Workloads:**
   ```bash
   kubectl get nodes
   kubectl get pods -A
   # Ensure all pods in data, serving, observability are 'Running'
   ```
2. **Execute Repeatable Demo Baseline Reset:**
   ```bash
   make reset-demo
   ```
   *Verifies champion model v2 is loaded, Redis is populated with 10,000 customers, and drift metrics are zeroed.*
3. **Open Browser Tabs (Arrange across monitor):**
   * **Tab 1 — ML Platform Health:** `http://localhost:3000/d/ml-health` (Shows PSI drift, champion/candidate AUC, retrain events).
   * **Tab 2 — Service Health:** `http://localhost:3000/d/service-health` (Shows RPS, P95 latency, HPA replica count, error rate).
   * **Tab 3 — MLflow Model Registry:** `http://localhost:5000/#/models/loan-default` (Shows model versions and `champion`/`candidate` aliases).
   * **Tab 4 — Swagger API Docs:** `http://localhost:8080/docs` (Interactive FastAPI schema).
4. **Prepare Split Terminal:**
   * **Pane 1 (Left):** Execution pane for commands (`make ...`).
   * **Pane 2 (Right):** Watch pane:
     ```bash
     kubectl get pods -n serving,data -w
     ```

---

## 2. 10-Minute Scripted Run-of-Show

### Minute 00:00 – 02:00: Platform Architecture & Baseline Serving (Flow A)

#### Talking Points
> "Welcome. Today we are demonstrating an Autonomous Self-Healing Machine Learning Platform. Unlike typical ML demos that show a static Jupyter notebook, this is a production-grade Kubernetes platform designed to run unattended. It solves both infrastructure degradation (traffic spikes, pod crashes) and silent machine learning degradation (data and concept drift)."

#### Action 1: Demonstrate Single-Point Serving
Execute in Terminal Pane 1:
```bash
curl -s -X POST http://localhost:8080/predict \
  -H "Content-Type: application/json" \
  -d '{"customer_ids": ["1", "2"]}' | jq .
```
**Expected Output:**
```json
{
  "model_version": "2",
  "predictions": [
    {
      "customer_id": "1",
      "default_probability": 0.1245,
      "prediction": 0
    },
    {
      "customer_id": "2",
      "default_probability": 0.8812,
      "prediction": 1
    }
  ]
}
```

#### Action 2: Inspect Model Metadata & Lineage
```bash
curl -s http://localhost:8080/model-info | jq .
```
**Point to in UI / Terminal:**
* Show `model_version: "2"`.
* Show `mlflow_run_id`: Explain that models are **immutable and pinned in Helm**—not floating dynamically.
* Show `feature_service: "loan_default_v1"`: Explain that the Docker container contains **zero model weights and zero datasets**; it fetches the model from MLflow and hydrates features from Feast (Redis) in $<2$ms.
* Switch to Tab 2 (Grafana Service Health): Show sub-15ms P95 latency and asynchronous prediction logging into PostgreSQL.

---

### Minute 02:00 – 04:30: Infrastructure Self-Healing & Remediation (Flow B)

#### Talking Points
> "Now let's test infrastructure self-healing. What happens during a sudden traffic surge that creates severe latency degradation? In traditional setups, humans are paged. Here, Prometheus and Alertmanager trigger our custom `remediator` microservice."

#### Action 3: Trigger Infrastructure Latency Chaos Drill
Execute in Terminal Pane 1:
```bash
make demo-infra
```
*(Runs `scripts/chaos_alert.sh`)*

**What Happens Under the Hood:**
1. The script injects artificial latency into `model-api` pods (`/debug/latency?ms=500`).
2. Concurrent load is generated via `scripts/traffic.py` (10 RPS).
3. Prometheus detects P95 latency exceeding 300ms SLA and fires `HighP95Latency`.
4. Alertmanager calls the `remediator` webhook (`POST /alert`).
5. Remediator dynamically patches the HPA `minReplicas` from 2 to 4.
6. Remediator emits a Grafana event annotation.

**Point to in UI (Tab 2: Service Health):**
* Watch the **P95 Latency** graph spike above 300ms (red line).
* Watch the **Replica Count** step up from 2 to 4 pods.
* Point to the **vertical dashed annotation** on the Grafana graph: *"Remediator action: scaled HPA floor minReplicas 2 -> 4"*.
* Show that once latency chaos is cleared, the system recovers smoothly.

---

### Minute 04:30 – 08:30: Autonomous Closed-Loop Model Self-Healing (Flow C)

#### Talking Points
> "Now for the core breakthrough of this platform: autonomous model self-healing. When customer economic conditions change, input data distributions drift. If left alone, model accuracy silently decays. Watch how our closed loop detects drift, retrains on point-in-time correct data, validates through staging gates, and deploys without human intervention."

#### Action 4: Trigger the Closed-Loop Self-Healing Orchestration
Execute in Terminal Pane 1:
```bash
make demo-model
```

**Walk through the live stages as the script prints:**

1. **Baseline Traffic & Reference Comparison:**
   * Script sends baseline traffic. Show Tab 1 (ML Platform Health): PSI across all 6 features is $<0.02$ (green).
2. **Synthetic Drift Injection:**
   * Script introduces distribution drift in `income`, `debt_to_income`, and `delinquencies_2yrs`.
   * **Watch Tab 1 in Grafana:** The PSI gauge surges past the 0.20 threshold (orange/red line), and `model_drift_detected` gauge flips to `1`.
3. **Automated Kubernetes Retrain Job:**
   * Look at Terminal Pane 2 (Watch pane): A new pod appears: `retrain-2-<timestamp>-...` in `data` namespace.
   * Explain: *"The drift monitor detected consecutive breaches and triggered a Kubernetes batch job using Feast point-in-time historical joins—guaranteeing zero future data leakage."*
4. **Quality Gates & Staging Validation (`scripts/gate.py`):**
   * The retrain job registers a new candidate version in MLflow.
   * The pipeline verifies:
     * AUC $\ge 0.70$ (quality floor).
     * No regression vs current champion.
     * Schema compatibility.
   * It deploys `model-api-staging` (port 8081) and fires a 2,000-request load check.
5. **Zero-Downtime Production Rollout:**
   * Look at Terminal Pane 2: `model-api` pods undergo a zero-downtime rolling update (`maxUnavailable: 0`).
   * The candidate is promoted to alias `champion` in MLflow.
6. **Closing the Loop:**
   * The script calls `/model-info`: the active version has updated!
   * The drift monitor evaluates the new model against its new `reference.parquet` baseline.
   * **Watch Tab 1 in Grafana:** The PSI drops back to $<0.02$, and `model_drift_detected` returns to `0`. The loop has autonomously healed!

---

### Minute 08:30 – 10:00: Lineage, Auditing & Q&A Wrap-Up

#### Action 5: Demonstrate Auditability & Lineage
1. **Switch to Tab 3 (MLflow Registry):**
   * Show `loan-default` model versions.
   * Click on the new champion run: show logged parameters (git SHA, Feast service), metrics (AUC, loss), and artifacts (`model/`, `reference.parquet`, `eval_set.parquet`).
2. **Switch to Grafana Explore (Loki):**
   * Run query: `{namespace="serving", app="model-api"}`.
   * Show structured JSON logs with `ts`, `level`, `request_id`, `customer_id`, and `score`.

#### Closing Summary
> "In summary: we have demonstrated a 100% Infrastructure-as-Code platform running on Kubernetes with full AWS parity, zero-downtime rolling updates, automated infrastructure remediation, and an autonomous closed-loop drift retraining cycle with verifiable feature parity. Thank you, and we welcome your questions."

---

## 3. Fallback Protocols (Live Demo Safety Net)

If an unexpected glitch occurs during the live presentation, follow these instant recovery steps:

| Failure Scenario | Instant Diagnostic & Fix | Fallback Demonstration |
|---|---|---|
| **Port-Forward Dropped** (`curl: Connection refused`) | Run `make forward` in a new terminal tab to re-establish background tunnels. | Use direct NodePorts (`localhost:30080` for API, `localhost:30000` for Grafana). |
| **Redis Cache Wiped** (`EntityNotFoundException`) | Run `make feast-rematerialize` (takes 10 seconds to reload 10,000 customers). | Show offline Parquet files in MinIO console (`http://localhost:9001`). |
| **Demo Interrupted / Stuck** | Run `make reset-demo` to restore pristine baseline within 15 seconds. | Reference pre-recorded evidence logs: `docs/model-selfheal.md` and `docs/alert-drill.md`. |
| **Cluster Crash / Starved RAM** | `make down && make up && make deploy-all` (18m rebuild). | Show captured terminal transcripts and full viva prep guide in `docs/`. |

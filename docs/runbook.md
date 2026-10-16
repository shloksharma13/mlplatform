# Platform Operational Runbook & Incident Response (P10.2)

This runbook documents production operations, alert triage procedures, disaster recovery, demo baselining, and security credential management for the Autonomous Self-Healing Machine Learning Platform.

---

## 1. Alert Response Directory

Alerts are defined in `infra/charts/observability/templates/alerts.yaml`. Automated remediations are handled by the `remediator` service (`remediator/`) and verified via `make demo-infra` (`scripts/chaos_alert.sh`).

### 1.1 ModelApiDown
* **Severity:** Critical
* **Expression:** `sum(up{job="model-api"}) == 0` for `1m`
* **Impact:** 100% loss of real-time credit scoring inference. Customer requests return connection refused or 502/503.
* **Automated Action:** Remediator emits a Grafana alert annotation and triggers a rolling restart (`kubectl rollout restart deployment/model-api -n serving`).
* **Human Triage Procedure:**
  1. Inspect pod status in serving namespace:
     ```bash
     kubectl get pods -n serving -l app.kubernetes.io/name=model-api
     ```
  2. If pods are in `CrashLoopBackOff`, view recent container crash logs:
     ```bash
     kubectl logs -n serving deploy/model-api -c api --tail=100
     ```
  3. Verify upstream dependency connectivity:
     ```bash
     # Check Redis online feature store
     kubectl exec -n data deploy/redis -- redis-cli ping
     # Check Postgres prediction log database
     kubectl exec -n data statefulset/postgres -- pg_isready -U mlp
     # Check MLflow tracking server
     curl -s http://localhost:5000/health || kubectl get pods -n data -l app=mlflow
     ```
  4. If a recent deployment broke the service, execute an atomic rollback:
     ```bash
     helm rollback model-api -n serving
     ```

### 1.2 PodCrashLooping
* **Severity:** Warning
* **Expression:** `increase(kube_pod_container_status_restarts_total{namespace="serving"}[10m]) > 2`
* **Impact:** Serving capacity reduced, risk of cascading latency spikes under load.
* **Automated Action:** Remediator logs the incident and tags Grafana dashboard timeline.
* **Human Triage Procedure:**
  1. Identify restarting pods:
     ```bash
     kubectl get pods -n serving --sort-by='.status.containerStatuses[0].restartCount'
     ```
  2. Inspect exit code and termination reason:
     ```bash
     kubectl describe pod -n serving <pod-name> | grep -A 10 "Last State:"
     ```
  3. Common causes:
     * OOMKilled: Check if batch prediction payloads exceed container memory limit (512Mi).
     * Bad Model Version: Verify that `model.version` in Helm matches an existing version in MLflow.
     * Startup probe timeout: Model loading or Feast registry download took > 60s.

### 1.3 HighErrorRate
* **Severity:** Critical
* **Expression:** `(sum(rate(model_api_requests_total{status=~"5.."}[2m])) / sum(rate(model_api_requests_total[2m]))) > 0.05` for `2m`
* **Impact:** >5% of inference requests are failing with HTTP 500.
* **Automated Action:** Paged to operations; no automatic destructive actions taken.
* **Human Triage Procedure:**
  1. Query Loki for error stack traces in Grafana Explore:
     ```logql
     {namespace="serving", app="model-api"} |= "ERROR"
     ```
  2. Check if errors are localized to specific customer IDs (e.g. missing features) or systemic:
     ```bash
     kubectl logs -n serving deploy/model-api -c api | grep '"level":"ERROR"' | tail -n 20
     ```
  3. If Redis cache has been flushed and is returning 404s, repopulate feature store:
     ```bash
     make feast-rematerialize
     ```
  4. If tied to a recent model deployment, roll back to prior release:
     ```bash
     helm rollback model-api -n serving
     ```

### 1.4 HighP95Latency
* **Severity:** Warning
* **Expression:** `histogram_quantile(0.95, sum(rate(model_api_request_duration_seconds_bucket[1m])) by (le)) > 0.300` for `1m`
* **Impact:** P95 response latency exceeds 300ms SLA.
* **Automated Action:** Remediator automatically patches HPA `minReplicas` from 2 to 4 to distribute load, and adds an event annotation in Grafana.
* **Human Triage Procedure:**
  1. Check current HPA status and pod counts:
     ```bash
     kubectl get hpa -n serving model-api
     kubectl top pods -n serving
     ```
  2. Verify if latency is synthetic (injected chaos) or organic traffic surge:
     ```bash
     # To clear synthetic latency injection:
     for pod in $(kubectl get pods -n serving -l app.kubernetes.io/name=model-api -o jsonpath='{.items[*].metadata.name}'); do
       kubectl exec -n serving $pod -c api -- curl -s -X POST "http://localhost:8080/debug/latency?ms=0" || true
     done
     ```
  3. If traffic is organic, monitor HPA scale-out. Once traffic drops, remediator resets minReplicas back to 2 after cooldown.

### 1.5 ModelDriftDetected
* **Severity:** Warning
* **Expression:** `max(model_drift_detected) == 1` for `1m`
* **Impact:** Serving data distribution has significantly diverged from training reference (PSI > 0.20 or KS p < 0.01). Prediction accuracy may degrade.
* **Automated Action:** Remediator annotates Grafana. The `drift-monitor` autonomously creates a Kubernetes Retrain Job (`retrain-*`) in `data` namespace.
* **Human Triage Procedure:**
  1. Inspect the ML Platform Health dashboard in Grafana (`http://localhost:3000`).
  2. Check which features breached PSI / KS thresholds:
     ```sql
     -- Query drift state table
     kubectl exec -n data statefulset/postgres -- psql -U mlp -d mlp -c \
       "SELECT evaluated_at, feature_name, metric_type, metric_value, threshold FROM drift_state WHERE drift_detected = true ORDER BY evaluated_at DESC LIMIT 10;"
     ```
  3. Monitor autonomous retrain job progress:
     ```bash
     kubectl get jobs -n data -l app=retrain
     kubectl logs -n data -l app=retrain --tail=50
     ```

### 1.6 RetrainFailed
* **Severity:** Critical
* **Expression:** `kube_job_status_failed{namespace="data", job_name=~"retrain-.*"} > 0`
* **Impact:** Autonomous retraining failed; stale model remains in production.
* **Automated Action:** None. Requires engineer inspection.
* **Human Triage Procedure:**
  1. Find the failed job name and inspect pod termination status:
     ```bash
     kubectl get jobs -n data
     kubectl logs -n data job/<failed-job-name>
     ```
  2. Verify historical feature retrieval from Feast / MinIO:
     ```bash
     kubectl exec -n data deploy/minio -- mc ls minio/ml-data/feast/
     ```
  3. If failure was due to insufficient post-parent samples (<500 samples), verify that new inference records are being logged to `prediction_log`.
  4. Once root cause is resolved, delete the failed job:
     ```bash
     kubectl delete job -n data <failed-job-name>
     ```

---

## 2. Common Failures & Diagnostic Decision Trees

### 2.1 Redis Feature Cache Wiped / Empty
* **Symptom:** `model-api` returns HTTP 404 / 500 on valid customer IDs (`EntityNotFoundException: Entity not found in online store`).
* **Root Cause:** Redis container restarted without AOF persistence (`--appendonly no`), wiping in-memory state.
* **Resolution:**
  ```bash
  # Repopulate Redis cache from MinIO offline parquet
  make feast-rematerialize
  # Verify hydration for customer 1
  uv run python -c "
  import feast
  fs = feast.FeatureStore('feast_repo')
  print(fs.get_online_features(features=['customer_credit_features:credit_score'], entity_rows=[{'customer_id': '1'}]).to_dict())
  "
  ```

### 2.2 Postgres Prediction Logger Buffer Exhaustion
* **Symptom:** API logs show `PredictionLogger queue full; dropped prediction record`.
* **Root Cause:** PostgreSQL database is unreachable, slow, or locked, causing the in-memory 10,000-item deque to overflow.
* **Resolution:**
  1. Check PostgreSQL pod and connection limits:
     ```bash
     kubectl exec -n data statefulset/postgres -- psql -U mlp -d mlp -c "SELECT count(*) FROM pg_stat_activity;"
     ```
  2. Check disk space on PostgreSQL volume:
     ```bash
     kubectl exec -n data statefulset/postgres -- df -h /var/lib/postgresql/data
     ```
  3. Restart PostgreSQL if hung:
     ```bash
     kubectl rollout restart statefulset/postgres -n data
     ```

### 2.3 MLflow Registry Connection Timeout
* **Symptom:** `model-api` pods fail startup probe and never enter Ready state (`503 Service Unavailable`).
* **Root Cause:** MLflow tracking server is down or unable to reach its Postgres backend.
* **Resolution:**
  ```bash
  kubectl logs -n data deploy/mlflow --tail=50
  kubectl rollout restart deployment/mlflow -n data
  ```

### 2.4 Staging Candidate Release Rejection
* **Symptom:** Retrain job completes, but `candidate` model is never promoted to `champion`.
* **Root Cause:** Candidate failed quality gate checks in `scripts/gate.py`:
  * Test AUC < 0.70 (AUC floor breach).
  * Candidate AUC < (Champion AUC - 0.02) (Regression gate).
  * Feature schema mismatch between candidate and active feature service.
* **Resolution:**
  1. Inspect release log in MLflow or gate output:
     ```bash
     uv run python scripts/gate.py --candidate-version <version>
     ```
  2. If data drift requires hyperparameter tuning or new features, update `ml/train.py` and run a manual retraining cycle.

---

## 3. How to Reset the Repeatable Demo Baseline

The platform includes a dedicated idempotent reset tool: `scripts/reset_demo.py` (accessible via `make reset-demo`).

### 3.1 What Reset Does
1. **Flushes Redis:** Wipes all online keys in `redis://redis.data.svc:6379/0`.
2. **Re-materializes Features:** Ingests pristine baseline features for 10,000 customers from `s3://ml-data/feast/features_latest.parquet` into Redis.
3. **Cleans Retraining State:** Deletes lingering Kubernetes retrain jobs, pods, and ConfigMaps in `data` namespace.
4. **Resets Staging:** Purges ephemeral `model-api-staging` Helm releases.
5. **Sets Active Model to Champion v2:** Verifies MLflow alias `champion` points to baseline v2.
6. **Clears Drift Metrics:** Updates Prometheus gauges (`model_drift_detected = 0`, resets PSI/KS series).
7. **Warms Caches:** Sends warm-up `/predict` requests to prime connection pools.

### 3.2 Command Execution
```bash
make reset-demo
```
**Expected Output:**
```
[INFO] Resetting Redis online feature store...
[INFO] Re-materializing Feast features into Redis (10,000 customers)...
[INFO] Cleaning up stale retrain jobs and staging Helm releases...
[INFO] Champion model verified as version 2 (AUC 0.835)...
[INFO] Drift metrics cleared in Prometheus / drift_state...
[INFO] Cache warm-up complete. Demo baseline is READY.
```

---

## 4. Secret Rotation Procedures

All platform secrets are managed in Kubernetes secrets (`data` and `serving` namespaces) and Terraform variables.

### 4.1 Rotating PostgreSQL Application Credentials
1. Generate new password:
   ```bash
   NEW_PG_PASS=$(openssl rand -base64 24 | tr -dc 'a-zA-Z0-9')
   ```
2. Update password in PostgreSQL engine:
   ```bash
   kubectl exec -n data statefulset/postgres -- psql -U postgres -c "ALTER USER mlp WITH PASSWORD '$NEW_PG_PASS';"
   ```
3. Update Kubernetes secrets:
   ```bash
   # Update database-url in serving namespace
   kubectl create secret generic postgres-app -n serving \
     --from-literal=database-url="postgresql://mlp:${NEW_PG_PASS}@postgres.data.svc:5432/mlp" \
     --dry-run=client -o yaml | kubectl apply -f -

   # Update database-url in data namespace (for MLflow and Retrain jobs)
   kubectl create secret generic postgres-app -n data \
     --from-literal=database-url="postgresql://mlp:${NEW_PG_PASS}@postgres.data.svc:5432/mlp" \
     --dry-run=client -o yaml | kubectl apply -f -
   ```
4. Perform zero-downtime rolling update of dependent workloads:
   ```bash
   kubectl rollout restart deploy/model-api -n serving
   kubectl rollout restart deploy/mlflow -n data
   kubectl rollout restart deploy/drift-monitor -n data
   ```

### 4.2 Rotating MinIO / S3 Storage Credentials
1. Update MinIO root credentials via Kubernetes secret `minio-root`:
   ```bash
   NEW_MINIO_USER="minioadmin"
   NEW_MINIO_PASS=$(openssl rand -base64 24 | tr -dc 'a-zA-Z0-9')

   kubectl create secret generic minio-root -n data \
     --from-literal=root-user="$NEW_MINIO_USER" \
     --from-literal=root-password="$NEW_MINIO_PASS" \
     --dry-run=client -o yaml | kubectl apply -f -
   ```
2. Roll out MinIO:
   ```bash
   kubectl rollout restart deploy/minio -n data
   ```
3. Mirror credentials into `minio-model` in `serving` and `feast-minio` in `data`.
4. Perform rolling restart of `mlflow` and `model-api`.

### 4.3 Rotating Grafana Admin Password
```bash
NEW_GRAFANA_PASS=$(openssl rand -base64 16)
kubectl exec -n observability deploy/kube-prometheus-stack-grafana -c grafana -- \
  grafana-cli admin reset-admin-password "$NEW_GRAFANA_PASS"
```

---

## 5. Clean-Room Disaster Recovery & Platform Rebuild (P9.2)

To reconstruct the entire platform from zero state on a fresh host:

```bash
make down && make up && make deploy-all && make e2e
```

### Measured Timing & Milestones (2026-10-04)

Total wall-clock duration: **18m 46s** (automated, zero human interventions).

1. `make down` (~15s): Terraform destroys resources, deletes Kind cluster and local registry.
2. `make up` (~9m 20s): Terraform provisions the 3-node Kind cluster, builds & pushes bootstrap `mlflow:dev` image, deploys Postgres, MinIO, Redis, MLflow, metrics-server, and the kube-prometheus stack, Loki, Alloy, and dashboards.
3. `make deploy-all` (~2m 10s): Builds & pushes all service images (`model-api`, `training`, `remediator`, `drift-monitor`, `retrain`), creates namespaces, deploys remediator, seeds Feast offline store, materializes Redis, trains baseline MLflow champion v1 & candidate v2, deploys `model-api`, deploys drift monitor, and resets demo baseline.
4. `make e2e` (~8m 26s): Runs the full end-to-end test suite (`test_serving_survives_pod_deletion`, `test_model_self_healing_loop`, `test_serving_contract`). All 3 test suites PASS.

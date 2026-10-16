# docs/cicd-failure-path.md (P4.6)

Local demonstration that a broken candidate fails in staging while
production keeps serving. (The GitHub `cd.yml` staging gate for this path
runs on the P4.1 self-hosted runner.)

## Steps run (2026-10-02)

1. Created branch `p4-demo-broken` from `main`.
2. Induced failure: `model_api/deps.py::build_model_source` raises
   `RuntimeError("P4.6 induced startup failure")` (branch-only commit).
3. Built + pushed `localhost:5001/model-api:broken` (digest recorded).
4. Deployed to staging (mirrors `cd.yml` staging step, short timeout):
   `helm upgrade --install model-api-staging infra/charts/model-api
   --namespace staging --set image.tag=broken
   --set service.nodePort=30081 --atomic --wait --timeout 2m`
   → `Error: release model-api-staging failed, and has been uninstalled
   due to atomic being set: context deadline exceeded`.
   The broken pods never passed startup/readiness, so the release was
   rolled back and removed.
5. Production untouched (same minute):
   `curl localhost:8080/readyz` → 200;
   `kubectl get deploy model-api -n serving` image still
   `localhost:5001/model-api:p4demo`.

## Local startup-failure confirmation

`docker run --rm localhost:5001/model-api:broken` exits with
`Application startup failed. Exiting.` (non-zero), confirming the candidate
can never go live.

## Result

Broken candidate rejected at the staging gate; prod kept serving.
Demo branch deleted afterwards; `main` was never touched by the bad code.

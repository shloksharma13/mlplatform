# Model release drills (P7.4) — observed 2026-10-03

All drills ran through the same scripts the workflow uses
(`scripts/model_release_local.sh` = local twin of
`.github/workflows/model-release.yml`). Registry ops use plain MLflow REST
from the host; anything touching model bytes runs in-cluster (presigned
artifact URLs are cluster-internal — see `docs/DECISIONS.md`).

Baseline before drills: `loan-default` v1 champion, prod serving `file`/`v1`
era just cut over to MLflow v1. Drill (a) promotes the first MLflow-trained
v2; later drills leave prod on v2.

## (a) Good candidate → full green

- `make train-mlflow` → `version=2 alias=candidate
  validation_auc=0.8562 test_auc=0.8581` (same rows+seed as v1).
- `make model-release-local VERSION=2`:
  - step 1 gate: `GATE verdict=PASS` — candidate 0.8581 vs champion 0.8581,
    6 features match, 12000 rows scored (`gate_report.json` kept).
  - step 2 staging `model-api-staging` (NodePort 30081) deployed v2.
  - step 3 loadcheck vs :8081: 2000 reqs, error_rate 0.0, p95 ~15ms → PASS.
  - step 4 champion 1 → 2; step 5 prod deploy v2; step 6 every pod
    `/model-info == "2"` (run `4cdfef0e…`); step 7 100-req check PASS.
- End state: prod v2, aliases `champion=2, candidate=2`.

## (b) Bad candidate (shuffled labels, AUC≈0.5) → gate rejects, prod unchanged

- `SHUFFLE=1 make train-mlflow` → `version=3 alias=candidate
  validation_auc=0.4984 test_auc=0.5079` (`--shuffle-labels` permutes labels
  pre-split; drill-only flag documented in `ml/train.py --help`).
- `make model-release-local VERSION=3` stops at step 1: `GATE verdict=REJECT`
  (`candidate_auc=0.5079 floor=0.75`; note the champion also scores ~0.50 on
  v3's shuffled hold-out, so the floor — not the regression check — fires).
- Reject path observed: v3 tagged `gate_status=rejected`, `candidate` alias
  removed (aliases back to `champion=2` only), workflow exit non-zero.
- Prod untouched: `/model-info` still `"2"` throughout.

## (c) Staging failure → rejected, prod unchanged

- `make train-mlflow` → `version=4 alias=candidate`, gate v4 PASS.
- Staging deployed v4 (`/model-info` on :8081 reported `"4"`), then 500ms
  latency chaos injected on both staging pods.
- `loadcheck --total 2000 --rps 20` vs :8081: error_rate 0.0 but
  p95 522ms > 150ms budget → `LOADCHECK verdict=FAIL` (exit 1).
- Rollback path observed: `helm rollback model-api-staging` (staging back to
  v2), v4 tagged `gate_status=rejected`, `candidate` alias removed.
- Prod untouched: `/model-info` on :8080 `"2"`, aliases `champion=2`.
- Note: a model that raises at *load* cannot reach staging in this
  architecture — the gate loads the identical bytes first and fails there
  (unit tests cover every gate rejection mode). The meaningful staging gate
  is latency/errors, exercised here for real.

## (d) Post-promotion prod failure → rollback works

- `helm upgrade model-api ... --set model.version=999` (no `--atomic`):
  `UPGRADE FAILED: context deadline exceeded`. New ReplicaSet 2 desired / 0
  available — pods log `provider not ready; retrying (provider=mlflow)` and
  exit 1 after the 60s startup budget; old ReplicaSet keeps 3/3 pods serving
  v2 (`maxUnavailable=0`), so `/model-info` never stops reporting `"2"`.
- `helm rollback model-api -n serving` → success; failing pods terminate,
  prod steady on v2 (run `4cdfef0e…`), alias `champion=2` intact.

## Side fix (kept, not reverted)

`scripts/deploy.sh` staging deploys smoke-tested *production's* URL
(`smoke.sh` default `:8080`). Staging now smokes `:8081` explicitly.

# docs/branch-to-deployment.md (P4.5)

Local demonstration of the branch → merge → build → deploy → smoke flow.
(The GitHub PR + self-hosted-runner CD run for this flow is documented for
the `cd.yml` workflow; the laptop runner itself is a HUMAN step, P4.1.)

## Steps run (2026-10-02)

1. Created branch `p4-demo-trivial` from `main`.
2. Trivial change: one comment line in `model_api/main.py` (`/predict`).
3. `make lint` → All checks passed. `make test` → 28 passed, 88.07%.
4. Committed on the branch, merged to `main` with `--no-ff`.
5. Rebuilt + pushed a distinct tag and deployed (mirrors `cd.yml` steps):
   `docker build -t localhost:5001/model-api:p4demo`,
   `docker push localhost:5001/model-api:p4demo`,
   `scripts/deploy.sh model-api serving p4demo` → revision 5, deployed.
6. Verified the rollout picked up the new image:
   `kubectl get deploy model-api -n serving -o jsonpath=...image`
   → `localhost:5001/model-api:p4demo`.
7. `scripts/smoke.sh serving model-api` → `/readyz` 200,
   `/predict` 3 predictions, `smoke PASS`.

## Result

Branch change flowed to a live deployment with a new image tag and a green
smoke. (Full GitHub PR + `cd.yml` run pending the P4.1 self-hosted runner.)
Demo branches deleted afterwards; `main` retains the merge.

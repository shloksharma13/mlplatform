## 1. Agent Operating Contract (copy to `AGENTS.md`)

1. **Spec is law.** If the spec is ambiguous, choose the simplest option that satisfies the acceptance check, record it in `docs/DECISIONS.md` (date, decision, reason), and proceed. Do not ask the human for things you can decide.
2. **One task at a time.** Finish, verify, commit, tick the box in `TASKS.md`, append to `PROGRESS.md`, then start the next.
3. **Verify, don't assume.** Run the acceptance command. Paste a 3–10 line summary of real output into `PROGRESS.md`. If the command fails, fix it; do not edit the acceptance check to make it pass.
4. **Pin everything.** At implementation time look up the latest *stable* version of each tool/chart/image/Python package from its official source, pin it exactly (no `latest`, no floating ranges in lockfiles), and record it in `docs/VERSIONS.md`. Your training data about versions may be stale; the registries are the truth.
5. **Makefile is the interface.** Every repeatable action is a `make` target. README documents targets, not raw commands.
6. **Idempotent and re-runnable.** `make up` twice must not break. Scripts must be safe to rerun. `make down && make up` must rebuild everything from scratch with zero manual steps (this is itself a P3 acceptance test).
7. **No secrets in git.** Credentials come from `.env` (gitignored) → Terraform `random_password` / Kubernetes Secrets. Provide `.env.example`. Add a secret-scan step (gitleaks) to CI.
8. **Small files, clear names, type hints, docstrings** on public functions. Python 3.11. Lint with `ruff`, format with `ruff format`, test with `pytest`. Target ≥ 80% line coverage on the runtime Python packages (`mlp_common/`, `model_api/`, and later `drift_monitor/`, `retrain/`, `remediator/`).
9. **Structured logging.** All Python services log JSON lines to stdout with fields: `ts, level, service, request_id, msg` plus context fields.
10. **Fail loudly.** No bare `except:`; no swallowed errors. Services must exit non-zero on unrecoverable startup errors so Kubernetes can restart them.
11. **Resource-conscious.** Target machine: 8 CPU / 16 GB RAM laptop. Set requests/limits on every pod. Prefer single-replica for infra components locally. If the cluster is starved, reduce replicas of infra components, not of `model-api`.
12. **Cost safety.** The agent MUST NOT run `terraform apply` against Azure or any command that creates billable resources. Only `init`, `validate`, `plan` are allowed for `infra/envs/azure`. Applying is **[HUMAN]**.
13. **Context hygiene.** If your context is getting long: update `PROGRESS.md` with "Next action: …", then continue. On resume, read `AGENTS.md`, `TASKS.md`, the tail of `PROGRESS.md`, and the current phase only.
14. **Don't gold-plate.** Anything marked *stretch* is done only after all must-haves in all phases pass.
15. **Honesty over polish.** If something is simulated (e.g., synthetic data, simulated drift), say so in docs. Never present simulated results as real.

### Authorized test deployment exception — 2026-10-13

The user explicitly replied “Yes—override rule 12 for this deployment.” Azure
provisioning for this test session is authorized, as is teardown when requested.
The user also selected automatic full teardown after two hours. Use project-owned
resource groups only; delete application, AKS node resources, and state storage
for full shutdown. This exception does not authorize unrelated Azure resources.

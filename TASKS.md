# Tasks

Checkboxes are changed only after the corresponding acceptance check has been observed.

## GitHub and Azure migration — 2026-10-13

- [x] G1 Compact infrastructure files and preserve the working application. Acceptance: `make lint test check-infra secret-scan`.
- [x] G2 Replace laptop Actions with pinned GitHub-hosted CI and manually approved Azure CD. Acceptance: `make check-actions check-infra lint test secret-scan` plus cloud deployment script contract tests.
- [x] G3 Push the verified repository to GitHub and observe CI. Acceptance: remote commit matches local HEAD and GitHub CI succeeds.

## Deletion and directory cleanup — 2026-10-13

- [x] C1 Delete obsolete files and flatten wrapper directories without changing runtime features. Acceptance: `make lint test check-infra check-actions check-charts secret-scan check-images` and GitHub CI.

## Azure test deployment and full teardown — 2026-10-13

- [x] A1 Add a state-independent full kill switch with ownership guards and verified deletion. Acceptance: `make lint test check-actions check-infra secret-scan`; mocked CLI teardown tests cover partial deployments, reruns, ownership rejection, and failures.
- [x] A2 Host the dashboard and adapt its operations to AKS; verify cloud configuration and images.
- [x] A3 Configure GitHub OIDC/state, provision through Actions, and verify live serving/dashboard/retraining with a documented kill action. Authorized test deployment exception is recorded in AGENTS.md.

## P0 — Foundations

- [x] P0.1 Create repository layout and foundational files.
- [x] P0.2 Create the Makefile interface.
- [x] P0.3 Implement prerequisite verification.
- [x] P0.4 Build the shared `mlp_common` package.
- [x] P0.5 Configure linting, tests, formatting, and pre-commit.
- [x] P0.6 Establish the Git workflow and PR template.

## P1 — Data, Model, Serving Stub, Containers

- [x] P1.1 Generate deterministic synthetic data.
- [x] P1.2 Train and evaluate the file-backed model.
- [x] P1.3 Implement model-api and provider protocols.
- [x] P1.4 Implement asynchronous prediction logging.
- [x] P1.5 Add model-api tests.
- [x] P1.6 Add the model-api container image.
- [x] P1.7 Wire seed, train, build, and hadolint checks.

## P2 — Kubernetes, HPA, Fault Tolerance

- [x] P2.1 Create the local registry script.
- [x] P2.2 Add the three-node kind configuration.
- [x] P2.3 Install and verify metrics-server.
- [x] P2.4 Create the model-api Helm chart.
- [x] P2.5 Implement atomic deployment and smoke scripts.
- [x] P2.6 Add pod and load chaos scripts.
- [x] P2.7 Add the traffic generator.
- [x] P2.8 Prove zero-error rolling updates.

## P3 — Infrastructure as Code

- [x] P3.1 Implement the kind Terraform module.
- [x] P3.2 Implement the registry Terraform module.
- [x] P3.3 Create data-service Helm charts.
- [x] P3.4 Implement the platform-services module.
- [x] P3.5 Compose the local environment.
- [x] P3.6 Create Azure-parity modules and environment.
- [x] P3.7 Add Terraform formatting and validation checks.
- [ ] P3.8 Add report-only infrastructure scanning. (stretch)

## P4 — CI/CD

- P4.1 Superseded by G2/G3: GitHub-hosted runners replace the laptop runner; no runner installation is required.
- [x] P4.2 Implement continuous integration.
- [x] P4.3 Implement local continuous deployment.
- [x] P4.4 Remove model artifacts from the application image.
- [x] P4.5 Demonstrate branch-to-deployment flow.
- [x] P4.6 Prove the deployment failure path.
- [ ] P4.7 Add local `act` configuration. (stretch; `.actrc` written, `act` not installed)

## P5 — Observability and Automated Response

- [x] P5.1 Install the Prometheus stack.
- [x] P5.2 Install Loki and Grafana Alloy.
- [x] P5.3 Enable model-api service discovery.
- [x] P5.4 Provision three dashboards.
- [x] P5.5 Define alert rules.
- [x] P5.6 Implement the remediator.
- [x] P5.7 Prove the alert-response drill.
- [ ] P5.8 Add a Loki-based alert. (stretch)

## P6 — Feature Store and Model Registry

- [x] P6.1 Deploy MLflow.
- [x] P6.2 Create the Feast repository.
- [x] P6.3 Seed and verify Feast data.
- [x] P6.4 Train via Feast and MLflow.
- [x] P6.4b Prove training-serving feature parity.
- [x] P6.5 Implement Feast and MLflow providers.
- [x] P6.6 Enrich model metadata.
- [x] P6.7 Prove the full serving flow.
- [x] P6.8 Document lineage.

## P7 — Model-Aware CI/CD

- [x] P7.1 Implement candidate gates.
- [x] P7.2 Implement staging load checks.
- [x] P7.3 Implement model release automation.
- [x] P7.4 Run the release drills.
- [ ] P7.5 Add a canary path. (stretch)

## P8 — Drift Detection and Automated Retraining

- [x] P8.1 Implement and test drift math.
- [x] P8.2 Implement the drift monitor.
- [x] P8.3 Implement Kubernetes retrain triggering.
- [x] P8.4 Implement retraining and dispatch.
- [x] P8.5 Export retraining metrics.
- [x] P8.6 Complete the ML health dashboard.
- [x] P8.7 Implement repeatable drift demos.
- [x] P8.8 Test retraining safeguards.
- [ ] P8.9 Add performance monitoring. (stretch)
- [ ] P8.10 Add score-distribution drift. (stretch)

## P9 — Integration, Hardening, Azure Port

- [x] P9.1 Build end-to-end tests.
- [x] P9.2 Prove a clean-room rebuild.
- [x] P9.3 Complete hardening.
- [x] P9.4 Document and test failure modes.
- [x] P9.5 Prepare the Azure port.
- [x] P9.6 Execute the Azure port.

## P10 — Documentation and Viva Pack

- [x] P10.1 Document architecture.
- [x] P10.2 Complete the runbook.
- [x] P10.3 Write the technical design document.
- [x] P10.4 Add the contributions template.
- [x] P10.5 Write the demo runbook.
- [x] P10.6 Complete the README.
- [x] P10.7 Add and update the human-provided viva guide.

DOCKER_NETWORK ?= default
SHELL := /usr/bin/env bash
.DEFAULT_GOAL := help

.PHONY: help bootstrap preflight lint fmt test build up down deploy forward seed-data train \
	traffic demo-infra demo-model reset-demo terraform-local-apply terraform-local-destroy \
	build-training publish-feast-data feast-apply feast-materialize feast-verify \
	train-mlflow gate-candidate feast-rematerialize model-release-local \
	deploy-monitor e2e deploy-all azure-init azure-validate azure-plan azure-apply azure-destroy \
	deploy-azure destroy-azure azure-preflight deploy-azure-full train-azure \
	azure-sleep azure-wake azure-kill-compute azure-kill ui

help: ## Show the supported project commands.
	@awk 'BEGIN {FS = ":.*## "; printf "MLPlatform targets:\n\n"} /^[a-zA-Z0-9_-]+:.*## / {printf "  %-22s %s\n", $$1, $$2}' $(MAKEFILE_LIST)

bootstrap: ## Verify the laptop prerequisites and record their versions.
	@bash scripts/bootstrap.sh

preflight: ## Verify host kernel limits required by kind.
	@test $$(cat /proc/sys/fs/inotify/max_user_watches 2>/dev/null || echo 0) -ge 524288 || (echo "Kernel limits too low. Run: sudo sysctl -w fs.inotify.max_user_watches=524288" && exit 1)

lint: ## Lint all implemented Python code.
	@uv run ruff check .
	@uv run ruff format --check .

fmt: ## Format all implemented Python code.
	@uv run ruff check --fix .
	@uv run ruff format .

test: ## Run the unit test suite and coverage gate.
	@uv run pytest --cov=mlp_common --cov=model_api --cov-report=term-missing

.PHONY: check-infra secret-scan github-login github-push tools check-actions check-charts security azure azure-check
tools: ## Install checksum-verified Linux CI tools (TOOLSET="...").
	@python3 scripts/tools.py $(or $(TOOLSET),actionlint gitleaks hadolint kubeconform trivy)

check-actions: ## Validate GitHub Actions workflows.
	@actionlint -shellcheck=""

check-charts: ## Validate rendered Kubernetes manifests and vendored CRD schemas.
	@uv run python scripts/tools.py check-charts

.PHONY: check-images
check-images: ## Build and import-smoke all six container targets without deploying.
	@hadolint infra/Dockerfile
	@for service in model-api drift-monitor remediator training retrain mlflow dashboard; do \
		docker build --network "$(DOCKER_NETWORK)" --file infra/Dockerfile --target "$$service" --tag "mlp-check-$$service" . || exit 1; \
	done
	@$(MAKE) smoke-images

.PHONY: smoke-images
smoke-images: ## Import real service entry points and ensure models/data are absent.
	@for service in model-api drift-monitor remediator training retrain mlflow dashboard; do \
		module="$${service//-/_}.main"; \
		case "$$service" in training) module="feast, ml.train";; mlflow) module=mlflow;; dashboard) module=ui.app;; esac; \
		docker run --rm --entrypoint python "mlp-check-$$service" -c "import $$module; print('$$service entry point OK')" || exit 1; \
	done
	@docker run --rm --entrypoint sh mlp-check-model-api -c 'test ! -e /app/data/features.parquet && test ! -e /app/artifacts/model.joblib'
	@docker run --rm --entrypoint bash mlp-check-retrain -c 'helm template model-api infra/charts/model-api >/dev/null && test -r infra/jobs/gate-job.yaml && for script in scripts/*.sh; do bash -n "$$script" || exit 1; done'

security: ## Reject critical dependency vulnerabilities; report IaC findings.
	@trivy fs --scanners vuln --file-patterns "pip:training.txt" --severity CRITICAL --exit-code 1 --skip-dirs .venv,.terraform --skip-files PROGRESS.md .
	@trivy config --severity HIGH,CRITICAL --exit-code 0 infra

.PHONY: image-scan
image-scan: ## Reject critical vulnerabilities in the model-api smoke-test image.
	@trivy image --scanners vuln --severity CRITICAL --exit-code 1 mlp-check-model-api

azure-check: ## Check cloud workflow configuration and human confirmation without Azure calls.
	@bash scripts/azure.sh check

azure: ## Run a manually selected cloud operation (ACTION=plan by default).
	@bash scripts/azure.sh $(or $(ACTION),plan)
check-infra: ## Validate Terraform, Helm charts, and shell syntax without deploying.
	@terraform fmt -check -recursive infra
	@for env in local azure; do terraform -chdir=infra/envs/$$env init -backend=false -input=false -lockfile=readonly && terraform -chdir=infra/envs/$$env validate || exit 1; done
	@for chart in infra/charts/*; do helm lint "$$chart" || exit 1; done
	@for script in scripts/*.sh infra/modules/*/files/*.sh; do bash -n "$$script" || exit 1; done

secret-scan: ## Scan Git history and staged changes for credentials (redacted output).
	@gitleaks git --redact --no-banner
	@gitleaks git --redact --no-banner --pre-commit --staged

github-login: ## Authenticate GitHub CLI and Git without sharing credentials.
	@gh auth login --hostname github.com --git-protocol https --web
	@gh auth setup-git

github-push: ## Push the verified current branch to origin without force.
	@git push --set-upstream origin HEAD

.PHONY: lock-training
lock-training: ## Refresh the fully pinned training environment while preserving constraints.
	@uv pip compile core/requirements/training.in --python-version 3.11 --constraint core/requirements/training.txt --no-header --no-annotate --output-file core/requirements/training.txt

build: ## Build local service images.
	@hadolint infra/Dockerfile
	@docker build --network "$(DOCKER_NETWORK)" --file infra/Dockerfile --target model-api \
		--tag localhost:5001/model-api:dev .

build-training: ## Build and push the training/Feast image.
	@hadolint infra/Dockerfile
	@docker build --network "$(DOCKER_NETWORK)" --file infra/Dockerfile --target training \
		--tag localhost:5001/training:dev .
	@docker push localhost:5001/training:dev

publish-feast-data: ## Publish Parquet data to the MinIO feast-offline bucket.
	@bash scripts/publish_feast_data.sh

feast-apply: build-training publish-feast-data ## Apply the Feast definitions (in-cluster Job).
	@bash scripts/run_job.sh infra/jobs/feast-apply-job.yaml

feast-materialize: feast-apply ## Materialize Feast features into the Redis online store.
	@bash scripts/run_job.sh infra/jobs/feast-materialize-job.yaml

feast-rematerialize: ## Rewrite the full feature window (recovery after a Redis wipe).
	@bash scripts/run_job.sh infra/jobs/feast-rematerialize-job.yaml

feast-verify: ## Verify the Feast offline and online stores match (P6.3).
	@bash scripts/run_job.sh infra/jobs/feast-verify-job.yaml

train-mlflow: build-training ## Train via Feast and register in MLflow (P6.4).
	@bash scripts/train_mlflow.sh

gate-candidate: build-training ## Score a candidate version (VERSION=N) in-cluster (P7.1).
	@VERSION="$${VERSION:?set VERSION=N}" bash scripts/run_gate.sh

model-release-local: ## Release a candidate to prod (VERSION=N, TAG=dev) (P7.3).
	@bash scripts/model_release_local.sh "$${VERSION:?set VERSION=N}" "$${TAG:-dev}"

deploy-monitor: ## Build and deploy the drift monitor and retrain platform (P8).
	@bash scripts/deploy_monitor.sh

e2e: ## Run the local serving and self-healing end-to-end tests.
	@MLP_RUN_E2E=1 MLP_E2E_RUN_DEMO=1 uv run pytest tests/e2e -q

up: ## Build the complete local platform from scratch.
	@test $$(cat /proc/sys/fs/inotify/max_user_watches 2>/dev/null || echo 0) -ge 524288 || (echo "Kernel limits too low. Run: sudo sysctl -w fs.inotify.max_user_watches=524288" && exit 1)
	@terraform -chdir=infra/envs/local init
	@terraform -chdir=infra/envs/local apply -auto-approve \
		-target=module.registry -target=module.cluster
	@docker build --network "$(DOCKER_NETWORK)" --file infra/Dockerfile --target mlflow --tag localhost:5001/mlflow:dev . && docker push localhost:5001/mlflow:dev
	@terraform -chdir=infra/envs/local apply -auto-approve
	@bash scripts/bootstrap-post.sh

deploy-all: ## Deploy all workloads and seed baselines after cluster creation (P9.2).
	@bash scripts/deploy_all.sh

down: ## Destroy the local platform.
	@terraform -chdir=infra/envs/local destroy -auto-approve || true
	@kind delete cluster --name mlp || true
	@docker rm -f kind-registry || true

deploy: ## Deploy model-api to the local cluster.
	@bash scripts/deploy.sh model-api serving dev

forward: ## Open local port-forwards when NodePorts are unavailable.
	@bash scripts/forward.sh

seed-data: ## Prepare deterministic UCI Taiwan feature snapshots.
	@uv run python -m ml.data.generate --out data --seed 42

train: ## Train and evaluate a model locally.
	@uv run python -m ml.train --mode file --data-dir data --artifacts-dir artifacts

traffic: ## Generate prediction traffic.
	@uv run python scripts/traffic.py --rps 10 --duration 30 --url http://localhost:8080

demo-infra: ## Demonstrate Kubernetes and alert self-healing.
	@bash scripts/chaos_alert.sh

demo-model: ## Demonstrate drift-triggered retraining and release.
	@bash scripts/retrain_and_release.sh

reset-demo: ## Restore the repeatable demo baseline.
	@uv run python scripts/reset_demo.py

terraform-local-apply: ## Apply the local Terraform stack.
	@terraform -chdir=infra/envs/local apply -auto-approve

terraform-local-destroy: ## Destroy the local Terraform stack.
	@terraform -chdir=infra/envs/local destroy -auto-approve

azure-init: ## Initialize Terraform Azure provider (no credentials required).
	@terraform -chdir=infra/envs/azure init -backend=false

azure-validate: ## Validate Terraform Azure configuration syntax.
	@terraform -chdir=infra/envs/azure validate

azure-plan: ## Run Terraform plan against Azure (requires Azure credentials or az login).
	@$(MAKE) azure ACTION=plan

# Compatibility targets share the single Azure operations script.
azure-apply deploy-azure deploy-azure-full: ## [HUMAN] Deploy Azure (CONFIRMATION=DEPLOY).
	@$(MAKE) azure ACTION=deploy

azure-destroy destroy-azure: ## [HUMAN] Destroy Azure (CONFIRMATION=DESTROY).
	@$(MAKE) azure ACTION=destroy

azure-preflight: ## Read-only Azure eligibility check; spends nothing.
	@bash scripts/azure_preflight.sh

train-azure: ## Train on the connected AKS cluster using the shared training Job.
	@TRAINING_IMAGE="$${TRAINING_IMAGE:?set TRAINING_IMAGE to the deployed ACR training image and tag}" bash scripts/train_mlflow.sh

azure-sleep: ## [HUMAN] Stop AKS; disks and managed services still bill.
	@$(MAKE) azure ACTION=sleep

azure-wake: ## [HUMAN] Start AKS (CONFIRMATION=WAKE).
	@$(MAKE) azure ACTION=wake

azure-kill: ## Delete ALL project Azure resources, including state (CONFIRMATION=KILL).
	@$(MAKE) azure ACTION=kill

azure-kill-compute: ## Explain the safer whole-cluster sleep option.
	@echo "Use make azure-sleep to retain in-cluster data; targeted destruction is not automated."
	@exit 1

ui: ## Launch the Central Management and Demo Dashboard (http://localhost:8050).
	@echo "Starting Central Management UI on http://localhost:8050 ..."
	@uv run python -m ui.app

.PHONY: azure-connect
azure-connect: ## Configure passwordless GitHub Azure access (no billable resources).
	@bash scripts/azure.sh connect

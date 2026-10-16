FROM python:3.11.17-slim-trixie@sha256:e88e9763f943ec1834f992a4b51e0f24500486803e8bc534e5767af9ea65f6ce AS builder

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

ENV UV_PROJECT_ENVIRONMENT=/opt/venv UV_HTTP_TIMEOUT=120 UV_HTTP_RETRIES=5
WORKDIR /build
COPY pyproject.toml uv.lock README.md ./
COPY core/ core/
RUN python -m pip install uv==0.13.0 \
    && uv sync --frozen --no-dev --no-editable

FROM python:3.11.17-slim-trixie@sha256:e88e9763f943ec1834f992a4b51e0f24500486803e8bc534e5767af9ea65f6ce AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN groupadd --system --gid 10001 mlplatform \
    && useradd --system --uid 10001 --gid mlplatform --home-dir /app mlplatform

WORKDIR /app
COPY --from=builder /opt/venv /opt/venv
ENV PATH=/opt/venv/bin:$PATH

USER 10001:10001
EXPOSE 8080

FROM runtime AS model-api
CMD ["uvicorn", "model_api.main:app", "--host", "0.0.0.0", "--port", "8080", "--workers", "1", "--no-access-log"]

FROM runtime AS drift-monitor
CMD ["uvicorn", "drift_monitor.main:app", "--host", "0.0.0.0", "--port", "8080", "--workers", "1", "--no-access-log"]

FROM runtime AS remediator
CMD ["uvicorn", "remediator.main:app", "--host", "0.0.0.0", "--port", "8080", "--workers", "1", "--no-access-log"]

FROM python:3.11.17-slim-trixie@sha256:e88e9763f943ec1834f992a4b51e0f24500486803e8bc534e5767af9ea65f6ce AS training

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# Feast constrains pandas to 2.x; keep its fully locked environment isolated.
COPY core/requirements/training.txt /tmp/training.txt
RUN pip install -r /tmp/training.txt

RUN groupadd --system --gid 10001 mlplatform \
    && useradd --system --uid 10001 --gid mlplatform --home-dir /app mlplatform

WORKDIR /app
COPY --chown=10001:10001 core/ml/ ml/
COPY --chown=10001:10001 core/mlp_common/ mlp_common/
COPY --chown=10001:10001 core/feature_repo/ feature_repo/
COPY --chown=10001:10001 scripts/gate.py scripts/gate.py
COPY --chown=10001:10001 pyproject.toml README.md ./

USER 10001:10001

FROM training AS retrain
# No USER switching for the payload: /app/bin is owned by the image user
# (10001) and keeps everything inside the workdir (hadolint DL3066/DL3045
# stay clean). One root step installs curl for smoke.sh/release REST calls.
WORKDIR /app
ENV PATH=/app/bin:$PATH
# hadolint ignore=DL3066
USER root
# hadolint ignore=DL3008,DL3009
RUN apt-get update && apt-get install -y --no-install-recommends curl jq \
    && rm -rf /var/lib/apt/lists/* \
    && mkdir -p /app/bin && chown 10001:10001 /app/bin
USER 10001:10001

ARG KUBECTL_VERSION=v1.37.1
ARG HELM_VERSION=v3.22.0
ARG KUBECTL_SHA256=65691ff77eb6fa44c908b77a1082c9f092c3b9733b5cefabec0d1104890e21a8
ARG HELM_SHA256=1e4ab49e429626cf6c6958d914248b78c9730803c2751b87627e171dc800e7bb
ENV KUBECTL_VERSION=${KUBECTL_VERSION} HELM_VERSION=${HELM_VERSION}

SHELL ["/bin/bash", "-o", "pipefail", "-c"]
RUN curl -fsSL --retry 3 --retry-all-errors --connect-timeout 20 --max-time 180 -o /app/bin/kubectl "https://dl.k8s.io/release/${KUBECTL_VERSION}/bin/linux/amd64/kubectl" \
    && echo "${KUBECTL_SHA256}  /app/bin/kubectl" | sha256sum -c - \
    && chmod +x /app/bin/kubectl \
    && curl -fsSL --retry 3 --retry-all-errors --connect-timeout 20 --max-time 180 -o /tmp/helm.tar.gz "https://get.helm.sh/helm-${HELM_VERSION}-linux-amd64.tar.gz" \
    && echo "${HELM_SHA256}  /tmp/helm.tar.gz" | sha256sum -c - \
    && tar -xzf /tmp/helm.tar.gz -C /tmp \
    && mv /tmp/linux-amd64/helm /app/bin/helm \
    && chmod +x /app/bin/helm \
    && rm -rf /tmp/linux-amd64

COPY --chown=10001:10001 core/retrain/ retrain/
COPY --chown=10001:10001 scripts/model_release_local.sh scripts/deploy.sh scripts/smoke.sh scripts/loadcheck.py scripts/run_gate.sh scripts/run_job.sh scripts/
COPY --chown=10001:10001 infra/charts/model-api infra/charts/model-api
COPY --chown=10001:10001 infra/jobs/gate-job.yaml infra/jobs/gate-job.yaml


USER 10001:10001

FROM training AS mlflow
EXPOSE 5000

FROM retrain AS dashboard
COPY --chown=10001:10001 ui/ ui/
COPY --chown=10001:10001 scripts/ scripts/
COPY --chown=10001:10001 infra/jobs/ infra/jobs/
COPY --chown=10001:10001 Makefile ./
EXPOSE 8050
CMD ["uvicorn", "ui.app:app", "--host", "0.0.0.0", "--port", "8050", "--ssl-keyfile", "/app/tls/tls.key", "--ssl-certfile", "/app/tls/tls.crt"]

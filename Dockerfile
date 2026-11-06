# syntax=docker/dockerfile:1
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Joaquín Garzón
#
# Pin the base image by digest for reproducible builds, e.g.
#   docker build --build-arg PYTHON_IMAGE=python:3.12-slim-bookworm@sha256:<digest> .
ARG PYTHON_IMAGE=python:3.12-slim-bookworm

# ---------------------------------------------------------------- builder
FROM ${PYTHON_IMAGE} AS builder
ENV PIP_DISABLE_PIP_VERSION_CHECK=1 PIP_NO_CACHE_DIR=1
WORKDIR /build
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"
COPY requirements.lock ./
RUN pip install -r requirements.lock
COPY pyproject.toml README.md LICENSE.md NOTICE ./
COPY src ./src
RUN pip install --no-deps .

# ---------------------------------------------------------------- test (docker build --target test .)
FROM builder AS test
COPY requirements-dev.lock ./
RUN pip install -r requirements-dev.lock
COPY tests ./tests
COPY docker ./docker
#Added to fix error executing tests (Copilot)
COPY docker-compose.yml Dockerfile .env.example .dockerignore .gitignore ./
RUN python -m pytest

# ---------------------------------------------------------------- runtime
FROM ${PYTHON_IMAGE} AS runtime
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    FASTMCP_CHECK_FOR_UPDATES=off \
    FASTMCP_SHOW_SERVER_BANNER=false \
    PATH="/opt/venv/bin:$PATH"
RUN apt-get update \
    && apt-get install -y --no-install-recommends openssl \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --system --gid 10001 app \
    && useradd --system --uid 10001 --gid app --no-create-home --shell /usr/sbin/nologin app \
    && mkdir /certs \
    && chown app:app /certs \
    && chmod 700 /certs
COPY --from=builder /opt/venv /opt/venv
COPY --chmod=0755 docker/entrypoint.sh /usr/local/bin/entrypoint.sh
WORKDIR /app
USER app
EXPOSE 8443
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD ["python", "-m", "otcs_mcp.healthcheck"]
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
CMD ["python", "-m", "otcs_mcp"]

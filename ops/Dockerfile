FROM python:3.12.8-slim-bookworm

ARG KNOWLEDGEOS_UID=1000
ARG KNOWLEDGEOS_GID=1000
ARG UV_VERSION=0.8.14

LABEL org.opencontainers.image.title="KnowledgeOS development runtime" \
      org.knowledgeos.python-version="3.12.8" \
      org.knowledgeos.uv-version="0.8.14"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    HOME=/home/vaultops \
    PATH=/opt/venv/bin:/usr/local/bin:/usr/bin:/bin:/usr/local/sbin:/usr/sbin:/sbin

RUN apt-get update \
    && apt-get install --no-install-recommends -y ca-certificates git passwd \
    && python -m pip install --no-cache-dir "uv==${UV_VERSION}" \
    && rm -rf /var/lib/apt/lists/*

RUN if ! getent group "${KNOWLEDGEOS_GID}" >/dev/null; then \
        groupadd --gid "${KNOWLEDGEOS_GID}" vaultops; \
    fi \
    && mkdir -p /home/vaultops \
    && chown "${KNOWLEDGEOS_UID}:${KNOWLEDGEOS_GID}" /home/vaultops

WORKDIR /opt/knowledgeos/ops
COPY ops/pyproject.toml ops/uv.lock ops/vaultops.toml ./
COPY ops/src ./src

RUN uv sync --locked

WORKDIR /workspace/control/ops
USER ${KNOWLEDGEOS_UID}:${KNOWLEDGEOS_GID}

CMD ["vaultctl", "--help"]

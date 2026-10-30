FROM python:3.12.8-slim-bookworm@sha256:2199a62885a12290dc9c5be3ca0681d367576ab7bf037da120e564723292a2f0 AS knowledgeos-dev

ARG KNOWLEDGEOS_UID
ARG KNOWLEDGEOS_GID
ARG UV_VERSION=0.8.14
ARG KNOWLEDGEOS_RUNTIME_EXTRA=

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

RUN case "${KNOWLEDGEOS_UID}" in ''|*[!0-9]*) echo 'KNOWLEDGEOS_UID must be numeric' >&2; exit 1;; esac \
    && case "${KNOWLEDGEOS_GID}" in ''|*[!0-9]*) echo 'KNOWLEDGEOS_GID must be numeric' >&2; exit 1;; esac \
    && [ "${KNOWLEDGEOS_UID}" -gt 0 ] \
    && if ! getent group "${KNOWLEDGEOS_GID}" >/dev/null; then \
        groupadd --gid "${KNOWLEDGEOS_GID}" vaultops; \
    fi \
    && mkdir -p /home/vaultops \
    && chown "${KNOWLEDGEOS_UID}:${KNOWLEDGEOS_GID}" /home/vaultops

WORKDIR /opt/knowledgeos/ops
COPY ops/pyproject.toml ops/uv.lock ops/vaultops.toml ./
# Import-time Core pins are immutable package inputs, not private root bindings.
COPY ops/config/core-adoption.json ./config/core-adoption.json
# MCP loads its exact capability manifest and schemas from the installed package tree.
COPY ops/schemas/mcp ./schemas/mcp
COPY ops/src ./src

RUN case "${KNOWLEDGEOS_RUNTIME_EXTRA}" in \
      '') uv sync --locked ;; \
      temporal) uv sync --locked --extra temporal ;; \
      *) echo 'Unsupported KNOWLEDGEOS_RUNTIME_EXTRA' >&2; exit 1 ;; \
    esac

WORKDIR /workspace/control/ops
USER ${KNOWLEDGEOS_UID}:${KNOWLEDGEOS_GID}

CMD ["vaultctl", "--help"]

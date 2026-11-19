# KnowledgeOS development image: Python 3.12.8 and uv 0.8.14 (mise.toml) with the
# locked dependencies, Node.js and a cached Temporal dev server. Source is bind-mounted.
FROM ghcr.io/astral-sh/uv:0.8.14 AS uv

FROM python:3.12.8-slim-bookworm
COPY --from=uv /uv /uvx /usr/local/bin/
RUN apt-get update \
    && apt-get install -y --no-install-recommends nodejs \
    && rm -rf /var/lib/apt/lists/*
# Published v1.5.1 archive digests; tests never download a server at runtime.
RUN set -eu; arch="$(dpkg --print-architecture)"; \
    case "$arch" in \
      arm64) checksum=bd1b0db9f18b051026de8bf6cc1505f2510f14bbb7a8b9a4a91fff46c77454f5 ;; \
      amd64) checksum=ddc95e08b0b076efd4ea9733a3f488eb7d2be875f8834e616cd2a37358b4852d ;; \
      *) echo "Unsupported Temporal dev-server architecture: $arch" >&2; exit 1 ;; \
    esac; \
    python -c 'import sys, urllib.request; urllib.request.urlretrieve("https://github.com/temporalio/cli/releases/download/v1.5.1/temporal_cli_1.5.1_linux_" + sys.argv[1] + ".tar.gz", "/tmp/temporal.tar.gz")' "$arch"; \
    printf '%s  /tmp/temporal.tar.gz\n' "$checksum" | sha256sum --check --strict; \
    tar -xzf /tmp/temporal.tar.gz -C /usr/local/bin temporal; \
    rm /tmp/temporal.tar.gz
ENV UV_PROJECT_ENVIRONMENT=/opt/venv \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PATH=/opt/venv/bin:$PATH \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HOME=/tmp
WORKDIR /opt/knowledgeos
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-install-project --group dev
WORKDIR /workspace/control

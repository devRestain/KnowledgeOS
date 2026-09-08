.PHONY: verify source-check container-source-check container-verify image-build test lint vaultctl blueprint-check

COMPOSE = docker compose -f ops/compose.yaml
KNOWLEDGEOS_UID ?= $(shell id -u)
KNOWLEDGEOS_GID ?= $(shell id -g)
export KNOWLEDGEOS_UID KNOWLEDGEOS_GID

verify:
	sh ops/check-foundation.sh

source-check:
	shasum -a 256 -c blueprint/CHECKSUMS.sha256

image-build:
	$(COMPOSE) build dev

container-source-check:
	$(COMPOSE) run --rm dev vaultctl foundation source-check --root /workspace/control

container-verify:
	$(COMPOSE) run --rm dev vaultctl foundation check --root /workspace/control

test:
	$(COMPOSE) run --rm dev uv run --frozen --no-sync pytest

lint:
	$(COMPOSE) run --rm dev uv run --frozen --no-sync ruff check src tests

vaultctl:
	$(COMPOSE) run --rm dev vaultctl --help

blueprint-check:
	$(COMPOSE) run --rm dev vaultctl blueprint validate --root /workspace/control

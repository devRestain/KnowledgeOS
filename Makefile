.PHONY: verify source-check container-source-check container-verify image-build test lint vaultctl blueprint-check schema-export schema-check vault-artifact-check profile-check contract-check

COMPOSE = docker compose -f ops/compose.yaml
TEST_COMPOSE = docker compose -f ops/compose.yaml -f ops/compose.test.yaml
# Bind mounts and the disposable cache must be owned by the invoking host user.
# Do not replace these with a portable-looking 1000:1000 fallback.
KNOWLEDGEOS_UID ?= $(shell id -u)
KNOWLEDGEOS_GID ?= $(shell id -g)
PYTEST_ARGS ?=
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
	$(TEST_COMPOSE) run --rm -e KNOWLEDGEOS_TEST_ENTRYPOINT=make dev uv run --frozen --no-sync pytest $(PYTEST_ARGS)

lint:
	$(TEST_COMPOSE) run --rm dev uv run --frozen --no-sync ruff check src tests ../scripts

vaultctl:
	$(COMPOSE) run --rm dev vaultctl --help

blueprint-check:
	$(COMPOSE) run --rm dev vaultctl blueprint validate --root /workspace/control

schema-export:
	$(COMPOSE) run --rm dev vaultctl schema export --root /workspace/control

schema-check:
	$(COMPOSE) run --rm dev vaultctl schema export --check --root /workspace/control

vault-artifact-check:
	$(COMPOSE) run --rm dev vaultctl vault-artifacts check --root /workspace/control

profile-check:
	$(COMPOSE) run --rm dev vaultctl plugins audit --profile mac --root /workspace/control

contract-check: blueprint-check schema-check

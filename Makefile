.PHONY: verify source-check container-source-check container-verify image-build test test-invariance lint vaultctl vaultmcp blueprint-check schema-export schema-check vault-artifact-check vault-readiness-check profile-check live-smoke contract-check acceptance scheduled-worker state-check core-readiness-check

COMPOSE = docker compose -f ops/compose.yaml
TEST_COMPOSE = docker compose -f ops/compose.test.yaml
VAULT_CHECK_COMPOSE = docker compose -f ops/compose.vault-check.yaml
# Bind mounts and the disposable cache must be owned by the invoking host user.
# Do not replace these with a portable-looking 1000:1000 fallback.
KNOWLEDGEOS_UID ?= $(shell id -u)
KNOWLEDGEOS_GID ?= $(shell id -g)
KNOWLEDGEOS_CONTROL_SOURCE ?= $(abspath $(dir $(lastword $(MAKEFILE_LIST))))
KNOWLEDGEOS_CONTROL_SOURCE := $(abspath $(KNOWLEDGEOS_CONTROL_SOURCE))
KNOWLEDGEOS_CORE_SOURCE ?= $(abspath $(KNOWLEDGEOS_CONTROL_SOURCE)/../../Core)
PYTEST_ARGS ?=
export KNOWLEDGEOS_UID KNOWLEDGEOS_GID KNOWLEDGEOS_SCHEDULED_ATTEMPT_ID KNOWLEDGEOS_SCHEDULED_CIDFILE
export KNOWLEDGEOS_CONTROL_SOURCE KNOWLEDGEOS_VAULT_SOURCE KNOWLEDGEOS_RUNTIME_SOURCE KNOWLEDGEOS_STATE_SOURCE
export KNOWLEDGEOS_CORE_SOURCE

verify:
	$(TEST_COMPOSE) run --rm dev sh check-foundation.sh

source-check:
	$(TEST_COMPOSE) run --rm dev python -c 'from pathlib import Path; from vaultops.foundation import check_source_manifest; p=check_source_manifest(Path("/workspace/control")); print(p or "Blueprint source digest: PASS"); raise SystemExit(bool(p))'

image-build:
	$(COMPOSE) build dev

container-source-check:
	$(TEST_COMPOSE) run --rm dev vaultctl foundation source-check --root /workspace/control

container-verify:
	$(TEST_COMPOSE) run --rm dev vaultctl foundation check --root /workspace/control

test:
	$(TEST_COMPOSE) run --rm -e KNOWLEDGEOS_TEST_ENTRYPOINT=make dev uv run --frozen --no-sync pytest $(PYTEST_ARGS)

test-invariance:
	$(MAKE) test PYTEST_ARGS='tests/test_hermetic_invariance.py -q'

lint:
	$(TEST_COMPOSE) run --rm dev uv run --frozen --no-sync ruff check src tests ../scripts

vaultctl:
	$(COMPOSE) run --rm dev vaultctl --help

vaultmcp:
	$(COMPOSE) run --rm -T dev vaultmcp --control-root /workspace/control

blueprint-check:
	$(TEST_COMPOSE) run --rm dev vaultctl blueprint validate --root /workspace/control

schema-export:
	$(COMPOSE) run --rm dev vaultctl schema export --root /workspace/control

schema-check:
	$(TEST_COMPOSE) run --rm dev vaultctl schema export --check --root /workspace/control

vault-artifact-check:
	$(VAULT_CHECK_COMPOSE) run --rm dev vaultctl vault-artifacts check --root /workspace/control

vault-readiness-check:
	$(VAULT_CHECK_COMPOSE) run --rm dev vaultctl vault-artifacts check --scope readiness --root /workspace/control

profile-check:
	$(VAULT_CHECK_COMPOSE) run --rm dev vaultctl plugins audit --profile mac --root /workspace/control

live-smoke:
	@test -n "$(SMOKE_KIND)"
	@test -n "$(SMOKE_ADAPTER)"
	@test -n "$(SMOKE_TIMEOUT_SECONDS)"
	$(COMPOSE) run --rm dev vaultctl smoke run --kind "$(SMOKE_KIND)" --adapter "$(SMOKE_ADAPTER)" --authorize-live-smoke --timeout-seconds "$(SMOKE_TIMEOUT_SECONDS)" --root /workspace/control

contract-check: blueprint-check schema-check

scheduled-worker:
	@test -n "$(KNOWLEDGEOS_SCHEDULED_CIDFILE)"
	@test -n "$(KNOWLEDGEOS_SCHEDULED_ATTEMPT_ID)"
	@$(COMPOSE) run --rm -T --no-deps --cidfile "$(KNOWLEDGEOS_SCHEDULED_CIDFILE)" dev vaultctl ai worker --once --scheduled-report

acceptance:
	$(MAKE) source-check
	$(MAKE) verify
	$(MAKE) blueprint-check
	$(MAKE) schema-check
	$(MAKE) container-source-check
	$(MAKE) container-verify
	$(MAKE) core-readiness-check
	$(MAKE) test-invariance
	$(MAKE) test
	$(MAKE) lint
	$(MAKE) state-check
	git diff --check
	@test -n "$(KNOWLEDGEOS_VAULT_SOURCE)"
	git -C "$(KNOWLEDGEOS_VAULT_SOURCE)" diff --check

core-readiness-check:
	$(MAKE) test PYTEST_ARGS='tests/test_core_readiness.py tests/test_paths.py tests/test_portability.py tests/test_mcp_schemas.py tests/test_mcp_protocol.py -q'

state-check:
	$(TEST_COMPOSE) run --rm dev python /workspace/control/scripts/validate_state.py /workspace/control/PROJECT_STATE.md

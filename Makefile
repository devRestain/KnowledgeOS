.PHONY: verify source-check container-source-check container-verify image-build test test-invariance lint vaultctl blueprint-check schema-export schema-check vault-artifact-check profile-check live-smoke contract-check acceptance

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

test-invariance:
	$(MAKE) test PYTEST_ARGS='tests/test_hermetic_invariance.py -q'

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

live-smoke:
	@test -n "$(SMOKE_KIND)"
	@test -n "$(SMOKE_ADAPTER)"
	@test -n "$(SMOKE_TIMEOUT_SECONDS)"
	$(COMPOSE) run --rm dev vaultctl smoke run --kind "$(SMOKE_KIND)" --adapter "$(SMOKE_ADAPTER)" --authorize-live-smoke --timeout-seconds "$(SMOKE_TIMEOUT_SECONDS)" --root /workspace/control

contract-check: blueprint-check schema-check

acceptance:
	$(MAKE) source-check
	$(MAKE) verify
	$(MAKE) blueprint-check
	$(MAKE) schema-check
	$(MAKE) container-source-check
	$(MAKE) container-verify
	$(MAKE) test PYTEST_ARGS='tests/test_test_runner_contract.py -q'
	$(MAKE) test PYTEST_ARGS='tests/test_foundation_contract.py tests/test_vault_artifact_check.py -q'
	$(MAKE) test PYTEST_ARGS='tests/test_plugin_audit.py tests/test_plugin_invariants.py -q'
	$(MAKE) test PYTEST_ARGS='tests/test_c08_dashboard.py tests/test_c12_diagnostics.py tests/test_c28_proposal_recovery.py tests/test_d01_configure.py tests/test_note_engine.py -q'
	$(MAKE) test PYTEST_ARGS='tests/test_smoke_lifecycle.py -q'
	$(MAKE) test-invariance
	$(MAKE) test
	$(MAKE) lint
	/usr/bin/python3 scripts/validate_state.py PROJECT_STATE.md
	git diff --check
	git -C KnowledgeHub diff --check

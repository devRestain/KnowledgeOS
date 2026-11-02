.PHONY: verify source-check container-source-check container-verify image-build image-smoke image-id officer-image-build officer-smoke model-officer-smoke team-image-build team-smoke team-recovery-smoke team-continuation-smoke team-control-smoke team-rollover-smoke owner-read-only-up owner-read-only-down test test-invariance lint vaultctl vaultmcp blueprint-check schema-export schema-check vault-artifact-check vault-readiness-check profile-check contract-check acceptance state-check core-readiness-check
.PHONY: research-team-smoke domain-restore-smoke steering-smoke attention-smoke native-lifecycle-smoke
.PHONY: native-metadata-smoke native-authoring-smoke native-boundary-smoke daily-scheduler-smoke owner-runtime-smoke owner-writable-run owner-writable-down
.PHONY: thin-client-check

COMPOSE = docker compose -f ops/compose.yaml
TEST_COMPOSE = docker compose -f ops/compose.test.yaml
VAULT_CHECK_COMPOSE = docker compose -f ops/compose.vault-check.yaml
OWNER_READ_ONLY_COMPOSE = docker compose -f ops/compose.owner-read-only.yaml
OWNER_WRITABLE_COMPOSE = docker compose -f ops/compose.owner-writable.yaml
# Bind mounts and the disposable cache must be owned by the invoking host user.
# Do not replace these with a portable-looking 1000:1000 fallback.
KNOWLEDGEOS_UID ?= $(shell id -u)
KNOWLEDGEOS_GID ?= $(shell id -g)
KNOWLEDGEOS_CONTROL_SOURCE ?= $(abspath $(dir $(lastword $(MAKEFILE_LIST))))
KNOWLEDGEOS_CONTROL_SOURCE := $(abspath $(KNOWLEDGEOS_CONTROL_SOURCE))
KNOWLEDGEOS_CORE_SOURCE ?= $(abspath $(KNOWLEDGEOS_CONTROL_SOURCE)/../../Core)
KNOWLEDGEOS_DEV_IMAGE ?= agentfabric-knowledgeos-dev:py3.12.8-uv0.8.14
KNOWLEDGEOS_OFFICER_IMAGE ?= agentfabric-knowledgeos-officer:py3.12.8-temporal1.34.0
KNOWLEDGEOS_TEAM_IMAGE ?= agentfabric-knowledgeos-team:py3.12.8-temporal1.34.0-langgraph1.2.0
KNOWLEDGEOS_TEMPORAL_ADAPTER_SOURCE ?= $(abspath $(KNOWLEDGEOS_CONTROL_SOURCE)/../../Services/clients/temporal.py)
KNOWLEDGEOS_TEMPORAL_SCHEMA_SOURCE ?= $(abspath $(KNOWLEDGEOS_CONTROL_SOURCE)/../../Services/temporal/setup-postgres.sh)
KNOWLEDGEOS_TEMPORAL_DYNAMIC_CONFIG_SOURCE ?= $(abspath $(KNOWLEDGEOS_CONTROL_SOURCE)/../../Services/temporal/dynamicconfig.yaml)
KNOWLEDGEOS_OFFICER_EVIDENCE ?=
KNOWLEDGEOS_MODEL_EVIDENCE ?=
KNOWLEDGEOS_TEAM_EVIDENCE ?=
KNOWLEDGEOS_TEAM_RECOVERY_EVIDENCE ?=
KNOWLEDGEOS_TEAM_CONTINUATION_EVIDENCE ?=
KNOWLEDGEOS_TEAM_CONTROL_EVIDENCE ?=
KNOWLEDGEOS_TEAM_CONTROL_CASE ?= both
KNOWLEDGEOS_TEAM_ROLLOVER_EVIDENCE ?=
KNOWLEDGEOS_RESEARCH_EVIDENCE ?=
KNOWLEDGEOS_METADATA_EVIDENCE ?=
KNOWLEDGEOS_AUTHORING_EVIDENCE ?=
KNOWLEDGEOS_BOUNDARY_EVIDENCE ?=
KNOWLEDGEOS_DAILY_SCHEDULER_EVIDENCE ?=
KNOWLEDGEOS_OWNER_RUNTIME_EVIDENCE ?=
KNOWLEDGEOS_OWNER_UI_HOLD_SECONDS ?= 0
DOMAIN_RESTORE_EVIDENCE ?=
KNOWLEDGEOS_STEERING_EVIDENCE ?=
KNOWLEDGEOS_ATTENTION_EVIDENCE ?=
NATIVE_LIFECYCLE_EVIDENCE ?=
KNOWLEDGEOS_HERMES_SOURCE ?= $(abspath $(KNOWLEDGEOS_CONTROL_SOURCE)/../../Runtimes/Hermes-runtime/hermes-agent)
KNOWLEDGEOS_HERMES_PROFILE_HOME ?= $(abspath $(KNOWLEDGEOS_CONTROL_SOURCE)/../../States/System/Hermes/profiles/kos-cited-answer)
KNOWLEDGEOS_HERMES_PYTHON ?= $(abspath $(KNOWLEDGEOS_CONTROL_SOURCE)/../../States/System/Hermes/installs/e6de586b2141ea09/environments/159730d9aced44a1a4c1fdf6a61c4192/venv/bin/python)
PYTEST_ARGS ?=
export KNOWLEDGEOS_UID KNOWLEDGEOS_GID
export KNOWLEDGEOS_CONTROL_SOURCE KNOWLEDGEOS_VAULT_SOURCE KNOWLEDGEOS_RUNTIME_SOURCE KNOWLEDGEOS_STATE_SOURCE
export KNOWLEDGEOS_CORE_SOURCE KNOWLEDGEOS_DEV_IMAGE
export KNOWLEDGEOS_OWNER_POLICY_SOURCE KNOWLEDGEOS_OWNER_ACTOR_ID KNOWLEDGEOS_OWNER_PORT
export KNOWLEDGEOS_OWNER_CONTAINER KNOWLEDGEOS_TEMPORAL_NETWORK
export KNOWLEDGEOS_OFFICER_IMAGE KNOWLEDGEOS_TEMPORAL_ADAPTER_SOURCE KNOWLEDGEOS_TEMPORAL_SCHEMA_SOURCE
export KNOWLEDGEOS_TEMPORAL_DYNAMIC_CONFIG_SOURCE
export KNOWLEDGEOS_TEAM_IMAGE
export KNOWLEDGEOS_HERMES_SOURCE KNOWLEDGEOS_HERMES_PROFILE_HOME KNOWLEDGEOS_HERMES_PYTHON

thin-client-check:
	node "$(KNOWLEDGEOS_CONTROL_SOURCE)/ops/tests/support/thin_client_check.js" $(if $(KNOWLEDGEOS_THIN_CLIENT_ENDPOINT),--endpoint "$(KNOWLEDGEOS_THIN_CLIENT_ENDPOINT)")

verify:
	$(TEST_COMPOSE) run --rm dev sh check-foundation.sh

source-check:
	$(TEST_COMPOSE) run --rm dev python -c 'from pathlib import Path; from vaultops.foundation import check_source_manifest; p=check_source_manifest(Path("/workspace/control")); print(p or "Blueprint source digest: PASS"); raise SystemExit(bool(p))'

image-build:
	docker build --pull --build-arg KNOWLEDGEOS_UID=$(KNOWLEDGEOS_UID) --build-arg KNOWLEDGEOS_GID=$(KNOWLEDGEOS_GID) -f "$(KNOWLEDGEOS_CONTROL_SOURCE)/ops/Dockerfile" -t "$(KNOWLEDGEOS_DEV_IMAGE)" "$(KNOWLEDGEOS_CONTROL_SOURCE)"
	$(MAKE) image-smoke

image-smoke:
	docker run --rm --pull=never --network none --read-only --cap-drop ALL --security-opt no-new-privileges --workdir /opt/knowledgeos/ops "$(KNOWLEDGEOS_DEV_IMAGE)" vaultctl --help
	docker run --rm --pull=never --network none --read-only --cap-drop ALL --security-opt no-new-privileges --workdir /opt/knowledgeos/ops "$(KNOWLEDGEOS_DEV_IMAGE)" python -I -m vaultops.interfaces.read_only_owner --help
	$(TEST_COMPOSE) run --rm -e PYTHONPATH=/workspace/control/ops/tests dev python /workspace/control/ops/tests/support/installed_mcp_smoke.py
	$(TEST_COMPOSE) run --rm -e PYTHONPATH=/workspace/control/ops/tests dev python /workspace/control/ops/tests/support/installed_web_console_smoke.py

image-id:
	@docker image inspect --format '{{.Id}}' "$(KNOWLEDGEOS_DEV_IMAGE)"

officer-image-build:
	docker build --pull --build-arg KNOWLEDGEOS_UID=$(KNOWLEDGEOS_UID) --build-arg KNOWLEDGEOS_GID=$(KNOWLEDGEOS_GID) --build-arg KNOWLEDGEOS_RUNTIME_EXTRA=temporal -f "$(KNOWLEDGEOS_CONTROL_SOURCE)/ops/Dockerfile" -t "$(KNOWLEDGEOS_OFFICER_IMAGE)" "$(KNOWLEDGEOS_CONTROL_SOURCE)"

officer-smoke:
	python3 "$(KNOWLEDGEOS_CONTROL_SOURCE)/ops/tests/support/normalization_work_smoke.py" host $(if $(KNOWLEDGEOS_OFFICER_EVIDENCE),--evidence "$(KNOWLEDGEOS_OFFICER_EVIDENCE)")

model-officer-smoke:
	python3 "$(KNOWLEDGEOS_CONTROL_SOURCE)/ops/tests/support/normalization_work_smoke.py" host-model $(if $(KNOWLEDGEOS_MODEL_EVIDENCE),--evidence "$(KNOWLEDGEOS_MODEL_EVIDENCE)")

team-image-build:
	docker build --pull --build-arg KNOWLEDGEOS_UID=$(KNOWLEDGEOS_UID) --build-arg KNOWLEDGEOS_GID=$(KNOWLEDGEOS_GID) --build-arg KNOWLEDGEOS_RUNTIME_EXTRA=team -f "$(KNOWLEDGEOS_CONTROL_SOURCE)/ops/Dockerfile" -t "$(KNOWLEDGEOS_TEAM_IMAGE)" "$(KNOWLEDGEOS_CONTROL_SOURCE)"

team-smoke:
	python3 "$(KNOWLEDGEOS_CONTROL_SOURCE)/ops/tests/support/curation_work_smoke.py" host $(if $(KNOWLEDGEOS_TEAM_EVIDENCE),--evidence "$(KNOWLEDGEOS_TEAM_EVIDENCE)")

team-recovery-smoke:
	python3 "$(KNOWLEDGEOS_CONTROL_SOURCE)/ops/tests/support/curation_recovery_smoke.py" host $(if $(KNOWLEDGEOS_TEAM_RECOVERY_EVIDENCE),--evidence "$(KNOWLEDGEOS_TEAM_RECOVERY_EVIDENCE)")

team-continuation-smoke:
	python3 "$(KNOWLEDGEOS_CONTROL_SOURCE)/ops/tests/support/curation_continuation_smoke.py" host $(if $(KNOWLEDGEOS_TEAM_CONTINUATION_EVIDENCE),--evidence "$(KNOWLEDGEOS_TEAM_CONTINUATION_EVIDENCE)")

team-control-smoke:
	python3 "$(KNOWLEDGEOS_CONTROL_SOURCE)/ops/tests/support/curation_control_smoke.py" host --case "$(KNOWLEDGEOS_TEAM_CONTROL_CASE)" $(if $(KNOWLEDGEOS_TEAM_CONTROL_EVIDENCE),--evidence "$(KNOWLEDGEOS_TEAM_CONTROL_EVIDENCE)")

team-rollover-smoke:
	python3 "$(KNOWLEDGEOS_CONTROL_SOURCE)/ops/tests/support/curation_control_smoke.py" host --case rollover $(if $(KNOWLEDGEOS_TEAM_ROLLOVER_EVIDENCE),--evidence "$(KNOWLEDGEOS_TEAM_ROLLOVER_EVIDENCE)")

research-team-smoke:
	python3 "$(KNOWLEDGEOS_CONTROL_SOURCE)/ops/tests/support/research_work_smoke.py" host $(if $(KNOWLEDGEOS_RESEARCH_EVIDENCE),--evidence "$(KNOWLEDGEOS_RESEARCH_EVIDENCE)")

native-metadata-smoke:
	python3 "$(KNOWLEDGEOS_CONTROL_SOURCE)/ops/tests/support/metadata_work_smoke.py" host $(if $(KNOWLEDGEOS_METADATA_EVIDENCE),--evidence "$(KNOWLEDGEOS_METADATA_EVIDENCE)")

native-authoring-smoke:
	python3 "$(KNOWLEDGEOS_CONTROL_SOURCE)/ops/tests/support/authoring_work_smoke.py" host $(if $(KNOWLEDGEOS_AUTHORING_EVIDENCE),--evidence "$(KNOWLEDGEOS_AUTHORING_EVIDENCE)")

native-boundary-smoke:
	python3 "$(KNOWLEDGEOS_CONTROL_SOURCE)/ops/tests/support/boundary_work_smoke.py" host $(if $(KNOWLEDGEOS_BOUNDARY_EVIDENCE),--evidence "$(KNOWLEDGEOS_BOUNDARY_EVIDENCE)")

daily-scheduler-smoke:
	python3 "$(KNOWLEDGEOS_CONTROL_SOURCE)/ops/tests/support/daily_scheduler_smoke.py" host $(if $(KNOWLEDGEOS_DAILY_SCHEDULER_EVIDENCE),--evidence "$(KNOWLEDGEOS_DAILY_SCHEDULER_EVIDENCE)")

owner-runtime-smoke:
	@test -n "$(KNOWLEDGEOS_OWNER_RUNTIME_EVIDENCE)" || (echo "KNOWLEDGEOS_OWNER_RUNTIME_EVIDENCE must name a new evidence file"; exit 2)
	python3 "$(KNOWLEDGEOS_CONTROL_SOURCE)/ops/tests/support/owner_runtime_smoke.py" host "$(KNOWLEDGEOS_OWNER_RUNTIME_EVIDENCE)" --ui-hold-seconds "$(KNOWLEDGEOS_OWNER_UI_HOLD_SECONDS)"

domain-restore-smoke:
	python3 "$(KNOWLEDGEOS_CONTROL_SOURCE)/ops/tests/support/domain_restore_smoke.py" host $(if $(DOMAIN_RESTORE_EVIDENCE),--evidence "$(DOMAIN_RESTORE_EVIDENCE)")

steering-smoke:
	python3 "$(KNOWLEDGEOS_CONTROL_SOURCE)/ops/tests/support/curation_steering_smoke.py" host $(if $(KNOWLEDGEOS_STEERING_EVIDENCE),--evidence "$(KNOWLEDGEOS_STEERING_EVIDENCE)")

attention-smoke:
	$(TEST_COMPOSE) run --rm --volume "$(abspath $(KNOWLEDGEOS_CONTROL_SOURCE)/../../Services/clients):/host-services/clients:ro" $(if $(KNOWLEDGEOS_ATTENTION_EVIDENCE),--volume "$(abspath $(KNOWLEDGEOS_ATTENTION_EVIDENCE)):/evidence:rw") -e PYTHONPATH=/workspace/control/ops/src:/workspace/control/ops/tests:/host-services dev python /workspace/control/ops/tests/support/owner_attention_smoke.py $(if $(KNOWLEDGEOS_ATTENTION_EVIDENCE),--evidence /evidence/completion.json)

native-lifecycle-smoke:
	python3 "$(KNOWLEDGEOS_CONTROL_SOURCE)/ops/tests/support/native_lifecycle_smoke_host.py" $(if $(NATIVE_LIFECYCLE_EVIDENCE),--evidence "$(NATIVE_LIFECYCLE_EVIDENCE)")

owner-read-only-up: image-id
	$(OWNER_READ_ONLY_COMPOSE) up -d --no-build

owner-read-only-down:
	$(OWNER_READ_ONLY_COMPOSE) down

owner-writable-run:
	@test -n "$(COMPOSE_PROJECT_NAME)" && test -n "$(KNOWLEDGEOS_OWNER_CONTAINER)" && test -n "$(KNOWLEDGEOS_OWNER_POLICY_SOURCE)" || (echo "COMPOSE_PROJECT_NAME, KNOWLEDGEOS_OWNER_CONTAINER and KNOWLEDGEOS_OWNER_POLICY_SOURCE must be explicit"; exit 2)
	python3 "$(KNOWLEDGEOS_CONTROL_SOURCE)/ops/src/vaultops/interfaces/native_owner_driver.py" --policy-file "$(KNOWLEDGEOS_OWNER_POLICY_SOURCE)/policy.json" --compose-file "$(KNOWLEDGEOS_CONTROL_SOURCE)/ops/compose.owner-writable.yaml" --project "$(COMPOSE_PROJECT_NAME)" --container-name "$(KNOWLEDGEOS_OWNER_CONTAINER)" --adapters-directory "$(KNOWLEDGEOS_CONTROL_SOURCE)/ops/clients/hermes" --hermes-python "$(KNOWLEDGEOS_HERMES_PYTHON)" --hermes-root "$(KNOWLEDGEOS_HERMES_SOURCE)" --profile-home "$(KNOWLEDGEOS_HERMES_PROFILE_HOME)"

owner-writable-down:
	@test -n "$(COMPOSE_PROJECT_NAME)" || (echo "COMPOSE_PROJECT_NAME must select the exact owner project"; exit 2)
	$(OWNER_WRITABLE_COMPOSE) --project-name "$(COMPOSE_PROJECT_NAME)" down

container-source-check:
	$(TEST_COMPOSE) run --rm dev vaultctl foundation source --root /workspace/control

container-verify:
	$(TEST_COMPOSE) run --rm dev vaultctl foundation check --root /workspace/control

test:
	$(TEST_COMPOSE) run --rm -e KNOWLEDGEOS_TEST_ENTRYPOINT=make dev uv run --frozen --no-sync pytest $(PYTEST_ARGS)

test-invariance:
	$(MAKE) test PYTEST_ARGS='tests/test_hermetic_invariance.py -q'

lint:
	$(TEST_COMPOSE) run --rm dev uv run --frozen --no-sync ruff check src tests ../scripts temporal_pilot.py clients/hermes host

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
	$(VAULT_CHECK_COMPOSE) run --rm dev python -c 'from vaultops.vault_readiness import check_vault_readiness; result=check_vault_readiness("/workspace/control"); print(result.as_json()); raise SystemExit(result.exit_code)'

profile-check:
	$(VAULT_CHECK_COMPOSE) run --rm dev vaultctl plugins audit --profile mac --root /workspace/control

contract-check: blueprint-check schema-check

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

.PHONY: image-build image-id lock test state-check trace-check wiki wiki-check

KNOWLEDGEOS_DEV_IMAGE ?= agentfabric-knowledgeos-dev:py3.12.8-uv0.8.14
AGENTFABRIC_ROOT := $(abspath $(CURDIR)/../..)
CORE_ROOT ?= $(AGENTFABRIC_ROOT)/Core
SITES_ROOT ?= $(AGENTFABRIC_ROOT)/Sites
OPERATIONKIT_ROOT ?= $(AGENTFABRIC_ROOT)/OperationKit
# Space-separated pytest node IDs or paths; empty runs the whole suite.
TESTS ?=

RUN_TEST_CONTAINER = docker run --rm --network none --read-only --cap-drop ALL \
	--security-opt no-new-privileges --user "$$(id -u):$$(id -g)" \
	--tmpfs /tmp:rw,exec,nosuid,nodev,mode=1777 \
	--env USER="$$(id -un)" \
	--env PYTHONPATH=/workspace/Operations/KnowledgeOS/src:/workspace/OperationKit/src:/workspace/hermestrace-src --env KNOWLEDGEOS_CORE_ROOT=/workspace/Core \
	--env KNOWLEDGEOS_VAULT_SYSTEM=/workspace/vault-system \
	--mount "type=bind,src=$(CURDIR),dst=/workspace/Operations/KnowledgeOS,readonly" \
	--mount "type=bind,src=$(OPERATIONKIT_ROOT),dst=/workspace/OperationKit,readonly" \
	--mount "type=bind,src=$(CORE_ROOT),dst=/workspace/Core,readonly" \
	--mount "type=bind,src=$(AGENTFABRIC_ROOT)/Operations/HermesTrace/src,dst=/workspace/hermestrace-src,readonly" \
	--mount "type=bind,src=$(AGENTFABRIC_ROOT)/Vaults/KnowledgeHub/99_System,dst=/workspace/vault-system,readonly" \
	--workdir /workspace/Operations/KnowledgeOS "$(KNOWLEDGEOS_DEV_IMAGE)"

# Tmp/ReferenceCapsule/run.sh consumes image-id; the image also carries jsonschema and ruff for it.
image-build:
	@set -eu; \
	context=$$(mktemp -d); archive=$$(mktemp); \
	trap 'rm -rf "$$context"; rm -f "$$archive"' EXIT; \
	mkdir -p "$$context/KnowledgeOS" "$$context/OperationKit/src/operationkit/wiki_assets"; \
	cp "$(CURDIR)/Dockerfile" "$$context/Dockerfile"; \
	cp "$(CURDIR)/pyproject.toml" "$(CURDIR)/uv.lock" "$$context/KnowledgeOS/"; \
	cp "$(OPERATIONKIT_ROOT)/pyproject.toml" "$$context/OperationKit/"; \
	cp "$(OPERATIONKIT_ROOT)"/src/operationkit/*.py "$$context/OperationKit/src/operationkit/"; \
	cp "$(OPERATIONKIT_ROOT)"/src/operationkit/wiki_assets/*.css \
		"$(OPERATIONKIT_ROOT)"/src/operationkit/wiki_assets/*.js \
		"$$context/OperationKit/src/operationkit/wiki_assets/"; \
	COPYFILE_DISABLE=1 tar --no-xattrs --format=ustar -C "$$context" -cf "$$archive" .; \
	docker build --tag "$(KNOWLEDGEOS_DEV_IMAGE)" - < "$$archive"

image-id:
	@docker image inspect --format '{{.Id}}' "$(KNOWLEDGEOS_DEV_IMAGE)"

lock:
	docker run --rm --mount "type=bind,src=$(CURDIR),dst=/workspace/Operations/KnowledgeOS" \
		--mount "type=bind,src=$(OPERATIONKIT_ROOT),dst=/workspace/OperationKit,readonly" \
		--workdir /workspace/Operations/KnowledgeOS ghcr.io/astral-sh/uv:0.8.14-python3.12-bookworm-slim uv lock

test:
	$(RUN_TEST_CONTAINER) python -m pytest -n auto $(TESTS)

trace-check:
	$(RUN_TEST_CONTAINER) python scripts/trace_check.py

state-check:
	docker run --rm --network none --read-only --workdir /workspace --mount "type=bind,src=$(CURDIR),dst=/workspace/control,readonly" \
		"$(KNOWLEDGEOS_DEV_IMAGE)" python /workspace/control/scripts/validate_state.py /workspace/control/PROJECT_STATE.md

# Static publication: authoring lives in Sites; only the Sites worktree is writable.
RUN_WIKI_CONTAINER = docker run --rm --network none --read-only --cap-drop ALL \
	--security-opt no-new-privileges --user "$$(id -u):$$(id -g)" \
	--env PYTHONPATH=/workspace/OperationKit/src --env AGENTFABRIC_ROOT=/workspace \
	--mount "type=bind,src=$(CURDIR),dst=/workspace/Operations/KnowledgeOS,readonly" \
	--mount "type=bind,src=$(AGENTFABRIC_ROOT)/Operations/HermesTrace,dst=/workspace/Operations/HermesTrace,readonly" \
	--mount "type=bind,src=$(OPERATIONKIT_ROOT),dst=/workspace/OperationKit,readonly" \
	--mount "type=bind,src=$(SITES_ROOT),dst=/workspace/Sites" \
	--workdir /workspace/Operations/KnowledgeOS "$(KNOWLEDGEOS_DEV_IMAGE)"

wiki:
	$(RUN_WIKI_CONTAINER) python -B scripts/build_wiki.py

wiki-check:
	$(RUN_WIKI_CONTAINER) python -B scripts/wiki_check.py

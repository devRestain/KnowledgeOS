.PHONY: image-build image-id lock test state-check trace-check wiki wiki-check

KNOWLEDGEOS_DEV_IMAGE ?= agentfabric-knowledgeos-dev:py3.12.8-uv0.8.14
AGENTFABRIC_ROOT := $(abspath $(CURDIR)/../..)
# Space-separated pytest node IDs or paths; empty runs the whole suite.
TESTS ?=

RUN_TEST_CONTAINER = docker run --rm --network none --read-only --cap-drop ALL \
	--security-opt no-new-privileges --user "$$(id -u):$$(id -g)" \
	--tmpfs /tmp:rw,exec,nosuid,nodev,mode=1777 \
	--env USER="$$(id -un)" \
	--env PYTHONPATH=/workspace/control/src:/workspace/hermestrace-src --env KNOWLEDGEOS_CORE_ROOT=/workspace/Core \
	--env KNOWLEDGEOS_VAULT_SYSTEM=/workspace/vault-system \
	--mount "type=bind,src=$(CURDIR),dst=/workspace/control,readonly" \
	--mount "type=bind,src=$(AGENTFABRIC_ROOT)/Core,dst=/workspace/Core,readonly" \
	--mount "type=bind,src=$(AGENTFABRIC_ROOT)/Operations/HermesTrace/src,dst=/workspace/hermestrace-src,readonly" \
	--mount "type=bind,src=$(AGENTFABRIC_ROOT)/Vaults/KnowledgeHub/99_System,dst=/workspace/vault-system,readonly" \
	--workdir /workspace/control "$(KNOWLEDGEOS_DEV_IMAGE)"

# Tmp/ReferenceCapsule/run.sh consumes image-id; the image also carries jsonschema and ruff for it.
image-build:
	docker build --tag "$(KNOWLEDGEOS_DEV_IMAGE)" "$(CURDIR)"

image-id:
	@docker image inspect --format '{{.Id}}' "$(KNOWLEDGEOS_DEV_IMAGE)"

lock:
	docker run --rm --mount "type=bind,src=$(CURDIR),dst=/w" --workdir /w ghcr.io/astral-sh/uv:0.8.14-python3.12-bookworm-slim uv lock

test:
	$(RUN_TEST_CONTAINER) python -m pytest -n auto $(TESTS)

trace-check:
	$(RUN_TEST_CONTAINER) python scripts/trace_check.py

state-check:
	docker run --rm --network none --read-only --workdir /workspace --mount "type=bind,src=$(CURDIR),dst=/workspace/control,readonly" \
		"$(KNOWLEDGEOS_DEV_IMAGE)" python /workspace/control/scripts/validate_state.py /workspace/control/PROJECT_STATE.md

# Static wiki: stdlib-only generator; only docs/wiki is writable.
RUN_WIKI_CONTAINER = docker run --rm --network none --read-only --cap-drop ALL \
	--security-opt no-new-privileges --user "$$(id -u):$$(id -g)" \
	--mount "type=bind,src=$(CURDIR),dst=/workspace/control,readonly" \
	--mount "type=bind,src=$(CURDIR)/docs/wiki,dst=/workspace/control/docs/wiki" \
	--workdir /workspace/control "$(KNOWLEDGEOS_DEV_IMAGE)"

wiki:
	$(RUN_WIKI_CONTAINER) python -B scripts/build_wiki.py

wiki-check:
	$(RUN_WIKI_CONTAINER) python -B scripts/wiki_check.py

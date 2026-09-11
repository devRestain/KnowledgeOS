# KnowledgeOS Project Contract

## Project Scope

- Treat `/Users/yuk/DevFolder/CodePractice/WorkingProject/KnowledgeOS` as the control workspace.
- Treat user-initialized control and `KnowledgeHub/` Git roots as independent repositories.
- Keep project Docker work within Compose project `ops` and service `dev`.
- Read `README.md`, `docs/IMPLEMENTATION_STATUS.md`, and `docs/IMPLEMENTATION_PLAN.md` before resuming.

## Project Structure

- Keep control documents, policies, source, and tests in repository root, `docs/`, and `ops/`.
- Keep Vault notes and artifacts under `KnowledgeHub/` and keep `runtime/` outside Git tracking.
- Resolve design conflicts as Blueprint Markdown, then `blueprint/blueprint.yaml`, then Whitepaper.
- Preserve `KnowledgeHub/.obsidian/{app.json,appearance.json,core-plugins.json,workspace.json}` as Codex-generated ignored baseline; separate profile and bridge namespaces.
- Keep generated schemas, templates, Bases, dashboards, and S07 fixtures aligned with their owning generators and tests.

## Development Workflow

- Use `Makefile` targets as the canonical project entrypoint.
- Use pinned Python `3.12.8` and uv `0.8.14` in the container for canonical evidence.
- Derive `KNOWLEDGEOS_UID` and `KNOWLEDGEOS_GID` from the current host `id -u` and `id -g` values.
- Never use `1000:1000` as a UID/GID fallback.
- Treat the observed macOS `501:20` mismatch as a permission-risk case for bind mounts and the disposable uv cache.
- Fail closed when direct Compose runs lack numeric UID/GID values.
- Use `docker compose -f ops/compose.yaml run --rm dev ...` for disposable project commands.
- Run `make test` and `make lint` sequentially because they share the disposable uv cache.

## Verification

- Run `make source-check` and `make verify` before implementation.
- Run `make container-source-check` and `make container-verify` for canonical container evidence.
- Run `make blueprint-check` for JSON Schema and semantic evidence.
- Run `make schema-check` for `portable_core` generated-artifact zero-diff evidence.
- Run `make test` and `make lint` for feature and static-analysis evidence.
- Run `git diff --check` before handoff.
- Separate evidence and mark unrun checks explicitly.

## Handoff

- Inventory both roots with `find` and `git status --short --branch`, including ignored and empty namespaces, before declaring any session goal complete.
- Compare inventory with `README.md` and authoritative `docs/` contracts.
- Reconcile stale, missing, or unexpected paths before updating status documents or claiming completion.
- Report unresolved directory mismatches as incomplete or blocked.
- Record Git state, evidence, blockers, deferred opt-ins, and the next acceptance slice in `docs/IMPLEMENTATION_STATUS.md`.
- Record structural decisions in `docs/DECISIONS.md`, preserve the `.obsidian` baseline, and never claim app smoke without evidence.

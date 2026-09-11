# KnowledgeOS Project Contract

## Project Scope

- Treat `/Users/yuk/DevFolder/CodePractice/WorkingProject/KnowledgeOS` as the control workspace.
- Treat user-initialized control and `KnowledgeHub/` Git roots as independent repositories.
- Keep Docker work within Compose project `ops` and service `dev`.

## Project Structure

- Keep control documents, policies, source, and tests in root, `docs/`, and `ops/`.
- Keep Vault content in `KnowledgeHub/`; keep `runtime/` outside Git.
- Resolve conflicts in Blueprint Markdown, then `blueprint/blueprint.yaml`, then Whitepaper.
- Preserve `KnowledgeHub/.obsidian/{app.json,appearance.json,core-plugins.json,workspace.json}`; separate profile and bridge namespaces.
- Align generated artifacts with their generators and tests.

## Development Workflow

- Use `Makefile` targets as canonical entrypoint.
- Use pinned container Python `3.12.8` and uv `0.8.14` for canonical evidence.
- Derive `KNOWLEDGEOS_UID` and `KNOWLEDGEOS_GID` from host `id -u` and `id -g`.
- Reject UID/GID fallbacks; fail closed when Compose runs lack numeric values.
- Use `docker compose -f ops/compose.yaml run --rm dev ...` for disposable commands.
- Run `make test` and `make lint` sequentially for acceptance evidence.
- Keep `.knowledgeos-root.json` create-only; validate it against the root-sentinel schema.

## Verification

- Run `make source-check` and `make verify` before implementation.
- Run `make container-source-check` and `make container-verify` for canonical container evidence.
- Run `make blueprint-check` for schema/semantic evidence.
- Run `make schema-check` for generated-artifact evidence.
- Run `git diff --check` before handoff.
- Separate evidence layers; mark unrun, deferred, blocked, and permission-limited checks explicitly.

## Handoff

- Define `README.md` and `docs/{IMPLEMENTATION_STATUS,IMPLEMENTATION_PLAN,OPERATIONS,DECISIONS}.md` as time-sensitive state documents.
- Define `docs/{SOURCE_CONTRACT,ARCHITECTURE,RUNTIME,MOBILE}.md` as dependent state documents.
- Read applicable state documents before each session.
- Record date, stage, active slice, Git roots, dirty sets, blockers, and deferred opt-ins at session start.
- Inventory both roots with `find` and `git status --short --branch`; include ignored paths and empty namespaces.
- Compare state documents with live files, manifests, tests, and runtime evidence at session boundaries.
- Reconcile stale, missing, or unexpected paths; report unresolved mismatches as incomplete or blocked.
- Refresh every applicable state document before final verification and after final verification.
- Record Git state, evidence, blockers, deferred opt-ins, next slice, and structural decisions in status and decisions.

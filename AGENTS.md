# KnowledgeOS Project Contract

## Project Scope

- Treat this checkout as the KnowledgeOS control boundary.
- Treat `KnowledgeHub/` as an independent Vault Git root.
- Keep `runtime/` outside Git and sync roots.
- Use Compose project `ops` and service `dev` for canonical container work.

## Project Structure

- Read `PROJECT_STATE.md` as the sole machine-state source for goals, evidence, blockers, and handoff.
- Ignore external memory, `MEMORY.md`, rollout summaries, and chats; use `PROJECT_STATE.md` and live evidence for state.
- Resolve design conflicts in `OBSIDIAN_VAULT_BLUEPRINT.md`, `blueprint/blueprint.yaml`, then `OBSIDIAN_VAULT_WHITEPAPER.md`.
- Keep executable contracts in manifests, schemas, source, tests, and commands.
- Treat `docs/*.md` as compact agent contract indexes, not authoritative state.
- Keep machine state only in `PROJECT_STATE.md`; update root and nested `README.md` files only for human-facing information at close.
- Exclude every `README.md` from startup and in-task state reads.

## Development Workflow

- Use `Makefile` targets and container Python `3.12.8` with uv `0.8.14` for canonical work.
- Derive numeric `KNOWLEDGEOS_UID` and `KNOWLEDGEOS_GID`; fail closed when Compose values are missing.
- Use `/goal` mode for substantive multi-step work and keep the active goal aligned with session progress.
- Set `token_budget` only when the user explicitly requests a goal budget.
- Keep create-only writes inside validated targets.
- Preserve control and Vault worktrees during every slice.

## Verification

- Read `PROJECT_STATE.md` before implementation or resumption; compare handoff with live state and rerun `make source-check` and `make verify`.
- Run required container, blueprint, schema, test, and lint checks; run test and lint sequentially.
- Validate `PROJECT_STATE.md` with `scripts/validate_state.py` after every write.
- Separate static, semantic, runtime, artifact, deployment, external, and device evidence.
- Record unrun, deferred, blocked, and permission-limited checks with controlled result states.
- Run `git diff --check` in every changed Git root before handoff.

## Handoff

- Inventory control and Vault Git roots with dirty sets, ignored paths, and empty namespaces before handoff.
- Keep exactly one current goal and at most one next goal; preserve action-constraining decisions in `PROJECT_STATE.md`.
- Compare `PROJECT_STATE.md` and indexes with live paths, manifests, tests, and runtime evidence at boundaries.
- Reconcile stale, missing, or unexpected paths; record unresolved mismatches as incomplete or blocked.
- Refresh `PROJECT_STATE.md` after verification; read relevant `README.md` files only at close; confirm requested changes.

# KnowledgeOS Test Agent Contract

## Responsibility

- Treat `ops/tests/` as the sole home for pytest implementations, fixtures, and test-only helpers.
- Own the regression responsibility map for every test changed or removed.
- Update tests when executable contracts change and delete tests whose responsibilities are invalid or superseded.
- Preserve unique coverage for paths, schemas, transactions, rollback, digests, privacy, authorization, and proposal-only behavior.
- Keep reusable production behavior under `ops/src/vaultops/` and test scaffolding under `ops/tests/`.

## Isolation

- Build unit and integration inputs from deterministic fixtures and `tmp_path`.
- Never read, copy, mutate, or snapshot real `../../../../Vaults/KnowledgeHub/`, `runtime/`, or `.obsidian*` state.
- Limit exact deployed Markdown reconciliation to accepted paths beneath `../../../../Vaults/KnowledgeHub/99_System/`.
- Treat ordinary notes, unrelated plugins, unknown settings, presentation preferences, ordering, and versions as user-owned inputs.
- Validate only KnowledgeOS-owned capabilities and fail-closed write, execution, network, overwrite, and review-bypass controls.
- Keep live, runtime, and device smoke outside pytest and exercise its lifecycle with temporary fixtures.
- Use `99_System/Smoke` only during separately authorized live smoke and require verified cleanup.

## Workflow

- Run tests only through repository-root `make` targets and never invoke pytest or Docker Compose directly.
- Use `make test PYTEST_ARGS="..."` for focused runs through the canonical container entrypoint.
- Keep test and lint execution sequential.
- Add a failing responsibility test before changing behavior when practical.
- Remove duplicated assertions only after naming and preserving their unique responsibility owner.
- Reject migration-era absence checks and whole-profile equality unless an active contract still owns them.
- Preserve unrelated user and agent changes while editing or deleting tests.

## Verification

- Run the smallest focused `make test` selection before the full suite.
- Run `make test` and `make lint` before handoff after test changes.
- Run source, foundation, blueprint, schema, and container checks when their owned contracts change.
- Report performed evidence separately from unrun runtime, artifact, deployment, and device checks.
- Record deletions, retained responsibilities, commands, results, and blockers in `PROJECT_STATE.md`.
- Run `git diff --check` in both Git roots before handoff.

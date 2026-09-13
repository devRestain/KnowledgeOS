# KnowledgeOS Ops Contract

## Project Scope

- Treat `ops/` as the execution and verification surface for KnowledgeOS.
- Use `ops/compose.yaml` and service `dev` for canonical container work.
- Keep project dependencies and generated environments inside the declared container workflow.

## Project Structure

- Keep Python source under `ops/src/` and tests under `ops/tests/`.
- Keep policies, commands, prompts, and schemas under their declared `ops/` namespaces.
- Keep generated artifacts aligned with their declared generators and expected outputs.
- Keep `ops/uv.lock` and `ops/pyproject.toml` as the pinned toolchain contract.

## Development Workflow

- Use `Makefile` targets as canonical entrypoints for operations and checks.
- Use `docker compose -f ops/compose.yaml run --rm dev ...` for disposable commands.
- Pass numeric `KNOWLEDGEOS_UID` and `KNOWLEDGEOS_GID` and fail closed on omission.
- Run `make test` and `make lint` sequentially to avoid disposable cache races.
- Use `vaultctl` actions with declared schemas and stdin or file content transport.
- Keep arbitrary shell text outside command and LLM action inputs.
- Keep LLM actions proposal-only and require digest-bound approval before Vault or Git mutation.

## Verification

- Run `make source-check` for checksum trust-anchor evidence.
- Run `make verify` for foundation paths and Git-boundary evidence.
- Run `make container-source-check` and `make container-verify` for container evidence.
- Run `make blueprint-check` for JSON Schema and semantic evidence.
- Treat `make schema-check` as portable-core evidence; use compiler and evaluator checks for Base and dashboard claims.
- Pass Git safe-directory settings per invocation; never mutate global Git configuration.
- Separate static, semantic, runtime, and artifact results in `PROJECT_STATE.md`.
- Record unrun, deferred, blocked, or permission-limited checks with controlled results.

## Handoff

- Report changed source, policy, schema, test, and generated-artifact paths.
- Report the command, container identity, result, and evidence class for each executed check.
- Preserve unrelated dirty paths and leave unresolved environment failures as blockers.
- Refresh `PROJECT_STATE.md` before handing work back to the root workflow.

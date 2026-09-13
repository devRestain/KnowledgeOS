# Agent Operations Contract

- Use `make source-check` to verify the checksum trust anchor.
- Use `make verify` to verify required paths, Git boundaries, and foundation contracts.
- Use `make blueprint-check` for JSON Schema and semantic evidence.
- Use `make schema-check` for owned generated-artifact evidence.
- Use `make container-source-check` and `make container-verify` for canonical container evidence when the active slice requires them.
- Run `make test` and `make lint` sequentially to avoid disposable cache races.
- Use `docker compose -f ops/compose.yaml run --rm dev ...` for disposable commands.
- Derive numeric `KNOWLEDGEOS_UID` and `KNOWLEDGEOS_GID` from the invoking host account; fail closed on omission.
- Keep control, `KnowledgeHub/`, and `runtime/` as separate storage and evidence surfaces.
- Verify Vault sentinel identity and branch binding before sync or publish gates.
- Keep runtime payloads durable where classified and never treat ignored as disposable by default.
- Keep ignored Obsidian baselines separate from disposable app-smoke evidence.
- Keep Git, plugin, device, provider, remote, and LaunchAgent effects behind exact approval gates.
- Record every check in `PROJECT_STATE.md` with its evidence class and controlled result.

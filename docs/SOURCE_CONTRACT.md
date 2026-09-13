# Agent Source Contract

- Treat `OBSIDIAN_VAULT_BLUEPRINT.md` as the product and experience authority.
- Treat `blueprint/blueprint.yaml` as the machine-readable registry and exact enum, path, and command contract.
- Treat `OBSIDIAN_VAULT_WHITEPAPER.md` as the low-level technical annex.
- Verify these sources through `blueprint/CHECKSUMS.sha256` before relying on their content.
- Use `blueprint/blueprint.schema.json` for Draft 2020-12 structural validation.
- Run semantic validation after schema validation and report both results independently.
- Run generated-artifact zero-diff validation through `make schema-check` independently of schema and semantic results.
- Keep path, type, template, action, command, bridge state, Base, projection, and transaction contracts exact.
- Keep `PROJECT_STATE.md` as the only machine-state source; keep this file as a static source index.
- Record source-contract changes in state decision and evidence records before handoff.

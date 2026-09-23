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
- Treat the F01-F06 GUI-first period-note and `vaultctl` boundary as an implemented, evidence-bound slice: F01-F06 control and artifact checks pass, and the explicitly authorized 2026-09-23 Obsidian session provides separate Navigator creation/opening, Templater rendering, no-overwrite, and Note Toolbar device evidence in `PROJECT_STATE.md`.
- Keep GUI-created daily, weekly, and monthly notes subject to the same KnowledgeOS path, title, frontmatter, period, and hash-validation contracts; `vaultctl note validate` is the file-byte contract check and must not be replaced by Obsidian CLI output.
- Keep the production ownership split exact: Core Daily Notes creates daily notes, Notebook Navigator creates/opens weekly and monthly notes, Templater renders only bounded date and document fields, and no second Python period writer remains after F01.
- Keep Templater templates free of shell, system commands, user scripts, network, AI, Git, `vaultctl`, global new-file triggers, and approval-free existing-note mutation.
- Keep the initial official Obsidian CLI adapter status-only and internal to `vaultctl`; reject arbitrary command text, arbitrary argv, document read/write/create/append/search/eval, and plugin control operations.
- Record source-contract changes in state decision and evidence records before handoff.

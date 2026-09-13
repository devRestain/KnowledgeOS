# Agent Architecture Contract

- Keep the control workspace, `KnowledgeHub/` Vault, and `runtime/` execution state as separate boundaries.
- Keep `KnowledgeHub/` as an independent Git repository and canonical Obsidian root.
- Keep `runtime/` untracked, mode `0700`, and outside every sync root.
- Keep the container as the canonical dependency and execution surface.
- Keep `00_Inbox/Captures` as the single capture lifecycle path.
- Keep fixed Vault namespaces, seven Base files, fourteen views, sixteen templates, and eighteen note types aligned with Blueprint.
- Keep Markdown body and flat YAML Properties as the canonical note representation.
- Keep title equal to filename stem, timezone-aware datetimes, quoted wikilinks, and filesystem mtime freshness.
- Keep project bundles under `20_Projects/<name>/` with `Working/` and `Artifacts/` siblings.
- Keep iPhone capture, iPad reading, and Mac deciding or applying roles separate.
- Keep bridge messages immutable and prevent workers from implicit network Git commands.
- Keep canonical directory markers exact and exclude bridge event and runtime placeholder files.
- Keep bridge protocol fixtures separate from production request and response events.
- Keep ignored Obsidian baselines separate from app-smoke evidence.
- Keep LLM output schema-constrained and proposal-only until approval binds source, target, policy, schema, and digest.
- Keep privacy defaults conservative and exclude denied or local-only material from remote candidate sets.
- Keep confidential or institutionally restricted material outside this Vault until a separate boundary is approved.
- Review large-binary storage and Git LFS only after observed asset size and remote policy justify it.

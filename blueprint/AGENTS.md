# KnowledgeOS Blueprint Contract

## Project Scope

- Treat `blueprint/` as the machine-contract package for KnowledgeOS.
- Keep product decisions in the root Blueprint and exact registries in this package.

## Project Structure

- Treat `blueprint/blueprint.yaml` as the machine-readable registry.
- Treat `blueprint/blueprint.schema.json` as the Draft 2020-12 structural schema.
- Treat `blueprint/CHECKSUMS.sha256` as the source-byte trust anchor.
- Keep generated contract copies aligned with their declared owners.

## Development Workflow

- Resolve design conflicts in root Blueprint Markdown, YAML, then Whitepaper order.
- Validate schema before semantic rules and stop on structural failure.
- Update checksums only for intentional source changes.
- Keep path, type, template, action, command, bridge, projection, and transaction keys exact.

## Verification

- Run `make source-check` before relying on checked-in contract bytes.
- Run `make blueprint-check` for JSON Schema and semantic evidence.
- Run `make schema-check` independently for generated-artifact zero-diff evidence.
- Record schema, semantic, and artifact results as separate evidence classes.
- Keep blueprint validation read-only; never write reports or touch Vault or runtime paths.

## Handoff

- Report changed contract keys, schema effects, checksum updates, and generated outputs.
- Record unresolved contract ambiguity in `PROJECT_STATE.md` as a blocker or pending decision.
- Preserve the root Blueprint precedence and do not promote future phases as implemented.

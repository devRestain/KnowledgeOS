# Agent Execution Pipeline

- Read `PROJECT_STATE.md` before selecting or promoting a slice.
- Resolve conflicts in `OBSIDIAN_VAULT_BLUEPRINT.md`, `blueprint/blueprint.yaml`, then `OBSIDIAN_VAULT_WHITEPAPER.md` order.
- Implement one acceptance-gated capability per session and preserve the isolated boundaries below.
- Use fresh lane-local labels: `Cxx` for ordered core capabilities, `Dxx` for gated deployment overlays, and `Exx` for optional extensions.
- Interpret numeric order only within the same lane; never infer ordering or authorization across lanes.
- Treat planning, state migration, documentation maintenance, and legacy labels in Git history as non-ordering provenance.

## Core capability lane

- `C01` — Establish the Colima/Docker runtime and verification harness.
- `C02` — Validate the Blueprint with Draft 2020-12 JSON Schema.
- `C03` — Validate registry, path, action, command, and bridge semantics.
- `C04` — Validate Base, dashboard, projection, and transaction semantics.
- `C05` — Define generated-artifact ownership and byte-for-byte zero-diff checks.
- `C06` — Implement operational policy, strict note schema, and note engine.
- `C07` — Implement templates, additive bootstrap, and create-only project bundles.
- `C08` — Compile Bases, Home, Mobile, and deterministic dashboards.
- `C09` — Verify the portable Vault fixture and restricted-mode smoke boundary.
- `C10` — Implement offline bridge, response, and root-sentinel schemas.
- `C11` — Implement offline mobile shortcuts, durable outbox, and recovery contracts.
- `C12` — Implement provider-free read-only diagnostics and CLI integration.
- `C13` — Implement create-only local commands and guarded formatting.
- `C14` — Implement hash-bound asset, capture-finalize, and archive transactions.
- `C15` — Implement fsynced recovery journals and idempotent transaction replay.
- `C16` — Implement read-only reconcile, repair plans, repair apply, and receipt verification.
- `C17` — Implement provider-free bridge ingest, exact local Git publish, and crash recovery.
- `C18` — Implement the read-only deterministic triage proposal contract.
- `C19` — Implement provider-free review, approve, reject, and apply closure.
- `C20` — Implement remaining proposal actions, facade routes, and PRD traceability.
- `C21` — Implement deterministic JSONL projection and atomic generation pointers.
- `C22` — Implement lexical and typed-link retrieval with a frozen evaluation baseline.
- `C23` — Implement cited answers and the full provider-free `guestbook-horror` flow.
- `C24` — Render background artifacts and verify synthetic wake and recovery behavior.

## Deployment overlay lane

- `D01` — Configure Git identity and the production root sentinel after explicit confirmation.
- `D02` — Verify the Obsidian Mac Core profile from app-generated configuration.
- `D03` — Install and audit QuickAdd under its own approval gate.
- `D04` — Install and audit Templater under its own approval gate.
- `D05` — Install and audit Tasks under its own approval gate.
- `D06` — Install and audit Linter under its own approval gate.
- `D07` — Install and audit Obsidian Git under its own approval gate.
- `D08` — Verify iPhone and iPad Working Copy transport and the local result renderer after `D01` and `C11`.
- `D09` — Verify one live mobile bridge round trip after `D08` and `C17`.
- `D10` — Verify a synthetic Codex provider adapter after `C19`, and after `C20` when covering every action.

## Optional extension lane

- `E01` — Evaluate local vector and RRF against the frozen `C22` baseline.
- `E02` — Verify an explicitly selected local provider profile after `C20` and `C22`.
- `E03` — Install the LaunchAgent only after the `C24` artifact and rollback gates pass.
- `E04` — Activate each remote or unattended lane through a separate decision and authorization gate.
- `E05` — Implement a thin Obsidian client only after `C23` proves repeated CLI friction.

- Resume the core lane at `C20` after the completed `C19` boundary recorded in `PROJECT_STATE.md`.
- Keep deployment and extension lanes inactive unless their own prerequisites and exact external-effect approvals are satisfied.

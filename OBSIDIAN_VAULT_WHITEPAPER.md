# KnowledgeOS Whitepaper — technical annex index

> Role: low-level execution and verification contract for agents. It is not a status snapshot or a user manual.
> Product decisions: `OBSIDIAN_VAULT_BLUEPRINT.md`. Machine contract: `blueprint/blueprint.yaml` and `blueprint/blueprint.schema.json`.
> Live state: `PROJECT_STATE.md`. Human-facing explanation: `README.md`.

## Contract loading and precedence

- Load the Blueprint Markdown first, then YAML, then this annex for implementation detail.
- Validate YAML/schema semantics with `make blueprint-check`; validate source and required paths with `make source-check` and `make verify`.
- Use `docs/SOURCE_CONTRACT.md`, `docs/ARCHITECTURE.md`, `docs/RUNTIME.md`, `docs/OPERATIONS.md`, `docs/MOBILE.md`, and `docs/DECISIONS.md` as compact cross-links, not competing status stores.
- Do not treat examples, generated indexes, or this document as proof that a capability is implemented; use `PROJECT_STATE.md` and executed evidence.

## Runtime and storage model

- Control repository is the workspace root; Vault repository is `KnowledgeHub/`; runtime is untracked and local under `runtime/`.
- Canonical execution uses Compose service `dev` on the existing Colima VM, pinned Python `3.12.8`, and uv `0.8.14`.
- Pass numeric `KNOWLEDGEOS_UID` and `KNOWLEDGEOS_GID` from the host; reject missing or fallback values.
- Keep disposable caches, temp files, logs, queues, locks, indexes, and durable receipts in their declared runtime namespaces; never place them in Vault source folders.
- Bind mounts are non-root where configured; permissions, ownership, and path traversal checks are acceptance conditions.
- Preserve independent Git metadata and record control/Vault heads in receipts; never merge repositories implicitly.
- Use the `vaultctl` command namespace and stdin/file content transport; validate action config before execution and never route arbitrary shell text through a command lane.

## Source, schema, and artifact boundaries

- Markdown body plus flat YAML Properties is the note source of truth.
- Exact note types, properties, relations, templates, Bases, dashboards, fixed paths, marker files, actions, bridge messages, retrieval, privacy, and Git policy are YAML-owned.
- JSON Schema validates shape; semantic validators validate cross-field meaning; generated-artifact checks validate ownership and byte identity. These are distinct claims.
- `blueprint/CHECKSUMS.sha256` binds the source documents and schema artifacts; update it only with the intentional source change and rerun `make source-check`.
- Generated files must have one declared generator, a safe path, deterministic ordering, and byte-for-byte zero-diff evidence.

## Writer and transaction protocol

- Enforce one writer per target file and exact-path allowlists.
- Create-only paths fail on collision; mutable paths require an expected SHA-256 or explicit maintenance approval.
- Write to a same-filesystem temporary file, fsync as required by the runtime contract, atomically replace or move, then record the resulting hash.
- Validate before mutation; preserve the original until validation and commit succeed; never silently repair or delete user content.
- Journal intent, precondition hash, changed paths, postcondition hash, approval id, and both Git heads.
- Recovery replays or rolls back only a journaled transaction with matching hashes; ambiguous state fails closed and requests Mac review.
- Asset import, capture finalize, project archive, and recovery are separate transaction types; do not combine them implicitly.

## Capture, bridge, and job state

- Capture path is mobile/local outbox → immutable `.vault-bridge` request → schema validation → local queue → interactive Mac apply.
- Bridge requests contain references and metadata, not executable code or hidden provider/network instructions.
- Trusted protocol schemas are copied under `KnowledgeHub/.vault-bridge/protocol`; requests and responses are empty until an enabled lane produces them.
- No bridge event grants Git pull/push, plugin install, remote API, or unattended execution.
- Job states are explicit (`received`, `validated`, `queued`, `proposed`, `approved`, `applied`, `rejected`, `failed`, `expired`); each transition is append-only and idempotent.
- Deduplicate by event/action id and source hash; quarantine invalid, stale, replayed, or path-escaping events.

## LLM proposal protocol

- Provider execution is optional and isolated from canonical write paths.
- Prompts, provider ids, model settings, and secrets are not persisted in Vault notes; external calls require a separate authorization gate.
- LLM output is a typed triage, finalize, or answer proposal with source references, confidence/coverage, input hash, and requested mutations.
- Human approval binds the proposal hash, target hashes, action id, and scope; apply rejects any drift.
- No silent provider fallback, live-note mutation, arbitrary shell command, or unreviewed remote result is allowed.
- Sensitive, people, meeting, confidential, and restricted content follows YAML privacy policy and may be denied before prompt construction.

## Projection and retrieval protocol

- Projection reads canonical Markdown/YAML and writes only declared runtime/generated namespaces.
- Generation is deterministic by stable id, path, and declared sort order; output includes source hash, schema version, and build metadata.
- Publish by atomic pointer swap; readers use one current pointer and tolerate no partial index.
- Retrieval order is native Obsidian → lexical/metadata filters → typed-link expansion → optional local vector → reciprocal-rank fusion.
- Vector, remote, and always-on lanes are opt-in; missing or stale indexes fail closed or fall back to lexical results with an explicit status.
- Answers cite note ids/paths and distinguish source text, derived projection, and model inference.

## Plugin, device, and remote gates

- Core Obsidian and portable Markdown/YAML must remain usable without community plugins.
- QuickAdd, Templater, Bases extensions, Git, Working Copy, LaunchAgent, bridge worker, provider, and remote sync are profile-specific opt-ins.
- Plugin configuration is replaceable and must not become a second source of truth, secret store, or hidden index.
- Mobile Git requires Working Copy Pro, external worktree, sentinel identity/fingerprint/branch/worktree checks, and a successful round trip.
- Mac alone may apply structural changes, resolve conflicts, alter profiles, and publish Git changes.
- Do not combine parallel iCloud/Obsidian Sync/Dropbox/OneDrive writers with the canonical mobile path.
- LaunchAgent and network work require explicit user opt-in, observable logs, bounded retries, and a stop/disable path.

## Privacy and security gates

- Default sensitivity is personal/ask; AI access to people and meeting content is deny-by-default.
- Keep confidential/restricted data in a separate Vault or approved local boundary; do not copy it into prompts, indexes, receipts, or logs.
- Treat paths, front matter, bridge payloads, plugin files, and generated content as untrusted data; validate schema, path, size, encoding, and symlink behavior.
- Use least-privilege filesystem access, no implicit credential discovery, and no provider key in Git or runtime receipts.
- Redact or hash content in diagnostics where full text is not needed; preserve enough evidence to reproduce a decision.

## Git and synchronization rules

- Control and Vault Git roots are independent; exact-path commits are the only canonical publish unit.
- Never force-push, reset, stash, rebase, auto-merge, or implicitly pull/push.
- A dirty or divergent root is a precondition failure for apply unless an explicit recovery transaction covers it.
- Verify branch, worktree, root sentinel, and remote identity before mobile or remote operations.
- Sync is transport, not authority: reconcile into canonical Markdown/YAML, then regenerate projections.

## Verification sequence and evidence

```text
make source-check → make verify → make blueprint-check → git diff --check
```

- Static evidence: file shape, checksums, required paths, directory markers, and policy scans.
- Semantic evidence: JSON Schema plus cross-file/registry invariants.
- Artifact evidence: generator ownership and zero-diff output.
- Runtime/container evidence: commands and tests executed in the pinned Compose environment.
- Device/deployment/external evidence: separate opt-in gates; absence or inability to run is not PASS.
- Record command, environment, result, timestamp, dirty set, and blocker in `PROJECT_STATE.md`; do not compress different evidence classes into one claim.

## Capability boundary

- This annex defines target-safe mechanics; it does not assert that later phases are implemented.
- Current phase, completed slices, deferred opt-ins, blockers, and next action are authoritative only in `PROJECT_STATE.md`.
- If this annex conflicts with the Blueprint, follow the Blueprint precedence and record the discrepancy for maintenance.

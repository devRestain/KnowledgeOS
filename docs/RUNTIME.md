# Agent Runtime Contract

- Bind Operation State and host Runtime through separate config v3 roots without aliases or implicit nested layout.
- Keep durable owner control, active generation pins and frozen restart evidence in State.
- Keep reconstructable index bodies, caches, logs and host process artifacts in Runtime.
- Keep both private roots outside Git and every sync root.
- Keep Python, uv, `vaultctl`, `vaultmcp`, and dependencies in the project container image or container filesystem.
- Keep the bind-mounted runtime and disposable uv cache separate.
- Match container UID and GID to numeric host identity and fail closed on missing values.
- Keep State/Runtime directories at mode `0700`, private payload files at mode `0600`, and process umask at `077`.
- Keep Docker socket, host secrets, keychains, SSH agents, and provider networks out of the runtime container.
- Keep current owner Work, method, Graph, proposal, and effect intents bound to immutable references and current State pins.
- Replay identical effect IDs and digests idempotently; reject changed digest, malformed input, traversal, wrong owner, and unknown action.
- Fail apply when source, target, policy, or schema hashes change after approval.
- Reconcile crash state through journals and receipts without silently changing canonical Markdown.
- Preserve receipt, authorization, and recovery evidence; reset only with an explicit backup and retention gate.
- Finalize owner decisions, exact effect intents, receipts, outbox and unresolved outcomes in State owner-control.json through OwnerJournal.
- Keep owner journal, linked transaction journals, and receipts in Operation State. Fsync immutable intent before an allowed canonical effect and reconcile incomplete or uncertain outcomes against exact preimage and postcondition evidence.
- Keep repair plans digest-bound and create-only under State; require a fresh observation match before maintenance apply. MCP `proposal_recovery_plan` records a Pending plan and cannot execute repair.

## Readiness and predeployment archive retirement

- Reject v1/v2 config and incompatible State before any new boundary writes; perform no automatic conversion.
- Use exact previous bytes, atomic replacement, fsync and generation/coordination fences for owner writers.
- Retain the latest 100 contracted detailed history records, expire unstarted work after 12 hours and preserve minimum dedupe receipts for 12 hours; keep unresolved identity/digest/fence records and required active evidence.
- Recover by observing journal and actual artifacts without automatic dispatch or unknown-effect retry.
- Remove withdrawn predeployment payloads instead of retaining them as current KnowledgeOS evidence. First produce an exact path and hash inventory, then check owner journal references, current index and selection pointers, transition records, active Work, receipts and recovery code. A transition record that still names an archive must be retired or replaced by a current-state checkpoint before deleting that archive; deleting only its payload would break replay and status checks.
- Prove the retirement on a disposable copy with the same reference graph. Then take the owner writer lock, verify the inventory has not changed, write the retirement intent and current-state checkpoint, remove only the selected archived payload and its obsolete metadata, fsync the parent, and run owner status, recovery and index verification. If any reference or in-flight effect remains, stop with an explicit blocker. Never import an archived approval, token, job or generation into the current owner State.
- Use `make source-check`, `make verify`, `make blueprint-check`, `make schema-check`, `make core-readiness-check`, and `make acceptance` with the proven project image and disposable roots. Distinguish the already performed owner State cutovers from unrun Hermes Runner, Thin Client installation, external Gateway, and device adoption.

# Agent Runtime Contract

- Bind Operation State and host Runtime through separate config v3 roots without aliases or implicit nested layout.
- Keep durable owner control, active generation pins and frozen restart evidence in State.
- Keep reconstructable index bodies, caches, logs and host process artifacts in Runtime.
- Keep both private roots outside Git and every sync root.
- Keep Python, uv, vaultctl, workers, and dependencies in the container image or container filesystem.
- Keep the bind-mounted runtime and disposable uv cache separate.
- Match container UID and GID to numeric host identity and fail closed on missing values.
- Keep State/Runtime directories at mode `0700`, private payload files at mode `0600`, and process umask at `077`.
- Keep Docker socket, host secrets, keychains, SSH agents, and provider networks out of the runtime container.
- Keep job manifests immutable and state markers mutable.
- Resume same job ID and digest idempotently; quarantine changed digest, malformed input, traversal, and unknown action.
- Fail apply when source, target, policy, or schema hashes change after approval.
- Reconcile crash state through journals and receipts without silently changing canonical Markdown.
- Preserve receipt, authorization, and recovery evidence; reset only with an explicit backup and retention gate.
- Finalize owner decisions, exact effect intents, receipts, outbox and unresolved outcomes in State owner-control.json through OwnerJournal.
- Store linked local transaction journals at `state/runs/JOB_ID/journal.jsonl` as hash-chained execution/recovery evidence, carrying owner_intent_id when owner-admitted.
- Fsync the immutable intent and each state append before canonical Vault mutation; write transaction completion receipts create-only under `state/receipts/`.
- Resume only when job, operation, path, and pre/postcondition hashes match; quarantine malformed or ambiguous transaction journals under `state/quarantine/transactions/`.
- Keep repair plans canonical, digest-bound, and create-only under State; require a fresh observation match before repair apply.
- Keep `.vault-bridge` as tracked Vault transport and accept a request only from the expected committed branch with add-only request history and matching source blobs.
- Keep bridge ingest manifests in `state/queue/`, outside the Vault commit; make the same job and digest idempotent and quarantine a changed digest.
- Publish only the response event and, for `needs_review`, its proposal artifact in one exact-path local Vault commit; do not fetch, pull, push, or invoke a provider.
- Fsync the bridge publish intent before creating outputs; resume only identical paths and bytes, then append completion and write the local receipt after commit.

## Readiness and retained legacy placement

- Reject v1/v2 config and incompatible State before any new boundary writes; perform no automatic conversion.
- Use exact previous bytes, atomic replacement, fsync and generation/coordination fences for owner writers.
- Retain the latest 100 contracted detailed history records, expire unstarted work after 12 hours and preserve minimum dedupe receipts for 12 hours; keep unresolved identity/digest/fence records and required active evidence.
- Recover by observing journal and actual artifacts without automatic dispatch or unknown-effect retry.
- Preserve all existing Runtimes/KnowledgeOS-runtime records at their old location until a separately selected data conversion and writer cutover.
- Classify legacy journals, queues, approvals and receipts as inert Operation evidence; legacy approvals do not authorize v3 owner apply.
- Use make core-readiness-check and make acceptance with the proven image and disposable roots. Local fixture results do not establish real State migration, runtime adoption, provider, deployment, device or other-host acceptance.

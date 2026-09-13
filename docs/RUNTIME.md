# Agent Runtime Contract

- Keep `runtime/` as durable control-side execution state, not Vault content or bridge transport.
- Keep `runtime/` outside Git and every sync root.
- Keep Python, uv, vaultctl, workers, and dependencies in the container image or container filesystem.
- Keep the bind-mounted runtime and disposable uv cache separate.
- Match container UID and GID to numeric host identity and fail closed on missing values.
- Keep runtime directories at mode `0700`, payload files at mode `0600`, and process umask at `077`.
- Keep Docker socket, host secrets, keychains, SSH agents, and provider networks out of the runtime container.
- Keep job manifests immutable and state markers mutable.
- Resume same job ID and digest idempotently; quarantine changed digest, malformed input, traversal, and unknown action.
- Fail apply when source, target, policy, or schema hashes change after approval.
- Reconcile crash state through journals and receipts without silently changing canonical Markdown.
- Preserve receipt, authorization, and recovery evidence; reset only with an explicit backup and retention gate.

# Agent Status Index

- Read `PROJECT_STATE.md` for authoritative status, checkpoint, goals, evidence, blockers, and handoff.
- Treat this file as an index and never as a competing machine-state source.
- Treat the live worktree, manifests, source, tests, and recorded evidence as authority for capability claims.
- Preserve the acceptance-slice boundary and finish one bounded slice before promoting another.
- Preserve the product boundary: core capability sessions `C01` through `C23` are complete and `C24` is the next planned core slice; the interactive MacBook completion target is `C23` and `C24` is optional core reliability.
- C21 provides a deterministic Markdown/YAML JSONL projection with immutable generations and one atomic runtime pointer; it does not implement retrieval or provider overlays.
- C22 provides read-only lexical and bounded typed-link retrieval over one verified C21 generation with digest-bound frozen candidates and evaluation; C23 adds deterministic extractive cited answers over that same pinned evidence without provider or Vault mutation.
- Preserve deployment evidence separately: the MacBook baseline through `D07` is complete; `D08` and `D09` are mobile overlays excluded from this baseline, and `D10` remains a separately gated provider overlay.
- Keep optional extension sessions `E01` through `E05`, background activation, and remote effects inactive because no optional extension is required for the MacBook baseline.
- Treat legacy numbers in Git history as provenance only, never as current execution order.
- Keep historical narrative, obsolete counts, and superseded snapshots out of this index.

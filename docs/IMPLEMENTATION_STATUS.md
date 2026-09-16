# Agent Status Index

- Read `PROJECT_STATE.md` for authoritative status, checkpoint, goals, evidence, blockers, and handoff.
- Treat this file as an index and never as a competing machine-state source.
- Treat the live worktree, manifests, source, tests, and recorded evidence as authority for capability claims.
- Preserve the acceptance-slice boundary and finish one bounded slice before promoting another.
- Preserve the product boundary: core capability sessions `C01` through `C17` are complete and `C18` is the next planned core slice.
- Preserve deployment evidence separately: `D01` is complete; `D02` through `D10` remain gated overlays unless `PROJECT_STATE.md` records a verified change.
- Keep optional extension sessions `E01` through `E05`, background activation, and remote effects inactive until their gates pass.
- Treat legacy numbers in Git history as provenance only, never as current execution order.
- Keep historical narrative, obsolete counts, and superseded snapshots out of this index.

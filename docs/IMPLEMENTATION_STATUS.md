# Agent Status Index

- Read `PROJECT_STATE.md` for authoritative status, checkpoint, goals, evidence, blockers, and handoff.
- Treat this file as an index and never as a competing machine-state source.
- Treat the live worktree, manifests, source, tests, and recorded evidence as authority for capability claims.
- Preserve the acceptance-slice boundary and finish one bounded slice before promoting another.
- Preserve the product boundary: S13C hash-bound asset, finalize, and archive transactions are complete; S13D fsynced recovery journaling follows.
- Keep S10 device work, S11/S12 plugin work, provider overlays, background execution, and remote effects inactive until their gates pass.
- Keep historical narrative, obsolete counts, and superseded snapshots out of this index.

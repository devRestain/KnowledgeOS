---
title: Design the ephemeral smoke lifecycle
status: resolved
assignee: codex
claimed: 2026-09-26
review_requested: 2026-09-26
resolved: 2026-09-26
labels:
  - wayfinder:prototype
  - T01
parent: ../MAP.md
blocks:
  - Set verification tiers and the regression budget
---

# Design the ephemeral smoke lifecycle

## Question

What explicit live/runtime/device smoke protocol can create a collision-proof
temporary note, prove only the requested behavior, and guarantee cleanup after
success, assertion failure, timeout, interruption, or a stale prior run without
turning the real Vault into a regression fixture?

The prototype decision must cover namespace, unique identity, preflight,
create-only semantics, cleanup verification, crash recovery, evidence capture,
and the boundary between automated cleanup and user confirmation. It must remain
outside the default regression command.

## Resolution

[Ephemeral live smoke lifecycle prototype v1](../prototypes/ephemeral-smoke-lifecycle-v1.md)
defines a single-note `99_System/Smoke` namespace, UUID-bound create-only path,
private runtime journal, bounded state machine, stable-absence verification,
stale-run recovery, evidence separation, and hermetic failure matrix.

The user accepted bounded automatic stale cleanup. A stale note may be unlinked
without another confirmation only when Vault identity, private journal path,
ownership marker, and sealed SHA-256 digest all match. Any mismatch preserves
the note and requires exact human confirmation.

The accepted lifecycle keeps the temporary note under `99_System/Smoke`, binds
one UUIDv4 path to one private runtime journal, uses create-only semantics,
requires cleanup for a passing result, and covers failure, timeout, catchable
interruption, crash recovery, and stable-absence verification. Default pytest
tests only synthesized temporary Vaults and never executes the live command.

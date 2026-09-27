---
title: Retire overlapping and historical test contracts
status: resolved
assignee: codex
claimed: 2026-09-27
resolved: 2026-09-27
labels:
  - wayfinder:grilling
  - T01
parent: ../MAP.md
blocks:
  - Set verification tiers and the regression budget
---

# Retire overlapping and historical test contracts

## Question

What responsibility matrix and deletion rule will identify tests that are true
duplicates, migration-era absence checks, broad snapshots, or superseded
contracts while preserving unique failure detection for safety, schema,
transactions, recovery, and authorization boundaries?

The answer must name exact keep, rewrite, merge, parameterize, and delete
actions. Test age, label, file length, or current passing status alone must not
justify deletion.

## Findings

- The refreshed suite contains 67 implementation files, 13,143 source lines,
  352 test functions, and 431 parametrized cases.
- Sixteen files derive the real KnowledgeHub from the source root. The coupling
  is concentrated in deployed template/dashboard parity, one diagnostics
  sentinel assertion, ten P03-P12 profile snapshots, the F cross-profile
  snapshot, and retrospective Vault structure checks.
- P01-P12 repeat the same profile-construction and three-case shape while mixing
  required capability, safety, presentation, and complete inventory concerns.
- Generated-artifact parity is repeated in schema export and the C20, C30, C31,
  C38, and E01 capability tests. Executable schema semantics remain valuable;
  repeated checked-in byte equality does not.
- `test_foundation.py` only reruns the current checkout foundation function from
  pytest, while the Make graph already owns current-checkout and container
  foundation evidence.
- `test_vault_structure.py` primarily proves a past migration and the current
  contents of a mutable Vault. Its remaining symlink and protected-system
  concerns have narrower owners.
- Transactional absence assertions elsewhere prove that unsafe operations made
  no change. Those are current safety evidence and are not legacy merely
  because they assert nonexistence.

## Resolution

Accept the exact keep, rewrite, consolidate, and delete actions in
[T01 Test Responsibility and Retirement Matrix](../RESPONSIBILITY_MATRIX.md).

The implementation will:

1. keep 40 files with distinct parser, path, transaction, recovery, proposal,
   retrieval, provider, authorization, rollback, adapter, and command duties;
2. rewrite six coupled or workspace-dependent files in place;
3. trim repeated artifact-parity assertions from C20, C30, C31, C38, and E01
   while preserving executable behavior and schema semantics;
4. replace P01-P12 plus the F snapshot with two synthesized-profile test files;
5. replace the current-workspace foundation test with an isolated
   control-foundation contract test;
6. delete the four retrospective Vault-structure assertions and move only the
   named `99_System`, symlink, identity, and path-safety duties to their narrow
   owners;
7. add one hermetic unit-test file for the explicit read-only Vault artifact
   checker; and
8. keep every test implementation, fixture, and helper under `ops/tests` and
   behind the root Makefile entrypoint.

The planning baseline projected a shape change from 67 to 56 `test_*.py` files
and identified 16 direct real-Vault dependencies. These were estimates and
inventory findings, not final-count requirements. Case count is not a deletion
target; the verification-tier ticket defines required invariance mutations and
records collection and duration as informational measurements.

## Deletion rule

Deletion requires a named current responsibility, a surviving owner or an
accepted retired contract, and a focused Make-driven check of the survivor.
Unique fail-closed coverage for path confinement, schema strictness,
transactions, recovery, rollback, privacy, provider authorization, review, and
digest binding cannot be traded for a smaller count. Historical absence,
deployed-copy duplication, whole-profile equality, and repeated ownership
parity may be removed under the matrix.

## Consequences

- Ordinary note changes, profile additions, plugin order, UI preferences, and
  unrelated plugin data cease to be regression inputs.
- High-risk negative cases remain, but their fixtures become minimal and make
  the exact owned invariant visible.
- Artifact byte parity has one control owner and one separately invoked
  `99_System` deployment owner instead of capability-by-capability repetition.
- The verification-tier ticket is now unblocked.

## Evidence

- `ops/tests/test_*.py` — refreshed file, line, function, direct-root, broad-copy,
  and workspace-dependent inventory on 2026-09-27.
- `planning/wayfinder/test-architecture/BASELINE.md` — original coupling and
  runtime baseline.
- `planning/wayfinder/test-architecture/RESPONSIBILITY_MATRIX.md` — exact
  per-file actions, deletion gate, replacement files, and expected shape.
- `planning/wayfinder/test-architecture/tickets/define-open-world-plugin-invariants.md`
  — accepted required-subset and safety-critical plugin responsibilities.
- `planning/wayfinder/test-architecture/tickets/choose-the-system-artifact-ownership-model.md`
  — accepted control, deployed-system, user-overlay, and identity ownership
  split.

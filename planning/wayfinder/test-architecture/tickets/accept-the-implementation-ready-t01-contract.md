---
title: Accept the implementation-ready T01 contract
status: resolved
assignee: codex
claimed: 2026-09-27
resolved: 2026-09-27
labels:
  - wayfinder:grilling
  - T01
parent: ../MAP.md
blocked_by:
  - Set verification tiers and the regression budget
---

# Accept the implementation-ready T01 contract

## Question

Does the assembled T01 contract fully specify the ownership boundary, exact
source and test changes, deletion list, migration order, temporary fixture and
smoke design, state/document updates, rollback points, and canonical acceptance
checks so that implementation can proceed without inventing another product
decision or touching user-owned KnowledgeHub content?

## Resolution

Yes. Accept
[T01 Hermetic Test Architecture Implementation Contract](../IMPLEMENTATION_CONTRACT.md)
as implementation-ready.

The assembled contract fixes, without another product decision:

- allowed and forbidden default-regression inputs and mount enforcement;
- four ownership classes and the exact 30-path deployed `99_System` allowlist;
- root-note, profile, bridge, identity, runtime, and device exclusions;
- P01-P12 required-subset and safety-critical open-world semantics;
- the exact keep, rewrite, consolidate, parameterize, and delete actions for
  every current test file;
- all replacement test and support filenames under `ops/tests`;
- the Make-only command graph and the absence of live pytest markers;
- the UUID-bound smoke lifecycle and four-factor stale cleanup rule;
- the eight-stage migration order and rollback evidence after each stage;
- structural hermeticity, invariance, and failure-locality predicates, with
  file, line, case, and duration totals treated as informational measurements;
- canonical close checks and both-root preservation requirements; and
- explicit exclusions for Vault mutation, Obsidian/device effects, services,
  providers, commits, pushes, deployment, and migration.

## Acceptance audit

| Required contract element | Authoritative planning source | Result |
|---|---|---|
| Hermetic regression inputs and enforcement | `define-the-hermetic-regression-boundary.md` | Specified |
| System artifact ownership and deployed-copy command | `choose-the-system-artifact-ownership-model.md` | Specified |
| Essential plugin and setting invariants | `define-open-world-plugin-invariants.md` | Specified |
| Exact retirement and replacement actions | `RESPONSIBILITY_MATRIX.md` | Specified |
| Temporary live-smoke lifecycle | `ephemeral-smoke-lifecycle-v1.md` | Accepted by user |
| Verification tiers and budgets | `set-verification-tiers-and-the-regression-budget.md` | Specified |
| Migration order, rollback points, and checks | `IMPLEMENTATION_CONTRACT.md` | Specified |
| State, docs, and handoff closure | `IMPLEMENTATION_CONTRACT.md` and project contract | Specified |

## Implementation handoff

Begin with stage 1, the hermetic harness. Do not delete a current test until its
surviving owner passes through the Make entrypoint. Do not run the explicit
Vault artifact, profile, or live-smoke targets merely to implement them. The
real KnowledgeHub must remain untouched throughout the implementation and
canonical acceptance run.

## User clarifications

- On 2026-09-27 the user clarified that document-structure contract assertions
  are limited to system-owned documents under `99_System`; root `Home.md`,
  `Mobile.md`, and ordinary-document structure are excluded.
- The user also clarified that test-file/source-line totals and test durations
  are informational measurements, not hard acceptance limits. The
  implementation contract and verification-tier ticket carry the current
  measurement policy.

## Evidence

- Every Wayfinder dependency is resolved.
- The 2026-09-27 live inventory refreshed the file, line, test, coupling,
  whole-copy, and runtime baselines before acceptance.
- The planning-stage canonical profile run passed 431 cases in 85.48 seconds
  and observed no individual call over two seconds. This is historical context,
  not a duration acceptance threshold.
- `PROJECT_STATE.md` recorded T01 as active when this planning ticket was
  resolved. Final implementation and verification status belongs to the live
  state file and the latest handoff checkpoint.

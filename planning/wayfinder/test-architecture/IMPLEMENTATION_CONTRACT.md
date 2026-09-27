---
title: T01 Hermetic Test Architecture Implementation Contract
status: accepted
updated: 2026-09-27
label: T01
authority: planning contract subordinate to PROJECT_STATE.md
---

# T01 Hermetic Test Architecture Implementation Contract

## 1. Objective

Rebuild the KnowledgeOS verification model so default regression and control
checks are independent from the mutable KnowledgeHub, while preserving unique
safety, schema, transaction, recovery, privacy, authorization, review, and
digest-binding responsibilities.

Implementation is complete only when the executable boundary, migrated tests,
open-world diagnostics, explicit system-artifact checker, smoke lifecycle, and
all acceptance checks agree. A smaller or faster suite alone is not completion.

## 2. Authority and ownership

- `PROJECT_STATE.md` remains the sole machine-state authority.
- `ops/tests/AGENTS.md` governs every test, fixture, and test-only helper.
- `ops/tests` is the sole test implementation root.
- Repository-root Make targets are the sole test entrypoints.
- The control Git root owns source, deterministic generators, policies,
  schemas, and expected bytes.
- KnowledgeHub remains an independent user-controlled Git root.
- Default tests and control checks never read or write the real KnowledgeHub or
  project runtime.
- Exact deployed document ownership is limited to the named `99_System`
  allowlist below and checked only by an explicit read-only command.

## 3. Named deployed system-artifact allowlist

The explicit checker accepts only these generator-owned or control-expected
Vault-relative paths:

### Templates

- `99_System/Templates/T00_Capture.md`
- `99_System/Templates/T01_AI_Proposal.md`
- `99_System/Templates/T10_Daily.md`
- `99_System/Templates/T11_Weekly.md`
- `99_System/Templates/T12_Monthly.md`
- `99_System/Templates/T20_Project.md`
- `99_System/Templates/T21_Idea.md`
- `99_System/Templates/T22_Question.md`
- `99_System/Templates/T23_Artifact.md`
- `99_System/Templates/T24_Project_Note.md`
- `99_System/Templates/T30_Area.md`
- `99_System/Templates/T40_Knowledge.md`
- `99_System/Templates/T41_Source.md`
- `99_System/Templates/T42_Person.md`
- `99_System/Templates/T50_MOC.md`
- `99_System/Templates/T60_Meeting.md`

### Bases, dashboards, schema, script, and CSS

- `99_System/Bases/Inbox.base`
- `99_System/Bases/Projects.base`
- `99_System/Bases/Decisions.base`
- `99_System/Bases/Knowledge.base`
- `99_System/Bases/Sources.base`
- `99_System/Bases/Review.base`
- `99_System/Bases/Journal.base`
- `99_System/Bases/Compass.base`
- `99_System/Dashboards/Tasks.md`
- `99_System/Dashboards/Today_Focus.md`
- `99_System/Dashboards/Weekly_Review.md`
- `99_System/Schemas/Property_Dictionary.md`
- `99_System/Scripts/QuickAdd/PrepareTitle.js`
- `99_System/CSS/dashboard.css`

The checker rejects root notes, ordinary content, profiles, `.vault-bridge`,
identity files, `99_System/Smoke`, wildcards, directory ownership, and every
unlisted path before reading it. Additional user files, including additional
files below `99_System`, are ignored unless they collide with one named target.

## 4. Exact implementation surface

### Runner and command graph

- Update `Makefile` with the hermetic Compose graph, focused test arguments,
  `test-invariance`, `vault-artifact-check`, `profile-check`, `live-smoke`, and
  sequential `acceptance` targets.
- Add `ops/compose.test.yaml` to replace the control, Vault, and runtime mounts
  for pytest and lint.
- Update `ops/pyproject.toml` only for deterministic cache, warning, or test-path
  configuration required by the accepted runner; do not add live markers.
- Extend `ops/tests/conftest.py` with Make, hermetic-mount, and collection-source
  guards.
- Extend `ops/tests/test_test_runner_contract.py` with guard failure cases.

### Control and deployment ownership

- Refactor `ops/src/vaultops/foundation.py` and `ops/check-foundation.sh` so
  default foundation checks contain no mutable Vault topology or content.
- Restrict `ops/src/vaultops/schema_export.py` and
  `ops/config/generated-artifacts.yaml` to control-root artifacts.
- Remove deployed bridge protocol schema copies from generated ownership while
  retaining the canonical schemas under `ops/schemas`.
- Add `ops/src/vaultops/vault_artifacts.py` for the named read-only
  `99_System` checker and deterministic expected-byte registry.
- Add a control-side expected source for `PrepareTitle.js`; never derive its
  expected bytes from the deployed Vault.
- Split user defaults from system outputs in
  `ops/src/vaultops/base_dashboard.py` and preserve create-only behavior in
  `ops/src/vaultops/bootstrap.py`.
- Wire read-only checker commands through `ops/src/vaultops/cli.py` without
  adding a default writer or mutating Vault bytes.

### Open-world diagnostics

- Refactor `ops/src/vaultops/diagnostics.py` so unmanaged community plugins and
  future Core flags are informational and do not enter health errors.
- Update the P01-P12 setting modules to validate stable owned identifiers,
  required capabilities, and safety-critical fields only.
- Remove ordinary root-note reads from plugin contracts.
- Keep serialized deployment evidence separate from runtime/device proof.
- Never open an unmanaged plugin's manifest or data file.

### Hermetic test support and migration

- Add `ops/tests/support/control_factory.py`, `plugin_factory.py`, and
  `snapshots.py`.
- Replace all 27 whole-control copies with declared minimal fixtures.
- Implement every action in `RESPONSIBILITY_MATRIX.md`.
- Add `test_foundation_contract.py`, `test_plugin_audit.py`,
  `test_plugin_invariants.py`, `test_vault_artifact_check.py`, and
  `test_hermetic_invariance.py`.
- Delete the twelve P01-P12 files, `test_f_contracts.py`,
  `test_foundation.py`, and `test_vault_structure.py` only after their surviving
  owners pass focused Make checks.
- Remove the named duplicate artifact assertions and historical dashboard or
  migration absence assertions from the matrix.

### Smoke lifecycle

- Add a smoke implementation module only if needed to expose the accepted
  lifecycle; unit-test it exclusively against temporary Vaults and private
  temporary runtime journals.
- Keep live execution behind `make live-smoke` and exact separate
  authorization. T01 does not execute a live smoke merely to implement the
  command.
- Auto-delete stale residue only when Vault identity, exact private journal
  path, ownership marker, and sealed SHA-256 digest all match.
- Preserve the note and return an exact confirmation request on every mismatch.

## 5. Migration order and rollback points

Each stage must leave default tests runnable or record a precise temporary
failure before continuing. No stage mutates KnowledgeHub.

1. **Harness:** add the Compose override, final Make graph, guards, and minimal
   fixture support. Rollback point: focused runner-contract test plus unchanged
   production behavior.
2. **Control foundation:** split control checks from deployment checks.
   Rollback point: host and container control foundation pass with masked Vault
   aliases.
3. **Artifact ownership:** separate control schema export and the read-only
   `99_System` checker. Rollback point: focused schema and temporary-Vault
   artifact tests pass; real Vault remains unmodified.
4. **Open-world diagnostics:** change production classification and migrate
   P01-P12 into synthesized fixtures. Rollback point: focused plugin audit and
   invariant tests pass with required, additive, and unsafe cases.
5. **Coupled tests:** migrate C07, C08, C09, C12, and C30 and remove duplicate
   artifact assertions. Rollback point: the collection boundary scan reports
   zero direct real-Vault references and zero whole-control copies.
6. **Retirement:** delete only the files whose responsibilities already pass at
   their new owner. Rollback point: responsibility matrix contains no orphan.
7. **Invariance and budget:** add mutation experiments, run a warm-up and two
   measured suites, and optimize shared fixture setup without weakening cases.
8. **Closure:** run canonical acceptance, optional read-only deployment checks
   only when separately requested, update state and compact indexes, and audit
   both Git roots.

If a stage fails, keep its edits visible, record the exact failing command and
responsibility, and repair or revert only the bounded T01 edits. Never reset,
clean, regenerate, or overwrite either Git root to recover a green result.

## 6. Acceptance criteria

### Hermeticity and independence

- All four real Vault/runtime aliases are empty and nonwritable before pytest
  collection.
- Collected test source contains no direct real-Vault reference, real-profile
  snapshot, live diagnostic, or whole-control copy.
- Adding, deleting, or editing an ordinary temporary note does not change the
  KnowledgeOS result.
- Adding an unrelated plugin, Core flag, key, record, version, order, or UI
  preference does not fail or degrade an owned capability.
- A safety-critical owned mutation still fails closed with a stable code.

### Responsibility and size

- Every deletion has a surviving owner or an accepted retired contract.
- Unique safety responsibilities named in the matrix remain covered.
- The final suite contains at most 57 `test_*.py` files and at most 11,750
  Python lines under `ops/tests`.
- The final handoff reports test-file, function, collected-case, direct-coupling,
  whole-copy, and line totals.

### Runtime

- After one warm-up, two consecutive `make test` runs each complete within 75
  seconds and average at most 72 seconds on the canonical environment.
- `make test-invariance` completes within 15 seconds.
- No test exceeds two seconds without a recorded integration justification.

### Canonical checks

Run sequentially and report only observed results:

1. `make source-check`
2. `make verify`
3. `make blueprint-check`
4. `make schema-check`
5. `make container-source-check`
6. `make container-verify`
7. focused runner, foundation, artifact, plugin, coupled-test, and invariance
   Make runs
8. one unmeasured `make test` warm-up
9. two measured `make test` runs
10. `make lint`
11. `/usr/bin/python3 scripts/validate_state.py PROJECT_STATE.md`
12. `git diff --check`
13. `git -C KnowledgeHub diff --check`
14. control and KnowledgeHub status inventories before and after verification

`make vault-artifact-check`, `make profile-check`, and `make live-smoke` are not
closure prerequisites. Run them only when their separate deployment or live
effects are explicitly requested, and never convert them into default evidence.

## 7. Exclusions

- Do not edit, normalize, regenerate, delete, or clean user-owned KnowledgeHub
  content or profiles during T01 implementation.
- Do not install or operate Obsidian, plugins, GUI, devices, host services,
  providers, connectors, or remote systems.
- Do not execute a live smoke without exact authorization.
- Do not commit, push, deploy, migrate, or perform remote Git effects.
- Do not weaken review, path, schema, privacy, transaction, recovery,
  authorization, or digest controls to meet size or duration budgets.

## 8. Planning sources

- `MAP.md`
- `BASELINE.md`
- `RESPONSIBILITY_MATRIX.md`
- `tickets/define-the-hermetic-regression-boundary.md`
- `tickets/choose-the-system-artifact-ownership-model.md`
- `tickets/define-open-world-plugin-invariants.md`
- `tickets/design-the-ephemeral-smoke-lifecycle.md`
- `tickets/retire-overlapping-and-historical-test-contracts.md`
- `tickets/set-verification-tiers-and-the-regression-budget.md`
- `prototypes/ephemeral-smoke-lifecycle-v1.md`

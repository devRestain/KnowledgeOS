---
title: T01 Test Responsibility and Retirement Matrix
status: accepted
updated: 2026-09-27
scope: implementation actions for the current ops/tests suite
---

# T01 Test Responsibility and Retirement Matrix

> [!note] Purpose
> This matrix assigns every current test file an implementation action. It does
> not authorize Vault mutation. All replacement tests and helpers remain under
> `ops/tests` and run only through repository-root `make` targets.

## 1. Current inventory

- Current implementation files: 67 `ops/tests/test_*.py` files.
- Current source size: 13,143 lines across those files.
- Current collected behavior: 352 test functions and 431 parametrized cases.
- Current direct real-Vault coupling: 16 files.
- Current canonical runtime: 96.02 seconds for 431 passing cases at the latest
  verified checkpoint.

These numbers are a baseline, not preservation targets. A test survives only
when it owns a distinct current responsibility.

## 2. Deletion gate

A test or assertion may be deleted only when all applicable steps are recorded:

1. Name its current responsibility and evidence class.
2. Map that responsibility to one surviving test, explicit non-pytest command,
   or an accepted retired product contract.
3. Preserve distinct fail-closed cases for path confinement, schema strictness,
   transactions, rollback, recovery, privacy, provider authorization, review,
   and digest binding.
4. Run the focused surviving test through `make test PYTEST_ARGS="..."` before
   deleting the old implementation.
5. Delete the old test only after the replacement detects the same unsafe
   condition or the accepted decision explicitly removes that condition.
6. Reject deletion justified only by age, capability label, file length,
   execution time, or current passing status.

Historical absence assertions do not survive merely to prove a migration once
happened. No-effect assertions do survive when they prove a current safety or
authorization boundary.

## 3. Keep as current distinct responsibilities

Keep the following files and their current behavioral responsibility. Local
fixture extraction and parametrization are allowed, but T01 does not delete
their unique positive and negative cases.

| Responsibility | Exact files | Reason to keep |
|---|---|---|
| Blueprint, note, YAML, toolchain, and runner trust | `test_blueprint.py`, `test_note_engine.py`, `test_yaml_safe.py`, `test_toolchain_contract.py`, `test_test_runner_contract.py` | Strict parsing, semantic mutation rejection, path safety, pinned toolchain, and Make-only execution are independent boundaries |
| Bridge, mobile, command, transaction, and recovery safety | `test_c10_bridge_contract.py`, `test_c11_mobile_offline.py`, `test_c13_commands.py`, `test_c14_transactions.py`, `test_c15_recovery.py`, `test_c16_reconcile.py`, `test_c17_bridge_publish.py` | Create-only behavior, committed-source binding, atomicity, rollback, quarantine, replay, and recovery are not deployment snapshots |
| Proposal and pipeline behavior | `test_c18_triage.py`, `test_c19_proposals.py`, `test_c21_projection.py`, `test_c22_retrieval.py`, `test_c23_answer.py`, `test_c24_background.py`, `test_c26_provenance.py`, `test_c27_proposals.py`, `test_c28_proposal_recovery.py`, `test_c29_ai_projection.py` | Proposal-only execution, privacy, citations, retrieval, projection identity, and background fail-closed behavior remain current |
| Provider, model, and thin-client boundaries | `test_c32_provider_broker.py`, `test_c33_ollama.py`, `test_c34_embedding.py`, `test_c35_gemma_routes.py`, `test_c36_provider_queue.py`, `test_c39_qwen_embedding.py`, `test_c40_local_routes.py`, `test_c41_thin_client.py`, `test_c42_safety_gates.py`, `test_c43_operations.py` | Network authorization, bounded I/O, model identity, proposal-only routing, safety gates, rollback, and tamper evidence remain distinct |
| Deployment and host adapter behavior in isolated fixtures | `test_d01_configure.py`, `test_e02.py`, `test_e02_launchers.py`, `test_e03_launchd.py`, `test_e03_log_retention.py`, `test_e05_frozen_promotion.py`, `test_e05_obsidian_thin_client.py`, `test_e05_thin_client_broker.py`, `test_f_obsidian_status.py` | Explicit activation, private storage, launcher boundaries, source-only client constraints, and bounded status probing remain current |
| Public command surface | `test_cli.py` | Parser ownership, dry-run routes, and intentionally absent writers are current API boundaries |

The `legacy` log bytes in `test_e03_log_retention.py` remain because their
bounded removal is an active privacy behavior. The one-shot thin-client parser
case remains while that route exists in production source; remove both source
and test together in a separately accepted product change.

## 4. Rewrite in place

| File | Keep | Remove or replace |
|---|---|---|
| `test_c07_templates.py` | Renderer bounds, schema validity, period edges, create-only bootstrap, conflict atomicity, and project bundle safety | Replace the helper that copies the real Vault with a minimal generated fixture. Replace deployed-template equality with source registry checks. Delete the retrospective whole-Vault placeholder-directory scan. |
| `test_c08_dashboard.py` | Base compiler semantics, frozen evaluator behavior, generated source semantics, note validation, and isolated bootstrap | Never read deployed Bases, root notes, dashboards, or CSS. Delete historical assertions naming removed Home views. Test root defaults in memory and system artifacts in a temporary Vault. |
| `test_c09_portable_fixture.py` | Frozen corpus integrity, materialization, deterministic mtime, validation, and negative fixtures | Delete `test_c08_surface_remains_blueprint_exact_inside_the_c09_gate`; C08 owns compiler output. Seed the smoke fixture from generators only. |
| `test_c12_diagnostics.py` | Exit classes, strict config, note validation, Git-state classification, and serialized audit behavior | Rewrite the current-workspace doctor case around a minimal temporary control/Vault tree. Do not read the real root sentinel or profile. Apply open-world plugin states. |
| `test_c30_diagnostics.py` | Local-model policy, state-dimension separation, drift, and JSON serialization | Run doctor cases against a synthesized temporary deployment. Delete the local-model generated-artifact ownership assertion; schema export owns exact bytes. |
| `test_schema_export.py` | Deterministic control artifact generation, one-byte drift, ownership allowlist, CLI report, and schema semantics | Restrict fixtures and assertions to control-root artifacts. Move deployed `99_System` comparison and conflict behavior to `test_vault_artifact_check.py`. Remove bridge protocol deployed-copy equality. |
| `test_c20_pipeline_registry.py` | Registry completeness, dispatch readiness, CLI routing, duplicate rejection, and missing-artifact failure | Delete `test_c20_artifact_ownership_promotes_all_c20_outputs`; centralized schema-export tests own manifest parity. |
| `test_c31_provider_contract.py` | Strict executable schemas, private immutable envelopes, authorization, size, privacy, and serialization | Keep executable schema validation but remove checked-in byte equality. Delete `test_c31_schema_export_binds_the_blueprint_contract`. |
| `test_c38_generation_identity.py` | Shared canonical profile, strict schema semantics, identity binding, private replay, drift, and path confinement | Remove only the `export_schema_artifacts` parity portion from `test_c38_schema_is_strict_and_generated`; rename it to describe strict executable schema behavior. |
| `test_e01_vector.py` | Deterministic embedding/RRF, retrieval behavior, frozen evaluation, stale projection, and CLI routes | Delete `test_e01_schema_artifact_matches_executable_contract`; centralized schema-export tests own byte parity. |

`test_c34_embedding.py` and `test_c39_qwen_embedding.py` keep their schema
constant assertions because those assertions prove executable model dimension
and canonical-mutation boundaries, not checked-in artifact equality.

## 5. Consolidate plugin and profile tests

Delete these files after their owned cases migrate:

- `test_p01_plugin_registry.py`
- `test_p02_core_settings.py`
- `test_p03_quickadd_settings.py`
- `test_p04_templater_settings.py`
- `test_p05_tasks_settings.py`
- `test_p06_linter_settings.py`
- `test_p07_obsidian_git_settings.py`
- `test_p08_homepage_settings.py`
- `test_p09_breadcrumbs_settings.py`
- `test_p10_notebook_navigator_settings.py`
- `test_p11_note_toolbar_settings.py`
- `test_p12_meta_bind_settings.py`
- `test_f_contracts.py`

Replace them with exactly two implementation files:

| New file | Responsibility |
|---|---|
| `test_plugin_audit.py` | P01/P02 aggregate classification: missing profile, minimal required subset, additional unrelated community plugin, future Core flag, malformed owned inventory, manifest mismatch, and static-versus-device evidence |
| `test_plugin_invariants.py` | Parameterized P03-P12 required-capability, additive-invariance, and safety-critical cases using stable owned identifiers and minimal synthesized profiles |

Each P03-P12 lane retains three conceptual cases, expressed through shared
builders rather than whole-profile snapshots:

1. minimal required subset passes;
2. unrelated plugin, key, record, order, version, or presentation change also
   passes; and
3. one lane-specific safety-critical mutation fails with the expected code.

Additional user plugins are identifiers only; tests and production diagnostics
must not open their manifests or data files. P05 and P08 never read root Home or
other ordinary deployed notes. P04, P05, P06, P11, and P12 may use synthesized
`99_System` fixtures only where their accepted invariant requires a protected
system path.

## 6. Replace workspace and migration tests

| Current file | Final action | Surviving destination |
|---|---|---|
| `test_foundation.py` | Delete after replacement | Add `test_foundation_contract.py` with a minimal control-only positive fixture plus missing-file and symlink negative cases. `make verify` and `make container-verify` exercise the current checkout separately. |
| `test_vault_structure.py` | Delete all four tests | Retire historical path/profile absence assertions. Move named `99_System` path and symlink safety to `test_vault_artifact_check.py`; keep Vault identity/path escape safety in `test_d01_configure.py` and `test_note_engine.py`. Do not retain ordinary directory or structural-marker inventory as regression truth. |
| `test_f_contracts.py` | Delete after plugin migration | P10 owns the stable period mapping; P11 owns reviewed toolbar actions. UI placement and complete serialized inventories become advisory or user-owned. |

Add `test_vault_artifact_check.py` to test the explicit checker entirely against
a temporary Vault. It covers the named `99_System` allowlist, parity success,
missing and mismatched outputs, symlink rejection, read-only behavior, and hard
rejection of `Home.md`, `Mobile.md`, profiles, `.vault-bridge`, and arbitrary
ordinary documents.

Add `test_hermetic_invariance.py` for the ordinary-note, unrelated-plugin,
unknown-key, ordering, advisory-version, future-Core-flag, and disposable-alias
mutations required by `make test-invariance`. It uses only synthesized trees.

## 7. Shared test support

Create test-only support beneath `ops/tests/support`:

- `control_factory.py` copies only declared control inputs into `tmp_path` and
  can generate an empty isolated `KnowledgeHub` and private `runtime`.
- `plugin_factory.py` builds minimal P01-P12 profiles from explicit test data;
  it never reads the real profile.
- `snapshots.py` records bounded file/digest snapshots for no-effect assertions
  without recursively copying an undeclared repository tree.

Extend `conftest.py` with the accepted hermetic path/mount guard and collection
boundary scan. Extend `test_test_runner_contract.py` with focused unit tests for
both guards. Do not add a live-Vault fixture.

## 8. Expected post-migration shape

- Remove 15 current files: twelve P files, `test_f_contracts.py`,
  `test_foundation.py`, and `test_vault_structure.py`.
- Add five focused files: `test_plugin_audit.py`,
  `test_plugin_invariants.py`, `test_foundation_contract.py`, and
  `test_vault_artifact_check.py`, plus `test_hermetic_invariance.py`.
- Retain 57 `test_*.py` files before any further purely mechanical grouping.
- Eliminate all 16 direct real-Vault test dependencies.
- Eliminate whole-profile snapshots and exact plugin-count expectations.
- Keep all test code, fixtures, and support beneath `ops/tests`.

The final case count is intentionally not fixed here. The verification-tier
ticket sets collection, wall-clock, and invariance budgets after this
responsibility-preserving consolidation is accepted.

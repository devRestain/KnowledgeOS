---
title: T01 Test Architecture Baseline
status: observed
updated: 2026-09-26
scope: static and canonical-container baseline before T01 implementation
---

# T01 Test Architecture Baseline

> [!note] Evidence boundary
> This document records observed starting facts for the decision map. It is not
> machine state, does not resolve any Wayfinder ticket, and does not authorize
> implementation.

## 1. Executed baseline

| Check | Observed result | Evidence class |
|---|---|---|
| `make source-check` | Passed all Blueprint trust-anchor checks | Static |
| `make verify` | Passed foundation paths and independent Git-root checks | Runtime |
| `make test` | `430 passed in 88.95s` using Python 3.12.8 and pytest 8.4.2 | Runtime |

The passing result does not prove Vault independence. It proves that the suite
currently agrees with the mutable profile and deployed Vault bytes observed at
the time of the run.

The suite currently contains 66 `test_*.py` files and 13,132 lines of test
source, excluding fixtures.

## 2. What counts as coupling

For this audit, a test is directly coupled when it derives the real repository
root from `Path(__file__)` and then reads or copies `CONTROL_ROOT/KnowledgeHub`.
Creating `tmp_path/control/KnowledgeHub` is not coupling: that is an isolated
test fixture even though it uses the production Vault name.

Sixteen test files currently contain direct real-Vault references:

| Responsibility | Directly coupled tests | Observed dependency | Planning direction |
|---|---|---|---|
| Template deployment | `test_c07_templates.py` | Reads exact live `99_System/Templates` bytes, copies the whole live Vault, and scans all live paths for historical placeholder names | Keep renderer and isolated bootstrap behavior; decide whether deployed `99_System` parity moves to an explicit artifact gate |
| Dashboard deployment | `test_c08_dashboard.py` | Reads exact live Bases, `Home.md`, `Mobile.md`, dashboards, and CSS | Keep pure compiler/evaluator and isolated bootstrap behavior; remove default regression dependence on user-facing root notes |
| Portable fixture parity | `test_c09_portable_fixture.py` | Compares fixture declarations with live generated Base files | Compare against compiler output or an isolated materialization instead |
| Diagnostics immutability | `test_c12_diagnostics.py` | Reads the live root sentinel before and after `doctor` | Exercise immutability in a temporary control/Vault fixture |
| Serialized GUI contract | `test_f_contracts.py` | Reads live Notebook Navigator and Note Toolbar JSON | Delete or absorb into isolated minimal-invariant tests after the plugin decision |
| Plugin setting snapshots | `test_p03_quickadd_settings.py` through `test_p12_meta_bind_settings.py` | First test in each file reads the live Mac plugin profile and asserts a broad serialized state | Replace live-profile snapshots with small fixtures for required keys, safety rules, and extension tolerance |
| Vault retrospective structure | `test_vault_structure.py` | Requires exact live paths and asserts historical paths and profiles remain absent | Retire legacy absence assertions; keep only system-owned bootstrap or boundary invariants in isolation |

## 3. Generated-artifact coupling outside pytest

`make schema-check` also compares deployed copies in the real Vault. The
current generated-artifact manifest names three KnowledgeHub paths:

- `KnowledgeHub/99_System/Schemas/Property_Dictionary.md`
- `KnowledgeHub/.vault-bridge/protocol/request.schema.json`
- `KnowledgeHub/.vault-bridge/protocol/response.schema.json`

Only the Property Dictionary is under the user-approved `99_System` document
boundary. The hidden bridge protocol files are machine protocol artifacts, not
ordinary notes, but their default-gate ownership still needs an explicit T01
decision.

## 4. Open-world plugin defect

The current plugin audit computes success only when all Blueprint-declared
plugins are installed **and no additional plugin IDs are present**. An unrelated
community plugin therefore changes the whole profile to `DEGRADED` even when all
KnowledgeOS-required capabilities remain intact.

This behavior is implemented in `vaultops.diagnostics._plugin_audit`, where
`unexpected_community_plugins` participates in `filesystem_pass`. The T01
contract must distinguish:

- missing or invalid required capability;
- unsafe value in a KnowledgeOS-owned setting;
- additional user-owned plugin;
- additional unrelated key in a plugin data file; and
- static serialized evidence versus actual device execution.

## 5. Test responsibilities worth preserving

The audit does not propose deleting tests merely because they are numerous.
High-value responsibilities include:

- path confinement and create-only writes;
- transaction atomicity, rollback, recovery, and digest binding;
- strict schema and YAML parsing;
- provider authorization, bounded I/O, and proposal-only behavior;
- deterministic generators evaluated against in-memory or temporary fixtures;
- negative cases that prove unsafe input fails closed;
- isolated bootstrap, migration, and idempotence checks; and
- explicit separation of static, semantic, runtime, artifact, deployment,
  external-service, and device evidence.

## 6. Likely overlap to decide, not yet delete

- Generator source tests, exact deployed-copy tests, bootstrap materialization
  tests, and schema-export parity can prove the same byte equality at multiple
  layers.
- `test_f_contracts.py` overlaps the Notebook Navigator and Note Toolbar P-lane
  registries while adding live-profile dependence.
- Many P03-P12 files repeat the same three-test shape: current live snapshot,
  missing profile, and unsafe fixture. The shared structure may be consolidated
  while retaining plugin-specific safety assertions.
- `test_vault_structure.py` includes retrospective absence rules that describe a
  past migration rather than a current KnowledgeOS responsibility.
- Large historical C/E files may contain valid orthogonal cases; they require a
  responsibility matrix before any deletion. File size alone is not a deletion
  criterion.

## 7. Required invariance experiments for implementation acceptance

The final implementation should demonstrate, in an isolated disposable Vault,
that the default regression result is unchanged when each unrelated mutation is
introduced independently:

1. add one ordinary note;
2. delete one ordinary note;
3. edit an ordinary note body and frontmatter;
4. add an unrelated community plugin ID and benign plugin directory;
5. add unrelated keys to a plugin data file;
6. change a nonessential user preference; and
7. leave pre-existing dirty files in the real KnowledgeHub.

The experiment must not edit the real KnowledgeHub. Live/runtime/device smoke,
if retained, belongs to a separate explicitly invoked tier with guaranteed
temporary-note cleanup.

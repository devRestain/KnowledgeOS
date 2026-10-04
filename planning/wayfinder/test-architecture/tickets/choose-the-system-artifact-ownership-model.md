---
title: Choose the system-artifact ownership model
status: resolved
assignee: codex
claimed: 2026-09-26
resolved: 2026-09-26
labels:
  - wayfinder:grilling
  - T01
parent: ../MAP.md
blocks:
  - Set verification tiers and the regression budget
---

# Choose the system-artifact ownership model

## Question

Which generated outputs remain exact KnowledgeOS-owned contracts, where should
their expected bytes live, and which command—default regression, explicit
artifact reconciliation, or bootstrap verification—may compare those bytes
with a deployed copy?

The answer must distinguish user-facing root notes such as `Home.md` and
`Mobile.md`, system documents under `99_System`, hidden bridge protocol files,
control-repository schemas and policies, and create-only bootstrap defaults. It
must prevent a normal user edit from becoming an unrelated regression failure.

## Findings

- `ops/config/generated-artifacts.yaml` currently mixes control-repository
  artifacts with three deployed Vault copies. Consequently `make schema-check`
  reads the real Property Dictionary and both `.vault-bridge/protocol` schemas.
- The bridge publisher validates requests with
  `ops/schemas/bridge-request.schema.json`; production code does not consume the
  deployed protocol copies. Their byte equality is therefore duplication, not
  a runtime safety boundary.
- `base_dashboard.dashboard_sources()` combines user-facing `Home.md` and
  `Mobile.md` defaults with system-owned dashboard and CSS outputs. Current
  tests turn that generator grouping into deployed-copy ownership for every
  path even though the root notes are user surfaces.
- `bootstrap()` is create-only, but it treats a changed existing generated
  surface as a whole-operation conflict. Its isolated create-only behavior is
  valuable; equality of an already deployed user surface is not a regression
  responsibility.
- Both foundation implementations require ordinary Vault namespaces, root
  notes, structural markers, transport directories, and deployed system files.
  This makes source and container foundation evidence depend on one mutable
  KnowledgeHub checkout.
- The user explicitly permits exact document ownership only beneath
  `99_System` and requires ordinary notes and profile customization to remain
  independent from KnowledgeOS regression.

## Resolution

Adopt four disjoint ownership classes. A path belongs to one class only; a
generator producing a default does not by itself own the deployed copy.

| Class | Surfaces | Canonical contract | Allowed verification |
|---|---|---|---|
| `control_exact` | Blueprint sources plus generated `ops/actions`, `ops/config`, `ops/expected`, `ops/launchd`, `ops/policies`, `ops/prompts`, and `ops/schemas` | Exact deterministic bytes in the control Git root | `make schema-check` and hermetic pytest may compare bytes without mounting the real Vault |
| `system_deployed` | Explicit allowlisted files below `../../../../../../Vaults/KnowledgeHub/99_System` | Generator output and path allowlist remain in the control root; the deployed copy is a replaceable projection | Only an explicit read-only `make vault-artifact-check` may compare deployed bytes |
| `user_overlay` | `Home.md`, `Mobile.md`, ordinary notes, assets, empty namespaces, structural markers, and every `.obsidian*` profile | Path safety and create-only default behavior, never exact deployed bytes or total inventory | Hermetic tests use synthesized temporary Vaults; an explicit diagnostic may inspect only accepted required subsets |
| `deployment_identity` | `.knowledgeos-root.json`, `.vault-bridge`, runtime layout, Git identity, services, and device state | Minimal identity, protocol, path-confinement, and authorization invariants | Explicit deployment, runtime, or live commands only; never default pytest or control artifact parity |

### Command boundary

1. Keep `make test`, `make verify`, `make container-verify`,
   `make schema-check`, and `make contract-check` independent from the real
   KnowledgeHub. `make verify` becomes a control-foundation check; its container
   form proves the same responsibility.
2. Keep `make schema-check` exact, but restrict its target set to
   `control_exact`. It must not traverse `KnowledgeHub`.
3. Add `make vault-artifact-check` as the sole exact deployed-copy comparison.
   It is read-only, separately invoked, and rejects every requested document
   path outside `99_System` before reading it.
4. Keep any writer separate from the check. A future
   `make vault-artifact-apply` requires explicit Vault-mutation authorization,
   reports create/update/conflict per path, and never runs transitively from a
   test or verification target.
5. Keep serialized plugin/profile diagnostics and live smoke on their own
   explicit targets. Neither may be a prerequisite of artifact reconciliation.

### Surface decisions

- Retain every deterministic `ops/**` output as an exact control-owned
  artifact. `ops/expected/Property_Dictionary.md` remains the expected Property
  Dictionary source.
- Register the deployed Property Dictionary, templates, Bases, dashboards,
  QuickAdd scripts, and system CSS in one explicit `99_System` deployment
  allowlist. The allowlist owns only named files, never all descendants.
- Split `dashboard_sources()` conceptually into user defaults (`Home.md` and
  `Mobile.md`) and `99_System` outputs. Keep both outputs available to
  create-only bootstrap, but limit document-structure assertions and validation
  to the system-owned outputs. The user clarified on 2026-09-27 that root-note
  and ordinary-document structure is not a regression contract; this supersedes
  the earlier wording to test both renderers in memory. Permit exact deployed
  comparison only for the `99_System` outputs.
- Remove `../../../../../../Vaults/KnowledgeHub/.vault-bridge/protocol/request.schema.json` and
  `response.schema.json` from generated-artifact ownership. Keep the canonical
  schemas under `ops/schemas`; bridge operations already consume the control
  request schema. Existing deployed copies are preserved as unmanaged
  compatibility residue until a separately authorized cleanup, not deleted or
  updated by T01.
- Treat `.knowledgeos-root.json` as deployment identity rather than generated
  content. Validate only its required identity fields, exact target root, and
  authorization binding in explicit deployment commands.
- Treat root Home and Mobile sources, ordinary notes, profile files, directory
  presence, and structural markers as mutable overlay state. A missing or
  changed deployed copy cannot fail default regression.
- Preserve create-only bootstrap behavior in disposable fixtures. Bootstrap
  may propose defaults for an absent path, but an existing user-overlay path is
  preserved and reported rather than compared as regression truth.

## Implementation consequences

1. Split `ops/src/vaultops/foundation.py` and `ops/check-foundation.sh` into a
   control-only foundation contract and an explicit deployment audit. Remove
   ordinary Vault topology, marker, root-note, and transport checks from the
   default foundation path.
2. Restrict `ops/src/vaultops/schema_export.py` and
   `ops/config/generated-artifacts.yaml` to control-root outputs. Introduce a
   separate `99_System` deployment allowlist and read-only checker.
3. Stop generating or checking deployed bridge protocol schema copies. Keep
   runtime request validation bound to the control schema.
4. Separate root-note defaults from system outputs in
   `ops/src/vaultops/base_dashboard.py`; preserve the create-only bootstrap seam
   in `ops/src/vaultops/bootstrap.py`.
5. Rewrite `test_schema_export.py`, `test_c07_templates.py`,
   `test_c08_dashboard.py`, `test_c09_portable_fixture.py`,
   `test_foundation.py`, and `test_vault_structure.py` according to the
   retirement matrix. No default test may read a deployed copy.
6. Prove that `make test`, control foundation, and control schema checks return
   the same result with an empty masked Vault and with arbitrary ordinary-note
   and profile changes in a disposable Vault.

## Consequences

- A root note edit, note creation or deletion, profile change, new community
  plugin, empty-directory change, or stale bridge protocol copy cannot fail
  canonical regression or control checks.
- Exact deployment drift remains observable for named `99_System` artifacts,
  but only when the operator asks for that evidence.
- The Vault remains an independently usable overlay: KnowledgeOS supplies
  deterministic contracts and optional projections without making mutable
  user content an input to its own regression.
- The verification-tier ticket can now assign the explicit artifact command a
  non-default evidence class and budget.

## Evidence

- `ops/config/generated-artifacts.yaml` and
  `ops/src/vaultops/schema_export.py` — observed the mixed control/deployed
  ownership list and three real-Vault byte comparisons.
- `ops/src/vaultops/bridge_publish.py` and
  `ops/src/vaultops/bridge_contract.py` — observed runtime validation against
  the control request schema and unused deployed protocol copies.
- `ops/src/vaultops/base_dashboard.py` and `ops/src/vaultops/bootstrap.py` —
  observed combined root/system sources and create-only default materialization.
- `ops/src/vaultops/foundation.py` and `ops/check-foundation.sh` — observed
  default source/foundation dependence on mutable Vault topology and content.
- `ops/tests/test_schema_export.py`, `test_c07_templates.py`,
  `test_c08_dashboard.py`, `test_c09_portable_fixture.py`,
  `test_foundation.py`, and `test_vault_structure.py` — observed the duplicate
  and deployed-copy assertions assigned to the implementation consequences.

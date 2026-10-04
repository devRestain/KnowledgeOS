# KnowledgeOS C12 preparation

## Accepted objective

Complete the Operation-owned KnowledgeOS structure and the provider-free retrieve -> normalize proposal -> review path against static Core 0.16.1. Stop at preparation readiness; retain CORE_C12_OPERATION_ADOPTION as the next separately scoped adoption lane. PROJECT_STATE.md is the sole progress authority.

## Implementation increments

- K12P1: preserve the dirty baseline; introduce independent Core, control, Vault, State and Runtime roots through strict configuration v3; classify all root consumers and retain old data untouched.
- K12P2: compose domain, application, adapters and interfaces; implement exact Core pins, graphless identity/manifest, owner policy, durable admission, CAS journal, human targets, evaluation, observation, recovery and local Experience outbox.
- K12P3: connect the existing vaultctl and four vaultmcp names to owner admission; exercise the complete provider-free path, denials, concurrency, crash/restart and independent-root cases; batch canonical checks and publish the adoption handoff.

## Contracts and ownership

- Read Core catalog, schemas and manifest as static inputs; never import development/reference code from Tmp.
- Pin Core/schema 0.16.1, Capsule 2.0.0, semantic 0.6.0 and ResourceBinding/HostBinding API 0.3.0 with exact content digests.
- Keep Blueprint note/property/relation registries authoritative. Preserve ontology authoring without runtime ontology adoption.
- Use domain for domain calculation; application for admission, policy, workflow, evaluation and recovery; adapters for concrete storage/host resources; interfaces for CLI/MCP presentation.
- Use private config v3 with core_root, control_root, vault_root, state_root and runtime_root. Reject old configuration and incompatible State before writes; never perform implicit translation or nested-root fallback.
- Retain admission, approvals, receipts, unresolved outcomes, continuation and active pins in State. Retain reconstructable index bytes/cache/process artifacts in host Runtime and durable knowledge in the independent Vault.
- Keep one owner control journal. Use exact previous-byte CAS, atomic replacement, fsync and cooperating-writer fencing. Apply Core retention only to contracted control history and keep unresolved markers.
- Authenticate through trusted startup dependencies. Never accept caller-selected physical roots, actor authority or foreign resource access.
- Commit receiver admission before acknowledgement; replay identical requests and reject changed effective content.
- Bind human decisions to exact source/proposal/policy/schema/semantic targets. Approval records intent; canonical apply remains explicit and separately admitted.
- Report proposal artifact writes separately from canonical note writes, execution outcome, evaluated acceptance and human approval.
- Keep recovery observational and reconciliatory; never blindly retry unknown effects. Keep Experience candidates/outbox local with transport unconfigured.

## Interfaces

Retain vaultctl, vaultmcp and knowledge_search, knowledge_retrieve, proposal_create and proposal_inspect. Replace public source/proposal path authority with Core ResourceReference identities. Keep MCP without approval/apply/provider activation. Add vaultctl operation check/status/recover.

## Acceptance and verification

Assemble the implementation before running the final focused, full-test and lint bundle. Preserve unique safety responsibilities while replacing obsolete legacy-layout/parity tests. Use the existing proven project image and dependencies, network-disabled read-only source/Core mounts, and disposable Vault/State/Runtime roots. Add make core-readiness-check and include it in make acceptance. Validate machine state in the project container and inspect both Git roots before handoff.

Required cases: deterministic retrieval/proposal/review; digest drift; denied pins, owners, paths and authority; concurrent CLI/MCP and replay; interrupted intents and artifact-before-receipt recovery; stale writers; independent roots and Runtime loss; retention/capacity; separate ACK/execution/acceptance/approval status; no provider or external effects.

## Preserved boundaries

Preserve existing user source changes, real Vault content and private Runtime records. Do not create or migrate the actual canonical State destination, convert old data, install/activate providers or Hermes, operate apps/devices, send external messages, publish, commit, or claim a second host/cross-OS result. Record actual C12 binding requirements, data inventory method, writer cutover and rollback steps and unrun gates in the final handoff.

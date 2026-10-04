# KnowledgeOS graphless Capsule responsibilities

This map defines the selected preparation slice. `PROJECT_STATE.md` owns progress and acceptance. Core remains a read-only static input; all executable ports listed here belong to KnowledgeOS.

| Capsule responsibility | Maintained implementation | Owned inputs and records | Verification responsibility |
| --- | --- | --- | --- |
| Identity and manifest | `adapters/core.py`, `KnowledgeApplication.from_roots`, `ops/config/core-adoption.json` | `knowledgeos`, explicit fabric/instance identity, Capsule 2.0.0 and exact Core/semantic pins; `operation check` emits the validated graphless manifest | Reject foreign identity, changed release bytes and incorrect pins before State writes; factory composition creates no processes |
| Domain | `domain/normalization.py`, existing retrieval, note engine, proposal calculation | Blueprint note/property/relation registries remain authoritative; `Ontology/` references them | Retrieve the expected synthetic typed note; preserve Properties and body meanings; produce deterministic proposal identity and bytes |
| Control and evaluation | `application/knowledge.py`, injected handlers/evaluator, `adapters/owner_journal.py` | Owner intents, receipts, exact checkpoints, criterion partitions and human decisions | Execution completion cannot establish acceptance; mismatched or unrun criteria cannot establish a passing proposal |
| Gateway | `application/gateway.py`, injected `GatewayIdentity` | Owner policy, Core eligibility, exact payload bytes, semantic profile, expiry and complete prior correlation records | Persist admission before ACK; replay exact requests; conflict on changed effective content; validate the prior request and authenticated sender before admitting a response |
| Policy | `ops/policies/owner-control.json`, existing privacy/retrieval/action policies | Trusted local operator/MCP roles and Gateway actions; conservative source privacy | Reject caller authority, foreign resources, disallowed actions, traversal and symlinks before domain/effect dispatch |
| State and evidence | `adapters/owner_journal.py`, `adapters/artifacts.py`, linked transaction journals | State v3 pins, frozen Pending bytes, decisions, receipts, outbox, unresolved outcomes and generation fences | Exact previous-byte comparison, atomic replacement, fsync and shared writer exclusion; incompatible State fails before lock creation; Runtime loss preserves owner evidence |
| Lifecycle | `KnowledgeApplication.recover`, explicit `apply_proposal` | Pending/unknown/completed effect observations and exact artifact evidence | Reconcile observed bytes without dispatch; cancel an undispatched absent artifact; preserve uncertainty for ambiguous effects; apply requires a separate owner decision and explicit dispatch |
| Experience | `KnowledgeApplication._experience` | Bounded Core `ExperienceCandidate` and owner `ExperienceOutboxEntry`; transport unconfigured | Record selected recovery observations; bound history and local outbox capacity; perform no delivery |
| Human observability | `KnowledgeApplication.status`, CLI operation commands, MCP v2 results | Core `HealthReport`, `StatusSnapshot`, execution, acceptance, freshness, completeness, human decision and effect facts | Keep ACK, execution, domain acceptance and approval distinct; retain unknown for unavailable evidence; separate Pending artifact creation from canonical note changes |

The profile is `graphless`. Graph assignment, scheduler dispatch, provider activation, Host adoption and external transport are separate C12 acceptance targets. The readiness slice does not require them to execute.

## Internal dependency direction

- `interfaces` validates and presents CLI/MCP inputs, then enters the application boundary.
- `application` owns admission, policy, sequencing, evaluation, human decisions and reconciliation.
- `domain` reuses existing Blueprint-based calculations and validators without owner authority.
- `adapters` resolves trusted roots, reads frozen Core inputs and performs admitted filesystem effects.
- Existing maintained workflows enter `KnowledgeApplication.run_legacy` from CLI before writer execution. Their transaction journals link to the owner intent through `owner_intent_id`; historical approval files do not authorize the selected owner apply path.
- Production imports never reach `Tmp/CoreDevelopment` or `Tmp/ReferenceCapsule`.

## Context handoffs

| Increment | Inputs to reload | Reviewable output | Next context boundary |
| --- | --- | --- | --- |
| K12P1 | Baseline archive and inventories, current root and Operation guidance, exact Core pins | Strict config v3, five independent roots, State/Runtime consumer ownership | Compose explicit owner ports without binding real private data |
| K12P2 | K12P1 roots, Core schemas/catalog, Blueprint registries, owner policy | Graphless manifest factory, owner journal, Gateway, human targets, evaluation, status, recovery and local outbox | Connect the selected provider-free interfaces and assemble final verification |
| K12P3 | The responsibility map, all control source and focused scenarios | Logical-reference MCP v2, retained CLI selectors converted by owner control, readiness target and canonical verification evidence | Use `C12_HANDOFF.md` to select exact real adoption targets and effects |

Each context reads the authoritative `PROJECT_STATE.md`; this table defines handoff contents and does not duplicate machine progress.

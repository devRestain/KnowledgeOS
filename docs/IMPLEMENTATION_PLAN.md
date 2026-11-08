# KnowledgeOS execution boundary and autonomous Team plan

`PROJECT_STATE.md` owns current progress, checks, blockers, and handoff. This file describes the current implementation boundary and the adopted autonomous Team ticket plan. Planned behavior below is not evidence of source completion or runtime activation.

## Current source

- Select Core and semantic pins from `ops/config/core-adoption.json`.
- Current source selects Core 0.20.1 / semantic 0.7.1. Ordinary owner composition rejects differently pinned retained State. `adapters/core_cutover.py` and `vaultctl storage core-cutover` support exact-preimage preview and separately authorized transition of supported quiescent envelopes to the validated target Core root. Archive preservation, owner-writer reinspection and generation/revision fencing are required; retained Work and unresolved effects stop this maintenance lane. Source support grants no live apply authorization or reinterpretation of historical evidence. See [State adoption](RUNTIME.md#source-adoption-and-retained-owner-state).
- Keep the five private roots separate and the KnowledgeHub Vault independent.
- Keep human writing and review in Obsidian, agent evidence and proposals in one `vaultmcp` v3, and `vaultctl` restricted to maintenance.
- Keep browser status and administration in `frontend/`, with no document search, creation, intake or review/apply UI; use the native Thin Client for those existing ExOps routes.
- Use the existing owner attention outbox and host `DiscordBotClient` with a distinct KnowledgeOS bot identity; keep real credentials, destination binding and activation separate from source alignment.
- Check exact Work, Team or Officer, method, Profile, Graph, source, and effect bindings in the owner on every MCP call.
- Keep proposal-based methods Pending-only with independent evaluation, recorded human decision and separate canonical apply. Owner-only provisional daily metadata follows its separate retained-delta review contract, not general apply authority.
- Team execution uses real model judgments from `director_agent.py`, `agent_graph.py` and `agent_assessment.py`; deterministic Owner validation never substitutes a satisfied semantic verdict. `agent-profiles.json`, COMMON and the 37 role SOULs define consumed prompts and explicit routes. The [profile guide](../../../Runtimes/README.md#모델-정책과-13개-home-정렬) links every complete prompt and its purpose, home, model and delegation boundary.

## Authoritative source indexes

`docs/SOURCE_CONTRACT.md` identifies Blueprint and generated-contract authority. Keep exact executable method, Team/role, owner-policy and MCP meanings in `ops/config/work-methods.json`, `ops/config/team-catalog.json`, `ops/policies/owner-control.json` and `ops/schemas/mcp/capabilities.json`; do not maintain another catalog here. Provider/model execution remains outside `vaultctl` and `vaultmcp`. Current checks, installation/profile/device observations and State retirement evidence belong in `PROJECT_STATE.md`; the runtime index retains bounded execution/recovery contracts and their evidence limits.

## Native source and separate adoption

Use `application/normalization_work.py` for one exact NormalizationOfficer WorkRun in disposable roots. The default path executes the deterministic Pending-only method. Its explicit model-bound mode delegates the three scoped MCP calls to `ops/clients/hermes/native_officer.py` under a fresh native Hermes process, then uses the same independent assessment and Owner HTTP export. Keep source, Work/Profile/Graph/runtime pins, real effect receipts and model execution evidence distinct. `make officer-smoke` and `make model-officer-smoke` exercise the respective modes without production mounts.

The bounded Curation Team supplies ordinary, committed-result recovery and criterion-linked continuation methods plus a separately selected quiescent control route with durable command receipt/application separation. CANCEL, one revision-bound PAUSE/RESUME pair and one owner-prepared/adopted history rollover preserve their exact checkpoints, logical identities and budgets. `make team-smoke`, `make team-recovery-smoke`, `make team-continuation-smoke`, `make team-control-smoke` and `make team-rollover-smoke` exercise distinct disposable boundaries, not interchangeable retry routes. [Runtime contracts](RUNTIME.md) retain the supported recovery/restore, lifecycle and attention limits; none implies in-flight control, combined crash recovery, host Cron, production activation or owner-backed Obsidian adoption.

The separately selected `knowledgeos.capture_steering_team` method supplies bounded native question/answer, declared future-context parameters, genuine partial-Graph CUTOVER and one Director-admitted labeled-report revision under [its runtime contract](RUNTIME.md#bounded-native-question-parameter-steering-and-partial-cutover). Its real Manager composes the immutable successor and independent EvalOfficer assesses exact partial/final evidence. This is not permission for arbitrary objective/delegation amendment, external-effect reconciliation or combined control/crash recovery. The controller owns journal-backed report export; common Work results retain exact JSON/reference/assessment without fabricated MCP artifact registration or apply/truth authority. Verification progress belongs in `PROJECT_STATE.md`.

Before selecting production activation, make each capability's binding explicit:

- **Owner and Director:** choose the unchanged read-only composition or the explicit private policy-selected `WritableOwnerRuntime` / `TrustedKnowledgeExOps` composition in `application/owner_runtime.py`, with cross-process `owner_authority.py` checks. `interfaces/writable_owner.py`, `interfaces/native_owner_driver.py` and `ops/compose.owner-writable.yaml` supply maintained installed serving and native stdio integration. Select exact existing roots, startup actor, image, policy, compatible method/Team/Runner registrations and actual service trust; `loopback_owner` alone still constructs no authority.
- **Metadata and daily:** `application/metadata_work.py` implements note-metadata, relation-ontology and daily candidate methods with method-specific native wrappers and shared `native_team.py` machinery. Template intake explicitly selects the two metadata admissions; registered native Profiles and fresh independent assessment remain required. Candidates preserve exact source/registry/value evidence and never establish uncertain relation truth. Source executors exist; their registration, provider availability, installed identity and real-data adoption are separate gates.
- **Research and authoring:** `research_work.py` supplies cited document-enhancement research; `authoring_work.py` supplies cited drafts and project/period review. Their native wrappers and `native_research.py` / `native_authoring.py` adapters enforce their own source scopes and tool surfaces. Research exports a cited report, drafts remain Pending-only, and review separates observations from interpretation and next actions. None gains canonical apply from a completed assessment.
- **Exchange and Maintenance:** `boundary_work.py` / `boundary_contract.py` and their native wrappers implement disclosure/provenance-bound adaptation and selected diagnostics/recovery planning. Exchange needs an actual owner-admitted authenticated correlated Gateway receipt, not foreign filesystem access or an implied external connection. Maintenance retains unexamined/unknown outcomes and produces a preimage-bound Pending plan, not repair execution. Exact selected registrations and separately authorized external effects remain prerequisites.
- **Daily admission and dispatch:** the selected note enters manual daily admission only; the serving supervisor never scans the Vault or creates scheduled daily Work. `DispatchSupervisor` connects durable pending Work to selected native routes, observes unknown starts without retrying them and consumes only independently completed manual daily candidates. `DailyAdmissionScheduler` retains explicit maintenance/test due-admission behavior, not an enabled serving schedule. Retained-result consumption reauthorizes its original Director grant before the marker and final provisional/no-evidence writers. A new registration never grandfathers old scope; no Cron is installed.
- **Scoped native tools:** the Officer adapter remains limited to its three tools. Maintained metadata, research, authoring and boundary adapters instead select their own method/Profile/stage tool surfaces and validate actual source reads, owner effects and independent artifacts. The stdio driver requires exact current private registration and adapter/interpreter/profile/provider identity; no generic Runner fallback or new public model authority is implied.
- **Models and durability:** choose a provider only where needed. Optional `--local-embedding ollama-qwen3-stdio` uses the ordered stdio bridge to fixed host `127.0.0.1:11434`, with exact Qwen identity/output checks and no new listener, host networking or download. Without a binding Vector/Hybrid remain unavailable; read-only Owner stays Lexical-only. Ordinary reads materialize current document vectors independently of maintenance publication. Explicit `--publish-index` needs private `publish_index` authorization at both pointer writers; a refused Lexical switch may leave an already committed Vector pointer, never permission for rollback or retry. This non-atomic maintenance export does not impose a whole-Vault freshness gate on live search. Follow the [runtime contract](RUNTIME.md#selected-local-ollama-embedding-binding) for matched installed code and separately authorized real-data activation; keep coordinated Vault/State/Temporal recovery and unknown-effect reconciliation distinct.

After separately approved backend activation, the user verifies the selected native Obsidian profile and actual plugin version, configures its local endpoint, reloads it, and checks active-note tracking. HTTP requires no login or manually entered token. The user then exercises Lexical search, explicitly enabled Vector/Hybrid and approved-edge Ontology search, two metadata Work receipts and current results, cited document-enhancement report, stale-note and unknown-delivery behavior, and separate human review/decision and canonical apply. Test only bound and separately authorized capabilities; report unavailable modes, blocked Work, empty corpus/edges, provider failures and missing device evidence instead of treating source or HTTP fixtures as native success.

The user performs Computer Use and native Obsidian verification. Report the required GUI checks and their backend prerequisites rather than opening the app, changing plugin settings, or treating an HTTP pass as device evidence.

For a structured request, replay returns the current two Work receipts. If a prior delivery is unknown, a new request for the same actor, template, and exact source is stopped before Director admission and checked again under the owner writer lock. The host adapter and owner can inspect an exact Temporal Workflow start and reconcile positive matching history; a native WorkRun must satisfy the same identity checks without retrying an uncertain start. The owner Work list marks receipts whose note hash has changed and rechecks that hash during status export, so the sidebar does not offer metadata preparation for stale Work.

## Proposal action contracts

Action schemas in `ops/actions/` and their prompts are owner method inputs. Their names do not create `vaultctl ai` routes or provider authority. The current MCP proposal tools and `ops/src/vaultops/application/mcp_effects.py` define the callable agent effects.

## Adopted autonomous Team target

The owner requested real model-based Director, Manager, specialist and independent EvalOfficer judgments for every planned Team method. The exact method and Team catalogs remain authoritative; the coverage inventory below prevents an accidental three-method-only delivery.

Use the existing development environment directly when practical. Do not add mandatory per-ticket environment isolation, whole-tree snapshots, compatibility shims, login/token ceremonies, redundant validation layers, or permanent infrastructure without a demonstrated need. Obsolete development implementations and records may be replaced within this cutover; unrelated assets remain outside scope.

Keep deterministic Owner authority and effect validation: admitted scope, explicit user assignments, exact artifact revisions, tool permissions, separate approval/apply and effect receipts. These checks validate model decisions; they must not manufacture the Director decision, Manager Graph or semantic assessment.

Keep all GUI actions outside this implementation. Update the existing Codex GUI handoff from verified backend behavior. Do not install Hermes Cron jobs or enable automatic Work scheduling; expose daily stewardship as a manual method.

### Model and prompt policy

- Remove DeepSeek from this task's implementation and orchestration policy, following the user's later revision.
- Use `gpt-6.1-sol` for difficult planning, Graph composition, semantic evaluation and complex specialist tasks; select supported effort by difficulty rather than applying maximum effort everywhere.
- Use the explicitly approved exact `gpt-6-luna` route for ordinary specialist work. The owner corrected the initial `gpt-6.1-luna` request after live account catalog inspection; do not create a silent alias for the old name.
- Use local Gemma 4 12B only for very light extraction, labeling or presentation tasks that do not decide authority or independently certify completion. The installed Ollama wire tag is `gemma4:12b`; the server advertises completion/tools/thinking support. Prove the selected thinking setting by an actual request before claiming runtime support.
- Record requested provider/model/effort, effective wire settings, returned model when supplied, role, session, prompt digest and actual tool/effect evidence. Do not label an inherited default as the selected model.
- Author role instructions as English `SOUL.md` files owned by KnowledgeOS. Keep one maintained source for each consumed role and install/reference it from the corresponding Hermes profile. Compose it with admitted method/source/tool context at invocation time; do not bypass SOUL in the native path.
- Give each Manager its Team-specific scope and decision criteria. Give specialists actual input/output and evidence obligations. Give EvalOfficer criterion-by-criterion outcomes and explicit unmet/not-run reasons, independent of the producer.

### Shared implementation contracts

Keep one integration owner for shared interfaces. Reuse the current callable Runner and Owner journal/ExOps boundaries instead of creating a second orchestration authority.

1. Pass responsibility, role, method, admitted Work/source/context, user pins, allowed Profiles/tools and exact prompt/model policy through the native invocation. Resolve the role's maintained SOUL before inference.
2. Return actual model/effort/session/prompt provenance, the structured decision or artifact result and actual tool receipts. Stop on provider refusal or uncertain new effects; never synthesize successful evidence.
3. Let Director produce the objective, declared criteria, target/allowed Profiles and proposed limits from the user's request. Let Owner assign trusted identities and validate/admit the proposal; replace fixed-factory re-creation checks that would reject every meaningful model decision.
4. Let Manager author the real Graph and select compatible teammates within the admitted envelope. Preserve required method stages and explicit pins while allowing useful optional stages/choices. Validate the proposed graph and execute that exact graph through the existing Temporal/LangGraph path; do not call a model and then discard its graph in favor of a hardcoded bundle.
5. Let independent EvalOfficer inspect exact source/evidence/artifact revisions and produce satisfied/unmet/not_run outcomes for every admitted criterion. Keep receipt/hash/scope checks separate. Feed genuine remaining work to the same Manager under admitted continuation limits; do not mark every structurally valid artifact semantically satisfied.
6. Resolve Team bindings in the method/Work context. Current single-binding-per-Team equality and fixed two-specialist assumptions must not prevent Curation's multiple methods or manual daily from sharing their dedicated Manager with different selected teammates.
7. Keep model/tool calls in recoverable Activities, immutable started segments, exact command receipt/application and the existing Owner effect boundary. Avoid arbitrary model-generated executable code or a second durable graph store.

### Tickets, dependencies and acceptance

Each implementation subagent owns its listed slice, investigates and edits it, and returns changed paths plus evidence needed for integration. Subagents do not run build/lint/tests/formatters mid-flight. The parent integrates shared files and runs checks once after each relevant wave. Progress/result states belong only in `PROJECT_STATE.md`.

| Ticket | Owner responsibility and target | Dependencies | Binary acceptance |
|---|---|---|---|
| A00 | Parent: exact provider/effort probes and coding-agent model selection | None | Confirm exact selected model routes and supported wire efforts; resolve Luna naming and the scout's exhausted Terra override without silent fallback. |
| A01 | Prompt/profile subagent: maintained role SOUL sources, catalog/profile references and role policy | A00 | Every invoked role loads its intended SOUL; all installed KnowledgeOS homes have explicit purpose/model/tool mode; recorded prompt digests match consumed bytes. |
| A02 | Native-runtime subagent with parent integration: `native_runtime.py`, native adapters and `native_owner_driver.py` | A00; shared invocation contract | Execute each selected provider with requested effective effort, no accidental Terra/DeepSeek, role SOUL consumption and real session/tool evidence. |
| A03 | Director subagent: `owner_runtime.py` admission, template/freeform/selected intake and admitted-spec validation callers | A01/A02 contract | A real model decision reaches the admitted WorkSpec; user pins and criteria survive admission; no hardcoded WorkSpec substitutes for it. |
| A04 | Manager/Graph subagent: `native_team.py`, `research_work.py`, `work_methods.py`, graph/assignment compilation and binding resolution | A01/A02 contract | Model-authored graph/teammate choices are Owner-admitted and the same graph executes; all required method stages and user pins are retained. |
| A05 | Evaluation subagent: native/research/curation assessment and continuation integration | A01/A02 contract | Independent model assessment covers every exact criterion/artifact; an unmet result remains unmet and produces real RemainingWork rather than automatic completion. |
| A06 | Method-family subagents: metadata/ontology, research, authoring/review, exchange/maintenance specialists and their native adapters | A02/A04/A05 interfaces | Every family's actual specialist role uses its SOUL/model policy and produces the expected receipt-bound result without granting apply authority. |
| A07 | Curation subagent: normalization, continuation and steering native routes | A03/A04/A05 interfaces | All three special Curation methods enter normal Owner admission/dispatch; continuation and question/CUTOVER paths use real agents and finish with independent assessment. |
| A08 | Owner/client integration subagent: all-method registrations, manual daily, method parameters, dispatch and Thin Client entry/result paths | A03–A07 | Every catalog method can be manually admitted from the intended client/Owner path, including daily; no Cron or automatic scheduler is enabled. |
| A09 | Parent/runtime adoption: matched images, installed prompts/profiles, selected policy, Owner/Worker and read-only MCP launcher | A01–A08 | Actual development Owner lists every manual method as usable with matching installed code/bindings; the cited-answer MCP initializes and its five reads execute. |
| A10 | Parent with method owners: full real-provider method matrix and regression integration | A09 | Every coverage row below completes real admission→Director→Manager graph→specialists→independent EvalOfficer→result read, with actual route/effort/tool evidence. |
| A11 | Parent: canonical acceptance, maintained contracts/state and Codex GUI handoff | A10 | `make -C Operations/KnowledgeOS acceptance` passes; installed behavior and current documents agree; GUI observations alone remain explicitly unrun. |

Run A01/A02 concurrently after A00. Run A03/A04/A05 against the agreed invocation contract in parallel with parent integration ownership. Run independent A06 method families and A07 after those shared interfaces are stable. A08 may develop client/manual-admission work concurrently against those interfaces; activate only after dependencies integrate. Sequence A09→A10→A11 as actual adoption/verification dependencies, not arbitrary serial work.

### Required method coverage

All IDs below carry the `knowledgeos.` prefix. The method catalog owns exact versions, stages and tool contracts.

| Method | Dedicated Team / Manager | Required actual acceptance path |
|---|---|---|
| capture_normalization_team | knowledge-curation / CurationManager | Read and classify a selected Capture, create one Pending normalization proposal, independently assess and read its result. |
| capture_normalization_continuation_team | knowledge-curation / CurationManager | Assess the initial segment, let Manager compose the criterion-linked successor, create Pending and independently assess the terminal result. |
| cited_draft_team | knowledge-curation / CurationManager | Read source evidence, author a cited draft, create Pending and independently assess the actual draft. |
| cited_research_team | evidence-research / ResearchManager | Retrieve/read admitted evidence, verify citations, synthesize a useful cited report and independently assess its claims and gaps. |
| relation_ontology_team | relation-ontology / OntologyManager | Read selected link/registry evidence, submit noncanonical relation candidates and independently assess meaning/evidence without auto-truth. |
| project_review_team | project-review / ReviewManager | Read the selected project/period/tasks, produce a Pending review with observations/interpretation/actions and independently assess it. |
| knowledge_exchange_team | knowledge-exchange / ExchangeManager | Read a genuinely Owner-admitted Gateway receipt, propose local adaptation with exact disclosure/provenance, independently assess and read the result. |
| note_metadata_team | corpus-stewardship / StewardshipManager | Read source and applicable property/type meaning, submit evidence-backed candidate fields/tags, independently assess and expose the Pending path. |
| daily_corpus_stewardship_team | corpus-stewardship / StewardshipManager | Manually admit a due source, assess eligible empty-field candidates and exercise the existing provisional/human-review result path without automatic scheduling. |
| capture_steering_team | knowledge-curation / CurationManager | Read evidence, obtain the required owner answer, apply supported steering/CUTOVER, let Manager compose the immutable successor and independently assess its report. |
| vault_maintenance_team | vault-maintenance / MaintenanceManager | Diagnose selected evidence, produce a concrete recovery-review plan, preserve uncertainty in the report and independently assess it without implicitly executing repair. |

### Verification and stop rules

Use project-owned commands and the existing development Owner/Temporal service; use representative development inputs without mandatory duplicated installations or whole-Vault freezing. Keep deterministic isolated regressions where they already catch real behavior; the user's isolation waiver does not turn shared mutable tests into safe full-suite tests.

Run baseline `make -C Operations/KnowledgeOS source-check verify` before implementation. Run focused smoke for a changed behavior, then canonical acceptance after an integrated wave. Record model/provider/effort/SOUL/Graph/assessment/artifact evidence separately from a passing suite and from installed or GUI observations.

Count one correction round as a parallel implementation batch plus one integrated verification. The Owner expanded the limit from 30 to at most 100 such rounds and instructed continuation. Do not count prerequisite metadata discovery as a completed implementation round. The expanded cap is not permission for blind retries, model fallback, new billing or GUI actions.

Stop the affected branch and ask the user when an exact required model/route/effort is unavailable, credentials are missing, quota/billing fails, a new payment is needed, a newly invoked external effect has unknown outcome, or a real design conflict needs owner judgment. Do not retry quota failures or replace unavailable models without permission. Continue only independent, already-authorized work. Keep the goal incomplete until every acceptance criterion is evidenced.

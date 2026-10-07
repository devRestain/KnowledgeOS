# Agent Architecture Contract

- Resolve Core, control, Vault, Operation State and host Runtime as five independent private config v3 roots.
- Keep the bound KnowledgeHub Vault as an independent Git repository and canonical Obsidian root.
- Keep State and Runtime untracked, mode `0700`, and outside every sync root.
- Use the exact Core selection in ops/config/core-adoption.json as read-only contracts, schemas and frozen manifest inputs; implement executable ports inside KnowledgeOS.
- Route selected CLI/MCP workflows through KnowledgeApplication, use one OwnerJournal and bind human decisions to exact source/proposal/policy/schema/semantic bytes.
- Separate admission ACK, execution, independent assessment, domain acceptance, human approval, and canonical apply. Admitted Team and Officer Work may use owner-validated immutable Graph segments.
- Keep the container as the canonical dependency and execution surface.
- Keep `00_Inbox/Captures` as the single capture lifecycle path.
- Keep fixed Vault namespaces, eight Base files, twenty views, sixteen templates, and eighteen note types aligned with Blueprint; `Compass.base` owns cross-type orientation signals.
- Keep Markdown body and flat YAML Properties as the canonical note representation.
- Keep title equal to filename stem, timezone-aware datetimes, quoted wikilinks, and filesystem mtime freshness.
- Keep project bundles under `20_Projects/<name>/` with `Working/` and `Artifacts/` siblings.
- Keep Obsidian document operations within the user's selected device and installed profile; a mobile sync or request route requires separate adoption evidence.
- Keep canonical directory markers exact and exclude runtime placeholder files.
- Keep ignored Obsidian baselines separate from app-smoke evidence.
- Keep agent proposals action-schema constrained, source and preimage bound, and Pending until owner review. Approval and canonical apply are distinct human-controlled effects.
- Keep privacy defaults conservative and exclude denied or local-only material from remote candidate sets.
- Keep confidential or institutionally restricted material outside this Vault until a separate boundary is approved.
- Keep Obsidian GUI as the human writing, navigation, search, review, and decision environment. Core Daily Notes, Notebook Navigator, Templater, QuickAdd, Bases, Backlinks, and Search retain their GUI jobs; `vaultctl note validate` checks existing note bytes.
- Keep general note, project, capture, and period creation out of the CLI. The internal template engine may support deterministic generated artifacts and fixtures, but it is not a second human entry point.
- Keep Home and Note Toolbar as the primary GUI navigation layer and Command Palette as fallback recovery. Toolbar actions must be explicit, reviewed, bounded, and free of arbitrary shell, script, network, Git, AI, or canonical-apply authority.
- Keep `vaultctl` for owner maintenance, index verification/build, note validation, receipts, repair, and exact storage transitions. User navigation and document operations stay in Obsidian; agent knowledge work uses the single `vaultmcp` v3.
- Keep the Mac Thin Client as an owner Work and review adapter: submit a digest-bound request to Director admission, display Work status and Pending evidence, and send human decision and separate apply requests to the owner. Keep retrieval, provider, index, Vault-write, Git, and canonical authority outside the plugin.
- Keep model execution and embedding outside the current KOS source boundary; any future model binding belongs to a separately admitted Runner.
- Review large-binary storage and Git LFS only after observed asset size and remote policy justify it.

## AgentFabric target model

- Use the adopted Core environment model in source: Operation canonical and durable control State, host reconstructable Runtime and shared service execution, independently governed Vault knowledge.
- Keep `States/Operations/knowledgeos/` as the detachable owner State root and `Runtimes/KnowledgeOS-runtime/` as reconstructable host Runtime. Retire predeployment archives only through an exact-reference, owner-locked transition; the current owner State has its own Core 0.18.1 pins and journal.
- Keep Operation routing/export policy, durable delivery intent, outcomes, and decisions when using a shared host transport.
- Assign one dedicated Manager to each KnowledgeOS Team: CurationManager, ResearchManager, OntologyManager, ReviewManager, ExchangeManager and MaintenanceManager. A Team exists only with its Manager identity and selects needed teammate calls per admitted Work; use docs/TEAMS_AND_ROADMAP.md for exact routes.
- Bind admitted Team work to its selected Manager through team_id and manager_executor_id, preserve that binding through immutable continuation, and require explicit Director amendment and owner validation for changes. A bounded Officer task has no Team or Manager binding. Reuse immutable teammate definitions across Teams and compatible Operations only through separate local authority bindings. Keep EvalOfficer independent from the selected Manager, teammates and producing Officer.
- Use `ops/config/core-adoption.json` and current Work, method, policy, and MCP contracts for implementation.

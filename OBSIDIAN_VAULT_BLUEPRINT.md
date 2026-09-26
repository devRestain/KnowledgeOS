# KnowledgeOS Blueprint — agent contract index

> Role: compact normative index for implementation agents. This file is not live status and not the human guide.
> Live status: `PROJECT_STATE.md`. Human explanation: `README.md`.

## Authority

- Contract id: `knowledgeos-blueprint-v2`.
- Precedence: this Markdown contract → `blueprint/blueprint.yaml` → `OBSIDIAN_VAULT_WHITEPAPER.md`.
- Machine validation: `blueprint/blueprint.schema.json`, `make blueprint-check`.
- Source/code contract: `docs/SOURCE_CONTRACT.md`; architecture/runtime/operations/mobile/decisions indexes live under `docs/`.
- Do not infer current implementation from this design index; compare `PROJECT_STATE.md`, live files, tests, and receipts.

## Scope and repositories

- Control root: `.`; track policies, schemas, source, tests, and agent docs in the control Git root.
- Vault root: `KnowledgeHub/`; independent Git root opened by Obsidian and Working Copy.
- Runtime root: `runtime/`; Git-untracked local queue, locks, receipts, journals, indexes, caches, and logs.
- Keep control, Vault, and runtime responsibilities separate; a receipt records both Git heads.
- Keep secrets, restricted data, provider state, and device credentials outside the Vault corpus.

## Contract defaults

- Workspace `KnowledgeOS`; vault `KnowledgeHub`; language `ko`; timezone `Asia/Seoul`.
- Mac is the canonical decider, writer, bridge worker, and Git conflict resolver.
- iPhone captures and consults; iPad reads, annotates, and performs light clarification; neither applies structural changes.
- Core Obsidian plus Markdown/YAML is canonical; plugins are replaceable adapters.
- Private notes may use remote sync only after explicit opt-in; unattended remote work is disabled by default.
- Mobile immediate API, always-on workers, vector retrieval, and parallel sync are disabled by default.

## Canonical Vault shape

- `Home.md` and `Mobile.md` are `type: home`; keep navigation and action views deterministic.
- Desktop `Home.md` is the generated KnowledgeHub command center: full-width Task, Inbox, and combined AI pending/conflict rows lead to paired active Projects and Decisions, followed by equal Review Pulse and Compass signal cards. Compass embeds `Compass.base#Signals` and the higher-priority `Compass.base#Tensions` view; Home omits research-question duplication and full-view links so the screen remains a compact read-only projection while source notes remain the write surface.
- Home capture choices and hotkeys remain profile-owned and hidden from the page. Home must not advertise provider, runtime, sync, Git, command-runner, or stale-success status; Note Toolbar at the Mac desktop bottom is the navigation surface that replaces the removed Home footer.
- Today Focus is a separate `99_System/Dashboards/Today_Focus.md` system document that can be opened from the sidebar; it is intentionally not a Home component.
- Keep document Properties hidden in the note body and use the Mac Properties sidebar for independent inspection and editing; this is a profile/UI contract, not a Home body projection.
- The Home layout is scoped by the Home-only `cssclasses: [knowledgeos-home]` field and the generated C08 chain (`blueprint/blueprint.yaml` → `ops/src/vaultops/base_dashboard.py` → `KnowledgeHub/Home.md`, canonical Bases, and `99_System/CSS/dashboard.css`).
- `00_Inbox/Captures/YYYY/MM/` stores immutable captures; do not recreate the removed `Imports` namespace.
- `01_AI_Review/{Pending,Resolved}/YYYY/MM/` stores review artifacts, not canonical notes.
- `20_Projects/`, `30_Areas/`, `40_Knowledge/`, `50_Maps/`, `60_Meetings/`, `80_Assets/`, `90_Archive/`, `99_System/` retain PARA-lite responsibilities.
- A project is a bundle (`20_Projects/<slug>/<slug>.md` plus managed companion files).
- `99_System/Bases/` has eight canonical Bases, including the cross-type `Compass.base`; Ideas remains a view in `Knowledge.base`, not a separate Base.
- Device profile roots are created on demand; do not add empty `.obsidian-*` scaffolds.
- Fixed files, marker files, and directory visibility rules are authoritative in YAML `fixed_paths` and `path_namespaces`.

## Note and relation contract

- Information models stay separate: PARA-lite assigns lifecycle folders; Evergreen/Zettelkasten preserves durable claims; RDF-lite expresses typed relations; Hybrid RAG retrieves cited evidence.
- Use flat YAML Properties and Markdown body; preserve stable ids, `type`, lifecycle, sensitivity, source, relation, and timestamp fields from the YAML registry.
- Use the declared 16 templates and the YAML-defined note types, mobile capture fields, assets, relation registry, Bases, dashboards, and required control files.
- Wikilinks and typed relation properties express graph meaning; folder location expresses lifecycle/ownership only.
- Search, embeddings, graph indexes, and dashboards are regenerable projections; never treat them as source.

## End-to-end workflow

```text
capture → durable outbox → validate/triage proposal → human review/approval
→ canonical Markdown/YAML note → exact-path commit/receipt → deterministic projection
→ lexical/typed-link retrieval → cited answer or next action
```

- Capture accepts text, voice, URL, selected text, and asset metadata; the capture remains immutable.
- Review may classify, link, summarize, or request clarification; it must not silently mutate a canonical note.
- Finalize applies only an approved, hash-bound proposal and writes through the one-writer gate.
- Project archive and asset import are explicit transactions; no implicit migration or deletion.

## Pipelines and lanes

- `l0`: local/mobile capture and outbox persistence.
- `l1 Mac`: interactive triage, finalize, asset import, archive, and Git apply.
- `l1 mobile async`: deferred request/response relay; immediate mobile API is off unless gated.
- `l2 retrieval`: native Obsidian → lexical → typed-link/graph expansion → optional local vector → RRF; stale indexes fail closed.
- Action registry, QuickAdd choices, shortcut contracts, automation lanes, bridge schemas, and command content transport are YAML-owned.
- Bridge transport is immutable and schema-trusted; requests contain no executable content and do not imply Git network access.

## Device and mobile gate

- Require Working Copy Pro, an external worktree, root sentinel identity/fingerprint/branch/worktree fields, and round-trip evidence before mobile Git use.
- Do not combine iCloud, Obsidian Sync, Dropbox, or OneDrive with the canonical mobile path.
- Mobile may capture, read, annotate, and defer; Mac performs canonical apply, conflict resolution, plugin changes, and structural moves.
- Device/profile settings and plugin activation are opt-in, profile-specific, and reversible.

## LLM, projection, and retrieval invariants

- LLM mode is proposal-only: no provider fallback, no secret persistence, no live Vault write, and no unapproved prompt result becomes canonical.
- Triage/finalize/answer schemas, approval hashes, source references, and action bindings are selected from YAML and validated before apply.
- Projection generation is deterministic, ordered, hash-bound, and pointer-swapped; it never edits Vault source files.
- Retrieval answers cite source notes and expose staleness/coverage; vector and remote retrieval remain opt-in.

## Privacy and Git invariants

- Default sensitivity is personal/ask; people and meeting content is deny-by-default for AI; confidential/restricted data stays in a separate Vault.
- One writer per file; use create-only or hash-checked atomic replace/move with journal and receipt.
- Never force-push, reset, stash, rebase, or implicitly pull/push; use exact paths and explicit credential/network gates.
- Preserve `KnowledgeHub/.obsidian/{app.json,appearance.json,core-plugins.json,workspace.json}` and separate device/bridge namespaces.

## Implementation and acceptance map

- Follow `implementation_phases` and `acceptance_scenarios` in `blueprint/blueprint.yaml`; do not promote a design phase from this file.
- Foundation: paths, sentinel, schema, semantic validation, generated-artifact ownership, and fixture evidence.
- Portable core: strict notes/templates/Bases/dashboard; local commands and diagnostics remain provider-free.
- Later opt-ins: asset/capture/archive transactions, recovery, bridge/Git publish, Mac plugins, mobile round-trip, and remote/vector lanes.
- Keep static, semantic, artifact, runtime, container, deployment, device, and external-service evidence separate.
- For current phase, blockers, dirty sets, and performed gates, read `PROJECT_STATE.md` only.

## Required checks

```text
make source-check
make verify
make blueprint-check
git diff --check
```

Run container, schema-artifact, tests, lint, runtime, deployment, and device checks only when the active slice requires them; report each result independently.

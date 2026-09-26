---
title: KnowledgeHub Home Command Center Implementation Specification
status: implemented_static
updated: 2026-09-26
planning_method:
  - wayfinder
  - obsidian-markdown
scope: Home.md command-center redesign and bounded static implementation
---

# KnowledgeHub Home Command Center Implementation Specification

> [!warning] Static implementation handoff, not device evidence
> The source, generated-artifact, semantic, and test slice described here is implemented. This document does not claim an additional Obsidian UI operation or device rendering result. `PROJECT_STATE.md` remains the only source of machine state.

## 1. Outcome

Redesign `Home.md` as a dense MacBook command center whose first question is **“What needs my attention or judgment now?”**, not **“Which command can I run?”**

The screen should answer seven questions in one glance:

1. Which Task items need action now?
2. What has entered the Inbox but has not been processed?
3. Which projects are active or blocked, and what is each next action?
4. Which decisions are waiting for me?
5. What am I reading and which reviews are open?
6. Which Compass signals and tensions deserve thought or a relationship check?
7. Which AI outputs are in conflict or waiting for review?

Daily, QuickAdd, full dashboards, Maps, Graph, and source notes remain reachable, but they no longer compete for prime Home space.

## 2. Governing product rules

### 2.1 Home is a projection

- Canonical work remains in project, task, proposal, capture, knowledge, question, source, Area, and MOC notes.
- Home provides bounded views and source links; it does not duplicate note bodies.
- An AI proposal remains non-canonical until human review and the existing apply boundary.
- Home never persists provider, model, broker, index, Git, sync, or deployment success claims.
- A relationship shown as a fact must be reconstructable from canonical frontmatter, not from an unreviewed similarity score.

### 2.2 Home is not a command tutorial

Remove these visible elements from Home:

- QuickAdd choice IDs and hotkey legends;
- the dedicated Quick Capture section;
- the Daily URI instruction;
- terminal commands and sync prose;
- a large static “quick navigation” list;
- the duplicate Projects “Next Actions” panel;
- proposal UUIDs, source hashes, model/provider fields, and runtime receipts.

The QuickAdd and hotkey contracts continue to exist in their own configuration source. Removing their visible documentation from Home must not remove the actual commands.

### 2.3 Home is read/choose first

- Clicking a row or backlink opens its source note for writing.
- Proposal approval/apply never occurs on Home.
- Project, decision, relation, and review fields are edited in their source notes.
- Task completion behavior is the one unresolved exception because Tasks query checkboxes mutate the canonical source line. The recommended navigation-only behavior is specified in [Define the task attention contract](tickets/define-the-task-attention-contract.md).

## 3. Observed current-state constraints

The redesign must account for these verified repository facts:

1. `KnowledgeHub/Home.md` is emitted as literal source by `ops/src/vaultops/base_dashboard.py` and is protected by exactness tests. A manual Vault-only edit is incorrect.
2. The Blueprint YAML, `semantic.py`, Homepage inspection, QuickAdd inspection, and Note Toolbar inspection all hardcode the current Home sections or command inventory.
3. `Projects.base#Now` already shows `next_action`; the current separate `Next Actions` panel spends space on substantially duplicated data.
4. `Decisions.base#Open` currently requires a nonempty `decision`, which can hide the exact undecided open questions that Home should surface.
5. The current `dashboard.css` is the scoped twelve-column source; its active profile loading is a separate device concern.
6. The live Vault is intentionally sparse. It cannot prove populated layout behavior; empty, normal, and overflow fixtures are required.
7. Several templates contain live blank `- [ ] #task` placeholders. A Home task query would surface these as meaningless tasks unless the query and templates are corrected.
8. The Mac profile keeps document Properties hidden and exposes Properties through the right sidebar; all Note Toolbar positions are recorded as desktop `bottom` for this Mac-only scope, so the Home footer is unnecessary.

## 4. Information architecture

### 4.1 Twelve-column screen budget

The MacBook layout uses five compact rows and no Home footer. Note Toolbar at the
desktop bottom owns repeated navigation. Home intentionally has no “전체 보기”
links; the embedded Base views and desktop sidebar provide the next navigation step:

| Region | Grid span | Priority | Visible content | Canonical source limit |
|---|---:|---|---|---:|
| Task | 12/12 | P0 | Bounded task queue without a redundant overdue/today heading | 4 tasks |
| Inbox | 12/12 | P0 | Existing `Unprocessed` capture queue | 10 |
| AI review | 12/12 | P1 | Combined pending/conflict review queue | 10 |
| Projects | 6/12 | P0 | Existing `Now` project queue, status, next action, target date | 3 |
| Decisions | 6/12 | P0 | Existing `Open` decision queue | 5 |
| Review Pulse | 6/12 | P1 | Existing `Reading queue` and open weekly/monthly reviews | 10, 2 |
| Compass | 6/12 | P1 | `Signals` plus the next-priority `Tensions` view | 6, 10 |

The canonical Base view owns each result limit. Home does not introduce a
second queue solely to make a screen-budget cap; where a shorter presentation
is needed later, that must be a separately reviewed projection contract.
Tasks should retain its result count.

### 4.2 Priority interpretation

- **P0:** omission could cause missed work, delayed review, or an unmade decision.
- **P1:** omission weakens system awareness or idea formation but does not immediately block work.
- **P2:** orientation and navigation; useful but deliberately compact.

### 4.3 Proposed wireframe

```text
┌──────────────────────────────────────────────────────────────────────────────┐
│ TASK 12/12                                                                   │
├──────────────────────────────────────────────────────────────────────────────┤
│ INBOX 12/12 — oldest unprocessed captures                                   │
├──────────────────────────────────────┬───────────────────────────────────────┤
│ AI REVIEW 12/12 — pending + conflict                                      │
├──────────────────────────────────────┼───────────────────────────────────────┤
│ PROJECTS 6/12                        │ DECISIONS 6/12                       │
├──────────────────────────────────────┼───────────────────────────────────────┤
│ REVIEW PULSE 6/12                    │ COMPASS 6/12                         │
│ reading + reviews                    │ signals + tensions                   │
└──────────────────────────────────────┴───────────────────────────────────────┘
```

This is a dashboard layout, not a new content hierarchy. Every region terminates in an existing source note, Base view, or full dashboard.

## 5. Panel contracts

### 5.1 Task

**Purpose:** put executable work in the first visual position without exposing plugin instructions.

#### Due task query

Proposed Tasks query:

````markdown
```tasks
not done
due before tomorrow
tags include #task
description regex matches /\S/
sort by due
sort by priority
limit 4
short mode
hide edit button
hide postpone button
hide recurrence rule
hide toolbar
```
````

Contract notes:

- `due before tomorrow` intentionally combines overdue and today.
- The description regex rejects blank/tag-only task placeholders after the configured global filter is removed from the description.
- The source backlink remains visible so the task can be opened in context.
- The implementation must extend the project's Tasks query allowlist only for the exact accepted directives.
- The full Tasks dashboard remains the overflow destination. Today Focus is not embedded on Home;
  it lives in `99_System/Dashboards/Today_Focus.md`.

#### AI review

Reuse the existing `Review.base#PendingOrConflict` view. It is the single
canonical queue for pending and conflict proposals, with its existing
ordering, limit, and columns. Home must not add a second filtered view or
split this queue into Home-only conflict and pending views. Proposal
approval/apply remains outside Home.

### 5.2 Inbox

**Purpose:** show the oldest unprocessed captures as a separate full-width queue.

- Source: `Inbox.base#Unprocessed`.
- Keep the existing Base limit and columns authoritative; Home does not add a
  second capped intake view.

### 5.3 Projects

Reuse `Projects.base#Now`:

- Filter: `type: project`, status in `active`, `blocked`.
- Sort: `focus_rank` ascending, priority high-to-low, target date ascending with null last, filename.
- Keep its existing limit and columns. Home does not add a second project view
  only to reduce the visible row count or change the column projection.
- Remove the separate Home `Next Actions` section. The existing `Now` view is
  the canonical project focus queue.

An active/blocked project without a next action is a schema defect, not a second dashboard category.

### 5.4 Decisions

Reuse `Decisions.base#Open`:

- Filter: `type: question`, status in `open`, `deciding`, `question_kind: decision`.
- Do **not** require `decision` to be nonempty.
- Sort: `decision_by` ascending with null last, priority high-to-low, filename.
- Keep its existing limit and columns. The open decision view remains the
  canonical source for both Home and full Base navigation.

The note title carries the decision question. The source note carries deliberation and the eventual decision.

### 5.5 Review Pulse

Use two short queues and omit Due Areas from Home:

| View | Filter | Sort | Limit | Columns |
|---|---|---|---:|---|
| `Sources.base#Reading queue` | source status queued/reading | published date descending null-last, filename | 10 | file link, status, source kind, authors, published date |
| `Journal.base#Open Reviews` | weekly/monthly status open | period start descending, filename | 2 | file link, type, period end |

Area review cadence remains available in its source Base and the detailed Weekly Review
dashboard; it does not compete with the Home signal row.

### 5.6 Compass

Replace the former Think & Connect card with a dedicated Compass signal card.
Do not duplicate `Decisions.base#Research Questions` on Home. Use two bounded
Compass views:

#### Signals

Use `Compass.base#Signals`, the canonical cross-type signal view. It is a
deterministic union of three human-readable conditions:

- knowledge/source notes with a non-empty `contradicts` relation;
- knowledge notes whose `confidence` is `low` or `unknown`;
- seed/incubating/testing ideas with no `projects`, `related`, or `raises` links.

The view is capped at six rows and sorted by actual file modification time,
then filename. It does not infer a relation or call an AI provider.

#### Tensions

`Compass.base#Tensions` is the next-priority view after `Signals` because it
isolates explicit contradictions in knowledge and source notes. It is capped at
ten rows and uses the canonical contradiction fields. The full Compass Base
continues to provide the remaining drill-down views without duplicating Home
cards:

| View | Purpose | Limit |
|---|---|---:|
| `Signals` | mixed tension, weak-claim, and unconnected-idea signals | 6 |
| `Tensions` | knowledge/source notes with explicit contradictions | 10 |
| `Research Gaps` | open research/problem questions without a project link | 10 |
| `Unconnected Ideas` | active ideas without project, relation, or follow-up links | 10 |
| `Low Confidence` | knowledge notes whose confidence is low/unknown | 10 |
| `Project Bridges` | active/blocked projects with explicit `implements` links | 10 |

`Knowledge.base#Radar` and `Decisions.base#Research Questions` remain available
as separate source views, but neither is embedded in Home; Compass owns the Home
connection signal.

### 5.7 AI review

**Purpose:** isolate AI review risk from ordinary work queues in one full-width
component directly below Inbox.

- `Review.base#PendingOrConflict` is the single combined pending/conflict
  queue. Its existing ordering and limit remain authoritative.
- Proposal approval/apply never occurs on Home.

The removed `ko-home-footer` is not replaced by another Markdown footer. The desktop
bottom Note Toolbar supplies reviewed navigation actions; its serialized position is a
Mac profile contract, while mobile/tablet positions are out of scope here.

## 6. Obsidian Markdown scaffold

The following is a syntactically valid Obsidian Flavored Markdown scaffold. It uses the eight-Base baseline and the view names above. The scoped `cssclasses` field keeps the layout Home-only; profile activation and device rendering remain separate evidence.

````markdown
---
schema_version: 1
id: home
type: home
title: Home
status: active
created: 2026-09-07T00:00:00+09:00
modified: 2026-09-26T00:00:00+09:00
aliases: []
tags: []
cssclasses:
  - knowledgeos-home
purpose: "행동·판단·연결을 한 화면에서 조망하는 KnowledgeHub 사령탑"
audience: desktop
sensitivity: personal
ai_policy: deny
ai_status: idle
---

# Home

> [!ko-home-grid]
> > [!ko-home-tasks] Task
> > ```tasks
> > not done
> > due before tomorrow
> > tags include #task
> > description regex matches /\S/
> > sort by due
> > sort by priority
> > limit 4
> > short mode
> > hide edit button
> > hide postpone button
> > hide recurrence rule
> > hide toolbar
> > ```
> > - Core-only fallback: Core Search와 원문 Markdown checkbox를 확인한다.
>
> > [!ko-home-inbox] Inbox
> > ![[99_System/Bases/Inbox.base#Unprocessed]]
> >
> > [!ko-home-ai-review] AI 검토 대기·충돌
> > ![[99_System/Bases/Review.base#PendingOrConflict]]
>
> > [!ko-home-projects] 진행 중인 프로젝트
> > ![[99_System/Bases/Projects.base#Now]]
>
> > [!ko-home-decisions] 내가 결정할 것
> > ![[99_System/Bases/Decisions.base#Open]]
>
> > [!ko-home-review] 검토 리듬
> > #### 읽는 중
> > ![[99_System/Bases/Sources.base#Reading queue]]
> >
> > #### 열린 회고
> > ![[99_System/Bases/Journal.base#Open Reviews]]
>
> > [!ko-home-compass] Compass 신호
> > #### 신호
> > ![[99_System/Bases/Compass.base#Signals]]
> >
> > #### 긴장
> > ![[99_System/Bases/Compass.base#Tensions]]
````

### 6.1 Markdown design rationale

- YAML lists remain YAML lists; wikilinks in frontmatter remain quoted where the schema requires them.
- Base embeds use the native `![[File.base#View]]` syntax.
- The Tasks query stays in an official fenced `tasks` block.
- Nested custom callouts provide semantic layout containers while degrading to a readable vertical sequence if CSS is absent.
- No raw HTML grid is required.
- No Dataview or DataviewJS block is introduced.
- Source links use aliases rather than exposing file-system detail in the visible label.

## 7. CSS and Mac layout contract

### 7.1 Scoping

Preferred scoping:

- Home frontmatter: `cssclasses: [knowledgeos-home]`.
- Layout callout: `[!ko-home-grid]`.
- Child card types: `ko-home-tasks`, `ko-home-inbox`, `ko-home-ai-review`, `ko-home-projects`, `ko-home-decisions`, `ko-home-review`, `ko-home-compass`.
- There is no Today Focus strip or `ko-home-footer`; Today Focus is a standalone system document and Note Toolbar owns repeated navigation.

All selectors must be nested under `.knowledgeos-home`. This avoids changing other notes and allows Home-only treatment of Properties and the inline title.

### 7.2 Grid behavior

At or above 1200 CSS pixels:

- `ko-home-grid` content uses 12 equal columns.
- Cards use spans 12, 12, 12, 6, 6, 6, 6 in source order.
- Row/column gap is 0.65–0.8 rem.
- Child cards set `min-width: 0` so Base tables can wrap rather than force page-wide overflow.

From 900 to 1199 pixels:

- Use two equal columns.
- The seven cards collapse to one span per column at this breakpoint.

Below 900 pixels or when CSS is unavailable:

- Use one column in source order.
- Preserve every source link and heading.

### 7.3 Density rules

- Reduce card padding, heading margins, and Base row height only within Home.
- Wrap `next_action`; do not shrink it into unreadable ellipsis-only text.
- Use tabular numerals for dates.
- Do not hide truncation/result counts that tell the user a queue has more items.
- Avoid decorative hero banners, large icons, progress rings, and count tiles with no action destination.
- Prefer hard query limits over card-internal scroll. Permit internal scroll only in the overflow fixture and keep the page itself stable.

### 7.4 Chrome rules

The accepted Mac profile boundary is:

- keep document Properties hidden in the note body and use the right Properties sidebar for independent inspection/editing;
- hide the duplicate inline title if the visible `# Home` heading remains;
- keep the Home Note Toolbar at the desktop `bottom` position as the navigation replacement for the removed footer;
- leave mobile/tablet toolbar positions and every other note unchanged.

### 7.5 Artifact and activation model

The implementation makes the current ownership boundary explicit:

1. Keep the canonical Home and stylesheet source in the control repository's C08 generator chain.
2. Emit the portable Vault copies at `Home.md`, `99_System/Bases/*.base`, and `99_System/CSS/dashboard.css`.
3. Keep the existing generated-artifact and schema checks authoritative for the static outputs; do not add a second Home writer.
4. Treat scoped CSS activation and visual checking as separate profile/device evidence; the source chain never implies UI success.

## 8. Plugin plan

No new community plugin is required for the baseline.

| Capability | Baseline decision | Reason |
|---|---|---|
| Property-backed queues | Core Bases | Already canonical, local, generated, and tested |
| Vault-wide due tasks | Existing Tasks plugin | Already matches canonical Markdown checkboxes |
| Startup destination | Existing Homepage plugin | Already opens Home in Reading view |
| Wide layout | Project-owned CSS snippet | Fewer dependencies and graceful fallback |
| Relation drill-down | Core links/Graph plus existing Breadcrumbs | Keeps Home deterministic and source-note oriented |
| Dataview | Do not add | Duplicates Bases and creates another query contract |
| Modular CSS Layout | Do not add | Custom callouts plus scoped CSS cover the requirement |
| TaskNotes | Do not add | Would change the canonical task model |
| Smart/semantic connection plugin | Do not add | Introduces another embedding/index/provider lifecycle and bypass risk |

Reopen plugin selection only for a precisely named accepted signal that cannot be expressed in Bases, Tasks, Markdown, or scoped CSS.

## 9. External pattern research applied

The proposal borrows principles, not a copied visual theme:

- Obsidian's official [Bases introduction](https://obsidian.md/help/bases), [view documentation](https://obsidian.md/help/bases/views), and [syntax reference](https://obsidian.md/help/bases/syntax) support local property-backed, bounded, embeddable views. That makes Bases the right primary projection layer.
- Obsidian's official [embed documentation](https://obsidian.md/help/embeds) supports embedded Base views and headings, while the [Canvas documentation](https://obsidian.md/help/plugins/canvas) confirms Canvas is a separate visual thinking surface. Home therefore links to Maps/Canvas rather than treating an embedded Canvas as the compact operational overview.
- Tasks' official [filters](https://publish.obsidian.md/tasks/Queries/Filters), [limits](https://publish.obsidian.md/tasks/Queries/Limiting), and [layout controls](https://publish.obsidian.md/tasks/Queries/Layout) support a short, bounded attention queue without adopting a new task system.
- Obsidian's official [CSS snippets guide](https://obsidian.md/help/snippets) supports the Mac-specific dense layout while keeping Markdown readable without styling.
- A public review-gated [Obsidian LLM wiki example](https://github.com/mickeytony0215-png/obsidian-llm-wiki/blob/main/wiki/dashboard.md) separates unreviewed AI output from canonical ingestion and exposes health prompts such as missing links and stale notes. KnowledgeOS adopts the review-first and health-signal pattern while retaining its stricter proposal/apply contracts.
- A public [LLM wiki operating pattern](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f) emphasizes indexes/logs and lint-style orphan, stale, contradiction, and gap checks. KnowledgeOS translates that idea into bounded canonical-property signals rather than an autonomous rewrite loop.

## 10. Contract migration and file-impact plan

This redesign must be implemented as one coherent acceptance slice. The implementation should touch only files needed by the accepted decisions.

### 10.1 Design authority

1. `OBSIDIAN_VAULT_BLUEPRINT.md`
   - Change Home's mission from command/launcher emphasis to Task, Inbox, judgment, and connection.
   - Record Home as a Mac-wide deterministic projection.
   - Preserve AI review and human-apply boundaries.
2. `blueprint/blueprint.yaml`
   - Replace the current Home section list and limits with Task/Inbox/AI review, Projects/Decisions, and equal Review Pulse/Compass signal cards.
   - Keep Today Focus in the standalone `99_System/Dashboards/Today_Focus.md` system document and remove the Home footer.
   - Add `Compass.base` as the canonical cross-type signal Base; do not add Home-only duplicate views.
   - Remove visible QuickAdd actions from the Home dashboard contract without deleting the QuickAdd profile registry.
   - Add the accepted Home-only `cssclasses` schema support.

### 10.2 Generated source and semantic contracts

3. `ops/src/vaultops/base_dashboard.py`
   - Emit the new Home body, Base views, and CSS/snippet source.
4. `ops/src/vaultops/semantic.py`
   - Replace the exact current section/source set.
   - Validate P0/P1 view filters, sorts, caps, and prohibited command/status content.
5. `ops/src/vaultops/tasks_settings.py`
   - Add `Home.md` as an approved Tasks-query source.
   - Allow only the exact compact/filter directives accepted for Home.
6. `ops/src/vaultops/homepage_settings.py`
   - Inspect the new required Home tokens and reject the removed QuickAdd/hotkey/sync content.
7. `ops/src/vaultops/quickadd_settings.py`
   - Decouple QuickAdd action/hotkey inventory from the visible Home `quick_capture` section.
8. `ops/src/vaultops/note_toolbar_settings.py`
   - Keep QuickAdd actions profile-owned and hidden from Home.
   - Encode Mac desktop `bottom` as the accepted Note Toolbar position and document that it replaces the removed Home footer; mobile/tablet positions remain out of scope.

### 10.3 Ownership and outputs

9. `ops/config/generated-artifacts.yaml`
   - Register Home, Base, dashboard CSS, and Mac snippet ownership or explicitly document why they use another ownership mechanism.
10. `KnowledgeHub/Home.md`
    - Regenerate from accepted source; do not hand-edit independently.
11. `KnowledgeHub/99_System/Bases/*.base`
    - Regenerate affected Base views, including the new `Compass.base` projection.
12. `KnowledgeHub/99_System/CSS/dashboard.css`
    - Regenerate the portable stylesheet.
13. `KnowledgeHub/.obsidian-mac/snippets/knowledgeos-home.css`
    - Create only in the later authorized implementation/profile slice.
14. `KnowledgeHub/.obsidian-mac/appearance.json`
    - Preserve the user's current dirty change and merge only an explicitly authorized snippet activation.

### 10.4 Task hygiene

15. Future-note templates that currently emit blank live tasks, including Daily, Weekly, and Project templates:
    - replace live blank checkboxes with HTML comments or non-query examples;
    - update template tests;
    - do not rewrite existing user notes automatically.

### 10.5 Fixtures and tests

16. Extend the C08 populated fixture with:
    - overdue and today tasks;
    - tag-only blank task noise;
    - proposal conflict and pending items;
    - active and blocked projects;
    - open decision with blank `decision`;
    - research/problem questions;
    - explicit contradiction;
    - low-confidence knowledge;
    - disconnected idea;
    - unprocessed captures and queued sources;
    - current reviews, due Area, and active MOC.
17. Add an explicit empty fixture and an overflow fixture.
18. Update exactness, semantic mutation, portable fixture, Homepage, QuickAdd, Tasks, and Note Toolbar tests.

### 10.6 Close-only human documentation

19. `README.md`
    - Read and update only after the implementation and verification results are known.
    - Replace the current “Home quick capture” and command-oriented walkthrough with the accepted attention → source-note flow.
    - Keep QuickAdd documented in its own capture workflow rather than presenting it as prime Home content.
    - Do not record completion, device rendering, or plugin activation that was not actually verified.

## 11. Implementation sequence

### Phase 0 — accept the decision frontier

Resolve or revise the open Wayfinder tickets. Register a new bounded implementation goal only after acceptance. Do not attach this work to the superseded G/D design lanes.

### Phase 1 — contract first

1. Update Blueprint Markdown and YAML.
2. Register the accepted Home-only `cssclasses` property in the Blueprint,
   generated Property Dictionary, note schema, and focused tests.
3. Update semantic rules and negative mutations before changing deployed outputs.
4. Run source and Blueprint checks.

### Phase 2 — query and projection sources

1. Add the accepted Base views.
2. Add the bounded Tasks query and allowlist.
3. Generate the OMF Home scaffold.
4. Add scoped CSS with single-column fallback.
5. Remove blank live task placeholders from future templates.

### Phase 3 — decouple plugin contracts

1. Move QuickAdd action/hotkey authority out of the visible Home section.
2. Update Homepage inspection to the new dashboard contract.
3. Remove the Home command inventory requirement from Note Toolbar inspection.
4. Keep the actual QuickAdd choices and hotkeys unchanged unless separately requested.

### Phase 4 — fixtures and deterministic verification

1. Compile empty, normal, and overflow fixtures.
2. Verify exact generated bytes and semantic invariants.
3. Verify that an open decision with a blank `decision` appears.
4. Verify that a blank/tag-only task does not appear.
5. Verify that conflicts precede pending review.
6. Verify that removed hotkeys, command IDs, and sync prose fail a prohibited-content check if reintroduced.

### Phase 5 — static handoff

1. Run the canonical source, Blueprint, schema, test, and lint checks sequentially.
2. Validate `PROJECT_STATE.md` after its implementation-state update.
3. Run `git diff --check` in both Git roots.
4. Inventory dirty sets and explicitly preserve unrelated user changes.
5. Read and reconcile the relevant `README.md` Home walkthrough only after the verified implementation state is known.

### Phase 6 — separately authorized Mac visual acceptance

1. Deploy/enable the tracked snippet in the intended Mac profile.
2. Open Home in Reading view through the existing Homepage behavior.
3. Capture empty, normal, and overflow screenshots at the accepted viewport matrix.
4. Verify no unintended change to other note types, Properties behavior, or toolbars.
5. Record this as profile/device evidence, not as static source evidence.

## 12. Acceptance criteria

### 12.1 Content and hierarchy

- Home begins with full-width Task and Inbox rows, followed by Projects and Decisions.
- No QuickAdd ID, hotkey legend, Daily URI instruction, terminal command, or sync status prose is visible.
- Today Focus is absent from Home and available as the standalone `Today_Focus.md` system dashboard.
- Active/blocked projects and next actions appear once in the same table.
- AI conflict and pending review are visible in the full-width AI review card and open their source rows directly.
- Open decision questions remain visible when `decision` is empty.
- Inbox, reading queue, Compass Signals, Compass Tensions, and open reviews have bounded projections without redundant full-view links; Due Areas are not embedded in Review Pulse.

### 12.2 Truthfulness

- No Home label depends on AI-inferred relations that have not passed human review/apply.
- No runtime/service/provider/Git/deployment state is presented as current.
- Every “tension,” “gap,” “due,” or “unconnected” label is backed by an accepted filter over canonical fields.
- Home does not approve/apply proposals or edit project/knowledge metadata.

### 12.3 Layout

- At 1512×982 in Reading view, the seven cards and all P0/P1 queues are visible without page scrolling.
- At 1440×900, Task/Inbox/Projects/Decisions and the three signal titles are visible with only queue-row overflow.
- No panel causes page-wide horizontal scrolling.
- The layout becomes two columns and then one column at the accepted breakpoints.
- With CSS disabled, the Markdown remains readable in correct priority order.

### 12.4 Empty and overflow behavior

- Empty views do not render as large blank regions or errors.
- The sparse live Vault produces a calm, usable Home.
- Overflow is represented by a bounded result plus the source Base or sidebar navigation, not by an unbounded Home list.
- A blank task placeholder does not appear as an actionable task.

### 12.5 Contract and verification

- Blueprint Markdown and YAML agree.
- Generator source, deployed outputs, schemas, semantic checks, and fixtures agree.
- QuickAdd remains functional but is no longer coupled to visible Home text.
- Homepage and Note Toolbar inspections reflect the new contract.
- Static/container checks and Mac profile/device checks are reported as separate evidence classes.

## 13. Verification matrix and handoff

The static implementation slice covers the Blueprint, C08 generator, generated Home/Base/CSS outputs, the canonical `Compass.base` signal projection, semantic/plugin contracts, bounded templates, fixtures, focused tests, and the downstream generated-artifact reconciliation for the current Home Blueprint digest. The first post-change run exposed the same stale digest chain as the earlier Home slice with 401 passed and 29 failures; replacing the prior authoritative input digest with the current Blueprint digest and regenerating all eight canonical Base files restored zero-diff output. The current full suite records 430 passed and `make schema-check` passes every applicable generated artifact including both Property Dictionary copies and the LaunchAgent. Obsidian UI rendering is not claimed by this repository slice.

| Evidence class | Check | Expected result |
|---|---|---|
| Static source | `make source-check` | Blueprint/source checks pass |
| Static contract | `make blueprint-check` | Home/Base/plugin contracts agree |
| Schema | `make schema-check` | Home metadata and affected note schemas pass |
| Unit/integration | canonical project test target | exactness, semantic mutations, plugin inspections, fixtures pass |
| Lint | canonical lint target, after tests | source and test lint pass without the uv-cache race |
| State syntax | `/usr/bin/python3 scripts/validate_state.py PROJECT_STATE.md` | implementation goal/evidence is valid |
| Git hygiene | `git diff --check` in control and Vault roots | no whitespace errors |
| Artifact | generated-output comparison | Home, Bases, CSS, and snippet match registered sources |
| Profile/device | authorized Obsidian visual run | viewport, fallback, toolbar, and Properties behavior accepted |

No static, fixture, or semantic check may be reported as proof that the snippet is enabled or that Obsidian rendered it correctly on the user's Mac.

## 14. Risks and mitigations

| Risk | Consequence | Mitigation |
|---|---|---|
| Too many small tables | Header chrome consumes the screen | Prototype combined versus split views; enforce caps and compact Home CSS |
| Sparse live Vault | Empty cards appear broken | Explicit empty fixture and compact empty rendering |
| Task checkbox is live | Home becomes an editing surface | Resolve the task-interaction ticket and retain source backlinks |
| `cssclasses` conflicts with strict schema | Home fails validation | Register it intentionally before deployment or choose callout-only fallback |
| Hidden Properties via global setting | Every note changes | Scope styling to Home; preserve global profile setting |
| Relation signal overclaims meaning | False inspiration becomes false fact | Use only reconstructable canonical fields and human-applied edges |
| `file.backlinks` refresh/performance | Stale or slow Home | Prefer explicit relation properties in baseline |
| Existing dirty profile files | User changes overwritten | Merge only after exact authorization; never regenerate blindly |
| Manual Home edit | Generator/test drift | Update authority, generator, outputs, and tests in one slice |
| Added plugin | New maintenance and privacy boundary | Baseline has no new plugin |

## 15. Deliberate exclusions

- G-labelled GUI plans and D-labelled mobile plans.
- Mobile/iPhone/iPad layout and capture behavior.
- Obsidian GUI operation or profile activation in this planning turn.
- Community-plugin installation.
- Provider/model selection, index/broker/runtime health, sync status, Git status, deployment state, and service receipts.
- Automatic relation generation or proposal apply.
- Existing user-note rewrites for blank task placeholders.
- A Home-based editor for project, decision, or knowledge content.

## 16. Decision handoff

The Compass implementation is complete at the source/file/test level. Five
later product decisions remain intentionally visible in the [Wayfinder map](MAP.md)
for a separate interaction or visual-acceptance slice:

1. accept the one-screen information budget and viewport target;
2. choose task navigation-only versus direct checkbox completion;
3. accept the minimal AI review fields or add a human-oriented proposal summary;
4. keep the existing plugin-free projection boundary (no new community plugin);
5. register Home-only `cssclasses` and remove/minimize the Home toolbar.

Those later choices can authorize a separate bounded interaction or profile/device
slice without revisiting the Compass source contract or the G/D design lanes.

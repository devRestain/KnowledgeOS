# G lane — KnowledgeHub MacBook GUI design contract

Status: revised planning contract for `G02_GUI_DESIGN_REVISION_20260925`  
Audience: the human who uses KnowledgeHub on a MacBook and on a vertical mobile screen  
Primary job: make the next useful decision visible with the least possible scrolling  
Scope: Home topology, action hierarchy, repository-text previews, visual tokens, responsive behavior, Figma edit direction, and Obsidian implementation boundaries  
Out of scope: editing the Figma file in this session, operating Obsidian, changing the live profile, installing a theme or plugin, moving Vault files, Git remote effects, provider effects, or canonical note mutation

This document supersedes the first-pass single-pane G direction. It is the
design authority for the next Figma-planning and Obsidian-implementation
slices. It is a design contract, not evidence that a Figma frame, theme, CSS
snippet, toolbar layout, text projection, or Properties position is active.

## Revision requested by the user

The MacBook should use its horizontal canvas as an information advantage. The
Home surface should behave as a command center rather than a long list of
equally weighted destinations:

- Use two coordinated sections on a wide MacBook surface so orientation,
  actions, and evidence can be seen together.
- Stack those sections vertically on a tall mobile surface. Never force a
  horizontal layout or horizontal scroll on a narrow viewport.
- Collapse actions that describe the same operation. `Open daily` and a
  `daily/open` implementation are one visible action, not two controls.
- Make known actions visually compact. The saved area should show useful
  repository text instead of repeating large buttons or navigation labels.

This is a change in information priority, not a request to add more dashboard
features. Home should show less chrome and more source-linked meaning.

## Design philosophy — the signal console

KnowledgeHub Home is a **signal console**: a small operational surface that
answers three questions in one glance.

1. **What deserves attention?** — today focus, active work, and an open decision.
2. **What can I do next?** — one daily entry and a compact set of reviewed
   capture actions.
3. **What does the source actually say?** — short, read-only text windows from
   canonical KnowledgeHub notes and Base projections.

The memorable element is a restrained **signal rail** at the boundary between
the two desktop sections. It is a functional ownership marker: the left side
is for moving, the right side is for reading and judging. It is not a
decorative dashboard border, a third navigation system, or a repeated card
outline.

The design must feel precise and quiet rather than like a generic SaaS card
wall. The recommended UI Pro Max navy/grey, secure-green, and Fira pairing is
adopted as a considered starting point, but its use is constrained by the
KnowledgeOS job: Fira Code is reserved for operational values, secure green
is a semantic accent, and the reading field stays content-first.

## Figma reference and future edit brief

The Figma project `KnowledgeHub — G GUI Design Studio` is a reference canvas
to revise in a later Figma session. The existing Figma arrangement is not an
authority over this brief and should not be preserved merely because it already
exists. This session records the change specification; it does not edit the
file or invent node IDs.

The next Figma session should make these edits in order:

1. Recompose the Home frame into a wide two-pane composition.
2. Shrink the action treatment into a compact command strip or command list.
3. Merge the two daily-opening affordances into one `Open daily` component.
4. Replace the removed button mass with three source-linked text windows.
5. Add a mobile frame that stacks the same semantic regions vertically.
6. Annotate loading, empty, focus, conflict, and reduced-motion states before
   the visual design is considered implementation-ready.

Recommended Figma frames:

| Frame | Purpose | Required topology |
| --- | --- | --- |
| `G/Home/MacBook` | primary wide command center | command deck + reading field |
| `G/Home/MacBook-compact` | sidebar-reduced desktop | narrower two-pane layout or early collapse |
| `G/Home/Mobile` | vertical phone layout | one column, same semantic order |
| `G/Home/States` | interaction and data states | focus, empty, conflict, loading, reduced motion |

The Figma component names should describe user meaning rather than the
implementation: `Daily anchor`, `Capture command`, `Text signal`, `Source
link`, `Empty signal`, and `Conflict signal`. Do not use a card component for
every region just because a component library makes that easy.

## Visual token system

### Palette

The first-pass cool mineral palette is replaced by a navy/slate foundation
with one secure-green semantic accent and one warm human-attention accent. The
values below incorporate the UI Pro Max recommendation while keeping light and
dark modes explicit.

| Token | Light value | Dark value | Use |
| --- | --- | --- | --- |
| `--g-field` | `#F1F5F9` | `#0F172A` | outer workspace and quiet background |
| `--g-surface` | `#FFFFFF` | `#192134` | Home reading surface and text windows |
| `--g-navy` | `#1E3A5F` | `#8FB4D5` | primary action, active route, strong divider |
| `--g-slate` | `#334155` | `#CBD5E1` | secondary text, supporting controls |
| `--g-ink` | `#0F172A` | `#F8FAFC` | primary reading text |
| `--g-muted` | `#64748B` | `#94A3B8` | secondary text and empty-state guidance |
| `--g-secure` | `#059669` | `#34D399` | confirmed, safe, or completed state |
| `--g-attention` | `#B45309` | `#FBBF24` | current human attention path |
| `--g-conflict` | `#B91C1C` | `#F87171` | conflict, stale source, or blocked state |
| `--g-line` | `#CBD5E1` | `#334155` | quiet division between ownership regions |

Rules:

- `--g-navy` owns the one primary action on a pane. It is not a fill for every
  link or button.
- `--g-secure` communicates a verified or safe state with text or an icon as
  well; color alone never carries the meaning.
- `--g-attention` marks the current human focus, not every active row.
- `--g-conflict` is reserved for an observed conflict or blocked condition.
- Body text and source excerpts must meet at least 4.5:1 contrast against
  their actual light or dark surface. Do not infer dark-mode contrast from
  light-mode values.
- Borders are quieter than text. Avoid shadows and gradients as hierarchy.

### Typography

The recommended pairing is accepted with a deliberate division of labor:

- `Fira Sans` is the interface, heading, label, and note-reading family.
- `Fira Code` is used only for shortcuts, note IDs, hashes, paths, command
  names, timestamps, and other values where fixed-width alignment helps.
- Do not use Fira Code for every heading. A full code-panel treatment would
  make a personal knowledge workspace feel like an admin console.
- Use a system fallback if the font is unavailable; do not block reading on a
  remote font load.

| Role | Size / leading | Weight | Measure |
| --- | --- | --- | --- |
| Home title | 28–32 / 1.15 | 600–700 | one or two lines |
| Pane title | 18–20 / 1.25 | 600 | short label |
| Body and excerpt | 16 / 1.5–1.65 | 400 | 60–75 characters |
| Compact action | 14–15 / 1.3 | 500–600 | sentence case |
| Operational value | 12–14 / 1.4 | 400–500 | Fira Code, wrap safely |

Labels use sentence case and plain verbs. The design does not use all-caps
eyebrows, decorative numbering, or a different typeface for every hierarchy
level.

### Shape, spacing, and motion

- Use an 8px base rhythm: 8 for icon gaps, 16 for related content, 24 for a
  pane gap, and 32 for a change of decision context.
- Use a 6px radius for compact controls and text windows. Do not wrap every
  section in a rounded container.
- Keep one continuous page scroll. Do not make the two Home columns separate
  scroll regions.
- Animate only user-triggered expansion, navigation, or confirmation. Use
  `transform` and `opacity`, keep transitions interruptible, and respect
  `prefers-reduced-motion`.
- Use a visible `:focus-visible` ring with at least a 2px outline. Focus must
  remain visible when the signal rail or any sticky UI is present.

## MacBook layout definition

The desktop split belongs inside the Home content canvas. It does not duplicate
Obsidian's native File Explorer or create a second permanent sidebar.

### Wide desktop: two-pane command center

At an effective content width of approximately 1120px or more:

- **Command deck:** 36–40% of the Home canvas. It contains the one daily
  anchor, compact capture commands, and a short action queue.
- **Repository reading field:** 60–64% of the Home canvas. It contains the
  source-linked text windows and the evidence that makes the next action
  intelligible.
- **Signal rail:** a 2px semantic divider between the panes. It can use
  `--g-attention` for the current focus state and `--g-line` otherwise.
- Align both pane titles to the same top baseline. Do not center either pane.

At 900–1119px, keep the split only when both panes retain their minimum
readable measure. Otherwise collapse early to one column rather than causing
text clipping or horizontal scroll.

```text
┌────────────────────────────────────────────────────────────────────────┐
│ Home                                      local status / sync observation │
├───────────────────────────────┬─ signal ─┬──────────────────────────────┤
│ COMMAND DECK                  │  rail    │ REPOSITORY READING FIELD      │
│                               │          │                                │
│ Today                         │          │ Read first                      │
│ Today focus sentence          │          │ source title                    │
│ [Open daily]                  │          │ two or three lines of text     │
│                               │          │ [Open source]                  │
│ Capture                       │          │                                │
│ [Thought] [Idea] [Project]    │          │ Decide                          │
│ [Question] [Knowledge]        │          │ question / decision excerpt    │
│                               │          │ [Open decision]                │
│ Action queue                  │          │                                │
│ Now                           │          │ Watch                           │
│ Needs a decision              │          │ knowledge / Inbox / Review    │
│ Next action                  │          │ short source-linked excerpt   │
│                               │          │ [Open source]                  │
└───────────────────────────────┴──────────┴──────────────────────────────┘
```

The right side is not a second list of links. Its primary content is text;
the source link is a quiet escape hatch after the excerpt.

### Mobile: vertical reading sequence

Below the collapse breakpoint, use one document flow. Preserve the same
meaning, not the desktop geometry:

```text
┌──────────────────────────────┐
│ Home                         │
│ Today                        │
│ focus sentence               │
│ [Open daily]                 │
│                              │
│ Capture                      │
│ [Thought]                    │
│ [Idea]                       │
│ [Project]                    │
│ [Question]                   │
│ [Knowledge]                  │
│                              │
│ Action queue                 │
│ Now · Decision · Next        │
│                              │
│ Repository reading           │
│ Read first                   │
│ excerpt + source             │
│ Decide                       │
│ excerpt + source             │
│ Watch                        │
│ excerpt + source             │
└──────────────────────────────┘
```

Mobile actions may become full-width rows for touch comfort, but they must not
grow into large promotional tiles. Each action has a minimum 44px hit area,
8px spacing, a visible label, and a keyboard or platform-equivalent path.

## Home information architecture

### 1. Command deck

The left desktop pane is a compact control surface, not a catalog of every
destination.

| Region | Visible content | Rule |
| --- | --- | --- |
| Today | one focus sentence and one `Open daily` action | expose one daily-opening control only |
| Capture | five reviewed choices | use compact command rows; preserve the existing choice IDs and hotkeys |
| Action queue | Now, Needs a decision, and Next action | show the smallest useful list; the full Base remains the destination |

The URI or command implementation behind `Open daily` is not a second visual
control. Do not render both a `daily/open` affordance and an equivalent
`Open daily` button. If a full Daily Base view is still useful, place it as a
quiet source link in the Today region or leave it to the Daily destination;
it must not compete with the primary daily action.

Capture controls follow the same rule. Their icon, label, and shortcut can sit
in one compact row. The visual size communicates “known command”, not “hero
CTA”. A desktop row may use a 32px visual control with a larger invisible hit
area; a mobile row must remain at least 44px tall.

### 2. Repository reading field

The right desktop pane replaces redundant button mass with three short text
windows. Each window contains a source title, a bounded excerpt or canonical
property value, a freshness/status cue, and one direct source link.

| Window | Preferred source | Allowed content |
| --- | --- | --- |
| Read first | `Journal.base#Today Focus` or the current focus note | today focus, outcome, or first useful sentence |
| Decide | `Decisions.base#Open` | question, decision, decision date, and one linked excerpt |
| Watch | `Projects.base#Now`, `Knowledge.base#Radar`, `Inbox.base#Unprocessed`, or `Review.base#PendingOrConflict` | outcome/next action, claim/possibility, capture excerpt, or proposal/conflict summary |

At most three windows are visible by default. Each excerpt is short enough to
scan in place and links to the full note. An empty window says why it is empty
and names the next safe route; it does not leave a blank box or invent a
success state.

“Repository text” means text owned by the canonical KnowledgeHub Markdown and
Base projection surfaces. Git logs, provider logs, credentials, and external
service output do not silently become Home content. If a later implementation
needs a body-text extractor because Native Bases expose only properties, that
extractor is a separate, read-only contract. It must not become a second note
writer or an unreviewed DataviewJS authority.

Text windows are projections, not hand-written summaries. They must preserve a
source link and an observable timestamp or status when one exists. A stale
preview is disclosed rather than presented as live truth.

## Source, action, and state semantics

The future Figma design and Obsidian rendering must keep these distinctions:

- Internal note navigation uses Obsidian wikilinks such as
  `[[99_System/Dashboards/Tasks]]`; external or reviewed URI actions remain
  ordinary Markdown links or exact command adapters.
- A navigation destination is a link; a mutation or command is a button-like
  action. Do not make a clickable text container stand in for both.
- A status badge is read-only unless its owner explicitly permits editing.
  `status`, `priority`, `next_action`, and `today_focus` remain the only
  candidate low-risk interactive metadata fields from the existing P12
  boundary.
- Use Obsidian callouts only for semantic state: `info` for fallback guidance,
  `warning` or `failure` for an actual blocked/conflict condition, and
  `success` only after a bounded result. Do not use callouts as decorative
  cards.
- Keep YAML frontmatter at the top of every note. A rendered bottom metadata
  footer is a presentation target, never a second hand-written copy of the
  frontmatter.

## Template and Properties presentation

The shared reading order remains:

1. Title and one-sentence orientation.
2. The note's primary content: capture, outcome, question, claim, or source.
3. Evidence, context, and typed relations.
4. Next action or decision boundary.
5. Related notes and review links.
6. Rendered metadata footer when a stable route is verified.

The current serialized Core observation is `propertiesInDocument: visible`.
The desired visual target is `rendered_position: bottom`; this is not a claim
that the current Mac profile supports it. The later implementation must test,
in order:

1. stable CSS reflow of the native metadata container;
2. a read-only Meta Bind footer for the approved low-risk fields; or
3. the native Properties view and portable Markdown as the explicit fallback.

The accepted G09 Home contract registers Obsidian's general `cssclasses`
property in the Blueprint, generated Property Dictionary, note schema, and
focused tests. Keep it scoped to the generated Home note; future-note
templates remain free of `cssclasses`. The Obsidian-flavored Markdown skill
makes the field valid in general, but the stricter KnowledgeOS schema remains
the source contract here.

## Plugin and icon placement

Plugins are replaceable adapters. Home should not expose a permanent icon for
an invisible renderer or make a plugin look like a new authority.

| Surface | Home or note placement | Visible job | Must not become |
| --- | --- | --- | --- |
| Homepage | startup target | open `Home.md` in Reading view | command runner |
| QuickAdd | compact Capture region | create one traced context | policy or writer authority |
| Core Daily Notes | single Today action | open/create today's Daily | second period writer |
| Notebook Navigator | native navigation pane | navigate and create periods | bulk move/delete surface |
| Note Toolbar | contextual row | open reviewed destinations | shell, URI, AI, or Git launcher |
| Breadcrumbs | relation trail under title | follow typed relations | relation writer |
| Tasks | Action queue or Tasks dashboard | read and complete a human task | automatic task mutator |
| Meta Bind | metadata footer candidate | view/edit approved low-risk fields | protected-property editor |
| Templater | invisible render boundary | render bounded new-note fields | startup or global script runner |
| Linter | manual command | normalize an approved file | save-time rewriter |
| Obsidian Git | status bar/manual review | show local status for review | auto pull/commit/push |
| Thin Client | Watch/Review context | present cited proposal or conflict | provider, retrieval, or apply authority |
| File Explorer | fallback navigation pane | recover when adapters are unavailable | hidden fallback |

Use one consistent vector icon family such as Lucide, with 16–18px stroke
icons and text labels. No emoji are structural icons. Icon-only critical
actions are disallowed.

## Implementation boundary and G03 handoff

This document defines the design boundary. The separately authorized G03 static
implementation traced the source chain below instead of editing
`KnowledgeHub/Home.md` alone:

```text
blueprint/blueprint.yaml
        ↓
ops/src/vaultops/base_dashboard.py
        ↓
KnowledgeHub/Home.md + 99_System/CSS/dashboard.css
        ↓
ops/tests/test_c08_dashboard.py and canonical checks
```

G03 changed the Home layout, CSS, and focused tests together while preserving
the existing Base query authority, QuickAdd choice IDs, Markdown/YAML fallback,
and mobile plugin-free boundary. Future slices must not add a second writer,
invent command IDs, mutate the live Mac profile, or claim that a CSS file is
loaded merely because it exists in the Vault.

Before adding body-text previews, the implementation must state which of the
following is true:

- Native Base fields already supply the intended excerpt or value.
- A canonical, read-only projection already exists and can be linked.
- A separately reviewed adapter is required because the current native
  surface cannot expose note body text.

If none is true, keep the window as a source-linked property view and mark the
body-excerpt behavior unconfigured. Do not fill the space with fabricated
copy.

## Quality gates for Figma and Obsidian

The design-review baseline combines the UI/UX Pro Max rules with the latest
Web Interface Guidelines:

- Use semantic action and navigation controls; preserve keyboard operation and
  visible focus for every interactive element.
- Maintain at least 4.5:1 text contrast, provide text or icon context for
  color states, and test light and dark surfaces independently.
- Keep the mobile flow at one column, prevent horizontal overflow, let long
  titles and identifiers wrap safely, and keep body text readable at 16px or
  larger on mobile.
- Use a single continuous scroll region. Do not hide focused content under a
  sticky toolbar or signal rail.
- Respect reduced motion; do not animate Home sections on load or make a
  state correct only after an animation callback.
- Keep labels specific and stable: `Open daily`, `Open decision`, and `Open
  source` describe the result of the action. Avoid `Continue`, duplicate
  labels, or controls that change vocabulary by pane.
- Show empty, loading, conflict, and unavailable states with a recovery route.

The web-specific source used for this review is the
[Web Interface Guidelines command](https://raw.githubusercontent.com/vercel-labs/web-interface-guidelines/main/command.md).
It is a quality baseline for the future rendering work, not permission to
turn Obsidian into a web application or to add a new UI runtime.

## G revision sequencing

### G02 — Reframe the design contract

This document records the user-requested two-pane MacBook topology, mobile
stacking, compact action hierarchy, single daily action, repository text
windows, recommended navy/slate + secure-green palette, Fira typography, and
future Figma edit frames.

### G03 — Implement the Home hierarchy and theme source

Update the C08 source owner, portable CSS, and focused tests together. Render
the two-pane Home only when the effective width supports both readable panes;
otherwise use the mobile/compact one-column flow. Do not introduce a
plugin-owned data source or repeated card grid.

### G04 — Define and verify text-window projection

Bind each text window to an existing canonical Base/property or an explicitly
approved read-only adapter. Record property-only fallback, body-excerpt
availability, freshness, and empty-state behavior separately.

### G05 — Align template reading order and the metadata footer

Preserve the schema fields and renderer ownership. Test native-top,
CSS-reflow, Meta Bind-footer, and plugin-free fallback results separately.

### G06 — Re-map contextual plugin and icon surfaces

Inspect exact installed manifests and data files, then produce a
version-aware map. Do not invent command IDs or mutate settings during the
static design pass.

### G07 — User-authorized Mac and Figma visual checks

After source implementation and Figma editing are separately authorized,
verify the Home hierarchy, text previews, action consolidation, note
readability, and Properties behavior. Record static, runtime, artifact, and
device evidence separately.

## Acceptance for G02

- **G02-A1, design:** the MacBook layout uses two readable sections and the
  mobile layout stacks the same semantic regions vertically.
- **G02-A2, interaction:** `Open daily` is defined as one visible action, and
  repeated large action controls are replaced with compact command rows.
- **G02-A3, content:** the saved Home area is assigned to three bounded,
  source-linked repository text windows with explicit empty and stale-state
  behavior.
- **G02-A4, visual:** the recommended navy/slate, secure-green, and
  `Fira Sans`/`Fira Code` combination is incorporated without turning the
  surface into a generic code or enterprise dashboard.
- **G02-A5, Figma:** the future edit frames, component vocabulary, and ordered
  edit brief are explicit without claiming that a Figma node was edited.
- **G02-A6, Obsidian:** the source chain, Base authority, frontmatter,
  plugin-free fallback, and body-text projection decision gate are explicit.
- **G02-A7, boundary:** no Figma edit, Obsidian UI operation, plugin/profile
  mutation, Vault move/delete, Git remote effect, provider effect, or
  canonical apply is performed by this documentation slice.

## Evidence and handoff

G02 evidence is static, semantic, and limited external-service access only:
the repository contract, tracked Vault sources, UI/UX guidance, the named
Figma project target, and the design reasoning above. It does not prove that a
Figma frame changed, that a CSS snippet loaded, that a text excerpt renders,
that a toolbar item works, or that Properties appear at the bottom.

G03 and the accepted G09 static source and verification slices are complete.
Figma editing and Mac/device verification remain separately authorized
follow-up work under G07. The generated Property Dictionary and C24
LaunchAgent artifacts are aligned with the current Blueprint; the previous
digest-based copies are legacy bytes rather than current contract evidence.

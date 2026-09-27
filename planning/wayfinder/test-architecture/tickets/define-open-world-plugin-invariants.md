---
title: Define open-world plugin invariants
status: resolved
assignee: codex
claimed: 2026-09-26
resolved: 2026-09-26
labels:
  - wayfinder:grilling
  - T01
parent: ../MAP.md
blocks:
  - Retire overlapping and historical test contracts
  - Set verification tiers and the regression budget
---

# Define open-world plugin invariants

## Question

For every KnowledgeOS-consumed Core or community plugin, what is the smallest
required capability and safety-critical value set, and how should diagnostics
classify additional plugins, unrelated keys, missing optional UI preferences,
version drift, and unavailable device evidence?

The answer must replace exhaustive profile equality with required-subset
semantics, preserve fail-closed checks for values that can write, execute,
network, overwrite, or bypass review, and keep static profile inspection
separate from plugin/device execution proof.

## Findings

- The Blueprint declares eleven Mac community-plugin roles and a plugin-free
  Markdown and Core fallback. The declared list is a KnowledgeOS capability
  inventory, not an exclusive allowlist for the user's profile.
- `_plugin_audit` currently marks the entire profile degraded when it observes
  any additional community plugin. P02 similarly emits an error for every
  future Core flag that lacks a KnowledgeOS policy.
- P03-P12 mix three different concerns in one error channel: KnowledgeOS-owned
  workflow requirements, global safety gates, and user-owned presentation or
  inventory snapshots. This makes harmless profile customization look like a
  KnowledgeOS defect.
- P05 and P08 read ordinary root documents, and several setting registries read
  deployed templates or generated documents. Those reads are deployment
  observations; they cannot remain inputs to default pytest.
- Current exact-equality checks include hotkey order, the number of startup
  targets, UI placement, hidden folders, file visibility, whole toolbar and
  folder-mapping inventories, all disabled Linter rule records, and complete
  relation-group equality. None of those snapshots is required to prove the
  corresponding safety boundary.
- A serialized manifest or `data.json` can establish only static configuration.
  It cannot establish that Obsidian loaded a plugin, registered a command, ran a
  workflow, or behaved correctly on a device.

## Resolution

Adopt an open-world, ownership-scoped settings contract. Default regression
tests exercise this contract only against deterministic temporary profiles.
The real profile is inspected only by an explicitly invoked read-only deployment
diagnostic.

### Policy vocabulary

Every observed field or record belongs to exactly one of these classes:

| Class | Meaning | Effect on a deployment audit |
|---|---|---|
| `required_capability` | A stable KnowledgeOS identifier or value needed by a declared workflow | Missing or incompatible state degrades that capability |
| `safety_critical` | A value that can write automatically, overwrite, execute code or commands, access the network, mutate Git, or bypass review | Invalid or enabled-forbidden state fails closed |
| `advisory` | Version, UI preference, presentation, or useful-but-nonessential state | Report without changing status or exit code |
| `user_owned` | An unrelated plugin, choice, profile, toolbar, mapping, query, setting, or document | Ignore for health; include only a bounded identifier summary when useful |
| `unmanaged` | State for which KnowledgeOS declares no capability | Do not parse its plugin files or infer safety or health |

Required-subset matching uses stable plugin, choice, profile, toolbar, field, or
workflow identifiers. Order, total count, unknown keys, and unrelated records
are never implicit requirements. An additional record becomes relevant only if
it reuses a KnowledgeOS-owned identifier, targets a protected `99_System`
surface, or activates a global safety-critical capability.

### Audit states and exit behavior

1. A missing profile remains `INACTIVE` with the plugin-free fallback available
   and exit 0.
2. A parseable profile with every declared capability present and every
   safety-critical invariant satisfied is `PASS` with exit 0. Extra plugins,
   Core flags, keys, versions, and user records do not change this result.
3. A missing declared plugin, missing KnowledgeOS-owned record, or incompatible
   required capability is `DEGRADED` with exit 4. The report names only the
   affected capability and its fallback; it does not mark unrelated capabilities
   unhealthy.
4. A malformed enabled-plugin inventory, duplicate KnowledgeOS-owned identifier,
   malformed required plugin manifest or data root, manifest ID mismatch,
   unsafe path, or violated safety-critical invariant is `FAIL` with exit 11.
5. A missing optional value is `unknown` or `not_configured`, not invalid.
   Unknown device evidence is always `not_run` or `not_inferred` and never
   changes a static audit exit code.
6. Plugin versions are advisory unless an executable capability contract names
   a concrete supported or minimum version. No such constraint may be inferred
   from the current installed version.
7. Reports expose bounded identifiers and classified values only. They do not
   echo arbitrary plugin data, commands, tokens, paths outside the profile, or
   possible secrets.

### Minimal invariant matrix

| Lane | Required KnowledgeOS subset | Fail-closed safety subset | Explicitly tolerated state |
|---|---|---|---|
| P01 plugin inventory | Membership and role for each Blueprint-declared Mac capability; manifest ID only for an installed declared plugin | Strict parse of the enabled-plugin inventory; reject duplicate owned IDs, symlinked required files, and declared-plugin ID mismatch | Additional community plugins, list order, plugin count, extra manifest keys, and version drift |
| P02 Core | Blueprint-declared Core capability IDs; enabled state for required capabilities; Daily Notes and Templates paths only where they own note creation | Reject malformed required values and unsafe or escaping creation paths | New Core flags, optional Core flags, Properties visibility, Workspaces/UI state, and unrelated serialized keys |
| P03 QuickAdd | The five Home action IDs and their Blueprint template and safe target contracts; match by ID rather than list position | Keep online/AI and developer execution disabled for the KnowledgeOS router; require create-only, no overwrite, no path escape, and reviewed template targets | Additional choices or macros, choice order, hotkeys, prompt presentation, and unrelated settings |
| P04 Templater | Canonical `99_System/Templates` folder for KnowledgeOS rendering and bounded syntax checks for system-owned weekly/monthly templates | Disable global new-file execution, system commands, shell paths, user scripts, and startup execution; reject mappings that target protected system surfaces without an owned contract | User mappings outside protected surfaces, extra keys, UI preferences, and version drift |
| P05 Tasks | `#task` convention, required status-name subset, and bounded directives only in declared system-owned query fixtures or `99_System` outputs | Reject active JavaScript/function queries and executable directives in the global or KnowledgeOS-owned query scope | Extra status types, date/editor preferences, user-note queries, root Home contents, and unrelated settings |
| P06 Linter | Protection of `99_System` from automatic formatting | Keep save/change triggers off; reject automatic or unscoped executable transformations that can touch protected surfaces | Rule count, manually enabled ordinary rules, rule options, user customizations outside protected scope, UI settings, and extra keys |
| P07 Obsidian Git | Manual local status capability when configured | Keep automatic save/commit/push/pull, boot pull, file-change backup, unattended submodule actions, and commit scripts disabled | Status-bar and menu visibility, refresh presentation, branch display, extra keys, and runtime state that static inspection cannot infer; report the Git fallback separately |
| P08 Homepage | At most one KnowledgeOS-owned startup entry matched by stable name and safe target when that capability is configured | Disable auto-create, automatic Dataview refresh, and startup commands for the owned entry; reject unsafe or executable owned targets | Additional homepages, open/view mode, pin/scroll presentation, ordinary `Home.md` content, mobile content, and user startup entries |
| P09 Breadcrumbs | One KnowledgeOS-owned relation group covering the required relation-field subset | Reject collisions that redefine owned fields, external/automatic edge sources for the owned group, transitive materialization, automatic suggestors, and automatic rebuild | Additional groups, views, commands, fields not owned by KnowledgeOS, presentation, and ordering |
| P10 Notebook Navigator | One `KnowledgeOS Mac` profile with weekly/monthly folder-pattern-template ownership and create-before-open/no-overwrite semantics | Reject path escape, conflicting duplicate owned profiles, or overwrite-on-conflict behavior | Additional profiles, `fileVisibility`, hidden folders/tags/files/properties, calendar presentation, UI state, and extra keys |
| P11 Note Toolbar | KnowledgeOS-named toolbars and mappings that target KnowledgeOS-owned contexts; validate only their reviewed action IDs and safe targets | Reject variables, executable/external targets, unverified commands, unsafe paths, and unreviewed mappings into protected surfaces for owned toolbars | Additional user toolbars and mappings outside protected contexts, desktop/mobile/tablet placement, styles, order, and extra keys |
| P12 Meta Bind | KnowledgeOS-owned input IDs and approved field subset; no write controls on protected `99_System` paths | Keep JavaScript, developer mode, restriction bypass, and unscoped executable buttons disabled; reject owned inputs that target unapproved fields or bypass single-note human review | Additional safe user inputs/views outside protected scope, folder-exclusion serialization, UI preferences, order, and extra keys |

The table defines the future validator responsibility. It does not freeze the
current serialized profile as a fixture. Blueprint/source contracts remain
testable from control-repository inputs; any deployed `99_System` existence or
parity observation belongs to the separate artifact tier.

### Test and implementation consequences

1. Replace the exclusive `expected` versus `unexpected` community-plugin model
   with `declared_capabilities` plus an informational `unmanaged_plugins` list.
   Never open unmanaged plugin manifests or data files.
2. Add a shared policy/result vocabulary so advisory and user-owned observations
   cannot enter the error list or affect `filesystem_evidence`.
3. Rename filesystem/profile evidence to `static` or `serialized_deployment`;
   retain device state as explicitly `not_run`.
4. Make every P02-P12 builder accept isolated roots and validate only the
   owned subset. Remove reads of ordinary root notes from the setting contract.
   System-owned document inputs, when needed, use deterministic fixtures in
   pytest and accepted `99_System` paths only in the separate artifact audit.
5. Rewrite P01-P12 regression cases around a synthesized minimal passing
   profile. For every lane, add one additive-invariance case proving that an
   unrelated plugin, key, record, order change, presentation preference, or
   version change still passes; add focused negative cases only for the required
   and safety-critical columns above.
6. Do not copy `.obsidian-mac`, `Home.md`, `Mobile.md`, templates, or plugin data
   from the real KnowledgeHub into fixtures. Do not run the real plugin audit
   from default pytest.
7. Treat `test_f_contracts.py` and other cross-lane exact-profile assertions as
   retirement candidates. The later retirement ticket must retain only unique
   cross-component responsibilities that are not already expressed by an owned
   invariant.

## Consequences

- Installing an unrelated community plugin, changing its settings, adding a
  future Core flag, changing a UI preference, or creating/deleting an ordinary
  note cannot fail default regression and cannot degrade an explicit static
  plugin audit.
- A user customization that collides with a KnowledgeOS-owned identifier or
  enables a global high-risk behavior remains visible and fail-closed.
- Missing enhancements remain honest capability degradation while KnowledgeOS's
  Markdown, Core, and Vaultctl fallbacks stay separately available.
- Static inspection no longer claims runtime or device health.
- [Retire overlapping and historical test contracts](retire-overlapping-and-historical-test-contracts.md)
  and the plugin portion of
  [Set verification tiers and the regression budget](set-verification-tiers-and-the-regression-budget.md)
  are now unblocked.

## Evidence

- `blueprint/blueprint.yaml#/plugin_profiles` — declares Core, Mac community,
  optional, forbidden-default, and plugin-free mobile contracts.
- `ops/src/vaultops/diagnostics.py` — currently degrades on additional plugins
  and aggregates all P02-P12 errors into one profile status.
- `ops/src/vaultops/core_settings.py` — currently rejects unregistered future
  Core flags and reads a deployed daily template.
- `ops/src/vaultops/{quickadd,templater,tasks,linter,obsidian_git,homepage,breadcrumbs,notebook_navigator,note_toolbar,meta_bind}_settings.py`
  — exposes the current required, safety, snapshot, and presentation checks
  classified by the matrix above.
- `ops/tests/test_p01_plugin_registry.py` through
  `ops/tests/test_p12_meta_bind_settings.py` plus
  `ops/tests/test_f_contracts.py` — identify the current exact-profile and
  real-Vault regression responsibilities to rewrite or retire.

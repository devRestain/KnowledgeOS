# KnowledgeOS technical contract

This annex explains the current owner boundary behind the [Blueprint](OBSIDIAN_VAULT_BLUEPRINT.md). Exact values come from `blueprint/blueprint.yaml`, `ops/config/core-adoption.json`, `ops/config/work-methods.json`, `ops/config/team-catalog.json`, `ops/policies/owner-control.json`, and `ops/schemas/mcp/capabilities.json`. Progress and verification belong only in `PROJECT_STATE.md`.

## Roots and source of truth

Core, control, independent KnowledgeHub Vault, owner State, and host Runtime are separately bound by private config v3. Canonical user documents are Markdown and flat YAML Properties in the Vault. The owner journal in State records Work admission, Graph and Profile pins, source references, effect intents, decisions, receipts, and unresolved outcomes. Runtime indexes and caches can be rebuilt from canonical inputs. A client-provided path, actor, owner, or binding cannot select a new authority.

The vault projection compiler may regenerate Bases, dashboards, templates, policy copies, and indexes from their declared sources. Generated views and search indexes do not supersede canonical notes. Source checks, schema checks, semantic checks, and generated-artifact byte checks are separate gates.

## Human path

Obsidian Core and configured community plugins handle note creation, period notes, search, Bases, links, backlinks, and tasks. The Thin Client is a presentation and Work intake adapter. It sends a bounded owner request, reads Work status and cited results, and shows Pending source text and diff. The owner authenticates the human request, records approval or rejection, and performs a separate requested canonical apply only after exact preimage checks. The plugin never edits canonical files as a substitute for owner apply.

## Agent path

One `vaultmcp` v3 server lists retrieval, bounded reading, Base/link/task queries, Work context, method, Graph proposal, artifact submission, independent assessment, action-specific Pending proposals, Gateway evidence, and diagnostics. Tool listing is descriptive. Each call checks owner policy; a Work call also checks the owner-issued WorkRun context, selected Team or Officer, Profile, method, Graph, resource scope, and effect grant. The model cannot choose an actor, filesystem root, or execution binding through tool input.

A source read verifies the owner reference, current digest and locator before returning a bounded excerpt. Candidate sets are filtered again before result publication. `ai_policy: ask` is allowed only by the configured local MCP owner policy; `deny` and confidential are excluded, and `local_only` body requires trusted local execution. An unlinked mention is a candidate, not a canonical relation. Citation verification checks reference, locator and hash; it does not establish the truth of a claim.

Mutation suggestions are action-schema validated Pending artifacts. Exact source, target preimage, policy and schema digests, intent id, and replay identity bind each proposal. A repeated identical call returns the recorded result; changed bytes or uncertain effects require reconciliation. Agents never approve, reject, apply, rebuild indexes, activate models, or run arbitrary file or shell commands through MCP.

## Work and execution

Director admits WorkSpec objectives, criteria, scope, budget and explicit pins. Each Team has one dedicated Manager; standalone Officer work has no Team or Manager identity. Managers may propose immutable Graph segments within admitted constraints. A producing Profile cannot submit the independent EvalOfficer assessment for its own output. Human approval and canonical apply are separate from Work completion.

Source and synthetic fixture checks do not establish an installed Hermes Runner. A trusted native launch must bind exact WorkRun, executor, Profile, GraphRun and source identity and recheck them per tool call. The project has a private Temporal pilot but no proven domain WorkRun execution. External Gateway exchange uses accepted references, never direct foreign Vault or State mounts.

## Operations and verification

`vaultctl` exposes maintenance checks, index management, note validation, receipts, repair, Work validation, and exact storage transitions. It does not expose note or project creation, general search or answer, provider execution, worker activation, or human proposal decisions. Use the project test container for source, schema, semantic, generated-artifact, test, lint and State checks. Native GUI, deployment, device, external service, and actual Runner evidence must be recorded separately when performed.

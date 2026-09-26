---
title: Choose the minimum plugin surface
status: resolved
labels:
  - wayfinder:research
parent: ../MAP.md
resolved: 2026-09-25
---

# Choose the minimum plugin surface

## Question

What is the smallest reliable Obsidian feature set that can provide the requested task, review, project, relationship, and dense Mac dashboard without creating a second query/runtime architecture?

## Findings

- Core [Bases](https://obsidian.md/help/bases) already provides local, property-backed views that can be filtered, sorted, limited, and embedded by view.
- Core [Base views](https://obsidian.md/help/bases/views) can be embedded with `![[File.base#View]]`, matching the repository's existing generator and semantic checks.
- The installed [Tasks plugin](https://publish.obsidian.md/tasks/) already supplies vault-wide task queries. Its official query language supports [limits](https://publish.obsidian.md/tasks/Queries/Limiting), [description filters](https://publish.obsidian.md/tasks/Queries/Filters), and [compact layout directives](https://publish.obsidian.md/tasks/Queries/Layout).
- Core [CSS snippets](https://obsidian.md/help/snippets) and Obsidian's per-note `cssclasses` mechanism can support a project-owned wide layout; a multi-column community plugin is not required.
- Breadcrumbs already maps the repository's canonical relation properties. It is useful on individual source notes, but Home should not depend on untracked device-local plugin data for its primary overview.
- Dataview would duplicate current Base responsibilities. Smart/semantic connection plugins would introduce a second embedding/index/provider lifecycle beside KnowledgeOS's review-gated proposal and retrieval contracts. TaskNotes would change the canonical task model from Markdown checkboxes.

## Resolution

Use this baseline:

1. Core Bases for all property-backed projections.
2. Installed Tasks for the bounded due-task panel.
3. Installed Homepage only to open `Home` in Reading view.
4. Project-owned scoped CSS for the 12-column Mac layout.
5. Core links, Backlinks/Outgoing Links, Graph, Canvas, and the existing Breadcrumbs installation only as drill-down tools from source notes.

Do not add Dataview, Modular CSS Layout, TaskNotes, Smart Connections, or another graph plugin in the first implementation. Reopen plugin research only when a named accepted Home signal cannot be expressed with native Bases, Tasks, Markdown, or scoped CSS.

## Consequence

The Home redesign is a contract/query/layout migration, not a plugin-installation project. Static implementation remains reproducible without network access, and runtime/device proof stays separately authorized.

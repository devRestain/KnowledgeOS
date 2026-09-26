---
title: Define the task attention contract
status: open
labels:
  - wayfinder:prototype
  - wayfinder:grilling
parent: ../MAP.md
blocked_by:
  - Approve the one-screen information budget and hierarchy
blocks:
  - Accept the implementation-ready Home contract
---

# Define the task attention contract

## Question

Should the Home task panel be navigation-only, or may its live Tasks checkboxes complete the source task directly?

## Recommended answer

Prefer navigation-only behavior because the product intent says detailed editing happens in source notes. Keep source backlinks visible, hide edit/postpone controls, and prototype a Home-scoped way to make only result checkboxes non-interactive without harming keyboard navigation or accessibility.

If that cannot be done robustly, choose one of these explicitly:

1. Accept checkbox completion as a narrow, documented exception because it still changes the canonical source Markdown task.
2. Replace the live Tasks block with a generated, noninteractive due-task projection; this is more implementation work and must not introduce Dataview solely for presentation.

## Query contract proposed for the prototype

- Include incomplete `#task` items due before tomorrow, which combines overdue and today.
- Exclude tag-only blank placeholders using a non-whitespace description filter.
- Sort by due date, then priority.
- Limit the Home result to four items while retaining the total/truncation indicator and source backlink.
- Keep the full `99_System/Dashboards/Tasks.md` as the drill-down.

## Required companion fix

Future templates must stop emitting live blank `- [ ] #task` placeholders. Replace them with comments or examples. Existing user notes are not rewritten automatically.

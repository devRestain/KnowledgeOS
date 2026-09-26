---
title: Define relationship and serendipity signals
status: resolved
labels:
  - wayfinder:resolved
parent: ../MAP.md
blocked_by:
  - Choose the Base topology for review and relation signals
blocks:
  - Accept the implementation-ready Home contract
---

# Define relationship and serendipity signals

## Question

Which deterministic signals are useful enough to prompt a new idea without pretending that recency or an AI similarity score is knowledge?

## Recommended first set

Use a maximum of two embedded views in the Home **Think & Connect** card and keep the rest as drill-down views:

1. **Open research/problem questions:** status `open` or `deciding`, oldest or due-soon first, limit three.
2. **Connection signals:** explicit contradictions first, then low/unknown-confidence knowledge and unconnected seed/incubating/testing ideas, total visible limit six in `Compass.base#Signals`.

Expose the supporting canonical fields in each view: `contradicts`, `raises`, `projects`, `related`, `confidence`, or `decision_by`. Never show a label such as “contradiction,” “gap,” or “unconnected” unless the filter can be reconstructed from those fields.

## Deliberately excluded signals

- Raw `file.mtime` as a proxy for importance.
- Automatically inferred semantic neighbors written as canonical relations.
- `file.backlinks` in a high-frequency Home query until refresh and performance behavior are accepted.
- Orphan and stale-note claims without a documented threshold and an owner action.

## Inspiration drill-down

Home links to `Compass.base#Signals`; the full Compass Base provides
`Tensions`, `Research Gaps`, `Unconnected Ideas`, `Low Confidence`, and
`Project Bridges` drill-downs. Source-note Breadcrumbs or local graph remain
available for deeper exploration. Canvas may be linked as a curated thinking
surface, but it is not the primary embedded Home relationship panel because
embedded Canvas cards do not provide a compact textual command-center view.

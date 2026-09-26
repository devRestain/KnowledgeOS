---
title: Choose the Base topology for review and relation signals
status: resolved
labels:
  - wayfinder:resolved
parent: ../MAP.md
blocked_by:
  - Approve the one-screen information budget and hierarchy
blocks:
  - Define relationship and serendipity signals
  - Accept the implementation-ready Home contract
---

# Choose the Base topology for review and relation signals

## Question

Should cross-type orientation views be added to the seven existing Base files, or should an eighth `Compass.base` own them?

## Option A — extend existing Bases

- `Decisions.base`: open decisions and research/problem questions.
- `Knowledge.base`: ideas, tensions, low-confidence claims, active MOCs.
- `Journal.base`: current weekly/monthly reviews and due Areas.

Advantages: no new required artifact. Disadvantages: file names cease to describe all contained types, and Home logic remains spread across several files.

## Option B — add `Compass.base` (recommended if the prototype stays understandable)

Give one cross-type Base these views:

- `Signals`
- `Tensions`
- `Unconnected Ideas`
- `Research Gaps`
- `Low Confidence`
- `Project Bridges`

Advantages: one coherent command-center source and clearer drill-down. Disadvantages: expands the exact required Base set, the Base compiler, semantic rules, fixtures, and exactness tests.

## Decision

Choose Option B. `Compass.base` is the eighth canonical Base and owns the
cross-type orientation views. Home embeds only `Compass.base#Signals`; the
remaining views are full-page drill-downs. The source chain, Blueprint schema,
semantic registry, fixture, and C08 exactness tests now encode this decision.

## Prototype rule

Build both structures against the populated fixture, not the sparse live Vault. Choose the topology that gives the clearest source-note destination with the least duplicated filtering logic. The choice must be encoded in Blueprint YAML and semantic mutation tests, not only in the rendered Home.

---
title: Approve the one-screen information budget and hierarchy
status: open
labels:
  - wayfinder:prototype
  - wayfinder:grilling
parent: ../MAP.md
blocks:
  - Define the task attention contract
  - Define the human-review attention contract
  - Choose the Base topology for review and relation signals
  - Choose the scoped CSS activation contract
  - Accept the implementation-ready Home contract
---

# Approve the one-screen information budget and hierarchy

## Question

Does the proposed 12-column, two-row Home allocate scarce above-the-fold space in the right order?

## Recommended answer

Accept this hierarchy as the prototype baseline:

1. A one-line orientation strip across all 12 columns.
2. First row: **Attention** at 5 columns, **Projects** at 4, **Decisions** at 3.
3. Second row: **Intake** at 5 columns, **Think & Connect** at 4, **Review Pulse** at 3.
4. A one-line source-navigation footer.

Daily and capture remain available but receive no dedicated card. Commands, hotkey legends, sync prose, and duplicate project views receive no screen budget.

## Prototype evidence required

- Reading-view screenshots at 1512×982 and 1440×900 logical viewports, default zoom, with both sidebars collapsed.
- At 1512×982, all six card titles and every P0/P1 queue must be visible without page scrolling.
- At 1440×900, the first row and all second-row titles must be visible without page scrolling; any remaining page scroll should be less than one partial viewport.
- Empty, normal, and overflow fixtures must all be rendered. The sparse live Vault is not sufficient evidence for density.

## Rejection signal

Reject or revise this hierarchy if the task/AI queue is clipped, project next actions require horizontal scrolling, empty cards dominate the page, or the relation/review row becomes decorative rather than actionable.

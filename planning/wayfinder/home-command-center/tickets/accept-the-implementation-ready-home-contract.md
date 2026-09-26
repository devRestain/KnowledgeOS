---
title: Accept the implementation-ready Home contract
status: open
labels:
  - wayfinder:grilling
parent: ../MAP.md
blocked_by:
  - Define the task attention contract
  - Define the human-review attention contract
  - Choose the Base topology for review and relation signals
  - Define relationship and serendipity signals
  - Choose the scoped CSS activation contract
---

# Accept the implementation-ready Home contract

## Question

Does the consolidated specification represent the intended Home closely enough to authorize a bounded implementation slice?

## Acceptance checklist

- The information budget, viewport targets, row limits, and fallback layout are accepted.
- Task interaction behavior is explicit.
- AI conflicts and pending review are visibly prioritized without implementation identifiers.
- Projects and next actions appear once, in the same view.
- Decision filtering no longer hides undecided open questions merely because `decision` is blank.
- Relationship and inspiration labels are reconstructable from canonical properties.
- Empty, normal, and overflow fixtures are specified.
- QuickAdd and hotkey configuration is decoupled from visible Home content.
- G-labelled GUI and D-labelled mobile plans are neither changed nor treated as authority.
- Static, container, profile, and device evidence are separated.
- The file-impact list and verification matrix are complete.

## On acceptance

Create a new implementation goal in `PROJECT_STATE.md` and execute only the accepted Home slice. Do not fold unrelated GUI, mobile, provider, sync, deployment, or plugin-installation work into that goal.

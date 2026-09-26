---
title: Choose the scoped CSS activation contract
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

# Choose the scoped CSS activation contract

## Question

How should the wide layout be scoped and activated without changing every note or depending on an untracked plugin?

## Recommended answer

Use two complementary mechanisms:

1. Nested custom callouts (`ko-home-grid`, `ko-home-attention`, and related types) so the Markdown remains intelligible in one column without CSS.
2. Register `cssclasses: [knowledgeos-home]` as an allowed Home-only schema property, then scope all layout, metadata, and toolbar rules under `.knowledgeos-home`.

Generate one canonical stylesheet and deploy a tracked Mac snippet copy. Do not change the global Properties display setting merely to optimize Home.

## Required separation of evidence

- Static evidence: Blueprint/schema allow `cssclasses`, generated CSS and snippet bytes match, and the profile contract names the snippet.
- Device evidence: the snippet is actually enabled in the user's Mac profile and Reading view renders correctly. This requires separately authorized Obsidian/profile operation.

## Toolbar and Properties recommendation

- Hide the Properties block and inline title only on Home through the scoped class.
- Remove or minimize the Home-specific sticky Note Toolbar mapping; the user already knows the commands, and Home has compact source links.
- Preserve toolbar behavior on all other notes.

## Rejected alternatives

- A global `propertiesInDocument: hidden` change, because it alters every note.
- A multi-column community plugin, because CSS already satisfies the need.
- Unscoped selectors tied to unstable Obsidian DOM structure.

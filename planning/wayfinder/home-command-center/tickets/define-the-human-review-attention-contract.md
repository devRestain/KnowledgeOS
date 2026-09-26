---
title: Define the human-review attention contract
status: open
labels:
  - wayfinder:grilling
parent: ../MAP.md
blocked_by:
  - Approve the one-screen information budget and hierarchy
blocks:
  - Accept the implementation-ready Home contract
---

# Define the human-review attention contract

## Question

What minimum information lets the user choose an AI review note without exposing implementation identifiers or silently expanding the proposal schema?

## Recommended answer

Split the queue into two bounded views so urgency is deterministic without a computed status rank:

- **Conflicts:** oldest first, limit two.
- **Pending review:** oldest first, limit three.

Display only the note link, status, and creation time in the first implementation. Do not display `proposal_id`, source hashes, model/provider details, or runtime receipts on Home.

## Follow-up decision if the prototype is too opaque

If link/title, status, and age do not explain what the user must review, add an explicitly human-oriented proposal field such as `review_action` or `review_summary` through the Blueprint, schema, template, generator, fixtures, and proposal workflow. Do not overload hashes or infer a label from body text only for Home.

## Invariant

Home never approves or applies a proposal. Review state remains human-controlled and proposal output remains non-canonical until the existing review/apply boundary is crossed.

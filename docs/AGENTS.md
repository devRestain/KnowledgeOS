# KnowledgeOS Documentation Contract

## Project Scope

- Treat `docs/` as compact agent contract indexes for KnowledgeOS.
- Keep `PROJECT_STATE.md` as the only machine-state source for project progress.

## Project Structure

- Keep source authority in `SOURCE_CONTRACT.md` and implementation flow in `IMPLEMENTATION_PLAN.md`.
- Keep architecture, runtime, operations, mobile, and decision rules in their named indexes.
- Keep current status, evidence, blockers, and handoff records in `PROJECT_STATE.md` only.
- Keep human explanations in the applicable root or directory `README.md`.

## Development Workflow

- Read `PROJECT_STATE.md` and the relevant source contract before changing an index.
- Preserve one bounded acceptance slice and remove superseded status snapshots.
- Record structural decisions in `PROJECT_STATE.md` and maintain the decision index as a static reference.
- Keep future capability descriptions separate from observed implementation evidence.

## Verification

- Compare changed indexes with Blueprint, YAML, manifests, source, tests, and runtime evidence.
- Record static and semantic document checks separately from runtime or artifact results.
- Mark stale, missing, or unexpected document references as incomplete or blocked.
- Run `git diff --check` for documentation changes before handoff.

## Handoff

- Report changed indexes, source references, evidence updates, and unresolved mismatches.
- Refresh `PROJECT_STATE.md` after document verification.
- Read relevant `README.md` files only at close when a human-readable update is required.

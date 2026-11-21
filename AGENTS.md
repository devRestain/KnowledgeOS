# KnowledgeOS Project Contract

## Project scope

- Treat this checkout as the KnowledgeOS control and source boundary.
- Bind the independent KnowledgeHub Vault, owner State (`States/Operations/knowledgeos/`) and host Runtime separately; never alias or nest them.
- Keep Operation State, Runtime and credentials outside Git and outside the Vault.
- Do not operate Obsidian GUI, plugins, profiles, devices or external transports without separate explicit authorization.

## Specification

- Treat `docs/CAPABILITIES.md` as the user-designated top-level behavior and acceptance specification, supplemented by intent in `USER_FEATURE_SPEC.md` and system constraints in `OBSIDIAN_VAULT_BLUEPRINT.md` (§0).
- Change the spec in the same change as any behavior it describes; delete superseded text instead of annotating it.
- Treat each acceptance example in `docs/CAPABILITIES.md` as an executable test through a public interface.
- Preserve Core contracts and schemas unchanged; resolve implementation gaps without weakening capability acceptance examples.
- Use `Ontology/` for domain term, relation and alignment authoring while treating the Blueprint Vault contract as the authoritative type, property and relation definition.
- Treat `Tmp/active/knowledgeos-spec-references-2026-11-19/REFERENCES.md` as non-normative reference material.
- Keep `docs/wiki/` as the owner's readable implementation guide for understanding agent-built features without reading source code; author explanations only in `docs/wiki/_source/` and never let the wiki override the source documents.
- Explain each feature's actual inputs, processing sequence, outputs, Hermes Profiles, Graph composition and Temporal boundaries from current implementation evidence, following `docs/wiki/_source/philosophy.md` instead of repeating common principles or copying specifications into feature bodies.
- Treat each wiki update as a spec review: fix conflicts between `docs/CAPABILITIES.md`, the Blueprint, `USER_FEATURE_SPEC.md` and `PROJECT_STATE.md` in the lower-precedence document in the same session, and drop answered Blueprint §9 questions.

## Development workflow

- Build in the delivery-order slices of `docs/CAPABILITIES.md`; finish one slice end to end before starting the next.
- Use container Python 3.12.8 and uv 0.8.14 (`mise.toml`) for project tooling.
- Keep tests fast, parallel-safe and behavioral; prove real Hermes, Ollama, model-route and Obsidian composition through small smokes in the real environment.

## Verification and state

- Read `PROJECT_STATE.md` as the sole machine-state source and validate it with `make state-check` after every write.
- Record static, runtime, deployment and device evidence separately; mark unrun checks accurately.
- Run `git diff --check` in every changed Git root before handoff.
- In every development session, update the source documents and wiki explanations for the changed scope, regenerate with `make wiki`, pass `make wiki-check` and review the changed pages in a browser before handoff.
- Keep README documents in Korean and directory guidance and machine state in English.

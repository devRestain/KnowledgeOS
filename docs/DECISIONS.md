# Agent Decision Index

- Read `PROJECT_STATE.md` for decision lifecycle, current scope, blockers, and handoff; this file is a compact static reference.
- Keep control, `KnowledgeHub/`, and `runtime/` physically distinct and keep control and `KnowledgeHub/` as independent Git repositories.
- Resolve Blueprint conflicts in Markdown, YAML, Whitepaper order and keep executable contracts in structured sources.
- Keep canonical evidence container-first and separate static, semantic, runtime, artifact, deployment, external-service, and device claims.
- Keep Vault writes create-only and hash-bound; keep provider output schema-constrained, proposal-only, and subject to human approval before canonical apply.
- Keep plugin-free Markdown and wikilink fallbacks available when plugins or device features are unavailable.
- Keep the E03 LaunchAgent provider-free; pursue local Ollama as the current provider lane, defer remote model routes, and keep provider promotion, local activation, plugin settings, device actions, and Git network effects behind their own gates.
- Keep learned retrieval and model promotion inactive until E02 quality, privacy, citation, staleness, latency, and resource evidence passes.
- Use `qwen3-embedding:8b-q4_K_M` as the live default embedding selection and retain `embeddinggemma:300m-qat-q8_0` only as the `emergency_resource_fallback`; use the passing temporary eligible-source C34 build as E02 promotion evidence, defer durable pointer materialization until KnowledgeOS implementation completion, and keep C39 as a separate immutable audit path.
- Accept the observed Gemma4 `24%` free-memory floor only for serial-only one-model-loaded operation with no concurrent or unattended activation; record C39 generation refusal as `not_applicable` and enforce the `0.40` unknown-query abstention threshold.
- Keep P01 and F as separate setting-state and GUI-first lanes, and implement C38 in an independent local-generation lane; apply only explicitly declared cross-lane dependencies.
- Keep Obsidian GUI as the canonical human interaction and review surface: Obsidian Core Daily Notes owns daily creation, Notebook Navigator owns weekly/monthly creation and opening, Templater renders bounded period fields, and `vaultctl note validate` checks the resulting document contract.
- Keep Home and Note Toolbar buttons as the intended primary GUI journey and retain Command Palette only as a recovery or diagnostic fallback; do not design normal user flows around command search.
- Keep one production writer per responsibility: retain the KnowledgeOS internal template engine for general typed notes and deterministic fixtures, remove the period-note production writer through F01, and keep `vaultctl note create` limited to strict general typed notes.
- Keep external automation in the `vaultctl` boundary; expose the official Obsidian CLI only through a bounded, status-only internal adapter with fixed argv, no shell, no raw pass-through, and no document or plugin control API.
- Keep Templater non-executing and proposal boundaries intact: no shell, system command, script, network, AI, Git, `vaultctl`, automatic canonical apply, or automatic AI-summary insertion from a period template.
- Keep plugin configuration, GUI execution, and device behavior as separately authorized evidence; serialized manifests and settings never prove that a plugin or GUI workflow ran successfully.
- Remove superseded rationale, resolved decisions, historical counts, and legacy session-order claims from this index; use `IMPLEMENTATION_PLAN.md` for detailed execution order.

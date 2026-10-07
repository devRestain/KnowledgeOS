# KnowledgeOS current implementation boundary

`PROJECT_STATE.md` owns current progress, checks, blockers, and handoff. This file describes the implemented source boundary and the next retirement and native-adoption sequence against current AgentFabric contracts.

## Current source

- Select Core and semantic pins from `ops/config/core-adoption.json`.
- Keep the five private roots separate and the KnowledgeHub Vault independent.
- Keep human writing and review in Obsidian, agent evidence and proposals in one `vaultmcp` v3, and `vaultctl` restricted to maintenance.
- Check exact Work, Team or Officer, method, Profile, Graph, source, and effect bindings in the owner on every MCP call.
- Use action-specific Pending proposals, independent evaluation, a recorded human decision, and a separate canonical apply.

## Completed source rebase

- The Blueprint Markdown, YAML, structural schema, semantic checks, generated artifacts, current owner policies, and MCP capability manifest agree on the v3 boundary.
- Withdrawn provider, embedding, vector execution, mobile bridge, and outdated planning source was removed. The current test inventory is `ops/tests/`.
- The generated KnowledgeHub guide, contract, Property Dictionary, and inactive Thin Client copies were updated under exact preimage checks. The read-only Vault projection check passes. This does not show installed or running plugin behavior.
- The project-owned container passed source, foundation, Blueprint, generated artifact, Core readiness, invariant, full regression, lint, and State checks. See `PROJECT_STATE.md` for the observed result.

## Next State retirement

`storage-transition.json` still pins the predeployment Runtime archive by exact inventory digest. Implement the retirement procedure in `docs/RUNTIME.md` with a disposable reference-graph fixture and an owner checkpoint before deleting the archived payload. Treat any unresolved effect or changed preimage as a stop condition.

## Next native adoption

After State retirement, bind a trusted NormalizationOfficer Runner to one exact WorkRun, Profile, GraphRun, method and source. Verify per-call MCP identity and policy, Pending replay, independent assessment, and GUI review in an isolated fixture before deploying a Web listener or installing the Thin Client. The Thin Client's concrete implementation and pointers may be replaced while keeping its conceptual owner Work and review boundary. Extend the same admission pattern to the six Teams and Gateway only after their own evidence.

## Proposal action contracts

Action schemas in `ops/actions/` and their prompts are owner method inputs. Their names do not create `vaultctl ai` routes or provider authority. The current MCP proposal tools and `ops/src/vaultops/application/mcp_effects.py` define the callable agent effects.

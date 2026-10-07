"""Operation-owned method definitions and exact Team Graph/tool grants.

This module is transport neutral. A Manager can read a method, while the
owning Operation validates the Graph and every Profile tool invocation.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from ..adapters.core import AdmissionError, canonical, digest, load_json
from ..adapters.paths import resolve_beneath
from .team_catalog import TeamCatalog
from .work import WorkContracts

_ID = re.compile(r"^[a-z][a-z0-9_-]*(?:\.[a-z0-9][a-z0-9_-]*)*$")
_VERSION = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")
_PORT_TO_TOOLS = {
    "work_read": {"work_context_read", "method_read"},
    "graph_propose": {"graph_segment_propose"},
    "artifact_export": {"knowledge_read", "artifact_read", "artifact_submit"},
    "draft_propose": {"proposal_draft_note", "proposal_project_review"},
    "blueprint_read": {"semantic_registry_read"},
    "alignment_propose": {"proposal_alignment", "proposal_link_suggestions"},
    "gateway_read": {"gateway_evidence_read"},
    "adaptation_propose": {"proposal_adaptation"},
    "vault_diagnostics": {"vault_diagnose"},
    "projection_read": {"vault_diagnose"},
    "journal_read": {"work_context_read"},
    "recovery_propose": {"proposal_recovery_plan"},
    "assessment_record": {"assessment_submit"},
    "knowledge_retrieve": {"knowledge_retrieve", "citation_verify", "link_context", "task_query", "base_view_query"},
}
_TOOLS = frozenset({"knowledge_search", "knowledge_retrieve", "proposal_inspect", "proposal_create",
                    "knowledge_read", "artifact_read", "base_view_query", "link_context", "task_query",
                    "work_context_read", "method_read", "graph_segment_propose", "artifact_submit",
                    "assessment_submit", "proposal_draft_note", "citation_verify",
                    "proposal_link_suggestions", "semantic_registry_read", "proposal_alignment",
                    "proposal_project_review", "gateway_evidence_read", "proposal_adaptation",
                    "vault_diagnose", "proposal_recovery_plan"})


def _require(condition: bool, code: str, message: str) -> None:
    if not condition:
        raise AdmissionError(code, message)


class WorkMethodCatalog:
    """Inert, versioned owner method definitions; no Profile registration."""

    def __init__(self, document: Mapping[str, Any], team_catalog: TeamCatalog) -> None:
        _require(set(document) == {"schema_version", "owner_operation_id", "methods"}
                 and document["schema_version"] == 1
                 and document["owner_operation_id"] == "knowledgeos"
                 and isinstance(document["methods"], list),
                 "METHOD_INVALID", "Method catalog header is invalid")
        self.methods: dict[str, dict[str, Any]] = {}
        self.team_catalog = team_catalog
        for method in document["methods"]:
            _require(isinstance(method, dict) and set(method) == {
                "method_id", "method_version", "purpose", "target_kind", "team_id",
                "input_kind", "result_kind", "required_stages", "required_evidence",
                "human_gate", "guidance"
            } and isinstance(method["method_id"], str) and _ID.fullmatch(method["method_id"])
                     and method["method_id"] not in self.methods
                     and isinstance(method["method_version"], str)
                     and _VERSION.fullmatch(method["method_version"])
                     and method["target_kind"] == "team"
                     and method["team_id"] in team_catalog.teams
                     and all(isinstance(method[key], str) and method[key].strip()
                             for key in ("purpose", "input_kind", "result_kind", "human_gate", "guidance"))
                     and isinstance(method["required_evidence"], list)
                     and len(method["required_evidence"]) == len(set(method["required_evidence"]))
                     and isinstance(method["required_stages"], list)
                     and len(method["required_stages"]) >= 2,
                     "METHOD_INVALID", "Method definition is missing or invalid")
            stages = method["required_stages"]
            _require(all(isinstance(stage, dict) for stage in stages)
                     and len({stage.get("stage_id") for stage in stages}) == len(stages)
                     and len({stage.get("role_id") for stage in stages}) == len(stages),
                     "METHOD_INVALID", "Method stages must have distinct identities and roles")
            for stage in stages:
                _require(isinstance(stage, dict) and set(stage) == {
                    "stage_id", "role_id", "tools", "effect_class"
                } and isinstance(stage["stage_id"], str) and _ID.fullmatch(stage["stage_id"])
                         and isinstance(stage["role_id"], str)
                         and stage["role_id"] in team_catalog.roles
                         and team_catalog.roles[stage["role_id"]]["kind"] == "specialist"
                         and isinstance(stage["tools"], list)
                         and all(isinstance(tool, str) for tool in stage["tools"])
                         and len(stage["tools"]) == len(set(stage["tools"]))
                         and set(stage["tools"]) <= _TOOLS
                         and set(stage["tools"]) <= set().union(*(
                             _PORT_TO_TOOLS.get(port, {port}) for port in
                             team_catalog.roles[stage["role_id"]]["required_owner_ports"]
                         ))
                         and stage["effect_class"] == team_catalog.roles[stage["role_id"]]["effect_class"],
                         "METHOD_INVALID", "Stage exceeds its exact Profile definition")
            _require(any(stage["effect_class"] in {"proposal_only", "read_only"} for stage in stages),
                     "METHOD_INVALID", "Method requires at least one admitted stage")
            self.methods[method["method_id"]] = dict(method)

    @classmethod
    def load(cls, control: Path) -> WorkMethodCatalog:
        return cls(load_json(resolve_beneath(control, "ops/config/work-methods.json")),
                   TeamCatalog.load(control))

    def reference(self, method_id: str) -> dict[str, str]:
        method = self.methods.get(method_id)
        _require(method is not None, "METHOD_UNAVAILABLE", "Owner method is unavailable")
        return {"owner_operation_id": "knowledgeos", "method_id": method_id,
                "method_version": method["method_version"],
                "content_digest": digest(canonical(method))}

    def admitted_method(self, spec: Mapping[str, Any], contracts: WorkContracts) -> dict[str, Any]:
        pinned = spec.get("method_contract_ref")
        _require(isinstance(pinned, Mapping), "METHOD_REQUIRED", "Team work requires an admitted method")
        current = self.reference(pinned.get("method_id", ""))
        contracts.method_binding(spec, current)
        method = self.methods[current["method_id"]]
        _require(spec["target_kind"] == method["target_kind"]
                 and spec.get("team_id") == method["team_id"],
                 "SCOPE_DENIED", "Method does not apply to this Team")
        allowed_roles = {profile["role_id"] for profile in spec["allowed_profiles"]}
        _require({stage["role_id"] for stage in method["required_stages"]} <= allowed_roles,
                 "SCOPE_DENIED", "WorkSpec omits a required method Profile")
        return method

    def validate_segment(self, *, spec: Mapping[str, Any], run: Mapping[str, Any],
                         bundle: Mapping[str, Any], contracts: WorkContracts,
                         artifacts: Mapping[str, Mapping[str, Any]]) -> None:
        method = self.admitted_method(spec, contracts)
        contracts.segment(spec=spec, run=run, artifacts=artifacts,
                          current_method_ref=self.reference(method["method_id"]), **bundle)
        nodes = {node["node_id"]: node for node in bundle["nodes"]}
        edges = bundle["edges"]
        successors = {edge["source_node_id"]: edge["target_node_id"] for edge in edges}
        _require(len(successors) == len(edges)
                 and len(edges) == len(nodes) - 1
                 and all(edge["condition"] == "always" for edge in edges),
                 "METHOD_STAGE_DENIED", "Method Graph must be one ordered path")
        assignments = {item["node_id"]: item for item in bundle["assignments"]}
        cursor = bundle["graph_spec"]["start_node_id"]
        selected: list[Mapping[str, Any]] = []
        while cursor in successors:
            cursor = successors[cursor]
            if nodes[cursor]["node_kind"] == "task":
                selected.append(assignments[cursor])
        _require(len(selected) == len(method["required_stages"]),
                 "METHOD_STAGE_DENIED", "Method Graph has unexpected task nodes")
        _require([item["role_id"] for item in selected]
                 == [stage["role_id"] for stage in method["required_stages"]],
                 "METHOD_STAGE_DENIED", "Graph omits, reorders, or adds method stages")

    def allowed_tools(self, spec: Mapping[str, Any], role_id: str,
                      contracts: WorkContracts) -> tuple[str, ...]:
        method = self.admitted_method(spec, contracts)
        if role_id == self.team_catalog.teams[method["team_id"]]["manager_role_id"]:
            return ("work_context_read", "method_read", "graph_segment_propose")
        for stage in method["required_stages"]:
            if role_id == stage["role_id"]:
                return tuple(stage["tools"])
        raise AdmissionError("SCOPE_DENIED", "Profile has no admitted method tool grant")

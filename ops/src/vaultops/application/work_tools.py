"""Trusted Runner view of an admitted Team method and Profile tool subset."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from ..adapters.core import AdmissionError
from .knowledge import KnowledgeApplication
from .team_catalog import TeamCatalog
from .work import PROFILE_KEYS, WorkContracts
from .work_methods import WorkMethodCatalog

if TYPE_CHECKING:
    from .exops import KnowledgeExOps


def _require(condition: bool, code: str, message: str) -> None:
    if not condition:
        raise AdmissionError(code, message)


@dataclass(frozen=True)
class TeamToolSession:
    """One Runner-created context; model arguments never select its Work or actor."""

    application: KnowledgeApplication
    work_run_id: str
    executor_id: str
    graph_run_id: str | None = None
    exops: KnowledgeExOps | None = None

    def _current(self) -> tuple[dict[str, Any], dict[str, Any], tuple[str, ...]]:
        self.application.guard("inspect")
        journal = self.application.journal.read()
        record = journal["intents"].get("exops.work." + self.work_run_id)
        _require(isinstance(record, dict) and record.get("state") not in {"unknown", "rejected"},
                 "UNAVAILABLE", "Owner WorkRun is unavailable")
        spec, run = record["spec"], record["run"]
        contracts = WorkContracts(self.application.core)
        contracts.run(run, spec)
        methods = WorkMethodCatalog.load(self.application.roots.control)
        method = methods.admitted_method(spec, contracts)
        _require(spec["target_kind"] == "team", "SCOPE_DENIED", "Method tool session needs Team work")
        team = TeamCatalog.load(self.application.roots.control)
        manager_role = team.teams[method["team_id"]]["manager_role_id"]
        if self.graph_run_id is None:
            _require(self.executor_id == spec["manager_executor_id"]
                     and run["state"] in {"admitted", "running"}
                     and not run["unresolved_effect_ids"],
                     "SCOPE_DENIED", "Only the admitted Manager may read this method")
            return record, method, methods.allowed_tools(spec, manager_role, contracts)
        _require(record["application_state"] == "started"
                 and run.get("active_graph_run_id") == self.graph_run_id
                 and run["state"] == "running"
                 and not run["pending_action_ids"] and not run["unresolved_effect_ids"],
                 "STALE_REVISION", "Profile tool call has no active, effect-safe GraphRun")
        admitted = record.get("admitted_method_segments", {}).get(self.graph_run_id)
        _require(isinstance(admitted, dict)
                 and admitted["method_contract_ref"] == spec["method_contract_ref"]
                 and admitted["team_catalog_digest"] == team.digest,
                 "PIN_MISMATCH", "Admitted Graph method or Team catalog changed")
        selected = [item for item in admitted["assignments"] if item["executor_id"] == self.executor_id]
        _require(len(selected) == 1 and selected[0]["assignment_generation"] == run["generation"],
                 "SCOPE_DENIED", "Profile has no exact active Graph assignment")
        role_id = selected[0]["role_id"]
        return record, method, methods.allowed_tools(spec, role_id, contracts)

    def tools(self) -> tuple[str, ...]:
        return self._current()[2]

    def method_view(self) -> dict[str, Any]:
        record, method, allowed = self._current()
        _require(self.graph_run_id is None and "method_read" in allowed,
                 "SCOPE_DENIED", "Only Manager may read the method view")
        return {"method_contract_ref": record["spec"]["method_contract_ref"],
                "purpose": method["purpose"], "target_kind": method["target_kind"],
                "team_id": method["team_id"], "input_kind": method["input_kind"],
                "result_kind": method["result_kind"],
                "required_stages": method["required_stages"],
                "required_evidence": method["required_evidence"],
                "human_gate": method["human_gate"], "guidance": method["guidance"]}

    def work_view(self) -> dict[str, Any]:
        record, method, allowed = self._current()
        _require("work_context_read" in allowed, "SCOPE_DENIED", "Profile has no Work context grant")
        spec, run = record["spec"], record["run"]
        return {"work_run_id": self.work_run_id, "work_spec_digest": spec["spec_digest"],
                "work_spec_revision": spec["work_spec_revision"],
                "criteria_revision": spec["criteria_revision"],
                "acceptance_criteria": spec["acceptance_criteria"],
                "team_id": method["team_id"], "method_contract_ref": spec["method_contract_ref"],
                "source_reference": record.get("method_source_reference"),
                "graph_run_id": self.graph_run_id,
                "run_state": run["state"], "allowed_tools": list(allowed)}

    def proposal_binding(self, source_reference: Mapping[str, Any]) -> dict[str, Any]:
        record, _method, _allowed = self._current()
        _require(self.graph_run_id is not None
                 and record["admitted_method_segments"][self.graph_run_id]["source_reference"] == source_reference,
                 "SCOPE_DENIED", "Proposal differs from admitted Graph source")
        return {"work_run_id": self.work_run_id,
                "work_spec_digest": record["spec"]["spec_digest"],
                "graph_run_id": self.graph_run_id, "executor_id": self.executor_id,
                "source_reference": dict(source_reference)}

    def admitted_source(self) -> dict[str, Any]:
        record, _method, _allowed = self._current()
        _require(self.graph_run_id is not None, "SCOPE_DENIED", "An active Graph is required")
        return dict(record["admitted_method_segments"][self.graph_run_id]["source_reference"])

    def propose_graph(self, arguments: Mapping[str, Any]) -> tuple[dict[str, Any], int]:
        self.authorize("graph_segment_propose", arguments)
        if self.exops is None:
            raise AdmissionError("UNAVAILABLE", "Trusted owner Graph binding is not configured")
        record, _method, _allowed = self._current()
        result = self.exops.admit_method_segment(
            self.work_run_id, manager_executor_id=self.executor_id,
            bundle=arguments["bundle"], artifacts=arguments["artifacts"],
            source_reference=record["method_source_reference"])
        return {"status": "PASS", "data": result, "execution_outcome": "completed",
                "acceptance_state": "not_evaluated"}, 0

    def authorize(self, tool_name: str, arguments: Mapping[str, Any]) -> None:
        record, _method, allowed = self._current()
        _require(tool_name in allowed, "SCOPE_DENIED", "Profile tool exceeds its admitted grant")
        if tool_name in {"method_read", "work_context_read"}:
            _require(not arguments, "INVALID_ARGUMENT", "Method view has no caller-selected target")
            return
        if tool_name == "graph_segment_propose":
            _require(self.graph_run_id is None, "SCOPE_DENIED", "Only the Manager may propose Graph segments")
            return
        _require(self.graph_run_id is not None,
                 "SCOPE_DENIED", "Profile has no active Graph assignment")
        admitted = record["admitted_method_segments"][self.graph_run_id]
        source = admitted["source_reference"]
        if "source_reference" in arguments:
            _require(arguments["source_reference"] == source,
                     "SCOPE_DENIED", "Source differs from the admitted Graph input")
        if "resource_reference" in arguments:
            _require(arguments["resource_reference"] == source,
                     "SCOPE_DENIED", "Resource differs from the admitted Graph input")
        if "proposal_reference" in arguments or "artifact_reference" in arguments:
            raise AdmissionError("SCOPE_DENIED", "Profile artifact access needs an admitted artifact binding")
        self.application.resolve_reference(dict(source))


@dataclass(frozen=True)
class OfficerToolSession:
    """Owner-checked view of one admitted standalone Officer Graph effect."""

    application: KnowledgeApplication
    work_run_id: str
    executor_id: str
    graph_run_id: str

    def _current(self) -> dict[str, Any]:
        self.application.guard("inspect")
        journal = self.application.journal.read()
        record = journal["intents"].get("exops.work." + self.work_run_id)
        _require(isinstance(record, dict) and record.get("application_state") == "started",
                 "STALE_REVISION", "Officer Work is not active")
        spec, run = record["spec"], record["run"]
        WorkContracts(self.application.core).run(run, spec)
        _require(spec["target_kind"] == "officer"
                 and spec["officer_executor_id"] == self.executor_id
                 and run["state"] == "running"
                 and run.get("active_graph_run_id") == self.graph_run_id
                 and not run["pending_action_ids"] and not run["unresolved_effect_ids"],
                 "SCOPE_DENIED", "Officer lacks the active effect-safe Graph")
        catalog = TeamCatalog.load(self.application.roots.control)
        profile = spec["allowed_profiles"][0]
        _require(profile == catalog.profile("knowledgeos.normalization_officer")
                 and "proposal_create" in catalog.roles[profile["role_id"]]["required_owner_ports"],
                 "PIN_MISMATCH", "Officer Profile has no exact proposal grant")
        admitted = record.get("admitted_officer_segments", {}).get(self.graph_run_id)
        _require(isinstance(admitted, dict)
                 and admitted["assignment"]["executor_id"] == self.executor_id
                 and all(admitted["assignment"][key] == profile[key] for key in PROFILE_KEYS)
                 and admitted["assignment"]["assignment_generation"] == run["generation"]
                 and admitted["segment"]["work_spec_digest"] == spec["spec_digest"]
                 and admitted["source_reference"] == record["officer_source_reference"],
                 "PIN_MISMATCH", "Officer Graph, Profile or Capture pin changed")
        self.application.resolve_reference(dict(admitted["source_reference"]))
        return record

    def tools(self) -> tuple[str, ...]:
        self._current()
        return ("work_context_read", "knowledge_read", "proposal_create")

    def work_view(self) -> dict[str, Any]:
        record = self._current()
        return {"work_run_id": self.work_run_id,
                "work_spec_digest": record["spec"]["spec_digest"],
                "source_reference": record["officer_source_reference"],
                "graph_run_id": self.graph_run_id,
                "allowed_tools": list(self.tools())}

    def admitted_source(self) -> dict[str, Any]:
        record = self._current()
        return dict(record["officer_source_reference"])

    def authorize(self, tool_name: str, arguments: Mapping[str, Any]) -> None:
        record = self._current()
        admitted = record["admitted_officer_segments"][self.graph_run_id]
        if tool_name == "work_context_read":
            _require(not arguments, "INVALID_ARGUMENT", "Work context has no caller target")
            return
        if tool_name == "knowledge_read":
            _require(arguments.get("resource_reference") == admitted["source_reference"],
                     "SCOPE_DENIED", "Officer read differs from admitted source")
            return
        _require(tool_name == "proposal_create"
                 and set(arguments) == {"source_reference"}
                 and arguments["source_reference"] == admitted["source_reference"],
                 "SCOPE_DENIED", "Officer tool or source differs from admitted Graph")

    def create_proposal(self, arguments: Mapping[str, Any]) -> tuple[dict[str, Any], int]:
        self.authorize("proposal_create", arguments)
        record = self._current()
        return self.application.create_normalize_proposal(
            source_reference=dict(arguments["source_reference"]),
            work_binding={
                "work_run_id": self.work_run_id,
                "work_spec_digest": record["spec"]["spec_digest"],
                "graph_run_id": self.graph_run_id,
                "executor_id": self.executor_id,
                "source_reference": dict(arguments["source_reference"]),
            })


@dataclass(frozen=True)
class EvalToolSession:
    """Independent evaluator selected by the trusted Runner startup binding."""

    application: KnowledgeApplication
    work_run_id: str
    executor_id: str

    def _current(self) -> dict[str, Any]:
        self.application.guard("inspect")
        record = self.application.journal.read()["intents"].get("exops.work." + self.work_run_id)
        _require(isinstance(record, dict) and record.get("application_state") == "started",
                 "STALE_REVISION", "EvalOfficer Work is unavailable")
        spec, run = record["spec"], record["run"]
        WorkContracts(self.application.core).run(run, spec)
        _require(spec["eval_officer_executor_id"] == self.executor_id
                 and self.executor_id != spec.get("manager_executor_id")
                 and run["state"] in {"running", "awaiting_human"}
                 and not run["unresolved_effect_ids"],
                 "SCOPE_DENIED", "Only the independent EvalOfficer may assess this Work")
        return record

    def tools(self) -> tuple[str, ...]:
        self._current()
        return ("work_context_read", "artifact_read", "assessment_submit")

    def work_view(self) -> dict[str, Any]:
        record = self._current()
        spec, run = record["spec"], record["run"]
        return {"work_run_id": self.work_run_id, "work_spec_digest": spec["spec_digest"],
                "criteria_revision": spec["criteria_revision"],
                "acceptance_criteria": spec["acceptance_criteria"],
                "graph_run_ids": run["graph_run_ids"], "run_state": run["state"]}

    def authorize(self, tool_name: str, arguments: Mapping[str, Any]) -> None:
        self._current()
        _require(tool_name in self.tools(), "SCOPE_DENIED", "EvalOfficer has no such grant")
        if tool_name == "work_context_read":
            _require(not arguments, "INVALID_ARGUMENT", "Work context has no selector")
        if tool_name == "artifact_read":
            reference = arguments["artifact_reference"]
            record = self._current()
            _require(reference in record.get("submitted_artifacts", []),
                     "SCOPE_DENIED", "Artifact is not admitted to this Work")

    def submit_assessment(self, arguments: Mapping[str, Any]) -> tuple[dict[str, Any], int]:
        self.authorize("assessment_submit", arguments)
        app = self.application
        app.guard("propose")
        with app.journal.writer() as writer:
            record = writer.value["intents"].get("exops.work." + self.work_run_id)
            _require(record is not None and record == self._current(),
                     "STALE_REVISION", "Work changed during assessment")
            spec, run = record["spec"], record["run"]
            segments = [item["segment"] for item in record.get("admitted_method_segments", {}).values()]
            segments += [item["segment"] for item in record.get("admitted_officer_segments", {}).values()]
            assessment = arguments["assessment"]
            _require(arguments["artifacts"] == record.get("artifact_references", {}),
                     "SCOPE_DENIED", "Assessment artifact snapshot differs from owner evidence")
            WorkContracts(app.core).assessment(
                assessment, spec, run, segments=segments,
                evaluations=arguments["evaluations"], artifacts=arguments["artifacts"],
                checkpoints=arguments["checkpoints"], candidate_assessor_id=self.executor_id)
            key = arguments["idempotency_key"]
            if not isinstance(key, str) or not 1 <= len(key) <= 500:
                raise AdmissionError("INVALID_ARGUMENT", "Assessment idempotency key is invalid")
            existing = record.setdefault("assessments", {}).get(assessment["assessment_id"])
            value = {"assessment": dict(assessment), "idempotency_key": key,
                     "evaluator_id": self.executor_id}
            if existing is not None:
                _require(existing == value, "IDEMPOTENCY_CONFLICT", "Assessment identity changed")
                return {"status": "NO_OP", "data": {"assessment_id": assessment["assessment_id"],
                        "replayed": True}, "execution_outcome": "completed",
                        "acceptance_state": "not_evaluated"}, 0
            record["assessments"][assessment["assessment_id"]] = value
            writer.value["history"].append({"event": "exops_assessment_submitted",
                                           "work_run_id": self.work_run_id,
                                           "sequence": writer.value["revision"] + 1})
            writer.commit(app.clock())
            return {"status": "PASS", "data": {"assessment_id": assessment["assessment_id"],
                    "replayed": False}, "execution_outcome": "completed",
                    "acceptance_state": "not_evaluated"}, 0

"""Trusted Runner view of an admitted Team method and Profile tool subset."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from ..adapters.core import AdmissionError
from ..retrieval import _chunks_for_note
from .knowledge import KnowledgeApplication
from .read_tools import KnowledgeReadTools
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
        from .owner_authority import validate_owner_authority

        validate_owner_authority(self.application, journal, record)
        contracts = WorkContracts(self.application.core)
        contracts.run(run, spec)
        methods = WorkMethodCatalog.load(self.application.roots.control)
        method = methods.admitted_method(spec, contracts)
        _require(spec["target_kind"] == "team", "SCOPE_DENIED", "Method tool session needs Team work")
        if (record.get("control_checkpoint") is True and run["graph_run_ids"]
                and (self.graph_run_id is None
                     or self.graph_run_id != run["graph_run_ids"][0])):
            from .curation_control import validate_control_release

            validate_control_release(self.application, journal, record)
            if self.exops is not None:
                control = getattr(self.exops, "curation_control", None)
                _require(control is not None, "UNAVAILABLE", "Owner control binding is unavailable")
                control._authorize_successor(journal, record)
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
                "human_gate": method["human_gate"], "guidance": method["guidance"],
                **({"segment_stages": method["segment_stages"]} if "segment_stages" in method else {})}

    def work_view(self) -> dict[str, Any]:
        record, method, allowed = self._current()
        _require("work_context_read" in allowed, "SCOPE_DENIED", "Profile has no Work context grant")
        spec, run = record["spec"], record["run"]
        source = record.get("method_source_reference")
        chunks = []
        if source is not None:
            reader = KnowledgeReadTools(self.application)
            path = self.application.resolve_reference(dict(source))
            _, notes = reader._eligible(selected_path=path)
            chunks = [chunk.locator for chunk in _chunks_for_note(reader._note(source, notes))]
        return {"work_run_id": self.work_run_id, "work_spec_digest": spec["spec_digest"],
                "work_spec_revision": spec["work_spec_revision"],
                "criteria_revision": spec["criteria_revision"],
                "acceptance_criteria": spec["acceptance_criteria"],
                "team_id": method["team_id"], "method_contract_ref": spec["method_contract_ref"],
                "source_reference": record.get("method_source_reference"),
                "source_chunks": chunks, "executor_id": self.executor_id,
                "generation": run["generation"], "fencing_token": run["fencing_token"],
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
        method_id = record["spec"].get("method_contract_ref", {}).get("method_id")
        criteria = [item["criterion_id"] for item in record["spec"]["acceptance_criteria"]]
        native_metadata = {
            "knowledgeos.note_metadata_team": ("note_metadata", "coverage_evidence"),
            "knowledgeos.relation_ontology_team": ("relation_ontology", "relation_evidence"),
            "knowledgeos.daily_corpus_stewardship_team": ("daily_corpus", "coverage_evidence"),
        }.get(method_id)
        if native_metadata is not None and criteria == ["criterion.native_" + native_metadata[0]]:
            if tool_name == "semantic_registry_read":
                _require(dict(arguments) == {"category": "relations"},
                         "SCOPE_DENIED", "Native metadata requires the exact relation registry")
            elif tool_name == "link_context":
                _require(dict(arguments) == {"resource_reference": source, "limit": 20},
                         "SCOPE_DENIED", "Native relation evidence requires exact selected link context")
            elif tool_name == "artifact_submit":
                import json

                from .metadata_work import KINDS, validate_candidate
                from .work import record_digest

                execution = record.get(native_metadata[0] + "_" + native_metadata[1], {})
                _require(execution.get("evidence_digest") == record_digest({
                    "request": execution.get("request"), "response": execution.get("response")}),
                    "SCOPE_DENIED", "Candidate writer lacks immutable completed source evidence")
                _require(arguments.get("artifact_kind") == KINDS[method_id],
                         "SCOPE_DENIED", "Native metadata artifact kind differs")
                try:
                    candidate = json.loads(arguments.get("content", ""))
                except (ValueError, TypeError) as error:
                    raise AdmissionError("INVALID_ARGUMENT", "Metadata artifact must be JSON") from error
                link_evidence = (execution["response"]["tool_calls"][-1]["result"]["data"]
                                 if native_metadata[0] == "relation_ontology" else None)
                validate_candidate(self.application, source, candidate, method_id=method_id,
                                   link_evidence=link_evidence)
        if ((method_id == "knowledgeos.cited_draft_team" and criteria == ["criterion.cited_draft"])
                or (method_id == "knowledgeos.project_review_team" and criteria == ["criterion.project_review"])):
            import json

            from .work import record_digest

            try:
                scope = json.loads(record["spec"]["objective"])
            except (ValueError, TypeError) as error:
                raise AdmissionError("SCOPE_DENIED", "Authoring scope pins are unavailable") from error
            path = self.application.resolve_reference(dict(source))
            if tool_name == "task_query":
                _require(dict(arguments) == {"path_prefix": path, "limit": 20},
                         "SCOPE_DENIED", "Task query differs from the admitted project")
            elif tool_name == "base_view_query":
                _require(dict(arguments) == {"base": scope["base"], "view": scope["view"],
                                            "filters": {}, "limit": 20},
                         "SCOPE_DENIED", "Base query differs from admitted project pins")
            elif tool_name == "knowledge_retrieve":
                from ..projection import load_current_projection

                generation = load_current_projection(self.application.roots, verify_sources=True).manifest["generation_id"]
                _require(dict(arguments) == {"query": scope["query"], "hops": 0,
                                            "expected_generation_id": generation, "limit": 20},
                         "SCOPE_DENIED", "Retrieval differs from admitted project pins")
            elif tool_name in {"proposal_draft_note", "proposal_project_review"}:
                key = ("cited_draft_source_evidence" if tool_name == "proposal_draft_note"
                       else "project_review_project_evidence")
                evidence = record.get(key, {})
                _require(evidence.get("evidence_digest") == record_digest({
                    "request": evidence.get("request"), "response": evidence.get("response")}),
                    "SCOPE_DENIED", "Writer lacks immutable completed source evidence")
                calls = evidence["response"]["tool_calls"]
                if tool_name == "proposal_draft_note":
                    body = arguments.get("draft_body")
                    _require(set(arguments) == {"source_reference", "target_path", "target_type", "title", "draft_body"}
                             and all(arguments.get(k) == scope[k] for k in ("target_path", "target_type", "title"))
                             and isinstance(body, str) and source["content_digest"] in body
                             and "[[" + path + "]]" in body
                             and all(c["result"]["data"]["chunk_locator"] in body
                                     and c["result"]["data"]["chunk_hash"] in body for c in calls),
                             "SCOPE_DENIED", "Draft changed target pins or omitted actual source citations")
                else:
                    try:
                        plan = json.loads(arguments.get("content", ""))
                    except (ValueError, TypeError) as error:
                        raise AdmissionError("INVALID_ARGUMENT", "Project review must be JSON") from error
                    retrieval = calls[0]["result"]["data"]["candidates"]
                    tasks = calls[2]["result"]["data"]["tasks"]
                    citations = [{k: c[k] for k in ("resource_reference", "chunk_locator", "chunk_hash")} for c in retrieval]
                    citations += [{k: task[k] for k in ("resource_reference", "locator", "line_sha256")} for task in tasks]
                    observations = plan.get("observations") if isinstance(plan, dict) else None
                    _require(isinstance(observations, list) and all(isinstance(item, str) for item in observations)
                             and plan.get("citations") == citations
                             and all(scope[k] in "\n".join(observations) for k in ("period_start", "period_end"))
                             and all(task["locator"] in "\n".join(observations)
                                     and task["line_sha256"] in "\n".join(observations) for task in tasks),
                             "SCOPE_DENIED", "Review changed admitted period or exact task/source evidence")
        if method_id == "knowledgeos.knowledge_exchange_team" and tool_name in {
                "gateway_evidence_read", "proposal_adaptation"}:
            import json

            from .boundary_contract import (
                exchange_evidence,
                exchange_receipt_id,
                validate_boundary_plan,
            )

            receipt_id = exchange_receipt_id(record["spec"]["objective"])
            exchange_evidence(self.application, receipt_id)
            if tool_name == "gateway_evidence_read":
                _require(arguments.get("receipt_id") == receipt_id,
                         "SCOPE_DENIED", "Gateway read differs from the admitted exchange")
            else:
                try:
                    payload = json.loads(arguments.get("content", ""))
                except (ValueError, TypeError) as error:
                    raise AdmissionError("INVALID_ARGUMENT", "Adaptation content must be JSON") from error
                validate_boundary_plan(self.application, tool_name, source, payload, receipt_id=receipt_id)
        if method_id == "knowledgeos.vault_maintenance_team" and tool_name == "proposal_recovery_plan":
            import json

            from .boundary_contract import validate_boundary_plan

            try:
                payload = json.loads(arguments.get("content", ""))
            except (ValueError, TypeError) as error:
                raise AdmissionError("INVALID_ARGUMENT", "Recovery content must be JSON") from error
            validate_boundary_plan(self.application, tool_name, source, payload)
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
        from .owner_authority import validate_owner_authority

        validate_owner_authority(self.application, journal, record)
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
        source = record["officer_source_reference"]
        reader = KnowledgeReadTools(self.application)
        path = self.application.resolve_reference(dict(source))
        _, notes = reader._eligible(selected_path=path)
        note = reader._note(source, notes)
        return {"work_run_id": self.work_run_id,
                "work_spec_digest": record["spec"]["spec_digest"],
                "source_reference": source,
                "source_chunks": [chunk.locator for chunk in _chunks_for_note(note)],
                "executor_id": self.executor_id,
                "generation": record["run"]["generation"],
                "fencing_token": record["run"]["fencing_token"],
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
        journal = self.application.journal.read()
        record = journal["intents"].get("exops.work." + self.work_run_id)
        _require(isinstance(record, dict) and record.get("application_state") == "started",
                 "STALE_REVISION", "EvalOfficer Work is unavailable")
        spec, run = record["spec"], record["run"]
        from .owner_authority import validate_owner_authority

        validate_owner_authority(self.application, journal, record)
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

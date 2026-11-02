"""Trusted Director-side WorkSpec construction for the two sidebar requests.

Only a trusted composition may call this with a receipt issuer backed by its
Director admission boundary. The browser supplies no method, Profile, Graph,
criterion or budget fields.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Mapping
from datetime import datetime, timedelta
from typing import Any

from ..adapters.core import AdmissionError
from .work import work_spec_digest
from .work_methods import WorkMethodCatalog

_TEMPLATES = {
    "note_metadata": (
        ("corpus-stewardship", "knowledgeos.note_metadata_team",
         "Find only missing properties and new tags in this exact note",
         ("Cited missing fields preserve every populated value",
          "Every suggestion binds the source and Blueprint registry")),
        ("relation-ontology", "knowledgeos.relation_ontology_team",
         "Find evidence-backed relation and ontology candidates for this exact note",
         ("Every relation follows the current Blueprint predicate and target type",
          "Uncertain links remain proposals rather than asserted truth")),
    ),
    "document_enhancement": (
        ("evidence-research", "knowledgeos.cited_research_team",
         "Produce a cited research report to strengthen this exact document",
         ("Improvement points cite current owner evidence and index generation",
          "Uncertainty and missing material are explicit; no document mutation")),
    ),
}


def build_template_admissions(exops: Any, template_id: str, scope: Mapping[str, str],
                              request_id: str, *,
                              issue_director_receipt: Callable[[str, str, Mapping[str, str], str], str],
                              native_research_runner_binding: Mapping[str, str] | None = None,
                              native_metadata_runner_binding: Mapping[str, str] | None = None,
                              now: datetime | None = None) -> list[tuple[dict[str, Any], dict[str, Any], str,
                                                                       datetime, dict[str, Any]]]:
    """Pin method/Team/Profile/criteria/budget before owner group admission."""
    if template_id not in _TEMPLATES or not callable(issue_director_receipt):
        raise AdmissionError("ADMISSION_REQUIRED", "Trusted template Director binding is unavailable")
    observed = now or exops.application.clock()
    if observed.tzinfo is None:
        raise AdmissionError("CLOCK_INVALID", "Director admission clock must be timezone aware")
    source = exops.application.reference(scope["path"])
    exops.application._mcp_source_policy(scope["path"])
    if source["source_revision"] != scope["content_sha256"]:
        raise AdmissionError("RESOURCE_STALE", "Template source changed before Director admission")
    methods = WorkMethodCatalog.load(exops.application.roots.control)
    pins = exops.application.core.binding
    pinned = {"core_version": pins["core_version"], "core_digest": pins["core_digest"],
              "schema_version": pins["schema_version_pin"],
              "semantic_version": pins["semantic_version"],
              "semantic_digest": pins["semantic_digest"]}
    admissions = []
    for team_id, method_id, objective, criteria in _TEMPLATES[template_id]:
        binding = exops.teams.get(team_id)
        if binding is None or exops.current_team_binding(team_id) != binding:
            raise AdmissionError("BINDING_INVALID", "Template Team has no current owner registration")
        method = methods.methods[method_id]
        specialists = {item.role_id: item for item in binding.specialists}
        if any(stage["role_id"] not in specialists for stage in method["required_stages"]):
            raise AdmissionError("BINDING_INVALID", "Template Team lacks a required specialist")
        identity = hashlib.sha256((request_id + ":" + team_id).encode()).hexdigest()[:24]
        admission_id = issue_director_receipt(template_id, team_id, scope, request_id)
        if not isinstance(admission_id, str) or not admission_id:
            raise AdmissionError("ADMISSION_REQUIRED", "Director did not issue a Work admission receipt")
        work_id = "work.template." + identity
        work_run_id = "workrun.template." + identity
        if template_id == "document_enhancement" and native_research_runner_binding is not None:
            from .research_work import research_admission

            spec, run = research_admission(
                exops.application, binding, source, work_id=work_id, work_run_id=work_run_id,
                admission_receipt_id=admission_id, runner_binding=native_research_runner_binding,
                question=objective,
            )
            admissions.append((spec, run, "action.template." + identity,
                               observed + timedelta(hours=1), source))
            continue
        if template_id == "note_metadata" and native_metadata_runner_binding is not None:
            from .metadata_work import metadata_admission

            spec, run = metadata_admission(
                exops.application, binding, source, method_id=method_id,
                work_id=work_id, work_run_id=work_run_id,
                admission_receipt_id=admission_id, runner_binding=native_metadata_runner_binding,
                objective=objective,
            )
            admissions.append((spec, run, "action.template." + identity,
                               observed + timedelta(hours=1), source))
            continue
        spec = {"owner_operation_id": "knowledgeos", "work_id": work_id,
                "work_spec_revision": 1, "criteria_revision": 1, "objective": objective,
                "constraints": "Use only owner-admitted local note and corpus evidence; submit an artifact, never apply a note",
                "acceptance_criteria": [
                    {"criterion_id": f"criterion.{number}", "description": description}
                    for number, description in enumerate(criteria, 1)],
                "target_kind": "team", "team_id": team_id,
                "allowed_executor_ids": [specialists[stage["role_id"]].executor_id
                                         for stage in method["required_stages"]],
                "manager_executor_id": binding.manager_executor_id,
                "eval_officer_executor_id": binding.eval_officer_executor_id,
                "allowed_profiles": [dict(specialists[stage["role_id"]].profile)
                                     for stage in method["required_stages"]],
                "explicit_assignments": [], "delegation_mode": "manager_bounded",
                "continuation_policy": {"max_segments": 2, "max_repeated_gap_assessments": 1,
                                        "max_profile_invocations": 4},
                "execution_profile": "sequential_graph",
                "admission_receipt_id": admission_id,
                "steerable_parameter_ids": [],
                "method_contract_ref": methods.reference(method_id), **pinned}
        spec["spec_digest"] = work_spec_digest(spec)
        run = {"owner_operation_id": "knowledgeos", "work_id": work_id,
               "work_run_id": work_run_id, "work_spec_revision": 1,
               "work_spec_digest": spec["spec_digest"], "revision": 1,
               "control_revision": 1, "generation": 1,
               "fencing_token": "fence.template." + identity, "state": "admitted",
               "graph_run_ids": [], "segments_started": 0, "repeated_gap_count": 0,
               "profile_invocations": 0, "pending_action_ids": [],
               "unresolved_effect_ids": [], "binding_reference": dict(binding.binding_reference),
               **pinned}
        admissions.append((spec, run, "action.template." + identity,
                           observed + timedelta(hours=1), source))
    return admissions

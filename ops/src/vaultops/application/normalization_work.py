"""One bounded Capture normalization Work and fresh independent evaluation."""

from __future__ import annotations

import base64
import copy
import re
from collections.abc import Mapping
from datetime import datetime
from itertools import pairwise
from pathlib import Path
from typing import Any

from ..adapters.core import AdmissionError, canonical, digest
from ..adapters.owner_journal import read_regular, stamp
from ..adapters.paths import resolve_beneath
from ..projection import source_properties
from .exops import KnowledgeExOps
from .knowledge import KnowledgeApplication
from .read_tools import KnowledgeReadTools
from .team_catalog import OfficerBinding, TeamCatalog
from .temporal_work import WorkResult
from .work import (
    WorkContracts,
    graph_content_digest,
    record_digest,
    segment_digest,
    work_spec_digest,
)
from .work_tools import OfficerToolSession

# Adopted deterministic Graph contract; this module owns its single task effect.
ENGINE_ID = "agentfabric.core.deterministic_graph"
ENGINE_VERSION = "1.0.0"
CRITERION = {
    "criterion_id": "criterion.proposal",
    "description": "One source-bound Pending normalization proposal has independently verified exact bytes, a live human gate, and no canonical apply.",
}
_POLICY = {"max_segments": 1, "max_repeated_gap_assessments": 1, "max_profile_invocations": 1}
_MODEL_TOOLS = ("work_context_read", "knowledge_read", "proposal_create")
_MODEL_ROUTE = {
    "runtime_kind": "hermes_native", "hermes_version": "0.22.5",
    "hermes_revision": "bf0203a794a07aac3ff764972ab9087baa4e4f4e",
    "provider": "openai-codex", "model": "gpt-6.1-terra",
}


def _runner_binding(value: Mapping[str, Any]) -> dict[str, Any]:
    _require(isinstance(value, Mapping)
             and set(value) == {*_MODEL_ROUTE, "adapter_digest"}
             and all(value.get(key) == expected for key, expected in _MODEL_ROUTE.items())
             and isinstance(value.get("adapter_digest"), str)
             and re.fullmatch(r"sha256:[0-9a-f]{64}", value["adapter_digest"]) is not None,
             "Normalization requires the exact native Hermes runtime binding")
    return dict(value)


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise AdmissionError("SCOPE_DENIED", message)


def normalization_native_digest(runner_binding: Mapping[str, Any] | None = None) -> str:
    """Pin actual owner module bytes and, for model mode, the exact host binding."""
    raw = read_regular(Path(__file__))
    _require(raw is not None, "Normalization execution module is unavailable")
    content = {"engine_id": ENGINE_ID, "engine_version": ENGINE_VERSION,
               "execution_module_digest": digest(raw)}
    if runner_binding is not None:
        content["runner_binding"] = _runner_binding(runner_binding)
    return record_digest(content)


def _pins(application: KnowledgeApplication) -> dict[str, Any]:
    selected = application.core.binding
    return {**{key: selected[key] for key in (
        "core_version", "core_digest", "semantic_version", "semantic_digest")},
        "schema_version": selected["schema_version_pin"]}


def _binding(application: KnowledgeApplication, binding: OfficerBinding,
             runner_binding: Mapping[str, Any] | None = None) -> None:
    _require(isinstance(binding, OfficerBinding), "Normalization requires an OfficerBinding")
    catalog = TeamCatalog.load(application.roots.control)
    catalog.validate_officer_binding(binding)
    _require(dict(binding.officer_profile) == catalog.profile("knowledgeos.normalization_officer")
             and dict(binding.eval_officer_profile) == catalog.profile("knowledgeos.eval_officer")
             and binding.officer_executor_id != binding.eval_officer_executor_id
             and binding.native_binding_digest == normalization_native_digest(runner_binding),
             "Normalization requires the exact independent Officer and native runtime binding")
    WorkContracts(application.core).record("ArtifactReference", binding.binding_reference)


def normalization_admission(application: KnowledgeApplication, binding: OfficerBinding,
                            source_reference: Mapping[str, Any], *, work_id: str,
                            work_run_id: str, admission_receipt_id: str,
                            runner_binding: Mapping[str, Any] | None = None):
    """Build records only; trusted Director callbacks must separately admit them."""
    _binding(application, binding, runner_binding)
    source_path = application.resolve_reference(dict(source_reference))
    _require(source_path.startswith("00_Inbox/Captures/"), "Normalization requires a selected Capture")
    pins = _pins(application)
    spec = {
        "owner_operation_id": "knowledgeos", "work_id": work_id, "work_spec_revision": 1,
        "criteria_revision": 1, "objective": "Prepare one Pending normalization proposal",
        "constraints": (("No provider, canonical apply, or source mutation; selected source "
                         if runner_binding is None else
                         "No canonical apply or source mutation; native Hermes runner "
                         + record_digest(_runner_binding(runner_binding)) + "; selected source ")
                        + record_digest(source_reference)),
        "acceptance_criteria": [dict(CRITERION)], "target_kind": "officer",
        "officer_executor_id": binding.officer_executor_id,
        "eval_officer_executor_id": binding.eval_officer_executor_id,
        "allowed_executor_ids": [binding.officer_executor_id],
        "allowed_profiles": [dict(binding.officer_profile)], "delegation_mode": "officer_direct",
        "explicit_assignments": [], "continuation_policy": dict(_POLICY),
        "execution_profile": "sequential_graph", "admission_receipt_id": admission_receipt_id,
        "steerable_parameter_ids": [], **pins,
    }
    spec["spec_digest"] = work_spec_digest(spec)
    run = {
        "owner_operation_id": "knowledgeos", "work_id": work_id, "work_run_id": work_run_id,
        "work_spec_revision": 1, "work_spec_digest": spec["spec_digest"],
        "revision": 1, "control_revision": 1, "generation": 1,
        "fencing_token": "normalization." + record_digest({"work_run_id": work_run_id})[7:],
        "state": "admitted", "graph_run_ids": [], "segments_started": 0,
        "profile_invocations": 0, "repeated_gap_count": 0,
        "pending_action_ids": [], "unresolved_effect_ids": [],
        "binding_reference": dict(binding.binding_reference), **pins,
    }
    WorkContracts(application.core).run(run, spec)
    return spec, run


def _current(exops: KnowledgeExOps, work_run_id: str,
             runner_binding: Mapping[str, Any] | None = None) -> dict[str, Any]:
    exops._current_binding()
    record = exops.application.journal.read()["intents"].get("exops.work." + work_run_id)
    _require(isinstance(record, dict) and record.get("application_state") == "started",
             "Normalization has no active owner attempt")
    spec, run = record["spec"], record["run"]
    binding = exops.officers.get(spec.get("officer_executor_id"))
    selected_runner = record.get("normalization_runner_binding", runner_binding)
    _require(runner_binding is None or selected_runner == runner_binding,
             "Normalization runner binding changed during execution")
    _binding(exops.application, binding, selected_runner)
    expected, _ = normalization_admission(
        exops.application, binding, record["officer_source_reference"],
        work_id=spec["work_id"], work_run_id=work_run_id,
        admission_receipt_id=spec["admission_receipt_id"], runner_binding=selected_runner)
    _require(spec == expected, "Unsupported normalization WorkSpec or criterion semantics")
    exops._team_binding(spec, run)
    _require(exops.director_admitted(spec)
             and exops.director_officer_source_admitted is not None
             and exops.director_officer_source_admitted(spec, record["officer_source_reference"]),
             "Exact normalization source admission is not current")
    exops.contracts.run(run, spec)
    _require(run["state"] == "running" and not run["pending_action_ids"]
             and not run["unresolved_effect_ids"], "Normalization Work is not effect-safe")
    return record


def _artifact(reference: Mapping[str, Any], raw: bytes) -> dict[str, Any]:
    return {"owner_operation_id": "knowledgeos", "artifact_id": reference["logical_id"],
            "source_revision": reference["source_revision"], "content_digest": digest(raw),
            "media_type": "text/markdown", "size_bytes": len(raw),
            "classification": reference["classification"],
            "availability": "available_by_owner_export",
            "export_capability_id": "knowledgeos.artifact_read"}


def _bundle(application: KnowledgeApplication, spec, run, source) -> tuple[dict, dict]:
    suffix = record_digest({"work_run_id": run["work_run_id"], "spec_digest": spec["spec_digest"]})[7:39]
    base = {"graph_id": "graph.normalization." + suffix, "graph_revision": 1}
    context = {key: run[key] for key in ("owner_operation_id", "work_id", "work_run_id",
                                        "work_spec_revision", "work_spec_digest")}
    pins = _pins(application)
    node_ids = ["node.start", "node.normalize", "node.finish"]
    nodes = [{**base, "node_id": name, "node_kind": kind, "max_attempts": 1,
              "input_semantic_term_ids": [], "output_semantic_term_ids": []}
             for name, kind in zip(node_ids, ("start", "task", "terminal"))]
    edges = [{**base, "edge_id": "edge." + str(index), "source_node_id": source_id,
              "target_node_id": target_id, "condition": "always"}
             for index, (source_id, target_id) in enumerate(pairwise(node_ids))]
    graph = {**base, "owner_operation_id": "knowledgeos", "profile": "graph_enabled",
             "engine_id": ENGINE_ID, "engine_version": ENGINE_VERSION,
             "start_node_id": node_ids[0], "node_ids": node_ids,
             "edge_ids": [edge["edge_id"] for edge in edges], "max_steps": 3,
             "input_semantic_term_ids": [], "output_semantic_term_ids": [], **pins}
    graph["graph_digest"] = graph_content_digest(graph, nodes, edges)
    source_path = application.resolve_reference(dict(source))
    raw = read_regular(resolve_beneath(application.roots.vault, source_path))
    _require(raw is not None and digest(raw) == source["content_digest"], "Capture bytes changed")
    source_artifact = _artifact(source, raw)
    segment = {**context, **base, **pins, "graph_run_id": "graphrun.normalization." + suffix,
               "graph_digest": graph["graph_digest"], "segment_index": 1,
               "target_criterion_ids": [CRITERION["criterion_id"]], "trigger_kind": "initial",
               "input_references": [source_artifact]}
    segment["segment_digest"] = segment_digest(segment)
    assignment = {**base, "owner_operation_id": "knowledgeos", "node_id": node_ids[1],
                  "assignment_generation": run["generation"],
                  "executor_id": spec["officer_executor_id"], **spec["allowed_profiles"][0],
                  "admission_receipt_id": spec["admission_receipt_id"],
                  "work_run_id": run["work_run_id"], "work_spec_revision": spec["work_spec_revision"],
                  "work_spec_digest": spec["spec_digest"], "selection_authority": "owner_officer",
                  "semantic_version": pins["semantic_version"], "semantic_digest": pins["semantic_digest"]}
    return {"segment": segment, "graph_spec": graph, "nodes": nodes, "edges": edges,
            "assignments": [assignment]}, {source_artifact["artifact_id"]: source_artifact}


def _model_request(exops: KnowledgeExOps, work_run_id: str, runner_binding) -> dict[str, Any]:
    record = _current(exops, work_run_id, runner_binding)
    run, spec = record["run"], record["spec"]
    session = OfficerToolSession(exops.application, work_run_id,
                                 spec["officer_executor_id"], run["active_graph_run_id"])
    app = exops.application
    source_path = app.resolve_reference(record["officer_source_reference"])
    app._mcp_source_policy(source_path)
    raw = read_regular(resolve_beneath(app.roots.vault, source_path))
    _require(raw is not None, "Model source is unavailable")
    properties = source_properties(source_path, raw)
    _require(properties.get("ai_policy") not in {"local_only", "deny"}
             and properties.get("sensitivity") != "confidential",
             "External model runner cannot receive a local-only or private source")
    context = session.work_view()
    _require(bool(context["source_chunks"]), "Model normalization has no admitted source chunks")
    request = {key: context[key] for key in (
        "work_run_id", "work_spec_digest", "graph_run_id", "executor_id",
        "generation", "fencing_token", "source_reference", "source_chunks")}
    request.update(runner_binding=_runner_binding(runner_binding),
                   control_root=str(exops.application.roots.control))
    return {"request_digest": record_digest(request), **request}


def _model_response(exops: KnowledgeExOps, work_run_id: str, request, response) -> dict[str, Any]:
    """Re-read context/chunks; a model's final answer never authorizes an effect."""
    binding = _runner_binding(request["runner_binding"])
    _require(request == _model_request(exops, work_run_id, binding),
             "Model request differs from current owner Work, source or runtime")
    _require(isinstance(response, dict) and set(response) == {
        "request_digest", "runner_binding", "model", "provider", "session_id",
        "final_text", "tool_calls", "exposed_tools"}
        and response["request_digest"] == request["request_digest"]
        and response["runner_binding"] == binding
        and response["model"] == binding["model"] and response["provider"] == binding["provider"]
        and isinstance(response["session_id"], str) and 0 < len(response["session_id"]) <= 256
        and isinstance(response["final_text"], str) and 0 < len(response["final_text"]) <= 16384
        and isinstance(response["exposed_tools"], list)
        and len(response["exposed_tools"]) == len(_MODEL_TOOLS)
        and all(isinstance(name, str) for name in response["exposed_tools"])
        and set(response["exposed_tools"]) == set(_MODEL_TOOLS),
        "Model response changed request, binding, route or exposed tools")
    calls = response["tool_calls"]
    _require(isinstance(calls, list) and 3 <= len(calls) <= 32,
             "Model normalization needs actual context, read and proposal tool calls")
    session = OfficerToolSession(exops.application, work_run_id,
                                 request["executor_id"], request["graph_run_id"])
    reader = KnowledgeReadTools(exops.application,
                               allowed_path=exops.application.resolve_reference(request["source_reference"]))
    read_locators = set()
    proposal = None
    for index, call in enumerate(calls):
        _require(isinstance(call, dict)
                 and {"name", "arguments", "result"} <= set(call)
                 and set(call) <= {"name", "arguments", "result", "raw_callback_name"}
                 and ("raw_callback_name" not in call or (
                     isinstance(call["raw_callback_name"], str) and len(call["raw_callback_name"]) <= 256))
                 and call["name"] in _MODEL_TOOLS and isinstance(call["arguments"], dict)
                 and isinstance(call["result"], dict), "Model trace contains an ungranted or malformed tool")
        name, arguments, envelope = call["name"], call["arguments"], call["result"]
        _require(envelope.get("schema_version") == 3 and envelope.get("operation") == name
                 and envelope.get("error") is None and envelope.get("canonical_target_changed") is False
                 and isinstance(envelope.get("data"), dict),
                 "Model trace contains a denied or changed owner result")
        session.authorize(name, arguments)
        data = envelope["data"]
        if name == "work_context_read":
            _require(index == 0 and envelope.get("status") == "ok"
                     and envelope.get("effect") == "read_only" and data == session.work_view(),
                     "Model trace context differs from exact owner Work")
        elif name == "knowledge_read":
            _require(index > 0 and proposal is None
                     and set(arguments) <= {"resource_reference", "chunk_locator", "offset", "max_chars"}
                     and {"resource_reference", "chunk_locator"} <= set(arguments)
                     and arguments["chunk_locator"] in request["source_chunks"]
                     and envelope.get("status") == "ok" and envelope.get("effect") == "read_only",
                     "Model trace read differs from admitted source chunks")
            actual = reader.knowledge_read(arguments["resource_reference"], arguments["chunk_locator"],
                offset=arguments.get("offset", 0), max_chars=arguments.get("max_chars", 4096))
            _require(data == actual and bool(actual["text"]),
                     "Model trace read differs from fresh owner source evidence")
            if actual["offset"] == 0:
                read_locators.add(arguments["chunk_locator"])
        else:
            _require(index == len(calls) - 1 and proposal is None
                     and read_locators == set(request["source_chunks"])
                     and envelope.get("status") in {"created", "replayed"}
                     and envelope.get("effect") in {"proposal_created", "proposal_replayed"}
                     and envelope.get("execution_outcome") == "completed"
                     and data.get("source_reference") == request["source_reference"]
                     and isinstance(data.get("proposal_reference"), dict),
                     "Model did not create one real source-bound proposal after actual source reads")
            proposal = data["proposal_reference"]
    _require(proposal is not None and calls[0]["name"] == "work_context_read",
             "Model final answer has no actual proposal tool effect")
    return proposal


def _model_evidence(exops: KnowledgeExOps, work_run_id: str, runner_binding, *, request, response=None) -> None:
    record = _current(exops, work_run_id, runner_binding)
    try:
        encoded = canonical({"request": request, "response": response})
    except (TypeError, ValueError) as error:
        raise AdmissionError("SCOPE_DENIED", "Model execution evidence is not JSON") from error
    _require(len(encoded) <= 256 * 1024, "Model execution evidence exceeds its bounded owner record")
    with exops.application.journal.writer() as writer:
        current = writer.value["intents"].get("exops.work." + work_run_id)
        _require(current == record, "Work changed before model evidence registration")
        current["normalization_runner_binding"] = _runner_binding(runner_binding)
        current["normalization_model_execution"] = copy.deepcopy({
            "request": request, "response": response, "evidence_digest": digest(encoded)})
        writer.value["history"].append({"event": "normalization_model_execution_recorded",
            "work_run_id": work_run_id, "sequence": writer.value["revision"] + 1})
        writer.commit(exops.application.clock())


def _register_proposal(exops: KnowledgeExOps, work_run_id: str, reference) -> None:
    app = exops.application
    record = _current(exops, work_run_id)
    path = app.resolve_reference(dict(reference), proposal=True)
    raw = read_regular(resolve_beneath(app.roots.vault, path))
    _require(raw is not None and digest(raw) == reference["content_digest"], "Proposal bytes changed")
    artifact = _artifact(reference, raw)
    with app.journal.writer() as writer:
        current = writer.value["intents"].get("exops.work." + work_run_id)
        _require(current == record, "Work changed before artifact registration")
        bound = {"work_run_id": work_run_id, "work_spec_digest": record["spec"]["spec_digest"],
                 "graph_run_id": record["run"]["active_graph_run_id"],
                 "executor_id": record["spec"]["officer_executor_id"],
                 "source_reference": record["officer_source_reference"]}
        effects = [item for item in writer.value["intents"].values()
                   if item.get("work_binding") == bound and item.get("artifact_path") == path
                   and item.get("state") == "completed" and item.get("content_digest") == digest(raw)]
        _require(len(effects) == 1, "Proposal has no exact completed Work effect")
        receipt = writer.value["receipts"].get(effects[0]["effect_id"], {}).get("execution", {})
        _require(receipt.get("outcome") == "succeeded" and receipt.get("result_digest") == digest(raw)
                 and base64.b64decode(effects[0]["frozen_artifact"], validate=True) == raw,
                 "Proposal has no exact successful byte receipt")
        current["artifact_references"] = {artifact["artifact_id"]: artifact}
        current["submitted_artifacts"] = [dict(reference)]
        current["canonical_proposal_reference"] = dict(reference)
        writer.value["history"].append({"event": "normalization_artifact_registered",
                                         "work_run_id": work_run_id,
                                         "sequence": writer.value["revision"] + 1})
        writer.commit(app.clock())


def evaluate_normalization(exops: KnowledgeExOps, work_run_id: str, *, evaluator_id: str) -> WorkResult:
    """Fresh EvalOfficer reads; no producer report or producer PASS is an input."""
    app = exops.application
    record = _current(exops, work_run_id)
    spec, run = record["spec"], record["run"]
    _require(evaluator_id == spec["eval_officer_executor_id"]
             and evaluator_id != spec["officer_executor_id"], "Only the independent EvalOfficer may certify")
    admitted = record.get("admitted_officer_segments", {})
    _require(len(admitted) == 1 and run["graph_run_ids"] == list(admitted)
             and run["segments_started"] == run["profile_invocations"] == 1,
             "Evaluation requires one admitted normalization Graph")
    graph_run_id = run["active_graph_run_id"]
    source = record["officer_source_reference"]
    source_path = app.resolve_reference(dict(source))
    source_bytes = read_regular(resolve_beneath(app.roots.vault, source_path))
    _require(source_bytes is not None and digest(source_bytes) == source["content_digest"],
             "Evaluation source bytes changed")
    segment = admitted[graph_run_id]["segment"]
    _require(admitted[graph_run_id]["source_reference"] == source
             and admitted[graph_run_id]["native_binding_digest"]
                 == normalization_native_digest(record.get("normalization_runner_binding"))
             and segment["input_references"] == [_artifact(source, source_bytes)]
             and segment["target_criterion_ids"] == [CRITERION["criterion_id"]]
             and segment_digest(segment) == segment["segment_digest"],
             "Evaluation source or immutable segment changed")
    references = record.get("submitted_artifacts", [])
    _require(len(references) == 1, "Evaluation requires one registered proposal")
    reference = references[0]
    if record.get("normalization_runner_binding") is not None:
        evidence = record.get("normalization_model_execution")
        _require(isinstance(evidence, dict) and set(evidence) == {"request", "response", "evidence_digest"}
                 and digest(canonical({"request": evidence["request"], "response": evidence["response"]}))
                     == evidence["evidence_digest"],
                 "Independent evaluation model execution evidence changed or is missing")
        _require(_model_response(exops, work_run_id, evidence["request"], evidence["response"]) == reference,
                 "Independent evaluation model proposal differs from registered owner effect")
    proposal_path = app.resolve_reference(dict(reference), proposal=True)
    proposal_bytes = read_regular(resolve_beneath(app.roots.vault, proposal_path))
    _require(proposal_bytes is not None, "Evaluated Pending proposal is unavailable")
    artifact = _artifact(reference, proposal_bytes)
    _require(record.get("artifact_references") == {artifact["artifact_id"]: artifact},
             "Evaluation artifact differs from registered exact bytes")
    snapshot = app.journal.read()
    _require(snapshot["intents"].get("exops.work." + work_run_id) == record,
             "Work changed during evaluation")
    bound = {"work_run_id": work_run_id, "work_spec_digest": spec["spec_digest"],
             "graph_run_id": graph_run_id, "executor_id": spec["officer_executor_id"],
             "source_reference": source}
    effects = [item for item in snapshot["intents"].values() if item.get("work_binding") == bound]
    _require(len(effects) == 1, "Evaluation requires one exact Work-bound proposal effect")
    effect = effects[0]
    receipt = snapshot["receipts"].get(effect["effect_id"], {}).get("execution", {})
    _require(effect["state"] == "completed" and effect["artifact_path"] == proposal_path
             and effect["source_path"] == source_path and effect["source_digest"] == digest(source_bytes)
             and effect["content_digest"] == digest(proposal_bytes)
             and base64.b64decode(effect["frozen_artifact"], validate=True) == proposal_bytes
             and all(receipt.get(key) == effect[key] for key in (
                 "effect_id", "owner_operation_id", "content_digest", "generation", "fencing_token"))
             and receipt.get("outcome") == "succeeded"
             and receipt.get("result_digest") == digest(proposal_bytes),
             "Evaluation proposal receipt or frozen bytes differ")
    report, code = app.inspect_proposal(dict(reference))
    _require(code == 0 and report.get("status") == "PASS" and report.get("count") == 1,
             "Independent Pending proposal inspection failed")
    inspected = report["proposals"][0]
    _require(inspected["proposal_path"] == proposal_path and inspected["status"] == "pending"
             and inspected["action"] == "update_note" and inspected["target_path"] == source_path
             and inspected["source_baselines"] == [{"path": source_path, "sha256": digest(source_bytes)[7:]}]
             and inspected["proposal_sha256"] == digest(proposal_bytes)[7:]
             and inspected["target_before_sha256"] == digest(source_bytes)[7:]
             and inspected["target_after_sha256"] != inspected["target_before_sha256"],
             "Independent proposal inspection differs from normalization criterion")
    request = snapshot["human_requests"].get(effect["human_request_id"], {})
    _require(request.get("lifecycle") == "pending" and request.get("allowed_actions") == ["approve", "reject", "inspect"]
             and request.get("evidence_reference_ids") == [reference["logical_id"]]
             and effect["human_request_id"] not in snapshot["decisions"]
             and datetime.fromisoformat(request["expires_at"]) > app.clock()
             and request.get("target_digest") == app._target_digest(source_path, proposal_path),
             "Normalization proposal lost its exact pending human gate")
    _require(app.reference(source_path) == source and app.reference(proposal_path, proposal=True) == reference,
             "Source or Pending proposal changed during independent inspection")
    checkpoint = record_digest({"graph_run_id": graph_run_id, "source_digest": digest(source_bytes),
                                "proposal_digest": digest(proposal_bytes)})
    context = {key: run[key] for key in ("owner_operation_id", "work_id", "work_run_id",
                                        "work_spec_revision", "work_spec_digest")}
    suffix = record_digest({"graph_run_id": graph_run_id, "checkpoint": checkpoint})[7:39]
    evaluation = {"owner_operation_id": "knowledgeos", "run_id": graph_run_id,
                  "work_run_id": work_run_id, "work_spec_revision": spec["work_spec_revision"],
                  "work_spec_digest": spec["spec_digest"], "checkpoint_digest": checkpoint,
                  "criteria_revision": spec["criteria_revision"], "criterion_ids": [CRITERION["criterion_id"]],
                  "passed_criterion_ids": [CRITERION["criterion_id"]], "failed_criterion_ids": [],
                  "not_run_criterion_ids": [], "evaluation_id": "evaluation.normalization." + suffix,
                  "evaluator_id": evaluator_id, "evidence_references": [artifact]}
    assessment = {**context, **_pins(app), "assessment_id": "assessment.normalization." + suffix,
                  "criteria_revision": spec["criteria_revision"], "assessor_id": evaluator_id,
                  "assessed_at": stamp(app.clock()), "graph_run_ids": [graph_run_id],
                  "completion_state": "complete", "criterion_outcomes": [{
                      "criterion_id": CRITERION["criterion_id"], "outcome": "satisfied",
                      "evaluation_ids": [evaluation["evaluation_id"]], "evidence_references": [artifact]}]}
    completed = {**copy.deepcopy(run), "revision": run["revision"] + 1,
                 "control_revision": run["control_revision"] + 1, "state": "completed",
                 "latest_assessment_id": assessment["assessment_id"],
                 "latest_assessment_digest": record_digest(assessment), "result_reference": artifact}
    completed.pop("active_graph_run_id")
    return WorkResult(completed, assessment, (segment,), (evaluation,),
                      {artifact["artifact_id"]: artifact}, {graph_run_id: checkpoint}, evaluator_id)


class NormalizationOfficerExecutor:
    """Temporal WorkExecutor for one exact deterministic or native model binding."""

    def __init__(self, exops: KnowledgeExOps, *, runner=None):
        self.exops = exops
        self.runner = runner
        self.runner_binding = _runner_binding(runner.binding) if runner is not None else None

    def _runtime(self) -> None:
        _require(self.runner is None or _runner_binding(self.runner.binding) == self.runner_binding,
                 "Normalization runner binding changed at the execution boundary")

    async def __call__(self, spec: Mapping[str, Any], run: Mapping[str, Any]) -> WorkResult:
        self._runtime()
        record = _current(self.exops, run["work_run_id"], self.runner_binding)
        _require(record["spec"] == dict(spec) and record["run"] == dict(run),
                 "Executor input differs from exact owner-admitted Work")
        _require(not run["graph_run_ids"] and not record.get("admitted_officer_segments"),
                 "Normalization cannot repeat an already-started Graph")
        source = record["officer_source_reference"]
        bundle, artifacts = _bundle(self.exops.application, spec, run, source)
        self.exops.admit_officer_segment(run["work_run_id"],
            officer_executor_id=spec["officer_executor_id"], bundle=bundle,
            artifacts=artifacts, source_reference=source)
        self._runtime()
        if self.runner is None:
            session = OfficerToolSession(self.exops.application, run["work_run_id"],
                                         spec["officer_executor_id"], bundle["segment"]["graph_run_id"])
            created, code = session.create_proposal({"source_reference": source})
            _require(code == 0 and created.get("status") == "PASS"
                     and created.get("effects") == {"pending_artifact_created": True, "canonical_note_changed": False}
                     and created.get("provider_called") is False,
                     "Normalization did not create one real Pending proposal; no-change is not completion")
            reference = created["resource_reference"]
        else:
            request = _model_request(self.exops, run["work_run_id"], self.runner_binding)
            _model_evidence(self.exops, run["work_run_id"], self.runner_binding, request=request)
            self._runtime()
            response = await self.runner(copy.deepcopy(request))
            self._runtime()
            _model_evidence(self.exops, run["work_run_id"], self.runner_binding,
                            request=request, response=response)
            reference = _model_response(self.exops, run["work_run_id"], request, response)
        _register_proposal(self.exops, run["work_run_id"], reference)
        return evaluate_normalization(self.exops, run["work_run_id"],
                                      evaluator_id=spec["eval_officer_executor_id"])

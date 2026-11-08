"""Model judgments carried by the existing native bridge, not a second Owner.

The host applies the pinned role SOUL/model policy. This module supplies admitted
context and parses a decision; each consumer validates its own domain result.
"""
from __future__ import annotations

import importlib.util
import json
from collections.abc import Mapping
from pathlib import Path

from ..adapters.core import AdmissionError
from .work import record_digest


def source_context(application, source, *, gateway_receipt_id=None):
    """Expose the method's actual input, never local anchor text as foreign disclosure."""
    from .native_team import source_bytes

    path, raw = source_bytes(application, source)
    context = {"source_reference": dict(source), "source_path": path}
    if gateway_receipt_id is not None:
        from .read_tools import KnowledgeReadTools

        return {**context, "source_role": "local_anchor_not_foreign_disclosure",
                "gateway_evidence": KnowledgeReadTools(application).gateway_evidence_read(gateway_receipt_id)}
    return {**context, "source_text": raw.decode("utf-8")}


def decision_request(runner, *, role_id, responsibility, method_id, context,
             instructions, record=None, route=None):
    agent = {"role_id": role_id, "responsibility": responsibility,
             "method_id": method_id, "context": dict(context),
             "instructions": instructions, "tools": [],
             "control_root": "/workspace/control"}
    run = record["run"] if record is not None else {}
    spec = record["spec"] if record is not None else {}
    if run:
        agent["work_run_id"] = run["work_run_id"]
        if run.get("active_graph_run_id") is not None:
            agent["graph_run_id"] = run["active_graph_run_id"]
    if responsibility == "manager" and spec:
        agent["executor_id"] = spec["manager_executor_id"]
    elif responsibility == "evaluator" and spec:
        agent["executor_id"] = spec["eval_officer_executor_id"]
    if route is not None:
        agent["route"] = route
    body = {"request_kind": "agent_decision", "agent_request": agent,
            "work_run_id": run.get("work_run_id"), "stage_id": responsibility,
            "runner_binding": dict(runner.binding)}
    return {**body, "request_digest": record_digest(body)}


def decision_from_response(request, response):
    """Parse an executed role response; exposed tools must match its Owner request."""
    if (not isinstance(response, Mapping)
            or response.get("role_id") != request["agent_request"]["role_id"]
            or response.get("responsibility") != request["agent_request"]["responsibility"]
            or response.get("request_digest") != record_digest(request["agent_request"])
            or not isinstance(response.get("session_id"), str)
            or not response["session_id"]
            or not isinstance(response.get("prompt_digest"), str)
            or not isinstance(response.get("final_text"), str)
            or response.get("exposed_tools") != request["agent_request"]["tools"]
            or not isinstance(response.get("tool_calls"), list)
            or not request["agent_request"]["tools"] and response["tool_calls"]):
        raise AdmissionError("AGENT_DECISION_INVALID", "Model judgment lacks its exact role/session evidence")
    text = response["final_text"].strip()
    if text.startswith("```") and text.endswith("```"):
        lines = text.splitlines()
        if lines[0].strip() in {"```", "```json"}:
            text = "\n".join(lines[1:-1])
    try:
        value = json.loads(text)
    except (TypeError, ValueError) as error:
        raise AdmissionError("AGENT_DECISION_INVALID", "Model judgment did not return a JSON object") from error
    if not isinstance(value, dict):
        raise AdmissionError("AGENT_DECISION_INVALID", "Model judgment must be a JSON object")
    evidence = {"request": request, "response": dict(response), "decision": value}
    return value, {**evidence, "content_digest": record_digest(evidence)}


async def role_decision(runner, *, role_id, responsibility, method_id, context,
                        instructions, record=None, route=None):
    """Ask one actual role model and return its decision with native evidence."""
    request = decision_request(runner, role_id=role_id, responsibility=responsibility,
                       method_id=method_id, context=context, instructions=instructions,
                       record=record, route=route)
    return decision_from_response(request, await runner(request))


def role_decision_sync(runner, *, role_id, responsibility, method_id, context,
                       instructions, record=None, route=None):
    """Bridge HTTP-thread admission to the Owner loop without a second event loop."""
    call = getattr(runner, "call_sync", None)
    if not callable(call):
        raise AdmissionError("MODEL_RUNNER_UNAVAILABLE", "Director requires the actual native decision bridge")
    request = decision_request(runner, role_id=role_id, responsibility=responsibility,
                       method_id=method_id, context=context, instructions=instructions,
                       record=record, route=route)
    return decision_from_response(request, call(request))


def validate_role_evidence(control_root, role_id, evidence, *, tools, route=None):
    """Match the host's actual role invocation to the selected maintained policy."""
    control = Path(control_root)
    module_spec = importlib.util.spec_from_file_location(
        "knowledgeos_owner_role_policy", control / "ops/clients/hermes/role_policy.py")
    policy = importlib.util.module_from_spec(module_spec)
    module_spec.loader.exec_module(policy)
    selected = policy.load_role(control, role_id, route=route)
    setting = "reasoning_effort" if selected["provider"] == "openai-codex" else "think"
    if (not isinstance(evidence, Mapping)
            or evidence.get("role_id") != role_id
            or evidence.get("provider") != selected["provider"]
            or evidence.get("model") != selected["model"]
            or evidence.get("route_name") != selected["route_name"]
            or evidence.get(setting) != selected[setting]
            or evidence.get("prompt_digest") != selected["prompt_digest"]
            or evidence.get("exposed_tools") != list(tools)
            or not evidence.get("session_id")
            or not evidence.get("transport", {}).get("requests")
            or evidence.get("provider_returned_model") not in (None, selected["model"])):
        raise AdmissionError("AGENT_DECISION_INVALID", "Native role evidence differs from its selected SOUL/model policy")


def admitted_director_context(application, record):
    """Resolve typed Owner inputs independently of the model-written objective."""
    spec = record["spec"]
    retained = application.journal.read()["intents"].get(
        "owner.director." + spec["admission_receipt_id"])
    if (not isinstance(retained, Mapping) or retained.get("state") != "completed"
            or retained.get("spec") != spec
            or retained.get("source") != record.get("method_source_reference")):
        raise AdmissionError("ADMISSION_REQUIRED", "Work has no exact retained Director input")
    return retained["director_evidence"]["request"]["agent_request"]["context"]


def admitted_owner_parameters(application, record):
    return admitted_director_context(application, record)["owner_scope"]


def admitted_owner_seed(application, record):
    return admitted_director_context(application, record)["owner_seed"]

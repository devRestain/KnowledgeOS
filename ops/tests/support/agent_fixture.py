"""Synthetic model evidence for Owner behavior tests; never provider-call proof."""
from __future__ import annotations

import copy
import importlib.util
import json
import shutil
from pathlib import Path

from vaultops.application.work import record_digest

CONTROL = Path(__file__).resolve().parents[3]


def install_agent_policy(control_root):
    target = Path(control_root)
    if target.resolve() != CONTROL.resolve():
        for relative in ("ops/config/agent-profiles.json", "ops/clients/hermes/role_policy.py"):
            destination = target / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(CONTROL / relative, destination)
        shutil.copytree(CONTROL / "ops/prompts/hermes", target / "ops/prompts/hermes", dirs_exist_ok=True)
    spec = importlib.util.spec_from_file_location("knowledgeos_fixture_role_policy", target / "ops/clients/hermes/role_policy.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def model_binding(control_root=CONTROL):
    policy = install_agent_policy(control_root)
    return {"runtime_kind": "hermes_native", "hermes_version": "0.22.5",
            "hermes_revision": "bf0203a794a07aac3ff764972ab9087baa4e4f4e",
            "adapter_digest": "sha256:" + "1" * 64,
            "agent_policy_digest": policy.policy_digest(Path(control_root))}


def role_response(agent_request, *, control_root, final_text, calls=()):
    selected = install_agent_policy(control_root).load_role(
        Path(control_root), agent_request["role_id"], route=agent_request.get("route"))
    setting = "reasoning_effort" if selected["provider"] == "openai-codex" else "think"
    trace = [{**copy.deepcopy(call), "call_id": "synthetic-call-" + str(index)}
             for index, call in enumerate(calls)]
    return {"role_id": agent_request["role_id"], "responsibility": agent_request["responsibility"],
            "method_id": agent_request["method_id"], "provider": selected["provider"],
            "model": selected["model"], setting: selected[setting], "route_name": selected["route_name"],
            "prompt_digest": selected["prompt_digest"],
            "instructions_digest": record_digest({"instructions": agent_request.get("instructions", "")}),
            "request_digest": record_digest(agent_request), "session_id": "synthetic-unit-model",
            "final_text": final_text, "tool_calls": trace,
            "exposed_tools": list(agent_request["tools"]), "provider_returned_model": None,
            "transport": {"requests": [{"kind": "synthetic-unit-model; no HTTP or provider call"}]}}


def decision_response(request, decision, *, control_root, calls=()):
    return role_response(request["agent_request"], control_root=control_root,
                         final_text=json.dumps(decision, ensure_ascii=False), calls=calls)


def specialist_response(request, calls, *, control_root, final_text="Synthetic model fixture; no provider call."):
    step = request["agent_step"]
    agent = {"role_id": step["role_id"], "responsibility": "specialist", "route": step["route"],
             "method_id": request["method_id"], "instructions": step["instruction"],
             "context": request, "tools": list(step["tools"])}
    native = role_response(agent, control_root=control_root, final_text=final_text, calls=calls)
    return {"request_digest": request["request_digest"], "runner_binding": copy.deepcopy(request["runner_binding"]),
            **{key: native[key] for key in ("provider", "model", "session_id", "final_text", "exposed_tools")},
            "tool_calls": copy.deepcopy(list(calls)), "agent_evidence": native}


def readonly_role_calls(request, *, exops):
    """Execute scoped Owner reads; synthetic role text never stands in for receipts."""
    from types import SimpleNamespace

    from vaultops.application.read_tools import KnowledgeReadTools
    from vaultops.application.work_tools import TeamToolSession
    from vaultops.interfaces.mcp_server import _convert_report, _operation
    from vaultops.retrieval import _chunks_for_note

    app = exops.application
    agent = request["agent_request"]
    source = agent["context"]["source_reference"]
    session = TeamToolSession(app, agent["work_run_id"], agent["executor_id"],
                              agent["graph_run_id"], exops)
    calls = []

    def call(name, arguments):
        session.authorize(name, arguments)
        result, error = _convert_report(
            SimpleNamespace(services=app), name, arguments,
            _operation(app, name, arguments, session=session)())
        assert not error, result
        calls.append({"name": name, "arguments": arguments, "result": result})
        return result["data"]

    context = call("work_context_read", {}) if "work_context_read" in agent["tools"] else None
    if "knowledge_read" in agent["tools"]:
        if context is not None:
            locators = context["source_chunks"]
        else:
            reader = KnowledgeReadTools(app)
            _, notes = reader._eligible(selected_path=app.resolve_reference(source))
            locators = [chunk.locator for chunk in _chunks_for_note(reader._note(source, notes))]
        for locator in locators:
            offset = 0
            while True:
                page = call("knowledge_read", {
                    "resource_reference": source, "chunk_locator": locator, "offset": offset})
                offset += len(page["text"])
                if not page["truncated"]:
                    break
    return calls


def retain_director_context(application, spec, run, source, *, owner_scope=None):
    """Stand in for a trusted Director only in explicitly composed unit fixtures."""
    context = {"owner_scope": copy.deepcopy(owner_scope or {}),
               "owner_seed": {"spec": copy.deepcopy(spec), "run": copy.deepcopy(run)}}
    with application.journal.writer() as writer:
        writer.value["intents"]["owner.director." + spec["admission_receipt_id"]] = {
            "state": "completed", "spec": copy.deepcopy(spec), "run": copy.deepcopy(run),
            "source": copy.deepcopy(source),
            "director_evidence": {"request": {"agent_request": {"context": context}}}}
        writer.commit(application.clock())


def synthetic_decision(request, *, outcome="satisfied", segment_outcomes=None):
    """Supply declared fixture judgments, never inferred real semantic quality."""
    agent = request["agent_request"]
    context = agent["context"]
    if agent["responsibility"] == "director":
        return {"objective": "Synthetic admitted goal for " + agent["method_id"],
                "constraints": "Use the selected source and retain the separate approval/application boundary.",
                "acceptance_criteria": [
                    {"criterion_id": identity, "description": "The exact selected result meets this fixture criterion."}
                    for identity in context["required_criterion_ids"]],
                "allowed_executor_ids": [item["executor_id"] for item in context["registered_teammates"]],
                "continuation_policy": copy.deepcopy(context["budget_ceiling"]),
                "rationale": "Explicit synthetic Director choice for an Owner behavior test."}
    if agent["responsibility"] == "manager":
        teammates = {item["role_id"]: item for item in context["allowed_teammates"]}
        pins = context["explicit_user_pins"]
        steps = []
        for stage in context["required_stages"]:
            role = teammates[stage["role_id"]]
            pin = next((item for item in pins if item["role_id"] == stage["role_id"]), None)
            steps.append({"stage_id": stage["stage_id"],
                          "node_id": pin["node_id"] if pin else "node." + stage["stage_id"],
                          "role_id": stage["role_id"], "instruction": "Complete this fixture task from exact admitted evidence.",
                          "route": role["default_route"],
                          "criterion_ids": list(pin["criterion_ids"] if pin else context["target_criterion_ids"])})
        for index, pin in enumerate(pins):
            if pin["node_id"] in {step["node_id"] for step in steps}:
                continue
            role = teammates[pin["role_id"]]
            steps.insert(0, {"stage_id": "fixture_optional_" + str(index), "node_id": pin["node_id"],
                             "role_id": pin["role_id"], "instruction": "Read only the pinned fixture evidence.",
                             "route": role["default_route"], "criterion_ids": list(pin["criterion_ids"])})
        return {"status": "compose", "rationale": "Explicit synthetic Manager choice.", "steps": steps}
    if agent["responsibility"] != "evaluator":
        raise ValueError("Specialist fixture decisions are method-specific")
    citable = list(context["citable_artifact_references"])

    def judgments(identities, chosen):
        return [{"criterion_id": identity, "outcome": chosen.get(identity, "not_run") if isinstance(chosen, dict) else chosen,
                 "reason": "Explicit synthetic criterion judgment for this Owner behavior test.",
                 "evidence_artifact_ids": citable if (chosen.get(identity, "not_run") if isinstance(chosen, dict) else chosen) == "satisfied" else []}
                for identity in identities]

    final = judgments([item["criterion_id"] for item in context["work_spec"]["acceptance_criteria"]], outcome)
    segments = context["segments"]
    return {"criterion_outcomes": final, "segment_outcomes": [
        {"graph_run_id": segment["graph_run_id"],
         "criterion_outcomes": judgments(segment["target_criterion_ids"],
             (segment_outcomes or {}).get(segment["graph_run_id"], outcome if index == len(segments) - 1 else "not_run"))}
        for index, segment in enumerate(segments)]}

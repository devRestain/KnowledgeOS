"""Small administrative views of retained Work evidence and maintained role sources.

No runner, source resolver, journal writer, or current-policy revalidation of history.
"""
from __future__ import annotations

import copy
import importlib.util
import json
import re

from ..adapters.core import AdmissionError, digest
from ..adapters.owner_journal import read_regular
from ..adapters.paths import resolve_beneath
from .work import graph_content_digest, record_digest

_RUN_ID = re.compile(r"[a-z][a-z0-9_.-]{0,159}\Z")
_ROUTE_FIELDS = ("provider", "model", "reasoning_effort", "think", "api_mode")
_OBSERVED_FIELDS = ("role_id", "responsibility", "provider", "model", "reasoning_effort",
                    "think", "route_name", "session_id", "prompt_digest",
                    "instructions_digest", "request_digest", "provider_returned_model")


def work_history(exops, actor, payload):
    exops._guard(actor, "read_work")
    limit, before = payload.get("limit", 30), payload.get("before")
    if (set(payload) - {"limit", "before"} or type(limit) is not int or not 1 <= limit <= 50
            or before is not None and (not isinstance(before, str) or not _RUN_ID.fullmatch(before))):
        raise AdmissionError("INVALID_PAYLOAD", "History needs a bounded limit and optional exact Work cursor")
    snapshot = exops.application.journal.read()
    records = [record for key, record in snapshot["intents"].items()
               if key.startswith("exops.work.") and isinstance(record, dict)
               and isinstance(record.get("run"), dict) and isinstance(record.get("spec"), dict)]
    records.sort(key=lambda item: (item.get("recorded_at", ""), item["run"]["work_run_id"]), reverse=True)
    if before is not None:
        position = next((index for index, item in enumerate(records)
                         if item["run"]["work_run_id"] == before), None)
        if position is None:
            raise AdmissionError("RESOURCE_STALE", "History cursor is unavailable; refresh recent Work")
        records = records[position + 1:]
    works = []
    for record in records[:limit]:
        run, spec = record["run"], record["spec"]
        assessment = record.get("assessment")
        evaluated = (isinstance(assessment, dict)
                     and record_digest(assessment) == run.get("latest_assessment_digest"))
        works.append({"work_run_id": run["work_run_id"], "work_id": spec["work_id"],
                      "objective": spec["objective"], "method_id": spec.get("method_contract_ref", {}).get("method_id"),
                      "team_id": spec.get("team_id"), "target_kind": spec.get("target_kind"),
                      "recorded_at": record.get("recorded_at"), "revision": run["revision"],
                      "run_state": "unknown" if record["application_state"] == "unknown" else run["state"],
                      "receipt_state": record["receipt"]["receipt_state"],
                      "dispatch_state": record["dispatch_state"], "application_state": record["application_state"],
                      "assessment_state": assessment["completion_state"] if evaluated else "unavailable",
                      "continuation_state": record.get("continuation_state"),
                      "segments_started": run["segments_started"]})
    return {"works": works, "next_before": works[-1]["work_run_id"] if len(records) > limit else None,
            "observed_at": snapshot["observed_at"], "order": "admission_newest_first",
            "runtime_observation": "unavailable"}


def _role_sources(control):
    module_spec = importlib.util.spec_from_file_location(
        "knowledgeos_console_role_policy", control / "ops/clients/hermes/role_policy.py")
    policy = importlib.util.module_from_spec(module_spec)
    module_spec.loader.exec_module(policy)
    manifest = json.loads(read_regular(control / "ops/config/agent-profiles.json"))
    return policy, manifest


def role_policy(exops, actor, payload, *, prompt=False):
    exops._guard(actor, "read_work")
    if (set(payload) != ({"role_id"} if prompt else set())
            or prompt and (not isinstance(payload["role_id"], str) or len(payload["role_id"]) > 160)):
        raise AdmissionError("INVALID_PAYLOAD", "Role sources take no filesystem selector")
    control = exops.application.roots.control
    try:
        policy, manifest = _role_sources(control)
        if prompt and payload["role_id"] not in manifest["profiles"]:
            raise AdmissionError("NOT_FOUND", "Role is not in the maintained policy")
        policy_digest = policy.policy_digest(control)
        if prompt:
            role_id = payload["role_id"]
            selected = policy.load_role(control, role_id)
            common_path = "ops/prompts/hermes/COMMON.md"
            soul_path = manifest["profiles"][role_id]["soul"]
            common = read_regular(resolve_beneath(control, common_path))
            soul = read_regular(resolve_beneath(control, soul_path))
            if digest(common + b"\n" + soul) != selected["prompt_digest"]:
                raise AdmissionError("RESOURCE_STALE", "Role prompt changed during source observation")
            return {"availability": "available", "kind": "current_configured_policy", "role_id": role_id,
                    "policy_digest": policy_digest, "prompt_digest": selected["prompt_digest"],
                    "composition": "COMMON bytes + LF + SOUL bytes (UTF-8, no normalization)",
                    "common": {"path": common_path, "content_digest": digest(common), "text": common.decode("utf-8")},
                    "soul": {"path": soul_path, "content_digest": digest(soul), "text": soul.decode("utf-8")}}
        catalog_raw = read_regular(control / "ops/config/team-catalog.json")
        catalog = json.loads(catalog_raw)
        purposes = {item["role_id"]: item for item in catalog.get("roles", [])}
        roles = []
        for role_id, profile in manifest["profiles"].items():
            selected = policy.load_role(control, role_id)
            definition = purposes.get(role_id, {})
            roles.append({"role_id": role_id, "purpose": definition.get("purpose"),
                          "kind": definition.get("kind"), "default_route": selected["route_name"],
                          "allowed_routes": list(selected["allowed_routes"]),
                          "prompt_digest": selected["prompt_digest"], "soul_path": profile["soul"],
                          "manual_homes": [name for name, role in manifest.get("home_roles", {}).items() if role == role_id]})
        return {"availability": "available", "kind": "current_configured_policy", "policy_digest": policy_digest,
                "routes": {name: {key: value for key, value in route.items() if key in _ROUTE_FIELDS}
                           for name, route in manifest["routes"].items()}, "roles": roles,
                "runtime_observation": "unavailable"}
    except AdmissionError:
        raise
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        return {"availability": "unavailable", "kind": "current_configured_policy",
                "reason": "Maintained role sources are unavailable in this Owner package",
                "roles": [], "routes": {}, "runtime_observation": "unavailable"}


def _invocation(source, request, response, *, graph_run_id=None, integrity="not_retained"):
    request = request if isinstance(request, dict) else {}
    agent = request.get("agent_request") or request.get("agent_step") or {}
    response = response if isinstance(response, dict) else {}
    native = response.get("agent_evidence", response)
    if not isinstance(native, dict):
        native = {}
    role_id = native.get("role_id") or agent.get("role_id")
    if not role_id:
        return None
    transport = native.get("transport", {})
    wire = transport.get("requests", []) if isinstance(transport, dict) else []
    metadata = {key: native[key] for key in _OBSERVED_FIELDS if key in native}
    traces = [{key: call[key] for key in ("name", "call_id") if key in call}
              | {"status": call.get("result", {}).get("status"),
                 "canonical_target_changed": call.get("result", {}).get("canonical_target_changed")}
              for call in native.get("tool_calls", [])[:64] if isinstance(call, dict)]
    return {"source": source, "role_id": role_id,
            "responsibility": native.get("responsibility") or agent.get("responsibility") or
                ("officer" if request.get("executor_id") and not agent else "specialist"),
            "graph_run_id": graph_run_id or agent.get("graph_run_id") or request.get("graph_run_id"),
            "stage_id": request.get("stage_id") or agent.get("stage_id"),
            "executor_id": agent.get("executor_id") or request.get("executor_id"),
            "observation_state": "integrity_mismatch" if integrity == "mismatch" else
                "observed" if native.get("session_id") and wire else
                "recorded_response" if native.get("session_id") else "unknown",
            "integrity": integrity, "observed": metadata,
            "requested_route": agent.get("route"),
            "owner_request_digest": request.get("request_digest"),
            "instructions": agent.get("instructions") or agent.get("instruction"),
            "instruction_scope": "retained_agent_request" if "agent_request" in request else "retained_manager_step",
            "historical_prompt_text": "not_retained",
            "runner_binding": {key: value for key, value in request.get("runner_binding", {}).items()
                               if key in {"runtime_kind", "hermes_version", "hermes_revision",
                                          "adapter_digest", "agent_policy_digest"}},
            "transport_requests": [{key: item[key] for key in (
                "model", "reasoning_effort", "think", "status_code", "request_digest", "wire_instructions_digest") if key in item}
                for item in wire[:64] if isinstance(item, dict)],
            "tool_calls": traces, "trace_truncated": len(native.get("tool_calls", [])) > 64 or len(wire) > 64}


def retained_execution(record, intents):
    """Select known evidence envelopes, never arbitrary recursive State or provider bodies."""
    invocations = []
    seen = {}

    def add(source, evidence, graph_id=None):
        if not isinstance(evidence, dict):
            return
        request, response = evidence.get("request"), evidence.get("response")
        if not isinstance(request, dict):
            return
        integrity = "not_retained"
        if "content_digest" in evidence and "decision" in evidence:
            integrity = "matches" if record_digest(evidence, ("content_digest",)) == evidence["content_digest"] else "mismatch"
        elif "evidence_digest" in evidence:
            integrity = "matches" if record_digest({"request": request, "response": response}) == evidence["evidence_digest"] else "mismatch"
        item = _invocation(source, request, response, graph_run_id=graph_id, integrity=integrity)
        if item is None:
            return
        item["evidence_digest"] = evidence.get("content_digest") or evidence.get("evidence_digest")
        identity = (item["observed"].get("session_id"), item["observed"].get("request_digest"), item["role_id"], item["graph_run_id"])
        if identity in seen:
            index = seen[identity]
            if integrity == "matches" and invocations[index]["integrity"] != "matches":
                invocations[index] = item
            return
        seen[identity] = len(invocations)
        invocations.append(item)

    for spec in [*record.get("steering_spec_history", []), record["spec"]]:
        director = intents.get("owner.director." + spec["admission_receipt_id"], {})
        add("director." + str(spec["work_spec_revision"]), director.get("director_evidence"))
    segments = []
    plans = record.get("agent_plans", {})
    for namespace in ("admitted_method_segments", "admitted_officer_segments"):
        for graph_id, admitted in record.get(namespace, {}).items():
            item = {"segment": copy.deepcopy(admitted["segment"]),
                    "assignments": copy.deepcopy(admitted.get("assignments", [])
                        or ([admitted["assignment"]] if "assignment" in admitted else []))}
            composed = plans.get(graph_id, {})
            bundle, plan = composed.get("bundle", {}), composed.get("plan")
            if not bundle:
                bundle = record.get("steering_bundles", {}).get(graph_id, {})
            matches = bundle.get("segment") == item["segment"] and bundle.get("assignments") == item["assignments"]
            graph = bundle.get("graph_spec")
            if matches and isinstance(graph, dict):
                matches = graph.get("graph_digest") == graph_content_digest(graph, bundle.get("nodes", []), bundle.get("edges", []))
            if matches and isinstance(plan, dict):
                matches = (plan.get("content_digest") == record_digest(plan, ("content_digest",))
                           and plan.get("bundle_digest") == record_digest(bundle))
            item.update(graph_availability="retained" if matches and graph else "unavailable",
                        graph_integrity="matches" if matches else "unavailable" if not bundle else "mismatch",
                        graph_spec=copy.deepcopy(graph) if matches else None,
                        nodes=copy.deepcopy(bundle.get("nodes", [])) if matches else [],
                        edges=copy.deepcopy(bundle.get("edges", [])) if matches else [],
                        plan=copy.deepcopy(plan) if matches else None)
            segments.append(item)
            add("manager." + graph_id, composed.get("evidence", {}).get("manager_decision"), graph_id)
    for key, value in record.items():
        if not isinstance(value, dict) or key in {"agent_plans", "steering_completion"}:
            continue
        if isinstance(value.get("request"), dict):
            add(key, value)
        for child in ("evidence", "agent_evidence"):
            if isinstance(value.get(child), dict):
                add(key + "." + child, value[child])
        if "agent_request" in value or "agent_step" in value:
            response_key = key.replace("_optional_request.", "_optional.") if "_optional_request." in key else key.removesuffix("_request")
            response = record.get(response_key)
            if isinstance(response, dict) and "response" in response:
                continue
            add(key, {"request": value, "response": response})
    for key, request in record.get("steering_optional_requests", {}).items():
        response = next((item for item in record.get("steering_optional", [])
                         if item.get("request_digest") == record_digest(request.get("agent_request", {}))), None)
        add("steering_optional." + key, {"request": request, "response": response})
    for key, intent in intents.items():
        if (key.startswith("curation.recovery.") and isinstance(intent, dict)
                and intent.get("target", {}).get("work_run_id") == record["run"]["work_run_id"]):
            add("recovery_assessment", intent.get("semantic_assessment", {}).get("evidence"))
    # Prefer a complete retained envelope over its pending request/attempt duplicate.
    completed = {(item["role_id"], item["graph_run_id"], item["stage_id"])
                 for item in invocations if item["observation_state"] != "unknown"}
    invocations = [item for item in invocations if item["observation_state"] != "unknown" or
                   (item["role_id"], item["graph_run_id"], item["stage_id"]) not in completed]
    assessments = []
    for key in ("curation_initial_semantic", "curation_initial_assessment", "steering_partial", "steering_final_semantic"):
        value = record.get(key, {})
        if isinstance(value.get("assessment"), dict):
            assessment = value["assessment"]
            if (record_digest(assessment) != record["run"].get("latest_assessment_digest")
                    and not any(item["assessment"] == assessment for item in assessments)):
                assessments.append({"source": key, "assessment": copy.deepcopy(assessment),
                                    "evaluations": copy.deepcopy(value.get("evaluations", [])),
                                    "remaining_work": copy.deepcopy(value.get("remaining_work"))})
    for item in segments:
        plan = item.get("plan") or {}
        for step in plan.get("steps", []):
            if not any(inv["graph_run_id"] == item["segment"]["graph_run_id"]
                       and (inv["stage_id"] == step["stage_id"] or inv["role_id"] == step["role_id"])
                       for inv in invocations):
                invocations.append({"source": "manager_plan", "role_id": step["role_id"],
                                    "responsibility": "specialist", "graph_run_id": item["segment"]["graph_run_id"],
                                    "stage_id": step["stage_id"], "executor_id": step["executor_id"],
                                    "observation_state": "not_observed", "observed": {},
                                    "instructions": step["instruction"], "requested_route": step["route"],
                                    "instruction_scope": "retained_manager_step",
                                    "historical_prompt_text": "not_retained"})
    segments.sort(key=lambda item: item["segment"]["segment_index"])
    role_observations = {}
    for responsibility in ("director", "manager", "specialist", "officer", "evaluator"):
        matches = [item for item in invocations if item["responsibility"] == responsibility]
        role_observations[responsibility] = (
            "observed" if any(item["observation_state"] == "observed" for item in matches) else
            "recorded_response" if any(item["observation_state"] == "recorded_response" for item in matches) else
            "not_applicable" if responsibility == "manager" and record["spec"].get("target_kind") == "officer" else
            "unavailable")
    return {"segments": segments, "invocations": invocations, "prior_assessments": assessments,
            "role_observations": role_observations,
            "work_spec_history": copy.deepcopy(record.get("steering_spec_history", [])),
            "historical_observation": "retained_owner_evidence_not_current_policy",
            "invocation_absence": "not_observed_is_not_proof_of_not_run"}

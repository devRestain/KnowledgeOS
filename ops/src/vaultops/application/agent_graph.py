"""Compile actual Manager decisions into owner-bound sequential Core Graphs.

Core digests retain their canonical contracts. The separate plan content_digest
hashes canonical JSON (via record_digest), binding directions and model routes to
bundle_digest, including assignments; persist both before any task dispatch.
"""
from __future__ import annotations

import copy
import re
from collections.abc import Mapping
from itertools import pairwise

from ..adapters.core import AdmissionError, load_json
from ..adapters.paths import resolve_beneath
from .agent_decisions import (
    admitted_owner_parameters,
    role_decision,
    source_context,
    validate_role_evidence,
)
from .work import graph_content_digest, record_digest, segment_digest
from .work_methods import WorkMethodCatalog

_ID = re.compile(r"[a-z][a-z0-9_-]*(?:\.[a-z0-9][a-z0-9_-]*)*")


def _require(condition, message):
    if not condition:
        raise AdmissionError("AGENT_PLAN_INVALID", message)


def _identity(value):
    return isinstance(value, str) and len(value) <= 96 and _ID.fullmatch(value) is not None


def _lineage(record, segment):
    if segment["segment_index"] == 1:
        return {}
    previous_id = segment.get("predecessor_graph_run_id")
    previous = record.get("admitted_method_segments", {}).get(previous_id)
    _require(isinstance(previous, Mapping), "Successor lacks its exact admitted predecessor")
    kind = segment["trigger_kind"]
    trigger = {"remaining_work": record.get("remaining_work"),
               "steering": record.get("steering_cutover"),
               "work_revision": record["spec"]}.get(kind)
    _require(isinstance(trigger, Mapping), "Successor lacks its exact owner-admitted trigger")
    return {"predecessor": previous["segment"], "trigger": trigger,
            "assessment": record.get("assessment")}


async def compose_graph(exops, runner, record, *, method_id, bundle, artifacts,
                        required_stage_ids=None):
    """Ask the actual bound Manager; compile its selected ordered tasks, or refuse.

    Seed identities, source references, lineage, engine and pins stay Owner-owned.
    required_stage_ids is an Owner-authorized segment subset, never model input.
    This function does not persist/admit the graph or execute any teammate.
    """
    app, contracts = exops.application, exops.contracts
    spec, run = record["spec"], record["run"]
    contracts.run(run, spec)
    exops._team_binding(spec, run)
    methods = WorkMethodCatalog.load(app.roots.control)
    method = methods.admitted_method(spec, contracts)
    _require(method["method_id"] == method_id, "Manager method differs from the admitted WorkSpec")
    binding = exops.team_for_spec(spec)
    catalog = methods.team_catalog
    catalog.validate_spec(spec, binding)
    compiled = copy.deepcopy(dict(bundle))
    exported = copy.deepcopy(dict(artifacts))
    graph, segment = compiled["graph_spec"], compiled["segment"]
    contracts.record("GraphSpec", graph)
    contracts.record("GraphSegment", segment)
    contracts.context(segment, spec, run)
    _require(all(graph[key] == segment[key] for key in ("graph_id", "graph_revision", "graph_digest")),
             "Seed Graph and segment identities differ")
    stages = methods.stages_for_segment(method, segment["segment_index"], required_stage_ids)
    required = {stage["stage_id"]: stage for stage in stages}
    all_stages = {stage["stage_id"]: stage for stage in method["required_stages"]}
    all_required_roles = {stage["role_id"] for stage in all_stages.values()}
    targets = segment["target_criterion_ids"]
    _require(bool(targets), "Manager segment has no admitted criterion targets")
    policy = load_json(resolve_beneath(app.roots.control, "ops/config/agent-profiles.json"))
    remaining = spec["continuation_policy"]["max_profile_invocations"] - run["profile_invocations"]
    limit = min(remaining, 62)  # Core GraphSpec permits at most 64 nodes including endpoints.
    _require(limit >= len(stages), "Required method stages exceed the remaining invocation budget")
    _require(segment["segment_index"] == run["segments_started"] + 1
             and segment["segment_index"] <= spec["continuation_policy"]["max_segments"],
             "Manager segment exceeds the admitted continuation budget")
    _require(not run.get("active_graph_run_id") and not run["pending_action_ids"]
             and not run["unresolved_effect_ids"], "Manager cannot plan across an unsafe Work boundary")
    registered = {}
    available = []
    for specialist in binding.specialists:
        role_id = specialist.role_id
        if specialist.executor_id not in spec["allowed_executor_ids"]:
            continue
        if role_id in all_required_roles and role_id not in {stage["role_id"] for stage in stages}:
            continue
        role = catalog.roles[role_id]
        if role_id not in all_required_roles and role["effect_class"] != "read_only":
            continue
        tools = methods.allowed_tools(spec, role_id, contracts)
        selected_policy = policy["profiles"].get(role_id)
        _require(isinstance(selected_policy, Mapping), "Admitted teammate lacks a committed role policy")
        routes = selected_policy["allowed_routes"]
        _require(isinstance(routes, list) and bool(routes)
                 and all(isinstance(route, str) and route in policy["routes"] for route in routes),
                 "Admitted teammate has invalid model routes")
        registered[role_id] = specialist
        available.append({"role_id": role_id, "executor_id": specialist.executor_id,
                          "profile": dict(specialist.profile), "purpose": role["purpose"],
                          "input_kinds": role["input_kinds"], "output_kinds": role["output_kinds"],
                          "effect_class": role["effect_class"], "tools": list(tools),
                          "allowed_routes": routes, "default_route": selected_policy["route"]})
    _require({stage["role_id"] for stage in stages} <= registered.keys(),
             "A required method teammate is not registered within the admitted envelope")
    pins = {pin["node_id"]: pin for pin in spec.get("explicit_assignments", [])}
    relevant_pins = [pin for pin in pins.values() if set(targets).intersection(pin["criterion_ids"])]
    owner_scope = admitted_owner_parameters(app, record)
    context = {"work_spec": copy.deepcopy(spec), "work_run": copy.deepcopy(run),
               "method": method, "required_stages": stages,
               "owner_scope": owner_scope,
               "allowed_teammates": available, "routes": policy["routes"],
               "explicit_user_pins": copy.deepcopy(relevant_pins),
               "target_criterion_ids": list(targets), "max_tasks": limit,
               "graph_identity": {key: graph[key] for key in ("graph_id", "graph_revision")},
               "segment_identity": {key: segment[key] for key in ("graph_run_id", "segment_index")},
               "input_references": copy.deepcopy(segment["input_references"])}
    if record.get("method_source_reference") is not None:
        context.update(source_context(app, record["method_source_reference"],
                                      gateway_receipt_id=owner_scope.get("gateway_receipt_id")))
    if segment["segment_index"] > 1:
        context["lineage"] = copy.deepcopy(_lineage(record, segment))
    manager_role = catalog.teams[spec["team_id"]]["manager_role_id"]
    decision, decision_evidence = await role_decision(
        runner, role_id=manager_role, responsibility="manager", method_id=method_id,
        context=context, record=record,
        instructions=(
            "Compose the executable ordered tasks for this admitted Work, not commentary about a fixed graph. "
            "Return one JSON object with status='compose', rationale (nonempty string), and steps (ordered list). "
            "Each step has exactly stage_id, node_id, role_id, instruction, route, criterion_ids. "
            "Use every required stage_id exactly once, in the supplied order and with its required role_id. "
            "You may insert useful optional stages with new unique stage_ids only for allowed read_only teammates. "
            "Select only allowed teammates, each at most once. Select a permitted route per role. "
            "Write substantive task-specific directions, evidence requirements and handoff expectations in instruction; "
            "never executable code or instructions expanding tool authority. Link every step to nonempty admitted "
            "target criterion_ids, collectively covering all targets. Preserve every explicit user node/profile/executor "
            "pin and its criterion_ids. If delegation_mode is explicit_assignments, use pinned nodes only. "
            "Tool argument and artifact content schemas belong to the method adapter. "
            "Do not invent additional payload keys or override its output format. "
            "required_evidence describes completion obligations, not values that must already exist at planning time. "
            "Plan the stages and declared human gates that obtain future inputs; do not require their outputs in advance "
            "or invent the eventual values. Ask only for a prerequisite that the admitted stages or gates cannot obtain. "
            "No required effects may be duplicated, no human gate bypassed, and task count must fit max_tasks. "
            "If the admitted goal cannot be planned, return status='reject' or status='question' with rationale instead; "
            "do not invent missing authority, a fallback route, source evidence or success."
        ))
    validate_role_evidence(app.roots.control, manager_role, decision_evidence["response"], tools=[])
    _require(isinstance(decision.get("rationale"), str) and decision["rationale"].strip(),
             "Manager plan lacks its rationale")
    _require(decision.get("status") == "compose",
             f"Manager {decision.get('status')}: {decision['rationale']}")
    steps = decision.get("steps")
    _require(isinstance(steps, list) and len(stages) <= len(steps) <= limit,
             "Manager task count exceeds the required stages or admitted budget")
    seed_nodes = {node["node_id"]: node for node in compiled["nodes"]}
    start = graph["start_node_id"]
    terminals = [node["node_id"] for node in compiled["nodes"] if node["node_kind"] == "terminal"]
    _require(start in seed_nodes and seed_nodes[start]["node_kind"] == "start" and len(terminals) == 1,
             "Owner seed lacks distinct sequential endpoints")
    finish = terminals[0]
    selected_steps, assignments = [], []
    seen_nodes, seen_stages, seen_roles = {start, finish}, set(), set()
    covered = set()
    for step in steps:
        _require(isinstance(step, dict) and set(step) == {
            "stage_id", "node_id", "role_id", "instruction", "route", "criterion_ids"},
            "Manager step has missing or unknown fields")
        stage_id, node_id, role_id = step["stage_id"], step["node_id"], step["role_id"]
        _require(_identity(stage_id) and _identity(node_id) and isinstance(role_id, str)
                 and node_id not in seen_nodes and stage_id not in seen_stages
                 and role_id in registered and role_id not in seen_roles,
                 "Manager step identity or selected teammate is invalid or duplicated")
        _require(isinstance(step["instruction"], str) and step["instruction"].strip(),
                 "Manager must supply a meaningful instruction for every selected task")
        criteria = step["criterion_ids"]
        _require(isinstance(criteria, list) and bool(criteria)
                 and all(isinstance(item, str) for item in criteria)
                 and len(criteria) == len(set(criteria)) and set(criteria) <= set(targets),
                 "Manager step expands or omits admitted criterion links")
        _require(isinstance(step["route"], str)
                 and step["route"] in policy["profiles"][role_id]["allowed_routes"],
                 "Manager selected a route outside the role policy")
        if stage_id in required:
            _require(role_id == required[stage_id]["role_id"], "Manager changed a required stage Profile")
        else:
            _require(stage_id not in all_stages and role_id not in all_required_roles,
                     "Optional task cannot replace a required method stage")
            methods.optional_tools(spec, role_id)
        specialist = registered[role_id]
        pin = pins.get(node_id)
        if pin is not None:
            _require(pin["executor_id"] == specialist.executor_id
                     and all(pin[key] == value for key, value in specialist.profile.items())
                     and set(pin["criterion_ids"]) == set(criteria),
                     "Manager changed an explicit user assignment or criterion pin")
        else:
            _require(spec["delegation_mode"] == "manager_bounded",
                     "Unpinned task exceeds explicit-only delegation")
        assignment = {"owner_operation_id": spec["owner_operation_id"],
                      "graph_id": graph["graph_id"], "graph_revision": graph["graph_revision"],
                      "node_id": node_id, "assignment_generation": run["generation"],
                      "executor_id": specialist.executor_id, **dict(specialist.profile),
                      "admission_receipt_id": spec["admission_receipt_id"],
                      "work_run_id": run["work_run_id"],
                      "work_spec_revision": spec["work_spec_revision"],
                      "work_spec_digest": spec["spec_digest"],
                      "selection_authority": "explicit_user" if pin is not None else "bounded_manager",
                      "semantic_version": graph["semantic_version"], "semantic_digest": graph["semantic_digest"]}
        assignments.append(assignment)
        selected_steps.append({**copy.deepcopy(step), "executor_id": specialist.executor_id,
                               "tools": list(methods.allowed_tools(spec, role_id, contracts))})
        seen_nodes.add(node_id)
        seen_stages.add(stage_id)
        seen_roles.add(role_id)
        covered.update(criteria)
    _require([step["stage_id"] for step in steps if step["stage_id"] in required] == list(required),
             "Manager omitted or reordered required method stages")
    _require(covered == set(targets) and all(pin["node_id"] in seen_nodes for pin in relevant_pins),
             "Manager omitted an admitted criterion target or explicit user pin")
    base = {key: graph[key] for key in ("graph_id", "graph_revision")}
    task_nodes = []
    for step in selected_steps:
        template = seed_nodes.get(step["node_id"])
        task_nodes.append({**base, "node_id": step["node_id"], "node_kind": "task", "max_attempts": 1,
                           "input_semantic_term_ids": list(template["input_semantic_term_ids"]) if template else [],
                           "output_semantic_term_ids": list(template["output_semantic_term_ids"]) if template else []})
    nodes = [seed_nodes[start], *task_nodes, seed_nodes[finish]]
    node_ids = [node["node_id"] for node in nodes]
    edges = [{**base, "edge_id": "edge." + str(index), "source_node_id": source,
              "target_node_id": target, "condition": "always"}
             for index, (source, target) in enumerate(pairwise(node_ids))]
    graph.update(node_ids=node_ids, edge_ids=[edge["edge_id"] for edge in edges], max_steps=len(nodes))
    graph["graph_digest"] = graph_content_digest(graph, nodes, edges)
    segment["graph_digest"] = graph["graph_digest"]
    segment["segment_digest"] = segment_digest(segment)
    compiled.update(nodes=nodes, edges=edges, assignments=assignments)
    methods.validate_segment(spec=spec, run=run, bundle=compiled, contracts=contracts,
                             artifacts=exported, required_stage_ids=required_stage_ids,
                             **_lineage(record, segment))
    catalog.validate_assignments(assignments, binding, spec)
    plan = {"owner_operation_id": spec["owner_operation_id"], "work_run_id": run["work_run_id"],
            "work_spec_revision": spec["work_spec_revision"], "work_spec_digest": spec["spec_digest"],
            "manager_executor_id": spec["manager_executor_id"], "manager_role_id": manager_role,
            "method_contract_ref": copy.deepcopy(spec["method_contract_ref"]),
            **base, "graph_run_id": segment["graph_run_id"],
            "graph_digest": graph["graph_digest"], "segment_digest": segment["segment_digest"],
            "bundle_digest": record_digest(compiled), "artifacts_digest": record_digest(exported),
            "route_policy_digest": record_digest(policy),
            "required_stage_ids": list(required), "rationale": decision["rationale"],
            "steps": selected_steps, "decision_digest": decision_evidence["content_digest"]}
    plan["content_digest"] = record_digest(plan)
    evidence = {"manager_decision": decision_evidence, "plan_digest": plan["content_digest"],
                "bundle_digest": plan["bundle_digest"], "graph_digest": graph["graph_digest"],
                "segment_digest": segment["segment_digest"]}
    evidence["content_digest"] = record_digest(evidence)
    return {"bundle": compiled, "artifacts": exported, "plan": plan, "evidence": evidence}

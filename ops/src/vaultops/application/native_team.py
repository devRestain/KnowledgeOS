"""Structural owner machinery for explicitly composed, method-scoped native Teams.

Policies select immutable method semantics. Concrete executors own tool evidence
and criterion validation; this module never treats model text as an assessment.
"""
from __future__ import annotations

import asyncio
import base64
import copy
import re
from collections.abc import Mapping
from dataclasses import dataclass
from itertools import pairwise
from pathlib import Path
from types import MappingProxyType

from ..adapters.core import AdmissionError, canonical, digest
from ..adapters.owner_journal import read_regular, stamp
from ..adapters.paths import resolve_beneath
from ..projection import source_properties
from .exops import OwnerActor
from .team_catalog import TeamBinding, TeamCatalog
from .temporal_work import WorkResult
from .work import (
    WorkContracts,
    graph_content_digest,
    record_digest,
    segment_digest,
    work_spec_digest,
)
from .work_methods import WorkMethodCatalog
from .work_tools import TeamToolSession

ENGINE_ID = "agentfabric.operation.temporal_langgraph"
ENGINE_VERSION = "0.1.0"
PACKAGES = {"temporalio": "1.34.0", "temporalio-langgraph": "0.1.0", "langgraph": "1.2.0"}
RUNTIME_IDENTITY = {"runtime_kind": "hermes_native", "hermes_version": "0.22.5",
                    "hermes_revision": "bf0203a794a07aac3ff764972ab9087baa4e4f4e"}


def require(condition, message):
    if not condition:
        raise AdmissionError("SCOPE_DENIED", message)


def validate_runner_binding(value):
    require(isinstance(value, Mapping)
            and set(value) == {*RUNTIME_IDENTITY, "adapter_digest", "agent_policy_digest"}
            and all(value.get(k) == v for k, v in RUNTIME_IDENTITY.items())
            and all(isinstance(value.get(key), str)
                    and re.fullmatch(r"sha256:[0-9a-f]{64}", value[key]) is not None
                    for key in ("adapter_digest", "agent_policy_digest")),
            "Native Team requires its exact role runtime, SOUL policy and adapter binding")
    return dict(value)


@dataclass(frozen=True)
class NativeTeamPolicy:
    namespace: str
    method_id: str
    team_id: str
    roles: tuple[str, str]
    criterion: Mapping
    stages: tuple[str, str]
    tools: Mapping
    modules: tuple[str, ...]

    def __post_init__(self):
        require(len(self.roles) == len(self.stages) == 2 and set(self.tools) == set(self.stages),
                "Native Team policy requires exactly two stages")
        object.__setattr__(self, "criterion", MappingProxyType(dict(self.criterion)))
        object.__setattr__(self, "tools", MappingProxyType({k: tuple(v) for k, v in self.tools.items()}))


def native_digest(policy, binding):
    modules = {}
    for name in (*policy.modules, "native_team.py", "temporal_contract.py"):
        raw = read_regular(Path(__file__).with_name(name))
        require(raw is not None, "Native Team execution module unavailable")
        modules[name] = digest(raw)
    return record_digest({"execution_modules": modules, "engine_id": ENGINE_ID,
                          "engine_version": ENGINE_VERSION, "packages": PACKAGES,
                          "runner_binding": validate_runner_binding(binding)})


def pins(app):
    selected = app.core.binding
    return {**{k: selected[k] for k in ("core_version", "core_digest", "semantic_version", "semantic_digest")},
            "schema_version": selected["schema_version_pin"]}


def source_bytes(app, reference):
    path = app.resolve_reference(dict(reference))
    app._mcp_source_policy(path)
    raw = read_regular(resolve_beneath(app.roots.vault, path))
    require(raw is not None and digest(raw) == reference["content_digest"], "Native source changed")
    properties = source_properties(path, raw)
    require(properties.get("ai_policy") not in {"local_only", "deny"}
            and properties.get("sensitivity") != "confidential", "Native source privacy denied")
    return path, raw


def retain_agent_plan(exops, record, composed):
    """Retain the actual Manager decision before admitting its immutable Graph."""
    graph_run_id = composed["bundle"]["segment"]["graph_run_id"]
    app = exops.application
    work_run_id = record["run"]["work_run_id"]
    with app.journal.writer() as writer:
        live = writer.value["intents"].get("exops.work." + work_run_id)
        require(live == record, "Work changed while its Manager composed a Graph")
        plans = live.setdefault("agent_plans", {})
        require(graph_run_id not in plans, "Manager Graph decision is already retained")
        plans[graph_run_id] = copy.deepcopy(composed)
        writer.commit(app.clock())
    return app.journal.read()["intents"]["exops.work." + work_run_id]


def agent_plan(record, graph_run_id=None):
    """Resolve retained instructions/routes without regenerating a seed Graph."""
    graph_run_id = graph_run_id or record["run"].get("active_graph_run_id")
    composed = record.get("agent_plans", {}).get(graph_run_id)
    require(isinstance(composed, dict), "Graph has no retained model Manager plan")
    plan, bundle = composed["plan"], composed["bundle"]
    require(plan["content_digest"] == record_digest(plan, ("content_digest",))
            and plan["bundle_digest"] == record_digest(bundle)
            and bundle["segment"]["graph_run_id"] == graph_run_id,
            "Retained Manager plan or Graph changed")
    admitted = record.get("admitted_method_segments", {}).get(graph_run_id)
    require(isinstance(admitted, dict)
            and admitted["segment"] == bundle["segment"]
            and admitted["assignments"] == bundle["assignments"],
            "Executing Graph differs from the Manager's admitted decision")
    return plan


def stage_step(record, stage, graph_run_id=None):
    steps = [step for step in agent_plan(record, graph_run_id)["steps"] if step["stage_id"] == stage]
    require(len(steps) == 1, "Stage has no unique admitted Manager instruction")
    return steps[0]


def readonly_step_request(exops, runner, record, step):
    """Build an optional read-only invocation for pre-dispatch evidence retention."""
    from .agent_decisions import admitted_owner_parameters, source_context

    app, run, spec = exops.application, record["run"], record["spec"]
    request = {"role_id": step["role_id"], "responsibility": "specialist",
               "method_id": spec["method_contract_ref"]["method_id"],
               "route": step["route"], "instructions": step["instruction"],
               "context": {"work_spec": spec, **source_context(app, record["method_source_reference"],
                   gateway_receipt_id=admitted_owner_parameters(app, record).get("gateway_receipt_id"))},
               "tools": list(step["tools"]), "work_run_id": run["work_run_id"],
               "graph_run_id": run["active_graph_run_id"], "executor_id": step["executor_id"],
               "control_root": str(app.roots.control)}
    body = {"request_kind": "role_invocation", "agent_request": request,
            "runner_binding": dict(runner.binding), "work_run_id": run["work_run_id"],
            "stage_id": step["stage_id"], "agent_step": dict(step)}
    return {**body, "request_digest": record_digest(body)}


def validate_readonly_response(exops, record, step, request, response):
    from .agent_decisions import validate_role_evidence

    validate_role_evidence(exops.application.roots.control, step["role_id"], response,
                           tools=step["tools"], route=step["route"])
    require(response.get("request_digest") == record_digest(request["agent_request"]),
            "Read-only role response belongs to another invocation")
    session = TeamToolSession(exops.application, record["run"]["work_run_id"], step["executor_id"],
                              record["run"]["active_graph_run_id"], exops)
    for call in response["tool_calls"]:
        require(call["name"] in step["tools"], "Read-only role escaped its selected tools")
        session.authorize(call["name"], call["arguments"])
        result = call["result"]
        require(result.get("schema_version") == 3 and result.get("operation") == call["name"]
                and result.get("status") in {"ok", "empty"} and result.get("error") is None
                and result.get("effect") == "read_only"
                and result.get("canonical_target_changed") is False,
                "Optional role lacks an actual successful read-only Owner receipt")


async def invoke_readonly_step(exops, runner, record, step, *, request=None):
    """Execute one selected read-only role; callers retain its request before dispatch."""
    request = request if request is not None else readonly_step_request(exops, runner, record, step)
    response = await runner(request)
    await asyncio.to_thread(validate_readonly_response, exops, record, step, request, response)
    return response


def native_admission(app, binding, source_reference, *, work_id, work_run_id,
                     admission_receipt_id, runner_binding, policy, objective=None):
    require(isinstance(binding, TeamBinding), "Native Team requires TeamBinding")
    TeamCatalog.load(app.roots.control).validate_binding(binding)
    require(binding.team_id == policy.team_id
            and set(policy.roles) <= {s.role_id for s in binding.specialists}
            and binding.native_binding_digest == native_digest(policy, runner_binding),
            "Native Team roles or executable binding differ")
    WorkContracts(app.core).record("ArtifactReference", binding.binding_reference)
    source_bytes(app, source_reference)
    objective = objective or ("Execute " + policy.method_id + " on this exact selected source")
    require(isinstance(objective, str) and 0 < len(objective.strip()) <= 512, "Invalid native objective")
    selected = pins(app)
    spec = {"owner_operation_id": "knowledgeos", "work_id": work_id,
            "work_spec_revision": 1, "criteria_revision": 1, "objective": objective,
            "constraints": "No unreviewed canonical effect; native runner " + record_digest(validate_runner_binding(runner_binding))
            + "; selected source " + record_digest(source_reference),
            "acceptance_criteria": [dict(policy.criterion)], "target_kind": "team",
            "team_id": binding.team_id, "manager_executor_id": binding.manager_executor_id,
            "eval_officer_executor_id": binding.eval_officer_executor_id,
            "allowed_executor_ids": [s.executor_id for s in binding.specialists],
            "allowed_profiles": [dict(s.profile) for s in binding.specialists],
            "delegation_mode": "manager_bounded", "explicit_assignments": [],
            "continuation_policy": {"max_segments": 1, "max_repeated_gap_assessments": 1,
                                    "max_profile_invocations": len(binding.specialists)},
            "execution_profile": "sequential_graph", "admission_receipt_id": admission_receipt_id,
            "steerable_parameter_ids": [],
            "method_contract_ref": WorkMethodCatalog.load(app.roots.control).reference(policy.method_id), **selected}
    spec["spec_digest"] = work_spec_digest(spec)
    run = {"owner_operation_id": "knowledgeos", "work_id": work_id, "work_run_id": work_run_id,
           "work_spec_revision": 1, "work_spec_digest": spec["spec_digest"], "revision": 1,
           "control_revision": 1, "generation": 1,
           "fencing_token": policy.namespace + "." + record_digest({"work_run_id": work_run_id})[7:],
           "state": "admitted", "graph_run_ids": [], "segments_started": 0, "profile_invocations": 0,
           "repeated_gap_count": 0, "pending_action_ids": [], "unresolved_effect_ids": [],
           "binding_reference": dict(binding.binding_reference), **selected}
    WorkContracts(app.core).run(run, spec)
    return spec, run


def source_artifact(reference, raw):
    return {"owner_operation_id": "knowledgeos", "artifact_id": reference["logical_id"],
            "source_revision": reference["source_revision"], "content_digest": digest(raw),
            "media_type": "text/markdown", "size_bytes": len(raw),
            "classification": reference["classification"], "availability": "available_by_owner_export",
            "export_capability_id": "knowledgeos.artifact_read"}


class NativeTeamExecutor:
    def __init__(self, exops, *, runner, policy):
        self.exops, self.runner, self.policy = exops, runner, policy
        self.runner_binding = validate_runner_binding(runner.binding)
        require(policy.method_id not in exops.method_executors, "Native owner already has this method executor")
        exops.method_executors[policy.method_id] = self

    def admission(self, binding, source, **identities):
        return native_admission(self.exops.application, binding, source,
                                runner_binding=self.runner_binding, policy=self.policy, **identities)

    def current(self, work_run_id):
        exops = self.exops
        require(self.runner.binding == self.runner_binding, "Native runner binding changed")
        exops._current_binding()
        record = exops.application.journal.read()["intents"].get("exops.work." + work_run_id)
        require(isinstance(record, dict) and record.get("application_state") == "started", "No active native owner attempt")
        spec, run = record["spec"], record["run"]
        binding = exops.team_for_spec(spec)
        require(binding is not None and binding.team_id == self.policy.team_id
                and binding.native_binding_digest == native_digest(self.policy, self.runner_binding)
                and spec["method_contract_ref"] == WorkMethodCatalog.load(exops.application.roots.control).reference(self.policy.method_id)
                and self.policy.criterion["criterion_id"] in {c["criterion_id"] for c in spec["acceptance_criteria"]},
                "Native method, admitted Team, or runtime changed")
        source_bytes(exops.application, record["method_source_reference"])
        exops._team_binding(spec, run)
        require(exops.director_admitted(spec) and exops.director_method_source_admitted is not None
                and exops.director_method_source_admitted(spec, record["method_source_reference"]),
                "Exact Director source admission is no longer current")
        exops.contracts.run(run, spec)
        require(run["state"] == "running" and not run["pending_action_ids"] and not run["unresolved_effect_ids"],
                "Native Work is not effect-safe")
        exops._guard(OwnerActor(record["actor_id"], record["actor_ingress"]), "submit_work")
        self.validate_current(record)
        return record

    _current = current

    def validate_current(self, record):
        """Concrete methods may recheck additional immutable source authority."""


    def save(self, record, key, value):
        require(len(canonical(value)) <= 256 * 1024, "Native evidence exceeds owner bound")
        app = self.exops.application
        with app.journal.writer() as writer:
            live = writer.value["intents"].get("exops.work." + record["run"]["work_run_id"])
            require(live == record and key not in live, "Native stage changed or repeated")
            self.current(record["run"]["work_run_id"])
            live[key] = copy.deepcopy(value)
            writer.value["history"].append({"event": key, "work_run_id": record["run"]["work_run_id"],
                                           "sequence": writer.value["revision"] + 1})
            writer.commit(app.clock())

    def bundle(self, spec, run, source):
        p, app = self.policy, self.exops.application
        suffix = record_digest({"work_run_id": run["work_run_id"], "spec_digest": spec["spec_digest"]})[7:39]
        base = {"graph_id": "graph." + p.namespace + "." + suffix, "graph_revision": 1}
        binding = self.exops.team_for_spec(spec)
        specialists = {s.role_id: s for s in binding.specialists if s.executor_id in spec["allowed_executor_ids"]}
        require(set(p.roles) <= specialists.keys(), "Director omitted a required method specialist")
        required = [specialists[role] for role in p.roles]
        ids = ["node.start", *("node." + s for s in p.stages), "node.finish"]
        nodes = [{**base, "node_id": n, "node_kind": kind, "max_attempts": 1,
                  "input_semantic_term_ids": [], "output_semantic_term_ids": []}
                 for n, kind in zip(ids, ("start", "task", "task", "terminal"))]
        edges = [{**base, "edge_id": "edge." + str(i), "source_node_id": a,
                  "target_node_id": b, "condition": "always"} for i, (a, b) in enumerate(pairwise(ids))]
        selected = pins(app)
        graph = {**base, "owner_operation_id": "knowledgeos", "profile": "graph_enabled",
                 "engine_id": ENGINE_ID, "engine_version": ENGINE_VERSION, "start_node_id": ids[0],
                 "node_ids": ids, "edge_ids": [e["edge_id"] for e in edges], "max_steps": 4,
                 "input_semantic_term_ids": [], "output_semantic_term_ids": [], **selected}
        graph["graph_digest"] = graph_content_digest(graph, nodes, edges)
        _, raw = source_bytes(app, source)
        artifact = source_artifact(source, raw)
        segment = {**{k: run[k] for k in ("owner_operation_id", "work_id", "work_run_id", "work_spec_revision", "work_spec_digest")},
                   **base, **selected, "graph_run_id": "graphrun." + p.namespace + "." + suffix,
                   "graph_digest": graph["graph_digest"], "segment_index": 1,
                   "target_criterion_ids": [p.criterion["criterion_id"]], "trigger_kind": "initial",
                   "input_references": [artifact]}
        segment["segment_digest"] = segment_digest(segment)
        assignments = [{**base, "owner_operation_id": "knowledgeos", "node_id": node,
                        "assignment_generation": run["generation"], "executor_id": executor, **profile,
                        "admission_receipt_id": spec["admission_receipt_id"], "work_run_id": run["work_run_id"],
                        "work_spec_revision": spec["work_spec_revision"], "work_spec_digest": spec["spec_digest"],
                        "selection_authority": "bounded_manager", "semantic_version": selected["semantic_version"],
                        "semantic_digest": selected["semantic_digest"]}
                       for node, executor, profile in
                       ((node, specialist.executor_id, dict(specialist.profile))
                        for node, specialist in zip(ids[1:3], required))]
        return {"segment": segment, "graph_spec": graph, "nodes": nodes, "edges": edges,
                "assignments": assignments}, {artifact["artifact_id"]: artifact}

    async def manager(self, spec, run):
        from .agent_graph import compose_graph

        record = self.current(run["work_run_id"])
        require(record["spec"] == dict(spec) and record["run"] == dict(run) and not run["graph_run_ids"],
                "Native Manager input changed or repeated")
        session = TeamToolSession(self.exops.application, run["work_run_id"], spec["manager_executor_id"], exops=self.exops)
        require(session.method_view()["method_contract_ref"] == spec["method_contract_ref"], "Manager method changed")
        bundle, artifacts = self.bundle(spec, run, record["method_source_reference"])
        composed = await compose_graph(self.exops, self.runner, record, method_id=self.policy.method_id,
                                       bundle=bundle, artifacts=artifacts)
        retain_agent_plan(self.exops, record, composed)
        session.propose_graph({"bundle": composed["bundle"], "artifacts": composed["artifacts"]})

    def segment(self, record):
        run = record["run"]
        plan = agent_plan(record)
        retained = record["agent_plans"][run["active_graph_run_id"]]
        bundle = retained["bundle"]
        admitted = record.get("admitted_method_segments", {})
        require(len(admitted) == 1 and run["graph_run_ids"] == list(admitted)
                and run["segments_started"] == 1 and run["profile_invocations"] == len(plan["steps"]),
                "Native Team differs from its admitted Profile plan")
        selected = admitted[run["active_graph_run_id"]]
        require(selected.get("source_reference") == record["method_source_reference"]
                and selected.get("native_binding_digest") == native_digest(self.policy, self.runner_binding),
                "Native immutable source or runtime changed")
        return bundle["segment"]

    def step(self, record, stage):
        return stage_step(record, stage)

    def handoffs(self, record, stage):
        handoffs = []
        for step in agent_plan(record)["steps"]:
            if step["stage_id"] == stage:
                return handoffs
            execution = record.get(self.policy.namespace + "_" + step["stage_id"])
            require(isinstance(execution, dict), "Earlier selected Profile has not completed")
            response = execution["response"]
            native = response.get("agent_evidence", response)
            handoffs.append({"stage_id": step["stage_id"], "role_id": step["role_id"],
                             "executor_id": step["executor_id"], "session_id": native["session_id"],
                             "prompt_digest": native["prompt_digest"], "final_text": native["final_text"]})
        require(False, "Stage is not in the admitted Manager plan")

    def request_context(self, record, stage):
        raise NotImplementedError

    def request(self, record, stage):
        self.segment(record)
        require(stage in self.policy.stages, "Unknown native stage")
        run, spec = record["run"], record["spec"]
        step = self.step(record, stage)
        session = TeamToolSession(self.exops.application, run["work_run_id"], step["executor_id"],
                                  run["active_graph_run_id"], self.exops)
        require(session.tools() == self.policy.tools[stage], "Native stage grants changed")
        request = {"runner_binding": self.runner_binding, "stage_id": stage, "method_id": self.policy.method_id,
                   "work_run_id": run["work_run_id"], "work_spec_digest": spec["spec_digest"],
                   "graph_run_id": run["active_graph_run_id"], "executor_id": step["executor_id"],
                   "generation": run["generation"], "fencing_token": run["fencing_token"],
                   "source_reference": record["method_source_reference"],
                   "control_root": str(self.exops.application.roots.control),
                   "agent_step": step, "agent_handoffs": self.handoffs(record, stage),
                   **self.request_context(record, stage)}
        require(len(canonical(request)) <= 65000, "Native request exceeds bound")
        return {"request_digest": record_digest(request), **request}

    def validate_stage(self, record, request, response):
        raise NotImplementedError

    def response(self, record, request, response):
        from .agent_decisions import validate_role_evidence

        require(request == self.request(record, request["stage_id"]), "Native request changed")
        step = self.step(record, request["stage_id"])
        require(isinstance(response, dict) and set(response) == {"request_digest", "runner_binding", "model", "provider",
                "session_id", "final_text", "tool_calls", "exposed_tools", "agent_evidence"}
                and response["request_digest"] == request["request_digest"] and response["runner_binding"] == self.runner_binding
                and isinstance(response["session_id"], str) and 0 < len(response["session_id"]) <= 256
                and isinstance(response["final_text"], str) and 0 < len(response["final_text"]) <= 16384
                and response["exposed_tools"] == list(self.policy.tools[request["stage_id"]]),
                "Native response changed binding, session or tools")
        native = response["agent_evidence"]
        validate_role_evidence(self.exops.application.roots.control, step["role_id"], native,
                               tools=step["tools"], route=step["route"])
        require(all(response[key] == native[key] for key in
                    ("provider", "model", "session_id", "final_text", "exposed_tools"))
                and response["tool_calls"] == [{k: v for k, v in call.items() if k != "call_id"}
                                               for call in native["tool_calls"]],
                "Native adapter changed the actual specialist evidence")
        calls = response["tool_calls"]
        require(isinstance(calls, list) and 1 <= len(calls) <= 64, "Native trace missing or unbounded")
        session = TeamToolSession(self.exops.application, request["work_run_id"], request["executor_id"], request["graph_run_id"], self.exops)
        for call in calls:
            require(isinstance(call, dict) and {"name", "arguments", "result"} <= set(call)
                    and set(call) <= {"name", "arguments", "result", "raw_callback_name"}
                    and call["name"] in self.policy.tools[request["stage_id"]]
                    and isinstance(call["arguments"], dict) and isinstance(call["result"], dict), "Ungrantable native tool trace")
            session.authorize(call["name"], call["arguments"])
            envelope = call["result"]
            require(envelope.get("schema_version") == 3 and envelope.get("operation") == call["name"]
                    and envelope.get("error") is None and envelope.get("canonical_target_changed") is False,
                    "Native trace has no successful exact Owner envelope")
        return self.validate_stage(record, request, response)

    def evidence(self, record, stage):
        execution = record.get(self.policy.namespace + "_" + stage)
        require(isinstance(execution, dict) and set(execution) == {"request", "response", "evidence_digest"}
                and execution["evidence_digest"] == record_digest({"request": execution["request"], "response": execution["response"]}),
                "Native stage evidence changed or missing")
        return self.response(record, execution["request"], execution["response"])

    def _prepare_stage(self, work_run_id, stage):
        record = self.current(work_run_id)
        step = self.step(record, stage)
        if stage in self.policy.stages:
            request = self.request(record, stage)
        else:
            request = readonly_step_request(self.exops, self.runner, record, step)
            request["agent_request"]["context"]["agent_handoffs"] = self.handoffs(record, stage)
            request["request_digest"] = record_digest(request, ("request_digest",))
        self.save(record, self.policy.namespace + "_" + stage + "_request", request)
        return record, step, request

    def _retain_stage(self, work_run_id, stage, request, response):
        record = self.current(work_run_id)
        evidence = {"request": request, "response": response}
        self.save(record, self.policy.namespace + "_" + stage + "_attempt", evidence)
        record = self.current(work_run_id)
        if stage in self.policy.stages:
            self.response(record, request, response)
        else:
            validate_readonly_response(self.exops, record, self.step(record, stage), request, response)
        self.save(record, self.policy.namespace + "_" + stage, {**evidence, "evidence_digest": record_digest(evidence)})

    async def execute_stage(self, work_run_id, stage):
        _record, _step, request = await asyncio.to_thread(self._prepare_stage, work_run_id, stage)
        response = await self.runner(copy.deepcopy(request))
        await asyncio.to_thread(self._retain_stage, work_run_id, stage, request, response)

    def result_call(self, execution):
        calls = execution["response"]["tool_calls"]
        require(len(calls) == 1 and calls[0]["name"] == "artifact_submit", "Result needs one artifact effect")
        return calls[0]

    def validate_result(self, record, candidate):
        raise NotImplementedError

    def _prepare_assessment(self, work_run_id):
        record = self.current(work_run_id)
        key = self.policy.namespace + "_assessed_at"
        if key not in record:
            self.save(record, key, stamp(self.exops.application.clock()))
        record = self.current(work_run_id)
        structural = self.assess_record(record)
        return record, structural, self.assessment_evidence(record)

    def _retain_assessment(self, work_run_id, record, judged):
        require(self.current(work_run_id) == record, "Work or source changed during semantic assessment")
        app = self.exops.application
        with app.journal.writer() as writer:
            live = writer.value["intents"].get("exops.work." + work_run_id)
            require(live == record, "Work changed before semantic evidence retention")
            live[self.policy.namespace + "_semantic_assessment"] = judged["evidence"]
            if judged["remaining_work"] is not None:
                live["remaining_work"] = copy.deepcopy(judged["remaining_work"])
                live["continuation_state"] = "human_required"
            writer.value["history"].append({"event": self.policy.namespace + "_semantic_assessment",
                                            "work_run_id": work_run_id, "sequence": writer.value["revision"] + 1})
            writer.commit(app.clock())
        return judged["result"]

    async def assess(self, work_run_id):
        from .agent_assessment import assess_result

        record, structural, evidence = await asyncio.to_thread(self._prepare_assessment, work_run_id)
        judged = await assess_result(self.exops, self.runner, record, structural, evidence=evidence)
        return await asyncio.to_thread(self._retain_assessment, work_run_id, record, judged)

    def assessment_observations(self, record):
        return {}

    def assessment_evidence(self, record):
        from .agent_decisions import source_context

        execution = record[self.policy.namespace + "_" + self.policy.stages[-1]]
        artifact, raw = self.result_artifact(record, execution)
        return {
            "sources": [source_context(self.exops.application, record["method_source_reference"])],
            "artifacts": [{"artifact_reference": artifact, "content_text": raw.decode("utf-8")}],
            "observations": {"manager_plan": agent_plan(record), "specialist_results": [
                {"step": step, "response": record[self.policy.namespace + "_" + step["stage_id"]]["response"]}
                for step in agent_plan(record)["steps"]], **self.assessment_observations(record)}}

    def final_result(self, record):
        from .agent_assessment import compile_assessment

        retained = record.get(self.policy.namespace + "_semantic_assessment")
        require(isinstance(retained, dict), "Native result lacks its actual independent semantic judgment")
        judged = compile_assessment(self.exops, self.runner, record, self.assess_record(record),
                                     evidence=self.assessment_evidence(record), decision_evidence=retained)
        require(record.get("remaining_work") == judged["remaining_work"],
                "Current RemainingWork differs from the independent judgment")
        return judged["result"]

    def effect_payload(self, call):
        return {k: v for k, v in call["arguments"].items() if k != "idempotency_key"}

    def result_artifact(self, record, execution):
        """Validate a standard proposal effect and return its exact artifact bytes."""
        app, run = self.exops.application, record["run"]
        call = self.result_call(execution)
        args, data = call["arguments"], call["result"]["data"]
        reference, artifact = data["artifact_reference"], data["work_artifact_reference"]
        path = app.resolve_reference(reference, proposal=True)
        raw = read_regular(resolve_beneath(app.roots.vault, path))
        require(raw is not None and digest(raw) == artifact["content_digest"] and len(raw) == artifact["size_bytes"]
                and record.get("submitted_artifacts") == [reference]
                and record.get("artifact_references") == {artifact["artifact_id"]: artifact}, "Exact artifact registration or bytes changed")
        snapshot = app.journal.read()
        effect = snapshot["intents"].get(data["effect_id"], {})
        receipt = snapshot["receipts"].get(data["effect_id"], {}).get("execution", {})
        payload = self.effect_payload(call)
        identity = {"action": call["name"], "source": record["method_source_reference"], "payload": payload,
                    "idempotency_key": args.get("idempotency_key") or digest(canonical(payload)),
                    "work_run_id": run["work_run_id"], "graph_run_id": run["active_graph_run_id"],
                    "executor_id": self.step(record, self.policy.stages[-1])["executor_id"]}
        effects = [item for item in snapshot["intents"].values() if item.get("identity", {}).get("work_run_id") == run["work_run_id"]]
        require(len(effects) == 1 and effect.get("state") == "completed" and effect.get("action") == call["name"]
                and effect.get("artifact_path") == path and effect.get("source_path") == app.resolve_reference(record["method_source_reference"])
                and effect.get("source_digest") == record["method_source_reference"]["content_digest"]
                and effect.get("content_digest") == digest(raw) and base64.b64decode(effect["frozen_artifact"], validate=True) == raw
                and effect.get("identity") == identity and all(receipt.get(k) == effect.get(k) for k in
                    ("effect_id", "owner_operation_id", "content_digest", "generation", "fencing_token"))
                and receipt.get("outcome") == "succeeded" and receipt.get("result_digest") == digest(raw),
                "Artifact lacks exact effect identity and successful receipt")
        return artifact, raw

    def assess_record(self, record):
        """Validate structure, bytes and effects only; semantic judgment is separate."""
        app, p = self.exops.application, self.policy
        spec, run = record["spec"], record["run"]
        evaluator = spec["eval_officer_executor_id"]
        require(evaluator not in [spec["manager_executor_id"], *spec["allowed_executor_ids"]], "EvalOfficer must be independent")
        segment = self.segment(record)
        for stage in p.stages:
            candidate = self.evidence(record, stage)
        self.validate_result(record, candidate)
        execution = record[p.namespace + "_" + p.stages[-1]]
        artifact, raw = self.result_artifact(record, execution)
        require(app.journal.read()["intents"]["exops.work." + run["work_run_id"]] == record, "Work changed during independent assessment")
        checkpoint = record_digest({"graph_run_id": run["active_graph_run_id"], "artifact_digest": digest(raw),
                                    "stage_digests": [record[p.namespace + "_" + step["stage_id"]]["evidence_digest"]
                                                      for step in agent_plan(record)["steps"]]})
        graph_run_id = run["active_graph_run_id"]
        candidate = {**copy.deepcopy(run), "revision": run["revision"] + 1,
                     "control_revision": run["control_revision"] + 1,
                     "state": "completed", "result_reference": artifact}
        candidate.pop("active_graph_run_id")
        return WorkResult(candidate, {"assessed_at": record[p.namespace + "_assessed_at"]},
                          (segment,), (), {artifact["artifact_id"]: artifact},
                          {graph_run_id: checkpoint}, evaluator)

"""Bounded source-only native Evidence Research Team and independent assessment."""

from __future__ import annotations

import base64
import copy
import json
import re
from collections.abc import Mapping
from itertools import pairwise
from pathlib import Path
from types import SimpleNamespace

from ..adapters.core import AdmissionError, canonical, digest
from ..adapters.owner_journal import read_regular, stamp
from ..adapters.paths import resolve_beneath
from ..interfaces.exops_web import _validate_research_report
from ..interfaces.mcp_server import _convert_report, _operation
from ..projection import source_properties
from ..retrieval import _chunks_for_note
from .exops import KnowledgeExOps, OwnerActor
from .read_tools import KnowledgeReadTools
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
METHOD_ID = "knowledgeos.cited_research_team"
CRITERION = {
    "criterion_id": "criterion.cited_report",
    "description": "Complete ordered scoped retrieval and verified exact citations precede one receipt-bound noncanonical document enhancement report with per-improvement citation indices and explicit uncertainty and missing materials; claim truth is not evaluated.",
}
PACKAGES = {"temporalio": "1.34.0", "temporalio-langgraph": "0.1.0", "langgraph": "1.2.0"}
_ROUTE = {
    "runtime_kind": "hermes_native",
    "hermes_version": "0.22.5",
    "hermes_revision": "bf0203a794a07aac3ff764972ab9087baa4e4f4e",
    "provider": "openai-codex",
    "model": "gpt-6.1-terra",
}
STAGE_TOOLS = {
    "retrieve_evidence": ("knowledge_search", "knowledge_retrieve", "knowledge_read", "citation_verify"),
    "answer_artifact": ("artifact_submit",),
}
_ROLES = ("knowledgeos.retriever", "knowledgeos.synthesizer")


def _require(condition, message):
    if not condition:
        raise AdmissionError("SCOPE_DENIED", message)


def _runner_binding(value):
    _require(
        isinstance(value, Mapping)
        and set(value) == {*_ROUTE, "adapter_digest"}
        and all(value.get(k) == v for k, v in _ROUTE.items())
        and isinstance(value.get("adapter_digest"), str)
        and re.fullmatch(r"sha256:[0-9a-f]{64}", value["adapter_digest"]) is not None,
        "Research requires the exact native Hermes runtime binding",
    )
    return dict(value)


def research_native_digest(runner_binding):
    modules = {}
    for name in (
        "research_work.py",
        "research_temporal.py",
        "research_workflow.py",
        "temporal_contract.py",
    ):
        raw = read_regular(Path(__file__).with_name(name))
        _require(raw is not None, "Research execution module is unavailable")
        modules[name] = digest(raw)
    return record_digest(
        {
            "execution_modules": modules,
            "engine_id": ENGINE_ID,
            "engine_version": ENGINE_VERSION,
            "packages": PACKAGES,
            "runner_binding": _runner_binding(runner_binding),
        }
    )


def _pins(app):
    selected = app.core.binding
    return {
        **{
            k: selected[k]
            for k in ("core_version", "core_digest", "semantic_version", "semantic_digest")
        },
        "schema_version": selected["schema_version_pin"],
    }


def _binding(app, binding, runner_binding):
    _require(isinstance(binding, TeamBinding), "Research requires a TeamBinding")
    catalog = TeamCatalog.load(app.roots.control)
    catalog.validate_binding(binding)
    _require(
        binding.team_id == "evidence-research"
        and [s.role_id for s in binding.specialists] == list(_ROLES)
        and binding.native_binding_digest == research_native_digest(runner_binding),
        "Research requires exact selected Team and native runtime binding",
    )
    WorkContracts(app.core).record("ArtifactReference", binding.binding_reference)


def research_admission(
    application,
    binding,
    source_reference,
    *,
    work_id,
    work_run_id,
    admission_receipt_id,
    runner_binding,
    question="Produce a cited research report to strengthen this exact document",
):
    """Build exact records; Director source/spec admission is a separate authority."""
    _binding(application, binding, runner_binding)
    _require(isinstance(question, str) and 0 < len(question.strip()) <= 512, "Research question must be bounded")
    _source(application, source_reference)
    pins = _pins(application)
    spec = {
        "owner_operation_id": "knowledgeos",
        "work_id": work_id,
        "work_spec_revision": 1,
        "criteria_revision": 1,
        "objective": question,
        "constraints": "No canonical apply or source mutation; native runner "
        + record_digest(_runner_binding(runner_binding))
        + "; selected source "
        + record_digest(source_reference),
        "acceptance_criteria": [dict(CRITERION)],
        "target_kind": "team",
        "team_id": binding.team_id,
        "manager_executor_id": binding.manager_executor_id,
        "eval_officer_executor_id": binding.eval_officer_executor_id,
        "allowed_executor_ids": [s.executor_id for s in binding.specialists],
        "allowed_profiles": [dict(s.profile) for s in binding.specialists],
        "delegation_mode": "explicit_assignments",
        "explicit_assignments": [
            {
                "node_id": node,
                "executor_id": specialist.executor_id,
                **dict(specialist.profile),
                "criterion_ids": [CRITERION["criterion_id"]],
            }
            for node, specialist in zip(
                ("node.retrieve_evidence", "node.answer_artifact"), binding.specialists
            )
        ],
        "continuation_policy": {
            "max_segments": 1,
            "max_repeated_gap_assessments": 1,
            "max_profile_invocations": 2,
        },
        "execution_profile": "sequential_graph",
        "admission_receipt_id": admission_receipt_id,
        "steerable_parameter_ids": [],
        "method_contract_ref": WorkMethodCatalog.load(application.roots.control).reference(
            METHOD_ID
        ),
        **pins,
    }
    spec["spec_digest"] = work_spec_digest(spec)
    run = {
        "owner_operation_id": "knowledgeos",
        "work_id": work_id,
        "work_run_id": work_run_id,
        "work_spec_revision": 1,
        "work_spec_digest": spec["spec_digest"],
        "revision": 1,
        "control_revision": 1,
        "generation": 1,
        "fencing_token": "research." + record_digest({"work_run_id": work_run_id})[7:],
        "state": "admitted",
        "graph_run_ids": [],
        "segments_started": 0,
        "profile_invocations": 0,
        "repeated_gap_count": 0,
        "pending_action_ids": [],
        "unresolved_effect_ids": [],
        "binding_reference": dict(binding.binding_reference),
        **pins,
    }
    WorkContracts(application.core).run(run, spec)
    return spec, run


def _current(exops, work_run_id, runner_binding, *, application_states=("started",)):
    exops._current_binding()
    record = exops.application.journal.read()["intents"].get("exops.work." + work_run_id)
    _require(
        isinstance(record, dict) and record.get("application_state") in application_states,
        "Research has no active owner attempt",
    )
    spec, run = record["spec"], record["run"]
    binding = exops.teams.get(spec.get("team_id"))
    source = record.get("method_source_reference")
    expected, _ = research_admission(
        exops.application,
        binding,
        source,
        work_id=spec["work_id"],
        work_run_id=work_run_id,
        admission_receipt_id=spec["admission_receipt_id"],
        runner_binding=runner_binding,
        question=spec["objective"],
    )
    _require(spec == expected, "Unsupported research WorkSpec or criterion semantics")
    exops._team_binding(spec, run)
    _require(
        exops.director_admitted(spec)
        and exops.director_method_source_admitted is not None
        and exops.director_method_source_admitted(spec, source),
        "Exact research source admission is not current",
    )
    exops.contracts.run(run, spec)
    _require(
        run["state"] == "running"
        and not run["pending_action_ids"]
        and not run["unresolved_effect_ids"],
        "Research Work is not effect-safe",
    )
    return record


def _artifact(reference, raw):
    return {
        "owner_operation_id": "knowledgeos",
        "artifact_id": reference["logical_id"],
        "source_revision": reference["source_revision"],
        "content_digest": digest(raw),
        "media_type": "text/markdown",
        "size_bytes": len(raw),
        "classification": reference["classification"],
        "availability": "available_by_owner_export",
        "export_capability_id": "knowledgeos.artifact_read",
    }


def _source(app, reference):
    path = app.resolve_reference(dict(reference))
    app._mcp_source_policy(path)
    raw = read_regular(resolve_beneath(app.roots.vault, path))
    _require(
        raw is not None and digest(raw) == reference["content_digest"],
        "Research source bytes changed",
    )
    properties = source_properties(path, raw)
    _require(
        properties.get("ai_policy") not in {"local_only", "deny"}
        and properties.get("sensitivity") != "confidential",
        "Native research cannot receive a local-only or private source",
    )
    return path, raw


def _bundle(app, spec, run, source):
    suffix = record_digest({"work_run_id": run["work_run_id"], "spec_digest": spec["spec_digest"]})[
        7:39
    ]
    base = {"graph_id": "graph.research." + suffix, "graph_revision": 1}
    ids = ["node.start", "node.retrieve_evidence", "node.answer_artifact", "node.finish"]
    nodes = [
        {
            **base,
            "node_id": name,
            "node_kind": kind,
            "max_attempts": 1,
            "input_semantic_term_ids": [],
            "output_semantic_term_ids": [],
        }
        for name, kind in zip(ids, ("start", "task", "task", "terminal"))
    ]
    edges = [
        {
            **base,
            "edge_id": "edge." + str(i),
            "source_node_id": a,
            "target_node_id": b,
            "condition": "always",
        }
        for i, (a, b) in enumerate(pairwise(ids))
    ]
    pins = _pins(app)
    graph = {
        **base,
        "owner_operation_id": "knowledgeos",
        "profile": "graph_enabled",
        "engine_id": ENGINE_ID,
        "engine_version": ENGINE_VERSION,
        "start_node_id": ids[0],
        "node_ids": ids,
        "edge_ids": [e["edge_id"] for e in edges],
        "max_steps": 4,
        "input_semantic_term_ids": [],
        "output_semantic_term_ids": [],
        **pins,
    }
    graph["graph_digest"] = graph_content_digest(graph, nodes, edges)
    _, raw = _source(app, source)
    artifact = _artifact(source, raw)
    segment = {
        **{
            k: run[k]
            for k in (
                "owner_operation_id",
                "work_id",
                "work_run_id",
                "work_spec_revision",
                "work_spec_digest",
            )
        },
        **base,
        **pins,
        "graph_run_id": "graphrun.research." + suffix,
        "graph_digest": graph["graph_digest"],
        "segment_index": 1,
        "target_criterion_ids": [CRITERION["criterion_id"]],
        "trigger_kind": "initial",
        "input_references": [artifact],
    }
    segment["segment_digest"] = segment_digest(segment)
    assignments = [
        {
            **base,
            "owner_operation_id": "knowledgeos",
            "node_id": node,
            "assignment_generation": run["generation"],
            "executor_id": executor,
            **profile,
            "admission_receipt_id": spec["admission_receipt_id"],
            "work_run_id": run["work_run_id"],
            "work_spec_revision": spec["work_spec_revision"],
            "work_spec_digest": spec["spec_digest"],
            "selection_authority": "explicit_user",
            "semantic_version": pins["semantic_version"],
            "semantic_digest": pins["semantic_digest"],
        }
        for node, executor, profile in zip(
            ids[1:3], spec["allowed_executor_ids"], spec["allowed_profiles"]
        )
    ]
    return {
        "segment": segment,
        "graph_spec": graph,
        "nodes": nodes,
        "edges": edges,
        "assignments": assignments,
    }, {artifact["artifact_id"]: artifact}

class ResearchTeamExecutor:
    """One immutable two-Profile segment; no automatic retries or recovery."""

    def __init__(self, exops: KnowledgeExOps, *, runner):
        self.exops, self.runner = exops, runner
        self.runner_binding = _runner_binding(runner.binding)
        _require(not hasattr(exops, "research_executor"), "Research owner already has an executor")
        exops.research_executor = self

    def _current(self, work_run_id):
        _require(self.runner.binding == self.runner_binding, "Research runtime binding changed")
        record = _current(self.exops, work_run_id, self.runner_binding)
        self.exops._guard(OwnerActor(record["actor_id"], record["actor_ingress"]), "submit_work")
        return record

    def _save(self, record, key, value):
        _require(len(canonical(value)) <= 256 * 1024, "Research evidence exceeds owner bound")
        app = self.exops.application
        with app.journal.writer() as writer:
            current = writer.value["intents"].get("exops.work." + record["run"]["work_run_id"])
            _require(current == record and key not in current, "Research stage changed or repeated")
            self._current(record["run"]["work_run_id"])
            current[key] = copy.deepcopy(value)
            writer.value["history"].append({
                "event": key, "work_run_id": record["run"]["work_run_id"],
                "sequence": writer.value["revision"] + 1,
            })
            writer.commit(app.clock())

    async def manager(self, spec, run):
        record = self._current(run["work_run_id"])
        _require(record["spec"] == dict(spec) and record["run"] == dict(run)
                 and not run["graph_run_ids"], "Research Manager input changed or repeated")
        session = TeamToolSession(self.exops.application, run["work_run_id"],
                                  spec["manager_executor_id"], exops=self.exops)
        _require(session.method_view()["method_contract_ref"] == spec["method_contract_ref"],
                 "Research Manager method changed")
        bundle, artifacts = _bundle(self.exops.application, spec, run, record["method_source_reference"])
        session.propose_graph({"bundle": bundle, "artifacts": artifacts})

    def _segment(self, record):
        run = record["run"]
        admitted = record.get("admitted_method_segments", {})
        bundle, _ = _bundle(self.exops.application, record["spec"], run,
                            record["method_source_reference"])
        _require(len(admitted) == 1 and run["graph_run_ids"] == list(admitted)
                 and run["segments_started"] == 1 and run["profile_invocations"] == 2,
                 "Research requires one segment and exactly two Profile invocations")
        selected = admitted.get(run["active_graph_run_id"], {})
        _require(selected.get("segment") == bundle["segment"]
                 and selected.get("assignments") == bundle["assignments"]
                 and selected.get("source_reference") == record["method_source_reference"]
                 and selected.get("native_binding_digest") == research_native_digest(self.runner_binding),
                 "Research immutable Graph, source, assignments or runtime changed")
        return bundle["segment"]

    def _request(self, record, stage):
        self._segment(record)
        app = self.exops.application
        source = record["method_source_reference"]
        path, _ = _source(app, source)
        reader = KnowledgeReadTools(app, allowed_path=path)
        projection, notes = reader._eligible(selected_path=path)
        chunks = [chunk.locator for chunk in _chunks_for_note(reader._note(source, notes))]
        _require(0 < len(chunks) <= 20, "Research source exceeds citation bound")
        run, spec = record["run"], record["spec"]
        index = 0 if stage == "retrieve_evidence" else 1
        session = TeamToolSession(app, run["work_run_id"], spec["allowed_executor_ids"][index],
                                  run["active_graph_run_id"], self.exops)
        _require(session.tools() == STAGE_TOOLS[stage], "Research stage grants changed")
        evidence = None
        if index:
            evidence = self._retrieval(record)
        request = {
            "runner_binding": self.runner_binding, "stage_id": stage,
            "work_run_id": run["work_run_id"], "work_spec_digest": spec["spec_digest"],
            "graph_run_id": run["active_graph_run_id"],
            "executor_id": spec["allowed_executor_ids"][index],
            "generation": run["generation"], "fencing_token": run["fencing_token"],
            "source_reference": source, "source_chunks": chunks,
            "control_root": str(app.roots.control), "question": spec["objective"],
            "index_generation_id": projection.manifest["generation_id"], "evidence": evidence,
        }
        _require(len(canonical(request)) <= 65000, "Research evidence exceeds native input bound")
        return {"request_digest": record_digest(request), **request}

    def _response(self, record, request, response):
        _require(request == self._request(record, request["stage_id"]),
                 "Research request no longer matches current owner Work")
        _require(isinstance(response, dict) and set(response) == {
            "request_digest", "runner_binding", "model", "provider", "session_id",
            "final_text", "tool_calls", "exposed_tools",
        } and response["request_digest"] == request["request_digest"]
            and response["runner_binding"] == self.runner_binding
            and all(response[k] == self.runner_binding[k] for k in ("model", "provider"))
            and isinstance(response["session_id"], str) and 0 < len(response["session_id"]) <= 256
            and isinstance(response["final_text"], str) and 0 < len(response["final_text"]) <= 16384
            and response["exposed_tools"] == list(STAGE_TOOLS[request["stage_id"]]),
            "Research response changed route, session, runtime or tools")
        calls = response["tool_calls"]
        _require(isinstance(calls, list) and 1 <= len(calls) <= 64,
                 "Research trace is missing or exceeds bound")
        app = self.exops.application
        session = TeamToolSession(app, request["work_run_id"], request["executor_id"],
                                  request["graph_run_id"], self.exops)
        for call in calls:
            _require(isinstance(call, dict) and {"name", "arguments", "result"} <= set(call)
                     and set(call) <= {"name", "arguments", "result", "raw_callback_name"}
                     and call["name"] in STAGE_TOOLS[request["stage_id"]]
                     and isinstance(call["arguments"], dict) and isinstance(call["result"], dict),
                     "Research trace includes an ungranted tool")
            session.authorize(call["name"], call["arguments"])
            envelope = call["result"]
            _require(envelope.get("schema_version") == 3 and envelope.get("operation") == call["name"]
                     and envelope.get("error") is None
                     and envelope.get("canonical_target_changed") is False,
                     "Research trace has no successful exact Owner envelope")
        if request["stage_id"] == "answer_artifact":
            _require(len(calls) == 1 and calls[0]["name"] == "artifact_submit",
                     "Research synthesis requires exactly one artifact effect")
            call = calls[0]
            args, envelope = call["arguments"], call["result"]
            _require(set(args) <= {"artifact_kind", "content", "idempotency_key"}
                     and args.get("artifact_kind") == "document_enhancement_report"
                     and isinstance(args.get("content"), str)
                     and envelope.get("status") == "created"
                     and envelope.get("execution_outcome") == "completed"
                     and envelope.get("effect") == "proposal_created"
                     and envelope.get("data", {}).get("apply_allowed") is False,
                     "Research synthesis lacks its exact artifact effect")
            report = json.loads(args["content"])
            _validate_research_report(report)
            evidence = request["evidence"]
            _require(report["source_reference"] == request["source_reference"]
                     and report["index_generation_id"] == request["index_generation_id"]
                     and report["citations"] == evidence["citations"],
                     "Research report changed source, generation or actual citations")
            return report, envelope["data"]
        names = [call["name"] for call in calls]
        _require(names[:2] == ["knowledge_search", "knowledge_retrieve"]
                 and names[-1] == "citation_verify"
                 and all(name == "knowledge_read" for name in names[2:-1]),
                 "Research retrieval stage order changed")
        for call in calls:
            _require(call["result"].get("status") in {"ok", "empty"}
                     and call["result"].get("effect") == "read_only",
                     "Research retrieval has an effect or denial")
            if call["name"] in {"knowledge_search", "knowledge_retrieve"}:
                args = call["arguments"]
                _require(args.get("query") == request["question"]
                         and args.get("expected_generation_id") == request["index_generation_id"]
                         and set(args) <= {"query", "expected_generation_id", "mode", "hops"}
                         and (args.get("mode", "lexical") == "lexical")
                         and (args.get("hops", 0) == 0), "Research query escaped admitted scope")
            actual, error = _convert_report(
                SimpleNamespace(services=app), call["name"], call["arguments"],
                _operation(app, call["name"], call["arguments"], session=session)(),
            )
            _require(not error and {k: v for k, v in actual.items() if k != "request_id"}
                     == {k: v for k, v in call["result"].items() if k != "request_id"},
                     "Research tool evidence differs from fresh Owner read")
        position, offset = 0, 0
        citations, pages = [], []
        for call in calls[2:-1]:
            args, data = call["arguments"], call["result"]["data"]
            _require(position < len(request["source_chunks"])
                     and args.get("resource_reference") == request["source_reference"]
                     and args.get("chunk_locator") == request["source_chunks"][position]
                     and args.get("offset", 0) == offset
                     and bool(data["text"]), "Research source pages skipped, repeated or reordered")
            pages.append(data)
            offset += len(data["text"])
            if not data["truncated"]:
                citations.append({k: data[k] for k in ("resource_reference", "chunk_locator", "chunk_hash")})
                position, offset = position + 1, 0
        _require(position == len(request["source_chunks"])
                 and calls[-1]["arguments"] == {"citations": citations}
                 and calls[-1]["result"]["data"]["all_valid"] is True
                 and calls[-1]["result"]["data"]["truth_evaluated"] is False,
                 "Research citations lack complete actual ordered source reads")
        return {
            "source_reference": request["source_reference"],
            "index_generation_id": request["index_generation_id"],
            "citations": citations, "pages": pages,
        }

    def _retrieval(self, record):
        execution = record.get("research_retrieve_evidence")
        _require(isinstance(execution, dict) and set(execution) == {"request", "response", "evidence_digest"}
                 and execution["evidence_digest"] == record_digest({
                     "request": execution["request"], "response": execution["response"],
                 }), "Research retrieval evidence changed or missing")
        return self._response(record, execution["request"], execution["response"])

    async def _execute(self, work_run_id, stage):
        record = self._current(work_run_id)
        request = self._request(record, stage)
        self._save(record, "research_" + stage + "_request", request)
        response = await self.runner(copy.deepcopy(request))
        record = self._current(work_run_id)
        self._response(record, request, response)
        evidence = {"request": request, "response": response}
        self._save(record, "research_" + stage, {**evidence, "evidence_digest": record_digest(evidence)})

    async def retrieve_evidence(self, work_run_id):
        await self._execute(work_run_id, "retrieve_evidence")

    async def answer_artifact(self, work_run_id):
        await self._execute(work_run_id, "answer_artifact")

    async def assess(self, work_run_id):
        record = self._current(work_run_id)
        if "research_assessed_at" not in record:
            self._save(record, "research_assessed_at", stamp(self.exops.application.clock()))
        return self._assess(self._current(work_run_id))

    def _assess(self, record):
        work_run_id = record["run"]["work_run_id"]
        spec, run = record["spec"], record["run"]
        evaluator = spec["eval_officer_executor_id"]
        _require(evaluator not in [spec["manager_executor_id"], *spec["allowed_executor_ids"]],
                 "Research requires independent EvalOfficer")
        segment = self._segment(record)
        self._retrieval(record)
        execution = record.get("research_answer_artifact", {})
        _require(execution.get("evidence_digest") == record_digest({
            "request": execution.get("request"), "response": execution.get("response"),
        }), "Research synthesis evidence changed or missing")
        report, data = self._response(record, execution["request"], execution["response"])
        app = self.exops.application
        reference, artifact = data["artifact_reference"], data["work_artifact_reference"]
        path = app.resolve_reference(reference, proposal=True)
        raw = read_regular(resolve_beneath(app.roots.vault, path))
        _require(raw is not None and digest(raw) == artifact["content_digest"]
                 and len(raw) == artifact["size_bytes"]
                 and record.get("submitted_artifacts") == [reference]
                 and record.get("artifact_references") == {artifact["artifact_id"]: artifact},
                 "Research submitted artifact registration or exact bytes changed")
        snapshot = app.journal.read()
        effect = snapshot["intents"].get(data["effect_id"], {})
        identity = effect.get("identity", {})
        receipt = snapshot["receipts"].get(data["effect_id"], {}).get("execution", {})
        args = execution["response"]["tool_calls"][0]["arguments"]
        payload = {"artifact_kind": args["artifact_kind"], "content": args["content"]}
        expected_identity = {
            "action": "artifact_submit", "source": record["method_source_reference"],
            "payload": payload, "idempotency_key": args.get("idempotency_key") or digest(canonical(payload)),
            "work_run_id": work_run_id, "graph_run_id": run["active_graph_run_id"],
            "executor_id": spec["allowed_executor_ids"][1],
        }
        effects = [item for item in snapshot["intents"].values()
                   if item.get("identity", {}).get("work_run_id") == work_run_id]
        _require(len(effects) == 1 and effect.get("state") == "completed"
                 and effect.get("action") == "artifact_submit"
                 and effect.get("artifact_path") == path
                 and effect.get("source_path") == app.resolve_reference(record["method_source_reference"])
                 and effect.get("source_digest") == record["method_source_reference"]["content_digest"]
                 and effect.get("content_digest") == digest(raw)
                 and base64.b64decode(effect["frozen_artifact"], validate=True) == raw
                 and identity == expected_identity
                 and all(receipt.get(key) == effect.get(key) for key in (
                     "effect_id", "owner_operation_id", "content_digest", "generation", "fencing_token"))
                 and receipt.get("outcome") == "succeeded"
                 and receipt.get("result_digest") == digest(raw),
                 "Research artifact lacks exact effect identity, frozen bytes and successful receipt")
        _require(KnowledgeReadTools(app).citation_verify(report["citations"])["all_valid"],
                 "Research citation is stale")
        _require(app.journal.read()["intents"]["exops.work." + work_run_id] == record,
                 "Research Work changed during independent assessment")
        checkpoint = record_digest({
            "graph_run_id": run["active_graph_run_id"], "artifact_digest": digest(raw),
            "retrieval_digest": record["research_retrieve_evidence"]["evidence_digest"],
            "synthesis_digest": execution["evidence_digest"],
        })
        graph_run_id = run["active_graph_run_id"]
        suffix = record_digest({"graph_run_id": graph_run_id, "checkpoint": checkpoint})[7:39]
        evaluation = {
            "owner_operation_id": "knowledgeos", "run_id": graph_run_id,
            "work_run_id": work_run_id, "work_spec_revision": spec["work_spec_revision"],
            "work_spec_digest": spec["spec_digest"], "checkpoint_digest": checkpoint,
            "criteria_revision": spec["criteria_revision"], "criterion_ids": [CRITERION["criterion_id"]],
            "passed_criterion_ids": [CRITERION["criterion_id"]], "failed_criterion_ids": [],
            "not_run_criterion_ids": [], "evaluation_id": "evaluation.research." + suffix,
            "evaluator_id": evaluator, "evidence_references": [artifact],
        }
        assessment = {
            **{k: run[k] for k in ("owner_operation_id", "work_id", "work_run_id",
                                   "work_spec_revision", "work_spec_digest")},
            **_pins(app), "assessment_id": "assessment.research." + suffix,
            "criteria_revision": spec["criteria_revision"], "assessor_id": evaluator,
            "assessed_at": record["research_assessed_at"], "graph_run_ids": [graph_run_id],
            "completion_state": "complete", "criterion_outcomes": [{
                "criterion_id": CRITERION["criterion_id"], "outcome": "satisfied",
                "evaluation_ids": [evaluation["evaluation_id"]], "evidence_references": [artifact],
            }],
        }
        completed = {
            **copy.deepcopy(run), "revision": run["revision"] + 1,
            "control_revision": run["control_revision"] + 1, "state": "completed",
            "latest_assessment_id": assessment["assessment_id"],
            "latest_assessment_digest": record_digest(assessment), "result_reference": artifact,
        }
        completed.pop("active_graph_run_id")
        return WorkResult(completed, assessment, (segment,), (evaluation,),
                          {artifact["artifact_id"]: artifact}, {graph_run_id: checkpoint}, evaluator)

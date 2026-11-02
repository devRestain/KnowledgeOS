"""Exact cited-note drafting and project-period review, never canonical writers."""
from __future__ import annotations

import base64
import copy
import json
from datetime import date, datetime
from types import SimpleNamespace

from ..action_proposals import DRAFT_SOURCE_TYPES, DRAFT_TARGET_TYPES, generate_action_proposal
from ..adapters.core import canonical, digest
from ..adapters.owner_journal import read_regular
from ..adapters.paths import resolve_beneath
from ..blueprint import load_yaml_file
from ..interfaces.mcp_server import _convert_report, _operation
from ..note_engine import NoteEngine
from ..projection import source_properties
from ..retrieval import _chunks_for_note
from .native_team import (
    NativeTeamExecutor,
    NativeTeamPolicy,
    native_admission,
    native_digest,
    require,
    source_artifact,
    source_bytes,
)
from .read_tools import KnowledgeReadTools
from .work_tools import TeamToolSession

DRAFT_METHOD = "knowledgeos.cited_draft_team"
REVIEW_METHOD = "knowledgeos.project_review_team"
MODULES = ("authoring_work.py", "authoring_temporal.py", "authoring_workflow.py", "native_team_temporal.py")
POLICIES = {
    DRAFT_METHOD: NativeTeamPolicy(
        namespace="cited_draft", method_id=DRAFT_METHOD, team_id="knowledge-curation",
        roles=("knowledgeos.source_extractor", "knowledgeos.draft_writer"),
        criterion={"criterion_id": "criterion.cited_draft", "description": "Complete exact source reads precede one authored cited C27 draft with immutable target pins, separate original excerpt and digest, exact successful Pending receipt and an unresolved human review gate; no canonical apply."},
        stages=("source_evidence", "cited_draft"),
        tools={"source_evidence": ("knowledge_read",), "cited_draft": ("proposal_draft_note",)}, modules=MODULES),
    REVIEW_METHOD: NativeTeamPolicy(
        namespace="project_review", method_id=REVIEW_METHOD, team_id="project-review",
        roles=("knowledgeos.project_reader", "knowledgeos.review_writer"),
        criterion={"criterion_id": "criterion.project_review", "description": "Exact admitted project and period retrieval, Base view and task evidence precede one receipt-bound noncanonical review plan separating observations, interpretation and next actions with exact citation and task locations; apply_allowed remains false."},
        stages=("project_evidence", "review_proposal"),
        tools={"project_evidence": ("knowledge_retrieve", "base_view_query", "task_query"), "review_proposal": ("proposal_project_review",)}, modules=MODULES),
}


def _policy(method_id):
    require(method_id in POLICIES, "Unknown concrete authoring method")
    return POLICIES[method_id]


def _scope(app, source, method_id, scope):
    require(isinstance(scope, dict), "Authoring requires explicit immutable scope")
    path, raw = source_bytes(app, source)
    kind = source_properties(path, raw, engine=NoteEngine.from_root(app.roots))["type"]
    if method_id == DRAFT_METHOD:
        require(set(scope) == {"target_path", "target_type", "title"}
                and kind in DRAFT_SOURCE_TYPES and scope["target_type"] in DRAFT_TARGET_TYPES,
                "Draft requires declared source type and exact target pins")
        target = scope["target_path"]
        require(isinstance(target, str) and target.endswith(".md") and target != path
                and not target.startswith("/") and "\\" not in target
                and not any(p in {"", ".", ".."} for p in target.split("/"))
                and not resolve_beneath(app.roots.vault, target).exists(), "Draft target must remain create-only")
        require(isinstance(scope["title"], str) and 0 < len(scope["title"]) <= 200
                and scope["title"] == scope["title"].strip(),
                "Draft title pin is invalid")
    else:
        require(kind == "project" and set(scope) == {"period_start", "period_end", "query", "base", "view"},
                "Project review requires one exact project and period/query/Base pins")
        for key in scope:
            require(isinstance(scope[key], str) and 0 < len(scope[key]) <= 160, "Invalid project scope pin")
        views = load_yaml_file(app.roots.control / "blueprint/blueprint.yaml")["bases"]["views"]
        view = views.get(scope["base"], {}).get(scope["view"], {})
        require(view.get("filters", {}).get("type") == "project",
                "Review Base/view must be a declared canonical project view")
        try:
            start, end = date.fromisoformat(scope["period_start"]), date.fromisoformat(scope["period_end"])
        except ValueError:
            require(False, "Review period must use exact ISO dates")
        require(start <= end and (end - start).days <= 366, "Review period is invalid or unbounded")
    return copy.deepcopy(scope)


def authoring_native_digest(runner_binding, method_id=DRAFT_METHOD):
    return native_digest(_policy(method_id), runner_binding)


def authoring_admission(application, binding, source_reference, *, method_id, scope=None, objective=None, **identities):
    if scope is None:
        try:
            scope = json.loads(objective)
        except (ValueError, TypeError):
            require(False, "Authoring scope is missing")
    selected = _scope(application, source_reference, method_id, scope)
    encoded = canonical(selected).decode()
    require(objective is None or objective == encoded, "Authoring objective changed immutable user pins")
    return native_admission(application, binding, source_reference, policy=_policy(method_id), objective=encoded, **identities)


def cited_draft_admission(application, binding, source_reference, **kwargs):
    return authoring_admission(application, binding, source_reference, method_id=DRAFT_METHOD, **kwargs)


def project_review_admission(application, binding, source_reference, **kwargs):
    return authoring_admission(application, binding, source_reference, method_id=REVIEW_METHOD, **kwargs)


class AuthoringExecutor(NativeTeamExecutor):
    def __init__(self, exops, *, runner, method_id):
        super().__init__(exops, runner=runner, policy=_policy(method_id))

    def admission(self, binding, source, **identities):
        return authoring_admission(self.exops.application, binding, source, method_id=self.policy.method_id,
                                  runner_binding=self.runner_binding, **identities)

    def request_context(self, record, stage):
        app = self.exops.application
        source = record["method_source_reference"]
        scope = _scope(app, source, self.policy.method_id, json.loads(record["spec"]["objective"]))
        path, _ = source_bytes(app, source)
        reader = KnowledgeReadTools(app, allowed_path=path)
        projection, notes = reader._eligible(selected_path=path)
        chunks = [c.locator for c in _chunks_for_note(reader._note(source, notes))]
        require(0 < len(chunks) <= 20, "Authoring source exceeds bounded citations")
        return {"scope": scope, "source_path": path, "source_chunks": chunks,
                "objective": record["spec"]["objective"], "index_generation_id": projection.manifest["generation_id"],
                "evidence": None if stage == self.policy.stages[0] else self.evidence(record, self.policy.stages[0])}

    def _fresh_call(self, record, call):
        app = self.exops.application
        session = TeamToolSession(app, record["run"]["work_run_id"], record["spec"]["allowed_executor_ids"][0],
                                  record["run"]["active_graph_run_id"], self.exops)
        actual, error = _convert_report(SimpleNamespace(services=app), call["name"], call["arguments"],
            _operation(app, call["name"], call["arguments"], session=session)())
        require(not error and {k: v for k, v in actual.items() if k != "request_id"}
                == {k: v for k, v in call["result"].items() if k != "request_id"},
                "Authoring tool evidence differs from fresh independent Owner read")

    def validate_stage(self, record, request, response):
        calls, stage = response["tool_calls"], request["stage_id"]
        if stage == "source_evidence":
            position, offset, pages, citations = 0, 0, [], []
            for call in calls:
                args, data = call["arguments"], call["result"].get("data", {})
                require(call["name"] == "knowledge_read" and position < len(request["source_chunks"])
                        and args.get("resource_reference") == request["source_reference"]
                        and args.get("chunk_locator") == request["source_chunks"][position]
                        and args.get("offset", 0) == offset and data.get("text"), "Source pages skipped, repeated or escaped")
                self._fresh_call(record, call)
                pages.append(data)
                offset += len(data["text"])
                if not data["truncated"]:
                    citations.append({k: data[k] for k in ("resource_reference", "chunk_locator", "chunk_hash")})
                    position, offset = position + 1, 0
            require(position == len(request["source_chunks"]), "Source extraction omitted a page")
            return {"source_reference": request["source_reference"], "citations": citations, "pages": pages}
        if stage == "project_evidence":
            scope = request["scope"]
            expected = [
                {"query": scope["query"], "hops": 0, "expected_generation_id": request["index_generation_id"], "limit": 20},
                {"base": scope["base"], "view": scope["view"], "filters": {}, "limit": 20},
                {"path_prefix": request["source_path"], "limit": 20},
            ]
            require([c["name"] for c in calls] == list(self.policy.tools[stage])
                    and [c["arguments"] for c in calls] == expected, "Project reader changed immutable scope or tool sequence")
            for call in calls:
                self._fresh_call(record, call)
            tasks = calls[2]["result"]["data"]
            require(tasks.get("truncated") is False, "Project task evidence exceeds bound")
            task_locations = tasks["tasks"]
            require(all(t["resource_reference"] == request["source_reference"] for t in task_locations), "Foreign project task escaped scope")
            retrieval = calls[0]["result"]["data"]
            require(retrieval.get("truncated") is False
                    and all(c["resource_reference"] == request["source_reference"] for c in retrieval["candidates"]),
                    "Project retrieval escaped or truncated admitted evidence")
            citations = [{"resource_reference": t["resource_reference"], "locator": t["locator"], "line_sha256": t["line_sha256"]}
                         for t in task_locations]
            citations = [{k: c[k] for k in ("resource_reference", "chunk_locator", "chunk_hash")}
                         for c in retrieval["candidates"]] + citations
            return {"source_reference": request["source_reference"], "source_digest": request["source_reference"]["content_digest"],
                    "period": {k: scope[k] for k in ("period_start", "period_end")}, "retrieval": retrieval,
                    "base_view": calls[1]["result"]["data"], "task_locations": task_locations, "citations": citations}
        tool = self.policy.tools[stage][0]
        require(len(calls) == 1 and calls[0]["name"] == tool, "Authoring writer must create exactly one proposal")
        call = calls[0]
        args, envelope = call["arguments"], call["result"]
        require(args.get("source_reference") == request["source_reference"]
                and envelope.get("status") == "created" and envelope.get("effect") == "proposal_created"
                and envelope.get("execution_outcome") == "completed", "Writer has no actual exact-source Pending effect")
        if stage == "cited_draft":
            require(set(args) == {"source_reference", "target_path", "target_type", "title", "draft_body"}
                    and all(args[k] == request["scope"][k] for k in request["scope"]), "Draft writer changed explicit user target pins")
            body = args["draft_body"]
            require(isinstance(body, str) and 0 < len(body.encode()) <= 16384
                    and request["source_reference"]["content_digest"] in body
                    and "[[" + request["source_path"] + "]]" in body
                    and all(c["chunk_locator"] in body and c["chunk_hash"] in body for c in request["evidence"]["citations"]),
                    "Authored draft lacks exact source and chunk citations")
            require(body.strip() != "".join(p["text"] for p in request["evidence"]["pages"]).strip(), "Draft is only an original excerpt")
            require(envelope["data"].get("apply_allowed") is True, "C27 draft lost separate review/apply contract")
            return {"reference": envelope["data"]["proposal_reference"], "arguments": args}
        require(set(args) <= {"source_reference", "content", "idempotency_key"}
                and envelope["data"].get("apply_allowed") is False, "Review plan must never allow generic apply")
        plan = json.loads(args["content"])
        require(set(plan) == {"observations", "interpretation", "next_actions", "citations"}
                and plan["citations"] == request["evidence"]["citations"], "Review changed exact task citations")
        for field in ("observations", "next_actions"):
            require(isinstance(plan[field], list) and 1 <= len(plan[field]) <= 40
                    and all(isinstance(v, str) and 0 < len(v) <= 2048 for v in plan[field]), "Review requires bounded separate observations/actions")
        require(isinstance(plan["interpretation"], str) and 0 < len(plan["interpretation"]) <= 8192, "Review interpretation must be explicit")
        observations = "\n".join(plan["observations"])
        require(all(request["scope"][k] in observations for k in ("period_start", "period_end"))
                and all(t["locator"] in observations and t["line_sha256"] in observations for t in request["evidence"]["task_locations"]),
                "Review omitted exact period or task locations/hashes")
        artifact = envelope["data"].get("work_artifact_reference")
        require(isinstance(artifact, dict), "Review lacks its Owner-registered formal artifact reference")
        self.exops.application.core.validate("ArtifactReference", artifact)
        return {"reference": envelope["data"]["artifact_reference"], "artifact": artifact,
                "arguments": args, "plan": plan}

    def result_call(self, execution):
        calls = execution["response"]["tool_calls"]
        require(len(calls) == 1 and calls[0]["name"] == self.policy.tools[self.policy.stages[-1]][0], "Missing exact proposal call")
        return calls[0]

    def validate_result(self, record, candidate):
        app = self.exops.application
        source_bytes(app, record["method_source_reference"])
        evidence = self.evidence(record, self.policy.stages[0])
        citations = [item for item in evidence["citations"] if "chunk_locator" in item]
        require(KnowledgeReadTools(app).citation_verify(citations)["all_valid"],
                "Authoring source evidence is stale")

    def result_artifact(self, record, execution):
        candidate = self.evidence(record, self.policy.stages[-1])
        app, run, spec = self.exops.application, record["run"], record["spec"]
        reference = candidate["reference"]
        path = app.resolve_reference(reference, proposal=True)
        raw = read_regular(resolve_beneath(app.roots.vault, path))
        require(raw is not None and digest(raw) == reference["content_digest"], "Pending exact bytes changed")
        artifact = (source_artifact(reference, raw) if self.policy.method_id == DRAFT_METHOD
                    else candidate["artifact"])
        app.core.validate("ArtifactReference", artifact)
        require(artifact["content_digest"] == digest(raw) and artifact["size_bytes"] == len(raw),
                "Formal proposal artifact reference differs from exact Pending bytes")
        snapshot = app.journal.read()
        bound = {"work_run_id": run["work_run_id"], "work_spec_digest": spec["spec_digest"],
                 "graph_run_id": run["active_graph_run_id"], "executor_id": spec["allowed_executor_ids"][-1],
                 "source_reference": record["method_source_reference"]}
        effects = [e for e in snapshot["intents"].values() if e.get("work_binding", {}).get("work_run_id") == run["work_run_id"]
                   or e.get("identity", {}).get("work_run_id") == run["work_run_id"]]
        require(len(effects) == 1, "Authoring requires exactly one Work-bound Pending effect")
        effect = effects[0]
        receipt = snapshot["receipts"].get(effect["effect_id"], {}).get("execution", {})
        require(effect.get("state") == "completed" and effect.get("artifact_path") == path
                and effect.get("source_path") == app.resolve_reference(record["method_source_reference"])
                and effect.get("source_digest") == record["method_source_reference"]["content_digest"]
                and effect.get("content_digest") == digest(raw)
                and base64.b64decode(effect["frozen_artifact"], validate=True) == raw
                and all(receipt.get(k) == effect[k] for k in ("effect_id", "owner_operation_id", "content_digest", "generation", "fencing_token"))
                and receipt.get("outcome") == "succeeded" and receipt.get("result_digest") == digest(raw), "Proposal lacks immutable frozen bytes and successful effect receipt")
        args = candidate["arguments"]
        if self.policy.method_id == DRAFT_METHOD:
            require(effect.get("work_binding") == bound, "Draft effect belongs to another Profile or Graph")
            prepared, code = generate_action_proposal(app.roots, action="draft_note", source_path=effect["source_path"],
                expected_sha256=effect["source_digest"][7:], prepare_only=True,
                **{k: args[k] for k in ("target_path", "target_type", "title", "draft_body")})
            require(code == 0 and prepared.get("artifact_bytes") == raw
                    and prepared.get("proposal_path") == path, "Fresh C27 preparation differs from authored draft bytes")
            gate = snapshot["human_requests"].get(effect.get("human_request_id"), {})
            require(gate.get("lifecycle") == "pending" and gate.get("allowed_actions") == ["approve", "reject", "inspect"]
                    and effect["human_request_id"] not in snapshot["decisions"]
                    and gate.get("target_digest") == app._target_digest(effect["source_path"], path)
                    and datetime.fromisoformat(gate["expires_at"]) > app.clock(), "Draft lost separate pending human review gate")
        else:
            payload = candidate["plan"]
            identity = {"action": "proposal_project_review", "source": record["method_source_reference"], "payload": payload,
                        "idempotency_key": args.get("idempotency_key") or digest(canonical(payload)),
                        "work_run_id": run["work_run_id"], "graph_run_id": run["active_graph_run_id"],
                        "executor_id": spec["allowed_executor_ids"][-1]}
            require(effect.get("identity") == identity and effect.get("action") == "proposal_project_review", "Review effect identity changed")
        require(record.get("submitted_artifacts") == [reference]
                and record.get("artifact_references") == {artifact["artifact_id"]: artifact}, "Authoring artifact registration changed")
        return artifact, raw

    async def execute_stage(self, work_run_id, stage):
        await super().execute_stage(work_run_id, stage)
        if stage != self.policy.stages[-1]:
            return
        record = self.current(work_run_id)
        if self.policy.method_id == REVIEW_METHOD:
            self.result_artifact(record, record[self.policy.namespace + "_" + stage])
            return
        candidate = self.evidence(record, stage)
        reference = candidate["reference"]
        app = self.exops.application
        path = app.resolve_reference(reference, proposal=True)
        raw = read_regular(resolve_beneath(app.roots.vault, path))
        require(raw is not None and digest(raw) == reference["content_digest"], "Proposal changed before registration")
        artifact = source_artifact(reference, raw)
        self.save(record, "submitted_artifacts", [reference])
        self.save(self.current(work_run_id), "artifact_references", {artifact["artifact_id"]: artifact})
        if self.policy.method_id == DRAFT_METHOD:
            self.save(self.current(work_run_id), "canonical_proposal_reference", reference)

    async def source_evidence(self, work_run_id):
        await self.execute_stage(work_run_id, "source_evidence")

    async def cited_draft(self, work_run_id):
        await self.execute_stage(work_run_id, "cited_draft")

    async def project_evidence(self, work_run_id):
        await self.execute_stage(work_run_id, "project_evidence")

    async def review_proposal(self, work_run_id):
        await self.execute_stage(work_run_id, "review_proposal")

"""Selected, effect-safe Curation steering at a real partial Profile boundary.

This route is deliberately deterministic: exact Capture reads and an atomic
owner-journal normalization report, not a model answer or a canonical Vault edit.
The initial two-task Graph is interrupted after triage, before report dispatch.
Only that boundary supports CUTOVER; arbitrary running reassignment is denied.
"""
from __future__ import annotations

import base64
import copy
import json
import re
from datetime import datetime
from itertools import pairwise
from pathlib import Path

from ..adapters.core import canonical, digest
from ..adapters.owner_journal import read_regular, stamp
from .curation_control import CurationRunControl
from .curation_steering_contract import METHOD_ID
from .curation_work import _artifact, _bundle, _pins, _require, _source
from .exops import OwnerActor
from .team_catalog import TeamCatalog
from .work import (
    directive_digest,
    graph_content_digest,
    record_digest,
    segment_digest,
    work_control_binding,
    work_spec_digest,
)
from .work_binding import _target_digest
from .work_methods import WorkMethodCatalog

PARAMETER = "report.order"
ORDERS = ("source_order", "alphabetical")
OBJECTIVE = "Produce a deterministic source-bound Capture normalization report"
AMENDED_OBJECTIVE = "Produce a labeled deterministic source-bound Capture normalization report"
DESCRIPTION = "Report exactly the nonempty stripped source lines, selected ordering, source digest and owner answer; no canonical write."
AMENDED_DESCRIPTION = DESCRIPTION + " Also includes the owner's nonempty label and an exact nonempty-source-line count."


def curation_steering_native_digest():
    modules = {}
    for name in ("curation_steering.py", "curation_steering_contract.py", "curation_steering_temporal.py", "curation_steering_workflow.py", "curation_work.py", "curation_control.py", "work.py"):
        raw = read_regular(Path(__file__).with_name(name))
        _require(raw is not None, "Steering execution module is unavailable")
        modules[name] = digest(raw)
    return record_digest({"modules": modules, "temporalio": "1.34.0", "temporalio-langgraph": "0.1.0", "langgraph": "1.2.0"})


def steering_admission(application, binding, source, *, work_id, work_run_id, admission_receipt_id):
    TeamCatalog.load(application.roots.control).validate_binding(binding)
    _require(binding.team_id == "knowledge-curation"
             and [item.role_id for item in binding.specialists] == ["knowledgeos.triage", "knowledgeos.draft_writer"]
             and binding.native_binding_digest == curation_steering_native_digest(),
             "Steering requires the exact deterministic native Team binding")
    path, _raw = _source(application, source)
    _require(path.startswith("00_Inbox/Captures/"), "Steering requires a selected Capture")
    pins = _pins(application)
    spec = {
        "owner_operation_id": "knowledgeos", "work_id": work_id, "work_spec_revision": 1, "criteria_revision": 1,
        "objective": OBJECTIVE, "constraints": "Read exact source " + record_digest(source) + "; report only; order in source_order,alphabetical; no canonical or external writes",
        "acceptance_criteria": [{"criterion_id": "criterion.proposal", "description": DESCRIPTION}],
        "target_kind": "team", "team_id": binding.team_id, "manager_executor_id": binding.manager_executor_id,
        "eval_officer_executor_id": binding.eval_officer_executor_id,
        "allowed_executor_ids": [item.executor_id for item in binding.specialists],
        "allowed_profiles": [dict(item.profile) for item in binding.specialists],
        "delegation_mode": "manager_bounded", "explicit_assignments": [],
        "continuation_policy": {"max_segments": 2, "max_repeated_gap_assessments": 2, "max_profile_invocations": 3},
        "execution_profile": "sequential_graph", "admission_receipt_id": admission_receipt_id,
        "steerable_parameter_ids": [PARAMETER],
        "method_contract_ref": WorkMethodCatalog.load(application.roots.control).reference(METHOD_ID), **pins,
    }
    spec["spec_digest"] = work_spec_digest(spec)
    run = {
        "owner_operation_id": "knowledgeos", "work_id": work_id, "work_run_id": work_run_id,
        "work_spec_revision": 1, "work_spec_digest": spec["spec_digest"], "revision": 1, "control_revision": 1,
        "generation": 1, "fencing_token": "steering." + record_digest({"id": work_run_id})[7:],
        "state": "admitted", "graph_run_ids": [], "segments_started": 0, "profile_invocations": 0,
        "repeated_gap_count": 0, "pending_action_ids": [], "unresolved_effect_ids": [],
        "binding_reference": dict(binding.binding_reference), **pins,
    }
    from .work import WorkContracts
    WorkContracts(application.core).run(run, spec)
    return spec, run


def _key(work_run_id):
    return "exops.work." + work_run_id


def _command(command_id):
    return "curation.steering.command." + command_id


def _receipt_key(identity):
    return "exops.control.steering." + record_digest({"identity": identity})[7:39]


def _context(run):
    return {key: run[key] for key in ("owner_operation_id", "work_id", "work_run_id", "work_spec_revision", "work_spec_digest")}


class SafeCurationSteering:
    def __init__(self, exops, *, director_amendment_admitted):
        _require(callable(director_amendment_admitted), "Major amendments require an explicit trusted Director callback")
        self.exops = exops
        self.director_amendment_admitted = director_amendment_admitted
        exops.curation_steering = self

    @property
    def app(self):
        return self.exops.application

    def _record(self, snapshot, work_run_id):
        record = snapshot["intents"].get(_key(work_run_id))
        _require(isinstance(record, dict) and record["spec"].get("method_contract_ref", {}).get("method_id") == METHOD_ID,
                 "Work did not select native steering")
        return record

    def _authority(self, record, actor=None, action="control_work"):
        self.exops._current_binding()
        _require(action == "read_work" or not self.exops.read_only, "Read-only Owner cannot execute steering")
        spec, run = record["spec"], record["run"]
        self.exops._guard(actor or OwnerActor(record["actor_id"], record["actor_ingress"]), action)
        if actor is not None:
            _require(actor.actor_id == record["actor_id"], "Steering belongs to the admitted human actor")
        self.exops._team_binding(spec, run)
        _require(self.exops.teams[spec["team_id"]].native_binding_digest == curation_steering_native_digest(),
                 "Native steering implementation changed")
        original = record.get("steering_spec_history", [spec])[0]
        expected, _initial = steering_admission(
            self.app, self.exops.teams[spec["team_id"]], record["method_source_reference"],
            work_id=spec["work_id"], work_run_id=run["work_run_id"],
            admission_receipt_id=original["admission_receipt_id"])
        _require(original == expected, "Steering Work differs from its supported initial envelope")
        if spec != original:
            self._amendment({**record, "spec": original, "run": record["steering_amendment_run"]}, spec)
        WorkMethodCatalog.load(self.app.roots.control).admitted_method(spec, self.exops.contracts)
        _require(self.exops.director_admitted(spec) and self.exops.director_method_source_admitted is not None
                 and self.exops.director_method_source_admitted(spec, record["method_source_reference"]),
                 "Director/source authority was revoked")
        if run["state"] == "completed":
            self._completed(record)
        else:
            self.exops.contracts.run(run, spec)
            _source(self.app, record["method_source_reference"])

    def _completed(self, record):
        """Reconstruct sealed evaluation inputs, never grant a fabricated live Run."""
        retained = record.get("steering_completion")
        _require(isinstance(retained, dict) and set(retained) == {
            "source_reference", "triage", "context", "bundle", "admission_run",
            "partial", "report", "effect_intent", "effect_receipt", "result", "work_receipt",
        }, "Completed steering has no complete immutable assessment context")
        spec, run = record["spec"], record["run"]
        result = retained["result"]
        _require(isinstance(result, dict) and {
            "run", "spec", "assessment", "evaluations", "checkpoints", "artifacts", "segment",
        } == set(result) and all(key in record for key in (
            "assessment", "evaluations", "checkpoints", "assessment_artifacts",
            "steering_successor_admission_run", "steering_report", "steering_report_intent",
            "steering_partial", "steering_bundles", "steering_triage", "steering_successor_context",
        )), "Completed steering lost a required independent result record")
        original = result["run"]
        _require(result["spec"] == spec and original["state"] == "running"
                 and not original["pending_action_ids"] and not original["unresolved_effect_ids"],
                 "Retained evaluation did not use the exact effect-safe admitted revision")
        graph_id = original.get("active_graph_run_id")
        _require(graph_id == run["graph_run_ids"][-1]
                 and record["method_source_reference"] == retained["source_reference"]
                 and record["steering_triage"] == retained["triage"]
                 and record["steering_successor_context"] == retained["context"]
                 and record["steering_partial"] == retained["partial"]
                 and record["steering_report"] == retained["report"]
                 and record["steering_report_intent"] == retained["effect_intent"]
                 and record["steering_successor_admission_run"] == retained["admission_run"]
                 and record["steering_bundles"][graph_id] == retained["bundle"],
                 "Completed steering changed its immutable source, context, lineage or effect")
        _path, source_raw = _source(self.app, retained["source_reference"])
        report = retained["report"]
        raw = base64.b64decode(report["bytes"], validate=True)
        _require(raw == self._normalized(record) and report["reference"] == self._report_reference(raw)
                 and retained["triage"]["source_digest"] == digest(source_raw)
                 and report["token"] == {
                     "work_run_id": original["work_run_id"], "generation": original["generation"],
                     "fencing_token": original["fencing_token"], "graph_run_id": graph_id},
                 "Completed report bytes differ from the exact source or admitted Profile generation")
        partial = retained["partial"]
        self.exops.contracts.assessment(partial["assessment"], partial["spec"], partial["run"],
            segments=[partial["segment"]], evaluations=partial["evaluations"], artifacts=partial["artifacts"],
            checkpoints=partial["checkpoints"], candidate_assessor_id=partial["spec"]["eval_officer_executor_id"])
        _require(partial["segment"] == record["steering_bundles"][run["graph_run_ids"][0]]["segment"]
                 and partial["artifacts"] == {
                     retained["source_reference"]["logical_id"]: _artifact(retained["source_reference"], source_raw)},
                 "Completed lineage lost its independently evaluated partial source evidence")
        admission = {**record, "run": copy.deepcopy(retained["admission_run"])}
        bundle, _artifacts = self._make_bundle(admission, successor=True)
        _require(bundle == retained["bundle"], "Completed Graph differs from its bounded Manager admission")
        self.exops.contracts.run(original, spec, previous=retained["admission_run"])
        evaluated = {**record, "run": copy.deepcopy(original)}
        fresh = self._evaluation(evaluated, complete=True, reference=report["reference"])
        fresh["assessment"]["assessed_at"] = result["assessment"]["assessed_at"]
        _require(fresh == result and record["assessment"] == result["assessment"]
                 and record["evaluations"] == result["evaluations"]
                 and record["checkpoints"] == result["checkpoints"]
                 and record["assessment_artifacts"] == result["artifacts"],
                 "Completed assessment or checkpoint differs from independently reconstructed evidence")
        self.exops.contracts.run(run, spec, previous=original, assessment=result["assessment"],
            segments=[result["segment"]], evaluations=result["evaluations"], artifacts=result["artifacts"],
            checkpoints=result["checkpoints"], candidate_assessor_id=spec["eval_officer_executor_id"])
        snapshot = self.app.journal.read()
        effect = snapshot["receipts"].get(_receipt_key(retained["effect_intent"]["effect_id"]), {})
        _require(effect.get("unresolved") is False and effect.get("receipt") == retained["effect_receipt"]
                 and retained["effect_intent"]["effect_id"] == "effect.steering.report." + record_digest(report["token"])[7:39]
                 and retained["effect_intent"]["content_digest"] == digest(raw)
                 and retained["effect_receipt"]["outcome"] == "succeeded"
                 and retained["effect_receipt"]["result_digest"] == digest(raw)
                 and datetime.fromisoformat(retained["effect_intent"]["issued_at"])
                     <= datetime.fromisoformat(retained["effect_receipt"]["observed_at"])
                     < datetime.fromisoformat(retained["effect_intent"]["expires_at"]),
                 "Completed report lost its exact successful durable effect receipt")
        self.exops.contracts.effect(retained["effect_intent"], original, receipt=retained["effect_receipt"])
        work_receipt = snapshot["receipts"].get("exops.work." + record["action_id"], {})
        self.exops.contracts.record("ExecutionReceipt", retained["work_receipt"])
        _require(work_receipt.get("unresolved") is False and work_receipt.get("receipt") == retained["work_receipt"]
                 and retained["work_receipt"]["outcome"] == "succeeded"
                 and retained["work_receipt"]["result_digest"] == record_digest(run)
                 and all(retained["work_receipt"][key] == record["execution_intent"][key] for key in (
                     "owner_operation_id", "effect_id", "content_digest", "generation", "fencing_token", "work_run_id")),
                 "Completed Work receipt does not certify this exact canonical result")

    def _quiescent(self, snapshot, record):
        run = record["run"]
        _require(not run["unresolved_effect_ids"], "Unresolved effects are not quiescent")
        _require(not any(item.get("work_binding", {}).get("work_run_id") == run["work_run_id"]
                         for item in snapshot["intents"].values()),
                 "This route cannot reconcile external or proposal effects")

    def _advance(self, record, *, spec=None, **changes):
        previous = copy.deepcopy(record["run"])
        run = {**previous, "revision": previous["revision"] + 1,
               "control_revision": previous["control_revision"] + 1, **changes}
        if run.get("active_graph_run_id") is None:
            run.pop("active_graph_run_id", None)
        self.exops.contracts.run(run, spec or record["spec"], previous=previous)
        record["run"] = run
        return run

    def target(self, record):
        run = record["run"]
        result = {**_context(run), **{key: run[key] for key in ("generation", "fencing_token")},
                  "target_control_revision": run["control_revision"], "temporal_run_id": record.get("temporal_run_id"),
                  "graph_run_id": run.get("active_graph_run_id")}
        result["checkpoint_digest"] = record_digest({"target": result, "boundary": record.get("steering_boundary"),
                                                     "source": record["method_source_reference"]})
        return result

    def view(self, work_run_id, *, actor):
        snapshot = self.app.journal.read()
        record = self._record(snapshot, work_run_id)
        self._authority(record, actor, "read_work")
        boundary = record.get("steering_boundary")
        supported = {"question": ["answer"], "partial": ["parameter", "cutover"], "closed": ["amend_work", "resume"]}.get(boundary, [])
        if boundary == "closed" and record["spec"]["work_spec_revision"] == 2:
            supported = ["resume"]
        if record["run"]["pending_action_ids"] or not record.get("steering_native_boundary") or self.exops.read_only:
            supported = []
        return {"target": self.target(record), "question": copy.deepcopy(record.get("steering_question")),
                "supported_kinds": supported, "parameter_bounds": {PARAMETER: list(ORDERS)},
                "commands": [copy.deepcopy(item["receipt"]) for name, item in snapshot["intents"].items()
                             if name.startswith("curation.steering.command.") and item["target"]["work_run_id"] == work_run_id],
                "run": copy.deepcopy(record["run"]), "successor_context": copy.deepcopy(record.get("steering_context", {}))}

    def register_directive(self, work_run_id, *, actor, content):
        _require(isinstance(content, dict) and len(canonical(content)) <= 8192, "Directive must be bounded JSON")
        raw = canonical(content)
        reference = {"owner_operation_id": "knowledgeos", "artifact_id": "artifact.steering." + digest(raw)[7:39],
                     "source_revision": digest(raw)[7:], "content_digest": digest(raw), "size_bytes": len(raw),
                     "media_type": "application/json", "classification": "internal", "availability": "available_by_owner_export",
                     "export_capability_id": "knowledgeos.steering.artifact"}
        self.exops.contracts.record("ArtifactReference", reference)
        with self.app.journal.writer() as writer:
            record = self._record(writer.value, work_run_id)
            self._authority(record, actor)
            artifacts = record.setdefault("steering_artifacts", {})
            value = {"reference": reference, "bytes": base64.b64encode(raw).decode()}
            _require(artifacts.get(reference["artifact_id"], value) == value, "Directive artifact identity conflicts")
            artifacts[reference["artifact_id"]] = value
            writer.commit(self.app.clock())
        return copy.deepcopy(reference)

    def _artifact(self, record, reference):
        self.exops.contracts.record("ArtifactReference", reference)
        stored = record.get("steering_artifacts", {}).get(reference["artifact_id"])
        _require(isinstance(stored, dict) and stored["reference"] == reference, "Exact directive artifact is unavailable")
        raw = base64.b64decode(stored["bytes"], validate=True)
        _require(digest(raw) == reference["content_digest"] and len(raw) == reference["size_bytes"], "Directive bytes changed")
        return json.loads(raw)

    def _amendment(self, record, successor):
        old = record["spec"]
        self.exops.contracts.spec(successor)
        allowed = {"objective", "acceptance_criteria", "criteria_revision", "work_spec_revision", "spec_digest", "admission_receipt_id"}
        _require(all(successor.get(key) == value for key, value in old.items() if key not in allowed),
                 "Amendment changed unsupported source, binding, delegation, pins or budgets")
        _require(old["work_spec_revision"] == 1 and successor["work_spec_revision"] == 2
                 and successor["criteria_revision"] == old["criteria_revision"] + 1
                 and successor["admission_receipt_id"] != old["admission_receipt_id"]
                 and successor["objective"] == AMENDED_OBJECTIVE
                 and successor["acceptance_criteria"] == [{"criterion_id": "criterion.proposal", "description": AMENDED_DESCRIPTION}],
                 "Only the labeled-report major revision is implemented by this evaluator")
        _require(self.director_amendment_admitted(copy.deepcopy(old), copy.deepcopy(successor), copy.deepcopy(record["run"])) is True
                 and self.exops.director_admitted(successor)
                 and self.exops.director_method_source_admitted(successor, record["method_source_reference"]),
                 "Major revision lacks a separate current trusted Director admission")

    def _build(self, record, actor, command_id, kind, target, expires_at, now, reference, parameter_id):
        # Reuse the existing Core command construction, not a second approval protocol.
        values = CurationRunControl._build(self, record, actor, command_id, kind, target, expires_at, now)
        directive = values["directive"]
        for key in ("human_request_id", "human_response_id", "decision_receipt_id", "effect_id"):
            directive[key] = directive[key].replace(".curation.control.", ".curation.steering.")
        values["request"]["request_id"] = directive["human_request_id"]
        values["response"].update(request_id=directive["human_request_id"], response_id=directive["human_response_id"])
        values["decision"].update(request_id=directive["human_request_id"], receipt_id=directive["decision_receipt_id"])
        values["execution_intent"]["effect_id"] = directive["effect_id"]
        directive["directive_reference"] = copy.deepcopy(reference)
        if kind == "parameter":
            directive["parameter_id"] = parameter_id
        if kind == "cutover":
            directive["target_graph_run_id"] = record["run"]["active_graph_run_id"]
        values["target_binding"]["mapping_id"] = "knowledgeos.curation.steering"
        values["target_binding"]["work_binding"] = work_control_binding(directive)
        target_digest = _target_digest(values["target_binding"], directive["semantic_version"], directive["semantic_digest"])
        directive["decision_target_digest"] = target_digest
        directive["directive_digest"] = directive_digest(directive)
        action = "steer" if kind == "parameter" else kind
        values["request"].update(question="Apply " + kind + " at the exact quiescent Curation Profile boundary?",
                                 impact="Only declared future context may change; immutable executed evidence is retained.",
                                 allowed_actions=[action], target_digest=target_digest)
        values["response"].update(chosen_action=action, target_digest=target_digest)
        values["decision"]["chosen_action"] = action
        values["execution_intent"]["content_digest"] = directive["directive_digest"]
        if record["run"].get("active_graph_run_id"):
            values["execution_intent"]["graph_run_id"] = record["run"]["active_graph_run_id"]
        return values

    def request(self, work_run_id, *, actor, command_id, kind, target, expires_at, directive_reference,
                parameter_id=None, successor_spec=None):
        _require(isinstance(command_id, str) and re.fullmatch(r"[a-z][a-z0-9_.-]{0,95}", command_id), "Invalid command identity")
        _require(kind in {"answer", "parameter", "cutover", "amend_work", "resume"}, "Unsupported running reassignment or control")
        expiry = datetime.fromisoformat(expires_at) if isinstance(expires_at, str) else expires_at
        _require(isinstance(expiry, datetime) and expiry.tzinfo is not None, "Command requires an aware expiry")
        body = {"command_id": command_id, "kind": kind, "target": copy.deepcopy(target), "expires_at": stamp(expiry),
                "directive_reference": copy.deepcopy(directive_reference), "parameter_id": parameter_id,
                "successor_spec": copy.deepcopy(successor_spec), "actor_id": actor.actor_id, "actor_ingress": actor.ingress}
        with self.app.journal.writer() as writer:
            record = self._record(writer.value, work_run_id)
            self._authority(record, actor)
            previous = writer.value["intents"].get(_command(command_id))
            if previous is not None:
                _require(previous["body"] == body, "Command identity conflicts with earlier content")
                return copy.deepcopy(previous["receipt"])
            now = self.app.clock()
            _require(now < expiry and (expiry - now).total_seconds() <= 3600, "Command expired or exceeds one hour")
            _require(isinstance(target, dict) and record_digest(target) == record_digest(self.target(record))
                     and not record["run"]["pending_action_ids"], "Stale control target or another pending command")
            self._quiescent(writer.value, record)
            _require(record.get("steering_native_boundary", {}).get("pending_nodes") == ["normalization_report"],
                     "Native LangGraph has not attested its unscheduled Profile checkpoint")
            boundary = record.get("steering_boundary")
            _require(kind in {"question": {"answer"}, "partial": {"parameter", "cutover"}, "closed": {"amend_work", "resume"}}.get(boundary, set()),
                     "Command is unsupported at this Profile boundary")
            content = self._artifact(record, directive_reference)
            _require((kind == "parameter") == (parameter_id is not None) and (kind == "amend_work") == (successor_spec is not None),
                     "Command fields differ from kind")
            if kind == "parameter":
                _require(parameter_id == PARAMETER and parameter_id in record["spec"]["steerable_parameter_ids"]
                         and set(content) == {"value"} and content["value"] in ORDERS, "Parameter is outside declared bounds")
            elif kind == "answer":
                _require(set(content) == {"answer"} and isinstance(content["answer"], str) and 0 < len(content["answer"].strip()) <= 256,
                         "Question requires a bounded nonempty owner answer")
            elif kind == "amend_work":
                _require(content == {"successor_spec_digest": successor_spec.get("spec_digest")}, "Amendment artifact targets another revision")
                self._amendment(record, successor_spec)
            else:
                _require(set(content) == {"reason"} and isinstance(content["reason"], str) and 0 < len(content["reason"]) <= 256,
                         "Structural control requires a bounded reason")
            before = copy.deepcopy(record["run"])
            received = {**before, "revision": before["revision"] + 1, "pending_action_ids": [command_id]}
            self.exops.contracts.run(received, record["spec"], previous=before)
            record["run"] = received
            if kind == "answer":
                request = copy.deepcopy(record["steering_question"])
                _require(now < datetime.fromisoformat(request["expires_at"]), "Question expired")
                response = {key: request[key] for key in ("owner_operation_id", "work_run_id", "work_spec_revision", "work_spec_digest", "target_control_revision", "target_digest", "request_id", "request_revision")}
                response.update(response_id="response.steering." + record_digest(body)[7:39], actor_id=actor.actor_id,
                                chosen_action="approve", idempotency_key=command_id, received_at=stamp(now), submission_state="submitted")
                self.exops.contracts.record("HumanActionResponse", response)
                decision = {"receipt_id": "decision.steering." + record_digest(body)[7:39], "owner_operation_id": "knowledgeos",
                            "request_id": request["request_id"], "request_revision": request["request_revision"],
                            "decision_state": "accepted", "chosen_action": "approve"}
                intent = {"owner_operation_id": "knowledgeos", "effect_id": "effect.steering." + record_digest(body)[7:39],
                          "content_digest": record_digest(body), "work_run_id": work_run_id,
                          "graph_run_id": before["active_graph_run_id"], "generation": before["generation"],
                          "fencing_token": before["fencing_token"], "issued_at": stamp(now), "expires_at": stamp(expiry)}
                values = {"request": request, "response": response, "decision": decision, "execution_intent": intent}
            else:
                values = self._build(record, actor, command_id, kind, target, expiry, now, directive_reference, parameter_id)
                self._validate(values, record["spec"], received, now, record)
            self.exops.contracts.record("ExecutionIntent", values["execution_intent"])
            receipt = {"owner_operation_id": "knowledgeos", "work_run_id": work_run_id, "command_id": command_id,
                       "kind": kind, "receipt_state": "accepted", "application_state": "received",
                       "target_control_revision": before["control_revision"], "expires_at": stamp(expiry)}
            command = {**values, "body": body, "target": copy.deepcopy(target), "actor_id": actor.actor_id,
                       "actor_ingress": actor.ingress, "received_run": copy.deepcopy(received), "spec": copy.deepcopy(record["spec"]),
                       "state": "received", "receipt": receipt,
                       "dispatch": {"work_run_id": work_run_id, "command_id": command_id, "target": copy.deepcopy(target)}}
            writer.value["intents"][_command(command_id)] = command
            writer.value["human_requests"][values["request"]["request_id"]] = copy.deepcopy(values["request"])
            writer.value["decisions"][values["request"]["request_id"]] = {
                "response": copy.deepcopy(values["response"]), "receipt": copy.deepcopy(values["decision"]),
                "target_digest": values["request"]["target_digest"], "pending_apply": True}
            writer.commit(now)
            return copy.deepcopy(receipt)

    def _validate(self, command, spec, run, now, record, **kwargs):
        artifacts = {key: value["reference"] for key, value in record.get("steering_artifacts", {}).items()}
        self.exops.contracts.steering(command["directive"], spec, run, request=command["request"], response=command["response"],
            decision=command["decision"], target_binding=command["target_binding"], intent=command["execution_intent"],
            candidate_actor_id=command.get("actor_id", command["directive"]["actor_id"]), now=now, artifacts=artifacts, **kwargs)

    def _native(self, record, temporal_run_id):
        _require(record.get("temporal_run_id") == temporal_run_id, "Native Run is foreign or superseded")

    def dispatch(self, work_run_id):
        record = self._record(self.app.journal.read(), work_run_id)
        self._authority(record)
        return {"work_run_id": work_run_id, "action_id": record["action_id"], "content_digest": record["content_digest"],
                "work_spec_digest": record["spec"]["spec_digest"]}

    def command_dispatch(self, work_run_id, command_id):
        record = self._record(self.app.journal.read(), work_run_id)
        self._authority(record)
        command = self.app.journal.read()["intents"].get(_command(command_id))
        _require(isinstance(command, dict) and command["target"]["work_run_id"] == work_run_id, "Command is unavailable")
        self._authority(record, OwnerActor(command["actor_id"], command["actor_ingress"]))
        return copy.deepcopy(command["dispatch"])

    def apply(self, payload, *, temporal_run_id):
        with self.app.journal.writer() as writer:
            record = self._record(writer.value, payload["work_run_id"])
            command = writer.value["intents"].get(_command(payload["command_id"]))
            _require(isinstance(command, dict) and payload == command["dispatch"], "Dispatch differs from durable owner outbox")
            self._authority(record, OwnerActor(command["actor_id"], command["actor_ingress"]))
            self._native(record, temporal_run_id)
            content = self._artifact(record, command["body"]["directive_reference"])
            decision = writer.value["decisions"].get(command["request"]["request_id"], {})
            expected_request = copy.deepcopy(command["request"])
            if command["body"]["kind"] == "answer" and command["state"] == "applied":
                expected_request["lifecycle"] = "resolved"
            _require(writer.value["human_requests"].get(command["request"]["request_id"]) == expected_request
                     and decision.get("response") == command["response"] and decision.get("receipt") == command["decision"]
                     and decision.get("target_digest") == command["request"]["target_digest"]
                     and decision.get("pending_apply") is (command["state"] != "applied"),
                     "Owner human decision was revoked, replaced or lost")
            if command["body"]["kind"] == "answer":
                intent = command["execution_intent"]
                response = command["response"]
                request = command["request"]
                _require(intent["content_digest"] == record_digest(command["body"])
                         and response["actor_id"] == command["actor_id"] and response["chosen_action"] == "approve"
                         and request["allowed_actions"] == ["approve"] and response["target_digest"] == request["target_digest"]
                         and all(intent[key] == command["received_run"][key] for key in ("generation", "fencing_token", "work_run_id"))
                         and intent["graph_run_id"] == command["received_run"].get("active_graph_run_id"),
                         "Human answer differs from its exact accepted target or effect")
            if command["state"] == "applied":
                _require(writer.value["receipts"].get(_receipt_key(payload["command_id"]), {}).get("receipt") == command["execution_receipt"],
                         "Lost acknowledgement has no exact durable receipt")
                if command["body"]["kind"] != "answer":
                    self._validate(command, command["spec"], command["received_run"], self.app.clock(), record,
                        receipt=command["execution_receipt"], receipt_reference_id=command["receipt"]["execution_receipt_id"],
                        applied_run=command["applied_run"], successor_spec=command["body"]["successor_spec"],
                        cutting_over_run=command.get("cutting_over_run"))
                return copy.deepcopy(command["receipt"])
            _require(command["state"] == "received" and record["run"] == command["received_run"], "Command target was superseded")
            _require(self.app.clock() < datetime.fromisoformat(command["body"]["expires_at"]), "Queued command expired")
            _require(self.app.clock() < datetime.fromisoformat(command["request"]["expires_at"]), "Human question or decision expired")
            self._quiescent(writer.value, record)
            kind = command["body"]["kind"]
            if kind == "cutover":
                _require(record.get("steering_boundary") == "partial" and record.get("steering_partial") is not None,
                         "CUTOVER requires fresh independent partial assessment")
                self._check_partial(record)
                cutting = self._advance(record, state="cutting_over")
                command["cutting_over_run"] = copy.deepcopy(cutting)
                generation = cutting["generation"] + 1
                changes = {"state": "paused", "pending_action_ids": [], "active_graph_run_id": None,
                           "generation": generation, "fencing_token": "steering." + record_digest({"id": payload["work_run_id"], "generation": generation})[7:]}
                record["steering_boundary"] = "closed"
            else:
                changes = {"pending_action_ids": []}
                if kind == "answer":
                    changes["state"] = "paused"
                    record.setdefault("steering_context", {})["answer"] = content["answer"]
                    record["steering_boundary"] = "partial"
                    record["steering_question"]["lifecycle"] = "resolved"
                    writer.value["human_requests"][command["request"]["request_id"]]["lifecycle"] = "resolved"
                elif kind == "parameter":
                    record.setdefault("steering_context", {})["order"] = content["value"]
                elif kind == "amend_work":
                    successor = command["body"]["successor_spec"]
                    self._amendment(record, successor)
                    record.setdefault("steering_spec_history", []).append(copy.deepcopy(record["spec"]))
                    record["steering_amendment_run"] = copy.deepcopy(record["run"])
                    changes.update(work_spec_revision=successor["work_spec_revision"], work_spec_digest=successor["spec_digest"])
                elif kind == "resume":
                    changes["state"] = "running"
                    record["steering_boundary"] = "released"
            old_spec = command["spec"]
            successor_spec = command["body"]["successor_spec"] if kind == "amend_work" else None
            applied = self._advance(record, spec=successor_spec, **changes)
            if successor_spec is not None:
                record["spec"] = copy.deepcopy(successor_spec)
            intent = command["execution_intent"]
            receipt = {key: intent[key] for key in ("owner_operation_id", "effect_id", "content_digest", "generation", "fencing_token", "work_run_id")}
            if "graph_run_id" in intent:
                receipt["graph_run_id"] = intent["graph_run_id"]
            receipt.update(outcome="succeeded", observed_at=stamp(self.app.clock()), result_digest=record_digest(applied))
            self.exops.contracts.record("ExecutionReceipt", receipt)
            receipt_id = "receipt.steering." + record_digest({"id": payload["command_id"]})[7:39]
            if kind != "answer":
                command["directive"].update(application_state="applied", applied_at=stamp(self.app.clock()),
                    applied_run_revision=applied["revision"], execution_receipt_id=receipt_id)
                self._validate(command, old_spec, command["received_run"], self.app.clock(), record,
                    receipt=receipt, receipt_reference_id=receipt_id, applied_run=applied,
                    successor_spec=successor_spec, cutting_over_run=command.get("cutting_over_run"))
            command.update(state="applied", execution_receipt=receipt, applied_run=copy.deepcopy(applied))
            command["receipt"].update(application_state="applied", execution_receipt_id=receipt_id, applied_run_revision=applied["revision"])
            writer.value["receipts"][_receipt_key(payload["command_id"])] = {"receipt": receipt, "unresolved": False}
            writer.value["decisions"][command["request"]["request_id"]]["pending_apply"] = False
            writer.commit(self.app.clock())
            return copy.deepcopy(command["receipt"])

    def _make_bundle(self, record, *, successor=False):
        spec, run = record["spec"], record["run"]
        bundle, artifacts = _bundle(self.app, spec, run, record["method_source_reference"])
        if successor:
            successor_graph_id = bundle["graph_spec"]["graph_id"] + ".successor"
            for item in (bundle["graph_spec"], bundle["segment"], *bundle["nodes"], *bundle["assignments"]):
                item["graph_id"] = successor_graph_id
            ids = ["node.start", "node.normalization_proposal", "node.finish"]
            bundle["nodes"] = [node for node in bundle["nodes"] if node["node_id"] in ids]
            base = {key: bundle["graph_spec"][key] for key in ("graph_id", "graph_revision")}
            bundle["edges"] = [{**base, "edge_id": "edge." + str(index), "source_node_id": a, "target_node_id": b, "condition": "always"}
                               for index, (a, b) in enumerate(pairwise(ids))]
            bundle["graph_spec"].update(node_ids=ids, edge_ids=[e["edge_id"] for e in bundle["edges"]], max_steps=3)
            bundle["graph_spec"]["graph_digest"] = graph_content_digest(bundle["graph_spec"], bundle["nodes"], bundle["edges"])
            bundle["assignments"] = [assignment for assignment in bundle["assignments"] if assignment["node_id"] == "node.normalization_proposal"]
            bundle["segment"]["graph_run_id"] += ".successor"
            bundle["segment"].update(segment_index=2, graph_digest=bundle["graph_spec"]["graph_digest"])
            predecessor = record["steering_bundles"][run["graph_run_ids"][-1]]["segment"]
            bundle["segment"]["predecessor_graph_run_id"] = predecessor["graph_run_id"]
            if spec["work_spec_revision"] > predecessor["work_spec_revision"]:
                trigger = spec
                bundle["segment"].update(trigger_kind="work_revision", trigger_reference_id=spec["work_id"], trigger_digest=record_digest(spec))
            else:
                trigger = record["steering_cutover"]
                bundle["segment"].update(trigger_kind="steering", trigger_reference_id=trigger["directive_id"], trigger_digest=record_digest(trigger))
            context_raw = canonical(record["steering_context"])
            context_ref = self._report_reference(context_raw, prefix="artifact.steering.context.")
            bundle["segment"]["input_references"].append(context_ref)
            artifacts[context_ref["artifact_id"]] = context_ref
        else:
            predecessor = trigger = None
        for assignment in bundle["assignments"]:
            assignment["selection_authority"] = "bounded_manager"
        bundle["segment"]["segment_digest"] = segment_digest(bundle["segment"])
        self.exops.contracts.segment(spec=spec, run=run, artifacts=artifacts, predecessor=predecessor, trigger=trigger,
            current_method_ref=WorkMethodCatalog.load(self.app.roots.control).reference(METHOD_ID), **bundle)
        return bundle, artifacts

    def start(self, dispatch, *, temporal_run_id):
        with self.app.journal.writer() as writer:
            record = self._record(writer.value, dispatch["work_run_id"])
            self._authority(record)
            _require(dispatch == {"work_run_id": record["run"]["work_run_id"], "action_id": record["action_id"],
                                 "content_digest": record["content_digest"], "work_spec_digest": record["spec"]["spec_digest"]}, "Native start differs from exact dispatch")
            if record.get("temporal_run_id"):
                self._native(record, temporal_run_id)
                _require(record.get("steering_bundles"), "Native start is incomplete")
                return copy.deepcopy(record["run"])
            _require(record["run"]["state"] == "admitted", "Steering can start only once")
            record["temporal_run_id"] = temporal_run_id
            self._advance(record, state="running")
            bundle, artifacts = self._make_bundle(record)
            graph_id = bundle["segment"]["graph_run_id"]
            self._advance(record, active_graph_run_id=graph_id, graph_run_ids=[graph_id], segments_started=1, profile_invocations=2)
            record.update(application_state="started", dispatch_state="dispatched", steering_bundles={graph_id: bundle},
                          steering_context={"order": "source_order"}, steering_boundary="triage",
                          admitted_method_segments={graph_id: {"segment": bundle["segment"], "assignments": bundle["assignments"]}},
                          artifact_references=artifacts)
            record["receipt"].update(application_state="started", dispatch_state="dispatched")
            writer.commit(self.app.clock())
            return copy.deepcopy(record["run"])

    def triage(self, work_run_id, *, temporal_run_id):
        with self.app.journal.writer() as writer:
            record = self._record(writer.value, work_run_id)
            self._authority(record)
            self._native(record, temporal_run_id)
            if record.get("steering_question"):
                return copy.deepcopy(record["steering_question"])
            _require(record["steering_boundary"] == "triage", "Triage Profile is no longer active")
            _path, raw = _source(self.app, record["method_source_reference"])
            record["steering_triage"] = {"source_digest": digest(raw), "size_bytes": len(raw),
                "generation": record["run"]["generation"], "graph_run_id": record["run"]["active_graph_run_id"],
                "executed_nodes": ["node.capture_evidence"], "not_run_nodes": ["node.normalization_proposal"]}
            run = self._advance(record, state="awaiting_human")
            record["steering_boundary"] = "question"
            target_digest = record_digest(self.target(record))
            from datetime import timedelta
            question = {"owner_operation_id": "knowledgeos", "request_id": "request.steering.question." + record_digest({"id": work_run_id})[7:39],
                        "request_revision": 1, "question": "What nonempty label should the later normalization report retain?",
                        "impact": "The answer changes future context only; triage and the partial Graph remain immutable.",
                        "allowed_actions": ["approve"], "evidence_reference_ids": [record["method_source_reference"]["logical_id"]],
                        "expires_at": stamp(self.app.clock() + timedelta(hours=1)), "lifecycle": "pending",
                        "semantic_version": run["semantic_version"], "semantic_digest": run["semantic_digest"],
                        "work_run_id": work_run_id, "work_spec_revision": run["work_spec_revision"], "work_spec_digest": run["work_spec_digest"],
                        "target_control_revision": run["control_revision"], "target_digest": target_digest}
            self.exops.contracts.record("HumanActionRequest", question)
            record.update(steering_question=question, steering_boundary="question")
            writer.value["human_requests"][question["request_id"]] = copy.deepcopy(question)
            writer.commit(self.app.clock())
            return copy.deepcopy(question)

    def open_boundary(self, work_run_id, *, pending_nodes, temporal_run_id):
        """Called only by the exact native boundary Activity after aget_state."""
        with self.app.journal.writer() as writer:
            record = self._record(writer.value, work_run_id)
            self._authority(record)
            self._native(record, temporal_run_id)
            _require(pending_nodes == ["normalization_report"] and record["run"]["state"] == "awaiting_human"
                     and record["steering_boundary"] == "question" and not record["run"]["pending_action_ids"],
                     "Native Graph did not expose the supported partial Profile checkpoint")
            proof = {"pending_nodes": list(pending_nodes), "temporal_run_id": temporal_run_id,
                     "graph_run_id": record["run"]["active_graph_run_id"], "generation": record["run"]["generation"],
                     "fencing_token": record["run"]["fencing_token"],
                     "triage_digest": record_digest(record["steering_triage"]),
                     "segment_digest": record["steering_bundles"][record["run"]["active_graph_run_id"]]["segment"]["segment_digest"]}
            _require(record.get("steering_native_boundary", proof) == proof, "Native partial checkpoint changed")
            record["steering_native_boundary"] = proof
            writer.commit(self.app.clock())
            return copy.deepcopy(proof)

    def _evaluation(self, record, *, complete, reference):
        spec, run = record["spec"], record["run"]
        evaluator = spec["eval_officer_executor_id"]
        _require(evaluator not in [spec["manager_executor_id"], *spec["allowed_executor_ids"]], "Evaluation must be independent")
        graph_id = run["active_graph_run_id"]
        checkpoint_evidence = {"graph_run_id": graph_id, "triage": record["steering_triage"], "reference": reference,
                               "context": record.get("steering_successor_context"), "complete": complete}
        if complete:
            checkpoint_evidence["partial_assessment_digest"] = record_digest(record["steering_partial"]["assessment"])
        checkpoint = record_digest(checkpoint_evidence)
        suffix = checkpoint[7:39]
        criterion = "criterion.proposal"
        evaluation = {"owner_operation_id": "knowledgeos", "run_id": graph_id, "work_run_id": run["work_run_id"],
                      "work_spec_revision": spec["work_spec_revision"], "work_spec_digest": spec["spec_digest"],
                      "checkpoint_digest": checkpoint, "criteria_revision": spec["criteria_revision"], "criterion_ids": [criterion],
                      "passed_criterion_ids": [criterion] if complete else [], "failed_criterion_ids": [],
                      "not_run_criterion_ids": [] if complete else [criterion], "evaluation_id": "evaluation.steering." + suffix,
                      "evaluator_id": evaluator, "evidence_references": [reference]}
        assessment = {**_context(run), **_pins(self.app), "assessment_id": "assessment.steering." + suffix,
                      "criteria_revision": spec["criteria_revision"], "assessor_id": evaluator, "assessed_at": stamp(self.app.clock()),
                      "graph_run_ids": [graph_id], "completion_state": "complete" if complete else "incomplete",
                      "criterion_outcomes": [{"criterion_id": criterion, "outcome": "satisfied" if complete else "not_run",
                                              "evaluation_ids": [evaluation["evaluation_id"]], "evidence_references": [reference]}]}
        segment = record["steering_bundles"][graph_id]["segment"]
        artifacts = {reference["artifact_id"]: reference}
        self.exops.contracts.assessment(assessment, spec, run, segments=[segment], evaluations=[evaluation], artifacts=artifacts,
                                       checkpoints={graph_id: checkpoint}, candidate_assessor_id=evaluator)
        return {"assessment": assessment, "evaluations": [evaluation], "checkpoints": {graph_id: checkpoint},
                "artifacts": artifacts, "segment": copy.deepcopy(segment), "spec": copy.deepcopy(spec), "run": copy.deepcopy(run)}

    def partial(self, payload, *, temporal_run_id):
        with self.app.journal.writer() as writer:
            record = self._record(writer.value, payload["work_run_id"])
            self._authority(record)
            self._native(record, temporal_run_id)
            command = writer.value["intents"].get(_command(payload["command_id"]))
            _require(command is not None and command["dispatch"] == payload and command["body"]["kind"] == "cutover"
                     and command["state"] == "received" and record["run"] == command["received_run"], "Partial assessment targets another command")
            self._quiescent(writer.value, record)
            proof = record.get("steering_native_boundary", {})
            _require(proof.get("pending_nodes") == ["normalization_report"]
                     and proof.get("graph_run_id") == record["run"].get("active_graph_run_id")
                     and proof.get("generation") == record["run"]["generation"]
                     and proof.get("fencing_token") == record["run"]["fencing_token"],
                     "CUTOVER did not use the actual interrupted native Graph")
            _require(record["steering_triage"]["not_run_nodes"] == ["node.normalization_proposal"]
                     and "steering_report" not in record, "Partial Graph already dispatched the replacement effect")
            _path, raw = _source(self.app, record["method_source_reference"])
            _require(record["steering_triage"]["source_digest"] == digest(raw), "Triage source evidence changed")
            result = self._evaluation(record, complete=False, reference=_artifact(record["method_source_reference"], raw))
            previous = record.get("steering_partial")
            if previous is not None:
                result["assessment"]["assessed_at"] = previous["assessment"]["assessed_at"]
                _require(previous == result, "Partial assessment changed its original evidence")
            record["steering_partial"] = result
            writer.commit(self.app.clock())
            return {"assessment_digest": record_digest(result["assessment"]), "outcome": "not_run"}

    def _check_partial(self, record):
        retained = record["steering_partial"]
        _require(retained["spec"] == record["spec"] and retained["run"] == record["run"], "Partial assessment does not bind current cutover")
        self.exops.contracts.assessment(retained["assessment"], retained["spec"], retained["run"], segments=[retained["segment"]],
            evaluations=retained["evaluations"], artifacts=retained["artifacts"], checkpoints=retained["checkpoints"],
            candidate_assessor_id=record["spec"]["eval_officer_executor_id"])

    def successor(self, work_run_id, *, temporal_run_id):
        with self.app.journal.writer() as writer:
            record = self._record(writer.value, work_run_id)
            self._authority(record)
            self._native(record, temporal_run_id)
            self._quiescent(writer.value, record)
            _require(record["run"]["state"] == "running" and record["steering_boundary"] == "released"
                     and not record["run"].get("active_graph_run_id") and not record["run"]["pending_action_ids"], "Successor is not released")
            commands = [value for name, value in writer.value["intents"].items() if name.startswith("curation.steering.command.")
                        and value["target"]["work_run_id"] == work_run_id and value["body"]["kind"] == "cutover"]
            _require(len(commands) == 1 and commands[0]["state"] == "applied", "Successor requires exactly one safely applied CUTOVER")
            record["steering_cutover"] = copy.deepcopy(commands[0]["directive"])
            bundle, artifacts = self._make_bundle(record, successor=True)
            graph_id = bundle["segment"]["graph_run_id"]
            run = record["run"]
            record["steering_successor_admission_run"] = copy.deepcopy(run)
            self._advance(record, active_graph_run_id=graph_id, graph_run_ids=[*run["graph_run_ids"], graph_id],
                          segments_started=run["segments_started"] + 1, profile_invocations=run["profile_invocations"] + 1)
            record["steering_bundles"][graph_id] = bundle
            record["admitted_method_segments"][graph_id] = {"segment": bundle["segment"], "assignments": bundle["assignments"]}
            record["artifact_references"].update(artifacts)
            record.update(steering_successor_context=copy.deepcopy(record["steering_context"]), steering_boundary="normalization")
            writer.commit(self.app.clock())
            return {"work_run_id": work_run_id, "generation": record["run"]["generation"], "fencing_token": record["run"]["fencing_token"], "graph_run_id": graph_id}

    @staticmethod
    def _report_reference(raw, *, prefix="artifact.steering.report."):
        return {"owner_operation_id": "knowledgeos", "artifact_id": prefix + digest(raw)[7:39], "source_revision": digest(raw)[7:],
                "content_digest": digest(raw), "size_bytes": len(raw), "media_type": "application/json", "classification": "internal",
                "availability": "available_by_owner_export", "export_capability_id": "knowledgeos.steering.artifact"}

    def _normalized(self, record):
        _path, raw = _source(self.app, record["method_source_reference"])
        context = record["steering_successor_context"]
        _require(context["order"] in ORDERS and isinstance(context["answer"], str) and context["answer"].strip(), "Successor context is outside admitted bounds")
        lines = [line.strip() for line in raw.decode("utf-8").splitlines() if line.strip()]
        if context["order"] == "alphabetical":
            lines.sort()
        report = {"source_digest": digest(raw), "lines": lines, "order": context["order"], "answer": context["answer"],
                  "objective": record["spec"]["objective"]}
        if record["spec"]["work_spec_revision"] == 2:
            report.update(label=context["answer"], line_count=len(lines))
        return canonical(report)

    def normalize(self, token, *, temporal_run_id):
        with self.app.journal.writer() as writer:
            record = self._record(writer.value, token["work_run_id"])
            self._authority(record)
            self._native(record, temporal_run_id)
            run = record["run"]
            _require(token == {"work_run_id": run["work_run_id"], "generation": run["generation"], "fencing_token": run["fencing_token"],
                               "graph_run_id": run.get("active_graph_run_id")}, "Late old-generation Profile cannot commit")
            _require(run["state"] == "running" and not run["pending_action_ids"] and record["steering_boundary"] == "normalization", "Normalization is not scheduled")
            raw = self._normalized(record)
            reference = self._report_reference(raw)
            value = {"reference": reference, "bytes": base64.b64encode(raw).decode(), "token": token}
            _require(record.get("steering_report", value) == value, "Report publication conflicts with prior effect")
            # Sole effect boundary: bytes and successful effect receipt share this
            # writer transaction. No filesystem/provider effect precedes the commit.
            effect_id = "effect.steering.report." + record_digest(token)[7:39]
            intent = {"owner_operation_id": "knowledgeos", "effect_id": effect_id, "content_digest": digest(raw),
                      "generation": run["generation"], "fencing_token": run["fencing_token"], "work_run_id": run["work_run_id"],
                      "graph_run_id": run["active_graph_run_id"], "issued_at": stamp(self.app.clock()),
                      "expires_at": record["expires_at"]}
            self.exops.contracts.effect(intent, run, dispatch=True)
            receipt = {**{key: value for key, value in intent.items() if key not in {"issued_at", "expires_at"}}, "outcome": "succeeded",
                       "observed_at": stamp(self.app.clock()), "result_digest": digest(raw)}
            self.exops.contracts.effect(intent, run, receipt=receipt)
            record["steering_report"] = value
            record["steering_report_intent"] = intent
            writer.value["receipts"][_receipt_key(effect_id)] = {"receipt": receipt, "unresolved": False}
            writer.commit(self.app.clock())
            return {"work_run_id": run["work_run_id"], "reference": reference}

    def finish(self, work_run_id, *, temporal_run_id):
        with self.app.journal.writer() as writer:
            record = self._record(writer.value, work_run_id)
            self._authority(record)
            self._native(record, temporal_run_id)
            if record["run"]["state"] == "completed":
                return copy.deepcopy(record["receipt"])
            report = record["steering_report"]
            raw = base64.b64decode(report["bytes"], validate=True)
            _require(raw == self._normalized(record) and report["reference"] == self._report_reference(raw), "Independent evaluator rejected report bytes/context")
            receipt = writer.value["receipts"].get(_receipt_key(record["steering_report_intent"]["effect_id"]), {})
            _require(receipt.get("unresolved") is False and receipt.get("receipt", {}).get("result_digest") == digest(raw), "Report effect is unresolved")
            self.exops.contracts.effect(record["steering_report_intent"], record["run"], receipt=receipt["receipt"])
            result = self._evaluation(record, complete=True, reference=report["reference"])
            previous = copy.deepcopy(record["run"])
            run = {**previous, "revision": previous["revision"] + 1, "control_revision": previous["control_revision"] + 1,
                   "state": "completed", "active_graph_run_id": None, "latest_assessment_id": result["assessment"]["assessment_id"],
                   "latest_assessment_digest": record_digest(result["assessment"]), "result_reference": report["reference"]}
            run.pop("active_graph_run_id")
            self.exops.contracts.run(run, record["spec"], previous=previous, assessment=result["assessment"], segments=[result["segment"]],
                evaluations=result["evaluations"], artifacts=result["artifacts"], checkpoints=result["checkpoints"],
                candidate_assessor_id=record["spec"]["eval_officer_executor_id"])
            record.update(run=run, assessment=result["assessment"], evaluations=result["evaluations"], checkpoints=result["checkpoints"],
                          assessment_artifacts=result["artifacts"], application_state="recorded", state="recorded", steering_boundary="completed")
            record["receipt"].update(application_state="recorded", dispatch_state="dispatched")
            execution = {key: record["execution_intent"][key] for key in (
                "owner_operation_id", "effect_id", "content_digest", "generation", "fencing_token", "work_run_id")}
            execution.update(outcome="succeeded", observed_at=stamp(self.app.clock()), result_digest=record_digest(run))
            self.exops.contracts.record("ExecutionReceipt", execution)
            writer.value["receipts"]["exops.work." + record["action_id"]] = {"receipt": execution, "unresolved": False}
            record["steering_completion"] = copy.deepcopy({
                "source_reference": record["method_source_reference"],
                "triage": record["steering_triage"], "context": record["steering_successor_context"],
                "bundle": record["steering_bundles"][previous["active_graph_run_id"]],
                "admission_run": record["steering_successor_admission_run"], "partial": record["steering_partial"],
                "report": report, "effect_intent": record["steering_report_intent"],
                "effect_receipt": receipt["receipt"], "result": result, "work_receipt": execution,
            })
            writer.commit(self.app.clock())
            return copy.deepcopy(record["receipt"])

    def export(self, work_run_id, *, actor):
        record = self._record(self.app.journal.read(), work_run_id)
        self._authority(record, actor, "read_work")
        _require(record["run"]["state"] == "completed", "Report has not completed independent assessment")
        report = record["steering_report"]
        raw = base64.b64decode(report["bytes"], validate=True)
        _require(report["reference"] == self._report_reference(raw) and raw == self._normalized(record), "Owner report export changed")
        return {"reference": copy.deepcopy(report["reference"]), "content_base64": report["bytes"], "assessment": copy.deepcopy(record["assessment"])}

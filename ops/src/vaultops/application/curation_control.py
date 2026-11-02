"""Owner-admitted bounded PAUSE/RESUME/CANCEL at a closed curation boundary."""

from __future__ import annotations

import copy
import re
from collections.abc import Mapping
from datetime import datetime

from ..adapters.core import AdmissionError
from ..adapters.owner_journal import stamp
from .curation_continuation import (
    METHOD_ID,
    CurationContinuationExecutor,
    _bundle,
    curation_continuation_admission,
    curation_continuation_native_digest,
)
from .curation_work import _require, _runner_binding, _source
from .exops import OwnerActor
from .team_catalog import TeamCatalog
from .temporal_contract import CURATION_DISPATCH_FIELDS
from .work import WorkContracts, directive_digest, record_digest, work_control_binding
from .work_binding import _target_digest
from .work_methods import WorkMethodCatalog


def _work_key(work_run_id):
    return "exops.work." + work_run_id


def _command_key(command_id):
    return "curation.control." + command_id


def _rollover_key(work_run_id):
    return "curation.rollover." + work_run_id


def _control_receipt_key(command_id):
    # Control receipts must not collide with caller-selected Work action IDs.
    return "exops.control." + record_digest({"command_id": command_id})[7:39]


def _exact(value, expected):
    return (
        isinstance(value, Mapping)
        and dict(value) == expected
        and all(type(value[key]) is type(item) for key, item in expected.items())
    )


def validate_control_release(application, snapshot, record):
    """Enforce retained release proof in an SDK-free scoped MCP process.

    Live Director/actor callbacks remain mandatory at owner admission/execution.
    Tools independently enforce the same durable release and current pins.
    """
    application.guard("inspect")
    _require(record.get("control_checkpoint") is True, "Work did not admit human control")
    checkpoint = record.get("curation_control_checkpoint")
    _require(isinstance(checkpoint, dict), "Controlled Work has no native checkpoint")
    baseline = checkpoint["initial_record"]
    _require(record_digest(baseline) == checkpoint["work_record_digest"]
             and checkpoint["control_policy"] == application.policy,
             "Original release admission or policy changed")
    spec = record["spec"]
    contracts = WorkContracts(application.core)
    contracts.run(record["run"], spec)
    _require(spec == baseline["spec"], "Released WorkSpec, criteria or explicit assignment changed")
    WorkMethodCatalog.load(application.roots.control).admitted_method(spec, contracts)
    _source(application, record["method_source_reference"])
    if record.get("selected_scope") is not None:
        scope = record["selected_scope"]
        _require(application.reference(scope["path"])["source_revision"] == scope["content_sha256"],
                 "Selected owner Work note changed")
    first = baseline["run"]["graph_run_ids"][0]
    admitted = baseline["admitted_method_segments"][first]
    _require(admitted["team_catalog_digest"] == TeamCatalog.load(application.roots.control).digest
             and admitted["native_binding_digest"] == curation_continuation_native_digest(checkpoint["runner_binding"])
             and record["admitted_method_segments"].get(first) == admitted,
             "Released Team, native execution or initial assignment changed")
    mutable = {"run", "assessment", "evaluations", "checkpoints", "assessment_artifacts",
               "admitted_method_segments", "state", "application_state", "receipt", "continuation_state"}
    _require(all(record.get(key) == value for key, value in baseline.items() if key not in mutable),
             "Released Work changed original evidence or admission")
    _require(all(record.get(key) == baseline[key] for key in (
        "assessment", "remaining_work", "evaluations", "checkpoints", "assessment_artifacts",
    )), "Released Work changed its independent initial gap")
    original = checkpoint["initial_run"]
    current = {**copy.deepcopy(original), "revision": original["revision"] + 1,
               "control_revision": original["control_revision"] + 1, "state": "awaiting_human"}
    _require(checkpoint["run"] == baseline["run"] == current
             and baseline["curation_initial_assessment"]["run"] == original,
             "Released Work changed its original closed run")
    ids = checkpoint["command_ids"]
    _require(isinstance(ids, list) and len(ids) == 2 and len(set(ids)) == 2,
             "Successor requires one PAUSE then one RESUME")
    commands = {item["command_id"]: item for name, item in snapshot["intents"].items()
                if name.startswith("curation.control.")
                and item.get("target", {}).get("work_run_id") == current["work_run_id"]}
    _require(set(commands) == set(ids), "Release omitted an owner command")
    target = _checkpoint_target(checkpoint, current)
    for command_id, kind in zip(ids, ("pause", "resume"), strict=True):
        command = commands[command_id]
        _require(command["state"] == "applied" and command["body"]["kind"] == kind
                 and command["actor_id"] == record["actor_id"]
                 and command["actor_ingress"] in {"cli", "web"}
                 and _exact(command["target"], target), "Successor lacks an exact applied control chain")
        _require(command["body"] == {
            "work_run_id": current["work_run_id"], "command_id": command_id, "kind": kind,
            "target": target, "actor_id": command["actor_id"], "actor_ingress": command["actor_ingress"],
            "expires_at": command["directive"]["expires_at"],
        }, "Release command body changed")
        received = {**copy.deepcopy(current), "revision": current["revision"] + 1,
                    "pending_action_ids": [command_id]}
        applied = {**copy.deepcopy(received), "revision": received["revision"] + 1,
                   "control_revision": current["control_revision"] + 1,
                   "state": "paused" if kind == "pause" else "running", "pending_action_ids": []}
        _require(command["received_run"] == received and command.get("applied_run") == applied,
                 "Release changed retained command snapshots")
        request_id = command["request"]["request_id"]
        decision = snapshot["decisions"].get(request_id, {})
        _require(snapshot["human_requests"].get(request_id) == command["request"]
                 and decision.get("response") == command["response"]
                 and decision.get("receipt") == command["decision"]
                 and decision.get("target_digest") == command["request"]["target_digest"]
                 and decision.get("pending_apply") is False, "Release changed its durable human decision")
        execution = snapshot["receipts"].get(_control_receipt_key(command_id), {})
        _require(execution.get("receipt") == command["execution_receipt"]
                 and execution.get("unresolved") is False
                 and command["execution_receipt"]["result_digest"] == record_digest(applied)
                 and "work_execution_receipt" not in command,
                 "Release has no exact nonterminal control receipt")
        contracts.steering(
            command["directive"], spec, received, request=command["request"], response=command["response"],
            decision=command["decision"], target_binding=command["target_binding"],
            intent=command["execution_intent"], candidate_actor_id=command["actor_id"],
            now=application.clock(), artifacts={}, receipt=command["execution_receipt"],
            receipt_reference_id=command["receipt"]["execution_receipt_id"], applied_run=applied,
        )
        public = {
            "owner_operation_id": "knowledgeos", "work_run_id": current["work_run_id"],
            "command_id": command_id, "kind": kind, "receipt_state": "accepted", "application_state": "applied",
            "target_control_revision": target["target_control_revision"],
            "expires_at": command["body"]["expires_at"], "applied_run_revision": applied["revision"],
            "execution_receipt_id": command["directive"]["execution_receipt_id"],
        }
        if kind == "pause":
            target = _checkpoint_target(checkpoint, applied)
            public["next_target"] = target
        _require(command["receipt"] == public, "Release public receipt changed")
        current = applied
    _require(_exact(checkpoint["target"], target), "Paused checkpoint target changed")
    run = record["run"]
    _require(run["state"] == "running" and record["application_state"] == "started"
             and not run["pending_action_ids"] and not run["unresolved_effect_ids"],
             "Released Work is not effect-safe")
    if run["segments_started"] == 1:
        _require(run == current, "Successor release changed the closed run")
    else:
        _require(run["segments_started"] == run["profile_invocations"] == len(run["graph_run_ids"]) == 2
                 and run["revision"] == current["revision"] + 1
                 and run["control_revision"] == current["control_revision"] + 1
                 and run["graph_run_ids"][:1] == current["graph_run_ids"]
                 and all(run[key] == current[key] for key in current if key not in {
                     "revision", "control_revision", "graph_run_ids", "segments_started", "profile_invocations",
                 }), "Successor changed release lineage, pins or consumed budgets")
        bundle, _artifacts = _bundle(
            application, spec, run, record["method_source_reference"], index=2,
            predecessor=admitted["segment"], remaining=baseline["remaining_work"],
        )
        successor = record["admitted_method_segments"].get(run["graph_run_ids"][1])
        _require(successor == {
            "segment": bundle["segment"], "assignments": bundle["assignments"],
            "method_contract_ref": spec["method_contract_ref"],
            "source_reference": record["method_source_reference"],
            "team_catalog_digest": admitted["team_catalog_digest"],
            "native_binding_digest": admitted["native_binding_digest"],
        } and run.get("active_graph_run_id") == bundle["segment"]["graph_run_id"],
                 "Released successor changed its exact immutable method assignment")
    retained = baseline["curation_initial_assessment"]
    _require(contracts.continuation(retained["remaining_work"], spec, current,
                                   assessment=retained["assessment"]) == "allowed",
             "Released continuation exhausted its admitted budget")
    return copy.deepcopy(commands[ids[-1]]["receipt"])


def _checkpoint_target(checkpoint, run):
    target = {key: run[key] for key in (
        "owner_operation_id", "work_run_id", "work_spec_revision", "work_spec_digest", "generation", "fencing_token",
    )}
    target.update(target_control_revision=run["control_revision"],
                  temporal_run_id=checkpoint["run_target_temporal_id"])
    target["checkpoint_digest"] = record_digest({
        "target": target, "run": run, "work_record_digest": checkpoint["work_record_digest"],
    })
    return target



class CurationRunControl:
    """A durable human decision is not a native command application.

    The checkpoint is registered only by the selected native Workflow after its
    three read-only initial Activities. Every later boundary reconstructs that
    original closed run in memory, never granting tools a fabricated live run.
    """

    def __init__(self, exops, executor):
        _require(
            isinstance(executor, CurationContinuationExecutor) and executor.exops is exops,
            "Control requires the exact owner continuation executor",
        )
        self.exops, self.executor = exops, executor
        exops.curation_control = self

    def _record(self, snapshot, work_run_id):
        record = snapshot["intents"].get(_work_key(work_run_id))
        _require(isinstance(record, dict), "Control WorkRun is unavailable")
        return record

    def _authority(self, record):
        exops, executor = self.exops, self.executor
        exops._current_binding()
        _require(record.get("control_checkpoint") is True, "Work did not admit human control")
        spec, run = record["spec"], record["run"]
        _require(spec.get("method_contract_ref", {}).get("method_id") == METHOD_ID,
                 "Control requires the selected continuation method")
        _require(_runner_binding(executor.runner.binding) == executor.runner_binding,
                 "Control native runner changed")
        expected, admitted_run = curation_continuation_admission(
            exops.application, exops.teams.get(spec.get("team_id")),
            record.get("method_source_reference"), work_id=spec["work_id"],
            work_run_id=run["work_run_id"], admission_receipt_id=spec["admission_receipt_id"],
            runner_binding=executor.runner_binding, continuation_policy=spec["continuation_policy"],
        )
        _require(spec == expected, "Control WorkSpec or criteria changed")
        admission = {
            "spec": expected, "run": admitted_run, "actor_id": record["actor_id"],
            "action_id": record["action_id"], "expires_at": record["expires_at"],
            "method_source_reference": record["method_source_reference"], "control_checkpoint": True,
        }
        if record.get("selected_scope") is not None:
            admission["selected_scope"] = record["selected_scope"]
            scope = record["selected_scope"]
            _require(exops.application.reference(scope["path"])["source_revision"] == scope["content_sha256"],
                     "Selected owner Work note changed")
        _require(record.get("officer_source_reference") is None
                 and record.get("content_digest") == record_digest(admission),
                 "Control flag or original owner admission changed")
        intent = record["execution_intent"]
        exops.contracts.record("ExecutionIntent", intent)
        _require(all(intent.get(key) == value for key, value in {
            "owner_operation_id": "knowledgeos", "effect_id": record["action_id"],
            "content_digest": record["content_digest"], "work_run_id": run["work_run_id"],
            "generation": admitted_run["generation"], "fencing_token": admitted_run["fencing_token"],
            "expires_at": record["expires_at"],
        }.items()), "Original owner execution intent changed")
        exops._team_binding(spec, run)
        _require(
            exops.director_admitted(spec)
            and exops.director_method_source_admitted is not None
            and exops.director_method_source_admitted(spec, record["method_source_reference"]),
            "Control Director/source admission changed",
        )
        _require(record.get("actor_ingress") in {"cli", "web"}, "Original ingress is unavailable")
        exops._guard(OwnerActor(record["actor_id"], record["actor_ingress"]), "submit_work")
        _source(exops.application, record["method_source_reference"])
        if run["state"] == "completed":
            exops.contracts.run(
                run, spec, assessment=record["assessment"],
                segments=[item["segment"] for item in record["admitted_method_segments"].values()],
                evaluations=record["evaluations"], artifacts=record["assessment_artifacts"],
                checkpoints=record["checkpoints"], candidate_assessor_id=spec["eval_officer_executor_id"],
            )
        else:
            exops.contracts.run(run, spec)

    def _reader(self, record, actor):
        self.exops._guard(actor, "read_work")
        _require(actor.actor_id == record["actor_id"], "Control belongs to the original actor")

    def _command_actor(self, record, actor):
        self.exops._guard(actor, "control_work")
        _require(actor.actor_id == record["actor_id"], "Control belongs to the original actor")

    def _quiescent(self, snapshot, record):
        run = record["run"]
        _require(
            run["segments_started"] == run["profile_invocations"] == 1
            and len(run["graph_run_ids"]) == 1
            and "active_graph_run_id" not in run and not run["unresolved_effect_ids"]
            and record.get("continuation_state") in {"allowed", "cancelled"}
            and not any(key in record for key in (
                "curation_model_request", "curation_model_execution", "submitted_artifacts",
                "canonical_proposal_reference", "curation_failure",
            ))
            and not any(
                item.get("work_binding", {}).get("work_run_id") == run["work_run_id"]
                for item in snapshot["intents"].values()
            ),
            "Control requires a closed initial boundary with no actual or uncertain effects",
        )

    def _initial(self, record, closed_run):
        initial = copy.deepcopy(record)
        initial["run"] = copy.deepcopy(closed_run)
        initial["run"]["active_graph_run_id"] = closed_run["graph_run_ids"][0]
        return self.executor._initial_admission(initial)

    def _target(self, checkpoint, run):
        return _checkpoint_target(checkpoint, run)

    def _checkpoint(self, snapshot, record, *, allow_rejected=False):
        self._authority(record)
        checkpoint = record.get("curation_control_checkpoint")
        _require(isinstance(checkpoint, dict), "Native control checkpoint is unavailable")
        original, closed = checkpoint["initial_run"], checkpoint["run"]
        expected_wait = {**copy.deepcopy(original), "revision": original["revision"] + 1,
                         "control_revision": original["control_revision"] + 1, "state": "awaiting_human"}
        _require(closed == expected_wait, "Safe-wait checkpoint changed its original closed run")
        self.exops.contracts.run(closed, record["spec"], previous=original)
        baseline = checkpoint["initial_record"]
        _require(baseline["run"] == closed
                 and record_digest(baseline) == checkpoint["work_record_digest"],
                 "Original checkpoint evidence or owner attempt changed")
        self._initial(baseline, original)
        _require(record.get("temporal_run_id", checkpoint["run_target_temporal_id"])
                 == checkpoint["run_target_temporal_id"]
                 and record.get("workflow_id") == "knowledgeos/" + closed["work_run_id"]
                 and record.get("dispatch_state") == "accepted", "Known native Work identity changed")
        ids = checkpoint["command_ids"]
        _require(isinstance(ids, list) and len(ids) <= 2 and len(set(ids)) == len(ids),
                 "Bounded control lineage changed")
        commands = {item["command_id"]: item for name, item in snapshot["intents"].items()
                    if name.startswith("curation.control.")
                    and item.get("target", {}).get("work_run_id") == closed["work_run_id"]}
        _require(set(commands) == set(ids), "Control lineage omits an owner command")
        current, target = copy.deepcopy(closed), self._target(checkpoint, closed)
        last = None
        for index, command_id in enumerate(ids):
            command = commands[command_id]
            kind = command["body"].get("kind")
            _require((index == 0 and kind in {"pause", "cancel"} and current["state"] == "awaiting_human")
                     or (index == 1 and kind in {"resume", "cancel"} and current["state"] == "paused"),
                     "Control does not follow the single initial pause/resume boundary")
            _require(_exact(command["target"], target), "Command targets another checkpoint")
            received = {**copy.deepcopy(current), "revision": current["revision"] + 1,
                        "pending_action_ids": [command_id]}
            _require(command["received_run"] == received, "Command pending run changed")
            self._validate_snapshot(snapshot, record, command, current, allow_rejected=allow_rejected)
            current = received
            if command["state"] == "applied":
                current = {**copy.deepcopy(received), "revision": received["revision"] + 1,
                           "control_revision": received["control_revision"] + 1,
                           "state": {"pause": "paused", "resume": "running", "cancel": "cancelled"}[kind],
                           "pending_action_ids": []}
                _require(command.get("applied_run") == current, "Retained applied run changed")
                if kind == "pause":
                    target = self._target(checkpoint, current)
                    _require(_exact(command["receipt"].get("next_target"), target),
                             "Applied PAUSE lost its exact fresh checkpoint")
            else:
                _require(index == len(ids) - 1, "Pending command cannot have a successor")
            last = command
        _require(_exact(checkpoint["target"], target), "Native checkpoint target changed")
        resumed = last is not None and last["state"] == "applied" and last["body"]["kind"] == "resume"
        if resumed and record["run"]["segments_started"] == 2:
            self._resumed_record(record, baseline, current)
        else:
            self._quiescent(snapshot, record)
            expected = copy.deepcopy(baseline)
            expected["run"] = current
            if current["state"] == "cancelled":
                expected.update(state="recorded", application_state="recorded", continuation_state="cancelled")
                expected["receipt"]["application_state"] = "recorded"
            actual = {key: value for key, value in record.items() if key != "curation_control_checkpoint"}
            _require(actual == expected, "Current Work is not the exact checkpoint derivation")
        return checkpoint, last

    def _resumed_record(self, record, baseline, released):
        # Successor admission/result may advance the live run, never the captured
        # initial evidence or the historical command result snapshots.
        mutable = {"run", "assessment", "evaluations", "checkpoints", "assessment_artifacts",
                   "admitted_method_segments", "state", "application_state", "receipt", "continuation_state"}
        _require(all(record.get(key) == value for key, value in baseline.items() if key not in mutable),
                 "Resumed Work changed original admission or independent evidence")
        first = released["graph_run_ids"][0]
        _require(record["admitted_method_segments"].get(first) == baseline["admitted_method_segments"][first],
                 "Resumed Work changed its immutable initial segment")
        selected = copy.deepcopy(record)
        selected["run"]["active_graph_run_id"] = record["run"]["graph_run_ids"][1]
        self.executor._segment(selected)
        run = record["run"]
        if run["state"] == "running":
            _require(all(record.get(key) == baseline[key] for key in (
                "assessment", "remaining_work", "evaluations", "checkpoints", "assessment_artifacts",
            )), "Resumed Work changed its current independent initial gap")
        _require(run["graph_run_ids"][:1] == released["graph_run_ids"]
                 and len(run["graph_run_ids"]) == run["segments_started"] == run["profile_invocations"] == 2
                 and run["repeated_gap_count"] == released["repeated_gap_count"]
                 and not run["pending_action_ids"] and not run["unresolved_effect_ids"]
                 and all(run[key] == released[key] for key in released if key not in {
                     "revision", "control_revision", "state", "graph_run_ids", "segments_started",
                     "profile_invocations", "latest_assessment_id", "latest_assessment_digest",
                 }), "Resumed Work changed consumed budgets, pins or lineage")
        _require(run["revision"] == released["revision"] + (2 if run["state"] == "completed" else 1)
                 and run["control_revision"] == released["control_revision"] + (2 if run["state"] == "completed" else 1),
                 "Resumed Work has an unexpected revision")

    def _authorize_successor(self, snapshot, record):
        _checkpoint, last = self._checkpoint(snapshot, record)
        validate_control_release(self.exops.application, snapshot, record)
        _require(last is not None and last["state"] == "applied"
                 and last["body"]["kind"] == "resume" and record["run"]["state"] == "running"
                 and record["application_state"] == "started",
                 "Successor requires the exact applied owner RESUME")
        return copy.deepcopy(last["receipt"])

    def authorize_successor(self, work_run_id, *, temporal_run_id):
        snapshot = self.exops.application.journal.read()
        record = self._record(snapshot, work_run_id)
        self._authorize_native_run(snapshot, record, temporal_run_id)
        return self._authorize_successor(snapshot, record)

    def _dispatch(self, record):
        return {
            "owner_operation_id": "knowledgeos", "work_run_id": record["run"]["work_run_id"],
            "action_id": record["action_id"], "content_digest": record["content_digest"],
            "work_spec_digest": record["spec"]["spec_digest"],
        }

    def _rollover_state(self, snapshot, record, *, retained=False):
        checkpoint, _last = self._checkpoint(snapshot, record, allow_rejected=retained)
        self._quiescent(snapshot, record)
        _require(record["run"]["state"] in {"awaiting_human", "paused"},
                 "Rollover requires a safe waiting checkpoint")
        commands, pending, retained = {}, [], {}
        for command_id in checkpoint["command_ids"]:
            command = snapshot["intents"][_command_key(command_id)]
            payload = {
                "command_id": command_id, "kind": command["body"]["kind"],
                "target": copy.deepcopy(command["target"]),
            }
            retained[command_id] = copy.deepcopy(command)
            if command["state"] == "applied":
                commands[command_id] = {"payload": payload, "receipt": copy.deepcopy(command["receipt"])}
            else:
                _require(command["state"] == "received", "Rollover has an unresolved rejected command")
                pending.append(payload)
        _require(record["run"]["pending_action_ids"] == [item["command_id"] for item in pending],
                 "Rollover pending command fence changed")
        return {
            "status": record["run"]["state"], "target": copy.deepcopy(checkpoint["target"]),
            "commands": commands,
        }, pending, retained

    def _retained_rollover(self, snapshot, record, intent):
        _require(isinstance(intent, dict) and intent.get("state") in {"prepared", "adopted"},
                 "Rollover intent is invalid")
        fields = {"state", "envelope", "work_record", "command_records"}
        if intent["state"] == "adopted":
            fields.add("temporal_run_id")
        _require(set(intent) == fields and isinstance(intent["work_record"], dict)
                 and isinstance(intent["command_records"], dict)
                 and isinstance(intent["envelope"], dict), "Rollover intent fields changed")
        envelope, original = intent["envelope"], intent["work_record"]
        _require(set(envelope) == {"dispatch", "rollover"}
                 and isinstance(envelope["dispatch"], dict)
                 and set(envelope["dispatch"]) == CURATION_DISPATCH_FIELDS
                 and isinstance(envelope["rollover"], dict), "Rollover envelope changed")
        handoff = envelope["rollover"]
        _require(set(handoff) == {
            "source_temporal_run_id", "workflow_state", "work_record_digest",
            "pending_commands", "handoff_digest",
        }, "Rollover handoff fields changed")
        _require(record_digest(handoff, ("handoff_digest",)) == handoff["handoff_digest"]
                 and record_digest(original) == handoff["work_record_digest"]
                 and record_digest(envelope["dispatch"]) == record_digest(self._dispatch(original))
                 and record_digest(envelope["dispatch"]) == record_digest(self._dispatch(record)),
                 "Rollover retained envelope or Work identity changed")
        original_checkpoint = original.get("curation_control_checkpoint", {})
        checkpoint = record.get("curation_control_checkpoint", {})
        immutable = (
            "initial_record", "initial_run", "run", "work_record_digest",
            "run_target_temporal_id", "runner_binding", "control_policy",
        )
        _require(handoff["source_temporal_run_id"] == original_checkpoint.get("run_target_temporal_id")
                 and record_digest({key: checkpoint.get(key) for key in immutable})
                 == record_digest({key: original_checkpoint.get(key) for key in immutable}),
                 "Rollover changed its original checkpoint identity")
        original_ids = original_checkpoint.get("command_ids")
        current_ids = checkpoint.get("command_ids")
        _require(isinstance(original_ids, list) and isinstance(current_ids, list)
                 and current_ids[:len(original_ids)] == original_ids
                 and record["run"]["revision"] >= original["run"]["revision"],
                 "Rollover lost its retained command lineage or Work revision")
        # Reconstruct only the frozen command view. Actual current human decisions
        # and receipts are validated separately by the current checkpoint below.
        retained = {**snapshot, "intents": dict(snapshot["intents"]),
                    "decisions": copy.deepcopy(snapshot["decisions"])}
        for key, command in tuple(retained["intents"].items()):
            if key.startswith("curation.control.") and command.get("target", {}).get(
                "work_run_id"
            ) == record["run"]["work_run_id"]:
                del retained["intents"][key]
            elif command.get("work_binding", {}).get("work_run_id") == record["run"]["work_run_id"]:
                # Later authorized successor effects were absent at preparation.
                del retained["intents"][key]
        for command_id, command in intent["command_records"].items():
            _require(isinstance(command, dict) and command.get("command_id") == command_id,
                     "Rollover retained command identity changed")
            retained["intents"][_command_key(command_id)] = command
            request_id = command["request"]["request_id"]
            decision = retained["decisions"].get(request_id)
            _require(isinstance(decision, dict), "Rollover lost its original human decision")
            decision["pending_apply"] = command["state"] != "applied"
        state, pending, commands = self._rollover_state(retained, original, retained=True)
        _require(record_digest({"state": state, "pending": pending, "commands": commands})
                 == record_digest({"state": handoff["workflow_state"],
                                   "pending": handoff["pending_commands"],
                                   "commands": intent["command_records"]}),
                 "Rollover retained Workflow state or commands changed")
        self._checkpoint(snapshot, record)
        if intent["state"] == "prepared":
            _require(record_digest(record) == handoff["work_record_digest"],
                     "Prepared rollover Work snapshot changed")
        if intent["state"] == "adopted":
            _require(isinstance(intent["temporal_run_id"], str)
                     and 0 < len(intent["temporal_run_id"]) <= 256
                     and intent["temporal_run_id"] != handoff["source_temporal_run_id"],
                     "Rollover replacement native Run changed")
        return envelope

    def prepare_rollover(self, work_run_id, *, temporal_run_id, workflow_state):
        self.exops._current_binding()
        app = self.exops.application
        with app.journal.writer() as writer:
            record = self._record(writer.value, work_run_id)
            state, pending, commands = self._rollover_state(writer.value, record)
            _require(isinstance(workflow_state, dict) and set(workflow_state) == {"status", "target", "commands"}
                     and record_digest(workflow_state) == record_digest(state),
                     "Rollover Workflow state differs from the canonical checkpoint")
            _require(isinstance(temporal_run_id, str) and 0 < len(temporal_run_id) <= 256
                     and record["curation_control_checkpoint"]["run_target_temporal_id"] == temporal_run_id,
                     "Rollover source differs from the original native Run")
            key = _rollover_key(work_run_id)
            previous = writer.value["intents"].get(key)
            if previous is not None:
                envelope = self._retained_rollover(writer.value, record, previous)
                _require(previous["state"] == "prepared"
                         and envelope["rollover"]["work_record_digest"] == record_digest(record),
                         "Rollover is already adopted or its prepared Work changed")
                return copy.deepcopy(envelope)
            handoff = {
                "source_temporal_run_id": temporal_run_id, "workflow_state": state,
                "work_record_digest": record_digest(record), "pending_commands": pending,
            }
            handoff["handoff_digest"] = record_digest(handoff)
            envelope = {"dispatch": self._dispatch(record), "rollover": handoff}
            writer.value["intents"][key] = {
                "state": "prepared", "envelope": copy.deepcopy(envelope),
                "work_record": copy.deepcopy(record), "command_records": commands,
            }
            writer.commit(app.clock())
            return copy.deepcopy(envelope)

    def adopt_rollover(self, envelope, *, temporal_run_id, previous_temporal_run_id):
        self.exops._current_binding()
        _require(isinstance(envelope, dict) and set(envelope) == {"dispatch", "rollover"}
                 and isinstance(envelope["dispatch"], dict)
                 and set(envelope["dispatch"]) == CURATION_DISPATCH_FIELDS
                 and isinstance(envelope["dispatch"].get("work_run_id"), str),
                 "Rollover adoption requires the exact prepared envelope")
        app = self.exops.application
        with app.journal.writer() as writer:
            work_run_id = envelope["dispatch"]["work_run_id"]
            record = self._record(writer.value, work_run_id)
            intent = writer.value["intents"].get(_rollover_key(work_run_id))
            retained = self._retained_rollover(writer.value, record, intent)
            source = retained["rollover"]["source_temporal_run_id"]
            _require(record_digest(envelope) == record_digest(retained)
                     and previous_temporal_run_id == source
                     and isinstance(temporal_run_id, str) and 0 < len(temporal_run_id) <= 256
                     and temporal_run_id != source, "Rollover adoption changed its envelope or native lineage")
            if intent["state"] == "adopted":
                _require(intent["temporal_run_id"] == temporal_run_id,
                         "Rollover already belongs to another replacement Run")
                return copy.deepcopy(retained["rollover"]["workflow_state"])
            _require(record_digest(record) == retained["rollover"]["work_record_digest"],
                     "Prepared rollover Work snapshot changed")
            state, pending, commands = self._rollover_state(writer.value, record)
            _require(record_digest({"state": state, "pending": pending, "commands": commands})
                     == record_digest({"state": retained["rollover"]["workflow_state"],
                                       "pending": retained["rollover"]["pending_commands"],
                                       "commands": intent["command_records"]}),
                     "Prepared rollover commands changed")
            intent.update(state="adopted", temporal_run_id=temporal_run_id)
            writer.commit(app.clock())
            return copy.deepcopy(state)

    def _authorize_native_run(self, snapshot, record, temporal_run_id, *, checkpoint=True):
        _require(isinstance(temporal_run_id, str) and 0 < len(temporal_run_id) <= 256,
                 "Native run identity is unavailable")
        intent = snapshot["intents"].get(_rollover_key(record["run"]["work_run_id"]))
        if intent is None:
            original = record.get("curation_control_checkpoint", {}).get(
                "run_target_temporal_id", record.get("temporal_run_id"),
            )
            _require(original == temporal_run_id, "Work belongs to another native Run")
            if checkpoint:
                if "curation_control_checkpoint" in record:
                    self._checkpoint(snapshot, record)
                else:
                    self._authority(record)
        else:
            self._retained_rollover(snapshot, record, intent)
            _require(intent["state"] == "adopted" and intent["temporal_run_id"] == temporal_run_id,
                     "Native Run is fenced by the rollover handoff")
        return copy.deepcopy(record)

    def authorize_native_run(self, work_run_id, *, temporal_run_id):
        self.exops._current_binding()
        snapshot = self.exops.application.journal.read()
        return self._authorize_native_run(snapshot, self._record(snapshot, work_run_id), temporal_run_id)

    def open_checkpoint(self, work_run_id, *, temporal_run_id):
        _require(isinstance(temporal_run_id, str) and 0 < len(temporal_run_id) <= 256,
                 "Native run identity is unavailable")
        app = self.exops.application
        with app.journal.writer() as writer:
            record = self._record(writer.value, work_run_id)
            existing = record.get("curation_control_checkpoint")
            if existing is not None:
                self._checkpoint(writer.value, record)
                _require(existing["run_target_temporal_id"] == temporal_run_id,
                         "Checkpoint belongs to another native run")
                return copy.deepcopy(existing["target"])
            self._authority(record)
            self._quiescent(writer.value, record)
            run = record["run"]
            _require(record["application_state"] == "started" and run["state"] == "running"
                     and not run["pending_action_ids"], "Checkpoint is not an active closed Work")
            self._initial(record, run)
            _require(record.get("temporal_run_id", temporal_run_id) == temporal_run_id,
                     "Checkpoint differs from known native run")
            original = copy.deepcopy(run)
            run = {**copy.deepcopy(run), "revision": run["revision"] + 1,
                   "control_revision": run["control_revision"] + 1, "state": "awaiting_human"}
            self.exops.contracts.run(run, record["spec"], previous=original)
            record["run"] = run
            checkpoint = {
                "run": copy.deepcopy(run), "initial_run": original,
                "work_record_digest": record_digest(record),
                "initial_record": copy.deepcopy(record),
                "run_target_temporal_id": temporal_run_id, "command_ids": [],
                "runner_binding": copy.deepcopy(self.executor.runner_binding),
                "control_policy": copy.deepcopy(app.policy),
            }
            target = self._target(checkpoint, run)
            checkpoint["target"] = target
            record["curation_control_checkpoint"] = checkpoint
            self._checkpoint(writer.value, record)
            writer.commit(app.clock())
            return copy.deepcopy(target)

    def target(self, work_run_id, *, actor):
        record = self._record(self.exops.application.journal.read(), work_run_id)
        self._reader(record, actor)
        checkpoint = record.get("curation_control_checkpoint")
        _require(isinstance(checkpoint, dict), "Native checkpoint is not open")
        _require(record["run"]["state"] in {"awaiting_human", "paused"}
                 and record["run"]["control_revision"] == checkpoint["target"]["target_control_revision"],
                 "Native checkpoint is no longer available for control")
        return copy.deepcopy(checkpoint["target"])

    def _build(self, record, actor, command_id, kind, target, expires_at, now):
        run, spec = record["run"], record["spec"]
        suffix = record_digest({"command_id": command_id})[7:39]
        context = {key: run[key] for key in (
            "owner_operation_id", "work_id", "work_run_id", "work_spec_revision", "work_spec_digest",
        )}
        directive = {
            **context, **{key: run[key] for key in (
                "core_version", "core_digest", "semantic_version", "semantic_digest", "schema_version",
                "generation", "fencing_token",
            )}, "directive_id": command_id, "actor_id": actor.actor_id, "kind": kind,
            "target_control_revision": run["control_revision"],
            "human_request_id": "request.curation.control." + suffix,
            "human_response_id": "response.curation.control." + suffix,
            "decision_receipt_id": "decision.curation.control." + suffix,
            "effect_id": "effect.curation.control." + suffix,
            "received_at": stamp(now), "expires_at": stamp(expires_at), "application_state": "received",
        }
        binding = {
            "action_id": command_id, "target_revision": "revision." + str(run["control_revision"]),
            "operative_package_version": spec["method_contract_ref"]["method_version"],
            "operative_package_digest": spec["method_contract_ref"]["content_digest"],
            "mapping_id": "knowledgeos.curation.control", "mapping_version": "1.0.0",
            "mapping_digest": self.exops.teams[spec["team_id"]].native_binding_digest,
            "work_binding": work_control_binding(directive),
        }
        target_digest = _target_digest(binding, run["semantic_version"], run["semantic_digest"])
        human_context = {key: directive[key] for key in (
            "owner_operation_id", "work_run_id", "work_spec_revision", "work_spec_digest", "target_control_revision",
        )}
        request = {
            **human_context, "request_id": directive["human_request_id"], "request_revision": 1,
            "question": kind.capitalize() + " this exact WorkRun at its closed initial boundary?",
            "impact": "Preserves evidence and budgets; only RESUME authorizes the immutable successor.",
            "allowed_actions": [kind], "evidence_reference_ids": [],
            "expires_at": stamp(expires_at), "lifecycle": "resolved",
            "semantic_version": run["semantic_version"], "semantic_digest": run["semantic_digest"],
            "target_digest": target_digest,
        }
        response = {
            **human_context, "request_id": request["request_id"], "request_revision": 1,
            "response_id": directive["human_response_id"], "actor_id": actor.actor_id,
            "chosen_action": kind, "idempotency_key": command_id, "received_at": stamp(now),
            "submission_state": "submitted", "target_digest": target_digest,
        }
        decision = {
            "receipt_id": directive["decision_receipt_id"], "owner_operation_id": "knowledgeos",
            "request_id": request["request_id"], "request_revision": 1,
            "decision_state": "accepted", "chosen_action": kind,
        }
        directive["decision_target_digest"] = target_digest
        directive["directive_digest"] = directive_digest(directive)
        intent = {
            "owner_operation_id": "knowledgeos", "effect_id": directive["effect_id"],
            "content_digest": directive["directive_digest"], "generation": run["generation"],
            "fencing_token": run["fencing_token"], "work_run_id": run["work_run_id"],
            "issued_at": stamp(now), "expires_at": stamp(expires_at),
        }
        return {"directive": directive, "request": request, "response": response, "decision": decision,
                "target_binding": binding, "execution_intent": intent}

    def _steering(self, command, spec, run, now, **kwargs):
        self.exops.contracts.steering(
            command["directive"], spec, run, request=command["request"], response=command["response"],
            decision=command["decision"], target_binding=command["target_binding"],
            intent=command["execution_intent"], candidate_actor_id=command["actor_id"],
            now=now, artifacts={}, **kwargs,
        )

    def request_control(self, work_run_id, *, kind, actor, command_id, target, expires_at):
        _require(isinstance(kind, str) and kind in {"cancel", "pause", "resume"},
                 "Unsupported bounded control kind")
        _require(isinstance(command_id, str) and len(command_id) <= 96
                 and re.fullmatch(r"[a-z][a-z0-9_-]*(?:\.[a-z0-9][a-z0-9_-]*)*", command_id),
                 "Stable control command identity is invalid")
        _require(isinstance(target, Mapping), "Exact checkpoint target is required")
        _require(isinstance(expires_at, datetime) and expires_at.tzinfo is not None,
                 "Control expiry must be timezone aware")
        app = self.exops.application
        body = {"work_run_id": work_run_id, "command_id": command_id, "kind": kind, "target": dict(target),
                "expires_at": stamp(expires_at), "actor_id": actor.actor_id, "actor_ingress": actor.ingress}
        with app.journal.writer() as writer:
            record = self._record(writer.value, work_run_id)
            self._command_actor(record, actor)
            previous = writer.value["intents"].get(_command_key(command_id))
            if previous is not None:
                _require(record_digest(previous.get("body", {})) == record_digest(body),
                         "Command identity has conflicting content or ingress")
                return self._validate_command(writer.value, record, previous, now=app.clock(), allow_rejected=True)
            rollover = writer.value["intents"].get(_rollover_key(work_run_id))
            if rollover is not None:
                self._retained_rollover(writer.value, record, rollover)
                _require(rollover["state"] == "adopted", "Control admission is frozen during rollover")
            checkpoint, _last = self._checkpoint(writer.value, record)
            _require(_exact(target, checkpoint["target"]), "Command target is stale or foreign")
            now = app.clock()
            _require(now < expires_at and (expires_at - now).total_seconds() <= 86400,
                     "Command requires bounded future expiry")
            _require(not record["run"]["pending_action_ids"]
                     and ((record["run"]["state"] == "awaiting_human" and kind in {"pause", "cancel"})
                          or (record["run"]["state"] == "paused" and kind in {"resume", "cancel"})),
                     "Command is not permitted at the current bounded checkpoint")
            _require(not any(value.get("action_id") == command_id
                             or value.get("effect_id") == command_id
                             for value in writer.value["intents"].values()), "Command identity is already owned")
            command = {
                "state": "received", "command_id": command_id, "body": body,
                "actor_id": actor.actor_id, "actor_ingress": actor.ingress,
                "target": copy.deepcopy(dict(target)),
                **self._build(record, actor, command_id, kind, target, expires_at, now),
            }
            self._steering(command, record["spec"], record["run"], now)
            pending = {**copy.deepcopy(record["run"]), "revision": record["run"]["revision"] + 1,
                       "pending_action_ids": [command_id]}
            self.exops.contracts.run(pending, record["spec"], previous=record["run"])
            command["received_run"] = copy.deepcopy(pending)
            command["receipt"] = {
                "owner_operation_id": "knowledgeos", "work_run_id": work_run_id,
                "command_id": command_id, "kind": kind, "receipt_state": "accepted",
                "application_state": "received", "target_control_revision": target["target_control_revision"],
                "expires_at": stamp(expires_at),
            }
            for collection, key, value in (
                ("intents", _command_key(command_id), command),
                ("human_requests", command["request"]["request_id"], command["request"]),
                ("decisions", command["request"]["request_id"], {
                    "response": command["response"], "receipt": command["decision"],
                    "reason_digest": None, "recorded_at": stamp(now), "pending_apply": True,
                    "target_digest": command["request"]["target_digest"],
                }),
            ):
                _require(key not in writer.value[collection], "Control receipt identity already exists")
                writer.value[collection][key] = copy.deepcopy(value)
            record["run"] = pending
            checkpoint["command_ids"].append(command_id)
            writer.value["history"].append({"event": "curation_control_received", "command_id": command_id,
                                             "work_run_id": work_run_id, "sequence": writer.value["revision"] + 1})
            writer.commit(now)
            return copy.deepcopy(command["receipt"])

    def read_command(self, work_run_id, command_id, *, actor):
        snapshot = self.exops.application.journal.read()
        record = self._record(snapshot, work_run_id)
        self._reader(record, actor)
        command = snapshot["intents"].get(_command_key(command_id))
        _require(isinstance(command, dict) and command["target"]["work_run_id"] == work_run_id
                 and command["actor_id"] == actor.actor_id, "Control command is unavailable")
        return copy.deepcopy(command["receipt"])

    def _validate_command(self, snapshot, record, command, *, now, allow_rejected=False):
        self._command_actor(record, OwnerActor(command["actor_id"], command["actor_ingress"]))
        self._checkpoint(snapshot, record, allow_rejected=allow_rejected or command["state"] == "applied")
        return copy.deepcopy(command["receipt"])

    def _validate_snapshot(self, snapshot, record, command, prior_run, *, allow_rejected=False):
        now = self.exops.application.clock()
        self._command_actor(record, OwnerActor(command["actor_id"], command["actor_ingress"]))
        kind = command["body"]["kind"]
        _require(command["body"] == {
            "work_run_id": command["target"]["work_run_id"], "command_id": command["command_id"],
            "kind": kind, "target": command["target"], "expires_at": command["directive"]["expires_at"],
            "actor_id": command["actor_id"], "actor_ingress": command["actor_ingress"],
        }, "Canonical command body changed")
        original = {**record, "run": prior_run}
        expected = self._build(
            original, OwnerActor(command["actor_id"], command["actor_ingress"]),
            command["command_id"], kind, command["target"],
            datetime.fromisoformat(command["body"]["expires_at"]),
            datetime.fromisoformat(command["directive"]["received_at"]),
        )
        received_directive = {key: value for key, value in command["directive"].items()
                              if key not in {"applied_at", "applied_run_revision", "execution_receipt_id"}}
        received_directive["application_state"] = "received"
        _require(received_directive == expected["directive"]
                 and all(command[key] == value for key, value in expected.items() if key != "directive"),
                 "Canonical human action, steering intent or binding changed")
        public = {
            "owner_operation_id": "knowledgeos", "work_run_id": command["target"]["work_run_id"],
            "command_id": command["command_id"], "kind": kind, "receipt_state": "accepted",
            "application_state": command["state"],
            "target_control_revision": command["target"]["target_control_revision"],
            "expires_at": command["body"]["expires_at"],
        }
        if command["state"] == "applied":
            public.update(applied_run_revision=command["applied_run"]["revision"],
                          execution_receipt_id=command["directive"]["execution_receipt_id"])
            if kind == "pause":
                public["next_target"] = self._target(record["curation_control_checkpoint"], command["applied_run"])
        _require(command["receipt"] == public, "Canonical public receipt changed")
        request_id = command["request"]["request_id"]
        decision = snapshot["decisions"].get(request_id, {})
        _require(snapshot["human_requests"].get(request_id) == command["request"]
                 and decision.get("response") == command["response"]
                 and decision.get("receipt") == command["decision"]
                 and decision.get("target_digest") == command["request"]["target_digest"]
                 and decision.get("pending_apply") is (command["state"] != "applied"),
                 "Durable human decision changed")
        _require(command["receipt"]["application_state"] == command["state"], "Public receipt changed")
        if command["state"] == "applied":
            execution = snapshot["receipts"].get(_control_receipt_key(command["command_id"]), {})
            _require(execution.get("receipt") == command["execution_receipt"]
                     and execution.get("unresolved") is False
                     and command["execution_receipt"]["result_digest"] == record_digest(command["applied_run"]),
                     "Applied command has no canonical successful receipt")
            if kind == "cancel":
                work_execution = snapshot["receipts"].get(_work_key(record["action_id"]), {})
                _require(work_execution.get("receipt") == command.get("work_execution_receipt")
                         and work_execution.get("unresolved") is False
                         and command["work_execution_receipt"]["result_digest"] == record_digest(command["applied_run"]),
                         "CANCEL lost its terminal Work receipt")
            else:
                _require("work_execution_receipt" not in command,
                         "Nonterminal control cannot finalize the Work receipt")
            self._steering(command, record["spec"], command["received_run"], now,
                           receipt=command["execution_receipt"],
                           receipt_reference_id=command["receipt"]["execution_receipt_id"],
                           applied_run=command["applied_run"])
        elif allow_rejected and command["state"] in {"expired", "rejected"}:
            self.exops.contracts.record("SteeringDirective", command["directive"])
        else:
            _require(command["state"] == "received", "Command was rejected or expired")
            self._steering(command, record["spec"], command["received_run"],
                           datetime.fromisoformat(command["directive"]["received_at"]) if allow_rejected else now)
        return copy.deepcopy(command["receipt"])

    def authorize_dispatch(self, payload):
        _require(isinstance(payload, dict) and set(payload) == {"command_id", "kind", "target"}
                 and isinstance(payload["command_id"], str) and isinstance(payload["kind"], str)
                 and payload["kind"] in {"cancel", "pause", "resume"}
                 and isinstance(payload["target"], dict),
                 "Native command payload must be exact owner dispatch")
        snapshot = self.exops.application.journal.read()
        command = snapshot["intents"].get(_command_key(payload["command_id"]))
        _require(isinstance(command, dict) and payload["kind"] == command["body"]["kind"]
                 and _exact(payload["target"], command["target"]),
                 "Native command has no exact owner intent")
        record = self._record(snapshot, command["target"]["work_run_id"])
        return self._validate_command(snapshot, record, command, now=self.exops.application.clock())

    def apply_control(self, work_run_id, command_id, *, temporal_run_id):
        self.exops._current_binding()
        app = self.exops.application
        failure = None
        result = None
        with app.journal.writer() as writer:
            command = writer.value["intents"].get(_command_key(command_id))
            _require(isinstance(command, dict) and command["target"]["work_run_id"] == work_run_id,
                     "Native application differs from exact owner command")
            record = self._record(writer.value, work_run_id)
            # Transport fencing precedes rejection handling: maintenance and
            # read-only denials must never turn a received command into rejection.
            self._authorize_native_run(writer.value, record, temporal_run_id, checkpoint=False)
            now = app.clock()
            try:
                result = self._validate_command(writer.value, record, command, now=now)
            except AdmissionError as error:
                # Keep the pending Work fence. A rejected command never authorizes
                # a successor or silently reconciles potentially changed evidence.
                if command["state"] == "received":
                    state = "expired" if now >= datetime.fromisoformat(command["directive"]["expires_at"]) else "rejected"
                    command["state"] = command["directive"]["application_state"] = state
                    command["receipt"]["application_state"] = state
                    self.exops.contracts.record("SteeringDirective", command["directive"])
                    writer.commit(now)
                failure = error
            if failure is None and command["state"] != "applied":
                previous = record["run"]
                kind = command["body"]["kind"]
                applied = {**copy.deepcopy(previous), "revision": previous["revision"] + 1,
                           "control_revision": previous["control_revision"] + 1,
                           "state": {"cancel": "cancelled", "pause": "paused", "resume": "running"}[kind],
                           "pending_action_ids": []}
                self.exops.contracts.run(applied, record["spec"], previous=previous)
                receipt_id = "receipt.curation.control." + record_digest({"command_id": command_id})[7:39]
                execution = {
                    **{key: command["execution_intent"][key] for key in (
                        "owner_operation_id", "effect_id", "content_digest", "generation", "fencing_token", "work_run_id",
                    )}, "outcome": "succeeded", "observed_at": stamp(now), "result_digest": record_digest(applied),
                }
                directive = {**command["directive"], "application_state": "applied", "applied_at": stamp(now),
                             "applied_run_revision": applied["revision"], "execution_receipt_id": receipt_id}
                candidate = {**command, "directive": directive}
                self._steering(candidate, record["spec"], previous, now, receipt=execution,
                               receipt_reference_id=receipt_id, applied_run=applied)
                record["run"] = applied
                command.update(state="applied", directive=directive, execution_receipt=execution,
                               applied_run=copy.deepcopy(applied))
                command["receipt"].update(application_state="applied", applied_run_revision=applied["revision"],
                                          execution_receipt_id=receipt_id)
                if kind == "pause":
                    next_target = self._target(record["curation_control_checkpoint"], applied)
                    record["curation_control_checkpoint"]["target"] = copy.deepcopy(next_target)
                    command["receipt"]["next_target"] = next_target
                if kind == "cancel":
                    work_execution = {
                        **{key: record["execution_intent"][key] for key in (
                            "owner_operation_id", "effect_id", "content_digest", "generation", "fencing_token", "work_run_id",
                        )}, "outcome": "succeeded", "observed_at": stamp(now), "result_digest": record_digest(applied),
                    }
                    self.exops.contracts.record("ExecutionReceipt", work_execution)
                    record.update(state="recorded", application_state="recorded", continuation_state="cancelled")
                    record["receipt"]["application_state"] = "recorded"
                    command["work_execution_receipt"] = work_execution
                    writer.value["receipts"][_work_key(record["action_id"])] = {
                        "recorded_at": stamp(now), "unresolved": False, "receipt": work_execution,
                    }
                writer.value["decisions"][command["request"]["request_id"]]["pending_apply"] = False
                writer.value["receipts"][_control_receipt_key(command_id)] = {
                    "recorded_at": stamp(now), "unresolved": False, "receipt": execution,
                }
                writer.value["history"].append({"event": "curation_control_applied", "command_id": command_id,
                                                 "work_run_id": work_run_id, "sequence": writer.value["revision"] + 1})
                writer.commit(now)
                result = copy.deepcopy(command["receipt"])
        if failure is not None:
            raise failure
        return result

"""Rollover owner handoffs against the real disposable journal and Work fixtures."""

from __future__ import annotations

import copy
from dataclasses import replace
from datetime import timedelta

import pytest
import test_curation_control as control

from vaultops.adapters.core import AdmissionError
from vaultops.application.work import record_digest

REPLACEMENT = "native-control-replacement"


def workflow_state(controller, target):
    snapshot = controller.exops.application.journal.read()
    record = control.saved(controller.exops, target)
    commands = {}
    for command_id in record["curation_control_checkpoint"]["command_ids"]:
        command = snapshot["intents"]["curation.control." + command_id]
        if command["state"] == "applied":
            commands[command_id] = {
                "payload": control.payload(command["target"], kind=command["body"]["kind"], command_id=command_id),
                "receipt": command["receipt"],
            }
    return {"status": record["run"]["state"], "target": target, "commands": commands}


def prepare(controller, target, state=None):
    return controller.prepare_rollover(
        target["work_run_id"], temporal_run_id=control.NATIVE_RUN,
        workflow_state=state if state is not None else workflow_state(controller, target),
    )


def adopt(controller, envelope, *, native=REPLACEMENT, previous=control.NATIVE_RUN):
    return controller.adopt_rollover(envelope, temporal_run_id=native, previous_temporal_run_id=previous)


def native_apply(controller, target, command_id):
    return controller.apply_control(target["work_run_id"], command_id, temporal_run_id=REPLACEMENT)


@pytest.mark.parametrize("paused,pending", [(False, False), (False, True), (True, False), (True, True)])
def test_handoff_preserves_full_work_and_pending_commands_without_consuming_budgets(
    tmp_path, monkeypatch, paused, pending,
):
    controller, target, exops, executor, _spec, run, path = control.checkpoint_ready(tmp_path, monkeypatch)
    if paused:
        target, _receipt = control.pause(controller, target)
    command_id, kind = (control.RESUME, "resume") if paused else (control.PAUSE, "pause")
    if pending:
        control.receive(controller, target, command_id=command_id, kind=kind)
    original = copy.deepcopy(control.saved(exops, target))
    source = path.read_bytes()
    state = copy.deepcopy(workflow_state(controller, target))
    envelope = prepare(controller, target, state)
    assert set(envelope) == {"dispatch", "rollover"}
    handoff = envelope["rollover"]
    assert handoff["workflow_state"] == state
    assert handoff["work_record_digest"] == record_digest(original)
    assert handoff["handoff_digest"] == record_digest(handoff, ("handoff_digest",))
    assert handoff["pending_commands"] == ([control.payload(target, kind=kind, command_id=command_id)] if pending else [])
    assert control.saved(exops, target) == original
    prepared = exops.application.journal.read()
    assert prepare(controller, target, state) == envelope
    assert exops.application.journal.read() == prepared
    for native in (control.NATIVE_RUN, REPLACEMENT, "native.foreign"):
        with pytest.raises(AdmissionError):
            controller.authorize_native_run(run["work_run_id"], temporal_run_id=native)
    if pending:
        assert controller.authorize_dispatch(control.payload(target, kind=kind, command_id=command_id))["application_state"] == "received"
        with pytest.raises(AdmissionError):
            control.apply(controller, target, command_id)
    with pytest.raises(AdmissionError):
        control.receive(controller, target, command_id="command.frozen", kind=kind)
    assert exops.application.journal.read() == prepared
    assert adopt(controller, envelope) == state
    adopted = exops.application.journal.read()
    assert adopt(controller, envelope) == state
    assert exops.application.journal.read() == adopted
    assert control.saved(exops, target) == original
    assert controller.authorize_native_run(run["work_run_id"], temporal_run_id=REPLACEMENT) == original
    for native in (control.NATIVE_RUN, "native.foreign"):
        with pytest.raises(AdmissionError):
            controller.authorize_native_run(run["work_run_id"], temporal_run_id=native)
    with pytest.raises(AdmissionError):
        prepare(controller, target, state)
    with pytest.raises(AdmissionError):
        adopt(controller, envelope, native="native.second")
    assert exops.application.journal.read() == adopted
    assert executor.runner.requests == [] and path.read_bytes() == source
    # Callers own returned objects; mutating one cannot corrupt owner retention.
    envelope["rollover"]["workflow_state"]["status"] = "forged"
    state["target"]["generation"] += 1
    assert exops.application.journal.read() == adopted


def test_paused_cached_receipt_pending_resume_and_completed_history_survive_rollover(tmp_path, monkeypatch):
    controller, original_target, exops, executor, _spec, run, _path = control.checkpoint_ready(tmp_path, monkeypatch)
    target, pause_receipt = control.pause(controller, original_target)
    control.receive(controller, target, kind="resume", command_id=control.RESUME)
    envelope = prepare(controller, target)
    state = adopt(controller, envelope)
    assert state["commands"][control.PAUSE] == {
        "payload": control.payload(original_target, kind="pause", command_id=control.PAUSE),
        "receipt": pause_receipt,
    }
    before = exops.application.journal.read()
    assert native_apply(controller, original_target, control.PAUSE) == pause_receipt
    assert controller.authorize_dispatch(state["commands"][control.PAUSE]["payload"]) == pause_receipt
    assert exops.application.journal.read() == before
    resume_receipt = native_apply(controller, target, control.RESUME)
    assert controller.authorize_successor(run["work_run_id"], temporal_run_id=REPLACEMENT) == resume_receipt
    with pytest.raises(AdmissionError):
        controller.authorize_successor(run["work_run_id"], temporal_run_id=control.NATIVE_RUN)
    control.finish_successor(exops, executor, target)
    completed = control.saved(exops, target)
    assert completed["run"]["state"] == "completed"
    assert completed["run"]["profile_invocations"] == completed["run"]["segments_started"] == 2
    assert completed["run"]["repeated_gap_count"] == 1
    # Original pending expiry must not invalidate a subsequently applied historical handoff.
    now = exops.application.clock()
    exops.application = replace(exops.application, clock=lambda: now + timedelta(days=2))
    before = exops.application.journal.read()
    assert adopt(controller, envelope) == state
    assert native_apply(controller, original_target, control.PAUSE) == pause_receipt
    assert native_apply(controller, target, control.RESUME) == resume_receipt
    assert controller.authorize_native_run(run["work_run_id"], temporal_run_id=REPLACEMENT) == completed
    assert exops.application.journal.read() == before


def test_new_commands_continue_after_adoption_and_keep_original_checkpoint_identity(tmp_path, monkeypatch):
    controller, target, exops, _executor, _spec, _run, _path = control.checkpoint_ready(tmp_path, monkeypatch)
    envelope = prepare(controller, target)
    adopt(controller, envelope)
    control.receive(controller, target, kind="pause", command_id=control.PAUSE)
    pause_receipt = native_apply(controller, target, control.PAUSE)
    paused_target = pause_receipt["next_target"]
    assert paused_target["temporal_run_id"] == target["temporal_run_id"] == control.NATIVE_RUN
    control.receive(controller, paused_target, kind="resume", command_id=control.RESUME)
    resume_receipt = native_apply(controller, paused_target, control.RESUME)
    assert controller.authorize_successor(target["work_run_id"], temporal_run_id=REPLACEMENT) == resume_receipt
    assert control.saved(exops, target)["curation_control_checkpoint"]["initial_record"] == envelope_record(exops, target)["curation_control_checkpoint"]["initial_record"]


def envelope_record(exops, target):
    return exops.application.journal.read()["intents"]["curation.rollover." + target["work_run_id"]]["work_record"]


@pytest.mark.parametrize("change", ["extra", "status", "target", "typed_target", "commands", "receipt", "pending_cache"])
def test_prepare_rejects_noncanonical_workflow_state_without_writes(tmp_path, monkeypatch, change):
    controller, target, exops, _executor, _spec, _run, _path = control.checkpoint_ready(tmp_path, monkeypatch)
    target, _receipt = control.pause(controller, target)
    control.receive(controller, target, kind="resume", command_id=control.RESUME)
    state = copy.deepcopy(workflow_state(controller, target))
    if change == "extra":
        state["applying"] = False
    elif change == "status":
        state["status"] = "running"
    elif change == "target":
        state["target"]["temporal_run_id"] = REPLACEMENT
    elif change == "typed_target":
        state["target"]["generation"] = float(state["target"]["generation"])
    elif change == "commands":
        state["commands"] = {}
    elif change == "receipt":
        state["commands"][control.PAUSE]["receipt"]["applied_run_revision"] += 1
    else:
        state["commands"][control.RESUME] = {"payload": control.payload(target, kind="resume", command_id=control.RESUME), "receipt": {}}
    before = exops.application.journal.read()
    with pytest.raises(AdmissionError):
        prepare(controller, target, state)
    assert exops.application.journal.read() == before


@pytest.mark.parametrize("change", ["envelope", "digest", "pending", "previous", "same_run", "missing_prepare"])
def test_adoption_rejects_forged_or_foreign_handoff_without_writes(tmp_path, monkeypatch, change):
    controller, target, exops, _executor, _spec, _run, _path = control.checkpoint_ready(tmp_path, monkeypatch)
    envelope = prepare(controller, target)
    args = {}
    if change == "envelope":
        envelope["dispatch"]["action_id"] = "action.foreign"
    elif change == "digest":
        envelope["rollover"]["handoff_digest"] = "sha256:" + "f" * 64
    elif change == "pending":
        envelope["rollover"]["pending_commands"] = [control.payload(target)]
        envelope["rollover"]["handoff_digest"] = record_digest(envelope["rollover"], ("handoff_digest",))
    elif change == "previous":
        args["previous"] = "native.foreign"
    elif change == "same_run":
        args["native"] = control.NATIVE_RUN
    else:
        with exops.application.journal.writer() as writer:
            del writer.value["intents"]["curation.rollover." + target["work_run_id"]]
            writer.commit(exops.application.clock())
    before = exops.application.journal.read()
    with pytest.raises(AdmissionError):
        adopt(controller, envelope, **args)
    assert exops.application.journal.read() == before


@pytest.mark.parametrize("phase", ["prepare", "adopt", "authorize"])
@pytest.mark.parametrize("change", ["evidence", "budget", "pins", "pending", "director", "actor", "control_actor", "source", "runner", "readonly"])
def test_every_handoff_boundary_rechecks_evidence_counters_pins_and_authority(
    tmp_path, monkeypatch, phase, change,
):
    controller, target, exops, executor, _spec, run, path = control.checkpoint_ready(tmp_path, monkeypatch)
    control.receive(controller, target, kind="pause", command_id=control.PAUSE)
    state = workflow_state(controller, target)
    envelope = prepare(controller, target) if phase != "prepare" else None
    if phase == "authorize":
        adopt(controller, envelope)
    if change == "director":
        exops.director_admitted = lambda _spec: False
    elif change in {"actor", "control_actor"}:
        denied = "submit_work" if change == "actor" else "control_work"
        exops.authorize_actor = lambda _actor, action: action != denied
    elif change == "source":
        path.write_text(path.read_text() + "\nChanged during rollover.\n")
    elif change == "runner":
        executor.runner.binding = {**executor.runner.binding, "adapter_digest": "sha256:" + "f" * 64}
    elif change == "readonly":
        exops.read_only = True
    else:
        def alter(record, snapshot):
            if change == "evidence":
                record["curation_capture_evidence"]["reads"][0]["data"]["text"] += " forged"
            elif change == "budget":
                record["run"]["profile_invocations"] = 0
            elif change == "pins":
                record["run"]["generation"] += 1
            else:
                snapshot["intents"]["curation.control." + control.PAUSE]["body"]["kind"] = "cancel"
        control.mutate(exops, target, alter)
    before = exops.application.journal.read()
    with pytest.raises(AdmissionError):
        if phase == "prepare":
            prepare(controller, target, state)
        elif phase == "adopt":
            adopt(controller, envelope)
        else:
            controller.authorize_native_run(run["work_run_id"], temporal_run_id=REPLACEMENT)
    if phase != "prepare":
        with pytest.raises(AdmissionError):
            controller.apply_control(run["work_run_id"], control.PAUSE, temporal_run_id=REPLACEMENT)
    assert exops.application.journal.read() == before


@pytest.mark.parametrize("change", ["envelope", "record", "commands", "source", "replacement"])
def test_retained_owner_handoff_integrity_is_rechecked_after_adoption(tmp_path, monkeypatch, change):
    controller, target, exops, _executor, _spec, run, _path = control.checkpoint_ready(tmp_path, monkeypatch)
    target, _receipt = control.pause(controller, target)
    envelope = prepare(controller, target)
    adopt(controller, envelope)
    with exops.application.journal.writer() as writer:
        intent = writer.value["intents"]["curation.rollover." + run["work_run_id"]]
        if change == "envelope":
            intent["envelope"]["rollover"]["workflow_state"]["commands"] = {}
        elif change == "record":
            intent["work_record"]["run"]["profile_invocations"] = 0
        elif change == "commands":
            intent["command_records"][control.PAUSE]["receipt"]["applied_run_revision"] += 1
        elif change == "source":
            intent["envelope"]["rollover"]["source_temporal_run_id"] = "native.foreign"
        else:
            intent["temporal_run_id"] = control.NATIVE_RUN
        writer.commit(exops.application.clock())
    before = exops.application.journal.read()
    with pytest.raises(AdmissionError):
        controller.authorize_native_run(run["work_run_id"], temporal_run_id=REPLACEMENT)
    with pytest.raises(AdmissionError):
        adopt(controller, envelope)
    assert exops.application.journal.read() == before


@pytest.mark.parametrize("change", ["active", "uncertain", "effect", "model"])
def test_prepare_requires_no_active_or_uncertain_effects(tmp_path, monkeypatch, change):
    controller, target, exops, _executor, _spec, run, _path = control.checkpoint_ready(tmp_path, monkeypatch)
    state = workflow_state(controller, target)

    def alter(record, snapshot):
        if change == "active":
            record["run"]["active_graph_run_id"] = record["run"]["graph_run_ids"][0]
        elif change == "uncertain":
            record["run"]["unresolved_effect_ids"] = ["effect.uncertain"]
        elif change == "effect":
            snapshot["intents"]["effect.uncertain"] = {
                "state": "unknown", "work_binding": {"work_run_id": run["work_run_id"]},
            }
        else:
            record["curation_model_request"] = {"request_digest": "sha256:" + "f" * 64}

    control.mutate(exops, target, alter)
    before = exops.application.journal.read()
    with pytest.raises(AdmissionError):
        prepare(controller, target, state)
    assert exops.application.journal.read() == before

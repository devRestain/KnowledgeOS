"""Native control boundaries using the real owner journal and Core contracts."""

from __future__ import annotations

import asyncio
import copy
import sys
from datetime import timedelta
from types import SimpleNamespace

import pytest
from test_curation_continuation_temporal import adapter as continuation_adapter
from test_curation_control import checkpoint_ready

from vaultops.adapters.core import AdmissionError
from vaultops.application.curation_control_temporal import (
    _ControlActivities,
    deliver_control,
    reconcile_control,
)
from vaultops.application.exops import OwnerActor
from vaultops.application.temporal_contract import (
    CONTROL_APPLY_ACTIVITY,
    CONTROL_CHECKPOINT_ACTIVITY,
    CONTROL_ROLLOVER_ADOPT_ACTIVITY,
    CONTROL_ROLLOVER_PREPARE_ACTIVITY,
    CONTROL_WORKFLOW_TYPE,
    curation_control_target,
)


def native_context(monkeypatch, target, *, activity_type=CONTROL_APPLY_ACTIVITY):
    info = SimpleNamespace(
        workflow_id="knowledgeos/" + target["work_run_id"],
        workflow_type=CONTROL_WORKFLOW_TYPE, workflow_run_id=target["temporal_run_id"],
        activity_type=activity_type,
    )
    monkeypatch.setitem(sys.modules, "temporalio", SimpleNamespace(
        activity=SimpleNamespace(info=lambda: info),
    ))
    return info


def command_ready(tmp_path, monkeypatch, *, kind="cancel"):
    controller, target, exops, executor, _spec, run, _path = checkpoint_ready(tmp_path, monkeypatch)
    command_id = "command.runtime." + kind
    receipt = controller.request_control(
        run["work_run_id"], kind=kind, actor=OwnerActor("owner.user", "web"), command_id=command_id,
        target=target, expires_at=exops.application.clock() + timedelta(minutes=5),
    )
    assert receipt["application_state"] == "received"
    return controller, {"command_id": command_id, "kind": kind, "target": target}, exops, executor


@pytest.mark.parametrize("change", (
    "actor", "grant", "target_extra", "target_missing", "blank", "too_long",
    "bool_revision", "zero_generation", "blank_fence", "foreign_owner", "negative_revision",
    "missing_kind", "unknown_kind", "nonstring_kind",
))
def test_control_wire_cannot_inject_authority_or_loosen_target(tmp_path, monkeypatch, change):
    _, target, *_ = checkpoint_ready(tmp_path, monkeypatch)
    payload = {"command_id": "command.wire", "kind": "pause", "target": copy.deepcopy(target)}
    if change in {"actor", "grant"}:
        payload[change] = True
    elif change == "missing_kind":
        del payload["kind"]
    elif change == "unknown_kind":
        payload["kind"] = "cutover"
    elif change == "nonstring_kind":
        payload["kind"] = True
    elif change == "target_extra":
        payload["target"]["quiesced"] = True
    elif change == "target_missing":
        del payload["target"]["checkpoint_digest"]
    elif change == "blank":
        payload["command_id"] = ""
    elif change == "too_long":
        payload["command_id"] = "c" * 97
    elif change == "bool_revision":
        payload["target"]["target_control_revision"] = True
    elif change == "zero_generation":
        payload["target"]["generation"] = 0
    elif change == "blank_fence":
        payload["target"]["fencing_token"] = ""
    elif change == "foreign_owner":
        payload["target"]["owner_operation_id"] = "foreign"
    else:
        payload["target"]["target_control_revision"] = -1
    with pytest.raises(ValueError):
        curation_control_target(payload)


@pytest.mark.parametrize("field,value", (
    ("workflow_type", "KnowledgeOSCurationContinuationV1"),
    ("workflow_id", "knowledgeos/foreign"),
    ("workflow_run_id", "foreign-native-run"),
    ("activity_type", CONTROL_CHECKPOINT_ACTIVITY),
))
def test_control_activity_rejects_foreign_native_context_without_owner_writes(
    tmp_path, monkeypatch, field, value,
):
    controller, payload, exops, executor = command_ready(tmp_path, monkeypatch)
    info = native_context(monkeypatch, payload["target"])
    setattr(info, field, value)
    before = exops.application.journal.read()
    with pytest.raises(AdmissionError):
        asyncio.run(_ControlActivities(exops, executor, controller).apply(payload))
    assert exops.application.journal.read() == before


@pytest.mark.parametrize("kind,state", (("cancel", "cancelled"), ("pause", "paused")))
def test_native_activity_applies_real_command_once_and_replays_exactly(tmp_path, monkeypatch, kind, state):
    controller, payload, exops, executor = command_ready(tmp_path, monkeypatch, kind=kind)
    native_context(monkeypatch, payload["target"])
    activities = _ControlActivities(exops, executor, controller)
    key = "exops.work." + payload["target"]["work_run_id"]
    received = exops.application.journal.read()["intents"][key]
    first = asyncio.run(activities.apply(payload))
    snapshot = exops.application.journal.read()
    record = snapshot["intents"][key]
    assert first["application_state"] == "applied" and record["run"]["state"] == state
    assert record["run"]["revision"] == received["run"]["revision"] + 1
    assert record["curation_initial_assessment"] == received["curation_initial_assessment"]
    assert not record["run"]["pending_action_ids"]
    assert asyncio.run(activities.apply(payload)) == first
    assert reconcile_control(controller, payload) == first
    assert exops.application.journal.read() == snapshot


def test_activity_and_applied_reconciliation_reauthorize_owner_revocation(tmp_path, monkeypatch):
    controller, payload, exops, executor = command_ready(tmp_path, monkeypatch)
    native_context(monkeypatch, payload["target"])
    activities = _ControlActivities(exops, executor, controller)
    asyncio.run(activities.apply(payload))
    snapshot = exops.application.journal.read()
    exops.authorize_actor = lambda actor, action: False
    with pytest.raises(AdmissionError):
        asyncio.run(activities.apply(payload))
    with pytest.raises(AdmissionError):
        reconcile_control(controller, payload)
    assert exops.application.journal.read() == snapshot


@pytest.mark.parametrize("change", ("extra", "blank", "nonstring", "missing", "foreign_dispatch"))
def test_checkpoint_activity_rejects_modified_dispatch_before_registration(tmp_path, monkeypatch, change):
    controller, target, exops, executor, _, run, _ = checkpoint_ready(tmp_path, monkeypatch)
    native_context(monkeypatch, target, activity_type=CONTROL_CHECKPOINT_ACTIVITY)
    record = exops.application.journal.read()["intents"]["exops.work." + run["work_run_id"]]
    payload = {
        "owner_operation_id": "knowledgeos", "work_run_id": run["work_run_id"],
        "action_id": record["action_id"], "content_digest": record["content_digest"],
        "work_spec_digest": record["spec"]["spec_digest"],
    }
    if change == "extra":
        payload["actor_id"] = "owner.user"
    elif change == "blank":
        payload["work_run_id"] = ""
    elif change == "nonstring":
        payload["work_run_id"] = 3
    elif change == "missing":
        del payload["action_id"]
    else:
        payload["content_digest"] = "sha256:foreign"
    snapshot = exops.application.journal.read()
    with pytest.raises(AdmissionError):
        asyncio.run(_ControlActivities(exops, executor, controller).checkpoint(payload))
    assert exops.application.journal.read() == snapshot


def test_ordinary_continuation_activity_cannot_execute_controlled_record(monkeypatch):
    activities, payload, record, _, calls, _ = continuation_adapter(monkeypatch)
    record["control_checkpoint"] = True
    with pytest.raises(AdmissionError):
        asyncio.run(activities.capture_evidence(payload))
    assert not calls


def test_control_initial_activity_requires_control_admission_flag(monkeypatch):
    activities, payload, _, info, calls, _ = continuation_adapter(monkeypatch)
    activities.workflow_type = CONTROL_WORKFLOW_TYPE
    info.workflow_type = CONTROL_WORKFLOW_TYPE
    with pytest.raises(AdmissionError):
        asyncio.run(activities.capture_evidence(payload))
    assert not calls


def transport_module(monkeypatch):
    # The maintained native smoke exercises the actual Services adapter. Unit
    # delivery tests need only its immutable dispatch envelope, not an SDK import.
    from types import ModuleType

    clients = ModuleType("clients")
    clients.__path__ = []
    temporal = ModuleType("clients.temporal")
    temporal.TemporalDispatch = SimpleNamespace
    monkeypatch.setitem(sys.modules, "clients", clients)
    monkeypatch.setitem(sys.modules, "clients.temporal", temporal)


def test_lost_update_response_reconciles_real_durable_application_without_redispatch(tmp_path, monkeypatch):
    controller, payload, exops, executor = command_ready(tmp_path, monkeypatch)
    native_context(monkeypatch, payload["target"])
    transport_module(monkeypatch)
    activities = _ControlActivities(exops, executor, controller)
    calls = []

    class LostResponse:
        async def update(self, dispatch):
            calls.append(dispatch)
            await activities.apply(dispatch.payload)
            raise ConnectionError("Response discarded after real owner commit")

    client = LostResponse()
    applied = asyncio.run(deliver_control(controller, client, payload))
    snapshot = exops.application.journal.read()
    assert applied["application_state"] == "applied" and len(calls) == 1
    assert calls[0].action_id == payload["command_id"]
    assert asyncio.run(deliver_control(controller, client, payload)) == applied
    assert len(calls) == 1 and exops.application.journal.read() == snapshot


def test_transport_outage_without_owner_application_remains_pending(tmp_path, monkeypatch):
    controller, payload, exops, _ = command_ready(tmp_path, monkeypatch)
    transport_module(monkeypatch)
    snapshot = exops.application.journal.read()
    calls = []

    class Unavailable:
        async def update(self, dispatch):
            calls.append(dispatch)
            raise ConnectionError("Worker remains offline")

    with pytest.raises(ConnectionError):
        asyncio.run(deliver_control(controller, Unavailable(), payload))
    assert len(calls) == 1
    assert exops.application.journal.read() == snapshot
    with pytest.raises(AdmissionError):
        reconcile_control(controller, payload)


@pytest.mark.parametrize("node", ("manager_successor", "normalization_proposal", "assess"))
def test_controlled_successor_requires_exact_owner_resume_release(monkeypatch, node):
    activities, payload, record, info, calls, _ = continuation_adapter(monkeypatch, node=node)
    activities.workflow_type = CONTROL_WORKFLOW_TYPE
    info.workflow_type = CONTROL_WORKFLOW_TYPE
    record["control_checkpoint"] = True
    with pytest.raises(AdmissionError):
        activities.current_record(payload, node, started=True)
    assert not calls


def test_control_initial_activity_rejects_known_foreign_native_run(monkeypatch):
    activities, payload, record, info, calls, _ = continuation_adapter(monkeypatch)
    activities.workflow_type = CONTROL_WORKFLOW_TYPE
    info.workflow_type = CONTROL_WORKFLOW_TYPE
    info.workflow_run_id = "foreign-native"
    record.update(control_checkpoint=True, temporal_run_id="known-native")
    with pytest.raises(AdmissionError):
        asyncio.run(activities.capture_evidence(payload))
    assert not calls


def test_worker_factory_rejects_mixed_owner_controller_before_loading_sdk(tmp_path, monkeypatch):
    from vaultops.application.curation_control_temporal import control_worker_components

    controller, _, exops, executor, *_ = checkpoint_ready(tmp_path, monkeypatch)
    before = exops.application.journal.read()
    with pytest.raises(AdmissionError):
        control_worker_components(object(), executor, controller)
    with pytest.raises(AdmissionError):
        control_worker_components(exops, object(), controller)
    assert exops.application.journal.read() == before


@pytest.mark.parametrize("field,value", (
    ("workflow_type", "KnowledgeOSCurationContinuationV1"),
    ("owner_operation_id", "foreign"),
    ("workflow_id_prefix", "knowledgeos/other/"),
    ("update_name", "curation.reconcile.v1"),
    ("query_name", "curation.recovery_status.v1"),
    ("target_host", "foreign:7233"),
    ("namespace", "foreign"),
    ("task_queue", "foreign.queue"),
    ("tls", True),
    ("tls", 0),
))
def test_control_transport_refuses_generic_or_recovery_bindings(monkeypatch, field, value):
    from vaultops.application.curation_control_temporal import connect_control_temporal
    from vaultops.application.temporal_contract import CONTROL_QUERY, CONTROL_UPDATE

    transport_module(monkeypatch)
    temporal = sys.modules["clients.temporal"]
    temporal.BindingError = ValueError

    async def forbidden_connect(*args):
        raise AssertionError("Invalid control binding reached an SDK connection")

    temporal.connect = forbidden_connect
    binding = SimpleNamespace(
        owner_operation_id="knowledgeos", workflow_type=CONTROL_WORKFLOW_TYPE,
        workflow_id_prefix="knowledgeos/", update_name=CONTROL_UPDATE, query_name=CONTROL_QUERY,
        target_host="server:7233", namespace="knowledgeos", task_queue="knowledgeos.work.v1", tls=False,
    )
    setattr(binding, field, value)
    with pytest.raises(ValueError):
        asyncio.run(connect_control_temporal(object(), binding))


def workflow_harness(monkeypatch, controller, payload, *, current_run_id=None):
    """Drive the real deterministic handler with real owner application."""
    import importlib
    from contextlib import nullcontext

    info = SimpleNamespace(
        run_id=current_run_id or payload["target"]["temporal_run_id"],
        workflow_id="knowledgeos/" + payload["target"]["work_run_id"],
        continued_run_id=None, is_continue_as_new_suggested=lambda: False,
    )
    applications = []

    def named(**kwargs):
        def decorate(value):
            value.validator = lambda validator: validator
            return value
        return decorate

    async def wait_condition(predicate):
        while not predicate():
            await asyncio.sleep(0)

    async def execute_activity(name, dispatch, **kwargs):
        applications.append(copy.deepcopy(dispatch))
        await asyncio.sleep(0)
        if name == CONTROL_ROLLOVER_PREPARE_ACTIVITY:
            return controller.prepare_rollover(
                dispatch["dispatch"]["work_run_id"], temporal_run_id=info.run_id,
                workflow_state=dispatch["workflow_state"],
            )
        if name == CONTROL_ROLLOVER_ADOPT_ACTIVITY:
            return controller.adopt_rollover(
                dispatch, temporal_run_id=info.run_id,
                previous_temporal_run_id=dispatch["rollover"]["source_temporal_run_id"],
            )
        return controller.apply_control(
            dispatch["target"]["work_run_id"], dispatch["command_id"],
            temporal_run_id=info.run_id,
        )

    workflow = SimpleNamespace(
        defn=named, query=named, update=named, run=lambda value: value,
        unsafe=SimpleNamespace(imports_passed_through=nullcontext),
        info=lambda: info, wait_condition=wait_condition, execute_activity=execute_activity,
        all_handlers_finished=lambda: True,
    )
    monkeypatch.setitem(sys.modules, "temporalio", SimpleNamespace(workflow=workflow))
    monkeypatch.setitem(sys.modules, "temporalio.common", SimpleNamespace(RetryPolicy=SimpleNamespace))

    class ApplicationError(Exception):
        def __init__(self, message, **kwargs):
            super().__init__(message)

    monkeypatch.setitem(sys.modules, "temporalio.exceptions", SimpleNamespace(
        ApplicationError=ApplicationError,
    ))
    monkeypatch.setitem(sys.modules, "temporalio.langgraph", SimpleNamespace(graph=None))
    module_name = "vaultops.application.curation_control_workflow"
    monkeypatch.setitem(sys.modules, module_name, None)
    monkeypatch.delitem(sys.modules, module_name, raising=False)
    selected = importlib.import_module(module_name).KnowledgeOSCurationControlWorkflow()
    selected._status = "awaiting_human"
    selected._target = copy.deepcopy(payload["target"])
    return selected, applications, info


def test_native_handler_serializes_pause_duplicates_and_replays_history_without_phase_changes(
    tmp_path, monkeypatch,
):
    controller, pause, exops, _ = command_ready(tmp_path, monkeypatch, kind="pause")
    selected, applications, _ = workflow_harness(monkeypatch, controller, pause)

    async def execute():
        receipts = await asyncio.gather(selected.control(pause), selected.control(copy.deepcopy(pause)))
        assert receipts[0] == receipts[1]
        assert selected.control_status()["status"] == "paused"
        assert selected.control_status()["target"] == receipts[0]["next_target"]
        paused_snapshot = exops.application.journal.read()
        assert await selected.control(pause) == receipts[0]
        assert exops.application.journal.read() == paused_snapshot
        resume = {"command_id": "command.runtime.resume", "kind": "resume",
                  "target": receipts[0]["next_target"]}
        controller.request_control(
            resume["target"]["work_run_id"], **resume,
            actor=OwnerActor("owner.user", "web"),
            expires_at=exops.application.clock() + timedelta(minutes=5),
        )
        resumed = await selected.control(resume)
        assert resumed["kind"] == "resume" and resumed["application_state"] == "applied"
        assert selected.control_status()["status"] == "running"
        assert selected.control_status()["target"] is None
        released_snapshot = exops.application.journal.read()
        assert await selected.control(pause) == receipts[0]
        assert await selected.control(resume) == resumed
        assert selected.control_status()["status"] == "running"
        assert selected.control_status()["target"] is None
        assert exops.application.journal.read() == released_snapshot
        assert len(applications) == 2
        conflicting = {**pause, "kind": "cancel"}
        with pytest.raises(ValueError):
            await selected.control(conflicting)
        assert exops.application.journal.read() == released_snapshot

    asyncio.run(execute())


@pytest.mark.parametrize("phase,kind", (
    ("awaiting_human", "resume"), ("paused", "pause"), ("running", "cancel"),
    ("completed", "resume"), ("cancelled", "pause"),
))
def test_native_handler_rejects_control_outside_admitted_phase_without_application(
    tmp_path, monkeypatch, phase, kind,
):
    controller, payload, exops, _ = command_ready(tmp_path, monkeypatch, kind="pause")
    selected, applications, _ = workflow_harness(monkeypatch, controller, payload)
    selected._status = phase
    before = exops.application.journal.read()
    with pytest.raises(ValueError):
        asyncio.run(selected.control({**payload, "kind": kind}))
    assert not applications and exops.application.journal.read() == before


@pytest.mark.parametrize("change", ("run", "workflow", "target"))
def test_native_handler_rejects_foreign_context_before_owner_application(tmp_path, monkeypatch, change):
    controller, payload, exops, _ = command_ready(tmp_path, monkeypatch, kind="pause")
    selected, applications, info = workflow_harness(monkeypatch, controller, payload)
    if change == "run":
        info.run_id = "foreign-run"
    elif change == "workflow":
        info.workflow_id = "knowledgeos/foreign"
    else:
        payload = copy.deepcopy(payload)
        payload["target"]["checkpoint_digest"] = "sha256:foreign"
    before = exops.application.journal.read()
    with pytest.raises(ValueError):
        asyncio.run(selected.control(payload))
    assert not applications and exops.application.journal.read() == before


@pytest.mark.parametrize("kind", ("pause", "resume"))
def test_response_loss_reconciles_nonterminal_control_without_dispatching_twice(tmp_path, monkeypatch, kind):
    controller, payload, exops, executor = command_ready(tmp_path, monkeypatch, kind="pause")
    if kind == "resume":
        paused = controller.apply_control(
            payload["target"]["work_run_id"], payload["command_id"],
            temporal_run_id=payload["target"]["temporal_run_id"],
        )
        payload = {"command_id": "command.runtime.resume", "kind": "resume",
                   "target": paused["next_target"]}
        controller.request_control(
            payload["target"]["work_run_id"], **payload, actor=OwnerActor("owner.user", "web"),
            expires_at=exops.application.clock() + timedelta(minutes=5),
        )
    native_context(monkeypatch, payload["target"])
    transport_module(monkeypatch)
    activities = _ControlActivities(exops, executor, controller)
    calls = []

    class LostResponse:
        async def update(self, dispatch):
            calls.append(dispatch)
            await activities.apply(dispatch.payload)
            raise ConnectionError("Committed native response discarded")

    receipt = asyncio.run(deliver_control(controller, LostResponse(), payload))
    before = exops.application.journal.read()
    assert receipt["kind"] == kind and receipt["application_state"] == "applied"
    assert asyncio.run(deliver_control(controller, LostResponse(), payload)) == receipt
    assert len(calls) == 1 and exops.application.journal.read() == before


@pytest.mark.parametrize("node", ("manager_successor", "normalization_proposal", "assess"))
def test_successor_activity_rechecks_real_owner_release_and_native_identity(
    tmp_path, monkeypatch, node,
):
    from vaultops.application.curation_continuation_temporal import _ContinuationActivities
    from vaultops.application.temporal_contract import CURATION_SUCCESSOR_GRAPH_NAME

    controller, pause, exops, executor = command_ready(tmp_path, monkeypatch, kind="pause")
    target = pause["target"]
    info = native_context(
        monkeypatch, target, activity_type=CURATION_SUCCESSOR_GRAPH_NAME + "." + node,
    )
    record = exops.application.journal.read()["intents"]["exops.work." + target["work_run_id"]]
    payload = {
        "owner_operation_id": "knowledgeos", "work_run_id": target["work_run_id"],
        "action_id": record["action_id"], "content_digest": record["content_digest"],
        "work_spec_digest": record["spec"]["spec_digest"],
    }
    activities = _ContinuationActivities(
        exops, executor, workflow_type=CONTROL_WORKFLOW_TYPE, controller=controller,
    )
    before = exops.application.journal.read()
    with pytest.raises(AdmissionError):
        activities.current_record(payload, node, started=True)
    assert exops.application.journal.read() == before
    paused = controller.apply_control(
        target["work_run_id"], pause["command_id"], temporal_run_id=target["temporal_run_id"],
    )
    with pytest.raises(AdmissionError):
        activities.current_record(payload, node, started=True)
    resume = {"command_id": "command.runtime.resume", "kind": "resume", "target": paused["next_target"]}
    controller.request_control(
        target["work_run_id"], **resume, actor=OwnerActor("owner.user", "web"),
        expires_at=exops.application.clock() + timedelta(minutes=5),
    )
    controller.apply_control(
        target["work_run_id"], resume["command_id"], temporal_run_id=target["temporal_run_id"],
    )
    assert activities.current_record(payload, node, started=True)["run"]["state"] == "running"
    before = exops.application.journal.read()
    info.workflow_run_id = "foreign-run"
    with pytest.raises(AdmissionError):
        activities.current_record(payload, node, started=True)
    assert exops.application.journal.read() == before
    info.workflow_run_id = target["temporal_run_id"]
    exops.authorize_actor = lambda actor, action: False
    with pytest.raises(AdmissionError):
        activities.current_record(payload, node, started=True)
    assert exops.application.journal.read() == before


def test_native_handler_cancel_while_paused_is_terminal_without_successor(tmp_path, monkeypatch):
    controller, pause, exops, _ = command_ready(tmp_path, monkeypatch, kind="pause")
    selected, applications, _ = workflow_harness(monkeypatch, controller, pause)

    async def execute():
        paused = await selected.control(pause)
        cancel = {"command_id": "command.runtime.cancel_paused", "kind": "cancel",
                  "target": paused["next_target"]}
        controller.request_control(
            cancel["target"]["work_run_id"], **cancel, actor=OwnerActor("owner.user", "web"),
            expires_at=exops.application.clock() + timedelta(minutes=5),
        )
        cancelled = await selected.control(cancel)
        assert selected.control_status()["status"] == "cancelled"
        assert selected.control_status()["target"] is None
        snapshot = exops.application.journal.read()
        record = snapshot["intents"]["exops.work." + cancel["target"]["work_run_id"]]
        assert record["run"]["state"] == "cancelled"
        assert record["run"]["segments_started"] == record["run"]["profile_invocations"] == 1
        assert "curation_model_request" not in record
        assert await selected.control(pause) == paused
        assert await selected.control(cancel) == cancelled
        assert exops.application.journal.read() == snapshot and len(applications) == 2

    asyncio.run(execute())


def original_dispatch(exops, work_run_id):
    record = exops.application.journal.read()["intents"]["exops.work." + work_run_id]
    return {
        "owner_operation_id": "knowledgeos", "work_run_id": work_run_id,
        "action_id": record["action_id"], "content_digest": record["content_digest"],
        "work_spec_digest": record["spec"]["spec_digest"],
    }


def prepared_rollover(tmp_path, monkeypatch, *, paused=False):
    controller, pause, exops, executor = command_ready(tmp_path, monkeypatch, kind="pause")
    target = pause["target"]
    commands = {}
    if paused:
        receipt = controller.apply_control(
            target["work_run_id"], pause["command_id"], temporal_run_id=target["temporal_run_id"],
        )
        commands[pause["command_id"]] = {"payload": pause, "receipt": receipt}
        target = receipt["next_target"]
        controller.request_control(
            target["work_run_id"], command_id="command.rollover.resume", kind="resume", target=target,
            actor=OwnerActor("owner.user", "web"),
            expires_at=exops.application.clock() + timedelta(minutes=5),
        )
    dispatch = original_dispatch(exops, target["work_run_id"])
    state = {"status": "paused" if paused else "awaiting_human",
             "target": target, "commands": commands}
    info = native_context(monkeypatch, target, activity_type=CONTROL_ROLLOVER_PREPARE_ACTIVITY)
    activities = _ControlActivities(exops, executor, controller)
    envelope = asyncio.run(activities.prepare_rollover({"dispatch": dispatch, "workflow_state": state}))
    return controller, envelope, activities, info, exops, pause


@pytest.mark.parametrize("activity_type", (
    CONTROL_ROLLOVER_PREPARE_ACTIVITY, CONTROL_ROLLOVER_ADOPT_ACTIVITY,
))
@pytest.mark.parametrize("field,value", (
    ("workflow_type", "KnowledgeOSCurationContinuationV1"),
    ("workflow_id", "knowledgeos/foreign"),
    ("workflow_run_id", ""),
    ("activity_type", CONTROL_APPLY_ACTIVITY),
))
def test_rollover_activities_reject_foreign_context_without_writes(
    tmp_path, monkeypatch, activity_type, field, value,
):
    controller, envelope, activities, info, exops, _ = prepared_rollover(tmp_path, monkeypatch)
    info.activity_type = activity_type
    if activity_type == CONTROL_ROLLOVER_ADOPT_ACTIVITY:
        info.workflow_run_id = "replacement-native"
    setattr(info, field, value)
    before = exops.application.journal.read()
    with pytest.raises(AdmissionError):
        if activity_type == CONTROL_ROLLOVER_PREPARE_ACTIVITY:
            asyncio.run(activities.prepare_rollover({
                "dispatch": envelope["dispatch"],
                "workflow_state": envelope["rollover"]["workflow_state"],
            }))
        else:
            asyncio.run(activities.adopt_rollover(envelope))
    assert exops.application.journal.read() == before
    assert controller is activities.controller


@pytest.mark.parametrize("change", (
    "extra", "missing", "dispatch", "handoff", "source", "state", "commands", "pending",
))
def test_adopt_activity_rejects_malformed_envelope_without_writes(tmp_path, monkeypatch, change):
    _, envelope, activities, info, exops, _ = prepared_rollover(tmp_path, monkeypatch)
    info.activity_type = CONTROL_ROLLOVER_ADOPT_ACTIVITY
    info.workflow_run_id = "replacement-native"
    malformed = copy.deepcopy(envelope)
    if change == "extra":
        malformed["actor"] = "owner.user"
    elif change == "missing":
        del malformed["dispatch"]
    elif change == "dispatch":
        malformed["dispatch"]["action_id"] = ""
    elif change == "handoff":
        malformed["rollover"]["extra"] = True
    elif change == "source":
        malformed["rollover"]["source_temporal_run_id"] = "foreign-native"
    elif change == "state":
        malformed["rollover"]["workflow_state"]["status"] = "running"
    elif change == "commands":
        malformed["rollover"]["workflow_state"]["commands"] = {"command.bad": {}}
    else:
        malformed["rollover"]["pending_commands"] = [{}]
    before = exops.application.journal.read()
    with pytest.raises(AdmissionError):
        asyncio.run(activities.adopt_rollover(malformed))
    assert exops.application.journal.read() == before


def test_rollover_activities_preserve_paused_cache_pending_resume_and_fence_old_run(
    tmp_path, monkeypatch,
):
    controller, envelope, activities, info, exops, pause = prepared_rollover(
        tmp_path, monkeypatch, paused=True,
    )
    target = envelope["rollover"]["workflow_state"]["target"]
    key = "exops.work." + target["work_run_id"]
    frozen = exops.application.journal.read()["intents"][key]
    assert envelope["rollover"]["pending_commands"] == [{
        "command_id": "command.rollover.resume", "kind": "resume", "target": target,
    }]
    info.activity_type = CONTROL_ROLLOVER_ADOPT_ACTIVITY
    with pytest.raises(AdmissionError):
        asyncio.run(activities.adopt_rollover(envelope))
    info.workflow_run_id = "replacement-native"
    state = asyncio.run(activities.adopt_rollover(envelope))
    adopted = exops.application.journal.read()
    assert state == envelope["rollover"]["workflow_state"]
    assert adopted["intents"][key] == frozen
    assert asyncio.run(activities.adopt_rollover(envelope)) == state
    assert exops.application.journal.read() == adopted
    selected, applications, workflow_info = workflow_harness(
        monkeypatch, controller, pause, current_run_id=info.workflow_run_id,
    )
    workflow_info.continued_run_id = target["temporal_run_id"]
    asyncio.run(selected._adopt(envelope))
    assert selected.control_status() == {
        "status": "paused", "target": target, "temporal_run_id": info.workflow_run_id,
        "rollover": {
            "count": 1, "source_temporal_run_id": target["temporal_run_id"],
            "current_temporal_run_id": info.workflow_run_id, "requested": False,
        },
    }
    before_duplicate = exops.application.journal.read()
    assert asyncio.run(selected.control(pause)) == state["commands"][pause["command_id"]]["receipt"]
    assert len(applications) == 1 and exops.application.journal.read() == before_duplicate
    with pytest.raises(ValueError):
        asyncio.run(selected.request_rollover(envelope["dispatch"]))
    resume = envelope["rollover"]["pending_commands"][0]
    resumed = asyncio.run(selected.control(resume))
    assert resumed["application_state"] == "applied"
    assert selected.control_status()["status"] == "running"
    assert selected.control_status()["target"] is None
    assert controller.authorize_native_run(
        target["work_run_id"], temporal_run_id=info.workflow_run_id,
    )["run"]["state"] == "running"
    with pytest.raises(AdmissionError):
        controller.authorize_native_run(
            target["work_run_id"], temporal_run_id=target["temporal_run_id"],
        )


@pytest.mark.parametrize("change", ("previous", "same_run", "workflow"))
def test_workflow_adoption_rejects_foreign_native_lineage_before_activity(
    tmp_path, monkeypatch, change,
):
    controller, envelope, _, _, exops, pause = prepared_rollover(tmp_path, monkeypatch)
    selected, applications, info = workflow_harness(
        monkeypatch, controller, pause, current_run_id="replacement-native",
    )
    info.continued_run_id = envelope["rollover"]["source_temporal_run_id"]
    if change == "previous":
        info.continued_run_id = "foreign-native"
    elif change == "same_run":
        info.run_id = info.continued_run_id
    else:
        info.workflow_id = "knowledgeos/foreign"
    before = exops.application.journal.read()
    with pytest.raises(Exception, match="lineage"):
        asyncio.run(selected._adopt(envelope))
    assert not applications and exops.application.journal.read() == before
    assert selected._commands == {}


@pytest.mark.parametrize("boundary", ("prepare", "adopt"))
def test_workflow_failed_rollover_activity_never_continues_or_restores_state(
    tmp_path, monkeypatch, boundary,
):
    controller, envelope, _, _, exops, pause = prepared_rollover(tmp_path, monkeypatch)
    current_run_id = "replacement-native" if boundary == "adopt" else None
    selected, applications, info = workflow_harness(
        monkeypatch, controller, pause, current_run_id=current_run_id,
    )
    workflow = sys.modules["temporalio"].workflow
    calls = []

    async def unavailable(name, payload, **kwargs):
        calls.append(name)
        raise ConnectionError("Owner rollover Activity unavailable")

    def forbidden_continue(payload):
        raise AssertionError("Failed prepare must never Continue-As-New")

    workflow.execute_activity = unavailable
    workflow.continue_as_new = forbidden_continue
    before = exops.application.journal.read()
    with pytest.raises(ConnectionError, match="unavailable"):
        if boundary == "prepare":
            selected._dispatch = envelope["dispatch"]
            selected._rollover_requested = True
            asyncio.run(selected._continue_as_new())
        else:
            selected._status = "initializing"
            selected._target = None
            info.continued_run_id = envelope["rollover"]["source_temporal_run_id"]
            asyncio.run(selected._adopt(envelope))
    assert len(calls) == 1 and not applications
    assert exops.application.journal.read() == before
    if boundary == "adopt":
        assert selected._status == "initializing" and selected._target is None
    else:
        assert selected._rolling_over


@pytest.mark.parametrize("change", (
    "dispatch", "workflow", "run", "phase", "applying", "pending", "handler", "second",
))
def test_rollover_update_requires_idle_exact_original_dispatch(tmp_path, monkeypatch, change):
    controller, pause, exops, _ = command_ready(tmp_path, monkeypatch, kind="pause")
    selected, applications, info = workflow_harness(monkeypatch, controller, pause)
    dispatch = original_dispatch(exops, pause["target"]["work_run_id"])
    selected._dispatch = copy.deepcopy(dispatch)
    if change == "dispatch":
        dispatch["content_digest"] = "sha256:foreign"
    elif change == "workflow":
        info.workflow_id = "knowledgeos/foreign"
    elif change == "run":
        info.run_id = "foreign-native"
    elif change == "phase":
        selected._status = "running"
    elif change == "applying":
        selected._applying = True
    elif change == "pending":
        selected._pending = pause
    elif change == "handler":
        selected._control_handlers = 1
    else:
        selected._rollover_count = 1
    before = exops.application.journal.read()
    with pytest.raises(ValueError):
        asyncio.run(selected.request_rollover(dispatch))
    assert not selected._rollover_requested
    assert not applications and exops.application.journal.read() == before


def test_native_suggestion_and_explicit_request_only_enable_one_idle_rollover(tmp_path, monkeypatch):
    controller, pause, exops, _ = command_ready(tmp_path, monkeypatch, kind="pause")
    selected, applications, info = workflow_harness(monkeypatch, controller, pause)
    selected._dispatch = original_dispatch(exops, pause["target"]["work_run_id"])
    assert not selected._rollover_ready()
    info.is_continue_as_new_suggested = lambda: True
    assert selected._rollover_ready()
    selected._control_handlers = 1
    assert not selected._rollover_ready()
    selected._control_handlers = 0
    selected._status = "paused"
    assert selected._rollover_ready()
    selected._rollover_count = 1
    assert not selected._rollover_ready()
    selected._rollover_count = 0
    info.is_continue_as_new_suggested = lambda: False
    before = exops.application.journal.read()
    assert asyncio.run(selected.request_rollover(selected._dispatch)) == {
        "rollover_state": "requested", "source_temporal_run_id": info.run_id,
    }
    assert selected._rollover_ready() and not applications
    with pytest.raises(ValueError):
        asyncio.run(selected.control(pause))
    assert exops.application.journal.read() == before


def test_continue_as_new_drains_handlers_before_prepare_and_carries_exact_envelope(
    tmp_path, monkeypatch,
):
    controller, envelope, _, _, exops, pause = prepared_rollover(
        tmp_path, monkeypatch, paused=True,
    )
    selected, applications, _ = workflow_harness(monkeypatch, controller, pause)
    state = envelope["rollover"]["workflow_state"]
    selected._status = state["status"]
    selected._target = copy.deepcopy(state["target"])
    selected._commands = copy.deepcopy(state["commands"])
    selected._dispatch = envelope["dispatch"]
    selected._rollover_requested = True
    workflow = sys.modules["temporalio"].workflow
    finished = False
    continued = []
    workflow.all_handlers_finished = lambda: finished

    class Continued(Exception):
        pass

    def continue_as_new(payload):
        assert finished
        continued.append(payload)
        raise Continued()

    workflow.continue_as_new = continue_as_new
    before = exops.application.journal.read()

    async def execute():
        nonlocal finished
        task = asyncio.create_task(selected._continue_as_new())
        await asyncio.sleep(0)
        assert selected._rolling_over and not applications and not continued
        finished = True
        with pytest.raises(Continued):
            await task

    asyncio.run(execute())
    assert applications == [{"dispatch": envelope["dispatch"], "workflow_state": state}]
    assert continued == [envelope]
    assert exops.application.journal.read() == before


@pytest.mark.parametrize("boundary", ("prepare", "adopt"))
def test_rollover_activity_propagates_current_owner_denial_without_writes(
    tmp_path, monkeypatch, boundary,
):
    _, envelope, activities, info, exops, _ = prepared_rollover(tmp_path, monkeypatch)
    if boundary == "adopt":
        info.activity_type = CONTROL_ROLLOVER_ADOPT_ACTIVITY
        info.workflow_run_id = "replacement-native"
    exops.authorize_actor = lambda actor, action: False
    before = exops.application.journal.read()
    with pytest.raises(AdmissionError):
        if boundary == "prepare":
            asyncio.run(activities.prepare_rollover({
                "dispatch": envelope["dispatch"],
                "workflow_state": envelope["rollover"]["workflow_state"],
            }))
        else:
            asyncio.run(activities.adopt_rollover(envelope))
    assert exops.application.journal.read() == before


def test_replacement_control_and_successor_activities_authorize_new_not_original_native_run(
    tmp_path, monkeypatch,
):
    from vaultops.application.curation_continuation_temporal import _ContinuationActivities
    from vaultops.application.temporal_contract import CURATION_SUCCESSOR_GRAPH_NAME

    _, envelope, activities, info, exops, _ = prepared_rollover(tmp_path, monkeypatch, paused=True)
    info.activity_type = CONTROL_ROLLOVER_ADOPT_ACTIVITY
    info.workflow_run_id = "replacement-native"
    asyncio.run(activities.adopt_rollover(envelope))
    resume = envelope["rollover"]["pending_commands"][0]
    info.activity_type = CONTROL_APPLY_ACTIVITY
    before = exops.application.journal.read()
    info.workflow_run_id = resume["target"]["temporal_run_id"]
    with pytest.raises(AdmissionError):
        asyncio.run(activities.apply(resume))
    assert exops.application.journal.read() == before
    info.workflow_run_id = "replacement-native"
    assert asyncio.run(activities.apply(resume))["application_state"] == "applied"
    successor = _ContinuationActivities(
        exops, activities.executor, workflow_type=CONTROL_WORKFLOW_TYPE,
        controller=activities.controller,
    )
    info.activity_type = CURATION_SUCCESSOR_GRAPH_NAME + ".manager_successor"
    assert successor.current_record(
        envelope["dispatch"], "manager_successor", started=True,
    )["run"]["state"] == "running"
    before = exops.application.journal.read()
    info.workflow_run_id = resume["target"]["temporal_run_id"]
    with pytest.raises(AdmissionError):
        successor.current_record(envelope["dispatch"], "manager_successor", started=True)
    assert exops.application.journal.read() == before

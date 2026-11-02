"""SDK-free exact-dispatch and current-owner continuation Activity contracts."""

from __future__ import annotations

import asyncio
import copy
import sys
from types import SimpleNamespace

import pytest

from vaultops.adapters.core import AdmissionError
from vaultops.application.curation_continuation_temporal import _ContinuationActivities
from vaultops.application.temporal_contract import (
    CONTINUATION_METHOD_ID,
    CONTINUATION_WORKFLOW_TYPE,
    CURATION_INITIAL_GRAPH_NAME,
    CURATION_SUCCESSOR_GRAPH_NAME,
)


def adapter(monkeypatch, node="capture_evidence"):
    payload = {
        "owner_operation_id": "knowledgeos", "work_run_id": "workrun.continuation.unit",
        "action_id": "action.continuation.unit", "content_digest": "sha256:content",
        "work_spec_digest": "sha256:spec",
    }
    record = {
        "spec": {
            "target_kind": "team", "spec_digest": payload["work_spec_digest"],
            "method_contract_ref": {"method_id": CONTINUATION_METHOD_ID},
        },
        "action_id": payload["action_id"], "content_digest": payload["content_digest"],
        "application_state": "started",
    }
    graph_name = CURATION_INITIAL_GRAPH_NAME if node in {
        "manager", "capture_evidence", "assess_initial"
    } else CURATION_SUCCESSOR_GRAPH_NAME
    info = SimpleNamespace(
        workflow_id="knowledgeos/" + payload["work_run_id"],
        workflow_type=CONTINUATION_WORKFLOW_TYPE, activity_type=graph_name + "." + node,
    )
    monkeypatch.setitem(sys.modules, "temporalio", SimpleNamespace(
        activity=SimpleNamespace(info=lambda: info)
    ))
    calls = []

    def current(work_run_id):
        calls.append(("authorize", work_run_id))
        return copy.deepcopy(record)

    async def capture(work_run_id):
        calls.append(("capture", work_run_id))

    decision = {"continuation_state": "human_required", "receipt": {"state": "awaiting_human"}}

    async def assess_initial(work_run_id):
        calls.append(("assess_initial", work_run_id))
        return decision

    def unknown(work_run_id, **context):
        calls.append(("unknown", work_run_id, context))

    executor = SimpleNamespace(_current=current, capture_evidence=capture, assess_initial=assess_initial)
    exops = SimpleNamespace(
        worker_identity="worker.unit", worker_unknown=unknown,
        application=SimpleNamespace(journal=SimpleNamespace(read=lambda: {
            "intents": {"exops.work." + payload["work_run_id"]: record}
        })),
    )
    return _ContinuationActivities(exops, executor), payload, record, info, calls, decision


@pytest.mark.parametrize("field,value", (
    ("workflow_id", "knowledgeos/foreign"),
    ("workflow_type", "KnowledgeOSCurationTeamV1"),
    ("activity_type", CURATION_SUCCESSOR_GRAPH_NAME + ".capture_evidence"),
))
def test_activity_rejects_foreign_native_context(monkeypatch, field, value):
    activities, payload, _, info, calls, _ = adapter(monkeypatch)
    setattr(info, field, value)
    with pytest.raises(AdmissionError):
        asyncio.run(activities.capture_evidence(payload))
    assert not calls


@pytest.mark.parametrize("change", (
    "method", "target", "action", "content", "spec", "owner", "extra", "missing", "blank", "state"
))
def test_activity_cannot_replace_original_dispatch_or_method(monkeypatch, change):
    activities, payload, record, _, calls, _ = adapter(monkeypatch)
    if change == "method":
        record["spec"]["method_contract_ref"]["method_id"] = "knowledgeos.capture_normalization_team"
    elif change == "target":
        record["spec"]["target_kind"] = "officer"
    elif change == "action":
        payload["action_id"] = "foreign"
    elif change == "content":
        payload["content_digest"] = "sha256:foreign"
    elif change == "spec":
        payload["work_spec_digest"] = "sha256:foreign"
    elif change == "owner":
        payload["owner_operation_id"] = "foreign"
    elif change == "extra":
        payload["continuation_state"] = "allowed"
    elif change == "missing":
        del payload["action_id"]
    elif change == "blank":
        payload["action_id"] = ""
    else:
        record["application_state"] = "unknown"
    with pytest.raises(AdmissionError):
        asyncio.run(activities.capture_evidence(payload))
    assert not calls


def test_current_owner_revocation_prevents_execution(monkeypatch):
    activities, payload, _, _, calls, _ = adapter(monkeypatch)

    def revoked(work_run_id):
        raise AdmissionError("ACTION_DENIED", "Owner revoked Work authority")

    activities.executor._current = revoked
    with pytest.raises(AdmissionError, match="revoked"):
        asyncio.run(activities.capture_evidence(payload))
    assert not calls


def test_uncertain_activity_records_unknown_without_retry(monkeypatch):
    activities, payload, _, _, calls, _ = adapter(monkeypatch)

    async def failed(work_run_id):
        calls.append(("attempt", work_run_id))
        raise RuntimeError("uncertain execution")

    activities.executor.capture_evidence = failed
    with pytest.raises(RuntimeError, match="uncertain"):
        asyncio.run(activities.capture_evidence(payload))
    assert [item[0] for item in calls] == ["authorize", "attempt", "unknown"]


@pytest.mark.parametrize("workflow_type,target_kind,method_id", (
    (CONTINUATION_WORKFLOW_TYPE, "team", "knowledgeos.capture_normalization_team"),
    (CONTINUATION_WORKFLOW_TYPE, "team", None),
    (CONTINUATION_WORKFLOW_TYPE, "officer", CONTINUATION_METHOD_ID),
    ("KnowledgeOSCurationTeamV1", "team", CONTINUATION_METHOD_ID),
    ("knowledgeos.work.v1", "officer", CONTINUATION_METHOD_ID),
))
def test_dispatch_workflow_is_bound_to_continuation_method(
    monkeypatch, workflow_type, target_kind, method_id
):
    from types import ModuleType

    from vaultops.application.temporal_work import connect_owner_temporal

    spec = {"target_kind": target_kind, "spec_digest": "sha256:spec"}
    if method_id is not None:
        spec["method_contract_ref"] = {"method_id": method_id}
    record = {"spec": spec}
    exops = SimpleNamespace(authorize_dispatch=lambda **context: record)
    binding = SimpleNamespace(
        owner_operation_id="knowledgeos", target_host="server:7233",
        namespace="knowledgeos", task_queue="knowledgeos.work.v1",
        workflow_type=workflow_type, workflow_id_prefix="knowledgeos/", tls=False,
    )

    async def connect(selected, authorize, authorize_observation):
        return authorize

    clients = ModuleType("clients")
    clients.__path__ = []
    transport = ModuleType("clients.temporal")
    transport.BindingError = ValueError
    transport.TemporalDispatch = SimpleNamespace
    transport.connect = connect
    monkeypatch.setitem(sys.modules, "clients", clients)
    monkeypatch.setitem(sys.modules, "clients.temporal", transport)
    authorize = asyncio.run(connect_owner_temporal(exops, binding))
    dispatch = SimpleNamespace(
        workflow_id="knowledgeos/workrun.unit", action_id="action.unit",
        payload={
            "owner_operation_id": "knowledgeos", "work_run_id": "workrun.unit",
            "action_id": "action.unit", "content_digest": "sha256:content",
            "work_spec_digest": spec["spec_digest"],
        },
    )
    with pytest.raises(ValueError, match="admitted Work method"):
        asyncio.run(authorize(dispatch))

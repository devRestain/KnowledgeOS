"""SDK-free contracts for the narrow native recovery Activity boundary."""

from __future__ import annotations

import asyncio
import copy
import sys
from types import SimpleNamespace

import pytest

from vaultops.adapters.core import AdmissionError
from vaultops.application.curation_temporal import _CurationActivities
from vaultops.application.temporal_contract import (
    CURATION_MARK_UNKNOWN_ACTIVITY,
    CURATION_RECONCILE_ACTIVITY,
    TEAM_WORKFLOW_TYPE,
    curation_recovery_target,
)


def recovery_adapter(monkeypatch):
    dispatch = {
        "owner_operation_id": "knowledgeos",
        "work_run_id": "workrun.curation.unit",
        "action_id": "action.curation.unit",
        "content_digest": "sha256:content",
        "work_spec_digest": "sha256:spec",
    }
    target = {
        **dispatch,
        "temporal_run_id": "native-run",
        "activity_id": "3",
        "run_revision": 2,
        "generation": 1,
        "fencing_token": "fence.unit",
    }
    payload = {"recovery_id": "recovery.unit", "target": target}
    record = {
        "spec": {"target_kind": "team", "spec_digest": dispatch["work_spec_digest"]},
        "action_id": dispatch["action_id"],
        "content_digest": dispatch["content_digest"],
        "application_state": "unknown",
    }
    intents = {
        "exops.work." + dispatch["work_run_id"]: record,
        "curation.recovery.recovery.unit": {"target": copy.deepcopy(target)},
    }
    info = SimpleNamespace(
        workflow_id="knowledgeos/" + dispatch["work_run_id"],
        workflow_type=TEAM_WORKFLOW_TYPE,
        workflow_run_id="native-run",
        activity_type=CURATION_RECONCILE_ACTIVITY,
    )
    monkeypatch.setitem(
        sys.modules,
        "temporalio",
        SimpleNamespace(activity=SimpleNamespace(info=lambda: info)),
    )
    calls = []
    receipt = {"work_run_id": dispatch["work_run_id"], "status": "completed"}

    async def reconcile(work_run_id, recovery_id, **context):
        calls.append((work_run_id, recovery_id, context))
        return receipt

    def mark_unknown(work_run_id, **context):
        calls.append((work_run_id, context))

    executor = SimpleNamespace(reconcile=reconcile, mark_unknown=mark_unknown)
    journal = SimpleNamespace(read=lambda: {"intents": intents})
    exops = SimpleNamespace(application=SimpleNamespace(journal=journal))
    return _CurationActivities(exops, executor), payload, dispatch, info, intents, calls, receipt


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("workflow_id", "knowledgeos/foreign"),
        ("workflow_type", "foreign-workflow"),
        ("workflow_run_id", "foreign-run"),
        ("activity_type", "foreign-activity"),
    ),
)
def test_reconcile_rejects_foreign_native_context(monkeypatch, field, value):
    adapter, payload, _, info, _, calls, _ = recovery_adapter(monkeypatch)
    setattr(info, field, value)
    with pytest.raises(AdmissionError):
        asyncio.run(adapter.reconcile(payload))
    assert not calls


@pytest.mark.parametrize("change", ("missing", "activity", "revision"))
def test_reconcile_cannot_manufacture_owner_authorization(monkeypatch, change):
    adapter, payload, _, _, intents, calls, _ = recovery_adapter(monkeypatch)
    if change == "missing":
        del intents["curation.recovery.recovery.unit"]
    elif change == "activity":
        payload["target"]["activity_id"] = "foreign-activity"
    else:
        payload["target"]["run_revision"] += 1
    with pytest.raises(AdmissionError):
        asyncio.run(adapter.reconcile(payload))
    assert not calls


@pytest.mark.parametrize("change", ("grant", "actor", "target_extra", "target_missing", "blank_id"))
def test_recovery_wire_shape_rejects_authorization_and_extra_fields(monkeypatch, change):
    _, payload, _, _, _, _, _ = recovery_adapter(monkeypatch)
    if change in {"grant", "actor"}:
        payload[change] = True
    elif change == "target_extra":
        payload["target"]["quiesced"] = True
    elif change == "target_missing":
        del payload["target"]["activity_id"]
    else:
        payload["recovery_id"] = ""
    with pytest.raises(ValueError):
        curation_recovery_target(payload)


@pytest.mark.parametrize("change", ("native_run", "native_type", "grant"))
def test_mark_unknown_rejects_foreign_context_and_grants(monkeypatch, change):
    adapter, _, dispatch, info, _, calls, _ = recovery_adapter(monkeypatch)
    info.activity_type = CURATION_MARK_UNKNOWN_ACTIVITY
    envelope = {
        "dispatch": dispatch,
        "failed_activity_id": "3",
        "temporal_run_id": "native-run",
    }
    if change == "native_run":
        info.workflow_run_id = "foreign-run"
    elif change == "native_type":
        info.activity_type = "foreign-activity"
    else:
        envelope["quiesced"] = True
    with pytest.raises(AdmissionError):
        asyncio.run(adapter.mark_unknown(envelope))
    assert not calls


@pytest.mark.parametrize(
    ("field", "value"), (("generation", True), ("run_revision", 0), ("fencing_token", ""))
)
def test_recovery_target_requires_exact_typed_revision_and_fence(monkeypatch, field, value):
    _, payload, _, _, _, _, _ = recovery_adapter(monkeypatch)
    payload["target"][field] = value
    with pytest.raises(ValueError):
        curation_recovery_target(payload)

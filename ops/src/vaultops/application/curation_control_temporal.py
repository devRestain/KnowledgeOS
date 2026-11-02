"""Opt-in native checkpoint/control Activities and owner-authorized delivery.

Services remains the transport adapter, not an authority or retry journal. A
failed Update response is reconciled from the exact canonical owner receipt;
it is never turned into an automatic repeat application.
"""

from __future__ import annotations

import copy
from functools import partial
from typing import Any

from ..adapters.core import AdmissionError
from .curation_continuation_temporal import continuation_worker_components
from .temporal_contract import (
    CONTINUATION_METHOD_ID,
    CONTROL_APPLY_ACTIVITY,
    CONTROL_CHECKPOINT_ACTIVITY,
    CONTROL_QUERY,
    CONTROL_ROLLOVER_ADOPT_ACTIVITY,
    CONTROL_ROLLOVER_PREPARE_ACTIVITY,
    CONTROL_UPDATE,
    CONTROL_WORKFLOW_TYPE,
    CURATION_DISPATCH_FIELDS,
    curation_control_target,
    curation_dispatch,
    curation_rollover_envelope,
    curation_rollover_state,
)


class _ControlActivities:
    def __init__(self, exops, executor, controller):
        self.exops = exops
        self.executor = executor
        self.controller = controller

    async def checkpoint(self, payload: dict[str, Any]) -> dict[str, Any]:
        from temporalio import activity

        info = activity.info()
        if info.activity_type != CONTROL_CHECKPOINT_ACTIVITY:
            raise AdmissionError("BINDING_INVALID", "Checkpoint differs from native Activity")
        # Reuse exact owner dispatch checks without pretending this is a graph
        # node. The controller additionally reconstructs the closed initial graph.
        if (
            not isinstance(payload, dict) or set(payload) != CURATION_DISPATCH_FIELDS
            or any(not isinstance(value, str) or not value for value in payload.values())
            or payload["owner_operation_id"] != "knowledgeos"
        ):
            raise AdmissionError("INVALID_PAYLOAD", "Checkpoint differs from owner dispatch")
        record = self.exops.application.journal.read()["intents"].get(
            "exops.work." + payload["work_run_id"]
        )
        if (
            record is None or record.get("control_checkpoint") is not True
            or record["spec"].get("target_kind") != "team"
            or record["spec"].get("method_contract_ref", {}).get("method_id") != CONTINUATION_METHOD_ID
            or record["action_id"] != payload["action_id"]
            or record["content_digest"] != payload["content_digest"]
            or record["spec"]["spec_digest"] != payload["work_spec_digest"]
            or info.workflow_type != CONTROL_WORKFLOW_TYPE
            or info.workflow_id != "knowledgeos/" + payload["work_run_id"]
            or not info.workflow_run_id
        ):
            raise AdmissionError("BINDING_INVALID", "Checkpoint lacks exact controlled Work dispatch")
        return self.controller.open_checkpoint(
            payload["work_run_id"], temporal_run_id=info.workflow_run_id,
        )

    async def apply(self, payload: dict[str, Any]) -> dict[str, Any]:
        from temporalio import activity

        try:
            target = curation_control_target(payload)
        except ValueError as error:
            raise AdmissionError("INVALID_PAYLOAD", str(error)) from error
        info = activity.info()
        if (
            info.activity_type != CONTROL_APPLY_ACTIVITY
            or info.workflow_type != CONTROL_WORKFLOW_TYPE
            or info.workflow_id != "knowledgeos/" + target["work_run_id"]
            or not info.workflow_run_id
        ):
            raise AdmissionError("BINDING_INVALID", "Control differs from exact native Run")
        self.controller.authorize_native_run(
            target["work_run_id"], temporal_run_id=info.workflow_run_id,
        )
        self.controller.authorize_dispatch(payload)
        return self.controller.apply_control(
            target["work_run_id"], payload["command_id"], temporal_run_id=info.workflow_run_id,
        )

    def _rollover_context(self, dispatch: dict[str, Any], activity_type: str):
        from temporalio import activity

        try:
            curation_dispatch(dispatch)
        except ValueError as error:
            raise AdmissionError("INVALID_PAYLOAD", str(error)) from error
        info = activity.info()
        record = self.exops.application.journal.read()["intents"].get(
            "exops.work." + dispatch["work_run_id"]
        )
        if (
            info.activity_type != activity_type
            or info.workflow_type != CONTROL_WORKFLOW_TYPE
            or info.workflow_id != "knowledgeos/" + dispatch["work_run_id"]
            or not info.workflow_run_id
            or record is None or record.get("control_checkpoint") is not True
            or record["spec"].get("target_kind") != "team"
            or record["spec"].get("method_contract_ref", {}).get("method_id") != CONTINUATION_METHOD_ID
            or record["action_id"] != dispatch["action_id"]
            or record["content_digest"] != dispatch["content_digest"]
            or record["spec"]["spec_digest"] != dispatch["work_spec_digest"]
        ):
            raise AdmissionError("BINDING_INVALID", "Rollover differs from exact native Work dispatch")
        return info

    async def prepare_rollover(self, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            if not isinstance(payload, dict) or set(payload) != {"dispatch", "workflow_state"}:
                raise ValueError("Rollover prepare requires exact dispatch and workflow_state")
            curation_rollover_state(payload["workflow_state"])
        except ValueError as error:
            raise AdmissionError("INVALID_PAYLOAD", str(error)) from error
        info = self._rollover_context(payload["dispatch"], CONTROL_ROLLOVER_PREPARE_ACTIVITY)
        envelope = self.controller.prepare_rollover(
            payload["dispatch"]["work_run_id"], temporal_run_id=info.workflow_run_id,
            workflow_state=payload["workflow_state"],
        )
        try:
            curation_rollover_envelope(envelope)
            if (
                envelope["dispatch"] != payload["dispatch"]
                or envelope["rollover"]["workflow_state"] != payload["workflow_state"]
                or envelope["rollover"]["source_temporal_run_id"] != info.workflow_run_id
            ):
                raise ValueError("Owner handoff changed original native dispatch/state")
        except ValueError as error:
            raise AdmissionError("BINDING_INVALID", str(error)) from error
        return envelope

    async def adopt_rollover(self, envelope: dict[str, Any]) -> dict[str, Any]:
        try:
            curation_rollover_envelope(envelope)
        except ValueError as error:
            raise AdmissionError("INVALID_PAYLOAD", str(error)) from error
        info = self._rollover_context(envelope["dispatch"], CONTROL_ROLLOVER_ADOPT_ACTIVITY)
        source = envelope["rollover"]["source_temporal_run_id"]
        if info.workflow_run_id == source:
            raise AdmissionError("BINDING_INVALID", "Rollover must adopt a distinct native Run")
        state = self.controller.adopt_rollover(
            envelope, temporal_run_id=info.workflow_run_id, previous_temporal_run_id=source,
        )
        try:
            curation_rollover_state(state)
            if state != envelope["rollover"]["workflow_state"]:
                raise ValueError("Owner adoption changed retained workflow state")
        except ValueError as error:
            raise AdmissionError("BINDING_INVALID", str(error)) from error
        return state


def control_worker_components(exops, executor, controller):
    """Return module-level Workflow and owner-gated official continuation graphs."""
    from .curation_control import CurationRunControl

    if (
        not isinstance(controller, CurationRunControl)
        or controller.exops is not exops or controller.executor is not executor
    ):
        raise AdmissionError("BINDING_INVALID", "Control Worker requires its exact owner controller")
    from temporalio import activity

    workflow, plugin = continuation_worker_components(
        exops, executor, control_mode=True, controller=controller,
    )
    activities = _ControlActivities(exops, executor, controller)
    plugin.activities.extend((
        activity.defn(name=CONTROL_CHECKPOINT_ACTIVITY)(partial(activities.checkpoint)),
        activity.defn(name=CONTROL_APPLY_ACTIVITY)(partial(activities.apply)),
        activity.defn(name=CONTROL_ROLLOVER_PREPARE_ACTIVITY)(partial(activities.prepare_rollover)),
        activity.defn(name=CONTROL_ROLLOVER_ADOPT_ACTIVITY)(partial(activities.adopt_rollover)),
    ))
    return workflow, plugin


async def connect_control_temporal(controller, binding):
    """Bind Services' fixed Update adapter to current owner command authority."""
    from clients.temporal import BindingError, connect

    if (
        binding.owner_operation_id != "knowledgeos"
        or binding.target_host != "server:7233"
        or binding.namespace != "knowledgeos"
        or binding.task_queue != "knowledgeos.work.v1"
        or binding.tls is not False
        or binding.workflow_type != CONTROL_WORKFLOW_TYPE
        or binding.workflow_id_prefix != "knowledgeos/"
        or binding.update_name != CONTROL_UPDATE
        or binding.query_name != CONTROL_QUERY
    ):
        raise BindingError("Control transport differs from exact owner Workflow binding")

    async def authorize(dispatch):
        target = curation_control_target(dispatch.payload)
        if (
            dispatch.owner_operation_id != target["owner_operation_id"]
            or dispatch.workflow_id != "knowledgeos/" + target["work_run_id"]
            or dispatch.action_id != dispatch.payload["command_id"]
        ):
            raise BindingError("Control transport differs from admitted command")
        controller.authorize_dispatch(dispatch.payload)

    return await connect(binding, authorize)


def reconcile_control(controller, payload: dict[str, Any]) -> dict[str, Any]:
    """Reauthorize durable application without native dispatch or effect execution."""
    target = curation_control_target(payload)
    receipt = controller.authorize_dispatch(payload)
    if (
        not isinstance(receipt, dict)
        or receipt.get("owner_operation_id") != target["owner_operation_id"]
        or receipt.get("work_run_id") != target["work_run_id"]
        or receipt.get("command_id") != payload["command_id"]
        or receipt.get("kind") != payload["kind"]
        or receipt.get("application_state") != "applied"
        or receipt.get("target_control_revision") != target["target_control_revision"]
    ):
        raise AdmissionError("CONTROL_UNCERTAIN", "Exact applied owner receipt is unavailable")
    return copy.deepcopy(receipt)


async def deliver_control(controller, client, payload: dict[str, Any]) -> dict[str, Any]:
    """Deliver once under stable Update ID; reconcile, never redispatch, on loss."""
    from clients.temporal import TemporalDispatch

    target = curation_control_target(payload)
    admitted = controller.authorize_dispatch(payload)
    if admitted.get("application_state") == "applied":
        return reconcile_control(controller, payload)
    dispatch = TemporalDispatch(
        owner_operation_id=target["owner_operation_id"],
        workflow_id="knowledgeos/" + target["work_run_id"],
        action_id=payload["command_id"], payload=copy.deepcopy(payload),
    )
    try:
        response = await client.update(dispatch)
    except Exception:
        # If the owner cannot prove application, preserve the original transport
        # error and pending command; an outage is not an applied control receipt.
        try:
            return reconcile_control(controller, payload)
        except AdmissionError:
            pass
        raise
    canonical = reconcile_control(controller, payload)
    if response != canonical:
        raise AdmissionError("BINDING_INVALID", "Native Update returned a noncanonical receipt")
    return canonical

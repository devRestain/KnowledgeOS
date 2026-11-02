"""SDK-only bounded Curation control; authority remains in the owner."""

from __future__ import annotations

import copy
from datetime import timedelta
from typing import Any

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ApplicationError

with workflow.unsafe.imports_passed_through():
    from temporalio.langgraph import graph

from .temporal_contract import (
    CONTROL_APPLY_ACTIVITY,
    CONTROL_CHECKPOINT_ACTIVITY,
    CONTROL_QUERY,
    CONTROL_ROLLOVER_ADOPT_ACTIVITY,
    CONTROL_ROLLOVER_PREPARE_ACTIVITY,
    CONTROL_ROLLOVER_UPDATE,
    CONTROL_UPDATE,
    CONTROL_WORKFLOW_TYPE,
    CURATION_INITIAL_GRAPH_NAME,
    CURATION_SUCCESSOR_GRAPH_NAME,
    curation_control_target,
    curation_dispatch,
    curation_rollover_envelope,
    curation_rollover_state,
)


@workflow.defn(name=CONTROL_WORKFLOW_TYPE)
class KnowledgeOSCurationControlWorkflow:
    def __init__(self) -> None:
        self._status = "initializing"
        self._target: dict[str, Any] | None = None
        self._pending: dict[str, Any] | None = None
        self._applying = False
        self._commands: dict[str, dict[str, Any]] = {}
        self._cancel_receipt: dict[str, Any] | None = None
        self._dispatch: dict[str, Any] | None = None
        self._native_run_id = workflow.info().run_id
        self._source_temporal_run_id = self._native_run_id
        self._rollover_count = 0
        self._rollover_requested = False
        self._rolling_over = False
        self._control_handlers = 0

    @workflow.query(name=CONTROL_QUERY)
    def control_status(self) -> dict[str, Any]:
        return {
            "status": self._status, "target": copy.deepcopy(self._target),
            "temporal_run_id": workflow.info().run_id,
            "rollover": {
                "count": self._rollover_count,
                "source_temporal_run_id": self._source_temporal_run_id,
                "current_temporal_run_id": self._native_run_id,
                "requested": self._rollover_requested,
            },
        }

    def _validate_control(self, payload: dict[str, Any]) -> None:
        target = curation_control_target(payload)
        if (
            target["temporal_run_id"] != self._source_temporal_run_id
            or workflow.info().run_id != self._native_run_id
            or workflow.info().workflow_id != "knowledgeos/" + target["work_run_id"]
        ):
            raise ValueError("Control differs from this exact native Run")
        previous = self._commands.get(payload["command_id"])
        if previous is not None:
            if payload != previous["payload"]:
                raise ValueError("Command identity has conflicting content")
            return
        if (
            self._target is None or target != self._target
            or self._rolling_over or self._rollover_requested
            or payload["kind"] not in {
                "awaiting_human": {"cancel", "pause"}, "paused": {"cancel", "resume"},
            }.get(self._status, set())
            or (self._pending is not None and payload != self._pending)
        ):
            raise ValueError("Control differs from this exact native checkpoint or phase")

    @workflow.update(name=CONTROL_UPDATE)
    async def control(self, payload: dict[str, Any]) -> dict[str, Any]:
        self._validate_control(payload)
        self._control_handlers += 1
        try:
            return await self._apply_control(payload)
        finally:
            self._control_handlers -= 1

    async def _apply_control(self, payload: dict[str, Any]) -> dict[str, Any]:
        await workflow.wait_condition(lambda: not self._applying)
        self._validate_control(payload)
        previous = self._commands.get(payload["command_id"])
        if previous is not None:
            return copy.deepcopy(previous["receipt"])
        # Retain this fence on any uncertain application; no automatic retries.
        self._pending = copy.deepcopy(payload)
        self._applying = True
        try:
            receipt = await workflow.execute_activity(
                CONTROL_APPLY_ACTIVITY, payload,
                start_to_close_timeout=timedelta(minutes=1),
                retry_policy=RetryPolicy(maximum_attempts=1),
            )
            if (
                not isinstance(receipt, dict)
                or receipt.get("application_state") != "applied"
                or receipt.get("command_id") != payload["command_id"]
                or receipt.get("kind") != payload["kind"]
                or receipt.get("work_run_id") != payload["target"]["work_run_id"]
                or receipt.get("target_control_revision") != payload["target"]["target_control_revision"]
            ):
                raise ApplicationError("Owner did not return exact applied control", non_retryable=True)
            next_target = None
            if payload["kind"] == "pause":
                next_target = receipt.get("next_target")
                curation_control_target({"command_id": payload["command_id"],
                                         "kind": "resume", "target": next_target})
                expected = {**payload["target"],
                            "target_control_revision": payload["target"]["target_control_revision"] + 1}
                # A paused checkpoint has its own digest, but every other pin is
                # immutable except the owner-advanced control revision.
                expected["checkpoint_digest"] = next_target["checkpoint_digest"]
                if next_target != expected:
                    raise ApplicationError("Owner paused target changed native pins", non_retryable=True)
            self._commands[payload["command_id"]] = {
                "payload": copy.deepcopy(payload), "receipt": copy.deepcopy(receipt),
            }
            self._pending = None
            if payload["kind"] == "pause":
                self._target = copy.deepcopy(next_target)
                self._status = "paused"
            elif payload["kind"] == "resume":
                self._target = None
                self._status = "running"
            else:
                self._target = None
                self._cancel_receipt = copy.deepcopy(receipt)
                self._status = "cancelled"
            return copy.deepcopy(receipt)
        finally:
            self._applying = False

    @control.validator
    def validate_control(self, payload: dict[str, Any]) -> None:
        self._validate_control(payload)

    def _validate_rollover(self, payload: dict[str, Any]) -> None:
        curation_dispatch(payload)
        if (
            payload != self._dispatch
            or workflow.info().workflow_id != "knowledgeos/" + payload["work_run_id"]
            or workflow.info().run_id != self._native_run_id
            or self._rollover_count != 0 or self._rollover_requested or self._rolling_over
            or self._status not in {"awaiting_human", "paused"}
            or self._target is None or self._applying or self._pending is not None
            or self._control_handlers != 0
        ):
            raise ValueError("Rollover requires the exact dispatch at an idle native checkpoint")

    @workflow.update(name=CONTROL_ROLLOVER_UPDATE)
    async def request_rollover(self, payload: dict[str, Any]) -> dict[str, Any]:
        self._validate_rollover(payload)
        self._rollover_requested = True
        return {
            "rollover_state": "requested", "source_temporal_run_id": self._native_run_id,
        }

    @request_rollover.validator
    def validate_rollover(self, payload: dict[str, Any]) -> None:
        self._validate_rollover(payload)

    def _rollover_ready(self) -> bool:
        return (
            self._rollover_count == 0 and self._status in {"awaiting_human", "paused"}
            and not self._applying and self._pending is None and self._control_handlers == 0
            and (self._rollover_requested or workflow.info().is_continue_as_new_suggested())
        )

    async def _continue_as_new(self) -> None:
        # Fence new controls before yielding; drain every accepted Update,
        # including the maintenance request, before freezing owner state.
        self._rolling_over = True
        await workflow.wait_condition(workflow.all_handlers_finished)
        state = {
            "status": self._status, "target": copy.deepcopy(self._target),
            "commands": copy.deepcopy(self._commands),
        }
        envelope = await workflow.execute_activity(
            CONTROL_ROLLOVER_PREPARE_ACTIVITY,
            {"dispatch": self._dispatch, "workflow_state": state},
            start_to_close_timeout=timedelta(minutes=1),
            retry_policy=RetryPolicy(maximum_attempts=1),
        )
        try:
            curation_rollover_envelope(envelope)
            if (
                envelope["dispatch"] != self._dispatch
                or envelope["rollover"]["workflow_state"] != state
                or envelope["rollover"]["source_temporal_run_id"] != self._native_run_id
            ):
                raise ValueError("Owner handoff changed native checkpoint/dispatch")
        except ValueError as error:
            raise ApplicationError(str(error), non_retryable=True) from error
        await workflow.wait_condition(workflow.all_handlers_finished)
        workflow.continue_as_new(envelope)

    async def _adopt(self, envelope: dict[str, Any]) -> None:
        try:
            curation_rollover_envelope(envelope)
            handoff = envelope["rollover"]
            if (
                workflow.info().continued_run_id != handoff["source_temporal_run_id"]
                or workflow.info().run_id == handoff["source_temporal_run_id"]
                or workflow.info().workflow_id != "knowledgeos/" + envelope["dispatch"]["work_run_id"]
            ):
                raise ValueError("Handoff differs from exact native Continue-As-New lineage")
        except ValueError as error:
            raise ApplicationError(str(error), non_retryable=True) from error
        self._dispatch = copy.deepcopy(envelope["dispatch"])
        state = await workflow.execute_activity(
            CONTROL_ROLLOVER_ADOPT_ACTIVITY, envelope,
            start_to_close_timeout=timedelta(minutes=1),
            retry_policy=RetryPolicy(maximum_attempts=1),
        )
        try:
            curation_rollover_state(state)
            if state != handoff["workflow_state"]:
                raise ValueError("Owner adoption changed retained checkpoint/cache")
        except ValueError as error:
            raise ApplicationError(str(error), non_retryable=True) from error
        self._source_temporal_run_id = handoff["source_temporal_run_id"]
        self._rollover_count = 1
        self._status = state["status"]
        self._target = copy.deepcopy(state["target"])
        self._commands = copy.deepcopy(state["commands"])

    @workflow.run
    async def run(self, payload: dict[str, Any]) -> dict[str, Any]:
        if isinstance(payload, dict) and set(payload) == {"dispatch", "rollover"}:
            await self._adopt(payload)
            payload = self._dispatch
        else:
            try:
                curation_dispatch(payload)
                if (
                    workflow.info().continued_run_id is not None
                    or workflow.info().workflow_id != "knowledgeos/" + payload["work_run_id"]
                ):
                    raise ValueError("Initial dispatch differs from native Workflow identity")
            except ValueError as error:
                raise ApplicationError(str(error), non_retryable=True) from error
            self._dispatch = copy.deepcopy(payload)
            decision = await graph(CURATION_INITIAL_GRAPH_NAME).compile().ainvoke(payload)
            if (
                not isinstance(decision, dict)
                or set(decision) != {"continuation_state", "receipt"}
                or not isinstance(decision["receipt"], dict)
            ):
                raise ApplicationError("Initial assessment returned no owner decision", non_retryable=True)
            if decision["continuation_state"] == "human_required":
                self._status = "human_required"
                return decision["receipt"]
            if decision["continuation_state"] != "allowed":
                raise ApplicationError("Owner did not admit continuation", non_retryable=True)
            self._target = await workflow.execute_activity(
                CONTROL_CHECKPOINT_ACTIVITY, payload,
                start_to_close_timeout=timedelta(minutes=1),
                retry_policy=RetryPolicy(maximum_attempts=1),
            )
            curation_control_target({"command_id": "checkpoint.validation", "kind": "pause",
                                     "target": self._target})
            if (
                self._target["temporal_run_id"] != workflow.info().run_id
                or self._target["work_run_id"] != payload["work_run_id"]
                or self._target["work_spec_digest"] != payload["work_spec_digest"]
            ):
                raise ApplicationError("Owner checkpoint differs from native Run", non_retryable=True)
            self._status = "awaiting_human"
        await workflow.wait_condition(
            lambda: self._status in {"running", "cancelled"} or self._rollover_ready(),
        )
        if self._rollover_ready():
            await self._continue_as_new()
        await workflow.wait_condition(workflow.all_handlers_finished)
        if self._status == "cancelled":
            return self._cancel_receipt
        result = await graph(CURATION_SUCCESSOR_GRAPH_NAME).compile().ainvoke(payload)
        if not isinstance(result, dict) or result.get("work_run_id") != payload["work_run_id"]:
            raise ApplicationError("Successor returned no owner Work result", non_retryable=True)
        self._status = "completed"
        await workflow.wait_condition(workflow.all_handlers_finished)
        return result

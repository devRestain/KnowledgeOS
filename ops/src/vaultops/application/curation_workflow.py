"""SDK-only bounded Team Workflow; owner effects remain in node Activities."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ActivityError, ApplicationError, TimeoutError

with workflow.unsafe.imports_passed_through():
    from temporalio.langgraph import graph

from .temporal_contract import (
    CURATION_DISPATCH_FIELDS,
    CURATION_GRAPH_NAME,
    CURATION_MARK_UNKNOWN_ACTIVITY,
    CURATION_RECONCILE_ACTIVITY,
    CURATION_RECOVERY_QUERY,
    CURATION_RECOVERY_UPDATE,
    TEAM_WORKFLOW_TYPE,
    curation_recovery_target,
)


@workflow.defn(name=TEAM_WORKFLOW_TYPE)
class KnowledgeOSCurationTeamWorkflow:
    def __init__(self) -> None:
        self._dispatch: dict[str, Any] | None = None
        self._status = "running"
        self._failed_activity_id: str | None = None
        self._recovery_id: str | None = None
        self._receipt: dict[str, Any] | None = None

    @workflow.query(name=CURATION_RECOVERY_QUERY)
    def recovery_status(self) -> dict[str, Any]:
        return {
            "status": self._status,
            "failed_activity_id": self._failed_activity_id,
            "temporal_run_id": workflow.info().run_id,
        }

    def _validate_recovery(self, payload: dict[str, Any]) -> None:
        target = curation_recovery_target(payload)
        if (
            self._status not in {"waiting", "applying", "completed"}
            or self._failed_activity_id is None
            or self._dispatch is None
            or target["temporal_run_id"] != workflow.info().run_id
            or target["activity_id"] != self._failed_activity_id
            or any(target[key] != self._dispatch[key] for key in CURATION_DISPATCH_FIELDS)
            or (self._recovery_id is not None and self._recovery_id != payload["recovery_id"])
        ):
            raise ValueError("Recovery target differs from this failed native Activity")

    @workflow.update(name=CURATION_RECOVERY_UPDATE)
    async def reconcile(self, payload: dict[str, Any]) -> dict[str, Any]:
        self._validate_recovery(payload)
        await workflow.wait_condition(lambda: self._status != "applying")
        self._validate_recovery(payload)
        if self._receipt is not None:
            return self._receipt
        self._recovery_id = payload["recovery_id"]
        self._status = "applying"
        try:
            receipt = await workflow.execute_activity(
                CURATION_RECONCILE_ACTIVITY,
                payload,
                start_to_close_timeout=timedelta(minutes=10),
                retry_policy=RetryPolicy(maximum_attempts=1),
            )
        except ActivityError:
            self._recovery_id = None
            self._status = "waiting"
            raise
        self._receipt = receipt
        self._status = "completed"
        return receipt

    @reconcile.validator
    def validate_reconcile(self, payload: dict[str, Any]) -> None:
        self._validate_recovery(payload)

    @workflow.run
    async def run(self, payload: dict[str, Any]) -> dict[str, Any]:
        self._dispatch = payload
        try:
            result = await graph(CURATION_GRAPH_NAME).compile().ainvoke(payload)
            self._status = "completed"
            return result
        except ActivityError as exc:
            if (
                exc.activity_type
                not in {
                    CURATION_GRAPH_NAME + ".normalization_proposal",
                    CURATION_GRAPH_NAME + ".assess",
                }
                or not exc.activity_id
            ):
                raise
            self._failed_activity_id = exc.activity_id
            failure_kind = None
            if workflow.patched("knowledgeos-native-lifecycle-v1"):
                if isinstance(exc.cause, TimeoutError):
                    failure_kind = "timeout"
                elif isinstance(exc.cause, ApplicationError):
                    failure_kind = "failure"
            await workflow.execute_activity(
                CURATION_MARK_UNKNOWN_ACTIVITY,
                {
                    "dispatch": payload,
                    "failed_activity_id": exc.activity_id,
                    "temporal_run_id": workflow.info().run_id,
                    **({"failure_kind": failure_kind} if failure_kind is not None else {}),
                },
                start_to_close_timeout=timedelta(minutes=1),
                retry_policy=RetryPolicy(maximum_attempts=1),
            )
            self._status = "waiting"
            await workflow.wait_condition(lambda: self._receipt is not None)
            await workflow.wait_condition(workflow.all_handlers_finished)
            if self._receipt is None:
                raise ApplicationError("Recovery produced no owner receipt", non_retryable=True)
            return self._receipt

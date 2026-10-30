"""Native SDK Workflow; imported only when composing an explicitly selected Worker."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from temporalio import workflow
from temporalio.common import RetryPolicy

from .temporal_contract import ACTIVITY_TYPE, WORKFLOW_TYPE


@workflow.defn(name=WORKFLOW_TYPE)
class KnowledgeWorkWorkflow:
    @workflow.run
    async def run(self, payload: dict[str, Any]) -> dict[str, Any]:
        # No automatic Activity retry: effects require owner reconciliation.
        return await workflow.execute_activity(
            ACTIVITY_TYPE, payload,
            start_to_close_timeout=timedelta(minutes=10),
            retry_policy=RetryPolicy(maximum_attempts=1),
        )

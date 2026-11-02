"""SDK-only conditional continuation; every decision and effect is owner-admitted."""

from __future__ import annotations

from typing import Any

from temporalio import workflow
from temporalio.exceptions import ApplicationError

with workflow.unsafe.imports_passed_through():
    from temporalio.langgraph import graph

from .temporal_contract import (
    CONTINUATION_WORKFLOW_TYPE,
    CURATION_INITIAL_GRAPH_NAME,
    CURATION_SUCCESSOR_GRAPH_NAME,
)


@workflow.defn(name=CONTINUATION_WORKFLOW_TYPE)
class KnowledgeOSCurationContinuationWorkflow:
    @workflow.run
    async def run(self, payload: dict[str, Any]) -> dict[str, Any]:
        decision = await graph(CURATION_INITIAL_GRAPH_NAME).compile().ainvoke(payload)
        if (
            not isinstance(decision, dict)
            or set(decision) != {"continuation_state", "receipt"}
            or not isinstance(decision["receipt"], dict)
        ):
            raise ApplicationError("Initial assessment returned no owner decision", non_retryable=True)
        if decision["continuation_state"] == "human_required":
            return decision["receipt"]
        if decision["continuation_state"] != "allowed":
            raise ApplicationError("Owner did not admit continuation", non_retryable=True)
        # The successor receives the exact original dispatch, never an assessment
        # output reinterpreted as authority or a manager-supplied predecessor.
        return await graph(CURATION_SUCCESSOR_GRAPH_NAME).compile().ainvoke(payload)

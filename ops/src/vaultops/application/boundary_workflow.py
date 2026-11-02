"""SDK-only immutable Exchange and Maintenance official sequential native Graphs."""
from __future__ import annotations

from temporalio import workflow

with workflow.unsafe.imports_passed_through():
    from temporalio.langgraph import graph

from .temporal_contract import (
    CURATION_DISPATCH_FIELDS,
    EXCHANGE_GRAPH_NAME,
    EXCHANGE_WORKFLOW_TYPE,
    MAINTENANCE_GRAPH_NAME,
    MAINTENANCE_WORKFLOW_TYPE,
)


def _payload(payload):
    if (not isinstance(payload, dict) or set(payload) != CURATION_DISPATCH_FIELDS
            or payload.get("owner_operation_id") != "knowledgeos"
            or any(not isinstance(value, str) or not value for value in payload.values())):
        raise ValueError("Boundary payload differs from exact owner dispatch")
    return payload


@workflow.defn(name=EXCHANGE_WORKFLOW_TYPE)
class KnowledgeOSExchangeWorkflow:
    @workflow.run
    async def run(self, payload: dict) -> dict:
        return await graph(EXCHANGE_GRAPH_NAME).compile().ainvoke(_payload(payload))


@workflow.defn(name=MAINTENANCE_WORKFLOW_TYPE)
class KnowledgeOSMaintenanceWorkflow:
    @workflow.run
    async def run(self, payload: dict) -> dict:
        return await graph(MAINTENANCE_GRAPH_NAME).compile().ainvoke(_payload(payload))

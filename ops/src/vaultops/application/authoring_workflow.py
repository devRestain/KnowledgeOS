"""Deterministic SDK-only authoring Workflows; owner work runs in Activities."""
from __future__ import annotations

from typing import Any

from temporalio import workflow

with workflow.unsafe.imports_passed_through():
    from temporalio.langgraph import graph

from .temporal_contract import (
    AUTHORING_DRAFT_GRAPH_NAME,
    AUTHORING_DRAFT_WORKFLOW_TYPE,
    AUTHORING_REVIEW_GRAPH_NAME,
    AUTHORING_REVIEW_WORKFLOW_TYPE,
    CURATION_DISPATCH_FIELDS,
)


def _validate(payload):
    if (not isinstance(payload, dict) or set(payload) != CURATION_DISPATCH_FIELDS
            or payload.get("owner_operation_id") != "knowledgeos"
            or any(not isinstance(value, str) or not value for value in payload.values())):
        raise ValueError("Authoring payload differs from exact owner dispatch")


@workflow.defn(name=AUTHORING_DRAFT_WORKFLOW_TYPE)
class KnowledgeOSCitedDraftWorkflow:
    @workflow.run
    async def run(self, payload: dict[str, Any]) -> dict[str, Any]:
        _validate(payload)
        return await graph(AUTHORING_DRAFT_GRAPH_NAME).compile().ainvoke(payload)


@workflow.defn(name=AUTHORING_REVIEW_WORKFLOW_TYPE)
class KnowledgeOSProjectReviewWorkflow:
    @workflow.run
    async def run(self, payload: dict[str, Any]) -> dict[str, Any]:
        _validate(payload)
        return await graph(AUTHORING_REVIEW_GRAPH_NAME).compile().ainvoke(payload)

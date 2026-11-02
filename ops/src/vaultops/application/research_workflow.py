"""SDK-only immutable bounded Research Workflow; all owner operations are Activities."""
from __future__ import annotations

from typing import Any

from temporalio import workflow

with workflow.unsafe.imports_passed_through():
    from temporalio.langgraph import graph

from .temporal_contract import CURATION_DISPATCH_FIELDS, RESEARCH_GRAPH_NAME, RESEARCH_WORKFLOW_TYPE


@workflow.defn(name=RESEARCH_WORKFLOW_TYPE)
class KnowledgeOSEvidenceResearchWorkflow:
    @workflow.run
    async def run(self, payload: dict[str, Any]) -> dict[str, Any]:
        if (not isinstance(payload, dict) or set(payload) != CURATION_DISPATCH_FIELDS
                or payload.get("owner_operation_id") != "knowledgeos"
                or any(not isinstance(value, str) or not value for value in payload.values())):
            raise ValueError("Research payload differs from exact owner dispatch")
        return await graph(RESEARCH_GRAPH_NAME).compile().ainvoke(payload)

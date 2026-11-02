"""Deterministic SDK-only module-level metadata Workflows."""
from __future__ import annotations

from temporalio import workflow

with workflow.unsafe.imports_passed_through():
    from temporalio.langgraph import graph

from .temporal_contract import (
    DAILY_GRAPH_NAME,
    DAILY_WORKFLOW_TYPE,
    METADATA_GRAPH_NAME,
    METADATA_WORKFLOW_TYPE,
    RELATION_GRAPH_NAME,
    RELATION_WORKFLOW_TYPE,
    curation_dispatch,
)


@workflow.defn(name=METADATA_WORKFLOW_TYPE)
class KnowledgeOSNoteMetadataWorkflow:
    @workflow.run
    async def run(self, payload: dict) -> dict:
        return await graph(METADATA_GRAPH_NAME).compile().ainvoke(curation_dispatch(payload))


@workflow.defn(name=RELATION_WORKFLOW_TYPE)
class KnowledgeOSRelationOntologyWorkflow:
    @workflow.run
    async def run(self, payload: dict) -> dict:
        return await graph(RELATION_GRAPH_NAME).compile().ainvoke(curation_dispatch(payload))


@workflow.defn(name=DAILY_WORKFLOW_TYPE)
class KnowledgeOSDailyCorpusWorkflow:
    @workflow.run
    async def run(self, payload: dict) -> dict:
        return await graph(DAILY_GRAPH_NAME).compile().ainvoke(curation_dispatch(payload))

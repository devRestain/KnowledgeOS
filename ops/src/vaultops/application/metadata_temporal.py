"""Trusted concrete metadata method selection over the official plugin graph."""
from .metadata_work import DAILY_METHOD_ID, METADATA_METHOD_ID, RELATION_METHOD_ID
from .native_team_temporal import native_worker_components
from .temporal_contract import (
    DAILY_GRAPH_NAME,
    DAILY_WORKFLOW_TYPE,
    METADATA_GRAPH_NAME,
    METADATA_WORKFLOW_TYPE,
    RELATION_GRAPH_NAME,
    RELATION_WORKFLOW_TYPE,
)


def metadata_worker_components(exops, executor):
    from .metadata_workflow import (
        KnowledgeOSDailyCorpusWorkflow,
        KnowledgeOSNoteMetadataWorkflow,
        KnowledgeOSRelationOntologyWorkflow,
    )

    selected = {
        METADATA_METHOD_ID: (KnowledgeOSNoteMetadataWorkflow, METADATA_GRAPH_NAME, METADATA_WORKFLOW_TYPE),
        RELATION_METHOD_ID: (KnowledgeOSRelationOntologyWorkflow, RELATION_GRAPH_NAME, RELATION_WORKFLOW_TYPE),
        DAILY_METHOD_ID: (KnowledgeOSDailyCorpusWorkflow, DAILY_GRAPH_NAME, DAILY_WORKFLOW_TYPE),
    }[executor.policy.method_id]
    return native_worker_components(exops, executor, workflow_class=selected[0], graph_name=selected[1], workflow_type=selected[2])

"""Select exact immutable authoring Workflows on the shared official native binding."""
from __future__ import annotations

from ..adapters.core import AdmissionError
from .temporal_contract import (
    AUTHORING_DRAFT_GRAPH_NAME,
    AUTHORING_DRAFT_WORKFLOW_TYPE,
    AUTHORING_REVIEW_GRAPH_NAME,
    AUTHORING_REVIEW_WORKFLOW_TYPE,
)


def authoring_worker_components(exops, executor):
    from .authoring_work import DRAFT_METHOD, REVIEW_METHOD
    from .authoring_workflow import KnowledgeOSCitedDraftWorkflow, KnowledgeOSProjectReviewWorkflow
    from .native_team_temporal import native_worker_components
    choices = {
        DRAFT_METHOD: (KnowledgeOSCitedDraftWorkflow, AUTHORING_DRAFT_WORKFLOW_TYPE, AUTHORING_DRAFT_GRAPH_NAME),
        REVIEW_METHOD: (KnowledgeOSProjectReviewWorkflow, AUTHORING_REVIEW_WORKFLOW_TYPE, AUTHORING_REVIEW_GRAPH_NAME),
    }
    if executor.policy.method_id not in choices:
        raise AdmissionError("BINDING_INVALID", "Unknown authoring method")
    workflow, wire, graph_name = choices[executor.policy.method_id]
    return native_worker_components(exops, executor, workflow_class=workflow, graph_name=graph_name, workflow_type=wire)

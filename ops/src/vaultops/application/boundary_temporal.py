"""Explicit official Temporal-backed LangGraph selections for boundary Teams."""
from __future__ import annotations

from .native_team_temporal import native_worker_components
from .temporal_contract import (
    EXCHANGE_GRAPH_NAME,
    EXCHANGE_WORKFLOW_TYPE,
    MAINTENANCE_GRAPH_NAME,
    MAINTENANCE_WORKFLOW_TYPE,
)


def exchange_worker_components(exops, executor):
    from .boundary_workflow import KnowledgeOSExchangeWorkflow

    return native_worker_components(exops, executor, workflow_class=KnowledgeOSExchangeWorkflow,
                                    graph_name=EXCHANGE_GRAPH_NAME, workflow_type=EXCHANGE_WORKFLOW_TYPE)


def maintenance_worker_components(exops, executor):
    from .boundary_workflow import KnowledgeOSMaintenanceWorkflow

    return native_worker_components(exops, executor, workflow_class=KnowledgeOSMaintenanceWorkflow,
                                    graph_name=MAINTENANCE_GRAPH_NAME, workflow_type=MAINTENANCE_WORKFLOW_TYPE)

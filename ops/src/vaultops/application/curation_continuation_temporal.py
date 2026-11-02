"""Opt-in official Temporal/LangGraph binding for criterion-linked continuation.

SDK imports are lazy. Module-level bound methods retain official plugin task
identities without closures, mutable global owners, or uncertain effect retries.
"""

from __future__ import annotations

import asyncio
from contextlib import suppress
from datetime import timedelta
from importlib.metadata import PackageNotFoundError, version
from typing import TYPE_CHECKING, Any

from ..adapters.core import AdmissionError
from .exops import KnowledgeExOps
from .temporal_contract import (
    CONTINUATION_METHOD_ID,
    CONTINUATION_WORKFLOW_TYPE,
    CONTROL_WORKFLOW_TYPE,
    CURATION_DISPATCH_FIELDS,
    CURATION_INITIAL_GRAPH_NAME,
    CURATION_SUCCESSOR_GRAPH_NAME,
)
from .temporal_work import WorkResult

if TYPE_CHECKING:
    from .curation_continuation import CurationContinuationExecutor


class _ContinuationActivities:
    def __init__(
        self, exops: KnowledgeExOps, executor: CurationContinuationExecutor,
        *, workflow_type: str = CONTINUATION_WORKFLOW_TYPE, controller=None,
    ):
        self.exops = exops
        self.executor = executor
        self.workflow_type = workflow_type
        self.controller = controller

    def current_record(self, payload: dict[str, Any], node: str, *, started: bool) -> dict:
        from temporalio import activity

        if (
            not isinstance(payload, dict)
            or set(payload) != CURATION_DISPATCH_FIELDS
            or any(not isinstance(value, str) or not value for value in payload.values())
            or payload["owner_operation_id"] != "knowledgeos"
        ):
            raise AdmissionError("INVALID_PAYLOAD", "Continuation payload differs from dispatch")
        info = activity.info()
        graph_name = (
            CURATION_INITIAL_GRAPH_NAME
            if node in {"manager", "capture_evidence", "assess_initial"}
            else CURATION_SUCCESSOR_GRAPH_NAME
        )
        record = self.exops.application.journal.read()["intents"].get(
            "exops.work." + payload["work_run_id"]
        )
        if (
            record is None
            or record["spec"]["target_kind"] != "team"
            or record["spec"].get("method_contract_ref", {}).get("method_id")
            != CONTINUATION_METHOD_ID
            or record["action_id"] != payload["action_id"]
            or record["content_digest"] != payload["content_digest"]
            or record["spec"]["spec_digest"] != payload["work_spec_digest"]
            or info.workflow_id != "knowledgeos/" + payload["work_run_id"]
            or info.workflow_type != self.workflow_type
            or info.activity_type != graph_name + "." + node
            or (started and record["application_state"] != "started")
            or (
                self.workflow_type == CONTINUATION_WORKFLOW_TYPE
                and record.get("control_checkpoint") is True
            )
            or (
                self.workflow_type != CONTINUATION_WORKFLOW_TYPE
                and (
                    self.workflow_type != CONTROL_WORKFLOW_TYPE
                    or record.get("control_checkpoint") is not True
                    or not getattr(info, "workflow_run_id", "")
                )
            )
        ):
            raise AdmissionError("BINDING_INVALID", "Activity differs from current continuation Work")
        if self.workflow_type == CONTROL_WORKFLOW_TYPE and record.get("temporal_run_id") is not None:
            if self.controller is None:
                raise AdmissionError("BINDING_INVALID", "Controlled Activity requires its owner")
            self.controller.authorize_native_run(
                payload["work_run_id"], temporal_run_id=info.workflow_run_id,
            )
        if (
            self.workflow_type == CONTROL_WORKFLOW_TYPE
            and node not in {"manager", "capture_evidence", "assess_initial"}
        ):
            if self.controller is None:
                raise AdmissionError("BINDING_INVALID", "Successor requires exact owner release")
            self.controller.authorize_successor(
                payload["work_run_id"], temporal_run_id=info.workflow_run_id,
            )
        if started:
            # Revalidate current Director/source/Team/runtime/actor authority at
            # every Activity boundary, not merely at the initial dispatch.
            return self.executor._current(payload["work_run_id"])
        return record

    def unknown(self, payload: dict[str, Any]) -> None:
        self.exops.worker_unknown(
            payload["work_run_id"],
            worker_identity=self.exops.worker_identity,
            action_id=payload["action_id"],
        )

    async def manager(self, payload: dict[str, Any]) -> dict[str, Any]:
        from temporalio import activity

        record = self.current_record(payload, "manager", started=False)
        run = self.exops.worker_started(
            payload["work_run_id"],
            worker_identity=self.exops.worker_identity,
            action_id=payload["action_id"],
            content_digest=payload["content_digest"],
            work_spec_digest=payload["work_spec_digest"],
            workflow_id=activity.info().workflow_id,
        )
        try:
            await self.executor.manager(record["spec"], run)
            return payload
        except Exception:
            self.unknown(payload)
            raise

    async def capture_evidence(self, payload: dict[str, Any]) -> dict[str, Any]:
        self.current_record(payload, "capture_evidence", started=True)
        try:
            await self.executor.capture_evidence(payload["work_run_id"])
            return payload
        except Exception:
            self.unknown(payload)
            raise

    async def assess_initial(self, payload: dict[str, Any]) -> dict[str, Any]:
        self.current_record(payload, "assess_initial", started=True)
        try:
            return await self.executor.assess_initial(payload["work_run_id"])
        except Exception:
            self.unknown(payload)
            raise

    async def manager_successor(self, payload: dict[str, Any]) -> dict[str, Any]:
        self.current_record(payload, "manager_successor", started=True)
        try:
            await self.executor.manager_successor(payload["work_run_id"])
            return payload
        except Exception:
            self.unknown(payload)
            raise

    async def normalization_proposal(self, payload: dict[str, Any]) -> dict[str, Any]:
        from temporalio import activity

        self.current_record(payload, "normalization_proposal", started=True)

        async def heartbeat() -> None:
            while True:
                activity.heartbeat(
                    {
                        "work_run_id": payload["work_run_id"],
                        "action_id": payload["action_id"],
                        "activity_id": activity.info().activity_id,
                    }
                )
                await asyncio.sleep(5)

        heartbeats = asyncio.create_task(heartbeat())
        try:
            await self.executor.normalization_proposal(payload["work_run_id"])
            return payload
        except (Exception, asyncio.CancelledError):
            self.unknown(payload)
            raise
        finally:
            heartbeats.cancel()
            with suppress(asyncio.CancelledError):
                await heartbeats

    async def assess(self, payload: dict[str, Any]) -> dict[str, Any]:
        self.current_record(payload, "assess", started=True)
        try:
            result = await self.executor.assess(payload["work_run_id"])
            if not isinstance(result, WorkResult):
                raise AdmissionError("INVALID_PAYLOAD", "Continuation returned no owner WorkResult")
            return self.exops.worker_finished(
                payload["work_run_id"],
                worker_identity=self.exops.worker_identity,
                action_id=payload["action_id"],
                next_run=result.next_run,
                assessment=result.assessment,
                segments=result.segments,
                evaluations=result.evaluations,
                artifacts=result.artifacts,
                checkpoints=result.checkpoints,
                candidate_assessor_id=result.candidate_assessor_id,
            )
        except Exception:
            self.unknown(payload)
            raise


def continuation_worker_components(
    exops: KnowledgeExOps, executor: CurationContinuationExecutor, *,
    control_mode: bool = False, controller=None,
):
    """Compose exact initial/successor graphs with owner-gated control release."""
    for package, selected in (
        ("temporalio", "1.34.0"),
        ("temporalio-langgraph", "0.1.0"),
        ("langgraph", "1.2.0"),
    ):
        try:
            installed = version(package)
        except PackageNotFoundError as exc:
            raise AdmissionError("BINDING_INVALID", f"Continuation requires {package}=={selected}") from exc
        if installed != selected:
            raise AdmissionError("BINDING_INVALID", f"Continuation requires {package}=={selected}")
    if not all(
        callable(getattr(executor, name, None))
        for name in (
            "_current", "manager", "capture_evidence", "assess_initial",
            "manager_successor", "normalization_proposal", "assess",
        )
    ):
        raise AdmissionError("BINDING_INVALID", "Continuation requires its owner executor")

    from langgraph.graph import END, START, StateGraph
    from temporalio.common import RetryPolicy
    from temporalio.langgraph import LangGraphPlugin

    if control_mode:
        from .curation_control_workflow import KnowledgeOSCurationControlWorkflow
        selected_workflow = KnowledgeOSCurationControlWorkflow
    else:
        from .curation_continuation_workflow import KnowledgeOSCurationContinuationWorkflow
        selected_workflow = KnowledgeOSCurationContinuationWorkflow
    activities = _ContinuationActivities(
        exops, executor,
        controller=controller,
        workflow_type=CONTROL_WORKFLOW_TYPE if control_mode else CONTINUATION_WORKFLOW_TYPE,
    )
    graphs = {}
    selected_graphs = [
        (CURATION_INITIAL_GRAPH_NAME, ("manager", "capture_evidence", "assess_initial")),
    ]
    selected_graphs.append(
        (CURATION_SUCCESSOR_GRAPH_NAME, ("manager_successor", "normalization_proposal", "assess"))
    )
    for graph_name, names in selected_graphs:
        selected_graph = StateGraph(dict)
        previous = START
        for name in names:
            selected_graph.add_node(
                name,
                getattr(activities, name),
                metadata={
                    "execute_in": "activity",
                    "start_to_close_timeout": timedelta(minutes=10),
                    "retry_policy": RetryPolicy(maximum_attempts=1),
                    **(
                        {"heartbeat_timeout": timedelta(seconds=15)}
                        if name == "normalization_proposal"
                        else {}
                    ),
                },
            )
            selected_graph.add_edge(previous, name)
            previous = name
        selected_graph.add_edge(previous, END)
        graphs[graph_name] = selected_graph
    return selected_workflow, LangGraphPlugin(graphs=graphs)

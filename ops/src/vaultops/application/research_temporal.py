"""Explicit official Temporal-backed LangGraph binding for bounded Research."""
from __future__ import annotations

import asyncio
from contextlib import suppress
from datetime import timedelta
from importlib.metadata import PackageNotFoundError, version
from typing import TYPE_CHECKING, Any

from ..adapters.core import AdmissionError
from .exops import KnowledgeExOps
from .temporal_contract import (
    CURATION_DISPATCH_FIELDS,
    RESEARCH_GRAPH_NAME,
    RESEARCH_METHOD_ID,
    RESEARCH_WORKFLOW_TYPE,
)
from .temporal_work import WorkResult

if TYPE_CHECKING:
    from .research_work import ResearchTeamExecutor


class _ResearchActivities:
    """Importable task identities with an instance-local trusted owner binding."""

    def __init__(self, exops: KnowledgeExOps, executor: ResearchTeamExecutor):
        self.exops = exops
        self.executor = executor

    def current_record(self, payload: dict[str, Any], *, started: bool) -> dict[str, Any]:
        from temporalio import activity

        if (
            not isinstance(payload, dict)
            or set(payload) != CURATION_DISPATCH_FIELDS
            or payload["owner_operation_id"] != "knowledgeos"
        ):
            raise AdmissionError(
                "INVALID_PAYLOAD", "Team Worker payload differs from owner dispatch"
            )
        info = activity.info()
        record = self.exops.application.journal.read()["intents"].get(
            "exops.work." + payload["work_run_id"]
        )
        if (
            record is None
            or record["spec"]["target_kind"] != "team"
            or record["spec"].get("team_id") != "evidence-research"
            or record["spec"].get("method_contract_ref", {}).get("method_id") != RESEARCH_METHOD_ID
            or record["action_id"] != payload["action_id"]
            or record["content_digest"] != payload["content_digest"]
            or record["spec"]["spec_digest"] != payload["work_spec_digest"]
            or info.workflow_id != "knowledgeos/" + payload["work_run_id"]
            or info.workflow_type != RESEARCH_WORKFLOW_TYPE
            or (started and record["application_state"] != "started")
        ):
            raise AdmissionError(
                "BINDING_INVALID", "Team Activity differs from its current owner Work"
            )
        return record

    def unknown(self, payload: dict[str, Any]) -> None:
        self.exops.worker_unknown(
            payload["work_run_id"],
            worker_identity=self.exops.worker_identity,
            action_id=payload["action_id"],
        )

    async def manager(self, payload: dict[str, Any]) -> dict[str, Any]:
        from temporalio import activity

        record = self.current_record(payload, started=False)
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

    async def retrieve_evidence(self, payload: dict[str, Any]) -> dict[str, Any]:
        from temporalio import activity

        self.current_record(payload, started=True)

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
            await self.executor.retrieve_evidence(payload["work_run_id"])
            return payload
        except (Exception, asyncio.CancelledError):
            self.unknown(payload)
            raise
        finally:
            heartbeats.cancel()
            with suppress(asyncio.CancelledError):
                await heartbeats

    async def answer_artifact(self, payload: dict[str, Any]) -> dict[str, Any]:
        from temporalio import activity

        self.current_record(payload, started=True)

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
            await self.executor.answer_artifact(payload["work_run_id"])
            return payload
        except (Exception, asyncio.CancelledError):
            self.unknown(payload)
            raise
        finally:
            heartbeats.cancel()
            with suppress(asyncio.CancelledError):
                await heartbeats

    async def assess(self, payload: dict[str, Any]) -> dict[str, Any]:
        self.current_record(payload, started=True)
        try:
            result = await self.executor.assess(payload["work_run_id"])
            if not isinstance(result, WorkResult):
                raise AdmissionError(
                    "INVALID_PAYLOAD", "Team executor returned no owner WorkResult"
                )
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


def research_team_graph(exops: KnowledgeExOps, executor: ResearchTeamExecutor):
    """Build the exact graph for either a dedicated or composed official Worker."""
    for package, selected in (
        ("temporalio", "1.34.0"),
        ("temporalio-langgraph", "0.1.0"),
        ("langgraph", "1.2.0"),
    ):
        try:
            installed = version(package)
        except PackageNotFoundError as exc:
            raise AdmissionError(
                "BINDING_INVALID", f"Team Worker requires {package}=={selected}"
            ) from exc
        if installed != selected:
            raise AdmissionError("BINDING_INVALID", f"Team Worker requires {package}=={selected}")
    if not all(
        callable(getattr(executor, name, None))
        for name in ("manager", "retrieve_evidence", "answer_artifact", "assess")
    ):
        raise AdmissionError("BINDING_INVALID", "Team Worker requires its owner Research executor")

    from langgraph.graph import END, START, StateGraph
    from temporalio.common import RetryPolicy
    activities = _ResearchActivities(exops, executor)

    team_graph = StateGraph(dict)
    nodes = (
        ("manager", activities.manager),
        ("retrieve_evidence", activities.retrieve_evidence),
        ("answer_artifact", activities.answer_artifact),
        ("assess", activities.assess),
    )
    previous = START
    for name, node in nodes:
        team_graph.add_node(
            name,
            node,
            metadata={
                "execute_in": "activity",
                "start_to_close_timeout": timedelta(minutes=10),
                # An uncertain model or proposal effect requires owner reconciliation.
                "retry_policy": RetryPolicy(maximum_attempts=1),
                **(
                    {"heartbeat_timeout": timedelta(seconds=15)}
                    if name in {"retrieve_evidence", "answer_artifact"}
                    else {}
                ),
            },
        )
        team_graph.add_edge(previous, name)
        previous = name
    team_graph.add_edge(previous, END)
    return team_graph


def research_worker_components(exops: KnowledgeExOps, executor: ResearchTeamExecutor):
    """Return the unchanged dedicated Workflow and official graph plugin."""
    from temporalio.langgraph import LangGraphPlugin

    from .research_workflow import KnowledgeOSEvidenceResearchWorkflow

    team_graph = research_team_graph(exops, executor)
    return KnowledgeOSEvidenceResearchWorkflow, LangGraphPlugin(graphs={RESEARCH_GRAPH_NAME: team_graph})

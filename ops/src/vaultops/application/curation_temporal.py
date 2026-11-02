"""Explicit official Temporal-LangGraph binding for one bounded Curation Team.

Importing this module does not load an SDK or connect a service. The selected
Team image supplies the exact plugin and SDK when composing its Worker.
"""

from __future__ import annotations

import asyncio
from contextlib import suppress
from datetime import timedelta
from functools import partial
from importlib.metadata import PackageNotFoundError, version
from typing import TYPE_CHECKING, Any

from ..adapters.core import AdmissionError
from .exops import KnowledgeExOps
from .temporal_contract import (
    CURATION_DISPATCH_FIELDS,
    CURATION_GRAPH_NAME,
    CURATION_MARK_UNKNOWN_ACTIVITY,
    CURATION_RECONCILE_ACTIVITY,
    TEAM_WORKFLOW_TYPE,
    curation_recovery_target,
)
from .temporal_work import WorkResult

if TYPE_CHECKING:
    from .curation_work import CurationTeamExecutor


class _CurationActivities:
    """Importable task identities with an instance-local trusted owner binding."""

    def __init__(self, exops: KnowledgeExOps, executor: CurationTeamExecutor):
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
            or record["action_id"] != payload["action_id"]
            or record["content_digest"] != payload["content_digest"]
            or record["spec"]["spec_digest"] != payload["work_spec_digest"]
            or info.workflow_id != "knowledgeos/" + payload["work_run_id"]
            or info.workflow_type != TEAM_WORKFLOW_TYPE
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

    async def capture_evidence(self, payload: dict[str, Any]) -> dict[str, Any]:
        self.current_record(payload, started=True)
        try:
            await self.executor.capture_evidence(payload["work_run_id"])
            return payload
        except Exception:
            self.unknown(payload)
            raise

    async def normalization_proposal(self, payload: dict[str, Any]) -> dict[str, Any]:
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

    async def mark_unknown(self, payload: dict[str, Any]) -> dict[str, Any]:
        from temporalio import activity

        if (
            not isinstance(payload, dict)
            or set(payload) not in (
                {"dispatch", "failed_activity_id", "temporal_run_id"},
                {"dispatch", "failed_activity_id", "temporal_run_id", "failure_kind"},
            )
            or not isinstance(payload["failed_activity_id"], str)
            or not payload["failed_activity_id"]
            or payload.get("failure_kind") not in (None, "failure", "timeout")
        ):
            raise AdmissionError("INVALID_PAYLOAD", "Invalid Team failure payload")
        dispatch = payload["dispatch"]
        self.current_record(dispatch, started=False)
        info = activity.info()
        if (
            info.activity_type != CURATION_MARK_UNKNOWN_ACTIVITY
            or payload["temporal_run_id"] != info.workflow_run_id
        ):
            raise AdmissionError("BINDING_INVALID", "Team failure differs from native Run")
        self.executor.mark_unknown(
            dispatch["work_run_id"],
            action_id=dispatch["action_id"],
            temporal_run_id=info.workflow_run_id,
            activity_id=payload["failed_activity_id"],
            failure_kind=payload.get("failure_kind"),
        )
        return payload

    async def reconcile(self, payload: dict[str, Any]) -> dict[str, Any]:
        from temporalio import activity

        try:
            target = curation_recovery_target(payload)
        except ValueError as exc:
            raise AdmissionError("INVALID_PAYLOAD", str(exc)) from exc
        dispatch = {key: target[key] for key in CURATION_DISPATCH_FIELDS}
        self.current_record(dispatch, started=False)
        info = activity.info()
        intent = self.exops.application.journal.read()["intents"].get(
            "curation.recovery." + payload["recovery_id"]
        )
        if (
            info.activity_type != CURATION_RECONCILE_ACTIVITY
            or target["temporal_run_id"] != info.workflow_run_id
            or intent is None
            or intent.get("target") != target
        ):
            raise AdmissionError("BINDING_INVALID", "Recovery lacks exact owner native Run intent")
        return await self.executor.reconcile(
            target["work_run_id"],
            payload["recovery_id"],
            temporal_run_id=info.workflow_run_id,
            activity_id=target["activity_id"],
        )


def team_worker_components(exops: KnowledgeExOps, executor: CurationTeamExecutor):
    """Return the module-level Workflow and official four-Activity graph plugin."""
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
        for name in ("manager", "capture_evidence", "normalization_proposal", "assess")
    ):
        raise AdmissionError("BINDING_INVALID", "Team Worker requires its owner Curation executor")

    from langgraph.graph import END, START, StateGraph
    from temporalio import activity
    from temporalio.common import RetryPolicy
    from temporalio.langgraph import LangGraphPlugin

    from .curation_workflow import KnowledgeOSCurationTeamWorkflow

    activities = _CurationActivities(exops, executor)

    team_graph = StateGraph(dict)
    nodes = (
        ("manager", activities.manager),
        ("capture_evidence", activities.capture_evidence),
        ("normalization_proposal", activities.normalization_proposal),
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
                    if name == "normalization_proposal"
                    else {}
                ),
            },
        )
        team_graph.add_edge(previous, name)
        previous = name
    team_graph.add_edge(previous, END)
    plugin = LangGraphPlugin(graphs={CURATION_GRAPH_NAME: team_graph})
    # Partials keep SDK decoration local to this Worker, without mutating the
    # importable owner methods or wrapping their input in LangGraph ActivityInput.
    plugin.activities.extend(
        (
            activity.defn(name=CURATION_MARK_UNKNOWN_ACTIVITY)(partial(activities.mark_unknown)),
            activity.defn(name=CURATION_RECONCILE_ACTIVITY)(partial(activities.reconcile)),
        )
    )
    return KnowledgeOSCurationTeamWorkflow, plugin

"""KnowledgeOS-owned Temporal Workflow and Worker binding.

Importing this module has no SDK, network, or process side effect. The selected
project container must supply an exact Temporal SDK and scoped host client.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from ..adapters.core import AdmissionError
from .exops import KnowledgeExOps, ScopedTemporalClient

WORKFLOW_TYPE = "knowledgeos.work.v1"
ACTIVITY_TYPE = "knowledgeos.execute_admitted_work.v1"


@dataclass(frozen=True)
class WorkDispatch:
    owner_operation_id: str
    workflow_id: str
    action_id: str
    payload: dict[str, Any]


@dataclass(frozen=True)
class WorkResult:
    """An Operation executor supplies fully validated owner evidence."""

    next_run: Mapping[str, Any]
    assessment: Mapping[str, Any] | None = None
    segments: tuple[Mapping[str, Any], ...] = ()
    evaluations: tuple[Mapping[str, Any], ...] = ()
    artifacts: Mapping[str, Mapping[str, Any]] | None = None
    checkpoints: Mapping[str, Mapping[str, Any]] | None = None
    candidate_assessor_id: str | None = None


WorkExecutor = Callable[[Mapping[str, Any], Mapping[str, Any]], Awaitable[WorkResult]]


async def connect_owner_temporal(exops: KnowledgeExOps, binding: Any) -> ScopedTemporalClient:
    """Connect the host SDK transport to current KnowledgeOS owner admission.

    The caller must run in the selected SDK image, with ``clients`` installed
    from Services and a separately authenticated KnowledgeExOps composition.
    """
    if (binding.owner_operation_id != "knowledgeos"
            or binding.target_host != "server:7233"
            or binding.namespace != "knowledgeos"
            or binding.task_queue != "knowledgeos.work.v1"
            or binding.workflow_type != WORKFLOW_TYPE
            or binding.workflow_id_prefix != "knowledgeos/"
            or binding.tls is not False):
        raise AdmissionError("BINDING_INVALID", "Temporal client differs from the selected owner route")

    from clients.temporal import BindingError, TemporalDispatch, connect

    async def authorize(dispatch: TemporalDispatch) -> None:
        payload = dispatch.payload
        if (set(payload) != {"owner_operation_id", "work_run_id", "action_id",
                             "content_digest", "work_spec_digest"}
                or payload["owner_operation_id"] != "knowledgeos"
                or dispatch.workflow_id != f"knowledgeos/{payload['work_run_id']}"
                or dispatch.action_id != payload["action_id"]):
            raise BindingError("Temporal dispatch differs from the owner intent shape")
        record = exops.authorize_dispatch(
            work_run_id=payload["work_run_id"], action_id=dispatch.action_id,
            content_digest=payload["content_digest"],
        )
        if record["spec"]["spec_digest"] != payload["work_spec_digest"]:
            raise BindingError("Temporal dispatch differs from the admitted WorkSpec")

    return await connect(binding, authorize)


async def dispatch_admitted_work(exops: KnowledgeExOps, client: ScopedTemporalClient,
                                 work_run_id: str) -> dict[str, Any]:
    """Deliver one persisted start; ambiguous transport results stay unresolved."""
    record = exops.application.journal.read()["intents"].get("exops.work." + work_run_id)
    if record is None:
        raise AdmissionError("NOT_FOUND", "No owner-admitted WorkRun exists")
    record = exops.authorize_dispatch(work_run_id=work_run_id,
                                      action_id=record["action_id"],
                                      content_digest=record["content_digest"])
    workflow_id = f"knowledgeos/{work_run_id}"
    dispatch = WorkDispatch("knowledgeos", workflow_id, record["action_id"], {
        "owner_operation_id": "knowledgeos", "work_run_id": work_run_id,
        "action_id": record["action_id"], "content_digest": record["content_digest"],
        "work_spec_digest": record["spec"]["spec_digest"],
    })
    try:
        started_id = await client.start(dispatch)
    except Exception:
        current = exops.application.journal.read()["intents"]["exops.work." + work_run_id]
        if current["dispatch_state"] == "pending":
            exops.record_dispatch(work_run_id, action_id=record["action_id"], outcome="unknown")
        raise
    if started_id != workflow_id:
        exops.record_dispatch(work_run_id, action_id=record["action_id"], outcome="unknown")
        raise AdmissionError("DISPATCH_UNKNOWN", "Temporal returned another Workflow ID")
    exops.record_dispatch(work_run_id, action_id=record["action_id"],
                          outcome="accepted", workflow_id=workflow_id)
    return exops.application.journal.read()["intents"]["exops.work." + work_run_id]["receipt"].copy()


def worker_components(exops: KnowledgeExOps, execute_work: WorkExecutor):
    """Build SDK definitions only after a selected runtime imports Temporal."""
    if not callable(execute_work):
        raise AdmissionError("BINDING_INVALID", "Worker needs an Operation-owned executor")
    from temporalio import activity, workflow
    from temporalio.common import RetryPolicy

    @activity.defn(name=ACTIVITY_TYPE)
    async def execute_admitted_work(payload: dict[str, Any]) -> dict[str, Any]:
        if (not isinstance(payload, dict) or set(payload) != {
            "owner_operation_id", "work_run_id", "action_id", "content_digest", "work_spec_digest"
        } or payload["owner_operation_id"] != "knowledgeos"):
            raise AdmissionError("INVALID_PAYLOAD", "Worker payload has an unsupported owner shape")
        info = activity.info()
        workflow_id = info.workflow_id
        work_run_id = payload["work_run_id"]
        run = exops.worker_started(work_run_id, worker_identity=exops.worker_identity,
                                   action_id=payload["action_id"],
                                   content_digest=payload["content_digest"],
                                   workflow_id=workflow_id)
        record = exops.application.journal.read()["intents"]["exops.work." + work_run_id]
        if record["spec"]["spec_digest"] != payload["work_spec_digest"]:
            raise AdmissionError("PIN_MISMATCH", "WorkSpec changed after dispatch")
        try:
            result = await execute_work(record["spec"], run)
            if not isinstance(result, WorkResult):
                raise AdmissionError("INVALID_PAYLOAD", "Executor returned no owner WorkResult")
            return exops.worker_finished(
                work_run_id, worker_identity=exops.worker_identity,
                action_id=payload["action_id"], next_run=result.next_run,
                assessment=result.assessment, segments=result.segments,
                evaluations=result.evaluations, artifacts=result.artifacts,
                checkpoints=result.checkpoints,
                candidate_assessor_id=result.candidate_assessor_id,
            )
        except Exception:
            exops.worker_unknown(work_run_id, worker_identity=exops.worker_identity,
                                 action_id=payload["action_id"])
            raise

    @workflow.defn(name=WORKFLOW_TYPE)
    class KnowledgeWorkWorkflow:
        @workflow.run
        async def run(self, payload: dict[str, Any]) -> dict[str, Any]:
            # No automatic Activity retry: effects require owner reconciliation.
            return await workflow.execute_activity(
                ACTIVITY_TYPE, payload,
                start_to_close_timeout=timedelta(minutes=10),
                retry_policy=RetryPolicy(maximum_attempts=1),
            )

    return KnowledgeWorkWorkflow, execute_admitted_work


async def run_temporal_worker(sdk_client: Any, binding: Any, exops: KnowledgeExOps,
                              execute_work: WorkExecutor) -> None:
    """Run only under a separately selected project-owned service binding."""
    if (binding.owner_operation_id != "knowledgeos"
            or binding.workflow_type != WORKFLOW_TYPE
            or not binding.task_queue or not binding.task_queue.strip()
            or not binding.workflow_id_prefix.startswith("knowledgeos/")):
        raise AdmissionError("BINDING_INVALID", "Worker differs from the scoped host binding")
    from temporalio.worker import Worker

    workflow_type, activity_type = worker_components(exops, execute_work)
    worker = Worker(sdk_client, task_queue=binding.task_queue,
                    workflows=[workflow_type], activities=[activity_type])
    await worker.run()

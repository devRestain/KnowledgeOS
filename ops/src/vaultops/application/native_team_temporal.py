"""Official-plugin sequential native Team graph with importable Activity identities."""
from __future__ import annotations

import asyncio
from contextlib import suppress
from datetime import timedelta
from importlib.metadata import PackageNotFoundError, version

from ..adapters.core import AdmissionError
from .native_team import PACKAGES, require
from .temporal_contract import CURATION_DISPATCH_FIELDS
from .temporal_work import WorkResult


class NativeTeamActivities:
    def __init__(self, exops, executor, workflow_type):
        self.exops, self.executor, self.workflow_type = exops, executor, workflow_type

    def record(self, payload, *, started):
        from temporalio import activity

        require(isinstance(payload, dict) and set(payload) == CURATION_DISPATCH_FIELDS
                and payload["owner_operation_id"] == "knowledgeos", "Unsupported native dispatch payload")
        info = activity.info()
        record = self.exops.application.journal.read()["intents"].get("exops.work." + payload["work_run_id"])
        require(record is not None and record["spec"]["target_kind"] == "team"
                and record["spec"].get("team_id") == self.executor.policy.team_id
                and record["spec"].get("method_contract_ref", {}).get("method_id") == self.executor.policy.method_id
                and record["action_id"] == payload["action_id"]
                and record["content_digest"] == payload["content_digest"]
                and record["spec"]["spec_digest"] == payload["work_spec_digest"]
                and info.workflow_id == "knowledgeos/" + payload["work_run_id"]
                and info.workflow_type == self.workflow_type
                and (not started or record["application_state"] == "started"), "Native Activity owner identity differs")
        return record

    def unknown(self, payload):
        self.exops.worker_unknown(payload["work_run_id"], worker_identity=self.exops.worker_identity,
                                  action_id=payload["action_id"])

    async def manager(self, payload):
        from temporalio import activity

        record = self.record(payload, started=False)
        run = self.exops.worker_started(payload["work_run_id"], worker_identity=self.exops.worker_identity,
                                        action_id=payload["action_id"], content_digest=payload["content_digest"],
                                        work_spec_digest=payload["work_spec_digest"], workflow_id=activity.info().workflow_id)
        try:
            await self.executor.manager(record["spec"], run)
            return payload
        except (Exception, asyncio.CancelledError):
            self.unknown(payload)
            raise

    async def stage(self, payload, index):
        from temporalio import activity

        self.record(payload, started=True)

        async def heartbeat():
            while True:
                activity.heartbeat({"work_run_id": payload["work_run_id"], "action_id": payload["action_id"],
                                    "activity_id": activity.info().activity_id})
                await asyncio.sleep(5)

        task = asyncio.create_task(heartbeat())
        try:
            await self.executor.execute_stage(payload["work_run_id"], self.executor.policy.stages[index])
            return payload
        except (Exception, asyncio.CancelledError):
            self.unknown(payload)
            raise
        finally:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task

    async def first(self, payload):
        return await self.stage(payload, 0)

    async def second(self, payload):
        return await self.stage(payload, 1)

    async def assess(self, payload):
        self.record(payload, started=True)
        try:
            result = await self.executor.assess(payload["work_run_id"])
            require(isinstance(result, WorkResult), "Native assessor returned no WorkResult")
            return self.exops.worker_finished(payload["work_run_id"], worker_identity=self.exops.worker_identity,
                action_id=payload["action_id"], next_run=result.next_run, assessment=result.assessment,
                segments=result.segments, evaluations=result.evaluations, artifacts=result.artifacts,
                checkpoints=result.checkpoints, candidate_assessor_id=result.candidate_assessor_id)
        except (Exception, asyncio.CancelledError):
            self.unknown(payload)
            raise


def native_team_graph(exops, executor, *, workflow_type):
    for package, selected in PACKAGES.items():
        try:
            installed = version(package)
        except PackageNotFoundError as exc:
            raise AdmissionError("BINDING_INVALID", f"Native Team requires {package}=={selected}") from exc
        require(installed == selected, f"Native Team requires {package}=={selected}")

    from langgraph.graph import END, START, StateGraph
    from temporalio.common import RetryPolicy

    activities = NativeTeamActivities(exops, executor, workflow_type)
    graph = StateGraph(dict)
    previous = START
    for name, node in (("manager", activities.manager), (executor.policy.stages[0], activities.first),
                       (executor.policy.stages[1], activities.second), ("assess", activities.assess)):
        graph.add_node(name, node, metadata={"execute_in": "activity", "start_to_close_timeout": timedelta(minutes=10),
                       "retry_policy": RetryPolicy(maximum_attempts=1),
                       **({"heartbeat_timeout": timedelta(seconds=15)} if name in executor.policy.stages else {})})
        graph.add_edge(previous, name)
        previous = name
    graph.add_edge(previous, END)
    return graph


def native_worker_components(exops, executor, *, workflow_class, graph_name, workflow_type):
    from temporalio.langgraph import LangGraphPlugin

    graph = native_team_graph(exops, executor, workflow_type=workflow_type)
    return workflow_class, LangGraphPlugin(graphs={graph_name: graph})

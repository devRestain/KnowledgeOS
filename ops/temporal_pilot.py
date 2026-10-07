"""Disposable native Temporal binding check for the KnowledgeOS owner queue.

This file is deliberately independent of canonical KnowledgeOS State and tools.
"""

from __future__ import annotations

import asyncio
import os
import sys
from datetime import timedelta
from uuid import uuid4

from clients.temporal import BindingError, TemporalBinding, TemporalDispatch, connect
from temporalio import activity, workflow
from temporalio.client import Client, WorkflowQueryFailedError
from temporalio.worker import Worker

QUEUE = "knowledgeos.pilot.v1"


@activity.defn(name="knowledgeos.pilot.activity.v1")
async def acknowledge(payload: dict[str, str]) -> str:
    if payload != {"fixture": "agentfabric-temporal-pilot"}:
        raise ValueError("Synthetic pilot input differs from the fixed fixture")
    return "ack:" + payload["fixture"]


@workflow.defn(name="knowledgeos.pilot.v1")
class KnowledgeOSPilot:
    @workflow.run
    async def run(self, payload: dict[str, str]) -> str:
        return await workflow.execute_activity(
            acknowledge, payload, start_to_close_timeout=timedelta(seconds=30)
        )


@workflow.defn(name="knowledgeos.recovery.pilot.v1")
class KnowledgeOSRecoveryPilot:
    def __init__(self) -> None:
        self.released = False

    @workflow.run
    async def run(self) -> str:
        await workflow.wait_condition(lambda: self.released)
        return "ack:agentfabric-temporal-recovery"

    @workflow.signal
    def release(self) -> None:
        self.released = True

    @workflow.query
    def state(self) -> str:
        return "released" if self.released else "waiting"


async def main(mode: str) -> None:
    if (os.environ.get("AF_TEMPORAL_OWNER") != "knowledgeos"
            or os.environ.get("AF_TEMPORAL_NAMESPACE") != "knowledgeos"
            or os.environ.get("AF_TEMPORAL_ADDRESS") != "server:7233"):
        raise RuntimeError("Pilot requires the exact private KnowledgeOS binding")
    client = await Client.connect("server:7233", namespace="knowledgeos", tls=False)
    if mode == "worker":
        worker = Worker(client, task_queue=QUEUE,
                        workflows=[KnowledgeOSPilot, KnowledgeOSRecoveryPilot],
                        activities=[acknowledge])
        print("KnowledgeOS synthetic pilot Worker ready", flush=True)
        await worker.run()
    elif mode == "client":
        workflow_id = "knowledgeos/pilot/" + uuid4().hex
        binding = TemporalBinding(
            owner_operation_id="knowledgeos", target_host="server:7233",
            namespace="knowledgeos", task_queue=QUEUE,
            workflow_type="knowledgeos.pilot.v1", workflow_id_prefix="knowledgeos/pilot/",
            update_name="pilot.update.disabled", signal_name="pilot.signal.disabled",
            query_name="pilot.query.disabled", tls=False,
        )

        async def authorize(dispatch: TemporalDispatch) -> None:
            if (dispatch.workflow_id != workflow_id or dispatch.action_id != "pilot-start"
                    or dispatch.payload != {"fixture": "agentfabric-temporal-pilot"}):
                raise BindingError("Synthetic owner pilot denied dispatch")

        scoped = await connect(binding, authorize)
        try:
            await scoped.start(TemporalDispatch("other-operation", workflow_id,
                                               "pilot-start", {"fixture": "agentfabric-temporal-pilot"}))
        except BindingError:
            pass
        else:
            raise RuntimeError("Foreign-owner pilot dispatch was accepted")
        started_id = await scoped.start(TemporalDispatch(
            "knowledgeos", workflow_id, "pilot-start",
            {"fixture": "agentfabric-temporal-pilot"},
        ))
        if started_id != workflow_id:
            raise RuntimeError("Scoped client returned another Workflow ID")
        result = await asyncio.wait_for(client.get_workflow_handle(workflow_id).result(), 60)
        if result != "ack:agentfabric-temporal-pilot":
            raise RuntimeError("Pilot Workflow returned another result")
        print(f"PASS {workflow_id} {result}", flush=True)
    elif mode == "recovery-start":
        workflow_id = "knowledgeos/pilot/recovery/" + uuid4().hex
        handle = await client.start_workflow(
            KnowledgeOSRecoveryPilot.run, id=workflow_id, task_queue=QUEUE,
        )
        async with asyncio.timeout(30):
            while True:
                try:
                    state = await handle.query(KnowledgeOSRecoveryPilot.state)
                except WorkflowQueryFailedError:
                    await asyncio.sleep(0.25)
                    continue
                if state != "waiting":
                    raise RuntimeError(f"Recovery pilot entered unexpected state: {state}")
                break
        print(f"PASS {workflow_id} waiting", flush=True)
    elif mode in {"recovery-finish", "recovery-inspect"}:
        workflow_id = os.environ["AF_RECOVERY_WORKFLOW_ID"]
        if not workflow_id.startswith("knowledgeos/pilot/recovery/"):
            raise RuntimeError("Recovery pilot Workflow ID is outside the fixture prefix")
        handle = client.get_workflow_handle(workflow_id)
        if mode == "recovery-finish":
            state = await handle.query(KnowledgeOSRecoveryPilot.state)
            if state != "waiting":
                raise RuntimeError(f"Recovery pilot was not waiting: {state}")
            await handle.signal(KnowledgeOSRecoveryPilot.release)
        result = await asyncio.wait_for(handle.result(), 60)
        if result != "ack:agentfabric-temporal-recovery":
            raise RuntimeError("Recovery pilot Workflow returned another result")
        print(f"PASS {workflow_id} {result}", flush=True)
    else:
        raise ValueError("Unknown synthetic pilot mode")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit(
            "usage: temporal_pilot.py "
            "worker|client|recovery-start|recovery-finish|recovery-inspect"
        )
    asyncio.run(main(sys.argv[1]))

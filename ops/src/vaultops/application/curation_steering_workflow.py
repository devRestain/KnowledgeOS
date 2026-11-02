"""Deterministic native wait and acknowledged owner-command delivery."""
from __future__ import annotations

from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy

with workflow.unsafe.imports_passed_through():
    from langgraph.checkpoint.memory import InMemorySaver
    from temporalio.langgraph import graph

from .curation_steering_contract import (
    APPLY_ACTIVITY,
    BOUNDARY_ACTIVITY,
    INITIAL_GRAPH,
    PARTIAL_ACTIVITY,
    QUERY,
    SUCCESSOR_GRAPH,
    UPDATE,
    WORKFLOW,
)


@workflow.defn(name=WORKFLOW)
class KnowledgeOSCurationSteeringWorkflow:
    def __init__(self):
        self._dispatch = None
        self._status = "starting"
        self._applying = False
        self._commands = {}
        self._pending_graph_nodes = []

    @workflow.query(name=QUERY)
    def status(self):
        return {"status": self._status, "temporal_run_id": workflow.info().run_id,
                "commands": self._commands, "pending_graph_nodes": self._pending_graph_nodes}

    @workflow.update(name=UPDATE)
    async def command(self, payload: dict) -> dict:
        if (not isinstance(payload, dict) or set(payload) != {"work_run_id", "command_id", "target"}
                or self._dispatch is None or payload["work_run_id"] != self._dispatch["work_run_id"]
                or payload["target"].get("temporal_run_id") != workflow.info().run_id):
            raise ValueError("Command does not target this exact native WorkRun")
        await workflow.wait_condition(lambda: not self._applying)
        # Even duplicates reauthorize against current owner permissions and exact
        # durable receipts in the Activity; the Workflow cache is not authority.
        if self._status not in {"waiting", "released"} and payload["command_id"] not in self._commands:
            raise ValueError("No supported Profile boundary is open")
        self._applying = True
        try:
            if payload["command_id"] not in self._commands:
                # The owner authorizes kind and exact outbox in both Activities.
                # A no-op partial assessment for non-CUTOVER is not fabricated:
                # the adapter checks the admitted kind before choosing this call.
                result = await workflow.execute_activity(
                    APPLY_ACTIVITY, {"phase": "inspect", "dispatch": payload},
                    start_to_close_timeout=timedelta(seconds=30), retry_policy=RetryPolicy(maximum_attempts=1))
                if result["kind"] == "cutover":
                    await workflow.execute_activity(PARTIAL_ACTIVITY, payload,
                        start_to_close_timeout=timedelta(seconds=30), retry_policy=RetryPolicy(maximum_attempts=1))
            receipt = await workflow.execute_activity(
                APPLY_ACTIVITY, {"phase": "apply", "dispatch": payload},
                start_to_close_timeout=timedelta(seconds=30), retry_policy=RetryPolicy(maximum_attempts=1))
            self._commands[payload["command_id"]] = {"payload": payload, "receipt": receipt}
            if receipt["kind"] == "resume":
                self._status = "released"
            return receipt
        finally:
            self._applying = False

    @workflow.run
    async def run(self, dispatch: dict) -> dict:
        self._dispatch = dispatch
        initial = graph(INITIAL_GRAPH).compile(
            checkpointer=InMemorySaver(), interrupt_before=["normalization_report"])
        config = {"configurable": {"thread_id": workflow.info().workflow_id}}
        await initial.ainvoke(dispatch, config)
        checkpoint = await initial.aget_state(config)
        if checkpoint.next != ("normalization_report",):
            raise ValueError("Initial Graph did not stop at its admitted unscheduled Profile")
        self._pending_graph_nodes = list(checkpoint.next)
        await workflow.execute_activity(BOUNDARY_ACTIVITY, {
            "work_run_id": dispatch["work_run_id"], "pending_nodes": self._pending_graph_nodes,
        }, start_to_close_timeout=timedelta(seconds=30), retry_policy=RetryPolicy(maximum_attempts=1))
        self._status = "waiting"
        await workflow.wait_condition(lambda: self._status == "released" and not self._applying)
        self._status = "running_successor"
        # The interrupted initial Graph is intentionally never resumed: CUTOVER
        # retained its partial evidence and admitted a distinct immutable Graph.
        self._pending_graph_nodes = []
        result = await graph(SUCCESSOR_GRAPH).compile().ainvoke({"work_run_id": dispatch["work_run_id"]})
        self._status = "completed"
        await workflow.wait_condition(workflow.all_handlers_finished)
        return result

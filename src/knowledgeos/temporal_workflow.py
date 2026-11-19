"""Deterministic orchestration only; never import Owner, State, tools or model adapters here."""

from __future__ import annotations

from datetime import timedelta
from itertools import pairwise
from typing import Any, TypedDict

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ActivityError

with workflow.unsafe.imports_passed_through():
    from langgraph.graph import END, START, StateGraph
    from temporalio.langgraph import graph


class GraphState(TypedDict, total=False):
    work_id: str
    request_id: str | None
    actor: str | None
    binding_digest: str
    segment: int
    timeout: int
    done: dict[str, Any] | None
    assessment: dict[str, Any]
    remaining: dict[str, Any] | None


async def phase(state: dict[str, Any], operation: str, **fields: Any) -> dict[str, Any]:
    return await workflow.execute_activity(
        "knowledgeos.phase", {**state, "operation": operation, **fields},
        start_to_close_timeout=timedelta(seconds=state["timeout"]),
        retry_policy=RetryPolicy(maximum_attempts=1),
        activity_id=fields.get("phase_id", operation + "." + str(state.get("segment", 0))),
    )


def topology(steps: list[tuple[str, str]]) -> StateGraph:
    """The exact registered graph; the evaluation Profile has three durable subphases."""
    builder = StateGraph(GraphState)

    def producer(role: str):
        async def execute(state: GraphState) -> dict[str, Any]:
            if state.get("done"):
                return {}
            return await phase(state, "produce", role=role,
                               phase_id=f"s{state['segment']}.produce.{role}")
        return execute

    async def evaluate(state: GraphState) -> dict[str, Any]:
        if state.get("done"):
            return {}
        for operation in ("evaluate_static", "evaluate_model", "evaluate_commit"):
            result = await phase(state, operation, phase_id=f"s{state['segment']}.{operation}")
            if result.get("done") or operation == "evaluate_commit":
                return result
        return {}

    names = [name for name, _ in steps] + ["evaluation"]
    for name, role in steps:
        builder.add_node(name, producer(role), metadata={"execute_in": "workflow"})
    builder.add_node("evaluation", evaluate, metadata={"execute_in": "workflow"})
    builder.add_edge(START, names[0])
    for source, target in pairwise(names):
        builder.add_edge(source, target)
    builder.add_edge(names[-1], END)
    return builder


@workflow.defn(name="knowledgeos.work.v1")
class WorkWorkflow:
    def __init__(self) -> None:
        self.cancel_requested = False
        self.stage = "admitted"

    @workflow.signal
    def cancel(self) -> None:
        self.cancel_requested = True

    @workflow.query
    def progress(self) -> dict[str, Any]:
        return {"stage": self.stage, "cancel_requested": self.cancel_requested}

    @workflow.run
    async def run(self, state: dict[str, Any]) -> dict[str, Any]:
        used_segments = 0
        used_producers = 0
        gaps: dict[str, int] = {}
        try:
            admitted = await phase(state, "begin")
            if admitted.get("done"):
                return await phase(state, "settle", done=admitted["done"])
            budget = admitted["budget"]
            while True:
                if self.cancel_requested:
                    done = {"status": "cancelled", "notice": "Work cancelled at a safe Activity boundary"}
                    break
                self.stage = "composition"
                composed = await phase(state, "compose", phase_id=f"s{used_segments}.compose")
                if composed.get("done"):
                    done = composed["done"]
                    break
                opened = await phase(state, "open", steps=composed["steps"],
                                     phase_id=f"s{used_segments}.open")
                if opened.get("done"):
                    done = opened["done"]
                    break
                used_segments += 1
                used_producers += len(composed["steps"])
                state = {**state, "segment": opened["segment"]}
                self.stage = "graph"
                result = await graph(opened["graph_key"]).compile().ainvoke(state)
                if result.get("done"):
                    done = result["done"]
                    break
                assessment = result["assessment"]
                if assessment["status"] == "satisfied":
                    self.stage = "commit"
                    committed = await phase(state, "commit")
                    done = committed["done"]
                    if done["status"] == "completed" and admitted["team"]:
                        self.stage = "team_review"
                        reviewed = await phase(state, "review")
                        if reviewed.get("done"):
                            done = reviewed["done"]
                    break
                remaining = result["remaining"]
                signature = remaining["gap_signature"]
                gaps[signature] = gaps.get(signature, 0) + 1
                stop = None
                if used_segments >= budget["segments"]:
                    stop = f"segment budget of {budget['segments']} used"
                elif used_producers >= budget["producer_invocations"]:
                    stop = f"producer budget of {budget['producer_invocations']} used"
                elif gaps[signature] > budget["same_gap_repeats"]:
                    stop = "the same gap remained after a successor"
                if stop or not admitted["team"]:
                    done = {"status": "unmet", "outcome": {"unmet": assessment["unmet"],
                            "remaining_work": remaining["public"]},
                            "notice": "Result did not pass evaluation (" + (stop or "Officer evaluation")
                                      + "); nothing written: " + ", ".join(assessment["unmet"])}
                    break
        except ActivityError as error:
            # A timed-out/crashed Activity may have dispatched an external effect. The Owner
            # resolves its durable intent; the Workflow never retries the invocation.
            return await phase(state, "recover", error=str(error))
        self.stage = "settled"
        return await phase(state, "settle", done=done)


@workflow.defn(name="knowledgeos.action.v1")
class ActionWorkflow:
    @workflow.run
    async def run(self, state: dict[str, Any]) -> dict[str, Any]:
        try:
            return await phase(state, "action", phase_id="action." + state["action_id"])
        except ActivityError as error:
            return await phase(state, "recover_action", error=str(error),
                               phase_id="recover_action." + state["action_id"])

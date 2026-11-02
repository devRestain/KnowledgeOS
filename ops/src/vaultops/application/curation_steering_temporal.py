"""Opt-in official Temporal/LangGraph Activities with exact native owner pins."""
from __future__ import annotations

from datetime import timedelta
from functools import partial
from importlib.metadata import version

from .curation_steering_contract import (
    APPLY_ACTIVITY,
    BOUNDARY_ACTIVITY,
    INITIAL_GRAPH,
    PARTIAL_ACTIVITY,
    SUCCESSOR_GRAPH,
    WORKFLOW,
)
from .curation_work import _require


class _SteeringActivities:
    def __init__(self, controller):
        self.controller = controller

    def native(self, work_run_id):
        from temporalio import activity
        info = activity.info()
        _require(info.workflow_type == WORKFLOW and info.workflow_id == "knowledgeos/" + work_run_id,
                 "Steering Activity has a foreign native Workflow binding")
        return info.workflow_run_id

    async def manager(self, dispatch: dict) -> dict:
        self.controller.start(dispatch, temporal_run_id=self.native(dispatch["work_run_id"]))
        return dispatch

    async def triage(self, dispatch: dict) -> dict:
        self.controller.triage(dispatch["work_run_id"], temporal_run_id=self.native(dispatch["work_run_id"]))
        return {"work_run_id": dispatch["work_run_id"]}

    async def boundary(self, payload: dict) -> dict:
        from temporalio import activity
        _require(activity.info().activity_type == BOUNDARY_ACTIVITY, "Wrong native boundary Activity")
        return self.controller.open_boundary(payload["work_run_id"], pending_nodes=payload["pending_nodes"],
                                             temporal_run_id=self.native(payload["work_run_id"]))

    async def initial_normalization(self, state: dict) -> dict:
        # This real node remains unscheduled behind interrupt_before. Even a
        # misconfigured graph cannot use it to bypass the owner's release gate.
        record = self.controller._record(self.controller.app.journal.read(), state["work_run_id"])
        run = record["run"]
        token = {"work_run_id": run["work_run_id"], "generation": run["generation"],
                 "fencing_token": run["fencing_token"], "graph_run_id": run.get("active_graph_run_id")}
        return self.controller.normalize(token, temporal_run_id=self.native(state["work_run_id"]))

    async def successor(self, state: dict) -> dict:
        return self.controller.successor(state["work_run_id"], temporal_run_id=self.native(state["work_run_id"]))

    async def normalize(self, state: dict) -> dict:
        return self.controller.normalize(state, temporal_run_id=self.native(state["work_run_id"]))

    async def finish(self, state: dict) -> dict:
        return self.controller.finish(state["work_run_id"], temporal_run_id=self.native(state["work_run_id"]))

    async def apply(self, payload: dict) -> dict:
        from temporalio import activity
        _require(isinstance(payload, dict) and set(payload) == {"phase", "dispatch"}
                 and payload["phase"] in {"inspect", "apply"}, "Invalid command Activity envelope")
        dispatch = payload["dispatch"]
        native = self.native(dispatch["work_run_id"])
        _require(activity.info().activity_type == APPLY_ACTIVITY, "Wrong steering apply Activity")
        admitted = self.controller.command_dispatch(dispatch["work_run_id"], dispatch["command_id"])
        _require(admitted == dispatch, "Native Update changed the durable outbox")
        if payload["phase"] == "inspect":
            from .curation_steering import _command
            record = self.controller.app.journal.read()["intents"][_command(dispatch["command_id"])]
            _require(record["target"]["temporal_run_id"] == native, "Command targets a foreign native Run")
            return {"kind": record["body"]["kind"]}
        return self.controller.apply(dispatch, temporal_run_id=native)

    async def partial(self, dispatch: dict) -> dict:
        from temporalio import activity
        _require(activity.info().activity_type == PARTIAL_ACTIVITY, "Wrong partial evaluation Activity")
        return self.controller.partial(dispatch, temporal_run_id=self.native(dispatch["work_run_id"]))


def steering_worker_components(controller):
    from langgraph.graph import END, START, StateGraph
    from temporalio import activity
    from temporalio.common import RetryPolicy
    from temporalio.langgraph import LangGraphPlugin

    from .curation_steering import SafeCurationSteering
    from .curation_steering_workflow import KnowledgeOSCurationSteeringWorkflow

    _require(isinstance(controller, SafeCurationSteering) and controller.exops.curation_steering is controller,
             "Steering Worker needs the exact owner controller")
    for package, pinned in (("temporalio", "1.34.0"), ("temporalio-langgraph", "0.1.0"), ("langgraph", "1.2.0")):
        _require(version(package) == pinned, "Steering SDK package pin changed: " + package)
    owner = _SteeringActivities(controller)
    graphs = {}
    for name, nodes in (
        (INITIAL_GRAPH, (("manager", owner.manager), ("triage", owner.triage), ("normalization_report", owner.initial_normalization))),
        (SUCCESSOR_GRAPH, (("manager_successor", owner.successor), ("normalization_report", owner.normalize), ("independent_assessment", owner.finish))),
    ):
        selected = StateGraph(dict)
        previous = START
        for node_name, node in nodes:
            selected.add_node(node_name, node, metadata={"execute_in": "activity", "start_to_close_timeout": timedelta(seconds=30),
                                                        "retry_policy": RetryPolicy(maximum_attempts=1)})
            selected.add_edge(previous, node_name)
            previous = node_name
        selected.add_edge(previous, END)
        graphs[name] = selected
    plugin = LangGraphPlugin(graphs=graphs)
    plugin.activities.extend((activity.defn(name=APPLY_ACTIVITY)(partial(owner.apply)),
                              activity.defn(name=PARTIAL_ACTIVITY)(partial(owner.partial)),
                              activity.defn(name=BOUNDARY_ACTIVITY)(partial(owner.boundary))))
    return KnowledgeOSCurationSteeringWorkflow, plugin


async def deliver_steering(controller, client, work_run_id, command_id):
    """One stable Update ID; a lost response reconciles the same owner receipt."""
    from .curation_steering import _command
    from .curation_steering_contract import UPDATE
    payload = controller.command_dispatch(work_run_id, command_id)
    command = controller.app.journal.read()["intents"][_command(command_id)]
    if command["state"] == "applied":
        # Retain application-time history, but reauthorize the live actor on read.
        return controller.apply(payload, temporal_run_id=payload["target"]["temporal_run_id"])
    handle = client.get_workflow_handle("knowledgeos/" + work_run_id, run_id=payload["target"]["temporal_run_id"])
    try:
        return await handle.execute_update(UPDATE, payload, id=command_id)
    except Exception:
        controller.command_dispatch(work_run_id, command_id)
        retained = controller.app.journal.read()["intents"][_command(command_id)]
        if retained["state"] == "applied":
            return controller.apply(payload, temporal_run_id=payload["target"]["temporal_run_id"])
        raise

"""Operation-owned Temporal Worker and the pinned LangGraph registration inventory.

Imported only by Engine.start(); read-only Owner/Core tools need no Temporal SDK import.
"""

from __future__ import annotations

import asyncio
import hashlib
import itertools
import threading
import tomllib
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from importlib import metadata
from pathlib import Path
from typing import Any

from temporalio import activity
from temporalio.client import Client
from temporalio.common import WorkflowIDConflictPolicy, WorkflowIDReusePolicy
from temporalio.exceptions import WorkflowAlreadyStartedError
from temporalio.langgraph import LangGraphPlugin
from temporalio.worker import Worker

from .capabilities import Done
from .core_records import core_name
from .temporal_workflow import ActionWorkflow, WorkWorkflow, topology
from .util import digest


def source_digest(distribution: str) -> str:
    installed = metadata.distribution(distribution)
    hasher = hashlib.sha256()
    for entry in sorted(installed.files or [], key=str):
        if entry.suffix == ".py":
            hasher.update(str(entry).encode() + b"\0")
            hasher.update(installed.locate_file(entry).read_bytes() + b"\0")
    return "sha256:" + hasher.hexdigest()


class TemporalRuntime:
    def __init__(self, engine: Any):
        self.engine = engine
        self.owner = engine.owner
        config = self.owner.config
        options = config.settings.get("runtime", {})
        self.address = options.get("address", "127.0.0.1:7233")
        self.namespace = options.get("namespace", "knowledgeos")
        self.task_queue = options.get("task_queue") or "knowledgeos-" + hashlib.sha256(
            str(config.state.resolve()).encode()).hexdigest()[:24]
        self.timeout = int(options.get("activity_timeout_seconds", 120))
        self.connect_timeout = float(options.get("connect_timeout_seconds", 10))
        if self.timeout < 1 or self.connect_timeout <= 0:
            raise ValueError("runtime Activity and connection timeouts must be positive")
        self.graphs: dict[str, Any] = {}
        self.topologies: dict[tuple[str, ...], dict[str, Any]] = {}
        inventory: set[tuple[str, ...]] = {(officer.name,) for officer in self.owner.registry.officers.values()}
        for declaration in self.owner.catalog.capabilities().values():
            pool = tuple((declaration.get("target") or {}).get("pool", {}))
            for length in range(1, len(pool) + 1):
                inventory.update(itertools.permutations(pool, length))
        for roles in sorted(inventory):
            nodes = [(core_name(role), role) for role in roles]
            names = [name for name, _ in nodes] + ["evaluation"]
            if len(names) != len(set(names)):
                raise ValueError("runtime graph node identities collide")
            key = "knowledgeos." + digest(names)
            registered = topology(nodes)
            self.graphs[key] = registered
            self.topologies[roles] = {
                "graph_key": key, "node_ids": list(registered.nodes),
                "edges": [list(pair) for pair in sorted(registered.edges)
                          if pair[0] in registered.nodes and pair[1] in registered.nodes],
            }
        libraries = ("temporalio", "temporalio-langgraph", "langgraph")
        source = hashlib.sha256()
        package = Path(__file__).resolve().parent
        for path in sorted(package.rglob("*.py")):
            source.update(path.relative_to(package).as_posix().encode() + b"\0" + path.read_bytes() + b"\0")
        if package.parent.name == "src":
            manifest = (package.parent.parent / "pyproject.toml").read_bytes()
            project = tomllib.loads(manifest.decode("utf-8"))["project"]
            if project["name"] != "knowledgeos":
                raise ValueError("runtime source manifest does not identify KnowledgeOS")
            engine_version = project["version"]
            engine_release = {"mode": "source_project",
                              "manifest_digest": "sha256:" + hashlib.sha256(manifest).hexdigest()}
        else:
            engine_version = metadata.version("knowledgeos")
            engine_release = {"mode": "installed_distribution"}
        self.binding = {
            "engine_id": "agentfabric.operation.temporal_langgraph",
            "engine_version": engine_version,
            "engine_release": engine_release,
            "libraries": {name: metadata.version(name) for name in libraries},
            "library_source_digests": {name: source_digest(name) for name in libraries},
            "implementation_source_digest": "sha256:" + source.hexdigest(),
            "source_revision": self.owner.identity,
            "address": self.address, "namespace": self.namespace, "task_queue": self.task_queue,
            "topology_digest": "sha256:" + digest(list(self.topologies.values())),
        }
        self.binding_digest = digest(self.binding)
        self.generations: dict[str, int] = {}
        self._shutdown_task = None
        engine.runtime = self
        for work in self.owner.store.list("works"):
            # Historical terminal and awaiting-human Work is never rewritten by a restart.
            if work["status"] in ("admitted", "running") and (work.get("runtime") or {}).get(
                    "binding_digest") == self.binding_digest:
                self.pin(work["work_id"])
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._loop.run_forever, name="kos-temporal", daemon=True)
        self._thread.start()
        self._executor = ThreadPoolExecutor(max_workers=max(2, config.workers), thread_name_prefix="kos-activity")
        self._closed = False
        try:
            self._call(self._start())
        except BaseException:
            self._executor.shutdown(wait=False, cancel_futures=True)
            self._loop.call_soon_threadsafe(self._loop.stop)
            self._thread.join()
            self._loop.close()
            engine.runtime = None
            raise

    def _call(self, operation):
        return asyncio.run_coroutine_threadsafe(operation, self._loop).result()


    async def _start(self) -> None:
        self.client = await asyncio.wait_for(
            Client.connect(self.address, namespace=self.namespace), self.connect_timeout)
        self.worker = Worker(
            self.client, task_queue=self.task_queue, workflows=[WorkWorkflow, ActionWorkflow],
            activities=[self.execute_phase], activity_executor=self._executor,
            max_concurrent_activities=max(2, self.owner.config.workers),
            graceful_shutdown_timeout=timedelta(seconds=self.timeout),
            plugins=[LangGraphPlugin(graphs=self.graphs)],
        )
        await self.worker.__aenter__()

    @activity.defn(name="knowledgeos.phase")
    def execute_phase(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self.engine.activity(payload)

    def pin(self, work_id: str) -> dict[str, Any]:
        def bind(work: dict[str, Any]) -> None:
            if work.get("runtime"):
                pin = work["runtime"]
                if pin["binding_digest"] != self.binding_digest:
                    raise ValueError("recorded runtime binding changed; revised admission is required")
                if work_id not in self.generations:
                    if not pin.pop("graceful_handoff", False):
                        pin["generation"] += 1
                    self.generations[work_id] = pin["generation"]
                return
            work["runtime"] = {
                "binding": self.binding, "binding_digest": self.binding_digest,
                "workflow_id": self.workflow_id(work_id), "phases": {},
                "role_declarations_digest": digest(self.owner.catalog.roles()),
                "role_declarations": self.owner.catalog.roles(),
                "cancel_requested": False, "generation": 1,
                "input_manifest_blob": self.owner.store.put_json_blob(work["workspec"]["inputs"]),
                "capability_declaration": self.owner.catalog.capability(work["workspec"]["capability"]),
            }
            self.generations[work_id] = 1
        return self.owner.update_work(work_id, bind)["runtime"]

    def workflow_id(self, work_id: str) -> str:
        return self.owner.config.operation_id + ":" + work_id

    def payload(self, work_id: str, request_id: str | None, actor: str | None) -> dict[str, Any]:
        pin = self.pin(work_id)
        return {"work_id": work_id, "request_id": request_id, "actor": actor,
                "binding_digest": pin["binding_digest"], "timeout": self.timeout}

    async def _run(self, payload: dict[str, Any], action: bool = False) -> dict[str, Any]:
        workflow_id = self.workflow_id(payload["work_id"])
        if action:
            workflow_id += ":" + payload["action_id"]
        try:
            handle = await self.client.start_workflow(
                ActionWorkflow.run if action else WorkWorkflow.run, payload, id=workflow_id,
                task_queue=self.task_queue, id_reuse_policy=WorkflowIDReusePolicy.REJECT_DUPLICATE,
                id_conflict_policy=WorkflowIDConflictPolicy.USE_EXISTING,
            )
        except WorkflowAlreadyStartedError:
            handle = self.client.get_workflow_handle(workflow_id)
        return await handle.result()

    def run(self, work_id: str, request_id: str | None, actor: str | None) -> Done:
        return Done(**self._call(self._run(self.payload(work_id, request_id, actor))))

    def action(self, payload: dict[str, Any]) -> Done:
        return Done(**self._call(self._run(payload, action=True)))

    async def _cancel(self, work_id: str) -> None:
        handle = self.client.get_workflow_handle(self.workflow_id(work_id))
        await handle.signal(WorkWorkflow.cancel)

    def cancel(self, work_id: str) -> None:
        self._call(self._cancel(work_id))

    async def _describe(self, work_id: str) -> dict[str, Any]:
        handle = self.client.get_workflow_handle(self.workflow_id(work_id))
        description = await handle.describe()
        return {"workflow_id": handle.id, "run_id": description.run_id,
                "status": description.status.name, "task_queue": description.task_queue}

    def describe(self, work_id: str) -> dict[str, Any]:
        return self._call(self._describe(work_id))

    async def _shutdown(self) -> None:
        if self._shutdown_task is None:
            self._shutdown_task = asyncio.create_task(self.worker.shutdown())
        await self._shutdown_task

    def assert_activity_current(self) -> None:
        if activity.in_activity():
            info = activity.info()
            expired = (info.start_to_close_timeout is not None
                       and datetime.now(UTC) >= info.started_time + info.start_to_close_timeout)
            if activity.is_cancelled() or expired:
                raise RuntimeError("Activity dispatch window closed; stale effects/commits are fenced")

    def prepare_shutdown(self) -> None:
        for work_id in tuple(self.generations):
            work = self.owner.get_work(work_id)
            if work and work["status"] in ("admitted", "running"):
                self.owner.update_work(work_id, lambda row: row["runtime"].update(graceful_handoff=True))


    def close(self) -> None:
        if not self._closed:
            self._closed = True
            self._call(self._shutdown())
            self._executor.shutdown(wait=True)
            self._loop.call_soon_threadsafe(self._loop.stop)
            self._thread.join()
            self._loop.close()

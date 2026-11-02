"""Explicit installed writable Owner driven by the selected host stdio bridge.

``serve --local-embedding ollama-qwen3-stdio`` binds the existing pinned Qwen
port through the host driver, not container loopback. The host must select the
same flag. Serving never publishes an index. The separately selected
``publish-index`` mode requires that binding and private ``publish_index``
permission, invokes the existing owner publication API, and starts no HTTP,
Worker, scheduler or Temporal client. It changes only reconstructable index
artifacts. Without selection Vector/Hybrid stay unavailable; read-only Owner
remains Lexical-only.

Publication preserves the existing two-pointer protocol, not an atomic pair:
revocation after Vector promotion can leave derived Runtime artifacts/a future
Vector pointer while Lexical promotion is refused. Readers reject that mismatch;
the command does not retry, roll back under revoked authority or write notes.
"""
from __future__ import annotations

import argparse
import asyncio
import copy
import json
import os
import signal
import sys
import threading
from contextlib import AsyncExitStack
from pathlib import Path

from ..adapters.paths import resolve_paths
from ..application.owner_runtime import (
    DispatchSupervisor,
    OwnerRuntimePolicy,
    WritableOwnerRuntime,
    require,
)
from .loopback_owner import create_loopback_owner

MAX_MESSAGE = 2 * 1024 * 1024
REQUEST = "KNOWLEDGEOS_NATIVE_REQUEST="
EMBEDDING_REQUEST = "KNOWLEDGEOS_EMBEDDING_REQUEST="
LOCAL_EMBEDDING = "ollama-qwen3-stdio"


class NativeStdioTransport:
    """One ordered pipe, including HTTP-thread embedding and native Work calls."""
    def __init__(self):
        self.lock = asyncio.Lock()
        self.loop = asyncio.get_running_loop()
        self.failed = False

    async def _call(self, prefix, request):
        async with self.lock:
            require(not self.failed, "Native host transport requires reconstruction")
            raw = json.dumps(request, ensure_ascii=False, allow_nan=False)
            require(len(raw.encode()) < MAX_MESSAGE, "Native request exceeds the host wire bound")
            print(prefix + raw, flush=True)
            try:
                async with asyncio.timeout(360):
                    response = await asyncio.to_thread(sys.stdin.readline, MAX_MESSAGE + 1)
                require(0 < len(response.encode()) <= MAX_MESSAGE and response.endswith("\n"),
                        "Native host response is uncertain; it will not be retried")
                value = json.loads(response)
                require(isinstance(value, dict) and set(value) == {"response"},
                        "Native host outcome requires explicit reconciliation")
                return value["response"]
            except BaseException:
                # A late pipe response must never be consumed by the next caller.
                self.failed = True
                raise

    async def call(self, method, request):
        return await self._call(REQUEST, {"method_id": method, "request": request})

    def embedding_request(self, path, body=None):
        """Synchronous port called off-loop by authenticated HTTP or maintenance."""
        from ..adapters.core import AdmissionError

        try:
            asyncio.get_running_loop()
        except RuntimeError:
            pass
        else:
            raise AdmissionError("BINDING_INVALID", "Embedding port must run outside the Owner event loop")
        future = asyncio.run_coroutine_threadsafe(
            self._call(EMBEDDING_REQUEST, {"path": path, "body": body}), self.loop)
        try:
            return future.result(timeout=365)
        except (ValueError, OSError, TimeoutError) as error:
            future.cancel()
            raise AdmissionError("EMBEDDING_UNAVAILABLE", "Selected host embedding request failed") from error


class NativeStdioRunner:
    def __init__(self, transport, method, binding):
        self.transport, self.method, self._binding = transport, method, copy.deepcopy(binding)

    @property
    def binding(self):
        return copy.deepcopy(self._binding)

    async def __call__(self, request):
        return await self.transport.call(self.method, request)


def compose_runtime(arguments):
    policy = OwnerRuntimePolicy(arguments.policy_file)
    transport = NativeStdioTransport()
    runners = {method: NativeStdioRunner(transport, method, item["runner_binding"])
               for method, item in policy.initial["methods"].items()}
    vector_port = None
    if arguments.local_embedding == LOCAL_EMBEDDING:
        from ..adapters.ollama_embedding import OllamaEmbeddingPort
        vector_port = OllamaEmbeddingPort(request=transport.embedding_request)
    return WritableOwnerRuntime(resolve_paths(), policy=policy, runners=runners, vector_port=vector_port)


async def publish_index(arguments):
    require(arguments.local_embedding == LOCAL_EMBEDDING,
            "Index publication requires the explicit selected local embedding binding")
    runtime = compose_runtime(arguments)
    publication = await asyncio.to_thread(runtime.application.daily_stewardship().publish_index,
                                         authorize=runtime.authorize_index_publication)
    print("KnowledgeOS Owner index published=" + json.dumps(publication, ensure_ascii=False), flush=True)


async def serve(arguments):
    from clients.temporal import TemporalBinding
    from temporalio.client import Client
    from temporalio.worker import Worker

    from ..application.temporal_contract import (
        NATIVE_TEAM_ROUTES,
        RESEARCH_METHOD_ID,
        RESEARCH_WORKFLOW_TYPE,
    )
    from ..application.temporal_work import connect_owner_temporal

    runtime = compose_runtime(arguments)
    policy = runtime.policy
    clients = {}
    for method in runtime.executors:
        wire = RESEARCH_WORKFLOW_TYPE if method == RESEARCH_METHOD_ID else NATIVE_TEAM_ROUTES[method][1]
        binding = TemporalBinding("knowledgeos", "server:7233", "knowledgeos", "knowledgeos.work.v1",
            wire, "knowledgeos/", "owner.unavailable.update", "owner.unavailable.signal",
            "owner.unavailable.query", False)
        clients[wire] = await connect_owner_temporal(runtime.exops, binding)
    sdk = await Client.connect("server:7233", namespace="knowledgeos", tls=False)
    require(bool(runtime.executors), "Writable Owner requires at least one explicitly bound method")
    server = create_loopback_owner(runtime.exops, actor_id=policy.initial["actor_id"],
        admit_template=runtime.admit_template if policy.initial["templates"] else None,
        admit_request=runtime.admit_request if policy.initial["freeform_research"] else None,
        selected_methods=runtime.selected_methods, admit_selected_request=runtime.admit_selected_request,
        bind_host=arguments.bind_host, port=arguments.port)
    scheduler = None
    if policy.initial["daily_enabled"] and not arguments.admission_only:
        from ..application.daily_scheduler import DailyAdmissionScheduler
        scheduler = DailyAdmissionScheduler(runtime.application, admit_request=runtime.admit_daily_request,
            authorize=lambda: policy.read()["daily_enabled"]
                and policy.actor_allowed(runtime.actor, "submit_work")
                and runtime.method_current("knowledgeos.daily_corpus_stewardship_team"))
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for signum in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(signum, stop.set)
    thread = threading.Thread(target=server.serve_forever, name="owner-http", daemon=True)
    try:
        async with AsyncExitStack() as stack:
            if not arguments.admission_only:
                workflows, plugin = runtime.combined_worker_components()
                await stack.enter_async_context(Worker(sdk, task_queue="knowledgeos.work.v1",
                    identity=policy.initial["worker_identity"], workflows=workflows, plugins=[plugin]))
            thread.start()
            print("KnowledgeOS writable Owner ready", flush=True)
            if arguments.admission_only:
                await stop.wait()
            else:
                async with asyncio.TaskGroup() as tasks:
                    if scheduler is not None:
                        tasks.create_task(scheduler.run(stop, enabled=True, interval_seconds=60))
                    tasks.create_task(DispatchSupervisor(runtime, clients).run(stop, interval=arguments.interval))
    finally:
        if thread.is_alive():
            server.shutdown()
            thread.join()
        server.server_close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="mode", required=True)
    owner = sub.add_parser("serve")
    owner.add_argument("--policy-file", required=True, type=Path)
    owner.add_argument("--bind-host", choices=("127.0.0.1", "owner-http"), default="127.0.0.1")
    owner.add_argument("--port", type=int, default=17900)
    owner.add_argument("--interval", type=float, default=1)
    owner.add_argument("--admission-only", action="store_true",
        help="Explicitly admit to the durable outbox without starting supervision, scheduling or Workers")
    owner.add_argument("--local-embedding", choices=(LOCAL_EMBEDDING,),
        help="Explicit pinned Qwen host stdio binding; no index publication or default activation")
    publication = sub.add_parser("publish-index",
        help="Explicit owner index publication only; starts no HTTP, Worker, scheduler or Temporal client")
    publication.add_argument("--policy-file", required=True, type=Path)
    publication.add_argument("--local-embedding", choices=(LOCAL_EMBEDDING,), required=True)
    arguments = parser.parse_args()
    os.umask(0o077)
    try:
        asyncio.run(publish_index(arguments) if arguments.mode == "publish-index" else serve(arguments))
    except KeyboardInterrupt:
        return 0
    except (ValueError, OSError):
        print("KnowledgeOS writable Owner refused: STARTUP_OR_BINDING_FAILED", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Trusted writable registration, exact Director receipts and send-once boundaries."""
from __future__ import annotations

import asyncio
import copy
import os
import sys
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from support.metadata_fixture import SOURCE
from support.owner_runtime_fixture import compose, write_policy

from vaultops.adapters.core import AdmissionError
from vaultops.application.exops import OwnerActor, work_workflow_type
from vaultops.application.metadata_work import METADATA_METHOD_ID
from vaultops.application.owner_runtime import (
    DispatchSupervisor,
    OwnerRuntimePolicy,
    WritableOwnerRuntime,
)
from vaultops.application.temporal_work import dispatch_admitted_work

ROUTE = {"runtime_kind": "hermes_native", "hermes_version": "0.22.5",
    "hermes_revision": "bf0203a794a07aac3ff764972ab9087baa4e4f4e",
    "provider": "openai-codex", "model": "gpt-6.1-terra", "adapter_digest": "sha256:" + "a" * 64}


class NoModel:
    def __init__(self, binding, _app):
        self.binding = copy.deepcopy(binding)

    async def __call__(self, _request):
        raise AssertionError("These owner boundary tests must never execute a model")


def admitted(runtime):
    source = runtime.application.reference(SOURCE)
    scope = {"path": SOURCE, "content_sha256": source["source_revision"]}
    request_id = str(uuid4())
    admissions = runtime.admit_template(runtime.actor, "note_metadata", scope, request_id)
    return runtime.exops.submit_template_group("note_metadata", scope, request_id, admissions,
                                               actor=runtime.actor)


def test_director_is_exact_durable_and_authority_is_live(tmp_path):
    runtime, runner, _canary, path, document = compose(tmp_path, ROUTE, NoModel)
    receipt = admitted(runtime)["receipts"][0]
    record = runtime.application.journal.read()["intents"]["exops.work." + receipt["work_run_id"]]
    rebuilt = WritableOwnerRuntime(runtime.application.roots, policy=OwnerRuntimePolicy(path),
        runners={method: runner for method in document["methods"]})
    assert rebuilt.director_admitted(record["spec"])
    changed = copy.deepcopy(record["spec"])
    changed["continuation_policy"]["max_profile_invocations"] += 1
    assert not rebuilt.director_admitted(changed)
    document["director_enabled"] = False
    write_policy(path, document)
    assert not rebuilt.director_admitted(record["spec"])
    with pytest.raises(AdmissionError):
        rebuilt.exops.authorize_dispatch(work_run_id=receipt["work_run_id"], action_id=receipt["action_id"],
                                         content_digest=receipt["content_digest"])


@pytest.mark.parametrize("change", ["actor", "source", "runner", "binding"])
def test_revoked_actor_stale_source_and_changed_native_binding_deny(tmp_path, change):
    runtime, runner, _canary, path, document = compose(tmp_path, ROUTE, NoModel)
    receipt = admitted(runtime)["receipts"][0]
    record = runtime.application.journal.read()["intents"]["exops.work." + receipt["work_run_id"]]
    if change == "actor":
        document["actions"].remove("submit_work")
        write_policy(path, document)
    elif change == "source":
        with (runtime.application.roots.vault / SOURCE).open("a") as note:
            note.write("\nchanged\n")
    elif change == "runner":
        runner.binding["adapter_digest"] = "sha256:" + "b" * 64
    else:
        document["methods"][METADATA_METHOD_ID]["team_binding"]["manager_executor_id"] += ".changed"
        write_policy(path, document)
    assert not runtime.source_admitted(record["spec"], record["method_source_reference"])
    with pytest.raises(AdmissionError):
        runtime.exops.authorize_dispatch(work_run_id=receipt["work_run_id"], action_id=receipt["action_id"],
                                         content_digest=receipt["content_digest"])
    with pytest.raises(AdmissionError):
        runtime.exops._guard(OwnerActor("wrong", "web"), "submit_work")


class Transport:
    def __init__(self, runtime, *, crash=False):
        self.runtime, self.crash, self.starts, self.observations = runtime, crash, [], []

    async def start(self, dispatch):
        record = self.runtime.application.journal.read()["intents"]["exops.work." + dispatch.payload["work_run_id"]]
        assert record["dispatch_state"] == "unknown"
        self.runtime.exops.authorize_dispatch(work_run_id=dispatch.payload["work_run_id"],
            action_id=dispatch.action_id, content_digest=dispatch.payload["content_digest"])
        self.starts.append(dispatch)
        if self.crash:
            raise KeyboardInterrupt("hard-loss boundary after durable marker")
        return dispatch.workflow_id

    async def observe_start(self, dispatch):
        self.observations.append(dispatch)
        if not self.starts:
            raise ConnectionError("absent is not proof of failed start")
        record = self.runtime.application.journal.read()["intents"]["exops.work." + dispatch.payload["work_run_id"]]
        return SimpleNamespace(workflow_id=dispatch.workflow_id, run_id="native.run.one",
            namespace="knowledgeos", task_queue="knowledgeos.work.v1",
            workflow_type=work_workflow_type(record), payload=dispatch.payload)


def test_hard_loss_after_marker_reconstruction_observes_never_resends(tmp_path):
    runtime, runner, _canary, path, document = compose(tmp_path, ROUTE, NoModel)
    receipt = admitted(runtime)["receipts"][0]
    transport = Transport(runtime, crash=True)
    with pytest.raises(KeyboardInterrupt):
        asyncio.run(dispatch_admitted_work(runtime.exops, transport, receipt["work_run_id"]))
    rebuilt = WritableOwnerRuntime(runtime.application.roots, policy=OwnerRuntimePolicy(path),
        runners={method: runner for method in document["methods"]})
    absent = Transport(rebuilt)
    wire = work_workflow_type(runtime.application.journal.read()["intents"]["exops.work." + receipt["work_run_id"]])
    supervisor = DispatchSupervisor(rebuilt, {wire: absent})
    asyncio.run(supervisor.tick())
    assert absent.starts == [] and len(absent.observations) == 1
    current = rebuilt.application.journal.read()["intents"]["exops.work." + receipt["work_run_id"]]
    assert current["dispatch_state"] == "unknown"
    with pytest.raises(AdmissionError):
        asyncio.run(dispatch_admitted_work(rebuilt.exops, absent, receipt["work_run_id"]))
    transport.crash = False
    asyncio.run(DispatchSupervisor(rebuilt, {wire: transport}).tick())
    assert len(transport.starts) == 1
    assert rebuilt.application.journal.read()["intents"]["exops.work." + receipt["work_run_id"]]["dispatch_state"] == "accepted"


def test_competing_supervisor_cannot_claim_during_first_send(tmp_path):
    runtime, runner, _canary, path, document = compose(tmp_path, ROUTE, NoModel)
    receipt = admitted(runtime)["receipts"][0]
    rebuilt = WritableOwnerRuntime(runtime.application.roots, policy=OwnerRuntimePolicy(path),
        runners={method: runner for method in document["methods"]})
    transport = Transport(runtime)
    class Competing(Transport):
        async def start(self, dispatch):
            with pytest.raises(AdmissionError):
                await dispatch_admitted_work(rebuilt.exops, transport, receipt["work_run_id"])
            return await super().start(dispatch)
    first = Competing(runtime)
    asyncio.run(dispatch_admitted_work(runtime.exops, first, receipt["work_run_id"]))
    assert len(first.starts) == 1 and transport.starts == []


def test_kill_before_native_start_keeps_permanent_unknown_without_retry(tmp_path):
    runtime, _runner, _canary, _path, _document = compose(tmp_path, ROUTE, NoModel)
    receipt = admitted(runtime)["receipts"][0]
    with runtime.exops.claim_dispatch(receipt["work_run_id"]):
        pass
    transport = Transport(runtime)
    with pytest.raises(AdmissionError):
        asyncio.run(dispatch_admitted_work(runtime.exops, transport, receipt["work_run_id"]))
    assert transport.starts == []


def test_director_receipt_recovers_before_work_commit_with_same_expiry(tmp_path):
    runtime, runner, _canary, path, document = compose(tmp_path, ROUTE, NoModel)
    scope = {"path": SOURCE, "content_sha256": runtime.application.reference(SOURCE)["source_revision"]}
    request_id = str(uuid4())
    first = runtime.admit_template(runtime.actor, "note_metadata", scope, request_id)
    rebuilt = WritableOwnerRuntime(runtime.application.roots, policy=OwnerRuntimePolicy(path),
        runners={method: runner for method in document["methods"]})
    recovered = rebuilt.admit_template(rebuilt.actor, "note_metadata", scope, request_id)
    assert recovered == first
    group = rebuilt.exops.submit_template_group("note_metadata", scope, request_id, recovered, actor=rebuilt.actor)
    assert len(group["receipts"]) == 2
    spec, run, action, expiry, source = recovered[0]
    with pytest.raises(AdmissionError, match="durable Director"):
        rebuilt.exops.submit_work(spec, run, actor=rebuilt.actor, action_id=action,
            expires_at=expiry + timedelta(minutes=1), method_source_reference=source)


@pytest.mark.parametrize("field,value", [("namespace", "foreign"), ("task_queue", "foreign"),
    ("workflow_type", "foreign"), ("run_id", ""), ("workflow_id", "knowledgeos/foreign")])
def test_unknown_observation_rejects_foreign_server_identity(tmp_path, field, value):
    runtime, _runner, _canary, _path, _document = compose(tmp_path, ROUTE, NoModel)
    receipt = admitted(runtime)["receipts"][0]
    transport = Transport(runtime)
    original = transport.observe_start
    async def mismatch(dispatch):
        observation = await original(dispatch)
        setattr(observation, field, value)
        return observation
    transport.observe_start = mismatch
    with pytest.raises(AdmissionError):
        asyncio.run(dispatch_admitted_work(runtime.exops, transport, receipt["work_run_id"]))
    assert len(transport.starts) == 1
    assert runtime.application.journal.read()["intents"]["exops.work." + receipt["work_run_id"]]["dispatch_state"] == "unknown"


def active_metadata_graph(tmp_path):
    runtime, runner, canary, path, document = compose(tmp_path, ROUTE, NoModel)
    receipt = admitted(runtime)["receipts"][0]
    run_id = receipt["work_run_id"]
    record = runtime.application.journal.read()["intents"]["exops.work." + run_id]
    started = runtime.exops.worker_started(run_id, worker_identity=runtime.exops.worker_identity,
        action_id=receipt["action_id"], content_digest=receipt["content_digest"],
        work_spec_digest=record["spec"]["spec_digest"], workflow_id="knowledgeos/" + run_id)
    asyncio.run(runtime.executors[METADATA_METHOD_ID].manager(record["spec"], started))
    record = runtime.application.journal.read()["intents"]["exops.work." + run_id]
    return runtime, runner, canary, path, document, record


@pytest.mark.parametrize("revocation", ["actor", "director", "method", "runner", "receipt"])
def test_actual_separate_mcp_process_rechecks_live_owner_authority(tmp_path, revocation):
    from mcp import Client
    from mcp.client.stdio import StdioServerParameters

    import vaultops
    from vaultops.application.read_tools import KnowledgeReadTools
    from vaultops.retrieval import _chunks_for_note

    runtime, _runner, canary, policy_path, document, record = active_metadata_graph(tmp_path)
    source = record["method_source_reference"]
    original_source = (runtime.application.roots.vault / SOURCE).read_bytes()
    reader = KnowledgeReadTools(runtime.application)
    _, notes = reader._eligible(selected_path=SOURCE)
    chunk = _chunks_for_note(reader._note(source, notes))[0].locator
    before_pending = {p.name: p.read_bytes()
        for p in (runtime.application.roots.vault / "01_AI_Review/Pending").glob("*.md")}
    async def exercise():
        parameters = StdioServerParameters(command=sys.executable, args=[
            "-m", "vaultops.interfaces.mcp_server", "--control-root", str(runtime.application.roots.control),
            "--work-run-id", record["run"]["work_run_id"], "--executor-id", record["spec"]["allowed_executor_ids"][0],
            "--graph-run-id", record["run"]["active_graph_run_id"]],
            env={"PYTHONPATH": str(Path(vaultops.__file__).resolve().parent.parent),
                 "PYTHONDONTWRITEBYTECODE": "1"})
        async with Client(parameters, mode="legacy") as client:
            arguments = {"resource_reference": source, "chunk_locator": chunk}
            first = await client.call_tool("knowledge_read", arguments)
            assert not first.is_error and first.structured_content["data"]["text"]
            if revocation == "actor":
                document["actions"].remove("submit_work")
            elif revocation == "director":
                document["director_enabled"] = False
            elif revocation == "method":
                del document["methods"][METADATA_METHOD_ID]
            elif revocation == "runner":
                document["methods"][METADATA_METHOD_ID]["runner_binding"]["adapter_digest"] = "sha256:" + "b" * 64
            else:
                with runtime.application.journal.writer() as writer:
                    del writer.value["intents"]["owner.director." + record["spec"]["admission_receipt_id"]]
                    writer.commit(runtime.application.clock())
            write_policy(policy_path, document)
            snapshot = runtime.application.journal.read()
            denied = await client.call_tool("knowledge_read", arguments)
            assert denied.is_error and denied.structured_content["effect"] == "none"
            assert denied.structured_content["data"] is None
            assert canary not in str(denied.structured_content)
            assert runtime.application.journal.read() == snapshot
    asyncio.run(exercise())
    assert {p.name: p.read_bytes()
        for p in (runtime.application.roots.vault / "01_AI_Review/Pending").glob("*.md")} == before_pending
    assert (runtime.application.roots.vault / SOURCE).read_bytes() == original_source


def test_cached_proposal_binding_cannot_bypass_live_private_policy(tmp_path):
    from vaultops.application.knowledge import KnowledgeApplication, LocalIdentity
    from vaultops.application.work_tools import TeamToolSession

    runtime, _runner, _canary, policy_path, document, record = active_metadata_graph(tmp_path)
    separate = KnowledgeApplication.from_roots(runtime.application.roots,
        identity=LocalIdentity("local.mcp", "mcp", local_execution=True))
    session = TeamToolSession(separate, record["run"]["work_run_id"],
        record["spec"]["allowed_executor_ids"][0], record["run"]["active_graph_run_id"])
    cached = session.proposal_binding(record["method_source_reference"])
    document["director_enabled"] = False
    write_policy(policy_path, document)
    before = separate.journal.read()
    report, code = separate.create_normalize_proposal(
        source_reference=record["method_source_reference"], work_binding=cached)
    assert code == 10 and report["errors"][0]["code"] == "ADMISSION_REQUIRED", report
    assert separate.journal.read() == before
    assert list((separate.roots.vault / "01_AI_Review/Pending").glob("*.md")) == []


def test_bridge_resolves_path_docker_to_exact_executable(tmp_path, monkeypatch):
    from vaultops.interfaces.native_owner_driver import resolve_docker_command

    binary = tmp_path / "docker"
    binary.write_text("#!/bin/sh\nexit 0\n")
    binary.chmod(0o700)
    monkeypatch.setenv("PATH", str(tmp_path))
    assert resolve_docker_command("docker") == str(binary.resolve())
    assert os.access(resolve_docker_command(str(binary)), os.X_OK)
    with pytest.raises(ValueError):
        resolve_docker_command("missing-docker")


@pytest.mark.parametrize("revocation", ["actor", "director", "method"])
def test_actual_separate_mcp_writer_cannot_create_pending_after_revocation(tmp_path, revocation):
    from mcp import Client
    from mcp.client.stdio import StdioServerParameters

    import vaultops

    runtime, _runner, canary, policy_path, document, record = active_metadata_graph(tmp_path)
    original_source = (runtime.application.roots.vault / SOURCE).read_bytes()
    async def exercise():
        parameters = StdioServerParameters(command=sys.executable, args=[
            "-m", "vaultops.interfaces.mcp_server", "--control-root", str(runtime.application.roots.control),
            "--work-run-id", record["run"]["work_run_id"], "--executor-id", record["spec"]["allowed_executor_ids"][1],
            "--graph-run-id", record["run"]["active_graph_run_id"]],
            env={"PYTHONPATH": str(Path(vaultops.__file__).resolve().parent.parent),
                 "PYTHONDONTWRITEBYTECODE": "1"})
        async with Client(parameters, mode="legacy") as client:
            # A real scoped process is established while its authority is current.
            if revocation == "actor":
                document["actions"].remove("submit_work")
            elif revocation == "director":
                document["director_enabled"] = False
            else:
                del document["methods"][METADATA_METHOD_ID]
            write_policy(policy_path, document)
            before = runtime.application.journal.read()
            denied = await client.call_tool("artifact_submit", {
                "artifact_kind": "note_metadata", "content": "{}",
                "idempotency_key": "revoked-owner-cannot-write"})
            assert denied.is_error and denied.structured_content["effect"] == "none"
            assert denied.structured_content["data"] is None
            assert canary not in str(denied.structured_content)
            assert runtime.application.journal.read() == before
    asyncio.run(exercise())
    assert list((runtime.application.roots.vault / "01_AI_Review/Pending").glob("*.md")) == []
    assert (runtime.application.roots.vault / SOURCE).read_bytes() == original_source


def _stop_frame_process(process):
    if process.poll() is None:
        process.kill()
    process.wait(timeout=5)
    for stream in (process.stdin, process.stdout, process.stderr):
        stream.close()


def test_driver_consumes_coalesced_ready_and_request_while_producer_waits():
    import json
    import subprocess

    from vaultops.interfaces.native_owner_driver import REQUEST, owner_frames

    request = {"method_id": METADATA_METHOD_ID,
        "request": {"work_run_id": "workrun.framing", "stage_id": "coverage_evidence",
                    "request_digest": "sha256:" + "1" * 64}}
    payload = ("KnowledgeOS writable Owner ready\n" + REQUEST + json.dumps(request) + "\n").encode()
    # One actual OS write coalesces both frames; the producer then waits for a
    # reply instead of exiting or making another descriptor-readiness event.
    process = subprocess.Popen([sys.executable, "-c",
        "import os,sys;os.write(1," + repr(payload) + ");sys.stdin.buffer.read(1)"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        frames = owner_frames(process.stdout)
        assert next(frames) == "KnowledgeOS writable Owner ready\n"
        assert process.poll() is None
        native = next(frames)
        assert native.startswith(REQUEST) and json.loads(native[len(REQUEST):]) == request
        process.stdin.write(b"x")
        process.stdin.flush()
        assert next(frames, None) is None
        process.wait(timeout=5)
        assert process.returncode == 0
    finally:
        _stop_frame_process(process)


def test_driver_waits_for_complete_utf8_frame_across_partial_pipe_writes():
    import json
    import subprocess
    from concurrent.futures import ThreadPoolExecutor

    from vaultops.interfaces.native_owner_driver import REQUEST, owner_frames

    character = "지".encode()
    prefix = REQUEST.encode() + b'{"question":"' + character[:1]
    suffix = character[1:] + b'"}\n'
    script = ("import os,sys;os.write(1," + repr(prefix) + ");os.write(2,b'1');"
              "sys.stdin.buffer.read(1);os.write(1," + repr(suffix) + ")")
    process = subprocess.Popen([sys.executable, "-c", script],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    pool = ThreadPoolExecutor(max_workers=1)
    try:
        frames = owner_frames(process.stdout)
        pending = pool.submit(next, frames)
        assert process.stderr.read(1) == b"1"
        assert not pending.done()
        process.stdin.write(b"x")
        process.stdin.flush()
        frame = pending.result(timeout=5)
        assert json.loads(frame[len(REQUEST):]) == {"question": "지"}
        assert next(frames, None) is None
    finally:
        _stop_frame_process(process)
        pool.shutdown(wait=True)


@pytest.mark.parametrize("payload", [b"unfinished-request", b"\xff\n", b"x" * 65 + b"\n"])
def test_driver_rejects_incomplete_invalid_or_oversized_frames_before_dispatch(payload, monkeypatch):
    import subprocess

    from vaultops.interfaces import native_owner_driver

    monkeypatch.setattr(native_owner_driver, "MAX_MESSAGE", 64)
    process = subprocess.Popen([sys.executable, "-c", "import os;os.write(1," + repr(payload) + ")"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        with pytest.raises(ValueError):
            next(native_owner_driver.owner_frames(process.stdout))
    finally:
        _stop_frame_process(process)


def test_supervisor_propagates_programming_errors_after_durable_unknown(tmp_path):
    runtime, _runner, _canary, _path, _document = compose(tmp_path, ROUTE, NoModel)
    receipt = admitted(runtime)["receipts"][0]
    record = runtime.application.journal.read()["intents"]["exops.work." + receipt["work_run_id"]]
    class BrokenTransport:
        async def start(self, _dispatch):
            raise TypeError("Unexpected transport implementation defect")
    supervisor = DispatchSupervisor(runtime, {work_workflow_type(record): BrokenTransport()})
    with pytest.raises(TypeError):
        asyncio.run(supervisor.tick())
    current = runtime.application.journal.read()["intents"]["exops.work." + receipt["work_run_id"]]
    assert current["dispatch_state"] == "unknown"
    assert current["application_state"] == "not_started"


def selected_web(runtime, *, bound=True, actor=None):
    import json

    from vaultops.interfaces.exops_web import ExOpsWebBackend
    from vaultops.interfaces.loopback_owner import LoopbackIngress

    backend = ExOpsWebBackend(runtime.exops, actor or runtime.actor,
        selected_methods=runtime.selected_methods if bound else None,
        admit_selected_request=runtime.admit_selected_request if bound else None)

    def call(route, payload):
        return backend(LoopbackIngress("knowledgeos", "POST", route, json.dumps(payload).encode()))

    return call


def selected_payload(runtime):
    return {"method_id": METADATA_METHOD_ID, "request_id": str(uuid4()),
            "scope": {"path": SOURCE,
                      "content_sha256": runtime.application.reference(SOURCE)["source_revision"]}}


def test_selected_methods_catalog_does_not_grant_unregistered_methods(tmp_path):
    runtime, _runner, _canary, path, document = compose(tmp_path, ROUTE, NoModel)
    web = selected_web(runtime)
    before = runtime.application.journal.read()
    catalog = web("work/methods", {})
    methods = {item["method_id"]: item for item in catalog["methods"]}
    assert set(catalog) == {"methods", "observed_at"}
    assert methods[METADATA_METHOD_ID]["available"] is True
    assert methods[METADATA_METHOD_ID]["parameters"] == {}
    assert methods[METADATA_METHOD_ID]["reason"] is None
    unavailable = methods["knowledgeos.cited_research_team"]
    assert unavailable["available"] is False and unavailable["reason"]
    assert unavailable["parameters"] == {}
    assert str(path) not in str(catalog) and "runner_binding" not in str(catalog)
    assert web("owner/status", {})["bindings"]["selected_methods"] is True
    payload = selected_payload(runtime)
    payload["method_id"] = unavailable["method_id"]
    with pytest.raises(AdmissionError, match="unavailable"):
        web("work/intake-selected", payload)
    assert runtime.application.journal.read() == before
    document["actions"].remove("submit_work")
    write_policy(path, document)
    assert not any(item["available"] for item in web("work/methods", {})["methods"])
    assert web("owner/status", {})["bindings"]["selected_methods"] is False
    with pytest.raises(AdmissionError):
        web("work/intake-selected", selected_payload(runtime))
    assert runtime.application.journal.read() == before


@pytest.mark.parametrize("change", ["unbound", "readonly", "wrong_actor", "read_revoked", "director",
                                  "registration", "runner", "source", "outside_scope"])
def test_selected_http_admission_fails_closed_without_owner_source_authority(tmp_path, change):
    runtime, runner, _canary, path, document = compose(tmp_path, ROUTE, NoModel)
    payload = selected_payload(runtime)
    web = selected_web(runtime, bound=change != "unbound",
                       actor=OwnerActor("wrong", "web") if change == "wrong_actor" else None)
    if change == "readonly":
        runtime.exops.read_only = True
    elif change == "read_revoked":
        document["actions"].remove("read_work")
        write_policy(path, document)
        with pytest.raises(AdmissionError):
            web("work/methods", {})
        # Admission still requires submit_work, not an unrelated read permission.
        document["actions"].remove("submit_work")
        write_policy(path, document)
    elif change == "director":
        document["director_enabled"] = False
        write_policy(path, document)
    elif change == "registration":
        document["methods"][METADATA_METHOD_ID]["admission_scope"]["parameters"] = {"objective": "changed"}
        write_policy(path, document)
    elif change == "runner":
        runner.binding["adapter_digest"] = "sha256:" + "b" * 64
    elif change == "source":
        with (runtime.application.roots.vault / SOURCE).open("a") as note:
            note.write("\nchanged\n")
    elif change == "outside_scope":
        # Recompose an exact valid binding with a different owner-selected source.
        document["methods"][METADATA_METHOD_ID]["admission_scope"]["source_paths"] = ["other.md"]
        write_policy(path, document)
        runtime = WritableOwnerRuntime(runtime.application.roots, policy=OwnerRuntimePolicy(path),
            runners={method: runner for method in document["methods"]})
        web = selected_web(runtime)
    before = runtime.application.journal.read()
    with pytest.raises(AdmissionError):
        web("work/intake-selected", payload)
    assert runtime.application.journal.read() == before
    if change in {"unbound", "readonly"}:
        assert not any(item["available"] for item in web("work/methods", {})["methods"])


@pytest.mark.parametrize("change", ["parameters", "actor", "graph", "scope_extra", "bad_uuid",
                                  "bad_digest", "unknown_method"])
def test_selected_http_rejects_caller_parameters_and_malformed_identity(tmp_path, change):
    runtime, _runner, _canary, _path, _document = compose(tmp_path, ROUTE, NoModel)
    payload = selected_payload(runtime)
    if change == "scope_extra":
        payload["scope"]["parameters"] = {}
    elif change == "bad_uuid":
        payload["request_id"] = "-" * 36
    elif change == "bad_digest":
        payload["scope"]["content_sha256"] = "0" * 64
    elif change == "unknown_method":
        payload["method_id"] = "knowledgeos.unknown"
    else:
        payload[change] = {}
    before = runtime.application.journal.read()
    with pytest.raises(AdmissionError):
        selected_web(runtime)("work/intake-selected", payload)
    assert runtime.application.journal.read() == before


def test_selected_exact_replay_retains_spec_after_dispatch_and_rebuild_without_second_start(tmp_path):
    runtime, runner, _canary, path, document = compose(tmp_path, ROUTE, NoModel)
    payload = selected_payload(runtime)
    receipt = selected_web(runtime)("work/intake-selected", payload)
    key = "exops.work." + receipt["work_run_id"]
    initial = runtime.application.journal.read()["intents"][key]
    transport = Transport(runtime)
    asyncio.run(dispatch_admitted_work(runtime.exops, transport, receipt["work_run_id"]))
    rebuilt = WritableOwnerRuntime(runtime.application.roots, policy=OwnerRuntimePolicy(path),
        runners={method: runner for method in document["methods"]})
    before = rebuilt.application.journal.read()
    replay = selected_web(rebuilt)("work/intake-selected", payload)
    assert replay["replayed"] is True
    assert replay["work_run_id"] == receipt["work_run_id"]
    assert replay["dispatch_state"] == "accepted"
    assert rebuilt.application.journal.read() == before
    assert before["intents"][key]["spec"] == initial["spec"]
    assert before["intents"]["owner.director." + initial["spec"]["admission_receipt_id"]]["run"] == initial["run"]
    asyncio.run(DispatchSupervisor(rebuilt, {work_workflow_type(initial): transport}).tick())
    assert len(transport.starts) == 1
    assert len([item for item in before["intents"] if item.startswith("exops.work.")]) == 1
    changed = copy.deepcopy(payload)
    changed["scope"]["content_sha256"] = "0" * 64
    with pytest.raises(AdmissionError):
        selected_web(rebuilt)("work/intake-selected", changed)
    source = rebuilt.application.reference(SOURCE)
    with pytest.raises(AdmissionError, match="different content"):
        rebuilt.admit_selected(METADATA_METHOD_ID, source, request_id=payload["request_id"],
                               request={**payload, "changed": True})
    assert rebuilt.application.journal.read() == before


def test_selected_uses_only_fixed_owner_parameters(tmp_path):
    runtime, runner, _canary, path, document = compose(tmp_path, ROUTE, NoModel)
    parameters = {"objective": "Inspect only this exact selected source"}
    document["methods"][METADATA_METHOD_ID]["admission_scope"]["parameters"] = parameters
    write_policy(path, document)
    runtime = WritableOwnerRuntime(runtime.application.roots, policy=OwnerRuntimePolicy(path),
        runners={method: runner for method in document["methods"]})
    web = selected_web(runtime)
    selected = next(item for item in web("work/methods", {})["methods"] if item["method_id"] == METADATA_METHOD_ID)
    assert selected["parameters"] == parameters and selected["available"]
    payload = selected_payload(runtime)
    receipt = web("work/intake-selected", payload)
    record = runtime.application.journal.read()["intents"]["exops.work." + receipt["work_run_id"]]
    assert record["spec"]["objective"] == parameters["objective"]
    assert record["selected_scope"] == payload["scope"]
    with pytest.raises(AdmissionError, match="parameters differ"):
        runtime.admit_selected(METADATA_METHOD_ID, runtime.application.reference(SOURCE),
            request_id=str(uuid4()), request={}, objective="Caller replacement")


def test_selected_invalid_private_parameters_are_not_exported_or_admitted(tmp_path):
    runtime, runner, _canary, path, document = compose(tmp_path, ROUTE, NoModel)
    document["methods"][METADATA_METHOD_ID]["admission_scope"]["parameters"] = {
        "runner_binding": {"private_path": "/private/secret"}}
    write_policy(path, document)
    runtime = WritableOwnerRuntime(runtime.application.roots, policy=OwnerRuntimePolicy(path),
        runners={method: runner for method in document["methods"]})
    web = selected_web(runtime)
    catalog = web("work/methods", {})
    selected = next(item for item in catalog["methods"] if item["method_id"] == METADATA_METHOD_ID)
    assert not selected["available"] and selected["parameters"] == {}
    assert "/private/secret" not in str(catalog)
    before = runtime.application.journal.read()
    with pytest.raises(AdmissionError):
        web("work/intake-selected", selected_payload(runtime))
    assert runtime.application.journal.read() == before


@pytest.mark.parametrize("enabled", [False, True])
def test_daily_selected_admission_requires_existing_daily_authority(tmp_path, enabled):
    from support.metadata_fixture import application
    from support.owner_runtime_fixture import policy_document

    from vaultops.application.metadata_work import DAILY_METHOD_ID

    app, _canary = application(tmp_path, daily=True)
    with app.journal.writer() as writer:
        writer.commit(app.clock())
    document = policy_document(app, ROUTE, methods=(DAILY_METHOD_ID,))
    document["templates"] = []
    document["daily_enabled"] = enabled
    path = tmp_path / "owner-policy.json"
    write_policy(path, document)
    runtime = WritableOwnerRuntime(app.roots, policy=OwnerRuntimePolicy(path),
        runners={DAILY_METHOD_ID: NoModel(ROUTE, app)})
    web = selected_web(runtime)
    method = next(item for item in web("work/methods", {})["methods"] if item["method_id"] == DAILY_METHOD_ID)
    assert not method["available"] and method["parameters"] == {}
    payload = selected_payload(runtime)
    payload["method_id"] = DAILY_METHOD_ID
    before = runtime.application.journal.read()
    with pytest.raises(AdmissionError):
        web("work/intake-selected", payload)
    with pytest.raises(AdmissionError):
        runtime.admit_selected(DAILY_METHOD_ID, app.reference(SOURCE),
                               request_id=payload["request_id"], request={})
    assert runtime.application.journal.read() == before


def test_daily_direct_admission_and_retained_dispatch_are_revoked_with_daily_policy(tmp_path):
    from support.metadata_fixture import application
    from support.owner_runtime_fixture import policy_document

    from vaultops.application.metadata_work import DAILY_METHOD_ID

    app, _canary = application(tmp_path, daily=True)
    with app.journal.writer() as writer:
        writer.commit(app.clock())
    document = policy_document(app, ROUTE, methods=(DAILY_METHOD_ID,))
    document["templates"] = []
    document["daily_enabled"] = True
    path = tmp_path / "owner-policy.json"
    write_policy(path, document)
    runtime = WritableOwnerRuntime(app.roots, policy=OwnerRuntimePolicy(path),
        runners={DAILY_METHOD_ID: NoModel(ROUTE, app)})
    requests = runtime.application.daily_stewardship().due_check()["work_requests"]
    request = next(item for item in requests
                   if runtime.application.resolve_reference(item["source_reference"]) == SOURCE)
    receipt = runtime.admit_daily_request(request)
    record = runtime.application.journal.read()["intents"]["exops.work." + receipt["work_run_id"]]
    assert runtime.director_admitted(record["spec"])
    document["daily_enabled"] = False
    write_policy(path, document)
    before = runtime.application.journal.read()
    assert not runtime.director_admitted(record["spec"])
    with pytest.raises(AdmissionError):
        runtime.admit_daily_request(request)
    with pytest.raises(AdmissionError):
        runtime.exops.authorize_dispatch(work_run_id=receipt["work_run_id"],
            action_id=receipt["action_id"], content_digest=receipt["content_digest"])
    assert runtime.application.journal.read() == before

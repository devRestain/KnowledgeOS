"""Disposable native Officer round trip; host orchestration is standard-library only."""

from __future__ import annotations

import argparse
import asyncio
import copy
import hashlib
import http.client
import importlib.metadata
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from support.process_streams import iter_process_lines

MARKER = "KNOWLEDGEOS_OFFICER_EVIDENCE="
MODEL_REQUEST = "KNOWLEDGEOS_MODEL_REQUEST="
MODEL_RESPONSE = "KNOWLEDGEOS_MODEL_RESPONSE="
BASE_CAPTURE = "00_Inbox/Captures/2026/09/20260909-090000-mac-deadbeef.md"
SOURCE = "00_Inbox/Captures/2026/09/Native Officer smoke.md"
SERVER_IMAGE = "temporalio/server:1.31.3@sha256:b4a78d2186e558aa3502be08d576086c3a82fccdf6184cb4c819eb27bea7bd63"
POSTGRES_IMAGE = "postgres:16.14@sha256:95206741a5b214807675e14165369d05b93a9cf692223b616d07cca227e74b0b"
ADMIN_IMAGE = "temporalio/admin-tools:1.31.3@sha256:76a70ead160808b54acdbc98944dfc07990d9637dda999118a2d9ee1f58b9bc9"


def owner_http(exops, run_id: str, proposal: bytes) -> dict:
    from vaultops.adapters.core import AdmissionError, digest
    from vaultops.application.exops import OwnerActor
    from vaultops.interfaces.loopback_owner import create_loopback_owner

    server = create_loopback_owner(exops, actor_id="owner.officer_smoke", port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    def call(payload: dict) -> tuple[int, dict]:
        connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=10)
        try:
            connection.request("POST", "/owner/work/result", json.dumps(payload), headers={
                "Content-Type": "application/json"})
            response = connection.getresponse()
            return response.status, json.loads(response.read())
        finally:
            connection.close()

    try:
        injected_status, _ = call({"work_run_id": run_id, "actor_id": "other"})
        assert injected_status == 403
        try:
            exops.read_work(run_id, actor=OwnerActor("other", "web"))
        except AdmissionError as error:
            assert error.code == "ACTION_DENIED"
        else:
            raise AssertionError("Foreign actor read was accepted")
        status, result = call({"work_run_id": run_id})
        assert status == 200 and result["state"] == "completed", result
        assert result["assessment"]["completion_state"] == "complete"
        assert result["reason"] is None and result["truncated"] is False
        assert result["text"].encode() == proposal
        return {"route": "POST /owner/work/result", "status": status,
                "result_digest": digest(result["text"].encode()),
                "body_actor_status": injected_status,
                "wrong_actor_denied": True, "assessment": result["assessment"]}
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=10)


class ModelRendezvous:
    """One host-selected model invocation; no provider or host transport in Owner."""

    def __init__(self, binding: dict, application):
        self.binding = copy.deepcopy(binding)
        self.application = application
        self.response = None
        self.denials = {}

    async def __call__(self, request: dict) -> dict:
        from mcp import Client
        from mcp.client.stdio import StdioServerParameters

        parameters = StdioServerParameters(
            command=sys.executable,
            args=["-m", "vaultops.interfaces.mcp_server", "--control-root", request["control_root"],
                  "--work-run-id", request["work_run_id"], "--executor-id", request["executor_id"],
                  "--graph-run-id", request["graph_run_id"]],
            env={"PYTHONPATH": "/workspace/control/ops/src", "PYTHONDONTWRITEBYTECODE": "1"},
        )
        other = self.application.reference(BASE_CAPTURE)
        probes = {
            "wrong_source_read": ("knowledge_read", {
                "resource_reference": other, "chunk_locator": request["source_chunks"][0]}),
            "wrong_source_proposal": ("proposal_create", {"source_reference": other}),
            "ungranted_tool": ("index_status_read", {}),
        }
        async with Client(parameters, mode="legacy") as client:
            for label, (tool_name, arguments) in probes.items():
                result = await client.call_tool(tool_name, arguments)
                envelope = result.structured_content
                assert result.is_error and envelope["effect"] == "none", envelope
                assert envelope["status"] == "denied", envelope
                self.denials[label] = envelope
        print(MODEL_REQUEST + json.dumps(request, ensure_ascii=False), flush=True)
        async with asyncio.timeout(360):
            line = await asyncio.to_thread(sys.stdin.readline)
        if not line or len(line) > 1024 * 1024:
            raise RuntimeError("Native model returned no bounded response; no effect retry is allowed")
        self.response = json.loads(line)
        return self.response


async def native_roundtrip(root: Path, runner_binding: dict | None = None) -> dict:
    from clients.temporal import TemporalBinding
    from temporalio.api.workflowservice.v1 import RegisterNamespaceRequest
    from temporalio.client import Client
    from temporalio.service import RPCError, RPCStatusCode
    from temporalio.worker import Worker

    from support.control_factory import make_separate_portable_fixture_roots
    from vaultops.adapters.core import AdmissionError, canonical, digest
    from vaultops.application import normalization_work
    from vaultops.application.exops import KnowledgeExOps, OwnerActor
    from vaultops.application.knowledge import KnowledgeApplication
    from vaultops.application.normalization_work import (
        NormalizationOfficerExecutor,
        normalization_admission,
        normalization_native_digest,
    )
    from vaultops.application.team_catalog import OfficerBinding, TeamCatalog
    from vaultops.application.temporal_work import (
        WORKFLOW_TYPE,
        connect_owner_temporal,
        dispatch_admitted_work,
        worker_components,
    )
    from vaultops.projection import generate_projection

    assert importlib.metadata.version("temporalio") == "1.34.0"
    roots = make_separate_portable_fixture_roots(
        root, extra_inputs=("ops/config/team-catalog.json", "ops/config/work-methods.json"),
        review_queues=True)
    original = (roots.vault / BASE_CAPTURE).read_text()
    source_path = roots.vault / SOURCE
    source_path.write_text(original.replace("11111111-1111-4111-8111-111111111111", str(uuid4()))
                           .replace("20260909-090000-mac-deadbeef", "Native Officer smoke"))
    canary = "modelofficer-" + uuid4().hex if runner_binding is not None else None
    if canary is not None:
        source_path.write_text(source_path.read_text() + f"\nSynthetic observation code: {canary}\n")
    generated, code = generate_projection(roots)
    assert code == 0, generated
    application = KnowledgeApplication.from_roots(roots)
    source_reference = application.reference(SOURCE)
    source_bytes = source_path.read_bytes()
    vault_before = {path.relative_to(roots.vault).as_posix(): digest(path.read_bytes())
                    for path in roots.vault.rglob("*") if path.is_file()}
    runner = ModelRendezvous(runner_binding, application) if runner_binding is not None else None
    catalog = TeamCatalog.load(roots.control)
    candidate = OfficerBinding(
        officer_executor_id="knowledgeos.normalization_officer.native_smoke",
        officer_profile=catalog.profile("knowledgeos.normalization_officer"),
        eval_officer_executor_id="knowledgeos.eval_officer.native_smoke",
        eval_officer_profile=catalog.profile("knowledgeos.eval_officer"),
        catalog_digest=catalog.digest,
        native_binding_digest=normalization_native_digest(runner_binding=runner_binding),
        binding_reference={})
    binding_bytes = canonical(candidate.content())
    binding = replace(candidate, binding_reference={
        "owner_operation_id": "knowledgeos", "artifact_id": "artifact.officer.native_binding",
        "source_revision": digest(binding_bytes).removeprefix("sha256:"),
        "content_digest": digest(binding_bytes), "size_bytes": len(binding_bytes),
        "media_type": "application/json", "classification": "internal",
        "availability": "available_by_owner_export",
        "export_capability_id": "knowledgeos.officer_binding.export"})
    suffix = uuid4().hex
    spec, run = normalization_admission(
        application, binding, source_reference, work_id="work.native." + suffix,
        work_run_id="workrun.native." + suffix, admission_receipt_id="admission.native." + suffix,
        runner_binding=runner_binding)
    admitted_spec = copy.deepcopy(spec)
    admitted_source = copy.deepcopy(source_reference)

    def exact_spec(selected) -> bool:
        return dict(selected) == admitted_spec and application.reference(SOURCE) == admitted_source

    exops = KnowledgeExOps(
        application,
        authorize_actor=lambda actor, action: actor.actor_id == "owner.officer_smoke"
        and actor.ingress in {"cli", "web"} and action in {"submit_work", "read_work"},
        director_admitted=exact_spec, current_team_binding=lambda _team: None,
        worker_identity="knowledgeos.worker.native_smoke", officers={binding.officer_executor_id: binding},
        current_officer_binding=lambda executor: binding if executor == binding.officer_executor_id else None,
        director_officer_source_admitted=lambda selected, source: exact_spec(selected)
        and dict(source) == admitted_source)
    actor = OwnerActor("owner.officer_smoke", "cli")
    try:
        exops.submit_work(spec, run, actor=OwnerActor("other", "cli"),
                          action_id="action.denied." + suffix,
                          expires_at=datetime.now(UTC) + timedelta(minutes=10),
                          officer_source_reference=source_reference)
    except AdmissionError as error:
        assert error.code == "ACTION_DENIED"
    else:
        raise AssertionError("Foreign actor admission was accepted")
    admitted = exops.submit_work(
        spec, run, actor=actor, action_id="action.native." + suffix,
        expires_at=datetime.now(UTC) + timedelta(minutes=10),
        officer_source_reference=source_reference)
    assert admitted["dispatch_state"] == "pending"
    transport = TemporalBinding(
        owner_operation_id="knowledgeos", target_host="server:7233", namespace="knowledgeos",
        task_queue="knowledgeos.work.v1", workflow_type=WORKFLOW_TYPE,
        workflow_id_prefix="knowledgeos/", update_name="officer.update.disabled",
        signal_name="officer.signal.disabled", query_name="officer.query.disabled", tls=False)
    async with asyncio.timeout(180):
        while True:
            try:
                sdk = await Client.connect(transport.target_host, namespace=transport.namespace, tls=False)
                await sdk.workflow_service.register_namespace(RegisterNamespaceRequest(
                    namespace=transport.namespace, workflow_execution_retention_period={"seconds": 3600}))
                break
            except RPCError as error:
                if error.status == RPCStatusCode.ALREADY_EXISTS:
                    break
                await asyncio.sleep(1)
            except (OSError, RuntimeError):
                await asyncio.sleep(1)
    scoped = await connect_owner_temporal(exops, transport)
    workflow_type, activity_type = worker_components(exops, NormalizationOfficerExecutor(exops, runner=runner))
    async with Worker(sdk, task_queue=transport.task_queue, workflows=[workflow_type],
                      activities=[activity_type], identity=exops.worker_identity):
        receipt = await dispatch_admitted_work(exops, scoped, run["work_run_id"])
        workflow_id = "knowledgeos/" + run["work_run_id"]
        result = await asyncio.wait_for(sdk.get_workflow_handle(workflow_id).result(), timeout=420)
        assert result["application_state"] == "recorded", result
        payload = {"owner_operation_id": "knowledgeos", "work_run_id": run["work_run_id"],
                   "action_id": admitted["action_id"], "content_digest": admitted["content_digest"],
                   "work_spec_digest": spec["spec_digest"]}
        handle = sdk.get_workflow_handle(workflow_id)
        description = await handle.describe()
        assert description.id == workflow_id and bool(description.run_id)
        assert description.namespace == transport.namespace
        assert description.workflow_type == transport.workflow_type
        assert description.task_queue == transport.task_queue
        history = await handle.fetch_history()
        first = history.events[0]
        assert first.event_id == 1 and first.HasField("workflow_execution_started_event_attributes")
        started = first.workflow_execution_started_event_attributes
        assert started.workflow_id == workflow_id
        assert started.workflow_type.name == transport.workflow_type
        assert started.task_queue.name == transport.task_queue
        decoded = await sdk.data_converter.decode(started.input.payloads)
        assert decoded == [payload]
    from temporalio.api.enums.v1 import EventType

    event_counts = {}
    for event in history.events:
        name = EventType.Name(event.event_type)
        event_counts[name] = event_counts.get(name, 0) + 1
    assert event_counts.get("EVENT_TYPE_WORKFLOW_EXECUTION_STARTED") == 1
    assert event_counts.get("EVENT_TYPE_ACTIVITY_TASK_STARTED") == 1
    assert event_counts.get("EVENT_TYPE_ACTIVITY_TASK_COMPLETED") == 1
    assert event_counts.get("EVENT_TYPE_WORKFLOW_EXECUTION_COMPLETED") == 1
    record = application.journal.read()["intents"]["exops.work." + run["work_run_id"]]
    assert record["spec"] == admitted_spec
    assert record["officer_source_reference"] == admitted_source
    assert record["application_state"] == "recorded" and record["dispatch_state"] == "accepted"
    assert record["run"]["state"] == "completed"
    assert record["run"]["segments_started"] == record["run"]["profile_invocations"] == 1
    assert record["assessment"]["assessor_id"] == binding.eval_officer_executor_id
    assert record["assessment"]["assessor_id"] != binding.officer_executor_id
    outcomes = record["assessment"]["criterion_outcomes"]
    assert len(outcomes) == 1 and outcomes[0]["criterion_id"] == "criterion.proposal"
    assert outcomes[0]["outcome"] == "satisfied"
    pending = list((roots.vault / "01_AI_Review/Pending").glob("*.md"))
    assert len(pending) == 1 and source_path.read_bytes() == source_bytes
    vault_after = {path.relative_to(roots.vault).as_posix(): digest(path.read_bytes())
                   for path in roots.vault.rglob("*") if path.is_file()}
    changed = {path for path in vault_before if vault_before[path] != vault_after.get(path)}
    added = set(vault_after) - set(vault_before)
    assert not changed and added == {pending[0].relative_to(roots.vault).as_posix()}
    proposal = pending[0].read_bytes()
    http_evidence = await asyncio.to_thread(owner_http, exops, run["work_run_id"], proposal)
    assert source_path.read_bytes() == source_bytes
    if runner is not None:
        assert runner.response is not None and canary in runner.response["final_text"]
        assert runner.response["runner_binding"] == runner_binding
        assert len(runner.denials) == 3
        assert record["normalization_runner_binding"] == runner_binding
        assert record["normalization_model_execution"]["response"] == runner.response
    return {
        "status": "PASS",
        "scope": "disposable native model NormalizationOfficer" if runner else
                 "disposable deterministic NormalizationOfficer only",
        "claims_excluded": ["Team", "LangGraph", "Obsidian", "production roots"]
                           + ([] if runner else ["Hermes", "model"]),
        "versions": {"python": sys.version.split()[0], "temporalio": importlib.metadata.version("temporalio"),
                     "temporal_server": "1.31.3", "postgres": "16.14"},
        "implementation_digest": digest(Path(normalization_work.__file__).read_bytes()),
        "native_binding_digest": binding.native_binding_digest,
        "binding_content_digest": binding.binding_reference["content_digest"],
        "services_adapter_digest": digest(Path("/opt/services/clients/temporal.py").read_bytes()),
        "work_run_id": run["work_run_id"], "work_spec_digest": spec["spec_digest"],
        "workflow_id": description.id, "temporal_run_id": description.run_id,
        "namespace": description.namespace, "task_queue": description.task_queue,
        "exact_start_payload": decoded[0], "history_event_counts": event_counts,
        "receipt": receipt, "terminal_state": record["run"]["state"],
        "application_state": record["application_state"], "source_reference": source_reference,
        "source_unchanged": True, "existing_vault_files_unchanged": True,
        "pending_count": 1, "proposal_digest": digest(proposal), "canonical_apply": False,
        "independent_assessor_id": binding.eval_officer_executor_id,
        "officer_executor_id": binding.officer_executor_id, "owner_http": http_evidence,
        "model_execution": runner.response if runner else None,
        "model_canary": canary,
        "model_canary_observed": runner is not None and canary in runner.response["final_text"],
        "model_scope_denials": runner.denials if runner else {},
        "foreign_actor_admission_denied": True, "disposable_roots_removed": False}


def _native_model_response(request: dict, *, control: Path, container: str) -> dict:
    adapter = control / "ops/clients/hermes/native_officer.py"
    actual_digest = "sha256:" + hashlib.sha256(adapter.read_bytes()).hexdigest()
    if request["runner_binding"]["adapter_digest"] != actual_digest:
        raise RuntimeError("Native model adapter changed after owner binding")
    command = [
        os.environ["KNOWLEDGEOS_HERMES_PYTHON"], "-I", str(adapter),
        "--hermes-root", os.environ["KNOWLEDGEOS_HERMES_SOURCE"],
        "--profile-home", os.environ["KNOWLEDGEOS_HERMES_PROFILE_HOME"],
        "--docker-command", shutil.which("docker") or "docker",
        "--container-name", container,
    ]
    process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, text=True, start_new_session=True)
    try:
        stdout, stderr = process.communicate(json.dumps(request), timeout=300)
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGTERM)
        try:
            process.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.communicate(timeout=10)
        raise
    if stderr:
        print(stderr, file=sys.stderr, end="")
    if process.returncode:
        raise RuntimeError(f"Native model adapter failed ({process.returncode}); no automatic retry")
    lines = [line[len(MODEL_RESPONSE):] for line in stdout.splitlines()
             if line.startswith(MODEL_RESPONSE)]
    if len(lines) != 1:
        raise RuntimeError("Native Hermes returned no unique model response")
    return json.loads(lines[0])


def _model_container(compose: list[str], *, binding: dict, control: Path, container: str) -> str:
    command = compose + [
        "run", "--rm", "--no-deps", "-T", "--name", container, "smoke",
        "python", "/workspace/control/ops/tests/support/normalization_work_smoke.py",
        "run-model", "--runner-binding", json.dumps(binding),
    ]
    output = []
    with tempfile.TemporaryFile(mode="w+t") as errors:
        process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                   stderr=errors, text=True, bufsize=1)
        assert process.stdout is not None and process.stdin is not None
        try:
            deadline = time.monotonic() + 540
            requests = 0
            for line in iter_process_lines(process.stdout, deadline):
                output.append(line)
                if line.startswith(MODEL_REQUEST):
                    requests += 1
                    if requests != 1:
                        raise RuntimeError("A bounded Officer cannot request another model invocation")
                    request = json.loads(line[len(MODEL_REQUEST):])
                    if request["runner_binding"] != binding:
                        raise RuntimeError("Owner request differs from the selected native model binding")
                    response = _native_model_response(request, control=control, container=container)
                    process.stdin.write(json.dumps(response) + "\n")
                    process.stdin.flush()
            process.wait(timeout=30)
            if process.returncode:
                raise RuntimeError(f"Model Officer container failed ({process.returncode})")
            if requests != 1:
                raise RuntimeError("Owner never requested the bound model")
            return "".join(output)
        finally:
            process.stdin.close()
            if process.poll() is None:
                try:
                    process.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    process.terminate()
                    process.wait(timeout=15)
            process.stdout.close()
            errors.seek(0)
            stderr = errors.read()
            if stderr:
                print(stderr, file=sys.stderr, end="")


def host(evidence_path: Path, *, model_mode: bool = False) -> None:
    control = Path(__file__).resolve().parents[3]
    project = ("kos-model-" if model_mode else "kos-officer-") + uuid4().hex[:16]
    compose = ["docker", "compose", "-p", project, "-f", str(control / "ops/compose.officer-smoke.yaml")]
    evidence_path = evidence_path.expanduser().absolute()
    if evidence_path.exists():
        raise FileExistsError("Choose a new evidence path; existing evidence is not overwritten")
    evidence_path.parent.mkdir(parents=True, exist_ok=True)
    report = {"status": "FAIL", "compose_project": project, "cleanup_complete": False}
    failure = None
    try:
        requested_image = os.environ["KNOWLEDGEOS_OFFICER_IMAGE"]
        selected = subprocess.run(["docker", "image", "inspect", requested_image], check=True,
                                  capture_output=True, text=True, timeout=30)
        officer_image_id = json.loads(selected.stdout)[0]["Id"]
        os.environ["KNOWLEDGEOS_OFFICER_IMAGE"] = officer_image_id
        subprocess.run(compose + ["up", "-d", "--wait", "--wait-timeout", "120", "postgres", "server"],
                       check=True, timeout=180)
        if model_mode:
            adapter = control / "ops/clients/hermes/native_officer.py"
            binding = {
                "runtime_kind": "hermes_native", "hermes_version": "0.22.5",
                "hermes_revision": "bf0203a794a07aac3ff764972ab9087baa4e4f4e",
                "provider": "openai-codex", "model": "gpt-6.1-terra",
                "adapter_digest": "sha256:" + hashlib.sha256(adapter.read_bytes()).hexdigest(),
            }
            stdout = _model_container(compose, binding=binding, control=control,
                                      container=project + "-runner")
        else:
            completed = subprocess.run(compose + ["run", "--rm", "--no-deps", "-T", "smoke"],
                                       capture_output=True, text=True, timeout=360, check=False)
            if completed.stderr:
                print(completed.stderr, file=sys.stderr, end="")
            if completed.returncode:
                raise RuntimeError(f"Native smoke failed ({completed.returncode}): {completed.stdout}")
            stdout = completed.stdout
        lines = [line[len(MARKER):] for line in stdout.splitlines() if line.startswith(MARKER)]
        if len(lines) != 1:
            raise RuntimeError("Native smoke returned no unique evidence record")
        report = json.loads(lines[0])
        report["compose_project"] = project
        report["officer_image"] = {"requested": requested_image, "id": officer_image_id}
        images = [os.environ["KNOWLEDGEOS_OFFICER_IMAGE"], SERVER_IMAGE, POSTGRES_IMAGE, ADMIN_IMAGE]
        inspected = subprocess.run(["docker", "image", "inspect", *images], check=True,
                                   capture_output=True, text=True, timeout=30)
        report["images"] = [{"id": image["Id"], "repo_digests": image.get("RepoDigests", [])}
                            for image in json.loads(inspected.stdout)]
    except (OSError, ValueError, KeyError, subprocess.SubprocessError, RuntimeError, KeyboardInterrupt) as error:
        failure = error
        report["status"] = "FAIL"
        report["error"] = str(error)
        subprocess.run(compose + ["logs", "--no-color"], check=False, timeout=30)
    finally:
        cleanup = subprocess.run(compose + ["down", "--volumes", "--remove-orphans", "--timeout", "10"],
                                 check=False, timeout=90)
        remaining = subprocess.run(["docker", "ps", "-aq", "--filter", f"label=com.docker.compose.project={project}"],
                                   check=True, capture_output=True, text=True, timeout=30)
        networks = subprocess.run(["docker", "network", "ls", "-q", "--filter", f"label=com.docker.compose.project={project}"],
                                  check=True, capture_output=True, text=True, timeout=30)
        clean = cleanup.returncode == 0 and not remaining.stdout.strip() and not networks.stdout.strip()
        report["cleanup_complete"] = clean
        if not clean:
            report["status"] = "FAIL"
        with evidence_path.open("x", encoding="utf-8") as output:
            json.dump(report, output, ensure_ascii=False, indent=2)
            output.write("\n")
    if failure is not None:
        raise failure
    if report["status"] != "PASS":
        raise RuntimeError("Disposable cleanup failed")
    print(f"PASS: native Officer Temporal round trip and Owner HTTP export; evidence: {evidence_path}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("host", "host-model", "run", "run-model"))
    parser.add_argument("--evidence", type=Path)
    parser.add_argument("--runner-binding", type=json.loads)
    arguments = parser.parse_args()
    if arguments.action in {"host", "host-model"}:
        evidence = arguments.evidence or (
            Path(__file__).resolve().parents[5] / "Tmp"
            / ("knowledgeos-model-runner" if arguments.action == "host-model" else "knowledgeos-native-officer")
            / (uuid4().hex + ".json"))
        host(evidence, model_mode=arguments.action == "host-model")
    else:
        if (arguments.action == "run-model") != (arguments.runner_binding is not None):
            parser.error("Only run-model requires an explicit runner binding")
        os.umask(0o077)
        with tempfile.TemporaryDirectory(prefix="knowledgeos-native-officer-") as directory:
            report = asyncio.run(native_roundtrip(Path(directory), arguments.runner_binding))
        report["disposable_roots_removed"] = not Path(directory).exists()
        assert report["disposable_roots_removed"]
        print(MARKER + json.dumps(report, ensure_ascii=False, separators=(",", ":")))


if __name__ == "__main__":
    main()

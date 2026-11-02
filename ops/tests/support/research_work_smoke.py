"""Disposable native Evidence Research Team; host orchestration uses only the standard library."""

from __future__ import annotations

import argparse
import asyncio
import copy
import hashlib
import importlib.metadata
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from support import normalization_work_smoke as officer
from support.process_streams import iter_process_lines

MARKER = "KNOWLEDGEOS_RESEARCH_EVIDENCE="
SOURCE = "00_Inbox/Captures/2026/09/Native Evidence Research Team smoke.md"
GRAPH_NAME = "knowledgeos-evidence-research-v1"
NODES = ("manager", "retrieve_evidence", "answer_artifact", "assess")


class ResearchRendezvous:
    """Two exact native Profile calls; stdout/stdin only bridges to the host provider."""
    def __init__(self, binding, application):
        self.binding, self.application = copy.deepcopy(binding), application
        self.responses, self.requests, self.denials = [], [], {}

    async def __call__(self, request):
        from mcp import Client
        from mcp.client.stdio import StdioServerParameters
        parameters = StdioServerParameters(command=sys.executable, args=[
            "-m", "vaultops.interfaces.mcp_server", "--control-root", request["control_root"],
            "--work-run-id", request["work_run_id"], "--executor-id", request["executor_id"],
            "--graph-run-id", request["graph_run_id"],
        ], env={"PYTHONPATH": "/workspace/control/ops/src", "PYTHONDONTWRITEBYTECODE": "1"})
        stage = request["stage_id"]
        other = self.application.reference(officer.BASE_CAPTURE)
        probes = {"ungranted_tool": ("index_status_read", {})}
        if stage == "retrieve_evidence":
            probes["wrong_source_read"] = ("knowledge_read", {
                "resource_reference": other, "chunk_locator": request["source_chunks"][0]})
            probes["wrong_source_citation"] = ("citation_verify", {"citations": [{
                "resource_reference": other, "chunk_locator": request["source_chunks"][0],
                "chunk_hash": "0" * 64}]})
        else:
            probes["synthesizer_read"] = ("knowledge_read", {
                "resource_reference": request["source_reference"], "chunk_locator": request["source_chunks"][0]})
        async with Client(parameters, mode="legacy") as client:
            for label, (name, arguments) in probes.items():
                result = await client.call_tool(name, arguments)
                assert result.is_error and result.structured_content["effect"] == "none", result
                self.denials[stage + "." + label] = result.structured_content
            if stage == "retrieve_evidence":
                tampered = await client.call_tool("citation_verify", {"citations": [{
                    "resource_reference": request["source_reference"],
                    "chunk_locator": request["source_chunks"][0], "chunk_hash": "0" * 64,
                }]})
                assert not tampered.is_error and tampered.structured_content["data"]["all_valid"] is False
                self.denials["citation_tamper_rejected"] = tampered.structured_content
        self.requests.append(copy.deepcopy(request))
        print(officer.MODEL_REQUEST + json.dumps(request, ensure_ascii=False), flush=True)
        async with asyncio.timeout(360):
            line = await asyncio.to_thread(sys.stdin.readline)
        if not line or len(line) > 1024 * 1024:
            raise RuntimeError("Research returned no bounded native response; no retry")
        response = json.loads(line)
        self.responses.append(response)
        return response


async def native_roundtrip(root: Path, runner_binding: dict) -> dict:
    from clients.temporal import TemporalBinding
    from temporalio.api.workflowservice.v1 import RegisterNamespaceRequest
    from temporalio.client import Client
    from temporalio.service import RPCError, RPCStatusCode
    from temporalio.worker import Worker

    from support.control_factory import make_separate_portable_fixture_roots
    from vaultops.adapters.core import AdmissionError, canonical, digest
    from vaultops.application import research_work
    from vaultops.application.exops import KnowledgeExOps, OwnerActor
    from vaultops.application.knowledge import KnowledgeApplication
    from vaultops.application.research_temporal import research_worker_components
    from vaultops.application.research_work import (
        ResearchTeamExecutor,
        research_admission,
        research_native_digest,
    )
    from vaultops.application.team_catalog import SpecialistBinding, TeamBinding, TeamCatalog
    from vaultops.application.temporal_contract import RESEARCH_WORKFLOW_TYPE
    from vaultops.application.temporal_work import connect_owner_temporal, dispatch_admitted_work
    from vaultops.application.work import segment_digest
    from vaultops.projection import generate_projection

    versions = {
        package: importlib.metadata.version(package)
        for package in ("temporalio", "temporalio-langgraph", "langgraph")
    }
    assert versions == {
        "temporalio": "1.34.0",
        "temporalio-langgraph": "0.1.0",
        "langgraph": "1.2.0",
    }
    roots = make_separate_portable_fixture_roots(
        root,
        extra_inputs=("ops/config/team-catalog.json", "ops/config/work-methods.json"),
        review_queues=True,
    )
    original = (roots.vault / officer.BASE_CAPTURE).read_text()
    source_path = roots.vault / SOURCE
    canary = "modelresearch-" + uuid4().hex
    source_path.write_text(
        original.replace("11111111-1111-4111-8111-111111111111", str(uuid4())).replace(
            "20260909-090000-mac-deadbeef", Path(SOURCE).stem
        )
        + "\n" + ("Synthetic sample measurement: 17 milligrams; calibration uncertainty remains unverified. " * 65)
        + f"\nSynthetic observation code: {canary}\n"
    )
    generated, code = generate_projection(roots)
    assert code == 0, generated
    application = KnowledgeApplication.from_roots(roots)
    source_reference = application.reference(SOURCE)
    source_bytes = source_path.read_bytes()
    vault_before = {
        path.relative_to(roots.vault).as_posix(): digest(path.read_bytes())
        for path in roots.vault.rglob("*")
        if path.is_file()
    }
    runner = ResearchRendezvous(runner_binding, application)
    catalog = TeamCatalog.load(roots.control)
    candidate = TeamBinding(
        team_id="evidence-research",
        manager_executor_id="knowledgeos.research_manager.native_smoke",
        manager_profile=catalog.profile("knowledgeos.research_manager"),
        eval_officer_executor_id="knowledgeos.eval_officer.native_team_smoke",
        eval_officer_profile=catalog.profile("knowledgeos.eval_officer"),
        specialists=tuple(
            SpecialistBinding(role, role + ".native_smoke", catalog.profile(role))
            for role in ("knowledgeos.retriever", "knowledgeos.synthesizer")
        ),
        catalog_digest=catalog.digest,
        native_binding_digest=research_native_digest(runner_binding),
        binding_reference={},
    )
    binding_bytes = canonical(candidate.content())
    binding = replace(
        candidate,
        binding_reference={
            "owner_operation_id": "knowledgeos",
            "artifact_id": "artifact.research.native_binding",
            "source_revision": digest(binding_bytes).removeprefix("sha256:"),
            "content_digest": digest(binding_bytes),
            "size_bytes": len(binding_bytes),
            "media_type": "application/json",
            "classification": "internal",
            "availability": "available_by_owner_export",
            "export_capability_id": "knowledgeos.team_binding.export",
        },
    )
    suffix = uuid4().hex
    spec, run = research_admission(
        application,
        binding,
        source_reference,
        work_id="work.research." + suffix,
        work_run_id="workrun.research." + suffix,
        admission_receipt_id="admission.research." + suffix,
        runner_binding=runner_binding,
        question="Synthetic observation",
    )
    admitted_spec = copy.deepcopy(spec)
    admitted_source = copy.deepcopy(source_reference)

    def exact_spec(selected) -> bool:
        return dict(selected) == admitted_spec and application.reference(SOURCE) == admitted_source

    exops = KnowledgeExOps(
        application,
        authorize_actor=lambda actor, action: (
            actor.actor_id == "owner.officer_smoke"
            and actor.ingress in {"cli", "web"}
            and action in {"submit_work", "read_work"}
        ),
        director_admitted=exact_spec,
        teams={binding.team_id: binding},
        current_team_binding=lambda team: binding if team == binding.team_id else None,
        worker_identity="knowledgeos.worker.native_team_smoke",
        director_method_source_admitted=lambda selected, source: (
            exact_spec(selected) and dict(source) == admitted_source
        ),
    )
    from vaultops.application.intake_templates import build_template_admissions

    scope = {"path": SOURCE, "content_sha256": source_reference["source_revision"]}
    admissions = build_template_admissions(
        exops, "document_enhancement", scope, "research." + suffix,
        issue_director_receipt=lambda template, team, selected, request: "admission.research." + suffix,
        native_research_runner_binding=runner_binding, now=datetime.now(UTC),
    )
    spec, run, _, _, _ = admissions[0]
    admitted_spec = copy.deepcopy(spec)
    try:
        exops.submit_template_group("document_enhancement", scope, "denied." + suffix,
                                    admissions, actor=OwnerActor("other", "web"))
    except AdmissionError as error:
        assert error.code == "ACTION_DENIED"
    else:
        raise AssertionError("Foreign actor Research admission was accepted")
    group = exops.submit_template_group("document_enhancement", scope, "research." + suffix,
                                        admissions, actor=OwnerActor("owner.officer_smoke", "web"))
    admitted = group["receipts"][0]
    transport = TemporalBinding(
        owner_operation_id="knowledgeos",
        target_host="server:7233",
        namespace="knowledgeos",
        task_queue="knowledgeos.work.v1",
        workflow_type=RESEARCH_WORKFLOW_TYPE,
        workflow_id_prefix="knowledgeos/",
        update_name="team.update.disabled",
        signal_name="team.signal.disabled",
        query_name="team.query.disabled",
        tls=False,
    )
    async with asyncio.timeout(180):
        while True:
            try:
                sdk = await Client.connect(
                    transport.target_host, namespace=transport.namespace, tls=False
                )
                await sdk.workflow_service.register_namespace(
                    RegisterNamespaceRequest(
                        namespace=transport.namespace,
                        workflow_execution_retention_period={"seconds": 3600},
                    )
                )
                break
            except RPCError as error:
                if error.status == RPCStatusCode.ALREADY_EXISTS:
                    break
                await asyncio.sleep(1)
            except (OSError, RuntimeError):
                await asyncio.sleep(1)
    scoped = await connect_owner_temporal(exops, transport)
    workflow, plugin = research_worker_components(exops, ResearchTeamExecutor(exops, runner=runner))
    async with Worker(
        sdk,
        task_queue=transport.task_queue,
        workflows=[workflow],
        plugins=[plugin],
        identity=exops.worker_identity,
    ):
        receipt = await dispatch_admitted_work(exops, scoped, run["work_run_id"])
        workflow_id = "knowledgeos/" + run["work_run_id"]
        handle = sdk.get_workflow_handle(workflow_id)
        result = await asyncio.wait_for(handle.result(), timeout=780)
        assert result["application_state"] == "recorded", result
        payload = {
            "owner_operation_id": "knowledgeos",
            "work_run_id": run["work_run_id"],
            "action_id": admitted["action_id"],
            "content_digest": admitted["content_digest"],
            "work_spec_digest": spec["spec_digest"],
        }
        description = await handle.describe()
        assert description.id == workflow_id and bool(description.run_id)
        assert description.namespace == transport.namespace
        assert description.workflow_type == RESEARCH_WORKFLOW_TYPE
        assert description.task_queue == transport.task_queue
        history = await handle.fetch_history()
        first = history.events[0]
        assert first.event_id == 1 and first.HasField("workflow_execution_started_event_attributes")
        started = first.workflow_execution_started_event_attributes
        assert (
            started.workflow_id == workflow_id and started.workflow_type.name == RESEARCH_WORKFLOW_TYPE
        )
        assert started.task_queue.name == transport.task_queue
        decoded = await sdk.data_converter.decode(started.input.payloads)
        assert decoded == [payload]

    scheduled = [event.activity_task_scheduled_event_attributes.activity_type.name
                 for event in history.events if event.HasField("activity_task_scheduled_event_attributes")]
    assert scheduled == [GRAPH_NAME + "." + node for node in NODES], scheduled
    assert all(event.activity_task_scheduled_event_attributes.retry_policy.maximum_attempts == 1
               for event in history.events if event.HasField("activity_task_scheduled_event_attributes"))
    snapshot = application.journal.read()
    record = snapshot["intents"]["exops.work." + run["work_run_id"]]
    assert record["run"]["state"] == "completed" and record["application_state"] == "recorded"
    assert record["spec"] == admitted_spec and record["method_source_reference"] == admitted_source
    assert record["run"]["segments_started"] == 1 and record["run"]["profile_invocations"] == 2
    assert record["assessment"]["completion_state"] == "complete"
    assert record["assessment"]["assessor_id"] == binding.eval_officer_executor_id
    assert binding.eval_officer_executor_id not in spec["allowed_executor_ids"]
    assert record["evaluations"][0]["passed_criterion_ids"] == ["criterion.cited_report"]
    segment = next(iter(record["admitted_method_segments"].values()))["segment"]
    assert segment_digest(segment) == segment["segment_digest"]
    assert len(runner.responses) == 2 and [request["stage_id"] for request in runner.requests] == [
        "retrieve_evidence", "answer_artifact"]
    assert canary not in json.dumps(runner.requests[0]) and canary not in spec["objective"]
    reads = [call["result"]["data"] for call in runner.responses[0]["tool_calls"]
             if call["name"] == "knowledge_read"]
    assert canary in "".join(page["text"] for page in reads)
    assert any(page["offset"] > 0 for page in reads), "Actual native retrieval did not exercise paging"
    assert canary in runner.responses[0]["final_text"]
    assert runner.responses[1]["exposed_tools"] == ["artifact_submit"]
    report = json.loads(runner.responses[1]["tool_calls"][0]["arguments"]["content"])
    assert canary in json.dumps(report) and report["source_reference"] == source_reference
    pending = list((roots.vault / "01_AI_Review/Pending").glob("*.md"))
    assert len(pending) == 1 and source_path.read_bytes() == source_bytes
    artifact = pending[0].read_bytes()
    assert record["run"]["result_reference"]["content_digest"] == digest(artifact)
    http_evidence = await asyncio.to_thread(research_owner_http, exops, run["work_run_id"], artifact,
                                             source_path, source_bytes, report)
    after = {path.relative_to(roots.vault).as_posix(): digest(path.read_bytes())
             for path in roots.vault.rglob("*") if path.is_file()}
    assert all(after.get(path) == value for path, value in vault_before.items())
    assert set(after) - set(vault_before) == {pending[0].relative_to(roots.vault).as_posix()}
    assert not snapshot["decisions"]
    installed = {}
    for relative in (
        "application/research_work.py", "application/research_temporal.py", "application/research_workflow.py",
        "application/temporal_contract.py", "application/temporal_work.py", "application/exops.py",
        "application/work_tools.py", "application/work_methods.py", "application/mcp_effects.py",
        "application/read_tools.py", "interfaces/mcp_server.py", "interfaces/exops_web.py",
        "interfaces/loopback_owner.py",
        "application/intake_templates.py",
    ):
        installed_raw = Path("/opt/knowledgeos/ops/src/vaultops", relative).read_bytes()
        source_raw = Path("/workspace/control/ops/src/vaultops", relative).read_bytes()
        assert installed_raw == source_raw, "Installed Research module differs: " + relative
        installed[relative] = digest(installed_raw)
    return {
        "status": "PASS", "scope": "disposable native Evidence Research Team",
        "claims_excluded": ["production", "GUI", "retries", "control", "recovery", "canonical effects", "model self-certification"],
        "versions": versions, "implementation_digest": digest(Path(research_work.__file__).read_bytes()),
        "installed_modules": installed, "native_binding_digest": binding.native_binding_digest,
        "team_id": binding.team_id, "work_run_id": run["work_run_id"],
        "work_spec_digest": spec["spec_digest"], "workflow_id": description.id,
        "workflow_type": description.workflow_type, "temporal_run_id": description.run_id,
        "history_activities": scheduled, "history_event_count": len(history.events),
        "exact_start_payload": decoded[0], "receipt": receipt,
        "segments_started": 1, "profile_invocations": 2, "native_model_invocations": 2,
        "source_reference": source_reference, "source_unchanged": True,
        "existing_vault_files_unchanged": True, "pending_count": 1, "artifact_digest": digest(artifact),
        "assessment": record["assessment"], "evaluations": record["evaluations"],
        "retrieval": record["research_retrieve_evidence"], "synthesis": record["research_answer_artifact"],
        "report": report, "owner_http": http_evidence, "model_scope_denials": runner.denials,
        "model_canary": canary, "model_canary_observed": True,
        "foreign_actor_admission_denied": True, "disposable_roots_removed": False,
    }


def research_owner_http(exops, run_id, artifact, source_path, original, expected_report):
    import http.client
    import threading

    from vaultops.interfaces.loopback_owner import create_loopback_owner
    server = create_loopback_owner(exops, actor_id="owner.officer_smoke", port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    def call(payload):
        connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=15)
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
        status, result = call({"work_run_id": run_id})
        assert status == 200 and result["text"].encode() == artifact, result
        assert result["report"] == expected_report and result["evidence"]
        assert result["assessment"]["completion_state"] == "complete"
        source_path.write_bytes(original + b"\nStale research source.\n")
        stale_status, stale = call({"work_run_id": run_id})
        assert stale_status == 200 and stale["text"].encode() == artifact, stale
        assert stale["historical"] and stale["source_stale"] and not stale["current_evidence_valid"], stale
        source_path.write_bytes(original)
        return {"route": "POST /owner/work/result", "status": status,
                "body_actor_status": injected_status,
                "stale_result_status": stale_status, "report": result["report"], "evidence": result["evidence"]}
    finally:
        source_path.write_bytes(original)
        server.shutdown()
        server.server_close()
        thread.join(timeout=15)


def native_adapter_digest(adapter):
    content = {"adapter_sha256": hashlib.sha256(adapter.read_bytes()).hexdigest(),
               "runtime_sha256": hashlib.sha256(adapter.with_name("native_runtime.py").read_bytes()).hexdigest()}
    raw = json.dumps(content, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def native_model_response(request: dict, *, control: Path, container: str) -> dict:
    """Use exact Research adapter and helper, with parent-owned interruption cleanup."""
    adapter = control / "ops/clients/hermes/native_research.py"
    if (
        request["runner_binding"]["adapter_digest"]
        != native_adapter_digest(adapter)
    ):
        raise RuntimeError("Native adapter changed after owner binding")
    command = [
        os.environ["KNOWLEDGEOS_HERMES_PYTHON"],
        "-I",
        str(adapter),
        "--hermes-root",
        os.environ["KNOWLEDGEOS_HERMES_SOURCE"],
        "--profile-home",
        os.environ["KNOWLEDGEOS_HERMES_PROFILE_HOME"],
        "--docker-command",
        shutil.which("docker") or "docker",
        "--container-name",
        container,
    ]
    with tempfile.TemporaryDirectory(prefix="knowledgeos-team-model-") as directory:
        # The unchanged adapter creates its own fresh Hermes home underneath this
        # parent-owned root. Even a forced subprocess exit cannot strand that home.
        process = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,
            env={**os.environ, "TMPDIR": directory},
        )
        try:
            stdout, stderr = process.communicate(json.dumps(request), timeout=300)
            if stderr:
                print(stderr, file=sys.stderr, end="")
            if process.returncode:
                raise RuntimeError(
                    f"Native model adapter failed ({process.returncode}); no automatic retry"
                )
            lines = [
                line[len(officer.MODEL_RESPONSE) :]
                for line in stdout.splitlines()
                if line.startswith(officer.MODEL_RESPONSE)
            ]
            if len(lines) != 1:
                raise RuntimeError("Native Hermes returned no unique model response")
            response = json.loads(lines[0])
        finally:
            if process.poll() is None:
                # SIGINT unwinds Python finally blocks, unlike default SIGTERM.
                os.killpg(process.pid, signal.SIGINT)
                try:
                    process.communicate(timeout=15)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.communicate(timeout=15)
            for stream in (process.stdin, process.stdout, process.stderr):
                if stream is not None:
                    stream.close()
    if Path(directory).exists():
        raise RuntimeError("Native model temporary root survived cleanup")
    return response


def model_container(compose: list[str], *, binding: dict, control: Path, container: str) -> str:
    command = compose + [
        "run",
        "--rm",
        "--no-deps",
        "-T",
        "--name",
        container,
        "smoke",
        "python",
        "/workspace/control/ops/tests/support/research_work_smoke.py",
        "run",
        "--runner-binding",
        json.dumps(binding),
    ]
    output = []
    with tempfile.TemporaryFile(mode="w+t") as errors:
        process = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=errors,
            text=True,
            bufsize=1,
        )
        assert process.stdout is not None and process.stdin is not None
        try:
            deadline = time.monotonic() + 1000
            requests = 0
            for line in iter_process_lines(process.stdout, deadline):
                output.append(line)
                if line.startswith(officer.MODEL_REQUEST):
                    requests += 1
                    if requests > 2:
                        raise RuntimeError(
                            "A bounded Team cannot request another model invocation"
                        )
                    request = json.loads(line[len(officer.MODEL_REQUEST) :])
                    if request.get("stage_id") != ("retrieve_evidence", "answer_artifact")[requests - 1]:
                        raise RuntimeError("Native Research stage repeated or reordered")
                    if request["runner_binding"] != binding:
                        raise RuntimeError(
                            "Owner request differs from the selected native model binding"
                        )
                    response = native_model_response(
                        request, control=control, container=container
                    )
                    process.stdin.write(json.dumps(response) + "\n")
                    process.stdin.flush()
            process.wait(timeout=30)
            if process.returncode:
                raise RuntimeError(
                    f"Native Team container failed ({process.returncode}): {''.join(output)}"
                )
            if requests != 2:
                raise RuntimeError("Team never requested the bound research Profiles")
            return "".join(output)
        finally:
            process.stdin.close()
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=15)
            process.stdout.close()
            errors.seek(0)
            stderr = errors.read()
            if stderr:
                print(stderr, file=sys.stderr, end="")


def cleanup_project(compose: list[str], project: str, container: str) -> dict:
    errors = []
    try:
        subprocess.run(
            ["docker", "rm", "--force", container],
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError) as error:
        errors.append(str(error))
    try:
        down = subprocess.run(
            compose + ["down", "--volumes", "--remove-orphans", "--timeout", "10"],
            check=False,
            capture_output=True,
            text=True,
            timeout=90,
        )
        if down.returncode:
            errors.append(down.stderr or "Compose down failed")
    except (OSError, subprocess.SubprocessError) as error:
        errors.append(str(error))
    remaining = {}
    for kind, command in (
        ("containers", ["docker", "ps", "-aq"]),
        ("networks", ["docker", "network", "ls", "-q"]),
        ("volumes", ["docker", "volume", "ls", "-q"]),
    ):
        try:
            result = subprocess.run(
                command + ["--filter", f"label=com.docker.compose.project={project}"],
                check=True,
                capture_output=True,
                text=True,
                timeout=30,
            )
            remaining[kind] = result.stdout.split()
        except (OSError, subprocess.SubprocessError) as error:
            errors.append(str(error))
    return {
        "complete": not errors and not any(remaining.values()),
        "remaining": remaining,
        "errors": errors,
    }


def preservation_snapshot(control):
    """Hash live domain/profile identity, allowing only selected OAuth token rotation."""
    vault = (control / "../../Vaults/KnowledgeHub").resolve()
    profile = Path(os.environ["KNOWLEDGEOS_HERMES_PROFILE_HOME"]).resolve()
    selected = {}
    for path in vault.rglob("*"):
        relative = path.relative_to(vault)
        if path.is_file() and not any(part.startswith(".") for part in relative.parts):
            selected["vault/" + relative.as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    selected_auth = (profile / "auth.json").resolve()
    for path in profile.parent.rglob("*"):
        if path.is_file() and path.name in {"config.yaml", "auth.json", ".env"}:
            raw = path.read_bytes()
            if path.resolve() == selected_auth:
                document = json.loads(raw)
                provider = document.get("providers", {}).get("openai-codex", {})
                tokens = provider.get("tokens", {})
                if tokens:
                    # Refresh changes secret token bytes and timestamps, not account
                    # identity, credential presence, other providers or configuration.
                    for name in ("access_token", "refresh_token", "id_token"):
                        if name in tokens:
                            tokens[name] = {"present": isinstance(tokens[name], str) and bool(tokens[name])}
                    if "last_refresh" in provider:
                        provider["last_refresh"] = "normal-token-refresh"
                    if "updated_at" in document:
                        document["updated_at"] = "normal-token-refresh"
                    raw = json.dumps(document, sort_keys=True, separators=(",", ":")).encode()
            selected["profiles/" + path.relative_to(profile.parent).as_posix()] = hashlib.sha256(raw).hexdigest()
    if not selected:
        raise RuntimeError("Live preservation inputs are unavailable")
    return selected


def host(evidence_path: Path) -> None:
    control = Path(__file__).resolve().parents[3]
    evidence_path = evidence_path.expanduser().absolute()
    evidence_path.parent.mkdir(parents=True, exist_ok=True)
    project = "kos-team-" + uuid4().hex[:16]
    container = project + "-runner"
    compose = [
        "docker",
        "compose",
        "-p",
        project,
        "-f",
        str(control / "ops/compose.team-smoke.yaml"),
    ]
    report = {"status": "FAIL", "compose_project": project, "cleanup_complete": False}
    failure = None
    preserved = preservation_snapshot(control)

    def interrupted(_number, _frame):
        raise KeyboardInterrupt("Native Team smoke interrupted")

    previous_sigterm = signal.signal(signal.SIGTERM, interrupted)
    try:
        with evidence_path.open("x", encoding="utf-8") as evidence:
            try:
                requested_image = os.environ["KNOWLEDGEOS_TEAM_IMAGE"]
                selected = subprocess.run(
                    ["docker", "image", "inspect", requested_image],
                    check=True,
                    capture_output=True,
                    text=True,
                    timeout=30,
                )
                team_image_id = json.loads(selected.stdout)[0]["Id"]
                os.environ["KNOWLEDGEOS_TEAM_IMAGE"] = team_image_id
                adapter = control / "ops/clients/hermes/native_research.py"
                binding = {
                    "runtime_kind": "hermes_native",
                    "hermes_version": "0.22.5",
                    "hermes_revision": "bf0203a794a07aac3ff764972ab9087baa4e4f4e",
                    "provider": "openai-codex",
                    "model": "gpt-6.1-terra",
                    "adapter_digest": native_adapter_digest(adapter),
                }
                subprocess.run(
                    compose + ["up", "-d", "--wait", "--wait-timeout", "120", "postgres", "server"],
                    check=True,
                    timeout=180,
                )
                stdout = model_container(
                    compose, binding=binding, control=control, container=container
                )
                lines = [
                    line[len(MARKER) :] for line in stdout.splitlines() if line.startswith(MARKER)
                ]
                if len(lines) != 1:
                    raise RuntimeError("Native Team returned no unique evidence record")
                report = json.loads(lines[0])
                if (
                    report.get("status") != "PASS"
                    or report.get("disposable_roots_removed") is not True
                ):
                    raise RuntimeError(
                        "Native Team did not complete and remove its disposable roots"
                    )
                report["compose_project"] = project
                report["team_image"] = {"requested": requested_image, "id": team_image_id}
                inspected = subprocess.run(
                    [
                        "docker",
                        "image",
                        "inspect",
                        team_image_id,
                        officer.SERVER_IMAGE,
                        officer.POSTGRES_IMAGE,
                        officer.ADMIN_IMAGE,
                    ],
                    check=True,
                    capture_output=True,
                    text=True,
                    timeout=30,
                )
                report["images"] = [
                    {"id": image["Id"], "repo_digests": image.get("RepoDigests", [])}
                    for image in json.loads(inspected.stdout)
                ]
                report["model_temporary_roots_removed"] = True
            except (
                OSError,
                ValueError,
                KeyError,
                subprocess.SubprocessError,
                RuntimeError,
                KeyboardInterrupt,
            ) as error:
                failure = error
                report["status"] = "FAIL"
                report["error"] = str(error)
                try:
                    subprocess.run(compose + ["logs", "--no-color"], check=False, timeout=30)
                except (OSError, subprocess.SubprocessError):
                    pass
            finally:
                cleanup = cleanup_project(compose, project, container)
                report["cleanup"] = cleanup
                report["cleanup_complete"] = cleanup["complete"]
                after = preservation_snapshot(control)
                report["preservation"] = {
                    "unchanged": preserved == after,
                    "domain_files": sum(key.startswith("vault/") for key in preserved),
                    "profile_config_auth_files": sum(key.startswith("profiles/") for key in preserved),
                }
                if preserved != after:
                    report["status"] = "FAIL"
                    failure = RuntimeError("Live domain or profile configuration changed")
                if not cleanup["complete"]:
                    report["status"] = "FAIL"
                json.dump(report, evidence, ensure_ascii=False, indent=2)
                evidence.write("\n")
    finally:
        signal.signal(signal.SIGTERM, previous_sigterm)
    if failure is not None:
        raise failure
    if report["status"] != "PASS":
        raise RuntimeError("Disposable Team cleanup failed")
    print(
        f"PASS: native Team Temporal/LangGraph, research Profiles and Owner export; evidence: {evidence_path}"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("host", "run"))
    parser.add_argument("--evidence", type=Path)
    parser.add_argument("--runner-binding", type=json.loads)
    arguments = parser.parse_args()
    if arguments.action == "host":
        if arguments.runner_binding is not None:
            parser.error("The host binds the exact native model adapter itself")
        evidence = arguments.evidence or (
            Path(__file__).resolve().parents[5]
            / "Tmp"
            / "knowledgeos-native-research"
            / (uuid4().hex + ".json")
        )
        host(evidence)
    else:
        if arguments.runner_binding is None or arguments.evidence is not None:
            parser.error("Container run requires only an explicit --runner-binding")
        os.umask(0o077)
        with tempfile.TemporaryDirectory(prefix="knowledgeos-native-research-") as directory:
            report = asyncio.run(native_roundtrip(Path(directory), arguments.runner_binding))
        report["disposable_roots_removed"] = not Path(directory).exists()
        assert report["disposable_roots_removed"]
        print(MARKER + json.dumps(report, ensure_ascii=False, separators=(",", ":")))


if __name__ == "__main__":
    main()

"""Disposable native Curation Team; host orchestration uses only the standard library."""

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
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import normalization_work_smoke as officer

from support.process_streams import iter_process_lines

MARKER = "KNOWLEDGEOS_TEAM_EVIDENCE="
SOURCE = "00_Inbox/Captures/2026/09/Native Curation Team smoke.md"
GRAPH_NAME = "knowledgeos-curation-v1"
NODES = ("manager", "capture_evidence", "normalization_proposal", "assess")


async def native_roundtrip(root: Path, runner_binding: dict) -> dict:
    from clients.temporal import TemporalBinding
    from temporalio.api.enums.v1 import EventType
    from temporalio.api.workflowservice.v1 import RegisterNamespaceRequest
    from temporalio.client import Client
    from temporalio.service import RPCError, RPCStatusCode
    from temporalio.worker import Worker

    from support.control_factory import make_separate_portable_fixture_roots
    from vaultops.adapters.core import AdmissionError, canonical, digest
    from vaultops.application import curation_work
    from vaultops.application.curation_temporal import team_worker_components
    from vaultops.application.curation_work import (
        CurationTeamExecutor,
        curation_admission,
        curation_native_digest,
    )
    from vaultops.application.exops import KnowledgeExOps, OwnerActor
    from vaultops.application.knowledge import KnowledgeApplication
    from vaultops.application.team_catalog import SpecialistBinding, TeamBinding, TeamCatalog
    from vaultops.application.temporal_contract import TEAM_WORKFLOW_TYPE
    from vaultops.application.temporal_work import connect_owner_temporal, dispatch_admitted_work
    from vaultops.application.work import record_digest, segment_digest
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
    canary = "modelcuration-" + uuid4().hex
    source_path.write_text(
        original.replace("11111111-1111-4111-8111-111111111111", str(uuid4())).replace(
            "20260909-090000-mac-deadbeef", "Native Curation Team smoke"
        )
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
    runner = officer.ModelRendezvous(runner_binding, application)
    catalog = TeamCatalog.load(roots.control)
    candidate = TeamBinding(
        team_id="knowledge-curation",
        manager_executor_id="knowledgeos.curation_manager.native_smoke",
        manager_profile=catalog.profile("knowledgeos.curation_manager"),
        eval_officer_executor_id="knowledgeos.eval_officer.native_team_smoke",
        eval_officer_profile=catalog.profile("knowledgeos.eval_officer"),
        specialists=tuple(
            SpecialistBinding(role, role + ".native_smoke", catalog.profile(role))
            for role in ("knowledgeos.triage", "knowledgeos.normalizer")
        ),
        catalog_digest=catalog.digest,
        native_binding_digest=curation_native_digest(runner_binding),
        binding_reference={},
    )
    binding_bytes = canonical(candidate.content())
    binding = replace(
        candidate,
        binding_reference={
            "owner_operation_id": "knowledgeos",
            "artifact_id": "artifact.curation.native_binding",
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
    spec, run = curation_admission(
        application,
        binding,
        source_reference,
        work_id="work.curation." + suffix,
        work_run_id="workrun.curation." + suffix,
        admission_receipt_id="admission.curation." + suffix,
        runner_binding=runner_binding,
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
    expires_at = datetime.now(UTC) + timedelta(minutes=10)
    try:
        exops.submit_work(
            spec,
            run,
            actor=OwnerActor("other", "cli"),
            action_id="action.denied." + suffix,
            expires_at=expires_at,
            method_source_reference=source_reference,
        )
    except AdmissionError as error:
        assert error.code == "ACTION_DENIED"
    else:
        raise AssertionError("Foreign actor Team admission was accepted")
    admitted = exops.submit_work(
        spec,
        run,
        actor=OwnerActor("owner.officer_smoke", "cli"),
        action_id="action.curation." + suffix,
        expires_at=expires_at,
        method_source_reference=source_reference,
    )
    assert admitted["dispatch_state"] == "pending"
    transport = TemporalBinding(
        owner_operation_id="knowledgeos",
        target_host="server:7233",
        namespace="knowledgeos",
        task_queue="knowledgeos.work.v1",
        workflow_type=TEAM_WORKFLOW_TYPE,
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
    workflow, plugin = team_worker_components(exops, CurationTeamExecutor(exops, runner=runner))
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
        result = await asyncio.wait_for(handle.result(), timeout=420)
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
        assert description.workflow_type == TEAM_WORKFLOW_TYPE
        assert description.task_queue == transport.task_queue
        history = await handle.fetch_history()
        first = history.events[0]
        assert first.event_id == 1 and first.HasField("workflow_execution_started_event_attributes")
        started = first.workflow_execution_started_event_attributes
        assert (
            started.workflow_id == workflow_id and started.workflow_type.name == TEAM_WORKFLOW_TYPE
        )
        assert started.task_queue.name == transport.task_queue
        decoded = await sdk.data_converter.decode(started.input.payloads)
        assert decoded == [payload]

    event_counts = {}
    scheduled = []
    completed = []
    for event in history.events:
        name = EventType.Name(event.event_type)
        event_counts[name] = event_counts.get(name, 0) + 1
        if event.HasField("activity_task_scheduled_event_attributes"):
            activity = event.activity_task_scheduled_event_attributes
            assert activity.retry_policy.maximum_attempts == 1
            scheduled.append((event.event_id, activity.activity_type.name))
        if event.HasField("activity_task_completed_event_attributes"):
            completed.append(event.activity_task_completed_event_attributes.scheduled_event_id)
    expected_activities = [GRAPH_NAME + "." + node for node in NODES]
    assert [name for _, name in scheduled] == expected_activities, scheduled
    assert completed == [event_id for event_id, _ in scheduled]
    assert event_counts.get("EVENT_TYPE_WORKFLOW_EXECUTION_STARTED") == 1
    assert event_counts.get("EVENT_TYPE_ACTIVITY_TASK_STARTED") == len(NODES)
    assert event_counts.get("EVENT_TYPE_ACTIVITY_TASK_COMPLETED") == len(NODES)
    assert event_counts.get("EVENT_TYPE_WORKFLOW_EXECUTION_COMPLETED") == 1
    assert not any(
        event_counts.get(name, 0)
        for name in (
            "EVENT_TYPE_ACTIVITY_TASK_FAILED",
            "EVENT_TYPE_ACTIVITY_TASK_TIMED_OUT",
            "EVENT_TYPE_WORKFLOW_EXECUTION_FAILED",
            "EVENT_TYPE_WORKFLOW_EXECUTION_CONTINUED_AS_NEW",
        )
    )
    snapshot = application.journal.read()
    record = snapshot["intents"]["exops.work." + run["work_run_id"]]
    assert record["spec"] == admitted_spec and record["method_source_reference"] == admitted_source
    assert record["application_state"] == "recorded" and record["dispatch_state"] == "accepted"
    assert record["run"]["state"] == "completed"
    assert record["run"]["segments_started"] == 1 and record["run"]["profile_invocations"] == 2
    assert len(record["run"]["graph_run_ids"]) == 1
    segments = record["admitted_method_segments"]
    assert list(segments) == record["run"]["graph_run_ids"]
    graph_run_id, segment_record = next(iter(segments.items()))
    segment = segment_record["segment"]
    assert segment_digest(segment) == segment["segment_digest"]
    assert segment_record["source_reference"] == admitted_source
    assert segment_record["native_binding_digest"] == binding.native_binding_digest
    assert segment_record["method_contract_ref"] == spec["method_contract_ref"]
    assert {item["executor_id"] for item in segment_record["assignments"]} == set(
        spec["allowed_executor_ids"]
    )
    assert len(segment_record["assignments"]) == 2
    events = [
        item["event"]
        for item in snapshot["history"]
        if item.get("work_run_id") == run["work_run_id"]
    ]
    assert events.count("exops_method_graph_admitted") == 1
    triage = record["curation_capture_evidence"]
    assert triage["evidence_digest"] == record_digest(
        {"context": triage["context"], "reads": triage["reads"]}
    )
    assert triage["context"]["executor_id"] == spec["allowed_executor_ids"][0]
    assert triage["context"]["source_reference"] == admitted_source
    assert triage["context"]["allowed_tools"] == ["work_context_read", "knowledge_read"]
    assert [item["arguments"]["chunk_locator"] for item in triage["reads"]] == triage["context"][
        "source_chunks"
    ]
    assert canary in "".join(item["data"]["text"] for item in triage["reads"])
    model_evidence = record["curation_model_execution"]
    assert model_evidence["request"] == record["curation_model_request"]
    assert model_evidence["response"] == runner.response
    assert model_evidence["evidence_digest"] == record_digest(
        {"request": model_evidence["request"], "response": model_evidence["response"]}
    )
    assert canary not in json.dumps(model_evidence["request"])
    assert events.index("curation_capture_evidence") < events.index("curation_model_request")
    assert events.index("curation_model_request") < events.index("curation_model_execution")
    assessor = binding.eval_officer_executor_id
    assert assessor not in {*spec["allowed_executor_ids"], binding.manager_executor_id}
    assert record["assessment"]["assessor_id"] == assessor
    assert record["assessment"]["completion_state"] == "complete"
    outcomes = record["assessment"]["criterion_outcomes"]
    assert len(outcomes) == 1 and outcomes[0]["criterion_id"] == "criterion.proposal"
    assert outcomes[0]["outcome"] == "satisfied"
    assert len(record["evaluations"]) == 1 and record["evaluations"][0]["evaluator_id"] == assessor
    pending = list((roots.vault / "01_AI_Review/Pending").glob("*.md"))
    assert len(pending) == 1 and source_path.read_bytes() == source_bytes
    vault_after = {
        path.relative_to(roots.vault).as_posix(): digest(path.read_bytes())
        for path in roots.vault.rglob("*")
        if path.is_file()
    }
    assert all(vault_after.get(path) == value for path, value in vault_before.items())
    assert set(vault_after) - set(vault_before) == {pending[0].relative_to(roots.vault).as_posix()}
    assert not snapshot["decisions"]
    proposal = pending[0].read_bytes()
    assert record["run"]["result_reference"]["content_digest"] == digest(proposal)
    expected_checkpoint = record_digest(
        {
            "graph_run_id": graph_run_id,
            "source_digest": digest(source_bytes),
            "proposal_digest": digest(proposal),
            "triage_digest": triage["evidence_digest"],
            "model_digest": model_evidence["evidence_digest"],
        }
    )
    assert record["evaluations"][0]["checkpoint_digest"] == expected_checkpoint
    assert outcomes[0]["evidence_references"] == [record["run"]["result_reference"]]
    http_evidence = await asyncio.to_thread(officer.owner_http, exops, run["work_run_id"], proposal)
    assert source_path.read_bytes() == source_bytes
    assert runner.response is not None and canary in runner.response["final_text"]
    assert runner.response["runner_binding"] == runner_binding
    assert runner.response["exposed_tools"] == [
        "work_context_read",
        "knowledge_read",
        "proposal_create",
    ]
    calls = runner.response["tool_calls"]
    assert calls[0]["name"] == "work_context_read" and calls[-1]["name"] == "proposal_create"
    assert sum(call["name"] == "proposal_create" for call in calls) == 1
    assert {call["name"] for call in calls} == set(runner.response["exposed_tools"])
    context = calls[0]["result"]["data"]
    assert context["work_run_id"] == run["work_run_id"] and context["graph_run_id"] == graph_run_id
    assert context["source_reference"] == admitted_source
    assert canary not in json.dumps(
        {
            key: context[key]
            for key in (
                "work_run_id",
                "work_spec_digest",
                "graph_run_id",
                "executor_id",
                "generation",
                "fencing_token",
                "source_reference",
                "source_chunks",
            )
        }
    )
    assert set(runner.denials) == {"wrong_source_read", "wrong_source_proposal", "ungranted_tool"}
    return {
        "status": "PASS",
        "scope": "disposable native model Curation Team",
        "claims_excluded": [
            "production roots",
            "Obsidian",
            "persistent LangGraph store",
            "continuation",
            "recovery",
            "model-based assessment",
        ],
        "versions": {
            "python": sys.version.split()[0],
            **versions,
            "temporal_server": "1.31.3",
            "postgres": "16.14",
        },
        "implementation_digest": digest(Path(curation_work.__file__).read_bytes()),
        "native_binding_digest": binding.native_binding_digest,
        "binding_content_digest": binding.binding_reference["content_digest"],
        "services_adapter_digest": digest(Path("/opt/services/clients/temporal.py").read_bytes()),
        "team_id": binding.team_id,
        "manager_executor_id": binding.manager_executor_id,
        "teammates": [
            {"executor_id": item.executor_id, "role_id": item.role_id}
            for item in binding.specialists
        ],
        "graph_name": GRAPH_NAME,
        "graph_run_id": graph_run_id,
        "segment_digest": segment["segment_digest"],
        "segments_started": 1,
        "profile_invocations": 2,
        "work_run_id": run["work_run_id"],
        "work_spec_digest": spec["spec_digest"],
        "workflow_id": description.id,
        "workflow_type": description.workflow_type,
        "temporal_run_id": description.run_id,
        "namespace": description.namespace,
        "task_queue": description.task_queue,
        "exact_start_payload": decoded[0],
        "history_event_counts": event_counts,
        "history_activities": expected_activities,
        "receipt": receipt,
        "terminal_state": record["run"]["state"],
        "application_state": record["application_state"],
        "source_reference": source_reference,
        "source_unchanged": True,
        "existing_vault_files_unchanged": True,
        "pending_count": 1,
        "proposal_digest": digest(proposal),
        "canonical_apply": False,
        "independent_assessor_id": assessor,
        "assessment": record["assessment"],
        "evaluations": record["evaluations"],
        "owner_http": http_evidence,
        "triage_evidence_digest": triage["evidence_digest"],
        "model_evidence_digest": model_evidence["evidence_digest"],
        "independent_checkpoint_digest": expected_checkpoint,
        "model_execution": runner.response,
        "model_canary": canary,
        "model_canary_observed": True,
        "model_scope_denials": runner.denials,
        "foreign_actor_admission_denied": True,
        "disposable_roots_removed": False,
    }


def native_model_response(request: dict, *, control: Path, container: str) -> dict:
    """Use the unchanged Officer adapter, with parent-owned cleanup on interruption."""
    adapter = control / "ops/clients/hermes/native_officer.py"
    if (
        request["runner_binding"]["adapter_digest"]
        != "sha256:" + hashlib.sha256(adapter.read_bytes()).hexdigest()
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
        "/workspace/control/ops/tests/support/curation_work_smoke.py",
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
            deadline = time.monotonic() + 540
            requests = 0
            for line in iter_process_lines(process.stdout, deadline):
                output.append(line)
                if line.startswith(officer.MODEL_REQUEST):
                    requests += 1
                    if requests != 1:
                        raise RuntimeError(
                            "A bounded Team cannot request another model invocation"
                        )
                    request = json.loads(line[len(officer.MODEL_REQUEST) :])
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
            if requests != 1:
                raise RuntimeError("Team never requested the bound model normalizer")
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
                adapter = control / "ops/clients/hermes/native_officer.py"
                binding = {
                    "runtime_kind": "hermes_native",
                    "hermes_version": "0.22.5",
                    "hermes_revision": "bf0203a794a07aac3ff764972ab9087baa4e4f4e",
                    "provider": "openai-codex",
                    "model": "gpt-6.1-terra",
                    "adapter_digest": "sha256:" + hashlib.sha256(adapter.read_bytes()).hexdigest(),
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
        f"PASS: native Team Temporal/LangGraph, model normalizer and Owner export; evidence: {evidence_path}"
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
            / "knowledgeos-native-team"
            / (uuid4().hex + ".json")
        )
        host(evidence)
    else:
        if arguments.runner_binding is None or arguments.evidence is not None:
            parser.error("Container run requires only an explicit --runner-binding")
        os.umask(0o077)
        with tempfile.TemporaryDirectory(prefix="knowledgeos-native-team-") as directory:
            report = asyncio.run(native_roundtrip(Path(directory), arguments.runner_binding))
        report["disposable_roots_removed"] = not Path(directory).exists()
        assert report["disposable_roots_removed"]
        print(MARKER + json.dumps(report, ensure_ascii=False, separators=(",", ":")))


if __name__ == "__main__":
    main()

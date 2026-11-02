"""Disposable native criterion-linked continuation and budget-stop evidence."""

from __future__ import annotations

import argparse
import asyncio
import copy
import importlib.metadata
import json
import os
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import curation_work_smoke as team
import normalization_work_smoke as officer

from support.process_streams import iter_process_lines

MARKER = "KNOWLEDGEOS_CONTINUATION_EVIDENCE="
SOURCE = "00_Inbox/Captures/2026/09/Native Curation Continuation smoke.md"
INITIAL = "knowledgeos-curation-initial-v1"
SUCCESSOR = "knowledgeos-curation-successor-v1"
INITIAL_NODES = ("manager", "capture_evidence", "assess_initial")
SUCCESSOR_NODES = ("manager_successor", "normalization_proposal", "assess")


class NoProvider:
    """An exhausted budget must never reach a provider invocation."""

    def __init__(self, binding):
        self.binding = copy.deepcopy(binding)
        self.calls = 0

    async def __call__(self, request):
        self.calls += 1
        raise AssertionError("Exhausted continuation scheduled a model/effect")


def waiting_http(exops, run_id: str, assessment: dict, remaining: dict) -> dict:
    import http.client
    import threading

    from vaultops.adapters.core import AdmissionError, digest
    from vaultops.application.exops import OwnerActor
    from vaultops.interfaces.loopback_owner import create_loopback_owner

    server = create_loopback_owner(exops, actor_id="owner.officer_smoke", port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    def call(payload):
        connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=10)
        try:
            connection.request("POST", "/owner/work/result", json.dumps(payload), headers={
                "Content-Type": "application/json",
            })
            response = connection.getresponse()
            raw = response.read()
            return response.status, json.loads(raw), raw
        finally:
            connection.close()

    try:
        injected_status, _, _ = call({"work_run_id": run_id, "actor_id": "other"})
        assert injected_status == 403
        try:
            exops.read_work(run_id, actor=OwnerActor("other", "web"))
        except AdmissionError as error:
            assert error.code == "ACTION_DENIED"
        else:
            raise AssertionError("Foreign actor waiting-result read was accepted")
        status, result, raw = call({"work_run_id": run_id})
        assert status == 200 and result["state"] == "awaiting_human"
        assert result["continuation_state"] == "human_required"
        assert result["assessment"] == assessment and result["remaining_work"] == remaining
        assert result["text"] is None
        return {
            "route": "POST /owner/work/result", "status": status,
            "response_bytes_digest": digest(raw), "response": result,
            "body_actor_status": injected_status,
            "wrong_actor_denied": True,
        }
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=10)


async def run_case(root: Path, runner_binding: dict, sdk, *, case: str, policy: dict | None) -> dict:
    from clients.temporal import TemporalBinding
    from temporalio.api.enums.v1 import EventType
    from temporalio.worker import Replayer, Worker

    from support.control_factory import make_separate_portable_fixture_roots
    from vaultops.adapters.core import AdmissionError, canonical, digest
    from vaultops.application.curation_continuation import (
        CurationContinuationExecutor,
        curation_continuation_admission,
        curation_continuation_native_digest,
    )
    from vaultops.application.curation_continuation_temporal import continuation_worker_components
    from vaultops.application.exops import KnowledgeExOps, OwnerActor
    from vaultops.application.knowledge import KnowledgeApplication
    from vaultops.application.team_catalog import SpecialistBinding, TeamBinding, TeamCatalog
    from vaultops.application.temporal_contract import CONTINUATION_WORKFLOW_TYPE
    from vaultops.application.temporal_work import connect_owner_temporal, dispatch_admitted_work
    from vaultops.application.work import record_digest, segment_digest
    from vaultops.projection import generate_projection

    roots = make_separate_portable_fixture_roots(
        root,
        extra_inputs=("ops/config/team-catalog.json", "ops/config/work-methods.json"),
        review_queues=True,
    )
    source_path = roots.vault / SOURCE
    canary = "continuation-" + uuid4().hex
    source_path.write_text(
        (roots.vault / officer.BASE_CAPTURE).read_text().replace(
            "11111111-1111-4111-8111-111111111111", str(uuid4())
        ).replace("20260909-090000-mac-deadbeef", "Native Curation Continuation smoke")
        + f"\nSynthetic observation code: {canary}\n"
    )
    generated, code = generate_projection(roots)
    assert code == 0, generated
    application = KnowledgeApplication.from_roots(roots)
    source = application.reference(SOURCE)
    before = {
        path.relative_to(roots.vault).as_posix(): digest(path.read_bytes())
        for path in roots.vault.rglob("*") if path.is_file()
    }
    source_bytes = source_path.read_bytes()
    catalog = TeamCatalog.load(roots.control)
    candidate = TeamBinding(
        team_id="knowledge-curation",
        manager_executor_id="knowledgeos.curation_manager.native_continuation_smoke",
        manager_profile=catalog.profile("knowledgeos.curation_manager"),
        eval_officer_executor_id="knowledgeos.eval_officer.native_continuation_smoke",
        eval_officer_profile=catalog.profile("knowledgeos.eval_officer"),
        specialists=tuple(
            SpecialistBinding(role, role + ".native_continuation_smoke", catalog.profile(role))
            for role in ("knowledgeos.triage", "knowledgeos.normalizer")
        ),
        catalog_digest=catalog.digest,
        native_binding_digest=curation_continuation_native_digest(runner_binding),
        binding_reference={},
    )
    binding_bytes = canonical(candidate.content())
    binding = replace(candidate, binding_reference={
        "owner_operation_id": "knowledgeos",
        "artifact_id": "artifact.curation.continuation_binding",
        "source_revision": digest(binding_bytes).removeprefix("sha256:"),
        "content_digest": digest(binding_bytes),
        "size_bytes": len(binding_bytes),
        "media_type": "application/json",
        "classification": "internal",
        "availability": "available_by_owner_export",
        "export_capability_id": "knowledgeos.team_binding.export",
    })
    suffix = uuid4().hex
    spec, run = curation_continuation_admission(
        application, binding, source,
        work_id="work.continuation." + suffix,
        work_run_id="workrun.continuation." + suffix,
        admission_receipt_id="admission.continuation." + suffix,
        runner_binding=runner_binding,
        **({"continuation_policy": policy} if policy is not None else {}),
    )
    admitted_spec = copy.deepcopy(spec)
    admitted_source = copy.deepcopy(source)

    def exact_spec(selected):
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
        current_team_binding=lambda selected: binding if selected == binding.team_id else None,
        worker_identity="knowledgeos.worker.native_continuation_smoke",
        director_method_source_admitted=lambda selected, reference: (
            exact_spec(selected) and dict(reference) == admitted_source
        ),
    )
    expires_at = datetime.now(UTC) + timedelta(minutes=15)
    try:
        exops.submit_work(
            spec, run, actor=OwnerActor("other", "cli"), action_id="action.denied." + suffix,
            expires_at=expires_at, method_source_reference=source,
        )
    except AdmissionError as error:
        assert error.code == "ACTION_DENIED"
    else:
        raise AssertionError("Foreign actor continuation admission was accepted")
    admitted = exops.submit_work(
        spec, run, actor=OwnerActor("owner.officer_smoke", "cli"),
        action_id="action.continuation." + suffix, expires_at=expires_at,
        method_source_reference=source,
    )
    transport = TemporalBinding(
        owner_operation_id="knowledgeos", target_host="server:7233", namespace="knowledgeos",
        task_queue="knowledgeos.work.v1", workflow_type=CONTINUATION_WORKFLOW_TYPE,
        workflow_id_prefix="knowledgeos/", update_name="continuation.update.disabled",
        signal_name="continuation.signal.disabled", query_name="continuation.query.disabled", tls=False,
    )
    scoped = await connect_owner_temporal(exops, transport)
    runner = officer.ModelRendezvous(runner_binding, application) if policy is None else NoProvider(runner_binding)
    executor = CurationContinuationExecutor(exops, runner=runner)
    workflow, plugin = continuation_worker_components(exops, executor)
    async with Worker(
        sdk, task_queue=transport.task_queue, workflows=[workflow], plugins=[plugin],
        identity=exops.worker_identity,
    ):
        dispatch_receipt = await dispatch_admitted_work(exops, scoped, run["work_run_id"])
        handle = sdk.get_workflow_handle("knowledgeos/" + run["work_run_id"])
        result = await asyncio.wait_for(handle.result(), timeout=420)
        description = await handle.describe()
        history = await handle.fetch_history()
    payload = {
        "owner_operation_id": "knowledgeos", "work_run_id": run["work_run_id"],
        "action_id": admitted["action_id"], "content_digest": admitted["content_digest"],
        "work_spec_digest": spec["spec_digest"],
    }
    started = history.events[0].workflow_execution_started_event_attributes
    assert started.workflow_id == description.id == "knowledgeos/" + run["work_run_id"]
    assert description.workflow_type == started.workflow_type.name == CONTINUATION_WORKFLOW_TYPE
    assert description.namespace == transport.namespace and description.task_queue == transport.task_queue
    assert started.task_queue.name == transport.task_queue
    assert await sdk.data_converter.decode(started.input.payloads) == [payload]
    scheduled, completed, counts = [], [], {}
    for event in history.events:
        event_name = EventType.Name(event.event_type)
        counts[event_name] = counts.get(event_name, 0) + 1
        if event.HasField("activity_task_scheduled_event_attributes"):
            item = event.activity_task_scheduled_event_attributes
            assert item.retry_policy.maximum_attempts == 1
            if item.activity_type.name.endswith(".normalization_proposal"):
                assert item.heartbeat_timeout.seconds == 15
            scheduled.append((event.event_id, item.activity_type.name))
        if event.HasField("activity_task_completed_event_attributes"):
            completed.append(event.activity_task_completed_event_attributes.scheduled_event_id)
    expected = [INITIAL + "." + node for node in INITIAL_NODES]
    if policy is None:
        expected += [SUCCESSOR + "." + node for node in SUCCESSOR_NODES]
    assert [name for _, name in scheduled] == expected, scheduled
    assert completed == [event_id for event_id, _ in scheduled]
    assert counts.get("EVENT_TYPE_WORKFLOW_EXECUTION_COMPLETED") == 1
    assert not any(counts.get(name, 0) for name in (
        "EVENT_TYPE_ACTIVITY_TASK_FAILED", "EVENT_TYPE_ACTIVITY_TASK_TIMED_OUT",
        "EVENT_TYPE_WORKFLOW_EXECUTION_FAILED", "EVENT_TYPE_WORKFLOW_EXECUTION_CONTINUED_AS_NEW",
    ))
    snapshot = application.journal.read()
    record = snapshot["intents"]["exops.work." + run["work_run_id"]]
    assert record["spec"] == admitted_spec and record["method_source_reference"] == admitted_source
    assert record["receipt"] == result and record["dispatch_state"] == "accepted"
    initial = record["curation_initial_assessment"]
    assessment, remaining = initial["assessment"], initial["remaining_work"]
    assessor = binding.eval_officer_executor_id
    assert assessor not in {*spec["allowed_executor_ids"], binding.manager_executor_id}
    assert assessment["assessor_id"] == assessor
    assert assessment["completion_state"] != "complete"
    assert [(item["criterion_id"], item["outcome"]) for item in assessment["criterion_outcomes"]] == [
        ("criterion.proposal", "not_run")
    ]
    assert remaining["not_run_criterion_ids"] == ["criterion.proposal"]
    assert remaining["unmet_criterion_ids"] == []
    assert remaining["assessment_id"] == assessment["assessment_id"]
    assert all(item["evaluator_id"] == assessor for item in initial["evaluations"])
    assert record["run"]["repeated_gap_count"] == 1
    assert record["run"]["last_gap_signature"] == remaining["gap_signature"]
    segments = list(record["admitted_method_segments"].values())
    first = segments[0]["segment"]
    assert first["segment_index"] == 1 and first["trigger_kind"] == "initial"
    assert first["target_criterion_ids"] == ["criterion.proposal"]
    assert segment_digest(first) == first["segment_digest"]
    assert initial["segments"] == [first]
    assert initial["run"].get("active_graph_run_id") is None
    assert initial["run"]["segments_started"] == initial["run"]["profile_invocations"] == 1
    assert initial["run"]["graph_run_ids"] == [first["graph_run_id"]]
    assert initial["run"]["latest_assessment_id"] == assessment["assessment_id"]
    assert [item["segment"]["graph_run_id"] for item in segments] == record["run"]["graph_run_ids"]
    triage = record["curation_capture_evidence"]
    assert canary in "".join(item["data"]["text"] for item in triage["reads"])
    assert triage["evidence_digest"] == record_digest({"context": triage["context"], "reads": triage["reads"]})
    pending = list((roots.vault / "01_AI_Review/Pending").glob("*.md"))
    if policy is None:
        assert record["run"]["state"] == "completed" and record["application_state"] == "recorded"
        assert record["run"]["segments_started"] == 2 and record["run"]["profile_invocations"] == 2
        assert len(segments) == 2 and len(pending) == 1
        successor = segments[1]["segment"]
        assert successor["segment_index"] == 2
        assert successor["predecessor_graph_run_id"] == first["graph_run_id"]
        assert successor["trigger_kind"] == "remaining_work"
        assert successor["trigger_reference_id"] == remaining["remaining_work_id"]
        assert successor["trigger_digest"] == record_digest(remaining)
        assert successor["target_criterion_ids"] == remaining["not_run_criterion_ids"]
        assert successor["graph_run_id"] != first["graph_run_id"]
        assert segment_digest(successor) == successor["segment_digest"]
        assert len(segments[0]["assignments"]) == len(segments[1]["assignments"]) == 1
        assert record["assessment"]["assessor_id"] == assessor
        assert record["assessment"]["completion_state"] == "complete"
        assert record["assessment"]["criterion_outcomes"][0]["outcome"] == "satisfied"
        proposal = pending[0].read_bytes()
        http_evidence = await asyncio.to_thread(officer.owner_http, exops, run["work_run_id"], proposal)
        assert record["run"]["result_reference"]["content_digest"] == digest(proposal)
        assert runner.response is not None and canary in runner.response["final_text"]
        assert canary not in json.dumps(record["curation_model_request"])
        assert runner.response["runner_binding"] == runner_binding
        assert sum(item["name"] == "proposal_create" for item in runner.response["tool_calls"]) == 1
        assert set(runner.denials) == {"wrong_source_read", "wrong_source_proposal", "ungranted_tool"}
        model_evidence = {"response": runner.response, "scope_denials": runner.denials}
    else:
        assert record["run"]["state"] == "awaiting_human"
        assert record["run"]["segments_started"] == record["run"]["profile_invocations"] == 1
        assert len(segments) == 1 and not pending and runner.calls == 0
        assert "curation_model_request" not in record and "curation_model_execution" not in record
        assert not snapshot["receipts"].get("proposal.create")
        assert not any(item.get("work_binding") for item in snapshot["intents"].values())
        assert record["assessment"] == assessment and record["remaining_work"] == remaining
        http_evidence = await asyncio.to_thread(
            waiting_http, exops, run["work_run_id"], assessment, remaining
        )
        model_evidence = {"provider_invocations": 0, "proposal_effects": 0}
    assert source_path.read_bytes() == source_bytes and not snapshot["decisions"]
    after = {
        path.relative_to(roots.vault).as_posix(): digest(path.read_bytes())
        for path in roots.vault.rglob("*") if path.is_file()
    }
    assert all(after.get(path) == value for path, value in before.items())
    assert set(after) - set(before) == {path.relative_to(roots.vault).as_posix() for path in pending}
    replay_workflow, replay_plugin = continuation_worker_components(
        exops, CurationContinuationExecutor(exops, runner=NoProvider(runner_binding))
    )
    with ThreadPoolExecutor(max_workers=2) as replay_executor:
        replay = await Replayer(
            workflows=[replay_workflow], plugins=[replay_plugin], namespace=transport.namespace,
            data_converter=sdk.data_converter, workflow_task_executor=replay_executor,
        ).replay_workflow(history)
    assert replay.replay_failure is None and replay.history == history
    assert application.journal.read() == snapshot
    return {
        "case": case, "status": "PASS", "continuation_policy": spec["continuation_policy"],
        "work_run_id": run["work_run_id"], "work_spec_digest": spec["spec_digest"],
        "native_binding_digest": binding.native_binding_digest,
        "workflow_id": description.id, "workflow_type": description.workflow_type,
        "temporal_run_id": description.run_id, "exact_start_payload": payload,
        "history": json.loads(history.to_json()), "history_event_counts": counts,
        "history_activities": expected, "initial_assessment": initial,
        "admitted_segments": segments, "final_run": record["run"],
        "final_assessment": record["assessment"], "final_evaluations": record["evaluations"],
        "dispatch_receipt": dispatch_receipt, "receipt": result,
        "source_reference": source, "source_unchanged": True,
        "existing_vault_files_unchanged": True, "pending_count": len(pending),
        "owner_http": http_evidence, "model_execution": model_evidence,
        "foreign_actor_admission_denied": True,
        "fresh_sdk_replay": {"status": "PASS", "events": len(history.events), "owner_writes": 0},
    }


async def native_roundtrip(root: Path, runner_binding: dict) -> dict:
    from temporalio.api.workflowservice.v1 import RegisterNamespaceRequest
    from temporalio.client import Client
    from temporalio.service import RPCError, RPCStatusCode

    versions = {package: importlib.metadata.version(package) for package in (
        "temporalio", "temporalio-langgraph", "langgraph"
    )}
    assert versions == {"temporalio": "1.34.0", "temporalio-langgraph": "0.1.0", "langgraph": "1.2.0"}
    async with asyncio.timeout(180):
        while True:
            try:
                sdk = await Client.connect("server:7233", namespace="knowledgeos", tls=False)
                await sdk.workflow_service.register_namespace(RegisterNamespaceRequest(
                    namespace="knowledgeos", workflow_execution_retention_period={"seconds": 3600},
                ))
                break
            except RPCError as error:
                if error.status == RPCStatusCode.ALREADY_EXISTS:
                    break
                await asyncio.sleep(1)
            except (OSError, RuntimeError):
                await asyncio.sleep(1)
    defaults = {"max_segments": 2, "max_repeated_gap_assessments": 2, "max_profile_invocations": 2}
    cases = []
    for case, key in (
        ("max_segments_exhausted", "max_segments"),
        ("profile_invocations_exhausted", "max_profile_invocations"),
        ("repeated_gap_exhausted", "max_repeated_gap_assessments"),
    ):
        policy = {**defaults, key: 1}
        policy["max_repeated_gap_assessments"] = min(
            policy["max_repeated_gap_assessments"], policy["max_segments"]
        )
        cases.append(await run_case(root / case, runner_binding, sdk, case=case, policy=policy))
    cases.append(await run_case(root / "success", runner_binding, sdk, case="two_segment_success", policy=None))
    return {
        "status": "PASS", "scope": "disposable native criterion-linked Curation continuation",
        "versions": {"python": sys.version.split()[0], **versions, "temporal_server": "1.31.3", "postgres": "16.14"},
        "cases": cases, "provider_invocations": 1, "canonical_apply": False,
        "claims_excluded": ["production roots", "Obsidian", "model-based assessment", "recovery", "persistent LangGraph store"],
        "disposable_roots_removed": False,
    }


def model_container(
    compose: list[str], *, binding: dict, control: Path, container: str,
    script: str = "curation_continuation_smoke.py", extra_args: tuple[str, ...] = (),
    expected_requests: int = 1,
) -> str:
    command = compose + [
        "run", "--rm", "--no-deps", "-T", "--name", container, "smoke", "python",
        "/workspace/control/ops/tests/support/" + script, "run",
        "--runner-binding", json.dumps(binding), *extra_args,
    ]
    output, requests = [], 0
    with tempfile.TemporaryFile(mode="w+t") as errors:
        process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                   stderr=errors, text=True, bufsize=1)
        assert process.stdout is not None and process.stdin is not None
        try:
            deadline = time.monotonic() + 720
            for line in iter_process_lines(process.stdout, deadline):
                output.append(line)
                if line.startswith(officer.MODEL_REQUEST):
                    requests += 1
                    if requests > expected_requests:
                        raise RuntimeError("Smoke requested another provider invocation")
                    request = json.loads(line[len(officer.MODEL_REQUEST):])
                    if request["runner_binding"] != binding:
                        raise RuntimeError("Owner changed the selected native model binding")
                    response = team.native_model_response(request, control=control, container=container)
                    process.stdin.write(json.dumps(response) + "\n")
                    process.stdin.flush()
            process.wait(timeout=30)
            if process.returncode or requests != expected_requests:
                raise RuntimeError(f"Native smoke failed ({process.returncode}): {''.join(output)}")
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


def host(evidence_path: Path) -> None:
    """Reuse the maintained Team host lifecycle without changing its old entrypoint.

    The lifecycle resolves an immutable image, composes the same pinned stack,
    creates evidence exclusively, and always removes its project and runner.
    Only the child runner and evidence marker are selected for this invocation.
    """
    original_runner, original_marker = team.model_container, team.MARKER
    original_image = os.environ.get("KNOWLEDGEOS_TEAM_IMAGE")
    try:
        team.model_container, team.MARKER = model_container, MARKER
        team.host(evidence_path)
    finally:
        team.model_container, team.MARKER = original_runner, original_marker
        if original_image is not None:
            os.environ["KNOWLEDGEOS_TEAM_IMAGE"] = original_image


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
            Path(__file__).resolve().parents[5] / "Tmp" / "knowledgeos-curation-continuation" / (uuid4().hex + ".json")
        )
        if evidence.exists() or evidence.is_symlink():
            raise FileExistsError("Continuation evidence is create-only")
        host(evidence)
    else:
        if arguments.runner_binding is None or arguments.evidence is not None:
            parser.error("Container run requires only an explicit --runner-binding")
        os.umask(0o077)
        with tempfile.TemporaryDirectory(prefix="knowledgeos-curation-continuation-") as directory:
            report = asyncio.run(native_roundtrip(Path(directory), arguments.runner_binding))
        report["disposable_roots_removed"] = not Path(directory).exists()
        assert report["disposable_roots_removed"]
        print(MARKER + json.dumps(report, ensure_ascii=False, separators=(",", ":")))


if __name__ == "__main__":
    main()

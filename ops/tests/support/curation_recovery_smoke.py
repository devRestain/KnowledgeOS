"""Disposable native Team Worker loss, history replay and exact effect reconciliation."""

from __future__ import annotations

import argparse
import asyncio
import copy
import hashlib
import importlib.metadata
import json
import os
import signal
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

MARKER = "KNOWLEDGEOS_TEAM_RECOVERY_EVIDENCE="
SOURCE = "00_Inbox/Captures/2026/09/Native Curation recovery smoke.md"
ACTOR = "owner.officer_smoke"
WORKER = "knowledgeos.worker.native_team_smoke"


def save_new(path: Path, value: dict) -> None:
    """Publish complete create-only rendezvous files, never a partly read JSON."""
    staging = path.with_name("." + path.name + "." + uuid4().hex)
    try:
        with staging.open("x", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.link(staging, path)
    finally:
        staging.unlink(missing_ok=True)


def file_digest(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def stopped(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return True
    return False


def load_setup(path: Path, expected_digest: str) -> dict:
    if path.is_symlink() or file_digest(path) != expected_digest:
        raise RuntimeError("Frozen owner setup changed across Worker restart")
    return json.loads(path.read_text())


def owner_from_setup(setup: dict):
    """Reconstruct authority from the frozen fixture and its durable owner journal."""
    from vaultops.application.exops import KnowledgeExOps
    from vaultops.application.knowledge import KnowledgeApplication
    from vaultops.application.team_catalog import SpecialistBinding, TeamBinding
    from vaultops.paths import resolve_paths

    application = KnowledgeApplication.from_roots(resolve_paths(Path(setup["control_root"])))
    selected = copy.deepcopy(setup["binding"])
    selected["specialists"] = tuple(SpecialistBinding(**item) for item in selected["specialists"])
    binding = TeamBinding(**selected)

    def exact_spec(spec) -> bool:
        return dict(spec) == setup["spec"] and application.reference(SOURCE) == setup["source"]

    exops = KnowledgeExOps(
        application,
        authorize_actor=lambda actor, action: (
            actor.actor_id == ACTOR
            and actor.ingress in {"cli", "web"}
            and action in {"submit_work", "read_work", "recover_work"}
        ),
        director_admitted=exact_spec,
        teams={binding.team_id: binding},
        current_team_binding=lambda selected: binding if selected == binding.team_id else None,
        worker_identity=WORKER,
        director_method_source_admitted=lambda spec, source: (
            exact_spec(spec) and dict(source) == setup["source"]
        ),
    )
    return exops, binding


def recovery_quiesced(root: Path, target: dict) -> bool:
    """Trusted supervisor proof, never a grant supplied in an Update or owner request."""
    path = root / "native-quiescence.json"
    if not path.is_file() or path.is_symlink():
        return False
    proof = json.loads(path.read_text())
    return (
        proof["target"] == target
        and proof["producer_exit_code"] == -signal.SIGKILL
        and stopped(proof["producer_pid"])
        and proof["failure"]["event_type"] == "EVENT_TYPE_ACTIVITY_TASK_TIMED_OUT"
        and proof["failure"]["timeout_type"] == "TIMEOUT_TYPE_HEARTBEAT"
        and proof["failure"]["activity_id"] == target["activity_id"]
        and proof["failure"]["temporal_run_id"] == target["temporal_run_id"]
    )


def make_executor(exops, setup: dict, *, hold: bool):
    from temporalio import activity

    from vaultops.application.curation_work import CurationTeamExecutor

    root = Path(setup["fixture_root"])

    class OneModel(officer.ModelRendezvous):
        async def __call__(self, request):
            save_new(root / "model-attempt.json", {"pid": os.getpid(), "request": request})
            response = await super().__call__(request)
            save_new(root / "model-response.json", {"response": response, "denials": self.denials})
            return response

    class NoModel:
        binding = copy.deepcopy(setup["runner_binding"])

        async def __call__(self, _request):
            save_new(root / "forbidden-model-rerun.json", {"pid": os.getpid()})
            raise AssertionError("Recovery or replay invoked the model a second time")

    class FaultBoundaryExecutor(CurationTeamExecutor):
        async def manager(self, spec, run):
            save_new(root / "manager-attempt.json", {"pid": os.getpid()})
            return await super().manager(spec, run)

        async def capture_evidence(self, work_run_id):
            save_new(root / "triage-attempt.json", {"pid": os.getpid()})
            return await super().capture_evidence(work_run_id)

        async def normalization_proposal(self, work_run_id):
            # The only injected fault is outside the production executor, after
            # its complete owner evidence and proposal registration have committed.
            result = await super().normalization_proposal(work_run_id)
            if hold:
                info = activity.info()
                snapshot = exops.application.journal.read()
                record = snapshot["intents"]["exops.work." + work_run_id]
                effects = [
                    item
                    for item in snapshot["intents"].values()
                    if item.get("work_binding", {}).get("work_run_id") == work_run_id
                ]
                assert len(effects) == 1 and effects[0]["state"] == "completed"
                save_new(
                    root / "committed-boundary.json",
                    {
                        "producer_pid": os.getpid(),
                        "temporal_run_id": info.workflow_run_id,
                        "activity_id": info.activity_id,
                        "activity_type": info.activity_type,
                        "attempt": info.attempt,
                        "work_run_id": work_run_id,
                        "model_evidence_digest": record["curation_model_execution"][
                            "evidence_digest"
                        ],
                        "proposal_reference": record["canonical_proposal_reference"],
                        "effect": effects[0],
                        "effect_receipt": snapshot["receipts"][effects[0]["effect_id"]],
                        "run": record["run"],
                    },
                )
                while True:
                    activity.heartbeat(
                        {"work_run_id": work_run_id, "activity_id": info.activity_id}
                    )
                    await asyncio.sleep(1)
            return result

        def _assess(self, record):
            save_new(
                root / "assessment-attempt.json",
                {
                    "pid": os.getpid(),
                    "work_run_id": record["run"]["work_run_id"],
                    "evaluator_id": record["spec"]["eval_officer_executor_id"],
                },
            )
            return super()._assess(record)

    runner = OneModel(setup["runner_binding"], exops.application) if hold else NoModel()
    return FaultBoundaryExecutor(
        exops, runner=runner, recovery_quiesced=lambda target: recovery_quiesced(root, target)
    )


async def child_worker(setup_path: Path, setup_digest: str, role: str) -> None:
    from temporalio.client import Client
    from temporalio.worker import Worker

    from vaultops.application.curation_temporal import team_worker_components

    setup = load_setup(setup_path, setup_digest)
    exops, _binding = owner_from_setup(setup)
    workflow, plugin = team_worker_components(
        exops, make_executor(exops, setup, hold=role == "producer")
    )
    sdk = await Client.connect("server:7233", namespace="knowledgeos", tls=False)
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    loop.add_signal_handler(signal.SIGTERM, stop.set)
    async with Worker(
        sdk,
        task_queue="knowledgeos.work.v1",
        workflows=[workflow],
        plugins=[plugin],
        identity=WORKER + "." + role + "." + str(os.getpid()),
    ):
        save_new(
            Path(setup["fixture_root"]) / (role + "-ready.json"),
            {
                "pid": os.getpid(),
                "setup_digest": setup_digest,
            },
        )
        await stop.wait()
    save_new(Path(setup["fixture_root"]) / (role + "-stopped.json"), {"pid": os.getpid()})


async def wait_file(path: Path, process: subprocess.Popen, *, timeout: int = 420) -> dict:
    async with asyncio.timeout(timeout):
        while not path.exists():
            if process.poll() is not None:
                raise RuntimeError(f"Worker {process.pid} exited early ({process.returncode})")
            await asyncio.sleep(0.2)
    return json.loads(path.read_text())


async def stop_child(process: subprocess.Popen) -> dict:
    if process.poll() is None:
        process.terminate()
        try:
            await asyncio.to_thread(process.wait, timeout=20)
        except subprocess.TimeoutExpired:
            process.kill()
            await asyncio.to_thread(process.wait, timeout=10)
    assert stopped(process.pid), "Stopped child PID is still present"
    return {"pid": process.pid, "exit_code": process.returncode, "confirmed_dead": True}


def start_child(setup_path: Path, setup_digest: str, role: str) -> subprocess.Popen:
    # Inherited stdio carries the single existing host/model rendezvous. Both
    # Workers share only the disposable filesystem, not Python owner objects.
    return subprocess.Popen(
        [
            sys.executable,
            "-u",
            str(Path(__file__).resolve()),
            "child-worker",
            "--setup",
            str(setup_path),
            "--setup-digest",
            setup_digest,
            "--role",
            role,
        ]
    )


async def connect_temporal():
    from temporalio.api.workflowservice.v1 import RegisterNamespaceRequest
    from temporalio.client import Client
    from temporalio.service import RPCError, RPCStatusCode

    async with asyncio.timeout(180):
        while True:
            try:
                sdk = await Client.connect("server:7233", namespace="knowledgeos", tls=False)
                await sdk.workflow_service.register_namespace(
                    RegisterNamespaceRequest(
                        namespace="knowledgeos",
                        workflow_execution_retention_period={"seconds": 3600},
                    )
                )
                return sdk
            except RPCError as error:
                if error.status == RPCStatusCode.ALREADY_EXISTS:
                    return sdk
                await asyncio.sleep(1)
            except (OSError, RuntimeError):
                await asyncio.sleep(1)


def history_details(history, *, temporal_run_id: str) -> dict:
    from temporalio.api.enums.v1 import EventType, TimeoutType

    counts, scheduled, completed, failed = {}, [], [], []
    by_id = {}
    for event in history.events:
        name = EventType.Name(event.event_type)
        counts[name] = counts.get(name, 0) + 1
        if event.HasField("activity_task_scheduled_event_attributes"):
            item = event.activity_task_scheduled_event_attributes
            assert item.retry_policy.maximum_attempts == 1
            row = {
                "event_id": event.event_id,
                "name": item.activity_type.name,
                "activity_id": item.activity_id,
                "heartbeat_timeout_seconds": item.heartbeat_timeout.seconds,
                "start_to_close_seconds": item.start_to_close_timeout.seconds,
            }
            scheduled.append(row)
            by_id[event.event_id] = row
        elif event.HasField("activity_task_completed_event_attributes"):
            completed.append(event.activity_task_completed_event_attributes.scheduled_event_id)
        elif event.HasField("activity_task_timed_out_event_attributes"):
            item = event.activity_task_timed_out_event_attributes
            row = by_id[item.scheduled_event_id]
            failed.append(
                {
                    "event_id": event.event_id,
                    "event_type": name,
                    "scheduled_event_id": item.scheduled_event_id,
                    "started_event_id": item.started_event_id,
                    "activity_id": row["activity_id"],
                    "activity_type": row["name"],
                    "timeout_type": TimeoutType.Name(
                        item.failure.timeout_failure_info.timeout_type
                    ),
                    "temporal_run_id": temporal_run_id,
                }
            )
    return {
        "event_counts": counts,
        "scheduled": scheduled,
        "completed_scheduled_event_ids": completed,
        "failures": failed,
    }


async def wait_native_timeout(handle, boundary: dict) -> dict:
    async with asyncio.timeout(90):
        while True:
            details = history_details(
                await handle.fetch_history(), temporal_run_id=boundary["temporal_run_id"]
            )
            if details["failures"]:
                assert len(details["failures"]) == 1, details
                failure = details["failures"][0]
                assert failure["activity_id"] == boundary["activity_id"]
                assert failure["activity_type"] == team.GRAPH_NAME + ".normalization_proposal"
                assert failure["timeout_type"] == "TIMEOUT_TYPE_HEARTBEAT"
                return failure
            await asyncio.sleep(0.5)


async def wait_recovery(handle, query_name: str, process: subprocess.Popen) -> dict:
    async with asyncio.timeout(90):
        while True:
            if process.poll() is not None:
                raise RuntimeError(f"Replacement Worker exited ({process.returncode})")
            status = await handle.query(query_name)
            if status["status"] == "waiting":
                return status
            assert status["status"] == "running", status
            await asyncio.sleep(0.5)


def denied_recovery(executor, run_id: str, actor, recovery_id: str, target: dict) -> dict:
    from vaultops.adapters.core import AdmissionError

    before = executor.exops.application.journal.read()
    try:
        executor.request_recovery(run_id, actor=actor, recovery_id=recovery_id, target=target)
    except AdmissionError as error:
        assert executor.exops.application.journal.read() == before
        return {"code": error.code, "owner_journal_unchanged": True}
    raise AssertionError("An untrusted, stale or incomplete recovery request was accepted")


async def native_roundtrip(root: Path, runner_binding: dict) -> dict:
    from clients.temporal import TemporalBinding
    from temporalio.api.enums.v1 import EventType
    from temporalio.client import WorkflowUpdateFailedError
    from temporalio.worker import Replayer

    from support.control_factory import make_separate_portable_fixture_roots
    from vaultops.adapters.core import AdmissionError, canonical, digest
    from vaultops.application import curation_temporal, curation_work, curation_workflow
    from vaultops.application.curation_temporal import team_worker_components
    from vaultops.application.curation_work import curation_admission, curation_native_digest
    from vaultops.application.exops import OwnerActor
    from vaultops.application.knowledge import KnowledgeApplication
    from vaultops.application.team_catalog import SpecialistBinding, TeamBinding, TeamCatalog
    from vaultops.application.temporal_contract import (
        CURATION_MARK_UNKNOWN_ACTIVITY,
        CURATION_RECONCILE_ACTIVITY,
        CURATION_RECOVERY_QUERY,
        CURATION_RECOVERY_UPDATE,
        TEAM_WORKFLOW_TYPE,
    )
    from vaultops.application.temporal_work import connect_owner_temporal, dispatch_admitted_work
    from vaultops.application.work import record_digest, segment_digest
    from vaultops.projection import generate_projection

    versions = {
        name: importlib.metadata.version(name)
        for name in ("temporalio", "temporalio-langgraph", "langgraph")
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
    source_path = roots.vault / SOURCE
    canary = "modelrecovery-" + uuid4().hex
    original = (roots.vault / officer.BASE_CAPTURE).read_text()
    source_path.write_text(
        original.replace("11111111-1111-4111-8111-111111111111", str(uuid4())).replace(
            "20260909-090000-mac-deadbeef", "Native Curation recovery smoke"
        )
        + f"\nSynthetic observation code: {canary}\n"
    )
    generated, code = generate_projection(roots)
    assert code == 0, generated
    application = KnowledgeApplication.from_roots(roots)
    source = application.reference(SOURCE)
    source_bytes = source_path.read_bytes()
    vault_before = {
        path.relative_to(roots.vault).as_posix(): digest(path.read_bytes())
        for path in roots.vault.rglob("*")
        if path.is_file()
    }
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
        source,
        work_id="work.curation." + suffix,
        work_run_id="workrun.curation." + suffix,
        admission_receipt_id="admission.curation." + suffix,
        runner_binding=runner_binding,
    )
    setup = {
        "fixture_root": str(root),
        "control_root": str(roots.control),
        "spec": spec,
        "run": run,
        "source": source,
        "runner_binding": runner_binding,
        "binding": {**binding.content(), "binding_reference": dict(binding.binding_reference)},
    }
    setup_path = root / "setup.json"
    save_new(setup_path, setup)
    setup_path.chmod(0o400)
    setup_digest = file_digest(setup_path)
    exops, actual_binding = owner_from_setup(load_setup(setup_path, setup_digest))
    assert actual_binding == binding
    actor = OwnerActor(ACTOR, "cli")
    expires_at = datetime.now(UTC) + timedelta(minutes=15)
    admitted = exops.submit_work(
        spec,
        run,
        actor=actor,
        action_id="action.curation." + suffix,
        expires_at=expires_at,
        method_source_reference=source,
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
    sdk = await connect_temporal()
    producer = start_child(setup_path, setup_digest, "producer")
    children = [producer]
    process_evidence = {}
    try:
        ready = await wait_file(root / "producer-ready.json", producer, timeout=60)
        assert ready == {"pid": producer.pid, "setup_digest": setup_digest}
        scoped = await connect_owner_temporal(exops, transport)
        dispatch_receipt = await dispatch_admitted_work(exops, scoped, run["work_run_id"])
        handle = sdk.get_workflow_handle("knowledgeos/" + run["work_run_id"])
        boundary = await wait_file(root / "committed-boundary.json", producer)
        assert boundary["producer_pid"] == producer.pid and boundary["attempt"] == 1
        assert boundary["activity_type"] == team.GRAPH_NAME + ".normalization_proposal"
        committed = exops.application.journal.read()["intents"]["exops.work." + run["work_run_id"]]
        assert committed["application_state"] == "started"
        assert (
            committed["curation_model_execution"]["evidence_digest"]
            == boundary["model_evidence_digest"]
        )
        assert committed["submitted_artifacts"] == [boundary["proposal_reference"]]
        assert "assessment" not in committed and not (root / "assessment-attempt.json").exists()
        partial_history = history_details(
            await handle.fetch_history(), temporal_run_id=boundary["temporal_run_id"]
        )
        assert [item["name"] for item in partial_history["scheduled"]] == [
            team.GRAPH_NAME + "." + node for node in team.NODES[:3]
        ]
        assert partial_history["completed_scheduled_event_ids"] == [
            item["event_id"] for item in partial_history["scheduled"][:2]
        ]
        assert partial_history["scheduled"][-1]["heartbeat_timeout_seconds"] == 15
        assert partial_history["scheduled"][-1]["start_to_close_seconds"] == 600
        target = {
            "owner_operation_id": "knowledgeos",
            "work_run_id": run["work_run_id"],
            "action_id": admitted["action_id"],
            "content_digest": admitted["content_digest"],
            "work_spec_digest": spec["spec_digest"],
            "temporal_run_id": boundary["temporal_run_id"],
            "activity_id": boundary["activity_id"],
            "run_revision": committed["run"]["revision"],
            "generation": committed["run"]["generation"],
            "fencing_token": committed["run"]["fencing_token"],
        }
        executor = make_executor(exops, setup, hold=False)
        denials = {
            "live_producer": denied_recovery(
                executor, run["work_run_id"], actor, "recovery.live." + suffix, target
            )
        }
        os.kill(producer.pid, signal.SIGKILL)
        exit_code = await asyncio.to_thread(producer.wait, timeout=10)
        assert exit_code == -signal.SIGKILL and stopped(producer.pid)
        process_evidence["producer"] = {
            "pid": producer.pid,
            "exit_code": exit_code,
            "confirmed_dead": True,
        }
        failure = await wait_native_timeout(handle, boundary)
        save_new(
            root / "native-quiescence.json",
            {
                "target": target,
                "producer_pid": producer.pid,
                "producer_exit_code": exit_code,
                "failure": failure,
            },
        )
        assert recovery_quiesced(root, target)
        replacement = start_child(setup_path, setup_digest, "replacement")
        children.append(replacement)
        replacement_ready = await wait_file(
            root / "replacement-ready.json", replacement, timeout=60
        )
        assert replacement_ready["pid"] == replacement.pid and replacement.pid != producer.pid
        assert replacement_ready["setup_digest"] == setup_digest
        waiting = await wait_recovery(handle, CURATION_RECOVERY_QUERY, replacement)
        assert waiting["failed_activity_id"] == boundary["activity_id"]
        assert waiting["temporal_run_id"] == boundary["temporal_run_id"]
        unknown = exops.application.journal.read()["intents"]["exops.work." + run["work_run_id"]]
        assert unknown["application_state"] == "unknown" and unknown["run"] == committed["run"]
        assert unknown["curation_failure"] == {
            "temporal_run_id": boundary["temporal_run_id"],
            "activity_id": boundary["activity_id"],
        }
        try:
            await executor.normalization_proposal(run["work_run_id"])
        except AdmissionError:
            pass
        else:
            raise AssertionError("Unknown Work allowed a normalizer/model rerun")
        assert not (root / "forbidden-model-rerun.json").exists()
        assert (
            exops.application.journal.read()["intents"]["exops.work." + run["work_run_id"]]
            == unknown
        )
        denials["foreign_actor"] = denied_recovery(
            executor,
            run["work_run_id"],
            OwnerActor("other", "cli"),
            "recovery.foreign." + suffix,
            target,
        )
        denials["foreign_activity"] = denied_recovery(
            executor,
            run["work_run_id"],
            actor,
            "recovery.activity." + suffix,
            {**target, "activity_id": "unrelated-activity"},
        )
        denials["stale_revision"] = denied_recovery(
            executor,
            run["work_run_id"],
            actor,
            "recovery.stale." + suffix,
            {**target, "run_revision": target["run_revision"] + 1},
        )
        denials["incomplete_target"] = denied_recovery(
            executor,
            run["work_run_id"],
            actor,
            "recovery.incomplete." + suffix,
            {key: value for key, value in target.items() if key != "fencing_token"},
        )
        try:
            await handle.execute_update(
                CURATION_RECOVERY_UPDATE,
                {
                    "recovery_id": "recovery.unadmitted." + suffix,
                    "target": {**target, "activity_id": "unrelated-activity"},
                },
            )
        except WorkflowUpdateFailedError:
            denials["foreign_native_update"] = {"rejected": True}
        else:
            raise AssertionError("Workflow accepted a foreign recovery Activity target")
        assert (await handle.query(CURATION_RECOVERY_QUERY))["status"] == "waiting"
        recovery_id = "recovery.curation." + suffix
        payload = executor.request_recovery(
            run["work_run_id"], actor=actor, recovery_id=recovery_id, target=target
        )
        assert payload == {"recovery_id": recovery_id, "target": target}
        recovery_key = "curation.recovery." + recovery_id
        received_snapshot = exops.application.journal.read()
        received = received_snapshot["intents"][recovery_key]
        assert received["state"] == "received" and received["target"] == target
        assert received["actor_id"] == ACTOR
        assert (
            executor.request_recovery(
                run["work_run_id"], actor=actor, recovery_id=recovery_id, target=target
            )
            == payload
        )
        assert exops.application.journal.read() == received_snapshot
        update_receipt = await handle.execute_update(
            CURATION_RECOVERY_UPDATE, payload, id=recovery_id
        )
        result = await asyncio.wait_for(handle.result(), timeout=90)
        assert update_receipt == result and result["application_state"] == "recorded"
        snapshot = exops.application.journal.read()
        assert snapshot["intents"][recovery_key]["state"] == "applied"
        assert snapshot["intents"][recovery_key]["applied_receipt"] == result
        assert (
            await executor.reconcile(
                run["work_run_id"],
                recovery_id,
                temporal_run_id=target["temporal_run_id"],
                activity_id=target["activity_id"],
            )
            == result
        )
        assert exops.application.journal.read() == snapshot
        history = await handle.fetch_history()
        description = await handle.describe()
        assert description.id == "knowledgeos/" + run["work_run_id"]
        assert description.run_id == target["temporal_run_id"]
        assert (
            description.namespace == "knowledgeos"
            and description.task_queue == transport.task_queue
        )
        assert description.workflow_type == TEAM_WORKFLOW_TYPE
        first = history.events[0]
        assert first.event_id == 1 and first.HasField("workflow_execution_started_event_attributes")
        started = first.workflow_execution_started_event_attributes
        assert started.workflow_id == description.id
        assert started.workflow_type.name == TEAM_WORKFLOW_TYPE
        assert started.task_queue.name == transport.task_queue
        decoded = await sdk.data_converter.decode(started.input.payloads)
        dispatch = {
            key: target[key]
            for key in (
                "owner_operation_id",
                "work_run_id",
                "action_id",
                "content_digest",
                "work_spec_digest",
            )
        }
        assert decoded == [dispatch]
        details = history_details(history, temporal_run_id=target["temporal_run_id"])
        scheduled = details["scheduled"]
        assert [item["name"] for item in scheduled] == [
            *(team.GRAPH_NAME + "." + node for node in team.NODES[:3]),
            CURATION_MARK_UNKNOWN_ACTIVITY,
            CURATION_RECONCILE_ACTIVITY,
        ]
        assert len(scheduled) == 5 and len(details["completed_scheduled_event_ids"]) == 4
        assert scheduled[2]["event_id"] not in details["completed_scheduled_event_ids"]
        assert details["failures"] == [failure]
        counts = details["event_counts"]
        assert counts.get("EVENT_TYPE_WORKFLOW_EXECUTION_STARTED") == 1
        assert counts.get("EVENT_TYPE_WORKFLOW_EXECUTION_COMPLETED") == 1
        assert counts.get("EVENT_TYPE_ACTIVITY_TASK_STARTED") == 5
        assert counts.get("EVENT_TYPE_WORKFLOW_EXECUTION_UPDATE_COMPLETED") == 1
        assert not any(
            counts.get(name, 0)
            for name in (
                "EVENT_TYPE_ACTIVITY_TASK_FAILED",
                "EVENT_TYPE_WORKFLOW_EXECUTION_FAILED",
                "EVENT_TYPE_WORKFLOW_EXECUTION_CONTINUED_AS_NEW",
            )
        )
        accepted_updates = [
            event
            for event in history.events
            if event.HasField("workflow_execution_update_accepted_event_attributes")
        ]
        completed_updates = [
            event
            for event in history.events
            if event.HasField("workflow_execution_update_completed_event_attributes")
        ]
        assert len(accepted_updates) == len(completed_updates) == 1
        accepted_update = accepted_updates[0].workflow_execution_update_accepted_event_attributes
        completed_update = completed_updates[0].workflow_execution_update_completed_event_attributes
        native_request = accepted_update.accepted_request
        assert native_request.meta.update_id == recovery_id
        assert native_request.input.name == CURATION_RECOVERY_UPDATE
        assert await sdk.data_converter.decode(native_request.input.args.payloads) == [payload]
        assert completed_update.meta.update_id == recovery_id
        assert completed_update.accepted_event_id == accepted_updates[0].event_id
        assert completed_update.outcome.HasField("success")
        assert await sdk.data_converter.decode(completed_update.outcome.success.payloads) == [
            result
        ]
        native_update = {
            "update_id": recovery_id,
            "accepted_event_id": accepted_updates[0].event_id,
            "completed_event_id": completed_updates[0].event_id,
            "exact_admitted_payload": payload,
            "exact_applied_receipt": result,
        }
        native_history = history.to_json()
        # A fresh native Replayer consumes the exact server history. Its plugin
        # graph is unmodified; Activities and model/effects must never execute.
        replay_workflow, replay_plugin = team_worker_components(
            exops, make_executor(exops, setup, hold=False)
        )
        with ThreadPoolExecutor(max_workers=2) as replay_executor:
            replay_result = await Replayer(
                workflows=[replay_workflow],
                plugins=[replay_plugin],
                namespace=transport.namespace,
                data_converter=sdk.data_converter,
                workflow_task_executor=replay_executor,
            ).replay_workflow(history)
        assert replay_result.replay_failure is None and replay_result.history == history
        assert exops.application.journal.read() == snapshot
        assert not (root / "forbidden-model-rerun.json").exists()
        process_evidence["replacement"] = await stop_child(replacement)
        assert process_evidence["replacement"]["exit_code"] == 0
        assert (root / "replacement-stopped.json").is_file()
        record = snapshot["intents"]["exops.work." + run["work_run_id"]]
        assert record["spec"] == spec and record["method_source_reference"] == source
        assert record["application_state"] == "recorded" and record["dispatch_state"] == "accepted"
        assert record["run"]["state"] == "completed"
        assert record["run"]["segments_started"] == 1 and record["run"]["profile_invocations"] == 2
        assert record["run"]["generation"] == target["generation"]
        assert record["run"]["fencing_token"] == target["fencing_token"]
        assert record["run"]["revision"] == target["run_revision"] + 1
        assert record["run"]["control_revision"] == committed["run"]["control_revision"] + 1
        assert record["run"]["work_spec_digest"] == spec["spec_digest"]
        assert len(record["run"]["graph_run_ids"]) == 1
        segments = record["admitted_method_segments"]
        assert list(segments) == record["run"]["graph_run_ids"]
        graph_run_id, segment_record = next(iter(segments.items()))
        segment = segment_record["segment"]
        assert segment_digest(segment) == segment["segment_digest"]
        assert segment_record["source_reference"] == source
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
        for event in (
            "exops_method_graph_admitted",
            "curation_capture_evidence",
            "curation_model_request",
            "curation_model_execution",
        ):
            assert events.count(event) == 1, (event, events)
        triage = record["curation_capture_evidence"]
        assert triage["evidence_digest"] == record_digest(
            {"context": triage["context"], "reads": triage["reads"]}
        )
        assert triage["context"]["executor_id"] == spec["allowed_executor_ids"][0]
        assert triage["context"]["source_reference"] == source
        assert triage["context"]["allowed_tools"] == ["work_context_read", "knowledge_read"]
        assert [item["arguments"]["chunk_locator"] for item in triage["reads"]] == triage[
            "context"
        ]["source_chunks"]
        assert canary in "".join(item["data"]["text"] for item in triage["reads"])
        model = record["curation_model_execution"]
        model_file = json.loads((root / "model-response.json").read_text())
        assert model["request"] == record["curation_model_request"]
        assert model["response"] == model_file["response"]
        assert model["evidence_digest"] == record_digest(
            {"request": model["request"], "response": model["response"]}
        )
        assert model["evidence_digest"] == boundary["model_evidence_digest"]
        assert canary not in json.dumps(model["request"])
        assert canary in model["response"]["final_text"]
        assert model["response"]["runner_binding"] == runner_binding
        assert model["response"]["exposed_tools"] == [
            "work_context_read",
            "knowledge_read",
            "proposal_create",
        ]
        calls = model["response"]["tool_calls"]
        assert calls[0]["name"] == "work_context_read" and calls[-1]["name"] == "proposal_create"
        assert sum(call["name"] == "proposal_create" for call in calls) == 1
        assert calls[-1]["result"]["status"] == "created"
        assert calls[-1]["result"]["effect"] == "proposal_created"
        assert {call["name"] for call in calls} == set(model["response"]["exposed_tools"])
        assert set(model_file["denials"]) == {
            "wrong_source_read",
            "wrong_source_proposal",
            "ungranted_tool",
        }
        assessor = binding.eval_officer_executor_id
        assert assessor not in {*spec["allowed_executor_ids"], binding.manager_executor_id}
        assert record["assessment"]["assessor_id"] == assessor
        assert record["assessment"]["completion_state"] == "complete"
        assert (
            len(record["evaluations"]) == 1 and record["evaluations"][0]["evaluator_id"] == assessor
        )
        outcomes = record["assessment"]["criterion_outcomes"]
        assert len(outcomes) == 1 and outcomes[0]["criterion_id"] == "criterion.proposal"
        assert outcomes[0]["outcome"] == "satisfied"
        pending = list((roots.vault / "01_AI_Review/Pending").glob("*.md"))
        assert len(pending) == 1 and not snapshot["decisions"]
        proposal = pending[0].read_bytes()
        assert source_path.read_bytes() == source_bytes
        vault_after = {
            path.relative_to(roots.vault).as_posix(): digest(path.read_bytes())
            for path in roots.vault.rglob("*")
            if path.is_file()
        }
        assert all(vault_after.get(path) == value for path, value in vault_before.items())
        assert set(vault_after) - set(vault_before) == {
            pending[0].relative_to(roots.vault).as_posix()
        }
        effects = [
            item
            for item in snapshot["intents"].values()
            if item.get("work_binding", {}).get("work_run_id") == run["work_run_id"]
        ]
        assert len(effects) == 1 and effects[0]["state"] == "completed"
        effect = effects[0]
        assert effect == boundary["effect"]
        assert snapshot["receipts"][effect["effect_id"]] == boundary["effect_receipt"]
        assert effect["work_binding"] == {
            "work_run_id": run["work_run_id"],
            "work_spec_digest": spec["spec_digest"],
            "graph_run_id": graph_run_id,
            "executor_id": spec["allowed_executor_ids"][1],
            "source_reference": source,
        }
        receipt = snapshot["receipts"][effect["effect_id"]]["execution"]
        assert receipt["outcome"] == "succeeded" and receipt["result_digest"] == digest(proposal)
        assert all(
            receipt[key] == effect[key]
            for key in (
                "effect_id",
                "owner_operation_id",
                "content_digest",
                "generation",
                "fencing_token",
            )
        )
        assert record["run"]["result_reference"]["content_digest"] == digest(proposal)
        checkpoint = record_digest(
            {
                "graph_run_id": graph_run_id,
                "source_digest": digest(source_bytes),
                "proposal_digest": digest(proposal),
                "triage_digest": triage["evidence_digest"],
                "model_digest": model["evidence_digest"],
            }
        )
        assert record["evaluations"][0]["checkpoint_digest"] == checkpoint
        assert outcomes[0]["evidence_references"] == [record["run"]["result_reference"]]
        http_evidence = await asyncio.to_thread(
            officer.owner_http, exops, run["work_run_id"], proposal
        )
        assert source_path.read_bytes() == source_bytes
        # Real KnowledgeApplication recover is the maintained sensor consumer.
        # Reconstruct after the native Worker has exited; no callback, model or
        # final Graph node participates in source discovery.
        from vaultops.application.knowledge import KnowledgeApplication

        lifecycle_application = KnowledgeApplication.from_roots(roots)
        lifecycle_before = lifecycle_application.journal.read()
        lifecycle_report, lifecycle_code = lifecycle_application.recover()
        assert lifecycle_code == 0, lifecycle_report
        assert lifecycle_report["experience"] == {
            "observed": 2, "skipped_unavailable": 0, "deferred": 0,
            "produced": 2, "quarantined": 0,
        }
        lifecycle_after = lifecycle_application.journal.read()
        assert lifecycle_after["intents"] == lifecycle_before["intents"]
        assert lifecycle_after["receipts"] == lifecycle_before["receipts"]
        assert {event["category"] for event in lifecycle_after["experience_events"]} == {
            "timeout", "crash_recovery",
        }
        assert len(lifecycle_after["experience"]) == len(lifecycle_after["outbox"]) == 2
        assert all(candidate["evidence_references"] == []
                   for candidate in lifecycle_after["experience"])
        assert all(event["evidence_references"][0]["availability"] == "unavailable"
                   for event in lifecycle_after["experience_events"])
        reconstructed = KnowledgeApplication.from_roots(roots)
        repeated, repeated_code = reconstructed.recover()
        assert repeated_code == 0 and repeated["experience"] == {
            "observed": 0, "skipped_unavailable": 0, "deferred": 0,
            "produced": 0, "quarantined": 0,
        }
        assert reconstructed.journal.read() == lifecycle_after
        assert not (root / "forbidden-model-rerun.json").exists()
        attempts = {
            name: json.loads((root / (name + "-attempt.json")).read_text())
            for name in ("manager", "triage", "model", "assessment")
        }
        assert all(attempts[name]["pid"] == producer.pid for name in ("manager", "triage", "model"))
        assert attempts["assessment"]["pid"] == replacement.pid
        assert file_digest(setup_path) == setup_digest
        return {
            "status": "PASS",
            "scope": "disposable native Team committed-effect recovery",
            "claims_excluded": [
                "production roots",
                "Obsidian",
                "general recovery",
                "disk restore",
                "service restore",
                "continuation",
                "control",
                "model-based assessment",
            ],
            "versions": {
                "python": sys.version.split()[0],
                **versions,
                "temporal_server": "1.31.3",
                "postgres": "16.14",
            },
            "implementation_digests": {
                Path(module.__file__).name: file_digest(Path(module.__file__))
                for module in (curation_work, curation_temporal, curation_workflow)
            },
            "services_adapter_digest": file_digest(Path("/opt/services/clients/temporal.py")),
            "setup_digest": setup_digest,
            "reconstructed_owner_binding_exact": True,
            "native_binding_digest": binding.native_binding_digest,
            "binding_content_digest": binding.binding_reference["content_digest"],
            "team_id": binding.team_id,
            "manager_executor_id": binding.manager_executor_id,
            "graph_name": team.GRAPH_NAME,
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
            "exact_start_payload": dispatch,
            "history": details,
            "committed_boundary": boundary,
            "native_failure": failure,
            "native_history": json.loads(native_history),
            "native_history_digest": digest(native_history.encode()),
            "worker_processes": process_evidence,
            "recovery_wait": waiting,
            "recovery_target": target,
            "recovery_intent": received,
            "recovery_update_name": CURATION_RECOVERY_UPDATE,
            "recovery_update_receipt": update_receipt,
            "native_recovery_update": native_update,
            "recovery_applied": snapshot["intents"][recovery_key],
            "native_lifecycle": {
                "recovery_report": lifecycle_report,
                "reconstructed_report": repeated,
                "source_events": lifecycle_after["experience_events"],
                "candidates": lifecycle_after["experience"],
                "historical_evidence_unchanged": True,
                "reconstruction_deduplicated": True,
                "native_failure_scope": "Activity timeout; effect outcome is separate",
                "journal_evidence_export": "unavailable",
            },
            "owner_receipt": result,
            "dispatch_receipt": dispatch_receipt,
            "recovery_denials": denials,
            "normalizer_rerun_denied": True,
            "exact_applied_receipt_replay": True,
            "native_replayer": {
                "status": "PASS",
                "history_event_count": len(history.events),
                "owner_journal_unchanged": True,
                "activity_effects_executed": False,
            },
            "attempts": attempts,
            "terminal_state": record["run"]["state"],
            "application_state": record["application_state"],
            "source_reference": source,
            "source_unchanged": True,
            "existing_vault_files_unchanged": True,
            "pending_count": 1,
            "effect_count": 1,
            "proposal_digest": digest(proposal),
            "canonical_apply": False,
            "independent_assessor_id": assessor,
            "assessment": record["assessment"],
            "evaluations": record["evaluations"],
            "owner_http": http_evidence,
            "triage_evidence_digest": triage["evidence_digest"],
            "model_evidence_digest": model["evidence_digest"],
            "independent_checkpoint_digest": checkpoint,
            "model_execution": model["response"],
            "model_canary": canary,
            "model_canary_observed": True,
            "model_scope_denials": model_file["denials"],
            "disposable_roots_removed": False,
            "native_history_event_types": sorted(
                {EventType.Name(event.event_type) for event in history.events}
            ),
        }
    finally:
        for process in reversed(children):
            await stop_child(process)


def recovery_container(compose: list[str], *, binding: dict, control: Path, container: str) -> str:
    command = compose + [
        "run",
        "--rm",
        "--no-deps",
        "-T",
        "--name",
        container,
        "smoke",
        "python",
        "-u",
        "/workspace/control/ops/tests/support/curation_recovery_smoke.py",
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
            deadline, requests = time.monotonic() + 720, 0
            for line in iter_process_lines(process.stdout, deadline):
                output.append(line)
                if line.startswith(officer.MODEL_REQUEST):
                    requests += 1
                    if requests != 1:
                        raise RuntimeError("Recovery requested another model invocation")
                    request = json.loads(line[len(officer.MODEL_REQUEST) :])
                    if request["runner_binding"] != binding:
                        raise RuntimeError(
                            "Owner request differs from the frozen model binding"
                        )
                    response = team.native_model_response(
                        request, control=control, container=container
                    )
                    process.stdin.write(json.dumps(response) + "\n")
                    process.stdin.flush()
            process.wait(timeout=30)
            if process.returncode:
                raise RuntimeError(
                    f"Native recovery container failed ({process.returncode}): {''.join(output)}"
                )
            if requests != 1:
                raise RuntimeError("Recovery smoke did not execute exactly one configured model")
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
    control = Path(__file__).resolve().parents[3]
    evidence_path = evidence_path.expanduser().absolute()
    if evidence_path.exists() or evidence_path.is_symlink():
        raise FileExistsError("Recovery evidence is create-only")
    evidence_path.parent.mkdir(parents=True, exist_ok=True)
    project = "kos-team-recovery-" + uuid4().hex[:12]
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
    requested_image = os.environ["KNOWLEDGEOS_TEAM_IMAGE"]

    def interrupted(_number, _frame):
        raise KeyboardInterrupt("Native Team recovery smoke interrupted")

    previous_sigterm = signal.signal(signal.SIGTERM, interrupted)
    try:
        try:
            selected = subprocess.run(
                ["docker", "image", "inspect", requested_image],
                check=True,
                capture_output=True,
                text=True,
                timeout=30,
            )
            image_id = json.loads(selected.stdout)[0]["Id"]
            os.environ["KNOWLEDGEOS_TEAM_IMAGE"] = image_id
            binding = {
                "runtime_kind": "hermes_native",
                "hermes_version": "0.22.5",
                "hermes_revision": "bf0203a794a07aac3ff764972ab9087baa4e4f4e",
                "provider": "openai-codex",
                "model": "gpt-6.1-terra",
                "adapter_digest": file_digest(control / "ops/clients/hermes/native_officer.py"),
            }
            subprocess.run(
                compose + ["up", "-d", "--wait", "--wait-timeout", "120", "postgres", "server"],
                check=True,
                timeout=180,
            )
            stdout = recovery_container(
                compose, binding=binding, control=control, container=container
            )
            lines = [line[len(MARKER) :] for line in stdout.splitlines() if line.startswith(MARKER)]
            if len(lines) != 1:
                raise RuntimeError("Native recovery returned no unique evidence record")
            report = json.loads(lines[0])
            if report.get("status") != "PASS" or report.get("disposable_roots_removed") is not True:
                raise RuntimeError("Native recovery did not complete and remove its fixture roots")
            report["compose_project"] = project
            report["team_image"] = {"requested": requested_image, "id": image_id}
            inspected = subprocess.run(
                [
                    "docker",
                    "image",
                    "inspect",
                    image_id,
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
            cleanup = team.cleanup_project(compose, project, container)
            report["cleanup"] = cleanup
            report["cleanup_complete"] = cleanup["complete"]
            if not cleanup["complete"]:
                report["status"] = "FAIL"
            # A PASS record cannot exist until model roots, fixture roots,
            # both Worker processes and the complete service stack are gone.
            save_new(evidence_path, report)
    finally:
        os.environ["KNOWLEDGEOS_TEAM_IMAGE"] = requested_image
        signal.signal(signal.SIGTERM, previous_sigterm)
    if failure is not None:
        raise failure
    if report["status"] != "PASS":
        raise RuntimeError("Disposable recovery cleanup failed")
    print(
        f"PASS: native Worker loss/restart, exact history replay and owner recovery; evidence: {evidence_path}"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("host", "run", "child-worker"))
    parser.add_argument("--evidence", type=Path)
    parser.add_argument("--runner-binding", type=json.loads)
    parser.add_argument("--setup", type=Path)
    parser.add_argument("--setup-digest")
    parser.add_argument("--role", choices=("producer", "replacement"))
    arguments = parser.parse_args()
    if arguments.action == "host":
        if any(
            value is not None
            for value in (
                arguments.runner_binding,
                arguments.setup,
                arguments.setup_digest,
                arguments.role,
            )
        ):
            parser.error("The host selects its own model binding and child setup")
        evidence = arguments.evidence or (
            Path(__file__).resolve().parents[5]
            / "Tmp"
            / "knowledgeos-native-team-recovery"
            / (uuid4().hex + ".json")
        )
        host(evidence)
    elif arguments.action == "child-worker":
        if (
            arguments.setup is None
            or arguments.setup_digest is None
            or arguments.role is None
            or arguments.evidence is not None
            or arguments.runner_binding is not None
        ):
            parser.error("Child Worker requires only --setup, --setup-digest and --role")
        asyncio.run(child_worker(arguments.setup, arguments.setup_digest, arguments.role))
    else:
        if (
            arguments.runner_binding is None
            or arguments.evidence is not None
            or arguments.setup is not None
            or arguments.setup_digest is not None
            or arguments.role is not None
        ):
            parser.error("Container run requires only an explicit --runner-binding")
        os.umask(0o077)

        def interrupted(_number, _frame):
            raise KeyboardInterrupt("Native recovery container interrupted")

        previous_sigterm = signal.signal(signal.SIGTERM, interrupted)
        try:
            with tempfile.TemporaryDirectory(
                prefix="knowledgeos-native-team-recovery-"
            ) as directory:
                report = asyncio.run(native_roundtrip(Path(directory), arguments.runner_binding))
            report["disposable_roots_removed"] = not Path(directory).exists()
            assert report["disposable_roots_removed"]
            print(
                MARKER + json.dumps(report, ensure_ascii=False, separators=(",", ":")), flush=True
            )
        finally:
            signal.signal(signal.SIGTERM, previous_sigterm)


if __name__ == "__main__":
    main()

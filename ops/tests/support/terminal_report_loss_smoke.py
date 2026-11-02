"""Real disposable terminal response/report loss; no production repair or fake SDK.

Canonical host command (inherit the same explicit bindings as native Team smoke)::

    python3 Operations/KnowledgeOS/ops/tests/support/terminal_report_loss_smoke.py \
        host --evidence /absolute/create-only/terminal-report-loss.json

Required environment: KNOWLEDGEOS_TEAM_IMAGE, KNOWLEDGEOS_CONTROL_SOURCE,
KNOWLEDGEOS_CORE_SOURCE, KNOWLEDGEOS_UID/GID, KNOWLEDGEOS_TEMPORAL_SCHEMA_SOURCE,
KNOWLEDGEOS_TEMPORAL_DYNAMIC_CONFIG_SOURCE, KNOWLEDGEOS_TEMPORAL_ADAPTER_SOURCE,
KNOWLEDGEOS_HERMES_PYTHON, KNOWLEDGEOS_HERMES_SOURCE,
KNOWLEDGEOS_HERMES_PROFILE_HOME. No service ports are published.

Two independent synthetic WorkRuns each use one actual selected model and one
Pending proposal. Case A discards the returned native terminal receipt, stops the
Worker, then reconstructs a trusted-local Owner/client. Case B intercepts ONLY
KnowledgeExOps.worker_finished AFTER super() committed the actual independent
assessment and terminal receipt. It publishes a durable boundary then blocks
without returning to the unchanged official Activity. The controller verifies
that boundary and host-provider quiescence, SIGKILLs that Worker, and reconstructs
a fresh Worker. The actual assess Activity has its canonical 600-second
start-to-close timeout, zero heartbeat timeout, maximum_attempts=1. Nothing
changes the graph, binding, deadlines, SDK identities or server history.

A failed native transport is NOT an accepted native completion. The harness
records the actual terminal transport status/failure separately and requires the
completed domain Work, independent assessment and Pending human gate to survive
unchanged. It never asks for effect reconciliation or dispatches another Work.
Any consumer-visible failure is emitted with exact history/journal evidence;
application fixes and permanent regressions require parent reproduction first.
"""
from __future__ import annotations

import argparse
import asyncio
import copy
import importlib.metadata
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from contextlib import AsyncExitStack, contextmanager
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import curation_recovery_smoke as recovery
import curation_work_smoke as team
import normalization_work_smoke as officer
from research_work_smoke import preservation_snapshot

from support.process_streams import iter_process_lines

MARKER = "KNOWLEDGEOS_TERMINAL_LOSS_EVIDENCE="
SOURCE = recovery.SOURCE
ACTOR, WORKER = recovery.ACTOR, recovery.WORKER
save_new, file_digest = recovery.save_new, recovery.file_digest
CASES = ("client_response_loss", "activity_completion_report_loss")


def failure_details(error):
    """Retain chained operation/cleanup failures without suppressing either."""
    return {
        "type": type(error).__name__, "message": str(error),
        "traceback": "".join(traceback.format_exception(error)),
    }


@contextmanager
def record_failure(report, key):
    """Observe a newly raised failure; programming errors still propagate."""
    incoming = sys.exception()
    try:
        yield
    finally:
        error = sys.exception()
        if error is not None and error is not incoming:
            report["status"] = "FAIL"
            report[key] = failure_details(error)


def owner_from_setup(setup, *, fault=False):
    from vaultops.application.exops import KnowledgeExOps
    from vaultops.application.knowledge import KnowledgeApplication
    from vaultops.application.team_catalog import SpecialistBinding, TeamBinding
    from vaultops.paths import resolve_paths

    class TerminalCommitBoundary(KnowledgeExOps):
        def worker_finished(self, work_run_id, **arguments):
            from temporalio import activity

            receipt = super().worker_finished(work_run_id, **arguments)
            info = activity.info()
            snapshot = self.application.journal.read()
            record = snapshot["intents"]["exops.work." + work_run_id]
            assert record["application_state"] == "recorded"
            assert record["run"]["state"] == "completed"
            assert record["assessment"]["completion_state"] == "complete"
            assert receipt == record["receipt"]
            save_new(Path(setup["fixture_root"]) / "terminal-commit.json", {
                "producer_pid": os.getpid(), "attempt": info.attempt,
                "temporal_run_id": info.workflow_run_id,
                "activity_id": info.activity_id, "activity_type": info.activity_type,
                "work_run_id": work_run_id, "receipt": receipt,
                "worker_finished_arguments": arguments, "snapshot": snapshot,
            })
            if fault:
                # Deliberately after the real commit, before this call or the
                # official Activity returns. No cancellation/exception handler
                # runs: only the separate controller may SIGKILL this process.
                while True:
                    time.sleep(1)
            return receipt

    application = KnowledgeApplication.from_roots(resolve_paths(Path(setup["control_root"])))
    selected = copy.deepcopy(setup["binding"])
    selected["specialists"] = tuple(SpecialistBinding(**item) for item in selected["specialists"])
    binding = TeamBinding(**selected)

    def exact_spec(spec):
        return dict(spec) == setup["spec"] and application.reference(SOURCE) == setup["source"]

    # Ordinary fresh consumers do not carry the fault wrapper at all.
    owner_type = TerminalCommitBoundary if setup.get("worker_role") == "producer" else KnowledgeExOps
    exops = owner_type(
        application,
        authorize_actor=lambda actor, action: actor.actor_id == ACTOR
        and actor.ingress in {"cli", "web"} and action in {"submit_work", "read_work"},
        director_admitted=exact_spec, teams={binding.team_id: binding},
        current_team_binding=lambda team_id: binding if team_id == binding.team_id else None,
        worker_identity=WORKER,
        director_method_source_admitted=lambda spec, source: exact_spec(spec)
        and dict(source) == setup["source"],
    )
    return exops, binding


def make_executor(exops, setup, *, producer):
    from vaultops.application.curation_work import CurationTeamExecutor

    root = Path(setup["fixture_root"])

    class OneModel(officer.ModelRendezvous):
        async def __call__(self, request):
            save_new(root / "model-attempt.json", {"pid": os.getpid(), "request": request})
            envelope = await super().__call__(request)
            assert set(envelope) == {"response", "provider_quiescence"}
            quiescence = envelope["provider_quiescence"]
            assert quiescence["exit_code"] == 0
            assert quiescence["process_group_absent"] is True
            assert quiescence["temporary_root_removed"] is True
            save_new(root / "model-response.json", {
                "response": envelope["response"], "denials": self.denials,
                "provider_quiescence": quiescence,
            })
            return envelope["response"]

    class NoModel:
        binding = copy.deepcopy(setup["runner_binding"])

        async def __call__(self, _request):
            save_new(root / "forbidden-model-rerun.json", {"pid": os.getpid()})
            raise AssertionError("Terminal result read/replay attempted another model")

    class CountRealExecutor(CurationTeamExecutor):
        async def manager(self, spec, run):
            save_new(root / "manager-attempt.json", {"pid": os.getpid()})
            return await super().manager(spec, run)

        async def capture_evidence(self, work_run_id):
            save_new(root / "triage-attempt.json", {"pid": os.getpid()})
            return await super().capture_evidence(work_run_id)

        async def normalization_proposal(self, work_run_id):
            save_new(root / "proposal-attempt.json", {"pid": os.getpid()})
            return await super().normalization_proposal(work_run_id)

        async def assess(self, work_run_id):
            save_new(root / "assessment-attempt.json", {"pid": os.getpid()})
            return await super().assess(work_run_id)

    runner = OneModel(setup["runner_binding"], exops.application) if producer else NoModel()
    return CountRealExecutor(exops, runner=runner)


async def child_worker(setup_path, setup_digest, role):
    from temporalio.worker import Worker

    from vaultops.application.curation_temporal import team_worker_components

    setup = recovery.load_setup(setup_path, setup_digest)
    # Role is process-local fault configuration, never admitted/hashed metadata.
    exops, _ = owner_from_setup({**setup, "worker_role": role},
                              fault=setup["case"] == CASES[1])
    workflow, plugin = team_worker_components(exops, make_executor(exops, setup, producer=role == "producer"))
    sdk = await recovery.connect_temporal()
    stop = asyncio.Event()
    asyncio.get_running_loop().add_signal_handler(signal.SIGTERM, stop.set)
    async with Worker(sdk, task_queue="knowledgeos.work.v1", workflows=[workflow], plugins=[plugin],
                      identity=WORKER + "." + role + "." + str(os.getpid())):
        save_new(Path(setup["fixture_root"]) / (role + "-ready.json"),
                 {"pid": os.getpid(), "setup_digest": setup_digest})
        await stop.wait()
    save_new(Path(setup["fixture_root"]) / (role + "-stopped.json"), {"pid": os.getpid()})


def start_child(setup_path, setup_digest, role):
    return subprocess.Popen([sys.executable, "-u", str(Path(__file__).resolve()), "child-worker",
                             "--setup", str(setup_path), "--setup-digest", setup_digest, "--role", role])


async def child_client(setup_path, setup_digest, label):
    from vaultops.application.exops import OwnerActor
    from vaultops.application.work import record_digest

    setup = recovery.load_setup(setup_path, setup_digest)
    exops, _ = owner_from_setup(setup)
    before = exops.application.journal.read()
    record = before["intents"]["exops.work." + setup["run"]["work_run_id"]]
    path = exops.application.resolve_reference(record["canonical_proposal_reference"], proposal=True)
    proposal = (exops.application.roots.vault / path).read_bytes()
    authorized = exops.read_work(setup["run"]["work_run_id"], actor=OwnerActor(ACTOR, "web"))
    assert authorized["run"] == record["run"] and authorized["receipt"] == record["receipt"]
    # This invokes real trusted-local HTTP export and fresh result revalidation,
    # including injected body actor and unauthorized actor denials.
    http = await asyncio.to_thread(officer.owner_http, exops, setup["run"]["work_run_id"], proposal)
    assert exops.application.journal.read() == before
    save_new(Path(setup["fixture_root"]) / (label + "-client.json"), {
        "pid": os.getpid(), "setup_digest": setup_digest, "http": http,
        "authorized_read": authorized, "journal_digest": record_digest(before),
        "owner_journal_unchanged": True,
    })


async def read_fresh_client(setup_path, setup_digest, label):
    process = await asyncio.to_thread(
        subprocess.Popen,
        [sys.executable, "-u", str(Path(__file__).resolve()), "child-client",
         "--setup", str(setup_path), "--setup-digest", setup_digest, "--role", label],
    )
    try:
        code = await asyncio.to_thread(process.wait, timeout=60)
        assert code == 0, f"Fresh trusted-local Owner/client failed ({code})"
        return json.loads((setup_path.parent / (label + "-client.json")).read_text())
    finally:
        if process.poll() is None:
            process.kill()
            await asyncio.to_thread(process.wait, timeout=10)


def prepare_case(root, runner_binding, case):
    from support.control_factory import make_separate_portable_fixture_roots
    from vaultops.adapters.core import canonical, digest
    from vaultops.application.curation_work import curation_admission, curation_native_digest
    from vaultops.application.exops import OwnerActor
    from vaultops.application.knowledge import KnowledgeApplication
    from vaultops.application.team_catalog import SpecialistBinding, TeamBinding, TeamCatalog
    from vaultops.projection import generate_projection

    roots = make_separate_portable_fixture_roots(root, extra_inputs=(
        "ops/config/team-catalog.json", "ops/config/work-methods.json"), review_queues=True)
    source_path = roots.vault / SOURCE
    canary = "terminal-loss-" + uuid4().hex
    original = (roots.vault / officer.BASE_CAPTURE).read_text()
    source_path.write_text(
        original.replace("11111111-1111-4111-8111-111111111111", str(uuid4()))
        .replace("20260909-090000-mac-deadbeef", source_path.stem)
        + f"\nSynthetic observation code: {canary}\n"
    )
    generated, code = generate_projection(roots)
    assert code == 0, generated
    application = KnowledgeApplication.from_roots(roots)
    source = application.reference(SOURCE)
    vault_before = {path.relative_to(roots.vault).as_posix(): digest(path.read_bytes())
                    for path in roots.vault.rglob("*") if path.is_file()}
    catalog = TeamCatalog.load(roots.control)
    candidate = TeamBinding(
        team_id="knowledge-curation", manager_executor_id="knowledgeos.curation_manager.native_smoke",
        manager_profile=catalog.profile("knowledgeos.curation_manager"),
        eval_officer_executor_id="knowledgeos.eval_officer.native_team_smoke",
        eval_officer_profile=catalog.profile("knowledgeos.eval_officer"),
        specialists=tuple(SpecialistBinding(role, role + ".native_smoke", catalog.profile(role))
                          for role in ("knowledgeos.triage", "knowledgeos.normalizer")),
        catalog_digest=catalog.digest, native_binding_digest=curation_native_digest(runner_binding),
        binding_reference={})
    raw = canonical(candidate.content())
    binding = replace(candidate, binding_reference={
        "owner_operation_id": "knowledgeos", "artifact_id": "artifact.curation.native_binding",
        "source_revision": digest(raw)[7:], "content_digest": digest(raw), "size_bytes": len(raw),
        "media_type": "application/json", "classification": "internal",
        "availability": "available_by_owner_export", "export_capability_id": "knowledgeos.team_binding.export"})
    suffix = uuid4().hex
    spec, run = curation_admission(application, binding, source, work_id="work.curation." + suffix,
        work_run_id="workrun.curation." + suffix, admission_receipt_id="admission.curation." + suffix,
        runner_binding=runner_binding)
    setup = {"fixture_root": str(root), "control_root": str(roots.control), "case": case,
             "spec": spec, "run": run, "source": source, "runner_binding": runner_binding,
             "binding": {**binding.content(), "binding_reference": dict(binding.binding_reference)}}
    setup_path = root / "setup.json"
    save_new(setup_path, setup)
    setup_path.chmod(0o400)
    setup_digest = file_digest(setup_path)
    exops, _ = owner_from_setup(setup)
    admitted = exops.submit_work(spec, run, actor=OwnerActor(ACTOR, "cli"),
        action_id="action.curation." + suffix, expires_at=datetime.now(UTC) + timedelta(minutes=30),
        method_source_reference=source)
    assert admitted["dispatch_state"] == "pending"
    return roots, setup, setup_path, setup_digest, exops, vault_before, canary


async def terminal_native_state(handle, boundary, replacement, report):
    """Wait the actual selected deadline, not a synthetic/shortened substitute."""
    from temporalio.api.enums.v1 import WorkflowExecutionStatus

    async with asyncio.timeout(750):
        while True:
            if replacement.poll() is not None:
                raise RuntimeError("Replacement Worker exited before observing native outcome")
            history = await handle.fetch_history()
            details = recovery.history_details(history, temporal_run_id=boundary["temporal_run_id"])
            description = await handle.describe()
            status = WorkflowExecutionStatus.Name(description.status.value)
            report.update(native_history=json.loads(history.to_json()), native_details=details,
                          native_status=status)
            timeouts = [item for item in details["failures"] if item["activity_id"] == boundary["activity_id"]]
            if timeouts:
                assert len(timeouts) == 1 and timeouts[0]["timeout_type"] == "TIMEOUT_TYPE_START_TO_CLOSE"
                assert timeouts[0]["activity_type"] == team.GRAPH_NAME + ".assess"
                report["actual_terminal_activity_timeout"] = timeouts[0]
                if status != "WORKFLOW_EXECUTION_STATUS_RUNNING":
                    return history
            await asyncio.sleep(1)


def domain_evidence(exops, setup, boundary, vault_before, canary):
    from vaultops.adapters.core import digest
    from vaultops.application.work import record_digest, segment_digest

    snapshot = exops.application.journal.read()
    record = snapshot["intents"]["exops.work." + setup["run"]["work_run_id"]]
    original = boundary["snapshot"]
    assert snapshot == original, "Terminal transport/reconstruction wrote to completed Owner truth"
    assert record["spec"] == setup["spec"] and record["method_source_reference"] == setup["source"]
    assert record["run"]["state"] == "completed" and record["application_state"] == "recorded"
    assert record["run"]["segments_started"] == 1 and record["run"]["profile_invocations"] == 2
    assert record["run"]["repeated_gap_count"] == 0
    assert record["run"]["generation"] == setup["run"]["generation"]
    assert record["run"]["fencing_token"] == setup["run"]["fencing_token"]
    assert record["assessment"]["completion_state"] == "complete"
    assert record["assessment"]["assessor_id"] == setup["spec"]["eval_officer_executor_id"]
    assert record["assessment"]["assessor_id"] not in [setup["spec"]["manager_executor_id"],
                                                       *setup["spec"]["allowed_executor_ids"]]
    assert len(record["evaluations"]) == 1
    assert record["assessment"]["criterion_outcomes"][0]["outcome"] == "satisfied"
    assert len(record["admitted_method_segments"]) == 1
    for graph_id, selected in record["admitted_method_segments"].items():
        assert selected["segment"]["segment_digest"] == segment_digest(selected["segment"])
        assert selected["native_binding_digest"] == setup["binding"]["native_binding_digest"]
        assert selected["source_reference"] == setup["source"]
        assert graph_id in record["run"]["graph_run_ids"]
    execution = record["curation_model_execution"]
    assert execution["evidence_digest"] == record_digest({"request": execution["request"],
                                                          "response": execution["response"]})
    assert execution["request"] == record["curation_model_request"]
    assert canary not in json.dumps(execution["request"])
    assert canary in execution["response"]["final_text"]
    assert execution["response"]["runner_binding"] == setup["runner_binding"]
    calls = execution["response"]["tool_calls"]
    assert calls[0]["name"] == "work_context_read" and calls[-1]["name"] == "proposal_create"
    assert sum(item["name"] == "proposal_create" for item in calls) == 1
    assert calls[-1]["result"]["status"] == "created"
    effects = [item for item in snapshot["intents"].values()
               if item.get("work_binding", {}).get("work_run_id") == setup["run"]["work_run_id"]]
    assert len(effects) == 1 and effects[0]["state"] == "completed"
    effect = effects[0]
    human = snapshot["human_requests"][effect["human_request_id"]]
    assert human["lifecycle"] == "pending" and effect["human_request_id"] not in snapshot["decisions"]
    assert datetime.fromisoformat(human["expires_at"]) > exops.application.clock()
    assert not snapshot["decisions"]
    proposal = (exops.application.roots.vault / effect["artifact_path"]).read_bytes()
    assert snapshot["receipts"][effect["effect_id"]]["execution"]["result_digest"] == digest(proposal)
    assert record["run"]["result_reference"]["content_digest"] == digest(proposal)
    assert snapshot["receipts"]["exops.work." + record["action_id"]]["receipt"]["result_digest"] == record_digest(record["run"])
    after = {path.relative_to(exops.application.roots.vault).as_posix(): digest(path.read_bytes())
             for path in exops.application.roots.vault.rglob("*") if path.is_file()}
    assert all(after.get(path) == value for path, value in vault_before.items())
    assert set(after) - set(vault_before) == {effect["artifact_path"]}
    assert len(list((exops.application.roots.vault / "01_AI_Review/Pending").glob("*.md"))) == 1
    attempts = {name: json.loads((Path(setup["fixture_root"]) / (name + "-attempt.json")).read_text())
                for name in ("manager", "triage", "model", "proposal", "assessment")}
    assert all(item["pid"] == boundary["producer_pid"] for item in attempts.values())
    assert not (Path(setup["fixture_root"]) / "forbidden-model-rerun.json").exists()
    return {"terminal_record": record, "effect": effect,
            "effect_receipt": snapshot["receipts"][effect["effect_id"]],
            "terminal_execution_receipt": snapshot["receipts"]["exops.work." + record["action_id"]],
            "human_request": human, "decisions": snapshot["decisions"],
            "control_receipts": {
                "intents": {key: value for key, value in snapshot["intents"].items()
                            if key.startswith("exops.control.")},
                "receipts": {key: value for key, value in snapshot["receipts"].items()
                             if key.startswith("exops.control.")},
            },
            "journal_digest": record_digest(snapshot), "attempts": attempts,
            "proposal_digest": digest(proposal), "canonical_sources_unchanged": True,
            "single_pending_proposal": True, "owner_journal_unchanged_after_terminal_commit": True}


async def run_case(root, runner_binding, case, report):
    from clients.temporal import TemporalBinding
    from temporalio.api.enums.v1 import WorkflowExecutionStatus
    from temporalio.client import Client
    from temporalio.worker import Replayer

    from vaultops.application.curation_temporal import team_worker_components
    from vaultops.application.temporal_contract import TEAM_WORKFLOW_TYPE
    from vaultops.application.temporal_work import connect_owner_temporal, dispatch_admitted_work

    _roots, setup, setup_path, setup_digest, exops, before_vault, canary = prepare_case(root, runner_binding, case)
    report.update(case=case, setup=setup, setup_digest=setup_digest, source_hidden_canary=canary)
    transport = TemporalBinding(owner_operation_id="knowledgeos", target_host="server:7233",
        namespace="knowledgeos", task_queue="knowledgeos.work.v1", workflow_type=TEAM_WORKFLOW_TYPE,
        workflow_id_prefix="knowledgeos/", update_name="team.update.disabled",
        signal_name="team.signal.disabled", query_name="team.query.disabled", tls=False)
    sdk = await recovery.connect_temporal()
    producer = await asyncio.to_thread(start_child, setup_path, setup_digest, "producer")
    children = [producer]
    report["processes"] = {}
    handle = None
    try:
        ready = await recovery.wait_file(root / "producer-ready.json", producer, timeout=60)
        assert ready == {"pid": producer.pid, "setup_digest": setup_digest}
        scoped = await connect_owner_temporal(exops, transport)
        report["dispatch_receipt"] = await dispatch_admitted_work(exops, scoped, setup["run"]["work_run_id"])
        handle = sdk.get_workflow_handle("knowledgeos/" + setup["run"]["work_run_id"])
        boundary = await recovery.wait_file(root / "terminal-commit.json", producer)
        report["fault_boundary"] = boundary
        assert boundary["producer_pid"] == producer.pid and boundary["attempt"] == 1
        assert boundary["activity_type"] == team.GRAPH_NAME + ".assess"
        report["provider"] = json.loads((root / "model-response.json").read_text())
        assert report["provider"]["response"] == boundary["snapshot"]["intents"][
            "exops.work." + setup["run"]["work_run_id"]]["curation_model_execution"]["response"]
        partial = recovery.history_details(await handle.fetch_history(), temporal_run_id=boundary["temporal_run_id"])
        assert [row["name"] for row in partial["scheduled"]] == [team.GRAPH_NAME + "." + node for node in team.NODES]
        selected = partial["scheduled"][-1]
        assert selected["start_to_close_seconds"] == 600 and selected["heartbeat_timeout_seconds"] == 0
        report["selected_terminal_deadline"] = selected
        if case == CASES[0]:
            # The real native client received it; deliberately retain no response
            # object. A new process/Owner/client must discover the existing result.
            assert await asyncio.wait_for(handle.result(), timeout=90) == boundary["receipt"]
            report["native_result_deliberately_discarded"] = True
            report["processes"]["producer"] = await recovery.stop_child(producer)
            assert report["processes"]["producer"]["exit_code"] == 0
            fresh_sdk = await Client.connect("server:7233", namespace="knowledgeos", tls=False)
            recovered = await fresh_sdk.get_workflow_handle("knowledgeos/" + setup["run"]["work_run_id"],
                run_id=boundary["temporal_run_id"]).result()
            assert recovered == boundary["receipt"]
            report["fresh_native_client_exact_receipt"] = recovered
            history = await handle.fetch_history()
            report["native_status"] = WorkflowExecutionStatus.Name((await handle.describe()).status.value)
            assert report["native_status"] == "WORKFLOW_EXECUTION_STATUS_COMPLETED"
            report["native_details"] = recovery.history_details(history, temporal_run_id=boundary["temporal_run_id"])
            assert len(report["native_details"]["completed_scheduled_event_ids"]) == 4
            assert not report["native_details"]["failures"]
        else:
            assert selected["event_id"] not in partial["completed_scheduled_event_ids"]
            assert len(partial["completed_scheduled_event_ids"]) == 3
            assert exops.application.journal.read() == boundary["snapshot"]
            os.kill(producer.pid, signal.SIGKILL)
            code = await asyncio.to_thread(producer.wait, timeout=10)
            assert code == -signal.SIGKILL and recovery.stopped(producer.pid)
            report["processes"]["producer"] = {"pid": producer.pid, "exit_code": code, "confirmed_dead": True}
            # Export works even before native timeout: domain truth was committed.
            report["fresh_client_before_timeout"] = await read_fresh_client(setup_path, setup_digest, "before-timeout")
            replacement = await asyncio.to_thread(start_child, setup_path, setup_digest, "replacement")
            children.append(replacement)
            ready = await recovery.wait_file(root / "replacement-ready.json", replacement, timeout=60)
            assert ready["pid"] != producer.pid and ready["setup_digest"] == setup_digest
            history = await terminal_native_state(handle, boundary, replacement, report)
            assert report["native_status"] == "WORKFLOW_EXECUTION_STATUS_FAILED", "Report actual failed transport, never accepted native completion"
            report["native_completion_accepted"] = False
            report["processes"]["replacement"] = await recovery.stop_child(replacement)
            assert report["processes"]["replacement"]["exit_code"] == 0
        description = await handle.describe()
        assert description.id == "knowledgeos/" + setup["run"]["work_run_id"]
        assert description.run_id == boundary["temporal_run_id"]
        assert description.workflow_type == TEAM_WORKFLOW_TYPE
        assert description.namespace == "knowledgeos" and description.task_queue == transport.task_queue
        first = history.events[0]
        assert first.HasField("workflow_execution_started_event_attributes")
        started = first.workflow_execution_started_event_attributes
        expected_dispatch = {
            "owner_operation_id": "knowledgeos", "work_run_id": setup["run"]["work_run_id"],
            "action_id": report["dispatch_receipt"]["action_id"],
            "content_digest": report["dispatch_receipt"]["content_digest"],
            "work_spec_digest": setup["spec"]["spec_digest"],
        }
        assert await sdk.data_converter.decode(started.input.payloads) == [expected_dispatch]
        assert started.workflow_id == description.id and started.workflow_type.name == TEAM_WORKFLOW_TYPE
        assert started.task_queue.name == transport.task_queue
        report["native_identity"] = {
            "workflow_id": description.id, "temporal_run_id": description.run_id,
            "workflow_type": description.workflow_type, "namespace": description.namespace,
            "task_queue": description.task_queue, "exact_dispatch": expected_dispatch,
        }
        report["native_history"] = json.loads(history.to_json())
        report["fresh_owner_client"] = await read_fresh_client(setup_path, setup_digest, "after-native")
        report["domain"] = domain_evidence(exops, setup, boundary, before_vault, canary)
        snapshot = exops.application.journal.read()
        replay_workflow, replay_plugin = team_worker_components(exops, make_executor(exops, setup, producer=False))
        with ThreadPoolExecutor(max_workers=2) as executor:
            replay = await Replayer(workflows=[replay_workflow], plugins=[replay_plugin],
                namespace="knowledgeos", data_converter=sdk.data_converter,
                workflow_task_executor=executor).replay_workflow(history)
        assert replay.replay_failure is None and replay.history == history
        assert exops.application.journal.read() == snapshot
        assert domain_evidence(exops, setup, boundary, before_vault, canary) == report["domain"]
        assert not (root / "forbidden-model-rerun.json").exists()
        assert file_digest(setup_path) == setup_digest
        report["replay"] = {"fresh_sdk_replayer": True, "events": len(history.events), "owner_writes": 0}
        report["status"] = "PASS"
    finally:
        error = sys.exception()
        if error is not None:
            report.update(status="FAIL", error=type(error).__name__ + ": " + str(error),
                          operation_failure=failure_details(error))
        try:
            # ExitStack invokes every real process cleanup even if an earlier
            # cleanup or evidence inspection fails; chained exceptions propagate.
            async with AsyncExitStack() as cleanup:
                for index, process in enumerate(children):
                    cleanup.push_async_callback(
                        stop_worker, process, role=("producer", "replacement")[index],
                        faulted=case == CASES[1] and process is producer, report=report,
                    )
                if error is not None:
                    with record_failure(report, "failure_inspection_error"):
                        report["owner_snapshot_at_failure"] = exops.application.journal.read()
                        if handle is not None:
                            report["native_history"] = json.loads((await handle.fetch_history()).to_json())
                            report["native_status"] = WorkflowExecutionStatus.Name((await handle.describe()).status.value)
        finally:
            report["all_worker_processes_stopped"] = all(process.poll() is not None for process in children)


async def stop_worker(process, *, role, faulted, report):
    try:
        with record_failure(report, role + "_cleanup_failure"):
            if process.poll() is None:
                if faulted:
                    # The injected commit boundary blocks the event loop.
                    process.kill()
                    await asyncio.to_thread(process.wait, timeout=10)
                else:
                    await recovery.stop_child(process)
    finally:
        report["processes"].setdefault(role, {
            "pid": process.pid, "exit_code": process.returncode,
            "confirmed_dead": recovery.stopped(process.pid),
        })


def installed_identities():
    """Byte identity, explicitly not a claim of installed rather than mounted execution."""
    files = (
        "application/exops.py", "application/curation_work.py", "application/curation_temporal.py",
        "application/curation_workflow.py", "application/temporal_work.py", "application/temporal_contract.py",
        "application/work.py", "application/work_tools.py", "application/team_catalog.py",
        "application/work_methods.py", "application/knowledge.py", "adapters/core.py",
        "adapters/owner_journal.py", "interfaces/loopback_owner.py", "interfaces/mcp_server.py",
        "interfaces/exops_web.py",
    )
    identities = {}
    for relative in files:
        source = Path("/workspace/control/ops/src/vaultops") / relative
        installed = Path("/opt/knowledgeos/ops/src/vaultops") / relative
        assert source.read_bytes() == installed.read_bytes(), "Rebuild selected image; installed module differs: " + relative
        identities[relative] = {"source": file_digest(source), "installed": file_digest(installed)}
    return identities


async def run(runner_binding):
    report = {"status": "FAIL", "scope": "two disposable terminal-loss scenarios",
              "evidence_classes": {"source": "read-only mounted maintained source; fault-only ExOps subclass",
                  "runtime": "actual model, native Temporal/plugin/Workers, fresh trusted-local Owner HTTP",
                  "artifact": "exact setup, journal/receipts, native histories and installed byte identities",
                  "deployment": "not claimed"},
              "claims_excluded": ["arbitrary crash/retry recovery", "production", "GUI", "Gateway adoption",
                                  "canonical apply", "model self-assessment", "native accepted completion after report loss"],
              "cases": []}
    directory = None
    try:
        report["installed_modules"] = installed_identities()
        report["versions"] = {name: importlib.metadata.version(name)
                              for name in ("temporalio", "temporalio-langgraph", "langgraph")}
        assert report["versions"] == {"temporalio": "1.34.0", "temporalio-langgraph": "0.1.0", "langgraph": "1.2.0"}
        report["fault_harness_digest"] = file_digest(Path(__file__))
        report["services_adapter_digest"] = file_digest(Path("/opt/services/clients/temporal.py"))
        with tempfile.TemporaryDirectory(prefix="knowledgeos-terminal-loss-") as directory:
            for case in CASES:
                root = Path(directory) / case
                root.mkdir()
                case_report = {"status": "FAIL", "case": case}
                report["cases"].append(case_report)
                await run_case(root, runner_binding, case, case_report)
                # No retry, replacement Work or budget reset after uncertain effects.
                if case_report["status"] != "PASS":
                    break
        report["disposable_roots_removed"] = not Path(directory).exists()
        assert report["disposable_roots_removed"]
        if len(report["cases"]) == 2 and all(case["status"] == "PASS" for case in report["cases"]):
            report["status"] = "PASS"
    finally:
        error = sys.exception()
        if error is not None:
            report.update(status="FAIL", error=type(error).__name__ + ": " + str(error),
                          failure=failure_details(error))
        if directory is not None:
            report["disposable_roots_removed"] = not Path(directory).exists()
        print(MARKER + json.dumps(report, ensure_ascii=False, separators=(",", ":")), flush=True)


def model_response(request, *, adapter, container):
    """Return proof that the actual old provider process group exited BEFORE injection."""
    assert request["runner_binding"]["adapter_digest"] == file_digest(adapter)
    command = [os.environ["KNOWLEDGEOS_HERMES_PYTHON"], "-I", str(adapter),
               "--hermes-root", os.environ["KNOWLEDGEOS_HERMES_SOURCE"],
               "--profile-home", os.environ["KNOWLEDGEOS_HERMES_PROFILE_HOME"],
               "--docker-command", shutil.which("docker") or "docker", "--container-name", container]
    with tempfile.TemporaryDirectory(prefix="knowledgeos-terminal-model-") as directory:
        process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True, start_new_session=True, env={**os.environ, "TMPDIR": directory})
        try:
            stdout, stderr = process.communicate(json.dumps(request), timeout=300)
            if stderr:
                print(stderr, file=sys.stderr, end="")
            if process.returncode:
                raise RuntimeError("Actual native model failed; uncertain effects are not retried")
            lines = [line[len(officer.MODEL_RESPONSE):] for line in stdout.splitlines()
                     if line.startswith(officer.MODEL_RESPONSE)]
            assert len(lines) == 1, "Actual adapter returned no unique model response"
            response = json.loads(lines[0])
            try:
                os.killpg(process.pid, 0)
            except ProcessLookupError:
                group_absent = True
            else:
                raise RuntimeError("Old provider process group is not quiescent")
        finally:
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGINT)
                try:
                    process.communicate(timeout=15)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.communicate(timeout=15)
            for stream in (process.stdin, process.stdout, process.stderr):
                stream.close()
    assert not Path(directory).exists()
    return {"response": response, "provider_quiescence": {"pid": process.pid, "exit_code": process.returncode,
        "process_group_absent": group_absent, "temporary_root_removed": True,
        "boundary": "host adapter exited and child process group absent before response delivered to Worker"}}


def container_run(compose, binding, adapter, container):
    command = compose + ["run", "--rm", "--no-deps", "-T", "--name", container, "smoke", "python", "-u",
        "/workspace/control/ops/tests/support/terminal_report_loss_smoke.py", "run", "--runner-binding", json.dumps(binding)]
    record, calls = None, []
    with tempfile.TemporaryFile(mode="w+t") as errors:
        process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                   stderr=errors, text=True, bufsize=1)
        try:
            # Two real model rounds plus the UNMODIFIED ten-minute assess
            # deadline, process reconstruction, replay and teardown margin.
            deadline = time.monotonic() + 1800
            for line in iter_process_lines(process.stdout, deadline):
                if line.startswith(officer.MODEL_REQUEST):
                    request = json.loads(line[len(officer.MODEL_REQUEST):])
                    if request["runner_binding"] != binding or len(calls) >= 2:
                        raise RuntimeError("Unexpected model route or additional invocation; no retry")
                    if any(item["work_run_id"] == request["work_run_id"] for item in calls):
                        raise RuntimeError("Same logical Work requested another model")
                    envelope = model_response(request, adapter=adapter, container=container)
                    calls.append({"work_run_id": request["work_run_id"],
                                  "quiescence": envelope["provider_quiescence"]})
                    process.stdin.write(json.dumps(envelope) + "\n")
                    process.stdin.flush()
                elif line.startswith(MARKER):
                    if record is not None:
                        raise RuntimeError("Multiple terminal loss evidence records")
                    record = json.loads(line[len(MARKER):])
                else:
                    print(line, end="")
            process.wait(timeout=30)
            if record is None:
                raise RuntimeError("Disposable terminal loss container returned no evidence")
            record["container_exit_code"] = process.returncode
            if process.returncode:
                record["status"] = "FAIL"
            record["host_model_invocations"] = calls
            if record["status"] == "PASS":
                assert len(calls) == 2
            return record
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


def host(evidence_path):
    control = Path(__file__).resolve().parents[3]
    evidence_path = evidence_path.expanduser().absolute()
    if evidence_path.exists() or evidence_path.is_symlink():
        raise FileExistsError("Terminal loss evidence is create-only")
    evidence_path.parent.mkdir(parents=True, exist_ok=True)
    project = "kos-terminal-loss-" + uuid4().hex[:12]
    container = project + "-runner"
    compose = ["docker", "compose", "-p", project, "-f", str(control / "ops/compose.team-smoke.yaml")]
    report, failure = {"status": "FAIL", "compose_project": project}, None
    original_image = os.environ["KNOWLEDGEOS_TEAM_IMAGE"]
    preserved = preservation_snapshot(control)

    def interrupted(_number, _frame):
        raise KeyboardInterrupt("Terminal loss smoke interrupted")

    previous = signal.signal(signal.SIGTERM, interrupted)
    try:
        try:
            inspected = subprocess.run(["docker", "image", "inspect", original_image], check=True,
                                       capture_output=True, text=True, timeout=30)
            image = json.loads(inspected.stdout)[0]
            os.environ["KNOWLEDGEOS_TEAM_IMAGE"] = image["Id"]
            adapter = control / "ops/clients/hermes/native_officer.py"
            binding = {"runtime_kind": "hermes_native", "hermes_version": "0.22.5",
                "hermes_revision": "bf0203a794a07aac3ff764972ab9087baa4e4f4e",
                "provider": "openai-codex", "model": "gpt-6.1-terra", "adapter_digest": file_digest(adapter)}
            subprocess.run(compose + ["up", "-d", "--wait", "--wait-timeout", "120", "postgres", "server"],
                           check=True, timeout=180)
            report = container_run(compose, binding, adapter, container)
            report.update(compose_project=project, team_image={"requested": original_image, "id": image["Id"],
                          "repo_digests": image.get("RepoDigests", [])}, runner_binding=binding,
                          adapter_digest=file_digest(adapter))
            if report["status"] != "PASS" or not report.get("disposable_roots_removed"):
                failure = RuntimeError("Terminal-loss acceptance failed; inspect exact evidence before repair")
        finally:
            error = sys.exception()
            if error is not None:
                report.update(status="FAIL", error=type(error).__name__ + ": " + str(error),
                              operation_failure=failure_details(error))
            if failure is not None:
                report["acceptance_failure"] = failure_details(failure)
            report["cleanup_complete"] = False
            try:
                with record_failure(report, "cleanup_failure"):
                    report["cleanup"] = team.cleanup_project(compose, project, container)
                    report["cleanup_complete"] = report["cleanup"]["complete"]
                    if not report["cleanup_complete"]:
                        cleanup_failure = RuntimeError("Complete disposable teardown failed")
                        report["status"] = "FAIL"
                        report["cleanup_failure"] = failure_details(cleanup_failure)
                        failure = failure or cleanup_failure
            finally:
                try:
                    with record_failure(report, "preservation_failure"):
                        after = preservation_snapshot(control)
                        report["preservation"] = {"unchanged": preserved == after,
                            "before": preserved, "after": after,
                            "domain_files": sum(key.startswith("vault/") for key in preserved),
                            "profile_config_auth_files": sum(key.startswith("profiles/") for key in preserved)}
                        if preserved != after:
                            preservation_failure = RuntimeError("Live/profile preservation failed")
                            report["status"] = "FAIL"
                            report["preservation_failure"] = failure_details(preservation_failure)
                            failure = failure or preservation_failure
                finally:
                    save_new(evidence_path, report)
    finally:
        os.environ["KNOWLEDGEOS_TEAM_IMAGE"] = original_image
        signal.signal(signal.SIGTERM, previous)
    if failure is not None:
        raise failure
    print("PASS: disposable terminal loss evidence: " + str(evidence_path))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("host", "run", "child-worker", "child-client"))
    parser.add_argument("--evidence", type=Path)
    parser.add_argument("--runner-binding", type=json.loads)
    parser.add_argument("--setup", type=Path)
    parser.add_argument("--setup-digest")
    parser.add_argument("--role")
    args = parser.parse_args()
    if args.action == "host":
        if any(value is not None for value in (args.runner_binding, args.setup, args.setup_digest, args.role)):
            parser.error("Host selects its own runner binding and child setup")
        host(args.evidence or Path(__file__).resolve().parents[5] / "Tmp" / "knowledgeos-terminal-loss" / (uuid4().hex + ".json"))
    elif args.action == "run":
        if args.runner_binding is None or any(value is not None for value in (args.evidence, args.setup, args.setup_digest, args.role)):
            parser.error("Container run requires only --runner-binding")
        os.umask(0o077)
        asyncio.run(run(args.runner_binding))
    else:
        if args.setup is None or args.setup_digest is None or args.role is None or args.evidence is not None or args.runner_binding is not None:
            parser.error("Child requires only --setup, --setup-digest and --role")
        if args.action == "child-worker":
            if args.role not in {"producer", "replacement"}:
                parser.error("Invalid Worker role")
            asyncio.run(child_worker(args.setup, args.setup_digest, args.role))
        else:
            if args.role not in {"before-timeout", "after-native"}:
                parser.error("Invalid client role")
            asyncio.run(child_client(args.setup, args.setup_digest, args.role))


if __name__ == "__main__":
    main()

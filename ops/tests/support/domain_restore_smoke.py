"""Bounded EX-R02/coordinated EX-R10 proof; never a production backup utility.

The only supported checkpoint is a stopped native Curation Worker, no running
HTTP writer, completed initial Graph/evidence, and one received unapplied CANCEL.
The host stops Temporal before dumping both databases. It retires the original
server/database before admitting a restored writer. Whole synthetic owner/Vault/
Runtime/control/Core trees are checksummed including modes; only the fixture's
absolute root locator is rebound after exact restore verification.

Exclusions: in-flight Activities/effects, producer hard-loss, effect rollback,
unsaved commands, divergent histories, schema/version migration, recurring
backups, disk/host loss, off-host durability, production, GUI, and model execution.
Cancellation intentionally proves terminal recovery with zero provider effects.
"""
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
import stat
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

import curation_continuation_smoke as continuation
import curation_control_smoke as control
import curation_recovery_smoke as recovery
import curation_work_smoke as team
import normalization_work_smoke as officer

from support.process_streams import iter_process_lines

MARKER = "KNOWLEDGEOS_DOMAIN_RESTORE_EVIDENCE="
RPC = "KNOWLEDGEOS_DOMAIN_RESTORE_HOST="
SOURCE = "00_Inbox/Captures/2026/09/Native Domain Restore smoke.md"
EXCLUSIONS = [
    "in-flight Activity/effect restore", "producer hard-loss", "effect rollback",
    "unsaved commands", "divergent histories", "schema/version migration",
    "scheduled backup", "disk/host loss", "off-host durability", "production", "GUI",
    "model execution", "general EX-R10 host/disk-loss acceptance",
]


def sha(content: bytes) -> str:
    return "sha256:" + hashlib.sha256(content).hexdigest()


def inventory(root: Path) -> dict:
    """Reject links/devices; retain empty directories and permission identities."""
    rows = {}
    for path in [root, *sorted(root.rglob("*"))]:
        metadata = path.lstat()
        if not (stat.S_ISREG(metadata.st_mode) or stat.S_ISDIR(metadata.st_mode)):
            raise ValueError("Backup accepts only regular synthetic files/directories")
        row = {"mode": stat.S_IMODE(metadata.st_mode),
               "kind": "directory" if path.is_dir() else "file"}
        if path.is_file():
            row.update(size=metadata.st_size, digest=sha(path.read_bytes()))
        rows[path.relative_to(root).as_posix()] = row
    return rows


def save_json(path: Path, value) -> None:
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    path.chmod(0o600)


async def host_action(action: str) -> dict:
    print(RPC + json.dumps({"action": action}), flush=True)
    response = json.loads(await asyncio.to_thread(sys.stdin.readline))
    if response.get("status") != "PASS":
        raise RuntimeError("Host recovery operation failed: " + str(response))
    return response


async def connect_existing(address: str):
    from temporalio.client import Client
    from temporalio.service import RPCError

    async with asyncio.timeout(180):
        while True:
            try:
                return await Client.connect(address, namespace="knowledgeos", tls=False)
            except (RPCError, OSError, RuntimeError):
                await asyncio.sleep(1)


async def waiting(sdk, transport, exops, binding, work_run_id, *, expected: dict):
    """Require exact restored history/query readiness, not merely an open port."""
    from temporalio.service import RPCError, RPCStatusCode
    from temporalio.worker import Worker

    from vaultops.application.curation_continuation import CurationContinuationExecutor
    from vaultops.application.curation_control import CurationRunControl
    from vaultops.application.curation_control_temporal import control_worker_components
    from vaultops.application.temporal_contract import CONTROL_QUERY

    runner = continuation.NoProvider(binding)
    executor = CurationContinuationExecutor(exops, runner=runner)
    controller = CurationRunControl(exops, executor)
    workflow, plugin = control_worker_components(exops, executor, controller)
    transient_responses = []
    started = time.monotonic()
    async with Worker(sdk, task_queue=transport.task_queue, workflows=[workflow], plugins=[plugin],
                      identity=exops.worker_identity + ".reconstructed." + uuid4().hex):
        handle = sdk.get_workflow_handle(
            "knowledgeos/" + work_run_id, run_id=expected["target"]["temporal_run_id"])
        # A restored frontend can precede history membership and Worker polling.
        # Retry only read-only UNAVAILABLE or the exact observed Query no-poller
        # response after identity validation; all other failures fail closed.
        async with asyncio.timeout(180):
            while True:
                phase = "describe"
                try:
                    description = await handle.describe(rpc_timeout=timedelta(seconds=5))
                    assert description.id == "knowledgeos/" + work_run_id
                    assert description.run_id == expected["target"]["temporal_run_id"]
                    assert description.namespace == transport.namespace
                    assert description.workflow_type == transport.workflow_type
                    assert description.task_queue == transport.task_queue
                    phase = "query"
                    observed = await handle.query(CONTROL_QUERY, rpc_timeout=timedelta(seconds=5))
                except RPCError as error:
                    query_poller_starting = (
                        phase == "query"
                        and error.status == RPCStatusCode.FAILED_PRECONDITION
                        and str(error) == "no poller seen for task queue recently, worker may be down"
                    )
                    if error.status != RPCStatusCode.UNAVAILABLE and not query_poller_starting:
                        raise
                    transient_responses.append({
                        "phase": phase, "status": error.status.name, "message": str(error),
                    })
                    await asyncio.sleep(1)
                    continue
                assert observed == expected
                history = await handle.fetch_history()
                break
    assert runner.calls == 0
    return observed, history, {
        "deadline_seconds": 180, "elapsed_seconds": time.monotonic() - started,
        "read_only_transient_responses": transient_responses,
        "exact_native_run_id": description.run_id, "exact_query": True,
    }


async def native_roundtrip(root: Path, runner_binding: dict) -> dict:
    from clients.temporal import TemporalBinding
    from temporalio.worker import Replayer, Worker

    from support.control_factory import make_separate_portable_fixture_roots, write_fixture_config
    from vaultops.adapters.core import canonical, digest
    from vaultops.application.curation_continuation import (
        CurationContinuationExecutor,
        curation_continuation_admission,
        curation_continuation_native_digest,
    )
    from vaultops.application.curation_control import CurationRunControl
    from vaultops.application.curation_control_temporal import (
        connect_control_temporal,
        control_worker_components,
        deliver_control,
        reconcile_control,
    )
    from vaultops.application.exops import KnowledgeExOps, OwnerActor
    from vaultops.application.knowledge import KnowledgeApplication
    from vaultops.application.team_catalog import SpecialistBinding, TeamBinding, TeamCatalog
    from vaultops.application.temporal_contract import (
        CONTROL_APPLY_ACTIVITY,
        CONTROL_CHECKPOINT_ACTIVITY,
        CONTROL_QUERY,
        CONTROL_UPDATE,
        CONTROL_WORKFLOW_TYPE,
        CURATION_INITIAL_GRAPH_NAME,
    )
    from vaultops.application.temporal_work import connect_owner_temporal, dispatch_admitted_work
    from vaultops.paths import resolve_paths
    from vaultops.projection import generate_projection

    versions = {name: importlib.metadata.version(name)
                for name in ("temporalio", "temporalio-langgraph", "langgraph")}
    assert versions == {"temporalio": "1.34.0", "temporalio-langgraph": "0.1.0", "langgraph": "1.2.0"}
    original = root / "original"
    backup = root / "backup"
    restored = root / "restored"
    if any(path.exists() or path.is_symlink() for path in (original, backup, restored)):
        raise FileExistsError("Recovery roots are create-only")
    original.mkdir(mode=0o700)
    backup.mkdir(mode=0o700)
    roots = make_separate_portable_fixture_roots(
        original, extra_inputs=("ops/config/team-catalog.json", "ops/config/work-methods.json"),
        review_queues=True,
    )
    source_path = roots.vault / SOURCE
    canary = "restore-" + uuid4().hex
    source_path.write_text((roots.vault / officer.BASE_CAPTURE).read_text().replace(
        "11111111-1111-4111-8111-111111111111", str(uuid4()))
        .replace(Path(officer.BASE_CAPTURE).stem, Path(SOURCE).stem)
        + "\nSynthetic recovery canary: " + canary + "\n")
    generated, code = generate_projection(roots)
    assert code == 0, generated
    application = KnowledgeApplication.from_roots(roots)
    source = application.reference(SOURCE)
    catalog = TeamCatalog.load(roots.control)
    candidate = TeamBinding(
        team_id="knowledge-curation",
        manager_executor_id="knowledgeos.curation_manager.native_control_smoke",
        manager_profile=catalog.profile("knowledgeos.curation_manager"),
        eval_officer_executor_id="knowledgeos.eval_officer.native_control_smoke",
        eval_officer_profile=catalog.profile("knowledgeos.eval_officer"),
        specialists=tuple(SpecialistBinding(role, role + ".native_control_smoke", catalog.profile(role))
                          for role in ("knowledgeos.triage", "knowledgeos.normalizer")),
        catalog_digest=catalog.digest,
        native_binding_digest=curation_continuation_native_digest(runner_binding), binding_reference={},
    )
    binding_bytes = canonical(candidate.content())
    binding = replace(candidate, binding_reference={
        "owner_operation_id": "knowledgeos", "artifact_id": "artifact.curation.restore_binding",
        "source_revision": digest(binding_bytes).removeprefix("sha256:"),
        "content_digest": digest(binding_bytes), "size_bytes": len(binding_bytes),
        "media_type": "application/json", "classification": "internal",
        "availability": "available_by_owner_export", "export_capability_id": "knowledgeos.team_binding.export",
    })
    suffix = uuid4().hex
    spec, run = curation_continuation_admission(
        application, binding, source, work_id="work.restore." + suffix,
        work_run_id="workrun.restore." + suffix, admission_receipt_id="admission.restore." + suffix,
        runner_binding=runner_binding,
    )
    admitted_spec, admitted_source = copy.deepcopy(spec), copy.deepcopy(source)

    def owner(selected_application):
        def exact_spec(selected):
            return dict(selected) == admitted_spec and selected_application.reference(SOURCE) == admitted_source

        result = KnowledgeExOps(
            selected_application, authorize_actor=lambda actor, action: (
                actor.actor_id == control.ACTOR and actor.ingress in {"cli", "web"}
                and action in {"submit_work", "read_work", "control_work"}
            ), director_admitted=exact_spec, teams={binding.team_id: binding},
            current_team_binding=lambda selected: binding if selected == binding.team_id else None,
            worker_identity="knowledgeos.worker.native_control_smoke",
            director_method_source_admitted=lambda selected, reference: (
                exact_spec(selected) and dict(reference) == admitted_source
            ),
        )
        return result

    exops = owner(application)
    admitted = exops.submit_work(
        spec, run, actor=OwnerActor(control.ACTOR, "cli"), action_id="action.restore." + suffix,
        expires_at=datetime.now(UTC) + timedelta(minutes=30), method_source_reference=source,
        control_checkpoint=True,
    )
    transport = TemporalBinding(
        owner_operation_id="knowledgeos", target_host="server:7233", namespace="knowledgeos",
        task_queue="knowledgeos.work.v1", workflow_type=CONTROL_WORKFLOW_TYPE,
        workflow_id_prefix="knowledgeos/", update_name=CONTROL_UPDATE,
        signal_name="curation.control.signal.disabled", query_name=CONTROL_QUERY, tls=False,
    )
    sdk = await recovery.connect_temporal()
    scoped = await connect_owner_temporal(exops, transport)
    runner = continuation.NoProvider(runner_binding)
    executor = CurationContinuationExecutor(exops, runner=runner)
    controller = CurationRunControl(exops, executor)
    workflow, plugin = control_worker_components(exops, executor, controller)
    key = "exops.work." + run["work_run_id"]
    workflow_id = "knowledgeos/" + run["work_run_id"]
    async with Worker(sdk, task_queue=transport.task_queue, workflows=[workflow], plugins=[plugin],
                      identity=exops.worker_identity + ".original"):
        dispatch_receipt = await dispatch_admitted_work(exops, scoped, run["work_run_id"])
        handle = sdk.get_workflow_handle(workflow_id)
        async with asyncio.timeout(90):
            while True:
                state = await handle.query(CONTROL_QUERY)
                if state["status"] == "awaiting_human":
                    break
                await asyncio.sleep(0.1)
    checkpoint = copy.deepcopy(application.journal.read()["intents"][key])
    assert checkpoint["run"]["segments_started"] == checkpoint["run"]["profile_invocations"] == 1
    assert checkpoint["run"]["repeated_gap_count"] == 1
    assert not checkpoint["run"]["pending_action_ids"] and runner.calls == 0
    http = control.OwnerHTTP(exops, controller)
    request = {"work_run_id": run["work_run_id"], "command_id": "command.restore.cancel." + suffix,
               "target": state["target"], "expires_at": (datetime.now(UTC) + timedelta(minutes=25)).isoformat()}
    try:
        status, received, _ = await asyncio.to_thread(http.call, "control/cancel", request)
        assert status == 200 and received["application_state"] == "received"
    finally:
        await asyncio.to_thread(http.close)
    frozen = copy.deepcopy(application.journal.read())
    assert frozen["intents"][key]["run"]["pending_action_ids"] == [request["command_id"]]
    before_restart = inventory(original)
    restart = await host_action("restart")
    sdk = await connect_existing("server:7233")
    # Same canonical owner is reopened; no command is delivered during restart proof.
    application = KnowledgeApplication.from_roots(roots)
    exops = owner(application)
    restarted_wait, restart_history, restart_readiness = await waiting(
        sdk, transport, exops, runner_binding, run["work_run_id"], expected=state)
    assert application.journal.read() == frozen
    assert inventory(original) == before_restart
    checkpoint_history = recovery.history_details(
        restart_history, temporal_run_id=state["target"]["temporal_run_id"])
    assert [row["name"] for row in checkpoint_history["scheduled"]] == [
        *[CURATION_INITIAL_GRAPH_NAME + "." + name for name in continuation.INITIAL_NODES],
        CONTROL_CHECKPOINT_ACTIVITY,
    ]
    assert checkpoint_history["completed_scheduled_event_ids"] == [
        row["event_id"] for row in checkpoint_history["scheduled"]]
    assert not checkpoint_history["failures"]
    save_json(backup / "checkpoint.json", {
        "schema_version": 1, "work_spec": spec, "work_run_id": run["work_run_id"],
        "team_binding": binding.content(), "runner_binding": runner_binding,
        "waiting_query": state, "pending_command": request, "received_receipt": received,
        "journal_digest": digest(canonical(frozen)), "versions": versions,
        "native_checkpoint_history": checkpoint_history,
        "runtime_policy": "included byte-for-byte; not claimed portable outside these exact pins",
        "quiescence": {"worker_stopped": True, "owner_http_stopped": True,
                        "pending_kind": "cancel", "initial_graph_completed": True,
                        "provider_invocations": 0, "proposal_effects": 0},
    })
    shutil.copytree(original, backup / "trees", copy_function=shutil.copy2)
    saved_manifest = inventory(backup / "trees")
    assert saved_manifest == before_restart
    save_json(backup / "tree-manifest.json", saved_manifest)
    coordinated = await host_action("backup-restore")
    for name, row in coordinated["database_archives"].items():
        archive = backup / name
        assert archive.stat().st_size == row["size_bytes"] > 0
        assert sha(archive.read_bytes()) == row["digest"]
        assert stat.S_IMODE(archive.stat().st_mode) == 0o600
    assert coordinated["original_server_and_database_retired"] is True
    assert inventory(original) == before_restart
    assert inventory(backup / "trees") == saved_manifest
    # No original producer remains. Remove its roots, not merely its locator.
    shutil.rmtree(original)
    assert not original.exists()
    shutil.copytree(backup / "trees", restored, copy_function=shutil.copy2)
    assert inventory(restored) == saved_manifest
    rebased = {kind: restored / getattr(roots, kind).relative_to(original)
               for kind in ("control", "core", "vault", "state", "runtime")}
    write_fixture_config(rebased["control"], **{kind: value for kind, value in rebased.items() if kind != "control"})
    roots = resolve_paths(rebased["control"])
    # Locator rewrite is the only permitted tree delta before owner reconstruction.
    rebound_manifest = inventory(restored)
    locator = (roots.control / "ops/vaultops.toml").relative_to(restored).as_posix()
    assert {name: row for name, row in rebound_manifest.items() if name != locator} == {
        name: row for name, row in saved_manifest.items() if name != locator}
    application = KnowledgeApplication.from_roots(roots)
    exops = owner(application)
    assert application.journal.read() == frozen
    assert application.reference(SOURCE) == admitted_source
    sdk = await connect_existing("server:7233")
    restored_wait, restored_history, restored_readiness = await waiting(
        sdk, transport, exops, runner_binding, run["work_run_id"], expected=state)
    assert application.journal.read() == frozen
    saved_events = json.loads(restart_history.to_json())["events"]
    restored_events = json.loads(restored_history.to_json())["events"]
    assert restored_events[:len(saved_events)] == saved_events
    fresh_runner = continuation.NoProvider(runner_binding)
    fresh_executor = CurationContinuationExecutor(exops, runner=fresh_runner)
    fresh_controller = CurationRunControl(exops, fresh_executor)
    fresh_workflow, fresh_plugin = control_worker_components(exops, fresh_executor, fresh_controller)
    client = await connect_control_temporal(fresh_controller, transport)
    payload = {"command_id": request["command_id"], "kind": "cancel", "target": request["target"]}
    http = control.OwnerHTTP(exops, fresh_controller)
    try:
        status, pending_receipt, _ = await asyncio.to_thread(
            http.call, "control/command", {"work_run_id": run["work_run_id"], "command_id": request["command_id"]})
        assert status == 200 and pending_receipt == received
        async with Worker(sdk, task_queue=transport.task_queue, workflows=[fresh_workflow], plugins=[fresh_plugin],
                          identity=exops.worker_identity + ".restored"):
            receipt = await deliver_control(fresh_controller, client, payload)
            handle = sdk.get_workflow_handle(workflow_id, run_id=state["target"]["temporal_run_id"])
            result = await asyncio.wait_for(handle.result(), timeout=90)
            assert result == receipt and receipt["application_state"] == "applied"
        terminal = copy.deepcopy(application.journal.read())
        record = terminal["intents"][key]
        final_run = record["run"]
        assert final_run["state"] == "cancelled" and not final_run["pending_action_ids"]
        for field in ("segments_started", "profile_invocations", "repeated_gap_count", "graph_run_ids",
                      "last_gap_signature", "latest_assessment_id", "generation", "fencing_token"):
            assert final_run[field] == checkpoint["run"][field]
        assert final_run["revision"] == checkpoint["run"]["revision"] + 2
        assert final_run["control_revision"] == checkpoint["run"]["control_revision"] + 1
        for field in ("admitted_method_segments", "curation_initial_assessment", "curation_capture_evidence"):
            assert record[field] == checkpoint[field]
        assert "curation_model_request" not in record and fresh_runner.calls == runner.calls == 0
        assert not list((roots.vault / "01_AI_Review/Pending").glob("*.md"))
        assert not any(item.get("work_binding") for item in terminal["intents"].values())
        status, http_status, _ = await asyncio.to_thread(http.call, "read", {"work_run_id": run["work_run_id"]})
        assert status == 200 and http_status["run"]["state"] == "cancelled"
        status, http_receipt, raw = await asyncio.to_thread(
            http.call, "control/command", {"work_run_id": run["work_run_id"], "command_id": request["command_id"]})
        assert status == 200 and http_receipt == receipt
        assert reconcile_control(fresh_controller, payload) == receipt
        assert await deliver_control(fresh_controller, client, payload) == receipt
        assert application.journal.read() == terminal
        description = await handle.describe()
        history = await handle.fetch_history()
        assert description.run_id == state["target"]["temporal_run_id"]
        start = history.events[0].workflow_execution_started_event_attributes
        assert await sdk.data_converter.decode(start.input.payloads) == [{
            "owner_operation_id": "knowledgeos", "work_run_id": run["work_run_id"],
            "action_id": admitted["action_id"], "content_digest": admitted["content_digest"],
            "work_spec_digest": spec["spec_digest"],
        }]
        details = recovery.history_details(history, temporal_run_id=description.run_id)
        assert [row["name"] for row in details["scheduled"]] == [
            *[CURATION_INITIAL_GRAPH_NAME + "." + name for name in continuation.INITIAL_NODES],
            CONTROL_CHECKPOINT_ACTIVITY, CONTROL_APPLY_ACTIVITY,
        ]
        assert not details["failures"]
        assert details["completed_scheduled_event_ids"] == [row["event_id"] for row in details["scheduled"]]
        replay_runner = continuation.NoProvider(runner_binding)
        replay_executor = CurationContinuationExecutor(exops, runner=replay_runner)
        replay_controller = CurationRunControl(exops, replay_executor)
        replay_workflow, replay_plugin = control_worker_components(exops, replay_executor, replay_controller)
        replay_tree = inventory(restored)
        vault_prefix = roots.vault.relative_to(restored).as_posix() + "/"
        assert {name: row for name, row in replay_tree.items() if name.startswith(vault_prefix)} == {
            name: row for name, row in saved_manifest.items() if name.startswith(vault_prefix)}
        with ThreadPoolExecutor(max_workers=2) as pool:
            replay = await Replayer(
                workflows=[replay_workflow], plugins=[replay_plugin], namespace="knowledgeos",
                data_converter=sdk.data_converter, workflow_task_executor=pool,
            ).replay_workflow(history)
        assert replay.replay_failure is None and replay.history == history
        assert application.journal.read() == terminal and inventory(restored) == replay_tree
        assert replay_runner.calls == 0
        backup_manifest = inventory(backup)
        return {
            "status": "PASS", "scope": "native domain retained-persistence restart and coordinated quiescent restore",
            "versions": {"python": sys.version.split()[0], **versions, "temporal_server": "1.31.3", "postgres": "16.14"},
            "work_run_id": run["work_run_id"], "workflow_id": workflow_id, "temporal_run_id": description.run_id,
            "native_binding_digest": binding.native_binding_digest, "runner_binding": runner_binding,
            "service_database_restart": restart, "coordinated_restore": coordinated,
            "waiting_query": state, "waiting_after_restart": restarted_wait, "waiting_after_restore": restored_wait,
            "native_readiness": {"after_restart": restart_readiness, "after_restore": restored_readiness},
            "owner_before_digest": digest(canonical(frozen)), "owner_restored_exact": True,
            "backup_manifest": backup_manifest, "tree_manifest": saved_manifest,
            "rebound_locator": locator, "one_writer_fence": {"original_roots_destroyed": True,
                "original_server_and_database_retired": True, "restored_writer_created_after_retirement": True},
            "dispatch_receipt": dispatch_receipt, "received_command": received, "applied_command": receipt,
            "initial_assessment": checkpoint["curation_initial_assessment"], "final_run": final_run,
            "owner_http": {"terminal_state": "cancelled", "command_receipt": http_receipt,
                           "raw_receipt_digest": digest(raw)},
            "provider_invocations": 0, "proposal_effects": 0, "finished_profiles_rerun": 0,
            "synthetic_vault_unchanged": True, "live_roots_unmounted": True, "profiles_unmounted": True,
            "history": json.loads(history.to_json()), "history_details": details,
            "fresh_sdk_replay": {"status": "PASS", "events": len(history.events), "owner_writes": 0},
            "claims_excluded": EXCLUSIONS,
        }
    finally:
        await asyncio.to_thread(http.close)


def command(arguments, *, data=None, timeout=180):
    try:
        return subprocess.run(arguments, input=data, capture_output=True, check=True, timeout=timeout)
    except subprocess.CalledProcessError as error:
        raise RuntimeError(
            f"Recovery command failed ({error.returncode}): {' '.join(arguments)}\n"
            + error.stderr.decode(errors="replace")
        ) from error


def inspect_services(compose) -> dict:
    result = {}
    for service in ("postgres", "server"):
        identity = command(compose + ["ps", "-q", service]).stdout.decode().strip()
        row = json.loads(command(["docker", "inspect", identity]).stdout)[0]
        result[service] = {"id": row["Id"], "started_at": row["State"]["StartedAt"],
                           "image": row["Image"], "mounts": row["Mounts"]}
    return result


def perform_action(action, *, source, target, original_project, restored_project, container, fixture, identities):
    if action == "restart":
        before = inspect_services(source)
        command(source + ["stop", "--timeout", "15", "server"])
        command(source + ["restart", "postgres"])
        command(source + ["up", "-d", "--no-deps", "--wait", "--wait-timeout", "120", "postgres"])
        command(source + ["up", "-d", "--no-deps", "--force-recreate", "--wait", "--wait-timeout", "120", "server"])
        after = inspect_services(source)
        assert before["postgres"]["started_at"] != after["postgres"]["started_at"]
        assert before["server"]["id"] != after["server"]["id"]
        assert before["postgres"]["mounts"] == after["postgres"]["mounts"]
        return {"status": "PASS", "before": before, "after": after,
                "postgres_actual_restart": True, "server_actual_recreation": True, "retained_volume": True}
    if action != "backup-restore":
        raise ValueError("Unknown recovery operation")
    save_json(fixture / "backup" / "runtime-identities.json", {
        **identities, "services_at_quiescent_checkpoint": inspect_services(source),
        "selected_databases": ["temporal", "temporal_visibility"],
        "transport_identity": {"endpoint": "server:7233", "namespace": "knowledgeos",
                               "queue": "knowledgeos.work.v1"},
    })
    command(source + ["stop", "--timeout", "15", "server"])
    database_clients = int(command(source + [
        "exec", "-T", "postgres", "psql", "-U", "temporal", "-d", "postgres", "-At", "-c",
        "SELECT count(*) FROM pg_stat_activity WHERE backend_type = 'client backend' AND pid <> pg_backend_pid()",
    ]).stdout.decode().strip())
    assert database_clients == 0, "Database still has a producer connection after Service shutdown"
    save_json(fixture / "backup" / "database-quiescence.json", {
        "other_client_backends": database_clients, "server_stopped": True,
        "worker_stopped": True, "owner_http_stopped": True,
    })
    archives = {}
    for database in ("temporal", "temporal_visibility"):
        content = command(source + ["exec", "-T", "postgres", "pg_dump", "-U", "temporal", "-Fc", database]).stdout
        path = fixture / "backup" / (database + ".dump")
        with path.open("xb") as stream:
            stream.write(content)
        path.chmod(0o600)
        archives[path.name] = {"database": database, "digest": sha(content), "size_bytes": len(content), "mode": "0600"}
    # Retire both original persistence and Service BEFORE bringing restored history online.
    command(source + ["rm", "--stop", "--force", "server", "schema", "postgres"])
    original_volume = original_project + "_recovery-postgres"
    command(["docker", "volume", "rm", original_volume])
    remaining = command(["docker", "ps", "-aq", "--filter", "label=com.docker.compose.project=" + original_project,
                         "--filter", "label=com.docker.compose.service=server"]).stdout.strip()
    assert not remaining
    command(target + ["up", "-d", "--no-deps", "--wait", "--wait-timeout", "120", "postgres"])
    for name, row in archives.items():
        archive = fixture / "backup" / name
        assert sha(archive.read_bytes()) == row["digest"]
        command(target + ["exec", "-T", "postgres", "createdb", "-U", "temporal", row["database"]])
        command(target + ["exec", "-T", "postgres", "pg_restore", "-U", "temporal", "--exit-on-error",
                          "--dbname", row["database"]], data=archive.read_bytes())
    command(target + ["up", "-d", "--no-deps", "--wait", "--wait-timeout", "120", "server"])
    command(["docker", "network", "connect", restored_project + "_default", container])
    return {"status": "PASS", "database_archives": archives, "source_server_stopped_before_dump": True,
            "original_server_and_database_retired": True, "original_volume_removed": original_volume,
            "fresh_databases": True, "restored_services": inspect_services(target),
            "archive_location": "same-host disposable fixture; removed after proof"}


def installed_identity(image, control_root) -> dict:
    # Deliberately no source mounts/PYTHONPATH. Image identity is NOT inferred from runner imports.
    program = (
        "import hashlib,importlib.metadata,json,pathlib,vaultops; "
        "import vaultops.adapters.core as core; "
        "root=pathlib.Path(vaultops.__file__).parent; "
        "print(json.dumps({'root':str(root),'pins':core.PINS,"
        "'adoption_digest':'sha256:'+hashlib.sha256((root.parents[1]/'config/core-adoption.json').read_bytes()).hexdigest(),"
        "'modules':{p.relative_to(root).as_posix():"
        "'sha256:'+hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(root.rglob('*.py'))},"
        "'packages':{n:importlib.metadata.version(n) for n in "
        "('temporalio','temporalio-langgraph','langgraph')}}))"
    )
    installed = json.loads(command(["docker", "run", "--rm", "--network", "none", "--read-only", image,
                                    "python", "-I", "-c", program]).stdout)
    source = control_root / "ops/src/vaultops"
    expected = {path.relative_to(source).as_posix(): sha(path.read_bytes()) for path in sorted(source.rglob("*.py"))}
    assert installed["modules"] == expected, "Installed image differs from current owner implementation; rebuild pinned Team image"
    assert installed["packages"] == {"temporalio": "1.34.0", "temporalio-langgraph": "0.1.0", "langgraph": "1.2.0"}
    assert installed["adoption_digest"] == sha((control_root / "ops/config/core-adoption.json").read_bytes())
    installed.update(source_module_equality=True, source_mounted=False, network="none")
    return installed


def host(evidence_path: Path):
    control_root = Path(__file__).resolve().parents[3]
    evidence_path = evidence_path.expanduser().absolute()
    evidence_path.parent.mkdir(parents=True, exist_ok=True)
    report = {"status": "FAIL", "claims_excluded": EXCLUSIONS, "cleanup_complete": False,
              "service_start_attempted": False}
    projects = ["kos-domain-" + uuid4().hex[:12], "kos-restored-" + uuid4().hex[:12]]
    container = projects[0] + "-runner"
    compositions = [["docker", "compose", "-p", project,
                     "-f", str(control_root / "ops/compose.team-smoke.yaml"),
                     "-f", str(control_root / "ops/compose.domain-restore-smoke.yaml")] for project in projects]
    source, target = compositions
    failure = None

    def interrupted(_number, _frame):
        raise KeyboardInterrupt("Coordinated domain restore interrupted")

    previous = signal.signal(signal.SIGTERM, interrupted)
    original_environment = {name: os.environ.get(name) for name in ("KNOWLEDGEOS_TEAM_IMAGE", "KNOWLEDGEOS_RESTORE_FIXTURE")}
    try:
        with evidence_path.open("x", encoding="utf-8") as evidence:
            evidence_path.chmod(0o600)
            # Docker's selected VM exports the workspace, not macOS $TMPDIR.
            # Keep only synthetic fixtures here; TemporaryDirectory removes them.
            with tempfile.TemporaryDirectory(
                prefix="knowledgeos-domain-restore-", dir=control_root.parents[1] / "Tmp",
            ) as directory:
                fixture = Path(directory)
                fixture.chmod(0o700)
                os.environ["KNOWLEDGEOS_RESTORE_FIXTURE"] = str(fixture)
                try:
                    requested = os.environ["KNOWLEDGEOS_TEAM_IMAGE"]
                    images = json.loads(command(["docker", "image", "inspect", requested, officer.SERVER_IMAGE,
                                                 officer.POSTGRES_IMAGE, officer.ADMIN_IMAGE]).stdout)
                    image = images[0]["Id"]
                    os.environ["KNOWLEDGEOS_TEAM_IMAGE"] = image
                    report["installed_identity"] = installed_identity(image, control_root)
                    report["images"] = [{"id": row["Id"], "repo_digests": row.get("RepoDigests", [])} for row in images]
                    report["team_image"] = {"requested": requested, "id": image}
                    report["runtime_inputs"] = {
                        name: {"digest": sha(Path(os.environ[name]).read_bytes())}
                        for name in ("KNOWLEDGEOS_TEMPORAL_ADAPTER_SOURCE",
                                     "KNOWLEDGEOS_TEMPORAL_SCHEMA_SOURCE",
                                     "KNOWLEDGEOS_TEMPORAL_DYNAMIC_CONFIG_SOURCE")
                    }
                    report["service_start_attempted"] = True
                    command(source + ["up", "-d", "--wait", "--wait-timeout", "120", "postgres", "server"])
                    binding = {"runtime_kind": "hermes_native", "hermes_version": "0.22.5",
                               "hermes_revision": "bf0203a794a07aac3ff764972ab9087baa4e4f4e",
                               "provider": "openai-codex", "model": "gpt-6.1-terra",
                               "adapter_digest": sha((control_root / "ops/clients/hermes/native_officer.py").read_bytes())}
                    args = source + ["run", "--rm", "--no-deps", "-T", "--name", container, "smoke", "python",
                                     "/workspace/control/ops/tests/support/domain_restore_smoke.py", "run",
                                     "--runner-binding", json.dumps(binding)]
                    with tempfile.TemporaryFile(mode="w+t") as errors:
                        process = subprocess.Popen(args, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                                   stderr=errors, text=True, bufsize=1)
                        try:
                            lines, actions = [], []
                            assert process.stdout is not None and process.stdin is not None
                            deadline = time.monotonic() + 720
                            for line in iter_process_lines(process.stdout, deadline):
                                if line.startswith(RPC):
                                    action = json.loads(line[len(RPC):])["action"]
                                    assert action == ("restart", "backup-restore")[len(actions)]
                                    response = perform_action(action, source=source, target=target,
                                        original_project=projects[0], restored_project=projects[1],
                                        container=container, fixture=fixture,
                                        identities={name: report[name] for name in (
                                            "installed_identity", "images", "team_image", "runtime_inputs")})
                                    actions.append(action)
                                    process.stdin.write(json.dumps(response) + "\n")
                                    process.stdin.flush()
                                else:
                                    lines.append(line)
                            process.wait(timeout=30)
                            if process.returncode:
                                errors.seek(0)
                                raise RuntimeError("Domain restore runner failed: " + errors.read() + "".join(lines))
                            records = [json.loads(line[len(MARKER):]) for line in lines if line.startswith(MARKER)]
                            assert len(records) == 1 and records[0]["status"] == "PASS"
                            assert actions == ["restart", "backup-restore"]
                            report.update(records[0])
                        finally:
                            if process.poll() is None:
                                process.terminate()
                                try:
                                    process.wait(timeout=15)
                                except subprocess.TimeoutExpired:
                                    process.kill()
                                    process.wait(timeout=15)
                            for stream in (process.stdin, process.stdout):
                                if stream is not None:
                                    stream.close()
                except (OSError, ValueError, KeyError, AssertionError, RuntimeError, subprocess.SubprocessError, KeyboardInterrupt) as error:
                    failure = error
                    report.update(status="FAIL", error=str(error))
                finally:
                    cleanups = [
                        {"compose_project": project, **team.cleanup_project(compose, project, container)}
                        for compose, project in zip(compositions, projects)
                    ]
                    report["cleanup"] = cleanups
                    report["cleanup_complete"] = len(cleanups) == 2 and all(row["complete"] for row in cleanups)
                    report["compose_projects"] = projects
                    if not report["cleanup_complete"]:
                        report["status"] = "FAIL"
            report["disposable_roots_removed"] = not fixture.exists()
            assert report["disposable_roots_removed"]
            json.dump(report, evidence, ensure_ascii=False, indent=2)
            evidence.write("\n")
    finally:
        signal.signal(signal.SIGTERM, previous)
        for name, value in original_environment.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
    if failure is not None:
        raise failure
    if report["status"] != "PASS":
        raise RuntimeError("Coordinated domain restore cleanup failed")
    print("PASS: native domain Service/DB restart, coordinated restore and cancellation; evidence: " + str(evidence_path))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("host", "run"))
    parser.add_argument("--evidence", type=Path)
    parser.add_argument("--runner-binding", type=json.loads)
    args = parser.parse_args()
    if args.action == "host":
        if args.runner_binding is not None:
            parser.error("Host selects exact pinned runner binding")
        evidence = args.evidence or (Path(__file__).resolve().parents[5] / "Tmp" / "knowledgeos-domain-restore" / (uuid4().hex + ".json"))
        host(evidence)
    else:
        if args.evidence is not None or args.runner_binding is None:
            parser.error("Container requires only runner binding")
        os.umask(0o077)
        result = asyncio.run(native_roundtrip(Path("/recovery"), args.runner_binding))
        print(MARKER + json.dumps(result, ensure_ascii=False, separators=(",", ":")), flush=True)


if __name__ == "__main__":
    main()

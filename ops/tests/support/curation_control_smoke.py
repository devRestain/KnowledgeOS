"""Disposable native control and one pending-command history rollover proof."""

from __future__ import annotations

import argparse
import asyncio
import copy
import http.client
import importlib.metadata
import io
import json
import os
import sys
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import redirect_stdout
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import curation_continuation_smoke as continuation
import curation_recovery_smoke as recovery
import curation_work_smoke as team
import normalization_work_smoke as officer

MARKER = "KNOWLEDGEOS_TEAM_CONTROL_EVIDENCE="
SOURCE = "00_Inbox/Captures/2026/09/Native Curation Control smoke.md"
ACTOR = "owner.officer_smoke"


class OwnerHTTP:
    def __init__(self, exops, controller, *, actor_id=ACTOR):
        from vaultops.interfaces.loopback_owner import create_loopback_owner

        self.server = create_loopback_owner(
            exops, actor_id=actor_id, port=0, run_control=controller,
        )
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def call(self, route, body):
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=10)
        try:
            connection.request("POST", "/owner/work/" + route, json.dumps(body), headers={
                "Content-Type": "application/json",
            })
            response = connection.getresponse()
            raw = response.read()
            return response.status, json.loads(raw), raw
        finally:
            connection.close()

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=10)


async def run_case(root: Path, runner_binding: dict, sdk, *, case: str) -> dict:
    from clients.temporal import TemporalBinding
    from temporalio.client import WorkflowUpdateFailedError
    from temporalio.service import RPCError
    from temporalio.worker import Replayer, Worker

    from support.control_factory import make_separate_portable_fixture_roots
    from vaultops.adapters.core import AdmissionError, canonical, digest
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
        CONTROL_ROLLOVER_ADOPT_ACTIVITY,
        CONTROL_ROLLOVER_PREPARE_ACTIVITY,
        CONTROL_ROLLOVER_UPDATE,
        CONTROL_UPDATE,
        CONTROL_WORKFLOW_TYPE,
        CURATION_INITIAL_GRAPH_NAME,
        CURATION_SUCCESSOR_GRAPH_NAME,
    )
    from vaultops.application.temporal_work import connect_owner_temporal, dispatch_admitted_work
    from vaultops.projection import generate_projection

    roots = make_separate_portable_fixture_roots(
        root, extra_inputs=("ops/config/team-catalog.json", "ops/config/work-methods.json"),
        review_queues=True,
    )
    source_path = roots.vault / SOURCE
    canary = "control-" + uuid4().hex
    source_path.write_text(
        (roots.vault / officer.BASE_CAPTURE).read_text().replace(
            "11111111-1111-4111-8111-111111111111", str(uuid4())
        ).replace("20260909-090000-mac-deadbeef", "Native Curation Control smoke")
        + f"\nSynthetic observation code: {canary}\n"
    )
    generated, code = generate_projection(roots)
    assert code == 0, generated
    application = KnowledgeApplication.from_roots(roots)
    source = application.reference(SOURCE)
    before = {path.relative_to(roots.vault).as_posix(): digest(path.read_bytes())
              for path in roots.vault.rglob("*") if path.is_file()}
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
        "owner_operation_id": "knowledgeos", "artifact_id": "artifact.curation.control_binding",
        "source_revision": digest(binding_bytes).removeprefix("sha256:"),
        "content_digest": digest(binding_bytes), "size_bytes": len(binding_bytes),
        "media_type": "application/json", "classification": "internal",
        "availability": "available_by_owner_export", "export_capability_id": "knowledgeos.team_binding.export",
    })
    suffix = uuid4().hex
    spec, run = curation_continuation_admission(
        application, binding, source, work_id="work.control." + suffix,
        work_run_id="workrun.control." + suffix, admission_receipt_id="admission.control." + suffix,
        runner_binding=runner_binding,
    )
    admitted_spec, admitted_source = copy.deepcopy(spec), copy.deepcopy(source)

    def exact_spec(selected):
        return dict(selected) == admitted_spec and application.reference(SOURCE) == admitted_source

    exops = KnowledgeExOps(
        application, authorize_actor=lambda actor, action: (
            actor.actor_id == ACTOR and actor.ingress in {"cli", "web"}
            and action in {"submit_work", "read_work", "control_work"}
        ), director_admitted=exact_spec, teams={binding.team_id: binding},
        current_team_binding=lambda selected: binding if selected == binding.team_id else None,
        worker_identity="knowledgeos.worker.native_control_smoke",
        director_method_source_admitted=lambda selected, reference: (
            exact_spec(selected) and dict(reference) == admitted_source
        ),
    )
    admitted = exops.submit_work(
        spec, run, actor=OwnerActor(ACTOR, "cli"), action_id="action.control." + suffix,
        expires_at=datetime.now(UTC) + timedelta(minutes=15), method_source_reference=source,
        control_checkpoint=True,
    )
    transport = TemporalBinding(
        owner_operation_id="knowledgeos", target_host="server:7233", namespace="knowledgeos",
        task_queue="knowledgeos.work.v1", workflow_type=CONTROL_WORKFLOW_TYPE,
        workflow_id_prefix="knowledgeos/", update_name=CONTROL_UPDATE,
        signal_name="curation.control.signal.disabled", query_name=CONTROL_QUERY, tls=False,
    )
    scoped = await connect_owner_temporal(exops, transport)
    runner = continuation.NoProvider(runner_binding)
    executor = CurationContinuationExecutor(exops, runner=runner)
    controller = CurationRunControl(exops, executor)
    workflow, plugin = control_worker_components(exops, executor, controller)
    key = "exops.work." + run["work_run_id"]
    async with Worker(sdk, task_queue=transport.task_queue, workflows=[workflow], plugins=[plugin],
                      identity=exops.worker_identity + ".original"):
        dispatch_receipt = await dispatch_admitted_work(exops, scoped, run["work_run_id"])
        handle = sdk.get_workflow_handle("knowledgeos/" + run["work_run_id"])
        async with asyncio.timeout(90):
            while True:
                state = await handle.query(CONTROL_QUERY)
                if state["status"] == "awaiting_human":
                    break
                await asyncio.sleep(0.1)
        checkpoint = copy.deepcopy(application.journal.read()["intents"][key])
        initial_target = state["target"]
    # Admission occurs only after actual idle Worker shutdown, twice for resume.
    outage_before = application.journal.read()
    http = OwnerHTTP(exops, controller)
    foreign = OwnerHTTP(exops, controller, actor_id="other")
    commands, applied, outages, lost_responses = [], [], [], []
    read_body = {"work_run_id": run["work_run_id"]}
    target = initial_target
    final_runner = runner
    rollover_evidence = None
    original_history = None
    try:
        status, observed_target, _ = await asyncio.to_thread(http.call, "control/target", read_body)
        assert status == 200 and observed_target == initial_target
        body_status, _, _ = await asyncio.to_thread(
            http.call, "control/target", {**read_body, "actor_id": "other"},
        )
        foreign_status, _, _ = await asyncio.to_thread(foreign.call, "control/target", read_body)
        assert body_status == foreign_status == 403
        assert application.journal.read() == outage_before
        kinds = ("cancel",) if case == "cancel" else ("pause", "resume")
        for index, kind in enumerate(kinds):
            before_admission = application.journal.read()
            before_run = before_admission["intents"][key]["run"]
            request = {**read_body, "command_id": f"command.{kind}.{suffix}", "target": target,
                       "expires_at": (datetime.now(UTC) + timedelta(minutes=5)).isoformat()}
            route = "control/" + kind
            for server, supplied in (
                (http, {**request, "actor_id": ACTOR}),
                (foreign, request),
            ):
                denied_status, _, _ = await asyncio.to_thread(
                    server.call, route, supplied,
                )
                assert denied_status == 403
            bad_target = copy.deepcopy(request)
            bad_target["command_id"] = f"command.foreign.{kind}.{suffix}"
            bad_target["target"]["temporal_run_id"] = "foreign-native-run"
            stale_status, _, _ = await asyncio.to_thread(http.call, route, bad_target)
            expired_request = {**request, "command_id": f"command.expired.{kind}.{suffix}",
                               "expires_at": (datetime.now(UTC) - timedelta(seconds=1)).isoformat()}
            expired_status, _, _ = await asyncio.to_thread(http.call, route, expired_request)
            assert stale_status >= 400 and expired_status >= 400
            if kind == "resume":
                stale_initial = {**request, "command_id": "command.stale." + suffix,
                                 "target": initial_target}
                stale_initial_status, _, _ = await asyncio.to_thread(http.call, route, stale_initial)
                assert stale_initial_status >= 400
            assert application.journal.read() == before_admission
            status, received, received_raw = await asyncio.to_thread(http.call, route, request)
            assert status == 200 and received["application_state"] == "received"
            status, replayed, _ = await asyncio.to_thread(http.call, route, request)
            assert status == 200 and replayed == received
            pending_record = application.journal.read()["intents"][key]
            pending_run = pending_record["run"]
            assert pending_run["state"] == ("paused" if kind == "resume" else "awaiting_human")
            assert pending_run["revision"] == before_run["revision"] + 1
            assert pending_run["control_revision"] == before_run["control_revision"]
            assert pending_run["pending_action_ids"] == [request["command_id"]]
            assert pending_record["curation_initial_assessment"] == checkpoint["curation_initial_assessment"]
            assert "curation_model_request" not in pending_record
            assert not list((roots.vault / "01_AI_Review/Pending").glob("*.md"))
            outages.append({"kind": kind, "worker_shutdown_before_command": True,
                            "journal_digest_before_admission": digest(canonical(before_admission)),
                            "received_run": pending_run})
            fresh_runner = (
                officer.ModelRendezvous(runner_binding, application)
                if kind == "resume" else continuation.NoProvider(runner_binding)
            )
            fresh_executor = CurationContinuationExecutor(exops, runner=fresh_runner)
            fresh_controller = CurationRunControl(exops, fresh_executor)
            fresh_workflow, fresh_plugin = control_worker_components(exops, fresh_executor, fresh_controller)
            client = await connect_control_temporal(fresh_controller, transport)
            payload = {"command_id": request["command_id"], "kind": kind, "target": target}

            class DiscardAppliedResponse:
                def __init__(self, bound_client):
                    self.client = bound_client

                async def update(self, dispatch):
                    response = await self.client.update(dispatch)
                    assert response["application_state"] == "applied"
                    lost_responses.append(response)
                    raise ConnectionError("Injected response loss after native Update application")

            async with Worker(sdk, task_queue=transport.task_queue, workflows=[fresh_workflow],
                              plugins=[fresh_plugin], identity=exops.worker_identity + f".replacement.{index}"):
                if case == "rollover" and kind == "resume":
                    dispatch = {
                        "owner_operation_id": "knowledgeos", "work_run_id": run["work_run_id"],
                        "action_id": admitted["action_id"], "content_digest": admitted["content_digest"],
                        "work_spec_digest": spec["spec_digest"],
                    }
                    before_rollover = application.journal.read()
                    invalid_dispatch = {**dispatch, "work_spec_digest": "sha256:foreign"}
                    try:
                        await handle.execute_update(
                            CONTROL_ROLLOVER_UPDATE, invalid_dispatch, id="rollover.foreign." + suffix,
                        )
                    except (WorkflowUpdateFailedError, RPCError):
                        pass
                    else:
                        raise AssertionError("Foreign rollover dispatch was accepted")
                    assert application.journal.read() == before_rollover
                    requested = await handle.execute_update(
                        CONTROL_ROLLOVER_UPDATE, dispatch, id="rollover.request." + suffix,
                    )
                    assert requested == {
                        "rollover_state": "requested", "source_temporal_run_id": target["temporal_run_id"],
                    }
                    async with asyncio.timeout(90):
                        while True:
                            state = await handle.query(CONTROL_QUERY)
                            if (
                                state["temporal_run_id"] != target["temporal_run_id"]
                                and state["status"] == "paused"
                            ):
                                break
                            await asyncio.sleep(0.1)
                    assert state["status"] == "paused" and state["target"] == target
                    new_temporal_run_id = state["temporal_run_id"]
                    adopted_snapshot = application.journal.read()
                    assert adopted_snapshot["intents"][key] == pending_record
                    assert adopted_snapshot["intents"][key]["run"]["pending_action_ids"] == [request["command_id"]]
                    for denied_native_id in (target["temporal_run_id"], "foreign-native-run"):
                        try:
                            fresh_controller.authorize_native_run(
                                run["work_run_id"], temporal_run_id=denied_native_id,
                            )
                        except AdmissionError:
                            pass
                        else:
                            raise AssertionError("Old or foreign native Run retained execution authority")
                    assert application.journal.read() == adopted_snapshot
                    # The applied PAUSE cache crossed the native history boundary.
                    # Replay it through the NEW Workflow, not only owner reconciliation.
                    cached = await handle.execute_update(
                        CONTROL_UPDATE, commands[0], id=commands[0]["command_id"],
                    )
                    assert cached == applied[0] and application.journal.read() == adopted_snapshot
                    try:
                        await handle.execute_update(
                            CONTROL_ROLLOVER_UPDATE, dispatch, id="rollover.second." + suffix,
                        )
                    except (WorkflowUpdateFailedError, RPCError):
                        pass
                    else:
                        raise AssertionError("Second bounded rollover was accepted")
                    assert application.journal.read() == adopted_snapshot
                    original_handle = sdk.get_workflow_handle(
                        handle.id, run_id=initial_target["temporal_run_id"],
                    )
                    original_history = await original_handle.fetch_history()
                    rollover_evidence = {
                        "source_temporal_run_id": target["temporal_run_id"],
                        "replacement_temporal_run_id": new_temporal_run_id,
                        "request_receipt": requested, "restored_status": state,
                        "pending_command": payload, "work_record_unchanged": True,
                        "cached_pause_receipt_replayed": True, "second_rollover_denied": True,
                        "foreign_dispatch_denied": True,
                        "old_and_foreign_native_runs_fenced": True,
                        "owner_handoff": adopted_snapshot["intents"]["curation.rollover." + run["work_run_id"]],
                    }
                    assert not list((roots.vault / "01_AI_Review/Pending").glob("*.md"))
                    assert "curation_model_request" not in pending_record
                # Same stable native Update ID, including one discarded applied
                # response. Owner reconciliation must not redispatch application.
                responses = await asyncio.gather(
                    deliver_control(fresh_controller, DiscardAppliedResponse(client), payload),
                    deliver_control(fresh_controller, client, payload),
                )
                assert responses[0] == responses[1]
                command_receipt = responses[0]
                if kind == "pause":
                    state = await handle.query(CONTROL_QUERY)
                    assert state["status"] == "paused"
                    assert state["target"] == command_receipt["next_target"]
                    status, work_status, _ = await asyncio.to_thread(http.call, "read", read_body)
                    assert status == 200 and work_status["run"]["state"] == "paused"
                    status, paused_target, _ = await asyncio.to_thread(http.call, "control/target", read_body)
                    assert status == 200 and paused_target == state["target"]
                    paused = application.journal.read()["intents"][key]
                    assert paused["run"]["revision"] == before_run["revision"] + 2
                    assert paused["run"]["control_revision"] == before_run["control_revision"] + 1
                    for field in ("segments_started", "profile_invocations", "repeated_gap_count",
                                  "graph_run_ids", "last_gap_signature", "latest_assessment_id",
                                  "generation", "fencing_token"):
                        assert paused["run"][field] == checkpoint["run"][field]
                    assert paused["admitted_method_segments"] == checkpoint["admitted_method_segments"]
                    assert not paused["run"]["pending_action_ids"]
                    assert "curation_model_request" not in paused
                    assert fresh_runner.calls == runner.calls == 0
                    paused_history = await handle.fetch_history()
                    paused_details = recovery.history_details(paused_history, temporal_run_id=target["temporal_run_id"])
                    assert len(paused_details["scheduled"]) == 5
                    assert not list((roots.vault / "01_AI_Review/Pending").glob("*.md"))
                    target = command_receipt["next_target"]
                else:
                    native_result = await asyncio.wait_for(handle.result(), timeout=420)
                    final_runner = fresh_runner
                    if kind == "cancel":
                        assert native_result == command_receipt
                    else:
                        assert native_result != command_receipt
            # The Worker has stopped again before the next loop admits RESUME.
            commands.append(payload)
            applied.append(command_receipt)
            status, command_result, command_raw = await asyncio.to_thread(
                http.call, "control/command", {**read_body, "command_id": request["command_id"]},
            )
            assert status == 200 and command_result == command_receipt
            assert received_raw and command_raw
        snapshot = application.journal.read()
        record = snapshot["intents"][key]
        final_run = record["run"]
        assert record["curation_initial_assessment"] == checkpoint["curation_initial_assessment"]
        assert record["curation_capture_evidence"] == checkpoint["curation_capture_evidence"]
        assert final_run["repeated_gap_count"] == checkpoint["run"]["repeated_gap_count"] == 1
        assert final_run["generation"] == checkpoint["run"]["generation"]
        assert final_run["fencing_token"] == checkpoint["run"]["fencing_token"]
        pending = list((roots.vault / "01_AI_Review/Pending").glob("*.md"))
        model_evidence = {"provider_invocations": 0}
        if case == "cancel":
            assert final_run["state"] == "cancelled"
            assert final_run["revision"] == checkpoint["run"]["revision"] + 2
            assert final_run["control_revision"] == checkpoint["run"]["control_revision"] + 1
            assert final_run["segments_started"] == final_run["profile_invocations"] == 1
            assert record["admitted_method_segments"] == checkpoint["admitted_method_segments"]
            assert not pending and runner.calls == final_runner.calls == 0
            assert not any(item.get("work_binding") for item in snapshot["intents"].values())
            http_result = None
        else:
            from vaultops.application.work import record_digest

            assert final_run["state"] == "completed" and record["receipt"] == native_result
            assert final_run["revision"] == checkpoint["run"]["revision"] + 2 * len(kinds) + 2
            assert final_run["control_revision"] == checkpoint["run"]["control_revision"] + len(kinds) + 2
            assert final_run["segments_started"] == final_run["profile_invocations"] == 2
            segments = list(record["admitted_method_segments"].values())
            assert len(segments) == 2 and len(pending) == 1
            first, successor = (item["segment"] for item in segments)
            assert first == checkpoint["curation_initial_assessment"]["segments"][0]
            assert successor["segment_index"] == 2
            assert successor["predecessor_graph_run_id"] == first["graph_run_id"]
            remaining = checkpoint["curation_initial_assessment"]["remaining_work"]
            assert successor["trigger_reference_id"] == remaining["remaining_work_id"]
            assert successor["trigger_digest"] == record_digest(remaining)
            assert record["assessment"]["completion_state"] == "complete"
            assert record["assessment"]["assessor_id"] == binding.eval_officer_executor_id
            assert binding.eval_officer_executor_id not in spec["allowed_executor_ids"]
            assert all(item["evaluator_id"] == binding.eval_officer_executor_id
                       for item in record["evaluations"])
            proposal = pending[0].read_bytes()
            http_result = await asyncio.to_thread(officer.owner_http, exops, run["work_run_id"], proposal)
            assert final_run["result_reference"]["content_digest"] == digest(proposal)
            assert final_runner.response is not None and canary in final_runner.response["final_text"]
            assert canary not in json.dumps(record["curation_model_request"])
            assert final_runner.response["runner_binding"] == runner_binding
            assert sum(item["name"] == "proposal_create" for item in final_runner.response["tool_calls"]) == 1
            assert set(final_runner.denials) == {"wrong_source_read", "wrong_source_proposal", "ungranted_tool"}
            model_evidence = {"provider_invocations": 1, "response": final_runner.response,
                              "scope_denials": final_runner.denials}
        assert not final_run["pending_action_ids"]
        stale_target_status, _, _ = await asyncio.to_thread(http.call, "control/target", read_body)
        assert stale_target_status == 403
        # In particular the OLD PAUSE receipt reconciles after RESUME and final
        # result revisions; no checkpoint reopening, graph execution or writes.
        for payload, receipt in zip(commands, applied, strict=True):
            assert reconcile_control(fresh_controller, payload) == receipt
            assert await deliver_control(fresh_controller, client, payload) == receipt
            status, historical, _ = await asyncio.to_thread(
                http.call, "control/command", {**read_body, "command_id": payload["command_id"]},
            )
            assert status == 200 and historical == receipt
        assert application.journal.read() == snapshot
        description = await handle.describe()
        history = await handle.fetch_history()
        details = recovery.history_details(history, temporal_run_id=description.run_id)
        expected_initial = [CURATION_INITIAL_GRAPH_NAME + "." + node for node in continuation.INITIAL_NODES]
        expected_initial += [CONTROL_CHECKPOINT_ACTIVITY]
        if case == "rollover":
            assert original_history is not None and rollover_evidence is not None
            original_details = recovery.history_details(
                original_history, temporal_run_id=initial_target["temporal_run_id"],
            )
            assert [row["name"] for row in original_details["scheduled"]] == (
                expected_initial + [CONTROL_APPLY_ACTIVITY, CONTROL_ROLLOVER_PREPARE_ACTIVITY]
            )
            assert original_details["event_counts"].get("EVENT_TYPE_WORKFLOW_EXECUTION_CONTINUED_AS_NEW") == 1
            assert not original_details["event_counts"].get("EVENT_TYPE_WORKFLOW_EXECUTION_COMPLETED", 0)
            assert not original_details["failures"]
            assert original_details["completed_scheduled_event_ids"] == [
                row["event_id"] for row in original_details["scheduled"]
            ]
            expected = [CONTROL_ROLLOVER_ADOPT_ACTIVITY, CONTROL_APPLY_ACTIVITY]
        else:
            expected = expected_initial + [CONTROL_APPLY_ACTIVITY] * len(kinds)
        if case != "cancel":
            expected += [CURATION_SUCCESSOR_GRAPH_NAME + "." + node for node in continuation.SUCCESSOR_NODES]
        assert [row["name"] for row in details["scheduled"]] == expected
        assert details["completed_scheduled_event_ids"] == [row["event_id"] for row in details["scheduled"]]
        assert not details["failures"]
        assert not any(details["event_counts"].get(name, 0) for name in (
            "EVENT_TYPE_ACTIVITY_TASK_FAILED", "EVENT_TYPE_WORKFLOW_EXECUTION_FAILED",
            "EVENT_TYPE_WORKFLOW_EXECUTION_CONTINUED_AS_NEW",
        ))
        assert details["event_counts"].get("EVENT_TYPE_WORKFLOW_EXECUTION_COMPLETED") == 1
        assert details["event_counts"].get("EVENT_TYPE_WORKFLOW_EXECUTION_UPDATE_COMPLETED") == (
            2 if case == "rollover" else len(kinds)
        )
        start = history.events[0].workflow_execution_started_event_attributes
        assert description.workflow_type == start.workflow_type.name == CONTROL_WORKFLOW_TYPE
        start_payload = {
            "owner_operation_id": "knowledgeos", "work_run_id": run["work_run_id"],
            "action_id": admitted["action_id"], "content_digest": admitted["content_digest"],
            "work_spec_digest": spec["spec_digest"],
        }
        decoded = await sdk.data_converter.decode(start.input.payloads)
        if case == "rollover":
            assert description.run_id == rollover_evidence["replacement_temporal_run_id"]
            assert start.continued_execution_run_id == initial_target["temporal_run_id"]
            assert decoded[0]["dispatch"] == start_payload
            assert decoded[0]["rollover"]["source_temporal_run_id"] == initial_target["temporal_run_id"]
            continued = original_history.events[-1].workflow_execution_continued_as_new_event_attributes
            assert continued.new_execution_run_id == description.run_id
            assert await sdk.data_converter.decode(continued.input.payloads) == decoded
        else:
            assert description.run_id == initial_target["temporal_run_id"]
            assert decoded == [start_payload]
        replay_executor = CurationContinuationExecutor(exops, runner=continuation.NoProvider(runner_binding))
        replay_controller = CurationRunControl(exops, replay_executor)
        replay_workflow, replay_plugin = control_worker_components(exops, replay_executor, replay_controller)
        replay_rows = []
        with ThreadPoolExecutor(max_workers=2) as pool:
            for selected_history in ([original_history, history] if original_history is not None else [history]):
                replay = await Replayer(
                    workflows=[replay_workflow], plugins=[replay_plugin], namespace=transport.namespace,
                    data_converter=sdk.data_converter, workflow_task_executor=pool,
                ).replay_workflow(selected_history)
                assert replay.replay_failure is None and replay.history == selected_history
                assert application.journal.read() == snapshot
                replay_rows.append({"status": "PASS", "events": len(selected_history.events), "owner_writes": 0})
        after = {path.relative_to(roots.vault).as_posix(): digest(path.read_bytes())
                 for path in roots.vault.rglob("*") if path.is_file()}
        assert all(after.get(path) == value for path, value in before.items())
        assert set(after) - set(before) == {path.relative_to(roots.vault).as_posix() for path in pending}
        cancellation_experience = None
        if case == "cancel":
            reconstructed = KnowledgeApplication.from_roots(roots)
            learning, learning_code = reconstructed.recover()
            assert learning_code == 0, learning
            assert learning["experience"] == {
                "observed": 1, "skipped_unavailable": 0, "deferred": 0,
                "produced": 1, "quarantined": 0,
            }
            learned = reconstructed.journal.read()
            for field in ("intents", "receipts", "human_requests", "decisions"):
                assert learned[field] == snapshot[field]
            event, = learned["experience_events"]
            candidate, = learned["experience"]
            assert (event["category"], event["outcome_state"]) == ("cancellation", "aborted")
            assert event["source_run_id"] == run["work_run_id"]
            assert event["evidence_references"][0]["availability"] == "unavailable"
            assert candidate["source_record"] == event["source_record"]
            assert candidate["evidence_references"] == []
            repeated, repeated_code = KnowledgeApplication.from_roots(roots).recover()
            assert repeated_code == 0 and repeated["experience"] == {
                "observed": 0, "skipped_unavailable": 0, "deferred": 0,
                "produced": 0, "quarantined": 0,
            }
            assert reconstructed.journal.read() == learned
            assert runner.calls == final_runner.calls == 0
            cancellation_experience = {
                "report": learning, "reconstructed_report": repeated,
                "source_event": event, "candidate": candidate,
                "historical_control_evidence_unchanged": True,
                "source_identity_deduplicated": True,
                "unknown_effects_relabelled": False,
                "journal_evidence_export": "unavailable",
            }
        return {
            "case": case, "status": "PASS", "work_run_id": run["work_run_id"],
            "workflow_type": CONTROL_WORKFLOW_TYPE, "workflow_id": description.id,
            "temporal_run_id": description.run_id, "native_binding_digest": binding.native_binding_digest,
            "initial_target": initial_target, "worker_outages": outages,
            "owner_http": {"body_actor_status": body_status,
                           "foreign_actor_status": foreign_status, "result": http_result},
            "dispatch_receipt": dispatch_receipt, "control_receipts": applied, "receipt": native_result,
            "initial_assessment": checkpoint["curation_initial_assessment"], "final_run": final_run,
            "history": json.loads(history.to_json()), "history_details": details,
            "concurrent_same_update_ids": True, "discarded_applied_responses": len(lost_responses),
            "rollover": rollover_evidence,
            "original_history": json.loads(original_history.to_json()) if original_history is not None else None,
            "historical_receipts_read_only": True, "model_execution": model_evidence,
            "provider_invocations": int(case != "cancel"), "proposal_effects": len(pending),
            "pending_count": len(pending), "source_unchanged": True,
            "existing_fixture_vault_files_unchanged": True,
            "fresh_sdk_replay": replay_rows,
            "cancellation_experience": cancellation_experience,
        }
    finally:
        await asyncio.to_thread(http.close)
        await asyncio.to_thread(foreign.close)


async def native_roundtrip(root: Path, runner_binding: dict, *, case: str) -> dict:
    versions = {package: importlib.metadata.version(package)
                for package in ("temporalio", "temporalio-langgraph", "langgraph")}
    assert versions == {"temporalio": "1.34.0", "temporalio-langgraph": "0.1.0", "langgraph": "1.2.0"}
    sdk = await recovery.connect_temporal()
    selected = ("cancel", "pause-resume") if case == "both" else (case,)
    cases = [await run_case(root / name, runner_binding, sdk, case=name) for name in selected]
    return {
        "status": "PASS", "scope": "disposable quiescent revision-bound Curation control and bounded rollover",
        "versions": {"python": sys.version.split()[0], **versions,
                     "temporal_server": "1.31.3", "postgres": "16.14"},
        "cases": cases, "provider_invocations": sum(item["provider_invocations"] for item in cases),
        "canonical_apply": False,
        "claims_excluded": ["production roots", "GUI", "in-flight control", "cutover",
                            "general human control", "rollback", "repeated pause/resume"],
        "live_roots_unmounted": True, "profiles_unmounted": True, "disposable_roots_removed": False,
    }


def run_container(compose, *, binding, control, container, case="both"):
    return continuation.model_container(
        compose, binding=binding, control=control, container=container,
        script="curation_control_smoke.py", extra_args=("--case", case),
        expected_requests=int(case != "cancel"),
    )


def host(evidence_path: Path, *, case="both") -> None:
    original_runner, original_marker = team.model_container, team.MARKER
    original_image = os.environ.get("KNOWLEDGEOS_TEAM_IMAGE")
    try:
        def selected_container(compose, **kwargs):
            return run_container(compose, **kwargs, case=case)

        team.model_container, team.MARKER = selected_container, MARKER
        with redirect_stdout(io.StringIO()):
            team.host(evidence_path)
        print(f"PASS: native quiescent control ({case}), outage receipts and replay; evidence: {evidence_path}")
    finally:
        team.model_container, team.MARKER = original_runner, original_marker
        if original_image is not None:
            os.environ["KNOWLEDGEOS_TEAM_IMAGE"] = original_image


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("host", "run"))
    parser.add_argument("--evidence", type=Path)
    parser.add_argument("--runner-binding", type=json.loads)
    parser.add_argument("--case", choices=("both", "cancel", "pause-resume", "rollover"), default="both")
    args = parser.parse_args()
    if args.action == "host":
        if args.runner_binding is not None:
            parser.error("Host selects the exact native runtime binding")
        evidence = args.evidence or (Path(__file__).resolve().parents[5] / "Tmp"
                                     / "knowledgeos-team-control" / (uuid4().hex + ".json"))
        if evidence.exists() or evidence.is_symlink():
            raise FileExistsError("Control evidence is create-only")
        host(evidence, case=args.case)
    else:
        if args.runner_binding is None or args.evidence is not None:
            parser.error("Run requires only explicit --runner-binding")
        os.umask(0o077)
        with tempfile.TemporaryDirectory(prefix="knowledgeos-team-control-") as directory:
            report = asyncio.run(native_roundtrip(Path(directory), args.runner_binding, case=args.case))
        report["disposable_roots_removed"] = not Path(directory).exists()
        assert report["disposable_roots_removed"]
        print(MARKER + json.dumps(report, ensure_ascii=False, separators=(",", ":")))


if __name__ == "__main__":
    main()

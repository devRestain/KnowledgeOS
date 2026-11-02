"""Actual Temporal/official LangGraph steering; disposable roots and no provider."""
from __future__ import annotations

import argparse
import asyncio
import base64
import copy
import http.client
import importlib.metadata
import io
import json
import os
import signal
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import redirect_stdout
from datetime import timedelta
from pathlib import Path
from uuid import uuid4

import curation_continuation_smoke as continuation
import curation_recovery_smoke as recovery
import curation_work_smoke as team

MARKER = "KNOWLEDGEOS_STEERING_EVIDENCE="


class OwnerHTTP:
    def __init__(self, exops):
        from support.steering_factory import ACTOR
        from vaultops.interfaces.loopback_owner import create_loopback_owner
        self.server = create_loopback_owner(exops, actor_id=ACTOR.actor_id, port=0)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def call(self, route, payload):
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=15)
        try:
            connection.request("POST", "/owner/work/steering/" + route, json.dumps(payload), headers={
                "Content-Type": "application/json"})
            response = connection.getresponse()
            raw = response.read()
            return response.status, json.loads(raw), raw
        finally:
            connection.close()

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=10)


async def run_case(root, sdk):
    from temporalio.worker import Replayer, Worker

    from support.steering_factory import fixture
    from vaultops.adapters.core import AdmissionError, canonical, digest
    from vaultops.application.curation_steering import _command, curation_steering_native_digest
    from vaultops.application.curation_steering_contract import QUERY, UPDATE
    from vaultops.application.curation_steering_temporal import (
        deliver_steering,
        steering_worker_components,
    )
    from vaultops.application.work import work_spec_digest

    controller, _spec, run, successor, authority, source_path, canary = fixture(root)
    work_id = run["work_run_id"]
    before_vault = {path.relative_to(controller.app.roots.vault).as_posix(): digest(path.read_bytes())
                    for path in controller.app.roots.vault.rglob("*") if path.is_file()}
    workflow, plugin = steering_worker_components(controller)
    queue = "knowledgeos.steering.smoke." + uuid4().hex
    key = "exops.work." + work_id
    def record():
        return controller.app.journal.read()["intents"][key]
    def worker(suffix):
        return Worker(sdk, task_queue=queue, workflows=[workflow], plugins=[plugin], identity="knowledgeos.steering.smoke." + suffix)
    http = OwnerHTTP(controller.exops)
    receipts = []
    async def call(route, body, *, expected=200):
        status, value, raw = await asyncio.to_thread(http.call, route, body)
        assert status == expected, (route, status, value)
        return value, raw
    async def receive(kind, content, **extra):
        view, _ = await call("view", {"work_run_id": work_id})
        reference, _ = await call("artifact", {"work_run_id": work_id, "content": content})
        body = {"work_run_id": work_id, "command_id": "command." + kind + "." + uuid4().hex,
                "kind": kind, "target": view["target"], "expires_at": (controller.app.clock() + timedelta(minutes=10)).isoformat(),
                "directive_reference": reference, **extra}
        receipt, raw = await call("request", body)
        assert receipt["application_state"] == "received"
        retained = controller.app.journal.read()["intents"][_command(body["command_id"])]
        assert retained["dispatch"]["target"] == body["target"] and retained["state"] == "received"
        assert retained["execution_intent"]["generation"] == body["target"]["generation"]
        return body, receipt, raw
    try:
        async with worker("initial"):
            handle = await sdk.start_workflow(workflow.run, controller.dispatch(work_id), id="knowledgeos/" + work_id, task_queue=queue)
            async with asyncio.timeout(60):
                while (await handle.query(QUERY))["status"] != "waiting":
                    await asyncio.sleep(0.05)
                assert (await handle.query(QUERY))["pending_graph_nodes"] == ["normalization_report"]
        # Worker is actually stopped. The human receipt and outbox must survive
        # without an Update handler or a fabricated application acknowledgement.
        initial = copy.deepcopy(record())
        assert initial["run"]["state"] == "awaiting_human" and initial["run"].get("active_graph_run_id")
        assert initial["steering_triage"]["not_run_nodes"] == ["node.normalization_proposal"]
        assert initial["steering_native_boundary"]["pending_nodes"] == ["normalization_report"]
        body, queued, _receipt_bytes = await receive("answer", {"answer": "Owner-labeled " + canary})
        after_receipt = controller.app.journal.read()
        duplicate, _duplicate_bytes = await call("request", body)
        assert duplicate == queued and controller.app.journal.read() == after_receipt
        assert record()["run"]["state"] == "awaiting_human" and record()["run"]["pending_action_ids"] == [body["command_id"]]
        outage = {"worker_stopped": True, "receipt_state": queued["application_state"], "outbox_committed": True,
                  "run_state": record()["run"]["state"]}

        class LostAcknowledgement:
            def get_workflow_handle(self, *args, **kwargs):
                actual = sdk.get_workflow_handle(*args, **kwargs)
                class Handle:
                    async def execute_update(self, *args, **kwargs):
                        await actual.execute_update(*args, **kwargs)
                        raise TimeoutError("deliberately discarded the successful native Update response")
                return Handle()
        async with worker("reconstructed"):
            applied = await deliver_steering(controller, LostAcknowledgement(), work_id, body["command_id"])
            assert applied["application_state"] == "applied"
            receipts.append(applied)
            observed, _ = await call("request", body)
            assert observed == applied
            # Temporal's same Update ID and the owner receipt agree after loss.
            native = await handle.execute_update(UPDATE, controller.command_dispatch(work_id, body["command_id"]), id=body["command_id"])
            assert native == applied
            parameter_body, _pending, _raw = await receive("parameter", {"value": "alphabetical"}, parameter_id="report.order")
            receipts.append(await deliver_steering(controller, sdk, work_id, parameter_body["command_id"]))
            assert record()["steering_bundles"] == initial["steering_bundles"]
            cutover_body, _pending, _raw = await receive("cutover", {"reason": "Replace the genuine unexecuted normalization stage"})
            receipts.append(await deliver_steering(controller, sdk, work_id, cutover_body["command_id"]))
            closed = copy.deepcopy(record())
            assert closed["run"]["generation"] == 2 and not closed["run"].get("active_graph_run_id")
            assert closed["steering_partial"]["assessment"]["completion_state"] == "incomplete"
            assert closed["steering_partial"]["assessment"]["criterion_outcomes"][0]["outcome"] == "not_run"
            assert closed["steering_bundles"] == initial["steering_bundles"]
            cutover_command = controller.app.journal.read()["intents"][_command(cutover_body["command_id"])]
            assert cutover_command["cutting_over_run"]["state"] == "cutting_over"
            assert cutover_command["applied_run"]["state"] == "paused"
            old_token = {"work_run_id": work_id, "generation": 1, "fencing_token": initial["run"]["fencing_token"],
                         "graph_run_id": initial["run"]["active_graph_run_id"]}
            before = controller.app.journal.read()
            try:
                controller.normalize(old_token, temporal_run_id=initial["temporal_run_id"])
            except AdmissionError:
                pass
            else:
                raise AssertionError("Old generation published a canonical effect")
            assert controller.app.journal.read() == before
            view, _ = await call("view", {"work_run_id": work_id})
            reference, _ = await call("artifact", {"work_run_id": work_id, "content": {"successor_spec_digest": successor["spec_digest"]}})
            amendment_body = {"work_run_id": work_id, "command_id": "command.amend." + uuid4().hex, "kind": "amend_work",
                              "target": view["target"], "expires_at": (controller.app.clock() + timedelta(minutes=10)).isoformat(),
                              "directive_reference": reference, "successor_spec": successor}
            before = controller.app.journal.read()
            status, _value, _raw = await asyncio.to_thread(http.call, "request", amendment_body)
            assert status >= 400 and controller.app.journal.read() == before
            authority["amendment"] = True
            out_of_envelope = copy.deepcopy(successor)
            out_of_envelope["continuation_policy"]["max_segments"] = 3
            out_of_envelope["spec_digest"] = work_spec_digest(out_of_envelope)
            other_reference, _ = await call("artifact", {"work_run_id": work_id, "content": {"successor_spec_digest": out_of_envelope["spec_digest"]}})
            before = controller.app.journal.read()
            status, _value, _raw = await asyncio.to_thread(http.call, "request", {
                **amendment_body, "command_id": "command.expand." + uuid4().hex, "directive_reference": other_reference,
                "successor_spec": out_of_envelope})
            assert status >= 400 and controller.app.journal.read() == before
            admitted, _ = await call("request", amendment_body)
            assert admitted["application_state"] == "received"
            receipts.append(await deliver_steering(controller, sdk, work_id, amendment_body["command_id"]))
            assert record()["spec"] == successor and record()["steering_partial"] == closed["steering_partial"]
            resume_body, _pending, _raw = await receive("resume", {"reason": "Execute the separately admitted labeled successor"})
            receipts.append(await deliver_steering(controller, sdk, work_id, resume_body["command_id"]))
            result = await asyncio.wait_for(handle.result(), timeout=60)
            assert result["application_state"] == "recorded"
        final = record()
        exported, export_http_bytes = await call("export", {"work_run_id": work_id})
        raw = base64.b64decode(exported["content_base64"], validate=True)
        report = json.loads(raw)
        source_lines = [line.strip() for line in source_path.read_text().splitlines() if line.strip()]
        expected = {"source_digest": digest(source_path.read_bytes()), "lines": sorted(source_lines), "order": "alphabetical",
                    "answer": "Owner-labeled " + canary, "objective": successor["objective"]}
        expected.update(label="Owner-labeled " + canary, line_count=len(source_lines))
        assert raw == canonical(expected) and report == expected
        assert exported["reference"] == final["run"]["result_reference"]
        assert exported["assessment"]["completion_state"] == "complete"
        assert final["steering_partial"] == closed["steering_partial"]
        successor_bundle = final["steering_bundles"][final["run"]["graph_run_ids"][-1]]
        assert successor_bundle["segment"]["trigger_kind"] == "work_revision"
        assert successor_bundle["segment"]["predecessor_graph_run_id"] == initial["run"]["active_graph_run_id"]
        assert final["steering_successor_context"] == {"order": "alphabetical", "answer": "Owner-labeled " + canary}
        history = await handle.fetch_history()
        details = recovery.history_details(history, temporal_run_id=initial["temporal_run_id"])
        before = controller.app.journal.read()
        with ThreadPoolExecutor(max_workers=2) as pool:
            replay = await Replayer(workflows=[workflow], plugins=[plugin], namespace=sdk.namespace,
                                    data_converter=sdk.data_converter, workflow_task_executor=pool).replay_workflow(history)
        assert replay.replay_failure is None and controller.app.journal.read() == before
        after_vault = {path.relative_to(controller.app.roots.vault).as_posix(): digest(path.read_bytes())
                       for path in controller.app.roots.vault.rglob("*") if path.is_file()}
        assert before_vault == after_vault
        from vaultops.application import curation_steering
        module_dir = Path(curation_steering.__file__).parent
        modules = {name: digest((module_dir / name).read_bytes()) for name in (
            "curation_steering.py", "curation_steering_contract.py", "curation_steering_temporal.py", "curation_steering_workflow.py",
            "curation_work.py", "curation_control.py", "work.py")}
        return {"status": "PASS", "work_run_id": work_id, "native_workflow_id": "knowledgeos/" + work_id,
                "native_run_id": initial["temporal_run_id"], "native_activities": details["scheduled"],
                "outage": outage, "lost_acknowledgement_reconciled": True, "old_generation_rejected": True,
                "director_admission_required": True, "out_of_envelope_rejected": True, "receipts": receipts,
                "partial_assessment": closed["steering_partial"]["assessment"], "successor_segment": successor_bundle["segment"],
                "result_reference": exported["reference"], "owner_http_export_digest": digest(export_http_bytes),
                "exact_result_bytes_digest": digest(raw), "replay": {"events": len(history.events), "owner_writes": 0},
                "vault_files_preserved": len(before_vault), "native_binding_digest": curation_steering_native_digest(),
                "installed_modules": modules}
    finally:
        await asyncio.to_thread(http.close)


async def native_roundtrip(root):
    sdk = await recovery.connect_temporal()
    result = await run_case(root, sdk)
    return {"status": "PASS", "scope": "bounded deterministic native Curation question/parameter/partial CUTOVER/Director revision",
            "versions": {name: importlib.metadata.version(name) for name in ("temporalio", "temporalio-langgraph", "langgraph")},
            "case": result, "provider_invocations": 0, "canonical_apply": False, "live_roots_unmounted": True,
            "profiles_unmounted": True, "claims_excluded": ["model reasoning", "external or filesystem effect reconciliation", "running reassignment",
            "arbitrary objective or delegation amendment", "production", "GUI", "hard-loss during control commit"]}


def host(evidence_path):
    original_runner, original_marker = team.model_container, team.MARKER
    original_image = os.environ.get("KNOWLEDGEOS_TEAM_IMAGE")
    try:
        def container(compose, **kwargs):
            return continuation.model_container(compose, **kwargs, script="curation_steering_smoke.py", expected_requests=0)
        team.model_container, team.MARKER = container, MARKER
        with redirect_stdout(io.StringIO()):
            team.host(evidence_path)
        print("PASS: native bounded steering, exact Owner export and teardown; evidence: " + str(evidence_path))
    finally:
        team.model_container, team.MARKER = original_runner, original_marker
        if original_image is not None:
            os.environ["KNOWLEDGEOS_TEAM_IMAGE"] = original_image


async def hold_console(root, args):
    """Keep an actual waiting native Run available for bounded browser checks."""
    from temporalio.worker import Worker

    from support.steering_factory import ACTOR, fixture
    from vaultops.application.curation_steering_contract import QUERY
    from vaultops.application.curation_steering_temporal import (
        deliver_steering,
        steering_worker_components,
    )
    from vaultops.interfaces.loopback_owner import create_loopback_owner

    controller, _spec, run, successor, authority, _source, _canary = fixture(root)
    authority["amendment"] = args.admit_steering_revision
    sdk = await recovery.connect_temporal()
    workflow, plugin = steering_worker_components(controller)
    queue = "knowledgeos.steering.console." + uuid4().hex
    work_id = run["work_run_id"]
    server = create_loopback_owner(
        controller.exops, actor_id=ACTOR.actor_id,
        port=args.port, bind_host=args.bind_host)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for selected in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(selected, stop.set)
    attempted, admitted_at = set(), {}

    async def deliver_pending():
        while not stop.is_set():
            snapshot = controller.app.journal.read()
            for identity in snapshot["intents"]["exops.work." + work_id]["run"]["pending_action_ids"]:
                first_seen = admitted_at.setdefault(identity, loop.time())
                if identity in attempted or loop.time() - first_seen < args.delivery_delay_seconds:
                    continue
                # Exactly one delivery attempt, using the admitted Update ID.
                # Propagate failure through the task after owned teardown;
                # never swallow the error or blindly repeat an uncertain effect.
                attempted.add(identity)
                await deliver_steering(controller, sdk, work_id, identity)
            try:
                await asyncio.wait_for(stop.wait(), timeout=0.2)
            except TimeoutError:
                pass

    thread_started = False
    try:
        async with Worker(sdk, task_queue=queue, workflows=[workflow], plugins=[plugin],
                          identity="knowledgeos.steering.console.worker"):
            handle = await sdk.start_workflow(
                workflow.run, controller.dispatch(work_id), id="knowledgeos/" + work_id, task_queue=queue)
            async with asyncio.timeout(60):
                while (await handle.query(QUERY))["status"] != "waiting":
                    await asyncio.sleep(0.05)
                assert (await handle.query(QUERY))["pending_graph_nodes"] == ["normalization_report"]
            thread.start()
            thread_started = True
            print(json.dumps({
                "console_url": f"http://127.0.0.1:{server.server_port}/console/",
                "work_run_id": work_id, "native_run_id": (await handle.describe()).run_id,
                "scope": "actual interrupted native Curation steering; synthetic disposable roots only",
                "hold_seconds": args.hold_seconds, "delivery_delay_seconds": args.delivery_delay_seconds,
                "director_revision": successor if args.admit_steering_revision else None}), flush=True)
            print("STEERING_CONSOLE_READY", flush=True)
            delivery = asyncio.create_task(deliver_pending())
            # A failed delivery wakes the hold and is re-raised by the existing
            # teardown await, with its original exception and traceback intact.
            delivery.add_done_callback(lambda _finished: stop.set())
            try:
                try:
                    await asyncio.wait_for(stop.wait(), timeout=args.hold_seconds)
                except TimeoutError:
                    stop.set()
            finally:
                stop.set()
                # Stop new HTTP admissions, then let any started owner delivery
                # reconcile before shutting down its real Worker.
                await asyncio.to_thread(server.shutdown)
                thread.join(timeout=10)
                await delivery
            record = controller.app.journal.read()["intents"]["exops.work." + work_id]
            summary = {"work_run_id": work_id, "run_state": record["run"]["state"],
                       "application_state": record["application_state"],
                       "native_run_id": (await handle.describe()).run_id,
                       "commands": controller.view(work_id, actor=ACTOR)["commands"],
                       "owner_listener_closed": True}
            if record["run"]["state"] != "completed":
                # Disposable-service teardown, explicitly not a user CANCEL
                # receipt, effect rollback, or production recovery assertion.
                await handle.cancel()
            return summary
    finally:
        if thread_started and thread.is_alive():
            await asyncio.to_thread(server.shutdown)
            thread.join(timeout=10)
        server.server_close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("host", "run", "hold"))
    parser.add_argument("--evidence", type=Path)
    parser.add_argument("--runner-binding", type=json.loads)  # Maintained Team host wire; never invokes a provider.
    parser.add_argument("--port", type=int, default=17903)
    parser.add_argument("--bind-host", choices=("127.0.0.1", "owner"), default="127.0.0.1")
    parser.add_argument("--hold-seconds", type=int, default=1800)
    parser.add_argument("--delivery-delay-seconds", type=float, default=5)
    parser.add_argument("--admit-steering-revision", action="store_true",
                        help="Trusted fixture Director explicitly preadmits the exact labeled-report revision")
    args = parser.parse_args()
    if not 30 <= args.hold_seconds <= 3600 or not 1 <= args.delivery_delay_seconds <= 30:
        parser.error("Hold is bounded to 30..3600 seconds; delivery delay to 1..30 seconds")
    if args.action == "host":
        evidence = args.evidence or Path(__file__).resolve().parents[5] / "Tmp" / "knowledgeos-steering" / (uuid4().hex + ".json")
        if evidence.exists() or evidence.is_symlink():
            raise FileExistsError("Steering evidence is create-only")
        host(evidence)
    elif args.action == "hold":
        if args.evidence is not None or args.runner_binding is not None:
            parser.error("Hold does not take evidence or runner-binding inputs")
        os.umask(0o077)
        with tempfile.TemporaryDirectory(prefix="knowledgeos-steering-console-") as directory:
            summary = asyncio.run(hold_console(Path(directory), args))
        summary["disposable_roots_removed"] = not Path(directory).exists()
        print("STEERING_CONSOLE_CLOSED=" + json.dumps(summary, separators=(",", ":")), flush=True)
    else:
        os.umask(0o077)
        with tempfile.TemporaryDirectory(prefix="knowledgeos-steering-") as directory:
            report = asyncio.run(native_roundtrip(Path(directory)))
        report["disposable_roots_removed"] = not Path(directory).exists()
        assert report["disposable_roots_removed"]
        print(MARKER + json.dumps(report, ensure_ascii=False, separators=(",", ":")))


if __name__ == "__main__":
    main()

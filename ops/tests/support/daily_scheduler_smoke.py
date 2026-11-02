"""Actual-provider daily due/Director/Work/supervisor/provisional/review proof.

Run ``python3 ops/tests/support/daily_scheduler_smoke.py host --evidence NEW.json``
with the same explicit image, Hermes and synthetic Temporal bindings as the
native metadata smoke. No host Cron job, profile or live domain is changed.
"""
from __future__ import annotations

import argparse
import asyncio
import copy
import json
import os
import tempfile
from pathlib import Path
from uuid import uuid4

MARKER = "KNOWLEDGEOS_DAILY_SCHEDULER_EVIDENCE="


def fixture(root, route, runner):
    """Build one eligible source and policy-denied synthetic fixture notes."""
    from support.metadata_fixture import SOURCE, application, binding
    from vaultops.application.metadata_work import DAILY_METHOD_ID
    from vaultops.application.owner_runtime import OwnerRuntimePolicy, WritableOwnerRuntime
    from vaultops.note_engine import RELATION_PROPERTIES, parse_frontmatter, render_frontmatter
    from vaultops.projection import generate_projection

    app, canary = application(root, daily=True)
    for path, _raw, _properties in app.daily_stewardship()._notes():
        selected = app.roots.vault / path
        document = parse_frontmatter(selected.read_text())
        properties = dict(document.properties)
        if path != SOURCE:
            properties["ai_policy"] = "deny"
        else:
            # This single-source fixture denies every other note. Preserve no
            # canonical edge whose target is deliberately outside its resolver.
            for field in RELATION_PROPERTIES:
                if field in properties:
                    properties[field] = []
        selected.write_text(render_frontmatter(properties, document.body))
    report, code = generate_projection(app.roots)
    assert code == 0, report
    team = binding(app, route, DAILY_METHOD_ID)
    policy = {"version": 1, "actor_id": "owner.officer_smoke", "director_enabled": True,
              "actions": ["submit_work", "read_work", "daily_list", "daily_read", "daily_decide"],
              "worker_identity": "knowledgeos.worker.native_daily_scheduler", "templates": [],
              "freeform_research": False, "daily_enabled": True,
              "methods": {DAILY_METHOD_ID: {"team_binding": {**team.content(), "binding_reference": team.binding_reference},
                  "runner_binding": route, "admission_scope": {"source_paths": [SOURCE], "parameters": {}}}}}
    policy_path = root / "owner-policy.json"
    policy_path.write_text(json.dumps(policy))
    policy_path.chmod(0o600)
    with app.journal.writer() as writer:
        writer.commit(app.clock())
    runtime = WritableOwnerRuntime(app.roots, policy=OwnerRuntimePolicy(policy_path), runners={DAILY_METHOD_ID: runner})
    return runtime, canary, policy_path


def scheduler(runtime, *, limit=50):
    from vaultops.application.daily_scheduler import DailyAdmissionScheduler
    from vaultops.application.metadata_work import DAILY_METHOD_ID

    def authorized():
        return (runtime.policy.read()["daily_enabled"] and runtime.method_current(DAILY_METHOD_ID)
                and runtime.policy.actor_allowed(runtime.actor, "submit_work"))
    return DailyAdmissionScheduler(runtime.application, admit_request=runtime.admit_daily_request,
                                   authorize=authorized, limit=limit)


async def execute(runtime, runner):
    from clients.temporal import TemporalBinding
    from temporalio.api.workflowservice.v1 import RegisterNamespaceRequest
    from temporalio.client import Client
    from temporalio.service import RPCError, RPCStatusCode
    from temporalio.worker import Worker

    from vaultops.application.metadata_work import DAILY_METHOD_ID
    from vaultops.application.owner_runtime import DispatchSupervisor
    from vaultops.application.temporal_contract import DAILY_GRAPH_NAME, NATIVE_TEAM_ROUTES
    from vaultops.application.temporal_work import connect_owner_temporal

    wire = NATIVE_TEAM_ROUTES[DAILY_METHOD_ID][1]
    transport = TemporalBinding(owner_operation_id="knowledgeos", target_host="server:7233", namespace="knowledgeos",
        task_queue="knowledgeos.work.v1", workflow_type=wire, workflow_id_prefix="knowledgeos/",
        update_name="team.update.disabled", signal_name="team.signal.disabled", query_name="team.query.disabled", tls=False)
    async with asyncio.timeout(180):
        while True:
            try:
                sdk = await Client.connect(transport.target_host, namespace=transport.namespace, tls=False)
                await sdk.workflow_service.register_namespace(RegisterNamespaceRequest(namespace=transport.namespace,
                    workflow_execution_retention_period={"seconds": 3600}))
                break
            except RPCError as error:
                if error.status == RPCStatusCode.ALREADY_EXISTS:
                    break
                await asyncio.sleep(1)
            except (OSError, RuntimeError):
                await asyncio.sleep(1)
    scoped = await connect_owner_temporal(runtime.exops, transport)
    supervisor = DispatchSupervisor(runtime, {wire: scoped})
    workflow, plugin = runtime.worker_components()[0]
    schedule = scheduler(runtime)
    stop = asyncio.Event()
    # Explicit activation of the real loop only against these disposable roots.
    async with Worker(sdk, task_queue=transport.task_queue, workflows=[workflow], plugins=[plugin],
                      identity=runtime.exops.worker_identity):
        task = asyncio.create_task(schedule.run(stop, enabled=True, interval_seconds=1))
        try:
            async with asyncio.timeout(30):
                while True:
                    records = [item for key, item in runtime.application.journal.read()["intents"].items()
                               if key.startswith("exops.work.")]
                    if records:
                        break
                    await asyncio.sleep(0.1)
            assert len(records) == 1
            stop.set()
            await task
            run_id = records[0]["run"]["work_run_id"]
            dispatched = await supervisor.tick()
            assert dispatched["outcomes"] == [{"work_run_id": run_id, "state": "accepted"}], dispatched
            result = await asyncio.wait_for(sdk.get_workflow_handle("knowledgeos/" + run_id).result(), timeout=780)
            assert result["application_state"] == "recorded", result
        finally:
            stop.set()
            await task
        from support.metadata_fixture import SOURCE
        source_path = runtime.application.roots.vault / SOURCE
        original = source_path.read_bytes()
        policy_raw = runtime.policy.path.read_bytes()
        revoked_policy = json.loads(policy_raw)
        revoked_policy["daily_enabled"] = False
        runtime.policy.path.write_text(json.dumps(revoked_policy))
        revoked_effect = schedule.tick()
        assert any(item["code"] == "ACTION_DENIED" for item in revoked_effect["blockers"])
        assert not runtime.application.daily_stewardship().pending()
        assert source_path.read_bytes() == original
        runtime.policy.path.write_bytes(policy_raw)
        source_path.write_bytes(original + b"\nConcurrent synthetic source edit\n")
        stale_effect = schedule.consume_completed()
        assert stale_effect["consumed"] == [] and stale_effect["blockers"]
        assert not runtime.application.daily_stewardship().pending()
        source_path.write_bytes(original)
        consumed = schedule.tick()
        pending = runtime.application.daily_stewardship().pending()
        assert len(pending) == 1, consumed
        handle = sdk.get_workflow_handle("knowledgeos/" + run_id)
        history, description = await handle.fetch_history(), await handle.describe()
    activities = [event.activity_task_scheduled_event_attributes.activity_type.name for event in history.events
                  if event.HasField("activity_task_scheduled_event_attributes")]
    assert activities == [DAILY_GRAPH_NAME + "." + stage for stage in
                          ("manager", "coverage_evidence", "enrichment_candidate", "assess")], activities
    assert all(event.activity_task_scheduled_event_attributes.retry_policy.maximum_attempts == 1
               for event in history.events if event.HasField("activity_task_scheduled_event_attributes"))
    return run_id, pending[0], {"workflow_id": description.id, "temporal_run_id": description.run_id,
                                "activities": activities, "events": len(history.events), "supervisor": dispatched,
                                "pre_effect_revoked": revoked_effect, "pre_effect_stale": stale_effect}


async def roundtrip(root, route):
    import metadata_work_smoke as metadata

    from support.metadata_fixture import SOURCE
    from vaultops.adapters.core import digest
    from vaultops.application.metadata_work import DAILY_METHOD_ID
    from vaultops.application.owner_runtime import OwnerRuntimePolicy, WritableOwnerRuntime
    from vaultops.note_engine import parse_frontmatter

    # The real rendezvous is attached to the fresh application after composition;
    # native model messages still traverse the unchanged installed host adapter.
    runner = metadata.MetadataRendezvous(route, None)
    runtime, canary, policy_path = fixture(root, route, runner)
    runner.app = runtime.application
    before = (runtime.application.roots.vault / SOURCE).read_bytes()
    initial_due = runtime.application.daily_stewardship().due_check()
    assert len(initial_due["work_requests"]) == 1, initial_due
    denied_paths = [path for path in runtime.application.roots.vault.rglob("*.md")
                    if path != runtime.application.roots.vault / SOURCE
                    and "ai_policy: deny" in path.read_text()]
    assert denied_paths
    # A held, policy-incomplete note is created after validated Owner composition;
    # due scans must report it without ever admitting it to the Team.
    held = runtime.application.roots.vault / "40_Knowledge/Notes/Unclassified.md"
    held.write_text("---\ntype: knowledge\ntitle: Unclassified\n---\n# Held\n")
    due = runtime.application.daily_stewardship().due_check()
    assert "40_Knowledge/Notes/Unclassified.md" in due["held_paths"]
    # Remove only this malformed synthetic probe before native source validation.
    held.unlink()
    from vaultops.projection import generate_projection
    report, code = generate_projection(runtime.application.roots)
    assert code == 0, report
    run_id, effect, history = await execute(runtime, runner)
    provisional = parse_frontmatter((runtime.application.roots.vault / SOURCE).read_text()).properties
    assert provisional["tags"] == [canary, "ai/pending"]
    assert len(runner.responses) == 2
    candidate = json.loads(runner.responses[-1]["tool_calls"][-1]["arguments"]["content"])
    assert candidate["fields"] == {"tags": [canary]}
    record = runtime.application.journal.read()["intents"]["exops.work." + run_id]
    assert record["assessment"]["completion_state"] == "complete"
    assert record["assessment"]["assessor_id"] == record["spec"]["eval_officer_executor_id"]

    def review(call):
        status, body = call("daily/read", {"effect_id": effect["effect_id"]})
        assert status == 200 and body["review_status"] == "pending", body
        status, body = call("daily/decide", {"effect_id": effect["effect_id"], "decision": "reject"})
        assert status == 200, body
        return {"read_status": 200, "decision_status": status, "decision": "reject"}
    http = await asyncio.to_thread(metadata.owner_http, runtime.exops, review)
    final = parse_frontmatter((runtime.application.roots.vault / SOURCE).read_text())
    assert final.properties["tags"] == []
    assert final.body == parse_frontmatter(before.decode()).body
    # Rebuild only the explicitly disposable projection for startup's exact
    # source-verification prerequisite; reconstruction never rebuilds it itself.
    report, code = generate_projection(runtime.application.roots)
    assert code == 0, report
    reconstructed = WritableOwnerRuntime(runtime.application.roots, policy=OwnerRuntimePolicy(policy_path),
                                          runners={DAILY_METHOD_ID: runner})
    again = scheduler(reconstructed).tick()
    assert again["admitted"] == 0 and again["due"] == 0, again
    works = [key for key in reconstructed.application.journal.read()["intents"] if key.startswith("exops.work.")]
    directors = [key for key in reconstructed.application.journal.read()["intents"] if key.startswith("owner.director.")]
    assert len(works) == len(directors) == 1
    stale = copy.deepcopy(initial_due["work_requests"][0])
    try:
        reconstructed.admit_daily_request(stale)
    except ValueError as error:
        stale_code = getattr(error, "code", "INVALID_ARGUMENT")
    else:
        raise AssertionError("Stale pre-provisional daily source was admitted")
    policy = json.loads(policy_path.read_text())
    policy["daily_enabled"] = False
    policy_path.write_text(json.dumps(policy))
    revoked = scheduler(reconstructed).tick()
    assert revoked["admitted"] == 0 and any(item["code"] == "ACTION_DENIED" for item in revoked["blockers"])
    # Deliberately discard one actual committed Director/Work admission reply.
    # This is an unknown delivery, not a fake receipt or a native model effect.
    from vaultops.application.daily_scheduler import DailyAdmissionScheduler
    unknown_runner = metadata.MetadataRendezvous(route, None)
    uncertain, _canary, uncertain_policy = fixture(root / "unknown", route, unknown_runner)
    calls = []
    def discarded(request):
        calls.append(request["request_id"])
        uncertain.admit_daily_request(request)
        raise ConnectionError("Deliberately discarded committed owner admission response")
    unknown_schedule = DailyAdmissionScheduler(uncertain.application, admit_request=discarded,
                                                authorize=scheduler(uncertain).authorize)
    unknown_first = unknown_schedule.tick()
    restored = WritableOwnerRuntime(uncertain.application.roots, policy=OwnerRuntimePolicy(uncertain_policy),
                                     runners={DAILY_METHOD_ID: unknown_runner})
    unknown_again = scheduler(restored).tick()
    unknown_works = [key for key in restored.application.journal.read()["intents"] if key.startswith("exops.work.")]
    assert len(calls) == len(unknown_works) == 1
    assert unknown_first["admitted"] == unknown_again["admitted"] == 0
    assert any(item["code"] == "OUTCOME_UNKNOWN" for item in unknown_again["blockers"])
    return {"status": "PASS", "scope": "disposable native daily scheduler admission and exact rejection",
            "history": history, "candidate": candidate, "owner_http": http, "work_count": len(works),
            "director_count": len(directors), "reconstructed_tick": again, "stale_code": stale_code,
            "revocation": revoked, "unknown_reconstruction": {"tick": unknown_again,
                "durable_work_count": len(unknown_works), "admission_calls": len(calls), "native_dispatch_performed": False},
            "held_paths": due["held_paths"], "noneligible_count": len(denied_paths),
            "tool_denials": runner.denials, "source_before_digest": digest(before), "installed_modules": {},
            "excluded": ["Hermes Cron activation", "production corpus", "GUI", "model-based assessment"]}


async def native_roundtrip(root, route):
    from vaultops.adapters.core import digest
    report = await roundtrip(root, route)
    for relative in ("application/daily_scheduler.py", "application/daily_stewardship.py", "application/owner_runtime.py",
                     "application/metadata_work.py", "application/metadata_temporal.py", "application/metadata_workflow.py",
                     "application/native_team.py", "application/native_team_temporal.py", "application/exops.py",
                     "application/temporal_work.py", "application/temporal_contract.py"):
        installed = Path("/opt/knowledgeos/ops/src/vaultops", relative).read_bytes()
        assert installed == Path("/workspace/control/ops/src/vaultops", relative).read_bytes(), relative
        report["installed_modules"][relative] = digest(installed)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("host", "run"))
    parser.add_argument("--evidence", type=Path)
    parser.add_argument("--runner-binding", type=json.loads)
    args = parser.parse_args()
    if args.action == "host":
        import native_team_smoke_host as harness
        if args.runner_binding is not None:
            parser.error("Host binds exact native adapter")
        path = args.evidence or Path(__file__).resolve().parents[5] / "Tmp/knowledgeos-daily-scheduler" / (uuid4().hex + ".json")
        harness.host(path, smoke_name="daily_scheduler_smoke.py", adapter_name="native_metadata.py", marker=MARKER,
                     stages=("coverage_evidence", "enrichment_candidate"))
    else:
        if args.runner_binding is None or args.evidence is not None:
            parser.error("Container needs only runner binding")
        os.umask(0o077)
        with tempfile.TemporaryDirectory(prefix="knowledgeos-daily-scheduler-") as directory:
            report = asyncio.run(native_roundtrip(Path(directory), args.runner_binding))
        report["disposable_roots_removed"] = not Path(directory).exists()
        assert report["disposable_roots_removed"]
        print(MARKER + json.dumps(report, ensure_ascii=False, separators=(",", ":")))


if __name__ == "__main__":
    main()

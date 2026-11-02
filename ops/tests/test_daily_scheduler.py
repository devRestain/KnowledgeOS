"""Consumer-visible daily admission restart, bounded scan and refusal boundaries.

No native model or Worker is simulated: these tests stop at real owner admission.
Actual native execution is the separate disposable daily_scheduler_smoke harness.
"""
from __future__ import annotations

import asyncio
import copy
import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from support.daily_scheduler_smoke import fixture, scheduler
from support.metadata_fixture import SOURCE

from vaultops.adapters.core import AdmissionError
from vaultops.application.daily_scheduler import DailyAdmissionScheduler
from vaultops.application.metadata_work import DAILY_METHOD_ID
from vaultops.application.owner_runtime import OwnerRuntimePolicy, WritableOwnerRuntime
from vaultops.note_engine import parse_frontmatter, render_frontmatter
from vaultops.projection import generate_projection

ROUTE = {"runtime_kind": "hermes_native", "hermes_version": "0.22.5",
         "hermes_revision": "bf0203a794a07aac3ff764972ab9087baa4e4f4e",
         "provider": "openai-codex", "model": "gpt-6.1-terra", "adapter_digest": "sha256:" + "1" * 64}


class UnusedRunner:
    binding = ROUTE

    async def __call__(self, _request):
        raise AssertionError("Admission regression must not call a model")


def build(tmp_path):
    runner = UnusedRunner()
    runtime, _canary, policy_path = fixture(tmp_path, ROUTE, runner)
    return runtime, policy_path, runner


def reconstruct(runtime, policy_path, runner):
    return WritableOwnerRuntime(runtime.application.roots, policy=OwnerRuntimePolicy(policy_path),
                                runners={DAILY_METHOD_ID: runner})


def works(runtime):
    return [item for key, item in runtime.application.journal.read()["intents"].items()
            if key.startswith("exops.work.")]


def test_duplicate_and_local_day_rollover_keep_one_durable_work(tmp_path):
    runtime, policy_path, runner = build(tmp_path)
    current = [datetime(2026, 10, 31, 14, 59, tzinfo=UTC)]
    app = replace(runtime.application, clock=lambda: current[0])
    first_schedule = DailyAdmissionScheduler(app, admit_request=runtime.admit_daily_request,
                                              authorize=scheduler(runtime).authorize, limit=1)
    original = (app.roots.vault / SOURCE).read_bytes()
    first = first_schedule.tick()
    assert first["day"] == "2026-10-31" and first["admitted"] == 1 and not first["blockers"]
    current[0] += timedelta(minutes=2)
    again = first_schedule.tick()
    assert again["day"] == "2026-11-01" and again["admitted"] == 0 and again["replayed"] == 1
    restored = reconstruct(runtime, policy_path, runner)
    replay = scheduler(restored).tick()
    assert replay["admitted"] == 0 and replay["replayed"] == 1
    assert len(works(restored)) == 1
    assert (app.roots.vault / SOURCE).read_bytes() == original


@pytest.mark.parametrize("phase", ["scan", "director", "work"])
def test_process_loss_reconstructs_exact_director_and_work_without_duplicate(tmp_path, monkeypatch, phase):
    runtime, policy_path, runner = build(tmp_path)
    schedule = scheduler(runtime)
    # A due scan is durable even if no scheduler admission was attempted.
    due = runtime.application.daily_stewardship().due_check()
    assert len(due["work_requests"]) == 1
    if phase == "director":
        def crash(*_args, **_kwargs):
            raise SystemExit("Lost process after Director receipt before ExOps Work")
        with monkeypatch.context() as patch:
            patch.setattr(runtime.exops, "submit_work", crash)
            with pytest.raises(SystemExit):
                schedule.tick()
        assert len(works(runtime)) == 0
    elif phase == "work":
        def crash(request):
            runtime.admit_daily_request(request)
            raise SystemExit("Lost process after durable Work before scheduler receipt")
        interrupted = DailyAdmissionScheduler(runtime.application, admit_request=crash, authorize=schedule.authorize)
        with pytest.raises(SystemExit):
            interrupted.tick()
        assert len(works(runtime)) == 1
    restored = reconstruct(runtime, policy_path, runner)
    first = scheduler(restored).tick()
    assert first["admitted"] + first["replayed"] == 1 and not first["blockers"], first
    assert scheduler(restored).tick()["admitted"] == 0
    assert len(works(restored)) == 1
    intents = restored.application.journal.read()["intents"]
    assert len([key for key in intents if key.startswith("owner.director.")]) == 1
    assert works(restored)[0]["method_source_reference"] == due["work_requests"][0]["source_reference"]
    assert works(restored)[0]["spec"]["method_contract_ref"] == due["work_requests"][0]["method_contract_ref"]


def test_committed_work_replay_after_director_expiry_preserves_original_admission(tmp_path):
    runtime, policy_path, runner = build(tmp_path)
    current = [runtime.application.clock()]
    runtime.application = replace(runtime.application, clock=lambda: current[0])
    runtime.exops.application = runtime.application
    original_source = (runtime.application.roots.vault / SOURCE).read_bytes()
    admitted = []
    def crash_after_work(request):
        admitted.append(runtime.admit_daily_request(request))
        raise SystemExit("Lost process after committed Work before scheduler receipt")
    interrupted = DailyAdmissionScheduler(runtime.application, admit_request=crash_after_work,
                                           authorize=scheduler(runtime).authorize)
    with pytest.raises(SystemExit):
        interrupted.tick()
    assert len(admitted) == len(works(runtime)) == 1
    original_work = copy.deepcopy(works(runtime)[0])
    original_expiry = original_work["expires_at"]
    original_directors = {key: copy.deepcopy(item)
                          for key, item in runtime.application.journal.read()["intents"].items()
                          if key.startswith("owner.director.")}
    assert len(original_directors) == 1
    current[0] += timedelta(hours=2)
    assert current[0] > datetime.fromisoformat(original_expiry)
    restored = reconstruct(runtime, policy_path, runner)
    restored.application = replace(restored.application, clock=lambda: current[0])
    restored.exops.application = restored.application
    replay = scheduler(restored).tick()
    assert replay["admitted"] == 0 and replay["replayed"] == 1 and not replay["blockers"], replay
    receipt = replay["work_receipts"][0]
    assert receipt["replayed"] is True
    assert receipt["work_run_id"] == admitted[0]["work_run_id"]
    assert receipt["action_id"] == admitted[0]["action_id"]
    assert receipt["content_digest"] == admitted[0]["content_digest"]
    assert len(works(restored)) == 1 and works(restored)[0] == original_work
    assert works(restored)[0]["expires_at"] == original_expiry
    intents = restored.application.journal.read()["intents"]
    assert {key: item for key, item in intents.items() if key.startswith("owner.director.")} == original_directors
    requests = [item for key, item in intents.items() if key.startswith("daily.admission.")]
    assert len(requests) == 1 and requests[0]["state"] == "admitted"
    again = scheduler(restored).tick()
    assert again["admitted"] == 0 and not again["blockers"]
    assert len(works(restored)) == 1 and works(restored)[0] == original_work
    assert (restored.application.roots.vault / SOURCE).read_bytes() == original_source


def test_discarded_admission_reply_is_unknown_and_never_retried(tmp_path):
    runtime, policy_path, runner = build(tmp_path)
    calls = []
    def discard(request):
        calls.append(copy.deepcopy(request))
        runtime.admit_daily_request(request)
        raise ConnectionError("Committed admission reply was lost")
    schedule = DailyAdmissionScheduler(runtime.application, admit_request=discard, authorize=scheduler(runtime).authorize)
    first = schedule.tick()
    assert first["admitted"] == 0 and first["blockers"]
    restored = reconstruct(runtime, policy_path, runner)
    second = scheduler(restored).tick()
    assert second["admitted"] == 0 and any(item["code"] == "OUTCOME_UNKNOWN" for item in second["blockers"])
    assert len(calls) == len(works(restored)) == 1
    # A new source hash does not permit admission around unresolved ownership.
    path = runtime.application.roots.vault / SOURCE
    path.write_bytes(path.read_bytes() + b"\nNew source revision\n")
    third = schedule.tick()
    assert third["admitted"] == 0 and any(item["code"] == "OUTCOME_UNKNOWN" for item in third["blockers"])
    assert len(calls) == 1


@pytest.mark.parametrize("unknown_first", [False, True])
def test_partial_batch_progresses_past_admitted_or_unknown_first_source(tmp_path, unknown_first):
    runtime, policy_path, runner = build(tmp_path)
    source = runtime.application.roots.vault / SOURCE
    document = parse_frontmatter(source.read_text())
    second_path = "40_Knowledge/Notes/Second.md"
    properties = dict(document.properties)
    properties.update(id=str(uuid4()), title="Second")
    (runtime.application.roots.vault / second_path).write_text(render_frontmatter(properties, document.body))
    policy = json.loads(policy_path.read_text())
    policy["methods"][DAILY_METHOD_ID]["admission_scope"]["source_paths"].append(second_path)
    policy_path.write_text(json.dumps(policy))
    report, code = generate_projection(runtime.application.roots)
    assert code == 0, report
    runtime = reconstruct(runtime, policy_path, runner)
    def admission(request):
        receipt = runtime.admit_daily_request(request)
        if unknown_first:
            raise ConnectionError("Discarded exact first-source admission receipt")
        return receipt
    first = DailyAdmissionScheduler(runtime.application, admit_request=admission,
                                    authorize=scheduler(runtime).authorize, limit=1).tick()
    assert first["admitted"] == int(not unknown_first) and not first["scan_complete"]
    second = scheduler(runtime, limit=1).tick()
    assert second["admitted"] == 1
    third = scheduler(runtime, limit=1).tick()
    assert third["admitted"] == 0 and third["scan_complete"]
    assert third["replayed"] == 2 - int(unknown_first)
    assert len(works(runtime)) == 2


@pytest.mark.parametrize("drift", ["source", "policy", "method", "registry", "revoked"])
def test_due_to_admission_rechecks_current_authority_and_exact_pins(tmp_path, drift):
    runtime, policy_path, _runner = build(tmp_path)
    trusted = scheduler(runtime)
    changed = []
    def authorization():
        # Change authority at the admission boundary after the due scan,
        # without replacing the exact trusted Director callback.
        changed.append(True)
        if len(changed) == 4:
            if drift == "source":
                path = runtime.application.roots.vault / SOURCE
                path.write_bytes(path.read_bytes() + b"\nConcurrent user edit\n")
            elif drift == "revoked":
                policy = json.loads(policy_path.read_text())
                policy["daily_enabled"] = False
                policy_path.write_text(json.dumps(policy))
            elif drift == "policy":
                path = runtime.application.roots.control / "ops/policies/owner-control.json"
                policy = json.loads(path.read_text())
                policy["operator_actions"].remove("apply")
                path.write_text(json.dumps(policy))
            elif drift == "method":
                path = runtime.application.roots.control / "ops/config/work-methods.json"
                catalog = json.loads(path.read_text())
                for method in catalog["methods"]:
                    if method["method_id"] == DAILY_METHOD_ID:
                        method["method_version"] = "9.0.0"
                path.write_text(json.dumps(catalog))
            else:
                path = runtime.application.roots.control / "blueprint/blueprint.yaml"
                text = path.read_text()
                path.write_text(text.replace("allowed_object_types: [knowledge, question]",
                                             "allowed_object_types: [knowledge, question, idea]", 1))
        return trusted.authorize()
    schedule = DailyAdmissionScheduler(runtime.application, admit_request=runtime.admit_daily_request, authorize=authorization)
    result = schedule.tick()
    assert result["admitted"] == 0 and result["blockers"], result
    assert works(runtime) == []


def test_unknown_admission_response_remains_unknown_without_unsafe_retry(tmp_path):
    runtime, _policy_path, _runner = build(tmp_path)
    def unknown(_request):
        return {"receipt_state": "unknown"}
    schedule = DailyAdmissionScheduler(runtime.application, admit_request=unknown, authorize=scheduler(runtime).authorize)
    result = schedule.tick()
    assert result["admitted"] == 0 and result["blockers"][0]["code"] == "OUTCOME_UNKNOWN"
    assert works(runtime) == []
    again = schedule.tick()
    assert any(item["code"] == "OUTCOME_UNKNOWN" for item in again["blockers"])


def test_missing_admitted_work_is_unknown_not_permission_to_create_another(tmp_path):
    runtime, _policy_path, _runner = build(tmp_path)
    first = scheduler(runtime).tick()
    assert first["admitted"] == 1
    run_id = first["work_receipts"][0]["work_run_id"]
    with runtime.application.journal.writer() as writer:
        del writer.value["intents"]["exops.work." + run_id]
        writer.commit(runtime.application.clock())
    observed = scheduler(runtime).tick()
    assert observed["admitted"] == 0 and works(runtime) == []
    assert any(item["code"] == "OUTCOME_UNKNOWN" for item in observed["blockers"])


def test_explicit_loop_enable_clean_stop_and_constructor_are_write_free(tmp_path):
    runtime, _policy_path, _runner = build(tmp_path)
    before = runtime.application.journal.path.read_bytes()
    schedule = scheduler(runtime)
    assert runtime.application.journal.path.read_bytes() == before
    async def exercise():
        stop = asyncio.Event()
        with pytest.raises(AdmissionError):
            await schedule.run(stop)
        stop.set()
        await schedule.run(stop, enabled=True, interval_seconds=1)
    asyncio.run(exercise())
    assert runtime.application.journal.path.read_bytes() == before
    assert works(runtime) == []

"""C7 scheduled jobs through HTTP trigger receipts and declared child requests."""

from __future__ import annotations

import time
from datetime import datetime, timedelta

from knowledgeos.notes import parse
from knowledgeos.util import SEOUL


def run_job(kos, name):
    trigger = kos.run("job.run", {"job": name}, actor=f"schedule/{name}")
    assert trigger["status"] == "completed", trigger
    child = kos.wait(trigger["outcome"]["request_id"])
    deadline = time.monotonic() + 5
    while True:
        status, jobs = kos.get("/api/jobs")
        assert status == 200
        job = next(job for job in jobs if job["job"] == name)
        run = next(run for run in job["runs"] if run["run_id"] == trigger["outcome"]["run_id"])
        if run["status"] == child["status"]:
            return child, job
        assert time.monotonic() < deadline, job
        time.sleep(0.02)


def test_JOB_1_second_standardize_job_writes_nothing(kos, vault):
    paths = ["40_Knowledge/Ideas/One.md", "40_Knowledge/Ideas/Two.md"]
    for path in paths:
        vault.write(path, "Fresh idea.\n", age=timedelta(hours=1))
    first, job = run_job(kos, "standardize")
    assert first["status"] == "completed", first
    assert sorted(first["outcome"]["applied"]) == paths
    assert job["last_outcome"] == "completed"
    assert all(parse(path, vault.read(path)).type == "idea" for path in paths)
    before = vault.snapshot()
    changes = kos.changes()
    second, job = run_job(kos, "standardize")
    assert second["outcome"]["applied"] == []
    assert vault.snapshot() == before
    assert kos.changes() == changes
    assert len(job["runs"]) == 2
    assert kos.get("/api/notices")[1] == []


def test_job_pause_resume_and_actor_binding(kos, vault):
    path = "00_Inbox/Paused.md"
    vault.write(path, "Capture.\n", age=timedelta(hours=1))
    assert kos.run("job.pause", {"job": "standardize"})["outcome"]["paused"]
    paused = kos.run("job.run", {"job": "standardize"}, actor="schedule/standardize")
    assert paused["status"] == "refused" and "paused" in paused["notice"]
    assert vault.text(path) == "Capture.\n"
    assert kos.run("job.resume", {"job": "standardize"})["outcome"]["paused"] is False
    assert run_job(kos, "standardize")[0]["outcome"]["applied"] == [path]
    wrong = kos.run("job.run", {"job": "standardize"}, actor="schedule/digest")
    assert wrong["status"] == "refused" and "actor" in wrong["notice"]


def test_job_missed_run_becomes_attention(kos, clock):
    clock.advance(hours=3)
    status, items = kos.get("/api/attention")
    assert status == 200
    assert any(item["kind"] == "missed_job" and item["job"] == "standardize" for item in items)
    assert not any(item["kind"] == "missed_job" and item["job"] == "digest" for item in items)
    run_job(kos, "standardize")
    assert not any(item["kind"] == "missed_job" and item["job"] == "standardize" for item in kos.get("/api/attention")[1])


def test_job_single_worker_trigger_does_not_wait_for_its_child(make_kos, vault):
    kos = make_kos(workers=1)
    vault.write("00_Inbox/Single.md", "One worker.\n", age=timedelta(hours=1))
    child, _ = run_job(kos, "standardize")
    assert child["status"] == "completed"


def test_job_stable_trigger_id_never_submits_a_second_run(kos, vault):
    vault.write("00_Inbox/Once.md", "Once.\n", age=timedelta(hours=1))
    status, first = kos.submit("job.run", {"job": "standardize"}, actor="schedule/standardize", request_id="stable-job-trigger")
    assert status == 202
    first = kos.wait(first["request_id"])
    kos.wait(first["outcome"]["request_id"])
    status, repeated = kos.submit("job.run", {"job": "standardize"}, actor="schedule/standardize", request_id="stable-job-trigger")
    assert status == 200 and repeated == first
    job = next(j for j in kos.get("/api/jobs")[1] if j["job"] == "standardize")
    assert len(job["runs"]) == 1


# ---------------------------------------------------------------- Owner-internal schedule

START = datetime(2026, 11, 19, 9, 10, tzinfo=SEOUL)
SCHEDULED = {"C7": {"scheduler_enabled": True, "tick_seconds": 0.05}}


def job_state(kos, name):
    return next(job for job in kos.get("/api/jobs")[1] if job["job"] == name)


def runs(kos, name):
    return job_state(kos, name)["runs"]


def eventually(check, timeout=10.0):
    deadline = time.monotonic() + timeout
    while True:
        value = check()
        if value:
            return value
        assert time.monotonic() < deadline, "condition not reached"
        time.sleep(0.02)


def quiet(seconds=0.4):
    """Real time for the scheduler to act on the clock if it were going to."""
    time.sleep(seconds)


def pause(kos, *names):
    for name in names:
        assert kos.run("job.pause", {"job": name})["outcome"]["paused"] is True


def assert_scheduled_run(kos, run, name):
    trigger = kos.wait(run["trigger_request_id"])
    assert trigger["command"] == "job.run" and trigger["actor"] == f"schedule/{name}"
    assert trigger["status"] == "completed", trigger
    assert kos.wait(run["request_id"])["status"] == "completed"


def test_JOB_4_slots_missed_while_stopped_run_once_after_the_startup_delay(make_kos, clock):
    clock.set(START)
    first = make_kos(settings=SCHEDULED)
    pause(first, "embed")
    first.close()
    clock.set(START + timedelta(hours=3, minutes=30))  # 12:40: the 10:00, 11:00 and 12:00 slots passed
    kos = make_kos(settings=SCHEDULED)
    assert kos.owner.setting("C7", "startup_delay_minutes") == 3
    quiet()
    assert runs(kos, "standardize") == []
    clock.advance(minutes=2, seconds=59)
    quiet()
    assert runs(kos, "standardize") == []
    clock.advance(seconds=1)
    [run] = eventually(lambda: runs(kos, "standardize"))
    assert_scheduled_run(kos, run, "standardize")
    quiet()
    assert [item["run_id"] for item in runs(kos, "standardize")] == [run["run_id"]]
    assert all(runs(kos, name) == [] for name in ("embed", "curate", "digest"))
    clock.advance(minutes=30)  # 13:13: while the Owner runs, the next slot fires as scheduled
    eventually(lambda: len(runs(kos, "standardize")) == 2)


def test_JOB_4_job_fires_at_each_slot_while_the_owner_runs(make_kos, clock):
    clock.set(START)
    kos = make_kos(settings=SCHEDULED)
    pause(kos, "embed")
    clock.advance(minutes=49)  # 09:59
    quiet()
    assert runs(kos, "standardize") == []
    clock.advance(minutes=1)  # 10:00
    eventually(lambda: len(runs(kos, "standardize")) == 1)
    assert_scheduled_run(kos, runs(kos, "standardize")[0], "standardize")
    # Advancing the fake clock while the first run is active can consume the next slot.
    eventually(lambda: job_state(kos, "standardize")["last_outcome"] == "completed")
    quiet()
    assert len(runs(kos, "standardize")) == 1
    clock.advance(hours=1)  # 11:00
    eventually(lambda: len(runs(kos, "standardize")) == 2)
    for run in runs(kos, "standardize"):
        assert_scheduled_run(kos, run, "standardize")


def test_JOB_4_daily_job_fires_at_its_clock_time(make_kos, clock):
    clock.set(datetime(2026, 11, 19, 7, 50, tzinfo=SEOUL))
    kos = make_kos(settings=SCHEDULED)
    pause(kos, "embed", "standardize")
    clock.advance(minutes=9, seconds=59)  # 07:59:59
    quiet()
    assert runs(kos, "digest") == []
    clock.advance(seconds=1)  # 08:00
    [run] = eventually(lambda: runs(kos, "digest"))
    assert_scheduled_run(kos, run, "digest")
    quiet()
    assert len(runs(kos, "digest")) == 1


def test_JOB_6_paused_job_is_skipped_and_waits_for_the_next_slot_after_resume(make_kos, clock):
    clock.set(START)
    kos = make_kos(settings=SCHEDULED)
    pause(kos, "embed", "standardize")
    clock.advance(minutes=50)  # 10:00 passes while paused
    eventually(lambda: job_state(kos, "standardize").get("last_slot"))
    assert runs(kos, "standardize") == []
    assert not any(item["kind"] == "blocked_job" for item in kos.get("/api/attention")[1])
    assert kos.run("job.resume", {"job": "standardize"})["outcome"]["paused"] is False
    quiet()
    assert runs(kos, "standardize") == []  # the skipped slot is not caught up
    clock.advance(hours=1)  # 11:00
    eventually(lambda: len(runs(kos, "standardize")) == 1)


def test_JOB_5_a_slot_that_ran_never_runs_again_after_a_restart(make_kos, clock):
    clock.set(START)
    first = make_kos(settings=SCHEDULED)
    pause(first, "embed")
    clock.advance(minutes=50)  # 10:00
    [run] = eventually(lambda: runs(first, "standardize"))
    assert_scheduled_run(first, run, "standardize")
    first.close()
    clock.advance(minutes=20)  # 10:20, still the 10:00 slot
    kos = make_kos(settings=SCHEDULED)
    clock.advance(minutes=4)  # past the startup delay
    quiet()
    assert [item["run_id"] for item in runs(kos, "standardize")] == [run["run_id"]]


def test_JOB_5_slot_trigger_request_id_is_stable(make_kos, clock):
    clock.set(START)
    kos = make_kos(settings=SCHEDULED)
    pause(kos, "embed")
    # A trigger already committed for the 10:00 slot is the slot's run; the schedule never adds a second.
    status, receipt = kos.submit("job.run", {"job": "standardize"}, actor="schedule/standardize",
                                 request_id="schedule-standardize-20261119T1000")
    assert status == 202 and kos.wait(receipt["request_id"])["status"] == "completed"
    clock.advance(minutes=50)  # 10:00
    eventually(lambda: job_state(kos, "standardize").get("last_slot"))
    quiet()
    assert len(runs(kos, "standardize")) == 1


def test_JOB_6_scheduler_disabled_runs_nothing_even_after_the_slots_pass(make_kos, clock):
    clock.set(START)
    kos = make_kos(settings={"C7": {"scheduler_enabled": False}})
    clock.advance(days=1)
    quiet()
    assert all(job["runs"] == [] for job in kos.get("/api/jobs")[1])
    assert job_state(kos, "standardize")["missed"] is True


def test_JOB_7_embed_job_is_declared_hourly_and_offset_from_standardize(kos):
    jobs = {job["job"]: job for job in kos.get("/api/jobs")[1]}
    assert jobs["embed"]["schedule"] == "30 * * * *" and jobs["standardize"]["schedule"] == "0 * * * *"
    assert jobs["embed"]["command"] == "index.embed"
    _, declaration = kos.owner.catalog.command("index.embed")
    assert "schedule" in declaration["origins"]


def test_JOB_7_scheduler_settings_are_declared_with_production_defaults(kos):
    assert kos.owner.catalog.settings("C7") == {"scheduler_enabled": True, "startup_delay_minutes": 3, "tick_seconds": 30}

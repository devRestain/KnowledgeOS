"""Declared job triggers and the Owner-internal scheduler; child requests keep their own admission and receipts."""

from __future__ import annotations

import logging
import threading
from datetime import datetime, timedelta
from typing import Any

from ..capabilities import Done, Registry
from ..scheduler import Schedule
from ..util import digest, iso

log = logging.getLogger("knowledgeos.jobs")

CAPABILITY = "C7"
TERMINAL = {"completed", "unmet", "failed", "refused", "cancelled", "unknown"}


def register(reg: Registry) -> None:
    reg.command("job.run", run)
    reg.command("job.pause", pause)
    reg.command("job.resume", resume)
    reg.view("jobs", jobs_view)
    reg.on_start(start)


def _job(adm):
    job = adm.params["job"]
    declared = adm.owner.catalog.jobs().get(job)
    if declared is None:
        adm.refuse(f"unknown job {job}")
    return job, declared


def _pending(owner):
    return owner.cache("C7.pending", lambda: {
        "lock": threading.Lock(),
        "ids": {r["run_id"] for r in owner.store.list("job_runs") if r["status"] not in TERMINAL},
    })


def run(adm) -> Done:
    job, declaration = _job(adm)
    if adm.actor != f"schedule/{job}":
        adm.refuse("job actor must match schedule/<job>")
    owner = adm.owner
    run_id = "jobrun-" + digest({"job": job, "request": adm.request_id})[:32]
    child_id = "jobchild-" + digest({"job": job, "request": adm.request_id})[:32]
    with owner.store.lock(f"jobs/{job}"):
        state = owner.store.get("jobs", job) or {"job": job, "paused": False, "observed_at": iso(owner.clock.now())}
        if state["paused"]:
            from ..learning import on_job_blocked

            on_job_blocked(owner, job)
            adm.refuse(f"job {job} is paused")
        active = [r for r in owner.store.list("job_runs") if r["job"] == job and r["status"] not in TERMINAL and r["run_id"] != run_id]
        if active:
            adm.refuse(f"job {job} already has an active run")
        entry = {"run_id": run_id, "job": job, "trigger_request_id": adm.request_id, "request_id": child_id,
                 "started_at": iso(owner.clock.now()), "status": "received", "outcome": None, "work_id": None}
        if owner.store.create("job_runs", run_id, entry):
            owner.store.append("job_runs", {**entry, "event": "started"})
        pending = _pending(owner)
        with pending["lock"]:
            pending["ids"].add(run_id)
        state.update(last_run=entry["started_at"], last_run_id=run_id, last_outcome="running")
        owner.store.put("jobs", job, state)
        receipt, _ = owner.submit({"request_id": child_id, "command": declaration["command"], "params": declaration.get("params", {})}, actor=f"schedule/{job}")
    owner.cache("C7.wake", threading.Event).set()
    return Done(outcome={"job": job, "run_id": run_id, "request_id": child_id, "execution_status": receipt["status"]},
                notice=f"Job {job} triggered; observe child request {child_id}")


def _paused(adm, value):
    job, _ = _job(adm)
    with adm.owner.store.lock(f"jobs/{job}"):
        state = adm.owner.store.get("jobs", job) or {"job": job, "observed_at": iso(adm.owner.clock.now())}
        if state.get("paused", False) != value:
            action = adm.owner.record_action(None, adm.request_id, adm.command, "received")
            state.update(paused=value, changed_at=iso(adm.owner.clock.now()))
            adm.owner.store.put("jobs", job, state)
            adm.owner.record_action(None, adm.request_id, adm.command, "applied", action)
    return Done(outcome={"job": job, "paused": value})


def pause(adm):
    return _paused(adm, True)


def resume(adm):
    return _paused(adm, False)


def settle_runs(owner) -> None:
    pending = _pending(owner)
    with pending["lock"]:
        ids = list(pending["ids"])
    for run_id in ids:
        entry = owner.store.get("job_runs", run_id)
        if entry is None or entry["status"] in TERMINAL:
            with pending["lock"]:
                pending["ids"].discard(run_id)
            continue
        receipt = owner.get_request(entry["request_id"])
        if not receipt or receipt["status"] not in TERMINAL:
            continue
        with owner.store.lock(f"jobs/{entry['job']}"):
            entry.update(status=receipt["status"], outcome=receipt["outcome"], work_id=receipt["work_id"], ended_at=iso(owner.clock.now()))
            owner.store.put("job_runs", entry["run_id"], entry)
            owner.store.append("job_runs", {**entry, "event": "settled"})
            state = owner.store.get("jobs", entry["job"]) or {"job": entry["job"], "paused": False}
            if state.get("last_run_id") == entry["run_id"]:
                state.update(last_outcome=entry["status"], last_result=receipt["outcome"], last_finished=entry["ended_at"])
                owner.store.put("jobs", entry["job"], state)
        with pending["lock"]:
            pending["ids"].discard(run_id)


def _observe(owner, job: str) -> dict[str, Any]:
    """First sight of a declared job: slots before now count as passed, not missed."""
    state = {"job": job, "paused": False, "observed_at": iso(owner.clock.now()), "last_run": None, "last_outcome": None}
    owner.store.create("jobs", job, state)
    return owner.store.get("jobs", job) or state


def start(owner) -> None:
    for declared in owner.catalog.jobs().values():
        Schedule(declared["schedule"])  # an unsupported schedule stops the Owner at start
    for job in owner.catalog.jobs():
        _observe(owner, job)
    stop = threading.Event()
    wake = owner.cache("C7.wake", threading.Event)

    def watch():
        while not stop.is_set():
            try:
                settle_runs(owner)
            except Exception:
                log.exception("settling job runs failed")
            wake.wait(0.1)
            wake.clear()
        settle_runs(owner)

    thread = threading.Thread(target=watch, name="kos-jobs", daemon=True)
    thread.start()

    def close():
        stop.set()
        wake.set()
        thread.join()

    owner.on_close(close)
    if owner.setting("C7", "scheduler_enabled", True):
        owner.on_quiesce(Scheduler(owner).start())


def _seen(state: dict[str, Any]) -> datetime | None:
    """When the job last ran or had a slot consumed, else when it was first observed."""
    stamps = [datetime.fromisoformat(state[key]) for key in ("last_run", "last_slot", "observed_at") if state.get(key)]
    return max(stamps) if stamps else None


class Scheduler:
    """Fires declared jobs while the Owner runs: one thread sleeping until the next slot, capped
    at ``tick_seconds`` so the Owner clock is re-read. Nothing runs before ``startup_delay_minutes``
    after start; then every job whose latest slot is later than its last run or consumed slot
    runs once, which covers slots that passed while the Owner was off. Each slot has one trigger
    request ID, so a slot is never triggered twice, not even across a restart."""

    def __init__(self, owner):
        delay = float(owner.setting("C7", "startup_delay_minutes", 3))
        self.tick = float(owner.setting("C7", "tick_seconds", 30))
        if delay < 0 or self.tick <= 0:
            raise ValueError("C7 startup_delay_minutes must not be negative and tick_seconds must be positive")
        self.owner = owner
        self.ready_at = owner.clock.now() + timedelta(minutes=delay)
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, name="kos-scheduler", daemon=True)

    def start(self):
        """Start the thread; returns the callback that stops and joins it."""
        self._thread.start()
        return self.stop

    def stop(self) -> None:
        self._stop.set()
        self._thread.join()

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                wait = self._pass()
            except Exception:
                log.exception("job scheduling pass failed")
                wait = self.tick
            self._stop.wait(wait)

    def _pass(self) -> float:
        """Trigger what is due; return the real seconds until the next look at the clock."""
        owner = self.owner
        now = owner.clock.now()
        if now < self.ready_at:
            return min(self.tick, max((self.ready_at - now).total_seconds(), 0.01))
        upcoming = []
        for job, declaration in sorted(owner.catalog.jobs().items()):
            schedule = Schedule(declaration["schedule"])
            seen = _seen(owner.store.get("jobs", job) or _observe(owner, job))
            slot = schedule.latest(now)
            if seen is None or slot > seen:
                self._trigger(job, slot)
            upcoming.append(schedule.next(now))
        soonest = min(upcoming, default=None)
        return self.tick if soonest is None else min(self.tick, max((soonest - now).total_seconds(), 0.01))

    def _trigger(self, job: str, slot: datetime) -> None:
        """Submit the slot's ``job.run`` and consume the slot. A paused job's slot is consumed
        without a request, so resuming waits for the next slot."""
        from ..owner import BadRequest, Conflict

        owner = self.owner
        with owner.store.lock(f"jobs/{job}"):
            paused = (owner.store.get("jobs", job) or {}).get("paused", False)
        if not paused:
            request = {"request_id": f"schedule-{job}-{slot:%Y%m%dT%H%M}", "command": "job.run", "params": {"job": job}}
            try:
                owner.submit(request, actor=f"schedule/{job}")
            except Conflict:
                pass  # the slot's request exists with other content: it was already triggered
            except BadRequest as error:
                log.error("job %s slot %s was not triggered: %s", job, slot.isoformat(), error)
        with owner.store.lock(f"jobs/{job}"):
            owner.store.update("jobs", job, lambda state: state.update(last_slot=iso(slot)))


def jobs_view(owner, query=None) -> list[dict[str, Any]]:
    runs = owner.store.list("job_runs")
    now = owner.clock.now()
    result = []
    for job, declaration in sorted(owner.catalog.jobs().items()):
        state = owner.store.get("jobs", job) or {}
        schedule = Schedule(declaration["schedule"])
        latest = schedule.latest(now)
        seen = _seen(state)
        # Two slots passed without a run: the Owner was off or the scheduler is not running.
        missed = bool(not state.get("paused") and seen and schedule.latest(latest - timedelta(minutes=1)) > seen)
        result.append({"job": job, **declaration, **state, "missed": missed,
                       "runs": sorted((r for r in runs if r["job"] == job), key=lambda r: (r["started_at"], r["run_id"]), reverse=True)})
    return result

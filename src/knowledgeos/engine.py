"""One business engine; Temporal owns scheduling and the registered LangGraph owns each segment."""

from __future__ import annotations

import importlib
import logging
import re
import threading
from collections.abc import Callable
from copy import deepcopy
from dataclasses import asdict
from typing import TYPE_CHECKING, Any

from . import roles
from .capabilities import Done, Refused
from .evidence import Evidence
from .notes import Note, parse
from .roles import AuthorityViolation, RoleOutcomeUnknown, RoleSession, RoleUnavailable
from operationkit.canonical import canonical, digest

from .util import iso
from .vault import PreimageMismatch

if TYPE_CHECKING:
    from .owner import Owner

log = logging.getLogger("knowledgeos.engine")

TERMINAL_WORK = ("completed", "unmet", "failed", "cancelled", "unknown")
TEAM_SEGMENTS = 2
TEAM_PRODUCERS = 6
SAME_GAP_REPEATS = 1
MAX_PROPOSALS = 3
EVAL_ROLE = "eval_officer"
# Graph fields that would address the EvalOfficer (Blueprint §4.3): an authority violation.
EVAL_FIELDS = {"instruction", "instructions", "input", "inputs", "route", "model_route", "prompt", "criteria", "position"}
TEXT_LIMIT = 600
LIST_LIMIT = 50


def bounded(value: Any) -> Any:
    """A size-bounded copy for role views: long strings cut, long lists and maps truncated."""
    if isinstance(value, str):
        return value if len(value) <= TEXT_LIMIT else value[:TEXT_LIMIT] + "…"
    if isinstance(value, list):
        return [bounded(item) for item in value[:LIST_LIMIT]]
    if isinstance(value, dict):
        return {str(key): bounded(item) for key, item in list(value.items())[:LIST_LIMIT]}
    return value


def rejected(session: RoleSession) -> str:
    """Why a session ended without a valid submission."""
    return "; ".join(session.diagnostics or ["no submission was made"])


class StepContext:
    """What a static step or check may use: the Work, its snapshots and read-only Owner services."""

    def __init__(self, owner: Owner, work: dict[str, Any]):
        self.owner = owner
        self.work = work
        self.work_id: str = work["work_id"]
        self.workspec: dict[str, Any] = work["workspec"]
        self.params: dict[str, Any] = self.workspec["params"]
        self.mode: str = self.workspec["mode"]
        self.capability: str = self.workspec["capability"]
        self.clock = owner.clock
        self.memo: dict[str, Any] = {}  # scratch space shared by the checks of one evaluation

    def inputs(self) -> list[dict[str, Any]]:
        """Snapshotted inputs ``{path, sha, blob, note_id, policy, title}`` in admission order."""
        return list(self.workspec["inputs"])

    def snapshot(self, path: str) -> Note:
        """The exact bytes admitted for ``path``, parsed. Edits after admission are not seen."""
        for item in self.workspec["inputs"]:
            if item["path"] == path:
                data = self.owner.store.get_blob(item["blob"])
                if data is None:
                    raise LookupError(f"snapshot for {path} is missing from State")
                return parse(path, data)
        raise KeyError(f"{path} is not an input of {self.work_id}")

    @property
    def evidence(self) -> Evidence:
        """This Work's evidence handles (``knowledgeos.evidence``)."""
        return Evidence(self.owner, self.work_id)

    def put_blob(self, data: bytes) -> str:
        return self.owner.store.put_blob(data)

    def blob(self, sha: str) -> bytes:
        data = self.owner.store.get_blob(sha)
        if data is None:
            raise LookupError(f"blob {sha} missing from State")
        return data

    def setting(self, key: str, default: Any = None) -> Any:
        return self.owner.setting(self.capability, key, default)

    def first_seen(self, path: str) -> str:
        return self.owner.first_seen(path)


class EffectContext(StepContext):
    """Owner effects for one Work: every Vault write records intent first (Blueprint §6.5)."""

    def __init__(self, owner: Owner, work: dict[str, Any], request_id: str | None, actor: str | None):
        super().__init__(owner, work)
        self.request_id = request_id
        self.actor = actor
        self.record = owner.journal(self.work_id, request_id)

    def _guard(self) -> None:
        self.owner.engine.assert_current(self.work_id)
        self.owner.engine._check_cancel(self.work_id)
        if self.owner.get_work(self.work_id)["status"] not in ("running", "awaiting_confirmation"):
            raise Refused("Work is no longer admitted for effects")

    def _note_effect(self, op: str, path: str, state: str, **extra: Any) -> None:
        entry = {"op": op, "path": path, "state": state, "at": iso(self.clock.now()), **extra}
        self.owner.update_work(self.work_id, lambda work: work["effects"].append(entry))

    def replace(self, path: str, preimage: str, data: bytes) -> bool:
        """Exact-preimage atomic replace. False (nothing written) when the file changed."""
        self._guard()
        try:
            self.owner.vault.replace(path, preimage, data, self.record)
        except PreimageMismatch:
            self._note_effect("replace", path, "not_applied", preimage=preimage)
            return False
        self._note_effect("replace", path, "applied", preimage=preimage, result=self.owner.store.put_blob(data))
        return True

    def create(self, path: str, data: bytes, exact: bool = False) -> str | None:
        self._guard()
        try:
            final = self.owner.vault.create(path, data, self.record, exact=exact)
        except PreimageMismatch:
            self._note_effect("create", path, "not_applied")
            return None
        self._note_effect("create", final, "applied")
        return final

    def move(self, path: str, preimage: str, new_path: str, data: bytes) -> str | None:
        self._guard()
        try:
            final = self.owner.vault.move(path, preimage, new_path, data, self.record)
        except PreimageMismatch:
            self._note_effect("move", path, "not_applied", to=new_path)
            return None
        self._note_effect("move", path, "applied", to=final)
        return final

    def delete_own(self, path: str, preimage: str | None) -> bool:
        self._guard()
        try:
            self.owner.vault.delete_own(path, preimage, self.record)
        except PreimageMismatch:
            self._note_effect("delete", path, "not_applied")
            return False
        self._note_effect("delete", path, "applied")
        return True


class ActionEffects(EffectContext):
    """Vault effects of one admitted human action.

    The originating Work is already terminal, so the fence is the pending action's identity
    (still ``received`` under the same running request). Exact preimages stay in the Vault.
    """

    def __init__(self, owner: Owner, work: dict[str, Any], request_id: str | None, actor: str | None,
                 action_id: str):
        super().__init__(owner, work, request_id, actor)
        self.action_id = action_id

    def _guard(self) -> None:
        action = self.owner.store.get("actions", self.action_id)
        if not action or action.get("state") != "received" or action.get("request_id") != self.request_id:
            raise Refused("human action is no longer pending; no effect admitted")


class Cancelled(Exception):
    pass


class StepFailed(Exception):
    """A role step failed visibly (route unavailable, runner error). Never retried or rerouted."""

    def __init__(self, role: str, reason: str, invocation: dict[str, Any] | None = None):
        super().__init__(f"{role}: {reason}")
        self.role = role
        self.reason = reason
        self.invocation = invocation


class StepUnknown(StepFailed):
    """A role invocation whose outcome cannot be observed. Never re-run."""



class Engine:
    def __init__(self, owner: Owner):
        self.owner = owner
        self._cancel: set[str] = set()
        self._locks: dict[str, threading.Lock] = {}
        self._guard = threading.Lock()
        self.runtime = None

    def start(self) -> None:
        if self.runtime is None:
            from .temporal_runtime import TemporalRuntime

            self.runtime = TemporalRuntime(self)

    def close(self) -> None:
        if self.runtime is not None:
            self.runtime.close()

    def prepare_work(self, work_id: str) -> None:
        """Bind the admitted Work before releasing the Owner's admission/shutdown lock."""
        if self.runtime is None:
            raise RuntimeError("Temporal runtime has not been started")
        self.runtime.pin(work_id)

    def prepare_shutdown(self) -> None:
        if self.runtime is not None:
            self.runtime.prepare_shutdown()


    def assert_current(self, work_id: str) -> None:
        work = self.owner.get_work(work_id)
        pin = (work or {}).get("runtime") or {}
        if pin.get("generation") != self.runtime.generations.get(work_id):
            raise RuntimeError("stale Owner worker generation; no effect or commit admitted")
        self.runtime.assert_activity_current()

    def _work_lock(self, work_id: str) -> threading.Lock:
        with self._guard:
            return self._locks.setdefault(work_id, threading.Lock())

    # ------------------------------------------------------------ records

    def _now(self) -> str:
        return iso(self.owner.clock.now())

    def _set_status(self, work_id: str, status: str, **fields: Any) -> dict[str, Any]:
        now = self._now()
        previous = None

        def change(work: dict[str, Any]) -> None:
            nonlocal previous
            previous = work["status"]
            work["status"] = status
            work["updated_at"] = now
            work.update(fields)

        work = self.owner.update_work(work_id, change)
        if previous != status:
            from .learning import on_work_status

            on_work_status(self.owner, work)
        return work

    def _open_segment(self, work_id: str, steps: list[str], composed_by: str, **extra: Any) -> int:
        holder: dict[str, int] = {}
        topology = deepcopy(self.runtime.topologies[tuple(steps)])

        def change(work: dict[str, Any]) -> None:
            holder["index"] = len(work["segments"])
            segment = {
                "index": holder["index"],
                "graph": {"steps": steps, "evaluation": "after_last"},
                "composed_by": composed_by,
                "steps": [],
                "checkpoint": None,
                "evaluation": None,
                "assessment": None,
                "runtime_topology": topology,
                "runtime_generation": work["runtime"]["generation"],
            }
            segment.update(extra)
            work["segments"].append(segment)

        self.owner.update_work(work_id, change)
        return holder["index"]

    def _open_team_segment(self, work_id: str, team: dict[str, Any], steps: list[str]) -> int:
        """Open a segment with the Owner's Graph, assignment and lineage record."""
        work = self.owner.get_work(work_id) or {}
        spec = work["workspec"]
        index = len(work["segments"])
        remaining = work["remaining_work"] if index else None
        graph: dict[str, Any] = {
            "graph_id": "knowledgeos." + re.sub(r"[^a-z0-9]+", "_", team["name"].lower()).strip("_"),
            "graph_revision": index + 1,
            "steps": steps,
            "evaluation": "after_last",
            "node_ids": [*steps, "evaluation"],
            "start_node_id": steps[0],
        }
        graph["graph_digest"] = digest(graph)
        pin = spec.get("evaluation") or {}
        assignments = [
            {"node_id": step, "role_id": step, "runtime": team["pool"][step]["runtime"],
             "selection_authority": "bounded_manager"}
            for step in steps
        ]
        assignments.append(
            {"node_id": "evaluation", "role_id": pin.get("role", EVAL_ROLE), "runtime": pin.get("runtime", "model"),
             "selection_authority": "owner_officer", "pin": pin}
        )
        lineage: dict[str, Any] = {
            "segment_index": index + 1,
            "trigger_kind": "remaining_work" if remaining else "initial",
            "target_criterion_ids": remaining["criteria"] if remaining else [c["id"] for c in spec["criteria"]],
            "work_spec_digest": digest(spec),
            "graph_digest": graph["graph_digest"],
        }
        lineage["segment_digest"] = digest(lineage)
        return self._open_segment(work_id, steps, "manager", graph=graph, assignments=assignments, segment=lineage)

    def _record_step(self, work_id: str, segment: int, step: dict[str, Any]) -> None:
        self.assert_current(work_id)
        def change(work: dict[str, Any]) -> None:
            steps = work["segments"][segment]["steps"]
            for index, existing in enumerate(steps):
                if existing["step_id"] == step["step_id"]:
                    steps[index] = step
                    return
            steps.append(step)

        self.owner.update_work(work_id, change)

    def _check_cancel(self, work_id: str) -> None:
        work = self.owner.get_work(work_id) or {}
        if work_id in self._cancel or (work.get("runtime") or {}).get("cancel_requested"):
            raise Cancelled(work_id)

    # ------------------------------------------------------------ entry points

    def run(self, work_id: str, request_id: str | None = None, actor: str | None = None) -> Done:
        """Start or reattach the sole durable Workflow, never an in-process fallback."""
        if self.runtime is None:
            raise RuntimeError("Temporal runtime has not been started")
        return self.runtime.run(work_id, request_id, actor)

    def resume(
        self,
        work_id: str,
        action: str,
        fn: Callable[[EffectContext, Any], Done],
        request_id: str | None,
        actor: str | None,
    ) -> Done:
        """Apply a human action (confirm, cancel, ...) to a Work awaiting confirmation.

        The decision is recorded as a HumanActionResponse before the effect.
        """
        if self.runtime is None:
            raise RuntimeError("Temporal runtime has not been started")
        existing = self.reattach_action(request_id)
        if existing is not None:
            return existing
        with self._work_lock(work_id):
            work = self.owner.get_work(work_id)
            if work is None:
                raise Refused(f"no Work {work_id}")
            if work["status"] != "awaiting_confirmation":
                raise Refused(f"Work {work_id} is {work['status']}, not awaiting confirmation")
            if "<locals>" in fn.__qualname__ or not fn.__module__.startswith("knowledgeos.capabilities."):
                raise ValueError("human action handler must be a declared module-scope capability function")
            action_id = self.owner.record_action(work_id, request_id, action, "received")
            payload = {**self.runtime.payload(work_id, request_id, actor), "action": action,
                       "action_id": action_id, "handler": fn.__module__ + ":" + fn.__qualname__}
            self.owner.store.update("actions", action_id, lambda row: row.update(runtime_payload=payload))
            if request_id:
                self.owner._update_receipt(request_id, status="admitted", work_id=work_id)
        return self.runtime.action(payload)

    def reattach_action(self, request_id: str | None) -> Done | None:
        if request_id is None:
            return None
        for action in self.owner.store.list("actions"):
            payload = action.get("runtime_payload")
            if action.get("request_id") == request_id and payload:
                self.runtime.pin(payload["work_id"])
                return self.runtime.action(payload)
        return None

    def cancel(self, work_id: str) -> str:
        """Request cancellation. Returns the resulting Work status (or ``cancelling``)."""
        work = self.owner.get_work(work_id)
        if work is None:
            raise Refused(f"no Work {work_id}")
        if work["status"] in TERMINAL_WORK:
            raise Refused(f"Work {work_id} is already {work['status']}")
        if work["status"] == "awaiting_confirmation":
            with self._work_lock(work_id):
                self._set_status(work_id, "cancelled", notice="cancelled; nothing written")
            return "cancelled"
        self.runtime.pin(work_id)
        self._cancel.add(work_id)
        self.owner.update_work(work_id, lambda record: record["runtime"].update(cancel_requested=True))
        try:
            self.runtime.cancel(work_id)
        except Exception:  # noqa: BLE001 - SDK boundary; durable cancellation remains queued.
            # Durable admission survives SDK/server downtime; the next Activity observes it.
            log.warning("cancel signal delivery pending for %s", work_id)
        return "cancelling"

    # ------------------------------------------------------------ Officer flow


    def _done(self, work_id: str, done: Done) -> dict[str, Any]:
        done.work_id = work_id
        self._set_status(work_id, done.work_status or done.status, outcome=done.outcome, notice=done.notice)
        self._cancel.discard(work_id)
        return asdict(done)

    def _phase_failure(self, payload: dict[str, Any], error: Exception) -> dict[str, Any]:
        work_id, operation = payload["work_id"], payload["operation"]
        if isinstance(error, Cancelled):
            done = Done(status="cancelled", notice="Work cancelled at a safe Activity boundary")
        elif isinstance(error, StepUnknown):
            self.owner.add_unknown("role_invocation", {"work_id": work_id,
                "request_id": payload.get("request_id"), "role": error.role})
            done = Done(status="unknown", notice=f"{error.role} outcome unknown ({error.reason}); not re-run")
        elif operation in ("commit", "action"):
            kind = "action" if operation == "action" else "runtime_phase"
            self.owner.add_unknown(kind, {"work_id": work_id, "phase": payload["phase_id"],
                "action_id": payload.get("action_id"), "request_id": payload.get("request_id")})
            done = Done(status="unknown", notice=f"{operation} outcome unknown ({error}); not re-run")
        elif isinstance(error, StepFailed):
            done = Done(status="failed", notice=f"{error.role} failed: {error.reason}; not retried or rerouted")
        else:
            log.exception("Work %s phase %s failed", work_id, operation)
            done = Done(status="failed", notice=f"Work failed: {error}")
        if operation == "action":
            self.owner.record_action(work_id, payload.get("request_id"), payload["action"], "unknown", payload["action_id"])
            return self._done(work_id, done)
        return {"done": asdict(done)}

    def activity(self, payload: dict[str, Any]) -> dict[str, Any]:
        """One durable boundary, with a stable intent before any model/tool/effect dispatch."""
        work_id, operation = payload["work_id"], payload["operation"]
        phase_id = payload.get("phase_id", operation + "." + str(payload.get("segment", 0)))
        payload = {**payload, "phase_id": phase_id}
        with self._work_lock(work_id):
            work = self.owner.get_work(work_id)
            pin = (work or {}).get("runtime") or {}
            if pin.get("binding_digest") != payload["binding_digest"] or payload["binding_digest"] != self.runtime.binding_digest:
                raise RuntimeError("runtime binding changed; no dispatch admitted")
            self.assert_current(work_id)
            retained = pin["phases"].get(phase_id)
            if retained and retained.get("state") == "completed":
                return retained["result"]
            if retained:
                done = Done(status="unknown", notice=f"{phase_id} has an unresolved intent; not re-run")
                self.owner.add_unknown("runtime_phase", {"work_id": work_id, "phase": phase_id})
                if operation == "action":
                    return self._done(work_id, done)
                return {"done": asdict(done)}
            intent = {"phase_id": phase_id, "operation": operation, "state": "intent",
                      "generation": pin["generation"], "started_at": self._now()}
            self.owner.update_work(work_id, lambda row: row["runtime"]["phases"].update({phase_id: intent}))
            try:
                if operation not in ("begin", "settle", "recover", "action", "recover_action"):
                    self._check_cancel(work_id)
                result = self._dispatch(payload)
            except Exception as error:  # noqa: BLE001 - Registry boundary; retain failed/unknown receipts.
                self.assert_current(work_id)
                result = self._phase_failure(payload, error)
            self.assert_current(work_id)
            receipt = {**intent, "state": "completed", "ended_at": self._now(), "result": result,
                       "result_digest": digest(result)}
            self.owner.update_work(work_id, lambda row: row["runtime"]["phases"].update({phase_id: receipt}))
            return result

    def _dispatch(self, payload: dict[str, Any]) -> dict[str, Any]:
        work_id, operation = payload["work_id"], payload["operation"]
        work = self.owner.get_work(work_id)
        spec = work["workspec"]
        segment = payload.get("segment", 0)
        team = work.get("team")
        hooks = self.owner.registry.teams.get(spec["target"]) if spec["target_kind"] == "team" else None
        officer = self.owner.registry.officers.get(spec["target"]) if spec["target_kind"] == "officer" else None
        if operation == "begin":
            self._set_status(work_id, "running")
            if hooks is not None:
                team = self._admit_team(work_id, spec, hooks)
            elif officer is None:
                raise RuntimeError(f"executor {spec['target']} is not registered")
            budget = (team or {}).get("budget") or {
                "segments": 1, "producer_invocations": 1, "same_gap_repeats": 1}
            return {"team": team is not None, "budget": budget}
        if operation == "compose":
            selected = self._compose(work_id, team) if team else [officer.name]
            return {"done": asdict(selected)} if isinstance(selected, Done) else {"steps": selected}
        if operation == "open":
            steps = payload["steps"]
            if team:
                remaining = work.get("remaining_work") if work["segments"] else None
                left = team["budget"]["producer_invocations"] - self._producers_used(work)
                problems = graph_problems({"steps": steps, "evaluation": "after_last"}, team, left, remaining)
                if problems or len(work["segments"]) >= team["budget"]["segments"]:
                    raise ValueError("Graph admission failed: " + "; ".join(problems or ["segment budget exhausted"]))
            elif steps != [officer.name] or work["segments"]:
                raise ValueError("Officer graph does not match its owner admission")
            if team:
                index = self._open_team_segment(work_id, team, steps)
            else:
                index = self._open_segment(work_id, steps, "director")
            opened = self.owner.get_work(work_id)["segments"][index]
            return {"segment": index, "graph_key": opened["runtime_topology"]["graph_key"]}
        if operation == "produce":
            role = payload["role"]
            if role not in work["segments"][segment]["graph"]["steps"]:
                raise ValueError("producer is not assigned in this segment")
            if team and team["pool"][role]["runtime"] == "model":
                self._run_model_step(work_id, segment, role, team, hooks)
            else:
                fn = hooks["static"][role] if team else officer.step
                self.run_static_step(work_id, segment, role, fn)
            return {}
        if operation == "evaluate_static":
            artifact = self._team_result(work_id, team, hooks) if team else self.last_artifact(work)
            self._evaluation_static(work_id, segment, artifact)
            return {}
        if operation == "evaluate_model":
            self._evaluation_model(work_id, segment)
            return {}
        if operation == "evaluate_commit":
            assessment = self._evaluation_commit(work_id, segment)
            current = self.owner.get_work(work_id)
            remaining = current.get("remaining_work")
            return {"assessment": assessment, "remaining": (
                {**remaining, "public": public_remaining(current)} if remaining else None)}
        if operation == "commit":
            artifact = self.owner.store.get_json_blob(work["segments"][segment]["evaluation_input"]["artifact_blob"])
            finish = hooks["finish"] if team else officer.finish
            done = finish(EffectContext(self.owner, work, payload.get("request_id"), payload.get("actor")), artifact)
            return {"done": asdict(done)}
        if operation == "review":
            self._team_review(work_id, team)
            return {}
        if operation == "settle":
            return self._done(work_id, Done(**payload["done"]))
        if operation == "recover":
            unfinished = [p for p in work["runtime"]["phases"].values()
                          if p["state"] == "intent" and p["operation"] not in ("recover", "settle")]
            committed = work["runtime"]["phases"].get(f"commit.{segment}", {}).get("result", {}).get("done")
            if unfinished and all(p["operation"] == "review" for p in unfinished) and committed:
                review = {"status": "unknown", "reason": "Worker stopped during TeamReview; not re-run",
                          "ratings": {}, "proposals": [], "recorded_at": self._now(), "invocation": None}
                self.owner.update_work(work_id, lambda row: row.update(team_review=review))
                done = Done(**committed)
            else:
                self.owner.add_unknown("runtime_phase", {"work_id": work_id,
                                                       "phases": [p["phase_id"] for p in unfinished]})
                done = Done(status="unknown", notice="Activity outcome unknown; retained intent is not re-run")
            return self._done(work_id, done)
        if operation == "action":
            action = self.owner.store.get("actions", payload["action_id"])
            if not action or action["state"] != "received" or work["status"] != "awaiting_confirmation":
                raise ValueError("human action is not pending on this Work")
            module, name = payload["handler"].split(":", 1)
            fn = getattr(importlib.import_module(module), name)
            done = fn(EffectContext(self.owner, work, payload.get("request_id"), payload.get("actor")),
                      self.last_artifact(work))
            state = "applied" if done.status == "completed" else "not_applied"
            self.owner.record_action(work_id, payload.get("request_id"), payload["action"], state, payload["action_id"])
            return self._done(work_id, done)
        if operation == "recover_action":
            self.owner.record_action(work_id, payload.get("request_id"), payload["action"], "unknown", payload["action_id"])
            self.owner.add_unknown("action", {"work_id": work_id, "action_id": payload["action_id"]})
            return self._done(work_id, Done(status="unknown", notice="Action outcome unknown; not re-run"))
        raise ValueError(f"unknown runtime phase {operation}")

    def run_static_step(
        self, work_id: str, segment: int, role: str, fn: Callable[[StepContext], dict[str, Any]]
    ) -> dict[str, Any]:
        """Run Owner code as one step and store its artifact as a State blob."""
        step: dict[str, Any] = {
            "step_id": f"s{segment}-{role}",
            "role": role,
            "runtime": "static",
            "status": "running",
            "started_at": self._now(),
            "ended_at": None,
            "artifact": None,
            "invocation": None,
            "error": None,
        }
        self._record_step(work_id, segment, step)
        try:
            artifact = fn(StepContext(self.owner, self.owner.get_work(work_id)))
        except Exception as error:
            step.update(status="failed", ended_at=self._now(), error=str(error))
            self._record_step(work_id, segment, step)
            raise
        step.update(
            status="completed",
            ended_at=self._now(),
            artifact={"blob": self.owner.store.put_json_blob(artifact), "digest": digest(artifact)},
        )
        self._record_step(work_id, segment, step)
        return artifact

    def last_artifact(self, work: dict[str, Any]) -> Any:
        """The artifact of the last completed producing step of the last segment."""
        for segment in reversed(work["segments"]):
            for step in reversed(segment["steps"]):
                if step["status"] == "completed" and step.get("artifact"):
                    return self.owner.store.get_json_blob(step["artifact"]["blob"])
        return None

    # ------------------------------------------------------------ evaluation

    def _evaluation_static(self, work_id: str, segment: int, artifact: Any) -> None:
        """Retain the exact checkpoint and static judgments before semantic dispatch."""
        work = self.owner.get_work(work_id)
        checkpoint = {
            "artifact_digest": digest(artifact), "recorded_at": self._now(),
            "graph_run_id": f"{work_id}.s{segment + 1}",
            "graph_key": work["segments"][segment]["runtime_topology"]["graph_key"],
            "runtime_binding_digest": work["runtime"]["binding_digest"],
            "steps": [{"step_id": step["step_id"], "status": step["status"],
                       "artifact": step.get("artifact"), "invocation": step.get("invocation")}
                      for step in work["segments"][segment]["steps"]],
        }
        checkpoint["checkpoint_digest"] = digest(checkpoint)
        ctx = StepContext(self.owner, work)
        criteria = work["workspec"]["criteria"]
        failed = self._failed_step_criteria(work, segment)
        judgments: list[dict[str, Any]] = []
        semantic = []
        if artifact is None:
            judgments = [{"criterion": c["id"], "target": None, "verdict": "unmet",
                          "reason": failed.get(c["id"], "no result artifact to evaluate"), "runtime": "owner"}
                         for c in criteria]
        else:
            for criterion in criteria:
                if criterion.get("runtime") != "static":
                    continue
                check = self.owner.registry.checks[criterion["id"]]
                try:
                    found = list(check(ctx, artifact))
                except Exception as error:  # noqa: BLE001 - A failed criterion plugin is an unmet judgment.
                    found = [{"target": None, "verdict": "unmet", "reason": f"check failed: {error}"}]
                if not found:
                    found = [{"target": None, "verdict": "satisfied", "reason": "nothing to check"}]
                judgments.extend({**judgment, "criterion": criterion["id"], "runtime": "static"} for judgment in found)
            judgments.extend(
                {"criterion": criterion, "target": None, "verdict": "unmet", "reason": reason, "runtime": "owner"}
                for criterion, reason in failed.items()
            )
            semantic = [c["id"] for c in criteria if c.get("runtime") != "static" and c["id"] not in failed]
        prepared = {"artifact_blob": self.owner.store.put_json_blob(artifact),
                    "judgments": judgments, "semantic_ids": semantic}
        self.owner.update_work(work_id, lambda row: row["segments"][segment].update(
            checkpoint=checkpoint, evaluation_input=prepared))

    def _evaluation_model(self, work_id: str, segment: int) -> None:
        work = self.owner.get_work(work_id)
        prepared = work["segments"][segment]["evaluation_input"]
        criteria = [c for c in work["workspec"]["criteria"] if c["id"] in prepared["semantic_ids"]]
        model, invocation = [], None
        if criteria:
            artifact = self.owner.store.get_json_blob(prepared["artifact_blob"])
            static = [j for j in prepared["judgments"] if j["runtime"] == "static"]
            model, invocation = self._model_judgments(work, segment, artifact, criteria, static)
        self.owner.update_work(work_id, lambda row: row["segments"][segment].update(
            evaluation_model={"judgments": model, "invocation": invocation}))

    def _evaluation_commit(self, work_id: str, segment: int) -> dict[str, Any]:
        work = self.owner.get_work(work_id)
        current = work["segments"][segment]
        prepared, model = current["evaluation_input"], current["evaluation_model"]
        static = [j for j in prepared["judgments"] if j["runtime"] == "static"]
        judgments = prepared["judgments"] + self._lower(model["judgments"], static)
        assessment = self.compile_assessment(work["workspec"]["criteria"], judgments)
        evaluation = {"officer": work["workspec"].get("evaluation", {}),
                      "judgments": judgments, "invocation": model["invocation"]}
        remaining = self._remaining_work(assessment, judgments, segment)

        def change(record: dict[str, Any]) -> None:
            record["segments"][segment].update(evaluation=evaluation, assessment=assessment)
            record["remaining_work"] = remaining

        self.owner.update_work(work_id, change)
        if work.get("team"):
            from .learning import on_assessment

            on_assessment(self.owner, work_id, work["team"]["manager"], segment,
                          current["graph"]["steps"], assessment)
        return assessment

    @staticmethod
    def compile_assessment(criteria: list[dict[str, Any]], judgments: list[dict[str, Any]]) -> dict[str, Any]:
        """Owner-compiled CompletionAssessment: a criterion is satisfied only if all its judgments are."""
        unmet = []
        for criterion in criteria:
            verdicts = [j["verdict"] for j in judgments if j["criterion"] == criterion["id"]]
            if not verdicts or any(verdict != "satisfied" for verdict in verdicts):
                unmet.append(criterion["id"])
        return {"status": "unmet" if unmet else "satisfied", "unmet": unmet}

    @staticmethod
    def _remaining_work(
        assessment: dict[str, Any], judgments: list[dict[str, Any]], segment: int
    ) -> dict[str, Any] | None:
        """RemainingWork: the unmet criteria with their targets and reasons, or None."""
        if assessment["status"] == "satisfied":
            return None
        gaps = []
        for criterion in assessment["unmet"]:
            failing = [j for j in judgments if j["criterion"] == criterion and j["verdict"] != "satisfied"]
            targets = sorted({j["target"] for j in failing if j.get("target")})
            gaps.append({"criterion": criterion, "targets": targets, "reasons": [j["reason"] for j in failing][:3]})
        signature = digest({"criteria_revision": 1, "criterion_ids": sorted(assessment["unmet"])})
        return {"criteria": assessment["unmet"], "gaps": gaps, "segment": segment, "gap_signature": signature}

    @staticmethod
    def _failed_step_criteria(work: dict[str, Any], segment: int) -> dict[str, str]:
        """Criteria addressed by teammate steps that failed in this segment, with the reason."""
        team = work.get("team")
        if not team:
            return {}
        admitted = {criterion["id"] for criterion in work["workspec"]["criteria"]}
        failed: dict[str, str] = {}
        for step in work["segments"][segment]["steps"]:
            if step["status"] != "failed":
                continue
            for criterion in team["pool"].get(step["role"], {}).get("addresses", []):
                if criterion in admitted:
                    failed.setdefault(criterion, f"step {step['role']} failed: {step.get('error')}")
        return failed

    @staticmethod
    def _lower(model: list[dict[str, Any]], static: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """The Owner may only lower a model judgment where a static check failed for its target."""
        failed_targets = {j.get("target") for j in static if j["verdict"] != "satisfied"}
        lowered = []
        for judgment in model:
            if judgment["verdict"] == "satisfied" and judgment.get("target") in failed_targets:
                judgment = {
                    **judgment,
                    "verdict": "unmet",
                    "lowered_from": "satisfied",
                    "reason": f"{judgment['reason']} (lowered: a static check failed for this target)",
                }
            lowered.append(judgment)
        return lowered

    def _model_judgments(
        self,
        work: dict[str, Any],
        segment: int,
        artifact: Any,
        criteria: list[dict[str, Any]],
        static_judgments: list[dict[str, Any]],
    ) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
        """The model EvalOfficer (``kos-eval``) judges the semantic criteria from the criteria,
        the static check results and bounded artifact excerpts with their cited evidence. It
        never sees Manager rationale or teammate self-assessment."""
        work_id = work["work_id"]
        pin = work["workspec"].get("evaluation") or {}
        role = pin.get("role", EVAL_ROLE)
        ids = [criterion["id"] for criterion in criteria]
        evidence = Evidence(self.owner, work_id)
        known = set(evidence.table())
        cited = evidence.handles_in(artifact)
        view_criteria = [{"id": c["id"], "text": c.get("text", "")} for c in criteria]
        static = [{k: j.get(k) for k in ("criterion", "target", "verdict", "reason")} for j in static_judgments]

        def tools(binding: roles.RoleBinding) -> dict[str, Callable[..., Any]]:
            def view() -> dict[str, Any]:
                evidence.assert_disclosure()
                return {
                    "criteria": view_criteria,
                    "static_results": static,
                    "artifact": bounded(artifact),
                    "evidence": evidence.excerpts(cited),
                }

            return {"eval_view": view, "feedback": lambda: self._feedback(binding.role)}

        try:
            session = self._invoke(role, f"s{segment}-eval", work_id, "submit_evaluation", tools,
                                   lambda payload: evaluation_problems(payload, ids, known))
        except StepFailed as failure:
            status = "unknown" if isinstance(failure, StepUnknown) else "failed"
            failed = {"officer": pin, "status": status, "error": failure.reason, "invocation": failure.invocation}
            self.owner.update_work(work_id, lambda record: record["segments"][segment].update(evaluation=failed))
            raise
        invocation = session.context["invocation"]
        if not session.accepted:
            reason = "the EvalOfficer's submission failed validation: " + rejected(session)
            return [
                {"criterion": cid, "target": None, "verdict": "unmet", "reason": reason, "runtime": "model"}
                for cid in ids
            ], invocation
        return [
            {
                "criterion": j["criterion"],
                "target": j.get("target"),
                "verdict": j["verdict"],
                "reason": j.get("reason", ""),
                "runtime": "model",
            }
            for j in session.submission["judgments"]
        ], invocation

    # ------------------------------------------------------------ role sessions

    def _invoke(
        self,
        role: str,
        step_id: str,
        work_id: str,
        submission: str | None,
        tools: Callable[[roles.RoleBinding], dict[str, Callable[..., Any]]],
        validate: Callable[[Any], list[str]],
    ) -> RoleSession:
        """Bind a role to this Work step, take its identity once, and run one session.

        An unavailable profile, route or credential (or any runner error) fails the step
        visibly; an unobservable outcome is unknown. Neither is retried or rerouted.
        """
        runner = self.owner.runner
        self.assert_current(work_id)
        work = self.owner.get_work(work_id)
        if digest(self.owner.catalog.roles()) != work["runtime"]["role_declarations_digest"]:
            raise StepFailed(role, "admitted role declarations changed; revised admission required")
        invocation: dict[str, Any] | None = None
        try:
            if runner is None:
                raise RoleUnavailable("no role runner is configured")
            binding = roles.binding(self.owner.catalog, role, work_id, step_id, submission)
            invocation = dict(runner.identity(binding))
            session = RoleSession(binding, tools(binding), validate, context={"invocation": invocation})
            roles.run_session(runner, session)
            self.assert_current(work_id)
        except RoleOutcomeUnknown as error:
            raise StepUnknown(role, str(error), invocation) from error
        except RoleUnavailable as error:
            raise StepFailed(role, f"unavailable: {error}", invocation) from error
        except Exception as error:
            log.exception("role %s failed in %s", role, work_id)
            raise StepFailed(role, f"{type(error).__name__}: {error}", invocation) from error
        return session

    def _feedback(self, role: str) -> dict[str, Any]:
        """Tool ``feedback``: outcomes of this role's own earlier outputs (C9 records them)."""
        items = [
            {key: value for key, value in entry.items() if key != "role"}
            for entry in self.owner.store.read_stream("feedback")
            if entry.get("role") == role
        ]
        return {"items": bounded(items[-20:])}

    # ------------------------------------------------------------ Team flow


    def _admit_team(self, work_id: str, spec: dict[str, Any], hooks: dict[str, Any]) -> dict[str, Any]:
        """The admitted pool, Manager and budget for this Work, from the catalog target."""
        target = self.owner.get_work(work_id)["runtime"]["capability_declaration"].get("target", {})
        pool = {name: dict(entry) for name, entry in target.get("pool", {}).items()}
        missing = [n for n, e in pool.items() if e.get("runtime") == "static" and n not in hooks.get("static", {})]
        if not target.get("manager") or not pool or missing or "finish" not in hooks:
            raise RuntimeError(f"Team {spec['target']} is not fully declared (manager, pool, static steps, finish)")
        budget = spec.get("budget") or {}
        team = {
            "name": spec["target"],
            "manager": target["manager"],
            "pool": pool,
            "final": next((name for name, entry in pool.items() if entry.get("final")), None),
            "budget": {
                "segments": int(budget.get("segments", TEAM_SEGMENTS)),
                "producer_invocations": int(budget.get("producer_invocations", TEAM_PRODUCERS)),
                "same_gap_repeats": int(budget.get("same_gap_repeats", SAME_GAP_REPEATS)),
            },
        }
        self.owner.update_work(work_id, lambda work: work.update(team=team))
        return team

    @staticmethod
    def _producers_used(work: dict[str, Any]) -> int:
        return sum(len(segment["steps"]) for segment in work["segments"])

    def _team_view(self, work_id: str, team: dict[str, Any], phase: str) -> dict[str, Any]:
        """Tool ``team_view``: pool, budget, prior segments and RemainingWork. No note content."""
        work = self.owner.get_work(work_id) or {}
        spec = work["workspec"]
        return {
            "phase": phase,
            "capability": spec["capability"],
            "team": team["name"],
            "mode": spec["mode"],
            "pool": [
                {"id": name, "runtime": entry["runtime"], "addresses": entry.get("addresses", [])}
                for name, entry in team["pool"].items()
            ],
            "final": team["final"],
            "criteria": [{"id": c["id"], "runtime": c.get("runtime"), "text": c.get("text", "")} for c in spec["criteria"]],
            "budget": {
                "segments": {"total": team["budget"]["segments"], "used": len(work["segments"])},
                "producer_invocations": {
                    "total": team["budget"]["producer_invocations"],
                    "used": self._producers_used(work),
                },
            },
            "segments": [
                {
                    "index": segment["index"],
                    "steps": [{"teammate": step["role"], "status": step["status"]} for step in segment["steps"]],
                    "assessment": segment["assessment"],
                }
                for segment in work["segments"]
            ],
            "remaining_work": public_remaining(work),
        }

    def _compose(self, work_id: str, team: dict[str, Any]) -> list[str] | Done:
        """The Manager proposes a Graph; the Owner validates it. An authority violation is
        rejected without a corrective turn and no segment starts."""
        work = self.owner.get_work(work_id) or {}
        index = len(work["segments"])
        remaining = work["remaining_work"] if index else None
        left = team["budget"]["producer_invocations"] - self._producers_used(work)

        def tools(binding: roles.RoleBinding) -> dict[str, Callable[..., Any]]:
            return {
                "team_view": lambda: self._team_view(work_id, team, "graph"),
                "feedback": lambda: self._feedback(binding.role),
            }

        try:
            session = self._invoke(team["manager"], f"s{index}-manager", work_id, "submit_graph", tools,
                                   lambda payload: graph_problems(payload, team, left, remaining))
        except StepFailed as failure:
            status = "unknown" if isinstance(failure, StepUnknown) else "failed"
            self._note_composition(work_id, index, status, failure.invocation, [failure.reason])
            raise
        invocation = session.context["invocation"]
        if session.violation:
            self._note_composition(work_id, index, "rejected", invocation, session.diagnostics, violation=True)
            return Done(
                status="failed",
                notice=f"Graph proposal rejected ({session.violation}); no segment started",
            )
        if not session.accepted:
            self._note_composition(work_id, index, "failed", invocation, session.diagnostics)
            notice = f"{team['manager']} proposed no valid Graph: " + rejected(session)
            if remaining:
                return Done(status="unmet", outcome={"unmet": remaining["criteria"],
                                                     "remaining_work": public_remaining(work)}, notice=notice)
            return Done(status="failed", notice=notice)
        self._note_composition(work_id, index, "accepted", invocation, [], attempts=session.attempts)
        return list(session.submission["steps"])

    def _note_composition(
        self, work_id: str, index: int, status: str, invocation: dict[str, Any] | None, diagnostics: list[str],
        **extra: Any,
    ) -> None:
        self.assert_current(work_id)
        entry = {"segment": index, "status": status, "invocation": invocation, "diagnostics": diagnostics,
                 "at": self._now(), **extra}
        self.owner.update_work(work_id, lambda work: work.setdefault("compositions", []).append(entry))


    def _run_model_step(
        self, work_id: str, segment: int, role: str, team: dict[str, Any], hooks: dict[str, Any]
    ) -> Any:
        """One model teammate session; its handoff is validated with one corrective turn."""
        step_id = f"s{segment}-{role}"
        step: dict[str, Any] = {
            "step_id": step_id,
            "role": role,
            "runtime": "model",
            "status": "running",
            "started_at": self._now(),
            "ended_at": None,
            "artifact": None,
            "invocation": None,
            "attempts": 0,
            "error": None,
        }
        self._record_step(work_id, segment, step)
        validator = hooks.get("validate", {}).get(role)

        def validate(payload: Any) -> list[str]:
            if validator is None:
                return [] if isinstance(payload, dict) else ["the handoff must be a JSON object"]
            return list(validator(StepContext(self.owner, self.owner.get_work(work_id)), payload))

        def tools(binding: roles.RoleBinding) -> dict[str, Callable[..., Any]]:
            evidence = Evidence(self.owner, work_id)
            by = binding.step_id
            granted: dict[str, Callable[..., Any]] = {
                "work_context": lambda: self._work_context(work_id, segment, binding, hooks),
                "read": lambda handle=None: evidence.read(handle, by=by),
                "search": lambda query=None, limit=10: evidence.search(query, by=by, limit=limit),
                "related": lambda handle=None, limit=10: evidence.related(handle, by=by, limit=limit),
                "feedback": lambda: self._feedback(binding.role),
            }
            # Team-specific tools ``fn(ctx, step_id, **args)``; the role binding (config/roles.json)
            # still decides exposure.
            for name, fn in hooks.get("tools", {}).items():
                granted[name] = lambda fn=fn, **args: fn(StepContext(self.owner, self.owner.get_work(work_id)), by, **args)
            return granted

        try:
            session = self._invoke(role, step_id, work_id, team["pool"][role].get("submission"), tools, validate)
        except StepFailed as failure:
            step.update(
                status="unknown" if isinstance(failure, StepUnknown) else "failed",
                ended_at=self._now(),
                invocation=failure.invocation,
                error=failure.reason,
            )
            self._record_step(work_id, segment, step)
            raise
        step.update(ended_at=self._now(), invocation=session.context["invocation"], attempts=session.attempts,
                    corrective=bool(session.corrective))
        if not session.accepted:
            step.update(status="failed", error="submission failed validation twice: " + rejected(session))
            self._record_step(work_id, segment, step)
            return None
        artifact = session.submission
        step.update(
            status="completed",
            artifact={"blob": self.owner.store.put_json_blob(artifact), "digest": digest(artifact)},
        )
        self._record_step(work_id, segment, step)
        return artifact

    def _handoffs(self, work: dict[str, Any], before: str | None = None) -> list[dict[str, Any]]:
        """Completed teammate handoffs in order (all segments), stopping at step ``before``."""
        found = []
        for segment in work["segments"]:
            for step in segment["steps"]:
                if step["step_id"] == before:
                    return found
                if step["status"] == "completed" and step.get("artifact"):
                    found.append(
                        {
                            "teammate": step["role"],
                            "segment": segment["index"],
                            "handoff": self.owner.store.get_json_blob(step["artifact"]["blob"]),
                        }
                    )
        return found

    def _work_context(
        self, work_id: str, segment: int, binding: roles.RoleBinding, hooks: dict[str, Any]
    ) -> dict[str, Any]:
        """Tool ``work_context``: the Work objective, mode, focal handles, earlier handoffs and,
        in a successor segment, the RemainingWork it addresses. No paths, IDs or digests."""
        work = self.owner.get_work(work_id) or {}
        spec = work["workspec"]
        view: dict[str, Any] = {
            "capability": spec["capability"],
            "mode": spec["mode"],
            "teammate": binding.role,
            "submission": binding.submission_tool,
        }
        context = hooks.get("context")
        if context is not None:
            view.update(context(StepContext(self.owner, work), binding.step_id))
        view["handoffs"] = bounded(self._handoffs(self.owner.get_work(work_id) or work, binding.step_id))
        if segment and work.get("remaining_work"):
            view["remaining_work"] = public_remaining(work)
        Evidence(self.owner, work_id).assert_disclosure()
        return view

    def _team_result(self, work_id: str, team: dict[str, Any], hooks: dict[str, Any]) -> Any:
        """The artifact the EvalOfficer assesses: by default the final teammate's latest handoff.

        A teammate whose latest step failed contributes nothing."""
        work = self.owner.get_work(work_id) or {}
        latest: dict[str, Any] = {}
        for segment in work["segments"]:
            for step in segment["steps"]:
                if step["status"] == "completed" and step.get("artifact"):
                    latest[step["role"]] = self.owner.store.get_json_blob(step["artifact"]["blob"])
                else:
                    latest.pop(step["role"], None)
        result = hooks.get("result")
        if result is not None:
            return result(StepContext(self.owner, work), latest)
        if team["final"]:
            return latest.get(team["final"])
        return latest or None

    def _team_review(self, work_id: str, team: dict[str, Any]) -> None:
        """Advisory TeamReview by the Manager. It changes no role, catalog entry or assessment."""
        work = self.owner.get_work(work_id) or {}
        required: list[str] = []
        allowed: list[str] = []
        for segment in work["segments"]:
            for step in segment["steps"]:
                if step["role"] not in allowed:
                    allowed.append(step["role"])
                if step["status"] == "completed" and step.get("artifact") and step["role"] not in required:
                    required.append(step["role"])

        def tools(binding: roles.RoleBinding) -> dict[str, Callable[..., Any]]:
            return {
                "team_view": lambda: {**self._team_view(work_id, team, "team_review"), "producing": required},
                "feedback": lambda: self._feedback(binding.role),
            }

        review: dict[str, Any]
        try:
            session = self._invoke(team["manager"], "review", work_id, "submit_team_review", tools,
                                   lambda payload: review_problems(payload, required, allowed))
        except StepFailed as failure:
            unknown = isinstance(failure, StepUnknown)
            if unknown:
                self.owner.add_unknown("role_invocation", {"work_id": work_id, "role": failure.role,
                                                           "step": "team_review"})
            review = {"status": "unknown" if unknown else "failed", "reason": failure.reason, "ratings": {},
                      "proposals": [], "recorded_at": self._now(), "invocation": failure.invocation}
        else:
            if session.accepted:
                payload = session.submission
                review = {
                    "status": "recorded",
                    "ratings": {
                        name: {"rating": value["rating"], "note": value.get("note", "")}
                        for name, value in payload["ratings"].items()
                    },
                    "proposals": [{"text": item["text"], "considered": False} for item in payload.get("proposals", [])],
                    "recorded_at": self._now(),
                    "invocation": session.context["invocation"],
                }
            else:
                review = {"status": "failed", "reason": rejected(session), "ratings": {},
                          "proposals": [], "recorded_at": self._now(), "invocation": session.context["invocation"]}
        self.owner.update_work(work_id, lambda record: record.update(team_review=review))
        from .learning import on_team_review

        on_team_review(self.owner, work_id, team["manager"], review)


# ---------------------------------------------------------------- Owner validation


def public_remaining(work: dict[str, Any]) -> dict[str, Any] | None:
    """RemainingWork as roles see it: criteria with text and the gaps (targets are handles)."""
    remaining = work.get("remaining_work")
    if not remaining:
        return None
    texts = {c["id"]: c.get("text", "") for c in work["workspec"]["criteria"]}
    return {
        "criteria": [{"id": cid, "text": texts.get(cid, "")} for cid in remaining["criteria"]],
        "gaps": [
            {"criterion": gap["criterion"], "targets": gap["targets"], "reasons": bounded(gap["reasons"])}
            for gap in remaining["gaps"]
        ],
    }


def graph_problems(
    payload: Any, team: dict[str, Any], left: int, remaining: dict[str, Any] | None
) -> list[str]:
    """Validate a Manager Graph proposal ``{"steps": [...], "evaluation": "after_last"}``.

    Anything addressing the EvalOfficer raises ``AuthorityViolation`` (no corrective turn).
    """
    if not isinstance(payload, dict):
        return ['submit {"steps": ["<teammate id>", ...], "evaluation": "after_last"}']
    for key in payload:
        name = str(key).lower()
        if key not in ("steps", "evaluation") and ("eval" in name or name in EVAL_FIELDS):
            raise AuthorityViolation(f"field {key!r} addresses the EvalOfficer")
    if "evaluation" in payload and payload["evaluation"] != "after_last":
        raise AuthorityViolation("the evaluation's position, instructions, inputs and route belong to the Owner")
    steps = payload.get("steps")
    if isinstance(steps, list):
        for item in steps:
            text = item if isinstance(item, str) else canonical(item).decode("utf-8")
            if "eval" in text.lower():
                raise AuthorityViolation("the EvalOfficer is placed by the Owner, not by the Manager")
    problems = []
    unknown = sorted(str(key) for key in payload if key not in ("steps", "evaluation"))
    if unknown:
        problems.append(f"unknown fields: {', '.join(unknown)}")
    if "evaluation" not in payload:
        problems.append('evaluation must be "after_last"')
    if not isinstance(steps, list) or not steps or not all(isinstance(item, str) for item in steps):
        problems.append("steps must be a non-empty list of teammate IDs")
        return problems
    pool = team["pool"]
    outside = [item for item in steps if item not in pool]
    if outside:
        problems.append(f"not in the admitted pool: {', '.join(outside)} (pool: {', '.join(pool)})")
    if len(set(steps)) != len(steps):
        problems.append("each teammate appears at most once per segment")
    if team["final"] and steps[-1] != team["final"]:
        problems.append(f"{team['final']} must be the last producer")
    if len(steps) > left:
        problems.append(f"{len(steps)} steps exceed the remaining producer budget of {left}")
    if remaining:
        gap = set(remaining["criteria"])
        idle = [item for item in steps if item in pool and not gap & set(pool[item].get("addresses", []))]
        if idle:
            problems.append(
                f"a successor addresses only RemainingWork ({', '.join(remaining['criteria'])}); "
                f"{', '.join(idle)} does not address it"
            )
    return problems


def evaluation_problems(payload: Any, criteria: list[str], handles: set[str]) -> list[str]:
    """Validate an EvalOfficer submission ``{"judgments": [...]}`` for the given criteria."""
    if not isinstance(payload, dict) or set(payload) != {"judgments"} or not isinstance(payload["judgments"], list):
        return ['submit {"judgments": [{"criterion", "target", "verdict", "reason"}, ...]}']
    problems = []
    judged = set()
    for number, judgment in enumerate(payload["judgments"], start=1):
        where = f"judgment {number}"
        if not isinstance(judgment, dict):
            problems.append(f"{where}: must be an object")
            continue
        extra = set(judgment) - {"criterion", "target", "verdict", "reason"}
        if extra:
            problems.append(f"{where}: unknown fields {', '.join(sorted(map(str, extra)))}")
        if judgment.get("criterion") not in criteria:
            problems.append(f"{where}: criterion must be one of {', '.join(criteria)}")
        else:
            judged.add(judgment["criterion"])
        if judgment.get("verdict") not in ("satisfied", "unmet"):
            problems.append(f"{where}: verdict must be satisfied or unmet")
        target = judgment.get("target")
        if target is not None and target not in handles:
            problems.append(f"{where}: target must be an evidence handle of this Work or null")
        if not isinstance(judgment.get("reason", ""), str):
            problems.append(f"{where}: reason must be text")
    missing = [criterion for criterion in criteria if criterion not in judged]
    if missing:
        problems.append(f"judge every semantic criterion: missing {', '.join(missing)}")
    return problems


def review_problems(payload: Any, required: list[str], allowed: list[str]) -> list[str]:
    """Validate a TeamReview ``{"ratings": {...}, "proposals": [{"text"}]}`` (≤ 3 proposals)."""
    if not isinstance(payload, dict) or not isinstance(payload.get("ratings"), dict):
        return ['submit {"ratings": {"<teammate>": {"rating": 1-5, "note": "..."}}, "proposals": [{"text": "..."}]}']
    problems = []
    extra = set(payload) - {"ratings", "proposals"}
    if extra:
        problems.append(f"unknown fields: {', '.join(sorted(map(str, extra)))}")
    ratings = payload["ratings"]
    missing = [name for name in required if name not in ratings]
    if missing:
        problems.append(f"rate every producing teammate: missing {', '.join(missing)}")
    outside = [str(name) for name in ratings if name not in allowed]
    if outside:
        problems.append(f"not a teammate of this Work: {', '.join(outside)}")
    for name, value in ratings.items():
        if not isinstance(value, dict) or set(value) - {"rating", "note"}:
            problems.append(f"{name}: a rating is {{\"rating\": 1-5, \"note\": \"...\"}}")
            continue
        rating = value.get("rating")
        if not isinstance(rating, int) or isinstance(rating, bool) or not 1 <= rating <= 5:
            problems.append(f"{name}: rating must be an integer from 1 to 5")
        if not isinstance(value.get("note", ""), str):
            problems.append(f"{name}: note must be text")
    proposals = payload.get("proposals", [])
    if not isinstance(proposals, list) or len(proposals) > MAX_PROPOSALS:
        problems.append(f"proposals must be a list of at most {MAX_PROPOSALS}")
    else:
        for number, item in enumerate(proposals, start=1):
            if not isinstance(item, dict) or set(item) != {"text"} or not isinstance(item["text"], str) \
                    or not item["text"].strip():
                problems.append(f"proposal {number}: must be {{\"text\": \"...\"}}")
    return problems

"""Explicit Operation-owned due admission; no Worker, model or host Cron authority.

``admit_request`` is the trusted Director-to-ExOps composition, not an external
submit hook. It must recover its deterministic Director and Work identities on
reentry after process loss. An explicit unknown response is never retried.
"""
from __future__ import annotations

import asyncio
import copy
import hashlib
import json
from collections.abc import Callable
from typing import Any

from ..adapters.core import AdmissionError, canonical, digest
from ..adapters.owner_journal import read_regular, stamp
from ..adapters.paths import resolve_beneath
from ..note_engine import parse_frontmatter
from .daily_stewardship import METHOD_ID, DailyStewardship
from .work_methods import WorkMethodCatalog

_PREFIX = "daily.admission."


class DailyAdmissionScheduler:
    """Persist exact due requests before trusted, idempotent Director admission."""

    def __init__(self, application: Any, *, admit_request: Callable[[dict], dict],
                 authorize: Callable[[], bool], limit: int = 50,
                 timezone: str = "Asia/Seoul") -> None:
        if type(limit) is not int or not 1 <= limit <= 50:
            raise AdmissionError("INVALID_ARGUMENT", "Daily admission limit must be between 1 and 50")
        if not callable(admit_request) or not callable(authorize):
            raise TypeError("Daily scheduling requires trusted admission and authorization ports")
        self.app, self.admit_request, self.authorize = application, admit_request, authorize
        self.limit = limit
        self.steward = DailyStewardship(application, timezone=timezone)

    def _guard(self) -> None:
        self.app.guard("apply")
        if not self.authorize():
            raise AdmissionError("ACTION_DENIED", "Daily scheduling authorization was revoked")

    def _records(self) -> dict[str, dict]:
        return {key: item for key, item in self.app.journal.read()["intents"].items()
                if key.startswith(_PREFIX) and isinstance(item, dict)}

    def _validate(self, record: dict) -> None:
        self._guard()
        request = record["request"]
        source = request["source_reference"]
        if (self.app.resolve_reference(source) != record["path"]
                or WorkMethodCatalog.load(self.app.roots.control).reference(METHOD_ID) != request["method_contract_ref"]
                or self.steward._registry_digest() != record["registry_digest"]
                or self.steward._policy_digest() != record["policy_digest"]
                or digest(canonical({"source": source, "method": request["method_contract_ref"],
                                     "registry": record["registry_digest"], "policy": record["policy_digest"]}))
                != request["request_id"]):
            raise AdmissionError("RESOURCE_STALE", "Daily source, method, registry or policy changed")
        self.app._mcp_source_policy(record["path"])

    def _save(self, key: str, changes: dict) -> dict:
        now = self.app.clock()
        with self.app.journal.writer() as writer:
            record = writer.value["intents"][key]
            record.update(copy.deepcopy(changes), recorded_at=stamp(now))
            writer.commit(now)
            return copy.deepcopy(record)

    @staticmethod
    def _blocker(error: Exception, *, request_id: str | None = None) -> dict:
        result = {"code": getattr(error, "code", "OUTCOME_UNKNOWN"), "message": str(error)}
        if request_id is not None:
            result["request_id"] = request_id
        return result

    def _admit(self, key: str, record: dict) -> dict:
        self._validate(record)
        # A crash here leaves an exact resumable owner-local call, never a new
        # request ID. The callback persists Director pins before admitting Work.
        self._save(key, {"state": "admitting"})
        self._validate(record)
        try:
            receipt = self.admit_request(copy.deepcopy(record["request"]))
        except Exception as error:
            self._save(key, {"state": "unknown", "blocker": self._blocker(error)})
            raise
        if (not isinstance(receipt, dict) or receipt.get("receipt_state") != "accepted"
                or receipt.get("owner_operation_id") != "knowledgeos"
                or not isinstance(receipt.get("work_run_id"), str)):
            error = AdmissionError("OUTCOME_UNKNOWN", "Daily Director admission has no exact accepted Work receipt")
            self._save(key, {"state": "unknown", "blocker": self._blocker(error)})
            raise error
        work = self.app.journal.read()["intents"].get("exops.work." + receipt["work_run_id"])
        if (not isinstance(work, dict) or work.get("spec", {}).get("method_contract_ref")
                != record["request"]["method_contract_ref"]
                or work.get("method_source_reference") != record["request"]["source_reference"]
                or work.get("run", {}).get("work_run_id") != receipt["work_run_id"]
                or any(receipt.get(field) != work.get("receipt", {}).get(field)
                       for field in ("work_id", "action_id", "content_digest"))):
            error = AdmissionError("OUTCOME_UNKNOWN", "Daily receipt lacks its exact durable source-bound Work")
            self._save(key, {"state": "unknown", "blocker": self._blocker(error)})
            raise error
        self._save(key, {"state": "admitted", "receipt": receipt})
        return receipt

    def _candidate(self, record: dict, work: dict) -> dict:
        artifact = work["run"].get("result_reference")
        if not isinstance(artifact, dict):
            raise AdmissionError("ACCEPTANCE_REQUIRED", "Daily Work has no assessed result artifact")
        selected = [ref for ref in work.get("submitted_artifacts", [])
                    if ref.get("content_digest") == artifact.get("content_digest")]
        if len(selected) != 1:
            raise AdmissionError("RESOURCE_UNAVAILABLE", "Daily result cannot be resolved exactly")
        path = self.app.resolve_reference(selected[0], proposal=True)
        raw = read_regular(resolve_beneath(self.app.roots.vault, path))
        if raw is None or digest(raw) != artifact["content_digest"]:
            raise AdmissionError("RESOURCE_STALE", "Daily result artifact changed")
        body = parse_frontmatter(raw.decode("utf-8")).body
        try:
            payload = json.loads(body.split("```json\n", 1)[1].split("\n```", 1)[0])
            candidate = json.loads(payload["content"])
        except (IndexError, KeyError, TypeError, ValueError) as error:
            raise AdmissionError("INVALID_ARGUMENT", "Daily result has no structured candidate") from error
        if (payload.get("artifact_kind") != "daily_enrichment" or not isinstance(candidate, dict)
                or candidate.get("source_reference") != record["request"]["source_reference"]
                or candidate.get("registry_digest") != record["registry_digest"]):
            raise AdmissionError("RESOURCE_STALE", "Daily result differs from its admitted request")
        return candidate

    def _observed_consumption(self, record: dict) -> dict | None:
        """Observe only positive owner evidence; absence never authorizes retry."""
        work_id = record["receipt"]["work_run_id"]
        intents = self.app.journal.read()["intents"]
        effects = [(key, item) for key, item in intents.items()
                   if key.startswith("daily.enrichment.") and item.get("work_run_id") == work_id
                   and item.get("source_digest") == record["request"]["source_reference"]["content_digest"]]
        if effects:
            if len(effects) != 1 or effects[0][1].get("state") != "completed":
                raise AdmissionError("OUTCOME_UNKNOWN", "Daily provisional effect requires owner reconciliation")
            return {"effect_id": effects[0][0], "review_status": effects[0][1]["review_status"]}
        coverage = intents.get("daily.enrichment." + hashlib.sha256(record["path"].encode()).hexdigest(), {})
        if (coverage.get("state") == "no_evidence"
                and coverage.get("source_digest") == record["request"]["source_reference"]["content_digest"]
                and coverage.get("registry_digest") == record["registry_digest"]
                and coverage.get("policy_digest") == record["policy_digest"]):
            return {"status": "no_evidence", "path": record["path"]}
        return None

    def _consumption_batch(self) -> list[tuple[str, dict]]:
        # Advance before inspecting Work or applying effects. A process loss or
        # persistent blocker cannot pin the first batch after reconstruction;
        # revisiting a candidate still uses exact positive effect evidence.
        now = self.app.clock()
        with self.app.journal.writer() as writer:
            records = sorted((key, item) for key, item in writer.value["intents"].items()
                             if key.startswith(_PREFIX) and isinstance(item, dict)
                             and item.get("state") in {"admitted", "consuming"})
            if not records:
                return []
            self._guard()
            cursor = writer.value["intents"].get("daily.scheduler.consumption", {}).get("after_request_key", "")
            ordered = [item for item in records if item[0] > cursor]
            ordered.extend(item for item in records if item[0] <= cursor)
            selected = ordered[:self.limit]
            if selected[-1][0] != cursor:
                writer.value["intents"]["daily.scheduler.consumption"] = {
                    "state": "completed", "recorded_at": stamp(now),
                    "after_request_key": selected[-1][0],
                }
                writer.commit(now)
            return copy.deepcopy(selected)

    def consume_completed(self) -> dict:
        """Consume a durable round-robin batch through existing owner effects."""
        self._guard()
        consumed, blockers = [], []
        for key, record in self._consumption_batch():
            work_id = record["receipt"]["work_run_id"]
            work = self.app.journal.read()["intents"].get("exops.work." + work_id, {})
            if (not work or work.get("dispatch_state") == "unknown"
                    or work.get("application_state") == "unknown"):
                blockers.append({"code": "OUTCOME_UNKNOWN", "request_id": record["request"]["request_id"],
                                 "message": "Daily Work admission, dispatch or application needs owner reconciliation"})
                continue
            if work.get("run", {}).get("state") != "completed":
                continue
            try:
                self._guard()
                observed = self._observed_consumption(record)
                if observed is not None:
                    self._save(key, {"state": "consumed", "consumption": observed})
                    consumed.append(observed)
                    continue
                if record["state"] == "consuming":
                    raise AdmissionError("OUTCOME_UNKNOWN", "Interrupted daily consumption needs explicit owner reconciliation")
                self._validate(record)
                candidate = self._candidate(record, work)
                # The existing boundary owns independent assessment checks and
                # the only provisional writer. Do not synthesize evaluations.
                self.steward._work_artifact(work_id, candidate, record["request"]["source_reference"])
                self._save(key, {"state": "consuming"})
                result = (self.steward.record_candidate(work_id, candidate, authorize=self.authorize)
                          if candidate.get("fields") else
                          self.steward.record_no_evidence(work_id, candidate, authorize=self.authorize))
                self._save(key, {"state": "consumed", "consumption": result})
                consumed.append(result)
            except (AdmissionError, OSError) as error:
                blockers.append(self._blocker(error, request_id=record["request"]["request_id"]))
        return {"consumed": consumed, "blockers": blockers}

    def tick(self) -> dict:
        """Run one bounded scan/admission batch; never start or dispatch Work."""
        summary = {"day": self.app.clock().astimezone(self.steward.zone).date().isoformat(),
                   "due": 0, "admitted": 0, "replayed": 0, "held": [], "blockers": [],
                   "work_receipts": [], "consumed": [], "scan_complete": False}
        try:
            self._guard()
            consumption = self.consume_completed()
            summary["consumed"], summary["blockers"] = consumption["consumed"], consumption["blockers"]
            records = self._records()
            excluded = frozenset(item["request"]["request_id"] for item in records.values()
                                 if item["state"] in {"admitted", "consuming", "consumed", "unknown"})
            for item in records.values():
                if item["state"] == "unknown":
                    summary["blockers"].append({"code": "OUTCOME_UNKNOWN", "request_id": item["request"]["request_id"],
                                                "message": "Daily admission needs explicit owner reconciliation"})
            self._guard()
            due = self.steward.due_check(limit=self.limit, exclude_request_ids=excluded)
            summary["replayed"] += sum(records[_PREFIX + request_id]["state"] != "unknown"
                                       for request_id in due["excluded_request_ids"])
            summary.update(day=due["day"], held=due["held_paths"], due=len(due["work_requests"]),
                           scan_complete=due["scan_complete"])
            for request in due["work_requests"]:
                key = _PREFIX + request["request_id"]
                try:
                    self._guard()
                    path = self.app.resolve_reference(request["source_reference"])
                    other_unknown = [item for old_key, item in self._records().items()
                                     if old_key != key and item.get("path") == path
                                     and item["state"] in {"prepared", "admitting", "consuming", "unknown"}]
                    if other_unknown:
                        raise AdmissionError("OUTCOME_UNKNOWN", "Another unresolved daily request owns this source")
                    now = self.app.clock()
                    with self.app.journal.writer() as writer:
                        record = writer.value["intents"].get(key)
                        if record is None:
                            record = {"state": "prepared", "recorded_at": stamp(now), "day": due["day"], "path": path,
                                      "request": copy.deepcopy(request), "registry_digest": self.steward._registry_digest(),
                                      "policy_digest": self.steward._policy_digest()}
                            self._validate(record)
                            writer.value["intents"][key] = record
                            writer.commit(now)
                        record = copy.deepcopy(record)
                    if record["state"] == "unknown":
                        raise AdmissionError("OUTCOME_UNKNOWN", "Daily admission needs explicit owner reconciliation")
                    if record["state"] in {"admitted", "consuming", "consumed"}:
                        summary["replayed"] += 1
                        continue
                    receipt = self._admit(key, record)
                    summary["work_receipts"].append(receipt)
                    summary["replayed" if receipt.get("replayed") else "admitted"] += 1
                except (AdmissionError, OSError) as error:
                    summary["blockers"].append(self._blocker(error, request_id=request["request_id"]))
        except (AdmissionError, OSError) as error:
            summary["blockers"].append(self._blocker(error))
        return summary

    async def run(self, stop: asyncio.Event, *, enabled: bool = False,
                  interval_seconds: float = 60) -> None:
        """Explicit host-owned activation, with cooperative clean shutdown.

        Each tick uses the selected timezone's current day. Day rollover does
        not reset source/method/registry/policy request identities. Repeated
        ticks drain bounded batches and recover exact owner-local admissions.
        """
        if enabled is not True or not 1 <= interval_seconds <= 86400:
            raise AdmissionError("ACTION_DENIED", "Explicit scheduling enable and bounded interval are required")
        while not stop.is_set():
            active = asyncio.create_task(asyncio.to_thread(self.tick))
            try:
                await asyncio.shield(active)
            except asyncio.CancelledError:
                # Do not return while the owner-local admission/effect thread
                # can still commit. Shutdown never interrupts its writer.
                await active
                raise
            try:
                await asyncio.wait_for(stop.wait(), timeout=interval_seconds)
            except TimeoutError:
                pass

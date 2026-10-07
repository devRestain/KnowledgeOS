"""KnowledgeOS ExOps work admission and owner-side Temporal outbox.

The host client is a transport. This module is the only authority for its
KnowledgeOS work dispatches; it never starts a service when imported.
"""

from __future__ import annotations

import copy
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol

from ..adapters.core import AdmissionError, canonical, digest, load_json
from ..adapters.owner_journal import read_regular, stamp
from ..adapters.paths import resolve_beneath
from .knowledge import KnowledgeApplication
from .team_catalog import OfficerBinding, TeamBinding, TeamCatalog
from .work import PROFILE_KEYS, WorkContracts, record_digest
from .work_methods import WorkMethodCatalog


class ScopedTemporalClient(Protocol):
    async def start(self, dispatch: Any) -> str: ...


@dataclass(frozen=True)
class OwnerActor:
    actor_id: str
    ingress: str


def _key(work_run_id: str) -> str:
    return "exops.work." + work_run_id


def _require(condition: bool, code: str, message: str) -> None:
    if not condition:
        raise AdmissionError(code, message)


class KnowledgeExOps:
    """Owner admission, inspection, delivery and worker-result boundary.

    The creator supplies authenticated actor and Director receipt checks from
    a trusted local composition. A body-provided actor or receipt is never
    sufficient authority.
    """

    def __init__(
        self,
        application: KnowledgeApplication,
        *,
        authorize_actor: Callable[[OwnerActor, str], bool],
        director_admitted: Callable[[Mapping[str, Any]], bool],
        current_team_binding: Callable[[str], TeamBinding | None],
        teams: Mapping[str, TeamBinding],
        worker_identity: str,
        current_officer_binding: Callable[[str], OfficerBinding | None] | None = None,
        officers: Mapping[str, OfficerBinding] | None = None,
        director_method_source_admitted: Callable[[Mapping[str, Any], Mapping[str, Any]], bool] | None = None,
        director_officer_source_admitted: Callable[[Mapping[str, Any], Mapping[str, Any]], bool] | None = None,
    ) -> None:
        _require(callable(authorize_actor) and callable(director_admitted)
                 and callable(current_team_binding),
                 "BINDING_INVALID", "ExOps requires live owner authority callbacks")
        _require(bool(worker_identity), "BINDING_INVALID", "Worker identity is required")
        self.application = application
        self.authorize_actor = authorize_actor
        self.director_admitted = director_admitted
        self.current_team_binding = current_team_binding
        self.teams = dict(teams)
        self.current_officer_binding = current_officer_binding
        self.officers = dict(officers or {})
        self.director_method_source_admitted = director_method_source_admitted
        self.director_officer_source_admitted = director_officer_source_admitted
        self.worker_identity = worker_identity
        self.contracts = WorkContracts(application.core)
        self.catalog = TeamCatalog.load(application.roots.control)
        for team_id, binding in self.teams.items():
            _require(team_id == binding.team_id, "BINDING_INVALID",
                     "Team registry key differs from its owner registration")
            self.catalog.validate_binding(binding)
            self.contracts.record("ArtifactReference", binding.binding_reference)
        _require(not self.officers or callable(self.current_officer_binding),
                 "BINDING_INVALID", "Officer registration needs a current owner binding check")
        for executor_id, binding in self.officers.items():
            _require(executor_id == binding.officer_executor_id,
                     "BINDING_INVALID", "Officer registry key differs from owner registration")
            self.catalog.validate_officer_binding(binding)
            self.contracts.record("ArtifactReference", binding.binding_reference)

    def _guard(self, actor: OwnerActor, action: str) -> None:
        _require(actor.ingress in {"cli", "web"} and bool(actor.actor_id),
                 "UNAUTHENTICATED", "ExOps needs a trusted actor mapping")
        self._current_binding()
        _require(self.authorize_actor(actor, action), "ACTION_DENIED",
                 "Current owner policy denies this actor or action")
        self.application.journal.read()

    def _current_binding(self) -> None:
        self.application.core.verify()
        _require(TeamCatalog.load(self.application.roots.control).digest == self.catalog.digest,
                 "PIN_MISMATCH", "Team definitions changed after ExOps composition")
        _require(load_json(resolve_beneath(self.application.roots.control,
                                           "ops/config/core-adoption.json")) == self.application.core.binding,
                 "PIN_MISMATCH", "Core adoption changed")
        _require(load_json(resolve_beneath(self.application.roots.control,
                                           "ops/policies/owner-control.json")) == self.application.policy,
                 "POLICY_STALE", "Owner policy changed")
        transition = resolve_beneath(self.application.roots.state, "storage-transition.json")
        if transition.exists():
            _require(load_json(transition).get("phase") == "complete", "STORAGE_TRANSITION_PENDING",
                     "Finish exact State preservation before ExOps writes")

    def _team_binding(self, spec: Mapping[str, Any], run: Mapping[str, Any]) -> None:
        if "method_contract_ref" in spec:
            WorkMethodCatalog.load(self.application.roots.control).admitted_method(spec, self.contracts)
        if spec["target_kind"] == "officer":
            registered = self.officers.get(spec["officer_executor_id"])
            current = (self.current_officer_binding(spec["officer_executor_id"])
                       if self.current_officer_binding is not None else None)
            _require(registered is not None and current == registered
                     and dict(run["binding_reference"]) == dict(registered.binding_reference),
                     "SCOPE_DENIED", "Current standalone Officer/EvalOfficer binding differs")
            self.catalog.validate_officer_spec(spec, registered)
            return
        registered = self.teams.get(spec["team_id"])
        current = self.current_team_binding(spec["team_id"])
        _require(registered is not None and current == registered
                 and dict(run["binding_reference"]) == dict(registered.binding_reference),
                 "SCOPE_DENIED", "Current Team and dedicated Manager/EvalOfficer binding differ")
        self.catalog.validate_spec(spec, registered)

    def admit_method_segment(self, work_run_id: str, *, manager_executor_id: str,
                             bundle: Mapping[str, Any],
                             artifacts: Mapping[str, Mapping[str, Any]],
                             source_reference: Mapping[str, Any]) -> dict[str, Any]:
        """Commit an exact Manager proposal before a scoped Profile can call a tool."""
        self._current_binding()
        now = self.application.clock()
        with self.application.journal.writer() as session:
            record = session.value["intents"].get(_key(work_run_id))
            _require(record is not None and record["application_state"] == "started",
                     "STALE_REVISION", "Method Graph has no active owner Work attempt")
            spec, run = record["spec"], record["run"]
            _require(spec["target_kind"] == "team" and spec.get("manager_executor_id") == manager_executor_id
                     and self.director_admitted(spec),
                     "SCOPE_DENIED", "Only the bound Manager may propose this admitted Team Graph")
            self._team_binding(spec, run)
            methods = WorkMethodCatalog.load(self.application.roots.control)
            methods.validate_segment(spec=spec, run=run, bundle=bundle,
                                     contracts=self.contracts, artifacts=artifacts)
            self.catalog.validate_assignments(
                bundle["assignments"], self.teams[spec["team_id"]], spec)
            segment = bundle["segment"]
            self.application.resolve_reference(dict(source_reference))
            _require(dict(source_reference) == record.get("method_source_reference"),
                     "SCOPE_DENIED", "Method Graph source differs from the Director-admitted selection")
            _require(self.director_method_source_admitted is not None
                     and self.director_method_source_admitted(spec, source_reference),
                     "ADMISSION_REQUIRED", "Director source admission is no longer current")
            _require(any(item["content_digest"] == source_reference["content_digest"]
                         for item in segment["input_references"]),
                     "SCOPE_DENIED", "Method source lacks exact admitted Graph input evidence")
            _require(segment["graph_run_id"] not in record.get("admitted_method_segments", {}),
                     "IDEMPOTENCY_CONFLICT", "GraphRun identity already has a method segment")
            next_run = copy.deepcopy(run)
            next_run["revision"] += 1
            next_run["control_revision"] += 1
            next_run["active_graph_run_id"] = segment["graph_run_id"]
            next_run["graph_run_ids"].append(segment["graph_run_id"])
            next_run["segments_started"] += 1
            next_run["profile_invocations"] += len(bundle["assignments"])
            self.contracts.run(next_run, spec, previous=run)
            record["run"] = next_run
            record.setdefault("admitted_method_segments", {})[segment["graph_run_id"]] = {
                "segment": copy.deepcopy(segment),
                "assignments": copy.deepcopy(bundle["assignments"]),
                "method_contract_ref": copy.deepcopy(spec["method_contract_ref"]),
                "source_reference": copy.deepcopy(dict(source_reference)),
                "team_catalog_digest": self.catalog.digest,
                "native_binding_digest": self.teams[spec["team_id"]].native_binding_digest,
            }
            session.value["history"].append({"event": "exops_method_graph_admitted",
                                             "work_run_id": work_run_id,
                                             "sequence": session.value["revision"] + 1})
            session.commit(now)
            return {"work_run_id": work_run_id, "graph_run_id": segment["graph_run_id"],
                    "segment_digest": segment["segment_digest"],
                    "method_contract_ref": copy.deepcopy(spec["method_contract_ref"])}

    def admit_officer_segment(self, work_run_id: str, *, officer_executor_id: str,
                              bundle: Mapping[str, Any],
                              artifacts: Mapping[str, Mapping[str, Any]],
                              source_reference: Mapping[str, Any]) -> dict[str, Any]:
        """Admit one source-bound standalone Officer Graph before a proposal effect."""
        self._current_binding()
        now = self.application.clock()
        with self.application.journal.writer() as session:
            record = session.value["intents"].get(_key(work_run_id))
            _require(record is not None and record["application_state"] == "started",
                     "STALE_REVISION", "Officer Graph has no active owner Work attempt")
            spec, run = record["spec"], record["run"]
            _require(spec["target_kind"] == "officer"
                     and spec["allowed_profiles"][0]["role_id"] == "knowledgeos.normalization_officer"
                     and spec["officer_executor_id"] == officer_executor_id
                     and self.director_admitted(spec),
                     "SCOPE_DENIED", "Only the admitted Officer may bind this Graph")
            self._team_binding(spec, run)
            selected = record.get("officer_source_reference")
            _require(dict(source_reference) == selected
                     and self.director_officer_source_admitted is not None
                     and self.director_officer_source_admitted(spec, source_reference),
                     "ADMISSION_REQUIRED", "Officer source admission is not current")
            source_path = self.application.resolve_reference(dict(source_reference))
            source_bytes = read_regular(resolve_beneath(self.application.roots.vault, source_path))
            _require(source_bytes is not None and digest(source_bytes) == selected["content_digest"],
                     "DIGEST_MISMATCH", "Selected Capture bytes changed during Graph admission")
            segment = bundle["segment"]
            inputs = segment["input_references"]
            _require(len(inputs) == 1 and inputs[0]["content_digest"] == selected["content_digest"]
                     and inputs[0]["source_revision"] == selected["source_revision"]
                     and inputs[0]["size_bytes"] == len(source_bytes)
                     and inputs[0]["media_type"] == "text/markdown",
                     "DIGEST_MISMATCH", "Officer Graph input differs from the selected Capture")
            self.contracts.segment(spec=spec, run=run, artifacts=artifacts,
                                   current_method_ref=None, **bundle)
            assignments = bundle["assignments"]
            _require(len(assignments) == 1
                     and assignments[0]["executor_id"] == officer_executor_id
                     and all(assignments[0][key] == spec["allowed_profiles"][0][key]
                             for key in PROFILE_KEYS),
                     "PIN_MISMATCH", "Officer Graph Profile differs from its WorkSpec")
            _require(not record.get("admitted_officer_segments")
                     and not run["graph_run_ids"], "IDEMPOTENCY_CONFLICT",
                     "Officer Work already admitted a Graph")
            next_run = copy.deepcopy(run)
            next_run["revision"] += 1
            next_run["control_revision"] += 1
            next_run["active_graph_run_id"] = segment["graph_run_id"]
            next_run["graph_run_ids"].append(segment["graph_run_id"])
            next_run["segments_started"] += 1
            next_run["profile_invocations"] += 1
            self.contracts.run(next_run, spec, previous=run)
            record["run"] = next_run
            record["admitted_officer_segments"] = {segment["graph_run_id"]: {
                "segment": copy.deepcopy(segment),
                "assignment": copy.deepcopy(assignments[0]),
                "source_reference": copy.deepcopy(selected),
                "native_binding_digest": self.officers[officer_executor_id].native_binding_digest,
            }}
            session.value["history"].append({"event": "exops_officer_graph_admitted",
                                             "work_run_id": work_run_id,
                                             "sequence": session.value["revision"] + 1})
            session.commit(now)
            return {"work_run_id": work_run_id, "graph_run_id": segment["graph_run_id"],
                    "segment_digest": segment["segment_digest"],
                    "source_digest": selected["content_digest"]}

    def submit_work(self, spec: Mapping[str, Any], run: Mapping[str, Any], *,
                    actor: OwnerActor, action_id: str, expires_at: datetime,
                    method_source_reference: Mapping[str, Any] | None = None,
                    officer_source_reference: Mapping[str, Any] | None = None) -> dict[str, Any]:
        """Persist one exact Director-admitted initial run before acknowledging it."""
        self._guard(actor, "submit_work")
        _require(1 <= len(action_id) <= 96 and action_id.isascii()
                 and all(char.islower() or char.isdigit() or char in "._-" for char in action_id),
                 "INVALID_PAYLOAD", "Stable action ID is invalid")
        now = self.application.clock()
        _require(now.tzinfo is not None and expires_at.tzinfo is not None
                 and now < expires_at and (expires_at - now).total_seconds() <= 86400,
                 "EXPIRED", "Work admission needs a bounded future expiry")
        self.contracts.spec(spec)
        self.contracts.run(run, spec)
        self._team_binding(spec, run)
        if "method_contract_ref" in spec:
            _require(isinstance(method_source_reference, Mapping), "SCOPE_DENIED",
                     "Method work needs an exact Director-admitted source")
            self.application.resolve_reference(dict(method_source_reference))
            _require(self.director_method_source_admitted is not None
                     and self.director_method_source_admitted(spec, method_source_reference),
                     "ADMISSION_REQUIRED", "Director receipt does not admit this method source")
        else:
            _require(method_source_reference is None, "SCOPE_DENIED",
                     "Unpinned work cannot receive a method source")
        if (spec["target_kind"] == "officer"
                and spec["allowed_profiles"][0]["role_id"] == "knowledgeos.normalization_officer"):
            _require(isinstance(officer_source_reference, Mapping)
                     and self.director_officer_source_admitted is not None,
                     "ADMISSION_REQUIRED", "Officer Work needs a Director-selected Capture")
            officer_source_path = self.application.resolve_reference(dict(officer_source_reference))
            _require(officer_source_path.startswith("00_Inbox/Captures/"),
                     "SCOPE_DENIED", "NormalizationOfficer source must be a selected Capture")
            _require(self.director_officer_source_admitted(spec, officer_source_reference),
                     "ADMISSION_REQUIRED", "Director did not admit this Officer source")
        else:
            _require(officer_source_reference is None, "SCOPE_DENIED",
                     "Work without a normalization Officer cannot borrow its Capture source")
        _require(run["state"] == "admitted" and run["revision"] == 1
                 and run["control_revision"] == 1 and run["generation"] == 1
                 and not run["graph_run_ids"] and not run["pending_action_ids"]
                 and not run["unresolved_effect_ids"] and run["segments_started"] == 0
                 and run["profile_invocations"] == 0 and run["repeated_gap_count"] == 0,
                 "SCOPE_DENIED", "New work requires an exact empty admitted WorkRun")
        _require(self.director_admitted(spec), "ADMISSION_REQUIRED",
                 "Current Director receipt did not admit this WorkSpec")
        body = {"spec": dict(spec), "run": dict(run), "actor_id": actor.actor_id,
                "action_id": action_id, "expires_at": stamp(expires_at)}
        if method_source_reference is not None:
            body["method_source_reference"] = dict(method_source_reference)
        if officer_source_reference is not None:
            body["officer_source_reference"] = dict(officer_source_reference)
        content_digest = digest(canonical(body))
        execution_intent = {
            "owner_operation_id": "knowledgeos", "effect_id": action_id,
            "content_digest": content_digest, "generation": run["generation"],
            "fencing_token": run["fencing_token"], "work_run_id": run["work_run_id"],
            "issued_at": stamp(now), "expires_at": stamp(expires_at),
        }
        self.contracts.record("ExecutionIntent", execution_intent)
        key = _key(run["work_run_id"])
        with self.application.journal.writer() as session:
            _require(self.director_admitted(spec), "ADMISSION_REQUIRED",
                     "Director receipt changed before owner commit")
            previous = session.value["intents"].get(key)
            if previous is not None:
                _require(previous["content_digest"] == content_digest,
                         "IDEMPOTENCY_CONFLICT", "WorkRun identity has different content")
                return {**previous["receipt"], "replayed": True}
            _require(not any(item.get("spec", {}).get("work_id") == spec["work_id"]
                             for name, item in session.value["intents"].items()
                             if name.startswith("exops.work.")),
                     "IDEMPOTENCY_CONFLICT", "Work identity already has a live run")
            receipt = {"owner_operation_id": "knowledgeos", "work_id": spec["work_id"],
                       "work_run_id": run["work_run_id"], "action_id": action_id,
                       "content_digest": content_digest, "receipt_state": "accepted",
                       "dispatch_state": "pending", "application_state": "not_started",
                       "replayed": False}
            session.value["intents"][key] = {
                "state": "pending", "recorded_at": stamp(now), "content_digest": content_digest,
                "spec": copy.deepcopy(dict(spec)), "run": copy.deepcopy(dict(run)),
                "method_source_reference": copy.deepcopy(dict(method_source_reference))
                if method_source_reference is not None else None,
                "officer_source_reference": copy.deepcopy(dict(officer_source_reference))
                if officer_source_reference is not None else None,
                "actor_id": actor.actor_id, "action_id": action_id,
                "expires_at": stamp(expires_at), "receipt": receipt,
                "execution_intent": execution_intent,
                "dispatch_state": "pending", "application_state": "not_started",
            }
            session.value["history"].append({"event": "exops_work_admitted",
                                              "work_run_id": run["work_run_id"],
                                              "sequence": session.value["revision"] + 1})
            session.commit(now)
            return copy.deepcopy(receipt)

    def read_work(self, work_run_id: str, *, actor: OwnerActor) -> dict[str, Any]:
        self._guard(actor, "read_work")
        record = self.application.journal.read()["intents"].get(_key(work_run_id))
        _require(record is not None, "NOT_FOUND", "WorkRun is unavailable")
        return {"spec": copy.deepcopy(record["spec"]), "run": copy.deepcopy(record["run"]),
                "receipt": copy.deepcopy(record["receipt"]),
                "dispatch_state": record["dispatch_state"],
                "application_state": record["application_state"],
                "observed_at": self.application.journal.read()["observed_at"]}

    def authorize_dispatch(self, *, work_run_id: str, action_id: str,
                           content_digest: str) -> dict[str, Any]:
        """Current owner callback for the host Temporal client."""
        self._current_binding()
        record = self.application.journal.read()["intents"].get(_key(work_run_id))
        _require(record is not None and record["action_id"] == action_id
                 and record["content_digest"] == content_digest
                 and record["dispatch_state"] == "pending"
                 and self.application.clock() < datetime.fromisoformat(record["expires_at"])
                 and self.director_admitted(record["spec"]),
                 "DISPATCH_DENIED", "No current owner-authorized pending dispatch")
        self.contracts.spec(record["spec"])
        self.contracts.run(record["run"], record["spec"])
        self._team_binding(record["spec"], record["run"])
        if "method_contract_ref" in record["spec"]:
            _require(self.director_method_source_admitted is not None
                     and self.director_method_source_admitted(
                         record["spec"], record["method_source_reference"]),
                     "ADMISSION_REQUIRED", "Director source admission changed before dispatch")
        if record.get("officer_source_reference") is not None:
            _require(self.director_officer_source_admitted is not None
                     and self.director_officer_source_admitted(
                         record["spec"], record["officer_source_reference"]),
                     "ADMISSION_REQUIRED", "Officer source admission changed before dispatch")
        return copy.deepcopy(record)

    def record_dispatch(self, work_run_id: str, *, action_id: str,
                        outcome: str, workflow_id: str | None = None) -> None:
        _require(outcome in {"accepted", "unknown", "rejected"}, "INVALID_PAYLOAD",
                 "Unsupported Temporal transport outcome")
        now = self.application.clock()
        with self.application.journal.writer() as session:
            record = session.value["intents"].get(_key(work_run_id))
            _require(record is not None and record["action_id"] == action_id,
                     "STALE_REVISION",
                     "Dispatch target changed")
            if record["dispatch_state"] == "accepted" and outcome == "accepted":
                _require(workflow_id is None or record.get("workflow_id", workflow_id) == workflow_id,
                         "IDEMPOTENCY_CONFLICT", "Temporal workflow identity changed")
                return
            _require(record["dispatch_state"] == "pending", "STALE_REVISION",
                     "Dispatch already has an outcome requiring reconciliation")
            record["dispatch_state"] = outcome
            record["receipt"]["dispatch_state"] = outcome
            if workflow_id is not None:
                record["workflow_id"] = workflow_id
            if outcome == "unknown":
                record["state"] = "unknown"
            elif outcome == "rejected":
                record["state"] = "rejected"
            session.commit(now)

    def worker_started(self, work_run_id: str, *, worker_identity: str,
                       action_id: str, content_digest: str,
                       workflow_id: str) -> dict[str, Any]:
        _require(worker_identity == self.worker_identity, "UNAUTHENTICATED",
                 "Only the bound Operation Worker can report application")
        self._current_binding()
        now = self.application.clock()
        with self.application.journal.writer() as session:
            record = session.value["intents"].get(_key(work_run_id))
            _require(record is not None and record["action_id"] == action_id
                     and record["content_digest"] == content_digest
                     and record["dispatch_state"] in {"pending", "accepted"}
                     and workflow_id == f"knowledgeos/{work_run_id}"
                     and record.get("workflow_id", workflow_id) == workflow_id
                     and now < datetime.fromisoformat(record["expires_at"])
                     and self.director_admitted(record["spec"]),
                     "STALE_REVISION", "Worker start lacks a correlated owner dispatch")
            self._team_binding(record["spec"], record["run"])
            # An actual Worker invocation can precede the host client's
            # start acknowledgement. Its exact Workflow ID resolves that race.
            record["dispatch_state"] = "accepted"
            record["receipt"]["dispatch_state"] = "accepted"
            record["workflow_id"] = workflow_id
            _require(record["application_state"] != "started", "OUTCOME_UNKNOWN",
                     "Started Activity must be reconciled before any re-execution")
            _require(record["application_state"] == "not_started", "STALE_REVISION",
                     "Work application already has another outcome")
            previous = record["run"]
            current = {**previous, "revision": previous["revision"] + 1,
                       "control_revision": previous["control_revision"] + 1,
                       "state": "running"}
            self.contracts.run(current, record["spec"], previous=previous)
            record["run"] = current
            record["application_state"] = "started"
            record["receipt"]["application_state"] = "started"
            session.commit(now)
            return copy.deepcopy(current)

    def worker_finished(self, work_run_id: str, *, worker_identity: str,
                        action_id: str, next_run: Mapping[str, Any],
                        assessment: Mapping[str, Any] | None = None,
                        segments: tuple[Mapping[str, Any], ...] = (),
                        evaluations: tuple[Mapping[str, Any], ...] = (),
                        artifacts: Mapping[str, Mapping[str, Any]] | None = None,
                        checkpoints: Mapping[str, Mapping[str, Any]] | None = None,
                        candidate_assessor_id: str | None = None) -> dict[str, Any]:
        _require(worker_identity == self.worker_identity, "UNAUTHENTICATED",
                 "Only the bound Operation Worker can record results")
        now = self.application.clock()
        with self.application.journal.writer() as session:
            record = session.value["intents"].get(_key(work_run_id))
            _require(record is not None and record["action_id"] == action_id
                     and record["application_state"] == "started", "STALE_REVISION",
                     "Worker result has no active owner attempt")
            if "method_contract_ref" in record["spec"]:
                admitted = record.get("admitted_method_segments", {})
                _require(admitted and {item["graph_run_id"] for item in segments} == set(admitted)
                         and all(item["graph_run_id"] in admitted
                                          and dict(item) == admitted[item["graph_run_id"]]["segment"]
                                          for item in segments),
                         "SCOPE_DENIED", "Worker result lacks the admitted method Graph lineage")
                if next_run["state"] in {"awaiting_human", "completed"}:
                    source = record["method_source_reference"]
                    source_path = self.application.resolve_reference(dict(source))
                    proposals = [intent for intent in session.value["intents"].values()
                                 if isinstance(intent, dict)
                                 and intent.get("state") == "completed"
                                 and intent.get("source_path") == source_path
                                 and intent.get("source_digest") == source["content_digest"]
                                 and (receipt := session.value["receipts"].get(intent.get("effect_id")))
                                 and receipt.get("execution", {}).get("outcome") == "succeeded"
                                 and receipt["execution"].get("result_digest") == intent["content_digest"]]
                    _require(bool(proposals), "SCOPE_DENIED",
                             "Method result lacks a completed source-bound proposal receipt")
                    if next_run["state"] == "completed":
                        _require(any(next_run["result_reference"]["content_digest"]
                                     == item["content_digest"] for item in proposals),
                                 "SCOPE_DENIED", "Completed method result differs from proposal digest")
            if record.get("officer_source_reference") is not None:
                admitted = record.get("admitted_officer_segments", {})
                _require(len(admitted) == 1
                         and {item["graph_run_id"] for item in segments} == set(admitted)
                         and all(dict(item) == admitted[item["graph_run_id"]]["segment"]
                                 for item in segments),
                         "SCOPE_DENIED", "Officer result lacks the admitted Graph lineage")
                if next_run["state"] == "completed":
                    graph_run_id = next(iter(admitted))
                    source = admitted[graph_run_id]["source_reference"]
                    proposal = next_run["result_reference"]
                    checkpoint = record_digest({
                        "graph_run_id": graph_run_id,
                        "source_digest": source["content_digest"],
                        "proposal_digest": proposal["content_digest"],
                    })
                    bound = {"work_run_id": work_run_id,
                             "work_spec_digest": record["spec"]["spec_digest"],
                             "graph_run_id": graph_run_id,
                             "executor_id": record["spec"]["officer_executor_id"],
                             "source_reference": source}
                    matching = [intent for intent in session.value["intents"].values()
                                if isinstance(intent, dict)
                                and intent.get("state") == "completed"
                                and intent.get("work_binding") == bound
                                and intent.get("content_digest") == proposal["content_digest"]
                                and (receipt := session.value["receipts"].get(intent.get("effect_id")))
                                and receipt.get("execution", {}).get("outcome") == "succeeded"
                                and receipt["execution"].get("result_digest") == proposal["content_digest"]]
                    _require(len(matching) == 1,
                             "SCOPE_DENIED", "Officer result lacks its exact proposal effect")
                    proposal_path = matching[0]["artifact_path"]
                    current_proposal = self.application.reference(proposal_path, proposal=True)
                    proposal_bytes = read_regular(resolve_beneath(
                        self.application.roots.vault, proposal_path))
                    _require(proposal_bytes is not None
                             and current_proposal["content_digest"] == proposal["content_digest"]
                             and current_proposal["source_revision"] == proposal["source_revision"]
                             and len(proposal_bytes) == proposal["size_bytes"],
                             "DIGEST_MISMATCH", "Proposal artifact differs from evaluated evidence")
                    _require(checkpoints is not None
                             and checkpoints.get(graph_run_id) == checkpoint
                             and len(evaluations) == 1
                             and evaluations[0]["checkpoint_digest"] == checkpoint
                             and evaluations[0]["evidence_references"] == [proposal]
                             and assessment is not None
                             and len(assessment["criterion_outcomes"]) == 1
                             and assessment["criterion_outcomes"][0]["evidence_references"] == [proposal],
                             "DIGEST_MISMATCH", "EvalOfficer checkpoint or proposal differs")
            self.contracts.run(next_run, record["spec"], previous=record["run"],
                               assessment=assessment, segments=segments,
                               evaluations=evaluations, artifacts=artifacts,
                               checkpoints=checkpoints,
                               candidate_assessor_id=candidate_assessor_id)
            _require(next_run["state"] in {"awaiting_human", "paused", "completed"},
                     "SCOPE_DENIED", "Worker result needs a safe waiting or terminal state")
            record["run"] = copy.deepcopy(dict(next_run))
            record["application_state"] = "recorded"
            record["receipt"]["application_state"] = "recorded"
            record["state"] = "recorded"
            intent = record["execution_intent"]
            execution_receipt = {
                "owner_operation_id": "knowledgeos", "effect_id": intent["effect_id"],
                "content_digest": intent["content_digest"],
                "generation": intent["generation"], "fencing_token": intent["fencing_token"],
                "work_run_id": work_run_id, "outcome": "succeeded",
                "observed_at": stamp(now), "result_digest": digest(canonical(next_run)),
            }
            self.contracts.record("ExecutionReceipt", execution_receipt)
            session.value["receipts"]["exops.work." + action_id] = {
                "recorded_at": stamp(now), "unresolved": False,
                "receipt": execution_receipt,
            }
            session.commit(now)
            return copy.deepcopy(record["receipt"])

    def worker_unknown(self, work_run_id: str, *, worker_identity: str,
                       action_id: str) -> None:
        _require(worker_identity == self.worker_identity, "UNAUTHENTICATED",
                 "Only the bound Operation Worker can report uncertainty")
        now = self.application.clock()
        with self.application.journal.writer() as session:
            record = session.value["intents"].get(_key(work_run_id))
            _require(record is not None and record["action_id"] == action_id
                     and record["application_state"] == "started", "STALE_REVISION",
                     "Unknown outcome has no active owner attempt")
            record["application_state"] = "unknown"
            record["receipt"]["application_state"] = "unknown"
            record["state"] = "unknown"
            intent = record["execution_intent"]
            execution_receipt = {
                "owner_operation_id": "knowledgeos", "effect_id": intent["effect_id"],
                "content_digest": intent["content_digest"],
                "generation": intent["generation"], "fencing_token": intent["fencing_token"],
                "work_run_id": work_run_id, "outcome": "outcome_unknown",
                "observed_at": stamp(now),
            }
            self.contracts.record("ExecutionReceipt", execution_receipt)
            session.value["receipts"]["exops.work." + action_id] = {
                "recorded_at": stamp(now), "unresolved": True,
                "receipt": execution_receipt,
            }
            session.commit(now)

"""Graphless owner control shared by trusted CLI and constrained MCP ingress."""

from __future__ import annotations

import base64
import copy
import hashlib
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from .. import proposals, retrieval
from ..adapters.artifacts import create_artifact
from ..adapters.core import PINS, AdmissionError, CoreContracts, canonical, digest, load_json
from ..adapters.owner_journal import (
    CURRENT_OWNER_INTENT,
    OwnerJournal,
    read_regular,
    replace_exact,
    stamp,
    utc_now,
)
from ..adapters.paths import ResolvedPaths, resolve_beneath
from ..domain.normalization import prepare_normalization


@dataclass(frozen=True)
class LocalIdentity:
    actor_id: str
    ingress: str
    owner_operation_id: str = "knowledgeos"


@dataclass(frozen=True)
class DomainHandlers:
    search: Callable[..., Any] = retrieval.search
    retrieve: Callable[..., Any] = retrieval.retrieve
    prepare: Callable[..., Any] = prepare_normalization
    inspect: Callable[..., Any] = proposals.review_proposals


def evaluate_proposal(report: dict[str, Any], effect_id: str, checkpoint: str) -> dict[str, Any]:
    passed = report.get("status") == "PASS" and report.get("count") == 1
    return {
        "owner_operation_id": "knowledgeos", "run_id": effect_id,
        "checkpoint_digest": checkpoint, "criteria_revision": 1,
        "criterion_ids": ["proposal.valid"],
        "passed_criterion_ids": ["proposal.valid"] if passed else [],
        "failed_criterion_ids": [] if passed else ["proposal.valid"],
        "not_run_criterion_ids": [],
    }


def failure(operation: str, error: Exception, *, effect_unknown: bool = False) -> tuple[dict[str, Any], int]:
    return {
        "status": "FAIL", "operation": operation, "provider_called": False,
        "mutation_performed": False, "canonical_target_changed": False,
        "execution_outcome": "unknown" if effect_unknown else "not_started",
        "acceptance_state": "unknown" if effect_unknown else "not_evaluated",
        "effects": {"pending_artifact_created": "unknown" if effect_unknown else False, "canonical_note_changed": False},
        "errors": [{"code": getattr(error, "code", "OWNER_INVALID"), "message": str(error)}],
    }, 10


@dataclass(frozen=True)
class KnowledgeApplication:
    roots: ResolvedPaths
    core: CoreContracts
    identity: LocalIdentity
    policy: dict[str, Any]
    journal: OwnerJournal
    handlers: DomainHandlers
    evaluator: Callable[..., dict[str, Any]]
    clock: Callable[[], datetime] = utc_now
    fault_hook: Callable[[str], None] = lambda _phase: None

    @classmethod
    def from_roots(
        cls, roots: ResolvedPaths, *, identity: LocalIdentity | None = None,
        handlers: DomainHandlers | None = None, evaluator: Callable[..., dict[str, Any]] = evaluate_proposal,
        clock: Callable[[], datetime] = utc_now, fault_hook: Callable[[str], None] = lambda _phase: None,
    ) -> KnowledgeApplication:
        """Compose trusted ports without creating files or starting a process."""
        if not isinstance(roots, ResolvedPaths):
            raise TypeError("application requires five resolved independent roots")
        binding = load_json(resolve_beneath(roots.control, "ops/config/core-adoption.json"))
        core = CoreContracts(roots.core, binding)
        core.verify()
        core.manifest()
        principal = identity or LocalIdentity("local.operator", "operator")
        policy = load_json(resolve_beneath(roots.control, "ops/policies/owner-control.json"))
        expected = {"schema_version", "operation_id", "operator_actions", "mcp_actions", "gateway_actions", "external_transport"}
        if set(policy) != expected or policy["schema_version"] != 1 or policy["operation_id"] != "knowledgeos":
            raise AdmissionError("POLICY_INVALID", "owner policy identity or schema is invalid")
        journal = OwnerJournal(roots, binding)
        journal.read()
        result = cls(roots, core, principal, policy, journal, handlers or DomainHandlers(), evaluator, clock, fault_hook)
        result.guard("inspect")
        return result

    def guard(self, action: str) -> None:
        if self.identity.owner_operation_id != "knowledgeos" or self.identity.ingress not in {"operator", "mcp"}:
            raise AdmissionError("OWNER_DENIED", "trusted local identity must name this owner and ingress")
        self.core.verify()
        if load_json(resolve_beneath(self.roots.control, "ops/config/core-adoption.json")) != self.core.binding:
            raise AdmissionError("PIN_MISMATCH", "trusted adoption pins changed; reconstruct the application")
        current = load_json(resolve_beneath(self.roots.control, "ops/policies/owner-control.json"))
        if current != self.policy:
            raise AdmissionError("POLICY_STALE", "owner policy changed; reconstruct the trusted application")
        if action not in self.policy[f"{self.identity.ingress}_actions"]:
            raise AdmissionError("ACTION_DENIED", "owner policy denies this action for the trusted ingress")
        self.journal.read()

    def reference(self, relative: str, *, proposal: bool = False) -> dict[str, Any]:
        path = resolve_beneath(self.roots.vault, relative)
        raw = read_regular(path)
        if raw is None:
            raise AdmissionError("RESOURCE_UNAVAILABLE", "logical resource is unavailable")
        reference = {
            "owner_operation_id": "knowledgeos", "resource_kind": "artifact" if proposal else "vault",
            "logical_id": ("proposal." if proposal else "note.") + hashlib.sha256(relative.encode()).hexdigest()[:32],
            "source_revision": digest(raw)[7:], "content_digest": digest(raw),
            "classification": "internal", "availability": "available_by_owner_export",
        }
        self.core.validate("ResourceReference", reference)
        return reference

    def resolve_reference(self, reference: dict[str, Any], *, proposal: bool = False) -> str:
        self.core.validate("ResourceReference", reference)
        if reference["owner_operation_id"] != "knowledgeos" or reference["resource_kind"] != ("artifact" if proposal else "vault"):
            raise AdmissionError("OWNER_DENIED", "resource belongs to another owner or kind")
        if reference["availability"] != "available_by_owner_export":
            raise AdmissionError("RESOURCE_UNAVAILABLE", "resource is not available by owner export")
        root = resolve_beneath(self.roots.vault, "01_AI_Review/Pending") if proposal else self.roots.vault
        prefix = "proposal." if proposal else "note."
        for path in sorted(root.rglob("*.md")):
            relative = path.relative_to(self.roots.vault).as_posix()
            logical_id = prefix + hashlib.sha256(relative.encode()).hexdigest()[:32]
            if logical_id != reference["logical_id"]:
                continue
            self._source_policy(relative, proposal=proposal)
            if self.reference(relative, proposal=proposal) != reference:
                raise AdmissionError("RESOURCE_STALE", "resource digest or revision changed")
            return relative
        raise AdmissionError("RESOURCE_UNAVAILABLE", "logical resource is unavailable")

    def _source_policy(self, relative: str, *, proposal: bool = False) -> None:
        resolve_beneath(self.roots.vault, relative)
        if proposal:
            if not relative.startswith("01_AI_Review/Pending/") or len(relative.split("/")) != 3:
                raise AdmissionError("PATH_DENIED", "inspection requires one Pending artifact")
        elif any(part.startswith(".") for part in relative.split("/")) or relative.startswith(("99_System/", "01_AI_Review/")):
            raise AdmissionError("SCOPE_DENIED", "source is outside the owner domain export policy")

    def _read(self, action: str, query: str, **kwargs: Any) -> tuple[dict[str, Any], int]:
        try:
            self.guard(action)
            report, code = getattr(self.handlers, action)(self.roots, query, **kwargs)
            for candidate in report.get("candidates", []):
                candidate["resource_reference"] = self.reference(candidate["path"])
            report["execution_outcome"] = "completed" if code == 0 else "failed"
            report["acceptance_state"] = "not_evaluated"
            return report, code
        except (ValueError, OSError) as error:
            return failure(action, error)

    def search(self, query: str, **kwargs: Any) -> tuple[dict[str, Any], int]:
        return self._read("search", query, **kwargs)

    def retrieve(self, query: str, **kwargs: Any) -> tuple[dict[str, Any], int]:
        return self._read("retrieve", query, **kwargs)

    def inspect_proposal(self, proposal: str | dict[str, Any]) -> tuple[dict[str, Any], int]:
        return self.review_proposals(proposal_path=proposal)

    def review_proposals(self, proposal_path: str | dict[str, Any] | None = None) -> tuple[dict[str, Any], int]:
        try:
            self.guard("inspect")
            relative = self.resolve_reference(proposal_path, proposal=True) if isinstance(proposal_path, dict) else proposal_path
            if relative is not None:
                self._source_policy(relative, proposal=True)
            report, code = self.handlers.inspect(self.roots, proposal_path=relative)
            for item in report.get("proposals", []):
                item["resource_reference"] = self.reference(item["proposal_path"], proposal=True)
            report["execution_outcome"] = "completed" if code == 0 else "failed"
            report["acceptance_state"] = "pass" if code == 0 else "fail"
            return report, code
        except (ValueError, OSError) as error:
            return failure("inspect", error)

    def _target_digest(self, source: str, proposal: str) -> str:
        bundle: dict[str, Any] = {"pins": PINS, "source": self.reference(source), "proposal": self.reference(proposal, proposal=True)}
        for root_name in ("ops/policies", "ops/actions", "ops/prompts", "ops/schemas", "blueprint"):
            directory = resolve_beneath(self.roots.control, root_name)
            bundle[root_name] = {path.relative_to(directory).as_posix(): digest(read_regular(resolve_beneath(directory, path.relative_to(directory).as_posix())) or b"") for path in sorted(directory.rglob("*")) if path.is_file()}
        return digest(canonical(bundle))

    def _evaluate(self, report: dict[str, Any], effect_id: str, checkpoint: str) -> tuple[dict[str, Any], str]:
        evaluation = self.evaluator(report, effect_id, checkpoint)
        self.core.validate("RunEvaluation", evaluation)
        if any(evaluation[key] != expected for key, expected in {
            "owner_operation_id": "knowledgeos", "run_id": effect_id,
            "checkpoint_digest": checkpoint, "criteria_revision": 1,
            "criterion_ids": ["proposal.valid"],
        }.items()):
            raise AdmissionError("EVALUATION_MISMATCH", "evaluation must bind the exact owner, effect, checkpoint and declared criteria")
        groups = [set(evaluation[key]) for key in ("passed_criterion_ids", "failed_criterion_ids", "not_run_criterion_ids")]
        if set.union(*groups) != {"proposal.valid"} or sum(len(group) for group in groups) != 1:
            raise AdmissionError("EVALUATION_MISMATCH", "each declared criterion requires exactly one observed state")
        state = "fail" if groups[1] else "partial" if groups[2] else "pass"
        return evaluation, state

    def _human_request(self, intent: dict[str, Any], now: datetime) -> dict[str, Any]:
        result = {
            "request_id": "human." + intent["effect_id"].split(".")[-1], "request_revision": 1,
            "owner_operation_id": "knowledgeos", "question": "Approve this exact normalization proposal?",
            "impact": "Approval records a pending canonical apply intent; a separate explicit apply is required.",
            "allowed_actions": ["approve", "reject", "inspect"],
            "target_digest": self._target_digest(intent["source_path"], intent["artifact_path"]),
            "semantic_version": "0.6.0", "semantic_digest": PINS["semantic_digest"],
            "evidence_reference_ids": [self.reference(intent["artifact_path"], proposal=True)["logical_id"]],
            "expires_at": stamp(now + timedelta(hours=12)), "lifecycle": "pending",
        }
        self.core.validate("HumanActionRequest", result)
        return result

    def _receipt(self, intent: dict[str, Any], now: datetime, outcome: str, result_digest: str | None = None) -> dict[str, Any]:
        result = {key: intent[key] for key in ("effect_id", "content_digest", "fencing_token", "generation", "owner_operation_id")}
        result.update(outcome=outcome, observed_at=stamp(now))
        if result_digest is not None:
            result["result_digest"] = result_digest
        self.core.validate("ExecutionReceipt", result)
        return result

    def create_normalize_proposal(
        self, *, source_path: str | None = None, expected_sha256: str | None = None,
        source_reference: dict[str, Any] | None = None, **options: Any,
    ) -> tuple[dict[str, Any], int]:
        dispatched = False
        try:
            self.guard("propose")
            if any(value is not None for value in options.values()):
                raise AdmissionError("ACTION_DENIED", "normalization accepts one complete typed source")
            if source_reference is not None:
                if source_path is not None or expected_sha256 is not None:
                    raise AdmissionError("AUTHORITY_DENIED", "logical reference cannot be combined with physical selectors")
                source_path = self.resolve_reference(source_reference)
                expected_sha256 = source_reference["content_digest"][7:]
            if source_path is None or expected_sha256 is None:
                raise AdmissionError("INPUT_INVALID", "source selection and digest are required")
            self._source_policy(source_path)
            with self.journal.writer() as session:
                self.guard("propose")
                prepared = self.handlers.prepare(self.roots, source_path, expected_sha256)
                if prepared["status"] == "NO_CHANGE":
                    return {**prepared, "source": source_path, "source_reference": self.reference(source_path), "provider_called": False, "mutation_performed": False, "canonical_target_changed": False, "execution_outcome": "completed", "acceptance_state": "not_applicable"}, 0
                content = prepared.pop("artifact_bytes")
                effect_id = "normalize." + prepared["proposal"]["proposal_id"]
                prior = session.value["intents"].get(effect_id)
                if prior is not None:
                    if prior["state"] != "completed":
                        raise AdmissionError("OUTCOME_UNKNOWN", "owner must reconcile the existing intent before another dispatch")
                    if read_regular(resolve_beneath(self.roots.vault, prior["artifact_path"])) != content:
                        raise AdmissionError("ARTIFACT_CONFLICT", "receipt artifact differs from frozen bytes")
                    request = session.value["human_requests"].get(prior["human_request_id"])
                    return {**prepared, "status": "NO_OP", "replayed": True, "created": False, "mutation_performed": False, "effects": {"pending_artifact_created": False, "canonical_note_changed": False}, "resource_reference": self.reference(prior["artifact_path"], proposal=True), "source_reference": self.reference(source_path), "human_action_request": request, "execution_outcome": "completed", "acceptance_state": prior.get("acceptance_state", "unknown"), "canonical_target_changed": False}, 0
                now = self.clock()
                effect = {
                    "effect_id": effect_id, "owner_operation_id": "knowledgeos", "content_digest": digest(content),
                    "generation": session.generation, "fencing_token": f"generation.g{session.generation}",
                    "issued_at": stamp(now), "expires_at": stamp(now + timedelta(hours=12)),
                }
                self.core.validate("ExecutionIntent", effect)
                intent = {**effect, "state": "pending", "recorded_at": stamp(now), "source_path": source_path,
                          "source_digest": "sha256:" + expected_sha256, "artifact_path": prepared["proposal_path"],
                          "frozen_artifact": base64.b64encode(content).decode(), "human_request_id": "human." + prepared["proposal"]["proposal_id"],
                          "acceptance_state": "not_evaluated"}
                session.value["intents"][effect_id] = intent
                session.commit(now)
                self.fault_hook("intent_committed")
                session.fence()
                self.guard("propose")
                if self.reference(source_path)["content_digest"] != intent["source_digest"]:
                    raise AdmissionError("RESOURCE_STALE", "source changed before artifact dispatch")
                intent["state"] = "unknown"
                session.commit(now)
                session.fence()
                dispatched = True
                created = create_artifact(self.roots.vault, intent["artifact_path"], content)
                self.fault_hook("artifact_created")
                session.fence()
                self.guard("propose")
                if self.reference(source_path)["content_digest"] != intent["source_digest"]:
                    raise AdmissionError("RESOURCE_STALE", "source changed after artifact dispatch; observe the exact intent before continuing")
                inspection, _ = self.handlers.inspect(self.roots, proposal_path=intent["artifact_path"])
                evaluation, acceptance = self._evaluate(inspection, effect_id, digest(content))
                intent["state"] = "completed"
                intent["acceptance_state"] = acceptance
                request = self._human_request(intent, now)
                session.value["human_requests"][request["request_id"]] = request
                session.value["receipts"][effect_id] = {"recorded_at": stamp(now), "execution": self._receipt(intent, now, "succeeded", digest(content)), "evaluation": evaluation}
                session.value["history"].append({"event": "proposal_created", "effect_id": effect_id, "sequence": session.value["revision"] + 1})
                session.commit(now)
                return {**prepared, "created": created, "replayed": not created, "mutation_performed": created,
                        "canonical_target_changed": False, "effects": {"pending_artifact_created": created, "canonical_note_changed": False},
                        "resource_reference": self.reference(intent["artifact_path"], proposal=True), "source_reference": self.reference(source_path),
                        "human_action_request": request, "execution_outcome": "completed", "acceptance_state": intent["acceptance_state"]}, 0
        except (ValueError, OSError) as error:
            return failure("propose", error, effect_unknown=dispatched)

    def human_decision(self, response: dict[str, Any], *, reason: str | None = None) -> tuple[dict[str, Any], int]:
        try:
            action = response.get("chosen_action", "")
            self.guard(action)
            self.core.validate("HumanActionResponse", response)
            if reason is not None and (not isinstance(reason, str) or len(reason) > 2000):
                raise AdmissionError("REASON_INVALID", "human reason must be bounded text")
            reason_digest = digest(reason.encode()) if reason is not None else None
            if response["actor_id"] != self.identity.actor_id or response["owner_operation_id"] != "knowledgeos":
                raise AdmissionError("AUTHORITY_DENIED", "human response must use the trusted local actor")
            with self.journal.writer() as session:
                request = session.value["human_requests"].get(response["request_id"])
                if request is None:
                    raise AdmissionError("HUMAN_TARGET_UNKNOWN", "human request is unavailable")
                intent = next(item for item in session.value["intents"].values() if item.get("human_request_id") == request["request_id"])
                now = self.clock()
                if intent["state"] != "completed" or intent.get("acceptance_state") != "pass":
                    raise AdmissionError("ACCEPTANCE_REQUIRED", "human approval requires a completed and passing proposal")
                if request["target_digest"] != self._target_digest(intent["source_path"], intent["artifact_path"]) or response["target_digest"] != request["target_digest"] or response["request_revision"] != request["request_revision"]:
                    raise AdmissionError("HUMAN_TARGET_STALE", "source, proposal, policy, schema or semantic target changed")
                if now >= datetime.fromisoformat(request["expires_at"]) or action not in request["allowed_actions"]:
                    raise AdmissionError("HUMAN_TARGET_STALE", "human request expired or action is not allowed")
                if action == "inspect":
                    return self.review_proposals(proposal_path=intent["artifact_path"])
                key = response["idempotency_key"]
                prior = session.value["decisions"].get(request["request_id"])
                if prior is not None:
                    if prior["response"] != response or prior["reason_digest"] != reason_digest:
                        raise AdmissionError("HUMAN_RESPONSE_CONFLICT", "human request already has another decision")
                    return {"status": "NO_OP", "replayed": True, "decision": prior, "canonical_target_changed": False}, 0
                if any(item["response"]["idempotency_key"] == key for item in session.value["decisions"].values()):
                    raise AdmissionError("HUMAN_RESPONSE_CONFLICT", "response key belongs to another request")
                request["lifecycle"] = "resolved"
                decision = {"response": copy.deepcopy(response), "reason_digest": reason_digest, "recorded_at": stamp(now), "pending_apply": action == "approve", "target_digest": request["target_digest"]}
                session.value["decisions"][request["request_id"]] = decision
                session.value["history"].append({"event": "human_" + action, "request_id": request["request_id"], "sequence": session.value["revision"] + 1})
                session.commit(now)
                return {"status": "PASS", "replayed": False, "decision": decision, "canonical_target_changed": False, "execution_outcome": "not_started"}, 0
        except (ValueError, OSError, StopIteration) as error:
            return failure("human_decision", error)

    def decide_proposal(self, proposal_path: str, expected_sha256: str, action: str, *, reason: str | None = None) -> tuple[dict[str, Any], int]:
        try:
            self.guard(action)
            self._source_policy(proposal_path, proposal=True)
            if self.reference(proposal_path, proposal=True)["content_digest"] != "sha256:" + expected_sha256:
                raise AdmissionError("HUMAN_TARGET_STALE", "proposal digest changed")
            value = self.journal.read()
            intent = next(item for item in value["intents"].values() if item.get("artifact_path") == proposal_path)
            request = value["human_requests"][intent["human_request_id"]]
            response = {
                "request_id": request["request_id"], "request_revision": request["request_revision"],
                "target_digest": request["target_digest"], "actor_id": self.identity.actor_id,
                "owner_operation_id": "knowledgeos", "chosen_action": action,
                "idempotency_key": request["request_id"] + ":" + action, "response_id": "response." + intent["effect_id"].split(".")[-1],
                "received_at": stamp(self.clock()), "submission_state": "submitted",
            }
            prior = value["decisions"].get(request["request_id"])
            if prior is not None and prior["response"]["chosen_action"] == action:
                response = copy.deepcopy(prior["response"])
            return self.human_decision(response, reason=reason)
        except (ValueError, OSError, StopIteration, KeyError) as error:
            return failure(action, error)

    def recover(self) -> tuple[dict[str, Any], int]:
        try:
            self.guard("recover")
            observations: list[dict[str, Any]] = []
            with self.journal.writer() as session:
                now = self.clock()
                for effect_id, intent in session.value["intents"].items():
                    if intent["state"] == "unknown" and "canonical_path" in intent:
                        observed = read_regular(resolve_beneath(self.roots.vault, intent["canonical_path"]))
                        if observed is not None and digest(observed) == intent["after_digest"]:
                            intent["state"] = "completed"
                            session.value["decisions"][intent["human_request_id"]]["pending_apply"] = False
                            session.value["receipts"][effect_id] = {"recorded_at": stamp(now), "execution": self._receipt(intent, now, "succeeded", digest(observed))}
                        else:
                            session.value["receipts"][effect_id] = {"recorded_at": stamp(now), "unresolved": True, "execution": self._receipt(intent, now, "outcome_unknown")}
                        observations.append({"effect_id": effect_id, "state": intent["state"]})
                        continue
                    if intent["state"] not in {"pending", "unknown"} or "artifact_path" not in intent:
                        continue
                    observed = read_regular(resolve_beneath(self.roots.vault, intent["artifact_path"]))
                    expected = base64.b64decode(intent["frozen_artifact"], validate=True)
                    if observed == expected:
                        report, _ = self.handlers.inspect(self.roots, proposal_path=intent["artifact_path"])
                        evaluation, acceptance = self._evaluate(report, effect_id, digest(expected))
                        intent["state"] = "completed"
                        intent["acceptance_state"] = acceptance
                        if intent["human_request_id"] not in session.value["human_requests"] and acceptance == "pass" and self.reference(intent["source_path"])["content_digest"] == intent["source_digest"]:
                            session.value["human_requests"][intent["human_request_id"]] = self._human_request(intent, now)
                        session.value["receipts"][effect_id] = {"recorded_at": stamp(now), "execution": self._receipt(intent, now, "succeeded", digest(expected)), "evaluation": evaluation}
                    elif observed is None and intent["state"] == "pending":
                        intent["state"] = "cancelled"
                        session.value["receipts"][effect_id] = {"recorded_at": stamp(now), "execution": self._receipt(intent, now, "failed")}
                    else:
                        intent["state"] = "unknown"
                        session.value["receipts"][effect_id] = {"recorded_at": stamp(now), "unresolved": True, "execution": self._receipt(intent, now, "outcome_unknown")}
                    observations.append({"effect_id": effect_id, "state": intent["state"]})
                    self._experience(session.value, intent, now)
                if observations:
                    session.value["history"].append({"event": "recovery_observed", "sequence": session.value["revision"] + 1})
                    session.commit(now)
            return {"status": "PASS", "observations": observations, "dispatch_performed": False, "canonical_target_changed": False}, 0
        except (ValueError, OSError) as error:
            return failure("recover", error)

    def apply_proposal(self, proposal_path: str, *, approval_path: str | None = None) -> tuple[dict[str, Any], int]:
        try:
            self.guard("apply")
            if approval_path is not None:
                raise AdmissionError("AUTHORITY_DENIED", "approval authority is the owner journal; remove the approval-file selector")
            with self.journal.writer() as session:
                intent = next(item for item in session.value["intents"].values() if item.get("artifact_path") == proposal_path)
                decision = session.value["decisions"].get(intent["human_request_id"])
                if decision is None or not decision["pending_apply"] or decision["response"]["chosen_action"] != "approve":
                    raise AdmissionError("APPROVAL_REQUIRED", "canonical apply requires the exact owner decision")
                if self.clock() >= datetime.fromisoformat(session.value["human_requests"][intent["human_request_id"]]["expires_at"]):
                    raise AdmissionError("APPROVAL_STALE", "the unstarted canonical apply target expired; obtain a fresh owner decision")
                if self._target_digest(intent["source_path"], proposal_path) != decision["target_digest"]:
                    raise AdmissionError("HUMAN_TARGET_STALE", "approval target changed before explicit apply")
                document = proposals._load_proposal(self.roots, proposal_path)
                if isinstance(document, tuple):
                    return document
                plan = proposals._plan(self.roots, document)
                if isinstance(plan, tuple):
                    return plan
                before = read_regular(resolve_beneath(self.roots.vault, plan.target_path))
                after = plan.target_markdown.encode("utf-8")
                effect_id = "apply." + intent["effect_id"].split(".")[-1]
                if effect_id in session.value["intents"]:
                    raise AdmissionError("OUTCOME_UNKNOWN", "existing apply intent requires owner reconciliation")
                now = self.clock()
                effect = {"effect_id": effect_id, "owner_operation_id": "knowledgeos", "content_digest": digest(after), "fencing_token": f"generation.g{session.generation}", "generation": session.generation, "issued_at": stamp(now), "expires_at": stamp(now + timedelta(hours=12))}
                self.core.validate("ExecutionIntent", effect)
                session.value["intents"][effect_id] = {**effect, "state": "unknown", "recorded_at": stamp(now), "canonical_path": plan.target_path, "before_digest": digest(before or b""), "after_digest": digest(after), "human_request_id": intent["human_request_id"]}
                session.commit(now)
                session.fence()
                self.guard("apply")
                replace_exact(resolve_beneath(self.roots.vault, plan.target_path), before, after)
                self.fault_hook("canonical_applied")
                session.fence()
                session.value["intents"][effect_id]["state"] = "completed"
                session.value["receipts"][effect_id] = {"recorded_at": stamp(now), "execution": self._receipt(effect, now, "succeeded", digest(after))}
                decision["pending_apply"] = False
                session.commit(now)
                return {"status": "PASS", "canonical_target_changed": True, "execution_outcome": "completed", "acceptance_state": "not_evaluated", "effect_id": effect_id}, 0
        except (ValueError, OSError, StopIteration, KeyError) as error:
            return failure("apply", error)

    def _experience(self, value: dict[str, Any], intent: dict[str, Any], now: datetime) -> None:
        fingerprint = digest(canonical({"effect_id": intent["effect_id"], "state": intent["state"], "generation": intent["generation"]}))
        if any(item["candidate_fingerprint"] == fingerprint for item in value["experience"]):
            return
        candidate = {
            "candidate_id": "experience." + fingerprint[7:39], "candidate_fingerprint": fingerprint,
            "idempotency_key": fingerprint, "owner_operation_id": "knowledgeos", "source_instance_id": self.core.binding["instance_id"],
            "source_event_id": intent["effect_id"], "source_revision": f"revision.r{value['revision']}",
            "occurred_at": stamp(now), "category": "crash_recovery", "classification": "internal",
            "observation_summary": "Owner reconciled a provider-free Pending artifact against frozen evidence.",
            "evidence_reference_ids": [], "outcome_state": "recovered" if intent["state"] == "completed" else "unknown",
            "evaluation_state": intent.get("acceptance_state", "unknown"), "core_version": "0.16.1", "core_digest": PINS["core_digest"],
            "semantic_version": "0.6.0", "semantic_digest": PINS["semantic_digest"],
            "semantic_term_ids": ["agentfabric.core.experience_candidate"], "extractor_version": "1.0.0", "redaction_policy_version": "knowledgeos.local",
        }
        self.core.validate("ExperienceCandidate", candidate)
        if len(canonical(candidate)) > 4096:
            raise AdmissionError("EXPERIENCE_CAPACITY", "experience candidate exceeds the Core limit")
        value["experience"].append(candidate)
        value["experience"] = value["experience"][-100:]
        if len(value["outbox"]) < 4 and sum(len(canonical(item)) for item in value["outbox"]) + len(canonical(candidate)) < 4096:
            entry = {"outbox_id": "outbox." + fingerprint[7:39], "candidate_id": candidate["candidate_id"], "owner_operation_id": "knowledgeos", "recipient_operation_id": "hermestrace", "payload_digest": digest(canonical(candidate)), "idempotency_key": fingerprint, "delivery_state": "pending", "attempt_count": 0, "priority": 1, "created_at": stamp(now), "expires_at": stamp(now + timedelta(hours=12)), "retention_until": stamp(now + timedelta(hours=12)), "next_attempt_at": stamp(now)}
            self.core.validate("ExperienceOutboxEntry", entry)
            queued = {"candidate": candidate, "entry": entry, "transport_state": "unconfigured"}
            if sum(len(canonical(item)) for item in value["outbox"]) + len(canonical(queued)) <= 4096:
                value["outbox"].append(queued)

    def check(self) -> tuple[dict[str, Any], int]:
        try:
            self.guard("inspect")
            return {"status": "PASS", "manifest": self.core.manifest(), "pins": PINS, "graph_profile": "graphless", "runtime_adoption": "unconfigured", "state_version": 3}, 0
        except (ValueError, OSError) as error:
            return failure("operation check", error)

    def status(self) -> tuple[dict[str, Any], int]:
        try:
            self.guard("inspect")
            value = self.journal.read()
            now = self.clock()
            unresolved = any(item["state"] == "unknown" for item in value["intents"].values())
            latest = max(value["receipts"].values(), key=lambda item: item["recorded_at"], default=None)
            execution = "unknown" if unresolved or latest is None and value["intents"] else "not_started" if latest is None else {"succeeded": "completed", "failed": "failed", "outcome_unknown": "unknown"}[latest["execution"]["outcome"]]
            evaluation = latest.get("evaluation") if latest else None
            acceptance = "unknown" if unresolved else "not_evaluated" if evaluation is None else "fail" if evaluation["failed_criterion_ids"] else "partial" if evaluation["not_run_criterion_ids"] else "pass"
            source = f"revision.r{value['revision']}"
            blockers = ["owner.outcome_unknown"] if unresolved else []
            health = {"operation_id": "knowledgeos", "instance_id": self.core.binding["instance_id"], "alive": True, "healthy": not unresolved, "ready": not unresolved, "blockers": blockers, "observed_at": stamp(now), "source_revision": source}
            snapshot = {"operation_id": "knowledgeos", "observed_at": stamp(now), "source_revision": source, "graph_profile": "graphless", "execution_outcome": execution, "acceptance_state": acceptance,
                        "freshness_seconds": None if value["observed_at"] is None else max(0, int((now - datetime.fromisoformat(value["observed_at"])).total_seconds())),
                        "completeness": "unknown" if value["observed_at"] is None else "partial" if unresolved else "complete", "health_alive": True, "health_healthy": not unresolved, "health_ready": not unresolved,
                        "blocker_ids": blockers, "pending_human_request_ids": [key for key, item in value["human_requests"].items() if item["lifecycle"] == "pending"]}
            self.core.validate("HealthReport", health)
            self.core.validate("StatusSnapshot", snapshot)
            chosen = {item["response"]["chosen_action"] for item in value["decisions"].values()}
            apply_states = {item["state"] for item in value["intents"].values() if "canonical_path" in item}
            canonical_apply = "unknown" if "unknown" in apply_states else "completed" if apply_states == {"completed"} else "not_started" if not apply_states else "partial"
            human_approval = "unknown" if not chosen else "approved" if chosen == {"approve"} else "rejected" if chosen == {"reject"} else "mixed"
            return {"status": "PASS", "health": health, "snapshot": snapshot, "human_approval": human_approval, "canonical_apply": canonical_apply, "external_transport": "unconfigured"}, 0
        except (ValueError, OSError) as error:
            return failure("operation status", error)

    def run_legacy(self, operation: Callable[[], int], *, request: dict[str, Any]) -> int:
        """Admit other maintained workflows under the same writer fence."""
        self.guard("legacy")
        with self.journal.writer() as session:
            session.fence()
            self.guard("legacy")
            if any(item["state"] == "unknown" for item in session.value["intents"].values()):
                raise AdmissionError("OUTCOME_UNKNOWN", "unresolved owner intent blocks another writer")
            now = self.clock()
            effect_id = "legacy." + uuid.uuid4().hex
            effect = {"effect_id": effect_id, "owner_operation_id": "knowledgeos", "content_digest": digest(canonical(request)), "generation": session.generation, "fencing_token": f"generation.g{session.generation}", "issued_at": stamp(now), "expires_at": stamp(now + timedelta(hours=12))}
            self.core.validate("ExecutionIntent", effect)
            session.value["intents"][effect_id] = {**effect, "state": "unknown", "recorded_at": stamp(now)}
            session.commit(now)
            token = CURRENT_OWNER_INTENT.set(effect_id)
            try:
                result = operation()
            finally:
                CURRENT_OWNER_INTENT.reset(token)
            session.fence()
            session.value["intents"][effect_id]["state"] = "completed" if result == 0 else "failed"
            session.value["receipts"][effect_id] = {"recorded_at": stamp(now), "execution": self._receipt(effect, now, "succeeded" if result == 0 else "failed")}
            session.commit(now)
            return result

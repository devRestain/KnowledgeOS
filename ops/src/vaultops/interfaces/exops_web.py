"""Owner Web backend bound to the trusted local startup actor."""

from __future__ import annotations

import base64
import copy
import difflib
import json
import re
from collections.abc import Callable
from dataclasses import replace
from datetime import datetime
from typing import Any, Protocol

from ..adapters.core import AdmissionError, canonical, digest
from ..adapters.owner_journal import read_regular, stamp
from ..adapters.paths import resolve_beneath
from ..application.curation_steering_contract import METHOD_ID as STEERING_METHOD
from ..application.exops import KnowledgeExOps, OwnerActor
from ..application.knowledge import LocalIdentity
from ..application.read_tools import KnowledgeReadTools
from ..application.work import record_digest
from ..projection import ProjectionError

_TEMPLATE_TEAMS = {
    "note_metadata": ("corpus-stewardship", "relation-ontology"),
    "document_enhancement": ("evidence-research",),
}
_TEMPLATE_METHODS = {
    "note_metadata": ("knowledgeos.note_metadata_team", "knowledgeos.relation_ontology_team"),
    "document_enhancement": ("knowledgeos.cited_research_team",),
}
_READ_ONLY_ROUTES = frozenset({
    "search/query", "owner/status", "work/read", "work/inspection", "work/list", "work/result",
    "work/steering/view", "work/steering/export",
    "work/methods", "mutation/receipt",
})


def _validate_research_report(report: Any) -> None:
    if (not isinstance(report, dict)
            or set(report) != {"source_reference", "index_generation_id", "improvements",
                               "citations", "uncertainty", "missing_materials"}):
        raise AdmissionError("EVIDENCE_REQUIRED", "Research report has an unsupported evidence shape")
    citations = report["citations"]
    improvements = report["improvements"]
    if (not isinstance(citations, list) or not 1 <= len(citations) <= 20
            or not all(isinstance(item, dict)
                       and set(item) == {"resource_reference", "chunk_locator", "chunk_hash"}
                       and isinstance(item["resource_reference"], dict)
                       and isinstance(item["chunk_locator"], str)
                       and 1 <= len(item["chunk_locator"]) <= 256
                       and isinstance(item["chunk_hash"], str)
                       and re.fullmatch(r"[0-9a-f]{64}", item["chunk_hash"])
                       for item in citations)
            or not isinstance(improvements, list) or not 1 <= len(improvements) <= 20
            or not all(isinstance(item, dict) and set(item) == {"point", "citation_indices"}
                       and isinstance(item["point"], str)
                       and 1 <= len(item["point"].strip()) <= 2048
                       and isinstance(item["citation_indices"], list)
                       and bool(item["citation_indices"])
                       and all(type(index) is int and 0 <= index < len(citations)
                               for index in item["citation_indices"])
                       for item in improvements)):
        raise AdmissionError("EVIDENCE_REQUIRED", "Every research improvement needs a valid citation")
    for key in ("uncertainty", "missing_materials"):
        values = report[key]
        if (not isinstance(values, list) or len(values) > 20
                or not all(isinstance(value, str) and 1 <= len(value.strip()) <= 2048
                           for value in values)):
            raise AdmissionError("EVIDENCE_REQUIRED", "Research gaps need bounded text")


class OwnerIngress(Protocol):
    owner_operation_id: str
    method: str
    resource_path: str
    body: bytes


class ExOpsWebBackend:
    """Use the local startup actor while preserving live owner action policy."""

    def __init__(self, exops: KnowledgeExOps, actor: OwnerActor,
                 admit_request: Callable[[OwnerActor, dict[str, Any]], tuple[dict[str, Any], dict[str, Any], str, datetime]] | None = None,
                 admit_template: Callable[[OwnerActor, str, dict[str, Any], str], list[tuple[dict[str, Any], dict[str, Any], str, datetime, dict[str, Any]]]] | None = None,
                 run_control: Any = None,
                 selected_methods: Callable[[OwnerActor], dict[str, Any]] | None = None,
                 admit_selected_request: Callable[[OwnerActor, dict[str, Any]], dict[str, Any]] | None = None) -> None:
        if not isinstance(actor, OwnerActor) or actor.ingress != "web" or not actor.actor_id:
            raise AdmissionError("BINDING_INVALID", "Web ingress needs a trusted local owner actor")
        self.exops = exops
        self.actor = actor
        self.admit_request = admit_request
        self.admit_template = admit_template
        self.selected_methods = selected_methods
        self.admit_selected_request = admit_selected_request
        if run_control is not None and (getattr(run_control, "exops", None) is not exops or exops.read_only):
            raise AdmissionError("BINDING_INVALID", "Run control needs this exact writable Owner")
        self.run_control = run_control

    def _scope(self, scope: Any) -> dict[str, str]:
        if (not isinstance(scope, dict) or set(scope) != {"path", "content_sha256"}
                or not isinstance(scope["path"], str)
                or not isinstance(scope["content_sha256"], str)
                or re.fullmatch(r"[0-9a-f]{64}", scope["content_sha256"]) is None):
            raise AdmissionError("INVALID_PAYLOAD", "Work scope must bind one exact note digest")
        self.exops.application._source_policy(scope["path"])
        self.exops.application._mcp_source_policy(scope["path"])
        if self.exops.application.reference(scope["path"])["source_revision"] != scope["content_sha256"]:
            raise AdmissionError("RESOURCE_STALE", "Selected note changed before Director admission")
        return scope

    def _mutation_receipt(self, actor: OwnerActor, payload: dict[str, Any]) -> dict[str, Any]:
        """Observe an exact retained command without resending an effect."""
        self.exops._guard(actor, "read_work")
        if set(payload) != {"route", "request"} or not isinstance(payload["request"], dict):
            raise AdmissionError("INVALID_PAYLOAD", "Receipt lookup needs the original route and request")
        route, request = payload["route"], payload["request"]
        state = self.exops.application.journal.read()
        intents = state["intents"]
        receipt = None
        if route == "work/intake-template":
            group = intents.get("exops.template." + str(request.get("request_id")))
            if (isinstance(group, dict) and group.get("actor_id") == actor.actor_id
                    and all(group.get(key) == request.get(key) for key in ("template_id", "scope"))):
                receipt = self.exops._template_receipt(intents, group, replayed=True)
        elif route in {"work/intake-selected", "work/intake"}:
            admission = next((item for key, item in intents.items()
                              if key.startswith("owner.director.")
                              and item.get("actor_id") == actor.actor_id
                              and item.get("request") == request), None)
            if admission is not None:
                work = intents.get("exops.work." + admission["run"]["work_run_id"])
                if isinstance(work, dict):
                    receipt = work["receipt"]
        elif route in {"proposal/decide", "proposal/apply"}:
            reference = request.get("proposal_reference")
            if isinstance(reference, dict):
                path = self.exops.application.resolve_reference(reference, proposal=True)
                intent = next((item for item in intents.values() if item.get("artifact_path") == path), {})
                key = ("plan." + intent["effect_id"] if str(intent.get("effect_id", "")).startswith("mcp.")
                       else intent.get("human_request_id"))
                if route == "proposal/apply":
                    effect_id = "apply." + str(intent.get("effect_id", "")).split(".")[-1]
                    effect = intents.get(effect_id, {})
                    if effect.get("state") == "completed":
                        receipt = state["receipts"].get(effect_id)
                else:
                    decision = state["decisions"].get(key)
                    chosen = (decision or {}).get("decision") or (decision or {}).get("response", {}).get("chosen_action")
                    reason = request.get("reason")
                    reason_matches = (
                        decision.get("reason") == reason if decision and "reason" in decision else
                        (decision or {}).get("reason_digest") == (digest(reason.encode()) if isinstance(reason, str) else None))
                    if chosen == request.get("decision") and reason_matches:
                        receipt = {"decision": decision}
        elif route == "daily/decide":
            receipt = state["decisions"].get(str(request.get("effect_id")) + "." + str(request.get("decision")))
        elif route in {"work/control/pause", "work/control/resume", "work/control/cancel"}:
            if self.run_control is not None:
                receipt = self.run_control.read_command(request.get("work_run_id"), request.get("command_id"), actor=actor)
        elif route == "work/prepare-metadata":
            group = intents.get("exops.template." + str(request.get("request_id")), {})
            if group.get("actor_id") == actor.actor_id:
                reference = group.get("canonical_proposal_reference")
                if reference is not None or group.get("metadata_promotion", {}).get("state") == "no_change":
                    receipt = {"proposal_reference": reference, "metadata_promotion": group.get("metadata_promotion")}
        elif route in {"work/steering/artifact", "work/steering/request"}:
            work_id = request.get("work_run_id")
            self.exops.read_work(work_id, actor=actor)
            record = intents.get("exops.work." + work_id, {})
            if route.endswith("/artifact"):
                raw = canonical(request.get("content"))
                stored = record.get("steering_artifacts", {}).get("artifact.steering." + digest(raw)[7:39])
                if stored is not None and base64.b64decode(stored["bytes"], validate=True) == raw:
                    receipt = stored["reference"]
            else:
                command = intents.get("curation.steering.command." + str(request.get("command_id")), {})
                if (command.get("actor_id") == actor.actor_id and command.get("target") == request.get("target")
                        and command.get("receipt", {}).get("work_run_id") == work_id):
                    receipt = command["receipt"]
        return {"found": receipt is not None, "receipt": copy.deepcopy(receipt),
                "observation_only": True, "mutation_retried": False}


    def __call__(self, ingress: OwnerIngress) -> dict[str, Any]:
        if ingress.owner_operation_id != "knowledgeos" or ingress.method != "POST":
            raise AdmissionError("OWNER_DENIED", "Web route is outside KnowledgeOS ExOps")
        actor = self.actor
        if self.exops.read_only and ingress.resource_path not in _READ_ONLY_ROUTES:
            raise AdmissionError("READ_ONLY", "This Owner route is unavailable in read-only mode")
        if len(ingress.body) > 16384:
            raise AdmissionError("INVALID_PAYLOAD", "Web body exceeds its bound")
        try:
            payload = json.loads(ingress.body)
        except (UnicodeError, ValueError) as error:
            raise AdmissionError("INVALID_PAYLOAD", "Web body must be JSON") from error
        if not isinstance(payload, dict):
            raise AdmissionError("INVALID_PAYLOAD", "Web body must be an object")
        if ingress.resource_path == "mutation/receipt":
            return self._mutation_receipt(actor, payload)
        if ingress.resource_path == "owner/status":
            if payload:
                raise AdmissionError("INVALID_PAYLOAD", "Owner status takes no selectors")
            self.exops._guard(actor, "read_work")
            state = self.exops.application.journal.read()
            return {"owner_operation_id": "knowledgeos", "read_only": self.exops.read_only,
                    "generation": state["generation"], "revision": state["revision"],
                    "observed_at": stamp(self.exops.application.clock()),
                    "bindings": {
                        "director_intake": not self.exops.read_only and self.admit_request is not None
                            and self.exops.authorize_actor(actor, "submit_work"),
                        "structured_templates": not self.exops.read_only and self.admit_template is not None
                            and self.exops.authorize_actor(actor, "submit_work"),
                        "selected_methods": not self.exops.read_only and self.selected_methods is not None
                            and self.admit_selected_request is not None
                            and self.exops.authorize_actor(actor, "submit_work"),
                        "run_control": self.run_control is not None,
                        "control_write": not self.exops.read_only
                            and self.exops.authorize_actor(actor, "control_work"),
                        "safe_steering": not self.exops.read_only
                            and getattr(getattr(self.exops, "curation_steering", None),
                                        "exops", None) is self.exops,
                        "proposal_review": not self.exops.read_only
                            and self.exops.authorize_actor(actor, "proposal_list"),
                        "proposal_decide": not self.exops.read_only
                            and self.exops.authorize_actor(actor, "proposal_decide"),
                        "proposal_apply": not self.exops.read_only
                            and self.exops.authorize_actor(actor, "proposal_apply"),
                        "daily_review": not self.exops.read_only
                            and self.exops.authorize_actor(actor, "daily_list"),
                        "daily_decide": not self.exops.read_only
                            and self.exops.authorize_actor(actor, "daily_decide"),
                    },
                    "runtime_observation": "unavailable"}
        if ingress.resource_path == "work/submit":
            if set(payload) != {"spec", "run", "action_id", "expires_at"}:
                raise AdmissionError("INVALID_PAYLOAD", "Work admission body has an unsupported shape")
            return self.exops.submit_work(payload["spec"], payload["run"], actor=actor,
                                          action_id=payload["action_id"],
                                          expires_at=datetime.fromisoformat(payload["expires_at"]))
        if ingress.resource_path == "work/read":
            if set(payload) != {"work_run_id"} or not isinstance(payload["work_run_id"], str):
                raise AdmissionError("INVALID_PAYLOAD", "Work read body has an unsupported shape")
            return self.exops.read_work(payload["work_run_id"], actor=actor)
        if ingress.resource_path == "work/inspection":
            if set(payload) != {"work_run_id"} or not isinstance(payload["work_run_id"], str):
                raise AdmissionError("INVALID_PAYLOAD", "Work inspection needs one exact run")
            read = self.exops.read_work(payload["work_run_id"], actor=actor)
            record = self.exops.application.journal.read()["intents"].get(
                "exops.work." + payload["work_run_id"])
            if record is None or record["run"] != read["run"]:
                raise AdmissionError("RESOURCE_STALE", "Work changed during inspection")
            segments = []
            for key in ("admitted_method_segments", "admitted_officer_segments"):
                for item in record.get(key, {}).values():
                    segments.append({"segment": copy.deepcopy(item["segment"]),
                                     "assignments": copy.deepcopy(item.get("assignments")
                                         or ([item["assignment"]] if "assignment" in item else []))})
            assessment = record.get("assessment")
            evaluated = (isinstance(assessment, dict)
                         and record_digest(assessment) == read["run"].get("latest_assessment_digest"))
            return {"work_run_id": payload["work_run_id"],
                    "observed_at": stamp(self.exops.application.clock()),
                    "segments": segments,
                    "assessment": copy.deepcopy(assessment) if evaluated else None,
                    "evaluations": copy.deepcopy(record.get("evaluations", [])) if evaluated else [],
                    "source_scope": copy.deepcopy(
                        record.get("template_scope") or record.get("selected_scope")),
                    "runtime_observation": "unavailable"}
        if ingress.resource_path in {
            "work/steering/view", "work/steering/artifact", "work/steering/request",
            "work/steering/export",
        }:
            controller = getattr(self.exops, "curation_steering", None)
            if controller is None:
                raise AdmissionError("UNAVAILABLE", "This Owner has no safe steering binding")
            if getattr(controller, "exops", None) is not self.exops:
                raise AdmissionError("BINDING_INVALID", "Steering belongs to another Owner")
            work_run_id = payload.get("work_run_id")
            if not isinstance(work_run_id, str) or not 1 <= len(work_run_id) <= 160:
                raise AdmissionError("INVALID_PAYLOAD", "Steering needs one exact WorkRun")
            if ingress.resource_path == "work/steering/view":
                if set(payload) != {"work_run_id"}:
                    raise AdmissionError("INVALID_PAYLOAD", "Steering view takes one WorkRun")
                return controller.view(work_run_id, actor=actor)
            if ingress.resource_path == "work/steering/export":
                if set(payload) != {"work_run_id"}:
                    raise AdmissionError("INVALID_PAYLOAD", "Steering export takes one WorkRun")
                return controller.export(work_run_id, actor=actor)
            if ingress.resource_path == "work/steering/artifact":
                if set(payload) != {"work_run_id", "content"} or not isinstance(payload["content"], dict):
                    raise AdmissionError("INVALID_PAYLOAD", "Steering evidence needs bounded content")
                return controller.register_directive(work_run_id, actor=actor, content=payload["content"])
            required = {"work_run_id", "command_id", "kind", "target", "expires_at",
                        "directive_reference"}
            if (not required <= set(payload)
                    or set(payload) - required - {"parameter_id", "successor_spec"}
                    or not isinstance(payload["kind"], str)
                    or payload["kind"] not in {"answer", "parameter", "cutover", "amend_work", "resume"}
                    or not isinstance(payload["command_id"], str)
                    or not isinstance(payload["target"], dict)
                    or not isinstance(payload["directive_reference"], dict)
                    or not isinstance(payload["expires_at"], str)
                    or len(payload["expires_at"]) > 64
                    or "parameter_id" in payload and not isinstance(payload["parameter_id"], str)
                    or "successor_spec" in payload and not isinstance(payload["successor_spec"], dict)):
                raise AdmissionError("INVALID_PAYLOAD", "Steering request differs from its owner contract")
            try:
                expires_at = datetime.fromisoformat(payload["expires_at"])
            except ValueError as error:
                raise AdmissionError("INVALID_PAYLOAD", "Steering expiry is invalid") from error
            selected = {key: value for key, value in payload.items()
                        if key not in {"work_run_id", "expires_at"}}
            return controller.request(work_run_id, actor=actor, expires_at=expires_at, **selected)
        if ingress.resource_path in {
            "work/control/target", "work/control/cancel", "work/control/pause",
            "work/control/resume", "work/control/command",
        }:
            if self.run_control is None:
                raise AdmissionError("UNAVAILABLE", "This Owner has no native control binding")
            required = (
                {"work_run_id"} if ingress.resource_path == "work/control/target"
                else {"work_run_id", "command_id"} if ingress.resource_path == "work/control/command"
                else {"work_run_id", "command_id", "target", "expires_at"}
            )
            if set(payload) != required or not isinstance(payload["work_run_id"], str):
                raise AdmissionError("INVALID_PAYLOAD", "Control body differs from its admitted route")
            if ingress.resource_path == "work/control/target":
                return self.run_control.target(payload["work_run_id"], actor=actor)
            if not isinstance(payload["command_id"], str):
                raise AdmissionError("INVALID_PAYLOAD", "Control command needs one stable identity")
            if ingress.resource_path == "work/control/command":
                return self.run_control.read_command(
                    payload["work_run_id"], payload["command_id"], actor=actor)
            if not isinstance(payload["expires_at"], str) or len(payload["expires_at"]) > 64:
                raise AdmissionError("INVALID_PAYLOAD", "Control expiry must be a bounded timestamp")
            try:
                expires_at = datetime.fromisoformat(payload["expires_at"])
            except ValueError as error:
                raise AdmissionError("INVALID_PAYLOAD", "Control expiry is invalid") from error
            return self.run_control.request_control(
                payload["work_run_id"], kind=ingress.resource_path.rsplit("/", 1)[-1],
                actor=actor, command_id=payload["command_id"],
                target=payload["target"], expires_at=expires_at)
        if ingress.resource_path == "work/list":
            if set(payload) != {"path"} or not isinstance(payload["path"], str):
                raise AdmissionError("INVALID_PAYLOAD", "Work list requires one path")
            self.exops._guard(actor, "read_work")
            self.exops.application._source_policy(payload["path"])
            self.exops.application._mcp_source_policy(payload["path"])
            current_source_sha256 = self.exops.application.reference(payload["path"])["source_revision"]
            works = []
            intents = self.exops.application.journal.read()["intents"]
            for record in intents.values():
                if not isinstance(record, dict):
                    continue
                scope = record.get("template_scope") or record.get("selected_scope") or {}
                if scope.get("path") != payload["path"]:
                    continue
                group = intents.get("exops.template." + (record.get("template_request_id") or ""), {})
                promotion = group.get("metadata_promotion", {}) if isinstance(group, dict) else {}
                run = record["run"]
                assessment = record.get("assessment")
                evaluated = (isinstance(assessment, dict) and bool(run.get("latest_assessment_digest"))
                             and record_digest(assessment) == run["latest_assessment_digest"])
                outcomes = assessment.get("criterion_outcomes") if evaluated else None
                passed = (evaluated and run["state"] == "completed"
                          and assessment.get("completion_state") == "complete"
                          and isinstance(outcomes, list) and bool(outcomes)
                          and all(isinstance(item, dict) and item.get("outcome") == "satisfied"
                                  for item in outcomes))
                works.append({"work_run_id": run["work_run_id"],
                              "template_id": record.get("template_id"), "team_id": record["spec"].get("team_id"),
                              "request_id": record.get("template_request_id") or
                                  intents.get("owner.director." + record["spec"]["admission_receipt_id"], {}).get("request", {}).get("request_id"),
                              "source_stale": scope.get("content_sha256") != current_source_sha256,
                              "proposal_reference": record.get("canonical_proposal_reference"),
                              "metadata_promotion_state": promotion.get("state", "pending")
                                  if record.get("template_id") == "note_metadata" else None,
                              "metadata_promotion_code": promotion.get("code")
                                  if record.get("template_id") == "note_metadata" else None,
                              "receipt_state": record["receipt"]["receipt_state"],
                              "dispatch_state": record["dispatch_state"],
                              "application_state": record["application_state"],
                              "run_state": "unknown" if record["application_state"] == "unknown" else run["state"],
                              "evaluation_state": "passed" if passed else
                                  "needs_attention" if evaluated else
                                  "unavailable" if run.get("latest_assessment_digest") else "pending"})
            self.exops.application._mcp_source_policy(payload["path"])
            if self.exops.application.reference(payload["path"])["source_revision"] != current_source_sha256:
                raise AdmissionError("RESOURCE_STALE", "Work source changed during status export")
            return {"works": works[-50:]}
        if ingress.resource_path == "work/prepare-metadata":
            if set(payload) != {"request_id"} or not isinstance(payload["request_id"], str):
                raise AdmissionError("INVALID_PAYLOAD", "Metadata promotion needs one request identity")
            from ..application.metadata_proposals import prepare_metadata_proposal

            return prepare_metadata_proposal(self.exops, payload["request_id"], actor=actor)
        if ingress.resource_path == "work/result":
            if set(payload) != {"work_run_id"} or not isinstance(payload["work_run_id"], str):
                raise AdmissionError("INVALID_PAYLOAD", "Work result needs one run identity")
            read = self.exops.read_work(payload["work_run_id"], actor=actor)
            run = read["run"]
            result = {"work_run_id": run["work_run_id"],
                      "state": "unknown" if read["application_state"] == "unknown" else run["state"],
                      "assessment": None, "text": None,
                      "reason": "Owner execution outcome is unknown; do not replay the producer."
                          if read["application_state"] == "unknown" else
                          "No independently evaluated result is available yet."}
            record = self.exops.application.journal.read()["intents"].get("exops.work." + run["work_run_id"])
            if record is None:
                raise AdmissionError("RESOURCE_UNAVAILABLE", "Work record is unavailable")
            scope = record.get("template_scope") or record.get("selected_scope")
            application = replace(self.exops.application,
                                  identity=LocalIdentity(actor.actor_id, "operator", local_execution=True))
            source_stale = False
            if scope:
                application._source_policy(scope["path"])
                try:
                    current_source = application.reference(scope["path"])
                except AdmissionError as error:
                    if error.code != "RESOURCE_UNAVAILABLE":
                        raise
                    current_source = None
                if current_source is not None:
                    application._mcp_source_policy(scope["path"])
                source_stale = current_source is None or current_source["source_revision"] != scope["content_sha256"]
            elif (record.get("method_source_reference") or {}).get("resource_kind") == "vault":
                try:
                    relative = application.resolve_reference(record["method_source_reference"])
                    application._mcp_source_policy(relative)
                except AdmissionError as error:
                    if error.code not in {"RESOURCE_STALE", "RESOURCE_UNAVAILABLE"}:
                        raise
                    source_stale = True
            result.update(source_stale=source_stale, source_scope=copy.deepcopy(scope))
            assessment = record.get("assessment")
            if (assessment is not None and run.get("latest_assessment_digest")
                    and record_digest(assessment) == run["latest_assessment_digest"]):
                result["assessment"] = copy.deepcopy(assessment)
            if "continuation_state" in read:
                result["continuation_state"] = read["continuation_state"]
            if "remaining_work" in read and result["assessment"] is not None:
                result["remaining_work"] = read["remaining_work"]
                if read["continuation_state"] == "human_required":
                    result["reason"] = "Continuation requires an authorized owner revision."
            if (record["spec"].get("method_contract_ref", {}).get("method_id") == STEERING_METHOD
                    and run["state"] == "completed"):
                controller = getattr(self.exops, "curation_steering", None)
                if controller is None:
                    raise AdmissionError("UNAVAILABLE", "This Owner has no steering result exporter")
                if getattr(controller, "exops", None) is not self.exops:
                    raise AdmissionError("BINDING_INVALID", "Steering result belongs to another Owner")
                exported = controller.export(run["work_run_id"], actor=actor)
                if (exported["reference"] != run.get("result_reference")
                        or exported["assessment"] != record.get("assessment")):
                    raise AdmissionError("RESOURCE_STALE", "Steering result changed during export")
                try:
                    raw = base64.b64decode(exported["content_base64"], validate=True)
                    text = raw.decode("utf-8")
                    report = json.loads(text)
                except (ValueError, UnicodeError, TypeError) as error:
                    raise AdmissionError("EVIDENCE_REQUIRED", "Steering result is not exact JSON") from error
                reference = exported["reference"]
                if (not isinstance(report, dict)
                        or digest(raw) != reference["content_digest"]
                        or len(raw) != reference["size_bytes"]):
                    raise AdmissionError("EVIDENCE_REQUIRED", "Steering result bytes differ from their reference")
                result.update({
                    "assessment": exported["assessment"], "text": text, "truncated": False,
                    "result_reference": copy.deepcopy(reference), "report": report,
                    "evidence": {
                        "source_reference": copy.deepcopy(record["method_source_reference"]),
                        "result_reference": copy.deepcopy(reference),
                        "criterion_outcomes": copy.deepcopy(exported["assessment"]["criterion_outcomes"]),
                    },
                    "proposal_reference": None, "reason": None,
                    "truth_evaluated": False, "apply_allowed": False,
                })
                return result
            if run["state"] != "completed" or result["assessment"] is None:
                return result
            reference = run.get("result_reference")
            if not isinstance(reference, dict) or record is None:
                raise AdmissionError("RESOURCE_UNAVAILABLE", "Completed Work lacks an exact result reference")
            if not any(item.get("content_digest") == reference.get("content_digest")
                       for item in record.get("artifact_references", {}).values()):
                raise AdmissionError("SCOPE_DENIED", "Result is not a submitted Work artifact")
            selected = next((item for item in record.get("submitted_artifacts", [])
                             if item.get("content_digest") == reference["content_digest"]), None)
            if selected is None:
                raise AdmissionError("RESOURCE_UNAVAILABLE", "Result artifact is not exportable")
            reader = KnowledgeReadTools(application)
            excerpts = []
            for offset in range(0, 65536, 4096):
                artifact = reader.artifact_read(selected, offset=offset)
                excerpts.append(artifact["text"])
                if not artifact["truncated"]:
                    break
            result.update({"text": "".join(excerpts), "truncated": artifact["truncated"],
                           "proposal_reference": record.get("canonical_proposal_reference"),
                           "reason": None})
            if source_stale:
                result.update(historical=True, current_evidence_valid=False, apply_allowed=False,
                              truth_evaluated=False, proposal_reference=None,
                              reason="Historical evaluated result; its source changed or moved. It is not current evidence.")
                return result
            boundary_method = record["spec"].get("method_contract_ref", {}).get("method_id")
            if boundary_method in {
                    "knowledgeos.note_metadata_team", "knowledgeos.relation_ontology_team",
                    "knowledgeos.daily_corpus_stewardship_team"}:
                from ..application.temporal_contract import native_team_route

                if native_team_route(record["spec"]) is not None:
                    from ..application.metadata_work import (
                        KINDS,
                        relation_link_evidence,
                        validate_candidate,
                    )

                    body = result["text"].split("```json\n", 1)
                    if len(body) != 2 or "\n```" not in body[1] or result["truncated"]:
                        raise AdmissionError("EVIDENCE_REQUIRED", "Native metadata result is incomplete")
                    try:
                        envelope = json.loads(body[1].split("\n```", 1)[0])
                        candidate = json.loads(envelope["content"])
                        if envelope.get("artifact_kind") != KINDS[boundary_method]:
                            raise AdmissionError("EVIDENCE_REQUIRED", "Native metadata artifact kind changed")
                        source = record["method_source_reference"]
                        links = (relation_link_evidence(record) if boundary_method
                                 == "knowledgeos.relation_ontology_team" else None)
                        validate_candidate(application, source, candidate, method_id=boundary_method,
                                           link_evidence=links)
                    except (KeyError, TypeError, ValueError) as error:
                        raise AdmissionError("EVIDENCE_REQUIRED", "Native metadata result evidence is invalid") from error
                    result["report"] = candidate
                    result["evidence"] = candidate["evidence"]
                    result["apply_allowed"] = False
                    result["truth_evaluated"] = False
            if boundary_method in {"knowledgeos.knowledge_exchange_team", "knowledgeos.vault_maintenance_team"}:
                from ..application.boundary_contract import validate_boundary_export

                source = record["method_source_reference"]
                application.resolve_reference(source)
                application._mcp_source_policy(application.resolve_reference(source))
                body = result["text"].split("```json\n", 1)
                if len(body) != 2 or "\n```" not in body[1] or result["truncated"]:
                    raise AdmissionError("EVIDENCE_REQUIRED", "Boundary result lacks exact structured plan content")
                try:
                    plan = json.loads(body[1].split("\n```", 1)[0])
                    evidence = validate_boundary_export(
                        application, record, plan, result["text"].encode("utf-8"))
                except (KeyError, TypeError, ValueError) as error:
                    raise AdmissionError("EVIDENCE_REQUIRED", "Boundary result plan evidence is invalid") from error
                result["report"] = plan
                result["evidence"] = evidence
                result["apply_allowed"] = False
            if record.get("template_id") == "document_enhancement":
                body = result["text"].split("```json\n", 1)
                if len(body) != 2 or "\n```" not in body[1] or result["truncated"]:
                    raise AdmissionError("INVALID_ARGUMENT", "Research report has no complete structured content")
                try:
                    envelope = json.loads(body[1].split("\n```", 1)[0])
                    research = json.loads(envelope["content"])
                except (KeyError, TypeError, ValueError) as error:
                    raise AdmissionError("INVALID_ARGUMENT", "Research report JSON is invalid") from error
                if envelope.get("artifact_kind") != "document_enhancement_report":
                    raise AdmissionError("EVIDENCE_REQUIRED", "Work result is not a research report")
                _validate_research_report(research)
                if research["source_reference"] != application.reference(scope["path"]):
                    raise AdmissionError("EVIDENCE_REQUIRED", "Research report lacks cited findings and gaps")
                try:
                    citations = reader.citation_verify(research["citations"])
                except ProjectionError as error:
                    raise AdmissionError("INDEX_STALE", "Research citation generation changed") from error
                if not citations["all_valid"]:
                    raise AdmissionError("RESOURCE_STALE", "Research citation changed after evaluation")
                result["report"] = research
                try:
                    result["evidence"] = [{
                        "path": application.resolve_reference(item["resource_reference"]),
                        "chunk_locator": item["chunk_locator"],
                        "excerpt": reader.knowledge_read(item["resource_reference"],
                                                         item["chunk_locator"], max_chars=320)["text"],
                    } for item in research["citations"]]
                except ProjectionError as error:
                    raise AdmissionError("INDEX_STALE", "Research evidence changed during export") from error
            return result
        if ingress.resource_path == "search/query":
            if (set(payload) != {"query", "mode"} or not isinstance(payload["query"], str)
                    or not 1 <= len(payload["query"].strip()) <= 512
                    or not isinstance(payload["mode"], str)
                    or payload["mode"] not in {"lexical", "vector", "hybrid", "ontology"}):
                raise AdmissionError("INVALID_PAYLOAD", "Search query or mode is invalid")
            if self.exops.read_only and payload["mode"] != "lexical":
                raise AdmissionError("READ_ONLY", "This Owner exposes Lexical search only")
            self.exops._guard(actor, "search_query")
            application = replace(self.exops.application,
                                  identity=LocalIdentity(actor.actor_id, "operator", local_execution=True))
            report, code = application.search(payload["query"].strip(), mode=payload["mode"], limit=10)
            if code != 0:
                error = report.get("errors", [{}])[0]
                raise AdmissionError(error.get("code", "SEARCH_UNAVAILABLE"),
                                     error.get("message", "Owner search failed"))
            reader = KnowledgeReadTools(application)
            try:
                _, eligible = reader._eligible()
            except ProjectionError as error:
                raise AdmissionError("INDEX_STALE", "Search sources changed before result export") from error
            results = []
            seen = set()
            for candidate in report.get("candidates", []):
                path = candidate["path"]
                if path in seen or path not in eligible:
                    continue
                reference = application.reference(path)
                if candidate.get("resource_reference") != reference:
                    raise AdmissionError("RESOURCE_STALE", "Search candidate source changed before result export")
                try:
                    excerpt = reader.knowledge_read(reference, candidate["chunk_locator"], max_chars=320)
                except ProjectionError as error:
                    raise AdmissionError("INDEX_STALE", "Search sources changed during excerpt export") from error
                results.append({"title": path.rsplit("/", 1)[-1].removesuffix(".md"),
                                "path": path, "excerpt": excerpt["text"],
                                "reason": candidate.get("retrieval_reason", "match"),
                                "resource_reference": reference,
                                "chunk_locator": excerpt["chunk_locator"],
                                "chunk_hash": excerpt["chunk_hash"]})
                seen.add(path)
            for item in results:
                if application.reference(item["path"]) != item["resource_reference"]:
                    raise AdmissionError("RESOURCE_STALE", "Search result source changed during export")
            return {"mode": payload["mode"], "index_generation_id": report.get("index_generation_id"),
                    "vector_generation_id": report.get("vector_generation_id"), "results": results}
        if ingress.resource_path == "work/methods":
            if payload:
                raise AdmissionError("INVALID_PAYLOAD", "Method enumeration takes no selectors")
            self.exops._guard(actor, "read_work")
            if self.selected_methods is not None and not self.exops.read_only:
                return self.selected_methods(actor)
            from ..application.work_methods import WorkMethodCatalog
            return {"methods": [
                {"method_id": method, "purpose": definition["purpose"], "available": False,
                 "reason": "This Owner has no writable selected-method admission binding", "parameters": {}}
                for method, definition in WorkMethodCatalog.load(
                    self.exops.application.roots.control).methods.items()],
                "observed_at": stamp(self.exops.application.clock())}
        if ingress.resource_path == "work/intake-selected":
            self.exops._guard(actor, "submit_work")
            if (set(payload) != {"method_id", "scope", "request_id"}
                    or not isinstance(payload["method_id"], str)
                    or not isinstance(payload["request_id"], str)):
                raise AdmissionError("INVALID_PAYLOAD", "Selected Work request shape is invalid")
            if self.admit_selected_request is None:
                raise AdmissionError("ADMISSION_REQUIRED", "Selected-method Director admission is unavailable")
            self._scope(payload["scope"])
            return self.admit_selected_request(actor, payload)
        if ingress.resource_path == "work/intake":
            if self.admit_request is None or set(payload) != {"question", "scope"}:
                raise AdmissionError("ADMISSION_REQUIRED", "Owner Director intake is unavailable")
            if not isinstance(payload["question"], str) or not 1 <= len(payload["question"]) <= 8192:
                raise AdmissionError("INVALID_PAYLOAD", "Work question is invalid")
            scope = dict(self._scope(payload["scope"]))
            spec, run, action_id, expires_at = self.admit_request(actor, payload)
            source = (self.exops.application.reference(scope["path"])
                      if spec.get("method_contract_ref") else None)
            if source is not None and source["source_revision"] != scope["content_sha256"]:
                raise AdmissionError("RESOURCE_STALE", "Selected note changed during Director admission")
            return self.exops.submit_work(spec, run, actor=actor,
                                          action_id=action_id, expires_at=expires_at,
                                          selected_scope=scope,
                                          method_source_reference=source)
        if ingress.resource_path == "work/intake-template":
            if (set(payload) != {"template_id", "scope", "request_id"}
                    or not isinstance(payload["template_id"], str)
                    or payload["template_id"] not in _TEMPLATE_TEAMS
                    or not isinstance(payload["request_id"], str)
                    or re.fullmatch(r"[0-9a-f-]{36}", payload["request_id"]) is None):
                raise AdmissionError("INVALID_PAYLOAD", "Structured Work request shape is invalid")
            scope = self._scope(payload["scope"])
            replay = self.exops.read_template_request(payload["request_id"], actor=actor,
                                                      template_id=payload["template_id"], scope=scope)
            if replay is not None:
                return replay
            self.exops.guard_template_scope_reconciled(
                payload["template_id"], scope, actor=actor)
            if self.admit_template is None:
                raise AdmissionError("ADMISSION_REQUIRED", "Trusted Director template admission is unavailable")
            admissions = self.admit_template(actor, payload["template_id"], scope, payload["request_id"])
            expected = _TEMPLATE_TEAMS[payload["template_id"]]
            if (not isinstance(admissions, list) or len(admissions) != len(expected)
                    or any(not isinstance(item, tuple) or len(item) != 5
                           or not isinstance(item[0], dict) for item in admissions)
                    or tuple(item[0].get("team_id") for item in admissions) != expected
                    or tuple(item[0].get("method_contract_ref", {}).get("method_id")
                             for item in admissions) != _TEMPLATE_METHODS[payload["template_id"]]):
                raise AdmissionError("ADMISSION_REQUIRED", "Director did not bind the template Teams")
            return self.exops.submit_template_group(payload["template_id"], scope,
                                                    payload["request_id"], admissions, actor=actor)
        if ingress.resource_path.startswith("daily/"):
            action = ingress.resource_path.split("/", 1)[1]
            if action not in {"list", "read", "decide"} or not self.exops.authorize_actor(actor, "daily_" + action):
                raise AdmissionError("ACTION_DENIED", "Daily review is outside owner policy")
            application = replace(self.exops.application,
                                  identity=LocalIdentity(actor.actor_id, "operator"))
            stewardship = application.daily_stewardship()
            if action == "list":
                if payload:
                    raise AdmissionError("INVALID_PAYLOAD", "Daily list takes no selectors")
                return {"pending": stewardship.pending()[:50]}
            if set(payload) != ({"effect_id"} if action == "read" else {"effect_id", "decision"}):
                raise AdmissionError("INVALID_PAYLOAD", "Daily review request shape is invalid")
            if not isinstance(payload["effect_id"], str) or len(payload["effect_id"]) > 128:
                raise AdmissionError("INVALID_PAYLOAD", "Daily effect identity is invalid")
            if action == "read":
                return stewardship.read_pending(payload["effect_id"])
            if not isinstance(payload["decision"], str):
                raise AdmissionError("INVALID_PAYLOAD", "Daily decision is invalid")
            return stewardship.review(payload["effect_id"], payload["decision"])
        if ingress.resource_path.startswith("proposal/"):
            if set(payload) - {"proposal_reference", "decision", "reason"} or (
                    ingress.resource_path != "proposal/list" and "proposal_reference" not in payload):
                raise AdmissionError("INVALID_PAYLOAD", "Proposal request has unsupported fields")
            action = ingress.resource_path.split("/", 1)[1]
            if action not in {"list", "read", "decide", "apply"} or not self.exops.authorize_actor(actor, "proposal_" + action):
                raise AdmissionError("ACTION_DENIED", "Owner denies this proposal action")
            application = replace(self.exops.application,
                                  identity=LocalIdentity(actor.actor_id, "operator"))
            if action == "list":
                if payload:
                    raise AdmissionError("INVALID_PAYLOAD", "Proposal list has no caller-selected scope")
                records = application.journal.read()["intents"].values()
                proposals = []
                for item in records:
                    if (not isinstance(item, dict) or item.get("state") != "completed"
                            or not str(item.get("artifact_path", "")).startswith("01_AI_Review/Pending/")):
                        continue
                    try:
                        reference = application.reference(item["artifact_path"], proposal=True)
                    except AdmissionError:
                        continue
                    proposals.append({"proposal_reference": reference,
                                      "action": item.get("action", "normalize"),
                                      "apply_allowed": not item["effect_id"].startswith("mcp.")})
                return {"proposals": proposals[-30:]}
            reference = payload["proposal_reference"]
            path = application.resolve_reference(reference, proposal=True)
            intent = next((item for item in application.journal.read()["intents"].values()
                           if isinstance(item, dict) and item.get("artifact_path") == path), None)
            if intent is None or intent.get("state") != "completed":
                raise AdmissionError("RESOURCE_UNAVAILABLE", "Proposal has no completed owner receipt")
            if action == "read":
                raw = read_regular(resolve_beneath(application.roots.vault, path))
                if raw is None or len(raw) > 65536:
                    raise AdmissionError("RESOURCE_UNAVAILABLE", "Pending artifact is unavailable")
                if digest(raw) != intent["content_digest"]:
                    raise AdmissionError("RESOURCE_STALE", "Pending artifact differs from its owner receipt")
                decision_key = ("plan." + intent["effect_id"] if intent["effect_id"].startswith("mcp.")
                                else intent["human_request_id"])
                decision = application.journal.read()["decisions"].get(decision_key)
                diff = None
                target_path = None
                if not intent["effect_id"].startswith("mcp."):
                    from .. import proposals

                    document = proposals._load_proposal(application.roots, path)
                    if not isinstance(document, tuple):
                        plan = proposals._plan(application.roots, document)
                        if not isinstance(plan, tuple):
                            target_path = plan.target_path
                            before = read_regular(plan.target) or b""
                            if len(before) <= 65536:
                                diff = "".join(difflib.unified_diff(
                                    before.decode("utf-8").splitlines(keepends=True),
                                    plan.target_markdown.splitlines(keepends=True),
                                    fromfile=target_path + " (current)", tofile=target_path + " (proposal)"))[:65536]
                try:
                    current_source = application.reference(intent["source_path"])
                except AdmissionError:
                    current_source = None
                source_text = None
                source_truncated = False
                if current_source is not None:
                    application._mcp_source_policy(intent["source_path"])
                    source_raw = read_regular(resolve_beneath(application.roots.vault, intent["source_path"]))
                    if source_raw is None or digest(source_raw) != current_source["content_digest"]:
                        raise AdmissionError("RESOURCE_STALE", "Source changed during proposal review")
                    source_body = source_raw.decode("utf-8")
                    source_text, source_truncated = source_body[:16384], len(source_body) > 16384
                fields = None
                if intent.get("template_request_id"):
                    changes = intent.get("metadata_changes", {})
                    evidence = intent.get("metadata_evidence", {})
                    fields = [{"field": field, "value": value,
                               "evidence": evidence[field][value]}
                              for field, values in changes.items() for value in values]
                return {"proposal_reference": reference, "text": raw.decode("utf-8"),
                        "source_reference": current_source,
                        "source_path": intent["source_path"],
                        "source_digest": intent["source_digest"],
                        "source_text": source_text, "source_truncated": source_truncated,
                        "source_stale": current_source is None or current_source["content_digest"] != intent["source_digest"],
                        "target_path": target_path, "diff": diff, "field_evidence": fields,
                        "apply_allowed": not intent["effect_id"].startswith("mcp."),
                        "decision": copy.deepcopy(decision), "state": intent["state"]}
            if action == "decide":
                if set(payload) != {"proposal_reference", "decision", "reason"} or payload["decision"] not in {"approve", "reject"}:
                    raise AdmissionError("INVALID_PAYLOAD", "Human decision is invalid")
                if intent["effect_id"].startswith("mcp."):
                    application.guard(payload["decision"])
                    with application.journal.writer() as writer:
                        current = writer.value["intents"].get(intent["effect_id"])
                        if current != intent or application.reference(path, proposal=True) != reference:
                            raise AdmissionError("RESOURCE_STALE", "Pending proposal changed")
                        key = "plan." + intent["effect_id"]
                        prior = writer.value["decisions"].get(key)
                        selected = {"actor_id": actor.actor_id, "decision": payload["decision"],
                                    "reason": payload["reason"], "proposal_reference": reference,
                                    "recorded_at": stamp(application.clock()), "apply_allowed": False}
                        if prior is not None:
                            if {key: value for key, value in prior.items() if key != "recorded_at"} != {
                                    key: value for key, value in selected.items() if key != "recorded_at"}:
                                raise AdmissionError("HUMAN_RESPONSE_CONFLICT", "Plan decision already differs")
                            return {"status": "NO_OP", "decision": prior}
                        writer.value["decisions"][key] = selected
                        writer.commit(application.clock())
                        return {"status": "PASS", "decision": selected}
                report, code = application.decide_proposal(path, reference["content_digest"][7:],
                                                          payload["decision"], reason=payload["reason"])
            else:
                if set(payload) != {"proposal_reference"} or intent["effect_id"].startswith("mcp."):
                    raise AdmissionError("ACTION_DENIED", "This proposal has no canonical apply action")
                report, code = application.apply_proposal(path)
            if code != 0:
                raise AdmissionError(report.get("errors", [{}])[0].get("code", "OWNER_INVALID"),
                                     "Owner could not complete this human action")
            return report
        raise AdmissionError("ACTION_DENIED", "Web route is not admitted by this ExOps backend")

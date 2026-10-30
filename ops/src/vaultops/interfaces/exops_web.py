"""Owner Web backend for a trusted Services ServeRouter ingress."""

from __future__ import annotations

import copy
import difflib
import json
import re
from collections.abc import Callable
from dataclasses import replace
from datetime import datetime
from typing import Any, Protocol

from ..adapters.core import AdmissionError, digest
from ..adapters.owner_journal import read_regular, stamp
from ..adapters.paths import resolve_beneath
from ..application.exops import KnowledgeExOps, OwnerActor
from ..application.knowledge import LocalIdentity
from ..application.read_tools import KnowledgeReadTools
from ..application.work import record_digest
from ..projection import ProjectionError, load_current_projection

_TEMPLATE_TEAMS = {
    "note_metadata": ("corpus-stewardship", "relation-ontology"),
    "document_enhancement": ("evidence-research",),
}
_TEMPLATE_METHODS = {
    "note_metadata": ("knowledgeos.note_metadata_team", "knowledgeos.relation_ontology_team"),
    "document_enhancement": ("knowledgeos.cited_research_team",),
}
_READ_ONLY_ROUTES = frozenset({
    "search/query", "owner/status", "work/read", "work/list", "work/result",
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
    actor_login: str
    method: str
    resource_path: str
    body: bytes
    csrf_token: str | None


class ExOpsWebBackend:
    """Reauthenticates the owner actor and mutation token at every call."""

    def __init__(self, exops: KnowledgeExOps,
                 authenticate_login: Callable[[str], OwnerActor | None],
                 validate_csrf: Callable[[OwnerActor, str], bool],
                 admit_request: Callable[[OwnerActor, dict[str, Any]], tuple[dict[str, Any], dict[str, Any], str, datetime]] | None = None,
                 admit_template: Callable[[OwnerActor, str, dict[str, Any], str], list[tuple[dict[str, Any], dict[str, Any], str, datetime, dict[str, Any]]]] | None = None) -> None:
        if not callable(authenticate_login) or not callable(validate_csrf):
            raise AdmissionError("BINDING_INVALID", "Web ingress needs owner session checks")
        self.exops = exops
        self.authenticate_login = authenticate_login
        self.validate_csrf = validate_csrf
        self.admit_request = admit_request
        self.admit_template = admit_template

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

    def __call__(self, ingress: OwnerIngress) -> dict[str, Any]:
        if ingress.owner_operation_id != "knowledgeos" or ingress.method != "POST":
            raise AdmissionError("OWNER_DENIED", "Web route is outside KnowledgeOS ExOps")
        actor = self.authenticate_login(ingress.actor_login)
        if (actor is None or actor.ingress != "web" or ingress.csrf_token is None
                or not self.validate_csrf(actor, ingress.csrf_token)):
            raise AdmissionError("UNAUTHENTICATED", "Web actor or CSRF binding is invalid")
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
        if ingress.resource_path == "owner/status":
            if payload:
                raise AdmissionError("INVALID_PAYLOAD", "Owner status takes no selectors")
            self.exops._guard(actor, "read_work")
            state = self.exops.application.journal.read()
            return {"owner_operation_id": "knowledgeos", "read_only": self.exops.read_only,
                    "generation": state["generation"], "revision": state["revision"]}
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
                              "request_id": record.get("template_request_id"),
                              "source_stale": scope.get("content_sha256") != current_source_sha256,
                              "proposal_reference": record.get("canonical_proposal_reference"),
                              "metadata_promotion_state": promotion.get("state", "pending")
                                  if record.get("template_id") == "note_metadata" else None,
                              "metadata_promotion_code": promotion.get("code")
                                  if record.get("template_id") == "note_metadata" else None,
                              "receipt_state": record["receipt"]["receipt_state"],
                              "dispatch_state": record["dispatch_state"],
                              "application_state": record["application_state"],
                              "run_state": run["state"],
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
            result = {"work_run_id": run["work_run_id"], "state": run["state"],
                      "assessment": None,
                      "text": None, "reason": "No independently evaluated result is available yet."}
            record = self.exops.application.journal.read()["intents"].get("exops.work." + run["work_run_id"])
            if record is None:
                raise AdmissionError("RESOURCE_UNAVAILABLE", "Work record is unavailable")
            scope = record.get("template_scope") or record.get("selected_scope")
            application = replace(self.exops.application,
                                  identity=LocalIdentity(actor.actor_id, "operator", local_execution=True))
            if scope:
                application._source_policy(scope["path"])
                application._mcp_source_policy(scope["path"])
                if self.exops.application.reference(scope["path"])["source_revision"] != scope["content_sha256"]:
                    raise AdmissionError("RESOURCE_STALE", "The Work source changed after admission")
            assessment = record.get("assessment")
            if (assessment is not None and run.get("latest_assessment_digest")
                    and record_digest(assessment) == run["latest_assessment_digest"]):
                result["assessment"] = copy.deepcopy(assessment)
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
                    projection = load_current_projection(application.roots, verify_sources=True)
                except ProjectionError as error:
                    raise AdmissionError("INDEX_STALE", "Research source generation requires rebuilding") from error
                if research["index_generation_id"] != projection.manifest["generation_id"]:
                    raise AdmissionError("INDEX_STALE", "Research report uses an older source generation")
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
                projection, eligible = reader._eligible()
            except ProjectionError as error:
                raise AdmissionError("INDEX_STALE", "Search sources changed before result export") from error
            if projection.manifest["generation_id"] != report.get("index_generation_id"):
                raise AdmissionError("INDEX_STALE", "Search generation changed before result export")
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
            try:
                current = load_current_projection(application.roots, verify_sources=True)
            except ProjectionError as error:
                raise AdmissionError("INDEX_STALE", "Search sources changed during result export") from error
            if current.manifest != projection.manifest:
                raise AdmissionError("INDEX_STALE", "Search generation changed during result export")
            if payload["mode"] in {"vector", "hybrid"}:
                from ..search_modes import index_status

                status = index_status(application.roots, application.handlers.vector_port)
                if (status.get("vector_state") != "ready"
                        or status.get("vector_generation_id") != report.get("vector_generation_id")):
                    raise AdmissionError("INDEX_STALE", "Vector generation changed during result export")
            return {"mode": payload["mode"], "index_generation_id": report.get("index_generation_id"),
                    "vector_generation_id": report.get("vector_generation_id"), "results": results}
        if ingress.resource_path == "work/intake":
            if self.admit_request is None or set(payload) != {"question", "scope"}:
                raise AdmissionError("ADMISSION_REQUIRED", "Owner Director intake is unavailable")
            if not isinstance(payload["question"], str) or not 1 <= len(payload["question"]) <= 8192:
                raise AdmissionError("INVALID_PAYLOAD", "Work question is invalid")
            self._scope(payload["scope"])
            spec, run, action_id, expires_at = self.admit_request(actor, payload)
            return self.exops.submit_work(spec, run, actor=actor,
                                          action_id=action_id, expires_at=expires_at,
                                          selected_scope=payload["scope"])
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
                decision = application.journal.read()["decisions"].get("plan." + intent["effect_id"])
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

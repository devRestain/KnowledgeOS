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

from ..adapters.core import AdmissionError
from ..adapters.owner_journal import read_regular, stamp
from ..adapters.paths import resolve_beneath
from ..application.exops import KnowledgeExOps, OwnerActor
from ..application.knowledge import LocalIdentity


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
                 admit_request: Callable[[OwnerActor, dict[str, Any]], tuple[dict[str, Any], dict[str, Any], str, datetime]] | None = None) -> None:
        if not callable(authenticate_login) or not callable(validate_csrf):
            raise AdmissionError("BINDING_INVALID", "Web ingress needs owner session checks")
        self.exops = exops
        self.authenticate_login = authenticate_login
        self.validate_csrf = validate_csrf
        self.admit_request = admit_request

    def __call__(self, ingress: OwnerIngress) -> dict[str, Any]:
        if ingress.owner_operation_id != "knowledgeos" or ingress.method != "POST":
            raise AdmissionError("OWNER_DENIED", "Web route is outside KnowledgeOS ExOps")
        actor = self.authenticate_login(ingress.actor_login)
        if (actor is None or actor.ingress != "web" or ingress.csrf_token is None
                or not self.validate_csrf(actor, ingress.csrf_token)):
            raise AdmissionError("UNAUTHENTICATED", "Web actor or CSRF binding is invalid")
        if len(ingress.body) > 16384:
            raise AdmissionError("INVALID_PAYLOAD", "Web body exceeds its bound")
        try:
            payload = json.loads(ingress.body)
        except (UnicodeError, ValueError) as error:
            raise AdmissionError("INVALID_PAYLOAD", "Web body must be JSON") from error
        if not isinstance(payload, dict):
            raise AdmissionError("INVALID_PAYLOAD", "Web body must be an object")
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
        if ingress.resource_path == "work/intake":
            if self.admit_request is None or set(payload) != {"question", "scope"}:
                raise AdmissionError("ADMISSION_REQUIRED", "Owner Director intake is unavailable")
            if not isinstance(payload["question"], str) or not 1 <= len(payload["question"]) <= 8192:
                raise AdmissionError("INVALID_PAYLOAD", "Work question is invalid")
            scope = payload["scope"]
            if (not isinstance(scope, dict) or set(scope) != {"path", "content_sha256"}
                    or not isinstance(scope["path"], str)
                    or not isinstance(scope["content_sha256"], str)
                    or re.fullmatch(r"[0-9a-f]{64}", scope["content_sha256"]) is None):
                raise AdmissionError("INVALID_PAYLOAD", "Work scope must bind one exact note digest")
            self.exops.application._source_policy(scope["path"])
            if self.exops.application.reference(scope["path"])["source_revision"] != scope["content_sha256"]:
                raise AdmissionError("RESOURCE_STALE", "Selected note changed before Director admission")
            spec, run, action_id, expires_at = self.admit_request(actor, payload)
            return self.exops.submit_work(spec, run, actor=actor,
                                          action_id=action_id, expires_at=expires_at)
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
                return {"proposal_reference": reference, "text": raw.decode("utf-8"),
                        "source_reference": current_source,
                        "source_path": intent["source_path"],
                        "source_digest": intent["source_digest"],
                        "source_stale": current_source is None or current_source["content_digest"] != intent["source_digest"],
                        "target_path": target_path, "diff": diff,
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

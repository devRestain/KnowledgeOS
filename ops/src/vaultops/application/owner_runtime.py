"""Explicit writable Owner composition and restart-safe outbox supervision.

Private policy is an owner registration, not a catalog-derived grant. Imports do
not initialize State, start a Worker, dispatch Work, or deploy services.
"""
from __future__ import annotations

import asyncio
import copy
from collections.abc import Mapping
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID

from jsonschema import Draft202012Validator

from ..adapters.core import AdmissionError, canonical, digest
from ..adapters.owner_journal import stamp
from ..adapters.paths import resolve_beneath
from ..projection import load_current_projection
from .exops import KnowledgeExOps, OwnerActor, work_workflow_type
from .knowledge import DomainHandlers, KnowledgeApplication, LocalIdentity
from .owner_authority import authority_gate_digest, read_owner_policy, validate_owner_authority
from .team_catalog import SpecialistBinding, TeamBinding
from .temporal_work import dispatch_admitted_work, reconcile_unknown_dispatch

_DAILY_METHOD = "knowledgeos.daily_corpus_stewardship_team"
_TEXT_PARAMETER = {"type": "string", "minLength": 1, "maxLength": 512, "pattern": r"\S"}


def selected_parameter_schema(method):
    """Public task inputs, independent of private installation defaults."""
    from .temporal_contract import CONTINUATION_METHOD_ID, CURATION_METHOD_ID, STEERING_METHOD_ID
    properties, required = {}, []
    if method in {CURATION_METHOD_ID, STEERING_METHOD_ID}:
        pass
    elif method == CONTINUATION_METHOD_ID:
        properties = {
            "control_checkpoint": {"type": "boolean", "default": False},
            "continuation_policy": {
                "type": "object", "additionalProperties": False,
                "required": ["max_segments", "max_repeated_gap_assessments", "max_profile_invocations"],
                "properties": {name: {"type": "integer", "minimum": 1, "maximum": maximum}
                               for name, maximum in (("max_segments", 256),
                                   ("max_repeated_gap_assessments", 256), ("max_profile_invocations", 4096))}},
        }
    elif method == "knowledgeos.cited_research_team":
        properties = {"question": dict(_TEXT_PARAMETER)}
    elif method in {"knowledgeos.cited_draft_team", "knowledgeos.project_review_team"}:
        fields = (("target_path", "target_type", "title") if method == "knowledgeos.cited_draft_team"
                  else ("period_start", "period_end", "query", "base", "view"))
        properties = {
            "objective": dict(_TEXT_PARAMETER),
            "scope": {"type": "object", "additionalProperties": False, "required": list(fields),
                      "properties": {name: dict(_TEXT_PARAMETER) for name in fields}},
        }
        required = ["scope"]
    elif method == "knowledgeos.knowledge_exchange_team":
        properties = {"gateway_receipt_id": dict(_TEXT_PARAMETER), "objective": dict(_TEXT_PARAMETER)}
        required = ["gateway_receipt_id"]
    else:
        from .temporal_contract import NATIVE_TEAM_ROUTES
        require(method in NATIVE_TEAM_ROUTES, "Method has no selected admission schema", "UNAVAILABLE")
        properties = {"objective": dict(_TEXT_PARAMETER)}
    return {"type": "object", "additionalProperties": False, "properties": properties,
            "required": required}


def require(condition, message, code="BINDING_INVALID"):
    if not condition:
        raise AdmissionError(code, message)


class OwnerRuntimePolicy:
    """Reread an exact private registration at each authority boundary."""
    def __init__(self, path: Path):
        self.path = path.absolute()
        self.initial = self.read()

    def read(self):
        return read_owner_policy(self.path)

    def actor_allowed(self, actor, action):
        selected = self.read()
        return (actor == OwnerActor(self.initial["actor_id"], "web")
                and selected["actor_id"] == actor.actor_id and action in selected["actions"])

    def method_current(self, method_id):
        current = self.read()
        return (current["actor_id"] == self.initial["actor_id"]
                and current["worker_identity"] == self.initial["worker_identity"]
                and current["director_enabled"] and "submit_work" in current["actions"]
                and (method_id != _DAILY_METHOD or current["daily_enabled"])
                and current["methods"].get(method_id) == self.initial["methods"].get(method_id)
                and method_id in current["methods"])


def decode_team(value):
    selected = copy.deepcopy(value)
    selected["specialists"] = tuple(SpecialistBinding(**item) for item in selected["specialists"])
    return TeamBinding(**selected)


def concrete_method(method_id):
    """Only these concrete implementations are executable; no generic fallback."""
    from .authoring_temporal import authoring_worker_components
    from .authoring_work import POLICIES as authoring
    from .authoring_work import AuthoringExecutor, authoring_admission, authoring_native_digest
    from .boundary_temporal import exchange_worker_components, maintenance_worker_components
    from .boundary_work import (
        EXCHANGE_POLICY,
        MAINTENANCE_POLICY,
        ExchangeTeamExecutor,
        MaintenanceTeamExecutor,
        exchange_admission,
        exchange_native_digest,
        maintenance_admission,
        maintenance_native_digest,
    )
    from .curation_continuation import (
        CurationContinuationExecutor,
        curation_continuation_admission,
        curation_continuation_native_digest,
    )
    from .curation_continuation_temporal import continuation_worker_components
    from .curation_steering import (
        SafeCurationSteering,
        curation_steering_native_digest,
        steering_admission,
    )
    from .curation_steering_temporal import steering_worker_components
    from .curation_temporal import team_worker_components
    from .curation_work import CurationTeamExecutor, curation_admission, curation_native_digest
    from .metadata_temporal import metadata_worker_components
    from .metadata_work import POLICIES as metadata
    from .metadata_work import MetadataTeamExecutor, metadata_admission, metadata_native_digest
    from .research_temporal import research_worker_components
    from .research_work import (
        METHOD_ID,
        ResearchTeamExecutor,
        research_admission,
        research_native_digest,
    )
    from .temporal_contract import CONTINUATION_METHOD_ID, CURATION_METHOD_ID, STEERING_METHOD_ID
    if method_id == CURATION_METHOD_ID:
        return (lambda exops, runner: CurationTeamExecutor(exops, runner=runner),
                curation_admission, curation_native_digest, team_worker_components)
    if method_id == CONTINUATION_METHOD_ID:
        return (lambda exops, runner: CurationContinuationExecutor(exops, runner=runner),
                curation_continuation_admission, curation_continuation_native_digest,
                continuation_worker_components)
    if method_id == STEERING_METHOD_ID:
        return (lambda exops, runner: SafeCurationSteering(exops, runner=runner,
                    director_amendment_admitted=lambda _old, successor, _run: exops.director_admitted(successor)),
                steering_admission, curation_steering_native_digest,
                lambda _exops, controller: steering_worker_components(controller))
    if method_id in metadata:
        return (lambda exops, runner: MetadataTeamExecutor(exops, runner=runner, method_id=method_id),
                lambda app, team, source, **kw: metadata_admission(app, team, source, method_id=method_id, **kw),
                lambda route: metadata_native_digest(route, method_id), metadata_worker_components)
    if method_id in authoring:
        return (lambda exops, runner: AuthoringExecutor(exops, runner=runner, method_id=method_id),
                lambda app, team, source, **kw: authoring_admission(app, team, source, method_id=method_id, **kw),
                lambda route: authoring_native_digest(route, method_id), authoring_worker_components)
    if method_id == METHOD_ID:
        return (lambda exops, runner: ResearchTeamExecutor(exops, runner=runner), research_admission,
                research_native_digest, research_worker_components)
    if method_id == EXCHANGE_POLICY.method_id:
        return (lambda exops, runner: ExchangeTeamExecutor(exops, runner=runner), exchange_admission,
                exchange_native_digest, exchange_worker_components)
    if method_id == MAINTENANCE_POLICY.method_id:
        return (lambda exops, runner: MaintenanceTeamExecutor(exops, runner=runner), maintenance_admission,
                maintenance_native_digest, maintenance_worker_components)
    raise AdmissionError("UNAVAILABLE", "Selected method has no writable Owner implementation")


class TrustedKnowledgeExOps(KnowledgeExOps):
    """HTTP cannot replace the Director's admitted initial run, expiry or action."""
    def submit_work(self, spec, run, *, actor, action_id, expires_at, **kwargs):
        self._guard(actor, "submit_work")
        require(isinstance(spec, Mapping) and isinstance(run, Mapping),
                "Work needs exact Director records", "INVALID_PAYLOAD")
        receipt = self.application.journal.read()["intents"].get(
            "owner.director." + spec.get("admission_receipt_id", ""))
        require(isinstance(receipt, dict) and receipt["spec"] == spec
                and receipt["run"] == run and receipt["actor_id"] == actor.actor_id
                and datetime.fromisoformat(receipt["expires_at"]) == expires_at
                and action_id == run["work_run_id"].replace("workrun.", "action.", 1)
                and kwargs.get("method_source_reference") == receipt["source"],
                "Work differs from the exact durable Director admission", "ADMISSION_REQUIRED")
        defaults = receipt["authority"]["registration"]["admission_scope"]["parameters"]
        parameters = {**defaults, **receipt["request"].get("parameters", {})}
        require(kwargs.get("control_checkpoint", False) == parameters.get("control_checkpoint", False),
                "Control mode differs from the Director's admitted task", "ADMISSION_REQUIRED")
        self.contracts.spec(spec)
        self.contracts.run(run, spec)
        self._team_binding(spec, run)
        require(self.director_admitted(spec) and self.director_method_source_admitted(spec, receipt["source"]),
                "Director source or binding is no longer current", "ADMISSION_REQUIRED")
        body = {"spec": dict(spec), "run": dict(run), "actor_id": actor.actor_id,
                "action_id": action_id, "expires_at": stamp(expires_at),
                "method_source_reference": dict(receipt["source"])}
        if kwargs.get("selected_scope") is not None:
            body["selected_scope"] = dict(kwargs["selected_scope"])
        if kwargs.get("control_checkpoint") is True:
            body["control_checkpoint"] = True
        with self.application.journal.writer() as session:
            self._guard(actor, "submit_work")
            existing = session.value["intents"].get("exops.work." + run["work_run_id"])
            if existing is not None:
                validate_owner_authority(self.application, session.value, existing)
                require(existing["content_digest"] == digest(canonical(body))
                        and existing["actor_id"] == actor.actor_id
                        and existing["actor_ingress"] == actor.ingress
                        and existing["action_id"] == action_id and existing["spec"] == spec
                        and existing["expires_at"] == stamp(expires_at)
                        and existing["method_source_reference"] == receipt["source"],
                        "Existing Work differs from exact Director admission", "IDEMPOTENCY_CONFLICT")
                return {**copy.deepcopy(existing["receipt"]), "replayed": True}
        return super().submit_work(spec, run, actor=actor, action_id=action_id,
                                   expires_at=expires_at, **kwargs)


class WritableOwnerRuntime:
    def __init__(self, roots, *, policy: OwnerRuntimePolicy, runners: Mapping[str, Any],
                 vector_port=None):
        self.policy = policy
        selected = policy.initial
        require(all(getattr(roots, key).is_dir() for key in ("core", "control", "vault", "state", "runtime")),
                "Writable Owner requires five existing roots")
        require(resolve_beneath(roots.state, "owner-control.json").is_file(),
                "Writable Owner requires an initialized journal", "STATE_MISSING")
        self.application = KnowledgeApplication.from_roots(roots, identity=LocalIdentity(
            selected["actor_id"], "operator", local_execution=True),
            handlers=DomainHandlers(vector_port=vector_port))
        self.application.journal.read()
        load_current_projection(roots, verify_sources=True)
        self.actor = OwnerActor(selected["actor_id"], "web")
        self.runners, self.method_teams, self.factories = {}, {}, {}
        for method, registration in selected["methods"].items():
            require(isinstance(registration, dict) and set(registration) == {
                "team_binding", "runner_binding", "admission_scope"}, "Method registration is incomplete")
            factory = concrete_method(method)
            team = decode_team(registration["team_binding"])
            runner = runners.get(method)
            require(runner is not None and callable(runner) and runner.binding == registration["runner_binding"]
                    and team.native_binding_digest == factory[2](runner.binding),
                    "Method needs its exact current native Runner and executable digest")
            self.method_teams[method], self.runners[method], self.factories[method] = team, runner, factory
        require(not selected["daily_enabled"] or "knowledgeos.daily_corpus_stewardship_team" in self.runners,
                "Manual daily admission requires its explicitly bound native method")
        from .daily_scheduler import DailyAdmissionScheduler
        self.daily_completions = (DailyAdmissionScheduler(
            self.application, admit_request=self.admit_daily_request,
            authorize=lambda: self.method_current(_DAILY_METHOD)
                and self.policy.actor_allowed(self.actor, "submit_work"))
            if _DAILY_METHOD in self.runners else None)
        require(not selected["freeform_research"] or "knowledgeos.cited_research_team" in self.runners,
                "Freeform research requires its explicitly bound native method")
        self.exops = TrustedKnowledgeExOps(self.application, authorize_actor=policy.actor_allowed,
            director_admitted=self.director_admitted, director_method_source_admitted=self.source_admitted,
            current_method_binding=self.current_method_team, method_teams=self.method_teams,
            worker_identity=selected["worker_identity"])
        self.executors = {method: self.factories[method][0](self.exops, runner)
                          for method, runner in self.runners.items()}
        from .curation_control import CurationRunControl
        from .temporal_contract import CONTINUATION_METHOD_ID
        if CONTINUATION_METHOD_ID in self.executors:
            self.curation_control = CurationRunControl(self.exops, self.executors[CONTINUATION_METHOD_ID])

    def authorize_index_publication(self):
        """Recheck the selected local owner and existing apply pins at promotion."""
        require(self.policy.actor_allowed(self.actor, "publish_index"),
                "Private owner policy denies index publication", "ACTION_DENIED")
        self.application.guard("apply")

    def current_method_team(self, method):
        if method not in self.method_teams or not self.method_current(method):
            return None
        return self.method_teams[method]

    def _admission_source(self, method, source):
        registration = self.policy.initial["methods"][method]["admission_scope"]
        require(isinstance(registration, dict) and set(registration) == {"source_paths", "parameters"}
                and isinstance(registration["source_paths"], list)
                and all(isinstance(path, str) for path in registration["source_paths"])
                and isinstance(registration["parameters"], dict), "Invalid owner-selected admission scope")
        path = self.application.resolve_reference(dict(source))
        require(path in registration["source_paths"] or registration["source_paths"] == ["*"],
                "Source is outside owner-selected admission scope", "SCOPE_DENIED")
        self.application._mcp_source_policy(path)
        require(self.application.reference(path) == source, "Selected source changed", "RESOURCE_STALE")
        return registration["parameters"]

    def method_current(self, method):
        return (self.policy.method_current(method)
                and self.runners[method].binding == self.policy.initial["methods"][method]["runner_binding"]
                and self.factories[method][2](self.runners[method].binding)
                == self.policy.initial["methods"][method]["team_binding"]["native_binding_digest"])

    def director_admitted(self, spec):
        method = spec.get("method_contract_ref", {}).get("method_id")
        if method not in self.runners or not self.method_current(method):
            return False
        record = self.application.journal.read()["intents"].get(
            "owner.director." + spec.get("admission_receipt_id", ""))
        if (not isinstance(record, dict) or record.get("state") != "completed"
                or not isinstance(record.get("director_evidence"), dict)
                or record.get("spec") != dict(spec) or record.get("actor_id") != self.actor.actor_id):
            return False
        current, request = self.policy.read(), record["request"]
        return (("template_id" not in request or request["template_id"] in current["templates"])
                and ("question" not in request or current["freeform_research"])
                and ("method_contract_ref" not in request or current["daily_enabled"]))

    def source_admitted(self, spec, source):
        if not self.director_admitted(spec):
            return False
        record = self.application.journal.read()["intents"]["owner.director." + spec["admission_receipt_id"]]
        if record["source"] != dict(source):
            return False
        try:
            self._admission_source(spec["method_contract_ref"]["method_id"], source)
            return True
        except (ValueError, OSError):
            return False

    def _director_authority(self, method):
        return {"policy_path": str(self.policy.path), "actor_id": self.actor.actor_id,
                "worker_identity": self.policy.initial["worker_identity"], "method_id": method,
                "registration": copy.deepcopy(self.policy.initial["methods"][method]),
                "gate_digest": authority_gate_digest()}

    def _replay_director(self, admission_id, source, request):
        previous = self.application.journal.read()["intents"].get("owner.director." + admission_id)
        if previous is None:
            return None
        require(previous["request"] == request and previous["source"] == source,
                "Director identity belongs to different content", "IDEMPOTENCY_CONFLICT")
        require(previous.get("state") == "completed"
                and isinstance(previous.get("director_evidence"), dict),
                "Director judgment is not completed; retained outcome requires reconciliation",
                previous.get("failure_code") or "DIRECTOR_OUTCOME_UNKNOWN")
        spec, run = copy.deepcopy(previous["spec"]), copy.deepcopy(previous["run"])
        expiry = self._retain(spec, run, source, request, previous["director_evidence"])
        return spec, run, expiry

    def _judge_director(self, spec, run, source, request):
        from .agent_decisions import role_decision_sync, validate_role_evidence
        from .director_agent import INSTRUCTIONS, compile_director_decision, director_context
        method = spec["method_contract_ref"]["method_id"]
        replay = self._replay_director(spec["admission_receipt_id"], source, request)
        if replay is not None:
            return replay
        team = self.method_teams[method]
        parameters = self._request_parameters(method, request)
        context = director_context(self.application, team, spec, run, source, request, parameters)
        key = "owner.director." + spec["admission_receipt_id"]
        now = self.application.clock()
        authority = self._director_authority(method)
        with self.application.journal.writer() as session:
            require(key not in session.value["intents"],
                    "Director judgment is already retained or in flight", "DIRECTOR_OUTCOME_UNKNOWN")
            require(self.method_current(method), "Director binding was revoked", "ADMISSION_REQUIRED")
            session.value["intents"][key] = {
                "state": "inference_pending", "recorded_at": stamp(now),
                "actor_id": self.actor.actor_id, "spec": copy.deepcopy(spec), "run": copy.deepcopy(run),
                "source": copy.deepcopy(source), "request": copy.deepcopy(request),
                "authority": authority,
                "decision_request_digest": digest(canonical(context))}
            session.commit(now)
        evidence = None
        try:
            decision, evidence = role_decision_sync(
                self.runners[method], role_id="knowledgeos.director", responsibility="director",
                method_id=method, context=context, instructions=INSTRUCTIONS,
                record={"spec": spec, "run": run})
            validate_role_evidence(self.application.roots.control, "knowledgeos.director", evidence["response"], tools=[])
            with self.application.journal.writer() as session:
                pending = session.value["intents"][key]
                require(pending["state"] == "inference_pending"
                        and pending["authority"] == authority,
                        "Director inference intent changed", "DIRECTOR_OUTCOME_UNKNOWN")
                pending.update(state="decision_received", director_evidence=copy.deepcopy(evidence))
                session.commit(self.application.clock())
            spec, run = compile_director_decision(self.application, team, context, decision)
            expiry = self._retain(spec, run, source, request, evidence)
            return spec, run, expiry
        except Exception as error:
            with self.application.journal.writer() as session:
                pending = session.value["intents"][key]
                if pending["state"] != "completed":
                    known = evidence is not None or (
                        isinstance(error, AdmissionError) and error.code == "AGENT_DECISION_INVALID")
                    pending["state"] = "rejected" if known else "unknown"
                    pending["failure_code"] = (getattr(error, "code", "AGENT_DECISION_INVALID")
                                               if known else "DIRECTOR_OUTCOME_UNKNOWN")
                    session.commit(self.application.clock())
            raise

    def _retain(self, spec, run, source, request, director_evidence):
        method = spec["method_contract_ref"]["method_id"]
        require(self.method_current(method), "Director or native binding was revoked", "ADMISSION_REQUIRED")
        self._admission_source(method, source)
        self.exops.contracts.run(run, spec)
        key = "owner.director." + spec["admission_receipt_id"]
        now = self.application.clock()
        authority = self._director_authority(method)
        with self.application.journal.writer() as session:
            require(self.method_current(method) and self.application.reference(
                self.application.resolve_reference(dict(source))) == source,
                "Director source or binding changed", "ADMISSION_REQUIRED")
            previous = session.value["intents"].get(key)
            require(isinstance(previous, dict) and previous["request"] == request
                    and previous["source"] == source and previous.get("authority") == authority
                    and previous.get("director_evidence") == director_evidence,
                    "Director identity belongs to different content", "IDEMPOTENCY_CONFLICT")
            if previous["state"] == "completed":
                require(previous["spec"] == spec and previous["run"] == run,
                        "Director identity belongs to different content", "IDEMPOTENCY_CONFLICT")
                return datetime.fromisoformat(previous["expires_at"])
            require(previous["state"] == "decision_received",
                    "Director decision has no completed inference evidence", "ADMISSION_REQUIRED")
            expiry = now + timedelta(hours=1)
            previous.update(state="completed", spec=copy.deepcopy(spec), run=copy.deepcopy(run),
                            expires_at=stamp(expiry))
            session.commit(now)
            return expiry

    def admit_template(self, actor, template_id, scope, request_id):
        from .intake_templates import build_template_admissions
        self.exops._guard(actor, "submit_work")
        require(template_id in self.policy.read()["templates"], "Template is not selected", "ACTION_DENIED")
        methods = {"note_metadata": ("knowledgeos.note_metadata_team", "knowledgeos.relation_ontology_team"),
                   "document_enhancement": ("knowledgeos.cited_research_team",)}
        require(template_id in methods and all(method in self.runners for method in methods[template_id]),
                "Template native bindings are absent", "UNAVAILABLE")
        request = {"template_id": template_id, "scope": dict(scope), "request_id": request_id}
        def receipt(_template, team, _scope, _request):
            return "admission.owner." + digest(canonical({**request, "team_id": team}))[7:39]
        source = self.application.reference(scope["path"])
        require(source["source_revision"] == scope["content_sha256"],
                "Template source changed", "RESOURCE_STALE")
        replayed = {}
        for method in methods[template_id]:
            team_id = self.policy.initial["methods"][method]["team_binding"]["team_id"]
            previous = self._replay_director(receipt(template_id, team_id, scope, request_id),
                                             source, request)
            if previous is not None:
                spec, run, expiry = previous
                replayed[method] = (spec, run, run["work_run_id"].replace("workrun.", "action.", 1),
                                    expiry, source)
        if len(replayed) == len(methods[template_id]):
            return [replayed[method] for method in methods[template_id]]
        kwargs = {}
        if template_id == "note_metadata":
            routes = [self.runners[method].binding for method in methods[template_id]]
            require(routes[0] == routes[1], "Metadata template requires the same explicit native adapter")
            kwargs["native_metadata_runner_binding"] = routes[0]
        else:
            kwargs["native_research_runner_binding"] = self.runners[methods[template_id][0]].binding
        admissions = build_template_admissions(self.exops, template_id, scope, request_id,
                                               issue_director_receipt=receipt, **kwargs)
        accepted = []
        for spec, run, action, _expiry, source in admissions:
            method = spec["method_contract_ref"]["method_id"]
            if method in replayed:
                accepted.append(replayed[method])
            else:
                spec, run, expiry = self._judge_director(spec, run, source, request)
                accepted.append((spec, run, action, expiry, source))
        return accepted

    def admit_request(self, actor, payload):
        """Freeform is explicitly only native research, with the original question."""
        from .research_work import METHOD_ID
        self.exops._guard(actor, "submit_work")
        require(self.policy.read()["freeform_research"], "Freeform research is disabled", "ACTION_DENIED")
        require(set(payload) == {"question", "scope"} and isinstance(payload["question"], str)
                and 1 <= len(payload["question"].strip()) <= 512,
                "Native research requires a bounded exact question", "INVALID_PAYLOAD")
        source = self.application.reference(payload["scope"]["path"])
        require(source["source_revision"] == payload["scope"]["content_sha256"],
                "Research scope changed", "RESOURCE_STALE")
        self._admission_source(METHOD_ID, source)
        identity = digest(canonical(payload))[7:39]
        team = self.method_teams[METHOD_ID]
        replay = self._replay_director("admission.owner." + identity, source, payload)
        if replay is None:
            spec, run = self.factories[METHOD_ID][1](self.application, team, source,
                work_id="work.owner." + identity, work_run_id="workrun.owner." + identity,
                admission_receipt_id="admission.owner." + identity,
                runner_binding=self.runners[METHOD_ID].binding, question=payload["question"])
            replay = self._judge_director(spec, run, source, payload)
        spec, run, expiry = replay
        return spec, run, "action.owner." + identity, expiry

    def _selected_parameters(self, method):
        """Validate optional installation defaults without requiring task inputs."""
        scope = self.policy.initial["methods"][method]["admission_scope"]
        require(isinstance(scope, dict) and set(scope) == {"source_paths", "parameters"}
                and isinstance(scope["source_paths"], list) and bool(scope["source_paths"])
                and all(isinstance(path, str) and bool(path) for path in scope["source_paths"])
                and isinstance(scope["parameters"], dict),
                "Method has no safe selected admission configuration", "UNAVAILABLE")
        schema = selected_parameter_schema(method)
        schema["required"] = []
        require(Draft202012Validator(schema).is_valid(scope["parameters"]),
                "Method has invalid typed admission defaults", "UNAVAILABLE")
        return copy.deepcopy(scope["parameters"])

    def _request_parameters(self, method, request):
        parameters = self._selected_parameters(method)
        selected = request.get("parameters", {})
        require(isinstance(selected, dict), "Task parameters must be an object", "INVALID_PAYLOAD")
        parameters.update(copy.deepcopy(selected))
        if method == "knowledgeos.cited_research_team" and "question" in request:
            parameters["question"] = request["question"]
        require(Draft202012Validator(selected_parameter_schema(method)).is_valid(parameters),
                "Task parameters do not satisfy the selected method schema", "INVALID_PAYLOAD")
        return parameters

    def selected_methods(self, actor):
        from .work_methods import WorkMethodCatalog
        self.exops._guard(actor, "read_work")
        methods = []
        for method, definition in WorkMethodCatalog.load(self.application.roots.control).methods.items():
            parameters, available, reason = {}, False, "No owner-registered executable binding"
            if method in self.runners:
                if not self.policy.actor_allowed(actor, "submit_work") or not self.method_current(method):
                    reason = "Owner submission authority or native binding is unavailable"
                else:
                    try:
                        parameters = self._selected_parameters(method)
                    except AdmissionError:
                        reason = "Method has no safe selected admission configuration"
                    else:
                        available, reason = True, None
            methods.append({"method_id": method, "purpose": definition["purpose"],
                            "available": available, "reason": reason, "parameters": parameters,
                            "parameter_schema": selected_parameter_schema(method)})
        return {"methods": methods, "observed_at": stamp(self.application.clock())}

    def admit_selected_request(self, actor, payload):
        """Admit typed user task inputs through the actual Director."""
        self.exops._guard(actor, "submit_work")
        require(isinstance(payload, dict) and set(payload) == {
                    "method_id", "scope", "request_id", "parameters"}
                and isinstance(payload["parameters"], dict)
                and isinstance(payload["method_id"], str) and isinstance(payload["request_id"], str),
                "Selected Work request shape is invalid", "INVALID_PAYLOAD")
        try:
            valid_id = str(UUID(payload["request_id"])) == payload["request_id"]
        except ValueError:
            valid_id = False
        require(valid_id, "Selected Work requires a canonical UUID", "INVALID_PAYLOAD")
        method, scope = payload["method_id"], payload["scope"]
        require(method in self.runners and self.method_current(method), "Method is unavailable", "UNAVAILABLE")
        self._request_parameters(method, payload)
        require(isinstance(scope, dict) and set(scope) == {"path", "content_sha256"}
                and isinstance(scope["path"], str) and isinstance(scope["content_sha256"], str),
                "Selected Work requires one exact source", "INVALID_PAYLOAD")
        source = self.application.reference(scope["path"])
        require(source["source_revision"] == scope["content_sha256"], "Selected source changed", "RESOURCE_STALE")
        return self.admit_selected(method, source, request_id=payload["request_id"], request=payload)

    def admit_selected(self, method, source, *, request_id, request):
        self.exops._guard(self.actor, "submit_work")
        require(method in self.runners and self.method_current(method), "Method is unavailable", "UNAVAILABLE")
        if method == _DAILY_METHOD and "method_contract_ref" in request:
            self._validate_daily_request(request)
            require(source == request["source_reference"] and request_id == request["request_id"],
                    "Daily admission differs from its exact request", "SCOPE_DENIED")
        identity = digest(canonical({"method": method, "request_id": request_id}))[7:39]
        team = self.method_teams[method]
        self._admission_source(method, source)
        admission = self._request_parameters(method, request)
        control_checkpoint = admission.pop("control_checkpoint", False)
        daily_key = None
        if method == _DAILY_METHOD and "method_contract_ref" not in request:
            daily_key = self.daily_completions.prepare_manual(
                source, request, "workrun.owner." + identity)
        replay = self._replay_director("admission.owner." + identity, source, request)
        if replay is None:
            spec, run = self.factories[method][1](self.application, team, source,
                work_id="work.owner." + identity, work_run_id="workrun.owner." + identity,
                admission_receipt_id="admission.owner." + identity,
                runner_binding=self.runners[method].binding, **admission)
            replay = self._judge_director(spec, run, source, request)
        spec, run, expiry = replay
        receipt = self.exops.submit_work(spec, run, actor=self.actor, action_id="action.owner." + identity,
            expires_at=expiry, method_source_reference=source, control_checkpoint=control_checkpoint,
            selected_scope={"path": self.application.resolve_reference(dict(source)),
                            "content_sha256": source["source_revision"]})
        if daily_key is not None:
            record = self.application.journal.read()["intents"][daily_key]
            self.daily_completions._manual_receipt(daily_key, record)
        return receipt

    def _validate_daily_request(self, request):
        from .daily_stewardship import METHOD_ID, DailyStewardship
        from .work_methods import WorkMethodCatalog
        require(isinstance(request, dict) and set(request) == {
                    "request_id", "source_reference", "method_contract_ref", "empty_fields"}
                and isinstance(request["source_reference"], dict)
                and isinstance(request["method_contract_ref"], dict)
                and isinstance(request["empty_fields"], list),
                "Daily requires its exact source coverage request", "INVALID_PAYLOAD")
        require(self.policy.read()["daily_enabled"], "Daily admission is disabled", "ACTION_DENIED")
        require(request["method_contract_ref"] == WorkMethodCatalog.load(
            self.application.roots.control).reference(METHOD_ID), "Daily method changed")
        stewardship = DailyStewardship(self.application)
        # Recompute the complete source/registry/policy-bound request, not its ID alone.
        expected = stewardship._work_request(request["source_reference"], request["method_contract_ref"],
            request["empty_fields"], stewardship._registry_digest(), stewardship._policy_digest())
        require(request == expected, "Daily registry or policy changed", "RESOURCE_STALE")

    def admit_daily_request(self, request):
        from .daily_stewardship import METHOD_ID
        return self.admit_selected(METHOD_ID, request["source_reference"],
            request_id=request["request_id"], request=request)

    def worker_components(self):
        from .curation_control_temporal import control_worker_components
        from .temporal_contract import CONTINUATION_METHOD_ID
        components = [self.factories[method][3](self.exops, executor)
                      for method, executor in self.executors.items()]
        if CONTINUATION_METHOD_ID in self.executors:
            components.append(control_worker_components(
                self.exops, self.executors[CONTINUATION_METHOD_ID], self.curation_control))
        return components

    def combined_worker_components(self):
        """One official plugin owns all statically selected graph namespaces."""
        from importlib import import_module

        from temporalio.langgraph import LangGraphPlugin

        from .curation_continuation_temporal import continuation_graph_components
        from .curation_control_temporal import control_graph_components
        from .curation_steering_temporal import steering_graph_components
        from .curation_temporal import curation_graph_components
        from .native_team_temporal import native_team_graph
        from .research_temporal import research_team_graph
        from .temporal_contract import (
            AUTHORING_DRAFT_GRAPH_NAME,
            AUTHORING_DRAFT_WORKFLOW_TYPE,
            AUTHORING_REVIEW_GRAPH_NAME,
            AUTHORING_REVIEW_WORKFLOW_TYPE,
            CONTINUATION_METHOD_ID,
            CURATION_METHOD_ID,
            DAILY_GRAPH_NAME,
            DAILY_WORKFLOW_TYPE,
            EXCHANGE_GRAPH_NAME,
            EXCHANGE_WORKFLOW_TYPE,
            MAINTENANCE_GRAPH_NAME,
            MAINTENANCE_WORKFLOW_TYPE,
            METADATA_GRAPH_NAME,
            METADATA_WORKFLOW_TYPE,
            RELATION_GRAPH_NAME,
            RELATION_WORKFLOW_TYPE,
            RESEARCH_GRAPH_NAME,
            RESEARCH_WORKFLOW_TYPE,
            STEERING_METHOD_ID,
        )
        selections = {
            "knowledgeos.note_metadata_team": ("metadata_workflow", "KnowledgeOSNoteMetadataWorkflow", METADATA_GRAPH_NAME, METADATA_WORKFLOW_TYPE),
            "knowledgeos.relation_ontology_team": ("metadata_workflow", "KnowledgeOSRelationOntologyWorkflow", RELATION_GRAPH_NAME, RELATION_WORKFLOW_TYPE),
            "knowledgeos.daily_corpus_stewardship_team": ("metadata_workflow", "KnowledgeOSDailyCorpusWorkflow", DAILY_GRAPH_NAME, DAILY_WORKFLOW_TYPE),
            "knowledgeos.cited_draft_team": ("authoring_workflow", "KnowledgeOSCitedDraftWorkflow", AUTHORING_DRAFT_GRAPH_NAME, AUTHORING_DRAFT_WORKFLOW_TYPE),
            "knowledgeos.project_review_team": ("authoring_workflow", "KnowledgeOSProjectReviewWorkflow", AUTHORING_REVIEW_GRAPH_NAME, AUTHORING_REVIEW_WORKFLOW_TYPE),
            "knowledgeos.knowledge_exchange_team": ("boundary_workflow", "KnowledgeOSExchangeWorkflow", EXCHANGE_GRAPH_NAME, EXCHANGE_WORKFLOW_TYPE),
            "knowledgeos.vault_maintenance_team": ("boundary_workflow", "KnowledgeOSMaintenanceWorkflow", MAINTENANCE_GRAPH_NAME, MAINTENANCE_WORKFLOW_TYPE),
            "knowledgeos.cited_research_team": ("research_workflow", "KnowledgeOSEvidenceResearchWorkflow", RESEARCH_GRAPH_NAME, RESEARCH_WORKFLOW_TYPE),
        }
        workflows, graphs, activities = [], {}, []
        for method, executor in self.executors.items():
            components = []
            if method == CURATION_METHOD_ID:
                components.append(curation_graph_components(self.exops, executor))
            elif method == CONTINUATION_METHOD_ID:
                components.extend((
                    continuation_graph_components(self.exops, executor),
                    control_graph_components(self.exops, executor, self.curation_control),
                ))
            elif method == STEERING_METHOD_ID:
                components.append(steering_graph_components(executor))
            else:
                module, name, graph, wire = selections[method]
                workflow = getattr(import_module("." + module, __package__), name)
                selected_graph = (research_team_graph(self.exops, executor)
                    if method == "knowledgeos.cited_research_team"
                    else native_team_graph(self.exops, executor, workflow_type=wire))
                components.append((workflow, {graph: selected_graph}, []))
            for workflow, selected_graphs, extra_activities in components:
                require(not graphs.keys() & selected_graphs.keys(),
                        "Worker graph namespaces must be unique")
                workflows.append(workflow)
                graphs.update(selected_graphs)
                activities.extend(extra_activities)
        plugin = LangGraphPlugin(graphs=graphs)
        plugin.activities.extend(activities)
        return workflows, plugin


class DispatchSupervisor:
    """One fenced send per persisted pending intent; unknown is observation only."""
    def __init__(self, runtime: WritableOwnerRuntime, clients: Mapping[str, Any], *, limit=50,
                 control_client=None, steering_client=None):
        require(type(limit) is int and 1 <= limit <= 50, "Invalid supervisor limit")
        self.runtime, self.clients, self.limit = runtime, dict(clients), limit
        self.control_client, self.steering_client = control_client, steering_client
        self._cursor, self._command_cursor = 0, 0

    async def tick(self):
        outcomes = []
        journal = self.runtime.application.journal.read()
        records = [(key.removeprefix("exops.work."), item) for key, item in journal["intents"].items()
                   if key.startswith("exops.work.") and item.get("dispatch_state") in {"pending", "unknown"}]
        if records:
            offset = self._cursor % len(records)
            records = records[offset:] + records[:offset]
            self._cursor = offset + min(len(records), self.limit)
        for work_run_id, record in records[:self.limit]:
            wire = work_workflow_type(record)
            client = self.clients.get(wire)
            if client is None:
                outcomes.append({"work_run_id": work_run_id, "state": "unbound"})
                continue
            try:
                if record["dispatch_state"] == "pending":
                    self.runtime.exops._guard(self.runtime.actor, "submit_work")
                    receipt = await dispatch_admitted_work(self.runtime.exops, client, work_run_id)
                else:
                    receipt = await reconcile_unknown_dispatch(self.runtime.exops, client, work_run_id)
                outcomes.append({"work_run_id": work_run_id, "state": receipt["dispatch_state"]})
            except (AdmissionError, OSError) as error:
                current = self.runtime.application.journal.read()["intents"]["exops.work." + work_run_id]
                outcomes.append({"work_run_id": work_run_id, "state": current["dispatch_state"],
                                 "code": getattr(error, "code", "OBSERVATION_UNAVAILABLE")})
        outcomes.extend(await self._commands())
        daily = self.runtime.daily_completions
        if daily is not None:
            try:
                consumption = daily.consume_completed(manual_only=True)
                outcomes.extend({"daily_consumption": item} for item in consumption["consumed"])
                outcomes.extend({"daily_blocker": item} for item in consumption["blockers"])
            except (AdmissionError, OSError) as error:
                outcomes.append({"daily_blocker": {
                    "code": getattr(error, "code", "OUTCOME_UNKNOWN"), "message": str(error)}})
        return {"outcomes": outcomes}

    async def _commands(self):
        """Fence each accepted manual command before its single native send."""
        if self.control_client is None and self.steering_client is None:
            return []
        from temporalio.exceptions import TemporalError

        from .curation_control_temporal import deliver_control, reconcile_control
        from .curation_steering_temporal import deliver_steering

        app, exops = self.runtime.application, self.runtime.exops
        snapshot = app.journal.read()
        selected = []
        for key, command in snapshot["intents"].items():
            if key.startswith("curation.control."):
                kind, controller, client = "control", getattr(exops, "curation_control", None), self.control_client
            elif key.startswith("curation.steering.command."):
                kind, controller, client = "steering", getattr(exops, "curation_steering", None), self.steering_client
            else:
                continue
            delivery_key = "owner.command_dispatch." + digest(canonical({"command_key": key}))[7:39]
            delivery = snapshot["intents"].get(delivery_key)
            if (controller is not None and client is not None
                    and (command.get("state") == "received"
                         or (delivery is not None and delivery["delivery_state"] == "unknown"))
                    and (delivery is None or delivery["delivery_state"] != "applied")):
                selected.append((key, command, kind, controller, client, delivery_key))
        if selected:
            offset = self._command_cursor % len(selected)
            selected = selected[offset:] + selected[:offset]
            self._command_cursor = offset + min(len(selected), self.limit)
        outcomes = []
        for key, command, kind, controller, client, delivery_key in selected[:self.limit]:
            command_id = command["receipt"]["command_id"]
            work_run_id = command["target"]["work_run_id"]
            try:
                if kind == "control":
                    payload = {"command_id": command_id, "kind": command["body"]["kind"],
                               "target": copy.deepcopy(command["target"])}
                    controller.authorize_dispatch(payload)
                else:
                    payload = controller.command_dispatch(work_run_id, command_id)
                payload_digest = digest(canonical(payload))
                send = False
                with app.journal.writer() as writer:
                    retained = writer.value["intents"].get(delivery_key)
                    if retained is None:
                        require(writer.value["intents"][key] == command,
                                "Command changed before delivery", "STALE_REVISION")
                        writer.value["intents"][delivery_key] = {
                            "command_key": key, "command_id": command_id, "work_run_id": work_run_id,
                            "payload_digest": payload_digest, "delivery_state": "unknown",
                            "attempted_at": stamp(app.clock())}
                        writer.commit(app.clock())
                        send = True
                    else:
                        require(retained["command_key"] == key
                                and retained["payload_digest"] == payload_digest,
                                "Command delivery identity changed", "IDEMPOTENCY_CONFLICT")
                current = app.journal.read()["intents"][key]
                if send:
                    delivery = (deliver_control(controller, client, payload) if kind == "control"
                                else deliver_steering(controller, client, work_run_id, command_id))
                    receipt = await asyncio.wait_for(delivery, timeout=30)
                elif current["state"] == "applied":
                    receipt = (reconcile_control(controller, payload) if kind == "control"
                               else controller.apply(payload, temporal_run_id=payload["target"]["temporal_run_id"]))
                else:
                    outcomes.append({"command_id": command_id, "state": "unknown"})
                    continue
                require(receipt.get("application_state") == "applied",
                        "Native command has no applied Owner receipt", "CONTROL_UNCERTAIN")
                with app.journal.writer() as writer:
                    retained = writer.value["intents"][delivery_key]
                    require(retained["payload_digest"] == payload_digest,
                            "Command delivery changed", "IDEMPOTENCY_CONFLICT")
                    retained.update(delivery_state="applied", observed_at=stamp(app.clock()))
                    writer.commit(app.clock())
                outcomes.append({"command_id": command_id, "state": "applied"})
            except (AdmissionError, OSError, RuntimeError, ValueError, TemporalError) as error:
                outcomes.append({"command_id": command_id, "state": "unknown",
                                 "code": getattr(error, "code", "CONTROL_UNCERTAIN")})
        return outcomes

    async def run(self, stop: asyncio.Event, *, interval=1.0):
        require(0.1 <= interval <= 3600, "Invalid explicit supervisor interval")
        while not stop.is_set():
            await self.tick()
            try:
                await asyncio.wait_for(stop.wait(), timeout=interval)
            except TimeoutError:
                pass

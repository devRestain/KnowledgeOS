"""Explicit writable Owner composition and restart-safe outbox supervision.

Private policy is an owner registration, not a catalog-derived grant. Imports do
not initialize State, start a Worker, dispatch Work, or deploy services.
"""
from __future__ import annotations

import asyncio
import copy
import json
from collections.abc import Mapping
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID

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
_SELECTED_PARAMETERS = {
    "knowledgeos.note_metadata_team": {"objective"},
    "knowledgeos.relation_ontology_team": {"objective"},
    "knowledgeos.cited_research_team": {"question"},
    "knowledgeos.cited_draft_team": {"scope", "objective"},
    "knowledgeos.project_review_team": {"scope", "objective"},
    "knowledgeos.knowledge_exchange_team": {"gateway_receipt_id", "objective"},
    "knowledgeos.vault_maintenance_team": {"objective"},
}


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
        self.runners, self.teams, self.factories = {}, {}, {}
        for method, registration in selected["methods"].items():
            require(isinstance(registration, dict) and set(registration) == {
                "team_binding", "runner_binding", "admission_scope"}, "Method registration is incomplete")
            factory = concrete_method(method)
            team = decode_team(registration["team_binding"])
            runner = runners.get(method)
            require(runner is not None and callable(runner) and runner.binding == registration["runner_binding"]
                    and team.native_binding_digest == factory[2](runner.binding),
                    "Method needs its exact current native Runner and executable digest")
            require(team.team_id not in self.teams or self.teams[team.team_id] == team,
                    "Methods disagree on their owner Team registration")
            self.teams[team.team_id], self.runners[method], self.factories[method] = team, runner, factory
        require(not selected["daily_enabled"] or "knowledgeos.daily_corpus_stewardship_team" in self.runners,
                "Daily scheduling requires its explicitly bound native method")
        require(not selected["freeform_research"] or "knowledgeos.cited_research_team" in self.runners,
                "Freeform research requires its explicitly bound native method")
        self.exops = TrustedKnowledgeExOps(self.application, authorize_actor=policy.actor_allowed,
            director_admitted=self.director_admitted, director_method_source_admitted=self.source_admitted,
            current_team_binding=self.current_team, teams=self.teams,
            worker_identity=selected["worker_identity"])
        self.executors = {method: self.factories[method][0](self.exops, runner)
                          for method, runner in self.runners.items()}

    def authorize_index_publication(self):
        """Recheck the selected local owner and existing apply pins at promotion."""
        require(self.policy.actor_allowed(self.actor, "publish_index"),
                "Private owner policy denies index publication", "ACTION_DENIED")
        self.application.guard("apply")

    def current_team(self, team_id):
        methods = [method for method, item in self.policy.initial["methods"].items()
                   if item["team_binding"]["team_id"] == team_id]
        if not methods or any(not self.method_current(method) for method in methods):
            return None
        return self.teams.get(team_id)

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
        if not isinstance(record, dict) or record.get("spec") != dict(spec) or record.get("actor_id") != self.actor.actor_id:
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

    def _retain(self, spec, run, source, request):
        method = spec["method_contract_ref"]["method_id"]
        require(self.method_current(method), "Director or native binding was revoked", "ADMISSION_REQUIRED")
        self._admission_source(method, source)
        key = "owner.director." + spec["admission_receipt_id"]
        now = self.application.clock()
        authority = {"policy_path": str(self.policy.path), "actor_id": self.actor.actor_id,
            "worker_identity": self.policy.initial["worker_identity"], "method_id": method,
            "registration": copy.deepcopy(self.policy.initial["methods"][method]),
            "gate_digest": authority_gate_digest()}
        with self.application.journal.writer() as session:
            require(self.method_current(method) and self.application.reference(
                self.application.resolve_reference(dict(source))) == source,
                "Director source or binding changed", "ADMISSION_REQUIRED")
            previous = session.value["intents"].get(key)
            if previous:
                require(previous["spec"] == spec and previous["run"] == run
                        and previous["request"] == request and previous["source"] == source
                        and previous.get("authority") == authority,
                        "Director identity belongs to different content", "IDEMPOTENCY_CONFLICT")
                return datetime.fromisoformat(previous["expires_at"])
            expiry = now + timedelta(hours=1)
            session.value["intents"][key] = {"state": "completed", "recorded_at": stamp(now),
                "actor_id": self.actor.actor_id, "spec": copy.deepcopy(spec), "run": copy.deepcopy(run),
                "source": copy.deepcopy(source), "request": copy.deepcopy(request), "expires_at": stamp(expiry),
                "authority": authority}
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
        kwargs = {}
        if template_id == "note_metadata":
            routes = [self.runners[method].binding for method in methods[template_id]]
            require(routes[0] == routes[1], "Metadata template requires the same explicit native adapter")
            kwargs["native_metadata_runner_binding"] = routes[0]
        else:
            kwargs["native_research_runner_binding"] = self.runners[methods[template_id][0]].binding
        admissions = build_template_admissions(self.exops, template_id, scope, request_id,
                                               issue_director_receipt=receipt, **kwargs)
        return [(spec, run, action, self._retain(spec, run, source, request), source)
                for spec, run, action, _expiry, source in admissions]

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
        team = self.teams["evidence-research"]
        spec, run = self.factories[METHOD_ID][1](self.application, team, source,
            work_id="work.owner." + identity, work_run_id="workrun.owner." + identity,
            admission_receipt_id="admission.owner." + identity,
            runner_binding=self.runners[METHOD_ID].binding, question=payload["question"])
        return (spec, run, "action.owner." + identity,
                self._retain(spec, run, source, payload))

    def _selected_parameters(self, method):
        """Only public, fixed factory inputs; never expose private registrations."""
        require(method in _SELECTED_PARAMETERS, "Method requires its separate admission authority", "UNAVAILABLE")
        scope = self.policy.initial["methods"][method]["admission_scope"]
        require(isinstance(scope, dict) and set(scope) == {"source_paths", "parameters"}
                and isinstance(scope["source_paths"], list) and bool(scope["source_paths"])
                and all(isinstance(path, str) and bool(path) for path in scope["source_paths"])
                and isinstance(scope["parameters"], dict)
                and set(scope["parameters"]) <= _SELECTED_PARAMETERS[method],
                "Method has no safe selected admission configuration", "UNAVAILABLE")
        parameters = copy.deepcopy(scope["parameters"])
        if method in {"knowledgeos.cited_draft_team", "knowledgeos.project_review_team"} and "scope" not in parameters:
            try:
                parameters["scope"] = json.loads(parameters.get("objective", ""))
            except (ValueError, TypeError) as error:
                raise AdmissionError("UNAVAILABLE", "Method requires fixed structured scope") from error
        for key, value in parameters.items():
            if key == "scope":
                keys = ({"target_path", "target_type", "title"} if method == "knowledgeos.cited_draft_team"
                        else {"period_start", "period_end", "query", "base", "view"})
                require(isinstance(value, dict) and set(value) == keys
                        and all(isinstance(item, str) and 0 < len(item) <= 512 for item in value.values()),
                        "Method has invalid fixed scope parameters", "UNAVAILABLE")
                if "target_path" in value:
                    target = value["target_path"]
                    require(not target.startswith("/") and "\\" not in target
                            and not any(part in {"", ".", ".."} for part in target.split("/")),
                            "Method target must be Vault-relative", "UNAVAILABLE")
            else:
                require(isinstance(value, str) and 0 < len(value.strip()) <= 512,
                        "Method has invalid fixed admission parameters", "UNAVAILABLE")
        if (method in {"knowledgeos.cited_draft_team", "knowledgeos.project_review_team"}
                and "objective" in parameters):
            require(parameters["objective"] == canonical(parameters["scope"]).decode(),
                    "Method objective differs from its fixed scope", "UNAVAILABLE")
        if method == "knowledgeos.knowledge_exchange_team":
            from .boundary_contract import exchange_receipt_id
            receipt = parameters.get("gateway_receipt_id")
            if "objective" in parameters:
                selected = exchange_receipt_id(parameters["objective"])
                require(receipt is None or receipt == selected, "Method Gateway receipt changed", "UNAVAILABLE")
                receipt = selected
            require(isinstance(receipt, str) and bool(receipt),
                    "Method requires its fixed Gateway receipt", "UNAVAILABLE")
        return copy.deepcopy(scope["parameters"])

    def selected_methods(self, actor):
        from .work_methods import WorkMethodCatalog
        self.exops._guard(actor, "read_work")
        methods = []
        for method, definition in WorkMethodCatalog.load(self.application.roots.control).methods.items():
            parameters, available, reason = {}, False, "No owner-registered executable binding"
            if method == _DAILY_METHOD:
                reason = "Daily requires scheduler admission and coverage authority"
            elif method in self.runners:
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
                            "available": available, "reason": reason, "parameters": parameters})
        return {"methods": methods, "observed_at": stamp(self.application.clock())}

    def admit_selected_request(self, actor, payload):
        """Exact HTTP selection; callers cannot choose factory parameters."""
        self.exops._guard(actor, "submit_work")
        require(isinstance(payload, dict) and set(payload) == {"method_id", "scope", "request_id"}
                and isinstance(payload["method_id"], str) and isinstance(payload["request_id"], str),
                "Selected Work request shape is invalid", "INVALID_PAYLOAD")
        try:
            valid_id = str(UUID(payload["request_id"])) == payload["request_id"]
        except ValueError:
            valid_id = False
        require(valid_id, "Selected Work requires a canonical UUID", "INVALID_PAYLOAD")
        method, scope = payload["method_id"], payload["scope"]
        require(method in self.runners and self.method_current(method), "Method is unavailable", "UNAVAILABLE")
        self._selected_parameters(method)
        require(isinstance(scope, dict) and set(scope) == {"path", "content_sha256"}
                and isinstance(scope["path"], str) and isinstance(scope["content_sha256"], str),
                "Selected Work requires one exact source", "INVALID_PAYLOAD")
        source = self.application.reference(scope["path"])
        require(source["source_revision"] == scope["content_sha256"], "Selected source changed", "RESOURCE_STALE")
        return self.admit_selected(method, source, request_id=payload["request_id"], request=payload)

    def admit_selected(self, method, source, *, request_id, request, **admission):
        self.exops._guard(self.actor, "submit_work")
        require(method in self.runners and self.method_current(method), "Method is unavailable", "UNAVAILABLE")
        if method == _DAILY_METHOD:
            self._validate_daily_request(request)
            require(source == request["source_reference"] and request_id == request["request_id"],
                    "Daily admission differs from its exact request", "SCOPE_DENIED")
        identity = digest(canonical({"method": method, "request_id": request_id}))[7:39]
        registration = self.policy.initial["methods"][method]
        team = self.teams[registration["team_binding"]["team_id"]]
        parameters = self._admission_source(method, source)
        require(not admission or admission == parameters,
                "Admission parameters differ from owner selection", "SCOPE_DENIED")
        admission = parameters
        previous = self.application.journal.read()["intents"].get("owner.director.admission.owner." + identity)
        if previous is not None:
            require(previous["request"] == request and previous["source"] == source,
                    "Director identity belongs to different content", "IDEMPOTENCY_CONFLICT")
            # Replay the original spec and initial run, not a newly interpreted
            # factory output after a completed effect or changed catalog.
            spec, run = copy.deepcopy(previous["spec"]), copy.deepcopy(previous["run"])
        else:
            spec, run = self.factories[method][1](self.application, team, source,
                work_id="work.owner." + identity, work_run_id="workrun.owner." + identity,
                admission_receipt_id="admission.owner." + identity,
                runner_binding=self.runners[method].binding, **admission)
        expiry = self._retain(spec, run, source, request)
        return self.exops.submit_work(spec, run, actor=self.actor, action_id="action.owner." + identity,
            expires_at=expiry, method_source_reference=source,
            selected_scope={"path": self.application.resolve_reference(dict(source)),
                            "content_sha256": source["source_revision"]})

    def _validate_daily_request(self, request):
        from .daily_stewardship import METHOD_ID, DailyStewardship
        from .work_methods import WorkMethodCatalog
        require(isinstance(request, dict) and set(request) == {
                    "request_id", "source_reference", "method_contract_ref", "empty_fields"}
                and isinstance(request["source_reference"], dict)
                and isinstance(request["method_contract_ref"], dict)
                and isinstance(request["empty_fields"], list),
                "Daily requires its exact scheduler request", "INVALID_PAYLOAD")
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
        return [self.factories[method][3](self.exops, executor)
                for method, executor in self.executors.items()]

    def combined_worker_components(self):
        """One official plugin owns all statically selected graph namespaces."""
        from importlib import import_module

        from temporalio.langgraph import LangGraphPlugin

        from .native_team_temporal import native_team_graph
        from .research_temporal import research_team_graph
        from .temporal_contract import (
            AUTHORING_DRAFT_GRAPH_NAME,
            AUTHORING_DRAFT_WORKFLOW_TYPE,
            AUTHORING_REVIEW_GRAPH_NAME,
            AUTHORING_REVIEW_WORKFLOW_TYPE,
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
        workflows, graphs = [], {}
        for method, executor in self.executors.items():
            module, name, graph, wire = selections[method]
            workflows.append(getattr(import_module("." + module, __package__), name))
            graphs[graph] = (research_team_graph(self.exops, executor)
                if method == "knowledgeos.cited_research_team"
                else native_team_graph(self.exops, executor, workflow_type=wire))
        return workflows, LangGraphPlugin(graphs=graphs)


class DispatchSupervisor:
    """One fenced send per persisted pending intent; unknown is observation only."""
    def __init__(self, runtime: WritableOwnerRuntime, clients: Mapping[str, Any], *, limit=50):
        require(type(limit) is int and 1 <= limit <= 50, "Invalid supervisor limit")
        self.runtime, self.clients, self.limit = runtime, dict(clients), limit
        self._cursor = 0

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
        return {"outcomes": outcomes}

    async def run(self, stop: asyncio.Event, *, interval=1.0):
        require(0.1 <= interval <= 3600, "Invalid explicit supervisor interval")
        while not stop.is_set():
            await self.tick()
            try:
                await asyncio.wait_for(stop.wait(), timeout=interval)
            except TimeoutError:
                pass

"""Versioned KnowledgeOS Team definitions and owner registration preflight.

Definitions are inert. A caller must supply a current, owner admitted binding;
neither this catalog nor a Hermes profile name grants execution authority.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..adapters.core import AdmissionError, canonical, digest, load_json
from ..adapters.paths import resolve_beneath
from .work import PROFILE_KEYS

_TEAMS = {
    "knowledge-curation": "knowledgeos.curation_manager",
    "evidence-research": "knowledgeos.research_manager",
    "relation-ontology": "knowledgeos.ontology_manager",
    "project-review": "knowledgeos.review_manager",
    "knowledge-exchange": "knowledgeos.exchange_manager",
    "vault-maintenance": "knowledgeos.maintenance_manager",
}
_ROLE_KEYS = {"role_id", "version", "kind", "purpose", "input_kinds",
              "output_kinds", "required_owner_ports", "effect_class"}
_TEAM_KEYS = {"team_id", "manager_role_id", "example_teammate_role_ids",
              "source_scope", "result_kind", "acceptance_controls"}
_EFFECT_CLASSES = {"plan_only", "read_only", "proposal_only", "independent_assessment"}
_SEMVER = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")
_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")


def _require(condition: bool, code: str, message: str) -> None:
    if not condition:
        raise AdmissionError(code, message)


def _unique_strings(value: Any, *, nonempty: bool = True) -> bool:
    return (isinstance(value, list) and (bool(value) or not nonempty)
            and all(isinstance(item, str) and bool(item) and len(item) <= 96 for item in value)
            and len(value) == len(set(value)))


@dataclass(frozen=True)
class SpecialistBinding:
    role_id: str
    executor_id: str
    profile: Mapping[str, str]


@dataclass(frozen=True)
class TeamBinding:
    """Exact owner registration supplied by a trusted composition, never by a request."""

    team_id: str
    manager_executor_id: str
    manager_profile: Mapping[str, str]
    eval_officer_executor_id: str
    eval_officer_profile: Mapping[str, str]
    specialists: tuple[SpecialistBinding, ...]
    catalog_digest: str
    native_binding_digest: str
    binding_reference: Mapping[str, Any]

    def content(self) -> dict[str, Any]:
        return {
            "team_id": self.team_id,
            "manager_executor_id": self.manager_executor_id,
            "manager_profile": dict(self.manager_profile),
            "eval_officer_executor_id": self.eval_officer_executor_id,
            "eval_officer_profile": dict(self.eval_officer_profile),
            "specialists": [
                {"role_id": item.role_id, "executor_id": item.executor_id,
                 "profile": dict(item.profile)} for item in self.specialists
            ],
            "catalog_digest": self.catalog_digest,
            "native_binding_digest": self.native_binding_digest,
        }


@dataclass(frozen=True)
class OfficerBinding:
    """One owner-registered task Profile outside every Team."""

    officer_executor_id: str
    officer_profile: Mapping[str, str]
    eval_officer_executor_id: str
    eval_officer_profile: Mapping[str, str]
    catalog_digest: str
    native_binding_digest: str
    binding_reference: Mapping[str, Any]

    def content(self) -> dict[str, Any]:
        return {
            "officer_executor_id": self.officer_executor_id,
            "officer_profile": dict(self.officer_profile),
            "eval_officer_executor_id": self.eval_officer_executor_id,
            "eval_officer_profile": dict(self.eval_officer_profile),
            "catalog_digest": self.catalog_digest,
            "native_binding_digest": self.native_binding_digest,
        }


class TeamCatalog:
    """Pure, strict contract for six definitions and prospective exact bindings."""

    def __init__(self, document: Mapping[str, Any]) -> None:
        _require(set(document) == {"schema_version", "owner_operation_id", "mode",
                                   "eval_officer_role_id", "roles", "teams"}
                 and document["schema_version"] == 2
                 and document["owner_operation_id"] == "knowledgeos"
                 and document["mode"] == "definitions_only",
                 "BINDING_INVALID", "Team catalog header is invalid")
        roles = document["roles"]
        teams = document["teams"]
        _require(isinstance(roles, list) and isinstance(teams, list),
                 "BINDING_INVALID", "Team catalog inventories must be lists")
        self.roles: dict[str, Mapping[str, Any]] = {}
        for role in roles:
            _require(isinstance(role, dict) and set(role) == _ROLE_KEYS
                     and isinstance(role["role_id"], str)
                     and re.fullmatch(r"[a-z][a-z0-9_-]*(?:\.[a-z0-9][a-z0-9_-]*)*", role["role_id"])
                     and isinstance(role["version"], str)
                     and _SEMVER.fullmatch(role["version"])
                     and role["kind"] in {"manager", "specialist", "officer", "evaluator"}
                     and isinstance(role["purpose"], str) and role["purpose"].strip()
                     and _unique_strings(role["input_kinds"])
                     and _unique_strings(role["output_kinds"])
                     and _unique_strings(role["required_owner_ports"])
                     and role["effect_class"] in _EFFECT_CLASSES
                     and role["role_id"] not in self.roles,
                     "BINDING_INVALID", "Team role definition is missing, duplicated or invalid")
            _require((role["kind"] == "manager" and role["effect_class"] == "plan_only")
                     or (role["kind"] == "evaluator" and role["effect_class"] == "independent_assessment")
                     or (role["kind"] in {"specialist", "officer"} and role["effect_class"] in {"read_only", "proposal_only"}),
                     "BINDING_INVALID", "Role effect class exceeds its responsibility")
            self.roles[role["role_id"]] = role
        self.eval_officer_role_id = document["eval_officer_role_id"]
        _require(self.eval_officer_role_id in self.roles
                 and self.roles[self.eval_officer_role_id]["kind"] == "evaluator",
                 "BINDING_INVALID", "Independent EvalOfficer definition is required")
        self.teams: dict[str, Mapping[str, Any]] = {}
        for team in teams:
            _require(isinstance(team, dict) and set(team) == _TEAM_KEYS
                     and team["team_id"] in _TEAMS
                     and team["team_id"] not in self.teams
                     and team["manager_role_id"] == _TEAMS[team["team_id"]]
                     and team["manager_role_id"] in self.roles
                     and self.roles[team["manager_role_id"]]["kind"] == "manager"
                     and _unique_strings(team["example_teammate_role_ids"], nonempty=False)
                     and _unique_strings(team["source_scope"])
                     and isinstance(team["result_kind"], str) and team["result_kind"]
                     and _unique_strings(team["acceptance_controls"]),
                     "BINDING_INVALID", "Team definition or dedicated Manager is invalid")
            for role_id in team["example_teammate_role_ids"]:
                _require(role_id in self.roles
                         and self.roles[role_id]["kind"] == "specialist",
                         "BINDING_INVALID", "Example teammate must name a specialist definition")
            self.teams[team["team_id"]] = team
        _require(set(self.teams) == set(_TEAMS)
                 and {name for name, role in self.roles.items() if role["kind"] == "manager"}
                 == set(_TEAMS.values()),
                 "BINDING_INVALID", "Catalog must define exactly six Teams and their roles")
        self.digest = digest(canonical(dict(document)))

    @classmethod
    def load(cls, control: Path) -> TeamCatalog:
        return cls(load_json(resolve_beneath(control, "ops/config/team-catalog.json")))

    def profile(self, role_id: str) -> dict[str, str]:
        role = self.roles[role_id]
        role_body = {key: role[key] for key in ("role_id", "version", "kind", "purpose",
                                               "input_kinds", "output_kinds", "effect_class")}
        capability_body = {"capability_id": role_id + ".capability",
                           "version": role["version"],
                           "required_owner_ports": role["required_owner_ports"],
                           "effect_class": role["effect_class"]}
        return {"role_id": role_id, "role_version": role["version"],
                "role_digest": digest(canonical(role_body)),
                "capability_id": capability_body["capability_id"],
                "capability_version": role["version"],
                "capability_digest": digest(canonical(capability_body))}

    def validate_binding(self, binding: TeamBinding) -> None:
        team = self.teams.get(binding.team_id)
        _require(team is not None and binding.catalog_digest == self.digest,
                 "PIN_MISMATCH", "Team binding uses a different definition revision")
        _require(isinstance(binding.native_binding_digest, str)
                 and _DIGEST.fullmatch(binding.native_binding_digest),
                 "BINDING_INVALID", "Native runner and Profile mapping must have an exact digest")
        _require(dict(binding.manager_profile) == self.profile(team["manager_role_id"])
                 and dict(binding.eval_officer_profile) == self.profile(self.eval_officer_role_id),
                 "PIN_MISMATCH", "Manager or EvalOfficer profile differs from Team definition")
        seen_roles: set[str] = set()
        executors = {binding.manager_executor_id, binding.eval_officer_executor_id}
        _require(all(isinstance(value, str) and value.startswith("knowledgeos.")
                     for value in executors) and len(executors) == 2
                 and bool(binding.specialists), "BINDING_INVALID",
                 "Team needs distinct owner Manager, EvalOfficer and specialist identities")
        for specialist in binding.specialists:
            _require(specialist.role_id in self.roles
                     and self.roles[specialist.role_id]["kind"] == "specialist"
                     and specialist.role_id not in seen_roles
                     and specialist.executor_id.startswith("knowledgeos.")
                     and specialist.executor_id not in executors
                     and dict(specialist.profile) == self.profile(specialist.role_id),
                     "BINDING_INVALID", "Specialist role, executor or exact profile differs")
            seen_roles.add(specialist.role_id)
            executors.add(specialist.executor_id)
        _require(binding.binding_reference.get("owner_operation_id") == "knowledgeos"
                 and binding.binding_reference.get("availability") == "available_by_owner_export"
                 and binding.binding_reference.get("content_digest") == digest(canonical(binding.content())),
                 "DIGEST_MISMATCH", "Owner binding reference does not pin exact registration content")

    def validate_spec(self, spec: Mapping[str, Any], binding: TeamBinding) -> None:
        self.validate_binding(binding)
        _require(spec["target_kind"] == "team"
                 and spec["team_id"] == binding.team_id
                 and spec["manager_executor_id"] == binding.manager_executor_id
                 and spec["eval_officer_executor_id"] == binding.eval_officer_executor_id,
                 "SCOPE_DENIED", "WorkSpec does not match the admitted Team identities")
        by_executor = {item.executor_id: item for item in binding.specialists}
        allowed = set(spec["allowed_executor_ids"])
        _require(bool(allowed) and allowed <= by_executor.keys(),
                 "SCOPE_DENIED", "WorkSpec names an unregistered Team specialist")
        pins = {tuple(by_executor[executor].profile[key] for key in PROFILE_KEYS)
                for executor in allowed}
        _require({tuple(profile[key] for key in PROFILE_KEYS)
                  for profile in spec["allowed_profiles"]} == pins,
                 "PIN_MISMATCH", "WorkSpec Profile envelope differs from selected executors")
        for assignment in spec["explicit_assignments"]:
            self.validate_assignment(assignment, binding, allowed=allowed)

    def validate_officer_binding(self, binding: OfficerBinding) -> None:
        role_id = binding.officer_profile.get("role_id")
        _require(binding.catalog_digest == self.digest
                 and role_id in self.roles
                 and self.roles[role_id]["kind"] == "officer"
                 and dict(binding.officer_profile) == self.profile(role_id)
                 and dict(binding.eval_officer_profile) == self.profile(self.eval_officer_role_id),
                 "PIN_MISMATCH", "Officer or EvalOfficer definition differs from catalog")
        _require(binding.officer_executor_id.startswith("knowledgeos.")
                 and binding.eval_officer_executor_id.startswith("knowledgeos.")
                 and binding.officer_executor_id != binding.eval_officer_executor_id
                 and isinstance(binding.native_binding_digest, str)
                 and _DIGEST.fullmatch(binding.native_binding_digest),
                 "BINDING_INVALID", "Officer requires distinct owner executors and exact native binding")
        _require(binding.binding_reference.get("owner_operation_id") == "knowledgeos"
                 and binding.binding_reference.get("availability") == "available_by_owner_export"
                 and binding.binding_reference.get("content_digest") == digest(canonical(binding.content())),
                 "DIGEST_MISMATCH", "Officer binding reference does not pin exact content")

    def validate_officer_spec(self, spec: Mapping[str, Any], binding: OfficerBinding) -> None:
        self.validate_officer_binding(binding)
        _require(spec["target_kind"] == "officer"
                 and spec["officer_executor_id"] == binding.officer_executor_id
                 and spec["eval_officer_executor_id"] == binding.eval_officer_executor_id
                 and spec["delegation_mode"] == "officer_direct"
                 and spec["allowed_executor_ids"] == [binding.officer_executor_id]
                 and spec["allowed_profiles"] == [binding.officer_profile],
                 "SCOPE_DENIED", "WorkSpec does not match the admitted standalone Officer")
        for assignment in spec["explicit_assignments"]:
            _require(assignment["executor_id"] == binding.officer_executor_id
                     and all(assignment[key] == binding.officer_profile[key]
                             for key in PROFILE_KEYS),
                     "PIN_MISMATCH", "Officer user pin differs from its exact Profile")

    def validate_assignment(self, assignment: Mapping[str, Any], binding: TeamBinding,
                            *, allowed: set[str] | None = None) -> None:
        """Also call at Graph admission; WorkSpec lists alone do not bind pairs."""
        self.validate_binding(binding)
        by_executor = {item.executor_id: item for item in binding.specialists}
        executor = assignment.get("executor_id")
        _require(executor in by_executor and (allowed is None or executor in allowed),
                 "SCOPE_DENIED", "Graph assignment names an unadmitted Team executor")
        _require(all(assignment.get(key) == by_executor[executor].profile[key]
                     for key in PROFILE_KEYS),
                 "PIN_MISMATCH", "Graph assignment Profile does not match its executor")

    def validate_assignments(self, assignments: Sequence[Mapping[str, Any]],
                             binding: TeamBinding, spec: Mapping[str, Any]) -> None:
        self.validate_spec(spec, binding)
        for assignment in assignments:
            self.validate_assignment(assignment, binding,
                                     allowed=set(spec["allowed_executor_ids"]))

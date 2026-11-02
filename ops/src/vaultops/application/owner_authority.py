"""SDK-free cross-process reauthorization for trusted writable Owner Work.

Only an owner-retained Director receipt selects the private policy file and exact
registration. Tool/model inputs cannot supply, replace or suppress this proof.
"""
from __future__ import annotations

import json
import os
import stat
from pathlib import Path

from ..adapters.core import AdmissionError, canonical, digest
from ..adapters.owner_journal import read_regular


def require(condition, message):
    if not condition:
        raise AdmissionError("ADMISSION_REQUIRED", message)


def read_owner_policy(path):
    path = Path(path)
    require(path.is_absolute() and not path.is_symlink(), "Owner policy needs its exact trusted regular file")
    metadata = path.stat(follow_symlinks=False)
    require(stat.S_ISREG(metadata.st_mode) and stat.S_IMODE(metadata.st_mode) == 0o600
            and metadata.st_uid == os.getuid() and metadata.st_size <= 262144,
            "Owner policy must be a bounded locally owned 0600 file")
    value = json.loads(read_regular(path))
    require(isinstance(value, dict) and set(value) == {
        "version", "actor_id", "actions", "director_enabled", "worker_identity",
        "methods", "templates", "freeform_research", "daily_enabled"}
        and value["version"] == 1 and isinstance(value["actor_id"], str)
        and 0 < len(value["actor_id"]) <= 128 and isinstance(value["actions"], list)
        and all(isinstance(item, str) for item in value["actions"])
        and isinstance(value["methods"], dict) and isinstance(value["templates"], list)
        and all(type(value[key]) is bool for key in (
            "director_enabled", "freeform_research", "daily_enabled"))
        and isinstance(value["worker_identity"], str) and bool(value["worker_identity"]),
        "Unsupported private Owner registration")
    return value


def authority_gate_digest():
    modules = ("owner_authority.py", "owner_runtime.py", "work_tools.py", "mcp_effects.py",
               "knowledge.py", "metadata_proposals.py")
    return digest(canonical({name: digest(read_regular(Path(__file__).with_name(name))) for name in modules}))


def registration_native_digest(method, runner):
    from .authoring_work import POLICIES as authoring
    from .authoring_work import authoring_native_digest
    from .boundary_work import (
        EXCHANGE_POLICY,
        MAINTENANCE_POLICY,
        exchange_native_digest,
        maintenance_native_digest,
    )
    from .metadata_work import POLICIES as metadata
    from .metadata_work import metadata_native_digest
    from .research_work import METHOD_ID, research_native_digest
    if method in metadata:
        return metadata_native_digest(runner, method)
    if method in authoring:
        return authoring_native_digest(runner, method)
    if method == METHOD_ID:
        return research_native_digest(runner)
    if method == EXCHANGE_POLICY.method_id:
        return exchange_native_digest(runner)
    if method == MAINTENANCE_POLICY.method_id:
        return maintenance_native_digest(runner)
    raise AdmissionError("ADMISSION_REQUIRED", "Work has no selected native authority implementation")


def validate_owner_authority(application, snapshot, record):
    """Check current authority on every scoped tool and at final Pending writer."""
    spec = record["spec"]
    receipt_id = spec.get("admission_receipt_id", "")
    director = snapshot["intents"].get("owner.director." + receipt_id)
    if director is None and not receipt_id.startswith("admission.owner."):
        return  # Other existing explicit compositions own their separate gates.
    require(isinstance(director, dict) and isinstance(director.get("authority"), dict),
            "Writable Work lacks retained cross-process Director authority")
    authority = director["authority"]
    require(set(authority) == {"policy_path", "actor_id", "worker_identity", "method_id", "registration", "gate_digest"},
            "Writable Work authority has an unsupported shape")
    method = spec.get("method_contract_ref", {}).get("method_id")
    require(method == authority["method_id"] and director["spec"] == spec
            and director["actor_id"] == record["actor_id"] == authority["actor_id"]
            and director["source"] == record.get("method_source_reference")
            and authority["gate_digest"] == authority_gate_digest(),
            "Director Work/source or installed authority implementation changed")
    policy = read_owner_policy(authority["policy_path"])
    registration = authority["registration"]
    require(policy["actor_id"] == authority["actor_id"] and policy["worker_identity"] == authority["worker_identity"]
            and policy["director_enabled"] and "submit_work" in policy["actions"]
            and policy["methods"].get(method) == registration,
            "Current Owner actor, Director, Team or Runner authority was revoked")
    request = director["request"]
    require(("template_id" not in request or request["template_id"] in policy["templates"])
            and ("question" not in request or policy["freeform_research"])
            and ("method_contract_ref" not in request or policy["daily_enabled"]),
            "Current Owner request policy was revoked")
    source = director["source"]
    path = application.resolve_reference(dict(source))
    scope = registration["admission_scope"]
    require(path in scope["source_paths"] or scope["source_paths"] == ["*"], "Owner source scope was revoked")
    application._mcp_source_policy(path)
    require(application.reference(path) == source, "Owner source changed")
    team = registration["team_binding"]
    from .team_catalog import TeamCatalog
    require(team["team_id"] == spec["team_id"]
            and team["binding_reference"] == record["run"]["binding_reference"]
            and team["catalog_digest"] == TeamCatalog.load(application.roots.control).digest
            and team["native_binding_digest"] == registration_native_digest(method, registration["runner_binding"]),
            "Owner Team, installed native methods or Runner registration changed")

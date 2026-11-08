"""Explicit synthetic owner registrations; never inspect real domain/profile roots."""
from __future__ import annotations

import json
from dataclasses import replace

from support.metadata_fixture import SOURCE, application
from vaultops.adapters.core import canonical, digest
from vaultops.application.metadata_work import METADATA_METHOD_ID, RELATION_METHOD_ID
from vaultops.application.owner_runtime import (
    OwnerRuntimePolicy,
    WritableOwnerRuntime,
    concrete_method,
)
from vaultops.application.team_catalog import SpecialistBinding, TeamBinding, TeamCatalog
from vaultops.application.work_methods import WorkMethodCatalog


def policy_document(app, route, methods=(METADATA_METHOD_ID, RELATION_METHOD_ID)):
    registrations = {}
    catalog = TeamCatalog.load(app.roots.control)
    definitions = WorkMethodCatalog.load(app.roots.control).methods
    for method in methods:
        definition = definitions[method]
        team_id = definition["team_id"]
        manager = catalog.teams[team_id]["manager_role_id"]
        roles = tuple(dict.fromkeys(stage["role_id"] for stage in definition["required_stages"]))
        candidate = TeamBinding(team_id, manager + ".owner_fixture", catalog.profile(manager),
            "knowledgeos.eval_officer.owner_fixture", catalog.profile("knowledgeos.eval_officer"),
            tuple(SpecialistBinding(role, role + ".owner_fixture", catalog.profile(role)) for role in roles),
            catalog.digest, concrete_method(method)[2](route), {})
        raw = canonical(candidate.content())
        team = replace(candidate, binding_reference={
            "owner_operation_id": "knowledgeos", "artifact_id": "artifact.binding." + method,
            "source_revision": digest(raw)[7:], "content_digest": digest(raw), "media_type": "application/json",
            "size_bytes": len(raw), "classification": "internal", "availability": "available_by_owner_export",
            "export_capability_id": "knowledgeos.team_binding.export"})
        registrations[method] = {
            "team_binding": {**team.content(), "binding_reference": dict(team.binding_reference)},
            "runner_binding": dict(route),
            "admission_scope": {"source_paths": [SOURCE], "parameters": {}},
        }
    return {"version": 1, "actor_id": "owner.officer_smoke",
        "actions": ["submit_work", "read_work", "search_query", "proposal_read", "proposal_list"],
        "director_enabled": True, "worker_identity": "knowledgeos.worker.owner_runtime",
        "methods": registrations, "templates": ["note_metadata"],
        "freeform_research": False, "daily_enabled": False}


def write_policy(path, document):
    path.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")
    path.chmod(0o600)


def compose(root, route, runner_factory):
    app, canary = application(root)
    # Fixture preparation is explicit; production composition must never do this.
    with app.journal.writer() as session:
        session.commit(app.clock())
    runner = runner_factory(route, app)
    document = policy_document(app, route)
    policy_path = root / "owner-policy.json"
    write_policy(policy_path, document)
    runtime = WritableOwnerRuntime(app.roots, policy=OwnerRuntimePolicy(policy_path),
        runners={method: runner for method in document["methods"]})
    return runtime, runner, canary, policy_path, document

"""Explicit synthetic owner registrations; never inspect real domain/profile roots."""
from __future__ import annotations

import json

from support.metadata_fixture import SOURCE, application, binding
from vaultops.application.metadata_work import METADATA_METHOD_ID, RELATION_METHOD_ID
from vaultops.application.owner_runtime import OwnerRuntimePolicy, WritableOwnerRuntime


def policy_document(app, route, methods=(METADATA_METHOD_ID, RELATION_METHOD_ID)):
    registrations = {}
    for method in methods:
        team = binding(app, route, method)
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

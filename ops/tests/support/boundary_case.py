"""Disposable owner bindings shared by behavioral tests and real native smoke."""
from __future__ import annotations

import copy
from dataclasses import replace
from datetime import timedelta
from uuid import uuid4

from support.boundary_fixture import accepted_exchange
from support.control_factory import make_separate_portable_fixture_roots
from vaultops.adapters.core import canonical, digest
from vaultops.application.boundary_work import (
    EXCHANGE_POLICY,
    MAINTENANCE_POLICY,
    ExchangeTeamExecutor,
    MaintenanceTeamExecutor,
    exchange_admission,
    maintenance_admission,
)
from vaultops.application.exops import KnowledgeExOps, OwnerActor
from vaultops.application.knowledge import KnowledgeApplication
from vaultops.application.native_team import native_digest
from vaultops.application.team_catalog import SpecialistBinding, TeamBinding, TeamCatalog
from vaultops.projection import generate_projection

BASE = "00_Inbox/Captures/2026/09/20260909-090000-mac-deadbeef.md"
SOURCE = "00_Inbox/Captures/2026/09/Native Boundary selected note.md"


def make_case(root, runner_binding, runner_factory, kind, *, unresolved=False):
    policy = EXCHANGE_POLICY if kind == "exchange" else MAINTENANCE_POLICY
    roots = make_separate_portable_fixture_roots(root,
        extra_inputs=("ops/config/team-catalog.json", "ops/config/work-methods.json"), review_queues=True)
    source_path = roots.vault / SOURCE
    text = (roots.vault / BASE).read_text().replace(
        "11111111-1111-4111-8111-111111111111", str(uuid4())).replace(
        "20260909-090000-mac-deadbeef", "Native Boundary selected note")
    source_path.write_text(text)
    report, code = generate_projection(roots)
    assert code == 0, report
    app = KnowledgeApplication.from_roots(roots)
    source = app.reference(SOURCE)
    canary = "synthetic." + uuid4().hex
    accepted = accepted_exchange(app, source, canary=canary) if kind == "exchange" else None
    diagnostic_canary = "uncertain.synthetic." + uuid4().hex
    if unresolved:
        with app.journal.writer() as writer:
            writer.value["intents"][diagnostic_canary] = {
                "state": "unknown", "source_path": SOURCE, "content_digest": digest(b"unresolved synthetic effect"),
                "generation": writer.generation, "fencing_token": "generation.g" + str(writer.generation)}
            writer.commit(app.clock())
    catalog = TeamCatalog.load(roots.control)
    manager_role = catalog.teams[policy.team_id]["manager_role_id"]
    candidate = TeamBinding(
        policy.team_id, manager_role + ".native_boundary", catalog.profile(manager_role),
        "knowledgeos.eval_officer.native_boundary", catalog.profile("knowledgeos.eval_officer"),
        tuple(SpecialistBinding(role, role + ".native_boundary", catalog.profile(role)) for role in policy.roles),
        catalog.digest, native_digest(policy, runner_binding), {})
    raw = canonical(candidate.content())
    binding = replace(candidate, binding_reference={
        "owner_operation_id": "knowledgeos", "artifact_id": "artifact." + kind + ".binding",
        "source_revision": digest(raw)[7:], "content_digest": digest(raw), "media_type": "application/json",
        "size_bytes": len(raw), "classification": "internal", "availability": "available_by_owner_export",
        "export_capability_id": "knowledgeos.binding_read"})
    suffix = uuid4().hex
    identities = {"work_id": "work." + kind + "." + suffix,
                  "work_run_id": "workrun." + kind + "." + suffix,
                  "admission_receipt_id": "admission." + kind + "." + suffix,
                  "runner_binding": runner_binding}
    admission = exchange_admission if kind == "exchange" else maintenance_admission
    spec, run = admission(app, binding, source,
                          **({"gateway_receipt_id": accepted[0]["receipt_id"]} if accepted else {}), **identities)
    exact = copy.deepcopy(spec)
    exops = KnowledgeExOps(app,
        authorize_actor=lambda actor, action: actor.actor_id == "owner.officer_smoke"
            and actor.ingress in {"cli", "web"} and action in {"submit_work", "read_work"},
        director_admitted=lambda selected: dict(selected) == exact and app.reference(SOURCE) == source,
        teams={binding.team_id: binding},
        current_team_binding=lambda team: binding if team == binding.team_id else None,
        worker_identity="knowledgeos.worker.native_boundary",
        director_method_source_admitted=lambda selected, reference: dict(selected) == exact and dict(reference) == source)
    runner = runner_factory(runner_binding, app)
    executor = (ExchangeTeamExecutor if kind == "exchange" else MaintenanceTeamExecutor)(exops, runner=runner)
    receipt = exops.submit_work(spec, run, actor=OwnerActor("owner.officer_smoke", "web"),
                               action_id="action." + suffix, expires_at=app.clock() + timedelta(hours=1),
                               method_source_reference=source)
    return {"exops": exops, "executor": executor, "spec": spec, "run": run, "source_path": source_path,
            "source": source, "runner": runner, "receipt": receipt, "binding": binding,
            "accepted": accepted, "canary": canary, "diagnostic_canary": diagnostic_canary, "policy": policy}

"""Shared real-journal fixture for bounded steering tests and native smoke."""
from __future__ import annotations

import copy
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from support.control_factory import make_separate_portable_fixture_roots
from vaultops.adapters.core import canonical, digest
from vaultops.application.curation_steering import (
    AMENDED_DESCRIPTION,
    AMENDED_OBJECTIVE,
    SafeCurationSteering,
    curation_steering_native_digest,
    steering_admission,
)
from vaultops.application.exops import KnowledgeExOps, OwnerActor
from vaultops.application.knowledge import KnowledgeApplication
from vaultops.application.team_catalog import SpecialistBinding, TeamBinding, TeamCatalog
from vaultops.application.work import work_spec_digest
from vaultops.projection import generate_projection

ACTOR = OwnerActor("owner.officer_smoke", "web")
SOURCE = "00_Inbox/Captures/2026/09/20260909-090000-mac-deadbeef.md"


def fixture(root):
    roots = make_separate_portable_fixture_roots(root, extra_inputs=("ops/config/team-catalog.json", "ops/config/work-methods.json"), review_queues=True)
    source_path = roots.vault / SOURCE
    canary = "steering-" + uuid4().hex
    source_path.write_text(source_path.read_text() + "\nZeta synthetic observation: " + canary + "\nAlpha synthetic measurement: 27 ms\n")
    projection, code = generate_projection(roots)
    assert code == 0, projection
    application = KnowledgeApplication.from_roots(roots)
    source = application.reference(SOURCE)
    catalog = TeamCatalog.load(roots.control)
    candidate = TeamBinding(
        team_id="knowledge-curation", manager_executor_id="knowledgeos.manager.steering_smoke",
        manager_profile=catalog.profile("knowledgeos.curation_manager"),
        eval_officer_executor_id="knowledgeos.evaluator.steering_smoke", eval_officer_profile=catalog.profile("knowledgeos.eval_officer"),
        specialists=tuple(SpecialistBinding(role, role + ".steering_smoke", catalog.profile(role))
                          for role in ("knowledgeos.triage", "knowledgeos.draft_writer")),
        catalog_digest=catalog.digest, native_binding_digest=curation_steering_native_digest(), binding_reference={})
    raw = canonical(candidate.content())
    binding = replace(candidate, binding_reference={
        "owner_operation_id": "knowledgeos", "artifact_id": "artifact.steering.binding", "source_revision": digest(raw)[7:],
        "content_digest": digest(raw), "size_bytes": len(raw), "media_type": "application/json", "classification": "internal",
        "availability": "available_by_owner_export", "export_capability_id": "knowledgeos.team_binding.export"})
    suffix = uuid4().hex
    spec, run = steering_admission(application, binding, source, work_id="work.steering." + suffix,
        work_run_id="workrun.steering." + suffix, admission_receipt_id="admission.steering." + suffix)
    successor = {**copy.deepcopy(spec), "work_spec_revision": 2, "criteria_revision": 2,
                 "admission_receipt_id": "admission.steering.amended." + suffix, "objective": AMENDED_OBJECTIVE,
                 "acceptance_criteria": [{"criterion_id": "criterion.proposal", "description": AMENDED_DESCRIPTION}]}
    successor["spec_digest"] = work_spec_digest(successor)
    authority = {"actor": True, "director": True, "amendment": False}
    def admitted(selected):
        return authority["director"] and (selected == spec or (authority["amendment"] and selected == successor))
    exops = KnowledgeExOps(application,
        authorize_actor=lambda actor, action: authority["actor"] and actor.actor_id == ACTOR.actor_id and actor.ingress in {"web", "cli"}
            and action in {"submit_work", "read_work", "control_work"},
        director_admitted=admitted, teams={binding.team_id: binding},
        current_team_binding=lambda selected: binding if selected == binding.team_id else None,
        director_method_source_admitted=lambda selected, reference: admitted(selected) and reference == source,
        worker_identity="knowledgeos.worker.steering_smoke")
    controller = SafeCurationSteering(exops, director_amendment_admitted=lambda old, new, waiting:
        authority["amendment"] and old == spec and new == successor and waiting["state"] == "paused"
        and not waiting.get("active_graph_run_id") and not waiting["unresolved_effect_ids"])
    exops.submit_work(spec, run, actor=ACTOR, action_id="action.steering." + suffix,
        expires_at=datetime.now(UTC) + timedelta(minutes=30), method_source_reference=source)
    return controller, spec, run, successor, authority, source_path, canary


def receive(controller, work_run_id, kind, content, *, command_id=None, **extra):
    reference = controller.register_directive(work_run_id, actor=ACTOR, content=content)
    return controller.request(work_run_id, actor=ACTOR, command_id=command_id or "command." + kind + "." + uuid4().hex,
        kind=kind, target=controller.view(work_run_id, actor=ACTOR)["target"],
        expires_at=controller.app.clock() + timedelta(minutes=10), directive_reference=reference, **extra)


def apply(controller, work_run_id, receipt, *, temporal_run_id="native-steering-unit"):
    payload = controller.command_dispatch(work_run_id, receipt["command_id"])
    if receipt["kind"] == "cutover":
        controller.partial(payload, temporal_run_id=temporal_run_id)
    return controller.apply(payload, temporal_run_id=temporal_run_id)


def waiting(root):
    values = fixture(root)
    controller, _spec, run, *_rest = values
    controller.start(controller.dispatch(run["work_run_id"]), temporal_run_id="native-steering-unit")
    controller.triage(run["work_run_id"], temporal_run_id="native-steering-unit")
    controller.open_boundary(run["work_run_id"], pending_nodes=["normalization_report"], temporal_run_id="native-steering-unit")
    return values

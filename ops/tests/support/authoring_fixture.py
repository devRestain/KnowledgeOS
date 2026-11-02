"""Synthetic exact-source authoring setup shared by boundary tests and native smoke."""
from __future__ import annotations

import copy
from dataclasses import replace
from uuid import uuid4

from support.control_factory import make_separate_portable_fixture_roots
from vaultops.adapters.core import canonical, digest
from vaultops.application.authoring_work import (
    DRAFT_METHOD,
    POLICIES,
    authoring_admission,
    authoring_native_digest,
)
from vaultops.application.exops import KnowledgeExOps
from vaultops.application.knowledge import KnowledgeApplication
from vaultops.application.team_catalog import SpecialistBinding, TeamBinding, TeamCatalog
from vaultops.base_dashboard import render_base_documents
from vaultops.blueprint import load_yaml_file
from vaultops.projection import generate_projection

CAPTURE = "00_Inbox/Captures/2026/09/20260909-090000-mac-deadbeef.md"
PROJECT = "20_Projects/guestbook-horror/guestbook-horror.md"


def prepare(root, runner_binding, method_id, *, canary=None):
    roots = make_separate_portable_fixture_roots(root,
        extra_inputs=("ops/config/team-catalog.json", "ops/config/work-methods.json"), review_queues=True)
    policy = POLICIES[method_id]
    canary = canary or ("authoring-" + uuid4().hex)
    if method_id == DRAFT_METHOD:
        source_path = "00_Inbox/Captures/2026/09/Native Cited Draft.md"
        raw = (roots.vault / CAPTURE).read_text().replace("11111111-1111-4111-8111-111111111111", str(uuid4())).replace(
            "20260909-090000-mac-deadbeef", "Native Cited Draft")
        scope = {"target_path": "40_Knowledge/Notes/Native Cited Draft.md", "target_type": "knowledge", "title": "Native Cited Draft"}
        raw += "\nSynthetic observation code: " + canary + "; measured sample 17 milligrams, calibration unknown.\n"
    else:
        source_path = PROJECT
        raw = (roots.vault / source_path).read_text()
        front, body = raw.split("\n---\n", 1)
        raw = front + "\n---\n" + "\nSynthetic observation code: " + canary + "; project review evidence.\n" + body
        raw += "\n- [ ] Review synthetic sample due:: 2026-09-15\n- [x] Record earlier observation due:: 2026-09-10\n"
        scope = {"period_start": "2026-09-01", "period_end": "2026-09-30", "query": "Synthetic observation code",
                 "base": "Projects.base", "view": "Now"}
        base = roots.vault / "99_System/Bases/Projects.base"
        base.parent.mkdir(parents=True, exist_ok=True)
        base.write_text(render_base_documents(load_yaml_file(roots.control / "blueprint/blueprint.yaml"))["99_System/Bases/Projects.base"])
    path = roots.vault / source_path
    path.write_text(raw)
    generated, code = generate_projection(roots)
    assert code == 0, generated
    app = KnowledgeApplication.from_roots(roots)
    source = app.reference(source_path)
    catalog = TeamCatalog.load(roots.control)
    manager = catalog.teams[policy.team_id]["manager_role_id"]
    candidate = TeamBinding(policy.team_id, manager + ".authoring", catalog.profile(manager),
        "knowledgeos.eval_officer.authoring", catalog.profile("knowledgeos.eval_officer"),
        tuple(SpecialistBinding(role, role + ".authoring", catalog.profile(role)) for role in policy.roles),
        catalog.digest, authoring_native_digest(runner_binding, method_id), {})
    binding_raw = canonical(candidate.content())
    binding = replace(candidate, binding_reference={
        "owner_operation_id": "knowledgeos", "artifact_id": "artifact.authoring.binding",
        "source_revision": digest(binding_raw)[7:], "content_digest": digest(binding_raw), "size_bytes": len(binding_raw),
        "media_type": "application/json", "classification": "internal", "availability": "available_by_owner_export",
        "export_capability_id": "knowledgeos.binding_read"})
    suffix = uuid4().hex
    spec, run = authoring_admission(app, binding, source, method_id=method_id, scope=scope,
        work_id="work.authoring." + suffix, work_run_id="workrun.authoring." + suffix,
        admission_receipt_id="admission.authoring." + suffix, runner_binding=runner_binding)
    admitted = copy.deepcopy(spec)
    exops = KnowledgeExOps(app,
        authorize_actor=lambda actor, action: actor.actor_id == "owner.authoring" and actor.ingress in {"cli", "web"},
        director_admitted=lambda selected: dict(selected) == admitted,
        current_team_binding=lambda team: binding if team == binding.team_id else None,
        teams={binding.team_id: binding}, worker_identity="knowledgeos.worker.authoring",
        director_method_source_admitted=lambda selected, ref: dict(selected) == admitted and dict(ref) == source)
    return exops, spec, run, binding, source, path, canary

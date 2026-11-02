"""Fresh exact-source fixtures for metadata native admission and pure validation."""
from __future__ import annotations

from dataclasses import replace
from uuid import uuid4

from support.control_factory import make_separate_portable_fixture_roots
from vaultops.adapters.core import canonical, digest
from vaultops.application.knowledge import KnowledgeApplication
from vaultops.application.metadata_work import POLICIES, metadata_native_digest
from vaultops.application.team_catalog import SpecialistBinding, TeamBinding, TeamCatalog
from vaultops.note_engine import parse_frontmatter, render_frontmatter
from vaultops.projection import generate_projection

SOURCE = "40_Knowledge/Notes/정보의 빈칸은 공포의 상상을 강화한다.md"
TARGET = "플레이어가 텍스트 공포 게임에 다시 돌아올 이유는 무엇인가"


def application(root, *, no_evidence=False, daily=False):
    roots = make_separate_portable_fixture_roots(root,
        extra_inputs=("ops/config/team-catalog.json", "ops/config/work-methods.json"), review_queues=True)
    path = roots.vault / SOURCE
    document = parse_frontmatter(path.read_text())
    properties = dict(document.properties)
    properties["tags"] = [] if daily else ["existing-preserved"]
    properties["related"] = []
    canary = "native-metadata-" + uuid4().hex
    body = ("# Bounded synthetic stewardship evidence\n\nNo enrichment values or target declarations are available.\n"
            if no_evidence else "# Bounded synthetic stewardship evidence\n\n"
            + "Synthetic observation code: " + canary + "\n\n"
            + "tags: " + canary + "\n\n"
            + ("" if daily else "related: [[" + TARGET + "]]\n"))
    path.write_text(render_frontmatter(properties, body))
    report, code = generate_projection(roots)
    assert code == 0, report
    return KnowledgeApplication.from_roots(roots), canary


def binding(app, route, method_id):
    policy = POLICIES[method_id]
    catalog = TeamCatalog.load(app.roots.control)
    manager = catalog.teams[policy.team_id]["manager_role_id"]
    candidate = TeamBinding(team_id=policy.team_id, manager_executor_id=manager + ".native_metadata",
        manager_profile=catalog.profile(manager), eval_officer_executor_id="knowledgeos.eval_officer.native_metadata",
        eval_officer_profile=catalog.profile("knowledgeos.eval_officer"),
        specialists=tuple(SpecialistBinding(role, role + ".native_metadata", catalog.profile(role)) for role in policy.roles),
        catalog_digest=catalog.digest, native_binding_digest=metadata_native_digest(route, method_id), binding_reference={})
    raw = canonical(candidate.content())
    return replace(candidate, binding_reference={"owner_operation_id": "knowledgeos", "artifact_id": "artifact.binding." + policy.namespace,
        "source_revision": digest(raw)[7:], "content_digest": digest(raw), "media_type": "application/json", "size_bytes": len(raw),
        "classification": "internal", "availability": "available_by_owner_export", "export_capability_id": "knowledgeos.team_binding.export"})

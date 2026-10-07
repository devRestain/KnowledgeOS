"""Owner policy and GUI review boundaries added by the single MCP v3 interface."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import pytest
from support.control_factory import make_separate_portable_fixture_roots
from test_exops_temporal import make_exops

from vaultops.adapters.core import AdmissionError
from vaultops.application.exops import OwnerActor
from vaultops.application.knowledge import KnowledgeApplication, LocalIdentity
from vaultops.application.mcp_effects import submit_pending
from vaultops.application.read_tools import KnowledgeReadTools
from vaultops.base_dashboard import evaluate_base_view, render_base_documents
from vaultops.interfaces.exops_web import ExOpsWebBackend
from vaultops.projection import generate_projection
from vaultops.retrieval import _chunks_for_note
from vaultops.yaml_safe import load_yaml_file

SOURCE = "40_Knowledge/Notes/정보의 빈칸은 공포의 상상을 강화한다.md"
CAPTURE = "00_Inbox/Captures/2026/09/20260909-090000-mac-deadbeef.md"


def _fixture(tmp_path):
    roots = make_separate_portable_fixture_roots(tmp_path, review_queues=True)
    blueprint = load_yaml_file(roots.control / "blueprint/blueprint.yaml")
    for relative, content in render_base_documents(blueprint).items():
        path = roots.vault / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    report, code = generate_projection(roots)
    assert code == 0, report
    app = KnowledgeApplication.from_roots(
        roots, identity=LocalIdentity("local.mcp", "mcp", local_execution=True))
    return app


def test_read_tools_recheck_exact_refs_and_match_canonical_base(tmp_path):
    app = _fixture(tmp_path)
    reader = KnowledgeReadTools(app)
    projection, allowed = reader._eligible()
    note = allowed[SOURCE]
    chunk = _chunks_for_note(note)[-1]
    source = app.reference(SOURCE)
    read = reader.knowledge_read(source, chunk.locator, max_chars=200)
    assert read["chunk_hash"] == chunk.chunk_hash and read["text"] == chunk.text[:200]
    with pytest.raises(AdmissionError):
        reader.knowledge_read({**source, "owner_operation_id": "foreign"}, chunk.locator)
    with pytest.raises(AdmissionError):
        reader.knowledge_read({**source, "content_digest": "sha256:" + "0" * 64}, chunk.locator)
    expected = evaluate_base_view(app.roots, "Projects.base", "Now")
    actual = reader.base_view_query("Projects.base", "Now")
    assert actual["count"] == len([row for row in expected if row["path"] in allowed])
    assert all(item["resource_reference"]["owner_operation_id"] == "knowledgeos"
               for item in actual["rows"])
    blueprint = load_yaml_file(app.roots.control / "blueprint/blueprint.yaml")
    assert reader.semantic_registry_read(category="relations")["definitions"] == blueprint["relation_registry"]
    links = reader.link_context(source)
    assert all(item["target_reference"]["owner_operation_id"] == "knowledgeos"
               for item in links["outgoing"])
    tasks = reader.task_query(limit=20)
    assert all(item["line_sha256"] and item["locator"].startswith("/body/line/")
               for item in tasks["tasks"])
    assert projection.manifest["generation_id"]


def test_exact_capture_read_and_local_only_work_scope(tmp_path):
    app = _fixture(tmp_path)
    capture = app.reference(CAPTURE)
    reader = KnowledgeReadTools(app)
    locator = _chunks_for_note(next(note for note in reader._eligible(selected_path=CAPTURE)[0].notes
                                     if note["path"] == CAPTURE))[-1].locator
    assert reader.knowledge_read(capture, locator)["text"]
    scoped = KnowledgeReadTools(app, allowed_path=CAPTURE)
    assert scoped.knowledge_read(capture, locator)["resource_reference"] == capture
    with pytest.raises(AdmissionError):
        scoped.knowledge_read(app.reference(SOURCE), "/body")

    capture_file = app.roots.vault / CAPTURE
    capture_file.write_text(capture_file.read_text(encoding="utf-8").replace(
        "ai_policy: ask", "ai_policy: local_only"), encoding="utf-8")
    report, code = generate_projection(app.roots)
    assert code == 0, report
    generic = KnowledgeApplication.from_roots(
        app.roots, identity=LocalIdentity("local.mcp", "mcp", local_execution=False))
    with pytest.raises(AdmissionError):
        KnowledgeReadTools(generic).knowledge_read(generic.reference(CAPTURE), locator)
    trusted = KnowledgeApplication.from_roots(
        app.roots, identity=LocalIdentity("local.mcp", "mcp", local_execution=True))
    assert KnowledgeReadTools(trusted).knowledge_read(trusted.reference(CAPTURE), locator)["text"]


def test_agent_authored_draft_is_pending_with_source_and_no_canonical_change(tmp_path):
    app = _fixture(tmp_path)
    target = "40_Knowledge/Ideas/MCP Authored Idea.md"
    assert not (app.roots.vault / target).exists()
    result, code = app.create_normalize_proposal(
        source_reference=app.reference(CAPTURE), action="draft_note",
        target_path=target, target_type="idea", title="MCP Authored Idea",
        draft_body="이것은 에이전트가 작성한 검토용 초안입니다.")
    assert code == 0, result
    pending = (app.roots.vault / result["proposal_path"]).read_text(encoding="utf-8")
    assert "에이전트가 작성한 검토용 초안" in pending
    assert CAPTURE in pending and "source_sha256" in pending
    assert not (app.roots.vault / target).exists()


def test_plan_proposal_replays_and_gui_decision_has_no_apply(tmp_path):
    exops, _fixture_work = make_exops(tmp_path)
    exops.authorize_actor = lambda actor, action: actor.actor_id == "owner.user" and action.startswith("proposal_")
    app = KnowledgeApplication.from_roots(
        exops.application.roots, identity=LocalIdentity("local.mcp", "mcp", local_execution=True))
    source = app.reference(SOURCE)
    arguments = {"source_reference": source, "idempotency_key": "review-one",
                 "content": json.dumps({"observations": ["source checked"], "interpretation": "possible gap",
                                        "next_actions": ["inspect"], "citations": []})}
    created, code = submit_pending(app, "proposal_project_review", arguments)
    assert code == 0 and created["data"]["apply_allowed"] is False
    replayed, code = submit_pending(app, "proposal_project_review", arguments)
    assert code == 0 and replayed["data"]["replayed"] is True
    with pytest.raises(AdmissionError, match="Effect identity belongs"):
        submit_pending(app, "proposal_project_review", {
            **arguments,
            "content": json.dumps({"observations": ["changed"], "interpretation": "other",
                                   "next_actions": ["inspect"], "citations": []}),
        })
    assert len(list((app.roots.vault / "01_AI_Review/Pending").glob("MCP*.md"))) == 1

    @dataclass
    class Ingress:
        owner_operation_id: str = "knowledgeos"
        actor_login: str = "owner.user"
        method: str = "POST"
        resource_path: str = "proposal/read"
        body: bytes = b"{}"
        csrf_token: str = "fixture-token"

    backend = ExOpsWebBackend(exops, lambda login: OwnerActor(login, "web"),
                              lambda _actor, token: token == "fixture-token")
    reference = created["data"]["artifact_reference"]
    read = backend(Ingress(body=json.dumps({"proposal_reference": reference}).encode()))
    assert read["apply_allowed"] is False and "source checked" in read["text"]
    decision = backend(Ingress(resource_path="proposal/decide", body=json.dumps({
        "proposal_reference": reference, "decision": "approve", "reason": "fixture review"}).encode()))
    assert decision["decision"]["apply_allowed"] is False
    with pytest.raises(AdmissionError):
        backend(Ingress(resource_path="proposal/apply", body=json.dumps({
            "proposal_reference": reference}).encode()))


def test_gui_work_intake_requires_owner_director_admission(tmp_path):
    exops, fixture = make_exops(tmp_path)
    source = exops.application.reference(SOURCE)

    @dataclass
    class Ingress:
        owner_operation_id: str = "knowledgeos"
        actor_login: str = "owner.user"
        method: str = "POST"
        resource_path: str = "work/intake"
        body: bytes = b"{}"
        csrf_token: str = "bound-token"

    ingress = Ingress(body=json.dumps({"question": "Check the source", "scope": {
        "path": SOURCE, "content_sha256": source["source_revision"]}}).encode())
    authenticate = lambda login: OwnerActor(login, "web") if login == "owner.user" else None
    csrf = lambda _actor, token: token == "bound-token"
    with pytest.raises(AdmissionError, match="Director intake"):
        ExOpsWebBackend(exops, authenticate, csrf)(ingress)

    def admit(actor, payload):
        assert actor.actor_id == "owner.user"
        assert payload["scope"] == {"path": SOURCE, "content_sha256": source["source_revision"]}
        return fixture.spec, fixture.run, "action.gui.intake", datetime.now(UTC) + timedelta(hours=1)

    backend = ExOpsWebBackend(exops, authenticate, csrf, admit_request=admit)
    with pytest.raises(AdmissionError, match="changed before Director admission"):
        backend(Ingress(body=json.dumps({"question": "Check the source", "scope": {
            "path": SOURCE, "content_sha256": "0" * 64}}).encode()))
    accepted = backend(ingress)
    assert accepted["receipt_state"] == "accepted"
    read = backend(Ingress(resource_path="work/read", body=json.dumps({
        "work_run_id": fixture.run["work_run_id"]}).encode()))
    assert read["run"]["work_run_id"] == fixture.run["work_run_id"]

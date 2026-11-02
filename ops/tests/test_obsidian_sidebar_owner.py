"""Synthetic owner boundaries used by the Obsidian sidebar source."""

from __future__ import annotations

import copy
import http.client
import json
import threading
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta

import pytest
from support.work_factory import WorkFixture
from test_exops_temporal import make_exops

from vaultops.adapters.core import AdmissionError, canonical, digest
from vaultops.application.exops import OwnerActor
from vaultops.application.intake_templates import build_template_admissions
from vaultops.application.knowledge import KnowledgeApplication
from vaultops.application.metadata_proposals import auto_prepare_metadata_proposal
from vaultops.application.read_tools import KnowledgeReadTools
from vaultops.application.team_catalog import SpecialistBinding
from vaultops.application.work import record_digest, work_spec_digest
from vaultops.application.work_methods import WorkMethodCatalog
from vaultops.interfaces.exops_web import ExOpsWebBackend
from vaultops.interfaces.loopback_owner import create_loopback_owner
from vaultops.interfaces.owner_relay import create_owner_relay
from vaultops.note_engine import NoteEngine, parse_frontmatter
from vaultops.projection import generate_projection, load_current_projection
from vaultops.retrieval import _chunks_for_note
from vaultops.template_engine import render_note_template

SOURCE = "40_Knowledge/Notes/정보의 빈칸은 공포의 상상을 강화한다.md"
REQUEST = "12345678-1234-4123-8123-123456789abc"


@dataclass
class Ingress:
    owner_operation_id: str = "knowledgeos"
    method: str = "POST"
    resource_path: str = "search/query"
    body: bytes = b"{}"


def _backend(exops, *, admit_template=None):
    return ExOpsWebBackend(
        exops, OwnerActor("owner.user", "web"), admit_template=admit_template)


def _call(backend, route, payload):
    return backend(Ingress(resource_path=route, body=json.dumps(payload).encode()))


def _group(tmp_path, *, template_id="note_metadata"):
    exops, _ = make_exops(tmp_path)
    catalog = exops.catalog
    methods = WorkMethodCatalog.load(exops.application.roots.control)
    source = exops.application.reference(SOURCE)
    scope = {"path": SOURCE, "content_sha256": source["source_revision"]}
    exops.authorize_actor = lambda actor, action: actor.actor_id == "owner.user" and action in {
        "submit_work", "read_work", "search_query"}
    exops.director_method_source_admitted = lambda _spec, selected: dict(selected) == source
    binding_source = exops.teams["knowledge-curation"]
    admissions = []
    bindings = {}
    metadata_teams = (
            ("corpus-stewardship", "knowledgeos.note_metadata_team",
             "knowledgeos.stewardship_manager",
             ("knowledgeos.coverage_reader", "knowledgeos.enrichment_proposer")),
            ("relation-ontology", "knowledgeos.relation_ontology_team",
             "knowledgeos.ontology_manager",
             ("knowledgeos.relation_analyst", "knowledgeos.ontology_curator")))
    research_teams = (("evidence-research", "knowledgeos.cited_research_team",
                       "knowledgeos.research_manager",
                       ("knowledgeos.retriever", "knowledgeos.synthesizer")),)
    selected_teams = metadata_teams if template_id == "note_metadata" else research_teams
    prefix = "metadata" if template_id == "note_metadata" else "research"
    for number, (team_id, method_id, manager_role, roles) in enumerate(selected_teams, 1):
        specialists = tuple(SpecialistBinding(role, role + ".one", catalog.profile(role))
                            for role in roles)
        candidate = replace(binding_source, team_id=team_id,
                            manager_executor_id=manager_role + ".one",
                            manager_profile=catalog.profile(manager_role),
                            specialists=specialists)
        binding = replace(candidate, binding_reference={
            **binding_source.binding_reference, "content_digest": digest(canonical(candidate.content()))})
        bindings[team_id] = binding
        fixture = WorkFixture()
        spec = copy.deepcopy(fixture.spec)
        spec.update(work_id=f"work.{prefix}.{number}", team_id=team_id,
                    manager_executor_id=binding.manager_executor_id,
                    eval_officer_executor_id=binding.eval_officer_executor_id,
                    allowed_executor_ids=[item.executor_id for item in specialists],
                    allowed_profiles=[dict(item.profile) for item in specialists],
                    method_contract_ref=methods.reference(method_id))
        spec["spec_digest"] = work_spec_digest(spec)
        run = copy.deepcopy(fixture.run)
        run.update(work_id=spec["work_id"], work_run_id=f"workrun.{prefix}.{number}",
                   work_spec_digest=spec["spec_digest"],
                   binding_reference=dict(binding.binding_reference))
        admissions.append((spec, run, f"action.{prefix}.{number}",
                           datetime.now(UTC) + timedelta(hours=1), source))
    exops.teams = bindings
    exops.current_team_binding = lambda team_id: bindings.get(team_id)
    return exops, source, scope, admissions


def test_structured_group_is_atomic_replayed_and_does_not_redispatch(tmp_path):
    exops, _source, scope, admissions = _group(tmp_path)
    actor = OwnerActor("owner.user", "web")
    broken = copy.deepcopy(admissions)
    wrong = list(broken[1])
    wrong[4] = {**wrong[4], "content_digest": "sha256:" + "0" * 64}
    broken[1] = tuple(wrong)
    with pytest.raises(AdmissionError):
        exops.submit_template_group("note_metadata", scope, REQUEST, broken, actor=actor)
    assert not any(key.startswith(("exops.template.", "exops.work."))
                   for key in exops.application.journal.read()["intents"])
    accepted = exops.submit_template_group("note_metadata", scope, REQUEST, admissions, actor=actor)
    assert len(accepted["receipts"]) == 2
    assert all(item["dispatch_state"] == "pending" for item in accepted["receipts"])
    exops.record_dispatch("workrun.metadata.1", action_id="action.metadata.1", outcome="unknown")
    replay = exops.read_template_request(REQUEST, actor=actor,
                                         template_id="note_metadata", scope=scope)
    assert replay["replayed"] is True and replay["reconciliation_required"] is True
    assert [item["work_run_id"] for item in replay["receipts"]] == [
        item["work_run_id"] for item in accepted["receipts"]]
    assert [item["dispatch_state"] for item in replay["receipts"]] == ["unknown", "pending"]
    second_request = "87654321-1234-4123-8123-123456789abc"
    director_calls = []

    def admit(_actor, template_id, selected_scope, request_id):
        director_calls.append(request_id)
        return build_template_admissions(
            exops, template_id, selected_scope, request_id,
            issue_director_receipt=lambda *_args: "admission.synthetic.one")

    backend = _backend(exops, admit_template=admit)
    with pytest.raises(AdmissionError, match="requires owner reconciliation"):
        _call(backend, "work/intake-template", {
            "template_id": "note_metadata", "scope": scope, "request_id": second_request})
    assert director_calls == []
    with pytest.raises(AdmissionError, match="requires owner reconciliation"):
        exops.submit_template_group("note_metadata", scope, second_request,
                                    admit(None, "note_metadata", scope, second_request), actor=actor)
    with pytest.raises(AdmissionError, match="different input"):
        exops.read_template_request(REQUEST, actor=actor, template_id="note_metadata",
                                    scope={**scope, "path": "another.md"})


def test_template_group_rejects_existing_action_identity_atomically(tmp_path):
    exops, _source, scope, admissions = _group(tmp_path)
    actor = OwnerActor("owner.user", "web")
    exops.submit_template_group("note_metadata", scope, REQUEST, admissions, actor=actor)
    other_admissions = []
    for index, (spec, run, action_id, expiry, source) in enumerate(admissions):
        other_spec = {**spec, "work_id": spec["work_id"] + ".second"}
        other_spec["spec_digest"] = work_spec_digest(other_spec)
        other_run = {**run, "work_id": other_spec["work_id"],
                     "work_run_id": run["work_run_id"] + ".second",
                     "work_spec_digest": other_spec["spec_digest"]}
        other_action = action_id + ".second" if index == 0 else action_id
        other_admissions.append((other_spec, other_run, other_action, expiry, source))
    before = exops.application.journal.read()

    with pytest.raises(AdmissionError, match="Template Work or action identity"):
        exops.submit_template_group("note_metadata", scope,
                                    "87654321-1234-4123-8123-123456789abc",
                                    other_admissions, actor=actor)

    assert exops.application.journal.read() == before
    assert exops.submit_template_group(
        "note_metadata", scope, REQUEST, admissions, actor=actor)["replayed"] is True


def test_sidebar_owner_routes_keep_search_read_only_and_metadata_unpromoted(tmp_path):
    exops, _source, scope, admissions = _group(tmp_path)
    projection, code = generate_projection(exops.application.roots)
    assert code == 0, projection
    backend = _backend(exops, admit_template=lambda _actor, _template, _scope, _request: admissions)
    search = _call(backend, "search/query", {"query": "정보", "mode": "lexical"})
    assert search["mode"] == "lexical" and search["results"]
    assert all(item["path"].endswith(".md") and item["excerpt"]
               for item in search["results"])
    assert not any(key.startswith("exops.work.") for key in exops.application.journal.read()["intents"])
    with pytest.raises(AdmissionError):
        _call(backend, "search/query", {"query": "정보", "mode": "surprise"})
    ontology = _call(backend, "search/query", {"query": "knowledge", "mode": "ontology"})
    assert ontology["mode"] == "ontology"
    for mode in ("vector", "hybrid"):
        with pytest.raises(AdmissionError, match="embedding port"):
            _call(backend, "search/query", {"query": "정보", "mode": mode})
    accepted = _call(backend, "work/intake-template", {
        "template_id": "note_metadata", "scope": scope, "request_id": REQUEST})
    assert len(accepted["receipts"]) == 2
    listed = _call(backend, "work/list", {"path": SOURCE})
    assert {item["team_id"] for item in listed["works"]} == {
        "corpus-stewardship", "relation-ontology"}
    assert all(item["evaluation_state"] == "pending" for item in listed["works"])
    assert all(item["source_stale"] is False for item in listed["works"])
    with pytest.raises(AdmissionError, match="independent evaluation"):
        _call(backend, "work/prepare-metadata", {"request_id": REQUEST})
    assert not any(key.startswith("metadata.") for key in exops.application.journal.read()["intents"])
    note = exops.application.roots.vault / SOURCE
    note.write_bytes(note.read_bytes() + b"\n")
    stale = _call(backend, "work/list", {"path": SOURCE})
    assert all(item["source_stale"] is True for item in stale["works"])
    result = _call(backend, "work/result", {"work_run_id": listed["works"][0]["work_run_id"]})
    assert result["source_stale"] is True
    assert result["text"] is None


def test_search_refuses_changed_selected_source_between_match_and_excerpt(tmp_path, monkeypatch):
    exops, _source, _scope, _admissions = _group(tmp_path)
    projection, code = generate_projection(exops.application.roots)
    assert code == 0, projection
    backend = _backend(exops)
    original_eligible = KnowledgeReadTools._eligible
    changed = False

    def change_generation(reader, *, selected_path=None):
        nonlocal changed
        if not changed:
            changed = True
            note = exops.application.roots.vault / SOURCE
            note.write_text(note.read_text(encoding="utf-8") + "\nLater source material.\n",
                            encoding="utf-8")
            regenerated, outcome = generate_projection(exops.application.roots)
            assert outcome == 0, regenerated
        return original_eligible(reader, selected_path=selected_path)

    monkeypatch.setattr(KnowledgeReadTools, "_eligible", change_generation)
    with pytest.raises(AdmissionError) as error:
        _call(backend, "search/query", {"query": "정보", "mode": "lexical"})
    assert error.value.code == "RESOURCE_STALE"

def test_lost_template_response_receipt_is_read_only_and_source_change_does_not_hide_it(tmp_path):
    exops, _source, scope, admissions = _group(tmp_path)
    backend = _backend(exops, admit_template=lambda *_args: admissions)
    request = {"template_id": "note_metadata", "scope": scope, "request_id": REQUEST}
    accepted = _call(backend, "work/intake-template", request)
    path = exops.application.roots.vault / SOURCE
    path.write_bytes(path.read_bytes() + b"\nLater user edit.\n")
    before = exops.application.journal.read()
    observed = _call(backend, "mutation/receipt", {"route": "work/intake-template", "request": request})
    assert observed["found"] is True
    assert observed["receipt"]["receipts"] == accepted["receipts"]
    assert observed["observation_only"] is True
    assert exops.application.journal.read() == before
    wrong = _call(backend, "mutation/receipt", {
        "route": "work/intake-template", "request": {**request, "scope": {**scope, "path": "another.md"}}})
    assert wrong["found"] is False



def test_work_list_refuses_a_source_change_during_status_export(tmp_path, monkeypatch):
    exops, _source, scope, admissions = _group(tmp_path)
    backend = _backend(exops, admit_template=lambda _actor, _template, _scope, _request: admissions)
    _call(backend, "work/intake-template", {
        "template_id": "note_metadata", "scope": scope, "request_id": REQUEST})
    original_reference = KnowledgeApplication.reference
    changed = False

    def change_after_first_read(application, relative, *, proposal=False):
        nonlocal changed
        reference = original_reference(application, relative, proposal=proposal)
        if relative == SOURCE and not changed:
            changed = True
            note = exops.application.roots.vault / SOURCE
            note.write_bytes(note.read_bytes() + b"\n")
        return reference

    monkeypatch.setattr(KnowledgeApplication, "reference", change_after_first_read)
    with pytest.raises(AdmissionError, match="Work source changed during status export"):
        _call(backend, "work/list", {"path": SOURCE})


def test_trusted_template_builder_pins_method_criteria_and_budget(tmp_path):
    exops, _source, scope, _admissions = _group(tmp_path)
    built = build_template_admissions(
        exops, "note_metadata", scope, REQUEST,
        issue_director_receipt=lambda *_args: "admission.synthetic.one")
    assert len(built) == 2
    assert [item[0]["team_id"] for item in built] == ["corpus-stewardship", "relation-ontology"]
    assert all(item[0]["continuation_policy"]["max_segments"] == 2 for item in built)
    for spec, run, _action_id, _expiry, source in built:
        exops.contracts.spec(spec)
        exops.contracts.run(run, spec)
        exops._team_binding(spec, run)
        assert source["source_revision"] == scope["content_sha256"]


def test_loopback_listener_returns_direct_search_and_admits_exact_templates_without_tokens(tmp_path):
    exops, _source, scope, admissions = _group(tmp_path)
    generated, code = generate_projection(exops.application.roots)
    assert code == 0, generated
    server = create_loopback_owner(
        exops, actor_id="owner.user", port=0,
        admit_template=lambda _actor, _template, _scope, _request: admissions)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        def request(route, payload):
            client = http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=5)
            client.request("POST", "/owner/" + route,
                           body=json.dumps(payload),
                           headers={"Content-Type": "application/json"})
            response = client.getresponse()
            value = response.status, json.loads(response.read())
            client.close()
            return value

        query = {"query": "정보", "mode": "lexical"}
        status, result = request("search/query", query)
        assert status == 200 and result["results"]
        assert not any(key.startswith("exops.work.")
                       for key in exops.application.journal.read()["intents"])
        intake = {"template_id": "note_metadata", "scope": scope, "request_id": REQUEST}
        accepted_status, accepted = request("work/intake-template", intake)
        assert accepted_status == 200 and len(accepted["receipts"]) == 2
        replay_status, replay = request("work/intake-template", intake)
        assert replay_status == 200 and replay["replayed"] is True
        assert [item["work_run_id"] for item in replay["receipts"]] == [
            item["work_run_id"] for item in accepted["receipts"]]
        list_status, listed = request("work/list", {"path": SOURCE})
        assert list_status == 200 and len(listed["works"]) == 2
        assert all(item["source_stale"] is False for item in listed["works"])
        result_status, pending = request(
            "work/result", {"work_run_id": accepted["receipts"][0]["work_run_id"]})
        assert result_status == 200 and pending["reason"]
        note = exops.application.roots.vault / SOURCE
        note.write_bytes(note.read_bytes() + b"\n")
        stale_status, stale = request("work/list", {"path": SOURCE})
        assert stale_status == 200 and all(item["source_stale"] is True for item in stale["works"])
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_owner_routes_preserve_loopback_binding_and_domain_admission_through_relay(tmp_path):
    exops, _source, scope, admissions = _group(tmp_path)
    generated, code = generate_projection(exops.application.roots)
    assert code == 0, generated
    with pytest.raises(AdmissionError, match="bind host"):
        create_loopback_owner(exops, actor_id="owner.user", bind_host="0.0.0.0")
    owner = create_loopback_owner(
        exops, actor_id="owner.user", port=0,
        admit_template=lambda _actor, _template, _scope, _request: admissions)
    relay = create_owner_relay(upstream_host="127.0.0.1",
                               upstream_port=owner.server_port, listen_port=0)
    threads = [threading.Thread(target=server.serve_forever, daemon=True)
               for server in (owner, relay)]
    for thread in threads:
        thread.start()
    try:
        def request(route, payload):
            client = http.client.HTTPConnection("127.0.0.1", relay.server_port, timeout=5)
            client.request("POST", "/owner/" + route, body=json.dumps(payload), headers={
                "Content-Type": "application/json",
            })
            response = client.getresponse()
            value = response.status, json.loads(response.read())
            client.close()
            return value

        status, found = request("search/query", {"query": "정보", "mode": "lexical"})
        assert status == 200 and found["results"]
        accepted_status, accepted = request("work/intake-template", {
            "template_id": "note_metadata", "scope": scope, "request_id": REQUEST,
        })
        assert accepted_status == 200 and len(accepted["receipts"]) == 2
        listed_status, listed = request("work/list", {"path": SOURCE})
        assert listed_status == 200 and len(listed["works"]) == 2
    finally:
        for server in (relay, owner):
            server.shutdown()
            server.server_close()
        for thread in threads:
            thread.join(timeout=5)


def test_enhancement_result_links_each_improvement_to_current_cited_evidence(tmp_path):
    exops, _source, scope, _admissions = _group(tmp_path, template_id="document_enhancement")
    exops.authorize_actor = lambda actor, action: actor.actor_id == "owner.user"
    generated, code = generate_projection(exops.application.roots)
    assert code == 0, generated
    admissions = build_template_admissions(
        exops, "document_enhancement", scope, REQUEST,
        issue_director_receipt=lambda *_args: "admission.synthetic.one")
    backend = _backend(exops, admit_template=lambda _actor, _template, _scope, _request: admissions)
    receipt = _call(backend, "work/intake-template", {
        "template_id": "document_enhancement", "scope": scope, "request_id": REQUEST})
    work_run_id = receipt["receipts"][0]["work_run_id"]
    app = exops.application
    original_source = (app.roots.vault / SOURCE).read_bytes()
    projection = load_current_projection(app.roots, verify_sources=True)
    selected = next(note for note in projection.notes if note["path"] == SOURCE)
    chunk = next(item for item in _chunks_for_note(selected)
                 if item.locator != "/frontmatter" and item.text.strip())
    source = app.reference(SOURCE)
    research = {"source_reference": source,
                "index_generation_id": projection.manifest["generation_id"],
                "improvements": [{"point": "Explain the observed mechanism",
                                  "citation_indices": [0]}],
                "citations": [{"resource_reference": source, "chunk_locator": chunk.locator,
                               "chunk_hash": chunk.chunk_hash}],
                "uncertainty": ["The causal direction is unverified"],
                "missing_materials": ["A longitudinal source"]}
    note = parse_frontmatter((app.roots.vault / SOURCE).read_text(encoding="utf-8"))

    def publish_report(candidate):
        body = "```json\n" + json.dumps({
            "artifact_kind": "document_enhancement_report",
            "content": json.dumps(candidate, ensure_ascii=False)}, ensure_ascii=False) + "\n```\n"
        rendered = render_note_template("T01_AI_Proposal.md", {
            "title": "research-report", "id": "00000003-0000-4000-8000-000000000000",
            "proposal_id": "00000003-0000-4000-8000-000000000000",
            "created": str(note.properties["created"]),
            "modified": str(note.properties["created"]),
            "sensitivity": note.properties.get("sensitivity", "personal"),
            "source_hashes": [f"{SOURCE}|{source['content_digest']}"],
            "body": body}).markdown
        relative = "01_AI_Review/Pending/MCP artifact_submit " + "a" * 24 + ".md"
        (app.roots.vault / relative).write_text(rendered, encoding="utf-8")
        reference = app.reference(relative, proposal=True)
        formal = {"content_digest": reference["content_digest"]}
        assessment = {"completion_state": "complete", "criterion_outcomes": [
            {"criterion_id": "criterion.1", "outcome": "satisfied"}]}
        with app.journal.writer() as writer:
            record = writer.value["intents"][f"exops.work.{work_run_id}"]
            record["run"]["state"] = "completed"
            record["run"]["result_reference"] = formal
            record["run"]["latest_assessment_digest"] = record_digest(assessment)
            record["assessment"] = assessment
            record["application_state"] = "recorded"
            record["artifact_references"] = {"research.report": formal}
            record["submitted_artifacts"] = [reference]
            writer.commit(app.clock())

    publish_report(research)
    still_current = load_current_projection(app.roots, verify_sources=True)
    assert still_current.manifest["generation_id"] == research["index_generation_id"]
    assert _call(backend, "search/query", {"query": "정보", "mode": "lexical"})["results"]
    result = _call(backend, "work/result", {"work_run_id": work_run_id})
    assert result["report"]["improvements"][0]["citation_indices"] == [0]
    assert result["evidence"][0]["path"] == SOURCE
    assert result["evidence"][0]["excerpt"]
    assert result["proposal_reference"] is None
    assert (app.roots.vault / SOURCE).read_bytes() == original_source
    research["improvements"][0]["citation_indices"] = []
    publish_report(research)
    with pytest.raises(AdmissionError, match="Every research improvement needs a valid citation"):
        _call(backend, "work/result", {"work_run_id": work_run_id})
    assert (app.roots.vault / SOURCE).read_bytes() == original_source


def _evaluated_candidates(exops, scope, *, metadata_fields=None, omit_evidence_value=None):
    app = exops.application
    source = app.reference(scope["path"])
    note = parse_frontmatter((app.roots.vault / scope["path"]).read_text(encoding="utf-8"))
    quote = next(line for line in note.body.splitlines() if line.strip())
    registry_digest = digest(canonical(NoteEngine.from_root(app.roots).blueprint["relation_registry"]))
    candidates = [(metadata_fields if metadata_fields is not None
                   else {"tags": ["knowledgeos/test"]}, "note_metadata"),
                  ({}, "note_relations")]
    with app.journal.writer() as writer:
        for index, (fields, kind) in enumerate(candidates, 1):
            proposal_id = f"{index:08d}-0000-4000-8000-000000000000"
            relative = f"01_AI_Review/Pending/candidate-{index}.md"
            evidence = {field: {value: [quote] for value in values}
                        for field, values in fields.items()}
            if index == 1 and omit_evidence_value is not None:
                evidence["tags"].pop(omit_evidence_value)
            candidate = {"source_reference": source, "registry_digest": registry_digest,
                         "fields": fields, "evidence": evidence}
            body = "```json\n" + json.dumps({"artifact_kind": kind,
                                                "content": json.dumps(candidate, ensure_ascii=False)},
                                               ensure_ascii=False) + "\n```\n"
            rendered = render_note_template("T01_AI_Proposal.md", {
                "title": f"candidate-{index}", "id": proposal_id, "proposal_id": proposal_id,
                "created": str(note.properties["created"]), "modified": str(note.properties["created"]),
                "sensitivity": note.properties.get("sensitivity", "personal"),
                "source_hashes": [f"{scope['path']}|{source['content_digest']}"],
                "body": body}).markdown
            (app.roots.vault / relative).write_text(rendered, encoding="utf-8")
            reference = app.reference(relative, proposal=True)
            formal = {"content_digest": reference["content_digest"]}
            assessment = {"completion_state": "complete", "criterion_outcomes": [
                {"criterion_id": "criterion.1", "outcome": "satisfied"}]}
            record = writer.value["intents"][f"exops.work.workrun.metadata.{index}"]
            record["run"]["state"] = "completed"
            record["run"]["result_reference"] = formal
            record["run"]["latest_assessment_digest"] = record_digest(assessment)
            record["assessment"] = assessment
            record["application_state"] = "recorded"
            record["artifact_references"] = {f"artifact.{index}": formal}
            record["submitted_artifacts"] = [reference]
        writer.commit(app.clock())
    return source


def test_two_evaluated_candidates_make_reviewable_update_and_separate_apply(tmp_path):
    exops, _source, scope, admissions = _group(tmp_path)
    exops.authorize_actor = lambda actor, action: actor.actor_id == "owner.user"
    backend = _backend(exops, admit_template=lambda _actor, _template, _scope, _request: admissions)
    _call(backend, "work/intake-template", {"template_id": "note_metadata",
                                             "scope": scope, "request_id": REQUEST})
    before = (exops.application.roots.vault / SOURCE).read_bytes()
    _evaluated_candidates(exops, scope)
    listed = _call(backend, "work/list", {"path": SOURCE})
    assert all(item["evaluation_state"] == "passed" and item["run_state"] == "completed"
               for item in listed["works"])
    promoted = auto_prepare_metadata_proposal(exops, REQUEST, actor=OwnerActor("owner.user", "web"))
    assert promoted == {"state": "available", "code": None}
    listed = _call(backend, "work/list", {"path": SOURCE})
    assert all(item["metadata_promotion_state"] == "available" and item["proposal_reference"]
               for item in listed["works"])
    assert (exops.application.roots.vault / SOURCE).read_bytes() == before
    reference = listed["works"][0]["proposal_reference"]
    preview = _call(backend, "proposal/read", {"proposal_reference": reference})
    assert preview["apply_allowed"] and "knowledgeos/test" in preview["diff"]
    assert [(item["field"], item["value"]) for item in preview["field_evidence"]] == [
        ("tags", "knowledgeos/test")]
    assert len(preview["field_evidence"][0]["evidence"]) == 1
    assert preview["field_evidence"][0]["evidence"][0] in (
        exops.application.roots.vault / SOURCE).read_text(encoding="utf-8")
    replay = _call(backend, "work/prepare-metadata", {"request_id": REQUEST})
    assert replay["status"] == "NO_OP"
    _call(backend, "proposal/decide", {"proposal_reference": reference,
                                       "decision": "approve", "reason": "Fixture review"})
    assert (exops.application.roots.vault / SOURCE).read_bytes() == before
    applied = _call(backend, "proposal/apply", {"proposal_reference": reference})
    assert applied["canonical_target_changed"] is True
    assert b"knowledgeos/test" in (exops.application.roots.vault / SOURCE).read_bytes()


def test_metadata_requires_evidence_for_each_new_value(tmp_path):
    exops, _source, scope, admissions = _group(tmp_path)
    exops.authorize_actor = lambda actor, action: actor.actor_id == "owner.user"
    backend = _backend(exops, admit_template=lambda _actor, _template, _scope, _request: admissions)
    _call(backend, "work/intake-template", {"template_id": "note_metadata",
                                             "scope": scope, "request_id": REQUEST})
    before = (exops.application.roots.vault / SOURCE).read_bytes()
    _evaluated_candidates(exops, scope,
                          metadata_fields={"tags": ["knowledgeos/test", "knowledgeos/other"]},
                          omit_evidence_value="knowledgeos/other")
    with pytest.raises(AdmissionError, match="Every candidate value needs its own evidence"):
        _call(backend, "work/prepare-metadata", {"request_id": REQUEST})
    assert (exops.application.roots.vault / SOURCE).read_bytes() == before
    assert not list((exops.application.roots.vault / "01_AI_Review/Pending").glob("Metadata *.md"))


def test_proposal_read_rejects_artifact_changed_after_owner_receipt(tmp_path):
    exops, _source, scope, admissions = _group(tmp_path)
    exops.authorize_actor = lambda actor, action: actor.actor_id == "owner.user"
    backend = _backend(exops, admit_template=lambda _actor, _template, _scope, _request: admissions)
    _call(backend, "work/intake-template", {"template_id": "note_metadata",
                                             "scope": scope, "request_id": REQUEST})
    _evaluated_candidates(exops, scope)
    proposal = _call(backend, "work/prepare-metadata", {"request_id": REQUEST})
    reference = proposal["proposal_reference"]
    path = exops.application.resolve_reference(reference, proposal=True)
    artifact = exops.application.roots.vault / path
    artifact.write_text(artifact.read_text(encoding="utf-8") + "\nChanged after receipt.\n",
                        encoding="utf-8")
    changed_reference = exops.application.reference(path, proposal=True)
    with pytest.raises(AdmissionError, match="Pending artifact differs from its owner receipt"):
        _call(backend, "proposal/read", {"proposal_reference": changed_reference})


def test_metadata_does_not_prepare_when_one_team_evaluation_is_unmet(tmp_path):
    exops, _source, scope, admissions = _group(tmp_path)
    exops.authorize_actor = lambda actor, action: actor.actor_id == "owner.user"
    backend = _backend(exops, admit_template=lambda _actor, _template, _scope, _request: admissions)
    _call(backend, "work/intake-template", {"template_id": "note_metadata",
                                             "scope": scope, "request_id": REQUEST})
    _evaluated_candidates(exops, scope)
    with exops.application.journal.writer() as writer:
        record = writer.value["intents"]["exops.work.workrun.metadata.2"]
        assessment = {"completion_state": "incomplete", "criterion_outcomes": [
            {"criterion_id": "criterion.1", "outcome": "unmet"}]}
        record["assessment"] = assessment
        record["run"]["latest_assessment_digest"] = record_digest(assessment)
        writer.commit(exops.application.clock())
    before = (exops.application.roots.vault / SOURCE).read_bytes()
    blocked = auto_prepare_metadata_proposal(exops, REQUEST, actor=OwnerActor("owner.user", "web"))
    assert blocked == {"state": "needs_attention", "code": "ACCEPTANCE_REQUIRED"}
    listed = _call(backend, "work/list", {"path": SOURCE})
    assert all(item["metadata_promotion_state"] == "needs_attention"
               and item["metadata_promotion_code"] == "ACCEPTANCE_REQUIRED"
               for item in listed["works"])
    with pytest.raises(AdmissionError, match="passing independent evaluation"):
        _call(backend, "work/prepare-metadata", {"request_id": REQUEST})
    assert (exops.application.roots.vault / SOURCE).read_bytes() == before
    assert not list((exops.application.roots.vault / "01_AI_Review/Pending").glob("Metadata *.md"))


def test_evaluated_metadata_with_no_new_values_records_no_change(tmp_path):
    exops, _source, scope, admissions = _group(tmp_path)
    exops.authorize_actor = lambda actor, action: actor.actor_id == "owner.user"
    backend = _backend(exops, admit_template=lambda _actor, _template, _scope, _request: admissions)
    _call(backend, "work/intake-template", {"template_id": "note_metadata",
                                             "scope": scope, "request_id": REQUEST})
    before = (exops.application.roots.vault / SOURCE).read_bytes()
    _evaluated_candidates(exops, scope, metadata_fields={})
    assert auto_prepare_metadata_proposal(
        exops, REQUEST, actor=OwnerActor("owner.user", "web")) == {
            "state": "no_change", "code": None}
    listed = _call(backend, "work/list", {"path": SOURCE})
    assert all(item["metadata_promotion_state"] == "no_change"
               and item["proposal_reference"] is None for item in listed["works"])
    assert (exops.application.roots.vault / SOURCE).read_bytes() == before
    assert not list((exops.application.roots.vault / "01_AI_Review/Pending").glob("Metadata *.md"))


def test_metadata_rejects_populated_field_replacement_before_pending(tmp_path):
    exops, _source, scope, admissions = _group(tmp_path)
    exops.authorize_actor = lambda actor, action: actor.actor_id == "owner.user"
    backend = _backend(exops, admit_template=lambda _actor, _template, _scope, _request: admissions)
    _call(backend, "work/intake-template", {"template_id": "note_metadata",
                                             "scope": scope, "request_id": REQUEST})
    _evaluated_candidates(exops, scope, metadata_fields={"claim": ["Replacement claim"]})
    before = (exops.application.roots.vault / SOURCE).read_bytes()
    with pytest.raises(AdmissionError, match="populated property cannot be replaced"):
        _call(backend, "work/prepare-metadata", {"request_id": REQUEST})
    assert (exops.application.roots.vault / SOURCE).read_bytes() == before
    assert not list((exops.application.roots.vault / "01_AI_Review/Pending").glob("Metadata *.md"))


def test_policy_denial_precedes_template_director_admission(tmp_path):
    exops, _source, _scope, _admissions = _group(tmp_path)
    note = exops.application.roots.vault / SOURCE
    note.write_text(note.read_text(encoding="utf-8").replace("ai_policy: ask", "ai_policy: deny"),
                    encoding="utf-8")
    denied_scope = {"path": SOURCE,
                    "content_sha256": exops.application.reference(SOURCE)["source_revision"]}
    director_calls = []
    backend = _backend(exops, admit_template=lambda *_args: director_calls.append(True))
    with pytest.raises(AdmissionError):
        _call(backend, "work/intake-template", {"template_id": "note_metadata",
                                                 "scope": denied_scope, "request_id": REQUEST})
    assert director_calls == []
    assert not any(key.startswith("exops.template.")
                   for key in exops.application.journal.read()["intents"])

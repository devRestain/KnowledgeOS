"""Pure native metadata admission/evidence boundary tests; no fake native replay."""
from __future__ import annotations

import asyncio
import copy
import json
from datetime import timedelta

import pytest
from support.agent_fixture import model_binding, retain_director_context
from support.metadata_fixture import SOURCE, TARGET, MetadataToolRunner, application, binding

from vaultops.adapters.core import AdmissionError
from vaultops.application.exops import KnowledgeExOps, OwnerActor
from vaultops.application.metadata_work import (
    DAILY_METHOD_ID,
    DAILY_POLICY,
    METADATA_METHOD_ID,
    METADATA_POLICY,
    RELATION_METHOD_ID,
    RELATION_POLICY,
    MetadataTeamExecutor,
    metadata_admission,
    validate_candidate,
)
from vaultops.application.temporal_contract import native_team_route
from vaultops.application.work import record_digest
from vaultops.application.work_methods import WorkMethodCatalog
from vaultops.application.work_tools import TeamToolSession
from vaultops.note_engine import parse_frontmatter, render_frontmatter

ROUTE = model_binding()


def candidate(app, fields, quotes):
    return {"source_reference": app.reference(SOURCE), "registry_digest": app.daily_stewardship()._registry_digest(),
            "fields": fields, "evidence": quotes}


def test_metadata_adds_only_new_value_preserving_populated_tags(tmp_path):
    app, value = application(tmp_path)
    original = (app.roots.vault / SOURCE).read_bytes()
    payload = candidate(app, {"tags": [value]}, {"tags": {value: ["tags: " + value]}})
    assert validate_candidate(app, app.reference(SOURCE), payload, method_id=METADATA_METHOD_ID) == payload
    assert (app.roots.vault / SOURCE).read_bytes() == original
    assert parse_frontmatter(original.decode()).properties["tags"] == ["existing-preserved"]


def test_plain_markdown_candidate_preserves_user_body_and_custom_properties(tmp_path):
    app, value = application(tmp_path)
    path = app.roots.vault / SOURCE
    original = "# Freeform idea\n\n" + value + " is a source observation.\n"
    path.write_text(original, encoding="utf-8")
    payload = candidate(app, {"tags": [value]}, {"tags": {value: [value + " is a source observation."]}})
    assert validate_candidate(app, app.reference(SOURCE), payload, method_id=METADATA_METHOD_ID) == payload
    assert path.read_text(encoding="utf-8") == original
    path.write_text("---\ncustom_property: keep-me\ntitle: An older name\n---\n" + original, encoding="utf-8")
    payload = candidate(app, {"tags": [value]}, {"tags": {value: [value + " is a source observation."]}})
    assert validate_candidate(app, app.reference(SOURCE), payload, method_id=METADATA_METHOD_ID) == payload
    assert parse_frontmatter(path.read_text()).properties["custom_property"] == "keep-me"


def test_metadata_scalar_uses_declared_type_without_expanding_daily_authority(tmp_path):
    app, _ = application(tmp_path)
    path = app.roots.vault / SOURCE
    document = parse_frontmatter(path.read_text())
    properties = dict(document.properties)
    properties.pop("confidence", None)
    path.write_text(render_frontmatter(properties, document.body + "\nconfidence: unknown\n"))
    payload = candidate(app, {"confidence": "unknown"}, {"confidence": {"unknown": ["confidence: unknown"]}})
    assert validate_candidate(app, app.reference(SOURCE), payload, method_id=METADATA_METHOD_ID) == payload
    original = path.read_bytes()
    with pytest.raises(AdmissionError):
        validate_candidate(app, app.reference(SOURCE), payload, method_id=DAILY_METHOD_ID)
    with pytest.raises(AdmissionError):
        validate_candidate(app, app.reference(SOURCE), payload, method_id=RELATION_METHOD_ID)
    assert path.read_bytes() == original


@pytest.mark.parametrize("mutation", [
    lambda p, value: p.update(registry_digest="sha256:" + "0" * 64),
    lambda p, value: p["evidence"]["tags"].update({value: []}),
    lambda p, value: p["evidence"]["tags"].update({value: ["invented exact quote"]}),
    lambda p, value: p["fields"]["tags"].append(value),
    lambda p, value: p["fields"].update(made_up=[value]),
    lambda p, value: p["fields"].update(title=[value]),
    lambda p, value: p["fields"]["tags"].append("existing-preserved"),
    lambda p, value: p["evidence"]["tags"].update({value: ["# Bounded synthetic stewardship evidence"]}),
])
def test_candidate_rejects_wrong_registry_missing_or_wrong_value_quotes_and_overwrite(tmp_path, mutation):
    app, value = application(tmp_path)
    payload = candidate(app, {"tags": [value]}, {"tags": {value: ["tags: " + value]}})
    mutation(payload, value)
    before = (app.roots.vault / SOURCE).read_bytes()
    with pytest.raises(AdmissionError):
        validate_candidate(app, app.reference(SOURCE), payload, method_id=METADATA_METHOD_ID)
    assert (app.roots.vault / SOURCE).read_bytes() == before


def test_stale_source_and_empty_no_evidence_are_distinct(tmp_path):
    app, _ = application(tmp_path)
    source = app.reference(SOURCE)
    payload = candidate(app, {}, {})
    assert validate_candidate(app, source, payload, method_id=METADATA_METHOD_ID) == payload
    path = app.roots.vault / SOURCE
    path.write_bytes(path.read_bytes() + b"\nUser edit\n")
    with pytest.raises(AdmissionError):
        validate_candidate(app, source, payload, method_id=METADATA_METHOD_ID)


def test_relation_requires_explicit_predicate_and_actual_resolved_link_target(tmp_path):
    app, _ = application(tmp_path)
    value = "[[" + TARGET + "]]"
    payload = candidate(app, {"related": [value]}, {"related": {value: ["related: " + value]}})
    with pytest.raises(AdmissionError):
        validate_candidate(app, app.reference(SOURCE), payload, method_id=RELATION_METHOD_ID, link_evidence={"outgoing": []})
    # A mere target quote without the predicate cannot authorize semantics.
    payload["evidence"]["related"][value] = [value]
    with pytest.raises(AdmissionError):
        validate_candidate(app, app.reference(SOURCE), payload, method_id=RELATION_METHOD_ID)


def test_daily_native_rejects_populated_fields_and_accepts_exact_empty_result(tmp_path):
    app, value = application(tmp_path, daily=True)
    payload = candidate(app, {"tags": [value]}, {"tags": {value: ["tags: " + value]}})
    assert validate_candidate(app, app.reference(SOURCE), payload, method_id=DAILY_METHOD_ID) == payload
    path = app.roots.vault / SOURCE
    doc = parse_frontmatter(path.read_text())
    properties = dict(doc.properties); properties["tags"] = ["user-value"]
    path.write_text(render_frontmatter(properties, doc.body))
    payload["source_reference"] = app.reference(SOURCE)
    with pytest.raises(AdmissionError):
        validate_candidate(app, app.reference(SOURCE), payload, method_id=DAILY_METHOD_ID)
    empty = candidate(app, {}, {})
    assert validate_candidate(app, app.reference(SOURCE), empty, method_id=DAILY_METHOD_ID) == empty


@pytest.mark.parametrize("extra", ["source_digest", "semantic_registry_digests", "field_evidence"])
def test_daily_candidate_rejects_request_provenance_and_payload_aliases(tmp_path, extra):
    app, value = application(tmp_path, daily=True)
    payload = candidate(app, {"tags": [value]}, {"tags": {value: ["tags: " + value]}})
    if extra == "source_digest":
        payload[extra] = payload["source_reference"]["content_digest"]
    elif extra == "semantic_registry_digests":
        payload[extra] = {"relations": payload["registry_digest"]}
    else:
        payload[extra] = payload.pop("evidence")
    before = (app.roots.vault / SOURCE).read_bytes()
    with pytest.raises(AdmissionError):
        validate_candidate(app, app.reference(SOURCE), payload, method_id=DAILY_METHOD_ID)
    assert (app.roots.vault / SOURCE).read_bytes() == before


@pytest.mark.parametrize("policy", [METADATA_POLICY, RELATION_POLICY, DAILY_POLICY])
def test_native_admission_uses_exact_policy_graph_roles_and_selected_route(tmp_path, policy):
    app, _ = application(tmp_path)
    team = binding(app, ROUTE, policy.method_id)
    spec, run = metadata_admission(app, team, app.reference(SOURCE), method_id=policy.method_id,
        work_id="work.native.metadata", work_run_id="workrun.native.metadata",
        admission_receipt_id="admission.native.metadata", runner_binding=ROUTE)
    assert spec["delegation_mode"] == "manager_bounded"
    assert set(policy.roles) <= {p["role_id"] for p in spec["allowed_profiles"]}
    assert not spec.get("explicit_assignments")
    assert spec["continuation_policy"]["max_profile_invocations"] >= len(policy.stages)
    assert native_team_route(spec)[2] == policy.namespace
    assert run["state"] == "admitted"
    generic = copy.deepcopy(spec)
    generic["acceptance_criteria"] = [{"criterion_id": "criterion.1", "description": "Fixture semantics"}]
    assert native_team_route(generic) is None
    changed = dict(ROUTE, adapter_digest="sha256:" + "2" * 64)
    with pytest.raises(AdmissionError):
        metadata_admission(app, team, app.reference(SOURCE), method_id=policy.method_id,
            work_id="work.native.metadata", work_run_id="workrun.native.metadata",
            admission_receipt_id="admission.native.metadata", runner_binding=changed)


def test_relation_method_grants_artifacts_without_direct_alignment_effects(tmp_path):
    app, _ = application(tmp_path)
    method = WorkMethodCatalog.load(app.roots.control).methods[RELATION_METHOD_ID]
    assert "artifact_submit" in method["required_stages"][-1]["tools"]
    assert "alignment_propose" not in method["required_stages"][-1]["tools"]


def test_relation_rejects_invalid_predicate_and_wrong_target_type(tmp_path):
    app, _ = application(tmp_path)
    path = app.roots.vault / SOURCE
    document = parse_frontmatter(path.read_text())
    value = "[[" + TARGET + "]]"
    quote = "projects: " + value
    path.write_text(render_frontmatter(document.properties, document.body + "\n" + quote + "\n"))
    payload = candidate(app, {"projects": [value]}, {"projects": {value: [quote]}})
    with pytest.raises(AdmissionError):
        validate_candidate(app, app.reference(SOURCE), payload, method_id=RELATION_METHOD_ID)
    bad = candidate(app, {"invented_predicate": [value]},
                    {"invented_predicate": {value: [quote]}})
    with pytest.raises(AdmissionError):
        validate_candidate(app, app.reference(SOURCE), bad, method_id=RELATION_METHOD_ID)


def test_one_current_corpus_binding_supports_both_explicit_native_methods(tmp_path):
    app, _ = application(tmp_path)
    metadata = binding(app, ROUTE, METADATA_METHOD_ID)
    daily = binding(app, ROUTE, DAILY_METHOD_ID)
    assert metadata.native_binding_digest == daily.native_binding_digest
    spec, _ = metadata_admission(app, metadata, app.reference(SOURCE), method_id=DAILY_METHOD_ID,
        work_id="work.daily.shared", work_run_id="workrun.daily.shared",
        admission_receipt_id="admission.daily.shared", runner_binding=ROUTE)
    assert spec["method_contract_ref"]["method_id"] == DAILY_METHOD_ID


@pytest.mark.parametrize("method_id", [METADATA_METHOD_ID, RELATION_METHOD_ID, DAILY_METHOD_ID])
@pytest.mark.parametrize("supported", [True, False])
def test_owner_candidate_identity_reaches_independent_eval_without_self_acceptance(tmp_path, method_id, supported):
    app, value = application(tmp_path, daily=method_id == DAILY_METHOD_ID)
    source = app.reference(SOURCE)
    payload = candidate(app, {"tags": [value]},
                        {"tags": {value: ["tags: " + value] if supported else ["invented quote"]}})
    if method_id == RELATION_METHOD_ID:
        target = "[[" + TARGET + "]]"
        payload = candidate(app, {"related": [target]},
                            {"related": {target: ["related: " + target] if supported else ["invented quote"]}})
    team = binding(app, ROUTE, method_id)
    spec, run = metadata_admission(app, team, source, method_id=method_id,
        work_id="work.identity", work_run_id="workrun.identity",
        admission_receipt_id="admission.identity", runner_binding=ROUTE)
    retain_director_context(app, spec, run, source)
    exops = KnowledgeExOps(app,
        authorize_actor=lambda actor, action: actor.actor_id == "owner.identity"
            and action in {"submit_work", "read_work"},
        director_admitted=lambda selected: dict(selected) == spec,
        method_teams={method_id: team},
        current_method_binding=lambda selected: team if selected == method_id else None,
        worker_identity="worker.identity",
        director_method_source_admitted=lambda selected, reference: dict(selected) == spec and dict(reference) == source)
    runner = MetadataToolRunner(application=app, candidate=payload, semantic_outcome="not_run",
                                registry_categories=("relations", "properties", "types", "properties", "relations"))
    runner.exops = exops
    executor = MetadataTeamExecutor(exops, runner=runner, method_id=method_id)
    receipt = exops.submit_work(spec, run, actor=OwnerActor("owner.identity", "cli"),
        action_id="action.identity", expires_at=app.clock() + timedelta(hours=1), method_source_reference=source)
    workflow_id = "knowledgeos/" + run["work_run_id"]
    exops.record_dispatch(run["work_run_id"], action_id=receipt["action_id"],
                          outcome="accepted", workflow_id=workflow_id)
    started = exops.worker_started(run["work_run_id"], worker_identity=exops.worker_identity,
        action_id=receipt["action_id"], content_digest=receipt["content_digest"],
        work_spec_digest=spec["spec_digest"], workflow_id=workflow_id)
    asyncio.run(executor.manager(spec, started))
    asyncio.run(executor.execute_stage(run["work_run_id"], executor.policy.stages[0]))
    before = (app.roots.vault / SOURCE).read_bytes()
    if not supported:
        snapshot = app.journal.read()
        with pytest.raises(AdmissionError) as refused:
            asyncio.run(executor.execute_stage(run["work_run_id"], executor.policy.stages[-1]))
        assert refused.value.code == "SCOPE_DENIED"
        effects = [item for item in app.journal.read()["intents"].values() if item.get("action") == "artifact_submit"]
        assert not effects
        assert app.journal.read()["receipts"] == snapshot["receipts"]
    else:
        record = executor.current(run["work_run_id"])
        source_execution = record[executor.policy.namespace + "_" + executor.policy.stages[0]]
        wrong_source = copy.deepcopy(source_execution["response"])
        wrong_source["tool_calls"][0]["arguments"]["resource_reference"]["content_digest"] = "sha256:" + "0" * 64
        missing_source = copy.deepcopy(source_execution["response"])
        missing_source["tool_calls"] = [
            item for item in missing_source["tool_calls"] if item["name"] != "knowledge_read"]
        changed_source = copy.deepcopy(source_execution["response"])
        changed_source["tool_calls"][0]["result"]["data"]["text"] += "\nInvented evidence"
        for response in (wrong_source, missing_source, changed_source):
            with pytest.raises(AdmissionError):
                executor.validate_stage(record, source_execution["request"], response)
        if method_id != RELATION_METHOD_ID:
            missing_types = copy.deepcopy(source_execution["response"])
            missing_types["tool_calls"] = [
                item for item in missing_types["tool_calls"]
                if item["arguments"].get("category") != "types"]
            with pytest.raises(AdmissionError):
                executor.validate_stage(record, source_execution["request"], missing_types)
        asyncio.run(executor.execute_stage(run["work_run_id"], executor.policy.stages[-1]))
        record = executor.current(run["work_run_id"])
        call = record[executor.policy.namespace + "_" + executor.policy.stages[-1]]["response"]["tool_calls"][-1]
        data = call["result"]["data"]
        effect = app.journal.read()["intents"][data["effect_id"]]
        assert data["candidate_digest"] == effect["candidate_digest"] == record_digest(payload)
        assert data["candidate_digest"] != data["work_artifact_reference"]["content_digest"]
        assert json.loads(effect["identity"]["payload"]["content"]) == payload
        execution = record[executor.policy.namespace + "_" + executor.policy.stages[-1]]
        stage_request = execution["request"]
        stage_response = execution["response"]
        session = TeamToolSession(app, run["work_run_id"], stage_request["executor_id"],
                                  stage_request["graph_run_id"], exops)
        with pytest.raises(AdmissionError) as refused:
            session.authorize("semantic_registry_read", {"category": "unregistered"})
        assert refused.value.code == "SCOPE_DENIED"
        duplicate_artifact = copy.deepcopy(stage_response)
        duplicate_artifact["tool_calls"].append(copy.deepcopy(call))
        with pytest.raises(AdmissionError):
            executor.validate_stage(record, stage_request, duplicate_artifact)
        if method_id == RELATION_METHOD_ID:
            assert [item["arguments"]["category"] for item in stage_response["tool_calls"][:-1]] == [
                "relations", "properties", "types", "properties", "relations"]
            for category in ("properties", "types", "relations"):
                session.authorize("semantic_registry_read", {"category": category})
            missing_relations = copy.deepcopy(stage_response)
            missing_relations["tool_calls"] = [
                item for item in missing_relations["tool_calls"]
                if item["arguments"].get("category") != "relations"]
            with pytest.raises(AdmissionError):
                executor.validate_stage(record, stage_request, missing_relations)
            changed_registry = copy.deepcopy(stage_response)
            changed_registry["tool_calls"][0]["result"]["data"]["definitions"] = {}
            with pytest.raises(AdmissionError):
                executor.validate_stage(record, stage_request, changed_registry)
        result = asyncio.run(executor.assess(run["work_run_id"]))
        context = runner.decisions[-1]["agent_request"]["context"]
        identity = context["evidence"]["observations"]["candidate_identity"]
        assert identity["candidate_digest"] == data["candidate_digest"]
        assert identity["source_digest"] == source["content_digest"]
        assert identity["registry_digest"] == payload["registry_digest"]
        assert set(identity["semantic_registry_digests"]) == {"properties", "types", "relations"}
        assert identity["work_artifact_reference"] == data["work_artifact_reference"]
        assert result.assessment["completion_state"] == "incomplete"
        assert result.next_run["state"] == "awaiting_human"
        assert data["apply_allowed"] is False
        assert not app.journal.read()["decisions"]
    assert (app.roots.vault / SOURCE).read_bytes() == before

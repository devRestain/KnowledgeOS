"""Pure native metadata admission/evidence boundary tests; no fake native replay."""
from __future__ import annotations

import copy

import pytest
from support.metadata_fixture import SOURCE, TARGET, application, binding

from vaultops.adapters.core import AdmissionError
from vaultops.application.metadata_work import (
    DAILY_METHOD_ID,
    DAILY_POLICY,
    METADATA_METHOD_ID,
    METADATA_POLICY,
    RELATION_METHOD_ID,
    RELATION_POLICY,
    metadata_admission,
    validate_candidate,
)
from vaultops.application.temporal_contract import native_team_route
from vaultops.application.work_methods import WorkMethodCatalog
from vaultops.note_engine import parse_frontmatter, render_frontmatter

ROUTE = {"runtime_kind": "hermes_native", "hermes_version": "0.22.5",
         "hermes_revision": "bf0203a794a07aac3ff764972ab9087baa4e4f4e",
         "provider": "openai-codex", "model": "gpt-6.1-terra", "adapter_digest": "sha256:" + "1" * 64}


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


@pytest.mark.parametrize("policy", [METADATA_POLICY, RELATION_POLICY, DAILY_POLICY])
def test_native_admission_uses_exact_policy_graph_roles_and_selected_route(tmp_path, policy):
    app, _ = application(tmp_path)
    team = binding(app, ROUTE, policy.method_id)
    spec, run = metadata_admission(app, team, app.reference(SOURCE), method_id=policy.method_id,
        work_id="work.native.metadata", work_run_id="workrun.native.metadata",
        admission_receipt_id="admission.native.metadata", runner_binding=ROUTE)
    assert spec["delegation_mode"] == "explicit_assignments"
    assert [p["role_id"] for p in spec["allowed_profiles"]] == list(policy.roles)
    assert [a["node_id"] for a in spec["explicit_assignments"]] == ["node." + stage for stage in policy.stages]
    assert spec["continuation_policy"] == {"max_segments": 1, "max_repeated_gap_assessments": 1, "max_profile_invocations": 2}
    assert native_team_route(spec)[2] == policy.namespace
    assert run["state"] == "admitted"
    generic = copy.deepcopy(spec)
    generic["acceptance_criteria"] = [{"criterion_id": "criterion.1", "description": "Fixture semantics"}]
    assert native_team_route(generic) is None
    changed = dict(ROUTE, model="other-model")
    with pytest.raises(AdmissionError):
        metadata_admission(app, team, app.reference(SOURCE), method_id=policy.method_id,
            work_id="work.native.metadata", work_run_id="workrun.native.metadata",
            admission_receipt_id="admission.native.metadata", runner_binding=changed)


def test_relation_method_explicitly_versions_artifact_grant(tmp_path):
    app, _ = application(tmp_path)
    methods = WorkMethodCatalog.load(app.roots.control)
    method = methods.methods[RELATION_METHOD_ID]
    assert method["method_version"] == "1.2.0"
    assert method["required_stages"][1]["tools"] == ["semantic_registry_read", "artifact_submit"]
    assert "artifact_export" in methods.team_catalog.roles["knowledgeos.ontology_curator"]["required_owner_ports"]
    assert "alignment_propose" not in methods.team_catalog.roles["knowledgeos.ontology_curator"]["required_owner_ports"]


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

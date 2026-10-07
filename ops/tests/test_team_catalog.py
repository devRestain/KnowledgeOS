"""Team definition and prospective binding denials without Hermes installation."""

from __future__ import annotations

import copy
from dataclasses import replace

import pytest
from test_exops_temporal import make_exops

from vaultops.adapters.core import AdmissionError, canonical, digest
from vaultops.application.team_catalog import SpecialistBinding
from vaultops.application.work import work_spec_digest


def test_six_team_definitions_are_pinned_and_only_one_team_is_registered(tmp_path):
    exops, fixture = make_exops(tmp_path)
    catalog = exops.catalog
    assert len(catalog.teams) == 6
    assert len(catalog.roles) == 33
    assert set(exops.teams) == {"knowledge-curation"}
    assert catalog.roles["knowledgeos.normalizer"]["required_owner_ports"] == [
        "knowledge_retrieve", "proposal_create"]
    assert catalog.roles["knowledgeos.draft_writer"]["required_owner_ports"] != [
        "knowledge_retrieve", "proposal_create"]
    assert fixture.spec["allowed_profiles"] == [catalog.profile("knowledgeos.normalizer")]


def test_wrong_team_profile_identity_and_shared_teammate_definition(tmp_path):
    exops, fixture = make_exops(tmp_path)
    binding = exops.teams["knowledge-curation"]
    wrong_team = copy.deepcopy(fixture.spec)
    wrong_team["team_id"] = "evidence-research"
    with pytest.raises(AdmissionError, match="WorkSpec does not match"):
        exops.catalog.validate_spec(wrong_team, binding)
    forged = copy.deepcopy(fixture.spec)
    forged["allowed_profiles"] = [exops.catalog.profile("knowledgeos.retriever")]
    with pytest.raises(AdmissionError, match="Profile envelope"):
        exops.catalog.validate_spec(forged, binding)
    shared = replace(binding, specialists=(SpecialistBinding(
        "shared.scoped_evidence_reader", "knowledgeos.evidence_reader.one",
        exops.catalog.profile("shared.scoped_evidence_reader")),))
    shared_reference = {**binding.binding_reference,
                        "content_digest": digest(canonical(shared.content()))}
    exops.catalog.validate_binding(replace(shared, binding_reference=shared_reference))
    research = replace(shared, team_id="evidence-research",
                       manager_executor_id="knowledgeos.research_manager.one",
                       manager_profile=exops.catalog.profile("knowledgeos.research_manager"))
    research_reference = {**binding.binding_reference,
                          "content_digest": digest(canonical(research.content()))}
    exops.catalog.validate_binding(replace(research, binding_reference=research_reference))
    assert shared.specialists[0].profile == research.specialists[0].profile


def test_exact_executor_profile_pair_and_changed_catalog_are_denied(tmp_path):
    exops, fixture = make_exops(tmp_path)
    binding = exops.teams["knowledge-curation"]
    assignment = {"executor_id": "knowledgeos.normalizer.one",
                  **exops.catalog.profile("knowledgeos.normalizer")}
    exops.catalog.validate_assignments([assignment], binding, fixture.spec)
    wrong = {**assignment, **exops.catalog.profile("knowledgeos.draft_writer")}
    with pytest.raises(AdmissionError, match="does not match its executor"):
        exops.catalog.validate_assignments([wrong], binding, fixture.spec)
    altered = copy.deepcopy(fixture.spec)
    altered["allowed_executor_ids"] = ["knowledgeos.normalizer.two"]
    altered["spec_digest"] = work_spec_digest(altered)
    with pytest.raises(AdmissionError, match="unregistered Team specialist"):
        exops.catalog.validate_spec(altered, binding)
    catalog_file = exops.application.roots.control / "ops/config/team-catalog.json"
    catalog_file.write_text(catalog_file.read_text().replace(
        "Compose bounded capture triage and proposal segments",
        "Compose changed capture and proposal segments"))
    with pytest.raises(AdmissionError, match="Team definitions changed"):
        exops._current_binding()


def test_missing_current_registration_blocks_admission(tmp_path):
    exops, fixture = make_exops(tmp_path)
    exops.current_team_binding = lambda team_id: None
    with pytest.raises(AdmissionError, match="Current Team"):
        exops._team_binding(fixture.spec, fixture.run)


def test_native_mapping_digest_and_owner_export_are_required(tmp_path):
    exops, _ = make_exops(tmp_path)
    binding = exops.teams["knowledge-curation"]
    with pytest.raises(AdmissionError, match="Native runner"):
        exops.catalog.validate_binding(replace(binding, native_binding_digest="unselected"))
    unavailable = {**binding.binding_reference, "availability": "unavailable"}
    with pytest.raises(AdmissionError, match="Owner binding reference"):
        exops.catalog.validate_binding(replace(binding, binding_reference=unavailable))

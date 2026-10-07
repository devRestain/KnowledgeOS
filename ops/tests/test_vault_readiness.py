"""Generated Vault responsibility checks use isolated sources and roots only."""

from __future__ import annotations

import json

import pytest
from support.control_factory import make_control_root
from support.vault_example import EXAMPLE_PATH, example_source

from vaultops.application.knowledge import KnowledgeApplication
from vaultops.bootstrap import _directory_targets
from vaultops.interfaces.cli import main
from vaultops.note_engine import NoteEngine, parse_frontmatter, render_frontmatter
from vaultops.paths import resolve_paths
from vaultops.projection import generate_projection
from vaultops.vault_projection import (
    CONTRACT_PATH,
    expected_projection,
)
from vaultops.vault_readiness import check_vault_readiness

INPUTS = ("blueprint", "ops/actions", "ops/config", "ops/expected", "ops/policies", "ops/prompts", "ops/schemas", "ops/clients/obsidian-thin-client")


def snapshot(root):
    return {path.relative_to(root).as_posix(): path.read_bytes() for path in root.rglob("*") if path.is_file() and not path.is_symlink()}


def fixture(tmp_path, *, seeded=False):
    control = make_control_root(tmp_path, INPUTS)
    roots = resolve_paths(control)
    for relative, payload in expected_projection(control).items():
        target = roots.vault / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(payload)
    for relative in _directory_targets(NoteEngine.from_root(control).blueprint):
        directory = roots.vault / relative
        directory.mkdir(parents=True, exist_ok=True)
        if not relative.startswith("99_System/"):
            (directory / ".knowledgeos-directory").write_bytes(b"")
    (roots.vault / ".knowledgeos-root.json").write_text(json.dumps({"schema_version": 1, "contract_id": "knowledgeos-vault-root-v1", "canonical_vault_name": "KnowledgeHub", "vault_uuid": "411602c1-5278-4a8b-8b96-9183fb6ef8c2", "expected_branch": "main", "remote_identity_sha256": "a" * 64}))
    if seeded:
        (roots.vault / EXAMPLE_PATH).write_text(example_source())
    return roots


def test_generated_vault_readiness_and_cli_are_read_only(tmp_path, capsys):
    roots = fixture(tmp_path)
    before = tuple(snapshot(root) for root in (roots.control, roots.vault, roots.state, roots.runtime))
    result = check_vault_readiness(roots)
    assert result.passed, result.report
    assert result.report["typed_notes"] == 6
    assert not (roots.vault / EXAMPLE_PATH).exists()
    assert result.report["static_links"] > 20
    assert result.report["core_manifest"]["graph_profile"] == "graphless"
    assert result.report["operational_adoption"] == "not_run"
    assert main(["vault-artifacts", "check", "--root", str(roots.control)]) == 0
    assert json.loads(capsys.readouterr().out)["mode"] == "read_only"
    assert tuple(snapshot(root) for root in (roots.control, roots.vault, roots.state, roots.runtime)) == before


def test_native_profile_json_reserialization_preserves_meaning_and_effect_guards(tmp_path):
    roots = fixture(tmp_path)
    for relative in expected_projection(roots.control):
        if relative.startswith((".obsidian-mac/", ".obsidian/")) and relative.endswith(".json"):
            path = roots.vault / relative
            path.write_text(json.dumps(json.loads(path.read_bytes()), ensure_ascii=False))
    before = snapshot(roots.vault)
    assert check_vault_readiness(roots).passed
    assert snapshot(roots.vault) == before
    default_profile = roots.vault / ".obsidian/core-plugins.json"
    settings = json.loads(default_profile.read_bytes())
    settings["editor-status"] = True
    default_profile.write_text(json.dumps(settings))
    assert check_vault_readiness(roots).passed
    path = roots.vault / ".obsidian-mac/plugins/obsidian-git/data.json"
    settings = json.loads(path.read_bytes())
    settings["autoPullOnBoot"] = True
    path.write_text(json.dumps(settings))
    result = check_vault_readiness(roots)
    assert not result.passed
    assert "VAULT_PROFILE_EFFECT_ENABLED" in {error["code"] for error in result.report["errors"]}


@pytest.mark.parametrize("field,value", [("authority", "operator"), ("purpose", "owner_journal")])
def test_vault_contract_cannot_grant_control_authority(tmp_path, field, value):
    roots = fixture(tmp_path)
    path = roots.vault / CONTRACT_PATH
    document = json.loads(path.read_bytes())
    document[field] = value
    path.write_text(json.dumps(document))
    before = snapshot(roots.state)
    result = check_vault_readiness(roots)
    assert not result.passed
    assert "VAULT_CONTROL_AUTHORITY" in {error["code"] for error in result.report["errors"]}
    assert snapshot(roots.state) == before


@pytest.mark.parametrize("change", ["pin", "link", "marker", "symlink", "malformed", "duplicate"])
def test_pin_link_marker_and_symlink_drift_is_refused(tmp_path, change):
    roots = fixture(tmp_path, seeded=change in {"link", "duplicate"})
    if change == "pin":
        path = roots.vault / CONTRACT_PATH
        document = json.loads(path.read_bytes())
        document["pins"]["core_version"] = "unknown"
        path.write_text(json.dumps(document))
    elif change == "link":
        path = roots.vault / EXAMPLE_PATH
        path.write_text(path.read_text() + "\n[[Missing note]]\n")
    elif change == "marker":
        (roots.vault / "20_Projects/.knowledgeos-directory").unlink()
    elif change == "malformed":
        (roots.vault / "40_Knowledge/Ideas/Broken.md").write_text("# Broken\n")
    elif change == "duplicate":
        document = parse_frontmatter((roots.vault / EXAMPLE_PATH).read_text())
        (roots.vault / "40_Knowledge/Ideas/Another example.md").write_text(render_frontmatter({**document.properties, "title": "Another example"}, document.body))
    else:
        path = roots.vault / CONTRACT_PATH
        path.unlink()
        path.symlink_to(tmp_path / "not-in-vault")
    before = (snapshot(roots.vault), snapshot(roots.state))
    result = check_vault_readiness(roots)
    assert not result.passed
    if change in {"malformed", "duplicate"}:
        assert {"malformed": "NOTE_FRONTMATTER_INVALID", "duplicate": "NOTE_ID_DUPLICATE"}[change] in {error["code"] for error in result.report["errors"]}
    assert (snapshot(roots.vault), snapshot(roots.state)) == before


@pytest.mark.parametrize("effect", ["git", "provider", "missing_toolbar_target"])
def test_profile_effect_guard_survives_matching_generated_bytes(tmp_path, effect):
    roots = fixture(tmp_path)
    relative = ".obsidian-mac/plugins/obsidian-git/data.json"
    profile_path = roots.control / "ops/config/vault-profile.json"
    profile = json.loads(profile_path.read_bytes())
    expected_error = "VAULT_PROFILE_EFFECT_ENABLED"
    if effect == "git":
        profile[relative]["autoPullOnBoot"] = True
    elif effect == "provider":
        relative = ".obsidian-mac/plugins/quickadd/data.json"
        profile[relative]["ai"]["providers"] = [{"endpoint": "https://provider.invalid", "apiKey": ""}]
    else:
        relative = ".obsidian-mac/plugins/note-toolbar/data.json"
        profile[relative]["toolbars"][0]["items"][0]["link"] = "Missing.md"
        expected_error = "VAULT_LINK_MISSING"
    profile_path.write_text(json.dumps(profile))
    (roots.vault / relative).write_bytes(expected_projection(roots.control)[relative])
    result = check_vault_readiness(roots)
    assert not result.passed
    assert {error["code"] for error in result.report["errors"]} == {expected_error}


def test_bad_core_binding_fails_before_vault_projection_read(tmp_path, monkeypatch):
    roots = fixture(tmp_path)
    binding_path = roots.control / "ops/config/core-adoption.json"
    binding = json.loads(binding_path.read_bytes())
    binding["operation_id"] = "foreign"
    binding_path.write_text(json.dumps(binding))
    called = []
    monkeypatch.setattr("vaultops.vault_readiness.expected_projection", lambda _root: called.append(True))
    assert not check_vault_readiness(roots).passed
    assert called == []


def test_seeded_domain_retrieve_proposal_review_preserves_canonical_bytes(tmp_path):
    roots = fixture(tmp_path, seeded=True)
    before = (roots.vault / EXAMPLE_PATH).read_bytes()
    projection, code = generate_projection(roots)
    assert code == 0, projection
    app = KnowledgeApplication.from_roots(roots)
    retrieved, code = app.retrieve("Core readiness example")
    assert code == 0, retrieved
    assert EXAMPLE_PATH in {candidate["path"] for candidate in retrieved["candidates"]}
    created, code = app.create_normalize_proposal(source_reference=app.reference(EXAMPLE_PATH))
    assert code == 0, created
    assert created["effects"] == {"pending_artifact_created": True, "canonical_note_changed": False}
    state_before, vault_before = snapshot(roots.state), snapshot(roots.vault)
    inspected, code = app.inspect_proposal(created["resource_reference"])
    assert code == 0, inspected
    assert inspected["acceptance_state"] == "pass"
    assert snapshot(roots.state) == state_before and snapshot(roots.vault) == vault_before
    assert (roots.vault / EXAMPLE_PATH).read_bytes() == before
    from vaultops import proposals

    document = proposals._load_proposal(roots, created["proposal_path"])
    target = proposals._plan(roots, document).target_markdown
    assert parse_frontmatter(target).properties == parse_frontmatter(before.decode()).properties
    assert parse_frontmatter(target).body == parse_frontmatter(before.decode()).body
    replay, code = app.create_normalize_proposal(source_reference=app.reference(EXAMPLE_PATH))
    assert code == 0 and replay["replayed"], replay
    assert replay["proposal_sha256"] == created["proposal_sha256"]

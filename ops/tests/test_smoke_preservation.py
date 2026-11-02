"""Native smoke preservation permits refresh, not account/configuration replacement."""
from __future__ import annotations

import copy
import json

import pytest
from support.research_work_smoke import preservation_snapshot


@pytest.fixture
def selected(tmp_path, monkeypatch):
    control = tmp_path / "Operations/KnowledgeOS"
    control.mkdir(parents=True)
    vault = tmp_path / "Vaults/KnowledgeHub"
    vault.mkdir(parents=True)
    (vault / "Existing.md").write_text("# Existing synthetic note\n")
    profile = tmp_path / "profiles/selected"
    profile.mkdir(parents=True)
    monkeypatch.setenv("KNOWLEDGEOS_HERMES_PROFILE_HOME", str(profile))
    (profile / "config.yaml").write_text("model: selected\n")
    (profile / ".env").write_text("EXAMPLE=preserved\n")
    document = {
        "version": 1,
        "updated_at": "before",
        "providers": {
            "openai-codex": {
                "auth_mode": "chatgpt",
                "last_refresh": "before",
                "tokens": {"account_id": "synthetic-account", "access_token": "old-access",
                           "refresh_token": "old-refresh", "id_token": "old-id"},
            },
            "other": {"key": "synthetic-other"},
        },
    }
    auth = profile / "auth.json"
    auth.write_text(json.dumps(document))
    return control, profile, auth, document


def test_selected_existing_grant_can_rotate_without_freezing_model_smoke(selected):
    control, _profile, auth, document = selected
    before = preservation_snapshot(control)
    changed = copy.deepcopy(document)
    changed["updated_at"] = "after"
    provider = changed["providers"]["openai-codex"]
    provider["last_refresh"] = "after"
    provider["tokens"].update(access_token="new-access", refresh_token="new-refresh", id_token="new-id")
    auth.write_text(json.dumps(changed))
    assert preservation_snapshot(control) == before


@pytest.mark.parametrize("change", ["account", "missing_token", "empty_token", "other_provider", "config", "env", "auth_deleted"])
def test_refresh_permission_does_not_hide_identity_or_user_data_changes(selected, change):
    control, profile, auth, document = selected
    before = preservation_snapshot(control)
    tokens = document["providers"]["openai-codex"]["tokens"]
    if change == "account":
        tokens["account_id"] = "different-account"
    elif change == "missing_token":
        del tokens["refresh_token"]
    elif change == "empty_token":
        tokens["access_token"] = ""
    elif change == "other_provider":
        document["providers"]["other"]["key"] = "replacement"
    elif change == "config":
        (profile / "config.yaml").write_text("model: other\n")
    elif change == "env":
        (profile / ".env").write_text("EXAMPLE=changed\n")
    elif change == "auth_deleted":
        auth.unlink()
    if change not in {"config", "env", "auth_deleted"}:
        auth.write_text(json.dumps(document))
    assert preservation_snapshot(control) != before


def test_unselected_profile_credentials_remain_exact(selected):
    control, profile, _auth, document = selected
    other = profile.parent / "other"
    other.mkdir()
    auth = other / "auth.json"
    auth.write_text(json.dumps(document))
    before = preservation_snapshot(control)
    document["providers"]["openai-codex"]["tokens"]["access_token"] = "changed-other-profile"
    auth.write_text(json.dumps(document))
    assert preservation_snapshot(control) != before

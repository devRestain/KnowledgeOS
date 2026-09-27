from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from support.plugin_factory import declared_community_plugins, make_plugin_profile, write_json

from vaultops.diagnostics import plugins_audit_report


def _health(root: Path) -> dict[str, Any]:
    report, exit_code = plugins_audit_report(root)
    assert exit_code == 0, report
    return {
        "status": report["status"],
        "errors": [item["code"] for item in report["errors"]],
        "declared_capabilities": [
            (item["id"], item["state"]) for item in report["declared_capabilities"]
        ],
        "degraded_core_capabilities": report["degraded_core_capabilities"],
        "core_required": report["setting_registry"]["core_setting_registry"][
            "required_capabilities"
        ],
    }


def test_ordinary_note_add_edit_and_delete_leave_audit_health_unchanged(tmp_path: Path) -> None:
    baseline_root, _ = make_plugin_profile(tmp_path / "baseline")
    variant_root, _ = make_plugin_profile(tmp_path / "variant")
    expected = _health(baseline_root)
    note = variant_root / "KnowledgeHub" / "20_Projects" / "Ordinary.md"

    note.parent.mkdir(parents=True)
    note.write_text("# User note\nfirst version\n", encoding="utf-8")
    assert _health(variant_root) == expected

    note.write_text(
        "---\nowner: user\ncustom_property: arbitrary\n---\n# User note\nsecond version\n",
        encoding="utf-8",
    )
    assert _health(variant_root) == expected

    note.unlink()
    assert _health(variant_root) == expected


def test_unrelated_bytes_in_a_disposable_vault_alias_do_not_change_audit_health(
    tmp_path: Path,
) -> None:
    root, _ = make_plugin_profile(tmp_path / "control")
    expected = _health(root)

    alias_bytes = root / "KnowledgeHub" / ".user-owned" / "opaque.bin"
    alias_bytes.parent.mkdir(parents=True)
    alias_bytes.write_bytes(b"unrelated disposable alias data\x00\xff")

    assert _health(root) == expected


@pytest.mark.parametrize(
    "mutation",
    (
        "unmanaged_plugin",
        "future_core_flag",
        "plugin_order",
        "plugin_version",
        "presentation",
        "extra_choice",
    ),
)
def test_open_world_profile_mutations_leave_owned_audit_health_unchanged(
    tmp_path: Path,
    mutation: str,
) -> None:
    baseline_root, _ = make_plugin_profile(tmp_path / "baseline")
    variant_root, profile = make_plugin_profile(tmp_path / "variant")
    expected = _health(baseline_root)

    if mutation == "unmanaged_plugin":
        ids = declared_community_plugins(variant_root)
        write_json(profile / "community-plugins.json", [*ids, "user-plugin"])
        user_manifest = profile / "plugins" / "user-plugin" / "manifest.json"
        user_manifest.parent.mkdir(parents=True)
        user_manifest.write_text("not-json", encoding="utf-8")
    elif mutation == "future_core_flag":
        path = profile / "core-plugins.json"
        flags = json.loads(path.read_text(encoding="utf-8"))
        flags["future-core-flag"] = {"arbitrary": [1, 2, 3]}
        write_json(path, flags)
    elif mutation == "plugin_order":
        write_json(
            profile / "community-plugins.json",
            list(reversed(declared_community_plugins(variant_root))),
        )
    elif mutation == "plugin_version":
        write_json(
            profile / "plugins" / "quickadd" / "manifest.json",
            {"id": "quickadd", "version": "user-selected-version"},
        )
    elif mutation == "presentation":
        write_json(profile / "app.json", {"propertiesInDocument": "hidden", "theme": "user-choice"})
    elif mutation == "extra_choice":
        write_json(
            profile / "plugins" / "quickadd" / "manifest.json",
            {"id": "quickadd", "version": "arbitrary"},
        )
        quickadd_data_path = profile / "plugins" / "quickadd" / "data.json"
        quickadd_data = json.loads(quickadd_data_path.read_text(encoding="utf-8"))
        quickadd_data["choices"].append({"name": "USER_OWNED_CHOICE", "type": "macro"})
        quickadd_data["userOwnedExtension"] = {"presentation": "compact"}
        write_json(
            quickadd_data_path,
            quickadd_data,
        )
    else:
        raise AssertionError(f"unknown mutation: {mutation}")

    assert _health(variant_root) == expected

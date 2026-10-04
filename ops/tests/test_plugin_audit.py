from __future__ import annotations

import json
from pathlib import Path

from support.control_factory import fixture_path
from support.plugin_factory import (
    declared_community_plugins,
    make_plugin_profile,
    required_core_flags,
    write_json,
)
from support.snapshots import snapshot_files

from vaultops.diagnostics import EXIT_CONFIG_INVALID, EXIT_DEGRADED, plugins_audit_report


def _separate_profile_roots(root: Path) -> tuple[Path, Path, Path, Path]:
    control = root.with_name("control Ω")
    root.rename(control)
    vault = control.parent / "KnowledgeHub Ω"
    (fixture_path(control, "vault")).rename(vault)
    runtime = control.parent / "runtime Ω"
    (fixture_path(control, "runtime")).rename(runtime)
    runtime.chmod(0o700)
    config = control / "ops/vaultops.toml"
    config.write_text(
        "\n".join(
            (
                "schema_version = 3",
                'project_name = "KnowledgeOS"',
                f"control_root = {json.dumps(str(control), ensure_ascii=False)}",
                f"vault_root = {json.dumps(str(vault), ensure_ascii=False)}",
                f"runtime_root = {json.dumps(str(runtime), ensure_ascii=False)}",
                f'core_root = {json.dumps(str(fixture_path(control, "core")), ensure_ascii=False)}',
                f'state_root = {json.dumps(str(fixture_path(control, "state")), ensure_ascii=False)}',
                'timezone = "Asia/Seoul"',
                "",
            )
        ),
        encoding="utf-8",
    )
    return control, vault, runtime, vault / ".obsidian-mac"


def test_plugin_audit_accepts_required_subset_and_ignores_unmanaged_files(tmp_path: Path) -> None:
    flags = required_core_flags()
    flags["future-core-flag"] = {"user_choice": True}
    root, profile = make_plugin_profile(
        tmp_path,
        core_flags=flags,
        plugin_data={
            "quickadd": {
                "disableOnlineFeatures": True,
                "futureSetting": "custom",
                "privateValue": "do-not-report",
            }
        },
        plugin_versions={"quickadd": "user-selected-version"},
        unmanaged_files={"plugins/unrelated-community-plugin/manifest.json": "not-json"},
    )

    # Include the Blueprint-declared capabilities as well as the unrelated ID.
    write_json(profile / "community-plugins.json", [*declared_community_plugins(root), "unrelated-community-plugin"])
    write_json(
        profile / "app.json",
        {"futureCoreSetting": {"custom": True}, "propertiesInDocument": "compact"},
    )
    before = snapshot_files(
        profile,
        (
            "community-plugins.json",
            "core-plugins.json",
            "app.json",
            "plugins/quickadd/manifest.json",
            "plugins/quickadd/data.json",
        ),
    )

    report, exit_code = plugins_audit_report(root)
    after = snapshot_files(profile, tuple(before))

    assert exit_code == 0, report
    assert report["status"] == "PASS"
    assert report["serialized_deployment_evidence"] == "pass"
    assert all(item["state"] == "installed" for item in report["declared_capabilities"])
    assert report["unmanaged_plugins"] == ["unrelated-community-plugin"]
    assert report["setting_registry"]["core_setting_registry"]["unknown_observed_flags"] == [
        "future-core-flag"
    ]
    core_registry = report["setting_registry"]["core_setting_registry"]
    assert core_registry["mobile_core_policy"]["state"] == "not_inferred"
    assert not any(
        item["component_id"] == "core:future-core-flag"
        for item in core_registry["entries"]
    )
    assert report["device_proof"]["state"] == "not_inferred"
    assert report["device_proof"]["evidence_class"] == "device"
    quickadd = next(
        item
        for item in report["setting_registry"]["entries"]
        if item["component_id"] == "plugin:quickadd"
    )
    assert quickadd["current_value"]["manifest_version"] == "user-selected-version"
    assert "futureSetting" in quickadd["current_value"]["serialized_keys"]
    assert "do-not-report" not in json.dumps(report)
    assert not any(
        item["component_id"] == "plugin:unrelated-community-plugin"
        for item in report["setting_registry"]["entries"]
    )
    assert after == before


def test_plugin_audit_uses_configured_vault_profile_and_preserves_unknown_settings(
    tmp_path: Path,
) -> None:
    flags = required_core_flags()
    flags["future-core-flag"] = {"user_choice": True}
    root, _profile = make_plugin_profile(
        tmp_path,
        core_flags=flags,
        plugin_data={"quickadd": {"futureSetting": "user-owned"}},
    )
    control, vault, runtime, profile = _separate_profile_roots(root)

    report, exit_code = plugins_audit_report(control)

    assert exit_code == 0, report
    assert report["status"] == "PASS"
    assert report["profile_root"] == str(profile)
    assert report["setting_registry"]["core_setting_registry"]["unknown_observed_flags"] == [
        "future-core-flag"
    ]
    assert report["setting_registry"]["core_setting_registry"]["daily_notes_contract"][
        "template_source_state"
    ] == "pass"
    quickadd = next(
        item
        for item in report["setting_registry"]["entries"]
        if item["component_id"] == "plugin:quickadd"
    )
    assert "futureSetting" in quickadd["current_value"]["serialized_keys"]
    assert not (control / "KnowledgeHub").exists()
    assert not (control / "runtime").exists()
    assert vault.is_dir() and runtime.is_dir()


def test_plugin_audit_degrades_only_the_missing_declared_plugin(tmp_path: Path) -> None:
    root, _ = make_plugin_profile(tmp_path)
    installed = [plugin_id for plugin_id in declared_community_plugins(root) if plugin_id != "homepage"]
    write_json(fixture_path(root, "vault") / ".obsidian-mac" / "community-plugins.json", installed)

    report, exit_code = plugins_audit_report(root)

    assert exit_code == EXIT_DEGRADED
    assert report["status"] == "DEGRADED"
    assert [item["id"] for item in report["declared_capabilities"] if item["state"] != "installed"] == [
        "homepage"
    ]
    assert report["fallback"] == "canonical_markdown_and_plugin_free_surface_available"


def test_plugin_audit_degrades_a_missing_required_core_capability(tmp_path: Path) -> None:
    flags = required_core_flags()
    flags.pop("properties")
    root, _ = make_plugin_profile(tmp_path, core_flags=flags)

    report, exit_code = plugins_audit_report(root)

    assert exit_code == EXIT_DEGRADED
    assert report["status"] == "DEGRADED"
    assert "core:properties" in report["degraded_core_capabilities"]


def test_plugin_audit_rejects_escaping_core_creation_paths(tmp_path: Path) -> None:
    root, profile = make_plugin_profile(tmp_path)
    write_json(
        profile / "daily-notes.json",
        {"folder": "../outside", "template": "99_System/Templates/T10_Daily.md"},
    )

    report, exit_code = plugins_audit_report(root)

    assert exit_code == EXIT_CONFIG_INVALID
    assert any(item["code"] == "PLUGIN_CORE_DAILY_PATH_UNSAFE" for item in report["errors"])


def test_plugin_audit_fails_closed_on_malformed_owned_inventory_and_manifest(tmp_path: Path) -> None:
    root, profile = make_plugin_profile(
        tmp_path,
        plugin_data={"quickadd": {"choices": []}},
    )
    manifest = profile / "plugins" / "quickadd" / "manifest.json"
    manifest.write_text(json.dumps({"id": "wrong-id", "version": "unknown"}), encoding="utf-8")

    report, exit_code = plugins_audit_report(root)

    assert exit_code == EXIT_CONFIG_INVALID
    assert report["status"] == "FAIL"
    assert any(item["code"] == "PLUGIN_MANIFEST_ID_MISMATCH" for item in report["errors"])


def test_plugin_audit_rejects_duplicate_owned_ids_and_symlinked_settings(tmp_path: Path) -> None:
    root, profile = make_plugin_profile(tmp_path)
    community = profile / "community-plugins.json"
    installed = declared_community_plugins(root)
    write_json(community, [*installed, "quickadd"])

    duplicate_report, duplicate_code = plugins_audit_report(root)

    assert duplicate_code == EXIT_CONFIG_INVALID
    assert any(item["code"] == "PLUGIN_CONFIG_INVALID" for item in duplicate_report["errors"])

    write_json(community, installed)
    data_path = profile / "plugins/quickadd/data.json"
    outside = tmp_path / "external-data.json"
    outside.write_text('{"not": "a trusted owned settings file"}\n', encoding="utf-8")
    data_path.unlink()
    data_path.symlink_to(outside)

    symlink_report, symlink_code = plugins_audit_report(root)

    assert symlink_code == EXIT_CONFIG_INVALID
    assert any(item["code"] == "PLUGIN_DATA_INVALID" for item in symlink_report["errors"])
    assert "a trusted owned settings file" not in json.dumps(symlink_report)

    malformed_root, malformed_profile = make_plugin_profile(tmp_path / "malformed-root")
    (malformed_profile / "plugins/quickadd/data.json").write_text("[]\n", encoding="utf-8")

    malformed_report, malformed_code = plugins_audit_report(malformed_root)

    assert malformed_code == EXIT_CONFIG_INVALID
    assert any(item["code"] == "PLUGIN_DATA_INVALID" for item in malformed_report["errors"])


def test_plugin_audit_rejects_a_symlinked_profile_root(tmp_path: Path) -> None:
    root, profile = make_plugin_profile(tmp_path)
    external_profile = tmp_path / "external-profile"
    profile.rename(external_profile)
    profile.symlink_to(external_profile, target_is_directory=True)

    report, exit_code = plugins_audit_report(root)

    assert exit_code == EXIT_CONFIG_INVALID
    assert report["status"] == "FAIL"
    assert report["profile_state"] == "invalid"
    assert any(item["code"] == "PLUGIN_PROFILE_PATH_INVALID" for item in report["errors"])


def test_plugin_audit_rejects_symlinked_owned_data_root_without_reading_it(
    tmp_path: Path, monkeypatch
) -> None:
    root, profile = make_plugin_profile(tmp_path)
    plugin_root = profile / "plugins/quickadd"
    plugin_root.rename(profile / "plugins/quickadd-original")
    external_root = tmp_path / "external-quickadd"
    external_root.mkdir()
    external_files = {external_root / "manifest.json", external_root / "data.json"}
    write_json(external_root / "manifest.json", {"id": "quickadd", "version": "private"})
    write_json(external_root / "data.json", {"privateValue": "must not be read"})
    plugin_root.symlink_to(external_root, target_is_directory=True)

    original_read_text = Path.read_text
    external_reads: list[Path] = []

    def observe_read(path: Path, *args, **kwargs):
        if path.resolve() in external_files:
            external_reads.append(path)
        return original_read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", observe_read)

    report, exit_code = plugins_audit_report(root)

    assert exit_code == EXIT_CONFIG_INVALID
    assert report["status"] == "FAIL"
    assert any(item["code"] == "PLUGIN_DATA_ROOT_INVALID" for item in report["errors"])
    assert external_reads == []
    assert "must not be read" not in json.dumps(report)


def test_plugin_audit_reports_missing_profile_as_inactive_with_fallback(tmp_path: Path) -> None:
    root, profile = make_plugin_profile(tmp_path)
    control, vault, _runtime, profile = _separate_profile_roots(root)
    profile.rename(profile.with_name("profile-preserved-outside-audit-path"))

    report, exit_code = plugins_audit_report(control)

    assert exit_code == 0, report
    assert report["status"] == "INACTIVE"
    assert report["profile_state"] == "not_configured"
    assert report["profile_root"] == str(profile)
    assert report["fallback"] == "canonical_markdown_and_plugin_free_surface_available"
    assert all(item["state"] == "inactive" for item in report["declared_capabilities"])
    assert not (control / "KnowledgeHub").exists()
    assert (vault / "profile-preserved-outside-audit-path").is_dir()


def test_plugin_audit_distinguishes_configured_empty_profile_from_missing_profile(
    tmp_path: Path,
) -> None:
    root, _profile = make_plugin_profile(tmp_path)
    control, _vault, _runtime, profile = _separate_profile_roots(root)
    data_path = profile / "plugins/quickadd/data.json"
    data = json.loads(data_path.read_text(encoding="utf-8"))
    data.pop("choices")
    write_json(data_path, data)

    unknown_report, _unknown_exit_code = plugins_audit_report(control)
    assert (
        unknown_report["setting_registry"]["quickadd_setting_registry"]["observed_choices"]["state"]
        == "unknown"
    )

    data["choices"] = []
    write_json(data_path, data)
    empty_report, _empty_exit_code = plugins_audit_report(control)

    assert empty_report["profile_state"] == "configured"
    assert empty_report["profile_root"] == str(profile)
    assert (
        empty_report["setting_registry"]["quickadd_setting_registry"]["observed_choices"]["state"]
        == "unconfigured"
    )
    assert (profile / "community-plugins.json").is_file()


def test_plugin_audit_preserves_malformed_profile_json_without_exposing_values(tmp_path: Path) -> None:
    root, profile = make_plugin_profile(tmp_path)
    community = profile / "community-plugins.json"
    community.write_text('{"not": "an array"}', encoding="utf-8")

    report, exit_code = plugins_audit_report(root)

    assert exit_code == EXIT_CONFIG_INVALID
    assert report["status"] == "FAIL"
    assert report["errors"] == [
        {
            "code": "PLUGIN_CONFIG_ROOT_INVALID",
            "locator": "/community-plugins.json",
            "message": "community-plugins.json root must be an array of plugin IDs: community-plugins.json",
            "severity": "error",
        }
    ]
    assert "not an array" not in json.dumps(report)

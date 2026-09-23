from __future__ import annotations

import json
from pathlib import Path

from vaultops.diagnostics import plugins_audit_report
from vaultops.tasks_settings import build_tasks_setting_registry
from vaultops.yaml_safe import load_yaml_file

CONTROL_ROOT = Path(__file__).resolve().parents[2]


def test_p05_registry_binds_read_only_queries_statuses_and_human_date_actions_without_mutation() -> None:
    data_path = CONTROL_ROOT / "KnowledgeHub/.obsidian-mac/plugins/obsidian-tasks-plugin/data.json"
    before = data_path.read_bytes()

    report, exit_code = plugins_audit_report(CONTROL_ROOT)

    assert exit_code == 0, report
    registry = report["setting_registry"]["tasks_setting_registry"]
    assert registry["schema_version"] == 1
    assert registry["plugin"] == "obsidian-tasks-plugin"
    assert registry["manifest"] == {
        "source": "KnowledgeHub/.obsidian-mac/plugins/obsidian-tasks-plugin/manifest.json",
        "id": "obsidian-tasks-plugin",
        "version": "8.4.0",
    }
    assert registry["global_query"] == {
        "observed": "",
        "state": "empty",
        "javascript_state": "not_active",
        "authority": "read_only_query_result_not_write_authority",
    }
    assert registry["global_filter"] == {
        "observed": "#task",
        "state": "pass",
        "remove_global_filter": {"observed": False, "state": "pass"},
        "schema_owner": "Markdown task tag convention and Tasks query surface",
    }

    status_registry = registry["status_mapping"]
    assert status_registry["mapping_state"] == "pass"
    statuses = {
        item["name"]: item
        for section in (status_registry["core"], status_registry["custom"])
        for item in section
    }
    assert statuses == {
        "Todo": {
            "symbol": " ",
            "name": "Todo",
            "next_status_symbol": "x",
            "available_as_command": True,
            "type": "TODO",
            "human_action": "explicit_status_command_or_checkbox_edit",
            "canonical_task_state": "open",
        },
        "Done": {
            "symbol": "x",
            "name": "Done",
            "next_status_symbol": " ",
            "available_as_command": True,
            "type": "DONE",
            "human_action": "explicit_status_command_or_checkbox_edit",
            "canonical_task_state": "done",
        },
        "In Progress": {
            "symbol": "/",
            "name": "In Progress",
            "next_status_symbol": "x",
            "available_as_command": True,
            "type": "IN_PROGRESS",
            "human_action": "explicit_status_command_or_checkbox_edit",
            "canonical_task_state": "in_progress",
        },
        "Cancelled": {
            "symbol": "-",
            "name": "Cancelled",
            "next_status_symbol": " ",
            "available_as_command": True,
            "type": "CANCELLED",
            "human_action": "explicit_status_command_or_checkbox_edit",
            "canonical_task_state": "cancelled",
        },
    }
    assert status_registry["canonical_frontmatter_status_is_separate"] is True

    dates = registry["date_policy"]
    assert dates["created"]["state"] == "disabled"
    assert dates["done"]["state"] == "human_action_write_enabled"
    assert dates["cancelled"]["state"] == "human_action_write_enabled"
    assert dates["filename_as_scheduled_date"]["state"] == "disabled"
    assert dates["filename_as_date_folders"]["state"] == "empty"
    assert registry["recurrence"]["recurrence_on_next_line"]["state"] == "preserved"
    assert registry["recurrence"]["remove_scheduled_date_on_recurrence"]["state"] == "preserved"

    javascript = registry["javascript_boundary"]
    assert javascript["setting"]["state"] == "unknown"
    assert javascript["preset_names"] == ["this_folder_only"]
    assert javascript["preset_state"] == "explicitly_unresolved"
    assert javascript["active_query_state"] == "not_active"
    assert javascript["filter_by_function_execution"] == "forbidden"

    sources = {item["source"]: item for item in registry["query_sources"]}
    assert sources["KnowledgeHub/99_System/Dashboards/Tasks.md"]["state"] == "pass"
    assert len(sources["KnowledgeHub/99_System/Dashboards/Tasks.md"]["queries"]) == 3
    assert sources["KnowledgeHub/99_System/Dashboards/Weekly_Review.md"]["state"] == "pass"
    assert len(sources["KnowledgeHub/99_System/Dashboards/Weekly_Review.md"]["queries"]) == 1
    all_queries = [query for source in sources.values() for query in source["queries"]]
    assert all(query["forbidden_javascript"] == [] for query in all_queries)
    assert all(query["authority"] == "read_only_query_result_not_write_authority" for query in all_queries)
    assert all(query["human_completion"] == "explicit_status_action_on_source_markdown_task_line" for query in all_queries)
    assert sum(query["limit_state"] == "bounded" for query in all_queries) == 3
    assert sum(query["limit_state"] == "unbounded_read_only" for query in all_queries) == 1
    assert registry["safety_policy"]["automatic_completion"] == "forbidden"
    assert registry["safety_policy"]["external_task_service"] == "forbidden"
    assert registry["evidence_boundaries"]["runtime"] == "not_run"
    assert registry["evidence_boundaries"]["device"] == "not_run"
    assert data_path.read_bytes() == before


def test_p05_registry_keeps_missing_profile_and_query_sources_unknown(tmp_path: Path) -> None:
    root = tmp_path / "control"
    root.mkdir()
    blueprint = load_yaml_file(CONTROL_ROOT / "blueprint/blueprint.yaml")

    registry, errors = build_tasks_setting_registry(
        root=root,
        profile_root=root / "KnowledgeHub/.obsidian-mac",
        blueprint=blueprint,
    )

    assert errors == []
    assert registry["manifest"]["version"] is None
    assert registry["global_filter"]["state"] == "unknown"
    assert registry["javascript_boundary"]["setting"]["state"] == "unknown"
    assert all(item["state"] == "unknown" for item in registry["query_sources"])
    assert registry["evidence_boundaries"]["task_completion"] == "not_run"


def test_p05_registry_rejects_active_javascript_query_and_unbounded_function_fixture(tmp_path: Path) -> None:
    root = tmp_path / "control"
    dashboard_root = root / "KnowledgeHub/99_System/Dashboards"
    plugin_root = root / "KnowledgeHub/.obsidian-mac/plugins/obsidian-tasks-plugin"
    dashboard_root.mkdir(parents=True)
    plugin_root.mkdir(parents=True)
    (plugin_root / "manifest.json").write_text(
        json.dumps({"id": "obsidian-tasks-plugin", "version": "8.4.0"}),
        encoding="utf-8",
    )
    (plugin_root / "data.json").write_text(
        json.dumps(
            {
                "globalQuery": "filter by function task.file.folder === query.file.folder",
                "globalFilter": "#task",
                "removeGlobalFilter": False,
            }
        ),
        encoding="utf-8",
    )
    (dashboard_root / "Tasks.md").write_text(
        "# Tasks\n\n```tasks\nfilter by function task.file.folder === query.file.folder\n```\n\n- Core-only fallback: Search\n",
        encoding="utf-8",
    )
    blueprint = load_yaml_file(CONTROL_ROOT / "blueprint/blueprint.yaml")

    _, errors = build_tasks_setting_registry(
        root=root,
        profile_root=root / "KnowledgeHub/.obsidian-mac",
        blueprint=blueprint,
    )

    codes = {error["code"] for error in errors}
    assert "P05_JAVASCRIPT_QUERY_ACTIVE" in codes


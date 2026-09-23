from __future__ import annotations

import json
from pathlib import Path

from vaultops.breadcrumbs_settings import build_breadcrumbs_setting_registry
from vaultops.diagnostics import plugins_audit_report
from vaultops.yaml_safe import load_yaml_file

CONTROL_ROOT = Path(__file__).resolve().parents[2]


def test_p09_registry_binds_blueprint_typed_edges_and_read_only_views_without_mutation() -> None:
    data_path = CONTROL_ROOT / "KnowledgeHub/.obsidian-mac/plugins/breadcrumbs/data.json"
    before = data_path.read_bytes()

    report, exit_code = plugins_audit_report(CONTROL_ROOT)

    assert exit_code == 0, report
    registry = report["setting_registry"]["breadcrumbs_setting_registry"]
    assert registry["schema_version"] == 1
    assert registry["plugin"] == "breadcrumbs"
    assert registry["manifest"] == {
        "source": "KnowledgeHub/.obsidian-mac/plugins/breadcrumbs/manifest.json",
        "id": "breadcrumbs",
        "version": "4.21.12",
    }
    relation = registry["relation_registry"]
    assert relation["allowed_fields"] == [
        "projects",
        "sources",
        "related",
        "supports",
        "contradicts",
        "explains",
        "applies_to",
        "derived_from",
        "implements",
        "raises",
    ]
    assert relation["available_context_fields_not_approved"] == ["areas", "topics", "people"]
    assert relation["inverse_materialization"] is False
    assert relation["unknown_predicate_policy"] == "propose_related_or_reject"
    assert registry["property_dictionary"]["state"] == "pass"
    assert registry["property_dictionary"]["missing_fields"] == []

    assert registry["edge_fields"] == {
        "observed": relation["allowed_fields"],
        "expected": relation["allowed_fields"],
        "state": "pass",
        "groups": {
            "observed": {
                "Context relation group": ["projects", "sources", "related"],
                "Semantic relation group": [
                    "supports",
                    "contradicts",
                    "explains",
                    "applies_to",
                    "derived_from",
                    "implements",
                    "raises",
                ],
            },
            "expected": {
                "Context relation group": ["projects", "sources", "related"],
                "Semantic relation group": [
                    "supports",
                    "contradicts",
                    "explains",
                    "applies_to",
                    "derived_from",
                    "implements",
                    "raises",
                ],
            },
            "state": "pass",
        },
        "ownership": {
            "context": ["projects", "sources", "related"],
            "semantic": [
                "supports",
                "contradicts",
                "explains",
                "applies_to",
                "derived_from",
                "implements",
                "raises",
            ],
            "source": "blueprint/blueprint.yaml#/relation_registry",
        },
    }
    assert registry["explicit_edge_sources"]["state"] == "pass"
    assert registry["explicit_edge_sources"]["unsafe_sources"] == []
    assert all(record["state"] == "unconfigured" for record in registry["explicit_edge_sources"]["sources"].values())

    assert registry["implied_relations"] == {
        "transitive": [],
        "state": "disabled",
        "inverse_materialization": False,
        "inverse_policy": "Blueprint inverse aliases are display/proposal vocabulary and are not materialized automatically",
    }
    assert registry["views"]["state"] == "pass"
    assert registry["views"]["page_trail"]["field_group_labels"] == ["Context relation group"]
    assert registry["views"]["page_trail"]["navigation_only"] is True
    assert registry["views"]["side_matrix"]["derived_groups_not_canonical"] == ["sames", "nexts", "prevs"]
    assert registry["views"]["side_tree"]["derived_groups_not_canonical"] == ["ups", "downs"]
    assert registry["views"]["canonical_frontmatter_write"] == "forbidden_from_view_rendering"

    assert registry["commands"]["state"] == "pass"
    assert registry["commands"]["write_capable_commands"] == ["create_canvas", "freeze_implied_edges", "thread"]
    assert all(record["human_action_required"] for record in registry["commands"]["commands"].values())
    assert registry["plugin_state"] == {
        "is_dirty": False,
        "self_is_sibling": [],
        "exclude_folders": [],
        "suggestors_edge_field": {"enabled": False, "trigger": "."},
    }
    assert registry["fallback"] == {
        "backlinks": "Core Backlinks",
        "outgoing_links": "Core Outgoing links",
        "ordinary_markdown_wikilinks": "canonical frontmatter and wikilink edits under human review",
        "plugin_free": True,
    }
    assert registry["evidence_boundaries"] == {
        "static": "observed",
        "semantic": "observed",
        "relation_rendering": "not_run",
        "relation_edit": "not_run",
        "runtime": "not_run",
        "device": "not_run",
        "plugin_data_mutation": "out_of_scope",
        "canonical_note_mutation": "out_of_scope",
    }
    assert data_path.read_bytes() == before


def test_p09_registry_keeps_missing_profile_and_relation_sources_unknown(tmp_path: Path) -> None:
    root = tmp_path / "control"
    root.mkdir()
    blueprint = load_yaml_file(CONTROL_ROOT / "blueprint/blueprint.yaml")

    registry, errors = build_breadcrumbs_setting_registry(
        root=root,
        profile_root=root / "KnowledgeHub/.obsidian-mac",
        blueprint=blueprint,
    )

    assert errors == []
    assert registry["manifest"]["version"] is None
    assert registry["edge_fields"]["state"] == "unknown"
    assert registry["explicit_edge_sources"]["state"] == "unknown"
    assert registry["views"]["state"] == "unknown"
    assert registry["property_dictionary"]["state"] == "unknown"
    assert registry["evidence_boundaries"]["runtime"] == "not_run"


def test_p09_registry_rejects_unapproved_edge_fields_sources_transitive_edges_and_automatic_triggers(tmp_path: Path) -> None:
    root = tmp_path / "control"
    plugin_root = root / "KnowledgeHub/.obsidian-mac/plugins/breadcrumbs"
    plugin_root.mkdir(parents=True)
    (plugin_root / "manifest.json").write_text(
        json.dumps({"id": "breadcrumbs", "version": "4.21.12"}),
        encoding="utf-8",
    )
    (plugin_root / "data.json").write_text(
        json.dumps(
            {
                "edge_fields": [{"label": "unapproved"}],
                "edge_field_groups": [],
                "implied_relations": {"transitive": [{"name": "unsafe", "rounds": 1}]},
                "explicit_edge_sources": {"dataview_note": {"default_field": "unsafe"}},
                "suggestors": {"edge_field": {"enabled": True}},
                "commands": {"rebuild_graph": {"trigger": {"note_save": True, "layout_change": False}}},
            }
        ),
        encoding="utf-8",
    )
    blueprint = load_yaml_file(CONTROL_ROOT / "blueprint/blueprint.yaml")

    _, errors = build_breadcrumbs_setting_registry(
        root=root,
        profile_root=root / "KnowledgeHub/.obsidian-mac",
        blueprint=blueprint,
    )

    codes = {error["code"] for error in errors}
    assert "P09_EDGE_FIELD_REGISTRY_DRIFT" in codes
    assert "P09_EDGE_FIELD_GROUP_DRIFT" in codes
    assert "P09_TRANSITIVE_RELATION_NOT_ACCEPTED" in codes
    assert "P09_EXTERNAL_EDGE_SOURCE_NOT_ACCEPTED" in codes
    assert "P09_AUTOMATIC_FIELD_SUGGESTOR_ENABLED" in codes
    assert "P09_AUTOMATIC_GRAPH_REBUILD" in codes

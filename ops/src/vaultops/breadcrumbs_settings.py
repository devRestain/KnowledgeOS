"""Read-only P09 Breadcrumbs typed-relation contract inspection.

Breadcrumbs is treated as a navigation and display adapter.  The Blueprint
relation registry owns canonical edge names and object-type rules; the plugin
does not become an authority for frontmatter, inverse materialization, or
automatic relation generation.  Write-capable commands are recorded as
explicit human actions and remain outside this planning slice.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

P09_BREADCRUMBS_REGISTRY_SCHEMA_VERSION = 1
P09_PLUGIN_ID = "breadcrumbs"
P09_APPROVED_CONTEXT_FIELDS = ("projects", "sources", "related")
P09_APPROVED_SEMANTIC_FIELDS = (
    "supports",
    "contradicts",
    "explains",
    "applies_to",
    "derived_from",
    "implements",
    "raises",
)


def _strict_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _read_json(path: Path) -> tuple[Any | None, str | None]:
    if path.is_symlink() or not path.is_file():
        return None, "missing"
    try:
        return json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=_strict_pairs), None
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as error:
        return None, str(error)


def _read_text(path: Path) -> tuple[str | None, str | None]:
    if path.is_symlink() or not path.is_file():
        return None, "missing"
    try:
        return path.read_text(encoding="utf-8"), None
    except (OSError, UnicodeError) as error:
        return None, str(error)


def _relative(root: Path, path: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.name


def _error(code: str, locator: str, message: str) -> dict[str, str]:
    return {"code": code, "locator": locator, "message": message, "severity": "error"}


def _state(value: Any, *, expected: Any = None, compare: bool = False) -> dict[str, Any]:
    if value is None:
        return {"observed": None, "state": "unknown"}
    if compare:
        return {"observed": value, "expected": expected, "state": "pass" if value == expected else "drift"}
    return {"observed": value, "state": "observed"}


def _unconfigured_materialization(value: Any) -> bool:
    return value is None or value is False or (
        isinstance(value, (str, list, dict)) and not value
    )


def _has_enabled_canonical_write(value: Any) -> bool:
    forbidden = {
        "apply_canonical",
        "apply_to_frontmatter",
        "auto_apply",
        "automatic_write",
        "canonical_write",
        "materialize_inverse",
        "materialize_transitive",
        "overwrite_canonical",
        "persist_canonical",
        "write_frontmatter",
        "write_to_frontmatter",
    }
    if isinstance(value, dict):
        return any(
            (key.casefold() in forbidden and child is True)
            or _has_enabled_canonical_write(child)
            for key, child in value.items()
            if isinstance(key, str)
        )
    if isinstance(value, list):
        return any(_has_enabled_canonical_write(child) for child in value)
    return False


def _relation_contract(blueprint: dict[str, Any], errors: list[dict[str, str]]) -> dict[str, Any]:
    relation = blueprint.get("relation_registry")
    relation = relation if isinstance(relation, dict) else {}
    canonical = relation.get("canonical_predicates")
    canonical = canonical if isinstance(canonical, dict) else {}
    context = relation.get("context_predicates")
    context = context if isinstance(context, dict) else {}
    inverse_aliases = relation.get("inverse_aliases")
    inverse_aliases = inverse_aliases if isinstance(inverse_aliases, dict) else {}
    context_fields = [field for field in P09_APPROVED_CONTEXT_FIELDS if field in context]
    missing_context_fields = [field for field in P09_APPROVED_CONTEXT_FIELDS if field not in context]
    if missing_context_fields:
        errors.append(
            _error(
                "P09_BLUEPRINT_CONTEXT_FIELD_MISSING",
                "blueprint/blueprint.yaml#/relation_registry/context_predicates",
                "the Blueprint must retain the KnowledgeOS-owned context relation subset",
            )
        )
    semantic_fields = [field for field in P09_APPROVED_SEMANTIC_FIELDS if field in canonical]
    missing_semantic_fields = [field for field in P09_APPROVED_SEMANTIC_FIELDS if field not in canonical]
    if missing_semantic_fields:
        errors.append(
            _error(
                "P09_BLUEPRINT_SEMANTIC_FIELD_MISSING",
                "blueprint/blueprint.yaml#/relation_registry/canonical_predicates",
                "the Blueprint must retain the KnowledgeOS-owned semantic relation subset",
            )
        )
    allowed_fields = context_fields + semantic_fields
    return {
        "allowed_fields": allowed_fields,
        "context_fields": context_fields,
        "missing_context_fields": missing_context_fields,
        "semantic_fields": semantic_fields,
        "missing_semantic_fields": missing_semantic_fields,
        "canonical_predicates": canonical,
        "context_predicates": context,
        "available_context_fields_not_approved": [field for field in context if field not in context_fields],
        "available_semantic_fields_not_approved": [field for field in canonical if field not in semantic_fields],
        "inverse_aliases": inverse_aliases,
        "unknown_predicate_policy": relation.get("unknown_predicate_policy"),
        "inverse_materialization": relation.get("inverse_materialization"),
        "edge_kinds": relation.get("edge_kinds"),
        "canonical_source_locator": relation.get("canonical_source_locator"),
        "canonical_object_locator": relation.get("canonical_object_locator"),
        "source": "blueprint/blueprint.yaml#/relation_registry",
    }


def _property_dictionary_contract(root: Path, allowed_fields: list[str]) -> dict[str, Any]:
    path = root / "KnowledgeHub/99_System/Schemas/Property_Dictionary.md"
    text, error = _read_text(path)
    if text is None:
        return {"state": "unknown", "path": _relative(root, path), "missing": error == "missing", "fields": []}
    observed = [field for field in allowed_fields if f"`{field}`" in text]
    return {
        "state": "pass" if len(observed) == len(allowed_fields) else "drift",
        "path": _relative(root, path),
        "fields": observed,
        "missing_fields": [field for field in allowed_fields if field not in observed],
        "source": "generated C06 Property Dictionary",
    }


def _edge_fields(
    data: dict[str, Any],
    allowed_fields: list[str],
    context_fields: list[str],
    semantic_fields: list[str],
    data_path: Path,
    root: Path,
    errors: list[dict[str, str]],
) -> dict[str, Any]:
    raw_fields = data.get("edge_fields")
    if raw_fields is None:
        observed = None
        state = "unknown"
    elif not isinstance(raw_fields, list):
        observed = None
        state = "blocked"
        errors.append(
            _error(
                "P09_EDGE_FIELD_REGISTRY_INVALID",
                _relative(root, data_path) + "#/edge_fields",
                "Breadcrumbs edge fields must be a list",
            )
        )
    else:
        observed = [
            item["label"]
            for item in raw_fields
            if isinstance(item, dict) and isinstance(item.get("label"), str)
        ]
        missing = [field for field in allowed_fields if field not in observed]
        duplicate_owned = [field for field in allowed_fields if observed.count(field) > 1]
        state = "pass" if not missing and not duplicate_owned else "drift"
        if state == "drift":
            errors.append(
                _error(
                    "P09_EDGE_FIELD_REGISTRY_DRIFT",
                    _relative(root, data_path) + "#/edge_fields",
                    "Breadcrumbs must retain each Blueprint-owned relation field exactly once",
                )
            )
    groups = data.get("edge_field_groups")
    group_records = groups if isinstance(groups, list) else []
    observed_groups: dict[str, Any] = {}
    for item in group_records:
        if not isinstance(item, dict) or not isinstance(item.get("label"), str):
            continue
        label = item["label"]
        fields = item.get("fields")
        if label not in observed_groups:
            observed_groups[label] = fields
    expected_groups = {
        "Context relation group": context_fields,
        "Semantic relation group": semantic_fields,
    }
    if groups is None:
        group_state = "unknown"
        duplicate_owned_groups: list[str] = []
    elif not isinstance(groups, list):
        group_state = "blocked"
        duplicate_owned_groups = []
        errors.append(
            _error(
                "P09_EDGE_FIELD_GROUPS_INVALID",
                _relative(root, data_path) + "#/edge_field_groups",
                "Breadcrumbs edge field groups must be a list",
            )
        )
    else:
        duplicate_owned_groups = [
            label
            for label in expected_groups
            if sum(
                isinstance(item, dict) and item.get("label") == label
                for item in group_records
            )
            > 1
        ]
        missing_or_incomplete = []
        for label, required_fields in expected_groups.items():
            observed_fields = observed_groups.get(label)
            if (
                not isinstance(observed_fields, list)
                or not all(isinstance(field, str) for field in observed_fields)
                or not set(required_fields).issubset(observed_fields)
                or label in duplicate_owned_groups
            ):
                missing_or_incomplete.append(label)
        group_state = "pass" if not missing_or_incomplete else "drift"
    if group_state in {"drift", "blocked"}:
        errors.append(
            _error(
                "P09_EDGE_FIELD_GROUP_DRIFT",
                _relative(root, data_path) + "#/edge_field_groups",
                "Breadcrumbs edge field groups must retain the Blueprint-owned relation subset",
            )
        )
    return {
        "observed": observed,
        "expected": allowed_fields,
        "state": state,
        "additional_fields": sorted(set(observed or []) - set(allowed_fields)),
        "groups": {
            "observed": observed_groups,
            "expected": expected_groups,
            "additional_groups": sorted(set(observed_groups) - set(expected_groups)),
            "state": group_state,
        },
        "ownership": {
            "context": context_fields,
            "semantic": semantic_fields,
            "source": "blueprint/blueprint.yaml#/relation_registry",
        },
    }


def _source_policy(data: dict[str, Any], data_path: Path, root: Path, errors: list[dict[str, str]]) -> dict[str, Any]:
    raw = data.get("explicit_edge_sources")
    if raw is None:
        return {"state": "unknown", "sources": {}}
    if not isinstance(raw, dict):
        errors.append(_error("P09_EDGE_SOURCES_INVALID", _relative(root, data_path) + "#/explicit_edge_sources", "explicit edge sources must be an object"))
        return {"state": "blocked", "sources": {}}
    sources: dict[str, Any] = {}
    unsafe: list[str] = []
    for name, value in raw.items():
        if not isinstance(value, dict):
            sources[name] = {"observed": value, "state": "unknown"}
            continue
        enabled = value.get("enabled")
        nonempty = any(item not in (None, "", [], {}) for key, item in value.items() if key not in {"enabled"})
        active = enabled is not False and (enabled is True or nonempty)
        sources[name] = {
            "enabled": enabled,
            "observed_keys": sorted(value),
            "state": "unconfigured" if not active else "configured_unaccepted",
            "policy": "no external or automatic edge source",
        }
        if active:
            unsafe.append(name)
    if unsafe:
        errors.append(_error("P09_EXTERNAL_EDGE_SOURCE_NOT_ACCEPTED", _relative(root, data_path) + "#/explicit_edge_sources", "external or automatic Breadcrumbs edge sources must remain empty or disabled"))
    return {"state": "pass" if not unsafe else "blocked", "sources": sources, "unsafe_sources": unsafe}


def _view_policy(data: dict[str, Any], data_path: Path, root: Path, errors: list[dict[str, str]]) -> dict[str, Any]:
    views = data.get("views")
    if not isinstance(views, dict):
        return {"state": "unknown", "views": {}}
    page = views.get("page") if isinstance(views.get("page"), dict) else {}
    side = views.get("side") if isinstance(views.get("side"), dict) else {}
    trail = page.get("trail") if isinstance(page.get("trail"), dict) else {}
    prev_next = page.get("prev_next") if isinstance(page.get("prev_next"), dict) else {}
    matrix = side.get("matrix") if isinstance(side.get("matrix"), dict) else {}
    tree = side.get("tree") if isinstance(side.get("tree"), dict) else {}
    trail_groups = trail.get("field_group_labels")
    expected_trail_groups = ["Context relation group"]
    trail_state = _required_group_state(trail_groups, expected_trail_groups)
    if trail_state in {"drift", "invalid"}:
        errors.append(_error("P09_TRAIL_GROUP_DRIFT", _relative(root, data_path) + "#/views/page/trail/field_group_labels", "Breadcrumbs trail must display the reviewed context relation group"))
    matrix_groups = matrix.get("field_group_labels")
    expected_matrix_groups = ["Context relation group", "Semantic relation group", "sames", "nexts", "prevs"]
    matrix_state = _required_group_state(matrix_groups, expected_matrix_groups)
    if matrix_state in {"drift", "invalid"}:
        errors.append(_error("P09_MATRIX_GROUP_DRIFT", _relative(root, data_path) + "#/views/side/matrix/field_group_labels", "Breadcrumbs matrix view groups must remain explicit and bounded"))
    tree_groups = tree.get("field_group_labels")
    expected_tree_groups = ["ups", "downs"]
    tree_state = _required_group_state(tree_groups, expected_tree_groups)
    if tree_state in {"drift", "invalid"}:
        errors.append(_error("P09_TREE_GROUP_DRIFT", _relative(root, data_path) + "#/views/side/tree/field_group_labels", "Breadcrumbs tree view groups must remain explicit and bounded"))
    return {
        "state": "pass" if all(state == "pass" for state in (trail_state, matrix_state, tree_state)) else "unknown" if all(state == "unknown" for state in (trail_state, matrix_state, tree_state)) else "blocked",
        "page_trail": {
            "enabled": trail.get("enabled"),
            "default_depth": trail.get("default_depth"),
            "field_group_labels": trail_groups,
            "state": trail_state,
            "navigation_only": True,
        },
        "page_prev_next": {
            "enabled": prev_next.get("enabled"),
            "field_group_labels": prev_next.get("field_group_labels"),
            "state": "observed" if prev_next else "unknown",
            "navigation_only": True,
        },
        "side_matrix": {
            "field_group_labels": matrix_groups,
            "show_attributes": matrix.get("show_attributes"),
            "state": matrix_state,
            "derived_groups_not_canonical": ["sames", "nexts", "prevs"],
            "navigation_only": True,
        },
        "side_tree": {
            "field_group_labels": tree_groups,
            "default_depth": tree.get("default_depth"),
            "state": tree_state,
            "derived_groups_not_canonical": ["ups", "downs"],
            "navigation_only": True,
        },
        "canonical_frontmatter_write": "forbidden_from_view_rendering",
    }


def _required_group_state(observed: Any, required: list[str]) -> str:
    if observed is None:
        return "unknown"
    if not isinstance(observed, list) or not all(isinstance(item, str) for item in observed):
        return "invalid"
    return "pass" if set(required).issubset(observed) else "drift"


def _command_policy(data: dict[str, Any], data_path: Path, root: Path, errors: list[dict[str, str]]) -> dict[str, Any]:
    commands = data.get("commands")
    if commands is None:
        return {"state": "unknown", "commands": {}}
    if not isinstance(commands, dict):
        errors.append(_error("P09_COMMANDS_INVALID", _relative(root, data_path) + "#/commands", "Breadcrumbs commands must be an object"))
        return {"state": "blocked", "commands": {}}
    records: dict[str, Any] = {}
    write_capable = {"freeze_implied_edges", "thread", "create_canvas"}
    automatic_triggers = ("note_save", "layout_change", "file_change", "vault_open", "startup", "on_save")
    forbidden_automatic_flags = (
        "automatic",
        "auto_run",
        "run_on_load",
        "apply_automatically",
        "write_on_save",
        "materialize_automatically",
    )
    for name, value in commands.items():
        records[name] = {
            "observed": value,
            "state": "manual_only_unapproved" if name in write_capable else "manual_only",
            "human_action_required": True,
            "runtime_evidence": "not_run",
            "mutation_class": "canonical_frontmatter_or_note_create" if name in write_capable else "local_report_or_display",
            "rollback": "restore original bytes or remove create-only report after human review" if name in write_capable else "no canonical mutation claimed",
        }
        if isinstance(value, dict):
            trigger = value.get("trigger")
            automatic = isinstance(trigger, dict) and any(trigger.get(key) is True for key in automatic_triggers)
            automatic = automatic or any(value.get(key) is True for key in forbidden_automatic_flags)
            if automatic and (name == "rebuild_graph" or name in write_capable):
                code = "P09_AUTOMATIC_GRAPH_REBUILD" if name == "rebuild_graph" else "P09_AUTOMATIC_COMMAND_NOT_ACCEPTED"
                errors.append(
                    _error(
                        code,
                        _relative(root, data_path) + f"#/commands/{name}",
                        "Breadcrumbs commands that rebuild or write relations must remain manual",
                    )
                )
    return {
        "state": "pass" if not errors else "blocked",
        "commands": records,
        "write_capable_commands": sorted(write_capable.intersection(commands)),
        "policy": "no relation rewrite or canonical apply without explicit human review",
    }


def build_breadcrumbs_setting_registry(
    *,
    root: Path,
    profile_root: Path,
    blueprint: dict[str, Any],
) -> tuple[dict[str, Any], list[dict[str, str]]]:
    """Build the P09 registry from the installed Breadcrumbs profile."""

    errors: list[dict[str, str]] = []
    manifest_path = profile_root / "plugins/breadcrumbs/manifest.json"
    data_path = profile_root / "plugins/breadcrumbs/data.json"
    manifest, manifest_error = _read_json(manifest_path)
    data, data_error = _read_json(data_path)
    if manifest_error not in (None, "missing"):
        errors.append(_error("P09_BREADCRUMBS_MANIFEST_INVALID", _relative(root, manifest_path), manifest_error))
    if data_error not in (None, "missing"):
        errors.append(_error("P09_BREADCRUMBS_DATA_INVALID", _relative(root, data_path), data_error))
    if manifest is not None and not isinstance(manifest, dict):
        errors.append(_error("P09_BREADCRUMBS_MANIFEST_ROOT_INVALID", _relative(root, manifest_path), "Breadcrumbs manifest root must be an object"))
        manifest = None
    if data is not None and not isinstance(data, dict):
        errors.append(_error("P09_BREADCRUMBS_DATA_ROOT_INVALID", _relative(root, data_path), "Breadcrumbs data root must be an object"))
        data = None
    if isinstance(manifest, dict) and manifest.get("id") not in (None, P09_PLUGIN_ID):
        errors.append(_error("P09_BREADCRUMBS_MANIFEST_ID_MISMATCH", _relative(root, manifest_path), "Breadcrumbs manifest id does not match the installed plugin id"))

    serialized = data if isinstance(data, dict) else {}
    relation = _relation_contract(blueprint, errors)
    property_dictionary = _property_dictionary_contract(root, relation["allowed_fields"])
    edge_fields = _edge_fields(
        serialized,
        relation["allowed_fields"],
        relation["context_fields"],
        relation["semantic_fields"],
        data_path,
        root,
        errors,
    )
    source_policy = _source_policy(serialized, data_path, root, errors)
    view_policy = _view_policy(serialized, data_path, root, errors)
    command_policy = _command_policy(serialized, data_path, root, errors)
    if _has_enabled_canonical_write(serialized.get("commands")) or _has_enabled_canonical_write(
        serialized.get("views")
    ):
        errors.append(
            _error(
                "P09_CANONICAL_WRITE_NOT_ACCEPTED",
                _relative(root, data_path),
                "Breadcrumbs views and commands cannot write canonical frontmatter automatically",
            )
        )
    implied = serialized.get("implied_relations")
    transitive = implied.get("transitive") if isinstance(implied, dict) else None
    inverse_materialization = relation.get("inverse_materialization")
    if not _unconfigured_materialization(transitive):
        errors.append(_error("P09_TRANSITIVE_RELATION_NOT_ACCEPTED", _relative(root, data_path) + "#/implied_relations/transitive", "Breadcrumbs transitive implied relations must remain empty"))
    inverse_relations = implied.get("inverse") if isinstance(implied, dict) else None
    serialized_inverse_materialization = implied.get("inverse_materialization") if isinstance(implied, dict) else None
    if not _unconfigured_materialization(inverse_relations) or not _unconfigured_materialization(serialized_inverse_materialization):
        errors.append(
            _error(
                "P09_INVERSE_RELATION_NOT_ACCEPTED",
                _relative(root, data_path) + "#/implied_relations",
                "Breadcrumbs inverse relations must never be materialized automatically",
            )
        )
    if inverse_materialization not in (None, False):
        errors.append(
            _error(
                "P09_BLUEPRINT_INVERSE_MATERIALIZATION_NOT_ACCEPTED",
                "blueprint/blueprint.yaml#/relation_registry/inverse_materialization",
                "Blueprint inverse aliases must remain non-materialized",
            )
        )
    materialization_disabled = (
        inverse_materialization is False
        and _unconfigured_materialization(transitive)
        and _unconfigured_materialization(inverse_relations)
        and _unconfigured_materialization(serialized_inverse_materialization)
    )
    suggestors = serialized.get("suggestors")
    edge_suggestor = suggestors.get("edge_field") if isinstance(suggestors, dict) else None
    if isinstance(edge_suggestor, dict) and edge_suggestor.get("enabled") is True:
        errors.append(_error("P09_AUTOMATIC_FIELD_SUGGESTOR_ENABLED", _relative(root, data_path) + "#/suggestors/edge_field/enabled", "automatic edge-field suggestion must remain disabled"))

    registry = {
        "schema_version": P09_BREADCRUMBS_REGISTRY_SCHEMA_VERSION,
        "plugin": P09_PLUGIN_ID,
        "profile": "mac",
        "manifest": {
            "source": _relative(root, manifest_path),
            "id": manifest.get("id") if isinstance(manifest, dict) else None,
            "version": manifest.get("version") if isinstance(manifest, dict) else None,
        },
        "serialized_source": _relative(root, data_path),
        "relation_registry": relation,
        "property_dictionary": property_dictionary,
        "edge_fields": edge_fields,
        "explicit_edge_sources": source_policy,
        "implied_relations": {
            "transitive": transitive,
            "inverse": inverse_relations,
            "state": "disabled" if materialization_disabled else "unknown",
            "inverse_materialization": inverse_materialization,
            "inverse_policy": "Blueprint inverse aliases are display/proposal vocabulary and are not materialized automatically",
        },
        "views": view_policy,
        "commands": command_policy,
        "plugin_state": {
            "is_dirty": serialized.get("is_dirty"),
            "self_is_sibling": serialized.get("self_is_sibling"),
            "exclude_folders": serialized.get("exclude_folders"),
            "suggestors_edge_field": serialized.get("suggestors", {}).get("edge_field") if isinstance(serialized.get("suggestors"), dict) else None,
        },
        "fallback": {
            "backlinks": "Core Backlinks",
            "outgoing_links": "Core Outgoing links",
            "ordinary_markdown_wikilinks": "canonical frontmatter and wikilink edits under human review",
            "plugin_free": True,
        },
        "evidence_boundaries": {
            "static": "observed" if isinstance(data, dict) else "unknown",
            "semantic": "observed" if not errors else "blocked",
            "relation_rendering": "not_run",
            "relation_edit": "not_run",
            "runtime": "not_run",
            "device": "not_run",
            "plugin_data_mutation": "out_of_scope",
            "canonical_note_mutation": "out_of_scope",
        },
    }
    return registry, errors


__all__ = [
    "P09_APPROVED_SEMANTIC_FIELDS",
    "P09_BREADCRUMBS_REGISTRY_SCHEMA_VERSION",
    "P09_PLUGIN_ID",
    "build_breadcrumbs_setting_registry",
]

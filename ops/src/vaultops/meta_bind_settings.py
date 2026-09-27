"""Read-only P12 Meta Bind low-risk property-view contract inspection.

P12 treats Meta Bind as a presentation and explicit human property-edit
surface.  The registry accepts only the four reviewed input declarations and
never treats serialized plugin settings as proof that a note was rendered or
edited.  Protected canonical system paths remain
write-forbidden even when Meta Bind's serialized folder-exclusion value is
ambiguous.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

P12_META_BIND_REGISTRY_SCHEMA_VERSION = 1
P12_PLUGIN_ID = "obsidian-meta-bind-plugin"
P12_CANONICAL_TEMPLATE_FOLDER = "99_System/Templates"
P12_PROTECTED_PATHS = (
    "99_System/Templates",
    "99_System/Bases",
    "99_System/Dashboards",
    "99_System/Schemas",
)

P12_APPROVED_INPUT_TEMPLATES = {
    "상태": {
        "field": "status",
        "control": "inlineSelect",
        "allowed_values": ["planned", "active", "blocked", "done", "cancelled"],
    },
    "우선순위": {
        "field": "priority",
        "control": "inlineSelect",
        "allowed_values": ["high", "medium", "low"],
    },
    "다음 행동": {
        "field": "next_action",
        "control": "text",
        "allowed_values": None,
    },
    "오늘의 방향": {
        "field": "today_focus",
        "control": "text",
        "allowed_values": None,
    },
}

P12_APPROVED_FIELDS = tuple(record["field"] for record in P12_APPROVED_INPUT_TEMPLATES.values())
_FIELD_OWNERS = {
    "status": "common_properties.required",
    "priority": "property_registry",
    "next_action": "property_registry",
    "today_focus": "property_registry",
}
_FIELD_NOTE_TYPE_SCOPES = {
    "status": ["project"],
    "priority": ["project", "question"],
    "next_action": ["project"],
    "today_focus": ["daily"],
}
_PROPERTY_DICTIONARY_PATH = "KnowledgeHub/99_System/Schemas/Property_Dictionary.md"
_CONTROL_MARKER = re.compile(r"\b(?:INPUT|VIEW|BUTTON)\[")
_INLINE_SELECT_RE = re.compile(
    r"^INPUT\[inlineSelect\((?P<options>.*)\):(?P<field>[A-Za-z_][A-Za-z0-9_]*)\]$"
)
_TEXT_INPUT_RE = re.compile(r"^INPUT\[text:(?P<field>[A-Za-z_][A-Za-z0-9_]*)\]$")
_INPUT_TARGET_RE = re.compile(r":(?P<field>[A-Za-z_][A-Za-z0-9_]*)\]$")


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


def _exact_setting(value: Any, expected: Any, *, policy: str) -> dict[str, Any]:
    if value is None:
        return {"observed": None, "expected": expected, "state": "unknown", "policy": policy}
    if type(value) is not type(expected):
        return {"observed": "invalid", "expected": expected, "state": "invalid", "policy": policy}
    state = "pass" if value == expected else "drift"
    return {"observed": value, "expected": expected, "state": state, "policy": policy}


def _parse_input_declaration(declaration: Any) -> dict[str, Any] | None:
    if not isinstance(declaration, str):
        return None
    inline_match = _INLINE_SELECT_RE.fullmatch(declaration)
    if inline_match:
        options: list[str] = []
        for raw_option in inline_match.group("options").split(","):
            option_match = re.fullmatch(r"\s*option\(([^()]+)\)\s*", raw_option)
            if option_match is None:
                return None
            options.append(option_match.group(1).strip())
        if not options or len(set(options)) != len(options):
            return None
        return {
            "control": "inlineSelect",
            "field": inline_match.group("field"),
            "allowed_values": options,
        }
    text_match = _TEXT_INPUT_RE.fullmatch(declaration)
    if text_match:
        return {
            "control": "text",
            "field": text_match.group("field"),
            "allowed_values": None,
        }
    return None


def _blueprint_field_source(blueprint: dict[str, Any], field: str) -> tuple[dict[str, Any] | None, str]:
    if field == "status":
        common = blueprint.get("common_properties")
        required = common.get("required") if isinstance(common, dict) else None
        source = required.get(field) if isinstance(required, dict) else None
        return source if isinstance(source, dict) else None, _FIELD_OWNERS[field]
    registry = blueprint.get("property_registry")
    source = registry.get(field) if isinstance(registry, dict) else None
    return source if isinstance(source, dict) else None, _FIELD_OWNERS[field]


def _blueprint_property_contract(
    *,
    blueprint: dict[str, Any],
    root: Path,
    errors: list[dict[str, str]],
) -> dict[str, Any]:
    del root
    records: dict[str, dict[str, Any]] = {}
    for field in P12_APPROVED_FIELDS:
        source, owner = _blueprint_field_source(blueprint, field)
        if source is None:
            records[field] = {
                "state": "unknown",
                "owner": owner,
                "observed": None,
                "source": "blueprint/blueprint.yaml",
            }
            continue
        observed = {
            "obsidian_type": source.get("obsidian_type"),
            "constraints": {
                key: value for key, value in source.items() if key != "obsidian_type"
            },
            "owner": owner,
        }
        expected = {
            "obsidian_type": "text",
            "owner": owner,
        }
        if field == "status":
            expected["constraints"] = {"enum_from": "note_types.TYPE.statuses"}
        elif field == "priority":
            expected["constraints"] = {"enum": ["high", "medium", "low"]}
        elif field == "next_action":
            expected["constraints"] = {"min_length": 1}
        else:
            expected["constraints"] = {}
        expected_constraints = expected["constraints"]
        observed_constraints = observed["constraints"]
        constraints_match = isinstance(observed_constraints, dict) and all(
            observed_constraints.get(key) == value
            for key, value in expected_constraints.items()
        )
        state = (
            "pass"
            if observed["obsidian_type"] == expected["obsidian_type"] and constraints_match
            else "drift"
        )
        records[field] = {
            "state": state,
            "owner": owner,
            "observed": observed,
            "expected": expected,
            "source": f"blueprint/blueprint.yaml#/{'common_properties/required' if field == 'status' else 'property_registry'}/{field}",
        }
        if state == "drift":
            errors.append(
                _error(
                    "P12_BLUEPRINT_PROPERTY_DRIFT",
                    records[field]["source"],
                    f"Blueprint property {field} does not satisfy the accepted P12 contract",
                )
            )

    states = [record["state"] for record in records.values()]
    state = "pass" if states and all(item == "pass" for item in states) else "unknown" if "unknown" in states else "blocked"
    return {
        "state": state,
        "fields": records,
        "source": "blueprint/blueprint.yaml#/common_properties/required and /property_registry",
    }


def _parse_property_dictionary_rows(text: str) -> dict[str, dict[str, Any]]:
    rows: dict[str, dict[str, Any]] = {}
    for line in text.splitlines():
        cells = [cell.strip().strip("`") for cell in line.split("|")]
        if len(cells) != 6 or cells[0] or cells[-1] or cells[1] in {"Property", "---", ""}:
            continue
        try:
            constraints = json.loads(cells[3])
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
        if not isinstance(constraints, dict):
            continue
        rows[cells[1]] = {
            "obsidian_type": cells[2],
            "constraints": constraints,
            "owner": cells[4],
        }
    return rows


def _property_dictionary_contract(
    *,
    root: Path,
    blueprint_contract: dict[str, Any],
    errors: list[dict[str, str]],
) -> dict[str, Any]:
    path = root / _PROPERTY_DICTIONARY_PATH
    text, read_error = _read_text(path)
    if read_error:
        return {
            "state": "unknown",
            "source": _relative(root, path),
            "fields": {field: {"state": "unknown"} for field in P12_APPROVED_FIELDS},
        }
    rows = _parse_property_dictionary_rows(text or "")
    records: dict[str, dict[str, Any]] = {}
    for field in P12_APPROVED_FIELDS:
        expected_record = blueprint_contract["fields"].get(field, {})
        expected = expected_record.get("observed")
        observed = rows.get(field)
        if expected is None or observed is None:
            state = "unknown"
        else:
            expected_constraints = expected.get("constraints", {})
            observed_constraints = observed.get("constraints", {})
            state = (
                "pass"
                if observed.get("obsidian_type") == expected.get("obsidian_type")
                and observed.get("owner") == expected.get("owner")
                and isinstance(expected_constraints, dict)
                and isinstance(observed_constraints, dict)
                and all(
                    observed_constraints.get(key) == value
                    for key, value in expected_constraints.items()
                )
                else "drift"
            )
        records[field] = {
            "state": state,
            "observed": observed,
            "expected": expected,
            "source": _relative(root, path) + f"#/{field}",
        }
        if state == "drift":
            errors.append(
                _error(
                    "P12_PROPERTY_DICTIONARY_DRIFT",
                    records[field]["source"],
                    f"generated Property Dictionary field {field} does not match the Blueprint property contract",
                )
            )
    states = [record["state"] for record in records.values()]
    state = "pass" if states and all(item == "pass" for item in states) else "unknown" if "unknown" in states else "blocked"
    return {
        "state": state,
        "source": _relative(root, path),
        "fields": records,
    }


def _note_type_scope_contract(
    *,
    blueprint: dict[str, Any],
    errors: list[dict[str, str]],
) -> dict[str, Any]:
    note_types = blueprint.get("note_types")
    note_types = note_types if isinstance(note_types, dict) else {}
    records: dict[str, dict[str, Any]] = {}
    for field in P12_APPROVED_FIELDS:
        observed: list[str] = []
        for note_type, contract in note_types.items():
            if not isinstance(contract, dict):
                continue
            statuses = contract.get("statuses")
            required = contract.get("required_extra") if isinstance(contract.get("required_extra"), list) else []
            optional = contract.get("optional_extra") if isinstance(contract.get("optional_extra"), list) else []
            if field == "status":
                expected_values = P12_APPROVED_INPUT_TEMPLATES["상태"]["allowed_values"]
                if statuses == expected_values:
                    observed.append(note_type)
            elif field in required or field in optional:
                observed.append(note_type)
        expected = _FIELD_NOTE_TYPE_SCOPES[field]
        state = "pass" if set(expected).issubset(observed) else "drift"
        records[field] = {
            "state": state,
            "observed_note_types": observed,
            "expected_note_types": list(expected),
            "source": f"blueprint/blueprint.yaml#/note_types/*/{field}",
        }
        if state == "drift":
            errors.append(
                _error(
                    "P12_NOTE_TYPE_SCOPE_DRIFT",
                    records[field]["source"],
                    f"Meta Bind field {field} does not have the accepted note-type scope",
                )
            )

    # The conditional project requirements are part of the safety boundary
    # for the next_action input and must remain explicit in the registry.
    project = note_types.get("project") if isinstance(note_types.get("project"), dict) else {}
    conditional = project.get("conditional_requirements") if isinstance(project, dict) else []
    expected_conditional = [
        {"when": {"status": "active"}, "require": ["focus_rank", "next_action"]},
        {"when": {"status": "blocked"}, "require": ["focus_rank", "next_action"]},
    ]
    conditional_state = (
        "pass"
        if isinstance(conditional, list)
        and all(item in conditional for item in expected_conditional)
        else "drift"
    )
    if conditional_state == "drift":
        errors.append(
            _error(
                "P12_NEXT_ACTION_CONDITION_DRIFT",
                "blueprint/blueprint.yaml#/note_types/project/conditional_requirements",
                "project next_action conditional requirements do not match the accepted P12 contract",
            )
        )
    records["next_action"]["conditional_requirements"] = {
        "state": conditional_state,
        "observed": conditional,
        "expected": expected_conditional,
    }
    state_values = [record["state"] for record in records.values()]
    state = "pass" if all(item == "pass" for item in state_values) else "blocked"
    return {
        "state": state,
        "fields": records,
        "source": "blueprint/blueprint.yaml#/note_types",
    }


def _input_template_contract(
    *,
    data: dict[str, Any],
    root: Path,
    data_path: Path,
    property_dictionary: dict[str, Any],
    note_type_scope: dict[str, Any],
    errors: list[dict[str, str]],
) -> dict[str, Any]:
    error_count_before = len(errors)
    raw_templates = data.get("inputFieldTemplates")
    if raw_templates is None:
        return {
            "state": "unknown",
            "observed": None,
            "records": {field: {"state": "unknown"} for field in P12_APPROVED_FIELDS},
            "source": _relative(root, data_path) + "#/inputFieldTemplates",
        }
    if not isinstance(raw_templates, list):
        errors.append(
            _error(
                "P12_INPUT_TEMPLATE_INVALID",
                _relative(root, data_path) + "#/inputFieldTemplates",
                "Meta Bind inputFieldTemplates must be a list",
            )
        )
        return {
            "state": "blocked",
            "observed": "invalid",
            "records": {},
            "source": _relative(root, data_path) + "#/inputFieldTemplates",
        }

    records: dict[str, dict[str, Any]] = {}
    additional_count = 0
    for index, item in enumerate(raw_templates):
        locator = _relative(root, data_path) + f"#/inputFieldTemplates/{index}"
        if not isinstance(item, dict) or not isinstance(item.get("name"), str):
            additional_count += 1
            continue
        name = item["name"]
        declaration = item.get("declaration")
        parsed = _parse_input_declaration(declaration)
        accepted = P12_APPROVED_INPUT_TEMPLATES.get(name)
        if accepted is None:
            additional_count += 1
            target_match = _INPUT_TARGET_RE.search(declaration) if isinstance(declaration, str) else None
            if (parsed is not None and parsed["field"] in P12_APPROVED_FIELDS) or (
                target_match is not None and target_match.group("field") in P12_APPROVED_FIELDS
            ):
                errors.append(
                    _error(
                        "P12_DUPLICATE_INPUT_FIELD",
                        locator,
                        "unowned Meta Bind inputs cannot create a second control for an owned property",
                    )
                )
            continue
        if parsed is None:
            errors.append(_error("P12_INPUT_DECLARATION_INVALID", locator + "/declaration", "KnowledgeOS-owned Meta Bind inputs must use the reviewed property syntax"))
            continue
        field = parsed["field"]
        if accepted["field"] != field:
            errors.append(_error("P12_UNAPPROVED_INPUT_FIELD", locator, f"Meta Bind input {name} targets an unapproved property"))
            continue
        expected = {
            "control": accepted["control"],
            "field": field,
            "allowed_values": accepted["allowed_values"],
        }
        if parsed != expected:
            errors.append(_error("P12_INPUT_DECLARATION_DRIFT", locator + "/declaration", f"Meta Bind input {name} does not match the approved property declaration"))
            state = "drift"
        else:
            state = "pass"
        if field in records:
            errors.append(_error("P12_DUPLICATE_INPUT_FIELD", locator, f"Meta Bind property {field} has more than one input template"))
            state = "drift"
        property_record = property_dictionary.get("fields", {}).get(field, {})
        scope_record = note_type_scope.get("fields", {}).get(field, {})
        records[field] = {
            "state": state if property_record.get("state") == "pass" and scope_record.get("state") == "pass" else "unknown" if state == "pass" else state,
            "name": name,
            "declaration": declaration,
            "control": parsed["control"],
            "property": field,
            "property_owner": _FIELD_OWNERS[field],
            "allowed_values": parsed["allowed_values"],
            "note_type_scope": scope_record.get("expected_note_types", []),
            "property_dictionary_state": property_record.get("state", "unknown"),
            "mutation": {
                "write_target": "one note YAML/frontmatter property",
                "mutation_class": "single_note_frontmatter_edit",
                "human_action_required": True,
                "canonical_apply": "forbidden_without_separate_human_workflow",
                "rollback": "restore the original frontmatter value and validate the reviewed note; remove a newly added optional field only after human review",
            },
        }
    for name, accepted in P12_APPROVED_INPUT_TEMPLATES.items():
        field = accepted["field"]
        if field not in records:
            errors.append(
                _error(
                    "P12_INPUT_TEMPLATE_MISSING",
                    _relative(root, data_path) + "#/inputFieldTemplates",
                    f"approved Meta Bind input for {field} is missing",
                )
            )
    new_errors = len(errors) > error_count_before
    state = "pass" if not new_errors and set(records) == set(P12_APPROVED_FIELDS) and all(item["state"] == "pass" for item in records.values()) else "blocked" if new_errors else "unknown"
    return {
        "state": state,
        "observed": {"owned_templates": sorted(records), "additional_count": additional_count},
        "records": records,
        "source": _relative(root, data_path) + "#/inputFieldTemplates",
    }


def _protected_path_contract(root: Path, errors: list[dict[str, str]]) -> dict[str, Any]:
    records: dict[str, dict[str, Any]] = {}
    scanned_files: list[str] = []
    for relative in P12_PROTECTED_PATHS:
        directory = root / "KnowledgeHub" / relative
        if directory.is_symlink() or not directory.is_dir():
            records[relative] = {
                "state": "unknown",
                "files": [],
                "control_hits": [],
                "source": relative,
            }
            continue
        files: list[str] = []
        hits: list[dict[str, str]] = []

        def record_walk_error(
            error: OSError,
            directory: Path = directory,
            hits: list[dict[str, str]] = hits,
        ) -> None:
            path = Path(error.filename) if error.filename else directory
            hits.append({"path": _relative(root, path), "marker": "read_error"})

        for current, directory_names, file_names in os.walk(
            directory,
            followlinks=False,
            onerror=record_walk_error,
        ):
            current_path = Path(current)
            for name in list(directory_names):
                candidate = current_path / name
                if candidate.is_symlink():
                    hits.append({"path": _relative(root, candidate), "marker": "symlink_unverified"})
                    directory_names.remove(name)
            for name in file_names:
                path = current_path / name
                if path.is_symlink():
                    hits.append({"path": _relative(root, path), "marker": "symlink_unverified"})
                    continue
                if path.suffix.casefold() != ".md":
                    continue
                relative_file = _relative(root, path)
                files.append(relative_file)
                scanned_files.append(relative_file)
                text, read_error = _read_text(path)
                if read_error:
                    hits.append({"path": relative_file, "marker": "read_error"})
                    continue
                for marker in sorted({match.group(0) for match in _CONTROL_MARKER.finditer(text or "")}):
                    hits.append({"path": relative_file, "marker": marker})
        state = "blocked" if hits else "pass"
        records[relative] = {
            "state": state,
            "files": files,
            "control_hits": hits,
            "source": relative,
        }
        if hits:
            errors.append(
                _error(
                    "P12_PROTECTED_PATH_CONTROL_EXPOSED",
                    relative,
                    "protected Meta Bind paths must not expose INPUT, VIEW, or BUTTON controls",
                )
            )
    states = [record["state"] for record in records.values()]
    state = "blocked" if "blocked" in states else "pass" if states and all(item == "pass" for item in states) else "unknown"
    return {
        "state": state,
        "paths": records,
        "scanned_files": scanned_files,
        "write_controls": "forbidden regardless of serialized excludedFolders semantics",
        "source": "blueprint/blueprint.yaml#/fixed_paths/vault and ops/policies/paths.yaml",
    }


def _exclusion_contract(
    *,
    data: dict[str, Any],
    root: Path,
    data_path: Path,
    errors: list[dict[str, str]],
) -> dict[str, Any]:
    del errors
    observed = data.get("excludedFolders")
    locator = _relative(root, data_path) + "#/excludedFolders"
    if observed is None:
        return {
            "state": "unknown",
            "observed": None,
            "serialized_key": "excludedFolders",
            "exact_ui_label": "unknown",
            "canonical_path_matches": [],
            "semantics": "unknown_from_read_only_serialized_evidence",
            "policy": "protected write controls remain forbidden",
            "source": locator,
        }
    return {
        "state": "observed",
        "observed": "configured",
        "serialized_key": "excludedFolders",
        "exact_ui_label": "unknown",
        "entry_count": len(observed) if isinstance(observed, (list, tuple, dict, set)) else None,
        "value_type": type(observed).__name__,
        "canonical_path_matches": [],
        "expected_template_path": P12_CANONICAL_TEMPLATE_FOLDER,
        "semantics": "presentation_only_unverified",
        "verification": "exact installed UI label and folder-matching behavior not_run",
        "policy": "do not infer path protection from exclusion presentation; protected writes remain forbidden",
        "source": locator,
    }


def _view_contract(data: dict[str, Any], *, root: Path, data_path: Path, errors: list[dict[str, str]]) -> dict[str, Any]:
    del errors
    raw_views = data.get("viewFieldTemplates")
    locator = _relative(root, data_path) + "#/viewFieldTemplates"
    if raw_views is None:
        return {
            "state": "unconfigured",
            "observed": [],
            "source": locator,
            "runtime_rendering": "not_run",
            "policy": "additional views are user-owned outside protected paths",
        }
    if not isinstance(raw_views, list):
        return {
            "state": "observed",
            "observed": "configured",
            "value_type": type(raw_views).__name__,
            "source": locator,
            "runtime_rendering": "not_run",
        }
    return {
        "state": "observed" if raw_views else "unconfigured",
        "observed": {"additional_count": len(raw_views)},
        "source": locator,
        "runtime_rendering": "not_run",
        "policy": "view declarations outside protected paths are user-owned presentation",
    }


def _fallback_contract(root: Path) -> dict[str, Any]:
    dictionary = root / _PROPERTY_DICTIONARY_PATH
    template = root / "KnowledgeHub/99_System/Templates/T20_Project.md"
    if dictionary.is_symlink() or template.is_symlink():
        state = "unknown"
    elif dictionary.is_file() and template.is_file():
        state = "pass"
    else:
        state = "unknown"
    return {
        "state": state,
        "plugin_free": True,
        "yaml_frontmatter": "ordinary human-reviewed YAML/frontmatter remains canonical",
        "property_dictionary": _PROPERTY_DICTIONARY_PATH,
        "note_validation": "vaultctl note validate remains independent of Meta Bind",
        "mutation": "human-reviewed single-note frontmatter edit with explicit rollback",
    }


def build_meta_bind_setting_registry(
    *,
    root: Path,
    profile_root: Path,
    blueprint: dict[str, Any],
) -> tuple[dict[str, Any], list[dict[str, str]]]:
    """Build the P12 registry from installed Meta Bind data without mutation."""

    errors: list[dict[str, str]] = []
    manifest_path = profile_root / "plugins/obsidian-meta-bind-plugin/manifest.json"
    data_path = profile_root / "plugins/obsidian-meta-bind-plugin/data.json"
    manifest, manifest_error = _read_json(manifest_path)
    data, data_error = _read_json(data_path)
    if manifest_error not in (None, "missing"):
        errors.append(_error("P12_META_BIND_MANIFEST_INVALID", _relative(root, manifest_path), manifest_error))
    if data_error not in (None, "missing"):
        errors.append(_error("P12_META_BIND_DATA_INVALID", _relative(root, data_path), data_error))
    if manifest is not None and not isinstance(manifest, dict):
        errors.append(_error("P12_META_BIND_MANIFEST_ROOT_INVALID", _relative(root, manifest_path), "Meta Bind manifest root must be an object"))
        manifest = None
    if data is not None and not isinstance(data, dict):
        errors.append(_error("P12_META_BIND_DATA_ROOT_INVALID", _relative(root, data_path), "Meta Bind data root must be an object"))
        data = None
    if isinstance(manifest, dict) and manifest.get("id") not in (None, P12_PLUGIN_ID):
        errors.append(_error("P12_META_BIND_MANIFEST_ID_MISMATCH", _relative(root, manifest_path), "Meta Bind manifest id does not match the installed plugin id"))

    serialized = data if isinstance(data, dict) else {}
    global_policy = {
        "dev_mode": _exact_setting(serialized.get("devMode"), False, policy="developer mode remains disabled"),
        "ignore_code_block_restrictions": _exact_setting(
            serialized.get("ignoreCodeBlockRestrictions"),
            False,
            policy="code-block restriction bypass remains disabled",
        ),
        "enable_js": _exact_setting(serialized.get("enableJs"), False, policy="JavaScript remains disabled"),
        "button_templates": _exact_setting(serialized.get("buttonTemplates"), [], policy="arbitrary button templates remain empty"),
    }
    for key, record in global_policy.items():
        if record["state"] in {"drift", "invalid"}:
            errors.append(_error("P12_FORBIDDEN_CAPABILITY_ENABLED", _relative(root, data_path), f"Meta Bind {key} does not satisfy the disabled-by-contract policy"))

    blueprint_contract = _blueprint_property_contract(blueprint=blueprint, root=root, errors=errors)
    property_dictionary = _property_dictionary_contract(root=root, blueprint_contract=blueprint_contract, errors=errors)
    note_type_scope = _note_type_scope_contract(blueprint=blueprint, errors=errors)
    inputs = _input_template_contract(
        data=serialized,
        root=root,
        data_path=data_path,
        property_dictionary=property_dictionary,
        note_type_scope=note_type_scope,
        errors=errors,
    )
    protected_paths = _protected_path_contract(root, errors)
    exclusion = _exclusion_contract(data=serialized, root=root, data_path=data_path, errors=errors)
    views = _view_contract(serialized, root=root, data_path=data_path, errors=errors)
    fallback = _fallback_contract(root)

    registry = {
        "schema_version": P12_META_BIND_REGISTRY_SCHEMA_VERSION,
        "plugin": P12_PLUGIN_ID,
        "profile": "mac",
        "manifest": {
            "source": _relative(root, manifest_path),
            "id": manifest.get("id") if isinstance(manifest, dict) else None,
            "version": manifest.get("version") if isinstance(manifest, dict) else None,
        },
        "serialized_source": _relative(root, data_path),
        "global_policy": {
            "state": "pass" if all(record["state"] == "pass" for record in global_policy.values()) else "unknown" if not errors else "blocked",
            "settings": global_policy,
        },
        "property_dictionary": property_dictionary,
        "note_type_scope": note_type_scope,
        "input_templates": inputs,
        "view_contract": views,
        "button_contract": {
            "state": global_policy["button_templates"]["state"],
            "templates": serialized.get("buttonTemplates"),
            "execution": "not_run",
            "policy": "button execution remains forbidden until individually reviewed",
        },
        "protected_paths": protected_paths,
        "folder_exclusion": exclusion,
        "mutation_policy": {
            "allowed_surface": "reviewed single-note property input only",
            "write_target": "one note YAML/frontmatter property",
            "mutation_class": "single_note_frontmatter_edit",
            "human_action_required": True,
            "rollback": "restore original frontmatter value and validate the reviewed note; remove a newly added optional field only after human review",
            "canonical_apply": "forbidden_without_separate_human_workflow",
            "protected_paths": "write controls forbidden regardless of excludedFolders semantics",
            "javascript": "forbidden",
            "developer_mode": "forbidden",
            "code_block_restriction_bypass": "forbidden",
            "arbitrary_buttons": "forbidden",
            "network": "forbidden",
            "shell_or_system": "forbidden",
            "ai": "forbidden",
            "git": "forbidden",
            "plugin_data_mutation": "out_of_scope",
            "canonical_note_mutation": "out_of_scope",
        },
        "fallback": fallback,
        "evidence_boundaries": {
            "static": "observed" if isinstance(data, dict) else "unknown",
            "semantic": "observed" if isinstance(data, dict) and not errors else "blocked" if errors else "unknown",
            "folder_exclusion_semantics": exclusion["state"],
            "input_rendering": "not_run",
            "input_edit": "not_run",
            "view_rendering": "not_run",
            "button_execution": "not_run",
            "runtime": "not_run",
            "device": "not_run",
            "plugin_data_mutation": "out_of_scope",
            "protected_note_mutation": "out_of_scope",
            "canonical_note_mutation": "out_of_scope",
        },
    }
    return registry, errors


__all__ = [
    "P12_APPROVED_FIELDS",
    "P12_APPROVED_INPUT_TEMPLATES",
    "P12_CANONICAL_TEMPLATE_FOLDER",
    "P12_META_BIND_REGISTRY_SCHEMA_VERSION",
    "P12_PLUGIN_ID",
    "P12_PROTECTED_PATHS",
    "build_meta_bind_setting_registry",
]

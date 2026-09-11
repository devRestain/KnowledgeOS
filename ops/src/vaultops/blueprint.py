"""Read-only validation of the canonical KnowledgeOS Blueprint.

This module keeps the JSON Schema and semantic gates distinct. The Blueprint
contains contracts which require cross-document knowledge (for example,
path/type/template and action/command relationships); those checks run only
after the JSON Schema gate passes and are reported separately.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError, ValidationError
from yaml import YAMLError

from .semantic import validate_semantic_contract
from .yaml_safe import load_yaml_file

DEFAULT_BLUEPRINT_PATH = "blueprint/blueprint.yaml"
DEFAULT_SCHEMA_PATH = "blueprint/blueprint.schema.json"
DEFAULT_MANIFEST_PATH = "blueprint/CHECKSUMS.sha256"
DEFAULT_CONTRACT_ID = "knowledgeos-blueprint-v2"
JSON_SCHEMA_DRAFT = "2020-12"

_REASON_CODES = {
    "additionalProperties": "JSON_SCHEMA_ADDITIONAL_PROPERTY",
    "contains": "JSON_SCHEMA_CONTAINS",
    "const": "JSON_SCHEMA_CONST",
    "dependentRequired": "JSON_SCHEMA_DEPENDENT_REQUIRED",
    "enum": "JSON_SCHEMA_ENUM",
    "exclusiveMaximum": "JSON_SCHEMA_EXCLUSIVE_MAXIMUM",
    "exclusiveMinimum": "JSON_SCHEMA_EXCLUSIVE_MINIMUM",
    "items": "JSON_SCHEMA_ITEMS",
    "maxItems": "JSON_SCHEMA_MAX_ITEMS",
    "maxLength": "JSON_SCHEMA_MAX_LENGTH",
    "maxProperties": "JSON_SCHEMA_MAX_PROPERTIES",
    "maximum": "JSON_SCHEMA_MAXIMUM",
    "minItems": "JSON_SCHEMA_MIN_ITEMS",
    "minLength": "JSON_SCHEMA_MIN_LENGTH",
    "minProperties": "JSON_SCHEMA_MIN_PROPERTIES",
    "minimum": "JSON_SCHEMA_MINIMUM",
    "multipleOf": "JSON_SCHEMA_MULTIPLE_OF",
    "not": "JSON_SCHEMA_NOT",
    "pattern": "JSON_SCHEMA_PATTERN",
    "patternProperties": "JSON_SCHEMA_PATTERN_PROPERTIES",
    "prefixItems": "JSON_SCHEMA_PREFIX_ITEMS",
    "propertyNames": "JSON_SCHEMA_PROPERTY_NAMES",
    "required": "JSON_SCHEMA_REQUIRED",
    "unevaluatedItems": "JSON_SCHEMA_UNEVALUATED_ITEMS",
    "unevaluatedProperties": "JSON_SCHEMA_UNEVALUATED_PROPERTIES",
    "uniqueItems": "JSON_SCHEMA_UNIQUE_ITEMS",
    "type": "JSON_SCHEMA_TYPE",
}


@dataclass(frozen=True)
class BlueprintValidation:
    """Deterministic result for one JSON Schema validation run."""

    report: dict[str, Any]

    @property
    def passed(self) -> bool:
        return self.report["status"] == "PASS"

    @property
    def exit_code(self) -> int:
        return 0 if self.passed else 1

    def as_json(self) -> str:
        """Serialize the report with stable key ordering for CLI evidence."""

        return json.dumps(self.report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def _sha256(path: Path) -> str | None:
    if not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _relative_path(root: Path, path: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def _pointer(parts: Iterable[Any]) -> str:
    encoded: list[str] = []
    for part in parts:
        value = str(part).replace("~", "~0").replace("/", "~1")
        encoded.append(value)
    return "/" + "/".join(encoded) if encoded else "/"


def _schema_pointer(parts: Iterable[Any]) -> str:
    encoded = _pointer(parts)
    return "#" if encoded == "/" else f"#{encoded}"


def _reason_code(error: ValidationError) -> str:
    keyword = str(error.validator)
    return _REASON_CODES.get(keyword, f"JSON_SCHEMA_{keyword.upper()}")


def _error_payload(
    error: ValidationError,
    *,
    locator_parts: Iterable[Any] | None = None,
    message: str | None = None,
    details: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "code": _reason_code(error),
        "keyword": str(error.validator),
        "locator": _pointer(error.absolute_path if locator_parts is None else locator_parts),
        "schema_locator": _schema_pointer(error.absolute_schema_path),
        "message": error.message if message is None else message,
    }
    if details:
        payload["details"] = dict(details)
    return payload


def _expanded_error_payloads(error: ValidationError) -> list[dict[str, Any]]:
    """Return useful leaf diagnostics without version-dependent combinator noise."""

    if error.validator in {"allOf", "anyOf", "oneOf"} and error.context:
        expanded: list[dict[str, Any]] = []
        for child in error.context:
            expanded.extend(_expanded_error_payloads(child))
        return expanded

    path = list(error.absolute_path)
    if error.validator == "required" and isinstance(error.validator_value, list):
        instance = error.instance if isinstance(error.instance, Mapping) else {}
        missing = sorted(str(item) for item in error.validator_value if item not in instance)
        return [
            _error_payload(
                error,
                locator_parts=[*path, item],
                message=f"required property {item!r} is missing",
                details={"property": item},
            )
            for item in missing
        ]

    if error.validator == "additionalProperties" and isinstance(error.instance, Mapping):
        schema_properties = (
            error.schema.get("properties", {}) if isinstance(error.schema, Mapping) else {}
        )
        allowed = schema_properties if isinstance(schema_properties, Mapping) else {}
        extras = sorted(str(item) for item in error.instance if item not in allowed)
        if extras:
            return [
                _error_payload(
                    error,
                    locator_parts=[*path, item],
                    message=f"additional property {item!r} is not allowed",
                    details={"property": item},
                )
                for item in extras
            ]

    return [_error_payload(error)]


def _deduplicate_and_sort(errors: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    unique: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    for error in errors:
        key = (
            str(error["locator"]),
            str(error["code"]),
            str(error["schema_locator"]),
            str(error["message"]),
        )
        unique[key] = error
    return [
        unique[key]
        for key in sorted(unique, key=lambda item: (item[0], item[1], item[2], item[3]))
    ]


def _manifest_report(root: Path, manifest_path: Path, paths: tuple[Path, ...]) -> dict[str, Any]:
    entries: dict[str, str] = {}
    parse_error: str | None = None
    line_number = 0
    if manifest_path.is_file():
        try:
            for line_number, line in enumerate(
                manifest_path.read_text(encoding="utf-8").splitlines(), start=1
            ):
                if not line.strip():
                    continue
                expected, relative = line.split("  ", 1)
                entries[relative] = expected
        except (OSError, UnicodeError, ValueError) as error:
            parse_error = f"line {line_number}: {error}"

    checked: list[dict[str, Any]] = []
    for path in paths:
        relative = _relative_path(root, path)
        actual = _sha256(path)
        expected = entries.get(relative)
        checked.append(
            {
                "actual_sha256": actual,
                "expected_sha256": expected,
                "matches": expected is not None and actual == expected,
                "path": relative,
            }
        )

    if parse_error:
        status = "INVALID"
    elif not manifest_path.is_file():
        status = "NOT_AVAILABLE"
    elif all(item["matches"] for item in checked):
        status = "PASS"
    else:
        status = "MISMATCH"

    report: dict[str, Any] = {
        "path": _relative_path(root, manifest_path),
        "sha256": _sha256(manifest_path),
        "status": status,
        "entries": checked,
    }
    if parse_error:
        report["error"] = parse_error
    return report


def _missing_input_error(root: Path, path: Path, code: str) -> dict[str, Any]:
    relative = _relative_path(root, path)
    return {
        "code": code,
        "keyword": "input",
        "locator": "/",
        "schema_locator": "#",
        "message": f"required input is missing: {relative}",
    }


def validate_blueprint(root: str | Path) -> BlueprintValidation:
    """Validate Blueprint YAML against its Draft 2020-12 JSON Schema.

    The function reads only the two source documents and the checksum manifest.
    It never creates directories, writes reports, or touches ``KnowledgeHub/`` or
    ``runtime/``. Source hashes and manifest comparison are provenance data;
    JSON Schema validity is reported independently so a schema pass cannot be
    mistaken for the later semantic or generated-artifact gates.
    """

    workspace = Path(root).resolve()
    blueprint_path = workspace / DEFAULT_BLUEPRINT_PATH
    schema_path = workspace / DEFAULT_SCHEMA_PATH
    manifest_path = workspace / DEFAULT_MANIFEST_PATH

    report: dict[str, Any] = {
        "status": "FAIL",
        "validation": {
            "name": "blueprint_json_schema",
            "draft": JSON_SCHEMA_DRAFT,
            "json_schema": "NOT_RUN",
            "semantic_validation": "NOT_RUN:blocked:json_schema",
            "generated_artifact_validation": "NOT_RUN:separate:vaultctl schema export --check",
        },
        "contract_id": {
            "expected": DEFAULT_CONTRACT_ID,
            "matches": False,
            "observed": None,
        },
        "provenance": {
            "blueprint": {
                "path": _relative_path(workspace, blueprint_path),
                "sha256": _sha256(blueprint_path),
            },
            "schema": {
                "path": _relative_path(workspace, schema_path),
                "sha256": _sha256(schema_path),
                "draft": JSON_SCHEMA_DRAFT,
                "source": "checked_in_canonical_schema",
            },
            "checksum_manifest": _manifest_report(
                workspace, manifest_path, (blueprint_path, schema_path)
            ),
        },
        "errors": [],
        "semantic_errors": [],
    }

    errors: list[dict[str, Any]] = []
    if not blueprint_path.is_file():
        errors.append(_missing_input_error(workspace, blueprint_path, "BLUEPRINT_INPUT_MISSING"))
    if not schema_path.is_file():
        errors.append(_missing_input_error(workspace, schema_path, "SCHEMA_INPUT_MISSING"))
    if errors:
        report["validation"]["json_schema"] = "NOT_RUN"
        report["errors"] = errors
        return BlueprintValidation(report)

    try:
        blueprint = load_yaml_file(blueprint_path)
    except (OSError, UnicodeError, ValueError, YAMLError) as error:
        errors.append(
            {
                "code": "BLUEPRINT_YAML_INVALID",
                "keyword": "yaml",
                "locator": "/",
                "schema_locator": "#",
                "message": f"safe YAML loading failed: {error}",
            }
        )
        report["validation"]["json_schema"] = "FAIL"
        report["errors"] = errors
        return BlueprintValidation(report)

    try:
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        errors.append(
            {
                "code": "SCHEMA_DOCUMENT_INVALID",
                "keyword": "json",
                "locator": "/",
                "schema_locator": "#",
                "message": f"schema JSON loading failed: {error}",
            }
        )
        report["validation"]["json_schema"] = "FAIL"
        report["errors"] = errors
        return BlueprintValidation(report)

    expected_contract_id = DEFAULT_CONTRACT_ID
    if isinstance(schema, Mapping):
        schema_properties = schema.get("properties", {})
        if isinstance(schema_properties, Mapping):
            contract_schema = schema_properties.get("contract_id", {})
            if isinstance(contract_schema, Mapping) and isinstance(contract_schema.get("const"), str):
                expected_contract_id = contract_schema["const"]
        report["provenance"]["schema"].update(
            {
                "id": schema.get("$id"),
                "declared_draft": schema.get("$schema"),
            }
        )
    report["contract_id"]["expected"] = expected_contract_id
    if isinstance(blueprint, Mapping):
        observed_contract_id = blueprint.get("contract_id")
        report["contract_id"].update(
            {
                "observed": observed_contract_id,
                "matches": observed_contract_id == expected_contract_id,
            }
        )

    try:
        Draft202012Validator.check_schema(schema)
    except SchemaError as error:
        errors.append(
            {
                "code": "SCHEMA_DEFINITION_INVALID",
                "keyword": str(error.validator),
                "locator": _pointer(error.absolute_path),
                "schema_locator": _schema_pointer(error.absolute_schema_path),
                "message": error.message,
            }
        )
        report["validation"]["json_schema"] = "FAIL"
        report["errors"] = errors
        return BlueprintValidation(report)
    except (AttributeError, TypeError) as error:
        errors.append(
            {
                "code": "SCHEMA_DEFINITION_INVALID",
                "keyword": "schema",
                "locator": "/",
                "schema_locator": "#",
                "message": str(error),
            }
        )
        report["validation"]["json_schema"] = "FAIL"
        report["errors"] = errors
        return BlueprintValidation(report)

    validator = Draft202012Validator(schema)
    for validation_error in validator.iter_errors(blueprint):
        errors.extend(_expanded_error_payloads(validation_error))

    schema_errors = _deduplicate_and_sort(errors)
    report["validation"]["json_schema"] = "PASS" if not schema_errors else "FAIL"
    report["errors"] = schema_errors
    if schema_errors:
        report["status"] = "FAIL"
        return BlueprintValidation(report)

    semantic = validate_semantic_contract(blueprint)
    report["semantic_errors"] = list(semantic.errors)
    report["validation"]["semantic_validation"] = "PASS" if semantic.passed else "FAIL"
    report["errors"] = _deduplicate_and_sort([*schema_errors, *semantic.errors])
    report["status"] = "PASS" if semantic.passed else "FAIL"
    return BlueprintValidation(report)

"""Deterministic generation and zero-diff validation for owned artifacts.

The cumulative ``portable_core`` profile now includes the S08A control-side
bridge and root-sentinel schemas.  Their first capability remains recorded as
``S08A`` so the ownership manifest preserves the implementation boundary.
Later prompts, action registries, and projection files remain visible but are
reported as ``NOT_APPLICABLE_FOR_PROFILE`` until their owner session starts.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import tempfile
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

import yaml
from yaml import YAMLError

from .blueprint import validate_blueprint
from .bridge_contract import (
    BRIDGE_REQUEST_SCHEMA_PATH,
    BRIDGE_RESPONSE_SCHEMA_PATH,
    PROTOCOL_REQUEST_SCHEMA_PATH,
    PROTOCOL_RESPONSE_SCHEMA_PATH,
    ROOT_SENTINEL_SCHEMA_PATH,
    build_bridge_request_schema,
    build_bridge_response_schema,
    build_root_sentinel_schema,
    schema_bytes,
)
from .note_engine import build_note_json_schema
from .yaml_safe import load_yaml_file

OWNERSHIP_CONTRACT_PATH = "ops/config/generated-artifacts.yaml"
CAPABILITY_PROFILE = "portable_core"
GENERATOR_ID = "vaultops.schema_export"
PROPERTY_DICTIONARY_PATH = "ops/expected/Property_Dictionary.md"
DEPLOYED_PROPERTY_DICTIONARY_PATH = "KnowledgeHub/99_System/Schemas/Property_Dictionary.md"


@dataclass(frozen=True)
class ArtifactSpec:
    """Version-controlled ownership metadata for one explicit artifact path."""

    path: str
    owner: str
    status: str
    authoritative_inputs: tuple[tuple[str, tuple[str, ...]], ...]
    generator: str
    deployed_copy: bool
    first_capability: str


def _owned(
    path: str,
    kind: str,
    selectors: tuple[str, ...],
    *,
    deployed_copy: bool = True,
    inputs_path: str = "blueprint/blueprint.yaml",
    owner: str = CAPABILITY_PROFILE,
    first_capability: str = CAPABILITY_PROFILE,
    generator_id: str = GENERATOR_ID,
) -> ArtifactSpec:
    return ArtifactSpec(
        path=path,
        owner=owner,
        status="OWNED",
        authoritative_inputs=((inputs_path, selectors),),
        generator=f"{generator_id}:{kind}",
        deployed_copy=deployed_copy,
        first_capability=first_capability,
    )


def _not_applicable(
    path: str,
    owner: str,
    selectors: tuple[str, ...],
    *,
    inputs_path: str = "blueprint/blueprint.yaml",
) -> ArtifactSpec:
    return ArtifactSpec(
        path=path,
        owner=owner,
        status="NOT_APPLICABLE_FOR_PROFILE",
        authoritative_inputs=((inputs_path, selectors),),
        generator="deferred_to_owner_session",
        deployed_copy=False,
        first_capability=owner,
    )


OWNED_ARTIFACTS = (
    _owned(
        "ops/policies/properties.yaml",
        "properties",
        ("/common_properties", "/property_registry"),
    ),
    _owned(
        "ops/policies/paths.yaml",
        "paths",
        ("/path_namespaces", "/path_match_semantics", "/fixed_paths"),
    ),
    _owned(
        "ops/policies/relations.yaml",
        "relations",
        ("/property_registry", "/relation_registry"),
    ),
    _owned("ops/policies/privacy.yaml", "privacy", ("/privacy",)),
    _owned("ops/policies/retrieval.yaml", "retrieval", ("/retrieval",)),
    _owned(
        "ops/schemas/blueprint.schema.json",
        "trusted_blueprint_schema",
        ("/",),
        inputs_path="blueprint/blueprint.schema.json",
    ),
    _owned(
        PROPERTY_DICTIONARY_PATH,
        "property_dictionary_expected",
        ("/common_properties", "/property_registry"),
        deployed_copy=False,
    ),
    _owned(
        "ops/schemas/note.schema.json",
        "note_schema",
        ("/common_properties", "/property_registry", "/note_types"),
        deployed_copy=True,
    ),
    _owned(
        DEPLOYED_PROPERTY_DICTIONARY_PATH,
        "property_dictionary_deployed",
        ("/common_properties", "/property_registry", "/note_types"),
        deployed_copy=True,
    ),
    _owned(
        BRIDGE_REQUEST_SCHEMA_PATH,
        "bridge_request_schema",
        ("/bridge",),
        deployed_copy=False,
        owner="S08A",
        first_capability="S08A",
        generator_id="vaultops.bridge_contract",
    ),
    _owned(
        BRIDGE_RESPONSE_SCHEMA_PATH,
        "bridge_response_schema",
        ("/bridge",),
        deployed_copy=False,
        owner="S08A",
        first_capability="S08A",
        generator_id="vaultops.bridge_contract",
    ),
    _owned(
        ROOT_SENTINEL_SCHEMA_PATH,
        "root_sentinel_schema",
        ("/mobile_install_gate/root_sentinel_contract",),
        deployed_copy=False,
        owner="S08A",
        first_capability="S08A",
        generator_id="vaultops.bridge_contract",
    ),
    _owned(
        PROTOCOL_REQUEST_SCHEMA_PATH,
        "bridge_request_protocol_copy",
        ("/bridge",),
        deployed_copy=True,
        owner="S08A",
        first_capability="S08A",
        generator_id="vaultops.bridge_contract",
    ),
    _owned(
        PROTOCOL_RESPONSE_SCHEMA_PATH,
        "bridge_response_protocol_copy",
        ("/bridge",),
        deployed_copy=True,
        owner="S08A",
        first_capability="S08A",
        generator_id="vaultops.bridge_contract",
    ),
)


NOT_APPLICABLE_ARTIFACTS = (
    _not_applicable(
        "ops/policies/generated-sections.yaml",
        "S04",
        ("/",),
        inputs_path="OBSIDIAN_VAULT_WHITEPAPER.md",
    ),
    _not_applicable(
        "ops/policies/redaction-patterns.yaml",
        "S15",
        ("/",),
        inputs_path="OBSIDIAN_VAULT_WHITEPAPER.md",
    ),
    _not_applicable("ops/actions/triage.json", "S15", ("/actions",)),
    _not_applicable("ops/actions/draft-note.json", "S15", ("/actions",)),
    _not_applicable("ops/actions/summarize.json", "S16B", ("/actions",)),
    _not_applicable("ops/actions/link-suggestions.json", "S16B", ("/actions",)),
    _not_applicable("ops/actions/normalize.json", "S16B", ("/actions",)),
    _not_applicable("ops/actions/answer.json", "S16B", ("/actions",)),
    _not_applicable(
        "ops/prompts/system.md", "S15", ("/",), inputs_path="OBSIDIAN_VAULT_WHITEPAPER.md"
    ),
    _not_applicable(
        "ops/prompts/triage.md", "S15", ("/",), inputs_path="OBSIDIAN_VAULT_WHITEPAPER.md"
    ),
    _not_applicable(
        "ops/prompts/draft-note.md", "S16B", ("/",), inputs_path="OBSIDIAN_VAULT_WHITEPAPER.md"
    ),
    _not_applicable(
        "ops/prompts/summarize.md", "S16B", ("/",), inputs_path="OBSIDIAN_VAULT_WHITEPAPER.md"
    ),
    _not_applicable(
        "ops/prompts/link-suggestions.md", "S16B", ("/",), inputs_path="OBSIDIAN_VAULT_WHITEPAPER.md"
    ),
    _not_applicable(
        "ops/prompts/normalize.md", "S16B", ("/",), inputs_path="OBSIDIAN_VAULT_WHITEPAPER.md"
    ),
    _not_applicable(
        "ops/prompts/answer.md", "S16B", ("/",), inputs_path="OBSIDIAN_VAULT_WHITEPAPER.md"
    ),
    _not_applicable("ops/schemas/job.schema.json", "S15", ("/llm", "/privacy")),
    _not_applicable("ops/schemas/proposal.schema.json", "S15", ("/llm", "/privacy")),
    _not_applicable("ops/schemas/receipt.schema.json", "S15", ("/llm", "/privacy")),
    _not_applicable("ops/schemas/triage-result.schema.json", "S15", ("/llm",)),
    _not_applicable("ops/schemas/note-record.schema.json", "S17", ("/projection",)),
    _not_applicable("ops/schemas/edge-record.schema.json", "S17", ("/projection",)),
    _not_applicable(
        "ops/schemas/retrieval-candidate.schema.json", "S17", ("/projection", "/retrieval")
    ),
    _not_applicable("ops/schemas/answer.schema.json", "S17", ("/projection", "/retrieval")),
)

ALL_ARTIFACTS = (*OWNED_ARTIFACTS, *NOT_APPLICABLE_ARTIFACTS)


@dataclass(frozen=True)
class SchemaExportResult:
    """Deterministic report for one export or check invocation."""

    report: dict[str, Any]

    @property
    def passed(self) -> bool:
        return self.report["status"] == "PASS"

    @property
    def exit_code(self) -> int:
        return 0 if self.passed else 1

    def as_json(self) -> str:
        return json.dumps(self.report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256(path: Path) -> str | None:
    if not path.is_file() or path.is_symlink():
        return None
    return _sha256_bytes(path.read_bytes())


def _safe_relative_path(relative: str) -> tuple[PurePosixPath | None, str | None]:
    candidate = PurePosixPath(relative)
    if (
        candidate.is_absolute()
        or not candidate.parts
        or any(part in {"", ".", ".."} for part in candidate.parts)
        or any(character in relative for character in "*?[]")
    ):
        return None, f"unsafe or wildcard artifact path: {relative}"
    return candidate, None


def _target_path(workspace: Path, relative: str) -> tuple[Path | None, str | None]:
    candidate, error = _safe_relative_path(relative)
    if error:
        return None, error
    assert candidate is not None
    path = workspace.joinpath(*candidate.parts)
    current = workspace
    for part in candidate.parts:
        current = current / part
        if current.is_symlink():
            return None, f"artifact path traverses a symlink: {relative}"
    return path, None


def _ownership_document() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "contract_id": "knowledgeos-blueprint-v2",
        "capability_profile": CAPABILITY_PROFILE,
        "generator": GENERATOR_ID,
        "artifacts": [
            {
                "path": spec.path,
                "owner": spec.owner,
                "status": spec.status,
                "authoritative_inputs": [
                    {"path": path, "selectors": list(selectors)}
                    for path, selectors in spec.authoritative_inputs
                ],
                "generator": spec.generator,
                "deployed_copy": spec.deployed_copy,
                "first_capability": spec.first_capability,
            }
            for spec in ALL_ARTIFACTS
        ],
    }


def _error(
    code: str,
    locator: str,
    message: str,
    *,
    details: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "code": code,
        "keyword": "generated_artifact",
        "locator": locator,
        "schema_locator": "#",
        "message": message,
    }
    if details:
        result["details"] = dict(details)
    return result


def _load_ownership(workspace: Path) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    path = workspace / OWNERSHIP_CONTRACT_PATH
    errors: list[dict[str, Any]] = []
    if path.is_symlink():
        errors.append(
            _error("SCHEMA_EXPORT_OWNERSHIP_SYMLINK", "/", "ownership contract is a symlink")
        )
        return None, errors
    if not path.is_file():
        errors.append(
            _error(
                "SCHEMA_EXPORT_OWNERSHIP_MISSING",
                "/",
                f"required ownership contract is missing: {OWNERSHIP_CONTRACT_PATH}",
            )
        )
        return None, errors
    try:
        value = load_yaml_file(path)
    except (OSError, UnicodeError, ValueError, YAMLError) as error:
        errors.append(
            _error("SCHEMA_EXPORT_OWNERSHIP_INVALID", "/", f"safe YAML loading failed: {error}")
        )
        return None, errors
    if value != _ownership_document():
        errors.append(
            _error(
                "SCHEMA_EXPORT_OWNERSHIP_MISMATCH",
                "/artifacts",
                "ownership contract does not match the executable artifact allowlist",
            )
        )
    return value if isinstance(value, dict) else None, errors


def _source_info(workspace: Path, spec: ArtifactSpec) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for path, selectors in spec.authoritative_inputs:
        source = workspace / path
        result.append({"path": path, "selectors": list(selectors), "sha256": _sha256(source)})
    return result


def _envelope(
    blueprint: Mapping[str, Any],
    spec: ArtifactSpec,
    artifact_kind: str,
    workspace: Path,
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "contract_id": blueprint["contract_id"],
        "capability_profile": CAPABILITY_PROFILE,
        "artifact_kind": artifact_kind,
        "authoritative_inputs": _source_info(workspace, spec),
    }


def _yaml_bytes(value: Mapping[str, Any]) -> bytes:
    return yaml.safe_dump(
        value,
        allow_unicode=True,
        default_flow_style=False,
        sort_keys=False,
        width=120,
    ).encode("utf-8")


def _property_dictionary_bytes(
    blueprint: Mapping[str, Any], spec: ArtifactSpec, workspace: Path
) -> bytes:
    common = blueprint["common_properties"]
    registry = blueprint["property_registry"]
    assert isinstance(common, Mapping)
    assert isinstance(registry, Mapping)
    required = common["required"]
    assert isinstance(required, Mapping)
    rows: dict[str, tuple[Any, str]] = {}
    for name, value in required.items():
        rows[str(name)] = (value, "common_properties.required")
    for name, value in registry.items():
        if str(name) in rows:
            rows[str(name)] = (value, "common_properties + property_registry")
        else:
            rows[str(name)] = (value, "property_registry")

    source = _source_info(workspace, spec)
    source_digest = source[0]["sha256"]
    lines = [
        "<!-- GENERATED: BEGIN knowledgeos-property-dictionary -->",
        "# Property Dictionary",
        "",
        "이 파일은 Blueprint registry에서 생성된 S04 strict note contract의 검증된 사본이다.",
        "",
        f"- contract: `{blueprint['contract_id']}`",
        f"- capability profile: `{CAPABILITY_PROFILE}`",
        f"- authoritative input: `blueprint/blueprint.yaml` (SHA-256 `{source_digest}`)",
        f"- property count: `{len(rows)}`",
        "",
        "| Property | Obsidian type | Constraints | Owner |",
        "|---|---|---|---|",
    ]
    for name in sorted(
        rows,
        key=lambda value: unicodedata.normalize("NFC", value).encode("utf-8"),
    ):
        value, owner = rows[name]
        if isinstance(value, Mapping):
            obsidian_type = str(value.get("obsidian_type", ""))
            constraints = {key: item for key, item in value.items() if key != "obsidian_type"}
            constraint_text = json.dumps(
                constraints,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
        else:
            obsidian_type = ""
            constraint_text = json.dumps(
                value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
            )
        escaped = lambda item: str(item).replace("|", "\\|").replace("\n", " ")
        lines.append(
            f"| `{escaped(name)}` | `{escaped(obsidian_type)}` | `{escaped(constraint_text)}` | `{escaped(owner)}` |"
        )
    lines.extend(["", "<!-- GENERATED: END knowledgeos-property-dictionary -->", ""])
    return "\n".join(lines).encode("utf-8")


def _generated_bytes(workspace: Path, blueprint: Mapping[str, Any], spec: ArtifactSpec) -> bytes:
    if spec.path == "ops/schemas/blueprint.schema.json":
        source = workspace / "blueprint/blueprint.schema.json"
        return source.read_bytes()
    if spec.path == BRIDGE_REQUEST_SCHEMA_PATH:
        return schema_bytes(build_bridge_request_schema(blueprint))
    if spec.path == BRIDGE_RESPONSE_SCHEMA_PATH:
        return schema_bytes(build_bridge_response_schema(blueprint))
    if spec.path == ROOT_SENTINEL_SCHEMA_PATH:
        return schema_bytes(build_root_sentinel_schema(blueprint))
    if spec.path == PROTOCOL_REQUEST_SCHEMA_PATH:
        return schema_bytes(build_bridge_request_schema(blueprint))
    if spec.path == PROTOCOL_RESPONSE_SCHEMA_PATH:
        return schema_bytes(build_bridge_response_schema(blueprint))
    if spec.path in {PROPERTY_DICTIONARY_PATH, DEPLOYED_PROPERTY_DICTIONARY_PATH}:
        return _property_dictionary_bytes(blueprint, spec, workspace)
    if spec.path == "ops/schemas/note.schema.json":
        return (
            json.dumps(
                build_note_json_schema(blueprint),
                ensure_ascii=False,
                indent=2,
                sort_keys=False,
            )
            + "\n"
        ).encode("utf-8")

    if spec.path.endswith("/properties.yaml"):
        envelope = _envelope(blueprint, spec, "property_policy", workspace)
        envelope["properties"] = {
            "common": copy.deepcopy(blueprint["common_properties"]),
            "registry": copy.deepcopy(blueprint["property_registry"]),
        }
    elif spec.path.endswith("/paths.yaml"):
        envelope = _envelope(blueprint, spec, "path_policy", workspace)
        envelope["paths"] = {
            "namespaces": copy.deepcopy(blueprint["path_namespaces"]),
            "match_semantics": blueprint["path_match_semantics"],
            "fixed": copy.deepcopy(blueprint["fixed_paths"]),
        }
    elif spec.path.endswith("/relations.yaml"):
        envelope = _envelope(blueprint, spec, "relation_policy", workspace)
        envelope["relations"] = {
            "property_registry": {
                key: copy.deepcopy(value)
                for key, value in blueprint["property_registry"].items()
                if key
                in {
                    "supports",
                    "contradicts",
                    "explains",
                    "applies_to",
                    "derived_from",
                    "implements",
                    "raises",
                }
            },
            "registry": copy.deepcopy(blueprint["relation_registry"]),
        }
    elif spec.path.endswith("/privacy.yaml"):
        envelope = _envelope(blueprint, spec, "privacy_policy", workspace)
        envelope["privacy"] = copy.deepcopy(blueprint["privacy"])
    elif spec.path.endswith("/retrieval.yaml"):
        envelope = _envelope(blueprint, spec, "retrieval_policy", workspace)
        envelope["retrieval"] = copy.deepcopy(blueprint["retrieval"])
    else:
        raise ValueError(f"no generator is registered for owned artifact: {spec.path}")
    return _yaml_bytes(envelope)


def _atomic_write(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o644)
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _artifact_report(
    workspace: Path,
    spec: ArtifactSpec,
    *,
    expected: bytes | None,
    mode: str,
    errors: list[dict[str, Any]],
) -> dict[str, Any]:
    path, path_error = _target_path(workspace, spec.path)
    item: dict[str, Any] = {
        "deployed_copy": spec.deployed_copy,
        "first_capability": spec.first_capability,
        "owner": spec.owner,
        "path": spec.path,
        "status": spec.status,
    }
    if path_error:
        errors.append(_error("SCHEMA_EXPORT_UNSAFE_PATH", f"/artifacts/{spec.path}", path_error))
        item["status"] = "FAIL"
        return item
    assert path is not None
    actual = path.read_bytes() if path.is_file() and not path.is_symlink() else None
    item["actual_sha256"] = _sha256_bytes(actual) if actual is not None else None
    if spec.status == "NOT_APPLICABLE_FOR_PROFILE":
        item["present"] = actual is not None
        return item
    if expected is None:
        item["status"] = "NOT_CHECKED"
        return item
    item["expected_sha256"] = _sha256_bytes(expected)
    if mode == "check":
        if actual is None:
            item["status"] = "MISSING"
            errors.append(
                _error(
                    "SCHEMA_EXPORT_ARTIFACT_MISSING",
                    f"/artifacts/{spec.path}",
                    f"owned artifact is missing: {spec.path}",
                )
            )
        elif actual != expected:
            item["status"] = "MISMATCH"
            errors.append(
                _error(
                    "SCHEMA_EXPORT_ARTIFACT_MISMATCH",
                    f"/artifacts/{spec.path}",
                    f"owned artifact differs from deterministic generator output: {spec.path}",
                    details={
                        "expected_sha256": _sha256_bytes(expected),
                        "actual_sha256": _sha256_bytes(actual),
                    },
                )
            )
        else:
            item["status"] = "PASS"
    else:
        item["status"] = "READY_TO_WRITE"
    return item


def export_schema_artifacts(root: str | Path, *, check: bool = False) -> SchemaExportResult:
    """Export owned artifacts or check their byte-for-byte zero-diff state."""

    workspace = Path(root).resolve()
    mode = "check" if check else "export"
    errors: list[dict[str, Any]] = []
    _ownership, ownership_errors = _load_ownership(workspace)
    errors.extend(ownership_errors)
    blueprint_result = validate_blueprint(workspace)
    if not blueprint_result.passed:
        errors.append(
            _error(
                "SCHEMA_EXPORT_BLUEPRINT_INVALID",
                "/blueprint/blueprint.yaml",
                "Blueprint JSON Schema or semantic validation failed before generation",
            )
        )

    blueprint: Mapping[str, Any] | None = None
    blueprint_path = workspace / "blueprint/blueprint.yaml"
    if blueprint_result.passed:
        try:
            loaded = load_yaml_file(blueprint_path)
            if isinstance(loaded, Mapping):
                blueprint = loaded
            else:
                errors.append(_error("SCHEMA_EXPORT_BLUEPRINT_INVALID", "/", "Blueprint root must be a mapping"))
        except (OSError, UnicodeError, ValueError, YAMLError) as error:
            errors.append(
                _error("SCHEMA_EXPORT_BLUEPRINT_INVALID", "/", f"safe YAML loading failed: {error}")
            )

    expected_bytes: dict[str, bytes] = {}
    if blueprint is not None and not ownership_errors:
        for spec in OWNED_ARTIFACTS:
            try:
                expected_bytes[spec.path] = _generated_bytes(workspace, blueprint, spec)
            except (OSError, TypeError, ValueError, KeyError) as error:
                errors.append(
                    _error(
                        "SCHEMA_EXPORT_GENERATION_FAILED",
                        f"/artifacts/{spec.path}",
                        f"deterministic generation failed: {error}",
                    )
                )

    artifact_reports: list[dict[str, Any]] = []
    for spec in OWNED_ARTIFACTS:
        artifact_reports.append(
            _artifact_report(
                workspace,
                spec,
                expected=expected_bytes.get(spec.path),
                mode=mode,
                errors=errors,
            )
        )
    for spec in NOT_APPLICABLE_ARTIFACTS:
        artifact_reports.append(
            _artifact_report(workspace, spec, expected=None, mode=mode, errors=errors)
        )

    # A deployed Vault file can be a user's artifact. Never silently replace a
    # differing file merely because the current generator owns the path.
    if not check and not errors:
        for spec in OWNED_ARTIFACTS:
            if not spec.deployed_copy or not spec.path.startswith("KnowledgeHub/"):
                continue
            path, path_error = _target_path(workspace, spec.path)
            expected = expected_bytes.get(spec.path)
            if path_error or path is None or expected is None:
                continue
            if path.is_file() and not path.is_symlink() and path.read_bytes() != expected:
                errors.append(
                    _error(
                        "SCHEMA_EXPORT_DEPLOYED_COPY_CONFLICT",
                        f"/artifacts/{spec.path}",
                        "refusing to overwrite a differing deployed Vault artifact",
                    )
                )

    if not check and not errors and blueprint is not None:
        for spec in OWNED_ARTIFACTS:
            path, path_error = _target_path(workspace, spec.path)
            if path_error or path is None:
                errors.append(
                    _error(
                        "SCHEMA_EXPORT_UNSAFE_PATH",
                        f"/artifacts/{spec.path}",
                        path_error or "invalid path",
                    )
                )
                continue
            _atomic_write(path, expected_bytes[spec.path])
        for item in artifact_reports:
            if item["status"] == "READY_TO_WRITE":
                item["status"] = "WRITTEN"
                item["actual_sha256"] = _sha256(workspace / str(item["path"]))

    if not check and errors:
        for item in artifact_reports:
            if item["status"] == "READY_TO_WRITE":
                item["status"] = "NOT_WRITTEN"

    errors.sort(key=lambda item: (str(item["locator"]), str(item["code"]), str(item["message"])))
    report = {
        "status": "PASS" if not errors else "FAIL",
        "validation": {
            "name": "generated_artifact_zero_diff",
            "mode": mode,
            "capability_profile": CAPABILITY_PROFILE,
            "ownership_contract": "PASS" if not ownership_errors else "FAIL",
            "blueprint": "PASS" if blueprint_result.passed else "FAIL",
            "future_artifacts": "NOT_APPLICABLE_FOR_PROFILE",
        },
        "ownership_contract": OWNERSHIP_CONTRACT_PATH,
        "artifacts": artifact_reports,
        "errors": errors,
    }
    return SchemaExportResult(report)

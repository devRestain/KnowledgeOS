"""Read the static Core release; Operation code owns all executable ports."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker

from .paths import resolve_beneath


class AdmissionError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def digest(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def load_json(path: Path) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise AdmissionError("BINDING_INVALID", "trusted binding must be a regular file")
    value = json.loads(path.read_bytes())
    if not isinstance(value, dict):
        raise AdmissionError("BINDING_INVALID", "trusted binding must be an object")
    return value


PIN_KEYS = ("core_version", "core_digest", "schema_version_pin", "semantic_version", "semantic_digest", "capsule_version", "resource_binding_api", "host_binding_api")


def adoption_pins(control: Path) -> dict[str, str]:
    """Read the single maintained adoption selection without changing storage."""
    binding = load_json(resolve_beneath(control, "ops/config/core-adoption.json"))
    return {key: binding[key] for key in PIN_KEYS}


# Compatibility exports derive from the same maintained selection, never a
# second version table. Per-instance verification also rechecks this binding.
PINS = adoption_pins(Path(__file__).resolve().parents[3].parent)
CORE_DIGEST = PINS["core_digest"]
SEMANTIC_DIGEST = PINS["semantic_digest"]


@dataclass(frozen=True)
class CoreContracts:
    root: Path
    binding: dict[str, Any]

    def verify(self) -> dict[str, Any]:
        if set(self.binding) != {"schema_version", "operation_id", "fabric_id", "instance_id", *PINS}:
            raise AdmissionError("BINDING_INVALID", "unknown or missing adoption fields")
        if self.binding["schema_version"] != 1 or self.binding["operation_id"] != "knowledgeos":
            raise AdmissionError("OWNER_DENIED", "adoption binding must belong to knowledgeos")
        if any(self.binding.get(key) != value for key, value in PINS.items()):
            raise AdmissionError("PIN_MISMATCH", "adoption requires the exact Core and semantic pins")
        manifest = load_json(resolve_beneath(self.root, "release/manifest.json"))
        release = {"core_release_version": manifest["core_release_version"], "files": manifest["files"]}
        if manifest["release_digest"] != CORE_DIGEST or digest(canonical(release)) != CORE_DIGEST:
            raise AdmissionError("CORE_DIGEST_MISMATCH", "Core manifest does not match the adopted release")
        for entry in manifest["files"]:
            path = resolve_beneath(self.root, entry["path"])
            if not path.is_file() or digest(path.read_bytes()) != "sha256:" + entry["sha256"]:
                raise AdmissionError("CORE_DIGEST_MISMATCH", "Core input bytes differ from the frozen manifest")
        catalog = load_json(resolve_beneath(self.root, "contracts/catalog.json"))
        if catalog["core_release_version"] != self.binding["core_version"] or catalog["schema_bundle_version"] != self.binding["schema_version_pin"]:
            raise AdmissionError("PIN_MISMATCH", "Core catalog versions differ from adoption")
        combination = {"core_release": self.binding["core_version"], "capsule_contract": self.binding["capsule_version"], "schema_bundle": self.binding["schema_version_pin"], "semantic_bundle": self.binding["semantic_version"], "python_binding": catalog["python_binding_version"], "resource_binding_api": self.binding["resource_binding_api"], "host_binding_api": self.binding["host_binding_api"]}
        if combination not in catalog["supported_combinations"]:
            raise AdmissionError("PIN_MISMATCH", "selected contract combination is unsupported")
        semantic = dict(catalog["semantic_bundle"])
        semantic.pop("digest", None)
        if digest(canonical(semantic)) != SEMANTIC_DIGEST:
            raise AdmissionError("SEMANTIC_PROFILE_UNKNOWN", "semantic definition bytes differ from adoption")
        return catalog

    def validate(self, name: str, value: dict[str, Any]) -> None:
        schema = load_json(resolve_beneath(self.root, f"schemas/{name}.schema.json"))
        errors = list(Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(value))
        if errors:
            raise AdmissionError("CONTRACT_INVALID", f"{name} does not satisfy the adopted schema")

    def manifest(self) -> dict[str, Any]:
        result = {
            "fabric_id": self.binding["fabric_id"], "operation_id": "knowledgeos",
            "instance_id": self.binding["instance_id"], "contract_version": self.binding["capsule_version"],
            "core_version": self.binding["core_version"], "core_digest": self.binding["core_digest"],
            "schema_version": self.binding["schema_version_pin"], "semantic_version": self.binding["semantic_version"],
            "semantic_digest": SEMANTIC_DIGEST, "revision": 1,
            "operation_version": "0.1.0", "accepted_contract_versions": [self.binding["capsule_version"]],
            "capabilities": ["knowledge_search", "knowledge_retrieve", "proposal_create", "proposal_inspect"],
            "logical_resource_kinds": ["vault", "artifact", "state"],
            "health_contract": "HealthReport", "graph_profile": "graphless",
            "recipient_required_term_ids": [],
        }
        self.validate("OperationManifest", result)
        return result

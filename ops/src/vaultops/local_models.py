"""C30 generated local-model configuration and read-only inspection.

The configuration selects one explicitly authorized live-default embedding
profile while keeping automatic startup, concurrent requests, and fallback
activation disabled.  Inspection remains read-only and does not discover
services, pull models, or make network requests.
"""

from __future__ import annotations

import copy
import hashlib
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import yaml

from .generation_identity import GENERATION_MODEL_TAG, generation_model_options
from .qwen_embedding_index import (
    QWEN_DOCUMENT_PROMPT_TEMPLATE,
    QWEN_MODEL_DIMENSION,
    QWEN_MODEL_TAG,
    QWEN_QUERY_PROMPT_TEMPLATE,
)
from .yaml_safe import load_yaml_file

LOCAL_MODEL_CONFIG_PATH = "ops/config/local-models.yaml"
LOCAL_MODEL_AUTHORITATIVE_SELECTORS = ("/llm",)
LOCAL_MODEL_ARTIFACT_KIND = "local_model_config"
LOCAL_MODEL_SCHEMA_VERSION = 1
QWEN_MODEL_DIGEST = "64b933495768fbd3b87c20583d379728a07471e0c66733a9df87cd1901b3c44b"

LOCAL_MODEL_DECLARATION: dict[str, Any] = {
    "schema_version": 1,
    "provider": "ollama",
    "route": "local_profile",
    "enabled_by_default": False,
    "default_embedding_profile": "embedding",
    "emergency_embedding_fallback_profile": "embedding_fallback",
    "activation_policy": {
        "selected_embedding_profile": "embedding",
        "mode": "explicit_serial",
        "one_model_loaded": True,
        "concurrent_requests": False,
        "unattended_activation": False,
        "accepted_min_free_memory_percent": 24,
    },
    "endpoint": {
        "base_url": "http://127.0.0.1:11434",
        "loopback_only": True,
        "cloud_disabled": True,
        "allow_redirects": False,
        "allow_proxy": False,
        "allow_tunnel": False,
        "auto_pull": False,
    },
    "environment": {"OLLAMA_HOST": "127.0.0.1:11434", "OLLAMA_NO_CLOUD": "1"},
    "profiles": {
        "generation": {
            "task": "generation",
            "model_tag": GENERATION_MODEL_TAG,
            "model_digest": None,
            "enabled": False,
            "options": generation_model_options(),
        },
        "embedding": {
            "task": "embedding",
            "selection": "live_default",
            "model_tag": QWEN_MODEL_TAG,
            "model_digest": QWEN_MODEL_DIGEST,
            "enabled": True,
            "options": {
                "dimension": QWEN_MODEL_DIMENSION,
                "truncate": False,
                "query_prefix": QWEN_QUERY_PROMPT_TEMPLATE,
                "document_prefix": QWEN_DOCUMENT_PROMPT_TEMPLATE,
            },
        },
        "embedding_fallback": {
            "task": "embedding",
            "selection": "emergency_resource_fallback",
            "model_tag": "embeddinggemma:300m-qat-q8_0",
            "model_digest": None,
            "enabled": False,
            "options": {
                "dimension": 768,
                "truncate": False,
                "query_prefix": "task: search result | query:",
                "document_prefix": "title: {title | none} | text:",
            },
        },
    },
    "provisioning": {
        "installation": "explicit_external_authorization",
        "model_download": "explicit_external_authorization",
        "live_service_inspection": "explicit_external_authorization",
        "full_digest_capture": "external_service_evidence",
    },
}


class LocalModelConfigError(ValueError):
    """Raised when the generated local-model contract is malformed."""


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _yaml_bytes(value: Mapping[str, Any]) -> bytes:
    return yaml.safe_dump(
        value,
        allow_unicode=True,
        default_flow_style=False,
        sort_keys=False,
        width=120,
    ).encode("utf-8")


def _mapping(value: object, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise LocalModelConfigError(f"{label} must be a mapping")
    return value


def _local_model_declaration(blueprint: Mapping[str, Any]) -> Mapping[str, Any]:
    llm = _mapping(blueprint.get("llm"), "/llm")
    declaration = _mapping(llm.get("local_models", LOCAL_MODEL_DECLARATION), "/llm/local_models")
    if declaration.get("schema_version") != LOCAL_MODEL_SCHEMA_VERSION:
        raise LocalModelConfigError("/llm/local_models/schema_version must be 1")
    if declaration.get("enabled_by_default") is not False:
        raise LocalModelConfigError("local model profiles must remain disabled by default")
    if declaration.get("default_embedding_profile") != "embedding":
        raise LocalModelConfigError("the default embedding profile must be /profiles/embedding")
    if declaration.get("emergency_embedding_fallback_profile") != "embedding_fallback":
        raise LocalModelConfigError(
            "the emergency embedding fallback must be /profiles/embedding_fallback"
        )
    activation = _mapping(declaration.get("activation_policy"), "/llm/local_models/activation_policy")
    expected_activation = {
        "selected_embedding_profile": "embedding",
        "mode": "explicit_serial",
        "one_model_loaded": True,
        "concurrent_requests": False,
        "unattended_activation": False,
        "accepted_min_free_memory_percent": 24,
    }
    if dict(activation) != expected_activation:
        raise LocalModelConfigError("local model activation policy must remain explicit serial and one-model-loaded")
    return declaration


def local_model_config_document(
    blueprint: Mapping[str, Any],
    *,
    source_info: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Build the deterministic generated C30 configuration document."""

    declaration = _local_model_declaration(blueprint)
    return {
        "schema_version": LOCAL_MODEL_SCHEMA_VERSION,
        "contract_id": blueprint["contract_id"],
        "capability_profile": "portable_core",
        "artifact_kind": LOCAL_MODEL_ARTIFACT_KIND,
        "authoritative_inputs": [copy.deepcopy(dict(item)) for item in source_info],
        "local_models": copy.deepcopy(dict(declaration)),
    }


def local_model_config_bytes(
    blueprint: Mapping[str, Any],
    *,
    source_info: Sequence[Mapping[str, Any]],
) -> bytes:
    """Serialize one generated configuration using the project YAML contract."""

    return _yaml_bytes(local_model_config_document(blueprint, source_info=source_info))


def _source_info(workspace: Path) -> list[dict[str, Any]]:
    source = workspace / "blueprint/blueprint.yaml"
    if source.is_symlink() or not source.is_file():
        raise LocalModelConfigError("blueprint/blueprint.yaml is missing or unsafe")
    return [
        {
            "path": "blueprint/blueprint.yaml",
            "selectors": list(LOCAL_MODEL_AUTHORITATIVE_SELECTORS),
            "sha256": _sha256_bytes(source.read_bytes()),
        }
    ]


def _expected_bytes(workspace: Path) -> bytes:
    blueprint = _mapping(load_yaml_file(workspace / "blueprint/blueprint.yaml"), "Blueprint")
    return local_model_config_bytes(blueprint, source_info=_source_info(workspace))


def _error(code: str, locator: str, message: str) -> dict[str, str]:
    return {"code": code, "locator": locator, "message": message}


def _semantic_errors(document: Mapping[str, Any]) -> list[dict[str, str]]:
    errors: list[dict[str, str]] = []
    declaration = document.get("local_models")
    if not isinstance(declaration, Mapping):
        return [_error("LOCAL_MODEL_DECLARATION_INVALID", "/local_models", "local_models must be a mapping")]

    if declaration.get("enabled_by_default") is not False:
        errors.append(
            _error(
                "LOCAL_MODEL_DEFAULT_ENABLED",
                "/local_models/enabled_by_default",
                "local model execution must remain disabled by default",
            )
        )
    if declaration.get("default_embedding_profile") != "embedding":
        errors.append(
            _error(
                "LOCAL_MODEL_DEFAULT_EMBEDDING_INVALID",
                "/local_models/default_embedding_profile",
                "the default embedding profile must be embedding",
            )
        )
    if declaration.get("emergency_embedding_fallback_profile") != "embedding_fallback":
        errors.append(
            _error(
                "LOCAL_MODEL_EMBEDDING_FALLBACK_INVALID",
                "/local_models/emergency_embedding_fallback_profile",
                "the emergency embedding fallback must be embedding_fallback",
            )
        )
    activation = declaration.get("activation_policy")
    expected_activation = {
        "selected_embedding_profile": "embedding",
        "mode": "explicit_serial",
        "one_model_loaded": True,
        "concurrent_requests": False,
        "unattended_activation": False,
        "accepted_min_free_memory_percent": 24,
    }
    if not isinstance(activation, Mapping) or dict(activation) != expected_activation:
        errors.append(
            _error(
                "LOCAL_MODEL_ACTIVATION_POLICY_INVALID",
                "/local_models/activation_policy",
                "activation must select one explicit serial Qwen profile with no unattended activation",
            )
        )
    endpoint = declaration.get("endpoint")
    if not isinstance(endpoint, Mapping):
        errors.append(_error("LOCAL_MODEL_ENDPOINT_INVALID", "/local_models/endpoint", "endpoint must be a mapping"))
    else:
        if endpoint.get("base_url") != "http://127.0.0.1:11434":
            errors.append(
                _error(
                    "LOCAL_MODEL_ENDPOINT_NOT_LOOPBACK",
                    "/local_models/endpoint/base_url",
                    "local model endpoint must use the explicit loopback URL",
                )
            )
        expected_flags = {
            "loopback_only": True,
            "cloud_disabled": True,
            "allow_redirects": False,
            "allow_proxy": False,
            "allow_tunnel": False,
            "auto_pull": False,
        }
        for key, expected in expected_flags.items():
            if endpoint.get(key) is not expected:
                errors.append(
                    _error(
                        "LOCAL_MODEL_ENDPOINT_POLICY_INVALID",
                        f"/local_models/endpoint/{key}",
                        f"endpoint policy {key} has an unsafe value",
                    )
                )

    environment = declaration.get("environment")
    if not isinstance(environment, Mapping) or environment.get("OLLAMA_NO_CLOUD") != "1":
        errors.append(
            _error(
                "LOCAL_MODEL_CLOUD_NOT_DISABLED",
                "/local_models/environment/OLLAMA_NO_CLOUD",
                "OLLAMA_NO_CLOUD=1 is required by the local-model contract",
            )
        )

    profiles = declaration.get("profiles")
    if not isinstance(profiles, Mapping):
        errors.append(_error("LOCAL_MODEL_PROFILES_INVALID", "/local_models/profiles", "profiles must be a mapping"))
    else:
        for profile_name in ("generation", "embedding", "embedding_fallback"):
            profile = profiles.get(profile_name)
            if not isinstance(profile, Mapping):
                errors.append(
                    _error(
                        "LOCAL_MODEL_PROFILE_INVALID",
                        f"/local_models/profiles/{profile_name}",
                        "required local model profile is missing",
                    )
                )
                continue
            expected_enabled = profile_name == "embedding"
            if profile.get("enabled") is not expected_enabled:
                errors.append(
                    _error(
                        "LOCAL_MODEL_PROFILE_ENABLED",
                        f"/local_models/profiles/{profile_name}/enabled",
                        "only the accepted Qwen live-default profile may be enabled",
                    )
                )
            expected_selection = "live_default" if profile_name == "embedding" else None
            if profile_name == "embedding" and profile.get("selection") != expected_selection:
                errors.append(
                    _error(
                        "LOCAL_MODEL_DEFAULT_SELECTION_INVALID",
                        f"/local_models/profiles/{profile_name}/selection",
                        "embedding profile must be the live default selector",
                    )
                )
            if profile_name == "embedding" and (
                profile.get("model_tag") != QWEN_MODEL_TAG or profile.get("model_digest") != QWEN_MODEL_DIGEST
            ):
                errors.append(
                    _error(
                        "LOCAL_MODEL_QWEN_IDENTITY_INVALID",
                        f"/local_models/profiles/{profile_name}",
                        "live-default embedding must bind the approved Qwen tag and full digest",
                    )
                )
            if profile.get("model_digest") is not None:
                digest = profile.get("model_digest")
                if not isinstance(digest, str) or len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
                    errors.append(
                        _error(
                            "LOCAL_MODEL_DIGEST_INVALID",
                            f"/local_models/profiles/{profile_name}/model_digest",
                            "model_digest must be null or lowercase SHA-256",
                        )
                    )
    return errors


def inspect_local_model_config(root: str | Path) -> tuple[dict[str, Any], list[dict[str, str]]]:
    """Inspect the checked-in config without probing or changing any service."""

    workspace = Path(root).resolve()
    path = workspace / LOCAL_MODEL_CONFIG_PATH
    base: dict[str, Any] = {
        "path": LOCAL_MODEL_CONFIG_PATH,
        "status": "INACTIVE",
        "profile_state": "not_configured",
        "enabled_by_default": False,
        "profiles": {},
        "service_probe": "not_run",
        "external_authorization": "not_authorized",
        "model_digests": {},
    }
    if path.is_symlink():
        return {**base, "status": "DEGRADED", "reason": "config_symlink"}, [
            _error("LOCAL_MODEL_CONFIG_SYMLINK", "/local-models.yaml", "local-models.yaml must be a regular file")
        ]
    if not path.is_file():
        return {**base, "reason": "config_missing"}, [
            _error("LOCAL_MODEL_CONFIG_MISSING", "/local-models.yaml", "generated local-models.yaml is missing")
        ]
    try:
        value = load_yaml_file(path)
        document = _mapping(value, "local-models.yaml")
        expected = _expected_bytes(workspace)
    except (OSError, UnicodeError, TypeError, ValueError, yaml.YAMLError) as error:
        return {**base, "status": "DEGRADED", "reason": "config_invalid"}, [
            _error("LOCAL_MODEL_CONFIG_INVALID", "/local-models.yaml", str(error))
        ]
    errors = _semantic_errors(document)
    if path.read_bytes() != expected:
        errors.append(
            _error(
                "LOCAL_MODEL_CONFIG_DRIFT",
                "/local-models.yaml",
                "local-models.yaml differs from the deterministic C30 generator output",
            )
        )
    declaration = document.get("local_models")
    if isinstance(declaration, Mapping):
        profiles = declaration.get("profiles")
        if isinstance(profiles, Mapping):
            profile_report: dict[str, Any] = {}
            digests: dict[str, Any] = {}
            for name in ("generation", "embedding", "embedding_fallback"):
                profile = profiles.get(name)
                if isinstance(profile, Mapping):
                    profile_report[name] = {
                        "task": profile.get("task"),
                        "model_tag": profile.get("model_tag"),
                        "enabled": profile.get("enabled"),
                        "state": (
                            "live_default"
                            if name == "embedding" and profile.get("enabled") is True
                            else "disabled"
                            if profile.get("enabled") is False
                            else "enabled"
                        ),
                    }
                    digests[name] = profile.get("model_digest")
            base["profiles"] = profile_report
            base["model_digests"] = digests
        base["enabled_by_default"] = declaration.get("enabled_by_default") is True
    if errors:
        return {**base, "status": "DEGRADED", "profile_state": "configured", "reason": "config_invalid"}, errors
    return {
        **base,
        "status": "PASS",
        "profile_state": "configured",
        "reason": "explicit_serial_live_default",
        "default_embedding_profile": "embedding",
        "emergency_embedding_fallback_profile": "embedding_fallback",
        "enabled_by_default": False,
        "activation_policy": {
            "mode": "explicit_serial",
            "one_model_loaded": True,
            "concurrent_requests": False,
            "unattended_activation": False,
            "accepted_min_free_memory_percent": 24,
        },
        "config_sha256": _sha256_bytes(path.read_bytes()),
    }, []


__all__ = [
    "LOCAL_MODEL_ARTIFACT_KIND",
    "LOCAL_MODEL_AUTHORITATIVE_SELECTORS",
    "LOCAL_MODEL_CONFIG_PATH",
    "LOCAL_MODEL_SCHEMA_VERSION",
    "LocalModelConfigError",
    "inspect_local_model_config",
    "local_model_config_bytes",
    "local_model_config_document",
]

"""Canonical KnowledgeHub Vault identity and root sentinel validation."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

from jsonschema import Draft202012Validator, FormatChecker

UUID_V4_PATTERN = r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"

SHA256_PATTERN = r"^[0-9a-f]{64}$"

BRANCH_PATTERN = r"^(?!-)(?!.*\.\.)(?!.*[ ~^:?*\[\\])[^\r\n]{1,255}$"



@dataclass(frozen=True)
class ContractIssue:
    """Stable Vault identity diagnostic."""

    code: str
    locator: str
    message: str

    def as_dict(self) -> dict[str, str]:
        return {"code": self.code, "locator": self.locator, "message": self.message}


@dataclass(frozen=True)
class ContractReport:
    """Read-only validation report for a JSON contract document."""

    contract: str
    issues: tuple[ContractIssue, ...] = ()

    @property
    def passed(self) -> bool:
        return not self.issues

    def as_dict(self) -> dict[str, Any]:
        return {
            "contract": self.contract,
            "status": "PASS" if self.passed else "FAIL",
            "issues": [issue.as_dict() for issue in self.issues],
        }

def _hash_schema() -> dict[str, str]:
    return {"type": "string", "pattern": SHA256_PATTERN}

def build_root_sentinel_schema(blueprint: Mapping[str, Any]) -> dict[str, Any]:
    """Build the allowlisted sentinel schema without producing a sentinel file."""

    contract = blueprint["vault_identity"]
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "https://local.invalid/knowledgeos/root-sentinel.schema.json",
        "title": "KnowledgeOS Vault root sentinel",
        "type": "object",
        "additionalProperties": False,
        "required": list(contract["required_fields"]),
        "properties": {
            "schema_version": {"const": contract["schema_version"]},
            "contract_id": {"const": contract["contract_id"]},
            "vault_uuid": {
                "type": "string",
                "minLength": 36,
                "maxLength": 36,
                "pattern": UUID_V4_PATTERN,
            },
            "canonical_vault_name": {"const": contract["canonical_vault_name"]},
            "remote_identity_sha256": _hash_schema(),
            "expected_branch": {"type": "string", "pattern": BRANCH_PATTERN},
        },
    }


def schema_bytes(schema: Mapping[str, Any]) -> bytes:
    """Return deterministic UTF-8 schema bytes."""

    return (json.dumps(schema, ensure_ascii=False, indent=2, sort_keys=False) + "\n").encode("utf-8")


def canonical_json_bytes(document: Mapping[str, Any]) -> bytes:
    """Serialize one identity document deterministically."""

    return (json.dumps(document, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode(
        "utf-8"
    )


def canonicalize_github_remote(remote: str) -> str:
    """Normalize an SSH/HTTPS GitHub remote to the digest input form.

    Only ``github.com/<owner>/<repository>.git\\n`` is returned.  Credentials,
    query strings, fragments, extra path components, and non-GitHub hosts are
    rejected before hashing.
    """

    if not isinstance(remote, str) or not remote or remote != remote.strip():
        raise ValueError("remote must be a non-empty string without surrounding whitespace")
    if any(character.isspace() or ord(character) < 32 for character in remote):
        raise ValueError("remote must not contain whitespace or control characters")
    if "?" in remote or "#" in remote or "\\" in remote:
        raise ValueError("remote query, fragment, and backslash are forbidden")

    host: str
    path: str
    if remote.startswith("git@") and ":" in remote:
        user_host, path = remote.split(":", 1)
        user, separator, host = user_host.partition("@")
        if not separator or user != "git":
            raise ValueError("only the git@github.com SSH form is allowed")
    else:
        parsed = urlsplit(remote if "://" in remote else f"https://{remote}")
        if parsed.password is not None or (
            parsed.username is not None
            and (parsed.scheme != "ssh" or parsed.username != "git")
        ):
            raise ValueError("remote credentials are forbidden")
        host = parsed.hostname or ""
        path = parsed.path.removeprefix("/")
        if parsed.query or parsed.fragment:
            raise ValueError("remote query and fragment are forbidden")
        if parsed.scheme not in {"https", "ssh"}:
            raise ValueError("remote scheme must be https or ssh")

    if host.casefold() != "github.com":
        raise ValueError("remote host must be github.com")
    parts = path.split("/")
    if len(parts) != 2:
        raise ValueError("remote path must contain exactly owner/repository")
    owner, repository = parts
    repository = repository.removesuffix(".git")
    component_pattern = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
    if not component_pattern.fullmatch(owner) or not component_pattern.fullmatch(repository):
        raise ValueError("remote owner and repository contain an invalid character")
    return f"github.com/{owner.casefold()}/{repository.casefold()}.git\n"


def remote_identity_sha256(remote: str) -> str:
    """Hash the canonical GitHub remote identity bytes."""

    return hashlib.sha256(canonicalize_github_remote(remote).encode("utf-8")).hexdigest()

def _schema_issues(document: Any, schema: Mapping[str, Any], contract: str) -> tuple[ContractIssue, ...]:
    validator = Draft202012Validator(schema, format_checker=FormatChecker())
    issues = [
        ContractIssue(
            "VAULT_IDENTITY_SCHEMA_INVALID",
            "/" + "/".join(str(part) for part in error.absolute_path),
            error.message,
        )
        for error in validator.iter_errors(document)
    ]
    return tuple(sorted(issues, key=lambda issue: (issue.locator, issue.message)))


def validate_root_sentinel(
    document: Any,
    blueprint: Mapping[str, Any],
    *,
    expected: Mapping[str, str] | None = None,
) -> ContractReport:
    """Validate sentinel shape and optional exact expected bindings."""

    issues = list(_schema_issues(document, build_root_sentinel_schema(blueprint), "root-sentinel"))
    if not issues and expected is not None and isinstance(document, Mapping):
        for field in ("vault_uuid", "canonical_vault_name", "remote_identity_sha256", "expected_branch"):
            if field in expected and document.get(field) != expected[field]:
                issues.append(
                    ContractIssue(
                        "VAULT_SENTINEL_BINDING_MISMATCH",
                        f"/{field}",
                        f"sentinel value does not match the expected {field}",
                    )
                )
    return ContractReport("root-sentinel", tuple(issues))


def validate_remote_identity(remote: str, expected_sha256: str) -> ContractReport:
    """Validate a remote's canonical identity against an expected digest."""

    try:
        observed = remote_identity_sha256(remote)
    except ValueError as error:
        return ContractReport(
            "remote-identity",
            (ContractIssue("VAULT_REMOTE_INVALID", "/remote", str(error)),),
        )
    if observed != expected_sha256:
        return ContractReport(
            "remote-identity",
            (
                ContractIssue(
                    "VAULT_REMOTE_IDENTITY_MISMATCH",
                    "/remote_identity_sha256",
                    "remote identity digest does not match the expected value",
                ),
            ),
        )
    return ContractReport("remote-identity")

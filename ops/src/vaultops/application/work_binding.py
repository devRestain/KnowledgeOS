"""Pure target binding checks adapted from the Core 0.17 reference contract.

Owned here for offline candidate validation; this module imports no development
implementation and grants no authenticated human action authority.
"""
import copy
import hashlib
import re
from typing import Any

from ..adapters.core import AdmissionError as ContractViolation
from ..adapters.core import canonical as canonical_json

_IDENTIFIER = re.compile(r"^[a-z][a-z0-9_-]*(?:\.[a-z0-9][a-z0-9_-]*)*$")
_REVISION = re.compile(r"^revision\.([1-9][0-9]*)$")
_SEMVER = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")
_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
_WORK_CONTEXT = ("work_run_id", "work_spec_revision", "work_spec_digest", "target_control_revision")

def _identifier(value: Any, field: str) -> str:
    if not isinstance(value, str) or not _IDENTIFIER.fullmatch(value):
        raise ContractViolation("INVALID_PAYLOAD", f"Invalid {field}")
    return value


def _target_digest(target_binding: dict[str, Any], semantic_version: str, semantic_pin: str) -> str:
    body = {
        "semantic_digest": semantic_pin,
        "semantic_version": semantic_version,
        "target_binding": target_binding,
    }
    return "sha256:" + hashlib.sha256(canonical_json(body)).hexdigest()


def _validate_target_binding(value: Any, owner_operation_id: str) -> dict[str, Any]:
    required = {
        "action_id", "target_revision", "operative_package_version",
        "operative_package_digest", "mapping_id", "mapping_version",
        "mapping_digest",
    }
    optional = {"graph_binding", "work_binding"}
    if not isinstance(value, dict) or required - value.keys() or value.keys() - required - optional:
        raise ContractViolation("INVALID_PAYLOAD", "Target binding has an unsupported shape")
    target = copy.deepcopy(value)
    _identifier(target["action_id"], "action id")
    if not isinstance(target["target_revision"], str) or not _REVISION.fullmatch(target["target_revision"]):
        raise ContractViolation("INVALID_PAYLOAD", "Target revision must use revision.N")
    for field in ("operative_package_version", "mapping_version"):
        if not isinstance(target[field], str) or not _SEMVER.fullmatch(target[field]):
            raise ContractViolation("INVALID_PAYLOAD", f"Invalid {field}")
    for field in ("operative_package_digest", "mapping_digest"):
        if not isinstance(target[field], str) or not _DIGEST.fullmatch(target[field]):
            raise ContractViolation("INVALID_PAYLOAD", f"Invalid {field}")
    _identifier(target["mapping_id"], "mapping id")
    graph = target.get("graph_binding")
    if graph is not None:
        graph_required = {
            "graph_id", "graph_revision", "node_id", "node_revision",
            "owner_operation_id", "executor_id", "assignment_revision",
            "role_digest", "capability_digest",
        }
        if not isinstance(graph, dict) or set(graph) != graph_required:
            raise ContractViolation("INVALID_PAYLOAD", "Graph target binding is incomplete")
        for field in ("graph_id", "node_id", "executor_id"):
            _identifier(graph[field], field)
        if graph["owner_operation_id"] != owner_operation_id:
            raise ContractViolation("SCOPE_DENIED", "Graph owner differs from the request owner")
        for field in ("graph_revision", "node_revision", "assignment_revision"):
            if type(graph[field]) is not int or graph[field] < 1:
                raise ContractViolation("INVALID_PAYLOAD", f"{field} must be positive")
        for field in ("role_digest", "capability_digest"):
            if not isinstance(graph[field], str) or not _DIGEST.fullmatch(graph[field]):
                raise ContractViolation("INVALID_PAYLOAD", f"Invalid {field}")
    work = target.get("work_binding")
    if work is not None:
        expected = {"owner_operation_id", *_WORK_CONTEXT, "generation", "fencing_token", "control_intent_digest"}
        if not isinstance(work, dict) or set(work) != expected:
            raise ContractViolation("INVALID_PAYLOAD", "Work target binding is incomplete")
        if work["owner_operation_id"] != owner_operation_id:
            raise ContractViolation("SCOPE_DENIED", "Work target belongs to another owner")
        for name in ("work_run_id", "fencing_token"):
            _identifier(work[name], name)
        for name in ("work_spec_revision", "target_control_revision", "generation"):
            if type(work[name]) is not int or work[name] < 1:
                raise ContractViolation("INVALID_PAYLOAD", f"Invalid {name}")
        for name in ("work_spec_digest", "control_intent_digest"):
            if not isinstance(work[name], str) or not _DIGEST.fullmatch(work[name]):
                raise ContractViolation("INVALID_PAYLOAD", f"Invalid {name}")
        if target["target_revision"] != f"revision.{work['target_control_revision']}":
            raise ContractViolation("STALE_REVISION", "Work target revision differs from the control binding")
    if len(canonical_json(target)) > 4096:
        raise ContractViolation("INVALID_PAYLOAD", "Target binding exceeds its local bound")
    return target



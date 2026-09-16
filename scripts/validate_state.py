#!/usr/bin/env python3
"""Validate a project-state protocol file using only the Python standard library."""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path

NAME_RE = re.compile(r"^[a-z_]+$")
KEY_RE = re.compile(r"^[a-z_]+$")
RFC3339_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$"
)
SCALAR_RE = re.compile(r"^[A-Za-z0-9._:/_+\-]+$")
IDENT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
GOAL_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")
BASE_VERB_RE = re.compile(r"^[A-Za-z]+(?:[ -][A-Za-z0-9][A-Za-z0-9._/-]*)*$")

GOAL_STATES = {"planned", "active", "blocked", "complete", "deferred", "cancelled"}
CHECK_STATES = {"pass", "fail", "blocked", "not_run", "unconfigured", "unknown"}
EVIDENCE_CLASSES = {
    "static",
    "semantic",
    "runtime",
    "artifact",
    "deployment",
    "external_service",
    "device",
}
PROJECT_STATES = {"planned", "active", "blocked", "complete", "unknown"}
HANDOFF_STATES = {"ready", "incomplete", "blocked"}


@dataclass
class Record:
    name: str
    fields: dict[str, str]
    line: int


class StateError(Exception):
    pass


def decode_value(raw: str, line: int) -> str:
    if raw.startswith('"'):
        if not raw.endswith('"'):
            raise StateError(f"line {line}: unterminated quoted value")
        body = raw[1:-1]
        result: list[str] = []
        index = 0
        while index < len(body):
            if body[index] != "\\":
                result.append(body[index])
                index += 1
                continue
            index += 1
            if index >= len(body) or body[index] not in {'"', "\\"}:
                raise StateError(f"line {line}: invalid escape")
            result.append(body[index])
            index += 1
        return "".join(result)
    if not SCALAR_RE.fullmatch(raw):
        raise StateError(f"line {line}: invalid scalar {raw!r}")
    return raw


def parse_line(raw: str, line: int) -> Record:
    if not raw.startswith("/"):
        raise StateError(f"line {line}: invalid directive syntax")
    name, separator, rest = raw[1:].partition(" ")
    if not NAME_RE.fullmatch(name):
        raise StateError(f"line {line}: invalid directive name")
    fields: dict[str, str] = {}
    if not separator:
        return Record(name, fields, line)
    rest = " " + rest
    cursor = 0
    while cursor < len(rest):
        if rest[cursor] != " ":
            raise StateError(f"line {line}: invalid field separator")
        cursor += 1
        key_start = cursor
        while cursor < len(rest) and rest[cursor] not in " =":
            cursor += 1
        key = rest[key_start:cursor]
        if not KEY_RE.fullmatch(key) or cursor >= len(rest) or rest[cursor] != "=":
            raise StateError(f"line {line}: invalid field syntax")
        cursor += 1
        if cursor >= len(rest):
            raise StateError(f"line {line}: missing field value")
        if rest[cursor] == '"':
            value_start = cursor
            cursor += 1
            escaped = False
            while cursor < len(rest):
                char = rest[cursor]
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == '"':
                    cursor += 1
                    break
                cursor += 1
            else:
                raise StateError(f"line {line}: unterminated quoted value")
            raw_value = rest[value_start:cursor]
        else:
            value_start = cursor
            while cursor < len(rest) and rest[cursor] != " ":
                cursor += 1
            raw_value = rest[value_start:cursor]
        if key in fields:
            raise StateError(f"line {line}: duplicate field {key!r}")
        fields[key] = decode_value(raw_value, line)
    return Record(name, fields, line)


def require(record: Record, *keys: str) -> None:
    missing = [key for key in keys if key not in record.fields]
    if missing:
        raise StateError(f"line {record.line}: {record.name} missing {', '.join(missing)}")


def exact_fields(record: Record, allowed: set[str]) -> None:
    extra = set(record.fields) - allowed
    if extra:
        raise StateError(
            f"line {record.line}: {record.name} has unknown fields {', '.join(sorted(extra))}"
        )


def require_verb(record: Record, key: str) -> None:
    value = record.fields[key]
    if not value or not BASE_VERB_RE.fullmatch(value):
        raise StateError(f"line {record.line}: {record.name}.{key} must start with a base-form verb")


def validate(records: list[Record]) -> None:
    allowed = {
        "state",
        "project",
        "status",
        "checkpoint",
        "goal",
        "accept",
        "evidence",
        "blocker",
        "decision",
        "scope",
        "handoff",
    }
    if not records:
        raise StateError("empty state file")
    unknown = [record for record in records if record.name not in allowed]
    if unknown:
        raise StateError(f"line {unknown[0].line}: unknown directive /{unknown[0].name}")

    by_name = {name: [record for record in records if record.name == name] for name in allowed}
    for name in ("state", "project", "status", "checkpoint", "handoff"):
        if len(by_name[name]) != 1:
            raise StateError(f"/{name} must occur exactly once")

    state = by_name["state"][0]
    exact_fields(state, {"version"})
    require(state, "version")
    if state.fields["version"] != "1":
        raise StateError(f"line {state.line}: unsupported state version")

    project = by_name["project"][0]
    exact_fields(project, {"id"})
    require(project, "id")
    if not IDENT_RE.fullmatch(project.fields["id"]):
        raise StateError(f"line {project.line}: invalid project id")

    status = by_name["status"][0]
    exact_fields(status, {"value"})
    require(status, "value")
    if status.fields["value"] not in PROJECT_STATES:
        raise StateError(f"line {status.line}: invalid project state")

    checkpoint = by_name["checkpoint"][0]
    exact_fields(checkpoint, {"revision", "dirty", "updated"})
    require(checkpoint, "revision", "dirty", "updated")
    if checkpoint.fields["revision"] == "":
        raise StateError(f"line {checkpoint.line}: empty revision")
    if checkpoint.fields["dirty"] not in {"true", "false", "unknown"}:
        raise StateError(f"line {checkpoint.line}: invalid dirty state")
    if not RFC3339_RE.fullmatch(checkpoint.fields["updated"]):
        raise StateError(f"line {checkpoint.line}: invalid RFC 3339 timestamp")

    goals = by_name["goal"]
    current = [record for record in goals if record.fields.get("phase") == "current"]
    next_goals = [record for record in goals if record.fields.get("phase") == "next"]
    if len(current) != 1 or len(next_goals) > 1:
        raise StateError("state must contain exactly one current goal and at most one next goal")
    seen_goal_ids: set[str] = set()
    for goal in goals:
        exact_fields(goal, {"phase", "id", "state", "action"})
        require(goal, "phase", "id", "state", "action")
        if goal.fields["phase"] not in {"current", "next", "history"}:
            raise StateError(f"line {goal.line}: invalid goal phase")
        if not GOAL_ID_RE.fullmatch(goal.fields["id"]):
            raise StateError(f"line {goal.line}: invalid goal id")
        if goal.fields["id"] in seen_goal_ids:
            raise StateError(f"line {goal.line}: duplicate goal id")
        seen_goal_ids.add(goal.fields["id"])
        if goal.fields["state"] not in GOAL_STATES:
            raise StateError(f"line {goal.line}: invalid goal state")
        if goal.fields["phase"] == "history" and goal.fields["state"] not in {
            "complete",
            "cancelled",
        }:
            raise StateError(f"line {goal.line}: history goal must be complete or cancelled")
        if goal.fields["phase"] == "next" and goal.fields["state"] != "planned":
            raise StateError(f"line {goal.line}: next goal must be planned")
        require_verb(goal, "action")

    accepts = by_name["accept"]
    accept_ids: set[str] = set()
    evidence_refs: dict[str, str] = {}
    for accept in accepts:
        exact_fields(accept, {"id", "state", "action", "evidence"})
        require(accept, "id", "state", "action", "evidence")
        if not IDENT_RE.fullmatch(accept.fields["id"]):
            raise StateError(f"line {accept.line}: invalid acceptance id")
        goal_id, separator, criterion_id = accept.fields["id"].partition(".")
        if not separator or goal_id not in seen_goal_ids or not criterion_id:
            raise StateError(f"line {accept.line}: acceptance id must use an existing goal id prefix")
        if accept.fields["id"] in accept_ids:
            raise StateError(f"line {accept.line}: duplicate acceptance id")
        accept_ids.add(accept.fields["id"])
        if accept.fields["state"] not in CHECK_STATES:
            raise StateError(f"line {accept.line}: invalid acceptance state")
        require_verb(accept, "action")
        evidence = accept.fields["evidence"]
        if evidence != "none":
            evidence_refs[accept.fields["id"]] = evidence

    evidence_ids: set[str] = set()
    for evidence in by_name["evidence"]:
        exact_fields(evidence, {"id", "class", "result", "source", "observed"})
        require(evidence, "id", "class", "result", "source", "observed")
        if not IDENT_RE.fullmatch(evidence.fields["id"]):
            raise StateError(f"line {evidence.line}: invalid evidence id")
        if evidence.fields["id"] in evidence_ids:
            raise StateError(f"line {evidence.line}: duplicate evidence id")
        evidence_ids.add(evidence.fields["id"])
        if evidence.fields["class"] not in EVIDENCE_CLASSES:
            raise StateError(f"line {evidence.line}: invalid evidence class")
        if evidence.fields["result"] not in CHECK_STATES:
            raise StateError(f"line {evidence.line}: invalid evidence result")
        require_verb(evidence, "observed")

    missing_evidence = sorted(set(evidence_refs.values()) - evidence_ids)
    if missing_evidence:
        raise StateError(f"acceptance references missing evidence: {', '.join(missing_evidence)}")

    for blocker in by_name["blocker"]:
        exact_fields(blocker, {"id", "state", "action", "source"})
        require(blocker, "id", "state", "action", "source")
        if not IDENT_RE.fullmatch(blocker.fields["id"]):
            raise StateError(f"line {blocker.line}: invalid blocker id")
        if blocker.fields["state"] not in {"open", "resolved"}:
            raise StateError(f"line {blocker.line}: invalid blocker state")
        require_verb(blocker, "action")

    for decision in by_name["decision"]:
        exact_fields(decision, {"id", "state", "action", "source"})
        require(decision, "id", "state", "action", "source")
        if not IDENT_RE.fullmatch(decision.fields["id"]):
            raise StateError(f"line {decision.line}: invalid decision id")
        if decision.fields["state"] not in {"accepted", "rejected", "pending"}:
            raise StateError(f"line {decision.line}: invalid decision state")
        require_verb(decision, "action")

    for scope in by_name["scope"]:
        exact_fields(scope, {"id", "state", "action"})
        require(scope, "id", "state", "action")
        if not IDENT_RE.fullmatch(scope.fields["id"]):
            raise StateError(f"line {scope.line}: invalid scope id")
        if scope.fields["state"] not in {"in", "out", "pending"}:
            raise StateError(f"line {scope.line}: invalid scope state")
        require_verb(scope, "action")

    handoff = by_name["handoff"][0]
    exact_fields(handoff, {"state", "action"})
    require(handoff, "state", "action")
    if handoff.fields["state"] not in HANDOFF_STATES:
        raise StateError(f"line {handoff.line}: invalid handoff state")
    require_verb(handoff, "action")

    current_goal = current[0]
    current_accepts = [
        accept
        for accept in accepts
        if accept.fields["id"].startswith(current_goal.fields["id"] + ".")
    ]
    if current_goal.fields["state"] == "complete":
        if not current_accepts or any(
            accept.fields["state"] != "pass" for accept in current_accepts
        ):
            raise StateError(f"line {current_goal.line}: complete goal lacks all-pass acceptance records")
        pass_evidence = {
            evidence.fields["id"]: evidence.fields["result"] for evidence in by_name["evidence"]
        }
        for accept in current_accepts:
            evidence_id = accept.fields["evidence"]
            if evidence_id == "none" or pass_evidence.get(evidence_id) != "pass":
                raise StateError(f"line {accept.line}: complete acceptance lacks pass evidence")
    if current_goal.fields["state"] == "blocked" and not any(
        blocker.fields.get("state") == "open" for blocker in by_name["blocker"]
    ):
        raise StateError(f"line {current_goal.line}: blocked goal lacks an open blocker")

    if records[0].name != "state":
        raise StateError("/state must be the first directive")
    if records[1].name != "project" or records[2].name != "status" or records[3].name != "checkpoint":
        raise StateError("header directives must be ordered /state /project /status /checkpoint")
    if records[-1].name != "handoff":
        raise StateError("/handoff must be the final directive")


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate a project-state protocol file.")
    parser.add_argument("state_file", type=Path)
    args = parser.parse_args()
    try:
        content = args.state_file.read_text(encoding="utf-8")
        if "\r" in content:
            raise StateError("CR characters are not allowed; use LF line endings")
        lines = content.splitlines()
        if not lines or any(not line for line in lines):
            raise StateError("blank lines are not allowed")
        records = [parse_line(line, index) for index, line in enumerate(lines, start=1)]
        validate(records)
    except (OSError, UnicodeError, StateError) as exc:
        print(f"INVALID: {exc}", file=sys.stderr)
        return 1
    print(f"VALID: {args.state_file}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

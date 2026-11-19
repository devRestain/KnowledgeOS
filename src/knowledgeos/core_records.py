"""AgentFabric Core records compiled from Owner State (Blueprint §7).

``config/core-adoption.json`` selects the exact Core release, schema bundle and semantic
package. Graph records describe the actual retained Temporal/LangGraph registration and
owner assignments; unavailable or unbound records are never fabricated.

Identifier and digest conventions (all recomputable from the Work record):
- Core identifiers are lowercase dotted (``^[a-z][a-z0-9_-]*(\\.[a-z0-9][a-z0-9_-]*)*``).
  A KnowledgeOS name that does not fit becomes ``<kind>.sha256-<hex of the UTF-8 name>``.
- Digests are ``sha256:<hex>`` of ``util.canonical`` JSON (record without its own digest
  field) or of exact bytes. ``work_run_id`` is ``<work_id>.run`` (one run per Work);
  ``graph_run_id`` is ``<work_id>.s<segment, 1-based>``.
- ``capability_digest`` covers the admitted capability declaration copied into the Work
  (capability, version, target, criteria, budget, evaluation, admitted pool);
  ``role_digest`` covers that digest, the role's admitted declaration and the Work's
  implementation ``source_revision``.
- Artifacts are not exported by the Owner, so every ArtifactReference is
  ``availability: unavailable``; ``classification`` follows the strictest ``ai_policy``.
"""

from __future__ import annotations

import json
import re
import threading
from pathlib import Path
from typing import Any

from . import OPERATION_ID
from .config import ROOT
from .policy import strictest
from .util import canonical, sha256
from .vault import CLASSIFICATION, PROPOSALS_DIR, REPORTS_DIR

ID_RE = re.compile(r"^[a-z][a-z0-9_-]*(\.[a-z0-9][a-z0-9_-]*)*$")
DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
SEMVER_RE = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")
KEY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
MAX_ID = 96
MAX_SUMMARY = 512
MAX_REFERENCES = 32
CORE_TYPES = (
    "WorkSpec", "GraphSpec", "GraphNode", "GraphEdge", "GraphAssignment", "GraphSegment",
    "RunEvaluation", "CompletionAssessment", "RemainingWork", "ArtifactReference", "HumanActionResponse",
)

# ------------------------------------------------------------------ adoption pins

_pins_lock = threading.Lock()
_pins_cache: dict[Path, tuple[tuple[int, int, int], dict[str, str]]] = {}


class AdoptionError(ValueError):
    """``config/core-adoption.json`` is missing or malformed."""


def pins(root: Path | None = None) -> dict[str, str]:
    """``{core_version, core_digest, semantic_version, semantic_digest, schema_version}``.

    Read from ``<root>/config/core-adoption.json``, cached by file identity.
    """
    path = Path(root or ROOT) / "config" / "core-adoption.json"
    try:
        stat = path.stat()
    except FileNotFoundError as error:
        raise AdoptionError(f"{path} is missing") from error
    identity = (stat.st_ino, stat.st_size, stat.st_mtime_ns)
    with _pins_lock:
        cached = _pins_cache.get(path)
        if cached is not None and cached[0] == identity:
            return dict(cached[1])
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise AdoptionError(f"{path.name}: {error}") from error
        selected = {
            "core_version": data.get("core_release_version"),
            "core_digest": data.get("core_digest"),
            "semantic_version": data.get("semantic_version"),
            "semantic_digest": data.get("semantic_digest"),
            "schema_version": data.get("schema_version"),
        }
        for key, value in selected.items():
            pattern = DIGEST_RE if key.endswith("_digest") else SEMVER_RE
            if not isinstance(value, str) or not pattern.fullmatch(value):
                raise AdoptionError(f"{path.name}: invalid {key}")
        _pins_cache[path] = (identity, selected)
        return dict(selected)


# ------------------------------------------------------------------ small helpers


def core_digest(value: Any) -> str:
    """``sha256:`` digest of the canonical JSON of ``value``."""
    return "sha256:" + sha256(canonical(value))


def prefixed(hex_digest: str) -> str:
    """A stored bare SHA-256 hex digest in Core form."""
    return hex_digest if hex_digest.startswith("sha256:") else "sha256:" + hex_digest


def core_id(value: str, kind: str) -> str:
    """``value`` if it already is a Core identifier, else ``<kind>.sha256-<hex>``."""
    if len(value) <= MAX_ID and ID_RE.fullmatch(value):
        return value
    return f"{kind}.sha256-{sha256(value.encode('utf-8'))}"


def idempotency_key(value: str) -> str:
    """A request ID as a Core idempotency key (longer IDs become their digest)."""
    return value if KEY_RE.fullmatch(value) else "sha256-" + sha256(value.encode("utf-8"))


def core_name(name: str) -> str:
    """A role or Officer name as a Core identifier: ``StandardizationOfficer`` -> ``standardization_officer``."""
    snake = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", name).lower()
    snake = re.sub(r"[^a-z0-9_-]+", "_", snake).strip("_-")
    return core_id(snake, "role") if snake else core_id(name, "role")


def actor_id(actor: str | None) -> str:
    """``user/local`` -> ``user.local``; ``schedule/<job>`` -> ``schedule.<job>``."""
    if not actor:
        return "unknown"
    return core_id(actor.replace("/", ".", 1), "actor")


def semver(version: Any) -> str:
    parts = str(version).split(".")
    if not 1 <= len(parts) <= 3 or not all(part.isdigit() for part in parts):
        raise ValueError(f"capability version {version!r} is not numeric")
    return ".".join([*parts, "0", "0"][:3])


def classification(policy: str) -> str:
    return CLASSIFICATION.get(policy, "restricted")


# ------------------------------------------------------------------ WorkSpec


class _Unavailable(Exception):
    """A Core record KnowledgeOS cannot honestly compile from the recorded Work."""


class _Compiler:
    def __init__(self, owner: Any, work: dict[str, Any]):
        self.owner = owner
        self.work = work
        self.spec = work["workspec"]
        self.pins = pins(owner.config.root)
        self.work_id = core_id(work["work_id"], "work")
        self.run_id = f"{self.work_id}.run"
        self.source_revision = (work.get("implementation") or {}).get("source_revision") or owner.identity
        self.team = work.get("team") if self.spec.get("target_kind") == "team" else None
        self.eval_pin = self.spec.get("evaluation") or {}
        self.eval_id = core_name(self.eval_pin.get("role") or self.eval_pin.get("officer") or "EvalOfficer")
        self._work_spec: dict[str, Any] | None = None

    # identity of what was admitted ------------------------------------------

    def capability_id(self) -> str:
        return core_id(f"{OPERATION_ID}.{str(self.spec['capability']).lower()}", "capability")

    def capability_version(self) -> str:
        return semver(self.spec["capability_version"])

    def capability_digest(self) -> str:
        declared = {key: self.spec.get(key) for key in (
            "capability", "capability_version", "target_kind", "target", "criteria", "budget", "evaluation")}
        declared["pool"] = (self.team or {}).get("pool")
        return core_digest(declared)

    def roles(self) -> dict[str, dict[str, Any]]:
        """Admitted executors: core id -> declaration."""
        found: dict[str, dict[str, Any]] = {}
        if self.spec["target_kind"] == "team":
            found[core_name(self.team["manager"])] = {"role": self.team["manager"], "kind": "manager"}
            for name, entry in self.team["pool"].items():
                found[core_name(name)] = {"role": name, "kind": "teammate", **entry}
        else:
            found[core_name(self.spec["target"])] = {"role": self.spec["target"], "kind": "officer", "runtime": "static"}
        found[self.eval_id] = {"role": self.eval_id, "kind": "evaluation", **self.eval_pin}
        declarations = (self.work.get("runtime") or {}).get("role_declarations") or {}
        for executor, declaration in found.items():
            found[executor] = {**declarations.get(declaration["role"], {}), **declaration}
        return found

    def role_digest(self, declaration: dict[str, Any]) -> str:
        return core_digest({"capability_digest": self.capability_digest(), "declaration": declaration,
                            "source_revision": self.source_revision})

    def profile(self, executor: str, declaration: dict[str, Any]) -> dict[str, Any]:
        return {
            "capability_digest": self.capability_digest(),
            "capability_id": self.capability_id(),
            "capability_version": self.capability_version(),
            "role_digest": self.role_digest(declaration),
            "role_id": executor,
            "role_version": self.capability_version(),
        }

    def criteria_ids(self) -> list[str]:
        return [core_id(criterion["id"], "criterion") for criterion in self.spec["criteria"]]

    def policy(self) -> str:
        policies = [self.spec.get("policy") or "ask"]
        policies += [entry.get("policy", "ask") for entry in (self.work.get("evidence") or {}).values()]
        return strictest(policies)

    def work_spec(self) -> dict[str, Any]:
        if self._work_spec is not None:
            return self._work_spec
        spec = self.spec
        if not spec.get("criteria"):
            raise _Unavailable("WorkSpec: the capability admitted no acceptance criteria")
        if spec["target_kind"] == "team" and not self.team:
            raise _Unavailable("WorkSpec: the Team pool is not admitted yet")
        if spec["target_kind"] not in ("team", "officer"):
            raise _Unavailable(f"WorkSpec: target kind {spec['target_kind']} has no Core form")
        roles = self.roles()
        budget = (self.team or {}).get("budget") or spec.get("budget") or {}
        inputs = spec.get("inputs") or []
        record: dict[str, Any] = {
            "acceptance_criteria": [
                {"criterion_id": core_id(c["id"], "criterion"), "description": c.get("text") or c["id"]}
                for c in spec["criteria"]
            ],
            "admission_receipt_id": core_id(self.work["request_id"], "request"),
            "allowed_executor_ids": list(roles),
            "allowed_profiles": [self.profile(executor, declared) for executor, declared in roles.items()],
            "constraints": (
                f"origin={spec.get('origin')}; strictest input ai_policy={spec.get('policy')}; "
                f"{len(inputs)} admitted input snapshot(s); Vault effects only by the Owner with exact preimage"
            ),
            "continuation_policy": {
                "max_profile_invocations": max(1, int(budget.get("producer_invocations", 1))),
                "max_repeated_gap_assessments": max(1, int(budget.get("same_gap_repeats", 1))),
                "max_segments": max(1, int(budget.get("segments", 1))),
            },
            **self.core_pins(),
            "criteria_revision": 1,
            "delegation_mode": "manager_bounded" if spec["target_kind"] == "team" else "officer_direct",
            "eval_officer_executor_id": self.eval_id,
            "execution_profile": "sequential_graph",
            "explicit_assignments": [],
            "objective": f"{spec['capability']} {spec['command']} (mode {spec.get('mode')})",
            "owner_operation_id": OPERATION_ID,
            "steerable_parameter_ids": [],
            "target_kind": spec["target_kind"],
            "work_id": self.work_id,
            "work_spec_revision": 1,
        }
        if spec["target_kind"] == "team":
            record["team_id"] = core_name(spec["target"])
            record["manager_executor_id"] = core_name(self.team["manager"])
        else:
            record["officer_executor_id"] = core_name(spec["target"])
        record["spec_digest"] = core_digest(record)
        self._work_spec = record
        return record

    def core_pins(self) -> dict[str, str]:
        return dict(self.pins)

    def run_binding(self) -> dict[str, Any]:
        spec = self.work_spec()
        return {"work_run_id": self.run_id, "work_spec_revision": 1, "work_spec_digest": spec["spec_digest"]}

    # artifacts -----------------------------------------------------------------

    def artifact(self, artifact_id: str, hex_digest: str, size: int, media_type: str) -> dict[str, Any]:
        return {
            "artifact_id": artifact_id,
            "availability": "unavailable",
            "classification": classification(self.policy()),
            "content_digest": prefixed(hex_digest),
            "media_type": media_type,
            "owner_operation_id": OPERATION_ID,
            "size_bytes": size,
            "source_revision": self.source_revision,
        }

    def step_reference(self, index: int, step: dict[str, Any]) -> dict[str, Any] | None:
        artifact = step.get("artifact")
        if step.get("status") != "completed" or not artifact:
            return None
        data = self.owner.store.get_blob(artifact["blob"])
        if data is None:
            return None
        artifact_id = f"{self.work_id}.s{index + 1}.{core_name(step['role'])}"
        return self.artifact(artifact_id, sha256(data), len(data), "application/json")

    def segment_references(self, index: int) -> list[dict[str, Any]]:
        segment = self.work["segments"][index]
        refs = [ref for step in segment["steps"] if (ref := self.step_reference(index, step))]
        checkpoint = (segment.get("checkpoint") or {}).get("artifact_digest")
        exact = [ref for ref in refs if checkpoint and ref["content_digest"] == prefixed(checkpoint)]
        return (exact or refs)[-MAX_REFERENCES:]

    def output_references(self) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
        """Reports and proposals this Work wrote, plus its TeamReview."""
        refs: list[dict[str, Any]] = []
        missing: list[dict[str, str]] = []
        created = {
            entry.get("path"): entry.get("result")
            for entry in self.owner.store.read_stream("effects")
            if entry.get("work_id") == self.work["work_id"] and entry.get("kind") == "outcome"
            and entry.get("op") == "create" and entry.get("state") == "applied"
        }
        counts = {"report": 0, "proposal": 0}
        for effect in self.work.get("effects") or []:
            path = effect.get("path") or ""
            if effect.get("state") != "applied" or effect.get("op") not in ("create", "replace"):
                continue
            kind = ("report" if path.startswith(REPORTS_DIR + "/")
                    else "proposal" if path.startswith(PROPOSALS_DIR + "/") else None)
            if kind is None:
                continue
            counts[kind] += 1
            artifact_id = f"{self.work_id}.{kind}.{counts[kind]}"
            hex_digest = effect.get("result") if effect["op"] == "replace" else created.get(path)
            size = self._size(hex_digest, path)
            if hex_digest is None or size is None:
                missing.append({"record": "ArtifactReference", "id": artifact_id,
                                "reason": f"{kind} bytes are no longer observable; size not recorded"})
                continue
            refs.append(self.artifact(artifact_id, hex_digest, size, "text/markdown"))
        review = self.work.get("team_review")
        if review and review.get("status") == "recorded":
            body = canonical({
                "status": review["status"],
                "ratings": review.get("ratings") or {},
                "proposals": [{"text": item.get("text")} for item in review.get("proposals") or []],
                "recorded_at": review.get("recorded_at"),
            })
            refs.append(self.artifact(f"{self.work_id}.team_review", sha256(body), len(body), "application/json"))
        return refs, missing

    def _size(self, hex_digest: str | None, path: str) -> int | None:
        if not hex_digest:
            return None
        data = self.owner.store.get_blob(hex_digest)
        if data is None:
            try:
                data = self.owner.vault.abs(path).read_bytes()
            except (OSError, ValueError):
                return None
            if sha256(data) != hex_digest:
                return None
        return len(data)

    def graph_run_id(self, index: int) -> str:
        return f"{self.work_id}.s{index + 1}"

    def graph_records(self, index: int) -> dict[str, list[dict[str, Any]]]:
        segment = self.work["segments"][index]
        runtime = self.work.get("runtime") or {}
        binding = runtime.get("binding") or {}
        topology = segment.get("runtime_topology")
        if binding.get("engine_id") != "agentfabric.operation.temporal_langgraph" or not topology:
            raise _Unavailable("segment has no retained Temporal/LangGraph registration")
        if not SEMVER_RE.fullmatch(binding.get("engine_version", "")):
            raise _Unavailable("installed engine release version is not a Core semver")
        if runtime.get("binding_digest") != sha256(canonical(binding)):
            raise _Unavailable("retained runtime binding digest does not match its bytes")
        graph_id, revision = f"{self.work_id}.graph", index + 1
        identity = {"graph_id": graph_id, "graph_revision": revision}
        node_ids = topology["node_ids"]
        nodes = [{**identity, "node_id": node, "node_kind": "task", "max_attempts": 1,
                  "input_semantic_term_ids": [], "output_semantic_term_ids": []}
                 for node in node_ids]
        edges = [{**identity, "edge_id": f"edge.{number}", "source_node_id": source,
                  "target_node_id": target, "condition": "always"}
                 for number, (source, target) in enumerate(topology["edges"], 1)]
        graph = {
            **identity, **self.core_pins(), "profile": "graph_enabled",
            "owner_operation_id": OPERATION_ID, "engine_id": binding["engine_id"],
            "engine_version": binding["engine_version"], "node_ids": node_ids,
            "edge_ids": [edge["edge_id"] for edge in edges], "start_node_id": node_ids[0],
            "max_steps": len(node_ids), "input_semantic_term_ids": [], "output_semantic_term_ids": [],
        }
        graph["graph_digest"] = core_digest(graph)
        admitted = self.roles()
        assignments = []
        for node in node_ids:
            executor = self.eval_id if node == "evaluation" else node
            assignments.append({
                **identity, **self.run_binding(), **self.profile(executor, admitted[executor]),
                "admission_receipt_id": core_id(self.work["request_id"], "request"),
                "assignment_generation": segment["runtime_generation"], "node_id": node,
                "executor_id": executor, "owner_operation_id": OPERATION_ID,
                "semantic_version": self.pins["semantic_version"],
                "semantic_digest": self.pins["semantic_digest"],
                "selection_authority": (
                    "bounded_manager" if self.team and node != "evaluation" else "owner_officer"),
            })
        lineage = {
            **identity, **self.core_pins(), **self.run_binding(), "graph_digest": graph["graph_digest"],
            "graph_run_id": self.graph_run_id(index), "segment_index": index + 1,
            "owner_operation_id": OPERATION_ID, "work_id": self.work_id,
            "target_criterion_ids": self.criteria_ids(), "trigger_kind": "initial", "input_references": [],
        }
        if index:
            evaluation = self.run_evaluation(index - 1)
            assessment = self.completion(index - 1, evaluation) if evaluation else None
            remaining = self.remaining(index - 1, assessment) if assessment else None
            if remaining is None:
                raise _Unavailable("successor has no exact admitted RemainingWork")
            lineage.update(
                predecessor_graph_run_id=self.graph_run_id(index - 1), trigger_kind="remaining_work",
                trigger_reference_id=remaining["remaining_work_id"], trigger_digest=core_digest(remaining),
                target_criterion_ids=remaining["unmet_criterion_ids"] + remaining["not_run_criterion_ids"],
                input_references=self.segment_references(index - 1),
            )
        else:
            blob = runtime.get("input_manifest_blob")
            data = self.owner.store.get_blob(blob) if blob else None
            if data is None:
                raise _Unavailable("initial input manifest bytes are not retained")
            lineage["input_references"] = [self.artifact(
                f"{self.work_id}.inputs", sha256(data), len(data), "application/json")]
        lineage["segment_digest"] = core_digest(lineage)
        return {"GraphSpec": [graph], "GraphNode": nodes, "GraphEdge": edges,
                "GraphAssignment": assignments, "GraphSegment": [lineage]}

    # evaluation ----------------------------------------------------------------

    def outcomes(self, index: int) -> dict[str, tuple[str, list[str]]]:
        """criterion id -> (satisfied|unmet|not_run, reasons) for one segment."""
        judgments = ((self.work["segments"][index].get("evaluation") or {}).get("judgments")) or []
        result = {}
        for criterion in self.spec["criteria"]:
            verdicts = [j for j in judgments if j.get("criterion") == criterion["id"]]
            if not verdicts:
                result[criterion["id"]] = ("not_run", [])
            elif all(j.get("verdict") == "satisfied" for j in verdicts):
                result[criterion["id"]] = ("satisfied", [])
            else:
                failing = [j for j in verdicts if j.get("verdict") != "satisfied"]
                reasons = [str(j["reason"]) for j in failing if j.get("reason")]
                targets = sorted({j["target"] for j in failing if j.get("target")})
                if targets:
                    reasons.append("targets: " + ", ".join(targets))
                result[criterion["id"]] = ("unmet", reasons)
        return result

    def run_evaluation(self, index: int) -> dict[str, Any] | None:
        segment = self.work["segments"][index]
        checkpoint = segment.get("checkpoint")
        if not checkpoint or not checkpoint.get("checkpoint_digest") or segment.get("evaluation") is None:
            return None
        outcomes = self.outcomes(index)

        def ids(state: str) -> list[str]:
            return [core_id(c, "criterion") for c, (outcome, _) in outcomes.items() if outcome == state]

        run_id = self.graph_run_id(index)
        return {
            "checkpoint_digest": prefixed(checkpoint["checkpoint_digest"]),
            "criteria_revision": 1,
            "criterion_ids": self.criteria_ids(),
            "evaluation_id": f"{run_id}.evaluation",
            "evaluator_id": self.eval_id,
            "evidence_references": self.segment_references(index),
            "failed_criterion_ids": ids("unmet"),
            "not_run_criterion_ids": ids("not_run"),
            "owner_operation_id": OPERATION_ID,
            "passed_criterion_ids": ids("satisfied"),
            "run_id": run_id,
            **self.run_binding(),
        }

    def completion(self, index: int, evaluation: dict[str, Any]) -> dict[str, Any] | None:
        segment = self.work["segments"][index]
        if segment.get("assessment") is None:
            return None
        outcomes = self.outcomes(index)
        refs = evaluation["evidence_references"]
        criterion_outcomes = []
        for criterion, (outcome, _) in outcomes.items():
            if outcome == "satisfied" and not refs:
                raise _Unavailable(f"CompletionAssessment s{index + 1}: no stored evidence for satisfied {criterion}")
            criterion_outcomes.append({
                "criterion_id": core_id(criterion, "criterion"),
                "evaluation_ids": [] if outcome == "not_run" else [evaluation["evaluation_id"]],
                "evidence_references": refs if outcome != "not_run" else [],
                "outcome": outcome,
            })
        complete = all(outcome == "satisfied" for outcome, _ in outcomes.values())
        return {
            "assessed_at": segment["checkpoint"]["recorded_at"],
            "assessment_id": f"{self.graph_run_id(index)}.assessment",
            "assessor_id": "owner",
            "completion_state": "complete" if complete else "incomplete",
            **self.core_pins(),
            "criteria_revision": 1,
            "criterion_outcomes": criterion_outcomes,
            "graph_run_ids": [self.graph_run_id(i) for i in range(index + 1)],
            "owner_operation_id": OPERATION_ID,
            "work_id": self.work_id,
            **self.run_binding(),
        }

    def remaining(self, index: int, assessment: dict[str, Any]) -> dict[str, Any] | None:
        if assessment["completion_state"] == "complete":
            return None
        outcomes = self.outcomes(index)
        open_items = [(c, outcome, reasons) for c, (outcome, reasons) in outcomes.items() if outcome != "satisfied"]
        gaps = [
            {"criterion_id": core_id(c, "criterion"),
             "summary": ("; ".join(reasons) or ("not evaluated" if outcome == "not_run" else "unmet"))[:MAX_SUMMARY]}
            for c, outcome, reasons in open_items
        ]
        signature = {"criteria_revision": 1, "criterion_ids": sorted(c for c, _, _ in open_items)}
        return {
            "assessment_digest": core_digest(assessment),
            "assessment_id": assessment["assessment_id"],
            **self.core_pins(),
            "criteria_revision": 1,
            "gap_signature": core_digest(signature),
            "gaps": gaps,
            "not_run_criterion_ids": [core_id(c, "criterion") for c, outcome, _ in open_items if outcome == "not_run"],
            "owner_operation_id": OPERATION_ID,
            "remaining_work_id": f"{self.graph_run_id(index)}.remaining",
            "unmet_criterion_ids": [core_id(c, "criterion") for c, outcome, _ in open_items if outcome == "unmet"],
            "work_id": self.work_id,
            **self.run_binding(),
        }

    # all ---------------------------------------------------------------------

    def compile(self) -> dict[str, Any]:
        result: dict[str, Any] = {"work_id": self.work["work_id"], "pins": self.core_pins(),
                                  **{name: [] for name in CORE_TYPES}, "unsupported": []}
        for action_id in self.work.get("actions") or []:
            action = self.owner.store.get("actions", action_id)
            if action and action.get("response"):
                result["HumanActionResponse"].append(action["response"])
        try:
            result["WorkSpec"].append(self.work_spec())
        except (_Unavailable, ValueError, KeyError) as error:
            result["unsupported"].append({"record": "WorkSpec", "reason": str(error)})
            return result
        for index in range(len(self.work["segments"])):
            try:
                for name, records in self.graph_records(index).items():
                    result[name].extend(records)
            except (_Unavailable, KeyError, ValueError) as error:
                result["unsupported"].append({"record": "GraphSpec", "segment": index + 1, "reason": str(error)})
            evaluation = self.run_evaluation(index)
            if evaluation is None:
                continue
            result["RunEvaluation"].append(evaluation)
            try:
                assessment = self.completion(index, evaluation)
            except _Unavailable as error:
                result["unsupported"].append({"record": "CompletionAssessment", "reason": str(error)})
                continue
            if assessment is None:
                continue
            result["CompletionAssessment"].append(assessment)
            remaining = self.remaining(index, assessment)
            if remaining is not None:
                result["RemainingWork"].append(remaining)
        refs, missing = self.output_references()
        result["ArtifactReference"].extend(refs)
        result["unsupported"].extend(missing)
        return result


def compile_work(owner: Any, work: dict[str, Any]) -> dict[str, Any]:
    """Core records for one Work: ``{work_id, pins, <Core type>: [records], unsupported}``."""
    return _Compiler(owner, work).compile()


def work_spec(owner: Any, work: dict[str, Any]) -> dict[str, Any] | None:
    """The Core WorkSpec of a Work, or None when it cannot be compiled honestly."""
    try:
        return _Compiler(owner, work).work_spec()
    except (_Unavailable, ValueError, KeyError):
        return None


# ------------------------------------------------------------------ human actions

CHOSEN_ACTION = {
    "confirm": "approve", "apply": "approve",
    "reject": "reject", "undo": "reject",
    "cancel": "cancel", "pause": "pause", "resume": "resume",
}


def chosen_action(action: str) -> str:
    """Core ``chosen_action`` for a KnowledgeOS action command (unmapped: ``inspect``)."""
    return CHOSEN_ACTION.get(action.rsplit(".", 1)[-1], "inspect")


def human_action_response(
    owner: Any, *, action_id: str, action: str, request_id: str | None, work: dict[str, Any] | None,
    received_at: str,
) -> dict[str, Any]:
    """Core HumanActionResponse for a submitted human action (submission only; the Owner's
    applied/not_applied/unknown outcome stays in the KnowledgeOS action record)."""
    selected = pins(owner.config.root)
    request = owner.store.get("requests", request_id) if request_id else None
    chosen = chosen_action(action)
    spec = work_spec(owner, work) if work else None
    if work is not None:
        target_id = core_id(work["work_id"], "work")
        target_revision = spec["spec_digest"] if spec else core_digest(work["workspec"])
    else:
        target_id = core_id(request_id or action_id, "request")
        target_revision = prefixed(request["digest"]) if request and request.get("digest") else core_digest(action)
    target = {
        "owner_operation_id": OPERATION_ID,
        "target_id": target_id,
        "target_revision": target_revision,
        "action": chosen,
        "scope": action,
        "policy_revision": owner.identity,
        **{key: selected[key] for key in
           ("core_version", "core_digest", "schema_version", "semantic_version", "semantic_digest")},
    }
    response: dict[str, Any] = {
        "actor_id": actor_id((request or {}).get("actor")),
        "chosen_action": chosen,
        "idempotency_key": idempotency_key(request_id or action_id),
        "owner_operation_id": OPERATION_ID,
        "received_at": received_at,
        "request_id": f"{target_id}.human_action",
        "request_revision": 1,
        "response_id": core_id(action_id, "action"),
        "submission_state": "submitted",
        "target_digest": core_digest(target),
    }
    if spec is not None:
        response.update(work_run_id=f"{spec['work_id']}.run", work_spec_revision=1,
                        work_spec_digest=spec["spec_digest"], target_control_revision=1)
    return response

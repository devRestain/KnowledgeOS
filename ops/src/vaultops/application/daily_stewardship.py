"""Owner-only daily corpus coverage and provisional, exact-delta note effects.

The scheduler may request a due check, but only an admitted, independently
assessed Team artifact can supply values. The MCP server never writes notes.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from ..adapters.core import AdmissionError, canonical, digest
from ..adapters.owner_journal import read_regular, replace_exact, stamp
from ..adapters.paths import resolve_beneath
from ..note_engine import (
    CONTEXT_RELATIONS,
    SEMANTIC_RELATIONS,
    NoteEngine,
    parse_frontmatter,
    parse_source_document,
    render_frontmatter,
    resolve_vault_relative_path,
)
from ..projection import source_properties
from .work_methods import WorkMethodCatalog

METHOD_ID = "knowledgeos.daily_corpus_stewardship_team"
_RELATIONS = frozenset((*CONTEXT_RELATIONS, *SEMANTIC_RELATIONS))
_SKIP_TYPES = frozenset({"home", "system", "proposal"})
_PREFIX = "daily.enrichment."


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _fail(code: str, message: str) -> None:
    raise AdmissionError(code, message)


class DailyStewardship:
    """A bounded owner effect composed over the existing journal and Vault."""

    def __init__(self, application: Any, *, timezone: str = "Asia/Seoul") -> None:
        self.app = application
        self.zone = ZoneInfo(timezone)
        self.engine = NoteEngine.from_root(application.roots)

    def _registry_digest(self) -> str:
        self.engine = NoteEngine.from_root(self.app.roots)
        return digest(canonical(self.engine.blueprint["relation_registry"]))

    def _policy_digest(self) -> str:
        return digest(canonical(self.app.policy["mcp_document_policy"]))

    def _eligible_fields(self, note_type: str) -> set[str]:
        predicates = self.engine.blueprint["relation_registry"]["canonical_predicates"]
        semantic = {field for field, rule in predicates.items()
                    if note_type in rule["allowed_subject_types"]}
        return {"tags", *CONTEXT_RELATIONS, *semantic}

    def _notes(self) -> list[tuple[str, bytes, dict[str, Any]]]:
        result = []
        for path in sorted(self.app.roots.vault.rglob("*.md")):
            relative = path.relative_to(self.app.roots.vault).as_posix()
            if (relative in {"Home.md", "Mobile.md"}
                    or relative.startswith(("99_System/", "01_AI_Review/"))
                    or any(part.startswith(".") for part in Path(relative).parts)):
                continue
            try:
                safe = resolve_vault_relative_path(self.app.roots.vault, relative)
                raw = read_regular(safe)
                if raw is None:
                    continue
                properties = source_properties(relative, raw, engine=self.engine)
            except (ValueError, OSError, UnicodeError):
                continue
            if (properties.get("type") in _SKIP_TYPES
                    or properties.get("sensitivity") == "confidential"
                    or properties.get("ai_policy") not in {"ask", "remote_ok", "local_only"}
                    or properties.get("ai_policy") == "local_only" and not self.app.identity.local_execution):
                continue
            result.append((relative, raw, properties))
        return result

    def _held_paths(self) -> list[str]:
        held = []
        for path in sorted(self.app.roots.vault.rglob("*.md")):
            relative = path.relative_to(self.app.roots.vault).as_posix()
            if (relative in {"Home.md", "Mobile.md"}
                    or relative.startswith(("99_System/", "01_AI_Review/"))
                    or any(part.startswith(".") for part in Path(relative).parts)):
                continue
            try:
                raw = read_regular(resolve_vault_relative_path(self.app.roots.vault, relative))
                if raw is None:
                    continue
                properties = source_properties(relative, raw, engine=self.engine)
            except (ValueError, OSError, UnicodeError):
                held.append(relative)
                continue
            if properties.get("ai_policy") not in {"ask", "remote_ok", "local_only", "deny"}:
                held.append(relative)
        return held

    @staticmethod
    def _work_request(reference: dict[str, Any], method: dict[str, Any],
                      empty_fields: list[str], registry: str, policy: str) -> dict[str, Any]:
        key = digest(canonical({"source": reference, "method": method,
                                "registry": registry, "policy": policy}))
        return {"request_id": key, "source_reference": reference,
                "method_contract_ref": method, "empty_fields": empty_fields}

    def due_check(self, *, limit: int = 50,
                  exclude_request_ids: frozenset[str] = frozenset()) -> dict[str, Any]:
        """Record a local-day scan, excluding caller-owned durable admissions."""
        self.app.guard("apply")
        if not 1 <= limit <= 50:
            _fail("INVALID_ARGUMENT", "Daily scan limit must be between 1 and 50")
        now = self.app.clock()
        day = now.astimezone(self.zone).date().isoformat()
        registry = self._registry_digest()
        policy = self._policy_digest()
        method = WorkMethodCatalog.load(self.app.roots.control).reference(METHOD_ID)
        candidates = []
        excluded = []
        with self.app.journal.writer() as writer:
            for relative, raw, properties in self._notes():
                key = _PREFIX + hashlib.sha256(relative.encode()).hexdigest()
                previous = writer.value["intents"].get(key, {})
                source_digest = "sha256:" + _sha(raw)
                unchanged = (previous.get("source_digest") == source_digest
                        and previous.get("registry_digest") == registry
                        and previous.get("policy_digest") == policy
                        and previous.get("method_contract_ref") == method)
                if unchanged and previous.get("state") in {"no_evidence", "completed"}:
                    continue
                if unchanged and previous.get("state") == "started":
                    # Return the same source-bound request after a restart.
                    # A scheduled caller owns admission and deduplicates Work.
                    request = self._work_request(self.app.reference(relative), method,
                                                 previous["empty_fields"], registry, policy)
                    if request["request_id"] in exclude_request_ids:
                        excluded.append(request["request_id"])
                        continue
                    if len(candidates) >= limit:
                        break
                    candidates.append(request)
                    continue
                if any(item.get("path") == relative and (
                           item.get("review_status") in {"pending", "conflict"}
                           or item.get("state") == "unknown")
                       for item in writer.value["intents"].values() if isinstance(item, dict)):
                    continue
                empty = sorted(field for field in self._eligible_fields(properties["type"])
                               if properties.get(field) in (None, [], ""))
                if not empty:
                    writer.value["intents"][key] = {
                        "state": "completed", "recorded_at": stamp(now), "path": relative,
                        "source_digest": source_digest, "registry_digest": registry,
                        "policy_digest": policy, "day": day, "empty_fields": [],
                        "method_contract_ref": method,
                    }
                    continue
                reference = self.app.reference(relative)
                request = self._work_request(reference, method, empty, registry, policy)
                if request["request_id"] in exclude_request_ids:
                    excluded.append(request["request_id"])
                    continue
                if len(candidates) >= limit:
                    break
                writer.value["intents"][key] = {
                    "state": "started", "recorded_at": stamp(now), "path": relative,
                    "source_digest": source_digest, "registry_digest": registry,
                    "policy_digest": policy, "day": day, "empty_fields": empty,
                    "method_contract_ref": method,
                }
                candidates.append(request)
            scan_key = "daily.scan." + day
            writer.value["intents"][scan_key] = {
                "state": "completed" if len(candidates) < limit else "started",
                "recorded_at": stamp(now), "day": day, "candidate_count": len(candidates),
            }
            writer.commit(now)
        return {"day": day, "work_requests": candidates, "held_paths": self._held_paths(),
                "scan_complete": len(candidates) < limit, "excluded_request_ids": excluded}

    def _work_artifact(self, work_run_id: str, candidate: dict[str, Any],
                       source_reference: dict[str, Any], *,
                       snapshot: dict[str, Any] | None = None) -> None:
        """Validate the result under its retained grant, never a new registration."""
        from .owner_authority import validate_owner_authority

        journal = self.app.journal.read() if snapshot is None else snapshot
        record = journal["intents"].get("exops.work." + work_run_id)
        if (not isinstance(record, dict) or record.get("application_state") != "recorded"
                or record.get("run", {}).get("state") != "completed"
                or record.get("spec", {}).get("team_id") != "corpus-stewardship"
                or record.get("spec", {}).get("method_contract_ref")
                != WorkMethodCatalog.load(self.app.roots.control).reference(METHOD_ID)
                or record.get("method_source_reference") != source_reference):
            _fail("SCOPE_DENIED", "Candidate lacks completed, source-bound Stewardship Work")
        validate_owner_authority(self.app, journal, record)
        artifact = record["run"].get("result_reference")
        if (not isinstance(artifact, dict)
                or artifact not in record.get("artifact_references", {}).values()):
            _fail("SCOPE_DENIED", "Evaluated result is not an admitted Work artifact")
        selected = [item for item in record.get("submitted_artifacts", [])
                    if item.get("content_digest") == artifact.get("content_digest")]
        if len(selected) != 1:
            _fail("RESOURCE_UNAVAILABLE", "Evaluated Work artifact cannot be resolved exactly")
        path = self.app.resolve_reference(selected[0], proposal=True)
        raw = read_regular(resolve_beneath(self.app.roots.vault, path))
        if raw is None or digest(raw) != artifact["content_digest"]:
            _fail("RESOURCE_STALE", "Evaluated candidate artifact bytes changed")
        body = parse_frontmatter(raw.decode("utf-8")).body
        marker = "```json\n"
        if marker not in body or "\n```" not in body.split(marker, 1)[1]:
            _fail("INVALID_ARGUMENT", "Candidate artifact has no structured content")
        payload = json.loads(body.split(marker, 1)[1].split("\n```", 1)[0])
        if (payload.get("artifact_kind") != "daily_enrichment"
                or json.loads(payload.get("content", "null")) != candidate):
            _fail("DIGEST_MISMATCH", "Candidate differs from the evaluated artifact")
        from .temporal_contract import native_team_route

        if native_team_route(record["spec"]) is not None:
            from .metadata_work import validate_candidate
            from .work import record_digest

            assessment = record.get("assessment", {})
            outcomes = assessment.get("criterion_outcomes", [])
            spec, run = record["spec"], record["run"]
            if (assessment.get("completion_state") != "complete"
                    or record_digest(assessment) != run.get("latest_assessment_digest")
                    or assessment.get("assessor_id") != spec.get("eval_officer_executor_id")
                    or assessment.get("assessor_id") in [spec.get("manager_executor_id"),
                                                         *spec.get("allowed_executor_ids", [])]
                    or not outcomes or any(item.get("outcome") != "satisfied" for item in outcomes)):
                _fail("ACCEPTANCE_REQUIRED", "Native daily candidate lacks exact independent assessment")
            validate_candidate(self.app, source_reference, candidate, method_id=METHOD_ID)

    def _target_types(self) -> dict[str, str]:
        targets = {}
        for relative, _, properties in self._notes():
            targets[relative.removesuffix(".md")] = properties["type"]
            targets[Path(relative).stem] = properties["type"]
        return targets

    def record_no_evidence(self, work_run_id: str, candidate: dict[str, Any], *,
                           authorize: Any = None) -> dict[str, Any]:
        """Suppress repeated inference until source, policy or registry changes."""
        self.app.guard("apply")
        if (set(candidate) != {"source_reference", "registry_digest", "fields", "evidence"}
                or candidate["fields"] != {} or candidate["evidence"] != {}
                or candidate["registry_digest"] != self._registry_digest()):
            _fail("INVALID_ARGUMENT", "No-evidence result must be an exact empty candidate")
        source = candidate["source_reference"]
        relative = self.app.resolve_reference(source)
        self._work_artifact(work_run_id, candidate, source)
        key = _PREFIX + hashlib.sha256(relative.encode()).hexdigest()
        now = self.app.clock()
        with self.app.journal.writer() as writer:
            self.app.guard("apply")
            if authorize is not None and not authorize():
                _fail("ACTION_DENIED", "Daily effect authorization was revoked")
            self.app.resolve_reference(source)
            self._work_artifact(work_run_id, candidate, source, snapshot=writer.value)
            if candidate["registry_digest"] != self._registry_digest():
                _fail("RESOURCE_STALE", "Relation registry changed")
            record = writer.value["intents"].get(key)
            if (not isinstance(record, dict) or record.get("source_digest") != source["content_digest"]
                    or record.get("registry_digest") != candidate["registry_digest"]
                    or record.get("policy_digest") != self._policy_digest()
                    or record.get("state") != "started"):
                _fail("RESOURCE_STALE", "Coverage record changed before no-evidence result")
            record["state"] = "no_evidence"
            record["policy_digest"] = self._policy_digest()
            record["recorded_at"] = stamp(now)
            writer.commit(now)
        return {"path": relative, "status": "no_evidence", "source_digest": source["content_digest"]}

    def record_candidate(self, work_run_id: str, candidate: dict[str, Any], *,
                         authorize: Any = None) -> dict[str, Any]:
        """Write only validated empty fields after a completed Team assessment."""
        self.app.guard("apply")
        if set(candidate) != {"source_reference", "registry_digest", "fields", "evidence"}:
            _fail("INVALID_ARGUMENT", "Candidate shape is invalid")
        source = candidate["source_reference"]
        self.app.core.validate("ResourceReference", source)
        effect_id = _PREFIX + hashlib.sha256((work_run_id + source["content_digest"]).encode()).hexdigest()
        if effect_id in self.app.journal.read()["intents"]:
            _fail("OUTCOME_UNKNOWN", "Existing candidate effect requires reconciliation")
        relative = self.app.resolve_reference(source)
        if candidate["registry_digest"] != self._registry_digest():
            _fail("RESOURCE_STALE", "Relation registry changed")
        self._work_artifact(work_run_id, candidate, source)
        path = resolve_vault_relative_path(self.app.roots.vault, relative)
        before = read_regular(path)
        if before is None or digest(before) != source["content_digest"]:
            _fail("RESOURCE_STALE", "Source changed before provisional apply")
        document = parse_source_document(before.decode("utf-8"))
        properties = dict(document.properties)
        source_policy = source_properties(relative, before, engine=self.engine)
        if (source_policy.get("sensitivity") == "confidential"
                or source_policy.get("ai_policy") not in {"ask", "remote_ok", "local_only"}
                or source_policy.get("ai_policy") == "local_only" and not self.app.identity.local_execution):
            _fail("SCOPE_DENIED", "Document policy denies local enrichment")
        fields, evidence = candidate["fields"], candidate["evidence"]
        if (not isinstance(fields, dict) or not fields
                or set(fields) - self._eligible_fields(source_policy["type"])
                or not isinstance(evidence, dict) or set(evidence) != set(fields)):
            _fail("INVALID_ARGUMENT", "Candidate fields or evidence are invalid")
        original_fields = {field: {"present": field in document.properties,
                                   "value": document.properties.get(field)} for field in fields}
        coverage_key = _PREFIX + hashlib.sha256(relative.encode()).hexdigest()
        coverage = self.app.journal.read()["intents"].get(coverage_key)
        if (not isinstance(coverage, dict) or coverage.get("state") != "started"
                or coverage.get("source_digest") != source["content_digest"]
                or coverage.get("registry_digest") != candidate["registry_digest"]
                or coverage.get("policy_digest") != self._policy_digest()
                or not set(fields) <= set(coverage.get("empty_fields", []))):
            _fail("RESOURCE_STALE", "Candidate differs from the admitted daily coverage")
        for field, values in fields.items():
            if (properties.get(field) not in (None, [], "") or not isinstance(values, list)
                    or not 1 <= len(values) <= 10 or any(not isinstance(v, str) or not v for v in values)
                    or len(set(values)) != len(values)):
                _fail("CONFLICT", "Candidate may fill only one empty list with bounded values")
            quotes = evidence[field]
            if isinstance(quotes, dict):
                if set(quotes) != set(values):
                    _fail("INVALID_ARGUMENT", "Every daily value needs exact source-body evidence")
                groups = list(quotes.values())
            else:
                groups = [quotes]
            if any(not isinstance(group, list) or not group
                   or any(not isinstance(q, str) or not q.strip() or q not in document.body for q in group)
                   for group in groups):
                _fail("INVALID_ARGUMENT", "Every field needs an exact source-body quote")
            properties[field] = values
        tags = list(properties.get("tags", []))
        if "ai/pending" in tags:
            _fail("CONFLICT", "A pending marker already exists without this owner effect")
        tags.append("ai/pending")
        properties["tags"] = tags
        now = self.app.clock()
        properties["modified"] = stamp(now)
        result = self.engine.validate_property_changes(
            properties, {*fields, "tags", "modified"}, relative, target_types=self._target_types())
        if not result.passed:
            _fail("INVALID_ARGUMENT", "Candidate violates the note or relation registry")
        after = render_frontmatter(properties, document.body).encode("utf-8")
        with self.app.journal.writer() as writer:
            self.app.guard("apply")
            if authorize is not None and not authorize():
                _fail("ACTION_DENIED", "Daily effect authorization was revoked")
            self.app.resolve_reference(source)
            self._work_artifact(work_run_id, candidate, source, snapshot=writer.value)
            if (candidate["registry_digest"] != self._registry_digest()
                    or coverage.get("policy_digest") != self._policy_digest()):
                _fail("RESOURCE_STALE", "Daily registry or policy changed before effect")
            if writer.value["intents"].get(coverage_key) != coverage:
                _fail("RESOURCE_STALE", "Daily coverage changed during candidate validation")
            if effect_id in writer.value["intents"]:
                _fail("OUTCOME_UNKNOWN", "Existing candidate effect requires reconciliation")
            if any(item.get("path") == relative and (
                       item.get("review_status") in {"pending", "conflict"}
                       or item.get("state") == "unknown")
                   for item in writer.value["intents"].values() if isinstance(item, dict)):
                _fail("CONFLICT", "Another exact delta for this note still awaits review")
            writer.value["intents"][effect_id] = {
                "state": "unknown", "recorded_at": stamp(now), "path": relative,
                "before_digest": digest(before), "after_digest": digest(after),
                "source_digest": source["content_digest"], "fields": fields,
                "original_fields": original_fields, "evidence": evidence,
                "review_status": "pending", "work_run_id": work_run_id,
                "registry_digest": candidate["registry_digest"],
            }
            writer.commit(now)
            writer.fence()
            replace_exact(path, before, after)
            self.app.fault_hook("daily_canonical_written")
            writer.fence()
            writer.value["intents"][effect_id]["state"] = "completed"
            writer.value["intents"][coverage_key]["state"] = "completed"
            writer.value["receipts"][effect_id] = {
                "recorded_at": stamp(now), "unresolved": False,
                "before_digest": digest(before), "after_digest": digest(after),
                "execution": {"outcome": "succeeded", "result_digest": digest(after)},
            }
            writer.commit(now)
        return {"effect_id": effect_id, "path": relative, "review_status": "pending",
                "before_digest": digest(before), "after_digest": digest(after), "fields": fields}

    def pending(self) -> list[dict[str, Any]]:
        self.app.guard("inspect")
        return [{"effect_id": key, "path": item["path"], "fields": item["fields"],
                 "before_digest": item["before_digest"], "after_digest": item["after_digest"],
                 "review_status": item["review_status"]}
                for key, item in sorted(self.app.journal.read()["intents"].items())
                if key.startswith(_PREFIX) and isinstance(item, dict)
                and item.get("state") == "completed"
                and item.get("review_status") in {"pending", "conflict"}]

    def read_pending(self, effect_id: str) -> dict[str, Any]:
        self.app.guard("inspect")
        record = self.app.journal.read()["intents"].get(effect_id)
        if (not isinstance(record, dict) or not effect_id.startswith(_PREFIX)
                or record.get("state") != "completed"
                or record.get("review_status") not in {"pending", "conflict"}):
            _fail("NOT_FOUND", "Pending daily delta is unavailable")
        raw = read_regular(resolve_vault_relative_path(self.app.roots.vault, record["path"]))
        if raw is None:
            _fail("RESOURCE_UNAVAILABLE", "Pending note is unavailable")
        note = parse_frontmatter(raw.decode("utf-8"))
        return {"effect_id": effect_id, "path": record["path"],
                "review_status": record["review_status"],
                "before_digest": record["before_digest"],
                "proposed_after_digest": record["after_digest"],
                "current_digest": digest(raw), "original_fields": record["original_fields"],
                "added_fields": record["fields"], "evidence": record["evidence"],
                "current_fields": {key: note.properties.get(key) for key in record["fields"]},
                "body_excerpt": note.body[:4096], "body_truncated": len(note.body) > 4096}

    def reconcile(self, effect_id: str) -> dict[str, Any]:
        """Resolve an interrupted exact write from observed bytes; never replay it."""
        self.app.guard("recover")
        now = self.app.clock()
        with self.app.journal.writer() as writer:
            record = writer.value["intents"].get(effect_id)
            if (not isinstance(record, dict) or not effect_id.startswith(_PREFIX)
                    or record.get("state") != "unknown"):
                _fail("CONFLICT", "No unresolved daily effect exists")
            raw = read_regular(resolve_vault_relative_path(self.app.roots.vault, record["path"]))
            observed = digest(raw) if raw is not None else None
            if observed == record["after_digest"]:
                record["state"] = "completed"
                if effect_id.endswith((".confirm", ".reject")):
                    source = writer.value["intents"].get(effect_id.rsplit(".", 1)[0])
                    if not isinstance(source, dict):
                        _fail("STATE_INVALID", "Review source record is unavailable")
                    decision = "confirm" if effect_id.endswith(".confirm") else "reject"
                    source["review_status"] = "confirmed" if decision == "confirm" else "rejected"
                    self._retain_reviewed_coverage(writer.value, source, record["before_digest"], observed)
                    writer.value["decisions"][effect_id] = {
                        "decision": decision, "effect_id": effect_id.rsplit(".", 1)[0],
                        "recorded_at": stamp(now), "before_digest": record["before_digest"],
                        "after_digest": record["after_digest"],
                    }
                else:
                    coverage_key = _PREFIX + hashlib.sha256(record["path"].encode()).hexdigest()
                    coverage = writer.value["intents"].get(coverage_key)
                    if isinstance(coverage, dict):
                        coverage["state"] = "completed"
                writer.value["receipts"][effect_id] = {
                    "recorded_at": stamp(now), "unresolved": False,
                    "execution": {"outcome": "succeeded", "result_digest": observed},
                }
                state = "completed"
            elif observed == record["before_digest"]:
                record["state"] = "not_applied"
                state = "not_applied"
            else:
                _fail("OUTCOME_UNKNOWN", "Note bytes match neither side of interrupted effect")
            writer.commit(now)
        return {"effect_id": effect_id, "state": state, "observed_digest": observed}

    def pending_relation_fields(self) -> set[tuple[str, str]]:
        return {(item["path"], field) for item in self.app.journal.read()["intents"].values()
                if isinstance(item, dict) and item.get("state") == "completed"
                and item.get("review_status") == "pending" for field in item.get("fields", {})
                if field in _RELATIONS}

    def review(self, effect_id: str, decision: str) -> dict[str, Any]:
        """Review the exact delta without treating owner-only bytes as new evidence."""
        if decision not in {"confirm", "reject"}:
            _fail("INVALID_ARGUMENT", "Review decision must be confirm or reject")
        self.app.guard("approve" if decision == "confirm" else "reject")
        review_id = effect_id + "." + decision
        if review_id in self.app.journal.read()["intents"]:
            _fail("OUTCOME_UNKNOWN", "Existing review effect requires reconciliation")
        now = self.app.clock()
        with self.app.journal.writer() as writer:
            record = writer.value["intents"].get(effect_id)
            if (not isinstance(record, dict) or not effect_id.startswith(_PREFIX)
                    or record.get("state") != "completed" or record.get("review_status") != "pending"):
                _fail("CONFLICT", "No pending exact delta is available")
            path = resolve_vault_relative_path(self.app.roots.vault, record["path"])
            before = read_regular(path)
            if before is None:
                _fail("RESOURCE_UNAVAILABLE", "Source note was removed")
            document = parse_frontmatter(before.decode("utf-8"))
            properties = dict(document.properties)
            if ("ai/pending" not in properties.get("tags", [])
                    or any(properties.get(field) != (
                        values + ["ai/pending"] if field == "tags" else values)
                        for field, values in record["fields"].items())):
                record["review_status"] = "conflict"
                writer.commit(now)
                _fail("CONFLICT", "User changed a provisional value; review manually")
            properties["tags"] = [tag for tag in properties["tags"] if tag != "ai/pending"]
            if decision == "reject":
                for field in record["fields"]:
                    original = record["original_fields"][field]
                    if original["present"]:
                        properties[field] = original["value"]
                    else:
                        properties.pop(field, None)
            properties["modified"] = stamp(now)
            after = render_frontmatter(properties, document.body).encode("utf-8")
            writer.value["intents"][review_id] = {
                "state": "unknown", "recorded_at": stamp(now), "path": record["path"],
                "before_digest": digest(before), "after_digest": digest(after),
            }
            writer.commit(now)
            writer.fence()
            replace_exact(path, before, after)
            writer.fence()
            writer.value["intents"][review_id]["state"] = "completed"
            record["review_status"] = "confirmed" if decision == "confirm" else "rejected"
            self._retain_reviewed_coverage(writer.value, record, digest(before), digest(after))
            writer.value["decisions"][review_id] = {
                "decision": decision, "effect_id": effect_id, "recorded_at": stamp(now),
                "before_digest": digest(before), "after_digest": digest(after),
            }
            writer.commit(now)
        return {"effect_id": effect_id, "decision": decision,
                "path": record["path"], "after_digest": digest(after)}

    @staticmethod
    def _retain_reviewed_coverage(journal: dict[str, Any], effect: dict[str, Any],
                                  before_digest: str, after_digest: str) -> None:
        # Do not hide an intervening user edit: only the exact provisional
        # postimage establishes that review alone changed the source bytes.
        if before_digest != effect["after_digest"]:
            return
        key = _PREFIX + hashlib.sha256(effect["path"].encode()).hexdigest()
        coverage = journal["intents"].get(key)
        if (isinstance(coverage, dict) and coverage.get("state") == "completed"
                and coverage.get("source_digest") == effect["source_digest"]):
            coverage["source_digest"] = after_digest

    def publish_index(self, *, authorize: Callable[[], None] | None = None) -> dict[str, Any]:
        """Publish one snapshot, with optional host authority at pointer switches."""
        self.app.guard("apply")
        if authorize is not None:
            authorize()
        from ..projection import (
            ProjectionConflict,
            ProjectionError,
            ProjectionRead,
            _publish_generation,
            build_projection,
        )
        from ..search_modes import build_vector_index

        port = self.app.handlers.vector_port
        if port is None or getattr(port, "trusted_local", False) is not True:
            _fail("INDEX_UNAVAILABLE", "A trusted local embedding service is not bound")
        try:
            build = build_projection(self.app.roots)
        except (ProjectionConflict, ProjectionError) as error:
            raise AdmissionError("INDEX_INVALID", "Lexical candidate is invalid") from error
        candidate = ProjectionRead(pointer={}, manifest=build.manifest,
                                   notes=build.notes, edges=build.edges)
        vector = build_vector_index(self.app.roots, port, projection=candidate, authorize=authorize)
        # Explicit maintenance exports retain their publication-time source CAS;
        # live reads independently materialize each current document.
        try:
            changed, generation_root, notes_path, edges_path = _publish_generation(
                self.app.roots, build, authorize=authorize)
        except (ProjectionConflict, ProjectionError) as error:
            raise AdmissionError("INDEX_STALE", "Lexical source changed before publication") from error
        lexical = {"generation_id": build.generation_id,
                   "source_snapshot_sha256": build.source_snapshot_sha256,
                   "generation_root": generation_root, "notes_path": notes_path,
                   "edges_path": edges_path, "pointer_swapped": changed}
        return {"lexical": lexical, "vector": vector}

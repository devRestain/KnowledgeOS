"""Promote two independently evaluated note candidates to one reviewable update.

The candidate artifacts are never canonical. This owner boundary checks their
exact Work lineage, source bytes, registry and evidence before creating a C19
Pending proposal. It never applies the target note.
"""

from __future__ import annotations

import base64
import hashlib
import json
import uuid
from datetime import timedelta
from typing import Any

from ..adapters.artifacts import create_artifact
from ..adapters.core import AdmissionError, canonical, digest
from ..adapters.owner_journal import read_regular, stamp
from ..adapters.paths import resolve_beneath
from ..note_engine import NoteEngine, parse_frontmatter, render_frontmatter
from ..template_engine import render_note_template
from .work import record_digest


def _fail(code: str, message: str) -> None:
    raise AdmissionError(code, message)


def _proposal_id(seed: str) -> str:
    return str(uuid.UUID(bytes=hashlib.sha256(seed.encode()).digest()[:16], version=4))


def _candidate(app: Any, record: dict[str, Any], kind: str,
               source: dict[str, Any], registry_digest: str) -> dict[str, Any]:
    run = record["run"]
    assessment = record.get("assessment")
    outcomes = assessment.get("criterion_outcomes") if isinstance(assessment, dict) else None
    if (record.get("application_state") != "recorded" or run.get("state") != "completed"
            or not isinstance(assessment, dict) or assessment.get("completion_state") != "complete"
            or record_digest(assessment) != run.get("latest_assessment_digest")
            or not isinstance(outcomes, list) or not outcomes
            or any(not isinstance(item, dict) or item.get("outcome") != "satisfied"
                   for item in outcomes)
            or record.get("method_source_reference") != source):
        _fail("ACCEPTANCE_REQUIRED", "Both source-bound Work results need passing independent evaluation")
    formal = run.get("result_reference")
    if not isinstance(formal, dict) or formal not in record.get("artifact_references", {}).values():
        _fail("SCOPE_DENIED", "Evaluated result is not an admitted Work artifact")
    selected = [item for item in record.get("submitted_artifacts", [])
                if item.get("content_digest") == formal.get("content_digest")]
    if len(selected) != 1:
        _fail("RESOURCE_UNAVAILABLE", "Evaluated Work artifact cannot be resolved exactly")
    path = app.resolve_reference(selected[0], proposal=True)
    raw = read_regular(resolve_beneath(app.roots.vault, path))
    if raw is None or digest(raw) != formal["content_digest"]:
        _fail("RESOURCE_STALE", "Evaluated artifact bytes changed")
    body = parse_frontmatter(raw.decode("utf-8")).body
    marker = "```json\n"
    if marker not in body or "\n```" not in body.split(marker, 1)[1]:
        _fail("INVALID_ARGUMENT", "Work artifact has no structured candidate")
    try:
        envelope = json.loads(body.split(marker, 1)[1].split("\n```", 1)[0])
        candidate = json.loads(envelope["content"])
    except (KeyError, TypeError, ValueError) as error:
        raise AdmissionError("INVALID_ARGUMENT", "Work candidate JSON is invalid") from error
    if (envelope.get("artifact_kind") != kind or not isinstance(candidate, dict)
            or set(candidate) != {"source_reference", "registry_digest", "fields", "evidence"}
            or candidate["source_reference"] != source
            or candidate["registry_digest"] != registry_digest
            or not isinstance(candidate["fields"], dict)
            or not isinstance(candidate["evidence"], dict)
            or set(candidate["fields"]) != set(candidate["evidence"])):
        _fail("INVALID_ARGUMENT", "Candidate source, registry, or evidence shape differs")
    return candidate


def auto_prepare_metadata_proposal(exops: Any, request_id: str, *, actor: Any) -> dict[str, Any]:
    """Finish a metadata group after its second trusted Worker result is recorded.

    Work completion is already durable when this runs. A failed promotion is
    recorded for review without converting that Work receipt into an unknown
    Worker outcome. The explicit prepare route can reconcile a later retry.
    """
    app = exops.application
    key = "exops.template." + request_id
    group = app.journal.read()["intents"].get(key)
    if (not isinstance(group, dict) or group.get("template_id") != "note_metadata"
            or group.get("actor_id") != actor.actor_id):
        return {"state": "unavailable", "code": "ADMISSION_REQUIRED"}
    if group.get("canonical_proposal_reference") is not None:
        return {"state": "available", "code": None}
    receipts = group.get("receipt", {}).get("receipts", [])
    if len(receipts) != 2:
        return {"state": "unavailable", "code": "ADMISSION_REQUIRED"}
    intents = app.journal.read()["intents"]
    members = [intents.get("exops.work." + item["work_run_id"]) for item in receipts]
    if any(not isinstance(member, dict) for member in members):
        return {"state": "unavailable", "code": "RESOURCE_UNAVAILABLE"}
    if any(member.get("application_state") != "recorded" for member in members):
        return {"state": "waiting", "code": None}
    if any(member["run"].get("state") != "completed" for member in members):
        outcome = {"state": "needs_attention", "code": "ACCEPTANCE_REQUIRED"}
    else:
        try:
            prepared = prepare_metadata_proposal(exops, request_id, actor=actor)
            return {"state": "available" if prepared.get("proposal_reference") else "no_change",
                    "code": None}
        except (AdmissionError, OSError, ValueError) as error:
            outcome = {"state": "needs_attention",
                       "code": getattr(error, "code", "OUTCOME_UNKNOWN")}
    with app.journal.writer() as writer:
        live = writer.value["intents"].get(key)
        if (isinstance(live, dict) and live.get("actor_id") == actor.actor_id
                and live.get("canonical_proposal_reference") is None):
            live["metadata_promotion"] = outcome
            writer.commit(app.clock())
    return outcome


def prepare_metadata_proposal(exops: Any, request_id: str, *, actor: Any) -> dict[str, Any]:
    """Create one exact-hash Pending update_note after both Team results pass."""
    exops._guard(actor, "submit_work")
    app = exops.application
    app.guard("propose")
    group_key = "exops.template." + request_id
    group = app.journal.read()["intents"].get(group_key)
    if (not isinstance(group, dict) or group.get("template_id") != "note_metadata"
            or group.get("actor_id") != actor.actor_id):
        _fail("NOT_FOUND", "Metadata request group is unavailable")
    scope = group["scope"]
    source_path = scope["path"]
    source = app.reference(source_path)
    if source["source_revision"] != scope["content_sha256"]:
        _fail("RESOURCE_STALE", "Selected note changed after Work admission")
    app._mcp_source_policy(source_path)
    previous = group.get("canonical_proposal_reference")
    if previous is not None:
        app.resolve_reference(previous, proposal=True)
        return {"status": "NO_OP", "proposal_reference": previous, "replayed": True}
    receipts = group["receipt"]["receipts"]
    if len(receipts) != 2:
        _fail("ADMISSION_REQUIRED", "Metadata request has no complete two-Team group")
    records = [app.journal.read()["intents"].get("exops.work." + item["work_run_id"])
               for item in receipts]
    if any(not isinstance(record, dict) for record in records):
        _fail("RESOURCE_UNAVAILABLE", "A grouped WorkRun is unavailable")
    if [record["spec"].get("team_id") for record in records] != ["corpus-stewardship", "relation-ontology"]:
        _fail("ADMISSION_REQUIRED", "Grouped Team identities changed")
    engine = NoteEngine.from_root(app.roots)
    registry_digest = digest(canonical(engine.blueprint["relation_registry"]))
    metadata = _candidate(app, records[0], "note_metadata", source, registry_digest)
    relations = _candidate(app, records[1], "note_relations", source, registry_digest)
    raw = read_regular(resolve_beneath(app.roots.vault, source_path))
    if raw is None or digest(raw) != source["content_digest"]:
        _fail("RESOURCE_STALE", "Selected note changed during candidate review")
    document = parse_frontmatter(raw.decode("utf-8"))
    properties = dict(document.properties)
    permitted = set(engine.blueprint["property_registry"])
    relation_fields = set(engine.blueprint["relation_registry"]["canonical_predicates"])
    relation_fields.update(engine.blueprint["relation_registry"].get("context_predicates", {}))
    if set(metadata["fields"]) - (permitted - relation_fields | {"tags"}):
        _fail("SCOPE_DENIED", "Stewardship suggested an undeclared property or relation")
    if set(relations["fields"]) - relation_fields:
        _fail("SCOPE_DENIED", "Ontology Team suggested an undeclared relation")
    if set(metadata["fields"]) & set(relations["fields"]):
        _fail("CONFLICT", "The two Team candidates overlap on one field")
    evidence = {**metadata["evidence"], **relations["evidence"]}
    changes = {**metadata["fields"], **relations["fields"]}
    if not changes:
        with app.journal.writer() as writer:
            live_group = writer.value["intents"].get(group_key)
            if live_group != group or app.reference(source_path) != source:
                _fail("RESOURCE_STALE", "Metadata group or source changed before no-change commit")
            live_group["metadata_promotion"] = {"state": "no_change", "code": None}
            writer.commit(app.clock())
        return {"status": "NO_CHANGE", "proposal_reference": None}
    for field, values in changes.items():
        if (not isinstance(values, list) or not 1 <= len(values) <= 10
                or any(not isinstance(value, str) or not value for value in values)
                or len(set(values)) != len(values)):
            _fail("INVALID_ARGUMENT", "Candidate values must be bounded, unique text lists")
        value_evidence = evidence[field]
        if (not isinstance(value_evidence, dict) or set(value_evidence) != set(values)):
            _fail("EVIDENCE_REQUIRED", "Every candidate value needs its own evidence")
        for value in values:
            quotes = value_evidence[value]
            if (not isinstance(quotes, list) or not quotes
                    or any(not isinstance(quote, str) or not quote.strip()
                           or quote not in document.body for quote in quotes)):
                _fail("EVIDENCE_REQUIRED", "Every candidate value needs an exact source-body quote")
        current = properties.get(field)
        if current not in (None, [], ""):
            if field != "tags" and field not in relation_fields:
                _fail("CONFLICT", "A populated property cannot be replaced")
            if not isinstance(current, list):
                _fail("CONFLICT", "A populated list has an incompatible shape")
            if any(value in current for value in values):
                _fail("CONFLICT", "Candidate repeats a populated value")
            properties[field] = [*current, *values]
        else:
            properties[field] = values
    properties["modified"] = stamp(app.clock())
    target_types = app.daily_stewardship()._target_types()
    validated = engine.validate_properties(properties, source_path, target_types=target_types)
    if not validated.passed:
        _fail("INVALID_ARGUMENT", "Candidate violates the Blueprint note or relation registry")
    target = render_frontmatter(properties, document.body)
    proposal_id = _proposal_id(request_id + source["content_digest"] + digest(canonical(changes)))
    proposal_path = f"01_AI_Review/Pending/Metadata {proposal_id}.md"
    manifest = {"action": "update_note", "target_path": source_path,
                "expected_target_sha256": scope["content_sha256"], "target_markdown": target}
    body = ("# Metadata and relation proposal\n\n"
            "The two evaluated Team artifacts are evidence; this proposal needs human approval and a separate apply.\n\n"
            "## Field evidence\n\n```json\n"
            + json.dumps({"changes": changes, "evidence": evidence,
                          "work_run_ids": [item["work_run_id"] for item in receipts]},
                         ensure_ascii=False, sort_keys=True, indent=2)
            + "\n```\n\n<!-- vaultops:proposal-manifest\n"
            + json.dumps(manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            + "\n-->\n")
    content = render_note_template("T01_AI_Proposal.md", {
        "title": f"Metadata {proposal_id}", "id": proposal_id, "proposal_id": proposal_id,
        "created": str(document.properties["created"]), "modified": str(document.properties["created"]),
        "sensitivity": document.properties.get("sensitivity", "personal"),
        "source_hashes": [f"{source_path}|{source['content_digest']}"], "body": body,
    }).markdown.encode("utf-8")
    if len(content) > 65536:
        _fail("INVALID_ARGUMENT", "Metadata Pending proposal exceeds its bound")
    engine.typed_note(proposal_path, content.decode("utf-8"))
    effect_id = "metadata." + proposal_id
    now = app.clock()
    with app.journal.writer() as writer:
        live_group = writer.value["intents"].get(group_key)
        if live_group != group:
            _fail("RESOURCE_STALE", "Metadata group changed before proposal commit")
        if app.reference(source_path) != source:
            _fail("RESOURCE_STALE", "Selected note changed before proposal commit")
        if effect_id in writer.value["intents"]:
            _fail("OUTCOME_UNKNOWN", "Existing proposal intent requires reconciliation")
        effect = {"effect_id": effect_id, "owner_operation_id": "knowledgeos",
                  "content_digest": digest(content), "generation": writer.generation,
                  "fencing_token": f"generation.g{writer.generation}", "issued_at": stamp(now),
                  "expires_at": stamp(now + timedelta(hours=12))}
        app.core.validate("ExecutionIntent", effect)
        intent = {**effect, "state": "pending", "recorded_at": stamp(now),
                  "source_path": source_path, "source_digest": source["content_digest"],
                  "artifact_path": proposal_path, "frozen_artifact": base64.b64encode(content).decode(),
                  "human_request_id": "human." + proposal_id, "acceptance_state": "not_evaluated",
                  "action": "update_note", "template_request_id": request_id,
                  "metadata_changes": changes, "metadata_evidence": evidence}
        writer.value["intents"][effect_id] = intent
        writer.commit(now)
        writer.fence()
        if app.reference(source_path) != source:
            _fail("RESOURCE_STALE", "Selected note changed before Pending dispatch")
        intent["state"] = "unknown"
        writer.commit(now)
        writer.fence()
        created = create_artifact(app.roots.vault, proposal_path, content)
        writer.fence()
        if app.reference(source_path) != source:
            _fail("RESOURCE_STALE", "Selected note changed after Pending dispatch")
        inspection, _ = app.handlers.inspect(app.roots, proposal_path=proposal_path)
        evaluation, acceptance = app._evaluate(inspection, effect_id, digest(content))
        if acceptance != "pass":
            _fail("ACCEPTANCE_REQUIRED", "Pending update_note did not pass owner inspection")
        intent["state"] = "completed"
        intent["acceptance_state"] = acceptance
        request = app._human_request(intent, now)
        writer.value["human_requests"][request["request_id"]] = request
        writer.value["receipts"][effect_id] = {"recorded_at": stamp(now),
            "execution": app._receipt(intent, now, "succeeded", digest(content)),
            "evaluation": evaluation}
        reference = app.reference(proposal_path, proposal=True)
        live_group["canonical_proposal_reference"] = reference
        live_group["metadata_promotion"] = {"state": "available", "code": None}
        for item in receipts:
            writer.value["intents"]["exops.work." + item["work_run_id"]]["canonical_proposal_reference"] = reference
        writer.value["history"].append({"event": "metadata_proposal_created",
                                        "effect_id": effect_id,
                                        "sequence": writer.value["revision"] + 1})
        writer.commit(now)
        return {"status": "PASS", "proposal_reference": reference, "created": created,
                "canonical_target_changed": False}

"""Concrete source/registry-bound metadata, ontology and daily candidate Teams."""
from __future__ import annotations

import json
from types import SimpleNamespace

from ..adapters.core import canonical, digest
from ..interfaces.mcp_server import _convert_report, _operation
from ..note_engine import NoteEngine, parse_source_document
from ..retrieval import _chunks_for_note
from .native_team import (
    NativeTeamExecutor,
    NativeTeamPolicy,
    native_admission,
    native_digest,
    require,
    source_bytes,
)
from .read_tools import KnowledgeReadTools
from .work import record_digest
from .work_tools import TeamToolSession

METADATA_METHOD_ID = "knowledgeos.note_metadata_team"
RELATION_METHOD_ID = "knowledgeos.relation_ontology_team"
DAILY_METHOD_ID = "knowledgeos.daily_corpus_stewardship_team"
_MODULES = ("metadata_work.py", "metadata_temporal.py", "metadata_workflow.py", "native_team_temporal.py",
            "metadata_proposals.py", "daily_stewardship.py", "work_tools.py", "read_tools.py", "mcp_effects.py")


def _policy(namespace, method, team, roles, stages, tools):
    return NativeTeamPolicy(namespace, method, team, roles,
        {"criterion_id": "criterion.native_" + namespace,
         "description": "Two exact native stages read the admitted source and registry before a receipt-bound candidate artifact; independent assessment checks each new value and source-body quote, declared fields, registry and target types without overwriting populated values or asserting uncertain relation truth."},
        stages, tools, _MODULES)


METADATA_POLICY = _policy("note_metadata", METADATA_METHOD_ID, "corpus-stewardship",
    ("knowledgeos.coverage_reader", "knowledgeos.enrichment_proposer"),
    ("coverage_evidence", "metadata_candidate"),
    {"coverage_evidence": ("knowledge_read", "semantic_registry_read"), "metadata_candidate": ("artifact_submit",)})
RELATION_POLICY = _policy("relation_ontology", RELATION_METHOD_ID, "relation-ontology",
    ("knowledgeos.relation_analyst", "knowledgeos.ontology_curator"),
    ("relation_evidence", "ontology_alignment"),
    {"relation_evidence": ("knowledge_read", "link_context"), "ontology_alignment": ("semantic_registry_read", "artifact_submit")})
DAILY_POLICY = _policy("daily_corpus", DAILY_METHOD_ID, "corpus-stewardship",
    ("knowledgeos.coverage_reader", "knowledgeos.enrichment_proposer"),
    ("coverage_evidence", "enrichment_candidate"),
    {"coverage_evidence": ("knowledge_read", "semantic_registry_read"), "enrichment_candidate": ("artifact_submit",)})
POLICIES = {p.method_id: p for p in (METADATA_POLICY, RELATION_POLICY, DAILY_POLICY)}
KINDS = {METADATA_METHOD_ID: "note_metadata", RELATION_METHOD_ID: "note_relations", DAILY_METHOD_ID: "daily_enrichment"}


def metadata_native_digest(binding, method_id=METADATA_METHOD_ID):
    require(method_id in POLICIES, "Unknown metadata native method")
    return native_digest(POLICIES[method_id], binding)


def metadata_admission(app, binding, source_reference, *, method_id=METADATA_METHOD_ID, **identities):
    require(method_id in POLICIES, "Unknown metadata native method")
    return native_admission(app, binding, source_reference, policy=POLICIES[method_id], **identities)


def relation_link_evidence(record):
    """Resolve the immutable actual relation read, preserving target revisions."""
    execution = record.get("relation_ontology_relation_evidence", {})
    require(execution.get("evidence_digest") == record_digest({
        "request": execution.get("request"), "response": execution.get("response")}),
        "Relation source/link evidence changed")
    calls = execution["response"].get("tool_calls", [])
    require(bool(calls) and calls[-1].get("name") == "link_context"
            and calls[-1]["result"].get("operation") == "link_context",
            "Relation result lacks its actual link-context read")
    return calls[-1]["result"]["data"]


def validate_candidate(app, source, candidate, *, method_id, link_evidence=None):
    """Pure exact candidate validation shared by native assessment and owner promotion."""
    require(method_id in POLICIES, "Unknown candidate method")
    path, raw = source_bytes(app, source)
    engine = NoteEngine.from_root(app.roots)
    registry = engine.blueprint["relation_registry"]
    registry_digest = digest(canonical(registry))
    require(isinstance(candidate, dict) and set(candidate) == {"source_reference", "registry_digest", "fields", "evidence"}
            and candidate["source_reference"] == source and candidate["registry_digest"] == registry_digest
            and isinstance(candidate["fields"], dict) and isinstance(candidate["evidence"], dict)
            and set(candidate["fields"]) == set(candidate["evidence"]), "Candidate source, registry or field evidence differs")
    document = parse_source_document(raw.decode("utf-8"))
    properties = dict(document.properties)
    relations = set(registry["canonical_predicates"]) | set(registry.get("context_predicates", {}))
    if method_id == RELATION_METHOD_ID:
        permitted = relations
    elif method_id == DAILY_METHOD_ID:
        permitted = app.daily_stewardship()._eligible_fields(properties.get("type", "knowledge"))
    else:
        permitted = set(engine.blueprint["property_registry"]) - relations | {"tags"}
        # Identity, ownership/privacy, timestamps and schema/control fields are not enrichment.
        permitted -= {"id", "type", "title", "created", "modified", "schema_version", "ai_policy", "sensitivity", "status"}
    require(set(candidate["fields"]) <= permitted, "Candidate uses undeclared or protected fields")
    for field, values in candidate["fields"].items():
        require(isinstance(values, list) and 1 <= len(values) <= 10
                and all(isinstance(v, str) and 0 < len(v) <= 256 for v in values)
                and len(set(values)) == len(values), "Candidate values must be bounded unique text")
        evidence = candidate["evidence"][field]
        require(isinstance(evidence, dict) and set(evidence) == set(values), "Every new value needs exact evidence")
        old = properties.get(field)
        if method_id == DAILY_METHOD_ID:
            require(old in (None, "", []), "Daily enrichment cannot modify any populated field")
        elif old not in (None, "", []):
            require(field == "tags" or field in relations, "Populated properties cannot be overwritten")
            require(isinstance(old, list) and not set(old) & set(values), "Candidate conflicts with populated list")
        for value in values:
            quotes = evidence[value]
            require(isinstance(quotes, list) and 1 <= len(quotes) <= 10
                    and all(isinstance(q, str) and q.strip() and q in document.body for q in quotes),
                    "Each new value needs a nonempty exact source-body quote")
            token = value.removeprefix("[[").removesuffix("]]").split("|", 1)[0]
            require(any(token.casefold() in q.casefold() for q in quotes), "Quote does not identify its proposed value")
            if field in relations:
                require(value.startswith("[[") and value.endswith("]]"), "Relations require exact wikilink values")
                require(any(field in q and value in q for q in quotes), "Source does not state this predicate and target")
                if link_evidence is not None:
                    targets = {app.resolve_reference(row["target_reference"]).removesuffix(".md")
                               for row in link_evidence.get("outgoing", []) if isinstance(row.get("target_reference"), dict)}
                    targets |= {target.rsplit("/", 1)[-1] for target in tuple(targets)}
                    require(token in targets, "Unresolved mentions remain candidates, not relation assertions")
        properties[field] = [*old, *values] if isinstance(old, list) else values
    validated = engine.validate_property_changes(
        properties, candidate["fields"], path, target_types=app.daily_stewardship()._target_types())
    require(validated.passed, "Candidate violates declared property, predicate or target-type semantics")
    return candidate


class MetadataTeamExecutor(NativeTeamExecutor):
    def __init__(self, exops, *, runner, method_id=METADATA_METHOD_ID):
        require(method_id in POLICIES, "Unknown metadata Team")
        super().__init__(exops, runner=runner, policy=POLICIES[method_id])

    def request_context(self, record, stage):
        app = self.exops.application
        source = record["method_source_reference"]
        path, raw = source_bytes(app, source)
        reader = KnowledgeReadTools(app, allowed_path=path)
        _, notes = reader._eligible(selected_path=path)
        chunks = [chunk.locator for chunk in _chunks_for_note(reader._note(source, notes))]
        require(0 < len(chunks) <= 20, "Metadata source exceeds native read bound")
        engine = NoteEngine.from_root(app.roots)
        registry_digest = digest(canonical(engine.blueprint["relation_registry"]))
        document = parse_source_document(raw.decode("utf-8"))
        return {"source_chunks": chunks, "registry_digest": registry_digest,
                "artifact_kind": KINDS[self.policy.method_id],
                "existing_properties": document.properties,
                "evidence": None if stage == self.policy.stages[0] else self.evidence(record, self.policy.stages[0])}

    def fresh_read(self, record, call):
        app = self.exops.application
        run, spec = record["run"], record["spec"]
        index = 0 if call["name"] in {"knowledge_read", "link_context"} else self.policy.stages.index(
            "ontology_alignment") if self.policy.method_id == RELATION_METHOD_ID else 0
        session = TeamToolSession(app, run["work_run_id"], spec["allowed_executor_ids"][index], run["active_graph_run_id"], self.exops)
        actual, error = _convert_report(SimpleNamespace(services=app), call["name"], call["arguments"],
                                        _operation(app, call["name"], call["arguments"], session=session)())
        require(not error and {k: v for k, v in actual.items() if k != "request_id"}
                == {k: v for k, v in call["result"].items() if k != "request_id"}, "Native read differs from fresh Owner evidence")
        require(actual.get("status") in {"ok", "empty"} and actual.get("effect") == "read_only", "Read stage produced an effect")
        return actual["data"]

    def validate_stage(self, record, request, response):
        calls = response["tool_calls"]
        if request["stage_id"] == self.policy.stages[0]:
            last = "link_context" if self.policy.method_id == RELATION_METHOD_ID else "semantic_registry_read"
            require(calls[-1]["name"] == last and all(c["name"] == "knowledge_read" for c in calls[:-1]),
                    "Evidence stage must read all source pages before exact registry/link context")
            position, offset, pages = 0, 0, []
            for call in calls[:-1]:
                args = call["arguments"]
                require(position < len(request["source_chunks"]) and args.get("resource_reference") == request["source_reference"]
                        and args.get("chunk_locator") == request["source_chunks"][position] and args.get("offset", 0) == offset,
                        "Native source pages skipped, repeated or escaped")
                data = self.fresh_read(record, call)
                require(bool(data["text"]), "Empty source page")
                pages.append(data)
                offset += len(data["text"])
                if not data["truncated"]:
                    position, offset = position + 1, 0
            require(position == len(request["source_chunks"]), "Not every exact source page was read")
            args = calls[-1]["arguments"]
            require(args == ({"resource_reference": request["source_reference"], "limit": 20} if last == "link_context"
                             else {"category": "relations"}), "Wrong registry or link scope")
            data = self.fresh_read(record, calls[-1])
            if last == "semantic_registry_read":
                require(digest(canonical(data["definitions"])) == request["registry_digest"], "Read registry differs")
            return {"pages": pages, last: data}
        expected = ["semantic_registry_read", "artifact_submit"] if self.policy.method_id == RELATION_METHOD_ID else ["artifact_submit"]
        require([c["name"] for c in calls] == expected, "Candidate stage tool sequence differs")
        if len(calls) == 2:
            require(calls[0]["arguments"] == {"category": "relations"}, "Wrong ontology registry")
            data = self.fresh_read(record, calls[0])
            require(digest(canonical(data["definitions"])) == request["registry_digest"], "Ontology registry differs")
        call = calls[-1]
        args, envelope = call["arguments"], call["result"]
        require(set(args) <= {"artifact_kind", "content", "idempotency_key"}
                and args.get("artifact_kind") == request["artifact_kind"] and isinstance(args.get("content"), str)
                and envelope.get("status") == "created" and envelope.get("execution_outcome") == "completed"
                and envelope.get("effect") == "proposal_created" and envelope.get("data", {}).get("apply_allowed") is False,
                "Candidate has no exact noncanonical artifact effect")
        candidate = json.loads(args["content"])
        self.validate_result(record, candidate)
        return candidate

    def result_call(self, execution):
        calls = execution["response"]["tool_calls"]
        require(calls[-1]["name"] == "artifact_submit", "Metadata result lacks artifact effect")
        return calls[-1]

    def validate_result(self, record, candidate):
        link = None
        if self.policy.method_id == RELATION_METHOD_ID:
            link = self.evidence(record, self.policy.stages[0])["link_context"]
        return validate_candidate(self.exops.application, record["method_source_reference"], candidate,
                                  method_id=self.policy.method_id, link_evidence=link)

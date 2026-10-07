"""Bounded, policy checked evidence views for the KnowledgeOS MCP interface."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping
from typing import Any

from ..adapters.core import AdmissionError
from ..adapters.owner_journal import read_regular
from ..adapters.paths import resolve_beneath
from ..base_dashboard import evaluate_base_view
from ..blueprint import load_yaml_file
from ..projection import load_current_projection
from ..retrieval import _chunks_for_note, _filter_notes

_CHECKBOX = re.compile(r"^\s*[-*+]\s+\[([ xX])\]\s+(.+)$")
_DUE = re.compile(r"(?:📅\s*|\bdue::\s*)(\d{4}-\d{2}-\d{2})")
_LINK = re.compile(r"\[\[[^\]]+\]\]")
_MAX_TEXT = 4096


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


class KnowledgeReadTools:
    """Use one verified projection and the owner policy for every returned note."""

    def __init__(self, application: Any, *, allowed_path: str | None = None) -> None:
        self.application = application
        self.allowed_path = allowed_path

    def _eligible(self, *, selected_path: str | None = None) -> tuple[Any, dict[str, Mapping[str, Any]]]:
        app = self.application
        app.guard("retrieve")
        projection = load_current_projection(app.roots, verify_sources=True)
        retrieval = load_yaml_file(app.roots.control / "ops/policies/retrieval.yaml")["retrieval"]
        included, *_ = _filter_notes(
            projection, retrieval, scope=None, path_prefix=None, include_types=None,
            include_review=False, limit=50, hops=0,
        )
        notes = {str(note["path"]): note for note in included}
        explicit = self.allowed_path or selected_path
        if explicit is not None:
            selected = next((note for note in projection.notes
                             if str(note["path"]) == explicit), None)
            if selected is None:
                raise AdmissionError("RESOURCE_UNAVAILABLE", "Selected source is unavailable")
            app._source_policy(explicit)
            app._mcp_source_policy(explicit)
            if self.allowed_path is not None:
                notes = {explicit: selected}
            else:
                notes[explicit] = selected
        if app.policy["mcp_document_policy"]["ask"] != "allow_configured_local_mcp":
            notes = {path: note for path, note in notes.items()
                     if note["properties"].get("ai_policy") != "ask"}
        if not app.identity.local_execution:
            notes = {
                path: note for path, note in notes.items()
                if note["properties"].get("ai_policy") != "local_only"
            }
        notes = {path: note for path, note in notes.items()
                 if note["properties"].get("sensitivity") != "confidential"
                 and note["properties"].get("ai_policy") != "deny"}
        return projection, notes

    def _note(self, reference: Mapping[str, Any], notes: Mapping[str, Mapping[str, Any]]) -> Mapping[str, Any]:
        path = self.application.resolve_reference(dict(reference))
        note = notes.get(path)
        if note is None:
            raise AdmissionError("SCOPE_DENIED", "Note is outside the configured MCP corpus")
        if note["content_hash"] != reference["content_digest"][7:]:
            raise AdmissionError("RESOURCE_STALE", "Note bytes differ from the current projection")
        return note

    def knowledge_read(self, reference: Mapping[str, Any], locator: str, *, offset: int = 0,
                       max_chars: int = _MAX_TEXT) -> dict[str, Any]:
        selected_path = self.application.resolve_reference(dict(reference))
        _, notes = self._eligible(selected_path=selected_path)
        note = self._note(reference, notes)
        if not isinstance(offset, int) or offset < 0 or not 1 <= max_chars <= _MAX_TEXT:
            raise AdmissionError("INVALID_ARGUMENT", "Excerpt bounds are invalid")
        chunks = {chunk.locator: chunk for chunk in _chunks_for_note(note)}
        chunk = chunks.get(locator)
        if chunk is None:
            raise AdmissionError("NOT_FOUND", "Requested source locator is unavailable")
        excerpt = chunk.text[offset:offset + max_chars]
        return {
            "resource_reference": dict(reference), "note_id": note["id"],
            "chunk_id": chunk.chunk_id, "chunk_locator": locator,
            "chunk_hash": chunk.chunk_hash, "offset": offset,
            "text": excerpt, "excerpt_sha256": _sha256(excerpt),
            "truncated": offset + max_chars < len(chunk.text),
        }

    def artifact_read(self, reference: Mapping[str, Any], *, offset: int = 0,
                      max_chars: int = _MAX_TEXT) -> dict[str, Any]:
        app = self.application
        app.guard("inspect")
        if not isinstance(offset, int) or offset < 0 or not 1 <= max_chars <= _MAX_TEXT:
            raise AdmissionError("INVALID_ARGUMENT", "Artifact bounds are invalid")
        path = app.resolve_reference(dict(reference), proposal=True)
        raw = read_regular(resolve_beneath(app.roots.vault, path))
        if raw is None or len(raw) > 256 * 1024:
            raise AdmissionError("RESOURCE_UNAVAILABLE", "Pending artifact is unavailable")
        text = raw.decode("utf-8")
        excerpt = text[offset:offset + max_chars]
        return {"artifact_reference": dict(reference), "offset": offset,
                "text": excerpt, "excerpt_sha256": _sha256(excerpt),
                "truncated": offset + max_chars < len(text)}

    def base_view_query(self, base: str, view: str, *, filters: Mapping[str, Any] | None = None,
                        limit: int = 20) -> dict[str, Any]:
        _, notes = self._eligible()
        if not isinstance(limit, int) or not 1 <= limit <= 20:
            raise AdmissionError("INVALID_ARGUMENT", "Base result limit is invalid")
        requested = dict(filters or {})
        declared = set(load_yaml_file(self.application.roots.control / "blueprint/blueprint.yaml")["property_registry"])
        if len(requested) > 4 or any(not isinstance(key, str) or key not in declared for key in requested):
            raise AdmissionError("INVALID_ARGUMENT", "Base filter is outside the declared property subset")
        rows = evaluate_base_view(self.application.roots, base, view)
        selected = []
        for row in rows:
            path = row["path"]
            if path not in notes:
                continue
            if any(notes[path]["properties"].get(key) != value for key, value in requested.items()):
                continue
            selected.append({"resource_reference": self.application.reference(path),
                             "columns": row["columns"]})
            if len(selected) == limit:
                break
        return {"base": base, "view": view, "rows": selected, "count": len(selected)}

    def link_context(self, reference: Mapping[str, Any], *, limit: int = 20) -> dict[str, Any]:
        selected_path = self.application.resolve_reference(dict(reference))
        projection, notes = self._eligible(selected_path=selected_path)
        source = self._note(reference, notes)
        if not isinstance(limit, int) or not 1 <= limit <= 20:
            raise AdmissionError("INVALID_ARGUMENT", "Link result limit is invalid")
        path = str(source["path"])
        outgoing = []
        incoming = []
        for edge in projection.edges:
            subject = str(edge["subject_path"])
            target = str(edge["object_path"])
            if subject not in notes or target not in notes:
                continue
            row = {"predicate": edge["predicate"], "locator": edge["source_locator"],
                   "source_reference": self.application.reference(subject),
                   "target_reference": self.application.reference(target)}
            if subject == path:
                outgoing.append(row)
            if target == path:
                incoming.append(row)
        for candidate in source["wikilinks"]:
            target = candidate.get("resolved_path")
            if target in notes:
                outgoing.append({"predicate": "wikilink", "locator": candidate["locator"],
                                 "source_reference": dict(reference),
                                 "target_reference": self.application.reference(target)})
        for candidate_path, candidate in notes.items():
            if candidate_path == path:
                continue
            if any(link.get("resolved_path") == path for link in candidate["wikilinks"]):
                incoming.append({"predicate": "wikilink", "locator": "/body",
                                 "source_reference": self.application.reference(candidate_path),
                                 "target_reference": dict(reference)})
        names = {str(source["title"]), *[str(item) for item in source["properties"].get("aliases", [])]}
        names = {name.casefold() for name in names if len(name) >= 3}
        mentions = []
        for candidate_path, candidate in notes.items():
            if candidate_path == path or any(link.get("resolved_path") == path for link in candidate["wikilinks"]):
                continue
            body = _LINK.sub("", str(candidate.get("body", ""))).casefold()
            if any(name in body for name in names):
                mentions.append({"source_reference": self.application.reference(candidate_path),
                                 "status": "unlinked_candidate"})
            if len(mentions) >= limit:
                break
        return {"resource_reference": dict(reference), "outgoing": outgoing[:limit],
                "backlinks": incoming[:limit], "unlinked_mentions": mentions}

    def task_query(self, *, path_prefix: str | None = None, limit: int = 20) -> dict[str, Any]:
        _, notes = self._eligible(selected_path=path_prefix if path_prefix and path_prefix.endswith(".md") else None)
        if not isinstance(limit, int) or not 1 <= limit <= 20:
            raise AdmissionError("INVALID_ARGUMENT", "Task result limit is invalid")
        rows = []
        for path, note in sorted(notes.items()):
            if path_prefix and not (path == path_prefix or path.startswith(path_prefix.rstrip("/") + "/")):
                continue
            for number, line in enumerate(str(note.get("body", "")).splitlines(), 1):
                match = _CHECKBOX.match(line)
                if match is None:
                    continue
                due = _DUE.search(match.group(2))
                rows.append({"resource_reference": self.application.reference(path),
                             "locator": f"/body/line/{number}", "line_sha256": _sha256(line),
                             "text": line[:512], "completed": match.group(1).lower() == "x",
                             "due": due.group(1) if due else None})
                if len(rows) >= limit:
                    return {"tasks": rows, "count": len(rows), "truncated": True}
        return {"tasks": rows, "count": len(rows), "truncated": False}

    def semantic_registry_read(self, *, category: str) -> dict[str, Any]:
        self.application.guard("inspect")
        blueprint = load_yaml_file(self.application.roots.control / "blueprint/blueprint.yaml")
        if category == "types":
            data = blueprint["note_types"]
        elif category == "relations":
            data = blueprint["relation_registry"]
        else:
            raise AdmissionError("INVALID_ARGUMENT", "Unknown registry category")
        return {"category": category, "definitions": data}

    def citation_verify(self, citations: list[Mapping[str, Any]]) -> dict[str, Any]:
        if len(citations) > 20:
            raise AdmissionError("INVALID_ARGUMENT", "Citation count exceeds the bound")
        observations = []
        for item in citations:
            result = self.knowledge_read(item["resource_reference"], item["chunk_locator"])
            observations.append({"resource_reference": item["resource_reference"],
                                 "chunk_locator": item["chunk_locator"],
                                 "valid": result["chunk_hash"] == item["chunk_hash"]})
        return {"citations": observations, "all_valid": all(item["valid"] for item in observations),
                "truth_evaluated": False}

    def gateway_evidence_read(self, receipt_id: str) -> dict[str, Any]:
        self.application.guard("inspect")
        for record in self.application.journal.read()["gateway"].values():
            if record["receipt"]["receipt_id"] == receipt_id:
                return {"receipt": record["receipt"], "envelope": record["envelope"],
                        "payload_digest": record["payload_digest"]}
        raise AdmissionError("NOT_FOUND", "Gateway admission receipt is unavailable")

    def vault_diagnose(self, reference: Mapping[str, Any]) -> dict[str, Any]:
        selected_path = self.application.resolve_reference(dict(reference))
        _, notes = self._eligible(selected_path=selected_path)
        note = self._note(reference, notes)
        return {"resource_reference": dict(reference), "note_id": note["id"],
                "type": note["type"], "status": note["status"],
                "link_count": len(note["wikilinks"]), "content_hash": note["content_hash"],
                "canonical_changed": False}

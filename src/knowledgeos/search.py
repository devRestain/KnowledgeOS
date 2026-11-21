"""Capability C3: the Owner's search service (keyword, semantic, related).

The index is rebuildable in-memory Runtime data refreshed per changed document: each
refresh stats every Markdown file and reparses only files whose identity changed. ``deny``
notes are never indexed: only their file identity is kept so they are not reread.

Audiences: ``people`` (Thin Client, console) see reports; ``role`` (Team read tools)
excludes reports unless the user changed their type. ``deny`` notes are never visible.
Semantic search needs a ready local vector index (``knowledgeos.semantic``); without one, or
when the query cannot be embedded, it answers ``unavailable`` and never substitutes keyword
results. Semantic neighbors join the related labels. Every semantic result passes the same
audience and policy filter as keyword results. When System One is configured, semantic neighbors
are vetted before they are labeled (``SearchIndex._vet``).
"""

from __future__ import annotations

import json
import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol

from .notes import WIKILINK_RE, Note
from .policy import approved, effective
from .standardize import normalize_tags
from .system_one import SystemOne, SystemOneUnavailable, note_input, predicate
from .util import sha256
from .vault import PROPOSALS_DIR, SYSTEM_ROOT, Vault, VaultPathError

EXCERPT_CHARS = 160
CANDIDATE_CHARS = 600
VERDICT_CACHE = 4096

log = logging.getLogger("knowledgeos.search")


@dataclass(frozen=True)
class Doc:
    """One indexed note. ``deny`` docs carry only path, file identity and policy."""

    path: str
    stamp: tuple[int, int, int, int]
    mtime: float
    policy: str
    sha: str | None = None
    note_id: str | None = None
    type: str | None = None
    status: str | None = None
    title: str = ""
    tags: tuple[str, ...] = ()
    links: tuple[str, ...] = ()
    lines: tuple[str, ...] = ()
    folded: tuple[str, ...] = ()
    body_start: int = 1
    valid: bool = True
    conflict: bool = False


@dataclass(frozen=True)
class SemanticHit:
    """The best-matching chunk of one note: its 1-based inclusive line range and cosine score."""

    path: str
    start: int
    end: int
    score: float


class SemanticUnavailable(Exception):
    """The semantic backend cannot answer right now, for example its embedding service is unreachable."""


class SemanticBackend(Protocol):
    """A local embedding index. ``accept`` is the caller's audience and policy filter; a backend
    applies it before counting results toward ``limit`` and never returns a stale or rejected note."""

    def ready(self) -> bool: ...

    def search(
        self, query: str, docs: dict[str, Doc], limit: int, accept: Callable[[Doc], bool]
    ) -> list[SemanticHit]: ...

    related_min_probability: float

    def neighbors(
        self, path: str, docs: dict[str, Doc], limit: int, accept: Callable[[Doc], bool]
    ) -> list[SemanticHit]: ...

    def status(self, docs: dict[str, Doc]) -> dict[str, Any]: ...


Allow = Callable[[Doc], bool]


def _stamp(stat: Any) -> tuple[int, int, int, int]:
    return (stat.st_mtime_ns, stat.st_size, stat.st_ino, stat.st_ctime_ns)


class SearchIndex:
    def __init__(self, vault: Vault, observe: Callable[[str], None] | None = None):
        self.vault = vault
        self.observe = observe
        self.semantic_backend: SemanticBackend | None = None
        self._verdicts: dict[tuple[str, str, str], float] = {}
        self._docs: dict[str, Doc] = {}
        self._by_name: dict[str, list[str]] = {}
        self._lock = threading.RLock()

    # ------------------------------------------------------------ index

    def _make_doc(self, path: str, note: Note, stamp: tuple[int, int, int, int], mtime: float) -> Doc:
        policy = effective(note)
        if policy == "deny":
            return Doc(path=path, stamp=stamp, mtime=mtime, policy="deny")
        lines = tuple(note.text.splitlines())
        return Doc(
            path=path,
            stamp=stamp,
            mtime=mtime,
            policy=policy,
            sha=note.sha,
            note_id=note.note_id,
            type=note.type,
            status=note.status,
            title=note.title,
            tags=tuple(normalize_tags(note.data.get("tags")) or ()),
            links=tuple(sorted(note.links())),
            lines=lines,
            folded=tuple(line.casefold() for line in lines),
            body_start=note.body_start_line,
            valid=note.valid,
            conflict=note.conflict,
        )

    def refresh(self) -> dict[str, Doc]:
        """Bring the index up to date with the Vault and return a snapshot of all docs."""
        with self._lock:
            seen: set[str] = set()
            changed = False
            for path in self.vault.iter_paths():
                try:
                    stat = self.vault.stat(path)
                    if stat is None:
                        continue
                    stamp = _stamp(stat)
                    current = self._docs.get(path)
                    if current is not None and current.stamp == stamp:
                        seen.add(path)
                        continue
                    note = self.vault.read(path)
                    if note is None:
                        continue
                except VaultPathError:
                    continue
                seen.add(path)
                doc = self._make_doc(path, note, stamp, stat.st_mtime)
                self._docs[path] = doc
                changed = True
                if doc.policy != "deny" and self.observe is not None:
                    self.observe(path)
            for path in set(self._docs) - seen:
                del self._docs[path]
                changed = True
            if changed:
                by_name: dict[str, list[str]] = {}
                for path in sorted(self._docs):
                    by_name.setdefault(path.rsplit("/", 1)[-1].removesuffix(".md").casefold(), []).append(path)
                self._by_name = by_name
            return dict(self._docs)

    def rebuild(self) -> dict[str, Any]:
        with self._lock:
            self._docs.clear()
            docs = self.refresh()
        return {"documents": sum(1 for doc in docs.values() if doc.policy != "deny")}

    def verify(self) -> dict[str, Any]:
        """Compare the index with the files on disk without changing either."""
        with self._lock:
            current = dict(self._docs)
        indexed = dict(current)
        stale, missing = [], []
        files = 0
        for path in self.vault.iter_paths():
            try:
                note = self.vault.read(path)
            except VaultPathError:
                continue
            if note is None:
                continue
            files += 1
            doc = indexed.pop(path, None)
            if effective(note) == "deny":
                continue
            if doc is None:
                missing.append(path)
            elif doc.sha != sha256(note.raw):
                stale.append(path)
        extra = sorted(path for path, doc in indexed.items() if doc.policy != "deny")
        return {
            "ok": not (stale or missing or extra),
            "documents": sum(1 for doc in current.values() if doc.policy != "deny"),
            "files": files,
            "stale": stale,
            "missing": missing,
            "extra": extra,
            "semantic": None if self.semantic_backend is None else self.semantic_backend.status(current),
        }

    def duplicate_ids(self, docs: dict[str, Doc] | None = None) -> dict[str, list[str]]:
        docs = docs if docs is not None else self.refresh()
        by_id: dict[str, list[str]] = {}
        for path, doc in sorted(docs.items()):
            if doc.policy != "deny" and doc.note_id:
                by_id.setdefault(doc.note_id, []).append(path)
        return {note_id: paths for note_id, paths in by_id.items() if len(paths) > 1}

    def paths_for_id(self, note_id: str) -> list[str]:
        return sorted(path for path, doc in self.refresh().items() if doc.policy != "deny" and doc.note_id == note_id)

    def resolve(self, target: str, docs: dict[str, Doc]) -> str | None:
        """Resolve a wikilink target (path or name, no extension) like Obsidian does."""
        target = target.strip().removesuffix(".md")
        if f"{target}.md" in docs:
            return f"{target}.md"
        candidates = self._by_name.get(target.rsplit("/", 1)[-1].casefold(), [])
        if "/" in target:
            candidates = [path for path in candidates if path.casefold().endswith(f"{target.casefold()}.md")]
        return candidates[0] if len(candidates) == 1 else (min(candidates, key=len) if candidates else None)

    # ------------------------------------------------------------ corpus

    @staticmethod
    def visible(doc: Doc, audience: str = "people", allow: Allow | None = None) -> bool:
        if doc.policy == "deny" or doc.path.startswith(SYSTEM_ROOT):
            return False
        if doc.type == "proposal" or doc.path.startswith(PROPOSALS_DIR + "/"):
            return False
        if audience == "role" and doc.type == "report":
            return False
        return allow is None or allow(doc)

    @staticmethod
    def result(doc: Doc, line: int, reasons: list[str] | None = None) -> dict[str, Any]:
        text = doc.lines[line - 1].strip() if 0 < line <= len(doc.lines) else doc.title
        item = {
            "title": doc.title,
            "path": doc.path,
            "type": doc.type,
            "status": doc.status,
            "excerpt": text[:EXCERPT_CHARS],
            "line": line,
        }
        if reasons is not None:
            item["reasons"] = reasons
        return item

    # ------------------------------------------------------------ modes

    def keyword(
        self, query: str, *, audience: str = "people", allow: Allow | None = None, limit: int = 20
    ) -> dict[str, Any]:
        """Partial-word match (Korean or English) of every query term in the title or body."""
        terms = [term for term in query.casefold().split() if term]
        if not terms:
            return {"status": "ok", "results": []}
        scored = []
        for doc in self.refresh().values():
            if not self.visible(doc, audience, allow):
                continue
            body = doc.folded[doc.body_start - 1 :]
            title = doc.title.casefold()
            hits = [number for number, line in enumerate(body) if any(term in line for term in terms)]
            if not all(term in title or any(term in body[number] for number in hits) for term in terms):
                continue
            item = self.result(doc, doc.body_start + hits[0] if hits else doc.body_start)
            if hits:
                item["excerpt"] = self._excerpt(doc.lines[item["line"] - 1], terms)
            in_title = all(term in title for term in terms)
            scored.append((-(100 * in_title + len(hits)), doc.path, item))
        scored.sort(key=lambda entry: entry[:2])
        return {"status": "ok", "results": [item for _, _, item in scored[:limit]]}

    @staticmethod
    def _excerpt(line: str, terms: list[str]) -> str:
        text = line.strip()
        if len(text) <= EXCERPT_CHARS:
            return text
        folded = text.casefold()
        positions = [folded.find(term) for term in terms if folded.find(term) >= 0]
        start = max(0, min(positions or [0]) - EXCERPT_CHARS // 3)
        return ("…" if start else "") + text[start : start + EXCERPT_CHARS]

    def semantic_ready(self) -> bool:
        return self.semantic_backend is not None and self.semantic_backend.ready()

    @classmethod
    def chunk_result(cls, doc: Doc, hit: SemanticHit) -> dict[str, Any]:
        """A semantic hit: the chunk's first line is the locator, its text the excerpt."""
        item = cls.result(doc, hit.start)
        text = " ".join(line.strip() for line in doc.lines[hit.start - 1 : hit.end] if line.strip())
        item.update(excerpt=text[:EXCERPT_CHARS], end_line=hit.end, score=round(hit.score, 4))
        return item

    def semantic(
        self, query: str, *, audience: str = "people", allow: Allow | None = None, limit: int = 20
    ) -> dict[str, Any]:
        backend = self.semantic_backend
        if backend is None or not backend.ready():
            return {"status": "unavailable", "results": []}
        docs = self.refresh()
        try:
            hits = backend.search(query, docs, limit, lambda doc: self.visible(doc, audience, allow))
        except SemanticUnavailable as error:
            return {"status": "unavailable", "results": [], "reason": str(error)}
        return {"status": "ok", "results": [self.chunk_result(docs[hit.path], hit) for hit in hits]}

    def related(
        self, path: str, *, audience: str = "people", allow: Allow | None = None, limit: int = 20,
        system_one: SystemOne | None = None,
    ) -> dict[str, Any]:
        """Links, backlinks, shared tags and semantic neighbors, each labeled by reason. With
        ``system_one``, semantic neighbors are vetted (see ``_vet``)."""
        docs = self.refresh()
        focal = docs.get(path)
        if focal is None or not self.visible(focal, audience, allow):
            return {"status": "ok", "results": []}
        reasons: dict[str, list[str]] = {}
        lines: dict[str, int] = {}
        similarity: dict[str, float] = {}

        def add(other: str | None, reason: str, line: int | None = None) -> None:
            if other is None or other == focal.path or other not in docs:
                return
            reasons.setdefault(other, [])
            if reason not in reasons[other]:
                reasons[other].append(reason)
            if line is not None:
                lines.setdefault(other, line)

        for target in focal.links:
            add(self.resolve(target, docs), "link")
        focal_tags = {tag.casefold() for tag in focal.tags}
        for doc in docs.values():
            if doc.policy == "deny" or doc.path == focal.path:
                continue
            for number, line in enumerate(doc.lines, start=1):
                if "[[" in line and any(
                    self.resolve(match.group(1), docs) == focal.path for match in WIKILINK_RE.finditer(line)
                ):
                    add(doc.path, "backlink", number)
                    break
            for tag in sorted(focal_tags & {tag.casefold() for tag in doc.tags}):
                add(doc.path, f"tag:{tag}")
        vetted: set[str] = set()
        if self.semantic_ready():
            assert self.semantic_backend is not None
            accept = lambda doc: self.visible(doc, audience, allow)
            hits = self.semantic_backend.neighbors(focal.path, docs, limit, accept)
            verdicts = self._vet(system_one, focal, hits, docs) if system_one and hits else {}
            for hit in hits:
                if verdicts.get(hit.path) is False:
                    continue
                add(hit.path, "semantic", hit.start)
                similarity[hit.path] = hit.score
                if hit.path in verdicts:
                    vetted.add(hit.path)
        ranked = sorted(reasons.items(), key=lambda item: (-len(item[1]), -similarity.get(item[0], 0.0), item[0]))
        results = []
        for other, labels in ranked:
            doc = docs[other]
            if self.visible(doc, audience, allow):
                item = self.result(doc, lines.get(other, doc.body_start), labels)
                if other in vetted:
                    item["vetted"] = True
                results.append(item)
        return {"status": "ok", "results": results[:limit]}

    def _vet(
        self, system_one: SystemOne, focal: Doc, hits: list[SemanticHit], docs: dict[str, Doc]
    ) -> dict[str, bool]:
        """System One's verdict per semantic neighbor path: is it substantively related to the
        focal note, not merely sharing generic words? One Decisions request carries the focal
        note and one predicate per neighbor that has no cached verdict. The focal note and each
        candidate are reread and their current policy checked right before sending; any that
        is no longer ``approve``, and every unavailable or refused answer, have no verdict,
        so that neighbor stays a plain cosine neighbor. Reads never write State (``ref=None``).
        Verdicts are cached in memory by focal sha, candidate sha and System One model."""
        assert self.semantic_backend is not None
        threshold = float(self.semantic_backend.related_min_probability)
        model = system_one.settings.model
        known: dict[str, float] = {}
        pending: list[tuple[str, SemanticHit, tuple[str, str, str], Note]] = []
        focal_note = self._sendable(focal)
        if focal_note is None:
            return {}
        for hit in hits:
            doc = docs[hit.path]
            key = (focal.sha or "", doc.sha or "", model)
            with self._lock:
                cached = self._verdicts.get(key)
            if cached is not None:
                known[hit.path] = cached
                continue
            candidate = self._sendable(doc)
            if candidate is not None:
                pending.append((f"related_{len(pending) + 1}", hit, key, candidate))
        if pending:
            questions = [predicate(name, (
                "Is the candidate note substantively related to the focal note (the same subject, argument, "
                "project or source), not merely sharing generic words? Candidate note: "
                + json.dumps({"title": note.title[:200], "excerpt": "\n".join(
                    note.text.splitlines()[hit.start - 1 : hit.end])[:CANDIDATE_CHARS]}, ensure_ascii=False)
            )) for name, hit, _, note in pending]
            try:
                answers = system_one.decide(
                    note_input(focal_note, system_one.max_input_chars, "focal note"), questions,
                    purpose="C3.related", ref=None,
                )
            except SystemOneUnavailable as error:
                log.info("related neighbors stay unvetted: %s", error)
                answers = {}
            for name, hit, key, _ in pending:
                answer = answers.get(name)
                if answer is None or system_one.holds(answer, threshold) is None:
                    continue
                known[hit.path] = float(answer["probability"])
                with self._lock:
                    if len(self._verdicts) >= VERDICT_CACHE:
                        self._verdicts.clear()
                    self._verdicts[key] = known[hit.path]
        return {path: probability >= threshold for path, probability in known.items()}

    def _sendable(self, doc: Doc) -> Note | None:
        """The note reread from the Vault when it is still the indexed content and its current
        policy is ``approve``."""
        try:
            note = self.vault.read(doc.path)
        except VaultPathError:
            return None
        if note is None or note.sha != doc.sha or not approved(effective(note)):
            return None
        return note

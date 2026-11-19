"""Capability C3: the Owner's search service (keyword, semantic, related).

The index is rebuildable in-memory Runtime data refreshed per changed document: each
refresh stats every Markdown file and reparses only files whose identity changed. ``deny``
notes are never indexed: only their file identity is kept so they are not reread.

Audiences: ``people`` (Thin Client, console) see reports; ``role`` (Team read tools)
excludes reports unless the user changed their type, and applies the Work's policy filter.
Semantic search needs a ready local vector index (``knowledgeos.semantic``); without one, or
when the query cannot be embedded, it answers ``unavailable`` and never substitutes keyword
results. Semantic neighbors join the related labels. Every semantic result passes the same
audience and policy filter as keyword results.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol

from .notes import WIKILINK_RE, Note
from .policy import DEFAULT_POLICY, effective, model_may_read
from .standardize import normalize_tags
from .util import sha256
from .vault import PROPOSALS_DIR, SYSTEM_ROOT, Vault, VaultPathError

EXCERPT_CHARS = 160


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

    def neighbors(
        self, path: str, docs: dict[str, Doc], limit: int, accept: Callable[[Doc], bool]
    ) -> list[SemanticHit]: ...

    def status(self, docs: dict[str, Doc]) -> dict[str, Any]: ...


Allow = Callable[[Doc], bool]


def role_filter(origin: str, local_route: bool = False) -> Allow:
    """The policy filter for a model role's reads in Work of the given origin (Blueprint §5.4)."""
    return lambda doc: model_may_read(doc.policy, origin, local_route)


def _stamp(stat: Any) -> tuple[int, int, int, int]:
    return (stat.st_mtime_ns, stat.st_size, stat.st_ino, stat.st_ctime_ns)


class SearchIndex:
    def __init__(
        self,
        vault: Vault,
        default_policy: str = DEFAULT_POLICY,
        observe: Callable[[str], None] | None = None,
    ):
        self.vault = vault
        self.default_policy = default_policy
        self.observe = observe
        self.semantic_backend: SemanticBackend | None = None
        self._docs: dict[str, Doc] = {}
        self._by_name: dict[str, list[str]] = {}
        self._lock = threading.RLock()

    # ------------------------------------------------------------ index

    def _make_doc(self, path: str, note: Note, stamp: tuple[int, int, int, int], mtime: float) -> Doc:
        policy = effective(note, self.default_policy)
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
            if effective(note, self.default_policy) == "deny":
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
        self, path: str, *, audience: str = "people", allow: Allow | None = None, limit: int = 20
    ) -> dict[str, Any]:
        """Links, backlinks, shared tags and semantic neighbors, each labeled by reason."""
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
        if self.semantic_ready():
            assert self.semantic_backend is not None
            accept = lambda doc: self.visible(doc, audience, allow)
            for hit in self.semantic_backend.neighbors(focal.path, docs, limit, accept):
                add(hit.path, "semantic", hit.start)
                similarity[hit.path] = hit.score
        ranked = sorted(reasons.items(), key=lambda item: (-len(item[1]), -similarity.get(item[0], 0.0), item[0]))
        results = []
        for other, labels in ranked:
            doc = docs[other]
            if self.visible(doc, audience, allow):
                results.append(self.result(doc, lines.get(other, doc.body_start), labels))
        return {"status": "ok", "results": results[:limit]}

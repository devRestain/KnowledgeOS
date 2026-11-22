"""C3 semantic index: local Ollama embeddings, line-range chunks and cosine search.

The Owner calls Ollama directly over loopback HTTP (``POST /api/embed``); no gateway, no
remote model. Vectors are derived, rebuildable data under ``<State>/semantic/``:

- ``manifest.json``: the embedding model, and per note path its content SHA-256, chunk line
  ranges and vector file. It is the commit point: a note counts as embedded only when the
  manifest names its current content digest.
- ``vectors/<path hash>-<content hash>.npy``: one float32 matrix per note, L2-normalized,
  one row per chunk. Files the manifest does not name are removed after each manifest write.

A note is served only while its current content digest equals the manifest's, so an edited
note drops out of results until the embedding job re-embeds it. A different configured model
invalidates every vector. A failed or unreachable embedding service never touches existing
vectors and is never retried within a run.
"""

from __future__ import annotations

import http.client
import io
import json
import logging
import re
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import numpy as np

from .catalog import Catalog
from .config import ConfigError, OwnerConfig
from .notes import Note
from .search import Doc, SemanticHit, SemanticUnavailable
from operationkit.canonical import canonical, sha256

from .util import atomic_write
from .vault import PROPOSALS_DIR, SYSTEM_ROOT

log = logging.getLogger("knowledgeos.semantic")

QUERY_INSTRUCTION = "Instruct: Given a search query, retrieve the notes that answer or relate to it\nQuery: "
CHUNK_CHARS = 1500
INPUT_CHARS = 8000
CHECKPOINT_NOTES = 20
LOOPBACK_HOSTS = ("127.0.0.1", "localhost", "::1")
HEADING_RE = re.compile(r"^#{1,6}\s")
MANIFEST = "manifest.json"
VECTORS = "vectors"


class EmbeddingError(SemanticUnavailable):
    """The embedding service failed this request. The message is safe to show: it never carries note text."""


# ---------------------------------------------------------------- eligibility


def ineligible_reason(path: str, policy: str, note_type: str | None, conflict: bool) -> str | None:
    """Why a note is never embedded, or None. Embedding is deterministic local processing
    (Blueprint §5.4): only ``deny`` forbids it. Notes whose application is undecided
    (proposals), system assets and conflict-marked notes are outside the corpus."""
    if policy == "deny":
        return "deny"
    if path.startswith(SYSTEM_ROOT):
        return "system"
    if note_type == "proposal" or path.startswith(PROPOSALS_DIR + "/"):
        return "proposal"
    if conflict:
        return "conflict"
    return None


def is_eligible(doc: Doc) -> bool:
    return doc.sha is not None and ineligible_reason(doc.path, doc.policy, doc.type, doc.conflict) is None


# ---------------------------------------------------------------- chunks


def _has_text(lines: list[str], numbers: list[int]) -> bool:
    return any(lines[n - 1].strip() and not HEADING_RE.match(lines[n - 1]) for n in numbers)


def chunk(note: Note) -> list[tuple[int, int]]:
    """Body line ranges (1-based, inclusive) of about ``CHUNK_CHARS`` characters.

    A heading starts a new chunk once the current one has text, so consecutive headings stay
    with the paragraph that follows. A long section splits at its last paragraph break that
    leaves text before it, else at a line boundary. Frontmatter is never chunked.
    """
    lines = note.text.splitlines()
    spans: list[tuple[int, int]] = []
    window: list[int] = []
    size = 0

    def emit(numbers: list[int]) -> None:
        while numbers and not lines[numbers[0] - 1].strip():
            numbers = numbers[1:]
        while numbers and not lines[numbers[-1] - 1].strip():
            numbers = numbers[:-1]
        if numbers:
            spans.append((numbers[0], numbers[-1]))

    for number in range(note.body_start_line, len(lines) + 1):
        line = lines[number - 1]
        heading = bool(HEADING_RE.match(line))
        if _has_text(lines, window) and (heading or size + len(line) + 1 > CHUNK_CHARS):
            cut = len(window)
            if not heading:
                breaks = [i for i, n in enumerate(window) if not lines[n - 1].strip() and _has_text(lines, window[:i])]
                if breaks:
                    cut = breaks[-1]
            emit(window[:cut])
            window = window[cut:]
            size = sum(len(lines[n - 1]) + 1 for n in window)
        window.append(number)
        size += len(line) + 1
    emit(window)
    return spans


def texts_for(note: Note, spans: list[tuple[int, int]]) -> list[str]:
    """The embedding input of each chunk: the note title, then the chunk's exact lines."""
    lines = note.text.splitlines()
    return [f"{note.title}\n\n" + "\n".join(lines[start - 1 : end])[:INPUT_CHARS] for start, end in spans]


# ---------------------------------------------------------------- Ollama


class OllamaClient:
    def __init__(self, url: str, model: str, timeout: float):
        self.endpoint = url.rstrip("/") + "/api/embed"
        self.model = model
        self.timeout = timeout
        self._opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def embed(self, texts: list[str]) -> np.ndarray:
        """One request: unit-length float32 vectors, one row per text. Never retried."""
        body = json.dumps({"model": self.model, "input": texts}).encode("utf-8")
        request = urllib.request.Request(
            self.endpoint, data=body, method="POST", headers={"Content-Type": "application/json"}
        )
        try:
            with self._opener.open(request, timeout=self.timeout) as response:
                payload = json.loads(response.read())
        except urllib.error.HTTPError as error:
            raise EmbeddingError(f"embedding service answered HTTP {error.code}{_detail(error)}") from error
        except (OSError, http.client.HTTPException) as error:
            reason = getattr(error, "reason", None) or error
            raise EmbeddingError(f"embedding service unreachable: {type(reason).__name__}: {reason}") from error
        except (json.JSONDecodeError, UnicodeDecodeError) as error:
            raise EmbeddingError("embedding service sent an invalid response") from error
        rows = payload.get("embeddings") if isinstance(payload, dict) else None
        try:
            vectors = np.asarray(rows, dtype=np.float32)
        except (TypeError, ValueError) as error:
            raise EmbeddingError("embedding service sent malformed vectors") from error
        if vectors.ndim != 2 or vectors.shape[0] != len(texts) or vectors.shape[1] == 0 or not np.isfinite(vectors).all():
            raise EmbeddingError("embedding service sent malformed vectors")
        norms = np.linalg.norm(vectors, axis=1)
        if not norms.all():
            raise EmbeddingError("embedding service sent an empty vector")
        return vectors / norms[:, None]


def _detail(error: urllib.error.HTTPError) -> str:
    try:
        message = json.loads(error.read(2000)).get("error")
    except (OSError, ValueError, AttributeError):
        return ""
    return f": {message[:200]}" if isinstance(message, str) and message else ""


# ---------------------------------------------------------------- store


@dataclass(frozen=True)
class Entry:
    sha: str
    spans: tuple[tuple[int, int], ...]
    file: str | None


@dataclass(frozen=True)
class Pending:
    """One note to embed: its admitted content digest, chunk ranges and chunk texts."""

    path: str
    sha: str
    spans: list[tuple[int, int]]
    texts: list[str]


@dataclass(frozen=True)
class Plan:
    embed: list[str]  # eligible notes without current vectors, most recently modified first
    remove: list[str]  # embedded notes that are no longer eligible or no longer exist
    current: int  # eligible notes whose vectors are current


@dataclass(frozen=True)
class _View:
    matrix: np.ndarray
    rows: tuple[tuple[str, int, int], ...]
    offsets: dict[str, tuple[int, int]]
    sha: dict[str, str]


@dataclass
class Settings:
    ollama_url: str
    model: str
    batch_size: int = 16
    timeout_seconds: float = 60.0
    max_notes: int = 100
    related_neighbors: int = 5
    related_min_score: float = 0.5
    related_min_probability: float = 0.5


def build(config: OwnerConfig, catalog: Catalog) -> SemanticStore | None:
    """The semantic backend from ``settings.C3.semantic`` over the catalog defaults, or None when
    no endpoint or model is configured. The endpoint must be loopback."""
    values = {**catalog.settings("C3").get("semantic", {}), **config.settings.get("C3", {}).get("semantic", {})}
    url, model = values.get("ollama_url"), values.get("model")
    if not url or not model:
        return None
    if not isinstance(url, str) or not isinstance(model, str):
        raise ConfigError("C3.semantic ollama_url and model must be strings")
    parts = urlsplit(url)
    if parts.scheme != "http" or parts.hostname not in LOOPBACK_HOSTS:
        raise ConfigError(f"C3.semantic ollama_url must be an http loopback URL, not {url!r}")
    settings = Settings(
        url, model,
        batch_size=values.get("batch_size", 16), timeout_seconds=values.get("timeout_seconds", 60),
        max_notes=values.get("max_notes", 100), related_neighbors=values.get("related_neighbors", 5),
        related_min_score=values.get("related_min_score", 0.5),
        related_min_probability=values.get("related_min_probability", 0.5),
    )
    for name in ("batch_size", "max_notes"):
        value = getattr(settings, name)
        if not isinstance(value, int) or isinstance(value, bool) or value < 1:
            raise ConfigError(f"C3.semantic {name} must be a positive integer")
    if not isinstance(settings.related_neighbors, int) or isinstance(settings.related_neighbors, bool) \
            or settings.related_neighbors < 0:
        raise ConfigError("C3.semantic related_neighbors must be a non-negative integer")
    if not isinstance(settings.related_min_score, (int, float)) or isinstance(settings.related_min_score, bool) \
            or not -1 <= settings.related_min_score <= 1:
        raise ConfigError("C3.semantic related_min_score must be a cosine similarity between -1 and 1")
    if not isinstance(settings.related_min_probability, (int, float)) \
            or isinstance(settings.related_min_probability, bool) or not 0 <= settings.related_min_probability <= 1:
        raise ConfigError("C3.semantic related_min_probability must be a probability between 0 and 1")
    if not isinstance(settings.timeout_seconds, (int, float)) or isinstance(settings.timeout_seconds, bool) \
            or settings.timeout_seconds <= 0:
        raise ConfigError("C3.semantic timeout_seconds must be positive")
    return SemanticStore(config.state / "semantic", settings)


class SemanticStore:
    """The vector index and its embedding client. One embedding run at a time; searches read an
    immutable view and never wait for a run."""

    def __init__(self, root: Path, settings: Settings):
        self.root = root
        self.model = settings.model
        self.batch_size = settings.batch_size
        self.timeout = float(settings.timeout_seconds)
        self.max_notes = settings.max_notes
        self.related_neighbors = settings.related_neighbors
        self.related_min_score = float(settings.related_min_score)
        self.related_min_probability = float(settings.related_min_probability)
        self.client = OllamaClient(settings.ollama_url, settings.model, self.timeout)
        self._write = threading.Lock()
        self._lock = threading.RLock()
        self._loaded = False
        self._state_model: str | None = None
        self._dim: int | None = None
        self._entries: dict[str, Entry] = {}
        self._vectors: dict[str, np.ndarray] = {}
        self._cached: _View | None = None

    # ------------------------------------------------------------ state

    def _ensure_loaded(self) -> None:
        with self._lock:
            if self._loaded:
                return
            self._loaded = True
            try:
                manifest = json.loads((self.root / MANIFEST).read_bytes())
                notes = manifest["notes"]
                self._state_model = manifest["model"]
            except FileNotFoundError:
                return
            except (OSError, ValueError, KeyError, TypeError):
                log.warning("semantic manifest is unreadable; the index is treated as empty until re-embedded")
                self._state_model = None
                return
            for path, item in notes.items():
                try:
                    spans = tuple((int(start), int(end)) for start, end in item["chunks"])
                    file = item["file"]
                    entry = Entry(str(item["sha"]), spans, file)
                    if file is not None:
                        array = np.load(self.root / VECTORS / file, allow_pickle=False)
                        if array.dtype != np.float32 or array.ndim != 2 or array.shape[0] != len(spans) or (
                            self._dim is not None and array.shape[1] != self._dim
                        ):
                            raise ValueError("unexpected vector file")
                        self._dim = int(array.shape[1])
                        self._vectors[path] = array
                    self._entries[path] = entry
                except (OSError, EOFError, ValueError, KeyError, TypeError):
                    log.warning("semantic vectors for one note are unreadable; it will be re-embedded")

    def _view(self) -> _View | None:
        with self._lock:
            self._ensure_loaded()
            if self._state_model != self.model or not self._vectors:
                return None
            if self._cached is None:
                paths = sorted(self._vectors)
                rows: list[tuple[str, int, int]] = []
                offsets: dict[str, tuple[int, int]] = {}
                for path in paths:
                    offsets[path] = (len(rows), len(self._entries[path].spans))
                    rows.extend((path, start, end) for start, end in self._entries[path].spans)
                self._cached = _View(
                    matrix=np.concatenate([self._vectors[path] for path in paths]),
                    rows=tuple(rows),
                    offsets=offsets,
                    sha={path: self._entries[path].sha for path in paths},
                )
            return self._cached

    def _current_entries(self) -> dict[str, Entry]:
        with self._lock:
            self._ensure_loaded()
            return dict(self._entries) if self._state_model == self.model else {}

    # ------------------------------------------------------------ queries

    def ready(self) -> bool:
        """The configured model has vectors for at least one note."""
        with self._lock:
            self._ensure_loaded()
            return self._state_model == self.model and bool(self._vectors)

    def status(self, docs: dict[str, Doc]) -> dict[str, Any]:
        """Coverage counts against the given index snapshot (never paths: removable notes may be ``deny``)."""
        entries = self._current_entries()
        eligible = {path: doc for path, doc in docs.items() if is_eligible(doc)}
        embedded = sum(1 for path, doc in eligible.items() if path in entries and entries[path].sha == doc.sha)
        with self._lock:
            chunks = sum(len(entry.spans) for entry in entries.values())
            indexed_model = self._state_model
        return {
            "model": self.model,
            "indexed_model": indexed_model,
            "ready": self.ready(),
            "eligible": len(eligible),
            "embedded": embedded,
            "stale": sum(1 for path, doc in eligible.items() if path in entries and entries[path].sha != doc.sha),
            "missing": sum(1 for path in eligible if path not in entries),
            "extra": sum(1 for path in entries if path not in eligible),
            "chunks": chunks,
        }

    def search(
        self, query: str, docs: dict[str, Doc], limit: int, accept: Callable[[Doc], bool]
    ) -> list[SemanticHit]:
        view = self._view()
        if view is None:
            return []
        vector = self.client.embed([(QUERY_INSTRUCTION + query.strip())[:INPUT_CHARS]])[0]
        if vector.shape[0] != view.matrix.shape[1]:
            raise EmbeddingError("the embedding dimension differs from the index; run `knowledgeos index embed`")
        return self._rank(view, view.matrix @ vector, docs, limit, accept, None, None)

    def neighbors(
        self, path: str, docs: dict[str, Doc], limit: int, accept: Callable[[Doc], bool]
    ) -> list[SemanticHit]:
        """Notes closest to the focal note's mean chunk vector; needs no embedding request."""
        view = self._view()
        focal = docs.get(path)
        if view is None or focal is None or view.sha.get(path) != focal.sha or self.related_neighbors < 1:
            return []
        offset, count = view.offsets[path]
        centroid = view.matrix[offset : offset + count].mean(axis=0)
        norm = float(np.linalg.norm(centroid))
        if norm == 0.0:
            return []
        return self._rank(
            view, view.matrix @ (centroid / norm), docs, min(limit, self.related_neighbors), accept, path,
            self.related_min_score,
        )

    @staticmethod
    def _rank(
        view: _View, scores: np.ndarray, docs: dict[str, Doc], limit: int, accept: Callable[[Doc], bool],
        exclude: str | None, minimum: float | None,
    ) -> list[SemanticHit]:
        """Best chunk per note, best first; stale, missing and rejected notes never count."""
        hits: list[SemanticHit] = []
        seen = {exclude}
        for index in np.argsort(-scores, kind="stable"):
            if minimum is not None and scores[index] < minimum:
                break
            path, start, end = view.rows[index]
            if path in seen:
                continue
            seen.add(path)
            doc = docs.get(path)
            if doc is None or doc.sha != view.sha[path] or not accept(doc):
                continue
            hits.append(SemanticHit(path, start, end, float(scores[index])))
            if len(hits) >= limit:
                break
        return hits

    # ------------------------------------------------------------ embedding runs

    def plan(self, docs: dict[str, Doc]) -> Plan:
        """What a run must do: embed eligible notes without current vectors, drop the rest."""
        entries = self._current_entries()
        eligible = {path: doc for path, doc in docs.items() if is_eligible(doc)}
        stale = [path for path, doc in eligible.items() if path not in entries or entries[path].sha != doc.sha]
        stale.sort(key=lambda path: (-eligible[path].mtime, path))
        return Plan(
            embed=stale,
            remove=sorted(path for path in entries if path not in eligible),
            current=len(eligible) - len(stale),
        )

    def apply(
        self,
        notes: list[Pending],
        remove: list[str],
        *,
        deadline: float,
        allowed: Callable[[str], bool],
        guard: Callable[[], None],
    ) -> dict[str, Any]:
        """Drop ``remove``, then embed ``notes`` in order until ``deadline`` (``time.monotonic``).

        ``allowed`` re-checks a note's current eligibility immediately before anything is sent;
        a note that is no longer eligible is dropped and never sent. The first embedding failure
        stops the run: nothing is retried, the failing note and later notes stay pending, and
        every vector already stored is kept. ``guard`` raises when the Work may no longer commit.
        """
        counts: dict[str, Any] = {"embedded": 0, "unchanged": 0, "removed": 0, "failed": 0, "pending": 0, "error": None}
        with self._write:
            with self._lock:
                self._ensure_loaded()
            dirty = False
            since = 0
            try:
                for path in remove:
                    if self._drop(path):
                        counts["removed"] += 1
                        dirty = True
                for index, note in enumerate(notes):
                    guard()
                    if not allowed(note.path):
                        if self._drop(note.path):
                            counts["removed"] += 1
                            dirty = True
                        continue
                    if self._is_current(note.path, note.sha):
                        counts["unchanged"] += 1
                        continue
                    if time.monotonic() >= deadline:
                        counts["pending"] = len(notes) - index
                        break
                    try:
                        vectors = self._embed(note, deadline)
                    except EmbeddingError as error:
                        counts.update(failed=1, error=str(error), pending=len(notes) - index - 1)
                        break
                    if vectors is None:
                        counts["pending"] = len(notes) - index
                        break
                    self._commit(note, vectors)
                    counts["embedded"] += 1
                    dirty = True
                    since += 1
                    if since >= CHECKPOINT_NOTES:
                        self._persist()
                        since = 0
                        dirty = False
            finally:
                if dirty:
                    self._persist()
        return counts

    def _is_current(self, path: str, sha: str) -> bool:
        with self._lock:
            entry = self._entries.get(path)
            return self._state_model == self.model and entry is not None and entry.sha == sha

    def _embed(self, note: Pending, deadline: float) -> np.ndarray | None:
        """The note's chunk vectors, or None when the deadline passes between requests."""
        parts = []
        for start in range(0, len(note.texts), self.batch_size):
            if parts and time.monotonic() >= deadline:
                return None
            parts.append(self.client.embed(note.texts[start : start + self.batch_size]))
        if len({part.shape[1] for part in parts}) > 1:
            raise EmbeddingError("the embedding service changed dimension within one note")
        vectors = np.concatenate(parts) if parts else np.empty((0, 0), dtype=np.float32)
        with self._lock:
            expected = self._dim if self._state_model == self.model else None
        if len(vectors) and expected is not None and vectors.shape[1] != expected:
            raise EmbeddingError("the embedding dimension differs from the stored vectors")
        return vectors

    def _commit(self, note: Pending, vectors: np.ndarray) -> None:
        file = None
        if len(vectors):
            file = f"{sha256(note.path.encode('utf-8'))[:20]}-{note.sha[:20]}.npy"
            buffer = io.BytesIO()
            np.save(buffer, vectors, allow_pickle=False)
            atomic_write(self.root / VECTORS / file, buffer.getvalue())
        with self._lock:
            if self._state_model != self.model:  # a different model invalidates every stored vector
                self._entries.clear()
                self._vectors.clear()
                self._dim = None
                self._state_model = self.model
            if file is None:
                self._vectors.pop(note.path, None)
            else:
                self._vectors[note.path] = vectors
                self._dim = int(vectors.shape[1])
            self._entries[note.path] = Entry(note.sha, tuple(note.spans), file)
            self._cached = None

    def _drop(self, path: str) -> bool:
        with self._lock:
            if self._state_model != self.model or path not in self._entries:
                return False
            del self._entries[path]
            self._vectors.pop(path, None)
            self._cached = None
            return True

    def _persist(self) -> None:
        """Write the manifest atomically, then remove vector files it no longer names."""
        with self._lock:
            manifest = {
                "version": 1,
                "model": self._state_model,
                "notes": {
                    path: {"sha": entry.sha, "chunks": [list(span) for span in entry.spans], "file": entry.file}
                    for path, entry in sorted(self._entries.items())
                },
            }
            keep = {entry.file for entry in self._entries.values() if entry.file}
        atomic_write(self.root / MANIFEST, canonical(manifest) + b"\n")
        directory = self.root / VECTORS
        if directory.is_dir():
            for file in directory.glob("*.npy"):
                if file.name not in keep:
                    file.unlink(missing_ok=True)

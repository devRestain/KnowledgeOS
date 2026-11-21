"""Per-Work evidence handles (Blueprint §4.4, §5.4, §5.5).

Read tools give model roles short handles (``e1``, ``e2``, ...) instead of paths, IDs or
digests. A handle names one chunk of one note: the exact bytes disclosed in this Work
(the admitted snapshot for Work inputs, otherwise the current file at read time, stored
as a State blob), the chunk's 1-based line range and the SHA-256 of the chunk's bytes.
Handles live in the Work record (``work["evidence"]``) and never change once issued.

"Read in this Work" means the handle was returned by ``read`` (or by a static teammate's
collection): ``read`` is ``True``. Search and related results issue handles that are not
read yet. Before any disclosure the note's current content is checked against the Work
policy; a moved, deleted or newly restricted source fails closed (``Unavailable``).
Every disclosure requires ``ai_policy: approve`` on both the disclosed version and the current file.
``assert_disclosure`` applies the same policy before replaying retained model context.
Citations expand to Core ``{resource_reference, chunk_locator, chunk_hash}`` and to
wikilinks with a line locator and a short hash.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from .notes import Note, parse
from .policy import approved, effective, strictest
from .util import sha256
from .vault import resource_reference

if TYPE_CHECKING:
    from .owner import Owner

HANDLE_RE = re.compile(r"^e[1-9][0-9]*$")
HEADING_RE = re.compile(r"^#{1,6}\s")
CHUNK_MAX_LINES = 40
READ_MAX_CHARS = 20_000
EVAL_EXCERPT_CHARS = 600


class Unavailable(LookupError):
    """The source moved, was deleted or is not readable under the Work policy."""


@dataclass(frozen=True)
class Chunk:
    start: int  # 1-based file line
    end: int
    text: str
    hash: str


def chunks(note: Note) -> list[Chunk]:
    """Body paragraphs (headings start a new chunk), at most ``CHUNK_MAX_LINES`` lines each.

    Frontmatter is never chunked, so roles never see note IDs. ``hash`` is the SHA-256 of
    the chunk's exact UTF-8 bytes (lines with their endings, BOM excluded).
    """
    lines = note.text.splitlines(keepends=True)
    found: list[Chunk] = []
    current: list[tuple[int, str]] = []

    def flush() -> None:
        if current:
            text = "".join(line for _, line in current)
            found.append(Chunk(current[0][0], current[-1][0], text, sha256(text.encode("utf-8"))))
            current.clear()

    for index in range(note.body_start_line - 1, len(lines)):
        line = lines[index]
        if not line.strip():
            flush()
            continue
        if HEADING_RE.match(line) or len(current) >= CHUNK_MAX_LINES:
            flush()
        current.append((index + 1, line))
    flush()
    return found


def _locator(entry: dict[str, Any]) -> str:
    start, end = entry["lines"]
    return f"L{start}" if start == end else f"L{start}-{end}"


class Evidence:
    """Evidence handles of one Work. Every method reads the current Work record."""

    def __init__(self, owner: Owner, work_id: str):
        self.owner = owner
        self.work_id = work_id

    # ------------------------------------------------------------ records

    def _work(self) -> dict[str, Any]:
        work = self.owner.get_work(self.work_id)
        if work is None:
            raise LookupError(f"no Work {self.work_id}")
        return work

    def table(self) -> dict[str, dict[str, Any]]:
        """Every handle issued in this Work: ``{handle: entry}``."""
        return dict(self._work().get("evidence") or {})

    def get(self, handle: Any) -> dict[str, Any] | None:
        if not isinstance(handle, str) or not HANDLE_RE.match(handle):
            return None
        return self.table().get(handle)

    def read_handles(self) -> set[str]:
        return {handle for handle, entry in self.table().items() if entry["read"]}

    def handles_in(self, value: Any) -> list[str]:
        """Issued handles mentioned anywhere in ``value`` (submission or artifact), in order."""
        table = self.table()
        found: list[str] = []

        def walk(item: Any) -> None:
            if isinstance(item, str):
                if item in table and item not in found:
                    found.append(item)
            elif isinstance(item, dict):
                for nested in item.values():
                    walk(nested)
            elif isinstance(item, list):
                for nested in item:
                    walk(nested)

        walk(value)
        return found

    def _register(self, path: str, note: Note, policy: str, source: str, picked: list[Chunk], read: bool,
                  by: str) -> list[str]:
        """Issue (or reuse) handles for chunks of one disclosed note version."""
        issued: list[str] = []
        base = {
            "path": path,
            "title": note.title,
            "note_id": note.note_id,
            "type": note.type,
            "sha": note.sha,
            "blob": note.sha,
            "policy": policy,
            "source": source,
        }

        def change(work: dict[str, Any]) -> None:
            table = work.setdefault("evidence", {})
            known = {(e["path"], e["sha"], e["lines"][0], e["lines"][1]): h for h, e in table.items()}
            for chunk in picked:
                key = (path, note.sha, chunk.start, chunk.end)
                handle = known.get(key)
                if handle is None:
                    handle = f"e{len(table) + 1}"
                    table[handle] = {
                        **base,
                        "lines": [chunk.start, chunk.end],
                        "chunk_hash": chunk.hash,
                        "read": read,
                        "registered_by": by,
                        "read_by": by if read else None,
                    }
                    known[key] = handle
                elif read and not table[handle]["read"]:
                    table[handle].update(read=True, read_by=by)
                issued.append(handle)

        self.owner.update_work(self.work_id, change)
        return issued

    # ------------------------------------------------------------ disclosure

    def _input(self, path: str) -> dict[str, Any] | None:
        for item in self._work()["workspec"]["inputs"]:
            if item["path"] == path:
                return item
        return None

    def _version(self, path: str) -> tuple[Note, str, str]:
        """The note version a Work discloses for ``path``: the admitted snapshot for inputs,
        otherwise the current bytes (stored as a blob). No policy check here."""
        item = self._input(path)
        if item is not None:
            data = self.owner.store.get_blob(item["blob"])
            if data is None:
                raise Unavailable("the admitted snapshot is missing from State")
            return parse(path, data), item["policy"], "snapshot"
        current = self.owner.vault.read(path)
        if current is None:
            raise Unavailable("the source was moved or deleted")
        self.owner.store.put_blob(current.raw)
        return current, effective(current), "current"

    def _check(self, path: str, also: str | None = None) -> str:
        """Policy of the note's current content, checked immediately before disclosure."""
        current = self.owner.vault.read(path)
        if current is None:
            raise Unavailable("the source was moved or deleted")
        policy = effective(current)
        if also is not None:
            policy = strictest([policy, also])
        if not approved(policy):
            raise Unavailable("privacy: the source's ai_policy is deny")
        return policy

    def assert_disclosure(self, *, handles: Iterable[str] | None = None) -> None:
        """Fail closed before replaying retained evidence to another receiving role."""
        table = self.table()
        selected = handles if handles is not None else (h for h, entry in table.items() if entry["read"])
        for handle in selected:
            entry = table.get(handle)
            if entry is None:
                raise Unavailable("privacy: the retained evidence handle is unavailable")
            self._check(entry["path"], entry["policy"])

    def read_note(self, path: str, *, by: str) -> list[str]:
        """Disclose every chunk of one note and mark the handles read. Raises ``Unavailable``."""
        self._check(path)
        note, version_policy, source = self._version(path)
        policy = self._check(path, version_policy)
        picked, used = [], 0
        for chunk in chunks(note):
            used += len(chunk.text)
            if picked and used > READ_MAX_CHARS:
                break
            picked.append(chunk)
        return self._register(path, note, policy, source, picked, True, by)

    def note_handles(self, path: str, *, by: str) -> list[str]:
        """Issue context handles only for readable versions."""
        self._check(path)
        note, policy, source = self._version(path)
        policy = self._check(path, policy)
        return self._register(path, note, policy, source, chunks(note), False, by)

    def _hit(self, path: str, line: int, by: str) -> str | None:
        """The handle of the chunk holding ``line`` (or the first chunk), not marked read."""
        try:
            self._check(path)
            note, policy, source = self._version(path)
            policy = self._check(path, policy)
        except Unavailable:
            return None
        found = chunks(note)
        if not found:
            return None
        chunk = next((c for c in found if c.start <= line <= c.end), found[0])
        return self._register(path, note, policy, source, [chunk], False, by)[0]

    # ------------------------------------------------------------ role tools

    def read(self, handle: Any, *, by: str) -> dict[str, Any]:
        """Tool ``read(handle)``: the note's chunks with their handles and line ranges."""
        entry = self.get(handle)
        if entry is None:
            return {"handle": handle, "status": "unknown_handle"}
        try:
            issued = self.read_note(entry["path"], by=by)
        except Unavailable as error:
            return {"handle": handle, "status": "unavailable", "reason": str(error)}
        table = self.table()
        first = table[issued[0]] if issued else entry
        return {
            "handle": handle,
            "status": "ok",
            "title": first["title"],
            "type": first["type"],
            "chunks": [
                {"handle": h, "lines": _locator(table[h])[1:], "text": self.chunk_text(h)} for h in issued
            ],
        }

    def search(self, query: Any, *, by: str, limit: int = 10) -> dict[str, Any]:
        """Tool ``search(query)``: role-audience keyword search over approved notes."""
        if not isinstance(query, str) or not query.strip():
            return {"status": "invalid", "results": []}
        found = self.owner.index.keyword(query, audience="role", limit=max(1, min(int(limit), 20)))
        return {"status": found["status"], "results": self._results(found["results"], by)}

    def related(self, handle: Any, *, by: str, limit: int = 10) -> dict[str, Any]:
        """Tool ``related(handle)``: links, backlinks and shared tags of the handle's note."""
        entry = self.get(handle)
        if entry is None:
            return {"status": "unknown_handle", "results": []}
        try:
            self._check(entry["path"], entry["policy"])
        except Unavailable as error:
            return {"status": "unavailable", "results": [], "reason": str(error)}
        found = self.owner.index.related(
            entry["path"], audience="role", limit=max(1, min(int(limit), 20)), system_one=self.owner.system_one,
        )
        return {"status": found["status"], "results": self._results(found["results"], by)}

    def _results(self, results: list[dict[str, Any]], by: str) -> list[dict[str, Any]]:
        shown = []
        for result in results:
            handle = self._hit(result["path"], result["line"], by)
            if handle is None:
                continue
            entry = self.get(handle)
            try:
                excerpt = self.chunk_text(handle).strip()[:160]
            except Unavailable:
                continue
            item = {
                "handle": handle,
                "title": entry["title"],
                "type": entry["type"],
                "excerpt": excerpt,
            }
            if "reasons" in result:
                item["reasons"] = result["reasons"]
            if result.get("vetted"):
                item["vetted"] = True
            shown.append(item)
        return shown

    # ------------------------------------------------------------ expansion

    def chunk_text(self, handle: str) -> str:
        entry = self.table()[handle]
        self._check(entry["path"], entry["policy"])
        data = self.owner.store.get_blob(entry["blob"])
        if data is None:
            raise LookupError(f"evidence bytes for {handle} are missing from State")
        lines = parse(entry["path"], data).text.splitlines(keepends=True)
        start, end = entry["lines"]
        return "".join(lines[start - 1 : end])

    def excerpts(self, handles: list[str]) -> list[dict[str, Any]]:
        """Bounded, policy-checked chunk excerpts (the EvalOfficer's view of cited evidence)."""
        table = self.table()
        shown = []
        for handle in handles[:30]:
            entry = table.get(handle)
            if entry is None:
                continue
            try:
                self._check(entry["path"], entry["policy"])
            except Unavailable as error:
                shown.append({"handle": handle, "status": "unavailable", "reason": str(error)})
                continue
            shown.append(
                {
                    "handle": handle,
                    "title": entry["title"],
                    "lines": _locator(entry)[1:],
                    "text": self.chunk_text(handle)[:EVAL_EXCERPT_CHARS],
                }
            )
        return shown

    def citation(self, handle: str) -> dict[str, Any]:
        """Core citation ``{resource_reference, chunk_locator, chunk_hash}`` for a handle.

        Digests use the Core ``sha256:<hex>`` form; the handle table keeps bare hex internally.
        """
        entry = self.table()[handle]
        data = self.owner.store.get_blob(entry["blob"])
        if data is None:
            raise LookupError(f"evidence bytes for {handle} are missing from State")
        start, end = entry["lines"]
        return {
            "resource_reference": resource_reference(entry["path"], parse(entry["path"], data), entry["policy"]),
            "chunk_locator": {"start_line": start, "end_line": end},
            "chunk_hash": f"sha256:{entry['chunk_hash']}",
        }

    def wikilink(self, handle: str) -> str:
        """``[[path|title]] (L3-5, 1a2b3c4d)``: a wikilink with line locator and short hash."""
        entry = self.table()[handle]
        title = re.sub(r"[\[\]|]", " ", entry["title"]).strip()
        return f"[[{entry['path'].removesuffix('.md')}|{title}]] ({_locator(entry)}, {entry['chunk_hash'][:8]})"

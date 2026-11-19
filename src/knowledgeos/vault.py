"""Vault access and the single Vault writer (Blueprint §5, §6).

Every modification is an exact-preimage atomic replace; outputs are create-only.
Intent is recorded before each effect and resolved by reading the file.
"""

from __future__ import annotations

import os
import re
import stat
import threading
from collections.abc import Callable
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any

from pathspec import GitIgnoreSpec

from . import OPERATION_ID
from .notes import Note, parse
from .policy import effective
from .util import atomic_write, create_exclusive, sha256

ATTACHMENT_ROOTS = ("80_Assets/",)
SYSTEM_ROOT = "99_System/"
REVIEW_ROOT = "01_AI_Review/"
REPORTS_DIR = "01_AI_Review/Reports"
PROPOSALS_DIR = "01_AI_Review/Proposals"
RESERVED_REQUESTS = "00_Inbox/Requests/"


class PreimageMismatch(Exception):
    """The file no longer has the expected bytes; nothing was written."""


class VaultPathError(ValueError):
    pass


# ai_policy -> Core classification. Personal notes are never "public"; deny is never referenced.
CLASSIFICATION = {"remote_ok": "internal", "ask": "confidential", "local_only": "restricted", "deny": "restricted"}
NOTE_ID_SEGMENT = re.compile(r"[a-z0-9][a-z0-9_-]{0,90}")


def resource_reference(path: str, note: Note, policy: str | None = None) -> dict[str, Any]:
    """Core ResourceReference for the exact bytes read (Blueprint §5.5).

    ``logical_id`` is ``note.<id>`` for a Core-safe note ID, else ``note.sha256-<hex of the ID>``,
    or ``path.sha256-<hex of the Vault path>`` for a note without ID. The Owner offers no export of
    note bytes, so availability is ``unavailable``; ``source_revision`` is the content SHA-256.
    """
    if note.note_id and NOTE_ID_SEGMENT.fullmatch(note.note_id):
        logical_id = f"note.{note.note_id}"
    elif note.note_id:
        logical_id = f"note.sha256-{sha256(note.note_id.encode('utf-8'))}"
    else:
        logical_id = f"path.sha256-{sha256(path.encode('utf-8'))}"
    return {
        "owner_operation_id": OPERATION_ID,
        "resource_kind": "vault",
        "logical_id": logical_id,
        "content_digest": f"sha256:{note.sha}",
        "source_revision": note.sha,
        "availability": "unavailable",
        "classification": CLASSIFICATION[policy or effective(note)],
    }


class Vault:
    def __init__(self, root: Path):
        self.root = root.resolve()
        self._write_lock = threading.Lock()
        self._ignore_lock = threading.RLock()
        self._ignore_cache: dict[Path, tuple[tuple[int, ...] | None, GitIgnoreSpec]] = {}

    # ------------------------------------------------------------- paths

    def normalize(self, rel: str) -> str:
        original = rel
        rel = rel.replace("\\", "/")
        pure = PurePosixPath(rel)
        if (
            not rel
            or "\x00" in rel
            or ".." in pure.parts
            or pure.is_absolute()
            or PureWindowsPath(rel).drive
        ):
            raise VaultPathError(f"invalid vault path: {original!r}")
        return str(pure)

    def abs(self, rel: str) -> Path:
        """Resolve only permitted paths, without following any symlink component."""
        rel = self.normalize(rel)
        path = self.root / rel
        if self._device_local(rel):
            raise VaultPathError(f"excluded vault path: {rel}")
        current = self.root
        for part in PurePosixPath(rel).parts:
            current /= part
            try:
                mode = current.lstat().st_mode
            except FileNotFoundError:
                break
            except NotADirectoryError as error:
                raise VaultPathError(f"invalid vault path: {rel}") from error
            if stat.S_ISLNK(mode) or not (stat.S_ISDIR(mode) or stat.S_ISREG(mode)):
                raise VaultPathError(f"unsafe vault path: {rel}")
        if self._git_ignored(rel, path.is_dir()):
            raise VaultPathError(f"Git-ignored vault path: {rel}")
        return path

    @staticmethod
    def _device_local(rel: str) -> bool:
        parts = PurePosixPath(rel).parts
        return any(part.startswith(".") for part in parts) or (
            bool(parts) and f"{parts[0]}/" in ATTACHMENT_ROOTS
        )

    @staticmethod
    def ignored(rel: str) -> bool:
        """Lexical exclusion for pure rules; instance I/O also enforces Git ignores."""
        return Vault._device_local(rel) or not rel.endswith(".md")

    def _ignore_spec(self, directory: Path) -> GitIgnoreSpec:
        path = directory / ".gitignore"
        with self._ignore_lock:
            try:
                info = path.lstat()
            except (FileNotFoundError, NotADirectoryError):
                identity = None
            else:
                if not stat.S_ISREG(info.st_mode):
                    raise VaultPathError("unsafe .gitignore")
                identity = (info.st_dev, info.st_ino, info.st_mtime_ns, info.st_ctime_ns, info.st_size)
            cached = self._ignore_cache.get(path)
            if cached is not None and cached[0] == identity:
                return cached[1]
            lines = path.read_text(encoding="utf-8", errors="surrogateescape").splitlines() if identity else []
            spec = GitIgnoreSpec.from_lines(lines)
            self._ignore_cache[path] = (identity, spec)
            return spec

    def _git_ignored(self, rel: str, is_dir: bool = False) -> bool:
        parts = PurePosixPath(rel).parts
        specs = [(0, self._ignore_spec(self.root))]
        for index in range(len(parts)):
            directory = index < len(parts) - 1 or is_dir
            ignored = False
            for depth, spec in specs:
                candidate = "/".join(parts[depth : index + 1]) + ("/" if directory else "")
                match = spec.check_file(candidate).include
                if match is not None:
                    ignored = match
            if ignored:
                return True  # Git cannot re-include a file below an excluded directory.
            if directory and index < len(parts) - 1:
                specs.append((index + 1, self._ignore_spec(self.root.joinpath(*parts[: index + 1]))))
        return False

    def _note_path(self, rel: str) -> Path:
        rel = self.normalize(rel)
        if self.ignored(rel):
            raise VaultPathError(f"excluded vault note: {rel}")
        return self.abs(rel)

    @staticmethod
    def system(rel: str) -> bool:
        return rel.startswith(SYSTEM_ROOT)

    @staticmethod
    def review(rel: str) -> bool:
        return rel.startswith(REVIEW_ROOT)

    def iter_paths(self, under: str = "") -> list[str]:
        base = self.abs(under) if under else self.root
        found: list[str] = []
        if base.is_file():
            rel = base.relative_to(self.root).as_posix()
            return [] if self.ignored(rel) else [rel]
        for directory, dirnames, filenames in os.walk(base, followlinks=False):
            permitted = []
            for name in sorted(dirnames):
                rel = (Path(directory) / name).relative_to(self.root).as_posix()
                try:
                    self.abs(rel)
                except VaultPathError:
                    continue
                permitted.append(name)
            dirnames[:] = permitted
            for name in sorted(filenames):
                rel = (Path(directory) / name).relative_to(self.root).as_posix()
                if self.ignored(rel):
                    continue
                try:
                    self._note_path(rel)
                except VaultPathError:
                    continue
                found.append(rel)
        return found

    def exists(self, rel: str) -> bool:
        return self._note_path(rel).is_file()

    def read(self, rel: str) -> Note | None:
        try:
            raw = self._note_path(rel).read_bytes()
        except (FileNotFoundError, IsADirectoryError, NotADirectoryError):
            return None
        return parse(self.normalize(rel), raw)

    def stat(self, rel: str) -> os.stat_result | None:
        try:
            return self._note_path(rel).stat()
        except (FileNotFoundError, NotADirectoryError):
            return None

    def find_by_id(self, note_id: str) -> list[str]:
        matches = []
        for rel in self.iter_paths():
            note = self.read(rel)
            if note is not None and note.note_id == note_id:
                matches.append(rel)
        return matches

    def resolve_link(self, target: str, paths: list[str] | None = None) -> str | None:
        """Resolve a wikilink target (path or basename, no extension) to a vault path."""
        target = target.strip().removesuffix(".md")
        candidates = paths if paths is not None else self.iter_paths()
        exact = f"{target}.md"
        if exact in candidates:
            return exact
        named = [rel for rel in candidates if rel.rsplit("/", 1)[-1] == f"{target.rsplit('/', 1)[-1]}.md"]
        return named[0] if len(named) == 1 else None

    # ------------------------------------------------------------- effects

    def replace(self, rel: str, expected_sha: str, data: bytes, record: Callable[[str, dict], None]) -> None:
        """Exact-preimage atomic replace. Raises PreimageMismatch without writing."""
        rel = self.normalize(rel)
        with self._write_lock:
            self._note_path(rel)
            intent = {"op": "replace", "path": rel, "preimage": expected_sha, "result": sha256(data)}
            record("intent", intent)
            path = self._note_path(rel)
            try:
                current = sha256(path.read_bytes())
            except FileNotFoundError:
                current = None
            if current != expected_sha:
                record("outcome", {**intent, "state": "not_applied", "observed": current})
                raise PreimageMismatch(rel)
            mode = path.stat().st_mode & 0o777
            atomic_write(path, data, mode)
            record("outcome", {**intent, "state": "applied"})

    def create(self, rel: str, data: bytes, record: Callable[[str, dict], None]) -> str:
        """Create a new file; a name collision gets a numeric suffix. Returns the final path."""
        rel = self.normalize(rel)
        self._note_path(rel)
        stem, suffix = rel.rsplit(".", 1)
        with self._write_lock:
            for attempt in range(1, 1000):
                candidate = rel if attempt == 1 else f"{stem} {attempt}.{suffix}"
                path = self._note_path(candidate)
                if path.exists():
                    continue
                intent = {"op": "create", "path": candidate, "preimage": None, "result": sha256(data)}
                record("intent", intent)
                path = self._note_path(candidate)
                create_exclusive(path, data)
                record("outcome", {**intent, "state": "applied"})
                return candidate
        raise VaultPathError(f"no free name for {rel}")

    def move(self, rel: str, expected_sha: str, new_rel: str, data: bytes,
             record: Callable[[str, dict], None]) -> str:
        """Move a note after an exact-preimage check, writing data at the new path."""
        rel, new_rel = self.normalize(rel), self.normalize(new_rel)
        with self._write_lock:
            self._note_path(rel)
            self._note_path(new_rel)
            intent = {"op": "move", "path": rel, "to": new_rel, "preimage": expected_sha, "result": sha256(data)}
            record("intent", intent)
            source = self._note_path(rel)
            target = self._note_path(new_rel)
            try:
                current = sha256(source.read_bytes())
            except FileNotFoundError:
                current = None
            if current != expected_sha:
                record("outcome", {**intent, "state": "not_applied", "observed": current})
                raise PreimageMismatch(rel)
            if target.exists():
                record("outcome", {**intent, "state": "not_applied", "observed": "target exists"})
                raise PreimageMismatch(new_rel)
            mode = source.stat().st_mode & 0o777
            atomic_write(target, data, mode)
            source.unlink()
            record("outcome", {**intent, "state": "applied"})
            return new_rel

    def delete_own(self, rel: str, expected_sha: str | None, record: Callable[[str, dict], None]) -> None:
        """Delete a KnowledgeOS-written file (proposals only). Never used for user notes."""
        rel = self.normalize(rel)
        if not rel.startswith(PROPOSALS_DIR + "/"):
            raise VaultPathError("KnowledgeOS deletes only its own proposal files")
        with self._write_lock:
            self._note_path(rel)
            intent = {"op": "delete", "path": rel, "preimage": expected_sha, "result": None}
            record("intent", intent)
            path = self._note_path(rel)
            if not path.exists():
                record("outcome", {**intent, "state": "applied", "observed": "absent"})
                return
            if expected_sha is not None and sha256(path.read_bytes()) != expected_sha:
                record("outcome", {**intent, "state": "not_applied"})
                raise PreimageMismatch(rel)
            path.unlink()
            record("outcome", {**intent, "state": "applied"})

    def observe_effect(self, intent: dict) -> str:
        """Resolve an effect intent by reading the file: applied, not_applied or conflict."""
        target = intent.get("to") or intent["path"]
        path = self._note_path(target)
        current = sha256(path.read_bytes()) if path.is_file() else None
        if intent["op"] == "delete":
            return "applied" if current is None else "not_applied"
        if current == intent["result"]:
            return "applied"
        if intent["op"] == "move":
            source = self._note_path(intent["path"])
            source_sha = sha256(source.read_bytes()) if source.is_file() else None
            return "not_applied" if source_sha == intent["preimage"] and current is None else "conflict"
        if current == intent["preimage"]:
            return "not_applied"
        return "conflict"

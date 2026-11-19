"""The one capability catalog plus role and job declarations (Blueprint §8.3, §8.6).

``config/capabilities/<Cn>.json`` holds one capability each; this module merges them.
Files are cached by identity (inode, size, mtime) and reparsed only when they change.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any


class CatalogError(ValueError):
    pass


def _identity(path: Path) -> tuple[int, int, int] | None:
    try:
        stat = path.stat()
    except FileNotFoundError:
        return None
    return (stat.st_ino, stat.st_size, stat.st_mtime_ns)


class Catalog:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.config_dir = self.root / "config"
        self._cache: dict[Path, tuple[Any, Any]] = {}
        self._merged: tuple[Any, dict[str, dict[str, Any]], dict[str, str]] | None = None
        self._lock = threading.Lock()

    def _load(self, path: Path, default: Any) -> Any:
        identity = _identity(path)
        if identity is None:
            return default
        cached = self._cache.get(path)
        if cached is not None and cached[0] == identity:
            return cached[1]
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as error:
            raise CatalogError(f"{path.name}: {error}") from error
        self._cache[path] = (identity, value)
        return value

    # ------------------------------------------------------------ capabilities

    def _capability_files(self) -> list[Path]:
        directory = self.config_dir / "capabilities"
        return sorted(directory.glob("C*.json")) if directory.is_dir() else []

    def capabilities(self) -> dict[str, dict[str, Any]]:
        with self._lock:
            files = self._capability_files()
            key = tuple((path, _identity(path)) for path in files)
            if self._merged is not None and self._merged[0] == key:
                return self._merged[1]
            merged: dict[str, dict[str, Any]] = {}
            commands: dict[str, str] = {}
            for path in files:
                entry = self._load(path, None)
                if not isinstance(entry, dict) or entry.get("id") != path.stem:
                    raise CatalogError(f"{path.name}: id must equal the file name")
                if not isinstance(entry.get("version"), str):
                    raise CatalogError(f"{path.name}: version must be a string")
                for name in entry.get("commands", {}):
                    if name in commands:
                        raise CatalogError(f"command {name} declared by {commands[name]} and {path.stem}")
                    commands[name] = path.stem
                merged[path.stem] = entry
            self._merged = (key, merged, commands)
            return merged

    def capability(self, capability_id: str) -> dict[str, Any] | None:
        return self.capabilities().get(capability_id)

    def command(self, name: str) -> tuple[str, dict[str, Any]] | None:
        """The declaring capability ID and the command declaration."""
        capabilities = self.capabilities()
        assert self._merged is not None
        capability_id = self._merged[2].get(name)
        if capability_id is None:
            return None
        return capability_id, capabilities[capability_id]["commands"][name]

    def version(self, capability_id: str) -> str | None:
        entry = self.capability(capability_id)
        return entry["version"] if entry else None

    def criteria(self, capability_id: str, mode: str) -> list[dict[str, Any]]:
        """The admitted criteria for one capability mode, each ``{id, runtime, text}``."""
        entry = self.capability(capability_id) or {}
        declared = entry.get("criteria", {})
        ids = entry.get("modes", {}).get(mode, {}).get("criteria", [])
        missing = [criterion for criterion in ids if criterion not in declared]
        if missing:
            raise CatalogError(f"{capability_id}/{mode}: undeclared criteria {missing}")
        return [{"id": criterion, **declared[criterion]} for criterion in ids]

    def budget(self, capability_id: str, mode: str) -> dict[str, Any]:
        entry = self.capability(capability_id) or {}
        mode_entry = entry.get("modes", {}).get(mode, {})
        return {**entry.get("budget", {}), **mode_entry.get("budget", {})}

    def settings(self, capability_id: str) -> dict[str, Any]:
        entry = self.capability(capability_id) or {}
        return dict(entry.get("settings", {}))

    # ------------------------------------------------------------ roles and jobs

    def roles(self) -> dict[str, dict[str, Any]]:
        with self._lock:
            return self._load(self.config_dir / "roles.json", {}).get("roles", {})

    def role(self, role_id: str) -> dict[str, Any] | None:
        return self.roles().get(role_id)

    def jobs(self) -> dict[str, dict[str, Any]]:
        with self._lock:
            return self._load(self.config_dir / "jobs.json", {}).get("jobs", {})

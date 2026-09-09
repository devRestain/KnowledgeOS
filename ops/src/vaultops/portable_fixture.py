"""S07 fixed-fixture validation for the portable Vault exit gate.

The fixture is deliberately a read-only evidence surface.  It does not
implement archive, capture-finalize, or asset-import mutations; those writers
belong to later command slices.  Instead, S07 checks the concrete input and
expected bytes that those later writers must preserve, together with the
already-owned note and Base contracts.
"""

from __future__ import annotations

import hashlib
import os
import re
import shutil
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from .base_dashboard import FrozenNote, evaluate_records
from .note_engine import (
    FrontmatterError,
    NoteEngine,
    normalize_vault_relative_path,
    parse_frontmatter,
    resolve_vault_relative_path,
)
from .yaml_safe import load_yaml_file

FIXTURE_NAME = "guestbook-horror"
FIXTURE_RELATIVE_ROOT = "ops/tests/fixtures/s07_portable_vault/guestbook-horror"
_WIKILINK_RE = re.compile(r"\[\[([^\]\n]+)\]\]")


@dataclass(frozen=True)
class FixtureIssue:
    """One stable S07 fixture diagnostic."""

    code: str
    path: str
    message: str
    details: dict[str, Any] | None = None

    def as_dict(self) -> dict[str, Any]:
        result = {"code": self.code, "path": self.path, "message": self.message}
        if self.details:
            result["details"] = dict(self.details)
        return result


@dataclass(frozen=True)
class FixtureReport:
    """Deterministic, read-only result for one fixture section."""

    fixture: str
    section: str
    issues: tuple[FixtureIssue, ...] = ()
    query_results: tuple[dict[str, Any], ...] = ()

    @property
    def passed(self) -> bool:
        return not self.issues

    @property
    def codes(self) -> tuple[str, ...]:
        return tuple(issue.code for issue in self.issues)

    def as_dict(self) -> dict[str, Any]:
        return {
            "fixture": self.fixture,
            "section": self.section,
            "status": "PASS" if self.passed else "FAIL",
            "issues": [issue.as_dict() for issue in self.issues],
            "query_results": list(self.query_results),
        }


def fixture_root(workspace_root: str | Path) -> Path:
    """Return the checked-in S07 fixture root."""

    return Path(workspace_root).resolve() / FIXTURE_RELATIVE_ROOT


def load_fixture_manifest(workspace_root: str | Path) -> dict[str, Any]:
    """Load the safe, fixed input/expected manifest."""

    manifest = load_yaml_file(fixture_root(workspace_root) / "manifest.yaml")
    if manifest.get("schema_version") != 1 or manifest.get("fixture") != FIXTURE_NAME:
        raise ValueError("S07 fixture manifest has an unsupported identity")
    return manifest


def _issue(code: str, path: str, message: str, details: Mapping[str, Any] | None = None) -> FixtureIssue:
    return FixtureIssue(code, path, message, dict(details) if details else None)


def _manifest_entries(section: Mapping[str, Any]) -> tuple[Mapping[str, Any], ...]:
    entries = section.get("files")
    if not isinstance(entries, list):
        raise TypeError("S07 manifest section files must be a list")
    result: list[Mapping[str, Any]] = []
    for entry in entries:
        if not isinstance(entry, Mapping) or not isinstance(entry.get("path"), str):
            raise TypeError("S07 manifest file entry needs a path")
        normalize_vault_relative_path(entry["path"])
        if not isinstance(entry.get("sha256"), str) or len(entry["sha256"]) != 64:
            raise ValueError(f"S07 manifest hash is invalid: {entry.get('path')}")
        if not isinstance(entry.get("mtime"), int):
            raise TypeError(f"S07 manifest mtime is invalid: {entry.get('path')}")
        result.append(entry)
    return tuple(result)


def _manifest_entry_map(section: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    entries = _manifest_entries(section)
    result: dict[str, Mapping[str, Any]] = {}
    for entry in entries:
        path = str(entry["path"])
        if path in result:
            raise ValueError(f"S07 manifest contains a duplicate path: {path}")
        result[path] = entry
    return result


def verify_manifest_tree(
    section_root: str | Path,
    section: Mapping[str, Any],
    *,
    check_mtime: bool = False,
) -> tuple[FixtureIssue, ...]:
    """Check exact file set, bytes, and optionally fixed mtimes."""

    root = Path(section_root)
    try:
        expected = _manifest_entry_map(section)
    except (TypeError, ValueError) as error:
        return (_issue("FIXTURE_MANIFEST_INVALID", "/manifest", str(error)),)
    issues: list[FixtureIssue] = []
    if root.is_symlink() or not root.is_dir():
        return (_issue("FIXTURE_ROOT_MISSING", str(root), "fixture section root is missing or symlinked"),)

    actual_paths: set[str] = set()
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            relative = path.relative_to(root).as_posix()
            issues.append(_issue("FIXTURE_SYMLINK", relative, "fixture must not contain symlinks"))
        elif path.is_file():
            actual_paths.add(path.relative_to(root).as_posix())
    for relative in sorted(actual_paths - set(expected)):
        issues.append(_issue("FIXTURE_UNEXPECTED_FILE", relative, "file is not listed in the manifest"))
    for relative in sorted(set(expected) - actual_paths):
        issues.append(_issue("FIXTURE_FILE_MISSING", relative, "manifest file is missing"))

    for relative, entry in expected.items():
        path = root / relative
        if not path.is_file() or path.is_symlink():
            continue
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if digest != entry["sha256"]:
            issues.append(
                _issue(
                    "FIXTURE_HASH_MISMATCH",
                    relative,
                    "fixture bytes differ from the fixed manifest",
                    {"expected": entry["sha256"], "observed": digest},
                )
            )
        if check_mtime and int(path.stat().st_mtime) != entry["mtime"]:
            issues.append(
                _issue(
                    "FIXTURE_MTIME_MISMATCH",
                    relative,
                    "fixture mtime differs from the fixed manifest",
                    {"expected": entry["mtime"], "observed": int(path.stat().st_mtime)},
                )
            )
    return tuple(issues)


def _target_index(
    parsed: Mapping[str, tuple[str, dict[str, Any], str]],
) -> tuple[dict[str, str], tuple[FixtureIssue, ...]]:
    targets: dict[str, str] = {}
    issues: list[FixtureIssue] = []
    for relative, (_, properties, _) in parsed.items():
        keys = {
            relative.removesuffix(".md"),
            PurePosixPath(relative).stem,
            str(properties.get("title", "")),
        }
        for key in keys - {""}:
            previous = targets.get(key)
            if previous is not None and previous != relative:
                issues.append(
                    _issue(
                        "FIXTURE_LINK_TARGET_AMBIGUOUS",
                        relative,
                        "wikilink target resolves to more than one fixture note",
                        {"target": key, "other_path": previous},
                    )
                )
            else:
                targets[key] = relative
    return targets, tuple(issues)


def _locator_exists(body: str, fragment: str) -> bool:
    if fragment.startswith("^"):
        return fragment in body
    wanted = fragment.strip().casefold()
    return any(line.lstrip("#").strip().casefold() == wanted for line in body.splitlines())


def _validate_wikilink_locators(
    vault_root: Path,
    relative: str,
    body: str,
    targets: Mapping[str, str],
) -> list[FixtureIssue]:
    issues: list[FixtureIssue] = []
    for match in _WIKILINK_RE.finditer(body):
        link = match.group(1)
        target, separator, fragment = link.partition("#")
        if not separator:
            continue
        target_path = targets.get(target) or targets.get(PurePosixPath(target).stem)
        if target_path is None:
            continue
        destination = vault_root / target_path
        try:
            destination_body = parse_frontmatter(destination.read_text(encoding="utf-8")).body
        except (OSError, UnicodeError, FrontmatterError):
            continue
        if not _locator_exists(destination_body, fragment):
            issues.append(
                _issue(
                    "FIXTURE_LOCATOR_UNRESOLVED",
                    relative,
                    "wikilink locator does not exist in its target note",
                    {"link": link, "target_path": target_path},
                )
            )
    return issues


def _relation_link_text(properties: Mapping[str, Any], body: str) -> str:
    """Return body and frontmatter relation values for locator checking."""

    values: list[str] = [body]
    for value in properties.values():
        if isinstance(value, str):
            values.append(value)
        elif isinstance(value, list):
            values.extend(item for item in value if isinstance(item, str))
    return "\n".join(values)


def validate_note_collection(
    workspace_root: str | Path,
    vault_root: str | Path,
    note_paths: Iterable[str],
    *,
    fixture: str = FIXTURE_NAME,
    section: str = "input",
) -> FixtureReport:
    """Validate a fixed note collection, IDs, relation types, and locators."""

    workspace = Path(workspace_root).resolve()
    vault = Path(vault_root).resolve()
    engine = NoteEngine.from_root(workspace)
    parsed: dict[str, tuple[str, dict[str, Any], str]] = {}
    issues: list[FixtureIssue] = []
    ids: dict[str, str] = {}
    for raw_relative in note_paths:
        try:
            relative = normalize_vault_relative_path(raw_relative)
            path = resolve_vault_relative_path(vault, relative)
            markdown = path.read_text(encoding="utf-8")
            document = parse_frontmatter(markdown)
        except (OSError, UnicodeError, FrontmatterError, ValueError) as error:
            issues.append(_issue("FIXTURE_NOTE_INVALID", str(raw_relative), str(error)))
            continue
        note_type = str(document.properties.get("type", ""))
        parsed[relative] = (note_type, document.properties, document.body)
        identifier = document.properties.get("id")
        if isinstance(identifier, str):
            previous = ids.get(identifier)
            if previous is not None:
                issues.append(
                    _issue(
                        "FIXTURE_DUPLICATE_ID",
                        relative,
                        "note id is duplicated in the fixture",
                        {"id": identifier, "other_path": previous},
                    )
                )
            else:
                ids[identifier] = relative

    targets, target_issues = _target_index(parsed)
    issues.extend(target_issues)
    target_types = {
        key: parsed[relative][0]
        for key, relative in targets.items()
        if relative in parsed
    }
    for relative, (_, properties, body) in parsed.items():
        markdown = (vault / relative).read_text(encoding="utf-8")
        result = engine.validate_text(relative, markdown, target_types=target_types)
        issues.extend(
            _issue(issue.code, relative, issue.message, issue.details) for issue in result.errors
        )
        issues.extend(
            _validate_wikilink_locators(
                vault,
                relative,
                _relation_link_text(properties, body),
                targets,
            )
        )
    return FixtureReport(fixture, section, tuple(issues))


def _query_results(
    workspace_root: Path,
    manifest: Mapping[str, Any],
    section: Mapping[str, Any],
) -> tuple[tuple[dict[str, Any], ...], tuple[FixtureIssue, ...]]:
    blueprint = load_yaml_file(workspace_root / "blueprint/blueprint.yaml")
    entries = _manifest_entry_map(section)
    note_paths = section.get("note_paths", [])
    if not isinstance(note_paths, list):
        return (), (_issue("FIXTURE_MANIFEST_INVALID", "/note_paths", "note_paths must be a list"),)
    records: list[FrozenNote] = []
    for relative in note_paths:
        entry = entries.get(relative)
        if entry is None:
            return (), (_issue("FIXTURE_MANIFEST_INVALID", relative, "query note is not in file manifest"),)
        note_path = fixture_root(workspace_root) / str(section["root"]) / relative
        try:
            properties = parse_frontmatter(note_path.read_text(encoding="utf-8")).properties
        except (OSError, UnicodeError, FrontmatterError) as error:
            return (), (_issue("FIXTURE_NOTE_INVALID", relative, str(error)),)
        records.append(FrozenNote(relative, properties, float(entry["mtime"])))

    results: list[dict[str, Any]] = []
    issues: list[FixtureIssue] = []
    queries = manifest.get("queries", [])
    if not isinstance(queries, list):
        return (), (_issue("FIXTURE_MANIFEST_INVALID", "/queries", "queries must be a list"),)
    for query in queries:
        if not isinstance(query, Mapping):
            issues.append(_issue("FIXTURE_MANIFEST_INVALID", "/queries", "query must be a mapping"))
            continue
        base = str(query.get("base", ""))
        view = str(query.get("view", ""))
        expected_paths = query.get("expected_paths", [])
        if not isinstance(expected_paths, list):
            issues.append(_issue("FIXTURE_MANIFEST_INVALID", f"{base}#{view}", "expected_paths must be a list"))
            continue
        try:
            rows = evaluate_records(blueprint, base, view, records, today=manifest.get("today"))
        except (KeyError, TypeError, ValueError) as error:
            issues.append(_issue("FIXTURE_QUERY_INVALID", f"{base}#{view}", str(error)))
            continue
        actual_paths = [str(row["path"]) for row in rows]
        if actual_paths != [str(path) for path in expected_paths]:
            issues.append(
                _issue(
                    "FIXTURE_QUERY_MISMATCH",
                    f"{base}#{view}",
                    "canonical Base query result differs from the fixed expectation",
                    {"expected": expected_paths, "observed": actual_paths},
                )
            )
        results.append({"base": base, "view": view, "paths": actual_paths})
    return tuple(results), tuple(issues)


def validate_guestbook_fixture(
    workspace_root: str | Path,
    *,
    section_name: str = "input",
    check_mtime: bool = False,
) -> FixtureReport:
    """Run the S07 exact-byte, note, and (for input) Base query gate."""

    workspace = Path(workspace_root).resolve()
    manifest = load_fixture_manifest(workspace)
    section = manifest.get(section_name)
    if not isinstance(section, Mapping) or not isinstance(section.get("root"), str):
        return FixtureReport(FIXTURE_NAME, section_name, (_issue("FIXTURE_MANIFEST_INVALID", f"/{section_name}", "section is invalid"),))
    root = fixture_root(workspace) / str(section["root"])
    issues = list(verify_manifest_tree(root, section, check_mtime=check_mtime))
    note_paths = section.get("note_paths", [])
    if not isinstance(note_paths, list):
        issues.append(_issue("FIXTURE_MANIFEST_INVALID", f"/{section_name}/note_paths", "note_paths must be a list"))
        return FixtureReport(FIXTURE_NAME, section_name, tuple(issues))
    note_report = validate_note_collection(workspace, root, note_paths, section=section_name)
    issues.extend(note_report.issues)
    query_results: tuple[dict[str, Any], ...] = ()
    if section_name == "input" and not issues:
        query_results, query_issues = _query_results(workspace, manifest, section)
        issues.extend(query_issues)
    return FixtureReport(FIXTURE_NAME, section_name, tuple(issues), query_results)


def materialize_guestbook_vault(
    workspace_root: str | Path,
    destination: str | Path,
    *,
    section_name: str = "input",
) -> Path:
    """Copy one fixed fixture section to a new directory and apply its mtimes."""

    workspace = Path(workspace_root).resolve()
    target = Path(destination).resolve()
    if target.exists():
        raise FileExistsError(f"refusing to materialize over an existing path: {target}")
    manifest = load_fixture_manifest(workspace)
    section = manifest.get(section_name)
    if not isinstance(section, Mapping) or not isinstance(section.get("root"), str):
        raise TypeError(f"fixture section is invalid: {section_name}")
    source = fixture_root(workspace) / str(section["root"])
    target.mkdir(parents=True)
    for entry in _manifest_entries(section):
        relative = str(entry["path"])
        source_path = source / relative
        target_path = target / relative
        target_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source_path, target_path)
        os.utime(target_path, (entry["mtime"], entry["mtime"]))
    return target


def materialize_phase1_smoke_vault(
    workspace_root: str | Path,
    destination: str | Path,
) -> Path:
    """Create a disposable app-smoke Vault from owned core artifacts and input notes."""

    workspace = Path(workspace_root).resolve()
    target = Path(destination).resolve()
    if target.exists():
        raise FileExistsError(f"refusing to materialize over an existing path: {target}")
    source_vault = workspace / "vault"
    if source_vault.is_symlink() or not source_vault.is_dir():
        raise ValueError("workspace Vault root is missing or symlinked")
    target.mkdir(parents=True)
    for source_path in sorted(source_vault.rglob("*")):
        relative = source_path.relative_to(source_vault)
        if any(part.startswith(".") for part in relative.parts):
            continue
        target_path = target / relative
        if source_path.is_symlink():
            raise ValueError(f"workspace Vault contains an unsafe symlink: {relative}")
        if source_path.is_dir():
            target_path.mkdir(parents=True, exist_ok=True)
        elif source_path.is_file():
            target_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source_path, target_path)

    manifest = load_fixture_manifest(workspace)
    section = manifest["input"]
    fixture_input = fixture_root(workspace) / str(section["root"])
    for entry in _manifest_entries(section):
        relative = str(entry["path"])
        source_path = fixture_input / relative
        target_path = target / relative
        if target_path.exists() or target_path.is_symlink():
            raise FileExistsError(f"smoke fixture would overwrite an owned Vault file: {relative}")
        target_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source_path, target_path)
        os.utime(target_path, (entry["mtime"], entry["mtime"]))
    return target

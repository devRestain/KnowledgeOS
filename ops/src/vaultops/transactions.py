"""S13C local, interactive transaction gates.

These operations are deliberately provider-free and explicit.  Asset imports
are create-only; capture finalization and project archival require a caller
supplied digest snapshot before they can move or replace anything.  Runtime
journals, crash recovery, bridge publishing, and Git effects belong to later
slices and are not implemented here.
"""

from __future__ import annotations

import hashlib
import mimetypes
import os
import re
import stat
import uuid
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path
from typing import Any

from .note_engine import (
    FrontmatterError,
    NoteEngine,
    UnsafePathError,
    normalize_vault_relative_path,
    parse_frontmatter,
    render_frontmatter,
    resolve_vault_relative_path,
    write_note_file,
)
from .workflows import validate_title

EXIT_OK = 0
EXIT_CONFLICT = 30
EXIT_INPUT_INVALID = 10
EXIT_VALIDATION_FAILED = 13

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_ASSET_DIRECTORIES = frozenset({"Inbox", "Images", "Documents", "Audio"})
_FORBIDDEN_MIME_TYPES = frozenset(
    {
        "application/x-executable",
        "application/x-sh",
        "application/x-shellscript",
        "application/x-msdownload",
        "application/x-dosexec",
    }
)
_FORBIDDEN_SUFFIXES = frozenset({".app", ".com", ".exe", ".js", ".jse", ".sh", ".command", ".bat", ".cmd"})
_SOFT_WARNING_BYTES = 262_144_000


class TransactionInputError(ValueError):
    """Raised when a transaction input cannot safely reach a writer."""


def _issue(code: str, locator: str, message: str, **details: Any) -> dict[str, Any]:
    result: dict[str, Any] = {"code": code, "locator": locator, "message": message}
    if details:
        result["details"] = details
    return result


def _fail(operation: str, code: str, message: str, *, exit_code: int = EXIT_INPUT_INVALID) -> tuple[dict[str, Any], int]:
    return {
        "status": "FAIL",
        "operation": operation,
        "errors": [_issue(code, "/", message)],
        "created": [],
    }, exit_code


def _workspace_and_vault(root: str | Path) -> tuple[Path, Path]:
    candidate = Path(root).expanduser()
    if candidate.is_symlink() or not candidate.is_dir():
        raise TransactionInputError("control root must be an existing non-symlink directory")
    workspace = candidate.resolve()
    vault = workspace / "KnowledgeHub"
    if vault.is_symlink() or not vault.is_dir():
        raise TransactionInputError("Vault root must be an existing non-symlink directory")
    return workspace, vault.resolve()


def _safe_vault_path(vault: Path, relative: str) -> Path:
    return resolve_vault_relative_path(vault, normalize_vault_relative_path(relative))


def _validated_sha256(value: str, *, label: str) -> str:
    if not isinstance(value, str) or not _SHA256_RE.fullmatch(value):
        raise TransactionInputError(f"{label} must be a lowercase SHA-256 digest")
    return value


def _hash_regular_file(path: Path, *, label: str) -> tuple[str, int]:
    if path.is_symlink() or not path.is_file():
        raise TransactionInputError(f"{label} must be an existing regular non-symlink file")
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        raise TransactionInputError(f"{label} could not be opened safely") from error
    digest = hashlib.sha256()
    size = 0
    try:
        with os.fdopen(descriptor, "rb") as handle:
            descriptor = -1
            file_stat = os.fstat(handle.fileno())
            if not stat.S_ISREG(os.stat(path, follow_symlinks=False).st_mode):
                raise TransactionInputError(f"{label} must be a regular file")
            while True:
                chunk = handle.read(1024 * 1024)
                if not chunk:
                    break
                digest.update(chunk)
                size += len(chunk)
            if file_stat.st_size != size:
                raise TransactionInputError(f"{label} changed while it was being read")
    finally:
        if descriptor != -1:
            os.close(descriptor)
    return digest.hexdigest(), size


def _safe_asset_filename(raw: str) -> str:
    if not isinstance(raw, str) or not raw or Path(raw).name != raw:
        raise TransactionInputError("asset filename must be one path component")
    if raw in {".", ".."} or raw.startswith(".") or "\x00" in raw:
        raise TransactionInputError("asset filename is protected")
    try:
        return validate_title(raw)
    except ValueError as error:
        raise TransactionInputError(str(error)) from error


def _asset_mime(filename: str, explicit: str | None) -> str | None:
    if explicit is not None:
        if not isinstance(explicit, str) or not re.fullmatch(r"[a-z0-9.+-]+/[a-z0-9.+-]+", explicit):
            raise TransactionInputError("asset MIME type is invalid")
        return explicit
    return mimetypes.guess_type(filename, strict=False)[0]


def _safe_target_file(vault: Path, relative: str) -> Path:
    target = _safe_vault_path(vault, relative)
    if target.is_symlink():
        raise TransactionInputError("asset target must not be a symlink")
    if target.exists():
        raise FileExistsError(target)
    if target.parent.is_symlink() or not target.parent.is_dir():
        raise TransactionInputError("asset target directory must be an existing real directory")
    return target


def _copy_create_only(source: Path, target: Path) -> tuple[str, int]:
    """Copy one regular file and atomically publish it without overwrite."""

    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    source_descriptor = os.open(source, flags)
    temporary = target.with_name(f".{target.name}.import-{uuid.uuid4().hex}.tmp")
    temp_descriptor = -1
    digest = hashlib.sha256()
    size = 0
    try:
        with os.fdopen(source_descriptor, "rb") as source_handle:
            source_descriptor = -1
            source_stat = os.fstat(source_handle.fileno())
            if not stat.S_ISREG(os.stat(source, follow_symlinks=False).st_mode):
                raise TransactionInputError("asset source must be a regular file")
            temp_flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
            if hasattr(os, "O_NOFOLLOW"):
                temp_flags |= os.O_NOFOLLOW
            temp_descriptor = os.open(temporary, temp_flags, 0o600)
            with os.fdopen(temp_descriptor, "wb") as target_handle:
                temp_descriptor = -1
                while True:
                    chunk = source_handle.read(1024 * 1024)
                    if not chunk:
                        break
                    digest.update(chunk)
                    size += len(chunk)
                    target_handle.write(chunk)
                target_handle.flush()
                os.fsync(target_handle.fileno())
            if source_stat.st_size != size:
                raise TransactionInputError("asset source changed while it was being copied")
        try:
            os.link(temporary, target)
        finally:
            if temporary.exists() and not temporary.is_symlink():
                temporary.unlink()
        return digest.hexdigest(), size
    finally:
        if source_descriptor != -1:
            os.close(source_descriptor)
        if temp_descriptor != -1:
            os.close(temp_descriptor)
        if temporary.exists() and not temporary.is_symlink():
            temporary.unlink()


def import_asset(
    root: str | Path,
    *,
    source_path: str | Path,
    target_directory: str = "Inbox",
    filename: str | None = None,
    mime_type: str | None = None,
    expected_sha256: str | None = None,
    dry_run: bool = False,
) -> tuple[dict[str, Any], int]:
    """Import one explicit regular file into ``80_Assets`` create-only."""

    operation = "asset import"
    try:
        source = Path(source_path).expanduser()
        if not source.is_absolute() or source.is_symlink() or not source.is_file():
            raise TransactionInputError("asset source must be an existing absolute regular non-symlink file")
        if target_directory not in _ASSET_DIRECTORIES:
            raise TransactionInputError(f"asset target directory must be one of {sorted(_ASSET_DIRECTORIES)}")
        target_name = _safe_asset_filename(filename if filename is not None else source.name)
        resolved_mime = _asset_mime(target_name, mime_type)
        if resolved_mime in _FORBIDDEN_MIME_TYPES or Path(target_name).suffix.casefold() in _FORBIDDEN_SUFFIXES:
            raise TransactionInputError("executable asset inputs are forbidden")
        expected = _validated_sha256(expected_sha256, label="expected source hash") if expected_sha256 is not None else None
        _workspace, vault = _workspace_and_vault(root)
        relative = f"80_Assets/{target_directory}/{target_name}"
        target = _safe_target_file(vault, relative)
        source_hash, source_size = _hash_regular_file(source, label="asset source")
        if expected is not None and source_hash != expected:
            return {
                "status": "CONFLICT",
                "operation": operation,
                "mode": "dry-run" if dry_run else "apply",
                "source_sha256": source_hash,
                "expected_sha256": expected,
                "errors": [_issue("ASSET_SOURCE_HASH_MISMATCH", "/source", "asset source hash does not match the guard")],
                "created": [],
            }, EXIT_CONFLICT
        report: dict[str, Any] = {
            "status": "PASS",
            "operation": operation,
            "mode": "dry-run" if dry_run else "apply",
            "source_name": source.name,
            "target": relative,
            "asset": f"[[{relative}]]",
            "asset_hash": source_hash,
            "source_sha256": source_hash,
            "size_bytes": source_size,
            "mime_type": resolved_mime,
            "warnings": ["asset exceeds the soft warning size"] if source_size > _SOFT_WARNING_BYTES else [],
            "provenance": {
                "source": "explicit_absolute_regular_file",
                "source_sha256": source_hash,
                "target_path": relative,
            },
            "created": [],
        }
        if dry_run:
            report["would_create"] = [relative]
            return report, EXIT_OK
        try:
            copied_hash, copied_size = _copy_create_only(source, target)
        except FileExistsError:
            return {
                "status": "CONFLICT",
                "operation": operation,
                "mode": "apply",
                "target": relative,
                "path_status": "EXISTING",
                "created": [],
            }, EXIT_CONFLICT
        if copied_hash != source_hash or copied_size != source_size:
            raise TransactionInputError("published asset bytes differ from the source snapshot")
        report["created"] = [relative]
        report["published_sha256"] = copied_hash
        return report, EXIT_OK
    except FileExistsError:
        return {
            "status": "CONFLICT",
            "operation": operation,
            "mode": "dry-run" if dry_run else "apply",
            "path_status": "EXISTING",
            "created": [],
        }, EXIT_CONFLICT
    except (OSError, TypeError, ValueError, TransactionInputError, UnsafePathError) as error:
        return _fail(operation, "ASSET_INPUT_INVALID", str(error))


def _parse_timestamp(value: str | datetime | None, *, fallback: datetime) -> datetime:
    parsed = fallback if value is None else (value if isinstance(value, datetime) else datetime.fromisoformat(value))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise TransactionInputError("modified-at must include a timezone offset")
    return parsed.replace(microsecond=0)


def _target_types(vault: Path, engine: NoteEngine) -> dict[str, str]:
    result: dict[str, str] = {}
    for path in sorted(vault.rglob("*.md"), key=lambda item: item.as_posix()):
        if path.is_symlink() or not path.is_file():
            continue
        relative = path.relative_to(vault).as_posix()
        try:
            note_type = engine.note_type_for_path(relative)
            document = parse_frontmatter(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, FrontmatterError, UnsafePathError):
            continue
        if note_type is None:
            continue
        keys = {relative.removesuffix(".md"), Path(relative).stem, str(document.properties.get("title", ""))}
        for key in keys - {""}:
            previous = result.get(key)
            if previous is None or previous == note_type:
                result[key] = note_type
    return result


def _related_link(vault: Path, engine: NoteEngine, raw: str) -> tuple[str, str]:
    if not isinstance(raw, str) or not raw.strip():
        raise TransactionInputError("related target must be non-empty text")
    value = raw.strip()
    if value.startswith("[[") and value.endswith("]]"):
        target = value[2:-2].split("#", 1)[0].split("^", 1)[0]
        if target not in _target_types(vault, engine):
            raise TransactionInputError("related wikilink target is not an existing note")
        return value, target
    relative = normalize_vault_relative_path(value)
    path = _safe_vault_path(vault, relative)
    if path.is_symlink() or not path.is_file():
        raise TransactionInputError("related target must be an existing regular Markdown note")
    document = parse_frontmatter(path.read_text(encoding="utf-8"))
    title = document.properties.get("title")
    if not isinstance(title, str) or not title:
        raise TransactionInputError("related target note title is invalid")
    return f"[[{title}]]", title


def _capture_source(relative: str) -> None:
    parts = normalize_vault_relative_path(relative).split("/")
    if len(parts) < 4 or parts[0:2] != ["00_Inbox", "Captures"] or not relative.endswith(".md"):
        raise TransactionInputError("capture path must be a Markdown file under 00_Inbox/Captures")


CAPTURE_FINALIZE_STEPS = (
    "verify_source_hash_and_capture_schema",
    "validate_outcome_triaged_or_discarded",
    "resolve_optional_related_target",
    "journal_status_and_related_update_intent",
    "publish_frontmatter_update",
    "link_aware_move_to_90_Archive/Captures/YYYY",
    "verify_destination_schema_and_links",
    "append_completion_receipt",
)


def finalize_capture(
    root: str | Path,
    *,
    capture_path: str,
    expected_sha256: str,
    outcome: str,
    related: str | None = None,
    modified_at: str | datetime | None = None,
    dry_run: bool = False,
) -> tuple[dict[str, Any], int]:
    """Finalize one capture and move it to the year-partitioned archive."""

    operation = "capture finalize"
    try:
        _capture_source(capture_path)
        if outcome not in {"triaged", "discarded"}:
            raise TransactionInputError("outcome must be triaged or discarded")
        expected = _validated_sha256(expected_sha256, label="expected capture hash")
        workspace, vault = _workspace_and_vault(root)
        source_relative = normalize_vault_relative_path(capture_path)
        source = _safe_vault_path(vault, source_relative)
        if source.is_symlink() or not source.is_file():
            raise TransactionInputError("capture source must be an existing regular non-symlink file")
        source_hash, _ = _hash_regular_file(source, label="capture source")
        if source_hash != expected:
            return {
                "status": "CONFLICT",
                "operation": operation,
                "mode": "dry-run" if dry_run else "apply",
                "source": source_relative,
                "expected_sha256": expected,
                "observed_sha256": source_hash,
                "errors": [_issue("CAPTURE_SOURCE_HASH_MISMATCH", "/source", "capture source hash does not match the guard")],
                "created": [],
            }, EXIT_CONFLICT
        raw_markdown = source.read_text(encoding="utf-8")
        parse_frontmatter(raw_markdown)
        engine = NoteEngine.from_root(workspace)
        resolver = _target_types(vault, engine)
        original = engine.validate_text(source_relative, raw_markdown, target_types=resolver)
        if not original.passed or original.frontmatter is None or original.body is None:
            return {
                "status": "FAIL",
                "operation": operation,
                "mode": "dry-run" if dry_run else "apply",
                "source": source_relative,
                "errors": [issue.as_dict() for issue in original.errors],
                "created": [],
            }, EXIT_VALIDATION_FAILED
        if original.frontmatter.get("status") != "unprocessed":
            raise TransactionInputError("capture must be unprocessed before finalization")
        modified = _parse_timestamp(
            modified_at,
            fallback=datetime.fromisoformat(str(original.frontmatter["created"])),
        )
        properties = dict(original.frontmatter)
        properties["status"] = outcome
        properties["modified"] = modified.isoformat(timespec="seconds")
        related_target: str | None = None
        if related is not None:
            related_link, related_target = _related_link(vault, engine, related)
            existing = properties.get("related", [])
            if not isinstance(existing, list):
                raise TransactionInputError("capture related property must be a flat list")
            properties["related"] = [*existing, related_link] if related_link not in existing else existing
        updated_markdown = render_frontmatter(properties, original.body)
        created = datetime.fromisoformat(str(original.frontmatter["created"]))
        destination_relative = f"90_Archive/Captures/{created:%Y}/{Path(source_relative).name}"
        destination = _safe_vault_path(vault, destination_relative)
        if destination.is_symlink() or destination.exists():
            return {
                "status": "CONFLICT",
                "operation": operation,
                "mode": "dry-run" if dry_run else "apply",
                "source": source_relative,
                "destination": destination_relative,
                "path_status": "EXISTING" if destination.exists() else "SYMLINK",
                "created": [],
            }, EXIT_CONFLICT
        destination_types = _target_types(vault, engine)
        destination_validation = engine.validate_text(
            destination_relative,
            updated_markdown,
            target_types=destination_types,
        )
        if not destination_validation.passed:
            return {
                "status": "FAIL",
                "operation": operation,
                "mode": "dry-run" if dry_run else "apply",
                "source": source_relative,
                "destination": destination_relative,
                "errors": [issue.as_dict() for issue in destination_validation.errors],
                "created": [],
            }, EXIT_VALIDATION_FAILED
        report: dict[str, Any] = {
            "status": "PASS",
            "operation": operation,
            "mode": "dry-run" if dry_run else "apply",
            "source": source_relative,
            "destination": destination_relative,
            "outcome": outcome,
            "note_id": original.frontmatter.get("id"),
            "source_sha256": source_hash,
            "related": related_target,
            "transaction": list(CAPTURE_FINALIZE_STEPS),
            "created": [],
        }
        if dry_run:
            report["would_move"] = [source_relative, destination_relative]
            return report, EXIT_OK
        # Recheck the guarded source immediately before publishing the new
        # bytes.  A later recovery slice will own fsynced journals.
        current_hash, _ = _hash_regular_file(source, label="capture source")
        if current_hash != expected:
            return {
                "status": "CONFLICT",
                "operation": operation,
                "mode": "apply",
                "source": source_relative,
                "expected_sha256": expected,
                "observed_sha256": current_hash,
                "errors": [_issue("CAPTURE_SOURCE_HASH_MISMATCH", "/source", "capture source changed before publish")],
                "created": [],
            }, EXIT_CONFLICT
        write_note_file(destination, updated_markdown)
        try:
            source.unlink()
        except Exception:
            if destination.is_file() and not destination.is_symlink():
                destination.unlink()
            raise
        report["created"] = [destination_relative]
        report["moved"] = [source_relative, destination_relative]
        report["completion_receipt"] = {
            "note_id": original.frontmatter.get("id"),
            "source_sha256": source_hash,
            "destination": destination_relative,
        }
        return report, EXIT_OK
    except (OSError, TypeError, UnicodeError, ValueError, FrontmatterError, TransactionInputError, UnsafePathError) as error:
        return _fail(operation, "CAPTURE_INPUT_INVALID", str(error))


def _normalize_expected_hashes(expected_hashes: Mapping[str, str] | None) -> dict[str, str]:
    if not isinstance(expected_hashes, Mapping) or not expected_hashes:
        raise TransactionInputError("project archive requires a non-empty exact expected hash map")
    normalized: dict[str, str] = {}
    for raw_path, digest in expected_hashes.items():
        relative = normalize_vault_relative_path(str(raw_path))
        normalized[relative] = _validated_sha256(str(digest), label=f"expected hash for {relative}")
    if len(normalized) != len(expected_hashes):
        raise TransactionInputError("project archive expected hash map contains duplicate normalized paths")
    return normalized


def _project_files(project_dir: Path, vault: Path) -> dict[str, Path]:
    if project_dir.is_symlink() or not project_dir.is_dir():
        raise TransactionInputError("project bundle must be an existing real directory")
    files: dict[str, Path] = {}
    for path in sorted(project_dir.rglob("*"), key=lambda item: item.as_posix()):
        if path.is_symlink():
            raise TransactionInputError("project archive refuses symlinked bundle members")
        if path.is_file():
            files[path.relative_to(vault).as_posix()] = path
    if not files:
        raise TransactionInputError("project bundle must contain at least one file")
    return files


def archive_project(
    root: str | Path,
    *,
    project_path: str,
    expected_hashes: Mapping[str, str],
    archive_year: int | None = None,
    dry_run: bool = False,
) -> tuple[dict[str, Any], int]:
    """Move one complete project bundle behind an exact digest guard."""

    operation = "project archive"
    try:
        workspace, vault = _workspace_and_vault(root)
        source_relative = normalize_vault_relative_path(project_path)
        if not source_relative.startswith("20_Projects/") or not source_relative.endswith(".md"):
            raise TransactionInputError("project path must be a canonical 20_Projects root note")
        parts = source_relative.split("/")
        if len(parts) != 3 or parts[2] != f"{parts[1]}.md":
            raise TransactionInputError("project path must be 20_Projects/<name>/<name>.md")
        project_dir_relative = "/".join(parts[:2])
        project_dir = _safe_vault_path(vault, project_dir_relative)
        files = _project_files(project_dir, vault)
        if source_relative not in files:
            raise TransactionInputError("project root note is missing from its bundle")
        engine = NoteEngine.from_root(workspace)
        resolver = _target_types(vault, engine)
        root_markdown = files[source_relative].read_text(encoding="utf-8")
        root_result = engine.validate_text(source_relative, root_markdown, target_types=resolver)
        if not root_result.passed or root_result.frontmatter is None:
            return {
                "status": "FAIL",
                "operation": operation,
                "mode": "dry-run" if dry_run else "apply",
                "source": project_dir_relative,
                "errors": [issue.as_dict() for issue in root_result.errors],
                "created": [],
            }, EXIT_VALIDATION_FAILED
        if root_result.frontmatter.get("title") != parts[1]:
            raise TransactionInputError("project root title must equal its directory name")
        created = datetime.fromisoformat(str(root_result.frontmatter["created"]))
        year = archive_year if archive_year is not None else created.year
        if not isinstance(year, int) or year < 1970 or year > 9999:
            raise TransactionInputError("archive year must be a four-digit integer")
        expected = _normalize_expected_hashes(expected_hashes)
        observed: dict[str, str] = {}
        for relative, path in files.items():
            digest, _ = _hash_regular_file(path, label=f"project member {relative}")
            observed[relative] = digest
        if set(expected) != set(observed):
            missing = sorted(set(observed) - set(expected))
            extra = sorted(set(expected) - set(observed))
            return {
                "status": "CONFLICT",
                "operation": operation,
                "mode": "dry-run" if dry_run else "apply",
                "source": project_dir_relative,
                "errors": [_issue("PROJECT_HASH_SET_MISMATCH", "/expected_hashes", "expected hashes must cover exactly the project bundle", missing=missing, extra=extra)],
                "created": [],
            }, EXIT_CONFLICT
        mismatches = [relative for relative in sorted(observed) if observed[relative] != expected[relative]]
        if mismatches:
            return {
                "status": "CONFLICT",
                "operation": operation,
                "mode": "dry-run" if dry_run else "apply",
                "source": project_dir_relative,
                "errors": [_issue("PROJECT_SOURCE_HASH_MISMATCH", "/expected_hashes", "project member hash does not match the guard", paths=mismatches)],
                "created": [],
            }, EXIT_CONFLICT
        destination_dir_relative = f"90_Archive/Projects/{year}/{parts[1]}"
        destination_dir = _safe_vault_path(vault, destination_dir_relative)
        if destination_dir.is_symlink() or destination_dir.exists():
            return {
                "status": "CONFLICT",
                "operation": operation,
                "mode": "dry-run" if dry_run else "apply",
                "source": project_dir_relative,
                "destination": destination_dir_relative,
                "path_status": "SYMLINK" if destination_dir.is_symlink() else "EXISTING",
                "created": [],
            }, EXIT_CONFLICT
        # Validate each note at its destination path before the directory move.
        # The bytes and title/ID/link targets remain unchanged; only the
        # project path namespace changes from 20_Projects to 90_Archive.
        for relative, path in files.items():
            if not relative.endswith(".md"):
                continue
            destination_relative = f"{destination_dir_relative}/{Path(relative).relative_to(project_dir_relative).as_posix()}"
            markdown = path.read_text(encoding="utf-8")
            result = engine.validate_text(destination_relative, markdown, target_types=resolver)
            if not result.passed:
                return {
                    "status": "FAIL",
                    "operation": operation,
                    "mode": "dry-run" if dry_run else "apply",
                    "source": project_dir_relative,
                    "destination": destination_dir_relative,
                    "errors": [issue.as_dict() for issue in result.errors],
                    "created": [],
                }, EXIT_VALIDATION_FAILED
        report: dict[str, Any] = {
            "status": "PASS",
            "operation": operation,
            "mode": "dry-run" if dry_run else "apply",
            "source": project_dir_relative,
            "destination": destination_dir_relative,
            "project_id": root_result.frontmatter.get("id"),
            "files": sorted(files),
            "identity_preserved": True,
            "links_preserved": True,
            "source_hashes": observed,
            "created": [],
        }
        if dry_run:
            report["would_move"] = [project_dir_relative, destination_dir_relative]
            return report, EXIT_OK
        destination_dir.parent.mkdir(parents=True, exist_ok=True)
        os.rename(project_dir, destination_dir)
        try:
            for relative, expected_hash in expected.items():
                destination_relative = f"{destination_dir_relative}/{Path(relative).relative_to(project_dir_relative).as_posix()}"
                destination_path = _safe_vault_path(vault, destination_relative)
                digest, _ = _hash_regular_file(destination_path, label=f"archived member {destination_relative}")
                if digest != expected_hash:
                    raise TransactionInputError("archived member hash differs from the guarded source")
        except Exception:
            if destination_dir.exists() and not project_dir.exists():
                os.rename(destination_dir, project_dir)
            raise
        report["created"] = [destination_dir_relative]
        report["moved"] = [project_dir_relative, destination_dir_relative]
        return report, EXIT_OK
    except (OSError, TypeError, UnicodeError, ValueError, FrontmatterError, TransactionInputError, UnsafePathError) as error:
        return _fail(operation, "PROJECT_INPUT_INVALID", str(error))


# Command-shaped aliases keep the Python API aligned with the Blueprint names.
asset_import = import_asset
capture_finalize = finalize_capture
project_archive = archive_project

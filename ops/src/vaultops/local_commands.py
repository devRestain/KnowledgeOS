"""C13 create-only commands and guarded Markdown formatting."""

from __future__ import annotations

import hashlib
import re
import sys
import unicodedata
import uuid
from datetime import date, datetime
from pathlib import Path
from typing import Any, BinaryIO
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

from .note_engine import (
    FrontmatterError,
    NoteEngine,
    UnsafePathError,
    normalize_vault_relative_path,
    render_frontmatter,
    resolve_vault_relative_path,
    write_note_file,
)
from .template_engine import TemplateRenderError, render_note_template
from .workflows import validate_title

EXIT_OK = 0
EXIT_CONFLICT = 30
EXIT_INPUT_INVALID = 10
EXIT_VALIDATION_FAILED = 13
CONTENT_MAX_BYTES = 65536
CANONICAL_TIMEZONE = ZoneInfo("Asia/Seoul")

_CONTROL_CHARS = {character for character in range(32) if character not in {9, 10}}
_NOTE_CREATE_TYPES = frozenset(
    {
        "capture",
        "idea",
        "question",
        "artifact",
        "area",
        "knowledge",
        "source",
        "person",
        "moc",
        "meeting",
        "project_note",
    }
)
_FORMAT_EXCLUDED_TYPES = frozenset({"home", "system"})
PERIOD_NOTE_GUI_MESSAGE = (
    "daily, weekly, and monthly notes must be created in Obsidian through the approved GUI workflow; "
    "use vaultctl note validate to validate an existing period note"
)


class LocalCommandInputError(ValueError):
    """Raised for input or path data that must not reach a writer."""


def _issue(code: str, locator: str, message: str) -> dict[str, str]:
    return {"code": code, "locator": locator, "message": message}


def _fail(
    operation: str,
    code: str,
    message: str,
    *,
    exit_code: int = EXIT_INPUT_INVALID,
) -> tuple[dict[str, Any], int]:
    return {
        "status": "FAIL",
        "operation": operation,
        "errors": [_issue(code, "/", message)],
        "created": [],
    }, exit_code


def _workspace(root: str | Path) -> Path:
    candidate = Path(root).expanduser()
    if candidate.is_symlink() or not candidate.is_dir():
        raise LocalCommandInputError("control root must be an existing non-symlink directory")
    return candidate.resolve()


def _vault_root(root: str | Path) -> tuple[Path, Path]:
    workspace = _workspace(root)
    vault = workspace / "KnowledgeHub"
    if vault.is_symlink() or not vault.is_dir():
        raise LocalCommandInputError("Vault root is missing or is a symlink")
    return workspace, vault.resolve()


def _normalize_text(raw: bytes | str, *, label: str, max_bytes: int = CONTENT_MAX_BYTES) -> str:
    if isinstance(raw, str):
        raw = raw.encode("utf-8")
    if len(raw) > max_bytes:
        raise LocalCommandInputError(f"{label} exceeds the {max_bytes}-byte UTF-8 limit")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as error:
        raise LocalCommandInputError(f"{label} is not valid UTF-8") from error
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    if "\x00" in text:
        raise LocalCommandInputError(f"{label} must not contain NUL")
    if any(ord(character) in _CONTROL_CHARS or ord(character) == 127 for character in text):
        raise LocalCommandInputError(f"{label} contains a forbidden control character")
    if len(text.encode("utf-8")) > max_bytes:
        raise LocalCommandInputError(f"{label} exceeds the {max_bytes}-byte UTF-8 limit")
    return text


def _read_stream(stream: BinaryIO, *, label: str, max_bytes: int = CONTENT_MAX_BYTES) -> str:
    return _normalize_text(stream.read(max_bytes + 1), label=label, max_bytes=max_bytes)


def read_content(
    *,
    stdin: bool,
    file_path: str | Path | None,
    label: str,
    required: bool = True,
    max_bytes: int = CONTENT_MAX_BYTES,
) -> str | None:
    """Read bounded UTF-8 content from exactly one transport."""

    if stdin and file_path is not None:
        raise LocalCommandInputError(f"{label} must use stdin or file, not both")
    if not stdin and file_path is None:
        if required:
            raise LocalCommandInputError(f"{label} requires stdin or a validated file")
        return None
    if stdin:
        stream = getattr(sys.stdin, "buffer", sys.stdin)
        return _read_stream(stream, label=label, max_bytes=max_bytes)
    path = Path(file_path).expanduser()
    if path.is_symlink() or not path.is_file():
        raise LocalCommandInputError(f"{label} file must be an existing regular non-symlink file")
    try:
        if path.stat().st_size > max_bytes:
            raise LocalCommandInputError(f"{label} exceeds the {max_bytes}-byte UTF-8 limit")
        raw = path.read_bytes()
    except OSError as error:
        raise LocalCommandInputError(f"{label} file could not be read") from error
    return _normalize_text(raw, label=label, max_bytes=max_bytes)


def _parse_created_at(value: str | datetime | None) -> datetime:
    if value is None:
        return datetime.now(CANONICAL_TIMEZONE).replace(microsecond=0)
    parsed = value if isinstance(value, datetime) else datetime.fromisoformat(value)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise LocalCommandInputError("created-at must include a timezone offset")
    return parsed.replace(microsecond=0)


def _safe_title(value: str) -> str:
    title = validate_title(value)
    if unicodedata.normalize("NFC", title) != title:
        raise LocalCommandInputError("title must be NFC-normalized")
    return title


def _derived_title(text: str, *, fallback: str) -> str:
    for raw_line in text.splitlines():
        candidate = re.sub(r"^\s*(?:#+\s*|[-*+]\s+|\d+[.)]\s+)", "", raw_line).strip()
        if candidate:
            candidate = candidate.replace("/", " - ").replace("\\", " - ")
            candidate = re.sub(r"[\x00-\x1f\x7f]", " ", candidate).strip()
            candidate = candidate[:120].rstrip()
            try:
                return _safe_title(candidate)
            except (LocalCommandInputError, ValueError):
                break
    return _safe_title(fallback)


def _capture_body(title: str, content: str, *, url: str | None = None, comment: str | None = None) -> str:
    parts = [f"# {title}", "", "## 원문", "", content.rstrip("\n")]
    if url is not None:
        parts.extend(["", "## 출처 URL", "", url])
    if comment:
        parts.extend(["", "## 메모", "", comment.rstrip("\n")])
    return "\n".join(parts) + "\n"


def _capture_target(created: datetime, title: str) -> str:
    local = created.astimezone(CANONICAL_TIMEZONE)
    return f"00_Inbox/Captures/{local:%Y}/{local:%m}/{local:%Y%m%d-%H%M%S} {title}.md"


def _create_rendered_note(
    workspace: Path,
    vault: Path,
    *,
    relative: str,
    note_type: str,
    context: dict[str, object],
    dry_run: bool,
    operation: str,
    target_types: dict[str, str] | None = None,
) -> tuple[dict[str, Any], int]:
    try:
        target = resolve_vault_relative_path(vault, relative)
        if target.is_symlink() or target.exists():
            report = {
                "status": "CONFLICT",
                "operation": operation,
                "mode": "dry-run" if dry_run else "apply",
                "path": relative,
                "path_status": "SYMLINK" if target.is_symlink() else "EXISTING",
                "created": [],
            }
            return report, EXIT_CONFLICT
        template = context.pop("template")
        rendered = render_note_template(str(template), context)
        if len(rendered.markdown.encode("utf-8")) > CONTENT_MAX_BYTES:
            raise LocalCommandInputError(
                f"rendered note exceeds the {CONTENT_MAX_BYTES}-byte UTF-8 limit"
            )
        validation = NoteEngine.from_root(workspace).validate_text(
            relative, rendered.markdown, target_types=target_types
        )
        if not validation.passed:
            return {
                "status": "FAIL",
                "operation": operation,
                "mode": "dry-run" if dry_run else "apply",
                "path": relative,
                "errors": [issue.as_dict() for issue in validation.errors],
                "created": [],
            }, EXIT_VALIDATION_FAILED
        if dry_run:
            return {
                "status": "PASS",
                "operation": operation,
                "mode": "dry-run",
                "path": relative,
                "would_create": [relative],
                "created": [],
                "note": {"type": note_type, "id": rendered.properties["id"]},
            }, EXIT_OK
        write_note_file(target, rendered.markdown)
        return {
            "status": "PASS",
            "operation": operation,
            "mode": "apply",
            "path": relative,
            "created": [relative],
            "note": {"type": note_type, "id": rendered.properties["id"]},
        }, EXIT_OK
    except FileExistsError:
        return {
            "status": "CONFLICT",
            "operation": operation,
            "mode": "dry-run" if dry_run else "apply",
            "path": relative,
            "path_status": "EXISTING",
            "created": [],
        }, EXIT_CONFLICT
    except (OSError, TypeError, ValueError, UnsafePathError, TemplateRenderError) as error:
        return _fail(operation, f"{note_type.upper()}_INPUT_INVALID", str(error))


def capture_text(
    root: str | Path,
    *,
    stdin: bool,
    device: str,
    title: str | None = None,
    created_at: str | datetime | None = None,
    dry_run: bool = False,
) -> tuple[dict[str, Any], int]:
    operation = "capture text"
    try:
        content = read_content(stdin=stdin, file_path=None, label="capture body")
        if not content:
            raise LocalCommandInputError("capture body must not be empty")
        if device not in {"mac", "iphone", "ipad"}:
            raise LocalCommandInputError("device must be mac, iphone, or ipad")
        created = _parse_created_at(created_at)
        safe_title = _safe_title(title) if title is not None else _derived_title(content, fallback="Capture")
        workspace, vault = _vault_root(root)
        relative = _capture_target(created, safe_title)
        target_title = Path(relative).stem
        context: dict[str, object] = {
            "template": "T00_Capture.md",
            "title": target_title,
            "id": str(uuid.uuid4()),
            "created": created,
            "modified": created,
            "captured_from": "mac_cli",
            "capture_device": device,
            "capture_kind": "thought",
            "capture_label": safe_title,
            "needs_desktop_review": device != "mac",
            "body": _capture_body(safe_title, content),
        }
        return _create_rendered_note(
            workspace,
            vault,
            relative=relative,
            note_type="capture",
            context=context,
            dry_run=dry_run,
            operation=operation,
        )
    except (OSError, TypeError, ValueError, LocalCommandInputError) as error:
        return _fail(operation, "CAPTURE_INPUT_INVALID", str(error))


def _validate_url(value: str) -> str:
    url = value.strip()
    if not url or "\n" in url or "\t" in url:
        raise LocalCommandInputError("URL must be one non-empty line")
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.username or parsed.password:
        raise LocalCommandInputError("URL must be an http(s) URL without embedded credentials")
    return url


def capture_url(
    root: str | Path,
    *,
    url_stdin: bool,
    url_file: str | Path | None,
    comment_stdin: bool = False,
    comment_file: str | Path | None = None,
    title: str | None = None,
    created_at: str | datetime | None = None,
    dry_run: bool = False,
) -> tuple[dict[str, Any], int]:
    operation = "capture url"
    try:
        raw_url = read_content(stdin=url_stdin, file_path=url_file, label="source URL")
        if raw_url is None:
            raise LocalCommandInputError("source URL requires stdin or a validated file")
        url = _validate_url(raw_url)
        comment = read_content(
            stdin=comment_stdin,
            file_path=comment_file,
            label="URL comment",
            required=False,
        )
        created = _parse_created_at(created_at)
        parsed = urlparse(url)
        fallback = (parsed.hostname or "Source").removeprefix("www.")
        safe_title = _safe_title(title) if title is not None else _derived_title(fallback, fallback="Source")
        workspace, vault = _vault_root(root)
        relative = _capture_target(created, safe_title)
        target_title = Path(relative).stem
        context: dict[str, object] = {
            "template": "T00_Capture.md",
            "title": target_title,
            "id": str(uuid.uuid4()),
            "created": created,
            "modified": created,
            "captured_from": "mac_cli",
            "capture_device": "mac",
            "capture_kind": "url",
            "capture_label": safe_title,
            "body": _capture_body(safe_title, url, url=url, comment=comment),
        }
        return _create_rendered_note(
            workspace,
            vault,
            relative=relative,
            note_type="capture",
            context=context,
            dry_run=dry_run,
            operation=operation,
        )
    except (OSError, TypeError, ValueError, LocalCommandInputError) as error:
        return _fail(operation, "CAPTURE_URL_INVALID", str(error))


def _project_context(vault: Path, engine: NoteEngine, project: str) -> tuple[str, Path]:
    relative = normalize_vault_relative_path(project)
    if not relative.endswith(".md"):
        raise LocalCommandInputError("--project must name a project Markdown file")
    project_path = resolve_vault_relative_path(vault, relative)
    if project_path.is_symlink() or not project_path.is_file():
        raise LocalCommandInputError("--project must name an existing regular project note")
    if engine.note_type_for_path(relative) != "project":
        raise LocalCommandInputError("--project must point to a canonical project root note")
    project_note = engine.validate_text(relative, project_path.read_text(encoding="utf-8"))
    if not project_note.passed or not project_note.frontmatter:
        raise LocalCommandInputError("--project must pass the project note contract")
    project_title = project_note.frontmatter.get("title")
    if not isinstance(project_title, str):
        raise LocalCommandInputError("project note title is invalid")
    return project_title, project_path


def create_note(
    root: str | Path,
    *,
    note_type: str,
    title: str,
    body_stdin: bool = False,
    body_file: str | Path | None = None,
    project: str | None = None,
    selected_date: str | date | None = None,
    identifier: str | None = None,
    created_at: str | datetime | None = None,
    status: str | None = None,
    source_kind: str | None = None,
    dry_run: bool = False,
) -> tuple[dict[str, Any], int]:
    operation = "note create"
    try:
        if note_type in {"daily", "weekly", "monthly"}:
            raise LocalCommandInputError(PERIOD_NOTE_GUI_MESSAGE)
        if note_type not in _NOTE_CREATE_TYPES:
            raise LocalCommandInputError(
                f"note type {note_type!r} is not createable in C13; use the approved note workflow"
            )
        safe_title = _safe_title(title)
        body = read_content(stdin=body_stdin, file_path=body_file, label="note body", required=False)
        workspace, vault = _vault_root(root)
        engine = NoteEngine.from_root(workspace)
        created = _parse_created_at(created_at)
        context: dict[str, object] = {
            "template": engine.blueprint["note_types"][note_type]["template"],
            "title": safe_title,
            "id": identifier or str(uuid.uuid4()),
            "created": created,
            "modified": created,
        }
        if status is not None:
            context["status"] = status
        if body is not None:
            context["body"] = body

        target_types: dict[str, str] | None = None

        if note_type == "capture":
            if body is None:
                raise LocalCommandInputError("capture note requires --body-stdin or --body-file")
            context.update(
                {
                    "captured_from": "mac_cli",
                    "capture_device": "mac",
                    "capture_kind": "thought",
                    "capture_label": safe_title,
                }
            )
            relative = _capture_target(created, safe_title)
            context["title"] = Path(relative).stem
            context["body"] = _capture_body(safe_title, body)
        elif note_type in {"project_note", "artifact"}:
            if project is None:
                raise LocalCommandInputError(f"{note_type} requires --project")
            project_title, project_path = _project_context(vault, engine, project)
            child_dir = project_path.parent / ("Working" if note_type == "project_note" else "Artifacts")
            if child_dir.is_symlink() or not child_dir.is_dir():
                raise LocalCommandInputError("project bundle is missing its required sibling directory")
            context["project_link"] = f"[[{project_title}]]"
            project_relative = project_path.parent.relative_to(vault).as_posix()
            relative = f"{project_relative}/{child_dir.name}/{safe_title}.md"
            target_types = {project_title: "project"}
        else:
            if project is not None:
                raise LocalCommandInputError("--project is only valid for project-local note types")
            if note_type == "meeting":
                selected = (
                    created.astimezone(CANONICAL_TIMEZONE).date()
                    if selected_date is None
                    else (selected_date if isinstance(selected_date, date) else date.fromisoformat(selected_date))
                )
                context["title"] = f"{selected:%Y-%m-%d} — {safe_title}"
                context["meeting_at"] = created
                relative = f"60_Meetings/{selected:%Y}/{selected:%Y-%m-%d} — {safe_title}.md"
            else:
                relative = _ordinary_note_target(note_type, safe_title)
            if note_type == "source" and source_kind is not None:
                context["source_kind"] = source_kind

        return _create_rendered_note(
            workspace,
            vault,
            relative=relative,
            note_type=note_type,
            context=context,
            dry_run=dry_run,
            operation=operation,
            target_types=target_types,
        )
    except (OSError, TypeError, ValueError, LocalCommandInputError, UnsafePathError, TemplateRenderError) as error:
        return _fail(operation, "NOTE_INPUT_INVALID", str(error))


def _ordinary_note_target(note_type: str, title: str) -> str:
    paths = {
        "idea": f"40_Knowledge/Ideas/{title}.md",
        "question": f"40_Knowledge/Questions/{title}.md",
        "area": f"30_Areas/{title}.md",
        "knowledge": f"40_Knowledge/Notes/{title}.md",
        "source": f"40_Knowledge/Sources/{title}.md",
        "person": f"40_Knowledge/People/{title}.md",
        "moc": f"50_Maps/{title} MOC.md",
    }
    try:
        return paths[note_type]
    except KeyError as error:
        raise LocalCommandInputError(f"no canonical target for note type {note_type}") from error


def _format_candidates(vault: Path, engine: NoteEngine, relative: str | None) -> list[str]:
    if relative is not None:
        normalized = normalize_vault_relative_path(relative)
        if not normalized.endswith(".md"):
            raise LocalCommandInputError("fmt path must be a Markdown file")
        target = resolve_vault_relative_path(vault, normalized)
        if target.is_symlink() or not target.is_file():
            raise LocalCommandInputError("fmt path must be an existing regular non-symlink file")
        note_type = engine.note_type_for_path(normalized)
        if note_type is None or note_type in _FORMAT_EXCLUDED_TYPES:
            raise LocalCommandInputError("fmt path must match a canonical user note type")
        return [normalized]
    candidates: list[str] = []
    for path in sorted(vault.rglob("*.md"), key=lambda item: item.as_posix()):
        relative_path = path.relative_to(vault)
        if any(part.startswith(".") for part in relative_path.parts):
            continue
        candidate = relative_path.as_posix()
        note_type = engine.note_type_for_path(candidate)
        if note_type is not None and note_type not in _FORMAT_EXCLUDED_TYPES:
            candidates.append(candidate)
    return candidates


def format_notes(
    root: str | Path,
    *,
    check: bool,
    relative: str | None = None,
) -> tuple[dict[str, Any], int]:
    operation = "fmt"
    try:
        if not check and relative is None:
            raise LocalCommandInputError("fmt write mode requires an explicit --path")
        workspace, vault = _vault_root(root)
        engine = NoteEngine.from_root(workspace)
        candidates = _format_candidates(vault, engine, relative)
        changes: list[str] = []
        errors: list[dict[str, str]] = []
        formatted: dict[str, bytes] = {}
        original: dict[str, bytes] = {}
        for candidate in candidates:
            path = resolve_vault_relative_path(vault, candidate)
            try:
                raw = path.read_bytes()
                markdown = raw.decode("utf-8")
                result = engine.validate_text(candidate, markdown)
            except (OSError, UnicodeError) as error:
                errors.append(_issue("FMT_INPUT_INVALID", f"/{candidate}", str(error)))
                continue
            if not result.passed or result.frontmatter is None or result.body is None:
                errors.extend(
                    _issue(issue.code, f"/{candidate}{issue.locator}", issue.message)
                    for issue in result.errors
                )
                continue
            canonical = render_frontmatter(result.frontmatter, result.body).encode("utf-8")
            original[candidate] = raw
            formatted[candidate] = canonical
            if raw != canonical:
                changes.append(candidate)
        if errors:
            return {
                "status": "FAIL",
                "operation": operation,
                "mode": "check" if check else "apply",
                "errors": errors,
                "changed": changes,
            }, EXIT_VALIDATION_FAILED
        if check:
            return {
                "status": "PASS" if not changes else "NEEDS_FORMAT",
                "operation": operation,
                "mode": "check",
                "checked": candidates,
                "changed": changes,
                "filesystem_changed": False,
            }, EXIT_OK if not changes else EXIT_VALIDATION_FAILED
        candidate = candidates[0]
        if candidate in changes:
            expected = hashlib.sha256(original[candidate]).hexdigest()
            write_note_file(
                resolve_vault_relative_path(vault, candidate),
                formatted[candidate].decode("utf-8"),
                overwrite=True,
                expected_sha256=expected,
            )
        return {
            "status": "PASS",
            "operation": operation,
            "mode": "apply",
            "path": candidate,
            "changed": changes,
            "filesystem_changed": bool(changes),
        }, EXIT_OK
    except (OSError, TypeError, ValueError, LocalCommandInputError, UnsafePathError, FrontmatterError) as error:
        return _fail(operation, "FMT_INPUT_INVALID", str(error))

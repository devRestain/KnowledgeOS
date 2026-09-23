"""C07 create-only project workflow and deterministic fixtures."""

from __future__ import annotations

import re
import shutil
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from .note_engine import (
    NoteEngine,
    UnsafePathError,
    normalize_vault_relative_path,
    validate_path_collisions,
    write_note_file,
)
from .template_engine import TemplateRenderError, render_note_template

RESERVED_PATH_COMPONENTS = {"YYYY", "MM", "GGGG", "WWW", "PROJECT_NAME", "JOB_ID"}
PROJECT_TITLE_MAX_LENGTH = 120
CANONICAL_TIMEZONE = ZoneInfo("Asia/Seoul")


def validate_title(title: str) -> str:
    if not isinstance(title, str) or not title:
        raise ValueError("title must be non-empty text")
    if len(title) > PROJECT_TITLE_MAX_LENGTH:
        raise ValueError(f"title must be at most {PROJECT_TITLE_MAX_LENGTH} characters")
    if any(ord(character) < 32 or ord(character) == 127 for character in title):
        raise ValueError("title contains a control character")
    if "/" in title or "\\" in title or "\x00" in title:
        raise ValueError("title must be a single path component")
    if title in {".", ".."} or title.startswith(".") or title in RESERVED_PATH_COMPONENTS:
        raise ValueError("title is a protected path component")
    return title


def _workspace(root: str | Path) -> Path:
    return Path(root).resolve()


def _parse_datetime(value: str | datetime | None) -> datetime:
    if value is None:
        return datetime.now(CANONICAL_TIMEZONE).replace(microsecond=0)
    if isinstance(value, datetime):
        result = value
    else:
        result = datetime.fromisoformat(value)
    if result.tzinfo is None or result.utcoffset() is None:
        raise ValueError("created-at must include a timezone offset")
    return result.replace(microsecond=0)


def _preflight_existing(path: Path) -> str:
    if path.is_symlink():
        return "CONFLICT"
    if path.exists():
        return "EXISTING"
    return "CREATE"


def _project_targets(title: str) -> tuple[str, str, str]:
    return (
        f"20_Projects/{title}/{title}.md",
        f"20_Projects/{title}/Working",
        f"20_Projects/{title}/Artifacts",
    )


def _safe_target(vault_root: Path, relative: str) -> Path:
    normalized = normalize_vault_relative_path(relative)
    candidate = vault_root.joinpath(*normalized.split("/"))
    current = vault_root
    for part in normalized.split("/"):
        current = current / part
        if current.is_symlink():
            raise UnsafePathError(f"path crosses a symlink: {relative}")
    return candidate


def create_project_bundle(
    root: str | Path,
    *,
    title: str,
    dry_run: bool = False,
    identifier: str | None = None,
    created_at: str | datetime | None = None,
    status: str = "planned",
    outcome: str | None = None,
    priority: str = "medium",
    focus_rank: int | None = None,
    next_action: str | None = None,
    target_date: str | None = None,
) -> dict[str, Any]:
    """Create a Project note and its two sibling directories atomically."""

    try:
        title = validate_title(title)
        workspace = _workspace(root)
        vault_root = workspace / "KnowledgeHub"
        if vault_root.is_symlink() or not vault_root.is_dir():
            raise ValueError("Vault root is missing or is a symlink")
        targets = _project_targets(title)
        path_issues = validate_path_collisions([targets[0]])
        if path_issues:
            raise ValueError(path_issues[0].message)
        projects_root = _safe_target(vault_root, "20_Projects")
        project_dir = projects_root / title
        project_dir_relative = f"20_Projects/{title}"
        raw_target_paths = [
            (targets[0], project_dir / f"{title}.md"),
            (targets[1], project_dir / "Working"),
            (targets[2], project_dir / "Artifacts"),
        ]
        if project_dir.is_symlink() or project_dir.exists():
            return {
                "status": "CONFLICT",
                "operation": "project create",
                "mode": "dry-run" if dry_run else "apply",
                "title": title,
                "paths": [
                    {"path": relative, "status": _preflight_existing(path)}
                    for relative, path in raw_target_paths
                ],
                "conflicts": [project_dir_relative],
                "created": [],
            }
        target_paths = [(relative, _safe_target(vault_root, relative)) for relative in targets]
        states = [{"path": relative, "status": _preflight_existing(path)} for relative, path in target_paths]
        conflicts = [item["path"] for item in states if item["status"] != "CREATE"]
        if conflicts:
            return {
                "status": "CONFLICT",
                "operation": "project create",
                "mode": "dry-run" if dry_run else "apply",
                "title": title,
                "paths": states,
                "conflicts": conflicts,
                "created": [],
            }
        created = _parse_datetime(created_at)
        context: dict[str, object] = {
            "title": title,
            "id": identifier or str(uuid.uuid4()),
            "created": created,
            "modified": created,
            "status": status,
            "outcome": outcome or f"{title}의 완료 조건을 정의한다.",
            "priority": priority,
        }
        if focus_rank is not None:
            context["focus_rank"] = focus_rank
        if next_action is not None:
            context["next_action"] = next_action
        if target_date is not None:
            context["target_date"] = target_date
        rendered = render_note_template("T20_Project.md", context)
        engine = NoteEngine.from_root(workspace)
        validation = engine.validate_text(targets[0], rendered.markdown)
        if not validation.passed:
            return {
                "status": "FAIL",
                "operation": "project create",
                "mode": "dry-run" if dry_run else "apply",
                "title": title,
                "paths": states,
                "errors": [issue.as_dict() for issue in validation.errors],
                "created": [],
            }
        if dry_run:
            return {
                "status": "PASS",
                "operation": "project create",
                "mode": "dry-run",
                "title": title,
                "paths": states,
                "would_create": [item["path"] for item in states],
                "created": [],
            }

        # The full target set was preflighted above.  Create the directory
        # only after note validation, then clean the complete owned directory
        # if a filesystem error occurs before the operation returns.
        project_dir.mkdir(parents=False, exist_ok=False)
        try:
            (project_dir / "Working").mkdir()
            (project_dir / "Artifacts").mkdir()
            write_note_file(project_dir / f"{title}.md", rendered.markdown)
        except Exception:
            if project_dir.exists():
                shutil.rmtree(project_dir)
            raise
        return {
            "status": "PASS",
            "operation": "project create",
            "mode": "apply",
            "title": title,
            "paths": [{"path": item["path"], "status": "CREATED"} for item in states],
            "created": [item["path"] for item in states],
            "note": {"path": targets[0], "type": "project", "id": rendered.properties["id"]},
        }
    except (OSError, UnicodeError, TypeError, ValueError, UnsafePathError, TemplateRenderError) as error:
        return {
            "status": "FAIL",
            "operation": "project create",
            "mode": "dry-run" if dry_run else "apply",
            "errors": [{"code": "PROJECT_INPUT_INVALID", "locator": "/", "message": str(error)}],
            "created": [],
        }


def project_local_link_resolves(relative_path: str, project_link: str, project_root_relative: str) -> bool:
    """Return whether a project-local link names exactly its parent project."""

    normalized = normalize_vault_relative_path(relative_path)
    expected = normalize_vault_relative_path(project_root_relative)
    if not re.fullmatch(r"\[\[[^\]\n]+\]\]", project_link):
        return False
    target = project_link[2:-2].split("#", 1)[0].split("^", 1)[0]
    if "/" in target:
        target = target.rsplit("/", 1)[-1]
    expected_parts = expected.split("/")
    if len(expected_parts) != 3 or expected_parts[0] != "20_Projects" or expected_parts[1] != expected_parts[2][:-3]:
        return False
    if target != expected_parts[1]:
        return False
    local_parts = normalized.split("/")
    return len(local_parts) >= 4 and local_parts[0:2] == expected_parts[0:2] and local_parts[2] in {"Working", "Artifacts"}


def render_project_local_sample(workspace: str | Path, *, title: str, note_kind: str = "exploration", artifact_title: str = "Artifact") -> tuple[str, str]:
    """Render schema-valid project-local samples for the C07 fixture gate."""

    safe = validate_title(title)
    project_root = f"20_Projects/{safe}/{safe}.md"
    link = f"[[{safe}]]"
    note = render_note_template("T24_Project_Note.md", {"title": "Working Note", "project_link": link, "note_kind": note_kind, "id": str(uuid.uuid4()), "created": "2026-09-09T09:00:00+09:00"})
    artifact = render_note_template("T23_Artifact.md", {"title": artifact_title, "project_link": link, "id": str(uuid.uuid4()), "created": "2026-09-09T09:00:00+09:00"})
    engine = NoteEngine.from_root(workspace)
    note_path = f"20_Projects/{safe}/Working/Working Note.md"
    artifact_path = f"20_Projects/{safe}/Artifacts/{artifact_title}.md"
    target_types = {safe: "project"}
    for path, rendered in ((note_path, note), (artifact_path, artifact)):
        result = engine.validate_text(path, rendered.markdown, target_types=target_types)
        if not result.passed:
            raise ValueError(str(result.as_dict()))
        if not project_local_link_resolves(path, link, project_root):
            raise ValueError(f"project link does not resolve to parent: {path}")
    return note.markdown, artifact.markdown

"""Portable equivalent of the macOS foundation check for container evidence."""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

from .runtime import RuntimeLayout
from .yaml_safe import load_yaml_file

MANIFEST_SHA256 = "47746572534e8b905aeb9b2b0f5eaf15d1407765f18e879ad108d37c6bb2080a"

REQUIRED_FILES = (
    ".gitignore",
    ".gitattributes",
    "AGENTS.md",
    "README.md",
    "Makefile",
    "OBSIDIAN_VAULT_BLUEPRINT.md",
    "OBSIDIAN_VAULT_WHITEPAPER.md",
    "blueprint/README.md",
    "blueprint/CHECKSUMS.sha256",
    "blueprint/blueprint.yaml",
    "blueprint/blueprint.schema.json",
    "docs/ARCHITECTURE.md",
    "docs/OPERATIONS.md",
    "docs/MOBILE.md",
    "docs/RUNTIME.md",
    "docs/DECISIONS.md",
    "docs/IMPLEMENTATION_STATUS.md",
    "docs/SOURCE_CONTRACT.md",
    "ops/check-foundation.sh",
    "ops/config/generated-artifacts.yaml",
    "KnowledgeHub/.gitignore",
    "KnowledgeHub/.gitattributes",
)

REQUIRED_DIRECTORIES = (
    "blueprint",
    "docs",
    "ops",
    "ops/config",
    "ops/actions",
    "ops/schemas",
    "ops/prompts",
    "ops/policies",
    "ops/expected",
    "ops/src/vaultops",
    "ops/launchd",
    "ops/tests",
    "ops/tests/fixtures",
    "KnowledgeHub",
    "KnowledgeHub/.vault-bridge",
    "KnowledgeHub/.vault-bridge/protocol",
    "KnowledgeHub/.vault-bridge/requests",
    "KnowledgeHub/.vault-bridge/responses",
    "runtime",
    "KnowledgeHub/00_Inbox/Captures",
    "KnowledgeHub/00_Inbox/Imports",
    "KnowledgeHub/01_AI_Review/Pending",
    "KnowledgeHub/01_AI_Review/Resolved",
    "KnowledgeHub/01_AI_Review/Rejected",
    "KnowledgeHub/01_AI_Review/Expired",
    "KnowledgeHub/01_AI_Review/Conflict",
    "KnowledgeHub/10_Journal/Daily",
    "KnowledgeHub/10_Journal/Weekly",
    "KnowledgeHub/10_Journal/Monthly",
    "KnowledgeHub/20_Projects",
    "KnowledgeHub/30_Areas",
    "KnowledgeHub/40_Knowledge/Notes",
    "KnowledgeHub/40_Knowledge/Ideas",
    "KnowledgeHub/40_Knowledge/Questions",
    "KnowledgeHub/40_Knowledge/Sources",
    "KnowledgeHub/40_Knowledge/People",
    "KnowledgeHub/50_Maps",
    "KnowledgeHub/60_Meetings",
    "KnowledgeHub/80_Assets/Inbox",
    "KnowledgeHub/80_Assets/Images",
    "KnowledgeHub/80_Assets/Documents",
    "KnowledgeHub/80_Assets/Audio",
    "KnowledgeHub/90_Archive/Projects",
    "KnowledgeHub/90_Archive/Captures",
    "KnowledgeHub/90_Archive/Other",
    "KnowledgeHub/99_System/Templates",
    "KnowledgeHub/99_System/Bases",
    "KnowledgeHub/99_System/Dashboards",
    "KnowledgeHub/99_System/Schemas",
    "KnowledgeHub/99_System/Scripts/QuickAdd",
    "KnowledgeHub/99_System/CSS",
    "KnowledgeHub/.obsidian-mac",
    "KnowledgeHub/.obsidian-phone",
    "KnowledgeHub/.obsidian-tablet",
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _git_output(root: Path, *args: str) -> tuple[int, str]:
    completed = subprocess.run(
        ["git", "-c", f"safe.directory={root}", "-C", str(root), *args],
        check=False,
        capture_output=True,
        text=True,
    )
    return completed.returncode, completed.stdout.strip()


def check_source_manifest(root: str | Path) -> list[str]:
    """Check the fixed blueprint manifest without inspecting runtime paths."""

    workspace = Path(root).resolve()
    manifest = workspace / "blueprint/CHECKSUMS.sha256"
    if not manifest.is_file():
        return ["missing checksum manifest: blueprint/CHECKSUMS.sha256"]
    problems: list[str] = []
    if _sha256(manifest) != MANIFEST_SHA256:
        problems.append("checksum manifest hash mismatch")
    for line in manifest.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        expected, relative = line.split("  ", 1)
        source = workspace / relative
        if not source.is_file():
            problems.append(f"missing blueprint source: {relative}")
        elif _sha256(source) != expected:
            problems.append(f"blueprint checksum mismatch: {relative}")
    return problems


def check_foundation(root: str | Path) -> list[str]:
    """Return portable foundation violations for a mounted workspace."""

    workspace = Path(root).resolve()
    problems: list[str] = []
    for relative in REQUIRED_FILES:
        path = workspace / relative
        if path.is_symlink():
            problems.append(f"required file is a symlink: {relative}")
        elif not path.is_file():
            problems.append(f"missing required file: {relative}")
    for relative in REQUIRED_DIRECTORIES:
        path = workspace / relative
        if path.is_symlink():
            problems.append(f"required directory is a symlink: {relative}")
        elif not path.is_dir():
            problems.append(f"missing required directory: {relative}")

    problems.extend(RuntimeLayout(workspace / "runtime").check())
    if (workspace / "bridge").exists():
        problems.append("obsolete top-level bridge/ exists")
    for expected in ("/KnowledgeHub/", "/runtime/"):
        if expected not in (workspace / ".gitignore").read_text(encoding="utf-8").splitlines():
            problems.append(f"control .gitignore is missing {expected}")
    if list((workspace / "KnowledgeHub").rglob(".gitkeep")):
        problems.append("Vault filler .gitkeep files found")
    for path in (workspace / "KnowledgeHub").rglob("*"):
        if path.is_symlink():
            problems.append(f"unexpected Vault symlink: {path.relative_to(workspace)}")
    for path in (workspace / "ops").rglob("*"):
        if path.is_symlink():
            problems.append(f"unexpected ops symlink: {path.relative_to(workspace)}")

    problems.extend(check_source_manifest(workspace))

    try:
        blueprint = load_yaml_file(workspace / "blueprint/blueprint.yaml")
        schema = json.loads((workspace / "blueprint/blueprint.schema.json").read_text(encoding="utf-8"))
        if blueprint.get("contract_id") != "knowledgeos-blueprint-v2":
            problems.append("unexpected contract_id")
        if not isinstance(schema, dict):
            problems.append("blueprint schema root must be an object")
        elif len(schema.get("required", [])) != len(blueprint):
            problems.append("blueprint schema required/key count mismatch")
    except (OSError, TypeError, ValueError, KeyError) as error:
        problems.append(f"blueprint bootstrap parse failed: {error}")

    control_root_code, control_root = _git_output(workspace, "rev-parse", "--show-toplevel")
    vault_root_code, vault_root = _git_output(workspace / "KnowledgeHub", "rev-parse", "--show-toplevel")
    if control_root_code != 0 or vault_root_code != 0:
        problems.append("both control and Vault Git roots must be initialized")
    else:
        if Path(control_root).resolve() != workspace:
            problems.append(f"wrong control Git root: {control_root}")
        if Path(vault_root).resolve() != workspace / "KnowledgeHub":
            problems.append(f"wrong Vault Git root: {vault_root}")
        for boundary in ("KnowledgeHub", "runtime"):
            ignored_code, _ = _git_output(workspace, "check-ignore", "--no-index", "--", boundary)
            if ignored_code != 0:
                problems.append(f"control repository must ignore {boundary}/")
        tracked_code, tracked = _git_output(workspace, "ls-files", "--stage", "--", "KnowledgeHub", "runtime")
        if tracked_code != 0 or tracked:
            problems.append("control index tracks a forbidden boundary")
    return problems

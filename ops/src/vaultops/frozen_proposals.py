"""Promote one validated frozen live-provider candidate into C19 Pending.

This is intentionally a promotion step, not a provider call and not a Vault
apply.  It turns a selected C35 triage candidate into the same review artifact
that C19 already knows how to inspect, approve, and apply.  The runtime source
bindings remain explicit so a missing canonical source cannot be silently
replaced with an invented Vault note.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from pathlib import Path
from typing import Any

from .frozen_evidence import (
    FrozenEvidenceError,
    FrozenTriageEvidence,
    inspect_frozen_triage_proposal,
)
from .note_engine import NoteContractError, NoteEngine, resolve_vault_relative_path, write_note_file
from .recovery import fsync_directory
from .template_engine import TemplateRenderError, render_note_template

EXIT_OK = 0
EXIT_CONFLICT = 30
EXIT_INPUT_INVALID = 10
_MAX_PROPOSAL_BYTES = 256 * 1024
_MAX_TITLE_BYTES = 200


def _workspace(root: str | Path) -> Path:
    candidate = Path(root).expanduser()
    if candidate.is_symlink() or not candidate.is_dir():
        raise ValueError("control root must be an existing non-symlink directory")
    return candidate.resolve()


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _uuid4_from_seed(seed: str) -> str:
    value = bytearray(hashlib.sha256(seed.encode("utf-8")).digest()[:16])
    value[6] = (value[6] & 0x0F) | 0x40
    value[8] = (value[8] & 0x3F) | 0x80
    return str(uuid.UUID(bytes=bytes(value)))


def _manifest_json(value: dict[str, Any]) -> str:
    """Serialize a manifest without allowing a Markdown comment terminator."""

    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).replace(
        "-->", r"\u002d\u002d>"
    )


def _proposal_body(
    evidence: FrozenTriageEvidence,
    *,
    manifest: dict[str, Any],
    target_markdown: str,
) -> str:
    evidence_view = {
        "job_id": evidence.job_id,
        "action": evidence.context["action"],
        "candidate_id": evidence.selected_candidate["candidate_id"],
        "candidate_sha256": evidence.frozen_candidate["candidate_sha256"],
        "candidate_type": evidence.target_type,
        "candidate_title": evidence.target_title,
        "source_path": evidence.source["path"],
        "source_sha256": evidence.source["content_hash"],
        "index_generation_id": evidence.context["index_generation_id"],
        "candidate_set_sha256": evidence.context["candidate_set_sha256"],
        "provider": evidence.context["provider"],
        "context_sha256": evidence.context["context_sha256"],
        "response_sha256": evidence.response["response_sha256"],
        "receipt_sha256": evidence.receipt["receipt_sha256"],
        "output_sha256": evidence.response["output_sha256"],
    }
    source_lines = "\n".join(
        f"- `{item['path']}` — `{item['sha256']}`" for item in evidence.source_bindings
    )
    excerpt = str(evidence.frozen_candidate.get("excerpt", "")).strip()
    return (
        f"# AI Proposal — E05 Frozen Triage {evidence.proposal['proposal_id'][:12]}\n\n"
        "> 이 노트는 frozen live-provider evidence에서 파생된 검토용 제안이며 정본이 아니다. "
        "C19 승인 후에만 canonical target을 생성한다.\n\n"
        "## 제안 요약\n\n"
        f"The selected `{evidence.target_type}` candidate deterministically maps to "
        f"`{evidence.target_path}`. The provider response remains untrusted and the target is create-only.\n\n"
        "## Frozen provider evidence\n\n"
        "```json\n"
        f"{json.dumps(evidence_view, ensure_ascii=False, indent=2, sort_keys=True)}\n"
        "```\n\n"
        "## Frozen excerpt\n\n"
        f"> {excerpt}\n\n"
        "## Runtime bindings\n\n"
        f"{source_lines}\n\n"
        "## Target preview\n\n"
        "```markdown\n"
        f"{target_markdown}```\n\n"
        "## C19 manifest\n\n"
        "<!-- vaultops:proposal-manifest\n"
        f"{_manifest_json(manifest)}\n"
        "-->\n"
    )


def _target_markdown(evidence: FrozenTriageEvidence) -> str:
    excerpt = str(evidence.frozen_candidate.get("excerpt", "")).strip()
    source_path = str(evidence.source["path"])
    locator = str(evidence.source.get("locator") or "frozen evidence")
    body = (
        f"# {evidence.target_title}\n\n"
        "## 가능성\n\n"
        f"{excerpt}\n\n"
        "## 왜 흥미로운가\n\n"
        "이 아이디어는 live provider activation 중에도 thin-client broker가 proposal-only 경계를 유지해야 한다는 frozen evidence에서 파생되었다.\n\n"
        "## 검증할 가정\n\n"
        "Obsidian presentation client와 canonical C19 apply authority가 계속 분리되어 있는지 확인한다.\n\n"
        "## 다음 실험\n\n"
        "- [ ] 실제 사용자 review에서 target 내용과 scope를 확인한다.\n\n"
        "## 연결\n\n"
        f"Frozen source: `{source_path}` ({locator}); job `{evidence.job_id}`.\n"
    )
    try:
        rendered = render_note_template(
            "T21_Idea.md",
            {
                "title": evidence.target_title,
                "id": _uuid4_from_seed(
                    f"e05-frozen-idea:{evidence.job_id}:{evidence.frozen_candidate['candidate_sha256']}"
                ),
                "created": evidence.response["completed_at"],
                "modified": evidence.response["completed_at"],
                "possibility": excerpt,
                "body": body,
            },
        )
    except (TemplateRenderError, ValueError) as error:
        raise FrozenEvidenceError("E05_TARGET_RENDER_INVALID", str(error)) from error
    return rendered.markdown


def promote_frozen_triage_proposal(
    root: str | Path,
    *,
    job_id: str,
    candidate_id: str,
) -> tuple[dict[str, Any], int]:
    """Create one C19 Pending proposal from an existing frozen triage run."""

    operation = "ai promote-frozen"
    try:
        workspace = _workspace(root)
        source_bindings = []
        for relative in (
            f"runtime/runs/{job_id}/context.json",
            f"runtime/runs/{job_id}/response.json",
            f"runtime/runs/{job_id}/receipts/provider-receipt.json",
        ):
            path = workspace / relative
            if path.is_symlink() or not path.is_file():
                raise FrozenEvidenceError("E05_RUNTIME_FILE_INVALID", f"required runtime evidence is missing: {relative}")
            source_bindings.append(f"{relative}|sha256:{_sha256(path.read_bytes())}")
        evidence = inspect_frozen_triage_proposal(
            workspace,
            source_bindings=source_bindings,
            candidate_id=candidate_id,
        )
        if evidence.job_id != job_id:
            raise FrozenEvidenceError("E05_RUNTIME_JOB_DRIFT", "requested job id differs from frozen runtime evidence")
        target = workspace / "KnowledgeHub" / evidence.target_path
        if target.is_symlink() or target.exists():
            raise FrozenEvidenceError("E05_TARGET_EXISTS", "derived canonical target already exists")
        if not target.parent.is_dir() or target.parent.is_symlink():
            raise FrozenEvidenceError("E05_TARGET_PARENT_INVALID", "derived target parent must already be a real directory")
        target_markdown = _target_markdown(evidence)
        frozen_manifest = {
            "job_id": evidence.job_id,
            "candidate_id": evidence.selected_candidate["candidate_id"],
            "candidate_sha256": evidence.frozen_candidate["candidate_sha256"],
            "source_path": evidence.source["path"],
            "source_sha256": evidence.source["content_hash"],
            "target_type": evidence.target_type,
            "title": evidence.target_title,
        }
        manifest: dict[str, Any] = {
            "action": "create_note",
            "target_path": evidence.target_path,
            "target_markdown": target_markdown,
            "frozen_evidence": frozen_manifest,
        }
        evidence = inspect_frozen_triage_proposal(
            workspace,
            source_bindings=source_bindings,
            candidate_id=candidate_id,
            manifest=manifest,
        )
        proposal_title = f"E05 Frozen Triage {evidence.proposal['proposal_id'][:12]}"
        proposal_relative = f"01_AI_Review/Pending/{proposal_title}.md"
        proposal_absolute = resolve_vault_relative_path(workspace / "KnowledgeHub", proposal_relative)
        body = _proposal_body(evidence, manifest=manifest, target_markdown=target_markdown)
        rendered = render_note_template(
            "T01_AI_Proposal.md",
            {
                "title": proposal_title,
                "id": evidence.proposal["proposal_id"],
                "proposal_id": evidence.proposal["proposal_id"],
                "created": evidence.response["completed_at"],
                "modified": evidence.response["completed_at"],
                "sensitivity": "personal",
                "source_hashes": [
                    f"{item['path']}|sha256:{item['sha256']}" for item in evidence.source_bindings
                ],
                "body": body,
            },
        )
        artifact = rendered.markdown
        if len(artifact.encode("utf-8")) > _MAX_PROPOSAL_BYTES:
            raise FrozenEvidenceError("E05_PROPOSAL_TOO_LARGE", "frozen proposal exceeds the bounded byte limit")
        try:
            NoteEngine.from_root(workspace).typed_note(proposal_relative, artifact)
        except (NoteContractError, ValueError, UnicodeError) as error:
            raise FrozenEvidenceError("E05_PROPOSAL_NOTE_INVALID", str(error)) from error
        payload = artifact.encode("utf-8")
        if proposal_absolute.exists() or proposal_absolute.is_symlink():
            if proposal_absolute.is_symlink() or not proposal_absolute.is_file() or proposal_absolute.read_bytes() != payload:
                raise FrozenEvidenceError("E05_PROPOSAL_CONFLICT", "existing Pending artifact differs")
            return {
                "status": "NO_OP",
                "operation": operation,
                "capability": "E05",
                "job_id": evidence.job_id,
                "proposal_path": proposal_relative,
                "proposal_sha256": _sha256(payload),
                "target_path": evidence.target_path,
                "provider_called": False,
                "mutation_performed": False,
                "replayed": True,
            }, EXIT_OK
        write_note_file(proposal_absolute, artifact, overwrite=False)
        fsync_directory(proposal_absolute.parent)
        return {
            "status": "PASS",
            "operation": operation,
            "capability": "E05",
            "job_id": evidence.job_id,
            "candidate_id": evidence.selected_candidate["candidate_id"],
            "candidate_sha256": evidence.frozen_candidate["candidate_sha256"],
            "proposal_path": proposal_relative,
            "proposal_sha256": _sha256(payload),
            "target_path": evidence.target_path,
            "source_bindings": list(evidence.source_bindings),
            "provider_called": False,
            "mutation_performed": True,
            "replayed": False,
        }, EXIT_OK
    except FrozenEvidenceError as error:
        return {
            "status": "FAIL",
            "operation": operation,
            "provider_called": False,
            "mutation_performed": False,
            "errors": [{"code": error.code, "message": str(error)}],
        }, EXIT_CONFLICT
    except (OSError, NoteContractError, TemplateRenderError, TypeError, ValueError) as error:
        return {
            "status": "FAIL",
            "operation": operation,
            "provider_called": False,
            "mutation_performed": False,
            "errors": [{"code": "E05_PROMOTION_INVALID", "message": str(error)}],
        }, EXIT_INPUT_INVALID


__all__ = ["promote_frozen_triage_proposal"]

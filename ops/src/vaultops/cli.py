"""KnowledgeOS command surface."""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Sequence
from pathlib import Path

from . import __version__
from .blueprint import validate_blueprint
from .bootstrap import bootstrap
from .bridge_publish import bridge_status, ingest_bridge_request, publish_bridge_response
from .configure import configure
from .diagnostics import (
    EXIT_INPUT_INVALID,
    EXIT_VALIDATION_FAILED,
    doctor_report,
    git_status_report,
    plugins_audit_report,
)
from .foundation import check_foundation, check_source_manifest
from .local_commands import capture_text, capture_url, create_note, format_notes
from .note_engine import NoteEngine, UnsafePathError, resolve_vault_relative_path
from .proposals import apply_proposal, approve_proposal, reject_proposal, review_proposals
from .reconcile import apply_repair_plan, reconcile_transactions, repair_plan, verify_receipts
from .schema_export import export_schema_artifacts
from .transactions import archive_project, finalize_capture, import_asset
from .triage import deterministic_triage
from .workflows import create_period_note, create_project_bundle
from .yaml_safe import load_yaml_file


def _control_root() -> Path:
    return Path(os.environ.get("KNOWLEDGEOS_CONTROL_ROOT", "/workspace/control"))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="vaultctl",
        description="KnowledgeOS deterministic runtime and validation harness",
    )
    parser.add_argument("--version", action="version", version=f"vaultops {__version__}")
    commands = parser.add_subparsers(dest="command")

    commands.add_parser("version", help="print the vaultops version")
    bootstrap_command = commands.add_parser(
        "bootstrap", help="add missing canonical directories and C07 templates"
    )
    bootstrap_command.add_argument("--dry-run", action="store_true", help="plan without writing")
    bootstrap_command.add_argument("--root", type=Path, default=None, help="mounted control root")

    configure_command = commands.add_parser(
        "configure", help="verify the confirmed notes identity and create the root sentinel"
    )
    configure_command.add_argument("--interactive", action="store_true", help="allow prompts for missing identity values")
    configure_command.add_argument("--remote", "--notes-remote-url", dest="remote", default=None)
    configure_command.add_argument("--branch", dest="branch", default=None)
    configure_command.add_argument("--vault-uuid", dest="vault_uuid", default=None)
    configure_command.add_argument("--canonical-vault-name", dest="canonical_vault_name", default=None)
    configure_command.add_argument(
        "--confirm-sensitive-data-boundary",
        action="store_true",
        help="confirm that the Vault contents may be synchronized to the configured notes repository",
    )
    configure_command.add_argument("--dry-run", action="store_true", help="verify without creating the sentinel")
    configure_command.add_argument("--root", type=Path, default=None, help="mounted control root")

    project = commands.add_parser("project", help="create-only project bundle workflows")
    project_commands = project.add_subparsers(dest="project_command", required=True)
    project_create = project_commands.add_parser(
        "create", help="atomically create a project root note and sibling directories"
    )
    project_create.add_argument("--title", required=True, help="bounded project title / filename stem")
    project_create.add_argument("--id", dest="identifier", default=None, help="lowercase UUID v4")
    project_create.add_argument("--created-at", default=None, help="ISO datetime with timezone")
    project_create.add_argument("--status", default="planned")
    project_create.add_argument("--outcome", default=None)
    project_create.add_argument("--priority", default="medium")
    project_create.add_argument("--focus-rank", type=int, default=None)
    project_create.add_argument("--next-action", default=None)
    project_create.add_argument("--target-date", default=None)
    project_create.add_argument("--dry-run", action="store_true", help="plan without writing")
    project_create.add_argument("--root", type=Path, default=None, help="mounted control root")

    period = commands.add_parser("period", help="deterministic journal period workflows")
    period_commands = period.add_subparsers(dest="period_command", required=True)
    period_create = period_commands.add_parser("create", help="create a daily, weekly, or monthly note")
    period_create.add_argument("--kind", required=True, choices=("daily", "weekly", "monthly"))
    period_create.add_argument("--date", dest="selected_date", default=None, help="YYYY-MM-DD")
    period_create.add_argument("--dry-run", action="store_true", help="plan without writing")
    period_create.add_argument("--root", type=Path, default=None, help="mounted control root")

    capture = commands.add_parser("capture", help="create one capture note without overwriting")
    capture_commands = capture.add_subparsers(dest="capture_command", required=True)
    capture_text_command = capture_commands.add_parser("text", help="capture bounded text from stdin")
    capture_text_command.add_argument("--stdin", action="store_true", required=True)
    capture_text_command.add_argument("--device", choices=("mac", "iphone", "ipad"), required=True)
    capture_text_command.add_argument("--title", default=None, help="optional bounded filename title")
    capture_text_command.add_argument("--created-at", default=None, help="ISO datetime with timezone")
    capture_text_command.add_argument("--dry-run", action="store_true", help="plan without writing")
    capture_text_command.add_argument("--root", type=Path, default=None, help="mounted control root")
    capture_url_command = capture_commands.add_parser("url", help="capture one URL from stdin or a file")
    url_input = capture_url_command.add_mutually_exclusive_group(required=True)
    url_input.add_argument("--url-stdin", action="store_true")
    url_input.add_argument("--url-file", type=Path)
    comment_input = capture_url_command.add_mutually_exclusive_group()
    comment_input.add_argument("--comment-stdin", action="store_true")
    comment_input.add_argument("--comment-file", type=Path)
    capture_url_command.add_argument("--title", default=None, help="optional bounded filename title")
    capture_url_command.add_argument("--created-at", default=None, help="ISO datetime with timezone")
    capture_url_command.add_argument("--dry-run", action="store_true", help="plan without writing")
    capture_url_command.add_argument("--root", type=Path, default=None, help="mounted control root")

    doctor = commands.add_parser("doctor", help="run read-only runtime diagnostics")
    doctor.add_argument("--root", type=Path, default=None, help="mounted control root")

    git = commands.add_parser("git", help="read-only Git diagnostics")
    git_commands = git.add_subparsers(dest="git_command", required=True)
    git_status = git_commands.add_parser("status", help="show independent control/Vault Git state")
    git_status.add_argument("--repo", choices=("control", "vault", "both"), default="both")
    git_status.add_argument("--root", type=Path, default=None, help="mounted control root")

    plugins = commands.add_parser("plugins", help="read-only plugin diagnostics")
    plugins_commands = plugins.add_subparsers(dest="plugins_command", required=True)
    plugins_audit = plugins_commands.add_parser("audit", help="audit the configured Mac plugin profile")
    plugins_audit.add_argument("--profile", choices=("mac",), default="mac")
    plugins_audit.add_argument("--root", type=Path, default=None, help="mounted control root")

    foundation = commands.add_parser("foundation", help="run portable foundation checks")
    foundation_commands = foundation.add_subparsers(dest="foundation_command", required=True)
    foundation_source = foundation_commands.add_parser(
        "source-check", help="check only the fixed blueprint checksum manifest"
    )
    foundation_source.add_argument("--root", type=Path, default=None, help="mounted control root")
    foundation_check = foundation_commands.add_parser("check", help="check paths, hashes, and Git roots")
    foundation_check.add_argument("--root", type=Path, default=None, help="mounted control root")

    blueprint = commands.add_parser("blueprint", help="inspect the blueprint validation capability")
    blueprint_commands = blueprint.add_subparsers(dest="blueprint_command", required=True)
    blueprint_commands.add_parser("status", help="show the current validation profile")
    blueprint_validate = blueprint_commands.add_parser(
        "validate",
        help="validate blueprint.yaml with the Draft 2020-12 JSON Schema",
    )
    blueprint_validate.add_argument("--root", type=Path, default=None, help="mounted control root")

    schema = commands.add_parser("schema", help="export and verify owned control artifacts")
    schema_commands = schema.add_subparsers(dest="schema_command", required=True)
    schema_export = schema_commands.add_parser(
        "export",
        help="generate owned policies and trusted schema copies",
    )
    schema_export.add_argument("--check", action="store_true", help="check byte-for-byte zero-diff without writing")
    schema_export.add_argument("--root", type=Path, default=None, help="mounted control root")

    ai = commands.add_parser("ai", help="proposal-only deterministic AI contract commands")
    ai_commands = ai.add_subparsers(dest="ai_command", required=True)
    ai_triage = ai_commands.add_parser("triage", help="read one note and return a non-mutating triage proposal")
    ai_triage.add_argument("--source", required=True, help="Vault-relative capture or daily note path")
    ai_triage.add_argument("--expected-sha256", required=True, help="lowercase SHA-256 of source bytes")
    ai_triage.add_argument("--locator", default=None, help="required daily fragment locator")
    ai_triage.add_argument("--fragment-sha256", default=None, help="required daily fragment SHA-256")
    ai_triage.add_argument("--root", type=Path, default=None, help="mounted control root")
    ai_review = ai_commands.add_parser("review", help="inspect pending proposals without mutation")
    ai_review.add_argument("--proposal", "--path", dest="proposal_path", default=None)
    ai_review.add_argument("--root", type=Path, default=None, help="mounted control root")
    ai_approve = ai_commands.add_parser("approve", help="create one digest-bound approval artifact")
    ai_approve.add_argument("--proposal", "--path", dest="proposal_path", required=True)
    ai_approve.add_argument("--expected-sha256", "--sha256", dest="expected_sha256", required=True)
    ai_approve.add_argument("--root", type=Path, default=None, help="mounted control root")
    ai_reject = ai_commands.add_parser("reject", help="reject and close one pending proposal")
    ai_reject.add_argument("--proposal", "--path", dest="proposal_path", required=True)
    ai_reject.add_argument("--expected-sha256", "--sha256", dest="expected_sha256", required=True)
    rejection_reason = ai_reject.add_mutually_exclusive_group(required=True)
    rejection_reason.add_argument("--reason")
    rejection_reason.add_argument("--reason-stdin", action="store_true")
    rejection_reason.add_argument("--reason-file", type=Path)
    ai_reject.add_argument("--root", type=Path, default=None, help="mounted control root")
    ai_apply = ai_commands.add_parser("apply", help="apply an approved proposal and close it")
    ai_apply.add_argument("--proposal", "--path", dest="proposal_path", required=True)
    ai_apply.add_argument("--approval", type=Path, default=None, help="optional explicit approval artifact")
    ai_apply.add_argument("--root", type=Path, default=None, help="mounted control root")

    note = commands.add_parser("note", help="validate one Markdown note against the strict registry")
    note_commands = note.add_subparsers(dest="note_command", required=True)
    note_validate = note_commands.add_parser("validate", help="read and validate a Vault-relative Markdown note")
    note_validate.add_argument("path", type=Path, help="Vault-relative Markdown path")
    note_validate.add_argument("--root", type=Path, default=None, help="mounted control root")
    note_create = note_commands.add_parser("create", help="create one typed note without overwriting")
    note_create.add_argument("--type", dest="note_type", required=True)
    note_create.add_argument("--title", required=True, help="bounded note title / filename stem")
    body_input = note_create.add_mutually_exclusive_group()
    body_input.add_argument("--body-stdin", action="store_true")
    body_input.add_argument("--body-file", type=Path)
    note_create.add_argument("--project", default=None, help="validated Vault-relative project root")
    note_create.add_argument("--date", dest="selected_date", default=None, help="YYYY-MM-DD for meetings")
    note_create.add_argument("--id", dest="identifier", default=None, help="lowercase UUID v4")
    note_create.add_argument("--created-at", default=None, help="ISO datetime with timezone")
    note_create.add_argument("--status", default=None)
    note_create.add_argument("--source-kind", default=None)
    note_create.add_argument("--dry-run", action="store_true", help="plan without writing")
    note_create.add_argument("--root", type=Path, default=None, help="mounted control root")

    fmt = commands.add_parser("fmt", help="check or guarded-format one canonical Markdown note")
    fmt.add_argument("--check", action="store_true", help="check without writing")
    fmt.add_argument("--path", type=Path, default=None, help="explicit Vault-relative Markdown path")
    fmt.add_argument("--root", type=Path, default=None, help="mounted control root")

    asset = commands.add_parser("asset", help="explicit binary asset transactions")
    asset_commands = asset.add_subparsers(dest="asset_command", required=True)
    asset_import = asset_commands.add_parser("import", help="copy one absolute regular file into 80_Assets")
    asset_import.add_argument("--source", "--path", dest="source_path", type=Path, required=True)
    asset_import.add_argument("--target-directory", choices=("Inbox", "Images", "Documents", "Audio"), default="Inbox")
    asset_import.add_argument("--filename", default=None, help="optional destination filename")
    asset_import.add_argument("--mime-type", default=None)
    asset_import.add_argument("--expected-sha256", "--sha256", dest="expected_sha256", default=None)
    asset_import.add_argument("--dry-run", action="store_true", help="plan without writing")
    asset_import.add_argument("--root", type=Path, default=None, help="mounted control root")

    capture_finalize_command = capture_commands.add_parser(
        "finalize", help="hash-guard and archive one capture after interactive triage"
    )
    capture_finalize_command.add_argument("--path", dest="capture_path", type=str, required=True)
    capture_finalize_command.add_argument("--expected-sha256", "--sha256", dest="expected_sha256", required=True)
    capture_finalize_command.add_argument("--outcome", choices=("triaged", "discarded"), required=True)
    capture_finalize_command.add_argument("--related", default=None, help="Vault-relative note path or wikilink")
    capture_finalize_command.add_argument("--modified-at", default=None, help="ISO datetime with timezone")
    capture_finalize_command.add_argument("--job-id", default=None, help="UUIDv4 used to resume a prior transaction")
    capture_finalize_command.add_argument("--dry-run", action="store_true", help="plan without writing")
    capture_finalize_command.add_argument("--root", type=Path, default=None, help="mounted control root")

    project_archive = project_commands.add_parser(
        "archive", help="hash-guard and move one complete project bundle into 90_Archive"
    )
    project_archive.add_argument("--project", "--path", dest="project_path", required=True)
    project_archive.add_argument(
        "--hash",
        dest="expected_hashes",
        action="append",
        required=True,
        metavar="PATH=SHA256",
        help="repeat once for every project bundle file",
    )
    project_archive.add_argument("--archive-year", type=int, default=None)
    project_archive.add_argument("--job-id", default=None, help="UUIDv4 used to resume a prior transaction")
    project_archive.add_argument("--dry-run", action="store_true", help="plan without writing")
    project_archive.add_argument("--root", type=Path, default=None, help="mounted control root")

    reconcile_command = commands.add_parser(
        "reconcile", help="read-only inspection of durable local transaction journals"
    )
    reconcile_command.add_argument("--job-id", default=None, help="inspect one UUIDv4 transaction")
    reconcile_command.add_argument("--root", type=Path, default=None, help="mounted control root")

    repair = commands.add_parser("repair", help="explicitly plan or apply local transaction repairs")
    repair_commands = repair.add_subparsers(dest="repair_command", required=True)
    repair_plan_command = repair_commands.add_parser(
        "plan", help="build a deterministic repair plan without changing Vault state"
    )
    repair_plan_command.add_argument("--job-id", default=None, help="limit the plan to one UUIDv4 transaction")
    repair_plan_command.add_argument(
        "--output", type=Path, default=None, help="optional create-only plan file directly under runtime"
    )
    repair_plan_command.add_argument("--root", type=Path, default=None, help="mounted control root")
    repair_apply_command = repair_commands.add_parser(
        "apply", help="apply a still-current digest-bound repair plan"
    )
    repair_apply_command.add_argument("--plan", type=Path, required=True, help="canonical repair plan JSON")
    repair_apply_command.add_argument("--root", type=Path, default=None, help="mounted control root")

    receipts = commands.add_parser("receipts", help="verify immutable local transaction receipts")
    receipts_commands = receipts.add_subparsers(dest="receipts_command", required=True)
    receipts_verify_command = receipts_commands.add_parser("verify", help="verify journal-bound receipts")
    receipts_verify_command.add_argument("--job-id", default=None, help="verify one UUIDv4 transaction receipt")
    receipts_verify_command.add_argument("--root", type=Path, default=None, help="mounted control root")

    bridge = commands.add_parser("bridge", help="validate and publish local bridge transport")
    bridge_commands = bridge.add_subparsers(dest="bridge_command", required=True)
    bridge_ingest_command = bridge_commands.add_parser(
        "ingest", help="validate one committed bridge request without changing the Vault"
    )
    bridge_ingest_command.add_argument("--request", "--path", dest="request_path", type=Path, default=None)
    bridge_ingest_command.add_argument("--job-id", default=None, help="limit ingest to one UUIDv4 request")
    bridge_ingest_command.add_argument("--root", type=Path, default=None, help="mounted control root")
    bridge_status_command = bridge_commands.add_parser(
        "status", help="list local bridge requests and responses without changing state"
    )
    bridge_status_command.add_argument("--job-id", default=None, help="limit status to one UUIDv4 job")
    bridge_status_command.add_argument("--root", type=Path, default=None, help="mounted control root")
    bridge_publish_command = bridge_commands.add_parser(
        "publish", help="create and commit one exact local response path set"
    )
    bridge_publish_command.add_argument("--response-file", "--response", dest="response_file", type=Path, required=True)
    bridge_publish_command.add_argument("--proposal-file", dest="proposal_file", type=Path, default=None)
    bridge_publish_command.add_argument("--root", type=Path, default=None, help="mounted control root")

    yaml_command = commands.add_parser("yaml", help="exercise the safe control YAML loader")
    yaml_command.add_argument("path", type=Path, help="UTF-8 YAML file")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command is None:
        parser.print_help()
        return 0
    if args.command == "version":
        print(__version__)
        return 0
    if args.command == "bootstrap":
        report = bootstrap(args.root or _control_root(), dry_run=args.dry_run)
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
        return 0 if report["status"] == "PASS" else 1
    if args.command == "configure":
        report = configure(
            args.root or _control_root(),
            remote=args.remote,
            branch=args.branch,
            vault_uuid=args.vault_uuid,
            canonical_vault_name=args.canonical_vault_name,
            sensitive_data_confirmed=args.confirm_sensitive_data_boundary,
            interactive=args.interactive,
            dry_run=args.dry_run,
        )
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
        return 0 if report["status"] == "PASS" else 1
    if args.command == "project" and args.project_command == "create":
        report = create_project_bundle(
            args.root or _control_root(),
            title=args.title,
            dry_run=args.dry_run,
            identifier=args.identifier,
            created_at=args.created_at,
            status=args.status,
            outcome=args.outcome,
            priority=args.priority,
            focus_rank=args.focus_rank,
            next_action=args.next_action,
            target_date=args.target_date,
        )
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
        return 0 if report["status"] == "PASS" else 1
    if args.command == "period" and args.period_command == "create":
        report = create_period_note(
            args.root or _control_root(),
            kind=args.kind,
            selected_date=args.selected_date,
            dry_run=args.dry_run,
        )
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
        return 0 if report["status"] == "PASS" else 1
    if args.command == "capture" and args.capture_command == "text":
        report, exit_code = capture_text(
            args.root or _control_root(),
            stdin=args.stdin,
            device=args.device,
            title=args.title,
            created_at=args.created_at,
            dry_run=args.dry_run,
        )
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
        return exit_code
    if args.command == "capture" and args.capture_command == "url":
        report, exit_code = capture_url(
            args.root or _control_root(),
            url_stdin=args.url_stdin,
            url_file=args.url_file,
            comment_stdin=args.comment_stdin,
            comment_file=args.comment_file,
            title=args.title,
            created_at=args.created_at,
            dry_run=args.dry_run,
        )
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
        return exit_code
    if args.command == "note" and args.note_command == "create":
        report, exit_code = create_note(
            args.root or _control_root(),
            note_type=args.note_type,
            title=args.title,
            body_stdin=args.body_stdin,
            body_file=args.body_file,
            project=args.project,
            selected_date=args.selected_date,
            identifier=args.identifier,
            created_at=args.created_at,
            status=args.status,
            source_kind=args.source_kind,
            dry_run=args.dry_run,
        )
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
        return exit_code
    if args.command == "fmt":
        report, exit_code = format_notes(
            args.root or _control_root(),
            check=args.check,
            relative=args.path.as_posix() if args.path is not None else None,
        )
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
        return exit_code
    if args.command == "asset" and args.asset_command == "import":
        report, exit_code = import_asset(
            args.root or _control_root(),
            source_path=args.source_path,
            target_directory=args.target_directory,
            filename=args.filename,
            mime_type=args.mime_type,
            expected_sha256=args.expected_sha256,
            dry_run=args.dry_run,
        )
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
        return exit_code
    if args.command == "capture" and args.capture_command == "finalize":
        report, exit_code = finalize_capture(
            args.root or _control_root(),
            capture_path=args.capture_path,
            expected_sha256=args.expected_sha256,
            outcome=args.outcome,
            related=args.related,
            modified_at=args.modified_at,
            dry_run=args.dry_run,
            job_id=args.job_id,
        )
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
        return exit_code
    if args.command == "project" and args.project_command == "archive":
        expected_hashes: dict[str, str] = {}
        for item in args.expected_hashes:
            if "=" not in item:
                parser.error("--hash must use PATH=SHA256")
            relative, digest = item.rsplit("=", 1)
            if not relative or not digest:
                parser.error("--hash must use PATH=SHA256")
            expected_hashes[relative] = digest
        report, exit_code = archive_project(
            args.root or _control_root(),
            project_path=args.project_path,
            expected_hashes=expected_hashes,
            archive_year=args.archive_year,
            dry_run=args.dry_run,
            job_id=args.job_id,
        )
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
        return exit_code
    if args.command == "reconcile":
        report, exit_code = reconcile_transactions(args.root or _control_root(), job_id=args.job_id)
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
        return exit_code
    if args.command == "repair":
        root = args.root or _control_root()
        if args.repair_command == "plan":
            report, exit_code = repair_plan(
                root,
                job_id=args.job_id,
                output=args.output,
            )
        else:
            report, exit_code = apply_repair_plan(root, plan_path=args.plan)
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
        return exit_code
    if args.command == "receipts" and args.receipts_command == "verify":
        report, exit_code = verify_receipts(args.root or _control_root(), job_id=args.job_id)
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
        return exit_code
    if args.command == "bridge":
        root = args.root or _control_root()
        if args.bridge_command == "ingest":
            report, exit_code = ingest_bridge_request(
                root,
                request_path=args.request_path,
                job_id=args.job_id,
            )
        elif args.bridge_command == "status":
            report, exit_code = bridge_status(root, job_id=args.job_id)
        else:
            report, exit_code = publish_bridge_response(
                root,
                response_file=args.response_file,
                proposal_file=args.proposal_file,
            )
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
        return exit_code
    if args.command == "doctor":
        report, exit_code = doctor_report(args.root or _control_root())
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
        return exit_code
    if args.command == "git" and args.git_command == "status":
        report, exit_code = git_status_report(args.root or _control_root(), args.repo)
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
        return exit_code
    if args.command == "plugins" and args.plugins_command == "audit":
        report, exit_code = plugins_audit_report(args.root or _control_root(), args.profile)
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
        return exit_code
    if args.command == "foundation":
        root = args.root or _control_root()
        if args.foundation_command == "source-check":
            problems = check_source_manifest(root)
            if problems:
                for problem in problems:
                    print(f"ERROR: {problem}", file=sys.stderr)
                return EXIT_INPUT_INVALID
            print("Source checksum checks: PASS")
            return 0
        problems = check_foundation(root)
        if problems:
            for problem in problems:
                print(f"ERROR: {problem}", file=sys.stderr)
            return 1
        print("Foundation checks: PASS")
        return 0
    if args.command == "blueprint":
        if args.blueprint_command == "status":
            print("capability_profile=portable_core")
            print("blueprint_json_schema=implemented:C02")
            print("cross_document_validation=implemented:C03-C04")
            print("generated_zero_diff=implemented:C05-C06 (vaultctl schema export --check)")
            return 0
        result = validate_blueprint(args.root or _control_root())
        print(result.as_json(), end="")
        return result.exit_code
    if args.command == "schema":
        result = export_schema_artifacts(args.root or _control_root(), check=args.check)
        print(result.as_json(), end="")
        return result.exit_code
    if args.command == "ai" and args.ai_command == "review":
        report, exit_code = review_proposals(
            args.root or _control_root(),
            proposal_path=args.proposal_path,
        )
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
        return exit_code
    if args.command == "ai" and args.ai_command == "approve":
        report, exit_code = approve_proposal(
            args.root or _control_root(),
            proposal_path=args.proposal_path,
            expected_sha256=args.expected_sha256,
        )
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
        return exit_code
    if args.command == "ai" and args.ai_command == "reject":
        try:
            if args.reason_file is not None:
                reason = args.reason_file.read_text(encoding="utf-8")
            elif args.reason_stdin:
                reason = sys.stdin.read()
            else:
                reason = args.reason
        except (OSError, UnicodeError) as error:
            print(json.dumps({"status": "FAIL", "errors": [{"code": "REASON_INPUT_INVALID", "message": str(error)}]}))
            return EXIT_INPUT_INVALID
        report, exit_code = reject_proposal(
            args.root or _control_root(),
            proposal_path=args.proposal_path,
            expected_sha256=args.expected_sha256,
            reason=reason,
        )
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
        return exit_code
    if args.command == "ai" and args.ai_command == "apply":
        report, exit_code = apply_proposal(
            args.root or _control_root(),
            proposal_path=args.proposal_path,
            approval_path=args.approval,
        )
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
        return exit_code
    if args.command == "ai" and args.ai_command == "triage":
        report, exit_code = deterministic_triage(
            args.root or _control_root(),
            source_path=args.source,
            expected_sha256=args.expected_sha256,
            locator=args.locator,
            fragment_sha256=args.fragment_sha256,
        )
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
        return exit_code
    if args.command == "note":
        root = (args.root or _control_root()).resolve()
        if args.note_command == "validate":
            try:
                relative = args.path.as_posix()
                note_path = resolve_vault_relative_path(root / "KnowledgeHub", relative)
                result = NoteEngine.from_root(root).validate_text(relative, note_path.read_text(encoding="utf-8"))
                print(json.dumps(result.as_dict(), ensure_ascii=False, indent=2, sort_keys=True))
                return 0 if result.passed else EXIT_VALIDATION_FAILED
            except (OSError, UnicodeError, UnsafePathError, ValueError) as error:
                print(
                    json.dumps(
                        {
                            "status": "FAIL",
                            "path": args.path.as_posix(),
                            "errors": [{"code": "NOTE_INPUT_INVALID", "locator": "/", "message": str(error)}],
                        },
                        ensure_ascii=False,
                        indent=2,
                        sort_keys=True,
                    )
                )
                return EXIT_INPUT_INVALID
    if args.command == "yaml":
        value = load_yaml_file(args.path)
        print(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    parser.error(f"unsupported command: {args.command}")
    return 2


app = main

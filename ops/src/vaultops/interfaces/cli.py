"""Small operator-only KnowledgeOS maintenance interface."""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from dataclasses import asdict
from pathlib import Path
from typing import Any

from .. import __version__
from ..paths import resolve_paths


def _root(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--root", type=Path, default=None, help="configured control root")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="vaultctl", description="KnowledgeOS maintenance")
    parser.add_argument("--version", action="version", version=f"vaultops {__version__}")
    commands = parser.add_subparsers(dest="command")
    commands.add_parser("version", help="print the installed version")

    bootstrap = commands.add_parser("bootstrap", help="initialize declared layout")
    bootstrap.add_argument("--dry-run", action="store_true")
    _root(bootstrap)
    configure = commands.add_parser("configure", help="verify and establish Vault identity")
    for key in ("remote", "branch", "vault-uuid", "canonical-vault-name"):
        configure.add_argument("--" + key)
    configure.add_argument("--confirm-sensitive-data-boundary", action="store_true")
    configure.add_argument("--interactive", action="store_true")
    configure.add_argument("--dry-run", action="store_true")
    _root(configure)

    for name, help_text in (("doctor", "diagnose owner resources"),
                            ("reconcile", "inspect incomplete local transactions")):
        command = commands.add_parser(name, help=help_text)
        _root(command)
    git = commands.add_parser("git", help="inspect Git boundaries")
    git_status = git.add_subparsers(dest="git_command", required=True).add_parser("status")
    git_status.add_argument("--repo", choices=("control", "vault", "both"), default="both")
    _root(git_status)
    plugins = commands.add_parser("plugins", help="diagnose the configured plugin profile")
    plugins_audit = plugins.add_subparsers(dest="plugins_command", required=True).add_parser("audit")
    plugins_audit.add_argument("--profile", choices=("mac",), default="mac")
    _root(plugins_audit)

    foundation = commands.add_parser("foundation", help="check portable foundations")
    foundation_commands = foundation.add_subparsers(dest="foundation_command", required=True)
    for name in ("source", "check"):
        _root(foundation_commands.add_parser(name))
    blueprint = commands.add_parser("blueprint", help="check the domain contract")
    _root(blueprint.add_subparsers(dest="blueprint_command", required=True).add_parser("validate"))
    schema = commands.add_parser("schema", help="maintain generated control artifacts")
    schema_export = schema.add_subparsers(dest="schema_command", required=True).add_parser("export")
    schema_export.add_argument("--check", action="store_true")
    _root(schema_export)
    vault_artifacts = commands.add_parser("vault-artifacts", help="check deployed system artifacts")
    _root(vault_artifacts.add_subparsers(dest="vault_artifacts_command", required=True).add_parser("check"))

    index = commands.add_parser("index", help="build and verify the search projection")
    index_commands = index.add_subparsers(dest="index_command", required=True)
    for name in ("build", "verify", "export"):
        command = index_commands.add_parser(name)
        if name != "verify":
            command.add_argument("--generation-id")
        _root(command)
    note = commands.add_parser("note", help="validate an Obsidian note")
    note_validate = note.add_subparsers(dest="note_command", required=True).add_parser("validate")
    note_validate.add_argument("--path", required=True)
    _root(note_validate)

    repair = commands.add_parser("repair", help="plan or apply an exact local repair")
    repair_commands = repair.add_subparsers(dest="repair_command", required=True)
    repair_plan = repair_commands.add_parser("plan")
    repair_plan.add_argument("--job-id")
    repair_plan.add_argument("--output")
    _root(repair_plan)
    repair_apply = repair_commands.add_parser("apply")
    repair_apply.add_argument("--plan-path", required=True)
    _root(repair_apply)
    receipts = commands.add_parser("receipts", help="verify transaction receipts")
    receipts_verify = receipts.add_subparsers(dest="receipts_command", required=True).add_parser("verify")
    receipts_verify.add_argument("--job-id")
    _root(receipts_verify)

    operation = commands.add_parser("operation", help="check, inspect, or recover owner State")
    operation_commands = operation.add_subparsers(dest="operation_command", required=True)
    for name in ("check", "status", "recover"):
        _root(operation_commands.add_parser(name))
    work = commands.add_parser("work", help="validate an admitted Work bundle")
    work_validate = work.add_subparsers(dest="work_command", required=True).add_parser("validate")
    work_validate.add_argument("--bundle", type=Path, required=True)
    _root(work_validate)
    storage = commands.add_parser("storage", help="inspect or apply an exact State transition")
    storage_commands = storage.add_subparsers(dest="storage_command", required=True)
    archive = storage_commands.add_parser("archive")
    archive.add_argument("--apply", action="store_true")
    archive.add_argument("--inventory-digest")
    archive.add_argument("--writer-evidence")
    _root(archive)
    cutover = storage_commands.add_parser("core-cutover")
    cutover.add_argument("--expected-sha256", required=True)
    cutover.add_argument("--apply", action="store_true")
    _root(cutover)
    return parser


def _dispatch(args: argparse.Namespace, roots: Any) -> tuple[dict[str, Any], int]:
    from ..application.knowledge import KnowledgeApplication

    command = args.command
    if command == "bootstrap":
        from ..bootstrap import bootstrap
        report = bootstrap(roots, dry_run=args.dry_run)
        return report, 0 if report.get("status") == "PASS" else 1
    if command == "configure":
        from ..configure import configure
        report = configure(roots, remote=args.remote, branch=args.branch,
                           vault_uuid=args.vault_uuid, canonical_vault_name=args.canonical_vault_name,
                           sensitive_data_confirmed=args.confirm_sensitive_data_boundary,
                           interactive=args.interactive, dry_run=args.dry_run)
        return report, 0 if report.get("status") == "PASS" else 1
    if command == "doctor":
        from ..diagnostics import doctor_report
        return doctor_report(roots)
    if command == "git":
        from ..diagnostics import git_status_report
        return git_status_report(roots, repo=args.repo)
    if command == "plugins":
        from ..diagnostics import plugins_audit_report
        return plugins_audit_report(roots, profile=args.profile)
    if command == "foundation":
        from ..foundation import check_foundation, check_source_manifest
        issues = (check_source_manifest if args.foundation_command == "source" else check_foundation)(roots.control)
        return {"status": "PASS" if not issues else "FAIL", "issues": issues}, 0 if not issues else 1
    if command == "blueprint":
        from ..blueprint import validate_blueprint
        result = validate_blueprint(roots.control)
        return result.report, result.exit_code
    if command == "schema":
        from ..schema_export import export_schema_artifacts
        result = export_schema_artifacts(roots.control, check=args.check)
        return result.report, result.exit_code
    if command == "vault-artifacts":
        from ..vault_artifacts import check_vault_artifacts
        result = check_vault_artifacts(roots)
        return result.report, result.exit_code
    if command == "index":
        from ..projection import build_index, export_jsonl, verify_projection
        if args.index_command == "verify":
            return verify_projection(roots)
        runner = build_index if args.index_command == "build" else export_jsonl
        return runner(roots, generation_id=args.generation_id)
    if command == "note":
        from ..diagnostics import EXIT_INPUT_INVALID, EXIT_VALIDATION_FAILED
        from ..note_engine import NoteEngine, resolve_vault_relative_path
        try:
            path = resolve_vault_relative_path(roots.vault, args.path)
            if path.is_symlink() or not path.is_file():
                raise ValueError("note must be a regular file inside the Vault")
        except (OSError, ValueError) as error:
            return {"status": "FAIL", "errors": [{"code": "NOTE_INPUT_INVALID",
                    "message": str(error)}]}, EXIT_INPUT_INVALID
        result = NoteEngine.from_root(roots.control).validate_text(args.path, path.read_text(encoding="utf-8"))
        return {"status": "PASS" if result.passed else "FAIL", "path": result.path,
                "errors": [asdict(issue) for issue in result.errors]}, 0 if result.passed else EXIT_VALIDATION_FAILED
    if command == "reconcile":
        from ..reconcile import reconcile_transactions
        return reconcile_transactions(roots)
    if command == "repair":
        from ..reconcile import apply_repair_plan, repair_plan
        if args.repair_command == "plan":
            return repair_plan(roots, job_id=args.job_id, output=args.output)
        return apply_repair_plan(roots, plan_path=args.plan_path)
    if command == "receipts":
        from ..reconcile import verify_receipts
        return verify_receipts(roots, job_id=args.job_id)
    if command == "storage" and args.storage_command == "core-cutover":
        from ..adapters.core_cutover import core_cutover
        report = core_cutover(roots, expected_sha256=args.expected_sha256, apply=args.apply)
        return report, 0 if report.get("status") == "PASS" else 1
    application = KnowledgeApplication.from_roots(roots)
    if command == "operation":
        return getattr(application, args.operation_command)()
    if command == "work":
        from ..application.work import validate_bundle_file
        application.guard("inspect")
        return validate_bundle_file(application.core, roots.control, args.bundle)
    if command == "storage":
        from ..adapters.legacy_archive import archive_runtime
        application.guard("storage")
        return archive_runtime(application, apply=args.apply, expected_digest=args.inventory_digest,
                               writer_evidence=args.writer_evidence)
    raise ValueError("unknown maintenance command")


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command is None:
        parser.print_help()
        return 0
    if args.command == "version":
        print(__version__)
        return 0
    try:
        report, code = _dispatch(args, resolve_paths(getattr(args, "root", None)))
    except (OSError, TypeError, ValueError) as error:
        report, code = {"status": "FAIL", "errors": [{"code": getattr(error, "code", "MAINTENANCE_INVALID"),
                                                        "message": str(error)}]}, 10
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return code


app = main

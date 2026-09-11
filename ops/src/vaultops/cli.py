"""KnowledgeOS command surface."""

from __future__ import annotations

import argparse
import json
import os
import sys
import tomllib
from collections.abc import Sequence
from pathlib import Path

from . import __version__
from .blueprint import validate_blueprint
from .bootstrap import bootstrap
from .configure import configure
from .foundation import check_foundation, check_source_manifest
from .note_engine import NoteEngine, UnsafePathError, resolve_vault_relative_path
from .runtime import RuntimeLayout
from .schema_export import export_schema_artifacts
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
        "bootstrap", help="add missing canonical directories and S05 templates"
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

    doctor = commands.add_parser("doctor", help="run read-only runtime diagnostics")
    doctor.add_argument("--root", type=Path, default=None, help="mounted control root")

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

    note = commands.add_parser("note", help="validate one Markdown note against the strict registry")
    note_commands = note.add_subparsers(dest="note_command", required=True)
    note_validate = note_commands.add_parser("validate", help="read and validate a Vault-relative Markdown note")
    note_validate.add_argument("path", type=Path, help="Vault-relative Markdown path")
    note_validate.add_argument("--root", type=Path, default=None, help="mounted control root")

    yaml_command = commands.add_parser("yaml", help="exercise the safe control YAML loader")
    yaml_command.add_argument("path", type=Path, help="UTF-8 YAML file")
    return parser


def _doctor(root: Path) -> int:
    ops_root = root / "ops"
    runtime_root = root / "runtime"
    config_path = ops_root / "vaultops.toml"
    checks: dict[str, object] = {
        "control_root": str(root),
        "runtime_root": str(runtime_root),
        "config_exists": config_path.is_file(),
        "runtime_problems": RuntimeLayout(runtime_root).check(),
    }
    if config_path.is_file():
        with config_path.open("rb") as handle:
            config = tomllib.load(handle)
        checks["config_schema_version"] = config.get("schema_version")
        checks["config_project_name"] = config.get("project_name")
    checks["status"] = "ok" if checks["config_exists"] and not checks["runtime_problems"] else "error"
    print(json.dumps(checks, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if checks["status"] == "ok" else 1


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
    if args.command == "doctor":
        return _doctor(args.root or _control_root())
    if args.command == "foundation":
        root = args.root or _control_root()
        if args.foundation_command == "source-check":
            problems = check_source_manifest(root)
            if problems:
                for problem in problems:
                    print(f"ERROR: {problem}", file=sys.stderr)
                return 1
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
            print("blueprint_json_schema=implemented:S02")
            print("cross_document_validation=implemented:S03A-S03B")
            print("generated_zero_diff=implemented:S03C-S04 (vaultctl schema export --check)")
            return 0
        result = validate_blueprint(args.root or _control_root())
        print(result.as_json(), end="")
        return result.exit_code
    if args.command == "schema":
        result = export_schema_artifacts(args.root or _control_root(), check=args.check)
        print(result.as_json(), end="")
        return result.exit_code
    if args.command == "note":
        root = (args.root or _control_root()).resolve()
        if args.note_command == "validate":
            try:
                relative = args.path.as_posix()
                note_path = resolve_vault_relative_path(root / "KnowledgeHub", relative)
                result = NoteEngine.from_root(root).validate_text(relative, note_path.read_text(encoding="utf-8"))
                print(json.dumps(result.as_dict(), ensure_ascii=False, indent=2, sort_keys=True))
                return 0 if result.passed else 1
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
                return 1
    if args.command == "yaml":
        value = load_yaml_file(args.path)
        print(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    parser.error(f"unsupported command: {args.command}")
    return 2


app = main

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
from .foundation import check_foundation, check_source_manifest
from .runtime import RuntimeLayout
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
            print("capability_profile=blueprint_semantic_gate2")
            print("blueprint_json_schema=implemented:S02")
            print("cross_document_validation=implemented:S03A-S03B")
            print("generated_zero_diff=deferred:S03C")
            return 0
        result = validate_blueprint(args.root or _control_root())
        print(result.as_json(), end="")
        return result.exit_code
    if args.command == "yaml":
        value = load_yaml_file(args.path)
        print(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    parser.error(f"unsupported command: {args.command}")
    return 2


app = main

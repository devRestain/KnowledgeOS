"""KnowledgeOS command surface."""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Sequence
from pathlib import Path

from . import __version__
from .action_proposals import ACTION_TYPES, generate_action_proposal
from .ai_projection import generate_ai_projection, verify_ai_projection
from .answer import answer_from_capture, ask
from .answer import evaluate_frozen_baseline as evaluate_answer_frozen_baseline
from .background import evaluate_frozen_baseline as evaluate_background_frozen_baseline
from .background import launchd_install, run_worker
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
from .gemma_routes import C35_ROUTES, run_gemma_job
from .local_commands import capture_text, capture_url, create_note, format_notes
from .note_engine import NoteEngine, UnsafePathError, resolve_vault_relative_path
from .ollama import OllamaClient, OllamaError, OllamaProfile, run_ollama_job
from .pipeline_registry import dispatch_user_action
from .projection import build_index, export_jsonl, verify_projection
from .proposals import apply_proposal, approve_proposal, reject_proposal, review_proposals
from .provider_broker import PIPELINES, SCENARIOS, run_synthetic_job
from .reconcile import apply_repair_plan, reconcile_transactions, repair_plan, verify_receipts
from .retrieval import (
    RetrievalValidationError,
    evaluate_frozen_baseline,
    read_query_file,
    retrieve,
    search,
)
from .schema_export import export_schema_artifacts
from .transactions import archive_project, finalize_capture, import_asset
from .triage import deterministic_triage
from .vector import evaluate_vector_baseline, vector_retrieve, vector_search
from .workflows import create_period_note, create_project_bundle
from .yaml_safe import load_yaml_file


def _control_root() -> Path:
    return Path(os.environ.get("KNOWLEDGEOS_CONTROL_ROOT", "/workspace/control"))


def _add_retrieval_arguments(
    command: argparse.ArgumentParser,
    *,
    default_hops: int,
    allow_evaluation: bool = False,
) -> None:
    query_input = command.add_mutually_exclusive_group(required=True)
    query_input.add_argument(
        "--query-stdin",
        action="store_true",
        help="read one logical UTF-8 query line from stdin",
    )
    query_input.add_argument(
        "--query-file",
        type=Path,
        help="read one logical UTF-8 query line from a regular file",
    )
    if allow_evaluation:
        query_input.add_argument(
            "--evaluation-file",
            type=Path,
            help="evaluate the checked-in C22 frozen baseline instead of one query",
        )
    command.add_argument("--scope", default=None, help="optional project or scope filter")
    command.add_argument("--path-prefix", default=None, help="optional Vault-relative path prefix")
    command.add_argument(
        "--include-type",
        dest="include_types",
        action="append",
        default=None,
        help="include one note type; repeat for a custom corpus",
    )
    command.add_argument("--include-review", action="store_true", help="include only the separate pending/conflict review corpus")
    command.add_argument("--limit", type=int, default=10, help="maximum number of candidates from 1 through 50")
    command.add_argument(
        "--hops",
        type=int,
        choices=(0, 1, 2),
        default=default_hops,
        help="bounded typed-link expansion hops",
    )
    command.add_argument(
        "--generation-id",
        dest="expected_generation_id",
        default=None,
        help="require the current pointer to select this immutable generation",
    )
    command.add_argument(
        "--vector",
        action="store_true",
        help="explicitly enable the local E01 vector and RRF overlay",
    )
    command.add_argument("--root", type=Path, default=None, help="mounted control root")


def _add_action_proposal_arguments(
    command: argparse.ArgumentParser,
    *,
    required_source: bool,
) -> None:
    command.add_argument("--source", "--source-path", dest="source", required=required_source)
    command.add_argument("--expected-sha256", "--source-sha256", dest="expected_sha256", required=required_source)
    command.add_argument("--locator", default=None, help="daily fragment locator")
    command.add_argument("--fragment-sha256", default=None, help="daily fragment SHA-256")
    command.add_argument("--target-path", "--target", dest="target_path", default=None)
    command.add_argument("--target-type", default=None, choices=("idea", "question", "knowledge", "project", "source"))
    command.add_argument("--title", default=None, help="validated target title or draft candidate title")
    command.add_argument("--selected-candidate-id", default=None)
    command.add_argument("--selected-candidate-sha256", default=None)
    command.add_argument("--candidate-set-file", type=Path, default=None)
    command.add_argument("--retrieval-profile-id", default=None)
    command.add_argument("--root", type=Path, default=None, help="mounted control root")


def _retrieval_query(args: argparse.Namespace) -> str:
    if args.query_stdin:
        return sys.stdin.read()
    return read_query_file(args.query_file)


def _answer_query(args: argparse.Namespace) -> str:
    if args.question_stdin:
        return sys.stdin.read()
    return read_query_file(args.question_file)


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

    export = commands.add_parser("export", help="publish deterministic runtime projections")
    export_commands = export.add_subparsers(dest="export_command", required=True)
    export_jsonl_command = export_commands.add_parser(
        "jsonl",
        help="project validated Vault notes and canonical edges into one generation",
    )
    export_jsonl_command.add_argument("--generation-id", default=None, help="optional immutable generation identifier")
    export_jsonl_command.add_argument("--root", type=Path, default=None, help="mounted control root")

    index = commands.add_parser("index", help="build and verify the deterministic projection index")
    index_commands = index.add_subparsers(dest="index_command", required=True)
    index_build = index_commands.add_parser("build", help="build and atomically publish one projection generation")
    index_build.add_argument("--generation-id", default=None, help="optional immutable generation identifier")
    index_build.add_argument("--root", type=Path, default=None, help="mounted control root")
    index_verify = index_commands.add_parser("verify", help="verify the current pointer and selected generation")
    index_verify.add_argument(
        "--no-source-check",
        action="store_true",
        help="verify generated files without comparing the source snapshot",
    )
    index_verify.add_argument("--root", type=Path, default=None, help="mounted control root")

    search_command = commands.add_parser("search", help="run provider-free lexical retrieval")
    _add_retrieval_arguments(search_command, default_hops=0)

    retrieve_command = commands.add_parser(
        "retrieve",
        help="run provider-free lexical retrieval and bounded typed-link expansion",
    )
    _add_retrieval_arguments(retrieve_command, default_hops=1, allow_evaluation=True)

    vector = commands.add_parser(
        "vector",
        help="run the explicit local E01 vector and RRF evaluation overlay",
    )
    vector_commands = vector.add_subparsers(dest="vector_command", required=True)
    vector_search_command = vector_commands.add_parser(
        "search",
        help="run local vector-only retrieval over one verified generation",
    )
    _add_retrieval_arguments(vector_search_command, default_hops=0)
    vector_retrieve_command = vector_commands.add_parser(
        "retrieve",
        help="run local vector/RRF retrieval and bounded typed-link expansion",
    )
    _add_retrieval_arguments(vector_retrieve_command, default_hops=1)
    vector_evaluate_command = vector_commands.add_parser(
        "evaluate",
        help="evaluate local vector and RRF against the frozen E01 baseline",
    )
    vector_evaluate_command.add_argument("--evaluation-file", type=Path, default=None)
    vector_evaluate_command.add_argument(
        "--generation-id",
        dest="expected_generation_id",
        default=None,
        help="require the current pointer to select this immutable generation",
    )
    vector_evaluate_command.add_argument("--root", type=Path, default=None, help="mounted control root")

    ask_command = commands.add_parser(
        "ask",
        help="return one deterministic provider-free cited answer",
    )
    answer_input = ask_command.add_mutually_exclusive_group(required=True)
    answer_input.add_argument(
        "--question-stdin",
        "--query-stdin",
        dest="question_stdin",
        action="store_true",
        help="read one logical UTF-8 question from stdin",
    )
    answer_input.add_argument(
        "--question-file",
        "--query-file",
        dest="question_file",
        type=Path,
        help="read one logical UTF-8 question from a regular file",
    )
    answer_input.add_argument(
        "--source",
        "--source-path",
        dest="source_path",
        help="answer the body of one hash-bound Vault capture note",
    )
    answer_input.add_argument(
        "--evaluation-file",
        type=Path,
        help="evaluate the checked-in C23 frozen answer baseline",
    )
    ask_command.add_argument(
        "--expected-sha256",
        "--source-sha256",
        dest="expected_sha256",
        default=None,
        help="required lowercase SHA-256 for --source",
    )
    ask_command.add_argument("--scope", default=None, help="optional project or scope filter")
    ask_command.add_argument("--path-prefix", default=None, help="optional Vault-relative path prefix")
    ask_command.add_argument(
        "--include-type",
        dest="include_types",
        action="append",
        default=None,
        help="include one note type; repeat for a custom corpus",
    )
    ask_command.add_argument(
        "--include-review",
        action="store_true",
        help="include only the separate pending/conflict review corpus",
    )
    ask_command.add_argument("--limit", type=int, default=5, help="maximum number of cited candidates from 1 through 50")
    ask_command.add_argument(
        "--hops",
        type=int,
        choices=(0, 1, 2),
        default=1,
        help="bounded typed-link expansion hops",
    )
    ask_command.add_argument(
        "--generation-id",
        dest="expected_generation_id",
        default=None,
        help="require the current pointer to select this immutable generation",
    )
    ask_command.add_argument("--root", type=Path, default=None, help="mounted control root")

    ai = commands.add_parser("ai", help="proposal-only deterministic AI contract commands")
    ai_commands = ai.add_subparsers(dest="ai_command", required=True)
    ai_triage = ai_commands.add_parser("triage", help="read one note and return a non-mutating triage proposal")
    ai_triage.add_argument("--source", required=True, help="Vault-relative capture or daily note path")
    ai_triage.add_argument("--expected-sha256", required=True, help="lowercase SHA-256 of source bytes")
    ai_triage.add_argument("--locator", default=None, help="required daily fragment locator")
    ai_triage.add_argument("--fragment-sha256", default=None, help="required daily fragment SHA-256")
    ai_triage.add_argument("--root", type=Path, default=None, help="mounted control root")
    ai_propose = ai_commands.add_parser("propose", help="create one deterministic C27 Pending proposal")
    ai_propose.add_argument("--action", required=True, choices=ACTION_TYPES)
    _add_action_proposal_arguments(ai_propose, required_source=True)
    for action_command, help_text in (
        ("draft-note", "create one C27 draft_note proposal"),
        ("link-suggestions", "create one C27 link_suggestions proposal"),
        ("normalize", "create one C27 normalize proposal"),
    ):
        direct_action = ai_commands.add_parser(action_command, help=help_text)
        _add_action_proposal_arguments(direct_action, required_source=True)
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
    ai_projection = ai_commands.add_parser(
        "projection",
        help="build or verify a privacy-minimized local or remote AI projection",
    )
    projection_commands = ai_projection.add_subparsers(dest="projection_command", required=True)
    ai_projection_build = projection_commands.add_parser(
        "build",
        help="publish one immutable profile-scoped AI projection generation",
    )
    ai_projection_build.add_argument("--profile", choices=("local", "remote"), required=True)
    ai_projection_build.add_argument("--generation-id", default=None)
    ai_projection_build.add_argument("--root", type=Path, default=None, help="mounted control root")
    ai_projection_verify = projection_commands.add_parser(
        "verify",
        help="verify one profile pointer and its privacy-filtered generation",
    )
    ai_projection_verify.add_argument("--profile", choices=("local", "remote"), required=True)
    ai_projection_verify.add_argument(
        "--no-source-check",
        action="store_true",
        help="verify generated bytes without recomputing the current Vault source snapshot",
    )
    ai_projection_verify.add_argument("--root", type=Path, default=None, help="mounted control root")
    for route in ("organize", "summarize", "relate", "extract", "inbox", "project-summary"):
        facade = ai_commands.add_parser(
            route,
            help="resolve one provider-free user action into a read-only dispatch plan",
        )
        _add_action_proposal_arguments(facade, required_source=False)
    ai_worker = ai_commands.add_parser(
        "worker",
        help="run one provider-free background wake pass or an explicit local watch",
    )
    worker_mode = ai_worker.add_mutually_exclusive_group()
    worker_mode.add_argument("--once", dest="once", action="store_true", default=True)
    worker_mode.add_argument("--watch", dest="once", action="store_false")
    ai_worker.add_argument("--max-cycles", type=int, default=None)
    ai_worker.add_argument("--wake-id", default=None)
    ai_worker.add_argument(
        "--dry-run",
        action="store_true",
        help="inspect requests and recovery without creating queue manifests",
    )
    ai_worker.add_argument("--evaluation-file", type=Path, default=None)
    ai_worker.add_argument("--root", type=Path, default=None, help="mounted control root")
    ai_broker = ai_commands.add_parser(
        "broker",
        help="run one C32 synthetic provider job from an immutable runtime request",
    )
    ai_broker.add_argument("--job-id", required=True, help="UUIDv4 of an existing C31 runtime job")
    ai_broker.add_argument("--pipeline", choices=PIPELINES, default=None)
    ai_broker.add_argument("--scenario", choices=SCENARIOS, default="success")
    ai_broker.add_argument("--root", type=Path, default=None, help="mounted control root")
    ai_ollama = ai_commands.add_parser(
        "ollama",
        help="run one C33 provider job through an explicit loopback Ollama profile",
    )
    ai_ollama.add_argument("--job-id", required=True, help="UUIDv4 of an existing C31 runtime job")
    ai_ollama.add_argument("--base-url", required=True, help="explicit http://127.0.0.1:<port> fake or local endpoint")
    ai_ollama.add_argument("--pipeline", choices=PIPELINES, default=None)
    ai_ollama.add_argument("--root", type=Path, default=None, help="mounted control root")
    ai_gemma = ai_commands.add_parser(
        "gemma",
        help="validate one recorded Gemma 4 route response without live provider access",
    )
    ai_gemma.add_argument("--job-id", required=True, help="UUIDv4 of an existing C31 runtime job")
    ai_gemma.add_argument(
        "--recorded-response",
        "--response-file",
        dest="recorded_response",
        type=Path,
        required=True,
        help="control-root-relative Ollama-shaped JSON response fixture",
    )
    ai_gemma.add_argument(
        "--route",
        choices=tuple(C35_ROUTES) + ("cited-answer", "draft", "draft-note", "link", "link-suggestions"),
        default=None,
        help="optional C35 route alias; otherwise use the C31 action",
    )
    ai_gemma.add_argument("--root", type=Path, default=None, help="mounted control root")

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

    launchd = commands.add_parser(
        "launchd",
        help="preview the inactive C24 LaunchAgent artifact",
    )
    launchd_commands = launchd.add_subparsers(dest="launchd_command", required=True)
    launchd_install_command = launchd_commands.add_parser(
        "install",
        help="preview E03 installation; C24 refuses activation",
    )
    launchd_install_command.add_argument(
        "--dry-run",
        action="store_true",
        help="preview without activation",
    )
    launchd_install_command.add_argument("--root", type=Path, default=None, help="mounted control root")

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
    if args.command == "launchd" and args.launchd_command == "install":
        report, exit_code = launchd_install(
            args.root or _control_root(),
            dry_run=args.dry_run,
        )
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
    if args.command == "export" and args.export_command == "jsonl":
        report, exit_code = export_jsonl(
            args.root or _control_root(),
            generation_id=args.generation_id,
        )
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
        return exit_code
    if args.command == "index" and args.index_command == "build":
        report, exit_code = build_index(
            args.root or _control_root(),
            generation_id=args.generation_id,
        )
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
        return exit_code
    if args.command == "index" and args.index_command == "verify":
        report, exit_code = verify_projection(
            args.root or _control_root(),
            verify_sources=not args.no_source_check,
        )
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
        return exit_code
    if args.command in {"search", "retrieve"}:
        root = args.root or _control_root()
        evaluation_file = getattr(args, "evaluation_file", None)
        if evaluation_file is not None:
            if args.vector:
                report, exit_code = evaluate_vector_baseline(
                    root,
                    baseline_path=evaluation_file,
                    expected_generation_id=args.expected_generation_id,
                )
            else:
                report, exit_code = evaluate_frozen_baseline(
                    root,
                    baseline_path=evaluation_file,
                    expected_generation_id=args.expected_generation_id,
                )
        else:
            try:
                query = _retrieval_query(args)
            except (OSError, UnicodeError, RetrievalValidationError, TypeError, ValueError) as error:
                report = {
                    "status": "FAIL",
                    "operation": args.command,
                    "capability": "C22",
                    "provider_called": False,
                    "mutation_performed": False,
                    "errors": [{"code": "RETRIEVAL_INPUT_INVALID", "message": str(error)}],
                }
                exit_code = EXIT_INPUT_INVALID
            else:
                runner = search if args.command == "search" else retrieve
                report, exit_code = runner(
                    root,
                    query,
                    scope=args.scope,
                    path_prefix=args.path_prefix,
                    include_types=args.include_types,
                    include_review=args.include_review,
                    limit=args.limit,
                    hops=args.hops,
                    expected_generation_id=args.expected_generation_id,
                    use_vector=args.vector,
                )
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
        return exit_code
    if args.command == "vector":
        root = args.root or _control_root()
        if args.vector_command == "evaluate":
            report, exit_code = evaluate_vector_baseline(
                root,
                baseline_path=args.evaluation_file,
                expected_generation_id=args.expected_generation_id,
            )
        else:
            try:
                query = _retrieval_query(args)
            except (OSError, UnicodeError, RetrievalValidationError, TypeError, ValueError) as error:
                report = {
                    "status": "FAIL",
                    "operation": f"vector {args.vector_command}",
                    "capability": "E01",
                    "vector_enabled": True,
                    "provider_called": False,
                    "mutation_performed": False,
                    "errors": [{"code": "E01_INPUT_INVALID", "message": str(error)}],
                }
                exit_code = 10
            else:
                runner = vector_search if args.vector_command == "search" else vector_retrieve
                report, exit_code = runner(
                    root,
                    query,
                    scope=args.scope,
                    path_prefix=args.path_prefix,
                    include_types=args.include_types,
                    include_review=args.include_review,
                    limit=args.limit,
                    hops=args.hops,
                    expected_generation_id=args.expected_generation_id,
                )
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
        return exit_code
    if args.command == "ask":
        root = args.root or _control_root()
        if args.evaluation_file is not None:
            report, exit_code = evaluate_answer_frozen_baseline(
                root,
                baseline_path=args.evaluation_file,
                expected_generation_id=args.expected_generation_id,
            )
        elif args.source_path is not None:
            report, exit_code = answer_from_capture(
                root,
                source_path=args.source_path,
                expected_sha256=args.expected_sha256,
                scope=args.scope,
                path_prefix=args.path_prefix,
                include_types=args.include_types,
                include_review=args.include_review,
                limit=args.limit,
                hops=args.hops,
                expected_generation_id=args.expected_generation_id,
            )
        else:
            try:
                query = _answer_query(args)
            except (OSError, UnicodeError, RetrievalValidationError, TypeError, ValueError) as error:
                report = {
                    "status": "FAIL",
                    "operation": "ask",
                    "capability": "C23",
                    "provider_called": False,
                    "mutation_performed": False,
                    "errors": [{"code": "ANSWER_INPUT_INVALID", "message": str(error)}],
                }
                exit_code = EXIT_INPUT_INVALID
            else:
                report, exit_code = ask(
                    root,
                    query,
                    scope=args.scope,
                    path_prefix=args.path_prefix,
                    include_types=args.include_types,
                    include_review=args.include_review,
                    limit=args.limit,
                    hops=args.hops,
                    expected_generation_id=args.expected_generation_id,
                )
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
        return exit_code
    if args.command == "ai" and args.ai_command == "broker":
        report, exit_code = run_synthetic_job(
            args.root or _control_root(),
            job_id=args.job_id,
            pipeline=args.pipeline,
            scenario=args.scenario,
        )
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
        return exit_code
    if args.command == "ai" and args.ai_command == "ollama":
        try:
            client = OllamaClient(OllamaProfile(base_url=args.base_url))
            report, exit_code = run_ollama_job(
                args.root or _control_root(),
                job_id=args.job_id,
                client=client,
                pipeline=args.pipeline,
            )
        except (OllamaError, OSError, TypeError, ValueError) as error:
            report = {
                "status": "FAIL",
                "operation": "ai ollama",
                "capability": "C33",
                "provider_called": False,
                "live_provider_called": False,
                "synthetic_provider_called": False,
                "mutation_performed": False,
                "vault_mutation_performed": False,
                "canonical_apply_allowed": False,
                "errors": [{"code": getattr(error, "code", "C33_INPUT_INVALID"), "message": str(error)}],
            }
            exit_code = EXIT_INPUT_INVALID
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
        return exit_code
    if args.command == "ai" and args.ai_command == "gemma":
        report, exit_code = run_gemma_job(
            args.root or _control_root(),
            job_id=args.job_id,
            recorded_response_path=args.recorded_response,
            route=args.route,
        )
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
        return exit_code
    if args.command == "ai" and args.ai_command == "worker":
        root = args.root or _control_root()
        if args.evaluation_file is not None:
            report, exit_code = evaluate_background_frozen_baseline(
                root,
                baseline_path=args.evaluation_file,
            )
        else:
            report, exit_code = run_worker(
                root,
                once=args.once,
                max_cycles=args.max_cycles,
                wake_id=args.wake_id,
                dry_run=args.dry_run,
            )
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
        return exit_code
    if args.command == "ai" and args.ai_command == "projection":
        root = args.root or _control_root()
        if args.projection_command == "build":
            report, exit_code = generate_ai_projection(
                root,
                profile=args.profile,
                generation_id=args.generation_id,
            )
        else:
            report, exit_code = verify_ai_projection(
                root,
                profile=args.profile,
                verify_sources=not args.no_source_check,
            )
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
        return exit_code
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
    if args.command == "ai" and args.ai_command in {
        "propose",
        "draft-note",
        "link-suggestions",
        "normalize",
    }:
        action = {
            "propose": args.action,
            "draft-note": "draft_note",
            "link-suggestions": "link_suggestions",
            "normalize": "normalize",
        }[args.ai_command]
        report, exit_code = generate_action_proposal(
            args.root or _control_root(),
            action=action,
            source_path=args.source,
            expected_sha256=args.expected_sha256,
            locator=args.locator,
            fragment_sha256=args.fragment_sha256,
            target_path=args.target_path,
            target_type=args.target_type,
            title=args.title,
            selected_candidate_id=args.selected_candidate_id,
            selected_candidate_sha256=args.selected_candidate_sha256,
            candidate_set_file=args.candidate_set_file,
            retrieval_profile_id=args.retrieval_profile_id,
        )
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
        return exit_code
    if args.command == "ai" and args.ai_command in {
        "organize",
        "summarize",
        "relate",
        "extract",
        "inbox",
        "project-summary",
    }:
        report, exit_code = dispatch_user_action(
            args.root or _control_root(),
            args.ai_command,
            source_path=args.source,
            expected_sha256=args.expected_sha256,
            locator=args.locator,
            fragment_sha256=args.fragment_sha256,
            target_path=args.target_path,
            target_type=args.target_type,
            title=args.title,
            selected_candidate_id=args.selected_candidate_id,
            selected_candidate_sha256=args.selected_candidate_sha256,
            candidate_set_file=args.candidate_set_file,
            retrieval_profile_id=args.retrieval_profile_id,
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

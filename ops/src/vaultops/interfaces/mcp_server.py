"""Minimal stdio MCP adapter with a server-selected KnowledgeOS context."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import uuid
from collections.abc import Callable, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass
from importlib.metadata import version
from pathlib import Path
from typing import Any
from urllib.parse import unquote

from jsonschema import Draft202012Validator
from mcp import types
from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server
from mcp.shared.exceptions import MCPError
from referencing import Registry, Resource

from ..adapters.core import AdmissionError
from ..application.knowledge import KnowledgeApplication, LocalIdentity
from ..application.mcp_effects import submit_pending
from ..application.read_tools import KnowledgeReadTools
from ..application.work_tools import EvalToolSession, OfficerToolSession, TeamToolSession
from ..paths import ResolvedPaths, RootResolutionError, resolve_paths

KnowledgeServices = KnowledgeApplication

_TOOL_NAMES = (
    "knowledge_search",
    "knowledge_retrieve",
    "proposal_inspect",
    "proposal_create",
    "knowledge_read",
    "artifact_read",
    "base_view_query",
    "link_context",
    "task_query",
    "work_context_read",
    "method_read",
    "graph_segment_propose",
    "artifact_submit",
    "assessment_submit",
    "proposal_draft_note",
    "citation_verify",
    "proposal_link_suggestions",
    "semantic_registry_read",
    "proposal_alignment",
    "proposal_project_review",
    "gateway_evidence_read",
    "proposal_adaptation",
    "vault_diagnose",
    "proposal_recovery_plan",
)
_TOOL_DESCRIPTIONS = {
    "knowledge_search": "Search the configured KnowledgeHub using bounded read-only filters.",
    "knowledge_retrieve": "Retrieve bounded evidence references from the configured KnowledgeHub.",
    "proposal_inspect": "Inspect one exact Pending proposal without returning its body.",
    "proposal_create": "Create or replay one digest-bound normalize proposal for human review.",
}
_OUTPUT_BRANCHES = {
    "knowledge_search": "search",
    "knowledge_retrieve": "retrieve",
    "proposal_inspect": "inspect",
    "proposal_create": "create",
    **{name: "extended" for name in _TOOL_NAMES[4:]},
}
_WORK_ONLY = frozenset({"work_context_read", "method_read", "graph_segment_propose",
                        "artifact_submit", "assessment_submit"})
_PROPOSAL_WRITES = frozenset({"proposal_create", "proposal_draft_note",
                              "proposal_link_suggestions", "proposal_alignment",
                              "proposal_project_review", "proposal_adaptation",
                              "proposal_recovery_plan", "graph_segment_propose",
                              "artifact_submit", "assessment_submit"})
_ALLOWED_TYPES = frozenset(
    {"knowledge", "source", "project", "project_note", "artifact", "idea", "question"}
)
_BACKGROUND_TASKS: set[asyncio.Task[Any]] = set()


@dataclass(frozen=True, slots=True)
class ServerContext:
    """Immutable startup authority shared by protocol callbacks."""

    roots: ResolvedPaths
    services: KnowledgeServices
    tools: tuple[types.Tool, ...]
    input_validators: Mapping[str, Draft202012Validator]
    result_validator: Draft202012Validator
    limits: Mapping[str, int]
    session: TeamToolSession | OfficerToolSession | EvalToolSession | None = None


def _schema_root() -> Path:
    return Path(__file__).resolve().parents[3] / "schemas" / "mcp"


def _load_context(control_root: Path, *, work_run_id: str | None = None,
                  executor_id: str | None = None, graph_run_id: str | None = None) -> ServerContext:
    if not control_root.is_absolute():
        raise ValueError("control root must be absolute")
    roots = resolve_paths(control_root, environment={})
    schema_root = _schema_root()
    manifest = json.loads((schema_root / "capabilities.json").read_text(encoding="utf-8"))
    sdk = manifest.get("sdk", {})
    if sdk.get("distribution") != "mcp" or version("mcp") != sdk.get("exact_version"):
        raise ValueError("pinned MCP SDK version does not match the capability manifest")
    definitions = manifest.get("tools")
    if not isinstance(definitions, list) or tuple(item.get("name") for item in definitions) != _TOOL_NAMES:
        raise ValueError("MCP capability allowlist is invalid")
    tools: list[types.Tool] = []
    input_validators: dict[str, Draft202012Validator] = {}
    registry: Registry[Any] = Registry()
    schema_values: list[dict[str, Any]] = []
    schema_files: dict[str, Path] = {}
    for path in schema_root.glob("*.schema.json"):
        schema = json.loads(path.read_text(encoding="utf-8"))
        Draft202012Validator.check_schema(schema)
        schema_values.append(schema)
        schema_files[path.name] = path
        registry = registry.with_resource(schema["$id"], Resource.from_contents(schema))
    result_schema_path = schema_root / "tool_result.schema.json"
    result_schema = json.loads(result_schema_path.read_text(encoding="utf-8"))
    for definition in definitions:
        input_schema = (json.loads((schema_root / definition["input_schema"]).read_text(encoding="utf-8"))
                        if isinstance(definition["input_schema"], str) else definition["input_schema"])
        Draft202012Validator.check_schema(input_schema)
        input_validators[definition["name"]] = Draft202012Validator(input_schema)
        output_schema = _inline_schema(
            result_schema["$defs"][_OUTPUT_BRANCHES[definition["name"]]],
            result_schema_path,
            schema_root,
            schema_files,
        )
        output_schema["$schema"] = result_schema["$schema"]
        output_schema["type"] = "object"
        tools.append(
            types.Tool(
                name=definition["name"],
                description=definition.get("description", _TOOL_DESCRIPTIONS.get(definition["name"], definition["name"])),
                inputSchema=input_schema,
                outputSchema=output_schema,
            )
        )
    services = KnowledgeApplication.from_roots(
        roots, identity=LocalIdentity("local.mcp", "mcp", local_execution=work_run_id is not None))
    if (work_run_id is None) != (executor_id is None):
        raise ValueError("Work and executor startup identities must be supplied together")
    session: TeamToolSession | OfficerToolSession | EvalToolSession | None = None
    if work_run_id is not None and executor_id is not None:
        record = services.journal.read()["intents"].get("exops.work." + work_run_id)
        if not isinstance(record, dict):
            raise ValueError("Owner WorkRun startup binding is unavailable")
        if executor_id == record["spec"]["eval_officer_executor_id"]:
            if graph_run_id is not None:
                raise ValueError("EvalOfficer startup cannot select a GraphRun")
            session = EvalToolSession(services, work_run_id, executor_id)
        elif record["spec"]["target_kind"] == "officer":
            if graph_run_id is None:
                raise ValueError("Officer startup requires an active GraphRun")
            session = OfficerToolSession(services, work_run_id, executor_id, graph_run_id)
        else:
            session = TeamToolSession(services, work_run_id, executor_id, graph_run_id)
        session.tools()
    return ServerContext(
        roots=roots,
        services=services,
        tools=tuple(tools),
        input_validators=input_validators,
        result_validator=Draft202012Validator(
            next(schema for schema in schema_values if schema["$id"].endswith("tool_result.schema.json")),
            registry=registry,
        ),
        limits=manifest["limits"],
        session=session,
    )


def _inline_schema(
    value: Any,
    source_path: Path,
    schema_root: Path,
    schema_files: Mapping[str, Path],
) -> Any:
    if isinstance(value, list):
        return [_inline_schema(item, source_path, schema_root, schema_files) for item in value]
    if not isinstance(value, dict):
        return value
    if "$ref" in value:
        reference = value["$ref"]
        target_path, fragment = reference.split("#", 1) if "#" in reference else (reference, "")
        resolved_path = source_path if not target_path else schema_files.get(Path(target_path).name)
        if resolved_path is None or resolved_path.parent != schema_root:
            raise ValueError("MCP schema reference is outside the local schema bundle")
        target = json.loads(resolved_path.read_text(encoding="utf-8"))
        if fragment:
            for token in fragment.lstrip("/").split("/"):
                target = target[unquote(token.replace("~1", "/").replace("~0", "~"))]
        expanded = _inline_schema(target, resolved_path, schema_root, schema_files)
        siblings = {key: item for key, item in value.items() if key != "$ref"}
        if not siblings:
            return expanded
        return {
            "allOf": [
                expanded,
                _inline_schema(siblings, source_path, schema_root, schema_files),
            ]
        }
    return {
        key: _inline_schema(item, source_path, schema_root, schema_files)
        for key, item in value.items()
    }


def _create_server(context: ServerContext) -> Server[ServerContext]:
    active_calls = asyncio.Semaphore(context.limits["concurrent_calls"])
    proposal_writer = asyncio.Lock()

    @asynccontextmanager
    async def lifespan(_server: Server[ServerContext]):
        yield context

    async def list_tools(
        _request_context: Any,
        _params: types.PaginatedRequestParams | None,
    ) -> types.ListToolsResult:
        return types.ListToolsResult(tools=list(context.tools))

    async def call_tool(
        _request_context: Any,
        params: types.CallToolRequestParams,
    ) -> types.CallToolResult:
        if params.name not in _TOOL_NAMES:
            raise MCPError(-32602, "Unsupported tool")
        arguments = params.arguments if isinstance(params.arguments, dict) else {}
        input_error = _validate_input(context, params.name, arguments)
        if input_error is not None:
            return _mcp_result(context, input_error, is_error=True)
        if context.session is None and params.name in _WORK_ONLY:
            return _mcp_result(context, _error_envelope(
                params.name, "SCOPE_DENIED", "An admitted Work binding is required.",
                status="denied", effect="none"), is_error=True)
        if context.session is not None:
            try:
                context.session.authorize(params.name, arguments)
            except AdmissionError:
                return _mcp_result(context, _error_envelope(
                    params.name, "SCOPE_DENIED", "Owner scope denied this call.",
                    status="denied", effect="none"), is_error=True)

        operation = _operation(context.services, params.name, arguments, session=context.session)
        is_proposal_write = params.name in _PROPOSAL_WRITES
        try:
            report = await _run_bounded(
                operation,
                semaphore=active_calls,
                writer=proposal_writer if is_proposal_write else None,
                timeout=context.limits[
                    "proposal_timeout_seconds" if is_proposal_write else "read_timeout_seconds"
                ],
            )
        except _OperationTimeout as timeout_error:
            effect_uncertain = is_proposal_write and timeout_error.effect_uncertain
            timeout_result = _error_envelope(
                params.name,
                "PERSISTENCE_ERROR" if effect_uncertain else "UNAVAILABLE",
                (
                    "The Pending proposal outcome could not be confirmed."
                    if effect_uncertain
                    else "The operation could not start within its fixed time budget."
                ),
                status="error" if effect_uncertain else "unavailable",
                effect="unknown" if effect_uncertain else "none",
                retryable=not effect_uncertain,
            )
            return _mcp_result(context, timeout_result, is_error=True)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - never expose an internal service exception over MCP
            return _mcp_result(
                context,
                _error_envelope(
                    params.name,
                    "INTERNAL_ERROR",
                    "The operation could not be completed.",
                    status="error",
                    effect="unknown" if is_proposal_write else "none",
                ),
                is_error=True,
            )

        envelope, is_error = _convert_report(context, params.name, arguments, report)
        return _mcp_result(context, envelope, is_error=is_error)

    return Server(
        "vaultmcp",
        version="0.1.0",
        lifespan=lifespan,
        on_list_tools=list_tools,
        on_call_tool=call_tool,
    )


class _OperationTimeout(Exception):
    """A local service call exceeded its fixed request budget."""

    def __init__(self, *, effect_uncertain: bool):
        super().__init__("operation exceeded its fixed time budget")
        self.effect_uncertain = effect_uncertain


def _defer_release(
    task: asyncio.Task[Any],
    semaphore: asyncio.Semaphore,
    writer: asyncio.Lock | None,
) -> None:
    async def release_after_operation() -> None:
        try:
            await task
        finally:
            if writer is not None and writer.locked():
                writer.release()
            semaphore.release()

    cleanup = asyncio.create_task(release_after_operation())
    _BACKGROUND_TASKS.add(cleanup)
    cleanup.add_done_callback(_consume_task_failure)
    cleanup.add_done_callback(_BACKGROUND_TASKS.discard)


def _consume_task_failure(task: asyncio.Task[Any]) -> None:
    if not task.cancelled():
        task.exception()


async def _run_bounded(
    operation: Callable[[], tuple[dict[str, Any], int]],
    *,
    semaphore: asyncio.Semaphore,
    writer: asyncio.Lock | None,
    timeout: int,
) -> tuple[dict[str, Any], int]:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout

    def remaining() -> float:
        return max(0.001, deadline - loop.time())

    try:
        await asyncio.wait_for(semaphore.acquire(), timeout=remaining())
    except TimeoutError as error:
        raise _OperationTimeout(effect_uncertain=False) from error
    writer_acquired = False
    if writer is not None:
        try:
            await asyncio.wait_for(writer.acquire(), timeout=remaining())
            writer_acquired = True
        except TimeoutError as error:
            semaphore.release()
            raise _OperationTimeout(effect_uncertain=False) from error
        except BaseException:
            semaphore.release()
            raise

    task = asyncio.create_task(asyncio.to_thread(operation))
    try:
        result = await asyncio.wait_for(asyncio.shield(task), timeout=remaining())
    except TimeoutError as error:
        _defer_release(task, semaphore, writer if writer_acquired else None)
        raise _OperationTimeout(effect_uncertain=True) from error
    except asyncio.CancelledError:
        _defer_release(task, semaphore, writer if writer_acquired else None)
        raise
    except BaseException:
        if writer_acquired and writer is not None:
            writer.release()
        semaphore.release()
        raise
    if writer_acquired and writer is not None:
        writer.release()
    semaphore.release()
    return result


def _validate_input(
    context: ServerContext,
    name: str,
    arguments: dict[str, Any],
) -> dict[str, Any] | None:
    try:
        encoded = json.dumps(
            arguments,
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeError):
        return _error_envelope(
            name,
            "INVALID_ARGUMENT",
            "The request does not match the capability schema.",
            status="denied",
            effect="none",
        )
    if len(encoded) > context.limits["request_utf8_bytes"]:
        return _error_envelope(
            name,
            "INVALID_ARGUMENT",
            "The request exceeds the capability byte limit.",
            status="denied",
            effect="none",
        )
    if list(context.input_validators[name].iter_errors(arguments)):
        return _error_envelope(
            name,
            "INVALID_ARGUMENT",
            "The request does not match the capability schema.",
            status="denied",
            effect="none",
        )
    include_types = arguments.get("include_types")
    if include_types is not None and not set(include_types).issubset(_ALLOWED_TYPES):
        return _error_envelope(
            name,
            "SCOPE_DENIED",
            "The request is outside the configured KnowledgeHub policy.",
            status="denied",
            effect="none",
        )
    return None


def _operation(
    services: KnowledgeServices,
    name: str,
    arguments: dict[str, Any],
    *,
    session: TeamToolSession | OfficerToolSession | EvalToolSession | None = None,
) -> Callable[[], tuple[dict[str, Any], int]]:
    work_source_path = None
    if isinstance(session, (TeamToolSession, OfficerToolSession)) and session.graph_run_id is not None:
        work_source_path = services.resolve_reference(session.admitted_source())
    if name == "knowledge_search":
        return lambda: services.search(
            arguments["query"],
            scope=arguments.get("scope") if work_source_path is None else None,
            path_prefix=work_source_path or arguments.get("path_prefix"),
            include_types=arguments.get("include_types"),
            include_review=False,
            limit=min(arguments.get("limit", 10) + 1, 21),
            hops=0,
            expected_generation_id=arguments.get("expected_generation_id"),
            use_vector=False,
            mcp_allowed_path=work_source_path,
        )
    if name == "knowledge_retrieve":
        return lambda: services.retrieve(
            arguments["query"],
            scope=arguments.get("scope") if work_source_path is None else None,
            path_prefix=work_source_path or arguments.get("path_prefix"),
            include_types=arguments.get("include_types"),
            include_review=False,
            limit=min(arguments.get("limit", 10) + 1, 21),
            hops=arguments.get("hops", 1),
            expected_generation_id=arguments.get("expected_generation_id"),
            use_vector=False,
            mcp_allowed_path=work_source_path,
        )
    if name == "proposal_inspect":
        return lambda: services.inspect_proposal(arguments["proposal_reference"])
    if name == "proposal_create":
        if isinstance(session, OfficerToolSession):
            return lambda: session.create_proposal(arguments)
        if isinstance(session, TeamToolSession):
            return lambda: services.create_normalize_proposal(
                source_reference=arguments["source_reference"],
                work_binding=session.proposal_binding(arguments["source_reference"]))
        return lambda: services.create_normalize_proposal(
            source_reference=arguments["source_reference"],
        )

    def run_extended() -> tuple[dict[str, Any], int]:
        reader = KnowledgeReadTools(services, allowed_path=work_source_path)
        try:
            if name == "knowledge_read":
                data = reader.knowledge_read(arguments["resource_reference"], arguments["chunk_locator"],
                                             offset=arguments.get("offset", 0),
                                             max_chars=arguments.get("max_chars", 4096))
            elif name == "artifact_read":
                data = reader.artifact_read(arguments["artifact_reference"],
                                            offset=arguments.get("offset", 0),
                                            max_chars=arguments.get("max_chars", 4096))
            elif name == "base_view_query":
                data = reader.base_view_query(arguments["base"], arguments["view"],
                                              filters=arguments.get("filters"),
                                              limit=arguments.get("limit", 20))
            elif name == "link_context":
                data = reader.link_context(arguments["resource_reference"],
                                           limit=arguments.get("limit", 20))
            elif name == "task_query":
                data = reader.task_query(path_prefix=arguments.get("path_prefix"),
                                         limit=arguments.get("limit", 20))
            elif name == "citation_verify":
                data = reader.citation_verify(arguments["citations"])
            elif name == "semantic_registry_read":
                data = reader.semantic_registry_read(category=arguments["category"])
            elif name == "gateway_evidence_read":
                data = reader.gateway_evidence_read(arguments["receipt_id"])
            elif name == "vault_diagnose":
                data = reader.vault_diagnose(arguments["resource_reference"])
            elif name == "method_read" and isinstance(session, TeamToolSession):
                data = session.method_view()
            elif name == "work_context_read" and session is not None:
                data = session.work_view()
            elif name == "graph_segment_propose" and isinstance(session, TeamToolSession):
                return session.propose_graph(arguments)
            elif name == "assessment_submit" and isinstance(session, EvalToolSession):
                return session.submit_assessment(arguments)
            elif name == "proposal_draft_note":
                work_binding = None
                if isinstance(session, TeamToolSession):
                    work_binding = session.proposal_binding(arguments["source_reference"])
                report, code = services.create_normalize_proposal(
                    source_reference=arguments["source_reference"], action="draft_note",
                    target_path=arguments["target_path"], target_type=arguments["target_type"],
                    title=arguments["title"], draft_body=arguments["draft_body"],
                    work_binding=work_binding)
                if code != 0:
                    return report, code
                data = {"proposal_id": report["proposal"]["proposal_id"],
                        "proposal_reference": report["resource_reference"],
                        "source_reference": report["source_reference"],
                        "replayed": bool(report.get("replayed")), "apply_allowed": True}
                return {"status": report["status"], "data": data,
                        "execution_outcome": report.get("execution_outcome", "completed"),
                        "acceptance_state": report.get("acceptance_state", "not_evaluated")}, 0
            elif name in {"proposal_link_suggestions", "proposal_alignment",
                          "proposal_project_review", "proposal_adaptation",
                          "proposal_recovery_plan", "artifact_submit"}:
                return submit_pending(services, name, arguments, session=session)
            else:
                raise AdmissionError("ACTION_DENIED", "Owner has no admitted handler for this tool")
            return {"status": "PASS", "data": data, "execution_outcome": "completed",
                    "acceptance_state": "not_evaluated"}, 0
        except (ValueError, OSError, KeyError, TypeError) as error:
            return {"status": "FAIL", "errors": [{"code": getattr(error, "code", "INVALID_ARGUMENT"),
                                                    "message": "Owner rejected the selected evidence or effect."}],
                    "execution_outcome": "not_started"}, 10

    return run_extended


def _error_envelope(
    name: str,
    code: str,
    message: str,
    *,
    status: str,
    effect: str,
    retryable: bool = False,
) -> dict[str, Any]:
    return {
        "schema_version": 3,
        "request_id": uuid.uuid4().hex,
        "operation": name,
        "status": status,
        "effect": effect,
        "execution_outcome": "unknown" if effect == "unknown" else "not_started",
        "acceptance_state": "unknown" if effect == "unknown" else "not_evaluated",
        "human_approval": "unknown",
        "canonical_target_changed": False,
        "data": None,
        "error": {"code": code, "message": message, "retryable": retryable},
    }


def _success_envelope(name: str, data: dict[str, Any], *, status: str, effect: str, report: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": 3,
        "request_id": uuid.uuid4().hex,
        "operation": name,
        "status": status,
        "effect": effect,
        "execution_outcome": report.get("execution_outcome", "unknown"),
        "acceptance_state": report.get("acceptance_state", "unknown"),
        "human_approval": "unknown",
        "canonical_target_changed": False,
        "data": data,
        "error": None,
    }


def _source_error(
    name: str,
    report: dict[str, Any],
    *,
    create_effect_uncertain: bool = False,
) -> dict[str, Any]:
    errors = report.get("errors")
    code = errors[0].get("code") if isinstance(errors, list) and errors and isinstance(errors[0], dict) else ""
    if code in {"RETRIEVAL_GENERATION_MISMATCH", "RETRIEVAL_INDEX_STALE"}:
        mapped, status, message, retryable = "STALE_GENERATION", "stale_generation", "The current index generation is stale.", True
    elif code == "RETRIEVAL_POLICY_STALE":
        mapped, status, message, retryable = "POLICY_STALE", "denied", "The configured retrieval policy changed.", True
    elif code == "RETRIEVAL_INDEX_UNAVAILABLE":
        mapped, status, message, retryable = "UNAVAILABLE", "unavailable", "The current retrieval generation is unavailable.", True
    elif code in {
        "OWNER_DENIED", "AUTHORITY_DENIED", "ACTION_DENIED", "PATH_DENIED",
        "SCOPE_DENIED", "ACCEPTANCE_REQUIRED", "PIN_MISMATCH", "CORE_DIGEST_MISMATCH",
        "CONTRACT_INVALID", "STATE_PIN_MISMATCH", "STATE_VERSION_UNSUPPORTED",
        "PROPOSAL_NAMESPACE_INVALID",
        "PROPOSAL_SOURCE_INVALID",
        "C27_PRIVACY_DENIED",
        "C27_SOURCE_INVALID",
        "C27_SOURCE_TYPE_INVALID",
    }:
        mapped, status, message, retryable = "SCOPE_DENIED", "denied", "The request is outside the configured KnowledgeHub policy.", False
    elif code in {"PROPOSAL_INVALID", "REVIEW_INVALID"} and name == "proposal_inspect":
        mapped, status, message, retryable = "NOT_FOUND", "not_found", "The requested Pending proposal is unavailable.", False
    elif code in {"C27_SOURCE_DRIFT", "C27_PROPOSAL_CONFLICT"}:
        mapped, status, message, retryable = "CONFLICT", "conflict", "Current source or proposal bytes conflict with the request.", False
    elif code in {"RESOURCE_STALE", "ARTIFACT_CONFLICT"} and not create_effect_uncertain:
        mapped, status, message, retryable = "CONFLICT", "conflict", "Current resource bytes conflict with the request.", False
    elif code == "RESOURCE_UNAVAILABLE":
        mapped, status, message, retryable = "NOT_FOUND", "not_found", "The requested resource is unavailable.", False
    elif code in {"RETRIEVAL_INPUT_INVALID", "C27_SOURCE_HASH_INVALID", "C27_PATH_INVALID"}:
        mapped, status, message, retryable = "INVALID_ARGUMENT", "denied", "The request does not match the capability schema.", False
    elif create_effect_uncertain or code == "OUTCOME_UNKNOWN":
        mapped, status, message, retryable = "PERSISTENCE_ERROR", "error", "The Pending proposal outcome could not be confirmed.", False
        return _error_envelope(name, mapped, message, status=status, effect="unknown", retryable=retryable)
    else:
        mapped, status, message, retryable = "INTERNAL_ERROR", "error", "The operation could not be completed.", False
    return _error_envelope(
        name,
        mapped,
        message,
        status=status,
        effect="none",
        retryable=retryable,
    )


def _convert_report(
    context: ServerContext,
    name: str,
    arguments: dict[str, Any],
    result: tuple[dict[str, Any], int],
) -> tuple[dict[str, Any], bool]:
    report, code = result
    if code != 0 or report.get("status") not in {"PASS", "NO_OP", "NO_CHANGE"}:
        return _source_error(
            name,
            report,
            create_effect_uncertain=report.get("execution_outcome") == "unknown",
        ), True
    if name not in {"knowledge_search", "knowledge_retrieve", "proposal_inspect", "proposal_create"}:
        data = report.get("data")
        if not isinstance(data, dict):
            return _error_envelope(name, "INTERNAL_ERROR", "Owner returned an invalid result.",
                                   status="error", effect="none"), True
        is_write = name in _PROPOSAL_WRITES
        replayed = report["status"] == "NO_OP" or bool(data.get("replayed"))
        return _success_envelope(name, data,
                                 status="replayed" if replayed else "created" if is_write else "ok",
                                 effect=("proposal_replayed" if replayed else "proposal_created") if is_write else "read_only",
                                 report=report), False
    if name in {"knowledge_search", "knowledge_retrieve"}:
        candidates = report.get("candidates")
        if not isinstance(candidates, list):
            return _error_envelope(
                name,
                "INTERNAL_ERROR",
                "The operation could not be completed.",
                status="error",
                effect="none",
            ), True
        requested_limit = arguments.get("limit", 10)
        truncated = len(candidates) > requested_limit
        bounded = []
        reader = KnowledgeReadTools(context.services)
        try:
            for candidate in candidates[:requested_limit]:
                excerpt = reader.knowledge_read(candidate["resource_reference"],
                                                candidate["chunk_locator"], max_chars=300)
                if excerpt["chunk_hash"] != candidate["chunk_hash"]:
                    raise AdmissionError("RESOURCE_STALE", "Cited chunk changed")
                bounded.append({
                    **{key: candidate[key] for key in (
                        "note_id", "resource_reference", "chunk_id", "chunk_hash", "chunk_locator")},
                    "excerpt": excerpt["text"], "excerpt_sha256": excerpt["excerpt_sha256"],
                })
        except (ValueError, OSError, KeyError):
            return _error_envelope(name, "CONFLICT", "Current evidence changed during read.",
                                   status="conflict", effect="none"), True
        data = {
            "query_sha256": report["query_sha256"],
            "policy_decision_sha256": report["policy_decision_sha256"],
            "index_generation_id": report["index_generation_id"],
            "retrieval_config_sha256": report["retrieval_config_sha256"],
            "candidate_count": len(bounded),
            "candidates": bounded,
            "truncated": truncated,
        }
        return _success_envelope(
            name,
            data,
            status="ok" if bounded else "empty",
            effect="read_only",
            report=report,
        ), False
    if name == "proposal_inspect":
        proposals = report.get("proposals")
        if not isinstance(proposals, list) or len(proposals) != 1:
            return _error_envelope(
                name,
                "NOT_FOUND",
                "The requested Pending proposal is unavailable.",
                status="not_found",
                effect="none",
            ), True
        item = proposals[0]
        return _success_envelope(
            name,
            {"proposal_id": item["proposal_id"], "proposal_reference": item["resource_reference"],
             "action": item["action"], "source_references": [context.services.reference(source["path"]) for source in item["source_baselines"]],
             "target_reference": context.services.reference(item["target_path"]),
             "target_before_sha256": item["target_before_sha256"], "target_after_sha256": item["target_after_sha256"]},
            status="ok",
            effect="read_only",
            report=report,
        ), False

    proposal = report.get("proposal")
    proposal_source = proposal.get("source") if isinstance(proposal, dict) else None
    source_path = report.get("source")
    source_hash = report.get("source_sha256")
    if isinstance(proposal_source, dict):
        source_path = source_path or proposal_source.get("path")
        source_hash = source_hash or proposal_source.get("sha256")
    if report.get("status") == "NO_CHANGE":
        return _success_envelope(
            name,
            {
                "source_reference": report["source_reference"],
                "proposal_id": None,
                "proposal_reference": None,
                "proposal_sha256": None,
            },
            status="no_change",
            effect="no_change",
            report=report,
        ), False
    if not isinstance(proposal, dict):
        return _error_envelope(
            name,
            "PERSISTENCE_ERROR",
            "The Pending proposal outcome could not be confirmed.",
            status="error",
            effect="unknown",
        ), True
    replayed = bool(report.get("replayed")) or report.get("status") == "NO_OP"
    data = {
        "source_reference": report["source_reference"],
        "proposal_id": proposal.get("proposal_id"),
        "proposal_reference": report["resource_reference"],
        "proposal_sha256": report.get("proposal_sha256"),
    }
    return _success_envelope(
        name,
        data,
        status="replayed" if replayed else "created",
        effect="proposal_replayed" if replayed else "proposal_created",
        report=report,
    ), False


def _mcp_result(
    context: ServerContext,
    envelope: dict[str, Any],
    *,
    is_error: bool,
) -> types.CallToolResult:
    if not context.result_validator.is_valid(envelope):
        name = str(envelope.get("operation", "knowledge_search"))
        envelope = _error_envelope(
            name,
            "INTERNAL_ERROR",
            "The operation could not be completed.",
            status="error",
            effect="unknown" if envelope.get("effect") == "unknown" else "none",
        )
        is_error = True
    serialized = json.dumps(envelope, ensure_ascii=False, separators=(",", ":"))
    if len(serialized.encode("utf-8")) > context.limits["response_utf8_bytes"]:
        envelope = _error_envelope(
            envelope["operation"],
            "INTERNAL_ERROR",
            "The response exceeds the capability byte limit.",
            status="error",
            effect="unknown" if envelope.get("effect") == "unknown" else "none",
        )
        serialized = json.dumps(envelope, ensure_ascii=False, separators=(",", ":"))
        is_error = True
    return types.CallToolResult(
        isError=is_error,
        content=[types.TextContent(type="text", text=serialized)],
        structuredContent=envelope,
    )


async def _serve(context: ServerContext) -> None:
    server = _create_server(context)
    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            server.create_initialization_options(),
        )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="vaultmcp")
    parser.add_argument(
        "--control-root",
        required=True,
        type=Path,
        help="trusted KnowledgeOS control root selected by the operator",
    )
    parser.add_argument("--work-run-id", help="owner launcher-selected WorkRun")
    parser.add_argument("--executor-id", help="owner launcher-selected executor")
    parser.add_argument("--graph-run-id", help="owner launcher-selected active GraphRun")
    return parser


def main() -> int:
    args = _parser().parse_args()
    try:
        context = _load_context(args.control_root, work_run_id=args.work_run_id,
                                executor_id=args.executor_id, graph_run_id=args.graph_run_id)
        asyncio.run(_serve(context))
    except (OSError, RootResolutionError, ValueError) as error:
        del error
        print("vaultmcp: startup configuration invalid", file=sys.stderr)
        return 2
    except (BrokenPipeError, EOFError):
        return 0
    except Exception:  # noqa: BLE001 - redact protocol failures at the process boundary
        print("vaultmcp: protocol session failed", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

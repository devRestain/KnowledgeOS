"""C40 explicitly enabled local provider routes.

The route layer is the only path that combines the bounded C33 Ollama
transport with the C35 route-specific validation contract.  It is disabled by
default and requires an explicit one-shot authorization from its caller.  It
never writes the Vault, invokes C19 apply, enables a scheduler, or creates a
persistent provider process.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from .gemma_routes import GemmaRouteError, validate_gemma_output
from .ollama import OllamaClient, OllamaProviderAdapter
from .provider_broker import (
    EXIT_CONFLICT,
    EXIT_INPUT_INVALID,
    BrokerError,
    _resolve_pipeline,
    _validate_request_and_context,
    _workspace,
    run_synthetic_job,
)

CAPABILITY = "C40"
OPERATION = "ai local route"

C40_LOCAL_PIPELINES = (
    "answer",
    "triage",
    "draft_note",
    "link_suggestions",
    "normalize",
)


def _report(
    *,
    status: str,
    pipeline: str | None,
    error_code: str | None = None,
    error_message: str | None = None,
    **extra: Any,
) -> dict[str, Any]:
    report: dict[str, Any] = {
        "status": status,
        "operation": OPERATION,
        "capability": CAPABILITY,
        "pipeline": pipeline,
        "provider_called": False,
        "synthetic_provider_called": False,
        "live_provider_called": False,
        "mutation_performed": False,
        "vault_mutation_performed": False,
        "canonical_apply_allowed": False,
    }
    if error_code is not None:
        report["errors"] = [{"code": error_code, "message": error_message or error_code}]
    report.update(extra)
    return report


def _validate_route_output(
    workspace: Path,
    context: Mapping[str, Any],
    output: Mapping[str, Any],
    pipeline: str,
) -> Mapping[str, Any]:
    if pipeline not in C40_LOCAL_PIPELINES:
        raise BrokerError("C40_ROUTE_INVALID", "C40 local route is not allowlisted")
    try:
        return validate_gemma_output(workspace, context, output, route=pipeline)
    except GemmaRouteError as error:
        raise BrokerError(error.code, str(error)) from error
    except (OSError, TypeError, ValueError) as error:
        raise BrokerError("C40_OUTPUT_VALIDATION_FAILED", str(error)) from error


def _with_c40_identity(report: Mapping[str, Any], *, pipeline: str) -> dict[str, Any]:
    result = dict(report)
    result["operation"] = OPERATION
    result["capability"] = CAPABILITY
    result["pipeline"] = pipeline
    result["proposal_only"] = True
    result["canonical_apply_allowed"] = False
    return result


def run_local_route(
    root: str | Path,
    *,
    job_id: str,
    client: OllamaClient | None,
    pipeline: str | None = None,
    enabled: bool = False,
    authorized: bool = False,
) -> tuple[dict[str, Any], int]:
    """Run one explicitly enabled C40 local route through the broker.

    ``enabled`` and ``authorized`` are intentionally separate.  The default
    path is a read-only deferred report, so a queue wake, LaunchAgent, or
    plugin cannot activate the provider implicitly.
    """

    selected_pipeline: str | None = pipeline
    request: Mapping[str, Any] | None = None
    try:
        workspace = _workspace(root)
        request, _context, _ = _validate_request_and_context(workspace, job_id)
        selected_pipeline = _resolve_pipeline(str(request["action"]), pipeline)
        if selected_pipeline not in C40_LOCAL_PIPELINES:
            raise BrokerError("C40_ROUTE_INVALID", "requested pipeline is not a C40 local route")
    except (BrokerError, OSError, TypeError, ValueError) as error:
        wrapped = error if isinstance(error, BrokerError) else BrokerError("C40_INPUT_INVALID", str(error))
        report = _report(
            status="FAIL",
            pipeline=selected_pipeline,
            error_code=wrapped.code,
            error_message=str(wrapped),
        )
        if request is not None:
            report.update(
                {
                    "job_id": request.get("job_id"),
                    "request_sha256": request.get("request_sha256"),
                    "context_sha256": request.get("context_sha256"),
                }
            )
        return report, EXIT_INPUT_INVALID

    if not enabled:
        return (
            _report(
                status="DEFERRED",
                pipeline=selected_pipeline,
                error_code="C40_ROUTE_DISABLED",
                error_message="C40 local routes are disabled unless explicitly enabled for one invocation",
                job_id=request.get("job_id"),
                request_sha256=request.get("request_sha256"),
                context_sha256=request.get("context_sha256"),
                authorization="not_authorized",
                route_enabled=False,
                proposal_only=True,
            ),
            EXIT_CONFLICT,
        )
    if not authorized:
        return (
            _report(
                status="DEFERRED",
                pipeline=selected_pipeline,
                error_code="C40_AUTHORIZATION_REQUIRED",
                error_message="one-shot local Ollama authorization is required for this invocation",
                job_id=request.get("job_id"),
                request_sha256=request.get("request_sha256"),
                context_sha256=request.get("context_sha256"),
                authorization="not_authorized",
                route_enabled=True,
                proposal_only=True,
            ),
            EXIT_CONFLICT,
        )
    if client is None:
        return (
            _report(
                status="FAIL",
                pipeline=selected_pipeline,
                error_code="C40_CLIENT_REQUIRED",
                error_message="an explicitly authorized Ollama client is required",
                job_id=request.get("job_id"),
                request_sha256=request.get("request_sha256"),
                context_sha256=request.get("context_sha256"),
                authorization="authorized",
                route_enabled=True,
                proposal_only=True,
            ),
            EXIT_INPUT_INVALID,
        )

    report, exit_code = run_synthetic_job(
        root,
        job_id=job_id,
        pipeline=selected_pipeline,
        adapter=OllamaProviderAdapter(client),
        output_validator=_validate_route_output,
    )
    result = _with_c40_identity(report, pipeline=selected_pipeline)
    result["authorization"] = "authorized"
    result["route_enabled"] = True
    return result, exit_code


__all__ = [
    "C40_LOCAL_PIPELINES",
    "CAPABILITY",
    "EXIT_CONFLICT",
    "EXIT_INPUT_INVALID",
    "OPERATION",
    "run_local_route",
]

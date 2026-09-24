"""C41 authenticated loopback transport and provider-free broker.

The Obsidian client is intentionally not a shell launcher.  This module is the
small HTTP seam it can call: one JSON request, one in-memory bearer token, and
one exact C41 response.  The default broker rechecks the persisted Vault note,
the retrieval and privacy policy bytes, and the current immutable projection
generation before delegating to the existing provider-free ``vaultctl ask``
path.  It has no writer, provider client, Git client, or scheduler.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
from collections.abc import Callable, Mapping
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from .answer import ask
from .note_engine import resolve_vault_relative_path
from .projection import load_current_projection
from .provider_contract import canonical_json_bytes
from .thin_client import (
    CAPABILITY,
    EXIT_CONFLICT,
    MAX_RESPONSE_BYTES,
    ThinClientError,
    build_citation_open_action,
    dispatch_thin_client_response,
)

BROKER_PATH = "/broker"
MAX_HTTP_REQUEST_BYTES = 128 * 1024
MAX_BEARER_TOKEN_BYTES = 256
MAX_NOTE_BYTES = 4 * 1024 * 1024
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_POLICY_PATHS = ("ops/policies/retrieval.yaml", "ops/policies/privacy.yaml")

PolicyDigestReader = Callable[[], tuple[str, str]]
IndexGenerationReader = Callable[[], str]
ContentDigestReader = Callable[[str], str]
AnswerRunner = Callable[[Mapping[str, Any]], tuple[Mapping[str, Any], int]]


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _safe_error_text(value: object, *, maximum: int = 512) -> str:
    text = str(value).replace("\x00", " ").strip()
    encoded = text.encode("utf-8")
    if len(encoded) <= maximum:
        return text
    return encoded[: maximum - 3].decode("utf-8", errors="ignore") + "..."


def _read_regular_file(path: Path, label: str, *, maximum: int = MAX_NOTE_BYTES) -> bytes:
    if path.is_symlink() or not path.is_file():
        raise ThinClientError("C41_RECHECK_UNAVAILABLE", f"{label} is not a regular file")
    try:
        raw = path.read_bytes()
    except OSError as error:
        raise ThinClientError("C41_RECHECK_UNAVAILABLE", f"{label} could not be read") from error
    if len(raw) > maximum:
        raise ThinClientError("C41_RECHECK_UNAVAILABLE", f"{label} exceeds the bounded read limit")
    return raw


def _validate_root(root: str | Path) -> Path:
    candidate = Path(root).expanduser()
    if candidate.is_symlink() or not candidate.is_dir():
        raise ThinClientError("C41_ROOT_INVALID", "control root must be an existing non-symlink directory")
    return candidate.resolve()


def _default_content_digest(root: Path, relative_path: str) -> str:
    vault = root / "KnowledgeHub"
    if vault.is_symlink() or not vault.is_dir():
        raise ThinClientError("C41_RECHECK_UNAVAILABLE", "KnowledgeHub is unavailable")
    try:
        path = resolve_vault_relative_path(vault, relative_path)
    except (TypeError, ValueError) as error:
        raise ThinClientError("C41_RECHECK_UNAVAILABLE", "scope path could not be resolved") from error
    return _sha256(_read_regular_file(path, "current note"))


def _default_policy_digests(root: Path) -> tuple[str, str]:
    retrieval, privacy = _POLICY_PATHS
    return (
        _sha256(_read_regular_file(root / retrieval, retrieval)),
        _sha256(_read_regular_file(root / privacy, privacy)),
    )


def _default_index_generation(root: Path) -> str:
    try:
        projection = load_current_projection(root, verify_sources=True)
    except (OSError, TypeError, ValueError) as error:
        raise ThinClientError("C41_RECHECK_UNAVAILABLE", "current index generation could not be verified") from error
    generation_id = projection.manifest.get("generation_id")
    if not isinstance(generation_id, str) or not generation_id:
        raise ThinClientError("C41_RECHECK_UNAVAILABLE", "current index generation is invalid")
    return generation_id


def _default_answer_runner(root: Path) -> AnswerRunner:
    def run(request: Mapping[str, Any]) -> tuple[Mapping[str, Any], int]:
        scope = request["scope"]
        policy = request["policy"]
        return ask(
            root,
            request["question"]["text"],
            path_prefix=scope["path"],
            expected_generation_id=policy["index_generation_id"],
        )

    return run


def _c41_response(
    request: Mapping[str, Any],
    *,
    status: str,
    result: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "capability": CAPABILITY,
        "request_sha256": request["request_sha256"],
        "brokered": True,
        "direct_provider_called": False,
        "content_digest_rechecked": True,
        "policy_digest_rechecked": True,
        "index_digest_rechecked": True,
        "mutation_performed": False,
        "canonical_apply_allowed": False,
        "proposal_only": True,
        "status": status,
        "result": dict(result),
    }


def _error_result(code: str, message: str) -> dict[str, Any]:
    return {
        "summary": message,
        "error_code": code,
        "errors": [{"code": code, "message": message}],
        "canonical_apply_allowed": False,
        "mutation_performed": False,
    }


def _answer_error(report: Mapping[str, Any]) -> tuple[str, str]:
    errors = report.get("errors")
    if isinstance(errors, list) and errors and isinstance(errors[0], Mapping):
        code = errors[0].get("code")
        message = errors[0].get("message")
        if isinstance(code, str) and isinstance(message, str):
            return code, _safe_error_text(message)
    return "C41_ANSWER_UNAVAILABLE", "vaultctl answer did not return a usable proposal-only result"


def _citation_payloads(citations: object) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if citations is None:
        return [], []
    if not isinstance(citations, list):
        raise ThinClientError("C41_ANSWER_CITATION_INVALID", "vaultctl answer citations are not a list")
    display: list[dict[str, Any]] = []
    actions: list[dict[str, Any]] = []
    for raw in citations[:8]:
        if not isinstance(raw, Mapping):
            raise ThinClientError("C41_ANSWER_CITATION_INVALID", "vaultctl answer citation is not an object")
        locator = raw.get("locator") or raw.get("chunk_locator")
        required = (
            "note_id",
            "path",
            "content_hash",
            "chunk_id",
            "chunk_hash",
            "index_generation_id",
            "excerpt",
            "excerpt_sha256",
        )
        if locator is None or any(field not in raw for field in required):
            raise ThinClientError("C41_ANSWER_CITATION_INVALID", "vaultctl answer citation is incomplete")
        candidate = {
            "note_id": raw["note_id"],
            "path": raw["path"],
            "content_hash": raw["content_hash"],
            "chunk_id": raw["chunk_id"],
            "chunk_hash": raw["chunk_hash"],
            "chunk_locator": locator,
            "index_generation_id": raw["index_generation_id"],
            "excerpt": raw["excerpt"],
            "excerpt_sha256": raw["excerpt_sha256"],
            "excerpt_byte_length": len(str(raw["excerpt"]).encode("utf-8")),
            "eligibility_class": "local_eligible",
        }
        citation = {
            field: candidate[field]
            for field in (
                "note_id",
                "path",
                "content_hash",
                "chunk_id",
                "chunk_hash",
                "chunk_locator",
                "index_generation_id",
                "excerpt",
                "excerpt_sha256",
                "excerpt_byte_length",
            )
        }
        try:
            action = build_citation_open_action(citation, [candidate])
        except ThinClientError as error:
            raise ThinClientError("C41_ANSWER_CITATION_INVALID", str(error)) from error
        rendered = dict(raw)
        rendered.update(
            {
                "locator": locator,
                "chunk_locator": locator,
                "exact_source": True,
                "canonical_apply_allowed": False,
            }
        )
        display.append(rendered)
        actions.append(action)
    return display, actions


class VaultThinClientBroker:
    """Read-only C41 broker backed by the provider-free ``vaultctl ask`` path."""

    def __init__(
        self,
        root: str | Path,
        *,
        content_digest_reader: ContentDigestReader | None = None,
        policy_digest_reader: PolicyDigestReader | None = None,
        index_generation_reader: IndexGenerationReader | None = None,
        answer_runner: AnswerRunner | None = None,
    ) -> None:
        self.root = _validate_root(root)
        self._content_digest_reader = content_digest_reader or (
            lambda relative_path: _default_content_digest(self.root, relative_path)
        )
        self._policy_digest_reader = policy_digest_reader or (
            lambda: _default_policy_digests(self.root)
        )
        self._index_generation_reader = index_generation_reader or (
            lambda: _default_index_generation(self.root)
        )
        self._answer_runner = answer_runner or _default_answer_runner(self.root)

    def dispatch(self, request: Mapping[str, Any]) -> Mapping[str, Any]:
        scope = request["scope"]
        policy = request["policy"]
        issues: list[dict[str, str]] = []

        try:
            observed_content = self._content_digest_reader(scope["path"])
        except ThinClientError:
            raise
        except (OSError, TypeError, ValueError) as error:
            raise ThinClientError("C41_RECHECK_UNAVAILABLE", "current note content could not be rechecked") from error
        if observed_content != scope["content_sha256"]:
            issues.append(
                {
                    "code": "C41_CONTENT_DRIFT",
                    "message": "current note content differs from the request digest",
                }
            )

        try:
            observed_policy, observed_privacy = self._policy_digest_reader()
        except ThinClientError:
            raise
        except (OSError, TypeError, ValueError) as error:
            raise ThinClientError("C41_RECHECK_UNAVAILABLE", "policy bytes could not be rechecked") from error
        if observed_policy != policy["policy_sha256"] or observed_privacy != policy["privacy_policy_sha256"]:
            issues.append(
                {
                    "code": "C41_POLICY_DRIFT",
                    "message": "current policy bytes differ from the request bindings",
                }
            )

        try:
            observed_generation = self._index_generation_reader()
        except ThinClientError:
            raise
        except (OSError, TypeError, ValueError) as error:
            raise ThinClientError("C41_RECHECK_UNAVAILABLE", "index generation could not be rechecked") from error
        if observed_generation != policy["index_generation_id"]:
            issues.append(
                {
                    "code": "C41_INDEX_DRIFT",
                    "message": "current index generation differs from the request binding",
                }
            )

        if issues:
            return _c41_response(
                request,
                status="conflict",
                result={
                    "summary": "The request was not answered because a bound input changed.",
                    "errors": issues,
                    "canonical_apply_allowed": False,
                    "mutation_performed": False,
                },
            )

        try:
            report, exit_code = self._answer_runner(request)
        except ThinClientError:
            raise
        except (OSError, TypeError, ValueError) as error:
            return _c41_response(
                request,
                status="failed",
                result=_error_result("C41_ANSWER_UNAVAILABLE", _safe_error_text(error)),
            )
        if not isinstance(report, Mapping):
            raise ThinClientError("C41_ANSWER_INVALID", "vaultctl answer returned a non-object report")
        if any(
            report.get(field) is True
            for field in ("provider_called", "live_provider_called", "mutation_performed", "vault_mutation_performed")
        ):
            raise ThinClientError("C41_BROKER_AUTHORITY", "vaultctl answer crossed a forbidden authority boundary")
        if report.get("status") != "PASS":
            code, message = _answer_error(report)
            status = "conflict" if exit_code == EXIT_CONFLICT else "failed"
            return _c41_response(
                request,
                status=status,
                result=_error_result(code, message),
            )

        summary = report.get("answer_plaintext")
        answer = report.get("answer")
        if not isinstance(summary, str) and isinstance(answer, Mapping):
            summary = answer.get("answer_plaintext")
        if not isinstance(summary, str) or not summary.strip():
            raise ThinClientError("C41_ANSWER_INVALID", "vaultctl answer did not return displayable text")
        citations, citation_actions = _citation_payloads(report.get("citations"))
        proposal_sha256 = report.get("answer_sha256")
        if not isinstance(proposal_sha256, str) or _SHA256.fullmatch(proposal_sha256) is None:
            proposal_sha256 = _sha256(canonical_json_bytes({"summary": summary, "citations": citations}))
        result: dict[str, Any] = {
            "summary": summary,
            "citations": citations,
            "citation_actions": citation_actions,
            "proposal_sha256": proposal_sha256,
            "answer_sha256": proposal_sha256,
            "proposal_kind": "provider_free_answer_review",
            "uncertainty": report.get("uncertainty"),
            "canonical_apply_allowed": False,
            "mutation_performed": False,
        }
        return _c41_response(request, status="completed", result=result)


def _parse_bearer(authorization: str | None) -> str:
    if not isinstance(authorization, str):
        raise ThinClientError("C41_AUTH_INVALID", "Bearer authorization is required")
    parts = authorization.split(" ")
    if len(parts) != 2 or parts[0] != "Bearer" or not parts[1]:
        raise ThinClientError("C41_AUTH_INVALID", "Bearer authorization is invalid")
    token = parts[1]
    if len(token.encode("utf-8")) > MAX_BEARER_TOKEN_BYTES or any(character.isspace() for character in token):
        raise ThinClientError("C41_AUTH_INVALID", "Bearer token is outside the bounded format")
    return token


class ThinClientHttpService:
    """One-request HTTP adapter with an in-memory server bearer token."""

    def __init__(self, broker: object, *, authorization_token: str) -> None:
        if not isinstance(authorization_token, str) or not authorization_token:
            raise ThinClientError("C41_AUTH_INVALID", "server authorization token is required")
        if len(authorization_token.encode("utf-8")) > MAX_BEARER_TOKEN_BYTES:
            raise ThinClientError("C41_AUTH_INVALID", "server authorization token is too large")
        if any(character.isspace() for character in authorization_token):
            raise ThinClientError("C41_AUTH_INVALID", "server authorization token contains whitespace")
        if not hasattr(broker, "dispatch") and not callable(broker):
            raise ThinClientError("C41_BROKER_INVALID", "broker must expose a dispatch seam")
        self._broker = broker
        self._authorization_token = authorization_token

    def dispatch(
        self,
        body: bytes,
        *,
        authorization: str | None,
        content_type: str | None,
    ) -> dict[str, Any]:
        if len(body) > MAX_HTTP_REQUEST_BYTES:
            raise ThinClientError("C41_REQUEST_TOO_LARGE", "HTTP request exceeds the bounded byte limit")
        if not isinstance(content_type, str) or content_type.split(";", 1)[0].strip().casefold() != "application/json":
            raise ThinClientError("C41_CONTENT_TYPE_INVALID", "HTTP request must use application/json")
        token = _parse_bearer(authorization)
        if not hmac.compare_digest(token, self._authorization_token):
            raise ThinClientError("C41_AUTH_INVALID", "Bearer token is not accepted by the broker")
        try:
            request = json.loads(body.decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError) as error:
            raise ThinClientError("C41_REQUEST_INVALID", "HTTP request is not valid UTF-8 JSON") from error
        if not isinstance(request, dict):
            raise ThinClientError("C41_REQUEST_INVALID", "HTTP request must be a JSON object")
        if canonical_json_bytes(request) != body:
            raise ThinClientError("C41_SERIALIZATION_INVALID", "HTTP request must use canonical JSON bytes")
        response = dispatch_thin_client_response(
            request,
            self._broker,
            authorization_token=self._authorization_token,
        )
        response_bytes = canonical_json_bytes(response)
        if len(response_bytes) > MAX_RESPONSE_BYTES:
            raise ThinClientError("C41_RESPONSE_TOO_LARGE", "HTTP response exceeds the bounded byte limit")
        return response


def _error_payload(error: ThinClientError) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "capability": CAPABILITY,
        "status": "FAIL",
        "errors": [{"code": error.code, "message": _safe_error_text(error)}],
    }


def _error_status(error: ThinClientError) -> int:
    if error.code.startswith("C41_AUTH"):
        return HTTPStatus.UNAUTHORIZED
    if error.code == "C41_RECHECK_UNAVAILABLE":
        return HTTPStatus.SERVICE_UNAVAILABLE
    return HTTPStatus.BAD_REQUEST


def create_thin_client_server(
    service: ThinClientHttpService,
    *,
    host: str = "127.0.0.1",
    port: int = 17900,
) -> ThreadingHTTPServer:
    """Create a server bound only to the explicitly allowed IPv4 loopback."""

    if host != "127.0.0.1":
        raise ThinClientError("C41_BINDING_INVALID", "thin-client broker must bind to 127.0.0.1")
    if not isinstance(port, int) or isinstance(port, bool) or not 0 <= port <= 65535:
        raise ThinClientError("C41_BINDING_INVALID", "thin-client broker port is invalid")

    class Handler(BaseHTTPRequestHandler):
        server_version = "KnowledgeOS-C41"
        sys_version = ""

        def log_message(self, _format: str, *_args: object) -> None:
            return

        def _write(self, status: int, payload: Mapping[str, Any]) -> None:
            body = canonical_json_bytes(payload)
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _method_not_allowed(self) -> None:
            self._write(
                HTTPStatus.METHOD_NOT_ALLOWED,
                _error_payload(ThinClientError("C41_METHOD_INVALID", "only POST is supported")),
            )

        def do_POST(self) -> None:
            if self.path != BROKER_PATH:
                self._write(
                    HTTPStatus.NOT_FOUND,
                    _error_payload(ThinClientError("C41_PATH_INVALID", "broker path is invalid")),
                )
                return
            lengths = self.headers.get_all("Content-Length") or []
            if len(lengths) != 1:
                self._write(
                    HTTPStatus.LENGTH_REQUIRED,
                    _error_payload(ThinClientError("C41_LENGTH_INVALID", "one Content-Length header is required")),
                )
                return
            try:
                length = int(lengths[0])
            except (TypeError, ValueError):
                length = -1
            if length < 0 or length > MAX_HTTP_REQUEST_BYTES:
                self._write(
                    HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
                    _error_payload(ThinClientError("C41_REQUEST_TOO_LARGE", "HTTP request length is invalid")),
                )
                return
            body = self.rfile.read(length)
            if len(body) != length:
                self._write(
                    HTTPStatus.BAD_REQUEST,
                    _error_payload(ThinClientError("C41_BODY_INVALID", "HTTP request body is truncated")),
                )
                return
            authorizations = self.headers.get_all("Authorization") or []
            authorization = authorizations[0] if len(authorizations) == 1 else None
            try:
                response = service.dispatch(
                    body,
                    authorization=authorization,
                    content_type=self.headers.get("Content-Type"),
                )
            except ThinClientError as error:
                self._write(_error_status(error), _error_payload(error))
                return
            self._write(HTTPStatus.OK, response)

        def do_GET(self) -> None:
            self._method_not_allowed()

        def do_PUT(self) -> None:
            self._method_not_allowed()

        def do_DELETE(self) -> None:
            self._method_not_allowed()

        def do_PATCH(self) -> None:
            self._method_not_allowed()

    return ThreadingHTTPServer((host, port), Handler)


def serve_thin_client(server: ThreadingHTTPServer) -> None:
    """Run a caller-owned loopback server until its caller shuts it down."""

    try:
        server.serve_forever()
    finally:
        server.server_close()


__all__ = [
    "BROKER_PATH",
    "MAX_BEARER_TOKEN_BYTES",
    "MAX_HTTP_REQUEST_BYTES",
    "ThinClientHttpService",
    "VaultThinClientBroker",
    "create_thin_client_server",
    "serve_thin_client",
]

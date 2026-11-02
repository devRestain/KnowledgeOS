"""Explicit owner-only Ollama embedding binding for the selected local model.

This module never installs, downloads or starts a model service. SearchModes
formats inputs according to ``pin`` before calling this port.
"""
from __future__ import annotations

import json
import math
import re
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Sequence
from typing import Any

from ..application.knowledge import DomainHandlers, KnowledgeApplication, LocalIdentity
from ..paths import ResolvedPaths
from ..search_modes import ModelPin
from .core import AdmissionError

MODEL = "qwen3-embedding:8b-q4_K_M"
MANIFEST_DIGEST = "64b933495768fbd3b87c20583d379728a07471e0c66733a9df87cd1901b3c44b"
GGUF_DIGEST = "3fcd3febec8b3fd64435204db75bf0dd73b91e8d0661e0331acfe7e7c3120b85"
QUERY_FORMAT = ("Instruct: Given a user query, retrieve relevant passages from the knowledge base "
                "that answer the query\nQuery:{text}")
DOCUMENT_FORMAT = "{text}"
PIN = ModelPin("ollama/" + MODEL, "sha256:" + MANIFEST_DIGEST, 4096,
               QUERY_FORMAT, DOCUMENT_FORMAT)
_MAX_RESPONSE = 4 * 1024 * 1024
_MAX_TEXT_BYTES = 1024 * 1024


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req: Any, fp: Any, code: int, msg: str,
                         headers: Any, newurl: str) -> None:
        return None


class OllamaEmbeddingPort:
    """One exact local endpoint and model; drift or untrusted output fails closed."""

    trusted_local = True

    def __init__(self, endpoint: str = "http://127.0.0.1:11434", *, timeout: float = 300,
                 request: Callable[[str, dict[str, Any] | None], dict[str, Any]] | None = None):
        try:
            parsed = urllib.parse.urlsplit(endpoint)
            valid = (parsed.scheme == "http" and parsed.hostname == "127.0.0.1"
                     and parsed.username is None and parsed.password is None
                     and parsed.path == "" and not parsed.query and not parsed.fragment
                     and parsed.port is not None and 1 <= parsed.port <= 65535
                     and parsed.netloc == f"127.0.0.1:{parsed.port}")
        except (TypeError, ValueError):
            valid = False
        if not valid or not isinstance(timeout, (int, float)) or not 0 < timeout <= 600:
            raise AdmissionError("BINDING_INVALID", "Ollama requires an explicit local loopback endpoint")
        self._endpoint = endpoint
        self._timeout = float(timeout)
        self._opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())
        # An explicitly selected owner stdio bridge may execute this exact local
        # HTTP request on the host instead of misusing container loopback.
        self._host_request = request

    def _request(self, path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
        if self._host_request is not None:
            value = self._host_request(path, body)
            if not isinstance(value, dict):
                raise AdmissionError("EMBEDDING_UNAVAILABLE", "Local embedding bridge returned invalid data")
            return value
        payload = None if body is None else json.dumps(body, ensure_ascii=False, allow_nan=False).encode("utf-8")
        request = urllib.request.Request(self._endpoint + path, data=payload,
                                         headers={"Content-Type": "application/json"})
        try:
            with self._opener.open(request, timeout=self._timeout) as response:
                if response.status != 200:
                    raise ValueError("unexpected status")
                raw = response.read(_MAX_RESPONSE + 1)
            if len(raw) > _MAX_RESPONSE:
                raise ValueError("response exceeds bound")
            value = json.loads(raw)
        except (OSError, ValueError, UnicodeError, urllib.error.URLError) as error:
            raise AdmissionError("EMBEDDING_UNAVAILABLE", "Local embedding service refused the request") from error
        if not isinstance(value, dict):
            raise AdmissionError("EMBEDDING_UNAVAILABLE", "Local embedding service returned invalid data")
        return value

    def _check_identity(self) -> None:
        models = self._request("/api/tags").get("models")
        matches = [row for row in models if isinstance(row, dict) and row.get("name") == MODEL] \
            if isinstance(models, list) else []
        if len(matches) != 1 or matches[0].get("digest") != MANIFEST_DIGEST:
            raise AdmissionError("MODEL_PIN_MISMATCH", "Selected local model manifest changed")
        show = self._request("/api/show", {"model": MODEL})
        info, details = show.get("model_info"), show.get("details")
        if not isinstance(info, dict) or not isinstance(details, dict):
            raise AdmissionError("MODEL_PIN_MISMATCH", "Selected local model details are unavailable")
        parent, capabilities = details.get("parent_model"), show.get("capabilities")
        if (not isinstance(parent, str) or re.search(r"(?:^|/)sha256-([0-9a-f]{64})$", parent) is None
                or parent.rsplit("sha256-", 1)[-1] != GGUF_DIGEST
                or details.get("format") != "gguf" or details.get("family") != "qwen3"
                or details.get("quantization_level") != "Q4_K_M"
                or info.get("qwen3.embedding_length") != PIN.dimension
                or info.get("qwen3.context_length") != 40960
                or not isinstance(capabilities, list) or "embedding" not in capabilities):
            raise AdmissionError("MODEL_PIN_MISMATCH", "Selected local model details changed")

    @property
    def pin(self) -> ModelPin:
        # SearchModes also consults this property when reusing an existing index.
        self._check_identity()
        return PIN

    def _embed(self, text: str) -> Sequence[float]:
        if not isinstance(text, str) or not text.strip() or len(text.encode("utf-8")) > _MAX_TEXT_BYTES:
            raise AdmissionError("EMBEDDING_INVALID", "Embedding input is empty or exceeds its byte bound")
        self._check_identity()
        result = self._request("/api/embed", {"model": MODEL, "input": text, "truncate": False})
        self._check_identity()
        embeddings = result.get("embeddings")
        if (result.get("model") != MODEL or not isinstance(embeddings, list) or len(embeddings) != 1
                or not isinstance(embeddings[0], list) or len(embeddings[0]) != PIN.dimension
                or any(type(value) not in (int, float) or not math.isfinite(value)
                       for value in embeddings[0])):
            raise AdmissionError("EMBEDDING_INVALID", "Local embedding response differs from its pin")
        return embeddings[0]

    def embed_document(self, text: str) -> Sequence[float]:
        return self._embed(text)

    def embed_query(self, text: str) -> Sequence[float]:
        return self._embed(text)


def compose_ollama_application(roots: ResolvedPaths, *, endpoint: str = "http://127.0.0.1:11434",
                               identity: LocalIdentity | None = None) -> KnowledgeApplication:
    """Inject the selected trusted port without changing CLI or MCP authority."""
    return KnowledgeApplication.from_roots(
        roots, identity=identity, handlers=DomainHandlers(vector_port=OllamaEmbeddingPort(endpoint)))

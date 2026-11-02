"""Contract tests for the explicitly selected local embedding transport."""
from __future__ import annotations

import io
import json
from unittest.mock import patch

import pytest

from vaultops.adapters.core import AdmissionError
from vaultops.adapters.ollama_embedding import (
    GGUF_DIGEST,
    MANIFEST_DIGEST,
    MODEL,
    PIN,
    OllamaEmbeddingPort,
    _NoRedirect,
)


def _tags(digest=MANIFEST_DIGEST):
    return {"models": [{"name": MODEL, "digest": digest}]}


def _show():
    return {"details": {"parent_model": "/private/blobs/sha256-" + GGUF_DIGEST,
                         "format": "gguf", "family": "qwen3", "quantization_level": "Q4_K_M"},
            "model_info": {"qwen3.embedding_length": 4096, "qwen3.context_length": 40960},
            "capabilities": ["embedding"]}


class Response(io.BytesIO):
    status = 200

    def __init__(self, value):
        super().__init__(json.dumps(value).encode())


class Opener:
    def __init__(self):
        self.requests = []
        self.digest = MANIFEST_DIGEST
        self.embedding = [0.25] * 4096
        self.show = _show()
        self.on_embed = None

    def open(self, request, timeout):
        self.requests.append((request.full_url, request.data, timeout))
        if request.full_url.endswith("/api/tags"):
            return Response(_tags(self.digest))
        if request.full_url.endswith("/api/show"):
            return Response(self.show)
        if self.on_embed:
            self.on_embed()
        return Response({"model": MODEL, "embeddings": [self.embedding]})


def _port(opener):
    with patch("urllib.request.build_opener", return_value=opener):
        return OllamaEmbeddingPort("http://127.0.0.1:9876")


@pytest.mark.parametrize("endpoint", ["http://localhost:11434", "http://192.168.1.2:11434",
                                      "https://127.0.0.1:11434", "http://127.0.0.1:11434/path",
                                      "http://user@127.0.0.1:11434", "http://127.0.0.1:11434/"])
def test_only_exact_loopback_endpoint(endpoint):
    with pytest.raises(AdmissionError) as error:
        OllamaEmbeddingPort(endpoint)
    assert error.value.code == "BINDING_INVALID"


def test_exact_pin_truncation_and_finite_dimension():
    opener = Opener()
    port = _port(opener)
    assert port.pin == PIN
    text = PIN.query_format.replace("{text}", "test")
    assert len(port.embed_query(text)) == 4096
    post = next(json.loads(body) for url, body, _ in opener.requests if url.endswith("/api/embed"))
    assert post == {"model": MODEL, "input": text, "truncate": False}
    assert [url.rsplit("/", 1)[-1] for url, _, _ in opener.requests[-5:]] == [
        "tags", "show", "embed", "tags", "show"]
    opener.embedding[0] = float("nan")
    with pytest.raises(AdmissionError) as error:
        port.embed_document("document")
    assert error.value.code == "EMBEDDING_INVALID"
    opener.embedding = [0.2]
    with pytest.raises(AdmissionError) as error:
        port.embed_document("document")
    assert error.value.code == "EMBEDDING_INVALID"


def test_manifest_drift_before_and_after_embedding_is_rejected():
    opener = Opener()
    port = _port(opener)
    opener.digest = "a" * 64
    with pytest.raises(AdmissionError) as error:
        _ = port.pin
    assert error.value.code == "MODEL_PIN_MISMATCH"
    opener.digest = MANIFEST_DIGEST
    opener.on_embed = lambda: setattr(opener, "digest", "b" * 64)
    with pytest.raises(AdmissionError) as error:
        port.embed_document("document")
    assert error.value.code == "MODEL_PIN_MISMATCH"


@pytest.mark.parametrize("changed", ["dimension", "blob", "capabilities"])
def test_model_details_drift_is_rejected(changed):
    opener = Opener()
    if changed == "dimension":
        opener.show["model_info"]["qwen3.embedding_length"] = 1024
    elif changed == "blob":
        opener.show["details"]["parent_model"] = "/private/blobs/sha256-" + "b" * 64
    else:
        opener.show["capabilities"] = None
    with pytest.raises(AdmissionError) as error:
        _ = _port(opener).pin
    assert error.value.code == "MODEL_PIN_MISMATCH"


def test_redirect_handler_refuses_every_destination():
    assert _NoRedirect().redirect_request(None, None, 302, "", {}, "http://example.invalid") is None


def test_oversized_or_empty_input_is_rejected_without_provider_call():
    opener = Opener()
    port = _port(opener)
    for text in (" ", "x" * (1024 * 1024 + 1)):
        with pytest.raises(AdmissionError) as error:
            port.embed_document(text)
        assert error.value.code == "EMBEDDING_INVALID"
    assert not opener.requests


@pytest.mark.parametrize("payload", [None, [], False, 12, "not-an-object"])
def test_non_object_service_json_remains_embedding_unavailable(payload):
    opener = Opener()
    port = _port(opener)
    with (
        patch.object(opener, "open", return_value=Response(payload)),
        pytest.raises(AdmissionError) as error,
    ):
        _ = port.pin
    assert error.value.code == "EMBEDDING_UNAVAILABLE"


def test_unexpected_python_type_error_is_not_relabelled_as_service_unavailability():
    opener = Opener()
    port = _port(opener)
    with (
        patch.object(opener, "open", side_effect=TypeError("unexpected transport implementation bug")),
        pytest.raises(TypeError),
    ):
        _ = port.pin


def test_selected_host_request_retains_pin_validation_and_unavailable_refusal():
    responses = {"/api/tags": _tags(), "/api/show": _show(),
                 "/api/embed": {"model": MODEL, "embeddings": [[0.25] * PIN.dimension]}}
    calls = []

    def request(path, body):
        calls.append((path, body))
        return responses[path]

    port = OllamaEmbeddingPort(request=request)
    assert port.pin == PIN
    assert len(port.embed_query("formatted query")) == PIN.dimension
    assert ("/api/embed", {"model": MODEL, "input": "formatted query", "truncate": False}) in calls
    responses["/api/tags"] = _tags("f" * 64)
    with pytest.raises(AdmissionError) as error:
        _ = port.pin
    assert error.value.code == "MODEL_PIN_MISMATCH"
    responses["/api/tags"] = None
    with pytest.raises(AdmissionError) as error:
        _ = port.pin
    assert error.value.code == "EMBEDDING_UNAVAILABLE"

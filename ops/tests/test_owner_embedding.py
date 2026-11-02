"""Optional Owner binding and real ordered-pipe consumer edges, without providers."""
from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from support.metadata_fixture import SOURCE
from support.owner_runtime_fixture import compose, write_policy
from support.process_streams import iter_process_lines

from vaultops.adapters.core import AdmissionError
from vaultops.application.owner_runtime import OwnerRuntimePolicy, WritableOwnerRuntime
from vaultops.interfaces import native_owner_driver as driver
from vaultops.search_modes import ModelPin

ROUTE = {"runtime_kind": "hermes_native", "hermes_version": "0.22.5",
    "hermes_revision": "bf0203a794a07aac3ff764972ab9087baa4e4f4e", "provider": "openai-codex",
    "model": "gpt-6.1-terra", "adapter_digest": "sha256:" + "a" * 64}


class NoModel:
    def __init__(self, binding, _app):
        self.binding = binding

    async def __call__(self, _request):
        raise AssertionError("No Work/model invocation is permitted")


def test_optional_port_preserves_default_and_owner_registration(tmp_path):
    unbound, runner, _, path, policy = compose(tmp_path, ROUTE, NoModel)
    assert unbound.application.handlers.vector_port is None
    before = unbound.application.journal.read()
    selected = object()
    bound = WritableOwnerRuntime(unbound.application.roots, policy=OwnerRuntimePolicy(path),
        runners={method: runner for method in policy["methods"]}, vector_port=selected)
    assert bound.application.handlers.vector_port is selected
    assert bound.application.journal.read() == before
    assert unbound.application.search("synthetic", mode="vector")[1] != 0
    policy["actions"].remove("search_query")
    write_policy(path, policy)
    for runtime in (unbound, bound):
        with pytest.raises(AdmissionError):
            runtime.exops._guard(runtime.actor, "search_query")


def test_publication_requires_selected_binding_and_private_action_before_embedding(tmp_path, monkeypatch):
    from vaultops.interfaces import writable_owner

    runtime, _, _, path, _ = compose(tmp_path, ROUTE, NoModel)
    before = runtime.application.journal.read()
    monkeypatch.setattr(writable_owner, "resolve_paths", lambda: runtime.application.roots)
    for selection in (None, driver.LOCAL_EMBEDDING):
        with pytest.raises(AdmissionError):
            asyncio.run(writable_owner.publish_index(SimpleNamespace(
                policy_file=path, local_embedding=selection)))
    assert runtime.application.journal.read() == before
    assert not (runtime.application.roots.runtime / "index/vector/current.json").exists()


@pytest.mark.parametrize("revocation", ["before_vector", "after_vector"])
def test_live_publication_revocation_refuses_pointer_switches(tmp_path, revocation):
    initial, runner, _, path, policy = compose(tmp_path, ROUTE, NoModel)
    policy["actions"].append("publish_index")
    write_policy(path, policy)

    class Port:
        trusted_local = True
        pin = ModelPin("fixture.owner-publication", "sha256:" + "a" * 64, 2,
                       "query:{text}", "{text}")
        revoke_on_document = False

        def embed_document(self, _text):
            if self.revoke_on_document:
                policy["actions"].remove("publish_index")
                write_policy(path, policy)
                self.revoke_on_document = False
            return [1.0, 0.5]

        def embed_query(self, _text):
            return [1.0, 0.5]

    port = Port()
    runtime = WritableOwnerRuntime(initial.application.roots, policy=OwnerRuntimePolicy(path),
        runners={method: runner for method in policy["methods"]}, vector_port=port)
    steward = runtime.application.daily_stewardship()
    steward.publish_index(authorize=runtime.authorize_index_publication)
    roots = runtime.application.roots
    lexical = roots.state / "index/exports/current.json"
    vector = roots.runtime / "index/vector/current.json"
    lexical_before, vector_before = lexical.read_bytes(), vector.read_bytes()
    journal_before = runtime.application.journal.read()
    note = roots.vault / SOURCE
    original_note = note.read_bytes()
    note.write_bytes(original_note + b"\\nFresh synthetic publication evidence.\\n")
    port.revoke_on_document = revocation == "before_vector"

    def authorize():
        if (revocation == "after_vector" and vector.read_bytes() != vector_before
                and "publish_index" in policy["actions"]):
            policy["actions"].remove("publish_index")
            write_policy(path, policy)
        runtime.authorize_index_publication()

    with pytest.raises(AdmissionError) as error:
        steward.publish_index(authorize=authorize)
    assert error.value.code == "ACTION_DENIED"
    assert lexical.read_bytes() == lexical_before
    assert (vector.read_bytes() == vector_before) == (revocation == "before_vector")
    assert runtime.application.journal.read() == journal_before
    note.write_bytes(original_note)
    report, code = runtime.application.search("synthetic", mode="vector")
    if revocation == "before_vector":
        assert code == 0, report
    else:
        # Existing two-pointer publication is not atomic: a revoked final
        # Lexical switch can leave a future Vector pointer, never served as ready.
        assert code != 0 and report["errors"][0]["code"] == "INDEX_STALE", report


@pytest.mark.parametrize("envelope,selection", [
    ({"path": "/api/tags", "body": None}, None),
    ({"path": "/api/pull", "body": {"model": driver.EMBEDDING_MODEL}}, driver.LOCAL_EMBEDDING),
    ({"path": "/api/show", "body": {"model": "other"}}, driver.LOCAL_EMBEDDING),
    ({"path": "/api/embed", "body": {"model": driver.EMBEDDING_MODEL,
        "input": "test", "truncate": True}}, driver.LOCAL_EMBEDDING),
    ({"path": "/api/tags", "body": None, "endpoint": "http://foreign"}, driver.LOCAL_EMBEDDING),
])
def test_host_bridge_denies_unselected_or_out_of_scope_before_network(monkeypatch, envelope, selection):
    def forbidden(*_args, **_kwargs):
        raise AssertionError("Denied bridge input must not open a connection")
    monkeypatch.setattr(driver.http.client, "HTTPConnection", forbidden)
    with pytest.raises(driver.NativeDriverError):
        driver.embedding_once(envelope, selection)


@pytest.mark.parametrize("status,payload,allowed", [
    (200, b'{"models":[]}', True), (302, b'{"models":[]}', False), (200, b'[]', False),
])
def test_host_bridge_fixed_loopback_and_redirect_refusal(monkeypatch, status, payload, allowed):
    connections, requests = [], []

    class Connection:
        def __init__(self, *args, **kwargs):
            connections.append((args, kwargs))

        def request(self, *args, **kwargs):
            requests.append((args, kwargs))

        def getresponse(self):
            return SimpleNamespace(status=status, read=lambda bound: payload[:bound])

        def close(self):
            connections.append("closed")

    monkeypatch.setattr(driver.http.client, "HTTPConnection", Connection)
    if allowed:
        assert driver.embedding_once({"path": "/api/tags", "body": None}, driver.LOCAL_EMBEDDING) == {"models": []}
    else:
        with pytest.raises(driver.NativeDriverError):
            driver.embedding_once({"path": "/api/tags", "body": None}, driver.LOCAL_EMBEDDING)
    assert connections == [(("127.0.0.1", 11434), {"timeout": 300}), "closed"]
    assert requests == [(("GET", "/api/tags"), {"body": None, "headers": {"Content-Type": "application/json"}})]


def test_http_thread_embedding_and_native_runner_share_one_real_pipe():
    import vaultops

    script = '''import asyncio,json
from vaultops.interfaces.writable_owner import NativeStdioTransport
async def main():
    transport=NativeStdioTransport()
    native=asyncio.create_task(transport.call("fixture.method", {"stage":"one"}))
    embed=asyncio.create_task(asyncio.to_thread(transport.embedding_request,"/api/tags",None))
    values=await asyncio.gather(native,embed)
    print("RESULT="+json.dumps(values),flush=True)
asyncio.run(main())
'''
    child = subprocess.Popen([sys.executable, "-c", script], stdin=subprocess.PIPE,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        env={**os.environ, "PYTHONPATH": str(Path(vaultops.__file__).resolve().parent.parent),
             "PYTHONDONTWRITEBYTECODE": "1"})
    try:
        frames = iter_process_lines(child.stdout, time.monotonic() + 15)
        first = next(frames)
        assert first.startswith(driver.REQUEST)
        child.stdin.write(b'{"response":{"native":"one"}}\n')
        child.stdin.flush()
        second = next(frames)
        assert second.startswith(driver.EMBEDDING_REQUEST)
        assert json.loads(second[len(driver.EMBEDDING_REQUEST):]) == {"path": "/api/tags", "body": None}
        child.stdin.write(b'{"response":{"models":[]}}\n')
        child.stdin.flush()
        assert json.loads(next(frames).removeprefix("RESULT=")) == [{"native": "one"}, {"models": []}]
        assert child.wait(timeout=5) == 0
    finally:
        if child.poll() is None:
            child.kill()
            child.wait(timeout=5)
        for stream in (child.stdin, child.stdout, child.stderr):
            stream.close()

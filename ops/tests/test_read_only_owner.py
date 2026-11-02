"""Read-only composition, local HTTP and unchanged domain payload boundaries."""

from __future__ import annotations

import http.client
import json
import threading
from dataclasses import replace

import pytest
from support.control_factory import make_separate_portable_fixture_roots

from vaultops.adapters.core import AdmissionError
from vaultops.application.exops import KnowledgeExOps, OwnerActor
from vaultops.application.read_tools import KnowledgeReadTools
from vaultops.interfaces.exops_web import ExOpsWebBackend
from vaultops.interfaces.loopback_owner import LoopbackIngress, create_loopback_owner
from vaultops.interfaces.owner_relay import create_owner_relay
from vaultops.interfaces.read_only_owner import compose_read_only_owner
from vaultops.projection import generate_projection

SOURCE = "40_Knowledge/Notes/정보의 빈칸은 공포의 상상을 강화한다.md"
DENIED_ROUTES = (
    "work/submit", "work/intake", "work/intake-template", "work/prepare-metadata",
    "work/approve", "work/apply", "work/effect", "work/dispatch", "worker/finished",
    "proposal/decide", "proposal/apply", "daily/decide", "effect/apply", "unknown/mutation",
)


def _roots(tmp_path):
    roots = make_separate_portable_fixture_roots(
        tmp_path, extra_inputs=("ops/config/team-catalog.json",))
    generate_projection(roots)
    return roots


def _snapshot(roots):
    return {(kind, path.relative_to(getattr(roots, kind)).as_posix()): path.read_bytes()
            for kind in ("vault", "state", "runtime")
            for path in getattr(roots, kind).rglob("*") if path.is_file()}


def test_read_only_owner_over_relay_preserves_sources_and_denies_all_effects(tmp_path):
    roots = _roots(tmp_path)
    before = _snapshot(roots)
    exops = compose_read_only_owner(roots, actor_id="owner.user")
    server = create_loopback_owner(exops, actor_id="owner.user", port=0)
    relay = create_owner_relay(upstream_host="127.0.0.1", upstream_port=server.server_port,
                               listen_port=0)
    threads = [threading.Thread(target=item.serve_forever, daemon=True) for item in (server, relay)]
    for thread in threads:
        thread.start()

    def call(route, payload):
        connection = http.client.HTTPConnection("127.0.0.1", relay.server_port, timeout=10)
        try:
            connection.request("POST", "/owner/" + route, json.dumps(payload), headers={
                "Content-Type": "application/json"})
            response = connection.getresponse()
            return response.status, json.loads(response.read())
        finally:
            connection.close()

    try:
        query = {"query": "공포", "mode": "lexical"}
        status, report = call("search/query", query)
        assert status == 200
        result = next(item for item in report["results"] if item["path"] == SOURCE)
        assert result["resource_reference"] == exops.application.reference(SOURCE)
        citation = KnowledgeReadTools(exops.application).knowledge_read(
            result["resource_reference"], result["chunk_locator"], max_chars=320)
        assert result["chunk_hash"] == citation["chunk_hash"]
        assert result["excerpt"] == citation["text"]
        assert call("owner/status", {})[1]["read_only"] is True
        assert call("work/list", {"path": SOURCE}) == (200, {"works": []})
        assert call("work/read", {"work_run_id": "absent"})[1]["errors"][0]["code"] == "NOT_FOUND"
        assert call("work/result", {"work_run_id": "absent"})[1]["errors"][0]["code"] == "NOT_FOUND"
        for route in DENIED_ROUTES:
            status, denial = call(route, {"actor_id": "owner.user", "decision": "approve"})
            assert status == 403 and denial["errors"][0]["code"] == "READ_ONLY"
        assert call("search/query", {**query, "actor_id": "another.owner"})[0] == 403
        assert call("search/query", {"query": "공포", "mode": "vector"})[0] == 403
        assert _snapshot(roots) == before
        source = roots.vault / SOURCE
        source.write_bytes(source.read_bytes() + b"\nChanged fixture source.\n")
        changed = _snapshot(roots)
        status, denial = call("search/query", query)
        assert status in {403, 409} and denial["status"] == "FAIL"
        assert _snapshot(roots) == changed
    finally:
        for item in (relay, server):
            item.shutdown()
            item.server_close()
        for thread in threads:
            thread.join(timeout=5)


def test_read_only_routes_deny_before_parsing_or_admission_and_bind_startup_actor(tmp_path):
    roots = _roots(tmp_path)
    exops = compose_read_only_owner(roots, actor_id="owner.user")
    actor = OwnerActor("owner.user", "web")
    called = []
    backend = ExOpsWebBackend(
        exops, actor,
        admit_request=lambda *_args: called.append(True),
        admit_template=lambda *_args: called.append(True))
    ingress = LoopbackIngress("knowledgeos", "POST", "work/submit", b"not JSON")
    for route in DENIED_ROUTES:
        with pytest.raises(AdmissionError) as failure:
            backend(replace(ingress, resource_path=route))
        assert failure.value.code == "READ_ONLY"
    assert not called
    with pytest.raises(AdmissionError):
        ExOpsWebBackend(exops, OwnerActor("another.owner", "web"))(
            replace(ingress, resource_path="owner/status", body=b"{}"))
    with pytest.raises(AdmissionError):
        exops._guard(OwnerActor("another.owner", "web"), "read_work")
    before = _snapshot(roots)
    for action in (
        lambda: exops._guard(actor, "submit_work"),
        lambda: exops.record_dispatch("none", action_id="none", outcome="accepted"),
        lambda: exops.record_observed_dispatch("none", action_id="none", observation=None),
        lambda: exops.worker_unknown("none", worker_identity=None, action_id="none"),
        lambda: exops.admit_method_segment("none", manager_executor_id="none", bundle={},
                                           artifacts={}, source_reference={}),
    ):
        with pytest.raises(AdmissionError) as failure:
            action()
        assert failure.value.code == "READ_ONLY"
    assert _snapshot(roots) == before
    with pytest.raises(AdmissionError):
        KnowledgeExOps(exops.application, authorize_actor=exops.authorize_actor)
    with pytest.raises(AdmissionError):
        KnowledgeExOps(exops.application, authorize_actor=exops.authorize_actor,
                       read_only=True, worker_identity="not-a-real-worker")


@pytest.mark.parametrize("damage", [
    "missing_runtime", "missing_journal", "state_pins", "core_pins", "source",
])
def test_read_only_startup_refuses_missing_roots_stale_state_and_sources(tmp_path, damage):
    roots = _roots(tmp_path)
    if damage == "missing_runtime":
        roots = replace(roots, runtime=tmp_path / "absent")
    elif damage == "missing_journal":
        (roots.state / "owner-control.json").unlink()
    elif damage == "state_pins":
        path = roots.state / "owner-control.json"
        state = json.loads(path.read_bytes())
        state["pins"] = {}
        path.write_text(json.dumps(state))
    elif damage == "core_pins":
        path = roots.control / "ops/config/core-adoption.json"
        binding = json.loads(path.read_bytes())
        binding["core_digest"] = "sha256:" + "0" * 64
        path.write_text(json.dumps(binding))
    else:
        path = roots.vault / SOURCE
        path.write_bytes(path.read_bytes() + b"\nChanged source.\n")
    before = _snapshot(roots)
    with pytest.raises(AdmissionError):
        compose_read_only_owner(roots, actor_id="owner.user")
    assert _snapshot(roots) == before

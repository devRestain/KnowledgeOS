"""C9 composition with the actual current-Core, authenticated HermesTrace adapter."""

from __future__ import annotations

import copy
import hashlib
import hmac
import json
import socket
import socketserver
import struct
import threading
import time
from contextlib import contextmanager
from types import SimpleNamespace

import pytest
from conftest import conforming_note, core_root, core_validate, organize_scripts
from hermestrace.operating import CoreBoundary, HermesTraceOwnerInbox, InterOpsError, InterOpsServer

from knowledgeos import interops
from operationkit.canonical import canonical, digest

SECRET = "PRIVATE_INTEROPS_NOTE_BODY_7b86f3"
DENIED = "40_Knowledge/Ideas/Denied.md"


def eventually(read, predicate, timeout=15):
    deadline = time.monotonic() + timeout
    while True:
        value = read()
        if predicate(value):
            return value
        assert time.monotonic() < deadline, value
        time.sleep(0.02)


def experience(kos):
    code, rows = kos.get("/api/experience")
    assert code == 200, rows
    return rows


def candidate(kos, delivery=None):
    rows = eventually(lambda: experience(kos), lambda rows: any(
        row["candidate"] and (delivery is None or row["delivery"] == delivery) for row in rows))
    return next(row for row in rows if row["candidate"] and (delivery is None or row["delivery"] == delivery))


def privacy_refusal(kos, vault, request_id="denied-one"):
    if not vault.path(DENIED).exists():
        vault.write(DENIED, conforming_note("idea", "seed", body=SECRET + "\n", policy="deny"))
    code, receipt = kos.submit("enhance", {"mode": "develop", "path": DENIED}, request_id=request_id)
    assert code == 202, receipt
    result = kos.wait(receipt["request_id"])
    assert result["status"] == "refused", result
    return result


def read_exact(connection, length):
    data = bytearray()
    while len(data) < length:
        chunk = connection.recv(length - len(data))
        if not chunk:
            raise EOFError("lost response")
        data.extend(chunk)
    return bytes(data)


def read_frame(connection, secret):
    length = struct.unpack("!I", read_exact(connection, 4))[0]
    assert 2 <= length <= 32768
    signature, body = read_exact(connection, 32), read_exact(connection, length)
    assert hmac.compare_digest(signature, hmac.new(secret, body, hashlib.sha256).digest())
    return json.loads(body)


def send_frame(connection, value, secret):
    body = canonical(value)
    signature = hmac.new(secret, body, hashlib.sha256).digest()
    connection.sendall(struct.pack("!I", len(body)) + signature + body)


def exchange(path, secret, request):
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
        connection.settimeout(2)
        connection.connect(str(path))
        send_frame(connection, request, secret)
        return read_frame(connection, secret)


def receiver_rows(trace):
    path = trace.state / "inbox.json"
    return list(json.loads(path.read_bytes())["entries"].values()) if path.exists() else []


@pytest.fixture(scope="session")
def boundary():
    return CoreBoundary(core_root())


@pytest.fixture
def trace(tmp_path, clock, boundary):
    state, inbox = tmp_path / "receiver-state", tmp_path / "Inbox"
    state.mkdir(mode=0o700)
    inbox.mkdir()
    key = tmp_path / "key"
    secret = b"k" * 32
    key.write_bytes(secret)
    key.chmod(0o600)
    owner = HermesTraceOwnerInbox(state, inbox, boundary, clock=clock.now)
    path = tmp_path / "h.sock"
    with InterOpsServer(path, key, owner) as server:
        thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True)
        thread.start()
        try:
            yield SimpleNamespace(state=state, inbox=inbox, path=path, key=key, secret=secret,
                                  core=boundary, owner=owner)
        finally:
            server.shutdown()
            thread.join(timeout=2)
            assert not thread.is_alive()


def settings(trace, path=None, **overrides):
    return {"C9": {"hermestrace_socket": str(path or trace.path),
                   "hermestrace_credential_file": str(trace.key),
                   "hermestrace_timeout_seconds": 0.2,
                   "delivery_interval_seconds": 0.02, **overrides}}


@contextmanager
def fault_proxy(trace, tmp_path, *, omit_admit=False, corrupt_receipt=False):
    """A faulty wire in front of the real adapter, never a replacement admission authority."""
    lock = threading.Lock()
    requests = []
    hold_queries = threading.Event()
    hold_queries.set()

    class Handler(socketserver.BaseRequestHandler):
        def handle(self):
            self.request.settimeout(2)
            try:
                request = read_frame(self.request, trace.secret)
                with lock:
                    requests.append(request)
                if request["method"] == "query_admission" and hold_queries.is_set():
                    return
                if omit_admit and request["method"] == "admit":
                    return
                response = exchange(trace.path, trace.secret, request)
                if request["method"] == "admit":
                    if not corrupt_receipt:
                        return  # Admission committed, but its real acknowledgment was lost.
                    response["result"]["candidate_id"] = "knowledgeos.candidate.unrelated"
                send_frame(self.request, response, trace.secret)
            except (OSError, EOFError):
                pass

    class Proxy(socketserver.ThreadingUnixStreamServer):
        daemon_threads = True

    path = tmp_path / "p.sock"
    with Proxy(str(path), Handler) as server:
        thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True)
        thread.start()
        try:
            yield SimpleNamespace(path=path, requests=requests, lock=lock, hold_queries=hold_queries)
        finally:
            server.shutdown()
            thread.join(timeout=2)
            path.unlink(missing_ok=True)


def test_LRN_interops_rejection_and_local_proposal(make_kos, vault, trace):
    kos = make_kos(settings=settings(trace))
    target = "00_Inbox/Private curation title.md"
    sha = vault.write(target, conforming_note("capture", "unprocessed", body=SECRET + "\n"))
    organize_scripts(kos.runner, link=False)
    organized = kos.run("organize", {"paths": [target], "digests": {target: sha}})
    assert organized["status"] == "completed", organized
    rows = eventually(lambda: experience(kos), lambda rows: any(row["signal"] == "teamreview_proposal" for row in rows))
    proposals = [row for row in rows if row["signal"] == "teamreview_proposal"]
    assert proposals and all(row["delivery"] == "local_only" for row in proposals)
    for row in proposals:
        assert row["candidate"] is None and row["candidate_id"] is None and row["outbox"] is None
        assert row["experience_id"] == row["event_id"]
        assert row["source_event"]["category"] == "routine_progress"
        assert row["source_event"]["evaluation_state"] == "not_evaluated"
        core_validate("ExperienceSourceEvent", row["source_event"])
        code, detail = kos.get(f"/api/experience/{row['event_id']}")
        assert code == 200 and detail == row
    assert receiver_rows(trace) == []
    files = list(vault.path("01_AI_Review/Proposals").glob("*.md"))
    assert len(files) == 1
    proposal = files[0].relative_to(vault.root).as_posix()
    rejected = kos.run("review.reject", {"path": proposal, "digest": vault.sha(proposal)})
    assert rejected["status"] == "completed", rejected
    entry = candidate(kos, "delivered")
    assert entry["signal"] == "proposal_rejected"
    assert entry["candidate"]["category"] == "human_correction"
    for name, record in (("ExperienceCandidate", entry["candidate"]),
                         ("ExperienceOutboxEntry", entry["outbox"]),
                         ("InterOpsEnvelope", entry["interops"]["envelope"]),
                         ("ExperienceDeliveryReceipt", entry["receipt"])):
        core_validate(name, record)
    assert entry["candidate"]["source_record"]["record_id"] == entry["source_event"]["event_id"]
    assert entry["candidate"]["source_record"]["content_digest"] == "sha256:" + digest(entry["source_event"])
    envelope = entry["interops"]["envelope"]
    assert {key: envelope[key] for key in trace.core.pins} == trace.core.pins
    assert envelope["payload_digest"] == "sha256:" + digest(entry["candidate"])
    assert envelope["payload_size_bytes"] == len(canonical(entry["candidate"]))
    assert trace.core.validate_submission(envelope, entry["candidate"], kos.clock.now()) == entry["interops"]["request_digest"]
    rows = receiver_rows(trace)
    assert len(rows) == 1 and rows[0]["receipt"] == entry["receipt"]
    assert rows[0]["candidate"] == entry["candidate"]
    assert entry["receipt"]["admission_level"] == "owner_durable"
    replay = exchange(trace.path, trace.secret, {
        "method": "admit", "envelope": envelope, "candidate": entry["candidate"]})
    assert replay == {"ok": True, "result": entry["receipt"]}
    assert len(receiver_rows(trace)) == 1
    assert all(row["delivery"] == "local_only" for row in experience(kos) if row["signal"] == "teamreview_proposal")
    exported = canonical(rows) + b"".join(path.read_bytes() for path in trace.inbox.glob("*.md"))
    assert SECRET.encode() not in exported and b"Private curation title" not in exported
    assert len(list(trace.inbox.glob("*.md"))) == 1
    assert "not promotion" in next(trace.inbox.glob("*.md")).read_text()


def test_LRN_interops_exact_admission(make_kos, vault, trace):
    kos = make_kos()
    privacy_refusal(kos, vault)
    entry = candidate(kos)
    prepared = interops.prepare(kos.owner, entry)
    request = {"method": "admit", "envelope": prepared["envelope"], "candidate": entry["candidate"]}
    bad = copy.deepcopy(request)
    bad["envelope"]["core_digest"] = "sha256:" + "0" * 64
    assert exchange(trace.path, trace.secret, bad)["result"]["code"] == "DIGEST_MISMATCH"
    bad = copy.deepcopy(request)
    bad["envelope"]["schema_version"] = "0.17.4"
    assert exchange(trace.path, trace.secret, bad)["result"]["code"] == "INVALID_PAYLOAD"
    bad = copy.deepcopy(request)
    bad["candidate"]["body"] = SECRET
    assert exchange(trace.path, trace.secret, bad)["result"]["code"] == "INVALID_PAYLOAD"
    with pytest.raises(ValueError, match="references-only"):
        interops.prepare(kos.owner, {**entry, "candidate": bad["candidate"]})
    bad = copy.deepcopy(request)
    bad["envelope"]["sender_principal_id"] = "another.local"
    assert exchange(trace.path, trace.secret, bad)["result"]["code"] == "SCOPE_DENIED"
    assert receiver_rows(trace) == []
    with pytest.raises(EOFError):
        exchange(trace.path, b"w" * 32, request)
    assert receiver_rows(trace) == []
    original = exchange(trace.path, trace.secret, request)
    assert original["ok"] and original["result"]["admission_level"] == "owner_durable"
    assert exchange(trace.path, trace.secret, request) == original
    changed = copy.deepcopy(request)
    changed["candidate"]["observation_summary"] = "A different bounded observation."
    changed["envelope"].update(payload_digest="sha256:" + digest(changed["candidate"]),
                               payload_size_bytes=len(canonical(changed["candidate"])))
    assert exchange(trace.path, trace.secret, changed)["result"]["code"] == "IDEMPOTENCY_CONFLICT"
    assert len(receiver_rows(trace)) == 1


@pytest.mark.parametrize("corrupt_receipt", [False, True], ids=["lost", "mismatched"])
def test_LRN_interops_unknown_queries_on_restart(make_kos, vault, trace, tmp_path, corrupt_receipt):
    with fault_proxy(trace, tmp_path, corrupt_receipt=corrupt_receipt) as proxy:
        config = settings(trace, proxy.path)
        kos = make_kos(settings=config)
        privacy_refusal(kos, vault)
        unknown = candidate(kos, "unknown")
        assert unknown["receipt"] is None and unknown["unknown_id"]
        committed = receiver_rows(trace)
        assert len(committed) == 1
        code, unknowns = kos.get("/api/unknowns")
        assert code == 200 and any(row["unknown_id"] == unknown["unknown_id"] for row in unknowns)
        kos.close()
        proxy.hold_queries.clear()
        restarted = make_kos(settings=config)
        recovered = candidate(restarted, "delivered")
        assert recovered["interops"] == unknown["interops"]
        assert recovered["receipt"] == committed[0]["receipt"]
        with proxy.lock:
            requests = list(proxy.requests)
        admits = [request for request in requests if request["method"] == "admit"]
        queries = [request for request in requests if request["method"] == "query_admission"]
        assert len(admits) == 1 and queries
        assert all(request["idempotency_key"] == unknown["outbox"]["idempotency_key"]
                   and request["request_digest"] == unknown["interops"]["request_digest"] for request in queries)
        assert len(receiver_rows(trace)) == 1


def test_LRN_interops_missing_receipt_never_resends(make_kos, vault, trace, tmp_path):
    with fault_proxy(trace, tmp_path, omit_admit=True) as proxy:
        proxy.hold_queries.clear()
        kos = make_kos(settings=settings(trace, proxy.path, outbox_limit=1))
        refused = privacy_refusal(kos, vault)
        first = candidate(kos, "unknown")
        eventually(lambda: list(proxy.requests), lambda rows: sum(row["method"] == "query_admission" for row in rows) >= 2)
        assert candidate(kos)["delivery"] == "unknown" and receiver_rows(trace) == []
        privacy_refusal(kos, vault, "denied-two")
        eventually(lambda: experience(kos), lambda rows: len(rows) == 1 and rows[0]["candidate_id"] != first["candidate_id"])
        # Replaying the original public Owner request cannot recreate an evicted unknown.
        code, replay = kos.submit("enhance", {"mode": "develop", "path": DENIED}, request_id="denied-one")
        assert code == 200 and replay == refused
        assert all(row["candidate_id"] != first["candidate_id"] for row in experience(kos))
        with proxy.lock:
            admits = [row for row in proxy.requests if row["method"] == "admit"
                      and row["candidate"]["candidate_id"] == first["candidate_id"]]
        assert len(admits) == 1 and receiver_rows(trace) == []


def test_LRN_interops_old_state_needs_scoped_migration(trace, clock):
    state = {"version": 1, "owner_operation_id": "hermestrace", "core_digest": "sha256:" + "a" * 64,
             "next_sequence": 1, "entries": {}}
    path = trace.state / "inbox.json"
    original = canonical(state) + b"\n"
    path.write_bytes(original)
    path.chmod(0o600)
    with pytest.raises(InterOpsError, match="scoped migration") as caught:
        HermesTraceOwnerInbox(trace.state, trace.inbox, trace.core, clock=clock.now)
    assert caught.value.code == "MIGRATION_REQUIRED"
    assert path.read_bytes() == original and list(trace.inbox.iterdir()) == []

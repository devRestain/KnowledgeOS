"""C9 composition with a Core-0.21.1-validated authenticated Unix InterOps receiver."""

from __future__ import annotations

import copy
import hashlib
import json
import socketserver
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import jsonschema
import pytest
from conftest import conforming_note, core_root, core_validate, organize_scripts
from operationkit.canonical import canonical, digest
from operationkit.interops import exchange as raw_exchange
from operationkit.interops import load_secret, receive_frame, request_digest, send_frame

from knowledgeos import interops

SECRET = "PRIVATE_INTEROPS_NOTE_BODY_7b86f3"
DENIED = "40_Knowledge/Ideas/Denied.md"


class ReceiverError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class CoreReceiverBoundary:
    """Validates Experience submissions against live frozen Core schemas and catalog."""

    def __init__(self, root: Path):
        self.root = Path(root)
        manifest = json.loads((self.root / "release/manifest.json").read_bytes())
        catalog = json.loads((self.root / "contracts/catalog.json").read_bytes())
        semantic = {k: v for k, v in catalog["semantic_bundle"].items() if k != "digest"}
        self.catalog = catalog
        self.pins = {
            "core_version": manifest["core_release_version"],
            "core_digest": manifest["release_digest"],
            "schema_version": catalog["schema_bundle_version"],
            "semantic_version": semantic["version"],
            "semantic_digest": "sha256:" + digest(semantic),
            "contract_version": catalog["capsule_contract_version"],
        }

    def validate_submission(self, envelope: dict, candidate: dict, now: datetime) -> str:
        try:
            core_validate("InterOpsEnvelope", envelope)
            core_validate("ExperienceCandidate", candidate)
        except jsonschema.ValidationError as error:
            raise ReceiverError("INVALID_PAYLOAD", str(error)) from error
        if any(envelope[key] != value for key, value in self.pins.items()) or any(
            candidate[key] != self.pins[key]
            for key in ("core_version", "core_digest", "semantic_version", "semantic_digest")
        ):
            raise ReceiverError("DIGEST_MISMATCH", "InterOps release pins differ")
        if (
            envelope["sender_operation_id"] != "knowledgeos"
            or envelope["sender_principal_id"] != "knowledgeos.local"
            or envelope["recipient_operation_id"] != "hermestrace"
            or envelope["resource_owner_operation_id"] != "hermestrace"
            or envelope["resource_kind"] != "exchange"
            or envelope["scope"] != "experience"
            or envelope["resource_id"] != "exchange.hermestrace.experience"
            or envelope["message_kind"] != "standard_submission"
            or envelope["purpose"] != "experience_submission"
            or envelope["payload_type"] != "experience.candidate"
            or envelope["action"] != "submit"
            or envelope["hop_count"] != 0
            or envelope["classification"] != candidate["classification"]
            or candidate["owner_operation_id"] != "knowledgeos"
            or candidate["classification"] not in {"public", "internal"}
            or candidate["idempotency_key"] != envelope["idempotency_key"]
            or candidate["candidate_id"] != envelope["payload_reference_id"]
            or candidate["semantic_term_ids"] != envelope["semantic_term_ids"]
            or envelope["payload_digest"] != "sha256:" + digest(candidate)
            or envelope["payload_size_bytes"] != len(canonical(candidate))
        ):
            raise ReceiverError("SCOPE_DENIED", "submission identity or payload differs")
        if len(canonical(candidate)) > self.catalog["limits"]["max_experience_candidate_bytes"]:
            raise ReceiverError("INVALID_PAYLOAD", "Experience candidate exceeds the Core byte limit")
        source = candidate["source_record"]
        if (
            source["owner_operation_id"] != "knowledgeos"
            or source["source_revision"] != candidate["source_revision"]
            or source["record_kind"] != "owner_lifecycle_event"
            or source["record_id"] != candidate["source_event_id"]
            or any(
                ref["owner_operation_id"] != "knowledgeos"
                or ref["classification"] not in {"public", "internal"}
                or ref["availability"] != "available_by_owner_export"
                for ref in candidate["evidence_references"]
            )
        ):
            raise ReceiverError("SCOPE_DENIED", "source or evidence owner differs")
        issued = datetime.fromisoformat(envelope["issued_at"])
        if issued > now or now >= issued + timedelta(seconds=envelope["ttl_seconds"]):
            raise ReceiverError("EXPIRED", "submission lifetime ended")
        policy = self.catalog["interop_policy"]
        profile = policy["payload_profiles"]["standard_submission"]
        if (
            envelope["purpose"] not in profile["purposes"]
            or envelope["payload_type"] not in profile["payload_types"]
            or envelope["action"] not in profile["actions"]
            or envelope["payload_type"]
            not in policy["purpose_payload_types"]["standard_submission"][envelope["purpose"]]
        ):
            raise ReceiverError("FORBIDDEN", "Core submission profile denies payload")
        known_terms = {row["id"] for row in self.catalog["semantic_bundle"]["terms"]}
        if not set(envelope["semantic_term_ids"]).issubset(known_terms) or (
            "agentfabric.core.experience_candidate" not in envelope["semantic_term_ids"]
        ):
            raise ReceiverError("SEMANTIC_PROFILE_UNKNOWN", "operative Experience term is absent")
        return request_digest(envelope, self.catalog["digest_policy"]["effective_request_fields"])


class ReceiverInbox:
    """Minimal durable test receiver using Core 0.21.1 receipt validation."""

    def __init__(self, state_root: Path, boundary: CoreReceiverBoundary, *, clock):
        self.state_root = state_root
        self.core = boundary
        self.clock = clock
        self.path = state_root / "inbox.json"
        self._lock = threading.Lock()

    def _read(self) -> dict:
        if not self.path.exists():
            return {"next_sequence": 1, "entries": {}}
        return json.loads(self.path.read_bytes())

    def _write(self, state: dict) -> None:
        self.path.write_bytes(canonical(state) + b"\n")

    def admit(self, envelope: dict, candidate: dict) -> dict:
        req_digest = self.core.validate_submission(envelope, candidate, self.clock())
        key = "knowledgeos:" + envelope["idempotency_key"]
        with self._lock:
            state = self._read()
            prior = state["entries"].get(key)
            if prior is not None:
                if prior["request_digest"] != req_digest or prior["candidate"] != candidate:
                    raise ReceiverError("IDEMPOTENCY_CONFLICT", "same key changed effective content")
                return copy.deepcopy(prior["receipt"])
            if any(row["candidate"]["candidate_id"] == candidate["candidate_id"] for row in state["entries"].values()):
                raise ReceiverError("IDEMPOTENCY_CONFLICT", "candidate ID already admitted")
            receipt = {
                "receipt_id": "receipt." + hashlib.sha256(key.encode()).hexdigest()[:24],
                "candidate_id": candidate["candidate_id"],
                "owner_operation_id": "knowledgeos",
                "recipient_operation_id": "hermestrace",
                "idempotency_key": envelope["idempotency_key"],
                "payload_digest": "sha256:" + digest(candidate),
                "disposition": "admitted",
                "admission_level": "owner_durable",
                "source_revision": f"revision.{state['next_sequence']}",
                "received_at": self.clock().isoformat(),
            }
            core_validate("ExperienceDeliveryReceipt", receipt)
            state["entries"][key] = {
                "key": key,
                "request_digest": req_digest,
                "candidate": copy.deepcopy(candidate),
                "receipt": copy.deepcopy(receipt),
            }
            state["next_sequence"] += 1
            self._write(state)
            return receipt

    def query_admission(self, *, sender_operation_id: str, idempotency_key: str, request_digest: str) -> dict | None:
        if sender_operation_id != "knowledgeos":
            raise ReceiverError("FORBIDDEN", "query sender differs from authenticated binding")
        with self._lock:
            row = self._read()["entries"].get("knowledgeos:" + idempotency_key)
        if row is None:
            return None
        if row["request_digest"] != request_digest:
            raise ReceiverError("IDEMPOTENCY_CONFLICT", "admission query content differs")
        return copy.deepcopy(row["receipt"])


class ReceiverServer(socketserver.UnixStreamServer):
    allow_reuse_address = False

    def __init__(self, socket_path: Path, credential_file: Path, inbox: ReceiverInbox):
        self.secret = load_secret(credential_file)
        self.inbox = inbox
        super().__init__(str(socket_path), _ReceiverHandler)

    def server_close(self) -> None:
        path = Path(self.server_address)
        super().server_close()
        if path.is_socket():
            path.unlink()


class _ReceiverHandler(socketserver.BaseRequestHandler):
    def handle(self) -> None:
        self.request.settimeout(5)
        try:
            request = receive_frame(self.request, self.server.secret)
        except (OSError, ValueError):
            return
        try:
            if request.get("method") == "admit" and set(request) == {"method", "envelope", "candidate"}:
                result = self.server.inbox.admit(request["envelope"], request["candidate"])
            elif request.get("method") == "query_admission" and set(request) == {
                "method",
                "sender_operation_id",
                "idempotency_key",
                "request_digest",
            }:
                result = self.server.inbox.query_admission(
                    sender_operation_id=request["sender_operation_id"],
                    idempotency_key=request["idempotency_key"],
                    request_digest=request["request_digest"],
                )
            else:
                raise ReceiverError("INVALID_PAYLOAD", "InterOps method or fields differ")
            response = {"ok": True, "result": result}
        except (ReceiverError, ValueError, TypeError, KeyError) as error:
            response = {"ok": False, "result": {
                "code": getattr(error, "code", "INVALID_PAYLOAD"),
                "message": "InterOps receiver rejected the request",
            }}
        try:
            send_frame(self.request, self.server.secret, canonical(response))
        except (OSError, ValueError):
            pass


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


def exchange(path, secret, request):
    return raw_exchange(path, secret, canonical(request), timeout=2.0)


def receiver_rows(trace):
    path = trace.state / "inbox.json"
    return list(json.loads(path.read_bytes())["entries"].values()) if path.exists() else []


@pytest.fixture(scope="session")
def boundary():
    return CoreReceiverBoundary(core_root())


@pytest.fixture
def trace(tmp_path, clock, boundary):
    state = tmp_path / "receiver-state"
    state.mkdir(mode=0o700)
    key = tmp_path / "key"
    secret = b"k" * 32
    key.write_bytes(secret)
    key.chmod(0o600)
    owner = ReceiverInbox(state, boundary, clock=clock.now)
    path = tmp_path / "h.sock"
    with ReceiverServer(path, key, owner) as server:
        thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True)
        thread.start()
        try:
            yield SimpleNamespace(state=state, path=path, key=key, secret=secret, core=boundary, owner=owner)
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
    """A faulty wire in front of the receiver, never a replacement admission authority."""
    lock = threading.Lock()
    requests = []
    hold_queries = threading.Event()
    hold_queries.set()

    class Handler(socketserver.BaseRequestHandler):
        def handle(self):
            self.request.settimeout(2)
            try:
                request = receive_frame(self.request, trace.secret)
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
                send_frame(self.request, trace.secret, canonical(response))
            except (OSError, ValueError):
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
    exported = canonical(rows)
    assert SECRET.encode() not in exported and b"Private curation title" not in exported


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
    bad["envelope"]["schema_version"] = "0.20.1"
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
    with pytest.raises((EOFError, ValueError)):
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


def test_LRN_interops_refuses_mismatched_core_manifest(make_kos, vault, tmp_path, monkeypatch):
    kos = make_kos()
    privacy_refusal(kos, vault)
    entry = candidate(kos)
    fake_core = tmp_path / "fake-core"
    (fake_core / "release").mkdir(parents=True)
    fake_manifest = {
        "core_release_version": "0.20.1",
        "files": [],
        "release_digest": "sha256:" + "0" * 64,
    }
    (fake_core / "release/manifest.json").write_bytes(canonical(fake_manifest))
    monkeypatch.setenv("KNOWLEDGEOS_CORE_ROOT", str(fake_core))
    with pytest.raises(ValueError, match="differs from KnowledgeOS adoption"):
        interops.prepare(kos.owner, entry)

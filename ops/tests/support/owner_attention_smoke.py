"""Disposable actual owner records and actual loopback HTTP; no Discord network.

Run only through ``make attention-smoke``. Emits redacted JSON evidence to stdout;
all five synthetic roots are removed before the completion marker is emitted.
"""
from __future__ import annotations

import argparse
import copy
import http.client
import json
import socket
import tempfile
import threading
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from clients.discord import DiscordWebhookClient, WebhookBinding
from test_normalization_work import _execute, _finish, _started

from vaultops.adapters.core import AdmissionError, digest
from vaultops.adapters.discord_attention import DiscordAttentionBinding
from vaultops.application.owner_attention import PREFIX, AttentionRoute, OwnerAttention
from vaultops.interfaces.attention_scanner import scan_committed_owner

MARKER = "KNOWLEDGEOS_ATTENTION_EVIDENCE="


def run_case(root: Path) -> dict:
    exops, spec, run, _source, receipt = _started(root)
    result = _execute(exops, spec, run)
    _finish(exops, run, receipt, result)
    journal = exops.application.journal
    work_key = "exops.work." + run["work_run_id"]
    committed = copy.deepcopy(journal.read()["intents"][work_key])
    route = [AttentionRoute("owner.attention", 1, 1, "https://knowledgeos.example.test/")]
    clock = [datetime.now(UTC)]
    host_evidence = {}
    state = {"mode": "confirmed", "calls": 0, "messages": {}}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            assert set(body) == {"content", "allowed_mentions"}
            assert body["allowed_mentions"] == {"parse": []}
            assert "#work_run_id=" in body["content"]
            state["calls"] += 1
            message = str(7000 + state["calls"])
            if state["mode"] == "unknown":
                # The server commits a message, then loses the response.
                state["messages"][message] = digest(body["content"].encode())
                self.connection.shutdown(socket.SHUT_RDWR)
                self.connection.close()
                return
            if state["mode"] == "rate_limit":
                self.send_response(429)
                self.send_header("Retry-After", "60")
                self.end_headers()
                self.wfile.write(b'{"retry_after":60}')
                return
            state["messages"][message] = digest(body["content"].encode())
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"id": message}).encode())

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    def local_http(_host_owned_url, payload):
        # Services still validates an exact host-owned Discord binding. Only
        # the separately supplied synthetic host HTTP connection targets local.
        connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=5)
        try:
            connection.request("POST", "/synthetic-webhook?wait=true", payload,
                               {"Content-Type": "application/json"})
            response = connection.getresponse()
            return response.status, dict(response.headers), response.read()
        finally:
            connection.close()

    def bind():
        binding = DiscordAttentionBinding(lambda authorize: DiscordWebhookClient(
            "knowledgeos", {"owner.attention": WebhookBinding(
                "owner.attention", 1, "https://discord.com/api/webhooks/123/synthetic-not-a-secret")},
            authorize, http_post=local_http))
        return OwnerAttention(replace(journal), lambda _event: route[0], binding,
                              clock=lambda: clock[0],
                              authorize_reconciliation=lambda actor, evidence:
                                  actor == "host.owner" and evidence == host_evidence)

    try:
        attention = bind()
        ids = attention.scan()
        kinds = {journal.read()["intents"][key]["event"]["event_type"]: key for key in ids}
        assert set(kinds) == {"human_wait", "evaluated_outcome"}
        wait = kinds["human_wait"]
        outcome = kinds["evaluated_outcome"]
        assert attention.attempt(wait)["state"] == "confirmed"
        calls = state["calls"]
        assert bind().attempt(wait)["state"] == "confirmed" and state["calls"] == calls
        state["mode"] = "rate_limit"
        retry = bind().attempt(outcome)
        assert retry["state"] == "retryable" and retry["retry_at"]
        calls = state["calls"]
        assert bind().attempt(outcome)["state"] == "retryable" and state["calls"] == calls
        for revoked in (replace(route[0], destination_generation=2),
                        replace(route[0], audience_generation=2), None):
            route[0] = revoked
            try:
                bind().attempt(outcome)
            except AdmissionError:
                pass
            else:
                raise AssertionError("Queued content crossed a revoked route")
            assert bind().scan() == []
            assert state["calls"] == calls
            route[0] = AttentionRoute("owner.attention", 1, 1, "https://knowledgeos.example.test/")
        clock[0] += timedelta(seconds=61)
        state["mode"] = "unknown"
        unknown = bind().attempt(outcome)
        assert unknown["state"] == "unknown"
        calls = state["calls"]
        assert bind().attempt(outcome)["state"] == "unknown" and state["calls"] == calls
        message_id = str(7000 + calls)
        assert message_id in state["messages"]
        host_evidence.update({"notice_id": outcome, "attempt_id": unknown["attempt_id"],
                              "route": unknown["route"], "message_id": message_id,
                              "delivered": True,
                              "evidence_digest": digest(json.dumps(state["messages"], sort_keys=True).encode())})
        reconciliation = {key: value for key, value in host_evidence.items() if key not in {"notice_id", "route"}}
        confirmed = bind().reconcile_unknown(outcome, actor_id="host.owner", route=route[0], **reconciliation)
        assert confirmed["state"] == "confirmed" and confirmed["message_id"] == message_id
        scan_committed_owner(bind())
        assert state["calls"] == calls
        assert journal.read()["intents"][work_key] == committed
        notices = {key: value for key, value in journal.read()["intents"].items() if key.startswith(PREFIX)}
        assert len(notices) == 2
        assert "synthetic-not-a-secret" not in json.dumps(notices)
        assert all(record["state"] == "confirmed" for record in notices.values())
        return {"status": "passed", "transport": "real-loopback-http-via-Services-send_once",
                "actual_owner_work": True, "human_wait_and_evaluated_outcome": True,
                "durable_reconstruction": True, "deduplicated": True,
                "destination_and_audience_generation_fenced": True,
                "current_export_revocation_denied": True, "persisted_rate_limit": True,
                "unknown_response_reconciled": True, "no_blind_resend": True,
                "work_outcome_unchanged_during_outage": True,
                "http_attempts": calls, "confirmed_host_messages": len(state["messages"]),
                "work_digest": record_digest_for_evidence(committed)}
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=10)


def record_digest_for_evidence(record):
    from vaultops.application.work import record_digest

    return record_digest(record)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence", type=Path, help="Exact parent-mounted disposable evidence output")
    arguments = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="knowledgeos-attention-") as temporary:
        root = Path(temporary)
        evidence = run_case(root)
    evidence["disposable_roots_removed"] = not root.exists()
    if arguments.evidence is not None:
        arguments.evidence.write_text(json.dumps(evidence, sort_keys=True, indent=2) + "\n")
    print(MARKER + json.dumps(evidence, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

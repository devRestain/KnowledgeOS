"""Consumer-visible owner attention safety over actual committed Officer records."""
from __future__ import annotations

import copy
import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from test_normalization_work import _execute, _finish, _started

from vaultops.adapters.core import AdmissionError, digest
from vaultops.application.owner_attention import (
    PREFIX,
    SUMMARIES,
    AttentionOutcome,
    AttentionRoute,
    OwnerAttention,
    committed_events,
)
from vaultops.application.work import record_digest
from vaultops.interfaces.attention_scanner import scan_committed_owner

ROUTE = AttentionRoute("owner.attention", 1, 1, "https://knowledgeos.example.test/")


class Transport:
    def __init__(self, outcomes=()):
        self.outcomes = list(outcomes)
        self.calls = []
        self.inspect = lambda notice: None

    def send_once(self, notice, authorize):
        authorize(notice)
        self.inspect(notice)
        self.calls.append(notice)
        outcome = self.outcomes.pop(0) if self.outcomes else AttentionOutcome("confirmed", str(len(self.calls)))
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


@pytest.fixture
def owner(tmp_path):
    exops, spec, run, source, receipt = _started(tmp_path)
    result = _execute(exops, spec, run)
    _finish(exops, run, receipt, result)
    return exops, run, source


def bind(owner, transport=None, *, route=None, clock=None, reconciliation=None):
    exops, _run, _source = owner
    return OwnerAttention(replace(exops.application.journal), route or (lambda _event: ROUTE),
                          transport or Transport(), clock=clock or (lambda: datetime.now(UTC)),
                          authorize_reconciliation=reconciliation)


def ids_by_type(attention):
    ids = attention.scan()
    records = attention.journal.read()["intents"]
    return {records[key]["event"]["event_type"]: key for key in ids}


def test_actual_admission_and_started_work_never_claim_completion(tmp_path):
    exops, _spec, _run, _source, _receipt = _started(tmp_path)
    transport = Transport()
    attention = bind((exops, None, None), transport)
    assert scan_committed_owner(attention) == {"admitted": [], "outcomes": {}}
    assert transport.calls == []


def test_wait_and_evaluated_outcome_are_separate_redacted_durable_notices(owner):
    transport = Transport()
    attention = bind(owner, transport)
    before = copy.deepcopy(owner[0].application.journal.read())
    ids = ids_by_type(attention)
    assert set(ids) == {"human_wait", "evaluated_outcome"}

    def inspect(notice):
        key = PREFIX + notice.notice_id
        durable = attention.journal.read()["intents"][key]
        assert durable["state"] == "unknown" and durable["attempt_count"] == 1

    transport.inspect = inspect
    first = scan_committed_owner(attention)
    assert set(first["outcomes"].values()) == {"confirmed"}
    after = attention.journal.read()
    # Reconstruction and repeated scans cannot dispatch the same message again.
    rebuilt = bind(owner, transport)
    scan_committed_owner(rebuilt)
    assert len(transport.calls) == 2
    assert all(item["message_id"] for key, item in after["receipts"].items() if key.startswith(PREFIX))
    for key, work in before["intents"].items():
        assert after["intents"][key] == work
    assert after["human_requests"] == before["human_requests"]
    assert after["decisions"] == before["decisions"]
    exported = json.dumps({key: value for key, value in after["intents"].items() if key.startswith(PREFIX)})
    assert owner[2].read_text() not in exported
    for notice in transport.calls:
        assert "#work_run_id=" in notice.content and len(notice.content) < 800
        assert "@everyone" not in notice.content and "source_reference" not in notice.content
        kind = after["intents"][PREFIX + notice.notice_id]["event"]["event_type"]
        assert notice.content == SUMMARIES[kind] + "\nhttps://knowledgeos.example.test/#work_run_id=" + owner[1]["work_run_id"]


@pytest.mark.parametrize("change", ["destination", "audience", "revoked", "url"])
def test_current_export_and_route_generation_fence_create_and_attempt(owner, change):
    selected = [ROUTE]
    transport = Transport()
    attention = bind(owner, transport, route=lambda _event: selected[0])
    ids = attention.scan()
    selected[0] = {
        "destination": replace(ROUTE, destination_generation=2),
        "audience": replace(ROUTE, audience_generation=2),
        "revoked": None,
        "url": replace(ROUTE, console_url="https://other.example.test/"),
    }[change]
    for key in ids:
        with pytest.raises(AdmissionError):
            attention.attempt(key)
    assert attention.scan() == []  # No old content inherited by a new audience.
    assert transport.calls == []
    if change == "revoked":
        fresh = bind(owner, route=lambda _event: None)
        assert fresh.scan() == []


def test_persisted_rate_limit_applies_after_owner_and_transport_reconstruction(owner):
    now = [datetime.now(UTC)]
    first = Transport([AttentionOutcome("retryable", retry_after_seconds=90)])
    attention = bind(owner, first, clock=lambda: now[0])
    ids = ids_by_type(attention)
    key = ids["evaluated_outcome"]
    retry = attention.attempt(key)
    assert retry["state"] == "retryable" and retry["retry_at"]
    fresh_transport = Transport()
    rebuilt = bind(owner, fresh_transport, clock=lambda: now[0])
    assert rebuilt.attempt(key)["state"] == "retryable"
    assert rebuilt.attempt(ids["human_wait"])["state"] == "pending"
    assert fresh_transport.calls == []
    now[0] += timedelta(seconds=91)
    assert rebuilt.attempt(key)["state"] == "confirmed"
    assert len(fresh_transport.calls) == 1


@pytest.mark.parametrize("outcome", [AttentionOutcome("unknown"), TimeoutError("credential-canary"),
                                     AdmissionError("ATTENTION_DENIED", "credential-canary"),
                                     ValueError("credential-canary"),
                                     AttentionOutcome("confirmed", "invalid"),
                                     AttentionOutcome("retryable", retry_after_seconds=float("nan"))])
def test_unknown_delivery_never_blindly_retries_or_changes_work(owner, outcome):
    transport = Transport([outcome])
    attention = bind(owner, transport)
    ids = ids_by_type(attention)
    before = copy.deepcopy(attention.journal.read()["intents"]["exops.work." + owner[1]["work_run_id"]])
    key = ids["evaluated_outcome"]
    assert attention.attempt(key)["state"] == "unknown"
    assert bind(owner, transport).attempt(key)["state"] == "unknown"
    assert len(transport.calls) == 1
    assert attention.journal.read()["intents"]["exops.work." + owner[1]["work_run_id"]] == before
    assert "credential-canary" not in attention.journal.path.read_text()


def test_unknown_reconciliation_requires_exact_trusted_destination_message_evidence(owner):
    transport = Transport([AttentionOutcome("unknown")])
    expected = {}
    attention = bind(owner, transport, reconciliation=lambda actor, evidence:
                     actor == "host.owner" and evidence == expected)
    key = ids_by_type(attention)["evaluated_outcome"]
    record = attention.attempt(key)
    kwargs = {"actor_id": "host.owner", "attempt_id": record["attempt_id"], "route": ROUTE,
              "message_id": "9001", "delivered": True, "evidence_digest": digest(b"host evidence")}
    expected.update({"notice_id": key, **{name: value for name, value in kwargs.items() if name != "actor_id"}})
    expected["route"] = record["route"]
    for replacement in ({"actor_id": "untrusted"}, {"attempt_id": "wrong"},
                        {"route": replace(ROUTE, destination_generation=2)}, {"message_id": "9002"}):
        with pytest.raises(AdmissionError):
            attention.reconcile_unknown(key, **(kwargs | replacement))
    assert attention.reconcile_unknown(key, **kwargs)["state"] == "confirmed"
    assert attention.reconcile_unknown(key, **kwargs)["message_id"] == "9001"
    assert attention.attempt(key)["message_id"] == "9001"
    assert len(transport.calls) == 1


def test_rejected_is_terminal_not_retryable(owner):
    transport = Transport([AttentionOutcome("rejected")])
    attention = bind(owner, transport)
    key = ids_by_type(attention)["evaluated_outcome"]
    assert attention.attempt(key)["state"] == "rejected"
    assert attention.attempt(key)["state"] == "rejected"
    assert len(transport.calls) == 1


def test_initial_gap_or_received_control_cannot_be_reported_as_evaluated_completion(owner):
    app = owner[0].application
    with app.journal.writer() as session:
        record = session.value["intents"]["exops.work." + owner[1]["work_run_id"]]
        # Preserve a genuine assessment but make it historical, like initial
        # Curation triage retained during a successor/control wait.
        record["curation_initial_assessment"] = {"assessment": copy.deepcopy(record["assessment"])}
        record["run"]["state"] = "running"
        record["run"]["latest_assessment_digest"] = record_digest({"initial_gap": True})
        record["run"]["pending_action_ids"] = ["command.cancel.received"]
        record["application_state"] = "started"
        session.value["intents"]["curation.control.command.cancel.received"] = {
            "state": "received", "recorded_at": datetime.now(UTC).isoformat(),
            "body": {"kind": "cancel", "command_id": "command.cancel.received"},
            "receipt": {"application_state": "received"},
        }
        session.commit(app.clock())
    kinds = {event.event_type for event in committed_events(app.journal.read(), app.clock())}
    assert "evaluated_outcome" not in kinds and "cancelled_outcome" not in kinds


def test_attention_mapping_and_unknown_intents_survive_journal_retention(owner):
    now = [datetime.now(UTC)]
    attention = bind(owner, Transport([AttentionOutcome("unknown")]), clock=lambda: now[0])
    ids = ids_by_type(attention)
    unknown = ids["evaluated_outcome"]
    confirmed = ids["human_wait"]
    attention.attempt(unknown)
    attention.attempt(confirmed)
    before = copy.deepcopy(attention.journal.read())
    now[0] += timedelta(days=2)
    with attention.journal.writer() as session:
        session.commit(now[0])
    after = attention.journal.read()
    for key in (unknown, confirmed):
        assert after["intents"][key] == before["intents"][key]
        assert after["receipts"][key] == before["receipts"][key]


def test_actual_control_wait_received_and_applied_cancel_have_distinct_attention(tmp_path, monkeypatch):
    from test_curation_control import apply, checkpoint_ready, receive

    controller, target, exops, _executor, _spec, _run, _path = checkpoint_ready(tmp_path, monkeypatch)
    attention = bind((exops, None, None))
    assert set(ids_by_type(attention)) == {"human_wait"}
    receive(controller, target)
    events = committed_events(attention.journal.read(), exops.application.clock())
    assert not any(event.event_type in {"cancelled_outcome", "evaluated_outcome", "human_wait"}
                   for event in events)
    apply(controller, target)
    assert set(ids_by_type(attention)) == {"cancelled_outcome"}
    event = next(event for event in committed_events(attention.journal.read(), exops.application.clock())
                 if event.event_type == "cancelled_outcome")
    assert event.action_id == attention.journal.read()["intents"]["exops.work." + target["work_run_id"]]["action_id"]


def test_concurrent_exact_duplicate_attempts_send_only_once(owner):
    from concurrent.futures import ThreadPoolExecutor

    transport = Transport()
    attention = bind(owner, transport)
    key = ids_by_type(attention)["evaluated_outcome"]
    with ThreadPoolExecutor(max_workers=2) as pool:
        records = list(pool.map(lambda _index: bind(owner, transport).attempt(key), range(2)))
    assert all(record["state"] == "confirmed" for record in records)
    assert len(transport.calls) == 1


def test_resolved_gate_cannot_deliver_an_obsolete_wait(owner):
    attention = bind(owner)
    key = ids_by_type(attention)["human_wait"]
    with attention.journal.writer() as session:
        for request in session.value["human_requests"].values():
            request["lifecycle"] = "resolved"
        session.commit(datetime.now(UTC))
    with pytest.raises(AdmissionError):
        attention.attempt(key)


def test_owner_generation_change_fences_queued_content(owner):
    attention = bind(owner)
    key = ids_by_type(attention)["human_wait"]
    with attention.journal.writer() as session:
        run = session.value["intents"]["exops.work." + owner[1]["work_run_id"]]["run"]
        run["generation"] += 1
        session.commit(datetime.now(UTC))
    with pytest.raises(AdmissionError):
        attention.attempt(key)


@pytest.mark.parametrize("url", ["http://knowledgeos.example.test/", "https://token@knowledgeos.example.test/",
                                  "https://knowledgeos.example.test/?token=secret",
                                  "https://knowledgeos.example.test/#secret",
                                  "https://knowledgeos.example.test/private-source"])
def test_only_authorized_bounded_console_origin_can_be_exported(owner, url):
    attention = bind(owner, route=lambda _event: replace(ROUTE, console_url=url))
    assert attention.scan() == []


def test_unexpected_transport_fault_propagates_and_keeps_durable_uncertainty(owner):
    fault = RuntimeError("credential-canary")
    transport = Transport([fault])
    attention = bind(owner, transport)
    key = ids_by_type(attention)["evaluated_outcome"]
    before = copy.deepcopy(attention.journal.read()["intents"]["exops.work." + owner[1]["work_run_id"]])
    with pytest.raises(RuntimeError) as failure:
        attention.attempt(key)
    assert failure.value is fault
    unknown = attention.journal.read()["intents"][key]
    assert unknown["state"] == "unknown" and unknown["attempt_count"] == 1
    assert bind(owner, transport).attempt(key)["state"] == "unknown"
    assert len(transport.calls) == 1
    assert attention.journal.read()["intents"]["exops.work." + owner[1]["work_run_id"]] == before
    assert "credential-canary" not in attention.journal.path.read_text()


def test_host_boundary_rechecks_current_export_instead_of_freezing_scan_grant(owner):
    route = [ROUTE]

    class RevokingTransport(Transport):
        def send_once(self, notice, authorize):
            route[0] = None
            return super().send_once(notice, authorize)

    transport = RevokingTransport()
    attention = bind(owner, transport, route=lambda _event: route[0])
    key = ids_by_type(attention)["evaluated_outcome"]
    assert attention.attempt(key)["state"] == "unknown"
    assert transport.calls == []


@pytest.mark.parametrize("unexpected", [False, True])
def test_scanner_startup_failure_is_visible_without_exporting_private_details(owner, monkeypatch, capsys, unexpected):
    import sys
    from types import ModuleType

    from vaultops.interfaces import attention_scanner

    module = ModuleType("attention_private_host_fixture")

    def compose(_journal):
        if unexpected:
            raise RuntimeError("credential-canary")
        raise OSError("credential-canary")

    module.compose = compose
    monkeypatch.setitem(sys.modules, module.__name__, module)
    monkeypatch.setattr(sys, "argv", ["attention_scanner", "--binding", module.__name__ + ":compose"])
    monkeypatch.setattr(attention_scanner, "resolve_paths", lambda: owner[0].application.roots)
    if unexpected:
        with pytest.raises(RuntimeError, match="credential-canary"):
            attention_scanner.main()
    else:
        assert attention_scanner.main() == 1
    captured = capsys.readouterr()
    assert "credential-canary" not in captured.out + captured.err


def test_public_cli_unhandled_fault_exits_unsuccessfully_without_private_traceback():
    import subprocess
    import sys

    result = subprocess.run(
        [sys.executable, "-c",
         ("import sys; from vaultops.interfaces.attention_scanner import private_failure_boundary; "
          "sys.excepthook = private_failure_boundary; raise RuntimeError('credential-canary')")],
        capture_output=True, text=True, check=False,
    )
    assert result.returncode != 0
    assert result.stdout == ""
    assert json.loads(result.stderr) == {"status": "failed", "code": "ATTENTION_UNEXPECTED"}
    assert "credential-canary" not in result.stdout + result.stderr

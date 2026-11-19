"""C8 notices: policy, current-state digests and single-attempt delivery."""

from __future__ import annotations

import json
import sys
import threading
import time
from datetime import timedelta

from conftest import conforming_note


def eventually(predicate, timeout=5):
    deadline = time.monotonic() + timeout
    while True:
        result = predicate()
        if result:
            return result
        assert time.monotonic() < deadline, "notification did not arrive"
        time.sleep(0.02)


def digest(kos):
    receipt = kos.run("digest.run", {}, actor="schedule/digest")
    assert receipt["status"] == "completed", receipt
    return receipt


def test_NTF_1_deny_notes_never_appear_in_notices_or_attention(make_kos, vault):
    sent = []
    kos = make_kos(notifier=sent.append)
    vault.write("40_Knowledge/Ideas/Forbidden title.md", "---\nai_policy: deny\ntype: idea\nstatus: dropped\n---\n<<<<<<< ours\nsecret body\n=======\nsecret\n>>>>>>> theirs\n")
    vault.write("40_Knowledge/Ideas/Allowed title.md", conforming_note("idea", "seed", body="Never export this body.\n"), age=timedelta(days=40))
    receipt = digest(kos)
    assert receipt["outcome"]["counts"]["revisit"] == 1
    payload = json.dumps(sent, ensure_ascii=False)
    assert "Allowed title" in payload
    assert "Forbidden" not in payload and "secret" not in payload
    assert "Never export this body" not in payload
    assert "Forbidden" not in json.dumps(kos.get("/api/attention")[1])
    assert any(link.startswith("obsidian://open?vault=") for notice in sent for link in notice["links"])
    assert any("/#attention" in link for notice in sent for link in notice["links"])


def test_NTF_2_unresolved_conflict_alert_once_but_both_digests(make_kos, vault, clock):
    sent = []
    kos = make_kos(notifier=sent.append)
    vault.write("40_Knowledge/Ideas/Unresolved.md", "<<<<<<< ours\nFirst body\n=======\nSecond body\n>>>>>>> theirs\n")
    # Detection must occur without a digest, job or GET initiating it.
    eventually(lambda: [notice for notice in sent if notice["kind"] == "alert"])
    digest(kos)
    clock.advance(days=1)
    digest(kos)
    alerts = [notice for notice in sent if notice["kind"] == "alert"]
    digests = [notice for notice in sent if notice["kind"] == "digest"]
    assert len(alerts) == 1 and len(digests) == 2
    assert "Unresolved" in alerts[0]["title"]
    assert all("Unresolved" in " ".join(notice["lines"]) for notice in digests)
    assert "First body" not in json.dumps(sent) and "Second body" not in json.dumps(sent)


def test_NTF_3_unknown_delivery_is_never_resent_even_after_restart(make_kos, vault):
    sent = []

    def notifier(notice):
        sent.append(notice)
        return "unknown"

    kos = make_kos(notifier=notifier)
    vault.write("40_Knowledge/Ideas/Unknown delivery.md", "<<<<<<< ours\nA\n=======\nB\n>>>>>>> theirs\n")
    eventually(lambda: [notice for notice in sent if notice["kind"] == "alert"])
    digest(kos)
    alerts = [notice for notice in kos.get("/api/notices")[1] if notice["kind"] == "alert"]
    assert len(alerts) == 1 and alerts[0]["delivery"] == "unknown"
    original_id = alerts[0]["notice_id"]
    kos.close()
    restarted = make_kos(notifier=notifier)
    digest(restarted)
    assert len([notice for notice in sent if notice["kind"] == "alert"]) == 1
    assert len([notice for notice in sent if notice["notice_id"] == original_id]) == 1
    assert len([notice for notice in sent if notice["kind"] == "digest"]) == 2
    assert "Unknown delivery" in " ".join([n for n in sent if n["kind"] == "digest"][-1]["lines"])


def test_notice_intent_precedes_callable_and_exception_is_unknown(make_kos):
    gate = threading.Event()
    release = threading.Event()

    def notifier(notice):
        gate.set()
        assert release.wait(5)
        raise OSError("delivery response lost")

    kos = make_kos(notifier=notifier)
    status, receipt = kos.submit("digest.run", {}, actor="schedule/digest")
    assert status == 202 and gate.wait(5)
    try:
        notices = kos.get("/api/notices")[1]
        assert len(notices) == 1 and notices[0]["delivery"] == "delivering"
    finally:
        release.set()
    assert kos.wait(receipt["request_id"])["status"] == "completed"
    assert kos.get("/api/notices")[1][0]["delivery"] == "unknown"
    assert len(kos.get("/api/unknowns")[1]) == 1


def test_unknown_effect_alert_is_observed_without_digest(make_kos):
    sent = []
    kos = make_kos(notifier=sent.append)
    uid = kos.owner.add_unknown("effect", {"effect_id": "unresolved-effect"})
    notice = eventually(lambda: next((n for n in sent if n["kind"] == "alert"), None))
    assert notice["item_id"] and "Unknown" in notice["title"]
    assert any("/#unknowns" in link for link in notice["links"])
    assert any(item["kind"] == "unknown" and item["unknown_id"] == uid for item in kos.get("/api/attention")[1])


def test_local_only_and_confidential_titles_do_not_leave_machine(make_kos, vault):
    sent = []
    kos = make_kos(notifier=sent.append)
    vault.write("40_Knowledge/Ideas/Local title.md", conforming_note("idea", "seed").replace("ai_policy: ask", "ai_policy: local_only"), age=timedelta(days=40))
    vault.write("40_Knowledge/Ideas/Confidential title.md", conforming_note("idea", "seed", extra="sensitivity: confidential\n"), age=timedelta(days=40))
    digest(kos)
    payload = json.dumps(sent)
    assert "Local title" not in payload and "Confidential title" not in payload
    assert "local_only" not in payload


def test_digest_lists_only_new_autofixes_since_previous_digest(make_kos, vault):
    sent = []
    kos = make_kos(notifier=sent.append)
    vault.write("00_Inbox/First.md", "Body must not leave.\n", age=timedelta(hours=1))
    kos.run("standardize.auto", {}, actor="schedule/standardize")
    assert digest(kos)["outcome"]["counts"]["autofix"] == 1
    # Same frozen clock timestamp still distinguishes newly recorded changes.
    vault.write("00_Inbox/Second.md", "Another hidden body.\n", age=timedelta(hours=1))
    kos.run("standardize.auto", {}, actor="schedule/standardize")
    assert digest(kos)["outcome"]["counts"]["autofix"] == 1
    assert digest(kos)["outcome"]["counts"]["autofix"] == 0
    assert "Body must not leave" not in json.dumps(sent)


def test_notifier_command_exit_codes(make_kos):
    for exit_code, expected in ((0, "delivered"), (3, "not_delivered"), (2, "unknown")):
        kos = make_kos(settings={"C8": {"notifier_command": [sys.executable, "-c", f"import json,sys; n=json.load(sys.stdin); assert n['kind']=='digest'; sys.exit({exit_code})"]}})
        receipt = digest(kos)
        notice = next(n for n in kos.get("/api/notices")[1] if n["notice_id"] == receipt["outcome"]["notice_id"])
        assert notice["delivery"] == expected
        kos.close()

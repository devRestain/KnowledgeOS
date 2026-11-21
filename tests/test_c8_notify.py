"""C8 notices: policy, current-state digests and single-attempt delivery."""

from __future__ import annotations

import json
import sys
import threading
import time
from datetime import timedelta

from conftest import classifier_script, conforming_note, organize_scripts
from test_c7_jobs import run_job

from knowledgeos.roles import RoleUnavailable
from knowledgeos.util import iso


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
    vault.write("00_Inbox/Review me.md", conforming_note("capture", "unprocessed"))
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


def test_NTF_4_failed_jobs_never_alert_and_quiet_digest_sends_nothing(make_kos, vault, clock):
    sent = []
    kos = make_kos(notifier=sent.append)
    path = "00_Inbox/Fresh.md"
    vault.write(path, "---\nai_policy: approve\n---\nCapture to classify.\n")
    organize_scripts(kos.runner, graph=("scout", "classifier"))

    def unavailable(session):
        raise RoleUnavailable("configured classifier route is unavailable")

    kos.runner.script("classifier", unavailable)
    failed, job = run_job(kos, "curate")
    assert failed["status"] == "failed" and job["last_outcome"] == "failed"
    quiet = digest(kos)
    assert quiet["outcome"]["notice_id"] is None
    assert quiet["outcome"]["counts"]["failed_jobs"] == 1
    assert quiet["outcome"]["counts"]["attention"] == 0
    assert kos.owner.store.get("digest", "cursor")["at"] == iso(clock.now())
    assert sent == [] and kos.get("/api/notices")[1] == []

    kos.runner.script("classifier", classifier_script())
    organized = kos.run("organize", {"paths": [path], "digests": {path: vault.sha(path)}})
    assert organized["status"] == "completed" and len(organized["outcome"]["proposals"]) == 1
    status, repeated = kos.post("/api/requests", {
        "request_id": quiet["request_id"], "command": "digest.run", "params": {}, "actor": "schedule/digest",
    })
    assert status == 200 and repeated == quiet and sent == []
    review = digest(kos)
    assert review["outcome"]["counts"]["proposals"] == 1
    assert review["outcome"]["counts"]["failed_jobs"] == 1
    assert len(sent) == 1 and sent[0]["kind"] == "digest"
    assert sent[0]["title"] == "KnowledgeOS 검토 요약"
    assert any(line.startswith("검토할 제안: 1 — ") for line in sent[0]["lines"])
    assert sent[0]["lines"].count("자동 작업 점검 필요: curate") == 1
    assert not any("missed job" in line or "blocked job" in line for line in sent[0]["lines"])
    assert "autofix" not in review["outcome"]["counts"]
    completed, job = run_job(kos, "curate")
    assert completed["status"] == "completed" and job["last_outcome"] == "completed"
    assert digest(kos)["outcome"]["counts"]["failed_jobs"] == 0
    assert not any(line.startswith("자동 작업 점검 필요:") for line in sent[-1]["lines"])
    assert all(notice["kind"] == "digest" for notice in sent)


def test_notice_intent_precedes_callable_and_exception_is_unknown(make_kos, vault):
    gate = threading.Event()
    release = threading.Event()

    def notifier(notice):
        gate.set()
        assert release.wait(5)
        raise OSError("delivery response lost")

    kos = make_kos(notifier=notifier)
    vault.write("00_Inbox/Review me.md", conforming_note("capture", "unprocessed"))
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


def test_NTF_5_unknown_model_step_never_alerts_but_unknown_vault_effect_does(make_kos, vault):
    sent = []
    kos = make_kos(notifier=sent.append)
    path = "40_Knowledge/Ideas/Unknown write.md"
    vault.write(path, conforming_note("idea", "seed"))
    quiet_ids = {
        kos.owner.add_unknown(kind, ref)
        for kind, ref in (
            ("runtime_phase", {"work_id": "classifier-work", "phases": ["s0.produce.classifier"]}),
            ("runtime_phase", {"work_id": "producer-work", "phase": "s0.produce.classifier"}),
            ("runtime_phase", {"work_id": "empty-work", "phases": []}),
            ("role_invocation", {"work_id": "role-work", "role": "classifier"}),
            ("request", {"request_id": "unknown-request"}),
            ("experience_delivery", {"work_id": "delivery-work"}),
            ("notice", {"notice_id": "unknown-notice"}),
        )
    }
    effect_id = kos.owner.add_unknown("effect", {"effect_id": "unknown-write", "path": path})
    notice = eventually(lambda: next((n for n in sent if n["kind"] == "alert"), None))
    # A second scan must not alert again or turn the State-only unknowns into alerts.
    digest(kos)
    items = {item["unknown_id"]: item for item in kos.get("/api/attention")[1] if item["kind"] == "unknown"}
    assert quiet_ids | {effect_id} <= items.keys()
    assert notice["item_id"] == items[effect_id]["item_id"]
    assert any(link.startswith("obsidian://") for link in notice["links"])
    assert [n for n in sent if n["kind"] == "alert"] == [notice]


def test_unknown_actions_cancellations_and_commit_phases_alert_once(make_kos):
    sent = []
    kos = make_kos(notifier=sent.append)
    unknown_ids = {
        kos.owner.add_unknown(kind, ref)
        for kind, ref in (
            ("action", {"work_id": "action-work", "action_id": "unknown-action"}),
            ("cancellation", {"work_id": "cancelled-work"}),
            ("runtime_phase", {"work_id": "commit-work", "phase": "commit.0"}),
            ("runtime_phase", {"work_id": "recovered-work", "phases": ["s0.produce.classifier", "commit.1"]}),
        )
    }
    eventually(lambda: len([n for n in sent if n["kind"] == "alert"]) == len(unknown_ids))
    digest(kos)
    expected = {item["item_id"] for item in kos.get("/api/attention")[1]
                if item.get("unknown_id") in unknown_ids}
    alerts = [n for n in sent if n["kind"] == "alert"]
    assert len(alerts) == len(unknown_ids)
    assert {notice["item_id"] for notice in alerts} == expected


def test_retired_policy_is_hidden_but_confidential_approve_title_may_leave(make_kos, vault):
    sent = []
    kos = make_kos(notifier=sent.append)
    vault.write("40_Knowledge/Ideas/Local title.md", conforming_note("idea", "seed", policy="local_only"), age=timedelta(days=40))
    vault.write("40_Knowledge/Ideas/Confidential title.md", conforming_note("idea", "seed", extra="sensitivity: confidential\n"), age=timedelta(days=40))
    vault.write("00_Inbox/Review me.md", conforming_note("capture", "unprocessed"))
    digest(kos)
    assert len(sent) == 1 and sent[0]["kind"] == "digest"
    payload = json.dumps(sent)
    assert "Local title" not in payload and "Confidential title" in payload
    assert "local_only" not in payload


def test_digest_lists_only_reports_created_after_previous_digest(make_kos, vault, clock):
    sent = []
    kos = make_kos(notifier=sent.append)
    vault.write("01_AI_Review/Reports/First.md",
                conforming_note("report", "complete", body="Report body must not leave.\n"))
    first = digest(kos)
    assert first["outcome"]["counts"]["reports"] == 1
    assert any(line.startswith("새 보고서: 1 — First") for line in sent[-1]["lines"])
    clock.advance(hours=1)
    quiet = digest(kos)
    assert quiet["outcome"]["counts"]["reports"] == 0 and quiet["outcome"]["notice_id"] is None
    assert kos.owner.store.get("digest", "cursor")["at"] == iso(clock.now())
    vault.write("01_AI_Review/Reports/At cursor.md", conforming_note("report", "complete").replace(
        "2026-11-01T09:00:00+09:00", iso(clock.now())))
    assert digest(kos)["outcome"]["notice_id"] is None
    clock.advance(hours=1)
    vault.write("01_AI_Review/Reports/New.md", conforming_note("report", "complete").replace(
        "2026-11-01T09:00:00+09:00", iso(clock.now())))
    vault.write("00_Inbox/Fresh.md", "Body must not leave.\n", age=timedelta(hours=1))
    assert kos.run("standardize.auto", {}, actor="schedule/standardize")["status"] == "completed"
    latest = digest(kos)
    assert latest["outcome"]["counts"]["reports"] == 1
    assert "autofix" not in latest["outcome"]["counts"]
    assert sent[-1]["lines"] == ["새 보고서: 1 — New"]
    assert len(sent) == 2
    assert "body must not leave" not in json.dumps(sent).lower()
    status, repeated = kos.post("/api/requests", {
        "request_id": latest["request_id"], "command": "digest.run", "params": {}, "actor": "schedule/digest",
    })
    assert status == 200 and repeated == latest and len(sent) == 2


def test_revisit_alone_is_quiet(make_kos, vault):
    sent = []
    kos = make_kos(notifier=sent.append)
    vault.write("40_Knowledge/Ideas/Revisit.md", conforming_note("idea", "seed"), age=timedelta(days=40))
    receipt = digest(kos)
    assert receipt["outcome"]["counts"]["revisit"] == 1
    assert receipt["outcome"]["notice_id"] is None and sent == []


def test_digest_pending_work_uses_console_link_without_disclosing_note(make_kos, vault):
    sent = []
    kos = make_kos(notifier=sent.append)
    path = "40_Knowledge/Ideas/Private title.md"
    sha = vault.write(path, "---\nai_policy: approve\n---\nPrivate body.\n")
    preview = kos.run("standardize.preview", {"paths": [path], "digests": {path: sha}})
    assert preview["status"] == "completed", preview
    receipt = digest(kos)
    assert receipt["outcome"]["counts"]["pending"] == 1
    assert sent[0]["lines"] == ["확인 대기 작업: 1"]
    assert any(link.endswith("/#works") for link in sent[0]["links"])
    assert "Private" not in json.dumps(sent)
    assert kos.run("work.cancel", {"work_id": preview["work_id"]})["status"] == "completed"
    quiet = digest(kos)
    assert quiet["outcome"]["counts"]["pending"] == 0 and quiet["outcome"]["notice_id"] is None
    assert len(sent) == 1


def test_notifier_command_exit_codes(make_kos, vault):
    vault.write("00_Inbox/Review me.md", conforming_note("capture", "unprocessed"))
    for exit_code, expected in ((0, "delivered"), (3, "not_delivered"), (2, "unknown")):
        kos = make_kos(settings={"C8": {"notifier_command": [sys.executable, "-c", f"import json,sys; n=json.load(sys.stdin); assert n['kind']=='digest'; sys.exit({exit_code})"]}})
        receipt = digest(kos)
        notice = next(n for n in kos.get("/api/notices")[1] if n["notice_id"] == receipt["outcome"]["notice_id"])
        assert notice["delivery"] == expected
        kos.close()

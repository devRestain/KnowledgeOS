"""C9 acceptance: role-scoped feedback and bounded, receipt-correlated Experience delivery."""

from __future__ import annotations

import json
import time

import pytest
from conftest import conforming_note, core_validate, enhance_scripts, evaluator_script, organize_scripts

from knowledgeos.roles import RoleOutcomeUnknown
from operationkit.canonical import digest

SECRET = "PRIVATE_NOTE_BODY_6a3d7f"
DENIED = "40_Knowledge/Ideas/Private note.md"
IDEA = "40_Knowledge/Ideas/Tools.md"


def eventually(read, predicate, timeout=5):
    deadline = time.monotonic() + timeout
    while True:
        value = read()
        if predicate(value):
            return value
        assert time.monotonic() < deadline, value
        time.sleep(0.02)


def experience(kos):
    status, items = kos.get("/api/experience")
    assert status == 200, items
    return items


def signaled(kos, category):
    return eventually(lambda: experience(kos), lambda rows: any(r["signal"] == category and r["candidate"] for r in rows))


def privacy_refusal(kos, vault, request_id=None):
    if not vault.path(DENIED).exists():
        vault.write(DENIED, conforming_note("idea", "seed", body=SECRET + "\n", policy="deny"))
    status, receipt = kos.submit("enhance", {"mode": "develop", "path": DENIED}, request_id=request_id)
    assert status == 202, receipt
    result = kos.wait(receipt["request_id"])
    assert result["status"] == "refused", result
    return result


def validate_entry(entry):
    core_validate("ExperienceSourceEvent", entry["source_event"])
    core_validate("ExperienceSourceReference", entry["source_event"]["source_record"])
    core_validate("ExperienceCandidate", entry["candidate"])
    core_validate("ExperienceOutboxEntry", entry["outbox"])
    for artifact in entry["candidate"]["evidence_references"]:
        core_validate("ArtifactReference", artifact)
    assert entry["outbox"]["payload_digest"] == "sha256:" + digest(entry["candidate"])
    core_validate("ExperienceSourceReference", entry["candidate"]["source_record"])
    assert entry["candidate"]["source_record"]["record_id"] == entry["source_event"]["event_id"]
    assert entry["candidate"]["source_record"]["content_digest"] == "sha256:" + digest(entry["source_event"])
    assert entry["candidate"]["source_revision"] == entry["candidate"]["source_record"]["source_revision"]
    assert entry["source_event"]["source_record"]["content_digest"] == "sha256:" + entry["source_blob"]


def test_LRN_1_rejected_curation_proposal_reaches_next_classifier(kos, vault):
    target = "00_Inbox/Private learning title.md"
    sha = vault.write(target, conforming_note("capture", "unprocessed", body=SECRET + "\n"))
    vault.write("40_Knowledge/Notes/Related.md", conforming_note("knowledge", "seed", body="A related observation.\n"))
    organize_scripts(kos.runner)
    classify = kos.runner.scripts["classifier"]
    feedback = []

    def classifier(session):
        feedback.append(session.call("feedback")["items"])
        classify(session)

    kos.runner.script("classifier", classifier)
    first = kos.run("organize", {"paths": [target], "digests": {target: sha}})
    assert first["status"] == "completed", first
    proposals = list(vault.path("01_AI_Review/Proposals").glob("*.md"))
    assert len(proposals) == 1
    proposal = proposals[0].relative_to(vault.root).as_posix()
    rejected = kos.run("review.reject", {"path": proposal, "digest": vault.sha(proposal)})
    assert rejected["status"] == "completed", rejected
    second = kos.run("organize", {"paths": [target], "digests": {target: sha}})
    assert second["status"] == "completed", second
    assert len(feedback) == 2
    assert any(row["kind"] == "proposal_change" and row["outcome"] == "rejected"
               and row["work_id"] == first["work_id"] for row in feedback[-1])
    assert all("role" not in row for row in feedback[-1])
    entries = signaled(kos, "proposal_rejected")
    entry = next(e for e in entries if e["signal"] == "proposal_rejected")
    validate_entry(entry)
    serialized = json.dumps(entries, ensure_ascii=False)
    assert SECRET not in serialized and "Private learning title" not in serialized
    status, rows = kos.get("/api/feedback?role=eval_officer")
    assert status == 200
    assert any(row["kind"] == "eval_overruled" and row["work_id"] == first["work_id"] for row in rows)


def test_LRN_3_unreachable_hermestrace_does_not_block_user_capabilities(make_kos, vault, tmp_path):
    key = tmp_path / "hermestrace-key"
    key.write_bytes(b"k" * 32)
    key.chmod(0o600)
    sockets = tmp_path / "sockets"
    sockets.mkdir(mode=0o700)
    kos = make_kos(settings={"C9": {"hermestrace_socket": str(sockets / "missing.sock"),
                                  "hermestrace_credential_file": str(key),
                                  "delivery_interval_seconds": 0.02}})
    privacy_refusal(kos, vault)
    signaled(kos, "privacy_refusal")
    entries = eventually(lambda: experience(kos),
                         lambda rows: any((e["outbox"] or {}).get("last_error_code") == "UNAVAILABLE" for e in rows))
    assert all(e["delivery"] == "pending" for e in entries)
    note = "40_Knowledge/Ideas/Public.md"
    sha = vault.write(note, conforming_note("idea", "seed", body="Accessible search canary.\n"))
    preview = kos.run("standardize.preview", {"paths": [note], "digests": {note: sha}})
    assert preview["status"] == "completed", preview
    result = kos.search(mode="keyword", query="Accessible search canary")
    assert result["status"] == "ok" and [r["path"] for r in result["results"]] == [note]
    status, health = kos.get("/api/health")
    assert status == 200 and health["ready"]
    assert health["dependencies"]["hermestrace"] == "unreachable"
    assert experience(kos)[0]["delivery"] == "pending"


def test_LRN_core_records_outbox_bound_and_retention(make_kos, vault, clock):
    kos = make_kos(settings={"C9": {"outbox_limit": 2, "retention_days": 30,
                                              "delivery_interval_seconds": 0.02}})
    first_id = None
    for index in range(3):
        privacy_refusal(kos, vault, request_id=f"private-{index}")
        entries = signaled(kos, "privacy_refusal")
        eventually(lambda: experience(kos),
                   lambda rows, index=index: any(r["source"]["id"] == f"private-{index}" and r["candidate"] for r in rows))
        if index == 0:
            first_id = entries[0]["candidate_id"]
        clock.advance(seconds=1)
    entries = experience(kos)
    assert len(entries) == 2 and first_id not in {e["candidate_id"] for e in entries}
    for entry in entries:
        validate_entry(entry)
        assert entry["delivery"] == "pending"
        status, one = kos.get(f"/api/experience/{entry['candidate_id']}")
        assert status == 200 and one == entry
    status, health = kos.get("/api/health")
    assert status == 200 and health["dependencies"]["hermestrace"] == "unconfigured"
    clock.advance(days=31)
    eventually(lambda: experience(kos), lambda rows: not rows)




def test_LRN_paused_job_is_a_blocked_job_signal(kos):
    kos.run("job.pause", {"job": "digest"})
    blocked = kos.run("job.run", {"job": "digest"}, actor="schedule/digest")
    assert blocked["status"] == "refused", blocked
    entries = signaled(kos, "job_blocked")
    entry = next(e for e in entries if e["signal"] == "job_blocked")
    validate_entry(entry)
    assert entry["candidate"]["category"] == "policy_denial"
    assert entry["candidate"]["outcome_state"] == "denied"
    assert kos.run("job.run", {"job": "digest"}, actor="schedule/digest")["status"] == "refused"
    assert len([e for e in experience(kos) if e["signal"] == "job_blocked"]) == 1


@pytest.mark.parametrize("state", ["unmet", "failed", "unknown"])
def test_LRN_unsuccessful_work_end_states_signal(kos, vault, state):
    vault.write(IDEA, conforming_note("idea", "seed", body="Tools change our thinking.\n"))
    if state != "failed":
        enhance_scripts(kos.runner)
        if state == "unmet":
            kos.runner.script("eval_officer", evaluator_script({"enh.addresses_focus": "No focus support"}))
        else:
            def unknown(session):
                raise RoleOutcomeUnknown("response lost")

            kos.runner.script("connector", unknown)
    result = kos.run("enhance", {"mode": "develop", "path": IDEA})
    assert result["status"] == state, result
    entries = signaled(kos, "work_" + state)
    entry = next(e for e in entries if e["signal"] == "work_" + state)
    validate_entry(entry)
    assert entry["source"]["id"] == result["work_id"]


def test_LRN_manager_assessments_teamreview_and_synthesizer_edits(make_kos, vault):
    kos = make_kos(settings={"C9": {"outbox_limit": 10, "delivery_interval_seconds": 0.02}})
    vault.write(IDEA, conforming_note("idea", "seed", body="Tools change our thinking.\n"))
    enhance_scripts(kos.runner, proposals=("Add a connector pass", "Consider a stronger challenge"))
    result = kos.run("enhance", {"mode": "develop", "path": IDEA})
    assert result["status"] == "completed", result
    entries = eventually(lambda: experience(kos),
                         lambda rows: len([e for e in rows if e["signal"] == "teamreview_proposal"]) == 2)
    proposals = [e for e in entries if e["signal"] == "teamreview_proposal"]
    assert len({e["event_id"] for e in proposals}) == 2
    for entry in proposals:
        core_validate("ExperienceSourceEvent", entry["source_event"])
        core_validate("ExperienceSourceReference", entry["source_event"]["source_record"])
        assert entry["source_event"]["category"] == "routine_progress"
        assert entry["source_event"]["evaluation_state"] == "not_evaluated"
        assert entry["delivery"] == "local_only"
        assert entry["candidate_id"] is None and entry["candidate"] is None and entry["outbox"] is None
        assert entry["experience_id"] == entry["event_id"]
        status, observed = kos.get(f"/api/experience/{entry['event_id']}")
        assert status == 200 and observed == entry
    status, work = kos.get(f"/api/works/{result['work_id']}")
    assert status == 200
    assert [p["text"] for p in work["team_review"]["proposals"]] == [
        "Add a connector pass", "Consider a stronger challenge",
    ]
    status, rows = kos.get("/api/feedback?role=thinking_manager")
    assert status == 200
    assert any(r["kind"] == "assessment" and r["outcome"] == "satisfied" for r in rows)
    assert any(r["kind"] == "team_review" and r["detail"]["proposals"] == 2 for r in rows)
    report = result["outcome"]["report"]
    vault.write(report, vault.read(report) + b"\nA user edit.\n")
    eventually(lambda: kos.get("/api/feedback?role=synthesizer")[1],
               lambda rows: any(r["kind"] == "report" and r["outcome"] == "edited" for r in rows))
    report_script = kos.runner.scripts["synthesizer"]
    feedback = []

    def synthesize(session):
        feedback.extend(session.call("feedback")["items"])
        report_script(session)

    kos.runner.script("synthesizer", synthesize)
    assert kos.run("enhance", {"mode": "develop", "path": IDEA})["status"] == "completed"
    assert any(r["kind"] == "report" and r["outcome"] == "edited" and r["work_id"] == result["work_id"] for r in feedback)
    assert all(r["kind"] not in ("assessment", "team_review") for r in feedback)


def test_LRN_teamreview_stays_local_while_user_correction_becomes_candidate(make_kos, vault, clock):
    kos = make_kos(settings={"C9": {"outbox_limit": 10, "delivery_interval_seconds": 0.02}})
    path = "00_Inbox/Idea.md"
    sha = vault.write(path, conforming_note("capture", "unprocessed", body=SECRET + "\n"))
    organize_scripts(kos.runner, proposals=("Consider a different classification strategy",))
    organized = kos.run("organize", {"paths": [path], "digests": {path: sha}})
    assert organized["status"] == "completed", organized
    rows = eventually(lambda: experience(kos),
                      lambda rows: any(e["signal"] == "teamreview_proposal" for e in rows))
    local = next(e for e in rows if e["signal"] == "teamreview_proposal")
    core_validate("ExperienceSourceEvent", local["source_event"])
    assert local["source_event"]["category"] == "routine_progress"
    assert local["source_event"]["evaluation_state"] == "not_evaluated"
    assert local["delivery"] == "local_only"
    assert local["candidate"] is None and local["outbox"] is None and local["candidate_id"] is None
    proposals = list(vault.path("01_AI_Review/Proposals").glob("*.md"))
    assert len(proposals) == 1
    proposal = proposals[0].relative_to(vault.root).as_posix()
    rejected = kos.run("review.reject", {"path": proposal, "digest": vault.sha(proposal)})
    assert rejected["status"] == "completed", rejected
    rows = eventually(lambda: experience(kos),
                      lambda rows: any(e["signal"] == "proposal_rejected" and e["candidate"] for e in rows))
    correction = next(e for e in rows if e["signal"] == "proposal_rejected")
    validate_entry(correction)
    assert correction["candidate"]["category"] == "human_correction"
    assert correction["delivery"] == "pending"
    assert SECRET not in json.dumps(correction["candidate"])
    status, retained = kos.get(f"/api/experience/{local['event_id']}")
    assert status == 200 and retained["delivery"] == "local_only"
    assert retained["candidate"] is None and retained["outbox"] is None
    clock.advance(days=31)
    eventually(lambda: experience(kos), lambda rows: not rows)


@pytest.mark.parametrize("outcome", ["promoted", "deleted"])
def test_LRN_synthesizer_sees_report_promotion_or_deletion(make_kos, vault, outcome):
    kos = make_kos(settings={"C9": {"delivery_interval_seconds": 0.02}})
    vault.write(IDEA, conforming_note("idea", "seed", body="Tools change our thinking.\n"))
    enhance_scripts(kos.runner)
    result = kos.run("enhance", {"mode": "develop", "path": IDEA})
    assert result["status"] == "completed", result
    report = result["outcome"]["report"]
    if outcome == "deleted":
        vault.path(report).unlink()
    else:
        vault.write(report, vault.read(report).replace(b"type: report\n", b"type: knowledge\n", 1))
    eventually(lambda: kos.get("/api/feedback?role=synthesizer")[1],
               lambda rows: any(row["kind"] == "report" and row["outcome"] == outcome
                                and row["work_id"] == result["work_id"] for row in rows))

"""Console administration through the same admitted requests as all user surfaces."""
from __future__ import annotations

import json

import pytest


def retained_unknown(tmp_path):
    """An unresolved transport outcome retained from an earlier Owner process."""
    record = {"unknown_id": "unknown-retained", "kind": "notice", "ref": {"notice_id": "notice-retained"},
              "observed": "unknown", "recorded_at": "2026-11-01T09:00:00+09:00", "resolution": None}
    directory = tmp_path / "state/unknowns"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "unknown-retained.json").write_text(json.dumps(record))
    return record


def test_SUR_4_cancel_waiting_preview_records_applied_and_writes_nothing(kos, vault, tmp_path):
    path = "40_Knowledge/Ideas/Waiting.md"
    sha = vault.write(path, "A raw idea.\n")
    preview = kos.run("standardize.preview", {"paths": [path], "digests": {path: sha}})
    assert preview["status"] == "completed"
    work_id = preview["work_id"]
    assert kos.get("/api/works/" + work_id)[1]["status"] == "awaiting_confirmation"
    before = vault.snapshot()
    status, received = kos.submit("work.cancel", {"work_id": work_id})
    assert status == 202 and received["status"] == "received"
    cancelled = kos.wait(received["request_id"])
    assert cancelled["status"] == "completed"
    assert cancelled["outcome"]["status"] == "cancelled" and cancelled["outcome"]["state"] == "applied"
    assert kos.get("/api/works/" + work_id)[1]["status"] == "cancelled"
    action = json.loads((tmp_path / "state/actions" / f"{cancelled['outcome']['action_id']}.json").read_text())
    assert action["action"] == "work.cancel" and action["state"] == "applied"
    assert action["request_id"] == received["request_id"] and action["work_id"] == work_id
    assert vault.snapshot() == before and kos.changes() == []
    assert kos.run("standardize.confirm", {"preview_id": work_id})["status"] == "refused"
    assert vault.snapshot() == before


@pytest.mark.parametrize("resolution", ["applied", "not_applied"])
def test_SUR_4_reconcile_retained_unknown_records_human_observation(make_kos, vault, tmp_path, resolution):
    original = retained_unknown(tmp_path)
    kos = make_kos()
    before = vault.snapshot()
    status, unknowns = kos.get("/api/unknowns")
    assert status == 200 and unknowns == [original]
    status, received = kos.submit("outcome.reconcile", {"unknown_id": original["unknown_id"], "resolution": resolution})
    assert status == 202 and received["status"] == "received"
    done = kos.wait(received["request_id"])
    assert done["status"] == "completed" and done["outcome"]["state"] == "applied"
    [record] = kos.get("/api/unknowns")[1]
    assert record["resolution"] == resolution and record["resolved_by"] == "user/local"
    assert record["ref"] == original["ref"] and record["observed"] == "unknown"
    action = json.loads((tmp_path / "state/actions" / f"{done['outcome']['action_id']}.json").read_text())
    assert action["action"] == "outcome.reconcile" and action["state"] == "applied"
    assert action["request_id"] == received["request_id"]
    repeated = kos.post("/api/requests", {"request_id": received["request_id"], "command": "outcome.reconcile",
                                         "params": {"unknown_id": original["unknown_id"], "resolution": resolution}})
    assert repeated == (200, done)
    changed = kos.run("outcome.reconcile", {"unknown_id": original["unknown_id"], "resolution": "not_applied" if resolution == "applied" else "applied"})
    assert changed["status"] == "refused"
    assert kos.get("/api/unknowns")[1] == [record]
    assert vault.snapshot() == before


def test_SUR_4_reconcile_missing_or_invalid_unknown_is_refused(kos):
    for params in ({"unknown_id": "absent", "resolution": "applied"},
                   {"unknown_id": "../outside", "resolution": "applied"},
                   {"unknown_id": "absent", "resolution": "retry"}):
        assert kos.run("outcome.reconcile", params)["status"] == "refused"


def test_SUR_4_consideration_without_teamreview_is_refused(kos, vault):
    path = "40_Knowledge/Ideas/No review.md"
    sha = vault.write(path, "Raw idea\n")
    preview = kos.run("standardize.preview", {"paths": [path], "digests": {path: sha}})
    before = vault.snapshot()
    for index in (0, -1, 8):
        refused = kos.run("teamreview.considered", {"work_id": preview["work_id"], "proposal_index": index})
        assert refused["status"] == "refused"
    assert kos.get("/api/works/" + preview["work_id"])[1]["team_review"] is None
    assert vault.snapshot() == before


@pytest.mark.parametrize("actor", ["schedule/standardize", "operation/research"])
@pytest.mark.parametrize(("command", "params"), [
    ("work.cancel", {"work_id": "absent"}),
    ("outcome.reconcile", {"unknown_id": "absent", "resolution": "applied"}),
    ("teamreview.considered", {"work_id": "absent", "proposal_index": 0}),
])
def test_SUR_4_console_actions_are_user_only(kos, actor, command, params):
    refused = kos.run(command, params, actor=actor)
    assert refused["status"] == "refused"
    assert "not available" in refused["notice"]


def test_SUR_4_teamreview_consideration_marks_only_the_selected_proposal(kos, vault, tmp_path):
    from conftest import conforming_note, enhance_scripts

    path = "40_Knowledge/Ideas/Idea.md"
    vault.write(path, conforming_note("idea", "seed", body="Tools shape how we think.\n"))
    enhance_scripts(kos.runner, proposals=("Consider a counterpoint pass", "Review evidence breadth"))
    completed = kos.run("enhance", {"mode": "develop", "path": path})
    assert completed["status"] == "completed", completed
    work_id = completed["work_id"]
    work = kos.get("/api/works/" + work_id)[1]
    assert work["team_review"]["status"] == "recorded"
    assert all(not proposal["considered"] for proposal in work["team_review"]["proposals"])
    before = vault.snapshot()
    roles = kos.get("/api/roles")[1]
    status, received = kos.submit("teamreview.considered", {"work_id": work_id, "proposal_index": 0})
    assert status == 202 and received["status"] == "received"
    done = kos.wait(received["request_id"])
    assert done["status"] == "completed" and done["outcome"]["state"] == "applied"
    changed = kos.get("/api/works/" + work_id)[1]
    proposals = changed["team_review"]["proposals"]
    assert proposals[0]["considered"] is True and proposals[0]["considered_at"]
    assert proposals[0]["text"] == work["team_review"]["proposals"][0]["text"]
    assert proposals[1] == work["team_review"]["proposals"][1]
    assert changed["status"] == "completed" and changed["segments"] == work["segments"]
    action_id = done["outcome"]["action_id"]
    assert changed["actions"].count(action_id) == 1
    action = json.loads((tmp_path / "state/actions" / f"{action_id}.json").read_text())
    assert action["action"] == "teamreview.considered" and action["state"] == "applied"
    assert kos.post("/api/requests", {"request_id": received["request_id"], "command": "teamreview.considered",
                                      "params": {"work_id": work_id, "proposal_index": 0}}) == (200, done)
    refused = kos.run("teamreview.considered", {"work_id": work_id, "proposal_index": 9})
    assert refused["status"] == "refused"
    assert kos.get("/api/works/" + work_id)[1] == changed
    assert kos.get("/api/roles")[1] == roles and vault.snapshot() == before

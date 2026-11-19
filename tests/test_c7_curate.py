"""Curate scheduled job acceptance through trigger and child request receipts."""

from __future__ import annotations

from datetime import timedelta

from conftest import classifier_script, organize_scripts
from test_c7_jobs import run_job

from knowledgeos.roles import RoleUnavailable


def test_JOB_2_curate_takes_ten_oldest_then_remaining_five(kos, vault):
    paths = [f"10_Drafts/Note {number:02}.md" for number in range(15)]
    for index, path in enumerate(paths):
        vault.write(path, f"---\nai_policy: remote_ok\n---\nA unique thought number {index}.\n", age=timedelta(days=15 - index))
    organize_scripts(kos.runner, graph=("scout", "classifier"))
    first, job = run_job(kos, "curate")
    assert first["status"] == "completed", first
    first_work = kos.get(f"/api/works/{first['work_id']}")[1]
    first_paths = [item["path"] for item in first_work["workspec"]["inputs"]]
    assert first_paths == paths[:10]
    assert first["outcome"]["remaining"] == 5 and len(first["outcome"]["proposals"]) == 10
    assert job["last_outcome"] == "completed"
    second, _ = run_job(kos, "curate")
    assert second["status"] == "completed", second
    second_work = kos.get(f"/api/works/{second['work_id']}")[1]
    second_paths = [item["path"] for item in second_work["workspec"]["inputs"]]
    assert second_paths == paths[10:] and set(first_paths).isdisjoint(second_paths)
    assert second["outcome"]["remaining"] == 0
    assert len(list(vault.root.glob("01_AI_Review/Proposals/*.md"))) == 15
    before = vault.snapshot()
    invocations = list(kos.runner.invocations)
    third, _ = run_job(kos, "curate")
    assert third["status"] == "completed" and third["outcome"]["proposals"] == []
    assert third["work_id"] is None and vault.snapshot() == before
    assert kos.runner.invocations == invocations


def test_JOB_3_unavailable_route_fails_visibly_without_proposals_or_retry(kos, vault):
    paths = ["00_Inbox/One.md", "00_Inbox/Two.md"]
    for path in paths:
        vault.write(path, "---\nai_policy: remote_ok\n---\nCapture to classify.\n")
    organize_scripts(kos.runner, graph=("scout", "classifier"))
    def unavailable(session):
        raise RoleUnavailable("configured classifier route is unavailable")
    kos.runner.script("classifier", unavailable)
    before = vault.snapshot()
    child, job = run_job(kos, "curate")
    assert child["status"] == "failed", child
    assert "classifier" in child["notice"] and "not retried or rerouted" in child["notice"]
    assert job["last_outcome"] == "failed" and len(job["runs"]) == 1
    assert len(kos.runner.invoked("classifier")) == 1
    assert kos.runner.invoked("eval_officer") == []
    assert vault.snapshot() == before
    attention = kos.get("/api/attention")[1]
    assert any(item["kind"] == "failed_job" and item["job"] == "curate" for item in attention)
    # Only a distinct explicit trigger starts another Work; failed targets were not consumed.
    kos.runner.script("classifier", classifier_script())
    retry, job = run_job(kos, "curate")
    assert retry["status"] == "completed", retry
    targets = kos.get(f"/api/works/{retry['work_id']}")[1]["workspec"]["inputs"]
    assert {item["path"] for item in targets} == set(paths)
    assert len(job["runs"]) == 2

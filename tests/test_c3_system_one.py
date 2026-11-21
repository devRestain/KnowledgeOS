"""C3 related-notes vetting acceptance (docs/CAPABILITIES.md SRC-9) through POST /api/search, against
a fake Ollama and a fake Decisions API on real loopback sockets."""

from __future__ import annotations

import pytest
import test_c3_semantic as c3
from conftest import conforming_note, organize_scripts, system_one_settings

from knowledgeos.config import ConfigError

NOTES = c3.NOTES
FOCAL = NOTES + "Focal.md"
GOOD = NOTES + "Good.md"
BAD = NOTES + "Bad.md"
LOCAL = NOTES + "Local.md"


@pytest.fixture
def ollama():
    fake = c3.FakeOllama().start()
    yield fake
    fake.stop()


def write_notes(vault) -> None:
    vault.write(FOCAL, c3.note(c3.BASE + " focalmarker\n", kind="knowledge", status="draft"))
    vault.write(GOOD, c3.note(c3.BASE + " goodmarker\n", policy="approve", kind="knowledge", status="draft"))
    vault.write(BAD, c3.note(c3.BASE + " badmarker\n", kind="knowledge", status="draft"))
    vault.write(LOCAL, c3.note(c3.BASE + " localmarker\n", policy="local_only", kind="knowledge", status="draft"))


def vetting_kos(make_kos, ollama, decisions, vault, tmp_path, **values):
    kos = make_kos(settings={
        **c3.semantic_settings(ollama, **values), "system_one": system_one_settings(decisions, tmp_path),
    })
    write_notes(vault)
    assert kos.run("index.embed", {})["outcome"]["embedded"] == 3
    return kos


def judge(question, body) -> float:
    return 0.1 if "badmarker" in question["instructions"] else 0.9


def test_SRC_9_related_notes_keep_only_semantic_neighbors_system_one_vets(
    make_kos, ollama, decisions, vault, tmp_path,
):
    decisions.predict("related_*", judge)
    kos = vetting_kos(make_kos, ollama, decisions, vault, tmp_path)

    first = kos.search(mode="related", path=FOCAL)
    by_path = {item["path"]: item for item in first["results"]}
    assert sorted(by_path) == [GOOD]
    assert by_path[GOOD]["reasons"] == ["semantic"] and by_path[GOOD]["vetted"] is True

    assert len(decisions.calls) == 1
    call = decisions.calls[0]
    assert call["model"] == "gpt-6-luna" and decisions.questions() == ["related_1", "related_2"]
    assert all(question["type"] == "predicate" for question in call["questions"])
    sent = decisions.sent_text()
    assert "focalmarker" in sent and "goodmarker" in sent and "badmarker" in sent
    assert "localmarker" not in sent

    assert kos.search(mode="related", path=FOCAL) == first
    assert len(decisions.calls) == 1  # verdicts are cached for unchanged notes

    vault.write(GOOD, c3.note(c3.BASE + " goodmarker changed\n", policy="approve", kind="knowledge", status="draft"))
    assert kos.run("index.embed", {})["outcome"]["embedded"] == 1
    kos.search(mode="related", path=FOCAL)
    assert len(decisions.calls) == 2 and decisions.questions()[2:] == ["related_1"]

    assert kos.owner.store.read_stream("system_one") == []
    assert not list((tmp_path / "state").rglob("system_one.jsonl"))


def test_SRC_9_related_notes_stay_cosine_only_without_a_usable_system_one(
    make_kos, ollama, decisions, vault, tmp_path,
):
    decisions.predict("related_*", judge)
    decisions.down = True
    kos = vetting_kos(make_kos, ollama, decisions, vault, tmp_path)

    result = kos.search(mode="related", path=FOCAL)
    assert sorted(item["path"] for item in result["results"]) == [BAD, GOOD]
    assert all(item["reasons"] == ["semantic"] and "vetted" not in item for item in result["results"])
    assert decisions.calls == [] and kos.owner.store.read_stream("system_one") == []


def test_SRC_9_refused_and_low_probability_answers(make_kos, ollama, decisions, vault, tmp_path):
    decisions.predict("related_*", lambda question, body: 0.6 if "goodmarker" in question["instructions"] else 0.4)
    kos = vetting_kos(make_kos, ollama, decisions, vault, tmp_path, related_min_probability=0.55)
    result = kos.search(mode="related", path=FOCAL)
    assert sorted(item["path"] for item in result["results"] if item.get("vetted")) == [GOOD]
    assert BAD not in [item["path"] for item in result["results"]]


def test_SRC_9_unanswered_questions_stay_unvetted(make_kos, ollama, decisions, vault, tmp_path):
    kos = vetting_kos(make_kos, ollama, decisions, vault, tmp_path)  # no rule: every question is refused
    result = kos.search(mode="related", path=FOCAL)
    assert sorted(item["path"] for item in result["results"]) == [BAD, GOOD]
    assert not any("vetted" in item for item in result["results"])


@pytest.mark.parametrize("value", [-0.1, 1.5, "high", True])
def test_SRC_9_related_min_probability_must_be_a_probability(make_kos, ollama, value):
    with pytest.raises(ConfigError, match="related_min_probability"):
        make_kos(settings=c3.semantic_settings(ollama, related_min_probability=value))


def test_SRC_9_role_related_tool_vets_approve_neighbors_in_scheduled_work(
    make_kos, ollama, decisions, vault, tmp_path,
):
    target = "00_Inbox/Target.md"
    vault.write(target, conforming_note("capture", "unprocessed", policy="approve", body=c3.BASE + " target\n"))
    vault.write(GOOD, c3.note(c3.BASE + " goodmarker\n", policy="approve", kind="knowledge", status="draft"))
    vault.write(BAD, c3.note(c3.BASE + " badmarker\n", policy="approve", kind="knowledge", status="draft"))
    vault.write(LOCAL, c3.note(c3.BASE + " localmarker\n", policy="local_only", kind="knowledge", status="draft"))
    decisions.predict("related_*", judge)
    kos = make_kos(settings={
        **c3.semantic_settings(ollama), "system_one": system_one_settings(decisions, tmp_path),
    })
    assert kos.run("index.embed", {})["outcome"]["embedded"] == 3

    organize_scripts(kos.runner)
    seen = {}
    original = kos.runner.scripts["linker"]

    def linker(session):
        context = session.call("work_context")
        seen["related"] = session.call("related", handle=context["targets"][0]["target"])
        original(session)

    kos.runner.script("linker", linker)
    receipt = kos.run("organize.auto", {}, actor="schedule/curate")
    assert receipt["status"] == "completed", receipt
    assert [(item["title"], item["reasons"], item.get("vetted")) for item in seen["related"]["results"]] == [
        ("Good", ["semantic"], True),
    ]
    assert [name for name in decisions.questions() if name.startswith("related_")] == ["related_1", "related_2"]
    assert "localmarker" not in decisions.sent_text()
    assert [entry for entry in kos.owner.store.read_stream("system_one") if entry["purpose"] == "C3.related"] == []

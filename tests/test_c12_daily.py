"""C12 daily-note acceptance through the Owner's ExOps request interface."""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path

import pytest
from conftest import conforming_note
from test_c2_standardize import render_template

from knowledgeos.config import ROOT
from knowledgeos.notes import UUID4_RE, parse
from knowledgeos.util import SEOUL, iso

FOLDER = "10_Journal/Daily"
TODAY = f"{FOLDER}/2026-11-21.md"
MORNING = datetime(2026, 11, 21, 7, tzinfo=SEOUL)
CLOSING = "하나씩 천천히 해 봐요. 오늘 다 못 해도 괜찮아요, 남은 일은 내일 노트로 잘 넘겨 둘게요."


def _template(identifier, created):
    return (
        f'---\nid: "{identifier}"\ntype: daily\ncreated: {created}\n'
        "tags:\n  - journal/daily\nai_policy: approve\nsensitivity: personal\n---\n"
        "## 메모\n<!-- - 떠오른 생각, 들은 말, 나중에 볼 링크를 한 줄씩 -->\n\n"
        "## 할 일\n<!-- - [ ] #task 할 일 이름 📅 YYYY-MM-DD -->\n\n"
        "## 일정\n<!-- - HH:MM 일정 제목, 위치 -->\n"
        "<!-- - [ ] YYYY-MM-DD HH:MM 일정 제목, 위치 (끝날 때까지 다음 날로 넘어감) -->\n\n"
        "## 하루 정리\n- 오늘 가장 잘한 일:\n- 아쉬웠던 점과 그 이유:\n"
        "- 새로 알게 된 것:\n- 고마웠던 사람이나 순간:\n- 내일 가장 먼저 할 일:\n"
    )


def _daily(body, policy="approve"):
    return conforming_note("daily", "", body=body, policy=policy,
                           extra="tags: [journal/daily]\nsensitivity: personal\n").replace("status: \n", "")


def _run(kos):
    receipt = kos.run("daily.run", {}, actor="schedule/daily")
    assert receipt["status"] == "completed", receipt
    assert receipt["work_id"], receipt
    return receipt


def test_DAY_1_creates_today_with_carried_open_items_and_marks_sources(kos, vault, clock):
    clock.set(MORNING)
    first = f"{FOLDER}/2026-11-19.md"
    second = f"{FOLDER}/2026-11-20.md"
    older = _daily(
        "- [ ] Before any heading\n  keep this detail\n\tand this\n\n"
        "## 일정 (개인)\n1. [/] Dentist\n  address\n\n"
        "## 할 일\n* [ ] First task\n- [x] Finished\n- [-] Cancelled\n- [>] Forwarded\n"
        "- plain bullet\n  - [ ] Nested only\n- [ ]    \n"
        "```markdown\n- [ ] Example only\n<!-- a comment in code\n```\n"
        "<!--\n## 일정 hidden\n- [ ] Comment only\n-->\n"
    )
    newer = _daily("## 일정\n+ [ ] Team meeting\n\n## 작업\n2) [ ] Second task\n").replace("\n", "\r\n")
    vault.write(first, older)
    vault.write(second, newer)
    untouched = {
        f"{FOLDER}/2026-11-18.md": _daily("- [ ] Denied task\n", policy="deny"),
        f"{FOLDER}/2026-11-17.md": "---\nai_policy: approve\nbad: [\n---\n- [ ] Bad YAML\n",
        f"{FOLDER}/2026-11-16.md": _daily("<<<<<<< conflict\n- [ ] Conflict task\n"),
        f"{FOLDER}/2026-11-15.md": b"\xff\n- [ ] Undecodable\n",
        f"{FOLDER}/2026-11-22.md": _daily("- [ ] Future task\n"),
        f"{FOLDER}/2026-02-30.md": _daily("- [ ] Invalid date\n"),
        f"{FOLDER}/nested/2026-11-18.md": _daily("- [ ] Nested file\n"),
    }
    for path, data in untouched.items():
        vault.write(path, data)
    before = vault.snapshot()

    receipt = _run(kos)

    note = parse(TODAY, vault.read(TODAY))
    assert UUID4_RE.fullmatch(note.note_id)
    assert vault.text(TODAY) == _template(note.note_id, iso(MORNING)).replace(
        "<!-- - [ ] #task 할 일 이름 📅 YYYY-MM-DD -->\n",
        "<!-- - [ ] #task 할 일 이름 📅 YYYY-MM-DD -->\n"
        "- [ ] Before any heading\n  keep this detail\n\tand this\n"
        "* [ ] First task\n2) [ ] Second task\n",
    ).replace(
        "<!-- - [ ] YYYY-MM-DD HH:MM 일정 제목, 위치 (끝날 때까지 다음 날로 넘어감) -->\n",
        "<!-- - [ ] YYYY-MM-DD HH:MM 일정 제목, 위치 (끝날 때까지 다음 날로 넘어감) -->\n"
        "1. [/] Dentist\n  address\n+ [ ] Team meeting\n",
    )
    expected_first = older
    for item in ("- [ ] Before any heading", "1. [/] Dentist", "* [ ] First task"):
        expected_first = expected_first.replace(item, item.replace("[ ]", "[>]").replace("[/]", "[>]"))
    assert vault.text(first) == expected_first
    assert vault.read(second) == newer.replace("+ [ ]", "+ [>]").replace("2) [ ]", "2) [>]").encode()
    for path in untouched:
        assert vault.snapshot()[path] == before[path]
    assert receipt["outcome"] == {
        "date": "2026-11-21", "path": TODAY, "created": True, "carried": 5,
        "marked": [first, second], "not_applied": [], "notice_id": None,
    }
    work = kos.get(f"/api/works/{receipt['work_id']}")[1]
    assert work["workspec"]["target"] == "DailyOfficer"
    assert work["workspec"]["criteria"][0]["id"] == "day.carry_exact"
    assert [effect["path"] for effect in work["effects"]] == [TODAY, first, second]


def test_DAY_2_existing_today_note_keeps_its_lines_and_gains_items_once(kos, vault, clock):
    clock.set(MORNING)
    source = f"{FOLDER}/2026-11-20.md"
    older = _daily("## 일정\n- [ ] Carried appointment\n\n## 할 일\n- [ ] Duplicate\n- [ ] Another task\n")
    original = _daily("User header\n\n## 일정\n- [ ] Own appointment\n\n## 작업\n- [ ] Duplicate\n"
                      "A user paragraph.\n\n## 메모\nKeep this memo exactly.\n")
    vault.write(source, older)
    vault.write(TODAY, original)

    first = _run(kos)

    assert vault.text(TODAY) == original.replace(
        "- [ ] Own appointment\n", "- [ ] Own appointment\n- [ ] Carried appointment\n"
    ).replace("A user paragraph.\n", "A user paragraph.\n- [ ] Another task\n")
    assert vault.text(TODAY).count("- [ ] Duplicate\n") == 1
    assert vault.text(source) == older.replace("[ ]", "[>]")
    assert first["outcome"]["created"] is False and first["outcome"]["carried"] == 2
    assert first["outcome"]["marked"] == [source]
    before = vault.snapshot()

    second = _run(kos)

    assert vault.snapshot() == before
    assert second["outcome"]["carried"] == 0 and second["outcome"]["marked"] == []
    work = kos.get(f"/api/works/{second['work_id']}")[1]
    assert work["effects"] == []


def test_DAY_3_source_edited_after_admission_is_not_marked(kos, vault, clock, monkeypatch):
    clock.set(MORNING)
    source = f"{FOLDER}/2026-11-20.md"
    original = _daily("## 할 일\n- [ ] Existing task\n")
    edited = original + "- [ ] Added after admission\n"
    vault.write(source, original)
    officer = kos.owner.registry.officers["DailyOfficer"]
    step = officer.step

    def edit_after_snapshot(ctx):
        artifact = step(ctx)
        vault.write(source, edited)
        return artifact

    monkeypatch.setattr(officer, "step", edit_after_snapshot)
    first = _run(kos)
    assert vault.text(source) == edited
    assert first["outcome"]["not_applied"] == [source]
    assert first["outcome"]["marked"] == []
    assert vault.text(TODAY).count("- [ ] Existing task\n") == 1
    monkeypatch.setattr(officer, "step", step)

    second = _run(kos)

    assert second["outcome"]["not_applied"] == [] and second["outcome"]["marked"] == [source]
    assert second["outcome"]["carried"] == 1
    assert vault.text(TODAY).count("- [ ] Existing task\n") == 1
    assert vault.text(TODAY).count("- [ ] Added after admission\n") == 1
    assert vault.text(source) == edited.replace("[ ]", "[>]")


def test_DAY_4_reminder_lists_today_and_due_items_without_private_notes(make_kos, vault, clock):
    clock.set(MORNING)
    sent = []
    kos = make_kos(notifier=sent.append)
    vault.write(TODAY, _daily(
        "## 메모\n- [ ] #task Read notes\n\n## 일정\n- [/] Morning meeting\n\n"
        "## 할 일\n- [ ] #task Send draft\n- [x] #task Done\n"))
    vault.write("40_Knowledge/Notes/Shared.md", conforming_note(
        "knowledge", "seed", policy="approve",
        body="- [ ] #task Overdue invoice 📅 2026-11-20\n- [ ] #task Today deadline 📅 2026-11-21\n"
             "- [ ] #task Future deadline 📅 2026-11-22\n- [x] #task Paid 📅 2026-11-20\n"
             "- [>] #task Moved 📅 2026-11-20\n  - [ ] #task Nested 📅 2026-11-20\n"
             "- [ ] Non-task deadline 📅 2026-11-20\n"))
    for folder, policy in (("40_Knowledge/Notes", "deny"), ("99_System", "approve"),
                           ("01_AI_Review/Reports", "approve")):
        vault.write(f"{folder}/Private secret.md", conforming_note(
            "knowledge", "seed", body="- [ ] #task Private due secret 📅 2026-11-20\n", policy=policy))

    first = _run(kos)
    second = _run(kos)

    assert len(sent) == 1
    notice = sent[0]
    today_id = parse(TODAY, vault.read(TODAY)).note_id
    assert notice["kind"] == "daily" and notice["item_id"] == f"daily/2026-11-21/{today_id}"
    assert notice["title"] == "좋은 아침이에요. 11월 21일 토요일, 오늘 챙길 일 4가지를 정리해 왔어요."
    assert notice["lines"] == [
        "• Read notes", "• Send draft", "• Overdue invoice (기한이 지났어요)",
        "• Today deadline (오늘까지)", "", CLOSING,
    ]
    rendered = json.dumps(notice, ensure_ascii=False)
    for excluded in ("secret", "Morning meeting", "Non-task deadline", "Shared", "#task", "obsidian://"):
        assert excluded not in rendered
    assert all("2026-" not in line and "📅" not in line for line in notice["lines"])
    assert notice["links"] == []
    assert first["outcome"]["notice_id"] == second["outcome"]["notice_id"] == notice["notice_id"]


def test_DAY_5_owner_created_and_template_daily_notes_need_no_standardization(kos, vault, clock):
    clock.set(MORNING)
    _run(kos)
    created = parse(TODAY, vault.read(TODAY))
    assert vault.read(TODAY) == _template(created.note_id, iso(clock.now())).encode("utf-8")
    mounted = os.environ.get("KNOWLEDGEOS_VAULT_SYSTEM")
    template = Path(mounted) / "Templates/T10_Daily.md" if mounted else None
    source = (template.read_text(encoding="utf-8") if template is not None and template.is_file()
              else _template("<% id %>", "<% now %>"))
    path = f"{FOLDER}/2026-11-22.md"
    rendered = render_template(source, "2026-11-22", iso(clock.now()))
    template_note = parse(path, rendered.encode("utf-8"))
    assert rendered.replace(template_note.note_id, created.note_id) == vault.text(TODAY)
    vault.write(path, rendered)
    before = vault.snapshot()
    receipt = kos.run("standardize.preview", {
        "paths": [TODAY, path], "digests": {note: vault.sha(note) for note in (TODAY, path)},
    })
    assert receipt["status"] == "completed", receipt
    assert receipt["outcome"]["notes"] == [] and receipt["outcome"]["unchanged"] == 2
    assert receipt["outcome"]["skipped"] == [] and vault.snapshot() == before


def test_DAY_job_is_declared_daily_at_seven():
    job = json.loads((ROOT / "config/jobs.json").read_text(encoding="utf-8"))["jobs"]["daily"]
    assert job["schedule"] == "0 7 * * *" and job["command"] == "daily.run" and job["params"] == {}


def test_DAY_exact_creation_collision_never_marks_sources_or_creates_a_suffix(kos, vault, clock, monkeypatch):
    clock.set(MORNING)
    source = f"{FOLDER}/2026-11-20.md"
    original = _daily("- [ ] Keep open\n")
    arrived = _daily("A note created by the user while the Work runs.\n")
    vault.write(source, original)
    officer = kos.owner.registry.officers["DailyOfficer"]
    step = officer.step

    def create_after_snapshot(ctx):
        artifact = step(ctx)
        vault.write(TODAY, arrived)
        return artifact

    monkeypatch.setattr(officer, "step", create_after_snapshot)
    receipt = _run(kos)

    assert receipt["outcome"]["created"] is False and receipt["outcome"]["carried"] == 0
    assert receipt["outcome"]["marked"] == [] and receipt["outcome"]["not_applied"] == [TODAY]
    assert vault.text(source) == original and vault.text(TODAY) == arrived
    assert sorted(vault.path(FOLDER).glob("2026-11-21*.md")) == [vault.path(TODAY)]
    work = kos.get(f"/api/works/{receipt['work_id']}")[1]
    assert work["effects"][0]["op"] == "create" and work["effects"][0]["state"] == "not_applied"


@pytest.mark.parametrize(("today", "reason"), [
    (_daily("- [ ] Denied secret\n", policy="deny"), "deny"),
    (_daily("- [ ] Retired secret\n", policy="ask"), "deny"),
    (_daily("<<<<<<< conflict\n- [ ] Conflicted\n"), "conflict"),
    ("---\nbad: [\n---\n- [ ] Invalid YAML\n", "invalid_yaml"),
], ids=["deny", "retired", "conflict", "invalid_yaml"])
def test_DAY_blocked_today_admits_without_snapshots_writes_or_notice(make_kos, vault, clock, today, reason):
    clock.set(MORNING)
    sent = []
    kos = make_kos(notifier=sent.append)
    vault.write(TODAY, today)
    vault.write(f"{FOLDER}/2026-11-20.md", _daily("- [ ] Still pending\n"))
    before = vault.snapshot()

    receipt = _run(kos)

    assert vault.snapshot() == before
    assert [notice for notice in sent if notice["kind"] == "daily"] == []
    assert receipt["outcome"]["notice_id"] is None and receipt["outcome"]["marked"] == []
    work = kos.get(f"/api/works/{receipt['work_id']}")[1]
    assert work["workspec"]["inputs"] == [] and work["workspec"]["params"]["today_blocked"] == reason


def test_DAY_reminder_rechecks_due_note_policy_after_admission(make_kos, vault, clock, monkeypatch):
    clock.set(MORNING)
    sent = []
    kos = make_kos(notifier=sent.append)
    vault.write(TODAY, _daily("## 일정\n- [ ] Morning meeting\n\n## 할 일\n- [ ] #task Send draft\n"))
    other = "40_Knowledge/Notes/Secret title.md"
    original = conforming_note("knowledge", "seed", policy="approve", body="- [ ] #task Secret due 📅 2026-11-20\n")
    vault.write(other, original)
    officer = kos.owner.registry.officers["DailyOfficer"]
    step = officer.step

    def restrict_after_snapshot(ctx):
        artifact = step(ctx)
        vault.write(other, original.replace("ai_policy: approve", "ai_policy: deny"))
        return artifact

    monkeypatch.setattr(officer, "step", restrict_after_snapshot)
    _run(kos)

    assert len(sent) == 1
    assert sent[0]["title"] == "좋은 아침이에요. 11월 21일 토요일, 오늘 챙길 일 1가지를 정리해 왔어요."
    assert sent[0]["lines"] == ["• Send draft", "", CLOSING]
    assert sent[0]["links"] == []
    assert "Secret" not in json.dumps(sent[0], ensure_ascii=False)


@pytest.mark.parametrize(("body", "expected"), [
    ("Keep this memo without a newline",
     "Keep this memo without a newline\n\n## 일정\n- [ ] Appointment\n\n## 할 일\n- [ ] Task\n"),
    ("## 일정\n\n## 할 일\n\n## 메모\nA memo.\n",
     "## 일정\n- [ ] Appointment\n\n## 할 일\n- [ ] Task\n\n## 메모\nA memo.\n"),
], ids=["missing_sections", "empty_sections"])
def test_DAY_missing_and_empty_sections_only_gain_carried_lines(kos, vault, clock, body, expected):
    clock.set(MORNING)
    original = _daily(body)
    vault.write(TODAY, original)
    vault.write(f"{FOLDER}/2026-11-20.md", _daily("## 일정\n- [ ] Appointment\n## 할 일\n- [ ] Task\n"))

    _run(kos)

    assert vault.text(TODAY) == original.removesuffix(body) + expected


def test_DAY_today_edited_after_admission_never_marks_a_duplicate_source(kos, vault, clock, monkeypatch):
    clock.set(MORNING)
    source = f"{FOLDER}/2026-11-20.md"
    original = _daily("- [ ] Already present\n")
    vault.write(source, original)
    vault.write(TODAY, original)
    edited = _daily("The user removed the task after admission.\n")
    officer = kos.owner.registry.officers["DailyOfficer"]
    step = officer.step

    def edit_today_after_snapshot(ctx):
        artifact = step(ctx)
        vault.write(TODAY, edited)
        return artifact

    monkeypatch.setattr(officer, "step", edit_today_after_snapshot)
    receipt = _run(kos)

    assert receipt["outcome"]["not_applied"] == [TODAY] and receipt["outcome"]["marked"] == []
    assert vault.text(source) == original and vault.text(TODAY) == edited


def test_DAY_reminder_caps_item_lines_and_reports_the_remainder(make_kos, vault, clock):
    clock.set(MORNING)
    sent = []
    kos = make_kos(notifier=sent.append)
    vault.write(TODAY, _daily("## 할 일\n" + "".join(f"- [ ] #task Task {index}\n" for index in range(22))))

    _run(kos)

    assert len(sent) == 1
    assert sent[0]["title"] == "좋은 아침이에요. 11월 21일 토요일, 오늘 챙길 일 22가지를 정리해 왔어요."
    assert sent[0]["lines"] == [*[f"• Task {index}" for index in range(20)], "• 그리고 2가지가 더 있어요", "", CLOSING]
    assert sent[0]["links"] == []


@pytest.mark.parametrize(("text", "name"), [
    ("#task 보고서 쓰기 📅 2026-11-21 ⏫", "보고서 쓰기"),
    (("#task  보고서   쓰기 ⏳ 2026-11-21 🛫 2026-11-20 ➕ 2026-11-19 "
      "✅ 2026-11-18 ❌ 2026-11-17 🔼 🔽 🔺 ⏬ 🔁 every week #work 📅 2026-11-21"), "보고서 쓰기 #work"),
], ids=["due_and_priority", "all_fields_and_other_tag"])
def test_DAY_reminder_shows_task_name_without_task_metadata(make_kos, vault, clock, text, name):
    clock.set(MORNING)
    sent = []
    kos = make_kos(notifier=sent.append)
    vault.write(TODAY, _daily(f"## 할 일\n- [ ] {text}\n"))

    _run(kos)

    assert len(sent) == 1
    assert sent[0]["lines"] == [f"• {name}", "", CLOSING]
    assert sent[0]["links"] == []


@pytest.mark.parametrize("body", [
    "## 일정\n- [ ] Morning meeting\n\n## 할 일\n- [ ] Untagged task\n",
    "## 할 일\n- [ ] #task 📅 2026-11-21 ⏫\n",
], ids=["no_task_tag", "empty_task_name"])
def test_DAY_reminder_skips_notes_without_task_names(make_kos, vault, clock, body):
    clock.set(MORNING)
    sent = []
    kos = make_kos(notifier=sent.append)
    vault.write(TODAY, _daily(body))
    vault.write("40_Knowledge/Notes/Deadline.md", conforming_note(
        "knowledge", "seed", body="- [ ] Untagged deadline 📅 2026-11-20\n"))

    receipt = _run(kos)

    assert sent == []
    assert receipt["outcome"]["notice_id"] is None

"""C12: exact daily-note handoff and the privacy-filtered morning reminder."""

from __future__ import annotations

import re
from datetime import date
from pathlib import PurePosixPath
from typing import Any

from ..capabilities import Done, Registry, WorkDraft
from ..engine import EffectContext, StepContext
from ..notes import UUID4_RE, Note, parse
from ..owner import Admission, Owner
from ..policy import effective
from ..standardize import path_skip_reason
from ..util import SEOUL, iso, new_uuid
from ..vault import VaultPathError

CAPABILITY = "C12"
OFFICER = "DailyOfficer"
OPEN_ITEM = re.compile(r"^(?:[-*+]|\d+[.)]) \[(?P<mark>[ /])\] +(?P<text>\S.*)")
HEADING = re.compile(r"^#{1,6}[ \t]+(.*?)(?:[ \t]+#+)?[ \t]*$")
FENCE = re.compile(r"^[ \t]*(`{3,})")
DUE = re.compile(r"📅 (\d{4}-\d{2}-\d{2})(?!\d)")
TASK_TAG = re.compile(r"(?<![\w/#-])#task(?![\w/-])")
TASK_FIELDS = re.compile(
    r"[📅⏳🛫➕✅❌]\s*\d{4}-\d{2}-\d{2}|[⏫🔼🔽🔺⏬]|🔁[^📅⏳🛫➕✅❌⏫🔼🔽🔺⏬#]*")


def register(reg: Registry) -> None:
    reg.command("daily.run", admit)
    reg.officer(OFFICER, officer_step, finish)
    reg.check("day.carry_exact", check)


def _skip(note: Note) -> str | None:
    if reason := path_skip_reason(note.path):
        return reason
    if effective(note) == "deny":
        return "deny"
    if note.conflict:
        return "conflict"
    if note.decode_error:
        return "not_utf8"
    if note.yaml_error is not None:
        return "invalid_yaml"
    return None


def _day(stem: str) -> date | None:
    try:
        result = date.fromisoformat(stem)
    except ValueError:
        return None
    return result if result.isoformat() == stem else None


def _scan(note: Note) -> tuple[list[dict[str, Any]], list[tuple[int, str]]]:
    """Keep source line positions; code and comments cannot introduce items or headings."""
    lines = note.text.splitlines(keepends=True)
    items, headings = [], []
    section, comment, fence = "task", False, 0
    for index in range(note.body_start_line - 1, len(lines)):
        line = lines[index].rstrip("\r\n")
        visible = ""
        rest = line
        if fence:
            marker = FENCE.match(line)
            if marker and len(marker[1]) >= fence:
                fence = 0
            continue
        while rest:
            if comment:
                end = rest.find("-->")
                if end < 0:
                    break
                comment, rest = False, rest[end + 3:]
            else:
                start = rest.find("<!--")
                if start < 0:
                    visible += rest
                    break
                visible += rest[:start]
                comment, rest = True, rest[start + 4:]
        marker = FENCE.match(visible)
        if marker:
            fence = len(marker[1])
            continue
        heading = HEADING.fullmatch(visible)
        if heading:
            title = heading[1]
            headings.append((index, title))
            section = "schedule" if title.startswith("일정") else "task"
            continue
        match = OPEN_ITEM.match(visible)
        if match is None or not OPEN_ITEM.match(line):
            continue
        end = index + 1
        while end < len(lines) and lines[end].strip() and lines[end].startswith((" ", "\t")):
            end += 1
        items.append({"section": section, "line": index, "mark": match.start("mark"),
                      "lines": lines[index:end], "text": match["text"]})
    return items, headings


def admit(adm: Admission) -> WorkDraft:
    owner = adm.owner
    now = owner.clock.now()
    today = now.astimezone(SEOUL).date()
    folder = owner.vault.normalize(adm.setting("folder", "10_Journal/Daily").rstrip("/"))
    path = f"{folder}/{today.isoformat()}.md"
    current = owner.vault.read(path)
    params = {"date": today.isoformat(), "path": path, "created": iso(now)}
    # A denied or malformed today is not snapshotted into State and cannot trigger disclosure.
    if current is not None and (reason := _skip(current)):
        return WorkDraft(mode="daily", params={**params, "today_blocked": reason})
    targets = [path] if current is not None else []
    for source in sorted(owner.vault.iter_paths(folder)):
        source_path = PurePosixPath(source)
        source_day = _day(source_path.stem)
        if source_path.parent.as_posix() != folder or source_day is None or source_day >= today:
            continue
        note = owner.vault.read(source)
        if note is not None and not _skip(note) and _scan(note)[0]:
            targets.append(source)
    return WorkDraft(mode="daily", targets=targets, params=params)


def _encode(note: Note, lines: list[str]) -> str:
    return ("\ufeff" if note.bom else "") + "".join(lines)


def _insert(note: Note, carried: dict[str, list[str]]) -> str:
    lines = note.text.splitlines(keepends=True)
    headings = _scan(note)[1]
    insertions = []
    missing = []
    for section, title in (("schedule", "일정"), ("task", "할 일")):
        if not carried[section]:
            continue
        choices = [entry for entry in headings if entry[1].startswith(title)]
        if not choices and section == "task":
            choices = [entry for entry in headings if entry[1].startswith("작업")]
        text = "".join(carried[section])
        if not choices:
            missing.append(f"{note.newline}## {title}{note.newline}{text}")
            continue
        start = choices[0][0]
        end = next((index for index, _ in headings if index > start), len(lines))
        while end > start + 1 and not lines[end - 1].strip():
            end -= 1
        insertions.append((end, text))
    for position, text in sorted(insertions, reverse=True):
        if position and not lines[position - 1].endswith(("\n", "\r")):
            lines[position - 1] += note.newline
        lines[position:position] = [text]
    if missing:
        if lines and not lines[-1].endswith(("\n", "\r")):
            lines[-1] += note.newline
        lines.extend(missing)
    return _encode(note, lines)


def _plan(ctx: StepContext, identifier: str | None) -> dict[str, Any]:
    path = ctx.params["path"]
    result = {"date": ctx.params["date"], "path": path, "id": identifier,
              "today": None, "sources": [], "carried": 0}
    if ctx.params.get("today_blocked"):
        return result
    inputs = {item["path"]: item for item in ctx.inputs()}
    today = ctx.snapshot(path) if path in inputs else None
    seen = set(today.text.splitlines()) if today is not None else set()
    newline = today.newline if today is not None else "\n"
    carried = {"schedule": [], "task": []}
    for item in ctx.inputs():
        if item["path"] == path:
            continue
        note = ctx.snapshot(item["path"])
        open_items = _scan(note)[0]
        if not open_items:
            continue
        lines = note.text.splitlines(keepends=True)
        for entry in open_items:
            first = entry["lines"][0].rstrip("\r\n")
            if first not in seen:
                carried[entry["section"]].extend(line.rstrip("\r\n") + newline for line in entry["lines"])
                seen.add(first)
                result["carried"] += 1
            index, mark = entry["line"], entry["mark"]
            lines[index] = lines[index][:mark] + ">" + lines[index][mark + 1:]
        result["sources"].append({"path": note.path, "preimage": note.sha, "data": _encode(note, lines)})
    if today is None:
        data = (f'---\nid: "{identifier}"\ntype: daily\ncreated: {ctx.params["created"]}\n'
                "tags:\n  - journal/daily\nai_policy: approve\nsensitivity: personal\n---\n"
                "## 메모\n<!-- - 떠오른 생각, 들은 말, 나중에 볼 링크를 한 줄씩 -->\n\n"
                "## 할 일\n<!-- - [ ] #task 할 일 이름 📅 YYYY-MM-DD -->\n\n"
                "## 일정\n<!-- - HH:MM 일정 제목, 위치 -->\n"
                "<!-- - [ ] YYYY-MM-DD HH:MM 일정 제목, 위치 (끝날 때까지 다음 날로 넘어감) -->\n\n"
                "## 하루 정리\n- 오늘 가장 잘한 일:\n- 아쉬웠던 점과 그 이유:\n"
                "- 새로 알게 된 것:\n- 고마웠던 사람이나 순간:\n- 내일 가장 먼저 할 일:\n")
        result["today"] = {"preimage": None, "data": _insert(parse(path, data.encode("utf-8")), carried)}
    else:
        result["today"] = {"preimage": today.sha, "data": _insert(today, carried)}
    return result


def officer_step(ctx: StepContext) -> dict[str, Any]:
    missing = not ctx.params.get("today_blocked") and ctx.params["path"] not in {
        item["path"] for item in ctx.inputs()}
    return _plan(ctx, new_uuid() if missing else None)


def check(ctx: StepContext, artifact: dict[str, Any]) -> list[dict[str, Any]]:
    """Rebuild exact results from admitted bytes, never from the current Vault."""
    path = ctx.params["path"]
    folder = PurePosixPath(path).parent
    today = date.fromisoformat(ctx.params["date"])
    ok = True
    for item in ctx.inputs():
        note = ctx.snapshot(item["path"])
        if _skip(note):
            ok = False
        if note.path != path:
            source = PurePosixPath(note.path)
            source_day = _day(source.stem)
            ok = ok and source.parent == folder and source_day is not None and source_day < today
    identifier = artifact.get("id")
    missing = not ctx.params.get("today_blocked") and path not in {item["path"] for item in ctx.inputs()}
    ok = ok and (bool(isinstance(identifier, str) and UUID4_RE.fullmatch(identifier)) if missing
                 else identifier is None)
    ok = ok and artifact == _plan(ctx, identifier)
    return [{"target": path, "verdict": "satisfied" if ok else "unmet",
             "reason": "Exact daily carry and checkbox-only source marks match admitted snapshots" if ok
             else "Daily carry differs from the admitted notes or their exact permitted changes"}]


def _task_name(text: str) -> str:
    if not TASK_TAG.search(text):
        return ""
    return " ".join(TASK_FIELDS.sub("", TASK_TAG.sub("", text)).split())


def _reminder(owner: Owner, path: str, day: str) -> str | None:
    # One reminder per daily note: a note deleted and recreated for the same day is a new handoff.
    current = owner.vault.read(path)
    item_id = f"daily/{day}/{(current.note_id if current is not None else None) or 'none'}"
    with owner.store.lock(f"C12.{day}"):
        previous = next(
            (notice for notice in owner.store.list("notices") if notice.get("item_id") == item_id), None)
        if previous is not None:
            return previous["notice_id"]
        template = owner.setting("C12", "reminder")
        tasks = []
        if current is not None:
            if _skip(current):
                return None
            for item in _scan(current)[0]:
                if name := _task_name(item["text"]):
                    tasks.append((name, None))
        folder = str(PurePosixPath(path).parent) + "/"
        for other in owner.vault.iter_paths():
            if other.startswith((folder, "99_System/", "01_AI_Review/")):
                continue
            try:
                note = owner.vault.read(other)
            except VaultPathError:
                continue
            if note is None or not note.valid or note.conflict:
                continue
            # Read current bytes and policy at disclosure, not an admission-time index or snapshot.
            if effective(note) == "deny":
                continue
            for item in _scan(note)[0]:
                name = _task_name(item["text"])
                match = DUE.search(item["text"])
                due = _day(match[1]) if match else None
                if name and due is not None and due.isoformat() <= day:
                    label = "overdue" if due.isoformat() < day else "due_today"
                    tasks.append((name, label))
        if not tasks:
            return None
        lines = [template[label or "item"].format(name=name) for name, label in tasks[:20]]
        if len(tasks) > 20:
            lines.append(template["more"].format(count=len(tasks) - 20))
        lines.extend(["", template["closing"]])
        today = date.fromisoformat(day)
        title = template["greeting"].format(
            month=today.month, day=today.day, weekday="월화수목금토일"[today.weekday()], count=len(tasks))
        notice = owner.post_notice("daily", title, [], item_id=item_id, lines=lines)
        return notice["notice_id"]


def finish(ctx: EffectContext, artifact: dict[str, Any]) -> Done:
    owner, path = ctx.owner, artifact["path"]
    outcome = {"date": artifact["date"], "path": path, "created": False, "carried": 0,
               "marked": [], "not_applied": [], "notice_id": None}
    if ctx.params.get("today_blocked"):
        return Done(outcome=outcome)
    planned = artifact["today"]
    current = owner.vault.read(path)
    if current is not None and _skip(current):
        outcome["not_applied"].append(path)
        return Done(outcome=outcome)
    if planned["preimage"] is None:
        applied = ctx.create(path, planned["data"].encode("utf-8"), exact=True) is not None
        outcome["created"] = applied
    elif planned["data"].encode("utf-8") != ctx.snapshot(path).raw:
        applied = ctx.replace(path, planned["preimage"], planned["data"].encode("utf-8"))
    else:
        applied = current is not None and current.sha == planned["preimage"]
    if applied:
        outcome["carried"] = artifact["carried"]
        for source in artifact["sources"]:
            if ctx.replace(source["path"], source["preimage"], source["data"].encode("utf-8")):
                outcome["marked"].append(source["path"])
            else:
                outcome["not_applied"].append(source["path"])
    else:
        outcome["not_applied"].append(path)
    ctx._guard()
    outcome["notice_id"] = _reminder(owner, path, artifact["date"])
    return Done(outcome=outcome)

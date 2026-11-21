"""Vault-borne, independently applicable proposal notes (Blueprint §5.6)."""

from __future__ import annotations

import difflib
import json
import re
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any

from .notes import FrontmatterEditor, Note, render_value
from .notes import parse as parse_note
from .policy import ORDER, effective, strictest
from .util import canonical, digest, iso, new_uuid, sha256
from .vault import PROPOSALS_DIR

CHANGE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9-]{0,63}$")
CHECK = re.compile(r"^- \[([ xX])\] .+ \^([A-Za-z0-9-]+)\s*$", re.MULTILINE)
BLOCK = re.compile(r"^(`{3,})json[ \t]+kos-changes[ \t]*\r?\n(.*?)^\1[ \t]*$", re.MULTILINE | re.DOTALL)
PROPERTY = re.compile(r"[A-Za-z_][\w-]*")
FIELD_ORDER = ("type", "status", "title")  # order of keys one multi-property `set` writes


@dataclass
class Proposal:
    path: str
    proposal_id: str
    target: str
    target_id: str | None
    target_path: str
    target_sha: str
    changes: list[dict[str, Any]]
    checked: set[str]
    status: str
    work_id: str


def _changes(changes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result = []
    ids = set()
    for index, item in enumerate(changes, 1):
        change = dict(item)
        change.setdefault("change_id", f"c{index}")
        cid = change["change_id"]
        if not isinstance(cid, str) or not CHANGE_ID.fullmatch(cid) or cid in ids:
            raise ValueError("invalid or duplicate change_id")
        ids.add(cid)
        op = change.get("op")
        if op == "set" and "values" in change:
            # One checklist item that sets several properties together (e.g. type and status).
            values = change["values"]
            if "key" in change or "value" in change or not isinstance(values, dict) or not values:
                raise ValueError("set with values takes a non-empty mapping instead of key and value")
            for key, value in values.items():
                if not isinstance(key, str) or not PROPERTY.fullmatch(key):
                    raise ValueError("invalid property key")
                if not (value is None or isinstance(value, (str, int, float, bool))):
                    raise ValueError("set requires scalars")
        elif op in ("set", "merge_list"):
            if "values" in change:
                raise ValueError("values belongs to set")
            key = change.get("key")
            if not isinstance(key, str) or not PROPERTY.fullmatch(key):
                raise ValueError("invalid property key")
            value = change.get("value")
            if op == "set" and not (value is None or isinstance(value, (str, int, float, bool))):
                raise ValueError("set requires a scalar")
            if op == "merge_list" and not (isinstance(value, list) and all(isinstance(v, str) for v in value)):
                raise ValueError("merge_list requires strings")
        elif op == "move" and "values" not in change:
            folder = change.get("to")
            if not isinstance(folder, str) or not folder.strip() or any(p in ("..", ".") for p in folder.split("/")):
                raise ValueError("invalid move folder")
            if folder.startswith(("/", ".")) or "\\" in folder or ":" in folder:
                raise ValueError("invalid move folder")
        else:
            raise ValueError("unknown proposal operation")
        if set(change) - {"change_id", "op", "key", "value", "values", "to", "role", "evidence", "description"}:
            raise ValueError("unknown change field")
        result.append(change)
    return result


def _ordered(values: dict[str, Any]) -> list[tuple[str, Any]]:
    return sorted(values.items(), key=lambda item: FIELD_ORDER.index(item[0]) if item[0] in FIELD_ORDER else len(FIELD_ORDER))


def apply(target_note: Note, changes: list[dict[str, Any]], checked: set[str]) -> tuple[bytes, str | None]:
    """Apply checked property changes only; moves keep the original filename."""
    if not target_note.valid or target_note.conflict:
        raise ValueError("target is invalid or has conflict markers")
    editor = FrontmatterEditor(target_note)
    values = dict(target_note.data)
    destination = None
    changed = False
    for change in _changes(changes):
        if change["change_id"] not in checked:
            continue
        if change["op"] == "move":
            destination = str(PurePosixPath(change["to"]) / PurePosixPath(target_note.path).name)
        else:
            pairs = _ordered(change["values"]) if "values" in change else [(change["key"], change.get("value"))]
            for key, value in pairs:
                if change["op"] == "merge_list":
                    before = values.get(key, [])
                    merged = list(before) if isinstance(before, list) else ([before] if before else [])
                    for item in value:
                        if item not in merged:
                            merged.append(item)
                    value = merged
                if key not in values or values[key] != value:
                    editor.set(key, value, keep_block=True)
                    values[key] = value
                    changed = True
    return (editor.render() if changed else target_note.raw), (destination if destination != target_note.path else None)


def _fenced(language: str, text: str) -> str:
    fence = "`" * max(3, 1 + max((len(m.group()) for m in re.finditer(r"`+", text)), default=0))
    return f"{fence}{language}\n{text.rstrip()}\n{fence}\n"


def render(target_note: Note, changes: list[dict[str, Any]], *, work_id: str, capability: str,
           created: str, inputs: list[Note] | None = None, proposal_id: str | None = None,
           status: str = "open", kind: str = "organize") -> bytes:
    changes = _changes(changes)
    if status not in ("open", "stale"):
        raise ValueError("invalid proposal status")
    sources = [target_note, *(inputs or [])]
    policy = strictest(effective(note) for note in sources)
    if policy == "deny":
        raise ValueError("deny input")
    fields = {
        "id": proposal_id or new_uuid(), "type": "proposal", "status": status, "created": created,
        "ai_policy": policy, "kos_work": work_id, "kos_capability": capability,
        "target": f"[[{target_note.path.removesuffix('.md')}]]", "target_id": target_note.note_id,
        "target_path": target_note.path, "target_sha256": target_note.sha,
        "inputs": list(dict.fromkeys(f"[[{note.path.removesuffix('.md')}]]" for note in sources)),
    }
    lines = ["---", *(f"{key}: {render_value(value)}" for key, value in fields.items()), "---", "", f"# {kind.capitalize()}", ""]
    machine = []
    for change in changes:
        entry = dict(change)
        description = entry.pop("description", None)
        if not description:
            if entry["op"] == "move":
                description = f"Move to {entry['to']}/"
            elif "values" in entry:
                description = ", ".join(f"{key}: {render_value(value)}" for key, value in _ordered(entry["values"]))
            else:
                description = f"{entry['key']}: {render_value(entry.get('value'))}"
        description = " ".join(str(description).split())
        lines.append(f"- [x] {description} ^{entry['change_id']}")
        machine.append(entry)
    result, destination = apply(target_note, changes, {c["change_id"] for c in changes})
    delta = "".join(difflib.unified_diff(target_note.text.splitlines(keepends=True), result.decode('utf-8-sig').splitlines(keepends=True), f"a/{target_note.path}", f"b/{destination or target_note.path}"))
    return ("\n".join(lines) + "\n\n## Diff\n\n" + _fenced("diff", delta) + "\n## Change data\n\n" + _fenced("json kos-changes", canonical(machine).decode())).encode()


def parse(note: Note) -> Proposal | None:
    if not note.valid or note.conflict or note.type != "proposal":
        return None
    data = note.data
    blocks = list(BLOCK.finditer(note.body))
    if len(blocks) != 1 or data.get("status") not in ("open", "stale"):
        return None
    try:
        raw = json.loads(blocks[0].group(2))
        if not isinstance(raw, list):
            return None
        changes = _changes(raw)
        if not all(isinstance(data.get(k), str) and data[k] for k in ("id", "target", "target_path", "target_sha256", "kos_work")):
            return None
        if not re.fullmatch(r"[a-f0-9]{64}", data["target_sha256"]):
            return None
    except (ValueError, TypeError, KeyError):
        return None
    # Fenced examples cannot impersonate a checklist item.
    checklist = re.sub(r"^(`{3,})[^\n]*\n.*?^\1[ \t]*$", "", note.body, flags=re.MULTILINE | re.DOTALL)
    checks = CHECK.findall(checklist)
    if len({cid for _, cid in checks}) != len(checks):
        return None
    if {cid for _, cid in checks} - {change["change_id"] for change in changes}:
        return None
    checked = {cid for flag, cid in checks if flag.lower() == "x"}
    return Proposal(note.path, data["id"], data["target"], data.get("target_id"), data["target_path"], data["target_sha256"], changes, checked, data["status"], data["kos_work"])


def open_for_target(owner, target_path_or_id: str) -> list[str]:
    key = target_path_or_id.removeprefix("id:")
    docs = owner.index.refresh()
    target_id = docs[key].note_id if key in docs and docs[key].policy != "deny" else key
    paths = []
    for path in owner.vault.iter_paths(PROPOSALS_DIR):
        note = owner.vault.read(path)
        if note is None or effective(note) == "deny":
            continue
        if note.type == "proposal" and note.status == "open" and (
            note.data.get("target_path") == key or (target_id is not None and note.data.get("target_id") == target_id)
        ):
            paths.append(path)
    return sorted(paths)


def write_or_regenerate(effect_ctx, target_note: Note, changes: list[dict[str, Any]], *, kind: str,
                        inputs: list[Note] | None = None, capability: str | None = None,
                        refresh_stale: bool = False) -> dict[str, Any]:
    """Never duplicate an open proposal or overwrite a user-edited/unrecorded one.

    A stale proposal reserves its target. With ``refresh_stale`` (an explicit new user request)
    a stale proposal whose bytes still match State's record is regenerated in place as open.
    """
    owner = effect_ctx.owner
    identity = target_note.note_id or target_note.path
    with owner.store.lock(f"proposal-target/{digest(identity)}"):
        current = owner.vault.read(target_note.path)
        if current is None or current.sha != target_note.sha or effective(current) == "deny":
            return {"status": "target_changed", "path": None}
        for source in inputs or []:
            observed = owner.vault.read(source.path)
            if observed is None or ORDER.index(effective(observed)) > ORDER.index(effective(source)):
                return {"status": "input_restricted", "path": None}
        existing = open_for_target(owner, target_note.path)
        if not existing:
            for path in owner.vault.iter_paths(PROPOSALS_DIR):
                waiting = owner.vault.read(path)
                if waiting and waiting.type == "proposal" and waiting.status == "stale" and (
                    waiting.data.get("target_path") == target_note.path
                    or (target_note.note_id and waiting.data.get("target_id") == target_note.note_id)
                ):
                    record = owner.store.get("proposals", waiting.note_id) if waiting.note_id else None
                    if not (refresh_stale and record and record.get("sha") == waiting.sha):
                        return {"status": "stale", "path": path}
                    existing = [path]
                    break
        old = owner.vault.read(existing[0]) if existing else None
        record = owner.store.get("proposals", old.note_id) if old and old.note_id else None
        if old and (not record or record.get("sha") != old.sha):
            return {"status": "user_edited", "path": old.path}
        # Preserve provenance on an unchanged regeneration so a new Work alone never rewrites a note.
        options = {
            "work_id": old.data.get("kos_work", effect_ctx.work_id) if old else effect_ctx.work_id,
            "capability": capability or effect_ctx.capability,
            "created": old.data.get("created", iso(effect_ctx.clock.now())) if old else iso(effect_ctx.clock.now()),
            "proposal_id": old.note_id if old else None, "inputs": inputs, "kind": kind,
        }
        raw = render(target_note, changes, **options)
        if old and raw == old.raw:
            return {"status": "unchanged", "path": old.path}
        if old:
            options["work_id"] = effect_ctx.work_id
            raw = render(target_note, changes, **options)
            if not effect_ctx.replace(old.path, old.sha, raw):
                return {"status": "user_edited", "path": old.path}
            path = old.path
        else:
            title = re.sub(r'[\\/:*?"<>|\[\]#\r\n]', "-", target_note.title).strip(" .")[:140] or "Note"
            short = re.sub(r"[^\w -]", "", kind).strip()[:40] or "proposal"
            path = effect_ctx.create(f"{PROPOSALS_DIR}/{title} — {short}.md", raw)
        note = parse_note(path, raw)
        owner.store.put("proposals", note.note_id, {"id": note.note_id, "path": path, "sha": sha256(raw), "target": identity,
                                                    "target_path": target_note.path, "work": effect_ctx.work_id, "status": "open"})
        return {"status": "regenerated" if old else "created", "path": path}

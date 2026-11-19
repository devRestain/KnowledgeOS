"""C1 Curation Team: bounded classification/link handoffs become reviewable proposals."""
from __future__ import annotations

import threading
from pathlib import PurePosixPath

from .. import proposals
from ..capabilities import Done, WorkDraft
from ..evidence import Unavailable, chunks
from ..notes import parse
from ..owner import Admission
from ..policy import model_may_read
from ..search import SearchIndex, role_filter
from ..standardize import FOLDER_TYPES, LINK_KEYS, STATUSES
from ..util import iso
from ..vault import RESERVED_REQUESTS, Vault, VaultPathError
from .c4_enhance import keyword_candidates

CAPABILITY = "C1"
TEAM = "curation"
LIMIT = 10


def register(reg):
    reg.command("organize", admit)
    reg.command("organize.auto", admit_auto)
    reg.team(TEAM, static={"scout": scout}, validate={"classifier": validate_classifier, "linker": validate_linker},
             context=team_context, result=result, finish=finish)
    reg.check("org.enums_valid", check_enums)
    reg.check("org.links_read", check_links)
    reg.check("org.tags_marked", check_tags)
    reg.check("org.one_open_proposal", check_proposals)


def exclude(path):
    if Vault.ignored(path) or Vault.system(path) or Vault.review(path) or path.startswith(RESERVED_REQUESTS):
        return "reserved or system path"
    return None


def _problem(owner, doc, origin):
    if doc.type in ("home", "system", "report", "proposal"):
        return "navigation or KnowledgeOS output"
    if not model_may_read(doc.policy, origin, False):
        return f"ai_policy {doc.policy} does not allow model roles in this Work"
    note = owner.vault.read(doc.path)
    if note is None or not chunks(note):
        return "no body content to classify"
    return None


def admit(adm):
    explicit, folders = [], []
    for raw in adm.params["paths"]:
        try:
            folder = raw.strip() in (".", "/") or (not raw.startswith("id:") and adm.owner.vault.abs(raw).is_dir())
        except VaultPathError:
            folder = False
        (folders if folder else explicit).append(raw)
    targets, skipped = adm.resolve(explicit, adm.params["digests"], strict=True, exclude=exclude)
    docs = adm.owner.index.refresh()
    for path in targets:
        if reason := _problem(adm.owner, docs[path], adm.origin):
            adm.refuse(f"{path}: {reason}")
    if folders:
        found, omitted = adm.resolve(folders, strict=False, exclude=exclude)
        skipped.extend(item for item in omitted if item["reason"] != "deny")
        for path in found:
            if reason := _problem(adm.owner, docs[path], adm.origin):
                skipped.append({"path": path, "reason": reason})
            elif path not in targets:
                targets.append(path)
    if len(targets) > LIMIT:
        adm.refuse("organize accepts at most 10 target notes per Work; narrow the selection")
    if not targets:
        return Done(outcome={"proposals": [], "skipped": skipped}, notice="Nothing to organize")
    return WorkDraft(mode="organize", targets=targets, skipped=skipped)


def _cursor(owner):
    def load():
        return {"seen": {row["path"]: row["sha"] for row in owner.store.read_stream("organize_seen")},
                "lock": threading.Lock()}
    return owner.cache("C1.cursor", load)


def admit_auto(adm):
    owner = adm.owner
    docs = owner.index.refresh()
    duplicates = set(owner.index.duplicate_ids(docs))
    seen = _cursor(owner)["seen"]
    eligible = [doc for doc in docs.values() if doc.policy == "remote_ok" and not exclude(doc.path)
                and not Admission.problem(doc, duplicates) and not _problem(owner, doc, adm.origin)
                and (not doc.type or (doc.type == "capture" and doc.status in (None, "unprocessed")))
                and seen.get(doc.path) != doc.sha]
    eligible.sort(key=lambda doc: (owner.first_seen(doc.path), doc.mtime, doc.path))
    if not eligible:
        return Done(outcome={"proposals": [], "skipped": [], "remaining": 0}, notice="Nothing to organize")
    return WorkDraft(mode="organize", targets=[doc.path for doc in eligible[:LIMIT]],
                     params={"remaining": max(0, len(eligible) - LIMIT)})


def folders(note):
    return {**{kind: path.rstrip("/") for path, kind in FOLDER_TYPES},
            "project": f"20_Projects/{PurePosixPath(note.path).stem}", "archived": "90_Archive"}


def vocabulary(ctx):
    allow = role_filter(ctx.workspec["origin"], False)
    return sorted({tag for doc in ctx.owner.index.refresh().values()
                   if doc.valid and not doc.conflict and SearchIndex.visible(doc, "role", allow)
                   for tag in doc.tags}, key=str.casefold)


def team_context(ctx, by):
    targets = []
    for item in ctx.inputs():
        note = ctx.snapshot(item["path"])
        handles = ctx.evidence.note_handles(note.path, by=by)
        targets.append({"target": handles[0], "handles": handles, "title": note.title, "type": note.type,
                        "status": note.status, "tags": note.list_value("tags"), "folders": folders(note)})
    return {"objective": "Propose classification and supported links for each target; never change the source notes.",
            "targets": targets, "tag_vocabulary": vocabulary(ctx),
            "statuses": {kind: [*values, "archived"] for kind, values in STATUSES.items()},
            "link_properties": list(LINK_KEYS)}


def scout(ctx):
    evidence = ctx.evidence
    allow = role_filter(ctx.workspec["origin"], False)
    limit = max(0, min(10, int(ctx.setting("max_candidates", 5))))
    found = []
    for item in ctx.inputs():
        note = ctx.snapshot(item["path"])
        handles = evidence.read_note(note.path, by="scout")
        candidates = {}
        for hit in ctx.owner.index.related(note.path, audience="role", allow=allow, limit=limit * 2)["results"]:
            candidates[hit["path"]] = hit.get("reasons", [])
        for path, why in keyword_candidates(ctx.owner, note.title + " " + note.body[:600], ctx.workspec["origin"], limit * 2):
            candidates.setdefault(path, why)
        collected = []
        for path, why in candidates.items():
            if path == note.path or len(collected) >= limit:
                continue
            doc = ctx.owner.index.refresh().get(path)
            if not doc or not doc.valid or doc.conflict:
                continue
            try:
                picked = evidence.read_note(path, by="scout")
            except Unavailable:
                continue
            if picked:
                entry = evidence.get(picked[0])
                collected.append({"handles": picked, "title": entry["title"], "type": entry["type"],
                                  "reasons": why, "excerpt": evidence.chunk_text(picked[0])[:160]})
        found.append({"target": handles[0], "candidates": collected})
    return {"targets": found, "tag_vocabulary": vocabulary(ctx)}


def _target(ctx, handle):
    entry = ctx.evidence.get(handle)
    return next((item["path"] for item in ctx.inputs()
                 if entry and item["path"] == entry["path"] and item["sha"] == entry["sha"]), None)


def _entries(payload):
    return isinstance(payload, dict) and set(payload) == {"notes"} and isinstance(payload["notes"], list)


def _evidence(value, read):
    if not isinstance(value, list) or not value or not all(isinstance(h, str) and h in read for h in value):
        return ["evidence must cite nonempty handles read in this Work"]
    return []


def _classification(ctx, entry):
    problems = []
    kind, status = entry.get("type"), entry.get("status")
    if not isinstance(kind, str) or kind not in STATUSES:
        problems.append("type must be an admitted note type")
    elif status not in (*STATUSES[kind], "archived"):
        problems.append("status is invalid for type")
    if not isinstance(entry.get("title"), str) or not entry["title"].strip() or len(entry["title"]) > 200:
        problems.append("title must contain 1-200 characters")
    path = _target(ctx, entry.get("target"))
    if path and kind in STATUSES:
        expected = folders(ctx.snapshot(path))["archived" if status == "archived" else kind]
        if entry.get("folder") != expected:
            problems.append("folder must be the conventional folder supplied in work_context")
    return problems


def _tags(entry, known):
    tags = entry.get("tags")
    if not isinstance(tags, list):
        return ["tags must be a list of {value, new}"]
    problems, seen = [], set()
    for tag in tags:
        if not isinstance(tag, dict) or set(tag) != {"value", "new"}:
            problems.append("a tag is {value, new}")
            continue
        value = tag["value"]
        if not isinstance(value, str) or not value.strip() or value != value.strip() or "#" in value or any(c.isspace() for c in value):
            problems.append("tag values must be nonempty plain strings without # or whitespace")
        elif value in seen:
            problems.append("duplicate tag")
        else:
            seen.add(value)
            if not isinstance(tag["new"], bool) or tag["new"] != (value not in known):
                problems.append(f"tag {value}: new must mark exactly tags outside the existing vocabulary")
    return problems


def validate_classifier(ctx, payload):
    if not _entries(payload):
        return ['submit {"notes": [{"target", "type", "status", "title", "tags", "folder", "evidence"}]}']
    problems, seen = [], set()
    read, known = ctx.evidence.read_handles(), set(vocabulary(ctx))
    for entry in payload["notes"]:
        if not isinstance(entry, dict) or set(entry) != {"target", "type", "status", "title", "tags", "folder", "evidence"}:
            problems.append("every classification needs exactly target, type, status, title, tags, folder, evidence")
            continue
        path = _target(ctx, entry["target"])
        if not path or path in seen:
            problems.append("target must uniquely name one admitted target note")
            continue
        seen.add(path)
        problems.extend(_classification(ctx, entry) + _tags(entry, known) + _evidence(entry["evidence"], read))
        if entry["target"] not in read:
            problems.append("target was not read in this Work")
    if not payload["notes"]:
        problems.append("classify at least one target")
    return problems


def validate_linker(ctx, payload):
    if not _entries(payload):
        return ['submit {"notes": [{"target", "links", "attach_to"}]}']
    problems, seen = [], set()
    read = ctx.evidence.read_handles()
    for entry in payload["notes"]:
        if not isinstance(entry, dict) or set(entry) != {"target", "links", "attach_to"}:
            problems.append("a linker note needs exactly target, links, attach_to")
            continue
        path = _target(ctx, entry["target"])
        if not path or path in seen:
            problems.append("target must uniquely name one admitted target note")
            continue
        seen.add(path)
        if entry["target"] not in read:
            problems.append("target was not read in this Work")
        if not isinstance(entry["links"], list):
            problems.append("links must be a list")
            continue
        for link in entry["links"]:
            if not isinstance(link, dict) or set(link) != {"property", "to", "evidence"}:
                problems.append("a link needs exactly property, to, evidence")
                continue
            if link["property"] not in LINK_KEYS:
                problems.append("invalid link property")
            if not isinstance(link["to"], str) or link["to"] not in read:
                problems.append("link target was not read in this Work")
            elif ctx.evidence.get(link["to"])["path"] == path:
                problems.append("a link cannot target itself")
            problems.extend(_evidence(link["evidence"], read))
        attach = entry["attach_to"]
        if attach is not None:
            if not isinstance(attach, str) or attach not in read:
                problems.append("attachment target was not read in this Work")
            elif ctx.evidence.get(attach)["path"] == path:
                problems.append("a capture cannot attach to itself")
            if ctx.snapshot(path).type != "capture":
                problems.append("only a capture may attach_to an existing note")
    return problems


def result(ctx, latest):
    # A successor may replace only one target; keep the other targets' accepted handoffs.
    merged = {item["path"]: {"target": ctx.evidence.note_handles(item["path"], by="owner")[0],
                             "classification": None, "links": [], "attach_to": None} for item in ctx.inputs()}
    for segment in ctx.work["segments"]:
        for step in segment["steps"]:
            if step["role"] not in ("classifier", "linker") or step["status"] != "completed":
                continue
            handoff = ctx.owner.store.get_json_blob(step["artifact"]["blob"])
            for entry in handoff["notes"]:
                path = _target(ctx, entry["target"])
                if path in merged:
                    if step["role"] == "classifier":
                        merged[path]["classification"] = entry
                    else:
                        merged[path].update(links=entry["links"], attach_to=entry["attach_to"])
    return {"notes": list(merged.values())}


def _judgment(target, problems, success):
    return {"target": target, "verdict": "unmet" if problems else "satisfied", "reason": "; ".join(problems) if problems else success}


def check_enums(ctx, artifact):
    found = []
    for entry in artifact["notes"]:
        problems = _classification(ctx, entry["classification"]) if entry["classification"] else ["target has no classification"]
        if any(link["property"] not in LINK_KEYS for link in entry["links"]):
            problems.append("invalid link relation property")
        found.append(_judgment(entry["target"], problems, "classification and relation enums are valid"))
    return found


def check_links(ctx, artifact):
    read = ctx.evidence.read_handles()
    found = []
    for entry in artifact["notes"]:
        handles = [link["to"] for link in entry["links"]]
        handles.extend(h for link in entry["links"] for h in link["evidence"])
        if entry["attach_to"]:
            handles.append(entry["attach_to"])
        found.append(_judgment(entry["target"], ["link target was not read in this Work"] if any(h not in read for h in handles) else [], "every link target was read"))
    return found


def check_tags(ctx, artifact):
    known = set(vocabulary(ctx))
    return [_judgment(entry["target"], _tags(entry["classification"], known) if entry["classification"] else [], "new tags are marked") for entry in artifact["notes"]]


def check_proposals(ctx, artifact):
    return [_judgment(entry["target"], ["multiple open proposals already reserve this target"]
                      if len(proposals.open_for_target(ctx.owner, _target(ctx, entry["target"]))) > 1 else [],
                      "one proposal slot per target") for entry in artifact["notes"]]


def _changes(ctx, note, entry):
    changes = []
    classification = entry["classification"]
    values = {}
    if classification:
        values = {key: classification[key] for key in ("type", "status") if note.data.get(key) != classification[key]}
    final_type = classification["type"] if classification else note.type
    # Processed is the capture lifecycle marker; converted typed notes use their own status enum.
    if note.type == "capture" and final_type == "capture" and (classification or {}).get("status") != "archived":
        values["status"] = "processed"
    if values:
        changes.append({"change_id": "type-status", "op": "set", "values": values, "role": "classifier" if classification else "linker"})
    if classification:
        if note.title != classification["title"]:
            changes.append({"change_id": "title", "op": "set", "key": "title", "value": classification["title"], "role": "classifier"})
        for index, tag in enumerate(classification["tags"]):
            if tag["value"] not in note.list_value("tags"):
                changes.append({"change_id": f"tag-{index + 1}", "op": "merge_list", "key": "tags", "value": [tag["value"]],
                                "role": "classifier", "description": f"tags: {tag['value']}" + (" (new)" if tag["new"] else "")})
        folder = classification["folder"]
        if str(PurePosixPath(note.path).parent) != folder:
            changes.append({"change_id": "move", "op": "move", "to": folder, "role": "classifier"})
    links = list(entry["links"])
    if entry["attach_to"]:
        links.append({"property": "related", "to": entry["attach_to"], "evidence": [entry["attach_to"]]})
    emitted = set()
    for link in links:
        path = ctx.evidence.get(link["to"])["path"]
        key = link["property"]
        value = f"[[{path.removesuffix('.md')}]]"
        if (key, value) not in emitted and value not in note.list_value(key):
            changes.append({"change_id": f"link-{len(emitted) + 1}", "op": "merge_list", "key": key, "value": [value], "role": "linker"})
            emitted.add((key, value))
    return changes


def finish(ctx, artifact):
    outcomes, settled = [], {}
    table = ctx.evidence.table()
    for entry in artifact["notes"]:
        path = _target(ctx, entry["target"])
        note = ctx.snapshot(path)
        changes = _changes(ctx, note, entry)
        inputs = {}
        for handle in ctx.evidence.handles_in(entry):
            source = table[handle]
            inputs[(source["path"], source["sha"])] = parse(source["path"], ctx.blob(source["blob"]))
        if changes:
            outcome = proposals.write_or_regenerate(ctx, note, changes, kind="organize", inputs=list(inputs.values()),
                                                    refresh_stale=ctx.workspec["origin"] == "user")
        else:
            outcome = {"status": "no_changes", "path": None}
        outcomes.append({"target": path, "status": outcome["status"], "proposal": outcome["path"]})
        if outcome["status"] in ("created", "regenerated", "unchanged", "user_edited", "stale", "no_changes"):
            settled[path] = note.sha
    cursor = _cursor(ctx.owner)
    with cursor["lock"]:
        for path, sha in settled.items():
            if cursor["seen"].get(path) != sha:
                ctx.owner.store.append("organize_seen", {"path": path, "sha": sha, "at": iso(ctx.clock.now())})
                cursor["seen"][path] = sha
    return Done(outcome={"proposals": outcomes, "skipped": ctx.workspec["skipped"], "remaining": ctx.params.get("remaining", 0)},
                notice=f"Organize produced {sum(bool(row['proposal']) for row in outcomes)} proposal(s); review before applying")

"""Capability C4 Enhance: the Thinking Team (docs/CAPABILITIES.md C4).

``enhance {mode, path?, digest?, question?, window?}``:
- ``develop`` strengthens one idea, knowledge or question note (``path``).
- ``ask`` answers a ``question`` from the Vault (``path`` optionally names a question note).
  A question no readable note addresses completes as "not covered" with no Work and no report.
- ``review`` interprets a project note (``path``) within a date ``window`` ``{from, to}``: the
  window selects which notes and changes to interpret; it is not a task filter.

The ThinkingManager composes the Graph from the pool declared in ``C4.json``: ``retriever``
(static, below), ``connector``, ``challenger`` and ``synthesizer`` (always the last producer).
The Owner renders the synthesizer's report into ``01_AI_Review/Reports/`` (create-only): its
language follows the focal note (ask: the question), every claim is labeled, citations are
wikilinks with line locators and short hashes, and it states when an input changed after
the snapshot. Refuses deny, missing and unreadable inputs.
"""

from __future__ import annotations

import re
from datetime import date, datetime, time
from typing import Any

from ..capabilities import Done, Registry, WorkDraft
from ..engine import EffectContext, StepContext
from ..evidence import Unavailable
from ..notes import render_list, render_scalar
from ..owner import Admission, Owner
from ..policy import model_may_read, strictest
from ..search import SearchIndex, role_filter
from ..util import SEOUL, iso, new_uuid
from ..vault import PROPOSALS_DIR, REPORTS_DIR, SYSTEM_ROOT, VaultPathError

CAPABILITY = "C4"
TEAM = "ThinkingTeam"
LABELS = ("cited", "inference", "not_found")
LABEL_TEXT = {
    "en": {"cited": "cited", "inference": "inference", "not_found": "not found"},
    "ko": {"cited": "인용", "inference": "추론", "not_found": "찾지 못함"},
}
MODE_TITLE = {
    "en": {"develop": "Develop", "ask": "Ask", "review": "Review"},
    "ko": {"develop": "발전", "ask": "묻기", "review": "돌아보기"},
}
CHANGED = {
    "en": "Input changed after the snapshot: {links}. This report cites the snapshot this Work read.",
    "ko": "입력 노트가 스냅샷 이후 바뀌었습니다: {links}. 이 보고서는 작업이 읽은 스냅샷을 인용합니다.",
}
OBJECTIVES = {
    "develop": "Strengthen the user's thinking about the focal note with what the Vault already holds.",
    "ask": "Answer the user's question from the Vault, separating what the notes say from inference.",
    "review": "Interpret the project's notes and changes within the window: what moved, stalled and is open.",
}
HANDOFFS = {
    "connector": ("related", "connections", "tensions"),
    "challenger": ("gaps", "counterpoints", "open_questions"),
}
MODE_PARAMS = {  # (required, optional) parameters besides mode
    "develop": ({"path"}, {"digest"}),
    "ask": ({"question"}, {"path", "digest"}),
    "review": ({"path", "window"}, {"digest"}),
}
HANGUL = re.compile(r"[\uac00-\ud7a3]")
PARTICLES = ("에서", "으로", "에게", "까지", "부터", "처럼", "은", "는", "이", "가", "을", "를", "의", "에", "도", "와", "과", "로")
STOPWORDS = {"the", "and", "what", "how", "why", "who", "when", "where", "which", "is", "are", "was", "were", "of",
             "to", "in", "an", "for", "on", "with", "does", "do", "did", "can", "about", "this", "that", "my", "me"}
CLAIM_CHARS = 2000


def register(reg: Registry) -> None:
    reg.command("enhance", admit)
    reg.team(
        TEAM,
        static={"retriever": retriever},
        validate={
            "connector": handoff_validator("connector"),
            "challenger": handoff_validator("challenger"),
            "synthesizer": validate_report,
        },
        context=team_context,
        finish=finish,
    )
    reg.check("enh.citations_read", check_citations_read)
    reg.check("enh.claims_labeled", check_claims_labeled)
    reg.check("enh.sections_present", check_sections_present)


# ---------------------------------------------------------------- admission


def admit(adm: Admission) -> WorkDraft | Done:
    mode = adm.params["mode"]
    required, optional = MODE_PARAMS[mode]
    given = {name for name in ("path", "digest", "question", "window") if name in adm.params}
    if missing := required - given:
        adm.refuse(f"enhance {mode} needs {', '.join(sorted(missing))}")
    if extra := given - required - optional:
        adm.refuse(f"enhance {mode} does not use {', '.join(sorted(extra))}")
    if "digest" in given and "path" not in given:
        adm.refuse("digest belongs to path")
    params: dict[str, Any] = {}
    targets: list[str] = []
    if "path" in given:
        path, doc = _focal(adm, adm.params["path"], adm.params.get("digest"))
        if mode == "review" and doc.type != "project":
            adm.refuse(f"{path}: review needs a project note")
        targets, params["path"] = [path], path
    if mode == "ask":
        params["question"] = adm.params["question"].strip()
        if not params["question"]:
            adm.refuse("the question is empty")
        if not targets and not keyword_candidates(adm.owner, params["question"], adm.origin, 1):
            return Done(
                status="completed",
                outcome={"mode": "ask", "covered": False, "report": None},
                notice="Not covered: no readable note in the Vault addresses this question; no report written",
            )
    if mode == "review":
        params["window"] = _window(adm, adm.params["window"])
    return WorkDraft(mode=mode, targets=targets, params=params)


def _exclude(path: str) -> str | None:
    if path.startswith(SYSTEM_ROOT):
        return "a system note"
    if path.startswith(PROPOSALS_DIR + "/"):
        return "a KnowledgeOS proposal"
    return None


def _focal(adm: Admission, raw: str, digest: str | None) -> tuple[str, Any]:
    """Exactly one readable note: missing, deny, conflicting and unreadable inputs are refused."""
    normalized = None
    if not raw.startswith("id:"):
        try:
            normalized = adm.owner.vault.normalize(raw.strip().rstrip("/"))
        except VaultPathError:
            adm.refuse(f"invalid path: {raw}")
    digests = {normalized: digest} if digest and normalized else None
    targets, _ = adm.resolve([raw], digests, strict=True, exclude=_exclude)
    if len(targets) != 1 or (normalized is not None and targets[0] != normalized):
        adm.refuse("enhance works on exactly one note")
    path = targets[0]
    doc = adm.owner.index.refresh()[path]
    if doc.type in ("report", "proposal"):
        adm.refuse(f"{path}: a KnowledgeOS {doc.type} is not evidence until its type changes")
    if not model_may_read(doc.policy, adm.origin, False):
        adm.refuse(f"{path}: ai_policy {doc.policy} does not allow model roles in this Work")
    return path, doc


def _bound(text: str, end: bool) -> datetime | None:
    try:
        day = date.fromisoformat(text)
    except ValueError:
        try:
            moment = datetime.fromisoformat(text)
        except ValueError:
            return None
        return moment if moment.tzinfo else moment.replace(tzinfo=SEOUL)
    return datetime.combine(day, time.max if end else time.min, SEOUL)


def _window(adm: Admission, window: dict[str, str]) -> dict[str, str]:
    if set(window) != {"from", "to"}:
        adm.refuse("window needs exactly from and to")
    start, stop = _bound(window["from"], False), _bound(window["to"], True)
    if start is None or stop is None:
        adm.refuse("window dates must be ISO-8601 dates")
    if start > stop:
        adm.refuse("window from is after to")
    return {"from": window["from"], "to": window["to"]}


# ---------------------------------------------------------------- retrieval


def terms(text: str) -> list[str]:
    """Search terms from a title or question: words of 2+ characters, Korean particles dropped."""
    found: list[str] = []
    for word in re.findall(r"\w+", text.casefold()):
        if HANGUL.search(word):
            for particle in PARTICLES:
                if len(word) > len(particle) + 1 and word.endswith(particle):
                    word = word[: -len(particle)]
                    break
        if len(word) >= 2 and word not in STOPWORDS and not word.isdigit() and word not in found:
            found.append(word)
    return found[:12]


def keyword_candidates(owner: Owner, text: str, origin: str, limit: int) -> list[tuple[str, list[str]]]:
    """Notes matching any term, ranked by matched terms, within the model policy for ``origin``."""
    allow = role_filter(origin, False)
    scores: dict[str, int] = {}
    for term in terms(text):
        for hit in owner.index.keyword(term, audience="role", allow=allow, limit=50)["results"]:
            scores[hit["path"]] = scores.get(hit["path"], 0) + 1
    ranked = sorted(scores.items(), key=lambda item: (-item[1], item[0]))
    return [(path, ["keyword"]) for path, _ in ranked[:limit]]


def _in_window(owner: Owner, path: str, mtime: float, window: dict[str, str]) -> bool:
    start, stop = _bound(window["from"], False), _bound(window["to"], True)
    assert start is not None and stop is not None
    moments = [datetime.fromtimestamp(mtime, SEOUL)]
    note = owner.vault.read(path)
    created = note.data.get("created") if note is not None else None
    if isinstance(created, str):
        moment = _bound(created.strip(), False)
        if moment is not None:
            moments.append(moment)
    return any(start <= moment <= stop for moment in moments)


def _review_candidates(ctx: StepContext, project: str, limit: int) -> list[tuple[str, list[str]]]:
    """Notes linked with the project or in its folder whose creation or change is in the window."""
    owner = ctx.owner
    allow = role_filter(ctx.workspec["origin"], False)
    docs = owner.index.refresh()
    reasons: dict[str, list[str]] = {}
    for hit in owner.index.related(project, audience="role", allow=allow, limit=100)["results"]:
        reasons[hit["path"]] = list(hit.get("reasons", []))
    folder = project.rsplit("/", 1)[0] if "/" in project else None
    if folder:
        for path, doc in docs.items():
            if path.startswith(folder + "/") and path != project and SearchIndex.visible(doc, "role", allow):
                reasons.setdefault(path, []).append("project_folder")
    window = ctx.params["window"]
    chosen = [
        (path, why) for path, why in reasons.items()
        if path in docs and _in_window(owner, path, docs[path].mtime, window)
    ]
    chosen.sort(key=lambda item: (-docs[item[0]].mtime, item[0]))
    return chosen[:limit]


def retriever(ctx: StepContext) -> dict[str, Any]:
    """Static teammate: evidence candidates through C3 within the Work policy, read as evidence."""
    evidence = ctx.evidence
    limit = int(ctx.setting("max_candidates", 8))
    inputs = ctx.inputs()
    artifact: dict[str, Any] = {"mode": ctx.mode, "focus": None, "candidates": []}
    focal = inputs[0]["path"] if inputs else None
    if focal is not None:
        try:
            handles = evidence.read_note(focal, by="retriever")
        except Unavailable as error:
            raise LookupError(f"the focal note is no longer readable: {error}") from error
        artifact["focus"] = {"title": inputs[0]["title"], "handles": handles}
    if ctx.mode == "review":
        assert focal is not None
        found = _review_candidates(ctx, focal, limit)
        artifact["window"] = ctx.params["window"]
    else:
        allow = role_filter(ctx.workspec["origin"], False)
        ranked: dict[str, list[str]] = {}
        if focal is not None:
            for hit in ctx.owner.index.related(focal, audience="role", allow=allow, limit=limit * 2)["results"]:
                ranked[hit["path"]] = list(hit.get("reasons", []))
        text = ctx.params["question"] if ctx.mode == "ask" else inputs[0]["title"]
        for path, why in keyword_candidates(ctx.owner, text, ctx.workspec["origin"], limit * 2):
            ranked.setdefault(path, []).extend(why)
        found = list(ranked.items())
        if ctx.mode == "ask":
            artifact["question"] = ctx.params["question"]
    for path, why in found:
        if len(artifact["candidates"]) >= limit:
            break
        if path == focal:
            continue
        try:
            handles = evidence.read_note(path, by="retriever")
        except Unavailable:
            continue
        if not handles:
            continue
        entry = evidence.get(handles[0]) or {}
        artifact["candidates"].append(
            {
                "title": entry.get("title"),
                "type": entry.get("type"),
                "reasons": why,
                "handles": handles,
                "excerpt": evidence.chunk_text(handles[0]).strip()[:160],
            }
        )
    return artifact


# ---------------------------------------------------------------- role context and validation


def sections(ctx: StepContext) -> list[dict[str, str]]:
    return ctx.owner.catalog.capability(CAPABILITY)["modes"][ctx.mode]["sections"]


def language(ctx: StepContext) -> str:
    """``ko`` when the focal note (ask: the question) contains Hangul, else ``en``."""
    if ctx.mode == "ask":
        text = ctx.params.get("question", "")
    else:
        inputs = ctx.inputs()
        note = ctx.snapshot(inputs[0]["path"]) if inputs else None
        text = f"{note.title}\n{note.body}" if note is not None else ""
    return "ko" if HANGUL.search(text) else "en"


def team_context(ctx: StepContext, by: str) -> dict[str, Any]:
    """C4 part of ``work_context``: objective, language, section IDs, focal handles, question, window."""
    view: dict[str, Any] = {
        "objective": OBJECTIVES[ctx.mode],
        "language": language(ctx),
        "sections": [section["id"] for section in sections(ctx)],
    }
    inputs = ctx.inputs()
    if inputs:
        view["focus"] = {"title": inputs[0]["title"], "handles": ctx.evidence.note_handles(inputs[0]["path"], by=by)}
    if ctx.mode == "ask":
        view["question"] = ctx.params["question"]
    if ctx.mode == "review":
        view["window"] = ctx.params["window"]
    return view


def _claim_problems(where: str, claims: Any, read: set[str], labeled: bool) -> list[str]:
    if not isinstance(claims, list):
        return [f"{where} must be a list of claims"]
    problems = []
    allowed = {"text", "citations", "label"} if labeled else {"text", "citations"}
    for number, claim in enumerate(claims, start=1):
        at = f"{where}[{number}]"
        if not isinstance(claim, dict):
            problems.append(f"{at}: a claim is an object {sorted(allowed)}")
            continue
        if extra := set(claim) - allowed:
            problems.append(f"{at}: unknown fields {', '.join(sorted(map(str, extra)))}")
        text = claim.get("text")
        if not isinstance(text, str) or not text.strip():
            problems.append(f"{at}: needs text")
        elif len(text) > CLAIM_CHARS:
            problems.append(f"{at}: text is longer than {CLAIM_CHARS} characters")
        citations = claim.get("citations", [])
        if not isinstance(citations, list) or not all(isinstance(item, str) for item in citations):
            problems.append(f"{at}: citations must be a list of evidence handles")
            citations = []
        unread = [item for item in citations if item not in read]
        if unread:
            problems.append(f"{at}: {', '.join(unread)} was not read in this Work; cite only handles you read")
        if labeled:
            label = claim.get("label")
            if label not in LABELS:
                problems.append(f"{at}: label must be cited, inference or not_found")
            elif label == "cited" and not citations:
                problems.append(f"{at}: a cited claim needs at least one citation")
        elif not citations:
            problems.append(f"{at}: every claim needs at least one citation")
    return problems


def handoff_validator(teammate: str):
    keys = HANDOFFS[teammate]

    def validate(ctx: StepContext, payload: Any) -> list[str]:
        if not isinstance(payload, dict):
            return [f"submit {{{', '.join(repr(k) for k in keys)}}}, each a list of claims"]
        problems = []
        if extra := set(payload) - set(keys):
            problems.append(f"unknown fields {', '.join(sorted(map(str, extra)))}; use {', '.join(keys)}")
        read = ctx.evidence.read_handles()
        for key in keys:
            problems.extend(_claim_problems(key, payload.get(key, []), read, labeled=False))
        return problems

    return validate


def validate_report(ctx: StepContext, payload: Any) -> list[str]:
    if not isinstance(payload, dict) or set(payload) != {"sections"} or not isinstance(payload["sections"], dict):
        return ['submit {"sections": {"<section id>": [claim, ...]}}']
    ids = [section["id"] for section in sections(ctx)]
    given = payload["sections"]
    problems = [
        f"missing section {sid}; a section the Vault cannot fill gets one not_found claim"
        for sid in ids if sid not in given
    ]
    problems.extend(f"unknown section {sid}; sections are {', '.join(ids)}" for sid in given if sid not in ids)
    read = ctx.evidence.read_handles()
    for sid in ids:
        if sid not in given:
            continue
        if isinstance(given[sid], list) and not given[sid]:
            problems.append(f"section {sid} has no claim")
        problems.extend(_claim_problems(sid, given[sid], read, labeled=True))
    return problems


# ---------------------------------------------------------------- static EvalOfficer checks


def _claims(artifact: Any) -> list[tuple[str, dict[str, Any]]]:
    found = []
    for sid, claims in artifact["sections"].items():
        for claim in claims:
            found.append((sid, claim))
    return found


def _cited(artifact: Any) -> list[str]:
    cited: list[str] = []
    for _, claim in _claims(artifact):
        for handle in claim.get("citations", []):
            if handle not in cited:
                cited.append(handle)
    return cited


def check_citations_read(ctx: StepContext, artifact: Any) -> list[dict[str, Any]]:
    read = ctx.evidence.read_handles()
    unread = [handle for handle in _cited(artifact) if handle not in read]
    if not unread:
        return [{"target": None, "verdict": "satisfied", "reason": "every citation was read in this Work"}]
    return [{"target": h, "verdict": "unmet", "reason": f"{h} was not read in this Work"} for h in unread]


def check_claims_labeled(ctx: StepContext, artifact: Any) -> list[dict[str, Any]]:
    found = []
    for sid, claim in _claims(artifact):
        label = claim.get("label")
        if label not in LABELS:
            found.append({"target": None, "verdict": "unmet", "reason": f"a claim in {sid} is not labeled"})
        elif label == "cited" and not claim.get("citations"):
            found.append({"target": None, "verdict": "unmet", "reason": f"a cited claim in {sid} has no citation"})
    return found or [{"target": None, "verdict": "satisfied", "reason": "every claim is labeled"}]


def check_sections_present(ctx: StepContext, artifact: Any) -> list[dict[str, Any]]:
    ids = [section["id"] for section in sections(ctx)]
    given = artifact["sections"]
    found = [{"target": None, "verdict": "unmet", "reason": f"section {sid} is missing or empty"}
             for sid in ids if not given.get(sid)]
    found.extend({"target": None, "verdict": "unmet", "reason": f"section {sid} is not a {ctx.mode} section"}
                 for sid in given if sid not in ids)
    return found or [{"target": None, "verdict": "satisfied", "reason": "every section is present"}]


# ---------------------------------------------------------------- report


def _file_title(text: str) -> str:
    cleaned = re.sub(r"\s+", " ", re.sub(r'[\\/:*?"<>|#^\[\]]', " ", text)).strip()
    return cleaned[:80].rstrip() or "Report"


def _title(ctx: StepContext, lang: str) -> str:
    head = MODE_TITLE[lang][ctx.mode]
    inputs = ctx.inputs()
    if ctx.mode == "ask":
        question = re.sub(r"\s+", " ", ctx.params["question"]).strip()
        return f"{head} — {question[:60]}"
    if ctx.mode == "review":
        window = ctx.params["window"]
        return f"{head} — {inputs[0]['title']} ({window['from']} – {window['to']})"
    return f"{head} — {inputs[0]['title']}"


def finish(ctx: EffectContext, artifact: dict[str, Any]) -> Done:
    """Render the synthesizer's report and create it in ``01_AI_Review/Reports/``."""
    evidence = ctx.evidence
    table = evidence.table()
    lang = language(ctx)
    inputs = ctx.inputs()
    read = [handle for handle, entry in table.items() if entry["read"]]
    shas: dict[str, set[str]] = {}
    for item in inputs:
        shas.setdefault(item["path"], set()).add(item["sha"])
    for handle in read:
        shas.setdefault(table[handle]["path"], set()).add(table[handle]["sha"])
    policy = strictest([item["policy"] for item in inputs] + [table[handle]["policy"] for handle in read])
    changed = []
    for path, seen in shas.items():
        current = ctx.owner.vault.read(path)
        if current is None or current.sha not in seen:
            changed.append(path)
    title = _title(ctx, lang)
    links = [f"[[{path.removesuffix('.md')}]]" for path in shas]
    lines = [
        "---",
        f"id: {new_uuid()}",
        "type: report",
        f"created: {iso(ctx.clock.now())}",
        f"title: {render_scalar(title)}",
        f"ai_policy: {policy}",
        f"kos_work: {ctx.work_id}",
        f"kos_mode: {ctx.mode}",
        f"inputs: {render_list(links)}",
        "---",
        "",
        f"# {title}",
        "",
    ]
    if changed:
        statement = CHANGED[lang].format(links=", ".join(f"[[{path.removesuffix('.md')}]]" for path in changed))
        lines += [f"> {statement}", ""]
    labels = LABEL_TEXT[lang]
    for section in sections(ctx):
        lines += [f"## {section[lang]}", ""]
        for claim in artifact["sections"].get(section["id"], []):
            text = re.sub(r"\s+", " ", claim["text"]).strip()
            cited = "; ".join(evidence.wikilink(handle) for handle in claim.get("citations", []))
            lines.append(f"- [{labels[claim['label']]}] {text}" + (f" — {cited}" if cited else ""))
        lines.append("")
    data = "\n".join(lines).encode("utf-8")
    name = f"{ctx.clock.now().strftime('%Y-%m-%d')} {_file_title(title)}.md"
    final = ctx.create(f"{REPORTS_DIR}/{name}", data)
    return Done(
        status="completed",
        outcome={
            "mode": ctx.mode,
            "report": final,
            "citations": [evidence.citation(handle) for handle in _cited(artifact)],
            "inputs_changed": changed,
        },
        notice=f"Report written: {final}",
    )

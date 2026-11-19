"""Privacy-filtered digest content, single-attempt delivery and change-driven alerts."""

from __future__ import annotations

import logging
import subprocess
import threading
from datetime import datetime, timedelta
from urllib.parse import urlencode

from ..capabilities import Registry, WorkDraft
from ..policy import effective
from ..util import canonical, digest, iso
from .c5_stewardship import attention, check, lifecycle_targets

CAPABILITY = "C8"
log = logging.getLogger("knowledgeos.notify")


def register(reg: Registry) -> None:
    reg.command("digest.run", lambda adm: WorkDraft(mode="digest", targets=lifecycle_targets(adm.owner)))
    reg.check("ntf.stewardship", check)
    reg.on_start(start)


def obsidian_link(owner, path: str) -> str:
    return "obsidian://open?" + urlencode({"vault": owner.config.vault.name, "file": path})


def console_link(owner, section: str) -> str:
    return owner.config.endpoint + "/#" + section


def _shareable(owner, paths):
    """Recheck current policy at the last disclosure boundary, not the cached title."""
    result = []
    for path in paths:
        note = owner.vault.read(path)
        if note is None or effective(note, owner.config.default_policy) not in ("remote_ok", "ask"):
            continue
        if note.type in ("proposal", "report"):
            sources = list(note.list_value("inputs"))
            target = note.data.get("target_path")
            if target:
                sources.append(f"[[{target.removesuffix('.md')}]]")
            safe = True
            for link in sources:
                destination = owner.vault.resolve_link(link.removeprefix("[[").removesuffix("]]"))
                source = owner.vault.read(destination) if destination else None
                if source is None or effective(source, owner.config.default_policy) not in ("remote_ok", "ask"):
                    safe = False
                    break
            if not safe:
                continue
        result.append((note.title, obsidian_link(owner, path)))
    return result


def alert_scan(owner) -> None:
    """Idempotent even when the observer and a digest encounter the same item."""
    with owner.store.lock("C8.alerts"):
        for item in attention(owner):
            if item["kind"] not in ("conflict", "unknown", "failed_job") or item.get("unknown_kind") == "notice":
                continue
            key = digest(item["item_id"])
            if owner.store.get("alerts", key):
                continue
            notes = _shareable(owner, item["paths"])
            if item["paths"] and not notes:
                continue
            titles = ", ".join(title for title, _ in notes)
            title = f"{item['kind'].replace('_', ' ').capitalize()}: {titles or item['title']}"
            links = [link for _, link in notes] or [console_link(owner, "jobs" if item["kind"] == "failed_job" else "unknowns")]
            # Claim before delivery, including unknown delivery. It is never retransmitted.
            owner.store.put("alerts", key, {"item_id": item["item_id"], "at": iso(owner.clock.now())})
            owner.post_notice("alert", title, links, item_id=item["item_id"])


def send_digest(owner, request_id: str) -> dict:
    with owner.store.lock("C8.digest"):
        existing = next((n for n in owner.notices() if n.get("item_id") == f"digest/{request_id}"), None)
        if existing:
            return {"notice_id": existing["notice_id"], "counts": existing.get("counts", {})}
        alert_scan(owner)
        previous = owner.store.get("digest", "cursor") or {"at": None, "ids_at": []}
        new_changes = [c for c in owner.store.list("changes") if c.get("kind") == "autofix" and
                       (not previous["at"] or c["at"] > previous["at"] or (c["at"] == previous["at"] and c["change_id"] not in previous["ids_at"]))]
        paths = {"autofix": [c["path"] for c in new_changes], "proposals": [], "reports": [], "inbox_older_3_days": [], "stale_proposals": [], "revisit": []}
        now = owner.clock.now()
        for path, doc in owner.index.refresh().items():
            if doc.policy == "deny" or owner.vault.system(path):
                continue
            if doc.type == "proposal" and doc.status in ("open", "stale"):
                paths["proposals"].append(path)
                if doc.status == "stale":
                    paths["stale_proposals"].append(path)
            if doc.type == "report":
                paths["reports"].append(path)
            if doc.type == "capture" and doc.status == "unprocessed":
                note = owner.vault.read(path)
                try:
                    created = datetime.fromisoformat(note.data.get("created", ""))
                    old = created < now - timedelta(days=3)
                except (ValueError, TypeError):
                    old = False
                if old:
                    paths["inbox_older_3_days"].append(path)
        items = attention(owner)
        paths["revisit"] = [p for item in items if item["kind"] == "revisit" for p in item["paths"]]
        counts, lines, links = {}, [], []
        for section, candidates in paths.items():
            shared = _shareable(owner, candidates)
            counts[section] = len(shared)
            lines.append(f"{section.replace('_', ' ').capitalize()}: {len(shared)}" + (" — " + ", ".join(title for title, _ in shared) if shared else ""))
            links.extend(link for _, link in shared)
        decisions = []
        for item in items:
            if item["kind"] in ("autofix", "revisit", "stale_proposal"):
                continue
            shared = _shareable(owner, item["paths"])
            if item["paths"] and not shared:
                continue
            label = ", ".join(title for title, _ in shared) if shared else item["title"]
            decisions.append(f"{item['kind'].replace('_', ' ')}: {label}")
            links.extend(link for _, link in shared)
        counts["attention"] = len(decisions)
        lines.append(f"Attention: {len(decisions)}" + (" — " + "; ".join(decisions) if decisions else ""))
        links.append(console_link(owner, "attention"))
        notice = owner.post_notice("digest", "KnowledgeOS digest", list(dict.fromkeys(links)), item_id=f"digest/{request_id}", lines=lines, counts=counts)
        at = iso(now)
        ids_at = [c["change_id"] for c in owner.store.list("changes") if c.get("kind") == "autofix" and c["at"] == at]
        owner.store.put("digest", "cursor", {"at": at, "ids_at": ids_at})
        return {"notice_id": notice["notice_id"], "counts": counts}


def start(owner) -> None:
    command = owner.setting("C8", "notifier_command", [])
    if owner.notifier is None and command:
        if not isinstance(command, list) or any(not isinstance(arg, str) or not arg for arg in command):
            raise ValueError("C8.notifier_command must be an argv list")

        def deliver(notice):
            try:
                result = subprocess.run(command, input=canonical(notice), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                        timeout=owner.setting("C8", "notifier_timeout_seconds", 10), check=False)
            except (OSError, subprocess.TimeoutExpired):
                return "unknown"
            return "delivered" if result.returncode == 0 else "not_delivered" if result.returncode == 3 else "unknown"

        owner.notifier = deliver
    for notice in owner.notices():
        if notice["delivery"] in ("pending", "delivering"):
            owner.store.update("notices", notice["notice_id"], lambda n: n.update(delivery="unknown"))
            owner.add_unknown("notice", {"notice_id": notice["notice_id"]})
    stop = threading.Event()
    wake = threading.Event()

    def fingerprint():
        paths = [owner.vault.abs(path) for path in owner.vault.iter_paths()]
        for kind in ("unknowns", "jobs"):
            paths.extend(sorted((owner.store.root / kind).glob("*.json")))
        stamps = []
        for path in paths:
            try:
                stat = path.stat()
                stamps.append((str(path), stat.st_mtime_ns, stat.st_size, stat.st_ino, stat.st_ctime_ns))
            except FileNotFoundError:
                continue
        return tuple(stamps)

    def observe():
        previous = None
        while not stop.is_set():
            current = fingerprint()
            if current != previous:
                try:
                    alert_scan(owner)
                except Exception:
                    log.exception("alert scan failed")
                previous = current
            wake.wait(0.1)
            wake.clear()

    thread = threading.Thread(target=observe, name="kos-notices", daemon=True)
    thread.start()

    def close():
        stop.set()
        wake.set()
        thread.join()

    owner.on_close(close)

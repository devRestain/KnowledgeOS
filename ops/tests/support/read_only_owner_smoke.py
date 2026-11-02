"""Prepare disposable roots or check the real read-only Compose HTTP service (no pytest)."""

from __future__ import annotations

import argparse
import hashlib
import http.client
import json
import os
import time
from pathlib import Path
from urllib.parse import urlsplit

SOURCE = "40_Knowledge/Notes/정보의 빈칸은 공포의 상상을 강화한다.md"
QUERY = "ownerreadonlyfixtureevidence"


def snapshot(root: Path) -> dict[str, str]:
    return {path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
            for kind in ("vault", "state", "runtime")
            for path in (root / kind).rglob("*") if path.is_file()}


def prepare(root: Path) -> None:
    # Imports stay inside container preparation; the host HTTP check is stdlib only.
    from support.control_factory import make_portable_fixture_root
    from vaultops.application.knowledge import KnowledgeApplication
    from vaultops.application.read_tools import KnowledgeReadTools
    from vaultops.paths import resolve_paths
    from vaultops.projection import generate_projection

    os.umask(0o077)
    control = make_portable_fixture_root(
        root, extra_inputs=("ops/config/team-catalog.json", "ops/src"))
    roots = resolve_paths(control)
    source = roots.vault / SOURCE
    source.write_bytes(source.read_bytes() + f"\n{QUERY}\n".encode())
    generate_projection(roots)
    application = KnowledgeApplication.from_roots(roots)
    report, code = application.search(QUERY, limit=10)
    assert code == 0
    candidate = next(item for item in report["candidates"] if item["path"] == SOURCE)
    citation = KnowledgeReadTools(application).knowledge_read(
        application.reference(SOURCE), candidate["chunk_locator"], max_chars=320)
    expected = {"path": SOURCE, "resource_reference": citation["resource_reference"],
                "chunk_locator": citation["chunk_locator"], "chunk_hash": citation["chunk_hash"],
                "excerpt": citation["text"]}
    (root / "expected.json").write_text(json.dumps(expected, ensure_ascii=False))
    (root / "baseline.json").write_text(json.dumps(snapshot(root), sort_keys=True))
    print("Disposable read-only Owner fixture prepared")


def check(root: Path, url: str) -> None:
    target = urlsplit(url)
    if (target.scheme != "http" or target.hostname != "127.0.0.1"
            or target.username or target.password or target.path not in {"", "/"}
            or target.query or target.fragment):
        raise ValueError("Smoke requires an exact host-loopback HTTP endpoint")
    expected = json.loads((root / "expected.json").read_text())
    baseline = json.loads((root / "baseline.json").read_text())

    def call(route: str, payload: dict) -> tuple[int, dict]:
        connection = http.client.HTTPConnection("127.0.0.1", target.port or 17900, timeout=10)
        try:
            connection.request("POST", "/owner/" + route, json.dumps(payload), headers={
                "Content-Type": "application/json"})
            response = connection.getresponse()
            return response.status, json.loads(response.read())
        finally:
            connection.close()

    for attempt in range(40):
        try:
            status, value = call("owner/status", {})
            if status == 200:
                break
        except (OSError, http.client.HTTPException):
            pass
        time.sleep(0.5)
    else:
        raise RuntimeError("Read-only Owner did not become available")
    assert value["read_only"] is True
    query = {"query": QUERY, "mode": "lexical"}
    status, report = call("search/query", query)
    assert status == 200 and report["mode"] == "lexical"
    result = next(item for item in report["results"] if item["path"] == SOURCE)
    assert {key: result[key] for key in expected} == expected
    assert call("work/list", {"path": SOURCE}) == (200, {"works": []})
    for route in ("work/submit", "work/intake", "work/intake-template", "work/prepare-metadata",
                  "work/approve", "work/apply", "work/effect", "work/dispatch", "worker/finished",
                  "proposal/decide", "proposal/apply", "daily/decide", "effect/apply", "unknown/mutation"):
        status, denial = call(route, {"actor_id": "owner.fixture", "decision": "approve"})
        assert status == 403 and denial["errors"][0]["code"] == "READ_ONLY"
    assert call("search/query", {**query, "actor_id": "owner.fixture"})[0] == 403
    assert snapshot(root) == baseline
    source = root / "vault" / SOURCE
    original = source.read_bytes()
    try:
        source.write_bytes(original + b"\nChanged only by disposable smoke driver.\n")
        changed = snapshot(root)
        status, denial = call("search/query", query)
        assert status in {403, 409} and denial["status"] == "FAIL"
        assert snapshot(root) == changed
    finally:
        source.write_bytes(original)
    assert snapshot(root) == baseline
    print("PASS: real relay HTTP citation, actor restriction, effect denial, stale refusal and unchanged roots")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("prepare", "check"))
    parser.add_argument("root", type=Path)
    parser.add_argument("--url", default="http://127.0.0.1:17901")
    arguments = parser.parse_args()
    if arguments.action == "prepare":
        prepare(arguments.root)
    else:
        check(arguments.root, arguments.url)


if __name__ == "__main__":
    main()

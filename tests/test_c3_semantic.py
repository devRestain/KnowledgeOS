"""C3 semantic search acceptance (docs/CAPABILITIES.md SRC-3, SRC-5..SRC-8) through the ExOps
API, the CLI and role tools bound to admitted Works, against a fake Ollama on a real loopback socket.

The fake embeds text as a hashed bag of words, so notes sharing words are similar, and logs every
request: what reached the embedding service is exactly what the Owner sent.
"""

from __future__ import annotations

import hashlib
import json
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from conftest import conforming_note, enhance_scripts, organize_scripts

from knowledgeos.config import ConfigError

IDEAS = "40_Knowledge/Ideas/"
NOTES = "40_Knowledge/Notes/"
QUERY_PREFIX = "Instruct:"


class FakeOllama:
    """``POST /api/embed {model, input}`` on 127.0.0.1. ``allowance`` answers HTTP 500 once spent."""

    DIM = 2048

    def __init__(self) -> None:
        self.models = {"fake-embed", "fake-embed-2"}
        self.calls: list[dict] = []
        self.allowance: int | None = None
        self.port = 0
        self._lock = threading.Lock()
        self._server: ThreadingHTTPServer | None = None

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def start(self) -> FakeOllama:
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args) -> None:
                pass

            def do_POST(self) -> None:
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
                status, payload = fake.answer(self.path, body)
                data = json.dumps(payload).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        self._server = ThreadingHTTPServer(("127.0.0.1", self.port), Handler)
        self.port = self._server.server_address[1]
        threading.Thread(target=self._server.serve_forever, daemon=True).start()
        return self

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None

    def answer(self, path: str, body: dict) -> tuple[int, dict]:
        with self._lock:
            if path != "/api/embed":
                return 404, {"error": "not found"}
            if body.get("model") not in self.models:
                return 404, {"error": f"model \"{body.get('model')}\" not found"}
            if self.allowance is not None:
                if self.allowance <= 0:
                    return 500, {"error": "boom"}
                self.allowance -= 1
            self.calls.append({"model": body["model"], "input": list(body["input"])})
            return 200, {"model": body["model"], "embeddings": [self.vector(t, body["model"]) for t in body["input"]]}

    def vector(self, text: str, model: str) -> list[float]:
        if text.startswith(QUERY_PREFIX):
            text = text.split("\nQuery: ", 1)[1]
        vector = [0.0] * self.DIM
        for token in re.findall(r"\w+", text.casefold()):
            vector[int(hashlib.sha256((model + token).encode()).hexdigest()[:8], 16) % self.DIM] += 1.0
        if not any(vector):
            vector[0] = 1.0
        return vector

    def documents(self) -> list[str]:
        """Every note chunk sent for embedding, in order."""
        return [t for call in self.calls for t in call["input"] if not t.startswith(QUERY_PREFIX)]

    def queries(self) -> list[str]:
        return [t for call in self.calls for t in call["input"] if t.startswith(QUERY_PREFIX)]


@pytest.fixture
def ollama():
    fake = FakeOllama().start()
    yield fake
    fake.stop()


def semantic_settings(ollama, model="fake-embed", **semantic):
    settings = {"ollama_url": ollama.url, "model": model, "batch_size": 4, "related_min_score": 0.3, **semantic}
    return {"C3": {"semantic": settings}}


def semantic_kos(make_kos, ollama, model="fake-embed", **semantic):
    return make_kos(settings=semantic_settings(ollama, model, **semantic))



def paths(result):
    return [item["path"] for item in result["results"]]


def note(body: str, policy: str = "approve", kind: str = "idea", status: str = "seed", extra: str = "") -> str:
    return conforming_note(kind, status, body=body, extra=extra, policy=policy)


def semantic_state(kos) -> dict:
    status, verify = kos.get("/api/index/verify")
    assert status == 200
    return verify["semantic"]


def test_SRC_5_embeds_changed_notes_once_and_drops_deleted_and_renamed(make_kos, ollama, vault):
    kos = semantic_kos(make_kos, ollama)
    garden, pasta, boat = IDEAS + "Garden.md", IDEAS + "Pasta.md", IDEAS + "Boat.md"
    vault.write(garden, note("garden soil tomato plants\n"))
    vault.write(pasta, note("pasta sauce kitchen tomato\n"))
    vault.write(boat, note("boat harbor sail wind\n"))

    first = kos.run("index.embed", {})
    assert first["status"] == "completed", first
    assert first["outcome"] == {
        "embedded": 3, "unchanged": 0, "removed": 0, "failed": 0, "pending": 0, "model": "fake-embed",
    }
    assert len(ollama.documents()) == 3
    work = kos.get(f"/api/works/{first['work_id']}")[1]
    assert work["status"] == "completed" and work["segments"][0]["assessment"]["status"] == "satisfied"
    assert (work["workspec"]["target"], work["workspec"]["mode"]) == ("EmbeddingOfficer", "embed")
    assert paths(kos.search(mode="semantic", query="garden soil"))[0] == garden

    idle = kos.run("index.embed", {})
    assert idle["status"] == "completed" and idle["work_id"] is None
    assert (idle["outcome"]["unchanged"], idle["outcome"]["embedded"]) == (3, 0)
    assert len(ollama.documents()) == 3

    vault.write(boat, note("boat harbor sail volcano ash\n"))
    assert boat not in paths(kos.search(mode="semantic", query="boat harbor sail"))  # stale until re-embedded
    second = kos.run("index.embed", {})
    assert (second["outcome"]["embedded"], second["outcome"]["unchanged"]) == (1, 2)
    changed = ollama.documents()[3:]
    assert len(changed) == 1 and "volcano" in changed[0]
    third = kos.run("index.embed", {})
    assert third["work_id"] is None and len(ollama.documents()) == 4
    assert paths(kos.search(mode="semantic", query="volcano ash"))[0] == boat

    vault.path(garden).unlink()
    vault.path(pasta).rename(vault.path(IDEAS + "Noodles.md"))
    fourth = kos.run("index.embed", {})
    assert fourth["outcome"] == {
        "embedded": 1, "unchanged": 1, "removed": 2, "failed": 0, "pending": 0, "model": "fake-embed",
    }
    found = paths(kos.search(mode="semantic", query="garden soil tomato pasta sauce"))
    assert sorted(found) == [boat, IDEAS + "Noodles.md"]
    assert semantic_state(kos) == {
        "model": "fake-embed", "indexed_model": "fake-embed", "ready": True,
        "eligible": 2, "embedded": 2, "stale": 0, "missing": 0, "extra": 0, "chunks": 2,
    }


def test_SRC_6_ineligible_notes_are_never_embedded_and_dropped_when_they_become_so(make_kos, ollama, vault):
    kos = semantic_kos(make_kos, ollama)
    ineligible = {
        NOTES + "Denied.md": ("denymarker", note("denymarker alpha\n", policy="deny")),
        NOTES + "Local.md": ("localmarker", note("localmarker body\n", policy="local_only")),
        NOTES + "Legacy.md": ("legacymarker", note("legacymarker body\n", policy="ask")),
        "99_System/Templates/Tmpl.md": ("systemmarker", "systemmarker template\n"),
        "01_AI_Review/Proposals/P1.md": ("proposalmarker", note("proposalmarker one\n", kind="proposal", status="open")),
        NOTES + "Moved.md": ("movedmarker", note("movedmarker two\n", kind="proposal", status="open")),
        IDEAS + "Conflict.md": ("conflictmarker", "<<<<<<< HEAD\nconflictmarker ours\n=======\ntheirs\n>>>>>>> b\n"),
    }
    eligible = {
        NOTES + "Confidential.md": (
            "confidentialmarker",
            note("confidentialmarker body\n", policy="approve", extra="sensitivity: confidential\n"),
        ),
        "01_AI_Review/Reports/Report.md": ("reportmarker", note("reportmarker body\n", kind="report", status="draft")),
        NOTES + "Ask.md": ("askmarker", note("askmarker body\n")),
        NOTES + "Remote.md": ("remotemarker", note("remotemarker body\n", policy="approve")),
    }
    for path, (_, text) in {**ineligible, **eligible}.items():
        vault.write(path, text)

    run = kos.run("index.embed", {})
    assert run["status"] == "completed" and run["outcome"]["embedded"] == 4, run
    sent = "\n".join(ollama.documents())
    for marker, _ in ineligible.values():
        assert marker not in sent, marker
    for marker, _ in eligible.values():
        assert marker in sent, marker
    for marker, _ in [*ineligible.values(), *eligible.values()]:
        assert not set(paths(kos.search(mode="semantic", query=marker))) & set(ineligible), marker
    for path in eligible:
        assert not set(paths(kos.search(mode="related", path=path))) & set(ineligible), path
    assert semantic_state(kos)["eligible"] == 4 and semantic_state(kos)["extra"] == 0

    sent_before = len(ollama.documents())
    vault.write(NOTES + "Remote.md", note("remotemarker body\n", policy="deny"))
    vault.write(NOTES + "Ask.md", note("askmarker body\n", kind="proposal", status="open"))
    dropped = kos.run("index.embed", {})
    assert dropped["status"] == "completed", dropped
    assert (dropped["outcome"]["removed"], dropped["outcome"]["embedded"]) == (2, 0)
    assert len(ollama.documents()) == sent_before
    for query in ("remotemarker body", "askmarker body"):
        assert not {NOTES + "Remote.md", NOTES + "Ask.md"} & set(paths(kos.search(mode="semantic", query=query)))
    assert kos.run("index.embed", {})["work_id"] is None
    assert semantic_state(kos)["embedded"] == 2


def test_SRC_7_semantic_results_rank_by_similarity_with_chunk_locators(make_kos, ollama, vault):
    kos = semantic_kos(make_kos, ollama)
    fruit = note("# Apples\n\napple orchard cider harvest\n\n# Bananas\n\nbanana plantation ripe yellow\n")
    vault.write(IDEAS + "Fruit.md", fruit)
    vault.write(IDEAS + "Ships.md", note("ship harbor anchor sail\n"))
    paragraphs = [f"p{i} alphabetagammadeltaepsilon " * 5 for i in range(30)]
    long_text = note("\n\n".join(paragraphs) + " zzfinal\n")
    vault.write(IDEAS + "Long.md", long_text)
    assert kos.run("index.embed", {})["outcome"]["embedded"] == 3

    lines = fruit.splitlines()
    result = kos.search(mode="semantic", query="ripe banana plantation")
    assert result["status"] == "ok"
    best = result["results"][0]
    assert best["path"] == IDEAS + "Fruit.md"
    assert (best["line"], best["end_line"]) == (lines.index("# Bananas") + 1, len(lines))
    assert best["excerpt"].startswith("# Bananas banana plantation")
    assert (best["title"], best["type"], best["status"]) == ("Fruit", "idea", "seed")
    scores = [item["score"] for item in result["results"]]
    assert scores == sorted(scores, reverse=True) and scores[0] > 0.5
    assert ollama.queries()[0].endswith("ripe banana plantation")
    assert len(paths(result)) == len(set(paths(result)))

    long_lines = long_text.splitlines()
    tail = kos.search(mode="semantic", query="zzfinal")["results"][0]
    assert tail["path"] == IDEAS + "Long.md" and tail["end_line"] == len(long_lines)
    assert tail["line"] > 8 and long_lines[tail["line"] - 2] == "" and long_lines[tail["line"] - 1]


BASE = "garden soil tomato plants water"


def write_neighbors(vault):
    """Approved and retired-policy neighbors, plus report and denied-note exclusions."""
    vault.write(NOTES + "Remote.md", note(BASE + " remote\n", policy="approve", kind="knowledge", status="draft"))
    vault.write(NOTES + "Ask.md", note(BASE + " ask\n", kind="knowledge", status="draft"))
    vault.write(NOTES + "Local.md", note(BASE + " local\n", policy="local_only", kind="knowledge", status="draft"))
    vault.write("01_AI_Review/Reports/Rep.md", note(BASE + " report\n", policy="approve", kind="report", status="draft"))
    vault.write(NOTES + "Secret.md", note(BASE + " secret\n", policy="deny", kind="knowledge", status="draft"))


def neighbors(result):
    """Titles of a role ``related`` result; every one is labeled only as a semantic neighbor."""
    assert result["status"] == "ok"
    assert all(item["reasons"] == ["semantic"] for item in result["results"]), result
    return sorted(item["title"] for item in result["results"])


def test_SRC_7_people_and_gateway_search_keep_their_audiences(make_kos, ollama, vault):
    settings = semantic_settings(ollama)
    settings["C10"] = {"authorized_operations": {"research": {
        "query": True, "purposes": ["consultation"], "max_results": 10, "max_excerpt_chars": 160,
    }}}
    kos = make_kos(settings=settings)
    focal = NOTES + "Focal.md"
    vault.write(focal, note(BASE + " focal\n", policy="approve", kind="knowledge", status="draft"))
    write_neighbors(vault)
    assert kos.run("index.embed", {})["outcome"]["embedded"] == 4

    related = kos.search(mode="related", path=focal)
    assert {item["path"]: item["reasons"] for item in related["results"]} == {
        NOTES + "Remote.md": ["semantic"], NOTES + "Ask.md": ["semantic"],
        "01_AI_Review/Reports/Rep.md": ["semantic"],
    }
    assert all(item["line"] >= 8 for item in related["results"])
    assert NOTES + "Secret.md" not in paths(kos.search(mode="semantic", query="garden soil tomato plants water secret"))
    status, exported = kos.post("/interops/query", {
        "sender_operation_id": "research", "purpose": "consultation", "query": "garden soil tomato",
    })
    assert status == 200, exported
    assert sorted(paths(exported)) == sorted([focal, NOTES + "Ask.md", NOTES + "Remote.md"])


def test_SRC_7_scheduled_curation_role_tools_get_only_approve_semantic_neighbors(
    make_kos, ollama, vault,
):
    target = "00_Inbox/Target.md"
    vault.write(target, conforming_note("capture", "unprocessed", policy="approve", body=BASE + " target\n"))
    write_neighbors(vault)
    kos = make_kos(settings=semantic_settings(ollama))
    assert kos.run("index.embed", {})["outcome"]["embedded"] == 4

    organize_scripts(kos.runner)
    seen = {}
    original = kos.runner.scripts["linker"]

    def linker(session):
        context = session.call("work_context")
        seen["related"] = session.call("related", handle=context["targets"][0]["target"])
        seen["search"] = session.call("search", query="garden soil tomato")
        original(session)

    kos.runner.script("linker", linker)
    receipt = kos.run("organize.auto", {}, actor="schedule/curate")
    assert receipt["status"] == "completed", receipt
    assert neighbors(seen["related"]) == ["Ask", "Remote"]
    assert seen["search"]["status"] == "ok"
    assert sorted(item["title"] for item in seen["search"]["results"]) == ["Ask", "Remote", "Target"]
    work = kos.get(f"/api/works/{receipt['work_id']}")[1]
    assert work["workspec"]["origin"] == "schedule"
    assert {entry["path"] for entry in work["evidence"].values()} == {target, NOTES + "Ask.md", NOTES + "Remote.md"}


def test_SRC_7_user_work_role_tools_get_only_approve_semantic_neighbors(
    make_kos, ollama, vault,
):
    focal = NOTES + "Focal.md"
    vault.write(focal, note(BASE + " focal\n", kind="knowledge", status="draft"))
    write_neighbors(vault)
    kos = make_kos(settings=semantic_settings(ollama))
    assert kos.run("index.embed", {})["outcome"]["embedded"] == 4

    enhance_scripts(kos.runner)
    seen = {}
    original = kos.runner.scripts["connector"]

    def connector(session):
        context = session.call("work_context")
        seen["related"] = session.call("related", handle=context["focus"]["handles"][0])
        seen["search"] = session.call("search", query="garden soil tomato")
        original(session)

    kos.runner.script("connector", connector)
    receipt = kos.run("enhance", {"mode": "develop", "path": focal})
    assert receipt["status"] == "completed", receipt
    assert neighbors(seen["related"]) == ["Ask", "Remote"]
    assert seen["search"]["status"] == "ok"
    assert sorted(item["title"] for item in seen["search"]["results"]) == ["Ask", "Focal", "Remote"]


def test_SRC_8_service_down_fails_visibly_keeps_vectors_and_resumes_incrementally(make_kos, ollama, vault):
    kos = semantic_kos(make_kos, ollama)
    base = "garden soil tomato plants water"
    a, b, c = IDEAS + "A.md", IDEAS + "B.md", IDEAS + "C.md"
    vault.write(a, note(base + " alpha\n"))
    vault.write(b, note(base + " beta\n"))
    assert kos.run("index.embed", {})["outcome"]["embedded"] == 2
    vault.write(c, note(base + " gamma\n"))

    ollama.stop()
    failed = kos.run("index.embed", {})
    assert failed["status"] == "failed" and "unreachable" in failed["notice"], failed
    assert (failed["outcome"]["embedded"], failed["outcome"]["failed"]) == (0, 1)
    assert kos.get(f"/api/works/{failed['work_id']}")[1]["status"] == "failed"
    down = kos.search(mode="semantic", query="garden soil")
    assert down["status"] == "unavailable" and down["results"] == [] and "unreachable" in down["reason"]
    assert semantic_state(kos)["embedded"] == 2 and semantic_state(kos)["missing"] == 1
    related = kos.search(mode="related", path=a)
    assert {item["path"]: item["reasons"] for item in related["results"]} == {b: ["semantic"]}

    ollama.start()
    sent = len(ollama.documents())
    resumed = kos.run("index.embed", {})
    assert resumed["status"] == "completed" and (resumed["outcome"]["embedded"], resumed["outcome"]["unchanged"]) == (1, 2)
    again = ollama.documents()[sent:]
    assert len(again) == 1 and "gamma" in again[0]

    for name in ("D", "E", "F"):
        vault.write(IDEAS + f"{name}.md", note(f"{base} {name.lower()}\n"))
    ollama.allowance = 1
    partial = kos.run("index.embed", {})
    assert partial["status"] == "failed" and "HTTP 500" in partial["notice"]
    assert (partial["outcome"]["embedded"], partial["outcome"]["failed"], partial["outcome"]["pending"]) == (1, 1, 1)
    ollama.allowance = None
    rest = kos.run("index.embed", {})
    assert (rest["status"], rest["outcome"]["embedded"], rest["outcome"]["unchanged"]) == ("completed", 2, 4)
    assert semantic_state(kos)["embedded"] == 6


def test_SRC_3_unconfigured_or_changed_model_is_unavailable_until_embedded(make_kos, ollama, vault):
    vault.write(IDEAS + "Garden.md", note("garden soil tomato\n"))
    unavailable = {"status": "unavailable", "results": []}

    off = make_kos(settings={"C3": {"semantic": {"ollama_url": "", "model": ""}}})
    assert off.search(mode="semantic", query="garden") == unavailable
    refused = off.run("index.embed", {})
    assert refused["status"] == "refused" and "not configured" in refused["notice"]
    assert off.get("/api/health")[1]["dependencies"]["search_semantic"] == "unavailable"
    off.close()

    first = semantic_kos(make_kos, ollama)
    assert first.search(mode="semantic", query="garden") == unavailable
    assert first.run("index.embed", {})["outcome"]["embedded"] == 1
    assert first.get("/api/health")[1]["dependencies"]["search_semantic"] == "ready"
    assert paths(first.search(mode="semantic", query="garden")) == [IDEAS + "Garden.md"]
    first.close()

    changed = semantic_kos(make_kos, ollama, model="fake-embed-2")
    assert changed.get("/api/health")[1]["dependencies"]["search_semantic"] == "unavailable"
    assert changed.search(mode="semantic", query="garden") == unavailable
    assert paths(changed.search(mode="keyword", query="garden")) == [IDEAS + "Garden.md"]  # refreshes the index
    state = semantic_state(changed)
    assert (state["ready"], state["indexed_model"], state["embedded"], state["missing"]) == (False, "fake-embed", 0, 1)
    rebuilt = changed.run("index.embed", {})
    assert rebuilt["outcome"]["embedded"] == 1 and ollama.calls[-1]["model"] == "fake-embed-2"
    assert paths(changed.search(mode="semantic", query="garden")) == [IDEAS + "Garden.md"]
    changed.close()

    restarted = semantic_kos(make_kos, ollama, model="fake-embed-2")
    sent = len(ollama.calls)
    assert restarted.get("/api/health")[1]["dependencies"]["search_semantic"] == "ready"
    assert restarted.run("index.embed", {})["work_id"] is None and len(ollama.calls) == sent


def test_semantic_endpoint_must_be_loopback(make_kos):
    with pytest.raises(ConfigError, match="loopback"):
        make_kos(settings={"C3": {"semantic": {"ollama_url": "http://192.0.2.10:11434"}}})


def test_embedding_job_actor_and_max_notes_bound_each_work(make_kos, ollama, vault):
    kos = semantic_kos(make_kos, ollama, max_notes=2)
    for name in ("One", "Two", "Three"):
        vault.write(IDEAS + f"{name}.md", note(f"{name.lower()} marker\n"))
    first = kos.run("index.embed", {}, actor="schedule/embed")
    assert first["status"] == "completed" and (first["outcome"]["embedded"], first["outcome"]["pending"]) == (2, 1)
    work = kos.get(f"/api/works/{first['work_id']}")[1]
    assert (work["actor"], work["workspec"]["origin"]) == ("schedule/embed", "schedule")
    assert len(work["workspec"]["inputs"]) == 2
    second = kos.run("index.embed", {}, actor="schedule/embed")
    assert (second["outcome"]["embedded"], second["outcome"]["pending"]) == (1, 0)
    assert kos.run("index.embed", {}, actor="schedule/embed")["work_id"] is None


def test_cli_index_embed_runs_until_nothing_is_pending_and_fails_loudly(make_kos, ollama, vault):
    kos = semantic_kos(make_kos, ollama, max_notes=2)
    for number in range(5):
        vault.write(IDEAS + f"Note{number}.md", note(f"cli marker number{number}\n"))
    code, out, err = kos.cli("index", "embed")
    assert code == 0, err
    report = json.loads(out)
    assert (report["embedded"], report["pending"], report["status"], report["model"]) == (5, 0, "completed", "fake-embed")
    code, out, _ = kos.cli("index", "embed")
    assert code == 0 and json.loads(out)["embedded"] == 0 and json.loads(out)["unchanged"] == 5
    code, out, _ = kos.cli("index", "verify")
    assert code == 0 and json.loads(out)["semantic"]["embedded"] == 5

    vault.write(IDEAS + "Later.md", note("cli marker later\n"))
    ollama.stop()
    code, out, _ = kos.cli("index", "embed")
    report = json.loads(out)
    assert code == 1 and report["status"] == "failed" and "unreachable" in report["notice"]

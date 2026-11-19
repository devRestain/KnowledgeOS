"""C3 Search acceptance (docs/CAPABILITIES.md SRC-1..SRC-4) through the ExOps API and CLI."""

from __future__ import annotations

from conftest import conforming_note

IDEAS = "40_Knowledge/Ideas/"
NOTES = "40_Knowledge/Notes/"


def paths(result):
    return [item["path"] for item in result["results"]]


def test_SRC_1_korean_partial_word_match_with_line_locator(kos, vault):
    text = conforming_note("idea", "seed", body="# 도구\n\n오늘의 사고실험: 도구가 생각을 바꾼다.\n")
    vault.write(IDEAS + "Tools.md", text)
    vault.write(IDEAS + "Other.md", "unrelated words\n")
    vault.write(NOTES + "Thinking.md", "Thinking tools matter.\n")

    result = kos.search(mode="keyword", query="사고")
    assert result["status"] == "ok"
    [hit] = result["results"]
    assert hit["path"] == IDEAS + "Tools.md"
    assert hit["line"] == text.splitlines().index("오늘의 사고실험: 도구가 생각을 바꾼다.") + 1
    assert "사고실험" in hit["excerpt"]
    assert hit["type"] == "idea" and hit["status"] == "seed" and hit["title"] == "Tools"
    assert paths(kos.search(mode="keyword", query="think")) == [NOTES + "Thinking.md"]


def test_SRC_2_deny_note_is_never_returned(kos, vault):
    secret = NOTES + "Secret.md"
    vault.write(secret, "---\nai_policy: deny\ntags: [shared]\n---\nsecret 사고 plan\n")
    vault.write(NOTES + "Open.md", "---\ntags: [shared]\n---\nopen 사고 note, see [[Secret]]\n")
    vault.write(NOTES + "Back.md", "---\ntags: [shared]\n---\nlinks to [[Open]]\n")

    queries = [
        {"mode": "keyword", "query": "사고"},
        {"mode": "keyword", "query": "secret plan"},
        {"mode": "semantic", "query": "사고"},
        {"mode": "related", "path": NOTES + "Open.md"},
        {"mode": "related", "path": secret},
    ]
    for body in queries:
        assert secret not in paths(kos.search(**body)), body
    assert paths(kos.search(mode="keyword", query="사고")) == [NOTES + "Open.md"]
    code, out, _ = kos.cli("vault-check")
    assert code == 0 and "Secret" not in out


def test_SRC_3_semantic_unavailable_never_falls_back(kos, vault):
    vault.write(IDEAS + "Experiment.md", "사고실험\n")
    status, result = kos.post("/api/search", {"mode": "semantic", "query": "사고실험"})
    assert status == 200
    assert result == {"status": "unavailable", "results": []}
    assert kos.get("/api/health")[1]["dependencies"]["search_semantic"] == "unavailable"


def test_SRC_4_search_reflects_saved_edit(kos, vault):
    path = IDEAS + "Edit.md"
    vault.write(path, "first: alpha idea\n")
    assert paths(kos.search(mode="keyword", query="alpha")) == [path]

    vault.write(path, "second line\nnow about beta things\n")
    result = kos.search(mode="keyword", query="beta")
    assert [(item["path"], item["line"]) for item in result["results"]] == [(path, 2)]
    assert kos.search(mode="keyword", query="alpha")["results"] == []


def test_related_labels_links_backlinks_and_tags(kos, vault):
    vault.write(IDEAS + "Focus.md", "---\ntags: [tools]\n---\nsee [[Linked]]\n")
    vault.write(NOTES + "Linked.md", "target\n")
    vault.write(NOTES + "Citing.md", "intro\nbuilds on [[Focus]]\n")
    vault.write(NOTES + "Tagged.md", "---\ntags: ['#tools']\n---\nsame topic\n")
    vault.write(NOTES + "Unrelated.md", "nothing here\n")

    result = kos.search(mode="related", path=IDEAS + "Focus.md")
    reasons = {item["path"]: item["reasons"] for item in result["results"]}
    assert reasons == {
        NOTES + "Linked.md": ["link"],
        NOTES + "Citing.md": ["backlink"],
        NOTES + "Tagged.md": ["tag:tools"],
    }
    citing = next(item for item in result["results"] if item["path"] == NOTES + "Citing.md")
    assert citing["line"] == 2


def test_people_corpus_includes_reports_but_not_proposals_or_system(kos, vault):
    vault.write("01_AI_Review/Reports/Report.md", "---\ntype: report\n---\nmarker report\n")
    vault.write("01_AI_Review/Proposals/Proposal.md", "---\ntype: proposal\n---\nmarker proposal\n")
    vault.write("99_System/Templates/T.md", "marker template\n")
    vault.write(IDEAS + "Note.md", "marker note\n")
    assert sorted(paths(kos.search(mode="keyword", query="marker"))) == [
        "01_AI_Review/Reports/Report.md",
        IDEAS + "Note.md",
    ]


def test_cli_index_rebuild_and_verify(kos, vault):
    vault.write(IDEAS + "A.md", "alpha\n")
    vault.write(IDEAS + "Hidden.md", "---\nai_policy: deny\n---\nhidden\n")
    code, out, err = kos.cli("index", "rebuild")
    assert code == 0, err
    assert '"documents": 1' in out
    code, out, err = kos.cli("index", "verify")
    assert code == 0, out + err
    assert '"ok": true' in out

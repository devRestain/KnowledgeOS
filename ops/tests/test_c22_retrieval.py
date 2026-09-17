from __future__ import annotations

import io
import json
import shutil
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from vaultops.answer import answer
from vaultops.blueprint import validate_blueprint
from vaultops.cli import main
from vaultops.note_engine import render_frontmatter
from vaultops.projection import EXIT_CONFLICT, EXIT_OK, generate_projection
from vaultops.retrieval import (
    evaluate_frozen_baseline,
    retrieval_candidate_schema,
    retrieve,
    search,
)
from vaultops.vector import vector_retrieve, vector_search

CONTROL_ROOT = Path(__file__).resolve().parents[2]
FIXTURE_ROOT = CONTROL_ROOT / "ops/tests/fixtures/c09_portable_vault/guestbook-horror/input"


def _fresh_control_copy(tmp_path: Path) -> Path:
    root = tmp_path / "control"
    shutil.copytree(
        CONTROL_ROOT,
        root,
        ignore=shutil.ignore_patterns(
            ".git",
            ".pytest_cache",
            ".ruff_cache",
            "KnowledgeHub",
            "runtime",
        ),
    )
    shutil.copytree(FIXTURE_ROOT, root / "KnowledgeHub")
    return root


def _build(root: Path, generation_id: str = "c22-fixture") -> None:
    report, code = generate_projection(root, generation_id=generation_id)
    assert code == EXIT_OK, report


def _runtime_snapshot(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in (root / "runtime").rglob("*")
        if path.is_file()
    }


def _write_review_note(
    root: Path,
    *,
    folder: str,
    filename: str,
    identifier: str,
    sensitivity: str = "personal",
    ai_policy: str = "ask",
    projects: list[str] | None = None,
    related: list[str] | None = None,
) -> str:
    relative = f"01_AI_Review/{folder}/{filename}.md"
    properties = {
        "schema_version": 1,
        "id": f"proposal-{identifier}",
        "type": "proposal",
        "title": filename,
        "status": "pending",
        "created": "2026-09-17T10:00:00+09:00",
        "modified": "2026-09-17T10:00:00+09:00",
        "aliases": [],
        "tags": ["ai/review"],
        "sensitivity": sensitivity,
        "ai_policy": ai_policy,
        "ai_status": "proposed",
        "proposal_id": identifier,
        "source_hashes": [
            "00_Inbox/Captures/2026/09/20260909-090000-mac-deadbeef.md|sha256:" + "a" * 64
        ],
    }
    if projects is not None:
        properties["projects"] = projects
    if related is not None:
        properties["related"] = related
    body = f"# {filename}\n\nC25 review signal for the privacy gate.\n"
    path = root / "KnowledgeHub" / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_frontmatter(properties, body), encoding="utf-8")
    return relative


def test_c22_search_is_lexical_deterministic_and_schema_valid(tmp_path: Path) -> None:
    root = _fresh_control_copy(tmp_path)
    _build(root)
    before = _runtime_snapshot(root)

    first, first_code = search(root, "확정되지 않은 정보")
    second, second_code = search(root, "확정되지 않은 정보")

    assert first_code == EXIT_OK, first
    assert second_code == EXIT_OK, second
    assert json.dumps(first, ensure_ascii=False, sort_keys=True) == json.dumps(
        second, ensure_ascii=False, sort_keys=True
    )
    assert first["candidates"][0]["path"] == "40_Knowledge/Notes/정보의 빈칸은 공포의 상상을 강화한다.md"
    assert first["candidates"][0]["vector_score_and_rank"] is None
    assert first["candidates"][0]["rrf_parameter_and_rank"] is None
    assert first["provider_called"] is False
    assert first["mutation_performed"] is False
    for candidate in first["candidates"]:
        assert not list(Draft202012Validator(retrieval_candidate_schema()).iter_errors(candidate))
    assert _runtime_snapshot(root) == before


def test_c22_retrieve_expands_one_hop_typed_links_without_excluded_targets(tmp_path: Path) -> None:
    root = _fresh_control_copy(tmp_path)
    _build(root)

    report, code = retrieve(root, "플레이 루프", scope="project:guestbook-horror")

    assert code == EXIT_OK, report
    paths = [candidate["path"] for candidate in report["candidates"]]
    assert paths[:2] == [
        "20_Projects/guestbook-horror/Working/플레이 루프 후보.md",
        "40_Knowledge/Ideas/이전 플레이의 흔적을 다음 방문에 반영한다.md",
    ]
    idea = next(candidate for candidate in report["candidates"] if candidate["path"].startswith("40_Knowledge/Ideas/"))
    assert idea["retrieval_reason"] == "lexical_match;typed_link_expansion"
    assert len(idea["graph_path"]) >= 1
    assert "implements" in idea["graph_path"][0]
    assert all("10_Journal/" not in path and "00_Inbox/" not in path for path in paths)
    assert report["filters"]["scope"] == "project:guestbook-horror"


def test_c22_default_corpus_filters_journal_capture_and_moc(tmp_path: Path) -> None:
    root = _fresh_control_copy(tmp_path)
    _build(root)

    report, code = search(root, "방명록")

    assert code == EXIT_OK, report
    paths = [candidate["path"] for candidate in report["candidates"]]
    assert all(not path.startswith("00_Inbox/") for path in paths)
    assert all(not path.startswith("10_Journal/") for path in paths)
    assert all(not path.endswith("MOC.md") for path in paths)
    excluded_reasons = {item["reason"] for item in report["filters"]["excluded"]}
    assert "journal_scope_required" in excluded_reasons
    assert "type_not_in_scope" in excluded_reasons


def test_c25_review_policy_is_reapplied_across_lexical_vector_rrf_graph_and_answer(tmp_path: Path) -> None:
    root = _fresh_control_copy(tmp_path)
    confidential_path = _write_review_note(
        root,
        folder="Pending",
        filename="C25 Confidential Review",
        identifier="bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
        sensitivity="confidential",
        ai_policy="local_only",
        projects=["[[guestbook-horror]]"],
    )
    denied_path = _write_review_note(
        root,
        folder="Pending",
        filename="C25 Denied Review",
        identifier="cccccccc-cccc-4ccc-8ccc-cccccccccccc",
        ai_policy="deny",
        projects=["[[guestbook-horror]]"],
    )
    wrong_scope_path = _write_review_note(
        root,
        folder="Pending",
        filename="C25 Wrong Scope Review",
        identifier="dddddddd-dddd-4ddd-8ddd-dddddddddddd",
    )
    wrong_path = _write_review_note(
        root,
        folder="Resolved",
        filename="C25 Wrong Path Review",
        identifier="eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee",
        projects=["[[guestbook-horror]]"],
    )
    valid_path = _write_review_note(
        root,
        folder="Pending",
        filename="C25 Valid Review",
        identifier="aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
        projects=["[[guestbook-horror]]"],
        related=["[[C25 Confidential Review]]"],
    )
    _build(root, generation_id="c25-fixture")

    kwargs = {
        "scope": "project:guestbook-horror",
        "include_types": ["proposal"],
        "include_review": True,
        "limit": 50,
        "hops": 1,
    }
    lexical, lexical_code = retrieve(root, "C25 review signal", **kwargs)
    vector_only, vector_only_code = vector_search(root, "C25 review signal", **kwargs)
    fused, fused_code = vector_retrieve(root, "C25 review signal", **kwargs)
    answered, answer_code = answer(root, "C25 review signal", **kwargs)

    for report, code in (
        (lexical, lexical_code),
        (vector_only, vector_only_code),
        (fused, fused_code),
        (answered, answer_code),
    ):
        assert code == EXIT_OK, report
        paths = (
            report["candidate_paths"]
            if "candidate_paths" in report
            else [candidate["path"] for candidate in report["candidates"]]
        )
        assert paths == [valid_path]
        assert confidential_path not in paths
        assert denied_path not in paths
        assert wrong_scope_path not in paths
        assert wrong_path not in paths
        assert report["provider_called"] is False
        assert report["mutation_performed"] is False

    excluded = {item["path"]: item["reason"] for item in lexical["filters"]["excluded"]}
    assert excluded[confidential_path] == "confidential_corpus_excluded"
    assert excluded[denied_path] == "ai_policy_denied_or_invalid"
    assert excluded[wrong_scope_path] == "scope_mismatch"
    assert excluded[wrong_path] == "review_path_not_allowlisted"
    fused_excluded = {item["path"]: item["reason"] for item in fused["filters"]["excluded"]}
    assert fused_excluded[confidential_path] == "confidential_corpus_excluded"
    assert fused["candidates"][0]["graph_path"] == []

    wrong_type, wrong_type_code = retrieve(
        root,
        "C25 review signal",
        include_review=True,
        include_types=["question"],
        limit=50,
        hops=0,
    )
    assert wrong_type_code == EXIT_OK, wrong_type
    wrong_type_reasons = {item["path"]: item["reason"] for item in wrong_type["filters"]["excluded"]}
    assert wrong_type_reasons[valid_path] == "type_not_in_scope"


def test_c25_candidate_revalidation_rejects_pinned_identity_drift(tmp_path: Path) -> None:
    root = _fresh_control_copy(tmp_path)
    _build(root, generation_id="c25-revalidation")

    report, code = search(root, "확정되지 않은 정보")

    assert code == EXIT_OK, report
    candidate = dict(report["candidates"][0])
    candidate["path"] = "40_Knowledge/Notes/not-the-pinned-note.md"

    from vaultops.projection import load_current_projection
    from vaultops.retrieval import RetrievalConflict, _load_contract, revalidate_candidates

    projection = load_current_projection(root)
    _, policy, _ = _load_contract(root)
    with pytest.raises(RetrievalConflict):
        revalidate_candidates(
            projection,
            policy,
            [candidate],
            limit=10,
            hops=0,
        )


def test_c22_stale_projection_fails_closed_without_runtime_mutation(tmp_path: Path) -> None:
    root = _fresh_control_copy(tmp_path)
    _build(root)
    source = root / "KnowledgeHub/20_Projects/guestbook-horror/guestbook-horror.md"
    source.write_bytes(source.read_bytes() + b"\n")
    before = _runtime_snapshot(root)

    report, code = search(root, "방명록")

    assert code == EXIT_CONFLICT
    assert report["status"] == "FAIL"
    assert report["errors"][0]["code"] == "RETRIEVAL_INDEX_STALE"
    assert report["provider_called"] is False
    assert report["mutation_performed"] is False
    assert _runtime_snapshot(root) == before


def test_c22_retrieval_policy_digest_drift_fails_closed(tmp_path: Path) -> None:
    root = _fresh_control_copy(tmp_path)
    _build(root)
    policy = root / "ops/policies/retrieval.yaml"
    policy.write_bytes(policy.read_bytes().replace(b"total_edge_cap: 60", b"total_edge_cap: 61", 1))

    report, code = retrieve(root, "방명록")

    assert code == EXIT_CONFLICT
    assert report["status"] == "FAIL"
    assert report["errors"][0]["code"] == "RETRIEVAL_INDEX_STALE"
    assert report["provider_called"] is False
    assert report["mutation_performed"] is False


def test_c22_retrieval_blueprint_bounds_are_semantically_checked(tmp_path: Path) -> None:
    root = _fresh_control_copy(tmp_path)
    blueprint = root / "blueprint/blueprint.yaml"
    blueprint.write_text(
        blueprint.read_text(encoding="utf-8").replace("total_candidate_cap: 50", "total_candidate_cap: 49", 1),
        encoding="utf-8",
    )

    result = validate_blueprint(root)

    assert result.report["validation"]["json_schema"] == "PASS"
    assert result.report["validation"]["semantic_validation"] == "FAIL"
    assert any(
        error["code"] == "SEMANTIC_RETRIEVAL_GRAPH_BOUNDS"
        and error["locator"] == "/retrieval/graph_expansion"
        for error in result.report["semantic_errors"]
    )


def test_c22_cli_requires_stdin_or_file_and_exposes_evaluation(tmp_path: Path, monkeypatch, capsys) -> None:
    root = _fresh_control_copy(tmp_path)
    _build(root)
    monkeypatch.setattr("sys.stdin", io.StringIO("synthetic source\n"))

    assert main(["search", "--query-stdin", "--root", str(root)]) == EXIT_OK
    search_report = json.loads(capsys.readouterr().out)
    assert search_report["operation"] == "search"
    assert search_report["candidates"][0]["path"] == "40_Knowledge/Sources/Guestbook Horror Design Note.md"

    assert (
        main(
            [
                "retrieve",
                "--evaluation-file",
                "ops/tests/fixtures/c22_retrieval/evaluation.yaml",
                "--root",
                str(root),
            ]
        )
        == EXIT_OK
    )
    evaluation_report = json.loads(capsys.readouterr().out)
    assert evaluation_report["operation"] == "retrieval evaluate"
    assert evaluation_report["metrics"]["all_cases_passed"] is True

    with pytest.raises(SystemExit):
        main(["search", "--query", "synthetic source", "--root", str(root)])


def test_c22_frozen_baseline_pins_one_generation(tmp_path: Path) -> None:
    root = _fresh_control_copy(tmp_path)
    _build(root, generation_id="c22-pinned")

    report, code = evaluate_frozen_baseline(root, expected_generation_id="c22-pinned")

    assert code == EXIT_OK, report
    assert report["status"] == "PASS"
    assert report["index_generation_id"] == "c22-pinned"
    assert report["source_verified"] is True
    assert report["provider_called"] is False
    assert report["mutation_performed"] is False

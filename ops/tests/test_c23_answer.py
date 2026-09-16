from __future__ import annotations

import hashlib
import io
import json
import shutil
from pathlib import Path

from jsonschema import Draft202012Validator

from vaultops.answer import answer, answer_from_capture, evaluate_frozen_baseline
from vaultops.cli import main
from vaultops.projection import EXIT_CONFLICT, EXIT_OK, answer_schema, generate_projection

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


def _build(root: Path, generation_id: str = "c23-fixture") -> None:
    report, code = generate_projection(root, generation_id=generation_id)
    assert code == EXIT_OK, report


def _file_snapshot(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file()
    }


def test_c23_answer_is_deterministic_schema_valid_and_read_only(tmp_path: Path) -> None:
    root = _fresh_control_copy(tmp_path)
    _build(root)
    before = _file_snapshot(root)

    first, first_code = answer(root, "플레이어가 텍스트 공포 게임에 다시 돌아올 이유", scope="project:guestbook-horror")
    second, second_code = answer(root, "플레이어가 텍스트 공포 게임에 다시 돌아올 이유", scope="project:guestbook-horror")

    assert first_code == EXIT_OK, first
    assert second_code == EXIT_OK, second
    assert json.dumps(first, ensure_ascii=False, sort_keys=True) == json.dumps(second, ensure_ascii=False, sort_keys=True)
    assert first["status"] == "PASS"
    assert first["operation"] == "answer"
    assert first["capability"] == "C23"
    assert first["candidate_paths"][0] == "40_Knowledge/Questions/플레이어가 텍스트 공포 게임에 다시 돌아올 이유는 무엇인가.md"
    assert "바뀌는 기록과 해석" in first["answer_plaintext"]
    assert "![[" not in first["answer_plaintext"]
    assert "command:" not in first["answer_plaintext"]
    assert "obsidian:" not in first["answer_plaintext"]
    assert not list(Draft202012Validator(answer_schema()).iter_errors(first["answer"]))
    assert first["source_verified"] is True
    assert first["provider_called"] is False
    assert first["mutation_performed"] is False
    assert _file_snapshot(root) == before


def test_c23_frozen_baseline_and_cli_question_transport(tmp_path: Path, monkeypatch, capsys) -> None:
    root = _fresh_control_copy(tmp_path)
    _build(root, generation_id="c23-cli")

    baseline, baseline_code = evaluate_frozen_baseline(
        root,
        baseline_path="ops/tests/fixtures/c23_answers/evaluation.yaml",
        expected_generation_id="c23-cli",
    )
    assert baseline_code == EXIT_OK, baseline
    assert baseline["operation"] == "answer evaluate"
    assert baseline["metrics"]["all_cases_passed"] is True
    assert baseline["provider_called"] is False
    assert baseline["mutation_performed"] is False

    monkeypatch.setattr("sys.stdin", io.StringIO("플레이 루프\n"))
    assert main(["ask", "--question-stdin", "--scope", "project:guestbook-horror", "--root", str(root)]) == EXIT_OK
    cli_report = json.loads(capsys.readouterr().out)
    assert cli_report["operation"] == "ask"
    assert cli_report["capability"] == "C23"
    assert cli_report["citations"][0]["path"] == "20_Projects/guestbook-horror/Working/플레이 루프 후보.md"

    assert (
        main(
            [
                "ask",
                "--evaluation-file",
                "ops/tests/fixtures/c23_answers/evaluation.yaml",
                "--generation-id",
                "c23-cli",
                "--root",
                str(root),
            ]
        )
        == EXIT_OK
    )
    evaluation_report = json.loads(capsys.readouterr().out)
    assert evaluation_report["operation"] == "answer evaluate"
    assert evaluation_report["metrics"]["all_cases_passed"] is True


def test_c23_hash_bound_capture_answers_without_vault_or_runtime_mutation(tmp_path: Path) -> None:
    root = _fresh_control_copy(tmp_path)
    _build(root)
    relative = "00_Inbox/Captures/2026/09/20260909-090000-mac-deadbeef.md"
    source = root / "KnowledgeHub" / relative
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    before = _file_snapshot(root)

    report, code = answer_from_capture(root, source_path=relative, expected_sha256=digest)

    assert code == EXIT_OK, report
    assert report["operation"] == "answer"
    assert report["source_reference"] == {"path": relative, "sha256": digest}
    assert report["citations"]
    assert report["provider_called"] is False
    assert report["mutation_performed"] is False
    assert _file_snapshot(root) == before


def test_c23_cli_accepts_hash_bound_capture_reference(tmp_path: Path, capsys) -> None:
    root = _fresh_control_copy(tmp_path)
    _build(root, generation_id="c23-source-cli")
    relative = "00_Inbox/Captures/2026/09/20260909-090000-mac-deadbeef.md"
    source = root / "KnowledgeHub" / relative
    digest = hashlib.sha256(source.read_bytes()).hexdigest()

    assert (
        main(
            [
                "ask",
                "--source",
                relative,
                "--expected-sha256",
                digest,
                "--scope",
                "project:guestbook-horror",
                "--root",
                str(root),
            ]
        )
        == EXIT_OK
    )
    report = json.loads(capsys.readouterr().out)
    assert report["operation"] == "answer"
    assert report["source_reference"] == {"path": relative, "sha256": digest}
    assert report["citations"]


def test_c23_stale_capture_digest_fails_closed(tmp_path: Path) -> None:
    root = _fresh_control_copy(tmp_path)
    _build(root)
    relative = "00_Inbox/Captures/2026/09/20260909-090000-mac-deadbeef.md"
    source = root / "KnowledgeHub" / relative
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    source.write_bytes(source.read_bytes() + b"\n")

    report, code = answer_from_capture(root, source_path=relative, expected_sha256=digest)

    assert code == EXIT_CONFLICT
    assert report["status"] == "FAIL"
    assert report["errors"][0]["code"] == "ANSWER_SOURCE_STALE"
    assert report["provider_called"] is False
    assert report["mutation_performed"] is False

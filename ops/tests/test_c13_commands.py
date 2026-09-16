from __future__ import annotations

import io
import json
import shutil
import sys
from pathlib import Path

from vaultops.cli import main
from vaultops.note_engine import NoteEngine
from vaultops.workflows import create_project_bundle

CONTROL_ROOT = Path(__file__).resolve().parents[2]


class _BytesStdin:
    def __init__(self, value: bytes) -> None:
        self.buffer = io.BytesIO(value)


def _fresh_control_copy(tmp_path: Path) -> Path:
    root = tmp_path / "control"
    shutil.copytree(
        CONTROL_ROOT,
        root,
        ignore=shutil.ignore_patterns(".git", ".pytest_cache", ".ruff_cache", "KnowledgeHub", "runtime"),
    )
    (root / "KnowledgeHub").mkdir()
    return root


def _json_output(capsys) -> dict[str, object]:
    return json.loads(capsys.readouterr().out)


def test_capture_text_is_typed_create_only_and_replay_is_a_conflict(
    tmp_path: Path, capsys, monkeypatch
) -> None:
    root = _fresh_control_copy(tmp_path)
    command = [
        "capture",
        "text",
        "--stdin",
        "--device",
        "mac",
        "--title",
        "CLI Capture",
        "--created-at",
        "2026-09-13T10:20:30+09:00",
        "--root",
        str(root),
    ]
    monkeypatch.setattr(sys, "stdin", _BytesStdin("원문 한 줄\n두 번째 줄\n".encode()))

    assert main(command) == 0
    report = _json_output(capsys)
    assert report["status"] == "PASS"
    assert report["path"] == "00_Inbox/Captures/2026/09/20260913-102030 CLI Capture.md"
    target = root / "KnowledgeHub" / str(report["path"])
    before = target.read_bytes()
    validation = NoteEngine.from_root(root).validate_text(str(report["path"]), before.decode())
    assert validation.passed, validation.as_dict()
    assert "원문 한 줄" in before.decode()

    monkeypatch.setattr(sys, "stdin", _BytesStdin("다른 원문\n".encode()))
    assert main(command) == 30
    replay = _json_output(capsys)
    assert replay["status"] == "CONFLICT"
    assert target.read_bytes() == before


def test_capture_url_uses_validated_files_and_keeps_url_and_comment_in_body(
    tmp_path: Path, capsys
) -> None:
    root = _fresh_control_copy(tmp_path)
    url_file = tmp_path / "url.txt"
    comment_file = tmp_path / "comment.txt"
    url_file.write_bytes(b"https://example.com/articles/portable\n")
    comment_file.write_bytes("나중에 출처를 다시 확인한다.\n".encode())

    assert main(
        [
            "capture",
            "url",
            "--url-file",
            str(url_file),
            "--comment-file",
            str(comment_file),
            "--title",
            "Portable Source",
            "--created-at",
            "2026-09-13T10:21:30+09:00",
            "--root",
            str(root),
        ]
    ) == 0
    report = _json_output(capsys)
    assert report["status"] == "PASS"
    text = (root / "KnowledgeHub" / str(report["path"])).read_text(encoding="utf-8")
    assert "https://example.com/articles/portable" in text
    assert "나중에 출처를 다시 확인한다." in text
    assert NoteEngine.from_root(root).validate_text(str(report["path"]), text).passed


def test_note_create_supports_project_local_notes_and_dry_run_does_not_write(
    tmp_path: Path, capsys
) -> None:
    root = _fresh_control_copy(tmp_path)
    assert main(["bootstrap", "--root", str(root)]) == 0
    capsys.readouterr()
    project = create_project_bundle(root, title="C13 Project", created_at="2026-09-13T10:00:00+09:00")
    assert project["status"] == "PASS", project
    body_file = tmp_path / "body.md"
    body_file.write_text("## 현재 초안\n\n검증할 내용\n", encoding="utf-8")

    command = [
        "note",
        "create",
        "--type",
        "project_note",
        "--title",
        "Working Note",
        "--project",
        "20_Projects/C13 Project/C13 Project.md",
        "--body-file",
        str(body_file),
        "--dry-run",
        "--root",
        str(root),
    ]
    assert main(command) == 0
    preview = _json_output(capsys)
    assert preview["status"] == "PASS"
    assert preview["mode"] == "dry-run"
    target = root / "KnowledgeHub/20_Projects/C13 Project/Working/Working Note.md"
    assert not target.exists()

    assert main([item for item in command if item != "--dry-run"]) == 0
    applied = _json_output(capsys)
    assert applied["status"] == "PASS"
    assert target.is_file()
    assert NoteEngine.from_root(root).validate_text(
        "20_Projects/C13 Project/Working/Working Note.md",
        target.read_text(encoding="utf-8"),
        target_types={"C13 Project": "project"},
    ).passed


def test_create_content_rejects_invalid_utf8_nul_and_oversize_without_writing(
    tmp_path: Path, capsys, monkeypatch
) -> None:
    root = _fresh_control_copy(tmp_path)
    command = [
        "capture",
        "text",
        "--stdin",
        "--device",
        "mac",
        "--title",
        "Rejected",
        "--created-at",
        "2026-09-13T10:22:30+09:00",
        "--root",
        str(root),
    ]
    for invalid in (b"bad\x00input", b"bad\xffinput", b"x" * 65537):
        monkeypatch.setattr(sys, "stdin", _BytesStdin(invalid))
        assert main(command) == 10
        report = _json_output(capsys)
        assert report["status"] == "FAIL"
        assert not list((root / "KnowledgeHub").rglob("Rejected.md"))


def test_fmt_check_is_read_only_and_guarded_write_is_idempotent(tmp_path: Path, capsys) -> None:
    root = _fresh_control_copy(tmp_path)
    body_file = tmp_path / "idea.md"
    body_file.write_text("## 가능성\n\n정규화 테스트\n", encoding="utf-8")
    assert main(
        [
            "note",
            "create",
            "--type",
            "idea",
            "--title",
            "Format Target",
            "--body-file",
            str(body_file),
            "--created-at",
            "2026-09-13T10:23:30+09:00",
            "--root",
            str(root),
        ]
    ) == 0
    created = _json_output(capsys)
    target = root / "KnowledgeHub" / str(created["path"])
    raw = target.read_bytes()
    target.write_bytes(raw.replace(b"\nschema_version: 1\n", b"\r\nschema_version: 1\r\n", 1))
    before_check = target.read_bytes()

    assert main(["fmt", "--check", "--path", str(created["path"]), "--root", str(root)]) == 13
    check = _json_output(capsys)
    assert check["status"] == "NEEDS_FORMAT"
    assert target.read_bytes() == before_check

    assert main(["fmt", "--path", str(created["path"]), "--root", str(root)]) == 0
    applied = _json_output(capsys)
    assert applied["status"] == "PASS"
    formatted = target.read_bytes()
    assert formatted != before_check

    assert main(["fmt", "--check", "--path", str(created["path"]), "--root", str(root)]) == 0
    assert _json_output(capsys)["changed"] == []
    assert main(["fmt", "--path", str(created["path"]), "--root", str(root)]) == 0
    second_apply = _json_output(capsys)
    assert second_apply["changed"] == []
    assert target.read_bytes() == formatted


def test_unsafe_title_and_url_do_not_create_a_target(tmp_path: Path, capsys, monkeypatch) -> None:
    root = _fresh_control_copy(tmp_path)
    monkeypatch.setattr(sys, "stdin", _BytesStdin(b"safe content\n"))
    assert main(
        [
            "capture",
            "text",
            "--stdin",
            "--device",
            "mac",
            "--title",
            "../escape",
            "--root",
            str(root),
        ]
    ) == 10
    assert _json_output(capsys)["status"] == "FAIL"
    assert not list((root / "KnowledgeHub").rglob("*.md"))

    url_file = tmp_path / "bad-url.txt"
    url_file.write_text("javascript:alert(1)\n", encoding="utf-8")
    assert main(
        [
            "capture",
            "url",
            "--url-file",
            str(url_file),
            "--root",
            str(root),
        ]
    ) == 10
    assert _json_output(capsys)["status"] == "FAIL"

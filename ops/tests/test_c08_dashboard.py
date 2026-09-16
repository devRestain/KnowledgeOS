from __future__ import annotations

import shutil
from pathlib import Path

import yaml

from vaultops.base_dashboard import (
    BASE_NAMES,
    DASHBOARD_PATHS,
    dashboard_sources,
    evaluate_records,
    load_frozen_fixture,
    render_base_documents,
)
from vaultops.bootstrap import bootstrap
from vaultops.note_engine import NoteEngine
from vaultops.yaml_safe import load_yaml_file

CONTROL_ROOT = Path(__file__).resolve().parents[2]
FIXTURE_PATH = CONTROL_ROOT / "ops/tests/fixtures/c08_dashboard/fixture.yaml"


def _fresh_control_copy(tmp_path: Path) -> Path:
    root = tmp_path / "control"
    shutil.copytree(
        CONTROL_ROOT,
        root,
        ignore=shutil.ignore_patterns(".git", ".pytest_cache", ".ruff_cache", "KnowledgeHub", "runtime"),
    )
    (root / "KnowledgeHub").mkdir()
    return root


def test_all_canonical_base_files_match_the_blueprint_compiler() -> None:
    blueprint = load_yaml_file(CONTROL_ROOT / "blueprint/blueprint.yaml")
    expected = render_base_documents(blueprint)
    actual_paths = tuple(sorted(path.relative_to(CONTROL_ROOT / "KnowledgeHub").as_posix() for path in (CONTROL_ROOT / "KnowledgeHub/99_System/Bases").glob("*.base")))
    assert actual_paths == tuple(sorted(expected))
    assert tuple(path.rsplit("/", 1)[-1] for path in actual_paths) == tuple(sorted(BASE_NAMES))
    for relative, expected_text in expected.items():
        actual = yaml.safe_load((CONTROL_ROOT / "KnowledgeHub" / relative).read_text(encoding="utf-8"))
        assert actual == yaml.safe_load(expected_text), relative


def test_frozen_evaluator_enforces_canonical_limits_order_and_mtime() -> None:
    blueprint = load_yaml_file(CONTROL_ROOT / "blueprint/blueprint.yaml")
    today, records = load_frozen_fixture(FIXTURE_PATH)

    assert [row["path"] for row in evaluate_records(blueprint, "Journal.base", "Today Focus", records, today=today)] == [
        "10_Journal/Daily/2026/2026-09-09.md"
    ]
    assert [row["path"] for row in evaluate_records(blueprint, "Projects.base", "Now", records, today=today)] == [
        "20_Projects/Alpha/Alpha.md",
        "20_Projects/Beta/Beta.md",
        "20_Projects/Gamma/Gamma.md",
    ]
    assert [row["path"] for row in evaluate_records(blueprint, "Decisions.base", "Open", records, today=today)] == [
        "40_Knowledge/Questions/Q3.md",
        "40_Knowledge/Questions/Q1.md",
        "40_Knowledge/Questions/Q6.md",
        "40_Knowledge/Questions/Q7.md",
        "40_Knowledge/Questions/Q2.md",
    ]
    assert [row["path"] for row in evaluate_records(blueprint, "Knowledge.base", "Radar", records, today=today)] == [
        "40_Knowledge/Notes/Knowledge-03.md",
        "40_Knowledge/Notes/Knowledge-02.md",
        "40_Knowledge/Notes/Knowledge-01.md",
        "40_Knowledge/Ideas/Idea-11.md",
        "40_Knowledge/Ideas/Idea-10.md",
        "40_Knowledge/Ideas/Idea-09.md",
    ]
    assert len(evaluate_records(blueprint, "Inbox.base", "Unprocessed", records, today=today)) == 10
    assert len(evaluate_records(blueprint, "Review.base", "PendingOrConflict", records, today=today)) == 10
    assert [row["path"] for row in evaluate_records(blueprint, "Review.base", "Mobile Results", records, today=today)] == [
        "01_AI_Review/Pending/Review-11.md",
        "01_AI_Review/Pending/Review-10.md",
        "01_AI_Review/Pending/Review-09.md",
        "01_AI_Review/Pending/Review-08.md",
        "01_AI_Review/Pending/Review-07.md",
    ]

    knowledge = next(note for note in records if note.path.endswith("Knowledge-03.md"))
    altered = type(knowledge)(knowledge.path, {**knowledge.properties, "modified": "1900-01-01T00:00:00+09:00"}, knowledge.mtime)
    altered_records = tuple(altered if note.path == knowledge.path else note for note in records)
    first_knowledge = next(
        row["path"]
        for row in evaluate_records(blueprint, "Knowledge.base", "Radar", altered_records, today=today)
    )
    assert first_knowledge.endswith("Knowledge-03.md")


def test_dashboard_sources_are_exactly_deployed_and_core_fallbacks_are_visible() -> None:
    sources = dashboard_sources()
    assert tuple(sorted(sources)) == tuple(sorted(DASHBOARD_PATHS))
    for relative, expected in sources.items():
        assert (CONTROL_ROOT / "KnowledgeHub" / relative).read_text(encoding="utf-8") == expected

    home = sources["Home.md"]
    mobile = sources["Mobile.md"]
    assert "Projects.base#Now" in home
    assert "Projects.base#Now|프로젝트 전체 보기" in home
    assert "Knowledge.base#Ideas|Ideas" in home
    assert "shortcuts://run-shortcut?name=KO%20%C2%B7%20Defer%20to%20Mac" in mobile
    assert "Projects.base#Mobile|프로젝트 전체 보기" in mobile
    assert "snapshot" in mobile
    assert "modified" not in (CONTROL_ROOT / "KnowledgeHub/99_System/Bases/Knowledge.base").read_text(encoding="utf-8")

    engine = NoteEngine.from_root(CONTROL_ROOT)
    for relative in ("Home.md", "Mobile.md", "99_System/Dashboards/Tasks.md", "99_System/Dashboards/Weekly_Review.md"):
        result = engine.validate_text(relative, sources[relative])
        assert result.passed, {"path": relative, "errors": result.as_dict()}


def test_bootstrap_includes_c08_surface_without_overwriting_or_creating_sentinel(tmp_path: Path) -> None:
    root = _fresh_control_copy(tmp_path)
    preview = bootstrap(root, dry_run=True)
    assert preview["status"] == "PASS"
    assert "Home.md" in preview["would_create"]
    assert "99_System/Bases/Inbox.base" in preview["would_create"]
    assert "99_System/Dashboards/Weekly_Review.md" in preview["would_create"]
    assert "99_System/CSS/dashboard.css" in preview["would_create"]

    applied = bootstrap(root)
    assert applied["status"] == "PASS", applied
    assert (root / "KnowledgeHub/Home.md").is_file()
    assert (root / "KnowledgeHub/99_System/Bases/Inbox.base").is_file()
    assert (root / "KnowledgeHub/99_System/Dashboards/Tasks.md").is_file()
    assert (root / "KnowledgeHub/99_System/CSS/dashboard.css").is_file()
    assert not (root / "KnowledgeHub/.knowledgeos-root.json").exists()
    assert not (root / "KnowledgeHub/99_System/Schemas/Property_Dictionary.md").exists()
    assert bootstrap(root)["created"] == []

from __future__ import annotations

from pathlib import Path

import yaml
from support.control_factory import make_control_root

from vaultops.base_dashboard import (
    BASE_NAMES,
    SYSTEM_DASHBOARD_PATHS,
    evaluate_records,
    load_frozen_fixture,
    render_base_documents,
    system_dashboard_sources,
)
from vaultops.bootstrap import bootstrap
from vaultops.note_engine import NoteEngine
from vaultops.yaml_safe import load_yaml_file

CONTROL_ROOT = Path(__file__).resolve().parents[2]
FIXTURE_PATH = CONTROL_ROOT / "ops/tests/fixtures/c08_dashboard/fixture.yaml"


def _fresh_control_copy(tmp_path: Path) -> Path:
    return make_control_root(tmp_path, ("blueprint",))


def test_base_compiler_emits_the_exact_blueprint_owned_source_set() -> None:
    blueprint = load_yaml_file(CONTROL_ROOT / "blueprint/blueprint.yaml")
    expected = render_base_documents(blueprint)
    emitted_paths = tuple(sorted(expected))
    assert tuple(path.rsplit("/", 1)[-1] for path in emitted_paths) == tuple(sorted(BASE_NAMES))
    assert all(yaml.safe_load(source) for source in expected.values())


def test_frozen_evaluator_enforces_canonical_limits_order_and_mtime() -> None:
    blueprint = load_yaml_file(CONTROL_ROOT / "blueprint/blueprint.yaml")
    today, records = load_frozen_fixture(FIXTURE_PATH)

    assert [row["path"] for row in evaluate_records(blueprint, "Journal.base", "Today Focus", records, today=today)] == [
        "10_Journal/Daily/2026/2026-09-09.md"
    ]
    assert [row["path"] for row in evaluate_records(blueprint, "Journal.base", "Open Reviews", records, today=today)] == [
        "10_Journal/Weekly/2026-W36.md",
        "10_Journal/Monthly/2026-09.md",
    ]
    assert [row["path"] for row in evaluate_records(blueprint, "Journal.base", "Due Areas", records, today=today)] == [
        "30_Areas/Health.md",
        "30_Areas/Finance.md",
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
    assert [row["path"] for row in evaluate_records(blueprint, "Decisions.base", "Research Questions", records, today=today)] == [
        "40_Knowledge/Questions/Q4.md",
    ]
    assert [row["path"] for row in evaluate_records(blueprint, "Knowledge.base", "Radar", records, today=today)] == [
        "40_Knowledge/Notes/Knowledge-03.md",
        "40_Knowledge/Notes/Knowledge-02.md",
        "40_Knowledge/Notes/Knowledge-01.md",
        "40_Knowledge/Ideas/Idea-11.md",
        "40_Knowledge/Ideas/Idea-10.md",
        "40_Knowledge/Ideas/Idea-09.md",
    ]
    assert [row["path"] for row in evaluate_records(blueprint, "Knowledge.base", "Active Maps", records, today=today)] == [
        "50_Maps/Knowledge Map.md",
    ]
    assert [row["path"] for row in evaluate_records(blueprint, "Compass.base", "Signals", records, today=today)] == [
        "40_Knowledge/Sources/Source-02.md",
        "40_Knowledge/Notes/Knowledge-03.md",
        "40_Knowledge/Notes/Knowledge-02.md",
        "40_Knowledge/Ideas/Idea-11.md",
        "40_Knowledge/Ideas/Idea-10.md",
        "40_Knowledge/Ideas/Idea-09.md",
    ]
    assert [row["path"] for row in evaluate_records(blueprint, "Compass.base", "Tensions", records, today=today)] == [
        "40_Knowledge/Sources/Source-02.md",
        "40_Knowledge/Notes/Knowledge-02.md",
    ]
    assert [row["path"] for row in evaluate_records(blueprint, "Compass.base", "Research Gaps", records, today=today)] == [
        "40_Knowledge/Questions/Q4.md",
    ]
    assert [row["path"] for row in evaluate_records(blueprint, "Compass.base", "Unconnected Ideas", records, today=today)] == [
        "40_Knowledge/Ideas/Idea-11.md",
        "40_Knowledge/Ideas/Idea-10.md",
        "40_Knowledge/Ideas/Idea-09.md",
        "40_Knowledge/Ideas/Idea-08.md",
        "40_Knowledge/Ideas/Idea-07.md",
        "40_Knowledge/Ideas/Idea-06.md",
        "40_Knowledge/Ideas/Idea-05.md",
        "40_Knowledge/Ideas/Idea-04.md",
        "40_Knowledge/Ideas/Idea-03.md",
        "40_Knowledge/Ideas/Idea-01.md",
    ]
    assert [row["path"] for row in evaluate_records(blueprint, "Compass.base", "Low Confidence", records, today=today)] == [
        "40_Knowledge/Notes/Knowledge-03.md",
    ]
    assert [row["path"] for row in evaluate_records(blueprint, "Compass.base", "Project Bridges", records, today=today)] == [
        "20_Projects/Alpha/Alpha.md",
        "20_Projects/Gamma/Gamma.md",
    ]
    assert len(evaluate_records(blueprint, "Inbox.base", "Unprocessed", records, today=today)) == 10
    assert [row["path"] for row in evaluate_records(blueprint, "Inbox.base", "Unprocessed", records, today=today)][:3] == [
        "00_Inbox/Captures/2026/09/Capture-01.md",
        "00_Inbox/Captures/2026/09/Capture-02.md",
        "00_Inbox/Captures/2026/09/Capture-03.md",
    ]
    assert [row["path"] for row in evaluate_records(blueprint, "Sources.base", "Reading queue", records, today=today)] == [
        "40_Knowledge/Sources/Source-02.md",
        "40_Knowledge/Sources/Source-01.md",
    ]
    assert len(evaluate_records(blueprint, "Review.base", "PendingOrConflict", records, today=today)) == 10
    assert [row["path"] for row in evaluate_records(blueprint, "Review.base", "PendingOrConflict", records, today=today)][:5] == [
        "01_AI_Review/Pending/Review-01.md",
        "01_AI_Review/Pending/Review-02.md",
        "01_AI_Review/Pending/Review-03.md",
        "01_AI_Review/Pending/Review-04.md",
        "01_AI_Review/Pending/Review-05.md",
    ]
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


def test_system_dashboard_documents_are_owned_beneath_99_system() -> None:
    sources = system_dashboard_sources()
    assert tuple(sorted(sources)) == tuple(sorted(SYSTEM_DASHBOARD_PATHS))
    assert all(path.startswith("99_System/") for path in sources)

    today_focus = sources["99_System/Dashboards/Today_Focus.md"]
    css = sources["99_System/CSS/dashboard.css"]
    assert "Journal.base#Today Focus" in today_focus
    assert "Today Focus 전체 보기" in today_focus
    assert ".knowledgeos-home" in css
    assert "grid-template-columns: repeat(12" in css
    assert 'data-callout="ko-home-tasks"]' in css
    assert 'data-callout="ko-home-inbox"]' in css
    assert 'data-callout="ko-home-ai-review"]' in css
    assert 'data-callout="ko-home-projects"]' in css
    assert 'data-callout="ko-home-decisions"]' in css
    assert 'data-callout="ko-home-review"]' in css
    assert 'data-callout="ko-home-compass"]' in css
    assert "grid-column: span 12;" in css
    assert "grid-column: span 6;" in css
    assert "@media (max-width: 899px)" in css

    engine = NoteEngine.from_root(CONTROL_ROOT)
    for relative, source in sources.items():
        if relative.endswith(".md"):
            result = engine.validate_text(relative, source)
            assert result.passed, {"path": relative, "errors": result.as_dict()}


def test_bootstrap_includes_c08_surface_without_overwriting_or_creating_sentinel(tmp_path: Path) -> None:
    root = _fresh_control_copy(tmp_path)
    preview = bootstrap(root, dry_run=True)
    assert preview["status"] == "PASS"
    assert "Home.md" in preview["would_create"]
    assert "99_System/Bases/Inbox.base" in preview["would_create"]
    assert "99_System/Dashboards/Weekly_Review.md" in preview["would_create"]
    assert "99_System/Dashboards/Today_Focus.md" in preview["would_create"]
    assert "99_System/CSS/dashboard.css" in preview["would_create"]

    applied = bootstrap(root)
    assert applied["status"] == "PASS", applied
    assert (root / "KnowledgeHub/Home.md").is_file()
    assert (root / "KnowledgeHub/99_System/Bases/Inbox.base").is_file()
    assert (root / "KnowledgeHub/99_System/Dashboards/Tasks.md").is_file()
    assert (root / "KnowledgeHub/99_System/Dashboards/Today_Focus.md").is_file()
    assert (root / "KnowledgeHub/99_System/CSS/dashboard.css").is_file()
    assert not (root / "KnowledgeHub/.knowledgeos-root.json").exists()
    assert not (root / "KnowledgeHub/99_System/Schemas/Property_Dictionary.md").exists()
    assert bootstrap(root)["created"] == []

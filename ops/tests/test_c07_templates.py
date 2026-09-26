from __future__ import annotations

import re
import shutil
import uuid
from datetime import date
from pathlib import Path

from vaultops.bootstrap import bootstrap
from vaultops.note_engine import NoteEngine
from vaultops.template_engine import (
    TEMPLATE_SPECS,
    render_note_template,
    render_template_source,
    template_paths,
    template_source,
)
from vaultops.workflows import (
    create_project_bundle,
    project_local_link_resolves,
)
from vaultops.yaml_safe import load_yaml_file

CONTROL_ROOT = Path(__file__).resolve().parents[2]


def _control_copy(tmp_path: Path, *, fresh_vault: bool = False) -> Path:
    root = tmp_path / "control"
    shutil.copytree(
        CONTROL_ROOT,
        root,
        ignore=shutil.ignore_patterns(".git", ".pytest_cache", ".ruff_cache", "KnowledgeHub", "runtime"),
    )
    (root / "KnowledgeHub").mkdir()
    if not fresh_vault:
        shutil.copytree(CONTROL_ROOT / "KnowledgeHub", root / "KnowledgeHub", dirs_exist_ok=True)
    return root


def _context_for(spec_name: str) -> dict[str, object]:
    identifier = str(uuid.uuid4())
    context: dict[str, object] = {
        "title": "Demo Note",
        "id": identifier,
        "created": "2026-09-09T09:00:00+09:00",
        "modified": "2026-09-09T09:01:00+09:00",
    }
    if spec_name == "T01_AI_Proposal.md":
        context.update(
            {
                "title": "Demo Proposal",
                "proposal_id": identifier,
                "source_hashes": ["00_Inbox/Captures/Demo.md|sha256:" + "a" * 64],
            }
        )
    if spec_name == "T00_Capture.md":
        context["title"] = "20260909-090000 Demo Note"
    if spec_name in {"T23_Artifact.md", "T24_Project_Note.md"}:
        context.update({"title": "Demo Artifact" if spec_name.startswith("T23") else "Demo Working", "project_link": "[[Demo Project]]"})
    if spec_name == "T20_Project.md":
        context.update({"title": "Demo Project", "outcome": "Ship the demo", "priority": "medium"})
    if spec_name == "T50_MOC.md":
        context["title"] = "Demo Note MOC"
    if spec_name in {"T10_Daily.md", "T11_Weekly.md", "T12_Monthly.md"}:
        context.update({"title": "2026-09-09", "date": date(2026, 9, 9)})
    return context


def _path_for(spec_name: str) -> str:
    paths = {
        "T00_Capture.md": "00_Inbox/Captures/2026/09/20260909-090000 Demo Note.md",
        "T01_AI_Proposal.md": "01_AI_Review/Pending/Demo Proposal.md",
        "T10_Daily.md": "10_Journal/Daily/2026/2026-09-09.md",
        "T11_Weekly.md": "10_Journal/Weekly/2026/2026-W37.md",
        "T12_Monthly.md": "10_Journal/Monthly/2026/2026-09.md",
        "T20_Project.md": "20_Projects/Demo Project/Demo Project.md",
        "T21_Idea.md": "40_Knowledge/Ideas/Demo Note.md",
        "T22_Question.md": "40_Knowledge/Questions/Demo Note.md",
        "T23_Artifact.md": "20_Projects/Demo Project/Artifacts/Demo Artifact.md",
        "T24_Project_Note.md": "20_Projects/Demo Project/Working/Demo Working.md",
        "T30_Area.md": "30_Areas/Demo Note.md",
        "T40_Knowledge.md": "40_Knowledge/Notes/Demo Note.md",
        "T41_Source.md": "40_Knowledge/Sources/Demo Note.md",
        "T42_Person.md": "40_Knowledge/People/Demo Note.md",
        "T50_MOC.md": "50_Maps/Demo Note MOC.md",
        "T60_Meeting.md": "60_Meetings/2026/Demo Note.md",
    }
    return paths[spec_name]


def test_exact_c07_template_allowlist_is_present_and_no_placeholder_directories_exist() -> None:
    blueprint = load_yaml_file(CONTROL_ROOT / "blueprint/blueprint.yaml")
    expected = tuple(f"99_System/Templates/{name}" for name in blueprint["templates"]["required"])
    assert template_paths() == expected
    assert tuple(spec.filename for spec in TEMPLATE_SPECS) == tuple(blueprint["templates"]["required"])
    assert tuple(sorted(path.name for path in (CONTROL_ROOT / "KnowledgeHub/99_System/Templates").glob("*.md"))) == tuple(
        sorted(blueprint["templates"]["required"])
    )
    for spec in TEMPLATE_SPECS:
        assert (CONTROL_ROOT / "KnowledgeHub" / spec.relative_path).read_bytes() == template_source(spec.filename).encode("utf-8")
    assert not any(
        path.is_dir() and path.name in {"YYYY", "MM", "GGGG", "WWW", "PROJECT_NAME", "JOB_ID"}
        for path in (CONTROL_ROOT / "KnowledgeHub").rglob("*")
    )


def test_future_templates_do_not_emit_blank_live_task_placeholders() -> None:
    task_templates = {"T10_Daily.md", "T11_Weekly.md", "T20_Project.md", "T42_Person.md", "T60_Meeting.md"}
    blank_task = re.compile(r"^\s*- \[ \](?:\s+#task)?\s*$")
    for template_name in task_templates:
        rendered_source = template_source(template_name)
        assert not any(blank_task.match(line) for line in rendered_source.splitlines()), template_name


def test_templates_use_the_native_inline_title_without_emitting_a_duplicate_h1() -> None:
    for spec in TEMPLATE_SPECS:
        rendered = render_note_template(spec.filename, _context_for(spec.filename))
        assert not re.search(r"(?m)^#\s+\S", rendered.body), spec.filename


def test_all_rendered_templates_pass_the_strict_note_schema() -> None:
    engine = NoteEngine.from_root(CONTROL_ROOT)
    target_types = {"Demo Project": "project"}
    for spec in TEMPLATE_SPECS:
        rendered = render_note_template(spec.filename, _context_for(spec.filename))
        result = engine.validate_text(_path_for(spec.filename), rendered.markdown, target_types=target_types)
        assert result.passed, {"template": spec.filename, "errors": result.as_dict()}


def test_optional_empty_context_lists_are_not_emitted() -> None:
    rendered = render_note_template(
        "T40_Knowledge.md",
        {
            "title": "Minimal Knowledge",
            "id": str(uuid.uuid4()),
            "created": "2026-09-09T09:00:00+09:00",
            "modified": "2026-09-09T09:01:00+09:00",
        },
    )
    assert not set(rendered.properties) & {"areas", "projects", "topics", "sources", "related"}


def test_proposal_template_binds_a_job_uuid_to_the_deterministic_note_id() -> None:
    identifier = str(uuid.uuid4())
    rendered = render_note_template(
        "T01_AI_Proposal.md",
        {
            "title": "Demo Proposal",
            "id": identifier,
            "proposal_id": identifier,
            "created": "2026-09-09T09:00:00+09:00",
            "modified": "2026-09-09T09:00:00+09:00",
        },
    )
    assert rendered.properties["id"] == f"proposal-{identifier}"
    assert rendered.properties["proposal_id"] == identifier


def test_source_renderer_is_bounded_and_refuses_unresolved_tokens() -> None:
    rendered = render_template_source(
        "T20_Project.md",
        {"id": str(uuid.uuid4()), "title": "Safe Project", "now": "2026-09-09T09:00:00+09:00", "aliases": []},
    )
    assert "<%" not in rendered
    assert "{{" not in rendered
    assert "Safe Project" in rendered


def test_bounded_source_renderer_resolves_every_registered_template() -> None:
    identifier = str(uuid.uuid4())
    context = {
        "id": identifier,
        "title": "Demo Note",
        "created": "2026-09-09T09:00:00+09:00",
        "now": "2026-09-09T09:00:00+09:00",
        "aliases": [],
        "project_link": "[[Demo Project]]",
        "proposal_id": identifier,
        "source_hashes": ["00_Inbox/Captures/Demo.md|sha256:" + "a" * 64],
        "date": "2026-09-09",
        "date_label": "2026-09-09 Wednesday",
        "time": "09:00",
        "week_id": "weekly-2026-w37",
        "week_label": "2026-W37",
        "week_heading": "2026 [W]37",
        "month_label": "2026-09",
        "month_start": "2026-09-01",
        "month_end": "2026-09-30",
        "monday": "2026-09-07",
        "tuesday": "2026-09-08",
        "wednesday": "2026-09-09",
        "thursday": "2026-09-10",
        "friday": "2026-09-11",
        "saturday": "2026-09-12",
        "sunday": "2026-09-13",
        "next_review": "2026-10-09",
        "summary": "검토할 제안입니다.",
        "source_table": "없음",
        "diff": "변경 없음",
        "warnings": "없음",
    }
    for spec in TEMPLATE_SPECS:
        rendered = render_template_source(spec.filename, context)
        assert "<%" not in rendered
        assert "{{" not in rendered


def test_bootstrap_dry_run_apply_and_second_apply_are_additive(tmp_path: Path) -> None:
    root = _control_copy(tmp_path, fresh_vault=True)
    planned = bootstrap(root, dry_run=True)
    assert planned["status"] == "PASS"
    assert planned["would_create"]
    assert not list((root / "KnowledgeHub/99_System/Templates").glob("*.md"))

    applied = bootstrap(root)
    assert applied["status"] == "PASS"
    assert set(applied["created"]) == set(planned["would_create"])
    second = bootstrap(root)
    assert second["status"] == "PASS"
    assert second["created"] == []
    second_preview = bootstrap(root, dry_run=True)
    assert second_preview["would_create"] == []
    assert all((root / "KnowledgeHub" / relative).exists() for relative in template_paths())


def test_bootstrap_conflict_is_preflighted_without_creating_other_templates(tmp_path: Path) -> None:
    root = _control_copy(tmp_path, fresh_vault=True)
    conflict = root / "KnowledgeHub/99_System/Templates/T00_Capture.md"
    conflict.parent.mkdir(parents=True)
    conflict.write_text("user-owned\n", encoding="utf-8")
    result = bootstrap(root)
    assert result["status"] == "CONFLICT"
    assert result["conflicts"] == ["99_System/Templates/T00_Capture.md"]
    assert not (root / "KnowledgeHub/99_System/Templates/T20_Project.md").exists()
    assert conflict.read_text(encoding="utf-8") == "user-owned\n"


def test_bootstrap_treats_a_directory_at_a_template_target_as_conflict(tmp_path: Path) -> None:
    root = _control_copy(tmp_path, fresh_vault=True)
    conflict = root / "KnowledgeHub/99_System/Templates/T00_Capture.md"
    conflict.parent.mkdir(parents=True)
    conflict.mkdir()
    result = bootstrap(root)
    assert result["status"] == "CONFLICT"
    assert result["conflicts"] == ["99_System/Templates/T00_Capture.md"]
    assert not (root / "KnowledgeHub/99_System/Templates/T20_Project.md").exists()


def test_project_bundle_is_atomic_create_only_and_noop_on_second_attempt(tmp_path: Path) -> None:
    root = _control_copy(tmp_path, fresh_vault=True)
    assert bootstrap(root)["status"] == "PASS"
    preview = create_project_bundle(
        root,
        title="Demo Project",
        dry_run=True,
        identifier=str(uuid.uuid4()),
        created_at="2026-09-09T09:00:00+09:00",
    )
    assert preview["status"] == "PASS"
    assert preview["would_create"] == [
        "20_Projects/Demo Project/Demo Project.md",
        "20_Projects/Demo Project/Working",
        "20_Projects/Demo Project/Artifacts",
    ]
    applied = create_project_bundle(
        root,
        title="Demo Project",
        identifier=str(uuid.uuid4()),
        created_at="2026-09-09T09:00:00+09:00",
    )
    assert applied["status"] == "PASS", applied
    assert (root / "KnowledgeHub/20_Projects/Demo Project/Demo Project.md").is_file()
    assert (root / "KnowledgeHub/20_Projects/Demo Project/Working").is_dir()
    assert (root / "KnowledgeHub/20_Projects/Demo Project/Artifacts").is_dir()
    second = create_project_bundle(root, title="Demo Project", created_at="2026-09-09T09:00:00+09:00")
    assert second["status"] == "CONFLICT"
    assert second["created"] == []


def test_project_bundle_refuses_an_existing_project_root(tmp_path: Path) -> None:
    root = _control_copy(tmp_path, fresh_vault=True)
    project_dir = root / "KnowledgeHub/20_Projects/Existing Project"
    project_dir.mkdir(parents=True)
    result = create_project_bundle(root, title="Existing Project")
    assert result["status"] == "CONFLICT"
    assert result["conflicts"] == ["20_Projects/Existing Project"]
    assert result["created"] == []
    assert list(project_dir.iterdir()) == []


def test_gui_period_fixtures_preserve_iso_boundaries_month_ends_and_no_overwrite_contract() -> None:
    engine = NoteEngine.from_root(CONTROL_ROOT)

    weekly = render_note_template(
        "T11_Weekly.md",
        {"title": "2025-W01", "date": date(2024, 12, 30)},
    )
    assert weekly.properties["id"] == "weekly-2025-w01"
    assert weekly.properties["title"] == "2025-W01"
    assert weekly.properties["period_start"] == "2024-12-30"
    assert weekly.properties["period_end"] == "2025-01-05"
    assert not re.search(r"(?m)^#\s+", weekly.body)
    assert weekly.body.count("- [[") == 7
    assert "vaultops:weekly-summary:begin" in weekly.body
    assert engine.validate_text("10_Journal/Weekly/2025/2025-W01.md", weekly.markdown).passed

    expected_month_ends = {
        "2024-02": "2024-02-29",
        "2025-02": "2025-02-28",
        "2026-04": "2026-04-30",
        "2026-07": "2026-07-31",
    }
    for month_label, expected_end in expected_month_ends.items():
        monthly = render_note_template(
            "T12_Monthly.md",
            {"title": month_label, "date": date.fromisoformat(f"{month_label}-18")},
        )
        assert monthly.properties["id"] == f"monthly-{month_label}"
        assert monthly.properties["period_start"] == f"{month_label}-01"
        assert monthly.properties["period_end"] == expected_end
        assert engine.validate_text(
            f"10_Journal/Monthly/{month_label[:4]}/{month_label}.md", monthly.markdown
        ).passed

    assert engine.validate_text(
        "10_Journal/Weekly/2025/2025-W01.md", weekly.markdown
    ).passed, "an existing GUI-created period note is validated without a writer or overwrite"


def test_gui_period_template_sources_are_bounded_templater_and_remove_unsupported_month_end_token() -> None:
    weekly_source = template_source("T11_Weekly.md")
    monthly_source = template_source("T12_Monthly.md")
    for source in (weekly_source, monthly_source):
        assert "tp.user" not in source
        assert "tp.system" not in source
        assert "tp.file.include" not in source
        assert "vaultctl" not in source
        assert "month_end:" not in source
        assert "moment(" in source
    assert "vaultops:weekly-summary:begin" in weekly_source


def test_project_local_links_resolve_to_one_parent_project() -> None:
    assert project_local_link_resolves(
        "20_Projects/Demo Project/Working/Note.md",
        "[[Demo Project]]",
        "20_Projects/Demo Project/Demo Project.md",
    )
    assert project_local_link_resolves(
        "20_Projects/Demo Project/Artifacts/Artifact.md",
        "[[Demo Project]]",
        "20_Projects/Demo Project/Demo Project.md",
    )
    assert not project_local_link_resolves(
        "20_Projects/Demo Project/Working/Note.md",
        "[[Other Project]]",
        "20_Projects/Demo Project/Demo Project.md",
    )

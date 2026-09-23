from __future__ import annotations

from pathlib import Path

from vaultops.gui_contracts import inspect_gui_contract

CONTROL_ROOT = Path(__file__).resolve().parents[2]


def test_serialized_gui_contract_accepts_the_authorized_period_mapping_and_toolbar_targets() -> None:
    navigator = CONTROL_ROOT / "KnowledgeHub/.obsidian-mac/plugins/notebook-navigator/data.json"
    toolbar = CONTROL_ROOT / "KnowledgeHub/.obsidian-mac/plugins/note-toolbar/data.json"
    before = (navigator.read_bytes(), toolbar.read_bytes())

    report = inspect_gui_contract(CONTROL_ROOT)

    assert report["state"] == "pass"
    assert report["evidence_class"] == "static"
    assert report["daily"]["state"] == "pass"
    assert report["daily"]["owner"] == "Obsidian Core Daily Notes"
    assert report["periodic"]["state"] == "pass"
    assert report["periodic"]["checks"]["weekly_pattern"]["observed"] == "[Weekly]/GGGG/GGGG-[W]WW"
    assert report["periodic"]["checks"]["monthly_pattern"]["observed"] == "[Monthly]/YYYY/YYYY-MM"
    assert report["periodic"]["checks"]["weekly_pattern"]["expected"] == "[Weekly]/GGGG/GGGG-[W]WW"
    assert report["periodic"]["checks"]["monthly_pattern"]["expected"] == "[Monthly]/YYYY/YYYY-MM"
    assert report["toolbar"]["state"] == "pass"
    assert report["toolbar"]["actions"]["daily"] == "configured"
    assert report["toolbar"]["actions"]["weekly"] == "configured"
    assert report["toolbar"]["actions"]["monthly"] == "configured"
    assert "10_Journal/Weekly/2026/2026-W39.md" in report["toolbar"]["file_links"]
    assert "10_Journal/Monthly/2026/2026-09.md" in report["toolbar"]["file_links"]
    assert report["toolbar"]["device_evidence"] == "not_run"
    assert (navigator.read_bytes(), toolbar.read_bytes()) == before

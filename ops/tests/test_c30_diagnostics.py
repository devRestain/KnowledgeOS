from __future__ import annotations

import json
import shutil
from pathlib import Path

from vaultops.diagnostics import doctor_report
from vaultops.local_models import inspect_local_model_config
from vaultops.schema_export import export_schema_artifacts

CONTROL_ROOT = Path(__file__).resolve().parents[2]


def test_c30_generated_local_model_contract_is_disabled_and_pinned_to_safe_baselines() -> None:
    report, errors = inspect_local_model_config(CONTROL_ROOT)

    assert errors == []
    assert report["status"] == "PASS"
    assert report["enabled_by_default"] is False
    assert report["profiles"]["generation"] == {
        "task": "generation",
        "model_tag": "gemma4:12b-it-q4_K_M",
        "enabled": False,
        "state": "disabled",
    }
    assert report["profiles"]["embedding"] == {
        "task": "embedding",
        "model_tag": "embeddinggemma:300m-qat-q8_0",
        "enabled": False,
        "state": "disabled",
    }
    assert report["model_digests"] == {"generation": None, "embedding": None}


def test_c30_local_model_config_is_owned_by_zero_diff_generation() -> None:
    result = export_schema_artifacts(CONTROL_ROOT, check=True)

    assert result.passed, result.report
    artifact = next(
        item for item in result.report["artifacts"] if item["path"] == "ops/config/local-models.yaml"
    )
    assert artifact["owner"] == "C30"
    assert artifact["status"] == "PASS"


def test_c30_doctor_separates_filesystem_evidence_from_live_and_device_proof() -> None:
    report, exit_code = doctor_report(CONTROL_ROOT)

    assert exit_code == 0, report
    assert report["capability"]["session_slice"] == "C30"
    assert report["capability"]["state_dimensions"] == [
        "declared",
        "configured",
        "reachable",
        "authorized",
        "verified",
        "enabled",
        "healthy",
    ]
    local_model = report["capabilities"]["local_model"]
    assert local_model["declared"] == "declared"
    assert local_model["configured"] == "configured"
    assert local_model["reachable"] == "not_run"
    assert local_model["authorized"] == "not_authorized"
    assert local_model["verified"] == "not_verified"
    assert local_model["enabled"] == "disabled"
    assert local_model["healthy"] == "not_ready"
    assert report["plugins"]["filesystem_evidence"] == "pass"
    assert report["plugins"]["device_proof"]["state"] == "not_inferred"
    assert report["capabilities"]["c24_background"]["enabled"] == "disabled"
    assert report["capabilities"]["e01_vector"]["enabled"] == "disabled"
    assert report["projection"]["usable"] is False
    assert report["projection"]["generation_id"] is None
    assert report["warnings"]


def test_c30_config_drift_is_degraded_and_does_not_enable_a_profile(tmp_path: Path) -> None:
    root = tmp_path / "control"
    (root / "blueprint").mkdir(parents=True)
    shutil.copy2(CONTROL_ROOT / "blueprint/blueprint.yaml", root / "blueprint/blueprint.yaml")
    config_path = root / "ops/config/local-models.yaml"
    config_path.parent.mkdir(parents=True)
    shutil.copy2(CONTROL_ROOT / "ops/config/local-models.yaml", config_path)
    document = config_path.read_text(encoding="utf-8").replace("enabled_by_default: false", "enabled_by_default: true", 1)
    config_path.write_text(document, encoding="utf-8")

    report, errors = inspect_local_model_config(root)

    assert report["status"] == "DEGRADED"
    assert report["enabled_by_default"] is True
    assert any(error["code"] == "LOCAL_MODEL_DEFAULT_ENABLED" for error in errors)
    assert any(error["code"] == "LOCAL_MODEL_CONFIG_DRIFT" for error in errors)


def test_c30_doctor_report_remains_json_serializable() -> None:
    report, exit_code = doctor_report(CONTROL_ROOT)

    assert exit_code == 0, report
    json.dumps(report, ensure_ascii=False, sort_keys=True)

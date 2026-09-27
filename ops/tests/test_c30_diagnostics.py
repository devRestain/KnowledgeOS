from __future__ import annotations

import json
from pathlib import Path

from support.control_factory import make_control_root, make_diagnostic_root

from vaultops.diagnostics import doctor_report
from vaultops.local_models import inspect_local_model_config

CONTROL_ROOT = Path(__file__).resolve().parents[2]


def test_c30_generated_local_model_contract_selects_qwen_as_explicit_serial_live_default() -> None:
    report, errors = inspect_local_model_config(CONTROL_ROOT)

    assert errors == []
    assert report["status"] == "PASS"
    assert report["enabled_by_default"] is False
    assert report["profiles"]["generation"] == {
        "task": "generation",
        "model_tag": "gemma4:12b",
        "enabled": False,
        "state": "disabled",
    }
    assert report["profiles"]["embedding"] == {
        "task": "embedding",
        "model_tag": "qwen3-embedding:8b-q4_K_M",
        "enabled": True,
        "state": "live_default",
    }
    assert report["profiles"]["embedding_fallback"] == {
        "task": "embedding",
        "model_tag": "embeddinggemma:300m-qat-q8_0",
        "enabled": False,
        "state": "disabled",
    }
    assert report["model_digests"] == {
        "generation": None,
        "embedding": "64b933495768fbd3b87c20583d379728a07471e0c66733a9df87cd1901b3c44b",
        "embedding_fallback": None,
    }
    assert report["activation_policy"] == {
        "mode": "explicit_serial",
        "one_model_loaded": True,
        "concurrent_requests": False,
        "unattended_activation": False,
        "accepted_min_free_memory_percent": 24,
    }


def test_c30_doctor_separates_serialized_evidence_from_live_and_device_proof(
    tmp_path: Path,
) -> None:
    report, exit_code = doctor_report(make_diagnostic_root(tmp_path))

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
    assert local_model["enabled"] == "enabled"
    assert local_model["healthy"] == "not_ready"
    assert report["plugins"]["serialized_deployment_evidence"] == "not_configured"
    assert report["plugins"]["device_proof"]["state"] == "not_inferred"
    assert report["capabilities"]["c24_background"]["enabled"] == "disabled"
    assert report["capabilities"]["e01_vector"]["enabled"] == "disabled"
    assert report["projection"]["usable"] is False
    assert report["projection"]["generation_id"] is None
    assert report["warnings"]


def test_c30_config_drift_is_degraded_and_does_not_enable_a_profile(tmp_path: Path) -> None:
    root = make_control_root(
        tmp_path,
        ("blueprint/blueprint.yaml", "ops/config/local-models.yaml"),
    )
    config_path = root / "ops/config/local-models.yaml"
    document = config_path.read_text(encoding="utf-8").replace("enabled_by_default: false", "enabled_by_default: true", 1)
    config_path.write_text(document, encoding="utf-8")

    report, errors = inspect_local_model_config(root)

    assert report["status"] == "DEGRADED"
    assert report["enabled_by_default"] is True
    assert any(error["code"] == "LOCAL_MODEL_DEFAULT_ENABLED" for error in errors)
    assert any(error["code"] == "LOCAL_MODEL_CONFIG_DRIFT" for error in errors)


def test_c30_doctor_report_remains_json_serializable(tmp_path: Path) -> None:
    report, exit_code = doctor_report(make_diagnostic_root(tmp_path))

    assert exit_code == 0, report
    json.dumps(report, ensure_ascii=False, sort_keys=True)

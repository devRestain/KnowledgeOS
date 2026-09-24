from __future__ import annotations

import json
from pathlib import Path

import pytest

from vaultops.operations import (
    OperationCrash,
    OperationsError,
    dry_run_operation,
    recover_operation,
    rollback_operation,
    run_operation,
    verify_operation,
)

BEFORE = {"version": 1, "enabled": False, "model_tag": "gemma4:12b"}
TARGET = {"version": 1, "enabled": True, "model_tag": "gemma4:12b"}


def test_c43_dry_run_has_no_runtime_or_external_effect() -> None:
    report = dry_run_operation(
        operation_id="c43-dry-run",
        component="thin_client",
        action="install",
        before_profile=BEFORE,
        target_profile=TARGET,
    )

    assert report["status"] == "DRY_RUN"
    assert report["mutation_performed"] is False
    assert all(value is False for value in report["effects"].values())
    assert report["rollback_available"] is True


def test_c43_simulated_crash_recovers_and_rolls_back_with_create_only_receipts(tmp_path: Path) -> None:
    root = tmp_path / "control"
    root.mkdir()

    with pytest.raises(OperationCrash, match="after_intent"):
        run_operation(
            root,
            operation_id="c43-crash",
            component="broker",
            action="upgrade",
            before_profile=BEFORE,
            target_profile=TARGET,
            crash_stage="after_intent",
        )

    recovered, recovered_code = recover_operation(root, "c43-crash")
    rollback, rollback_code = rollback_operation(root, "c43-crash")
    replay, replay_code = rollback_operation(root, "c43-crash")
    verified = verify_operation(root, "c43-crash")

    assert recovered_code == rollback_code == replay_code == 0
    assert recovered["status"] == "RECOVERED"
    assert recovered["prior_profile_unchanged"] is True
    assert rollback["status"] == "ROLLED_BACK"
    assert replay["status"] == "NO_OP"
    assert replay["replayed"] is True
    assert verified["status"] == "PASS"
    assert verified["receipt_count"] == 2
    assert all(value is False for value in verified["effects"].values())
    assert (root / "runtime/operations/c43-crash/intent.json").stat().st_mode & 0o777 == 0o600
    assert (root / "runtime/operations/c43-crash/receipt.json").stat().st_mode & 0o777 == 0o600
    assert (root / "runtime/operations/c43-crash/rollback.json").stat().st_mode & 0o777 == 0o600
    assert not (root / "KnowledgeHub").exists()


def test_c43_completed_operation_is_replay_safe_and_tamper_evident(tmp_path: Path) -> None:
    root = tmp_path / "control"
    root.mkdir()
    first, first_code = run_operation(
        root,
        operation_id="c43-complete",
        component="model_identity",
        action="disable",
        before_profile=BEFORE,
        target_profile={"version": 1, "enabled": False, "model_tag": "gemma4:12b"},
    )
    second, second_code = run_operation(
        root,
        operation_id="c43-complete-replay",
        component="model_identity",
        action="disable",
        before_profile=BEFORE,
        target_profile={"version": 1, "enabled": False, "model_tag": "gemma4:12b"},
    )

    assert first_code == second_code == 0
    assert first["mutation_performed"] is False
    assert second["effects"]["provider_effect"] is False

    receipt_path = root / "runtime/operations/c43-complete/receipt.json"
    value = json.loads(receipt_path.read_text(encoding="utf-8"))
    value["target_sha256"] = "f" * 64
    receipt_path.write_text(json.dumps(value, sort_keys=True, separators=(",", ":")), encoding="utf-8")
    with pytest.raises(OperationsError, match="receipt digest"):
        verify_operation(root, "c43-complete")

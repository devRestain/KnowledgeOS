from __future__ import annotations

from pathlib import Path

from vaultops.foundation import check_foundation


def test_portable_foundation_check_passes_for_current_workspace() -> None:
    control_root = Path(__file__).resolve().parents[2]
    assert check_foundation(control_root) == []

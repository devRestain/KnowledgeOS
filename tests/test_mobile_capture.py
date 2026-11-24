"""CaptureNote QuickAdd behavioral contract through the mounted Vault script."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HARNESS = ROOT / "tests" / "capture_note_node.mjs"


def test_capture_note_contract_and_c2_std7(kos, vault) -> None:
    result = subprocess.run(
        ["node", str(HARNESS)],
        cwd=ROOT,
        env=os.environ.copy(),
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    output = result.stdout + result.stderr
    assert result.returncode == 0, output
    payload = json.loads(result.stdout)
    cases = payload["cases"]

    assert payload["loader"] == "function"
    assert cases["bodyOnly"]["saved"] is True
    assert cases["urlOnly"]["saved"] is True
    assert cases["empty"]["files"] == []
    assert cases["empty"]["saved"] is False
    assert cases["invalidUrl"]["files"] == []
    assert cases["invalidUrl"]["saved"] is False
    assert cases["failedCreate"]["files"] == []
    assert cases["failedCreate"]["saved"] is False
    assert cases["openFailure"]["saved"] is True
    assert "노트는 저장했지만 열지 못했습니다" in cases["openFailure"]["notices"]
    assert cases["folderCollision"]["saved"] is False
    assert cases["uuidCollision"]["content"] == "keep"

    sample = payload["sample"]
    path = sample["path"]
    content = sample["content"]
    vault.write(path, content)
    before = vault.path(path).read_bytes()

    receipt = kos.run(
        "standardize.preview",
        {"paths": [path], "digests": {path: kos.vault.sha(path)}},
    )
    assert receipt["status"] == "completed", receipt
    outcome = receipt["outcome"]
    assert outcome["notes"] == []
    assert outcome["skipped"] == []
    assert outcome["unchanged"] == 1
    assert vault.path(path).read_bytes() == before

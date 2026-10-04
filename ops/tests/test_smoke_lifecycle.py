from __future__ import annotations

import json
import os
import signal
import stat
import subprocess
import sys
import textwrap
import time
from datetime import UTC, datetime
from pathlib import Path

import pytest
from support.control_factory import fixture_path
from support.control_factory import make_identity_root as _smoke_root

from vaultops import smoke
from vaultops.bridge_contract import canonical_json_bytes
from vaultops.paths import resolve_paths

_RUN_ID = "411602c1-5278-4a8b-8b96-9183fb6ef8c2"


def _start_stale_run(root: Path, monkeypatch: pytest.MonkeyPatch) -> smoke._Run:
    monkeypatch.setattr(smoke, "_now", lambda: datetime(2020, 1, 1, tzinfo=UTC))
    run = smoke._reserve_run(
        root,
        kind="filesystem",
        adapter="filesystem-roundtrip",
        timeout_seconds=5,
        authorized=True,
        run_id=_RUN_ID,
    )
    assert smoke._exercise(run, deadline=time.monotonic() + 5)
    monkeypatch.setattr(smoke, "_now", lambda: datetime.now(UTC))
    return run


def _start_unsealed_run(root: Path, monkeypatch: pytest.MonkeyPatch) -> smoke._Run:
    monkeypatch.setattr(smoke, "_now", lambda: datetime(2020, 1, 1, tzinfo=UTC))
    run = smoke._reserve_run(
        root,
        kind="filesystem",
        adapter="filesystem-roundtrip",
        timeout_seconds=5,
        authorized=True,
        run_id=_RUN_ID,
    )
    created_namespace = smoke._prepare_namespace(run.roots.vault, run.relative_path)
    run.journal.append("namespace_ready", {"created_by_run": created_namespace})
    smoke._write_probe(run.roots.vault, run.relative_path, run.body)
    monkeypatch.setattr(smoke, "_now", lambda: datetime.now(UTC))
    return run


def _assert_confirmation(report: dict[str, object], run: smoke._Run) -> dict[str, object]:
    assert report["status"] == "CONFLICT"
    confirmation = report["confirmation_request"]
    assert isinstance(confirmation, dict)
    assert confirmation["required"] is True
    assert confirmation["action"] == "confirm_exact_smoke_note_disposition"
    assert confirmation["relative_path"] == run.relative_path
    assert confirmation["automatic_deletion"] is False
    return confirmation


def _fast_forward_cleanup_window(monkeypatch: pytest.MonkeyPatch) -> None:
    current = smoke.time.monotonic()

    def advance_monotonic() -> float:
        nonlocal current
        current += 0.5
        return current

    monkeypatch.setattr(smoke.time, "monotonic", advance_monotonic)
    monkeypatch.setattr(smoke.time, "sleep", lambda _seconds: None)


def _separate_smoke_roots(tmp_path: Path):
    legacy_control = _smoke_root(tmp_path)
    control = legacy_control.with_name("control Ω")
    legacy_control.rename(control)
    vault = control.parent / "KnowledgeHub Ω"
    (fixture_path(control, "vault")).rename(vault)
    runtime = control.parent / "runtime Ω"
    (fixture_path(control, "runtime")).rename(runtime)
    runtime.chmod(0o700)
    config = control / "ops/vaultops.toml"
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text(
        "\n".join(
            (
                "schema_version = 3",
                'project_name = "KnowledgeOS"',
                f"control_root = {json.dumps(str(control), ensure_ascii=False)}",
                f"vault_root = {json.dumps(str(vault), ensure_ascii=False)}",
                f"runtime_root = {json.dumps(str(runtime), ensure_ascii=False)}",
                f'core_root = {json.dumps(str(fixture_path(control, "core")), ensure_ascii=False)}',
                f'state_root = {json.dumps(str(fixture_path(control, "state")), ensure_ascii=False)}',
                'timezone = "Asia/Seoul"',
                "",
            )
        ),
        encoding="utf-8",
    )
    return resolve_paths(control)


def test_smoke_plan_is_read_only_and_requires_separate_run_authorization(tmp_path: Path) -> None:
    root = _smoke_root(tmp_path)

    plan = smoke.plan_smoke(root, kind="filesystem", adapter="filesystem-roundtrip")

    assert plan["status"] == "PASS"
    assert plan["authorization_required_for_run"] is True
    assert plan["observations"] == {
        "static": {"state": "pass", "evidence_class": "static"},
        "runtime": {"state": "not_run", "evidence_class": "runtime"},
        "deployment": {"state": "not_run", "evidence_class": "deployment"},
        "device": {"state": "not_run", "evidence_class": "device"},
    }
    assert plan["relative_path"].startswith("99_System/Smoke/KnowledgeOS-Smoke-filesystem-")
    assert not (fixture_path(root, "vault") / "99_System/Smoke").exists()
    assert not (fixture_path(root, "state") / "smoke").exists()


@pytest.mark.parametrize(
    ("kind", "adapter", "timeout_seconds", "authorized", "code"),
    (
        ("filesystem", "filesystem-roundtrip", 5, False, "SMOKE_AUTHORIZATION_REQUIRED"),
        ("device", "filesystem-roundtrip", 5, True, "SMOKE_ADAPTER_NOT_REGISTERED"),
        ("filesystem", "unregistered", 5, True, "SMOKE_ADAPTER_NOT_REGISTERED"),
        ("filesystem", "filesystem-roundtrip", 121, True, "SMOKE_TIMEOUT_INVALID"),
    ),
)
def test_smoke_preflight_failures_have_no_vault_or_runtime_effect(
    tmp_path: Path,
    kind: str,
    adapter: str,
    timeout_seconds: int,
    authorized: bool,
    code: str,
) -> None:
    root = _smoke_root(tmp_path)
    before = tuple(sorted(path.relative_to(root).as_posix() for path in root.rglob("*")))

    with pytest.raises(smoke.SmokeError) as caught:
        smoke.run_smoke(
            root,
            kind=kind,
            adapter=adapter,
            authorized=authorized,
            timeout_seconds=timeout_seconds,
        )

    assert caught.value.code == code
    assert tuple(sorted(path.relative_to(root).as_posix() for path in root.rglob("*"))) == before


def test_smoke_success_cleans_exact_probe_and_retains_private_receipt(tmp_path: Path) -> None:
    root = _smoke_root(tmp_path)

    report = smoke.run_smoke(
        root,
        kind="filesystem",
        adapter="filesystem-roundtrip",
        authorized=True,
        timeout_seconds=5,
    )

    assert report["status"] == "PASS"
    assert report["assertion_passed"] is True
    assert report["cleanup_passed"] is True
    assert report["observations"]["static"]["state"] == "pass"
    assert report["observations"]["runtime"]["state"] == "pass"
    assert report["observations"]["deployment"]["state"] == "not_run"
    assert report["observations"]["device"]["state"] == "not_run"
    assert not (fixture_path(root, "vault") / "99_System/Smoke").exists()
    assert report["relative_path"].startswith("99_System/Smoke/")
    smoke_root = (fixture_path(root, "state") / "smoke")
    journal = smoke_root / "runs" / report["run_id"] / "journal.jsonl"
    receipt_path = smoke_root / "receipts" / f"{report['run_id']}.json"
    assert journal.is_file() and receipt_path.is_file()
    assert stat.S_IMODE(smoke_root.stat().st_mode) == 0o700
    assert stat.S_IMODE((fixture_path(root, "state")).stat().st_mode) == 0o700
    assert stat.S_IMODE((smoke_root / "runs").stat().st_mode) == 0o700
    assert stat.S_IMODE((smoke_root / "receipts").stat().st_mode) == 0o700
    assert stat.S_IMODE((smoke_root / "active").stat().st_mode) == 0o700
    assert stat.S_IMODE(journal.parent.stat().st_mode) == 0o700
    assert stat.S_IMODE(journal.stat().st_mode) == 0o600
    assert stat.S_IMODE(receipt_path.stat().st_mode) == 0o600
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    assert receipt["cleanup_passed"] is True
    assert "Deterministic disposable probe" not in json.dumps(receipt)
    assert not (smoke_root / "active" / f"{_RUN_ID}.json").exists()
    assert not smoke._path_git_status(smoke._validated_roots(root), report["relative_path"])


def test_smoke_success_uses_selected_vault_and_runtime_roots(tmp_path: Path) -> None:
    roots = _separate_smoke_roots(tmp_path)

    report = smoke.run_smoke(
        roots.control,
        kind="filesystem",
        adapter="filesystem-roundtrip",
        authorized=True,
        timeout_seconds=5,
    )

    assert report["status"] == "PASS"
    assert not (roots.vault / report["relative_path"]).exists()
    assert not (roots.vault / "99_System/Smoke").exists()
    journal = roots.state / "smoke/runs" / report["run_id"] / "journal.jsonl"
    receipt = roots.state / "smoke/receipts" / f"{report['run_id']}.json"
    assert journal.is_file() and receipt.is_file()
    assert not (roots.control / "KnowledgeHub").exists()
    assert not (roots.control / "runtime").exists()


def test_smoke_refusal_with_selected_roots_has_no_vault_or_runtime_effect(
    tmp_path: Path,
) -> None:
    roots = _separate_smoke_roots(tmp_path)
    control_before = tuple(sorted(path.relative_to(roots.control).as_posix() for path in roots.control.rglob("*")))
    vault_before = tuple(sorted(path.relative_to(roots.vault).as_posix() for path in roots.vault.rglob("*")))
    runtime_before = tuple(sorted(path.relative_to(roots.runtime).as_posix() for path in roots.runtime.rglob("*")))

    with pytest.raises(smoke.SmokeError) as caught:
        smoke.run_smoke(
            roots.control,
            kind="filesystem",
            adapter="filesystem-roundtrip",
            authorized=False,
            timeout_seconds=5,
        )

    assert caught.value.code == "SMOKE_AUTHORIZATION_REQUIRED"
    assert tuple(sorted(path.relative_to(roots.control).as_posix() for path in roots.control.rglob("*"))) == control_before
    assert tuple(sorted(path.relative_to(roots.vault).as_posix() for path in roots.vault.rglob("*"))) == vault_before
    assert tuple(sorted(path.relative_to(roots.runtime).as_posix() for path in roots.runtime.rglob("*"))) == runtime_before


def test_stale_recovery_deletes_only_the_journal_bound_sealed_probe(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _smoke_root(tmp_path)
    run = _start_stale_run(root, monkeypatch)
    probe = fixture_path(root, "vault") / run.relative_path
    assert probe.is_file()
    sealed = smoke.sha256_bytes(probe.read_bytes())

    report = smoke.recover_smoke(root, run_id=run.run_id, authorized=True)

    assert report["status"] == "PASS"
    assert report["removed"] is True
    assert report["observations"]["runtime"]["state"] == "pass"
    assert report["observations"]["device"]["state"] == "not_run"
    assert not probe.exists()
    assert not probe.parent.exists()
    assert report["receipt"]["cleanup_passed"] is True
    assert not run.active_path.exists()
    assert sealed in smoke._sealed_digests(run.journal.records())


def test_uncatchable_child_exit_leaves_a_probe_that_stale_recovery_removes(
    tmp_path: Path,
) -> None:
    roots = _separate_smoke_roots(tmp_path)
    child_program = textwrap.dedent(
        """\
        import os
        import sys
        import time
        from datetime import UTC, datetime
        from pathlib import Path

        from vaultops import smoke

        smoke._now = lambda: datetime(2020, 1, 1, tzinfo=UTC)
        run = smoke._reserve_run(
            Path(sys.argv[1]),
            kind="filesystem",
            adapter="filesystem-roundtrip",
            timeout_seconds=5,
            authorized=True,
            run_id="411602c1-5278-4a8b-8b96-9183fb6ef8c2",
        )
        assert smoke._exercise(run, deadline=time.monotonic() + 5)
        os._exit(86)
        """
    )
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(Path(__file__).resolve().parents[1] / "src")
    completed = subprocess.run(
        [sys.executable, "-c", child_program, str(roots.control)],
        cwd=Path(__file__).resolve().parents[1],
        env=environment,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )

    assert completed.returncode == 86, completed.stderr
    journal = smoke._SmokeJournal(roots.control, _RUN_ID)
    intent = journal.intent()
    probe = roots.vault / intent["relative_path"]
    assert probe.is_file()
    assert journal.path.is_relative_to(roots.state)
    active = smoke._active_lock(smoke._active_path(smoke._validated_roots(roots.control)))
    assert active is not None and active["run_id"] == _RUN_ID

    report = smoke.recover_smoke(roots.control, run_id=_RUN_ID, authorized=True)

    assert report["status"] == "PASS"
    assert report["removed"] is True
    assert not probe.exists()
    assert journal.records()[-1]["state"] == "cleaned"
    assert (roots.state / "smoke/receipts" / f"{_RUN_ID}.json").is_file()
    assert not (roots.control / "KnowledgeHub").exists()
    assert not (roots.control / "runtime").exists()


def test_selected_root_identity_mismatch_requests_confirmation_without_deletion(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    roots = _separate_smoke_roots(tmp_path)
    run = _start_stale_run(roots.control, monkeypatch)
    probe = roots.vault / run.relative_path
    original = probe.read_bytes()
    sentinel_path = roots.vault / ".knowledgeos-root.json"
    sentinel = json.loads(sentinel_path.read_text(encoding="utf-8"))
    sentinel["vault_uuid"] = "511602c1-5278-4a8b-8b96-9183fb6ef8c2"
    sentinel_path.write_bytes(canonical_json_bytes(sentinel) + b"\n")

    report = smoke.recover_smoke(roots.control, run_id=run.run_id, authorized=True)

    assert report["status"] == "CONFLICT"
    assert report["reason_code"] == "SMOKE_VAULT_IDENTITY_MISMATCH"
    confirmation = report["confirmation_request"]
    assert confirmation["required"] is True
    assert confirmation["relative_path"] == run.relative_path
    assert confirmation["current_sha256"] == smoke.sha256_bytes(original)
    assert confirmation["automatic_deletion"] is False
    assert probe.read_bytes() == original
    assert run.active_path.is_file()
    assert run.journal.path.is_relative_to(roots.state)
    assert not (roots.control / "KnowledgeHub").exists()
    assert not (roots.control / "runtime").exists()


def test_stale_recovery_accepts_absent_probe_without_touching_user_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _smoke_root(tmp_path)
    user_note = (fixture_path(root, "vault") / "Ordinary.md")
    user_note.write_text("user-owned\n", encoding="utf-8")
    run = _start_stale_run(root, monkeypatch)
    (fixture_path(root, "vault") / run.relative_path).unlink()

    report = smoke.recover_smoke(root, run_id=run.run_id, authorized=True)

    assert report["status"] == "PASS"
    assert report["removed"] is True
    assert user_note.read_text(encoding="utf-8") == "user-owned\n"
    assert not (fixture_path(root, "vault") / "99_System/Smoke").exists()


@pytest.mark.parametrize(
    ("mutation", "reason"),
    (
        ("marker", "SMOKE_OWNERSHIP_MARKER_MISMATCH"),
        ("digest", "SMOKE_DIGEST_NOT_SEALED"),
        ("unsealed", "SMOKE_DIGEST_NOT_SEALED"),
        ("symlink", "SMOKE_PROBE_UNSAFE"),
    ),
)
def test_stale_note_mismatch_is_preserved_with_exact_confirmation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mutation: str,
    reason: str,
) -> None:
    root = _smoke_root(tmp_path)
    run = _start_unsealed_run(root, monkeypatch) if mutation == "unsealed" else _start_stale_run(root, monkeypatch)
    probe = fixture_path(root, "vault") / run.relative_path
    outside = tmp_path / "user-owned.txt"
    outside.write_text("never delete\n", encoding="utf-8")
    if mutation == "marker":
        probe.write_bytes(probe.read_bytes().replace(b"knowledgeos-smoke:v1", b"changed-smoke:v1", 1))
    elif mutation == "digest":
        probe.write_bytes(probe.read_bytes() + b"changed\n")
    elif mutation == "unsealed":
        pass
    elif mutation == "symlink":
        probe.unlink()
        probe.symlink_to(outside)
    before = probe.lstat()
    current_digest = None if mutation == "symlink" else smoke.sha256_bytes(probe.read_bytes())
    expected_bytes = probe.read_bytes() if mutation != "symlink" else None

    report = smoke.recover_smoke(root, run_id=run.run_id, authorized=True)

    confirmation = _assert_confirmation(report, run)
    assert report["reason_code"] == reason
    assert confirmation["current_sha256"] == current_digest
    assert probe.lstat().st_ino == before.st_ino
    if mutation == "symlink":
        assert probe.is_symlink()
        assert outside.read_text(encoding="utf-8") == "never delete\n"
    else:
        assert probe.read_bytes() == expected_bytes
    assert run.active_path.is_file()


def test_probe_changed_during_cleanup_is_rechecked_and_preserved(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _smoke_root(tmp_path)
    run = _start_stale_run(root, monkeypatch)
    probe = fixture_path(root, "vault") / run.relative_path
    append = smoke._SmokeJournal.append
    raced_bytes = probe.read_bytes() + b"concurrent-user-edit\n"

    def append_then_change(
        journal: smoke._SmokeJournal, state: str, payload: dict[str, object]
    ) -> dict[str, object]:
        record = append(journal, state, payload)
        if journal.run_id == run.run_id and state == "cleanup_started":
            probe.write_bytes(raced_bytes)
        return record

    monkeypatch.setattr(smoke._SmokeJournal, "append", append_then_change)
    report = smoke.recover_smoke(root, run_id=run.run_id, authorized=True)

    confirmation = _assert_confirmation(report, run)
    assert report["reason_code"] == "SMOKE_PROBE_CHANGED_BEFORE_DELETE"
    assert confirmation["current_sha256"] == smoke.sha256_bytes(raced_bytes)
    assert probe.read_bytes() == raced_bytes
    assert run.active_path.is_file()


def test_probe_recreated_after_unlink_is_preserved_as_cleanup_conflict(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _smoke_root(tmp_path)
    run = _start_stale_run(root, monkeypatch)
    probe = fixture_path(root, "vault") / run.relative_path
    recreated_bytes = b"user recreation after the owned probe was removed\n"
    unlink = smoke._unlink_probe

    def unlink_then_recreate(*args: object, **kwargs: object) -> None:
        unlink(*args, **kwargs)
        probe.write_bytes(recreated_bytes)
        _fast_forward_cleanup_window(monkeypatch)

    monkeypatch.setattr(smoke, "_unlink_probe", unlink_then_recreate)

    report = smoke.recover_smoke(root, run_id=run.run_id, authorized=True)

    confirmation = _assert_confirmation(report, run)
    assert report["reason_code"] == "SMOKE_PROBE_RECREATED"
    assert confirmation["current_sha256"] == smoke.sha256_bytes(recreated_bytes)
    assert probe.read_bytes() == recreated_bytes
    assert run.active_path.is_file()
    assert run.journal.records()[-1]["state"] == "conflict"
    assert not ((fixture_path(root, "state") / "smoke/receipts") / f"{run.run_id}.json").exists()


def test_run_probe_recreated_after_unlink_is_preserved_as_cleanup_conflict(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _smoke_root(tmp_path)
    recreated_bytes = b"user recreation after the owned probe was removed\n"
    unlink = smoke._unlink_probe

    def unlink_then_recreate(
        vault: Path,
        relative_path: str,
        *,
        expected_digest: str,
        expected_marker: str,
    ) -> None:
        unlink(
            vault,
            relative_path,
            expected_digest=expected_digest,
            expected_marker=expected_marker,
        )
        (vault / relative_path).write_bytes(recreated_bytes)
        _fast_forward_cleanup_window(monkeypatch)

    monkeypatch.setattr(smoke, "_unlink_probe", unlink_then_recreate)
    report = smoke.run_smoke(
        root,
        kind="filesystem",
        adapter="filesystem-roundtrip",
        authorized=True,
        timeout_seconds=5,
    )

    assert report["status"] == "CLEANUP_CONFLICT"
    confirmation = report["confirmation_request"]
    assert confirmation["required"] is True
    assert confirmation["reason_code"] == "SMOKE_PROBE_RECREATED"
    assert confirmation["current_sha256"] == smoke.sha256_bytes(recreated_bytes)
    probe = fixture_path(root, "vault") / report["relative_path"]
    assert probe.read_bytes() == recreated_bytes
    journal = smoke._SmokeJournal(root, report["run_id"])
    assert journal.records()[-1]["state"] == "conflict"
    assert smoke._active_path(smoke._validated_roots(root)).is_file()
    assert not ((fixture_path(root, "state") / "smoke/receipts") / f"{report['run_id']}.json").exists()


def _mutate_stale_recovery_binding(
    root: Path, run: smoke._Run, tmp_path: Path, mutation: str
) -> Path | None:
    if mutation == "vault_identity":
        path = (fixture_path(root, "vault") / ".knowledgeos-root.json")
        sentinel = json.loads(path.read_text(encoding="utf-8"))
        sentinel["vault_uuid"] = "511602c1-5278-4a8b-8b96-9183fb6ef8c2"
        path.write_bytes(canonical_json_bytes(sentinel) + b"\n")
    elif mutation == "journal_binding":
        lock = json.loads(run.active_path.read_text(encoding="utf-8"))
        lock["journal_relative_path"] = "state/smoke/runs/another-run/journal.jsonl"
        run.active_path.write_bytes(canonical_json_bytes(lock) + b"\n")
    elif mutation == "corrupt_journal":
        run.journal.path.write_bytes(b"not-json\n")
    elif mutation == "symlink_journal":
        outside = tmp_path / "opaque-private-data"
        outside.write_bytes(run.journal.path.read_bytes())
        run.journal.path.unlink()
        run.journal.path.symlink_to(outside)
        return outside
    else:
        records = [
            json.loads(line)
            for line in run.journal.path.read_text(encoding="utf-8").splitlines()
        ]
        records[0]["payload"]["intent"]["untrusted"] = "\ud800"
        run.journal.path.write_text(
            "\n".join(json.dumps(record) for record in records) + "\n",
            encoding="utf-8",
        )
    return None


@pytest.mark.parametrize(
    ("mutation", "reason", "bound_note_known"),
    (
        ("vault_identity", "SMOKE_VAULT_IDENTITY_MISMATCH", True),
        ("journal_binding", "SMOKE_PRIVATE_JOURNAL_BINDING_MISMATCH", True),
        ("corrupt_journal", "SMOKE_JOURNAL_CORRUPT", False),
        ("symlink_journal", "SMOKE_JOURNAL_PATH_INVALID", False),
        ("non_utf8_journal", "SMOKE_JOURNAL_CORRUPT", False),
    ),
    ids=("vault-identity", "journal-binding", "corrupt-journal", "symlink-journal", "non-utf8-journal"),
)
def test_stale_recovery_binding_mismatches_preserve_note_and_request_exact_confirmation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mutation: str,
    reason: str,
    bound_note_known: bool,
) -> None:
    root = _smoke_root(tmp_path)
    run = _start_stale_run(root, monkeypatch)
    probe = fixture_path(root, "vault") / run.relative_path
    original = probe.read_bytes()
    original_inode = probe.lstat().st_ino
    original_journal = run.journal.path.read_bytes()
    outside = _mutate_stale_recovery_binding(root, run, tmp_path, mutation)

    report = smoke.recover_smoke(root, run_id=run.run_id, authorized=True)

    assert report["status"] == "CONFLICT"
    assert report["reason_code"] == reason
    confirmation = report["confirmation_request"]
    assert confirmation["required"] is True
    assert confirmation["automatic_deletion"] is False
    assert confirmation["private_journal_path"] == (
        f"state/smoke/runs/{run.run_id}/journal.jsonl"
    )
    if bound_note_known:
        assert confirmation["relative_path"] == run.relative_path
        assert confirmation["current_sha256"] == smoke.sha256_bytes(original)
    else:
        assert confirmation["relative_path"] is None
        assert confirmation["current_sha256"] is None
    assert probe.lstat().st_ino == original_inode
    assert probe.read_bytes() == original
    assert run.active_path.is_file()
    if outside is not None:
        assert outside.read_bytes() == original_journal


def test_active_lock_mismatch_is_not_unlinked_during_cleanup(tmp_path: Path) -> None:
    root = _smoke_root(tmp_path)
    run = smoke._reserve_run(
        root,
        kind="filesystem",
        adapter="filesystem-roundtrip",
        timeout_seconds=5,
        authorized=True,
        run_id=_RUN_ID,
    )
    lock = json.loads(run.active_path.read_text(encoding="utf-8"))
    lock["vault_uuid"] = "511602c1-5278-4a8b-8b96-9183fb6ef8c2"
    run.active_path.write_bytes(canonical_json_bytes(lock) + b"\n")

    with pytest.raises(smoke.SmokeError) as caught:
        smoke._remove_active_lock(run)

    assert caught.value.code == "SMOKE_ACTIVE_LOCK_MISMATCH"
    assert run.active_path.is_file()


@pytest.mark.parametrize("mutation", ("extra_key", "invalid_status", "boolean_version"))
def test_existing_receipt_requires_exact_run_binding_and_shape(
    tmp_path: Path, mutation: str
) -> None:
    root = _smoke_root(tmp_path)
    run = smoke._reserve_run(
        root,
        kind="filesystem",
        adapter="filesystem-roundtrip",
        timeout_seconds=5,
        authorized=True,
        run_id=_RUN_ID,
    )
    receipt = smoke._write_receipt(
        run,
        status="PASS",
        assertion_passed=True,
        cleanup_passed=True,
    )
    if mutation == "extra_key":
        receipt["user_owned"] = True
    elif mutation == "invalid_status":
        receipt["status"] = {"not": "a status"}
    else:
        receipt["schema_version"] = True
    path = (fixture_path(root, "state") / "smoke/receipts") / f"{run.run_id}.json"
    path.write_bytes(canonical_json_bytes(receipt) + b"\n")

    with pytest.raises(smoke.SmokeError) as caught:
        smoke._existing_receipt(run)

    assert caught.value.code == "SMOKE_RECEIPT_INVALID"


def test_recovery_removes_only_exact_probe_and_preserves_extra_namespace_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _smoke_root(tmp_path)
    smoke_directory = (fixture_path(root, "vault") / "99_System/Smoke")
    smoke_directory.mkdir()
    extra = smoke_directory / "user-owned.md"
    extra.write_text("do not touch\n", encoding="utf-8")
    run = _start_stale_run(root, monkeypatch)
    probe = fixture_path(root, "vault") / run.relative_path

    report = smoke.recover_smoke(root, run_id=run.run_id, authorized=True)

    assert report["status"] == "PASS"
    assert not probe.exists()
    assert extra.read_text(encoding="utf-8") == "do not touch\n"
    assert smoke_directory.is_dir()


def test_recovery_keeps_a_preexisting_empty_smoke_namespace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = _smoke_root(tmp_path)
    smoke_directory = (fixture_path(root, "vault") / "99_System/Smoke")
    smoke_directory.mkdir()
    run = _start_stale_run(root, monkeypatch)

    report = smoke.recover_smoke(root, run_id=run.run_id, authorized=True)

    assert report["status"] == "PASS"
    assert smoke_directory.is_dir()
    assert list(smoke_directory.iterdir()) == []


def test_unexpired_run_cannot_be_recovered_or_deleted(tmp_path: Path) -> None:
    root = _smoke_root(tmp_path)
    run = smoke._reserve_run(
        root,
        kind="filesystem",
        adapter="filesystem-roundtrip",
        timeout_seconds=5,
        authorized=True,
        run_id=_RUN_ID,
    )
    assert smoke._exercise(run, deadline=time.monotonic() + 5)
    probe = fixture_path(root, "vault") / run.relative_path
    original = probe.read_bytes()

    report = smoke.recover_smoke(root, run_id=run.run_id, authorized=True)

    assert report["status"] == "ACTIVE"
    assert report["automatic_deletion"] is False
    assert probe.read_bytes() == original
    assert run.active_path.is_file()


def test_second_active_run_is_refused_before_a_second_journal_is_written(tmp_path: Path) -> None:
    root = _smoke_root(tmp_path)
    first = smoke._reserve_run(
        root,
        kind="filesystem",
        adapter="filesystem-roundtrip",
        timeout_seconds=5,
        authorized=True,
        run_id=_RUN_ID,
    )
    second_id = "511602c1-5278-4a8b-8b96-9183fb6ef8c2"

    with pytest.raises(smoke.SmokeError) as caught:
        smoke._reserve_run(
            root,
            kind="filesystem",
            adapter="filesystem-roundtrip",
            timeout_seconds=5,
            authorized=True,
            run_id=second_id,
        )

    assert caught.value.code == "SMOKE_ACTIVE_RUN_EXISTS"
    assert not ((fixture_path(root, "state") / "smoke/runs") / second_id).exists()
    assert first.active_path.is_file()


@pytest.mark.parametrize(
    ("failure", "expected_status"),
    (
        ("timeout", "TIMEOUT_CLEAN"),
        ("exception", "FAIL_CLEAN"),
        ("signal", "INTERRUPTED_CLEAN"),
    ),
)
def test_catchable_timeout_exception_and_signal_all_clean_the_probe(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
    expected_status: str,
) -> None:
    root = _smoke_root(tmp_path)
    exercise = smoke._exercise

    def fail_after_probe(run: smoke._Run, *, deadline: float) -> bool:
        assert exercise(run, deadline=deadline)
        if failure == "timeout":
            raise smoke.SmokeError("SMOKE_TIMEOUT", "test timeout")
        if failure == "signal":
            raise smoke._SmokeInterruption(signal.SIGTERM)
        raise RuntimeError("test action failure")

    monkeypatch.setattr(smoke, "_exercise", fail_after_probe)
    report = smoke.run_smoke(
        root,
        kind="filesystem",
        adapter="filesystem-roundtrip",
        authorized=True,
        timeout_seconds=5,
    )

    assert report["status"] == expected_status
    assert report["cleanup_passed"] is True
    assert not (fixture_path(root, "vault") / "99_System/Smoke").exists()
    assert not ((fixture_path(root, "state") / "smoke/active") / f"{_RUN_ID}.json").exists()


def test_smoke_cli_exposes_plan_and_requires_explicit_live_authorization(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from vaultops.cli import main

    root = _smoke_root(tmp_path)
    assert main(
        [
            "smoke",
            "plan",
            "--kind",
            "filesystem",
            "--adapter",
            "filesystem-roundtrip",
            "--root",
            str(root),
        ]
    ) == 0
    assert json.loads(capsys.readouterr().out)["authorization_required_for_run"] is True

    with pytest.raises(SystemExit) as caught:
        main(
            [
                "smoke",
                "run",
                "--kind",
                "filesystem",
                "--adapter",
                "filesystem-roundtrip",
                "--timeout-seconds",
                "5",
                "--root",
                str(root),
            ]
        )
    assert caught.value.code == 2
    assert not (fixture_path(root, "vault") / "99_System/Smoke").exists()
    assert not (fixture_path(root, "state") / "smoke").exists()


def test_smoke_cli_maps_cleanup_conflict_to_conflict_exit(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    from vaultops import cli

    root = _smoke_root(tmp_path)
    monkeypatch.setattr(
        cli,
        "run_smoke",
        lambda *_args, **_kwargs: {"operation": "smoke run", "status": "CLEANUP_CONFLICT"},
    )

    code = cli.main(
        [
            "smoke",
            "run",
            "--kind",
            "filesystem",
            "--adapter",
            "filesystem-roundtrip",
            "--timeout-seconds",
            "5",
            "--authorize-live-smoke",
            "--root",
            str(root),
        ]
    )

    assert code == 11
    assert json.loads(capsys.readouterr().out)["status"] == "CLEANUP_CONFLICT"


def test_invalid_active_lock_requests_manual_confirmation_without_new_run(
    tmp_path: Path,
) -> None:
    root = _smoke_root(tmp_path)
    run = smoke._reserve_run(
        root,
        kind="filesystem",
        adapter="filesystem-roundtrip",
        timeout_seconds=5,
        authorized=True,
        run_id=_RUN_ID,
    )
    assert smoke._exercise(run, deadline=time.monotonic() + 5)
    probe = fixture_path(root, "vault") / run.relative_path
    original = probe.read_bytes()
    run.active_path.write_text("not-json\n", encoding="utf-8")

    report = smoke.run_smoke(
        root,
        kind="filesystem",
        adapter="filesystem-roundtrip",
        authorized=True,
        timeout_seconds=5,
    )

    assert report["status"] == "CONFLICT"
    assert report["reason_code"] == "SMOKE_ACTIVE_LOCK_INVALID"
    assert report["confirmation_request"]["required"] is True
    assert report["confirmation_request"]["automatic_deletion"] is False
    assert report["confirmation_request"]["relative_path"] is None
    assert probe.read_bytes() == original
    assert run.active_path.is_file()

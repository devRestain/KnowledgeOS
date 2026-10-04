from __future__ import annotations

import fcntl
import hashlib
import importlib.util
import json
import os
import sys
import uuid
from pathlib import Path

import pytest
import yaml
from support.control_factory import fixture_path, make_control_root, write_fixture_config

from vaultops.paths import resolve_paths
from vaultops.recovery import RecoveryJournal

SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts/knowledgeos_scheduled_worker.py"
SPEC = importlib.util.spec_from_file_location("knowledgeos_scheduled_worker", SCRIPT_PATH)
assert SPEC is not None and SPEC.loader is not None
scheduled = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(scheduled)


def _fixture_layout(tmp_path: Path) -> tuple[Path, Path, Path]:
    seed = make_control_root(tmp_path / "seed", ())
    fabric = tmp_path / "AgentFabric"
    destinations = {
        "core": fabric / "Core",
        "vault": fabric / "Vaults/KnowledgeHub",
        "state": fabric / "States/Operations/knowledgeos",
        "runtime": fabric / "Runtimes/KnowledgeOS-runtime",
        "control": fabric / "Operations/KnowledgeOS",
    }
    for kind, destination in destinations.items():
        destination.parent.mkdir(parents=True, exist_ok=True)
        (seed if kind == "control" else fixture_path(seed, kind)).rename(destination)
    control = destinations.pop("control")
    write_fixture_config(control, **destinations)
    return control, destinations["vault"], destinations["state"]


def _fake_make(tmp_path: Path, output: str) -> tuple[Path, Path]:
    binary_directory = tmp_path / "bin"
    binary_directory.mkdir()
    captured = tmp_path / "argv.json"
    executable = binary_directory / "make"
    executable.write_text(
        f"#!{sys.executable}\n"
        "import json, os, sys\n"
        "from pathlib import Path\n"
        "if any(key in os.environ for key in ('MAKEFLAGS', 'MFLAGS', 'MAKEOVERRIDES', 'GNUMAKEFLAGS', 'MAKEFILES')):\n"
        "    raise SystemExit(125)\n"
        "Path(os.environ['CAPTURED_ARGS']).write_text(json.dumps(sys.argv[1:]))\n"
        "if os.environ.get('CAPTURED_ROOTS'):\n"
        "    Path(os.environ['CAPTURED_ROOTS']).write_text(json.dumps({key: os.environ.get(key) for key in ('KNOWLEDGEOS_CONTROL_SOURCE', 'KNOWLEDGEOS_CORE_SOURCE', 'KNOWLEDGEOS_VAULT_SOURCE', 'KNOWLEDGEOS_RUNTIME_SOURCE', 'KNOWLEDGEOS_STATE_SOURCE')}))\n"
        "if os.environ.get('RUN_FIXTURE_WORKER') == '1':\n"
        "    for source, target in (('FIXTURE_CONTROL_ROOT', 'KNOWLEDGEOS_CONTROL_ROOT'), ('FIXTURE_VAULT_ROOT', 'KNOWLEDGEOS_VAULT_ROOT'), ('FIXTURE_STATE_ROOT', 'KNOWLEDGEOS_STATE_ROOT')):\n"
        "        os.environ[target] = os.environ[source]\n"
        "    from vaultops.cli import main\n"
        "    if os.environ.get('FIXTURE_LOSE_RESPONSE') == '1':\n"
        "        from contextlib import redirect_stdout\n"
        "        from io import StringIO\n"
        "        with redirect_stdout(StringIO()):\n"
        "            result_code = main(['ai', 'worker', '--scheduled-report'])\n"
        "        sys.stdout.write('lost response\\n')\n"
        "        raise SystemExit(result_code)\n"
        "    raise SystemExit(main(['ai', 'worker', '--scheduled-report']))\n"
        "if os.environ.get('FIXTURE_UPDATE_ATTEMPT') == '1':\n"
        "    result = json.loads(os.environ['FIXTURE_RESULT'])\n"
        "    marker_path = Path(os.environ['KNOWLEDGEOS_STATE_SOURCE']) / 'worker/attempt.json'\n"
        "    marker = json.loads(marker_path.read_text(encoding='utf-8'))\n"
        "    marker['status'] = 'completed' if result['status'] == 'PASS' else 'needs_attention'\n"
        "    marker['finished_at'] = 'fixture-finished'\n"
        "    marker['result_status'] = result['status']\n"
        "    marker_path.write_text(json.dumps(marker, sort_keys=True, separators=(',', ':')) + '\\n', encoding='utf-8')\n"
        "sys.stdout.write(os.environ['FIXTURE_RESULT'])\n",
        encoding="utf-8",
    )
    executable.chmod(0o755)
    return binary_directory, captured


def test_fixed_launcher_dispatches_make_entrypoint_once_and_sanitizes_result(tmp_path: Path) -> None:
    control, vault, state = _fixture_layout(tmp_path)
    (vault / ".vault-bridge/requests").mkdir(parents=True)
    vault_file = vault / "sentinel.md"
    vault_file.write_bytes(b"preserve vault bytes\n")
    result = {
        "schema_version": 1,
        "record_type": "scheduled_worker_result",
        "operation": "ai worker",
        "status": "REPAIR_REQUIRED",
        "outcome": "needs_attention",
        "exit_code": 0,
        "delivery": "local",
        "agent_reasoning": False,
        "provider_called": False,
        "vault_mutated": False,
        "git_network_called": False,
        "request_count": 0,
        "recovery_status": "REPAIR_REQUIRED",
        "recovery_summary": {"jobs": 1, "repairable": 1},
        "private_detail": "must be rejected",
    }
    binary_directory, captured = _fake_make(tmp_path, json.dumps(result))
    captured_roots = tmp_path / "roots.json"

    record = scheduled._run(
        control,
        argv=[],
        base_environment={
            "PATH": str(binary_directory),
            "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src"),
            "CAPTURED_ARGS": str(captured),
            "CAPTURED_ROOTS": str(captured_roots),
            "FIXTURE_RESULT": json.dumps({key: value for key, value in result.items() if key != "private_detail"}),
            "FIXTURE_UPDATE_ATTEMPT": "1",
            "COMPOSE_FILE": "untrusted-compose.yaml",
            "COMPOSE_PROJECT_NAME": "untrusted-project",
            "MAKEFLAGS": "--eval=untrusted",
            "MAKEFILES": "untrusted.mk",
            "KNOWLEDGEOS_CONTROL_SOURCE": "/untrusted/control",
            "KNOWLEDGEOS_VAULT_SOURCE": "/untrusted/vault",
            "KNOWLEDGEOS_RUNTIME_SOURCE": "/untrusted/runtime",
            "KNOWLEDGEOS_STATE_SOURCE": "/untrusted/state",
        },
    )

    assert record["status"] == "REPAIR_REQUIRED"
    assert record["outcome"] == "needs_attention"
    assert record["exit_code"] == 0
    assert "private_detail" not in record
    assert json.loads(captured.read_text(encoding="utf-8")) == ["-C", str(control), "scheduled-worker"]
    assert json.loads(captured_roots.read_text(encoding="utf-8")) == {
        "KNOWLEDGEOS_CONTROL_SOURCE": str(control),
        "KNOWLEDGEOS_VAULT_SOURCE": str(vault),
        "KNOWLEDGEOS_CORE_SOURCE": str(fixture_path(control, "core")),
        "KNOWLEDGEOS_RUNTIME_SOURCE": str(fixture_path(control, "runtime")),
        "KNOWLEDGEOS_STATE_SOURCE": str(state),
    }
    assert vault_file.read_bytes() == b"preserve vault bytes\n"
    assert (state / "worker/launches").is_dir()
    assert list((state / "worker/launches").iterdir()) == []


def test_launcher_rejects_unexpected_arguments_before_dispatch(tmp_path: Path) -> None:
    control, _, _ = _fixture_layout(tmp_path)
    binary_directory, captured = _fake_make(tmp_path, "{}")

    record = scheduled._run(
        control,
        argv=["--command", "arbitrary"],
        base_environment={"PATH": str(binary_directory), "CAPTURED_ARGS": str(captured), "FIXTURE_RESULT": "{}"},
    )

    assert record["status"] == "FAIL"
    assert not captured.exists()


def test_launcher_discards_oversized_or_non_json_container_output(tmp_path: Path) -> None:
    control, _, _ = _fixture_layout(tmp_path)
    binary_directory, captured = _fake_make(tmp_path, "x" * (scheduled.OUTPUT_LIMIT_BYTES * 2))

    record = scheduled._run(
        control,
        argv=[],
        base_environment={
            "PATH": str(binary_directory),
            "CAPTURED_ARGS": str(captured),
            "FIXTURE_RESULT": "x" * (scheduled.OUTPUT_LIMIT_BYTES * 2),
        },
    )

    assert record == scheduled._failure(outcome="needs_attention", exit_code=10)


def test_fixed_launcher_runs_one_synthetic_worker_pass_and_changes_only_state(
    tmp_path: Path,
) -> None:
    control, vault, state = _fixture_layout(tmp_path)
    (vault / ".vault-bridge/requests").mkdir(parents=True)
    for name in ("queue", "quarantine", "runs", "logs", "cache"):
        (state / name).mkdir(mode=0o700)
    source_relative = "00_Inbox/Captures/2026/09/scheduled-source.md"
    destination_relative = "90_Archive/Captures/2026/scheduled-source.md"
    source = vault / source_relative
    destination = vault / destination_relative
    source.parent.mkdir(parents=True)
    destination.parent.mkdir(parents=True)
    source_bytes = b"scheduled source\n"
    destination_bytes = b"published destination\n"
    source.write_bytes(source_bytes)
    destination.write_bytes(destination_bytes)
    vault_before = {
        path.relative_to(vault).as_posix(): path.read_bytes()
        for path in vault.rglob("*")
        if path.is_file()
    }

    roots = resolve_paths(control)
    RecoveryJournal(roots, job_id=str(uuid.uuid4()), operation="capture_finalize").start(
        {
            "schema_version": 1,
            "source": source_relative,
            "destination": destination_relative,
            "source_sha256": hashlib.sha256(source_bytes).hexdigest(),
            "destination_sha256": hashlib.sha256(destination_bytes).hexdigest(),
        }
    )
    state_before = {
        path.relative_to(state).as_posix(): path.read_bytes()
        for path in state.rglob("*")
        if path.is_file()
    }
    binary_directory, captured = _fake_make(tmp_path, "")

    record = scheduled._run(
        control,
        argv=[],
        base_environment={
            "PATH": str(binary_directory),
            "CAPTURED_ARGS": str(captured),
            "FIXTURE_RESULT": "",
            "RUN_FIXTURE_WORKER": "1",
            "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src"),
            "FIXTURE_CONTROL_ROOT": str(control),
            "FIXTURE_VAULT_ROOT": str(vault),
            "FIXTURE_STATE_ROOT": str(state),
        },
    )

    state_after = {
        path.relative_to(state).as_posix(): path.read_bytes()
        for path in state.rglob("*")
        if path.is_file()
    }
    assert json.loads(captured.read_text(encoding="utf-8")) == ["-C", str(control), "scheduled-worker"]
    assert record["status"] == "REPAIR_REQUIRED"
    assert record["outcome"] == "needs_attention"
    assert record["exit_code"] == 0
    assert record["request_count"] == 0
    assert set(state_after) - set(state_before) == {
        "worker/attempt.json",
        "worker/launcher.lock",
        "worker/worker.lock",
    }
    assert (state / "worker/attempt.json").stat().st_mode & 0o777 == 0o600
    assert {
        path.relative_to(vault).as_posix(): path.read_bytes()
        for path in vault.rglob("*")
        if path.is_file()
    } == vault_before


def test_job_specification_is_inactive_no_agent_and_has_fixed_execution() -> None:
    specification = yaml.safe_load(
        (SCRIPT_PATH.parents[1] / "ops/config/scheduled-worker.yaml").read_text(encoding="utf-8")
    )

    assert specification["activation"]["enabled"] is False
    assert specification["activation"]["status"] == "unadmitted"
    assert specification["activation"]["profile"] == "unselected"
    assert specification["activation"]["supervisor"] == "unselected"
    assert specification["trigger"]["delivery"] == "local"
    assert specification["trigger"]["agent"] == "none"
    assert specification["trigger"]["no_agent"] is True
    assert specification["execution"]["provider"] is False
    assert specification["execution"]["git_network"] is False
    assert specification["execution"]["canonical_apply"] is False
    assert specification["execution"]["arguments_from_job"] is False


def test_lost_result_keeps_completed_attempt_and_allows_idempotent_next_tick(tmp_path: Path) -> None:
    control, vault, state = _fixture_layout(tmp_path)
    (vault / ".vault-bridge/requests").mkdir(parents=True)
    for name in ("queue", "quarantine", "runs", "logs", "cache"):
        (state / name).mkdir(mode=0o700)

    binary_directory, captured = _fake_make(tmp_path, "")
    environment = {
        "PATH": str(binary_directory),
        "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src"),
        "CAPTURED_ARGS": str(captured),
        "FIXTURE_RESULT": "",
        "RUN_FIXTURE_WORKER": "1",
        "FIXTURE_CONTROL_ROOT": str(control),
        "FIXTURE_VAULT_ROOT": str(vault),
        "FIXTURE_STATE_ROOT": str(state),
        "FIXTURE_LOSE_RESPONSE": "1",
    }

    lost = scheduled._run(control, argv=[], base_environment=environment)
    first_marker = json.loads((state / "worker/attempt.json").read_text(encoding="utf-8"))
    environment["FIXTURE_LOSE_RESPONSE"] = "0"
    replay = scheduled._run(control, argv=[], base_environment=environment)
    second_marker = json.loads((state / "worker/attempt.json").read_text(encoding="utf-8"))

    assert lost["outcome"] == "needs_attention"
    assert first_marker["status"] == "completed"
    assert first_marker["result_status"] == "PASS"
    assert replay["status"] == "PASS"
    assert second_marker["status"] == "completed"
    assert second_marker["attempt_id"] != first_marker["attempt_id"]


def test_launcher_rejects_state_symlink_drift_before_dispatch(tmp_path: Path) -> None:
    control, _, state = _fixture_layout(tmp_path)
    preserved = state.with_name("preserved-state")
    state.rename(preserved)
    outside = tmp_path / "outside-state"
    outside.mkdir(mode=0o700)
    state.symlink_to(outside, target_is_directory=True)
    binary_directory, captured = _fake_make(tmp_path, "{}")

    with pytest.raises(ValueError, match="[Ss]tate root"):
        scheduled._run(
            control,
            argv=[],
            base_environment={"PATH": str(binary_directory), "CAPTURED_ARGS": str(captured), "FIXTURE_RESULT": "{}"},
        )

    assert not captured.exists()


@pytest.mark.parametrize("root_name", ["control", "vault"])
def test_launcher_rejects_source_root_symlink_drift_before_dispatch(
    tmp_path: Path, root_name: str
) -> None:
    control, vault, _state = _fixture_layout(tmp_path)
    if root_name == "control":
        renamed = control.with_name("KnowledgeOS-source")
        control.rename(renamed)
        control.symlink_to(renamed, target_is_directory=True)
    else:
        vault.rmdir()
        outside = tmp_path / "outside-vault"
        outside.mkdir()
        vault.symlink_to(outside, target_is_directory=True)

    binary_directory, captured = _fake_make(tmp_path, "{}")

    with pytest.raises(ValueError, match="symlink"):
        scheduled._run(
            control,
            argv=[],
            base_environment={
                "PATH": str(binary_directory),
                "CAPTURED_ARGS": str(captured),
                "FIXTURE_RESULT": "{}",
            },
        )

    assert not captured.exists()


@pytest.mark.parametrize("kill_returncode", [0, 1])
def test_timeout_kills_only_owned_container_or_keeps_an_unresolved_lease(
    tmp_path: Path, monkeypatch, kill_returncode: int
) -> None:
    control, _, state = _fixture_layout(tmp_path)
    binary_directory, _captured = _fake_make(tmp_path, "")
    make = binary_directory / "make"
    container_id = "a" * 64
    make.write_text(
        f"#!{sys.executable}\n"
        "import os, time\n"
        "from pathlib import Path\n"
        "Path(os.environ['CAPTURED_ARGS']).write_text('started')\n"
        f"Path(os.environ['KNOWLEDGEOS_SCHEDULED_CIDFILE']).write_text('{container_id}')\n"
        "time.sleep(30)\n",
        encoding="utf-8",
    )
    make.chmod(0o755)
    docker = binary_directory / "docker"
    docker.write_text(
        f"#!{sys.executable}\n"
        "import json, os, sys\n"
        "from pathlib import Path\n"
        "with Path(os.environ['CAPTURED_DOCKER']).open('a', encoding='utf-8') as handle:\n"
        "    handle.write(json.dumps(sys.argv[1:]) + '\\n')\n"
        "if sys.argv[1] == 'inspect':\n"
        "    sys.stdout.write(os.environ['FIXTURE_INSPECT'])\n"
        "    raise SystemExit(0)\n"
        "if sys.argv[1] == 'kill':\n"
        "    raise SystemExit(int(os.environ['FIXTURE_KILL_STATUS']))\n"
        "raise SystemExit(2)\n",
        encoding="utf-8",
    )
    docker.chmod(0o755)
    capture = tmp_path / "docker.jsonl"
    started = tmp_path / "started.txt"
    labels = {
        "com.docker.compose.service": "dev",
        "com.docker.compose.project.working_dir": str(control),
        "com.docker.compose.project.config_files": str(control / "ops/compose.yaml"),
    }
    monkeypatch.setattr(scheduled, "DEADLINE_SECONDS", 0.5)
    monkeypatch.setattr(scheduled, "CANCELLATION_GRACE_SECONDS", 0.2)
    environment = {
        "PATH": str(binary_directory),
        "CAPTURED_ARGS": str(started),
        "CAPTURED_DOCKER": str(capture),
        "FIXTURE_INSPECT": f"{container_id}|{json.dumps(labels)}\n",
        "FIXTURE_KILL_STATUS": str(kill_returncode),
    }

    record = scheduled._run(control, argv=[], base_environment=environment)
    marker = json.loads((state / "worker/attempt.json").read_text(encoding="utf-8"))
    calls = [json.loads(line) for line in capture.read_text(encoding="utf-8").splitlines()]
    started_before_retry = started.read_text(encoding="utf-8")
    retry = scheduled._run(control, argv=[], base_environment=environment)

    assert record["outcome"] == "needs_attention"
    assert record["exit_code"] == 124
    assert calls == [
        ["inspect", "--type", "container", "--format", "{{.Id}}|{{json .Config.Labels}}", container_id],
        ["kill", container_id],
    ]
    assert marker["status"] == ("needs_attention" if kill_returncode == 0 else "running")
    assert retry["outcome"] == "needs_attention"
    assert retry["exit_code"] == 30
    assert started.read_text(encoding="utf-8") == started_before_retry


def test_scheduler_contract_double_coalesces_missed_ticks() -> None:
    specification = yaml.safe_load(
        (SCRIPT_PATH.parents[1] / "ops/config/scheduled-worker.yaml").read_text(encoding="utf-8")
    )
    due_ticks = 7
    dispatches: list[str] = []
    for _tick in range(min(due_ticks, specification["trigger"]["maximum_catch_up_runs"])):
        dispatches.append("one-shot")

    assert due_ticks == 7
    assert dispatches == ["one-shot"]


def test_host_launch_lock_rejects_duplicate_scheduler_start(tmp_path: Path) -> None:
    control, _, state = _fixture_layout(tmp_path)
    worker_directory = state / "worker"
    worker_directory.mkdir(mode=0o700)
    descriptor = os.open(worker_directory / "launcher.lock", os.O_CREAT | os.O_RDWR, 0o600)
    fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    binary_directory, captured = _fake_make(tmp_path, "{}")
    try:
        record = scheduled._run(
            control,
            argv=[],
            base_environment={"PATH": str(binary_directory), "CAPTURED_ARGS": str(captured), "FIXTURE_RESULT": "{}"},
        )
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)

    assert record["outcome"] == "needs_attention"
    assert record["exit_code"] == 30
    assert not captured.exists()

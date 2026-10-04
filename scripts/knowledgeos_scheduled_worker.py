#!/usr/bin/env python3
"""Run the fixed one-shot KnowledgeOS worker through its project container."""

from __future__ import annotations

import fcntl
import json
import os
import re
import selectors
import signal
import stat
import subprocess
import sys
import time
import tomllib
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

DEADLINE_SECONDS = 240
CANCELLATION_GRACE_SECONDS = 10
OUTPUT_LIMIT_BYTES = 8192
RESULT_KEYS = frozenset(
    {
        "schema_version",
        "record_type",
        "operation",
        "status",
        "outcome",
        "exit_code",
        "delivery",
        "agent_reasoning",
        "provider_called",
        "vault_mutated",
        "git_network_called",
        "request_count",
        "recovery_status",
        "recovery_summary",
    }
)
ATTEMPT_KEYS = frozenset(
    {"schema_version", "attempt_id", "owner", "status", "started_at", "finished_at", "result_status"}
)
SUMMARY_KEYS = frozenset({"jobs", "complete", "repairable", "conflict"})
COMPOSE_ENV_KEYS = (
    "COMPOSE_FILE",
    "COMPOSE_PROJECT_NAME",
    "COMPOSE_PROFILES",
    "COMPOSE_PATH_SEPARATOR",
    "COMPOSE_ENV_FILES",
)
MAKE_ENV_KEYS = ("MAKEFLAGS", "MFLAGS", "MAKEOVERRIDES", "GNUMAKEFLAGS", "MAKEFILES")


class SchedulerBusy(ValueError):
    """Raised when an earlier launch or another scheduler owns this State root."""


def _failure(*, outcome: str = "failed", exit_code: int = 10) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "record_type": "scheduled_worker_result",
        "operation": "ai worker",
        "status": "FAIL",
        "outcome": outcome,
        "exit_code": exit_code,
        "delivery": "local",
        "agent_reasoning": False,
        "provider_called": False,
        "vault_mutated": False,
        "git_network_called": False,
        "request_count": 0,
        "recovery_status": "NOT_RUN",
        "recovery_summary": {},
    }


def _private_directory(path: Path, *, create: bool) -> None:
    if path.is_symlink():
        raise ValueError("State directory must not be a symlink")
    if create:
        path.mkdir(mode=0o700, parents=True, exist_ok=True)
    if not path.is_dir():
        raise ValueError("State directory must exist")
    info = path.stat()
    if stat.S_IMODE(info.st_mode) != 0o700 or info.st_uid != os.getuid():
        raise ValueError("State directory must be owned by the caller with mode 0700")


def _acquire_launch_lock(worker_directory: Path) -> int:
    path = worker_directory / "launcher.lock"
    descriptor = os.open(path, os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        info = os.fstat(descriptor)
        if (
            not stat.S_ISREG(info.st_mode)
            or stat.S_IMODE(info.st_mode) != 0o600
            or info.st_uid != os.getuid()
        ):
            raise ValueError("launcher lock ownership or mode is invalid")
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise SchedulerBusy("another scheduler invocation owns the launch lock") from error
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def _read_attempt(worker_directory: Path) -> dict[str, Any] | None:
    path = worker_directory / "attempt.json"
    if not path.exists() and not path.is_symlink():
        return None
    info = path.lstat()
    if (
        stat.S_ISLNK(info.st_mode)
        or not stat.S_ISREG(info.st_mode)
        or stat.S_IMODE(info.st_mode) != 0o600
        or info.st_uid != os.getuid()
        or info.st_size > 4096
    ):
        raise ValueError("worker attempt marker ownership or size is invalid")
    raw = path.read_bytes()
    value = json.loads(raw.decode("utf-8"))
    if not isinstance(value, dict) or set(value) != ATTEMPT_KEYS:
        raise ValueError("worker attempt marker schema is invalid")
    if raw != (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8"):
        raise ValueError("worker attempt marker is not canonical")
    if (
        type(value.get("schema_version")) is not int
        or value["schema_version"] != 1
        or not isinstance(value.get("attempt_id"), str)
        or not re.fullmatch(r"[0-9a-f]{32}", value["attempt_id"])
        or not isinstance(value.get("owner"), str)
        or value["owner"] not in {"manual", "scheduler"}
        or not isinstance(value.get("status"), str)
        or value["status"] not in {"running", "completed", "needs_attention"}
        or not isinstance(value.get("started_at"), str)
        or value.get("finished_at") is not None and not isinstance(value["finished_at"], str)
        or value.get("result_status") not in {"PASS", "REPAIR_REQUIRED", "CONFLICT", "FAIL", None}
        or value["status"] == "running" and (value["finished_at"] is not None or value["result_status"] is not None)
        or value["status"] == "completed" and (value["finished_at"] is None or value["result_status"] != "PASS")
        or value["status"] == "needs_attention"
        and (value["finished_at"] is None or value["result_status"] not in {"REPAIR_REQUIRED", "CONFLICT", "FAIL"})
    ):
        raise ValueError("worker attempt marker fields are invalid")
    return value


def _attempt_record(
    attempt_id: str,
    *,
    started_at: str,
    status: str,
    result_status: str | None,
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "attempt_id": attempt_id,
        "owner": "scheduler",
        "status": status,
        "started_at": started_at,
        "finished_at": None
        if status == "running"
        else datetime.now(UTC).isoformat(timespec="seconds"),
        "result_status": result_status,
    }


def _write_attempt(worker_directory: Path, record: dict[str, Any]) -> None:
    path = worker_directory / "attempt.json"
    if path.is_symlink():
        raise ValueError("worker attempt marker must not be a symlink")
    temporary = worker_directory / f".attempt-{uuid.uuid4().hex}.tmp"
    payload = (json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
    descriptor = os.open(
        temporary,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
        0o600,
    )
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        directory_fd = os.open(worker_directory, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def _mark_attempt_attention(
    worker_directory: Path, *, attempt_id: str, started_at: str
) -> dict[str, Any] | None:
    current = _read_attempt(worker_directory)
    if (
        current is not None
        and current["attempt_id"] == attempt_id
        and current["owner"] == "scheduler"
        and current["status"] == "running"
    ):
        _write_attempt(
            worker_directory,
            _attempt_record(
                attempt_id,
                started_at=started_at,
                status="needs_attention",
                result_status="FAIL",
            ),
        )
        return _read_attempt(worker_directory)
    return current


def _roots(repo_root: Path) -> tuple[Path, Path, Path, Path, Path]:
    if repo_root.is_symlink():
        raise ValueError("control root must not be a symlink")
    control = repo_root.resolve(strict=True)
    config_path = control / "ops/vaultops.toml"
    if config_path.is_symlink() or not config_path.is_file():
        raise ValueError("an explicit private config v3 is required")
    config = tomllib.loads(config_path.read_text(encoding="utf-8"))
    keys = {"schema_version", "project_name", "timezone", "core_root", "control_root", "vault_root", "state_root", "runtime_root"}
    if set(config) != keys or type(config["schema_version"]) is not int or config["schema_version"] != 3:
        raise ValueError("declare five independent roots in private config v3")
    selected: dict[str, Path] = {}
    for kind in ("core", "control", "vault", "state", "runtime"):
        value = config[kind + "_root"]
        if not isinstance(value, str) or not value:
            raise ValueError(f"{kind} root must be an absolute path")
        path = Path(value).expanduser()
        if not path.is_absolute() or ".." in path.parts:
            raise ValueError(f"{kind} root must be an absolute path")
        if any(parent.is_symlink() for parent in (path, *path.parents)) or not path.is_dir():
            raise ValueError(f"{kind} root must be an existing non-symlink directory")
        selected[kind] = path.resolve(strict=True)
        if kind in {"state", "runtime"}:
            _private_directory(path, create=False)
            if (path / ".git").exists():
                raise ValueError(f"{kind} root must not be a Git checkout")
    if selected["control"] != control:
        raise ValueError("private configuration must bind this exact control root")
    paths = list(selected.values())
    if any(a == b or a in b.parents or b in a.parents for i, a in enumerate(paths) for b in paths[i + 1:]):
        raise ValueError("Core, control, Vault, State and Runtime must be independent")
    return control, selected["vault"], selected["state"], selected["runtime"], selected["core"]


def _clean_record(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict) or set(value) != RESULT_KEYS:
        return None
    if (
        value.get("schema_version") != 1
        or value.get("record_type") != "scheduled_worker_result"
        or value.get("operation") != "ai worker"
        or value.get("status") not in {"PASS", "REPAIR_REQUIRED", "CONFLICT", "FAIL"}
        or value.get("outcome") not in {"completed", "needs_attention", "failed"}
        or value.get("delivery") != "local"
        or value.get("agent_reasoning") is not False
        or value.get("provider_called") is not False
        or value.get("vault_mutated") is not False
        or value.get("git_network_called") is not False
        or not isinstance(value.get("exit_code"), int)
        or isinstance(value.get("exit_code"), bool)
        or not isinstance(value.get("request_count"), int)
        or isinstance(value.get("request_count"), bool)
        or not 0 <= value["request_count"] <= 100
        or value.get("recovery_status") not in {"PASS", "REPAIR_REQUIRED", "CONFLICT", "FAIL", "NOT_RUN"}
        or not isinstance(value.get("recovery_summary"), dict)
        or set(value["recovery_summary"]) - SUMMARY_KEYS
    ):
        return None
    summary = value["recovery_summary"]
    if any(
        not isinstance(count, int) or isinstance(count, bool) or not 0 <= count <= 100_000
        for count in summary.values()
    ):
        return None
    status = value["status"]
    expected_outcome = (
        "needs_attention"
        if status == "REPAIR_REQUIRED" or value["exit_code"] == 124
        else "completed" if status == "PASS" else "failed"
    )
    if value["outcome"] != expected_outcome:
        return None
    return {
        **{key: value[key] for key in RESULT_KEYS if key != "recovery_summary"},
        "recovery_summary": dict(summary),
    }


def _drain_process(
    process: subprocess.Popen[bytes], *, deadline: float
) -> tuple[int | None, bytes, bytes, bool]:
    selector = selectors.DefaultSelector()
    outputs = {"stdout": bytearray(), "stderr": bytearray()}
    for name, stream in (("stdout", process.stdout), ("stderr", process.stderr)):
        if stream is not None:
            os.set_blocking(stream.fileno(), False)
            selector.register(stream, selectors.EVENT_READ, name)
    timed_out = False
    try:
        while selector.get_map():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                timed_out = True
                break
            for key, _ in selector.select(min(remaining, 0.2)):
                try:
                    chunk = os.read(key.fileobj.fileno(), 4096)
                except OSError:
                    chunk = b""
                if not chunk:
                    selector.unregister(key.fileobj)
                    key.fileobj.close()
                    continue
                output = outputs[key.data]
                if len(output) < OUTPUT_LIMIT_BYTES:
                    output.extend(chunk[: OUTPUT_LIMIT_BYTES - len(output)])
    finally:
        selector.close()
    if timed_out:
        raise TimeoutError("worker deadline exceeded")
    return process.wait(), bytes(outputs["stdout"]), bytes(outputs["stderr"]), False


def _terminate_owned_container(
    cidfile: Path, environment: dict[str, str], control_root: Path
) -> bool:
    try:
        if cidfile.is_symlink() or not cidfile.is_file() or cidfile.stat().st_size > 128:
            return False
        container_id = cidfile.read_text(encoding="ascii").strip()
    except (OSError, UnicodeError):
        return False
    if len(container_id) != 64 or any(char not in "0123456789abcdef" for char in container_id):
        return False
    try:
        inspected = subprocess.run(
            [
                "docker",
                "inspect",
                "--type",
                "container",
                "--format",
                "{{.Id}}|{{json .Config.Labels}}",
                container_id,
            ],
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=True,
            text=True,
            timeout=CANCELLATION_GRACE_SECONDS,
        )
        reported_id, raw_labels = inspected.stdout.strip().split("|", 1)
        labels = json.loads(raw_labels)
        if not isinstance(labels, dict):
            return False
        expected_config = str(control_root / "ops/compose.yaml")
        config_files = labels.get("com.docker.compose.project.config_files", "")
        config_paths = {value.strip() for value in config_files.split(",")}
        if (
            reported_id != container_id
            or labels.get("com.docker.compose.service") != "dev"
            or labels.get("com.docker.compose.project.working_dir") != str(control_root)
            or expected_config not in config_paths
        ):
            return False
        killed = subprocess.run(
            ["docker", "kill", container_id],
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
            timeout=CANCELLATION_GRACE_SECONDS,
        )
        return killed.returncode == 0
    except (OSError, ValueError, TypeError, subprocess.SubprocessError, json.JSONDecodeError):
        return False


def _run(repo_root: Path, *, argv: list[str], base_environment: dict[str, str]) -> dict[str, Any]:
    if argv:
        return _failure()
    control, vault, state, runtime, core = _roots(repo_root)
    worker_directory = state / "worker"
    _private_directory(worker_directory, create=True)
    launch_directory = worker_directory / "launches"
    _private_directory(launch_directory, create=True)
    cidfile = launch_directory / f"{uuid.uuid4().hex}.cid"
    attempt_id = uuid.uuid4().hex
    started_at = datetime.now(UTC).isoformat(timespec="seconds")
    environment = dict(base_environment)
    for key in (*COMPOSE_ENV_KEYS, *MAKE_ENV_KEYS):
        environment.pop(key, None)
    environment.update(
        {
            "KNOWLEDGEOS_CONTROL_SOURCE": str(control),
            "KNOWLEDGEOS_VAULT_SOURCE": str(vault),
            "KNOWLEDGEOS_CORE_SOURCE": str(core),
            "KNOWLEDGEOS_RUNTIME_SOURCE": str(runtime),
            "KNOWLEDGEOS_STATE_SOURCE": str(state),
            "KNOWLEDGEOS_UID": str(os.getuid()),
            "KNOWLEDGEOS_GID": str(os.getgid()),
            "KNOWLEDGEOS_SCHEDULED_CIDFILE": str(cidfile),
            "KNOWLEDGEOS_SCHEDULED_ATTEMPT_ID": attempt_id,
        }
    )
    process: subprocess.Popen[bytes] | None = None
    launch_lock: int | None = None
    attempt_written = False
    try:
        launch_lock = _acquire_launch_lock(worker_directory)
        previous = _read_attempt(worker_directory)
        if previous is not None and previous["status"] in {"running", "needs_attention"}:
            return _failure(outcome="needs_attention", exit_code=30)
        _write_attempt(
            worker_directory,
            _attempt_record(
                attempt_id,
                started_at=started_at,
                status="running",
                result_status=None,
            ),
        )
        attempt_written = True
        process = subprocess.Popen(
            ["make", "-C", str(control), "scheduled-worker"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=environment,
            start_new_session=True,
        )
        try:
            return_code, stdout, _stderr, _ = _drain_process(
                process, deadline=time.monotonic() + DEADLINE_SECONDS
            )
        except TimeoutError:
            terminated = _terminate_owned_container(cidfile, environment, control)
            try:
                os.killpg(process.pid, signal.SIGTERM)
                process.wait(timeout=CANCELLATION_GRACE_SECONDS)
            except (OSError, subprocess.TimeoutExpired):
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except OSError:
                    pass
                try:
                    process.wait(timeout=CANCELLATION_GRACE_SECONDS)
                except subprocess.TimeoutExpired:
                    pass
            if terminated:
                _mark_attempt_attention(
                    worker_directory,
                    attempt_id=attempt_id,
                    started_at=started_at,
                )
            return _failure(outcome="needs_attention", exit_code=124)
        current = _read_attempt(worker_directory)
        try:
            decoded = json.loads(stdout.decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError):
            if current is not None and current["attempt_id"] == attempt_id and current["status"] == "running":
                _mark_attempt_attention(worker_directory, attempt_id=attempt_id, started_at=started_at)
            return _failure(outcome="needs_attention", exit_code=return_code if return_code not in {None, 0} else 10)
        record = _clean_record(decoded)
        if record is None:
            if current is not None and current["attempt_id"] == attempt_id and current["status"] == "running":
                _mark_attempt_attention(worker_directory, attempt_id=attempt_id, started_at=started_at)
            return _failure(outcome="needs_attention", exit_code=return_code if return_code not in {None, 0} else 10)
        if return_code != record["exit_code"]:
            if current is not None and current["attempt_id"] == attempt_id and current["status"] == "running":
                _mark_attempt_attention(worker_directory, attempt_id=attempt_id, started_at=started_at)
            return _failure(outcome="needs_attention", exit_code=return_code if return_code not in {None, 0} else 10)
        if (
            current is None
            or current["attempt_id"] != attempt_id
            or current["owner"] != "scheduler"
            or current["status"] == "running"
            or current["result_status"] != record["status"]
        ):
            if current is not None and current["attempt_id"] == attempt_id and current["status"] == "running":
                _mark_attempt_attention(worker_directory, attempt_id=attempt_id, started_at=started_at)
            return _failure(outcome="needs_attention", exit_code=10)
        return record
    except SchedulerBusy:
        return _failure(outcome="needs_attention", exit_code=30)
    except (OSError, ValueError, TypeError, subprocess.SubprocessError):
        if attempt_written:
            try:
                _mark_attempt_attention(
                    worker_directory,
                    attempt_id=attempt_id,
                    started_at=started_at,
                )
            except (OSError, ValueError, TypeError):
                pass
        return _failure()
    finally:
        if process is not None and process.poll() is None:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except OSError:
                pass
            try:
                process.wait(timeout=CANCELLATION_GRACE_SECONDS)
            except subprocess.TimeoutExpired:
                pass
        if process is not None:
            for stream in (process.stdout, process.stderr):
                if stream is not None and not stream.closed:
                    stream.close()
        if not cidfile.is_symlink():
            cidfile.unlink(missing_ok=True)
        if launch_lock is not None:
            try:
                fcntl.flock(launch_lock, fcntl.LOCK_UN)
            finally:
                os.close(launch_lock)


def main(argv: list[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    try:
        record = _run(
            Path(__file__).resolve(strict=True).parents[1],
            argv=arguments,
            base_environment=os.environ.copy(),
        )
    except (OSError, TypeError, ValueError, RuntimeError):
        record = _failure()
    print(json.dumps(record, ensure_ascii=False, separators=(",", ":"), sort_keys=True))
    return int(record["exit_code"])


if __name__ == "__main__":
    raise SystemExit(main())

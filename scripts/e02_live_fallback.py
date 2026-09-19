#!/usr/bin/env python3
"""Run one E02 job in the optional networkless e02-live container."""

from __future__ import annotations

import argparse
import os
import stat
import subprocess
import sys
import time
from pathlib import Path

from e02_runner_support import (
    PROJECT_ROOT,
    clear_proxy_environment,
    job_directory,
    ollama_environment,
    private_relay_socket,
)

_RELAY_SCRIPT = PROJECT_ROOT / "scripts" / "e02_host_relay.py"
_COMPOSE_FILE = PROJECT_ROOT / "ops" / "compose.yaml"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run one E02 job in the isolated fallback container")
    parser.add_argument("job_dir", help="exactly one project-relative runtime/runs/<lowercase UUIDv4> spool")
    parser.add_argument(
        "--authorize-live-service",
        action="store_true",
        help="authorize one live loopback inspection/inference call through the per-job relay",
    )
    return parser


def _wait_for_socket(socket_path: Path, relay: subprocess.Popen[bytes]) -> None:
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        if relay.poll() is not None:
            raise RuntimeError("E02 host relay exited before the container started")
        if not socket_path.is_symlink() and socket_path.exists() and stat.S_ISSOCK(socket_path.stat().st_mode):
            return
        time.sleep(0.05)
    raise RuntimeError("E02 host relay did not create its private Unix socket")


def _numeric_environment(environment: dict[str, str]) -> None:
    environment.setdefault("KNOWLEDGEOS_UID", str(os.getuid()))
    environment.setdefault("KNOWLEDGEOS_GID", str(os.getgid()))
    environment.setdefault("KNOWLEDGEOS_UV_VERSION", "0.8.14")


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        job_dir = job_directory(args.job_dir)
    except ValueError as error:
        print(f"E02 fallback job rejected: {error}", file=sys.stderr)
        return 10

    clear_proxy_environment()
    environment = ollama_environment()
    _numeric_environment(environment)
    environment["E02_JOB_ID"] = job_dir.name
    environment["E02_JOB_DIR"] = str(job_dir)
    relay_socket = private_relay_socket(job_dir)
    relay: subprocess.Popen[bytes] | None = None
    try:
        if args.authorize_live_service:
            relay = subprocess.Popen(
                [sys.executable, str(_RELAY_SCRIPT), "--socket", str(relay_socket)],
                cwd=PROJECT_ROOT,
                env=environment,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=None,
            )
            _wait_for_socket(relay_socket, relay)
        command = [
            "docker",
            "compose",
            "-f",
            str(_COMPOSE_FILE),
            "--profile",
            "e02-live",
            "run",
            "--rm",
            "--no-deps",
            "e02-live",
            "vaultctl",
            "ai",
            "e02",
            "run",
            "--job-dir",
            f"/runtime/runs/{job_dir.name}",
            "--storage-root",
            "/runtime",
            "--base-url",
            "http://127.0.0.1:11434",
        ]
        if args.authorize_live_service:
            command.extend(
                [
                    "--relay-socket",
                    f"/runtime/runs/{job_dir.name}/.ollama-relay.sock",
                    "--authorize-live-service",
                ]
            )
        completed = subprocess.run(command, cwd=PROJECT_ROOT, env=environment, check=False)
        return completed.returncode
    except (OSError, RuntimeError) as error:
        print(f"E02 fallback failed before container completion: {error}", file=sys.stderr)
        return 10
    finally:
        if relay is not None and relay.poll() is None:
            relay.terminate()
            try:
                relay.wait(timeout=5)
            except subprocess.TimeoutExpired:
                relay.kill()
                relay.wait()
        if not relay_socket.is_symlink() and relay_socket.exists():
            try:
                if stat.S_ISSOCK(relay_socket.stat().st_mode):
                    relay_socket.unlink()
            except OSError:
                pass


if __name__ == "__main__":
    raise SystemExit(main())

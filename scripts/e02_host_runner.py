#!/usr/bin/env python3
"""Run exactly one bounded E02 host-native job through the project runtime."""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from e02_runner_support import PROJECT_ROOT, RUNTIME_ROOT, clear_proxy_environment, job_directory

OPS_SOURCE = PROJECT_ROOT / "ops" / "src"
if str(OPS_SOURCE) not in sys.path:
    sys.path.insert(0, str(OPS_SOURCE))

from vaultops.e02 import (
    DEFAULT_BASE_URL,
    E02Error,
    build_client,
    run_e02_host_job,
    write_e02_report,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run one private KnowledgeOS E02 job through loopback Ollama",
    )
    parser.add_argument(
        "job_dir",
        help="exactly one project-relative runtime/runs/<lowercase UUIDv4> spool",
    )
    parser.add_argument(
        "--authorize-live-service",
        action="store_true",
        help="authorize one live loopback inspection/inference call for this invocation",
    )
    return parser


def _print_report(report: dict[str, Any]) -> None:
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        job_dir = job_directory(args.job_dir)
    except ValueError as error:
        print(f"E02 job rejected: {error}", file=sys.stderr)
        return 10

    clear_proxy_environment()
    client: object = object()
    if args.authorize_live_service:
        try:
            client = build_client(DEFAULT_BASE_URL)
        except (E02Error, OSError, TypeError, ValueError) as error:
            print(f"E02 client rejected: {error}", file=sys.stderr)
            return 10
    try:
        report, exit_code = run_e02_host_job(
            job_dir,
            client,
            storage_root=RUNTIME_ROOT,
            authorized=args.authorize_live_service,
            base_url=DEFAULT_BASE_URL,
        )
        if report["status"] != "DEFERRED":
            write_e02_report(job_dir, report, storage_root=RUNTIME_ROOT)
        _print_report(report)
        return exit_code
    except (E02Error, OSError, TypeError, ValueError) as error:
        print(f"E02 job failed before a report could be persisted: {error}", file=sys.stderr)
        return 10


if __name__ == "__main__":
    raise SystemExit(main())

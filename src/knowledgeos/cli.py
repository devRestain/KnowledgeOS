"""``knowledgeos`` CLI (Blueprint §3.3). Except ``serve`` and source-distribution
installation, commands call the running Owner over its loopback ExOps API.

Endpoint: ``--endpoint``, else the ``--config`` file's host and port, else
``$KNOWLEDGEOS_ENDPOINT``, else ``http://127.0.0.1:8765``.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import signal
import sys
import threading
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from typing import Any

from .config import DEFAULT_PORT, ConfigError, load

TERMINAL = ("completed", "unmet", "failed", "refused", "cancelled", "unknown")


class CliError(Exception):
    pass


class Client:
    def __init__(self, endpoint: str, timeout: float = 30.0):
        self.endpoint = endpoint.rstrip("/")
        self.timeout = timeout
        self._opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def call(self, method: str, path: str, body: dict[str, Any] | None = None) -> tuple[int, Any]:
        data = None if body is None else json.dumps(body).encode("utf-8")
        headers = {} if data is None else {"Content-Type": "application/json"}
        request = urllib.request.Request(self.endpoint + path, data=data, method=method, headers=headers)
        try:
            with self._opener.open(request, timeout=self.timeout) as response:
                return response.status, _decode(response.read())
        except urllib.error.HTTPError as error:
            return error.code, _decode(error.read())
        except (urllib.error.URLError, OSError) as error:
            raise CliError(f"cannot reach the Owner at {self.endpoint}: {error}") from error

    def request(
        self, command: str, params: dict[str, Any], actor: str | None = None, wait: float = 600.0
    ) -> dict[str, Any]:
        """Submit a request and poll its receipt until it settles."""
        body: dict[str, Any] = {"request_id": str(uuid.uuid4()), "command": command, "params": params}
        if actor:
            body["actor"] = actor
        status, receipt = self.call("POST", "/api/requests", body)
        if status not in (200, 202) or not isinstance(receipt, dict):
            raise CliError(f"request rejected ({status}): {receipt}")
        return self.poll(body["request_id"], receipt, wait)

    def poll(self, request_id: str, receipt: dict[str, Any] | None = None, wait: float = 600.0) -> dict[str, Any]:
        """Poll a receipt until it settles."""
        deadline = time.monotonic() + wait
        while receipt is None or receipt.get("status") not in TERMINAL:
            if receipt is not None and time.monotonic() > deadline:
                raise CliError(f"request {request_id} is still {receipt.get('status')}; check it later")
            if receipt is not None:
                time.sleep(0.1)
            status, receipt = self.call("GET", f"/api/requests/{request_id}")
            if status != 200:
                raise CliError(f"receipt lookup failed ({status})")
        return receipt


def _decode(data: bytes) -> Any:
    try:
        return json.loads(data or b"null")
    except json.JSONDecodeError:
        return {"error": data.decode("utf-8", "replace")[:200]}


def _print_json(value: Any) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2))


def _endpoint(args: argparse.Namespace) -> str:
    if args.endpoint:
        return args.endpoint
    if args.config:
        return load(args.config).endpoint
    return os.environ.get("KNOWLEDGEOS_ENDPOINT", f"http://127.0.0.1:{DEFAULT_PORT}")


def _value(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


# ---------------------------------------------------------------- commands


def cmd_serve(args: argparse.Namespace) -> int:
    from .exops import ExOps
    from .hermes import host_from_config, notifier_from_config
    from .owner import Owner
    from .roles import HermesRunner

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    config = load(args.config)
    host = host_from_config(config)
    runner = HermesRunner(host, config.runtime, config.endpoint) if host else None
    owner = Owner(config, runner=runner, notifier=notifier_from_config(config, host))
    try:
        exops = ExOps(owner).start()
    except BaseException:
        owner.close()
        raise
    if runner is not None:
        runner.owner_url = exops.url
    stop = threading.Event()
    for signum in (signal.SIGINT, signal.SIGTERM):
        signal.signal(signum, lambda *_: stop.set())
    print(f"KnowledgeOS ExOps listening on {exops.url}", flush=True)
    try:
        stop.wait()
    finally:
        owner.close()
        exops.stop()
    return 0


def cmd_health(args: argparse.Namespace, client: Client) -> int:
    status, health = client.call("GET", "/api/health")
    _print_json(health)
    return 0 if status == 200 and health.get("healthy") else 1


def _embed(client: Client) -> int:
    """Run ``index.embed`` Works until none is pending. Each run is bounded by the Activity
    window, so a large first embedding takes several; it stops when a run makes no progress."""
    totals = {"embedded": 0, "removed": 0}
    while True:
        receipt = client.request("index.embed", {})
        outcome = receipt.get("outcome") or {}
        for key in totals:
            totals[key] += outcome.get(key, 0)
        progress = outcome.get("embedded", 0) or outcome.get("removed", 0)
        if receipt["status"] != "completed" or not outcome.get("pending") or not progress:
            break
    _print_json({
        **totals, "unchanged": outcome.get("unchanged", 0), "pending": outcome.get("pending", 0),
        "failed": outcome.get("failed", 0), "model": outcome.get("model"), "status": receipt["status"],
        "notice": receipt.get("notice"),
    })
    return 0 if receipt["status"] == "completed" else 1


def cmd_index(args: argparse.Namespace, client: Client) -> int:
    if args.action == "rebuild":
        status, result = client.call("POST", "/api/index/rebuild", {})
        _print_json(result)
        return 0 if status == 200 else 1
    if args.action == "embed":
        return _embed(client)
    status, result = client.call("GET", "/api/index/verify")
    _print_json(result)
    return 0 if status == 200 and result.get("ok") else 1


def cmd_vault_check(args: argparse.Namespace, client: Client) -> int:
    status, result = client.call("GET", "/api/vault-check")
    _print_json(result)
    if status != 200:
        return 1
    return 1 if result["invalid_yaml"] or result["conflicts"] or result["duplicate_ids"] else 0


def cmd_standardize(args: argparse.Namespace, client: Client) -> int:
    receipt = client.request("standardize.batch", {"path": args.path, "apply": bool(args.apply)})
    outcome = receipt.get("outcome") or {}
    for note in outcome.get("notes", []):
        print(note["path"])
        for change in note["changes"]:
            print(f"  {change['key']}: {_value(change['before'])} -> {_value(change['after'])} ({change['rule']})")
        for line in note["diff"].splitlines():
            print(f"    {line}")
    for item in outcome.get("skipped", []):
        print(f"skipped {item['path']}: {item['reason']}")
    for item in outcome.get("not_applied", []):
        print(f"not written {item['path']}: {item['reason']}")
    if args.dry_run:
        print("dry run: nothing written")
    print(f"{receipt['status']}: {receipt.get('notice') or ''}")
    return 0 if receipt["status"] == "completed" else 1


def cmd_work(args: argparse.Namespace, client: Client) -> int:
    status, work = client.call("GET", f"/api/works/{args.work_id}")
    if status != 200:
        print(f"knowledgeos: no Work {args.work_id}", file=sys.stderr)
        return 1
    _print_json(work)
    return 0


def cmd_job(args: argparse.Namespace, client: Client) -> int:
    """Trigger a job, then follow its separately admitted execution request."""
    trigger = client.request("job.run", {"job": args.job}, actor=f"schedule/{args.job}")
    child_id = (trigger.get("outcome") or {}).get("request_id")
    execution = client.poll(child_id) if trigger["status"] == "completed" and child_id else None
    _print_json({"trigger": trigger, "execution": execution})
    return 0 if execution is not None and execution["status"] == "completed" else 1


def cmd_reconcile(args: argparse.Namespace, client: Client) -> int:
    if not args.yes:
        raise CliError("reconciliation records your observation; confirm with --yes")
    receipt = client.request("outcome.reconcile", {"unknown_id": args.unknown_id, "resolution": args.resolution})
    _print_json(receipt)
    return 0 if receipt["status"] == "completed" else 1


def cmd_install(args: argparse.Namespace) -> int:
    from .hermes import host_from_config, install_profiles

    if not args.config:
        raise CliError("installation requires --config (Runtime, control root and settings.hermes)")
    config = load(args.config)
    if args.target != "service" and (args.plist or args.label):
        raise CliError("--plist and --label apply only to `install service`")
    if args.target == "service":
        from . import service

        try:
            report = service.install(
                config, Path(args.config).expanduser().resolve(), label=args.label or service.LABEL,
                plist=Path(args.plist) if args.plist else None, dry_run=args.dry_run,
            )
        except (service.ServiceError, OSError) as error:
            raise CliError(f"cannot install the Owner service: {error}") from error
        _print_json(report)
        return 0 if report["ok"] else 1
    host = host_from_config(config)
    if host is None:
        raise CliError("installation requires settings.hermes.binary")
    try:
        report = install_profiles(config, host, dry_run=args.dry_run)
    except OSError as error:
        raise CliError(f"cannot generate distribution: {error}") from error
    _print_json(report)
    return 0 if report["ok"] else 1


# ---------------------------------------------------------------- parser


def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--endpoint", help="Owner ExOps URL, e.g. http://127.0.0.1:8765")
    common.add_argument("--config", help="Owner config JSON (used for the endpoint)")
    parser = argparse.ArgumentParser(prog="knowledgeos", description="KnowledgeOS maintenance CLI")
    sub = parser.add_subparsers(dest="command", required=True)
    serve = sub.add_parser("serve", help="run the Owner and its ExOps backend")
    serve.add_argument("--config", required=True)
    serve.set_defaults(handler=cmd_serve, endpoint=None)
    sub.add_parser("health", parents=[common], help="Owner health").set_defaults(handler=cmd_health)
    index = sub.add_parser("index", parents=[common], help="search index maintenance")
    index.add_argument("action", choices=["rebuild", "verify", "embed"])
    index.set_defaults(handler=cmd_index)
    sub.add_parser("vault-check", parents=[common], help="invalid YAML, conflicts, duplicate IDs").set_defaults(
        handler=cmd_vault_check
    )
    standardize = sub.add_parser("standardize", parents=[common], help="batch standardization (C2)")
    standardize.add_argument("path", help="note or folder path in the Vault ('.' for all)")
    mode = standardize.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true", help="print planned changes; write nothing")
    mode.add_argument("--apply", action="store_true", help="write the changes")
    standardize.set_defaults(handler=cmd_standardize)
    work = sub.add_parser("work", help="Work records")
    work_sub = work.add_subparsers(dest="work_command", required=True)
    export = work_sub.add_parser("export", parents=[common], help="print one Work record as JSON")
    export.add_argument("work_id")
    export.set_defaults(handler=cmd_work)
    job = sub.add_parser("job", help="scheduled jobs (C7)")
    job_sub = job.add_subparsers(dest="job_command", required=True)
    run = job_sub.add_parser("run", parents=[common], help="run a declared job now")
    run.add_argument("job")
    run.set_defaults(handler=cmd_job)
    reconcile = sub.add_parser("reconcile", parents=[common], help="record a human observation of an unknown outcome")
    reconcile.add_argument("unknown_id")
    reconcile.add_argument("--resolution", choices=["applied", "not_applied"], required=True)
    reconcile.add_argument("--yes", action="store_true", help="confirm this observation (never reruns the effect)")
    reconcile.set_defaults(handler=cmd_reconcile)
    install = sub.add_parser(
        "install", parents=[common], help="install Hermes profiles or the Owner LaunchAgent from source"
    )
    install.add_argument("target", choices=["profiles", "service"])
    install.add_argument(
        "--dry-run", action="store_true",
        help="profiles: generate only under Runtime, do not invoke Hermes; service: print plist and argv, write nothing",
    )
    install.add_argument("--label", help="service: launchd label (default com.agentfabric.knowledgeos.owner)")
    install.add_argument("--plist", help="service: plist path (default ~/Library/LaunchAgents/<label>.plist)")
    install.set_defaults(handler=cmd_install)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.handler is cmd_serve:
            return cmd_serve(args)
        if args.handler is cmd_install:
            return cmd_install(args)
        return args.handler(args, Client(_endpoint(args)))
    except (CliError, ConfigError) as error:
        print(f"knowledgeos: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())

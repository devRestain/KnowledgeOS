"""Private host binding, actual HTTP/OS lock, and send-once launch semantics."""
from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import os
import plistlib
import socketserver
import subprocess
import sys
import threading
import time
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace

import pytest

SOURCE = Path(__file__).parents[1] / "host" / "local_backend.py"
SPEC = importlib.util.spec_from_file_location("knowledgeos_local_backend", SOURCE)
backend = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(backend)
IMAGE = "sha256:" + "a" * 64
STATUS = {"owner_operation_id": "knowledgeos", "read_only": False, "generation": 1,
          "revision": 2, "bindings": {}, "runtime_observation": "unavailable"}


def private_write(path, value):
    path.write_bytes(value)
    path.chmod(0o600)


@pytest.fixture
def installed(tmp_path, monkeypatch):
    root = tmp_path / "runtime"
    root.mkdir(mode=0o700)
    docker = root / "docker"
    docker.write_text("#!/bin/sh\nexit 0\n")
    docker.chmod(0o700)
    monkeypatch.setattr(backend, "LAUNCHCTL", str(docker))
    driver = root / "native_owner_driver.py"
    private_write(driver, b"# selected existing driver\n")
    compose = root / "compose.yaml"
    private_write(compose, b"services: {}\n")
    policy = root / "policy.json"
    private_write(policy, b'{"version":1}')
    environment = {"KNOWLEDGEOS_OWNER_PORT": "17900", "KNOWLEDGEOS_TEAM_IMAGE": IMAGE,
        "KNOWLEDGEOS_OWNER_CONTAINER": "selected-owner", "KNOWLEDGEOS_TEMPORAL_NETWORK": "private-temporal"}
    for name in ("CORE", "CONTROL", "VAULT", "STATE", "RUNTIME", "OWNER_POLICY", "TEMPORAL_ADAPTER"):
        environment["KNOWLEDGEOS_" + name + "_SOURCE"] = str(root / name.lower())
    for name in ("adapters", "hermes", "profile"):
        (root / name).mkdir(mode=0o700)
    plist_path = root / "manual-owner.plist"
    plist = {"Label": "test.knowledgeos", "RunAtLoad": False, "KeepAlive": False,
        "ProgramArguments": [sys.executable, str(driver), "--policy-file", str(policy),
            "--compose-file", str(compose), "--project", "selected", "--container-name", "selected-owner",
            "--docker-command", str(docker), "--adapters-directory", str(root / "adapters"),
            "--hermes-python", sys.executable, "--hermes-root", str(root / "hermes"),
            "--profile-home", str(root / "profile")], "EnvironmentVariables": environment}
    private_write(plist_path, plistlib.dumps(plist))
    binding = {"version": 1, "uid": os.getuid(), "plist": str(plist_path),
        "plist_sha256": hashlib.sha256(plist_path.read_bytes()).hexdigest(),
        "compose_sha256": hashlib.sha256(compose.read_bytes()).hexdigest(),
        "label": plist["Label"], "docker": str(docker), "project": "selected",
        "owner_container": "selected-owner", "owner_image": IMAGE,
        "relay": {"name": "selected-relay-1", "image": IMAGE}, "port": 17900,
        "temporal_network": {"name": "private-temporal", "id": "b" * 64},
        "temporal": [{"name": "temporal-" + service, "id": str(index) * 64,
            "image": IMAGE, "project": "temporal", "service": service, "health": "healthy"}
            for index, service in enumerate(("postgres", "server", "client"), 1)]}
    path = root / "binding.json"
    private_write(path, json.dumps(binding).encode())
    return SimpleNamespace(path=path, root=root, binding=binding, plist=plist, compose=compose)


class Host:
    """Stateful command-level model; assertions forbid unintended mutation commands."""
    def __init__(self, installed):
        self.install = installed
        self.binding = installed.binding
        self.now = 0.0
        self.calls = []
        self.registered = self.running = False
        self.ready_after = 0.0
        self.kicked_at = None
        self.docker_available = True
        self.compose_available = True
        self.job_changed = False
        self.port_reply = None
        self.temporal = {}
        for item in self.binding["temporal"]:
            self.temporal[item["id"]] = {"Id": item["id"], "Name": "/" + item["name"], "Image": item["image"],
                "Config": {"Labels": {"com.docker.compose.project": item["project"],
                    "com.docker.compose.service": item["service"]}},
                "State": {"Running": True, "Health": {"Status": "healthy"}},
                "NetworkSettings": {"Networks": {"private-temporal": {"NetworkID": "b" * 64}}}}
        self.containers = {}

    def clock(self):
        return self.now

    def sleep(self, duration):
        self.now += duration

    def create_backend(self):
        for index, role in enumerate(("owner", "relay"), 4):
            identity = str(index) * 64
            networks = {"selected_owner-private": {"NetworkID": "c" * 64}}
            if role == "owner":
                networks["private-temporal"] = {"NetworkID": "b" * 64}
            else:
                networks["selected_loopback-publication"] = {"NetworkID": "d" * 64}
            value = {"Id": identity, "Name": "/selected-owner" if role == "owner" else "/selected-relay-1",
                "Image": IMAGE, "Config": {"Labels": {"com.docker.compose.project": "selected",
                    "com.docker.compose.service": role}}, "State": {"Running": True},
                "Mounts": [], "HostConfig": {"ReadonlyRootfs": True},
                "NetworkSettings": {"Networks": networks}}
            if role == "relay":
                value["HostConfig"]["PortBindings"] = {"17900/tcp": [{"HostIp": "127.0.0.1", "HostPort": "17900"}]}
            else:
                for target, key in {"/workspace/Core": "CORE", "/workspace/control": "CONTROL",
                    "/workspace/KnowledgeHub": "VAULT", "/workspace/state": "STATE", "/workspace/runtime": "RUNTIME",
                    "/run/owner-policy": "OWNER_POLICY", "/opt/services/clients/temporal.py": "TEMPORAL_ADAPTER"}.items():
                    value["Mounts"].append({"Type": "bind", "Destination": target,
                        "Source": self.install.plist["EnvironmentVariables"]["KNOWLEDGEOS_" + key + "_SOURCE"],
                        "RW": key in {"VAULT", "STATE", "RUNTIME"}})
            self.containers[identity] = value

    def job_text(self):
        plist = self.install.plist
        text = f"job = {{\n\tpath = {self.binding['plist']}\n\tprogram = {plist['ProgramArguments'][0]}\n"
        text += "\tstate = " + ("running" if self.running else "not running") + "\n"
        if self.running:
            text += "\tpid = 1234\n"
        text += "\targuments = {\n" + "\n".join("\t\t" + arg for arg in plist["ProgramArguments"]) + "\n\t}\n"
        text += "\tenvironment = {\n" + "\n".join(f"\t\t{key} => {value}"
            for key, value in plist["EnvironmentVariables"].items()) + "\n\t}\n}"
        return text.replace("--container-name", "--foreign-container") if self.job_changed else text

    def run(self, argv, **kwargs):
        self.calls.append(argv)
        assert kwargs["timeout"] <= 5
        assert kwargs["env"]["PATH"] == "/usr/bin:/bin:/usr/sbin:/sbin"
        self.now += 0.001
        args = argv[1:]
        code, output, error = 0, "", ""
        if args[:2] == ["info", "--format"]:
            code = 0 if self.docker_available else 1
        elif args == ["compose", "version", "--short"]:
            code = 0 if self.compose_available else 1
        elif args[:2] == ["image", "inspect"]:
            output = json.dumps([{"Id": IMAGE}])
        elif args[:2] == ["network", "inspect"]:
            output = json.dumps([{"Id": "b" * 64, "Internal": True}])
        elif args[:2] == ["container", "inspect"]:
            values = {**self.temporal, **self.containers}
            if not all(identity in values for identity in args[2:]):
                code = 1
            else:
                output = json.dumps([values[identity] for identity in args[2:]])
        elif args[:2] == ["container", "ls"]:
            selected = args[args.index("--filter") + 1]
            output = "\n".join(identity for identity, value in self.containers.items()
                if selected.startswith("label=") or selected == "name=^" + value["Name"] + "$")
        elif args[:2] == ["container", "start"]:
            self.temporal[args[2]]["State"] = {"Running": True, "Health": {"Status": "healthy"}}
        elif args[0] == "print":
            if not self.registered:
                code, error = 113, "Could not find service in domain"
            else:
                output = self.job_text()
        elif args[0] == "bootstrap":
            assert not self.registered
            self.registered = True
        elif args[0] == "kickstart":
            assert args[1] == f"gui/{os.getuid()}/test.knowledgeos"
            assert not self.running
            self.running, self.kicked_at = True, self.now
            self.create_backend()
        else:
            raise AssertionError(f"Forbidden/unexpected host command: {argv}")
        return subprocess.CompletedProcess(argv, code, output, error)

    def probe(self, port, deadline, clock):
        assert port == 17900 and clock() < deadline
        if self.port_reply is not None:
            return self.port_reply
        if self.running and self.now >= (self.kicked_at or 0) + self.ready_after:
            return "reachable", None
        return "unreachable", "OWNER_UNREACHABLE"

    def operate(self, operation="start"):
        return backend.operate(self.install.path, operation, run=self.run, probe=self.probe,
                               monotonic=self.clock, sleep=self.sleep)

    def mutations(self):
        return [args for args in self.calls if args[1] in {"bootstrap", "kickstart"}
                or args[1:3] == ["container", "start"]]


def test_status_is_read_only_and_cold_start_is_owned_by_launchd(installed):
    host = Host(installed)
    before = set(installed.root.iterdir())
    report = host.operate("status")
    assert report["state"] == "unreachable" and report["owner_endpoint"] == "http://127.0.0.1:17900/owner"
    assert set(installed.root.iterdir()) == before and not host.mutations()
    report = host.operate()
    assert report == backend.result("owner_connected", "http://127.0.0.1:17900/owner", "reachable")
    assert [args[1] for args in host.mutations()] == ["bootstrap", "kickstart"]
    host.calls.clear()
    assert host.operate()["state"] == "owner_connected"
    assert not host.mutations()


def test_registered_stopped_job_is_kicked_once_without_bootstrap(installed):
    host = Host(installed)
    host.registered = True
    assert host.operate()["state"] == "owner_connected"
    assert [args[1] for args in host.mutations()] == ["kickstart"]


def test_existing_temporal_starts_db_server_client_in_order(installed):
    host = Host(installed)
    for value in host.temporal.values():
        value["State"] = {"Running": False}
    assert host.operate()["state"] == "owner_connected"
    assert [args[3] for args in host.mutations() if args[1:3] == ["container", "start"]] == [
        item["id"] for item in installed.binding["temporal"]]
    assert host.mutations()[-2][1] == "bootstrap"


@pytest.mark.parametrize("fault", ["missing", "foreign", "docker_off", "compose_missing"])
def test_dependency_failure_never_starts_anything(installed, fault):
    host = Host(installed)
    if fault == "missing":
        host.temporal.pop(installed.binding["temporal"][2]["id"])
    elif fault == "foreign":
        next(iter(host.temporal.values()))["Image"] = "sha256:" + "e" * 64
    elif fault == "docker_off":
        host.docker_available = False
    else:
        host.compose_available = False
    report = host.operate()
    assert report["state"] == "blocked" and not host.mutations()
    if fault == "docker_off":
        assert report["error"]["code"] == "DOCKER_UNAVAILABLE"


@pytest.mark.parametrize("reply,code", [(("refused", "HTTP_REFUSED"), "HTTP_REFUSED"),
    (("refused", "FOREIGN_OWNER"), "FOREIGN_OWNER"),
    (("unreachable", "RELAY_UNREACHABLE"), "PORT_CONFLICT"),
    (("reachable", None), "OWNER_JOB_MISMATCH")])
def test_foreign_http_and_port_conflict_never_create_second_writer(installed, reply, code):
    host = Host(installed)
    host.port_reply = reply
    report = host.operate()
    assert report["state"] == "blocked" and report["error"]["code"] == code
    assert not host.mutations()
    assert report["owner_http"] != "reachable"


def test_live_registered_job_configuration_mismatch_is_not_restarted(installed):
    host = Host(installed)
    host.registered = host.running = host.job_changed = True
    host.create_backend()
    report = host.operate()
    assert report["error"]["code"] == "JOB_MISMATCH" and not host.mutations()


@pytest.mark.parametrize("fault", ["image", "port", "mount", "network", "orphan"])
def test_backend_identity_is_not_inferred_from_http_port(installed, fault):
    host = Host(installed)
    host.registered = host.running = True
    host.create_backend()
    owner, relay = host.containers["4" * 64], host.containers["5" * 64]
    if fault == "image":
        owner["Image"] = "sha256:" + "f" * 64
    elif fault == "port":
        relay["HostConfig"]["PortBindings"]["17900/tcp"][0]["HostIp"] = "0.0.0.0"
    elif fault == "mount":
        owner["Mounts"][0]["RW"] = True
    elif fault == "network":
        relay["NetworkSettings"]["Networks"]["selected_owner-private"]["NetworkID"] = "e" * 64
    else:
        host.running = False
    assert host.operate()["state"] == "blocked"
    assert not host.mutations()


def test_selected_relay_502_waits_for_live_job_without_kickstart(installed):
    host = Host(installed)
    host.registered = host.running = True
    host.create_backend()
    calls = 0
    def probe(*_args):
        nonlocal calls
        calls += 1
        return ("unreachable", "RELAY_UNREACHABLE") if calls < 3 else ("reachable", None)
    host.probe = probe
    assert host.operate()["state"] == "owner_connected" and calls == 3
    assert not host.mutations()


@pytest.mark.parametrize("ready_after,expected", [(1, "owner_connected"), (61, "failed")])
def test_registered_process_during_launchd_transition_is_observed_without_kickstart(
        installed, ready_after, expected):
    host = Host(installed)
    host.registered = host.running = True
    host.ready_after = ready_after
    host.create_backend()
    original_text = host.job_text

    def transitioning_text():
        text = original_text()
        return text.replace("state = running", "state = spawned") if host.now < ready_after else text

    host.job_text = transitioning_text
    report = host.operate()
    assert report["state"] == expected
    if expected == "failed":
        assert report["error"]["code"] == "STARTUP_TIMEOUT"
    assert not host.mutations()
    assert host.running


@pytest.mark.parametrize("pid", ["0", "-1", "foreign"])
def test_invalid_registered_pid_cannot_authorize_connection_or_restart(installed, pid):
    host = Host(installed)
    host.registered = host.running = True
    host.create_backend()
    original_text = host.job_text
    host.job_text = lambda: original_text().replace("pid = 1234", "pid = " + pid)
    report = host.operate()
    assert report["state"] == "blocked" and report["error"]["code"] == "JOB_MISMATCH"
    assert not host.mutations()


@pytest.mark.parametrize("delay,expected", [(59, "owner_connected"), (61, "failed")])
def test_start_has_one_absolute_deadline_and_late_start_is_not_restarted(installed, delay, expected):
    host = Host(installed)
    host.ready_after = delay
    report = host.operate()
    assert report["state"] == expected
    assert len([args for args in host.mutations() if args[1] == "kickstart"]) == 1
    if expected == "failed":
        assert report["error"]["code"] == "STARTUP_TIMEOUT" and host.running
        assert host.now == pytest.approx(60)
        # A later read-only observation reuses the independent job, never restarts it.
        host.sleep(2)
        assert host.operate("status")["state"] == "owner_connected"


def test_response_arriving_after_deadline_cannot_claim_connected(installed):
    host = Host(installed)
    original = host.probe
    def late_probe(*args):
        if host.running:
            host.now += 61
            return "reachable", None
        return original(*args)
    host.probe = late_probe
    report = host.operate()
    assert report["state"] == "failed" and report["owner_http"] == "unavailable"
    assert host.running


@pytest.mark.parametrize("fault", ["mode", "symlink", "parent_mode", "autostart", "compose", "interpreter"])
def test_private_installation_validation_precedes_host_commands(installed, fault):
    if fault == "mode":
        installed.path.chmod(0o644)
    elif fault == "symlink":
        original = installed.path.with_suffix(".original")
        installed.path.rename(original)
        installed.path.symlink_to(original)
    elif fault == "parent_mode":
        installed.root.chmod(0o755)
    elif fault == "compose":
        installed.compose.write_text("services: {foreign: {}}")
    else:
        plist = copy.deepcopy(installed.plist)
        if fault == "autostart":
            plist["RunAtLoad"] = True
        else:
            plist["ProgramArguments"][0] = str(installed.root / "missing-python")
        private_write(Path(installed.binding["plist"]), plistlib.dumps(plist))
        installed.binding["plist_sha256"] = hashlib.sha256(Path(installed.binding["plist"]).read_bytes()).hexdigest()
        private_write(installed.path, json.dumps(installed.binding).encode())
    host = Host(installed)
    assert host.operate()["state"] == "blocked" and host.calls == []


def test_real_process_lock_reports_starting_without_host_mutations(installed):
    lock_path = installed.path.with_name("local-backend.lock")
    code = ("import fcntl,os,sys; "
            "fd=os.open(sys.argv[1],os.O_CREAT|os.O_RDWR,0o600); "
            "fcntl.flock(fd,fcntl.LOCK_EX); print('locked',flush=True); sys.stdin.readline()")
    process = subprocess.Popen([sys.executable, "-c", code, str(lock_path)],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        assert process.stdout.readline() == "locked\n"
        host = Host(installed)
        report = host.operate()
        assert report == backend.result("starting", "http://127.0.0.1:17900/owner")
        assert host.calls == []
    finally:
        process.communicate("release\n", timeout=5)
    assert process.returncode == 0
    assert Host(installed).operate()["state"] == "owner_connected"


@contextmanager
def http_owner(status=200, value=STATUS, *, content_type="application/json", raw=None):
    requests = []
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            requests.append((self.path, self.rfile.read(int(self.headers["Content-Length"])), dict(self.headers)))
            payload = json.dumps(value).encode() if raw is None else raw
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
        def log_message(self, *_args):
            pass
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server.server_port, requests
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@pytest.mark.parametrize("status,value,expected", [(200, STATUS, ("reachable", None)),
    (403, {}, ("refused", "HTTP_REFUSED")), (502, {}, ("unreachable", "RELAY_UNREACHABLE")),
    (200, {**STATUS, "owner_operation_id": "foreign"}, ("refused", "FOREIGN_OWNER")),
    (200, {**STATUS, "read_only": True}, ("refused", "FOREIGN_OWNER")),
    (302, {}, ("refused", "PORT_CONFLICT"))])
def test_actual_http_classifies_refused_foreign_and_redirect_responses(status, value, expected):
    with http_owner(status, value) as (port, _):
        assert backend.http_status(port, time.monotonic() + 3) == expected


def test_actual_http_payload_bound_and_non_json_are_refused():
    with http_owner(raw=b"x" * (backend.MAX_JSON + 1)) as (port, _):
        assert backend.http_status(port, time.monotonic() + 3) == ("refused", "INVALID_OWNER_RESPONSE")
    with http_owner(content_type="text/html") as (port, _):
        assert backend.http_status(port, time.monotonic() + 3) == ("refused", "PORT_CONFLICT")


def test_connection_closed_before_status_is_unavailable_not_an_owner_refusal():
    class EarlyClose(socketserver.BaseRequestHandler):
        def handle(self):
            self.request.recv(16384)

    server = socketserver.TCPServer(("127.0.0.1", 0), EarlyClose)
    thread = threading.Thread(target=server.handle_request, daemon=True)
    thread.start()
    try:
        assert backend.http_status(server.server_address[1], time.monotonic() + 5) == (
            "unavailable", "OWNER_TRANSPORT_UNAVAILABLE")
    finally:
        server.server_close()
        thread.join(timeout=5)


def test_actual_owner_adapter_is_reached_without_treating_read_only_as_writable(tmp_path):
    from support.control_factory import make_separate_portable_fixture_roots

    from vaultops.interfaces.loopback_owner import create_loopback_owner
    from vaultops.interfaces.read_only_owner import compose_read_only_owner
    from vaultops.projection import generate_projection

    roots = make_separate_portable_fixture_roots(
        tmp_path, extra_inputs=("ops/config/team-catalog.json",))
    generate_projection(roots)
    exops = compose_read_only_owner(roots, actor_id="owner.user")
    server = create_loopback_owner(exops, actor_id="owner.user", port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    before = {str(path): path.read_bytes() for area in ("vault", "state", "runtime")
              for path in getattr(roots, area).rglob("*") if path.is_file()}
    thread.start()
    try:
        assert backend.http_status(server.server_port, time.monotonic() + 5) == ("refused", "FOREIGN_OWNER")
        assert {str(path): path.read_bytes() for area in ("vault", "state", "runtime")
                for path in getattr(roots, area).rglob("*") if path.is_file()} == before
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@pytest.mark.parametrize("argv", [["stop", "--json"], ["start"], ["status", "--json", "--binding", "/tmp/foreign"]])
def test_cli_does_not_accept_roots_commands_or_arbitrary_arguments(installed, capsys, argv):
    assert backend.main(binding_path=installed.path, argv=argv) == 1
    report = json.loads(capsys.readouterr().out)
    assert set(report) == {"version", "operation", "state", "owner_endpoint", "owner_http", "runtime_observation", "error"}
    assert report["error"]["code"] == "INVALID_ARGUMENTS"
    assert not installed.path.with_name("local-backend.lock").exists()


def test_new_session_recreates_only_transient_backend_resources(installed):
    host = Host(installed)
    assert host.operate()["state"] == "owner_connected"
    original_ids = set(host.containers)
    # The independently owned native driver removes transient backend resources
    # on exit. Persistent Temporal identities and installation pins are retained.
    host.containers.clear()
    host.running = False
    host.calls.clear()
    assert host.operate("status")["state"] == "unreachable"
    assert not host.mutations()
    create_backend = host.create_backend
    def recreate_backend():
        create_backend()
        host.containers = {("e" if index == 0 else "f") * 64: {
            **value, "Id": ("e" if index == 0 else "f") * 64}
            for index, value in enumerate(host.containers.values())}
    host.create_backend = recreate_backend
    assert host.operate()["state"] == "owner_connected"
    assert set(host.containers).isdisjoint(original_ids)
    assert [args[1] for args in host.mutations()] == ["kickstart"]


def test_unhealthy_temporal_does_not_start_later_dependencies_or_owner(installed):
    host = Host(installed)
    postgres, server, client = installed.binding["temporal"]
    host.temporal[postgres["id"]]["State"]["Health"]["Status"] = "unhealthy"
    host.temporal[server["id"]]["State"] = {"Running": False}
    host.temporal[client["id"]]["State"] = {"Running": False}
    assert host.operate()["error"]["code"] == "TEMPORAL_UNHEALTHY"
    assert not host.mutations()


def test_later_temporal_dependencies_cannot_reset_the_shared_start_deadline(installed):
    host = Host(installed)
    postgres, server, client = installed.binding["temporal"]
    for item in (postgres, server, client):
        host.temporal[item["id"]]["State"] = {"Running": False}
    original_run = host.run

    def run(argv, **kwargs):
        completed = original_run(argv, **kwargs)
        if argv[1:3] == ["container", "start"]:
            host.temporal[argv[3]]["State"]["Health"]["Status"] = "starting"
        return completed

    def sleep(duration):
        host.now += duration
        if host.now >= 40:
            host.temporal[postgres["id"]]["State"]["Health"]["Status"] = "healthy"
        if host.now >= 80:
            host.temporal[server["id"]]["State"]["Health"]["Status"] = "healthy"

    host.run, host.sleep = run, sleep
    report = host.operate()
    assert report["error"]["code"] == "STARTUP_TIMEOUT"
    assert host.temporal[postgres["id"]]["State"]["Running"] is True
    assert host.temporal[server["id"]]["State"]["Running"] is True
    assert host.temporal[client["id"]]["State"]["Running"] is False
    assert not host.registered and not host.running


def test_missing_binding_status_does_not_create_installation(tmp_path):
    path = tmp_path / "absent-runtime" / "binding.json"
    report = backend.operate(path, "status")
    assert report["state"] == "blocked"
    assert not path.parent.exists()


def test_status_of_exact_stopped_temporal_allows_explicit_start(installed):
    host = Host(installed)
    for value in host.temporal.values():
        value["State"] = {"Running": False}
    report = host.operate("status")
    assert report["state"] == "unreachable"
    assert report["owner_endpoint"] == "http://127.0.0.1:17900/owner"
    assert not host.mutations()
    assert not installed.path.with_name("local-backend.lock").exists()
    assert host.operate()["state"] == "owner_connected"


def test_symlink_start_lock_is_refused_without_touching_target(installed):
    target = installed.root / "unrelated-private-file"
    private_write(target, b"unchanged")
    installed.path.with_name("local-backend.lock").symlink_to(target)
    host = Host(installed)
    assert host.operate()["state"] == "blocked"
    assert target.read_bytes() == b"unchanged" and not host.calls

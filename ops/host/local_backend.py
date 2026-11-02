"""Private, on-demand macOS launcher for one already installed KnowledgeOS backend.

The installed shim supplies binding_path; the public interface has no root,
command, environment, repair, restart or stop options. launchd owns the driver.
"""
from __future__ import annotations

import fcntl
import hashlib
import http.client
import json
import os
import plistlib
import re
import stat
import subprocess
import sys
import time
from pathlib import Path

MAX_JSON = 65536
LAUNCHCTL = "/bin/launchctl"


class Refused(ValueError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


class Deadline(TimeoutError):
    pass


def require(condition, code, message):
    if not condition:
        raise Refused(code, message)


def remaining(deadline, clock):
    value = deadline - clock()
    if value <= 0:
        raise Deadline()
    return value


def regular(path, *, private=False, executable=False):
    """Reject symlink components, not only a symlink at the final filename."""
    path = Path(path)
    require(path.is_absolute(), "INVALID_BINDING", "Selected paths must be absolute")
    for component in (path, *path.parents):
        require(not component.is_symlink(), "UNSAFE_INSTALL", "Selected path contains a symlink")
    try:
        metadata = path.stat(follow_symlinks=False)
    except OSError as error:
        raise Refused("MISSING_INSTALL", "A selected installation file is unavailable") from error
    require(stat.S_ISREG(metadata.st_mode), "UNSAFE_INSTALL", "Selected file is not regular")
    require(metadata.st_uid == os.getuid() and not metadata.st_mode & 0o022,
            "UNSAFE_INSTALL", "Selected file ownership or permissions are unsafe")
    if private:
        require(stat.S_IMODE(metadata.st_mode) == 0o600, "UNSAFE_INSTALL",
                "Private installation files must have mode 0600")
    if executable:
        require(os.access(path, os.X_OK), "MISSING_DEPENDENCY", "Selected executable is unavailable")
    return path


def private_read(path):
    path = regular(path, private=True)
    require(path.stat().st_size <= MAX_JSON, "INVALID_BINDING", "Private file exceeds its bound")
    # O_NOFOLLOW also closes a final-component replacement race.
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(descriptor, "rb") as stream:
        metadata = os.fstat(stream.fileno())
        require(stat.S_ISREG(metadata.st_mode) and metadata.st_uid == os.getuid()
                and stat.S_IMODE(metadata.st_mode) == 0o600,
                "UNSAFE_INSTALL", "Private file changed during open")
        raw = stream.read(MAX_JSON + 1)
    require(len(raw) <= MAX_JSON, "INVALID_BINDING", "Private file exceeds its bound")
    return raw


def load_binding(path):
    path = Path(path)
    raw = private_read(path)
    parent = path.parent.stat(follow_symlinks=False)
    require(parent.st_uid == os.getuid() and stat.S_IMODE(parent.st_mode) == 0o700,
            "UNSAFE_INSTALL", "Binding directory must be owner-only mode 0700")
    binding = json.loads(raw)
    keys = {"version", "uid", "plist", "plist_sha256", "compose_sha256", "label", "docker",
            "project", "owner_container", "owner_image", "relay", "port", "temporal_network", "temporal"}
    require(isinstance(binding, dict) and set(binding) == keys and binding["version"] == 1
            and binding["uid"] == os.getuid(), "INVALID_BINDING", "Unsupported private binding")
    require(type(binding["port"]) is int and 1024 <= binding["port"] <= 65535,
            "INVALID_BINDING", "Invalid selected loopback port")
    for key in ("label", "project", "owner_container"):
        require(isinstance(binding[key], str) and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", binding[key]),
                "INVALID_BINDING", "Invalid selected logical identity")
    for key in ("plist_sha256", "compose_sha256"):
        require(isinstance(binding[key], str) and re.fullmatch(r"[0-9a-f]{64}", binding[key]),
                "INVALID_BINDING", "Invalid selected installation digest")
    require(isinstance(binding["relay"], dict) and set(binding["relay"]) == {"name", "image"}
            and isinstance(binding["temporal_network"], dict)
            and set(binding["temporal_network"]) == {"name", "id"}
            and isinstance(binding["temporal"], list) and len(binding["temporal"]) == 3,
            "INVALID_BINDING", "Missing selected existing service identities")
    for name in (binding["relay"]["name"], binding["temporal_network"]["name"],
                 *(item.get("name") for item in binding["temporal"] if isinstance(item, dict))):
        require(isinstance(name, str) and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", name),
                "INVALID_BINDING", "Invalid selected service name")
    require(isinstance(binding["temporal_network"]["id"], str)
            and re.fullmatch(r"[0-9a-f]{64}", binding["temporal_network"]["id"]),
            "INVALID_BINDING", "Temporal network ID must be exact")
    for item, service in zip(binding["temporal"], ("postgres", "server", "client"), strict=True):
        require(isinstance(item, dict) and set(item) == {"name", "id", "image", "project", "service", "health"}
                and item["service"] == service and item["health"] == "healthy",
                "INVALID_BINDING", "Temporal must select existing postgres, server and client in order")
        require(re.fullmatch(r"[0-9a-f]{64}", item["id"]) is not None,
                "INVALID_BINDING", "Temporal container ID must be exact")
    for image in [binding["owner_image"], binding["relay"]["image"],
                  *(item["image"] for item in binding["temporal"])]:
        require(isinstance(image, str) and re.fullmatch(r"sha256:[0-9a-f]{64}", image),
                "INVALID_BINDING", "Images must select exact installed identities")
    require(binding["owner_image"] == binding["relay"]["image"], "INVALID_BINDING",
            "Owner and relay must select the same installed Team image")
    plist_path = regular(binding["plist"], private=True)
    require("LaunchAgents" not in plist_path.parts, "AUTOSTART_REFUSED", "Login autostart is not supported")
    plist_raw = private_read(plist_path)
    require(hashlib.sha256(plist_raw).hexdigest() == binding["plist_sha256"],
            "INSTALL_CHANGED", "Selected launch configuration changed")
    plist = plistlib.loads(plist_raw)
    require(plist.get("Label") == binding["label"] and plist.get("RunAtLoad") is False
            and plist.get("KeepAlive") is False and set(plist) <= {
                "Label", "ProgramArguments", "EnvironmentVariables", "RunAtLoad", "KeepAlive",
                "WorkingDirectory", "StandardErrorPath", "StandardOutPath", "Umask", "ExitTimeOut"},
            "AUTOSTART_REFUSED", "Only the selected manual non-autostart job is permitted")
    args, environment = plist.get("ProgramArguments"), plist.get("EnvironmentVariables")
    require(isinstance(args, list) and len(args) >= 4 and isinstance(environment, dict)
            and all(isinstance(arg, str) for arg in args), "INVALID_BINDING", "Invalid driver configuration")
    # Python installations commonly use executable symlinks; preserve their venv prefix.
    require(Path(args[0]).is_absolute() and Path(args[0]).is_file() and os.access(args[0], os.X_OK),
            "MISSING_DEPENDENCY", "Selected Python interpreter is unavailable")
    regular(args[1])
    require(Path(args[1]).name == "native_owner_driver.py", "INVALID_BINDING", "Unexpected selected driver")
    require(len(args[2:]) % 2 == 0, "INVALID_BINDING", "Unexpected driver arguments")
    options = dict(zip(args[2::2], args[3::2], strict=True))
    require(len(options) * 2 == len(args[2:]) and set(options) <= {
        "--policy-file", "--compose-file", "--project", "--container-name", "--adapters-directory",
        "--hermes-python", "--hermes-root", "--profile-home", "--docker-command", "--local-embedding", "--interval"},
        "INVALID_BINDING", "Unexpected driver mode or duplicate argument")
    require({"--policy-file", "--compose-file", "--project", "--container-name", "--adapters-directory",
             "--hermes-python", "--hermes-root", "--profile-home", "--docker-command"} <= set(options),
            "INVALID_BINDING", "Selected native driver prerequisites are incomplete")
    interpreter = Path(options["--hermes-python"])
    require(interpreter.is_absolute() and interpreter.is_file() and os.access(interpreter, os.X_OK),
            "MISSING_DEPENDENCY", "Selected native Runner interpreter is unavailable")
    for key in ("--adapters-directory", "--hermes-root", "--profile-home"):
        selected = Path(options[key])
        require(selected.is_absolute() and selected.is_dir(), "MISSING_INSTALL",
                "Selected native driver directory is unavailable")
    require(options.get("--project") == binding["project"]
            and options.get("--container-name") == binding["owner_container"]
            and options.get("--docker-command") == binding["docker"]
            and environment.get("KNOWLEDGEOS_OWNER_PORT") == str(binding["port"])
            and environment.get("KNOWLEDGEOS_TEAM_IMAGE") == binding["owner_image"]
            and environment.get("KNOWLEDGEOS_OWNER_CONTAINER") == binding["owner_container"]
            and environment.get("KNOWLEDGEOS_TEMPORAL_NETWORK") == binding["temporal_network"]["name"],
            "INVALID_BINDING", "Driver does not select the bound backend identities")
    compose = regular(options["--compose-file"])
    require(hashlib.sha256(compose.read_bytes()).hexdigest() == binding["compose_sha256"],
            "INSTALL_CHANGED", "Selected compose source changed")
    private_read(options["--policy-file"])
    # Docker installed by Homebrew is commonly a symlink; select its exact absolute launcher.
    require(Path(binding["docker"]).is_absolute() and Path(binding["docker"]).is_file()
            and os.access(binding["docker"], os.X_OK), "MISSING_DEPENDENCY", "Selected Docker executable is unavailable")
    require(Path(LAUNCHCTL).is_file() and os.access(LAUNCHCTL, os.X_OK),
            "MISSING_DEPENDENCY", "macOS launchctl is unavailable")
    binding["_plist"], binding["_options"] = plist, options
    return binding


def http_status(port, deadline, clock=time.monotonic):
    """Read-only domain POST, no proxy, redirect, cookies, credentials or retries."""
    connection = http.client.HTTPConnection("127.0.0.1", port,
                                            timeout=min(2.0, remaining(deadline, clock)))
    try:
        connection.request("POST", "/owner/owner/status", body=b"{}",
                           headers={"Content-Type": "application/json", "Connection": "close"})
        transport = connection.sock
        response = connection.getresponse()
        if response.status == 403:
            return "refused", "HTTP_REFUSED"
        if response.status == 502:
            return "unreachable", "RELAY_UNREACHABLE"
        if response.status != 200:
            return "refused", "PORT_CONFLICT"
        if response.getheader("Content-Type", "").split(";", 1)[0].strip() != "application/json":
            return "refused", "PORT_CONFLICT"
        data = bytearray()
        while True:
            transport.settimeout(min(2.0, remaining(deadline, clock)))
            chunk = response.read1(min(8192, MAX_JSON + 1 - len(data)))
            remaining(deadline, clock)
            if not chunk:
                break
            data.extend(chunk)
            if len(data) > MAX_JSON:
                return "refused", "INVALID_OWNER_RESPONSE"
            if response.isclosed():
                break
        value = json.loads(data)
        valid = (isinstance(value, dict) and value.get("owner_operation_id") == "knowledgeos"
                 and value.get("read_only") is False and type(value.get("generation")) is int
                 and type(value.get("revision")) is int and isinstance(value.get("bindings"), dict)
                 and value.get("runtime_observation") == "unavailable")
        return ("reachable", None) if valid else ("refused", "FOREIGN_OWNER")
    except ConnectionRefusedError:
        return "unreachable", "OWNER_UNREACHABLE"
    except TimeoutError:
        remaining(deadline, clock)
        return "unavailable", "OWNER_TIMEOUT"
    except ConnectionError:
        remaining(deadline, clock)
        return "unavailable", "OWNER_TRANSPORT_UNAVAILABLE"
    except (OSError, ValueError, http.client.HTTPException):
        return "refused", "INVALID_OWNER_RESPONSE"
    finally:
        connection.close()


def result(state, endpoint=None, owner_http="unavailable", code=None, message=None):
    return {"version": 1, "operation": "knowledgeos", "state": state,
            "owner_endpoint": endpoint, "owner_http": owner_http,
            "runtime_observation": "unavailable",
            "error": None if code is None else {"code": code, "message": message}}


def operate(binding_path, operation, *, run=subprocess.run, probe=http_status,
            monotonic=time.monotonic, sleep=time.sleep):
    """Return the public JSON value; only start acquires a lock or changes services."""
    deadline = monotonic() + (60 if operation == "start" else 12)
    endpoint, observation, lock = None, "unavailable", None

    def command(argv, *, missing_ok=False):
        completed = run(argv, capture_output=True, text=True,
                        timeout=min(5.0, remaining(deadline, monotonic)),
                        env={"PATH": "/usr/bin:/bin:/usr/sbin:/sbin", "HOME": str(Path.home()),
                             "DOCKER_CONFIG": str(Path.home() / ".docker")})
        remaining(deadline, monotonic)
        require(len(completed.stdout) <= 2 * 1024 * 1024 and len(completed.stderr) <= MAX_JSON,
                "INSPECTION_TOO_LARGE", "Host inspection exceeded its bound")
        if completed.returncode and not missing_ok:
            raise Refused("HOST_COMMAND_FAILED", "Selected host command failed; no repair was attempted")
        return completed

    try:
        require(operation in {"start", "status"}, "INVALID_ARGUMENTS", "Only start --json and status --json are supported")
        binding = load_binding(binding_path)
        endpoint = f"http://127.0.0.1:{binding['port']}/owner"
        docker, job = binding["docker"], f"gui/{binding['uid']}/{binding['label']}"
        if operation == "start":
            lock_path = Path(binding_path).with_name("local-backend.lock")
            descriptor = os.open(lock_path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
            lock = os.fdopen(descriptor, "r+")
            metadata = os.fstat(descriptor)
            require(stat.S_ISREG(metadata.st_mode) and metadata.st_uid == os.getuid()
                    and stat.S_IMODE(metadata.st_mode) == 0o600,
                    "UNSAFE_LOCK", "Start lock must be a private owner regular file")
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return result("starting", endpoint)
        available = command([docker, "info", "--format", "{{json .ServerVersion}}"], missing_ok=True)
        require(available.returncode == 0, "DOCKER_UNAVAILABLE", "Selected Docker VM is unavailable; start it separately")
        compose_available = command([docker, "compose", "version", "--short"], missing_ok=True)
        require(compose_available.returncode == 0, "MISSING_DEPENDENCY",
                "Selected Docker Compose plugin is unavailable; install it separately")
        image_response = command([docker, "image", "inspect", binding["owner_image"]], missing_ok=True)
        require(image_response.returncode == 0, "MISSING_DEPENDENCY",
                "Selected installed Team image is absent; no pull or build is permitted")
        images = json.loads(image_response.stdout)
        require(len(images) == 1 and images[0].get("Id") == binding["owner_image"],
                "MISSING_DEPENDENCY", "Selected installed Team image is unavailable; no pull or build is permitted")
        network_response = command([docker, "network", "inspect", binding["temporal_network"]["name"]], missing_ok=True)
        require(network_response.returncode == 0, "MISSING_TEMPORAL", "Selected existing Temporal network is absent")
        network = json.loads(network_response.stdout)
        require(len(network) == 1 and network[0].get("Id") == binding["temporal_network"]["id"]
                and network[0].get("Internal") is True,
                "FOREIGN_TEMPORAL", "Selected private Temporal network differs")

        def inspect_temporal(item):
            response = command([docker, "container", "inspect", item["id"]], missing_ok=True)
            require(response.returncode == 0, "MISSING_TEMPORAL", "Selected existing Temporal container is absent")
            values = json.loads(response.stdout)
            require(len(values) == 1, "MISSING_TEMPORAL", "Selected existing Temporal container is absent")
            value = values[0]
            labels = value.get("Config", {}).get("Labels") or {}
            networks = value.get("NetworkSettings", {}).get("Networks", {})
            require(value.get("Id") == item["id"] and value.get("Name") == "/" + item["name"]
                    and value.get("Image") == item["image"]
                    and labels.get("com.docker.compose.project") == item["project"]
                    and labels.get("com.docker.compose.service") == item["service"]
                    and networks.get(binding["temporal_network"]["name"], {}).get("NetworkID") == binding["temporal_network"]["id"],
                    "FOREIGN_TEMPORAL", "Existing Temporal container identity differs")
            return value

        # Validate all prerequisites before starting any dependency.
        temporal = [(item, inspect_temporal(item)) for item in binding["temporal"]]

        def inspect_job():
            response = command([LAUNCHCTL, "print", job], missing_ok=True)
            if response.returncode:
                require("Could not find service" in response.stderr
                        or "Could not find specified service" in response.stderr,
                        "LAUNCHD_UNAVAILABLE", "Cannot inspect the selected GUI launchd domain")
                return False, False
            text = response.stdout
            fields = {}
            for key in ("path", "program", "pid"):
                match = re.search(r"^\s*" + key + r" = (.+)$", text, re.MULTILINE)
                fields[key] = match.group(1).strip().strip('"') if match else None
            arguments = re.search(r"\n\s*arguments = \{\n(.*?)\n\s*\}", text, re.DOTALL)
            parsed_args = [] if arguments is None else [line.strip().strip('"')
                                                       for line in arguments.group(1).splitlines() if line.strip()]
            env = re.search(r"\n\s*environment = \{\n(.*?)\n\s*\}", text, re.DOTALL)
            parsed_env = {} if env is None else dict(re.findall(r"^\s*(\S+) => (.*)$", env.group(1), re.MULTILINE))
            expected = binding["_plist"]
            require(fields["path"] == binding["plist"] and fields["program"] == expected["ProgramArguments"][0]
                    and parsed_args == expected["ProgramArguments"]
                    and all(parsed_env.get(key) == str(value) for key, value in expected["EnvironmentVariables"].items()),
                    "JOB_MISMATCH", "Registered job configuration differs; no second writer will be started")
            pid = fields["pid"]
            require(pid is None or pid.isdecimal() and int(pid) > 0,
                    "JOB_MISMATCH", "Registered job PID is invalid")
            # launchd may expose a PID before its display state becomes running.
            # Its presence fences another kickstart; HTTP proves readiness below.
            return True, pid is not None

        def inspect_backend():
            ids = set()
            filters = ["label=com.docker.compose.project=" + binding["project"],
                       "name=^/" + binding["owner_container"] + "$",
                       "name=^/" + binding["relay"]["name"] + "$"]
            for selected in filters:
                ids.update(command([docker, "container", "ls", "-a", "--no-trunc", "--filter", selected,
                                    "--format", "{{.ID}}"]).stdout.split())
            values = [] if not ids else json.loads(command([docker, "container", "inspect", *sorted(ids)]).stdout)
            found = {}
            for value in values:
                name = value.get("Name", "").removeprefix("/")
                role = "owner" if name == binding["owner_container"] else "relay"
                expected_name = binding["owner_container"] if role == "owner" else binding["relay"]["name"]
                labels = value.get("Config", {}).get("Labels") or {}
                require(name == expected_name and role not in found
                        and value.get("Image") == binding["owner_image"]
                        and labels.get("com.docker.compose.project") == binding["project"]
                        and labels.get("com.docker.compose.service") == role,
                        "FOREIGN_BACKEND", "Unexpected backend container identity; no second writer is permitted")
                require(value.get("HostConfig", {}).get("ReadonlyRootfs") is True,
                        "FOREIGN_BACKEND", "Selected backend must retain a read-only root filesystem")
                networks = value.get("NetworkSettings", {}).get("Networks", {})
                private_name = binding["project"] + "_owner-private"
                expected_networks = {private_name, binding["temporal_network"]["name"]} if role == "owner" else {
                    private_name, binding["project"] + "_loopback-publication"}
                require(set(networks) == expected_networks and networks[private_name].get("NetworkID"),
                        "FOREIGN_BACKEND", "Backend network selection differs")
                if role == "owner":
                    require(networks[binding["temporal_network"]["name"]].get("NetworkID") == binding["temporal_network"]["id"],
                            "FOREIGN_BACKEND", "Owner Temporal network identity differs")
                if role == "relay":
                    ports = value.get("HostConfig", {}).get("PortBindings")
                    require(ports == {"17900/tcp": [{"HostIp": "127.0.0.1", "HostPort": str(binding["port"])}]}
                            and not value.get("Mounts"), "FOREIGN_BACKEND", "Relay publication or mounts differ")
                else:
                    expected_mounts = {"/workspace/Core": "KNOWLEDGEOS_CORE_SOURCE",
                        "/workspace/control": "KNOWLEDGEOS_CONTROL_SOURCE", "/workspace/KnowledgeHub": "KNOWLEDGEOS_VAULT_SOURCE",
                        "/workspace/state": "KNOWLEDGEOS_STATE_SOURCE", "/workspace/runtime": "KNOWLEDGEOS_RUNTIME_SOURCE",
                        "/run/owner-policy": "KNOWLEDGEOS_OWNER_POLICY_SOURCE",
                        "/opt/services/clients/temporal.py": "KNOWLEDGEOS_TEMPORAL_ADAPTER_SOURCE"}
                    mounts = {item["Destination"]: item for item in value.get("Mounts", []) if item.get("Type") != "tmpfs"}
                    require(set(mounts) == set(expected_mounts)
                            and all(mounts[target].get("Source") == binding["_plist"]["EnvironmentVariables"].get(key)
                                    for target, key in expected_mounts.items()),
                            "FOREIGN_BACKEND", "Owner root mounts differ from the selected installation")
                    writable = {"/workspace/KnowledgeHub", "/workspace/state", "/workspace/runtime"}
                    require(all(mounts[target].get("RW") is (target in writable) for target in expected_mounts),
                            "FOREIGN_BACKEND", "Owner mount access differs from the selected installation")
                found[role] = value
            if set(found) == {"owner", "relay"}:
                private_name = binding["project"] + "_owner-private"
                require(found["owner"]["NetworkSettings"]["Networks"][private_name]["NetworkID"]
                        == found["relay"]["NetworkSettings"]["Networks"][private_name]["NetworkID"],
                        "FOREIGN_BACKEND", "Owner and relay private networks differ")
            return found

        registered, running = inspect_job()
        backend = inspect_backend()
        observation, code = probe(binding["port"], deadline, monotonic)
        remaining(deadline, monotonic)

        def connected():
            return (running and set(backend) == {"owner", "relay"}
                    and all(value.get("State", {}).get("Running") is True for value in backend.values()))

        if observation == "refused":
            raise Refused(code, "Selected Owner HTTP endpoint refused or returned a foreign response")
        if observation == "reachable":
            require(connected(), "OWNER_JOB_MISMATCH", "HTTP is reachable without the exact selected running job and containers")
            return result("owner_connected", endpoint, observation)
        if code == "RELAY_UNREACHABLE":
            require("relay" in backend and backend["relay"].get("State", {}).get("Running") is True,
                    "PORT_CONFLICT", "An unverified listener occupies the selected port")
        if not running:
            require("owner" not in backend, "OWNER_JOB_MISMATCH",
                    "An orphaned Owner container exists; no second writer or repair is permitted")
        if operation == "status":
            return result("unreachable", endpoint, observation, code or "OWNER_UNREACHABLE",
                          "Selected Owner is not connected; Worker health is not observed")
        for item, value in temporal:
            if value.get("State", {}).get("Running") is not True:
                command([docker, "container", "start", item["id"]])
            while True:
                current = inspect_temporal(item)
                state = current.get("State", {})
                require(state.get("Running") is True and not state.get("Paused") and not state.get("Restarting"),
                        "TEMPORAL_STOPPED", "Selected Temporal dependency did not remain running")
                if state.get("Health", {}).get("Status") == "healthy":
                    break
                require(state.get("Health", {}).get("Status") == "starting", "TEMPORAL_UNHEALTHY",
                        "Selected Temporal dependency is unhealthy or lacks its installed health check")
                sleep(min(0.25, remaining(deadline, monotonic)))
        if not registered:
            command([LAUNCHCTL, "bootstrap", f"gui/{binding['uid']}", binding["plist"]])
        # Bootstrap with RunAtLoad=false registers only. Never kick a running driver.
        registered, running = inspect_job()
        require(registered, "JOB_MISSING", "Selected launchd job was not registered")
        if not running:
            command([LAUNCHCTL, "kickstart", job])
        while True:
            registered, running = inspect_job()
            require(registered, "JOB_MISSING", "Selected launchd job disappeared during startup")
            backend = inspect_backend()
            observation, code = probe(binding["port"], deadline, monotonic)
            remaining(deadline, monotonic)
            if observation == "refused":
                raise Refused(code, "Selected Owner refused HTTP during startup; no restart was attempted")
            if observation == "reachable":
                require(connected(), "OWNER_JOB_MISMATCH", "HTTP does not match the selected running job and containers")
                return result("owner_connected", endpoint, observation)
            if code == "RELAY_UNREACHABLE":
                require("relay" in backend and backend["relay"].get("State", {}).get("Running") is True,
                        "PORT_CONFLICT", "An unverified listener occupies the selected port")
            sleep(min(0.25, remaining(deadline, monotonic)))
    except (Deadline, subprocess.TimeoutExpired):
        return result("failed" if operation == "start" else "unreachable", endpoint,
                      "unavailable" if observation == "reachable" else observation,
                      "STARTUP_TIMEOUT" if operation == "start" else "STATUS_TIMEOUT",
                      "Observation deadline expired; the independently owned backend was not stopped or restarted")
    except Refused as error:
        return result("blocked", endpoint, "refused" if observation == "reachable" else observation,
                      error.code, str(error))
    except (OSError, ValueError, KeyError, TypeError, AttributeError, plistlib.InvalidFileException):
        return result("blocked", endpoint, observation, "INVALID_INSTALL", "Selected private installation is missing, malformed or unavailable")
    finally:
        if lock is not None:
            lock.close()


def main(*, binding_path, argv=None):
    arguments = sys.argv[1:] if argv is None else argv
    if arguments not in (["start", "--json"], ["status", "--json"]):
        value = result("blocked", code="INVALID_ARGUMENTS", message="Only start --json and status --json are supported")
    else:
        value = operate(binding_path, arguments[0])
    print(json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False))
    return 0 if value["state"] in {"owner_connected", "starting"} else 1

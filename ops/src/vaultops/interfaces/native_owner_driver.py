"""Host-owned native stdio driver for the explicitly selected installed Owner.

This host entrypoint deliberately uses only the standard library. It never
receives provider credentials in arguments or wire messages. Each model request
executes once in a fresh selected native process.

``--local-embedding ollama-qwen3-stdio`` additionally permits only the selected
Qwen tags/show/embed operations on host 127.0.0.1:11434 over this same pipe.
It adds no listener, container gateway or provider fallback. ``--publish-index``
explicitly publishes matching generations through the same selected bridge and
private ``publish_index`` action, without starting HTTP, Workers or Temporal.
"""
from __future__ import annotations

import argparse
import hashlib
import http.client
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
from pathlib import Path

REQUEST = "KNOWLEDGEOS_NATIVE_REQUEST="
RESPONSE = "KNOWLEDGEOS_MODEL_RESPONSE="
MAX_MESSAGE = 2 * 1024 * 1024
EMBEDDING_REQUEST = "KNOWLEDGEOS_EMBEDDING_REQUEST="
LOCAL_EMBEDDING = "ollama-qwen3-stdio"
EMBEDDING_MODEL = "qwen3-embedding:8b-q4_K_M"
ADAPTERS = {
    "knowledgeos.cited_research_team": "native_research.py",
    "knowledgeos.note_metadata_team": "native_metadata.py",
    "knowledgeos.relation_ontology_team": "native_metadata.py",
    "knowledgeos.daily_corpus_stewardship_team": "native_metadata.py",
    "knowledgeos.cited_draft_team": "native_authoring.py",
    "knowledgeos.project_review_team": "native_authoring.py",
    "knowledgeos.knowledge_exchange_team": "native_boundary.py",
    "knowledgeos.vault_maintenance_team": "native_boundary.py",
}


class NativeDriverError(ValueError):
    """An explicit host binding or native response contract was refused."""


def require(condition, message):
    if not condition:
        raise NativeDriverError(message)


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def resolve_docker_command(selected):
    located = shutil.which(selected)
    require(located is not None, "Selected Docker executable is unavailable")
    path = Path(located).resolve(strict=True)
    require(path.is_file() and os.access(path, os.X_OK), "Selected Docker command is not executable")
    return str(path)


def policy_read(path):
    import stat
    metadata = path.stat(follow_symlinks=False)
    require(not path.is_symlink() and stat.S_ISREG(metadata.st_mode)
            and stat.S_IMODE(metadata.st_mode) == 0o600 and metadata.st_uid == os.getuid()
            and metadata.st_size <= 262144, "Host policy must be an existing private regular file")
    value = json.loads(path.read_bytes())
    require(value.get("version") == 1 and isinstance(value.get("methods"), dict), "Unsupported host Owner policy")
    return value


def adapter_binding_digest(adapter):
    return "sha256:" + hashlib.sha256(canonical({
        "adapter_sha256": hashlib.sha256(adapter.read_bytes()).hexdigest(),
        "runtime_sha256": hashlib.sha256(adapter.with_name("native_runtime.py").read_bytes()).hexdigest(),
    })).hexdigest()


def native_once(envelope, arguments, initial, docker):
    require(isinstance(envelope, dict) and set(envelope) == {"method_id", "request"}, "Invalid native envelope")
    method, request = envelope["method_id"], envelope["request"]
    current = policy_read(arguments.policy_file)
    require(method in ADAPTERS and current.get("director_enabled") is True
            and "submit_work" in current.get("actions", [])
            and current.get("actor_id") == initial.get("actor_id")
            and current.get("worker_identity") == initial.get("worker_identity")
            and method in initial["methods"] and current["methods"].get(method) == initial["methods"][method],
            "Current host native authority was revoked")
    registration = initial["methods"][method]
    require(request.get("runner_binding") == registration["runner_binding"], "Native Runner identity differs")
    adapter = (arguments.adapters_directory / ADAPTERS[method]).resolve(strict=True)
    require(request["runner_binding"]["adapter_digest"] == adapter_binding_digest(adapter), "Native adapter bytes changed")
    # Preserve the venv launcher path: resolving its Python symlink would select
    # the standalone interpreter and discard the committed dependency prefix.
    interpreter = arguments.hermes_python.absolute()
    require(interpreter.is_file() and os.access(interpreter, os.X_OK), "Selected native interpreter is unavailable")
    command = [str(interpreter), "-I", str(adapter),
        "--hermes-root", str(arguments.hermes_root.resolve(strict=True)),
        "--profile-home", str(arguments.profile_home.resolve(strict=True)),
        "--docker-command", docker, "--container-name", arguments.container_name]
    with tempfile.TemporaryDirectory(prefix="knowledgeos-owner-native-") as temporary:
        child = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True, start_new_session=True,
            env={**os.environ, "TMPDIR": temporary})
        try:
            output, _errors = child.communicate(json.dumps(request, ensure_ascii=False), timeout=330)
            require(child.returncode == 0, "Native result is uncertain; it will not be retried")
            values = [line[len(RESPONSE):] for line in output.splitlines() if line.startswith(RESPONSE)]
            require(len(values) == 1, "Native adapter returned no unique actual response")
            return json.loads(values[0])
        finally:
            if child.poll() is None:
                os.killpg(child.pid, signal.SIGTERM)
                try:
                    child.communicate(timeout=15)
                except subprocess.TimeoutExpired:
                    os.killpg(child.pid, signal.SIGKILL)
                    child.communicate(timeout=15)
            for stream in (child.stdin, child.stdout, child.stderr):
                stream.close()


def embedding_once(envelope, selection):
    """Bounded fixed-endpoint bridge; the Owner port independently verifies pins."""
    require(selection == LOCAL_EMBEDDING, "Host local embedding binding is absent")
    require(isinstance(envelope, dict) and set(envelope) == {"path", "body"},
            "Invalid local embedding envelope")
    path, body = envelope["path"], envelope["body"]
    if path == "/api/tags":
        require(body is None, "Invalid model identity request")
    elif path == "/api/show":
        require(body == {"model": EMBEDDING_MODEL}, "Invalid selected model request")
    elif path == "/api/embed":
        require(isinstance(body, dict) and set(body) == {"model", "input", "truncate"}
                and body["model"] == EMBEDDING_MODEL and body["truncate"] is False
                and isinstance(body["input"], str) and bool(body["input"].strip())
                and len(body["input"].encode("utf-8")) <= 1024 * 1024,
                "Invalid selected embedding request")
    else:
        raise NativeDriverError("Request is outside the local embedding binding")
    payload = None if body is None else json.dumps(body, ensure_ascii=False, allow_nan=False).encode("utf-8")
    connection = http.client.HTTPConnection("127.0.0.1", 11434, timeout=300)
    try:
        connection.request("GET" if body is None else "POST", path, body=payload,
                           headers={"Content-Type": "application/json"})
        response = connection.getresponse()
        require(response.status == 200, "Local embedding service refused the request")
        raw = response.read(MAX_MESSAGE + 1)
        require(len(raw) <= MAX_MESSAGE, "Local embedding response exceeds the wire bound")
        value = json.loads(raw)
        require(isinstance(value, dict), "Local embedding response is not an object")
        require(len(canonical({"response": value})) < MAX_MESSAGE,
                "Local embedding response exceeds the wire bound")
        return value
    finally:
        connection.close()


def owner_frames(stream):
    """Read complete bounded UTF-8 frames, including already buffered lines.

    This blocking reader remains interruptible by the driver's signal handler;
    it never combines descriptor readiness with buffered text reads.
    """
    while True:
        raw = stream.readline(MAX_MESSAGE + 1)
        if not raw:
            return
        require(len(raw) <= MAX_MESSAGE and raw.endswith(b"\n"),
                "Owner native wire frame is incomplete or oversized")
        yield raw.decode("utf-8")


def drive(arguments):
    docker = resolve_docker_command(arguments.docker_command)
    initial = policy_read(arguments.policy_file)
    require(not arguments.publish_index or arguments.local_embedding == LOCAL_EMBEDDING,
            "Index publication requires the explicit selected local embedding binding")
    network_name = os.environ.get("KNOWLEDGEOS_TEMPORAL_NETWORK")
    require(bool(network_name), "Select the exact host-owned internal Temporal network")
    inspected = subprocess.run([docker, "network", "inspect", network_name], check=True,
        capture_output=True, text=True, timeout=30)
    networks = json.loads(inspected.stdout)
    require(len(networks) == 1 and networks[0].get("Internal") is True,
            "Writable Owner requires an internal host-owned Temporal network")
    compose = [docker, "compose", "-p", arguments.project, "-f", str(arguments.compose_file.resolve(strict=True))]
    events = arguments.event_file.open("x", encoding="utf-8") if arguments.event_file else None
    process = None
    def stop(_signal, _frame):
        raise KeyboardInterrupt
    previous = {sig: signal.signal(sig, stop) for sig in (signal.SIGINT, signal.SIGTERM)}
    try:
        if not arguments.publish_index:
            subprocess.run(compose + ["up", "-d", "--no-build", "--no-deps", "relay"], check=True,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=60)
        command = compose + ["run", "--rm", "--no-deps", "--use-aliases", "-T", "--name", arguments.container_name,
            "owner", "python", "-m", "vaultops.interfaces.writable_owner",
            "publish-index" if arguments.publish_index else "serve",
            "--policy-file", "/run/owner-policy/policy.json"]
        if not arguments.publish_index:
            command.extend(["--bind-host", "owner-http", "--interval", str(arguments.interval)])
        if arguments.admission_only:
            command.append("--admission-only")
        if arguments.local_embedding:
            command.extend(["--local-embedding", arguments.local_embedding])
        with tempfile.TemporaryFile(mode="w+b") as errors:
            process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=errors)
            for line in owner_frames(process.stdout):
                if line.startswith(REQUEST):
                    envelope = json.loads(line[len(REQUEST):])
                    event = {"method_id": envelope["method_id"],
                        "work_run_id": envelope["request"]["work_run_id"],
                        "stage_id": envelope["request"]["stage_id"],
                        "request_digest": envelope["request"]["request_digest"]}
                    try:
                        require(not arguments.publish_index, "Index publication cannot execute native Work")
                        response = {"response": native_once(envelope, arguments, initial, docker)}
                        event["outcome"] = "response_received"
                    except (NativeDriverError, json.JSONDecodeError, OSError, subprocess.SubprocessError):
                        response = {"error": "NATIVE_RESULT_UNKNOWN"}
                        event["outcome"] = "unknown"
                    if events:
                        events.write(json.dumps(event) + "\n")
                        events.flush()
                    encoded = (json.dumps(response, ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8")
                    require(len(encoded) <= MAX_MESSAGE, "Native host response exceeds its wire bound")
                    process.stdin.write(encoded)
                    process.stdin.flush()
                elif line.startswith(EMBEDDING_REQUEST):
                    try:
                        response = {"response": embedding_once(
                            json.loads(line[len(EMBEDDING_REQUEST):]), arguments.local_embedding)}
                    except (ValueError, OSError, http.client.HTTPException):
                        # A refused embedding has no uncertain durable Work effect.
                        response = {"response": None}
                    encoded = (json.dumps(response, ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8")
                    require(len(encoded) <= MAX_MESSAGE, "Embedding host response exceeds its wire bound")
                    process.stdin.write(encoded)
                    process.stdin.flush()
                elif line.strip() == "KnowledgeOS writable Owner ready":
                    print("KnowledgeOS writable Owner ready", flush=True)
                elif line.startswith("KnowledgeOS Owner index published="):
                    print(line.rstrip("\n"), flush=True)
            process.wait(timeout=30)
            require(process.returncode == 0, "Installed Owner stopped unsuccessfully")
    except KeyboardInterrupt:
        pass
    finally:
        if process is not None:
            subprocess.run([docker, "stop", "--time", "30", arguments.container_name], check=False,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=45)
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=15)
            for stream in (process.stdin, process.stdout):
                stream.close()
        subprocess.run(compose + ["down", "--remove-orphans"], check=False,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=60)
        if events:
            events.close()
        for sig, callback in previous.items():
            signal.signal(sig, callback)
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("policy-file", "compose-file", "adapters-directory", "hermes-python", "hermes-root", "profile-home"):
        parser.add_argument("--" + name, required=True, type=Path)
    parser.add_argument("--project", required=True)
    parser.add_argument("--container-name", required=True)
    parser.add_argument("--docker-command", default="docker")
    parser.add_argument("--event-file", type=Path)
    parser.add_argument("--interval", type=float, default=1)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--publish-index", action="store_true",
        help="Explicit index publication only; requires selected embedding and private publish_index action")
    modes.add_argument("--admission-only", action="store_true",
        help="Explicitly admit without supervision, scheduling or Workers")
    parser.add_argument("--local-embedding", choices=(LOCAL_EMBEDDING,),
        help="Explicit pinned Qwen bridge to host 127.0.0.1:11434 over existing stdio")
    arguments = parser.parse_args()
    os.umask(0o077)
    try:
        return drive(arguments)
    except (ValueError, OSError, subprocess.SubprocessError):
        print("KnowledgeOS native Owner driver refused", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

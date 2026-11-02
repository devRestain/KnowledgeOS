"""Run a synthetic container smoke through a host-owned, stdio-only Ollama bridge.

Host: python3 ops/tests/support/ollama_embedding_smoke.py /absolute/new-evidence.json
No container network, model download, private root mount or published port is used.
"""
from __future__ import annotations

import hashlib
import http.client
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from uuid import uuid4

MODEL = "qwen3-embedding:8b-q4_K_M"
DIGEST = "64b933495768fbd3b87c20583d379728a07471e0c66733a9df87cd1901b3c44b"
IMAGE = "agentfabric-knowledgeos-dev:py3.12.8-uv0.8.14"
CONTROL = Path(__file__).resolve().parents[3]
WORKSPACE = CONTROL.parents[1]


class EmbeddingSmokeError(RuntimeError):
    """A declared smoke invariant failed, without exposing private diagnostics."""


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())


def host_request(path, body=None):
    data = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request("http://127.0.0.1:11434" + path, data=data,
                                     headers={"Content-Type": "application/json"})
    with OPENER.open(request, timeout=300) as response:
        raw = response.read(4 * 1024 * 1024 + 1)
    if len(raw) > 4 * 1024 * 1024:
        raise ValueError("Ollama response exceeds bound")
    return json.loads(raw)


def private_snapshot():
    result = {"domain": {}, "profiles": {}}
    for root in (WORKSPACE / "Vaults/KnowledgeHub", WORKSPACE / "States/Operations/knowledgeos",
                 WORKSPACE / "Runtimes/KnowledgeOS-runtime"):
        for current, dirs, files in os.walk(root, followlinks=False):
            dirs[:] = [name for name in dirs if name != ".git" and not (Path(current) / name).is_symlink()]
            for name in files:
                path = Path(current) / name
                if path.is_file() and not path.is_symlink():
                    result["domain"][str(path.relative_to(WORKSPACE))] = hashlib.sha256(path.read_bytes()).hexdigest()
    for path in (WORKSPACE / "States/System/Hermes/profiles").glob("kos-*/*"):
        if path.name in {"config.yaml", "auth.json", ".env", "SOUL.md"} and path.is_file() and not path.is_symlink():
            result["profiles"][str(path.relative_to(WORKSPACE))] = hashlib.sha256(path.read_bytes()).hexdigest()
    return result


def loaded():
    rows = host_request("/api/ps").get("models", [])
    return [{key: row.get(key) for key in ("name", "digest", "size", "size_vram")} for row in rows]


def selected_identity():
    models = host_request("/api/tags").get("models", [])
    return [{key: row.get(key) for key in ("name", "digest", "size")}
            for row in models if row.get("name") == MODEL]


def runner_resident_memory():
    completed = subprocess.run(["ps", "-axo", "rss=,command="], capture_output=True, text=True, check=True)
    rows = []
    for line in completed.stdout.splitlines():
        parts = line.strip().split(None, 1)
        if (len(parts) == 2 and "ollama" in parts[1].lower() and "runner" in parts[1].lower()
                and "3fcd3febec8b3fd64435204db75bf0dd73b91e8d0661e0331acfe7e7c3120b85" in parts[1]):
            rows.append(int(parts[0]))
    return {"matching_runner_process_count": len(rows), "process_rss_kib": sum(rows) if rows else None,
            "meaning": "host process RSS from ps in KiB; not exclusively model weights or GPU allocation"}


def relay_call(message, timings):
    path, body = message.get("path"), message.get("body")
    if path == "/api/tags":
        if body is not None:
            raise ValueError("unexpected tags body")
    elif path == "/api/show":
        if body != {"model": MODEL}:
            raise ValueError("unexpected model identity request")
    elif path == "/api/embed":
        if (not isinstance(body, dict) or set(body) != {"model", "input", "truncate"}
                or body["model"] != MODEL or body["truncate"] is not False
                or not isinstance(body["input"], str) or len(body["input"].encode()) > 1024 * 1024):
            raise ValueError("unexpected embedding request")
    else:
        raise ValueError("request is outside the local model bridge")
    start = time.monotonic_ns()
    result = host_request(path, body)
    if path == "/api/embed":
        timings.append({"wall_ms": (time.monotonic_ns() - start) / 1_000_000,
                        "total_duration_ns": result.get("total_duration"),
                        "load_duration_ns": result.get("load_duration"),
                        "prompt_eval_count_tokens": result.get("prompt_eval_count")})
    return result


def cleanup_container(process, container_name):
    """Remove only this invocation's named disposable container, even after pipe failure."""
    errors = []
    remaining = False
    if process is None:
        return {"complete": True, "errors": errors}
    try:
        try:
            if process.stdin is not None and not process.stdin.closed:
                process.stdin.close()
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=15)
        except (OSError, subprocess.SubprocessError) as error:
            errors.append(type(error).__name__)
        finally:
            if process.stdout is not None:
                process.stdout.close()
    finally:
        try:
            # --rm may already have removed it. Only the following absence check
            # determines whether that expected nonzero rm result is harmless.
            subprocess.run(["docker", "rm", "-f", container_name], check=False,
                           capture_output=True, timeout=30)
            observed = subprocess.run(
                ["docker", "ps", "-aq", "--filter", "name=^/" + container_name + "$"],
                check=True, capture_output=True, text=True, timeout=30,
            )
            remaining = bool(observed.stdout.strip())
        except (OSError, subprocess.SubprocessError) as error:
            remaining = True
            errors.append(type(error).__name__)
        finally:
            try:
                process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=15)
    return {"complete": not remaining and not errors, "errors": errors}


def host(evidence):
    if evidence.exists():
        raise ValueError("evidence must be a new file")
    evidence.parent.mkdir(parents=True, exist_ok=True)
    before = private_snapshot()
    report = {"status": "FAIL", "live_before_counts": {key: len(value) for key, value in before.items()},
              "network": "container none; bounded stdio bridge to host loopback", "timings": []}
    process = None
    container_name = "kos-embedding-" + uuid4().hex
    container_passed = preservation_passed = cleanup_complete = False
    try:
        report["model_before"] = selected_identity()
        report["loaded_before"] = loaded()
        if len(report["model_before"]) != 1 or report["model_before"][0]["digest"] != DIGEST:
            raise EmbeddingSmokeError("selected model manifest changed")
        report["measurement_context"] = (
            "Ollama scheduler may serve other models; latency and memory are concurrent and not isolated")
        argv = ["docker", "run", "--rm", "-i", "--name", container_name,
                "--pull=never", "--network", "none", "--read-only",
                "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
                "--user", f"{os.getuid()}:{os.getgid()}", "--tmpfs", "/tmp:rw,exec,nosuid,nodev,mode=1777",
                "--mount", f"type=bind,src={CONTROL},dst=/workspace/control,readonly",
                "--mount", f"type=bind,src={WORKSPACE / 'Core'},dst=/workspace/Core,readonly",
                "-e", "PYTHONDONTWRITEBYTECODE=1", "-e", "HOME=/tmp",
                "-e", "PYTHONPATH=/workspace/control/ops/src:/workspace/control/ops/tests",
                "--workdir", "/workspace/control/ops", IMAGE, "python",
                "/workspace/control/ops/tests/support/ollama_embedding_smoke.py", "container"]
        report["container_command"] = argv
        process = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, bufsize=1)
        assert process.stdin is not None and process.stdout is not None
        for line in process.stdout:
            message = json.loads(line)
            if message.get("kind") == "result":
                report["container_result"] = message["result"]
                break
            if message.get("kind") != "request":
                raise ValueError("unknown bridge message")
            try:
                response = {"ok": True, "value": relay_call(message, report["timings"])}
            except (OSError, ValueError, http.client.HTTPException) as error:
                response = {"ok": False, "error": type(error).__name__}
            process.stdin.write(json.dumps(response, ensure_ascii=False) + "\n")
            process.stdin.flush()
        process.stdin.close()
        report["container_exit_code"] = process.wait(timeout=60)
        if report["container_exit_code"] != 0 or report.get("container_result", {}).get("status") != "PASS":
            raise EmbeddingSmokeError("container embedding smoke did not pass")
        container_passed = True
    except (OSError, ValueError, http.client.HTTPException,
            subprocess.SubprocessError, EmbeddingSmokeError) as error:
        report["error"] = type(error).__name__
    finally:
        try:
            cleanup = cleanup_container(process, container_name)
            report["cleanup"] = cleanup
            cleanup_complete = cleanup["complete"]
        finally:
            try:
                after = private_snapshot()
                report["preservation"] = {
                    "domain_count": len(after["domain"]), "profiles_count": len(after["profiles"]),
                    "domain_unchanged": before["domain"] == after["domain"],
                    "profiles_unchanged": before["profiles"] == after["profiles"]}
                report["model_after"] = selected_identity()
                report["loaded_after"] = loaded()
                report["runner_resident_memory"] = runner_resident_memory()
                preservation_passed = (
                    report.get("model_before") == report["model_after"]
                    and report["preservation"]["domain_unchanged"]
                    and report["preservation"]["profiles_unchanged"])
                if not preservation_passed:
                    report["preservation_error"] = "model identity or private files changed"
            except (OSError, ValueError, http.client.HTTPException,
                    subprocess.SubprocessError) as error:
                report["preservation_error"] = type(error).__name__
            finally:
                report["status"] = "PASS" if container_passed and preservation_passed and cleanup_complete else "FAIL"
                evidence.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({"status": report["status"], "evidence": str(evidence)}))
    return 0 if report["status"] == "PASS" else 1


def container():
    from support.control_factory import make_separate_portable_fixture_roots
    from vaultops.adapters.core import AdmissionError
    from vaultops.adapters.ollama_embedding import PIN, compose_ollama_application
    from vaultops.projection import generate_projection
    from vaultops.search_modes import index_status

    class Bridge(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def _forward(self):
            length = int(self.headers.get("Content-Length", "0"))
            if length < 0 or length > 2 * 1024 * 1024:
                self.send_error(413)
                return
            body = json.loads(self.rfile.read(length)) if length else None
            print(json.dumps({"kind": "request", "path": self.path, "body": body}, ensure_ascii=False), flush=True)
            response = json.loads(sys.stdin.readline())
            payload = json.dumps(response.get("value", {}), ensure_ascii=False).encode()
            self.send_response(200 if response.get("ok") else 502)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        do_GET = _forward
        do_POST = _forward

    server = HTTPServer(("127.0.0.1", 0), Bridge)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    result = {"status": "FAIL"}
    try:
        with tempfile.TemporaryDirectory(prefix="knowledgeos-embedding-", dir="/tmp") as directory:
            roots = make_separate_portable_fixture_roots(Path(directory))
            projection, code = generate_projection(roots, generation_id="ollama-synthetic")
            assert code == 0, projection
            app = compose_ollama_application(roots, endpoint=f"http://127.0.0.1:{server.server_port}")
            port = app.handlers.vector_port
            assert index_status(roots, port)["vector_state"] == "unavailable"
            start = time.monotonic_ns()
            publication = app.daily_stewardship().publish_index()
            build_ms = (time.monotonic_ns() - start) / 1_000_000
            assert index_status(roots, port)["vector_state"] == "ready"
            queries = {"확정되지 않은 정보": "40_Knowledge/Notes/정보의 빈칸은 공포의 상상을 강화한다.md",
                       "Guestbook Horror Design Note": "40_Knowledge/Sources/Guestbook Horror Design Note.md"}
            quality = {}
            for query, expected in queries.items():
                quality[query] = {"expected": expected, "modes": {}}
                for mode in ("lexical", "vector", "hybrid"):
                    started = time.monotonic_ns()
                    report, code = app.search(query, mode=mode)
                    elapsed = (time.monotonic_ns() - started) / 1_000_000
                    assert code == 0, report
                    paths = [row["path"] for row in report["candidates"]]
                    quality[query]["modes"][mode] = {"top_paths": paths[:3], "top1_hit": bool(paths and paths[0] == expected),
                                                     "top3_hit": expected in paths[:3], "latency_ms": elapsed}
            ontology, code = app.search("implements", mode="ontology")
            assert code == 0 and ontology["candidates"] and all(row["graph_path"] for row in ontology["candidates"])
            pointer = roots.runtime / "index/vector/current.json"
            original = pointer.read_bytes()
            changed = json.loads(original)
            changed["model"]["artifact_digest"] = "sha256:" + "f" * 64
            pointer.write_text(json.dumps(changed))
            report, code = app.search("Guestbook Horror Design Note", mode="vector")
            assert code != 0 and report["errors"][0]["code"] == "INDEX_STALE"
            pointer.write_bytes(original)
            policy = roots.control / "ops/policies/retrieval.yaml"
            policy_before = policy.read_text()
            assert "default_hops: 1" in policy_before
            policy.write_text(policy_before.replace("default_hops: 1", "default_hops: 0"))
            report, code = app.search("Guestbook Horror Design Note", mode="hybrid")
            assert code != 0 and report["errors"][0]["code"] in {"INDEX_STALE", "INDEX_INVALID"}
            policy.write_text(policy_before)
            note = roots.vault / queries["확정되지 않은 정보"]
            note.write_text(note.read_text() + "\nSynthetic source change.\n")
            report, code = app.search("확정되지 않은 정보", mode="vector")
            assert code != 0 and report["errors"][0]["code"] in {"INDEX_STALE", "INDEX_INVALID"}
            result = {"status": "PASS", "pin": vars(PIN), "publication": publication,
                      "index_build_ms": build_ms, "quality": quality,
                      "ontology_candidates": len(ontology["candidates"]),
                      "source_policy_model_drift_refused": True}
    except (AdmissionError, AssertionError, OSError, ValueError) as error:
        result = {"status": "FAIL", "error": type(error).__name__}
    finally:
        try:
            server.shutdown()
        finally:
            try:
                server.server_close()
            finally:
                thread.join()
    print(json.dumps({"kind": "result", "result": result}, ensure_ascii=False), flush=True)
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("supply container or a new absolute evidence path")
    if sys.argv[1] == "container":
        raise SystemExit(container())
    target = Path(sys.argv[1])
    if not target.is_absolute():
        raise SystemExit("evidence path must be absolute")
    raise SystemExit(host(target))

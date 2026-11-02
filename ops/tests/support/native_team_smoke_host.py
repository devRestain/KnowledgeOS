"""Host-only native Team smoke orchestration; no live domain/profile writes."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from uuid import uuid4

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from support.normalization_work_smoke import MODEL_REQUEST, MODEL_RESPONSE
from support.process_streams import iter_process_lines
from support.research_work_smoke import (
    cleanup_project,
    native_adapter_digest,
    preservation_snapshot,
)


def _model(request, *, adapter, container):
    if request["runner_binding"]["adapter_digest"] != native_adapter_digest(adapter):
        raise RuntimeError("Native adapter changed after admission")
    command = [os.environ["KNOWLEDGEOS_HERMES_PYTHON"], "-I", str(adapter),
               "--hermes-root", os.environ["KNOWLEDGEOS_HERMES_SOURCE"],
               "--profile-home", os.environ["KNOWLEDGEOS_HERMES_PROFILE_HOME"],
               "--docker-command", shutil.which("docker") or "docker", "--container-name", container]
    with tempfile.TemporaryDirectory(prefix="knowledgeos-team-model-") as temporary:
        process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, text=True, start_new_session=True,
                                   env={**os.environ, "TMPDIR": temporary})
        try:
            stdout, stderr = process.communicate(json.dumps(request), timeout=300)
            if stderr:
                print(stderr, file=sys.stderr, end="")
            if process.returncode:
                raise RuntimeError("Native provider failed; uncertain effects are not retried")
            responses = [line[len(MODEL_RESPONSE):] for line in stdout.splitlines() if line.startswith(MODEL_RESPONSE)]
            if len(responses) != 1:
                raise RuntimeError("Native adapter returned no unique actual response")
            return json.loads(responses[0])
        finally:
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGINT)
                try:
                    process.communicate(timeout=15)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.communicate(timeout=15)
            for stream in (process.stdin, process.stdout, process.stderr):
                stream.close()


def _container(compose, *, binding, adapter, container, smoke_name, stages, marker):
    command = compose + ["run", "--rm", "--no-deps", "-T", "--name", container, "smoke", "python",
                         "/workspace/control/ops/tests/support/" + smoke_name, "run",
                         "--runner-binding", json.dumps(binding)]
    lines, count = [], 0
    with tempfile.TemporaryFile(mode="w+t") as errors:
        process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=errors,
                                   bufsize=0)
        try:
            deadline = time.monotonic() + max(1800, 360 * len(stages))
            for line in iter_process_lines(process.stdout, deadline):
                lines.append(line)
                if line.startswith(MODEL_REQUEST):
                    request = json.loads(line[len(MODEL_REQUEST):])
                    if count >= len(stages) or request["stage_id"] != stages[count] or request["runner_binding"] != binding:
                        raise RuntimeError("Native stages reordered/repeated or route changed")
                    count += 1
                    response = _model(request, adapter=adapter, container=container)
                    payload = memoryview((json.dumps(response, ensure_ascii=False) + "\n").encode("utf-8"))
                    while payload:
                        written = process.stdin.write(payload)
                        if not written:
                            raise BrokenPipeError("Native Team container stopped accepting its response")
                        payload = payload[written:]
            process.wait(timeout=30)
            if process.returncode or count != len(stages):
                raise RuntimeError("Native Team container failed: " + "".join(lines))
            records = [line[len(marker):] for line in lines if line.startswith(marker)]
            if len(records) != 1:
                raise RuntimeError("Native smoke returned no unique evidence record")
            return json.loads(records[0])
        finally:
            process.stdin.close()
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=15)
            process.stdout.close()
            errors.seek(0)
            stderr = errors.read()
            if stderr:
                print(stderr, file=sys.stderr, end="")


def host(evidence_path, *, adapter_name, smoke_name, stages, marker):
    control = Path(__file__).resolve().parents[3]
    evidence_path = Path(evidence_path).expanduser().absolute()
    evidence_path.parent.mkdir(parents=True, exist_ok=True)
    project = "kos-native-" + uuid4().hex[:16]
    container = project + "-runner"
    compose = ["docker", "compose", "-p", project, "-f", str(control / "ops/compose.team-smoke.yaml")]
    adapter = control / "ops/clients/hermes" / adapter_name
    preserved = preservation_snapshot(control)
    report = {"status": "FAIL", "compose_project": project}

    def interrupted(_signal, _frame):
        raise KeyboardInterrupt("Native smoke interrupted")

    previous = signal.signal(signal.SIGTERM, interrupted)
    try:
        with evidence_path.open("x", encoding="utf-8") as evidence:
            try:
                image = os.environ["KNOWLEDGEOS_TEAM_IMAGE"]
                inspected = subprocess.run(["docker", "image", "inspect", image], check=True,
                                           capture_output=True, text=True, timeout=30)
                image_id = json.loads(inspected.stdout)[0]["Id"]
                os.environ["KNOWLEDGEOS_TEAM_IMAGE"] = image_id
                binding = {"runtime_kind": "hermes_native", "hermes_version": "0.22.5",
                           "hermes_revision": "bf0203a794a07aac3ff764972ab9087baa4e4f4e",
                           "provider": "openai-codex", "model": "gpt-6.1-terra",
                           "adapter_digest": native_adapter_digest(adapter)}
                subprocess.run(compose + ["up", "-d", "--wait", "--wait-timeout", "120", "postgres", "server"],
                               check=True, timeout=180)
                report = _container(compose, binding=binding, adapter=adapter, container=container,
                                    smoke_name=smoke_name, stages=tuple(stages), marker=marker)
                if report.get("status") != "PASS" or report.get("disposable_roots_removed") is not True:
                    raise RuntimeError("Native Team did not complete and remove isolated roots")
                report["compose_project"] = project
                report["team_image"] = {"requested": image, "id": image_id}
                report["adapter_digest"] = binding["adapter_digest"]
                report["adapter_modules"] = {
                    path.name: "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()
                    for path in (adapter, adapter.with_name("native_runtime.py"))}
                report["model_temporary_roots_removed"] = True
            finally:
                failure = sys.exception()
                if failure is not None:
                    report["status"] = "FAIL"
                    report["error"] = str(failure)
                try:
                    cleanup = cleanup_project(compose, project, container)
                    report["cleanup"] = cleanup
                    report["cleanup_complete"] = cleanup["complete"]
                    after = preservation_snapshot(control)
                    report["preservation"] = {"unchanged": preserved == after,
                        "domain_files": sum(key.startswith("vault/") for key in preserved),
                        "profile_config_auth_files": sum(key.startswith("profiles/") for key in preserved)}
                    if preserved != after or not cleanup["complete"]:
                        report["status"] = "FAIL"
                finally:
                    cleanup_failure = sys.exception()
                    if cleanup_failure is not None and cleanup_failure is not failure:
                        report["status"] = "FAIL"
                        report["cleanup_error"] = str(cleanup_failure)
                    json.dump(report, evidence, ensure_ascii=False, indent=2)
                    evidence.write("\n")
    finally:
        signal.signal(signal.SIGTERM, previous)
    if report["status"] != "PASS":
        raise RuntimeError("Native smoke preservation or teardown failed")
    print("PASS: disposable native Team evidence: " + str(evidence_path))

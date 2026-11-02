"""Own disposable native lifecycle service startup, evidence and verified teardown."""

from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
from pathlib import Path
from uuid import uuid4

from curation_work_smoke import cleanup_project

MARKER = "KNOWLEDGEOS_NATIVE_LIFECYCLE="


def host(evidence: Path) -> None:
    control = Path(__file__).resolve().parents[3]
    evidence = evidence.expanduser().absolute()
    if evidence.exists() or evidence.is_symlink():
        raise FileExistsError("Native lifecycle evidence is create-only")
    evidence.parent.mkdir(parents=True, exist_ok=True)
    project = "kos-lifecycle-" + uuid4().hex[:12]
    container = project + "-runner"
    compose = ["docker", "compose", "-p", project, "-f", str(control / "ops/compose.team-smoke.yaml")]
    requested = os.environ["KNOWLEDGEOS_TEAM_IMAGE"]
    report = {"status": "FAIL", "compose_project": project, "cleanup_complete": False}
    failure = None

    def interrupted(_number, _frame):
        raise KeyboardInterrupt("Native lifecycle smoke interrupted")

    previous = signal.signal(signal.SIGTERM, interrupted)
    try:
        try:
            inspected = subprocess.run(["docker", "image", "inspect", requested], check=True,
                                       capture_output=True, text=True, timeout=30)
            image_id = json.loads(inspected.stdout)[0]["Id"]
            os.environ["KNOWLEDGEOS_TEAM_IMAGE"] = image_id
            subprocess.run(compose + ["up", "-d", "--wait", "--wait-timeout", "120", "postgres", "server"],
                           check=True, timeout=180)
            executed = subprocess.run(
                compose + ["run", "--rm", "--no-deps", "--name", container, "smoke", "python",
                           "/workspace/control/ops/tests/support/native_lifecycle_smoke.py"],
                check=True, capture_output=True, text=True, timeout=300,
            )
            lines = [line[len(MARKER):] for line in executed.stdout.splitlines() if line.startswith(MARKER)]
            if len(lines) != 1:
                raise RuntimeError("Native lifecycle runner returned no unique evidence record")
            report = json.loads(lines[0])
            if report.get("status") != "PASS" or not report.get("disposable_roots_removed"):
                raise RuntimeError("Native lifecycle runner did not finish its disposable proof")
            report.update(compose_project=project, image={"requested": requested, "id": image_id})
        except (OSError, ValueError, KeyError, subprocess.SubprocessError, RuntimeError, KeyboardInterrupt) as error:
            failure = error
            report.update(status="FAIL", error=str(error))
            if isinstance(error, (subprocess.CalledProcessError, subprocess.TimeoutExpired)):
                # This runner has only synthetic roots on an internal network;
                # no profile, credential, live State or Vault mounts exist.
                # Retain bounded diagnostics rather than losing the actual fault
                # behind CalledProcessError after --rm and verified teardown.
                def bounded(value):
                    if isinstance(value, bytes):
                        value = value.decode("utf-8", errors="replace")
                    return (value or "")[-65536:]

                report["runner_diagnostics"] = {
                    "stdout": bounded(error.output), "stderr": bounded(error.stderr),
                    "source": "credential-free disposable synthetic runner",
                }
        finally:
            cleanup = cleanup_project(compose, project, container)
            report.update(cleanup=cleanup, cleanup_complete=cleanup["complete"])
            if not cleanup["complete"]:
                report["status"] = "FAIL"
            with evidence.open("x", encoding="utf-8") as stream:
                json.dump(report, stream, ensure_ascii=False, indent=2)
                stream.write("\n")
    finally:
        os.environ["KNOWLEDGEOS_TEAM_IMAGE"] = requested
        signal.signal(signal.SIGTERM, previous)
    if failure is not None:
        raise failure
    if report["status"] != "PASS":
        raise RuntimeError("Native lifecycle service teardown did not complete")
    print(f"PASS: native lifecycle failure/recovery and restart dedupe; evidence: {evidence}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence", type=Path)
    args = parser.parse_args()
    evidence = args.evidence or (Path(__file__).resolve().parents[5] / "Tmp" /
                                 "knowledgeos-native-lifecycle" / (uuid4().hex + ".json"))
    host(evidence)


if __name__ == "__main__":
    main()

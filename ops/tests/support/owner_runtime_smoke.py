"""Actual installed writable CLI + maintained stdio driver in disposable roots.

Host: python3 ops/tests/support/owner_runtime_smoke.py host NEW_EVIDENCE.json
Optional --ui-hold-seconds retains the actual completed Owner for browser proof.
No fixture Runner, model response, Work completion or HTTP result is injected.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import http.client as http_client
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from uuid import uuid4

MARKER = "KNOWLEDGEOS_OWNER_RUNTIME_EVIDENCE="
PREPARED = "KNOWLEDGEOS_OWNER_FIXTURE="
STARTED = "KNOWLEDGEOS_OWNER_NATIVE_START="
HISTORIES = "KNOWLEDGEOS_OWNER_HISTORIES="
UI_READY = "KNOWLEDGEOS_OWNER_UI_READY="


def sha(raw):
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def prepare(directory, route):
    from support.control_factory import write_fixture_config
    from support.metadata_fixture import SOURCE, application
    from support.owner_runtime_fixture import policy_document, write_policy
    from vaultops.application import owner_runtime
    assert str(Path(owner_runtime.__file__).resolve()).startswith("/opt/knowledgeos/ops/src/")
    app, canary = application(directory / "roots")
    with app.journal.writer() as writer:
        writer.commit(app.clock())
    write_policy(directory / "owner-policy/policy.json", policy_document(app, route))
    layout = {kind: str(getattr(app.roots, kind).relative_to(directory))
              for kind in ("core", "control", "vault", "state", "runtime")}
    source = app.reference(SOURCE)
    # The production composition binds these exact independent container roots.
    # Scoped MCP intentionally resolves config without environment overrides.
    write_fixture_config(app.roots.control, core=Path("/workspace/Core"),
        vault=Path("/workspace/KnowledgeHub"), state=Path("/workspace/state"), runtime=Path("/workspace/runtime"))
    return {"layout": layout, "source_path": SOURCE, "source": source, "canary": canary,
            "installed_owner_module": str(Path(owner_runtime.__file__).resolve())}


async def namespace():
    from temporalio.api.workflowservice.v1 import RegisterNamespaceRequest
    from temporalio.client import Client
    from temporalio.service import RPCError, RPCStatusCode
    client = await Client.connect("server:7233", namespace="knowledgeos", tls=False)
    try:
        await client.workflow_service.register_namespace(RegisterNamespaceRequest(namespace="knowledgeos",
            workflow_execution_retention_period={"seconds": 3600}))
    except RPCError as error:
        if error.status != RPCStatusCode.ALREADY_EXISTS:
            raise


async def claim_and_exit(work_run_id):
    """Actual native start, then hard process loss before owner acknowledgement."""
    from clients.temporal import TemporalBinding
    from temporalio.client import Client

    from vaultops.adapters.paths import resolve_paths
    from vaultops.application.exops import work_workflow_type
    from vaultops.application.owner_runtime import OwnerRuntimePolicy, WritableOwnerRuntime
    from vaultops.application.temporal_work import WorkDispatch, connect_owner_temporal
    from vaultops.interfaces.writable_owner import NativeStdioRunner, NativeStdioTransport
    policy = OwnerRuntimePolicy(Path("/run/owner-policy/policy.json"))
    transport = NativeStdioTransport()
    runtime = WritableOwnerRuntime(resolve_paths(), policy=policy,
        runners={method: NativeStdioRunner(transport, method, item["runner_binding"])
                 for method, item in policy.initial["methods"].items()})
    record = runtime.application.journal.read()["intents"]["exops.work." + work_run_id]
    wire = work_workflow_type(record)
    binding = TemporalBinding("knowledgeos", "server:7233", "knowledgeos", "knowledgeos.work.v1",
        wire, "knowledgeos/", "disabled.update", "disabled.signal", "disabled.query", False)
    scoped = await connect_owner_temporal(runtime.exops, binding)
    with runtime.exops.claim_dispatch(work_run_id) as admitted:
        dispatch = WorkDispatch("knowledgeos", "knowledgeos/" + work_run_id, admitted["action_id"], {
            "owner_operation_id": "knowledgeos", "work_run_id": work_run_id,
            "action_id": admitted["action_id"], "content_digest": admitted["content_digest"],
            "work_spec_digest": admitted["spec"]["spec_digest"]})
        await scoped.start(dispatch)
        sdk = await Client.connect("server:7233", namespace="knowledgeos", tls=False)
        description = await sdk.get_workflow_handle(dispatch.workflow_id).describe()
        print(STARTED + json.dumps({"workflow_id": description.id, "run_id": description.run_id,
            "workflow_type": description.workflow_type, "task_queue": description.task_queue}), flush=True)
        os._exit(75)


def extract(output, marker):
    values = [json.loads(line[len(marker):]) for line in output.splitlines() if line.startswith(marker)]
    if len(values) != 1:
        raise RuntimeError("Disposable command returned no unique evidence record")
    return values[0]


def http(port, route, payload):
    connection = http_client.HTTPConnection("127.0.0.1", port, timeout=30)
    try:
        connection.request("POST", "/owner/" + route, json.dumps(payload), headers={
            "Content-Type": "application/json"})
        response = connection.getresponse()
        return response.status, json.loads(response.read())
    finally:
        connection.close()


def wait_owner(process, port):
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError("Maintained Owner driver stopped before readiness")
        try:
            status, value = http(port, "owner/status", {})
            if status == 200:
                return value
        except (OSError, ValueError):
            pass
        time.sleep(0.2)
    raise TimeoutError("Maintained Owner HTTP readiness deadline exceeded")


def stop_driver(process):
    if process is None:
        return
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=150)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=30)
    if process.stdout:
        process.stdout.close()


def host(evidence_path, *, ui_hold_seconds=0):
    import native_team_smoke_host as host_support

    if not 0 <= ui_hold_seconds <= 1800:
        raise ValueError("UI hold must be between 0 and 1800 seconds")
    control = Path(__file__).resolve().parents[3]
    evidence_path = Path(evidence_path).expanduser().absolute()
    evidence_path.parent.mkdir(parents=True, exist_ok=True)
    project = "kos-owner-runtime-" + uuid4().hex[:12]
    temporal_project, owner_project = project + "-temporal", project + "-owner"
    container = project + "-owner"
    compose = ["docker", "compose", "-p", temporal_project, "-f", str(control / "ops/compose.team-smoke.yaml")]
    before = host_support.preservation_snapshot(control)
    report, failure, driver = {"status": "FAIL"}, None, None
    evidence = evidence_path.open("x", encoding="utf-8")
    with tempfile.TemporaryDirectory(prefix="knowledgeos-writable-owner-") as temporary:
        root = Path(temporary)
        policy_directory = root / "owner-policy"
        policy_directory.mkdir(mode=0o700)
        try:
            image = os.environ["KNOWLEDGEOS_TEAM_IMAGE"]
            inspected = subprocess.run(["docker", "image", "inspect", image], check=True, capture_output=True, text=True, timeout=30)
            image_id = json.loads(inspected.stdout)[0]["Id"]
            os.environ["KNOWLEDGEOS_TEAM_IMAGE"] = image_id
            adapter = control / "ops/clients/hermes/native_metadata.py"
            route = {"runtime_kind": "hermes_native", "hermes_version": "0.22.5",
                "hermes_revision": "bf0203a794a07aac3ff764972ab9087baa4e4f4e", "provider": "openai-codex",
                "model": "gpt-6.1-terra", "adapter_digest": host_support.native_adapter_digest(adapter)}
            subprocess.run(compose + ["up", "-d", "--wait", "--wait-timeout", "120", "postgres", "server"], check=True, timeout=180)
            prepared = subprocess.run(compose + ["run", "--rm", "--no-deps", "-T",
                "--volume", str(root) + ":/fixture", "-e", "PYTHONPATH=/opt/knowledgeos/ops/src:/workspace/control/ops/tests:/opt/services",
                "smoke", "python", "/workspace/control/ops/tests/support/owner_runtime_smoke.py", "prepare",
                "--directory", "/fixture", "--runner-binding", json.dumps(route)],
                check=True, capture_output=True, text=True, timeout=180)
            fixture = extract(prepared.stdout, PREPARED)
            sources = {kind: root / relative for kind, relative in fixture["layout"].items()}
            with socket.socket() as selected:
                selected.bind(("127.0.0.1", int(os.environ.get("KNOWLEDGEOS_OWNER_PORT") or "0")))
                port = selected.getsockname()[1]
            env = {**os.environ, "KNOWLEDGEOS_TEAM_IMAGE": image_id,
                "KNOWLEDGEOS_OWNER_CONTAINER": container, "KNOWLEDGEOS_OWNER_PORT": str(port),
                "KNOWLEDGEOS_OWNER_POLICY_SOURCE": str(policy_directory),
                "KNOWLEDGEOS_TEMPORAL_NETWORK": temporal_project + "_default"}
            for kind, source in sources.items():
                env["KNOWLEDGEOS_" + kind.upper() + "_SOURCE"] = str(source)
            owner_compose = ["docker", "compose", "-p", owner_project, "-f", str(control / "ops/compose.owner-writable.yaml")]
            driver_command = [sys.executable, str(control / "ops/src/vaultops/interfaces/native_owner_driver.py"),
                "--policy-file", str(policy_directory / "policy.json"), "--compose-file", str(control / "ops/compose.owner-writable.yaml"),
                "--project", owner_project, "--container-name", container,
                "--adapters-directory", str(control / "ops/clients/hermes"),
                "--hermes-python", os.environ["KNOWLEDGEOS_HERMES_PYTHON"],
                "--hermes-root", os.environ["KNOWLEDGEOS_HERMES_SOURCE"],
                "--profile-home", os.environ["KNOWLEDGEOS_HERMES_PROFILE_HOME"]]
            events_first, events_final = root / "admission-events.jsonl", root / "native-events.jsonl"
            driver = subprocess.Popen(driver_command + ["--admission-only", "--event-file", str(events_first)],
                env=env, stdout=subprocess.PIPE, text=True)
            wait_owner(driver, port)
            request = {"template_id": "note_metadata", "request_id": str(uuid4()),
                "scope": {"path": fixture["source_path"], "content_sha256": fixture["source"]["source_revision"]}}
            policy_file = policy_directory / "policy.json"
            policy = json.loads(policy_file.read_text())
            policy["actions"].remove("submit_work")
            policy_file.write_text(json.dumps(policy))
            assert http(port, "work/intake-template", request)[0] == 403
            policy["actions"].append("submit_work")
            policy_file.write_text(json.dumps(policy))
            status, group = http(port, "work/intake-template", request)
            assert status == 200 and len(group["receipts"]) == 2, group
            assert http(port, "work/intake-template", request)[1]["replayed"]
            ids = [receipt["work_run_id"] for receipt in group["receipts"]]
            state_path = sources["state"] / "owner-control.json"
            initial = json.loads(state_path.read_bytes())
            assert all(initial["intents"]["exops.work." + work_id]["dispatch_state"] == "pending" for work_id in ids)
            assert events_first.read_text() == ""
            stop_driver(driver)
            driver = None
            helper = str(control / "ops/tests/support/owner_runtime_smoke.py") + ":/run/owner-runtime-smoke.py:ro"
            claimed = subprocess.run(owner_compose + ["run", "--rm", "--no-deps", "-T", "--name", container + "-claim",
                "--volume", helper, "owner", "python", "/run/owner-runtime-smoke.py", "claim", "--work-run-id", ids[0]],
                env=env, capture_output=True, text=True, timeout=120, check=False)
            assert claimed.returncode == 75, "Actual native start hard-loss command did not reach its boundary"
            start = extract(claimed.stdout, STARTED)
            unknown = json.loads(state_path.read_bytes())
            assert unknown["intents"]["exops.work." + ids[0]]["dispatch_state"] == "unknown"
            assert unknown["intents"]["exops.work." + ids[1]]["dispatch_state"] == "pending"
            driver = subprocess.Popen(driver_command + ["--event-file", str(events_final)], env=env,
                stdout=subprocess.PIPE, text=True)
            wait_owner(driver, port)
            deadline = time.monotonic() + 1800
            while True:
                if driver.poll() is not None:
                    raise RuntimeError("Maintained driver failed during native work")
                current = json.loads(state_path.read_bytes())
                records = [current["intents"]["exops.work." + work_id] for work_id in ids]
                if all(item.get("application_state") == "recorded" for item in records):
                    break
                if time.monotonic() > deadline:
                    raise TimeoutError("Actual native Owner result deadline exceeded")
                time.sleep(0.5)
            exports = []
            for work_id, record in zip(ids, records):
                assert record["run"]["state"] == "completed" and record["assessment"]["completion_state"] == "complete"
                assert record["spec"]["eval_officer_executor_id"] not in record["spec"]["allowed_executor_ids"]
                status, result = http(port, "work/result", {"work_run_id": work_id})
                assert status == 200 and result["assessment"] == record["assessment"], result
                effect = next(item for item in current["intents"].values()
                    if item.get("identity", {}).get("work_run_id") == work_id
                    and item.get("content_digest") == record["run"]["result_reference"]["content_digest"])
                raw = (sources["vault"] / effect["artifact_path"]).read_bytes()
                assert result["text"] == raw.decode() and sha(raw) == record["run"]["result_reference"]["content_digest"]
                assert fixture["canary"] in json.dumps(record, ensure_ascii=False)
                exports.append({"work_run_id": work_id, "text_digest": sha(raw), "assessment": result["assessment"]})
            # Worker receipt commits precede automatic grouped proposal creation.
            promotion_deadline = time.monotonic() + 30
            while True:
                current = json.loads(state_path.read_bytes())
                promotions = [item for key, item in current["intents"].items()
                    if key.startswith("metadata.") and item.get("template_request_id") == request["request_id"]]
                if len(promotions) == 1 and promotions[0]["state"] == "completed":
                    break
                if time.monotonic() > promotion_deadline:
                    raise RuntimeError("Actual independent results did not produce the one grouped Pending proposal")
                time.sleep(0.2)
            events = [json.loads(line) for line in events_final.read_text().splitlines()]
            assert len(events) == 4 and all(item["outcome"] == "response_received" for item in events)
            assert len({(item["work_run_id"], item["stage_id"]) for item in events}) == 4
            assert len([key for key in current["intents"] if key.startswith("exops.work.")]) == 2
            assert sha((sources["vault"] / fixture["source_path"]).read_bytes()) == fixture["source"]["content_digest"]
            replay_status, replay = http(port, "work/prepare-metadata", {"request_id": request["request_id"]})
            assert replay_status == 200 and replay["status"] == "NO_OP", replay
            fetched = subprocess.run(["docker", "exec", "-i", container, "python", "-c",
                "import sys;exec(compile(sys.stdin.read(),'<owner-history-proof>','exec'))"],
                input="import asyncio,json\nfrom temporalio.client import Client\n"
                    + "async def proof():\n c=await Client.connect('server:7233',namespace='knowledgeos',tls=False)\n out=[]\n"
                    + " for work_id in " + repr(ids) + ":\n  h=c.get_workflow_handle('knowledgeos/'+work_id)\n"
                    + "  d=await h.describe(); history=await h.fetch_history()\n"
                    + "  a=[e.activity_task_scheduled_event_attributes.activity_type.name for e in history.events if e.HasField('activity_task_scheduled_event_attributes')]\n"
                    + "  assert len(a)==len(set(a))==4\n  out.append({'work_run_id':work_id,'run_id':d.run_id,'activities':a,'events':len(history.events)})\n"
                    + " print('" + HISTORIES + "'+json.dumps(out))\nasyncio.run(proof())\n",
                check=True, capture_output=True, text=True, timeout=120)
            native_histories = extract(fetched.stdout, HISTORIES)
            assert native_histories[0]["run_id"] == start["run_id"]
            if ui_hold_seconds:
                print(UI_READY + json.dumps({"url": "http://127.0.0.1:" + str(port),
                    "work_run_ids": ids, "hold_seconds": ui_hold_seconds}), flush=True)
                time.sleep(ui_hold_seconds)
            report = {"status": "PASS", "team_image": {"requested": image, "id": image_id},
                "installed_owner_module": fixture["installed_owner_module"], "maintained_driver": True,
                "http_admission": group, "histories": native_histories, "exports": exports,
                "unknown_reconstructed_native_run_id": start["run_id"], "model_calls": len(events),
                "source_preserved": True, "pending_metadata_proposals": 1,
                "revoked_policy_ingress_denied": True, "duplicate_work_or_model_or_proposal": False}
        except (AssertionError, OSError, ValueError, RuntimeError, subprocess.SubprocessError, KeyboardInterrupt) as error:
            failure = error
            report.update(status="FAIL", error=type(error).__name__)
        finally:
            stop_driver(driver)
            # Driver owns only its writable Owner/relay project, never Temporal.
            subprocess.run(["docker", "compose", "-p", owner_project, "-f", str(control / "ops/compose.owner-writable.yaml"),
                "down", "--remove-orphans"], env=locals().get("env", os.environ), check=False,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=90)
            subprocess.run(["docker", "rm", "--force", container, container + "-claim"], check=False,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=60)
            remaining = subprocess.run(["docker", "ps", "--all", "--quiet", "--filter",
                "label=com.docker.compose.project=" + owner_project], check=True,
                capture_output=True, text=True, timeout=30)
            owner_networks = subprocess.run(["docker", "network", "ls", "--quiet", "--filter",
                "label=com.docker.compose.project=" + owner_project], check=True,
                capture_output=True, text=True, timeout=30)
            owner_cleanup = not remaining.stdout.strip() and not owner_networks.stdout.strip()
            cleanup = host_support.cleanup_project(compose, temporal_project, container)
            cleanup["owner_complete"] = owner_cleanup
            cleanup["complete"] = cleanup["complete"] and owner_cleanup
            report["cleanup"] = cleanup
            report["preservation_unchanged"] = before == host_support.preservation_snapshot(control)
            if not cleanup["complete"] or not report["preservation_unchanged"]:
                failure = failure or RuntimeError("Disposable preservation or cleanup failed")
                report["status"] = "FAIL"
            report["disposable_roots_removed"] = False
    report["disposable_roots_removed"] = not root.exists()
    with evidence:
        json.dump(report, evidence, ensure_ascii=False, indent=2)
        evidence.write("\n")
    if failure:
        raise failure
    print("PASS: maintained writable Owner evidence: " + str(evidence_path))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="mode", required=True)
    host_parser = sub.add_parser("host")
    host_parser.add_argument("evidence", type=Path)
    host_parser.add_argument("--ui-hold-seconds", type=int, default=0)
    prep = sub.add_parser("prepare")
    prep.add_argument("--directory", type=Path, required=True)
    prep.add_argument("--runner-binding", required=True)
    claim = sub.add_parser("claim")
    claim.add_argument("--work-run-id", required=True)
    args = parser.parse_args()
    if args.mode == "host":
        host(args.evidence, ui_hold_seconds=args.ui_hold_seconds)
    elif args.mode == "prepare":
        asyncio.run(namespace())
        print(PREPARED + json.dumps(prepare(args.directory, json.loads(args.runner_binding)), ensure_ascii=False), flush=True)
    else:
        asyncio.run(claim_and_exit(args.work_run_id))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

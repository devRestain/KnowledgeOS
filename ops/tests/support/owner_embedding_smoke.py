"""Explicit installed publication + trusted-local four-mode HTTP, without Work/Codex.

Host: KNOWLEDGEOS_TEAM_IMAGE=<rebuilt-image> python3 \
  ops/tests/support/owner_embedding_smoke.py NEW_EVIDENCE.json
Uses the existing host Qwen service only through the maintained selected driver.
Starts disposable Temporal for the unchanged serving startup contract; publication
itself starts no Temporal client, HTTP relay, Worker or scheduler. No real roots
are mounted; private roots/profiles are hashed only for preservation evidence.
"""
from __future__ import annotations

import hashlib
import http.client as http_client
import json
import os
import socket
import subprocess
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

import native_team_smoke_host as host_support
import owner_runtime_smoke as owner_support
from ollama_embedding_smoke import private_snapshot

BINDING = "ollama-qwen3-stdio"
PUBLISHED = "KnowledgeOS Owner index published="


def http(port, query, mode):
    connection = http_client.HTTPConnection("127.0.0.1", port, timeout=360)
    try:
        connection.request("POST", "/owner/search/query", json.dumps({"query": query, "mode": mode}),
            headers={"Content-Type": "application/json"})
        response = connection.getresponse()
        return response.status, json.loads(response.read())
    finally:
        connection.close()


def notes(root):
    return {str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in root.rglob("*.md")}


@contextmanager
def smoke_evidence(path, report):
    """Persist redacted evidence after root cleanup without catching failures."""
    with path.open("x", encoding="utf-8") as evidence:
        root = None
        try:
            with tempfile.TemporaryDirectory(prefix="knowledgeos-owner-embedding-") as temporary:
                root = Path(temporary)
                yield root
        finally:
            error = sys.exception()
            if error is not None:
                report.update({"status": "FAIL", "error": type(error).__name__})
            report["disposable_roots_removed"] = root is None or not root.exists()
            evidence.write(json.dumps(report, indent=2, ensure_ascii=False) + "\n")


def host(evidence_path):
    control = Path(__file__).resolve().parents[3]
    evidence_path = Path(evidence_path).expanduser().absolute()
    evidence_path.parent.mkdir(parents=True, exist_ok=True)
    project = "kos-owner-embedding-" + uuid4().hex[:12]
    temporal_project, owner_project = project + "-temporal", project + "-owner"
    container = project + "-owner"
    compose = ["docker", "compose", "-p", temporal_project, "-f", str(control / "ops/compose.team-smoke.yaml")]
    report, failure, driver = {"status": "FAIL"}, None, None
    with smoke_evidence(evidence_path, report) as root:
        before = private_snapshot()
        policy_directory = root / "owner-policy"
        policy_directory.mkdir(mode=0o700)
        try:
            image = os.environ["KNOWLEDGEOS_TEAM_IMAGE"]
            inspected = subprocess.run(["docker", "image", "inspect", image], check=True,
                capture_output=True, text=True, timeout=30)
            image_id = json.loads(inspected.stdout)[0]["Id"]
            os.environ["KNOWLEDGEOS_TEAM_IMAGE"] = image_id
            route = {"runtime_kind": "hermes_native", "hermes_version": "0.22.5",
                "hermes_revision": "bf0203a794a07aac3ff764972ab9087baa4e4f4e", "provider": "openai-codex",
                "model": "gpt-6.1-terra", "adapter_digest": host_support.native_adapter_digest(
                    control / "ops/clients/hermes/native_metadata.py")}
            subprocess.run(compose + ["up", "-d", "--wait", "--wait-timeout", "120", "postgres", "server"],
                check=True, timeout=180)
            prepared = subprocess.run(compose + ["run", "--rm", "--no-deps", "-T",
                "--volume", str(root) + ":/fixture", "-e",
                "PYTHONPATH=/opt/knowledgeos/ops/src:/workspace/control/ops/tests:/opt/services",
                "smoke", "python", "/workspace/control/ops/tests/support/owner_runtime_smoke.py", "prepare",
                "--directory", "/fixture", "--runner-binding", json.dumps(route)],
                check=True, capture_output=True, text=True, timeout=180)
            fixture = owner_support.extract(prepared.stdout, owner_support.PREPARED)
            sources = {kind: root / relative for kind, relative in fixture["layout"].items()}
            note_before = notes(sources["vault"])
            with socket.socket() as selected:
                selected.bind(("127.0.0.1", 0))
                port = selected.getsockname()[1]
            env = {**os.environ, "KNOWLEDGEOS_TEAM_IMAGE": image_id,
                "KNOWLEDGEOS_OWNER_CONTAINER": container, "KNOWLEDGEOS_OWNER_PORT": str(port),
                "KNOWLEDGEOS_OWNER_POLICY_SOURCE": str(policy_directory),
                "KNOWLEDGEOS_TEMPORAL_NETWORK": temporal_project + "_default"}
            for kind, source in sources.items():
                env["KNOWLEDGEOS_" + kind.upper() + "_SOURCE"] = str(source)
            command = [sys.executable, str(control / "ops/src/vaultops/interfaces/native_owner_driver.py"),
                "--policy-file", str(policy_directory / "policy.json"), "--compose-file",
                str(control / "ops/compose.owner-writable.yaml"), "--project", owner_project,
                "--container-name", container, "--adapters-directory", str(control / "ops/clients/hermes"),
                # Required native arguments are inert: no Hermes process/profile is consulted.
                "--hermes-python", "/unselected/hermes-python", "--hermes-root", "/unselected/hermes",
                "--profile-home", "/unselected/profile"]
            # Neither default serving nor explicit binding publishes an index.
            for selection in (None, BINDING):
                event_file = root / ("unpublished-" + str(selection) + "-events.jsonl")
                selected_command = command + ["--admission-only", "--event-file", str(event_file)]
                if selection is not None:
                    selected_command.extend(["--local-embedding", selection])
                driver = subprocess.Popen(selected_command, env=env, stdout=subprocess.PIPE, text=True)
                owner_support.wait_owner(driver, port)
                assert http(port, "Guestbook", "lexical")[0] == 200
                for mode in ("vector", "hybrid"):
                    status, value = http(port, "Guestbook", mode)
                    assert status != 200 and value["errors"][0]["code"] == "INDEX_UNAVAILABLE", value
                assert not (sources["runtime"] / "index/vector/current.json").exists()
                owner_support.stop_driver(driver)
                driver = None
                assert event_file.read_text() == ""
            # Explicit publication requires both the selected binding and a private action.
            refused = subprocess.run(command + ["--publish-index"], env=env, check=False,
                capture_output=True, text=True, timeout=60,
            )
            assert refused.returncode != 0
            refused = subprocess.run(command + ["--publish-index", "--local-embedding", BINDING], env=env, check=False,
                capture_output=True, text=True, timeout=120,
            )
            assert refused.returncode != 0
            policy_file = policy_directory / "policy.json"
            policy = json.loads(policy_file.read_bytes())
            policy["actions"].append("publish_index")
            policy_file.write_text(json.dumps(policy))
            published = subprocess.run(command + ["--publish-index", "--local-embedding", BINDING],
                env=env, check=True, capture_output=True, text=True, timeout=1800)
            publication = owner_support.extract(published.stdout, PUBLISHED)
            pointer = sources["runtime"] / "index/vector/current.json"
            pinned = json.loads(pointer.read_bytes())
            assert pinned["model"]["model_id"] == "ollama/qwen3-embedding:8b-q4_K_M"
            assert pinned["model"]["artifact_digest"] == "sha256:64b933495768fbd3b87c20583d379728a07471e0c66733a9df87cd1901b3c44b"
            assert pinned["model"]["dimension"] == 4096
            state_file = sources["state"] / "owner-control.json"
            state_before = state_file.read_bytes()
            event_file = root / "bound-events.jsonl"
            driver = subprocess.Popen(command + ["--admission-only", "--local-embedding", BINDING,
                "--event-file", str(event_file)], env=env, stdout=subprocess.PIPE, text=True)
            owner_support.wait_owner(driver, port)
            results = {}
            for mode in ("lexical", "vector", "hybrid", "ontology"):
                query = "implements" if mode == "ontology" else "Guestbook Horror Design Note"
                status, result = http(port, query, mode)
                assert status == 200 and result["results"], result
                results[mode] = result
            assert all(row["reason"] == "approved_ontology_edge" for row in results["ontology"]["results"])
            assert results["vector"]["vector_generation_id"] == publication["vector"]["generation_id"]
            policy["actions"].remove("search_query")
            policy_file.write_text(json.dumps(policy))
            assert http(port, "Guestbook", "vector")[0] == 403
            policy["actions"].append("search_query")
            policy_file.write_text(json.dumps(policy))
            original_pointer = pointer.read_bytes()
            changed = json.loads(original_pointer)
            changed["model"]["artifact_digest"] = "sha256:" + "f" * 64
            pointer.write_text(json.dumps(changed))
            status, value = http(port, "Guestbook", "vector")
            assert status != 200 and value["errors"][0]["code"] == "INDEX_STALE", value
            pointer.write_bytes(original_pointer)
            retrieval_policy = sources["control"] / "ops/policies/retrieval.yaml"
            original_policy = retrieval_policy.read_bytes()
            assert b"default_hops: 1" in original_policy
            retrieval_policy.write_bytes(original_policy.replace(b"default_hops: 1", b"default_hops: 0"))
            status, value = http(port, "Guestbook", "hybrid")
            assert status != 200 and value["errors"][0]["code"] in {"INDEX_STALE", "INDEX_INVALID"}, value
            retrieval_policy.write_bytes(original_policy)
            source = sources["vault"] / fixture["source_path"]
            original_source = source.read_bytes()
            source.write_bytes(original_source + b"\nSynthetic stale source.\n")
            status, value = http(port, "Guestbook", "vector")
            assert status != 200 and value["errors"][0]["code"] in {"INDEX_STALE", "INDEX_INVALID"}, value
            source.write_bytes(original_source)
            assert state_before == state_file.read_bytes()
            assert not any(key.startswith("exops.work.") for key in json.loads(state_before)["intents"])
            assert note_before == notes(sources["vault"])
            assert event_file.read_text() == ""
            report.update({"status": "PASS", "image": image_id, "fixture": fixture,
                "publication": publication, "model_pin": pinned["model"], "http": results,
                "unbound_and_publication_permission_refused": True,
                "live_policy_revocation_refused": True,
                "model_generation_source_policy_drift_refused": True,
                "canonical_fixture_notes_unchanged": True, "work_admissions": 0, "hermes_codex_calls": 0,
                "limits": "Disposable roots only; no real activation, provider Work or read-only Owner enablement"})
        finally:
            cleanup_errors = []
            try:
                owner_support.stop_driver(driver)
            except (OSError, subprocess.SubprocessError) as error:
                cleanup_errors.append(type(error).__name__)
            for command, environment in (
                (["docker", "compose", "-p", owner_project, "-f",
                  str(control / "ops/compose.owner-writable.yaml"), "down", "--volumes", "--remove-orphans"],
                 locals().get("env", os.environ)),
                (compose + ["down", "--volumes", "--remove-orphans"], os.environ),
            ):
                try:
                    stopped = subprocess.run(command, env=environment, check=False,
                        capture_output=True, timeout=120)
                    if stopped.returncode:
                        cleanup_errors.append("Compose teardown refused")
                except (OSError, subprocess.SubprocessError) as error:
                    cleanup_errors.append(type(error).__name__)
            remaining = {}
            for selected_project in (owner_project, temporal_project):
                for kind, command in (
                    ("containers", ["docker", "ps", "-aq"]),
                    ("networks", ["docker", "network", "ls", "-q"]),
                    ("volumes", ["docker", "volume", "ls", "-q"]),
                ):
                    try:
                        observed = subprocess.run(command + ["--filter",
                            "label=com.docker.compose.project=" + selected_project],
                            check=True, capture_output=True, text=True, timeout=30)
                        remaining[selected_project + "/" + kind] = observed.stdout.split()
                    except (OSError, subprocess.SubprocessError) as error:
                        cleanup_errors.append(type(error).__name__)
            report["cleanup"] = {"complete": not cleanup_errors and not any(remaining.values()),
                                 "remaining": remaining, "errors": cleanup_errors}
            try:
                after = private_snapshot()
                report["preservation"] = {"domain_count": len(after["domain"]),
                    "profiles_count": len(after["profiles"]), "domain_unchanged": before["domain"] == after["domain"],
                    "profiles_unchanged": before["profiles"] == after["profiles"]}
                preserved = before == after
            except OSError as error:
                preserved = False
                report["preservation_error"] = type(error).__name__
            if not preserved or not report["cleanup"]["complete"]:
                report["status"] = "FAIL"
                failure = failure or RuntimeError("Preservation or cleanup failed")
    if failure is not None:
        raise RuntimeError("Maintained Owner embedding smoke failed: " + str(evidence_path)) from failure
    print("PASS: maintained Owner embedding evidence: " + str(evidence_path))


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("Supply a new evidence path")
    host(Path(sys.argv[1]))

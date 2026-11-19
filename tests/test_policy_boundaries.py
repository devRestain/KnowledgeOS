"""Automatic Curation never widens its remote_ok scope when a role uses a local route."""
from __future__ import annotations

import json
import shutil

import pytest
from conftest import (
    classifier_script,
    conforming_note,
    enhance_scripts,
    evaluator_script,
    organize_scripts,
)

from knowledgeos.config import ROOT

TARGET = "00_Inbox/Boundary.md"
CANDIDATE = "40_Knowledge/Notes/Allowed.md"
PRIVATE = "40_Knowledge/Notes/Local.md"
ASK = "40_Knowledge/Notes/Ask.md"
LOCAL_SENTINEL = "LOCAL_ONLY_BOUNDARY_SENTINEL_137"
ASK_SENTINEL = "ASK_BOUNDARY_SENTINEL_829"


@pytest.fixture
def local_control(tmp_path):
    control = tmp_path / "control"
    shutil.copytree(ROOT / "config", control / "config")
    shutil.copytree(ROOT / "roles", control / "roles")
    roles_path = control / "config/roles.json"
    declarations = json.loads(roles_path.read_text())
    for declaration in declarations["roles"].values():
        declaration["local"] = True
    roles_path.write_text(json.dumps(declarations))
    # Exercise gateway Work policy through public admission without changing production
    # routing: this disposable catalog admits organize for the test Operation as well.
    catalog_path = control / "config/capabilities/C1.json"
    catalog = json.loads(catalog_path.read_text())
    catalog["commands"]["organize"]["origins"].append("gateway")
    catalog_path.write_text(json.dumps(catalog))
    return control


def seed(vault):
    vault.write(TARGET, conforming_note("capture", "unprocessed", policy="remote_ok",
                                      body="Boundary tools support thought. [[Allowed]]\n"))
    vault.write(CANDIDATE, conforming_note("knowledge", "seed", policy="remote_ok",
                                         body="Boundary notebooks support [[Boundary]].\n"))
    vault.write(PRIVATE, conforming_note("knowledge", "seed", policy="local_only",
                                       extra=f"title: {LOCAL_SENTINEL}\ntags: [local-boundary-tag]\n",
                                       body=f"Boundary {LOCAL_SENTINEL} [[Boundary]]\n"))
    vault.write(ASK, conforming_note("knowledge", "seed", policy="ask",
                                   extra=f"title: {ASK_SENTINEL}\ntags: [ask-boundary-tag]\n",
                                   body=f"Boundary {ASK_SENTINEL} [[Boundary]]\n"))


def run_curation(kos, origin):
    if origin == "schedule":
        return kos.run("organize.auto", {}, actor="schedule/curate")
    return kos.run("organize", {"paths": [TARGET]}, actor="operation/policy-test")


@pytest.mark.parametrize("origin", ["schedule", "gateway"])
def test_automatic_curation_local_roles_cannot_search_related_or_context_private_notes(
    make_kos, vault, local_control, origin,
):
    seed(vault)
    kos = make_kos(root=local_control)
    organize_scripts(kos.runner)
    disclosed = []
    visited = set()
    for role, original in list(kos.runner.scripts.items()):
        def recording(session, original=original):
            assert session.binding.local is True
            visited.add(session.role)
            call = session.call

            def capture(name, **args):
                value = call(name, **args)
                disclosed.append(value)
                return value

            session.call = capture
            if session.role in ("classifier", "linker"):
                context = session.call("work_context")
                for query in ("Boundary", LOCAL_SENTINEL, ASK_SENTINEL):
                    for result in session.call("search", query=query)["results"]:
                        session.call("read", handle=result["handle"])
                for target in context["targets"]:
                    session.call("read", handle=target["target"])
                    related = session.call("related", handle=target["target"])
                    for result in related["results"]:
                        session.call("read", handle=result["handle"])
            original(session)

        kos.runner.script(role, recording)

    receipt = run_curation(kos, origin)
    assert receipt["status"] == "completed", receipt
    assert visited == {"curation_manager", "classifier", "linker", "eval_officer"}
    output = json.dumps(disclosed)
    for secret in (LOCAL_SENTINEL, ASK_SENTINEL, "local-boundary-tag", "ask-boundary-tag"):
        assert secret not in output
    work = kos.get(f"/api/works/{receipt['work_id']}")[1]
    assert work["workspec"]["origin"] == origin
    assert {entry["path"] for entry in work["evidence"].values()} == {TARGET, CANDIDATE}


@pytest.mark.parametrize("origin", ["schedule", "gateway"])
@pytest.mark.parametrize("restricted", ["local_only", "ask"])
@pytest.mark.parametrize("source", ["snapshot", "current"])
def test_automatic_curation_rechecks_snapshot_and_current_reads_for_local_roles(
    make_kos, vault, local_control, origin, restricted, source,
):
    seed(vault)
    kos = make_kos(root=local_control)
    organize_scripts(kos.runner, graph=("scout", "classifier"))
    observed = []

    def classifier(session):
        context = session.call("work_context")
        if source == "snapshot":
            path, handle = TARGET, context["targets"][0]["target"]
        else:
            path = CANDIDATE
            [hit] = session.call("search", query="notebooks")["results"]
            handle = hit["handle"]
        original = vault.text(path)
        sentinel = "NEWLY_RESTRICTED_BOUNDARY_SENTINEL_513"
        vault.write(path, original.replace("ai_policy: remote_ok", f"ai_policy: {restricted}") + sentinel + "\n")
        try:
            result = session.call("read", handle=handle)
            if source == "snapshot":
                with pytest.raises((LookupError, RuntimeError), match="privacy"):
                    session.call("work_context")
            assert result["status"] == "unavailable", result
            assert "privacy" in result["reason"]
            assert sentinel not in json.dumps(result)
            related = session.call("related", handle=handle)
            assert related["status"] == "unavailable" and related["results"] == []
            assert session.call("search", query=sentinel)["results"] == []
            observed.append(result)
        finally:
            vault.write(path, original)
        classifier_script()(session)

    kos.runner.script("classifier", classifier)
    receipt = run_curation(kos, origin)
    assert receipt["status"] == "completed", receipt
    assert len(observed) == 1


@pytest.mark.parametrize("origin", ["schedule", "gateway"])
@pytest.mark.parametrize("restricted", ["local_only", "ask"])
def test_eval_view_never_discloses_newly_restricted_retained_excerpts_to_local_eval(
    make_kos, vault, local_control, origin, restricted,
):
    seed(vault)
    kos = make_kos(root=local_control)
    organize_scripts(kos.runner, graph=("scout", "classifier"))
    inspected = []

    def classifier(session):
        context = session.call("work_context")
        target = context["targets"][0]
        [hit] = session.call("search", query="notebooks")["results"]
        session.call("read", handle=hit["handle"])
        session.call("submit_handoff", payload={"notes": [{
            "target": target["target"], "type": "idea", "status": "seed", "title": "Boundary",
            "tags": [], "folder": target["folders"]["idea"],
            "evidence": [target["target"], hit["handle"]],
        }]})

    def evaluator(session):
        original = vault.text(CANDIDATE)
        vault.write(CANDIDATE, original.replace("ai_policy: remote_ok", f"ai_policy: {restricted}"))
        try:
            try:
                view = session.call("eval_view")
            except (LookupError, RuntimeError) as error:
                assert "privacy" in str(error)
                inspected.append("refused")
                raise
            excerpts = view["evidence"]
            assert any(entry.get("status") == "unavailable" for entry in excerpts)
            assert "Boundary notebooks" not in json.dumps(excerpts)
            inspected.append("unavailable")
        finally:
            vault.write(CANDIDATE, original)
        evaluator_script()(session)

    kos.runner.script("classifier", classifier)
    kos.runner.script("eval_officer", evaluator)
    receipt = run_curation(kos, origin)
    assert inspected
    expected = "failed" if "refused" in inspected else "completed"
    assert receipt["status"] == expected, receipt


def test_c4_local_role_keeps_local_only_access(make_kos, vault, local_control):
    seed(vault)
    kos = make_kos(root=local_control)
    enhance_scripts(kos.runner)
    observed = []
    original = kos.runner.scripts["connector"]

    def connector(session):
        assert session.binding.local is True
        [hit] = session.call("search", query=LOCAL_SENTINEL)["results"]
        read = session.call("read", handle=hit["handle"])
        assert read["status"] == "ok" and LOCAL_SENTINEL in json.dumps(read)
        observed.append(read)
        original(session)

    kos.runner.script("connector", connector)
    receipt = kos.run("enhance", {"mode": "develop", "path": TARGET})
    assert receipt["status"] == "completed", receipt
    assert len(observed) == 1

"""Real native Activity failure and admitted recovery; no provider or external effects.

Run against the disposable compose.team-smoke server. The synthetic runner uses
production scoped Owner tools and creates a real Pending effect; only the fault
(after its committed evidence) is injected. Native SDK failure classification,
owner recovery admission/application and operation recover are unmodified.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from uuid import uuid4

from test_curation_work import started

from vaultops.adapters.core import digest
from vaultops.application import (
    curation_temporal,
    curation_work,
    curation_workflow,
    knowledge,
    native_lifecycle,
)
from vaultops.application.curation_temporal import team_worker_components
from vaultops.application.curation_work import CurationTeamExecutor
from vaultops.application.exops import OwnerActor
from vaultops.application.knowledge import KnowledgeApplication
from vaultops.application.temporal_contract import CURATION_RECOVERY_QUERY, CURATION_RECOVERY_UPDATE


class FailureAfterCommit(CurationTeamExecutor):
    producer_stopped = False

    async def normalization_proposal(self, work_run_id):
        try:
            await super().normalization_proposal(work_run_id)
            raise RuntimeError("Disposable native fault after committed Owner evidence")
        finally:
            self.producer_stopped = True


async def run(target: str) -> dict:
    from temporalio.api.workflowservice.v1 import RegisterNamespaceRequest
    from temporalio.client import Client
    from temporalio.worker import Replayer, Worker

    namespace = "kos-lifecycle-" + uuid4().hex[:16]
    initial = await Client.connect(target, namespace="default", tls=False)
    await initial.workflow_service.register_namespace(RegisterNamespaceRequest(
        namespace=namespace, workflow_execution_retention_period={"seconds": 86400}))
    sdk = await Client.connect(target, namespace=namespace, tls=False)
    with tempfile.TemporaryDirectory(prefix="knowledgeos-native-lifecycle-") as directory:
        exops, producer, spec, run, source, admission = started(Path(directory), worker_start=False)
        native_proof = {}

        def quiesced(selected):
            return (executor.producer_stopped
                    and native_proof.get("target") == selected
                    and native_proof.get("event_type") == "EVENT_TYPE_ACTIVITY_TASK_FAILED")

        executor = FailureAfterCommit(exops, runner=producer.runner, recovery_quiesced=quiesced)
        workflow, plugin = team_worker_components(exops, executor)
        dispatch = {
            **{key: admission[key] for key in (
                "owner_operation_id", "work_run_id", "action_id", "content_digest")},
            "work_spec_digest": spec["spec_digest"],
        }
        before_source = source.read_bytes()
        async with Worker(sdk, task_queue="knowledgeos.work.v1", workflows=[workflow],
                          plugins=[plugin], identity=exops.worker_identity):
            handle = await sdk.start_workflow(workflow.run, dispatch,
                                             id="knowledgeos/" + run["work_run_id"],
                                             task_queue="knowledgeos.work.v1")
            async with asyncio.timeout(120):
                while True:
                    status = await handle.query(CURATION_RECOVERY_QUERY)
                    if status["status"] == "waiting":
                        break
                    await asyncio.sleep(0.1)
            history = await handle.fetch_history()
            scheduled = {event.event_id: event.activity_task_scheduled_event_attributes
                         for event in history.events
                         if event.HasField("activity_task_scheduled_event_attributes")}
            failures = [event for event in history.events
                        if event.HasField("activity_task_failed_event_attributes")]
            assert len(failures) == 1
            failed = failures[0]
            activity = scheduled[failed.activity_task_failed_event_attributes.scheduled_event_id]
            assert activity.activity_id == status["failed_activity_id"]
            assert activity.activity_type.name.endswith(".normalization_proposal")
            assert executor.producer_stopped
            app = KnowledgeApplication.from_roots(exops.application.roots)
            snapshot = app.journal.read()
            record = snapshot["intents"]["exops.work." + run["work_run_id"]]
            marker = record["curation_native_failure"]
            assert marker["kind"] == "failure"
            assert marker["target"]["temporal_run_id"] == status["temporal_run_id"]
            assert marker["target"]["activity_id"] == activity.activity_id
            assert snapshot["receipts"]["exops.work." + admission["action_id"]]["receipt"]["outcome"] == "outcome_unknown"
            report, code = app.recover()
            assert code == 0, report
            assert report["experience"] == {"observed": 1, "skipped_unavailable": 0,
                                             "deferred": 0, "produced": 1, "quarantined": 0}
            failure_event = app.journal.read()["experience_events"][0]
            native_proof.update(target=marker["target"], event_type="EVENT_TYPE_ACTIVITY_TASK_FAILED",
                                event_id=failed.event_id)
            payload = executor.request_recovery(
                run["work_run_id"], actor=OwnerActor("owner.user", "cli"),
                recovery_id="recovery.native.lifecycle", target=marker["target"])
            receipt = await handle.execute_update(CURATION_RECOVERY_UPDATE, payload,
                                                  id="recovery.native.lifecycle")
            assert await handle.result() == receipt
            history = await handle.fetch_history()
        # Observe after Worker shutdown, then reconstruct all application objects.
        before_scan = app.journal.read()
        reconstructed = KnowledgeApplication.from_roots(app.roots)
        recovered, code = reconstructed.recover()
        assert code == 0, recovered
        assert recovered["experience"] == {"observed": 1, "skipped_unavailable": 0,
                                            "deferred": 0, "produced": 1, "quarantined": 0}
        final = reconstructed.journal.read()
        assert final["intents"] == before_scan["intents"]
        assert final["receipts"] == before_scan["receipts"]
        assert final["experience_events"][0] == failure_event
        assert [event["category"] for event in final["experience_events"]] == ["failure", "crash_recovery"]
        assert all(event["evidence_references"][0]["availability"] == "unavailable"
                   for event in final["experience_events"])
        assert all(not candidate["evidence_references"] for candidate in final["experience"])
        assert len(final["experience"]) == len(final["outbox"]) == 2
        repeated, code = KnowledgeApplication.from_roots(app.roots).recover()
        assert code == 0 and repeated["experience"] == {
            "observed": 0, "skipped_unavailable": 0, "deferred": 0,
            "produced": 0, "quarantined": 0,
        }
        assert app.journal.read() == final
        assert len(producer.runner.requests) == 1
        assert source.read_bytes() == before_source
        # Real native history replay must not write owner State or rerun tools.
        replay_workflow, replay_plugin = team_worker_components(exops, executor)
        with ThreadPoolExecutor(max_workers=2) as pool:
            replay = await Replayer(workflows=[replay_workflow], plugins=[replay_plugin],
                                    namespace=namespace, data_converter=sdk.data_converter,
                                    workflow_task_executor=pool).replay_workflow(history)
        assert replay.replay_failure is None
        assert app.journal.read() == final and len(producer.runner.requests) == 1
        result = {
            "status": "PASS", "namespace": namespace,
            "scope": "disposable native Activity failure and owner-admitted recovery sensors",
            "real_provider_called": False, "ordinary_success_learned": False,
            "effect_outcome_at_failure": "outcome_unknown",
            "native_failure_event_id": failed.event_id,
            "native_activity_id": activity.activity_id,
            "temporal_run_id": status["temporal_run_id"],
            "history_events": len(history.events), "history_replayed": True,
            "recovery_receipt": receipt,
            "failure_report": report, "recovery_report": recovered, "dedupe_report": repeated,
            "source_events": final["experience_events"], "candidates": final["experience"],
            "historical_evidence_unchanged": True, "effect_invocations": 1,
            "implementation_digests": {
                Path(module.__file__).name: digest(Path(module.__file__).read_bytes())
                for module in (curation_work, curation_temporal, curation_workflow,
                               knowledge, native_lifecycle)
            },
            "excluded_claims": ["real model", "production", "GUI", "cancellation learning",
                                "timeout timing", "human correction", "policy denial",
                                "verified unusual success", "journal evidence export"],
        }
    result["disposable_roots_removed"] = not Path(directory).exists()
    assert result["disposable_roots_removed"]
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", default="server:7233")
    args = parser.parse_args()
    print("KNOWLEDGEOS_NATIVE_LIFECYCLE=" + json.dumps(asyncio.run(run(args.target)), sort_keys=True))


if __name__ == "__main__":
    main()

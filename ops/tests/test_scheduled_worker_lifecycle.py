from __future__ import annotations

from pathlib import Path

import yaml

SPECIFICATION = yaml.safe_load(
    (Path(__file__).resolve().parents[2] / "ops/config/scheduled-worker.yaml").read_text(
        encoding="utf-8"
    )
)


class SchedulerContractDouble:
    """Model only the frozen catch-up rule while leaving Hermes uninstalled."""

    def __init__(self, maximum_catch_up_runs: int) -> None:
        self.maximum_catch_up_runs = maximum_catch_up_runs

    def coalesced_runs(self, due_ticks: int) -> int:
        return min(max(due_ticks, 0), self.maximum_catch_up_runs)


def test_missed_ticks_coalesce_to_one_bounded_dispatch() -> None:
    scheduler = SchedulerContractDouble(
        SPECIFICATION["trigger"]["maximum_catch_up_runs"]
    )

    assert scheduler.coalesced_runs(0) == 0
    assert scheduler.coalesced_runs(1) == 1
    assert scheduler.coalesced_runs(7) == 1
    assert SPECIFICATION["trigger"]["interval_seconds"] == 300


def test_scheduler_contract_keeps_all_limits_finite() -> None:
    limits = SPECIFICATION["limits"]

    assert limits["request_count"] == 100
    assert limits["deadline_seconds"] == 240
    assert limits["cancellation_grace_seconds"] == 10
    assert limits["concurrent_workers"] == 1
    assert limits["lock_wait_seconds"] == 0
    assert limits["stdout_bytes"] == 8192
    assert limits["stderr_bytes"] == 8192

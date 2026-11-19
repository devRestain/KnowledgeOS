"""Job schedule slots (C7): a five-field cron expression limited to minute and hour fields.

Day-of-month, month and weekday must be ``*``: every declared job runs hourly or daily.
Times are Asia/Seoul wall-clock times, the Owner's time zone.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta

from .util import SEOUL

PART = re.compile(r"^(?:\*|(\d+)(?:-(\d+))?)(?:/(\d+))?$")


def _field(text: str, low: int, high: int, expression: str) -> set[int]:
    values: set[int] = set()
    for part in text.split(","):
        match = PART.match(part)
        if match is None:
            raise ValueError(f"unsupported job schedule: {expression}")
        first, last, step = match.groups()
        start = int(first) if first else low
        end = int(last) if last else high if not first or step else start
        increment = int(step) if step else 1
        if not low <= start <= end <= high or increment < 1:
            raise ValueError(f"unsupported job schedule: {expression}")
        values.update(range(start, end + 1, increment))
    return values


class Schedule:
    """The slots of one declared schedule, such as ``30 * * * *`` or ``0 6 * * *``."""

    def __init__(self, expression: str):
        fields = expression.split()
        if len(fields) != 5 or fields[2:] != ["*", "*", "*"]:
            raise ValueError(f"unsupported job schedule: {expression}")
        minutes = _field(fields[0], 0, 59, expression)
        hours = _field(fields[1], 0, 23, expression)
        self._times = sorted((hour, minute) for hour in hours for minute in minutes)

    def latest(self, now: datetime) -> datetime:
        """The most recent slot at or before ``now``."""
        local = now.astimezone(SEOUL)
        for back in (0, 1):
            day = (local - timedelta(days=back)).replace(hour=0, minute=0, second=0, microsecond=0)
            for hour, minute in reversed(self._times):
                slot = day.replace(hour=hour, minute=minute)
                if slot <= local:
                    return slot
        raise AssertionError("a schedule has a slot every day")

    def next(self, now: datetime) -> datetime:
        """The first slot after ``now``."""
        local = now.astimezone(SEOUL)
        for ahead in (0, 1):
            day = (local + timedelta(days=ahead)).replace(hour=0, minute=0, second=0, microsecond=0)
            for hour, minute in self._times:
                slot = day.replace(hour=hour, minute=minute)
                if slot > local:
                    return slot
        raise AssertionError("a schedule has a slot every day")

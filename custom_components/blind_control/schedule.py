"""Schedule rules: time windows that tell a blind what to do.

Pure Python (no Home Assistant imports) so it can be unit-tested directly.

A rule is active from ``start`` to ``end`` on the selected weekdays (the
weekday of the *start* counts, so ``sunset -> sunrise`` on Friday ends on
Saturday morning). Start and end may be a fixed time or sunrise/sunset with an
offset in minutes. When several rules are active the one with the highest
priority wins; ties go to the rule listed first.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, tzinfo
from typing import Any

DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")

KIND_TIME = "time"
KIND_SUNRISE = "sunrise"
KIND_SUNSET = "sunset"

SunTimes = Callable[[date], tuple[datetime | None, datetime | None]]


@dataclass(frozen=True, slots=True)
class TimePoint:
    """A fixed time of day or a sun event with an offset."""

    kind: str = KIND_TIME
    at: time = time(0, 0)
    offset: int = 0  # minutes

    def resolve(self, day: date, tz: tzinfo, sun_times: SunTimes) -> datetime | None:
        if self.kind == KIND_TIME:
            base: datetime | None = datetime.combine(day, self.at, tzinfo=tz)
        else:
            sunrise, sunset = sun_times(day)
            base = sunrise if self.kind == KIND_SUNRISE else sunset
            if base is None:  # polar day / night
                return None
        return base + timedelta(minutes=self.offset)


@dataclass(frozen=True, slots=True)
class Rule:
    """One schedule entry."""

    id: str
    name: str
    days: frozenset[int]
    start: TimePoint
    end: TimePoint
    action: str
    position: int | None = None
    tilt: int | None = None
    priority: int = 50
    condition: str | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Rule:
        return cls(
            id=data["id"],
            name=data.get("name") or data["id"],
            days=frozenset(DAYS.index(d) for d in data.get("days", DAYS) if d in DAYS),
            start=_point(data, "start"),
            end=_point(data, "end"),
            action=data.get("action", "auto"),
            position=_opt_int(data.get("position")),
            tilt=_opt_int(data.get("tilt")),
            priority=int(data.get("priority", 50)),
            condition=(data.get("condition") or "").strip() or None,
        )

    def intervals(
        self, now: datetime, sun_times: SunTimes
    ) -> Iterator[tuple[datetime, datetime]]:
        """Yield the rule's windows that start yesterday, today or tomorrow."""
        tz = now.tzinfo
        assert tz is not None
        today = now.date()
        for delta in (-1, 0, 1):
            day = today + timedelta(days=delta)
            if day.weekday() not in self.days:
                continue
            start = self.start.resolve(day, tz, sun_times)
            end = self.end.resolve(day, tz, sun_times)
            if start is None or end is None:
                continue
            if end <= start:
                end = self.end.resolve(day + timedelta(days=1), tz, sun_times)
                if end is None or end <= start:
                    continue
            yield start, end

    def is_active(self, now: datetime, sun_times: SunTimes) -> bool:
        return any(start <= now < end for start, end in self.intervals(now, sun_times))


def _point(data: dict[str, Any], prefix: str) -> TimePoint:
    raw = data.get(f"{prefix}_time") or "00:00:00"
    return TimePoint(
        kind=data.get(f"{prefix}_type", KIND_TIME),
        at=time.fromisoformat(raw) if isinstance(raw, str) else raw,
        offset=int(data.get(f"{prefix}_offset") or 0),
    )


def _opt_int(value: Any) -> int | None:
    return None if value in (None, "") else int(value)


def active_rule(
    rules: Iterable[Rule],
    now: datetime,
    sun_times: SunTimes,
    condition_ok: Callable[[Rule], bool] = lambda _: True,
) -> Rule | None:
    """Return the winning active rule, or None."""
    best: Rule | None = None
    for rule in rules:
        if best is not None and rule.priority <= best.priority:
            continue
        # Cheap time check first, templates only when the window matches.
        if rule.is_active(now, sun_times) and (rule.condition is None or condition_ok(rule)):
            best = rule
    return best


def next_boundary(rules: Iterable[Rule], now: datetime, sun_times: SunTimes) -> datetime | None:
    """Earliest future start or end of any rule (to wake up exactly on time)."""
    upcoming = [
        moment
        for rule in rules
        for interval in rule.intervals(now, sun_times)
        for moment in interval
        if moment > now
    ]
    return min(upcoming, default=None)


def default_rules() -> list[dict[str, Any]]:
    """Rules created for a new blind: closed from sunset until sunrise."""
    return [
        {
            "id": "night",
            "name": "Night",
            "days": list(DAYS),
            "start_type": KIND_SUNSET,
            "start_time": "00:00:00",
            "start_offset": 0,
            "end_type": KIND_SUNRISE,
            "end_time": "00:00:00",
            "end_offset": 0,
            "action": "close",
            "priority": 50,
        }
    ]

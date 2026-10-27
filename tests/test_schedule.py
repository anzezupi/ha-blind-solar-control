"""Tests for schedule rules."""

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from schedule import Rule, active_rule, default_rules, next_boundary

TZ = ZoneInfo("Europe/Ljubljana")


def sun_times(day: date):
    return (
        datetime.combine(day, time(6, 30), TZ),
        datetime.combine(day, time(19, 0), TZ),
    )


def at(day: int, hh: int, mm: int = 0) -> datetime:
    # 2026-10-26 is a Monday
    return datetime.combine(date(2026, 10, 26) + timedelta(days=day), time(hh, mm), TZ)


def rule(**kw) -> Rule:
    data = {
        "id": kw.pop("id", "r"),
        "name": kw.pop("name", "Rule"),
        "start_type": "time",
        "start_time": "08:00:00",
        "end_type": "time",
        "end_time": "12:00:00",
        "action": "open",
    }
    data.update(kw)
    return Rule.from_dict(data)


def test_default_night_rule_overnight():
    night = Rule.from_dict(default_rules()[0])
    assert night.is_active(at(0, 22), sun_times)
    assert night.is_active(at(1, 5), sun_times)  # Tuesday early morning
    assert not night.is_active(at(1, 7), sun_times)
    assert not night.is_active(at(1, 18, 59), sun_times)


def test_sun_offset():
    r = rule(start_type="sunrise", start_offset=30)
    assert not r.is_active(at(0, 6, 45), sun_times)
    assert r.is_active(at(0, 7, 1), sun_times)


def test_weekday_refers_to_start():
    # Friday overnight rule must still be active on Saturday morning.
    r = rule(days=["fri"], start_time="22:00:00", end_time="09:00:00")
    assert r.is_active(at(5, 8), sun_times)  # Saturday 08:00
    assert not r.is_active(at(6, 8), sun_times)  # Sunday 08:00


def test_priority_and_order():
    low = rule(id="a", name="A", priority=10)
    high = rule(id="b", name="B", priority=90)
    tie = rule(id="c", name="C", priority=10)
    assert active_rule([low, high, tie], at(0, 9), sun_times).name == "B"
    assert active_rule([low, tie], at(0, 9), sun_times).name == "A"
    assert active_rule([low], at(0, 13), sun_times) is None


def test_condition_callback():
    r = rule(condition="{{ false }}")
    assert active_rule([r], at(0, 9), sun_times, lambda _: False) is None
    assert active_rule([r], at(0, 9), sun_times, lambda _: True) is r


def test_next_boundary():
    r = rule()
    assert next_boundary([r], at(0, 7), sun_times) == at(0, 8)
    assert next_boundary([r], at(0, 9), sun_times) == at(0, 12)
    assert next_boundary([r], at(0, 13), sun_times) == at(1, 8)


def test_polar_day_is_ignored():
    r = rule(start_type="sunrise")
    assert not r.is_active(at(0, 9), lambda _d: (None, None))


def test_dst_change_is_handled():
    # 2026-10-25 is the DST change in Europe; an 02:00-03:00 rule must not crash.
    r = rule(start_time="02:00:00", end_time="03:00:00")
    moment = datetime(2026, 10, 25, 2, 30, tzinfo=TZ)
    assert r.is_active(moment, sun_times)
    assert next_boundary([r], moment, sun_times) - moment <= timedelta(hours=1)

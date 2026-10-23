from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml

from nullone.config import CALENDAR_DIR

ET = ZoneInfo("America/New_York")


@dataclass(frozen=True)
class ReleaseWindow:
    week_ending: date
    release_at: datetime

    @property
    def release_at_et(self) -> datetime:
        return self.release_at


def _load_schedule(path: Path | None = None) -> dict:
    schedule_path = path or CALENDAR_DIR / "wngsr_schedule.yaml"
    with schedule_path.open() as f:
        return yaml.safe_load(f)


def _parse_time(value: str) -> time:
    hour, minute = value.split(":")
    return time(int(hour), int(minute))


def release_for_week_ending(
    week_ending: date,
    schedule: dict | None = None,
) -> ReleaseWindow:
    """Return the scheduled release datetime (ET) for a given week-ending Friday."""
    schedule = schedule or _load_schedule()
    default_time = _parse_time(schedule["default"]["time"])
    overrides = schedule.get("overrides", [])

    # Normal cadence: report drops six days after the week-ending Friday (Thursday).
    nominal = week_ending + timedelta(days=6)
    window_start = week_ending + timedelta(days=4)
    window_end = week_ending + timedelta(days=8)

    override_match = None
    for item in overrides:
        override_date = date.fromisoformat(item["date"])
        if window_start <= override_date <= window_end:
            override_match = item
            break

    if override_match:
        release_date = date.fromisoformat(override_match["date"])
        release_time = _parse_time(override_match["time"])
    else:
        release_date = nominal
        release_time = default_time

    release_at = datetime.combine(release_date, release_time, tzinfo=ET)
    return ReleaseWindow(week_ending=week_ending, release_at=release_at)


def next_release_after(
    after: datetime | None = None,
    schedule: dict | None = None,
) -> ReleaseWindow:
    """Find the next WNGSR release after a given moment (defaults to now ET)."""
    schedule = schedule or _load_schedule()
    now = after or datetime.now(tz=ET)
    if now.tzinfo is None:
        now = now.replace(tzinfo=ET)
    else:
        now = now.astimezone(ET)

    # WNGSR week ends on Friday; search forward up to 14 days.
    start = now.date()
    for offset in range(15):
        week_friday = start + timedelta(days=offset)
        if week_friday.weekday() != 4:
            continue
        window = release_for_week_ending(week_friday, schedule)
        if window.release_at > now:
            return window

    raise RuntimeError("Could not find upcoming WNGSR release within 14 days")


def seconds_until_release(release_at: datetime, now: datetime | None = None) -> float:
    now = now or datetime.now(tz=ET)
    if now.tzinfo is None:
        now = now.replace(tzinfo=ET)
    else:
        now = now.astimezone(ET)
    target = release_at.astimezone(ET)
    return (target - now).total_seconds()

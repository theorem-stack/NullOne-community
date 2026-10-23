from datetime import datetime
from zoneinfo import ZoneInfo

from nullone.calendar.scheduler import release_for_week_ending

ET = ZoneInfo("America/New_York")


def test_default_thursday_release():
    # Week ending Friday 2026-05-15 -> release Thursday 2026-05-21 10:30 ET
    window = release_for_week_ending(datetime(2026, 5, 15).date())
    assert window.release_at.date().isoformat() == "2026-05-21"
    assert window.release_at.weekday() == 3  # Thursday
    assert window.release_at.hour == 10
    assert window.release_at.minute == 30
    assert window.release_at.tzinfo == ET


def test_thanksgiving_override():
    # 2026 Thanksgiving week: release Wed Nov 25 at 12:00 ET
    window = release_for_week_ending(datetime(2026, 11, 20).date())
    assert window.release_at.date().isoformat() == "2026-11-25"
    assert window.release_at.hour == 12
    assert window.release_at.minute == 0

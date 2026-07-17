"""The daily event reminder must fire at the configured LOCAL time, not UTC.

Regression: `tasks.loop(time=...)` treats a naive time as UTC, so a 19:30
config fired at 21:30 Europe/Berlin. The loop time must carry tzinfo.
"""
from datetime import time
from zoneinfo import ZoneInfo

from n3x_bot.bot import reminder_loop_time
from n3x_bot.config import Settings

BASE = dict(discord_token="t", target_role_id=1, welcome_channel_id=2,
            reminder_channel_id=3, julez_id=4, _env_file=None,
            _env_prefix="NONEXISTENT_")


def _settings(**o):
    return Settings(**{**BASE, **o})


class _RC:
    def __init__(self, h, m):
        self._t = (h, m)

    def reminder_hm(self):
        return self._t


def test_reminder_loop_time_is_tz_aware_at_configured_local_time():
    s = _settings(timezone="Europe/Berlin", reminder_time="19:30")
    t = reminder_loop_time(_RC(19, 30), s)
    assert isinstance(t, time)
    assert (t.hour, t.minute) == (19, 30)
    assert t.tzinfo == ZoneInfo("Europe/Berlin")


def test_reminder_loop_time_follows_timezone_setting():
    s = _settings(timezone="America/New_York", reminder_time="08:00")
    t = reminder_loop_time(_RC(8, 0), s)
    assert t.tzinfo == ZoneInfo("America/New_York")
    assert (t.hour, t.minute) == (8, 0)

"""gate_entries.created_at is UTC; the chart must render LOCAL wall-clock."""
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from n3x_bot.charts import _to_local_naive, render_gate_history_chart

BERLIN = ZoneInfo("Europe/Berlin")


def test_utc_timestamp_converts_to_local_naive():
    # 22:30 UTC on 2026-07-01 is 00:30 on 2026-07-02 in Berlin (CEST, +2).
    utc = datetime(2026, 7, 1, 22, 30, tzinfo=timezone.utc)
    local = _to_local_naive(utc, BERLIN)
    assert local.tzinfo is None                 # naive so matplotlib won't re-UTC it
    assert (local.year, local.month, local.day) == (2026, 7, 2)   # DATE rolled over
    assert (local.hour, local.minute) == (0, 30)                  # local wall-clock


def test_none_and_no_tz_passthrough():
    assert _to_local_naive(None, BERLIN) is None
    naive = datetime(2026, 7, 1, 12, 0)
    assert _to_local_naive(naive, None) == naive  # nothing to convert


def test_render_with_utc_entries_still_valid_png():
    from io import BytesIO
    from PIL import Image
    now = datetime(2026, 7, 15, tzinfo=BERLIN)
    entries = [{"cost": 46000,
                "created_at": datetime(2026, 7, 1, 22, 30, tzinfo=timezone.utc),
                "drops": {}}]
    png = render_gate_history_chart("a", entries, now)
    assert Image.open(BytesIO(png)).format == "PNG"


def test_aggregate_by_day_means_per_local_day():
    from n3x_bot.charts import aggregate_by_day
    e = [
        {"cost": 100, "created_at": datetime(2026, 7, 1, 8, 0, tzinfo=timezone.utc), "drops": {}},
        {"cost": 200, "created_at": datetime(2026, 7, 1, 20, 0, tzinfo=timezone.utc), "drops": {}},
        {"cost": 400, "created_at": datetime(2026, 7, 2, 9, 0, tzinfo=timezone.utc), "drops": {}},
    ]
    from datetime import date
    days, means, dmap = aggregate_by_day(e, BERLIN)
    assert days == [date(2026, 7, 1), date(2026, 7, 2)]
    assert means == [150.0, 400.0]        # day-1 mean of 100/200
    assert dmap[date(2026, 7, 1)] == 150.0


def test_aggregate_by_day_empty():
    from n3x_bot.charts import aggregate_by_day
    assert aggregate_by_day([], BERLIN) == ([], [], {})


def test_render_daily_chart_valid_png_multiple_days():
    from io import BytesIO
    from PIL import Image
    now = datetime(2026, 7, 15, tzinfo=BERLIN)
    e = [
        {"cost": 100, "created_at": datetime(2026, 7, 1, 8, 0, tzinfo=timezone.utc), "drops": {"laser": True}},
        {"cost": 200, "created_at": datetime(2026, 7, 2, 8, 0, tzinfo=timezone.utc), "drops": {}},
    ]
    png = render_gate_history_chart("d", e, now)
    assert Image.open(BytesIO(png)).format == "PNG"

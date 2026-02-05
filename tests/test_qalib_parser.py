from datetime import time, timedelta

from munazzim.models import FixedEvent
from munazzim.models import PrayerBoundEvent
from munazzim.qalib import parse_qalib, render_template


SAMPLE = """# name: Sample Day
# description: Simple qalib for tests
05:00
.30 Warmup
- [5] Stretch :: Focus on posture
7:00 07:45 Breakfast
1 Study Block
"""


def test_parse_qalib_template() -> None:
    template = parse_qalib(SAMPLE, default_name="Fallback")
    assert template.name == "Sample Day"
    assert template.description.startswith("Simple qalib")
    assert template.start_time == time(5, 0)
    assert len(template.events) == 3
    relative = template.events[0]
    assert relative.duration == timedelta(minutes=30)
    assert relative.tasks[0].total_occurrences == 5
    assert relative.tasks[0].remaining_occurrences == 5
    fixed = template.events[1]
    assert isinstance(fixed, FixedEvent)
    assert fixed.anchor == time(7, 0)
    assert fixed.duration == timedelta(minutes=45)
    last = template.events[2]
    assert last.duration == timedelta(hours=1)


def test_qalib_round_trip() -> None:
    template = parse_qalib(SAMPLE, default_name="Fallback")
    rendered = render_template(template)
    regenerated = parse_qalib(rendered, default_name="Other")
    assert regenerated.start_time == template.start_time
    assert regenerated.events[0].duration == template.events[0].duration
    assert regenerated.events[1].duration == template.events[1].duration


def test_qalib_parses_fixed_duration_events() -> None:
    template = parse_qalib("05:00\n12:30 +2 Focus Block\n", default_name="Fixed")
    assert len(template.events) == 1
    fixed = template.events[0]
    assert isinstance(fixed, FixedEvent)
    assert fixed.anchor == time(12, 30)
    assert fixed.duration == timedelta(hours=2)


def test_qalib_parses_prayer_bound_duration_events() -> None:
    template = parse_qalib("05:00\nFajr +2 Dawn Study\n", default_name="PrayerBound")
    assert len(template.events) == 1
    bound = template.events[0]
    assert isinstance(bound, PrayerBoundEvent)
    assert bound.start_ref == "Fajr"
    assert bound.duration == timedelta(hours=2)


def test_qalib_parses_prayer_range_events() -> None:
    template = parse_qalib("05:00\nDhuhr..Asr Reading\n..Maghrib Run\n", default_name="PrayerRange")
    assert len(template.events) == 2
    first = template.events[0]
    assert isinstance(first, PrayerBoundEvent)
    assert first.start_ref == "Dhuhr"
    assert first.end_ref == "Asr"
    second = template.events[1]
    assert isinstance(second, PrayerBoundEvent)
    assert second.start_ref is None
    assert second.end_ref == "Maghrib"


def test_qalib_parses_prayer_offset_ranges() -> None:
    template = parse_qalib("05:00\n..Maghrib-50 Evening Prep\n", default_name="PrayerOffset")
    assert len(template.events) == 1
    event = template.events[0]
    assert isinstance(event, PrayerBoundEvent)
    assert event.start_ref is None
    assert event.end_ref == "Maghrib-50"


def test_qalib_parses_prayer_tokens() -> None:
    from datetime import timedelta
    from munazzim.models import PrayerEvent

    tmpl1 = parse_qalib("05:00\n.30 Fajr\n", default_name="Prayers")
    assert isinstance(tmpl1.events[0], PrayerEvent)
    assert tmpl1.events[0].prayer.lower() == "fajr"
    assert tmpl1.events[0].duration == timedelta(minutes=30)

    tmpl2 = parse_qalib("05:00\n06:30 06:50 Fajr\n", default_name="Prayers2")
    assert isinstance(tmpl2.events[0], PrayerEvent)
    assert tmpl2.events[0].prayer.lower() == "fajr"
    assert tmpl2.events[0].duration == timedelta(minutes=20)


def test_qalib_parses_all_prayer_tokens() -> None:
    from datetime import timedelta
    from munazzim.models import PrayerEvent
    prayers = ["fajr", "dhuhr", "asr", "maghrib", "isha"]
    for p in prayers:
        r = parse_qalib(f"05:00\n.15 {p.title()}\n", default_name="Prayers")
        assert isinstance(r.events[0], PrayerEvent)
        assert r.events[0].prayer.lower() == p
        assert r.events[0].duration == timedelta(minutes=15)
        f = parse_qalib(f"05:00\n06:30 06:45 {p.title()}\n", default_name="Prayers2")
        assert isinstance(f.events[0], PrayerEvent)
        assert f.events[0].prayer.lower() == p
        assert f.events[0].duration == timedelta(minutes=15)


def test_qalib_parses_prayer_headers() -> None:
    template = parse_qalib(
        """
# prayer_durations.fajr: 0:25
# prayer_durations.maghrib: 0:10
# prayer_overrides.asr: dhuhr + 5
# prayer_overrides.isha: 19:50
05:00
.30 Warmup
""",
        default_name="PrayerHeaders",
    )
    assert template.prayer_durations["fajr"] == "0:25"
    assert template.prayer_durations["maghrib"] == "0:10"
    assert template.prayer_overrides["asr"] == "dhuhr + 5"
    assert template.prayer_overrides["isha"] == "19:50"

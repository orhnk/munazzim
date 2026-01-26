from pathlib import Path

from munazzim.templates import TemplateRepository


def test_extensionless_qalib_detected(tmp_path: Path) -> None:
    template_file = tmp_path / "Deneme"
    template_file.write_text(
        """\
5:00
.5 Task
""",
        encoding="utf-8",
    )

    repo = TemplateRepository(tmp_path)

    assert "Deneme" in repo.template_names()
    template = repo.get("Deneme")
    assert template.name == "Deneme"
    assert template.events[0].name == "Task"


def test_invalid_template_surfaces_error(tmp_path: Path) -> None:
    bad_file = tmp_path / "broken.toml"
    bad_file.write_text("name = 'Broken'\n", encoding="utf-8")

    repo = TemplateRepository(tmp_path)

    errors = repo.errors()
    assert errors, "Expected template parse error to be captured"
    assert errors[0].path == bad_file
    assert "missing required" in errors[0].message.lower()


def test_toml_parses_prayer_event_by_name(tmp_path: Path) -> None:
    from munazzim.models import PrayerEvent

    toml_file = tmp_path / "prayer.toml"
    toml_file.write_text(
        """
        name = "PrayerTemplate"
        start_time = "05:00"

        [[events]]
        name = "Fajr"
        duration = "0.30"
        """,
        encoding="utf-8",
    )
    repo = TemplateRepository(tmp_path)
    template = repo.get("PrayerTemplate")
    assert isinstance(template.events[0], PrayerEvent)
    assert template.events[0].prayer.lower() == "fajr"


def test_toml_parses_all_prayer_event_by_name(tmp_path: Path) -> None:
    from munazzim.models import PrayerEvent
    prayers = ["Fajr", "Dhuhr", "Asr", "Maghrib", "Isha"]
    for p in prayers:
        toml_file = tmp_path / f"prayer_{p.lower()}.toml"
        toml_file.write_text(
            f"""
            name = "PrayerTemplate_{p}"
            start_time = "05:00"

            [[events]]
            name = "{p}"
            duration = "0.20"
            """,
            encoding="utf-8",
        )
        repo = TemplateRepository(tmp_path)
        template = repo.get(f"PrayerTemplate_{p}")
        assert isinstance(template.events[0], PrayerEvent)
        assert template.events[0].prayer.lower() == p.lower()

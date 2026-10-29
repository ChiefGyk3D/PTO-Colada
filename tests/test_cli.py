"""Configuration layering and the command-line interface."""

from __future__ import annotations

import datetime as dt
import json

import pytest

from pto_colada.cli import build_parser, main, resolve_window
from pto_colada.config import build_config, parse_date_ranges, parse_pto_by_year


class TestConfig:
    def test_preset_is_the_starting_point(self):
        config = build_config(preset="minimal")
        assert len(config.holidays) == 6

    def test_add_and_drop(self):
        config = build_config(
            preset="federal",
            drop=["Columbus Day"],
            add=["Day After Thanksgiving=Thanksgiving+1"],
        )
        names = config.holiday_names()
        assert "Columbus Day" not in names
        assert "Day After Thanksgiving" in names

    def test_drop_is_case_insensitive(self):
        config = build_config(preset="federal", drop=["columbus day"])
        assert "Columbus Day" not in config.holiday_names()

    def test_drop_accepts_a_comma_separated_list(self):
        config = build_config(preset="federal", drop=["Columbus Day,Veterans Day"])
        names = config.holiday_names()
        assert "Columbus Day" not in names and "Veterans Day" not in names

    def test_dropping_an_unknown_holiday_is_an_error(self):
        with pytest.raises(ValueError, match="not in this set"):
            build_config(preset="federal", drop=["Boxing Day"])

    def test_adding_a_duplicate_is_an_error(self):
        with pytest.raises(ValueError, match="more than once"):
            build_config(preset="federal", add=["Juneteenth=06-19"])

    def test_unknown_preset_lists_the_valid_ones(self):
        with pytest.raises(ValueError, match="federal"):
            build_config(preset="nope")

    def test_blackout_and_committed_cannot_overlap(self):
        with pytest.raises(ValueError, match="both blacked out and committed"):
            build_config(preset="minimal", blackout=["2026-04-01"], committed=["2026-04-01"])

    def test_date_range_expansion(self):
        dates = parse_date_ranges(["2026-04-01:2026-04-03", "2026-05-01"])
        assert dates == {
            dt.date(2026, 4, 1), dt.date(2026, 4, 2), dt.date(2026, 4, 3), dt.date(2026, 5, 1),
        }

    def test_backwards_date_range_is_rejected(self):
        with pytest.raises(ValueError, match="ends before"):
            parse_date_ranges(["2026-04-10:2026-04-01"])

    def test_pto_by_year_parsing(self):
        assert parse_pto_by_year(["2026=15,2027=20"]) == {2026: 15, 2027: 20}

    @pytest.mark.parametrize("bad", ["2026", "2026=abc", "abc=10"])
    def test_bad_pto_by_year_is_rejected(self, bad):
        with pytest.raises(ValueError):
            parse_pto_by_year([bad])


class TestNumericValidation:
    """Budgets and limits are checked even when the planner never runs."""

    def test_negative_pto_is_rejected(self):
        with pytest.raises(ValueError, match="PTO budget cannot be negative"):
            build_config(pto=-3)

    def test_negative_carryover_is_rejected(self):
        with pytest.raises(ValueError, match="carryover cannot be negative"):
            build_config(carryover=-1)

    def test_min_break_below_one_is_rejected(self):
        with pytest.raises(ValueError, match="at least 1"):
            build_config(min_break=0)

    def test_max_consecutive_pto_below_one_is_rejected(self):
        with pytest.raises(ValueError, match="at least 1"):
            build_config(max_consecutive_pto=0)

    def test_negative_per_year_budget_from_a_config_file_is_rejected(self, tmp_path):
        path = tmp_path / "bad.json"
        path.write_text(json.dumps({"pto_by_year": {"2026": -5}}), encoding="utf-8")
        with pytest.raises(ValueError, match="cannot be negative"):
            build_config(config_path=str(path))

    def test_a_zero_budget_is_allowed(self):
        assert build_config(pto=0).pto_days == 0

    @pytest.mark.parametrize(
        "argv",
        [
            ["--pto", "-3"],
            ["--carryover", "-1"],
            ["--min-break", "0"],
            ["--max-consecutive-pto", "0"],
        ],
    )
    def test_the_cli_exits_with_an_error(self, argv):
        with pytest.raises(SystemExit) as excinfo:
            main(argv + ["--years", "1"])
        assert excinfo.value.code == 2


class TestConfigFile:
    def write(self, tmp_path, name, text):
        path = tmp_path / name
        path.write_text(text, encoding="utf-8")
        return str(path)

    def test_json_config(self, tmp_path):
        path = self.write(tmp_path, "acme.json", json.dumps({
            "name": "Acme",
            "preset": "minimal",
            "add": ["Founders Day=06-01"],
            "workweek": "MON-THU",
            "observance": "next",
            "pto_days": 12,
            "carryover": 3,
            "min_break": 5,
        }))
        config = build_config(config_path=path)
        assert config.name == "Acme"
        assert "Founders Day" in config.holiday_names()
        assert config.workweek == {0, 1, 2, 3}
        assert config.pto_days == 12
        assert config.carryover == 3
        assert config.min_break == 5

    def test_holidays_key_replaces_the_preset(self, tmp_path):
        path = self.write(tmp_path, "c.json", json.dumps({
            "holidays": [
                "New Year's Day=01-01",
                {"name": "Thanksgiving", "month": 11, "weekday": "THU", "n": 4},
                {"name": "Day After Thanksgiving", "after": "Thanksgiving", "days": 1},
                {"name": "Shutdown", "date": "2026-12-28"},
            ],
        }))
        config = build_config(config_path=path)
        assert config.holiday_names() == [
            "New Year's Day", "Thanksgiving", "Day After Thanksgiving", "Shutdown",
        ]

    def test_cli_overrides_beat_the_file(self, tmp_path):
        path = self.write(tmp_path, "c.json", json.dumps({"preset": "minimal", "pto_days": 10}))
        config = build_config(config_path=path, pto=25)
        assert config.pto_days == 25

    def test_missing_file(self):
        with pytest.raises(FileNotFoundError):
            build_config(config_path="/nonexistent/company.json")

    def test_invalid_json_is_reported(self, tmp_path):
        path = self.write(tmp_path, "bad.json", "{not json")
        with pytest.raises(ValueError, match="not valid JSON"):
            build_config(config_path=path)

    def test_non_mapping_top_level_is_reported(self, tmp_path):
        path = self.write(tmp_path, "list.json", "[1, 2, 3]")
        with pytest.raises(ValueError, match="mapping"):
            build_config(config_path=path)


class TestWindow:
    def parse(self, *argv):
        return build_parser().parse_args(list(argv))

    def test_defaults_to_the_current_year_and_the_next(self):
        today = dt.date(2026, 8, 21)
        start, end, start_year, end_year = resolve_window(self.parse(), today)
        assert (start_year, end_year) == (2026, 2027)
        assert start == dt.date(2026, 1, 1)
        assert end == dt.date(2027, 12, 31)

    def test_default_follows_the_clock(self):
        """The window must roll forward on its own, not sit on a hard-coded year."""
        for year in (2027, 2031, 2045):
            _, _, start_year, end_year = resolve_window(self.parse(), dt.date(year, 3, 1))
            assert (start_year, end_year) == (year, year + 1)

    def test_years_argument(self):
        _, _, start_year, end_year = resolve_window(self.parse("--years", "5"), dt.date(2026, 1, 1))
        assert (start_year, end_year) == (2026, 2030)

    def test_explicit_range(self):
        args = self.parse("--start-year", "2030", "--end-year", "2032")
        _, _, start_year, end_year = resolve_window(args, dt.date(2026, 1, 1))
        assert (start_year, end_year) == (2030, 2032)

    def test_from_today_clips_the_start(self):
        today = dt.date(2026, 8, 21)
        start, _, _, _ = resolve_window(self.parse("--from-today"), today)
        assert start == today

    def test_from_today_outside_the_window_is_ignored(self):
        args = self.parse("--from-today", "--start-year", "2030")
        start, _, _, _ = resolve_window(args, dt.date(2026, 8, 21))
        assert start == dt.date(2030, 1, 1)

    def test_backwards_range_is_rejected(self):
        args = self.parse("--start-year", "2030", "--end-year", "2028")
        with pytest.raises(ValueError, match="before"):
            resolve_window(args, dt.date(2026, 1, 1))

    def test_zero_years_is_rejected(self):
        with pytest.raises(ValueError, match="at least 1"):
            resolve_window(self.parse("--years", "0"), dt.date(2026, 1, 1))


class TestMain:
    def test_text_output(self, capsys):
        assert main(["--start-year", "2026", "--years", "1"]) == 0
        out = capsys.readouterr().out
        assert "Juneteenth" in out
        assert "Thanksgiving" in out

    def test_plan_output_mentions_pto(self, capsys):
        assert main(["--start-year", "2026", "--years", "1", "--pto", "15"]) == 0
        out = capsys.readouterr().out
        assert "PTO day(s) used in 2026" in out

    def test_json_output_is_valid(self, capsys):
        assert main(["--start-year", "2026", "--years", "1", "--pto", "10", "--format", "json"]) == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["config"]["observance"] == "nearest"
        assert len(payload["plan"]["pto_dates"]) <= 10
        assert "2026" in payload["years"]

    def test_ics_output_is_well_formed(self, capsys):
        assert main(["--start-year", "2026", "--years", "1", "--pto", "5", "--format", "ics"]) == 0
        out = capsys.readouterr().out
        assert out.startswith("BEGIN:VCALENDAR\r\n")
        assert out.rstrip().endswith("END:VCALENDAR")
        assert out.count("BEGIN:VEVENT") == out.count("END:VEVENT")
        assert "\r\n" in out

    def test_list_presets(self, capsys):
        assert main(["--list-presets"]) == 0
        assert "federal" in capsys.readouterr().out

    def test_legacy_nonstandard_flag_still_works(self, capsys):
        assert main(["--nonstandard", "--start-year", "2024", "--years", "1"]) == 0
        out = capsys.readouterr().out
        assert "Day After Thanksgiving" in out
        assert "Nov 29" in out  # not the 4th Friday, which was Nov 22

    def test_bad_argument_exits_with_usage(self, capsys):
        with pytest.raises(SystemExit) as excinfo:
            main(["--drop", "Not A Holiday"])
        assert excinfo.value.code == 2

    def test_empty_holiday_set_is_reported(self, capsys):
        args = ["--preset", "minimal", "--drop", ",".join([
            "New Year's Day", "Memorial Day", "Independence Day",
            "Labor Day", "Thanksgiving", "Christmas Day",
        ])]
        assert main(args) == 1
        assert "No holidays configured" in capsys.readouterr().err

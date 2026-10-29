"""Config loader, round 5: date-range bounds, wrong-typed keys, lone surrogates."""

from __future__ import annotations

import json

import pytest

from longweekend.cli import main
from longweekend.config import (
    MAX_RANGE_DAYS,
    MAX_RANGE_TOTAL_DAYS,
    build_config,
    load_config_file,
    parse_date_ranges,
)

yaml = pytest.importorskip("yaml")

HOL = [{"name": "X", "date": "2027-03-03"}]


def write(tmp_path, data, name="c.json"):
    path = tmp_path / name
    path.write_text(data if isinstance(data, str) else json.dumps(data), encoding="utf-8")
    return str(path)


def cli_exit_two(capsys, *argv):
    with pytest.raises(SystemExit) as exc:
        main(list(argv))
    assert exc.value.code == 2
    err = capsys.readouterr().err
    assert "Traceback" not in err
    return err


# ------------------------------------------------------------------ 1. ranges


class TestRanges:
    def test_constants(self):
        assert MAX_RANGE_DAYS == 3660 and MAX_RANGE_TOTAL_DAYS == 20000

    def test_the_reporters_range_is_rejected_fast(self):
        with pytest.raises(ValueError, match="blackout"):
            parse_date_ranges(["0001-01-01:9999-12-31"], option="blackout")

    def test_range_exactly_at_the_cap_works(self):
        from datetime import date, timedelta
        start = date(2027, 1, 1)
        end = start + timedelta(days=MAX_RANGE_DAYS - 1)
        assert len(parse_date_ranges(["%s:%s" % (start, end)], option="blackout")) == MAX_RANGE_DAYS

    def test_one_day_over_the_cap_is_rejected(self):
        from datetime import date, timedelta
        start = date(2027, 1, 1)
        end = start + timedelta(days=MAX_RANGE_DAYS)
        with pytest.raises(ValueError, match="longer than"):
            parse_date_ranges(["%s:%s" % (start, end)], option="commit")

    def test_repeated_ranges_hit_the_total_cap(self):
        ranges = ["2027-01-01:2027-12-31"] * 60  # 60 x 365 = 21,900 declared days
        with pytest.raises(ValueError, match="prefer.*total|total.*prefer"):
            parse_date_ranges(ranges, option="prefer")

    def test_total_just_under_the_cap_works(self):
        assert len(parse_date_ranges(["2027-01-01:2027-12-31"] * 50, option="prefer")) == 365

    def test_the_reporters_range_through_the_cli_is_a_clean_exit(self, capsys):
        err = cli_exit_two(capsys, "--blackout", "0001-01-01:9999-12-31", "--pto", "3",
                           "--start-year", "2027", "--years", "1")
        assert "blackout" in err and "1900" in err

    def test_reversed_range(self):
        with pytest.raises(ValueError, match="ends before it starts"):
            parse_date_ranges(["2027-05-02:2027-05-01"], option="blackout")

    @pytest.mark.parametrize("text", ["0001-01-01", "1899-12-31", "2201-01-01", "9999-12-31"])
    def test_years_outside_1900_to_2200(self, text):
        with pytest.raises(ValueError, match="1900"):
            parse_date_ranges([text], option="commit")

    def test_edges_of_the_year_window_work(self):
        assert len(parse_date_ranges(["1900-01-01", "2200-12-31"], option="commit")) == 2

    def test_option_defaults_keep_old_callers_working(self):
        assert len(parse_date_ranges(["2027-01-01:2027-01-03"])) == 3

    @pytest.mark.parametrize("flag", ["--blackout", "--commit", "--prefer"])
    def test_cli_flags_share_the_limits(self, capsys, flag):
        err = cli_exit_two(capsys, flag, "1900-01-01:2200-12-31", "--pto", "3",
                           "--start-year", "2027", "--years", "1")
        assert flag.lstrip("-") in err and "longer than" in err

    @pytest.mark.parametrize("key", ["blackout", "committed", "prefer"])
    def test_file_keys_share_the_limits(self, tmp_path, capsys, key):
        path = write(tmp_path, {"holidays": HOL, key: ["1900-01-01:2200-12-31"]})
        err = cli_exit_two(capsys, "--config", path, "--start-year", "2027", "--years", "1")
        assert key in err and "longer than" in err

    def test_memory_error_is_a_clean_exit(self, monkeypatch, capsys):
        import longweekend.cli as cli_module

        def boom(**_kwargs):
            raise MemoryError

        monkeypatch.setattr(cli_module, "build_config", boom)
        err = cli_exit_two(capsys, "--start-year", "2027", "--years", "1")
        assert "not enough memory to read this configuration" in err


# ------------------------------------------------------------------ 2. wrong-typed keys

KEYS = ["name", "preset", "holidays", "add", "drop", "workweek", "observance", "pto_days",
        "pto_by_year", "floating_days", "floating_by_year", "carryover", "min_break", "goal",
        "min_vacation_days", "max_breaks", "prefer", "max_consecutive_pto", "min_return",
        "target_days_off", "conference_travel", "blackout", "committed"]
WRONG = {"int": 1, "bool": True, "list": [1], "mapping": {"a": 1}, "null": None,
         "string": "zzz", "float": 2.5}


@pytest.mark.parametrize("key", KEYS)
@pytest.mark.parametrize("kind", sorted(WRONG))
def test_every_key_with_every_wrong_type_is_clean(tmp_path, capsys, key, kind):
    data = {"holidays": HOL, "pto_days": 3}
    data[key] = WRONG[kind]
    path = write(tmp_path, data)
    try:
        code = main(["--config", path, "--start-year", "2027", "--years", "1"])
    except SystemExit as exc:
        assert exc.code == 2
        assert "Traceback" not in capsys.readouterr().err
    else:
        # A containers, a boolean or a null is never a valid value for any key;
        # only plain numbers and text can be (an int for an integer key, say).
        assert code == 0
        assert kind in {"int", "float", "string"}, (key, kind)
        capsys.readouterr()


class TestAddEntries:
    @pytest.mark.parametrize("value", [1, [1], True, None, [None], ["Good Friday", 7]])
    def test_bad_add_values(self, tmp_path, value):
        path = write(tmp_path, {"holidays": HOL, "add": value})
        with pytest.raises(ValueError) as exc:
            build_config(config_path=path)
        assert "'add'" in str(exc.value)

    def test_position_of_the_bad_entry_is_named(self, tmp_path):
        path = write(tmp_path, {"holidays": HOL, "add": ["Good Friday", 7]})
        with pytest.raises(ValueError, match=r"entry 2"):
            build_config(config_path=path)

    def test_good_add_forms_still_work(self, tmp_path):
        path = write(tmp_path, {"preset": "minimal", "add": ["Good Friday",
                     {"name": "Founders", "month": 6, "day": 1}]})
        config = build_config(config_path=path)
        assert "Good Friday" in config.holiday_names() and "Founders" in config.holiday_names()

    def test_drop_entries_must_be_text(self, tmp_path):
        path = write(tmp_path, {"preset": "federal", "drop": [1]})
        with pytest.raises(ValueError, match="'drop'"):
            build_config(config_path=path)

    def test_date_lists_need_text_entries(self, tmp_path):
        path = write(tmp_path, {"holidays": HOL, "blackout": [20270301]})
        with pytest.raises(ValueError, match="'blackout'"):
            build_config(config_path=path)

    def test_examples_still_load(self):
        for name in ("employer-2027.yaml", "employer-2027.json"):
            assert build_config(config_path="examples/" + name).pto_days == 17


# ------------------------------------------------------------------ 3. lone surrogates


class TestSurrogates:
    def test_reporters_file(self, tmp_path, capsys):
        path = write(tmp_path, '{"name": "\\ud800", "holidays": [{"name": "X", "date": "2027-03-03"}]}')
        err = cli_exit_two(capsys, "--config", path, "--start-year", "2027", "--years", "1")
        assert "name" in err and "UTF-8" in err

    @pytest.mark.parametrize("text,where", [
        ('{"holidays": [{"name": "X\\ud800", "date": "2027-03-03"}]}', "holidays"),
        ('{"holidays": [{"name": "X", "date": "2027-03-03"}], "drop": ["a", "\\udfff"]}', "drop"),
        ('{"holidays": [{"name": "X", "date": "2027-03-03"}], "bad\\ud800key": 1}', "key"),
        ('{"holidays": [{"name": "X", "date": "2027-03-03"}], "x": {"y": [{"z\\ud800": 1}]}}', "x"),
    ])
    def test_json_positions(self, tmp_path, text, where):
        with pytest.raises(ValueError) as exc:
            load_config_file(write(tmp_path, text))
        assert "UTF-8" in str(exc.value) and where in str(exc.value)

    def test_yaml_surrogate(self, tmp_path):
        path = write(tmp_path, 'name: "\\ud800"\n', "c.yaml")
        with pytest.raises(ValueError, match="UTF-8"):
            load_config_file(path)

    def test_valid_non_ascii_text_still_loads(self, tmp_path):
        path = write(tmp_path, {"name": "Caf\u00e9 \u65e5\u672c \U0001f600", "holidays": HOL})
        assert build_config(config_path=path).name.startswith("Caf")

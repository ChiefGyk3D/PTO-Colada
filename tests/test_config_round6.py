"""Round 6: quoted whole numbers, empty list keys, name handling, BOM, huge JSON numbers."""

from __future__ import annotations

import json

import pytest

from longweekend.cli import main
from longweekend.config import build_config, load_config_file

yaml = pytest.importorskip("yaml")

HOL = [{"name": "X", "date": "2027-03-03"}]
INT_KEYS = {
    "pto_days": "pto_days", "min_break": "min_break", "carryover": "carryover",
    "floating_days": "floating_days", "conference_travel": "conference_travel",
    "max_consecutive_pto": "max_consecutive_pto", "min_vacation_days": "min_vacation_days",
    "max_breaks": "max_breaks", "target_days_off": "target_days_off",
}
BAD_STRINGS = ["010", "0x11", "1:20", "1_5", "1.5e1", "１２", "", "abc", " 15", "15 ", "1" * 19, "--1", "+-1"]


#: (key, quoted text, value); values below a key's own minimum are range errors,
#: not parsing questions, so they are left out of the list.
_MINIMUM = {"min_break": 1, "min_vacation_days": 2, "max_breaks": 1, "target_days_off": 1,
            "max_consecutive_pto": 1}
QUOTED_CASES = [
    (key, text, value)
    for key in sorted(INT_KEYS)
    for text, value in (("15", 15), ("+3", 3), ("0", 0), ("-0", 0))
    if value >= _MINIMUM.get(key, 0)
]


def write(tmp_path, data, name="c.json"):
    path = tmp_path / name
    path.write_text(data if isinstance(data, str) else json.dumps(data), encoding="utf-8")
    return str(path)


def error_of(path):
    with pytest.raises(ValueError) as exc:
        build_config(config_path=path)
    return str(exc.value)


class TestQuotedNumbers:
    @pytest.mark.parametrize("key,text,value", QUOTED_CASES)
    def test_quoted_plain_decimals_match_the_unquoted_value(self, tmp_path, key, text, value):
        quoted = build_config(config_path=write(tmp_path, {"holidays": HOL, key: text}))
        bare = build_config(config_path=write(tmp_path, {"holidays": HOL, key: value}, "b.json"))
        assert getattr(quoted, key) == getattr(bare, key) == value

    @pytest.mark.parametrize("key", sorted(INT_KEYS))
    @pytest.mark.parametrize("bad", BAD_STRINGS)
    def test_every_bad_string_stays_rejected_when_quoted(self, tmp_path, key, bad):
        message = error_of(write(tmp_path, {"holidays": HOL, key: bad}))
        assert key in message and "plain digits" in message and "plain decimal" in message

    @pytest.mark.parametrize("bad", ["010", "0x11", "1:20", "1_5", "1.5e1", "abc"])
    def test_yaml_quoted_forms_too(self, tmp_path, bad):
        text = 'holidays: [{name: X, date: 2027-03-03}]\npto_days: "%s"\n' % bad
        assert "plain digits" in error_of(write(tmp_path, text, "c.yaml"))

    def test_yaml_quoted_plain_works(self, tmp_path):
        text = 'holidays: [{name: X, date: 2027-03-03}]\npto_days: "15"\npto_by_year: {2027: "4"}\n'
        config = build_config(config_path=write(tmp_path, text, "c.yaml"))
        assert (config.pto_days, config.pto_by_year) == (15, {2027: 4})

    def test_by_year_values_accept_quoted_numbers(self, tmp_path):
        data = {"holidays": HOL, "pto_by_year": {"2027": "4"}, "floating_by_year": {"2028": "+2"}}
        config = build_config(config_path=write(tmp_path, data))
        assert config.pto_by_year == {2027: 4} and config.floating_by_year == {2028: 2}
        assert "plain digits" in error_of(write(tmp_path, {"holidays": HOL, "pto_by_year": {"2027": "x"}}, "d.json"))

    def test_holiday_mapping_numbers_accept_quoted_values(self, tmp_path):
        data = {"holidays": [
            {"name": "Fixed", "month": "7", "day": "4"},
            {"name": "Nth", "month": 5, "weekday": "MON", "n": "2"},
            {"name": "Anchor", "month": 11, "day": 25},
            {"name": "After", "after": "Anchor", "days": "1"},
        ]}
        quoted = build_config(config_path=write(tmp_path, data))
        bare = {"holidays": [
            {"name": "Fixed", "month": 7, "day": 4},
            {"name": "Nth", "month": 5, "weekday": "MON", "n": 2},
            {"name": "Anchor", "month": 11, "day": 25},
            {"name": "After", "after": "Anchor", "days": 1},
        ]}
        assert quoted.holidays == build_config(config_path=write(tmp_path, bare, "b.json")).holidays

    @pytest.mark.parametrize("entry,key", [
        ({"name": "Bad", "month": 7, "day": "0x4"}, "day"),
        ({"name": "Bad", "month": 5, "weekday": "MON", "n": "1_0"}, "n"),
        ({"name": "Bad", "after": "Anchor", "days": "010"}, "days"),
    ])
    def test_holiday_mapping_errors_name_the_holiday_and_key(self, tmp_path, entry, key):
        data = {"holidays": [{"name": "Anchor", "month": 11, "day": 25}, entry]}
        message = error_of(write(tmp_path, data))
        assert "'holidays' entry 2" in message and "Bad" in message and repr(key) in message
        assert "plain digits" in message


class TestEmptyKeys:
    @pytest.mark.parametrize("key", ["add", "drop", "blackout", "committed", "prefer"])
    def test_null_list_keys_mean_empty(self, tmp_path, key):
        base = build_config(config_path=write(tmp_path, {"holidays": HOL}))
        config = build_config(config_path=write(tmp_path, {"holidays": HOL, key: None}, "n.json"))
        assert config.holidays == base.holidays and not config.blackout and not config.prefer

    def test_yaml_empty_lines_mean_empty(self, tmp_path):
        text = "holidays: [{name: X, date: 2027-03-03}]\nadd:\ndrop:\nblackout:\ncommitted:\nprefer:\n"
        assert len(build_config(config_path=write(tmp_path, text, "c.yaml")).holidays) == 1

    @pytest.mark.parametrize("key", ["pto_days", "holidays", "pto_by_year", "workweek", "goal",
                                      "name", "min_return", "floating_by_year", "max_breaks"])
    def test_other_null_keys_say_what_to_do(self, tmp_path, key):
        data = {"holidays": HOL, key: None}
        message = error_of(write(tmp_path, data))
        assert f"{key!r} is empty; delete the line or give it a value" in message

    def test_a_null_inside_an_add_list_is_still_an_entry_error(self, tmp_path):
        message = error_of(write(tmp_path, {"holidays": HOL, "add": [None]}))
        assert "'add' entry 1" in message


class TestName:
    def test_plain_integer_name_is_converted(self, tmp_path):
        assert build_config(config_path=write(tmp_path, {"holidays": HOL, "name": 2027})).name == "2027"

    def test_yaml_integer_name(self, tmp_path):
        text = "name: 2027\nholidays: [{name: X, date: 2027-03-03}]\n"
        assert build_config(config_path=write(tmp_path, text, "c.yaml")).name == "2027"

    @pytest.mark.parametrize("word", ["no", "yes", "on", "off", "No"])
    def test_yaml_booleans_are_explained(self, tmp_path, word):
        text = "name: %s\nholidays: [{name: X, date: 2027-03-03}]\n" % word
        message = error_of(write(tmp_path, text, "c.yaml"))
        assert "YAML" in message and "boolean" in message and 'name: "' in message

    def test_other_wrong_types_say_to_quote(self, tmp_path):
        for value in (2.5, [1], {"a": 1}):
            message = error_of(write(tmp_path, {"holidays": HOL, "name": value}))
            assert "'name' must be text" in message and "quotes" in message

    def test_text_name_is_unchanged(self, tmp_path):
        assert build_config(config_path=write(tmp_path, {"holidays": HOL, "name": "Acme"})).name == "Acme"


class TestJsonDetails:
    def test_huge_integer_names_its_key(self, tmp_path):
        text = '{"holidays": [{"name": "X", "date": "2027-03-03"}], "pto_days": %s}' % ("9" * 25)
        message = error_of(write(tmp_path, text))
        assert "pto_days" in message and "too many digits" in message

    def test_huge_integer_in_a_nested_place(self, tmp_path):
        text = '{"pto_by_year": {"2027": %s}, "holidays": []}' % ("1" * 30)
        message = error_of(write(tmp_path, text))
        assert "pto_by_year" in message and "2027" in message

    def test_json_bom_is_ignored(self, tmp_path):
        path = tmp_path / "c.json"
        path.write_bytes(b"\xef\xbb\xbf" + json.dumps({"holidays": HOL, "pto_days": 5}).encode())
        assert build_config(config_path=str(path)).pto_days == 5

    def test_yaml_bom_is_ignored(self, tmp_path):
        path = tmp_path / "c.yaml"
        path.write_bytes(b"\xef\xbb\xbfpto_days: 5\nholidays: [{name: X, date: 2027-03-03}]\n")
        assert build_config(config_path=str(path)).pto_days == 5
        assert load_config_file(str(path))["pto_days"] == 5

    def test_cli_exit_two_for_huge_number(self, tmp_path, capsys):
        path = write(tmp_path, '{"holidays": [{"name": "X", "date": "2027-03-03"}], "pto_days": %s}' % ("9" * 25))
        with pytest.raises(SystemExit) as exc:
            main(["--config", path, "--start-year", "2027", "--years", "1"])
        assert exc.value.code == 2
        assert "pto_days" in capsys.readouterr().err


class TestDocumentedLimits:
    def test_flat_list_of_9990_numbers_fits_and_5000_pairs_do_not(self, tmp_path):
        flat = json.dumps({"x": list(range(9990))})
        assert load_config_file(write(tmp_path, flat))["x"][-1] == 9989
        pairs = json.dumps({"k%d" % i: 0 for i in range(5000)})
        assert "too many" in error_of(write(tmp_path, pairs, "b.json"))
        assert load_config_file(write(tmp_path, json.dumps({"k%d" % i: 0 for i in range(4999)}), "c.json"))

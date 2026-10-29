"""Config loader, round 2: amplification, normalized duplicates, non-finite and legacy numbers."""

from __future__ import annotations

import json
import time
import tracemalloc

import pytest

from longweekend.cli import main
from longweekend.config import (
    MAX_CONFIG_BYTES,
    MAX_CONFIG_DEPTH,
    MAX_CONFIG_NODES,
    build_config,
    load_config_file,
)

yaml = pytest.importorskip("yaml")

HOL = {"holidays": [{"name": "X", "date": "2027-03-03"}]}


def write(tmp_path, text, name="c.json"):
    path = tmp_path / name
    path.write_text(text)
    return str(path)


def jwrite(tmp_path, **extra):
    data = dict(HOL)
    data.update(extra)
    return write(tmp_path, json.dumps(data))


def error_of(path):
    with pytest.raises(ValueError) as exc:
        build_config(config_path=path)
    return str(exc.value)


def cli_error(capsys, path):
    with pytest.raises(SystemExit) as exc:
        main(["--config", path, "--start-year", "2027", "--years", "1"])
    assert exc.value.code == 2
    err = capsys.readouterr().err
    assert "Traceback" not in err
    return err


# ------------------------------------------------------------------ 1. amplification


class TestAmplification:
    def test_caps_are_small_named_constants(self):
        assert MAX_CONFIG_BYTES == 64 * 1024
        assert MAX_CONFIG_NODES == 10000

    @staticmethod
    def _timed_and_peak(path):
        start = time.perf_counter()
        message = error_of(path)
        elapsed = time.perf_counter() - start
        tracemalloc.start()
        try:
            error_of(path)
            peak = tracemalloc.get_traced_memory()[1]
        finally:
            tracemalloc.stop()
        return message, elapsed, peak

    def test_reviewer_input_is_rejected_fast(self, tmp_path):
        text = "x: [" + ",".join("{}" for _ in range(300000)) + "]"
        message, elapsed, peak = self._timed_and_peak(write(tmp_path, text, "c.yaml"))
        assert "too large" in message
        assert elapsed < 1.0 and peak < 20 * 1024 * 1024

    def test_many_small_nodes_under_the_byte_cap_are_rejected_before_construction(self, tmp_path):
        count = MAX_CONFIG_NODES + 100
        text = "x: [" + ",".join("{}" for _ in range(count)) + "]"
        assert len(text) < MAX_CONFIG_BYTES
        message, elapsed, peak = self._timed_and_peak(write(tmp_path, text, "c.yaml"))
        assert "too many" in message
        assert elapsed < 2.0 and peak < 20 * 1024 * 1024

    def test_the_yaml_cap_trips_before_any_object_is_constructed(self, tmp_path, monkeypatch):
        def forbidden(self, node):
            raise AssertionError("constructed Python objects for an over-cap document")

        monkeypatch.setattr(yaml.constructor.BaseConstructor, "construct_document", forbidden)
        text = "x: [" + ",".join("{}" for _ in range(MAX_CONFIG_NODES + 100)) + "]"
        assert "too many" in error_of(write(tmp_path, text, "c.yaml"))
        deep = "x: " + "[" * (MAX_CONFIG_DEPTH + 2) + "]" * (MAX_CONFIG_DEPTH + 2)
        assert "nested" in error_of(write(tmp_path, deep, "d.yaml"))

    def test_json_node_count_is_capped_too(self, tmp_path):
        text = '{"x": [' + ",".join("{}" for _ in range(MAX_CONFIG_NODES + 100)) + "]}"
        assert len(text) < MAX_CONFIG_BYTES
        assert "too many" in error_of(write(tmp_path, text))

    def test_nodes_just_under_the_cap_load(self, tmp_path):
        text = '{"x": [' + ",".join("1" for _ in range(MAX_CONFIG_NODES - 10)) + "]}"
        assert len(load_config_file(write(tmp_path, text))["x"]) == MAX_CONFIG_NODES - 10

    def test_cli_exits_two(self, tmp_path, capsys):
        text = "x: [" + ",".join("{}" for _ in range(MAX_CONFIG_NODES + 100)) + "]"
        assert "too many" in cli_error(capsys, write(tmp_path, text, "c.yaml"))


# ------------------------------------------------------------------ 2. normalized duplicates


class TestNormalizedDuplicates:
    @pytest.mark.parametrize("key", ["pto_by_year", "floating_by_year"])
    def test_json_string_year_forms_collide(self, tmp_path, key):
        text = json.dumps({"holidays": HOL["holidays"], key: {"2027": 1, "02027": 99}})
        message = error_of(write(tmp_path, text))
        assert "2027" in message and key in message and "more than once" in message

    @pytest.mark.parametrize("key", ["pto_by_year", "floating_by_year"])
    def test_yaml_int_and_string_year_collide(self, tmp_path, key):
        text = 'holidays:\n  - {name: X, date: 2027-03-03}\n%s: {"2027": 1, 2027: 99}\n' % key
        message = error_of(write(tmp_path, text, "c.yaml"))
        assert "2027" in message and "more than once" in message

    def test_distinct_years_are_fine(self, tmp_path):
        config = build_config(config_path=jwrite(tmp_path, pto_by_year={"2027": 1, "2028": 2}))
        assert config.pto_by_year == {2027: 1, 2028: 2}

    def test_bad_year_keys_are_clean_errors(self, tmp_path):
        for key in ("20x7", "", "-1", "1" * 5000, "2027.5"):
            message = error_of(jwrite(tmp_path, pto_by_year={key: 1}))
            assert "year" in message

    def test_year_value_must_be_whole(self, tmp_path):
        assert "whole number" in error_of(jwrite(tmp_path, pto_by_year={"2027": 1.5}))


# ------------------------------------------------------------------ 3. non-finite and huge numbers

INT_KEYS = ["pto_days", "floating_days", "carryover", "min_break", "max_consecutive_pto",
            "conference_travel", "target_days_off", "min_vacation_days", "max_breaks"]


class TestNumbers:
    @pytest.mark.parametrize("key", INT_KEYS)
    @pytest.mark.parametrize("raw", ["1e309", "-1e309", "NaN", "Infinity", "true", "null",
                                     '"7"', "2.5", "[1]", "1" + "0" * 400])
    def test_json_numbers_are_clean_errors(self, tmp_path, key, raw):
        path = write(tmp_path, '{"holidays": [{"name": "X", "date": "2027-03-03"}], "%s": %s}' % (key, raw))
        message = error_of(path)
        assert key in message or "digits" in message or "NaN" in message or "Infinity" in message

    @pytest.mark.parametrize("key", INT_KEYS)
    @pytest.mark.parametrize("raw", [".inf", "-.inf", ".nan", "2.5"])
    def test_yaml_numbers_are_clean_errors(self, tmp_path, key, raw):
        text = "holidays:\n  - {name: X, date: 2027-03-03}\n%s: %s\n" % (key, raw)
        assert key in error_of(write(tmp_path, text, "c.yaml"))

    def test_min_return_with_a_huge_integer(self, tmp_path):
        path = write(tmp_path, '{"holidays": [{"name": "X", "date": "2027-03-03"}], "min_return": %s}' % ("9" * 400))
        error_of(path)

    @pytest.mark.parametrize("key", ["pto_days", "carryover", "min_break"])
    def test_whole_floats_still_work(self, tmp_path, key):
        value = 3.0 if key != "min_break" else 4.0
        config = build_config(config_path=jwrite(tmp_path, **{key: value}))
        assert getattr(config, key) in (3, 4)

    def test_cli_exits_two_for_infinity(self, tmp_path, capsys):
        path = write(tmp_path, '{"holidays": [{"name": "X", "date": "2027-03-03"}], "pto_days": 1e309}')
        assert "pto_days" in cli_error(capsys, path)

    def test_cli_also_catches_overflow(self, monkeypatch, capsys):
        import longweekend.cli as cli_module

        def boom(**_kwargs):
            raise OverflowError("cannot convert float infinity to integer")

        monkeypatch.setattr(cli_module, "build_config", boom)
        with pytest.raises(SystemExit) as exc:
            main(["--start-year", "2027", "--years", "1"])
        assert exc.value.code == 2
        assert "Traceback" not in capsys.readouterr().err

    def test_rule_fields_in_holiday_entries(self, tmp_path):
        for entry in ({"name": "N", "weekday": "MON", "month": 5, "n": 1e309},
                      {"name": "N", "month": 5, "day": 1e309},
                      {"name": "N", "after": "X", "days": 1e309}):
            path = write(tmp_path, json.dumps({"holidays": [{"name": "X", "date": "2027-03-03"}, entry]}))
            error_of(path)


# ------------------------------------------------------------------ 4. legacy YAML numerics


class TestLegacyNumerics:
    @pytest.mark.parametrize("raw", ["010", "1:20", "0x10", "0o10", "0b11", "1_000", "+010", "00"])
    def test_legacy_forms_are_errors(self, tmp_path, raw):
        text = "holidays:\n  - {name: X, date: 2027-03-03}\npto_days: %s\n" % raw
        message = error_of(write(tmp_path, text, "c.yaml"))
        assert "plain decimal" in message and "pto_days" in message

    @pytest.mark.parametrize("raw,expected", [("17", 17), ("0", 0), ("+5", 5), ("3.0", 3)])
    def test_plain_decimals_still_work(self, tmp_path, raw, expected):
        text = "holidays:\n  - {name: X, date: 2027-03-03}\npto_days: %s\n" % raw
        assert build_config(config_path=write(tmp_path, text, "c.yaml")).pto_days == expected

    def test_quoted_strings_stay_strings(self, tmp_path):
        text = 'name: "010"\nholidays:\n  - {name: "0x10", date: 2027-03-03}\n'
        config = build_config(config_path=write(tmp_path, text, "c.yaml"))
        assert config.name == "010" and config.holiday_names() == ["0x10"]

    def test_year_keys_in_yaml_still_work(self, tmp_path):
        text = "holidays:\n  - {name: X, date: 2027-03-03}\npto_by_year: {2027: 4, 2028: 5}\n"
        assert build_config(config_path=write(tmp_path, text, "c.yaml")).pto_by_year == {2027: 4, 2028: 5}

    def test_cli_exit_two(self, tmp_path, capsys):
        text = "holidays:\n  - {name: X, date: 2027-03-03}\npto_days: 010\n"
        assert "plain decimal" in cli_error(capsys, write(tmp_path, text, "c.yaml"))

    def test_shipped_examples_still_load(self):
        for name in ("employer-2027.yaml", "employer-2027.json"):
            config = build_config(config_path="examples/" + name)
            assert config.pto_days == 17 and len(config.holidays) == 8

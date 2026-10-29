"""Config loader, round 3: explicit YAML tags, JSON key counting, diagnostics without source text."""

from __future__ import annotations

import json

import pytest

from pto_colada.cli import main
from pto_colada.config import MAX_CONFIG_BYTES, MAX_CONFIG_NODES, build_config

yaml = pytest.importorskip("yaml")


def write(tmp_path, text, name="c.yaml"):
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return str(path)


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


def with_pto(raw):
    return "holidays: [{name: X, date: 2027-03-03}]\npto_days: %s\n" % raw


class TestExplicitIntTags:
    @pytest.mark.parametrize("raw", [
        "!!int 010", "!!int 0x10", "!!int 0o10", "!!int 0b11", "!!int 1:20", "!!int 1_000",
        "!!int １２", "!!int " + "9" * 40, "!!int 1" + "0" * 400, '!!int "1 2"', "!!int abc",
    ])
    def test_non_decimal_tagged_integers_are_clean_errors(self, tmp_path, capsys, raw):
        path = write(tmp_path, with_pto(raw))
        assert "plain decimal" in error_of(path)
        assert "plain decimal" in cli_error(capsys, path)

    @pytest.mark.parametrize("raw,expected", [("!!int 17", 17), ("!!int +5", 5), ('!!int "12"', 12), ("!!int 0", 0)])
    def test_plain_tagged_integers_work(self, tmp_path, raw, expected):
        assert build_config(config_path=write(tmp_path, with_pto(raw))).pto_days == expected

    def test_tagged_integer_year_key(self, tmp_path):
        text = "holidays: [{name: X, date: 2027-03-03}]\npto_by_year: {!!int 2027: 4}\n"
        assert build_config(config_path=write(tmp_path, text)).pto_by_year == {2027: 4}
        bad = "holidays: [{name: X, date: 2027-03-03}]\npto_by_year: {!!int 0x7e3: 4}\n"
        assert "plain decimal" in error_of(write(tmp_path, bad, "d.yaml"))


class TestOtherExplicitTags:
    @pytest.mark.parametrize("raw", ["!!float 1e999", "!!float -1e999"])
    def test_tagged_floats_that_overflow_are_clean_errors(self, tmp_path, capsys, raw):
        path = write(tmp_path, with_pto(raw))
        assert "finite" in error_of(path)
        assert "finite" in cli_error(capsys, path)

    def test_tagged_float_that_is_whole_works(self, tmp_path):
        assert build_config(config_path=write(tmp_path, with_pto("!!float 3.0"))).pto_days == 3

    def test_explicit_infinity_is_still_rejected_by_name_downstream(self, tmp_path):
        assert "pto_days" in error_of(write(tmp_path, with_pto("!!float .inf")))

    @pytest.mark.parametrize("raw", [
        "!!binary aGVsbG8=", "!!set {a, b}", "!!omap [a: 1]", "!!pairs [a: 1]",
    ])
    def test_tags_no_key_can_use_are_rejected(self, tmp_path, capsys, raw):
        path = write(tmp_path, "name: %s\nholidays: [{name: X, date: 2027-03-03}]\n" % raw)
        assert "not allowed" in error_of(path)
        assert "not allowed" in cli_error(capsys, path)

    @pytest.mark.parametrize("raw,field,expected", [
        ('!!str 7', "name", "7"), ("!!null ''", "name", None), ("!!bool true", "name", True),
    ])
    def test_harmless_tags_load_as_data(self, tmp_path, raw, field, expected):
        from pto_colada.config import load_config_file
        data = load_config_file(write(tmp_path, "name: %s\n" % raw))
        assert data["name"] == expected

    def test_explicit_timestamp_stays_text_and_names_the_holiday(self, tmp_path):
        text = "holidays:\n  - {name: Memorial Day, date: !!timestamp 2027-02-30}\n"
        assert "Memorial Day: 2027-02-30 is not a date" in error_of(write(tmp_path, text))

    def test_python_tags_are_clean_errors(self, tmp_path, capsys):
        path = write(tmp_path, "name: !!python/object/apply:os.getcwd []\n")
        assert error_of(path)
        cli_error(capsys, path)


class TestJsonKeysCount:
    def test_report_generator_input_is_rejected(self, tmp_path):
        pairs, i = [], 0
        while True:
            candidate = pairs + [(format(i, "x"), 0)]
            text = json.dumps(dict(candidate), separators=(",", ":"))
            if len(text.encode()) > MAX_CONFIG_BYTES:
                break
            pairs, text_ok = candidate, text
            i += 1
        text = json.dumps(dict(pairs), separators=(",", ":"))
        assert len(text.encode()) <= MAX_CONFIG_BYTES and 1 + 2 * len(pairs) > MAX_CONFIG_NODES
        assert "too many" in error_of(write(tmp_path, text, "c.json"))

    def test_keys_count_like_the_yaml_composer(self, tmp_path):
        half = MAX_CONFIG_NODES // 2
        under = json.dumps({"k%d" % n: 0 for n in range(half - 10)})
        over = json.dumps({"k%d" % n: 0 for n in range(half + 10)})
        assert len(over.encode()) < MAX_CONFIG_BYTES
        assert build_config(config_path=write(tmp_path, under, "a.json")) is not None
        assert "too many" in error_of(write(tmp_path, over, "b.json"))
        flow_over = "{" + ", ".join("k%d: 0" % n for n in range(half + 10)) + "}"
        assert "too many" in error_of(write(tmp_path, flow_over, "c.yaml"))

    def test_shipped_examples_still_load(self):
        for name in ("employer-2027.yaml", "employer-2027.json"):
            assert build_config(config_path="examples/" + name).pto_days == 17


class TestDiagnosticsWithoutSource:
    def test_yaml_error_has_position_not_source(self, tmp_path, capsys):
        path = write(tmp_path, "secret: [password123\n")
        message = error_of(path)
        assert "line " in message and "column" in message
        assert "password123" not in message
        assert "password123" not in cli_error(capsys, path)

    def test_yaml_error_on_a_later_line(self, tmp_path):
        message = error_of(write(tmp_path, "name: ok\npto_days: 3\ntoken: {abc-secret-xyz\n"))
        assert "line 4" in message or "line 3" in message
        assert "abc-secret-xyz" not in message and "^" not in message

    def test_json_error_has_position_not_source(self, tmp_path, capsys):
        path = write(tmp_path, '{"secret": "password123" "x": 1}', "c.json")
        message = error_of(path)
        assert "line 1" in message and "password123" not in message
        assert "password123" not in cli_error(capsys, path)

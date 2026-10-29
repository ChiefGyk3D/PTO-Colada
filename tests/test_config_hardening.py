"""Hostile config files: duplicates, aliases, size, depth, odd file types, bad dates."""

from __future__ import annotations

import json
import os
import threading

import pytest

from longweekend import config as config_module
from longweekend.cli import main
from longweekend.config import MAX_CONFIG_BYTES, MAX_CONFIG_DEPTH, build_config, load_config_file

yaml = pytest.importorskip("yaml")


def write(tmp_path, text, name="c.json"):
    path = tmp_path / name
    path.write_text(text)
    return str(path)


def load_error(path):
    with pytest.raises(ValueError) as exc:
        build_config(config_path=path)
    return str(exc.value)


def cli_exit(capsys, path):
    with pytest.raises(SystemExit) as exc:
        main(["--config", path, "--start-year", "2027", "--years", "1"])
    assert exc.value.code == 2
    return capsys.readouterr().err


class TestDuplicateKeys:
    def test_json_duplicate_status_never_wins_silently(self, tmp_path):
        text = ('{"holidays": [{"name": "X", "date": "2027-03-03", '
                '"status": "assumed", "status": "confirmed"}]}')
        assert "duplicate key 'status'" in load_error(write(tmp_path, text))

    def test_json_duplicate_top_level_key(self, tmp_path):
        assert "duplicate key 'pto_days'" in load_error(write(tmp_path, '{"pto_days": 1, "pto_days": 2}'))

    def test_yaml_duplicate_key(self, tmp_path):
        text = "holidays:\n  - {name: X, date: 2027-03-03, status: assumed, status: confirmed}\n"
        assert "duplicate key 'status'" in load_error(write(tmp_path, text, "c.yaml"))

    def test_distinct_keys_are_fine(self, tmp_path):
        text = '{"pto_days": 1, "holidays": [{"name": "X", "date": "2027-03-03"}]}'
        assert build_config(config_path=write(tmp_path, text)).pto_days == 1


class TestYamlFeatures:
    def test_aliases_are_rejected(self, tmp_path):
        text = "a: &x {name: X}\nholidays: [*x]\n"
        assert "alias" in load_error(write(tmp_path, text, "c.yaml")).lower()

    def test_billion_laughs_is_rejected_quickly(self, tmp_path):
        text = "a: &a [x, x, x, x]\nb: &b [*a, *a, *a, *a]\nc: &c [*b, *b, *b, *b]\nd: [*c, *c, *c, *c]\n"
        assert "alias" in load_error(write(tmp_path, text, "c.yaml")).lower()

    def test_merge_keys_are_rejected(self, tmp_path):
        text = "base: {name: X}\nholidays:\n  - <<: {name: Y, date: 2027-03-03}\n"
        assert "not allowed" in load_error(write(tmp_path, text, "c.yaml")).lower()

    def test_malformed_yaml_is_a_clean_error(self, tmp_path):
        assert load_error(write(tmp_path, "a: [1, 2\n", "c.yaml"))

    def test_plain_yaml_still_loads(self, tmp_path):
        text = "pto_days: 3\nholidays:\n  - {name: X, date: 2027-03-03, status: assumed}\n"
        assert build_config(config_path=write(tmp_path, text, "c.yaml")).pto_days == 3


class TestLimits:
    def test_size_cap_is_a_named_constant(self):
        assert MAX_CONFIG_BYTES == 1024 * 1024

    def test_oversize_file_is_rejected(self, tmp_path):
        text = '{"name": "' + "a" * MAX_CONFIG_BYTES + '"}'
        assert "too large" in load_error(write(tmp_path, text))

    def test_file_at_the_cap_is_read(self, tmp_path):
        pad = MAX_CONFIG_BYTES - len('{"name": ""}')
        text = '{"name": "' + "a" * pad + '"}'
        assert len(text) == MAX_CONFIG_BYTES
        assert len(load_config_file(write(tmp_path, text))["name"]) == pad

    def test_deep_json_is_a_clean_error(self, tmp_path):
        assert "nested" in load_error(write(tmp_path, "[" * 200000))

    def test_deep_yaml_is_a_clean_error(self, tmp_path):
        assert "nested" in load_error(write(tmp_path, "[" * 100000, "c.yaml"))

    def test_moderate_depth_over_the_cap_is_rejected(self, tmp_path):
        text = '{"a":' * (MAX_CONFIG_DEPTH + 2) + "1" + "}" * (MAX_CONFIG_DEPTH + 2)
        assert "nested" in load_error(write(tmp_path, text))

    def test_depth_within_the_cap_is_fine(self, tmp_path):
        assert load_config_file(write(tmp_path, '{"a":{"b":{"c":1}}}'))["a"]["b"]["c"] == 1

    def test_oversize_through_the_cli_exits_two(self, tmp_path, capsys):
        path = write(tmp_path, "x" * (MAX_CONFIG_BYTES + 1))
        err = cli_exit(capsys, path)
        assert "too large" in err and "Traceback" not in err


class TestFileTypes:
    def test_directory_is_rejected(self, tmp_path, capsys):
        err = cli_exit(capsys, str(tmp_path))
        assert "regular file" in err and "Traceback" not in err

    def test_fifo_does_not_hang(self, tmp_path):
        fifo = str(tmp_path / "pipe.json")
        os.mkfifo(fifo)
        result = {}

        def attempt():
            try:
                load_config_file(fifo)
            except Exception as exc:
                result["error"] = exc

        thread = threading.Thread(target=attempt, daemon=True)
        thread.start()
        thread.join(5)
        assert not thread.is_alive(), "loading a FIFO blocked"
        assert isinstance(result["error"], ValueError)
        assert "regular file" in str(result["error"])

    def test_missing_file_message_is_unchanged(self, tmp_path):
        with pytest.raises(FileNotFoundError, match="config file not found"):
            load_config_file(str(tmp_path / "nope.json"))

    def test_invalid_utf8_is_a_clean_error(self, tmp_path):
        path = tmp_path / "c.json"
        path.write_bytes(b'{"name": "\xff\xfe"}')
        assert "UTF-8" in load_error(str(path))


class TestBadDates:
    @pytest.mark.parametrize("name,text", [("c.json", '{"holidays": [{"name": "Memorial Day", "date": "2027-02-30"}]}'),
                                           ("c.yaml", "holidays:\n  - {name: Memorial Day, date: 2027-02-30}\n")])
    def test_error_names_the_holiday_and_the_date(self, tmp_path, name, text):
        assert "Memorial Day: 2027-02-30 is not a date" in load_error(write(tmp_path, text, name))

    def test_cli_exits_two_with_that_message(self, tmp_path, capsys):
        path = write(tmp_path, '{"holidays": [{"name": "Memorial Day", "date": "2027-02-30"}]}')
        err = cli_exit(capsys, path)
        assert "Memorial Day: 2027-02-30 is not a date" in err and "Traceback" not in err

    def test_add_spec_names_the_holiday_too(self):
        with pytest.raises(ValueError, match="Founders: 2027-13-01|Founders"):
            build_config(add=["Founders=2027-02-30"])


def test_constants_are_exported():
    assert hasattr(config_module, "MAX_CONFIG_BYTES")

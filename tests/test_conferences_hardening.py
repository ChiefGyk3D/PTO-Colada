"""The conference catalog file is read as untrusted input, like the config file."""

import json
import os

import pytest

from longweekend import config
from longweekend.conferences import load_conferences


def _write(tmp_path, text, name="cons.json"):
    path = tmp_path / name
    path.write_bytes(text if isinstance(text, bytes) else text.encode("utf-8"))
    return str(path)


GOOD = json.dumps(
    {"conferences": [{"name": "Tiny Con", "dates": {"2027": ["2027-05-04", "2027-05-05"]}}]}
)


def test_valid_file_still_loads(tmp_path):
    names = [c.name for c in load_conferences(_write(tmp_path, GOOD))]
    assert "Tiny Con" in names


def test_missing_file_is_file_not_found(tmp_path):
    with pytest.raises(FileNotFoundError, match="conference file not found"):
        load_conferences(str(tmp_path / "nope.json"))


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="needs FIFOs")
def test_fifo_is_rejected_without_hanging(tmp_path):
    fifo = str(tmp_path / "fifo.json")
    os.mkfifo(fifo)
    with pytest.raises(ValueError, match="not a regular file"):
        load_conferences(fifo)


def test_directory_is_rejected(tmp_path):
    with pytest.raises(ValueError, match="not a regular file"):
        load_conferences(str(tmp_path))


def test_oversize_file_is_rejected(tmp_path):
    pad = " " * (config.MAX_CONFIG_BYTES + 10)
    with pytest.raises(ValueError, match="too large"):
        load_conferences(_write(tmp_path, GOOD + pad))


def test_duplicate_keys_are_rejected(tmp_path):
    text = '{"conferences": [{"name": "A", "name": "B"}]}'
    with pytest.raises(ValueError, match="duplicate key"):
        load_conferences(_write(tmp_path, text))


def test_deep_nesting_is_a_value_error(tmp_path):
    text = "[" * 5000 + "]" * 5000
    with pytest.raises(ValueError, match="nested too deeply"):
        load_conferences(_write(tmp_path, text))


def test_moderate_nesting_over_the_limit_is_rejected(tmp_path):
    depth = config.MAX_CONFIG_DEPTH + 3
    text = '{"conferences": [{"name": "A", "note": ' + "[" * depth + "]" * depth + "}]}"
    with pytest.raises(ValueError, match="nested too deeply"):
        load_conferences(_write(tmp_path, text))


def test_node_cap_bites_on_flat_input(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "MAX_CONFIG_NODES", 20)
    entries = [{"name": f"C{n}"} for n in range(20)]
    with pytest.raises(ValueError, match="too many values"):
        load_conferences(_write(tmp_path, json.dumps(entries)))


def test_huge_integer_is_rejected(tmp_path):
    text = '{"conferences": [{"name": "A", "note": 1' + "0" * 40 + "}]}"
    with pytest.raises(ValueError, match="too many digits"):
        load_conferences(_write(tmp_path, text))


def test_invalid_utf8_is_a_value_error(tmp_path):
    with pytest.raises(ValueError, match="UTF-8"):
        load_conferences(_write(tmp_path, b'{"conferences": [\xff]}'))


def test_lone_surrogate_is_rejected(tmp_path):
    text = '{"conferences": [{"name": "A\\ud800"}]}'
    with pytest.raises(ValueError, match="UTF-8"):
        load_conferences(_write(tmp_path, text))


def test_bom_is_accepted(tmp_path):
    names = [c.name for c in load_conferences(_write(tmp_path, b"\xef\xbb\xbf" + GOOD.encode()))]
    assert "Tiny Con" in names


def test_invalid_json_is_a_value_error(tmp_path):
    with pytest.raises(ValueError, match="not valid JSON"):
        load_conferences(_write(tmp_path, "{nope"))


@pytest.mark.parametrize(
    "entry",
    [
        {"name": "A", "dates": ["x"]},
        {"name": "A", "dates": {"2027": "2027-01-01"}},
        {"name": "A", "dates": {"abc": ["2027-01-01", "2027-01-02"]}},
        {"name": "A", "dates": {"2027": [1, 2]}},
        {"name": "A", "aliases": 5},
        {"name": "A", "aliases": [["x"]]},
        {"name": "A", "recurrence": "nope"},
        {"name": "A", "recurrence": {"when": "2nd tuesday of May", "days": "many"}},
    ],
)
def test_wrongly_typed_fields_are_value_errors(tmp_path, entry):
    with pytest.raises(ValueError):
        load_conferences(_write(tmp_path, json.dumps({"conferences": [entry]})))

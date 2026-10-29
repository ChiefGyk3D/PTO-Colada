"""The conference catalog file is read as untrusted input, like the config file."""

import json
import os
import subprocess
import sys

import pytest

from pto_colada import config
from pto_colada.conferences import load_conferences


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
    # Run in a child so a regression fails the test instead of hanging the suite.
    fifo = str(tmp_path / "fifo.json")
    os.mkfifo(fifo)
    code = (
        "import sys\n"
        "from pto_colada.conferences import load_conferences\n"
        "try:\n"
        "    load_conferences(sys.argv[1])\n"
        "except ValueError as exc:\n"
        "    print(exc)\n"
    )
    repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    env = dict(os.environ, PYTHONPATH=repo)
    try:
        done = subprocess.run(
            [sys.executable, "-c", code, fifo],
            capture_output=True, text=True, timeout=5, env=env, cwd=repo,
        )
    except subprocess.TimeoutExpired:
        pytest.fail("loading a FIFO hung instead of being rejected")
    assert "not a regular file" in done.stdout


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


def _entry(**extra):
    return {"conferences": [dict({"name": "A"}, **extra)]}


@pytest.mark.parametrize(
    "extra",
    [
        {"recurrence": {"when": 5}},
        {"recurrence": {}},
        {"recurrence": {"when": "2nd tuesday of May", "days": True}},
        {"recurrence": {"when": "2nd tuesday of May", "days": 0}},
        {"recurrence": {"when": "2nd tuesday of May", "days": 367}},
        {"recurrence": {"when": "2nd tuesday of May", "days": 10**17}},
        {"recurrence": {"when": "2nd tuesday of May", "days": 2.5}},
        {"recurrence": {"when": "2nd tuesday of May", "days": "x"}},
    ],
)
def test_bad_recurrence_is_a_value_error_naming_the_conference(tmp_path, extra):
    with pytest.raises(ValueError, match="'A'|A "):
        load_conferences(_write(tmp_path, json.dumps(_entry(**extra))))


@pytest.mark.parametrize("days", [1, 4, 366, 3.0, "3"])
def test_valid_recurrence_days_still_load(tmp_path, days):
    extra = {"recurrence": {"when": "2nd tuesday of May", "days": days}}
    assert any(c.name == "A" for c in load_conferences(_write(tmp_path, json.dumps(_entry(**extra)))))


def test_year_keys_that_normalize_to_the_same_year_are_rejected(tmp_path):
    span = ["2027-05-04", "2027-05-05"]
    text = json.dumps(_entry(dates={"2027": span, "02027": span}))
    with pytest.raises(ValueError, match="A.*2027.*more than once|more than once.*A"):
        load_conferences(_write(tmp_path, text))


def test_names_that_normalize_to_the_same_key_are_rejected(tmp_path):
    text = json.dumps({"conferences": [{"name": "Tiny Con"}, {"name": "  tiny   CON "}]})
    with pytest.raises(ValueError, match="listed more than once"):
        load_conferences(_write(tmp_path, text))


@pytest.mark.parametrize(
    "extra",
    [
        {"dates": {"2027": ["2027-13-01", "2027-13-02"]}},
        {"dates": {"abc": ["2027-01-01", "2027-01-02"]}},
        {"dates": {"2027": ["2027-01-02", "2027-01-01"]}},
        {"dates": {"2027": [None, None]}},
    ],
)
def test_date_errors_name_file_and_conference(tmp_path, extra):
    path = _write(tmp_path, json.dumps(_entry(**extra)))
    with pytest.raises(ValueError) as caught:
        load_conferences(path)
    assert path in str(caught.value) and "A" in str(caught.value)


def test_null_dates_still_means_none(tmp_path):
    assert any(c.name == "A" for c in load_conferences(_write(tmp_path, json.dumps(_entry(dates=None)))))

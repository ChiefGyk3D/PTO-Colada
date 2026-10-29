"""Employer holiday config: explicit dated days with a confirmation status."""

from __future__ import annotations

import datetime as dt
import json
import os

import pytest

from pto_colada.catalog import Observance, resolve_year
from pto_colada.cli import main
from pto_colada.config import build_config

D = dt.date
HERE = os.path.dirname(__file__)
JSON_PATH = os.path.join(HERE, "..", "examples", "employer-2027.json")
YAML_PATH = os.path.join(HERE, "..", "examples", "employer-2027.yaml")

EXPECTED = {
    "New Year's Day": (D(2027, 1, 1), "confirmed"),
    "Memorial Day": (D(2027, 5, 31), "assumed"),
    "Independence Day (observed)": (D(2027, 7, 5), "observed-unconfirmed"),
    "Labor Day": (D(2027, 9, 6), "assumed"),
    "Thanksgiving": (D(2027, 11, 25), "confirmed"),
    "Day After Thanksgiving": (D(2027, 11, 26), "confirmed"),
    "Christmas (observed)": (D(2027, 12, 24), "observed-unconfirmed"),
    "New Year's 2028 (observed)": (D(2027, 12, 31), "observed-unconfirmed"),
}
UNCONFIRMED = {
    "Memorial Day", "Independence Day (observed)", "Labor Day",
    "Christmas (observed)", "New Year's 2028 (observed)",
}


def _resolved(config, year=2027):
    return {h.name: h for h in resolve_year(config.holidays, year, config.workweek, config.observance)}


@pytest.mark.parametrize("path", [JSON_PATH, YAML_PATH])
def test_example_file_loads_exactly_eight_days(path):
    if path.endswith(".yaml"):
        pytest.importorskip("yaml")
    config = build_config(config_path=path)
    assert config.observance is Observance.NONE
    resolved = _resolved(config)
    assert set(resolved) == set(EXPECTED)
    for name, (date, status) in EXPECTED.items():
        assert resolved[name].observed == date, name
        assert resolved[name].status == status, name


def test_nothing_else_is_added_automatically():
    config = build_config(config_path=JSON_PATH)
    assert sorted(config.holiday_names()) == sorted(EXPECTED)
    for year in (2026, 2028):
        assert _resolved(config, year) == {}  # one-off dates exist only in 2027


def test_extras_are_enabled_by_hand_with_add():
    config = build_config(config_path=JSON_PATH, add=["Juneteenth"])
    assert "Juneteenth" in config.holiday_names()
    assert len(config.holidays) == 9


def test_unknown_status_is_rejected(tmp_path):
    path = tmp_path / "bad.json"
    path.write_text(json.dumps({"holidays": [{"name": "X", "date": "2027-03-01", "status": "maybe"}]}))
    with pytest.raises(ValueError, match="status"):
        build_config(config_path=str(path))


def test_status_on_a_recurring_rule_is_rejected(tmp_path):
    path = tmp_path / "bad.json"
    path.write_text(json.dumps({"holidays": [{"name": "X", "month": 3, "day": 1, "status": "assumed"}]}))
    with pytest.raises(ValueError, match="status"):
        build_config(config_path=str(path))


def test_a_dated_day_without_status_is_confirmed(tmp_path):
    path = tmp_path / "ok.json"
    path.write_text(json.dumps({"holidays": [{"name": "X", "date": "2027-03-01"}]}))
    config = build_config(config_path=str(path))
    assert _resolved(config)["X"].status is None


def test_two_explicit_holidays_on_one_date_are_allowed(tmp_path):
    path = tmp_path / "dup.json"
    path.write_text(json.dumps({"observance": "none", "holidays": [
        {"name": "A", "date": "2027-11-25"}, {"name": "B", "date": "2027-11-25"}]}))
    config = build_config(config_path=str(path))
    assert len(config.holidays) == 2


# --------------------------------------------------------------- provisional output


def _run(capsys, *argv):
    code = main(list(argv))
    out = capsys.readouterr().out
    assert code == 0
    return out


BASE = ["--config", JSON_PATH, "--start-year", "2027", "--years", "1"]


def test_text_is_provisional_and_lists_every_unconfirmed_day(capsys):
    out = _run(capsys, *BASE)
    assert "PROVISIONAL" in out
    for name in UNCONFIRMED:
        assert name in out.split("PROVISIONAL", 1)[1]
    block = out.split("PROVISIONAL", 1)[1].split("\n\n", 1)[0]
    assert "Thanksgiving" not in block and "New Year's Day" not in block


def test_json_is_provisional(capsys):
    data = json.loads(_run(capsys, *BASE, "--format", "json"))
    assert data["provisional"] is True
    names = {a["name"]: a["status"] for a in data["assumptions"]}
    assert set(names) == UNCONFIRMED
    assert names["Memorial Day"] == "assumed"
    assert names["Labor Day"] == "assumed"
    assert names["Christmas (observed)"] == "observed-unconfirmed"


def test_ics_is_provisional(capsys):
    out = _run(capsys, *BASE, "--format", "ics", "--pto", "3")
    assert "X-LWP-PROVISIONAL:TRUE" in out
    assert "PROVISIONAL" in out.split("BEGIN:VEVENT", 1)[1]
    assert out.count("X-LWP-ASSUMPTION:") == 5


def test_a_plan_is_provisional_in_all_formats(capsys):
    text = _run(capsys, *BASE, "--pto", "5")
    assert "PROVISIONAL" in text.split("2027", 1)[0] or "PROVISIONAL" in text
    data = json.loads(_run(capsys, *BASE, "--pto", "5", "--format", "json"))
    assert data["provisional"] is True


def test_all_confirmed_is_not_provisional(capsys, tmp_path):
    path = tmp_path / "ok.json"
    path.write_text(json.dumps({"observance": "none", "holidays": [
        {"name": "A", "date": "2027-03-03", "status": "confirmed"}]}))
    argv = ["--config", str(path), "--start-year", "2027", "--years", "1"]
    assert "PROVISIONAL" not in _run(capsys, *argv)
    assert "provisional" not in json.loads(_run(capsys, *argv, "--format", "json"))
    assert "PROVISIONAL" not in _run(capsys, *argv, "--format", "ics")

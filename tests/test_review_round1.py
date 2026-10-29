"""Fix round 1: provisional scope, same-date status, carryover display, README pins."""

from __future__ import annotations

import datetime as dt
import json
import os
import re
import shlex

import pytest

from longweekend.catalog import ResolvedHoliday
from longweekend.cli import main
from longweekend.report import provisional_assumptions

D = dt.date
ROOT = os.path.join(os.path.dirname(__file__), "..")
EXAMPLE = os.path.join(ROOT, "examples", "employer-2027.json")


def cli(capsys, *argv, expect=0):
    if expect == 0:
        assert main(list(argv)) == 0
        return capsys.readouterr().out
    with pytest.raises(SystemExit) as exc:
        main(list(argv))
    assert exc.value.code == expect
    return capsys.readouterr().err


def write(tmp_path, data, name="c.json"):
    path = tmp_path / name
    path.write_text(json.dumps(data))
    return str(path)


# ----------------------------------------------------------- item 3: provisional scope


def adj_config(tmp_path, assumed_date="2028-01-03"):
    return write(tmp_path, {
        "observance": "none", "pto_days": 1,
        "holidays": [
            {"name": "Conf", "date": "2027-12-30", "status": "confirmed"},
            {"name": "NY28", "date": assumed_date, "status": "assumed"},
        ],
    })


GOAL = ["--start-year", "2027", "--years", "1", "--goal", "weekends", "--pto", "3"]


def test_unconfirmed_day_in_an_adjacent_year_labels_every_format(tmp_path, capsys):
    path = adj_config(tmp_path)
    text = cli(capsys, "--config", path, *GOAL)
    assert "PROVISIONAL" in text and "NY28" in text.split("PROVISIONAL", 1)[1]
    data = json.loads(cli(capsys, "--config", path, *GOAL, "--format", "json"))
    assert data["provisional"] is True
    assert [a["name"] for a in data["assumptions"]] == ["NY28"]
    ics = cli(capsys, "--config", path, *GOAL, "--format", "ics")
    assert "X-LWP-PROVISIONAL:TRUE" in ics and "NY28" in ics


def test_unconfirmed_day_far_outside_the_padded_window_is_ignored(tmp_path, capsys):
    path = adj_config(tmp_path, assumed_date="2028-06-01")
    text = cli(capsys, "--config", path, *GOAL)
    assert "PROVISIONAL" not in text
    assert "provisional" not in json.loads(cli(capsys, "--config", path, *GOAL, "--format", "json"))


def test_confirmed_only_is_not_provisional(tmp_path, capsys):
    path = write(tmp_path, {"observance": "none", "pto_days": 1, "holidays": [
        {"name": "A", "date": "2027-12-30", "status": "confirmed"},
        {"name": "B", "date": "2028-01-03", "status": "confirmed"}]})
    assert "PROVISIONAL" not in cli(capsys, "--config", path, *GOAL)


def test_unconfirmed_inside_the_window_still_labels_everything(tmp_path, capsys):
    path = write(tmp_path, {"observance": "none", "pto_days": 1, "holidays": [
        {"name": "Mid", "date": "2027-06-02", "status": "assumed"}]})
    assert "PROVISIONAL" in cli(capsys, "--config", path, *GOAL)


def test_extra_holidays_argument_adds_to_the_window():
    h = ResolvedHoliday("Late", D(2028, 2, 10), D(2028, 2, 10), "assumed")
    assert provisional_assumptions({2027: []}) == []
    assert provisional_assumptions({2027: []}, extra=[h]) == [h]


# ----------------------------------------------------------- item 9: same date


def test_a_date_with_a_confirmed_holiday_is_not_unconfirmed():
    a = ResolvedHoliday("A", D(2027, 1, 1), D(2027, 1, 1), "confirmed")
    b = ResolvedHoliday("B", D(2027, 1, 1), D(2027, 1, 1), "assumed")
    c = ResolvedHoliday("C", D(2027, 5, 31), D(2027, 5, 31), "assumed")
    assert provisional_assumptions({2027: [a, b, c]}) == [c]
    assert provisional_assumptions({2027: [a, b]}) == []


def test_same_date_confirmed_and_assumed_end_to_end(tmp_path, capsys):
    path = write(tmp_path, {"observance": "none", "pto_days": 3, "holidays": [
        {"name": "A", "date": "2027-01-01", "status": "confirmed"},
        {"name": "B", "date": "2027-01-01", "status": "assumed"}]})
    argv = ["--config", path, "--start-year", "2027", "--years", "1"]
    assert "PROVISIONAL" not in cli(capsys, *argv)
    assert "provisional" not in json.loads(cli(capsys, *argv, "--format", "json"))
    assert "PROVISIONAL" not in cli(capsys, *argv, "--goal", "weekends")


# ----------------------------------------------------------- item 2: carryover

CARRY = ["--config", EXAMPLE, "--start-year", "2027", "--years", "2", "--pto-year", "2027=10",
         "--pto-year", "2028=2", "--carryover", "5", "--goal", "weekends"]


def test_carryover_is_shown_not_flagged_as_over_budget(capsys):
    data = json.loads(cli(capsys, *CARRY, "--format", "json"))
    rows = {r["year"]: r for r in data["honest"]["budgets"]}
    assert rows[2028]["used"] > rows[2028]["granted"]  # it did borrow from 2027
    assert rows[2028]["carried_over"] == rows[2028]["used"] - rows[2028]["granted"]
    assert rows[2028]["over"] == 0
    text = cli(capsys, *CARRY)
    assert "OVER BUDGET" not in text
    row = rows[2028]
    assert f"{row['used']} of {row['granted']} + {row['carried_over']} carried over" in text


def test_a_real_overspend_is_still_flagged():
    from longweekend.honest import score_pto
    r = score_pto({D(2027, 1, 4), D(2027, 2, 5)}, {}, years=[2027], budgets={2027: 1})
    assert r.budget_rows[0].over == 1
    r = score_pto({D(2027, 1, 4), D(2027, 2, 5)}, {}, years=[2027], budgets={2027: 1},
                  carried={2027: 1})
    assert r.budget_rows[0].over == 0 and r.budget_rows[0].carried == 1


# ----------------------------------------------------------- items 6, 7, 8

BASE = ["--config", EXAMPLE, "--start-year", "2027", "--years", "1"]


def test_efficiency_is_rejected_under_goal(capsys):
    err = cli(capsys, *BASE, "--goal", "weekends", "--efficiency", expect=2)
    assert "--efficiency" in err and "classic planner" in err


def test_goal_json_labels_the_headline(capsys):
    data = json.loads(cli(capsys, *BASE, "--goal", "balanced", "--format", "json"))
    plan = data["plan"]
    assert plan["headline"] == "honest"
    assert "every day off in the window" in plan["total_days_off_note"]
    classic = json.loads(cli(capsys, *BASE, "--pto", "5", "--format", "json"))["plan"]
    assert "headline" not in classic and "total_days_off_note" not in classic


@pytest.mark.parametrize("key,value", [
    ("min_vacation_days", True), ("min_vacation_days", "x7"),
    ("min_vacation_days", 7.5), ("max_breaks", "x"),
    ("max_breaks", False), ("max_breaks", 2.5), ("max_breaks", [3]),
])
def test_bad_integer_keys_are_clean_errors(tmp_path, capsys, key, value):
    path = write(tmp_path, {"pto_days": 3, key: value,
                            "holidays": [{"name": "X", "date": "2027-03-03"}]})
    err = cli(capsys, "--config", path, expect=2)
    assert key in err and "whole number" in err and "Traceback" not in err


@pytest.mark.parametrize("key", ["min_vacation_days", "max_breaks"])
def test_empty_integer_keys_say_what_to_do(tmp_path, capsys, key):
    path = write(tmp_path, {"pto_days": 3, key: None,
                            "holidays": [{"name": "X", "date": "2027-03-03"}]})
    err = cli(capsys, "--config", path, expect=2)
    assert f"{key!r} is empty; delete the line or give it a value" in err


def test_goal_must_be_text(tmp_path, capsys):
    path = write(tmp_path, {"pto_days": 3, "goal": None,
                            "holidays": [{"name": "X", "date": "2027-03-03"}]})
    assert "goal" in cli(capsys, "--config", path, expect=2)


def test_whole_valued_integers_still_work(tmp_path):
    from longweekend.config import build_config
    path = write(tmp_path, {"pto_days": 3, "max_breaks": 2, "min_vacation_days": 6,
                            "holidays": [{"name": "X", "date": "2027-03-03"}]})
    config = build_config(config_path=path)
    assert (config.max_breaks, config.min_vacation_days) == (2, 6)


# ----------------------------------------------------------- item 1: README cannot drift

README = os.path.join(ROOT, "README.md")


def _readme_commands():
    text = open(README, encoding="utf-8").read()
    commands = []
    for block in re.findall(r"```[a-z]*\n(.*?)```", text, re.S):
        for line in block.splitlines():
            if line.startswith("long-weekend") and "employer-2027" in line:
                commands.append(line.split("#", 1)[0].strip())
    return text, commands


def test_every_readme_example_command_names_the_year_window():
    _, commands = _readme_commands()
    assert len(commands) >= 5
    for command in commands:
        assert "--start-year 2027" in command and "--years 1" in command, command


def test_every_readme_example_command_runs_and_plans_one_year(capsys):
    _, commands = _readme_commands()
    for command in commands:
        argv = shlex.split(command)[1:]
        argv = [a.replace("examples/", os.path.join(ROOT, "examples") + os.sep) for a in argv]
        if "--goal" in argv:
            argv += ["--format", "json"]
            assert main(argv) == 0
            data = json.loads(capsys.readouterr().out)
            assert data["honest"]["pto_used"] <= 17, command
            assert {b["year"] for b in data["honest"]["budgets"]} == {2027}, command
        else:
            assert main(argv) == 0, command
            capsys.readouterr()


@pytest.mark.parametrize("label,extra,numbers", [
    ("weekends", ["--goal", "weekends"], (17, 59, 5)),
    ("weekends --max-breaks 14", ["--goal", "weekends", "--max-breaks", "14"], (14, 55, 10)),
    ("balanced", ["--goal", "balanced"], (6, 45, 10)),
    ("vacations", ["--goal", "vacations"], (4, 39, 12)),
])
def test_readme_table_matches_the_printed_numbers(capsys, label, extra, numbers):
    text, _ = _readme_commands()
    row = "| `%s` | %d | %d | %d |" % ((label,) + numbers)
    assert row in text
    path = os.path.join(ROOT, "examples", "employer-2027.yaml")
    pytest.importorskip("yaml")
    argv = ["--config", path, "--start-year", "2027", "--years", "1", *extra, "--format", "json"]
    assert main(argv) == 0
    h = json.loads(capsys.readouterr().out)["honest"]
    assert (h["breaks_count"], h["total_days"], h["longest"]) == numbers
    assert h["pto_used"] == 17


# ----------------------------------------------------------- item 5: ICS injection (#14)

EVIL_NAMES = [
    "Evil\rBEGIN:VEVENT\rSUMMARY:pwn",
    "Evil\nBEGIN:VEVENT\nSUMMARY:pwn",
    "Evil\r\nBEGIN:VEVENT\r\nSUMMARY:pwn",
    "Evil BEGIN:VEVENT SUMMARY:pwn",
    "Evil\u0085BEGIN:VEVENT\u0085SUMMARY:pwn",
    "Evil\x0bBEGIN:VEVENT\x0cSUMMARY:pwn\x00\x1b\x7f\ttab",
]


def _unfold(lines):
    out = []
    for line in lines:
        if line.startswith(" ") and out:
            out[-1] += line[1:]
        else:
            out.append(line)
    return out


def _event_count(ics):
    counts = []
    for lines in (ics.split("\r\n"), ics.splitlines()):
        unfolded = _unfold(lines)
        counts.append(sum(1 for line in unfolded if line.startswith("BEGIN:VEVENT")))
        assert not any(line.startswith("SUMMARY:pwn") for line in unfolded)
        assert not any(line.startswith("BEGIN:VEVENT") and line != "BEGIN:VEVENT" for line in unfolded)
    return counts


@pytest.mark.parametrize("evil", EVIL_NAMES)
def test_holiday_names_cannot_split_ics_events(tmp_path, capsys, evil):
    path = write(tmp_path, {"name": evil, "observance": "none", "holidays": [
        {"name": evil, "date": "2027-03-03", "status": "assumed"},
        {"name": "Plain", "date": "2027-03-04"}]})
    ics = cli(capsys, "--config", path, "--start-year", "2027", "--years", "1", "--format", "ics")
    assert _event_count(ics) == [2, 2]
    assert "X-LWP-ASSUMPTION:" in ics


@pytest.mark.parametrize("evil", EVIL_NAMES)
def test_goal_mode_ics_with_hostile_names(tmp_path, capsys, evil):
    path = write(tmp_path, {"observance": "none", "pto_days": 2, "holidays": [
        {"name": evil, "date": "2027-03-03", "status": "assumed"}]})
    ics = cli(capsys, "--config", path, "--start-year", "2027", "--years", "1",
              "--goal", "weekends", "--format", "ics")
    n = ics.count("\r\nBEGIN:VEVENT")
    assert _event_count(ics) == [n, n]
    assert "PROVISIONAL" in ics


def test_normal_names_are_escaped_as_before():
    from longweekend.report import _escape
    assert _escape("A, B; C\\D\nE") == "A\\, B\\; C\\\\D\\nE"
    assert _escape("Plain Name (observed)") == "Plain Name (observed)"


# ----------------------------------------------------------- round 2: pad and featured clauses (#7)

PADDED = ["--start-year", "2027", "--years", "1", "--goal", "weekends", "--pto", "3"]


def one_assumed(tmp_path, date):
    return write(tmp_path, {"observance": "none", "pto_days": 3, "holidays": [
        {"name": "Anchor", "date": "2027-06-02", "status": "confirmed"},
        {"name": "Edge", "date": date, "status": "assumed"}]})


@pytest.mark.parametrize("date", ["2028-01-25", "2028-01-31", "2026-12-01"])
def test_assumed_day_inside_the_pad_but_in_no_break_is_provisional(tmp_path, capsys, date):
    out = cli(capsys, "--config", one_assumed(tmp_path, date), *PADDED)
    assert "PROVISIONAL" in out and "Edge" in out.split("PROVISIONAL", 1)[1]


@pytest.mark.parametrize("date", ["2028-02-01", "2026-11-30"])
def test_assumed_day_just_past_the_pad_is_not_flagged(tmp_path, capsys, date):
    path = one_assumed(tmp_path, date)
    assert "PROVISIONAL" not in cli(capsys, "--config", path, *PADDED)
    data = json.loads(cli(capsys, "--config", path, *PADDED, "--format", "json"))
    assert "provisional" not in data


def test_assumed_day_beyond_the_pad_but_inside_a_featured_break_is_provisional(tmp_path, capsys):
    holidays = []
    day = D(2028, 1, 3)
    while day <= D(2028, 2, 15):
        if day.weekday() < 5:
            status = "assumed" if day == D(2028, 2, 10) else "confirmed"
            holidays.append({"name": "H%s" % day.isoformat(), "date": day.isoformat(), "status": status})
        day += dt.timedelta(1)
    path = write(tmp_path, {"observance": "none", "pto_days": 1, "holidays": holidays})
    argv = ["--config", path, "--start-year", "2027", "--years", "1", "--goal", "weekends",
            "--pto", "1", "--format", "json"]
    data = json.loads(cli(capsys, *argv))
    assert max(b["end"] for b in data["honest"]["breaks"]) >= "2028-02-10"
    assert data["provisional"] is True
    assert [a["observed"] for a in data["assumptions"]] == ["2028-02-10"]


# ----------------------------------------------------------- round 2: same-date ICS consistency (#7)


def test_same_date_confirmed_and_assumed_carries_no_provisional_wording_anywhere(tmp_path, capsys):
    path = write(tmp_path, {"observance": "none", "pto_days": 3, "holidays": [
        {"name": "A", "date": "2027-01-01", "status": "confirmed"},
        {"name": "B", "date": "2027-01-01", "status": "assumed"}]})
    base = ["--config", path, "--start-year", "2027", "--years", "1"]
    for extra in ([], ["--goal", "weekends"]):
        assert "PROVISIONAL" not in cli(capsys, *base, *extra)
        assert "provisional" not in json.loads(cli(capsys, *base, *extra, "--format", "json"))
        ics = cli(capsys, *base, *extra, "--format", "ics")
        assert "PROVISIONAL" not in ics and "X-LWP-ASSUMPTION" not in ics

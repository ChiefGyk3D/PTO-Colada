"""--goal, --min-vacation-days, --prefer, --max-breaks end to end."""

from __future__ import annotations

import datetime as dt
import json
import os

import pytest

from longweekend.cli import main
from longweekend.config import build_config

D = dt.date
EXAMPLE = os.path.join(os.path.dirname(__file__), "..", "examples", "employer-2027.json")
BASE = ["--config", EXAMPLE, "--start-year", "2027", "--years", "1", "--pto", "17"]


def run(capsys, *argv):
    code = main(list(argv))
    captured = capsys.readouterr()
    assert code == 0, captured.err
    return captured.out


def run_json(capsys, *argv):
    return json.loads(run(capsys, *BASE, *argv, "--format", "json"))


def fails(capsys, *argv):
    with pytest.raises(SystemExit) as exc:
        main(list(BASE) + list(argv))
    assert exc.value.code == 2
    return capsys.readouterr().err


class TestGoals:
    @pytest.mark.parametrize("goal", ["weekends", "vacations", "balanced"])
    def test_each_goal_reports_the_honest_numbers(self, capsys, goal):
        data = run_json(capsys, "--goal", goal)
        honest = data["honest"]
        assert data["goal"]["name"] == goal
        assert data["goal"]["method"].startswith("exact")
        assert honest["pto_used"] <= 17
        assert honest["total_days"] == (
            honest["pto_used"] + honest["holidays_included"] + honest["weekend_days_included"]
        )
        assert honest["total_days"] == sum(b["length"] for b in honest["breaks"])
        assert honest["days_in_selected_years"] + honest["days_in_adjacent_years"] == honest["total_days"]
        assert all(b["pto_cost"] >= 1 for b in honest["breaks"])

    def test_vacations_with_minimum_length(self, capsys):
        data = run_json(capsys, "--goal", "vacations", "--min-vacation-days", "9")
        assert all(b["length"] >= 9 for b in data["honest"]["breaks"])
        assert data["goal"]["min_vacation_days"] == 9

    def test_min_vacation_days_alone_implies_vacations(self, capsys):
        data = run_json(capsys, "--min-vacation-days", "8")
        assert data["goal"]["name"] == "vacations"

    def test_other_options_alone_imply_weekends(self, capsys):
        data = run_json(capsys, "--max-breaks", "3")
        assert data["goal"]["name"] == "weekends"
        assert len(data["honest"]["breaks"]) <= 3

    def test_max_breaks_is_exact_and_said_so(self, capsys):
        out = run(capsys, *BASE, "--goal", "weekends", "--max-breaks", "6")
        assert "exact" in out
        data = run_json(capsys, "--goal", "weekends", "--max-breaks", "6")
        assert len(data["honest"]["breaks"]) == 6

    def test_prefer_date_is_taken(self, capsys):
        # One day of leave: without a preference Nov 29 wins (a 5-day break scores 5);
        # a preferred Jan 4 is a 4-day break worth 4 + the 2-point bonus.
        plain = run_json(capsys, "--goal", "weekends", "--pto", "1")
        assert "2027-01-04" not in plain["plan"]["pto_dates"]
        data = run_json(capsys, "--goal", "weekends", "--pto", "1", "--prefer", "2027-01-04")
        assert data["plan"]["pto_dates"] == ["2027-01-04"]
        assert data["goal"]["prefer"] == ["2027-01-04"]

    def test_prefer_range(self):
        config = build_config(prefer=["2027-03-01:2027-03-03"])
        assert len(config.prefer) == 3

    def test_blackout_is_respected(self, capsys):
        free = run_json(capsys, "--goal", "weekends")["plan"]["pto_dates"]
        blocked = free[0]
        data = run_json(capsys, "--goal", "weekends", "--blackout", blocked)
        assert blocked not in data["plan"]["pto_dates"]

    def test_year_end_break_is_whole_in_the_output(self, capsys):
        data = run_json(
            capsys, "--goal", "weekends", "--commit", "2027-12-27:2027-12-30"
        )
        ends = [b["end"] for b in data["honest"]["breaks"]]
        assert "2028-01-02" in ends
        assert data["honest"]["days_by_year"].get("2028", 0) >= 2

    def test_json_is_provisional(self, capsys):
        data = run_json(capsys, "--goal", "balanced")
        assert data["provisional"] is True


class TestText:
    def test_text_has_the_sections(self, capsys):
        out = run(capsys, *BASE, "--goal", "balanced")
        for needle in (
            "PROVISIONAL", "Goal: balanced", "Honest count", "PTO budget per year",
            "Every nonworking day", "Tradeoff",
        ):
            assert needle in out, needle
        assert "= 17 PTO" in out or "PTO +" in out

    def test_tradeoff_never_crowns_the_biggest_total(self, capsys):
        out = run(capsys, *BASE, "--goal", "vacations")
        tradeoff = out.split("Tradeoff", 1)[1]
        for goal in ("weekends", "vacations", "balanced"):
            assert goal in tradeoff
        assert "not automatically" in tradeoff
        assert "best" not in tradeoff.lower().replace("not automatically better", "")
        assert "<- your goal" in tradeoff

    def test_bridge_is_highlighted_when_present(self, capsys):
        out = run(
            capsys, *BASE, "--goal", "weekends",
            "--commit", "2027-11-22:2027-11-24", "--commit", "2027-12-27:2027-12-30",
        )
        assert "Thanksgiving + Christmas/New Year bridge" in out
        assert "7 PTO" in out and "19 calendar days" in out

    def test_bridge_absent_when_the_plan_lacks_it(self, capsys):
        out = run(capsys, *BASE, "--goal", "vacations", "--max-breaks", "1")
        assert "bridge" not in out

    def test_default_output_has_no_goal_sections(self, capsys):
        out = run(capsys, *BASE)
        assert "Tradeoff" not in out and "Honest count" not in out

    def test_uncharged_requests_are_listed(self, capsys):
        out = run(capsys, *BASE, "--goal", "weekends", "--commit", "2027-01-01")
        assert "cost nothing" in out or "free" in out


class TestIcs:
    def test_ics_has_pto_and_breaks_and_provisional(self, capsys):
        out = run(capsys, *BASE, "--goal", "balanced", "--format", "ics")
        assert "CATEGORIES:PTO" in out and "CATEGORIES:BREAK" in out
        assert "X-LWP-PROVISIONAL:TRUE" in out


class TestErrors:
    def test_goal_without_any_budget(self, capsys):
        with pytest.raises(SystemExit) as exc:
            main(["--goal", "weekends", "--start-year", "2027", "--years", "1"])
        assert exc.value.code == 2
        assert "--pto" in capsys.readouterr().err

    def test_goal_conflicts_with_curve_options(self, capsys):
        assert "classic planner" in fails(capsys, "--goal", "weekends", "--min-return", "2")
        assert "--curve" in fails(capsys, "--goal", "weekends", "--curve")

    def test_goal_conflicts_with_style(self, capsys):
        assert "--style" in fails(capsys, "--goal", "weekends", "--style", "vacations")

    def test_bad_values(self, capsys):
        assert "min_vacation_days" in fails(capsys, "--min-vacation-days", "1")
        assert "max_breaks" in fails(capsys, "--max-breaks", "0")
        assert "blacked out" in fails(
            capsys, "--blackout", "2027-03-01", "--prefer", "2027-03-01", "--goal", "weekends"
        )

    def test_config_file_can_set_the_options(self, tmp_path, capsys):
        path = tmp_path / "c.json"
        path.write_text(json.dumps({
            "observance": "none", "pto_days": 5, "goal": "vacations", "min_vacation_days": 6,
            "max_breaks": 2, "prefer": ["2027-03-01"],
            "holidays": [{"name": "X", "date": "2027-03-03"}],
        }))
        config = build_config(config_path=str(path))
        assert (config.goal, config.min_vacation_days, config.max_breaks) == ("vacations", 6, 2)
        assert D(2027, 3, 1) in config.prefer


class TestValidationCasesThroughTheCli:
    """Cases A and B are given PTO sets, committed in full so the planner has no choice."""

    @staticmethod
    def commits(dates):
        out = []
        for date in dates:
            out += ["--commit", date]
        return out

    def test_case_a(self, capsys):
        dates = ["2027-01-04", "2027-05-28", "2027-07-02", "2027-09-03", "2027-11-24",
                 "2027-12-27:2027-12-30", "2027-02-05", "2027-03-05", "2027-04-09",
                 "2027-05-07", "2027-06-04", "2027-08-06", "2027-10-01", "2027-11-05"]
        h = run_json(capsys, "--goal", "weekends", *self.commits(dates))["honest"]
        assert (h["pto_used"], h["breaks_count"], h["total_days"]) == (17, 14, 55)
        assert (h["holidays_included"], h["weekend_days_included"]) == (8, 30)
        assert (h["days_in_selected_years"], h["days_in_adjacent_years"], h["longest"]) == (53, 2, 10)
        assert [b["length"] for b in h["breaks"]] == [4, 3, 3, 3, 3, 4, 3, 4, 3, 4, 3, 3, 5, 10]

    def test_case_b(self, capsys):
        dates = ["2027-01-04", "2027-06-01:2027-06-04", "2027-07-06:2027-07-09", "2027-09-03",
                 "2027-11-22:2027-11-24", "2027-12-27:2027-12-30"]
        data = run_json(capsys, "--goal", "balanced", *self.commits(dates))
        h = data["honest"]
        assert (h["pto_used"], h["breaks_count"], h["total_days"]) == (17, 6, 45)
        assert (h["holidays_included"], h["weekend_days_included"]) == (8, 20)
        assert (h["days_in_selected_years"], h["days_in_adjacent_years"], h["longest"]) == (43, 2, 10)
        assert [b["length"] for b in h["breaks"]] == [4, 9, 9, 4, 9, 10]
        assert h["thanksgiving_bridge"] == {"pto": 7, "calendar_days": 19}


class TestObservedShiftsThroughTheCli:
    def test_saturday_holiday_is_observed_on_friday_and_costs_nothing(self, tmp_path, capsys):
        path = tmp_path / "c.json"
        path.write_text(json.dumps({
            "pto_days": 1, "holidays": [{"name": "July Fourth", "month": 7, "day": 4}],
        }))
        argv = ["--config", str(path), "--start-year", "2026", "--years", "1", "--pto", "1",
                "--goal", "weekends", "--commit", "2026-07-03", "--format", "json"]
        assert main(argv) == 0
        data = json.loads(capsys.readouterr().out)
        h = data["honest"]
        # 2026-07-04 is a Saturday: observed Friday Jul 3, so the "PTO" there is free
        # and the one real day of leave is spent elsewhere.
        assert "2026-07-03" not in data["plan"]["pto_dates"]
        assert h["pto_used"] == 1
        assert h["uncharged_requests"] == [{"date": "2026-07-03", "reason": "holiday"}]

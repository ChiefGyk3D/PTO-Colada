"""Planning from today in hours: refill, rollover, rounding, and rest."""

from __future__ import annotations

import datetime as dt
import json
from decimal import Decimal

import pytest

from longweekend.catalog import PRESETS, resolve_year
from longweekend.cli import main
from longweekend.config import _FILE_KEYS
from longweekend.horizon import (
    add_months,
    build_horizon,
    hours_to_days,
    parse_hours,
    rest_metrics,
)
from longweekend.planner import PtoBudget, optimize_pto
from longweekend.schedule import Day, DayKind, build_days

TODAY = "2026-11-02"


def run_json(capsys, *argv):
    assert main(list(argv) + ["--format", "json"]) == 0
    return json.loads(capsys.readouterr().out)


def federal_days(start, end):
    holidays = []
    for year in range(start.year - 1, end.year + 2):
        holidays.extend(resolve_year(PRESETS["federal"], year))
    return build_days(start, end, holidays)


class TestRounding:
    def test_whole_days_planned_leftover_reported(self):
        assert hours_to_days(Decimal(36), Decimal(8)) == (4, Decimal(4))

    def test_less_than_a_day_plans_nothing(self):
        assert hours_to_days(Decimal("7.5"), Decimal(8)) == (0, Decimal("7.5"))

    def test_exact_multiple_has_no_leftover(self):
        assert hours_to_days(Decimal(40), Decimal(8)) == (5, Decimal(0))

    def test_hours_per_day_changes_the_conversion(self):
        assert hours_to_days(Decimal(36), Decimal("7.5")) == (4, Decimal(6))

    def test_cli_states_the_rule_and_the_leftover(self, capsys):
        assert main(["--today", TODAY, "--pto-hours", "36", "--no-holidays"]) == 0
        out = capsys.readouterr().out
        assert "a workday is 8 hours" in out
        assert "36 hours = 4 day(s) planned, 4 hour(s) left over and not planned" in out
        assert "rounded down" in out

    def test_hours_per_day_flag(self, capsys):
        data = run_json(capsys, "--today", TODAY, "--pto-hours", "36", "--hours-per-day", "6")
        assumptions = data["horizon"]["assumptions"]
        assert assumptions["balance_days"] == 6
        assert assumptions["balance_leftover_hours"] == 0


class TestNovemberIntoMarch:
    ARGS = (
        "--today", TODAY, "--pto-hours", "36", "--refill-hours", "120",
        "--through", "2027-03-31", "--commit", "2027-03-15:2027-03-19",
    )

    def test_plans_only_future_days_through_the_horizon(self, capsys):
        data = run_json(capsys, *self.ARGS)
        dates = [dt.date.fromisoformat(d) for d in data["plan"]["pto_dates"]]
        assert dates and min(dates) >= dt.date(2026, 11, 2)
        assert max(dates) <= dt.date(2027, 3, 31)
        assert data["horizon"]["through"] == "2027-03-31"

    def test_march_break_is_in_the_plan(self, capsys):
        data = run_json(capsys, *self.ARGS)
        march = {f"2027-03-{d}" for d in (15, 16, 17, 18, 19)}
        assert march <= set(data["plan"]["pto_dates"])

    def test_this_year_uses_the_balance_and_next_year_the_refill(self, capsys):
        data = run_json(capsys, *self.ARGS)
        assert data["plan"]["granted_by_year"] == {"2026": 4, "2027": 15}
        assert data["plan"]["spent_by_year"]["2026"] <= 4

    def test_months_horizon_crosses_the_year(self, capsys):
        data = run_json(capsys, "--today", TODAY, "--pto-hours", "36", "--months", "5")
        assert data["horizon"]["through"] == "2027-04-02"

    def test_default_horizon_is_the_end_of_this_year(self, capsys):
        data = run_json(capsys, "--today", TODAY, "--pto-hours", "36")
        assert data["horizon"]["through"] == "2026-12-31"
        assert list(data["plan"]["granted_by_year"]) == ["2026"]

    def test_text_states_the_assumptions(self, capsys):
        assert main(list(self.ARGS) + ["--carryover", "5"]) == 0
        out = capsys.readouterr().out
        assert "refill on each January 1 inside the window: 120 hours = 15 day(s)" in out
        assert "carryover: up to 5 day(s) (40 hours)" in out
        assert "Longest working stretch" in out
        assert "Efficiency:" in out  # rest metrics sit next to the efficiency score

    def test_a_pto_year_override_wins_and_is_reported(self, capsys):
        data = run_json(capsys, *self.ARGS, "--pto-year", "2027=7")
        assert data["plan"]["granted_by_year"]["2027"] == 7
        assert data["horizon"]["assumptions"]["granted_days_by_year"]["2027"] == 7


class TestRefillAndCarryover:
    # 400 hours = 50 days on 2026-12-01: far more than December can use.
    BASE = ("--today", "2026-12-01", "--pto-hours", "400", "--refill-hours", "64",
            "--through", "2027-01-31")

    def test_jan_first_refill_lands_in_the_next_year(self, capsys):
        data = run_json(capsys, *self.BASE)
        assert data["plan"]["granted_by_year"] == {"2026": 50, "2027": 8}
        assert data["horizon"]["assumptions"]["refill_days"] == 8

    def test_carryover_cap_limits_what_rolls_and_the_rest_is_lost(self, capsys):
        data = run_json(capsys, *self.BASE, "--carryover", "5")
        row = data["horizon"]["year_ends"][0]
        assert row["year"] == 2026
        assert row["carried_days"] == 5
        assert row["lost_days"] == row["unspent_days"] - 5 > 0
        assert row["lost_hours"] == row["lost_days"] * 8

    def test_without_carryover_everything_unspent_is_lost(self, capsys):
        data = run_json(capsys, *self.BASE)
        row = data["horizon"]["year_ends"][0]
        assert row["carried_days"] == 0
        assert row["lost_days"] == row["unspent_days"]

    def test_carryover_never_exceeds_the_years_grant(self, capsys):
        data = run_json(capsys, "--today", "2026-12-01", "--pto-hours", "24",
                        "--through", "2027-01-31", "--carryover", "10")
        row = data["horizon"]["year_ends"][0]
        assert row["carried_days"] <= 3

    def test_use_it_or_lose_it_is_called_out_in_text(self, capsys):
        assert main(list(self.BASE) + ["--carryover", "5", "--no-holidays"]) == 0
        out = capsys.readouterr().out
        assert "use-it-or-lose-it:" in out
        assert "5 carry over (40 hours)" in out

    def test_no_year_end_row_when_the_horizon_stops_before_december_31(self, capsys):
        data = run_json(capsys, "--today", TODAY, "--pto-hours", "36", "--through", "2026-12-15")
        assert data["horizon"]["year_ends"] == []


class TestRestMetrics:
    def test_metrics_of_a_plain_window(self):
        # Mon Dec 14 2026 to Fri Jan 8 2027. Christmas (Fri 25) and New Year's Day
        # (Fri Jan 1) each make a 3-day weekend, so there are two rest breaks and the
        # working stretches are 9 (Dec 14-24), 4 (Dec 28-31) and 5 (Jan 4-8).
        metrics = rest_metrics(federal_days(dt.date(2026, 12, 14), dt.date(2027, 1, 8)))
        assert metrics.rest_breaks == 2
        assert metrics.longest == 9
        assert (metrics.longest_start, metrics.longest_end) == (
            dt.date(2026, 12, 14), dt.date(2026, 12, 24))
        assert metrics.average_gap == 4.0  # only the stretch between the two breaks

    def test_one_break_has_no_average_gap(self):
        metrics = rest_metrics(federal_days(dt.date(2026, 12, 14), dt.date(2026, 12, 31)))
        assert metrics.rest_breaks == 1
        assert metrics.average_gap is None

    def test_average_gap_needs_two_breaks(self):
        days = federal_days(dt.date(2026, 11, 2), dt.date(2027, 1, 29))
        metrics = rest_metrics(days)
        assert metrics.rest_breaks >= 2
        assert metrics.average_gap is not None and metrics.average_gap > 0

    def test_empty_window(self):
        metrics = rest_metrics([])
        assert metrics.longest == 0 and metrics.average_gap is None

    def test_cli_reports_the_metrics(self, capsys):
        data = run_json(capsys, "--today", TODAY, "--pto-hours", "36", "--through", "2027-03-31")
        rest = data["horizon"]["rest"]
        assert rest["longest_working_stretch"] > 0
        assert rest["rest_breaks"] >= 2
        assert rest["average_gap_working_days"] is not None


class TestMaxWorkStreak:
    WINDOW = (dt.date(2026, 11, 2), dt.date(2027, 3, 31))

    def plan(self, streak, per_year):
        days = federal_days(*self.WINDOW)
        return optimize_pto(
            days, PtoBudget(per_year=per_year, default=15), max_work_streak=streak,
        )

    def test_guard_bites_without_the_limit(self):
        days = federal_days(*self.WINDOW)
        free = optimize_pto(days, PtoBudget(per_year={2026: 4}, default=15))
        assert rest_metrics(free.days).longest > 12

    def test_feasible_limit_is_honored(self):
        plan = self.plan(12, {2026: 4})
        # The limit binds somewhere, and nothing exceeds it.
        assert rest_metrics(plan.days).longest == 12

    @staticmethod
    def straight_work(count):
        start = dt.date(2026, 3, 2)
        return [Day(start + dt.timedelta(days=i), DayKind.WORK) for i in range(count)]

    def test_limit_equal_to_the_stretch_is_feasible_one_less_is_not(self):
        # Ten working days in a row, no PTO, no days off: the stretch is exactly 10.
        days = self.straight_work(10)
        plan = optimize_pto(days, PtoBudget(default=0), max_work_streak=10)
        assert rest_metrics(plan.days).longest == 10
        with pytest.raises(ValueError, match="--max-work-streak 9 cannot be met"):
            optimize_pto(days, PtoBudget(default=0), max_work_streak=9)

    def test_pto_makes_the_tighter_limit_feasible(self):
        # Fourteen straight working days: limit 7 needs a 3-day rest break from PTO.
        days = self.straight_work(14)
        with pytest.raises(ValueError, match="cannot be met"):
            optimize_pto(days, PtoBudget(default=2), max_work_streak=7)
        plan = optimize_pto(days, PtoBudget(default=3), max_work_streak=7)
        assert plan.total_pto == 3
        assert rest_metrics(plan.days).longest <= 7

    def test_limit_that_needs_no_pto_changes_nothing(self):
        days = federal_days(*self.WINDOW)
        free = optimize_pto(days, PtoBudget(per_year={2026: 4}, default=15))
        longest = rest_metrics(free.days).longest
        same = optimize_pto(
            days, PtoBudget(per_year={2026: 4}, default=15), max_work_streak=longest
        )
        # Equal-scoring plans may differ in which tied days they pick; the score may not.
        assert (same.days_in_breaks, same.total_pto) == (free.days_in_breaks, free.total_pto)

    def test_infeasible_limit_is_a_clear_error(self):
        with pytest.raises(ValueError, match="--max-work-streak 5 cannot be met"):
            optimize_pto(
                federal_days(*self.WINDOW), PtoBudget(default=0), max_work_streak=5
            )

    def test_infeasible_through_the_cli(self, capsys):
        with pytest.raises(SystemExit) as exit_info:
            main(["--today", TODAY, "--pto-hours", "8", "--through", "2027-03-31",
                  "--max-work-streak", "10"])
        assert exit_info.value.code == 2
        err = capsys.readouterr().err
        assert "--max-work-streak 10 cannot be met" in err
        assert "Raise the PTO balance" in err

    def test_feasible_through_the_cli(self, capsys):
        data = run_json(capsys, "--today", TODAY, "--pto-hours", "400",
                        "--refill-hours", "120", "--through", "2027-03-31",
                        "--max-work-streak", "15")
        rest = data["horizon"]["rest"]
        assert rest["longest_working_stretch"] <= 15
        assert rest["max_work_streak"] == 15

    def test_goal_cannot_be_combined(self, capsys):
        with pytest.raises(SystemExit):
            main(["--today", TODAY, "--pto-hours", "40", "--goal", "balanced",
                  "--max-work-streak", "15"])
        assert "classic planner" in capsys.readouterr().err

    def test_zero_is_rejected(self):
        with pytest.raises(ValueError, match="at least 1"):
            optimize_pto(federal_days(*self.WINDOW), PtoBudget(default=5), max_work_streak=0)


class TestRefillClauses:
    def test_no_january_first_in_window_says_so(self, capsys):
        assert main(["--today", TODAY, "--pto-hours", "36", "--refill-hours", "120",
                     "--no-holidays"]) == 0
        out = capsys.readouterr().out
        assert "not used: the window has no January 1 after today" in out
        assert "refill on each January 1" not in out

    def test_today_on_january_first_gets_no_refill_that_day(self, capsys):
        assert main(["--today", "2027-01-01", "--pto-hours", "36", "--refill-hours", "120",
                     "--through", "2027-06-30", "--no-holidays"]) == 0
        out = capsys.readouterr().out
        assert "today is January 1: that day's refill is not added" in out

    def test_hours_per_day_alone_prints_a_note(self, capsys):
        assert main(["--hours-per-day", "6", "--start-year", "2026", "--years", "1",
                     "--no-holidays"]) == 0
        assert "--hours-per-day has no effect without --pto-hours" in capsys.readouterr().err


class TestGoalPathStillWorks:
    def test_goal_plans_from_today(self, capsys):
        data = run_json(capsys, "--today", TODAY, "--pto-hours", "80",
                        "--refill-hours", "120", "--through", "2027-03-31",
                        "--goal", "balanced")
        assert "horizon" in data
        assert min(data["plan"]["pto_dates"]) >= TODAY


class TestHardening:
    """Limits on the new options; the config loader's own limits are unchanged."""

    @pytest.mark.parametrize("args, message", [
        (["--pto-hours", "1e30"], "hour limit"),
        (["--pto-hours", "inf"], "finite"),
        (["--pto-hours", "nan"], "finite"),
        (["--pto-hours", "-1"], "negative"),
        (["--pto-hours", "abc"], "number of hours"),
        (["--pto-hours", "8.123"], "two decimal"),
        (["--pto-hours", "8", "--hours-per-day", "0"], "above 0"),
        (["--pto-hours", "8", "--hours-per-day", "25"], "at most 24"),
        (["--pto-hours", "8", "--refill-hours", "99999999"], "hour limit"),
        (["--pto-hours", "8", "--months", "0"], "between 1 and 60"),
        (["--pto-hours", "8", "--months", "1000000000"], "between 1 and 60"),
        (["--pto-hours", "8", "--through", "2026-10-01"], "before today"),
        (["--pto-hours", "8", "--through", "2200-01-01"], "60 months"),
        (["--pto-hours", "8", "--through", "9999-12-31"], "outside the years"),
        (["--pto-hours", "8", "--through", "not-a-date"], "YYYY-MM-DD"),
        (["--pto-hours", "8", "--through", "2027-01-01", "--months", "3"], "not both"),
        (["--pto-hours", "8", "--max-work-streak", "0"], "between 1 and"),
        (["--pto-hours", "8", "--max-work-streak", "99999999"], "between 1 and"),
        (["--refill-hours", "8"], "need --pto-hours"),
        (["--max-work-streak", "8"], "need --pto-hours"),
        (["--pto-hours", "8", "--pto", "10"], "replaces --pto"),
        (["--pto-hours", "8", "--start-year", "2026"], "plans from today"),
        (["--pto-hours", "8", "--from-today"], "plans from today"),
    ])
    def test_rejects(self, args, message, capsys):
        with pytest.raises(SystemExit) as exit_info:
            main(["--today", TODAY] + args)
        assert exit_info.value.code == 2
        assert message in capsys.readouterr().err

    @pytest.mark.parametrize("today", ["1899-12-31", "2201-01-01", "2026-13-01", "x"])
    def test_today_is_bounded(self, today, capsys):
        with pytest.raises(SystemExit):
            main(["--today", today, "--pto-hours", "8"])
        err = capsys.readouterr().err
        assert "--today" in err or "YYYY-MM-DD" in err

    def test_error_text_does_not_echo_huge_input(self):
        with pytest.raises(ValueError) as info:
            parse_hours("9" * 5000, "--pto-hours")
        assert len(str(info.value)) < 200

    @pytest.mark.parametrize("key", ["pto_hours", "refill_hours", "today", "max_work_streak"])
    def test_config_files_gain_no_new_keys(self, key, tmp_path, capsys):
        # The new options are command-line only: the loader's key allowlist is
        # unchanged, so its size, depth and node limits cover everything it reads,
        # and a file cannot smuggle the options in.
        assert key not in _FILE_KEYS
        base = {"name": "Acme", "holidays": ["Christmas Day"]}
        outputs = []
        for extra in ({}, {key: 8}):
            path = tmp_path / "company.json"
            path.write_text(json.dumps({**base, **extra}))
            assert main(["--config", str(path), "--start-year", "2026", "--years", "1"]) == 0
            outputs.append(capsys.readouterr().out)
        assert outputs[0] == outputs[1]
        assert "Planning from today" not in outputs[1]

    def test_add_months_clamps_to_month_end(self):
        assert add_months(dt.date(2026, 1, 31), 1) == dt.date(2026, 2, 28)
        assert add_months(dt.date(2027, 11, 30), 3) == dt.date(2028, 2, 29)
        assert add_months(dt.date(2026, 12, 15), 1) == dt.date(2027, 1, 15)

    def test_build_horizon_defaults(self):
        spec = build_horizon(dt.date(2026, 11, 2), "36")
        assert spec.end == dt.date(2026, 12, 31)
        assert spec.balance_days == 4 and spec.refill_days == 0


class TestDefaultsUnchanged:
    def test_no_horizon_text_without_the_flags(self, capsys):
        assert main(["--start-year", "2026", "--years", "1", "--pto", "15"]) == 0
        out = capsys.readouterr().out
        assert "Planning from today" not in out

    def test_no_horizon_key_without_the_flags(self, capsys):
        data = run_json(capsys, "--start-year", "2026", "--years", "1", "--pto", "15")
        assert "horizon" not in data

    def test_today_alone_only_moves_the_clock(self, capsys):
        assert main(["--today", "2030-06-01", "--years", "1", "--no-holidays"]) == 0
        assert "Planning from today" not in capsys.readouterr().out

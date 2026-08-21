"""Schedule construction, break detection and PTO optimisation."""

from __future__ import annotations

import datetime as dt
import itertools

import pytest

from longweekend.catalog import PRESETS, FixedDate, parse_workweek, resolve_year
from longweekend.planner import PtoBudget, optimize_pto
from longweekend.schedule import DayKind, build_days, find_breaks, find_opportunities


def make_days(start, end, rules=(), workweek=None, blackout=None):
    holidays = []
    for year in range(start.year - 1, end.year + 2):
        holidays.extend(resolve_year(list(rules), year, workweek))
    return build_days(start, end, holidays, workweek, blackout)


def score(days, min_break, long_break_bonus=0):
    """Reference scorer: total days inside runs of at least ``min_break``."""
    total = 0
    run = 0
    for day in list(days) + [None]:
        if day is not None and day.is_off:
            run += 1
            continue
        if run >= min_break:
            total += min_break + (run - min_break) * (1 + long_break_bonus)
        run = 0
    return total


class TestSchedule:
    def test_weekend_and_workday_classification(self):
        days = make_days(dt.date(2026, 6, 1), dt.date(2026, 6, 7))
        kinds = [d.kind for d in days]
        assert kinds[:5] == [DayKind.WORK] * 5  # Mon-Fri
        assert kinds[5:] == [DayKind.WEEKEND] * 2

    def test_non_standard_workweek(self):
        workweek = parse_workweek("SUN-THU")
        days = make_days(dt.date(2026, 6, 1), dt.date(2026, 6, 7), workweek=workweek)
        # 2026-06-05 is a Friday, which is now a day off.
        assert days[4].kind is DayKind.WEEKEND
        assert days[6].kind is DayKind.WORK  # Sunday is a working day

    def test_find_breaks_respects_min_length(self):
        rules = [FixedDate("Test", 7, 3)]  # 2026-07-03 is a Friday
        days = make_days(dt.date(2026, 6, 29), dt.date(2026, 7, 12), rules)
        assert [b.length for b in find_breaks(days, min_length=3)] == [3]
        assert find_breaks(days, min_length=4) == []


class TestOpportunities:
    def test_wednesday_holiday_is_reported_not_dropped(self):
        """Regression: midweek holidays used to produce no output at all."""
        rules = [FixedDate("Veterans Day", 11, 11)]  # Wednesday in 2026
        days = make_days(dt.date(2026, 11, 1), dt.date(2026, 11, 30), rules)
        opportunities = find_opportunities(days)
        assert len(opportunities) == 1
        assert opportunities[0].bridge_cost == 2
        assert opportunities[0].bridged_length == 5

    def test_weekend_holiday_is_reported_via_its_observed_date(self):
        """Regression: Saturday and Sunday holidays used to vanish."""
        rules = [FixedDate("Independence Day", 7, 4)]  # Saturday in 2026
        days = make_days(dt.date(2026, 6, 20), dt.date(2026, 7, 20), rules)
        opportunities = find_opportunities(days)
        assert len(opportunities) == 1
        assert opportunities[0].date == dt.date(2026, 7, 3)
        assert opportunities[0].natural_length == 3

    def test_thursday_holiday_suggests_taking_the_friday(self):
        """Regression: Thanksgiving used to be labelled a three-day weekend."""
        rules = [PRESETS["federal"][-2]]  # Thanksgiving
        days = make_days(dt.date(2026, 11, 1), dt.date(2026, 12, 15), rules)
        opportunity = find_opportunities(days)[0]
        assert opportunity.natural_length == 1
        assert opportunity.bridge_cost == 1
        assert opportunity.bridge_dates == [dt.date(2026, 11, 27)]
        assert opportunity.bridged_length == 4

    def test_monday_holiday_needs_no_bridge_for_three_days(self):
        rules = [PRESETS["federal"][1]]  # MLK Day, always a Monday
        days = make_days(dt.date(2026, 1, 1), dt.date(2026, 2, 15), rules)
        opportunity = find_opportunities(days)[0]
        assert opportunity.natural_length == 3

    def test_blackout_blocks_a_bridge(self):
        rules = [PRESETS["federal"][-2]]  # Thanksgiving 2026 is Nov 26
        blackout = {dt.date(2026, 11, 27)}
        days = make_days(dt.date(2026, 11, 1), dt.date(2026, 12, 15), rules, blackout=blackout)
        opportunity = find_opportunities(days)[0]
        assert dt.date(2026, 11, 27) not in opportunity.bridge_dates


class TestOptimizer:
    RULES = PRESETS["federal"]

    def test_zero_budget_takes_nothing(self):
        days = make_days(dt.date(2026, 1, 1), dt.date(2026, 12, 31), self.RULES)
        plan = optimize_pto(days, PtoBudget(default=0))
        assert plan.pto_dates == []

    def test_budget_is_never_exceeded(self):
        days = make_days(dt.date(2026, 1, 1), dt.date(2027, 12, 31), self.RULES)
        plan = optimize_pto(days, PtoBudget(default=10))
        assert plan.spent_by_year[2026] <= 10
        assert plan.spent_by_year[2027] <= 10

    def test_budget_is_per_year_not_pooled(self):
        days = make_days(dt.date(2026, 1, 1), dt.date(2027, 12, 31), self.RULES)
        plan = optimize_pto(days, PtoBudget(default=5))
        assert plan.spent_by_year.get(2026, 0) <= 5
        assert plan.spent_by_year.get(2027, 0) <= 5

    def test_per_year_override(self):
        days = make_days(dt.date(2026, 1, 1), dt.date(2027, 12, 31), self.RULES)
        plan = optimize_pto(days, PtoBudget(per_year={2027: 0}, default=8))
        assert plan.spent_by_year.get(2027, 0) == 0
        assert plan.granted_by_year[2027] == 0

    def test_carryover_allows_more_in_the_second_year(self):
        days = make_days(dt.date(2026, 1, 1), dt.date(2027, 12, 31), self.RULES)
        plan = optimize_pto(
            days, PtoBudget(per_year={2026: 0, 2027: 2}, default=0, carryover=5), min_break=4
        )
        # Nothing granted in 2026 means nothing to roll over, so 2027 is capped at 2.
        assert plan.spent_by_year.get(2027, 0) <= 2

    def test_pto_days_are_only_ever_working_days(self):
        days = make_days(dt.date(2026, 1, 1), dt.date(2026, 12, 31), self.RULES)
        plan = optimize_pto(days, PtoBudget(default=15))
        original = {d.date: d.kind for d in days}
        assert all(original[date] is DayKind.WORK for date in plan.pto_dates)

    def test_blackout_days_are_never_chosen(self):
        blackout = {dt.date(2026, 4, d) for d in range(1, 31)}
        days = make_days(dt.date(2026, 1, 1), dt.date(2026, 12, 31), self.RULES, blackout=blackout)
        plan = optimize_pto(days, PtoBudget(default=20))
        assert not (set(plan.pto_dates) & blackout)

    def test_committed_days_are_always_taken(self):
        committed = {dt.date(2026, 3, 17), dt.date(2026, 3, 18)}
        days = make_days(dt.date(2026, 1, 1), dt.date(2026, 12, 31), self.RULES)
        plan = optimize_pto(days, PtoBudget(default=15), committed=committed)
        assert committed <= set(plan.pto_dates)

    def test_committed_days_are_charged_to_the_budget(self):
        committed = {dt.date(2026, 3, 17), dt.date(2026, 3, 18)}
        days = make_days(dt.date(2026, 1, 1), dt.date(2026, 12, 31), self.RULES)
        plan = optimize_pto(days, PtoBudget(default=15), committed=committed)
        assert plan.spent_by_year[2026] <= 15

    def test_overcommitting_fails_with_a_clear_message(self):
        committed = {dt.date(2026, 3, d) for d in (17, 18, 19)}
        days = make_days(dt.date(2026, 1, 1), dt.date(2026, 12, 31), self.RULES)
        with pytest.raises(ValueError, match="exceed"):
            optimize_pto(days, PtoBudget(default=2), committed=committed)

    def test_max_consecutive_pto_is_respected(self):
        days = make_days(dt.date(2026, 1, 1), dt.date(2026, 12, 31), self.RULES)
        plan = optimize_pto(days, PtoBudget(default=20), max_consecutive_pto=1)
        taken = sorted(plan.pto_dates)
        runs = [
            len(list(group))
            for _, group in itertools.groupby(
                enumerate(taken), key=lambda pair: pair[1] - dt.timedelta(days=pair[0])
            )
        ]
        assert max(runs) == 1

    def test_holiday_bridges_are_preferred_over_plain_weekends(self):
        days = make_days(dt.date(2026, 1, 1), dt.date(2026, 12, 31), self.RULES)
        plan = optimize_pto(days, PtoBudget(default=3))
        # Every break bought with three days of budget should include a holiday.
        assert all(b.holidays for b in plan.breaks if b.pto_cost)

    def test_vacation_style_produces_longer_breaks(self):
        days = make_days(dt.date(2026, 1, 1), dt.date(2026, 12, 31), self.RULES)
        weekends = optimize_pto(days, PtoBudget(default=15), long_break_bonus=0)
        vacations = optimize_pto(days, PtoBudget(default=15), long_break_bonus=1)
        assert max(b.length for b in vacations.breaks) > max(b.length for b in weekends.breaks)

    def test_min_break_raises_the_target_length(self):
        days = make_days(dt.date(2026, 1, 1), dt.date(2026, 12, 31), self.RULES)
        plan = optimize_pto(days, PtoBudget(default=20), min_break=7)
        assert all(b.length >= 7 for b in plan.breaks)

    def test_empty_window(self):
        plan = optimize_pto([], PtoBudget(default=10))
        assert plan.pto_dates == []
        assert plan.breaks == []

    @pytest.mark.parametrize("min_break", [1, 2])
    def test_small_min_break_values_are_accepted(self, min_break):
        days = make_days(dt.date(2026, 6, 1), dt.date(2026, 6, 30), self.RULES)
        plan = optimize_pto(days, PtoBudget(default=2), min_break=min_break)
        assert len(plan.pto_dates) <= 2

    @pytest.mark.parametrize("bad", [{"min_break": 0}, {"max_consecutive_pto": 0}, {"long_break_bonus": -1}])
    def test_invalid_parameters_raise(self, bad):
        days = make_days(dt.date(2026, 6, 1), dt.date(2026, 6, 30))
        with pytest.raises(ValueError):
            optimize_pto(days, PtoBudget(default=1), **bad)


class TestOptimality:
    """The DP must match exhaustive search on windows small enough to brute-force."""

    @pytest.mark.parametrize(
        "budget,min_break,bonus",
        [(1, 4, 0), (2, 4, 0), (3, 4, 0), (3, 4, 1), (3, 5, 0), (2, 3, 0)],
    )
    def test_matches_brute_force(self, budget, min_break, bonus):
        rules = [FixedDate("Midweek", 7, 8)]  # 2026-07-08 is a Wednesday
        days = make_days(dt.date(2026, 7, 1), dt.date(2026, 7, 24), rules)

        plan = optimize_pto(
            days, PtoBudget(default=budget), min_break=min_break, long_break_bonus=bonus
        )
        dp_score = score(plan.days, min_break, bonus)

        workdays = [i for i, d in enumerate(days) if d.kind is DayKind.WORK]
        best = 0
        for size in range(budget + 1):
            for choice in itertools.combinations(workdays, size):
                trial = [
                    type(d)(d.date, DayKind.PTO if i in choice else d.kind, d.holiday, d.blackout)
                    for i, d in enumerate(days)
                ]
                best = max(best, score(trial, min_break, bonus))

        assert dp_score == best

    def test_spends_budget_across_a_year_boundary_correctly(self):
        days = make_days(dt.date(2026, 12, 15), dt.date(2027, 1, 15), PRESETS["federal"])
        plan = optimize_pto(days, PtoBudget(default=2))
        assert plan.spent_by_year.get(2026, 0) <= 2
        assert plan.spent_by_year.get(2027, 0) <= 2
        # Both years' allowances are usable, so more than one year's worth can
        # be spent inside a window that straddles New Year.
        assert plan.total_pto <= 4

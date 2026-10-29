"""Honest counting: validation cases A and B, merging, boundaries, budgets.

Cases A and B are *validation cases*: given PTO date sets scored by the
counting function. They are not proven optima and the optimizer is not
required to reproduce them.
"""

from __future__ import annotations

import datetime as dt

import pytest

from longweekend.honest import score_pto, thanksgiving_bridge

D = dt.date


def d(m, day, y=2027):
    return D(y, m, day)


def rng(a, b):
    return [a + dt.timedelta(i) for i in range((b - a).days + 1)]


HOLIDAYS = {
    d(1, 1): "New Year's Day",
    d(5, 31): "Memorial Day",
    d(7, 5): "Independence Day (observed)",
    d(9, 6): "Labor Day",
    d(11, 25): "Thanksgiving",
    d(11, 26): "Day After Thanksgiving",
    d(12, 24): "Christmas (observed)",
    d(12, 31): "New Year's 2028 (observed)",
}

CORE = [d(1, 4), d(5, 28), d(7, 2), d(9, 3), d(11, 24)] + rng(d(12, 27), d(12, 30))
FRIDAYS = [d(2, 5), d(3, 5), d(4, 9), d(5, 7), d(6, 4), d(8, 6), d(10, 1), d(11, 5)]
CASE_A = set(CORE + FRIDAYS)
CASE_B = set(
    [d(1, 4)] + rng(d(6, 1), d(6, 4)) + rng(d(7, 6), d(7, 9)) + [d(9, 3)]
    + rng(d(11, 22), d(11, 24)) + rng(d(12, 27), d(12, 30))
)


def oracle(pto, holidays, years_lo=2026, years_hi=2029):
    """The independent reference from the verified script (plain weekday math)."""
    hol = set(holidays)
    off = lambda x: x.weekday() >= 5 or x in hol or x in pto  # noqa: E731
    out, cur, x = [], [], D(years_lo, 1, 1)
    while x <= D(years_hi, 12, 31):
        if off(x):
            cur.append(x)
        else:
            if cur:
                out.append(cur)
            cur = []
        x += dt.timedelta(1)
    return [b for b in out if any(y in pto for y in b)]


class TestCaseA:
    def setup_method(self):
        self.r = score_pto(CASE_A, HOLIDAYS, years=[2027])

    def test_headline_numbers(self):
        r = self.r
        assert r.pto_used == 17
        assert r.breaks_count == 14
        assert r.total_days == 55
        assert (r.pto_used, r.holidays_included, r.weekend_days_included) == (17, 8, 30)
        assert r.days_in_years == 53 and r.days_adjacent == 2
        assert r.days_by_year == {2027: 53, 2028: 2}
        assert r.longest == 10

    def test_break_lengths_in_date_order(self):
        assert [b.length for b in self.r.breaks] == [4, 3, 3, 3, 3, 4, 3, 4, 3, 4, 3, 3, 5, 10]

    def test_six_core_breaks(self):
        core = score_pto(set(CORE), HOLIDAYS, years=[2027])
        assert core.pto_used == 9
        assert core.breaks_count == 6
        assert core.total_days == 31

    def test_matches_the_independent_oracle(self):
        expected = oracle(CASE_A, HOLIDAYS)
        assert [(b.start, b.end) for b in self.r.breaks] == [(b[0], b[-1]) for b in expected]


class TestCaseB:
    def setup_method(self):
        self.r = score_pto(CASE_B, HOLIDAYS, years=[2027])

    def test_headline_numbers(self):
        r = self.r
        assert r.pto_used == 17
        assert [b.length for b in r.breaks] == [4, 9, 9, 4, 9, 10]
        assert r.breaks_count == 6
        assert r.total_days == 45
        assert (r.pto_used, r.holidays_included, r.weekend_days_included) == (17, 8, 20)
        assert (r.days_in_years, r.days_adjacent) == (43, 2)
        assert r.longest == 10

    def test_matches_the_independent_oracle(self):
        expected = oracle(CASE_B, HOLIDAYS)
        assert [(b.start, b.end) for b in self.r.breaks] == [(b[0], b[-1]) for b in expected]


class TestBridge:
    PTO = rng(d(11, 22), d(11, 24)) + rng(d(12, 27), d(12, 30))

    def test_seven_pto_nineteen_days(self):
        r = score_pto(set(self.PTO), HOLIDAYS, years=[2027])
        assert r.pto_used == 7
        assert r.breaks_count == 2
        assert r.total_days == 19

    def test_bridge_is_found(self):
        r = score_pto(set(self.PTO), HOLIDAYS, years=[2027])
        bridge = thanksgiving_bridge(r)
        assert bridge is not None
        assert (bridge.pto, bridge.days) == (7, 19)
        assert bridge.first.start == d(11, 20)
        assert bridge.second.end == D(2028, 1, 2)

    def test_bridge_absent_when_plan_lacks_it(self):
        assert thanksgiving_bridge(score_pto({d(1, 4)}, HOLIDAYS, years=[2027])) is None
        # Only the Thanksgiving half: no second break to join.
        only = score_pto(set(rng(d(11, 22), d(11, 24))), HOLIDAYS, years=[2027])
        assert thanksgiving_bridge(only) is None

    def test_case_b_contains_the_bridge(self):
        assert thanksgiving_bridge(score_pto(CASE_B, HOLIDAYS, years=[2027])) is not None


class TestBreakRules:
    def test_free_breaks_are_not_featured(self):
        # Memorial Day weekend is a 3-day break that costs nothing.
        r = score_pto({d(1, 4)}, HOLIDAYS, years=[2027])
        assert r.breaks_count == 1
        assert r.total_days == 4  # Jan 1 (Fri) - Jan 4 (Mon)
        assert r.free_breaks_count > 5  # reported separately, not in the headline
        assert r.total_days == sum(b.length for b in r.breaks)

    def test_adjacent_pto_breaks_merge(self):
        # Wed-Thu PTO either side of nothing: Mon 2 Aug .. Fri 6 Aug PTO = one break
        r = score_pto(set(rng(d(8, 2), d(8, 6))), HOLIDAYS, years=[2027])
        assert r.breaks_count == 1
        assert r.breaks[0].length == 9  # Sat Jul 31 .. Sun Aug 8

    def test_overlapping_breaks_merge_through_a_holiday(self):
        # Wed Nov 24 and Mon Nov 29 PTO join through Thanksgiving, its Friday and the weekend
        r = score_pto({d(11, 24), d(11, 29)}, HOLIDAYS, years=[2027])
        assert r.breaks_count == 1
        assert r.breaks[0].start == d(11, 24) and r.breaks[0].end == d(11, 29)
        assert r.pto_used == 2

    def test_two_separate_breaks_stay_separate(self):
        r = score_pto({d(2, 5), d(3, 5)}, HOLIDAYS, years=[2027])
        assert r.breaks_count == 2

    def test_each_date_counted_once(self):
        r = score_pto(CASE_A, HOLIDAYS, years=[2027])
        dates = [x for b in r.breaks for x in b.dates]
        assert len(dates) == len(set(dates)) == r.total_days


class TestChargeRules:
    def test_pto_on_a_holiday_or_weekend_costs_nothing(self):
        r = score_pto({d(1, 4), d(1, 1), d(1, 2)}, HOLIDAYS, years=[2027])
        assert r.pto_used == 1
        reasons = {x: why for x, why in r.pto_uncharged}
        assert reasons[d(1, 1)] == "holiday" and reasons[d(1, 2)] == "weekend"

    def test_a_free_request_does_not_make_a_break_featured(self):
        r = score_pto({d(1, 2)}, HOLIDAYS, years=[2027])
        assert r.breaks_count == 0 and r.total_days == 0 and r.pto_used == 0

    def test_non_standard_workweek(self):
        # Sun-Thu workweek: Friday and Saturday are the weekend.
        r = score_pto({d(1, 3)}, {}, workweek={6, 0, 1, 2, 3}, years=[2027])
        assert r.pto_used == 1  # Sunday Jan 3 is a workday here
        assert r.breaks[0].start == d(1, 1) and r.breaks[0].end == d(1, 3)


class TestHolidayChanges:
    def test_adding_a_holiday_changes_counts(self):
        more = dict(HOLIDAYS)
        more[d(1, 18)] = "MLK"  # Monday
        base = score_pto(CASE_A, HOLIDAYS, years=[2027])
        # PTO on the new holiday is now free, so 16 charged
        r = score_pto(CASE_A | {d(1, 19)}, more, years=[2027])
        assert r.pto_used == 18
        assert r.holidays_included == base.holidays_included + 1

    def test_removing_a_holiday_changes_counts(self):
        fewer = {k: v for k, v in HOLIDAYS.items() if k != d(12, 24)}
        r = score_pto(CASE_A, fewer, years=[2027])
        assert r.total_days != 55
        assert r.holidays_included == 7

    def test_moving_a_holiday_changes_counts(self):
        moved = {k: v for k, v in HOLIDAYS.items() if k != d(7, 5)}
        moved[d(7, 2)] = "Independence Day"
        r = score_pto({d(7, 6)}, moved, years=[2027])
        base = score_pto({d(7, 6)}, HOLIDAYS, years=[2027])
        assert (r.total_days, base.total_days) != (0, 0)
        assert r.total_days != base.total_days


class TestWeekendHolidays:
    def test_holiday_on_a_weekend_costs_nothing_and_is_not_double_counted(self):
        hol = {d(7, 3): "Independence Day"}  # a Saturday
        r = score_pto({d(7, 2)}, hol, years=[2027])
        assert r.pto_used == 1
        assert r.total_days == 3  # Fri PTO + Sat + Sun
        assert r.holidays_included == 0
        assert r.weekend_days_included == 2
        assert r.weekend_holidays == [d(7, 3)]

    def test_observed_shift_to_friday_is_a_workday_holiday(self):
        hol = {d(7, 2): "Independence Day (observed)"}
        r = score_pto({d(7, 6)}, hol, years=[2027])
        assert r.holidays_included == 0  # break not adjacent
        r = score_pto({d(7, 1)}, hol, years=[2027])
        assert r.holidays_included == 1 and r.total_days == 4  # Thu PTO, Fri hol, Sat, Sun

    def test_same_date_holidays_count_once(self):
        r = score_pto({d(11, 24)}, {d(11, 25): "A / B"}, years=[2027])
        assert r.holidays_included == 1
        hol_list = [(d(11, 25), "A"), (d(11, 25), "B")]
        r2 = score_pto({d(11, 24)}, hol_list, years=[2027])
        assert r2.holidays_included == 1
        assert r2.total_days == r.total_days


class TestYears:
    def test_break_crossing_new_year_is_whole(self):
        r = score_pto({d(12, 27), d(12, 28), d(12, 29), d(12, 30)}, HOLIDAYS, years=[2027])
        last = r.breaks[-1]
        assert (last.start, last.end) == (d(12, 24), D(2028, 1, 2))
        assert last.length == 10
        assert r.days_by_year == {2027: 8, 2028: 2}

    def test_budget_is_charged_to_the_pto_date_year(self):
        pto = {d(12, 30), D(2028, 1, 3), D(2028, 1, 4)}
        hol = dict(HOLIDAYS)
        hol[D(2028, 1, 1)] = "New Year's Day"
        r = score_pto(pto, hol, years=[2027, 2028], budgets={2027: 1, 2028: 5})
        assert r.pto_by_year == {2027: 1, 2028: 2}
        rows = {row.year: row for row in r.budget_rows}
        assert (rows[2027].used, rows[2027].remaining) == (1, 0)
        assert (rows[2028].used, rows[2028].remaining) == (2, 3)

    def test_over_budget_is_flagged(self):
        r = score_pto({d(1, 4), d(2, 5)}, HOLIDAYS, years=[2027], budgets={2027: 1})
        assert r.budget_rows[0].over == 1

    def test_inside_and_adjacent_split_for_multi_year_selection(self):
        pto = {d(12, 27), d(12, 28), d(12, 29), d(12, 30)}
        r = score_pto(pto, HOLIDAYS, years=[2027, 2028])
        assert r.days_adjacent == 0 and r.days_in_years == r.total_days


class TestAnnualNonworking:
    def test_all_annual_nonworking_days_shown_separately(self):
        r = score_pto(CASE_A, HOLIDAYS, years=[2027])
        row = r.annual[2027]
        assert row.weekend_days == 104  # 2027: 52 Saturdays + 52 Sundays
        assert row.holiday_days == 8 - 0  # all 8 fall on weekdays
        assert row.total == row.weekend_days + row.holiday_days

    def test_annual_totals_do_not_depend_on_pto(self):
        a = score_pto(set(), HOLIDAYS, years=[2027]).annual[2027]
        b = score_pto(CASE_A, HOLIDAYS, years=[2027]).annual[2027]
        assert a == b

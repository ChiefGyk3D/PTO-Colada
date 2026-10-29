"""Holiday resolution: date rules and observance shifting."""

from __future__ import annotations

import datetime as dt

import pytest

from pto_colada.catalog import (
    PRESETS,
    ExplicitDate,
    FixedDate,
    NthWeekday,
    Observance,
    RelativeTo,
    nth_weekday,
    parse_rule,
    parse_workweek,
    resolve_year,
)


def observed(rules, year, **kwargs):
    return {h.name: h.observed for h in resolve_year(rules, year, **kwargs)}


def actual(rules, year, **kwargs):
    return {h.name: h.actual for h in resolve_year(rules, year, **kwargs)}


class TestNthWeekday:
    def test_fourth_thursday(self):
        assert nth_weekday(2026, 11, 3, 4) == dt.date(2026, 11, 26)

    def test_last_monday_of_may(self):
        assert nth_weekday(2026, 5, 0, -1) == dt.date(2026, 5, 25)
        assert nth_weekday(2027, 5, 0, -1) == dt.date(2027, 5, 31)

    def test_first_weekday_when_month_starts_on_it(self):
        # September 2025 starts on a Monday, so the first Monday is the 1st.
        assert nth_weekday(2025, 9, 0, 1) == dt.date(2025, 9, 1)

    def test_missing_fifth_occurrence_returns_none(self):
        assert nth_weekday(2026, 2, 0, 5) is None

    def test_zero_is_rejected(self):
        with pytest.raises(ValueError):
            nth_weekday(2026, 1, 0, 0)


class TestObservance:
    """A holiday landing on a non-working day must not silently vanish."""

    NEW_YEAR = [FixedDate("New Year's Day", 1, 1)]
    VETERANS = [FixedDate("Veterans Day", 11, 11)]

    def test_sunday_shifts_forward_to_monday(self):
        # 2023-01-01 was a Sunday; the federal rule observes it on the Monday.
        assert observed(self.NEW_YEAR, 2023)["New Year's Day"] == dt.date(2023, 1, 2)

    def test_saturday_shifts_back_to_friday(self):
        # 2023-11-11 was a Saturday.
        assert observed(self.VETERANS, 2023)["Veterans Day"] == dt.date(2023, 11, 10)

    def test_weekday_is_untouched(self):
        assert observed(self.VETERANS, 2026)["Veterans Day"] == dt.date(2026, 11, 11)

    def test_none_keeps_the_actual_date(self):
        result = observed(self.NEW_YEAR, 2023, observance=Observance.NONE)
        assert result["New Year's Day"] == dt.date(2023, 1, 1)

    def test_next_always_moves_forward(self):
        result = observed(self.VETERANS, 2023, observance=Observance.NEXT)
        assert result["Veterans Day"] == dt.date(2023, 11, 13)

    def test_previous_always_moves_back(self):
        result = observed(self.NEW_YEAR, 2023, observance=Observance.PREVIOUS)
        assert result["New Year's Day"] == dt.date(2022, 12, 30)

    def test_two_holidays_do_not_collapse_onto_one_day(self):
        # Christmas 2027 is a Saturday and Christmas Eve is the Friday, so the
        # shifted Christmas Day has to land on the Thursday instead.
        rules = [FixedDate("Christmas Eve", 12, 24), FixedDate("Christmas Day", 12, 25)]
        result = observed(rules, 2027)
        assert result["Christmas Eve"] == dt.date(2027, 12, 24)
        assert result["Christmas Day"] == dt.date(2027, 12, 23)

    def test_observance_follows_a_non_standard_workweek(self):
        # With a Sun-Thu workweek, Friday is a day off and Saturday is not.
        rules = [FixedDate("Test", 5, 1)]  # 2026-05-01 is a Friday
        result = observed(rules, 2026, workweek=parse_workweek("SUN-THU"))
        assert result["Test"] == dt.date(2026, 4, 30)


class TestRelativeTo:
    def test_day_after_thanksgiving_is_never_the_fourth_friday(self):
        """Regression: the 4th Friday of November precedes the 4th Thursday
        whenever November starts on a Friday (2024, 2030, ...)."""
        rules = PRESETS["nonstandard"]
        for year in (2024, 2030):
            dates = actual(rules, year)
            assert dates["Day After Thanksgiving"] == dates["Thanksgiving"] + dt.timedelta(days=1)

    def test_holds_for_every_year_in_a_long_range(self):
        rules = PRESETS["nonstandard"]
        for year in range(2020, 2051):
            dates = actual(rules, year)
            assert dates["Day After Thanksgiving"] == dates["Thanksgiving"] + dt.timedelta(days=1)

    def test_offset_uses_the_actual_anchor_not_the_observed_one(self):
        # Independence Day 2026 is a Saturday observed on the Friday; a "+1"
        # holiday still means July 5.
        rules = [FixedDate("Independence Day", 7, 4), RelativeTo("Day After", "Independence Day", 1)]
        dates = actual(rules, 2026)
        assert dates["Day After"] == dt.date(2026, 7, 5)

    def test_chained_relative_rules_resolve(self):
        rules = [
            NthWeekday("Thanksgiving", 11, 3, 4),
            RelativeTo("Friday After", "Thanksgiving", 1),
            RelativeTo("Saturday After", "Friday After", 1),
        ]
        dates = actual(rules, 2026)
        assert dates["Saturday After"] == dt.date(2026, 11, 28)

    def test_unknown_anchor_is_reported(self):
        with pytest.raises(ValueError, match="Nonexistent"):
            resolve_year([RelativeTo("Orphan", "Nonexistent", 1)], 2026)

    def test_cyclic_anchors_are_reported(self):
        rules = [RelativeTo("A", "B", 1), RelativeTo("B", "A", 1)]
        with pytest.raises(ValueError):
            resolve_year(rules, 2026)


class TestEdgeCases:
    def test_leap_day_is_skipped_in_non_leap_years(self):
        rules = [FixedDate("Leap Day", 2, 29)]
        assert resolve_year(rules, 2026) == []
        assert len(resolve_year(rules, 2028)) == 1

    def test_explicit_date_appears_only_in_its_own_year(self):
        rules = [ExplicitDate("Shutdown", dt.date(2026, 12, 28))]
        assert len(resolve_year(rules, 2026)) == 1
        assert resolve_year(rules, 2027) == []

    def test_results_are_sorted_by_observed_date(self):
        """Regression: the original script printed December before January."""
        resolved = resolve_year(PRESETS["federal"], 2026)
        assert [h.observed for h in resolved] == sorted(h.observed for h in resolved)

    def test_no_federal_holiday_is_ever_dropped(self):
        for year in range(2024, 2041):
            assert len(resolve_year(PRESETS["federal"], year)) == len(PRESETS["federal"])

    def test_juneteenth_is_present(self):
        assert "Juneteenth" in {r.name for r in PRESETS["federal"]}


class TestParsing:
    @pytest.mark.parametrize(
        "spec,expected",
        [
            ("Boxing Day=12-26", FixedDate("Boxing Day", 12, 26)),
            ("Founders=3rd MON in JAN", NthWeekday("Founders", 1, 0, 3)),
            ("End of May=last MON in May", NthWeekday("End of May", 5, 0, -1)),
            ("After=Thanksgiving+1", RelativeTo("After", "Thanksgiving", 1)),
            ("Before=Christmas Day-1", RelativeTo("Before", "Christmas Day", -1)),
            ("Shutdown=2026-12-28", ExplicitDate("Shutdown", dt.date(2026, 12, 28))),
        ],
    )
    def test_supported_forms(self, spec, expected):
        assert parse_rule(spec) == expected

    @pytest.mark.parametrize("spec", ["no equals sign", "=12-25", "Name=", "Name=nonsense", "Name=13-45"])
    def test_bad_specs_raise(self, spec):
        with pytest.raises(ValueError):
            parse_rule(spec)

    def test_workweek_range(self):
        assert parse_workweek("MON-FRI") == {0, 1, 2, 3, 4}

    def test_workweek_wraps_around_the_week(self):
        assert parse_workweek("SUN-THU") == {6, 0, 1, 2, 3}

    def test_workweek_explicit_list(self):
        assert parse_workweek("MON,WED,FRI") == {0, 2, 4}

    def test_observance_aliases(self):
        assert Observance.parse("federal") is Observance.NEAREST
        assert Observance.parse("monday") is Observance.NEXT
        with pytest.raises(ValueError):
            Observance.parse("whenever")

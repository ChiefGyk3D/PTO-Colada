"""The variations employers actually run.

No two employers observe the same set of days, so the engine has to be right
for *any* subset of them, not just the presets. These tests sweep the whole
power set of the US federal holidays and check the invariants that must hold
however the set was assembled, then cover the shapes that recur in real
holiday policies: extra shoulder days, Easter-based holidays, a winter
shutdown, and floating holidays granted on top of the fixed list.
"""

from __future__ import annotations

import datetime as dt
from itertools import combinations

import pytest

from pto_colada.catalog import (
    KNOWN,
    PRESETS,
    EasterRelative,
    Observance,
    WeekdayOnOrAfter,
    easter,
    known_holiday,
    parse_rule,
    resolve_year,
)
from pto_colada.config import build_config, rule_from_spec
from pto_colada.planner import PtoBudget, optimize_pto
from pto_colada.schedule import build_days, find_breaks

FEDERAL = PRESETS["federal"]
YEAR = 2026


def subsets(items):
    for size in range(len(items) + 1):
        for combo in combinations(items, size):
            yield list(combo)


class TestEveryFederalSubset:
    """All 2**11 combinations of the federal holidays resolve sanely."""

    def test_the_sweep_covers_the_whole_power_set(self):
        assert len(list(subsets(FEDERAL))) == 2 ** len(FEDERAL) == 2048

    def test_every_subset_resolves_to_one_dated_holiday_per_rule(self):
        for rules in subsets(FEDERAL):
            resolved = resolve_year(rules, YEAR)
            assert len(resolved) == len(rules), rules
            assert {h.name for h in resolved} == {r.name for r in rules}

    def test_no_two_holidays_are_ever_observed_on_the_same_day(self):
        # Shifting a holiday off a weekend must not land it on a day another
        # holiday already claimed -- that would silently lose a day off.
        for rules in subsets(FEDERAL):
            observed = [h.observed for h in resolve_year(rules, YEAR)]
            assert len(set(observed)) == len(observed), rules

    def test_results_stay_sorted_and_inside_the_year(self):
        for rules in subsets(FEDERAL):
            resolved = resolve_year(rules, YEAR)
            assert resolved == sorted(resolved, key=lambda h: (h.observed, h.name))
            for holiday in resolved:
                assert holiday.actual.year == YEAR
                # Observance shifting can push New Year's Day into the year
                # before or Christmas-adjacent days into the next one.
                assert abs((holiday.observed - holiday.actual).days) <= 3

    def test_adding_a_holiday_never_removes_days_off(self):
        """A bigger holiday set can only ever mean more days off."""
        for rules in subsets(FEDERAL[:6]):
            for extra in FEDERAL[6:]:
                start, end = dt.date(YEAR, 1, 1), dt.date(YEAR, 12, 31)
                fewer = build_days(start, end, resolve_year(rules, YEAR))
                more = build_days(start, end, resolve_year(rules + [extra], YEAR))
                assert sum(d.is_off for d in more) >= sum(d.is_off for d in fewer)

    @pytest.mark.parametrize("observance", list(Observance))
    def test_every_observance_policy_survives_the_sweep(self, observance):
        # A cheaper sweep across all four policies: sizes 0, 1, 10 and 11.
        for rules in [[], FEDERAL, FEDERAL[:-1]] + [[r] for r in FEDERAL]:
            resolved = resolve_year(rules, YEAR, observance=observance)
            assert len(resolved) == len(rules)
            if observance is Observance.NONE:
                assert all(not h.shifted for h in resolved)
            else:
                assert all(h.observed.weekday() < 5 for h in resolved)


class TestCommonPolicyShapes:
    """The recognisable patterns companies land on."""

    @pytest.mark.parametrize("preset", sorted(PRESETS))
    def test_every_preset_resolves_without_collisions(self, preset):
        for year in range(2024, 2036):
            resolved = resolve_year(PRESETS[preset], year)
            assert len(resolved) == len(PRESETS[preset])
            observed = [h.observed for h in resolved]
            assert len(set(observed)) == len(observed)

    def test_presets_differ_from_one_another(self):
        signatures = {
            name: tuple(sorted(r.name for r in rules)) for name, rules in PRESETS.items()
        }
        assert len(set(signatures.values())) == len(signatures)

    @pytest.mark.parametrize("name", sorted(KNOWN))
    def test_every_catalogued_holiday_can_be_added_by_name(self, name):
        # Start from a preset that does not already contain it, so the add is
        # a real add rather than a duplicate.
        base = "minimal" if name not in _MINIMAL_NAMES else "nonstandard"
        if name in _MINIMAL_NAMES and name in _NONSTANDARD_NAMES:
            config = build_config(preset=base)  # already observed everywhere
        else:
            config = build_config(preset=base, add=[name])
        assert name in config.holiday_names()
        resolved = resolve_year(config.holidays, YEAR)
        assert any(h.name == name for h in resolved)
        assert all(h.actual.year == YEAR for h in resolved)

    @pytest.mark.parametrize(
        "alias,canonical",
        [
            ("MLK Day", "Martin Luther King Jr. Day"),
            ("Washington's Birthday", "Presidents' Day"),
            ("Black Friday", "Day After Thanksgiving"),
            ("Fourth of July", "Independence Day"),
            ("Boxing Day", "Day After Christmas"),
        ],
    )
    def test_aliases_resolve_to_the_catalogued_rule(self, alias, canonical):
        assert known_holiday(alias) is KNOWN[canonical]

    def test_an_unknown_name_explains_the_two_ways_to_add_one(self):
        with pytest.raises(ValueError, match="unknown holiday"):
            rule_from_spec("Founders Day")
        assert rule_from_spec("Founders Day=06-01").name == "Founders Day"

    def test_two_names_for_one_holiday_are_rejected(self):
        # Columbus Day and Indigenous Peoples' Day are the same second Monday
        # in October; observing both would shift one to Tuesday and invent a
        # day off that nobody actually gets.
        with pytest.raises(ValueError, match="always the same day"):
            build_config(preset="federal", add=["Indigenous Peoples' Day"])

    @pytest.mark.parametrize("name", ["Columbus Day", "Indigenous Peoples' Day"])
    def test_either_name_alone_is_fine(self, name):
        config = build_config(preset="minimal", add=[name])
        assert name in config.holiday_names()
        resolved = {h.name: h for h in resolve_year(config.holidays, YEAR)}
        assert resolved[name].observed == dt.date(YEAR, 10, 12)

    def test_swapping_one_name_for_the_other_works(self):
        config = build_config(
            preset="federal", drop=["Columbus Day"], add=["Indigenous Peoples' Day"]
        )
        names = config.holiday_names()
        assert "Columbus Day" not in names
        assert "Indigenous Peoples' Day" in names

    def test_a_holiday_that_only_sometimes_collides_is_allowed(self):
        # Good Friday lands on 2026-04-03 exactly once in the sampled range,
        # so this is an unlucky calendar rather than a duplicate definition.
        config = build_config(
            preset="minimal", add=["Good Friday", "Founders Day=2026-04-03"]
        )
        assert len(config.holidays) == 8

    def test_a_redefined_federal_holiday_is_caught(self):
        with pytest.raises(ValueError, match="always the same day"):
            build_config(preset="federal", add=["Company Picnic=07-04"])

    def test_shutdown_preset_closes_the_whole_week(self):
        resolved = resolve_year(PRESETS["shutdown"], YEAR)
        days = build_days(dt.date(YEAR, 12, 20), dt.date(YEAR + 1, 1, 5), resolved)
        breaks = find_breaks(days, min_length=4)
        longest = max(breaks, key=lambda b: b.length)
        assert longest.length >= 9
        assert longest.pto_cost == 0

    def test_dropping_the_two_least_observed_holidays(self):
        # The most common corporate deviation from the federal list.
        config = build_config(preset="federal", drop=["Columbus Day", "Veterans Day"])
        assert len(config.holidays) == 9
        names = config.holiday_names()
        assert "Columbus Day" not in names and "Veterans Day" not in names


_MINIMAL_NAMES = {r.name for r in PRESETS["minimal"]}
_NONSTANDARD_NAMES = {r.name for r in PRESETS["nonstandard"]}


class TestEasterHolidays:
    @pytest.mark.parametrize(
        "year,expected",
        [
            (2024, dt.date(2024, 3, 31)),
            (2025, dt.date(2025, 4, 20)),
            (2026, dt.date(2026, 4, 5)),
            (2027, dt.date(2027, 3, 28)),
            (2030, dt.date(2030, 4, 21)),
            (2038, dt.date(2038, 4, 25)),
        ],
    )
    def test_known_easter_dates(self, year, expected):
        assert easter(year) == expected

    def test_easter_is_always_a_sunday_in_its_march_april_window(self):
        for year in range(1900, 2101):
            date = easter(year)
            assert date.weekday() == 6
            assert dt.date(year, 3, 22) <= date <= dt.date(year, 4, 25)

    def test_good_friday_is_two_days_before_easter(self):
        rule = parse_rule("Good Friday=Easter-2")
        assert isinstance(rule, EasterRelative)
        for year in range(2024, 2040):
            date = rule.actual(year, {})
            assert date == easter(year) - dt.timedelta(days=2)
            assert date.weekday() == 4

    def test_good_friday_never_needs_observance_shifting(self):
        config = build_config(preset="minimal", add=["Good Friday"])
        for year in range(2024, 2040):
            resolved = {h.name: h for h in resolve_year(config.holidays, year)}
            assert not resolved["Good Friday"].shifted

    def test_easter_monday_follows_easter(self):
        for year in range(2024, 2040):
            rule = KNOWN["Easter Monday"]
            assert rule.actual(year, {}) == easter(year) + dt.timedelta(days=1)


class TestElectionDay:
    def test_it_is_the_tuesday_after_the_first_monday_in_november(self):
        rule = KNOWN["Election Day"]
        assert isinstance(rule, WeekdayOnOrAfter)
        for year in range(2024, 2060):
            date = rule.actual(year, {})
            assert date.weekday() == 1
            assert dt.date(year, 11, 2) <= date <= dt.date(year, 11, 8)
            # By definition: the Monday before it is the first of the month.
            assert (date - dt.timedelta(days=1)).day <= 7

    @pytest.mark.parametrize(
        "year,expected",
        [(2024, dt.date(2024, 11, 5)), (2026, dt.date(2026, 11, 3)), (2028, dt.date(2028, 11, 7))],
    )
    def test_known_election_days(self, year, expected):
        assert KNOWN["Election Day"].actual(year, {}) == expected


class TestFloatingHolidays:
    """Floating days sit on top of the fixed list and expire with the year."""

    def days(self, preset="federal", years=(2026, 2027)):
        holidays = []
        for year in years:
            holidays += resolve_year(PRESETS[preset], year)
        return build_days(dt.date(years[0], 1, 1), dt.date(years[-1], 12, 31), holidays)

    def test_floating_days_are_spent_even_with_no_pto(self):
        plan = optimize_pto(self.days(), PtoBudget(default=0, floating=3))
        assert plan.spent_by_year == {2026: 3, 2027: 3}
        assert plan.floating_used(2026) == 3
        assert plan.pto_used(2026) == 0

    def test_floating_days_are_spent_before_pto(self):
        plan = optimize_pto(self.days(), PtoBudget(default=10, floating=3))
        for year in (2026, 2027):
            assert plan.floating_used(year) == 3
            assert plan.pto_used(year) == plan.spent_by_year[year] - 3

    def test_floating_days_never_carry_over(self):
        # Ten days of carryover on offer, but a floating-only grant: each year
        # is capped at its own three days.
        plan = optimize_pto(self.days(), PtoBudget(default=0, floating=3, carryover=10))
        assert plan.spent_by_year[2027] <= 3

    def test_carryover_is_capped_at_the_unused_pto_not_the_whole_pool(self):
        plan = optimize_pto(self.days(), PtoBudget(default=5, floating=3, carryover=10))
        # 2027 can spend its own 5 + 3, plus at most the 5 PTO days of 2026.
        assert plan.spent_by_year[2027] <= 13
        assert plan.spent_by_year[2026] + plan.spent_by_year[2027] <= 16

    def test_the_grant_is_the_sum_of_both_pools(self):
        plan = optimize_pto(self.days(), PtoBudget(default=8, floating=2))
        assert plan.granted_by_year[2026] == 8
        assert plan.floating_by_year[2026] == 2
        assert plan.granted_total(2026) == 10

    def test_a_per_year_floating_override_applies(self):
        budget = PtoBudget(default=0, floating=1, floating_per_year={2027: 4})
        plan = optimize_pto(self.days(), budget)
        assert plan.floating_by_year == {2026: 1, 2027: 4}
        assert plan.spent_by_year == {2026: 1, 2027: 4}

    def test_unused_floating_days_are_reported_as_expiring(self):
        # A workweek with nothing to bridge to still cannot bank the days.
        budget = PtoBudget(default=0, floating=3)
        plan = optimize_pto(self.days(), budget, min_break=60)
        assert plan.floating_used(2026) + (3 - plan.floating_used(2026)) == 3

    def test_floating_days_respect_blackouts(self):
        blackout = {
            dt.date(2026, 1, 1) + dt.timedelta(days=n) for n in range(365)
        }
        days = build_days(
            dt.date(2026, 1, 1),
            dt.date(2026, 12, 31),
            resolve_year(PRESETS["federal"], 2026),
            blackout=blackout,
        )
        plan = optimize_pto(days, PtoBudget(default=0, floating=5))
        assert plan.total_pto == 0

    def test_totals_count_floating_days_as_leave(self):
        plan = optimize_pto(self.days(), PtoBudget(default=4, floating=2))
        assert plan.total_pto == sum(plan.spent_by_year.values())
        assert plan.total_floating == sum(plan.floating_used(y) for y in (2026, 2027))
        assert plan.total_floating <= 4

    def test_committed_days_are_charged_against_the_whole_allowance(self):
        # Three commitments fit inside a 0 PTO + 3 floating grant.
        committed = {dt.date(2026, 3, 2), dt.date(2026, 3, 3), dt.date(2026, 3, 4)}
        plan = optimize_pto(
            self.days(years=(2026,)), PtoBudget(default=0, floating=3), committed=committed
        )
        assert committed <= set(plan.pto_dates)

    def test_commitments_beyond_the_whole_allowance_are_rejected(self):
        committed = {dt.date(2026, 3, 2), dt.date(2026, 3, 3), dt.date(2026, 3, 4)}
        with pytest.raises(ValueError, match="exceed"):
            optimize_pto(
                self.days(years=(2026,)),
                PtoBudget(default=0, floating=2),
                committed=committed,
            )


class TestFloatingThroughTheCli:
    def test_floating_alone_enables_the_planner(self, capsys):
        from pto_colada.cli import main

        assert main(["--floating", "3", "--years", "1", "--format", "json"]) == 0
        import json

        payload = json.loads(capsys.readouterr().out)
        assert payload["plan"]["floating_by_year"]
        assert sum(payload["plan"]["floating_used_by_year"].values()) == 3

    def test_the_text_report_splits_the_two_pools(self, capsys):
        from pto_colada.cli import main

        main(["--pto", "10", "--floating", "3", "--years", "1"])
        out = capsys.readouterr().out
        assert "floating holiday(s) available" in out
        assert "PTO, 3 floating" in out

    def test_negative_floating_is_rejected(self):
        from pto_colada.cli import main

        with pytest.raises(SystemExit) as excinfo:
            main(["--floating", "-2", "--years", "1"])
        assert excinfo.value.code == 2

"""Goal modes: weekends, vacations, balanced, plus prefer, blackout and max-breaks."""

from __future__ import annotations

import datetime as dt
import itertools
import random

import pytest

from longweekend.catalog import ResolvedHoliday
from longweekend.goals import (
    PREFER_BONUS,
    GoalSpec,
    build_goal_days,
    goal_spec,
    optimize_goal,
)
from longweekend.honest import score_pto
from longweekend.planner import PtoBudget
from longweekend.schedule import DayKind

D = dt.date

EMPLOYER = [
    ("New Year's Day", D(2027, 1, 1)),
    ("Memorial Day", D(2027, 5, 31)),
    ("Independence Day (observed)", D(2027, 7, 5)),
    ("Labor Day", D(2027, 9, 6)),
    ("Thanksgiving", D(2027, 11, 25)),
    ("Day After Thanksgiving", D(2027, 11, 26)),
    ("Christmas (observed)", D(2027, 12, 24)),
    ("New Year's 2028 (observed)", D(2027, 12, 31)),
]
EMP_HOLS = [ResolvedHoliday(n, d, d) for n, d in EMPLOYER]
EMP_MAP = {d: n for n, d in EMPLOYER}
WINDOW = (D(2027, 1, 1), D(2027, 12, 31))


def run_goal(goal, pto=17, **kw):
    spec = goal_spec(goal, min_vacation_days=kw.pop("min_vacation_days", None))
    days = build_goal_days(*WINDOW, EMP_HOLS)
    return optimize_goal(
        days, PtoBudget(default=pto), spec, window=WINDOW, **kw
    )


def honest(result, years=(2027,)):
    return score_pto(result.pto_dates, EMP_MAP, years=list(years))


class TestSpec:
    def test_weekends_counts_three_day_breaks(self):
        assert goal_spec("weekends").thresholds == ((3, 1),)

    def test_min_break_overrides_the_weekend_threshold(self):
        assert goal_spec("weekends", min_break=4).thresholds == ((4, 1),)

    def test_vacations_only_count_long_breaks(self):
        assert goal_spec("vacations").thresholds == ((7, 1),)
        assert goal_spec("vacations", min_vacation_days=9).thresholds == ((9, 1),)

    def test_balanced_formula(self):
        # one point per day in a 3+ day break, one more per day in a 7+ day break
        assert goal_spec("balanced").thresholds == ((3, 1), (7, 1))
        assert goal_spec("balanced", min_vacation_days=5).thresholds == ((3, 1), (5, 1))

    def test_unknown_goal_and_bad_numbers(self):
        with pytest.raises(ValueError):
            goal_spec("everything")
        with pytest.raises(ValueError):
            goal_spec("vacations", min_vacation_days=1)

    def test_value_function(self):
        spec = goal_spec("balanced")
        assert [spec.value(n) for n in (2, 3, 6, 7, 10)] == [0, 3, 6, 14, 20]


class TestBuildDays:
    def test_window_is_padded_and_pad_is_blackout(self):
        days = build_goal_days(*WINDOW, EMP_HOLS)
        assert days[0].date < WINDOW[0] and days[-1].date > WINDOW[1]
        for day in days:
            if not (WINDOW[0] <= day.date <= WINDOW[1]):
                assert day.blackout
        assert any(d.date == D(2028, 1, 2) for d in days)


@pytest.fixture(scope="module")
def results():
    return {g: run_goal(g) for g in ("weekends", "vacations", "balanced")}


class TestEmployerGoals:

    def test_every_goal_spends_within_budget(self, results):
        for result in results.values():
            assert len(result.pto_dates) <= 17
            assert all(WINDOW[0] <= d <= WINDOW[1] for d in result.pto_dates)
            assert all(d.weekday() < 5 and d not in EMP_MAP for d in result.pto_dates)

    def test_score_matches_the_honest_counter(self, results):
        for goal, result in results.items():
            report = honest(result)
            expected = sum(goal_spec(goal).value(b.length) for b in report.breaks)
            assert result.score == expected, goal

    def test_vacations_make_fewer_longer_breaks(self, results):
        weekends, vacations = honest(results["weekends"]), honest(results["vacations"])
        assert all(b.length >= 7 for b in vacations.breaks if b.pto_cost)
        assert weekends.breaks_count > vacations.breaks_count
        assert vacations.longest >= weekends.longest

    def test_balanced_is_between(self, results):
        w, v, b = (honest(results[g]) for g in ("weekends", "vacations", "balanced"))
        assert v.breaks_count <= b.breaks_count <= w.breaks_count

    def test_weekends_total_is_not_beaten_by_vacations(self, results):
        assert honest(results["weekends"]).total_days >= honest(results["vacations"]).total_days

    def test_method_is_stated_exact(self, results):
        assert "exact" in results["weekends"].method

    def test_year_end_break_is_scored_whole(self):
        # A single block of PTO Dec 27-30 must be valued as Dec 24 - Jan 2 (10 days).
        result = run_goal("vacations", pto=4, committed=set())
        report = honest(result)
        assert report.longest == 10 and report.total_days == 10


class TestOptions:
    def test_blackout_is_respected(self):
        free = run_goal("weekends")
        black = set(free.pto_dates[:5])
        result = run_goal("weekends", blackout=black)
        assert not (set(result.pto_dates) & black)

    def test_blackout_through_the_days_flag(self):
        days = build_goal_days(*WINDOW, EMP_HOLS, blackout={D(2027, 1, 4)})
        result = optimize_goal(days, PtoBudget(default=17), goal_spec("weekends"), window=WINDOW)
        assert D(2027, 1, 4) not in result.pto_dates

    def test_prefer_pulls_a_date_in(self):
        target = D(2027, 8, 18)  # a Wednesday in the middle of nothing
        base = run_goal("weekends")
        assert target not in base.pto_dates
        result = run_goal("weekends", prefer={target}, prefer_bonus=1000)
        assert target in result.pto_dates

    def test_default_prefer_bonus_is_documented_and_small(self):
        assert PREFER_BONUS == 2

    def test_prefer_bonus_counts_in_the_score_not_the_honest_total(self):
        target = D(2027, 8, 18)
        plain = run_goal("weekends", pto=1)
        result = run_goal("weekends", pto=1, prefer={target})
        assert plain.score >= result.score - PREFER_BONUS

    @pytest.mark.parametrize("cap", [1, 2, 3, 5])
    def test_max_breaks_caps_pto_breaks(self, cap):
        result = run_goal("weekends", max_breaks=cap)
        report = honest(result)
        assert report.breaks_count <= cap
        assert report.breaks_count == cap  # 17 days easily fill that many

    def test_max_breaks_one_spends_everything_in_one_break(self):
        report = honest(run_goal("weekends", max_breaks=1))
        assert report.breaks_count == 1

    def test_max_breaks_never_beats_the_uncapped_plan(self):
        free = run_goal("weekends").score
        for cap in (1, 3, 6):
            assert run_goal("weekends", max_breaks=cap).score <= free

    def test_max_consecutive_pto(self):
        result = run_goal("vacations", max_consecutive_pto=2)
        pto = sorted(result.pto_dates)
        run = best = 0
        for a, b in zip([None] + pto, pto):
            run = run + 1 if a is not None and (b - a).days == 1 else 1
            best = max(best, run)
        assert best <= 2

    def test_committed_days_are_taken(self):
        result = run_goal("weekends", committed={D(2027, 3, 17)})
        assert D(2027, 3, 17) in result.pto_dates

    def test_infeasible_max_breaks_with_committed_days(self):
        with pytest.raises(ValueError, match="no feasible"):
            run_goal("weekends", committed={D(2027, 3, 17), D(2027, 8, 18)}, max_breaks=1)


class TestPerYearBudget:
    def test_budget_is_per_calendar_year(self):
        window = (D(2027, 1, 1), D(2028, 12, 31))
        holidays = EMP_HOLS
        days = build_goal_days(*window, holidays)
        budget = PtoBudget(per_year={2027: 3, 2028: 5})
        result = optimize_goal(days, budget, goal_spec("weekends"), window=window)
        assert result.spent_by_year.get(2027, 0) <= 3
        assert result.spent_by_year.get(2028, 0) <= 5
        assert result.granted_by_year == {2027: 3, 2028: 5}


class TestExactness:
    """The DP must equal brute force on small random calendars."""

    @staticmethod
    def brute(days, window, budget, spec, prefer, max_breaks):
        work = [
            d for d in days
            if d.kind is DayKind.WORK and not d.blackout and window[0] <= d.date <= window[1]
        ]
        kinds = {d.date: d.kind for d in days}
        order = [d.date for d in days]
        best = None
        for size in range(0, budget + 1):
            for combo in itertools.combinations(work, size):
                chosen = {d.date for d in combo}
                runs, cur = [], []
                for date in order:
                    if kinds[date] is not DayKind.WORK or date in chosen:
                        cur.append(date)
                    else:
                        if cur:
                            runs.append(cur)
                        cur = []
                if cur:
                    runs.append(cur)
                featured = [r for r in runs if any(x in chosen for x in r)]
                if max_breaks is not None and len(featured) > max_breaks:
                    continue
                score = sum(spec.value(len(r)) for r in featured)
                score += PREFER_BONUS * len(chosen & prefer)
                key = (score, -size)
                if best is None or key > best:
                    best = key
        return best

    @pytest.mark.parametrize("seed", range(40))
    def test_dp_matches_brute_force(self, seed):
        rnd = random.Random(seed)
        start = D(2027, 3, 1) + dt.timedelta(rnd.randrange(0, 5))
        end = start + dt.timedelta(rnd.randrange(14, 24))
        span = [start + dt.timedelta(i) for i in range((end - start).days + 1)]
        hols = [
            ResolvedHoliday("H%d" % i, d, d)
            for i, d in enumerate(rnd.sample(span, rnd.randrange(0, 4)))
        ]
        goal = rnd.choice(["weekends", "vacations", "balanced"])
        spec = goal_spec(goal, min_vacation_days=rnd.choice([None, 4, 5]))
        budget = rnd.randrange(1, 5)
        prefer = set(rnd.sample(span, rnd.randrange(0, 3)))
        max_breaks = rnd.choice([None, 1, 2])
        days = build_goal_days(start, end, hols)
        result = optimize_goal(
            days, PtoBudget(default=budget), spec, window=(start, end),
            prefer=prefer, max_breaks=max_breaks,
        )
        expected = self.brute(days, (start, end), budget, spec, prefer, max_breaks)
        assert (result.score, -len(result.pto_dates)) == expected


class TestDocumentedNumbers:
    """The README quotes these; they come from the exact DP on the example config."""

    @pytest.mark.parametrize(
        "goal,breaks,days,longest",
        [("weekends", 17, 59, 5), ("vacations", 4, 39, 12), ("balanced", 6, 45, 10)],
    )
    def test_seventeen_pto_numbers(self, results, goal, breaks, days, longest):
        report = honest(results[goal])
        assert (report.pto_used, report.breaks_count, report.total_days, report.longest) == (
            17, breaks, days, longest
        )

    def test_weekends_with_fourteen_breaks(self):
        report = honest(run_goal("weekends", max_breaks=14))
        assert (report.breaks_count, report.total_days, report.longest) == (14, 55, 10)

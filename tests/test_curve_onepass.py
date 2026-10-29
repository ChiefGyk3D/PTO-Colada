"""The one-pass return curve against the per-budget planner it replaces.

``ORACLE`` below is the original definition of the curve: replan the exact
dynamic program once for every budget from 0 to the ceiling. The one-pass
computation must reproduce it exactly, point for point, and the plans the curve
hands out must be the ones that replanning produces.
"""

from __future__ import annotations

import datetime as dt
import random

import pytest

import pto_colada.efficiency as efficiency_module
from pto_colada.catalog import PRESETS, resolve_year
from pto_colada.efficiency import capped_budget, return_curve
from pto_colada.planner import PtoBudget, optimize_pto
from pto_colada.schedule import DayKind, build_days


def oracle(days, budget, min_break, max_consecutive_pto, committed, bonus, max_budget):
    """Plan every budget from scratch (no skipping); rows of (budget, spent, in_breaks, marginal)."""
    years = sorted({d.date.year for d in days})
    ceiling = max_budget if max_budget is not None else max(
        (budget.for_year(y) for y in years), default=budget.default
    )
    rows = []
    for cap in range(ceiling + 1):
        try:
            plan = optimize_pto(
                days, capped_budget(budget, cap, years), min_break=min_break,
                max_consecutive_pto=max_consecutive_pto, committed=committed,
                long_break_bonus=bonus,
            )
        except ValueError:
            if cap == ceiling:
                raise
            continue
        marginal = None
        if rows:
            booked = plan.total_pto - rows[-1][1]
            marginal = (plan.days_in_breaks - rows[-1][2]) / booked if booked > 0 else 0.0
        rows.append((cap, plan.total_pto, plan.days_in_breaks, marginal))
    return rows


def random_config(rng, short):
    preset = rng.choice(sorted(PRESETS))
    if short:
        start = dt.date(2026, 11, 1) + dt.timedelta(days=rng.randrange(0, 90))
        end = start + dt.timedelta(days=rng.randrange(12, 75))
    else:
        first = rng.choice([2026, 2027])
        start = dt.date(first, 1, 1)
        end = dt.date(first + rng.choice([0, 0, 1]), 12, 31)
    holidays = []
    for year in range(start.year - 1, end.year + 2):
        holidays.extend(resolve_year(PRESETS[preset], year))
    workweek = rng.choice([None, None, {0, 1, 2, 3}, {0, 1, 2, 3, 4, 5}])
    blackout = {
        start + dt.timedelta(days=rng.randrange((end - start).days + 1))
        for _ in range(rng.choice([0, 0, 3, 10]))
    }
    days = build_days(start, end, holidays, workweek=workweek, blackout=blackout)
    years = sorted({d.date.year for d in days})
    top = 9 if short else 8
    budget = PtoBudget(
        per_year={y: rng.randrange(0, top + 1) for y in years},
        default=rng.randrange(0, top + 1),
        carryover=rng.choice([0, 0, 1, 3, 5]),
        floating_per_year={y: rng.randrange(0, 3) for y in years} if rng.random() < 0.3 else {},
        floating=rng.choice([0, 0, 1, 2]),
    )
    work = [d.date for d in days if d.kind is DayKind.WORK]
    committed = set(rng.sample(work, min(len(work), rng.choice([0, 0, 0, 1, 2])))) or None
    return dict(
        days=days,
        budget=budget,
        min_break=rng.choice([3, 4, 4, 5]),
        max_consecutive_pto=rng.choice([None, None, None, 2, 3, 5]),
        committed=committed,
        bonus=rng.choice([0, 0, 0, 0, 1]),
        max_budget=rng.choice([None, None, None, 4, 14]),
    )


def compare(config):
    days = config["days"]
    kwargs = dict(
        min_break=config["min_break"], max_consecutive_pto=config["max_consecutive_pto"],
        committed=config["committed"], max_budget=config["max_budget"],
    )
    try:
        expected = oracle(
            days, config["budget"], config["min_break"], config["max_consecutive_pto"],
            config["committed"], config["bonus"], config["max_budget"],
        )
    except ValueError as error:
        with pytest.raises(ValueError) as caught:
            return_curve(days, config["budget"], long_break_bonus=config["bonus"], **kwargs)
        assert str(caught.value) == str(error)
        return None
    curve = return_curve(days, config["budget"], long_break_bonus=config["bonus"], **kwargs)
    got = [(p.budget, p.pto_spent, p.days_in_breaks, p.marginal) for p in curve.points]
    assert got == expected
    assert list(curve.plans) == [row[0] for row in expected]
    years = sorted({d.date.year for d in days})
    for budget_value in sorted({row[0] for row in expected})[::3]:
        fresh = optimize_pto(
            days, capped_budget(config["budget"], budget_value, years),
            min_break=config["min_break"], max_consecutive_pto=config["max_consecutive_pto"],
            committed=config["committed"], long_break_bonus=config["bonus"],
        )
        plan = curve.plans[budget_value]
        assert plan.pto_dates == fresh.pto_dates
        assert plan.days_in_breaks == fresh.days_in_breaks
        assert plan.granted_by_year == fresh.granted_by_year
        assert plan.spent_by_year == fresh.spent_by_year
    return curve


@pytest.mark.parametrize("seed", range(60))
def test_full_year_curves_match_replanning(seed):
    compare(random_config(random.Random(1000 + seed), short=False))


@pytest.mark.parametrize("seed", range(40))
def test_short_window_curves_match_replanning(seed):
    compare(random_config(random.Random(5000 + seed), short=True))


def test_the_suite_exercises_what_it_claims():
    """Guard: the random configs really include carryover, uneven grants and a bonus."""
    configs = [random_config(random.Random(1000 + s), short=False) for s in range(60)]
    configs += [random_config(random.Random(5000 + s), short=True) for s in range(40)]
    assert len(configs) == 100
    assert any(c["budget"].carryover > 0 and len(set(c["budget"].per_year.values())) > 1 for c in configs)
    assert any(c["budget"].floating or c["budget"].floating_per_year for c in configs)
    assert any(c["committed"] for c in configs)
    assert any(c["max_consecutive_pto"] for c in configs)
    assert any(c["bonus"] > 0 for c in configs)
    assert any(len({d.date.year for d in c["days"]}) > 1 for c in configs)


def test_a_wrong_curve_is_caught():
    """Negative case: the comparison bites when one point is off."""
    config = random_config(random.Random(7), short=False)
    config["max_budget"] = 8
    config["bonus"] = 0
    curve = compare(config)
    assert curve is not None
    curve.points[3].days_in_breaks += 1
    expected = oracle(
        config["days"], config["budget"], config["min_break"], config["max_consecutive_pto"],
        config["committed"], 0, 8,
    )
    got = [(p.budget, p.pto_spent, p.days_in_breaks, p.marginal) for p in curve.points]
    assert got != expected


def test_the_curve_is_not_built_by_replanning_every_budget(monkeypatch):
    """The points come from one pass: no per-budget planner run to build them."""
    holidays = []
    for year in (2026, 2027, 2028):
        holidays.extend(resolve_year(PRESETS["federal"], year))
    days = build_days(dt.date(2027, 1, 1), dt.date(2027, 12, 31), holidays)
    calls = []
    real = efficiency_module.optimize_pto
    monkeypatch.setattr(
        efficiency_module, "optimize_pto", lambda *a, **k: calls.append(1) or real(*a, **k)
    )
    curve = return_curve(days, PtoBudget(default=30), min_break=4)
    assert len(curve.points) == 31
    assert len(calls) == 0
    plan = curve.plans[12]  # a plan is still available on demand
    assert len(calls) == 1
    assert plan.granted_by_year == {2027: 12}

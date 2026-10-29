"""The return curve honors --max-work-streak (issue #26).

A curve built without the limit describes a different plan than the one
exported. Every point must equal what ``optimize_pto`` returns for that budget
under the same limit, and the point at the plan's budget must equal the plan.
"""

from __future__ import annotations

import datetime as dt
import json
import random

import pytest

from pto_colada.catalog import PRESETS, resolve_year
from pto_colada.cli import main
from pto_colada.efficiency import capped_budget, return_curve
from pto_colada.planner import PtoBudget, optimize_pto
from pto_colada.schedule import build_days
from test_curve_onepass import random_config

HORIZON = ["--today", "2026-11-02", "--pto-hours", "400", "--refill-hours", "120",
           "--through", "2027-03-31"]


def federal_year_days(year=2027):
    holidays = []
    for y in (year - 1, year, year + 1):
        holidays.extend(resolve_year(PRESETS["federal"], y))
    return build_days(dt.date(year, 1, 1), dt.date(year, 6, 30), holidays)


def streak_oracle(days, budget, kwargs, streak):
    """Replan every budget with the limit; rows of (budget, spent, in_breaks)."""
    years = sorted({d.date.year for d in days})
    ceiling = max(budget.for_year(y) for y in years)
    rows = []
    for cap in range(ceiling + 1):
        try:
            plan = optimize_pto(days, capped_budget(budget, cap, years),
                                max_work_streak=streak, **kwargs)
        except ValueError:
            if cap == ceiling:
                raise
            continue
        rows.append((cap, plan.total_pto, plan.days_in_breaks))
    return rows


def test_curve_point_at_the_plans_budget_equals_the_plan_under_a_binding_limit():
    days = federal_year_days()
    budget = PtoBudget(default=14)
    free = optimize_pto(days, budget, min_break=4)
    plan = optimize_pto(days, budget, min_break=4, max_work_streak=10)
    # The limit must actually change the answer, or this test proves nothing.
    assert (plan.days_in_breaks, plan.total_pto) != (free.days_in_breaks, free.total_pto)
    curve = return_curve(days, budget, min_break=4, max_work_streak=10)
    last = curve.points[-1]
    assert (last.budget, last.days_in_breaks, last.pto_spent) == (
        14, plan.days_in_breaks, plan.total_pto)
    # ... and the curve without the limit would have said otherwise.
    unlimited = return_curve(days, budget, min_break=4).points[-1]
    assert (unlimited.days_in_breaks, unlimited.pto_spent) != (last.days_in_breaks, last.pto_spent)
    assert curve.plans[14].pto_dates == plan.pto_dates


def test_budgets_that_cannot_meet_the_limit_are_left_off_the_curve():
    days = federal_year_days()
    curve = return_curve(days, PtoBudget(default=14), min_break=4, max_work_streak=10)
    budgets = [p.budget for p in curve.points]
    assert budgets[0] > 0 and budgets[-1] == 14
    with pytest.raises(ValueError):
        optimize_pto(days, capped_budget(PtoBudget(default=14), budgets[0] - 1, [2027]),
                     min_break=4, max_work_streak=10)
    assert list(curve.plans) == budgets


def test_a_wholly_infeasible_curve_is_a_clear_error():
    with pytest.raises(ValueError, match="--max-work-streak 5 cannot be met"):
        return_curve(federal_year_days(), PtoBudget(default=0), max_work_streak=5)


def test_the_limit_is_validated_like_the_planner():
    with pytest.raises(ValueError, match="at least 1"):
        return_curve(federal_year_days(), PtoBudget(default=5), max_work_streak=0)


def random_streak_config(seed):
    config = random_config(random.Random(9000 + seed), short=True)
    config["bonus"] = 0
    config["max_budget"] = None
    return config, random.Random(seed).choice([3, 5, 8, 12])


def run_differential(seed):
    config, streak = random_streak_config(seed)
    kwargs = dict(min_break=config["min_break"],
                  max_consecutive_pto=config["max_consecutive_pto"],
                  committed=config["committed"])
    days = config["days"]
    try:
        expected = streak_oracle(days, config["budget"], kwargs, streak)
    except ValueError as error:
        with pytest.raises(ValueError) as caught:
            return_curve(days, config["budget"], max_work_streak=streak, **kwargs)
        assert str(caught.value) == str(error)
        return "infeasible", config, streak
    curve = return_curve(days, config["budget"], max_work_streak=streak, **kwargs)
    got = [(p.budget, p.pto_spent, p.days_in_breaks) for p in curve.points]
    assert got == expected
    return "feasible", config, streak


@pytest.mark.parametrize("seed", range(60))
def test_curve_matches_per_budget_planning_under_a_limit(seed):
    run_differential(seed)


def test_the_differential_suite_exercises_what_it_claims():
    outcomes = [run_differential(s) for s in range(60)]
    assert sum(o[0] == "feasible" for o in outcomes) >= 15
    assert any(o[0] == "infeasible" for o in outcomes)
    # In some configs the limit changes the curve; otherwise nothing is tested.
    changed = 0
    for kind, config, streak in outcomes:
        if kind != "feasible":
            continue
        kwargs = dict(min_break=config["min_break"],
                      max_consecutive_pto=config["max_consecutive_pto"],
                      committed=config["committed"])
        limited = return_curve(config["days"], config["budget"], max_work_streak=streak, **kwargs)
        free = return_curve(config["days"], config["budget"], **kwargs)
        changed += [(p.budget, p.pto_spent, p.days_in_breaks) for p in limited.points] != [
            (p.budget, p.pto_spent, p.days_in_breaks) for p in free.points]
    assert changed >= 5


def test_the_default_curve_still_uses_the_one_pass(monkeypatch):
    import pto_colada.efficiency as efficiency_module
    calls = []
    real = efficiency_module.optimize_pto
    monkeypatch.setattr(efficiency_module, "optimize_pto",
                        lambda *a, **k: calls.append(1) or real(*a, **k))
    curve = return_curve(federal_year_days(), PtoBudget(default=14), min_break=4)
    assert len(curve.points) == 15
    assert len(calls) <= 1  # at most the guard-path call; never one per budget


def cli_json(capsys, *argv):
    assert main(list(argv) + ["--format", "json"]) == 0
    return json.loads(capsys.readouterr().out)


@pytest.mark.parametrize("streak", ["8", "10"])
def test_cli_curve_ends_at_the_plan_under_the_limit(capsys, streak):
    data = cli_json(capsys, *HORIZON, "--max-work-streak", streak, "--curve")
    last = data["curve"]["points"][-1]
    eff = data["plan"]["efficiency"]
    assert (last["days_in_breaks"], last["pto_spent"]) == (eff["days_in_breaks"], eff["pto_spent"])
    assert data["horizon"]["rest"]["longest_working_stretch"] <= int(streak)


def test_cli_limit_changes_the_curve(capsys):
    free = cli_json(capsys, *HORIZON, "--curve")["curve"]["points"]
    limited = cli_json(capsys, *HORIZON, "--max-work-streak", "8", "--curve")["curve"]["points"]
    assert free != limited


def test_cli_xlsx_chart_uses_the_limit(tmp_path, capsys):
    openpyxl = pytest.importorskip("openpyxl")
    out = tmp_path / "plan.xlsx"
    data = cli_json(capsys, *HORIZON, "--max-work-streak", "8", "--export-xlsx", str(out))
    sheet = openpyxl.load_workbook(out)["Return curve"]
    rows = list(sheet.iter_rows(min_row=2, values_only=True))
    eff = data["plan"]["efficiency"]
    assert (rows[-1][2], rows[-1][1]) == (eff["days_in_breaks"], eff["pto_spent"])
    # The chart must be the limited curve, not the unlimited one.
    limited = cli_json(capsys, *HORIZON, "--max-work-streak", "8", "--curve")["curve"]["points"]
    free = cli_json(capsys, *HORIZON, "--curve")["curve"]["points"]
    assert [r[:3] for r in rows] == [(p["budget"], p["pto_spent"], p["days_in_breaks"]) for p in limited]
    assert [r[:3] for r in rows] != [(p["budget"], p["pto_spent"], p["days_in_breaks"]) for p in free]

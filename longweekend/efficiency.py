"""Efficiency: what each extra PTO day buys, and where to stop spending.

The planner answers "given this budget, what is the best plan?". This module
answers the question people actually ask first: "how much budget is worth
spending?". It reruns the exact planner for every budget from 0 up to the
ceiling and records the result, which gives a return curve.

Terms
-----
*Days in breaks* is the number of days that fall inside a break of at least
``min_break`` consecutive days off -- the same quantity the planner maximises.
Weekends and holidays that do not belong to such a break are not counted.
The *efficiency* of a plan (or one break) is days in breaks per PTO day spent.

The *budget* on the curve is a per-year cap: budget ``b`` grants every year
``min(its grant, b)`` PTO days, with floating holidays, carryover, committed
days and blackouts exactly as configured. ``pto_spent`` is what the planner
actually booked across the whole window, floating days included.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Set

from .planner import PtoBudget, PtoPlan, optimize_pto
from .schedule import Day, DayKind

__all__ = ["CurvePoint", "ReturnCurve", "capped_budget", "return_curve"]

_EPSILON = 1e-9


@dataclass
class CurvePoint:
    budget: int            #: per-year PTO cap this point was planned with
    pto_spent: int         #: leave actually booked across the window
    days_in_breaks: int
    #: Days gained per extra day booked, relative to the previous point;
    #: ``None`` for the first point on the curve.
    marginal: Optional[float] = None

    @property
    def days_per_pto(self) -> Optional[float]:
        return self.days_in_breaks / self.pto_spent if self.pto_spent else None


@dataclass
class ReturnCurve:
    points: List[CurvePoint]
    #: The plan behind each point, keyed by budget, so callers can adopt one.
    plans: Dict[int, PtoPlan] = field(default_factory=dict, repr=False, compare=False)

    @property
    def knee(self) -> Optional[CurvePoint]:
        """The last budget that still earns the best rate of return.

        Once the best marginal return has been reached, the knee is the point
        just before the first budget step that earns less. ``None`` when the
        return never falls (including a curve with a single point).
        """
        marginals = [p.marginal for p in self.points if p.marginal is not None]
        if not marginals:
            return None
        best = max(marginals)
        peak = next(
            i for i, p in enumerate(self.points)
            if p.marginal is not None and p.marginal >= best - _EPSILON
        )
        for index in range(peak + 1, len(self.points)):
            marginal = self.points[index].marginal
            if marginal is not None and marginal < best - _EPSILON:
                return self.points[index - 1]
        return None

    def _hull(self) -> List[CurvePoint]:
        """The upper concave hull of days in breaks against PTO spent.

        The curve is not smooth: one day can return nothing while the next two
        return a whole new break, and a long tail alternates between poor and
        good days. The hull is the best that any mix of budgets can average,
        so its slopes only fall, and comparing them with a threshold is
        meaningful where comparing neighbouring steps is not.
        """
        hull: List[CurvePoint] = []
        for point in self.points:
            if hull and point.pto_spent == hull[-1].pto_spent:
                if point.days_in_breaks <= hull[-1].days_in_breaks:
                    continue  # extra budget that bought nothing
                hull.pop()
            while len(hull) >= 2 and _turns_up(hull[-2], hull[-1], point):
                hull.pop()
            hull.append(point)
        return hull

    def stop_for_min_return(self, minimum: float) -> CurvePoint:
        """Where to stop so that spending keeps averaging ``minimum`` days per PTO day.

        Walk the upper hull of the curve and stop at the last vertex reached by
        a slope of at least ``minimum``. Looking across a stretch of budgets,
        rather than at one day, means a day that returns nothing on its own
        (the first of two days that bridge a midweek holiday) does not end the
        search, and neither does a poor day followed by a good one. The last
        point is returned when every stretch clears the bar.
        """
        hull = self._hull()
        chosen = hull[0]
        for start, end in zip(hull, hull[1:]):
            gained = end.days_in_breaks - start.days_in_breaks
            booked = end.pto_spent - start.pto_spent
            if gained < minimum * booked - _EPSILON:
                break
            chosen = end
        return chosen

    def cheapest_for_target(self, target: int) -> CurvePoint:
        """The smallest budget reaching ``target`` days in breaks."""
        for point in self.points:
            if point.days_in_breaks >= target:
                return point
        best = self.points[-1]
        raise ValueError(
            f"cannot reach {target} days in breaks: a budget of {best.budget} PTO "
            f"day(s) per year can reach at most {best.days_in_breaks} days. "
            "Raise the budget or lower the target."
        )

    def as_dicts(self) -> List[Dict[str, Any]]:
        """The curve as plain rows, ready for JSON or a spreadsheet export."""
        knee = self.knee
        return [
            {
                "budget": p.budget,
                "pto_spent": p.pto_spent,
                "days_in_breaks": p.days_in_breaks,
                "marginal": round(p.marginal, 3) if p.marginal is not None else None,
                "days_per_pto": round(p.days_per_pto, 3) if p.days_per_pto is not None else None,
                "knee": knee is not None and p.budget == knee.budget,
            }
            for p in self.points
        ]


def _turns_up(a: CurvePoint, b: CurvePoint, c: CurvePoint) -> bool:
    """True when ``b`` lies on or below the line from ``a`` to ``c``."""
    return (
        (b.days_in_breaks - a.days_in_breaks) * (c.pto_spent - b.pto_spent)
        <= (c.days_in_breaks - b.days_in_breaks) * (b.pto_spent - a.pto_spent)
    )


def capped_budget(budget: PtoBudget, cap: int, years: Sequence[int]) -> PtoBudget:
    """``budget`` with every year's PTO grant held to at most ``cap`` days."""
    return PtoBudget(
        per_year={year: min(budget.for_year(year), cap) for year in years},
        default=min(budget.default, cap),
        carryover=budget.carryover,
        floating_per_year=dict(budget.floating_per_year),
        floating=budget.floating,
    )


def return_curve(
    days: Sequence[Day],
    budget: PtoBudget,
    min_break: int = 4,
    max_consecutive_pto: Optional[int] = None,
    committed: Optional[Set[dt.date]] = None,
    long_break_bonus: int = 0,
    max_budget: Optional[int] = None,
) -> ReturnCurve:
    """Plan every per-year budget from 0 to the ceiling and record what each buys.

    The ceiling is ``max_budget``, or the largest yearly grant in ``budget``.
    Budgets too small to honor committed days are left off the curve, which
    then starts at the first feasible one; if even the ceiling is infeasible
    the planner's error is raised.
    """
    years = sorted({d.date.year for d in days})
    ceiling = max_budget if max_budget is not None else max(
        (budget.for_year(year) for year in years), default=budget.default
    )

    # No year can take more PTO than it has working days, so once the cap
    # reaches that every larger budget plans identically; reuse the result
    # rather than rerunning the dynamic program for each one.
    usable = max(
        (sum(1 for d in days if d.date.year == y and d.kind is DayKind.WORK and not d.blackout)
         for y in years),
        default=0,
    )
    points: List[CurvePoint] = []
    plans: Dict[int, PtoPlan] = {}
    for cap in range(ceiling + 1):
        if points and points[-1].budget >= usable:
            last = points[-1]
            points.append(CurvePoint(cap, last.pto_spent, last.days_in_breaks, 0.0))
            plans[cap] = plans[last.budget]
            continue
        try:
            plan = optimize_pto(
                days,
                capped_budget(budget, cap, years),
                min_break=min_break,
                max_consecutive_pto=max_consecutive_pto,
                committed=committed,
                long_break_bonus=long_break_bonus,
            )
        except ValueError:
            if cap == ceiling:
                raise
            continue
        point = CurvePoint(cap, plan.total_pto, plan.days_in_breaks)
        if points:
            previous = points[-1]
            gained = point.days_in_breaks - previous.days_in_breaks
            booked = point.pto_spent - previous.pto_spent
            point.marginal = gained / booked if booked > 0 else 0.0
        points.append(point)
        plans[cap] = plan
    return ReturnCurve(points=points, plans=plans)

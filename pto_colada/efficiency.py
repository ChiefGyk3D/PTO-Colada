"""Efficiency: what each extra PTO day buys, and where to stop spending.

The planner answers "given this budget, what is the best plan?". This module
answers the question people actually ask first: "how much budget is worth
spending?". It records what the exact planner returns for every budget from 0
up to the ceiling, which gives a return curve. The numbers come from a single
pass over the schedule (see :func:`_one_pass_points`) rather than one planner
run per budget; the plan behind a point is still made by the planner itself.

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
from typing import Any, Dict, Iterator, List, Mapping, Optional, Sequence, Set, Tuple

from .planner import PtoBudget, PtoPlan, _extend, optimize_pto
from .schedule import Day, DayKind

__all__ = [
    "CurvePoint", "REPLAN_MAX_BUDGETS", "REPLAN_WARN_BUDGETS", "ReturnCurve",
    "capped_budget", "replan_count", "return_curve",
]

#: A curve that must replan more budgets than this is slow enough to warn about.
REPLAN_WARN_BUDGETS = 10
#: The xlsx export drops a Return curve that would replan more budgets than this
#: (about a second each); ``--curve`` and the stop options still compute it.
REPLAN_MAX_BUDGETS = 120

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
    #: Valid keys are exactly the budgets in ``points``; others raise
    #: ``KeyError``. Plans for budgets the curve skipped as saturated are made
    #: on first access (see :class:`_CurvePlans`).
    plans: Mapping[int, PtoPlan] = field(default_factory=dict, repr=False, compare=False)

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


class _Planner:
    """Plans one per-year cap with fixed settings; a class so a curve can be pickled."""

    def __init__(
        self,
        days: Sequence[Day],
        budget: PtoBudget,
        years: Sequence[int],
        min_break: int,
        max_consecutive_pto: Optional[int],
        committed: Optional[Set[dt.date]],
        long_break_bonus: int,
        max_work_streak: Optional[int] = None,
    ) -> None:
        self.days = days
        self.budget = budget
        self.years = years
        self.min_break = min_break
        self.max_consecutive_pto = max_consecutive_pto
        self.committed = committed
        self.long_break_bonus = long_break_bonus
        self.max_work_streak = max_work_streak

    def __call__(self, cap: int) -> PtoPlan:
        return optimize_pto(
            self.days,
            capped_budget(self.budget, cap, self.years),
            min_break=self.min_break,
            max_consecutive_pto=self.max_consecutive_pto,
            committed=self.committed,
            long_break_bonus=self.long_break_bonus,
            max_work_streak=self.max_work_streak,
        )


class _CurvePlans(Mapping[int, PtoPlan]):
    """The plan behind each of a curve's budgets, made when first asked for.

    The keys are exactly the curve's budgets: anything else raises
    ``KeyError`` as a dict would, and ``in``, ``len`` and iteration never plan
    anything. Budgets the curve skipped (past a year's working days) are
    planned on first access and remembered, so ``values()`` and ``items()``
    plan every skipped budget -- iterate the keys when that is not wanted.
    """

    def __init__(self, planner: _Planner) -> None:
        self._planner = planner
        self._budgets: List[int] = []
        self._made: Dict[int, PtoPlan] = {}

    def _record(self, cap: int, plan: Optional[PtoPlan] = None) -> None:
        self._budgets.append(cap)
        if plan is not None:
            self._made[cap] = plan

    def __getitem__(self, cap: int) -> PtoPlan:
        if cap not in self._budgets:
            raise KeyError(cap)
        plan = self._made.get(cap)
        if plan is None:
            plan = self._made[cap] = self._planner(cap)
        return plan

    def __contains__(self, cap: object) -> bool:
        return cap in self._budgets

    def __iter__(self) -> Iterator[int]:
        return iter(self._budgets)

    def __len__(self) -> int:
        return len(self._budgets)


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


_NEG = -(10 ** 9)  # "unreachable"; far below any real score

_Run = Tuple[int, int]  # (capped run of days off, capped run of PTO days)


class _Segment:
    """One calendar year of the window, tabulated by how many PTO days it spends.

    The planner's state carries the allowance still unspent. Here the state
    carries the days spent instead, so a single table answers every budget:
    ``score[start][end][k]`` is the most the segment can score when it begins
    in run-state ``start``, ends in ``end`` and books exactly ``k`` days
    (``_NEG`` when impossible). Whether ``k`` fits the allowance is decided
    later, per budget, when the segments are chained.
    """

    def __init__(
        self,
        days: Sequence[Day],
        grant: int,
        floating: int,
        committed: Set[dt.date],
        min_break: int,
        pto_run_cap: int,
        limit: int,
    ) -> None:
        self.days = days
        self.grant = grant
        self.floating = floating
        self.committed = committed
        self.min_break = min_break
        self.pto_run_cap = pto_run_cap
        #: Most days any budget could let this segment spend.
        self.size = min(
            limit, sum(1 for d in days if not d.is_off and not d.blackout)
        )
        self.score: Dict[_Run, Dict[_Run, List[int]]] = {}
        #: ``best[start][end][a]``: the (score, days) with the fewest days among
        #: the top scores over every ``k <= a``; ``None`` when unreachable.
        self.best: Dict[_Run, Dict[_Run, List[Optional[Tuple[int, int]]]]] = {}

    def table(self, start: _Run) -> Dict[_Run, List[int]]:
        if start not in self.score:
            self._build(start)
        return self.score[start]

    def _build(self, start: _Run) -> None:
        size, neg = self.size, _NEG
        min_break, cap = self.min_break, self.pto_run_cap
        current: Dict[_Run, List[int]] = {start: [0] + [neg] * size}

        def merge(into: Dict[_Run, List[int]], key: _Run, row: List[int]) -> None:
            old = into.get(key)
            into[key] = row if old is None else [a if a >= b else b for a, b in zip(old, row)]

        for day in self.days:
            following: Dict[_Run, List[int]] = {}
            if day.is_off:
                for (run, _pto), row in current.items():
                    new_run, gain = _extend(run, min_break)
                    merge(following, (new_run, 0), [v + gain for v in row])
            else:
                forced = day.date in self.committed
                for (run, pto), row in current.items():
                    if not forced:
                        merge(following, (0, 0), row)  # work the day
                    if day.blackout or (cap and pto >= cap):
                        continue
                    new_run, gain = _extend(run, min_break)
                    new_pto = min(pto + 1, cap) if cap else 0
                    merge(following, (new_run, new_pto), [neg] + [v + gain for v in row[:-1]])
            current = following

        self.score[start] = {
            end: row for end, row in current.items() if any(v > neg // 2 for v in row)
        }
        best: Dict[_Run, List[Optional[Tuple[int, int]]]] = {}
        for end, row in self.score[start].items():
            top: Optional[Tuple[int, int]] = None
            prefix: List[Optional[Tuple[int, int]]] = []
            for spent, value in enumerate(row):
                if value > neg // 2 and (top is None or value > top[0]):
                    top = (value, spent)  # equal scores keep the earlier, cheaper one
                prefix.append(top)
            best[end] = prefix
        self.best[start] = best


def _one_pass_points(
    days: Sequence[Day],
    budget: PtoBudget,
    caps: Sequence[int],
    min_break: int,
    max_consecutive_pto: Optional[int],
    committed: Optional[Set[dt.date]],
) -> Dict[int, Tuple[int, int]]:
    """``{cap: (days_in_breaks, pto_spent)}`` for every feasible cap, in one pass.

    Reproduces what :func:`optimize_pto` returns under each per-year cap
    (``long_break_bonus`` 0, where its score is days in breaks) without running
    it once per cap. Each calendar year is tabulated once by days spent; the
    tables are then chained per cap. Years are coupled only by the run of days
    off across New Year and by carryover: the days rolled into a year are
    ``min(unspent, carryover, that year's capped grant)``.
    """
    pto_run_cap = max_consecutive_pto if max_consecutive_pto is not None else 0
    committed = committed or set()
    top = max(caps, default=0)
    # Split the window into calendar years, in order, as the planner walks it.
    chunks: List[List[Day]] = []
    for day in days:
        if chunks and chunks[-1][0].date.year == day.date.year:
            chunks[-1].append(day)
        else:
            chunks.append([day])
    segments = [
        _Segment(
            chunk,
            budget.for_year(chunk[0].date.year),
            budget.floating_for_year(chunk[0].date.year),
            committed,
            min_break,
            pto_run_cap,
            min(budget.for_year(chunk[0].date.year), top)
            + budget.floating_for_year(chunk[0].date.year)
            + (budget.carryover if chunk is not chunks[0] else 0),
        )
        for chunk in chunks
    ]

    results: Dict[int, Tuple[int, int]] = {}
    for cap in caps:
        starts: Dict[Tuple[_Run, int], Tuple[int, int]] = {((0, 0), 0): (0, 0)}
        for index, segment in enumerate(segments):
            last = index == len(segments) - 1
            capped = min(segment.grant, cap)
            base = capped + segment.floating
            rolls = min(budget.carryover, capped)
            nxt: Dict[Tuple[_Run, int], Tuple[int, int]] = {}
            for (start, carried), (score, spent) in starts.items():
                allowance = base + carried
                table = segment.table(start)
                best = segment.best[start]
                for end in table:
                    prefix = best[end]
                    if last:
                        entry = prefix[min(allowance, segment.size)]
                        _offer(nxt, (end, 0), entry, score, spent)
                        continue
                    if allowance - rolls >= 0:
                        entry = prefix[min(allowance - rolls, segment.size)]
                        _offer(nxt, (end, rolls), entry, score, spent)
                    row = table[end]
                    for used in range(max(0, allowance - rolls + 1), min(allowance, segment.size) + 1):
                        if row[used] > _NEG // 2:
                            _offer(nxt, (end, allowance - used), (row[used], used), score, spent)
            starts = nxt
            if not starts:
                break
        if starts:
            results[cap] = _pick(starts)
    return results


def _pick(states: Dict[Tuple[_Run, int], Tuple[int, int]]) -> Tuple[int, int]:
    """The best (score, spent): highest score, then fewest days."""
    score, spent = min(((-s, k) for s, k in states.values()))
    return -score, spent


def _offer(
    into: Dict[Tuple[_Run, int], Tuple[int, int]],
    key: Tuple[_Run, int],
    entry: Optional[Tuple[int, int]],
    score: int,
    spent: int,
) -> None:
    if entry is None:
        return
    candidate = (score + entry[0], spent + entry[1])
    old = into.get(key)
    if old is None or (candidate[0], -candidate[1]) > (old[0], -old[1]):
        into[key] = candidate


def _ceiling_and_usable(
    days: Sequence[Day], budget: PtoBudget, max_budget: Optional[int]
) -> Tuple[List[int], int, int]:
    """The curve's years, its ceiling, and the budget past which plans stop changing."""
    years = sorted({d.date.year for d in days})
    ceiling = max_budget if max_budget is not None else max(
        (budget.for_year(year) for year in years), default=budget.default
    )

    # No year can take more PTO than it has working days, so once the cap
    # reaches that every larger budget plans identically. That holds only
    # when a year's allowance does not depend on the cap beyond its own grant:
    # with carryover, a year granted fewer days than it has working days lives
    # off days rolled from the year before, and those exist only while the cap
    # leaves that earlier year unused days. Then every budget is computed.
    # Likewise a cap at or above every grant leaves the budget unchanged.
    workdays = {
        y: sum(1 for d in days if d.date.year == y and d.kind is DayKind.WORK and not d.blackout)
        for y in years
    }
    skippable = budget.carryover == 0 or all(
        budget.for_year(y) >= workdays[y] for y in years
    )
    usable = max(workdays.values(), default=0) if skippable else ceiling + 1
    if years:
        usable = min(usable, max(budget.for_year(y) for y in years))
    return years, ceiling, usable


def replan_count(
    days: Sequence[Day],
    budget: PtoBudget,
    max_budget: Optional[int] = None,
    max_work_streak: Optional[int] = None,
) -> int:
    """How many budgets :func:`return_curve` must replan with the planner.

    Zero when the curve comes from the one pass (no ``max_work_streak``). With
    a limit every budget up to the point where plans stop changing is planned.
    """
    if max_work_streak is None or not days:
        return 0
    _years, ceiling, usable = _ceiling_and_usable(days, budget, max_budget)
    return min(ceiling, usable) + 1


def return_curve(
    days: Sequence[Day],
    budget: PtoBudget,
    min_break: int = 4,
    max_consecutive_pto: Optional[int] = None,
    committed: Optional[Set[dt.date]] = None,
    long_break_bonus: int = 0,
    max_budget: Optional[int] = None,
    max_work_streak: Optional[int] = None,
) -> ReturnCurve:
    """Record what every per-year budget from 0 to the ceiling buys.

    The ceiling is ``max_budget``, or the largest yearly grant in ``budget``.
    Budgets too small to honor committed days are left off the curve, which
    then starts at the first feasible one; if even the ceiling is infeasible
    the planner's error is raised.

    The points are computed in one pass over the schedule. The exception is a
    nonzero ``long_break_bonus``: the planner then scores a break by more than
    its length, so which of several equally scored plans it settles on decides
    how many days land in breaks, and only the planner itself can say. Those
    curves replan each budget.

    ``max_work_streak`` is honored the same way as in :func:`optimize_pto`. The
    one-pass tables do not track the working-day streak, so a curve with a
    limit replans each budget too (correctness over speed). Budgets that
    cannot meet the limit are left off the curve; if the ceiling cannot meet
    it, the planner's error is raised.
    """
    years, ceiling, usable = _ceiling_and_usable(days, budget, max_budget)

    planner = _Planner(
        days, budget, years, min_break, max_consecutive_pto, committed, long_break_bonus,
        max_work_streak,
    )
    if long_break_bonus or max_work_streak is not None or not days:
        return _replanned_curve(planner, ceiling, usable)

    computed = _one_pass_points(
        days, budget, range(min(ceiling, usable) + 1), min_break, max_consecutive_pto, committed
    )
    points: List[CurvePoint] = []
    plans = _CurvePlans(planner)
    for cap in range(ceiling + 1):
        found = computed.get(min(cap, usable))
        if found is None:
            continue
        in_breaks, spent = found
        point = CurvePoint(cap, spent, in_breaks)
        if points:
            previous = points[-1]
            booked = point.pto_spent - previous.pto_spent
            point.marginal = (point.days_in_breaks - previous.days_in_breaks) / booked if booked > 0 else 0.0
        points.append(point)
        plans._record(cap)
    if not points or points[-1].budget != ceiling:
        planner(ceiling)  # reached only if the one pass and the planner disagree
        raise RuntimeError("return curve and planner disagree on feasibility")
    return ReturnCurve(points=points, plans=plans)


def _replanned_curve(planner: _Planner, ceiling: int, usable: int) -> ReturnCurve:
    """The curve made by running the planner for each budget (the reference method)."""
    points: List[CurvePoint] = []
    # Skipped budgets keep their numbers but not a stored plan: a fresh plan
    # may pick different dates among equal ones, so it is made when asked for.
    plans = _CurvePlans(planner)
    for cap in range(ceiling + 1):
        if points and points[-1].budget >= usable:
            last = points[-1]
            points.append(CurvePoint(cap, last.pto_spent, last.days_in_breaks, 0.0))
            plans._record(cap)
            continue
        try:
            plan = planner(cap)
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
        plans._record(cap, plan)
    return ReturnCurve(points=points, plans=plans)

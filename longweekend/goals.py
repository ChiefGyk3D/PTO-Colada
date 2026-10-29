"""Goal-driven PTO planning on top of the honest counting rules.

The classic planner (:mod:`longweekend.planner`) maximises days inside breaks
of a minimum length, whether or not a break cost any PTO. This planner scores
what :mod:`longweekend.honest` reports: only breaks that contain at least one
PTO day count, each calendar date once, and a break crossing a year boundary
is valued whole.

Goals
-----
A goal is a value function ``f(L)`` for one PTO-containing break of ``L`` days,
built from ``(threshold, weight)`` pairs::

    f(L) = sum(weight * L for every pair where L >= threshold)

``weekends``    ``f = L if L >= 3``. Every day in a long weekend counts, so many
                short breaks win whenever they are cheap.
``vacations``   ``f = L if L >= V`` (``V`` is ``--min-vacation-days``, default 7).
                Shorter breaks are worth nothing, so leave is gathered into
                long trips.
``balanced``    ``f = L if L >= 3, plus L if L >= V``. A day in a long weekend
                is worth 1; a day in a vacation is worth 2.

A plan's score is the sum of ``f`` over its breaks, plus ``PREFER_BONUS`` for
each PTO day taken on a ``--prefer`` date. ``--max-breaks`` caps the number of
PTO-containing breaks.

Method
------
Exact dynamic program over days, like the classic planner, with state
``(allowance left, run length, run has PTO, PTO run, breaks so far)``. A run
without PTO is tracked at its true length so that when PTO joins it the whole
run is valued retroactively. The break counter makes ``--max-breaks`` exact;
the price is a state space larger by about the cap. The method is always
exact, and the result says so.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple

from .catalog import DEFAULT_WORKWEEK, ResolvedHoliday
from .planner import PtoBudget, _validate_commitments
from .schedule import Day, DayKind, build_days

__all__ = [
    "GOALS",
    "PREFER_BONUS",
    "PAD_DAYS",
    "GoalResult",
    "GoalSpec",
    "build_goal_days",
    "goal_spec",
    "optimize_goal",
]

GOALS = ("weekends", "vacations", "balanced")

#: Points added to a plan for each PTO day taken on a preferred date. A long
#: weekend is worth 3+ points, so a preference tips close calls rather than
#: dragging in a day that buys nothing.
PREFER_BONUS = 2

#: Days of context added either side of the window. They are blackout days:
#: they count toward breaks that cross the boundary but can never take PTO.
PAD_DAYS = 31

_DEFAULT_LONG_WEEKEND = 3
_DEFAULT_VACATION = 7


@dataclass(frozen=True)
class GoalSpec:
    name: str
    thresholds: Tuple[Tuple[int, int], ...]

    def value(self, length: int) -> int:
        return sum(weight * length for threshold, weight in self.thresholds if length >= threshold)

    @property
    def cap(self) -> int:
        return max(threshold for threshold, _ in self.thresholds)

    def describe(self) -> str:
        parts = [f"{w}/day in breaks of {t}+ days" for t, w in self.thresholds]
        return f"{self.name}: " + ", ".join(parts)


def goal_spec(
    goal: str,
    min_break: Optional[int] = None,
    min_vacation_days: Optional[int] = None,
) -> GoalSpec:
    """The value function for ``goal``. ``min_break`` replaces the 3-day long-weekend threshold."""
    if goal not in GOALS:
        raise ValueError(f"unknown goal {goal!r}; choose one of: {', '.join(GOALS)}")
    short = min_break if min_break is not None else _DEFAULT_LONG_WEEKEND
    vacation = min_vacation_days if min_vacation_days is not None else _DEFAULT_VACATION
    if short < 1:
        raise ValueError("min_break must be at least 1")
    if vacation < 2:
        raise ValueError("minimum vacation length must be at least 2 days")
    if goal == "weekends":
        return GoalSpec(goal, ((short, 1),))
    if goal == "vacations":
        return GoalSpec(goal, ((vacation, 1),))
    return GoalSpec(goal, ((short, 1), (vacation, 1)))


@dataclass
class GoalResult:
    pto_dates: List[dt.date]
    score: int
    spent_by_year: Dict[int, int]
    granted_by_year: Dict[int, int]
    floating_by_year: Dict[int, int]
    spec: GoalSpec
    method: str
    days: List[Day] = field(default_factory=list, repr=False)


def build_goal_days(
    start: dt.date,
    end: dt.date,
    holidays: Sequence[ResolvedHoliday],
    workweek: Optional[Set[int]] = None,
    blackout: Optional[Set[dt.date]] = None,
    pad: int = PAD_DAYS,
) -> List[Day]:
    """Days for ``[start, end]`` plus ``pad`` days either side, the extras blacked out."""
    one = dt.timedelta(days=1)
    days = build_days(start - pad * one, end + pad * one, holidays, workweek, blackout)
    for day in days:
        if not (start <= day.date <= end):
            day.blackout = True
    return days


# State: (allowance left, run length, run has PTO, PTO run, PTO breaks so far)
_State = Tuple[int, int, bool, int, int]


def optimize_goal(
    days: Sequence[Day],
    budget: PtoBudget,
    spec: GoalSpec,
    window: Tuple[dt.date, dt.date],
    committed: Optional[Set[dt.date]] = None,
    prefer: Optional[Set[dt.date]] = None,
    prefer_bonus: int = PREFER_BONUS,
    blackout: Optional[Set[dt.date]] = None,
    max_breaks: Optional[int] = None,
    max_consecutive_pto: Optional[int] = None,
) -> GoalResult:
    """Choose PTO days to maximise ``spec``'s score; see the module docstring."""
    if max_breaks is not None and max_breaks < 1:
        raise ValueError("max_breaks must be at least 1")
    if max_consecutive_pto is not None and max_consecutive_pto < 1:
        raise ValueError("max_consecutive_pto must be at least 1")
    if prefer_bonus < 0:
        raise ValueError("prefer_bonus cannot be negative")
    committed = set(committed or ())
    prefer = set(prefer or ())
    blocked = set(blackout or ())
    start, end = window
    plan_days = [Day(d.date, d.kind, d.holiday, d.blackout or d.date in blocked) for d in days]
    in_window = [d for d in plan_days if start <= d.date <= end]
    if not in_window:
        return GoalResult([], 0, {}, {}, {}, spec, "exact dynamic program")
    # Commitments outside the window are not this plan's to book.
    committed = {c for c in committed if start <= c <= end}
    _validate_commitments(in_window, budget, committed)

    pto_run_cap = max_consecutive_pto or 0
    cap = spec.cap
    value = spec.value
    inc = [value(r + 1) - value(r) for r in range(cap + 1)]
    first_year, last_year = start.year, end.year

    granted: Dict[int, int] = {first_year: budget.for_year(first_year)}
    floating: Dict[int, int] = {first_year: budget.floating_for_year(first_year)}
    start_state: _State = (budget.total_for_year(first_year), 0, False, 0, 0)
    frontier: Dict[_State, Tuple[int, int]] = {start_state: (0, 0)}
    trace: List[Dict[_State, Tuple[_State, bool]]] = []

    previous_year = first_year
    for day in plan_days:
        year = min(max(day.date.year, first_year), last_year)
        regrant: Optional[int] = None
        rollover_cap = 0
        if year != previous_year:
            granted[year] = budget.for_year(year)
            floating[year] = budget.floating_for_year(year)
            regrant = budget.total_for_year(year)
            rollover_cap = min(budget.carryover, granted[previous_year])
            previous_year = year

        is_pad = not (start <= day.date <= end)
        next_frontier: Dict[_State, Tuple[int, int]] = {}
        step: Dict[_State, Tuple[_State, bool]] = {}

        def offer(state: _State, val: Tuple[int, int], came: _State, took: bool) -> None:
            current = next_frontier.get(state)
            if current is None or (val[0], -val[1]) > (current[0], -current[1]):
                next_frontier[state] = val
                step[state] = (came, took)

        bonus = prefer_bonus if day.date in prefer else 0
        must_take = day.date in committed
        for state, (score, spent) in frontier.items():
            remaining, run, has_pto, pto_run, breaks = state
            if regrant is not None:
                remaining = regrant + min(remaining, rollover_cap)

            if day.is_off:
                if has_pto:
                    offer((remaining, min(run + 1, cap), True, 0, breaks),
                          (score + inc[run], spent), state, False)
                else:
                    offer((remaining, run + 1, False, 0, breaks), (score, spent), state, False)
                continue

            can_take = (
                not is_pad
                and remaining > 0
                and not day.blackout
                and (pto_run_cap == 0 or pto_run < pto_run_cap)
            )
            take_state: Optional[_State] = None
            gain = 0
            if can_take:
                new_pto_run = min(pto_run + 1, pto_run_cap) if pto_run_cap else 0
                if has_pto:
                    take_state = (remaining - 1, min(run + 1, cap), True, new_pto_run, breaks)
                    gain = inc[run]
                elif max_breaks is None or breaks < max_breaks:
                    length = run + 1
                    take_state = (
                        remaining - 1, min(length, cap), True, new_pto_run,
                        breaks + 1 if max_breaks is not None else 0,
                    )
                    gain = value(length)
            if not must_take:
                offer((remaining, 0, False, 0, breaks), (score, spent), state, False)
            if take_state is not None:
                offer(take_state, (score + gain + bonus, spent + 1), state, True)

        if not next_frontier:
            raise ValueError(
                f"no feasible PTO plan at {day.date.isoformat()}: committed dates conflict "
                "with the budget, a blackout period, --max-breaks or --max-consecutive-pto"
            )
        frontier = next_frontier
        trace.append(step)

    best = max(frontier, key=lambda s: (frontier[s][0], -frontier[s][1]))
    best_score = frontier[best][0]
    taken: List[dt.date] = []
    for index in range(len(plan_days) - 1, -1, -1):
        previous_state, took = trace[index][best]
        if took:
            taken.append(plan_days[index].date)
        best = previous_state
    taken.sort()

    spent: Dict[int, int] = {}
    for date in taken:
        spent[date.year] = spent.get(date.year, 0) + 1
    taken_set = set(taken)
    marked = [Day(d.date, DayKind.PTO if d.date in taken_set else d.kind, d.holiday, d.blackout)
              for d in plan_days]
    return GoalResult(
        pto_dates=taken,
        score=best_score,
        spent_by_year=spent,
        granted_by_year=granted,
        floating_by_year=floating,
        spec=spec,
        method="exact dynamic program" + (
            f" (break count tracked, cap {max_breaks})" if max_breaks is not None else ""
        ),
        days=marked,
    )

"""PTO allocation.

Given a schedule of working days, holidays and a PTO budget, choose which
working days to take off so that the year contains as many long consecutive
breaks as possible.

The search is an exact dynamic program rather than a greedy pass over
"bridge" opportunities. Greedy scoring gets the easy cases right but loses
whenever two candidate bridges compete for the same budget, or when spending
four days around a midweek holiday beats spending one day twice elsewhere.
The state space here is small enough (days x remaining budget x run length)
that the exact answer is cheap.

Objective
---------
Maximise the total number of days that fall inside a break of at least
``min_break`` consecutive days off. This rewards turning a three-day weekend
into a four-day one (+4) far more than lengthening an existing nine-day break
(+1), which matches how people actually value time off, and it does not
degenerate into a single enormous holiday the way a length-squared objective
does. Ties are broken toward spending less PTO.

That objective spends leftover budget on many four-day weekends rather than
one long trip, because a fresh short break scores more per day than extending
an existing one. Which of those you want is a preference, not a fact, so
``long_break_bonus`` raises the value of every day past ``min_break``: 0 keeps
the many-long-weekends behaviour, 1 consolidates leftover days into longer
vacations.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Set, Tuple

from .schedule import Break, Day, DayKind, find_breaks

__all__ = ["PtoPlan", "PtoBudget", "optimize_pto"]


@dataclass
class PtoBudget:
    """PTO days available per calendar year, plus how many may roll over."""

    per_year: Dict[int, int] = field(default_factory=dict)
    default: int = 0
    carryover: int = 0

    def for_year(self, year: int) -> int:
        return self.per_year.get(year, self.default)


@dataclass
class PtoPlan:
    days: List[Day]
    breaks: List[Break]
    pto_dates: List[dt.date]
    budget: PtoBudget
    spent_by_year: Dict[int, int]
    granted_by_year: Dict[int, int]
    min_break: int

    @property
    def total_days_off(self) -> int:
        return sum(1 for d in self.days if d.is_off)

    @property
    def total_pto(self) -> int:
        return len(self.pto_dates)

    def remaining(self, year: int) -> int:
        return self.granted_by_year.get(year, 0) - self.spent_by_year.get(year, 0)


# State: (pto_remaining_this_year, capped_run_length, capped_pto_run_length)
_State = Tuple[int, int, int]


def optimize_pto(
    days: Sequence[Day],
    budget: PtoBudget,
    min_break: int = 4,
    max_consecutive_pto: Optional[int] = None,
    committed: Optional[Set[dt.date]] = None,
    long_break_bonus: int = 0,
) -> PtoPlan:
    """Return a copy of ``days`` with the best PTO days marked.

    ``committed`` dates are treated as already booked: they are always taken
    and always charged to the budget. ``long_break_bonus`` trades many short
    breaks for fewer long ones; see the module docstring.
    """
    if min_break < 1:
        raise ValueError("min_break must be at least 1")
    if long_break_bonus < 0:
        raise ValueError("long_break_bonus cannot be negative")
    if max_consecutive_pto is not None and max_consecutive_pto < 1:
        raise ValueError("max_consecutive_pto must be at least 1")
    committed = committed or set()

    plan_days = [Day(d.date, d.kind, d.holiday, d.blackout) for d in days]
    if not plan_days:
        return _finish(plan_days, budget, {}, {}, min_break)

    _validate_commitments(plan_days, budget, committed)

    pto_run_cap = max_consecutive_pto if max_consecutive_pto is not None else 0
    granted: Dict[int, int] = {}

    first_year = plan_days[0].date.year
    granted[first_year] = budget.for_year(first_year)
    start_state: _State = (granted[first_year], 0, 0)
    # value: (score, pto_spent) -- ranked by score, then by spending less.
    frontier: Dict[_State, Tuple[int, int]] = {start_state: (0, 0)}
    trace: List[Dict[_State, Tuple[_State, bool]]] = []

    previous_year = first_year
    for day in plan_days:
        # A new calendar year resets the allowance. The reset is applied as
        # part of the day's transition rather than as a separate pass so that
        # every state in the trace stays reachable when backtracking.
        regrant: Optional[int] = None
        if day.date.year != previous_year:
            granted[day.date.year] = budget.for_year(day.date.year)
            regrant = granted[day.date.year]
            previous_year = day.date.year

        frontier, step = _advance(
            frontier, day, min_break, pto_run_cap, day.date in committed,
            regrant, budget.carryover, long_break_bonus,
        )
        if not frontier:
            raise ValueError(
                f"no feasible PTO plan at {day.date.isoformat()}: committed dates conflict "
                "with the budget, a blackout period, or --max-consecutive-pto"
            )
        trace.append(step)

    best_state = max(frontier, key=lambda s: (frontier[s][0], -frontier[s][1]))
    for index in range(len(plan_days) - 1, -1, -1):
        previous_state, took_pto = trace[index][best_state]
        if took_pto:
            plan_days[index].kind = DayKind.PTO
        best_state = previous_state

    pto_dates = [d.date for d in plan_days if d.kind is DayKind.PTO]
    spent: Dict[int, int] = {}
    for date in pto_dates:
        spent[date.year] = spent.get(date.year, 0) + 1
    return _finish(plan_days, budget, spent, granted, min_break)


def _advance(
    frontier: Dict[_State, Tuple[int, int]],
    day: Day,
    min_break: int,
    pto_run_cap: int,
    is_committed: bool,
    regrant: Optional[int] = None,
    carryover: int = 0,
    long_break_bonus: int = 0,
) -> Tuple[Dict[_State, Tuple[int, int]], Dict[_State, Tuple[_State, bool]]]:
    """Expand every state across a single day.

    When ``regrant`` is set this day begins a new calendar year, so the
    remaining allowance becomes the new grant plus whatever rolls over.
    """
    next_frontier: Dict[_State, Tuple[int, int]] = {}
    step: Dict[_State, Tuple[_State, bool]] = {}

    def offer(state: _State, value: Tuple[int, int], came_from: _State, took_pto: bool) -> None:
        current = next_frontier.get(state)
        if current is None or (value[0], -value[1]) > (current[0], -current[1]):
            next_frontier[state] = value
            step[state] = (came_from, took_pto)

    for state, (score, spent) in frontier.items():
        remaining, run, pto_run = state
        if regrant is not None:
            remaining = regrant + min(remaining, carryover)

        if day.is_off:  # already a holiday or a non-working day
            new_run, gain = _extend(run, min_break, long_break_bonus)
            offer((remaining, new_run, 0), (score + gain, spent), state, False)
            continue

        can_take = (
            remaining > 0
            and not day.blackout
            and (pto_run_cap == 0 or pto_run < pto_run_cap)
        )
        if is_committed:
            # A committed day is not optional: only the "take it" branch exists.
            if can_take:
                new_run, gain = _extend(run, min_break, long_break_bonus)
                new_pto_run = min(pto_run + 1, pto_run_cap) if pto_run_cap else 0
                offer(
                    (remaining - 1, new_run, new_pto_run),
                    (score + gain, spent + 1),
                    state,
                    True,
                )
            continue

        offer((remaining, 0, 0), (score, spent), state, False)  # work the day
        if can_take:
            new_run, gain = _extend(run, min_break, long_break_bonus)
            new_pto_run = min(pto_run + 1, pto_run_cap) if pto_run_cap else 0
            offer(
                (remaining - 1, new_run, new_pto_run),
                (score + gain, spent + 1),
                state,
                True,
            )

    return next_frontier, step


def _extend(run: int, min_break: int, long_break_bonus: int = 0) -> Tuple[int, int]:
    """Extend a run of days off by one, returning the new capped run and the score gained.

    A run scores nothing until it reaches ``min_break``, at which point the
    whole run counts at once; every day beyond that adds one, plus the
    long-break bonus.
    """
    if run >= min_break:
        return min_break, 1 + long_break_bonus
    length = run + 1
    if length >= min_break:
        return min_break, min_break
    return length, 0


def _validate_commitments(
    days: Sequence[Day], budget: PtoBudget, committed: Set[dt.date]
) -> None:
    """Fail early and clearly when commitments cannot fit the budget."""
    working = {d.date for d in days if d.kind is DayKind.WORK}
    first_year = days[0].date.year

    by_year: Dict[int, int] = {}
    for date in committed:
        if date in working:  # a commitment on a holiday or weekend costs nothing
            by_year[date.year] = by_year.get(date.year, 0) + 1

    for year, count in sorted(by_year.items()):
        grant = budget.for_year(year)
        # Only a year after the first can receive rolled-over days.
        available = grant + (budget.carryover if year > first_year else 0)
        if count > available:
            raise ValueError(
                f"{count} committed PTO day(s) in {year} exceed the {available} day(s) "
                f"available that year (budget {grant}"
                + (f" + {budget.carryover} carryover" if year > first_year and budget.carryover else "")
                + ")"
            )


def _finish(
    days: List[Day],
    budget: PtoBudget,
    spent: Dict[int, int],
    granted: Dict[int, int],
    min_break: int,
) -> PtoPlan:
    return PtoPlan(
        days=days,
        breaks=find_breaks(days, min_length=min_break),
        pto_dates=[d.date for d in days if d.kind is DayKind.PTO],
        budget=budget,
        spent_by_year=spent,
        granted_by_year=granted,
        min_break=min_break,
    )

"""Day-by-day schedule model and consecutive-break detection.

Everything downstream works off a flat list of :class:`Day` records rather
than special-casing weekdays, which is what lets the tool support non-standard
workweeks and company shutdown days without extra branches.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple

from .catalog import DEFAULT_WORKWEEK, ResolvedHoliday

__all__ = ["DayKind", "Day", "Break", "build_days", "find_breaks", "date_range"]


class DayKind(str, Enum):
    WORK = "work"          #: A normal working day.
    WEEKEND = "weekend"    #: Off because of the workweek (not necessarily Sat/Sun).
    HOLIDAY = "holiday"    #: Off because of an observed holiday.
    PTO = "pto"            #: A working day spent from the PTO budget.

    @property
    def is_off(self) -> bool:
        return self is not DayKind.WORK


@dataclass
class Day:
    date: dt.date
    kind: DayKind
    holiday: Optional[str] = None
    blackout: bool = False

    @property
    def is_off(self) -> bool:
        return self.kind.is_off


@dataclass
class Break:
    """A maximal run of consecutive days off."""

    start: dt.date
    end: dt.date
    days: List[Day] = field(default_factory=list)

    @property
    def length(self) -> int:
        return len(self.days)

    @property
    def pto_dates(self) -> List[dt.date]:
        return [d.date for d in self.days if d.kind is DayKind.PTO]

    @property
    def pto_cost(self) -> int:
        return len(self.pto_dates)

    @property
    def holidays(self) -> List[str]:
        return [d.holiday for d in self.days if d.holiday]

    @property
    def efficiency(self) -> Optional[float]:
        """Days off gained per PTO day spent, or ``None`` when nothing was spent."""
        return self.length / self.pto_cost if self.pto_cost else None

    def label(self) -> str:
        if self.start == self.end:
            return self.start.strftime("%a %b %d")
        if self.start.year == self.end.year:
            return f"{self.start.strftime('%a %b %d')} - {self.end.strftime('%a %b %d, %Y')}"
        return f"{self.start.strftime('%a %b %d, %Y')} - {self.end.strftime('%a %b %d, %Y')}"


def date_range(start: dt.date, end: dt.date) -> Iterable[dt.date]:
    current = start
    while current <= end:
        yield current
        current += dt.timedelta(days=1)


def build_days(
    start: dt.date,
    end: dt.date,
    holidays: Sequence[ResolvedHoliday],
    workweek: Optional[Set[int]] = None,
    blackout: Optional[Set[dt.date]] = None,
) -> List[Day]:
    """Lay out every date in ``[start, end]`` as a working, weekend or holiday day."""
    workweek = DEFAULT_WORKWEEK if workweek is None else workweek
    blackout = blackout or set()

    by_date: Dict[dt.date, str] = {}
    for holiday in holidays:
        if start <= holiday.observed <= end:
            # Two holidays can share a day only if observance shifting was
            # disabled; keep both names so the report stays truthful.
            existing = by_date.get(holiday.observed)
            by_date[holiday.observed] = (
                f"{existing} / {holiday.name}" if existing else holiday.name
            )

    days: List[Day] = []
    for date in date_range(start, end):
        name = by_date.get(date)
        if name is not None:
            kind = DayKind.HOLIDAY
        elif date.weekday() in workweek:
            kind = DayKind.WORK
        else:
            kind = DayKind.WEEKEND
        days.append(Day(date=date, kind=kind, holiday=name, blackout=date in blackout))
    return days


def find_breaks(days: Sequence[Day], min_length: int = 3) -> List[Break]:
    """Group ``days`` into maximal runs of days off at least ``min_length`` long.

    Runs at the very start or end of the window are included as-is; they may be
    clipped by the window rather than by a working day.
    """
    breaks: List[Break] = []
    current: List[Day] = []
    for day in days:
        if day.is_off:
            current.append(day)
            continue
        if len(current) >= min_length:
            breaks.append(Break(start=current[0].date, end=current[-1].date, days=list(current)))
        current = []
    if len(current) >= min_length:
        breaks.append(Break(start=current[0].date, end=current[-1].date, days=list(current)))
    return breaks


@dataclass
class Opportunity:
    """What a single holiday is worth, and the cheapest way to make it better."""

    holiday: str
    date: dt.date
    natural_length: int
    natural_start: dt.date
    natural_end: dt.date
    bridge_cost: int = 0
    bridge_dates: List[dt.date] = field(default_factory=list)
    bridged_length: int = 0
    bridged_start: Optional[dt.date] = None
    bridged_end: Optional[dt.date] = None

    @property
    def worth_bridging(self) -> bool:
        return self.bridge_cost > 0 and self.bridged_length > self.natural_length

    def advice(self) -> str:
        if self.worth_bridging:
            taken = ", ".join(d.strftime("%a %b %d") for d in self.bridge_dates)
            plural = "day" if self.bridge_cost == 1 else "days"
            return (
                f"take {self.bridge_cost} {plural} off ({taken}) "
                f"for a {self.bridged_length}-day break"
            )
        if self.natural_length >= 3:
            return f"{self.natural_length}-day break already, no PTO needed"
        return f"{self.natural_length}-day break; nothing nearby to bridge to"


def find_opportunities(
    days: Sequence[Day], target: int = 4, max_bridge: int = 4
) -> List[Opportunity]:
    """For every holiday in ``days``, report its natural break and best bridge.

    A "bridge" is a run of working days taken as PTO to join the holiday to
    adjacent days off. The cheapest bridge reaching ``target`` consecutive days
    wins; if none reaches it, the best available improvement is reported.
    """
    index_by_date = {day.date: i for i, day in enumerate(days)}
    opportunities: List[Opportunity] = []
    for index, day in enumerate(days):
        if day.kind is not DayKind.HOLIDAY:
            continue
        low, high = _expand(days, index, index)
        best: Optional[Tuple[int, List[dt.date], int, int]] = None
        best_key: Optional[Tuple[bool, int, int]] = None
        for cost in range(1, max_bridge + 1):
            for left in range(cost + 1):
                candidate = _bridge(days, low, high, left, cost - left)
                if candidate is None:
                    continue
                length, dates, start, end = candidate
                if length <= high - low + 1:
                    continue
                # Prefer reaching the target, then spending less, then going longer.
                key = (length >= target, -cost, length)
                if best_key is None or key > best_key:
                    best, best_key = (length, dates, start, end), key
            if best is not None and best[0] >= target:
                break  # cheapest bridge that reaches the target wins

        opportunity = Opportunity(
            holiday=day.holiday or "Holiday",
            date=day.date,
            natural_length=high - low + 1,
            natural_start=days[low].date,
            natural_end=days[high].date,
        )
        if best is not None and best[0] > opportunity.natural_length:
            length, dates, start, end = best
            opportunity.bridge_cost = len(dates)
            opportunity.bridge_dates = dates
            opportunity.bridged_length = length
            opportunity.bridged_start = days[start].date
            opportunity.bridged_end = days[end].date
        opportunities.append(opportunity)
    return opportunities


def _expand(days: Sequence[Day], low: int, high: int, extra: Optional[Set[int]] = None) -> Tuple[int, int]:
    extra = extra or set()

    def off(i: int) -> bool:
        return days[i].is_off or i in extra

    while low - 1 >= 0 and off(low - 1):
        low -= 1
    while high + 1 < len(days) and off(high + 1):
        high += 1
    return low, high


def _bridge(
    days: Sequence[Day], low: int, high: int, left: int, right: int
) -> Optional[Tuple[int, List[dt.date], int, int]]:
    """Take ``left`` working days before the run and ``right`` after it."""
    extra: Set[int] = set()
    cursor = low - 1
    for _ in range(left):
        if cursor < 0 or days[cursor].kind is not DayKind.WORK or days[cursor].blackout:
            return None
        extra.add(cursor)
        cursor -= 1
    cursor = high + 1
    for _ in range(right):
        if cursor >= len(days) or days[cursor].kind is not DayKind.WORK or days[cursor].blackout:
            return None
        extra.add(cursor)
        cursor += 1
    if not extra:
        return None
    start, end = _expand(days, min(low, *extra), max(high, *extra), extra)
    return end - start + 1, sorted(days[i].date for i in extra), start, end

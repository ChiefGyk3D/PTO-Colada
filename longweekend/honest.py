"""Honest counting of days off.

A claim like "17 PTO days give 55 days off" is only worth something if the 55
can be reproduced from the rules. This module is those rules, as plain
calendar-date arithmetic on :class:`datetime.date` (no clocks, no time zones).

Rules
-----
* A *break* is a maximal run of consecutive nonworking days: weekend days,
  holidays and PTO. Breaks that touch or overlap merge into one.
* PTO is charged only on a scheduled workday that is not a holiday. A request
  for a weekend or a holiday costs nothing and is listed as uncharged; it does
  not make a break featured on its own.
* The *featured* total counts only breaks that contain at least one charged
  PTO day, and each calendar date once. Breaks that cost no PTO (a plain
  holiday weekend) are reported separately and never inflate the headline.
* A day is classified once: charged PTO, else a holiday on a workday, else a
  weekend day. A holiday that lands on a weekend therefore costs nothing and
  is not counted twice. Two holidays on one date count once.
* The window reaches one calendar year past the years you select, on both
  sides, so a break crossing December 31 is whole.
* Budgets are tracked per calendar year: PTO is charged to the year of the
  date it is taken.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Dict, Iterable, List, Mapping, Optional, Sequence, Set, Tuple, Union

from .catalog import DEFAULT_WORKWEEK

__all__ = [
    "AnnualRow",
    "Bridge",
    "BudgetRow",
    "HonestBreak",
    "HonestReport",
    "score_pto",
    "thanksgiving_bridge",
]

HolidayInput = Union[Mapping[dt.date, str], Iterable[object]]


@dataclass(frozen=True)
class HonestBreak:
    start: dt.date
    end: dt.date
    dates: Tuple[dt.date, ...]
    pto_dates: Tuple[dt.date, ...]
    holiday_dates: Tuple[dt.date, ...]
    holiday_names: Tuple[str, ...]
    weekend_days: int

    @property
    def length(self) -> int:
        return len(self.dates)

    @property
    def pto_cost(self) -> int:
        return len(self.pto_dates)


@dataclass(frozen=True)
class AnnualRow:
    """Every nonworking day of one calendar year, whatever PTO does."""

    year: int
    weekend_days: int   #: nonworking days of the workweek, weekend holidays included
    holiday_days: int   #: holidays that fall on a scheduled workday

    @property
    def total(self) -> int:
        return self.weekend_days + self.holiday_days


@dataclass(frozen=True)
class BudgetRow:
    year: int
    used: int
    granted: Optional[int] = None

    @property
    def remaining(self) -> Optional[int]:
        return None if self.granted is None else self.granted - self.used

    @property
    def over(self) -> int:
        return 0 if self.granted is None else max(0, self.used - self.granted)


@dataclass(frozen=True)
class Bridge:
    """Thanksgiving break joined to the Christmas / New Year break."""

    first: HonestBreak
    second: HonestBreak

    @property
    def pto(self) -> int:
        return self.first.pto_cost + self.second.pto_cost

    @property
    def days(self) -> int:
        return self.first.length + self.second.length


@dataclass
class HonestReport:
    years: Tuple[int, ...]
    breaks: List[HonestBreak]
    pto_requested: List[dt.date]
    pto_charged: List[dt.date]
    pto_uncharged: List[Tuple[dt.date, str]]
    free_breaks: List[HonestBreak]      #: breaks of 3+ days that cost no PTO
    annual: Dict[int, AnnualRow]
    budget_rows: List[BudgetRow]
    weekend_holidays: List[dt.date]
    days_by_year: Dict[int, int]
    pto_by_year: Dict[int, int]
    holidays_included: int
    weekend_days_included: int

    @property
    def pto_used(self) -> int:
        return len(self.pto_charged)

    @property
    def breaks_count(self) -> int:
        return len(self.breaks)

    @property
    def free_breaks_count(self) -> int:
        return len(self.free_breaks)

    @property
    def total_days(self) -> int:
        return sum(b.length for b in self.breaks)

    @property
    def longest(self) -> int:
        return max((b.length for b in self.breaks), default=0)

    @property
    def days_in_years(self) -> int:
        return sum(n for y, n in self.days_by_year.items() if y in self.years)

    @property
    def days_adjacent(self) -> int:
        return self.total_days - self.days_in_years


def _holiday_names(holidays: HolidayInput) -> Dict[dt.date, List[str]]:
    names: Dict[dt.date, List[str]] = {}

    def add(date: dt.date, name: str) -> None:
        bucket = names.setdefault(date, [])
        for part in str(name).split(" / "):
            if part not in bucket:
                bucket.append(part)

    if isinstance(holidays, Mapping):
        for date, name in holidays.items():
            add(date, name)
    else:
        for item in holidays:
            if isinstance(item, tuple):
                add(item[0], item[1])
            else:
                add(item, "Holiday")  # type: ignore[arg-type]
    return names


def score_pto(
    pto: Iterable[dt.date],
    holidays: HolidayInput,
    workweek: Optional[Set[int]] = None,
    years: Optional[Sequence[int]] = None,
    budgets: Optional[Mapping[int, int]] = None,
) -> HonestReport:
    """Score a set of requested PTO dates under the rules in the module docstring.

    ``holidays`` maps observed dates to names (or is any iterable of dates or
    ``(date, name)`` pairs); ``years`` are the selected years for the
    inside-versus-adjacent split, defaulting to the years PTO is taken in.
    """
    workweek = set(DEFAULT_WORKWEEK) if workweek is None else set(workweek)
    names = _holiday_names(holidays)
    requested = sorted(set(pto))

    def is_workday(date: dt.date) -> bool:
        return date.weekday() in workweek

    charged: List[dt.date] = []
    uncharged: List[Tuple[dt.date, str]] = []
    for date in requested:
        if not is_workday(date):
            uncharged.append((date, "weekend"))
        elif date in names:
            uncharged.append((date, "holiday"))
        else:
            charged.append(date)
    charged_set = set(charged)

    selected = tuple(sorted(set(years))) if years else tuple(sorted({d.year for d in requested}))
    span = set(selected) | {d.year for d in requested}
    if not span:
        span = {dt.date.today().year}
    low = dt.date(min(span) - 1, 1, 1)
    high = dt.date(max(span) + 1, 12, 31)

    runs: List[List[dt.date]] = []
    current: List[dt.date] = []
    day = low
    one = dt.timedelta(days=1)
    while day <= high:
        if day in charged_set or day in names or not is_workday(day):
            current.append(day)
        elif current:
            runs.append(current)
            current = []
        day += one
    if current:
        runs.append(current)

    featured: List[HonestBreak] = []
    free: List[HonestBreak] = []
    for run in runs:
        item = _make_break(run, charged_set, names, is_workday)
        if item.pto_cost:
            featured.append(item)
        elif item.length >= 3 and any(d.year in selected for d in run):
            free.append(item)

    days_by_year: Dict[int, int] = {}
    holiday_total = weekend_total = 0
    for item in featured:
        holiday_total += len(item.holiday_dates)
        weekend_total += item.weekend_days
        for date in item.dates:
            days_by_year[date.year] = days_by_year.get(date.year, 0) + 1

    pto_by_year: Dict[int, int] = {}
    for date in charged:
        pto_by_year[date.year] = pto_by_year.get(date.year, 0) + 1

    annual = {y: _annual(y, names, is_workday) for y in selected}
    weekend_holidays = sorted(
        d for d in names if not is_workday(d) and d.year in selected
    )

    budgets = dict(budgets or {})
    rows = [
        BudgetRow(year=y, used=pto_by_year.get(y, 0), granted=budgets.get(y))
        for y in sorted(set(budgets) | set(pto_by_year))
    ]
    return HonestReport(
        years=selected,
        breaks=featured,
        pto_requested=requested,
        pto_charged=charged,
        pto_uncharged=uncharged,
        free_breaks=free,
        annual=annual,
        budget_rows=rows,
        weekend_holidays=weekend_holidays,
        days_by_year=dict(sorted(days_by_year.items())),
        pto_by_year=dict(sorted(pto_by_year.items())),
        holidays_included=holiday_total,
        weekend_days_included=weekend_total,
    )


def _make_break(run, charged_set, names, is_workday) -> HonestBreak:
    pto_dates = tuple(d for d in run if d in charged_set)
    holiday_dates = tuple(d for d in run if d not in charged_set and d in names and is_workday(d))
    weekend = sum(1 for d in run if d not in charged_set and not is_workday(d))
    seen: List[str] = []
    for date in run:
        for name in names.get(date, ()):
            if name not in seen:
                seen.append(name)
    return HonestBreak(
        start=run[0],
        end=run[-1],
        dates=tuple(run),
        pto_dates=pto_dates,
        holiday_dates=holiday_dates,
        holiday_names=tuple(seen),
        weekend_days=weekend,
    )


def _annual(year: int, names, is_workday) -> AnnualRow:
    weekend = holiday = 0
    day = dt.date(year, 1, 1)
    one = dt.timedelta(days=1)
    while day.year == year:
        if not is_workday(day):
            weekend += 1
        elif day in names:
            holiday += 1
        day += one
    return AnnualRow(year=year, weekend_days=weekend, holiday_days=holiday)


def thanksgiving_bridge(report: HonestReport) -> Optional[Bridge]:
    """The Thanksgiving break and the Christmas / New Year break after it, if both are featured."""
    for index, first in enumerate(report.breaks):
        if not any("thanksgiving" in n.lower() for n in first.holiday_names):
            continue
        for second in report.breaks[index + 1:]:
            if second.start.year != first.end.year:
                continue
            if any("christmas" in n.lower() for n in second.holiday_names):
                return Bridge(first=first, second=second)
    return None

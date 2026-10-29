"""Planning from today, in hours: balance, January refill, rollover and rest.

The classic planner works on whole calendar years and whole-day budgets. This
module is the layer for "it's November, I have 36 hours left, I want a break
in March, and the bank refills on January 1": it converts hours to the days the
planner spends, builds the per-year budget, and reports what the plan leaves
behind. The search itself is :func:`longweekend.planner.optimize_pto`.

Rules
-----
* **Hours to days.** Whole days are planned: ``days = floor(hours / hours per
  day)``. The remainder is reported as leftover hours and is never planned
  (half-day support is a follow-up). The same rule applies to the refill.
* **Today.** The window starts on the given day and runs to ``--through`` or
  ``--months`` months later (default: December 31 of today's year). Days
  before today are never planned.
* **Refill.** Each January 1 inside the window grants the refill. The balance
  you enter is this year's whole remaining allowance.
* **Rollover.** At year end, unspent days carry into the next year up to the
  carryover setting (in days), and never more than that year's grant, which is
  the planner's existing rule. Whatever is left beyond that is use-it-or-lose-it
  and is reported for every year end inside the window.
* **Rest.** A *rest break* is three or more consecutive days off (a long
  weekend counts, holidays and PTO included). A *working stretch* is the
  working days between two rest breaks, counted from the first day of the
  window to the first break and from the last break to the end of the window.
  ``longest`` is the largest stretch; ``average gap`` is the mean stretch
  between two rest breaks (needs at least two).
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Dict, List, Mapping, Optional, Sequence, Tuple

from .config import _bounded_date
from .planner import REST_BREAK_DAYS, PtoPlan
from .schedule import Day, DayKind

__all__ = [
    "MAX_HOURS",
    "MAX_HOURS_PER_DAY",
    "MAX_MONTHS",
    "MAX_WORK_STREAK",
    "HorizonSpec",
    "RestMetrics",
    "YearEnd",
    "add_months",
    "build_horizon",
    "describe_horizon",
    "horizon_json",
    "hours_to_days",
    "parse_hours",
    "rest_metrics",
    "year_ends",
]

#: Largest hours figure accepted for a balance or a refill (a few lifetimes of leave).
MAX_HOURS = Decimal(100000)
#: A workday cannot be longer than the day.
MAX_HOURS_PER_DAY = Decimal(24)
#: Longest look-ahead in months; the planner is exact, so the horizon is bounded.
MAX_MONTHS = 60
#: Largest ``--max-work-streak`` that can mean anything inside the longest horizon.
MAX_WORK_STREAK = 3660


def parse_hours(value: object, option: str) -> Decimal:
    """A finite, non-negative number of hours with at most two decimals, capped at ``MAX_HOURS``."""
    text = str(value).strip()
    try:
        hours = Decimal(text)
    except InvalidOperation:
        raise ValueError(f"{option}: expected a number of hours, got {text[:40]!r}") from None
    if not hours.is_finite():
        raise ValueError(f"{option}: expected a finite number of hours, got {text[:40]!r}")
    if hours < 0:
        raise ValueError(f"{option} cannot be negative (got {text[:40]})")
    if hours > MAX_HOURS:
        raise ValueError(f"{option}: {text[:40]} is more than the {MAX_HOURS} hour limit")
    if hours != hours.quantize(Decimal("0.01")):
        raise ValueError(f"{option}: use at most two decimal places (got {text[:40]})")
    return hours


def hours_to_days(hours: Decimal, hours_per_day: Decimal) -> Tuple[int, Decimal]:
    """Whole days plannable from ``hours``, and the leftover hours (always less than one day)."""
    if hours_per_day <= 0:
        raise ValueError("hours per day must be above zero")
    days = int(hours // hours_per_day)
    return days, hours - days * hours_per_day


def _fmt(hours: Decimal) -> str:
    return format(hours.normalize(), "f")


@dataclass(frozen=True)
class HorizonSpec:
    today: dt.date
    end: dt.date
    hours_per_day: Decimal
    balance_hours: Decimal
    balance_days: int
    balance_leftover: Decimal
    refill_hours: Decimal
    refill_days: int
    refill_leftover: Decimal
    carryover_days: int
    max_work_streak: Optional[int]

    @property
    def carryover_hours(self) -> Decimal:
        return self.carryover_days * self.hours_per_day


def add_months(day: dt.date, months: int) -> dt.date:
    """``day`` plus whole months, clamped to the target month's last day."""
    year, month0 = divmod(day.year * 12 + (day.month - 1) + months, 12)
    month = month0 + 1
    following = dt.date(year + (month == 12), month % 12 + 1, 1)
    last = (following - dt.timedelta(days=1)).day
    return dt.date(year, month, min(day.day, last))


def build_horizon(
    today: dt.date,
    pto_hours: object,
    hours_per_day: object = 8,
    refill_hours: object = 0,
    through: Optional[str] = None,
    months: Optional[int] = None,
    carryover_days: int = 0,
    max_work_streak: Optional[int] = None,
) -> HorizonSpec:
    """Validate the horizon options and turn them into days. Raises ``ValueError``."""
    per_day = parse_hours(hours_per_day, "--hours-per-day")
    if per_day <= 0 or per_day > MAX_HOURS_PER_DAY:
        raise ValueError(f"--hours-per-day must be above 0 and at most {MAX_HOURS_PER_DAY}")
    balance = parse_hours(pto_hours, "--pto-hours")
    refill = parse_hours(refill_hours, "--refill-hours")
    if through is not None and months is not None:
        raise ValueError("use --through or --months, not both")
    if months is not None:
        if not 1 <= months <= MAX_MONTHS:
            raise ValueError(f"--months must be between 1 and {MAX_MONTHS}")
        end = add_months(today, months)
    elif through is not None:
        end = _bounded_date(through, "--through")
    else:
        end = dt.date(today.year, 12, 31)
    if end < today:
        raise ValueError(f"--through ({end.isoformat()}) is before today ({today.isoformat()})")
    if end > add_months(today, MAX_MONTHS):
        raise ValueError(f"the horizon cannot run more than {MAX_MONTHS} months past today")
    if max_work_streak is not None and not 1 <= max_work_streak <= MAX_WORK_STREAK:
        raise ValueError(f"--max-work-streak must be between 1 and {MAX_WORK_STREAK}")
    balance_days, balance_left = hours_to_days(balance, per_day)
    refill_days, refill_left = hours_to_days(refill, per_day)
    return HorizonSpec(
        today=today, end=end, hours_per_day=per_day,
        balance_hours=balance, balance_days=balance_days, balance_leftover=balance_left,
        refill_hours=refill, refill_days=refill_days, refill_leftover=refill_left,
        carryover_days=carryover_days, max_work_streak=max_work_streak,
    )


@dataclass(frozen=True)
class RestMetrics:
    longest: int                       #: working days in the longest stretch
    longest_start: Optional[dt.date]
    longest_end: Optional[dt.date]
    rest_breaks: int                   #: runs of REST_BREAK_DAYS or more days off
    average_gap: Optional[float]       #: mean working days between two rest breaks


def rest_metrics(days: Sequence[Day]) -> RestMetrics:
    """Rest metrics for a planned window; see the module docstring for the definitions."""
    # A stretch is (working days, first working date, last working date).
    stretches: List[Tuple[int, Optional[dt.date], Optional[dt.date]]] = []
    interior: List[int] = []
    count = 0
    first: Optional[dt.date] = None
    last: Optional[dt.date] = None
    off_run = 0
    breaks = 0

    for day in days:
        if day.kind is DayKind.WORK:
            off_run = 0
            count += 1
            first = first or day.date
            last = day.date
            continue
        off_run += 1
        if off_run == REST_BREAK_DAYS:
            if breaks:
                interior.append(count)
            breaks += 1
            stretches.append((count, first, last))
            count, first, last = 0, None, None
    stretches.append((count, first, last))

    best = max(stretches, key=lambda s: s[0])
    average = round(sum(interior) / len(interior), 1) if interior else None
    return RestMetrics(
        longest=best[0],
        longest_start=best[1] if best[0] else None,
        longest_end=best[2] if best[0] else None,
        rest_breaks=breaks,
        average_gap=average,
    )


@dataclass(frozen=True)
class YearEnd:
    year: int
    available: int      #: grant plus days rolled in
    spent: int
    unspent: int
    carried: int
    lost: int           #: use-it-or-lose-it, in days


def year_ends(plan: PtoPlan, rolled: Mapping[int, int], end: dt.date) -> List[YearEnd]:
    """What each December 31 inside the window leaves behind.

    ``rolled`` maps a year to the days it receives from the one before (the
    CLI computes it with the planner's own rollover rule) and must include the
    year after the last year-end being reported.
    """
    rows: List[YearEnd] = []
    for year in sorted(plan.granted_by_year):
        if dt.date(year, 12, 31) > end:
            continue
        available = plan.granted_total(year) + rolled.get(year, 0)
        spent = plan.spent_by_year.get(year, 0)
        unspent = max(0, available - spent)
        carried = rolled.get(year + 1, 0)
        rows.append(YearEnd(year, available, spent, unspent, carried, max(0, unspent - carried)))
    return rows


def _longest_line(metrics: RestMetrics) -> str:
    if metrics.longest_start is None or metrics.longest_end is None:
        return "    Longest working stretch: 0 working day(s)"
    return (
        f"    Longest working stretch: {metrics.longest} working day(s), "
        f"{metrics.longest_start.strftime('%b %d, %Y')} to "
        f"{metrics.longest_end.strftime('%b %d, %Y')}"
    )


def describe_horizon(
    spec: HorizonSpec,
    metrics: RestMetrics,
    ends: Sequence[YearEnd],
    granted: Mapping[int, int],
) -> List[str]:
    """Plain-text block: what was assumed, then use-it-or-lose-it and rest."""
    hpd = spec.hours_per_day
    left = (
        f", {_fmt(spec.balance_leftover)} hour(s) left over and not planned"
        if spec.balance_leftover else ""
    )
    lines = [
        "Planning from today",
        "-" * 70,
        f"  Window: {spec.today.isoformat()} to {spec.end.isoformat()} "
        "(days before today are never planned)",
        "  Assumptions:",
        f"    - a workday is {_fmt(hpd)} hours; whole days are planned (hours / day, rounded down)",
        f"    - balance today: {_fmt(spec.balance_hours)} hours = "
        f"{spec.balance_days} day(s) planned{left}",
    ]
    if spec.refill_hours:
        refill_left = (
            f", {_fmt(spec.refill_leftover)} hour(s) left over and not planned"
            if spec.refill_leftover else ""
        )
        if spec.end.year > spec.today.year:
            lines.append(
                f"    - refill on each January 1 inside the window: "
                f"{_fmt(spec.refill_hours)} hours = {spec.refill_days} day(s){refill_left}"
            )
        else:
            lines.append(
                f"    - refill of {_fmt(spec.refill_hours)} hours not used: the window "
                "has no January 1 after today"
            )
        if (spec.today.month, spec.today.day) == (1, 1):
            lines.append(
                "    - today is January 1: that day's refill is not added; the balance "
                "is this year's allowance"
            )
    else:
        lines.append("    - no refill on January 1 (add --refill-hours to set one)")
    if spec.carryover_days:
        lines.append(
            f"    - carryover: up to {spec.carryover_days} day(s) "
            f"({_fmt(spec.carryover_hours)} hours) roll into the next year, never more than "
            "that year's grant"
        )
    else:
        lines.append("    - no carryover: unspent days are lost on December 31 (--carryover sets one)")
    lines.append(
        "    - days granted per year used by the plan: "
        + ", ".join(f"{y}: {n}" for y, n in sorted(granted.items()))
    )
    lines.append("")
    if ends:
        lines.append("  Year end")
        for row in ends:
            lines.append(
                f"    {row.year}: {row.unspent} day(s) unspent of {row.available}; "
                f"{row.carried} carry over ({_fmt(row.carried * hpd)} hours); "
                f"use-it-or-lose-it: {_fmt(row.lost * hpd)} hours ({row.lost} day(s))"
            )
        lines.append("")
    lines.append("  Rest")
    lines.append(_longest_line(metrics))
    gap = (
        f"{metrics.average_gap:.1f} working day(s)"
        if metrics.average_gap is not None else "n/a (needs two rest breaks)"
    )
    lines.append(f"    Average gap between breaks: {gap}")
    lines.append(
        f"    Rest breaks counted: {metrics.rest_breaks} (a break is {REST_BREAK_DAYS}+ days off "
        "in a row, holidays and weekends included)"
    )
    if spec.max_work_streak is not None:
        lines.append(f"    Limit --max-work-streak {spec.max_work_streak}: met")
    return lines


def horizon_json(
    spec: HorizonSpec,
    metrics: RestMetrics,
    ends: Sequence[YearEnd],
    granted: Mapping[int, int],
) -> Dict[str, object]:
    return {
        "today": spec.today.isoformat(),
        "through": spec.end.isoformat(),
        "assumptions": {
            "hours_per_day": float(spec.hours_per_day),
            "rounding": "whole days planned (hours / hours per day, rounded down)",
            "balance_hours": float(spec.balance_hours),
            "balance_days": spec.balance_days,
            "balance_leftover_hours": float(spec.balance_leftover),
            "refill_hours": float(spec.refill_hours),
            "refill_days": spec.refill_days,
            "refill_leftover_hours": float(spec.refill_leftover),
            "carryover_days": spec.carryover_days,
            "carryover_hours": float(spec.carryover_hours),
            "granted_days_by_year": {str(y): n for y, n in sorted(granted.items())},
        },
        "year_ends": [
            {
                "year": r.year,
                "available_days": r.available,
                "spent_days": r.spent,
                "unspent_days": r.unspent,
                "carried_days": r.carried,
                "lost_days": r.lost,
                "lost_hours": float(r.lost * spec.hours_per_day),
            }
            for r in ends
        ],
        "rest": {
            "longest_working_stretch": metrics.longest,
            "longest_start": metrics.longest_start.isoformat() if metrics.longest_start else None,
            "longest_end": metrics.longest_end.isoformat() if metrics.longest_end else None,
            "rest_breaks": metrics.rest_breaks,
            "average_gap_working_days": metrics.average_gap,
            "max_work_streak": spec.max_work_streak,
        },
    }

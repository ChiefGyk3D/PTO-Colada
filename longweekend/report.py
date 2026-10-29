"""Rendering: human-readable text, JSON, and iCalendar output."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from dataclasses import dataclass, field

from .catalog import WEEKDAY_NAMES, Observance, ResolvedHoliday
from .config import CompanyConfig
from .efficiency import ReturnCurve
from .goals import PREFER_BONUS, GoalSpec
from .honest import HonestBreak, HonestReport, thanksgiving_bridge
from .planner import PtoPlan
from .schedule import Break, Day, DayKind, Opportunity

__all__ = [
    "GoalSummary",
    "render_text",
    "render_json",
    "render_ics",
    "provisional_assumptions",
]

_RULE = "-" * 72

_STATUS_NOTE = {
    "assumed": "assumed to be a paid day, not confirmed",
    "observed-unconfirmed": "holiday is paid, observed date not confirmed",
}


@dataclass
class GoalSummary:
    """Everything the goal-mode output needs besides the plan itself."""

    goal: str
    spec: GoalSpec
    method: str
    report: HonestReport
    #: Every goal planned under the same constraints, in a fixed order.
    comparison: List[Tuple[str, HonestReport]]
    min_vacation_days: Optional[int] = None
    max_breaks: Optional[int] = None
    prefer: List[dt.date] = field(default_factory=list)
    blackout_count: int = 0
    exact_breaks_note: str = ""
    #: Holidays outside the selected window that the plan depends on (padding or featured breaks).
    context_holidays: List[ResolvedHoliday] = field(default_factory=list)


def provisional_assumptions(
    holidays_by_year: Dict[int, List[ResolvedHoliday]],
    extra: Sequence[ResolvedHoliday] = (),
) -> List[ResolvedHoliday]:
    """Holidays whose date is not confirmed and that the result can depend on.

    That is every unconfirmed holiday in the selected window, plus any in
    ``extra`` (holidays in the padding around the window or inside a featured
    break, which the goal planner looks at). A date that also carries a
    confirmed holiday is off for certain, so it is not listed.
    """
    seen = set()
    pool: List[ResolvedHoliday] = []
    for holiday in [h for year in sorted(holidays_by_year) for h in holidays_by_year[year]] + list(extra):
        key = (holiday.name, holiday.observed)
        if key not in seen:
            seen.add(key)
            pool.append(holiday)
    certain = {h.observed for h in pool if not h.unconfirmed}
    found = [h for h in pool if h.unconfirmed and h.observed not in certain]
    return sorted(found, key=lambda h: h.observed)


def _provisional_text(assumptions: List[ResolvedHoliday]) -> List[str]:
    if not assumptions:
        return []
    lines = [
        "PROVISIONAL: this result depends on days that are not confirmed. "
        "Confirm them with the employer",
        "and rerun; a changed date can change every number below.",
        "  Unconfirmed:",
    ]
    for h in assumptions:
        lines.append(
            f"    {h.observed.strftime('%a %b %d, %Y')}  {h.name}: {_STATUS_NOTE.get(h.status or '', h.status)}"
        )
    lines.append("")
    return lines


# --------------------------------------------------------------------------- text


def render_text(
    config: CompanyConfig,
    holidays_by_year: Dict[int, List[ResolvedHoliday]],
    opportunities_by_year: Dict[int, List[Opportunity]],
    plan: Optional[PtoPlan] = None,
    show_holidays: bool = True,
    efficiency: bool = False,
    curve: Optional[ReturnCurve] = None,
    objective: Optional[Dict[str, Any]] = None,
    goal: Optional[GoalSummary] = None,
) -> str:
    lines: List[str] = []
    lines.append(f"{config.name} - {_describe_config(config)}")
    lines.append("")
    lines.extend(_provisional_text(provisional_assumptions(holidays_by_year, goal.context_holidays if goal else ())))
    if goal is not None:
        lines.extend(_render_goal(goal, plan))
        if show_holidays:
            for year in sorted(holidays_by_year):
                lines.extend(_holiday_listing(year, holidays_by_year[year]))
        return "\n".join(lines).rstrip() + "\n"
    if plan is not None:
        lines.extend(_render_headline(plan, config))

    for year in sorted(holidays_by_year):
        header = f"{year}"
        if plan is not None and year in plan.granted_by_year:
            floating = plan.floating_by_year.get(year, 0)
            header += f"   {plan.granted_by_year[year]} PTO day(s)"
            if floating:
                header += f" + {floating} floating holiday(s)"
            header += " available"
        lines.append(header)
        lines.append(_RULE)

        if show_holidays:
            lines.append("  Holidays")
            for holiday in holidays_by_year[year]:
                note = ""
                if holiday.shifted:
                    note = f"   (falls on {holiday.actual.strftime('%a %b %d')}, observed here)"
                if holiday.unconfirmed:
                    note += f"   [{holiday.status}]"
                lines.append(f"    {holiday.observed.strftime('%a %b %d')}  {holiday.name}{note}")
            lines.append("")

        if plan is None:
            lines.extend(_render_observances(config, None, year))
            lines.append(f"  Long weekends and how to extend them (target {config.min_break} days)")
            for opportunity in opportunities_by_year.get(year, []):
                lines.append(
                    f"    {opportunity.date.strftime('%a %b %d')}  "
                    f"{opportunity.holiday}: {opportunity.advice()}"
                )
            lines.append("")
        else:
            lines.extend(_render_plan_year(plan, year, config, efficiency))

    if plan is not None:
        lines.extend(_render_totals(plan, labeled=efficiency))
        if efficiency:
            lines.extend(_render_efficiency(plan, objective))
        if curve is not None:
            lines.extend(_render_curve(curve))
    return "\n".join(lines).rstrip() + "\n"


def _holiday_listing(year: int, holidays: List[ResolvedHoliday]) -> List[str]:
    lines = [f"Holidays {year}", _RULE]
    for holiday in holidays:
        note = ""
        if holiday.shifted:
            note = f"   (falls on {holiday.actual.strftime('%a %b %d')}, observed here)"
        if holiday.unconfirmed:
            note += f"   [{holiday.status}]"
        lines.append(f"  {holiday.observed.strftime('%a %b %d')}  {holiday.name}{note}")
    lines.append("")
    return lines


_GOAL_BLURB = {
    "weekends": "frequent long weekends",
    "vacations": "fewer, longer vacations",
    "balanced": "a mix of long weekends and vacations",
}


def _span(item: HonestBreak) -> str:
    if item.start.year == item.end.year:
        return f"{item.start.strftime('%a %b %d')} - {item.end.strftime('%a %b %d, %Y')}"
    return f"{item.start.strftime('%a %b %d, %Y')} - {item.end.strftime('%a %b %d, %Y')}"


def _plural(n: int, word: str) -> str:
    return f"{n} {word}" if n == 1 else f"{n} {word}s"


def _render_goal(goal: GoalSummary, plan: Optional[PtoPlan]) -> List[str]:
    report = goal.report
    lines: List[str] = []
    lines.append(f"Goal: {goal.goal} - {_GOAL_BLURB[goal.goal]}   (method: {goal.method})")
    scoring = " and ".join(
        f"{w} point{'s' if w != 1 else ''} per day in a PTO break of {t}+ days"
        for t, w in goal.spec.thresholds
    )
    lines.append(f"  Scoring: {scoring}.")
    options = []
    if goal.min_vacation_days is not None:
        options.append(f"vacation minimum {goal.min_vacation_days} days")
    if goal.max_breaks is not None:
        options.append(f"at most {goal.max_breaks} PTO break(s)")
    if goal.prefer:
        options.append(f"{len(goal.prefer)} preferred date(s), bonus {PREFER_BONUS} point(s) each when taken")
    if goal.blackout_count:
        options.append(f"{goal.blackout_count} blackout date(s)")
    if options:
        lines.append("  Options: " + "; ".join(options) + ".")
    lines.append("")

    lines.append("Honest count (breaks that contain at least one PTO day; each calendar date once)")
    lines.append(_RULE)
    lines.append(
        f"  {report.total_days} days off = {report.pto_used} PTO + "
        f"{report.holidays_included} holidays + {report.weekend_days_included} weekend days"
    )
    lines.append(
        f"  {_plural(report.breaks_count, 'break')}, longest {report.longest} days"
    )
    years = ", ".join(str(y) for y in report.years)
    other = {y: n for y, n in report.days_by_year.items() if y not in report.years}
    detail = (
        " (" + ", ".join(f"{n} in {y}" for y, n in sorted(other.items())) + ")" if other else ""
    )
    lines.append(
        f"  {report.days_in_years} days inside {years}, "
        f"{report.days_adjacent} in adjacent years{detail}"
    )
    if report.free_breaks_count:
        lines.append(
            f"  Not counted: {report.free_breaks_count} other holiday break(s) of 3+ days "
            "that cost no PTO"
        )
    if report.pto_uncharged:
        lines.append("  Requested days that cost nothing (not counted as PTO):")
        for date, why in report.pto_uncharged:
            lines.append(f"    {date.strftime('%a %b %d, %Y')}  already a {why}")
    lines.append("")

    lines.append("Breaks")
    lines.append(_RULE)
    for item in report.breaks:
        taken = ", ".join(d.strftime("%b %d") for d in item.pto_dates)
        lines.append(f"  {item.length:2d} days  {_span(item):<36} {item.pto_cost:>2} PTO  take {taken}")
        if item.holiday_names:
            lines.append(f"{'':>12}covers {'; '.join(item.holiday_names)}")
    if not report.breaks:
        lines.append("  (none)")
    lines.append("")

    bridge = thanksgiving_bridge(report)
    if bridge is not None:
        lines.append(
            f"Thanksgiving + Christmas/New Year bridge: {bridge.pto} PTO for "
            f"{bridge.days} calendar days across two breaks"
        )
        lines.append(
            f"  {_span(bridge.first)} ({bridge.first.pto_cost} PTO) and "
            f"{_span(bridge.second)} ({bridge.second.pto_cost} PTO)"
        )
        lines.append("")

    lines.append("PTO budget per year (PTO is charged to the year it is taken)")
    lines.append(_RULE)
    for row in report.budget_rows:
        if row.granted is None:
            lines.append(f"  {row.year}: {row.used} used")
        else:
            tail = f", {row.remaining} left over" if row.remaining else ""
            if row.over:
                tail = f", {row.over} OVER BUDGET"
            carry = f" + {row.carried} carried over" if row.carried else ""
            lines.append(f"  {row.year}: {row.used} of {row.granted}{carry} used{tail}")
    lines.append("")

    lines.append("Every nonworking day of the year, shown separately (free, whatever you book)")
    lines.append(_RULE)
    for year in report.years:
        row = report.annual[year]
        lines.append(
            f"  {year}: {row.weekend_days} weekend days + {row.holiday_days} holidays "
            f"= {row.total} nonworking days"
        )
        if report.weekend_holidays:
            on_weekend = [d for d in report.weekend_holidays if d.year == year]
            if on_weekend:
                lines.append(
                    "    holiday(s) on a weekend, counted as weekend days: "
                    + ", ".join(d.strftime("%a %b %d") for d in on_weekend)
                )
    lines.append("")

    lines.extend(_render_tradeoff(goal))
    return lines


def _render_tradeoff(goal: GoalSummary) -> List[str]:
    lines = [
        "Tradeoff (same PTO, same holidays, same options; the goals reward different things)",
        _RULE,
        "  goal        breaks  days off  longest  avg break  shortest  PTO",
    ]
    rows = goal.comparison
    for name, rep in rows:
        avg = rep.total_days / rep.breaks_count if rep.breaks_count else 0.0
        shortest = min((b.length for b in rep.breaks), default=0)
        mark = "   <- your goal" if name == goal.goal else ""
        lines.append(
            f"  {name:<10} {rep.breaks_count:>7} {rep.total_days:>9} {rep.longest:>8} "
            f"{avg:>10.1f} {shortest:>9} {rep.pto_used:>4}{mark}"
        )
    biggest = max(rows, key=lambda r: r[1].total_days)
    longest = max(rows, key=lambda r: r[1].longest)
    lines.append("")
    lines.append(
        "  Many short breaks and a few long ones can cost the same PTO and still give a different "
        "headline total and a different kind of rest."
    )
    lines.append(
        f"  Here the largest total is {biggest[1].total_days} days ({biggest[0]}), "
        f"spread over {_plural(biggest[1].breaks_count, 'break')}; the longest stretch of "
        f"rest is {longest[1].longest} days ({longest[0]})."
    )
    lines.append(
        "  A bigger total is not automatically better: it may mean many 3 to 4 day breaks "
        "and no time to travel or recover. Pick the shape you want, then read the total."
    )
    lines.append("")
    return lines


def _render_headline(plan: PtoPlan, config: CompanyConfig) -> List[str]:
    """The answer, before the detail: what this plan buys you."""
    days_off = plan.total_days_off
    spent = plan.total_pto
    longest = max((b.length for b in plan.breaks), default=0)
    lines = [
        f"  You get {days_off} days off across {len(plan.breaks)} break(s) of "
        f"{plan.min_break}+ days,",
    ]
    if spent:
        lines.append(
            f"  spending {spent} day(s) of leave -- {days_off / spent:.1f} days off "
            "for every day you book."
        )
    else:
        lines.append("  without booking a single day of leave.")
    if longest:
        best = max(plan.breaks, key=lambda b: b.length)
        lines.append(f"  Longest break: {longest} days, {best.label()}.")
    lines.append("")
    return lines


def _render_observances(config: CompanyConfig, plan: Optional[PtoPlan], year: int) -> List[str]:
    """What the days you chose yourself actually cost you."""
    kinds: Dict[dt.date, DayKind] = {}
    if plan is not None:
        kinds = {day.date: day.kind for day in plan.days}

    rows: List[str] = []
    for observance in config.observances:
        if observance.date.year != year:
            continue
        rows.append(
            _observance_row(observance.date, observance.name, observance.note, kinds)
        )
    travel = dt.timedelta(days=config.conference_travel)
    for occurrence in config.conferences:
        if occurrence.start.year != year:
            continue
        # Travel days are booked too, so they belong in the cost.
        span = _date_span(occurrence.start - travel, occurrence.end + travel)
        cost = sum(
            1
            for date in _dates_between(occurrence.start - travel, occurrence.end + travel)
            if kinds.get(date) is DayKind.PTO
        )
        note = "" if occurrence.confirmed else "estimated dates"
        where = f" ({occurrence.conference.city})" if occurrence.conference.city else ""
        rows.append(
            f"    {span:<12}  {occurrence.name + where:<30} {cost} day(s) of leave"
            + (f"   ({note})" if note else "")
        )

    if not rows:
        return []
    return ["  Days you are taking yourself"] + rows + [""]


def _date_span(start: dt.date, end: dt.date) -> str:
    if start == end:
        return start.strftime("%b %d")
    if start.month == end.month:
        return f"{start.strftime('%b %d')}-{end.strftime('%d')}"
    return f"{start.strftime('%b %d')}-{end.strftime('%b %d')}"


def _dates_between(start: dt.date, end: dt.date) -> List[dt.date]:
    return [start + dt.timedelta(days=n) for n in range((end - start).days + 1)]


def _observance_row(
    date: dt.date, name: str, note: str, kinds: Dict[dt.date, DayKind]
) -> str:
    kind = kinds.get(date)
    if kind is DayKind.PTO:
        cost = "1 day of leave"
    elif kind is DayKind.HOLIDAY:
        cost = "already a company holiday"
    elif kind is DayKind.WEEKEND:
        cost = "free, non-working day"
    else:
        cost = "outside the planned window"
    row = f"    {date.strftime('%a %b %d')}  {name:<30} {cost}"
    return f"{row}   ({note})" if note else row


def _render_plan_year(
    plan: PtoPlan, year: int, config: CompanyConfig, efficiency: bool = False
) -> List[str]:
    breaks = [b for b in plan.breaks if b.start.year == year or b.end.year == year]
    lines = [f"  Planned breaks ({plan.min_break}+ consecutive days off)"]
    if not breaks:
        lines.append("    (none)")
    for item in breaks:
        if item.pto_cost:
            cost = f"{item.pto_cost} PTO"
            taken = ", ".join(d.strftime("%b %d") for d in item.pto_dates)
            detail = f"take {taken}"
            if efficiency:
                detail += f"  ({item.efficiency:.1f} per PTO day)"
        else:
            cost, detail = "free", "no PTO needed"
        names = "; ".join(dict.fromkeys(item.holidays))
        lines.append(f"    {item.length:2d} days  {item.label():<34} {cost:>7}  {detail}")
        if names:
            lines.append(f"{'':>14}covers {names}")

    lines.extend(_render_observances(config, plan, year))

    spent = plan.spent_by_year.get(year, 0)
    granted = plan.granted_total(year)
    floating = plan.floating_by_year.get(year, 0)
    lines.append("")
    if floating:
        lines.append(
            f"  {spent} of {granted} day(s) used in {year} "
            f"({plan.pto_used(year)} PTO, {plan.floating_used(year)} floating)"
            + (f", {granted - spent} left over" if granted - spent > 0 else "")
        )
        unused_floating = floating - plan.floating_used(year)
        if unused_floating:
            lines.append(
                f"  {unused_floating} floating holiday(s) unused; they expire on Dec 31"
            )
    else:
        lines.append(
            f"  {spent} of {granted} PTO day(s) used in {year}"
            + (f", {granted - spent} left over" if granted - spent > 0 else "")
        )
    if config.blackout:
        blocked = sum(1 for d in config.blackout if d.year == year)
        if blocked:
            lines.append(f"  {blocked} day(s) excluded by blackout periods")
    lines.append("")
    return lines


def _render_totals(plan: PtoPlan, labeled: bool = False) -> List[str]:
    days_off = plan.total_days_off
    bridged = sum(b.length for b in plan.breaks)
    lines = ["Totals", _RULE]
    lines.append(f"  {len(plan.breaks)} break(s) of {plan.min_break}+ days, {bridged} days inside them")
    if plan.total_floating:
        lines.append(
            f"  {plan.total_pto} day(s) of leave spent for {days_off} total days off "
            f"({plan.total_pto - plan.total_floating} PTO, {plan.total_floating} floating)"
        )
    else:
        lines.append(f"  {plan.total_pto} PTO day(s) spent for {days_off} total days off")
    if plan.total_pto:
        unit = "day of leave" if plan.total_floating else "PTO day"
        # Beside the days-in-breaks score, say which days this older figure counts.
        scope = " (every day off, weekends included)" if labeled else ""
        lines.append(f"  {days_off / plan.total_pto:.1f} days off per {unit} spent{scope}")
    return lines


def _render_efficiency(plan: PtoPlan, objective: Optional[Dict[str, Any]]) -> List[str]:
    """Days in breaks per day of leave: the score the planner is judged on."""
    lines = [""]
    if plan.total_pto:
        lines.append(
            f"  Efficiency: {plan.days_in_breaks} days in breaks for {plan.total_pto} PTO "
            f"day(s): {plan.efficiency:.1f} days per PTO day, days in breaks only"
        )
    else:
        lines.append(
            f"  Efficiency: {plan.days_in_breaks} days in breaks without booking any leave"
        )
    if objective is not None:
        value = objective["value"]
        if objective["kind"] == "min_return" and objective["full_budget"]:
            unit = "day" if value == 1 else "days"
            lines.append(
                f"  Spending used the full budget; the extra days averaged at least {value:g} "
                f"{unit} in breaks each."
            )
        elif objective["kind"] == "min_return":
            lines.append(
                f"  Spending stopped at up to {objective['budget_per_year']} PTO day(s) per "
                f"year: past that, extra days would average fewer than {value:g} days in breaks."
            )
        else:
            lines.append(
                f"  Target of {value} days in breaks reached with up to "
                f"{objective['budget_per_year']} PTO day(s) per year."
            )
    return lines


def _render_curve(curve: ReturnCurve) -> List[str]:
    """Budget against days in breaks, with the point where returns fall away."""
    knee = curve.knee
    lines = [
        "",
        "Return curve (PTO budget per year against days in breaks)",
        _RULE,
        "  Budget  PTO spent  Days in breaks  Last day returned  Days per PTO",
    ]
    for point in curve.points:
        gained = "-" if point.marginal is None else f"{point.marginal:.1f}"
        score = "-" if point.days_per_pto is None else f"{point.days_per_pto:.1f}"
        row = (
            f"  {point.budget:>6}  {point.pto_spent:>9}  {point.days_in_breaks:>14}"
            f"  {gained:>17}  {score:>12}"
        )
        if knee is not None and point.budget == knee.budget:
            row += "  <- knee"
        lines.append(row)
    if knee is None:
        lines.append("  (no knee: the return never falls)")
    return lines


def _describe_config(config: CompanyConfig) -> str:
    workweek = _workweek_label(config.workweek)
    observance = {
        Observance.NONE: "no weekend observance",
        Observance.NEAREST: "nearest working day observance",
        Observance.NEXT: "next working day observance",
        Observance.PREVIOUS: "previous working day observance",
    }[config.observance]
    return f"{len(config.holidays)} holidays, {workweek}, {observance}"


def _workweek_label(workweek: Iterable[int]) -> str:
    days = sorted(workweek)
    if not days:
        return "no working days"
    short = [WEEKDAY_NAMES[d][:3] for d in days]
    # Collapse a contiguous run into "Mon-Fri"; otherwise list the days.
    if len(days) > 2 and days == list(range(days[0], days[0] + len(days))):
        return f"{short[0]}-{short[-1]} workweek"
    return f"{'/'.join(short)} workweek"


# --------------------------------------------------------------------------- json


def render_json(
    config: CompanyConfig,
    holidays_by_year: Dict[int, List[ResolvedHoliday]],
    opportunities_by_year: Dict[int, List[Opportunity]],
    plan: Optional[PtoPlan] = None,
    efficiency: bool = False,
    curve: Optional[ReturnCurve] = None,
    objective: Optional[Dict[str, Any]] = None,
    goal: Optional[GoalSummary] = None,
) -> str:
    payload: Dict[str, object] = {
        "config": {
            "name": config.name,
            "workweek": sorted(config.workweek),
            "observance": config.observance.value,
            "min_break": config.min_break,
            "floating_days": config.floating_days,
            "holidays": config.holiday_names(),
            "blackout": sorted(d.isoformat() for d in config.blackout),
            "committed": sorted(d.isoformat() for d in config.committed),
            "observances": [
                {"name": o.name, "date": o.date.isoformat(), "source": o.source, "note": o.note}
                for o in config.observances
            ],
            "conferences": [
                {
                    "name": o.name,
                    "start": o.start.isoformat(),
                    "end": o.end.isoformat(),
                    "confirmed": o.confirmed,
                    "city": o.conference.city,
                }
                for o in config.conferences
            ],
        },
        "years": {},
    }
    assumptions = provisional_assumptions(holidays_by_year, goal.context_holidays if goal else ())
    if assumptions:
        payload["provisional"] = True
        payload["assumptions"] = [
            {"name": h.name, "observed": h.observed.isoformat(), "status": h.status}
            for h in assumptions
        ]
    years: Dict[str, object] = payload["years"]  # type: ignore[assignment]
    for year in sorted(holidays_by_year):
        years[str(year)] = {
            "holidays": [
                {
                    "name": h.name,
                    "actual": h.actual.isoformat(),
                    "observed": h.observed.isoformat(),
                    "shifted": h.shifted,
                }
                for h in holidays_by_year[year]
            ],
            "opportunities": [
                {
                    "holiday": o.holiday,
                    "date": o.date.isoformat(),
                    "natural_length": o.natural_length,
                    "bridge_cost": o.bridge_cost,
                    "bridge_dates": [d.isoformat() for d in o.bridge_dates],
                    "bridged_length": o.bridged_length,
                    "advice": o.advice(),
                }
                for o in opportunities_by_year.get(year, [])
            ],
        }

    if plan is not None:
        payload["plan"] = {
            "min_break": plan.min_break,
            "pto_dates": [d.isoformat() for d in plan.pto_dates],
            "granted_by_year": {str(k): v for k, v in sorted(plan.granted_by_year.items())},
            "floating_by_year": {str(k): v for k, v in sorted(plan.floating_by_year.items())},
            "spent_by_year": {str(k): v for k, v in sorted(plan.spent_by_year.items())},
            "floating_used_by_year": {
                str(year): plan.floating_used(year) for year in sorted(plan.granted_by_year)
            },
            "pto_used_by_year": {
                str(year): plan.pto_used(year) for year in sorted(plan.granted_by_year)
            },
            "total_days_off": plan.total_days_off,
            "breaks": [_break_json(b) for b in plan.breaks],
        }
        extras: Dict[str, object] = payload["plan"]  # type: ignore[assignment]
        if goal is not None:
            extras["headline"] = "honest"
            extras["total_days_off_note"] = (
                "total_days_off is the classic count of every day off in the window, "
                "including breaks that cost no PTO; the headline is the 'honest' block"
            )
        if efficiency:
            extras["efficiency"] = {
                "days_in_breaks": plan.days_in_breaks,
                "pto_spent": plan.total_pto,
                "days_per_pto": round(plan.efficiency, 3) if plan.efficiency else None,
            }
        if objective is not None:
            stop: Dict[str, object] = {
                ("min_return" if objective["kind"] == "min_return" else "target_days_off"):
                    objective["value"],
                "budget_per_year": objective["budget_per_year"],
            }
            if objective["kind"] == "min_return":
                stop["full_budget"] = objective["full_budget"]
            extras["objective"] = stop
    if goal is not None:
        payload["goal"] = {
            "name": goal.goal,
            "scoring": [{"min_length": t, "points_per_day": w} for t, w in goal.spec.thresholds],
            "method": goal.method,
            "min_vacation_days": goal.min_vacation_days,
            "max_breaks": goal.max_breaks,
            "prefer": [d.isoformat() for d in goal.prefer],
            "tradeoff": [_honest_summary_json(name, rep) for name, rep in goal.comparison],
        }
        payload["honest"] = _honest_json(goal.report)
    if curve is not None:
        knee = curve.knee
        payload["curve"] = {
            "points": curve.as_dicts(),
            "knee": knee.budget if knee is not None else None,
        }
    return json.dumps(payload, indent=2, sort_keys=False)


def _honest_summary_json(name: str, rep: HonestReport) -> Dict[str, object]:
    return {
        "goal": name,
        "pto_used": rep.pto_used,
        "breaks": rep.breaks_count,
        "total_days": rep.total_days,
        "longest": rep.longest,
    }


def _honest_json(rep: HonestReport) -> Dict[str, object]:
    bridge = thanksgiving_bridge(rep)
    return {
        "pto_used": rep.pto_used,
        "holidays_included": rep.holidays_included,
        "weekend_days_included": rep.weekend_days_included,
        "total_days": rep.total_days,
        "breaks_count": rep.breaks_count,
        "longest": rep.longest,
        "days_in_selected_years": rep.days_in_years,
        "days_in_adjacent_years": rep.days_adjacent,
        "days_by_year": {str(y): n for y, n in rep.days_by_year.items()},
        "pto_by_year": {str(y): n for y, n in rep.pto_by_year.items()},
        "budgets": [
            {"year": r.year, "used": r.used, "granted": r.granted,
             "carried_over": r.carried, "remaining": r.remaining, "over": r.over}
            for r in rep.budget_rows
        ],
        "annual_nonworking": {
            str(y): {"weekend_days": r.weekend_days, "holidays": r.holiday_days, "total": r.total}
            for y, r in rep.annual.items()
        },
        "uncharged_requests": [
            {"date": d.isoformat(), "reason": why} for d, why in rep.pto_uncharged
        ],
        "free_breaks_not_counted": rep.free_breaks_count,
        "breaks": [
            {
                "start": b.start.isoformat(),
                "end": b.end.isoformat(),
                "length": b.length,
                "pto_cost": b.pto_cost,
                "pto_dates": [d.isoformat() for d in b.pto_dates],
                "holidays": list(b.holiday_names),
            }
            for b in rep.breaks
        ],
        "thanksgiving_bridge": (
            {"pto": bridge.pto, "calendar_days": bridge.days} if bridge is not None else None
        ),
    }


def _break_json(item: Break) -> Dict[str, object]:
    return {
        "start": item.start.isoformat(),
        "end": item.end.isoformat(),
        "length": item.length,
        "pto_cost": item.pto_cost,
        "pto_dates": [d.isoformat() for d in item.pto_dates],
        "holidays": list(dict.fromkeys(item.holidays)),
        "efficiency": round(item.efficiency, 3) if item.efficiency else None,
    }


# --------------------------------------------------------------------------- ics


def render_ics(
    config: CompanyConfig,
    holidays_by_year: Dict[int, List[ResolvedHoliday]],
    plan: Optional[PtoPlan] = None,
    include_holidays: bool = True,
    efficiency: bool = False,
    goal: Optional[GoalSummary] = None,
) -> str:
    """Emit an all-day-event calendar of holidays, PTO days and planned breaks."""
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    assumptions = provisional_assumptions(holidays_by_year, goal.context_holidays if goal else ())
    caveat = ""
    if assumptions:
        caveat = "PROVISIONAL: depends on unconfirmed days (" + ", ".join(
            h.name for h in assumptions
        ) + ")"
    lines: List[str] = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//Long Weekend Planner//EN",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        f"X-WR-CALNAME:{_escape(config.name)} time off" + (" (PROVISIONAL)" if assumptions else ""),
    ]
    if assumptions:
        lines.append("X-LWP-PROVISIONAL:TRUE")
        for h in assumptions:
            lines.append(
                f"X-LWP-ASSUMPTION:{_escape(h.name)} {h.observed.isoformat()} {h.status}"
            )

    if include_holidays:
        for year in sorted(holidays_by_year):
            for holiday in holidays_by_year[year]:
                lines.extend(
                    _event(
                        start=holiday.observed,
                        end=holiday.observed + dt.timedelta(days=1),
                        summary=holiday.name,
                        description=(
                            f"Observed holiday (actual date {holiday.actual.isoformat()})"
                            if holiday.shifted
                            else "Company holiday"
                        ) + (f" PROVISIONAL: {_STATUS_NOTE.get(holiday.status or '', '')}" if holiday.unconfirmed else ""),
                        stamp=stamp,
                        category="HOLIDAY",
                    )
                )

    if plan is not None:
        named = _named_days(config)
        for date in plan.pto_dates:
            name = named.get(date)
            lines.extend(
                _event(
                    start=date,
                    end=date + dt.timedelta(days=1),
                    summary=name or "PTO",
                    description=(
                        f"{name} (booked as time off)" if name else "Planned time off"
                    ) + (f" {caveat}" if caveat else ""),
                    stamp=stamp,
                    category="PTO",
                )
            )
        for item in plan.breaks:
            names = "; ".join(dict.fromkeys(item.holidays))
            notes = names or "Long weekend"
            if efficiency and item.efficiency is not None:
                notes += f" ({item.efficiency:.1f} days off per PTO day)"
            if caveat:
                notes += f" {caveat}"
            lines.extend(
                _event(
                    start=item.start,
                    end=item.end + dt.timedelta(days=1),
                    summary=f"{item.length}-day break ({item.pto_cost} PTO)",
                    description=notes,
                    stamp=stamp,
                    category="BREAK",
                    transparent=True,
                )
            )

    lines.append("END:VCALENDAR")
    return "\r\n".join(_fold(line) for line in lines) + "\r\n"


def _named_days(config: CompanyConfig) -> Dict[dt.date, str]:
    """Dates the user booked for a specific reason, so events can say why."""
    named: Dict[dt.date, str] = {}
    for occurrence in config.conferences:
        for date in occurrence.dates():
            named[date] = occurrence.name
    # Observances win over conferences on a clash: they are the more specific
    # reason for that particular day being unavailable.
    for observance in config.observances:
        named[observance.date] = observance.name
    return named


def _event(
    start: dt.date,
    end: dt.date,
    summary: str,
    description: str,
    stamp: str,
    category: str,
    transparent: bool = False,
) -> List[str]:
    seed = f"{category}|{start.isoformat()}|{end.isoformat()}|{summary}"
    uid = hashlib.sha256(seed.encode("utf-8")).hexdigest()[:40]
    return [
        "BEGIN:VEVENT",
        f"UID:{uid}@long-weekend-planner",
        f"DTSTAMP:{stamp}",
        f"DTSTART;VALUE=DATE:{start.strftime('%Y%m%d')}",
        f"DTEND;VALUE=DATE:{end.strftime('%Y%m%d')}",
        f"SUMMARY:{_escape(summary)}",
        f"DESCRIPTION:{_escape(description)}",
        f"CATEGORIES:{category}",
        f"TRANSP:{'TRANSPARENT' if transparent else 'OPAQUE'}",
        "END:VEVENT",
    ]


def _escape(text: str) -> str:
    return (
        str(text)
        .replace("\\", "\\\\")
        .replace(";", "\\;")
        .replace(",", "\\,")
        .replace("\n", "\\n")
    )


def _fold(line: str) -> str:
    """Fold a content line to 75 octets per RFC 5545, continuing with a space."""
    encoded = line.encode("utf-8")
    if len(encoded) <= 75:
        return line
    chunks: List[bytes] = []
    remaining = encoded
    limit = 75
    while len(remaining) > limit:
        split = limit
        # Do not split inside a multi-byte character.
        while split > 0 and (remaining[split] & 0xC0) == 0x80:
            split -= 1
        chunks.append(remaining[:split])
        remaining = remaining[split:]
        limit = 74
    chunks.append(remaining)
    return "\r\n ".join(chunk.decode("utf-8") for chunk in chunks)

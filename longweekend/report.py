"""Rendering: human-readable text, JSON, and iCalendar output."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
from typing import Any, Dict, Iterable, List, Optional, Sequence

from .catalog import WEEKDAY_NAMES, Observance, ResolvedHoliday
from .config import CompanyConfig
from .efficiency import ReturnCurve
from .planner import PtoPlan
from .schedule import Break, Day, DayKind, Opportunity

__all__ = ["render_text", "render_json", "render_ics"]

_RULE = "-" * 72


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
) -> str:
    lines: List[str] = []
    lines.append(f"{config.name} - {_describe_config(config)}")
    lines.append("")
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
    if curve is not None:
        knee = curve.knee
        payload["curve"] = {
            "points": curve.as_dicts(),
            "knee": knee.budget if knee is not None else None,
        }
    return json.dumps(payload, indent=2, sort_keys=False)


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
) -> str:
    """Emit an all-day-event calendar of holidays, PTO days and planned breaks."""
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    lines: List[str] = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//Long Weekend Planner//EN",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        f"X-WR-CALNAME:{_escape(config.name)} time off",
    ]

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
                        ),
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
                    ),
                    stamp=stamp,
                    category="PTO",
                )
            )
        for item in plan.breaks:
            names = "; ".join(dict.fromkeys(item.holidays))
            notes = names or "Long weekend"
            if efficiency and item.efficiency is not None:
                notes += f" ({item.efficiency:.1f} days off per PTO day)"
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

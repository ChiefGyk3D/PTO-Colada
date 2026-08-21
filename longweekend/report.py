"""Rendering: human-readable text, JSON, and iCalendar output."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
from typing import Dict, Iterable, List, Optional, Sequence

from .catalog import WEEKDAY_NAMES, Observance, ResolvedHoliday
from .config import CompanyConfig
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
) -> str:
    lines: List[str] = []
    lines.append(f"{config.name} - {_describe_config(config)}")
    lines.append("")

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
            lines.append(f"  Long weekends and how to extend them (target {config.min_break} days)")
            for opportunity in opportunities_by_year.get(year, []):
                lines.append(
                    f"    {opportunity.date.strftime('%a %b %d')}  "
                    f"{opportunity.holiday}: {opportunity.advice()}"
                )
            lines.append("")
        else:
            lines.extend(_render_plan_year(plan, year, config))

    if plan is not None:
        lines.extend(_render_totals(plan))
    return "\n".join(lines).rstrip() + "\n"


def _render_plan_year(plan: PtoPlan, year: int, config: CompanyConfig) -> List[str]:
    breaks = [b for b in plan.breaks if b.start.year == year or b.end.year == year]
    lines = [f"  Planned breaks ({plan.min_break}+ consecutive days off)"]
    if not breaks:
        lines.append("    (none)")
    for item in breaks:
        if item.pto_cost:
            cost = f"{item.pto_cost} PTO"
            taken = ", ".join(d.strftime("%b %d") for d in item.pto_dates)
            detail = f"take {taken}"
        else:
            cost, detail = "free", "no PTO needed"
        names = "; ".join(dict.fromkeys(item.holidays))
        lines.append(f"    {item.length:2d} days  {item.label():<34} {cost:>7}  {detail}")
        if names:
            lines.append(f"{'':>14}covers {names}")

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


def _render_totals(plan: PtoPlan) -> List[str]:
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
        lines.append(f"  {days_off / plan.total_pto:.1f} days off per {unit} spent")
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
        for date in plan.pto_dates:
            lines.extend(
                _event(
                    start=date,
                    end=date + dt.timedelta(days=1),
                    summary="PTO",
                    description="Planned time off",
                    stamp=stamp,
                    category="PTO",
                )
            )
        for item in plan.breaks:
            names = "; ".join(dict.fromkeys(item.holidays))
            lines.extend(
                _event(
                    start=item.start,
                    end=item.end + dt.timedelta(days=1),
                    summary=f"{item.length}-day break ({item.pto_cost} PTO)",
                    description=(names or "Long weekend"),
                    stamp=stamp,
                    category="BREAK",
                    transparent=True,
                )
            )

    lines.append("END:VCALENDAR")
    return "\r\n".join(_fold(line) for line in lines) + "\r\n"


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
    uid = hashlib.sha1(seed.encode("utf-8")).hexdigest()  # noqa: S324 - identity only, not security
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

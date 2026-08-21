"""Security conferences, so leave can be planned around them.

Conference dates are the one thing in this tool that cannot be derived from
first principles: organisers move events, and a hard-coded table goes stale.
So the bundled catalog holds *announced* dates where they are known, and a
recurrence rule for the years where they are not -- and anything derived from
a rule is labelled an estimate rather than presented as fact.

Point ``--conferences`` at your own JSON file to override or extend the
catalog; the format is the same and your entries win on a name clash.
"""

from __future__ import annotations

import datetime as dt
import json
import os
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Sequence

from .catalog import parse_rule

__all__ = [
    "Conference",
    "ConferenceOccurrence",
    "load_conferences",
    "find_conference",
    "occurrences_for",
    "describe_catalog",
]

_BUNDLED = os.path.join(os.path.dirname(__file__), "data", "conferences.json")


@dataclass(frozen=True)
class Conference:
    name: str
    city: str = ""
    url: str = ""
    note: str = ""
    aliases: Sequence[str] = ()
    #: Announced dates, keyed by year: {2026: (start, end)}.
    dates: Dict[int, "tuple"] = field(default_factory=dict)
    #: {"when": "<rule spec>", "days": N} for years with nothing announced.
    recurrence: Optional[Dict[str, object]] = None

    def keys(self) -> List[str]:
        return [_key(self.name)] + [_key(a) for a in self.aliases]


@dataclass(frozen=True)
class ConferenceOccurrence:
    """One year of one conference."""

    conference: Conference
    start: dt.date
    end: dt.date
    confirmed: bool

    @property
    def name(self) -> str:
        return self.conference.name

    @property
    def length(self) -> int:
        return (self.end - self.start).days + 1

    def dates(self) -> List[dt.date]:
        return [self.start + dt.timedelta(days=n) for n in range(self.length)]

    def label(self) -> str:
        span = (
            self.start.strftime("%a %b %d")
            if self.start == self.end
            else f"{self.start.strftime('%a %b %d')} - {self.end.strftime('%a %b %d')}"
        )
        tag = "" if self.confirmed else "  (estimated)"
        where = f", {self.conference.city}" if self.conference.city else ""
        return f"{span}{where}{tag}"


def _key(name: str) -> str:
    return " ".join(str(name).split()).casefold()


def load_conferences(path: Optional[str] = None) -> List[Conference]:
    """Load the bundled catalog, optionally overlaid with a user's own file."""
    conferences = _read(_BUNDLED)
    if path:
        overrides = _read(path)
        by_key = {_key(c.name): c for c in conferences}
        for conference in overrides:
            by_key[_key(conference.name)] = conference
        conferences = list(by_key.values())
    return sorted(conferences, key=lambda c: c.name.casefold())


def _read(path: str) -> List[Conference]:
    if not os.path.exists(path):
        raise FileNotFoundError(f"conference file not found: {path}")
    with open(path, "r", encoding="utf-8") as handle:
        try:
            data = json.load(handle)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{path} is not valid JSON: {exc}") from None

    entries = data.get("conferences") if isinstance(data, dict) else data
    if not isinstance(entries, list):
        raise ValueError(f"{path}: expected a 'conferences' list")

    out: List[Conference] = []
    for entry in entries:
        if not isinstance(entry, dict) or "name" not in entry:
            raise ValueError(f"{path}: every conference needs a 'name': {entry!r}")
        dates: Dict[int, tuple] = {}
        for year, span in (entry.get("dates") or {}).items():
            if not isinstance(span, list) or len(span) != 2:
                raise ValueError(
                    f"{path}: {entry['name']} {year} must be [start, end] in YYYY-MM-DD"
                )
            start, end = (dt.date.fromisoformat(str(d)) for d in span)
            if end < start:
                raise ValueError(f"{path}: {entry['name']} {year} ends before it starts")
            dates[int(year)] = (start, end)
        out.append(
            Conference(
                name=str(entry["name"]),
                city=str(entry.get("city", "")),
                url=str(entry.get("url", "")),
                note=str(entry.get("note", "")),
                aliases=tuple(entry.get("aliases", ())),
                dates=dates,
                recurrence=entry.get("recurrence"),
            )
        )
    return out


def find_conference(name: str, conferences: Sequence[Conference]) -> Conference:
    """Look a conference up by name or alias, forgivingly."""
    key = _key(name)
    for conference in conferences:
        if key in conference.keys():
            return conference
    # Fall back to a prefix match so "def con 34" and "black hat" both land.
    for conference in conferences:
        if any(k.startswith(key) or key.startswith(k) for k in conference.keys()):
            return conference
    raise ValueError(
        f"unknown conference {name!r}. Known: "
        + ", ".join(c.name for c in conferences)
        + ". Add your own with --conferences PATH."
    )


def occurrences_for(
    conference: Conference, years: Iterable[int]
) -> List[ConferenceOccurrence]:
    """Every occurrence of ``conference`` in ``years``, announced or estimated."""
    out: List[ConferenceOccurrence] = []
    for year in sorted(set(years)):
        if year in conference.dates:
            start, end = conference.dates[year]
            out.append(ConferenceOccurrence(conference, start, end, confirmed=True))
            continue
        estimated = _estimate(conference, year)
        if estimated is not None:
            out.append(estimated)
    return out


def _estimate(conference: Conference, year: int) -> Optional[ConferenceOccurrence]:
    """Apply the recurrence rule, or give up rather than guess."""
    rule = conference.recurrence
    if not rule or "when" not in rule:
        return None
    parsed = parse_rule(f"{conference.name}={rule['when']}")
    start = parsed.actual(year, {})
    if start is None:
        return None
    days = int(rule.get("days", 1))
    end = start + dt.timedelta(days=max(days, 1) - 1)
    return ConferenceOccurrence(conference, start, end, confirmed=False)


def describe_catalog(conferences: Sequence[Conference], years: Sequence[int]) -> str:
    lines = ["Security conferences this tool knows about:", ""]
    for conference in conferences:
        lines.append(f"  {conference.name}")
        if conference.note:
            lines.append(f"      {conference.note}")
        for occurrence in occurrences_for(conference, years):
            lines.append(f"      {occurrence.start.year}  {occurrence.label()}")
        if conference.aliases:
            lines.append(f"      also answers to: {', '.join(conference.aliases)}")
        if conference.url:
            lines.append(f"      {conference.url}")
        lines.append("")
    lines.append(
        "Dates marked (estimated) come from a recurrence rule, not an announcement --\n"
        "check the event's own site before booking. Override any of this with\n"
        "--conferences PATH pointing at your own JSON in the same format."
    )
    return "\n".join(lines)

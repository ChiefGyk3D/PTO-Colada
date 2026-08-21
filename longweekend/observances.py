"""Days *you* take off, as opposed to days your employer closes for.

A company holiday is free: the office is shut and your balance is untouched.
A personal observance is not. If you take Yom Kippur off, it comes out of your
leave, and the planner needs to know that before it starts spending the rest of
the budget -- both so the arithmetic is honest, and so it can bridge from what
you are already taking to something longer.

Every observance here resolves to a set of dates, which the planner treats as
committed: always taken, always charged. Days that land on a weekend or on a
company holiday cost nothing, and are reported that way.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Dict, Iterable, List, Sequence, Set

from . import hebrew

__all__ = [
    "PersonalDay",
    "OBSERVANCE_SETS",
    "describe_sets",
    "resolve_observances",
]


@dataclass(frozen=True)
class PersonalDay:
    """One day you intend to take off, and where it came from.

    Named for what it is rather than "observance", which in this codebase
    already means the policy for holidays that land on a weekend.
    """

    name: str
    date: dt.date
    source: str
    note: str = ""

    def label(self) -> str:
        return f"{self.name}{' - ' + self.note if self.note else ''}"


#: Named sets, for ``--observe <name>``.
OBSERVANCE_SETS: Dict[str, str] = {
    "jewish": (
        "Yom Kippur, Rosh Hashanah, and the festival days work is traditionally "
        "prohibited on (yom tov)"
    ),
    "jewish-all": (
        "everything in 'jewish' plus Chol HaMoed, Hanukkah, Purim, Tu BiShvat, "
        "Lag BaOmer and Tisha B'Av"
    ),
    "jewish-high-holidays": "Rosh Hashanah, Yom Kippur, Shemini Atzeret and Simchat Torah only",
}


def describe_sets() -> str:
    width = max(len(name) for name in OBSERVANCE_SETS)
    lines = ["Observance sets you can pass to --observe:", ""]
    for name, description in OBSERVANCE_SETS.items():
        lines.append(f"  {name:<{width}}  {description}")
    lines.append("")
    lines.append(
        "You can also name a single holiday -- --observe \"Yom Kippur\" -- or give\n"
        "plain dates: --observe 2026-09-21. Names are matched loosely, so 'Passover'\n"
        "finds Pesach and 'Sukkot' finds all of its days."
    )
    return "\n".join(lines)


_HIGH_HOLIDAYS = {
    "Rosh Hashanah",
    "Rosh Hashanah (day 2)",
    "Yom Kippur",
    "Shemini Atzeret",
    "Simchat Torah",
}

#: Loose aliases so people can type what they say out loud.
_ALIASES: Dict[str, str] = {
    "passover": "pesach",
    "day of atonement": "yom kippur",
    "feast of weeks": "shavuot",
    "feast of tabernacles": "sukkot",
    "tabernacles": "sukkot",
    "jewish new year": "rosh hashanah",
    "high holidays": "rosh hashanah",
    "high holy days": "rosh hashanah",
    "chanukah": "hanukkah",
    "channukah": "hanukkah",
    "hannukah": "hanukkah",
    "succot": "sukkot",
    "shavuos": "shavuot",
    "simchas torah": "simchat torah",
}


def _normalize(text: str) -> str:
    return " ".join(str(text).split()).casefold()


def _jewish_year_pool(year: int, diaspora: bool) -> List[hebrew.JewishHoliday]:
    return hebrew.holidays(year, diaspora=diaspora)


def resolve_observances(
    specs: Sequence[str],
    years: Iterable[int],
    diaspora: bool = True,
    include_erev: bool = False,
) -> List[PersonalDay]:
    """Turn ``--observe`` specs into dated observances across ``years``.

    A spec is a named set (``jewish``), a single holiday name (``Yom Kippur``,
    ``Passover``), or an explicit ``YYYY-MM-DD`` date. Unknown specs raise, with
    the list of things that would have worked.
    """
    years = sorted(set(years))
    found: List[PersonalDay] = []
    seen: Set[tuple] = set()

    def keep(observance: PersonalDay) -> None:
        key = (observance.name, observance.date)
        if key not in seen:
            seen.add(key)
            found.append(observance)

    for raw in specs:
        for chunk in str(raw).split(","):
            spec = chunk.strip()
            if not spec:
                continue
            for observance in _resolve_one(spec, years, diaspora):
                keep(observance)

    if include_erev:
        # Only the *first* day of a run gets an erev. The evening before the
        # second day of a festival is the first day, which is already booked.
        booked = {o.date for o in found}
        for observance in list(found):
            if observance.source != "jewish":
                continue
            evening = observance.date - dt.timedelta(days=1)
            if evening in booked:
                continue
            booked.add(evening)
            keep(
                PersonalDay(
                    name=f"Erev {observance.name}",
                    date=evening,
                    source="jewish",
                    note="the holiday begins at sunset",
                )
            )

    found.sort(key=lambda o: (o.date, o.name))
    return found


def _resolve_one(spec: str, years: Sequence[int], diaspora: bool) -> List[PersonalDay]:
    key = _normalize(spec)

    # An explicit date needs no calendar at all.
    try:
        return [
            PersonalDay(
                name=spec, date=dt.date.fromisoformat(spec.strip()), source="date"
            )
        ]
    except ValueError:
        pass

    if key in {"jewish", "jewish-all", "jewish-high-holidays"}:
        out: List[PersonalDay] = []
        for year in years:
            for holiday in _jewish_year_pool(year, diaspora):
                if key == "jewish" and not holiday.work_restricted:
                    continue
                if key == "jewish-high-holidays" and holiday.name not in _HIGH_HOLIDAYS:
                    continue
                out.append(_from_jewish(holiday))
        return out

    # A single holiday, matched loosely: "Passover" finds every day of Pesach.
    wanted = _ALIASES.get(key, key)
    matches: List[hebrew.JewishHoliday] = []
    for year in years:
        for holiday in _jewish_year_pool(year, diaspora):
            if _normalize(holiday.name).startswith(wanted):
                matches.append(holiday)
    if matches:
        # Naming a festival means the days you cannot work, not the whole
        # span: "Passover" should not book Chol HaMoed, when work is
        # permitted. Minor holidays have no restricted days, so they come
        # through whole -- which is what someone asking for Hanukkah wants.
        restricted = [h for h in matches if h.work_restricted]
        return [_from_jewish(h) for h in (restricted or matches)]

    raise ValueError(
        f"do not know the observance {spec!r}. Pass a set ("
        + ", ".join(OBSERVANCE_SETS)
        + "), a holiday name such as \"Yom Kippur\" or \"Passover\", or a "
        "YYYY-MM-DD date. Run --list-observances for the full list."
    )


def _from_jewish(holiday: hebrew.JewishHoliday) -> PersonalDay:
    note = ""
    if holiday.second_day:
        note = "diaspora second day"
    elif not holiday.work_restricted:
        note = "work permitted"
    return PersonalDay(name=holiday.name, date=holiday.date, source="jewish", note=note)

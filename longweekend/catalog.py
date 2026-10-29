"""Holiday rules, presets, and date resolution.

A *rule* describes how a holiday's date is derived for a given year. Rules are
resolved into concrete dates by :func:`resolve_year`, which also applies the
observance policy (what happens when a holiday lands on a non-working day).

This module is deliberately dependency-free; all date math uses the stdlib.
"""

from __future__ import annotations

import calendar as _calendar
import dataclasses
import datetime as dt
import re
from dataclasses import dataclass
from enum import Enum
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple, Union

__all__ = [
    "Observance",
    "FixedDate",
    "NthWeekday",
    "RelativeTo",
    "ExplicitDate",
    "STATUSES",
    "EasterRelative",
    "WeekdayOnOrAfter",
    "Rule",
    "ResolvedHoliday",
    "PRESETS",
    "KNOWN",
    "easter",
    "known_holiday",
    "parse_rule",
    "parse_weekday",
    "parse_workweek",
    "resolve_year",
]

WEEKDAYS: Dict[str, int] = {
    "MON": 0, "TUE": 1, "WED": 2, "THU": 3, "FRI": 4, "SAT": 5, "SUN": 6,
}
WEEKDAY_ALIASES: Dict[str, int] = {
    "MONDAY": 0, "TUESDAY": 1, "WEDNESDAY": 2, "THURSDAY": 3,
    "FRIDAY": 4, "SATURDAY": 5, "SUNDAY": 6,
    "M": 0, "T": 1, "W": 2, "R": 3, "F": 4, "SA": 5, "SU": 6,
}
WEEKDAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]

MONTHS: Dict[str, int] = {
    name.upper(): i for i, name in enumerate(_calendar.month_abbr) if name
}
MONTHS.update({name.upper(): i for i, name in enumerate(_calendar.month_name) if name})

ORDINALS = {"1ST": 1, "2ND": 2, "3RD": 3, "4TH": 4, "5TH": 5, "LAST": -1}

DEFAULT_WORKWEEK: Set[int] = {0, 1, 2, 3, 4}


class Observance(str, Enum):
    """What to do when a holiday falls on a day you would not have worked."""

    NONE = "none"          #: Observed on the day itself; no bonus day off.
    NEAREST = "nearest"    #: Nearest working day, ties to the earlier one (US federal rule).
    NEXT = "next"          #: The next working day on or after the actual date.
    PREVIOUS = "previous"  #: The last working day on or before the actual date.

    @classmethod
    def parse(cls, value: str) -> "Observance":
        key = str(value).strip().lower()
        aliases = {
            "federal": cls.NEAREST,
            "us": cls.NEAREST,
            "closest": cls.NEAREST,
            "monday": cls.NEXT,
            "forward": cls.NEXT,
            "back": cls.PREVIOUS,
            "backward": cls.PREVIOUS,
            "off": cls.NONE,
            "actual": cls.NONE,
        }
        if key in aliases:
            return aliases[key]
        try:
            return cls(key)
        except ValueError:
            valid = ", ".join(sorted({*(m.value for m in cls), *aliases}))
            raise ValueError(f"unknown observance {value!r} (expected one of: {valid})") from None


# --------------------------------------------------------------------------- rules


@dataclass(frozen=True)
class FixedDate:
    """A holiday on the same month/day every year, e.g. July 4."""

    name: str
    month: int
    day: int

    def actual(self, year: int, _resolved: Dict[str, dt.date]) -> Optional[dt.date]:
        try:
            return dt.date(year, self.month, self.day)
        except ValueError:
            # Feb 29 in a non-leap year: the holiday simply does not occur.
            return None

    def describe(self) -> str:
        return f"{_calendar.month_name[self.month]} {self.day}"


@dataclass(frozen=True)
class NthWeekday:
    """The nth weekday of a month, e.g. the 4th Thursday of November.

    ``n`` counts from the start of the month when positive and from the end
    when negative (``-1`` is "last").
    """

    name: str
    month: int
    weekday: int
    n: int

    def actual(self, year: int, _resolved: Dict[str, dt.date]) -> Optional[dt.date]:
        return nth_weekday(year, self.month, self.weekday, self.n)

    def describe(self) -> str:
        which = "last" if self.n < 0 else f"{self.n}{_ordinal_suffix(self.n)}"
        return f"{which} {WEEKDAY_NAMES[self.weekday]} in {_calendar.month_name[self.month]}"


@dataclass(frozen=True)
class RelativeTo:
    """A fixed offset from another holiday, e.g. the day after Thanksgiving.

    The offset is applied to the *actual* date of the anchor, not its observed
    date, which is what makes "Day After Thanksgiving" correct in every year.
    """

    name: str
    anchor: str
    offset: int

    def actual(self, _year: int, resolved: Dict[str, dt.date]) -> Optional[dt.date]:
        anchor = resolved.get(_normalize(self.anchor))
        if anchor is None:
            return None
        return anchor + dt.timedelta(days=self.offset)

    def describe(self) -> str:
        sign = "+" if self.offset >= 0 else "-"
        return f"{self.anchor} {sign} {abs(self.offset)} day(s)"


#: How sure we are of a dated holiday: ``confirmed`` (published by the
#: employer), ``assumed`` (believed to be paid, not confirmed) or
#: ``observed-unconfirmed`` (the holiday is paid, the observed date is a guess).
STATUSES = ("confirmed", "assumed", "observed-unconfirmed")


@dataclass(frozen=True)
class ExplicitDate:
    """A one-off date, e.g. a company shutdown day announced for a single year."""

    name: str
    date: dt.date
    #: ``None`` means the status was never stated; it counts as confirmed.
    status: Optional[str] = None

    def actual(self, year: int, _resolved: Dict[str, dt.date]) -> Optional[dt.date]:
        return self.date if self.date.year == year else None

    def describe(self) -> str:
        return self.date.isoformat()


@dataclass(frozen=True)
class EasterRelative:
    """A fixed offset from Easter Sunday, e.g. Good Friday is Easter - 2.

    Western (Gregorian) Easter only; employers that observe the Orthodox date
    can pin it with an explicit date per year.
    """

    name: str
    offset: int

    def actual(self, year: int, _resolved: Dict[str, dt.date]) -> Optional[dt.date]:
        return easter(year) + dt.timedelta(days=self.offset)

    def describe(self) -> str:
        if self.offset == 0:
            return "Easter Sunday"
        sign = "+" if self.offset > 0 else "-"
        return f"Easter {sign} {abs(self.offset)} day(s)"


@dataclass(frozen=True)
class WeekdayOnOrAfter:
    """The first given weekday on or after a fixed month/day.

    This is the shape of holidays that resist the nth-weekday rule, the
    canonical one being US Election Day: the Tuesday after the first Monday in
    November, which is exactly the first Tuesday on or after November 2.
    """

    name: str
    month: int
    day: int
    weekday: int

    def actual(self, year: int, _resolved: Dict[str, dt.date]) -> Optional[dt.date]:
        try:
            anchor = dt.date(year, self.month, self.day)
        except ValueError:
            return None
        return anchor + dt.timedelta(days=(self.weekday - anchor.weekday()) % 7)

    def describe(self) -> str:
        return (
            f"first {WEEKDAY_NAMES[self.weekday]} on or after "
            f"{_calendar.month_name[self.month]} {self.day}"
        )


Rule = Union[FixedDate, NthWeekday, RelativeTo, ExplicitDate, EasterRelative, WeekdayOnOrAfter]


@dataclass(frozen=True)
class ResolvedHoliday:
    """A holiday pinned to a year, with both its actual and observed dates."""

    name: str
    actual: dt.date
    observed: dt.date
    #: Confirmation status of a dated holiday (see :data:`STATUSES`), if stated.
    status: Optional[str] = None

    @property
    def unconfirmed(self) -> bool:
        return self.status is not None and self.status != "confirmed"

    @property
    def shifted(self) -> bool:
        return self.actual != self.observed


# --------------------------------------------------------------------------- date math


def nth_weekday(year: int, month: int, weekday: int, n: int) -> Optional[dt.date]:
    """Return the nth ``weekday`` of ``month``, or ``None`` if it does not exist.

    A month has either four or five of any given weekday, so ``n=5`` can be
    absent; every other positive ``n`` and ``n=-1`` always resolve.
    """
    if n == 0:
        raise ValueError("n must be non-zero (use -1 for 'last')")
    days_in_month = _calendar.monthrange(year, month)[1]
    if n > 0:
        first_weekday = dt.date(year, month, 1).weekday()
        day = 1 + (weekday - first_weekday) % 7 + (n - 1) * 7
    else:
        last_weekday = dt.date(year, month, days_in_month).weekday()
        day = days_in_month - (last_weekday - weekday) % 7 + (n + 1) * 7
    if not 1 <= day <= days_in_month:
        return None
    return dt.date(year, month, day)


def easter(year: int) -> dt.date:
    """Western (Gregorian) Easter Sunday, by the anonymous Gregorian computus."""
    a = year % 19
    b, c = divmod(year, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month, day = divmod(h + l - 7 * m + 114, 31)
    return dt.date(year, month, day + 1)


def observed_date(
    date: dt.date,
    workweek: Set[int],
    observance: Observance,
    taken: Optional[Set[dt.date]] = None,
) -> dt.date:
    """Apply an observance policy to ``date``.

    ``taken`` holds dates already claimed by other holidays so that two
    holidays shifting onto the same working day (Christmas Day and Boxing Day
    over a weekend, say) land on separate days instead of collapsing into one.
    """
    if observance is Observance.NONE or not workweek:
        return date
    taken = taken or set()

    def free(candidate: dt.date) -> bool:
        return candidate.weekday() in workweek and candidate not in taken

    if free(date):
        return date

    # Search outward. NEAREST breaks ties toward the earlier day, which
    # reproduces the US federal rule (Saturday -> Friday, Sunday -> Monday)
    # for a Mon-Fri workweek and generalises to any other workweek.
    for delta in range(1, 15):
        backward = date - dt.timedelta(days=delta)
        forward = date + dt.timedelta(days=delta)
        if observance is Observance.NEAREST:
            candidates: Sequence[dt.date] = (backward, forward)
        elif observance is Observance.NEXT:
            candidates = (forward,)
        else:
            candidates = (backward,)
        for candidate in candidates:
            if free(candidate):
                return candidate
    return date


# --------------------------------------------------------------------------- resolution


def resolve_year(
    rules: Sequence[Rule],
    year: int,
    workweek: Optional[Set[int]] = None,
    observance: Observance = Observance.NEAREST,
) -> List[ResolvedHoliday]:
    """Resolve ``rules`` into dated holidays for ``year``, sorted by observed date."""
    workweek = DEFAULT_WORKWEEK if workweek is None else workweek

    # RelativeTo rules depend on their anchor, so resolve in dependency passes.
    actuals: Dict[str, dt.date] = {}
    pending: List[Rule] = list(rules)
    for _ in range(len(pending) + 1):
        if not pending:
            break
        deferred: List[Rule] = []
        progressed = False
        for rule in pending:
            if isinstance(rule, RelativeTo) and _normalize(rule.anchor) not in actuals:
                deferred.append(rule)
                continue
            date = rule.actual(year, actuals)
            if date is not None:
                actuals[_normalize(rule.name)] = date
            progressed = True
        if not progressed:
            break
        pending = deferred
    if pending:
        missing = ", ".join(sorted({r.anchor for r in pending if isinstance(r, RelativeTo)}))
        raise ValueError(
            f"cannot resolve holiday(s) anchored to unknown or cyclic reference(s): {missing}"
        )

    ordered = sorted(
        ((rule, actuals[_normalize(rule.name)]) for rule in rules if _normalize(rule.name) in actuals),
        key=lambda pair: pair[1],
    )

    resolved: List[ResolvedHoliday] = []
    taken: Set[dt.date] = set()
    for rule, actual in ordered:
        observed = observed_date(actual, workweek, observance, taken)
        taken.add(observed)
        resolved.append(
            ResolvedHoliday(
                name=rule.name,
                actual=actual,
                observed=observed,
                status=getattr(rule, "status", None),
            )
        )
    resolved.sort(key=lambda h: (h.observed, h.name))
    return resolved


# --------------------------------------------------------------------------- parsing


def parse_weekday(value: str) -> int:
    key = str(value).strip().upper()
    if key in WEEKDAYS:
        return WEEKDAYS[key]
    if key in WEEKDAY_ALIASES:
        return WEEKDAY_ALIASES[key]
    raise ValueError(f"unknown weekday {value!r}")


def parse_month(value: Union[str, int]) -> int:
    if isinstance(value, int):
        if not 1 <= value <= 12:
            raise ValueError(f"month out of range: {value}")
        return value
    key = str(value).strip().upper()
    if key.isdigit():
        return parse_month(int(key))
    if key in MONTHS:
        return MONTHS[key]
    raise ValueError(f"unknown month {value!r}")


def parse_workweek(value: str) -> Set[int]:
    """Parse a workweek spec such as ``MON-FRI``, ``SUN-THU`` or ``MON,TUE,THU``."""
    text = str(value).strip().upper()
    if not text:
        raise ValueError("workweek cannot be empty")
    if text in {"NONE", "-"}:
        return set()
    days: Set[int] = set()
    for chunk in text.replace(" ", "").split(","):
        if not chunk:
            continue
        if "-" in chunk:
            start_name, _, end_name = chunk.partition("-")
            start, end = parse_weekday(start_name), parse_weekday(end_name)
            # Ranges wrap, so SAT-WED is a legal (if unusual) workweek.
            day = start
            days.add(day)
            while day != end:
                day = (day + 1) % 7
                days.add(day)
        else:
            days.add(parse_weekday(chunk))
    return days


_NTH_RE = re.compile(
    r"^(1st|2nd|3rd|4th|5th|last)\s+([a-z]+)\s+(?:in|of)\s+([a-z]+)$", re.IGNORECASE
)
_ISO_RE = re.compile(r"^(\d{4})-(\d{1,2})-(\d{1,2})$")
_MD_RE = re.compile(r"^(\d{1,2})[-/](\d{1,2})$")
_REL_RE = re.compile(r"^(.+?)\s*([+-])\s*(\d+)$")
_ONOR_RE = re.compile(
    r"^(?:first\s+)?([a-z]+)\s+on\s+or\s+after\s+([a-z0-9]+)[-/\s]+(\d{1,2})$",
    re.IGNORECASE,
)
_EASTER_NAMES = {"easter", "easter sunday"}


def parse_rule(spec: str) -> Rule:
    """Parse a ``Name=when`` holiday spec into a :data:`Rule`.

    Supported forms for the ``when`` half::

        07-04              July 4, every year
        2026-12-28         a one-off date in a single year
        3rd MON in JAN     the nth weekday of a month ('last' also works)
        Thanksgiving+1     an offset from another holiday in the same set
    """
    if "=" not in spec:
        raise ValueError(
            f"holiday spec {spec!r} must look like \"Name=07-04\" "
            "(see --help for the supported date forms)"
        )
    name, _, when = spec.partition("=")
    name, when = name.strip(), when.strip()
    if not name:
        raise ValueError(f"holiday spec {spec!r} is missing a name")
    if not when:
        raise ValueError(f"holiday spec {spec!r} is missing a date")
    return _parse_when(name, when)


def _parse_when(name: str, when: str) -> Rule:
    match = _ISO_RE.match(when)
    if match:
        year, month, day = (int(g) for g in match.groups())
        return ExplicitDate(name=name, date=dt.date(year, month, day))

    match = _MD_RE.match(when)
    if match:
        month, day = int(match.group(1)), int(match.group(2))
        _validate_month_day(name, month, day)
        return FixedDate(name=name, month=month, day=day)

    match = _NTH_RE.match(when)
    if match:
        ordinal, weekday_name, month_name = match.groups()
        return NthWeekday(
            name=name,
            month=parse_month(month_name),
            weekday=parse_weekday(weekday_name),
            n=ORDINALS[ordinal.upper()],
        )

    match = _ONOR_RE.match(when)
    if match:
        weekday_name, month_text, day_text = match.groups()
        month, day = parse_month(month_text), int(day_text)
        _validate_month_day(name, month, day)
        return WeekdayOnOrAfter(
            name=name, month=month, day=day, weekday=parse_weekday(weekday_name)
        )

    if _normalize(when) in _EASTER_NAMES:
        return EasterRelative(name=name, offset=0)

    match = _REL_RE.match(when)
    if match:
        anchor, sign, offset = match.groups()
        anchor = anchor.strip()
        signed = int(offset) * (1 if sign == "+" else -1)
        if _normalize(anchor) in _EASTER_NAMES:
            return EasterRelative(name=name, offset=signed)
        if not anchor:
            raise ValueError(f"holiday {name!r}: relative spec {when!r} is missing an anchor")
        return RelativeTo(name=name, anchor=anchor, offset=signed)

    raise ValueError(
        f"holiday {name!r}: cannot parse date {when!r}. Expected 'MM-DD', "
        "'YYYY-MM-DD', '3rd MON in JAN', 'Anchor Holiday+1', 'Easter-2', "
        "or 'TUE on or after 11-02'."
    )


def rule_from_mapping(data: Dict[str, object]) -> Rule:
    """Build a rule from a config-file mapping."""
    rule = _rule_from_mapping(data)
    if "status" not in data:
        return rule
    status = str(data["status"]).strip().lower()
    if status not in STATUSES:
        raise ValueError(
            f"holiday {rule.name!r}: status must be one of {', '.join(STATUSES)} "
            f"(got {data['status']!r})"
        )
    if not isinstance(rule, ExplicitDate):
        raise ValueError(
            f"holiday {rule.name!r}: 'status' is only supported on entries with an "
            "explicit 'date' (a one-off day), not on recurring rules"
        )
    return dataclasses.replace(rule, status=status)


def _rule_from_mapping(data: Dict[str, object]) -> Rule:
    if "name" not in data:
        raise ValueError(f"holiday entry is missing 'name': {data!r}")
    name = str(data["name"])

    if "when" in data:
        return _parse_when(name, str(data["when"]))
    if "date" in data:
        value = data["date"]
        if isinstance(value, dt.date):
            return ExplicitDate(name=name, date=value)
        return _parse_when(name, str(value))
    if "after" in data or "before" in data:
        anchor = str(data.get("after") or data["before"])
        offset = int(data.get("days", 1))
        if "before" in data and not data.get("after"):
            offset = -abs(offset)
        return RelativeTo(name=name, anchor=anchor, offset=offset)
    if "weekday" in data:
        if "month" not in data:
            raise ValueError(f"holiday {name!r}: 'weekday' requires 'month'")
        n = data.get("n", data.get("nth", 1))
        n = -1 if str(n).lower() == "last" else int(n)
        return NthWeekday(
            name=name,
            month=parse_month(data["month"]),  # type: ignore[arg-type]
            weekday=parse_weekday(str(data["weekday"])),
            n=n,
        )
    if "month" in data and "day" in data:
        month, day = parse_month(data["month"]), int(data["day"])  # type: ignore[arg-type]
        _validate_month_day(name, month, day)
        return FixedDate(name=name, month=month, day=day)

    raise ValueError(
        f"holiday {name!r}: entry needs one of 'when', 'date', 'month'+'day', "
        "'month'+'weekday', or 'after'/'before'"
    )


def _validate_month_day(name: str, month: int, day: int) -> None:
    if not 1 <= month <= 12:
        raise ValueError(f"holiday {name!r}: month {month} out of range")
    # Use a leap year so Feb 29 is accepted; it is skipped in non-leap years.
    if not 1 <= day <= _calendar.monthrange(2024, month)[1]:
        raise ValueError(f"holiday {name!r}: day {day} out of range for month {month}")


def _normalize(name: str) -> str:
    return " ".join(str(name).split()).casefold()


def _ordinal_suffix(n: int) -> str:
    return {1: "st", 2: "nd", 3: "rd"}.get(n if n < 20 else n % 10, "th")


# --------------------------------------------------------------------------- presets

_NEW_YEAR = FixedDate("New Year's Day", 1, 1)
_MLK = NthWeekday("Martin Luther King Jr. Day", 1, WEEKDAYS["MON"], 3)
_PRESIDENTS = NthWeekday("Presidents' Day", 2, WEEKDAYS["MON"], 3)
_MEMORIAL = NthWeekday("Memorial Day", 5, WEEKDAYS["MON"], -1)
_JUNETEENTH = FixedDate("Juneteenth", 6, 19)
_JULY_FOURTH = FixedDate("Independence Day", 7, 4)
_LABOR = NthWeekday("Labor Day", 9, WEEKDAYS["MON"], 1)
_COLUMBUS = NthWeekday("Columbus Day", 10, WEEKDAYS["MON"], 2)
_VETERANS = FixedDate("Veterans Day", 11, 11)
_THANKSGIVING = NthWeekday("Thanksgiving", 11, WEEKDAYS["THU"], 4)
_DAY_AFTER_THANKSGIVING = RelativeTo("Day After Thanksgiving", "Thanksgiving", 1)
_CHRISTMAS_EVE = FixedDate("Christmas Eve", 12, 24)
_CHRISTMAS = FixedDate("Christmas Day", 12, 25)
_BOXING_DAY = FixedDate("Day After Christmas", 12, 26)
_NEW_YEARS_EVE = FixedDate("New Year's Eve", 12, 31)
_GOOD_FRIDAY = EasterRelative("Good Friday", -2)
_EASTER_MONDAY = EasterRelative("Easter Monday", 1)
_ELECTION_DAY = WeekdayOnOrAfter("Election Day", 11, 2, WEEKDAYS["TUE"])
_INDIGENOUS = NthWeekday("Indigenous Peoples' Day", 10, WEEKDAYS["MON"], 2)

#: Named holiday sets. Start from the closest match and adjust with --add/--drop.
PRESETS: Dict[str, List[Rule]] = {
    # All 11 US federal holidays, including Juneteenth (added 2021).
    "federal": [
        _NEW_YEAR, _MLK, _PRESIDENTS, _MEMORIAL, _JUNETEENTH, _JULY_FOURTH,
        _LABOR, _COLUMBUS, _VETERANS, _THANKSGIVING, _CHRISTMAS,
    ],
    # What a generous private employer typically closes for.
    "corporate": [
        _NEW_YEAR, _MLK, _PRESIDENTS, _MEMORIAL, _JUNETEENTH, _JULY_FOURTH,
        _LABOR, _THANKSGIVING, _DAY_AFTER_THANKSGIVING, _CHRISTMAS_EVE,
        _CHRISTMAS, _NEW_YEARS_EVE,
    ],
    # The six almost every US employer observes.
    "minimal": [
        _NEW_YEAR, _MEMORIAL, _JULY_FOURTH, _LABOR, _THANKSGIVING, _CHRISTMAS,
    ],
    # Kept for compatibility with the original script's two hard-coded lists.
    "standard": [
        _NEW_YEAR, _MLK, _PRESIDENTS, _MEMORIAL, _JULY_FOURTH, _LABOR,
        _VETERANS, _THANKSGIVING, _CHRISTMAS,
    ],
    "nonstandard": [
        _NEW_YEAR, _MEMORIAL, _JULY_FOURTH, _LABOR, _THANKSGIVING,
        _DAY_AFTER_THANKSGIVING, _CHRISTMAS,
    ],
    # Closed from Christmas Eve through New Year's Day. The days between are
    # ordinary fixed dates, so a weekend inside the shutdown is not double-paid.
    "shutdown": [
        _NEW_YEAR, _MLK, _MEMORIAL, _JUNETEENTH, _JULY_FOURTH, _LABOR,
        _THANKSGIVING, _DAY_AFTER_THANKSGIVING, _CHRISTMAS_EVE, _CHRISTMAS,
        _BOXING_DAY, FixedDate("Winter Shutdown (Dec 27)", 12, 27),
        FixedDate("Winter Shutdown (Dec 28)", 12, 28),
        FixedDate("Winter Shutdown (Dec 29)", 12, 29),
        FixedDate("Winter Shutdown (Dec 30)", 12, 30),
        _NEW_YEARS_EVE,
    ],
    # Everything the engine knows about, for people who would rather start
    # from the full list and --drop their way down.
    "everything": [
        _NEW_YEAR, _MLK, _PRESIDENTS, _GOOD_FRIDAY, _EASTER_MONDAY, _MEMORIAL,
        _JUNETEENTH, _JULY_FOURTH, _LABOR, _COLUMBUS, _ELECTION_DAY, _VETERANS,
        _THANKSGIVING, _DAY_AFTER_THANKSGIVING, _CHRISTMAS_EVE, _CHRISTMAS,
        _BOXING_DAY, _NEW_YEARS_EVE,
    ],
}

#: Holidays that can be added by name alone, e.g. ``--add "Good Friday"``.
#: Employers mix and match these, so the catalog is deliberately wider than
#: any one preset. Aliases (the second column) resolve to the same rule.
KNOWN: Dict[str, Rule] = {
    rule.name: rule
    for rule in [
        _NEW_YEAR, _MLK, _PRESIDENTS, _GOOD_FRIDAY, _EASTER_MONDAY, _MEMORIAL,
        _JUNETEENTH, _JULY_FOURTH, _LABOR, _COLUMBUS, _INDIGENOUS, _ELECTION_DAY,
        _VETERANS, _THANKSGIVING, _DAY_AFTER_THANKSGIVING, _CHRISTMAS_EVE,
        _CHRISTMAS, _BOXING_DAY, _NEW_YEARS_EVE,
    ]
}

_KNOWN_ALIASES: Dict[str, str] = {
    "mlk day": "Martin Luther King Jr. Day",
    "martin luther king day": "Martin Luther King Jr. Day",
    "washington's birthday": "Presidents' Day",
    "presidents day": "Presidents' Day",
    "juneteenth national independence day": "Juneteenth",
    "fourth of july": "Independence Day",
    "july 4th": "Independence Day",
    "black friday": "Day After Thanksgiving",
    "thanksgiving day": "Thanksgiving",
    "boxing day": "Day After Christmas",
    "christmas": "Christmas Day",
    "new years day": "New Year's Day",
    "new years eve": "New Year's Eve",
    "veterans' day": "Veterans Day",
    "indigenous peoples day": "Indigenous Peoples' Day",
    "columbus day / indigenous peoples' day": "Columbus Day",
}

_KNOWN_BY_KEY: Dict[str, Rule] = {_normalize(name): rule for name, rule in KNOWN.items()}


def known_holiday(name: str) -> Optional[Rule]:
    """Look a holiday up by name or alias, or return ``None`` if it is unknown."""
    key = _normalize(name)
    if key in _KNOWN_BY_KEY:
        return _KNOWN_BY_KEY[key]
    alias = _KNOWN_ALIASES.get(key)
    return _KNOWN_BY_KEY[_normalize(alias)] if alias else None

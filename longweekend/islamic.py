"""The Islamic (Hijri) calendar, and the Muslim holidays that fall out of it.

This is the *tabular* (arithmetic) Islamic calendar: twelve months alternating
30 and 29 days, with an extra day added to the last month in 11 years of every
30-year cycle, counted from the civil epoch of Friday 16 July 622 (Julian). It
is pure integer arithmetic on the standard library, no tables and no
dependencies.

It is NOT the calendar anyone actually observes. Real Islamic months begin when
the new crescent moon is sighted (or, in some countries, by a calculation such
as Umm al-Qura), so the day a holiday is kept can differ by region and is only
certain a day or so ahead. Measured against the Umm al-Qura calendar for every
holiday here from 2026 to 2030, the tabular date was the same day or one day
later, never earlier. Different published sources disagree with each other by
a day as well. The documented margin is therefore :data:`MARGIN_DAYS` = 2 days
either way, and every date this module produces should be treated as
provisional until it is announced. Use an explicit override (see
:func:`holidays`) or a shift when you know better.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Dict, List, Mapping, Optional, Sequence, Tuple

__all__ = [
    "MARGIN_DAYS",
    "MAX_SHIFT_DAYS",
    "OVERRIDE_TOLERANCE_DAYS",
    "HOLIDAY_NAMES",
    "IslamicHoliday",
    "canonical_name",
    "check_shift",
    "from_gregorian",
    "holidays",
    "is_leap_year",
    "match_names",
    "month_length",
    "to_gregorian",
    "validate_overrides",
]

#: How far, in days, a date from this module may be from the one announced.
MARGIN_DAYS = 2
#: Largest ``--shift`` accepted: the margin plus a day for a local sighting.
MAX_SHIFT_DAYS = 3
#: An announced date this far from the computed one is treated as a typing slip.
OVERRIDE_TOLERANCE_DAYS = 3

# 1 Muharram 1 AH, civil epoch: Friday 16 July 622 Julian = 19 July 622 Gregorian.
_EPOCH_ORDINAL = dt.date(622, 7, 19).toordinal()
_MAX_YEAR = 9999

#: (name, Hijri month, Hijri day). Order here is the order sets list them in.
#: The month and day numbers are the usual conventions, not a ruling.
_DEFINITIONS: Tuple[Tuple[str, int, int], ...] = (
    ("Islamic New Year", 1, 1),
    ("Ashura", 1, 10),
    ("Mawlid", 3, 12),
    ("Isra and Mi'raj", 7, 27),
    ("First day of Ramadan", 9, 1),
    ("Laylat al-Qadr", 9, 27),
    ("Eid al-Fitr", 10, 1),
    ("Eid al-Adha", 12, 10),
)
HOLIDAY_NAMES: Tuple[str, ...] = tuple(name for name, _, _ in _DEFINITIONS)

#: Loose names, so people can type what they say out loud. Keys are already
#: in :func:`_norm` form.
_ALIASES: Dict[str, str] = {
    "ramadan": "first day of ramadan",
    "start of ramadan": "first day of ramadan",
    "muharram": "islamic new year",
    "hijri new year": "islamic new year",
    "al hijra": "islamic new year",
    "eid ul fitr": "eid al fitr",
    "id al fitr": "eid al fitr",
    "id ul fitr": "eid al fitr",
    "eid ul adha": "eid al adha",
    "id al adha": "eid al adha",
    "id ul adha": "eid al adha",
    "bakrid": "eid al adha",
    "mawlid al nabi": "mawlid",
    "milad un nabi": "mawlid",
    "prophets birthday": "mawlid",
    "isra and miraj": "isra and miraj",
    "isra miraj": "isra and miraj",
    "night journey": "isra and miraj",
    "lailat al qadr": "laylat al qadr",
    "laylat ul qadr": "laylat al qadr",
    "night of power": "laylat al qadr",
    "night of decree": "laylat al qadr",
    "shab e qadr": "laylat al qadr",
}


@dataclass(frozen=True)
class IslamicHoliday:
    """One Muslim holiday, pinned to a Gregorian date.

    ``confirmed`` is True only when the date came from an explicit override
    (a date you entered because it was announced). Everything computed here is
    unconfirmed.
    """

    name: str
    date: dt.date
    hijri_year: int
    month: int
    day: int
    confirmed: bool = False


# --------------------------------------------------------------------- arithmetic


def _int(value: object, what: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{what} must be a whole number (got {value!r})")
    return value


def is_leap_year(year: int) -> bool:
    """True in 11 years of every 30: 2, 5, 7, 10, 13, 16, 18, 21, 24, 26, 29."""
    return (14 + 11 * year) % 30 < 11


def month_length(year: int, month: int) -> int:
    """Odd months have 30 days, even months 29; the 12th gets 30 in a leap year."""
    if not 1 <= _int(month, "month") <= 12:
        raise ValueError(f"Hijri month must be 1 to 12 (got {month})")
    if month == 12 and is_leap_year(_int(year, "year")):
        return 30
    return 30 if month % 2 else 29


def to_gregorian(year: int, month: int, day: int) -> dt.date:
    """The Gregorian date of a tabular Hijri date; ``ValueError`` if it is not one."""
    _int(year, "year")
    if year < 1 or year > 10000:
        raise ValueError(f"Hijri year out of range: {year}")
    if not 1 <= _int(day, "day") <= month_length(year, month):
        raise ValueError(f"day {day} does not exist in month {month} of {year} AH")
    elapsed = (year - 1) * 354 + (3 + 11 * year) // 30 + 29 * (month - 1) + month // 2 + (day - 1)
    ordinal = _EPOCH_ORDINAL + elapsed
    if not 1 <= ordinal <= dt.date.max.toordinal():
        raise ValueError(f"{day}/{month}/{year} AH is outside the supported Gregorian range")
    return dt.date.fromordinal(ordinal)


def from_gregorian(date: dt.date) -> Tuple[int, int, int]:
    """``(year, month, day)`` of the tabular Hijri date of ``date``."""
    elapsed = date.toordinal() - _EPOCH_ORDINAL
    if elapsed < 0:
        raise ValueError(f"{date} is before the Islamic epoch")
    year = max(1, (30 * elapsed + 10646) // 10631)
    # The closed form is exact; step defensively rather than trust it blindly.
    while to_gregorian(year, 1, 1) > date:
        year -= 1
    while to_gregorian(year + 1, 1, 1) <= date:
        year += 1
    remaining = (date - to_gregorian(year, 1, 1)).days
    month = 1
    while remaining >= month_length(year, month):
        remaining -= month_length(year, month)
        month += 1
    return year, month, remaining + 1


# ------------------------------------------------------------------------ naming


def _norm(text: object) -> str:
    out = str(text).casefold()
    for ch in "'’‘ʼʿ`":
        out = out.replace(ch, "")
    out = out.replace("-", " ").replace("_", " ")
    return " ".join(out.split())


_CANONICAL: Dict[str, str] = {_norm(name): name for name in HOLIDAY_NAMES}


def canonical_name(text: str) -> Optional[str]:
    """The holiday ``text`` names exactly or by alias, else ``None``."""
    key = _norm(text)
    key = _ALIASES.get(key, key)
    return _CANONICAL.get(key)


def match_names(text: str) -> List[str]:
    """Holidays ``text`` names: exactly, by alias, or as a prefix ('Eid')."""
    exact = canonical_name(text)
    if exact:
        return [exact]
    key = _norm(text)
    if not key:
        return []
    return [name for name in HOLIDAY_NAMES if _norm(name).startswith(key)]


# --------------------------------------------------------------------- the holidays


def check_shift(value: object) -> int:
    """Validate a ``--shift``: a whole number between -3 and 3 days."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"the shift must be a whole number of days (got {value!r})")
    if not -MAX_SHIFT_DAYS <= value <= MAX_SHIFT_DAYS:
        raise ValueError(
            f"the shift must be between -{MAX_SHIFT_DAYS} and {MAX_SHIFT_DAYS} days (got {value})"
        )
    return value


def _raw_occurrences(year: int) -> List[Tuple[str, int, int, int, dt.date]]:
    """Every holiday whose tabular date is near Gregorian ``year`` (padded a year)."""
    if not 1 <= _int(year, "year") <= _MAX_YEAR - 1:
        raise ValueError(f"year out of range: {year}")
    first = from_gregorian(dt.date(max(year, 623), 1, 1))[0]
    last = from_gregorian(dt.date(year, 12, 31))[0]
    found = []
    for hijri_year in range(first, last + 2):
        for name, month, day in _DEFINITIONS:
            try:
                found.append((name, hijri_year, month, day, to_gregorian(hijri_year, month, day)))
            except ValueError:
                continue
    return found


def holidays(
    year: int,
    shift: int = 0,
    overrides: Optional[Mapping[str, Sequence[dt.date]]] = None,
) -> List[IslamicHoliday]:
    """Muslim holidays falling in Gregorian ``year``, sorted by date.

    A Hijri year is 11 or so days shorter than a Gregorian one, so a holiday
    can fall twice in one Gregorian year (Eid al-Fitr in 2033, the first day
    of Ramadan in 2030); both are returned.

    ``shift`` (days, validated by :func:`check_shift`) moves every computed
    date, for a local sighting. ``overrides`` maps a holiday name to announced
    dates; a date within :data:`OVERRIDE_TOLERANCE_DAYS` of a computed
    occurrence replaces it, is not shifted, and is marked ``confirmed``.
    """
    shift = check_shift(shift)
    overrides = _canonical_overrides(overrides)
    out: List[IslamicHoliday] = []
    for name, hijri_year, month, day, raw in _raw_occurrences(year):
        announced = None
        for candidate in overrides.get(name, ()):
            if abs((candidate - raw).days) <= OVERRIDE_TOLERANCE_DAYS:
                announced = candidate
        if announced is not None:
            date, confirmed = announced, True
        else:
            try:
                date = raw + dt.timedelta(days=shift)
            except OverflowError:
                continue
            confirmed = False
        if date.year == year:
            out.append(IslamicHoliday(name, date, hijri_year, month, day, confirmed))
    out.sort(key=lambda h: (h.date, h.name))
    return out


def _canonical_overrides(
    overrides: Optional[Mapping[str, Sequence[dt.date]]],
) -> Dict[str, List[dt.date]]:
    result: Dict[str, List[dt.date]] = {}
    for key, dates in (overrides or {}).items():
        name = canonical_name(key)
        if name is None:
            raise ValueError(
                f"{key!r} is not a Muslim holiday name; use one of: " + ", ".join(HOLIDAY_NAMES)
            )
        result.setdefault(name, []).extend(dates)
    return result


def validate_overrides(
    overrides: Mapping[str, Sequence[dt.date]],
) -> Dict[str, List[dt.date]]:
    """Check every override against the calendar; returns them by canonical name.

    Each date must be within :data:`OVERRIDE_TOLERANCE_DAYS` of some computed
    occurrence of its holiday, and two dates may not claim the same one.
    """
    canonical = _canonical_overrides(overrides)
    for name, dates in canonical.items():
        _, month, day = next(d for d in _DEFINITIONS if d[0] == name)
        claimed: Dict[Tuple[int, dt.date], dt.date] = {}
        for date in dates:
            start = from_gregorian(date)[0]
            near = []
            for hijri_year in (start - 1, start, start + 1):
                try:
                    near.append((hijri_year, to_gregorian(hijri_year, month, day)))
                except ValueError:
                    continue
            hijri_year, raw = min(near, key=lambda pair: abs((pair[1] - date).days))
            gap = abs((raw - date).days)
            if gap > OVERRIDE_TOLERANCE_DAYS:
                raise ValueError(
                    f"{name} on {date.isoformat()} is {gap} days from the calendar's "
                    f"{raw.isoformat()}; dates are good to about {MARGIN_DAYS} days, so "
                    "check the date you entered"
                )
            if (hijri_year, raw) in claimed:
                raise ValueError(
                    f"{name} is given twice for the same year: "
                    f"{claimed[(hijri_year, raw)].isoformat()} and {date.isoformat()}"
                )
            claimed[(hijri_year, raw)] = date
    return canonical

"""The Hebrew calendar, and the Jewish holidays that fall out of it.

Jewish holidays are fixed on the Hebrew calendar and therefore wander across
the Gregorian one, which is exactly why they are worth computing rather than
tabulating: a table goes stale, an algorithm does not.

The arithmetic here is the standard rule-based Hebrew calendar -- the one
fixed by Hillel II that every printed luach follows -- so results agree with
published calendars for any year you are plausibly planning leave in. It is
pure integer arithmetic on the stdlib, no dependencies and no lookup tables.

Two things to know before reading dates out of this module:

* A Hebrew day starts at sunset. The dates here are the *daytime* dates, which
  is what you would put on a leave request; the holiday actually begins the
  evening before. :func:`erev` gives you that preceding day.
* Outside Israel most pilgrimage festivals run an extra day (yom tov sheni).
  Pass ``diaspora=False`` for the Israeli calendar.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import List, Tuple

__all__ = [
    "JewishHoliday",
    "erev",
    "from_gregorian",
    "holidays",
    "is_leap_year",
    "month_names",
    "months_in_year",
    "rosh_hashanah",
    "to_gregorian",
    "year_length",
]

#: Ordinal of 1 Tishrei of Hebrew year 1, chosen so that the elapsed-day count
#: below lands on the Gregorian dates published in every luach. Verified in the
#: tests against known holiday dates across several decades.
_EPOCH_ORDINAL = -1373428

# Month names in civil order, i.e. starting at Tishrei, the way the year is
# numbered. Adar I only exists in a leap year.
TISHREI = 1


@dataclass(frozen=True)
class JewishHoliday:
    """One Jewish holiday, pinned to a Gregorian date.

    ``work_restricted`` marks the days on which work is traditionally
    prohibited -- yom tov and Yom Kippur. Those are the days someone observing
    the calendar has to be off, as opposed to the minor holidays (Hanukkah,
    Purim, Tu BiShvat) where work is permitted.
    """

    name: str
    date: dt.date
    hebrew_year: int
    work_restricted: bool = False
    #: Set for the extra diaspora day of a festival, so callers can explain it.
    second_day: bool = False

    @property
    def erev(self) -> dt.date:
        """The day the holiday begins at sunset."""
        return self.date - dt.timedelta(days=1)


# --------------------------------------------------------------------- arithmetic


def is_leap_year(year: int) -> bool:
    """True in the 7 leap years of each 19-year Metonic cycle."""
    return ((7 * year + 1) % 19) < 7


def months_in_year(year: int) -> int:
    return 13 if is_leap_year(year) else 12


def _elapsed_days(year: int) -> int:
    """Days from the epoch to 1 Tishrei of ``year``, postponements included.

    The molad (mean new moon) of Tishrei sets the day, and then the four
    dehiyyot push it later: a molad at or after noon, and the rule that Rosh
    Hashanah never falls on Sunday, Wednesday or Friday, plus the two rules
    that keep the year a legal length.
    """
    months_elapsed = (
        235 * ((year - 1) // 19)              # months in whole Metonic cycles
        + 12 * ((year - 1) % 19)              # ordinary months this cycle
        + (7 * ((year - 1) % 19) + 1) // 19   # leap months this cycle
    )
    parts_elapsed = 204 + 793 * (months_elapsed % 1080)
    hours_elapsed = (
        5 + 12 * months_elapsed + 793 * (months_elapsed // 1080) + parts_elapsed // 1080
    )
    day = 1 + 29 * months_elapsed + hours_elapsed // 24
    parts = 1080 * (hours_elapsed % 24) + parts_elapsed % 1080

    if (
        parts >= 19440                                    # molad zaken: at/after noon
        or (day % 7 == 2 and parts >= 9924 and not is_leap_year(year))
        or (day % 7 == 1 and parts >= 16789 and is_leap_year(year - 1))
    ):
        day += 1

    # Lo ADU rosh: never Sunday, Wednesday or Friday.
    if day % 7 in (0, 3, 5):
        day += 1
    return day


def year_length(year: int) -> int:
    """Length of ``year`` in days: 353-355, or 383-385 in a leap year."""
    return _elapsed_days(year + 1) - _elapsed_days(year)


def month_names(year: int) -> List[str]:
    """Month names in civil order for ``year``."""
    return [name for name, _ in _months(year)]


def _months(year: int) -> List[Tuple[str, int]]:
    """(name, length) for every month, in civil order from Tishrei."""
    length = year_length(year)
    # Cheshvan gains a day in a "complete" year; Kislev loses one in a
    # "deficient" year. That is the only slack in the calendar.
    cheshvan = 30 if length in (355, 385) else 29
    kislev = 29 if length in (353, 383) else 30

    months: List[Tuple[str, int]] = [
        ("Tishrei", 30),
        ("Cheshvan", cheshvan),
        ("Kislev", kislev),
        ("Tevet", 29),
        ("Shevat", 30),
    ]
    if is_leap_year(year):
        months += [("Adar I", 30), ("Adar II", 29)]
    else:
        months += [("Adar", 29)]
    months += [
        ("Nisan", 30),
        ("Iyar", 29),
        ("Sivan", 30),
        ("Tammuz", 29),
        ("Av", 30),
        ("Elul", 29),
    ]
    return months


def rosh_hashanah(year: int) -> dt.date:
    """Gregorian date of 1 Tishrei -- the first day of Hebrew year ``year``."""
    return dt.date.fromordinal(_EPOCH_ORDINAL + _elapsed_days(year))


def to_gregorian(year: int, month: str, day: int) -> dt.date:
    """Convert a Hebrew date to Gregorian. ``month`` is a name, e.g. "Nisan".

    In a leap year "Adar" means Adar II, which is where Purim and the rest of
    Adar's observances land.
    """
    months = _months(year)
    if month == "Adar" and is_leap_year(year):
        month = "Adar II"
    offset = 0
    for name, length in months:
        if name == month:
            if not 1 <= day <= length:
                raise ValueError(f"{month} {year} has {length} days, not {day}")
            return rosh_hashanah(year) + dt.timedelta(days=offset + day - 1)
        offset += length
    raise ValueError(f"Hebrew year {year} has no month named {month!r}")


def from_gregorian(date: dt.date) -> Tuple[int, str, int]:
    """Convert a Gregorian date to ``(hebrew_year, month_name, day)``."""
    # The Hebrew year runs ahead of the Gregorian one by 3760 or 3761.
    year = date.year + 3761
    while rosh_hashanah(year) > date:
        year -= 1
    while rosh_hashanah(year + 1) <= date:
        year += 1
    remaining = (date - rosh_hashanah(year)).days
    for name, length in _months(year):
        if remaining < length:
            return year, name, remaining + 1
        remaining -= length
    raise AssertionError("date fell outside its own Hebrew year")  # pragma: no cover


def erev(date: dt.date) -> dt.date:
    """The evening a holiday begins: the day before its daytime date."""
    return date - dt.timedelta(days=1)


# --------------------------------------------------------------------- holidays


def _holidays_for_hebrew_year(year: int, diaspora: bool = True) -> List[JewishHoliday]:
    """Every holiday of one Hebrew year, in date order."""
    out: List[JewishHoliday] = []

    def add(name: str, month: str, day: int, restricted: bool = False, second: bool = False):
        out.append(
            JewishHoliday(
                name=name,
                date=to_gregorian(year, month, day),
                hebrew_year=year,
                work_restricted=restricted,
                second_day=second,
            )
        )

    # Tishrei: the High Holidays and Sukkot.
    add("Rosh Hashanah", "Tishrei", 1, restricted=True)
    # Rosh Hashanah is two days everywhere, Israel included.
    add("Rosh Hashanah (day 2)", "Tishrei", 2, restricted=True, second=True)
    add("Yom Kippur", "Tishrei", 10, restricted=True)
    add("Sukkot", "Tishrei", 15, restricted=True)
    if diaspora:
        add("Sukkot (day 2)", "Tishrei", 16, restricted=True, second=True)
    for offset, day in enumerate(range(17 if diaspora else 16, 22), start=1):
        add(f"Sukkot (Chol HaMoed {offset})", "Tishrei", day)
    add("Shemini Atzeret", "Tishrei", 22, restricted=True)
    if diaspora:
        add("Simchat Torah", "Tishrei", 23, restricted=True, second=True)

    # Kislev/Tevet: Hanukkah runs eight days and can cross a month boundary.
    first_night = to_gregorian(year, "Kislev", 25)
    for offset in range(8):
        out.append(
            JewishHoliday(
                name=f"Hanukkah (day {offset + 1})",
                date=first_night + dt.timedelta(days=offset),
                hebrew_year=year,
            )
        )

    add("Tu BiShvat", "Shevat", 15)

    # Adar: Purim, which moves to Adar II in a leap year.
    adar = "Adar II" if is_leap_year(year) else "Adar"
    add("Purim", adar, 14)
    add("Shushan Purim", adar, 15)

    # Nisan: Pesach.
    add("Pesach", "Nisan", 15, restricted=True)
    if diaspora:
        add("Pesach (day 2)", "Nisan", 16, restricted=True, second=True)
    for offset, day in enumerate(range(17 if diaspora else 16, 21), start=1):
        add(f"Pesach (Chol HaMoed {offset})", "Nisan", day)
    add("Pesach (day 7)", "Nisan", 21, restricted=True)
    if diaspora:
        add("Pesach (day 8)", "Nisan", 22, restricted=True, second=True)

    add("Lag BaOmer", "Iyar", 18)

    # Sivan: Shavuot.
    add("Shavuot", "Sivan", 6, restricted=True)
    if diaspora:
        add("Shavuot (day 2)", "Sivan", 7, restricted=True, second=True)

    # Av: Tisha B'Av, deferred a day when it would fall on Shabbat.
    tisha_av = to_gregorian(year, "Av", 9)
    if tisha_av.weekday() == 5:  # Saturday
        tisha_av += dt.timedelta(days=1)
    out.append(JewishHoliday(name="Tisha B'Av", date=tisha_av, hebrew_year=year))

    out.sort(key=lambda h: (h.date, h.name))
    return out


def holidays(
    gregorian_year: int,
    diaspora: bool = True,
    work_restricted_only: bool = False,
) -> List[JewishHoliday]:
    """Jewish holidays falling inside a Gregorian year.

    A Gregorian year overlaps two Hebrew years, so both are resolved and the
    results clipped. Set ``work_restricted_only`` for just the days work is
    traditionally prohibited -- the ones that actually have to come out of a
    leave balance.
    """
    found: List[JewishHoliday] = []
    for hebrew_year in (gregorian_year + 3760, gregorian_year + 3761):
        for holiday in _holidays_for_hebrew_year(hebrew_year, diaspora):
            if holiday.date.year == gregorian_year:
                if work_restricted_only and not holiday.work_restricted:
                    continue
                found.append(holiday)
    found.sort(key=lambda h: (h.date, h.name))
    return found

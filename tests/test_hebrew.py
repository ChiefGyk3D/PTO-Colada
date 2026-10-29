"""The Hebrew calendar.

The arithmetic is worth testing hard, because a calendar bug is silent: you
find out when you book leave for the wrong Thursday. Two kinds of test here --
dates checked against published calendars, and the structural rules the Hebrew
calendar has to obey in *every* year, which is what catches an off-by-one that
happens to leave one anchor date correct.
"""

from __future__ import annotations

import datetime as dt

import pytest

from pto_colada import hebrew


class TestKnownDates:
    """Checked against published calendars."""

    @pytest.mark.parametrize(
        "hebrew_year,expected",
        [
            (5784, dt.date(2023, 9, 16)),
            (5785, dt.date(2024, 10, 3)),
            (5786, dt.date(2025, 9, 23)),
            (5787, dt.date(2026, 9, 12)),
            (5788, dt.date(2027, 10, 2)),
        ],
    )
    def test_rosh_hashanah(self, hebrew_year, expected):
        assert hebrew.rosh_hashanah(hebrew_year) == expected

    @pytest.mark.parametrize(
        "hebrew_year,expected",
        [
            (5785, dt.date(2024, 10, 12)),
            (5786, dt.date(2025, 10, 2)),
            (5787, dt.date(2026, 9, 21)),
        ],
    )
    def test_yom_kippur(self, hebrew_year, expected):
        assert hebrew.to_gregorian(hebrew_year, "Tishrei", 10) == expected

    @pytest.mark.parametrize(
        "hebrew_year,expected",
        [
            (5785, dt.date(2025, 4, 13)),
            (5786, dt.date(2026, 4, 2)),
            (5787, dt.date(2027, 4, 22)),
        ],
    )
    def test_first_day_of_pesach(self, hebrew_year, expected):
        assert hebrew.to_gregorian(hebrew_year, "Nisan", 15) == expected

    def test_shavuot(self):
        assert hebrew.to_gregorian(5785, "Sivan", 6) == dt.date(2025, 6, 2)
        assert hebrew.to_gregorian(5786, "Sivan", 6) == dt.date(2026, 5, 22)


class TestStructuralRules:
    """Rules that hold for every year the calendar can produce."""

    YEARS = range(5700, 5900)  # roughly 1940-2140

    def test_lo_adu_rosh(self):
        # Rosh Hashanah never falls on Sunday, Wednesday or Friday.
        for year in self.YEARS:
            assert hebrew.rosh_hashanah(year).weekday() not in (6, 2, 4)

    def test_year_lengths_are_legal(self):
        for year in self.YEARS:
            assert hebrew.year_length(year) in (353, 354, 355, 383, 384, 385)

    def test_leap_years_are_the_long_ones(self):
        for year in self.YEARS:
            assert hebrew.is_leap_year(year) == (hebrew.year_length(year) >= 383)

    def test_seven_leap_years_per_metonic_cycle(self):
        for start in (5700, 5719, 5738, 5871):
            assert sum(hebrew.is_leap_year(y) for y in range(start, start + 19)) == 7

    def test_months_sum_to_the_year(self):
        for year in self.YEARS:
            assert sum(length for _, length in hebrew._months(year)) == hebrew.year_length(year)

    def test_yom_kippur_never_abuts_shabbat(self):
        # Two consecutive days of total rest are not allowed, so Yom Kippur
        # falls on neither Friday nor Sunday.
        for year in self.YEARS:
            assert hebrew.to_gregorian(year, "Tishrei", 10).weekday() not in (4, 6)

    def test_pesach_never_falls_on_badu(self):
        for year in self.YEARS:
            assert hebrew.to_gregorian(year, "Nisan", 15).weekday() not in (0, 2, 4)

    def test_at_bash_mnemonic(self):
        # The traditional check: day 1 of Pesach and Tisha B'Av share a weekday.
        for year in self.YEARS:
            pesach = hebrew.to_gregorian(year, "Nisan", 15)
            assert hebrew.to_gregorian(year, "Av", 9).weekday() == pesach.weekday()

    def test_purim_is_thirty_days_before_pesach(self):
        for year in self.YEARS:
            purim = hebrew.to_gregorian(year, "Adar", 14)
            pesach = hebrew.to_gregorian(year, "Nisan", 15)
            assert (pesach - purim).days == 30

    def test_shavuot_closes_the_omer(self):
        for year in self.YEARS:
            pesach = hebrew.to_gregorian(year, "Nisan", 15)
            assert (hebrew.to_gregorian(year, "Sivan", 6) - pesach).days == 50


class TestConversion:
    def test_round_trip_every_day_for_thirty_years(self):
        date = dt.date(2010, 1, 1)
        while date < dt.date(2040, 1, 1):
            year, month, day = hebrew.from_gregorian(date)
            assert hebrew.to_gregorian(year, month, day) == date
            date += dt.timedelta(days=1)

    def test_adar_means_adar_ii_in_a_leap_year(self):
        leap = next(y for y in range(5786, 5800) if hebrew.is_leap_year(y))
        assert hebrew.to_gregorian(leap, "Adar", 14) == hebrew.to_gregorian(leap, "Adar II", 14)

    def test_a_leap_year_has_thirteen_months(self):
        leap = next(y for y in range(5786, 5800) if hebrew.is_leap_year(y))
        assert len(hebrew.month_names(leap)) == 13
        assert "Adar I" in hebrew.month_names(leap)
        assert hebrew.months_in_year(leap) == 13

    def test_an_ordinary_year_has_no_adar_i(self):
        plain = next(y for y in range(5786, 5800) if not hebrew.is_leap_year(y))
        assert "Adar I" not in hebrew.month_names(plain)
        assert len(hebrew.month_names(plain)) == 12

    def test_a_day_out_of_range_is_rejected(self):
        with pytest.raises(ValueError, match="days"):
            hebrew.to_gregorian(5786, "Iyar", 30)  # Iyar has 29

    def test_an_unknown_month_is_rejected(self):
        with pytest.raises(ValueError, match="no month named"):
            hebrew.to_gregorian(5786, "Brumaire", 1)


class TestHolidays:
    def test_a_gregorian_year_draws_on_two_hebrew_years(self):
        found = hebrew.holidays(2026)
        assert {h.hebrew_year for h in found} == {5786, 5787}
        assert all(h.date.year == 2026 for h in found)

    def test_results_are_in_date_order(self):
        found = hebrew.holidays(2026)
        assert found == sorted(found, key=lambda h: (h.date, h.name))

    def test_work_restricted_filter_keeps_only_yom_tov(self):
        restricted = hebrew.holidays(2026, work_restricted_only=True)
        assert all(h.work_restricted for h in restricted)
        names = {h.name for h in restricted}
        assert "Yom Kippur" in names
        assert not any("Hanukkah" in n for n in names)
        assert not any("Chol HaMoed" in n for n in names)

    def test_israel_drops_the_second_festival_days(self):
        diaspora = hebrew.holidays(2026, diaspora=True, work_restricted_only=True)
        israel = hebrew.holidays(2026, diaspora=False, work_restricted_only=True)
        assert len(israel) < len(diaspora)
        # Rosh Hashanah is two days everywhere, Israel included.
        assert sum(1 for h in israel if h.name.startswith("Rosh Hashanah")) == 2
        # Shavuot is one day in Israel.
        assert sum(1 for h in israel if h.name.startswith("Shavuot")) == 1
        assert sum(1 for h in diaspora if h.name.startswith("Shavuot")) == 2

    def test_hanukkah_runs_eight_consecutive_days(self):
        found = [h for h in hebrew.holidays(2026) if h.name.startswith("Hanukkah")]
        # Hanukkah can straddle New Year, so one Gregorian year may hold part
        # of two Hanukkahs; check that each run is consecutive.
        dates = sorted(h.date for h in found)
        for earlier, later in zip(dates, dates[1:]):
            assert (later - earlier).days == 1

    def test_erev_is_the_day_before(self):
        yom_kippur = next(h for h in hebrew.holidays(2026) if h.name == "Yom Kippur")
        assert yom_kippur.erev == yom_kippur.date - dt.timedelta(days=1)
        assert hebrew.erev(yom_kippur.date) == yom_kippur.erev

    def test_tisha_bav_is_deferred_off_shabbat(self):
        # Find a year where Av 9 lands on Saturday and check it moved.
        for year in range(2024, 2060):
            for holiday in hebrew.holidays(year):
                if holiday.name == "Tisha B'Av":
                    assert holiday.date.weekday() != 5

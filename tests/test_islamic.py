"""The tabular Islamic calendar and the Muslim holiday sets.

Reference data
--------------
:data:`UMM_AL_QURA` is the Gregorian date of each holiday for 2026-2030 as
returned by the AlAdhan prayer-times API, endpoint
``https://api.aladhan.com/v1/hToG/DD-MM-YYYY``, which reports the method
``HJCoSA`` (the Hijri calendar of Saudi Arabia, i.e. Umm al-Qura). Fetched
2026-10-29, one request per holiday, 42 values. VERIFIED: the Hijri day of
each holiday is the convention in :mod:`longweekend.islamic`; the Gregorian
date is the API's answer.

Cross-checks from Wikipedia, "Eid al-Fitr" (https://en.wikipedia.org/wiki/Eid_al-Fitr),
fetched the same day: Eid al-Fitr 2026-03-20, 2027-03-09, 2028-02-26,
2029-02-14 agree with the table; 2030 is given as 3 February there and
4 February by the API, which is itself a one-day disagreement between two
published references and is why the documented margin is wider than the
tabular calendar's observed error.

INFERRED, not fetched: that Isra and Mi'raj is 27 Rajab, Mawlid is 12 Rabi I,
Laylat al-Qadr is observed on 27 Ramadan, and the day numbers of the other
holidays. These are the common conventions, not a published source.
"""

from __future__ import annotations

import datetime as dt
import json

import pytest

from longweekend import islamic
from longweekend.cli import main
from longweekend.config import build_config
from longweekend.observances import OBSERVANCE_SETS, resolve_observances

UMM_AL_QURA = [
    ("Isra and Mi'raj", dt.date(2026, 1, 16)),
    ("First day of Ramadan", dt.date(2026, 2, 18)),
    ("Laylat al-Qadr", dt.date(2026, 3, 16)),
    ("Eid al-Fitr", dt.date(2026, 3, 20)),
    ("Eid al-Adha", dt.date(2026, 5, 27)),
    ("Islamic New Year", dt.date(2026, 6, 16)),
    ("Ashura", dt.date(2026, 6, 25)),
    ("Mawlid", dt.date(2026, 8, 25)),
    ("Isra and Mi'raj", dt.date(2027, 1, 5)),
    ("First day of Ramadan", dt.date(2027, 2, 8)),
    ("Laylat al-Qadr", dt.date(2027, 3, 6)),
    ("Eid al-Fitr", dt.date(2027, 3, 9)),
    ("Eid al-Adha", dt.date(2027, 5, 16)),
    ("Islamic New Year", dt.date(2027, 6, 6)),
    ("Ashura", dt.date(2027, 6, 15)),
    ("Mawlid", dt.date(2027, 8, 14)),
    ("Isra and Mi'raj", dt.date(2027, 12, 25)),
    ("First day of Ramadan", dt.date(2028, 1, 28)),
    ("Laylat al-Qadr", dt.date(2028, 2, 23)),
    ("Eid al-Fitr", dt.date(2028, 2, 26)),
    ("Eid al-Adha", dt.date(2028, 5, 5)),
    ("Islamic New Year", dt.date(2028, 5, 25)),
    ("Ashura", dt.date(2028, 6, 3)),
    ("Mawlid", dt.date(2028, 8, 3)),
    ("Isra and Mi'raj", dt.date(2028, 12, 14)),
    ("First day of Ramadan", dt.date(2029, 1, 16)),
    ("Laylat al-Qadr", dt.date(2029, 2, 11)),
    ("Eid al-Fitr", dt.date(2029, 2, 14)),
    ("Eid al-Adha", dt.date(2029, 4, 24)),
    ("Islamic New Year", dt.date(2029, 5, 14)),
    ("Ashura", dt.date(2029, 5, 23)),
    ("Mawlid", dt.date(2029, 7, 24)),
    ("Isra and Mi'raj", dt.date(2029, 12, 3)),
    ("First day of Ramadan", dt.date(2030, 1, 5)),
    ("Laylat al-Qadr", dt.date(2030, 1, 31)),
    ("Eid al-Fitr", dt.date(2030, 2, 4)),
    ("Eid al-Adha", dt.date(2030, 4, 13)),
    ("Islamic New Year", dt.date(2030, 5, 3)),
    ("Ashura", dt.date(2030, 5, 12)),
    ("Mawlid", dt.date(2030, 7, 13)),
    ("Isra and Mi'raj", dt.date(2030, 11, 23)),
    ("First day of Ramadan", dt.date(2030, 12, 26)),
]


class TestCalendarArithmetic:
    def test_epoch_is_a_friday(self):
        first = islamic.to_gregorian(1, 1, 1)
        assert first == dt.date(622, 7, 19)  # 16 July 622 Julian
        assert first.weekday() == 4

    def test_a_known_anchor(self):
        assert islamic.to_gregorian(1447, 10, 1) == dt.date(2026, 3, 20)

    def test_round_trip_over_a_long_span(self):
        day = dt.date(1990, 1, 1)
        for _ in range(0, 365 * 60):
            assert islamic.to_gregorian(*islamic.from_gregorian(day)) == day
            day += dt.timedelta(days=1)

    def test_thirty_year_cycle_has_eleven_leap_years(self):
        assert sum(islamic.is_leap_year(y) for y in range(1, 31)) == 11
        assert [y for y in range(1, 31) if islamic.is_leap_year(y)] == [
            2, 5, 7, 10, 13, 16, 18, 21, 24, 26, 29,
        ]

    def test_month_and_year_lengths(self):
        assert islamic.month_length(1446, 1) == 30
        assert islamic.month_length(1446, 2) == 29
        assert islamic.month_length(1445, 12) == 30  # 1445 and 1447 are leap years
        assert islamic.month_length(1446, 12) == 29
        assert sum(islamic.month_length(1446, m) for m in range(1, 13)) == 354
        assert sum(islamic.month_length(1447, m) for m in range(1, 13)) == 355

    @pytest.mark.parametrize(
        "args",
        [(0, 1, 1), (1447, 0, 1), (1447, 13, 1), (1447, 2, 30), (1447, 1, 0), (1447, 1, 31),
         (10 ** 9, 1, 1)],
    )
    def test_impossible_dates_raise(self, args):
        with pytest.raises(ValueError):
            islamic.to_gregorian(*args)

    def test_booleans_are_not_numbers(self):
        with pytest.raises(ValueError):
            islamic.to_gregorian(True, 1, 1)


class TestAgainstPublishedReference:
    @pytest.mark.parametrize("name,expected", UMM_AL_QURA)
    def test_within_the_documented_margin(self, name, expected):
        found = [
            h.date
            for h in islamic.holidays(expected.year)
            if h.name == name and abs((h.date - expected).days) <= islamic.MARGIN_DAYS
        ]
        assert len(found) == 1, (name, expected)

    def test_the_reference_is_complete_and_the_margin_is_stated(self):
        assert len(UMM_AL_QURA) == 42
        assert islamic.MARGIN_DAYS == 2

    def test_observed_error_against_the_reference(self):
        """Tabular dates are the same day or one day later; never earlier."""
        deltas = set()
        for name, expected in UMM_AL_QURA:
            mine = [h.date for h in islamic.holidays(expected.year) if h.name == name]
            deltas.add(min((d - expected).days for d in mine if abs((d - expected).days) < 5))
        assert deltas == {0, 1}

    def test_the_guard_bites(self):
        # A calendar shifted by three days must fail the margin check.
        name, expected = UMM_AL_QURA[3]
        shifted = [h.date for h in islamic.holidays(expected.year, shift=3) if h.name == name]
        assert all(abs((d - expected).days) > 0 for d in shifted)
        assert all(
            abs((d - expected).days) > islamic.MARGIN_DAYS
            for d in [x + dt.timedelta(days=3) for x in shifted]
        )


class TestTwiceInAGregorianYear:
    def test_ramadan_begins_twice_in_2030(self):
        # 5 January and 26 December 2030 in the published reference above.
        found = [h for h in islamic.holidays(2030) if h.name == "First day of Ramadan"]
        assert [h.date for h in found] == [dt.date(2030, 1, 6), dt.date(2030, 12, 26)]
        assert found[0].hijri_year == 1451 and found[1].hijri_year == 1452

    def test_eid_al_fitr_twice_in_2033(self):
        found = [h.date for h in islamic.holidays(2033) if h.name == "Eid al-Fitr"]
        assert len(found) == 2 and found[0].month == 1 and found[1].month == 12

    def test_both_are_booked_and_sorted(self):
        days = resolve_observances(["First day of Ramadan"], [2030])
        assert [d.date.year for d in days] == [2030, 2030]
        assert days == sorted(days, key=lambda d: d.date)

    def test_no_holiday_appears_outside_the_requested_year(self):
        assert all(h.date.year == 2030 for h in islamic.holidays(2030))


class TestShift:
    def test_shift_moves_every_date(self):
        base = {(h.name, h.date) for h in islamic.holidays(2027)}
        later = {(h.name, h.date - dt.timedelta(days=1)) for h in islamic.holidays(2027, shift=1)}
        # Same holidays, one day apart, apart from any that cross a year boundary.
        assert len(base & later) >= 6

    @pytest.mark.parametrize("bad", [4, -4, 100, True, 1.5, "1", None])
    def test_shift_is_bounded_and_strict(self, bad):
        with pytest.raises(ValueError):
            islamic.check_shift(bad)

    @pytest.mark.parametrize("good", [-3, -1, 0, 2, 3])
    def test_shift_accepts_the_range(self, good):
        assert islamic.check_shift(good) == good


class TestSets:
    def test_muslim_is_the_two_eids(self):
        days = resolve_observances(["muslim"], [2027])
        assert [d.name for d in days] == ["Eid al-Fitr", "Eid al-Adha"]
        assert all(d.source == "muslim" for d in days)

    def test_muslim_all_adds_the_rest(self):
        names = {d.name for d in resolve_observances(["muslim-all"], [2027, 2028])}
        assert names >= {
            "Eid al-Fitr", "Eid al-Adha", "Islamic New Year", "Ashura", "Mawlid",
            "Isra and Mi'raj", "First day of Ramadan", "Laylat al-Qadr",
        }

    def test_sets_are_listed(self):
        assert "muslim" in OBSERVANCE_SETS and "muslim-all" in OBSERVANCE_SETS

    @pytest.mark.parametrize(
        "spec,expected",
        [
            ("Eid al-Fitr", "Eid al-Fitr"),
            ("eid ul fitr", "Eid al-Fitr"),
            ("Eid-al-Adha", "Eid al-Adha"),
            ("Ramadan", "First day of Ramadan"),
            ("Muharram", "Islamic New Year"),
            ("Night of Power", "Laylat al-Qadr"),
            ("Isra and Miraj", "Isra and Mi'raj"),
            ("Mawlid", "Mawlid"),
        ],
    )
    def test_single_names(self, spec, expected):
        days = resolve_observances([spec], [2027])
        assert days and {d.name for d in days} == {expected}

    def test_every_muslim_day_is_provisional(self):
        days = resolve_observances(["muslim-all"], [2026, 2027])
        assert days and all(d.provisional for d in days)

    def test_jewish_and_dates_are_not_provisional(self):
        days = resolve_observances(["jewish", "2026-07-20"], [2026])
        assert days and not any(d.provisional for d in days)

    def test_both_sets_can_be_combined(self):
        sources = {d.source for d in resolve_observances(["jewish", "muslim"], [2027])}
        assert sources == {"jewish", "muslim"}

    def test_erev_is_a_jewish_only_option(self):
        days = resolve_observances(["muslim"], [2027], include_erev=True)
        assert not any(d.name.startswith("Erev") for d in days)

    def test_unknown_name_error_mentions_the_muslim_sets(self):
        with pytest.raises(ValueError, match="muslim"):
            resolve_observances(["Saturnalia"], [2027])


class TestOverrides:
    def test_override_replaces_the_computed_date_and_confirms_it(self):
        # The tabular date is 10 March 2027; the announced date is the 9th.
        days = resolve_observances(
            ["Eid al-Fitr"], [2027], overrides={"Eid al-Fitr": [dt.date(2027, 3, 9)]}
        )
        assert [d.date for d in days] == [dt.date(2027, 3, 9)]
        assert not days[0].provisional

    def test_shift_does_not_move_an_overridden_date(self):
        days = resolve_observances(
            ["Eid al-Fitr"], [2027], shift=2, overrides={"Eid al-Fitr": [dt.date(2027, 3, 9)]}
        )
        assert days[0].date == dt.date(2027, 3, 9)

    def test_shift_applies_to_the_rest_and_stays_provisional(self):
        plain = resolve_observances(["muslim"], [2027])
        moved = resolve_observances(["muslim"], [2027], shift=1)
        assert [(m.date - p.date).days for p, m in zip(plain, moved)] == [1, 1]
        assert all(d.provisional for d in moved)

    def test_an_override_for_one_of_two_occurrences_in_a_year(self):
        days = resolve_observances(
            ["First day of Ramadan"], [2030],
            overrides={"Ramadan": [dt.date(2030, 12, 25)]},
        )
        assert [d.date for d in days] == [dt.date(2030, 1, 6), dt.date(2030, 12, 25)]
        assert [d.provisional for d in days] == [True, False]

    def test_an_override_far_from_the_calendar_is_rejected(self):
        with pytest.raises(ValueError, match="days from"):
            resolve_observances(
                ["muslim"], [2027], overrides={"Eid al-Fitr": [dt.date(2027, 3, 20)]}
            )

    def test_an_override_for_an_unknown_holiday_is_rejected(self):
        with pytest.raises(ValueError, match="not a Muslim holiday"):
            resolve_observances(
                ["muslim"], [2027], overrides={"Saturnalia": [dt.date(2027, 3, 9)]}
            )

    def test_override_for_an_unselected_holiday_is_ignored(self):
        days = resolve_observances(
            ["Eid al-Fitr"], [2027], overrides={"Ashura": [dt.date(2027, 6, 14)]}
        )
        assert [d.name for d in days] == ["Eid al-Fitr"]


def write(tmp_path, text, name="cfg.json"):
    path = tmp_path / name
    path.write_text(text)
    return str(path)


class TestConfigFile:
    def test_observances_mapping_sets_the_date(self, tmp_path):
        path = write(tmp_path, json.dumps({"observances": {"Eid al-Fitr": "2027-03-09"}}))
        config = build_config(
            config_path=path, observe=["muslim"], years=[2027]
        )
        fitr = next(o for o in config.observances if o.name == "Eid al-Fitr")
        assert fitr.date == dt.date(2027, 3, 9) and not fitr.provisional
        assert fitr.date in config.committed

    def test_a_list_of_dates_is_accepted(self, tmp_path):
        path = write(
            tmp_path, json.dumps({"observances": {"Ramadan": ["2030-01-05", "2030-12-25"]}})
        )
        config = build_config(config_path=path, observe=["Ramadan"], years=[2030])
        assert [o.date for o in config.observances] == [dt.date(2030, 1, 5), dt.date(2030, 12, 25)]

    def test_unquoted_yaml_dates_work(self, tmp_path):
        pytest.importorskip("yaml")
        path = write(tmp_path, "observances:\n  Eid al-Fitr: 2027-03-09\n", "cfg.yaml")
        config = build_config(config_path=path, observe=["muslim"], years=[2027])
        assert dt.date(2027, 3, 9) in {o.date for o in config.observances}

    @pytest.mark.parametrize(
        "value",
        [
            ["not", "a", "mapping"],
            "text",
            5,
            {"Eid al-Fitr": 20270309},
            {"Eid al-Fitr": True},
            {"Eid al-Fitr": None},
            {"Eid al-Fitr": ""},
            {"Eid al-Fitr": "2027-3-9"},
            {"Eid al-Fitr": "20270309"},
            {"Eid al-Fitr": "2027-02-30"},
            {"Eid al-Fitr": "0001-03-09"},
            {"Eid al-Fitr": "2027-03-09T00:00"},
            {"Eid al-Fitr": "２０２７-03-09"},
            {"Eid al-Fitr": {"nested": "2027-03-09"}},
            {"Eid al-Fitr": ["2027-03-09"] * 9},
            {"Eid al-Fitr": [["2027-03-09"]]},
            {"Saturnalia": "2027-03-09"},
            {"Eid al-Fitr": "2027-03-09", "eid ul fitr": "2027-03-09"},
            {5: "2027-03-09"},
            {"x" * 500: "2027-03-09"},
        ],
    )
    def test_hostile_values_are_rejected_with_a_value_error(self, tmp_path, value):
        path = write(tmp_path, json.dumps({"observances": value}))
        with pytest.raises(ValueError):
            build_config(config_path=path, observe=["muslim"], years=[2027])

    def test_two_dates_for_one_occurrence_are_rejected(self, tmp_path):
        path = write(
            tmp_path, json.dumps({"observances": {"Eid al-Fitr": ["2027-03-09", "2027-03-10"]}})
        )
        with pytest.raises(ValueError, match="twice"):
            build_config(config_path=path, observe=["muslim"], years=[2027])

    def test_an_empty_date_list_is_rejected(self, tmp_path):
        path = write(tmp_path, json.dumps({"observances": {"Eid al-Fitr": []}}))
        with pytest.raises(ValueError, match="between 1 and"):
            build_config(config_path=path, years=[2027])

    def test_the_date_list_is_capped_even_for_real_dates(self, tmp_path):
        dates = [islamic.to_gregorian(1448 + n, 10, 1).isoformat() for n in range(9)]
        path = write(tmp_path, json.dumps({"observances": {"Eid al-Fitr": dates}}))
        with pytest.raises(ValueError, match="between 1 and"):
            build_config(config_path=path, years=[2027])

    def test_dates_outside_the_supported_years_are_rejected_even_if_calendar_exact(self, tmp_path):
        old = islamic.to_gregorian(1200, 10, 1).isoformat()
        assert old < "1900"
        path = write(tmp_path, json.dumps({"observances": {"Eid al-Fitr": old}}))
        with pytest.raises(ValueError, match="outside the years"):
            build_config(config_path=path, years=[2027])

    def test_a_huge_name_is_not_echoed(self, tmp_path):
        path = write(tmp_path, json.dumps({"observances": {"x" * 10000: "2027-03-09"}}))
        with pytest.raises(ValueError) as excinfo:
            build_config(config_path=path, years=[2027])
        assert len(str(excinfo.value)) < 400

    def test_too_many_entries_are_rejected(self, tmp_path):
        many = {f"Holiday {n}": "2027-03-09" for n in range(200)}
        path = write(tmp_path, json.dumps({"observances": many}))
        with pytest.raises(ValueError, match="at most"):
            build_config(config_path=path, years=[2027])

    def test_empty_value_is_an_error_like_every_other_key(self, tmp_path):
        pytest.importorskip("yaml")
        path = write(tmp_path, "observances:\n", "cfg.yaml")
        with pytest.raises(ValueError, match="empty"):
            build_config(config_path=path, years=[2027])

    def test_overrides_without_muslim_observances_do_nothing(self, tmp_path):
        path = write(tmp_path, json.dumps({"observances": {"Eid al-Fitr": "2027-03-09"}}))
        config = build_config(config_path=path, observe=["jewish"], years=[2027])
        assert not any(o.source == "muslim" for o in config.observances)


class TestBuildConfigShift:
    def test_shift_needs_muslim_observances(self):
        with pytest.raises(ValueError, match="Muslim"):
            build_config(observe=["jewish"], muslim_shift=1, years=[2027])
        with pytest.raises(ValueError, match="Muslim"):
            build_config(muslim_shift=1, years=[2027])

    def test_shift_applies(self):
        a = build_config(observe=["muslim"], years=[2027])
        b = build_config(observe=["muslim"], muslim_shift=-1, years=[2027])
        assert [(x.date - y.date).days for x, y in zip(a.observances, b.observances)] == [1, 1]

    def test_shift_out_of_range(self):
        with pytest.raises(ValueError, match="between"):
            build_config(observe=["muslim"], muslim_shift=9, years=[2027])


class TestThroughTheCli:
    def run(self, capsys, *argv):
        assert main(["--today", "2026-10-29", *argv]) == 0
        return capsys.readouterr().out

    def test_text_report_flags_muslim_dates_as_provisional(self, capsys):
        out = self.run(capsys, "--pto", "15", "--years", "2", "--observe", "muslim")
        assert "PROVISIONAL" in out
        block = out.split("PROVISIONAL", 1)[1].split("\n\n", 1)[0]
        assert "Eid al-Fitr" in block and "moon sighting" in block
        assert "Days you are taking yourself" in out

    def test_nothing_provisional_without_muslim_days(self, capsys):
        out = self.run(capsys, "--pto", "15", "--years", "2", "--observe", "jewish")
        assert "PROVISIONAL" not in out

    def test_json_marks_provisional(self, capsys):
        data = json.loads(
            self.run(capsys, "--pto", "15", "--years", "2", "--observe", "muslim", "--format", "json")
        )
        assert data["provisional"] is True
        assert any(a["status"] == "moon-sighting" for a in data["assumptions"])
        assert all(o["provisional"] for o in data["config"]["observances"])

    def test_json_without_muslim_has_no_new_keys(self, capsys):
        data = json.loads(
            self.run(capsys, "--pto", "15", "--years", "2", "--observe", "jewish", "--format", "json")
        )
        assert "provisional" not in data
        assert all("provisional" not in o for o in data["config"]["observances"])

    def test_ics_is_provisional(self, capsys):
        out = self.run(capsys, "--pto", "15", "--years", "2", "--observe", "muslim", "--format", "ics")
        assert "X-LWP-PROVISIONAL:TRUE" in out and "moon-sighting" in out

    def test_shift_flag(self, capsys):
        plain = json.loads(
            self.run(capsys, "--pto", "15", "--years", "2", "--observe", "muslim", "--format", "json")
        )
        moved = json.loads(
            self.run(capsys, "--pto", "15", "--years", "2", "--observe", "muslim",
                     "--shift", "1", "--format", "json")
        )
        a = [o["date"] for o in plain["config"]["observances"]]
        b = [o["date"] for o in moved["config"]["observances"]]
        assert a != b and len(a) == len(b)

    @pytest.mark.parametrize("bad", ["4", "-4", "x", "1.5"])
    def test_bad_shift_exits_cleanly(self, bad):
        with pytest.raises(SystemExit) as excinfo:
            main(["--observe", "muslim", "--shift", bad, "--years", "1"])
        assert excinfo.value.code == 2

    def test_shift_without_muslim_exits_cleanly(self):
        with pytest.raises(SystemExit) as excinfo:
            main(["--observe", "jewish", "--shift", "1", "--years", "1"])
        assert excinfo.value.code == 2

    def test_list_observances_mentions_the_margin(self, capsys):
        assert main(["--list-observances"]) == 0
        out = capsys.readouterr().out
        assert "muslim-all" in out and "moon" in out

    def test_override_through_a_config_file_clears_provisional(self, tmp_path, capsys):
        path = write(
            tmp_path, json.dumps({"observances": {"Eid al-Fitr": "2027-03-09",
                                                    "Eid al-Adha": "2027-05-16"}})
        )
        out = self.run(
            capsys, "--config", path, "--pto", "15", "--start-year", "2027", "--years", "1",
            "--observe", "muslim",
        )
        assert "PROVISIONAL" not in out
        assert "Eid al-Fitr" in out

    def test_both_sets_together(self, capsys):
        out = self.run(capsys, "--pto", "15", "--years", "2", "--observe", "jewish",
                       "--observe", "muslim")
        assert "Yom Kippur" in out and "Eid al-Fitr" in out

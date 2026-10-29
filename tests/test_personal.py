"""Days you take off yourself: observances, conferences, and the wizard."""

from __future__ import annotations

import datetime as dt
import json

import pytest

from longweekend.cli import build_parser, main
from longweekend.conferences import (
    find_conference,
    load_conferences,
    occurrences_for,
)
from longweekend.config import build_config
from longweekend.observances import OBSERVANCE_SETS, resolve_observances
from longweekend.planner import PtoBudget, optimize_pto
from longweekend.schedule import DayKind, build_days
from longweekend.catalog import PRESETS, resolve_year


class TestResolvingObservances:
    def test_a_named_set_resolves_across_the_window(self):
        found = resolve_observances(["jewish"], [2026, 2027])
        years = {o.date.year for o in found}
        assert years == {2026, 2027}
        assert all(o.source == "jewish" for o in found)

    def test_yom_kippur_2026(self):
        found = resolve_observances(["Yom Kippur"], [2026])
        assert [o.date for o in found] == [dt.date(2026, 9, 21)]

    def test_naming_a_festival_books_only_the_days_work_is_prohibited(self):
        # Chol HaMoed is work-permitted, so "Passover" should not spend leave
        # on it. Ask for jewish-all if you want the whole span.
        pesach = resolve_observances(["Passover"], [2026])
        assert [o.date for o in pesach] == [
            dt.date(2026, 4, 2),
            dt.date(2026, 4, 3),
            dt.date(2026, 4, 8),
            dt.date(2026, 4, 9),
        ]

    def test_a_minor_holiday_comes_through_whole(self):
        # Hanukkah has no work-restricted days at all, so all eight survive.
        found = resolve_observances(["Hanukkah"], [2026])
        assert len(found) >= 8

    def test_aliases(self):
        assert resolve_observances(["Passover"], [2026]) == resolve_observances(
            ["Pesach"], [2026]
        )
        assert resolve_observances(["Chanukah"], [2026]) == resolve_observances(
            ["Hanukkah"], [2026]
        )

    def test_an_explicit_date_needs_no_calendar(self):
        found = resolve_observances(["2026-07-20"], [2026])
        assert [o.date for o in found] == [dt.date(2026, 7, 20)]
        assert found[0].source == "date"

    def test_specs_can_be_comma_separated(self):
        found = resolve_observances(["Yom Kippur,2026-07-20"], [2026])
        assert len(found) == 2

    def test_results_are_sorted_and_deduplicated(self):
        found = resolve_observances(["jewish", "Yom Kippur"], [2026])
        dates = [o.date for o in found]
        assert dates == sorted(dates)
        assert len(dates) == len(set(dates))

    def test_israel_drops_second_days(self):
        diaspora = resolve_observances(["jewish"], [2026], diaspora=True)
        israel = resolve_observances(["jewish"], [2026], diaspora=False)
        assert len(israel) < len(diaspora)

    def test_high_holidays_only(self):
        found = resolve_observances(["jewish-high-holidays"], [2026])
        assert not any("Pesach" in o.name for o in found)
        assert any(o.name == "Yom Kippur" for o in found)

    def test_erev_never_collides_with_another_observance(self):
        found = resolve_observances(["jewish"], [2026], include_erev=True)
        dates = [o.date for o in found]
        assert len(dates) == len(set(dates))
        # The evening before day 2 of a festival is day 1, already booked.
        assert not any(o.name.startswith("Erev Pesach (day 2)") for o in found)

    def test_erev_precedes_the_first_day(self):
        found = resolve_observances(["Yom Kippur"], [2026], include_erev=True)
        assert [o.date for o in found] == [dt.date(2026, 9, 20), dt.date(2026, 9, 21)]

    def test_an_unknown_observance_lists_the_alternatives(self):
        with pytest.raises(ValueError, match="do not know the observance"):
            resolve_observances(["Saturnalia"], [2026])

    @pytest.mark.parametrize("name", sorted(OBSERVANCE_SETS))
    def test_every_advertised_set_resolves(self, name):
        assert resolve_observances([name], [2026])


class TestObservancesInThePlan:
    def days(self, year=2026):
        holidays = resolve_year(PRESETS["federal"], year)
        return build_days(dt.date(year, 1, 1), dt.date(year, 12, 31), holidays)

    def test_observances_are_always_taken(self):
        config = build_config(pto=15, observe=["jewish"], years=[2026])
        plan = optimize_pto(
            self.days(), PtoBudget(default=15), committed=config.committed
        )
        taken = set(plan.pto_dates)
        for date in config.committed:
            day = next(d for d in plan.days if d.date == date)
            # Either it cost leave, or it was free because nobody works that day.
            assert date in taken or day.kind in (DayKind.WEEKEND, DayKind.HOLIDAY)

    def test_an_observance_on_a_weekend_costs_nothing(self):
        # Rosh Hashanah 2026 falls on a Saturday.
        config = build_config(pto=5, observe=["Rosh Hashanah"], years=[2026])
        plan = optimize_pto(self.days(), PtoBudget(default=5), committed=config.committed)
        assert dt.date(2026, 9, 12) not in plan.pto_dates

    def test_the_planner_bridges_from_an_observance(self):
        # Yom Kippur 2026 is a Monday: the weekend before comes free, and the
        # planner should notice it can be extended.
        config = build_config(pto=10, observe=["Yom Kippur"], years=[2026])
        plan = optimize_pto(self.days(), PtoBudget(default=10), committed=config.committed)
        covering = [b for b in plan.breaks if b.start <= dt.date(2026, 9, 21) <= b.end]
        assert covering and covering[0].length >= 4

    def test_committing_more_than_the_budget_says_how_short_you_are(self):
        with pytest.raises(ValueError, match="Raise the budget by"):
            config = build_config(pto=2, observe=["jewish"], years=[2026])
            optimize_pto(self.days(), PtoBudget(default=2), committed=config.committed)


class TestConferences:
    def test_the_bundled_catalog_loads(self):
        catalog = load_conferences()
        assert {c.name for c in catalog} >= {"DEF CON", "Black Hat USA", "RSA Conference"}

    @pytest.mark.parametrize(
        "query,expected",
        [
            ("defcon", "DEF CON"),
            ("DEF CON", "DEF CON"),
            ("dc", "DEF CON"),
            ("black hat", "Black Hat USA"),
            ("ccc", "Chaos Communication Congress"),
            ("rsac", "RSA Conference"),
        ],
    )
    def test_lookup_by_name_or_alias(self, query, expected):
        assert find_conference(query, load_conferences()).name == expected

    def test_an_unknown_conference_lists_the_known_ones(self):
        with pytest.raises(ValueError, match="unknown conference"):
            find_conference("Nonexistent Con", load_conferences())

    def test_announced_dates_are_reported_as_confirmed(self):
        defcon = find_conference("DEF CON", load_conferences())
        occurrence = occurrences_for(defcon, [2026])[0]
        assert occurrence.confirmed
        assert (occurrence.start, occurrence.end) == (dt.date(2026, 8, 6), dt.date(2026, 8, 9))
        assert occurrence.length == 4

    def test_a_year_with_no_announcement_is_marked_estimated(self):
        defcon = find_conference("DEF CON", load_conferences())
        occurrence = occurrences_for(defcon, [2029])[0]
        assert not occurrence.confirmed
        assert "estimated" in occurrence.label()

    def test_no_recurrence_rule_means_no_guess(self):
        # RSAC moves around the spring; better to say nothing than invent it.
        rsac = find_conference("RSA Conference", load_conferences())
        assert occurrences_for(rsac, [2029]) == []

    def test_the_congress_is_always_the_same_four_days(self):
        ccc = find_conference("ccc", load_conferences())
        for occurrence in occurrences_for(ccc, range(2027, 2035)):
            assert (occurrence.start.month, occurrence.start.day) == (12, 27)
            assert occurrence.length == 4

    def test_a_user_file_overrides_the_bundled_catalog(self, tmp_path):
        path = tmp_path / "mine.json"
        path.write_text(
            json.dumps(
                {
                    "conferences": [
                        {"name": "DEF CON", "dates": {"2026": ["2026-09-01", "2026-09-02"]}},
                        {"name": "My Local Con", "dates": {"2026": ["2026-05-04", "2026-05-05"]}},
                    ]
                }
            ),
            encoding="utf-8",
        )
        catalog = load_conferences(str(path))
        defcon = find_conference("DEF CON", catalog)
        assert occurrences_for(defcon, [2026])[0].start == dt.date(2026, 9, 1)
        assert find_conference("My Local Con", catalog)

    def test_a_malformed_user_file_is_reported(self, tmp_path):
        path = tmp_path / "bad.json"
        path.write_text(json.dumps({"conferences": [{"city": "nowhere"}]}), encoding="utf-8")
        with pytest.raises(ValueError, match="needs a 'name'"):
            load_conferences(str(path))

    def test_conference_days_become_committed_leave(self):
        config = build_config(pto=15, conferences=["DEF CON"], years=[2026])
        assert dt.date(2026, 8, 6) in config.committed
        assert len(config.conferences) == 1

    def test_travel_days_extend_the_booking(self):
        config = build_config(
            pto=15, conferences=["DEF CON"], conference_travel=1, years=[2026]
        )
        assert dt.date(2026, 8, 5) in config.committed
        assert dt.date(2026, 8, 10) in config.committed

    def test_negative_travel_is_rejected(self):
        with pytest.raises(ValueError, match="travel days cannot be negative"):
            build_config(conferences=["DEF CON"], conference_travel=-1, years=[2026])


class TestThroughTheCli:
    def test_observances_appear_in_the_report(self, capsys):
        assert main(["--pto", "15", "--observe", "Yom Kippur", "--years", "1"]) == 0
        out = capsys.readouterr().out
        assert "Days you are taking yourself" in out
        assert "Yom Kippur" in out

    def test_the_headline_leads_with_the_answer(self, capsys):
        main(["--pto", "15", "--years", "1"])
        out = capsys.readouterr().out
        assert "You get" in out and "days off across" in out
        assert "for every day you book" in out

    def test_json_carries_observances_and_conferences(self, capsys):
        main([
            "--pto", "15", "--observe", "Yom Kippur", "--conference", "DEF CON",
            "--years", "1", "--format", "json",
        ])
        payload = json.loads(capsys.readouterr().out)
        assert payload["config"]["observances"][0]["name"] == "Yom Kippur"
        conference = payload["config"]["conferences"][0]
        assert conference["name"] == "DEF CON" and conference["confirmed"] is True

    def test_list_observances(self, capsys):
        assert main(["--list-observances"]) == 0
        assert "jewish" in capsys.readouterr().out

    def test_list_conferences(self, capsys):
        assert main(["--list-conferences"]) == 0
        out = capsys.readouterr().out
        assert "DEF CON" in out and "estimated" in out

    def test_israel_flag_changes_the_plan(self, capsys):
        main(["--pto", "15", "--observe", "jewish", "--israel", "--years", "1",
              "--format", "json"])
        israel = json.loads(capsys.readouterr().out)
        main(["--pto", "15", "--observe", "jewish", "--years", "1", "--format", "json"])
        diaspora = json.loads(capsys.readouterr().out)
        assert len(israel["config"]["observances"]) < len(diaspora["config"]["observances"])

    def test_an_unknown_observance_exits_cleanly(self):
        with pytest.raises(SystemExit) as excinfo:
            main(["--observe", "Saturnalia", "--years", "1"])
        assert excinfo.value.code == 2


class TestWizard:
    """The wizard's real job is printing the command you should have typed."""

    def run(self, answers):
        from longweekend.wizard import run_wizard

        parser = build_parser()
        args = parser.parse_args([])
        replies = iter(answers)
        said = []
        argv = run_wizard(args, ask=lambda _: next(replies, ""), say=said.append)
        return argv, "\n".join(said)

    def test_defaults_produce_a_working_command(self):
        argv, output = self.run([""] * 10)
        assert "--pto" in argv
        assert main(argv) == 0
        assert "pto-colada" in output

    def test_answers_become_flags(self):
        argv, _ = self.run(["20", "3", "5", "corporate", "Yom Kippur", "DEF CON", "2", "vacations", "3"])
        assert argv[:2] == ["--pto", "20"]
        assert "--floating" in argv and "3" in argv
        assert "--preset" in argv and "corporate" in argv
        assert "--observe" in argv and "Yom Kippur" in argv
        assert "--conference" in argv and "DEF CON" in argv
        assert "--style" in argv and "vacations" in argv
        assert main(argv) == 0

    def test_blank_optional_answers_are_omitted(self):
        argv, _ = self.run(["15", "", "", "", "", "", "", ""])
        assert "--floating" not in argv
        assert "--observe" not in argv
        assert "--conference" not in argv

    def test_a_bad_number_is_asked_again(self):
        argv, _ = self.run(["not a number", "12", "", "", "", "", "", "", ""])
        assert argv[:2] == ["--pto", "12"]

    def test_it_prints_the_equivalent_command(self):
        _, output = self.run(["20"] + [""] * 9)
        assert "Next time, skip the questions:" in output
        assert "pto-colada --pto 20" in output

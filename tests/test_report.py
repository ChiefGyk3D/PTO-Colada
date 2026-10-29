"""Output rendering."""

from __future__ import annotations

import datetime as dt
import hashlib
import re

import pytest

from longweekend.catalog import PRESETS, resolve_year
from longweekend.config import build_config
from longweekend.planner import PtoBudget, optimize_pto
from longweekend.report import _event, _fold, render_ics, render_json, render_text
from longweekend.schedule import build_days, find_opportunities


@pytest.fixture
def fixtures():
    config = build_config(preset="federal", pto=12)
    holidays = []
    for year in (2025, 2026, 2027):
        holidays.extend(resolve_year(config.holidays, year, config.workweek, config.observance))
    days = build_days(dt.date(2026, 1, 1), dt.date(2026, 12, 31), holidays, config.workweek)
    by_year = {2026: [h for h in holidays if h.observed.year == 2026]}
    opportunities = {2026: find_opportunities(days)}
    plan = optimize_pto(days, PtoBudget(default=12))
    return config, by_year, opportunities, plan


class TestFold:
    def test_short_lines_are_untouched(self):
        assert _fold("SUMMARY:PTO") == "SUMMARY:PTO"

    def test_long_lines_fold_to_75_octets(self):
        line = "SUMMARY:" + "x" * 200
        pieces = _fold(line).split("\r\n")
        assert len(pieces) > 1
        assert all(len(p.encode()) <= 75 for p in pieces)

    def test_folding_is_reversible(self):
        line = "DESCRIPTION:" + "abcdefghij" * 30
        pieces = _fold(line).split("\r\n")
        assert pieces[0] + "".join(p[1:] for p in pieces[1:]) == line

    def test_multibyte_characters_are_not_split(self):
        line = "SUMMARY:" + "é" * 100
        pieces = _fold(line).split("\r\n")
        assert all(len(p.encode()) <= 75 for p in pieces)
        assert pieces[0] + "".join(p[1:] for p in pieces[1:]) == line


class TestRender:
    def test_text_includes_holidays_and_plan(self, fixtures):
        text = render_text(*fixtures)
        assert "Juneteenth" in text
        assert "PTO day(s) used in 2026" in text

    def test_text_can_omit_holidays(self, fixtures):
        config, by_year, opportunities, plan = fixtures
        text = render_text(config, by_year, opportunities, plan, show_holidays=False)
        assert "Holidays" not in text

    def test_text_notes_a_shifted_observance(self, fixtures):
        # Independence Day 2026 is a Saturday.
        assert "falls on Sat Jul 04" in render_text(*fixtures)

    def test_json_round_trips(self, fixtures):
        import json

        payload = json.loads(render_json(*fixtures))
        assert payload["config"]["name"] == "federal"
        assert payload["plan"]["total_days_off"] > 0
        for item in payload["plan"]["breaks"]:
            assert item["length"] >= payload["plan"]["min_break"]

    def test_ics_uses_crlf_and_balanced_events(self, fixtures):
        config, by_year, _, plan = fixtures
        ics = render_ics(config, by_year, plan)
        assert ics.startswith("BEGIN:VCALENDAR\r\n")
        assert ics.count("BEGIN:VEVENT") == ics.count("END:VEVENT")
        assert all(len(line.encode()) <= 75 for line in ics.split("\r\n"))

    def test_ics_escapes_special_characters(self):
        config = build_config(preset="minimal", add=["Founders; Day, Really=06-01"])
        holidays = resolve_year(config.holidays, 2026, config.workweek, config.observance)
        ics = render_ics(config, {2026: holidays})
        assert r"Founders\; Day\, Really" in ics

    def test_ics_can_omit_holidays(self, fixtures):
        config, by_year, _, plan = fixtures
        ics = render_ics(config, by_year, plan, include_holidays=False)
        assert "CATEGORIES:HOLIDAY" not in ics
        assert "CATEGORIES:PTO" in ics


class TestEndToEnd:
    def test_blackout_and_commitments_are_honoured(self):
        config = build_config(
            preset="federal",
            pto=15,
            blackout=["2026-09-28:2026-10-09"],
            committed=["2026-07-20:2026-07-24"],
        )
        holidays = []
        for year in (2025, 2026, 2027):
            holidays.extend(resolve_year(config.holidays, year, config.workweek, config.observance))
        days = build_days(
            dt.date(2026, 1, 1), dt.date(2026, 12, 31), holidays, config.workweek, config.blackout
        )
        plan = optimize_pto(
            days, PtoBudget(default=15), min_break=config.min_break, committed=config.committed
        )
        taken = set(plan.pto_dates)
        assert not (taken & config.blackout)
        # The committed week is Mon-Fri, so all five days are working days.
        assert config.committed <= taken
        assert plan.spent_by_year[2026] <= 15


def _uid(start, end, summary, category):
    lines = _event(start, end, summary, "desc", "20260101T000000Z", category)
    (uid_line,) = [l for l in lines if l.startswith("UID:")]
    return uid_line[len("UID:"):]


def test_event_uid_stable_distinct_and_shaped():
    a = dt.date(2026, 5, 22)
    b = dt.date(2026, 5, 26)
    base = _uid(a, b, "Memorial Day", "holiday")
    assert base == _uid(a, b, "Memorial Day", "holiday")
    assert re.fullmatch(r"[0-9a-f]{40}@long-weekend-planner", base)
    seed = "holiday|2026-05-22|2026-05-26|Memorial Day"
    assert base == hashlib.sha256(seed.encode("utf-8")).hexdigest()[:40] + "@long-weekend-planner"
    others = {
        _uid(a, b, "Memorial Day", "pto"),
        _uid(a + dt.timedelta(days=1), b, "Memorial Day", "holiday"),
        _uid(a, b, "Other", "holiday"),
    }
    assert base not in others and len(others) == 3


def test_event_uid_pinned_across_the_pto_colada_rename():
    # UIDs changed once in #8 and must not change again. The "@long-weekend-planner"
    # domain is an opaque identifier now, not the product name; calendar clients
    # match events by UID, so renaming it would duplicate every imported event.
    uid = _uid(dt.date(2026, 5, 22), dt.date(2026, 5, 26), "Memorial Day", "holiday")
    assert uid == "83ceffc0378a345192d52228624db92ac90acd92@long-weekend-planner"


def test_ics_prodid_uses_the_new_name():
    config = build_config(preset="minimal")
    holidays = resolve_year(config.holidays, 2026, config.workweek, config.observance)
    ics = render_ics(config, {2026: holidays})
    assert "PRODID:-//PTO Colada//EN" in ics
    assert "Long Weekend" not in ics

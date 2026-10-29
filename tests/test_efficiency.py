"""Efficiency score, return curve, --min-return and --target-days-off."""

from __future__ import annotations

import datetime as dt
import json

import pytest

from longweekend.catalog import PRESETS, resolve_year
from longweekend.cli import main
from longweekend.config import build_config
from longweekend.efficiency import return_curve
from longweekend.planner import PtoBudget, optimize_pto
from longweekend.schedule import build_days


def days_for(preset, start_year=2027, years=1):
    holidays = []
    for year in range(start_year - 1, start_year + years + 1):
        holidays.extend(resolve_year(PRESETS[preset], year))
    return build_days(dt.date(start_year, 1, 1), dt.date(start_year + years - 1, 12, 31), holidays)


def by_budget(curve):
    return {point.budget: point for point in curve.points}


class TestEfficiencyScore:
    def test_plan_scores_days_inside_breaks_per_pto_day(self):
        plan = optimize_pto(days_for("federal"), PtoBudget(default=17), min_break=4)
        assert plan.days_in_breaks == 56
        assert plan.total_pto == 17
        assert plan.efficiency == pytest.approx(56 / 17)

    def test_no_pto_means_no_score(self):
        plan = optimize_pto(days_for("federal"), PtoBudget(default=0), min_break=4)
        assert plan.efficiency is None


class TestReturnCurve:
    def test_federal_2027_matches_the_reported_claim(self):
        curve = return_curve(days_for("federal"), PtoBudget(default=17), min_break=4)
        points = by_budget(curve)
        assert [p.budget for p in curve.points] == list(range(18))
        assert points[0].days_in_breaks == 0
        assert points[10].days_in_breaks == 40
        assert points[17].days_in_breaks == 56

    def test_federal_2027_knee_is_near_ten(self):
        curve = return_curve(days_for("federal"), PtoBudget(default=17), min_break=4)
        assert curve.knee is not None
        assert 10 <= curve.knee.budget <= 12
        # Every day up to the knee earns the best rate; the next one earns less.
        best = max(p.marginal for p in curve.points if p.marginal is not None)
        assert best == pytest.approx(4.0)
        assert by_budget(curve)[curve.knee.budget + 1].marginal < best

    @pytest.mark.parametrize("preset", ["federal", "corporate", "shutdown"])
    def test_curve_shape(self, preset):
        days = days_for(preset)
        curve = return_curve(days, PtoBudget(default=20), min_break=4)
        values = [p.days_in_breaks for p in curve.points]
        assert len(values) == 21
        assert values == sorted(values)  # more budget never means fewer days
        assert all(p.marginal is None or p.marginal >= 0 for p in curve.points)
        assert curve.points[0].marginal is None
        # The last point is exactly what the planner returns for the full budget.
        full = optimize_pto(days, PtoBudget(default=20), min_break=4)
        assert values[-1] == full.days_in_breaks
        assert curve.points[-1].pto_spent == full.total_pto
        # The first PTO day is worth the most; the tail is worth less.
        assert curve.points[1].marginal > curve.points[-1].marginal

    @pytest.mark.parametrize("preset", ["corporate", "shutdown"])
    def test_free_days_are_the_zero_budget_floor(self, preset):
        curve = return_curve(days_for(preset), PtoBudget(default=5), min_break=4)
        free = optimize_pto(days_for(preset), PtoBudget(default=0), min_break=4)
        assert curve.points[0].days_in_breaks == free.days_in_breaks > 0

    def test_points_serialize_to_plain_dicts(self):
        curve = return_curve(days_for("federal"), PtoBudget(default=3), min_break=4)
        rows = curve.as_dicts()
        assert [r["budget"] for r in rows] == [0, 1, 2, 3]
        assert rows[2] == {
            "budget": 2, "pto_spent": 2, "days_in_breaks": 8,
            "marginal": 4.0, "days_per_pto": 4.0, "knee": False,
        }
        json.dumps(rows)

    def test_a_per_year_grant_below_the_ceiling_is_respected(self):
        budget = PtoBudget(default=10, per_year={2027: 3})
        curve = return_curve(days_for("federal", 2026, 2), budget, min_break=4)
        assert curve.points[-1].budget == 10
        plan = curve.plans[10]
        assert plan.spent_by_year.get(2027, 0) <= 3

    def test_budgets_below_committed_days_are_skipped(self):
        days = days_for("federal")
        working = [d.date for d in days if d.kind.value == "work"][10:13]
        curve = return_curve(
            days, PtoBudget(default=8), min_break=4, committed=set(working)
        )
        assert curve.points[0].budget == 3
        assert curve.points[-1].budget == 8

    def test_everything_infeasible_raises(self):
        days = days_for("federal")
        working = {d.date for d in days if d.kind.value == "work"}
        with pytest.raises(ValueError):
            return_curve(days, PtoBudget(default=1), committed=set(sorted(working)[:5]))

    def test_a_flat_curve_has_no_knee(self):
        curve = return_curve(days_for("federal"), PtoBudget(default=0), min_break=4)
        assert curve.knee is None


class TestMinReturn:
    def curve(self):
        return return_curve(days_for("federal"), PtoBudget(default=17), min_break=4)

    def test_stops_before_the_first_day_that_returns_too_little(self):
        stop = self.curve().stop_for_min_return(2.5)
        assert (stop.budget, stop.days_in_breaks) == (11, 44)

    def test_a_demanding_threshold_stops_at_the_start(self):
        assert self.curve().stop_for_min_return(5).budget == 0

    def test_a_day_that_returns_exactly_the_minimum_is_kept(self):
        assert self.curve().stop_for_min_return(4).budget == 11

    def test_zero_never_stops_early(self):
        assert self.curve().stop_for_min_return(0).budget == 17



class TestTarget:
    def curve(self):
        return return_curve(days_for("federal"), PtoBudget(default=17), min_break=4)

    @pytest.mark.parametrize("target,pto", [(1, 1), (40, 10), (41, 11), (44, 11), (45, 12), (56, 17)])
    def test_fewest_pto_days_that_reach_the_target(self, target, pto):
        point = self.curve().cheapest_for_target(target)
        assert point.pto_spent == pto
        assert point.days_in_breaks >= target

    def test_an_infeasible_target_names_the_most_reachable(self):
        with pytest.raises(ValueError, match=r"at most 56 days"):
            self.curve().cheapest_for_target(57)


class TestConfigValidation:
    @pytest.mark.parametrize("bad", [-0.5, float("nan"), float("inf")])
    def test_bad_min_return_is_rejected(self, bad):
        with pytest.raises(ValueError, match="min_return"):
            build_config(pto=10, min_return=bad)

    @pytest.mark.parametrize("bad", [0, -4])
    def test_bad_target_is_rejected(self, bad):
        with pytest.raises(ValueError, match="target_days_off"):
            build_config(pto=10, target_days_off=bad)

    def test_the_two_stops_are_alternatives(self):
        with pytest.raises(ValueError, match="alternatives"):
            build_config(pto=10, min_return=2.0, target_days_off=30)

    def test_values_from_a_config_file_are_checked_too(self, tmp_path):
        path = tmp_path / "c.json"
        path.write_text(json.dumps({"min_return": -1}), encoding="utf-8")
        with pytest.raises(ValueError, match="min_return"):
            build_config(config_path=str(path))

    def test_valid_values_are_kept(self):
        config = build_config(pto=10, min_return=2.5)
        assert config.min_return == 2.5
        assert build_config(pto=10, target_days_off=40).target_days_off == 40


BASE = ["--start-year", "2027", "--years", "1", "--min-break", "4"]


class TestCli:
    def run(self, capsys, *extra):
        assert main(BASE + list(extra)) == 0
        return capsys.readouterr().out

    def test_efficiency_in_text(self, capsys):
        out = self.run(capsys, "--pto", "17", "--efficiency")
        assert "56 days in breaks for 17 PTO day(s): 3.3 days per PTO day" in out
        assert "4.0 per PTO day" in out  # a per-break score

    def test_efficiency_in_json(self, capsys):
        payload = json.loads(self.run(capsys, "--pto", "17", "--efficiency", "--format", "json"))
        assert payload["plan"]["efficiency"] == {
            "days_in_breaks": 56, "pto_spent": 17, "days_per_pto": 3.294,
        }

    def test_efficiency_in_ics(self, capsys):
        out = self.run(capsys, "--pto", "17", "--efficiency", "--format", "ics")
        assert "days off per PTO day" in out

    def test_without_the_flag_nothing_new_appears(self, capsys):
        out = self.run(capsys, "--pto", "17")
        assert "days per PTO day" not in out
        assert "in breaks for" not in out
        assert "Return curve" not in out
        payload = json.loads(self.run(capsys, "--pto", "17", "--format", "json"))
        assert "efficiency" not in payload["plan"]
        assert "curve" not in payload
        assert "days off per PTO day" not in self.run(capsys, "--pto", "17", "--format", "ics")

    def test_curve_table_marks_the_knee(self, capsys):
        out = self.run(capsys, "--pto", "17", "--curve")
        assert "Return curve" in out
        knee_lines = [line for line in out.splitlines() if "<- knee" in line]
        assert len(knee_lines) == 1
        assert knee_lines[0].split()[0] == "11"
        assert "days per PTO day" in out  # --curve implies the efficiency score

    def test_curve_in_json(self, capsys):
        payload = json.loads(self.run(capsys, "--pto", "17", "--curve", "--format", "json"))
        assert len(payload["curve"]["points"]) == 18
        assert payload["curve"]["knee"] == 11
        assert payload["curve"]["points"][10]["days_in_breaks"] == 40

    def test_min_return_stops_spending(self, capsys):
        out = self.run(capsys, "--pto", "17", "--min-return", "2.5")
        assert "11 PTO day(s) spent for" in out
        assert "Return curve" not in out  # computed to decide, shown only on request
        assert "stopped at 11 PTO day(s) per year" in out
        assert "44 days in breaks for 11 PTO day(s)" in out

    def test_target_days_off(self, capsys):
        out = self.run(capsys, "--pto", "17", "--target-days-off", "40")
        assert "10 PTO day(s) spent for" in out
        assert "40 days in breaks for 10 PTO day(s)" in out

    def test_target_in_json_records_the_objective(self, capsys):
        payload = json.loads(
            self.run(capsys, "--pto", "17", "--target-days-off", "40", "--format", "json")
        )
        assert payload["plan"]["objective"]["target_days_off"] == 40
        assert payload["plan"]["objective"]["budget_per_year"] == 10
        assert len(payload["plan"]["pto_dates"]) == 10

    def test_infeasible_target_is_a_clear_error(self, capsys):
        with pytest.raises(SystemExit) as excinfo:
            main(BASE + ["--pto", "17", "--target-days-off", "90"])
        assert excinfo.value.code == 2
        err = capsys.readouterr().err
        assert "at most 56 days" in err

    @pytest.mark.parametrize(
        "argv",
        [
            ["--pto", "10", "--min-return", "-1"],
            ["--pto", "10", "--min-return", "nan"],
            ["--pto", "10", "--target-days-off", "0"],
            ["--pto", "10", "--min-return", "2", "--target-days-off", "30"],
            ["--curve"],
            ["--min-return", "2"],
            ["--target-days-off", "30"],
        ],
    )
    def test_bad_combinations_exit_with_an_error(self, argv):
        with pytest.raises(SystemExit) as excinfo:
            main(BASE + argv)
        assert excinfo.value.code == 2

    def test_a_curve_beside_a_stop_condition_covers_the_whole_budget(self, capsys):
        out = self.run(capsys, "--pto", "17", "--min-return", "2.5", "--curve")
        assert "stopped at 11" in out
        assert len([ln for ln in out.splitlines() if ln.strip().endswith("<- knee")]) == 1

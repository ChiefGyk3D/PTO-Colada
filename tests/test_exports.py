"""--export-ics, --export-csv and --export-xlsx: files are reopened and checked."""

from __future__ import annotations

import csv
import datetime as dt
import json
import os
import re
import sys

import pytest

from longweekend import exports
from longweekend.cli import main
from longweekend.config import build_config
from longweekend.exports import ExportError, neutralize, render_csv, write_exports
from longweekend.planner import PtoBudget, PtoPlan
from longweekend.schedule import Break, Day, DayKind

ARGS = ["--start-year", "2026", "--years", "1", "--pto", "10"]


def planner_json(capsys, extra=()):
    """The planner's own answer, read from its JSON output."""
    assert main(ARGS + ["--format", "json", "--efficiency", *extra]) == 0
    return json.loads(capsys.readouterr().out)


def run(args, capsys):
    code = main(args)
    return code, capsys.readouterr()


def unfold(text):
    return re.sub(r"\r\n[ \t]", "", text).split("\r\n")


# ------------------------------------------------------------------------- ics


class TestIcs:
    def test_new_calendar_with_event_counts(self, tmp_path, capsys):
        plan = planner_json(capsys)
        out = tmp_path / "plan.ics"
        code, cap = run(ARGS + ["--export-ics", str(out)], capsys)
        assert code == 0
        lines = unfold(out.read_bytes().decode("utf-8"))
        assert lines[0] == "BEGIN:VCALENDAR" and "END:VCALENDAR" in lines
        assert any(l.startswith("X-WR-CALNAME:") and "time off" in l for l in lines)
        categories = [l.split(":", 1)[1] for l in lines if l.startswith("CATEGORIES:")]
        holidays = sum(len(y["holidays"]) for y in plan["years"].values())
        assert categories.count("HOLIDAY") == holidays
        assert categories.count("PTO") == len(plan["plan"]["pto_dates"])
        assert categories.count("BREAK") == len(plan["plan"]["breaks"])
        assert lines.count("BEGIN:VEVENT") == lines.count("END:VEVENT") == len(categories)
        # every event is all-day
        assert all(l.startswith("DTSTART;VALUE=DATE:") for l in lines if l.startswith("DTSTART"))

    def test_no_holidays_flag_respected(self, tmp_path, capsys):
        out = tmp_path / "p.ics"
        run(ARGS + ["--no-holidays", "--export-ics", str(out)], capsys)
        assert "CATEGORIES:HOLIDAY" not in out.read_text()

    def test_works_without_pto(self, tmp_path, capsys):
        out = tmp_path / "h.ics"
        code, _ = run(["--start-year", "2026", "--years", "1", "--export-ics", str(out)], capsys)
        assert code == 0
        text = out.read_text()
        assert "CATEGORIES:HOLIDAY" in text and "CATEGORIES:PTO" not in text


# ------------------------------------------------------------------------- csv


class TestCsv:
    def test_rows_match_planner(self, tmp_path, capsys):
        plan = planner_json(capsys)["plan"]
        out = tmp_path / "plan.csv"
        code, _ = run(ARGS + ["--export-csv", str(out)], capsys)
        assert code == 0
        with open(out, newline="", encoding="utf-8") as handle:
            rows = list(csv.reader(handle))
        assert rows[0] == exports.CSV_HEADER
        body = rows[1:]
        breaks = [r for r in body if r[0] == "break"]
        pto = [r for r in body if r[0] == "pto"]
        assert len(breaks) == len(plan["breaks"]) and len(pto) == len(plan["pto_dates"])
        for row, expected in zip(breaks, plan["breaks"]):
            assert row[1:5] == [expected["start"], expected["end"],
                                str(expected["length"]), str(expected["pto_cost"])]
            assert float(row[5]) == pytest.approx(expected["efficiency"], abs=1e-3)
        assert [r[1] for r in pto] == plan["pto_dates"]

    def test_needs_a_plan(self, tmp_path, capsys):
        out = tmp_path / "x.csv"
        with pytest.raises(SystemExit) as info:
            main(["--start-year", "2026", "--years", "1", "--export-csv", str(out)])
        assert info.value.code == 2
        assert "--pto" in capsys.readouterr().err
        assert not out.exists()


class TestFormulaInjection:
    @pytest.mark.parametrize("lead", ["=", "+", "-", "@", "\t", "\r"])
    def test_neutralize_prefixes_each_leader(self, lead):
        assert neutralize(lead + "cmd") == "'" + lead + "cmd"

    def test_neutralize_leaves_safe_values(self):
        assert neutralize("Labor Day") == "Labor Day"
        assert neutralize(3) == 3
        assert neutralize("2026-01-02") == "2026-01-02"

    def _evil_plan(self):
        config = build_config(preset="federal", years=[2026])
        day = Day(dt.date(2026, 1, 2), DayKind.PTO)
        hol = Day(dt.date(2026, 1, 1), DayKind.HOLIDAY, holiday='=HYPERLINK("http://x","y")')
        plan = PtoPlan(
            days=[hol, day], breaks=[Break(dt.date(2026, 1, 1), dt.date(2026, 1, 2), [hol, day])],
            pto_dates=[day.date], budget=PtoBudget(default=1),
            spent_by_year={2026: 1}, granted_by_year={2026: 1}, min_break=2,
        )
        return config, plan

    def test_csv_cells_never_start_with_formula_characters(self):
        config, plan = self._evil_plan()
        text = render_csv(plan, config)
        cells = [c for row in csv.reader(text.splitlines()) for c in row]
        assert "'=HYPERLINK" in "".join(cells)
        assert not [c for c in cells if c.startswith(("=", "+", "@", "\t"))]
        # negative control: the raw value really would have been dangerous
        assert any(h.startswith("=") for h in plan.breaks[0].holidays)

    def test_xlsx_text_stays_text(self, tmp_path):
        openpyxl = pytest.importorskip("openpyxl")
        config, plan = self._evil_plan()
        out = tmp_path / "e.xlsx"
        write_exports(config, {}, plan, xlsx=str(out))
        cell = openpyxl.load_workbook(out)["Breaks"]["F2"]
        assert cell.data_type == "s" and cell.value.startswith("=HYPERLINK")


# ------------------------------------------------------------------------- xlsx


def cat_ref(series):
    """openpyxl stores category refs as numRef or strRef depending on the writer."""
    return (series.cat.strRef or series.cat.numRef).f


def resolve(wb, ref):
    """Values behind an ``'Sheet'!$A$2:$A$9`` reference."""
    sheet, cells = ref.rsplit("!", 1)
    ws = wb[sheet.strip("'")]
    out = []
    for row in ws[cells.replace("$", "")]:
        out.extend(cell.value for cell in (row if isinstance(row, tuple) else (row,)))
    return out


class TestXlsx:
    @pytest.fixture
    def book(self, tmp_path, capsys):
        openpyxl = pytest.importorskip("openpyxl")
        plan = planner_json(capsys)["plan"]
        out = tmp_path / "plan.xlsx"
        code, _ = run(ARGS + ["--export-xlsx", str(out)], capsys)
        assert code == 0
        return openpyxl.load_workbook(out), plan

    def test_summary_has_efficiency_score(self, book):
        wb, plan = book
        rows = {r[0]: r[1] for r in wb["Summary"].iter_rows(values_only=True) if r[0]}
        score = next(v for k, v in rows.items() if str(k).startswith("Efficiency score"))
        assert score == pytest.approx(plan["efficiency"]["days_per_pto"], abs=1e-3)
        assert rows["Breaks"] == len(plan["breaks"])
        assert rows["PTO days booked (floating included)"] == len(plan["pto_dates"])

    def test_three_charts_with_planner_data(self, book):
        wb, plan = book
        charts = {ws.title: ws._charts for ws in wb.worksheets if ws._charts}
        assert sorted(charts) == ["Breaks", "PTO per month", "Return curve"]
        assert sum(len(c) for c in charts.values()) == 3

        (timeline,) = charts["Breaks"]
        lengths, costs = (resolve(wb, s.val.numRef.f) for s in timeline.series)
        assert lengths == [b["length"] for b in plan["breaks"]]
        assert costs == [b["pto_cost"] for b in plan["breaks"]]
        assert resolve(wb, cat_ref(timeline.series[0])) == [b["start"] for b in plan["breaks"]]

        (monthly,) = charts["PTO per month"]
        counts = resolve(wb, monthly.series[0].val.numRef.f)
        months = resolve(wb, cat_ref(monthly.series[0]))
        assert sum(counts) == len(plan["pto_dates"]) and len(months) == 12
        for date in plan["pto_dates"]:
            assert counts[months.index(date[:7])] >= 1

    def test_curve_chart_matches_return_curve(self, book, capsys):
        wb, _ = book
        assert main(ARGS + ["--format", "json", "--curve"]) == 0
        curve = json.loads(capsys.readouterr().out)["curve"]["points"]
        (chart,) = wb["Return curve"]._charts
        assert resolve(wb, chart.series[0].val.numRef.f) == [p["days_in_breaks"] for p in curve]
        assert resolve(wb, cat_ref(chart.series[0])) == [p["budget"] for p in curve]

    def test_sheet_rows_match_plan(self, book):
        wb, plan = book
        assert wb["Breaks"].max_row - 1 == len(plan["breaks"])
        assert [r[0] for r in wb["PTO"].iter_rows(min_row=2, values_only=True)] == plan["pto_dates"]

    def test_goal_mode_has_no_curve_but_still_exports(self, tmp_path, capsys):
        openpyxl = pytest.importorskip("openpyxl")
        out = tmp_path / "g.xlsx"
        code, _ = run(ARGS + ["--goal", "weekends", "--export-xlsx", str(out)], capsys)
        assert code == 0
        wb = openpyxl.load_workbook(out)
        assert "Return curve" not in wb.sheetnames


class TestMissingOpenpyxl:
    def test_clear_error_and_nothing_written(self, tmp_path, capsys, monkeypatch):
        monkeypatch.setitem(sys.modules, "openpyxl", None)  # makes `import openpyxl` fail
        xlsx, ics = tmp_path / "a.xlsx", tmp_path / "a.ics"
        with pytest.raises(SystemExit) as info:
            main(ARGS + ["--export-xlsx", str(xlsx), "--export-ics", str(ics)])
        assert info.value.code == 2
        err = capsys.readouterr().err
        assert "openpyxl" in err and "long-weekend-planner[xlsx]" in err
        assert not xlsx.exists() and not ics.exists()

    def test_core_import_does_not_need_openpyxl(self, monkeypatch):
        monkeypatch.setitem(sys.modules, "openpyxl", None)
        assert exports.render_csv  # module already imported without openpyxl


# ------------------------------------------------------------------------- paths


def fails(args, capsys):
    with pytest.raises(SystemExit) as info:
        main(args)
    assert info.value.code == 2
    return capsys.readouterr().err


class TestPathSafety:
    def test_symlink_refused_and_target_untouched(self, tmp_path, capsys):
        victim = tmp_path / "victim.txt"
        victim.write_text("keep")
        link = tmp_path / "out.csv"
        link.symlink_to(victim)
        err = fails(ARGS + ["--export-csv", str(link)], capsys)
        assert "symbolic link" in err
        assert victim.read_text() == "keep" and link.is_symlink()

    def test_dangling_symlink_refused(self, tmp_path, capsys):
        link = tmp_path / "out.ics"
        link.symlink_to(tmp_path / "nowhere")
        assert "symbolic link" in fails(ARGS + ["--export-ics", str(link)], capsys)
        assert not (tmp_path / "nowhere").exists()

    def test_directory_target_refused(self, tmp_path, capsys):
        assert "not a regular file" in fails(ARGS + ["--export-csv", str(tmp_path)], capsys)

    def test_fifo_refused(self, tmp_path, capsys):
        fifo = tmp_path / "pipe"
        os.mkfifo(fifo)
        assert "not a regular file" in fails(ARGS + ["--export-csv", str(fifo)], capsys)

    def test_missing_directory_refused(self, tmp_path, capsys):
        err = fails(ARGS + ["--export-csv", str(tmp_path / "no" / "x.csv")], capsys)
        assert "directory does not exist" in err

    def test_same_file_for_two_exports_refused(self, tmp_path, capsys):
        path = str(tmp_path / "same")
        err = fails(ARGS + ["--export-csv", path, "--export-ics", path], capsys)
        assert "same file" in err

    def test_one_bad_path_writes_nothing(self, tmp_path, capsys):
        good = tmp_path / "ok.csv"
        fails(ARGS + ["--export-csv", str(good), "--export-ics", str(tmp_path / "no" / "x.ics")], capsys)
        assert not good.exists()

    def test_overwrites_regular_file_and_leaves_no_temp(self, tmp_path, capsys):
        out = tmp_path / "plan.csv"
        out.write_text("old")
        assert run(ARGS + ["--export-csv", str(out)], capsys)[0] == 0
        assert out.read_text().startswith("kind,start")
        assert [p.name for p in tmp_path.iterdir()] == ["plan.csv"]

    def test_failed_write_keeps_old_file_and_cleans_up(self, tmp_path, monkeypatch):
        out = tmp_path / "plan.csv"
        out.write_text("old")

        def boom(src, dst):
            raise OSError(28, "No space left on device")

        monkeypatch.setattr(exports.os, "replace", boom)
        with pytest.raises(ExportError, match="could not write"):
            exports._atomic_write(str(out), lambda tmp: open(tmp, "w").write("new"), "--export-csv")
        assert out.read_text() == "old"
        assert [p.name for p in tmp_path.iterdir()] == ["plan.csv"]

    def test_symlink_planted_after_check_is_replaced_not_followed(self, tmp_path):
        victim = tmp_path / "victim.txt"
        victim.write_text("keep")
        link = tmp_path / "out.csv"
        link.symlink_to(victim)
        exports._atomic_write(str(link), lambda tmp: open(tmp, "w").write("new"), "--export-csv")
        assert victim.read_text() == "keep" and not link.is_symlink()


class TestDefaultOutputUnchanged:
    def test_stdout_identical_with_and_without_exports(self, tmp_path, capsys):
        pytest.importorskip("openpyxl")
        _, plain = run(ARGS, capsys)
        _, exported = run(ARGS + [
            "--export-ics", str(tmp_path / "a.ics"), "--export-csv", str(tmp_path / "a.csv"),
            "--export-xlsx", str(tmp_path / "a.xlsx"),
        ], capsys)
        assert exported.out == plain.out
        assert "wrote" in exported.err and plain.err == ""


# ------------------------------------------------------------------------- round 2


class TestRound2:
    def test_unwritable_directory_is_a_clean_error(self, tmp_path, capsys):
        if os.geteuid() == 0:
            pytest.skip("root ignores directory permissions")
        ro = tmp_path / "ro"
        ro.mkdir()
        ro.chmod(0o555)
        try:
            err = fails(ARGS + ["--export-csv", str(ro / "x.csv")], capsys)
        finally:
            ro.chmod(0o755)
        assert "could not write" in err and "Traceback" not in err
        assert list(ro.iterdir()) == []

    def test_symlinked_directory_duplicate_refused(self, tmp_path, capsys):
        (tmp_path / "real").mkdir()
        (tmp_path / "alias").symlink_to(tmp_path / "real")
        err = fails(ARGS + ["--export-csv", str(tmp_path / "real" / "f"),
                            "--export-ics", str(tmp_path / "alias" / "f")], capsys)
        assert "same file" in err

    def test_hardlink_duplicate_refused(self, tmp_path, capsys):
        first = tmp_path / "a"
        first.write_text("x")
        os.link(first, tmp_path / "b")
        err = fails(ARGS + ["--export-csv", str(first), "--export-ics", str(tmp_path / "b")], capsys)
        assert "same file" in err
        assert first.read_text() == "x"

    def test_empty_path_is_an_error_not_ignored(self, capsys):
        assert "not a usable file path" in fails(ARGS + ["--export-csv", ""], capsys)

    def test_nul_in_library_path_refused(self, tmp_path):
        config = build_config(preset="federal", years=[2026])
        with pytest.raises(ExportError, match="not a usable"):
            write_exports(config, {}, None, ics=str(tmp_path / "a\x00b"))

    def test_new_file_follows_umask(self, tmp_path, capsys):
        old = os.umask(0o027)
        try:
            run(ARGS + ["--export-csv", str(tmp_path / "n.csv")], capsys)
        finally:
            os.umask(old)
        assert (tmp_path / "n.csv").stat().st_mode & 0o777 == 0o640

    def test_replace_keeps_existing_mode(self, tmp_path, capsys):
        out = tmp_path / "e.csv"
        out.write_text("old")
        out.chmod(0o600)
        run(ARGS + ["--export-csv", str(out)], capsys)
        assert out.stat().st_mode & 0o777 == 0o600
        assert out.read_text().startswith("kind,")

    def test_horizon_mode_exports(self, tmp_path, capsys):
        openpyxl = pytest.importorskip("openpyxl")
        base = ["--today", "2026-11-02", "--pto-hours", "36", "--through", "2027-03-31"]
        assert main(base + ["--format", "json"]) == 0
        plan = json.loads(capsys.readouterr().out)["plan"]
        out = {k: tmp_path / ("h." + k) for k in ("ics", "csv", "xlsx")}
        code, cap = run(base + [f"--export-{k}={v}" for k, v in out.items()], capsys)
        assert code == 0
        rows = list(csv.reader(out["csv"].read_text().splitlines()))[1:]
        assert [r[1] for r in rows if r[0] == "pto"] == plan["pto_dates"]
        assert len([r for r in rows if r[0] == "break"]) == len(plan["breaks"])
        assert out["ics"].read_text().count("CATEGORIES:PTO") == len(plan["pto_dates"])
        wb = openpyxl.load_workbook(out["xlsx"])
        assert wb["Breaks"].max_row - 1 == len(plan["breaks"])
        _, plain = run(base, capsys)
        assert cap.out == plain.out

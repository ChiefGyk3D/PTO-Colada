"""Command-line interface."""

from __future__ import annotations

import argparse
import datetime as dt
import sys
from dataclasses import replace
from typing import Dict, List, Optional, Sequence

from .catalog import KNOWN, PRESETS, ResolvedHoliday, resolve_year
from .conferences import describe_catalog, load_conferences
from .observances import describe_sets
from .config import CompanyConfig, _bounded_date, build_config
from .efficiency import ReturnCurve, return_curve
from .exports import ExportError, write_exports
from .goals import GOALS, PAD_DAYS, build_goal_days, goal_spec, optimize_goal
from .honest import score_pto
from .horizon import (
    HorizonSpec,
    build_horizon,
    describe_horizon,
    horizon_json,
    rest_metrics,
    year_ends,
)
from .planner import PtoBudget, PtoPlan, optimize_pto
from .report import GoalSummary, render_ics, render_json, render_text
from .schedule import Break, Day, Opportunity, build_days, find_opportunities

__all__ = ["main", "build_parser"]

_EPILOG = """\
holiday date forms (for --add and config files):
  Good Friday             a holiday this tool knows; see --list-known
  Name=07-04              same month and day every year
  Name=2026-12-28         a one-off date in a single year
  Name=3rd MON in JAN     nth weekday of a month ('last' works too)
  Name=Thanksgiving+1     an offset from another holiday in the same set
  Name=Easter-2           an offset from Easter Sunday
  Name=TUE on or after 11-02   first weekday on or after a date

examples:
  pto-colada                                    this year and next, US federal holidays
  pto-colada --preset corporate --pto 15        plan 15 PTO days a year
  pto-colada --preset federal --drop "Columbus Day" --add "Day After Thanksgiving=Thanksgiving+1"
  pto-colada --pto 20 --min-break 7             aim for week-long breaks
  pto-colada --preset tech --pto 15 --floating 2       two floating holidays on top
  pto-colada --add "Good Friday" --add "Election Day" --pto 15
  pto-colada --pto 15 --blackout 2026-04-01:2026-04-15 --commit 2026-07-20
  pto-colada --pto 15 --format ics > timeoff.ics
  pto-colada --pto 17 --curve                   where extra PTO stops paying off
  pto-colada --pto 17 --min-return 2.5          stop when a day returns under 2.5 days
  pto-colada --pto 17 --target-days-off 40      the fewest PTO days that reach 40
  pto-colada --config examples/employer-2027.yaml --start-year 2027 --years 1 --goal balanced --max-breaks 8
                                                honest count, one goal, at most 8 breaks
  pto-colada --pto 15 --goal vacations --min-vacation-days 9 --prefer 2026-12-24:2026-12-31
  pto-colada --today 2026-11-02 --pto-hours 36 --refill-hours 120 --carryover 5 --through 2027-03-31
                                                plan from a date, in hours, across the new year
"""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pto-colada",
        description=(
            "Find long weekends and plan PTO around the holidays your employer "
            "actually observes."
        ),
        epilog=_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    window = parser.add_argument_group("time window")
    window.add_argument(
        "--start-year", type=int, default=None,
        help="first year to plan (default: the current year)",
    )
    window.add_argument(
        "--end-year", type=int, default=None,
        help="last year to plan (default: --start-year plus --years minus one)",
    )
    window.add_argument(
        "--years", type=int, default=2,
        help="how many years to cover when --end-year is not given (default: 2)",
    )
    window.add_argument(
        "--from-today", action="store_true",
        help="start the window at today instead of January 1, so past days are not planned",
    )

    horizon = parser.add_argument_group(
        "planning from today, in hours (any option here, except --today and "
        "--hours-per-day, starts this mode; it needs --pto-hours)"
    )
    horizon.add_argument(
        "--pto-hours", metavar="H",
        help=(
            "your PTO balance now, in hours. Whole days (hours / --hours-per-day, rounded "
            "down) are planned; the leftover hours are reported. Plans from today; "
            "--start-year, --end-year and --from-today do not apply and --years is ignored"
        ),
    )
    horizon.add_argument(
        "--hours-per-day", metavar="H", default="8",
        help="hours in one workday, for converting hours to days (default: 8)",
    )
    horizon.add_argument(
        "--refill-hours", metavar="H",
        help=(
            "hours granted on each January 1 inside the horizon (default: none). Unspent "
            "days roll over up to --carryover (in days); the rest is reported as "
            "use-it-or-lose-it"
        ),
    )
    horizon.add_argument(
        "--today", metavar="DATE",
        help="treat DATE (YYYY-MM-DD) as today, so examples are reproducible (default: the real date)",
    )
    horizon.add_argument(
        "--through", metavar="DATE",
        help="last day to plan (default: December 31 of today's year)",
    )
    horizon.add_argument(
        "--months", type=int, metavar="N",
        help="plan N months from today instead of using --through (1 to 60)",
    )
    horizon.add_argument(
        "--max-work-streak", type=int, metavar="N",
        help=(
            "never go more than N working days without a rest break (3+ days off in a row, "
            "holidays and weekends included). The count starts on the first planned day. "
            "Classic planner only; an error says so when no plan can keep the limit"
        ),
    )

    holidays = parser.add_argument_group("which holidays your employer observes")
    holidays.add_argument(
        "--preset", default="federal", choices=sorted(PRESETS),
        help="starting holiday set (default: federal)",
    )
    holidays.add_argument(
        "--config", dest="config_path", metavar="PATH",
        help="JSON or YAML company config; overrides the preset",
    )
    holidays.add_argument(
        "--add", action="append", default=[], metavar="SPEC",
        help="add a holiday, e.g. --add \"Founders Day=06-01\" (repeatable)",
    )
    holidays.add_argument(
        "--drop", action="append", default=[], metavar="NAME",
        help="remove a holiday by name (repeatable, comma-separated allowed)",
    )
    holidays.add_argument(
        "--workweek", metavar="SPEC",
        help="working days, e.g. MON-FRI (default), SUN-THU, or MON,TUE,WED,THU",
    )
    holidays.add_argument(
        "--observance", metavar="MODE",
        help=(
            "what happens when a holiday lands on a non-working day: "
            "nearest (US federal rule, the default), next, previous, or none"
        ),
    )

    pto = parser.add_argument_group("PTO planning")
    pto.add_argument(
        "--pto", type=int, metavar="DAYS",
        help="PTO days available per year; enables the planner",
    )
    pto.add_argument(
        "--pto-year", action="append", default=[], metavar="YEAR=DAYS",
        help="override the budget for one year, e.g. --pto-year 2027=20 (repeatable)",
    )
    pto.add_argument(
        "--floating", type=int, metavar="DAYS",
        help=(
            "floating holidays granted on top of the fixed ones: discretionary "
            "days you place anywhere. They expire at the end of the calendar "
            "year, so the planner spends them before touching PTO"
        ),
    )
    pto.add_argument(
        "--floating-year", action="append", default=[], metavar="YEAR=DAYS",
        help="override the floating-holiday grant for one year (repeatable)",
    )
    pto.add_argument(
        "--carryover", type=int, metavar="DAYS",
        help="unused days that may roll into the next year (default: 0)",
    )
    pto.add_argument(
        "--min-break", type=int, metavar="DAYS",
        help=(
            "how many consecutive days off counts as a break worth having "
            "(default: 4); raise it to build longer vacations"
        ),
    )
    pto.add_argument(
        "--style", choices=("weekends", "vacations"), default=None,
        help=(
            "how to spend budget left over after bridging holidays: 'weekends' "
            "(default) makes as many long weekends as possible, 'vacations' "
            "consolidates the leftovers into fewer, longer trips"
        ),
    )
    pto.add_argument(
        "--max-consecutive-pto", type=int, metavar="DAYS",
        help="never spend more than this many PTO days in a row",
    )
    pto.add_argument(
        "--blackout", action="append", default=[], metavar="DATE[:DATE]",
        help="dates you cannot take off, e.g. --blackout 2026-04-01:2026-04-15 (repeatable)",
    )
    pto.add_argument(
        "--commit", action="append", default=[], dest="committed", metavar="DATE[:DATE]",
        help="PTO you have already booked; charged to the budget (repeatable)",
    )

    goals = parser.add_argument_group(
        "goals: what kind of time off to plan for (honest count; needs a PTO budget)"
    )
    goals.add_argument(
        "--goal", choices=GOALS,
        help=(
            "weekends: frequent long weekends (each day in a PTO break of 3+ days "
            "scores 1); vacations: fewer, longer trips (only breaks of "
            "--min-vacation-days or more score); balanced: weekends plus a second "
            "point for every day in a vacation. Counts only breaks that contain PTO"
        ),
    )
    goals.add_argument(
        "--min-vacation-days", type=int, metavar="N",
        help="shortest break that counts as a vacation (default: 7); implies --goal vacations",
    )
    goals.add_argument(
        "--prefer", action="append", default=[], metavar="DATE[:DATE]",
        help="dates you would like to take off; each one taken earns a small bonus (repeatable)",
    )
    goals.add_argument(
        "--max-breaks", type=int, metavar="N",
        help="plan at most N breaks that contain PTO (exact; the method is stated in the output)",
    )

    efficiency = parser.add_argument_group("efficiency: how much PTO is worth spending")
    efficiency.add_argument(
        "--efficiency", action="store_true",
        help=(
            "show days in breaks per PTO day, overall and for each break "
            "(implied by the options below)"
        ),
    )
    efficiency.add_argument(
        "--curve", action="store_true",
        help=(
            "show the return curve: for every per-year PTO budget up to --pto, "
            "the days in breaks it buys, what the last day returned, and the knee"
        ),
    )
    efficiency.add_argument(
        "--min-return", type=float, metavar="DAYS",
        help=(
            "stop spending PTO once the next day would return fewer than DAYS "
            "days in breaks (--pto is the ceiling)"
        ),
    )
    efficiency.add_argument(
        "--target-days-off", "--target-days-in-breaks", dest="target_days_off",
        type=int, metavar="N",
        help=(
            "spend the fewest PTO days that put N days into breaks (days in "
            "breaks, not every day off); an error names the most --pto can "
            "reach if N is out of range"
        ),
    )

    personal = parser.add_argument_group("days you take off yourself")
    personal.add_argument(
        "--observe", action="append", default=[], metavar="WHAT",
        help=(
            "days you will take off regardless: a set such as 'jewish' or 'muslim', a "
            "holiday name such as \"Yom Kippur\" or \"Eid al-Fitr\", or a date. "
            "Charged to your budget, and the planner builds around them "
            "(repeatable). See --list-observances"
        ),
    )
    personal.add_argument(
        "--israel", action="store_true",
        help="use the Israeli festival calendar (no second diaspora day)",
    )
    personal.add_argument(
        "--erev", action="store_true",
        help="also take the day each Jewish holiday begins on, since it starts at sunset",
    )
    personal.add_argument(
        "--shift", type=int, default=0, metavar="N",
        help=(
            "move every computed Muslim holiday by N days (-3 to 3), for a local "
            "moon sighting. Dates from a config 'observances' entry are not moved. "
            "Muslim dates stay provisional either way"
        ),
    )
    personal.add_argument(
        "--conference", action="append", default=[], metavar="NAME",
        help=(
            "take a security conference off, e.g. --conference \"DEF CON\" "
            "(repeatable). See --list-conferences"
        ),
    )
    personal.add_argument(
        "--conference-travel", type=int, default=0, metavar="DAYS",
        help="days either side of a conference for travel (default: 0)",
    )
    personal.add_argument(
        "--conferences", dest="conference_file", metavar="PATH",
        help="your own conference JSON, overriding or extending the bundled catalog",
    )

    output = parser.add_argument_group("output")
    output.add_argument(
        "--format", choices=("text", "json", "ics"), default="text",
        help="output format (default: text)",
    )
    output.add_argument(
        "--no-holidays", action="store_true",
        help="omit the holiday listing from text and ics output",
    )
    output.add_argument(
        "--list-presets", action="store_true",
        help="show the built-in holiday sets and exit",
    )
    output.add_argument(
        "--list-known", action="store_true",
        help="show every holiday that --add accepts by name, and exit",
    )
    output.add_argument(
        "--list-observances", action="store_true",
        help="show what --observe accepts, and exit",
    )
    output.add_argument(
        "--list-conferences", action="store_true",
        help="show the conference catalog and its dates, and exit",
    )
    output.add_argument(
        "--export-ics", metavar="FILE",
        help="write an .ics to import as its own new calendar (holidays, PTO days, breaks)",
    )
    output.add_argument(
        "--export-csv", metavar="FILE",
        help="write the breaks and PTO days as CSV (needs --pto)",
    )
    output.add_argument(
        "--export-xlsx", metavar="FILE",
        help=(
            "write an Excel workbook with a summary and charts (needs --pto and "
            "the optional extra: pip install 'pto-colada[xlsx]')"
        ),
    )
    output.add_argument(
        "--interactive", "-i", action="store_true",
        help="answer a few questions instead of remembering flags",
    )

    parser.add_argument(
        "--nonstandard", action="store_true",
        help=argparse.SUPPRESS,  # compatibility with the original script
    )
    return parser


def resolve_window(args: argparse.Namespace, today: dt.date) -> "tuple[dt.date, dt.date, int, int]":
    """Work out the date window, defaulting to the current year and the next one."""
    start_year = args.start_year if args.start_year is not None else today.year
    if args.end_year is not None:
        end_year = args.end_year
    else:
        if args.years < 1:
            raise ValueError("--years must be at least 1")
        end_year = start_year + args.years - 1
    if end_year < start_year:
        raise ValueError(f"--end-year ({end_year}) is before --start-year ({start_year})")

    start = dt.date(start_year, 1, 1)
    end = dt.date(end_year, 12, 31)
    if args.from_today and start <= today <= end:
        start = today
    return start, end, start_year, end_year


def _list_presets() -> str:
    lines = ["Built-in holiday sets:", ""]
    for name in sorted(PRESETS):
        rules = PRESETS[name]
        lines.append(f"  {name}  ({len(rules)} holidays)")
        for rule in rules:
            lines.append(f"      {rule.name:<34} {rule.describe()}")
        lines.append("")
    lines.append("Adjust any of them with --add and --drop, or start fresh with --config.")
    return "\n".join(lines)


def _list_known() -> str:
    lines = [
        "Holidays you can add by name, e.g. --add \"Good Friday\":",
        "",
    ]
    for name in sorted(KNOWN):
        lines.append(f"  {name:<34} {KNOWN[name].describe()}")
    lines.append("")
    lines.append(
        "Anything else takes a date: --add \"Founders Day=2nd FRI in MAY\". "
        "Use --drop to remove\na holiday a preset included."
    )
    return "\n".join(lines)


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.list_presets:
        print(_list_presets())
        return 0

    if args.list_known:
        print(_list_known())
        return 0

    if args.list_observances:
        print(describe_sets())
        return 0

    try:
        today = _bounded_date(args.today, "--today") if args.today else dt.date.today()
    except ValueError as exc:
        parser.error(str(exc))
        return 2  # pragma: no cover - parser.error exits

    if args.list_conferences:
        try:
            catalog = load_conferences(args.conference_file)
        except (ValueError, FileNotFoundError) as exc:
            parser.error(str(exc))
            return 2  # pragma: no cover - parser.error exits
        print(describe_catalog(catalog, [today.year, today.year + 1, today.year + 2]))
        return 0

    if args.interactive:
        from .wizard import run_wizard

        argv = run_wizard(args)
        if argv is None:
            return 1
        args = parser.parse_args(argv)

    preset = "nonstandard" if args.nonstandard and args.preset == "federal" else args.preset

    spec: Optional[HorizonSpec] = None
    pto, pto_year = args.pto, list(args.pto_year)
    try:
        if args.pto_hours is None and args.hours_per_day != "8":
            print("note: --hours-per-day has no effect without --pto-hours", file=sys.stderr)
        if _wants_horizon(args):
            spec = _horizon_spec(args, today)
            pto = spec.refill_days
            # The balance is this year's allowance; the refill applies from the next
            # January 1. A --pto-year entry overrides either.
            pto_year = [f"{today.year}={spec.balance_days}"] + pto_year
            start, end = today, spec.end
            start_year, end_year = today.year, spec.end.year
        else:
            start, end, start_year, end_year = resolve_window(args, today)
        config = build_config(
            preset=preset,
            config_path=args.config_path,
            add=args.add,
            drop=args.drop,
            workweek=args.workweek,
            observance=args.observance,
            pto=pto,
            pto_by_year=pto_year,
            floating=args.floating,
            floating_by_year=args.floating_year,
            carryover=args.carryover,
            min_break=args.min_break,
            max_consecutive_pto=args.max_consecutive_pto,
            min_return=args.min_return,
            target_days_off=args.target_days_off,
            blackout=args.blackout,
            committed=args.committed,
            goal=args.goal,
            min_vacation_days=args.min_vacation_days,
            prefer=args.prefer,
            max_breaks=args.max_breaks,
            observe=args.observe,
            conferences=args.conference,
            conference_file=args.conference_file,
            conference_travel=args.conference_travel,
            years=range(start_year, end_year + 1),
            diaspora=not args.israel,
            include_erev=args.erev,
            muslim_shift=args.shift,
        )
    except (ValueError, FileNotFoundError, OverflowError) as exc:
        parser.error(str(exc))
        return 2  # pragma: no cover - parser.error exits
    except MemoryError:
        parser.error("not enough memory to read this configuration")
        return 2  # pragma: no cover - parser.error exits

    if not config.holidays:
        print("No holidays configured; nothing to plan.", file=sys.stderr)
        return 1

    # Resolve one year either side of the window so that breaks spanning a New
    # Year boundary are complete rather than clipped.
    holidays_by_year: Dict[int, List[ResolvedHoliday]] = {}
    all_holidays: List[ResolvedHoliday] = []
    for year in range(start_year - 1, end_year + 2):
        resolved = resolve_year(config.holidays, year, config.workweek, config.observance)
        all_holidays.extend(resolved)
        if start_year <= year <= end_year:
            holidays_by_year[year] = [h for h in resolved if start <= h.observed <= end]

    days = build_days(start, end, all_holidays, config.workweek, config.blackout)

    opportunities_by_year: Dict[int, List[Opportunity]] = {}
    for opportunity in find_opportunities(days, target=config.min_break):
        opportunities_by_year.setdefault(opportunity.date.year, []).append(opportunity)

    plan: Optional[PtoPlan] = None
    curve: Optional[ReturnCurve] = None
    objective: Optional[Dict[str, object]] = None
    stops = config.min_return is not None or config.target_days_off is not None
    wants_curve = args.curve or stops
    has_budget = (
        config.pto_days is not None
        or config.pto_by_year
        or config.floating_days is not None
        or config.floating_by_year
    )
    if wants_curve and not has_budget:
        parser.error("--curve, --min-return and --target-days-off need a PTO budget: add --pto")
        return 2  # pragma: no cover - parser.error exits
    goal_summary: Optional[GoalSummary] = None
    if config.wants_goal:
        if not has_budget:
            parser.error("--goal and its options need a PTO budget: add --pto")
            return 2  # pragma: no cover - parser.error exits
        if args.curve:
            parser.error(
                "--curve belongs to the classic planner and cannot be combined with --goal "
                "or its options"
            )
            return 2  # pragma: no cover - parser.error exits
        if args.efficiency:
            parser.error(
                "--efficiency belongs to the classic planner and cannot be combined with "
                "--goal or its options; the goal output already gives the honest count"
            )
            return 2  # pragma: no cover - parser.error exits
        if args.style is not None:
            parser.error("--style belongs to the classic planner; use --goal instead")
            return 2  # pragma: no cover - parser.error exits
        try:
            plan, goal_summary = _plan_for_goal(
                config, days, all_holidays, start, end, start_year, end_year
            )
        except ValueError as exc:
            parser.error(str(exc))
            return 2  # pragma: no cover - parser.error exits
    elif has_budget:
        budget = PtoBudget(
            per_year=dict(config.pto_by_year),
            default=config.pto_days or 0,
            carryover=config.carryover,
            floating_per_year=dict(config.floating_by_year),
            floating=config.floating_days or 0,
        )
        long_break_bonus = 1 if args.style == "vacations" else 0
        try:
            if wants_curve or args.export_xlsx:
                curve = return_curve(
                    days,
                    budget,
                    min_break=config.min_break,
                    max_consecutive_pto=config.max_consecutive_pto,
                    committed=config.committed,
                    long_break_bonus=long_break_bonus,
                )
            if curve is not None and config.min_return is not None:
                chosen = curve.stop_for_min_return(config.min_return)
                objective = {"kind": "min_return", "value": config.min_return}
                last = curve.points[-1]
                objective["full_budget"] = (chosen.pto_spent, chosen.days_in_breaks) == (
                    last.pto_spent, last.days_in_breaks
                )
            elif curve is not None and config.target_days_off is not None:
                chosen = curve.cheapest_for_target(config.target_days_off)
                objective = {"kind": "target_days_off", "value": config.target_days_off}
            if curve is not None and objective is not None:
                objective["budget_per_year"] = chosen.budget
                plan = curve.plans[chosen.budget]
            else:
                plan = optimize_pto(
                    days,
                    budget,
                    min_break=config.min_break,
                    max_consecutive_pto=config.max_consecutive_pto,
                    committed=config.committed,
                    long_break_bonus=long_break_bonus,
                    max_work_streak=args.max_work_streak,
                )
        except ValueError as exc:
            parser.error(str(exc))
            return 2  # pragma: no cover - parser.error exits

    if any(v is not None for v in (args.export_ics, args.export_csv, args.export_xlsx)):
        try:
            written = write_exports(
                config, holidays_by_year, plan, goal=goal_summary, curve=curve,
                include_holidays=not args.no_holidays, ics=args.export_ics,
                csv_path=args.export_csv, xlsx=args.export_xlsx,
            )
        except ExportError as exc:
            parser.error(str(exc))
            return 2  # pragma: no cover - parser.error exits
        for path in written:
            print(f"wrote {path}", file=sys.stderr)

    show_efficiency = args.efficiency or wants_curve or spec is not None
    horizon_block = None
    if spec is not None and plan is not None:
        horizon_block = _horizon_block(spec, plan, config)
    # The curve is always computed for the stop conditions, but printed only on request.
    shown_curve = curve if args.curve else None

    if args.format == "json":
        print(
            render_json(
                config, holidays_by_year, opportunities_by_year, plan,
                efficiency=show_efficiency, curve=shown_curve, objective=objective,
                goal=goal_summary,
                horizon=horizon_json(*horizon_block) if horizon_block else None,
            )
        )
    elif args.format == "ics":
        sys.stdout.write(
            render_ics(
                config, holidays_by_year, plan,
                include_holidays=not args.no_holidays, efficiency=show_efficiency,
                goal=goal_summary,
            )
        )
    else:
        print(
            render_text(
                config,
                holidays_by_year,
                opportunities_by_year,
                plan,
                show_holidays=not args.no_holidays,
                efficiency=show_efficiency,
                curve=shown_curve,
                objective=objective,
                goal=goal_summary,
            ),
            end="",
        )
        if horizon_block:
            print("\n" + "\n".join(describe_horizon(*horizon_block)))
    return 0


def _wants_horizon(args: argparse.Namespace) -> bool:
    return any(
        value is not None
        for value in (args.pto_hours, args.refill_hours, args.through, args.months,
                      args.max_work_streak)
    )


def _horizon_spec(args: argparse.Namespace, today: dt.date) -> HorizonSpec:
    """Validate the planning-from-today options against the rest of the command line."""
    if args.pto_hours is None:
        raise ValueError(
            "--refill-hours, --through, --months and --max-work-streak need --pto-hours "
            "(your balance now, in hours)"
        )
    if args.pto is not None:
        raise ValueError(
            "--pto-hours replaces --pto: give your balance in hours, and the January refill "
            "with --refill-hours"
        )
    if args.start_year is not None or args.end_year is not None or args.from_today:
        raise ValueError(
            "--pto-hours plans from today; --start-year, --end-year and --from-today do "
            "not apply (use --today, --through or --months)"
        )
    if args.max_work_streak is not None and (
        args.goal is not None or args.min_vacation_days is not None or args.max_breaks is not None
        or args.prefer
    ):
        raise ValueError(
            "--max-work-streak belongs to the classic planner and cannot be combined with "
            "--goal or its options"
        )
    return build_horizon(
        today,
        args.pto_hours,
        hours_per_day=args.hours_per_day,
        refill_hours=args.refill_hours if args.refill_hours is not None else 0,
        through=args.through,
        months=args.months,
        carryover_days=args.carryover or 0,
        max_work_streak=args.max_work_streak,
    )


def _horizon_block(spec: HorizonSpec, plan: PtoPlan, config: CompanyConfig):
    """Rest metrics and year-end losses for a plan made from today."""
    # The carryover may have come from a config file rather than --carryover.
    spec = replace(spec, carryover_days=config.carryover)
    years = list(range(spec.today.year, spec.end.year + 2))
    rolled = _rolled_in(plan.budget, plan, years)
    ends = year_ends(plan, rolled, spec.end)
    granted = {y: plan.granted_total(y) for y in sorted(plan.granted_by_year)}
    return spec, rest_metrics(plan.days), ends, granted


def _rolled_in(budget: PtoBudget, result, years: Sequence[int]) -> Dict[int, int]:
    """Days each year received from the one before, mirroring the planner's rollover rule."""
    rolled: Dict[int, int] = {}
    previous = None
    for year in years:
        if previous is not None:
            available = (
                result.granted_by_year.get(previous, 0)
                + result.floating_by_year.get(previous, 0)
                + rolled.get(previous, 0)
            )
            left = max(0, available - result.spent_by_year.get(previous, 0))
            cap = min(budget.carryover, result.granted_by_year.get(previous, 0))
            rolled[year] = min(left, cap)
        previous = year
    return {y: n for y, n in rolled.items() if n}


def _plan_for_goal(
    config: CompanyConfig,
    window_days: Sequence[Day],
    all_holidays: Sequence[ResolvedHoliday],
    start: dt.date,
    end: dt.date,
    start_year: int,
    end_year: int,
):
    """Plan with the chosen goal and score it, and the other goals, with the honest count."""
    budget = PtoBudget(
        per_year=dict(config.pto_by_year),
        default=config.pto_days or 0,
        carryover=config.carryover,
        floating_per_year=dict(config.floating_by_year),
        floating=config.floating_days or 0,
    )
    names: Dict[dt.date, List[str]] = {}
    for holiday in all_holidays:
        bucket = names.setdefault(holiday.observed, [])
        if holiday.name not in bucket:
            bucket.append(holiday.name)
    holiday_names = {date: " / ".join(parts) for date, parts in names.items()}
    padded = build_goal_days(start, end, all_holidays, config.workweek, config.blackout)
    years = list(range(start_year, end_year + 1))
    chosen_name = config.effective_goal
    min_break = config.min_break if config.min_break_explicit else None

    results = {}
    reports = {}
    for name in GOALS:
        spec = goal_spec(name, min_break=min_break, min_vacation_days=config.min_vacation_days)
        result = optimize_goal(
            padded,
            budget,
            spec,
            window=(start, end),
            committed=config.committed,
            prefer=config.prefer,
            max_breaks=config.max_breaks,
            max_consecutive_pto=config.max_consecutive_pto,
        )
        requested = set(result.pto_dates) | {c for c in config.committed if start <= c <= end}
        grants = {
            y: result.granted_by_year.get(y, 0) + result.floating_by_year.get(y, 0)
            for y in years
        }
        results[name] = result
        reports[name] = score_pto(
            requested, holiday_names, workweek=config.workweek, years=years, budgets=grants,
            carried=_rolled_in(budget, result, years),
        )

    result, report = results[chosen_name], reports[chosen_name]
    by_date = {d.date: d for d in result.days}
    breaks = [
        Break(start=b.start, end=b.end, days=[by_date[x] for x in b.dates if x in by_date])
        for b in report.breaks
    ]
    plan = PtoPlan(
        days=[d for d in result.days if start <= d.date <= end],
        breaks=breaks,
        pto_dates=list(result.pto_dates),
        budget=budget,
        spent_by_year=dict(result.spent_by_year),
        granted_by_year=dict(result.granted_by_year),
        min_break=result.spec.thresholds[0][0],
        floating_by_year=dict(result.floating_by_year),
    )
    one = dt.timedelta(days=1)
    pad_start, pad_end = start - PAD_DAYS * one, end + PAD_DAYS * one
    featured_dates = {x for b in report.breaks for x in b.dates}
    summary = GoalSummary(
        goal=chosen_name,
        spec=result.spec,
        method=result.method,
        report=report,
        comparison=[(name, reports[name]) for name in GOALS],
        min_vacation_days=config.min_vacation_days,
        max_breaks=config.max_breaks,
        prefer=sorted(config.prefer),
        blackout_count=len(config.blackout),
        context_holidays=[
            h for h in all_holidays
            if pad_start <= h.observed <= pad_end
            or h.observed in featured_dates
        ],
    )
    return plan, summary


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())

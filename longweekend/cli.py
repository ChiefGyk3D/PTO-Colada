"""Command-line interface."""

from __future__ import annotations

import argparse
import datetime as dt
import sys
from typing import Dict, List, Optional, Sequence

from .catalog import KNOWN, PRESETS, ResolvedHoliday, resolve_year
from .conferences import describe_catalog, load_conferences
from .observances import describe_sets
from .config import CompanyConfig, build_config
from .efficiency import ReturnCurve, return_curve
from .planner import PtoBudget, PtoPlan, optimize_pto
from .report import render_ics, render_json, render_text
from .schedule import Opportunity, build_days, find_opportunities

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
  long-weekend                                  this year and next, US federal holidays
  long-weekend --preset corporate --pto 15      plan 15 PTO days a year
  long-weekend --preset federal --drop "Columbus Day" --add "Day After Thanksgiving=Thanksgiving+1"
  long-weekend --pto 20 --min-break 7           aim for week-long breaks
  long-weekend --preset tech --pto 15 --floating 2     two floating holidays on top
  long-weekend --add "Good Friday" --add "Election Day" --pto 15
  long-weekend --pto 15 --blackout 2026-04-01:2026-04-15 --commit 2026-07-20
  long-weekend --pto 15 --format ics > timeoff.ics
  long-weekend --pto 17 --curve                 where extra PTO stops paying off
  long-weekend --pto 17 --min-return 2.5        stop when a day returns under 2.5 days
  long-weekend --pto 17 --target-days-off 40    the fewest PTO days that reach 40
"""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="long-weekend",
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
        "--style", choices=("weekends", "vacations"), default="weekends",
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
            "days you will take off regardless: a set such as 'jewish', a "
            "holiday name such as \"Yom Kippur\" or \"Passover\", or a date. "
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

    today = dt.date.today()

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

    try:
        start, end, start_year, end_year = resolve_window(args, today)
        config = build_config(
            preset=preset,
            config_path=args.config_path,
            add=args.add,
            drop=args.drop,
            workweek=args.workweek,
            observance=args.observance,
            pto=args.pto,
            pto_by_year=args.pto_year,
            floating=args.floating,
            floating_by_year=args.floating_year,
            carryover=args.carryover,
            min_break=args.min_break,
            max_consecutive_pto=args.max_consecutive_pto,
            min_return=args.min_return,
            target_days_off=args.target_days_off,
            blackout=args.blackout,
            committed=args.committed,
            observe=args.observe,
            conferences=args.conference,
            conference_file=args.conference_file,
            conference_travel=args.conference_travel,
            years=range(start_year, end_year + 1),
            diaspora=not args.israel,
            include_erev=args.erev,
        )
    except (ValueError, FileNotFoundError) as exc:
        parser.error(str(exc))
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
    if has_budget:
        budget = PtoBudget(
            per_year=dict(config.pto_by_year),
            default=config.pto_days or 0,
            carryover=config.carryover,
            floating_per_year=dict(config.floating_by_year),
            floating=config.floating_days or 0,
        )
        long_break_bonus = 1 if args.style == "vacations" else 0
        try:
            if wants_curve:
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
                )
        except ValueError as exc:
            parser.error(str(exc))
            return 2  # pragma: no cover - parser.error exits

    show_efficiency = args.efficiency or wants_curve
    # The curve is always computed for the stop conditions, but printed only on request.
    shown_curve = curve if args.curve else None

    if args.format == "json":
        print(
            render_json(
                config, holidays_by_year, opportunities_by_year, plan,
                efficiency=show_efficiency, curve=shown_curve, objective=objective,
            )
        )
    elif args.format == "ics":
        sys.stdout.write(
            render_ics(
                config, holidays_by_year, plan,
                include_holidays=not args.no_holidays, efficiency=show_efficiency,
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
            ),
            end="",
        )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())

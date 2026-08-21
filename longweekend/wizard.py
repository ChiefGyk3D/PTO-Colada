"""An interactive mode, for people who would rather not learn the flags.

The wizard asks a short series of questions, then does two things: runs the
plan, and prints the command line that would have produced it. The second part
matters more than the first -- nobody wants to answer seven questions every
time, so the wizard's real job is to teach you the one command you actually
need and then get out of the way.
"""

from __future__ import annotations

import argparse
from typing import Callable, List, Optional, Sequence

from .catalog import PRESETS
from .conferences import load_conferences
from .observances import OBSERVANCE_SETS

__all__ = ["run_wizard"]

Ask = Callable[[str], str]
Say = Callable[[str], None]


def run_wizard(
    args: argparse.Namespace,
    ask: Optional[Ask] = None,
    say: Optional[Say] = None,
) -> Optional[List[str]]:
    """Ask the questions and return an argv list, or ``None`` if cancelled."""
    ask = ask or _prompt
    say = say or print

    say("")
    say("Let's plan your time off. Press enter to accept the default in [brackets].")
    say("")

    argv: List[str] = []

    try:
        pto = _number(ask, "How many PTO days do you get a year?", default=15)
        argv += ["--pto", str(pto)]

        floating = _number(
            ask,
            "Any floating holidays on top of that? (discretionary days that expire yearly)",
            default=0,
        )
        if floating:
            argv += ["--floating", str(floating)]

        carryover = _number(
            ask, "How many unused PTO days can roll into next year?", default=0
        )
        if carryover:
            argv += ["--carryover", str(carryover)]

        say("")
        say("  Holiday sets: " + ", ".join(sorted(PRESETS)))
        preset = _choice(
            ask,
            "Which set does your employer observe?",
            options=sorted(PRESETS),
            default="federal",
        )
        if preset != "federal":
            argv += ["--preset", preset]

        say("")
        say("  Observance sets: " + ", ".join(OBSERVANCE_SETS))
        say("  Or name a holiday ('Yom Kippur'), or leave blank for none.")
        observe = _text(ask, "Days you take off yourself?", default="")
        if observe:
            argv += ["--observe", observe]

        say("")
        try:
            known = ", ".join(c.name for c in load_conferences())
        except (ValueError, FileNotFoundError):  # pragma: no cover - bundled file
            known = ""
        if known:
            say(f"  Conferences: {known}")
        conference = _text(ask, "Any conference you plan to attend?", default="")
        if conference:
            argv += ["--conference", conference]
            travel = _number(ask, "Travel days either side?", default=0)
            if travel:
                argv += ["--conference-travel", str(travel)]

        say("")
        style = _choice(
            ask,
            "Spend leftover days on many long weekends, or fewer longer trips?",
            options=["weekends", "vacations"],
            default="weekends",
        )
        if style != "weekends":
            argv += ["--style", style]

        years = _number(ask, "How many years ahead should I plan?", default=2)
        if years != 2:
            argv += ["--years", str(years)]

    except (EOFError, KeyboardInterrupt):  # pragma: no cover - interactive only
        say("\nCancelled.")
        return None

    # Carry over anything they already passed on the command line.
    for flag, value in (("--format", args.format), ("--min-break", args.min_break)):
        if value not in (None, "text"):
            argv += [flag, str(value)]
    if args.israel:
        argv.append("--israel")
    if args.erev:
        argv.append("--erev")

    say("")
    say("Next time, skip the questions:")
    say("")
    say("    long-weekend " + " ".join(_quote(a) for a in argv))
    say("")
    return argv


def _prompt(question: str) -> str:  # pragma: no cover - interactive only
    return input(question)


def _quote(argument: str) -> str:
    return f'"{argument}"' if " " in argument else argument


def _text(ask: Ask, question: str, default: str) -> str:
    shown = f" [{default}]" if default else " [none]"
    answer = ask(f"{question}{shown} ").strip()
    return answer or default


def _number(ask: Ask, question: str, default: int) -> int:
    while True:
        answer = ask(f"{question} [{default}] ").strip()
        if not answer:
            return default
        try:
            value = int(answer)
        except ValueError:
            continue
        if value >= 0:
            return value


def _choice(ask: Ask, question: str, options: Sequence[str], default: str) -> str:
    while True:
        answer = ask(f"{question} [{default}] ").strip().casefold()
        if not answer:
            return default
        for option in options:
            if option.casefold() == answer:
                return option
            if option.casefold().startswith(answer):
                return option

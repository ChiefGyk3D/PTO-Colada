"""Company configuration: presets, config files, and CLI overrides.

No two employers observe the same set of days, so nothing here is hard-coded
into the calculation. A :class:`CompanyConfig` is assembled by layering, in
increasing order of precedence: a named preset, an optional config file, then
explicit command-line overrides.
"""

from __future__ import annotations

import datetime as dt
import json
import math
import os
import re
import stat
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Sequence, Set

from .goals import GOALS
from .conferences import (
    ConferenceOccurrence,
    find_conference,
    load_conferences,
    occurrences_for,
)
from . import islamic
from .observances import PersonalDay, resolve_observances
from .catalog import (
    DEFAULT_WORKWEEK,
    ExplicitDate,
    KNOWN,
    Observance,
    resolve_year,
    PRESETS,
    Rule,
    known_holiday,
    parse_rule,
    parse_workweek,
    rule_from_mapping,
    whole_number,
)

__all__ = [
    "CompanyConfig",
    "MAX_CONFIG_BYTES",
    "MAX_CONFIG_DEPTH",
    "load_config_file",
    "build_config",
    "parse_date",
    "parse_date_ranges",
    "parse_pto_by_year",
    "rule_from_spec",
]


@dataclass
class CompanyConfig:
    name: str = "Default"
    holidays: List[Rule] = field(default_factory=list)
    workweek: Set[int] = field(default_factory=lambda: set(DEFAULT_WORKWEEK))
    observance: Observance = Observance.NEAREST
    pto_days: Optional[int] = None
    pto_by_year: Dict[int, int] = field(default_factory=dict)
    #: Discretionary days granted on top of the fixed holidays. They expire
    #: with the calendar year and never roll over.
    floating_days: Optional[int] = None
    floating_by_year: Dict[int, int] = field(default_factory=dict)
    carryover: int = 0
    min_break: int = 4
    max_consecutive_pto: Optional[int] = None
    #: Stop spending once the next PTO day would return fewer than this many
    #: days in breaks; see :mod:`pto_colada.efficiency`.
    min_return: Optional[float] = None
    #: Spend the fewest PTO days that reach this many days in breaks.
    target_days_off: Optional[int] = None
    blackout: Set[dt.date] = field(default_factory=set)
    committed: Set[dt.date] = field(default_factory=set)
    #: Goal mode (``weekends``, ``vacations`` or ``balanced``) and its options;
    #: see :mod:`pto_colada.goals`. ``None`` everywhere means the classic planner.
    goal: Optional[str] = None
    #: Shortest break that counts as a vacation under the vacation goals.
    min_vacation_days: Optional[int] = None
    #: Dates that earn a bonus when taken as PTO.
    prefer: Set[dt.date] = field(default_factory=set)
    #: Cap on the number of breaks that contain PTO.
    max_breaks: Optional[int] = None
    #: True when ``min_break`` was set by the user rather than left at its default.
    min_break_explicit: bool = False
    #: Personal days off (religious observances, conferences). These are folded
    #: into ``committed``; kept separately so the report can say what each one
    #: was and what it cost.
    observances: List[PersonalDay] = field(default_factory=list)
    #: Announced dates for Muslim holidays, from the config file's
    #: ``observances`` mapping: canonical holiday name -> dates.
    observance_dates: Dict[str, List[dt.date]] = field(default_factory=dict)
    conferences: List[ConferenceOccurrence] = field(default_factory=list)
    #: Days taken either side of a conference to travel.
    conference_travel: int = 0

    def holiday_names(self) -> List[str]:
        return [rule.name for rule in self.holidays]

    @property
    def wants_goal(self) -> bool:
        """True when any goal-mode option was set, which switches to honest planning."""
        return bool(
            self.goal is not None
            or self.min_vacation_days is not None
            or self.prefer
            or self.max_breaks is not None
        )

    @property
    def effective_goal(self) -> str:
        """The goal in force: the one named, else vacations if a vacation length was given."""
        if self.goal is not None:
            return self.goal
        return "vacations" if self.min_vacation_days is not None else "weekends"


def parse_date(value: str) -> dt.date:
    text = str(value).strip()
    try:
        return dt.date.fromisoformat(text)
    except ValueError:
        raise ValueError(f"expected a YYYY-MM-DD date, got {value!r}") from None


#: Longest single date range accepted, in days (ten years).
MAX_RANGE_DAYS = 3660
#: Most days all the ranges of one option may declare together.
MAX_RANGE_TOTAL_DAYS = 20000
_MIN_YEAR, _MAX_YEAR = 1900, 2200


def _bounded_date(text: str, option: str) -> dt.date:
    date = parse_date(text)
    if not _MIN_YEAR <= date.year <= _MAX_YEAR:
        raise ValueError(
            f"{option}: {text.strip()} is outside the years {_MIN_YEAR} to {_MAX_YEAR}"
        )
    return date


def parse_date_ranges(values: Iterable[str], option: str = "date range") -> Set[dt.date]:
    """Expand ``YYYY-MM-DD`` and ``YYYY-MM-DD:YYYY-MM-DD`` specs into a date set.

    ``option`` names the flag or key for error messages. Ranges are checked
    before they are expanded: one range may span at most :data:`MAX_RANGE_DAYS`
    days and all the ranges together at most :data:`MAX_RANGE_TOTAL_DAYS`.
    """
    dates: Set[dt.date] = set()
    declared = 0
    for value in values:
        for chunk in str(value).split(","):
            chunk = chunk.strip()
            if not chunk:
                continue
            if ":" in chunk:
                start_text, _, end_text = chunk.partition(":")
                start, end = _bounded_date(start_text, option), _bounded_date(end_text, option)
                if end < start:
                    raise ValueError(f"{option}: date range {chunk!r} ends before it starts")
                size = (end - start).days + 1
                if size > MAX_RANGE_DAYS:
                    raise ValueError(
                        f"{option}: date range {chunk!r} is longer than {MAX_RANGE_DAYS} days"
                    )
                declared += size
                if declared > MAX_RANGE_TOTAL_DAYS:
                    raise ValueError(
                        f"{option}: the ranges together cover more than "
                        f"{MAX_RANGE_TOTAL_DAYS} days in total"
                    )
                current = start
                while current <= end:
                    dates.add(current)
                    current += dt.timedelta(days=1)
            else:
                declared += 1
                if declared > MAX_RANGE_TOTAL_DAYS:
                    raise ValueError(
                        f"{option}: more than {MAX_RANGE_TOTAL_DAYS} days in total"
                    )
                dates.add(_bounded_date(chunk, option))
    return dates


def parse_pto_by_year(values: Iterable[str]) -> Dict[int, int]:
    """Parse ``2026=15`` style per-year budgets."""
    budgets: Dict[int, int] = {}
    for value in values:
        for chunk in str(value).split(","):
            chunk = chunk.strip()
            if not chunk:
                continue
            if "=" not in chunk:
                raise ValueError(f"expected YEAR=DAYS, got {chunk!r}")
            year_text, _, days_text = chunk.partition("=")
            try:
                year, days = int(year_text), int(days_text)
            except ValueError:
                raise ValueError(f"expected YEAR=DAYS with whole numbers, got {chunk!r}") from None
            if days < 0:
                raise ValueError(f"PTO budget for {year} cannot be negative")
            budgets[year] = days
    return budgets


def rule_from_spec(spec: str) -> Rule:
    """Resolve ``--add`` input: a known holiday's name, or a full ``Name=when`` spec.

    Naming a catalogued holiday is how you build the many variations employers
    actually use -- ``--add "Good Friday"`` rather than making the caller know
    that Good Friday is two days before Easter.
    """
    text = str(spec).strip()
    if "=" not in text:
        rule = known_holiday(text)
        if rule is not None:
            return rule
        raise ValueError(
            f"unknown holiday {text!r}. Either name one of the holidays this tool "
            f"knows ({', '.join(sorted(KNOWN))}) or give a date, "
            f'like "{text}=07-04". Run --list-known to see the catalog.'
        )
    return parse_rule(text)


#: Largest config file read, in bytes. A company holiday file is a few kilobytes.
MAX_CONFIG_BYTES = 64 * 1024
#: Deepest nesting of mappings and lists accepted; real configs are about four deep.
MAX_CONFIG_DEPTH = 16
#: Most nodes (mappings, lists and scalars) accepted in one config file.
MAX_CONFIG_NODES = 10000
#: Longest run of digits read as an integer; longer ones are rejected the same way
#: on every Python version.
_MAX_INT_DIGITS = 18
_YAML_INT = "tag:yaml.org,2002:int"
_PLAIN_INT = re.compile(r"[-+]?(?:0|[1-9][0-9]{0,%d})" % (_MAX_INT_DIGITS - 1))
#: Explicit YAML tags whose values no config key can use.
_REJECTED_TAGS = ("binary", "set", "omap", "pairs")


def _read_regular_file(path: str, label: str = "config file") -> bytes:
    """Read a config file, refusing anything but a regular file within the size cap.

    The file is opened non-blocking and checked with ``fstat`` on the open
    descriptor, so a FIFO cannot hang the read and the check cannot race a swap.
    """
    flags = os.O_RDONLY | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_CLOEXEC", 0)
    try:
        fd = os.open(path, flags)
    except FileNotFoundError:
        raise FileNotFoundError(f"{label} not found: {path}") from None
    except OSError as exc:
        raise ValueError(f"cannot open {label} {path}: {exc.strerror}") from None
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            raise ValueError(f"{path} is not a regular file; give the path of a JSON or YAML file")
        if info.st_size > MAX_CONFIG_BYTES:
            raise ValueError(
                f"{path} is too large ({info.st_size} bytes; the limit is {MAX_CONFIG_BYTES})"
            )
        with os.fdopen(fd, "rb") as handle:
            fd = -1  # the file object owns the descriptor now
            data = handle.read(MAX_CONFIG_BYTES + 1)
    finally:
        if fd >= 0:
            os.close(fd)
    if len(data) > MAX_CONFIG_BYTES:
        raise ValueError(f"{path} is too large (the limit is {MAX_CONFIG_BYTES} bytes)")
    return data


class _HugeNumber:
    """Stands in for a JSON integer with too many digits until its location is known."""


def _json_hooks(path: str):
    """``json.loads`` keyword arguments that refuse over-long integers (NaN and
    infinity parse as floats and are rejected by name where a number is read)."""

    def parse_int(text: str):
        if len(text.lstrip("-")) > _MAX_INT_DIGITS:
            return _HugeNumber()  # reported with its key path by _check_text
        return int(text)

    return {
        "object_pairs_hook": _json_object(path),
        "parse_int": parse_int,
    }


def _json_object(path: str):
    def hook(pairs):
        result: Dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"{path}: duplicate key {key!r}")
            result[key] = value
        return result

    return hook


def _yaml_loader(path: str):
    """A SafeLoader that rejects duplicate keys, aliases and merge keys, bounds the
    node count and depth while composing, and reads integers as plain decimals only."""
    import yaml  # type: ignore[import-untyped]

    class StrictLoader(yaml.SafeLoader):
        def __init__(self, stream):  # type: ignore[no-untyped-def]
            super().__init__(stream)
            self._nodes = 0
            self._depth = 0

        def compose_node(self, parent, index):  # type: ignore[no-untyped-def]
            if self.check_event(yaml.events.AliasEvent):
                raise ValueError(
                    f"{path}: YAML aliases are not allowed in config files; write the value out"
                )
            self._nodes += 1
            if self._nodes > MAX_CONFIG_NODES:
                raise ValueError(
                    f"{path} has too many values (limit {MAX_CONFIG_NODES})"
                )
            self._depth += 1
            try:
                if self._depth > MAX_CONFIG_DEPTH:
                    raise ValueError(
                        f"{path} is nested too deeply (limit {MAX_CONFIG_DEPTH} levels)"
                    )
                return super().compose_node(parent, index)
            finally:
                self._depth -= 1

        def construct_object(self, node, deep=False):  # type: ignore[no-untyped-def]
            try:
                return super().construct_object(node, deep)
            except (ValueError, yaml.YAMLError, RecursionError, MemoryError):
                raise
            except Exception:
                # Any other failure inside a constructor (a bad !!bool raises
                # KeyError, for one) is a bad value in the file, not a crash.
                raise ValueError(_bad_value(path, node)) from None

        def construct_mapping(self, node, deep=False):  # type: ignore[no-untyped-def]
            seen = set()
            for key_node, _ in node.value:
                if key_node.tag == "tag:yaml.org,2002:merge":
                    raise ValueError(
                        f"{path}: YAML merge keys ('<<') are not allowed in config files"
                    )
                key = self.construct_object(key_node, deep=True)
                try:
                    duplicate = key in seen
                    seen.add(key)
                except TypeError:
                    continue  # unhashable key; the base class reports it
                if duplicate:
                    raise ValueError(f"{path}: duplicate key {key!r}")
            return super().construct_mapping(node, deep)

    # Keep dates as text so a bad one is reported with its holiday's name
    # instead of a bare "day is out of range" from inside the YAML library.
    StrictLoader.add_constructor(
        "tag:yaml.org,2002:timestamp", lambda loader, node: loader.construct_scalar(node)
    )
    # YAML 1.1 reads 010 as 8, 1:20 as 80 and 0x10 as 16. Only plain decimals
    # are integers here; everything else stays text and is rejected where a
    # number is required.
    StrictLoader.yaml_implicit_resolvers = {
        first: [(tag, regexp) for tag, regexp in resolvers if tag != _YAML_INT]
        for first, resolvers in yaml.SafeLoader.yaml_implicit_resolvers.items()
    }
    StrictLoader.add_implicit_resolver(
        _YAML_INT,
        re.compile("^" + _PLAIN_INT.pattern + "$"),
        list("-+0123456789"),
    )

    # The same rule for an explicit !!int tag, which skips the resolver.
    def construct_int(loader, node):  # type: ignore[no-untyped-def]
        text = loader.construct_scalar(node)
        if not isinstance(text, str) or not _PLAIN_INT.fullmatch(text):
            raise ValueError(
                f"{path}: {text!r} is not a whole number written in plain decimal, like 17"
            )
        return int(text)

    default_float = yaml.SafeLoader.yaml_constructors["tag:yaml.org,2002:float"]

    def construct_float(loader, node):  # type: ignore[no-untyped-def]
        value = default_float(loader, node)
        text = str(loader.construct_scalar(node)).lower()
        if value != value or value in (float("inf"), float("-inf")):
            if ".inf" not in text and ".nan" not in text:
                raise ValueError(f"{path}: {text!r} is not a finite number")
        return value

    def reject_tag(loader, node):  # type: ignore[no-untyped-def]
        raise ValueError(f"{path}: the YAML tag {node.tag} is not allowed in config files")

    StrictLoader.add_constructor(_YAML_INT, construct_int)
    StrictLoader.add_constructor("tag:yaml.org,2002:float", construct_float)
    for name in _REJECTED_TAGS:
        StrictLoader.add_constructor("tag:yaml.org,2002:" + name, reject_tag)
    return StrictLoader


def _bad_value(path: str, node: object) -> str:
    """Name the tag and position of a value that could not be read, never its text."""
    tag = str(getattr(node, "tag", ""))
    if tag.startswith("tag:yaml.org,2002:"):
        tag = "!!" + tag[len("tag:yaml.org,2002:"):]
    mark = getattr(node, "start_mark", None)
    where = f" at line {mark.line + 1}, column {mark.column + 1}" if mark is not None else ""
    return f"{path} is invalid YAML{where}: could not read a {tag or 'value'} value"


def _yaml_problem(exc: Exception) -> str:
    """Describe a YAML error by position and problem only; the source line is left out."""
    mark = getattr(exc, "problem_mark", None)
    problem = getattr(exc, "problem", None) or getattr(exc, "context", None) or "syntax error"
    if mark is not None:
        return f"invalid YAML at line {mark.line + 1}, column {mark.column + 1}: {problem}"
    return f"invalid YAML: {problem}"


def _check_depth(data: object, path: str) -> None:
    """Bound nesting depth and total node count of parsed JSON (YAML is bounded while composing)."""
    stack = [(data, 1)]
    nodes = 0
    while stack:
        item, depth = stack.pop()
        nodes += 1
        if nodes > MAX_CONFIG_NODES:
            raise ValueError(f"{path} has too many values (limit {MAX_CONFIG_NODES})")
        if isinstance(item, dict):
            nodes += len(item)  # the keys, counted as the YAML composer counts them
            if nodes > MAX_CONFIG_NODES:
                raise ValueError(f"{path} has too many values (limit {MAX_CONFIG_NODES})")
            children = list(item.values())
        elif isinstance(item, list):
            children = list(item)
        else:
            continue
        if depth > MAX_CONFIG_DEPTH:
            raise ValueError(f"{path} is nested too deeply (limit {MAX_CONFIG_DEPTH} levels)")
        stack.extend((child, depth + 1) for child in children)


def load_json_file(path: str, label: str = "config file") -> object:
    """Read a JSON file under the same limits as a config file.

    Regular file only, within :data:`MAX_CONFIG_BYTES`; duplicate keys, over-long
    integers, lone surrogates, and nesting or node counts past the config limits
    are all ``ValueError``. Any top-level JSON value is returned.
    """
    raw = _read_regular_file(path, label)
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise ValueError(f"{path} is not valid UTF-8 text") from None
    try:
        data = json.loads(text, **_json_hooks(path))
    except json.JSONDecodeError as exc:
        raise ValueError(f"{path} is not valid JSON: {exc}") from None
    except RecursionError:
        raise ValueError(f"{path} is nested too deeply to read") from None
    except (ValueError, MemoryError):
        raise
    except Exception:
        raise ValueError(f"{path} is not valid JSON: a value could not be read") from None
    _check_depth(data, path)
    _check_text(data, path)
    return data


def load_config_file(path: str) -> Dict[str, object]:
    """Load a JSON or YAML company config.

    YAML needs PyYAML; JSON works with the stdlib alone. The file must be a
    regular file no larger than :data:`MAX_CONFIG_BYTES`; duplicate keys,
    YAML aliases and merge keys, and over-deep nesting are errors.
    """
    raw = _read_regular_file(path)
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise ValueError(f"{path} is not valid UTF-8 text") from None

    suffix = os.path.splitext(path)[1].lower()
    try:
        if suffix in {".yaml", ".yml"}:
            try:
                import yaml  # type: ignore[import-untyped]
            except ImportError:
                raise ValueError(
                    f"{path} is YAML but PyYAML is not installed. "
                    "Install it with 'pip install pyyaml', or use a .json config instead."
                ) from None
            loader = _yaml_loader(path)(text)
            try:
                node = loader.get_single_node()
                data = loader.construct_document(node) if node is not None else None
            except yaml.YAMLError as exc:
                raise ValueError(f"{path} is {_yaml_problem(exc)}") from None
            except (ValueError, RecursionError, MemoryError):
                raise
            except Exception:
                raise ValueError(f"{path} is invalid YAML: a value could not be read") from None
            finally:
                loader.dispose()
        else:
            try:
                data = json.loads(text, **_json_hooks(path))
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path} is not valid JSON: {exc}") from None
            except (ValueError, RecursionError, MemoryError):
                raise
            except Exception:
                raise ValueError(f"{path} is not valid JSON: a value could not be read") from None
    except RecursionError:
        raise ValueError(f"{path} is nested too deeply to read") from None

    if data is None:
        data = {}
    if not isinstance(data, dict):
        raise ValueError(f"{path} must contain a mapping at the top level")
    _check_depth(data, path)
    _check_text(data, path)
    return data


def build_config(
    preset: str = "federal",
    config_path: Optional[str] = None,
    add: Sequence[str] = (),
    drop: Sequence[str] = (),
    workweek: Optional[str] = None,
    observance: Optional[str] = None,
    pto: Optional[int] = None,
    pto_by_year: Sequence[str] = (),
    floating: Optional[int] = None,
    floating_by_year: Sequence[str] = (),
    carryover: Optional[int] = None,
    min_break: Optional[int] = None,
    max_consecutive_pto: Optional[int] = None,
    min_return: Optional[float] = None,
    target_days_off: Optional[int] = None,
    blackout: Sequence[str] = (),
    committed: Sequence[str] = (),
    goal: Optional[str] = None,
    min_vacation_days: Optional[int] = None,
    prefer: Sequence[str] = (),
    max_breaks: Optional[int] = None,
    observe: Sequence[str] = (),
    conferences: Sequence[str] = (),
    conference_file: Optional[str] = None,
    conference_travel: int = 0,
    years: Sequence[int] = (),
    diaspora: bool = True,
    include_erev: bool = False,
    muslim_shift: int = 0,
) -> CompanyConfig:
    """Layer preset, config file and CLI overrides into one configuration."""
    if preset not in PRESETS:
        raise ValueError(
            f"unknown preset {preset!r}; choose one of: {', '.join(sorted(PRESETS))}"
        )

    config = CompanyConfig(name=preset, holidays=list(PRESETS[preset]))

    if config_path:
        _apply_file(config, load_config_file(config_path), config_path)

    for spec in add:
        config.holidays.append(rule_from_spec(spec))
    if drop:
        wanted = {_key(name) for value in drop for name in str(value).split(",") if name.strip()}
        known = {_key(rule.name) for rule in config.holidays}
        unknown = wanted - known
        if unknown:
            raise ValueError(
                "cannot drop holiday(s) that are not in this set: "
                + ", ".join(sorted(unknown))
                + ". Available: "
                + ", ".join(sorted(rule.name for rule in config.holidays))
            )
        config.holidays = [rule for rule in config.holidays if _key(rule.name) not in wanted]

    _reject_duplicates(config.holidays)
    _reject_same_day(config.holidays)

    if workweek is not None:
        config.workweek = parse_workweek(workweek)
    if observance is not None:
        config.observance = Observance.parse(observance)
    if pto is not None:
        config.pto_days = pto
    if pto_by_year:
        config.pto_by_year.update(parse_pto_by_year(pto_by_year))
    if floating is not None:
        config.floating_days = floating
    if floating_by_year:
        config.floating_by_year.update(parse_pto_by_year(floating_by_year))
    if carryover is not None:
        config.carryover = carryover
    if min_break is not None:
        config.min_break = min_break
        config.min_break_explicit = True
    if max_consecutive_pto is not None:
        config.max_consecutive_pto = max_consecutive_pto
    if goal is not None:
        config.goal = goal
    if min_vacation_days is not None:
        config.min_vacation_days = min_vacation_days
    if prefer:
        config.prefer |= parse_date_ranges(prefer, "prefer")
    if max_breaks is not None:
        config.max_breaks = max_breaks
    if min_return is not None:
        config.min_return = min_return
    if target_days_off is not None:
        config.target_days_off = target_days_off
    if blackout:
        config.blackout |= parse_date_ranges(blackout, "blackout")
    if committed:
        config.committed |= parse_date_ranges(committed, "commit")

    if conference_travel:
        config.conference_travel = conference_travel

    muslim_shift = islamic.check_shift(muslim_shift)
    if observe or config.observance_dates:
        config.observances = resolve_observances(
            observe,
            years or _default_years(),
            diaspora=diaspora,
            include_erev=include_erev,
            shift=muslim_shift,
            overrides=config.observance_dates,
        )
        config.committed |= {o.date for o in config.observances}

    if muslim_shift and not any(o.source == "muslim" for o in config.observances):
        raise ValueError(
            "the shift moves Muslim holidays, and none are selected; "
            "add --observe muslim (or a holiday name such as \"Eid al-Fitr\")"
        )

    if conferences:
        catalog = load_conferences(conference_file)
        for name in conferences:
            for chunk in str(name).split(","):
                if not chunk.strip():
                    continue
                conference = find_conference(chunk.strip(), catalog)
                for occurrence in occurrences_for(conference, years or _default_years()):
                    config.conferences.append(occurrence)
                    dates = set(occurrence.dates())
                    for step in range(1, config.conference_travel + 1):
                        dates.add(occurrence.start - dt.timedelta(days=step))
                        dates.add(occurrence.end + dt.timedelta(days=step))
                    config.committed |= dates
        config.conferences.sort(key=lambda o: o.start)

    overlap = config.blackout & config.committed
    if overlap:
        raise ValueError(
            "date(s) are both blacked out and committed: "
            + ", ".join(d.isoformat() for d in sorted(overlap))
        )

    _validate_numbers(config)
    return config


def _validate_numbers(config: CompanyConfig) -> None:
    """Reject nonsensical budgets and limits wherever they came from.

    The planner checks its own arguments, but it only runs when a PTO budget
    is set, so a bad --min-break or --max-consecutive-pto would otherwise pass
    silently in the report-only path. Checking the assembled config catches
    values from config files and the command line alike.
    """
    if config.pto_days is not None and config.pto_days < 0:
        raise ValueError(f"PTO budget cannot be negative (got {config.pto_days})")
    for year, days in sorted(config.pto_by_year.items()):
        if days < 0:
            raise ValueError(f"PTO budget for {year} cannot be negative (got {days})")
    if config.floating_days is not None and config.floating_days < 0:
        raise ValueError(f"floating holidays cannot be negative (got {config.floating_days})")
    for year, days in sorted(config.floating_by_year.items()):
        if days < 0:
            raise ValueError(f"floating holidays for {year} cannot be negative (got {days})")
    if config.carryover < 0:
        raise ValueError(f"carryover cannot be negative (got {config.carryover})")
    if config.min_break < 1:
        raise ValueError(f"min_break must be at least 1 (got {config.min_break})")
    if config.conference_travel < 0:
        raise ValueError(
            f"conference travel days cannot be negative (got {config.conference_travel})"
        )
    if config.max_consecutive_pto is not None and config.max_consecutive_pto < 1:
        raise ValueError(
            f"max_consecutive_pto must be at least 1 (got {config.max_consecutive_pto})"
        )
    # "not x >= 0" also rejects NaN, which compares false either way.
    if config.min_return is not None and (
        not config.min_return >= 0 or math.isinf(config.min_return)
    ):
        raise ValueError(
            f"min_return must be a finite number, zero or more (got {config.min_return})"
        )
    if config.target_days_off is not None and config.target_days_off < 1:
        raise ValueError(
            f"target_days_off must be at least 1 (got {config.target_days_off})"
        )
    if config.goal is not None and config.goal not in GOALS:
        raise ValueError(
            f"goal must be one of {', '.join(GOALS)} (got {config.goal!r})"
        )
    if config.min_vacation_days is not None and config.min_vacation_days < 2:
        raise ValueError(
            f"min_vacation_days must be at least 2 (got {config.min_vacation_days})"
        )
    if config.max_breaks is not None and config.max_breaks < 1:
        raise ValueError(f"max_breaks must be at least 1 (got {config.max_breaks})")
    overlap = config.prefer & config.blackout
    if overlap:
        raise ValueError(
            "date(s) are both preferred and blacked out: "
            + ", ".join(d.isoformat() for d in sorted(overlap))
        )
    if config.wants_goal and (
        config.min_return is not None or config.target_days_off is not None
    ):
        raise ValueError(
            "--goal and its options plan with the honest count; --min-return and "
            "--target-days-off use the classic planner, so use one or the other"
        )
    if config.min_return is not None and config.target_days_off is not None:
        raise ValueError(
            "min_return and target_days_off are alternatives: each decides how much "
            "PTO to spend, so set only one"
        )


def _default_years() -> List[int]:
    """The window the CLI would have used, for callers that skip it."""
    this_year = dt.date.today().year
    return [this_year, this_year + 1]


#: Keys where an empty value (null) means the same as an empty list.
_EMPTY_LIST_KEYS = ("add", "drop", "blackout", "committed", "prefer")
_FILE_KEYS = _EMPTY_LIST_KEYS + (
    "name", "preset", "holidays", "workweek", "observance", "pto_days", "pto_by_year",
    "floating_days", "floating_by_year", "carryover", "min_break", "goal",
    "min_vacation_days", "max_breaks", "max_consecutive_pto", "min_return",
    "target_days_off", "conference_travel", "observances",
)


def _name(value: object, path: str) -> str:
    if isinstance(value, bool):
        raise ValueError(
            f"{path}: 'name' was read as a YAML boolean ({str(value).lower()}); YAML treats "
            'yes, no, on and off as booleans, so write it in quotes, name: "no"'
        )
    if isinstance(value, int):
        return str(value)
    if isinstance(value, str):
        return value
    raise ValueError(f"{path}: 'name' must be text; write it in quotes, name: \"2027\" (got {value!r})")


def _apply_file(config: CompanyConfig, data: Dict[str, object], path: str) -> None:
    data = dict(data)
    for key in _FILE_KEYS:
        if key in data and data[key] is None:
            if key in _EMPTY_LIST_KEYS:
                del data[key]  # an empty list
            else:
                raise ValueError(f"{path}: {key!r} is empty; delete the line or give it a value")

    if "name" in data:
        config.name = _name(data["name"], path)

    if "preset" in data:
        base = _text(data["preset"], "preset", path)
        if base not in PRESETS:
            raise ValueError(
                f"{path}: unknown preset {base!r}; choose one of: {', '.join(sorted(PRESETS))}"
            )
        config.holidays = list(PRESETS[base])

    if "holidays" in data:
        entries = data["holidays"]
        if not isinstance(entries, list):
            raise ValueError(f"{path}: 'holidays' must be a list")
        rules: List[Rule] = []
        for position, entry in enumerate(entries, start=1):
            try:
                if isinstance(entry, str):
                    rules.append(rule_from_spec(entry))
                elif isinstance(entry, dict):
                    rules.append(rule_from_mapping(entry))
                else:
                    raise ValueError(f"holiday entries must be strings or mappings, got {entry!r}")
            except ValueError as exc:
                raise ValueError(f"{path}: 'holidays' entry {position}: {exc}") from None
        # 'holidays' replaces the preset; use 'add'/'drop' to amend one instead.
        config.holidays = rules

    if "add" in data:
        for position, spec in enumerate(_entries(data["add"], "add", path), start=1):
            try:
                config.holidays.append(
                    rule_from_spec(spec) if isinstance(spec, str) else rule_from_mapping(spec)
                )
            except ValueError as exc:
                raise ValueError(f"{path}: 'add' entry {position}: {exc}") from None
    dropped = {_key(name) for name in _text_list(data.get("drop"), "drop", path)} if "drop" in data else set()
    if dropped:
        config.holidays = [r for r in config.holidays if _key(r.name) not in dropped]

    if "workweek" in data:
        config.workweek = parse_workweek(",".join(_text_list(data["workweek"], "workweek", path)))
    if "observance" in data:
        config.observance = Observance.parse(_text(data["observance"], "observance", path))
    if "pto_days" in data:
        config.pto_days = _file_int(data["pto_days"], "pto_days", path)
    if "pto_by_year" in data:
        config.pto_by_year.update(_year_budgets(data["pto_by_year"], "pto_by_year", path))
    if "floating_days" in data:
        config.floating_days = _file_int(data["floating_days"], "floating_days", path)
    if "floating_by_year" in data:
        config.floating_by_year.update(
            _year_budgets(data["floating_by_year"], "floating_by_year", path)
        )
    if "carryover" in data:
        config.carryover = _file_int(data["carryover"], "carryover", path)
    if "min_break" in data:
        config.min_break = _file_int(data["min_break"], "min_break", path)
        config.min_break_explicit = True
    if "goal" in data:
        config.goal = _text(data["goal"], "goal", path)
    if "min_vacation_days" in data:
        config.min_vacation_days = _file_int(data["min_vacation_days"], "min_vacation_days", path)
    if "max_breaks" in data:
        config.max_breaks = _file_int(data["max_breaks"], "max_breaks", path)
    if "prefer" in data:
        config.prefer |= parse_date_ranges(_text_list(data["prefer"], "prefer", path), "prefer")
    if "max_consecutive_pto" in data:
        config.max_consecutive_pto = _file_int(data["max_consecutive_pto"], "max_consecutive_pto", path)
    if "min_return" in data:
        config.min_return = _file_number(data["min_return"], "min_return", path)
    if "target_days_off" in data:
        config.target_days_off = _file_int(data["target_days_off"], "target_days_off", path)
    if "conference_travel" in data:
        config.conference_travel = _file_int(data["conference_travel"], "conference_travel", path)
    if "observances" in data:
        config.observance_dates = _observance_dates(data["observances"], path)
    if "blackout" in data:
        config.blackout |= parse_date_ranges(_text_list(data["blackout"], "blackout", path), "blackout")
    if "committed" in data:
        config.committed |= parse_date_ranges(_text_list(data["committed"], "committed", path), "committed")


#: Most holidays, and most dates per holiday, an ``observances`` mapping may name.
MAX_OBSERVANCE_ENTRIES = 32
MAX_OBSERVANCE_DATES = 8
_MAX_OBSERVANCE_NAME = 64
_ISO_DATE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")


def _observance_dates(raw: object, path: str) -> Dict[str, List[dt.date]]:
    """``{holiday name: date or dates}`` for announced Muslim holiday dates.

    Names must be Muslim holidays this program knows; dates are strict
    ``YYYY-MM-DD`` text (ASCII digits only) in 1900 to 2200. How close a date
    must be to the calendar's is checked when the holidays are resolved.
    """
    if not isinstance(raw, dict):
        raise ValueError(
            f"{path}: 'observances' must be a mapping of holiday name to date, "
            'e.g. observances: {"Eid al-Fitr": "2027-03-09"}'
        )
    if len(raw) > MAX_OBSERVANCE_ENTRIES:
        raise ValueError(
            f"{path}: 'observances' names {len(raw)} holidays; at most "
            f"{MAX_OBSERVANCE_ENTRIES} are accepted"
        )
    result: Dict[str, List[dt.date]] = {}
    for key, value in raw.items():
        if not isinstance(key, str) or len(key) > _MAX_OBSERVANCE_NAME:
            raise ValueError(
                f"{path}: 'observances' has a holiday name that is not short text: "
                f"{str(key)[:40]!r}"
            )
        name = islamic.canonical_name(key)
        if name is None:
            raise ValueError(
                f"{path}: 'observances' entry {key!r} is not a Muslim holiday name; use one of: "
                + ", ".join(islamic.HOLIDAY_NAMES)
            )
        if name in result:
            raise ValueError(f"{path}: 'observances' names {name} more than once")
        items = value if isinstance(value, list) else [value]
        if not items or len(items) > MAX_OBSERVANCE_DATES:
            raise ValueError(
                f"{path}: 'observances' {key!r} needs between 1 and "
                f"{MAX_OBSERVANCE_DATES} dates"
            )
        dates: List[dt.date] = []
        for item in items:
            if not isinstance(item, str) or not _ISO_DATE.fullmatch(item):
                raise ValueError(
                    f"{path}: 'observances' {key!r} must be YYYY-MM-DD dates in quotes "
                    f"(got {str(item)[:40]!r})"
                )
            try:
                date = dt.date.fromisoformat(item)
            except ValueError:
                raise ValueError(f"{path}: 'observances' {key!r}: {item!r} is not a real date") from None
            if not _MIN_YEAR <= date.year <= _MAX_YEAR:
                raise ValueError(
                    f"{path}: 'observances' {key!r}: {item} is outside the years "
                    f"{_MIN_YEAR} to {_MAX_YEAR}"
                )
            dates.append(date)
        result[name] = dates
    return result


def _text(value: object, key: str, path: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{path}: {key!r} must be text (got {value!r})")
    return value


def _text_list(value: object, key: str, path: str) -> List[str]:
    """A string or a list of strings; anything else is an error naming the entry."""
    if isinstance(value, str):
        return [value]
    if not isinstance(value, list):
        raise ValueError(f"{path}: {key!r} must be text or a list of text (got {value!r})")
    for position, item in enumerate(value, start=1):
        if not isinstance(item, str):
            raise ValueError(f"{path}: {key!r} entry {position} must be text (got {item!r})")
    return list(value)


def _entries(value: object, key: str, path: str) -> List[object]:
    """Holiday entries: a string or mapping, or a list of them."""
    items = value if isinstance(value, list) else [value]
    for position, item in enumerate(items, start=1):
        if not isinstance(item, (str, dict)):
            raise ValueError(
                f"{path}: {key!r} entry {position} must be text or a mapping (got {item!r})"
            )
    return list(items)


def _check_text(data: object, path: str) -> None:
    """Reject strings that cannot be written out as UTF-8 (lone surrogates)."""
    stack = [(data, "the top level")]
    while stack:
        item, where = stack.pop()
        if isinstance(item, _HugeNumber):
            raise ValueError(f"{path}: the number at {where} has too many digits")
        if isinstance(item, str):
            try:
                item.encode("utf-8")
            except UnicodeEncodeError:
                raise ValueError(
                    f"{path}: text at {where} is not valid UTF-8 (a lone surrogate character)"
                ) from None
        elif isinstance(item, dict):
            for key, value in item.items():
                if isinstance(key, str):
                    try:
                        key.encode("utf-8")
                    except UnicodeEncodeError:
                        raise ValueError(
                            f"{path}: a key under {where} is not valid UTF-8 "
                            "(a lone surrogate character)"
                        ) from None
                    label = f"{where}.{key}" if where != "the top level" else key
                else:
                    label = f"{where}.{key!r}"
                stack.append((value, label))
        elif isinstance(item, list):
            for position, value in enumerate(item):
                stack.append((value, f"{where}[{position}]"))


def _file_number(value: object, key: str, path: str) -> float:
    """A number from a config file; null, booleans and text are errors, not zero."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{path}: {key!r} must be a number (got {value!r})")
    try:
        return float(value)
    except OverflowError:
        raise ValueError(f"{path}: {key!r} is too large (got a {len(str(value))}-digit number)") from None


def _file_int(value: object, key: str, path: str) -> int:
    """A whole number from a config file; see :func:`whole_number` for what is accepted.

    YAML 1.1 forms such as 010 or 1:20 arrive as text and are rejected with a
    message that says to write plain digits.
    """
    return whole_number(value, f"{path}: {key!r}")


_YEAR_KEY = re.compile(r"^[0-9]{1,8}$")


def _year_budgets(raw: object, key: str, path: str) -> Dict[int, int]:
    """``{year: days}`` from a config file; years that normalize to one collide."""
    if not isinstance(raw, dict):
        raise ValueError(f"{path}: {key!r} must be a mapping of year to days")
    result: Dict[int, int] = {}
    for year_key, days in raw.items():
        if isinstance(year_key, bool) or not (
            isinstance(year_key, int) or (isinstance(year_key, str) and _YEAR_KEY.match(year_key))
        ):
            raise ValueError(f"{path}: {key!r} has a year key that is not a year: {year_key!r}")
        year = int(year_key)
        if not 1 <= year <= 9999:
            raise ValueError(f"{path}: {key!r} has a year key out of range: {year_key!r}")
        if year in result:
            raise ValueError(f"{path}: year {year} appears more than once in {key!r}")
        result[year] = _file_int(days, f"{key}[{year}]", path)
    return result


def _as_list(value: object) -> List[object]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def _reject_duplicates(rules: Sequence[Rule]) -> None:
    seen: Set[str] = set()
    for rule in rules:
        key = _key(rule.name)
        if key in seen:
            raise ValueError(
                f"holiday {rule.name!r} is defined more than once; "
                "drop the preset entry first or rename the addition"
            )
        seen.add(key)


#: Years sampled when looking for holidays that are always the same day.
_COLLISION_YEARS = range(2024, 2032)


def _reject_same_day(rules: Sequence[Rule]) -> None:
    """Reject two holidays that land on the same date in *every* year.

    Two names for one day -- Columbus Day and Indigenous Peoples' Day being the
    obvious pair -- would otherwise be shifted apart by the observance rule and
    silently invent a day off. Holidays that merely coincide in one year are
    left alone: that is a real (if unlucky) calendar, not a configuration
    mistake.
    """
    if len(rules) < 2:
        return
    dates: Dict[str, Set[dt.date]] = {}
    for year in _COLLISION_YEARS:
        for holiday in resolve_year(rules, year, observance=Observance.NONE):
            dates.setdefault(holiday.name, set()).add(holiday.actual)

    # Two one-off dated entries are an explicit statement of two names for one
    # day (a real calendar), not a rule that might have been defined twice.
    explicit = {r.name for r in rules if isinstance(r, ExplicitDate)}
    names = sorted(dates)
    for i, first in enumerate(names):
        for second in names[i + 1:]:
            if first in explicit and second in explicit:
                continue
            if dates[first] and dates[first] == dates[second]:
                raise ValueError(
                    f"{first!r} and {second!r} are always the same day; keep one "
                    "of them (they are two names for one holiday, so observing "
                    "both would invent a day off)"
                )


def _key(name: str) -> str:
    return " ".join(str(name).split()).casefold()

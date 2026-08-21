"""Company configuration: presets, config files, and CLI overrides.

No two employers observe the same set of days, so nothing here is hard-coded
into the calculation. A :class:`CompanyConfig` is assembled by layering, in
increasing order of precedence: a named preset, an optional config file, then
explicit command-line overrides.
"""

from __future__ import annotations

import datetime as dt
import json
import os
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Sequence, Set

from .catalog import (
    DEFAULT_WORKWEEK,
    KNOWN,
    Observance,
    resolve_year,
    PRESETS,
    Rule,
    known_holiday,
    parse_rule,
    parse_workweek,
    rule_from_mapping,
)

__all__ = [
    "CompanyConfig",
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
    blackout: Set[dt.date] = field(default_factory=set)
    committed: Set[dt.date] = field(default_factory=set)

    def holiday_names(self) -> List[str]:
        return [rule.name for rule in self.holidays]


def parse_date(value: str) -> dt.date:
    text = str(value).strip()
    try:
        return dt.date.fromisoformat(text)
    except ValueError:
        raise ValueError(f"expected a YYYY-MM-DD date, got {value!r}") from None


def parse_date_ranges(values: Iterable[str]) -> Set[dt.date]:
    """Expand ``YYYY-MM-DD`` and ``YYYY-MM-DD:YYYY-MM-DD`` specs into a date set."""
    dates: Set[dt.date] = set()
    for value in values:
        for chunk in str(value).split(","):
            chunk = chunk.strip()
            if not chunk:
                continue
            if ":" in chunk:
                start_text, _, end_text = chunk.partition(":")
                start, end = parse_date(start_text), parse_date(end_text)
                if end < start:
                    raise ValueError(f"date range {chunk!r} ends before it starts")
                current = start
                while current <= end:
                    dates.add(current)
                    current += dt.timedelta(days=1)
            else:
                dates.add(parse_date(chunk))
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


def load_config_file(path: str) -> Dict[str, object]:
    """Load a JSON or YAML company config.

    YAML needs PyYAML; JSON works with the stdlib alone.
    """
    if not os.path.exists(path):
        raise FileNotFoundError(f"config file not found: {path}")
    with open(path, "r", encoding="utf-8") as handle:
        text = handle.read()

    suffix = os.path.splitext(path)[1].lower()
    if suffix in {".yaml", ".yml"}:
        try:
            import yaml  # type: ignore[import-untyped]
        except ImportError:
            raise ValueError(
                f"{path} is YAML but PyYAML is not installed. "
                "Install it with 'pip install pyyaml', or use a .json config instead."
            ) from None
        data = yaml.safe_load(text)
    else:
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{path} is not valid JSON: {exc}") from None

    if data is None:
        data = {}
    if not isinstance(data, dict):
        raise ValueError(f"{path} must contain a mapping at the top level")
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
    blackout: Sequence[str] = (),
    committed: Sequence[str] = (),
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
    if max_consecutive_pto is not None:
        config.max_consecutive_pto = max_consecutive_pto
    if blackout:
        config.blackout |= parse_date_ranges(blackout)
    if committed:
        config.committed |= parse_date_ranges(committed)

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
    if config.max_consecutive_pto is not None and config.max_consecutive_pto < 1:
        raise ValueError(
            f"max_consecutive_pto must be at least 1 (got {config.max_consecutive_pto})"
        )


def _apply_file(config: CompanyConfig, data: Dict[str, object], path: str) -> None:
    config.name = str(data.get("name", config.name))

    if "preset" in data:
        base = str(data["preset"])
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
        for entry in entries:
            if isinstance(entry, str):
                rules.append(rule_from_spec(entry))
            elif isinstance(entry, dict):
                rules.append(rule_from_mapping(entry))
            else:
                raise ValueError(f"{path}: holiday entries must be strings or mappings, got {entry!r}")
        # 'holidays' replaces the preset; use 'add'/'drop' to amend one instead.
        config.holidays = rules

    for spec in _as_list(data.get("add")):
        config.holidays.append(
            rule_from_spec(str(spec)) if isinstance(spec, str) else rule_from_mapping(spec)
        )
    dropped = {_key(str(name)) for name in _as_list(data.get("drop"))}
    if dropped:
        config.holidays = [r for r in config.holidays if _key(r.name) not in dropped]

    if "workweek" in data:
        value = data["workweek"]
        config.workweek = (
            parse_workweek(",".join(str(v) for v in value))
            if isinstance(value, list)
            else parse_workweek(str(value))
        )
    if "observance" in data:
        config.observance = Observance.parse(str(data["observance"]))
    if "pto_days" in data:
        config.pto_days = int(data["pto_days"])  # type: ignore[arg-type]
    if "pto_by_year" in data:
        raw = data["pto_by_year"]
        if not isinstance(raw, dict):
            raise ValueError(f"{path}: 'pto_by_year' must be a mapping of year to days")
        config.pto_by_year.update({int(k): int(v) for k, v in raw.items()})
    if "floating_days" in data:
        config.floating_days = int(data["floating_days"])  # type: ignore[arg-type]
    if "floating_by_year" in data:
        raw = data["floating_by_year"]
        if not isinstance(raw, dict):
            raise ValueError(f"{path}: 'floating_by_year' must be a mapping of year to days")
        config.floating_by_year.update({int(k): int(v) for k, v in raw.items()})
    if "carryover" in data:
        config.carryover = int(data["carryover"])  # type: ignore[arg-type]
    if "min_break" in data:
        config.min_break = int(data["min_break"])  # type: ignore[arg-type]
    if "max_consecutive_pto" in data:
        config.max_consecutive_pto = int(data["max_consecutive_pto"])  # type: ignore[arg-type]
    if "blackout" in data:
        config.blackout |= parse_date_ranges(str(v) for v in _as_list(data["blackout"]))
    if "committed" in data:
        config.committed |= parse_date_ranges(str(v) for v in _as_list(data["committed"]))


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

    names = sorted(dates)
    for i, first in enumerate(names):
        for second in names[i + 1:]:
            if dates[first] and dates[first] == dates[second]:
                raise ValueError(
                    f"{first!r} and {second!r} are always the same day; keep one "
                    "of them (they are two names for one holiday, so observing "
                    "both would invent a day off)"
                )


def _key(name: str) -> str:
    return " ".join(str(name).split()).casefold()

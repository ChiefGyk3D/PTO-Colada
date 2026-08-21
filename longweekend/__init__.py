"""Long Weekend Planner: find long weekends and plan PTO around them."""

from .catalog import PRESETS, Observance, ResolvedHoliday, resolve_year
from .conferences import Conference, ConferenceOccurrence, load_conferences
from .hebrew import JewishHoliday, holidays as jewish_holidays
from .observances import OBSERVANCE_SETS, PersonalDay, resolve_observances
from .config import CompanyConfig, build_config
from .planner import PtoBudget, PtoPlan, optimize_pto
from .schedule import Break, Day, DayKind, build_days, find_breaks, find_opportunities

__version__ = "1.1.0"

__all__ = [
    "OBSERVANCE_SETS",
    "PRESETS",
    "Conference",
    "ConferenceOccurrence",
    "JewishHoliday",
    "PersonalDay",
    "Break",
    "CompanyConfig",
    "Day",
    "DayKind",
    "Observance",
    "PtoBudget",
    "PtoPlan",
    "ResolvedHoliday",
    "__version__",
    "build_config",
    "jewish_holidays",
    "load_conferences",
    "resolve_observances",
    "build_days",
    "find_breaks",
    "find_opportunities",
    "optimize_pto",
    "resolve_year",
]

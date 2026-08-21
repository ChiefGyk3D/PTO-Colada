"""Long Weekend Planner: find long weekends and plan PTO around them."""

from .catalog import PRESETS, Observance, ResolvedHoliday, resolve_year
from .config import CompanyConfig, build_config
from .planner import PtoBudget, PtoPlan, optimize_pto
from .schedule import Break, Day, DayKind, build_days, find_breaks, find_opportunities

__version__ = "1.0.0"

__all__ = [
    "PRESETS",
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
    "build_days",
    "find_breaks",
    "find_opportunities",
    "optimize_pto",
    "resolve_year",
]

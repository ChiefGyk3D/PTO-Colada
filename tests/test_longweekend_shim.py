"""The old ``longweekend`` package name keeps working as a shim over ``pto_colada``."""

import importlib
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SUBMODULES = sorted(
    p.stem for p in (ROOT / "pto_colada").glob("*.py") if p.stem != "__init__"
)


def test_submodules_were_collected():
    # Guard: the parametrization below must not silently be empty.
    assert {"cli", "report", "planner", "config"} <= set(SUBMODULES)


def test_top_level_reexports_public_api():
    import longweekend
    import pto_colada

    assert longweekend.__version__ == pto_colada.__version__
    for name in pto_colada.__all__:
        assert getattr(longweekend, name) is getattr(pto_colada, name), name
    from longweekend import PRESETS, PtoBudget, build_days, optimize_pto, resolve_year  # noqa: F401


@pytest.mark.parametrize("sub", SUBMODULES)
def test_submodule_is_the_same_object(sub):
    old = importlib.import_module("longweekend." + sub)
    new = importlib.import_module("pto_colada." + sub)
    assert old is new


def test_private_names_and_from_imports_still_work():
    from longweekend.report import _event, render_ics
    from pto_colada import report

    assert _event is report._event and render_ics is report.render_ics


def test_negative_unknown_submodule_still_fails():
    with pytest.raises(ImportError):
        importlib.import_module("longweekend.does_not_exist")


def _run(*args):
    return subprocess.run(
        [sys.executable, *args], cwd=ROOT, env={"PYTHONPATH": str(ROOT), "PATH": ""},
        capture_output=True, text=True, timeout=120,
    )


def test_python_dash_m_old_cli_matches_new():
    argv = ["--start-year", "2027", "--years", "1", "--pto", "15"]
    old = _run("-m", "longweekend.cli", *argv)
    new = _run("-m", "pto_colada.cli", *argv)
    assert old.returncode == 0 and new.returncode == 0, old.stderr
    assert old.stdout == new.stdout and old.stdout.strip()


def test_aliasing_does_not_rewrite_the_real_modules_spec():
    old = importlib.import_module("longweekend.conferences")
    assert old.__spec__.name == "pto_colada.conferences"
    assert old.__name__ == "pto_colada.conferences"

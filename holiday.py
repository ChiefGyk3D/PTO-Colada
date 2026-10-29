#!/usr/bin/env python3
"""Backwards-compatible entry point.

The original single-file script lives on as a thin wrapper so that existing
invocations keep working:

    python3 holiday.py
    python3 holiday.py --nonstandard

Everything the old script did is still available, and the full feature set --
company presets, custom holiday sets, PTO planning, JSON and calendar export --
is reachable through the same command line. Run ``python3 holiday.py --help``
or install the package and use the ``pto-colada`` command (``long-weekend`` still works).
"""

from pto_colada.cli import main

if __name__ == "__main__":
    raise SystemExit(main())

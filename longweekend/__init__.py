"""Compatibility shim: ``longweekend`` is the old name of :mod:`pto_colada`.

PTO Colada was called Long Weekend Planner and its package was ``longweekend``.
Old imports keep working: ``import longweekend``, ``from longweekend import
PRESETS``, ``from longweekend.report import render_ics`` and
``python -m longweekend.cli`` all resolve to the very same module objects as
their ``pto_colada`` counterparts (no second copy is imported, so classes and
monkeypatches are shared). New code should import ``pto_colada``.
"""

import importlib
import importlib.abc
import importlib.util
import sys

import pto_colada
from pto_colada import *  # noqa: F401,F403  (re-export the public API)
from pto_colada import __all__, __version__  # noqa: F401

_OLD = __name__ + "."
_NEW = "pto_colada."

# Names starting with an underscore are not covered by ``import *``; callers of
# the old package reached into a few of them, so mirror every attribute.
for _name in dir(pto_colada):
    if not _name.startswith("__"):
        globals().setdefault(_name, getattr(pto_colada, _name))
del _name

# Lets ``python -m longweekend.cli`` run: runpy needs source to execute.
_RUN_MAIN = "from pto_colada.cli import main\nraise SystemExit(main())\n"


class _AliasLoader(importlib.abc.Loader):
    def __init__(self, target):
        self._target = target

    def create_module(self, spec):
        module = importlib.import_module(self._target)
        # The import system overwrites ``__spec__`` on whatever create_module
        # returns; remember the real one so exec_module can put it back.
        self._spec = module.__spec__
        return module

    def exec_module(self, module):
        """The aliased module is already executed; just undo the spec overwrite."""
        module.__spec__ = self._spec

    def get_code(self, fullname):
        return compile(_RUN_MAIN, "<longweekend shim>", "exec")

    def is_package(self, fullname):
        return False


class _AliasFinder(importlib.abc.MetaPathFinder):
    """Map ``longweekend.<sub>`` onto ``pto_colada.<sub>``, same module object."""

    def find_spec(self, fullname, path=None, target=None):
        if not fullname.startswith(_OLD):
            return None
        new = _NEW + fullname[len(_OLD):]
        if importlib.util.find_spec(new) is None:
            return None
        return importlib.util.spec_from_loader(fullname, _AliasLoader(new))


if not any(isinstance(f, _AliasFinder) for f in sys.meta_path):
    sys.meta_path.insert(0, _AliasFinder())

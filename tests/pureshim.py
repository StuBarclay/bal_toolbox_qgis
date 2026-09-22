"""Load the osgeo-free shim modules in isolation for testing.

The ``_rio`` package's ``__init__`` imports GDAL-backed submodules
(``crs``, ``warp``, ``features`` ...), so importing the package requires
``osgeo``. The purely numeric modules -- ``affine``, ``windows``,
``transform`` and ``errors`` -- have no GDAL dependency and hold the
highest-risk hand-written maths. This loader imports just those four as a
synthetic package (so their relative imports resolve) without triggering
the GDAL imports, letting them be tested anywhere ``numpy`` is available.
"""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

_RIO_DIR = (
    Path(__file__).resolve().parent.parent / "bal_toolbox_qgis" / "balcore" / "_rio"
)

_PKG = "riopure"


def _load() -> types.ModuleType:
    if _PKG in sys.modules:
        return sys.modules[_PKG]
    package = types.ModuleType(_PKG)
    package.__path__ = [str(_RIO_DIR)]
    sys.modules[_PKG] = package
    for name in ("errors", "affine", "transform", "windows"):
        spec = importlib.util.spec_from_file_location(
            f"{_PKG}.{name}", _RIO_DIR / f"{name}.py"
        )
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules[f"{_PKG}.{name}"] = module
        spec.loader.exec_module(module)
        setattr(package, name, module)
    return package


_pkg = _load()
affine = _pkg.affine
transform = _pkg.transform
windows = _pkg.windows
errors = _pkg.errors

Affine = affine.Affine
Window = windows.Window

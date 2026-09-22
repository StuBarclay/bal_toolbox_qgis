"""Module entry point so ``python -m bal_toolbox_qgis.balcore`` invokes the CLI."""

from __future__ import annotations

import sys

from bal_toolbox_qgis.balcore.cli import main

if __name__ == "__main__":
    sys.exit(main())

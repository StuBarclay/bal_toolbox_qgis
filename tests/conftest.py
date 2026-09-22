"""Pytest configuration: make the plugin package importable."""

from __future__ import annotations

import sys
from pathlib import Path

# The plugin package ``bal_toolbox_qgis`` lives directly under the project
# root (one level above this ``tests`` directory).
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

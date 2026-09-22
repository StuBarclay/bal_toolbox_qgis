"""Shared help metadata for the BAL Toolbox processing algorithms.

Kept deliberately free of any ``qgis`` import so the constant can be reused
(and unit-tested) outside a QGIS runtime. Every algorithm's ``helpUrl()``
returns :data:`HELP_URL`, which drives the "Help" button in the Processing
algorithm dialog.
"""

from __future__ import annotations

#: Target of every algorithm's ``helpUrl()`` -- the project README, which
#: documents each algorithm. Kept in one place so it tracks the repository URL
#: declared in ``metadata.txt``.
HELP_URL = "https://github.com/StuBarclay/bal_toolbox_qgis#readme"

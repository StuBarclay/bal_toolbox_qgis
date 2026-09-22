"""Graphical dialog wrapper for the BAL toolbox.

The dialog is a thin front-end: it gathers a handful of friendly inputs
and delegates to the registered Processing algorithm
(``baltoolbox:balmethod1``), so there is exactly one implementation of
the calculation. Import lives behind :mod:`bal_toolbox_qgis.plugin` and
is only pulled in when the user opens the dialog, keeping the
Processing-only load path free of Qt-widget imports.
"""

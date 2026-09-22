# Changelog

All notable changes to the BAL Toolbox (AS 3959:2018) QGIS plugin are recorded
here. The format follows [Keep a Changelog](https://keepachangelog.com/), and
the project uses [Semantic Versioning](https://semver.org/).

## [0.1.6] - 2026-09-22

### Added
- Live progress reporting and cooperative cancellation during the
  eight-direction BAL search: the progress bar advances once per compass
  direction and the run stops cleanly (writing nothing) when cancelled. The
  vendored compute core is left untouched — the per-direction function is
  wrapped only for the duration of a run.
- The friendly dialog now remembers your last-used choices (national-DEM
  toggle, FDI source and value, region, method, directional-rasters toggle,
  output CRS and output folder) between sessions via `QgsSettings`.
- Save and recall named custom-remap presets: tick "Save these rules as a
  preset" on the **Reclassify vegetation** helper to store the resolved
  rules, then choose the "Saved preset" source on either that helper or the
  main BAL tool to reuse them. Presets live in your QGIS user profile.
- QGIS-side tests (`tests/test_qgis_glue.py`, skipped without a QGIS runtime)
  and pure-Python tests for the progress wrapper and preset store.
- A raster listing icon (`icon.png`, 256×256, rendered from `icon.svg`) so the
  plugin shows an icon in the QGIS plugin repository catalogue; the in-app
  toolbar button and Processing provider still use the vector `icon.svg`.
- Every algorithm now implements `helpUrl()`, so the Processing dialog's "Help"
  button opens the project README. The URL lives in one place
  (`algorithms/_help.py`) and is cross-checked against `metadata.txt` by a test.
- A GitHub Actions CI workflow (`.github/workflows/ci.yml`) that runs the four
  gates (Ruff lint, Ruff format check, mypy strict, pytest) on Python 3.10 and
  3.12. QGIS-only tests skip on the runner exactly as they do locally.

### Changed
- The main BAL algorithm now declares its threading behaviour explicitly via
  `flags()`; threading stays enabled so the progress bar and cancel button
  remain live.

## [0.1.5] - 2026-09-22

### Added
- The maximum-BAL output raster now loads pre-styled with a categorised
  AS 3959 palette (BAL-LOW / 12.5 / 19 / 29 / 40 / FZ), applied on both the
  Processing (Toolbox) and friendly-dialog load paths. Values outside the
  assessed area (NODATA) render transparent.
- `CHANGELOG.md` and a `changelog=` field in `metadata.txt` (shown in the
  QGIS Plugin Manager).

### Changed
- Replaced the placeholder `example.com` repository, tracker, homepage and
  contact email in `metadata.txt` with real project URLs.

### Removed
- Deleted the unused, empty `processing_provider/` scaffold directory.

## [0.1.4] - 2026-09-22

### Added
- Custom vegetation reclassification on the main BAL tool: an editable
  Low/High/Class table or a CSV of `low,high,class` rows (the table wins when
  both are given), plus a passthrough option for rasters already carrying
  AS 3959 classes 1-8.
- New standalone **Reclassify vegetation (custom remap)** algorithm.

## [0.1.3] - 2026-09-22

### Added
- The friendly dialog can now drive the national SRTM 1-second DEM (over a
  drawn/canvas extent) and the from-location Fire Danger Index (region or
  auto-detect), matching more of the underlying Processing algorithm.

## [0.1.2] - 2026-09-22

### Changed
- The maximum-BAL raster is now an ordinary, temp-capable raster destination
  (defaults to a temporary layer; written to disk only when a path is named).
  The output folder is optional and holds only the directional / QA extras.

## [0.1.1] - 2026-09-22

### Fixed
- QGIS 4 / PyQt6 compatibility: fully scope Qt-native enums
  (e.g. `QDialogButtonBox.ButtonRole.AcceptRole`) and set
  `qgisMaximumVersion=4.99` so the plugin is recognised on QGIS 4.

## [0.1.0] - 2026-09-21

### Added
- Initial release: Method 1 (and experimental Method 2) BAL raster, BAL-to-
  building/parcel assignment, fire-history / fuel-recovery analysis, a Fire
  Danger Index helper, and a "from YAML config" runner. Pure-numpy compute
  core with GDAL/OGR-native I/O — no third-party runtime dependencies.

# Changelog

All notable changes to the BAL Toolbox (AS 3959:2018) QGIS plugin are recorded
here. The format follows [Keep a Changelog](https://keepachangelog.com/), and
the project uses [Semantic Versioning](https://semver.org/).

## [0.1.10] - 2026-09-22

### Fixed
- A vegetation or DEM raster held inside an ESRI File Geodatabase (`.gdb`)
  still failed to read after 0.1.9. That release wrapped the GDAL descriptor
  in a VRT built with `gdal.Translate(..., "VRT")`, but for a geodatabase
  source that produced a `.vrt` file GDAL then refused to reopen
  (`RuntimeError: '...source.vrt' not recognized as being in a supported file
  format`) — so the run advanced past the "Raster not found" guard only to
  fail at the read itself. The VRT is now assembled by hand from the source's
  size, projection, geotransform and per-band datatype/nodata (a new,
  unit-tested `build_vrt_xml` in `algorithms/_raster_source.py`) and written
  with a plain file write, which guarantees a well-formed document that starts
  with `<VRTDataset` and is fully flushed before use. Each band references the
  descriptor through `<SourceFilename relativeToVRT="0">`, which GDAL passes
  verbatim to `GDALOpen`, so the geodatabase raster (or any GDAL-openable
  subdataset) is read lazily — only the requested window, never the whole
  national raster. As a final guard the written VRT is reopened with GDAL and,
  if that fails, the layer is materialised instead of handing the core a file
  it cannot read. The vendored compute core is unchanged.

## [0.1.9] - 2026-09-22

### Fixed
- A vegetation or DEM raster held inside an ESRI File Geodatabase (`.gdb`)
  failed with `Raster not found: OpenFileGDB:"...gdb":LAYER`, even though QGIS
  could load it. Release 0.1.7 let the QGIS glue hand a GDAL *dataset
  descriptor* (a File Geodatabase raster, a NetCDF/HDF subdataset, a
  `/vsicurl/` path or a WCS descriptor) straight to the compute core, but the
  core opens rasters by path and first checks the path *exists* — which a bare
  descriptor never does. Such a descriptor is now wrapped in a tiny virtual
  raster (`.vrt`) file: a real file the core can find and open that merely
  references the source and reads it lazily, so only the pixel window the run
  needs is read and a national raster is never copied in full. The vendored
  compute core is unchanged; the decision lives in a new qgis-free module
  (`algorithms/_raster_source.py`) covered by CI tests, and the QGIS-side test
  now asserts the resolved path actually exists on disk.

## [0.1.8] - 2026-09-22

### Fixed
- The "Use national SRTM DEM" option (and the underlying national-DEM code
  path) failed with `RuntimeError: Malformed Result: ...ArcGIS Server
  Error...http.400`. The Geoscience Australia service is a healthy OGC WCS,
  but its ArcGIS server rejects the `GetCoverage` request GDAL's own WCS
  driver constructs. The DEM window is now fetched with a direct WCS 1.0.0
  `GetCoverage` KVP request (built in `balcore/_wcs_request.py`), downloaded
  to a temporary GeoTIFF with `urllib`, validated, then read back through the
  GDAL shim. The request window is snapped to whole SRTM cells and clamped to
  the coverage's WGS84 extent; a disjoint AOI still raises a clear "does not
  intersect the national DEM coverage" error, and an XML/HTML service
  exception is surfaced as a readable message instead of a raw GDAL failure.
  The returned `(data, RasterGrid)` contract and all downstream reprojection
  maths are unchanged. New network-free unit tests
  (`tests/test_wcs_request.py`) cover the request geometry and validation.

## [0.1.7] - 2026-09-22

### Changed
- DEM and vegetation raster inputs now accept a much wider range of sources,
  not just plain files on disk. A raster held inside an ESRI File Geodatabase
  (`.gdb`), a GDAL subdataset (e.g. NetCDF/HDF), a `/vsicurl/` path or a WCS
  XML descriptor is passed straight through to the compute core once it is
  confirmed GDAL-openable. Anything else QGIS can render but GDAL cannot open
  directly (an in-memory raster, some WMS/WCS layers) is transparently
  exported to a temporary GeoTIFF via QGIS's own raster writer. This removes
  the previous "no local file source that GDAL can read; export it to a
  GeoTIFF first" failure for geodatabase rasters. (Vector inputs already
  worked with any QGIS-readable source, including geodatabase feature
  classes.)

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
- Marked as a stable (non-experimental) release: `experimental=False` in
  `metadata.txt`, so the QGIS Plugin Manager lists it without the experimental
  filter.

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

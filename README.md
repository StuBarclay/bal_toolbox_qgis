# BAL Toolbox (AS 3959:2018) — QGIS plugin

A QGIS plugin that computes the **Bushfire Attack Level (BAL)** over an area
following the prescriptive **Method 1** of AS 3959:2018 *Construction of
buildings in bushfire-prone areas*, plus an experimental radiant-heat-flux
Method 2, assignment of BAL ratings to building/parcel polygons, and a
fire-history / fuel-recovery analysis.

It is a packaging of the standalone `bal_toolbox` project as a native QGIS
plugin. The numerical core is reused **unchanged**; only the file I/O layer is
rewritten against GDAL/OGR/OSR, which ship inside QGIS. **No extra Python
packages are installed** — the plugin has no `rasterio`, `fiona` or `scipy`
runtime dependency.

> This tool is a decision aid, not a substitute for a site assessment by a
> qualified bushfire practitioner. Table 2.1 FDI values are jurisdictional
> design values and Method 2 is experimental and not yet verified against the
> published tables. Confirm any FDI with the relevant authority.

## What you get

The plugin adds a **Processing provider** ("BAL Toolbox (AS 3959:2018)") with
six algorithms, and a **Plugins → BAL Toolbox** menu item / toolbar button
that opens a friendly dialog for the common case. The dialog remembers your
last-used choices between sessions.

Processing algorithms:

- **BAL raster (Method 1 / 2)** — the main tool. From a DEM and a vegetation
  raster it derives slope, aspect and the directional separation-distance
  search. The maximum-BAL raster is the primary output and behaves like any
  other Processing raster output — it defaults to a temporary layer and is only
  written to disk permanently when you name a path. An optional output folder
  collects the eight directional rasters and the aligned QA input rasters when
  you tick those options. FDI is an explicit value or looked up from an
  AS 3959 Table 2.1 region. Optionally fetches the national SRTM 1-second DEM
  over an extent. The vegetation raster can be reclassified with the built-in
  NVIS preset, your own Low/High/Class rules (an editable table or a CSV), a
  saved custom-remap preset (recalled by name), or passed through unchanged
  when it already carries AS 3959 classes 1-8. The eight-direction search
  reports progress and can be cancelled mid-run. The loaded max-BAL layer is
  styled automatically with the AS 3959 colour palette (BAL-LOW / 12.5 / 19 /
  29 / 40 / FZ).
- **BAL raster (from YAML config)** — runs a full calculation from a single
  reproducible YAML file, exposing everything the interactive tool omits:
  polygon areas of interest and weather-derived FDI (custom vegetation remaps
  are now available on the interactive tool too).
- **Assign BAL to buildings / parcels** — samples a BAL raster onto polygon
  footprints, adding `bal_max` and `bal_dominant` to each feature.
- **Fire history & fuel recovery** — annotates footprints with `year_last_fire`,
  `times_burnt`, `years_since_fire`, and (with a vegetation-class raster) a
  coarse `fuel_recovery_pct`, alongside — never replacing — the BAL rating.
- **Fire Danger Index helper** — determines the Method 1 design FDI
  (40/50/80/100) from a region key, a longitude/latitude, or weather
  observations (McArthur Mark 5 FFDI).
- **Reclassify vegetation (custom remap)** — turns a raw vegetation raster
  into the eight AS 3959 vegetation classes on its own, so you can inspect or
  reuse the classified layer. Rules come from the NVIS preset, your own
  Low/High/Class table or CSV, a saved preset, or a passthrough for rasters
  already in classes 1-8; unmatched values become NODATA. Tick "Save these
  rules as a preset" with a name to store the resolved rules for reuse here or
  on the main BAL tool.

## Installing

The plugin is a single folder, `bal_toolbox_qgis/`, packaged as a ZIP.

1. In QGIS: **Plugins → Manage and Install Plugins → Install from ZIP**, and
   choose `bal_toolbox_qgis.zip`.
2. Or copy the `bal_toolbox_qgis/` folder into your QGIS plugin directory
   (e.g. on Windows `…/QGIS3/profiles/default/python/plugins/`), then enable
   **BAL Toolbox (AS 3959:2018)** in the plugin manager.

Requires QGIS 3.34 LTR or newer (it bundles Python 3.10+, which the compute
core needs). Nothing else to install.

## Inputs and conventions

- **DEM** — must be in a projected metre CRS (slope, aspect and distance are
  computed in metres). Use the national SRTM source to have this handled for
  you (it reprojects to the appropriate MGA zone automatically).
- **Vegetation raster** — reclassified into the eight AS 3959 vegetation
  classes. The default is the NVIS Major Vegetation Group preset (`nvis_mvg`),
  but you can supply your own Low/High/Class rules (an editable table or a CSV
  of `low,high,class` rows, where a value `v` is assigned `class` when
  `low <= v <= high`), or select passthrough when the raster is already in
  classes 1-8. Values matching no rule become NODATA.
- **Output CRS** — results are reprojected to this on the way out (default
  GDA94 lat/lon, EPSG:4283); the calculation itself always runs in projected
  metres.
- **Vector I/O** — footprint/fire layers are read via OGR and exchanged with
  the core as GeoJSON; polygon CRSs are preserved. Layers should carry an EPSG
  code.

## Architecture

```
bal_toolbox_qgis/
  __init__.py         classFactory — QGIS plugin entry point
  metadata.txt        plugin metadata (hasProcessingProvider=yes)
  plugin.py           registers the provider + menu/toolbar action
  provider.py         QgsProcessingProvider
  algorithms/         one thin QgsProcessingAlgorithm per feature
    _qgis_io.py         QGIS-layer <-> core-file bridging helpers
  gui/dialog.py       friendly dialog (delegates to the algorithm)
  balcore/            the vendored, unchanged compute core
    _rio/               GDAL/OGR-backed shim mirroring the rasterio API
```

Every algorithm is a small adapter: it collects native QGIS parameters,
materialises layers into the plain files the core expects (GeoTIFF rasters,
GeoJSON feature collections), calls into `balcore`, and loads the results back
as QGIS layers. All BAL numerics live in the untouched core — nothing in the
plugin layer re-implements the standard.

The `balcore/_rio` package is a compatibility shim that presents the exact
`rasterio`/`fiona` API the core was written against, but implemented on
`osgeo.gdal`/`ogr`/`osr`. This is what removes the third-party I/O
dependencies while keeping the ported modules byte-for-byte identical to the
upstream project apart from their import lines.

## Development

```bash
pip install -e ".[dev]"      # dev tools only; the plugin itself needs none
pytest                       # pure-core + shim-equivalence tests
ruff check bal_toolbox_qgis tests
mypy --cache-dir=/tmp
```

The test suite runs the compute core and checks the `_rio` GDAL shim against
real `rasterio` where it is installed; tests that need `osgeo` are skipped
automatically when GDAL is not present (they run inside QGIS).

## Licence

Apache-2.0. See [LICENSE](LICENSE).

"""End-to-end BAL Method 1 workflow orchestration.

Ties together raster I/O, terrain derivation, vegetation
reclassification and the BAL engine into a single callable that
consumes a :class:`bal_toolbox_qgis.balcore.config.RunConfig` and writes output
rasters. This is the open-source, ``arcpy``-free equivalent of the
original ``bal.bal_calc`` driver.

When the configuration defines an area of interest (AOI), the DEM is
cropped to it (the DEM still defines the output grid) and only the
covering window of the vegetation raster is read, so a large continental
input is never loaded in full. A polygon AOI additionally masks output
cells outside its boundary to nodata.
"""

from __future__ import annotations

import logging
from pathlib import Path

import numpy as np
from numpy.typing import NDArray
from bal_toolbox_qgis.balcore._rio.crs import CRS
from bal_toolbox_qgis.balcore._rio.enums import Resampling
from bal_toolbox_qgis.balcore._rio.transform import array_bounds

from bal_toolbox_qgis.balcore import dem_source, engine, tables, terrain, weather
from bal_toolbox_qgis.balcore import fdi as fdi_module
from bal_toolbox_qgis.balcore.config import AOI, FdiSpec, RunConfig
from bal_toolbox_qgis.balcore.raster import (
    RasterGrid,
    crop_to_bounds,
    mask_to_polygon,
    matches_grid,
    read_polygon_bounds,
    read_raster,
    read_raster_window,
    reproject_raster,
    reproject_to_grid,
    write_raster,
)

logger = logging.getLogger(__name__)


def output_timestamp() -> str:
    """Return a filename-safe UTC timestamp, ``YYYYMMDDTHHMMSSZ``.

    Returns:
        The current UTC time formatted for use as an output filename
        suffix (e.g. ``"20260616T024500Z"``).
    """
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _to_output_crs(
    data: NDArray[np.floating] | NDArray[np.integer],
    grid: RasterGrid,
    output_crs: str,
    resampling: Resampling,
) -> tuple[NDArray[np.floating], RasterGrid]:
    """Reproject a raster to the configured output CRS for writing.

    A thin wrapper over :func:`bal_toolbox_qgis.balcore.raster.reproject_raster` that
    returns the data unchanged (as a float array) when the grid is already
    in ``output_crs`` or has no CRS, so the common case is cheap.

    Args:
        data: Raster data on ``grid``.
        grid: The compute grid (projected metres).
        output_crs: Target CRS string (e.g. ``"EPSG:4283"``).
        resampling: Nearest for categorical rasters, bilinear for the DEM.

    Returns:
        ``(data, grid)`` in the output CRS.
    """
    if grid.crs is None or CRS.from_user_input(output_crs) == grid.crs:
        return np.asarray(data, dtype=np.float64), grid
    return reproject_raster(data, grid, output_crs, resampling)


def _align_to_dem(
    veg: NDArray[np.float64],
    veg_grid: RasterGrid,
    dem_grid: RasterGrid,
) -> NDArray[np.float64]:
    """Align the vegetation raster to the DEM grid, reprojecting if needed.

    Method 1 evaluates vegetation and terrain cell-by-cell, so the two
    rasters must share a grid. If they already match, the vegetation
    array is returned unchanged; otherwise it is warped onto the DEM
    grid using nearest-neighbour resampling (correct for categorical
    vegetation classes).

    Args:
        veg: Vegetation array.
        veg_grid: Vegetation grid.
        dem_grid: DEM grid to align to.

    Returns:
        The vegetation array on the DEM grid.

    Raises:
        ValueError: If the rasters do not share a coordinate reference
            system and so cannot be warped, or do not overlap at all.
    """
    if matches_grid(veg_grid, dem_grid):
        return veg

    logger.info(
        "Vegetation grid (%s, %.3g m, %s) differs from DEM grid "
        "(%s, %.3g m, %s); reprojecting vegetation onto the DEM grid.",
        veg_grid.shape,
        veg_grid.pixel_width,
        veg_grid.crs,
        dem_grid.shape,
        dem_grid.pixel_width,
        dem_grid.crs,
    )
    aligned = reproject_to_grid(veg, veg_grid, dem_grid)
    if not np.any(aligned != tables.NODATA):
        raise ValueError(
            "Vegetation raster does not overlap the DEM after "
            "reprojection; check that the two inputs cover the same area."
        )
    return aligned


def _resolve_aoi_bounds(
    aoi: AOI,
) -> tuple[tuple[float, float, float, float], str | None]:
    """Resolve an AOI to bounds and the CRS those bounds are stated in.

    Args:
        aoi: The configured area of interest.

    Returns:
        A tuple of the AOI bounds ``(xmin, ymin, xmax, ymax)`` and the
        CRS string they are expressed in, or ``None`` to interpret them
        in the DEM's CRS.
    """
    if aoi.bbox is not None:
        return aoi.bbox, aoi.crs
    assert aoi.polygon_path is not None  # config guarantees one is set.
    poly_bounds, poly_crs = read_polygon_bounds(aoi.polygon_path)
    # An explicit aoi.crs overrides the polygon file's declared CRS.
    return poly_bounds, aoi.crs if aoi.crs is not None else poly_crs


def _read_inputs(
    config: RunConfig,
) -> tuple[NDArray[np.float64], RasterGrid, NDArray[np.float64], RasterGrid]:
    """Read the DEM and vegetation, applying the AOI window if configured.

    Without an AOI both rasters are read in full (the original
    behaviour). With an AOI the DEM is cropped to the AOI and the
    vegetation is read only over the cropped DEM's extent, avoiding a
    full load of a large input raster.

    Args:
        config: Validated run configuration.

    Returns:
        ``(elevation, dem_grid, veg_raw, veg_grid)``.
    """
    if config.dem_is_national:
        # config validation guarantees an AOI for the national DEM source.
        assert config.aoi is not None
        elevation, dem_grid = _national_dem_grid(config.aoi)
    else:
        logger.info("Reading DEM %s", config.dem_path)
        elevation, dem_grid = read_raster(config.dem_path)

        if config.aoi is None:
            logger.info("Reading vegetation %s", config.vegetation_path)
            veg_raw, veg_grid = read_raster(config.vegetation_path)
            return elevation, dem_grid, veg_raw, veg_grid

        bounds, bounds_crs = _resolve_aoi_bounds(config.aoi)
        logger.info(
            "Cropping DEM to AOI bounds %s (crs=%s)", bounds, bounds_crs or "DEM"
        )
        elevation, dem_grid = crop_to_bounds(elevation, dem_grid, bounds, bounds_crs)

    dem_bounds = array_bounds(dem_grid.shape[0], dem_grid.shape[1], dem_grid.transform)
    logger.info("Reading vegetation window over AOI from %s", config.vegetation_path)
    veg_raw, veg_grid = read_raster_window(
        config.vegetation_path, dem_bounds, dem_grid.crs
    )
    return elevation, dem_grid, veg_raw, veg_grid


def _national_dem_grid(
    aoi: AOI,
) -> tuple[NDArray[np.float64], RasterGrid]:
    """Fetch and project the national SRTM DEM window for an AOI.

    The DEM window is fetched from the GA WCS (geographic degrees), the
    appropriate GDA2020 MGA zone is chosen from the AOI centre, and the
    window is reprojected into that projected CRS at ~30 m. The result
    defines the output grid for the run.

    Args:
        aoi: The configured area of interest (bounds resolved here).

    Returns:
        ``(elevation, dem_grid)`` in a projected MGA CRS.
    """
    bounds, bounds_crs = _resolve_aoi_bounds(aoi)
    if bounds_crs is None:
        # The national DEM source interprets a crs-less AOI as EPSG:4326;
        # guard against projected (metre) coordinates given without a crs.
        dem_source.assert_geographic_bounds(bounds)
    logger.info("Fetching national SRTM DEM window for AOI %s", bounds)
    geo_dem, geo_grid = dem_source.fetch_dem_window(bounds, bounds_crs)
    target_epsg = dem_source.mga_epsg_for_aoi(bounds, bounds_crs)
    logger.info("Reprojecting national DEM to MGA EPSG:%d", target_epsg)
    return dem_source.reproject_dem_to_mga(geo_dem, geo_grid, target_epsg)


def _aoi_centre_lonlat(aoi: AOI) -> tuple[float, float]:
    """Return the AOI centre as ``(longitude, latitude)`` in degrees.

    Args:
        aoi: The configured area of interest.

    Returns:
        The centre longitude and latitude in EPSG:4326 degrees.
    """
    bounds, bounds_crs = _resolve_aoi_bounds(aoi)
    centre_x = 0.5 * (bounds[0] + bounds[2])
    centre_y = 0.5 * (bounds[1] + bounds[3])
    if bounds_crs is None:
        return centre_x, centre_y
    from bal_toolbox_qgis.balcore._rio.crs import CRS
    from bal_toolbox_qgis.balcore._rio.warp import transform

    src = CRS.from_user_input(bounds_crs)
    geographic = CRS.from_epsg(4326)
    if src == geographic:
        return centre_x, centre_y
    xs, ys = transform(src, geographic, [centre_x], [centre_y])
    return float(xs[0]), float(ys[0])


def _resolve_fdi(config: RunConfig) -> int:
    """Resolve the configuration's FDI to a tabulated Method 1 value.

    A directly-given ``fdi`` is returned as-is. Otherwise the
    ``fdi_spec`` is resolved: ``location`` uses AS 3959:2018 Table 2.1
    (explicit region, else auto-detected from the AOI centre);
    ``weather`` fetches a provider observation, computes the McArthur
    FFDI and snaps it up to a tabulated FDI.

    Args:
        config: The validated run configuration.

    Returns:
        A tabulated FDI (one of :data:`bal_toolbox_qgis.balcore.tables.FDI_VALUES`).

    Raises:
        ValueError: If neither an FDI nor a resolvable spec is present,
            or a spec needs an AOI that is not set.
        RuntimeError: If a weather provider request fails.
    """
    if config.fdi is not None:
        return config.fdi
    spec = config.fdi_spec
    if spec is None:  # pragma: no cover - guarded by config validation
        raise ValueError("No FDI value or derivation spec was provided.")

    if spec.method == "location":
        if spec.region is not None:
            value = fdi_module.fdi_for_region(spec.region)
            logger.info("FDI from Table 2.1 region %r: %d", spec.region, value)
        else:
            if config.aoi is None:
                raise ValueError(
                    "Auto-detecting the FDI region requires an 'aoi'; set "
                    "'fdi.region' explicitly or add an AOI."
                )
            lon, lat = _aoi_centre_lonlat(config.aoi)
            region = fdi_module.detect_region_from_lonlat(lon, lat)
            value = fdi_module.fdi_for_region(region)
            logger.info(
                "FDI auto-detected as region %r (FDI %d) from AOI centre "
                "(%.4f, %.4f); confirm the fire-weather district with the "
                "relevant authority.",
                region,
                value,
                lon,
                lat,
            )
        return value

    # Weather method.
    if config.aoi is None:
        raise ValueError("Weather-derived FDI requires an 'aoi' for the location.")
    lon, lat = _aoi_centre_lonlat(config.aoi)
    drought = spec.drought_factor if spec.drought_factor is not None else 10.0
    observations = _gather_weather(spec, lat, lon)

    # Compute the FFDI for each day and take the worst (maximum) day; over a
    # single day or current conditions this is just that one observation.
    worst_obs = observations[0]
    worst_ffdi = -1.0
    for obs in observations:
        day_ffdi = fdi_module.mcarthur_ffdi(
            obs.temperature_c, obs.relative_humidity_pct, obs.wind_speed_kmh, drought
        )
        if day_ffdi > worst_ffdi:
            worst_ffdi, worst_obs = day_ffdi, obs

    value = fdi_module.snap_to_tabulated(worst_ffdi)
    logger.info(
        "FDI from weather: worst of %d day(s) is %s "
        "(T=%.1fC RH=%.0f%% wind=%.1fkm/h DF=%.1f) -> FFDI %.1f -> tabulated "
        "FDI %d (rounded up; confirm with the relevant authority).",
        len(observations),
        worst_obs.source,
        worst_obs.temperature_c,
        worst_obs.relative_humidity_pct,
        worst_obs.wind_speed_kmh,
        drought,
        worst_ffdi,
        value,
    )
    return value


def _gather_weather(
    spec: FdiSpec, latitude: float, longitude: float
) -> list[weather.WeatherObservation]:
    """Fetch the weather observation(s) for an FDI weather spec.

    Returns current conditions (one observation) when no date is given,
    or one observation per day across the requested range.

    Args:
        spec: The validated weather :class:`FdiSpec`.
        latitude: AOI centre latitude in degrees north.
        longitude: AOI centre longitude in degrees east.

    Returns:
        A non-empty list of :class:`weather.WeatherObservation`.

    Raises:
        ValueError: If the provider/date combination is unsupported.
        RuntimeError: If a provider request fails.
    """
    if spec.provider == "open_meteo":
        if spec.start_date is None:
            return [weather.fetch_open_meteo(latitude, longitude)]
        assert spec.end_date is not None  # config pairs start/end
        return weather.fetch_open_meteo_daily(
            latitude, longitude, spec.start_date, spec.end_date
        )
    if spec.provider == "silo":
        if spec.start_date is None or spec.end_date is None:
            raise ValueError("The SILO provider requires a date or date range.")
        return _gather_silo(spec, latitude, longitude)
    raise ValueError(f"Unknown weather provider {spec.provider!r}.")  # pragma: no cover


def _gather_silo(
    spec: FdiSpec, latitude: float, longitude: float
) -> list[weather.WeatherObservation]:
    """Fetch SILO temperature/humidity and supply the missing wind.

    SILO does not measure wind. If ``spec.wind_speed_kmh`` is given it is
    used for every day; otherwise the daily maximum wind is fetched from
    Open-Meteo for the same dates and matched per day. Days without a
    matched wind are dropped with a warning.

    Args:
        spec: The validated SILO weather spec (dates present).
        latitude: AOI centre latitude in degrees north.
        longitude: AOI centre longitude in degrees east.

    Returns:
        A non-empty list of :class:`weather.WeatherObservation`.

    Raises:
        RuntimeError: If a provider request fails or no day has wind.
    """
    assert spec.start_date is not None and spec.end_date is not None

    if spec.wind_speed_kmh is not None:
        return weather.fetch_silo_range(
            latitude, longitude, spec.start_date, spec.end_date, spec.wind_speed_kmh
        )

    # Fetch SILO temperature/humidity (wind filled in below) and the daily
    # max wind from Open-Meteo for the same dates, then merge by date.
    silo_obs = weather.fetch_silo_range(
        latitude, longitude, spec.start_date, spec.end_date, 0.0
    )
    logger.info("SILO has no wind; sourcing daily wind from Open-Meteo.")
    wind_by_date = weather.fetch_open_meteo_wind_by_date(
        latitude, longitude, spec.start_date, spec.end_date
    )

    merged: list[weather.WeatherObservation] = []
    for obs in silo_obs:
        wind = wind_by_date.get(obs.date) if obs.date is not None else None
        if wind is None:
            logger.warning("No Open-Meteo wind for SILO day %s; skipping it.", obs.date)
            continue
        merged.append(
            weather.WeatherObservation(
                temperature_c=obs.temperature_c,
                relative_humidity_pct=obs.relative_humidity_pct,
                wind_speed_kmh=wind,
                source=f"SILO+Open-Meteo wind {obs.date}",
                date=obs.date,
            )
        )
    if len(merged) != len(silo_obs):
        logger.warning(
            "Wind matched for %d of %d SILO day(s); unmatched days are "
            "excluded from the FDI estimate.",
            len(merged),
            len(silo_obs),
        )
    if not merged:
        raise RuntimeError(
            "Could not match Open-Meteo wind to any SILO day; set "
            "'fdi.wind_speed_kmh' manually."
        )
    return merged


def run(
    config: RunConfig,
    overwrite: bool = False,
    write_inputs: bool = False,
    timestamp: bool = False,
) -> dict[str, Path]:
    """Run the full BAL Method 1 calculation and write outputs.

    Args:
        config: Validated run configuration.
        overwrite: Replace existing output rasters when ``True``; when
            ``False`` (the default) the run stops before any computation
            if an output file already exists.
        write_inputs: If ``True`` (or ``config.write_inputs``), also write
            the aligned input rasters used by the calculation -- the DEM,
            the raw NVIS MVG codes and the reclassified AS 3959 vegetation
            class -- alongside the BAL outputs, for QA.
        timestamp: If ``True`` (or ``config.timestamp``), append a single
            UTC timestamp suffix to every output filename so successive
            runs do not clash (e.g. ``bal_max_20260616T024500Z.tif``).

    Returns:
        Mapping of output name to the written raster path. Includes
        ``"max"`` and, optionally, each compass direction; when input
        rasters are written it also includes ``"dem"``,
        ``"vegetation_mvg"`` and ``"vegetation_class"``.

    Raises:
        ValueError: If the inputs cannot be aligned or are invalid.
        FileExistsError: If an output exists and ``overwrite`` is
            ``False``.
    """
    emit_inputs = write_inputs or config.write_inputs
    suffix = f"_{output_timestamp()}" if (timestamp or config.timestamp) else ""

    # Fail fast: refuse to clobber existing outputs before doing any
    # (potentially network-bound and expensive) work.
    names = engine.output_names(config.write_directions)
    target_paths = {
        name: config.output_dir / f"bal_{name}{suffix}.tif" for name in names
    }
    input_paths: dict[str, Path] = {}
    if emit_inputs:
        input_paths = {
            "dem": config.output_dir / f"dem{suffix}.tif",
            "vegetation_mvg": config.output_dir / f"vegetation_mvg{suffix}.tif",
            "vegetation_class": config.output_dir / f"vegetation_class{suffix}.tif",
        }
    if not overwrite:
        existing = sorted(
            str(p)
            for p in (*target_paths.values(), *input_paths.values())
            if p.exists()
        )
        if existing:
            raise FileExistsError(
                "Output raster(s) already exist: "
                + ", ".join(existing)
                + ". Re-run with overwrite enabled (--overwrite) to replace them."
            )

    # Resolve the FDI first so a bad region/provider fails before the
    # (potentially network-bound) DEM and vegetation reads.
    fdi_value = _resolve_fdi(config)

    elevation, dem_grid, veg_raw, veg_grid = _read_inputs(config)
    veg_raw = _align_to_dem(veg_raw, veg_grid, dem_grid)

    # Slope/aspect and the distance search all assume the cell size is in
    # metres. A geographic (degree) DEM would feed ~0.0003 "metres" per cell
    # into the slope math, silently producing nonsense. The national source
    # reprojects to an MGA zone; a user-supplied DEM must already be projected.
    if dem_grid.crs is not None and dem_grid.crs.is_geographic:
        raise ValueError(
            f"The DEM is in a geographic CRS ({dem_grid.crs.to_string()}); BAL "
            "requires a projected metre CRS so slope, aspect and distance are "
            "in metres. Reproject the DEM to the appropriate GDA2020 MGA zone "
            "(EPSG:7849-7856) before running, or use the 'srtm_1s' national "
            "source, which reprojects automatically."
        )

    logger.info("Deriving slope and aspect")
    slope_deg, aspect_deg = terrain.slope_aspect(
        elevation, dem_grid.pixel_width, dem_grid.pixel_height
    )
    slope_band = terrain.reclassify_slope(slope_deg)
    aspect_band = terrain.reclassify_aspect(aspect_deg)

    logger.info("Reclassifying vegetation into AS 3959:2018 classes")
    veg_class = terrain.reclassify_vegetation(veg_raw, config.remap)

    if config.method == 2:
        from bal_toolbox_qgis.balcore import detailed

        logger.warning(
            "Method 2 (detailed radiant-heat-flux) is EXPERIMENTAL and not yet "
            "verified against the AS 3959:2018 prescriptive tables; treat its "
            "output as indicative only."
        )
        logger.info("Computing BAL (Method 2) for FDI %d", fdi_value)
        results = detailed.compute_bal_method2(
            veg_class,
            slope_band,
            aspect_band,
            dem_grid.pixel_width,
            fdi_value,
            config.receiver_elevation_m,
        )
    else:
        logger.info("Computing BAL (Method 1) for FDI %d", fdi_value)
        results = engine.compute_bal(
            veg_class,
            slope_band,
            aspect_band,
            dem_grid.pixel_width,
            fdi_value,
        )

    mask_polygon = (
        config.aoi.polygon_path
        if config.aoi is not None and config.aoi.polygon_path is not None
        else None
    )
    if mask_polygon is not None:
        logger.info("Masking outputs to polygon AOI %s", mask_polygon)

    reprojecting = (
        dem_grid.crs is not None
        and CRS.from_user_input(config.output_crs) != dem_grid.crs
    )
    if reprojecting:
        logger.info("Reprojecting outputs to %s", config.output_crs)

    written: dict[str, Path] = {}
    for name, out_path in target_paths.items():
        raster = results[name]
        if mask_polygon is not None:
            raster = mask_to_polygon(raster, dem_grid, mask_polygon)
        # BAL bands are categorical -> nearest-neighbour (no invented values).
        out_data, out_grid = _to_output_crs(
            raster, dem_grid, config.output_crs, Resampling.nearest
        )
        write_raster(out_path, out_data, out_grid, overwrite=overwrite)
        written[name] = out_path
        logger.info("Wrote %s", out_path)

    if emit_inputs:
        # QA rasters: the aligned inputs the calculation actually used. The DEM
        # is continuous (bilinear); the categorical veg rasters use nearest.
        qa_rasters: list[
            tuple[str, NDArray[np.floating] | NDArray[np.integer], Resampling]
        ] = [
            ("dem", elevation, Resampling.bilinear),
            ("vegetation_mvg", veg_raw, Resampling.nearest),
            ("vegetation_class", veg_class, Resampling.nearest),
        ]
        for name, array, resampling in qa_rasters:
            out_path = input_paths[name]
            out_data, out_grid = _to_output_crs(
                array, dem_grid, config.output_crs, resampling
            )
            write_raster(out_path, out_data, out_grid, overwrite=overwrite)
            written[name] = out_path
            logger.info("Wrote input raster %s", out_path)

    return written

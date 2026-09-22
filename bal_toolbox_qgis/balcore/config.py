"""YAML run-configuration loading and validation.

A single YAML file fully specifies a BAL run: the input DEM and
vegetation rasters, the output directory, the Fire Danger Index, the
vegetation remap rules, an optional area of interest (AOI), and which
output rasters to write. This replaces the interactive ArcGIS toolbox
parameter dialog of the original tool.

The ``remap`` key accepts either an explicit list of
``[low, high, class]`` rules or the name of a built-in preset (for
example ``nvis_mvg`` for the canonical NVIS Major Vegetation Group
mapping; see :mod:`bal_toolbox_qgis.balcore.nvis`).

The optional ``aoi`` block restricts the calculation to an area of
interest, which also lets the toolbox read only the relevant window of a
large (e.g. continental) input raster instead of loading all of it. It
is given either as a bounding box or as a polygon vector file; see
:func:`_parse_aoi`.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from bal_toolbox_qgis.balcore import dem_source, nvis, tables


@dataclass(frozen=True, slots=True)
class AOI:
    """Area of interest restricting the BAL calculation.

    Exactly one of :attr:`bbox` or :attr:`polygon_path` is set.

    Attributes:
        bbox: Bounding box ``(xmin, ymin, xmax, ymax)``, or ``None`` when
            a polygon is used instead.
        polygon_path: Path to a polygon vector file (shapefile, GeoJSON,
            ...), or ``None`` when a bounding box is used instead.
        crs: Coordinate reference system of the AOI coordinates as a
            string (e.g. ``"EPSG:4326"``), or ``None`` to interpret them
            in the DEM's coordinate reference system.
    """

    bbox: tuple[float, float, float, float] | None
    polygon_path: Path | None
    crs: str | None


@dataclass(frozen=True, slots=True)
class AssignSpec:
    """Optional building/cadastre BAL-assignment settings.

    Records where the polygon footprint layers live and whether the
    ``assign-buildings`` step should run for them. Assignment is a
    separate command (``bal-toolbox assign-buildings --config ...``); this
    block simply supplies its inputs.

    Attributes:
        enabled: Whether assignment should be performed.
        buildings_path: Path to a building-footprint GeoJSON, or ``None``.
        cadastre_path: Path to a cadastre/parcel GeoJSON, or ``None``.
    """

    enabled: bool
    buildings_path: Path | None
    cadastre_path: Path | None


@dataclass(frozen=True, slots=True)
class HistorySpec:
    """Optional fire-history / fuel-recovery analysis settings.

    Configures the (separate) ``analyse-history`` step, which annotates
    building/cadastre footprints with past-fire and fuel-recovery context
    alongside -- never altering -- the BAL rating. Assignment to BAL is a
    separate concern (see :class:`AssignSpec`).

    Attributes:
        enabled: Whether the history analysis should be performed.
        fire_path: Path to a fire-history polygon layer.
        year_field: Property name holding each polygon's fire year.
        analysis_year: Reference year for years-since-fire, or ``None`` to
            use the current year at run time.
        footprints_path: Path to the footprint layer to annotate, or
            ``None`` to reuse the ``assign`` buildings layer.
        veg_class_path: Path to a reclassified AS 3959 vegetation-class
            raster enabling the fuel-recovery estimate, or ``None`` to
            skip fuel recovery.
    """

    enabled: bool
    fire_path: Path
    year_field: str
    analysis_year: int | None
    footprints_path: Path | None
    veg_class_path: Path | None


@dataclass(frozen=True, slots=True)
class FdiSpec:
    """How to derive the Fire Danger Index when it is not given directly.

    Exactly one method is described. For ``"location"`` the FDI comes from
    AS 3959:2018 Table 2.1 (an explicit ``region`` key, else auto-detected
    from the AOI). For ``"weather"`` a McArthur FFDI is computed from a
    provider's observations and snapped up to a tabulated FDI.

    Attributes:
        method: Either ``"location"`` or ``"weather"``.
        region: Table 2.1 region key for the location method, or ``None``
            to auto-detect from the AOI centre.
        provider: Weather provider for the weather method
            (``"open_meteo"`` or ``"silo"``), or ``None``.
        drought_factor: Drought factor (0-10) for the FFDI; ``None`` uses
            the conservative default.
        wind_speed_kmh: Optional manual wind speed (km/h) overriding the
            provider. SILO has no wind; when this is ``None`` the SILO
            wind is auto-filled from Open-Meteo for the same dates.
        start_date: First date of interest as ``YYYYMMDD``; ``None`` means
            current conditions (Open-Meteo only).
        end_date: Last date of interest as ``YYYYMMDD``; equals
            ``start_date`` for a single day, ``None`` for current
            conditions. When a range is given the FDI is the worst
            (maximum) daily FFDI across it.
    """

    method: str
    region: str | None = None
    provider: str | None = None
    drought_factor: float | None = None
    wind_speed_kmh: float | None = None
    start_date: str | None = None
    end_date: str | None = None


@dataclass(frozen=True, slots=True)
class RunConfig:
    """Validated configuration for a single BAL Method 1 run.

    Attributes:
        dem_path: Path to the projected DEM raster.
        vegetation_path: Path to the raw vegetation raster.
        output_dir: Directory for output rasters.
        fdi: Fire Danger Index (one of :data:`tables.FDI_VALUES`), or
            ``None`` when ``fdi_spec`` describes how to derive it.
        fdi_spec: How to derive the FDI (location or weather), or ``None``
            when ``fdi`` is given directly.
        remap: Vegetation remap rules as ``(low, high, class_code)``.
        write_directions: If true, write all eight directional rasters
            in addition to the maximum-BAL raster.
        aoi: Optional area of interest restricting the calculation, or
            ``None`` to process the full overlap of the inputs.
        dem_is_national: If true, ``dem_path`` is ignored and the DEM is
            fetched on demand from the national SRTM 1-Second WCS over the
            AOI (which is then required).
        write_inputs: If true, also write the aligned input rasters used
            by the calculation (DEM, raw NVIS MVG, and reclassified
            AS 3959 vegetation class) alongside the BAL outputs, for QA.
        timestamp: If true, append a UTC timestamp suffix to every output
            filename so successive runs do not clash (e.g.
            ``bal_max_20260616T024500Z.tif``).
        method: BAL methodology -- ``1`` for the prescriptive Method 1
            distance tables (default), ``2`` for the detailed
            radiant-heat-flux Method 2 (Appendix B). Method 2 is
            EXPERIMENTAL and not yet verified against the published tables.
        receiver_elevation_m: Method 2 receiver elevation above ground (m);
            ignored by Method 1.
        assign: Optional building/cadastre assignment settings, or ``None``
            if no ``assign:`` block was given.
        history: Optional fire-history / fuel-recovery analysis settings,
            or ``None`` if no ``history:`` block was given.
        output_crs: Coordinate reference system for ALL written outputs
            (BAL rasters, QA input rasters and annotated GeoJSONs), as a
            string such as ``"EPSG:4283"``. Defaults to GDA94 geographic
            lat/lon (EPSG:4283). The BAL is always *computed* in a
            projected metre CRS (slope/aspect/distance need metres); this
            only governs the CRS the results are reprojected into on the
            way out.
    """

    dem_path: Path
    vegetation_path: Path
    output_dir: Path
    fdi: int | None
    remap: tuple[tuple[float, float, int], ...]
    write_directions: bool = field(default=False)
    aoi: AOI | None = field(default=None)
    dem_is_national: bool = field(default=False)
    fdi_spec: FdiSpec | None = field(default=None)
    write_inputs: bool = field(default=False)
    timestamp: bool = field(default=False)
    method: int = field(default=1)
    receiver_elevation_m: float = field(default=0.0)
    assign: AssignSpec | None = field(default=None)
    history: HistorySpec | None = field(default=None)
    output_crs: str = field(default="EPSG:4283")


def _parse_remap(raw: object) -> tuple[tuple[float, float, int], ...]:
    """Parse and validate the vegetation remap section.

    The ``remap`` value may be either the name of a built-in preset (a
    string, e.g. ``"nvis_mvg"``) or an explicit list of
    ``[low, high, class_code]`` triples.

    Args:
        raw: The ``remap`` value loaded from YAML.

    Returns:
        The remap as a tuple of validated triples.

    Raises:
        ValueError: If the structure, preset name, or any class code is
            invalid.
    """
    if isinstance(raw, str):
        try:
            return nvis.get_preset(raw)
        except KeyError as err:
            raise ValueError(str(err)) from err

    if not isinstance(raw, list) or not raw:
        raise ValueError(
            "'remap' must be a preset name or a non-empty list of [low, high, class]."
        )

    rules: list[tuple[float, float, int]] = []
    for entry in raw:
        if not isinstance(entry, (list, tuple)) or len(entry) != 3:
            raise ValueError(
                f"Each remap rule must be [low, high, class]; got {entry!r}."
            )
        low, high, class_code = entry
        if class_code not in tables.VEG_CLASSES:
            raise ValueError(
                f"Remap class {class_code!r} must be one of {tables.VEG_CLASSES}."
            )
        if low > high:
            raise ValueError(f"Remap low {low} exceeds high {high}.")
        rules.append((float(low), float(high), int(class_code)))
    return tuple(rules)


def _parse_bbox(raw: object) -> tuple[float, float, float, float]:
    """Parse and validate a bounding box ``[xmin, ymin, xmax, ymax]``.

    Args:
        raw: The ``bbox`` value loaded from YAML.

    Returns:
        The validated bounding box as a 4-tuple of floats.

    Raises:
        ValueError: If the structure is wrong or the box is degenerate.
    """
    if not isinstance(raw, (list, tuple)) or len(raw) != 4:
        raise ValueError("'aoi.bbox' must be [xmin, ymin, xmax, ymax].")
    try:
        xmin, ymin, xmax, ymax = (float(v) for v in raw)
    except (TypeError, ValueError) as err:
        raise ValueError(f"'aoi.bbox' values must be numbers; got {raw!r}.") from err
    if xmin >= xmax or ymin >= ymax:
        raise ValueError(
            f"'aoi.bbox' must have xmin < xmax and ymin < ymax; got {raw!r}."
        )
    return (xmin, ymin, xmax, ymax)


def _parse_aoi(raw: object, resolve: object) -> AOI:
    """Parse and validate the optional ``aoi`` block.

    The block must specify exactly one of ``bbox`` (a
    ``[xmin, ymin, xmax, ymax]`` list) or ``polygon`` (a path to a vector
    file), plus an optional ``crs`` string. When ``crs`` is omitted the
    AOI coordinates are interpreted in the DEM's coordinate reference
    system.

    Args:
        raw: The ``aoi`` value loaded from YAML (expected to be a mapping).
        resolve: Callable resolving a path value relative to the config
            file (the closure defined in :func:`load_config`).

    Returns:
        A validated :class:`AOI`.

    Raises:
        ValueError: If the structure is invalid or both/neither of
            ``bbox`` and ``polygon`` are given.
    """
    if not isinstance(raw, dict):
        raise ValueError("'aoi' must be a mapping with 'bbox' or 'polygon'.")

    has_bbox = "bbox" in raw and raw["bbox"] is not None
    has_polygon = "polygon" in raw and raw["polygon"] is not None
    if has_bbox == has_polygon:
        raise ValueError("'aoi' must specify exactly one of 'bbox' or 'polygon'.")

    crs = raw.get("crs")
    if crs is not None and not isinstance(crs, str):
        raise ValueError(f"'aoi.crs' must be a string (e.g. 'EPSG:4326'); got {crs!r}.")

    if has_bbox:
        return AOI(bbox=_parse_bbox(raw["bbox"]), polygon_path=None, crs=crs)

    polygon_path = resolve(raw["polygon"])  # type: ignore[operator]
    if not polygon_path.exists():
        raise ValueError(f"'aoi.polygon' file not found: {polygon_path}")
    return AOI(bbox=None, polygon_path=polygon_path, crs=crs)


_FDI_METHODS: frozenset[str] = frozenset({"location", "weather"})
_WEATHER_PROVIDERS: frozenset[str] = frozenset({"open_meteo", "silo"})


def _normalise_date(value: object, field_name: str) -> str:
    """Validate a date value and return it as ``YYYYMMDD``.

    Accepts ``YYYYMMDD`` or ISO ``YYYY-MM-DD`` strings.

    Args:
        value: The raw date value from YAML.
        field_name: Name of the field (for error messages).

    Returns:
        The date as an 8-digit ``YYYYMMDD`` string.

    Raises:
        ValueError: If the value is not a valid date string.
    """
    from datetime import date, datetime

    if isinstance(value, bool):
        raise ValueError(f"{field_name!r} must be a date, not a boolean.")
    # YAML parses an unquoted YYYY-MM-DD as a date/datetime object.
    if isinstance(value, datetime):
        return value.strftime("%Y%m%d")
    if isinstance(value, date):
        return value.strftime("%Y%m%d")
    if isinstance(value, int):
        text = str(value)
    elif isinstance(value, str):
        text = value.strip().replace("-", "")
    else:
        raise ValueError(f"{field_name!r} must be a date string; got {value!r}.")
    if len(text) != 8 or not text.isdigit():
        raise ValueError(
            f"{field_name!r} must be 'YYYYMMDD' or 'YYYY-MM-DD'; got {value!r}."
        )
    try:
        datetime.strptime(text, "%Y%m%d")
    except ValueError as err:
        raise ValueError(f"{field_name!r} is not a real date: {value!r}.") from err
    return text


def _parse_date_range(raw: dict[str, object]) -> tuple[str | None, str | None]:
    """Parse the weather date selection into a ``(start, end)`` pair.

    Recognises a single ``date`` (shorthand for a one-day range) or a
    ``start_date``/``end_date`` range. All dates are normalised to
    ``YYYYMMDD``. ``(None, None)`` means current conditions.

    Args:
        raw: The ``fdi`` mapping from YAML.

    Returns:
        ``(start_date, end_date)`` as ``YYYYMMDD`` strings, or
        ``(None, None)`` if no date selection was given.

    Raises:
        ValueError: If the selection is inconsistent or out of order.
    """
    single = raw.get("date")
    start = raw.get("start_date")
    end = raw.get("end_date")

    if single is not None and (start is not None or end is not None):
        raise ValueError(
            "Specify either 'fdi.date' or 'fdi.start_date'/'fdi.end_date', not both."
        )
    if single is not None:
        day = _normalise_date(single, "fdi.date")
        return day, day
    if start is None and end is None:
        return None, None
    if start is None or end is None:
        raise ValueError(
            "A weather date range needs both 'fdi.start_date' and 'fdi.end_date'."
        )
    start_norm = _normalise_date(start, "fdi.start_date")
    end_norm = _normalise_date(end, "fdi.end_date")
    if start_norm > end_norm:
        raise ValueError(
            f"'fdi.start_date' ({start_norm}) must not be after "
            f"'fdi.end_date' ({end_norm})."
        )
    return start_norm, end_norm


def _parse_assign(raw: object, resolve: Callable[[object], Path]) -> AssignSpec:
    """Parse and validate the optional ``assign:`` block.

    The block configures the (separate) ``assign-buildings`` step:

    .. code-block:: yaml

        assign:
          enabled: true
          buildings: footprints.geojson   # optional
          cadastre: parcels.geojson       # optional

    At least one of ``buildings`` or ``cadastre`` must be given when the
    block is present. Paths resolve relative to the config file and must
    exist.

    Args:
        raw: The ``assign`` value from YAML (expected to be a mapping).
        resolve: Closure resolving a path relative to the config file.

    Returns:
        A validated :class:`AssignSpec`.

    Raises:
        ValueError: If the structure is invalid or a path does not exist.
    """
    if not isinstance(raw, dict):
        raise ValueError("'assign' must be a mapping (enabled/buildings/cadastre).")

    enabled = bool(raw.get("enabled", True))

    def _resolve_layer(key: str) -> Path | None:
        value = raw.get(key)
        if value is None:
            return None
        resolved = resolve(value)
        if not resolved.exists():
            raise ValueError(f"'assign.{key}' file not found: {resolved}")
        return resolved

    buildings = _resolve_layer("buildings")
    cadastre = _resolve_layer("cadastre")
    if buildings is None and cadastre is None:
        raise ValueError(
            "'assign' must specify at least one of 'buildings' or 'cadastre'."
        )
    return AssignSpec(enabled=enabled, buildings_path=buildings, cadastre_path=cadastre)


def _parse_history(raw: object, resolve: Callable[[object], Path]) -> HistorySpec:
    """Parse and validate the optional ``history:`` block.

    The block configures the (separate) ``analyse-history`` step:

    .. code-block:: yaml

        history:
          enabled: true
          fire: fire_history.geojson    # required: polygon layer w/ a year
          year_field: fire_year         # property holding the fire year
          analysis_year: 2024           # optional; defaults to current year
          footprints: buildings.geojson # optional; else the assign buildings
          vegetation_class: vegetation_class.tif  # optional; enables recovery

    Args:
        raw: The ``history`` value from YAML (expected to be a mapping).
        resolve: Closure resolving a path relative to the config file.

    Returns:
        A validated :class:`HistorySpec`.

    Raises:
        ValueError: If the structure is invalid or a path does not exist.
    """
    if not isinstance(raw, dict):
        raise ValueError(
            "'history' must be a mapping (fire/year_field/analysis_year/...)."
        )

    enabled = bool(raw.get("enabled", True))

    fire_value = raw.get("fire")
    if fire_value is None:
        raise ValueError("'history.fire' (a fire-history polygon layer) is required.")
    fire_path = resolve(fire_value)
    if not fire_path.exists():
        raise ValueError(f"'history.fire' file not found: {fire_path}")

    year_field = raw.get("year_field", "year")
    if not isinstance(year_field, str) or not year_field:
        raise ValueError(
            f"'history.year_field' must be a non-empty string; got {year_field!r}."
        )

    analysis_year = raw.get("analysis_year")
    if analysis_year is not None and (
        isinstance(analysis_year, bool) or not isinstance(analysis_year, int)
    ):
        raise ValueError(
            f"'history.analysis_year' must be an integer year; got {analysis_year!r}."
        )

    def _resolve_existing(key: str) -> Path | None:
        value = raw.get(key)
        if value is None:
            return None
        resolved = resolve(value)
        if not resolved.exists():
            raise ValueError(f"'history.{key}' file not found: {resolved}")
        return resolved

    footprints_path = _resolve_existing("footprints")
    veg_class_path = _resolve_existing("vegetation_class")
    return HistorySpec(
        enabled=enabled,
        fire_path=fire_path,
        year_field=year_field,
        analysis_year=analysis_year,
        footprints_path=footprints_path,
        veg_class_path=veg_class_path,
    )


def _parse_fdi(raw: object) -> tuple[int | None, FdiSpec | None]:
    """Parse the ``fdi`` field into a direct value or a derivation spec.

    Args:
        raw: The ``fdi`` value from YAML -- either an integer (one of
            :data:`tables.FDI_VALUES`) or a mapping with a ``from`` key
            of ``"location"`` or ``"weather"``.

    Returns:
        A tuple ``(fdi, fdi_spec)`` with exactly one element set: a
        validated integer FDI, or an :class:`FdiSpec` describing how to
        derive it at run time.

    Raises:
        ValueError: If the value or mapping is invalid.
    """
    if isinstance(raw, bool):  # guard: bool is an int subclass
        raise ValueError("'fdi' must be a number or a mapping, not a boolean.")
    if isinstance(raw, int):
        if raw not in tables.FDI_VALUES:
            raise ValueError(f"'fdi' must be one of {tables.FDI_VALUES}; got {raw!r}.")
        return raw, None
    if not isinstance(raw, dict):
        raise ValueError(
            "'fdi' must be an integer or a mapping with a 'from' key "
            "('location' or 'weather')."
        )

    method = str(raw.get("from", "")).strip().lower()
    if method not in _FDI_METHODS:
        raise ValueError(
            f"'fdi.from' must be one of {sorted(_FDI_METHODS)}; got {method!r}."
        )

    if method == "location":
        region = raw.get("region")
        if region is not None and not isinstance(region, str):
            raise ValueError(f"'fdi.region' must be a string; got {region!r}.")
        region_key = region.strip().lower() if isinstance(region, str) else None
        if region_key is not None:
            from bal_toolbox_qgis.balcore import fdi as fdi_module

            # Validate the region now so a typo fails at load time, not mid-run.
            fdi_module.fdi_for_region(region_key)
        return None, FdiSpec(method="location", region=region_key)

    provider = str(raw.get("provider", "")).strip().lower()
    if provider not in _WEATHER_PROVIDERS:
        raise ValueError(
            f"'fdi.provider' must be one of {sorted(_WEATHER_PROVIDERS)}; "
            f"got {provider!r}."
        )
    drought = raw.get("drought_factor")
    if drought is not None:
        if isinstance(drought, bool) or not isinstance(drought, (int, float)):
            raise ValueError(
                f"'fdi.drought_factor' must be a number in 0-10; got {drought!r}."
            )
        if not 0.0 <= float(drought) <= 10.0:
            raise ValueError(f"'fdi.drought_factor' must be in 0-10; got {drought!r}.")
    wind = raw.get("wind_speed_kmh")
    if wind is not None:
        if isinstance(wind, bool) or not isinstance(wind, (int, float)):
            raise ValueError(
                f"'fdi.wind_speed_kmh' must be a non-negative number; got {wind!r}."
            )
        if float(wind) < 0:
            raise ValueError(
                f"'fdi.wind_speed_kmh' must be non-negative; got {wind!r}."
            )
    # SILO supplies no wind; if not given it is auto-filled from Open-Meteo
    # (see workflow), so 'wind_speed_kmh' is an optional manual override only.
    start_date, end_date = _parse_date_range(raw)
    if provider == "silo" and start_date is None:
        raise ValueError(
            "The SILO provider is a daily product; set 'fdi.date' or "
            "'fdi.start_date'/'fdi.end_date'."
        )
    return None, FdiSpec(
        method="weather",
        provider=provider,
        drought_factor=float(drought) if drought is not None else None,
        wind_speed_kmh=float(wind) if wind is not None else None,
        start_date=start_date,
        end_date=end_date,
    )


def _parse_method(raw: object) -> int:
    """Validate the BAL method selector (1 or 2)."""
    # bool is an int subclass, so `True in (1, 2)` is True and `1.0 in (1, 2)`
    # is True; require a genuine int to reject `method: true`/`method: 2.0`.
    if type(raw) is not int or raw not in (1, 2):
        raise ValueError(f"'method' must be the integer 1 or 2; got {raw!r}.")
    return raw


def _parse_output_crs(raw: object) -> str:
    """Validate the optional ``output_crs`` and return it as a string.

    Args:
        raw: The ``output_crs`` value from YAML (a CRS string such as
            ``"EPSG:4283"``), or ``None`` to use the GDA94 lat/lon default.

    Returns:
        The validated CRS string (``"EPSG:4283"`` when unset).

    Raises:
        ValueError: If the value is not a string rasterio can parse.
    """
    if raw is None:
        return "EPSG:4283"
    if not isinstance(raw, str) or not raw.strip():
        raise ValueError(f"'output_crs' must be a CRS string; got {raw!r}.")
    from bal_toolbox_qgis.balcore._rio.crs import CRS
    from bal_toolbox_qgis.balcore._rio.errors import CRSError

    try:
        CRS.from_user_input(raw)
    except CRSError as err:
        raise ValueError(f"'output_crs' is not a valid CRS: {raw!r} ({err}).") from err
    return raw


def load_config(path: Path) -> RunConfig:
    """Load and validate a YAML run configuration.

    Args:
        path: Path to the YAML configuration file.

    Returns:
        A validated :class:`RunConfig`.

    Raises:
        FileNotFoundError: If ``path`` does not exist.
        ValueError: If a required key is missing or a value is invalid.
    """
    if not path.exists():
        raise FileNotFoundError(f"Configuration file not found: {path}")

    with path.open("r", encoding="utf-8") as handle:
        raw = yaml.safe_load(handle)

    if not isinstance(raw, dict):
        raise ValueError("Configuration root must be a mapping of keys to values.")

    missing = {"dem", "vegetation", "output_dir", "fdi", "remap"} - raw.keys()
    if missing:
        raise ValueError(f"Configuration missing required keys: {sorted(missing)}.")

    fdi, fdi_spec = _parse_fdi(raw["fdi"])

    base = path.resolve().parent

    def _resolve(value: object) -> Path:
        candidate = Path(str(value)).expanduser()
        return candidate if candidate.is_absolute() else base / candidate

    aoi_raw = raw.get("aoi")
    aoi = _parse_aoi(aoi_raw, _resolve) if aoi_raw is not None else None

    assign_raw = raw.get("assign")
    assign = _parse_assign(assign_raw, _resolve) if assign_raw is not None else None

    history_raw = raw.get("history")
    history = _parse_history(history_raw, _resolve) if history_raw is not None else None

    dem_value = str(raw["dem"])
    dem_is_national = dem_source.is_national_dem(dem_value)
    if dem_is_national:
        if aoi is None:
            raise ValueError(
                "A national DEM source "
                f"({dem_value!r}) requires an 'aoi' to bound the area fetched."
            )
        # The path is unused for the WCS source but kept non-empty for clarity.
        dem_path = Path(dem_value)
    else:
        dem_path = _resolve(dem_value)

    return RunConfig(
        dem_path=dem_path,
        vegetation_path=_resolve(raw["vegetation"]),
        output_dir=_resolve(raw["output_dir"]),
        fdi=fdi,
        remap=_parse_remap(raw["remap"]),
        write_directions=bool(raw.get("write_directions", False)),
        aoi=aoi,
        dem_is_national=dem_is_national,
        fdi_spec=fdi_spec,
        write_inputs=bool(raw.get("write_inputs", False)),
        timestamp=bool(raw.get("timestamp", False)),
        method=_parse_method(raw.get("method", 1)),
        receiver_elevation_m=float(raw.get("receiver_elevation_m", 0.0)),
        assign=assign,
        history=history,
        output_crs=_parse_output_crs(raw.get("output_crs")),
    )

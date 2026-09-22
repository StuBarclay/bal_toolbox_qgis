"""External weather providers for weather-derived FDI.

Two providers supply the observations the McArthur FFDI needs
(:func:`bal_toolbox_qgis.balcore.fdi.mcarthur_ffdi`). They differ in what they
measure, which the caller must be aware of:

* **Open-Meteo** (:func:`fetch_open_meteo`) -- current temperature,
  relative humidity and 10 m wind speed. No drought factor, so a
  configured/default drought factor is used.
* **SILO** (:func:`fetch_silo`) -- the Queensland Government Long Paddock
  daily climate grid: temperature, relative humidity and rainfall, but
  **no wind**. A wind speed must therefore be supplied separately, and
  rainfall can inform the drought factor.

Each provider is a single function performing the live HTTP request, so
callers and tests can mock them. Network failures are raised as
:class:`RuntimeError`.

.. note::

   These observations describe *current/recent* conditions, not the
   design weather behind AS 3959 Table 2.1. A weather-derived FFDI is an
   estimate to inform a decision, not a regulated design value.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Final

#: Default request timeout for provider HTTP calls (seconds).
_HTTP_TIMEOUT_S: Final[float] = 30.0

#: Open-Meteo current-conditions / forecast endpoint (recent and future dates).
OPEN_METEO_URL: Final[str] = "https://api.open-meteo.com/v1/forecast"

#: Open-Meteo historical archive (ERA5) endpoint (past dates).
OPEN_METEO_ARCHIVE_URL: Final[str] = "https://archive-api.open-meteo.com/v1/archive"

#: SILO DataDrill point-dataset endpoint (Queensland Government Long Paddock).
SILO_URL: Final[str] = (
    "https://www.longpaddock.qld.gov.au/cgi-bin/silo/DataDrillDataset.php"
)


@dataclass(frozen=True, slots=True)
class WeatherObservation:
    """Weather inputs for the McArthur FFDI.

    Attributes:
        temperature_c: Air temperature in degrees Celsius.
        relative_humidity_pct: Relative humidity (%), 0-100.
        wind_speed_kmh: 10 m wind speed in km/h.
        source: Human-readable provider/observation description.
        date: Observation date as ``YYYYMMDD``, or ``None`` for current
            conditions.
    """

    temperature_c: float
    relative_humidity_pct: float
    wind_speed_kmh: float
    source: str
    date: str | None = None


def _http_get_json(url: str) -> dict[str, object]:
    """Perform an HTTP GET and parse a JSON body.

    Args:
        url: Fully-formed request URL.

    Returns:
        The parsed JSON object.

    Raises:
        RuntimeError: If the request fails or the body is not JSON.
    """
    try:
        with urllib.request.urlopen(url, timeout=_HTTP_TIMEOUT_S) as response:
            payload = response.read().decode("utf-8")
    except (urllib.error.URLError, TimeoutError, OSError) as err:
        raise RuntimeError(f"Weather request failed for {url!r}: {err}") from err
    try:
        parsed = json.loads(payload)
    except json.JSONDecodeError as err:
        raise RuntimeError(f"Weather response was not valid JSON: {err}") from err
    if not isinstance(parsed, dict):
        raise RuntimeError("Weather response JSON was not an object.")
    return parsed


def fetch_open_meteo(latitude: float, longitude: float) -> WeatherObservation:
    """Fetch current temperature, humidity and wind from Open-Meteo.

    Args:
        latitude: Latitude in degrees north.
        longitude: Longitude in degrees east.

    Returns:
        The current :class:`WeatherObservation` (wind in km/h).

    Raises:
        RuntimeError: If the request fails or required fields are missing.
    """
    query = urllib.parse.urlencode(
        {
            "latitude": latitude,
            "longitude": longitude,
            "current": "temperature_2m,relative_humidity_2m,wind_speed_10m",
            "wind_speed_unit": "kmh",
        }
    )
    data = _http_get_json(f"{OPEN_METEO_URL}?{query}")
    current = data.get("current")
    if not isinstance(current, dict):
        raise RuntimeError("Open-Meteo response had no 'current' block.")
    try:
        return WeatherObservation(
            temperature_c=float(current["temperature_2m"]),
            relative_humidity_pct=float(current["relative_humidity_2m"]),
            wind_speed_kmh=float(current["wind_speed_10m"]),
            source=f"Open-Meteo current @ {current.get('time', 'now')}",
        )
    except (KeyError, TypeError, ValueError) as err:
        raise RuntimeError(f"Open-Meteo response missing fields: {err}") from err


def fetch_silo_range(
    latitude: float,
    longitude: float,
    start_yyyymmdd: str,
    finish_yyyymmdd: str,
    wind_speed_kmh: float,
) -> list[WeatherObservation]:
    """Fetch daily temperature and humidity from SILO over a date range.

    SILO provides no wind observation, so ``wind_speed_kmh`` must be
    supplied by the caller (e.g. a design wind speed) and is applied to
    every day. Relative humidity is taken at the time of maximum
    temperature (``rh_tmax``), the worst-case daytime pairing for fire
    danger.

    Args:
        latitude: Latitude in degrees north.
        longitude: Longitude in degrees east.
        start_yyyymmdd: First date, as ``YYYYMMDD``.
        finish_yyyymmdd: Last date, as ``YYYYMMDD``.
        wind_speed_kmh: 10 m wind speed in km/h (not provided by SILO).

    Returns:
        One :class:`WeatherObservation` per day in the range.

    Raises:
        RuntimeError: If the request fails or required fields are missing.
    """
    query = urllib.parse.urlencode(
        {
            "format": "json",
            "lat": latitude,
            "lon": longitude,
            "start": start_yyyymmdd,
            "finish": finish_yyyymmdd,
            # comment codes: X=max_temp, H=rh_tmax (humidity at max temp).
            "comment": "XH",
            "username": "apirequest@longpaddock.qld.gov.au",
            "password": "apirequest",
        }
    )
    data = _http_get_json(f"{SILO_URL}?{query}")
    records = data.get("data")
    if not isinstance(records, list) or not records:
        raise RuntimeError("SILO response had no daily data.")
    observations: list[WeatherObservation] = []
    for record in records:
        if not isinstance(record, dict):
            raise RuntimeError("SILO daily record was not an object.")
        variables = record.get("variables", [])
        values: dict[str, object] = {
            str(var["variable_code"]): var.get("value")
            for var in variables
            if isinstance(var, dict) and "variable_code" in var
        }
        record_date = str(record.get("date", start_yyyymmdd)).replace("-", "")
        try:
            observations.append(
                WeatherObservation(
                    temperature_c=float(values["max_temp"]),  # type: ignore[arg-type]
                    relative_humidity_pct=float(values["rh_tmax"]),  # type: ignore[arg-type]
                    wind_speed_kmh=float(wind_speed_kmh),
                    source=f"SILO {record.get('date', start_yyyymmdd)} (wind supplied)",
                    date=record_date,
                )
            )
        except (KeyError, TypeError, ValueError) as err:
            raise RuntimeError(f"SILO response missing fields: {err}") from err
    return observations


def fetch_silo(
    latitude: float,
    longitude: float,
    date_yyyymmdd: str,
    wind_speed_kmh: float,
) -> WeatherObservation:
    """Fetch SILO temperature and humidity for a single day.

    A thin single-day wrapper over :func:`fetch_silo_range`.

    Args:
        latitude: Latitude in degrees north.
        longitude: Longitude in degrees east.
        date_yyyymmdd: The date to retrieve, as ``YYYYMMDD``.
        wind_speed_kmh: 10 m wind speed in km/h (not provided by SILO).

    Returns:
        The day's :class:`WeatherObservation` at maximum temperature.

    Raises:
        RuntimeError: If the request fails or required fields are missing.
    """
    return fetch_silo_range(
        latitude, longitude, date_yyyymmdd, date_yyyymmdd, wind_speed_kmh
    )[0]


#: The ERA5 archive lags real time by several days, so recent dates are
#: absent from it. Route anything within this many days of today (or in the
#: future) to the forecast endpoint, which also serves recent past dates.
_ARCHIVE_LAG_DAYS: int = 7


def _open_meteo_daily_endpoint(end_date: str) -> str:
    """Choose the Open-Meteo daily endpoint for a date range.

    The historical archive (ERA5) endpoint only covers dates older than a
    few days -- recent dates (including today) are not yet in it and come
    back as ``null``. The forecast endpoint serves recent past dates (up to
    ~92 days back) as well as future ones, so any date within
    :data:`_ARCHIVE_LAG_DAYS` of today, or in the future, is routed there;
    only older dates use the archive. The choice is made on the latest date
    in the range.

    Args:
        end_date: The last date of the range, as ``YYYYMMDD``.

    Returns:
        The endpoint URL to query.
    """
    from datetime import date, datetime, timedelta

    last = datetime.strptime(end_date, "%Y%m%d").date()
    archive_cutoff = date.today() - timedelta(days=_ARCHIVE_LAG_DAYS)
    return OPEN_METEO_ARCHIVE_URL if last < archive_cutoff else OPEN_METEO_URL


def _fetch_open_meteo_daily_raw(
    latitude: float,
    longitude: float,
    start_date: str,
    end_date: str,
    variables: str,
) -> dict[str, object]:
    """Fetch a raw Open-Meteo daily block, routed by date.

    Args:
        latitude: Latitude in degrees north.
        longitude: Longitude in degrees east.
        start_date: First date, as ``YYYYMMDD``.
        end_date: Last date, as ``YYYYMMDD``.
        variables: Comma-separated Open-Meteo ``daily`` variable list.

    Returns:
        The ``daily`` mapping from the response.

    Raises:
        RuntimeError: If the request fails or has no ``daily`` block.
    """
    endpoint = _open_meteo_daily_endpoint(end_date)
    query = urllib.parse.urlencode(
        {
            "latitude": latitude,
            "longitude": longitude,
            "daily": variables,
            "wind_speed_unit": "kmh",
            "timezone": "auto",
            "start_date": _iso_date(start_date),
            "end_date": _iso_date(end_date),
        }
    )
    data = _http_get_json(f"{endpoint}?{query}")
    daily = data.get("daily")
    if not isinstance(daily, dict):
        raise RuntimeError("Open-Meteo response had no 'daily' block.")
    return daily


def _daily_list(daily: dict[str, object], key: str) -> list[object]:
    """Extract a daily variable array from an Open-Meteo ``daily`` block.

    Args:
        daily: The ``daily`` mapping from an Open-Meteo response.
        key: The variable name to extract.

    Returns:
        The variable's value list.

    Raises:
        RuntimeError: If the key is missing or not a list.
    """
    value = daily.get(key)
    if not isinstance(value, list):
        raise RuntimeError(f"Open-Meteo daily response missing '{key}'.")
    return value


def fetch_open_meteo_daily(
    latitude: float,
    longitude: float,
    start_date: str,
    end_date: str,
) -> list[WeatherObservation]:
    """Fetch daily worst-case weather from Open-Meteo over a date range.

    For each day the worst-case fire-weather values are used: maximum
    temperature, minimum relative humidity and maximum wind speed. Wind
    is returned in km/h. Dates are ISO ``YYYY-MM-DD`` to the API but are
    accepted here as ``YYYYMMDD``.

    Args:
        latitude: Latitude in degrees north.
        longitude: Longitude in degrees east.
        start_date: First date, as ``YYYYMMDD``.
        end_date: Last date, as ``YYYYMMDD``.

    Returns:
        One :class:`WeatherObservation` per day in the range.

    Raises:
        RuntimeError: If the request fails or required fields are missing.
    """
    daily = _fetch_open_meteo_daily_raw(
        latitude,
        longitude,
        start_date,
        end_date,
        "temperature_2m_max,relative_humidity_2m_min,wind_speed_10m_max",
    )
    days = _daily_list(daily, "time")
    temps = _daily_list(daily, "temperature_2m_max")
    humidities = _daily_list(daily, "relative_humidity_2m_min")
    winds = _daily_list(daily, "wind_speed_10m_max")
    observations: list[WeatherObservation] = []
    for day, temp, humidity, wind in zip(days, temps, humidities, winds, strict=False):
        if temp is None or humidity is None or wind is None:
            continue  # skip days the provider could not populate
        observations.append(
            WeatherObservation(
                temperature_c=float(temp),  # type: ignore[arg-type]
                relative_humidity_pct=float(humidity),  # type: ignore[arg-type]
                wind_speed_kmh=float(wind),  # type: ignore[arg-type]
                source=f"Open-Meteo daily {day}",
                date=str(day).replace("-", ""),
            )
        )
    if not observations:
        raise RuntimeError("Open-Meteo returned no usable daily records.")
    return observations


def _iso_date(yyyymmdd: str) -> str:
    """Convert a ``YYYYMMDD`` string to ISO ``YYYY-MM-DD``.

    Args:
        yyyymmdd: An 8-digit date string.

    Returns:
        The date as ``YYYY-MM-DD``.
    """
    return f"{yyyymmdd[0:4]}-{yyyymmdd[4:6]}-{yyyymmdd[6:8]}"


def fetch_open_meteo_wind_by_date(
    latitude: float,
    longitude: float,
    start_date: str,
    end_date: str,
) -> dict[str, float]:
    """Fetch daily maximum wind speed (km/h) keyed by date from Open-Meteo.

    Used to supply wind for providers that do not measure it (SILO). The
    request is routed to the archive or forecast endpoint by date.

    Args:
        latitude: Latitude in degrees north.
        longitude: Longitude in degrees east.
        start_date: First date, as ``YYYYMMDD``.
        end_date: Last date, as ``YYYYMMDD``.

    Returns:
        Mapping of ``YYYYMMDD`` to that day's maximum 10 m wind speed in
        km/h. Days the provider could not populate are omitted.

    Raises:
        RuntimeError: If the request fails or returns no wind data.
    """
    daily = _fetch_open_meteo_daily_raw(
        latitude, longitude, start_date, end_date, "wind_speed_10m_max"
    )
    days = _daily_list(daily, "time")
    winds = _daily_list(daily, "wind_speed_10m_max")
    by_date: dict[str, float] = {}
    for day, wind in zip(days, winds, strict=False):
        if wind is None:
            continue
        # Open-Meteo returns ISO dates (YYYY-MM-DD); key by YYYYMMDD.
        by_date[str(day).replace("-", "")] = float(wind)  # type: ignore[arg-type]
    if not by_date:
        raise RuntimeError("Open-Meteo returned no usable wind data.")
    return by_date

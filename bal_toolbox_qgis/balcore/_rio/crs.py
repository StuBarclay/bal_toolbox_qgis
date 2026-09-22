"""A CRS type wrapping ``osr.SpatialReference``, mirroring ``rasterio.crs.CRS``.

The vendored core uses the class methods :meth:`CRS.from_user_input`,
:meth:`CRS.from_epsg` and :meth:`CRS.from_wkt`, the properties
:attr:`is_geographic`, and the methods :meth:`to_string` / :meth:`to_wkt` /
:meth:`to_epsg`, along with equality and hashing (e.g. ``grid.crs ==
reference.crs``).

Every :class:`~osgeo.osr.SpatialReference` this class creates is put into
*traditional GIS* axis order (long/lat, x/y) so coordinate transforms
behave like rasterio's always-xy convention rather than the
authority-defined (lat/long) order GDAL 3 uses by default.
"""

from __future__ import annotations

from typing import Any, Union

from osgeo import osr

from .errors import CRSError

CRSLike = Union["CRS", str, int, dict[str, Any], None]


def _new_srs() -> osr.SpatialReference:
    """Return a fresh SpatialReference in traditional GIS (x/y) axis order."""
    srs = osr.SpatialReference()
    srs.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
    return srs


class CRS:
    """A coordinate reference system backed by ``osr.SpatialReference``."""

    __slots__ = ("_srs",)

    def __init__(self, srs: osr.SpatialReference) -> None:
        # Always normalise to traditional GIS axis order.
        srs.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
        self._srs = srs

    # -- constructors ----------------------------------------------------
    @classmethod
    def from_epsg(cls, code: int) -> "CRS":
        """Build a CRS from an EPSG code."""
        srs = _new_srs()
        if srs.ImportFromEPSG(int(code)) != 0:
            raise CRSError(f"Invalid EPSG code: {code!r}")
        return cls(srs)

    @classmethod
    def from_wkt(cls, wkt: str) -> "CRS":
        """Build a CRS from a WKT string."""
        srs = _new_srs()
        if srs.ImportFromWkt(str(wkt)) != 0:
            raise CRSError(f"Could not parse CRS from WKT: {wkt!r}")
        return cls(srs)

    @classmethod
    def from_string(cls, value: str) -> "CRS":
        """Build a CRS from an ``"EPSG:xxxx"``/WKT/PROJ/authority string."""
        return cls.from_user_input(value)

    @classmethod
    def from_user_input(cls, value: CRSLike) -> "CRS":
        """Build a CRS from any user-supplied representation.

        Accepts an existing :class:`CRS`, an EPSG integer, an
        ``"EPSG:xxxx"`` string, a bare authority string, WKT, PROJ.4, or a
        well-known name such as ``"WGS84"``.

        Raises:
            CRSError: If the value cannot be interpreted as a CRS.
        """
        if isinstance(value, CRS):
            return value
        if value is None:
            raise CRSError("Cannot create a CRS from None")
        if isinstance(value, int):
            return cls.from_epsg(value)
        if isinstance(value, dict):
            # A PROJ-style mapping, e.g. {"init": "epsg:4326"} or params.
            init = value.get("init")
            if init:
                return cls.from_user_input(init)
            proj4 = " ".join(f"+{k}={v}" for k, v in value.items())
            value = proj4

        text = str(value).strip()
        if not text:
            raise CRSError("Cannot create a CRS from an empty string")

        srs = _new_srs()
        # SetFromUserInput handles "EPSG:xxxx", WKT, PROJ.4, urn:ogc:...,
        # and well-known names, returning 0 (OGRERR_NONE) on success.
        if srs.SetFromUserInput(text) != 0:
            raise CRSError(f"Could not parse CRS from: {value!r}")
        return cls(srs)

    # -- exporters -------------------------------------------------------
    def to_wkt(self) -> str:
        """Return this CRS as a WKT string."""
        return str(self._srs.ExportToWkt())

    def to_epsg(self) -> int | None:
        """Return the EPSG code if one can be identified, else ``None``."""
        srs = self._srs.Clone()
        srs.AutoIdentifyEPSG()
        code = srs.GetAuthorityCode(None)
        if code is None:
            return None
        try:
            return int(code)
        except (TypeError, ValueError):
            return None

    def to_authority(self) -> tuple[str, str] | None:
        """Return ``(authority, code)`` if identifiable, else ``None``."""
        srs = self._srs.Clone()
        srs.AutoIdentifyEPSG()
        name = srs.GetAuthorityName(None)
        code = srs.GetAuthorityCode(None)
        if name and code:
            return str(name), str(code)
        return None

    def to_string(self) -> str:
        """Return a compact round-trippable string for this CRS.

        Prefers ``"AUTHORITY:CODE"`` (e.g. ``"EPSG:4283"``) when the CRS
        can be identified, matching rasterio; otherwise falls back to WKT,
        which :meth:`from_user_input` can still parse.
        """
        authority = self.to_authority()
        if authority is not None:
            return f"{authority[0]}:{authority[1]}"
        return self.to_wkt()

    # -- properties ------------------------------------------------------
    @property
    def is_geographic(self) -> bool:
        """Whether this CRS uses geographic (lat/long) coordinates."""
        return bool(self._srs.IsGeographic())

    @property
    def is_projected(self) -> bool:
        """Whether this CRS uses projected coordinates."""
        return bool(self._srs.IsProjected())

    @property
    def srs(self) -> osr.SpatialReference:
        """The underlying ``osr.SpatialReference`` (traditional axis order)."""
        return self._srs

    # -- dunder ----------------------------------------------------------
    def __eq__(self, other: object) -> bool:
        if isinstance(other, CRS):
            return bool(self._srs.IsSame(other._srs))
        if other is None:
            return False
        if isinstance(other, (str, int, dict)):
            try:
                return bool(self._srs.IsSame(CRS.from_user_input(other)._srs))
            except CRSError:
                return False
        return NotImplemented

    def __ne__(self, other: object) -> bool:
        result = self.__eq__(other)
        if result is NotImplemented:
            return result
        return not result

    def __hash__(self) -> int:
        return hash(self.to_string())

    def __bool__(self) -> bool:
        return True

    def __repr__(self) -> str:
        return f"CRS.from_user_input({self.to_string()!r})"

    def __str__(self) -> str:
        return self.to_string()

"""Bridging helpers between QGIS layers and the compute core's file I/O.

The compute core reads and writes plain files (GeoTIFF rasters and
GeoJSON FeatureCollections carrying an OGC-URN ``crs`` member). These
helpers convert QGIS Processing inputs into those files and back, so the
algorithm classes stay small and the conversions are tested in one place.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

from qgis.core import (
    QgsCoordinateReferenceSystem,
    QgsFeatureSink,
    QgsProcessingException,
    QgsRasterLayer,
    QgsVectorLayer,
)


def raster_source_path(layer: QgsRasterLayer) -> str:
    """Return a filesystem path GDAL can open for a raster layer.

    Args:
        layer: The raster layer chosen for the algorithm.

    Returns:
        The layer's data source path.

    Raises:
        QgsProcessingException: If the layer has no readable file source
            (e.g. a WMS/remote layer, which the core cannot open).
    """
    if layer is None:
        raise QgsProcessingException("A raster layer is required.")
    source = str(layer.source()).split("|", 1)[0]
    if not source or not Path(source).exists():
        raise QgsProcessingException(
            f"Raster layer '{layer.name()}' has no local file source that GDAL "
            "can read; export it to a GeoTIFF first."
        )
    return source


def export_raster(source_tif: str, destination: str, feedback: Any = None) -> str:
    """Mirror a GeoTIFF the core wrote to a Processing raster destination.

    The compute core always writes GeoTIFFs into a working folder. Processing
    raster destinations, by contrast, may be a temporary file or a path the
    user named in another format. This copies the core's file to the
    destination when the extensions match, or translates it with GDAL when
    they differ (so a user who named e.g. ``result.img`` still gets that
    format), and returns the resulting path. When the destination is empty or
    already resolves to the same file, the source path is returned unchanged.

    Args:
        source_tif: Path to the GeoTIFF the core produced.
        destination: The resolved Processing raster-destination path (may be
            empty).
        feedback: Optional Processing feedback for a progress line.

    Returns:
        The path the raster now lives at (the destination, or the source when
        no export was needed).
    """
    if not destination:
        return source_tif
    src = Path(source_tif)
    dst = Path(destination)
    if src.resolve() == dst.resolve():
        return source_tif
    dst.parent.mkdir(parents=True, exist_ok=True)
    if src.suffix.lower() == dst.suffix.lower():
        shutil.copy2(src, dst)
    else:
        from osgeo import gdal

        gdal.UseExceptions()
        gdal.Translate(str(dst), str(src))
    if feedback is not None:
        feedback.pushInfo(f"  exported raster: {dst}")
    return str(dst)


def crs_to_epsg_string(crs: QgsCoordinateReferenceSystem) -> str | None:
    """Return a CRS as an ``"EPSG:NNNN"`` string, or ``None`` if unknown.

    Args:
        crs: A QGIS coordinate reference system.

    Returns:
        The authid when it is an EPSG code, else ``None``.
    """
    if crs is None or not crs.isValid():
        return None
    authid = crs.authid()  # e.g. "EPSG:4283"
    return authid if authid.upper().startswith("EPSG:") else None


def _epsg_urn(epsg: str) -> dict[str, Any]:
    """Build the non-standard GeoJSON named-CRS member for an EPSG code."""
    code = "".join(ch for ch in epsg.rsplit(":", 1)[-1] if ch.isdigit())
    return {"type": "name", "properties": {"name": f"urn:ogc:def:crs:EPSG::{code}"}}


def vector_source_to_geojson(source: Any, out_path: Path) -> str:
    """Write a Processing feature source to a GeoJSON FeatureCollection.

    The geometries are written verbatim (no reprojection) and the source
    CRS is recorded in the non-standard named ``crs`` member the core
    understands, so no coordinate meaning is lost.

    Args:
        source: A ``QgsProcessingFeatureSource`` (or ``QgsVectorLayer``).
        out_path: Destination ``.geojson`` path.

    Returns:
        The written path as a string.

    Raises:
        QgsProcessingException: If the source is empty or has no geometry.
    """
    if source is None:
        raise QgsProcessingException("A vector source is required.")

    fields = [f.name() for f in source.fields()]
    features_json: list[dict[str, Any]] = []
    for feature in source.getFeatures():
        geometry = feature.geometry()
        if geometry is None or geometry.isEmpty():
            continue
        geom_obj = json.loads(geometry.asJson())
        attrs = feature.attributes()
        props = {
            name: _json_safe(attrs[i])
            for i, name in enumerate(fields)
            if i < len(attrs)
        }
        features_json.append(
            {"type": "Feature", "geometry": geom_obj, "properties": props}
        )

    if not features_json:
        raise QgsProcessingException(
            "The vector source contains no features with geometry."
        )

    doc: dict[str, Any] = {"type": "FeatureCollection", "features": features_json}
    epsg = crs_to_epsg_string(source.sourceCrs())
    if epsg and epsg.upper() != "EPSG:4326":
        doc["crs"] = _epsg_urn(epsg)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as handle:
        json.dump(doc, handle)
    return str(out_path)


def _json_safe(value: Any) -> Any:
    """Coerce a QGIS attribute value into a JSON-serialisable form."""
    # QGIS NULL is a PyQt QVariant; represent it (and anything exotic) sensibly.
    if value is None:
        return None
    if isinstance(value, (str, int, float, bool)):
        return value
    try:
        # QDate/QDateTime and similar expose isoformat-like via str().
        return str(value)
    except Exception:  # pragma: no cover - defensive
        return None


def deliver_geojson_to_sink(
    algorithm: Any,
    parameters: dict,
    output_name: str,
    geojson_path: str,
    context: Any,
) -> str:
    """Stream a GeoJSON file the core wrote into a Processing feature sink.

    Loading the core's GeoJSON output and copying it into the sink the
    user chose lets them pick any output format (GeoPackage, shapefile,
    temporary layer) while the core keeps writing only GeoJSON.

    Args:
        algorithm: The calling ``QgsProcessingAlgorithm`` (for
            ``parameterAsSink``/``invalidSinkError``).
        parameters: The algorithm's parameter mapping.
        output_name: Name of the sink output parameter.
        geojson_path: Path to the GeoJSON file produced by the core.
        context: The Processing context.

    Returns:
        The destination layer identifier for the sink output.

    Raises:
        QgsProcessingException: If the GeoJSON cannot be read or the sink
            cannot be created.
    """
    layer = QgsVectorLayer(str(geojson_path), "result", "ogr")
    if not layer.isValid():
        raise QgsProcessingException(
            f"Could not read the result written to {geojson_path}."
        )
    sink, dest_id = algorithm.parameterAsSink(
        parameters,
        output_name,
        context,
        layer.fields(),
        layer.wkbType(),
        layer.sourceCrs(),
    )
    if sink is None:
        raise QgsProcessingException(
            algorithm.invalidSinkError(parameters, output_name)
        )
    for feature in layer.getFeatures():
        sink.addFeature(feature, QgsFeatureSink.FastInsert)
    return str(dest_id)


def load_geojson_layer(path: str, name: str, context: Any) -> QgsVectorLayer:
    """Load a GeoJSON file as a temporary QGIS vector layer.

    Args:
        path: Path to the GeoJSON file to load.
        name: Display name for the resulting layer.
        context: The Processing context (used to register temp layers).

    Returns:
        The loaded vector layer.

    Raises:
        QgsProcessingException: If the layer fails to load.
    """
    layer = QgsVectorLayer(path, name, "ogr")
    if not layer.isValid():
        raise QgsProcessingException(f"Could not load result layer from {path}.")
    if context is not None:
        context.temporaryLayerStore().addMapLayer(layer)
    return layer

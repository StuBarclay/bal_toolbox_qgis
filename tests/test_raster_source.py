"""Tests for the qgis-free raster-source resolution policy.

These exercise :mod:`bal_toolbox_qgis.algorithms._raster_source`, which holds
the branch logic behind ``raster_source_path`` with no ``qgis``/``osgeo``
dependency. The QGIS-specific probes (does a path exist, can GDAL wrap a
descriptor in a VRT, can QGIS materialise the layer) are injected as callables,
so the three-tier decision runs in the plain sandbox and in CI. The live GDAL
VRT wrapping and QGIS materialisation are exercised only in a real QGIS run
(``tests/test_qgis_glue.py``).

The regression this guards: a GDAL descriptor (a File Geodatabase raster, a
NetCDF subdataset, a ``/vsicurl/`` path, ...) is not a plain file on disk, so
the compute core's ``path.exists()`` check rejects it with "Raster not found".
The policy must therefore return a *real, on-disk* path (a VRT wrapping the
descriptor), never the bare descriptor.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET

from bal_toolbox_qgis.algorithms._raster_source import (
    build_vrt_xml,
    resolve_raster_source,
    source_candidates,
)

_GDB_DESCRIPTOR = (
    r'OpenFileGDB:"C:\data\NVIS_V7_0_AUST_EXT.gdb":NVIS7_0_AUST_EXT_MVG_ALB'
)


def test_plain_existing_file_is_returned_unchanged() -> None:
    """A candidate that exists on disk wins and is returned verbatim."""
    calls: list[str] = []

    def wrap(_source: str) -> str | None:  # should never run
        calls.append("wrap")
        return None

    def materialise() -> str:  # should never run
        calls.append("materialise")
        return "MATERIALISED"

    result = resolve_raster_source(
        ["/data/veg.tif", "/data/veg.tif|layername=veg"],
        exists=lambda c: c == "/data/veg.tif",
        wrap_descriptor=wrap,
        materialise=materialise,
    )

    assert result == "/data/veg.tif"
    assert calls == []  # neither fallback was consulted


def test_gdal_descriptor_is_wrapped_in_a_vrt_not_returned_raw() -> None:
    """A .gdb descriptor is wrapped in a VRT whose path exists on disk.

    This is the core regression: returning the bare descriptor would fail the
    compute core's ``path.exists()`` guard. The resolved path must be the VRT.
    """
    on_disk = {"/tmp/baltoolbox_vrt_x/source.vrt"}
    wrapped: list[str] = []

    def wrap(source: str) -> str | None:
        wrapped.append(source)
        return "/tmp/baltoolbox_vrt_x/source.vrt"

    def materialise() -> str:  # should never run for an openable descriptor
        raise AssertionError("materialise must not be called for a descriptor")

    result = resolve_raster_source(
        [_GDB_DESCRIPTOR],
        exists=lambda c: c in on_disk,
        wrap_descriptor=wrap,
        materialise=materialise,
    )

    assert result == "/tmp/baltoolbox_vrt_x/source.vrt"
    # The descriptor itself must not be returned, and it must be what we wrapped.
    assert result != _GDB_DESCRIPTOR
    assert wrapped == [_GDB_DESCRIPTOR]
    # The property the compute core requires: the resolved path exists on disk.
    assert (lambda c: c in on_disk)(result)


def test_falls_back_to_materialise_when_nothing_opens() -> None:
    """A layer with no file and no GDAL-openable source is materialised."""

    def wrap(_source: str) -> str | None:
        return None  # GDAL cannot open any candidate

    result = resolve_raster_source(
        ["memory://layer", "otherexotic://layer"],
        exists=lambda _c: False,
        wrap_descriptor=wrap,
        materialise=lambda: "/tmp/exported.tif",
    )

    assert result == "/tmp/exported.tif"


def test_first_openable_descriptor_wins_over_later_candidates() -> None:
    """Wrapping stops at the first candidate GDAL can open."""
    seen: list[str] = []

    def wrap(source: str) -> str | None:
        seen.append(source)
        return "/tmp/first.vrt" if source == "GOOD" else None

    result = resolve_raster_source(
        ["BAD", "GOOD", "ALSO_GOOD"],
        exists=lambda _c: False,
        wrap_descriptor=wrap,
        materialise=lambda: "MATERIALISED",
    )

    assert result == "/tmp/first.vrt"
    assert seen == ["BAD", "GOOD"]  # ALSO_GOOD never reached


def test_source_candidates_splits_pipe_and_dedupes() -> None:
    """Candidate order is pre-pipe source, raw source, then the URI, no dupes."""
    candidates = source_candidates(
        "/data/veg.tif|layername=veg", "/data/veg.tif|layername=veg"
    )
    assert candidates == ["/data/veg.tif", "/data/veg.tif|layername=veg"]


def test_source_candidates_keeps_descriptor_and_distinct_uri() -> None:
    """A descriptor with no pipe is kept once; a distinct URI is appended."""
    candidates = source_candidates(_GDB_DESCRIPTOR, "gdal:///some/other/uri")
    assert candidates == [_GDB_DESCRIPTOR, "gdal:///some/other/uri"]


def test_source_candidates_drops_empty_entries() -> None:
    """An empty data-source URI does not add a blank candidate."""
    assert source_candidates("/data/veg.tif", "") == ["/data/veg.tif"]


def test_build_vrt_xml_is_well_formed_and_carries_geometry() -> None:
    """The assembled VRT parses as XML and bakes in size, SRS and transform."""
    xml = build_vrt_xml(
        width=1000,
        height=800,
        source="/data/veg.tif",
        bands=[("Byte", 255.0)],
        srs_wkt='PROJCS["fake",AUTHORITY["EPSG","3577"]]',
        geotransform=[-100.0, 25.0, 0.0, 200.0, 0.0, -25.0],
    )
    assert xml.startswith("<VRTDataset")

    root = ET.fromstring(xml)  # raises if the document is not well-formed
    assert root.tag == "VRTDataset"
    assert root.get("rasterXSize") == "1000"
    assert root.get("rasterYSize") == "800"
    assert root.findall(".//SRS")
    assert root.findall(".//GeoTransform")


def test_build_vrt_xml_escapes_descriptor_and_round_trips() -> None:
    """A .gdb descriptor's quotes survive as an exact SourceFilename value.

    This is the property that broke the 0.1.9 ``gdal.Translate`` VRT: the
    written file must reference the descriptor verbatim (so GDAL reopens the
    File Geodatabase raster) and must be a well-formed document that starts
    with ``<VRTDataset`` -- both asserted here without needing GDAL.
    """
    xml = build_vrt_xml(
        width=4,
        height=4,
        source=_GDB_DESCRIPTOR,
        bands=[("Int16", None)],
    )
    # Quotes are XML-escaped in the raw text so the document stays well-formed.
    assert "&quot;" in xml

    root = ET.fromstring(xml)
    source_filenames = root.findall(".//SourceFilename")
    assert len(source_filenames) == 1
    element = source_filenames[0]
    # relativeToVRT="0" => GDAL passes the descriptor straight to GDALOpen.
    assert element.get("relativeToVRT") == "0"
    # The parsed (un-escaped) text is exactly the descriptor we started with.
    assert element.text == _GDB_DESCRIPTOR


def test_build_vrt_xml_emits_one_band_each_with_optional_nodata() -> None:
    """Every source band gets a VRTRasterBand; NoDataValue appears only if set."""
    xml = build_vrt_xml(
        width=4,
        height=4,
        source="/data/multi.tif",
        bands=[("Float32", -9999.0), ("Float32", None)],
    )
    root = ET.fromstring(xml)
    raster_bands = root.findall(".//VRTRasterBand")
    assert len(raster_bands) == 2
    assert [b.get("band") for b in raster_bands] == ["1", "2"]
    assert [b.get("dataType") for b in raster_bands] == ["Float32", "Float32"]
    # Only the first band declared a nodata value.
    assert len(root.findall(".//NoDataValue")) == 1


def test_build_vrt_xml_omits_srs_and_transform_when_absent() -> None:
    """A source with no projection or geotransform yields neither element."""
    xml = build_vrt_xml(
        width=4,
        height=4,
        source="/data/plain.tif",
        bands=[("Byte", None)],
    )
    root = ET.fromstring(xml)
    assert not root.findall(".//SRS")
    assert not root.findall(".//GeoTransform")

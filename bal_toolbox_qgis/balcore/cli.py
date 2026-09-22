"""Command-line interface for the AS 3959:2018 BAL toolbox.

Three subcommands are provided. ``run`` computes BAL from a YAML
configuration (see :mod:`bal_toolbox_qgis.balcore.config`); ``assign-buildings``
assigns the resulting BAL ratings to polygon footprints (see
:mod:`bal_toolbox_qgis.balcore.zonal`); ``analyse-history`` annotates footprints with
past-fire and fuel-recovery context (see
:mod:`bal_toolbox_qgis.balcore.history_overlay`) without ever altering a BAL rating.
Examples::

    python -m bal_toolbox_qgis.balcore run --config config.yaml
    python -m bal_toolbox_qgis.balcore assign-buildings --bal bal_max.tif \
        --polygons buildings.geojson --output buildings_bal.geojson
    python -m bal_toolbox_qgis.balcore analyse-history --config config.yaml

"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from bal_toolbox_qgis.balcore import __version__
from bal_toolbox_qgis.balcore.config import load_config
from bal_toolbox_qgis.balcore.workflow import run as run_workflow
from bal_toolbox_qgis.balcore.zonal import assign_bal_to_polygons

logger = logging.getLogger("bal_toolbox_qgis.balcore")


def build_parser() -> argparse.ArgumentParser:
    """Build the top-level argument parser.

    Returns:
        Configured :class:`argparse.ArgumentParser` with the ``run``,
        ``assign-buildings`` and ``analyse-history`` subcommands.
    """
    parser = argparse.ArgumentParser(
        prog="bal_toolbox_qgis.balcore",
        description=(
            "Compute AS 3959:2018 bushfire attack levels from a DEM and "
            "vegetation raster, configured by a YAML file."
        ),
    )
    parser.add_argument(
        "--version", action="version", version=f"%(prog)s {__version__}"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser(
        "run", help="Run a BAL calculation from a YAML configuration."
    )
    run_parser.add_argument(
        "-c",
        "--config",
        type=Path,
        required=True,
        help="Path to the YAML run configuration file.",
    )
    run_parser.add_argument(
        "-f",
        "--overwrite",
        action="store_true",
        help="Overwrite existing output rasters instead of stopping.",
    )
    run_parser.add_argument(
        "--write-inputs",
        action="store_true",
        help=(
            "Also write the aligned input rasters (DEM, raw NVIS MVG and "
            "reclassified AS 3959 vegetation class) for QA."
        ),
    )
    run_parser.add_argument(
        "--timestamp",
        action="store_true",
        help="Append a UTC timestamp suffix to every output filename.",
    )
    run_parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Enable verbose (DEBUG) logging.",
    )

    assign_parser = subparsers.add_parser(
        "assign-buildings",
        help="Assign BAL ratings from a raster to polygons (buildings, parcels).",
    )
    assign_parser.add_argument(
        "-c",
        "--config",
        type=Path,
        help=(
            "Read the BAL raster (output_dir/bal_max.tif) and the building / "
            "cadastre layers from a run configuration's 'assign:' block. "
            "Either use this, or give --bal/--polygons/--output explicitly."
        ),
    )
    assign_parser.add_argument(
        "-b", "--bal", type=Path, help="Path to a BAL GeoTIFF (explicit mode)."
    )
    assign_parser.add_argument(
        "--polygons",
        "--buildings",
        dest="polygons",
        type=Path,
        help=(
            "Path to a GeoJSON FeatureCollection of polygons (explicit mode; "
            "'--buildings' is an alias)."
        ),
    )
    assign_parser.add_argument(
        "-o",
        "--output",
        type=Path,
        help="Path to write the annotated GeoJSON (explicit mode).",
    )
    assign_parser.add_argument(
        "-f",
        "--overwrite",
        action="store_true",
        help="Overwrite the output file if it exists.",
    )
    assign_parser.add_argument(
        "--timestamp",
        action="store_true",
        help="Append a UTC timestamp suffix to the output filename.",
    )
    assign_parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Enable verbose (DEBUG) logging.",
    )

    history_parser = subparsers.add_parser(
        "analyse-history",
        help=(
            "Annotate footprints with past-fire and fuel-recovery context "
            "(analysis only; never changes the BAL rating)."
        ),
    )
    history_parser.add_argument(
        "-c",
        "--config",
        type=Path,
        required=True,
        help=(
            "Run configuration with a 'history:' block. The BAL raster "
            "(output_dir/bal_max.tif) and footprints are read from it."
        ),
    )
    history_parser.add_argument(
        "-b",
        "--bal",
        type=Path,
        help=(
            "Path to the BAL GeoTIFF to read, overriding output_dir/bal_max.tif. "
            "Required when the run used --timestamp (no stable bal_max.tif exists)."
        ),
    )
    history_parser.add_argument(
        "-f",
        "--overwrite",
        action="store_true",
        help="Overwrite the output file if it exists.",
    )
    history_parser.add_argument(
        "--timestamp",
        action="store_true",
        help="Append a UTC timestamp suffix to the output filename.",
    )
    history_parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Enable verbose (DEBUG) logging.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """Entry point for the ``bal_toolbox_qgis.balcore`` command.

    Args:
        argv: Optional argument list (defaults to ``sys.argv``).

    Returns:
        Process exit code: ``0`` on success, ``1`` on a handled error.
    """
    parser = build_parser()
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
    )

    if args.command == "assign-buildings":
        return _run_assign(args)
    if args.command == "analyse-history":
        return _run_history(args)
    return _run_calculation(args)


def _run_calculation(args: argparse.Namespace) -> int:
    """Handle the ``run`` subcommand."""
    try:
        config = load_config(args.config)
        outputs = run_workflow(
            config,
            overwrite=args.overwrite,
            write_inputs=args.write_inputs,
            timestamp=args.timestamp,
        )
    except FileExistsError as error:
        logger.error("%s", error)
        return 1
    except (FileNotFoundError, ValueError, KeyError, TypeError) as error:
        logger.error("%s", error)
        return 1

    logger.info(
        "Done. Wrote %d raster(s): %s",
        len(outputs),
        ", ".join(str(p) for p in outputs.values()),
    )
    return 0


def _timestamped(path: Path, timestamp: bool) -> Path:
    """Return ``path`` with a UTC timestamp suffix when requested."""
    if not timestamp:
        return path
    from bal_toolbox_qgis.balcore.workflow import output_timestamp

    return path.with_name(f"{path.stem}_{output_timestamp()}{path.suffix}")


def _run_assign(args: argparse.Namespace) -> int:
    """Handle the ``assign-buildings`` subcommand (config or explicit mode)."""
    try:
        if args.config is not None:
            return _assign_from_config(args)
        return _assign_explicit(args)
    except FileExistsError as error:
        logger.error("%s", error)
        return 1
    except (FileNotFoundError, ValueError, KeyError, TypeError) as error:
        logger.error("%s", error)
        return 1


def _assign_explicit(args: argparse.Namespace) -> int:
    """Assign using explicit --bal/--polygons/--output arguments."""
    if not (args.bal and args.polygons and args.output):
        raise ValueError(
            "Without --config, all of --bal, --polygons and --output are required."
        )
    output = _timestamped(args.output, args.timestamp)
    count = assign_bal_to_polygons(
        args.bal,
        args.polygons,
        output,
        overwrite=args.overwrite,
        output_crs="EPSG:4283",
    )
    logger.info("Done. Annotated %d footprint(s): %s", count, output)
    return 0


def _assign_from_config(args: argparse.Namespace) -> int:
    """Assign building/cadastre layers from a run configuration."""
    config = load_config(args.config)
    if config.assign is None:
        raise ValueError(
            f"Configuration {args.config} has no 'assign:' block to drive "
            "building/cadastre assignment."
        )
    if not config.assign.enabled:
        logger.info("Assignment is disabled (assign.enabled: false); nothing to do.")
        return 0

    # The BAL raster: --bal override, else output_dir/bal_max.tif.
    bal = args.bal if args.bal else config.output_dir / "bal_max.tif"

    layers: list[tuple[str, Path]] = []
    if config.assign.buildings_path is not None:
        layers.append(("buildings", config.assign.buildings_path))
    if config.assign.cadastre_path is not None:
        layers.append(("cadastre", config.assign.cadastre_path))

    for name, polygons in layers:
        out = _timestamped(config.output_dir / f"{name}_bal.geojson", args.timestamp)
        count = assign_bal_to_polygons(
            bal,
            polygons,
            out,
            overwrite=args.overwrite,
            output_crs=config.output_crs,
        )
        logger.info("Annotated %d %s footprint(s): %s", count, name, out)
    return 0


def _run_history(args: argparse.Namespace) -> int:
    """Handle the ``analyse-history`` subcommand (config-driven)."""
    from bal_toolbox_qgis.balcore.history_overlay import analyse_history, format_summary

    try:
        config = load_config(args.config)
        if config.history is None:
            raise ValueError(
                f"Configuration {args.config} has no 'history:' block to drive "
                "the fire-history analysis."
            )
        spec = config.history
        if not spec.enabled:
            logger.info("History analysis is disabled (enabled: false); nothing to do.")
            return 0

        footprints = spec.footprints_path
        if footprints is None and config.assign is not None:
            footprints = config.assign.buildings_path
        if footprints is None:
            raise ValueError(
                "No footprints for history analysis: set 'history.footprints' "
                "or an 'assign.buildings' layer in the configuration."
            )

        bal = args.bal if args.bal else config.output_dir / "bal_max.tif"
        if not bal.exists():
            raise FileNotFoundError(
                f"BAL raster not found: {bal}. Run 'run' first, or pass --bal "
                "explicitly (required when the run used --timestamp, which "
                "writes a timestamped name instead of bal_max.tif)."
            )
        out = _timestamped(
            config.output_dir / "footprints_history.geojson", args.timestamp
        )
        summary = analyse_history(
            bal_raster=bal,
            footprints=footprints,
            fire_layer=spec.fire_path,
            output=out,
            year_field=spec.year_field,
            analysis_year=spec.analysis_year,
            veg_class_raster=spec.veg_class_path,
            overwrite=args.overwrite,
            output_crs=config.output_crs,
        )
    except FileExistsError as error:
        logger.error("%s", error)
        return 1
    except (FileNotFoundError, ValueError, KeyError, TypeError) as error:
        logger.error("%s", error)
        return 1

    logger.info("Wrote %s", out)
    logger.info("BAL band x time-since-fire summary:\n%s", format_summary(summary))
    return 0


if __name__ == "__main__":
    sys.exit(main())

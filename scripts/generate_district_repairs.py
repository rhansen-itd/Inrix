#!/usr/bin/env python3
"""Generate link repair CSVs for all ITD districts (ROADMAP Item 45.5).

Uses corridors.repair_links() to analyze topology breaks in the XD network
per district and write repair CSVs matching the d3_link_repairs.csv format.

Usage:
    python scripts/generate_district_repairs.py
    python scripts/generate_district_repairs.py --districts 1 2 4
    python scripts/generate_district_repairs.py --districts 5 --out-dir scripts/
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import geopandas as gpd
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from inrix_tools import corridors, geometry
from inrix_tools.couplets import DISTRICT_COUNTIES


def generate_repairs(
    network_source: str | Path,
    district: int,
    out_dir: Path,
    *,
    cache_dir: Path | None = None,
    full_net: gpd.GeoDataFrame | None = None,
    force: bool = False,
) -> Path:
    """Generate link repairs for a single district."""
    out_path = out_dir / f"d{district}_link_repairs.csv"
    if out_path.exists() and not force:
        print(f"District {district} repairs already exist at {out_path} (skipping)")
        return out_path

    cache_path = (cache_dir / f"d{district}_network.geoparquet") if cache_dir else None

    # Load district network
    if cache_path and cache_path.exists():
        print(f"Loading cached district network from {cache_path}...")
        district_net = gpd.read_parquet(cache_path)
    else:
        if full_net is None:
            print(f"Loading network from {network_source}...")
            full_net = geometry.load_xd_network(network_source)

        counties = DISTRICT_COUNTIES.get(district, [])
        print(f"Filtering to District {district} counties ({len(counties)} counties)...")
        district_net = full_net[full_net["County"].isin(counties)].copy()

        if cache_path:
            print(f"Caching district network to {cache_path}...")
            district_net.to_parquet(cache_path)

    print(f"Deriving link repairs across {len(district_net):,} segments...")
    repairs_df = corridors.repair_links(district_net)
    attrs = repairs_df.attrs

    header_lines = [
        f"# ITD District {district} NextXDSegI repair table - derived by corridors.repair_links",
        f"# district: {district}",
        f"# radius_m: {attrs.get('radius_m', 25.0)}",
        f"# max_bearing_delta_deg: {attrs.get('max_bearing_delta_deg', 90.0)}",
        f"# require_same_bearing: {attrs.get('require_same_bearing', True)}",
        f"# bearing_probe_m: {attrs.get('bearing_probe_m', 30.0)}",
        f"# n_segments: {attrs.get('n_segments', len(district_net))}",
        f"# n_null_next: {attrs.get('n_null_next', 0)}",
        f"# n_fill: {attrs.get('n_fill', 0)}",
        f"# n_override: {attrs.get('n_override', 0)}",
        f"# ambiguous_fill: {attrs.get('ambiguous_fill', 0)}",
        f"# ambiguous_override: {attrs.get('ambiguous_override', 0)}",
        f"# skipped_off_subset: {attrs.get('skipped_off_subset', 0)}",
        f"# crs: {attrs.get('crs', '')}",
    ]

    out_dir.mkdir(parents=True, exist_ok=True)
    with out_path.open("w") as fh:
        fh.write("\n".join(header_lines) + "\n")
        repairs_df.to_csv(fh, index=False)

    print(f"Written {len(repairs_df):,} repairs to {out_path} "
          f"(fill: {attrs.get('n_fill', 0)}, override: {attrs.get('n_override', 0)})")
    return out_path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--network", default="USA_Idaho_shapefile.zip")
    parser.add_argument("--districts", nargs="*", type=int, default=[1, 2, 3, 4, 5, 6])
    parser.add_argument("--out-dir", default="scripts")
    parser.add_argument("--cache-dir", default="geometry_cache")
    parser.add_argument("--force", action="store_true", help="Overwrite existing repairs files")
    args = parser.parse_args()

    out_dir = Path(args.out_dir)
    cache_dir = Path(args.cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)

    # Preload full network once if multiple districts need processing
    full_net = None
    districts_to_run = [
        d for d in args.districts
        if args.force or not (out_dir / f"d{d}_link_repairs.csv").exists()
    ]

    if districts_to_run:
        needs_full_load = any(
            not (cache_dir / f"d{d}_network.geoparquet").exists()
            for d in districts_to_run
        )
        if needs_full_load:
            print(f"Preloading full XD network from {args.network}...")
            full_net = geometry.load_xd_network(args.network)

    for d in args.districts:
        print(f"\n=== District {d} ===")
        generate_repairs(
            args.network, d, out_dir,
            cache_dir=cache_dir,
            full_net=full_net,
            force=args.force,
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

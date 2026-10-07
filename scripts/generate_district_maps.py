#!/usr/bin/env python3
"""Regenerate interactive HTML maps for ITD districts from saved screening outputs.

Reads the saved screening parquets and totals CSVs from:
    out/statewide_screening/d{1..6}/
and renders the interactive HTML vector maps without needing DuckDB.

Usage:
    python scripts/generate_district_maps.py
    python scripts/generate_district_maps.py --districts 1 2 3
    python scripts/generate_district_maps.py --metric vhd
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import geopandas as gpd
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from inrix_tools import corridors, screen  # noqa: E402
from scripts.run_district_screening import (  # noqa: E402
    DEFAULT_VHD_RATE,
    VHD_RATES,
    _map_viewer_html,
    attach_direction_totals,
    generate_maps,
    vhd_unit,
)

SCENARIO_REGISTRY = "screening_scenarios.json"


def resolve_scenario_windows(sc_entry: dict):
    """Extract PeakWindow objects from a screening_scenarios.json scenario entry."""
    sc = sc_entry.get("scenario", {})
    wins_dict = sc.get("windows", {})
    if not wins_dict:
        return ["am", "pm"]
    windows = []
    for k, v in wins_dict.items():
        if isinstance(v, dict):
            windows.append(screen.PeakWindow(**v))
        elif isinstance(v, screen.PeakWindow):
            windows.append(v)
        else:
            windows.append(k)
    return windows


def generate_district_maps(
    district: int,
    base_dir: Path,
    scenarios: list[str] | None = None,
    metric: str = "all",
    catalogue_override: str | None = None,
    per_hour: bool = False,
):
    dist_dir = base_dir / f"d{district}"
    if not dist_dir.exists():
        print(f"Skipping District {district}: {dist_dir} does not exist")
        return

    net_cache = Path(f"geometry_cache/d{district}_network.geoparquet")
    if not net_cache.exists():
        print(f"Skipping District {district}: network cache {net_cache} not found")
        return
    net = gpd.read_parquet(net_cache)

    cat_path = Path(catalogue_override or f"scripts/d{district}_corridors.json")
    if not cat_path.exists():
        print(f"Skipping District {district}: catalogue {cat_path} not found")
        return
    cat_entries = corridors.load_catalogue(cat_path)

    repairs_path = Path(f"scripts/d{district}_link_repairs.csv")
    repairs = corridors.load_link_repairs(repairs_path) if repairs_path.exists() else None
    res = corridors.resolve_catalogue(net, cat_entries, repairs=repairs)
    chains = res.attrs["chains"]

    reg_path = dist_dir / SCENARIO_REGISTRY
    if not reg_path.exists():
        print(f"Skipping District {district}: {reg_path} not found")
        return
    reg = json.loads(reg_path.read_text())

    target_scenarios = scenarios or list(reg.keys())
    print(f"\n--- Generating District {district} Maps ({len(target_scenarios)} scenarios) ---")

    for tag in target_scenarios:
        if tag not in reg:
            print(f"  Warning: scenario {tag} not in registry for D{district}")
            continue

        sc_info = reg[tag]
        label = sc_info.get("label", tag)
        files = sc_info.get("files", {})
        windows = resolve_scenario_windows(sc_info)

        scr_fn = files.get("segment_screen")
        vhd_fn = files.get("segment_vhd")
        totals_fn = files.get("totals")
        breakout_fn = files.get("breakout")
        tti_map_fn = files.get("map")
        vhd_map_fn = files.get("map_vhd")

        if not scr_fn or not (dist_dir / scr_fn).exists():
            print(f"  [{tag}] Missing segment screen parquet: {scr_fn}")
            continue
        if not totals_fn or not (dist_dir / totals_fn).exists():
            print(f"  [{tag}] Missing totals CSV: {totals_fn}")
            continue

        scr = pd.read_parquet(dist_dir / scr_fn)
        seg_vhd = pd.read_parquet(dist_dir / vhd_fn) if (vhd_fn and (dist_dir / vhd_fn).exists()) else None
        totals = pd.read_csv(dist_dir / totals_fn, comment="#")

        breakout = None
        if breakout_fn and (dist_dir / breakout_fn).exists():
            breakout = pd.read_csv(dist_dir / breakout_fn, comment="#", index_col=[0, 1])

        corridor_ranks = attach_direction_totals(
            totals.set_index("corridor_group").to_dict("index"), breakout
        )

        dist_label = f"District {district}"
        delay_label = f"Total delay, {label}"

        # Render TTI map
        if metric in ("all", "tti") and tti_map_fn:
            print(f"  Rendering {dist_label} [{tag}] TTI Map -> {tti_map_fn}...")
            generate_maps(
                dist_dir,
                scr,
                net,
                cat_entries,
                chains,
                corridor_ranks,
                windows=windows,
                window_label=label,
                title_prefix=f"ITD {dist_label}",
                delay_label=delay_label,
                map_filename=tti_map_fn,
                metric="tti",
                per_hour=per_hour,
            )

        # Render VHD map
        if metric in ("all", "vhd") and vhd_map_fn and seg_vhd is not None:
            print(f"  Rendering {dist_label} [{tag}] {vhd_unit(per_hour)} Map "
                  f"-> {vhd_map_fn}...")
            generate_maps(
                dist_dir,
                scr,
                net,
                cat_entries,
                chains,
                corridor_ranks,
                windows=windows,
                window_label=label,
                title_prefix=f"ITD {dist_label}",
                delay_label=delay_label,
                map_filename=vhd_map_fn,
                metric="vhd_per_mile",
                segment_vhd=seg_vhd,
                per_hour=per_hour,
            )

    # Re-generate map_viewer.html
    available_maps = []
    for info in reg.values():
        for key, kind in (("map", "TTI"), ("map_vhd", vhd_unit(per_hour))):
            fn = info.get("files", {}).get(key)
            if fn and (dist_dir / fn).exists():
                available_maps.append((f"{info['label']} ({kind})", fn))

    if len(available_maps) > 1:
        viewer_path = dist_dir / "map_viewer.html"
        viewer_path.write_text(_map_viewer_html(available_maps), encoding="utf-8")
        print(f"  Updated map viewer -> {viewer_path} ({len(available_maps)} maps)")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-dir", default="out/statewide_screening",
                        help="Base directory containing d{1..6} screening outputs")
    parser.add_argument("--districts", nargs="*", type=int, default=[1, 2, 3, 4, 5, 6],
                        help="Districts to regenerate (default: 1-6)")
    parser.add_argument("--scenarios", nargs="*", default=None,
                        help="Specific scenarios to regenerate (default: all registered)")
    parser.add_argument("--metric", choices=["all", "vhd", "tti"], default="all",
                        help="Map metrics to render: 'all', 'vhd', or 'tti' (default: all)")
    parser.add_argument("--catalogue-override", action="append", default=[], metavar="D=PATH",
                        help="District D's catalogue is PATH (repeatable)")
    parser.add_argument("--vhd-rate", choices=VHD_RATES, default=DEFAULT_VHD_RATE,
                        help="delay density as VHD/mi per window hour (per-hour) or "
                             "VHD/mi (per-mile)")
    args = parser.parse_args()

    overrides = {}
    for v in args.catalogue_override:
        d, _, path = v.partition("=")
        overrides[int(d)] = path

    base_dir = Path(args.base_dir)
    for d in args.districts:
        generate_district_maps(
            district=d,
            base_dir=base_dir,
            scenarios=args.scenarios,
            metric=args.metric,
            catalogue_override=overrides.get(d),
            per_hour=args.vhd_rate == "per-hour",
        )

    print("\nAll district maps regenerated successfully!")


if __name__ == "__main__":
    main()

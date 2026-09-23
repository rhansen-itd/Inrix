#!/usr/bin/env python3
"""Generate standalone interactive HTML map visualizations of District 3 screening.

This is the multi-map workflow: it runs two separate screens (typical weekday peak
and 7-day all-day) and produces a tabbed viewer that switches between them. For a
single map alongside a ranking run, use ``run_district_screening.py --maps`` instead.

Produces:
1. out/district_screening/d3_typical_peak_map.html:
   All segments colored by worst peak TTI (AM/PM weekdays), with ranked corridors
   overlaid and their start/end termini delineated.
2. out/district_screening/d3_vhd_per_mile_map.html:
   All segments colored by peak delay density (VHD / Mile), volume-weighted to
   directly reflect corridor screening rankings.
3. out/district_screening/d3_7day_all_day_map.html:
   All segments colored by 7-day all-day TTI (6 AM - 9 PM, 7 days/week), with
   ranked corridors and start/end termini.
4. out/district_screening/d3_7day_vhd_per_mile_map.html:
   All segments colored by 7-day all-day delay density (VHD / Mile).
5. out/district_screening/map_viewer.html:
   A clean, self-contained tabbed browser interface to toggle between all four maps.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import geopandas as gpd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from inrix_tools import corridors, screen, store             # noqa: E402

# Re-use the map generation functions from the pipeline script.
from run_district_screening import (                                   # noqa: E402
    _map_viewer_html,
    corridor_geometry,
    generate_maps,
    join_volumes,
    BBOX_MARGIN_DEG,
)

OUT_DIR = Path("out/district_screening")
DB_PATH = "d3_store.duckdb"
NET_CACHE = "geometry_cache/d3_network.geoparquet"
CAT_PATH = "scripts/d3_corridors.json"
REPAIRS_PATH = "scripts/d3_link_repairs.csv"
AADT_ZIP = "Cumulative_AADT.zip"
PEAK_TOTALS_CSV = OUT_DIR / "corridor_peak_totals.csv"


def main():
    t0 = time.time()
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    print("Connecting to DuckDB store and loading network geometry...")
    con = store.connect(DB_PATH)
    area_key = store.list_areas(con).iloc[0]["area_key"]
    net = gpd.read_parquet(NET_CACHE)
    repairs = corridors.load_link_repairs(REPAIRS_PATH)
    cat_entries = corridors.load_catalogue(CAT_PATH)
    groups = corridors.load_reporting_corridors(CAT_PATH)
    gnames = {g.id: g.name for g in groups}
    couplets = [g.id for g in groups if g.one_way_couplet]

    # Resolve corridors
    res = corridors.resolve_catalogue(net, cat_entries, repairs=repairs)
    chains = res.attrs["chains"]
    accepted = {cid: chains[cid] for cid in res.loc[res["accepted"], "id"]}
    membership = res.loc[res["accepted"], ["id", "corridor", "direction"]]

    # Full network AADT join for segment-level VHD/mile visualization.
    # This runs FIRST: both joins share one layer cache, and load_aadt returns a
    # cache hit *ignoring bbox*. Priming it from the corridor-only bounds would
    # leave the full-network join reading an under-covered layer.
    net_geo = net.copy()
    net_geo["Segment ID"] = net_geo["XDSegID"]
    aadt_all = join_volumes(net_geo, AADT_ZIP, year=2024, cache_path="geometry_cache/d3_aadt.parquet",
                            max_distance_m=60.0, bbox_margin=BBOX_MARGIN_DEG)

    # AADT for the corridor members (shared by ranking and map traces)
    geo = corridor_geometry(net, res, chains)
    aadt = join_volumes(geo, AADT_ZIP, year=2024, cache_path="geometry_cache/d3_aadt.parquet",
                        max_distance_m=60.0, bbox_margin=BBOX_MARGIN_DEG)

    map_files = []

    # --- Map 1: Typical Weekday Peak (AM / PM) - TTI ---
    print("Screening segments for Typical Weekday Peak (AM / PM)...")
    peak_windows = screen.PEAK_WINDOWS
    scr_peak = screen.segment_screen(con, area_key, windows=peak_windows)

    ranking_peak = screen.rank_corridors(scr_peak, accepted, aadt)
    totals_peak = screen.corridor_peak_totals(
        ranking_peak, membership, names=gnames, rank_by="vhd_per_mile",
        couplets=couplets)
    peak_ranks = totals_peak.set_index("corridor_group").to_dict(orient="index")

    print("Building Typical Peak Map (TTI)...")
    peak_path = generate_maps(
        OUT_DIR, scr_peak, net, cat_entries, chains, peak_ranks,
        windows=peak_windows,
        window_label="Typical Weekday Peak (AM / PM)",
        title_prefix="ITD District 3",
        delay_label="Total Peak Delay",
        map_filename="d3_typical_peak_map.html",
        metric="tti")
    print(f"Written: {peak_path}")
    map_files.append(("Typical Peak (TTI)", peak_path.name))

    # --- Map 2: Typical Weekday Peak (AM / PM) - VHD / Mile ---
    print("Building Typical Peak Delay Density Map (VHD / Mile)...")
    vhd_path = generate_maps(
        OUT_DIR, scr_peak, net, cat_entries, chains, peak_ranks,
        windows=peak_windows,
        window_label="Typical Weekday Peak (AM / PM)",
        title_prefix="ITD District 3",
        delay_label="Total Peak Delay",
        map_filename="d3_vhd_per_mile_map.html",
        metric="vhd_per_mile",
        aadt=aadt_all)
    print(f"Written: {vhd_path}")
    map_files.append(("Typical Peak (VHD / Mile)", vhd_path.name))

    # --- Map 3: 7-Day All-Day (6 AM - 9 PM, all 7 days) - TTI ---
    print("Screening segments for 7-Day All-Day (6:00 AM - 9:00 PM, 7 days)...")
    day7_windows = {"day_7d": screen.ALL_DAY_7D_WINDOW}
    scr_7d = screen.segment_screen(con, area_key, windows=day7_windows)

    ranking_7d = screen.rank_corridors(scr_7d, accepted, aadt)
    totals_7d = screen.corridor_peak_totals(
        ranking_7d, membership, names=gnames, rank_by="vhd_per_mile",
        couplets=couplets)
    day7_ranks = totals_7d.set_index("corridor_group").to_dict(orient="index")

    # Write 7-day corridor tables for analysis
    totals_7d_path = OUT_DIR / "corridor_7day_totals.csv"
    totals_7d.to_csv(totals_7d_path, index=False)
    print(f"Written: {totals_7d_path}")

    rankings_7d_path = OUT_DIR / "corridor_7day_rankings.csv"
    ranking_7d.to_csv(rankings_7d_path, index=False)
    print(f"Written: {rankings_7d_path}")

    print("Building 7-Day All-Day Map (TTI)...")
    day7_path = generate_maps(
        OUT_DIR, scr_7d, net, cat_entries, chains, day7_ranks,
        windows=day7_windows,
        window_label="7-Day All-Day (6 AM – 9 PM)",
        title_prefix="ITD District 3",
        delay_label="Total 7-Day Delay",
        map_filename="d3_7day_all_day_map.html",
        metric="tti")
    print(f"Written: {day7_path}")
    map_files.append(("7-Day All-Day (TTI)", day7_path.name))

    # --- Map 4: 7-Day All-Day (6 AM - 9 PM, all 7 days) - VHD / Mile ---
    print("Building 7-Day All-Day Delay Density Map (VHD / Mile)...")
    day7_vhd_path = generate_maps(
        OUT_DIR, scr_7d, net, cat_entries, chains, day7_ranks,
        windows=day7_windows,
        window_label="7-Day All-Day (6 AM – 9 PM)",
        title_prefix="ITD District 3",
        delay_label="Total 7-Day Delay",
        map_filename="d3_7day_vhd_per_mile_map.html",
        metric="vhd_per_mile",
        aadt=aadt_all)
    print(f"Written: {day7_vhd_path}")
    map_files.append(("7-Day All-Day (VHD / Mile)", day7_vhd_path.name))

    con.close()

    # --- Tabbed Viewer ---
    viewer_path = OUT_DIR / "map_viewer.html"
    viewer_path.write_text(_map_viewer_html(map_files), encoding="utf-8")
    print(f"Written: {viewer_path}")

    print(f"\nAll GIS map visualizations generated in {time.time() - t0:.1f}s!")


if __name__ == "__main__":
    main()

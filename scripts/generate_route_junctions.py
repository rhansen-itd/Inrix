#!/usr/bin/env python3
"""Add route-junction links to the district repair tables (ROADMAP Item 51).

``NextXDSegI`` follows INRIX's route, and where ITD's route turns off it the link runs
straight on (DATA_FORMAT trap 4): Payette's northbound US-95 links onto S Main St, and
the 16th St head that ITD's US-95 turns onto has no ``PreviousXD``. Item 38's
``corridors.repair_links`` cannot bridge that, because it stays inside one ``XDGroup``.
This finds the junctions the Item 51 chain walk joins
(``extents.route_junction_repairs``, on ITD membership) and appends them to
``scripts/d<N>_link_repairs.csv`` as kind ``route_junction``, so every consumer of the
table — ``corridors.build_chain`` for District 3's curated catalogue included — walks
across them. Re-running replaces the previous ``route_junction`` rows.

Usage:
    python scripts/generate_route_junctions.py
    python scripts/generate_route_junctions.py --districts 3 --dry-run
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import geopandas as gpd
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from inrix_tools import corridors, extents, itd_layers, routes  # noqa: E402

MEMBERSHIP = "out/highways/route_membership/d{district}_route_membership.csv"
HEADER = [
    "# route_junction rows (ROADMAP Item 51): extents.route_junction_repairs on ITD route",
    "#   membership - where a route turns off INRIX's link (the link is null or leaves every",
    "#   route the segment carries) onto a head nothing in the route links into, starting on",
    "#   the segment's second half within 20 m, turning <= 120 deg, milepost-consistent where",
    "#   both lie on one SHS line. These cross XDGroups, unlike fill/override.",
]


def network_for(district: int, shs) -> tuple[gpd.GeoDataFrame, pd.DataFrame, Path]:
    path = Path(f"scripts/d{district}_link_repairs.csv")
    table = corridors.load_link_repairs(path)
    base = table[table["kind"] != extents.ROUTE_JUNCTION]
    net = gpd.read_parquet(f"geometry_cache/d{district}_network.geoparquet")
    net = corridors.apply_link_repairs(net, base)
    net = routes.apply_route_membership(
        net, routes.read_membership(MEMBERSHIP.format(district=district)))
    if shs is not None:
        idx = net.set_index("XDSegID", drop=False)
        mp = itd_layers.shs_mileposts(idx, shs, idx[routes.ITD_ROUTE_ID_COL])
        for col in mp.columns:
            net[col] = net["XDSegID"].map(mp[col]).to_numpy()
    return net, base, path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--districts", nargs="*", type=int, default=[1, 2, 3, 4, 5, 6])
    parser.add_argument("--shs", default="SHS_Primary.zip")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    shs = itd_layers.load_shs(args.shs) if Path(args.shs).exists() else None

    for d in args.districts:
        net, base, path = network_for(d, shs)
        junctions = extents.route_junction_repairs(net)
        print(f"District {d}: {len(junctions)} route junctions")
        if len(junctions):
            print(junctions[["segment", "old_next", "new_next", "gap_m",
                             "bearing_delta_deg", "road_name"]].to_string(index=False))
        if args.dry_run:
            continue
        header = [ln.rstrip("\n") for ln in path.read_text().splitlines()
                  if ln.startswith("#") and "route_junction" not in ln
                  and not ln.startswith("#   ")]
        header += HEADER + [f"# n_route_junction: {len(junctions)}"]
        table = pd.concat([base, junctions], ignore_index=True)
        with path.open("w") as fh:
            fh.write("\n".join(header) + "\n")
            table.to_csv(fh, index=False)
        print(f"  -> {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

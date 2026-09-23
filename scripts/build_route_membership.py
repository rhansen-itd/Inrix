#!/usr/bin/env python3
"""Resolve every district segment's state route against ITD's AADT layer.  (ROADMAP Item 48)

Wiring only — the verdicts are :func:`inrix_tools.routes.route_membership`'s. For
each district, read the network cache (exactly the district's counties), add the
shapefile's ``SlipRoad`` flag, join the AADT layer, apply
``scripts/route_overrides.csv``, and write

* ``<out-dir>/d<N>_route_membership.csv`` — one row per segment; read by
  ``generate_district_highway_inventories.py`` and ``build_statewide_catalogues.py``;
* ``<out-dir>/route_changes_by_road.csv`` — every segment whose route differs from
  INRIX's ``RoadNumber``, grouped by road, for review;
* ``<out-dir>/route_membership.txt`` — the same, as a report.

Usage:
    python scripts/build_route_membership.py
    python scripts/build_route_membership.py --districts 2
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import geopandas as gpd
import pandas as pd
import pyogrio

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from inrix_tools import aadt as aadt_mod     # noqa: E402
from inrix_tools import geometry, routes     # noqa: E402

AADT_CACHE = {3: "geometry_cache/d3_aadt_full.geoparquet"}


def slip_roads(shapefile) -> pd.Series:
    """``XDSegID -> SlipRoad`` from the shapefile; the network caches don't carry it."""
    frame = pyogrio.read_dataframe(f"zip://{shapefile}!USA_Idaho.shp", read_geometry=False,
                                   columns=["XDSegID", "SlipRoad"])
    return frame.set_index(frame["XDSegID"].astype(int))["SlipRoad"]


def district_membership(d, *, aadt_source, aadt_year, overrides, slip):
    net = gpd.read_parquet(f"geometry_cache/d{d}_network.geoparquet")
    net["XDSegID"] = net["XDSegID"].astype(int)
    geo = geometry.segment_geometry(net)
    geo["County"] = net.set_index("XDSegID")["County"].reindex(geo.index)
    geo["SlipRoad"] = slip.reindex(geo.index)
    layer = aadt_mod.load_aadt(aadt_source, year=aadt_year,
                               cache_path=AADT_CACHE.get(d, f"geometry_cache/d{d}_aadt.parquet"))
    member = routes.route_membership(geo, layer, overrides=overrides)
    miles = net.set_index("XDSegID")["Miles"].astype(float)
    return member, miles


def render(results) -> str:
    lines = ["ROUTE MEMBERSHIP FROM ITD'S AADT LAYER  (ROADMAP Item 48)", "=" * 78]
    for d, (member, miles, changes) in results.items():
        counts = member["verdict"].value_counts()
        lines += ["", f"DISTRICT {d}", "-" * 78]
        for v in routes.VERDICTS:
            if v in counts:
                mi = miles.reindex(member.index[member["verdict"] == v]).sum()
                lines.append(f"  {v:<12}{counts[v]:>7} segs {mi:>9.1f} mi")
        if changes.empty:
            continue
        lines.append("")
        lines.append(f"  {'county':<11}{'road':<24}{'verdict':<12}{'inrix':>6} "
                     f"{'routes':<8}{'segs':>5}{'miles':>7}")
        for r in changes.itertuples():
            inrix = "" if pd.isna(r.inrix_route) else str(r.inrix_route)
            lines.append(f"  {str(r.County)[:10]:<11}{str(r.RoadName)[:23]:<24}"
                         f"{r.verdict:<12}{inrix:>6} {r.routes or '-':<8}{r.segments:>5}"
                         f"{r.miles:>7.2f}")
    return "\n".join(lines) + "\n"


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--districts", nargs="*", type=int, default=[1, 2, 3, 4, 5, 6])
    p.add_argument("--aadt", default="Cumulative_AADT.zip")
    p.add_argument("--aadt-year", type=int, default=aadt_mod.DEFAULT_YEAR)
    p.add_argument("--shapefile", default="USA_Idaho_shapefile.zip")
    p.add_argument("--overrides", default="scripts/route_overrides.csv")
    p.add_argument("--out-dir", default="out/highways/route_membership")
    args = p.parse_args(argv)

    out_dir = Path(args.out_dir)
    overrides = routes.load_route_overrides(args.overrides)
    slip = slip_roads(args.shapefile)
    results, frames = {}, []
    for d in args.districts:
        member, miles = district_membership(d, aadt_source=args.aadt,
                                            aadt_year=args.aadt_year,
                                            overrides=overrides, slip=slip)
        routes.write_membership(member, out_dir / f"d{d}_route_membership.csv")
        changes = routes.changes_by_road(member, miles)
        results[d] = (member, miles, changes)
        frames.append(changes.assign(district=d))
        print(f"District {d}: {member.attrs['route_membership']['verdicts']}")

    by_road = pd.concat(frames, ignore_index=True)
    by_road = by_road[["district"] + [c for c in by_road.columns if c != "district"]]
    by_road.to_csv(out_dir / "route_changes_by_road.csv", index=False)
    report = render(results)
    (out_dir / "route_membership.txt").write_text(report)
    print(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

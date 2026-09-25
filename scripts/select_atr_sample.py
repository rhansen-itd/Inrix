#!/usr/bin/env python3
"""Choose which permanent TCDS stations to pull: a sample, not every one.  (ROADMAP Item 62)

Wiring only — :func:`inrix_tools.counts.sample_stations` decides. Reads the TCDS
station list (``data/atr/atr_stations_statewide.csv``, from
``tcds-scraper/scratch/atr_stations.py``), keeps the stations on a state route
(:func:`counts.parse_route` reads ``On``, then the description, so 00270 on N Eagle Rd
is SH-55) that count the mainline (:func:`counts.is_ramp_station`), snaps each to its
own route's SHS line for a milepost (:func:`itd_layers.station_mileposts`), and takes
roughly every other station along each route around the ones already pulled.

Writes

* ``scripts/atr_sample.csv`` — every candidate, in route/milepost order, with its role
  (``pulled`` / ``forced`` / ``pick`` / ``skip``) and why — the list to read *before*
  pulling; plus the stations left out as ramps or off the state routes;
* ``<sites-out>`` — the ids to pull (``forced`` + ``pick``), one per line, for
  ``tcds.py --sites-file``.

Usage:
    python scripts/select_atr_sample.py
    python scripts/select_atr_sample.py --force 00270 00xxx
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

import geopandas as gpd
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from inrix_tools import counts, itd_layers  # noqa: E402

PULLED_DIRS = ("data/atr/2026-04", "data/atr/fallback")
FORCED = {
    "00270": "Eagle Rd (SH-55) north of Chinden (owner, Item 62)",
}
"""Stations the owner named. The I-84 valley picks are checked after the rule runs
(see the DESIGN_HISTORY entry) and added here only where the rule missed one."""

NO_DATA = {
    "00016": "US-95", "00085": "US-12", "00159": "US-20", "00173": "I-15",
}
"""Stations TCDS has no count for in April 2026, March/May 2026 or April 2023-2025
(Item 62's pull). They leave the candidates, so the rule picks around them."""
DAILY_ONLY = {
    "00027": "SH-3", "00114": "US-2", "00147": "SH-57", "00182": "SH-55 (2025-04)",
    "00291": "I-90 (2026-03)",
}
"""Stations whose workbook is daily totals spread flat over the hours (Item 62): no
hourly shape to fit (``counts.FLAT_DAY_REASON``). They leave the candidates too."""

_SITE_FILE = re.compile(r"^(\d{5})_\d{4}-\d{2}_")


def pulled_ids(dirs) -> set[str]:
    """Station ids with a workbook already on disk."""
    ids = set()
    for d in dirs:
        for f in Path(d).glob("*.xlsx"):
            m = _SITE_FILE.match(f.name)
            if m:
                ids.add(m.group(1))
    return ids


def candidates(stations: pd.DataFrame, shs) -> tuple[pd.DataFrame, pd.DataFrame]:
    """``(candidates, left_out)``: state-route mainline stations with a milepost, and
    the rest with the reason."""
    st = stations.copy()
    st["route"] = [counts.parse_route(o, a) for o, a in zip(st["on"], st["at"])]
    st["ramp"] = [counts.is_ramp_station(o, a) for o, a in zip(st["on"], st["at"])]
    st["number"] = st["route"].map(lambda r: counts.split_route(r)[0])
    st["business"] = st["route"].map(lambda r: counts.split_route(r)[1])
    # A relocated counter's second id ("00213-8-26-25") duplicates a station already
    # listed; the TCDS portal pulls by the five-digit id.
    st["reason"] = ""
    st.loc[~st["local_id"].str.fullmatch(r"\d{5}"), "reason"] = "not a five-digit id"
    st.loc[(st["reason"] == "") & (st["route"] == ""), "reason"] = "not on a state route"
    st.loc[(st["reason"] == "") & st["ramp"], "reason"] = "ramp count"
    st.loc[(st["reason"] == "") & st["local_id"].isin(list(NO_DATA)), "reason"] = \
        "no TCDS count since 2023"
    st.loc[(st["reason"] == "") & st["local_id"].isin(list(DAILY_ONLY)), "reason"] = \
        "daily totals only (no hourly shape)"
    keep = st[st["reason"] == ""].copy()
    pts = gpd.GeoDataFrame(keep, geometry=gpd.points_from_xy(keep["lon"], keep["lat"]),
                           crs="EPSG:4326")
    mp = itd_layers.station_mileposts(pts, shs, keep["number"], keep["business"])
    keep = keep.join(mp)
    nomp = keep["shs_mp"].isna()
    st.loc[keep.index[nomp], "reason"] = "no SHS line of its route within reach"
    keep = keep[~nomp].copy()
    # One sequence per route; each business loop is its own (its own SHS RouteID).
    keep["route_key"] = [
        f"{n} BL {str(rid)[:5]}" if bl else str(n)
        for n, bl, rid in zip(keep["number"], keep["business"], keep["shs_route_id"])]
    keep["mp"] = keep["shs_mp"]
    return keep, st[st["reason"] != ""]


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--stations", default="data/atr/atr_stations_statewide.csv")
    p.add_argument("--shs", default="SHS_Primary.zip")
    p.add_argument("--force", nargs="*", default=sorted(FORCED))
    p.add_argument("--out", default="scripts/atr_sample.csv")
    p.add_argument("--sites-out", default="data/atr/sample_sites.txt")
    args = p.parse_args(argv)

    stations = pd.read_csv(args.stations, dtype={"local_id": str})
    shs = itd_layers.load_shs(args.shs)
    keep, left = candidates(stations, shs)
    pulled = pulled_ids(PULLED_DIRS)
    frame = keep.rename(columns={"local_id": "station_id"})
    sample = counts.sample_stations(frame, pulled=pulled, forced=args.force)
    for sid, why in FORCED.items():
        sample.loc[(sample["station_id"] == sid) & (sample["role"] == "forced"),
                   "reason"] = why

    cols = ["station_id", "route_key", "mp", "role", "reason", "site", "station_type",
            "on", "at", "shs_offset_m", "lat", "lon"]
    out = sample[cols]
    left_rows = left.rename(columns={"local_id": "station_id"}).assign(
        route_key="", mp=float("nan"), role="out", site="", shs_offset_m=float("nan"))
    out = pd.concat([out, left_rows[cols]], ignore_index=True)
    out.to_csv(args.out, index=False)
    to_pull = sample[sample["role"].isin(["forced", "pick"])]["station_id"].tolist()
    Path(args.sites_out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.sites_out).write_text("".join(f"{s}\n" for s in to_pull))

    print(sample["role"].value_counts().to_string())
    print(f"left out: {left['reason'].value_counts().to_dict()}")
    for key, grp in sample.groupby("route_key", sort=False):
        line = " ".join(
            f"{r.station_id}{ {'pulled': '*', 'forced': '!', 'pick': '+', 'skip': '-'}[r.role]}"
            f"@{r.mp:.1f}" for r in grp.itertuples())
        print(f"  {key:<14} {line}")
    print(f"\n{len(to_pull)} to pull -> {args.sites_out};  table -> {args.out}")
    print("  * pulled  ! forced  + pick  - skip")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

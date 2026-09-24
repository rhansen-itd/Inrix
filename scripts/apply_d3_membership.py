#!/usr/bin/env python3
"""Apply the State Highway System membership to the curated District 3 lists (Item 49).

District 3's inventory is hand-curated (Item 42), not generated like D1/D2/D4–D6,
so Item 52's membership was reported for it but not applied. The owner's call
(2026-09-23): the D3 lists take it. This script

- drops, from every D3 list (the master, the no-Caldwell variant, and the
  per-highway ``_ALL/_EB/_WB/_NB/_SB`` files), the segments membership leaves on no
  route (``routes.no_route_segments``). These are Cleveland Blvd, Blaine St,
  Northside Blvd, Payette's S Main St / S 7th St / 7th Ave N, Elmore's Old
  Highway 30 and short stubs;
- **keeps Banks-Lowman Hwy**, which is off the system but stays in the D3 export on
  purpose (owner); the ranking filters it by membership;
- recomputes the counts and miles in ``district_3_master_highways.json``,
  ``district_3_highways.json`` and ``district_3_highways_summary.csv``;
- writes the add-list of SHS-route segments the D3 lists lack.

The segments stay in ``d3_store.duckdb``. The pre-Item-49 lists are copied to
``out/highways/pre_item49/`` first, and a second run is a no-op.

Usage:
    python scripts/apply_d3_membership.py
    python scripts/apply_d3_membership.py --dry-run
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

import geopandas as gpd
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from inrix_tools import routes  # noqa: E402

HIGHWAYS = Path("out/highways")
MEMBERSHIP = HIGHWAYS / "route_membership" / "d3_route_membership.csv"
BACKUP = HIGHWAYS / "pre_item49"
ADD_DIR = Path("out/export_reconciliation/item49_d3")
KEEP_COMPONENT = "Banks-Lowman"
DIRS = ("EB", "WB", "NB", "SB")


def d3_list_files() -> list[Path]:
    """Every D3 list: the top-level ``out/highways/*.txt`` except the other districts'
    masters and the statewide one (the inventory generator copies these into
    ``district_3/``)."""
    return sorted(p for p in HIGHWAYS.glob("*.txt")
                  if p.name.startswith("District_3_")
                  or not p.name.startswith(("District_", "Statewide_")))


def read_ids(path: Path) -> list[int]:
    return [int(x) for x in path.read_text().strip().split(",") if x.strip()]


def write_ids(path: Path, ids: list[int]) -> None:
    path.write_text(",".join(str(i) for i in ids))


def filter_json(node, drop: set[int], miles: pd.Series):
    """Remove ``drop`` from every ``segment_ids`` list and recompute the
    ``count``/``miles`` (directions) and ``total_segments``/``total_miles``
    (components) that describe it."""
    if isinstance(node, dict):
        out = {k: filter_json(v, drop, miles) for k, v in node.items()}
        if isinstance(out.get("segment_ids"), list):
            ids = out["segment_ids"]
            if "count" in out:
                out["count"] = len(ids)
            if "miles" in out:
                out["miles"] = round(float(miles.reindex(ids).sum()), 2)
        dirs = [out[d] for d in DIRS if isinstance(out.get(d), dict)
                and "segment_ids" in out[d]]
        if dirs and "total_segments" in out:
            ids = [s for d in dirs for s in d["segment_ids"]]
            out["total_segments"] = len(ids)
            out["total_miles"] = round(float(miles.reindex(ids).sum()), 2)
        return out
    if isinstance(node, list):
        return [filter_json(v, drop, miles) for v in node
                if not (isinstance(v, int) and v in drop)]
    return node


def summary_csv(components: dict) -> pd.DataFrame:
    rows = []
    for name, c in components.items():
        row = {"Highway": name, "Description": c.get("description", name)}
        for d in DIRS:
            if isinstance(c.get(d), dict):
                row[f"{d} Segs"] = c[d]["count"]
                row[f"{d} Miles"] = c[d]["miles"]
        row["Total Segs"] = c.get("total_segments")
        row["Total Miles"] = c.get("total_miles")
        rows.append(row)
    cols = ["Highway", "Description", "EB Segs", "EB Miles", "WB Segs", "WB Miles",
            "Total Segs", "Total Miles", "NB Segs", "NB Miles", "SB Segs", "SB Miles"]
    return pd.DataFrame(rows).reindex(columns=cols)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--network", default="geometry_cache/d3_network.geoparquet")
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args(argv)

    member = routes.read_membership(MEMBERSHIP)
    net = gpd.read_parquet(args.network).set_index("XDSegID")
    miles = net["Miles"].astype(float)
    master_json_path = HIGHWAYS / "district_3_master_highways.json"
    master_json = json.loads(master_json_path.read_text())
    keep_comp = master_json["components"][KEEP_COMPONENT]
    keep = [s for d in DIRS if isinstance(keep_comp.get(d), dict)
            for s in keep_comp[d]["segment_ids"]]

    master = read_ids(HIGHWAYS / "District_3_ALL_Highways.txt")
    rec = routes.reconcile_curated_list(master, member, keep=keep)
    drop = set(rec["dropped"])
    print(f"D3 master: {len(master)} segments; dropping {len(drop)} "
          f"({miles.reindex(rec['dropped']).sum():.2f} mi); "
          f"{len(rec['missing'])} SHS-route segments missing "
          f"({miles.reindex(rec['missing']).sum():.2f} mi)")
    by_road = (member.loc[rec["dropped"], ["RoadName", "verdict"]]
               .assign(miles=miles.reindex(rec["dropped"]).values)
               .groupby(["RoadName", "verdict"], dropna=False)
               .agg(segments=("miles", "size"), miles=("miles", "sum"))
               .sort_values("miles", ascending=False).round(2))
    print(by_road.to_string())
    if args.dry_run:
        return 0

    files = d3_list_files()
    json_paths = [master_json_path, HIGHWAYS / "district_3_highways.json"]
    csv_path = HIGHWAYS / "district_3_highways_summary.csv"
    if not BACKUP.exists():
        BACKUP.mkdir(parents=True)
        for f in [*files, *json_paths, csv_path]:
            if f.exists():
                shutil.copy2(f, BACKUP / f.name)
        print(f"pre-Item-49 lists copied to {BACKUP}/")

    changed = 0
    for f in files:
        ids = read_ids(f)
        kept = [s for s in ids if s not in drop]
        if len(kept) != len(ids):
            write_ids(f, kept)
            changed += 1
            print(f"  {f.name}: {len(ids)} -> {len(kept)}")
    print(f"{changed} list files rewritten")

    for jp in json_paths:
        if jp.exists():
            data = filter_json(json.loads(jp.read_text()), drop, miles)
            if jp == master_json_path:
                for key in ("master_full", "master_no_caldwell_i84b"):
                    ids = read_ids(Path(data[key]["file"]))
                    data[key]["total_segments"] = len(ids)
                    data[key]["total_miles"] = round(float(miles.reindex(ids).sum()), 2)
                summary_csv(data["components"]).to_csv(csv_path, index=False)
            jp.write_text(json.dumps(data, indent=2))

    ADD_DIR.mkdir(parents=True, exist_ok=True)
    write_ids(ADD_DIR / "segments_to_add.txt", rec["missing"])
    by_road.reset_index().to_csv(ADD_DIR / "dropped_by_road.csv", index=False)
    (member.loc[rec["missing"], ["RoadName", "verdict", "routes"]]
     .assign(miles=miles.reindex(rec["missing"]).round(3).values)
     .to_csv(ADD_DIR / "segments_to_add.csv"))
    print(f"add-list and drop table written to {ADD_DIR}/")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

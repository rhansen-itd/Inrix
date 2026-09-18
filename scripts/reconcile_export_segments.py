#!/usr/bin/env python3
"""Reconcile the export's segment set against the corridor inventory.  (Item 42)

Two questions, asked of the same export and kept apart:

1. **Was everything the inventory asked for delivered?** ``out/highways/`` holds a
   per-corridor id list for every D3 state route and a master list that collects
   them. Three set differences answer it — ids a corridor file holds that never
   reached the master list, ids the master asked for that the store never observed,
   and ids the store holds that nobody asked for. The last one being empty is what
   makes the inventory trustworthy; the first is how 57 SH-55 ids went missing
   without anyone noticing.
2. **Is anything on-system missing from the inventory altogether?** Every XD segment
   in the district that is *not* in the export is classified against ITD's AADT layer
   by :func:`inrix_tools.aadt.classify_on_system`, and the ones that come back
   on-system are listed by road. This is the check the earlier pass got wrong by
   reading on-system-ness off the volume join (see the function's docstring).

Wiring only: the set arithmetic is this script's, every classification is the core's.

Typical run (paths are this machine's; nothing here assumes them)::

    python scripts/reconcile_export_segments.py \\
        --db d3_store.duckdb \\
        --highways-dir out/highways \\
        --network-cache geometry_cache/d3_network.geoparquet \\
        --aadt Cumulative_AADT.zip \\
        --aadt-cache geometry_cache/d3_aadt_full.geoparquet \\
        --out-dir out/export_reconciliation

Without ``--aadt`` it runs section 1 only, which needs no geometry at all.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from inrix_tools import aadt as aadt_mod                  # noqa: E402
from inrix_tools import geometry, store                   # noqa: E402

MASTER_NAME = "District_3_ALL_Highways.txt"
DIRECTIONS = ("NB", "SB", "EB", "WB")
BBOX_MARGIN_DEG = 0.05


# ---------------------------------------------------------------------------
# Section 1 — the corridor inventory
# ---------------------------------------------------------------------------
def read_id_file(path) -> list[int]:
    """The ids in an ``out/highways/*.txt`` list — comma-separated, possibly wrapped
    over lines, possibly with a trailing comma. Order is kept; duplicates are not
    dropped, because a file repeating an id is itself a finding."""
    text = Path(path).read_text()
    return [int(tok) for tok in text.replace("\n", ",").split(",") if tok.strip()]


def corridor_files(highways_dir) -> list[Path]:
    """The per-corridor ``*_ALL.txt`` lists, master lists excluded."""
    return sorted(p for p in Path(highways_dir).glob("*_ALL.txt")
                  if not p.name.startswith("District_3"))


def reconcile_inventory(highways_dir, master_ids, observed) -> pd.DataFrame:
    """One row per corridor file: how many of its ids reached the master list, how
    many the store observed, and whether its directional files add up to it.

    ``missing_from_master`` is the column that matters — an id a corridor file holds
    and the master list does not was never requested, so its absence from the export
    is not evidence of anything about the road.
    """
    master, obs = set(master_ids), set(observed)
    rows = []
    for path in corridor_files(highways_dir):
        ids = read_id_file(path)
        unique = set(ids)
        base = path.name[: -len("_ALL.txt")]
        parts = [Path(highways_dir) / f"{base}_{d}.txt" for d in DIRECTIONS]
        split = {i for p in parts if p.exists() for i in read_id_file(p)}
        rows.append({
            "file": path.name,
            "n_ids": len(ids),
            "n_unique": len(unique),
            "missing_from_master": len(unique - master),
            "not_observed": len(unique - obs),
            "directional_files": sum(p.exists() for p in parts),
            "directional_mismatch": len(split ^ unique) if split else 0,
            "missing_ids": ",".join(str(i) for i in sorted(unique - master)),
        })
    frame = pd.DataFrame(rows)
    frame.attrs["totals"] = {
        "n_corridor_files": len(frame),
        "n_ids_in_corridor_files": int(len(set().union(
            *[set(read_id_file(p)) for p in corridor_files(highways_dir)]) or set())),
        "n_master": len(master),
        "n_observed": len(obs),
        "requested_not_returned": len(master - obs),
        "observed_not_requested": len(obs - master),
    }
    return frame


def inventory_membership(highways_dir) -> dict:
    """``Segment ID -> the corridor files that list it``. An absent segment that a
    corridor file already holds was *asked for* and is a different finding from one
    no list has ever named: the first is a delivery question, the second means the
    query that built the inventory never saw the road."""
    out: dict[int, list[str]] = {}
    for path in corridor_files(highways_dir):
        for sid in read_id_file(path):
            out.setdefault(sid, []).append(path.name)
    return out


# ---------------------------------------------------------------------------
# Section 2 — on-system segments absent from the export
# ---------------------------------------------------------------------------
def load_network(source, cache_path=None):
    """The XD network, cache-first (the same rule the screening runner uses)."""
    if cache_path is None and Path(source).suffix.lower() in (".parquet", ".geoparquet"):
        cache_path = source
    return geometry.load_xd_network(source, cache_path=cache_path)


def absent_segments(net, observed) -> pd.DataFrame:
    """The district's XD segments that the export does not carry."""
    return net[~net["XDSegID"].isin(set(observed))]


def classify_absent(net, observed, aadt_source, *, year, aadt_cache,
                    max_distance_m, min_coverage, on_system_distance_m):
    """Join the absent segments to the AADT layer and classify them on-system.

    Returns the classified frame (one row per absent segment) with the segment's
    ``Miles`` carried across, so a finding can be reported in miles rather than in
    segment counts — 24 stubs of a subdivision drive and 24 segments of a highway are
    not the same discovery.
    """
    absent = absent_segments(net, observed)
    geo = geometry.segment_geometry(net, segment_ids=list(absent["XDSegID"]))
    bounds = geo["geometry"].dropna().total_bounds
    bbox = (bounds[0] - BBOX_MARGIN_DEG, bounds[1] - BBOX_MARGIN_DEG,
            bounds[2] + BBOX_MARGIN_DEG, bounds[3] + BBOX_MARGIN_DEG)
    layer = aadt_mod.load_aadt(aadt_source, year=year, bbox=bbox, cache_path=aadt_cache)
    joined = aadt_mod.join_aadt(geo, layer, max_distance_m=max_distance_m)
    classified = aadt_mod.classify_on_system(
        joined, min_coverage=min_coverage, max_distance_m=on_system_distance_m)
    classified["Miles"] = net.set_index("XDSegID")["Miles"].reindex(classified.index)
    return classified


def annotate_inventory(classified, membership) -> pd.DataFrame:
    """Add ``inventory_files`` — which corridor lists already name each segment."""
    out = classified.copy()
    out["inventory_files"] = [";".join(membership.get(int(sid), []))
                              for sid in out.index]
    return out


def candidates_by_road(classified) -> pd.DataFrame:
    """The on-system absentees grouped by road and route — the review unit. A road
    is a thing the owner can accept or reject; a segment id is not."""
    hit = classified[classified[aadt_mod.ON_SYSTEM_COL]].copy()
    if hit.empty:
        return pd.DataFrame(columns=["road", "route", "segments", "miles", "aadt_desc",
                                     "aadt", "median_dist_m", "inventory", "segment_ids"])
    hit["road"] = hit["RoadName"].fillna("(unnamed)")
    grouped = hit.groupby(["road", hit[aadt_mod.ON_SYSTEM_REASON_COL]], dropna=False)
    out = grouped.agg(
        segments=("Miles", "size"),
        miles=("Miles", "sum"),
        aadt_desc=(aadt_mod.AADT_DESC_COL, "first"),
        aadt=(aadt_mod.AADT_COL, "median"),
        median_dist_m=(aadt_mod.AADT_DIST_COL, "median"),
        inventory=("inventory_files", lambda s: ";".join(sorted(
            {f for v in s for f in str(v).split(";") if f}))),
        segment_ids=("Miles", lambda s: ",".join(str(i) for i in sorted(s.index))),
    ).reset_index()
    out = out.rename(columns={aadt_mod.ON_SYSTEM_REASON_COL: "route"})
    out["miles"] = out["miles"].round(3)
    out["median_dist_m"] = out["median_dist_m"].round(2)
    return out.sort_values("miles", ascending=False, ignore_index=True)


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------
def render(inventory, classified, candidates, prov) -> str:
    t = inventory.attrs["totals"]
    lines = [
        "EXPORT SEGMENT RECONCILIATION  (ROADMAP Item 42)",
        "=" * 78,
        f"generated {prov['generated_utc']}   store {prov['db']} / {prov['area_name']}",
        "",
        "1. THE CORRIDOR INVENTORY",
        "-" * 78,
        f"  per-corridor *_ALL.txt files      {t['n_corridor_files']:>6}",
        f"  ids across those files            {t['n_ids_in_corridor_files']:>6}",
        f"  ids in the master list            {t['n_master']:>6}",
        f"  segments observed in the store    {t['n_observed']:>6}",
        f"  requested, returned nothing       {t['requested_not_returned']:>6}",
        f"  observed but never requested      {t['observed_not_requested']:>6}",
        "",
    ]
    flagged = inventory[(inventory["missing_from_master"] > 0)
                        | (inventory["not_observed"] > 0)
                        | (inventory["directional_mismatch"] > 0)]
    if flagged.empty:
        lines.append("  Every corridor file reached the master list and the store.")
    else:
        lines.append("  Corridor files that do not reconcile:")
        lines.append(f"    {'file':<32}{'ids':>6}{'not in master':>15}{'not observed':>14}"
                     f"{'dir mismatch':>14}")
        for r in flagged.itertuples():
            lines.append(f"    {r.file:<32}{r.n_unique:>6}{r.missing_from_master:>15}"
                         f"{r.not_observed:>14}{r.directional_mismatch:>14}")
    lines += ["", "2. ON-SYSTEM SEGMENTS ABSENT FROM THE EXPORT", "-" * 78]
    if classified is None:
        lines.append("  (not run — no --aadt source given)")
        return "\n".join(lines) + "\n"
    pol = classified.attrs["on_system"]
    n_absent = len(classified)
    n_hit = int(classified[aadt_mod.ON_SYSTEM_COL].sum())
    miles = float(classified.loc[classified[aadt_mod.ON_SYSTEM_COL], "Miles"].sum())
    lines += [
        f"  district XD segments not in the export   {n_absent:>6}",
        f"  of those, classified on-system           {n_hit:>6}   ({miles:.2f} mi)",
        f"  policy: route named, identity agrees, mainline, <= {pol['max_distance_m']:g} m, "
        f"coverage >= {pol['min_coverage']:g}",
        "",
        "  Why the rest were rejected:",
    ]
    for reason, count in pol["rejected_because"].items():
        lines.append(f"    {count:>6}  {reason}")
    lines += ["", "  Candidates by road (review these, accept or reject with a reason):"]
    if candidates.empty:
        lines.append("    none")
    else:
        lines.append(f"    {'road':<24}{'route':<10}{'segs':>5}{'miles':>7}{'dist m':>7}"
                     f"{'aadt':>8}  matched record / already inventoried as")
        for r in candidates.itertuples():
            inv = r.inventory or "-- in no corridor list --"
            lines.append(f"    {str(r.road)[:23]:<24}{str(r.route):<10}{r.segments:>5}"
                         f"{r.miles:>7.2f}{r.median_dist_m:>7.2f}{r.aadt:>8.0f}  "
                         f"{r.aadt_desc} / {inv}")
        ids = sorted(classified.index[classified[aadt_mod.ON_SYSTEM_COL]])
        lines += ["", f"  PASTE-READY -- {len(ids)} segments, {miles:.2f} mi",
                  "  " + ",".join(str(i) for i in ids)]
    return "\n".join(lines) + "\n"


def write_outputs(out_dir, inventory, classified, candidates, prov, report) -> dict:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    written = {}
    inv_path = out_dir / "inventory_reconciliation.csv"
    inventory.to_csv(inv_path, index=False)
    written["inventory"] = str(inv_path)
    if classified is not None:
        cand_path = out_dir / "on_system_candidates.csv"
        cols = ["RoadName", "RoadNumber", "Miles", aadt_mod.AADT_COL,
                aadt_mod.AADT_DESC_COL, aadt_mod.AADT_DIST_COL,
                aadt_mod.AADT_COVER_COL, aadt_mod.AADT_ROUTE_NUM_COL,
                aadt_mod.ON_SYSTEM_COL, aadt_mod.ON_SYSTEM_REASON_COL,
                aadt_mod.ON_SYSTEM_CATEGORY_COL, "inventory_files"]
        frame = classified[[c for c in cols if c in classified.columns]]
        frame.to_csv(cand_path)
        written["absent_segments"] = str(cand_path)
        by_road = out_dir / "on_system_candidates_by_road.csv"
        candidates.to_csv(by_road, index=False)
        written["by_road"] = str(by_road)
    report_path = out_dir / "reconciliation.txt"
    report_path.write_text(report)
    written["report"] = str(report_path)
    prov_path = out_dir / "reconciliation_provenance.json"
    prov_path.write_text(json.dumps(prov, indent=2, default=str))
    written["provenance"] = str(prov_path)
    return written


# ---------------------------------------------------------------------------
# The run
# ---------------------------------------------------------------------------
def resolve_area(con, area: str | None) -> tuple[str, str]:
    areas = store.list_areas(con)
    if areas.empty:
        raise SystemExit("The store holds no areas — ingest an export first.")
    if area is None:
        if len(areas) > 1:
            raise SystemExit(
                "The store holds several areas; pass --area. Available: "
                + ", ".join(f"{r.area_key} ({r.area_name})" for r in areas.itertuples()))
        row = areas.iloc[0]
    else:
        hit = areas[(areas["area_key"] == area) | (areas["area_name"] == area)]
        if hit.empty:
            raise SystemExit(f"No area {area!r}; available: "
                             + ", ".join(areas["area_key"].astype(str)))
        row = hit.iloc[0]
    return str(row["area_key"]), str(row["area_name"])


def run(args) -> dict:
    con = store.connect(args.db, read_only=False)
    try:
        area_key, area_name = resolve_area(con, args.area)
        # The observations, not the metadata: an export is split into parts by
        # segment and each part's metadata.csv lists only its own (store.area_segments).
        observed = store.area_segments(con, area_key)
    finally:
        con.close()

    master_path = Path(args.master) if args.master else Path(args.highways_dir) / MASTER_NAME
    inventory = reconcile_inventory(args.highways_dir, read_id_file(master_path), observed)

    classified = candidates = None
    if args.aadt:
        net = load_network(args.network, args.network_cache)
        classified = annotate_inventory(
            classify_absent(
                net, observed, args.aadt, year=args.aadt_year,
                aadt_cache=args.aadt_cache, max_distance_m=args.aadt_max_distance_m,
                min_coverage=args.min_coverage,
                on_system_distance_m=args.on_system_distance_m),
            inventory_membership(args.highways_dir))
        candidates = candidates_by_road(classified)

    prov = {
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "db": str(args.db),
        "area_key": area_key,
        "area_name": area_name,
        "highways_dir": str(args.highways_dir),
        "master": str(master_path),
        "inventory_totals": inventory.attrs["totals"],
        "network": None if classified is None else str(args.network),
        "aadt": None if classified is None else {
            "source": str(args.aadt), "year": args.aadt_year,
            "join": classified.attrs.get("aadt_join"),
            "on_system": classified.attrs.get("on_system"),
        },
    }
    report = render(inventory, classified, candidates, prov)
    written = write_outputs(args.out_dir, inventory, classified,
                            candidates if candidates is not None else pd.DataFrame(),
                            prov, report)
    return {"inventory": inventory, "classified": classified,
            "candidates": candidates, "report": report, "written": written}


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--db", required=True, help="DuckDB store (store.connect)")
    p.add_argument("--area", default=None, help="area key or name (default: the only one)")
    p.add_argument("--highways-dir", default="out/highways")
    p.add_argument("--master", default=None,
                   help=f"master id list (default: <highways-dir>/{MASTER_NAME})")
    p.add_argument("--network", default="USA_Idaho_shapefile.zip")
    p.add_argument("--network-cache", default=None)
    p.add_argument("--aadt", default=None,
                   help="AADT source; omitted runs the inventory check only")
    p.add_argument("--aadt-cache", default=None)
    p.add_argument("--aadt-year", type=int, default=aadt_mod.DEFAULT_YEAR)
    p.add_argument("--aadt-max-distance-m", type=float, default=60.0,
                   help="the join's gate (aadt.join_aadt)")
    p.add_argument("--on-system-distance-m", type=float,
                   default=aadt_mod.DEFAULT_ON_SYSTEM_DISTANCE_M,
                   help="furthest a record may lie and still classify the segment")
    p.add_argument("--min-coverage", type=float,
                   default=aadt_mod.DEFAULT_ON_SYSTEM_COVERAGE,
                   help="least share of the segment the record must run beside")
    p.add_argument("--out-dir", default="out/export_reconciliation")
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    result = run(args)
    print(result["report"])
    print("Written:")
    pad = max(len(k) for k in result["written"]) + 2
    for k, v in result["written"].items():
        print(f"  {k:<{pad}}{v}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

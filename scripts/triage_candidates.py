#!/usr/bin/env python3
"""Triage all recurring congestion candidate runs for any ITD district (ROADMAP Item 45.4).

Runs congestion extraction over the on-system export in both AM and PM peak
windows, evaluates every candidate against objective acceptance/rejection
rules (A1, M1, R1–R3), and outputs an auditable triage table.

Replaces the previous D3-specific version that used hardcoded route lists.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import geopandas as gpd
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from inrix_tools import corridors, geometry, screen, store


# ─── Objective Triage Rules ──────────────────────────────────────────

# Rule A1 thresholds
A1_MIN_MILES = 1.00
A1_MIN_RECURRENCE = 0.50
A1_MIN_TTI = 1.20

# Rule R2 thresholds
R2_MAX_MILES = 0.35
R2_ADJACENT_TTI_FREEFLOW = 1.05

# Rule R3: geometric delay (TTI ≈ 1.0 means speed == free-flow)
R3_TTI_TOLERANCE = 0.05  # TTI within 1.0 ± 0.05 means no congestion delay


def _is_on_state_highway(run, network_indexed: pd.DataFrame) -> bool:
    """Check if the run's primary segments are on a numbered state highway."""
    ramp_count = 0
    numbered_count = 0
    total = len(run.segment_ids)
    if total == 0:
        return False

    for sid in run.segment_ids:
        if sid not in network_indexed.index:
            continue
        row = network_indexed.loc[sid]
        if isinstance(row, pd.DataFrame):
            row = row.iloc[0]
        rn = row.get("RoadNumber")
        if pd.notna(rn) and str(rn).strip() != "":
            numbered_count += 1
        slip = row.get("SlipRoad", 0)
        if pd.notna(slip) and str(slip) in ("1", "1.0"):
            ramp_count += 1

    return numbered_count > total / 2 and ramp_count < total / 2


def _adjacent_segments_freeflow(run, recurrence: pd.DataFrame, network_indexed: pd.DataFrame, window: str = "am") -> bool:
    """Check if segments immediately adjacent to the run are free-flowing."""
    tti_col = f"{window}_mean_tti"
    if tti_col not in recurrence.columns:
        return False

    first_seg = run.start_segment
    last_seg = run.end_segment

    # Upstream: walk backwards via PreviousXD
    upstream_ttis = []
    curr = first_seg
    for _ in range(3):
        if curr not in network_indexed.index:
            break
        row = network_indexed.loc[curr]
        if isinstance(row, pd.DataFrame):
            row = row.iloc[0]
        prev = row.get("PreviousXD")
        if pd.isna(prev):
            break
        try:
            prev = int(prev)
        except (ValueError, TypeError):
            break
        if prev in recurrence.index:
            val = recurrence.loc[prev, tti_col]
            if isinstance(val, pd.Series):
                val = val.iloc[0]
            if pd.notna(val):
                upstream_ttis.append(float(val))
        curr = prev

    # Downstream: walk forwards via NextXDSegI
    downstream_ttis = []
    curr = last_seg
    for _ in range(3):
        if curr not in network_indexed.index:
            break
        row = network_indexed.loc[curr]
        if isinstance(row, pd.DataFrame):
            row = row.iloc[0]
        nxt = row.get("NextXDSegI")
        if pd.isna(nxt):
            break
        try:
            nxt = int(nxt)
        except (ValueError, TypeError):
            break
        if nxt in recurrence.index:
            val = recurrence.loc[nxt, tti_col]
            if isinstance(val, pd.Series):
                val = val.iloc[0]
            if pd.notna(val):
                downstream_ttis.append(float(val))
        curr = nxt

    adj_ttis = upstream_ttis + downstream_ttis
    if not adj_ttis:
        return False
    return all(t < R2_ADJACENT_TTI_FREEFLOW for t in adj_ttis)


def triage_candidates(
    rec: pd.DataFrame,
    net: gpd.GeoDataFrame,
    repairs=None,
    cat_entries=None,
    chains: dict | None = None,
    *,
    recurrence: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """Triage all candidate runs into ACCEPTED, MERGED, or REJECTED using objective rules."""
    net_idx = net.set_index("XDSegID", drop=False) if net.index.name != "XDSegID" else net

    # Build segment-to-corridor membership map if catalogue entries provided
    seg_to_corridors: dict[int, list[str]] = {}
    if cat_entries and chains:
        for entry in cat_entries:
            cid = entry.id
            if cid not in chains:
                continue
            ch = chains[cid]
            for sid in ch.segment_ids:
                seg_to_corridors.setdefault(int(sid), []).append(cid)

    if recurrence is None:
        recurrence = rec
    if recurrence.index.name != "Segment ID" and "Segment ID" in recurrence.columns:
        recurrence = recurrence.set_index("Segment ID")

    triage_rows = []
    for win in ["am", "pm"]:
        runs = screen.extract_congestion_runs(rec, net, window=win, repairs=repairs)
        cands = screen.emit_candidates(runs, net, repairs=repairs, window=win)

        for r, c in zip(runs, cands):
            cid = c["id"]
            rnums = list(r.road_numbers)
            primary_rnum = rnums[0] if rnums else ""
            direction = c.get("_direction", "")
            miles = r.total_miles
            segs = r.n_segments
            qual = r.n_qualifying
            mean_rec = r.mean_recurrence
            mean_tti = r.mean_tti
            road_label = r.road_label

            # Find overlapping catalogue corridors (Rule M1)
            corridor_matches = set()
            for sid in r.segment_ids:
                for match_cid in seg_to_corridors.get(sid, []):
                    corridor_matches.add(match_cid)

            # Apply rules in priority order
            is_ramp = "Ramp" in road_label or "IC" in road_label
            is_on_system = _is_on_state_highway(r, net_idx)

            decision = "REJECTED"
            rule = "R2"
            matched_corridor = None
            reason = ""

            # Rule R1: Topological rejection (ramps, off-system local streets)
            if is_ramp:
                decision = "REJECTED"
                rule = "R1"
                reason = "Interchange ramp terminal or turnaround stub"
            elif not primary_rnum and not is_on_system:
                decision = "REJECTED"
                rule = "R1"
                reason = "Unnumbered or non-mainline road segment outside state highway network"

            # Rule M1: Corridor membership merge
            elif corridor_matches:
                matched_corridor = ";".join(sorted(corridor_matches))
                decision = "MERGED"
                rule = "M1"
                reason = (f"Contiguous congestion run merged into corridor extent {matched_corridor} "
                          f"({miles:.2f} mi, TTI {mean_tti:.2f})")

            # Rule A1: Independent empirical corridor acceptance
            elif miles >= A1_MIN_MILES and mean_rec >= A1_MIN_RECURRENCE and mean_tti >= A1_MIN_TTI and is_on_system:
                decision = "ACCEPTED"
                rule = "A1"
                reason = (f"Independent empirical corridor on route {primary_rnum} "
                          f"({miles:.2f} mi, TTI {mean_tti:.2f}, recurrence {mean_rec:.2f})")

            # Rule R2: Isolated signal queue rejection
            elif miles < R2_MAX_MILES and _adjacent_segments_freeflow(r, recurrence, net_idx, win):
                decision = "REJECTED"
                rule = "R2"
                reason = (f"Isolated junction queue (< {R2_MAX_MILES:.2f} mi, {miles:.2f} mi) on route "
                          f"{primary_rnum}; adjacent segments free-flowing")

            # Rule R3: Geometric / low speed delay rejection
            elif mean_tti < 1.0 + R3_TTI_TOLERANCE and is_on_system:
                decision = "REJECTED"
                rule = "R3"
                reason = (f"Geometric speed suppression without congestion delay "
                          f"(TTI {mean_tti:.2f}) on route {primary_rnum}")

            # Catch-all
            else:
                decision = "REJECTED"
                rule = "R2"
                reason = (f"Isolated queue on route {primary_rnum} ({miles:.2f} mi, TTI {mean_tti:.2f}) "
                          f"insufficient to form independent reporting corridor")

            start_coords = c.get("start_latlon", [0.0, 0.0])
            end_coords = c.get("end_latlon", [0.0, 0.0])

            triage_rows.append({
                "candidate_id": cid,
                "window": win,
                "road_number": primary_rnum,
                "road_label": road_label,
                "direction": direction,
                "bearing": r.bearing,
                "xdgroup": r.xdgroup,
                "miles": round(miles, 3),
                "n_segments": segs,
                "n_qualifying": qual,
                "gaps_bridged": r.gaps_bridged,
                "gap_miles": round(r.gap_miles, 3),
                "mean_recurrence": round(mean_rec, 3),
                "mean_tti": round(mean_tti, 3),
                "start_segment": r.start_segment,
                "end_segment": r.end_segment,
                "start_lat": round(start_coords[0], 5),
                "start_lon": round(start_coords[1], 5),
                "end_lat": round(end_coords[0], 5),
                "end_lon": round(end_coords[1], 5),
                "decision": decision,
                "rule": rule,
                "matched_corridor": matched_corridor,
                "reason": reason,
            })

    return pd.DataFrame(triage_rows)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True, help="DuckDB store path (e.g. d1_store.duckdb)")
    parser.add_argument("--catalogue", default=None, help="Corridor catalogue JSON (optional, for M1 merge checks)")
    parser.add_argument("--repairs", default=None, help="Link repairs CSV (optional)")
    parser.add_argument("--network", default="USA_Idaho_shapefile.zip", help="XD network shapefile or GeoParquet")
    parser.add_argument("--network-cache", default=None, help="GeoParquet network cache path")
    parser.add_argument("--recurrence-cache", default=None, help="Parquet file with pre-computed recurrence")
    parser.add_argument("--out-dir", default="out/district_screening")
    parser.add_argument("--area", default=None, help="Area key or name (default: first area in store)")
    args = parser.parse_args()

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    print("Loading network...")
    if args.network_cache and Path(args.network_cache).exists():
        net = gpd.read_parquet(args.network_cache)
    else:
        net = geometry.load_xd_network(args.network, cache_path=args.network_cache)

    repairs = None
    if args.repairs and Path(args.repairs).exists():
        print(f"Loading repairs from {args.repairs}...")
        repairs = corridors.load_link_repairs(args.repairs)
        net = corridors.apply_link_repairs(net, repairs)

    con = store.connect(args.db)
    areas = store.list_areas(con)
    area_key = args.area or str(areas.iloc[0]["area_key"])

    cat_entries = None
    chains = None
    if args.catalogue and Path(args.catalogue).exists():
        print(f"Loading and resolving catalogue from {args.catalogue}...")
        cat_entries = corridors.load_catalogue(args.catalogue)
        observed = set(store.area_segments(con, area_key))
        res = corridors.resolve_catalogue(net, cat_entries, observed=observed, repairs=repairs)
        chains = res.attrs["chains"]

    # Load or compute recurrence
    if args.recurrence_cache and Path(args.recurrence_cache).exists():
        print(f"Reading cached recurrence from {args.recurrence_cache}...")
        rec = pd.read_parquet(args.recurrence_cache)
    else:
        print(f"Computing recurrence from DuckDB store for area {area_key}...")
        rec = screen.segment_recurrence(con, area_key, windows=["am", "pm"])

    print("Triaging all candidate runs...")
    triage_df = triage_candidates(rec, net, repairs, cat_entries, chains, recurrence=rec)

    csv_path = out_dir / "candidate_triage.csv"
    triage_df.to_csv(csv_path, index=False)
    print(f"Written triage CSV to {csv_path}")

    json_path = out_dir / "candidate_triage.json"
    triage_df.to_json(json_path, orient="records", indent=2)
    print(f"Written triage JSON to {json_path}")

    print("\n=== TRIAGE SUMMARY ===")
    print(triage_df["decision"].value_counts().to_string())
    print("\n=== DECISIONS BY RULE ===")
    print(triage_df.groupby(["decision", "rule"]).size().to_string())
    print("\n=== ACCEPTED CORRIDORS ===")
    accepted = triage_df[triage_df["decision"] == "ACCEPTED"]
    if len(accepted) > 0:
        print(accepted[["candidate_id", "window", "road_number", "miles",
                         "mean_tti", "mean_recurrence"]].to_string(index=False))
    else:
        print("  (none)")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

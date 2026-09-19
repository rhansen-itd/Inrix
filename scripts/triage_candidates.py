#!/usr/bin/env python3
"""Triage all recurring congestion candidate runs across District 3 (ROADMAP Item 44).

Runs Item 43 extraction over the on-system export in both AM and PM peak windows,
evaluates every candidate against the 18 reporting corridors (36 directional entries),
and outputs an auditable triage table recording acceptances, merges, and rejections
with explicit reasons.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import geopandas as gpd
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from inrix_tools import corridors, screen, store


def triage_candidates(rec: pd.DataFrame, net: gpd.GeoDataFrame, repairs, cat_entries,
                      chains: dict) -> pd.DataFrame:
    """Triage all candidate runs into ACCEPTED, MERGED, or REJECTED."""
    # Build segment-to-corridor membership map
    seg_to_corridors: dict[int, list[str]] = {}
    for entry in cat_entries:
        cid = entry.id
        ch = chains[cid]
        for sid in ch.segment_ids:
            seg_to_corridors.setdefault(int(sid), []).append(cid)

    # Core accepted runs that directly define a catalogue extent
    # (longest contiguous backbone runs for key corridors)
    CORE_ACCEPTED_RUN_CHECKS = {
        ("pm", "84", "W"): "i84-wb",
        ("am", "84", "E"): "i84-eb",
        ("pm", "55", "W"): "sh55-karcher-wb",
        ("am", "55", "E"): "sh55-karcher-eb",
        ("pm", "20", "N"): "front-wb",
        ("am", "20", "E"): "myrtle-eb",
        ("pm", "184", "W"): "i184-wb",
        ("am", "184", "E"): "i184-eb",
    }

    triage_rows = []
    for win in ["am", "pm"]:
        runs = screen.extract_congestion_runs(rec, net, window=win, repairs=repairs)
        cands = screen.emit_candidates(runs, net, repairs=repairs, window=win)

        for r, c in zip(runs, cands):
            cid = c["id"]
            rnums = list(r.road_numbers)
            primary_rnum = rnums[0] if rnums else ""
            direction = c["_direction"]
            miles = r.total_miles
            segs = r.n_segments
            qual = r.n_qualifying
            mean_rec = r.mean_recurrence
            mean_tti = r.mean_tti

            # Find overlapping catalogue corridors
            corridor_matches = set()
            for sid in r.segment_ids:
                for match_cid in seg_to_corridors.get(sid, []):
                    corridor_matches.add(match_cid)

            # Determine triage decision and reason
            decision = "REJECTED"
            matched_corridor = None
            reason = ""

            if corridor_matches:
                matched_corridor = ";".join(sorted(corridor_matches))
                check_key = (win, primary_rnum, r.bearing)
                target_cid = CORE_ACCEPTED_RUN_CHECKS.get(check_key)
                if target_cid and target_cid in corridor_matches and miles >= 1.0:
                    decision = "ACCEPTED"
                    reason = f"Primary empirical extent for {target_cid} ({miles:.2f} mi, TTI {mean_tti:.2f})"
                else:
                    decision = "MERGED"
                    reason = f"Contiguous congestion run merged into corridor extent {matched_corridor} across XDGroup boundaries or gap tolerance"
            else:
                # Rejections
                if not primary_rnum:
                    decision = "REJECTED"
                    reason = "Unnumbered or non-mainline road segment outside state highway network"
                elif miles < 0.25:
                    decision = "REJECTED"
                    reason = f"Isolated intersection/signal approach queue (< 0.25 mi, {miles:.2f} mi) on route {primary_rnum} without corridor extent"
                elif primary_rnum in ("78", "51", "71", "167", "30", "52", "67", "72", "95", "21", "19"):
                    decision = "REJECTED"
                    reason = f"Localized rural town/junction queue on route {primary_rnum} ({miles:.2f} mi in {r.road_label}); corridor free-flowing"
                elif "Ramp" in r.road_label or "IC" in r.road_label:
                    decision = "REJECTED"
                    reason = "Interchange ramp terminal or turnaround stub"
                else:
                    decision = "REJECTED"
                    reason = f"Isolated queue on route {primary_rnum} ({miles:.2f} mi) insufficient to form independent reporting corridor"

            triage_rows.append({
                "candidate_id": cid,
                "window": win,
                "road_number": primary_rnum,
                "road_label": r.road_label,
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
                "start_lat": round(c["start_latlon"][0], 5),
                "start_lon": round(c["start_latlon"][1], 5),
                "end_lat": round(c["end_latlon"][0], 5),
                "end_lon": round(c["end_latlon"][1], 5),
                "decision": decision,
                "matched_corridor": matched_corridor,
                "reason": reason,
            })

    return pd.DataFrame(triage_rows)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default="d3_store.duckdb")
    parser.add_argument("--catalogue", default="scripts/d3_corridors.json")
    parser.add_argument("--repairs", default="scripts/d3_link_repairs.csv")
    parser.add_argument("--network-cache", default="geometry_cache/d3_network.geoparquet")
    parser.add_argument("--recurrence-cache",
                        default="/home/hansrkid/.gemini/antigravity-cli/brain/d39f22f5-5da2-435d-86a1-4fce53e6a6f1/scratch/d3_recurrence.parquet")
    parser.add_argument("--out-dir", default="out/district_screening")
    args = parser.parse_args()

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    print("Loading network, repairs, and catalogue...")
    net = gpd.read_parquet(args.network_cache)
    repairs = corridors.load_link_repairs(args.repairs)
    cat_entries = corridors.load_catalogue(args.catalogue)

    con = store.connect(args.db)
    area_key = store.list_areas(con).iloc[0]["area_key"]
    observed = set(store.area_segments(con, area_key))

    res = corridors.resolve_catalogue(net, cat_entries, observed=observed, repairs=repairs)
    chains = res.attrs["chains"]

    # Load recurrence
    rec_path = Path(args.recurrence_cache)
    if rec_path.exists():
        print(f"Reading cached recurrence from {rec_path}...")
        rec = pd.read_parquet(rec_path)
    else:
        print("Computing recurrence from DuckDB store...")
        rec = screen.segment_recurrence(con, area_key, windows=["am", "pm"])

    print("Triaging all candidate runs...")
    triage_df = triage_candidates(rec, net, repairs, cat_entries, chains)

    csv_path = out_dir / "candidate_triage.csv"
    triage_df.to_csv(csv_path, index=False)
    print(f"Written triage CSV to {csv_path}")

    json_path = out_dir / "candidate_triage.json"
    triage_df.to_json(json_path, orient="records", indent=2)
    print(f"Written triage JSON to {json_path}")

    print("\n=== TRIAGE SUMMARY ===")
    print(triage_df["decision"].value_counts().to_string())
    print("\n=== REJECTIONS BY ROUTE ===")
    rejections = triage_df[triage_df["decision"] == "REJECTED"]
    print(rejections["road_number"].replace("", "UNNUMBERED").value_counts().to_string())

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

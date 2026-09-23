#!/usr/bin/env python3
"""Aggregate district corridor screening outputs into statewide rankings.

Reads the per-district peak and 7-day screening outputs from:
    out/statewide_screening/d{1..6}/corridor_peak_totals.csv
    out/statewide_screening/d{1..6}/corridor_7day_totals.csv

Produces:
1. out/statewide_screening/statewide_peak_corridor_rankings.csv
   - Statewide ranking of all monitored corridors by Peak Delay Density (VHD / Mile).
2. out/statewide_screening/statewide_7day_corridor_rankings.csv
   - Statewide ranking of all monitored corridors by 7-Day All-Day Delay Density (VHD / Mile).
3. out/statewide_screening/statewide_couplet_rankings.csv
   - Synthesis and comparison of all one-way couplet facilities statewide.
4. out/statewide_screening/statewide_extent_tiers_comparison.csv
   - Multi-scale extent tier comparison (Core Bottleneck vs Commuter Extent).
5. out/statewide_screening/statewide_district_summary.csv
   - District-level roll-up metrics (Total VHD, Monitored Miles, Top Corridors).
"""
from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

# Multi-scale extent tier pairings for comparison analysis
EXTENT_TIER_GROUPS = [
    {
        "facility": "I-84 (Treasure Valley)",
        "district": 3,
        "tiers": [
            ("i84", "Core Bottleneck (Nampa IC 35 to Boise IC 49)"),
            ("i84-full-valley", "Full Commuter Extent (Caldwell Exit 27 to East Boise Exit 59)"),
        ],
    },
    {
        "facility": "SH-55 (Treasure Valley to West Central Mountains)",
        "district": 3,
        "tiers": [
            ("sh55-eagle", "Urban Commercial Core (I-84 to State St)"),
            ("sh55-eagle-hsb", "Suburban/Foothill Commuter (Eagle to Horseshoe Bend)"),
            ("sh55-hsb-cascade", "Payette River Canyon (Horseshoe Bend to Cascade)"),
            ("sh55-cascade-mccall", "Long Valley Mountain Arterial (Cascade to McCall)"),
            ("sh55-mccall-town", "Resort Town Bottleneck (McCall Town Center)"),
        ],
    },
    {
        "facility": "SH-75 (Wood River Valley)",
        "district": 4,
        "tiers": [
            ("sh75-ketchum", "Core Resort Bottleneck (Ketchum Urban Core)"),
            ("sh75-hailey", "Commuter Bottleneck (Bellevue to Hailey)"),
            ("sh75-galena", "Rural Recreational Baseline (Galena Summit)"),
        ],
    },
    {
        "facility": "US-20 (Upper Snake River Valley)",
        "district": 6,
        "tiers": [
            ("us20-if-urban", "Urban Commercial Bypass (Idaho Falls Core)"),
            ("us20-if-rexburg", "Regional Commuter Expressway (Idaho Falls to Rexburg)"),
        ],
    },
    {
        "facility": "US-95 (District 2 / Palouse)",
        "district": 2,
        "tiers": [
            ("moscow-couplet", "Core Couplet Bottleneck (Washington/Jackson)"),
            ("us95-moscow", "Extended Urban Arterial (Moscow City Limits)"),
            ("us95-lewiston-hill", "Regional Arterial (Lewiston Hill Grade)"),
        ],
    },
    {
        "facility": "US-95 (District 1 / Panhandle)",
        "district": 1,
        "tiers": [
            ("us95-cda-hayden", "Core Commercial Bottleneck (CDA to Hayden)"),
            ("sandpoint-couplet", "Urban Couplet Bottleneck (Sandpoint 1st/5th)"),
            ("us95-bonners-ferry", "Northern Commercial Arterial (Bonners Ferry)"),
        ],
    },
]


def load_district_table(csv_path: Path, district: int) -> pd.DataFrame | None:
    """Load a corridor totals CSV, skipping comment lines."""
    if not csv_path.exists():
        return None
    df = pd.read_csv(csv_path, comment="#")
    if df.empty:
        return None
    df["district"] = district
    return df


def aggregate_rankings(
    base_dir: Path,
    filename: str,
    districts: list[int],
    rank_col: str = "vhd_per_mile",
) -> pd.DataFrame:
    """Combine district totals into a single statewide ranked DataFrame."""
    frames = []
    for d in districts:
        csv_p = base_dir / f"d{d}" / filename
        sub = load_district_table(csv_p, d)
        if sub is not None:
            frames.append(sub)

    if not frames:
        return pd.DataFrame()

    combined = pd.concat(frames, ignore_index=True)

    # Sort descending by ranking metric (nulls last)
    combined = combined.sort_values(
        by=rank_col, ascending=False, na_position="last"
    ).reset_index(drop=True)

    # Add statewide rank
    combined.insert(0, "statewide_rank", combined.index + 1)
    return combined


def build_couplet_analysis(
    peak_df: pd.DataFrame, day7_df: pd.DataFrame
) -> pd.DataFrame:
    """Synthesize metrics for all one-way couplets statewide."""
    # Look for couplet indicators (either one_way_couplet column or naming).
    # ``peak_df.get(col, False)`` returns a *scalar* when the column is absent, and
    # ``peak_df[False]`` raises KeyError rather than falling through — so test for
    # the column explicitly before using it as a mask.
    if "one_way_couplet" in peak_df.columns:
        p_couplets = peak_df[peak_df["one_way_couplet"].fillna(False).astype(bool)].copy()
    else:
        p_couplets = peak_df.iloc[0:0].copy()
    if p_couplets.empty and "group_name" in peak_df.columns:
        # Fallback to name pattern
        p_couplets = peak_df[
            peak_df["group_name"].astype(str).str.lower().str.contains("couplet")
        ].copy()

    if p_couplets.empty:
        return pd.DataFrame()

    d7_indexed = day7_df.set_index("corridor_group") if not day7_df.empty else None

    rows = []
    for _, r in p_couplets.iterrows():
        cid = r["corridor_group"]
        d7_row = d7_indexed.loc[cid] if (d7_indexed is not None and cid in d7_indexed.index) else None

        rows.append({
            "district": r["district"],
            "corridor_group": cid,
            "group_name": r.get("group_name", cid),
            "miles": r.get("miles", 0.0),
            "peak_rank": r.get("statewide_rank"),
            "peak_vhd_per_mile": r.get("vhd_per_mile", 0.0),
            "peak_vhd": r.get("vhd", 0.0),
            "peak_tti": r.get("tti", 1.0),
            "peak_speed_mph": r.get("speed", r.get("mean_speed", 0.0)),
            "day7_vhd_per_mile": d7_row.get("vhd_per_mile", 0.0) if d7_row is not None else None,
            "day7_vhd": d7_row.get("vhd", 0.0) if d7_row is not None else None,
            "day7_tti": d7_row.get("tti", 1.0) if d7_row is not None else None,
            "day7_speed_mph": d7_row.get("speed", d7_row.get("mean_speed", 0.0)) if d7_row is not None else None,
        })

    out = pd.DataFrame(rows)
    if not out.empty:
        out = out.sort_values(by="peak_vhd_per_mile", ascending=False).reset_index(drop=True)
        out.insert(0, "couplet_rank", out.index + 1)
    return out


def build_extent_tiers_analysis(
    peak_df: pd.DataFrame, day7_df: pd.DataFrame
) -> pd.DataFrame:
    """Analyze dilution and metric shifts across multi-scale extent tiers."""
    p_indexed = peak_df.set_index("corridor_group") if not peak_df.empty else None
    d7_indexed = day7_df.set_index("corridor_group") if not day7_df.empty else None

    rows = []
    for grp in EXTENT_TIER_GROUPS:
        facility = grp["facility"]
        dist = grp["district"]
        tiers = grp["tiers"]

        baseline_vhd_rate = None
        for cid, tier_label in tiers:
            p_row = p_indexed.loc[cid] if (p_indexed is not None and cid in p_indexed.index) else None
            d7_row = d7_indexed.loc[cid] if (d7_indexed is not None and cid in d7_indexed.index) else None

            if p_row is None:
                continue

            miles = p_row.get("miles", 0.0)
            vhd_rate = p_row.get("vhd_per_mile", 0.0)
            if baseline_vhd_rate is None:
                baseline_vhd_rate = vhd_rate
                retention_pct = 100.0
            else:
                retention_pct = (vhd_rate / baseline_vhd_rate * 100.0) if baseline_vhd_rate > 0 else 0.0

            rows.append({
                "facility": facility,
                "district": dist,
                "corridor_group": cid,
                "tier_scale": tier_label,
                "miles": miles,
                "peak_rank": p_row.get("statewide_rank"),
                "peak_vhd_per_mile": vhd_rate,
                "vhd_density_retention_pct": round(retention_pct, 1),
                "peak_vhd": p_row.get("vhd", 0.0),
                "peak_tti": p_row.get("tti", 1.0),
                "peak_speed_mph": p_row.get("speed", p_row.get("mean_speed", 0.0)),
                "day7_vhd_per_mile": d7_row.get("vhd_per_mile", 0.0) if d7_row is not None else None,
                "day7_tti": d7_row.get("tti", 1.0) if d7_row is not None else None,
            })

    return pd.DataFrame(rows)


def build_district_summary(
    peak_df: pd.DataFrame, day7_df: pd.DataFrame, districts: list[int]
) -> pd.DataFrame:
    """Summarize overall delay and top corridors per district."""
    rows = []
    for d in districts:
        p_sub = peak_df[peak_df["district"] == d] if not peak_df.empty else pd.DataFrame()
        d7_sub = day7_df[day7_df["district"] == d] if not day7_df.empty else pd.DataFrame()

        if p_sub.empty:
            continue

        n_corridors = len(p_sub)
        total_miles = p_sub["miles"].sum()
        total_peak_vhd = p_sub["vhd"].sum()
        total_7day_vhd = d7_sub["vhd"].sum() if not d7_sub.empty else 0.0

        top_peak = p_sub.iloc[0]
        top_7day = d7_sub.iloc[0] if not d7_sub.empty else None

        rows.append({
            "district": d,
            "monitored_reporting_corridors": n_corridors,
            "monitored_centerline_miles": round(total_miles, 2),
            "total_peak_vhd": round(total_peak_vhd, 1),
            "total_7day_vhd": round(total_7day_vhd, 1),
            "top_peak_corridor": top_peak.get("group_name", top_peak.get("corridor_group")),
            "top_peak_vhd_per_mile": round(top_peak.get("vhd_per_mile", 0.0), 1),
            "top_peak_tti": round(top_peak.get("tti", 1.0), 2),
            "top_7day_corridor": (top_7day.get("group_name", top_7day.get("corridor_group")) if top_7day is not None else "—"),
            "top_7day_vhd_per_mile": (round(top_7day.get("vhd_per_mile", 0.0), 1) if top_7day is not None else None),
            "top_7day_tti": (round(top_7day.get("tti", 1.0), 2) if top_7day is not None else None),
        })

    return pd.DataFrame(rows)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dir", default="out/statewide_screening",
                        help="Base directory containing d{1..6} screening outputs")
    parser.add_argument("--districts", nargs="*", type=int, default=[1, 2, 3, 4, 5, 6])
    args = parser.parse_args()

    base_dir = Path(args.dir)

    print("Aggregating statewide peak rankings...")
    peak_rankings = aggregate_rankings(
        base_dir, "corridor_peak_totals.csv", args.districts, rank_col="vhd_per_mile"
    )
    if not peak_rankings.empty:
        p_path = base_dir / "statewide_peak_corridor_rankings.csv"
        peak_rankings.to_csv(p_path, index=False)
        print(f"  -> Written {p_path} ({len(peak_rankings)} corridors)")

    print("Aggregating statewide 7-day all-day rankings...")
    day7_rankings = aggregate_rankings(
        base_dir, "corridor_7day_totals.csv", args.districts, rank_col="vhd_per_mile"
    )
    if not day7_rankings.empty:
        d7_path = base_dir / "statewide_7day_corridor_rankings.csv"
        day7_rankings.to_csv(d7_path, index=False)
        print(f"  -> Written {d7_path} ({len(day7_rankings)} corridors)")

    if not peak_rankings.empty:
        print("Generating statewide couplet synthesis...")
        couplets = build_couplet_analysis(peak_rankings, day7_rankings)
        if not couplets.empty:
            c_path = base_dir / "statewide_couplet_rankings.csv"
            couplets.to_csv(c_path, index=False)
            print(f"  -> Written {c_path} ({len(couplets)} couplets)")

        print("Generating multi-scale extent tiers comparison...")
        tiers = build_extent_tiers_analysis(peak_rankings, day7_rankings)
        if not tiers.empty:
            t_path = base_dir / "statewide_extent_tiers_comparison.csv"
            tiers.to_csv(t_path, index=False)
            print(f"  -> Written {t_path} ({len(tiers)} facility tiers)")

        print("Generating district summary roll-up...")
        dist_summary = build_district_summary(peak_rankings, day7_rankings, args.districts)
        if not dist_summary.empty:
            ds_path = base_dir / "statewide_district_summary.csv"
            dist_summary.to_csv(ds_path, index=False)
            print(f"  -> Written {ds_path}")

    print("\nStatewide aggregation complete!")


if __name__ == "__main__":
    main()

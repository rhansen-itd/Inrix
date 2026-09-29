#!/usr/bin/env python3
"""Compare statewide recreational corridor screening with weekday commute screening.

Compares:
- out/statewide_screening/statewide_peak_corridor_rankings.csv (Weekday Commute Peak)
- out/recreational_screening/statewide_rec_corridor_rankings.csv (Weekend Recreational 9am-9pm, May-Aug)

Outputs:
- out/recreational_screening/recreational_vs_commute_comparison.csv
- Detailed printout highlighting high-ratio recreational corridors, dual-demand routes,
  and directional Friday/Sunday dynamics.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def load_clean(path: Path) -> pd.DataFrame:
    """Load a ranking table skipping provenance comments."""
    with path.open() as fh:
        skip = 0
        for line in fh:
            if not line.startswith("#"):
                break
            skip += 1
    return pd.read_csv(path, skiprows=skip)


def compare_rankings(
    commute_csv: Path,
    rec_csv: Path,
    rec_dir: Path,
    districts: list[int] = (1, 2, 3, 4, 5, 6),
) -> pd.DataFrame:
    if not commute_csv.exists():
        raise FileNotFoundError(f"Commute rankings not found at {commute_csv}")
    if not rec_csv.exists():
        raise FileNotFoundError(f"Recreational rankings not found at {rec_csv}")

    commute = load_clean(commute_csv)
    rec = load_clean(rec_csv)

    # Standardize join key
    key = ["district", "corridor_group"]
    cols_commute = key + [
        "group_name", "miles", "statewide_rank", "vhd", "vhd_per_mile", "tti", "delay_min"
    ]
    cols_rec = key + [
        "group_name", "statewide_rank", "vhd", "vhd_per_mile", "tti", "delay_min"
    ]

    c_sub = commute[[c for c in cols_commute if c in commute.columns]].rename(
        columns={
            "statewide_rank": "commute_rank",
            "vhd": "commute_vhd",
            "vhd_per_mile": "commute_vhd_per_mile",
            "tti": "commute_tti",
            "delay_min": "commute_delay_min",
        }
    )

    r_sub = rec[[c for c in cols_rec if c in rec.columns]].rename(
        columns={
            "statewide_rank": "rec_rank",
            "vhd": "rec_vhd",
            "vhd_per_mile": "rec_vhd_per_mile",
            "tti": "rec_tti",
            "delay_min": "rec_delay_min",
        }
    )

    merged = pd.merge(r_sub, c_sub, on=key, how="outer", suffixes=("", "_c"))
    if "group_name_c" in merged.columns:
        merged["group_name"] = merged["group_name"].fillna(merged["group_name_c"])
        merged = merged.drop(columns=["group_name_c"])

    for tag in ["fri", "sat", "sun"]:
        tag_csv = rec_dir / f"statewide_{tag}_corridor_rankings.csv"
        if tag_csv.exists():
            tdf = load_clean(tag_csv)
            cols = [c for c in key + ["statewide_rank", "vhd", "vhd_per_mile"] if c in tdf.columns]
            tsub = tdf[cols].rename(
                columns={
                    "statewide_rank": f"{tag}_rank",
                    "vhd": f"{tag}_vhd_tot",
                    "vhd_per_mile": f"{tag}_vhd_per_mile",
                }
            )
            merged = pd.merge(merged, tsub, on=key, how="left")

    # Metrics
    merged["rec_vhd"] = pd.to_numeric(merged["rec_vhd"], errors="coerce")
    merged["commute_vhd"] = pd.to_numeric(merged["commute_vhd"], errors="coerce")
    merged["rec_vhd_per_mile"] = pd.to_numeric(merged["rec_vhd_per_mile"], errors="coerce")
    merged["commute_vhd_per_mile"] = pd.to_numeric(merged["commute_vhd_per_mile"], errors="coerce")

    # Ratio of recreational total VHD to weekday commute VHD
    merged["rec_to_commute_ratio"] = merged["rec_vhd"] / merged["commute_vhd"].where(merged["commute_vhd"] > 0)
    merged["rec_to_commute_density_ratio"] = merged["rec_vhd_per_mile"] / merged["commute_vhd_per_mile"].where(
        merged["commute_vhd_per_mile"] > 0
    )

    # Rank shift (positive means corridor ranks worse/higher priority on weekends than weekdays)
    merged["rank_shift_to_rec"] = merged["commute_rank"] - merged["rec_rank"]

    # Classification
    def classify(row):
        ratio = row["rec_to_commute_ratio"]
        r_vhd = row["rec_vhd"]
        c_vhd = row["commute_vhd"]
        if pd.isna(ratio):
            return "Recreational Only" if pd.notna(r_vhd) and r_vhd > 0 else "Commute Only"
        if ratio >= 1.4:
            return "Recreational Dominant"
        elif ratio <= 0.7:
            return "Commuter Dominant"
        else:
            return "Balanced / Dual-Peak"

    merged["classification"] = merged.apply(classify, axis=1)

    # Pull directional Friday vs Sunday dynamics from district breakout files
    breakouts = []
    for d in districts:
        bo_file = rec_dir / f"d{d}" / "corridor_rec_breakout.csv"
        if bo_file.exists():
            bdf = load_clean(bo_file)
            bdf["district"] = d
            breakouts.append(bdf)

    if breakouts:
        all_bo = pd.concat(breakouts, ignore_index=True)
        # Summarize by corridor and window
        piv = all_bo.pivot_table(
            index=["district", "corridor_group"],
            columns="window",
            values="vhd",
            aggfunc="sum",
        ).reset_index()
        for w in ["fri", "sat", "sun"]:
            if w in piv.columns:
                piv = piv.rename(columns={w: f"vhd_{w}"})
        merged = pd.merge(merged, piv, on=["district", "corridor_group"], how="left")

    return merged.sort_values(
        by=["rec_vhd_per_mile"], ascending=False, na_position="last"
    ).reset_index(drop=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--commute", default="out/statewide_screening/statewide_peak_corridor_rankings.csv")
    parser.add_argument("--rec", default="out/recreational_screening/statewide_rec_corridor_rankings.csv")
    parser.add_argument("--rec-dir", default="out/recreational_screening")
    parser.add_argument("--out-csv", default="out/recreational_screening/recreational_vs_commute_comparison.csv")
    parser.add_argument("--top", type=int, default=25)
    args = parser.parse_args()

    commute_p = Path(args.commute)
    rec_p = Path(args.rec)
    rec_dir = Path(args.rec_dir)

    df = compare_rankings(commute_p, rec_p, rec_dir)
    out_csv = Path(args.out_csv)
    out_csv.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(out_csv, index=False)
    print(f"Written comparison to {out_csv} ({len(df)} corridors)")

    print("\n" + "=" * 95)
    print("TOP CORRIDORS BY RECREATIONAL DELAY DENSITY (VHD / MILE, MAY-AUG)")
    print("=" * 95)
    show_cols = [
        "rec_rank", "commute_rank", "rank_shift_to_rec", "district",
        "group_name", "miles", "rec_vhd", "commute_vhd", "rec_to_commute_ratio", "classification"
    ]
    present_cols = [c for c in show_cols if c in df.columns]
    print(df[present_cols].head(args.top).to_string(index=False))

    print("\n" + "=" * 95)
    print("TOP RECREATIONAL-DOMINANT CORRIDORS (HIGHEST RECREATIONAL / COMMUTE VHD RATIO)")
    print("=" * 95)
    rec_dom = df[df["commute_vhd"] > 50].sort_values("rec_to_commute_ratio", ascending=False)
    print(rec_dom[present_cols].head(15).to_string(index=False))


if __name__ == "__main__":
    main()

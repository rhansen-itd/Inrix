#!/usr/bin/env python3
"""Fit volume-profile curves from traffic counts.  (ROADMAP Item 59)

Wiring only — :mod:`inrix_tools.counts` imports and fits. Reads ITD ATR hourly
volumes (TCDS report 87, flattened by ``tcds-scraper/tidy.py``) and/or count CSVs
already in the Item 59 schema (``counts.write_counts``), fits one curve per
station-direction, and writes what ``run_district_screening.py --count-profiles``
reads::

    out/count_profiles/
      fitted_profiles.json   the fitted curves (the package library schema)
      count_stations.csv     one row per station-direction: the station rule's table
      fit_report.csv         per curve: usable/dropped days, what was borrowed, the
                             nearest generic curve and how far, peak-hour share, DOW
      counts.csv             the imported counts in the schema

Typical run (the April 2026 ATR pull plus the older-month fallbacks)::

    python scripts/fit_count_profiles.py \\
        --tcds-hourly data/atr/2026-04/atr_2026-04_hourly.csv \\
                      data/atr/fallback/atr_fallback_hourly.csv \\
        --tcds-stations data/atr/atr_stations_statewide.csv

A station in both files keeps the first file's rows (list the current month first).
TCDS hours are the station's local wall clock; the zone comes from its ITD district
(D1/D2 Pacific, D3–D6 Mountain).
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from inrix_tools import counts, volume_profiles  # noqa: E402

DISTRICT_TZ = {1: "America/Los_Angeles", 2: "America/Los_Angeles", 3: "America/Boise",
               4: "America/Boise", 5: "America/Boise", 6: "America/Boise"}
DEFAULT_OUT = "out/count_profiles"


def load_tcds(paths, stations_path) -> pd.DataFrame:
    """Every TCDS hourly file → schema counts, each district in its own zone. A
    station already read from an earlier file is skipped in later ones."""
    stations = pd.read_csv(stations_path, dtype={"local_id": str})
    seen: set[str] = set()
    parts = []
    for path in paths:
        hourly = pd.read_csv(path, dtype={"series_id": str})
        hourly["_station"] = hourly["series_id"].str.split("_").str[0].str.zfill(5)
        hourly = hourly[~hourly["_station"].isin(seen)]
        for district, rows in hourly.groupby("district"):
            tz = DISTRICT_TZ[int(district)]
            got = counts.import_tcds_hourly(rows, stations, tz)
            parts.append(got)
            print(f"  {Path(path).name} D{district} ({tz}): "
                  f"{got['station_id'].nunique()} stations, {len(got):,} hours"
                  + (f"; 2-way only {got.attrs['tcds']['two_way_only']}"
                     if got.attrs["tcds"]["two_way_only"] else ""))
        seen |= set(hourly["_station"])
    return parts


def fit_report(fitted, stations, library) -> pd.DataFrame:
    """One row per fitted curve: what went in and how it compares."""
    clusters = counts.cluster_profiles(fitted, library)
    rows = []
    for cid, prof in fitted.items():
        st = prof.provenance["station"]
        wk = np.asarray(prof.hourly["weekday"])
        rows.append({
            "curve_id": cid, "station_id": st["station_id"], "direction": st["direction"],
            "route": st["route"], "period_start": st["period_start"],
            "period_end": st["period_end"],
            **{f"days_{t}": st["usable_days"][t] for t in volume_profiles.DAY_TYPES},
            "dropped_days": len(st["dropped_days"]),
            "dropped": "; ".join(f"{d} {r}" for d, r in st["dropped_days"].items()),
            "borrowed": ";".join(prof.provenance["borrowed"]),
            "borrowed_from": prof.provenance["borrowed_from"] or "",
            "mean_daily_volume": st["mean_daily_volume"],
            "weekday_peak_hour": int(wk.argmax()),
            "weekday_peak_share": round(float(wk.max()), 4),
            "am_share_7_9": round(float(wk[7:9].sum()), 4),
            "pm_share_16_18": round(float(wk[16:18].sum()), 4),
            **{f"dow_{d}": round(v, 3) for d, v in
               zip(("mon", "tue", "wed", "thu", "fri", "sat", "sun"), prof.dow)},
            **clusters.loc[cid].to_dict(),
        })
    return pd.DataFrame(rows)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--tcds-hourly", nargs="*", default=[],
                   help="TCDS report-87 hourly CSVs from tidy.py (earlier files win)")
    p.add_argument("--tcds-stations", default="data/atr/atr_stations_statewide.csv",
                   help="the TCDS station list (local_id, lat, lon, on, at)")
    p.add_argument("--counts", nargs="*", default=[],
                   help="count CSVs already in the Item 59 schema (counts.write_counts)")
    p.add_argument("--counts-tz", default="America/Boise",
                   help="the zone --counts timestamps are shown in")
    p.add_argument("--out-dir", default=DEFAULT_OUT)
    args = p.parse_args(argv)
    if not args.tcds_hourly and not args.counts:
        p.error("give --tcds-hourly and/or --counts")

    parts = load_tcds(args.tcds_hourly, args.tcds_stations) if args.tcds_hourly else []
    parts += [counts.read_counts(c, args.counts_tz) for c in args.counts]
    # Fitting reads each station's own wall clock, so frames in different zones are
    # fitted separately and only their outputs are combined.
    library = volume_profiles.load_profiles()
    fitted, tables, unfitted = {}, [], {}
    for part in parts:
        f, st = counts.station_curves(part, library)
        fitted.update(f)
        tables.append(st)
        unfitted.update(st.attrs["unfitted"])
    stations = pd.concat(tables, ignore_index=True)

    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    volume_profiles.write_profiles(
        fitted, out / "fitted_profiles.json",
        note="Volume-profile curves fitted from traffic counts by "
             "scripts/fit_count_profiles.py (ROADMAP Item 59); each curve's provenance "
             "names its station, period, usable days and anything borrowed.")
    counts.write_stations(stations, out / "count_stations.csv")
    report = fit_report(fitted, stations, library)
    report.to_csv(out / "fit_report.csv", index=False)
    all_counts = pd.concat([c.assign(timestamp=c["timestamp"].map(lambda t: t.isoformat()))
                            for c in parts], ignore_index=True)
    all_counts.to_csv(out / "counts.csv", index=False)

    print(f"\n{len(fitted)} curves from {stations['station_id'].nunique()} stations "
          f"-> {out}/")
    print("nearest generic:", report["nearest_generic"].value_counts().to_dict())
    print(f"misplaced share to the nearest generic: median "
          f"{report['misplaced_share'].median():.3f}, max "
          f"{report['misplaced_share'].max():.3f}")
    if unfitted:
        print("not fitted:", unfitted)
    return 0


if __name__ == "__main__":
    sys.exit(main())

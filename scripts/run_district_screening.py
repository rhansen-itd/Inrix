#!/usr/bin/env python3
"""Run the district-wide corridor screening end to end.  (ROADMAP Item 39)

Wiring only. Every number comes from ``inrix_tools`` — this script opens the store,
calls the core in order, and writes the results out. It computes no statistics of its
own and has no hardcoded paths.

The pipeline is the one Items 34-38 built, composed for the first time::

    store.connect
      -> screen.segment_screen          one row per segment, per named window
      -> corridors.resolve_catalogue    the corridor extents, walked and accounted
      -> aadt.load_aadt / join_aadt     mainline-preferred volume weights (Item 34)
      -> screen.rank_corridors          one row per corridor x window

Typical run (paths are this machine's; nothing here assumes them)::

    python scripts/run_district_screening.py \
        --db d3_store.duckdb \
        --catalogue scripts/d3_corridors.json \
        --repairs scripts/d3_link_repairs.csv \
        --network-cache geometry_cache/d3_network.geoparquet \
        --aadt Cumulative_AADT.zip \
        --out-dir out/district_screening

**A corridor that does not resolve is not ranked.** It is reported in its own section
with its ``stop_reason`` and coverage, never carried into the ranking with blank
metrics — the same rule Item 36 set for the catalogue, applied to the output.

Needs the ``geo`` extra (the chains are assembled from the XD shapefile).
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
from inrix_tools import corridors, geometry, kml, screen, store  # noqa: E402
from inrix_tools.io import DEFAULT_TZ                     # noqa: E402

DEFAULT_CVALUE = 80
BBOX_MARGIN_DEG = 0.02

# 20 hues for the corridor KML. The default categorical palette holds 12 and raises
# past it (Item 37) — a 20-corridor map is exactly the case that guard exists for, so
# the palette is passed explicitly rather than by letting anything wrap.
CORRIDOR_PALETTE = [
    "#e6194b", "#3cb44b", "#ffe119", "#4363d8", "#f58231",
    "#911eb4", "#46f0f0", "#f032e6", "#bcf60c", "#fabebe",
    "#008080", "#e6beff", "#9a6324", "#fffac8", "#800000",
    "#aaffc3", "#808000", "#ffd8b1", "#000075", "#a9a9a9",
]


# ---------------------------------------------------------------------------
# The pipeline
# ---------------------------------------------------------------------------
def resolve_area(con, area: str | None) -> str:
    """The area key to screen: the only one, or the one named (key or name)."""
    areas = store.list_areas(con)
    if areas.empty:
        raise SystemExit("The store holds no areas — ingest an export first.")
    if area is None:
        if len(areas) > 1:
            raise SystemExit(
                "The store holds several areas; pass --area. Available: "
                + ", ".join(f"{r.area_key} ({r.area_name})" for r in areas.itertuples()))
        return str(areas.iloc[0]["area_key"])
    hit = areas[(areas["area_key"] == area) | (areas["area_name"] == area)]
    if hit.empty:
        raise SystemExit(f"No area {area!r}; available: "
                         + ", ".join(areas["area_key"].astype(str)))
    return str(hit.iloc[0]["area_key"])


def load_repairs(path, enabled: bool):
    """The topology patch table, or ``None`` when the run is asked to walk on
    ``NextXDSegI`` exactly as published (Item 38)."""
    if not enabled:
        return None
    p = Path(path)
    if not p.exists():
        raise SystemExit(f"No repair table at {p} — pass --no-repairs to walk without one.")
    return corridors.load_link_repairs(p)


def screen_segments(con, area_key, *, windows, cvalue_threshold, bin_minutes, tz,
                    date_start, date_end) -> pd.DataFrame:
    return screen.segment_screen(
        con, area_key, windows=windows, cvalue_threshold=cvalue_threshold,
        bin_minutes=bin_minutes, tz=tz, date_start=date_start, date_end=date_end)


def load_network(source, cache_path=None):
    """The XD network. ``geometry.load_xd_network`` reads a GeoParquet only through
    ``cache_path``, so a ``--network`` that *is* one is handed over as the cache —
    otherwise it goes to the shapefile reader and fails on a parquet file."""
    if cache_path is None and Path(source).suffix.lower() in (".parquet", ".geoparquet"):
        cache_path = source
    return geometry.load_xd_network(source, cache_path=cache_path)


def resolve_corridors(network_source, catalogue_path, observed, *, repairs,
                      network_cache=None, min_coverage=corridors.DEFAULT_MIN_COVERAGE):
    """Resolve the catalogue against the network, judged on this export's coverage."""
    net = load_network(network_source, network_cache)
    catalogue = corridors.load_catalogue(catalogue_path)
    return net, corridors.resolve_catalogue(
        net, catalogue, observed=observed, repairs=repairs, min_coverage=min_coverage)


def corridor_geometry(net, resolution, chains):
    """One row per chain member of every **accepted** corridor, carrying its
    corridor id — the frame the AADT join and the KML both read."""
    accepted = resolution.loc[resolution["accepted"], "id"]
    rows = []
    for cid in accepted:
        for seq, sid in enumerate(chains[cid].segment_ids, start=1):
            rows.append({"Segment ID": int(sid), "corridor": cid, "sequence": seq})
    members = pd.DataFrame(rows)
    if members.empty:
        return members
    import geopandas as gpd

    geo = geometry.segment_geometry(net, segment_ids=members["Segment ID"].unique())
    # A segment can belong to two corridors (a split corridor shares no members, but
    # nothing forbids it); keep one geometry row per (corridor, segment). The merge
    # yields a plain DataFrame, so the geometry column is re-declared — otherwise
    # ``total_bounds`` and the KML writer both see an ordinary Series.
    merged = members.merge(geo, left_on="Segment ID", right_index=True, how="left")
    return gpd.GeoDataFrame(merged, geometry="geometry", crs=geo.crs)


def join_volumes(geo, aadt_source, *, year, cache_path, max_distance_m, bbox_margin):
    """Mainline-preferred AADT for the corridor members (Item 34)."""
    if aadt_source is None:
        return None
    bounds = geo["geometry"].dropna().total_bounds
    bbox = (bounds[0] - bbox_margin, bounds[1] - bbox_margin,
            bounds[2] + bbox_margin, bounds[3] + bbox_margin)
    layer = aadt_mod.load_aadt(aadt_source, year=year, bbox=bbox, cache_path=cache_path)
    unique = geo.drop_duplicates(subset="Segment ID").set_index("Segment ID")
    return aadt_mod.join_aadt(unique, layer, max_distance_m=max_distance_m)


def provenance(args, area_key, con, screen_frame, resolution, repairs, aadt) -> dict:
    """What this ranking rests on. A ranking with no stated basis cannot be handed to
    anyone, so it travels with the CSV rather than only with the log."""
    areas = store.list_areas(con)
    row = areas[areas["area_key"] == area_key].iloc[0]
    a = screen_frame.attrs
    out = {
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "db": str(args.db),
        "area_key": area_key,
        "area_name": str(row["area_name"]),
        "bin_minutes": a.get("bin_minutes"),
        "tz": a.get("tz"),
        "cvalue_threshold": a.get("cvalue_threshold"),
        "date_start": str(a.get("date_start")),
        "date_end": str(a.get("date_end")),
        "windows": {k: dict(v) if isinstance(v, dict) else str(v)
                    for k, v in (a.get("windows") or {}).items()},
        "n_segments_screened": int(len(screen_frame)),
        "n_obs": int(screen_frame["n_obs"].sum()),
        "n_obs_ungated": int(screen_frame["n_obs_ungated"].sum()),
        "catalogue": str(args.catalogue),
        "n_entries": resolution.attrs["n_entries"],
        "n_accepted": resolution.attrs["n_accepted"],
        "findings": list(resolution.attrs["findings"]),
        "min_coverage": resolution.attrs["min_coverage"],
        "repairs": (None if repairs is None else {
            "table": str(args.repairs),
            "n_repairs": int(len(repairs)),
            "n_used": int(resolution["n_repaired_links"].sum()),
            "rule": {k: v for k, v in repairs.attrs.items() if k != "source"},
        }),
        "aadt": None,
    }
    if aadt is not None:
        joined = aadt.attrs.get("aadt_join", {})
        out["aadt"] = {
            "source": str(args.aadt), "year": args.aadt_year,
            "policy": {k: v for k, v in joined.items() if k != "caveat"},
            "caveat": joined.get("caveat"),
        }
    return out


def write_outputs(out_dir, ranking, resolution, prov, geo, *, grouped=None,
                  totals=None, breakout=None, write_kml=True) -> dict:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    written = {}

    prov_path = out_dir / "screening_provenance.json"
    prov_path.write_text(json.dumps(prov, indent=2, default=str) + "\n")
    written["provenance"] = prov_path

    header = "".join(f"# {k}: {json.dumps(v, default=str)}\n" for k, v in prov.items())
    tables = [("corridor_rankings.csv", ranking, False),
              ("corridor_resolution.csv", resolution, False)]
    if grouped is not None:
        tables.insert(0, ("reporting_corridor_rankings.csv", grouped, False))
    if breakout is not None:
        tables.insert(0, ("corridor_breakout.csv", breakout, True))
    if totals is not None:
        tables.insert(0, ("corridor_peak_totals.csv", totals, False))
    for name, frame, with_index in tables:
        path = out_dir / name
        with path.open("w") as fh:
            fh.write(header)
            frame.to_csv(fh, index=with_index)
        written[name.split(".")[0]] = path

    if write_kml and not geo.empty:
        path = out_dir / "corridors.kml"
        drawable = geo.dropna(subset=["geometry"]).set_index("Segment ID")
        kml.geometry_to_kml(drawable, path, color_by="corridor", folder_by="corridor",
                            palette=CORRIDOR_PALETTE,
                            document_name="District screening corridors")
        written["kml"] = path
    return written


def _worst_rows(frame, key, top):
    worst = frame[frame["window"] == frame["worst_peak"]].copy()
    worst = worst.sort_values("vhd", ascending=False, na_position="last").head(top)
    width = max([len(str(c)) for c in worst[key]] + [8]) + 2
    return worst, width


def summarise(ranking: pd.DataFrame, resolution: pd.DataFrame, top: int,
              grouped: pd.DataFrame | None = None, totals: pd.DataFrame | None = None,
              breakout: pd.DataFrame | None = None) -> str:
    """The reporting table.

    One block per reporting corridor — the road, totalled over every peak window and
    both directions, which is the row it is ranked on — with **every direction x peak
    cell printed beneath it**, because a total that cannot be opened up is a number
    taken on trust. Ranked on delay per mile by default: a total rewards length, and
    a 17-mile rural highway will out-total a saturated one-mile couplet leg that is
    four times worse to drive.
    """
    lines = []
    if totals is not None and breakout is not None:
        wins = "+".join(totals.attrs["windows"])
        metric = totals.attrs["rank_by"]
        lines.append(f"District reporting corridors — totalled over {wins}, both "
                     f"directions, ranked on {metric}")
        lines.append(f"{'#':>3}  {'corridor':<32}{'mi':>8}{'delay':>8}{'TTI':>6}"
                     f"{'d/mi':>7}{'veh-hrs':>10}{'vh/mi':>9}{'also ranks':>13}")
        lines.append(f"{'':>3}  {'':<32}{'':>8}{'':>8}{'':>6}{'':>7}{'':>10}{'':>9}"
                     f"{'vh':>6}{'d/mi':>7}")
        for r in totals.itertuples():
            vhd = "n/a" if pd.isna(r.vhd) else f"{r.vhd:,.0f}"
            # A rank is <NA> when its metric is — no AADT joined means no vhd and so
            # no vhd ranking, which is reported rather than printed as a zero.
            vhmi = "n/a" if pd.isna(r.vhd_per_mile) else f"{r.vhd_per_mile:,.0f}"
            rank = "—" if pd.isna(r.rank) else f"{int(r.rank)}"
            rank_vhd = "—" if pd.isna(r.rank_vhd) else f"{int(r.rank_vhd)}"
            rank_dmi = ("—" if pd.isna(r.rank_delay_per_mile)
                        else f"{int(r.rank_delay_per_mile)}")
            label = f"{r.corridor_group}{' (couplet)' if r.one_way_couplet else ''}"
            lines.append(
                f"{rank:>3}  {label:<32}{r.directional_miles:>8.2f}"
                f"{r.delay_min:>8.2f}{r.tti:>6.2f}{r.delay_per_mile:>7.2f}{vhd:>10}"
                f"{vhmi:>9}{rank_vhd:>6}{rank_dmi:>7}")
            lines.append(f"{'':>5}{r.group_name}")
            cells = breakout.loc[r.corridor_group]
            for (direction, window), c in cells.iterrows():
                cvhd = "n/a" if pd.isna(c["vhd"]) else f"{c['vhd']:,.0f}"
                cvhmi = ("n/a" if pd.isna(c["vhd_per_mile"])
                         else f"{c['vhd_per_mile']:,.0f}")
                lines.append(
                    f"{'':>3}  {'':<6}{str(direction):<4}{str(window):<22}"
                    f"{c['miles']:>8.2f}{c['delay_min']:>8.2f}{c['tti']:>6.2f}"
                    f"{c['delay_per_mile']:>7.2f}{cvhd:>10}{cvhmi:>9}")
        lines.append("")
        lines.append("  The corridor row TOTALS its cells: delay, veh-hrs and the travel-time")
        lines.append("  components are summed over every direction x peak. 'mi' on that row is")
        lines.append("  directional miles — each direction counted once, so it is the distance a")
        lines.append("  round trip covers, and every per-mile rate divides the summed delay by it.")
        lines.append("  TTI is recomputed from the summed components, never averaged.")
        lines.append("  Ranked on vh/mi: vehicle-hours of delay per mile. It keeps the volume")
        lines.append("  weighting that veh-hrs has and drops the length reward — 'also ranks'")
        lines.append("  shows where each corridor would sit on the bare total (vh) and on the")
        lines.append("  unweighted rate (d/mi), so the three orderings can be compared.")
        lines.append("  (couplet) marks a corridor whose directions are different streets, so its")
        lines.append("  directional miles are distinct pavement rather than the same ground twice.")
        lines.append("")

    if totals is None:
        # No reporting corridors declared: the per-direction table is the whole
        # report, as it was before Item 40. Without this an ungrouped catalogue
        # prints nothing but its findings.
        worst, width = _worst_rows(ranking, "corridor", top)
        lines.append(f"By direction — top {len(worst)}, each at its own worst peak window")
        lines.append(f"{'corridor':<{width}}{'win':>5}{'mi':>7}{'delay':>8}{'TTI':>6}"
                     f"{'d/mi':>7}{'veh-hrs':>10}")
        for r in worst.itertuples():
            vhd = "n/a" if pd.isna(r.vhd) else f"{r.vhd:,.0f}"
            lines.append(f"{str(r.corridor):<{width}}{r.window:>5}{r.miles:>7.2f}"
                         f"{r.delay_min:>8.2f}{r.tti:>6.2f}{r.delay_per_mile:>7.2f}"
                         f"{vhd:>10}")
        lines.append("")

    if grouped is not None:
        worst, width = _worst_rows(grouped, "corridor_group", top)
        lines.append("Same roads at their single worst peak window (not totalled)")
        lines.append(f"{'corridor':<{width}}{'win':>5}{'mi':>7}{'peak':>6}{'TTI':>6}"
                     f"{'d/mi':>7}{'veh-hrs':>10}")
        for r in worst.itertuples():
            vhd = "n/a" if pd.isna(r.vhd) else f"{r.vhd:,.0f}"
            lines.append(
                f"{str(r.corridor_group):<{width}}{r.window:>5}{r.miles:>7.2f}"
                f"{str(r.peak_direction):>6}{r.tti:>6.2f}"
                f"{r.delay_per_mile:>7.2f}{vhd:>10}")
        lines.append("")

    findings = resolution[~resolution["accepted"]]
    if len(findings):
        lines.append(f"NOT RANKED — {len(findings)} entr"
                     f"{'y' if len(findings) == 1 else 'ies'} did not resolve:")
        for r in findings.itertuples():
            cov = "" if pd.isna(getattr(r, "miles_covered_fraction", float("nan"))) \
                else f", covered {r.miles_covered_fraction:.3f}"
            lines.append(f"  {r.id}: {r.stop_reason}{cov}")
    return "\n".join(lines)


def run(args) -> dict:
    con = store.connect(args.db)
    try:
        area_key = resolve_area(con, args.area)
        repairs = load_repairs(args.repairs, not args.no_repairs)
        windows = args.windows or screen.PEAK_WINDOWS
        scr = screen_segments(
            con, area_key, windows=windows, cvalue_threshold=args.cvalue_threshold,
            bin_minutes=args.bin_minutes, tz=args.tz,
            date_start=args.date_start, date_end=args.date_end)

        net, resolution = resolve_corridors(
            args.network, args.catalogue, scr.index, repairs=repairs,
            network_cache=args.network_cache, min_coverage=args.min_coverage)
        chains = resolution.attrs["chains"]
        accepted = {cid: chains[cid]
                    for cid in resolution.loc[resolution["accepted"], "id"]}
        if not accepted:
            raise SystemExit("No catalogue entry resolved — nothing to rank. "
                             f"Findings: {resolution.attrs['findings']}")

        geo = corridor_geometry(net, resolution, chains)
        aadt = join_volumes(geo, args.aadt, year=args.aadt_year,
                            cache_path=args.aadt_cache,
                            max_distance_m=args.aadt_max_distance_m,
                            bbox_margin=BBOX_MARGIN_DEG)

        # Keyed on the catalogue **id**, not the name: ids are short and unique,
        # where two names can share their first 20 characters ("SH-44 (State St)
        # WB: ..." names two different halves of one corridor). The full name rides
        # along as its own column so the CSV still says what each id is.
        ranking = screen.rank_corridors(scr, accepted, aadt)
        names = dict(zip(resolution["id"], resolution["name"]))
        ranking.insert(1, "corridor_name", ranking["corridor"].map(names))

        # Both directions of one road are one *reporting* corridor (Item 40). The
        # directional table above is not replaced by it — a district reads the road,
        # then asks which direction and which peak, and both need to be on the page.
        groups = corridors.load_reporting_corridors(args.catalogue)
        grouped = totals = breakout = None
        if groups:
            membership = resolution.loc[resolution["accepted"],
                                        ["id", "corridor", "direction"]]
            gnames = {g.id: g.name for g in groups}
            grouped = screen.rank_corridor_groups(ranking, membership, names=gnames)
            # The reporting table: totalled over every peak and both directions, and
            # ranked on a per-mile rate so length does not decide the order.
            couplets = [g.id for g in groups if g.one_way_couplet]
            totals = screen.corridor_peak_totals(
                ranking, membership, names=gnames, rank_by=args.rank_by,
                couplets=couplets)
            if totals.attrs["rank_metric_all_null"]:
                # The volume-weighted metrics are NaN without an AADT join, and a
                # table of <NA> ranks is not a ranking. Fall back to the unweighted
                # rate and say so, rather than presenting an order that is really
                # just catalogue order.
                fallback = "delay_per_mile"
                print(f"note: {args.rank_by!r} is null for every corridor (no AADT "
                      f"joined?) — ranking on {fallback!r} instead.")
                totals = screen.corridor_peak_totals(
                    ranking, membership, names=gnames, rank_by=fallback,
                    couplets=couplets)
            breakout = screen.corridor_breakout(
                ranking, membership, names=gnames,
                order=totals[screen.GROUP_COL].tolist())

        prov = provenance(args, area_key, con, scr, resolution, repairs, aadt)
        if grouped is not None:
            prov["reporting_corridors"] = {
                "n_groups": int(grouped[screen.GROUP_COL].nunique()),
                "ungrouped_entries": grouped.attrs["ungrouped"],
                "miles_basis": grouped.attrs["miles_basis_group"],
                "ranked_on": totals.attrs["rank_by"],
                "totalled_windows": list(totals.attrs["windows"]),
                "totals_miles_basis": totals.attrs["miles_basis"],
                "one_way_couplets": [g.id for g in groups if g.one_way_couplet],
            }
        written = write_outputs(args.out_dir, ranking, resolution, prov, geo,
                                grouped=grouped, totals=totals, breakout=breakout,
                                write_kml=not args.no_kml)
        return {"ranking": ranking, "grouped": grouped, "totals": totals,
                "breakout": breakout, "resolution": resolution,
                "provenance": prov, "written": written, "screen": scr}
    finally:
        con.close()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--db", required=True, help="DuckDB store (store.connect)")
    p.add_argument("--area", default=None, help="area key or name (default: the only one)")
    p.add_argument("--bin-minutes", type=int, default=None)
    p.add_argument("--catalogue", default="scripts/d3_corridors.json")
    p.add_argument("--repairs", default="scripts/d3_link_repairs.csv")
    p.add_argument("--no-repairs", action="store_true",
                   help="walk NextXDSegI exactly as published (Item 38 off)")
    p.add_argument("--network", default="USA_Idaho_shapefile.zip")
    p.add_argument("--network-cache", default=None)
    p.add_argument("--aadt", default=None, help="AADT source; omitted leaves vhd NaN")
    p.add_argument("--aadt-cache", default=None)
    p.add_argument("--aadt-year", type=int, default=aadt_mod.DEFAULT_YEAR)
    p.add_argument("--aadt-max-distance-m", type=float, default=60.0)
    p.add_argument("--cvalue-threshold", type=float, default=DEFAULT_CVALUE,
                   help="keep CValue > threshold; 'none' disables the gate")
    p.add_argument("--windows", default=None,
                   help="comma-separated window names (default: every PEAK_WINDOWS preset)")
    p.add_argument("--date-start", default=None)
    p.add_argument("--date-end", default=None)
    p.add_argument("--tz", default=DEFAULT_TZ)
    p.add_argument("--min-coverage", type=float, default=corridors.DEFAULT_MIN_COVERAGE)
    p.add_argument("--out-dir", default="out/district_screening")
    p.add_argument("--no-kml", action="store_true")
    p.add_argument("--top", type=int, default=20)
    p.add_argument("--rank-by", default=screen.DEFAULT_RANK_METRIC,
                   choices=screen.RANK_METRICS,
                   help="what the reporting table is ranked on (default: vehicle-hours "
                        "of delay per mile — volume-weighted, but length does not "
                        "decide the order)")
    return p


def parse_args(argv=None):
    args = build_parser().parse_args(argv)
    if isinstance(args.cvalue_threshold, float) and args.cvalue_threshold < 0:
        args.cvalue_threshold = None
    if args.windows:
        args.windows = {n: screen.PEAK_WINDOWS[n]
                        for n in (w.strip() for w in args.windows.split(","))
                        if n in screen.PEAK_WINDOWS} or None
    return args


def main(argv=None) -> int:
    args = parse_args(argv)
    result = run(args)
    print(summarise(result["ranking"], result["resolution"], args.top,
                    result.get("grouped"), result.get("totals"),
                    result.get("breakout")))
    print("\nWritten:")
    pad = max(len(k) for k in result["written"]) + 2
    for k, v in result["written"].items():
        print(f"  {k:<{pad}}{v}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

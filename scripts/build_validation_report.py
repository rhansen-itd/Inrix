#!/usr/bin/env python3
"""Build the INRIX-vs-reference validation report.  (ROADMAP Item 30)

Wiring only. Every number in the report comes from ``inrix_tools`` — this script
loads the inputs, calls the core in order, hands the frames to
``gui.validation_report``, and writes the pages out. It computes no statistics of
its own, and it has **no hardcoded paths**: the retired predecessor
(``legacy/generate_corridor_html_reports.py``) opened with
``BASE_DIR = "/home/hansrkid/Inrix"`` and fitted regression slopes inside its
figure loops (Session 33, G10).

Typical run (paths are this machine's; nothing here assumes them)::

    python scripts/build_validation_report.py \
        --export out/extracted_query_segments_2026.csv \
        --workbook "TT Logger.xlsx" \
        --network USA_Idaho_shapefile.zip \
        --network-cache geometry_cache/d3_validation_network.geoparquet \
        --out-dir out/validation_report

Needs the ``geo`` extra (the chains are assembled from the XD shapefile).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from gui import validation_report as vr                    # noqa: E402
from inrix_tools import agreement, corridors, io, reference  # noqa: E402
from inrix_tools.io import DATETIME_COL, DEFAULT_TZ, SEGMENT_COL  # noqa: E402
from inrix_tools.reference import ROUTE_COL, TT_COL        # noqa: E402

BIN_MINUTES = 15
BBOX_MARGIN_DEG = 0.05


def _bbox(routes: pd.DataFrame, margin: float = BBOX_MARGIN_DEG):
    """WGS84 bbox around every chain-matchable endpoint, plus a margin — so the
    network read stays a subset without clipping a chain."""
    lats = pd.concat([routes["origin_lat"], routes["dest_lat"]]).dropna()
    lons = pd.concat([routes["origin_lon"], routes["dest_lon"]]).dropna()
    return (float(lons.min()) - margin, float(lats.min()) - margin,
            float(lons.max()) + margin, float(lats.max()) + margin)


def build_frames(export, workbook, network_source, *, tz: str = DEFAULT_TZ,
                 network_cache=None, bin_minutes: int = BIN_MINUTES,
                 sheets=None, route_segments=None, bbox=None,
                 verbose: bool = True) -> dict:
    """Run the whole comparison and return the frames the report renders.

    Order is the pipeline Item 29 ends with: load the reference -> assemble a
    chain per chain-matchable route -> sum INRIX travel time along each chain ->
    match bin for bin -> compare, gate, and profile.

    Args:
        route_segments: optional ``{route: {"start_segment", "end_segment"}}`` for
            sheets whose endpoints are **place names**. Those routes cannot be
            snapped (their extent would be a geocoder's guess), so an operator
            states the two terminal segments and the chain is walked between them
            (``corridors.chain_between_segments``). Such a route is carried with
            ``extent_source="operator-specified terminal segments"`` and is shown
            that way in the report — the end segments are whole, so nothing is
            prorated and there is no snap distance to report.
        bbox: WGS84 ``(minx, miny, maxx, maxy)`` for the network read. Default:
            the coordinate endpoints' bbox — but a place-name route's segments can
            sit far outside it, so supplying ``route_segments`` without a bbox
            reads the whole network instead of silently clipping the walk.
    """
    def log(msg):
        if verbose:
            print(msg, flush=True)

    log(f"Reference workbook: {workbook}")
    obs = reference.load_tt_logger(workbook, tz=tz, sheets=sheets)
    routes = reference.routes_frame(obs)
    log(f"  {len(routes)} sheets, {len(obs):,} samples, "
        f"{obs.attrs['n_ambiguous_dropped']} dropped in the DST fold")

    route_segments = dict(route_segments or {})
    matchable = routes[routes[reference.CHAIN_MATCHABLE_COL]]
    named = [r for r in routes[ROUTE_COL] if r in route_segments
             and r not in set(matchable[ROUTE_COL])]
    skipped = [r for r in routes.loc[~routes[reference.CHAIN_MATCHABLE_COL], ROUTE_COL]
               if r not in route_segments]
    log(f"  {len(matchable)} chain-matchable (coordinate) routes; "
        f"{len(named)} place-name routes with operator-specified terminal segments; "
        f"{len(skipped)} place-name routes excluded")

    log(f"Network: {network_source}")
    from inrix_tools.geometry import load_xd_network
    if bbox is None and not named:
        bbox = _bbox(matchable)
    elif bbox is None:
        log("  reading the whole network: a place-name route's segments can sit "
            "outside the coordinate endpoints' bbox")
    network = load_xd_network(network_source, bbox=bbox, cache_path=network_cache)
    log(f"  {len(network):,} segments loaded")

    chains, descriptions, extent_sources = {}, {}, {}
    for route in matchable[ROUTE_COL]:
        start, end = reference.route_endpoints(routes, route)
        chains[route] = corridors.build_chain(network, start, end)
        extent_sources[route] = "snapped query points"
    for route in named:
        spec = route_segments[route]
        chains[route] = corridors.chain_between_segments(
            network, spec["start_segment"], spec["end_segment"])
        extent_sources[route] = "operator-specified terminal segments"
    for route, chain in chains.items():
        descriptions[route] = corridors.chain_description(chain, network)
        snap = (f"snap {chain.snap_start_feet:5.0f}/{chain.snap_end_feet:5.0f} ft"
                if chain.snap_start_feet == chain.snap_start_feet else "no snap (segments given)")
        log(f"  {route:<22} {chain.n_segments:>3} segments  "
            f"{chain.requested_miles:6.2f} mi requested / {chain.chain_miles:6.2f} mi whole  "
            f"{snap}  "
            f"target={'yes' if chain.reached_target else 'NO (' + chain.stop_reason + ')'}")

    member_ids = sorted({sid for c in chains.values() for sid in c.segment_ids})
    log(f"Export: {export}")
    df = io.to_local(io.load_data(export), tz=tz)
    df = df[df[SEGMENT_COL].isin(member_ids)]
    log(f"  {len(df):,} rows on {df[SEGMENT_COL].nunique()} of "
        f"{len(member_ids)} chain member segments")

    coverage_frames, inrix_parts = {}, []
    for route, chain in chains.items():
        coverage_frames[route] = corridors.chain_coverage(chain, df, value=TT_COL)
        part = corridors.chain_travel_time(df, chain, label=route)
        inrix_parts.append(part.rename(columns={io.CORRIDOR_COL: ROUTE_COL}))
    inrix = pd.concat(inrix_parts, ignore_index=True)

    ref_binned = reference.align_to_bins(obs, bin_minutes=bin_minutes)
    matched = agreement.match_bins(inrix, ref_binned)
    summary = agreement.compare(inrix, ref_binned, matched=matched)
    log(f"Matched {len(matched):,} bins across {matched[ROUTE_COL].nunique()} routes")

    window = (df[DATETIME_COL].min(), df[DATETIME_COL].max())
    frames = {
        "summary": summary,
        "matched": matched,
        "coverage": reference.coverage_gate(obs, window=window),
        "nesting": reference.nesting_gate(ref_binned, chains=chains),
        "lag": reference.lag_summary(reference.lag_scan(ref_binned, inrix)),
        "totals": agreement.independent_totals(matched, chains=chains),
        "observations": obs,
        "routes": routes,
        "chains": chains,
        "descriptions": descriptions,
        "extent_sources": extent_sources,
        "chain_coverage": coverage_frames,
        "profile_tod": agreement.profile(matched, by="time_of_day"),
        "profile_grid": agreement.profile(matched, by=["day_of_week", "time_of_day"],
                                          bin_minutes=120),
        "profile_date": agreement.profile(matched, by="date"),
        "meta": {
            "export": str(export), "workbook": str(workbook),
            "network": str(network_source), "tz": tz, "bin_minutes": bin_minutes,
            "export_window": window,
            "n_ambiguous_dropped": obs.attrs["n_ambiguous_dropped"],
            "place_name_routes": tuple(skipped),
            "operator_specified_routes": tuple(named),
        },
    }
    return frames


def write_report(frames: dict, out_dir) -> list[Path]:
    """Render the pages and write them; returns the files written."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    written = []
    for filename, html in vr.build_report(frames).items():
        path = out / filename
        path.write_text(html, encoding="utf-8")
        written.append(path)
    tables = out / "tables"
    tables.mkdir(exist_ok=True)
    for name in ("summary", "coverage", "nesting", "lag", "totals"):
        frames[name].to_csv(tables / f"{name}.csv", index=False)
        written.append(tables / f"{name}.csv")
    (tables / "chains.json").write_text(json.dumps(
        {r: c.summary() for r, c in frames["chains"].items()}, indent=2, default=str),
        encoding="utf-8")
    written.append(tables / "chains.json")
    return written


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--export", required=True,
                   help="INRIX export: a .zip download, a directory, or a data.csv")
    p.add_argument("--workbook", required=True, help="TT Logger .xlsx reference workbook")
    p.add_argument("--network", required=True, help="XD shapefile .zip / .shp / cache")
    p.add_argument("--network-cache", default=None,
                   help="GeoParquet cache for the network subset (written if absent)")
    p.add_argument("--out-dir", default="out/validation_report")
    p.add_argument("--tz", default=DEFAULT_TZ)
    p.add_argument("--bin-minutes", type=int, default=BIN_MINUTES)
    p.add_argument("--sheets", nargs="*", default=None,
                   help="restrict to these workbook sheets (default: all)")
    p.add_argument("--route-segments", default=None,
                   help="JSON of {route: {start_segment, end_segment}} for sheets "
                        "whose endpoints are place names and cannot be snapped")
    p.add_argument("--bbox", nargs=4, type=float, default=None,
                   metavar=("MINX", "MINY", "MAXX", "MAXY"),
                   help="WGS84 bbox for the network read (default: the endpoints')")
    args = p.parse_args(argv)

    route_segments = None
    if args.route_segments:
        route_segments = {k: v for k, v in
                          json.loads(Path(args.route_segments).read_text()).items()
                          if isinstance(v, dict)}

    frames = build_frames(args.export, args.workbook, args.network, tz=args.tz,
                          network_cache=args.network_cache,
                          bin_minutes=args.bin_minutes, sheets=args.sheets,
                          route_segments=route_segments,
                          bbox=tuple(args.bbox) if args.bbox else None)
    written = write_report(frames, args.out_dir)
    print(f"\nWrote {len(written)} files to {Path(args.out_dir).resolve()}")
    for path in written:
        print(f"  {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

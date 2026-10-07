#!/usr/bin/env python3
"""Generate the six district corridor screening catalogues (ROADMAP Items 46, 63).

The catalogues are **derived**, not drawn. For each district this walks the XD
network's numbered mainline chains, cuts them at objectively detected split
points (`inrix_tools.extents`, ROADMAP Item 45.1–45.2), emits the Tier 1/2/3
extent alternatives as catalogue entries whose ``description`` states the split
that ended them, detects the one-way couplets topologically
(`inrix_tools.couplets`, Item 45.3), and verifies every entry resolves through
``corridors.resolve_catalogue``.

The predecessor — hand-picked lat/lon hints per corridor and hand-authored
couplet entries — is kept in ``legacy/handbuilt_catalogues/`` for diffing, and
District 3's hand-curated Item 44 catalogue in ``legacy/d3_curated/`` (Item 63).

A detected couplet's legs pair as one facility whatever their separation (Item 63).
A couplet is a stitching aid, never ranked on its own (``_ranked: false``); a core
that runs on it is named for it and lists it (``_counted_in``).

A catalogue is written **only when it verifies**: a file that ships despite a
failed verification is a broken catalogue that looks like a good one.

Usage:
    python scripts/build_statewide_catalogues.py
    python scripts/build_statewide_catalogues.py --districts 1 2 --refresh-baseline
    python scripts/build_statewide_catalogues.py --dry-run
    python scripts/build_statewide_catalogues.py --districts 1 2 3 4 5 6 \
        --out-dir /tmp/regen --audit-dir /tmp/regen     # compare, don't replace

The manual hard stops (``scripts/corridor_hard_stops.csv``, ROADMAP Item 60) are
resolved on each district's network and cut into the chains before cores are found;
``--hard-stops ''`` builds without them.

**Corridor types (ROADMAP Item 66).** Each type in ``--types`` (default
``commute,recreational,retail``, ``extents.CORRIDOR_TYPES``) is cored on its own
windows: the weekday peaks, summer Fri/Sat/Sun, weekday midday + Saturday daytime.
Every type uses the same chains, floors, hard stops and couplet legs. The per-type
catalogues are merged into **one** ``dN_corridors.json``
(``extents.merge_typed_catalogues``). A corridor several types find is one corridor
whose ``_types`` lists them, and ``_class`` says what it is (``urban_hybrid`` for
commute + retail). Each type caches its own baseline and bin screens beside the
commute ones (``segment_baseline_screen_<type>.parquet``). ``--types commute``
rebuilds the pre-Item 66 catalogue.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import geopandas as gpd
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from inrix_tools import aadt as aadt_mod          # noqa: E402
from inrix_tools import corridors, couplets, extents  # noqa: E402
from inrix_tools import hard_stops as hard_stops_mod  # noqa: E402
from inrix_tools import itd_layers, routes, screen, store  # noqa: E402
from inrix_tools import profile_assignment as profiles_mod  # noqa: E402

MEMBERSHIP = "out/highways/route_membership/d{district}_route_membership.csv"


def apply_membership(net: gpd.GeoDataFrame, district: int) -> gpd.GeoDataFrame:
    """``net`` with ``RoadNumber`` resolved against ITD's layer (ROADMAP Item 48), so
    the chains and couplets are walked on ITD's routes — Lewiston's US-12 on the levee
    bypass, not downtown Main St / D St. Requires ``build_route_membership.py``."""
    path = Path(MEMBERSHIP.format(district=district))
    if not path.exists():
        raise SystemExit(f"{path} missing — run scripts/build_route_membership.py first")
    return routes.apply_route_membership(net, routes.read_membership(path))

DEFAULT_DISTRICTS = [1, 2, 3, 4, 5, 6]
"""Every district, District 3 included: its generated catalogue is primary since Item
63 (the curated Item 44 one is archived in ``legacy/d3_curated/``)."""


def catalogue_name(district: int) -> str:
    """The file a district's generated catalogue is written to."""
    return f"d{district}_corridors.json"


def load_district(district: int, *, aadt_source: str | None,
                  aadt_year: int, shs=None) -> tuple[gpd.GeoDataFrame, pd.DataFrame]:
    """The district's repaired, AADT-joined network and its link repair table."""
    net = gpd.read_parquet(f"geometry_cache/d{district}_network.geoparquet")
    repairs = corridors.load_link_repairs(f"scripts/d{district}_link_repairs.csv")
    net = corridors.apply_link_repairs(net, repairs)
    net = apply_membership(net, district)
    if shs is not None:
        # SHS mileposts order each route's pieces; the chain walk reads them to
        # accept a junction join only where the milepost gap shows it (Item 51).
        layer = itd_layers._shs_frame_from(shs)
        idx = net.set_index("XDSegID", drop=False)
        mp = itd_layers.shs_mileposts(idx, layer, idx[routes.ITD_ROUTE_ID_COL])
        for col in mp.columns:
            net[col] = net["XDSegID"].map(mp[col]).to_numpy()

    if aadt_source:
        layer = aadt_mod.load_aadt(aadt_source, year=aadt_year,
                                   cache_path=f"geometry_cache/d{district}_aadt.parquet",
                                   shs=shs)
        indexed = net if net.index.name == "XDSegID" else net.set_index("XDSegID", drop=False)
        net = aadt_mod.join_aadt(indexed, layer)
    return net, repairs


URBAN_CONTEXT = "out/highways/route_membership/d{district}_urban_context.csv"


def join_urban_context(net: gpd.GeoDataFrame, district: int) -> gpd.GeoDataFrame:
    """``net`` with Item 52's urban context (area, share inside, signed edge distance)
    joined — the guide Tier 2 and Tier 3 read, never a gate."""
    path = Path(URBAN_CONTEXT.format(district=district))
    if not path.exists():
        raise SystemExit(f"{path} missing — run scripts/build_route_membership.py first")
    ctx = pd.read_csv(path, dtype={"urban_uace": str}).set_index("XDSegID")
    net = net.drop(columns=[c for c in ctx.columns if c in net.columns])
    ids = net["XDSegID"].astype("int64")
    for col in ctx.columns:
        net[col] = ids.map(ctx[col]).to_numpy()
    return net


DISTRICT_TZ = {1: "America/Los_Angeles", 2: "America/Los_Angeles", 3: "America/Boise",
               4: "America/Boise", 5: "America/Boise", 6: "America/Boise"}
BASELINE_FILE = "segment_baseline_screen.parquet"


COMMUTE = extents.CORRIDOR_TYPES["commute"]


def type_file(filename: str, ctype: extents.CorridorType) -> str:
    """A per-type cache file. The commute type keeps the names it had before types
    existed (``segment_baseline_screen.parquet``), so its caches still hit; any other
    type inserts its name before the suffix (``segment_baseline_screen_retail.parquet``)."""
    if ctype.name == "commute":
        return filename
    stem, dot, ext = filename.rpartition(".")
    return f"{stem}_{ctype.name}{dot}{ext}"


def _district_area(con, district: int) -> str:
    areas = store.list_areas(con)
    if len(areas) != 1:
        raise SystemExit(f"d{district}_store.duckdb holds {len(areas)} areas; expected 1")
    return str(areas.iloc[0]["area_key"])


def load_baseline(district: int, screening_dir: Path, *, refresh: bool = False,
                  cvalue_threshold: float = 80,
                  ctype: extents.CorridorType = COMMUTE) -> pd.DataFrame:
    """The district's baseline screen for one corridor type (ROADMAP Items 50, 66): the
    type's windows, the night, weekday travel-time percentiles and the real-time share,
    one row per segment.

    Computed from the district store on first use and cached next to the peak screen;
    ``refresh`` recomputes it (after an ingest), and so does a cache that lacks one of
    the type's windows."""
    windows = ctype.baseline_windows()
    path = screening_dir / f"d{district}" / type_file(BASELINE_FILE, ctype)
    if path.exists() and not refresh:
        cached = pd.read_parquet(path)
        if all(f"{w}_travel_time" in cached.columns for w in windows):
            return cached
        print(f"  {path} lacks some of {list(windows)}; recomputing")
    con = store.connect(f"d{district}_store.duckdb")
    con.execute("SET enable_progress_bar = false")
    try:
        scr = screen.segment_screen(
            con, _district_area(con, district), windows=windows,
            cvalue_threshold=cvalue_threshold, tz=DISTRICT_TZ[district],
            quantiles=extents.BASELINE_QUANTILES)
    finally:
        con.close()
    path.parent.mkdir(parents=True, exist_ok=True)
    scr.to_parquet(path)
    print(f"  Baseline screen ({ctype.name}) -> {path} ({len(scr)} segments)")
    return scr


MONTHLY_FILE = "segment_monthly_screen.parquet"


def load_monthly(district: int, screening_dir: Path, *, refresh: bool = False,
                 cvalue_threshold: float = 80,
                 ctype: extents.CorridorType = COMMUTE) -> pd.DataFrame:
    """Per segment, per month travel time in the type's windows (Item 50's episodic
    flag), cached next to the baseline screen."""
    path = screening_dir / f"d{district}" / type_file(MONTHLY_FILE, ctype)
    if path.exists() and not refresh:
        return pd.read_parquet(path)
    con = store.connect(f"d{district}_store.duckdb")
    con.execute("SET enable_progress_bar = false")
    try:
        mon = screen.segment_monthly_screen(
            con, _district_area(con, district), windows=ctype.window_map(),
            cvalue_threshold=cvalue_threshold, tz=DISTRICT_TZ[district])
    finally:
        con.close()
    path.parent.mkdir(parents=True, exist_ok=True)
    mon.to_parquet(path)
    print(f"  Monthly screen ({ctype.name}) -> {path} ({len(mon)} segment-months)")
    return mon


BINS_FILE = "segment_peak_bins.parquet"
PROFILES_FILE = "d{district}_volume_profiles.csv"


def load_bins(district: int, screening_dir: Path, *, refresh: bool = False,
              cvalue_threshold: float = 80,
              ctype: extents.CorridorType = COMMUTE) -> pd.DataFrame:
    """The district's delay cells in the type's windows, segment x month x day type x
    bin (Item 57's ``screen.segment_bin_screen``), cached next to the baseline screen:
    the curve-weighted VHD the core floors read (Item 58). A cache built over other
    windows than the type's is recomputed."""
    path = screening_dir / f"d{district}" / type_file(BINS_FILE, ctype)
    want = {n: w.to_dict() for n, w in ctype.window_map().items()}
    if path.exists() and not refresh:
        bins = pd.read_parquet(path)
        meta = path.with_suffix(".json")
        bins.attrs = json.loads(meta.read_text()) if meta.exists() else {}
        if not bins.attrs.get("windows") or bins.attrs["windows"] == want:
            return bins
        print(f"  {path} was built over {list(bins.attrs['windows'])}; recomputing")
    con = store.connect(f"d{district}_store.duckdb")
    con.execute("SET enable_progress_bar = false")
    try:
        bins = screen.segment_bin_screen(
            con, _district_area(con, district), windows=ctype.window_map(),
            cvalue_threshold=cvalue_threshold, tz=DISTRICT_TZ[district])
    finally:
        con.close()
    path.parent.mkdir(parents=True, exist_ok=True)
    bins.to_parquet(path)
    # The period, zone, bin width and windows the weights are built from.
    path.with_suffix(".json").write_text(json.dumps(bins.attrs, default=str) + "\n")
    print(f"  Bin screen ({ctype.name}) -> {path} ({len(bins):,} cells)")
    return bins


def load_curves(district: int, screening_dir: Path) -> pd.DataFrame:
    """The volume-profile curve per XD segment the district's screening run assigned
    (Item 56, ``run_district_screening.py``). ``attrs['library']`` is the library it
    reads against: the packaged curves plus the run's count-fitted ones (Item 59)."""
    path = screening_dir / f"d{district}" / PROFILES_FILE.format(district=district)
    if not path.exists():
        raise SystemExit(f"{path} missing — run the district screening "
                         f"(run_statewide_screening.py --mode full) first; its curves "
                         f"weight the core VHD (Item 58).")
    curves = profiles_mod.read_assignment(path)
    curves.attrs["library"] = profiles_mod.assignment_profiles(path)
    return curves


def couplet_block(net: gpd.GeoDataFrame, district: int,
                  observed: set[int] | None = None,
                  review: list | None = None) -> tuple[list[dict], list[dict], list, dict]:
    """Detected couplets for one district, in catalogue shape.

    A couplet whose legs are not in the export is dropped: it resolves on the
    network but has nothing to screen, and would rank as a blank row. ``review``
    collects every detected pair, kept or rejected, with the one-way test that
    decided (Item 51).

    Returns ``(entries, groups, detected, legs)``. ``legs`` is ``{couplet group id:
    (leg 1 ids, leg 2 ids)}`` for every detected pair, which
    ``extents.generate_catalogue`` pairs couplet legs by (Item 63).
    """
    rejected: list = []
    detected = couplets.detect_couplets(
        net, counties=couplets.DISTRICT_COUNTIES.get(district), rejected=rejected)
    if review is not None:
        for pair, verdict in [(p, "kept") for p in detected] + rejected:
            review.append({"district": district, "county": pair.county,
                           "street1": pair.dir1_street, "dir1": pair.dir1_bearing,
                           "street2": pair.dir2_street, "dir2": pair.dir2_bearing,
                           "routes": "/".join(pair.route_numbers),
                           "miles": pair.total_miles,
                           "mean_lateral_sep_m": pair.mean_lateral_sep_m,
                           "verdict": verdict})
    entries, groups, legs = [], [], {}
    for pair in detected:
        e1, e2, group = couplets.couplet_catalogue_entries(pair, net)
        legs[group["id"]] = (pair.dir1_segment_ids, pair.dir2_segment_ids)
        if observed is not None and not (
                all(s in observed for s in pair.dir1_segment_ids)
                and all(s in observed for s in pair.dir2_segment_ids)):
            continue
        entries.extend([e1, e2])
        groups.append(group)
    # ``detected`` (not the observed subset) goes to the registry validation: that
    # table measures **the detector**, and scoring it on the post-coverage subset would
    # blame it for couplets the export simply does not carry.
    return entries, groups, detected, legs


def unrank_couplets(cat: dict, legs: dict, miles) -> dict[str, str | None]:
    """Mark every couplet group not ranked (``_ranked: false``), naming in
    ``_counted_in`` the facility whose ranked core runs on most of its legs.

    A couplet is a stitching aid, not a class of corridor (owner, 2026-10-07): its
    detection lets the chain walk pair the two one-way streets as one road's two
    directions (Item 63). Its legs are already walked into their route's chains like
    any other segments, so a congested couplet is a core (Boise's Front St / Myrtle
    St, Moscow's Washington St / Jackson St) and an uncongested one is not; ranking
    the couplet group as well would rank the same pavement twice, or rank pavement
    the cores turned down.

    The group stays in the catalogue: its legs are what the AADT one-way fallback
    reads (``corridors.couplet_segments``, Items 53/54), and the ranking carries it as
    context under ``_counted_in``.

    Args:
        legs: ``{couplet group id: (leg 1 ids, leg 2 ids)}`` (:func:`couplet_block`).
        miles: segment id -> miles.

    Returns ``{couplet group id: facility or None}``."""
    def _mi(ids) -> float:
        return float(sum(float(miles.get(s, 0.0)) for s in ids))

    core_segs: dict[str, set[int]] = {}
    groups = {g["id"]: g for g in cat["reporting_corridors"]}
    for e in cat["corridors"]:
        g = groups.get(e.get("corridor"), {})
        if g.get("_ranked") and g.get("_couplets"):
            core_segs.setdefault(g.get("_facility", g["id"]), set()).update(
                int(s) for s in e.get("_segment_ids", []))
    out: dict[str, str | None] = {}
    for g in cat["reporting_corridors"]:
        if not g.get("_couplet"):
            continue
        g["_ranked"] = False
        on = set()
        if g["id"] in legs:
            l1, l2 = legs[g["id"]]
            on = set(map(int, l1)) | set(map(int, l2))
        shared = {f: _mi(on & segs) for f, segs in core_segs.items()}
        best = max(shared, key=shared.get, default=None)
        out[g["id"]] = best if best is not None and shared[best] > 0 else None
        if out[g["id"]]:
            g["_counted_in"] = out[g["id"]]
    return out


def merge_blocks(base: dict, entries: list[dict], groups: list[dict]) -> dict:
    """Add entries/groups to a catalogue, skipping ids it already carries."""
    have_e = {e["id"] for e in base["corridors"]}
    have_g = {g["id"] for g in base["reporting_corridors"]}
    base["corridors"].extend(e for e in entries if e["id"] not in have_e)
    base["reporting_corridors"].extend(g for g in groups if g["id"] not in have_g)
    return base


def verify(cat: dict, net, repairs, district: int, observed: set[int] | None) -> pd.DataFrame:
    """Resolve every entry and print the per-entry verdict."""
    print(f"\n--- Verifying District {district} catalogue ---")
    res = corridors.resolve_catalogue(net, cat["corridors"], observed=observed,
                                      repairs=repairs)
    for _, r in res.iterrows():
        status = "OK" if r["reached_target"] else f"FAIL ({r['stop_reason']})"
        cover = "" if "miles_covered_fraction" not in res.columns \
            else f" cov {r['miles_covered_fraction']:.2f}"
        print(f"  [{status:^12}] {r['id']:<46} {r['chain_miles']:>7.2f} mi "
              f"({r['n_segments']:>3} segs, {r['n_repaired_links']} rep){cover}")
    print(f"  {int(res['reached_target'].sum())}/{len(res)} reached target, "
          f"{int(res['accepted'].sum())}/{len(res)} accepted")
    return res


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--districts", nargs="*", type=int, default=DEFAULT_DISTRICTS)
    parser.add_argument("--out-dir", default="scripts",
                        help="where the dN_corridors.json files are written")
    parser.add_argument("--screening-dir", default="out/statewide_screening",
                        help="per-district screening outputs (the baseline screen is cached here)")
    parser.add_argument("--report-dir", default="out/statewide_screening",
                        help="where the couplet validation table is written")
    parser.add_argument("--aadt", default=aadt_mod.DEFAULT_SOURCE,
                        help="ITD cumulative AADT source; '' to skip the volume join")
    parser.add_argument("--aadt-year", type=int, default=aadt_mod.DEFAULT_YEAR)
    parser.add_argument("--shs", default="SHS_Primary.zip",
                        help="classifies AADT records (Item 52); '' = descriptions only")
    parser.add_argument("--refresh-baseline", action="store_true",
                        help="recompute the baseline screen from the store (after an ingest)")
    parser.add_argument("--max-facilities", type=int, default=None,
                        help="keep only the N facilities with the largest core delay")
    parser.add_argument("--hard-stops", default="scripts/corridor_hard_stops.csv",
                        help="manual hard stops (Item 60); '' = none")
    parser.add_argument("--audit-dir", default=None,
                        help="where dN/core_audit.csv goes (default: --screening-dir)")
    parser.add_argument("--types", default=",".join(extents.DEFAULT_CORRIDOR_TYPES),
                        help="corridor types to discover, in merge priority order "
                             f"(ROADMAP Item 66; presets: {', '.join(extents.CORRIDOR_TYPES)})")
    parser.add_argument("--no-couplets", action="store_true")
    parser.add_argument("--dry-run", action="store_true",
                        help="generate and verify, but write nothing")
    args = parser.parse_args()

    out_dir = Path(args.out_dir)
    report_dir = Path(args.report_dir)
    stop_table = (hard_stops_mod.read_hard_stops(args.hard_stops)
                  if args.hard_stops else None)
    ctypes = extents.resolve_corridor_types(args.types)
    all_pairs, all_ok = [], True
    couplet_review: list[dict] = []

    for d in args.districts:
        print(f"\n{'=' * 70}\n  DISTRICT {d}\n{'=' * 70}")
        net, repairs = load_district(d, aadt_source=args.aadt or None,
                                     shs=(args.shs if args.shs and Path(args.shs).exists()
                                          else None),
                                     aadt_year=args.aadt_year)
        net = join_urban_context(net, d)
        sdir = Path(args.screening_dir)
        baselines = {t.name: load_baseline(d, sdir, refresh=args.refresh_baseline, ctype=t)
                     for t in ctypes}
        # A segment is observed if it carries a gated row in any type's screen.
        observed = {int(s) for b in baselines.values() for s in b.index[b["n_obs"] > 0]}
        # Curve-weighted VHD (Items 57, 58): the core floors and the monthly profile
        # read each bin's delay times that bin's volume. It needs the AADT join.
        curves = load_curves(d, sdir) if args.aadt else None
        profiles = None if curves is None else curves.attrs["library"]
        stops = None
        if stop_table is not None:
            resolved = hard_stops_mod.resolve_hard_stops(stop_table, net, d)
            stops = hard_stops_mod.stop_boundaries(resolved)
            print(f"  {resolved['row'].nunique()} hard stops -> {len(stops)} boundaries")

        # Couplets first: the corridor pass pairs a couplet's legs as one facility
        # whatever their separation (Item 63).
        c_entries, c_groups, pairs, legs = ([], [], [], {}) if args.no_couplets else \
            couplet_block(net, d, observed, couplet_review)

        # One catalogue per corridor type, on the same chains, floors, stops and
        # couplet legs; only the windows differ (Item 66). Then one merged catalogue.
        audit: list[dict] = []
        per_type: dict[str, dict] = {}
        congestion: dict[str, pd.DataFrame] = {}
        bins_by_type: dict[str, pd.DataFrame] = {}
        for t in ctypes:
            bins = monthly = None
            if curves is not None:
                bins = load_bins(d, sdir, refresh=args.refresh_baseline, ctype=t)
                if bins is not None:
                    bins_by_type[t.name] = bins
            else:
                monthly = load_monthly(d, sdir, refresh=args.refresh_baseline, ctype=t)
            congestion[t.name] = extents.segment_congestion(
                baselines[t.name], net, peak_windows=t.window_names, bins=bins,
                curves=curves, profiles=profiles)
            t_audit: list[dict] = []
            per_type[t.name] = extents.generate_catalogue(
                net, baselines[t.name],
                peak_windows=t.window_names,
                max_facilities=args.max_facilities,
                observed=observed,
                audit=t_audit,
                monthly=monthly,
                bins=bins,
                curves=curves,
                profiles=profiles,
                hard_stops=stops,
                couplet_legs=legs,
                congestion=congestion[t.name],
            )
            audit.extend({"type": t.name, **row} for row in t_audit)
            gen = per_type[t.name]["_generated"]
            print(f"  [{t.name}] {gen['n_chains']} mainline chains -> "
                  f"{gen['n_facilities']} facilities")
        cat = extents.merge_typed_catalogues(
            per_type, miles=net.set_index("XDSegID")["Miles"].astype(float).to_dict(),
            types=ctypes, congestion=congestion,
            bins=bins_by_type if bins_by_type else None,
            note=(f"ITD District {d} screening catalogue: corridor types "
                  f"{', '.join(t.name for t in ctypes)} (ROADMAP Item 66), each generated "
                  f"by inrix_tools.extents.generate_catalogue on its own windows (cores on "
                  f"recurring congestion against each segment's own baseline, Item 50), "
                  f"merged by extents.merge_typed_catalogues; couplets by "
                  f"inrix_tools.couplets.detect_couplets. Item 46/49 predecessor in "
                  f"legacy/item46_catalogues/; the separate 2026-09-29 recreational "
                  f"catalogues in legacy/rec_catalogues_2026-09-29/."),
        )
        audit_path = Path(args.audit_dir or args.screening_dir) / f"d{d}" / "core_audit.csv"
        if not args.dry_run:
            audit_path.parent.mkdir(parents=True, exist_ok=True)
            pd.DataFrame(audit).to_csv(audit_path, index=False)
            print(f"  Core audit -> {audit_path}")
        for name, info in cat["_generated"]["types"].items():
            print(f"  [{name}] {info['n_facilities']} facilities: {info['n_merged']} merged "
                  f"into an earlier type's, {info['n_standalone']} standalone")
        classes = pd.Series([g["_class"] for g in cat["reporting_corridors"]
                             if g.get("_tier") == extents.ExtentTier.CORE.value])
        print(f"  {cat['_generated']['n_facilities']} facilities -> "
              f"{len(cat['corridors'])} directional entries, "
              f"{len(cat['reporting_corridors'])} reporting corridors; classes: "
              + ", ".join(f"{k} {v}" for k, v in classes.value_counts().items()))

        if not args.no_couplets:
            all_pairs.extend(pairs)
            cat = merge_blocks(cat, c_entries, c_groups)
            counted = unrank_couplets(
                cat, legs, net.set_index("XDSegID")["Miles"].to_dict())
            print(f"  + {len(pairs)} couplets detected, "
                  f"{len(c_entries) // 2} fully observed and catalogued "
                  f"({len(c_entries)} legs), none ranked on its own; on a core: "
                  + (", ".join(f"{k} -> {v}" for k, v in counted.items() if v) or "-"))

        res = verify(cat, net, repairs, d, observed)
        passed = bool(res["reached_target"].all())
        all_ok &= passed

        out_path = out_dir / catalogue_name(d)
        if args.dry_run:
            print(f"  --dry-run: not writing {out_path}")
        elif passed:
            out_path.write_text(json.dumps(cat, indent=2) + "\n")
            print(f"  Wrote {out_path}")
        else:
            print(f"  NOT WRITTEN — {int((~res['reached_target']).sum())} entries did not "
                  f"resolve; {out_path} left as it was.")

    if all_pairs and not args.dry_run:
        # The registry spans all six districts, so detection is run on the ones this
        # invocation did not build too. Scoring the registry on a partial statewide
        # sweep would report every unvisited district as a miss.
        for d in sorted(set(range(1, 7)) - set(args.districts)):
            cache = Path(f"geometry_cache/d{d}_network.geoparquet")
            if not cache.exists():
                continue
            other = apply_membership(corridors.apply_link_repairs(
                gpd.read_parquet(cache),
                corridors.load_link_repairs(f"scripts/d{d}_link_repairs.csv")), d)
            all_pairs.extend(couplets.detect_couplets(
                other, counties=couplets.DISTRICT_COUNTIES.get(d)))

        report_dir.mkdir(parents=True, exist_ok=True)
        review_path = report_dir / "couplet_review.csv"
        pd.DataFrame(couplet_review).to_csv(review_path, index=False)
        print(f"Couplet review (kept and rejected pairs) -> {review_path}")
        table = couplets.match_known_couplets(all_pairs)
        path = report_dir / "couplet_registry_validation.csv"
        table.to_csv(path, index=False)
        print(f"\nCouplet registry validation -> {path}")
        print(table["match_kind"].value_counts().to_string())

    print(f"\nStatewide catalogue generation: {'ALL VERIFIED' if all_ok else 'FINDINGS'}")
    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Generate the District 1/2/4/5/6 corridor screening catalogues (ROADMAP Item 46).

The catalogues are **derived**, not drawn. For each district this walks the XD
network's numbered mainline chains, cuts them at objectively detected split
points (`inrix_tools.extents`, ROADMAP Item 45.1–45.2), emits the Tier 1/2/3
extent alternatives as catalogue entries whose ``description`` states the split
that ended them, detects the one-way couplets topologically
(`inrix_tools.couplets`, Item 45.3), and verifies every entry resolves through
``corridors.resolve_catalogue``.

The predecessor — hand-picked lat/lon hints per corridor and hand-authored
couplet entries — is kept in ``legacy/handbuilt_catalogues/`` for diffing.

A catalogue is written **only when it verifies**: a file that ships despite a
failed verification is a broken catalogue that looks like a good one.

Usage:
    python scripts/build_statewide_catalogues.py
    python scripts/build_statewide_catalogues.py --districts 1 2 --window am
    python scripts/build_statewide_catalogues.py --dry-run
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

DEFAULT_DISTRICTS = [1, 2, 4, 5, 6]
"""District 3's catalogue is the Item 44 empirical rebuild
(``scripts/rebuild_d3_catalogue.py``) and is not regenerated here."""


def load_district(district: int, *, aadt_source: str | None,
                  aadt_year: int) -> tuple[gpd.GeoDataFrame, pd.DataFrame]:
    """The district's repaired, AADT-joined network and its link repair table."""
    net = gpd.read_parquet(f"geometry_cache/d{district}_network.geoparquet")
    repairs = corridors.load_link_repairs(f"scripts/d{district}_link_repairs.csv")
    net = corridors.apply_link_repairs(net, repairs)

    if aadt_source:
        layer = aadt_mod.load_aadt(aadt_source, year=aadt_year,
                                   cache_path=f"geometry_cache/d{district}_aadt.parquet")
        indexed = net if net.index.name == "XDSegID" else net.set_index("XDSegID", drop=False)
        net = aadt_mod.join_aadt(indexed, layer)
    return net, repairs


def load_screen(district: int, screening_dir: Path) -> pd.DataFrame | None:
    """The district's per-segment peak screening frame, if it has been run."""
    path = screening_dir / f"d{district}" / "segment_peak_screen.parquet"
    if not path.exists():
        return None
    return pd.read_parquet(path)


def couplet_block(net: gpd.GeoDataFrame, district: int,
                  observed: set[int] | None = None) -> tuple[list[dict], list[dict], list]:
    """Detected couplets for one district, in catalogue shape.

    A couplet whose legs are not in the export is dropped: it resolves on the
    network but has nothing to screen, and would rank as a blank row.
    """
    detected = couplets.detect_couplets(
        net, counties=couplets.DISTRICT_COUNTIES.get(district))
    pairs = detected
    if observed is not None:
        pairs = [p for p in detected
                 if all(s in observed for s in p.dir1_segment_ids)
                 and all(s in observed for s in p.dir2_segment_ids)]
    entries, groups = [], []
    for pair in pairs:
        e1, e2, group = couplets.couplet_catalogue_entries(pair, net)
        entries.extend([e1, e2])
        groups.append(group)
    # ``detected`` (not ``pairs``) goes to the registry validation: that table
    # measures **the detector**, and scoring it on the post-coverage subset would
    # blame it for couplets the export simply does not carry.
    return entries, groups, detected


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
                        help="per-district screening outputs (segment_peak_screen.parquet)")
    parser.add_argument("--report-dir", default="out/statewide_screening",
                        help="where the couplet validation table is written")
    parser.add_argument("--aadt", default="Cumulative_AADT.zip",
                        help="ITD cumulative AADT source; '' to skip the volume join")
    parser.add_argument("--aadt-year", type=int, default=aadt_mod.DEFAULT_YEAR)
    parser.add_argument("--window", default="am,pm",
                        help="peak window(s) the cores are read from; several are "
                             "combined as the worst TTI per segment")
    parser.add_argument("--min-core-miles", type=float, default=extents.MIN_CORE_MILES)
    parser.add_argument("--max-facilities", type=int, default=None,
                        help="keep only the N facilities with the largest core delay")
    parser.add_argument("--no-couplets", action="store_true")
    parser.add_argument("--dry-run", action="store_true",
                        help="generate and verify, but write nothing")
    args = parser.parse_args()

    out_dir = Path(args.out_dir)
    report_dir = Path(args.report_dir)
    all_pairs, all_ok = [], True

    for d in args.districts:
        print(f"\n{'=' * 70}\n  DISTRICT {d}\n{'=' * 70}")
        net, repairs = load_district(d, aadt_source=args.aadt or None,
                                     aadt_year=args.aadt_year)
        screen = load_screen(d, Path(args.screening_dir))
        if screen is None:
            print(f"  No screening frame for District {d} — extents need one; skipping.")
            all_ok = False
            continue
        observed = {int(s) for s in screen.index}

        cat = extents.generate_catalogue(
            net, screen_data=screen, window=args.window,
            min_core_miles=args.min_core_miles,
            max_facilities=args.max_facilities,
            observed=observed,
            note=(f"ITD District {d} screening catalogue, generated by "
                  f"inrix_tools.extents.generate_catalogue and "
                  f"inrix_tools.couplets.detect_couplets (ROADMAP Item 46). "
                  f"Hand-built predecessor in legacy/handbuilt_catalogues/."),
        )
        gen = cat["_generated"]
        print(f"  {gen['n_chains']} mainline chains -> {gen['n_facilities']} facilities "
              f"-> {len(cat['corridors'])} directional entries, "
              f"{len(cat['reporting_corridors'])} reporting corridors")

        if not args.no_couplets:
            c_entries, c_groups, pairs = couplet_block(net, d, observed)
            all_pairs.extend(pairs)
            cat = merge_blocks(cat, c_entries, c_groups)
            print(f"  + {len(pairs)} couplets detected, "
                  f"{len(c_entries) // 2} fully observed and catalogued "
                  f"({len(c_entries)} legs)")

        res = verify(cat, net, repairs, d, observed)
        passed = bool(res["reached_target"].all())
        all_ok &= passed

        out_path = out_dir / f"d{d}_corridors.json"
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
        # invocation did not build too (District 3's catalogue is the Item 44
        # empirical rebuild and is not regenerated here). Scoring the registry on a
        # partial statewide sweep would report every unvisited district as a miss.
        for d in sorted(set(range(1, 7)) - set(args.districts)):
            cache = Path(f"geometry_cache/d{d}_network.geoparquet")
            if not cache.exists():
                continue
            other = corridors.apply_link_repairs(
                gpd.read_parquet(cache),
                corridors.load_link_repairs(f"scripts/d{d}_link_repairs.csv"))
            all_pairs.extend(couplets.detect_couplets(
                other, counties=couplets.DISTRICT_COUNTIES.get(d)))

        report_dir.mkdir(parents=True, exist_ok=True)
        table = couplets.match_known_couplets(all_pairs)
        path = report_dir / "couplet_registry_validation.csv"
        table.to_csv(path, index=False)
        print(f"\nCouplet registry validation -> {path}")
        print(table["match_kind"].value_counts().to_string())

    print(f"\nStatewide catalogue generation: {'ALL VERIFIED' if all_ok else 'FINDINGS'}")
    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())

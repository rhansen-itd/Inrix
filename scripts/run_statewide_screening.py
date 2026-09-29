#!/usr/bin/env python3
"""Run corridor screening across all ITD districts (ROADMAP Item 45.5).

Orchestrates per-district screening or candidate triage using the
triage_candidates.py and run_district_screening.py pipelines, generating
individual district deliverables and a statewide summary.

Usage:
    python scripts/run_statewide_screening.py
    python scripts/run_statewide_screening.py --districts 1 2 3
    python scripts/run_statewide_screening.py --mode full --maps
    python scripts/run_statewide_screening.py --mode full \
        --scenarios peak,day_7d,sat:summer,sun:summer,weekend:summer

Every district is screened once per **scenario** (ROADMAP Item 67;
``screen.SCENARIO_PRESETS``, optionally ``:summer`` or ``:MM-DD..MM-DD``) on its one
typed catalogue (Item 66), so every scenario ranks the same corridors. The default,
``peak,day_7d``, is the run as it was before scenarios existed.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from inrix_tools.screen import (  # noqa: E402
    DEFAULT_SCENARIOS,
    SCENARIO_PRESETS,
    resolve_scenarios,
)

DISTRICT_TIMEZONES = {
    1: "America/Los_Angeles",
    2: "America/Los_Angeles",
    3: "America/Boise",
    4: "America/Boise",
    5: "America/Boise",
    6: "America/Boise",
}


def store_path(district: int) -> str:
    return f"d{district}_store.duckdb"


def repairs_path(district: int) -> str:
    return f"scripts/d{district}_link_repairs.csv"


CATALOGUE_OVERRIDES: dict[int, str] = {}
"""District -> catalogue path, from ``--catalogue-override`` (e.g. the archived curated
District 3 catalogue, ``legacy/d3_curated/d3_corridors.json``, instead of the generated
one, which is the default since Item 63)."""


def catalogue_path(district: int) -> str:
    return CATALOGUE_OVERRIDES.get(district, f"scripts/d{district}_corridors.json")


def parse_overrides(values) -> dict[int, str]:
    """``["3=legacy/d3_curated/d3_corridors.json"]`` -> ``{3: "legacy/..."}``."""
    out = {}
    for v in values or []:
        d, sep, path = str(v).partition("=")
        if not sep or not d.strip().isdigit() or not path.strip():
            raise SystemExit(f"--catalogue-override expects D=PATH, got {v!r}")
        out[int(d)] = path.strip()
    return out


def override_args(overrides: dict[int, str]) -> list[str]:
    return [a for d, p in sorted(overrides.items()) for a in ("--catalogue-override", f"{d}={p}")]


def scenario_list(scenarios: str, windows: str | None = None) -> list[str]:
    """The scenario specs to run, validated (``screen.resolve_scenarios``) so a typo
    fails before six districts are screened. ``--windows both``, the pre-Item 67
    spelling of the default, still means ``peak,day_7d``."""
    if windows is not None:
        if windows != "both":
            raise SystemExit(f"--windows {windows!r} is retired; use --scenarios "
                             f"(e.g. 'peak,day_7d,sat:summer').")
        scenarios = ",".join(DEFAULT_SCENARIOS)
    specs = [x for x in (p.strip() for p in scenarios.split(",")) if x]
    resolve_scenarios(specs)
    return specs


def network_cache_path(district: int) -> str:
    return f"geometry_cache/d{district}_network.geoparquet"


def run_triage(district: int, out_dir: Path) -> int:
    """Run triage_candidates.py for a single district."""
    dist_out = out_dir / f"d{district}"
    cmd = [
        sys.executable, "scripts/triage_candidates.py",
        "--db", store_path(district),
        "--network", "USA_Idaho_shapefile.zip",
        "--out-dir", str(dist_out),
    ]
    repairs = Path(repairs_path(district))
    if repairs.exists():
        cmd.extend(["--repairs", str(repairs)])
    cat = Path(catalogue_path(district))
    if cat.exists():
        cmd.extend(["--catalogue", str(cat)])
    cache = Path(network_cache_path(district))
    if cache.exists():
        cmd.extend(["--network-cache", str(cache)])

    print(f"\n{'='*60}")
    print(f"  DISTRICT {district} — CANDIDATE TRIAGE")
    print(f"{'='*60}")
    return subprocess.call(cmd)


def run_screening(district: int, out_dir: Path, scenario: str, extra_args: list[str]) -> int:
    """Run run_district_screening.py for a single district and scenario."""
    dist_out = out_dir / f"d{district}"
    cmd = [
        sys.executable, "scripts/run_district_screening.py",
        "--db", store_path(district),
        "--district", str(district),
        "--out-dir", str(dist_out),
        "--scenario", scenario,
    ]
    repairs = Path(repairs_path(district))
    if repairs.exists():
        cmd.extend(["--repairs", str(repairs)])
    else:
        cmd.append("--no-repairs")
    cat = Path(catalogue_path(district))
    if cat.exists():
        cmd.extend(["--catalogue", str(cat)])
    cache = Path(network_cache_path(district))
    if cache.exists():
        cmd.extend(["--network-cache", str(cache)])
    cmd.extend(extra_args)

    print(f"\n{'='*60}")
    print(f"  DISTRICT {district} — SCREENING PIPELINE ({scenario})")
    print(f"{'='*60}")
    return subprocess.call(cmd)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--districts", nargs="*", type=int, default=[1, 2, 3, 4, 5, 6])
    parser.add_argument("--mode", choices=["triage-only", "full"], default="triage-only",
                        help="'triage-only' runs candidate discovery; 'full' runs complete screening")
    parser.add_argument("--out-dir", default="out/statewide_screening")
    parser.add_argument("--scenarios", default=",".join(DEFAULT_SCENARIOS),
                        help="comma-separated ranking scenarios (ROADMAP Item 67): any of "
                             + ", ".join(SCENARIO_PRESETS)
                             + ", each optionally ':summer' or ':MM-DD..MM-DD' "
                             "(default: %(default)s)")
    parser.add_argument("--windows", default=None, help=argparse.SUPPRESS)
    parser.add_argument("--date-start", default=None, help="Inclusive local start date YYYY-MM-DD")
    parser.add_argument("--date-end", default=None, help="Inclusive local end date YYYY-MM-DD")
    parser.add_argument("--aadt", default="AADT_2025.zip")
    parser.add_argument("--aadt-year", type=int, default=2025)
    parser.add_argument("--maps", action="store_true")
    parser.add_argument("--catalogue-override", action="append", default=[], metavar="D=PATH",
                        help="screen district D on catalogue PATH instead of "
                             "scripts/dD_corridors.json (repeatable); passed on to the "
                             "aggregation and the statewide maps")
    args, extra = parser.parse_known_args()
    CATALOGUE_OVERRIDES.update(parse_overrides(args.catalogue_override))
    scenarios = scenario_list(args.scenarios, args.windows)

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    results: dict[str, int] = {}

    if args.mode == "triage-only":
        for d in args.districts:
            db = Path(store_path(d))
            if not db.exists():
                print(f"WARNING: {db} not found on disk, skipping District {d}")
                results[f"d{d}_triage"] = -1
                continue
            results[f"d{d}_triage"] = run_triage(d, out_dir)
    else:
        # Full screening mode: every district once per scenario.
        for d in args.districts:
            db = Path(store_path(d))
            if not db.exists():
                print(f"WARNING: {db} not found on disk, skipping District {d}")
                results[f"d{d}"] = -1
                continue

            for sc in scenarios:
                pass_through = list(extra)
                if args.aadt and "--aadt" not in pass_through:
                    pass_through.extend(["--aadt", args.aadt, "--aadt-year", str(args.aadt_year)])
                if args.date_start and "--date-start" not in pass_through:
                    pass_through.extend(["--date-start", args.date_start])
                if args.date_end and "--date-end" not in pass_through:
                    pass_through.extend(["--date-end", args.date_end])
                if args.maps and "--maps" not in pass_through:
                    pass_through.append("--maps")

                rc = run_screening(d, out_dir, sc, pass_through)
                results[f"d{d}_{sc}"] = rc
                if rc != 0:
                    print(f"ERROR: Screening failed for District {d} ({sc})")

        # Aggregate statewide rankings
        print(f"\n{'='*60}")
        print("  AGGREGATING STATEWIDE RANKINGS & ANALYSIS")
        print(f"{'='*60}")
        cmd_agg = [
            sys.executable, "scripts/aggregate_statewide_rankings.py",
            "--dir", str(out_dir),
            "--districts", *[str(d) for d in args.districts],
            "--scenarios", ",".join(scenarios),
            *override_args(CATALOGUE_OVERRIDES),
        ]
        rc_agg = subprocess.call(cmd_agg)
        results["statewide_aggregation"] = rc_agg

        # Generate statewide maps if requested
        if args.maps:
            print(f"\n{'='*60}")
            print("  GENERATING STATEWIDE MAP VISUALIZATIONS")
            print(f"{'='*60}")
            cmd_maps = [
                sys.executable, "scripts/generate_statewide_maps.py",
                "--dir", str(out_dir),
                "--districts", *[str(d) for d in args.districts],
                "--scenarios", ",".join(scenarios),
                *override_args(CATALOGUE_OVERRIDES),
            ]
            rc_maps = subprocess.call(cmd_maps)
            results["statewide_maps"] = rc_maps

    print(f"\n{'='*60}")
    print("  STATEWIDE SCREENING SUMMARY")
    print(f"{'='*60}")
    for k, rc in sorted(results.items()):
        if rc == 0:
            status = "SUCCESS"
        elif rc == -1:
            status = "SKIPPED (db missing)"
        else:
            status = f"FAILED (exit {rc})"
        print(f"  {k}: {status}")

    all_ok = all(rc in (0, -1) for rc in results.values())
    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())

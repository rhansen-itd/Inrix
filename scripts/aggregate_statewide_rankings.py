#!/usr/bin/env python3
"""Aggregate district corridor screening outputs into statewide rankings.

Reads the per-district peak and 7-day screening outputs from:
    out/statewide_screening/d{1..6}/corridor_peak_totals.csv
    out/statewide_screening/d{1..6}/corridor_7day_totals.csv

Produces:
1. out/statewide_screening/statewide_peak_corridor_rankings.csv
   - Statewide ranking of the **ranked** corridors by Peak Delay Density (VHD / Mile):
     Tier 1 cores and the couplets no core runs on. Tier 2 and Tier 3 extents, and a
     couplet a facility's core covers (Item 63), are context, not peers (ROADMAP
     Item 50), and go to
   ``statewide_peak_context_extents.csv`` with their facility's core rank.
2. out/statewide_screening/statewide_7day_corridor_rankings.csv
   - The same for the 7-day all-day window (context in
     ``statewide_7day_context_extents.csv``).
3. out/statewide_screening/statewide_couplet_rankings.csv
   - Synthesis and comparison of all one-way couplet facilities statewide.
4. out/statewide_screening/statewide_extent_tiers_comparison.csv
   - Multi-scale extent tier comparison (Core Bottleneck vs Commuter Extent).
5. out/statewide_screening/statewide_district_summary.csv
   - District-level roll-up metrics (Total VHD, Monitored Miles, Top Corridors).

**Scenarios (ROADMAP Item 67).** Items 1 and 2 are written for **every** scenario the
districts were screened under: ``statewide_<tag>_corridor_rankings.csv`` and
``statewide_<tag>_context_extents.csv``, where ``<tag>`` is ``peak``, ``7day`` or the
scenario's own tag (``sat_summer``). The scenarios come from ``--scenarios``, else from
each district's ``screening_scenarios.json``. With more than one, 6.
``statewide_scenario_matrix.csv`` lists each ranked corridor's rank, VHD/mile, VHD and
TTI under every scenario side by side. It is one catalogue, so the rows join exactly.
Every table carries the corridor-type columns (``corridor_types``,
``corridor_class``, ``primary_type``; Item 66).

Since Item 58 ``vhd`` is the curve-weighted vehicle-hours of delay on an average day of
the window (``vhd_per``: per weekday for the peaks, per calendar day for the 7-day
window). Districts on different bases are refused, not ranked together.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from inrix_tools import screen  # noqa: E402

DEFAULT_CATALOGUE = "scripts/d{district}_corridors.json"
SCENARIO_REGISTRY = "screening_scenarios.json"
"""Written by ``run_district_screening.py`` in each district's directory (Item 67)."""
TYPE_COLUMNS = ("corridor_types", "corridor_class", "primary_type")


def catalogue_file(pattern: str, district: int, overrides: dict | None = None) -> Path:
    """The catalogue a district was screened on: ``overrides`` wins over ``pattern``."""
    return Path((overrides or {}).get(district) or pattern.format(district=district))


def load_extent_tier_groups(districts, pattern: str = DEFAULT_CATALOGUE,
                            overrides: dict | None = None) -> list[dict]:
    """Read the multi-scale extent tiers out of the **generated** catalogues.

    Before ROADMAP Item 46 this was a hand-written list of six facilities
    (``EXTENT_TIER_GROUPS``), so the dilution comparison covered whatever someone
    had thought to type. The generated catalogues carry the tiering as data —
    each reporting corridor knows its ``_facility`` and ``_tier_number`` — so the
    comparison now covers every facility the pipeline catalogued.

    A catalogue with no tier metadata (an override such as the archived curated
    District 3 one, ``legacy/d3_curated/``) contributes nothing and is reported as
    such, rather than being back-filled by hand.
    """
    groups: list[dict] = []
    for d in districts:
        path = catalogue_file(pattern, d, overrides)
        if not path.exists():
            continue
        cat = json.loads(path.read_text())
        by_facility: dict[str, list[dict]] = {}
        for grp in cat.get("reporting_corridors", []):
            facility = grp.get("_facility")
            if not facility:
                continue
            by_facility.setdefault(facility, []).append(grp)
        for facility, tier_groups in by_facility.items():
            tier_groups.sort(key=lambda g: g.get("_tier_number", 0))
            if len(tier_groups) < 2:
                continue        # one extent is not a comparison
            groups.append({
                "facility": tier_groups[0].get("_facility_name", facility),
                "district": d,
                "tiers": [(g["id"], g.get("_tier_label", g.get("name", g["id"])))
                          for g in tier_groups],
            })
    return groups


def load_group_tiers(districts, pattern: str = DEFAULT_CATALOGUE,
                     overrides: dict | None = None) -> dict:
    """``(district, corridor_group) -> (tier_number, ranked, facility, flags)`` from the
    generated catalogues. A group with no tier metadata (a couplet) is absent and ranks
    as before, unless it is marked ``_ranked: false`` because a facility's core covers
    it (Item 63): then it is context under that facility (``_counted_in``). One a core
    covers only in part ranks, and carries its ``_flags``."""
    out = {}
    for d in districts:
        path = catalogue_file(pattern, d, overrides)
        if not path.exists():
            continue
        cat = json.loads(path.read_text())
        for grp in cat.get("reporting_corridors", []):
            if "_tier_number" not in grp:
                # A couplet whose legs a facility's core runs on (Item 63) is context
                # under that facility, not a second ranked row for the same delay.
                # A couplet a core runs on only in part ranks, with the overlap flagged.
                if grp.get("_ranked") is False:
                    out[(d, grp["id"])] = (pd.NA, False, grp.get("_counted_in"),
                                           f"counted in {grp.get('_counted_in')}")
                elif grp.get("_flags"):
                    out[(d, grp["id"])] = (pd.NA, True, None, "; ".join(grp["_flags"]))
                continue
            ranked = grp.get("_ranked", grp["_tier_number"] == 1)
            out[(d, grp["id"])] = (grp["_tier_number"], bool(ranked), grp.get("_facility"),
                                   "; ".join(grp.get("_flags", [])))
    return out


def load_group_types(districts, pattern: str = DEFAULT_CATALOGUE,
                     overrides: dict | None = None) -> dict:
    """``(district, corridor_group) -> {corridor_types, corridor_class, primary_type}``
    from the typed catalogues (ROADMAP Item 66); absent for an untyped group."""
    out = {}
    for d in districts:
        path = catalogue_file(pattern, d, overrides)
        if not path.exists():
            continue
        cat = json.loads(path.read_text())
        for grp in cat.get("reporting_corridors", []):
            if "_types" in grp:
                out[(d, grp["id"])] = {"corridor_types": "+".join(grp["_types"]),
                                       "corridor_class": grp.get("_class", ""),
                                       "primary_type": grp.get("_primary_type", "")}
    return out


def attach_types(frame: pd.DataFrame, group_types: dict) -> pd.DataFrame:
    """``frame`` with the type columns after ``corridor_group``, from the catalogues,
    unless the district tables already carried them (Item 67's runner writes them)."""
    if frame.empty or not group_types or all(c in frame.columns for c in TYPE_COLUMNS):
        return frame
    out = frame.copy()
    keys = list(zip(out["district"], out["corridor_group"]))
    at = list(out.columns).index("corridor_group") + 1
    for i, col in enumerate(TYPE_COLUMNS):
        if col not in out.columns:
            out.insert(at + i, col, [group_types.get(k, {}).get(col, "") for k in keys])
    return out


def scenario_tables(base_dir: Path, districts, specs=None) -> list[dict]:
    """The scenarios to aggregate, as ``{tag, label, windows, totals, breakout}``
    (Item 67); ``windows`` is ``{name: screen.PeakWindow}``.

    Named by ``specs`` (``screen.resolve_scenarios``), else discovered from each
    district's ``screening_scenarios.json``, the peak first. With no registry at all
    (tables written before scenarios), the historic peak and 7-day pair."""
    def _entry(sc: screen.Scenario):
        tag = sc.file_tag
        totals = "corridor_peak_totals.csv" if tag == "peak" else f"corridor_{tag}_totals.csv"
        breakout = "corridor_breakout.csv" if tag == "peak" else f"corridor_{tag}_breakout.csv"
        return {"tag": tag, "label": sc.label, "windows": sc.window_map,
                "totals": totals, "breakout": breakout}

    if specs:
        return [_entry(sc) for sc in screen.resolve_scenarios(specs)]
    found: dict[str, dict] = {}
    for d in districts:
        path = Path(base_dir) / f"d{d}" / SCENARIO_REGISTRY
        if not path.exists():
            continue
        for tag, info in json.loads(path.read_text()).items():
            if tag in found:
                continue
            spec = info.get("scenario") or {}
            windows = tuple(
                screen.PeakWindow(w["name"], w["window"],
                                  None if w.get("days") is None else tuple(w["days"]),
                                  bool(w.get("peak")),
                                  None if not w.get("season") else tuple(w["season"]))
                for w in (spec.get("windows") or {}).values())
            e = _entry(screen.Scenario(spec.get("name", tag), info.get("label", tag),
                                       windows, None if tag == "peak" else tag))
            e["totals"] = info.get("files", {}).get("totals", e["totals"])
            e["breakout"] = info.get("files", {}).get("breakout", e["breakout"])
            found[tag] = e
    if not found:
        return [_entry(screen.resolve_scenario("peak")),
                _entry(screen.resolve_scenario("day_7d"))]
    order = ["peak", "7day"]
    return sorted(found.values(), key=lambda e: (e["tag"] not in order,
                                                 order.index(e["tag"]) if e["tag"] in order
                                                 else 0))


def scenario_matrix(ranked: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """One row per ranked corridor, its rank, VHD/mile, VHD and TTI under every
    scenario side by side (Item 67). Every scenario ranks the same typed catalogue, so
    the rows join on ``(district, corridor_group)`` exactly — what the retired
    rec-vs-commute comparison could not do across two catalogues."""
    key = ["district", "corridor_group"]
    ident = ["group_name", "miles", "facility", *TYPE_COLUMNS]
    frames = [df for df in ranked.values() if not df.empty]
    if not frames:
        return pd.DataFrame()
    base = pd.concat([df[key + [c for c in ident if c in df.columns]] for df in frames],
                     ignore_index=True).drop_duplicates(subset=key, keep="first")
    out = base
    for tag, df in ranked.items():
        if df.empty:
            continue
        cols = {"statewide_rank": f"{tag}_rank", "vhd_per_mile": f"{tag}_vhd_per_mile",
                "vhd": f"{tag}_vhd", "tti": f"{tag}_tti"}
        part = df[key + [c for c in cols if c in df.columns]].rename(columns=cols)
        out = out.merge(part, on=key, how="left")
    first = next(f"{t}_rank" for t, df in ranked.items() if not df.empty)
    return out.sort_values(first, na_position="last").reset_index(drop=True)


def split_ranked(combined: pd.DataFrame, group_tiers: dict,
                 rank_col: str = "vhd_per_mile") -> tuple[pd.DataFrame, pd.DataFrame]:
    """Split a combined table into the **ranked** rows and the **context** rows.

    Before Item 50 every tier ranked as a peer, so a facility could appear three
    times and a 98-mile Tier 3 sat between two real bottlenecks. The ranked table
    carries the Tier 1 cores (and the untiered groups); the context table carries
    Tiers 2 and 3, each with the statewide rank of its facility's core.
    """
    if combined.empty:
        return combined, combined
    keys = list(zip(combined["district"], combined["corridor_group"]))
    meta = [group_tiers.get(k) for k in keys]
    combined = combined.copy()
    combined["tier"] = [m[0] if m else pd.NA for m in meta]
    combined["facility"] = [m[2] if m else pd.NA for m in meta]
    # Flags (e.g. an episodic, work-zone-like core) travel with the row: they are
    # for the reader, never a reason to drop it (Item 50).
    combined["flags"] = [m[3] if m and len(m) > 3 else "" for m in meta]
    is_ranked = pd.Series([m is None or m[1] for m in meta], index=combined.index)

    ranked = combined[is_ranked].drop(columns=["statewide_rank"], errors="ignore")
    ranked = ranked.sort_values(by=rank_col, ascending=False,
                                na_position="last").reset_index(drop=True)
    ranked.insert(0, "statewide_rank", ranked.index + 1)

    context = combined[~is_ranked].drop(columns=["statewide_rank"], errors="ignore")
    core_rank = {(r.district, r.facility): r.statewide_rank
                 for r in ranked.itertuples() if pd.notna(r.facility)}
    context.insert(0, "core_statewide_rank",
                   [core_rank.get((d, f)) for d, f in zip(context["district"],
                                                            context["facility"])])
    context = context.sort_values(["core_statewide_rank", "tier"],
                                  na_position="last").reset_index(drop=True)
    return ranked, context


def load_district_table(csv_path: Path, district: int) -> pd.DataFrame | None:
    """Load a corridor totals CSV, skipping its leading provenance header.

    The header is a block of ``# key: value`` lines at the top of the file, and it
    is skipped by **counting** those lines rather than by ``comment="#"`` — that
    option treats a ``#`` anywhere in a line as the start of a comment, so a
    corridor named after an interchange ("IC #12") silently truncates its own row
    and arrives as a line of nulls.
    """
    if not csv_path.exists():
        return None
    with csv_path.open() as fh:
        skip = 0
        for line in fh:
            if not line.startswith("#"):
                break
            skip += 1
    df = pd.read_csv(csv_path, skiprows=skip)
    if df.empty:
        return None
    df["district"] = district
    return df


def _vhd_basis(frame: pd.DataFrame) -> str:
    """What a district table's ``vhd`` is: the curve VHD's ``vhd_per`` (Item 57), or
    ``index`` for a table written before Item 58 (no ``vhd_per`` column, or none set)."""
    if "vhd_per" not in frame.columns or frame["vhd_per"].isna().all():
        return "index"
    return "/".join(sorted(str(p) for p in frame["vhd_per"].dropna().unique()))


def check_vhd_basis(frames: list[pd.DataFrame]) -> str:
    """Refuse to rank districts whose ``vhd`` are different quantities against each
    other: a stale Item 54 index table beside curve VHD (about 6x larger), or vehicle-
    hours per weekday beside per calendar day. Returns the shared basis."""
    bases = {int(f["district"].iloc[0]): _vhd_basis(f) for f in frames}
    if len(set(bases.values())) > 1:
        raise SystemExit(f"District tables are on different VHD bases {bases}; re-run "
                         f"the stale districts' screening before aggregating.")
    return next(iter(bases.values()))


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
    check_vhd_basis(frames)

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
    peak_df: pd.DataFrame, day7_df: pd.DataFrame, tier_groups: list[dict]
) -> pd.DataFrame:
    """Analyze dilution and metric shifts across multi-scale extent tiers.

    ``tier_groups`` comes from :func:`load_extent_tier_groups` — the generated
    catalogues' own tiering, not a hand-kept list.
    """
    p_indexed = peak_df.set_index("corridor_group") if not peak_df.empty else None
    d7_indexed = day7_df.set_index("corridor_group") if not day7_df.empty else None

    rows = []
    for grp in tier_groups:
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
        peak_per = _vhd_basis(p_sub)
        day7_per = _vhd_basis(d7_sub) if not d7_sub.empty else None
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
            # What the totals are per (Item 58): "weekday" for the peaks, "day" for
            # the 7-day window; "index" for a table from before the curve VHD.
            "peak_vhd_per": peak_per,
            "day7_vhd_per": day7_per,
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
    parser.add_argument("--catalogue", default=DEFAULT_CATALOGUE,
                        help="catalogue path pattern the extent tiers are read from")
    parser.add_argument("--catalogue-override", action="append", default=[], metavar="D=PATH",
                        help="district D's catalogue is PATH (repeatable)")
    parser.add_argument("--scenarios", default=None,
                        help="comma-separated scenarios to aggregate (default: every "
                             "scenario the districts' screening_scenarios.json lists)")
    args = parser.parse_args()
    overrides = {}
    for v in args.catalogue_override:
        d, _, path = v.partition("=")
        overrides[int(d)] = path

    base_dir = Path(args.dir)
    group_tiers = load_group_tiers(args.districts, args.catalogue, overrides)
    group_types = load_group_types(args.districts, args.catalogue, overrides)

    def _write(label: str, filename: str, stem: str) -> pd.DataFrame:
        """Aggregate, split ranked/context, write both; return the **full** table
        (the tier comparison reads every tier)."""
        print(f"Aggregating statewide {label} rankings...")
        full = aggregate_rankings(base_dir, filename, args.districts, rank_col="vhd_per_mile")
        if full.empty:
            return full
        full = attach_types(full, group_types)
        ranked, context = split_ranked(full, group_tiers)
        ranked_by_tag[stem] = ranked
        r_path = base_dir / f"statewide_{stem}_corridor_rankings.csv"
        ranked.to_csv(r_path, index=False)
        print(f"  -> Written {r_path} ({len(ranked)} ranked corridors)")
        c_path = base_dir / f"statewide_{stem}_context_extents.csv"
        context.to_csv(c_path, index=False)
        print(f"  -> Written {c_path} ({len(context)} Tier 2/3 context extents)")
        return full.merge(ranked[["district", "corridor_group", "statewide_rank"]],
                          on=["district", "corridor_group"], how="left",
                          suffixes=("_all", ""))

    ranked_by_tag: dict[str, pd.DataFrame] = {}
    written = {e["tag"]: _write(e["label"], e["totals"], e["tag"])
               for e in scenario_tables(base_dir, args.districts, args.scenarios)}
    peak_rankings = written.get("peak", pd.DataFrame())
    day7_rankings = written.get("7day", pd.DataFrame())

    if len(ranked_by_tag) > 1:
        print("Generating the cross-scenario matrix...")
        matrix = scenario_matrix(ranked_by_tag)
        m_path = base_dir / "statewide_scenario_matrix.csv"
        matrix.to_csv(m_path, index=False)
        print(f"  -> Written {m_path} ({len(matrix)} corridors x "
              f"{len(ranked_by_tag)} scenarios)")

    if not peak_rankings.empty:
        print("Generating statewide couplet synthesis...")
        couplets = build_couplet_analysis(peak_rankings, day7_rankings)
        if not couplets.empty:
            c_path = base_dir / "statewide_couplet_rankings.csv"
            couplets.to_csv(c_path, index=False)
            print(f"  -> Written {c_path} ({len(couplets)} couplets)")

        print("Generating multi-scale extent tiers comparison...")
        tier_groups = load_extent_tier_groups(args.districts, args.catalogue, overrides)
        covered = sorted({g["district"] for g in tier_groups})
        missing = [d for d in args.districts if d not in covered]
        print(f"  {len(tier_groups)} tiered facilities read from the generated "
              f"catalogues (districts {covered or 'none'}).")
        if missing:
            print(f"  Districts {missing} carry no tier metadata — their catalogues "
                  f"are not generated passes, so they are absent from the dilution "
                  f"table rather than hand-listed.")
        tiers = build_extent_tiers_analysis(peak_rankings, day7_rankings, tier_groups)
        if not tiers.empty:
            t_path = base_dir / "statewide_extent_tiers_comparison.csv"
            tiers.to_csv(t_path, index=False)
            print(f"  -> Written {t_path} ({len(tiers)} facility tiers)")

        print("Generating district summary roll-up...")
        dist_summary = build_district_summary(
            peak_rankings[peak_rankings["statewide_rank"].notna()],
            day7_rankings[day7_rankings["statewide_rank"].notna()]
            if not day7_rankings.empty else day7_rankings, args.districts)
        if not dist_summary.empty:
            ds_path = base_dir / "statewide_district_summary.csv"
            dist_summary.to_csv(ds_path, index=False)
            print(f"  -> Written {ds_path}")

    print("\nStatewide aggregation complete!")


if __name__ == "__main__":
    main()

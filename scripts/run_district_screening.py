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

    python scripts/run_district_screening.py \\
        --db d3_store.duckdb \\
        --catalogue scripts/d3_corridors.json \\
        --repairs scripts/d3_link_repairs.csv \\
        --network-cache geometry_cache/d3_network.geoparquet \\
        --aadt Cumulative_AADT.zip \\
        --out-dir out/district_screening

With a 7-day all-day window (6:00 AM – 9:00 PM, all 7 days) and interactive maps::

    python scripts/run_district_screening.py \\
        --db d3_store.duckdb --windows day_7d --maps ...

``--windows`` accepts any comma-separated preset names from ``screen.ALL_WINDOWS``
(``am``, ``pm``, ``midday``, ``night``, ``day_7d``).

``--maps`` generates standalone interactive HTML map visualizations of all segments
and ranked corridors in the output directory. No GIS software required.

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
    if not enabled or path is None:
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


DEFAULT_SHS = "SHS_Primary.zip"


def shs_source(path=DEFAULT_SHS):
    """The State Highway System path when it is present, else ``None`` (the volume
    join then classifies records by their descriptions alone, as before Item 52)."""
    return path if path and Path(path).exists() else None


def join_volumes(geo, aadt_source, *, year, cache_path, max_distance_m, bbox_margin,
                 shs=None):
    """Mainline-preferred AADT for the corridor members (Item 34); record kinds from the
    State Highway System where it is unambiguous (Item 52)."""
    if aadt_source is None:
        return None
    bounds = geo["geometry"].dropna().total_bounds
    bbox = (bounds[0] - bbox_margin, bounds[1] - bbox_margin,
            bounds[2] + bbox_margin, bounds[3] + bbox_margin)
    layer = aadt_mod.load_aadt(aadt_source, year=year, bbox=bbox, cache_path=cache_path,
                               shs=shs)
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
                  totals=None, breakout=None, write_kml=True, is_7day: bool = False) -> dict:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    written = {}

    prov_fname = "screening_7day_provenance.json" if is_7day else "screening_provenance.json"
    prov_path = out_dir / prov_fname
    prov_path.write_text(json.dumps(prov, indent=2, default=str) + "\n")
    written["provenance"] = prov_path

    header = "".join(f"# {k}: {json.dumps(v, default=str)}\n" for k, v in prov.items())
    ranking_fname = "corridor_7day_rankings.csv" if is_7day else "corridor_rankings.csv"
    tables = [(ranking_fname, ranking, False),
              ("corridor_resolution.csv", resolution, False)]
    if grouped is not None:
        grouped_fname = "reporting_corridor_7day_rankings.csv" if is_7day else "reporting_corridor_rankings.csv"
        tables.insert(0, (grouped_fname, grouped, False))
    if breakout is not None:
        breakout_fname = "corridor_7day_breakout.csv" if is_7day else "corridor_breakout.csv"
        tables.insert(0, (breakout_fname, breakout, True))
    if totals is not None:
        totals_fname = "corridor_7day_totals.csv" if is_7day else "corridor_peak_totals.csv"
        tables.insert(0, (totals_fname, totals, False))
    for name, frame, with_index in tables:
        path = out_dir / name
        with path.open("w") as fh:
            fh.write(header)
            frame.to_csv(fh, index=with_index)
        written[name.split(".")[0]] = path

    if write_kml and not geo.empty and not is_7day:
        path = out_dir / "corridors.kml"
        drawable = geo.dropna(subset=["geometry"]).set_index("Segment ID")
        kml.geometry_to_kml(drawable, path, color_by="corridor", folder_by="corridor",
                            palette=CORRIDOR_PALETTE,
                            document_name="District screening corridors")
        written["kml"] = path
    return written


# ---------------------------------------------------------------------------
# Interactive HTML maps (--maps)
# ---------------------------------------------------------------------------
def _extract_linestring_coords(geom):
    """Extract (lat, lon) coordinate lists from a Shapely geometry.

    Returns ``(lats, lons)`` with ``None`` separators between disjoint parts
    (for Plotly Scattermap line-break convention).
    """
    from shapely.geometry import LineString, MultiLineString

    if geom is None or geom.is_empty:
        return [], []
    if isinstance(geom, LineString):
        lons, lats = zip(*geom.coords)
        return list(lats), list(lons)
    if isinstance(geom, MultiLineString):
        all_lats, all_lons = [], []
        for line in geom.geoms:
            lons, lats = zip(*line.coords)
            all_lats.extend(lats)
            all_lats.append(None)
            all_lons.extend(lons)
            all_lons.append(None)
        if all_lats and all_lats[-1] is None:
            all_lats.pop()
            all_lons.pop()
        return all_lats, all_lons
    return [], []


# TTI tier definitions: label, color, line width.
_TTI_TIERS = [
    ("Free Flow (TTI < 1.10)",              1.10, "#4a5568", 1.8),
    ("Minor Delay (1.10 ≤ TTI < 1.25)",     1.25, "#d69e2e", 3.0),
    ("Moderate Delay (1.25 ≤ TTI < 1.50)",  1.50, "#dd6b20", 4.0),
    ("Severe Congestion (TTI ≥ 1.50)",      None, "#e53e3e", 5.2),
]

# VHD/mile tier definitions: label, upper bound, color, line width.
_VHD_TIERS = [
    ("Low / Free Flow (< 25 VHD/mi)",        25.0,  "#4a5568", 1.8),
    ("Minor Delay (25–100 VHD/mi)",          100.0, "#d69e2e", 3.0),
    ("Moderate Delay (100–300 VHD/mi)",      300.0, "#dd6b20", 4.0),
    ("Severe Congestion (≥ 300 VHD/mi)",     None,  "#e53e3e", 5.2),
]

# Segments the AADT join never reached get their own tier rather than being
# folded into the lowest one — an unvolumed segment is *unknown*, not free-flowing,
# and a join that silently matched nothing must look wrong on the map at a glance.
_VHD_NO_DATA_TIER = ("No AADT Data (unvolumed)", "#9f7aea", 1.4)


def _segment_tti_frame(scr, net_indexed, windows, aadt=None):
    """Build a GeoDataFrame of per-segment worst-window metrics for map rendering.

    Each segment gets: ``worst_tti``, ``worst_speed``, ``worst_window``,
    ``ref_speed``, ``worst_delay_rate`` (min/mi), optional ``worst_vhd_per_mile``,
    and the network geometry.
    """
    import numpy as np

    wins = screen.resolve_windows(windows)
    peak_wins = {n: w for n, w in wins.items() if w.peak}
    if not peak_wins:
        peak_wins = wins          # all windows if none flagged peak

    lengths = net_indexed["Miles"]
    seg_miles = scr.index.map(lengths)
    ref_sp = scr["ref_speed"]
    ff_time = (seg_miles / ref_sp) * 60.0

    best_tti = None
    best_speed = None
    best_window = None
    best_delay = None
    for name in peak_wins:
        tt_col = f"{name}_travel_time"
        sp_col = f"{name}_speed"
        if tt_col not in scr.columns:
            continue
        tt = scr[tt_col]
        tti = (tt / ff_time).fillna(1.0)
        delay = np.maximum(0, tt - ff_time)
        if best_tti is None:
            best_tti = tti
            best_speed = scr[sp_col]
            best_window = pd.Series(peak_wins[name].window, index=scr.index)
            best_delay = delay
        else:
            worse = tti > best_tti
            best_tti = best_tti.where(~worse, tti)
            best_speed = best_speed.where(~worse, scr[sp_col])
            best_window = best_window.where(~worse, peak_wins[name].window)
            best_delay = best_delay.where(~worse, delay)

    df = pd.DataFrame(index=scr.index)
    df["worst_tti"] = best_tti if best_tti is not None else 1.0
    df["worst_speed"] = best_speed if best_speed is not None else ref_sp
    df["worst_window"] = best_window if best_window is not None else "—"
    df["ref_speed"] = ref_sp
    df["worst_delay_rate"] = (best_delay / seg_miles) if best_delay is not None else 0.0

    if aadt is not None:
        # A segment the AADT join did not reach stays **NaN**, never 0. A zero here
        # is indistinguishable from free flow on the map, which is exactly how a
        # statewide join that matched nothing once rendered as "everything is fine"
        # (Session 60). This matches screen.rank_corridors, which leaves vhd NaN and
        # counts the misses in ``n_aadt_missing`` rather than zero-filling them.
        aadt_vol = aadt["AADT"] if "AADT" in getattr(aadt, "columns", ()) else aadt
        seg_aadt = pd.Series(df.index.map(aadt_vol), index=df.index, dtype="float64")
        df["aadt"] = seg_aadt
        df["worst_vhd_per_mile"] = (df["worst_delay_rate"] / 60.0) * seg_aadt
        df["worst_vhd"] = ((best_delay / 60.0) * seg_aadt if best_delay is not None
                           else pd.Series(np.nan, index=df.index))
    else:
        df["aadt"] = np.nan
        df["worst_vhd_per_mile"] = np.nan
        df["worst_vhd"] = np.nan

    import geopandas as gpd

    net_sub = net_indexed.loc[net_indexed.index.isin(df.index)].copy()
    return gpd.GeoDataFrame(net_sub.join(df), geometry="geometry", crs=net_sub.crs)


def _fmt_or_na(val, fmt=",.0f") -> str:
    """Format a metric, or ``n/a`` when it is missing — the same convention the
    ranking tables print, so a map tooltip can never imply a volume it lacks."""
    if val is None or pd.isna(val):
        return "n/a"
    return format(float(val), fmt)


def _build_segment_vhd_traces(merged, window_label="Peak"):
    """Build Plotly Scattermap traces for all segments, tiered by VHD / Mile.

    Segments whose ``worst_vhd_per_mile`` is NaN (no AADT joined) get their own
    ``No AADT Data`` trace instead of falling into the lowest delay tier.
    """
    import plotly.graph_objects as go

    rate = merged["worst_vhd_per_mile"]

    # Unvolumed segments first, then the delay-density tiers over the rest.
    nd_label, nd_color, nd_width = _VHD_NO_DATA_TIER
    buckets = [(nd_label, merged[rate.isna()], nd_color, nd_width)]
    prev_upper = 0.0
    for label, upper, color, width in _VHD_TIERS:
        mask = rate.notna() & (rate >= prev_upper)
        if upper is not None:
            mask &= rate < upper
        buckets.append((label, merged[mask], color, width))
        prev_upper = upper if upper is not None else prev_upper

    traces = []
    for label, subset, color, width in buckets:
        all_lats, all_lons, all_texts = [], [], []
        for sid, row in subset.iterrows():
            lats, lons = _extract_linestring_coords(row.geometry)
            if not lats:
                continue
            rname = row.get("RoadName", "Segment") or "Segment"
            rnum = row.get("RoadNumber", "") or ""
            bearing = row.get("Bearing", "") or ""
            route_str = f"Route {rnum} ({bearing})" if rnum else bearing
            miles = row.get("Miles", 0.0)
            # "n/a", never "0" — a segment with no joined volume has no delay
            # density, and printing a zero would assert free flow it cannot know.
            aadt_val = _fmt_or_na(row.get("aadt"))
            vhd_val = _fmt_or_na(row.get("worst_vhd"))
            vhd_rate = _fmt_or_na(row.get("worst_vhd_per_mile"))
            tooltip = (
                f"<b>{rname}</b> {route_str}<br>"
                f"<b>Window:</b> {row.get('worst_window', window_label)} | "
                f"<b>Length:</b> {miles:.2f} mi<br>"
                f"<b>Delay Density:</b> {vhd_rate} VHD/mi "
                f"({vhd_val} veh-hrs)<br>"
                f"<b>AADT:</b> {aadt_val} veh/day<br>"
                f"<b>TTI:</b> {row['worst_tti']:.2f} | "
                f"<b>Delay Rate:</b> {row['worst_delay_rate']:.2f} min/mi<br>"
                f"<b>Speed:</b> {row['worst_speed']:.1f} mph "
                f"(Ref: {row['ref_speed']:.0f} mph)<br>"
                f"<span style='font-size:10px;color:#718096'>Segment ID: {sid}</span>"
            )
            for lat, lon in zip(lats, lons):
                if lat is None:
                    all_lats.append(None); all_lons.append(None); all_texts.append(None)
                else:
                    all_lats.append(lat); all_lons.append(lon); all_texts.append(tooltip)
            all_lats.append(None); all_lons.append(None); all_texts.append(None)

        traces.append(go.Scattermap(
            lat=all_lats, lon=all_lons, mode="lines",
            line=dict(color=color, width=width),
            name=f"{label} ({len(subset):,} segs)",
            text=all_texts, hoverinfo="text",
            hoverlabel=dict(bgcolor="rgba(255,255,255,0.95)",
                            font_size=12, font_color="#1a202c"),
        ))
    return traces


def _build_segment_traces(merged, window_label="Peak"):
    """Build Plotly Scattermap traces for all segments, tiered by TTI."""
    import plotly.graph_objects as go

    traces = []
    prev_upper = 0.0
    for label, upper, color, width in _TTI_TIERS:
        if upper is not None:
            mask = (merged["worst_tti"] >= prev_upper) & (merged["worst_tti"] < upper)
        else:
            mask = merged["worst_tti"] >= prev_upper
        subset = merged[mask]
        prev_upper = upper or 999.0

        all_lats, all_lons, all_texts = [], [], []
        for sid, row in subset.iterrows():
            lats, lons = _extract_linestring_coords(row.geometry)
            if not lats:
                continue
            rname = row.get("RoadName", "Segment") or "Segment"
            rnum = row.get("RoadNumber", "") or ""
            bearing = row.get("Bearing", "") or ""
            route_str = f"Route {rnum} ({bearing})" if rnum else bearing
            miles = row.get("Miles", 0.0)
            tooltip = (
                f"<b>{rname}</b> {route_str}<br>"
                f"<b>Window:</b> {row.get('worst_window', window_label)} | "
                f"<b>Length:</b> {miles:.2f} mi<br>"
                f"<b>TTI:</b> {row['worst_tti']:.2f} | "
                f"<b>Speed:</b> {row['worst_speed']:.1f} mph "
                f"(Ref: {row['ref_speed']:.0f} mph)<br>"
                f"<b>Excess Delay:</b> {row['worst_delay_rate']:.2f} min/mi<br>"
                f"<span style='font-size:10px;color:#718096'>Segment ID: {sid}</span>"
            )
            for lat, lon in zip(lats, lons):
                if lat is None:
                    all_lats.append(None); all_lons.append(None); all_texts.append(None)
                else:
                    all_lats.append(lat); all_lons.append(lon); all_texts.append(tooltip)
            all_lats.append(None); all_lons.append(None); all_texts.append(None)

        traces.append(go.Scattermap(
            lat=all_lats, lon=all_lons, mode="lines",
            line=dict(color=color, width=width),
            name=f"{label} ({len(subset):,} segs)",
            text=all_texts, hoverinfo="text",
            hoverlabel=dict(bgcolor="rgba(255,255,255,0.95)",
                            font_size=12, font_color="#1a202c"),
        ))
    return traces


# Corridor outline styling (underlay casing behind segment TTI lines).
# Light mode / street map uses a crisp black casing; dark mode switches dynamically to white.
_CORRIDOR_OUTLINE_LIGHT = "#1a202c"
_CORRIDOR_OUTLINE_DARK = "#ffffff"
_CORRIDOR_OUTLINE_WIDTH = 8.5


def _bearing_deg(p1: tuple[float, float], p2: tuple[float, float]) -> float:
    """Bearing from p1 (lat, lon) to p2 (lat, lon) in degrees clockwise from North."""
    import math

    lat1, lon1 = p1
    lat2, lon2 = p2
    d_lat = lat2 - lat1
    d_lon = (lon2 - lon1) * math.cos(math.radians(lat1))
    if d_lat == 0 and d_lon == 0:
        return 0.0
    return math.degrees(math.atan2(d_lon, d_lat)) % 360.0


def _build_corridor_overlay(cat_entries, chains, corridor_ranks, net_indexed,
                            delay_label="Total Peak Delay"):
    """Build Plotly traces for ranked corridor centerlines and termini markers.

    Returns ``(line_traces, marker_traces)``. Each corridor group produces:
    1. A Scattermap line trace on ``legend2`` (defaulting to ``visible='legendonly'``),
       styled in a crisp casing wider than the segments so it renders as an underlay.
    2. A Scattermap marker trace with inward-pointing triangles (``>-<``) at each
       extent terminus, linked to the same ``legendgroup`` with ``showlegend=False``.
    """
    import plotly.graph_objects as go

    # Group accepted catalogue entries by corridor group (or entry id if ungrouped)
    entries_by_group: dict[str, list] = {}
    for entry in cat_entries:
        if entry.id in chains and chains[entry.id].reached_target:
            gid = entry.corridor or entry.id
            entries_by_group.setdefault(gid, []).append(entry)

    def _corridor_sort_key(gid: str):
        ri = corridor_ranks.get(gid, {})
        rank = ri.get("rank")
        if pd.notna(rank) and rank != "—":
            try:
                return (0, float(rank), gid)
            except (ValueError, TypeError):
                pass
        return (1, 999999, gid)

    sorted_groups = sorted(entries_by_group.keys(), key=_corridor_sort_key)

    kw = dict(hoverinfo="text",
              hoverlabel=dict(bgcolor="rgba(255,255,255,0.95)",
                              font_size=12, font_color="#1a202c"))

    line_traces = []
    marker_traces = []

    for gid in sorted_groups:
        ri = corridor_ranks.get(gid, {})
        rank = ri.get("rank", "—")
        gname = ri.get("group_name", gid)
        rank_str = f"#{int(rank)}" if (pd.notna(rank) and rank != "—") else "—"

        c_lats, c_lons, c_texts = [], [], []
        m_lats, m_lons, m_angles, m_texts = [], [], [], []

        for entry in entries_by_group[gid]:
            ch = chains[entry.id]
            vhd_rate = ri.get("vhd_per_mile", 0.0)
            total_vhd = ri.get("vhd", 0.0)
            tti = ri.get("tti", 0.0)
            tip = (
                f"<b>RANK {rank_str}: {entry.name}</b><br>"
                f"<b>Direction:</b> {entry.direction} | "
                f"<b>Length:</b> {ch.chain_miles:.2f} mi<br>"
                f"<b>TTI:</b> {tti:.2f} | <b>VHD/mi:</b> {vhd_rate:,.1f}<br>"
                f"<b>{delay_label}:</b> {total_vhd:,.0f} veh-hrs<br>"
                f"<i>{entry.description[:120]}...</i>"
            )

            coords = []
            for sid in ch.segment_ids:
                if int(sid) in net_indexed.index:
                    lats, lons = _extract_linestring_coords(
                        net_indexed.loc[int(sid)].geometry)
                    if not lats:
                        continue
                    for lat, lon in zip(lats, lons):
                        if lat is not None and lon is not None:
                            coords.append((lat, lon))
                            c_lats.append(lat); c_lons.append(lon); c_texts.append(tip)
                    c_lats.append(None); c_lons.append(None); c_texts.append(None)
            if not coords:
                continue

            # Inward-pointing triangles at extent limits (>-<)
            if len(coords) >= 2:
                # Start terminus: points inward along corridor from coords[0]
                p0 = coords[0]
                p_next = next((p for p in coords[1:] if p != p0), coords[1])
                angle_start = _bearing_deg(p0, p_next)

                m_lats.append(p0[0]); m_lons.append(p0[1])
                m_angles.append(round(angle_start, 1))
                m_texts.append(
                    f"<b>▲ Terminus: {entry.name}</b><br>"
                    f"<b>Rank:</b> {rank_str} ({gid})<br>"
                    f"<b>Direction:</b> {entry.direction} | "
                    f"<b>Extent:</b> {ch.chain_miles:.2f} mi<br>"
                    f"<b>Coordinates:</b> ({p0[0]:.4f}, {p0[1]:.4f})"
                )

                # End terminus: points inward along corridor back from coords[-1]
                pN = coords[-1]
                p_prev = next((p for p in reversed(coords[:-1]) if p != pN), coords[-2])
                angle_end = _bearing_deg(pN, p_prev)

                m_lats.append(pN[0]); m_lons.append(pN[1])
                m_angles.append(round(angle_end, 1))
                m_texts.append(
                    f"<b>▲ Terminus: {entry.name}</b><br>"
                    f"<b>Rank:</b> {rank_str} ({gid})<br>"
                    f"<b>Direction:</b> {entry.direction} | "
                    f"<b>Extent:</b> {ch.chain_miles:.2f} mi<br>"
                    f"<b>Coordinates:</b> ({pN[0]:.4f}, {pN[1]:.4f})"
                )

        trace_label = f"{rank_str} {gname}" if rank_str != "—" else gname
        line_traces.append(
            go.Scattermap(lat=c_lats, lon=c_lons, mode="lines",
                          line=dict(color=_CORRIDOR_OUTLINE_LIGHT, width=_CORRIDOR_OUTLINE_WIDTH),
                          name=trace_label,
                          legendgroup=gid,
                          legend="legend2",
                          visible="legendonly",
                          text=c_texts, **kw)
        )
        marker_traces.append(
            go.Scattermap(lat=m_lats, lon=m_lons, mode="markers",
                          marker=dict(symbol="triangle", size=13, angle=m_angles,
                                      color=_CORRIDOR_OUTLINE_LIGHT, allowoverlap=True),
                          legendgroup=gid,
                          showlegend=False,
                          visible="legendonly",
                          text=m_texts, **kw)
        )

    return line_traces, marker_traces


def _assemble_map(seg_traces, corridor_traces, termini_traces=None, ends_trace=None,
                  title="ITD District Screening", subtitle="", *,
                  center_lat=43.62, center_lon=-116.32, zoom=9.5,
                  seg_legend_title="TTI"):
    """Combine segment and corridor traces into a Plotly Figure.

    Layering order:
    1. Ranked corridor outlines FIRST (renders under segments as an outline casing)
    2. Segment TTI lines NEXT (renders on top of corridor outlines, clean segment legend)
    3. Corridor termini markers LAST (inward-pointing triangles on top of everything)
    """
    import plotly.graph_objects as go

    c_list = corridor_traces if isinstance(corridor_traces, list) else [corridor_traces]
    if ends_trace is not None:
        m_list = [termini_traces, ends_trace]
    elif termini_traces is not None:
        m_list = termini_traces if isinstance(termini_traces, list) else [termini_traces]
    else:
        m_list = []

    n_corridors = len(c_list)
    n_markers = len(m_list)
    n_segs = len(seg_traces)

    fig = go.Figure()
    # 1. Underlay: ranked corridor outlines (indices 0 .. n_corridors - 1)
    for ct in c_list:
        fig.add_trace(ct)
    # 2. Middle: state highway segments tiered by TTI (indices n_corridors .. n_corridors + n_segs - 1)
    for t in seg_traces:
        fig.add_trace(t)
    # 3. Overlay: corridor termini inward-pointing triangles (indices n_corridors + n_segs .. end)
    for mt in m_list:
        fig.add_trace(mt)

    c_indices = list(range(n_corridors))
    m_indices = list(range(n_corridors + n_segs, n_corridors + n_segs + n_markers))
    all_corridor_indices = c_indices + m_indices

    menus = [
        dict(
            type="buttons", direction="left", x=0.98, xanchor="right", y=0.97, showactive=True,
            buttons=[
                dict(args=[
                        {"line.color": _CORRIDOR_OUTLINE_LIGHT, "marker.color": _CORRIDOR_OUTLINE_LIGHT},
                        {"map.style": "carto-positron"},
                        all_corridor_indices
                     ],
                     label="Light (Clean)", method="update"),
                dict(args=[
                        {"line.color": _CORRIDOR_OUTLINE_LIGHT, "marker.color": _CORRIDOR_OUTLINE_LIGHT},
                        {"map.style": "open-street-map"},
                        all_corridor_indices
                     ],
                     label="Street Map", method="update"),
                dict(args=[
                        {"line.color": _CORRIDOR_OUTLINE_DARK, "marker.color": _CORRIDOR_OUTLINE_DARK},
                        {"map.style": "carto-darkmatter"},
                        all_corridor_indices
                     ],
                     label="Dark (High-Contrast)", method="update"),
            ],
            bgcolor="rgba(255,255,255,0.9)",
            bordercolor="#cbd5e0", font=dict(size=11, color="#2d3748")),
    ]
    if all_corridor_indices:
        menus.append(
            dict(
                type="buttons", direction="left", x=0.98, xanchor="right", y=0.91,
                active=1,  # Default is OFF
                showactive=True,
                buttons=[
                    dict(args=[{"visible": [True] * len(all_corridor_indices)}, all_corridor_indices],
                         label="All Outlines", method="restyle"),
                    dict(args=[{"visible": ["legendonly"] * len(all_corridor_indices)}, all_corridor_indices],
                         label="Hide Outlines", method="restyle"),
                ],
                bgcolor="rgba(255,255,255,0.9)",
                bordercolor="#cbd5e0", font=dict(size=11, color="#2d3748"))
        )

    fig.update_layout(
        title=dict(
            text=(f"<b>{title}</b><br>"
                  f"<span style='font-size:13px;color:#4a5568'>{subtitle}</span>"),
            x=0.03, y=0.97,
            font=dict(family=("-apple-system, BlinkMacSystemFont, Segoe UI, "
                              "Roboto, sans-serif"), size=18, color="#1a202c")),
        map=dict(style="carto-positron",
                 center=dict(lat=center_lat, lon=center_lon), zoom=zoom),
        margin=dict(l=0, r=0, t=75, b=0),
        legend=dict(
            x=0.02, y=0.03, bgcolor="rgba(255,255,255,0.92)",
            bordercolor="#cbd5e0", borderwidth=1,
            title=dict(text=f"<b>Segment Delay ({seg_legend_title})</b>",
                       font=dict(size=11, color="#1a202c")),
            font=dict(size=11, color="#2d3748"), itemsizing="constant"),
        legend2=dict(
            x=0.98, xanchor="right", y=0.03, yanchor="bottom",
            bgcolor="rgba(255,255,255,0.92)",
            bordercolor="#cbd5e0", borderwidth=1,
            font=dict(size=10, color="#2d3748"), itemsizing="constant",
            maxheight=360,
            title=dict(text="<b>Ranked Corridors (Outlines)</b>",
                       font=dict(size=11, color="#1a202c")),
            itemdoubleclick="toggle"),
        updatemenus=menus,
    )
    return fig


def _map_viewer_html(map_files: list[tuple[str, str]]) -> str:
    """Generate a tabbed viewer HTML page for switching between multiple maps.

    Args:
        map_files: list of (tab_label, filename) tuples.
    """
    if not map_files:
        return ""

    button_html = "\n".join([
        f'      <button class="tab-btn{" active" if i == 0 else ""}" onclick="switchMap({i})">{label}</button>'
        for i, (label, _) in enumerate(map_files)
    ])
    case_js = "\n".join([
        f'      if (idx === {i}) frame.src = "{fn}";'
        for i, (_, fn) in enumerate(map_files)
    ])

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>ITD District Screening GIS Visualizations</title>
  <style>
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; display: flex; flex-direction: column; height: 100vh; overflow: hidden; background: #1a202c; }}
    header {{ background: #2d3748; color: #fff; padding: 10px 20px; display: flex; align-items: center; justify-content: space-between; border-bottom: 2px solid #4a5568; z-index: 10; }}
    h1 {{ font-size: 16px; font-weight: 600; letter-spacing: 0.5px; color: #edf2f7; }}
    .nav-tabs {{ display: flex; gap: 8px; }}
    .tab-btn {{ background: #4a5568; color: #cbd5e0; border: none; padding: 8px 16px; border-radius: 6px; cursor: pointer; font-size: 13px; font-weight: 500; transition: all 0.2s ease; }}
    .tab-btn:hover {{ background: #718096; color: #fff; }}
    .tab-btn.active {{ background: #3182ce; color: #fff; box-shadow: 0 2px 4px rgba(0,0,0,0.2); }}
    .frame-container {{ flex: 1; position: relative; width: 100%; }}
    iframe {{ width: 100%; height: 100%; border: none; position: absolute; top: 0; left: 0; }}
  </style>
</head>
<body>
  <header>
    <h1>ITD District Screening GIS Visualizations</h1>
    <div class="nav-tabs">
{button_html}
    </div>
  </header>
  <div class="frame-container">
    <iframe id="map-frame" src="{map_files[0][1]}"></iframe>
  </div>
  <script>
    function switchMap(idx) {{
      const frame = document.getElementById('map-frame');
      const buttons = document.querySelectorAll('.tab-btn');
      buttons.forEach(b => b.classList.remove('active'));
      buttons[idx].classList.add('active');
{case_js}
    }}
  </script>
</body>
</html>
"""


def generate_maps(out_dir, scr, net, cat_entries, chains, corridor_ranks,
                  *, windows, window_label="Peak", title_prefix="ITD District",
                  delay_label="Total Peak Delay", map_filename="screening_map.html",
                  metric="tti", aadt=None):
    """Generate a standalone interactive HTML map of all segments and corridors.

    This is **wiring**, not core computation: it reads a segment screen, looks up
    network geometry, assembles Plotly Scattermap traces, and writes HTML. The import
    of ``plotly`` is deferred to here so the rest of the script remains importable
    without it.

    Args:
        out_dir: output directory (Path).
        scr: a :func:`screen.segment_screen` frame.
        net: the XD network GeoDataFrame (with ``XDSegID``).
        cat_entries: the catalogue entries (from ``corridors.load_catalogue``).
        chains: the chain results dict.
        corridor_ranks: dict from ``totals.set_index('corridor_group').to_dict('index')``.
        windows: the windows spec (passed to ``_segment_tti_frame``).
        window_label: display label for the window in tooltips.
        title_prefix: prefix for the map title.
        delay_label: label for the delay tooltip.
        map_filename: output filename for the map HTML.
        metric: ``'tti'`` (speed index) or ``'vhd_per_mile'`` (delay density).
        aadt: optional AADT joined frame or Series for volume weighting.

    Returns:
        Path to the written map HTML file.
    """
    out_dir = Path(out_dir)
    net_indexed = net.set_index("XDSegID")
    n_segs = len(scr)

    merged = _segment_tti_frame(scr, net_indexed, windows, aadt=aadt)
    c_traces, m_traces = _build_corridor_overlay(
        cat_entries, chains, corridor_ranks, net_indexed,
        delay_label=delay_label)
    n_groups = len(c_traces)

    if metric == "vhd_per_mile":
        seg_traces = _build_segment_vhd_traces(merged, window_label=window_label)
        map_title = f"{title_prefix} — {window_label} Delay Density (VHD / Mile)"
        map_subtitle = (f"All {n_segs:,} State Highway Segments colored by VHD/Mile | "
                        f"{n_groups} Ranked Corridors with Termini (▲)")
        seg_legend_title = "VHD / Mile"
    else:
        seg_traces = _build_segment_traces(merged, window_label=window_label)
        map_title = f"{title_prefix} — {window_label} Congestion Screening"
        map_subtitle = (f"All {n_segs:,} State Highway Segments colored by TTI | "
                        f"{n_groups} Ranked Corridors with Termini (▲)")
        seg_legend_title = "TTI"

    # Derive map center from network extent
    bounds = net_indexed["geometry"].dropna().total_bounds
    center_lat = (bounds[1] + bounds[3]) / 2
    center_lon = (bounds[0] + bounds[2]) / 2

    fig = _assemble_map(
        seg_traces, c_traces, m_traces,
        title=map_title,
        subtitle=map_subtitle,
        center_lat=center_lat, center_lon=center_lon,
        seg_legend_title=seg_legend_title)

    path = out_dir / map_filename
    fig.write_html(str(path), include_plotlyjs="cdn", full_html=True)
    return path


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

        if not args.catalogue:
            raise SystemExit("A catalogue JSON must be specified via --catalogue.")
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
        # Full network AADT join so both corridor ranking and segment-level delay-density map traces
        # have complete AADT coverage across the district.
        net_geo = net.copy()
        net_geo["Segment ID"] = net_geo["XDSegID"]
        aadt_cache = args.aadt_cache or (f"geometry_cache/d{args.district}_aadt.parquet" if args.district else None)
        aadt = join_volumes(net_geo, args.aadt, year=args.aadt_year,
                            cache_path=aadt_cache,
                            max_distance_m=args.aadt_max_distance_m,
                            bbox_margin=BBOX_MARGIN_DEG, shs=shs_source(args.shs))

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

        win_names = list((windows or screen.PEAK_WINDOWS).keys())
        is_7day = win_names == ["day_7d"]

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
                                write_kml=not args.no_kml, is_7day=is_7day)

        # Save segment screen results for fast statewide vector map aggregation
        scr_fname = "segment_7day_screen.parquet" if is_7day else "segment_peak_screen.parquet"
        scr_path = Path(args.out_dir) / scr_fname
        scr.to_parquet(scr_path)
        written["segment_screen"] = scr_path

        # Interactive HTML maps (--maps). Generated after the CSVs so the run
        # succeeds even if plotly is not installed — the maps are optional.
        if getattr(args, "maps", False) and totals is not None:
            cat_entries = corridors.load_catalogue(args.catalogue)
            corridor_ranks = totals.set_index(
                screen.GROUP_COL).to_dict(orient="index")

            # Build a human-readable window label from the windows that were run.
            dist_label = f"District {args.district}" if args.district else "District"
            if is_7day:
                window_label = "7-Day All-Day (6 AM – 9 PM)"
                delay_label = "Total 7-Day Delay"
                map_fname = "screening_7day_map.html"
            else:
                peak_names = [n for n, w in (windows or screen.PEAK_WINDOWS).items()
                              if w.peak]
                window_label = ("Typical Weekday Peak ("
                                + " / ".join(n.upper() for n in peak_names) + ")")
                delay_label = "Total Peak Delay"
                map_fname = "screening_peak_map.html"

            map_path = generate_maps(
                args.out_dir, scr, net, cat_entries, chains, corridor_ranks,
                windows=windows or screen.PEAK_WINDOWS,
                window_label=window_label,
                title_prefix=f"ITD {dist_label}",
                delay_label=delay_label,
                map_filename=map_fname)
            written["map"] = map_path

            if aadt is not None:
                vhd_fname = ("screening_7day_vhd_map.html" if is_7day
                             else "screening_vhd_map.html")
                vhd_path = generate_maps(
                    args.out_dir, scr, net, cat_entries, chains, corridor_ranks,
                    windows=windows or screen.PEAK_WINDOWS,
                    window_label=window_label,
                    title_prefix=f"ITD {dist_label}",
                    delay_label=delay_label,
                    map_filename=vhd_fname,
                    metric="vhd_per_mile",
                    aadt=aadt)
                written["map_vhd"] = vhd_path

            # Generate / update map_viewer.html if multiple maps exist in out_dir
            candidates = [
                ("Typical Peak (TTI)", "screening_peak_map.html"),
                ("Typical Peak (VHD / Mile)", "screening_vhd_map.html"),
                ("7-Day All-Day (TTI)", "screening_7day_map.html"),
                ("7-Day All-Day (VHD / Mile)", "screening_7day_vhd_map.html"),
            ]
            available_maps = [
                (lbl, fn) for lbl, fn in candidates
                if (Path(args.out_dir) / fn).exists()
            ]
            if len(available_maps) > 1:
                viewer_path = Path(args.out_dir) / "map_viewer.html"
                viewer_path.write_text(_map_viewer_html(available_maps), encoding="utf-8")
                written["map_viewer"] = viewer_path

        return {"ranking": ranking, "grouped": grouped, "totals": totals,
                "breakout": breakout, "resolution": resolution,
                "provenance": prov, "written": written, "screen": scr,
                "net": net}
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
    p.add_argument("--catalogue", default=None,
                   help="corridor catalogue JSON path (default: None; or auto-discovered if --district passed)")
    p.add_argument("--repairs", default=None,
                   help="link repairs CSV path (default: None; or auto-discovered if --district passed)")
    p.add_argument("--district", type=int, choices=[1, 2, 3, 4, 5, 6], default=None,
                   help="ITD district number (auto-sets timezone and default catalogue/repair paths)")
    p.add_argument("--no-repairs", action="store_true",
                   help="walk NextXDSegI exactly as published (Item 38 off)")
    p.add_argument("--network", default="USA_Idaho_shapefile.zip")
    p.add_argument("--network-cache", default=None)
    p.add_argument("--aadt", default=None, help="AADT source; omitted leaves vhd NaN")
    p.add_argument("--aadt-cache", default=None)
    p.add_argument("--aadt-year", type=int, default=aadt_mod.DEFAULT_YEAR)
    p.add_argument("--shs", default=DEFAULT_SHS,
                   help="ITD State Highway System, used to classify AADT records "
                        "(Item 52); '' falls back to the descriptions")
    p.add_argument("--aadt-max-distance-m", type=float, default=60.0)
    p.add_argument("--cvalue-threshold", type=float, default=DEFAULT_CVALUE,
                   help="keep CValue > threshold; 'none' disables the gate")
    p.add_argument("--windows", default=None,
                   help="comma-separated window preset names — any of: "
                        + ", ".join(sorted(screen.ALL_WINDOWS))
                        + " (default: am,pm,midday,night)")
    p.add_argument("--date-start", default=None)
    p.add_argument("--date-end", default=None)
    p.add_argument("--tz", default=DEFAULT_TZ)
    p.add_argument("--min-coverage", type=float, default=corridors.DEFAULT_MIN_COVERAGE)
    p.add_argument("--out-dir", default="out/district_screening")
    p.add_argument("--no-kml", action="store_true")
    p.add_argument("--maps", action="store_true",
                   help="generate standalone interactive HTML maps of all segments "
                        "and ranked corridors (opens in any browser, no GIS software)")
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
        args.windows = {n: screen.ALL_WINDOWS[n]
                        for n in (w.strip() for w in args.windows.split(","))
                        if n in screen.ALL_WINDOWS} or None

    if args.district is not None:
        DISTRICT_TZ = {
            1: "America/Los_Angeles",
            2: "America/Los_Angeles",
            3: "America/Boise",
            4: "America/Boise",
            5: "America/Boise",
            6: "America/Boise",
        }
        if args.tz == DEFAULT_TZ:
            args.tz = DISTRICT_TZ.get(args.district, DEFAULT_TZ)
        if args.catalogue is None:
            cand_cat = Path(f"scripts/d{args.district}_corridors.json")
            if cand_cat.exists():
                args.catalogue = str(cand_cat)
        if args.repairs is None and not args.no_repairs:
            cand_rep = Path(f"scripts/d{args.district}_link_repairs.csv")
            if cand_rep.exists():
                args.repairs = str(cand_rep)
    elif args.catalogue is None:
        # Fallback to D3 catalogue if present and no district specified
        d3_cat = Path("scripts/d3_corridors.json")
        if d3_cat.exists():
            args.catalogue = str(d3_cat)
        d3_rep = Path("scripts/d3_link_repairs.csv")
        if d3_rep.exists() and args.repairs is None and not args.no_repairs:
            args.repairs = str(d3_rep)

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

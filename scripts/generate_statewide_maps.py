#!/usr/bin/env python3
"""Generate statewide interactive HTML map visualizations across all ITD districts.

Builds four statewide master vector maps:
1. out/statewide_screening/statewide_peak_map.html:
   All segments colored by Typical Weekday Peak TTI (AM/PM), with statewide
   ranked corridors overlaid and start/end termini delineated.
2. out/statewide_screening/statewide_vhd_map.html:
   All segments colored by Typical Weekday Peak Delay Density (VHD / Mile).
3. out/statewide_screening/statewide_7day_map.html:
   All segments colored by 7-Day All-Day TTI (6 AM – 9 PM, 7 days/week).
4. out/statewide_screening/statewide_7day_vhd_map.html:
   All segments colored by 7-Day All-Day Delay Density (VHD / Mile).
5. out/statewide_screening/statewide_map_viewer.html:
   A tabbed browser interface toggling between all four statewide views.

The two VHD/mile maps need a per-segment AADT join (``--aadt``); without it they
are skipped rather than drawn with zeroed volumes.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import geopandas as gpd
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from inrix_tools import aadt as aadt_mod  # noqa: E402
from inrix_tools import corridors, screen  # noqa: E402
from scripts.run_district_screening import (  # noqa: E402
    BBOX_MARGIN_DEG,
    join_volumes,
    shs_source,
    _build_corridor_overlay,
    _build_segment_vhd_traces,
    _build_segment_traces,
    _map_viewer_html,
    _segment_tti_frame,
    _CORRIDOR_OUTLINE_LIGHT,
    _CORRIDOR_OUTLINE_DARK,
    _TERMINI_ZOOM_JS,
)


def load_statewide_data(districts: list[int], base_dir: Path, *,
                        aadt_source=None, aadt_year: int = aadt_mod.DEFAULT_YEAR,
                        catalogue_overrides: dict | None = None):
    """Load combined networks, AADT, catalogues, resolved chains, and screen frames.

    The AADT returned is the **spatially joined, per-segment** frame (indexed by
    ``Segment ID`` == ``XDSegID``), not the raw ITD route-measure layer. Those are
    different things: ``geometry_cache/d{N}_aadt.parquet`` caches the *layer*
    (``load_aadt(cache_path=...)``), whose index is a row number. Handing that to
    ``_segment_tti_frame`` matches nothing, which is how every statewide segment
    once rendered at 0 VHD/mi (Session 60). The join per district is cheap because
    the layer cache short-circuits the shapefile read.
    """
    net_parts = []
    aadt_parts = []
    all_cat_entries = []
    all_chains = {}

    scr_peak_parts = []
    scr_7d_parts = []

    for d in districts:
        net_cache = Path(f"geometry_cache/d{d}_network.geoparquet")
        if not net_cache.exists():
            print(f"Skipping District {d}: {net_cache} not found")
            continue
        net_d = gpd.read_parquet(net_cache)
        net_parts.append(net_d)

        if aadt_source is not None:
            net_geo = net_d.copy()
            net_geo["Segment ID"] = net_geo["XDSegID"]
            joined = join_volumes(
                net_geo, aadt_source, year=aadt_year,
                cache_path=f"geometry_cache/d{d}_aadt.parquet",
                max_distance_m=60.0, bbox_margin=BBOX_MARGIN_DEG, shs=shs_source())
            if joined is not None:
                aadt_parts.append(joined)

        cat_path = Path((catalogue_overrides or {}).get(d) or f"scripts/d{d}_corridors.json")
        repairs_path = Path(f"scripts/d{d}_link_repairs.csv")
        if cat_path.exists():
            entries = corridors.load_catalogue(cat_path)
            # Tier 2 / Tier 3 extents are context, not ranked corridors (ROADMAP
            # Item 50); outlining them on the ranked map would show them as unranked
            # peers with no delay. They are in statewide_*_context_extents.csv.
            context = {g["id"] for g in json.loads(cat_path.read_text()).get(
                "reporting_corridors", []) if g.get("_ranked") is False}
            entries = [e for e in entries if (e.corridor or e.id) not in context]
            all_cat_entries.extend(entries)
            repairs = corridors.load_link_repairs(repairs_path) if repairs_path.exists() else None
            res = corridors.resolve_catalogue(net_d, entries, repairs=repairs)
            for cid, ch in res.attrs["chains"].items():
                all_chains[cid] = ch

        # Screen parquet files
        p_peak = base_dir / f"d{d}" / "segment_peak_screen.parquet"
        if p_peak.exists():
            scr_peak_parts.append(pd.read_parquet(p_peak))

        p_7d = base_dir / f"d{d}" / "segment_7day_screen.parquet"
        if p_7d.exists():
            scr_7d_parts.append(pd.read_parquet(p_7d))

    if not net_parts:
        raise SystemExit("No district networks found to assemble statewide maps.")

    combined_net = gpd.GeoDataFrame(pd.concat(net_parts, ignore_index=True), crs=net_parts[0].crs)
    # Deduplicate segments if any overlap
    combined_net = combined_net.drop_duplicates(subset=["XDSegID"])

    # Each part is already indexed by Segment ID, so districts sharing a boundary
    # segment collapse on that index. Deduping on a positional index instead would
    # throw away every row whose row-number repeats across districts.
    combined_aadt = pd.concat(aadt_parts) if aadt_parts else None
    if combined_aadt is not None and not combined_aadt.empty:
        combined_aadt = combined_aadt[~combined_aadt.index.duplicated(keep="first")]

    combined_scr_peak = pd.concat(scr_peak_parts, ignore_index=False) if scr_peak_parts else None
    if combined_scr_peak is not None:
        combined_scr_peak = combined_scr_peak[~combined_scr_peak.index.duplicated(keep="first")]

    combined_scr_7d = pd.concat(scr_7d_parts, ignore_index=False) if scr_7d_parts else None
    if combined_scr_7d is not None:
        combined_scr_7d = combined_scr_7d[~combined_scr_7d.index.duplicated(keep="first")]

    return (
        combined_net,
        combined_aadt,
        all_cat_entries,
        all_chains,
        combined_scr_peak,
        combined_scr_7d,
    )


def generate_statewide_map(
    out_dir: Path,
    scr: pd.DataFrame,
    net: gpd.GeoDataFrame,
    cat_entries: list,
    chains: dict,
    corridor_ranks: dict,
    windows: dict,
    *,
    window_label: str,
    title: str,
    subtitle: str,
    delay_label: str,
    map_filename: str,
    metric: str = "tti",
    aadt=None,
    center_lat: float = 44.8,
    center_lon: float = -114.7,
    zoom: float = 6.2,
) -> Path:
    """Render and write a statewide interactive HTML vector map."""
    import plotly.graph_objects as go

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    map_path = out_dir / map_filename

    net_indexed = net.set_index("XDSegID")
    merged = _segment_tti_frame(scr, net_indexed, windows=windows, aadt=aadt)

    if metric == "vhd_per_mile":
        seg_traces = _build_segment_vhd_traces(merged, window_label=window_label)
        seg_legend_title = "VHD / Mile"
    else:
        seg_traces = _build_segment_traces(merged, window_label=window_label)
        seg_legend_title = "TTI Tier"

    n_segs = len(seg_traces)

    # Corridor outlines and termini markers
    line_list, m_list = _build_corridor_overlay(
        cat_entries, chains, corridor_ranks, net_indexed, delay_label=delay_label,
        zoom=zoom,
    )
    n_corridors = len(line_list)
    n_markers = len(m_list)

    fig = go.Figure()
    for lt in line_list:
        fig.add_trace(lt)
    for st in seg_traces:
        fig.add_trace(st)
    for mt in m_list:
        fig.add_trace(mt)

    c_indices = list(range(n_corridors))
    m_indices = list(range(n_corridors + n_segs, n_corridors + n_segs + n_markers))
    all_corridor_indices = c_indices + m_indices

    menus = [
        dict(
            type="buttons",
            direction="left",
            x=0.98,
            xanchor="right",
            y=0.97,
            showactive=True,
            buttons=[
                dict(
                    args=[
                        {"line.color": _CORRIDOR_OUTLINE_LIGHT, "fillcolor": _CORRIDOR_OUTLINE_LIGHT},
                        {"map.style": "carto-positron"},
                        all_corridor_indices,
                    ],
                    label="Light (Clean)",
                    method="update",
                ),
                dict(
                    args=[
                        {"line.color": _CORRIDOR_OUTLINE_LIGHT, "fillcolor": _CORRIDOR_OUTLINE_LIGHT},
                        {"map.style": "open-street-map"},
                        all_corridor_indices,
                    ],
                    label="Street Map",
                    method="update",
                ),
                dict(
                    args=[
                        {"line.color": _CORRIDOR_OUTLINE_DARK, "fillcolor": _CORRIDOR_OUTLINE_DARK},
                        {"map.style": "carto-darkmatter"},
                        all_corridor_indices,
                    ],
                    label="Dark (High-Contrast)",
                    method="update",
                ),
            ],
            bgcolor="rgba(255,255,255,0.9)",
            bordercolor="#cbd5e0",
            font=dict(size=11, color="#2d3748"),
        ),
    ]
    if all_corridor_indices:
        menus.append(
            dict(
                type="buttons",
                direction="left",
                x=0.98,
                xanchor="right",
                y=0.91,
                active=1,  # Default is OFF
                showactive=True,
                buttons=[
                    dict(
                        args=[{"visible": [True] * len(all_corridor_indices)}, all_corridor_indices],
                        label="All Outlines",
                        method="restyle",
                    ),
                    dict(
                        args=[{"visible": ["legendonly"] * len(all_corridor_indices)}, all_corridor_indices],
                        label="Hide Outlines",
                        method="restyle",
                    ),
                ],
                bgcolor="rgba(255,255,255,0.9)",
                bordercolor="#cbd5e0",
                font=dict(size=11, color="#2d3748"),
            )
        )

    fig.update_layout(
        title=dict(
            text=(f"<b>{title}</b><br><span style='font-size:13px;color:#4a5568'>{subtitle}</span>"),
            x=0.03,
            y=0.97,
            font=dict(
                family="-apple-system, BlinkMacSystemFont, Segoe UI, Roboto, sans-serif",
                size=18,
                color="#1a202c",
            ),
        ),
        map=dict(style="carto-positron", center=dict(lat=center_lat, lon=center_lon), zoom=zoom),
        margin=dict(l=0, r=0, t=75, b=0),
        legend=dict(
            x=0.02,
            y=0.03,
            bgcolor="rgba(255,255,255,0.92)",
            bordercolor="#cbd5e0",
            borderwidth=1,
            title=dict(text=f"<b>Segment Delay ({seg_legend_title})</b>", font=dict(size=11, color="#1a202c")),
            font=dict(size=11, color="#2d3748"),
            itemsizing="constant",
        ),
        legend2=dict(
            x=0.98,
            xanchor="right",
            y=0.03,
            yanchor="bottom",
            bgcolor="rgba(255,255,255,0.92)",
            bordercolor="#cbd5e0",
            borderwidth=1,
            font=dict(size=10, color="#2d3748"),
            itemsizing="constant",
            maxheight=360,
            title=dict(text="<b>Ranked Corridors (Outlines)</b>", font=dict(size=11, color="#1a202c")),
            itemdoubleclick="toggle",
        ),
        updatemenus=menus,
    )

    fig.write_html(
        str(map_path),
        include_plotlyjs=True,
        full_html=True,
        config={"responsive": True, "displayModeBar": True},
        post_script=_TERMINI_ZOOM_JS,
    )
    return map_path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dir", default="out/statewide_screening",
                        help="Base output directory")
    parser.add_argument("--districts", nargs="*", type=int, default=[1, 2, 3, 4, 5, 6])
    parser.add_argument("--aadt", default=aadt_mod.DEFAULT_SOURCE,
                        help="AADT source for the per-segment volume join; "
                             "omitted skips the two VHD/mile maps")
    parser.add_argument("--aadt-year", type=int, default=aadt_mod.DEFAULT_YEAR)
    parser.add_argument("--catalogue-override", action="append", default=[], metavar="D=PATH",
                        help="district D's catalogue is PATH (repeatable)")
    args = parser.parse_args()
    overrides = {int(v.partition("=")[0]): v.partition("=")[2] for v in args.catalogue_override}

    base_dir = Path(args.dir)
    t0 = time.time()

    print("Loading statewide network geometries, AADT, and screening frames...")
    (
        net,
        aadt,
        cat_entries,
        chains,
        scr_peak,
        scr_7d,
    ) = load_statewide_data(args.districts, base_dir,
                            aadt_source=args.aadt, aadt_year=args.aadt_year,
                            catalogue_overrides=overrides)
    if aadt is None or aadt.empty:
        print("  WARNING: no AADT joined — the VHD/mile maps will be skipped.")
    else:
        print(f"  Joined AADT for {aadt['AADT'].notna().sum():,} of {len(aadt):,} segments.")

    print(f"  Loaded {len(net):,} network segments and {len(cat_entries)} corridor entries across Idaho.")

    # Load statewide rankings
    p_rank_csv = base_dir / "statewide_peak_corridor_rankings.csv"
    d7_rank_csv = base_dir / "statewide_7day_corridor_rankings.csv"

    peak_ranks = {}
    if p_rank_csv.exists():
        df_p = pd.read_csv(p_rank_csv)
        df_p["rank"] = df_p["statewide_rank"]
        peak_ranks = df_p.set_index("corridor_group").to_dict(orient="index")

    d7_ranks = {}
    if d7_rank_csv.exists():
        df_7d = pd.read_csv(d7_rank_csv)
        df_7d["rank"] = df_7d["statewide_rank"]
        d7_ranks = df_7d.set_index("corridor_group").to_dict(orient="index")

    map_files = []

    # Map 1: Statewide Peak TTI
    if scr_peak is not None:
        print("Rendering Statewide Peak TTI Map...")
        p_path = generate_statewide_map(
            base_dir,
            scr_peak,
            net,
            cat_entries,
            chains,
            peak_ranks,
            windows=screen.PEAK_WINDOWS,
            window_label="Typical Weekday Peak (AM / PM)",
            title="ITD Statewide Corridor Screening — Weekday Peak Congestion (TTI)",
            subtitle="Statewide XD Network Colored by Peak Travel Time Index (AM/PM) with Ranked Corridors",
            delay_label="Total Peak Delay",
            map_filename="statewide_peak_map.html",
            metric="tti",
        )
        print(f"  -> Written {p_path}")
        map_files.append(("Statewide Peak (TTI)", p_path.name))

        # Map 2: Statewide Peak VHD / Mile
        if aadt is not None:
            print("Rendering Statewide Peak Delay Density (VHD / Mile) Map...")
            vhd_path = generate_statewide_map(
                base_dir,
                scr_peak,
                net,
                cat_entries,
                chains,
                peak_ranks,
                windows=screen.PEAK_WINDOWS,
                window_label="Typical Weekday Peak (AM / PM)",
                title="ITD Statewide Corridor Screening — Peak Delay Density (VHD / Mile)",
                subtitle="Volume-Weighted Vehicle-Hours of Delay per Mile Across All Monitored Highway Segments",
                delay_label="Total Peak Delay",
                map_filename="statewide_vhd_map.html",
                metric="vhd_per_mile",
                aadt=aadt,
            )
            print(f"  -> Written {vhd_path}")
            map_files.append(("Statewide Peak (VHD / Mile)", vhd_path.name))

    # Map 3: Statewide 7-Day TTI
    if scr_7d is not None:
        day7_windows = {"day_7d": screen.ALL_DAY_7D_WINDOW}
        print("Rendering Statewide 7-Day All-Day TTI Map...")
        d7_path = generate_statewide_map(
            base_dir,
            scr_7d,
            net,
            cat_entries,
            chains,
            d7_ranks,
            windows=day7_windows,
            window_label="7-Day All-Day (6 AM – 9 PM)",
            title="ITD Statewide Corridor Screening — 7-Day All-Day Congestion (TTI)",
            subtitle="Continuous 7-Day All-Day Congestion Profile (6:00 AM – 9:00 PM, 7 Days/Week)",
            delay_label="Total 7-Day Delay",
            map_filename="statewide_7day_map.html",
            metric="tti",
        )
        print(f"  -> Written {d7_path}")
        map_files.append(("Statewide 7-Day (TTI)", d7_path.name))

        # Map 4: Statewide 7-Day VHD / Mile
        if aadt is not None:
            print("Rendering Statewide 7-Day Delay Density (VHD / Mile) Map...")
            d7_vhd_path = generate_statewide_map(
                base_dir,
                scr_7d,
                net,
                cat_entries,
                chains,
                d7_ranks,
                windows=day7_windows,
                window_label="7-Day All-Day (6 AM – 9 PM)",
                title="ITD Statewide Corridor Screening — 7-Day Delay Density (VHD / Mile)",
                subtitle="Continuous 7-Day Volume-Weighted Delay Density (VHD / Mile) Across Idaho Highways",
                delay_label="Total 7-Day Delay",
                map_filename="statewide_7day_vhd_map.html",
                metric="vhd_per_mile",
                aadt=aadt,
            )
            print(f"  -> Written {d7_vhd_path}")
            map_files.append(("Statewide 7-Day (VHD / Mile)", d7_vhd_path.name))

    if map_files:
        viewer_path = base_dir / "statewide_map_viewer.html"
        viewer_path.write_text(_map_viewer_html(map_files), encoding="utf-8")
        print(f"  -> Written Statewide Tabbed Viewer {viewer_path}")

    print(f"\nStatewide maps generated in {time.time() - t0:.1f}s!")


if __name__ == "__main__":
    main()

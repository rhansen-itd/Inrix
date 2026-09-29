#!/usr/bin/env python3
"""Generate statewide interactive HTML map visualizations across all ITD districts.

Builds two statewide master vector maps **per scenario** (ROADMAP Item 67):

1. ``statewide_<tag>_map.html``: all segments coloured by the scenario's TTI, with the
   statewide ranked corridors overlaid and their termini delineated;
2. ``statewide_<tag>_vhd_map.html``: the same by delay density (VHD / mile);

plus ``statewide_map_viewer.html``, a tabbed viewer over all of them. The weekday peak
keeps its historic names (``statewide_peak_map.html``, ``statewide_vhd_map.html``), and
the 7-day window is ``7day``. The scenarios come from ``--scenarios``, else from the
districts' ``screening_scenarios.json`` (the same discovery the aggregate uses), else
the historic peak + 7-day pair.

The VHD/mile maps colour by the **curve-weighted** VHD each district run saved
beside its segment screen (``segment_<tag>_curve_vhd.parquet``, Item 58), which
carries its own AADT. A district without one (screened with no AADT) draws as
unvolumed; with none at all the VHD maps are skipped rather than drawn with zeroed
volumes. The maps no longer join AADT themselves, so they can never weight by a
different AADT source than the ranking did.
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

from inrix_tools import corridors  # noqa: E402
from scripts.aggregate_statewide_rankings import (  # noqa: E402
    load_district_table,
    scenario_tables,
)
from scripts.run_district_screening import (  # noqa: E402
    SEGMENT_VHD_PEAK,
    _build_corridor_overlay,
    _build_segment_vhd_traces,
    _build_segment_traces,
    _direction_menu,
    _legend_sidebar_layout,
    attach_direction_totals,
    _map_viewer_html,
    _segment_tti_frame,
    scenario_file,
    _CORRIDOR_OUTLINE_LIGHT,
    _CORRIDOR_OUTLINE_DARK,
    _TERMINI_ZOOM_JS,
)


def scenario_frames(tag: str | None) -> tuple[str, str]:
    """``(segment screen, segment curve VHD)`` file names a district run wrote for a
    scenario tag (``run_district_screening.scenario_file``); ``None``/``"peak"`` is the
    un-tagged peak run."""
    tag = None if tag in (None, "peak") else tag
    return (scenario_file("segment", tag, "_screen.parquet", "segment_peak_screen.parquet"),
            scenario_file("segment", tag, "_curve_vhd.parquet", SEGMENT_VHD_PEAK))


def load_statewide_data(districts: list[int], base_dir: Path, tags=("peak", "7day"), *,
                        catalogue_overrides: dict | None = None):
    """Load combined networks, catalogues, resolved chains, and per scenario tag the
    screen frames and the per-segment curve VHD each district run saved (Item 58).

    Returns ``(net, cat_entries, chains, frames)`` with ``frames[tag] = (scr, vhd)``;
    a frame no district wrote is ``None``. The VHD frames carry the ``AADT`` the district
    ranking was weighted by, so the map and the ranking cannot disagree about volume.
    """
    net_parts = []
    all_cat_entries = []
    all_chains = {}
    parts = {(tag, kind): [] for tag in tags for kind in ("scr", "vhd")}

    for d in districts:
        net_cache = Path(f"geometry_cache/d{d}_network.geoparquet")
        if not net_cache.exists():
            print(f"Skipping District {d}: {net_cache} not found")
            continue
        net_d = gpd.read_parquet(net_cache)
        net_parts.append(net_d)

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

        for tag in tags:
            scr_name, vhd_name = scenario_frames(tag)
            scr_path = base_dir / f"d{d}" / scr_name
            if scr_path.exists():
                parts[(tag, "scr")].append(pd.read_parquet(scr_path))
            vhd_path = base_dir / f"d{d}" / vhd_name
            if vhd_path.exists():
                parts[(tag, "vhd")].append(pd.read_parquet(vhd_path))
            elif scr_path.exists():
                print(f"  District {d}: no {vhd_name} (screened without AADT?) — its "
                      f"segments draw as unvolumed on the {tag} VHD map.")

    if not net_parts:
        raise SystemExit("No district networks found to assemble statewide maps.")

    combined_net = gpd.GeoDataFrame(pd.concat(net_parts, ignore_index=True), crs=net_parts[0].crs)
    # Deduplicate segments if any overlap
    combined_net = combined_net.drop_duplicates(subset=["XDSegID"])

    def _combine(key):
        if not parts[key]:
            return None
        if key[1] == "vhd":
            frame = pd.concat(parts[key], ignore_index=True)
            # A boundary segment screened by two districts keeps its first row.
            return frame.drop_duplicates(subset=["Segment ID", "window"], keep="first")
        frame = pd.concat(parts[key])
        return frame[~frame.index.duplicated(keep="first")]

    frames = {tag: (_combine((tag, "scr")), _combine((tag, "vhd"))) for tag in tags}
    return combined_net, all_cat_entries, all_chains, frames


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
    segment_vhd=None,
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
    merged = _segment_tti_frame(scr, net_indexed, windows=windows,
                                segment_vhd=segment_vhd)

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
                    label="Light",
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
                        label="All Corridors",
                        method="restyle",
                    ),
                    dict(
                        args=[{"visible": ["legendonly"] * len(all_corridor_indices)}, all_corridor_indices],
                        label="Hide Corridors",
                        method="restyle",
                    ),
                ],
                bgcolor="rgba(255,255,255,0.9)",
                bordercolor="#cbd5e0",
                font=dict(size=11, color="#2d3748"),
            )
        )
    dir_menu = _direction_menu(fig)
    if dir_menu is not None:
        menus.append(dir_menu)

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
        **_legend_sidebar_layout(seg_legend_title),
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


def statewide_map_names(tag: str) -> tuple[str, str]:
    """The (TTI, VHD/mile) map files for a scenario tag; the peak keeps its historic
    names."""
    if tag == "peak":
        return "statewide_peak_map.html", "statewide_vhd_map.html"
    return f"statewide_{tag}_map.html", f"statewide_{tag}_vhd_map.html"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dir", default="out/statewide_screening",
                        help="Base output directory")
    parser.add_argument("--districts", nargs="*", type=int, default=[1, 2, 3, 4, 5, 6])
    parser.add_argument("--scenarios", default=None,
                        help="comma-separated scenarios to map (default: every scenario "
                             "the districts' screening_scenarios.json lists)")
    parser.add_argument("--catalogue-override", action="append", default=[], metavar="D=PATH",
                        help="district D's catalogue is PATH (repeatable)")
    args = parser.parse_args()
    overrides = {int(v.partition("=")[0]): v.partition("=")[2] for v in args.catalogue_override}

    base_dir = Path(args.dir)
    t0 = time.time()
    scenarios = scenario_tables(base_dir, args.districts, args.scenarios)

    print("Loading statewide network geometries, screening frames and curve VHD...")
    net, cat_entries, chains, frames = load_statewide_data(
        args.districts, base_dir, [e["tag"] for e in scenarios],
        catalogue_overrides=overrides)
    print(f"  Loaded {len(net):,} network segments and {len(cat_entries)} corridor entries across Idaho.")

    def _breakouts(fname):
        parts = [load_district_table(base_dir / f"d{d}" / fname, d) for d in args.districts]
        parts = [p for p in parts if p is not None]
        return pd.concat(parts, ignore_index=True) if parts else None

    map_files = []
    for e in scenarios:
        tag, label = e["tag"], e["label"]
        scr, vhd = frames[tag]
        if scr is None:
            print(f"  No district saved a {tag} segment screen — its maps are skipped.")
            continue
        if vhd is None:
            print(f"  WARNING: no district saved a {tag} curve VHD — that VHD/mile map "
                  f"is skipped.")
        else:
            n = vhd.drop_duplicates("Segment ID")["AADT"].notna().sum()
            print(f"  {tag} curve VHD for {n:,} volumed segments.")

        ranks = {}
        rank_csv = base_dir / f"statewide_{tag}_corridor_rankings.csv"
        if rank_csv.exists():
            df = pd.read_csv(rank_csv)
            df["rank"] = df["statewide_rank"]
            ranks = df.set_index("corridor_group").to_dict(orient="index")
        # Per-direction figures for the corridor tooltips, from each district's breakout.
        attach_direction_totals(ranks, _breakouts(e["breakout"]))

        tti_name, vhd_name = statewide_map_names(tag)
        for metric, fname, kind in (("tti", tti_name, "TTI"),
                                    ("vhd_per_mile", vhd_name, "VHD / Mile")):
            if metric == "vhd_per_mile" and vhd is None:
                continue
            print(f"Rendering Statewide {label} ({kind}) Map...")
            path = generate_statewide_map(
                base_dir, scr, net, cat_entries, chains, ranks,
                windows=e["windows"],
                window_label=label,
                title=f"ITD Statewide Corridor Screening — {label} ({kind})",
                subtitle=("Statewide XD network coloured by "
                          + ("travel time index" if metric == "tti"
                             else "volume-weighted vehicle-hours of delay per mile")
                          + ", with the ranked corridors"),
                delay_label=f"Total delay, {label}",
                map_filename=fname,
                metric=metric,
                segment_vhd=vhd if metric == "vhd_per_mile" else None,
            )
            print(f"  -> Written {path}")
            map_files.append((f"{label} ({kind})", path.name))

    if map_files:
        viewer_path = base_dir / "statewide_map_viewer.html"
        viewer_path.write_text(_map_viewer_html(map_files), encoding="utf-8")
        print(f"  -> Written Statewide Tabbed Viewer {viewer_path}")

    print(f"\nStatewide maps generated in {time.time() - t0:.1f}s!")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Generate human-readable corridor ranking reports for ITD screening.

Produces clean, beautifully formatted HTML and Markdown reports for:
1. Statewide Corridor Screening (out/statewide_screening/statewide_corridor_rankings.html & .md)
2. District Corridor Screening (out/statewide_screening/d{1..6}/corridor_rankings.html & .md)

Usage:
    python scripts/generate_ranking_reports.py
    python scripts/generate_ranking_reports.py --base-dir out/statewide_screening
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.aggregate_statewide_rankings import load_district_table  # noqa: E402
from scripts.run_district_screening import _scenario_window_hours  # noqa: E402


def _district_table(path: Path) -> pd.DataFrame:
    """A district totals CSV, its provenance header skipped by counting lines (not
    ``comment="#"``, which truncates "Banks Lowman Rd #2"), with ``vhd_per_mi_hr``
    from each row's own windows."""
    df = load_district_table(path, 0) if path.exists() else None
    if df is None:
        return pd.DataFrame()
    if "vhd_per_mi_hr" not in df.columns and "windows" in df.columns:
        hours = df["windows"].map(_scenario_window_hours)
        df["vhd_per_mi_hr"] = df["vhd_per_mile"] / hours
    return df


def _fmt(val, fmt=",.1f", default="—"):
    if val is None or pd.isna(val):
        return default
    try:
        return format(float(val), fmt)
    except (ValueError, TypeError):
        return str(val)


def _badge(text: str) -> str:
    if not text:
        return ""
    t_lower = text.lower()
    if "commute" in t_lower:
        bg, color = "#ebf8ff", "#2b6cb0"
    elif "rec" in t_lower:
        bg, color = "#f0fff4", "#276749"
    elif "retail" in t_lower:
        bg, color = "#fefcbf", "#744210"
    else:
        bg, color = "#edf2f7", "#4a5568"
    return f'<span style="background:{bg};color:{color};padding:2px 8px;border-radius:12px;font-size:11px;font-weight:600;display:inline-block;margin:1px;">{text}</span>'


CSS_STYLES = """
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; background: #f7fafc; color: #2d3748; line-height: 1.5; padding: 24px; }
    .container { max-width: 1400px; margin: 0 auto; background: #fff; padding: 32px; border-radius: 8px; box-shadow: 0 4px 6px -1px rgba(0,0,0,0.1), 0 2px 4px -1px rgba(0,0,0,0.06); }
    header { border-bottom: 2px solid #e2e8f0; padding-bottom: 20px; margin-bottom: 24px; display: flex; justify-content: space-between; align-items: flex-end; flex-wrap: wrap; gap: 16px; }
    h1 { font-size: 24px; font-weight: 700; color: #1a202c; letter-spacing: -0.5px; }
    .subtitle { color: #718096; font-size: 14px; margin-top: 4px; }
    .nav-links a { color: #3182ce; text-decoration: none; font-size: 13px; font-weight: 600; margin-left: 12px; padding: 6px 12px; background: #ebf8ff; border-radius: 4px; }
    .nav-links a:hover { background: #bee3f8; }
    .summary-cards { display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 16px; margin-bottom: 28px; }
    .card { background: #f8fafc; border: 1px solid #e2e8f0; border-radius: 6px; padding: 16px; }
    .card-title { font-size: 12px; font-weight: 600; text-transform: uppercase; letter-spacing: 0.5px; color: #718096; }
    .card-val { font-size: 24px; font-weight: 700; color: #2b6cb0; margin-top: 4px; }
    .card-sub { font-size: 12px; color: #a0aec0; margin-top: 2px; }
    section { margin-bottom: 36px; }
    h2 { font-size: 18px; font-weight: 700; color: #2d3748; margin-bottom: 12px; padding-bottom: 6px; border-bottom: 1px solid #edf2f7; display: flex; align-items: center; justify-content: space-between; }
    .section-desc { font-size: 13px; color: #718096; margin-bottom: 14px; }
    table { width: 100%; border-collapse: collapse; font-size: 13px; margin-top: 8px; }
    th { background: #edf2f7; color: #4a5568; font-weight: 600; text-align: left; padding: 10px 12px; border-top: 1px solid #cbd5e0; border-bottom: 2px solid #cbd5e0; white-space: nowrap; }
    th.num, td.num { text-align: right; }
    td { padding: 9px 12px; border-bottom: 1px solid #e2e8f0; vertical-align: middle; }
    tr:nth-child(even) td { background: #fcfdfd; }
    tr:hover td { background: #f0f4f8; }
    .rank-cell { font-weight: 700; color: #2b6cb0; text-align: center; }
    .rate-highlight { font-weight: 700; color: #c53030; }
    .filter-box { display: flex; gap: 12px; margin-bottom: 12px; align-items: center; }
    .filter-box input { padding: 6px 12px; border: 1px solid #cbd5e0; border-radius: 4px; font-size: 13px; width: 300px; }
    footer { margin-top: 32px; padding-top: 16px; border-top: 1px solid #e2e8f0; font-size: 12px; color: #a0aec0; text-align: center; }
"""


def build_statewide_html(base_dir: Path) -> str:
    peak_path = base_dir / "statewide_peak_corridor_rankings.csv"
    matrix_path = base_dir / "statewide_scenario_matrix.csv"
    dist_sum_path = base_dir / "statewide_district_summary.csv"

    peak_df = pd.read_csv(peak_path) if peak_path.exists() else pd.DataFrame()
    matrix_df = pd.read_csv(matrix_path) if matrix_path.exists() else pd.DataFrame()
    dist_df = pd.read_csv(dist_sum_path) if dist_sum_path.exists() else pd.DataFrame()

    total_corridors = len(peak_df)
    total_miles = peak_df["miles"].sum() if not peak_df.empty else 0.0
    total_peak_vhd = peak_df["vhd"].sum() if not peak_df.empty else 0.0
    top_corridor = peak_df.iloc[0]["group_name"] if not peak_df.empty else "None"
    top_vhd_mi_hr = peak_df.iloc[0].get("vhd_per_mi_hr", 0.0) if not peak_df.empty else 0.0

    cards_html = f"""
    <div class="summary-cards">
      <div class="card">
        <div class="card-title">Ranked Corridors</div>
        <div class="card-val">{total_corridors}</div>
        <div class="card-sub">Tier 1 Congested Cores</div>
      </div>
      <div class="card">
        <div class="card-title">Monitored Centerline</div>
        <div class="card-val">{total_miles:,.1f} mi</div>
        <div class="card-sub">State Highway System</div>
      </div>
      <div class="card">
        <div class="card-title">Total Weekday Peak Delay</div>
        <div class="card-val">{total_peak_vhd:,.0f} veh-hrs</div>
        <div class="card-sub">AM (7–9) + PM (4–6:30) (4.5 hrs/day)</div>
      </div>
      <div class="card">
        <div class="card-title">#1 Congested Bottleneck</div>
        <div class="card-val" style="font-size:18px;line-height:1.2;margin-top:6px;">{_fmt(top_vhd_mi_hr, ',.1f')} <span style="font-size:13px;color:#718096;">VHD/mi/hr</span></div>
        <div class="card-sub" style="white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">{top_corridor}</div>
      </div>
    </div>
    """

    # 1. Top Corridors Table
    rows_peak = []
    for _, r in peak_df.iterrows():
        rank = int(r["statewide_rank"]) if pd.notna(r.get("statewide_rank")) else "—"
        dist = int(r["district"]) if pd.notna(r.get("district")) else "—"
        name = r.get("group_name", r.get("corridor_group", "—"))
        miles = _fmt(r.get("miles"), ",.2f")
        vhd_hr = _fmt(r.get("vhd_per_mi_hr"), ",.2f")
        vhd_mi = _fmt(r.get("vhd_per_mile"), ",.1f")
        vhd_tot = _fmt(r.get("vhd"), ",.0f")
        tti = _fmt(r.get("tti"), ".2f")
        ptype = _badge(str(r.get("primary_type", "")))

        rows_peak.append(f"""
        <tr>
          <td class="rank-cell">{rank}</td>
          <td style="text-align:center;">D{dist}</td>
          <td><b>{name}</b></td>
          <td>{ptype}</td>
          <td class="num">{miles}</td>
          <td class="num rate-highlight">{vhd_hr}</td>
          <td class="num">{vhd_mi}</td>
          <td class="num">{vhd_tot}</td>
          <td class="num">{tti}</td>
        </tr>
        """)

    table_peak = f"""
    <table>
      <thead>
        <tr>
          <th style="width:50px;">Rank</th>
          <th style="width:50px;">Dist</th>
          <th>Corridor Name & Congested Limits</th>
          <th style="width:100px;">Type</th>
          <th class="num" style="width:70px;">Miles</th>
          <th class="num" style="width:105px;">VHD/mi/hr</th>
          <th class="num" style="width:95px;">VHD/mi</th>
          <th class="num" style="width:95px;">Peak VHD</th>
          <th class="num" style="width:65px;">TTI</th>
        </tr>
      </thead>
      <tbody>
        {"".join(rows_peak)}
      </tbody>
    </table>
    """

    # 2. Cross Scenario Comparison Table
    rows_matrix = []
    for _, r in matrix_df.iterrows():
        name = r.get("group_name", r.get("corridor_group", "—"))
        dist = int(r["district"]) if pd.notna(r.get("district")) else "—"
        miles = _fmt(r.get("miles"), ",.2f")
        p_rank = int(r["peak_rank"]) if pd.notna(r.get("peak_rank")) else "—"
        p_hr = _fmt(r.get("peak_vhd_per_mi_hr"), ",.2f")
        d7_rank = int(r["7day_rank"]) if pd.notna(r.get("7day_rank")) else "—"
        d7_hr = _fmt(r.get("7day_vhd_per_mi_hr"), ",.2f")
        fri_hr = _fmt(r.get("fri_summer_vhd_per_mi_hr"), ",.2f")
        sat_hr = _fmt(r.get("sat_summer_vhd_per_mi_hr"), ",.2f")
        sun_hr = _fmt(r.get("sun_summer_vhd_per_mi_hr"), ",.2f")
        wk_hr = _fmt(r.get("weekend_summer_vhd_per_mi_hr"), ",.2f")

        rows_matrix.append(f"""
        <tr>
          <td class="rank-cell">{p_rank}</td>
          <td style="text-align:center;">D{dist}</td>
          <td><b>{name}</b></td>
          <td class="num">{miles}</td>
          <td class="num rate-highlight">{p_hr}</td>
          <td class="num">{d7_hr} <span style="font-size:11px;color:#a0aec0;">(#{d7_rank})</span></td>
          <td class="num">{fri_hr}</td>
          <td class="num">{sat_hr}</td>
          <td class="num">{sun_hr}</td>
          <td class="num">{wk_hr}</td>
        </tr>
        """)

    table_matrix = f"""
    <table>
      <thead>
        <tr>
          <th style="width:50px;">Peak #</th>
          <th style="width:50px;">Dist</th>
          <th>Corridor Name</th>
          <th class="num" style="width:65px;">Miles</th>
          <th class="num" style="width:100px;">Peak (hr)</th>
          <th class="num" style="width:110px;">7-Day (hr)</th>
          <th class="num" style="width:85px;">Fri Summer</th>
          <th class="num" style="width:85px;">Sat Summer</th>
          <th class="num" style="width:85px;">Sun Summer</th>
          <th class="num" style="width:95px;">Wknd Summer</th>
        </tr>
      </thead>
      <tbody>
        {"".join(rows_matrix)}
      </tbody>
    </table>
    """

    # 3. District Summary Roll-up
    rows_dist = []
    for _, r in dist_df.iterrows():
        d = int(r["district"])
        n_corr = int(r["monitored_reporting_corridors"])
        miles = _fmt(r["monitored_centerline_miles"], ",.1f")
        pk_vhd = _fmt(r["total_peak_vhd"], ",.0f")
        d7_vhd = _fmt(r["total_7day_vhd"], ",.0f")
        top_p = r["top_peak_corridor"]
        top_p_hr = _fmt(r.get("top_peak_vhd_per_mi_hr"), ",.1f")
        top_d7 = r["top_7day_corridor"]
        top_d7_hr = _fmt(r.get("top_7day_vhd_per_mi_hr"), ",.1f")

        rows_dist.append(f"""
        <tr>
          <td style="text-align:center;font-weight:700;">District {d}</td>
          <td class="num">{n_corr}</td>
          <td class="num">{miles}</td>
          <td class="num">{pk_vhd}</td>
          <td class="num">{d7_vhd}</td>
          <td><b>{top_p}</b> <span class="rate-highlight">({top_p_hr} VHD/mi/hr)</span></td>
          <td><b>{top_d7}</b> <span style="color:#2b6cb0;">({top_d7_hr} VHD/mi/hr)</span></td>
        </tr>
        """)

    table_dist = f"""
    <table>
      <thead>
        <tr>
          <th style="width:90px;">District</th>
          <th class="num" style="width:80px;">Corridors</th>
          <th class="num" style="width:80px;">Miles</th>
          <th class="num" style="width:110px;">Peak VHD</th>
          <th class="num" style="width:110px;">7-Day VHD</th>
          <th>Top Peak Corridor</th>
          <th>Top 7-Day Corridor</th>
        </tr>
      </thead>
      <tbody>
        {"".join(rows_dist)}
      </tbody>
    </table>
    """

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>ITD Statewide Corridor Screening — Rankings & Scenario Matrix</title>
  <style>{CSS_STYLES}</style>
</head>
<body>
  <div class="container">
    <header>
      <div>
        <h1>ITD Statewide Corridor Screening Report</h1>
        <div class="subtitle">Multi-Scenario Congestion Screening & Corridor Delay Density (VHD/mi/hr)</div>
      </div>
      <div class="nav-links">
        <a href="statewide_map_viewer.html" target="_blank">Open Statewide GIS Map Viewer ↗</a>
      </div>
    </header>

    {cards_html}

    <section>
      <h2>
        <span>1. Statewide Peak Corridor Rankings (Delay Density Basis)</span>
        <span style="font-size:12px;font-weight:normal;color:#718096;">Ranked on VHD/mi (normalized rate = VHD/mi/hr)</span>
      </h2>
      <div class="section-desc">
        All Tier 1 Congested Cores across Districts 1–6 totalled over weekday peaks (AM 7–9 + PM 4–6:30, 4.5 hours total duration).
        Delay density is presented both as normalized hourly rate (<b>VHD/mi/hr</b>) and traditional window total (<b>VHD/mi</b>).
      </div>
      {table_peak}
    </section>

    <section>
      <h2>
        <span>2. Cross-Scenario Comparison Matrix (VHD/mi/hr Normalized)</span>
        <span style="font-size:12px;font-weight:normal;color:#718096;">All rates normalized to vehicle-hours of delay per mile per hour</span>
      </h2>
      <div class="section-desc">
        Compares corridor delay density across operating regimes on the exact same basis. Note how commuter cores dominate Weekday Peaks, while recreational routes surge during Friday and Weekend summer windows.
      </div>
      {table_matrix}
    </section>

    <section>
      <h2>3. District Summary Roll-Up</h2>
      <div class="section-desc">
        Aggregate delay totals and top bottleneck facilities by ITD Administrative District.
      </div>
      {table_dist}
    </section>

    <footer>
      Idaho Transportation Department &bull; INRIX Statewide Corridor Screening &bull; Generated from authoritative roadway networks and curve-weighted volume profiles.
    </footer>
  </div>
</body>
</html>
"""


def build_statewide_markdown(base_dir: Path) -> str:
    peak_path = base_dir / "statewide_peak_corridor_rankings.csv"
    matrix_path = base_dir / "statewide_scenario_matrix.csv"
    dist_sum_path = base_dir / "statewide_district_summary.csv"

    peak_df = pd.read_csv(peak_path) if peak_path.exists() else pd.DataFrame()
    matrix_df = pd.read_csv(matrix_path) if matrix_path.exists() else pd.DataFrame()
    dist_df = pd.read_csv(dist_sum_path) if dist_sum_path.exists() else pd.DataFrame()

    lines = [
        "# ITD Statewide Corridor Screening — Ranking Report",
        "",
        "**Basis:** Delay density in vehicle-hours of delay per mile per hour (**VHD/mi/hr**) and total vehicle-hours of delay (**VHD**).",
        "",
        "## 1. District Summary",
        "",
        "| District | Corridors | Monitored Miles | Total Peak VHD | Total 7-Day VHD | Top Peak Corridor | Peak VHD/mi/hr | Top 7-Day Corridor | 7-Day VHD/mi/hr |",
        "|:---:|:---:|:---:|:---:|:---:|:---|:---:|:---|:---:|",
    ]

    for _, r in dist_df.iterrows():
        d = int(r["district"])
        n_corr = int(r["monitored_reporting_corridors"])
        miles = _fmt(r["monitored_centerline_miles"], ",.1f")
        p_vhd = _fmt(r["total_peak_vhd"], ",.0f")
        d7_vhd = _fmt(r["total_7day_vhd"], ",.0f")
        top_p = r["top_peak_corridor"]
        top_p_hr = _fmt(r.get("top_peak_vhd_per_mi_hr"), ",.1f")
        top_d7 = r["top_7day_corridor"]
        top_d7_hr = _fmt(r.get("top_7day_vhd_per_mi_hr"), ",.1f")
        lines.append(f"| D{d} | {n_corr} | {miles} | {p_vhd} | {d7_vhd} | {top_p} | **{top_p_hr}** | {top_d7} | **{top_d7_hr}** |")

    lines.extend([
        "",
        "## 2. Statewide Top 25 Peak Corridors",
        "",
        "| Rank | Dist | Corridor Name | Type | Length (mi) | VHD/mi/hr | VHD/mi | Peak VHD | TTI |",
        "|:---:|:---:|:---|:---:|:---:|:---:|:---:|:---:|:---:|",
    ])

    for _, r in peak_df.head(25).iterrows():
        rank = int(r["statewide_rank"]) if pd.notna(r.get("statewide_rank")) else "—"
        dist = int(r["district"]) if pd.notna(r.get("district")) else "—"
        name = r.get("group_name", r.get("corridor_group", "—"))
        ptype = str(r.get("primary_type", "—"))
        miles = _fmt(r.get("miles"), ",.2f")
        vhd_hr = _fmt(r.get("vhd_per_mi_hr"), ",.2f")
        vhd_mi = _fmt(r.get("vhd_per_mile"), ",.1f")
        vhd_tot = _fmt(r.get("vhd"), ",.0f")
        tti = _fmt(r.get("tti"), ".2f")
        lines.append(f"| **{rank}** | D{dist} | {name} | {ptype} | {miles} | **{vhd_hr}** | {vhd_mi} | {vhd_tot} | {tti} |")

    lines.extend([
        "",
        "## 3. Top Corridors Cross-Scenario Comparison (VHD/mi/hr)",
        "",
        "| Peak # | Dist | Corridor Name | Miles | Peak (hr) | 7-Day (hr) | Fri Summer (hr) | Sat Summer (hr) | Sun Summer (hr) | Weekend Summer (hr) |",
        "|:---:|:---:|:---|:---:|:---:|:---:|:---:|:---:|:---:|:---:|",
    ])

    for _, r in matrix_df.head(25).iterrows():
        p_rank = int(r["peak_rank"]) if pd.notna(r.get("peak_rank")) else "—"
        dist = int(r["district"]) if pd.notna(r.get("district")) else "—"
        name = r.get("group_name", r.get("corridor_group", "—"))
        miles = _fmt(r.get("miles"), ",.2f")
        p_hr = _fmt(r.get("peak_vhd_per_mi_hr"), ",.2f")
        d7_hr = _fmt(r.get("7day_vhd_per_mi_hr"), ",.2f")
        fri_hr = _fmt(r.get("fri_summer_vhd_per_mi_hr"), ",.2f")
        sat_hr = _fmt(r.get("sat_summer_vhd_per_mi_hr"), ",.2f")
        sun_hr = _fmt(r.get("sun_summer_vhd_per_mi_hr"), ",.2f")
        wk_hr = _fmt(r.get("weekend_summer_vhd_per_mi_hr"), ",.2f")
        lines.append(f"| **{p_rank}** | D{dist} | {name} | {miles} | **{p_hr}** | {d7_hr} | {fri_hr} | {sat_hr} | {sun_hr} | {wk_hr} |")

    return "\n".join(lines) + "\n"


def build_district_html(dist_dir: Path, district: int) -> str:
    peak_path = dist_dir / "corridor_peak_totals.csv"
    day7_path = dist_dir / "corridor_7day_totals.csv"

    peak_df = _district_table(peak_path)
    day7_df = _district_table(day7_path)

    total_corridors = len(peak_df)
    total_miles = peak_df["miles"].sum() if not peak_df.empty else 0.0
    total_peak_vhd = peak_df["vhd"].sum() if not peak_df.empty else 0.0

    cards_html = f"""
    <div class="summary-cards">
      <div class="card">
        <div class="card-title">Reporting Corridors</div>
        <div class="card-val">{total_corridors}</div>
        <div class="card-sub">District {district} Monitored Roads</div>
      </div>
      <div class="card">
        <div class="card-title">Monitored Centerline</div>
        <div class="card-val">{total_miles:,.1f} mi</div>
        <div class="card-sub">Directional Span: {peak_df['directional_miles'].sum():,.1f} mi</div>
      </div>
      <div class="card">
        <div class="card-title">Total Peak Delay</div>
        <div class="card-val">{total_peak_vhd:,.0f} veh-hrs</div>
        <div class="card-sub">Weekday AM + PM Peaks (4.5 hrs)</div>
      </div>
    </div>
    """

    # Peak Table
    rows_peak = []
    for _, r in peak_df.iterrows():
        rank = int(r["rank"]) if pd.notna(r.get("rank")) else "—"
        name = r.get("group_name", r.get("corridor_group", "—"))
        miles = _fmt(r.get("directional_miles"), ",.2f")
        vhd_hr = _fmt(r.get("vhd_per_mi_hr"), ",.2f")
        vhd_mi = _fmt(r.get("vhd_per_mile"), ",.1f")
        vhd_tot = _fmt(r.get("vhd"), ",.0f")
        tti = _fmt(r.get("tti"), ".2f")
        delay_min = _fmt(r.get("delay_min"), ",.1f")
        ptype = _badge(str(r.get("primary_type", "")))

        rows_peak.append(f"""
        <tr>
          <td class="rank-cell">{rank}</td>
          <td><b>{name}</b></td>
          <td>{ptype}</td>
          <td class="num">{miles}</td>
          <td class="num rate-highlight">{vhd_hr}</td>
          <td class="num">{vhd_mi}</td>
          <td class="num">{vhd_tot}</td>
          <td class="num">{delay_min}</td>
          <td class="num">{tti}</td>
        </tr>
        """)

    table_peak = f"""
    <table>
      <thead>
        <tr>
          <th style="width:50px;">Rank</th>
          <th>Reporting Corridor</th>
          <th style="width:100px;">Type</th>
          <th class="num" style="width:80px;">Dir Miles</th>
          <th class="num" style="width:110px;">VHD/mi/hr</th>
          <th class="num" style="width:100px;">VHD/mi</th>
          <th class="num" style="width:100px;">Veh-Hrs</th>
          <th class="num" style="width:90px;">Delay (min)</th>
          <th class="num" style="width:70px;">TTI</th>
        </tr>
      </thead>
      <tbody>
        {"".join(rows_peak)}
      </tbody>
    </table>
    """

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>ITD District {district} Corridor Screening — Ranking Report</title>
  <style>{CSS_STYLES}</style>
</head>
<body>
  <div class="container">
    <header>
      <div>
        <h1>ITD District {district} Corridor Screening Report</h1>
        <div class="subtitle">Reporting Corridor Delay Density & Ranking (VHD/mi/hr)</div>
      </div>
      <div class="nav-links">
        <a href="map_viewer.html" target="_blank">Open District {district} Map Viewer ↗</a>
      </div>
    </header>

    {cards_html}

    <section>
      <h2>District {district} Corridor Peak Rankings (Delay Density Basis)</h2>
      <div class="section-desc">
        Corridors totalled over weekday peaks (AM 7–9 + PM 4–6:30, 4.5 hours total duration) across both directions.
      </div>
      {table_peak}
    </section>

    <footer>
      Idaho Transportation Department &bull; District {district} Corridor Screening &bull; INRIX Analytics
    </footer>
  </div>
</body>
</html>
"""


def build_district_markdown(dist_dir: Path, district: int) -> str:
    peak_path = dist_dir / "corridor_peak_totals.csv"
    peak_df = _district_table(peak_path)

    lines = [
        f"# ITD District {district} Corridor Screening — Ranking Report",
        "",
        f"**Monitored Centerline Miles:** {peak_df['miles'].sum():,.1f} mi | **Total Peak Delay:** {peak_df['vhd'].sum():,.0f} veh-hrs",
        "",
        "## Corridor Peak Rankings",
        "",
        "| Rank | Corridor Name | Type | Directional Miles | VHD/mi/hr | VHD/mi | Peak Delay (veh-hrs) | TTI |",
        "|:---:|:---|:---:|:---:|:---:|:---:|:---:|:---:|",
    ]

    for _, r in peak_df.iterrows():
        rank = int(r["rank"]) if pd.notna(r.get("rank")) else "—"
        name = r.get("group_name", r.get("corridor_group", "—"))
        ptype = str(r.get("primary_type", "—"))
        miles = _fmt(r.get("directional_miles"), ",.2f")
        vhd_hr = _fmt(r.get("vhd_per_mi_hr"), ",.2f")
        vhd_mi = _fmt(r.get("vhd_per_mile"), ",.1f")
        vhd_tot = _fmt(r.get("vhd"), ",.0f")
        tti = _fmt(r.get("tti"), ".2f")
        lines.append(f"| **{rank}** | {name} | {ptype} | {miles} | **{vhd_hr}** | {vhd_mi} | {vhd_tot} | {tti} |")

    return "\n".join(lines) + "\n"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-dir", default="out/statewide_screening",
                        help="Base directory containing d{1..6} and statewide tables")
    args = parser.parse_args()

    base_dir = Path(args.base_dir)

    print("Generating Statewide Human-Readable Reports...")
    statewide_html = build_statewide_html(base_dir)
    sw_html_path = base_dir / "statewide_corridor_rankings.html"
    sw_html_path.write_text(statewide_html, encoding="utf-8")
    print(f"  -> Written {sw_html_path}")

    statewide_md = build_statewide_markdown(base_dir)
    sw_md_path = base_dir / "statewide_corridor_rankings.md"
    sw_md_path.write_text(statewide_md, encoding="utf-8")
    print(f"  -> Written {sw_md_path}")

    for d in range(1, 7):
        dist_dir = base_dir / f"d{d}"
        if not dist_dir.exists():
            continue
        print(f"Generating District {d} Reports...")
        d_html = build_district_html(dist_dir, d)
        d_html_path = dist_dir / "corridor_rankings.html"
        d_html_path.write_text(d_html, encoding="utf-8")
        print(f"  -> Written {d_html_path}")

        d_md = build_district_markdown(dist_dir, d)
        d_md_path = dist_dir / "corridor_rankings.md"
        d_md_path.write_text(d_md, encoding="utf-8")
        print(f"  -> Written {d_md_path}")

    print("\nAll ranking reports generated successfully!")


if __name__ == "__main__":
    main()

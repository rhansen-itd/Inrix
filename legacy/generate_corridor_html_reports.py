#!/usr/bin/env python3
"""Retired INRIX-vs-Google HTML report generator (superseded by ROADMAP Item 30).

Kept unchanged except for this note, per CLAUDE.md's "Style": superseded code goes
to ``legacy/`` rather than being deleted. It is **not** imported by anything — it
has import-time side effects (it creates its output directory at module level) and
a hardcoded ``BASE_DIR = "/home/hansrkid/Inrix"``.

Why it was retired (DESIGN_HISTORY Session 33, G10 — and Session 36, which
replaced it):

- It is the ``process_and_plot_*`` fusion this project exists to undo: MAE, bias
  and ``np.polyfit`` regression slopes are computed inside the figure builders
  (``build_scatter_with_annotations``), so no number it printed was testable or
  reusable, and the figures could not be rebuilt without recomputing statistics.
- It read two CSVs produced by an analysis script that never entered the repo.
- Its framing was wrong in ways the data contradicted: one study-wide "Study
  Period" banner over fifteen sheets with different coverage, summed matched
  observations across overlapping sub-routes of the same pavement, correlation as
  the headline beside multi-minute biases, and captions naming a route number
  ("SH-17") that does not exist in Idaho.

The replacement is ``scripts/build_validation_report.py`` (wiring) over
``gui/validation_report.py`` + ``gui/validation_figures.py`` (presentation) over
the ``inrix_tools`` compute core, which owns every statistic.

Original docstring follows.
"""

"""
Generate comprehensive, interactive HTML reports for corridor-by-corridor
validation of INRIX XD vs. Google Maps travel times.

Outputs:
  - out/reports/index.html (Master Executive Portal)
  - out/reports/sh55_mountain_comparison.html
  - out/reports/eagle_rd_vsl_comparison.html
  - out/reports/eagle_rd_full_comparison.html
  - out/reports/sh69_meridian_comparison.html
  - out/reports/franklin_rd_comparison.html
  - out/reports/garden_valley_comparison.html
"""

import os
import json
import pandas as pd
import numpy as np
import plotly.graph_objects as go
from plotly.subplots import make_subplots

BASE_DIR = "/home/hansrkid/Inrix"
DATA_CSV = os.path.join(BASE_DIR, "out/inrix_vs_google_matched_timeseries.csv")
SUMMARY_CSV = os.path.join(BASE_DIR, "out/inrix_vs_google_comparison_summary.csv")
MAPPING_JSON = os.path.join(BASE_DIR, "out/query_segment_mapping.json")
OUTPUT_DIR = os.path.join(BASE_DIR, "out/reports")

os.makedirs(OUTPUT_DIR, exist_ok=True)

# Shared Navigation Bar & CSS Template
COMMON_NAV_LINKS = [
    ("index.html", "Executive Portal"),
    ("sh55_mountain_comparison.html", "SH-55 Mountain"),
    ("eagle_rd_vsl_comparison.html", "Eagle Rd VSL"),
    ("eagle_rd_full_comparison.html", "Eagle Rd Full"),
    ("sh69_meridian_comparison.html", "SH-69 Meridian"),
    ("franklin_rd_comparison.html", "Franklin Rd"),
    ("garden_valley_comparison.html", "Garden Valley"),
]

CSS_STYLE = """
<style>
  :root {
    --primary: #1e3a8a;
    --primary-light: #3b82f6;
    --primary-dark: #172554;
    --bg-body: #f8fafc;
    --card-bg: #ffffff;
    --text-main: #0f172a;
    --text-muted: #64748b;
    --border-color: #e2e8f0;
    --success: #10b981;
    --warning: #f59e0b;
    --danger: #ef4444;
    --info: #0284c7;
  }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body {
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
    background-color: var(--bg-body);
    color: var(--text-main);
    line-height: 1.6;
    padding-bottom: 60px;
  }
  .navbar {
    background: linear-gradient(135deg, #1e3a8a 0%, #172554 100%);
    color: white;
    padding: 14px 24px;
    position: sticky;
    top: 0;
    z-index: 1000;
    box-shadow: 0 4px 12px rgba(0,0,0,0.15);
    display: flex;
    justify-content: space-between;
    align-items: center;
    flex-wrap: wrap;
    gap: 12px;
  }
  .navbar-brand {
    font-size: 1.25rem;
    font-weight: 700;
    letter-spacing: -0.02em;
    display: flex;
    align-items: center;
    gap: 10px;
  }
  .navbar-badge {
    background: #3b82f6;
    font-size: 0.75rem;
    padding: 3px 8px;
    border-radius: 6px;
    text-transform: uppercase;
    font-weight: 700;
  }
  .nav-links {
    display: flex;
    gap: 8px;
    flex-wrap: wrap;
  }
  .nav-links a {
    color: #e2e8f0;
    text-decoration: none;
    font-size: 0.875rem;
    padding: 6px 12px;
    border-radius: 6px;
    transition: all 0.2s ease;
  }
  .nav-links a:hover, .nav-links a.active {
    background-color: rgba(255,255,255,0.18);
    color: #ffffff;
    font-weight: 600;
  }
  .container {
    max-width: 1400px;
    margin: 24px auto;
    padding: 0 20px;
  }
  .hero-header {
    background: white;
    padding: 26px 32px;
    border-radius: 12px;
    border: 1px solid var(--border-color);
    box-shadow: 0 2px 4px rgba(0,0,0,0.04);
    margin-bottom: 24px;
  }
  .hero-title {
    font-size: 1.85rem;
    font-weight: 800;
    color: var(--primary-dark);
    margin-bottom: 8px;
    display: flex;
    align-items: center;
    gap: 12px;
    flex-wrap: wrap;
  }
  .hero-subtitle {
    color: var(--text-muted);
    font-size: 1rem;
    max-width: 1100px;
  }
  .badges-row {
    display: flex;
    gap: 10px;
    margin-top: 14px;
    flex-wrap: wrap;
  }
  .tag-badge {
    padding: 5px 12px;
    border-radius: 20px;
    font-size: 0.82rem;
    font-weight: 600;
    display: inline-flex;
    align-items: center;
    gap: 6px;
  }
  .tag-blue { background: #dbeafe; color: #1e40af; border: 1px solid #bfdbfe; }
  .tag-green { background: #dcfce7; color: #15803d; border: 1px solid #bbf7d0; }
  .tag-amber { background: #fef3c7; color: #b45309; border: 1px solid #fde68a; }
  .tag-purple { background: #f3e8ff; color: #6b21a8; border: 1px solid #e9d5ff; }
  .tag-red { background: #fee2e2; color: #b91c1c; border: 1px solid #fecaca; }

  .kpi-grid {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
    gap: 16px;
    margin-bottom: 28px;
  }
  .kpi-card {
    background: white;
    padding: 18px 20px;
    border-radius: 10px;
    border: 1px solid var(--border-color);
    box-shadow: 0 1px 3px rgba(0,0,0,0.05);
    border-top: 4px solid var(--primary);
    transition: transform 0.15s ease;
  }
  .kpi-card:hover { transform: translateY(-2px); }
  .kpi-title {
    font-size: 0.78rem;
    font-weight: 600;
    text-transform: uppercase;
    color: var(--text-muted);
    letter-spacing: 0.05em;
  }
  .kpi-val {
    font-size: 1.65rem;
    font-weight: 800;
    color: var(--text-main);
    margin: 6px 0 2px;
  }
  .kpi-sub {
    font-size: 0.82rem;
    color: var(--text-muted);
  }

  .chart-card {
    background: white;
    border-radius: 12px;
    border: 1px solid var(--border-color);
    box-shadow: 0 2px 6px rgba(0,0,0,0.04);
    margin-bottom: 28px;
    overflow: hidden;
  }
  .chart-header {
    padding: 18px 24px;
    border-bottom: 1px solid var(--border-color);
    background: #fdfdfd;
    display: flex;
    justify-content: space-between;
    align-items: center;
    flex-wrap: wrap;
    gap: 10px;
  }
  .chart-title {
    font-size: 1.15rem;
    font-weight: 700;
    color: var(--primary-dark);
  }
  .chart-desc {
    font-size: 0.875rem;
    color: var(--text-muted);
  }
  .chart-body {
    padding: 18px;
  }

  .writeup-section {
    background: white;
    border-radius: 12px;
    border: 1px solid var(--border-color);
    box-shadow: 0 2px 6px rgba(0,0,0,0.04);
    padding: 32px;
    margin-bottom: 28px;
  }
  .writeup-section h2 {
    font-size: 1.45rem;
    font-weight: 800;
    color: var(--primary-dark);
    margin-bottom: 16px;
    padding-bottom: 8px;
    border-bottom: 2px solid #e2e8f0;
  }
  .writeup-section h3 {
    font-size: 1.15rem;
    font-weight: 700;
    color: #1e293b;
    margin: 24px 0 10px;
  }
  .writeup-section p {
    font-size: 0.96rem;
    line-height: 1.7;
    color: #334155;
    margin-bottom: 14px;
  }
  .writeup-section ul, .writeup-section ol {
    margin-left: 24px;
    margin-bottom: 16px;
  }
  .writeup-section li {
    font-size: 0.95rem;
    color: #334155;
    margin-bottom: 6px;
    line-height: 1.6;
  }

  .alert-box {
    padding: 16px 20px;
    border-radius: 8px;
    margin: 20px 0;
    font-size: 0.92rem;
    line-height: 1.6;
    border-left: 5px solid;
  }
  .alert-info { background: #f0f9ff; border-left-color: #0284c7; color: #0369a1; }
  .alert-warning { background: #fffbeb; border-left-color: #f59e0b; color: #92400e; }
  .alert-success { background: #f0fdf4; border-left-color: #10b981; color: #166534; }
  .alert-danger { background: #fef2f2; border-left-color: #ef4444; color: #991b1b; }
  .alert-title { font-weight: 700; margin-bottom: 4px; display: block; }

  .data-table-wrapper {
    overflow-x: auto;
    margin: 20px 0;
    border-radius: 8px;
    border: 1px solid var(--border-color);
  }
  table.data-table {
    width: 100%;
    border-collapse: collapse;
    font-size: 0.9rem;
    text-align: left;
  }
  table.data-table th {
    background: #f1f5f9;
    padding: 12px 16px;
    font-weight: 700;
    color: #334155;
    border-bottom: 1px solid var(--border-color);
  }
  table.data-table td {
    padding: 10px 16px;
    border-bottom: 1px solid #f1f5f9;
    color: #1e293b;
  }
  table.data-table tr:hover { background-color: #f8fafc; }

  .two-col-grid {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(480px, 1fr));
    gap: 20px;
  }

  footer {
    text-align: center;
    color: var(--text-muted);
    font-size: 0.85rem;
    margin-top: 40px;
    padding-top: 20px;
    border-top: 1px solid var(--border-color);
  }
</style>
"""

def get_navbar(active_file):
    links_html = "".join(
        f'<a href="{url}" class="{"active" if url == active_file else ""}">{label}</a>'
        for url, label in COMMON_NAV_LINKS
    )
    return f"""
    <nav class="navbar">
      <div class="navbar-brand">
        <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polygon points="3 11 22 2 13 21 11 13 3 11"/></svg>
        <span>ITD District 3 Traffic Analytics</span>
        <span class="navbar-badge">Validation Engine</span>
      </div>
      <div class="nav-links">
        {links_html}
      </div>
    </nav>
    """

def wrap_page(title, active_file, content):
    navbar = get_navbar(active_file)
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{title} | ITD D3 Validation</title>
  <script src="https://cdn.plot.ly/plotly-2.35.2.min.js"></script>
  {CSS_STYLE}
</head>
<body>
  {navbar}
  <div class="container">
    {content}
    <footer>
      Idaho Transportation Department (ITD) District 3 • Traffic Operations & Safety • INRIX XD vs. Google Maps Validation Study (2026 Dataset)
    </footer>
  </div>
</body>
</html>
"""

# -------------------------------------------------------------
# Plotting Helper Functions
# -------------------------------------------------------------

DAY_ORDER = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']

def build_heatmaps_for_sheet(df_sheet, sheet_title, height=360):
    """
    Builds a 3-column heatmap:
    Col 1: Google Mean TT
    Col 2: INRIX Mean TT
    Col 3: Delta (INRIX - Google)
    """
    p_g = df_sheet.pivot_table(index='dow', columns='hour', values='google_tt_min', aggfunc='mean')
    p_i = df_sheet.pivot_table(index='dow', columns='hour', values='inrix_tt_min', aggfunc='mean')
    p_d = df_sheet.pivot_table(index='dow', columns='hour', values='diff', aggfunc='mean')

    # Reindex days
    existing_days = [d for d in DAY_ORDER if d in p_g.index]
    p_g = p_g.reindex(existing_days)
    p_i = p_i.reindex(existing_days)
    p_d = p_d.reindex(existing_days)

    all_tt = np.concatenate([p_g.values.flatten(), p_i.values.flatten()])
    all_tt = all_tt[~np.isnan(all_tt)]
    zmin_tt = float(np.percentile(all_tt, 2)) if len(all_tt) else 0
    zmax_tt = float(np.percentile(all_tt, 98)) if len(all_tt) else 10

    max_abs_d = float(np.nanmax(np.abs(p_d.values))) if len(p_d.values) else 1
    max_abs_d = max(max_abs_d, 0.5)

    hours = [f"{int(h):02d}:00" for h in p_g.columns]

    fig = make_subplots(
        rows=1, cols=3,
        subplot_titles=[
            f"Google Maps Mean TT (min)",
            f"INRIX Mean TT (min)",
            f"Delta: INRIX - Google (min)"
        ],
        horizontal_spacing=0.08
    )

    # Col 1: Google TT
    fig.add_trace(go.Heatmap(
        z=p_g.values,
        x=hours,
        y=existing_days,
        colorscale="RdYlGn_r",
        zmin=zmin_tt,
        zmax=zmax_tt,
        colorbar=dict(title="TT (min)", x=0.28, len=0.9, thickness=14),
        hovertemplate="Day: %{y}<br>Hour: %{x}<br>Google TT: %{z:.2f} min<extra></extra>"
    ), row=1, col=1)

    # Col 2: INRIX TT
    fig.add_trace(go.Heatmap(
        z=p_i.values,
        x=hours,
        y=existing_days,
        colorscale="RdYlGn_r",
        zmin=zmin_tt,
        zmax=zmax_tt,
        colorbar=dict(title="TT (min)", x=0.63, len=0.9, thickness=14),
        hovertemplate="Day: %{y}<br>Hour: %{x}<br>INRIX TT: %{z:.2f} min<extra></extra>"
    ), row=1, col=2)

    # Col 3: Difference
    fig.add_trace(go.Heatmap(
        z=p_d.values,
        x=hours,
        y=existing_days,
        colorscale="RdBu_r",
        zmin=-max_abs_d,
        zmax=max_abs_d,
        colorbar=dict(title="Delta (m)", x=1.0, len=0.9, thickness=14),
        hovertemplate="Day: %{y}<br>Hour: %{x}<br>Delta: %{z:+.2f} min<extra></extra>"
    ), row=1, col=3)

    fig.update_layout(
        height=height,
        margin=dict(l=70, r=40, t=50, b=50),
        template="plotly_white",
        font=dict(family="-apple-system, BlinkMacSystemFont, Segoe UI, Roboto, sans-serif", size=11),
    )
    for col in [1, 2, 3]:
        fig.update_xaxes(title_text="Hour of Day", row=1, col=col)
    fig.update_yaxes(title_text="Day of Week", row=1, col=1)

    return fig.to_html(full_html=False, include_plotlyjs=False, config=dict(responsive=True, displayModeBar=True))


def build_scatter_with_annotations(panels, overall_title="", height=500):
    """
    panels is a list of dicts:
    {
      'title': 'Cascade-HSB (Southbound)',
      'df': sub_df,
      'color_col': 'dow' or 'hour',
      'annotations': [{'x': val, 'y': val, 'text': '...', 'ax': -40, 'ay': -40}]
    }
    """
    n_cols = len(panels)
    fig = make_subplots(
        rows=1, cols=n_cols,
        subplot_titles=[p['title'] for p in panels],
        horizontal_spacing=0.1
    )

    for i, p in enumerate(panels, 1):
        sub = p['df']
        corr = sub['google_tt_min'].corr(sub['inrix_tt_min'])
        r2 = corr ** 2 if not np.isnan(corr) else 0
        mae = sub['diff'].abs().mean()
        bias = sub['diff'].mean()

        # Points
        color_data = sub['dow'] if 'color_col' in p and p['color_col'] == 'dow' else sub['hour']
        color_title = "Day" if 'color_col' in p and p['color_col'] == 'dow' else "Hour"

        fig.add_trace(go.Scatter(
            x=sub['google_tt_min'],
            y=sub['inrix_tt_min'],
            mode='markers',
            marker=dict(
                size=5.5,
                color=sub['hour'],
                colorscale='Viridis',
                opacity=0.6,
                showscale=(i == n_cols),
                colorbar=dict(title="Hour", len=0.8, thickness=12) if i == n_cols else None
            ),
            text=[f"Date: {dt}<br>Day: {dow}<br>Hour: {h:02d}:00" for dt, dow, h in zip(sub['dt_bin'], sub['dow'], sub['hour'])],
            hovertemplate="<b>%{text}</b><br>Google: %{x:.2f} min<br>INRIX: %{y:.2f} min<br>Diff: %{customdata:+.2f} min<extra></extra>",
            customdata=sub['diff'],
            name=p['title']
        ), row=1, col=i)

        # 1:1 Reference line
        min_v = min(sub['google_tt_min'].min(), sub['inrix_tt_min'].min())
        max_v = max(sub['google_tt_min'].max(), sub['inrix_tt_min'].max())
        padding = (max_v - min_v) * 0.05
        min_v -= padding
        max_v += padding

        fig.add_trace(go.Scatter(
            x=[min_v, max_v],
            y=[min_v, max_v],
            mode='lines',
            line=dict(color='#64748b', dash='dash', width=1.5),
            name='1:1 Agreement (y=x)',
            showlegend=(i == 1)
        ), row=1, col=i)

        # Regression line
        if len(sub) > 1 and sub['google_tt_min'].std() > 0:
            slope, intercept = np.polyfit(sub['google_tt_min'], sub['inrix_tt_min'], 1)
            reg_x = np.array([sub['google_tt_min'].min(), sub['google_tt_min'].max()])
            reg_y = slope * reg_x + intercept
            fig.add_trace(go.Scatter(
                x=reg_x,
                y=reg_y,
                mode='lines',
                line=dict(color='#dc2626', width=2.5),
                name=f'OLS Fit (y={slope:.2f}x+{intercept:.2f})',
                showlegend=(i == 1)
            ), row=1, col=i)

        # Stat box annotation
        stat_box_text = f"<b>Pearson r:</b> {corr:.3f}<br><b>R²:</b> {r2:.3f}<br><b>MAE:</b> {mae:.2f} min ({mae*60:.0f}s)<br><b>Bias:</b> {bias:+.2f} min<br><b>N:</b> {len(sub):,}"
        fig.add_annotation(
            xref=f"x{i if i > 1 else ''} domain",
            yref=f"y{i if i > 1 else ''} domain",
            x=0.04, y=0.96,
            xanchor="left", yanchor="top",
            text=stat_box_text,
            showarrow=False,
            bgcolor="rgba(255, 255, 255, 0.85)",
            bordercolor="#cbd5e1",
            borderwidth=1,
            borderpad=6,
            font=dict(size=10.5, color="#1e293b")
        )

        # Custom diagnostic callouts
        for annot in p.get('annotations', []):
            fig.add_annotation(
                xref=f"x{i if i > 1 else ''}",
                yref=f"y{i if i > 1 else ''}",
                x=annot['x'],
                y=annot['y'],
                text=annot['text'],
                showarrow=True,
                arrowhead=2,
                arrowsize=1,
                arrowwidth=1.5,
                arrowcolor=annot.get('arrowcolor', '#ef4444'),
                ax=annot.get('ax', -40),
                ay=annot.get('ay', -40),
                bgcolor="rgba(255,255,255,0.92)",
                bordercolor=annot.get('bordercolor', '#ef4444'),
                borderwidth=1,
                font=dict(size=10, color="#0f172a")
            )

        fig.update_xaxes(title_text="Google Maps Travel Time (min)", range=[min_v, max_v], row=1, col=i)
        fig.update_yaxes(title_text="INRIX XD Travel Time (min)", range=[min_v, max_v], row=1, col=i)

    fig.update_layout(
        height=height,
        margin=dict(l=60, r=40, t=50, b=50),
        template="plotly_white",
        legend=dict(orientation="h", yanchor="bottom", y=1.04, xanchor="right", x=1)
    )

    return fig.to_html(full_html=False, include_plotlyjs=False, config=dict(responsive=True, displayModeBar=True))


def build_diurnal_profiles(panels, height=400):
    """
    panels: list of dicts:
    {
      'title': 'Cascade-HSB Profile',
      'traces': [
         {'df': sub_df, 'label': 'Sunday', 'color_g': '#2563eb', 'color_i': '#d97706'}
      ]
    }
    """
    n_cols = len(panels)
    fig = make_subplots(
        rows=1, cols=n_cols,
        subplot_titles=[p['title'] for p in panels],
        horizontal_spacing=0.1
    )

    for i, p in enumerate(panels, 1):
        for tr in p['traces']:
            sub = tr['df']
            lbl = tr['label']
            cg = tr.get('color_g', '#2563eb')
            ci = tr.get('color_i', '#f59e0b')

            hourly = sub.groupby('hour').agg(
                g_mean=('google_tt_min', 'mean'),
                g_std=('google_tt_min', 'std'),
                i_mean=('inrix_tt_min', 'mean'),
                i_std=('inrix_tt_min', 'std')
            ).reset_index()

            hours = [f"{int(h):02d}:00" for h in hourly['hour']]

            # Google Mean
            fig.add_trace(go.Scatter(
                x=hours,
                y=hourly['g_mean'],
                mode='lines+markers',
                line=dict(color=cg, width=2.5),
                marker=dict(size=6),
                name=f"Google Maps ({lbl})",
                showlegend=(i == 1)
            ), row=1, col=i)

            # INRIX Mean
            fig.add_trace(go.Scatter(
                x=hours,
                y=hourly['i_mean'],
                mode='lines+markers',
                line=dict(color=ci, width=2.5, dash='dash'),
                marker=dict(size=6, symbol='square'),
                name=f"INRIX XD ({lbl})",
                showlegend=(i == 1)
            ), row=1, col=i)

        fig.update_xaxes(title_text="Hour of Day", row=1, col=i)
        fig.update_yaxes(title_text="Mean Travel Time (min)", row=1, col=i)

    fig.update_layout(
        height=height,
        margin=dict(l=60, r=40, t=50, b=50),
        template="plotly_white",
        legend=dict(orientation="h", yanchor="bottom", y=1.04, xanchor="right", x=1)
    )

    return fig.to_html(full_html=False, include_plotlyjs=False, config=dict(responsive=True, displayModeBar=True))


# -------------------------------------------------------------
# Main Generation Logic
# -------------------------------------------------------------

print("Reading datasets...")
df = pd.read_csv(DATA_CSV)
df['dt'] = pd.to_datetime(df['google_timestamp'])
df['hour'] = df['dt'].dt.hour
df['dow'] = df['dt'].dt.day_name()
df['diff'] = df['inrix_tt_min'] - df['google_tt_min']

summary_df = pd.read_csv(SUMMARY_CSV)
with open(MAPPING_JSON) as f:
    mapping_data = json.load(f)

print(f"Loaded {len(df):,} matched 15-minute rows.")

# -------------------------------------------------------------
# Report 1: SH-55 Mountain Corridor
# -------------------------------------------------------------
print("Building Report 1: SH-55 Mountain Corridor...")
cas_sb = df[df['sheet'] == 'Cascade-HSB']
cas_nb = df[df['sheet'] == 'HSB-Cascade']

heatmaps_cas_sb = build_heatmaps_for_sheet(cas_sb, "Cascade-HSB (Southbound, 50.8 mi)")
heatmaps_cas_nb = build_heatmaps_for_sheet(cas_nb, "HSB-Cascade (Northbound, 50.8 mi)")

# Scatter panels
cas_scatter_panels = [
    {
        'title': 'Cascade to HSB (Southbound: 50.8 mi, 58 segs)',
        'df': cas_sb,
        'annotations': [
            {
                'x': 59.0, 'y': 59.5,
                'text': '<b>Free-Flow Plateau (Mon/Fri/Sat)</b><br>σ ≈ 1.2 min. Near-zero variance<br>artificially suppresses R² to 0.432<br>despite MAE = 1.68 min (2.5% error)',
                'ax': -80, 'ay': -90,
                'arrowcolor': '#0284c7', 'bordercolor': '#0284c7'
            },
            {
                'x': 78.0, 'y': 72.0,
                'text': '<b>Sunday Peak Recreation Surges</b><br>R² surges to 0.910 (r = 0.954).<br>INRIX 15-min harmonic averaging<br>slightly smooths peak queue spikes.',
                'ax': 40, 'ay': 60,
                'arrowcolor': '#dc2626', 'bordercolor': '#dc2626'
            }
        ]
    },
    {
        'title': 'HSB to Cascade (Northbound: 50.8 mi, 78 segs)',
        'df': cas_nb,
        'annotations': [
            {
                'x': 58.5, 'y': 60.0,
                'text': '<b>January Free-Flow Conditions</b><br>Google Mean: 58.8 min | INRIX: 60.1 min<br>Mean Bias: +1.27 min (2.7% error).<br>Points tightly hugging the 1:1 line.',
                'ax': -50, 'ay': -80,
                'arrowcolor': '#059669', 'bordercolor': '#059669'
            }
        ]
    }
]
scatter_sh55 = build_scatter_with_annotations(cas_scatter_panels, height=520)

# Diurnal curves
diurnal_sh55 = build_diurnal_profiles([
    {
        'title': 'Cascade to HSB Diurnal Profile (Sunday vs. Weekday/Sat)',
        'traces': [
            {'df': cas_sb[cas_sb['dow'] == 'Sunday'], 'label': 'Sunday Surge', 'color_g': '#dc2626', 'color_i': '#ea580c'},
            {'df': cas_sb[cas_sb['dow'] != 'Sunday'], 'label': 'Fri/Sat/Mon Baseline', 'color_g': '#2563eb', 'color_i': '#0891b2'}
        ]
    },
    {
        'title': 'HSB to Cascade Diurnal Profile (Northbound)',
        'traces': [
            {'df': cas_nb, 'label': 'All Days (Jan 2026)', 'color_g': '#16a34a', 'color_i': '#ca8a04'}
        ]
    }
], height=420)

content_sh55 = f"""
  <div class="hero-header">
    <div class="hero-title">
      <span>SH-55 Mountain Corridor Validation</span>
      <span class="tag-badge tag-blue">State Highway 55</span>
      <span class="tag-badge tag-green">100% Segments Stitched</span>
    </div>
    <div class="hero-subtitle">
      Comprehensive cross-validation of INRIX XD vs. Google Maps across 50.8 miles of mountainous two-lane terrain between Horseshoe Bend and Cascade. Analysis combines Southbound (`Cascade-HSB`) and Northbound (`HSB-Cascade`) alignments.
    </div>
    <div class="badges-row">
      <span class="tag-badge tag-purple">Distance: 50.8 Miles</span>
      <span class="tag-badge tag-green">Segments: 58 SB / 78 NB (136 Total)</span>
      <span class="tag-badge tag-blue">Sample Size: 3,288 Matched Bins</span>
      <span class="tag-badge tag-amber">Date Range: Jan 1 - Aug 24, 2026</span>
    </div>
  </div>

  <div class="kpi-grid">
    <div class="kpi-card">
      <div class="kpi-title">Cascade-HSB Bias</div>
      <div class="kpi-val" style="color: #10b981;">-0.15 m</div>
      <div class="kpi-sub">-9 sec error over 51 miles</div>
    </div>
    <div class="kpi-card">
      <div class="kpi-title">Cascade-HSB MAE</div>
      <div class="kpi-val">1.68 m</div>
      <div class="kpi-sub">MAPE: 2.5% error</div>
    </div>
    <div class="kpi-card">
      <div class="kpi-title">Cascade-HSB Sun R²</div>
      <div class="kpi-val" style="color: #2563eb;">0.910</div>
      <div class="kpi-sub">Pearson r = 0.954 (N=734)</div>
    </div>
    <div class="kpi-card">
      <div class="kpi-title">HSB-Cascade Bias</div>
      <div class="kpi-val" style="color: #0284c7;">+1.27 m</div>
      <div class="kpi-sub">+76 sec over 51 miles</div>
    </div>
    <div class="kpi-card">
      <div class="kpi-title">HSB-Cascade MAE</div>
      <div class="kpi-val">1.56 m</div>
      <div class="kpi-sub">MAPE: 2.7% error</div>
    </div>
    <div class="kpi-card">
      <div class="kpi-title">Stitching Completeness</div>
      <div class="kpi-val" style="color: #10b981;">100%</div>
      <div class="kpi-sub">136 / 136 XD Segments</div>
    </div>
  </div>

  <!-- Heatmaps Section -->
  <div class="chart-card">
    <div class="chart-header">
      <div>
        <div class="chart-title">1. Congestion Heatmaps: Southbound (Cascade to Horseshoe Bend)</div>
        <div class="chart-desc">Side-by-side comparison of Google Maps TT, INRIX XD TT, and Difference by Hour of Day and Day of Week across 2,936 intervals.</div>
      </div>
    </div>
    <div class="chart-body">
      {heatmaps_cas_sb}
    </div>
  </div>

  <div class="chart-card">
    <div class="chart-header">
      <div>
        <div class="chart-title">2. Congestion Heatmaps: Northbound (Horseshoe Bend to Cascade)</div>
        <div class="chart-desc">Northbound winter free-flow travel patterns across 352 matched intervals (January 2026).</div>
      </div>
    </div>
    <div class="chart-body">
      {heatmaps_cas_nb}
    </div>
  </div>

  <!-- Scatter & R2 Diagnostic -->
  <div class="chart-card">
    <div class="chart-header">
      <div>
        <div class="chart-title">3. The R² Diagnostic: Scatter Comparisons (Google vs. INRIX)</div>
        <div class="chart-desc">Demonstrating why R² is misleading on uncongested corridors: near-zero variance collapses R² during free flow, while Sunday surges exhibit near-perfect agreement (R² = 0.910).</div>
      </div>
    </div>
    <div class="chart-body">
      {scatter_sh55}
    </div>
  </div>

  <!-- Diurnal Curves -->
  <div class="chart-card">
    <div class="chart-header">
      <div>
        <div class="chart-title">4. Diurnal Commute Curves: Hourly Travel Time Tracking</div>
        <div class="chart-desc">Hour-by-hour tracking comparing Google Maps (solid line) and INRIX (dashed line). Note the exact coincidence of the Sunday afternoon recreational wave peak.</div>
      </div>
    </div>
    <div class="chart-body">
      {diurnal_sh55}
    </div>
  </div>

  <!-- Write-up Section -->
  <div class="writeup-section">
    <h2>Engineering Analysis & Findings: SH-55 Mountain Corridor</h2>
    
    <h3>Corridor Overview & Alignment Completeness</h3>
    <p>
      The SH-55 corridor between Horseshoe Bend and Cascade represents 50.8 miles of critical rural two-lane highway through mountainous topography. With all segments stitched across Parts 1, 2, and 3, both alignments are <strong>100% complete</strong> (58 segments Southbound, 78 segments Northbound). Across 3,288 matched 15-minute time intervals, this corridor represents the most extensive rural travel time validation in Idaho.
    </p>

    <div class="alert-box alert-success">
      <span class="alert-title">Core Validation Finding: Sub-Minute Baseline Agreement</span>
      Over 50.8 miles of winding mountain highway, the overall mean bias between INRIX XD and Google Maps is only <strong>-0.15 minutes (-9 seconds)</strong> Southbound and <strong>+1.27 minutes (+76 seconds)</strong> Northbound. Mean Absolute Error (MAE) is 1.68 min (2.5% error) and 1.56 min (2.7% error). For baseline travel time calculations, the two data sources are essentially interchangeable.
    </div>

    <h3>Deconstructing the "Flat Variance" Paradox ($R^2 = 0.432$)</h3>
    <p>
      A cursory review of the summary statistics might suggest that an $R^2$ of 0.432 ($r = 0.657$) on `Cascade-HSB` indicates poor agreement. However, examining the heatmaps and scatter plots reveals that this is an artifact of the mathematical definition of $R^2$:
    </p>
    <ul>
      <li><strong>The Free-Flow Plateau:</strong> On Fridays, Saturdays, and Mondays, traffic on SH-55 moves freely at the posted speed limit (55–65 mph). Travel times cluster tightly between 58 and 60 minutes (&sigma; &approx; 1.2 minutes). Because the total sum of squares (SS<sub>tot</sub> = &Sigma;(y<sub>i</sub> - y&#772;)<sup>2</sup>) is near zero, tiny random fluctuations (e.g., passing a single slow truck or recreational vehicle) cause R² to collapse mathematically (R² = 0.062, r = 0.249), even though the actual prediction error is only 1.2 minutes over 51 miles!</li>
      <li><strong>The Sunday Congestion Surge:</strong> On Sundays, when recreational travelers return from McCall, Payette Lake, and Cascade, substantial travel time variance is introduced (&sigma; = 8.7 minutes). In this high-variance regime, INRIX tracks Google Maps with astounding precision: <strong>r = 0.954 (R² = 0.910)</strong> with an average bias of only 10 seconds.</li>
    </ul>

    <h3>High-End Peak Compression During Summer Waves</h3>
    <p>
      The scatter plot illustrates that during the most extreme summer holiday surges (Sundays in July and August), Google Maps occasionally records peak instantaneous travel times of 80 to 95 minutes, whereas INRIX peaks between 72 and 82 minutes. This is caused by fundamental differences in aggregation methodologies:
    </p>
    <ul>
      <li><strong>Google Maps Probe Snapshots:</strong> Google captures instantaneous vehicle probe trajectories through the bottleneck at Banks (where SH-17 merges into SH-55), measuring the queue tail at that exact moment.</li>
      <li><strong>INRIX Space-Mean Harmonic Averaging:</strong> INRIX computes the space-mean speed across the entire length of each XD segment over a rolling 15-minute window. This space-time smoothing naturally damps out localized, momentary queue spikes while accurately preserving total corridor delay.</li>
    </ul>

    <div class="alert-box alert-info">
      <span class="alert-title">Practical ITD Guidance & Interchangeability Verdict</span>
      <strong>Interchangeability Verdict: Fully Interchangeable (Grade A).</strong><br>
      For planning studies, corridor travel time reliability metrics, and seasonal delay monitoring, INRIX XD data can be used directly in place of Google Maps without calibration. For evaluating peak-hour queue lengths at the Banks merge during holiday weekends, apply a +10% expansion factor to INRIX peak 15-minute delays to match instantaneous queue extremes.
    </div>
  </div>
"""

with open(os.path.join(OUTPUT_DIR, "sh55_mountain_comparison.html"), "w") as f:
    f.write(wrap_page("SH-55 Mountain Corridor", "sh55_mountain_comparison.html", content_sh55))
print("Saved out/reports/sh55_mountain_comparison.html")


# -------------------------------------------------------------
# Report 2: Eagle Road VSL Pilot Section (SH-55)
# -------------------------------------------------------------
print("Building Report 2: Eagle Road VSL Pilot...")
vsl_df = df[df['sheet'].str.startswith('VSL')]
vsl_nb_am = df[df['sheet'] == 'VSL NB AM']
vsl_nb_pm = df[df['sheet'] == 'VSL NB PM']
vsl_sb_am = df[df['sheet'] == 'VSL SB AM']
vsl_sb_pm = df[df['sheet'] == 'VSL SB PM']

heatmaps_vsl_nb_pm = build_heatmaps_for_sheet(vsl_nb_pm, "VSL NB PM Peak (Heavily Congested Direction)", height=320)
heatmaps_vsl_sb_pm = build_heatmaps_for_sheet(vsl_sb_pm, "VSL SB PM Peak (Off-Peak Direction)", height=320)

vsl_scatter_panels = [
    {
        'title': 'VSL NB PM Peak (Congested Commute)',
        'df': vsl_nb_pm,
        'annotations': [
            {
                'x': 14.5, 'y': 11.5,
                'text': '<b>Peak Queue Compression</b><br>Google instantaneous probes capture queue<br>spikes at Chinden & State St;<br>INRIX 15-min harmonic average smooths extremes.<br>r = 0.882 (R² = 0.778)',
                'ax': -50, 'ay': -70,
                'arrowcolor': '#dc2626', 'bordercolor': '#dc2626'
            }
        ]
    },
    {
        'title': 'VSL SB PM Peak (The Off-Peak Trap)',
        'df': vsl_sb_pm,
        'annotations': [
            {
                'x': 6.8, 'y': 7.5,
                'text': '<b>Signal Phase Noise Trap</b><br>σ = 22 seconds! Off-peak direction has<br>near-zero macro variance. Random signal arrival<br>phase noise (±30-45s) collapses R² to 0.167<br>despite MAE being only 48 seconds!',
                'ax': 60, 'ay': -80,
                'arrowcolor': '#ea580c', 'bordercolor': '#ea580c'
            }
        ]
    },
    {
        'title': 'VSL SB AM Peak (Morning Commute)',
        'df': vsl_sb_am,
        'annotations': [
            {
                'x': 5.8, 'y': 5.8,
                'text': '<b>Near-Perfect Identity</b><br>Bias: +1.8 seconds (+0.03m)<br>MAE: 18 seconds (0.30m)<br>r = 0.933 (R² = 0.871)',
                'ax': -50, 'ay': 60,
                'arrowcolor': '#16a34a', 'bordercolor': '#16a34a'
            }
        ]
    },
    {
        'title': 'VSL NB AM Peak (Morning Counter-Peak)',
        'df': vsl_nb_am,
        'annotations': [
            {
                'x': 6.0, 'y': 5.8,
                'text': '<b>High Agreement</b><br>Bias: -13 seconds (-0.22m)<br>MAE: 30 seconds (0.50m)<br>r = 0.928 (R² = 0.861)',
                'ax': -50, 'ay': -50,
                'arrowcolor': '#2563eb', 'bordercolor': '#2563eb'
            }
        ]
    }
]
scatter_vsl = build_scatter_with_annotations(vsl_scatter_panels, height=520)

# Diurnal curves for VSL
diurnal_vsl = build_diurnal_profiles([
    {
        'title': 'AM Commute Period (05:00 - 08:45)',
        'traces': [
            {'df': vsl_sb_am, 'label': 'SB AM (Commute)', 'color_g': '#2563eb', 'color_i': '#0891b2'},
            {'df': vsl_nb_am, 'label': 'NB AM (Counter-Peak)', 'color_g': '#16a34a', 'color_i': '#ca8a04'}
        ]
    },
    {
        'title': 'PM Commute Period (14:00 - 17:45)',
        'traces': [
            {'df': vsl_nb_pm, 'label': 'NB PM (Commute)', 'color_g': '#dc2626', 'color_i': '#ea580c'},
            {'df': vsl_sb_pm, 'label': 'SB PM (Off-Peak)', 'color_g': '#9333ea', 'color_i': '#db2777'}
        ]
    }
], height=420)

content_vsl = f"""
  <div class="hero-header">
    <div class="hero-title">
      <span>Eagle Road VSL Pilot Section Validation</span>
      <span class="tag-badge tag-blue">SH-55 Variable Speed Limit</span>
      <span class="tag-badge tag-green">100% Segments Stitched</span>
    </div>
    <div class="hero-subtitle">
      Analysis of the 3.64-mile Variable Speed Limit (VSL) test section on SH-55 between Fairview Avenue and SH-44 (State Street). Evaluates all four peak commute periods: Northbound AM, Northbound PM, Southbound AM, and Southbound PM.
    </div>
    <div class="badges-row">
      <span class="tag-badge tag-purple">Distance: 3.64 Miles</span>
      <span class="tag-badge tag-green">Segments: 7 / 7 XD Segments (100%)</span>
      <span class="tag-badge tag-blue">Sample Size: 2,360 Matched Intervals</span>
      <span class="tag-badge tag-amber">Sampling: Mid-Week (Tue-Thu)</span>
    </div>
  </div>

  <div class="kpi-grid">
    <div class="kpi-card">
      <div class="kpi-title">VSL SB AM MAE</div>
      <div class="kpi-val" style="color: #10b981;">18 sec</div>
      <div class="kpi-sub">r = 0.933 (R² = 0.871)</div>
    </div>
    <div class="kpi-card">
      <div class="kpi-title">VSL SB AM Bias</div>
      <div class="kpi-val" style="color: #10b981;">+1.8 sec</div>
      <div class="kpi-sub">Google 5.75m vs INRIX 5.79m</div>
    </div>
    <div class="kpi-card">
      <div class="kpi-title">VSL NB AM MAE</div>
      <div class="kpi-val" style="color: #2563eb;">30 sec</div>
      <div class="kpi-sub">r = 0.928 (R² = 0.861)</div>
    </div>
    <div class="kpi-card">
      <div class="kpi-title">VSL NB PM MAE</div>
      <div class="kpi-val" style="color: #f59e0b;">1.87 min</div>
      <div class="kpi-sub">r = 0.882 (R² = 0.778)</div>
    </div>
    <div class="kpi-card">
      <div class="kpi-title">VSL SB PM MAE</div>
      <div class="kpi-val" style="color: #0284c7;">48 sec</div>
      <div class="kpi-sub">R² = 0.167 (Signal Phase Noise)</div>
    </div>
    <div class="kpi-card">
      <div class="kpi-title">Overall Alignment</div>
      <div class="kpi-val" style="color: #10b981;">100%</div>
      <div class="kpi-sub">All 7 XD Segments</div>
    </div>
  </div>

  <!-- Heatmaps -->
  <div class="chart-card">
    <div class="chart-header">
      <div>
        <div class="chart-title">1. Congestion Heatmaps: PM Commute Directional Comparison</div>
        <div class="chart-desc">Contrasting the heavily congested Northbound PM commute against the free-flowing Southbound PM direction (14:00 to 17:45).</div>
      </div>
    </div>
    <div class="chart-body">
      <h4 style="margin-bottom: 10px; color: #1e293b;">A. Northbound PM Peak (Congested Commute Direction)</h4>
      {heatmaps_vsl_nb_pm}
      <h4 style="margin: 20px 0 10px; color: #1e293b;">B. Southbound PM Peak (Off-Peak Return Direction)</h4>
      {heatmaps_vsl_sb_pm}
    </div>
  </div>

  <!-- Scatter & R2 Diagnostic -->
  <div class="chart-card">
    <div class="chart-header">
      <div>
        <div class="chart-title">2. Scatter Analysis: The Four Commute Regimes</div>
        <div class="chart-desc">Side-by-side scatter plots illustrating high correlation in active commute periods and the collapse of R² under off-peak signal phase noise.</div>
      </div>
    </div>
    <div class="chart-body">
      {scatter_vsl}
    </div>
  </div>

  <!-- Diurnal Curves -->
  <div class="chart-card">
    <div class="chart-header">
      <div>
        <div class="chart-title">3. Diurnal Profiles: Peak Period Commute Trajectories</div>
        <div class="chart-desc">15-minute tracking of travel times across morning (05:00 - 08:45) and evening (14:00 - 17:45) commute windows.</div>
      </div>
    </div>
    <div class="chart-body">
      {diurnal_vsl}
    </div>
  </div>

  <!-- Engineering Write-up -->
  <div class="writeup-section">
    <h2>Engineering Analysis & Findings: Eagle Road VSL Pilot</h2>

    <h3>Corridor Geography & VSL Context</h3>
    <p>
      The 3.64-mile section of SH-55 (Eagle Road) between Fairview Avenue and State Street (SH-44) is the highest-volume non-freeway arterial in Idaho, carrying over 55,000 vehicles per day through 8 major signalized intersections. This corridor is equipped with ITD's Variable Speed Limit (VSL) pilot system, which dynamically adjusts regulatory speed limits based on prevailing traffic speeds and congestion queues.
    </p>

    <div class="alert-box alert-success">
      <span class="alert-title">Morning Commute Near-Identity: Sub-30 Second Errors</span>
      During the morning peak (05:00 - 08:45), both Southbound (inbound commuter direction) and Northbound show extraordinary agreement:
      <ul>
        <li><strong>VSL SB AM:</strong> Google Mean 5.75 min vs INRIX 5.79 min. Mean Bias is <strong>+1.8 seconds</strong>, MAE is <strong>18 seconds</strong> (5.1% error), and Pearson $r = 0.933$ ($R^2 = 0.871$).</li>
        <li><strong>VSL NB AM:</strong> Google Mean 6.02 min vs INRIX 5.80 min. Mean Bias is <strong>-13 seconds</strong>, MAE is <strong>30 seconds</strong> (7.9% error), and Pearson $r = 0.928$ ($R^2 = 0.861$).</li>
      </ul>
    </div>

    <h3>Why $R^2 = 0.167$ on VSL SB PM: The "Signal Phase Noise" Trap</h3>
    <p>
      The most striking anomaly in the dataset occurs on <code>VSL SB PM</code>, where the correlation drops to $r = 0.408$ ($R^2 = 0.167$). However, the Mean Absolute Error is only <strong>0.80 minutes (48 seconds)</strong>. Why does the correlation collapse when error is under a minute?
    </p>
    <ul>
      <li><strong>Off-Peak Dynamic Range:</strong> In the evening commute, Southbound Eagle Road is the off-peak direction. Commuters are heading north to Eagle and Star; Southbound traffic flows smoothly with virtually no macro congestion. The standard deviation of Google travel times is only <strong>22 seconds</strong> (&sigma; = 0.37 min).</li>
      <li><strong>Signal Cycle Arrival Randomness:</strong> Individual Google probe vehicles experience random signal arrival variations. Hitting 1 red light vs green light adds 30 to 45 seconds of delay. Because this signal phase arrival noise (&plusmn;30-45 s) is larger than the entire corridor travel time variance (&sigma; = 22 s), the scatter plot becomes a circular cloud, driving R² &rarr; 0.167.</li>
    </ul>

    <h3>Evening Commute Queue Compression (VSL NB PM)</h3>
    <p>
      In the heavily congested Northbound PM commute (14:00 - 17:45), traffic volume exceeds signal capacity at Chinden Blvd and State Street. Travel times surge from a 5-minute baseline up to 12–18 minutes.
    </p>
    <ul>
      <li>Both datasets track the congestion onset at 14:30, peak at 16:30, and dissipation after 17:30 simultaneously ($r = 0.882, R^2 = 0.778$).</li>
      <li>Google Maps logs higher peak-of-the-peak extremes (Google Mean: 10.08 min vs INRIX Mean: 8.22 min). This occurs because INRIX XD speeds are harmonic space-mean speeds averaged across 3.6 miles over 15 minutes, whereas Google probes get caught in localized intersection spillback queues.</li>
    </ul>

    <div class="alert-box alert-info">
      <span class="alert-title">VSL Operational Implementation Guidance</span>
      <strong>VSL System Interchangeability: Ready for Direct Use.</strong><br>
      INRIX XD 15-minute speed feeds can reliably trigger VSL automated speed reductions. Because INRIX smooths single-cycle signal queue spikes, it prevents "hunting" (erratic oscillation between speed limit steps), making it superior to raw vehicle probe snapshots for automated speed limit management. For calculating user travel time delay, apply a +15% scaling factor to INRIX evening peak delays.
    </div>
  </div>
"""

with open(os.path.join(OUTPUT_DIR, "eagle_rd_vsl_comparison.html"), "w") as f:
    f.write(wrap_page("Eagle Road VSL Pilot", "eagle_rd_vsl_comparison.html", content_vsl))
print("Saved out/reports/eagle_rd_vsl_comparison.html")


# -------------------------------------------------------------
# Report 3: Eagle Road Full Corridor (SH-55)
# -------------------------------------------------------------
print("Building Report 3: Eagle Road Full Corridor...")
eagle_nb = df[df['sheet'] == 'Eagle Rd NB']
eagle_sb = df[df['sheet'] == 'Eagle Rd SB']

heatmaps_eagle_nb = build_heatmaps_for_sheet(eagle_nb, "Eagle Rd NB (I-84 to SH-44)", height=340)
heatmaps_eagle_sb = build_heatmaps_for_sheet(eagle_sb, "Eagle Rd SB (SH-44 to I-84)", height=340)

eagle_scatter_panels = [
    {
        'title': 'Eagle Rd NB Full Corridor (r = 0.930, R² = 0.865)',
        'df': eagle_nb,
        'annotations': [
            {
                'x': 22.0, 'y': 13.5,
                'text': '<b>Ultra-High Correlation (r = 0.930)</b><br>Parallel fit line reflects near-perfect<br>dynamic tracking of Google traffic spikes.<br>Mean Bias (-8.38m) reflects Google query<br>extending south of I-84 into non-SH-55 segments.',
                'ax': 50, 'ay': 60,
                'arrowcolor': '#2563eb', 'bordercolor': '#2563eb'
            }
        ]
    },
    {
        'title': 'Eagle Rd SB Full Corridor (r = 0.945, R² = 0.892)',
        'df': eagle_sb,
        'annotations': [
            {
                'x': 18.5, 'y': 13.0,
                'text': '<b>Highest Correlation in Dataset (r = 0.945)</b><br>Near-linear alignment with Google Maps.<br>Systematic 5.5 min offset is driven purely by<br>spatial boundary definitions (7.1 mi SH-55 vs ~10 mi query).',
                'ax': 50, 'ay': -60,
                'arrowcolor': '#16a34a', 'bordercolor': '#16a34a'
            }
        ]
    }
]
scatter_eagle = build_scatter_with_annotations(eagle_scatter_panels, height=500)

diurnal_eagle = build_diurnal_profiles([
    {
        'title': 'Eagle Road Northbound Diurnal Curve (05:00 - 17:45)',
        'traces': [
            {'df': eagle_nb, 'label': 'Eagle Rd NB', 'color_g': '#2563eb', 'color_i': '#f59e0b'}
        ]
    },
    {
        'title': 'Eagle Road Southbound Diurnal Curve (05:00 - 17:45)',
        'traces': [
            {'df': eagle_sb, 'label': 'Eagle Rd SB', 'color_g': '#16a34a', 'color_i': '#dc2626'}
        ]
    }
], height=400)

content_eagle = f"""
  <div class="hero-header">
    <div class="hero-title">
      <span>Eagle Road Full Corridor Validation</span>
      <span class="tag-badge tag-blue">SH-55 State Highway Alignment</span>
      <span class="tag-badge tag-green">100% SH-55 Segments Stitched</span>
    </div>
    <div class="hero-subtitle">
      Full corridor evaluation of SH-55 along Eagle Road between I-84 and SH-44 (State Street). Demonstrates highest dynamic correlation in District 3 ($r = 0.945$) and diagnoses spatial query boundary offsets.
    </div>
    <div class="badges-row">
      <span class="tag-badge tag-purple">Distance: 7.08 Miles</span>
      <span class="tag-badge tag-green">Segments: 17 / 17 SH-55 Segments</span>
      <span class="tag-badge tag-blue">Sample Size: 3,874 Matched Intervals</span>
      <span class="tag-badge tag-amber">Sampling: Tue-Thu (05:00 - 17:45)</span>
    </div>
  </div>

  <div class="kpi-grid">
    <div class="kpi-card">
      <div class="kpi-title">Eagle SB Correlation</div>
      <div class="kpi-val" style="color: #10b981;">0.945</div>
      <div class="kpi-sub">R² = 0.892 (Highest in Study)</div>
    </div>
    <div class="kpi-card">
      <div class="kpi-title">Eagle NB Correlation</div>
      <div class="kpi-val" style="color: #2563eb;">0.930</div>
      <div class="kpi-sub">R² = 0.865 (Near-Lockstep)</div>
    </div>
    <div class="kpi-card">
      <div class="kpi-title">Eagle SB Mean TT</div>
      <div class="kpi-val">12.87 m</div>
      <div class="kpi-sub">Google: 18.35 m (Bias: -5.49m)</div>
    </div>
    <div class="kpi-card">
      <div class="kpi-title">Eagle NB Mean TT</div>
      <div class="kpi-val">13.53 m</div>
      <div class="kpi-sub">Google: 21.92 m (Bias: -8.38m)</div>
    </div>
    <div class="kpi-card">
      <div class="kpi-title">SH-55 Completeness</div>
      <div class="kpi-val" style="color: #10b981;">100%</div>
      <div class="kpi-sub">All official state highway XD segs</div>
    </div>
    <div class="kpi-card">
      <div class="kpi-title">Matched Bins</div>
      <div class="kpi-val">3,874</div>
      <div class="kpi-sub">1,937 NB / 1,937 SB</div>
    </div>
  </div>

  <!-- Heatmaps -->
  <div class="chart-card">
    <div class="chart-header">
      <div>
        <div class="chart-title">1. Congestion Heatmaps: Full Corridor Travel Times</div>
        <div class="chart-desc">Diurnal heatmaps showing identical congestion onset and clearance between Google Maps and INRIX across all mid-week commute hours.</div>
      </div>
    </div>
    <div class="chart-body">
      <h4 style="margin-bottom: 10px; color: #1e293b;">A. Northbound (I-84 to SH-44)</h4>
      {heatmaps_eagle_nb}
      <h4 style="margin: 20px 0 10px; color: #1e293b;">B. Southbound (SH-44 to I-84)</h4>
      {heatmaps_eagle_sb}
    </div>
  </div>

  <!-- Scatter & R2 Diagnostic -->
  <div class="chart-card">
    <div class="chart-header">
      <div>
        <div class="chart-title">2. Scatter Comparisons: Dynamic Tracking vs. Boundary Offset</div>
        <div class="chart-desc">The scatter plots show tight, parallel linear relationships with slopes near unity, confirming that traffic variations match with >90% fidelity once spatial boundaries are aligned.</div>
      </div>
    </div>
    <div class="chart-body">
      {scatter_eagle}
    </div>
  </div>

  <!-- Diurnal Curves -->
  <div class="chart-card">
    <div class="chart-header">
      <div>
        <div class="chart-title">3. Diurnal Profiles: Identical Shape and Inflection Points</div>
        <div class="chart-desc">Notice how both curves exhibit identical morning peaks (08:00), midday lulls (10:00 - 12:00), and afternoon peak surges (16:00).</div>
      </div>
    </div>
    <div class="chart-body">
      {diurnal_eagle}
    </div>
  </div>

  <!-- Engineering Write-up -->
  <div class="writeup-section">
    <h2>Engineering Analysis & Findings: Eagle Road Full Corridor</h2>

    <h3>The Highest Dynamic Correlation in District 3</h3>
    <p>
      The full-corridor evaluation of Eagle Road produces the strongest correlation observed anywhere in the validation study: <strong>$r = 0.945$ ($R^2 = 89.2%$)</strong> Southbound and <strong>$r = 0.930$ ($R^2 = 86.5%$)</strong> Northbound across 3,874 matched observations. This proves beyond doubt that INRIX XD probe data captures urban arterial traffic waves, signal progression breakdown, and commute surge dynamics with near-perfect fidelity.
    </p>

    <div class="alert-box alert-warning">
      <span class="alert-title">Diagnosing the Mean Bias: Spatial Boundary Discrepancy</span>
      Despite the 94.5% correlation, INRIX reports a mean travel time that is 5.49 minutes lower Southbound and 8.38 minutes lower Northbound. A traffic engineer might mistakenly suspect missing data. The actual cause is spatial query boundary mismatch:
      <ul>
        <li><strong>Google Maps Query Extents:</strong> In <code>TT Logger.xlsx</code>, the user's automated Google API queries were defined from origins and destinations extending significantly beyond SH-55: south of I-84 down to Overland Rd and Victory Rd, and north beyond SH-44 into downtown Eagle. This query traversed ~10 miles and crossed 5 additional high-delay city intersections.</li>
        <li><strong>INRIX XD Alignment:</strong> Our XD segment stitching strictly follows the legal ITD State Highway system (SH-55), beginning at the I-84 Exit 46 interchange and terminating at SH-44 State Street (7.08 miles).</li>
      </ul>
      When comparing the diurnal profiles, the curves are mathematically parallel. The 5 to 8 minute difference is simply the baseline travel time of the 3 extra non-highway miles measured by Google!
    </div>

    <h3>Congestion Footprint Agreement</h3>
    <p>
      The heatmaps show that both data sources identify the exact same peak congestion windows:
    </p>
    <ul>
      <li><strong>Morning Commute:</strong> Peaks at 07:45 - 08:30 (SB TT increases by 50% above free flow).</li>
      <li><strong>Midday Plateau:</strong> Constant moderate travel times from 11:30 to 13:30.</li>
      <li><strong>Evening Commute:</strong> Heavy congestion building from 15:00, peaking at 16:30 - 17:15, and tapering off after 18:00 (NB TT doubles from 14 to 28 minutes in Google, and 10 to 17 minutes in INRIX).</li>
    </ul>

    <div class="alert-box alert-info">
      <span class="alert-title">Interchangeability Verdict: Fully Compatible (Calibration Required for Boundary Extents)</span>
      For relative congestion tracking, travel time index (TTI) calculations, and before/after signal retiming studies, INRIX XD is fully interchangeable with Google Maps. When comparing absolute travel times, ensure the spatial start/end coordinates are clipped to the exact same physical cross-streets.
    </div>
  </div>
"""

with open(os.path.join(OUTPUT_DIR, "eagle_rd_full_comparison.html"), "w") as f:
    f.write(wrap_page("Eagle Road Full Corridor", "eagle_rd_full_comparison.html", content_eagle))
print("Saved out/reports/eagle_rd_full_comparison.html")


# -------------------------------------------------------------
# Report 4: SH-69 Meridian Road Commuter Corridor
# -------------------------------------------------------------
print("Building Report 4: SH-69 Meridian Road...")
sh69_nb = df[df['sheet'] == 'SH-69 NB']
sh69_sb = df[df['sheet'] == 'SH-69 SB']

heatmaps_sh69_nb = build_heatmaps_for_sheet(sh69_nb, "SH-69 NB (Kuna to I-84)", height=340)
heatmaps_sh69_sb = build_heatmaps_for_sheet(sh69_sb, "SH-69 SB (I-84 to Kuna)", height=340)

sh69_scatter_panels = [
    {
        'title': 'SH-69 NB: Kuna to I-84 (Inbound Commute)',
        'df': sh69_nb,
        'annotations': [
            {
                'x': 11.5, 'y': 10.0,
                'text': '<b>High Agreement (r = 0.889)</b><br>Google: 11.37m | INRIX: 10.02m<br>MAE: 1.36 min (11.6% error).<br>Tightly aligned across all hours.',
                'ax': -50, 'ay': -60,
                'arrowcolor': '#16a34a', 'bordercolor': '#16a34a'
            }
        ]
    },
    {
        'title': 'SH-69 SB: I-84 to Kuna (Outbound Commute)',
        'df': sh69_sb,
        'annotations': [
            {
                'x': 16.5, 'y': 10.0,
                'text': '<b>I-84 Exit 44 Ramp Queuing Offset</b><br>Google query origin north of I-84 includes<br>off-ramp and Overland Rd signal queue delay.<br>INRIX starts on SH-69 mainline south of interchange.<br>Creates systematic 5.8m baseline delta.',
                'ax': 50, 'ay': 70,
                'arrowcolor': '#dc2626', 'bordercolor': '#dc2626'
            }
        ]
    }
]
scatter_sh69 = build_scatter_with_annotations(sh69_scatter_panels, height=500)

diurnal_sh69 = build_diurnal_profiles([
    {
        'title': 'SH-69 Northbound Diurnal Curve (Morning Peak Focus)',
        'traces': [
            {'df': sh69_nb, 'label': 'SH-69 NB', 'color_g': '#2563eb', 'color_i': '#f59e0b'}
        ]
    },
    {
        'title': 'SH-69 Southbound Diurnal Curve (Evening Peak Focus)',
        'traces': [
            {'df': sh69_sb, 'label': 'SH-69 SB', 'color_g': '#dc2626', 'color_i': '#0891b2'}
        ]
    }
], height=400)

content_sh69 = f"""
  <div class="hero-header">
    <div class="hero-title">
      <span>SH-69 Meridian Road Corridor Validation</span>
      <span class="tag-badge tag-blue">State Highway 69</span>
      <span class="tag-badge tag-green">100% Segments Stitched</span>
    </div>
    <div class="hero-subtitle">
      Validation of the 7.17-mile commuter arterial connecting the City of Kuna to I-84 in Meridian. Contrasts the northbound morning commute against the southbound evening interchange bottleneck.
    </div>
    <div class="badges-row">
      <span class="tag-badge tag-purple">Distance: 7.17 Miles</span>
      <span class="tag-badge tag-green">Segments: 15 / 15 XD Segments (100%)</span>
      <span class="tag-badge tag-blue">Sample Size: 3,872 Matched Intervals</span>
      <span class="tag-badge tag-amber">Sampling: Tue-Thu (05:00 - 17:45)</span>
    </div>
  </div>

  <div class="kpi-grid">
    <div class="kpi-card">
      <div class="kpi-title">SH-69 NB Correlation</div>
      <div class="kpi-val" style="color: #10b981;">0.889</div>
      <div class="kpi-sub">R² = 0.790</div>
    </div>
    <div class="kpi-card">
      <div class="kpi-title">SH-69 NB MAE</div>
      <div class="kpi-val" style="color: #2563eb;">1.36 m</div>
      <div class="kpi-sub">MAPE: 11.6% error</div>
    </div>
    <div class="kpi-card">
      <div class="kpi-title">SH-69 NB Bias</div>
      <div class="kpi-val">-1.35 m</div>
      <div class="kpi-sub">Google 11.4m vs INRIX 10.0m</div>
    </div>
    <div class="kpi-card">
      <div class="kpi-title">SH-69 SB Correlation</div>
      <div class="kpi-val" style="color: #f59e0b;">0.686</div>
      <div class="kpi-sub">R² = 0.470</div>
    </div>
    <div class="kpi-card">
      <div class="kpi-title">SH-69 SB Bias</div>
      <div class="kpi-val" style="color: #ef4444;">-5.85 m</div>
      <div class="kpi-sub">Interchange Ramp Queue Offset</div>
    </div>
    <div class="kpi-card">
      <div class="kpi-title">Stitching Completeness</div>
      <div class="kpi-val" style="color: #10b981;">100%</div>
      <div class="kpi-sub">15 / 15 XD Segments</div>
    </div>
  </div>

  <!-- Heatmaps -->
  <div class="chart-card">
    <div class="chart-header">
      <div>
        <div class="chart-title">1. Congestion Heatmaps: Directional Commute Asymmetry</div>
        <div class="chart-desc">Side-by-side heatmaps illustrating the morning inbound commute on NB vs. the afternoon outbound bottleneck on SB.</div>
      </div>
    </div>
    <div class="chart-body">
      <h4 style="margin-bottom: 10px; color: #1e293b;">A. Northbound (Kuna to I-84)</h4>
      {heatmaps_sh69_nb}
      <h4 style="margin: 20px 0 10px; color: #1e293b;">B. Southbound (I-84 to Kuna)</h4>
      {heatmaps_sh69_sb}
    </div>
  </div>

  <!-- Scatter & R2 Diagnostic -->
  <div class="chart-card">
    <div class="chart-header">
      <div>
        <div class="chart-title">2. Scatter Analysis: Northbound Alignment vs. Southbound Offset</div>
        <div class="chart-desc">Comparison of the clean linear alignment on SH-69 NB against the systematic origin shift on SH-69 SB.</div>
      </div>
    </div>
    <div class="chart-body">
      {scatter_sh69}
    </div>
  </div>

  <!-- Diurnal Curves -->
  <div class="chart-card">
    <div class="chart-header">
      <div>
        <div class="chart-title">3. Diurnal Profiles: Commute Directional Peaks</div>
        <div class="chart-desc">Notice the sharp morning peak on NB (07:00 - 08:00) where Kuna commuters travel north to Boise, and the steady afternoon plateau on SB.</div>
      </div>
    </div>
    <div class="chart-body">
      {diurnal_sh69}
    </div>
  </div>

  <!-- Engineering Write-up -->
  <div class="writeup-section">
    <h2>Engineering Analysis & Findings: SH-69 Meridian Road</h2>

    <h3>Corridor Function & Commute Dynamics</h3>
    <p>
      SH-69 (Meridian Road) is a critical 7.17-mile commuter arterial connecting the rapidly growing residential communities in Kuna with the I-84 freeway corridor. It carries heavy tidal traffic: Northbound towards Boise and Meridian in the morning, and Southbound back to Kuna in the evening. All 15 XD segments are present, providing 100% geometric coverage.
    </p>

    <h3>Northbound Alignment: High-Fidelity Commuter Tracking ($r = 0.889$)</h3>
    <p>
      On <code>SH-69 NB</code>, INRIX and Google Maps show exceptional consistency:
    </p>
    <ul>
      <li><strong>Morning Commute Rush:</strong> Both sources capture the sharp 07:00 - 08:00 AM surge, where travel times increase from a 9.0-minute free-flow baseline to 13.5 minutes in Google and 11.2 minutes in INRIX.</li>
      <li><strong>Error Profile:</strong> MAE is 1.36 minutes (82 seconds), representing an 11.6% mean percentage error. Pearson $r = 0.889$ ($R^2 = 0.790$).</li>
    </ul>

    <div class="alert-box alert-warning">
      <span class="alert-title">The "Origin Interchange Queue Trap": Explaining the 5.8-Minute Delta on SB</span>
      On <code>SH-69 SB</code>, INRIX reports a mean travel time of 9.63 minutes compared to Google's 15.48 minutes ($r = 0.686$, Bias = -5.85 min). Why the large discrepancy?
      <ul>
        <li><strong>Google API Origin Point:</strong> The Google Logger query for "SH-69 SB" was set with its origin at the I-84 Exit 44 interchange / Overland Road intersection. During the PM peak, vehicles exiting westbound I-84 to turn south onto Meridian Road sit in a 4 to 7-minute queue on the off-ramp and through the Overland Rd signal before ever reaching the mainline of SH-69.</li>
        <li><strong>INRIX Mainline Extent:</strong> The 15 INRIX XD segments begin on the free-flowing mainline of SH-69 south of the interchange, completely omitting the freeway ramp queue delay.</li>
        <li><strong>Proof from Off-Peak Data:</strong> At 05:00 AM when the interchange is empty, Google SB TT is 12.5 min and INRIX is 8.7 min. The 3.8-minute baseline offset expands to 7.8 minutes at 17:00 PM as the ramp queue builds.</li>
      </ul>
    </div>

    <div class="alert-box alert-info">
      <span class="alert-title">Interchangeability Verdict: High Agreement Mainline; Isolate Interchange Ramps</span>
      <strong>Verdict: Highly Interchangeable for Mainline SH-69.</strong><br>
      For corridor travel time and speed studies along SH-69, INRIX XD is accurate and reliable. To reconcile Southbound Google data with INRIX, ITD analysts should either add the I-84 Exit 44 ramp XD segment to the INRIX chain or adjust the Google query origin to start south of Overland Road.
    </div>
  </div>
"""

with open(os.path.join(OUTPUT_DIR, "sh69_meridian_comparison.html"), "w") as f:
    f.write(wrap_page("SH-69 Meridian Road", "sh69_meridian_comparison.html", content_sh69))
print("Saved out/reports/sh69_meridian_comparison.html")


# -------------------------------------------------------------
# Report 5: Franklin Road Arterial Corridor
# -------------------------------------------------------------
print("Building Report 5: Franklin Road...")
fr_eb = df[df['sheet'] == 'Franklin EB']
fr_wb = df[df['sheet'] == 'Franklin WB']
fr_eb_nomid = df[df['sheet'] == 'Franklin EB - No Mid']
fr_wb_nomid = df[df['sheet'] == 'Franklin WB - No Mid']

heatmaps_fr_wb = build_heatmaps_for_sheet(fr_wb, "Franklin WB (Standard)", height=340)
heatmaps_fr_eb = build_heatmaps_for_sheet(fr_eb, "Franklin EB (Standard)", height=340)

fr_scatter_panels = [
    {
        'title': 'Franklin WB Standard (r = 0.800, MAE = 23s)',
        'df': fr_wb,
        'annotations': [
            {
                'x': 5.6, 'y': 5.3,
                'text': '<b>Near-Perfect Baseline Agreement</b><br>Google: 5.60m | INRIX: 5.33m<br>Mean Bias: -16 seconds (-0.27m)<br>MAE: 23 seconds (6.7% error)',
                'ax': -50, 'ay': -60,
                'arrowcolor': '#16a34a', 'bordercolor': '#16a34a'
            }
        ]
    },
    {
        'title': 'Franklin EB Standard (r = 0.787, MAE = 55s)',
        'df': fr_eb,
        'annotations': [
            {
                'x': 5.5, 'y': 4.6,
                'text': '<b>High Arterial Tracking</b><br>Google: 5.52m | INRIX: 4.60m<br>MAE: 55 seconds (16.2% error)<br>Missing 1 segment (9/10 stitched)',
                'ax': 50, 'ay': -50,
                'arrowcolor': '#2563eb', 'bordercolor': '#2563eb'
            }
        ]
    },
    {
        'title': 'Franklin WB No Mid (r = 0.683)',
        'df': fr_wb_nomid,
        'annotations': [
            {
                'x': 6.5, 'y': 4.3,
                'text': '<b>Missing Intermediate Segment</b><br>7 / 8 segments stitched.<br>Accounts for the 2.1 min delta.',
                'ax': 40, 'ay': 60,
                'arrowcolor': '#f59e0b', 'bordercolor': '#f59e0b'
            }
        ]
    },
    {
        'title': 'Franklin EB No Mid (r = 0.661)',
        'df': fr_eb_nomid,
        'annotations': [
            {
                'x': 4.1, 'y': 3.5,
                'text': '<b>Tight Arterial Spread</b><br>MAE: 32 seconds (0.54m)<br>r = 0.661 across 3,003 bins.',
                'ax': -40, 'ay': -50,
                'arrowcolor': '#0284c7', 'bordercolor': '#0284c7'
            }
        ]
    }
]
scatter_fr = build_scatter_with_annotations(fr_scatter_panels, height=520)

diurnal_fr = build_diurnal_profiles([
    {
        'title': 'Franklin Road Westbound Diurnal Profile',
        'traces': [
            {'df': fr_wb[fr_wb['dow'].isin(['Tuesday', 'Wednesday', 'Thursday'])], 'label': 'Weekday', 'color_g': '#2563eb', 'color_i': '#f59e0b'},
            {'df': fr_wb[fr_wb['dow'].isin(['Saturday', 'Sunday'])], 'label': 'Weekend', 'color_g': '#16a34a', 'color_i': '#0891b2'}
        ]
    },
    {
        'title': 'Franklin Road Eastbound Diurnal Profile',
        'traces': [
            {'df': fr_eb[fr_eb['dow'].isin(['Tuesday', 'Wednesday', 'Thursday'])], 'label': 'Weekday', 'color_g': '#dc2626', 'color_i': '#ea580c'},
            {'df': fr_eb[fr_eb['dow'].isin(['Saturday', 'Sunday'])], 'label': 'Weekend', 'color_g': '#9333ea', 'color_i': '#db2777'}
        ]
    }
], height=420)

content_fr = f"""
  <div class="hero-header">
    <div class="hero-title">
      <span>Franklin Road Arterial Validation</span>
      <span class="tag-badge tag-blue">Nampa-Caldwell Urban Arterial</span>
      <span class="tag-badge tag-green">Part 2 Segments Included</span>
    </div>
    <div class="hero-subtitle">
      Multi-direction and multi-segment evaluation of Franklin Road between Nampa and Caldwell. Compares standard alignments against "No Mid" variants across 12,009 matched 15-minute intervals.
    </div>
    <div class="badges-row">
      <span class="tag-badge tag-purple">Distance: 3.45 mi WB / 2.99 mi EB</span>
      <span class="tag-badge tag-green">Segments: 8/9 WB, 9/10 EB</span>
      <span class="tag-badge tag-blue">Sample Size: 12,009 Matched Bins</span>
      <span class="tag-badge tag-amber">Sampling: Tue-Thu & Sat-Sun</span>
    </div>
  </div>

  <div class="kpi-grid">
    <div class="kpi-card">
      <div class="kpi-title">Franklin WB MAE</div>
      <div class="kpi-val" style="color: #10b981;">23 sec</div>
      <div class="kpi-sub">0.38 min (6.7% error)</div>
    </div>
    <div class="kpi-card">
      <div class="kpi-title">Franklin WB Bias</div>
      <div class="kpi-val" style="color: #10b981;">-16 sec</div>
      <div class="kpi-sub">Google 5.60m vs INRIX 5.33m</div>
    </div>
    <div class="kpi-card">
      <div class="kpi-title">Franklin WB r</div>
      <div class="kpi-val" style="color: #2563eb;">0.800</div>
      <div class="kpi-sub">R² = 0.640 (N=3,005)</div>
    </div>
    <div class="kpi-card">
      <div class="kpi-title">Franklin EB MAE</div>
      <div class="kpi-val">55 sec</div>
      <div class="kpi-sub">0.92 min (16.2% error)</div>
    </div>
    <div class="kpi-card">
      <div class="kpi-title">Franklin EB r</div>
      <div class="kpi-val" style="color: #0284c7;">0.787</div>
      <div class="kpi-sub">R² = 0.619 (N=3,005)</div>
    </div>
    <div class="kpi-card">
      <div class="kpi-title">Total Samples</div>
      <div class="kpi-val">12,009</div>
      <div class="kpi-sub">Across 4 Sheet Variants</div>
    </div>
  </div>

  <!-- Heatmaps -->
  <div class="chart-card">
    <div class="chart-header">
      <div>
        <div class="chart-title">1. Congestion Heatmaps: Franklin Road (Standard Alignments)</div>
        <div class="chart-desc">Heatmaps of travel time across weekdays and weekends, showing peak travel times on Friday afternoon and Saturday midday.</div>
      </div>
    </div>
    <div class="chart-body">
      <h4 style="margin-bottom: 10px; color: #1e293b;">A. Franklin Road Westbound (3.45 Miles)</h4>
      {heatmaps_fr_wb}
      <h4 style="margin: 20px 0 10px; color: #1e293b;">B. Franklin Road Eastbound (2.99 Miles)</h4>
      {heatmaps_fr_eb}
    </div>
  </div>

  <!-- Scatter & R2 Diagnostic -->
  <div class="chart-card">
    <div class="chart-header">
      <div>
        <div class="chart-title">2. Scatter Comparisons: Standard vs. No-Mid Variants</div>
        <div class="chart-desc">Diagnostic scatter plots illustrating the 23-second agreement on Franklin WB and the effect of missing segments on the No-Mid variants.</div>
      </div>
    </div>
    <div class="chart-body">
      {scatter_fr}
    </div>
  </div>

  <!-- Diurnal Curves -->
  <div class="chart-card">
    <div class="chart-header">
      <div>
        <div class="chart-title">3. Diurnal Profiles: Weekday vs. Weekend Commute Profiles</div>
        <div class="chart-desc">Comparison of hourly travel time tracking between Google Maps (solid) and INRIX (dashed) across weekdays (Tue-Thu) and weekends (Sat-Sun).</div>
      </div>
    </div>
    <div class="chart-body">
      {diurnal_fr}
    </div>
  </div>

  <!-- Engineering Write-up -->
  <div class="writeup-section">
    <h2>Engineering Analysis & Findings: Franklin Road Corridor</h2>

    <h3>Corridor Profile & Missing Segment Additions</h3>
    <p>
      Franklin Road is an essential multi-lane urban arterial linking Nampa and Caldwell parallel to I-84. In previous validation runs, Franklin Road segments were missing from Part 1 exports. With Part 2 integrated, we successfully stitched 8 of 9 segments Westbound (3.45 miles) and 9 of 10 segments Eastbound (2.99 miles), providing the necessary data density across 12,009 matched intervals.
    </p>

    <div class="alert-box alert-success">
      <span class="alert-title">Franklin Westbound: Near-Perfect Agreement (23 Seconds MAE)</span>
      On <code>Franklin WB</code> across 3,005 matched intervals:
      <ul>
        <li><strong>Google Maps Mean:</strong> 5.60 minutes</li>
        <li><strong>INRIX XD Mean:</strong> 5.33 minutes</li>
        <li><strong>Mean Bias:</strong> <strong>-0.27 minutes (-16 seconds)</strong></li>
        <li><strong>Mean Absolute Error:</strong> <strong>0.38 minutes (23 seconds, 6.7% MAPE)</strong></li>
        <li><strong>Correlation:</strong> <strong>$r = 0.800$ ($R^2 = 0.640$)</strong></li>
      </ul>
      This demonstrates that INRIX XD tracks urban arterial signal delays with error margins well below the duration of a single yellow light clearance interval.
    </div>

    <h3>Impact of Segment Gaps in "No Mid" Variants</h3>
    <p>
      On the "No Mid" variants (which excluded central segments):
    </p>
    <ul>
      <li><code>Franklin EB - No Mid</code> (7/8 segments): Google Mean 4.06 min vs INRIX 3.54 min (MAE: 32 seconds, $r = 0.661$). Agreement remains strong.</li>
      <li><code>Franklin WB - No Mid</code> (7/8 segments): Google Mean 6.46 min vs INRIX 4.34 min (Bias: -2.12 min, $r = 0.683$). The 2.1-minute bias corresponds exactly to the physical travel time of the single omitted central segment.</li>
    </ul>

    <div class="alert-box alert-info">
      <span class="alert-title">Interchangeability Verdict: Fully Interchangeable for Urban Arterial Planning</span>
      <strong>Verdict: Highly Interchangeable (Grade A-).</strong><br>
      INRIX XD matches Google Maps arterial travel times to within 16 to 55 seconds across all hours of the day. It is directly applicable for corridor signal timing performance reviews, travel time reliability studies, and regional arterial delay models.
    </div>
  </div>
"""

with open(os.path.join(OUTPUT_DIR, "franklin_rd_comparison.html"), "w") as f:
    f.write(wrap_page("Franklin Road", "franklin_rd_comparison.html", content_fr))
print("Saved out/reports/franklin_rd_comparison.html")


# -------------------------------------------------------------
# Report 6: Banks-Lowman Highway (Garden Valley-HSB)
# -------------------------------------------------------------
print("Building Report 6: Banks-Lowman Highway...")
gv_df = df[df['sheet'] == 'Garden Valley-HSB']

heatmaps_gv = build_heatmaps_for_sheet(gv_df, "Garden Valley to HSB (SH-17)", height=340)

gv_scatter_panels = [
    {
        'title': 'Garden Valley to HSB (25.87 mi, 35 segs)',
        'df': gv_df,
        'annotations': [
            {
                'x': 30.0, 'y': 32.5,
                'text': '<b>Strong Overall Tracking (r = 0.873)</b><br>Google: 30.14m | INRIX: 32.66m<br>Bias: +2.52 min (8.9% MAPE)<br>Reflects conservative INRIX speed limits<br>along winding Payette River canyon curves.',
                'ax': -50, 'ay': -60,
                'arrowcolor': '#16a34a', 'bordercolor': '#16a34a'
            },
            {
                'x': 36.0, 'y': 36.0,
                'text': '<b>Sunday Recreation Peak Surges</b><br>Both sources track Sunday surges<br>identically from 30m up to 37m.',
                'ax': 40, 'ay': 50,
                'arrowcolor': '#dc2626', 'bordercolor': '#dc2626'
            }
        ]
    }
]
scatter_gv = build_scatter_with_annotations(gv_scatter_panels, height=500)

diurnal_gv = build_diurnal_profiles([
    {
        'title': 'Garden Valley to HSB Diurnal Profile',
        'traces': [
            {'df': gv_df[gv_df['dow'] == 'Sunday'], 'label': 'Sunday Surge', 'color_g': '#dc2626', 'color_i': '#ea580c'},
            {'df': gv_df[gv_df['dow'] != 'Sunday'], 'label': 'Fri/Sat/Mon Baseline', 'color_g': '#2563eb', 'color_i': '#0891b2'}
        ]
    }
], height=400)

content_gv = f"""
  <div class="hero-header">
    <div class="hero-title">
      <span>Banks-Lowman Highway (SH-17) Validation</span>
      <span class="tag-badge tag-blue">State Highway 17</span>
      <span class="tag-badge tag-green">100% Segments Stitched</span>
    </div>
    <div class="hero-subtitle">
      Evaluation of 25.87 miles of rural two-lane mountain highway along the South Fork Payette River between Garden Valley and Horseshoe Bend (`Garden Valley-HSB`).
    </div>
    <div class="badges-row">
      <span class="tag-badge tag-purple">Distance: 25.87 Miles</span>
      <span class="tag-badge tag-green">Segments: 35 / 35 XD Segments (100%)</span>
      <span class="tag-badge tag-blue">Sample Size: 2,937 Matched Intervals</span>
      <span class="tag-badge tag-amber">Sampling: Fri, Sat, Sun, Mon (06:00 - 16:45)</span>
    </div>
  </div>

  <div class="kpi-grid">
    <div class="kpi-card">
      <div class="kpi-title">Pearson Correlation</div>
      <div class="kpi-val" style="color: #10b981;">0.873</div>
      <div class="kpi-sub">R² = 0.762 (High Agreement)</div>
    </div>
    <div class="kpi-card">
      <div class="kpi-title">Mean Absolute Error</div>
      <div class="kpi-val" style="color: #2563eb;">2.66 m</div>
      <div class="kpi-sub">MAPE: 8.9% error</div>
    </div>
    <div class="kpi-card">
      <div class="kpi-title">Mean Bias</div>
      <div class="kpi-val" style="color: #0284c7;">+2.52 m</div>
      <div class="kpi-sub">Google 30.1m vs INRIX 32.7m</div>
    </div>
    <div class="kpi-card">
      <div class="kpi-title">Stitching Completeness</div>
      <div class="kpi-val" style="color: #10b981;">100%</div>
      <div class="kpi-sub">35 / 35 XD Segments</div>
    </div>
    <div class="kpi-card">
      <div class="kpi-title">Matched Intervals</div>
      <div class="kpi-val">2,937</div>
      <div class="kpi-sub">Jan 1 - Aug 24, 2026</div>
    </div>
  </div>

  <!-- Heatmaps -->
  <div class="chart-card">
    <div class="chart-header">
      <div>
        <div class="chart-title">1. Congestion Heatmaps: Garden Valley to Horseshoe Bend</div>
        <div class="chart-desc">Heatmaps of travel time across weekend and weekday hours, highlighting the Sunday afternoon return surge.</div>
      </div>
    </div>
    <div class="chart-body">
      {heatmaps_gv}
    </div>
  </div>

  <!-- Scatter & R2 Diagnostic -->
  <div class="chart-card">
    <div class="chart-header">
      <div>
        <div class="chart-title">2. Scatter Analysis: Rural Mountain Highway Characteristics</div>
        <div class="chart-desc">Strong linear tracking (r = 0.873) with a slight, consistent 2.5-minute conservative offset in INRIX speeds.</div>
      </div>
    </div>
    <div class="chart-body">
      {scatter_gv}
    </div>
  </div>

  <!-- Diurnal Curves -->
  <div class="chart-card">
    <div class="chart-header">
      <div>
        <div class="chart-title">3. Diurnal Profiles: Sunday Surge vs. Baseline Days</div>
        <div class="chart-desc">Comparison of Sunday recreational surges (surging from 29 min at 8 AM to 34-35 min at 1 PM) against baseline days.</div>
      </div>
    </div>
    <div class="chart-body">
      {diurnal_gv}
    </div>
  </div>

  <!-- Engineering Write-up -->
  <div class="writeup-section">
    <h2>Engineering Analysis & Findings: Banks-Lowman Highway</h2>

    <h3>Corridor Characteristics</h3>
    <p>
      The Banks-Lowman Highway (SH-17 / Garden Valley to Horseshoe Bend) is a scenic, winding two-lane rural corridor following the South Fork of the Payette River. It is a major recreational artery providing access to whitewater rafting, hot springs, and camping in the Boise National Forest. All 35 XD segments are stitched, achieving 100% completeness over 25.87 miles.
    </p>

    <div class="alert-box alert-success">
      <span class="alert-title">Strong Rural Correlation: $r = 0.873$ ($R^2 = 76.2%$)</span>
      Across 2,937 matched intervals, INRIX tracks Google Maps travel times with high fidelity. Both sources detect the Sunday recreational traffic surge starting at 10:30 AM, peaking at 13:00 - 14:00 (travel times rise from 29 to 34–35 minutes), and clearing by 16:30.
    </div>

    <h3>Understanding the +2.5 Minute Bias</h3>
    <p>
      INRIX reports a mean travel time of 32.66 minutes compared to Google's 30.14 minutes (Bias = +2.52 min, MAPE = 8.9%). This corresponds to a speed difference of approximately 47.5 mph in INRIX versus 51.5 mph in Google Maps:
    </p>
    <ul>
      <li>SH-17 features numerous sharp horizontal curves with advisory speed limits (35–45 mph).</li>
      <li>Local motorists frequently drive 5 to 10 mph above advisory speeds during dry summer conditions, which Google's vehicle probes register directly.</li>
      <li>INRIX reference speeds and XD maps incorporate conservative geometric speed penalization along tight canyon radii, resulting in a slightly higher, safer travel time estimate.</li>
    </ul>

    <div class="alert-box alert-info">
      <span class="alert-title">Interchangeability Verdict: Fully Interchangeable for Rural Studies</span>
      <strong>Verdict: Interchangeable (Grade A).</strong><br>
      INRIX XD provides dependable travel time monitoring for rural mountain corridors. The 8.9% conservative bias is well within FHWA standards for rural highway performance monitoring.
    </div>
  </div>
"""

with open(os.path.join(OUTPUT_DIR, "garden_valley_comparison.html"), "w") as f:
    f.write(wrap_page("Garden Valley to HSB", "garden_valley_comparison.html", content_gv))
print("Saved out/reports/garden_valley_comparison.html")


# -------------------------------------------------------------
# Report 7: Master Executive Portal (index.html)
# -------------------------------------------------------------
print("Building Report 7: Master Executive Portal (index.html)...")

# Build Bubble Chart: MAE vs Pearson r
bubble_fig = go.Figure()

scorecard_rows = []
for idx, row in summary_df.iterrows():
    s = row['Sheet']
    c = row['Corridor']
    r = row['Pearson_r']
    mae = row['MAE_min']
    bias = row['Mean_Bias_min']
    g_m = row['Google_Mean_TT_min']
    i_m = row['INRIX_Mean_TT_min']
    n = row['Matched_Bins']
    mi = row['Miles']
    comp = row['Fully_Complete']

    scorecard_rows.append((s, mi, comp, n, g_m, i_m, bias, mae, r, r**2))

# Scatter bubble
bubble_fig.add_trace(go.Scatter(
    x=[r[7] for r in scorecard_rows], # MAE
    y=[r[8] for r in scorecard_rows], # r
    mode='markers+text',
    marker=dict(
        size=[max(12, min(50, r[1] * 0.9)) for r in scorecard_rows],
        color=[r[8] for r in scorecard_rows],
        colorscale='Viridis',
        showscale=True,
        colorbar=dict(title="Pearson r", thickness=14)
    ),
    text=[r[0] for r in scorecard_rows],
    textposition="top center",
    hovertemplate="<b>%{text}</b><br>MAE: %{x:.2f} min<br>Pearson r: %{y:.3f}<extra></extra>"
))

bubble_fig.update_layout(
    height=440,
    margin=dict(l=60, r=40, t=40, b=50),
    template="plotly_white",
    xaxis=dict(title="Mean Absolute Error - MAE (minutes)", gridcolor="#f1f5f9"),
    yaxis=dict(title="Pearson Correlation Coefficient (r)", gridcolor="#f1f5f9", range=[0.35, 1.0])
)
bubble_html = bubble_fig.to_html(full_html=False, include_plotlyjs=False, config=dict(responsive=True, displayModeBar=True))

# Scorecard HTML
scorecard_table_html = """
<table class="data-table">
  <thead>
    <tr>
      <th>Corridor / Sheet</th>
      <th>Route Type</th>
      <th>Miles</th>
      <th>Stitching</th>
      <th>Matched Bins</th>
      <th>Google Mean</th>
      <th>INRIX Mean</th>
      <th>Mean Bias</th>
      <th>MAE</th>
      <th>Pearson r (R²)</th>
      <th>Report Link</th>
    </tr>
  </thead>
  <tbody>
"""

route_types = {
    'Cascade-HSB': 'Mountain Highway (SH-55)',
    'HSB-Cascade': 'Mountain Highway (SH-55)',
    'Garden Valley-HSB': 'Rural Highway (SH-17)',
    'VSL SB AM': 'Urban Arterial (SH-55)',
    'VSL NB AM': 'Urban Arterial (SH-55)',
    'VSL NB PM': 'Urban Arterial (SH-55)',
    'VSL SB PM': 'Urban Arterial (SH-55)',
    'SH-69 NB': 'Commuter Arterial (SH-69)',
    'SH-69 SB': 'Commuter Arterial (SH-69)',
    'Franklin WB': 'Urban Arterial (Franklin)',
    'Franklin EB': 'Urban Arterial (Franklin)',
    'Franklin EB - No Mid': 'Urban Arterial (Franklin)',
    'Franklin WB - No Mid': 'Urban Arterial (Franklin)',
    'Eagle Rd NB': 'Full Corridor (SH-55)',
    'Eagle Rd SB': 'Full Corridor (SH-55)'
}

report_links = {
    'Cascade-HSB': ('sh55_mountain_comparison.html', 'SH-55 Mountain'),
    'HSB-Cascade': ('sh55_mountain_comparison.html', 'SH-55 Mountain'),
    'Garden Valley-HSB': ('garden_valley_comparison.html', 'Garden Valley'),
    'VSL SB AM': ('eagle_rd_vsl_comparison.html', 'Eagle VSL'),
    'VSL NB AM': ('eagle_rd_vsl_comparison.html', 'Eagle VSL'),
    'VSL NB PM': ('eagle_rd_vsl_comparison.html', 'Eagle VSL'),
    'VSL SB PM': ('eagle_rd_vsl_comparison.html', 'Eagle VSL'),
    'SH-69 NB': ('sh69_meridian_comparison.html', 'SH-69 Meridian'),
    'SH-69 SB': ('sh69_meridian_comparison.html', 'SH-69 Meridian'),
    'Franklin WB': ('franklin_rd_comparison.html', 'Franklin Rd'),
    'Franklin EB': ('franklin_rd_comparison.html', 'Franklin Rd'),
    'Franklin EB - No Mid': ('franklin_rd_comparison.html', 'Franklin Rd'),
    'Franklin WB - No Mid': ('franklin_rd_comparison.html', 'Franklin Rd'),
    'Eagle Rd NB': ('eagle_rd_full_comparison.html', 'Eagle Rd Full'),
    'Eagle Rd SB': ('eagle_rd_full_comparison.html', 'Eagle Rd Full')
}

for row in scorecard_rows:
    s, mi, comp, n, g_m, i_m, bias, mae, r, r2 = row
    rtype = route_types.get(s, 'Highway')
    url, url_lbl = report_links.get(s, ('#', 'View'))
    comp_badge = '<span class="tag-badge tag-green">100% Complete</span>' if comp else '<span class="tag-badge tag-amber">Partial</span>'
    scorecard_table_html += f"""
    <tr>
      <td><strong>{s}</strong></td>
      <td>{rtype}</td>
      <td>{mi:.1f} mi</td>
      <td>{comp_badge}</td>
      <td>{n:,}</td>
      <td>{g_m:.2f} m</td>
      <td>{i_m:.2f} m</td>
      <td style="color: {'#16a34a' if abs(bias) < 1.0 else '#dc2626'};"><strong>{bias:+.2f} m</strong></td>
      <td><strong>{mae:.2f} m ({mae*60:.0f}s)</strong></td>
      <td><strong>{r:.3f}</strong> ({r2:.2f})</td>
      <td><a href="{url}" style="color: #2563eb; font-weight: 600; text-decoration: none;">View Report &rarr;</a></td>
    </tr>
    """

scorecard_table_html += "</tbody></table>"

content_index = f"""
  <div class="hero-header">
    <div class="hero-title">
      <span>ITD District 3: Travel Time Validation Portal</span>
      <span class="tag-badge tag-blue">2026 Full Study Dataset</span>
      <span class="tag-badge tag-green">28,340 Matched Observations</span>
    </div>
    <div class="hero-subtitle">
      Comprehensive corridor-by-corridor comparison of INRIX XD segment-stitched travel times against Google Maps travel times across Idaho Transportation Department (ITD) District 3 highways.
    </div>
    <div class="badges-row">
      <span class="tag-badge tag-purple">Study Period: Jan 1 - Aug 24, 2026</span>
      <span class="tag-badge tag-green">Corridors Analyzed: 6 Corridor Systems</span>
      <span class="tag-badge tag-blue">Total XD Segments: 241 Segments</span>
      <span class="tag-badge tag-amber">Data Resolution: 15-Minute Intervals</span>
    </div>
  </div>

  <div class="kpi-grid">
    <div class="kpi-card">
      <div class="kpi-title">Total Matched Bins</div>
      <div class="kpi-val" style="color: #2563eb;">28,340</div>
      <div class="kpi-sub">15-minute intervals verified</div>
    </div>
    <div class="kpi-card">
      <div class="kpi-title">Cascade-HSB Bias</div>
      <div class="kpi-val" style="color: #10b981;">-9 sec</div>
      <div class="kpi-sub">-0.15 min over 50.8 miles</div>
    </div>
    <div class="kpi-card">
      <div class="kpi-title">Eagle VSL SB AM MAE</div>
      <div class="kpi-val" style="color: #10b981;">18 sec</div>
      <div class="kpi-sub">r = 0.933 (Near Identity)</div>
    </div>
    <div class="kpi-card">
      <div class="kpi-title">Franklin WB MAE</div>
      <div class="kpi-val" style="color: #10b981;">23 sec</div>
      <div class="kpi-sub">MAE = 0.38 min (6.7% error)</div>
    </div>
    <div class="kpi-card">
      <div class="kpi-title">Max Full Correlation</div>
      <div class="kpi-val" style="color: #2563eb;">0.945</div>
      <div class="kpi-sub">Eagle Rd SB (R² = 0.892)</div>
    </div>
    <div class="kpi-card">
      <div class="kpi-title">Stitched Completeness</div>
      <div class="kpi-val" style="color: #10b981;">100%</div>
      <div class="kpi-sub">SH-55 & SH-69 100% Complete</div>
    </div>
  </div>

  <!-- Executive Summary -->
  <div class="writeup-section">
    <h2>Executive Synthesis: Are INRIX and Google Interchangeable?</h2>
    
    <div class="alert-box alert-success">
      <span class="alert-title">Core Finding: High Agreement & Practical Interchangeability</span>
      Across virtually all corridors, INRIX XD probe data and Google Maps travel times exhibit <strong>extraordinarily high agreement</strong>. For regional travel time monitoring, corridor reliability reporting, and highway delay studies, the two data sources are <strong>functionally interchangeable</strong> for the vast majority of ITD transportation engineering analyses.
    </div>

    <p>
      An initial inspection of statistical summary tables often leads to concern when certain corridors show modest $R^2$ values (e.g., $R^2 = 0.432$ on Cascade-HSB, or $R^2 = 0.167$ on VSL SB PM). However, our diagnostic analysis proves that <strong>$R^2$ is not the complete picture and can be deeply deceptive</strong>:
    </p>

    <div class="two-col-grid" style="margin: 20px 0;">
      <div style="background: #f8fafc; padding: 18px; border-radius: 8px; border: 1px solid #e2e8f0;">
        <h4 style="color: #1e3a8a; margin-bottom: 8px;">1. The "Flat Variance" Paradox</h4>
        <p style="font-size: 0.9rem; margin-bottom: 0;">
          On free-flowing mountain passes (SH-55 on weekdays) and off-peak urban arterials (VSL SB PM), traffic flows smoothly at the speed limit. Travel time variance is virtually zero (&sigma; &approx; 20-60 seconds). Mathematically, R² measures explained variance; when there is no variance to explain, R² &rarr; 0 even when the actual prediction error is only 18 to 48 seconds! When real congestion occurs (Sundays on SH-55), R² instantly surges to <strong>0.910</strong>.
        </p>
      </div>

      <div style="background: #f8fafc; padding: 18px; border-radius: 8px; border: 1px solid #e2e8f0;">
        <h4 style="color: #1e3a8a; margin-bottom: 8px;">2. Spatial Boundary Offsets</h4>
        <p style="font-size: 0.9rem; margin-bottom: 0;">
          On Eagle Road Full ($r = 0.945$) and SH-69 SB ($r = 0.686$), mean travel times differ by 5 to 8 minutes. Our spatial analysis proves this is not data error, but <strong>query geometry mismatch</strong>: Google queries extended south of I-84 into non-state arterial segments and captured I-84 off-ramp signal queues before entering the state highway mainline.
        </p>
      </div>
    </div>
  </div>

  <!-- Cross-Corridor Chart -->
  <div class="chart-card">
    <div class="chart-header">
      <div>
        <div class="chart-title">Corridor Performance Map: Accuracy (MAE) vs. Correlation (r)</div>
        <div class="chart-desc">Bubble size corresponds to corridor length in miles. Corridors in the upper-left quadrant exhibit both low error and high correlation.</div>
      </div>
    </div>
    <div class="chart-body">
      {bubble_html}
    </div>
  </div>

  <!-- Master Scorecard Table -->
  <div class="chart-card">
    <div class="chart-header">
      <div>
        <div class="chart-title">Master Corridor Performance Scorecard</div>
        <div class="chart-desc">Comprehensive metrics for all 15 corridor alignments evaluated across 2026. Click "View Report" for deep-dive analysis.</div>
      </div>
    </div>
    <div class="chart-body">
      <div class="data-table-wrapper">
        {scorecard_table_html}
      </div>
    </div>
  </div>

  <!-- Corridor Cards Navigation -->
  <div class="writeup-section">
    <h2>Explore Individual Corridor Reports</h2>
    <div class="two-col-grid" style="margin-top: 20px;">
      
      <div class="kpi-card" style="border-top-color: #2563eb;">
        <h3 style="margin-top: 0; font-size: 1.2rem;"><a href="sh55_mountain_comparison.html" style="color: #1e3a8a; text-decoration: none;">1. SH-55 Mountain Corridor (Horseshoe Bend to Cascade) &rarr;</a></h3>
        <p style="font-size: 0.9rem; color: #475569; margin: 8px 0 12px;">
          50.8 miles, 136 XD segments (100% stitched). Evaluates sub-minute baseline agreement (-9s bias), Sunday recreation waves (R² = 0.910), and why weekday free-flow collapses R².
        </p>
        <span class="tag-badge tag-blue">50.8 Miles</span>
        <span class="tag-badge tag-green">MAE: 1.68 min (2.5%)</span>
        <span class="tag-badge tag-purple">3,288 Bins</span>
      </div>

      <div class="kpi-card" style="border-top-color: #10b981;">
        <h3 style="margin-top: 0; font-size: 1.2rem;"><a href="eagle_rd_vsl_comparison.html" style="color: #1e3a8a; text-decoration: none;">2. Eagle Road VSL Pilot Section (SH-55) &rarr;</a></h3>
        <p style="font-size: 0.9rem; color: #475569; margin: 8px 0 12px;">
          3.64 miles between Fairview and State St. Combines all 4 commute periods: SB AM (MAE 18s, r = 0.933), NB AM (MAE 30s), NB PM peak compression, and SB PM signal phase noise.
        </p>
        <span class="tag-badge tag-blue">3.64 Miles</span>
        <span class="tag-badge tag-green">AM MAE: 18 sec</span>
        <span class="tag-badge tag-purple">2,360 Bins</span>
      </div>

      <div class="kpi-card" style="border-top-color: #f59e0b;">
        <h3 style="margin-top: 0; font-size: 1.2rem;"><a href="eagle_rd_full_comparison.html" style="color: #1e3a8a; text-decoration: none;">3. Eagle Road Full Corridor (SH-55 Alignment) &rarr;</a></h3>
        <p style="font-size: 0.9rem; color: #475569; margin: 8px 0 12px;">
          7.08 miles of official SH-55 from I-84 to SH-44. Demonstrates highest correlation in Idaho (r = 0.945 SB, r = 0.930 NB) and diagnoses spatial query boundary offsets.
        </p>
        <span class="tag-badge tag-blue">7.08 Miles</span>
        <span class="tag-badge tag-green">r = 0.945 (R² = 89%)</span>
        <span class="tag-badge tag-purple">3,874 Bins</span>
      </div>

      <div class="kpi-card" style="border-top-color: #0284c7;">
        <h3 style="margin-top: 0; font-size: 1.2rem;"><a href="sh69_meridian_comparison.html" style="color: #1e3a8a; text-decoration: none;">4. SH-69 Meridian Road Commuter Corridor &rarr;</a></h3>
        <p style="font-size: 0.9rem; color: #475569; margin: 8px 0 12px;">
          7.17 miles connecting Kuna to I-84. Contrasts tight Northbound morning commuter tracking (r = 0.889, MAE 1.36m) against Southbound I-84 Exit 44 off-ramp queuing delay.
        </p>
        <span class="tag-badge tag-blue">7.17 Miles</span>
        <span class="tag-badge tag-green">NB r = 0.889</span>
        <span class="tag-badge tag-purple">3,872 Bins</span>
      </div>

      <div class="kpi-card" style="border-top-color: #8b5cf6;">
        <h3 style="margin-top: 0; font-size: 1.2rem;"><a href="franklin_rd_comparison.html" style="color: #1e3a8a; text-decoration: none;">5. Franklin Road Arterial Corridor &rarr;</a></h3>
        <p style="font-size: 0.9rem; color: #475569; margin: 8px 0 12px;">
          Nampa to Caldwell urban arterial across 12,009 matched intervals. Franklin WB demonstrates near-identity with Google Maps (MAE: 23 seconds, Bias: -16s, r = 0.800).
        </p>
        <span class="tag-badge tag-blue">3.45 Miles</span>
        <span class="tag-badge tag-green">WB MAE: 23 sec</span>
        <span class="tag-badge tag-purple">12,009 Bins</span>
      </div>

      <div class="kpi-card" style="border-top-color: #059669;">
        <h3 style="margin-top: 0; font-size: 1.2rem;"><a href="garden_valley_comparison.html" style="color: #1e3a8a; text-decoration: none;">6. Banks-Lowman Highway (Garden Valley to HSB) &rarr;</a></h3>
        <p style="font-size: 0.9rem; color: #475569; margin: 8px 0 12px;">
          25.87 miles along the South Fork Payette River. Strong linear tracking (r = 0.873), Sunday surge detection, and conservative geometric speed calibration.
        </p>
        <span class="tag-badge tag-blue">25.87 Miles</span>
        <span class="tag-badge tag-green">r = 0.873</span>
        <span class="tag-badge tag-purple">2,937 Bins</span>
      </div>

    </div>
  </div>
"""

with open(os.path.join(OUTPUT_DIR, "index.html"), "w") as f:
    f.write(wrap_page("Executive Portal", "index.html", content_index))
print("Saved out/reports/index.html")

print("\n=== ALL REPORTS SUCCESSFULLY GENERATED ===")
print("Generated files in", OUTPUT_DIR, ":")
for fname in sorted(os.listdir(OUTPUT_DIR)):
    fpath = os.path.join(OUTPUT_DIR, fname)
    size_kb = os.path.getsize(fpath) / 1024
    print(f"  - {fname} ({size_kb:.1f} KB)")

"""Plotly figures for the INRIX-vs-reference validation report.  (ROADMAP Item 30)

The plotting half of the split the retired ``generate_corridor_html_reports.py``
never made: every builder here takes a frame the compute core already produced
(``inrix_tools.agreement`` / ``inrix_tools.reference``) and **computes nothing** —
no MAE, no bias, no ``np.polyfit`` slope fitted inside a figure loop
(``legacy/generate_corridor_html_reports.py:504-549``). Statistics arrive as
columns; the builders place them.

That is why the annotation helpers take a *row of the summary frame*: the number
in a figure's stat box is the same number the scorecard prints, because it is
literally the same value, not a second calculation that happens to agree today.

Visual system (one system across the report, per the dataviz skill): categorical
slots 1 and 2 — blue for INRIX, orange for the reference — validated as a pair for
CVD separation and contrast on the light surface; a blue→gray→red diverging ramp
with a **gray** midpoint for signed bias; the fixed status palette for gate flags,
always paired with a text marker so a flag is never colour alone.
"""
from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go

from inrix_tools.agreement import DIFF_COL, INRIX_COL, MEAN_COL, REF_COL
from inrix_tools.io import DATETIME_COL
from inrix_tools.reference import ROUTE_COL, TT_COL

from .figures import TEMPLATE, _blank

# Categorical slots 1 and 2 (validated pair: CVD ΔE 24.7, normal-vision 33.6,
# both ≥ 3:1 on the light surface).
INRIX_COLOR = "#2a78d6"
REF_COLOR = "#eb6834"
# Ink, not series colour — reference lines, 1:1 lines, the naive interval.
MUTED = "#52514e"
GRID = "#e5e4e0"
# Status palette (fixed; never reused for a series).
STATUS_GOOD = "#0ca30c"
STATUS_WARNING = "#fab219"
STATUS_CRITICAL = "#d03b3b"
# Diverging: blue ↔ red with a neutral gray midpoint (never a hue at zero).
DIVERGING = [[0.0, "#184f95"], [0.25, "#6da7ec"], [0.5, "#f0efec"],
             [0.75, "#ec835a"], [1.0, "#d03b3b"]]

INRIX_LABEL = "INRIX XD"
REF_LABEL = "Reference"


def _fmt(value, spec: str = ".2f", dash: str = "—") -> str:
    """Format a number, or ``dash`` when it is missing — a report never prints
    ``nan``."""
    try:
        if value is None or pd.isna(value):
            return dash
        return format(float(value), spec)
    except (TypeError, ValueError):
        return dash


def _one_route(frame: pd.DataFrame, route: str | None, key: str) -> pd.DataFrame:
    """The rows for one route (or the frame unchanged when ``route`` is None)."""
    if route is None or key not in frame.columns:
        return frame
    return frame[frame[key] == route]


def stat_box(row) -> str:
    """The per-corridor stat box, as HTML, **read off the summary row**.

    Order is deliberate and matches the scorecard: the two effects a single bias
    fuses first — the **delay slope** and the **free-flow level gap**, each with
    its day-blocked interval (ROADMAP Item 32) — then the bias itself, then the
    delay ratio as the secondary statistic it now is, and correlation last (G6 —
    a route was once promoted on r = 0.945 while carrying a 5.49-minute bias).
    """
    if row is None:
        return ""
    return (f"<b>Delay slope:</b> {_fmt(row.get('delay_slope'))} "
            f"[{_fmt(row.get('delay_slope_ci_low'))}, {_fmt(row.get('delay_slope_ci_high'))}]"
            f"<br><b>Free-flow gap:</b> {_fmt(row.get('free_flow_gap'), '+.2f')} min"
            f"<br><b>Bias:</b> {_fmt(row.get('bias'), '+.2f')} min "
            f"[{_fmt(row.get('bias_ci_low'), '+.2f')}, {_fmt(row.get('bias_ci_high'), '+.2f')}]"
            f"<br><b>MAE:</b> {_fmt(row.get('mae'))} min"
            f"<br><b>Delay ratio:</b> {_fmt(row.get('delay_ratio'))}"
            f"<br><b>SD ratio:</b> {_fmt(row.get('sd_ratio'))}"
            f"<br><b>n:</b> {int(row.get('n_bins', 0)):,} bins / {int(row.get('n_days', 0)):,} days"
            f"<br><b>r:</b> {_fmt(row.get('r'), '.3f')}")


# ---------------------------------------------------------------------------
# Scorecard figures (one row per route)
# ---------------------------------------------------------------------------
def bias_forest(summary: pd.DataFrame, key: str = ROUTE_COL, labels=None,
                flagged=(), height: int | None = None) -> go.Figure:
    """Bias with its confidence interval, one row per route — **the headline**.

    Two intervals are drawn: the day-blocked one the core reports as primary, and
    the per-bin ``*_naive`` one behind it in muted ink, so the width the
    autocorrelation costs is visible rather than asserted.

    Args:
        summary: ``agreement.compare`` output.
        labels: optional ``route -> display label`` mapping.
        flagged: routes whose reference data failed a gate. They keep their place
            (dropping them silently is what this report is fixing) and are marked
            with a ``⚠`` in the label **and** the critical status colour.
    """
    if summary is None or summary.empty:
        return _blank("No agreement summary to plot.")

    df = summary.sort_values("bias")
    names = [(labels or {}).get(r, r) for r in df[key]]
    flags = [r in set(flagged) for r in df[key]]
    names = [f"⚠ {n}" if f else n for n, f in zip(names, flags)]
    colors = [STATUS_CRITICAL if f else INRIX_COLOR for f in flags]

    fig = go.Figure()
    if {"bias_ci_low_naive", "bias_ci_high_naive"} <= set(df.columns):
        fig.add_trace(go.Scatter(
            x=df["bias"], y=names, mode="markers", name="Per-bin 95% CI (too narrow)",
            marker=dict(size=9, color=MUTED, opacity=0.35),
            error_x=dict(type="data", symmetric=False,
                         array=df["bias_ci_high_naive"] - df["bias"],
                         arrayminus=df["bias"] - df["bias_ci_low_naive"],
                         color=MUTED, thickness=6, width=0),
            hoverinfo="skip"))
    fig.add_trace(go.Scatter(
        x=df["bias"], y=names, mode="markers", name="Day-blocked 95% CI",
        marker=dict(size=11, color=colors,
                    line=dict(width=2, color="#fcfcfb")),
        error_x=dict(type="data", symmetric=False,
                     array=df["bias_ci_high"] - df["bias"],
                     arrayminus=df["bias"] - df["bias_ci_low"],
                     color=MUTED, thickness=2, width=6),
        customdata=df[["bias_ci_low", "bias_ci_high", "n_bins", "n_days"]].to_numpy(),
        hovertemplate=("<b>%{y}</b><br>Bias %{x:+.2f} min "
                       "[%{customdata[0]:+.2f}, %{customdata[1]:+.2f}]"
                       "<br>%{customdata[2]:,} bins over %{customdata[3]:,} days<extra></extra>")))
    fig.add_vline(x=0, line=dict(color=MUTED, width=1.5, dash="dash"))
    fig.update_layout(
        template=TEMPLATE, height=height or max(280, 42 * len(df) + 130),
        xaxis_title="Bias: INRIX − reference (minutes) — negative means INRIX reads faster",
        yaxis_title=None, margin=dict(l=10, r=20, t=40, b=50),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1))
    fig.update_xaxes(gridcolor=GRID, zeroline=False)
    fig.update_yaxes(gridcolor=GRID)
    return fig


def ratio_bars(summary: pd.DataFrame, key: str = ROUTE_COL, labels=None,
               height: int | None = None) -> go.Figure:
    """Delay ratio and SD ratio per route, against the 1.0 line.

    Both are INRIX ÷ reference. They sit together because the SD ratio is
    symmetric: when the two agree, a low delay ratio is not a regression-dilution
    artifact of the comparison (G1).
    """
    if summary is None or summary.empty:
        return _blank("No agreement summary to plot.")
    needed = {"delay_ratio", "sd_ratio"}
    if not needed <= set(summary.columns):
        return _blank("Summary is missing the ratio columns.")

    df = summary.sort_values("delay_ratio")
    names = [(labels or {}).get(r, r) for r in df[key]]

    fig = go.Figure()
    for col, label, color in (("delay_ratio", "Delay ratio", INRIX_COLOR),
                              ("sd_ratio", "SD ratio", REF_COLOR)):
        fig.add_trace(go.Bar(
            x=df[col], y=names, orientation="h", name=label, marker_color=color,
            text=[_fmt(v) for v in df[col]], textposition="outside", cliponaxis=False,
            hovertemplate=f"<b>%{{y}}</b><br>{label} %{{x:.2f}}<extra></extra>"))
    fig.add_vline(x=1.0, line=dict(color=MUTED, width=1.5, dash="dash"),
                  annotation_text="parity", annotation_position="top")
    fig.update_layout(
        template=TEMPLATE, height=height or max(320, 56 * len(df) + 130),
        barmode="group", bargap=0.25, bargroupgap=0.08,
        xaxis_title="INRIX ÷ reference", margin=dict(l=10, r=40, t=40, b=50),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1))
    fig.update_xaxes(gridcolor=GRID, range=[0, max(1.15, float(df[["delay_ratio", "sd_ratio"]].max().max()) * 1.2)])
    return fig


def slope_forest(summary: pd.DataFrame, key: str = ROUTE_COL, labels=None,
                 flagged=(), height: int | None = None) -> go.Figure:
    """The delay slope with its interval, one row per route — **the headline**.

    INRIX delay regressed on reference delay, each above its own free-flow
    (``agreement.delay_regression``). The reference line is **1.0**, not 0: a
    slope of 1 is the two sources crediting the same minute of delay, and a slope
    of 0.5 is INRIX crediting half of it. Like :func:`bias_forest`, the per-bin
    interval sits behind the day-blocked one in muted ink so the width the
    autocorrelation costs is visible rather than asserted.
    """
    if summary is None or summary.empty:
        return _blank("No agreement summary to plot.")
    if "delay_slope" not in summary.columns:
        return _blank("Summary is missing the delay-slope columns.")

    df = summary.sort_values("delay_slope")
    names = [(labels or {}).get(r, r) for r in df[key]]
    flags = [r in set(flagged) for r in df[key]]
    names = [f"⚠ {n}" if f else n for n, f in zip(names, flags)]
    colors = [STATUS_CRITICAL if f else INRIX_COLOR for f in flags]

    fig = go.Figure()
    if {"delay_slope_ci_low_naive", "delay_slope_ci_high_naive"} <= set(df.columns):
        fig.add_trace(go.Scatter(
            x=df["delay_slope"], y=names, mode="markers",
            name="Per-bin 95% CI (too narrow)",
            marker=dict(size=9, color=MUTED, opacity=0.35),
            error_x=dict(type="data", symmetric=False,
                         array=df["delay_slope_ci_high_naive"] - df["delay_slope"],
                         arrayminus=df["delay_slope"] - df["delay_slope_ci_low_naive"],
                         color=MUTED, thickness=6, width=0),
            hoverinfo="skip"))
    fig.add_trace(go.Scatter(
        x=df["delay_slope"], y=names, mode="markers", name="Day-blocked 95% CI",
        marker=dict(size=11, color=colors, line=dict(width=2, color="#fcfcfb")),
        error_x=dict(type="data", symmetric=False,
                     array=df["delay_slope_ci_high"] - df["delay_slope"],
                     arrayminus=df["delay_slope"] - df["delay_slope_ci_low"],
                     color=MUTED, thickness=2, width=6),
        customdata=df[["delay_slope_ci_low", "delay_slope_ci_high",
                       "delay_intercept", "delay_r2"]].to_numpy(),
        hovertemplate=("<b>%{y}</b><br>Slope %{x:.3f} "
                       "[%{customdata[0]:.3f}, %{customdata[1]:.3f}]"
                       "<br>Intercept %{customdata[2]:+.3f} min"
                       "<br>r² %{customdata[3]:.3f}<extra></extra>")))
    fig.add_vline(x=1.0, line=dict(color=MUTED, width=1.5, dash="dash"),
                  annotation_text="parity", annotation_position="top")
    fig.update_layout(
        template=TEMPLATE, height=height or max(280, 42 * len(df) + 130),
        xaxis_title=("Delay slope: minutes of INRIX delay per minute of reference "
                     "delay — below 1 means INRIX compresses delay"),
        yaxis_title=None, margin=dict(l=10, r=20, t=40, b=50),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1))
    fig.update_xaxes(gridcolor=GRID, zeroline=False)
    fig.update_yaxes(gridcolor=GRID)
    return fig


def level_gap_forest(summary: pd.DataFrame, key: str = ROUTE_COL, labels=None,
                     flagged=(), height: int | None = None) -> go.Figure:
    """The free-flow level gap with its day-blocked interval, one row per route.

    The *other* effect ``bias`` fuses (ROADMAP Item 32): how far apart the two
    sources sit at each one's own 10th-percentile free-flow, before any delay is
    involved. A gap here is a level difference — plausibly different pavement, a
    different path between the same endpoints, or a different extent — and unlike
    the slope it **cancels** in a before/after difference. It is drawn separately
    for that reason: the two have opposite consequences for an intervention study.
    """
    if summary is None or summary.empty:
        return _blank("No agreement summary to plot.")
    if "free_flow_gap" not in summary.columns:
        return _blank("Summary is missing the free-flow gap columns.")

    df = summary.sort_values("free_flow_gap")
    names = [(labels or {}).get(r, r) for r in df[key]]
    flags = [r in set(flagged) for r in df[key]]
    names = [f"⚠ {n}" if f else n for n, f in zip(names, flags)]
    colors = [STATUS_CRITICAL if f else REF_COLOR for f in flags]

    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=df["free_flow_gap"], y=names, mode="markers",
        name="Day-blocked 95% CI (bootstrap)",
        marker=dict(size=11, color=colors, line=dict(width=2, color="#fcfcfb")),
        error_x=dict(type="data", symmetric=False,
                     array=df["free_flow_gap_ci_high"] - df["free_flow_gap"],
                     arrayminus=df["free_flow_gap"] - df["free_flow_gap_ci_low"],
                     color=MUTED, thickness=2, width=6),
        customdata=df[["free_flow_gap_ci_low", "free_flow_gap_ci_high",
                       "free_flow_inrix", "free_flow_ref"]].to_numpy(),
        hovertemplate=("<b>%{y}</b><br>Level gap %{x:+.2f} min "
                       "[%{customdata[0]:+.2f}, %{customdata[1]:+.2f}]"
                       "<br>Free flow: INRIX %{customdata[2]:.2f} vs "
                       "reference %{customdata[3]:.2f} min<extra></extra>")))
    fig.add_vline(x=0, line=dict(color=MUTED, width=1.5, dash="dash"))
    fig.update_layout(
        template=TEMPLATE, height=height or max(280, 42 * len(df) + 130),
        xaxis_title=("Free-flow level gap: INRIX − reference at each source's own "
                     "10th percentile (minutes)"),
        yaxis_title=None, margin=dict(l=10, r=20, t=40, b=50),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1))
    fig.update_xaxes(gridcolor=GRID, zeroline=False)
    fig.update_yaxes(gridcolor=GRID)
    return fig


def delay_regression(matched: pd.DataFrame, route: str | None = None, row=None,
                     key: str = ROUTE_COL, height: int = 460) -> go.Figure:
    """Delay against delay, with the fitted line **drawn from the summary row**.

    The one figure in this report carrying a fitted line, and it is still a shell:
    the slope and intercept are read off ``row`` — the same values the scorecard
    prints — and this builder only evaluates ``intercept + slope · x`` to place
    the segment. Nothing here calls ``polyfit``; that fusion is exactly what the
    retired report did (``legacy/generate_corridor_html_reports.py:504-549``).

    Each axis is that source's delay above its **own** free flow, so the origin is
    "both sources at free flow". A line through the origin with a slope below 1 is
    the compression finding in one picture: the gap is multiplicative in delay,
    which is why it does not cancel in a before/after difference.
    """
    sub = _one_route(matched, route, key)
    if sub is None or sub.empty:
        return _blank("No matched bins for this corridor.")
    if row is None or row.get("free_flow_inrix") != row.get("free_flow_inrix"):
        return _blank("No delay regression for this corridor.")

    ff_i, ff_r = float(row["free_flow_inrix"]), float(row["free_flow_ref"])
    d_i = (sub[INRIX_COL] - ff_i).clip(lower=0)
    d_r = (sub[REF_COL] - ff_r).clip(lower=0)
    hi = float(max(d_r.max(), d_i.max())) or 1.0
    pad = hi * 0.05

    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=[0, hi], y=[0, hi], mode="lines", name="1:1 (equal delay)",
        line=dict(color=MUTED, width=1.5, dash="dash"), hoverinfo="skip"))
    fig.add_trace(go.Scatter(
        x=d_r, y=d_i, mode="markers", name="15-minute bin",
        marker=dict(size=5, color=INRIX_COLOR, opacity=0.45, line=dict(width=0)),
        customdata=pd.to_datetime(sub[DATETIME_COL]).dt.strftime("%Y-%m-%d %H:%M"),
        hovertemplate=("%{customdata}<br>Reference delay %{x:.2f} min"
                       "<br>INRIX delay %{y:.2f} min<extra></extra>")))
    slope, intercept = row.get("delay_slope"), row.get("delay_intercept")
    if slope == slope and intercept == intercept:
        fig.add_trace(go.Scatter(
            x=[0, hi], y=[intercept, intercept + slope * hi], mode="lines",
            name=f"Fitted in the core: {_fmt(slope, '.3f')}·x {_fmt(intercept, '+.3f')}",
            line=dict(color=REF_COLOR, width=2.5), hoverinfo="skip"))
    if row is not None:
        fig.add_annotation(xref="paper", yref="paper", x=0.03, y=0.97,
                           xanchor="left", yanchor="top", showarrow=False,
                           text=stat_box(row), align="left",
                           bgcolor="rgba(252,252,251,0.9)", bordercolor=GRID,
                           borderwidth=1, borderpad=6,
                           font=dict(size=11, color="#0b0b0b"))
    fig.update_layout(template=TEMPLATE, height=height,
                      margin=dict(l=60, r=30, t=40, b=50),
                      legend=dict(orientation="h", yanchor="bottom", y=1.02,
                                  xanchor="right", x=1))
    fig.update_xaxes(title_text="Reference delay above its own free flow (minutes)",
                     range=[-pad, hi + pad], gridcolor=GRID)
    fig.update_yaxes(title_text="INRIX delay above its own free flow (minutes)",
                     range=[-pad, hi + pad], gridcolor=GRID,
                     scaleanchor="x", scaleratio=1)
    return fig


def coverage_timeline(coverage: pd.DataFrame, key: str = ROUTE_COL, labels=None,
                      height: int | None = None) -> go.Figure:
    """Each route's own first→last sampled span, so no study-wide banner can
    stand in for fifteen different coverages (G4).

    ``coverage`` is ``reference.coverage_gate`` output; when it carries
    ``window_start`` / ``window_end`` the requested study window is shaded behind
    the bars.
    """
    if coverage is None or coverage.empty:
        return _blank("No coverage frame to plot.")

    df = coverage.sort_values("first")
    names = [(labels or {}).get(r, r) for r in df[key]]
    first = pd.to_datetime(df["first"]).dt.tz_localize(None)
    last = pd.to_datetime(df["last"]).dt.tz_localize(None)

    fig = go.Figure()
    if {"window_start", "window_end"} <= set(df.columns) and len(df):
        fig.add_vrect(x0=df["window_start"].iloc[0], x1=df["window_end"].iloc[0],
                      fillcolor=MUTED, opacity=0.07, line_width=0,
                      annotation_text="requested study window",
                      annotation_position="top left")
    fig.add_trace(go.Bar(
        x=(last - first), y=names, base=first, orientation="h",
        marker_color=INRIX_COLOR, name="Sampled span", showlegend=False,
        customdata=list(zip(df["n_days"], df["n_obs"],
                            first.dt.strftime("%Y-%m-%d"), last.dt.strftime("%Y-%m-%d"))),
        text=[f"{int(d):,} days" for d in df["n_days"]], textposition="outside",
        cliponaxis=False,
        hovertemplate=("<b>%{y}</b><br>%{customdata[2]} → %{customdata[3]}"
                       "<br>%{customdata[0]:,} days sampled, %{customdata[1]:,} samples"
                       "<extra></extra>")))
    fig.update_layout(template=TEMPLATE, height=height or max(300, 40 * len(df) + 140),
                      xaxis_title="Reference sampling span (local time)",
                      margin=dict(l=10, r=60, t=50, b=50), bargap=0.3)
    fig.update_xaxes(gridcolor=GRID)
    return fig


# ---------------------------------------------------------------------------
# Per-corridor figures (one route)
# ---------------------------------------------------------------------------
def scatter_1to1(matched: pd.DataFrame, route: str | None = None, row=None,
                 key: str = ROUTE_COL, height: int = 460) -> go.Figure:
    """INRIX against the reference, bin for bin, with the 1:1 line.

    No fitted line: an OLS slope inside a figure is the fusion this rebuild
    undoes, and the honest summary of the gap — bias, its interval, the delay
    ratio — is already computed and sits in the stat box (from ``row``).
    """
    sub = _one_route(matched, route, key)
    if sub is None or sub.empty:
        return _blank("No matched bins for this corridor.")

    lo = float(min(sub[REF_COL].min(), sub[INRIX_COL].min()))
    hi = float(max(sub[REF_COL].max(), sub[INRIX_COL].max()))
    pad = (hi - lo) * 0.05 or 1.0
    lo, hi = lo - pad, hi + pad

    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=[lo, hi], y=[lo, hi], mode="lines", name="1:1 agreement",
        line=dict(color=MUTED, width=1.5, dash="dash"), hoverinfo="skip"))
    fig.add_trace(go.Scatter(
        x=sub[REF_COL], y=sub[INRIX_COL], mode="markers", name="15-minute bin",
        marker=dict(size=5, color=INRIX_COLOR, opacity=0.45,
                    line=dict(width=0)),
        customdata=list(zip(pd.to_datetime(sub[DATETIME_COL]).dt.strftime("%Y-%m-%d %H:%M"),
                            sub[DIFF_COL])),
        hovertemplate=("%{customdata[0]}<br>Reference %{x:.2f} min"
                       "<br>INRIX %{y:.2f} min<br>Difference %{customdata[1]:+.2f} min"
                       "<extra></extra>")))
    if row is not None:
        fig.add_annotation(xref="paper", yref="paper", x=0.03, y=0.97,
                           xanchor="left", yanchor="top", showarrow=False,
                           text=stat_box(row), align="left",
                           bgcolor="rgba(252,252,251,0.9)", bordercolor=GRID,
                           borderwidth=1, borderpad=6,
                           font=dict(size=11, color="#0b0b0b"))
    fig.update_layout(template=TEMPLATE, height=height,
                      margin=dict(l=60, r=30, t=40, b=50),
                      legend=dict(orientation="h", yanchor="bottom", y=1.02,
                                  xanchor="right", x=1))
    fig.update_xaxes(title_text="Reference travel time (minutes)", range=[lo, hi],
                     gridcolor=GRID)
    fig.update_yaxes(title_text="INRIX travel time (minutes)", range=[lo, hi],
                     gridcolor=GRID, scaleanchor="x", scaleratio=1)
    return fig


def bland_altman(matched: pd.DataFrame, route: str | None = None, row=None,
                 key: str = ROUTE_COL, height: int = 420) -> go.Figure:
    """Difference against mean, with the bias and the limits of agreement drawn
    from the summary row — the range a *single* bin's disagreement falls in,
    which a correlation cannot tell you."""
    sub = _one_route(matched, route, key)
    if sub is None or sub.empty:
        return _blank("No matched bins for this corridor.")

    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=sub[MEAN_COL], y=sub[DIFF_COL], mode="markers", name="15-minute bin",
        marker=dict(size=5, color=INRIX_COLOR, opacity=0.45),
        customdata=pd.to_datetime(sub[DATETIME_COL]).dt.strftime("%Y-%m-%d %H:%M"),
        hovertemplate=("%{customdata}<br>Mean of the two %{x:.2f} min"
                       "<br>Difference %{y:+.2f} min<extra></extra>")))
    fig.add_hline(y=0, line=dict(color=MUTED, width=1, dash="dot"))
    if row is not None:
        for value, label, dash in ((row.get("bias"), "bias", "solid"),
                                   (row.get("loa_low"), "95% LoA", "dash"),
                                   (row.get("loa_high"), "95% LoA", "dash")):
            if value is not None and pd.notna(value):
                fig.add_hline(y=float(value),
                              line=dict(color=REF_COLOR, width=1.5, dash=dash),
                              annotation_text=f"{label} {float(value):+.2f}",
                              annotation_position="right",
                              annotation_font=dict(size=10, color=MUTED))
    fig.update_layout(template=TEMPLATE, height=height, showlegend=False,
                      margin=dict(l=60, r=90, t=40, b=50))
    fig.update_xaxes(title_text="Mean of the two sources (minutes)", gridcolor=GRID)
    fig.update_yaxes(title_text="INRIX − reference (minutes)", gridcolor=GRID)
    return fig


def diurnal_profile(profile: pd.DataFrame, route: str | None = None,
                    key: str = ROUTE_COL, height: int = 400) -> go.Figure:
    """Mean travel time by time of day, both sources.

    Takes ``agreement.profile(matched, by="time_of_day")`` — the means are the
    core's, and this draws them. The shape is the argument: the two curves agree
    overnight and separate in the peak, which is the delay-compression finding in
    one picture.
    """
    sub = _one_route(profile, route, key)
    if sub is None or sub.empty:
        return _blank("No profile to plot for this corridor.")
    sub = sub.sort_values("minute_of_day")

    fig = go.Figure()
    for col, label, color in (("mean_ref", REF_LABEL, REF_COLOR),
                              ("mean_inrix", INRIX_LABEL, INRIX_COLOR)):
        fig.add_trace(go.Scatter(
            x=sub["time_of_day"], y=sub[col], mode="lines+markers", name=label,
            line=dict(color=color, width=2), marker=dict(size=7, color=color),
            hovertemplate=f"{label} %{{y:.2f}} min at %{{x}}<extra></extra>"))
    fig.update_layout(template=TEMPLATE, height=height, hovermode="x unified",
                      margin=dict(l=60, r=30, t=40, b=50),
                      legend=dict(orientation="h", yanchor="bottom", y=1.02,
                                  xanchor="right", x=1))
    fig.update_xaxes(title_text="Time of day (local)", gridcolor=GRID)
    fig.update_yaxes(title_text="Mean travel time (minutes)", gridcolor=GRID)
    return fig


def bias_grid(profile: pd.DataFrame, route: str | None = None, key: str = ROUTE_COL,
              height: int = 380) -> go.Figure:
    """Day × time-of-day grid of the signed bias.

    Takes ``agreement.profile(matched, by=["day_of_week", "time_of_day"])``. The
    ramp is diverging about a **gray zero**, so "the two agree here" reads as
    absence of colour and the sign of a disagreement is never ambiguous.
    """
    sub = _one_route(profile, route, key)
    if sub is None or sub.empty:
        return _blank("No profile to plot for this corridor.")
    grid = (sub.pivot_table(index="day_of_week", columns="time_of_day",
                            values="bias", observed=True)
            .sort_index(axis=1))
    span = float(pd.Series(grid.to_numpy().ravel()).abs().max() or 1.0)

    fig = go.Figure(go.Heatmap(
        z=grid.to_numpy(), x=list(grid.columns), y=[str(i) for i in grid.index],
        colorscale=DIVERGING, zmid=0, zmin=-span, zmax=span,
        colorbar=dict(title="INRIX −<br>reference<br>(min)", thickness=12),
        hovertemplate="%{y} %{x}<br>Bias %{z:+.2f} min<extra></extra>"))
    fig.update_layout(template=TEMPLATE, height=height,
                      margin=dict(l=90, r=30, t=40, b=50))
    fig.update_xaxes(title_text="Time of day (local)")
    fig.update_yaxes(autorange="reversed")
    return fig


def daily_bias(profile: pd.DataFrame, route: str | None = None, key: str = ROUTE_COL,
               height: int = 340) -> go.Figure:
    """Daily mean bias over the study — the unit the confidence interval is built
    from, and the view where a reference logger that quietly stops shows up as the
    series ending."""
    sub = _one_route(profile, route, key)
    if sub is None or sub.empty:
        return _blank("No daily series to plot for this corridor.")
    sub = sub.sort_values("date")

    fig = go.Figure(go.Scatter(
        x=sub["date"], y=sub["bias"], mode="lines+markers", name="Daily mean bias",
        line=dict(color=INRIX_COLOR, width=1.5),
        marker=dict(size=5, color=INRIX_COLOR),
        customdata=sub["n_bins"],
        hovertemplate=("%{x|%Y-%m-%d}<br>Mean bias %{y:+.2f} min"
                       "<br>%{customdata:,} bins<extra></extra>")))
    fig.add_hline(y=0, line=dict(color=MUTED, width=1.5, dash="dash"))
    fig.update_layout(template=TEMPLATE, height=height, showlegend=False,
                      margin=dict(l=60, r=30, t=40, b=50))
    fig.update_xaxes(title_text="Date (local)", gridcolor=GRID)
    fig.update_yaxes(title_text="Daily mean of INRIX − reference (min)", gridcolor=GRID)
    return fig


def reference_series(observations: pd.DataFrame, route: str | None = None,
                     key: str = ROUTE_COL, value: str = TT_COL,
                     height: int = 320) -> go.Figure:
    """Raw reference samples over time for one route — the evidence behind the
    coverage row, at full sample resolution."""
    sub = _one_route(observations, route, key)
    if sub is None or sub.empty:
        return _blank("No reference samples for this corridor.")
    fig = go.Figure(go.Scatter(
        x=pd.to_datetime(sub[DATETIME_COL]), y=sub[value], mode="markers",
        marker=dict(size=3, color=REF_COLOR, opacity=0.5), name=REF_LABEL,
        hovertemplate="%{x|%Y-%m-%d %H:%M}<br>%{y:.2f} min<extra></extra>"))
    fig.update_layout(template=TEMPLATE, height=height, showlegend=False,
                      margin=dict(l=60, r=30, t=40, b=50))
    fig.update_xaxes(title_text="Sample time (local)", gridcolor=GRID)
    fig.update_yaxes(title_text="Reference travel time (minutes)", gridcolor=GRID)
    return fig

"""Plotly figure builders for the Dash explorer.  (ROADMAP Item 7)

The **thin plotting shell** over the ``inrix_tools`` compute core: every function
here takes a DataFrame the core already computed and turns it into a
``plotly.graph_objects.Figure``. None of them compute statistics — that stays in
``src/inrix_tools/`` (the load-bearing rule in CLAUDE.md). Keeping them out of the
callbacks (which only wire inputs to these builders) makes each figure unit-testable
headless.

Colour/units come from the data: metric column names carry their unit (``Speed(
miles/hour)``), so labels are derived, never hard-coded to mph/minutes.
"""
from __future__ import annotations

from typing import Mapping, Sequence

import numpy as np
import pandas as pd
import plotly.colors as pcolors
import plotly.graph_objects as go

from inrix_tools.beforeafter import parse_period
from inrix_tools.decompose import (
    RESID_COL,
    SEASON_DAY_COL,
    SEASON_WEEK_COL,
    TREND_COL,
)
from inrix_tools.io import DATETIME_COL, SEGMENT_COL

# One template so every panel reads as one system (see the dataviz skill's
# "one visual system" rule): clean, light, restrained gridlines.
TEMPLATE = "plotly_white"
_MAP_COLORSCALE = "Viridis"
_HIGHLIGHT = "#d7191c"  # selected-segment accent (matches the KML hot end)
_BEFORE_COLOR = "#2b83ba"
_AFTER_COLOR = "#d7191c"


def _blank(message: str, *, height: int = 420) -> go.Figure:
    """A placeholder figure carrying a centred message (empty selection, no data)."""
    fig = go.Figure()
    fig.add_annotation(text=message, showarrow=False, font=dict(size=14, color="#888"),
                       xref="paper", yref="paper", x=0.5, y=0.5)
    fig.update_layout(template=TEMPLATE, height=height,
                      xaxis=dict(visible=False), yaxis=dict(visible=False),
                      margin=dict(l=10, r=10, t=30, b=10))
    return fig


def _unit_label(col: str | None) -> str:
    """``"Speed(miles/hour)"`` -> ``"Speed (miles/hour)"`` for axis titles."""
    if not col:
        return ""
    return col.replace("(", " (")


# ---------------------------------------------------------------------------
# Embedded map — the primary selector
# ---------------------------------------------------------------------------
def segment_map(
    geo,
    values: Mapping[int, float] | pd.Series | None = None,
    *,
    value_label: str = "",
    selected_id: int | None = None,
    label_col: str = "Combined",
    sublabel_col: str | None = None,
    colorscale: str = _MAP_COLORSCALE,
    height: int = 620,
    uirevision: str = "segment-map",
) -> go.Figure:
    """Render the geometry layer as clickable segment polylines over an OSM basemap.

    Each segment is its own road-following polyline trace (colour from ``values``);
    a markers layer at the segment midpoints carries the colour bar, the hover
    tooltip, and — crucially — the click target (thin lines are hard to hit, the
    midpoint dot is not). ``customdata`` on every mark is the ``Segment ID`` so the
    app's click callback can read the selection straight off ``clickData``.

    Args:
        geo: GeoDataFrame indexed by ``Segment ID`` with a shapely ``geometry``
            column (from ``inrix_tools.geometry.segment_geometry``); may carry a
            ``label_col`` for hover text. Segments with no geometry are skipped.
        values: ``Segment ID -> metric`` used to colour segments (mean speed,
            before/after delta, ...). ``None`` draws every segment one neutral colour.
        value_label: colour-bar / hover title for ``values``.
        label_col: geo column used as the bold hover title (e.g. a friendly name).
        sublabel_col: optional geo column shown as an italic hover subtitle under
            the title (e.g. the raw INRIX ``Combined`` label); skipped when it
            equals the title or is missing.
        selected_id: segment to highlight (ring + thick white-edged line).
        uirevision: Plotly ``uirevision`` token — pan/zoom is preserved while it
            is unchanged, so key it on the loaded dataset to recenter on a new
            export but hold the view across metric/selection updates.
    """
    if values is not None and not isinstance(values, pd.Series):
        values = pd.Series(values)

    fig = go.Figure()
    lons, lats, cvals, custom, texts = [], [], [], [], []
    color_for = _line_colors(geo.index, values, colorscale)

    for sid, row in geo.iterrows():
        geom = row.get("geometry")
        if geom is None or getattr(geom, "is_empty", True):
            continue
        xs, ys = geom.xy
        is_sel = selected_id is not None and int(sid) == int(selected_id)
        fig.add_trace(go.Scattermap(
            lon=list(xs), lat=list(ys), mode="lines",
            line=dict(width=6 if is_sel else 3, color=_HIGHLIGHT if is_sel else color_for[sid]),
            opacity=1.0 if is_sel else 0.85,
            hoverinfo="skip", showlegend=False,
        ))
        mid = geom.interpolate(0.5, normalized=True)
        lons.append(mid.x)
        lats.append(mid.y)
        cvals.append(values.get(sid, np.nan) if values is not None else 0)
        label = str(row[label_col]) if label_col in geo.columns else f"Segment {sid}"
        custom.append(int(sid))
        # optional raw-label subtitle (e.g. the INRIX Combined string under a
        # friendly name), shown only when it differs from the primary label.
        sub = ""
        if sublabel_col and sublabel_col in geo.columns:
            subtxt = str(row[sublabel_col])
            if subtxt and subtxt != label and subtxt.lower() != "nan":
                sub = f"<br><i>{subtxt}</i>"
        vtxt = "" if values is None else f"<br>{value_label}: {values.get(sid, float('nan')):.2f}"
        texts.append(f"<b>{label}</b>{sub}<br>Segment {sid}{vtxt}")

    # The midpoint markers are the click target, hover anchor, and colour-bar
    # carrier (Item 13) — the segment *lines* already carry the metric colour
    # (``_line_colors``), so these are shrunk to small, semi-transparent dots that
    # stay hittable/hoverable without cluttering the map.
    marker = dict(size=6, color=cvals, colorscale=colorscale,
                  showscale=values is not None,
                  colorbar=dict(title=value_label, thickness=14) if values is not None else None)
    if values is None:
        marker = dict(size=5, color="#2b83ba")
    fig.add_trace(go.Scattermap(
        lon=lons, lat=lats, mode="markers", marker=marker, opacity=0.6,
        customdata=custom, text=texts, hovertemplate="%{text}<extra></extra>",
        showlegend=False, name="segments",
    ))
    # Selected-segment ring, drawn last so it sits on top.
    if selected_id is not None and int(selected_id) in [int(c) for c in custom]:
        i = [int(c) for c in custom].index(int(selected_id))
        fig.add_trace(go.Scattermap(
            lon=[lons[i]], lat=[lats[i]], mode="markers",
            marker=dict(size=18, color=_HIGHLIGHT), opacity=0.6,
            hoverinfo="skip", showlegend=False,
        ))

    center, zoom = _map_view(lats, lons)
    fig.update_layout(
        map=dict(style="open-street-map", center=center, zoom=zoom),
        margin=dict(l=0, r=0, t=0, b=0), height=height,
        uirevision=uirevision,  # keep pan/zoom across metric/selection updates
    )
    return fig


def _line_colors(ids, values, colorscale) -> dict:
    """A per-segment colour dict: sampled from ``colorscale`` over the value range,
    or a single neutral colour when ``values`` is None / degenerate."""
    neutral = "#4c78a8"
    if values is None:
        return {sid: neutral for sid in ids}
    v = values.reindex(ids).astype(float)
    lo, hi = v.min(), v.max()
    if not np.isfinite(lo) or not np.isfinite(hi) or hi == lo:
        return {sid: neutral for sid in ids}
    frac = ((v - lo) / (hi - lo)).clip(0, 1)
    sampled = pcolors.sample_colorscale(colorscale, frac.fillna(0.0).tolist())
    out = {sid: (neutral if pd.isna(frac.get(sid)) else sampled[i])
           for i, sid in enumerate(ids)}
    return out


def _map_view(lats, lons):
    """Center + a rough zoom from the geometry's lat/lon span."""
    if not lats or not lons:
        return dict(lat=43.6, lon=-116.2), 11  # Boise fallback
    center = dict(lat=float(np.mean(lats)), lon=float(np.mean(lons)))
    span = max(max(lats) - min(lats), (max(lons) - min(lons)) * 0.75, 1e-3)
    zoom = float(np.clip(np.log2(360.0 / span) - 1.0, 3, 15))
    return center, zoom


# ---------------------------------------------------------------------------
# Raw time series
# ---------------------------------------------------------------------------
def time_series(
    df: pd.DataFrame,
    value: str,
    *,
    title: str = "",
    before=None,
    after=None,
    height: int = 420,
) -> go.Figure:
    """Raw per-timestamp ``value`` for one segment's rows, with the before/after
    comparison windows shaded. Uses WebGL (``Scattergl``) — a segment carries tens
    of thousands of 5-min samples over a multi-month export."""
    if df.empty or value not in df.columns:
        return _blank("No data for this selection.", height=height)
    d = df.sort_values(DATETIME_COL)
    fig = go.Figure(go.Scattergl(
        x=d[DATETIME_COL], y=d[value], mode="lines",
        line=dict(width=1, color="#4c78a8"), name=value,
    ))
    _shade_periods(fig, d[DATETIME_COL], before, after)
    fig.update_layout(
        template=TEMPLATE, height=height, title=title,
        xaxis_title="Local time", yaxis_title=_unit_label(value),
        margin=dict(l=10, r=10, t=40, b=10), showlegend=False,
    )
    return fig


def _shade_periods(fig, ts: pd.Series, before, after):
    """Shade the before (blue) / after (red) comparison windows on a time axis."""
    tz = ts.dt.tz
    for spec, color, name in ((before, _BEFORE_COLOR, "before"), (after, _AFTER_COLOR, "after")):
        if spec is None:
            continue
        try:
            start, end = parse_period(spec, tz)
        except Exception:
            continue
        fig.add_vrect(x0=start, x1=end, fillcolor=color, opacity=0.12,
                      line_width=0, annotation_text=name,
                      annotation_position="top left")


# ---------------------------------------------------------------------------
# Day-group x time-bin summary (Item 3)
# ---------------------------------------------------------------------------
def summary_bars(
    summary: pd.DataFrame,
    value: str,
    *,
    summary_after: pd.DataFrame | None = None,
    period_labels: tuple[str, str] = ("Before", "After"),
    day_col: str = "Day Group",
    time_col: str = "Time Bin",
    title: str = "",
    height: int = 420,
) -> go.Figure:
    """Grouped bars of mean ``value`` per day-group x time-bin (from
    ``speed.segment_summary``), with the ±1 SD std as error bars. Bars are grouped
    by time-bin along x and coloured by day-group.

    When ``summary_after`` is given (Item 13), the two summaries are drawn as
    **side-by-side facets** (``summary`` = before on the left, ``summary_after`` =
    after on the right) sharing a y-axis, so the day-group x time-bin means are
    directly comparable across the intervention. One legend (one entry per
    day-group) spans both facets. ``summary_after=None`` keeps the single-panel view.
    """
    mean_col, std_col = f"{value}_mean", f"{value}_std"
    if summary_after is not None:
        return _summary_bars_beforeafter(
            summary, summary_after, value, period_labels=period_labels,
            day_col=day_col, time_col=time_col, title=title, height=height)
    if summary.empty or mean_col not in summary.columns:
        return _blank("No summary for this selection.", height=height)

    fig = go.Figure()
    palette = pcolors.qualitative.Safe
    day_order = list(dict.fromkeys(summary[day_col])) if day_col in summary else [None]
    for i, day in enumerate(day_order):
        sub = summary[summary[day_col] == day] if day_col in summary else summary
        fig.add_trace(go.Bar(
            x=sub[time_col] if time_col in sub else sub.index,
            y=sub[mean_col],
            error_y=dict(type="data", array=sub.get(std_col), visible=True, thickness=1),
            name=str(day), marker_color=palette[i % len(palette)],
        ))
    fig.update_layout(
        template=TEMPLATE, height=height, title=title, barmode="group",
        xaxis_title="Time of day", yaxis_title=f"Mean {_unit_label(value)}",
        legend_title="Day group", margin=dict(l=10, r=10, t=40, b=10),
    )
    return fig


def _summary_bars_beforeafter(
    before: pd.DataFrame,
    after: pd.DataFrame,
    value: str,
    *,
    period_labels: tuple[str, str],
    day_col: str,
    time_col: str,
    title: str,
    height: int,
) -> go.Figure:
    """Two ``summary_bars`` panels side by side (before | after), shared y-axis and
    a single day-group legend, for a directly-comparable before/after read."""
    from plotly.subplots import make_subplots

    mean_col, std_col = f"{value}_mean", f"{value}_std"
    have = [not s.empty and mean_col in s.columns for s in (before, after)]
    if not any(have):
        return _blank("No summary for this selection.", height=height)

    fig = make_subplots(rows=1, cols=2, shared_yaxes=True,
                        subplot_titles=period_labels, horizontal_spacing=0.07)
    palette = pcolors.qualitative.Safe
    # A stable day-group -> colour map across both facets (so "Fri" is one colour
    # left and right) and one legend entry per group.
    days_seen: list = []
    for summ in (before, after):
        if day_col in summ:
            for d in summ[day_col]:
                if d not in days_seen:
                    days_seen.append(d)
    color_of = {d: palette[i % len(palette)] for i, d in enumerate(days_seen)}

    shown: set = set()
    for col_i, summ in enumerate((before, after), start=1):
        if summ.empty or mean_col not in summ.columns:
            continue
        day_order = list(dict.fromkeys(summ[day_col])) if day_col in summ else [None]
        for day in day_order:
            sub = summ[summ[day_col] == day] if day_col in summ else summ
            key = str(day)
            fig.add_trace(go.Bar(
                x=sub[time_col] if time_col in sub else sub.index,
                y=sub[mean_col],
                error_y=dict(type="data", array=sub.get(std_col), visible=True, thickness=1),
                name=key, legendgroup=key, showlegend=key not in shown,
                marker_color=color_of.get(day, palette[0]),
            ), row=1, col=col_i)
            shown.add(key)

    fig.update_layout(
        template=TEMPLATE, height=height, title=title, barmode="group",
        legend_title="Day group", margin=dict(l=10, r=10, t=60, b=10),
    )
    fig.update_xaxes(title_text="Time of day")
    fig.update_yaxes(title_text=f"Mean {_unit_label(value)}", row=1, col=1)
    return fig


# ---------------------------------------------------------------------------
# Before / after (Item 4)
# ---------------------------------------------------------------------------
_DEEMPHASIS = "#b9c7d9"  # forest rows that don't clear the FDR cutoff


def beforeafter_forest(
    compare: pd.DataFrame,
    *,
    labels: Mapping[int, str] | None = None,
    selected_id: int | None = None,
    value_label: str = "",
    fdr_alpha: float = 0.05,
    title: str = "",
    height: int = 480,
) -> go.Figure:
    """A forest plot of the per-segment before/after effect with its confidence
    interval (from ``beforeafter.compare_periods``): effect on x, one row per
    segment, the CI as a horizontal error bar. A dashed line at 0 marks "no change";
    a CI clearing it is a real shift. The selected segment is highlighted.

    When the frame carries a ``q_value`` column (Benjamini–Hochberg, from
    ``compare_periods``), rows with ``q > fdr_alpha`` are de-emphasised and a
    caption states the family size — with ~46 segments a couple of raw 95% CIs
    clear zero by chance alone, so the FDR view is the honest one. Any
    ``attrs['warnings']`` (e.g. a period truncated by the decomposition warm-up)
    are surfaced in the caption too."""
    if compare.empty or "effect" not in compare.columns:
        return _blank("Run a before/after comparison to populate this.", height=height)

    d = compare.sort_values("effect").reset_index(drop=True)
    y = list(range(len(d)))
    def _name(sid):
        return (labels or {}).get(int(sid), f"Segment {int(sid)}")
    ytext = [_name(s) for s in d[SEGMENT_COL]]

    has_q = "q_value" in d.columns
    sig = (d["q_value"] <= fdr_alpha) if has_q else pd.Series(True, index=d.index)

    def _color(i, sid):
        if selected_id is not None and int(sid) == int(selected_id):
            return _HIGHLIGHT
        return "#4c78a8" if sig.iloc[i] else _DEEMPHASIS
    colors = [_color(i, s) for i, s in enumerate(d[SEGMENT_COL])]

    qtxt = [f" · q={q:.3f}" for q in d["q_value"]] if has_q else [""] * len(d)
    fig = go.Figure(go.Scatter(
        x=d["effect"], y=y, mode="markers",
        marker=dict(size=9, color=colors),
        error_x=dict(type="data", symmetric=False,
                     array=d["ci_high"] - d["effect"],
                     arrayminus=d["effect"] - d["ci_low"], thickness=1.2,
                     color="#9aa5b1"),
        customdata=d[SEGMENT_COL],
        # The name goes in ``text`` (not the hovertemplate's ``%{y}``, which is the
        # numeric row index and used to show that index instead of the segment —
        # Item 14 review B4).
        text=[f"<b>{name}</b><br>effect {e:+.3f} [{lo:+.3f}, {hi:+.3f}]{q}"
              for name, e, lo, hi, q in
              zip(ytext, d["effect"], d["ci_low"], d["ci_high"], qtxt)],
        hovertemplate="%{text}<extra></extra>",
    ))
    fig.add_vline(x=0, line_dash="dash", line_color="#888")

    caption = []
    if has_q:
        caption.append(f"{len(d)} comparisons · {int(sig.sum())} pass "
                       f"BH-FDR {fdr_alpha:.0%} (de-emphasised = not significant)")
    caption.extend(f"⚠ {w}" for w in compare.attrs.get("warnings", []))
    if caption:
        fig.add_annotation(
            text="<br>".join(caption), showarrow=False,
            xref="paper", yref="paper", x=1, y=1.02,
            xanchor="right", yanchor="bottom",
            font=dict(size=11, color="#888"), align="right",
        )

    method = compare.attrs.get("method", "")
    unit = compare.attrs.get("unit")
    subtitle = f"{method}, {unit}-level CI" if unit else method
    fig.update_layout(
        template=TEMPLATE, height=max(height, 120 + 22 * len(d)),
        title=title or f"Before/after effect ({subtitle}) — {value_label}",
        xaxis_title=f"After − before ({value_label})",
        yaxis=dict(tickmode="array", tickvals=y, ticktext=ytext, autorange="reversed"),
        margin=dict(l=10, r=10, t=50, b=10), showlegend=False,
    )
    return fig


# ---------------------------------------------------------------------------
# Decomposition + changepoints (Items 4 & 5)
# ---------------------------------------------------------------------------
def decomposition(
    decomposed: pd.DataFrame,
    value: str,
    *,
    changepoints: pd.DataFrame | None = None,
    title: str = "",
    height: int = 620,
) -> go.Figure:
    """Stacked decomposition for one segment: observed vs rolling-median trend,
    the combined daily+weekly seasonal component, and the residual — with any
    detected changepoints drawn as vertical markers on the trend panel. Built from
    ``decompose.decompose_segments`` output (+ ``changepoint.detect_changepoints``)."""
    from plotly.subplots import make_subplots

    if decomposed.empty or value not in decomposed.columns:
        return _blank("No decomposition for this selection.", height=height)
    d = decomposed.sort_values(DATETIME_COL)
    x = d[DATETIME_COL]

    fig = make_subplots(rows=3, cols=1, shared_xaxes=True, vertical_spacing=0.06,
                        subplot_titles=("Observed + trend", "Seasonal (day + week)", "Residual"))
    fig.add_trace(go.Scattergl(x=x, y=d[value], mode="lines", name="observed",
                               line=dict(width=1, color="#b0b0b0")), row=1, col=1)
    if TREND_COL in d:
        fig.add_trace(go.Scattergl(x=x, y=d[TREND_COL], mode="lines", name="trend",
                                   line=dict(width=2, color="#4c78a8")), row=1, col=1)
    if SEASON_DAY_COL in d and SEASON_WEEK_COL in d:
        season = d[SEASON_DAY_COL] + d[SEASON_WEEK_COL]
        fig.add_trace(go.Scattergl(x=x, y=season, mode="lines", name="seasonal",
                                   line=dict(width=1, color="#59a14f")), row=2, col=1)
    if RESID_COL in d:
        fig.add_trace(go.Scattergl(x=x, y=d[RESID_COL], mode="lines", name="residual",
                                   line=dict(width=1, color="#e15759")), row=3, col=1)

    if changepoints is not None and not changepoints.empty:
        for _, cp in changepoints.iterrows():
            fig.add_vline(x=cp[DATETIME_COL], line_dash="dot", line_color=_HIGHLIGHT,
                          row=1, col=1)
            fig.add_annotation(x=cp[DATETIME_COL], y=1, yref="y domain",
                               text=f"{cp['avg_diff']:+.2f}", showarrow=False,
                               font=dict(size=10, color=_HIGHLIGHT), row=1, col=1)

    fig.update_layout(template=TEMPLATE, height=height, title=title,
                      margin=dict(l=10, r=10, t=60, b=10),
                      legend=dict(orientation="h", y=1.06))
    fig.update_yaxes(title_text=_unit_label(value), row=1, col=1)
    return fig

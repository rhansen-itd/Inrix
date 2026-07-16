"""Plotly Dash data explorer + embedded map — a thin shell over inrix_tools.
(ROADMAP Item 7)

The interactive explorer. The **embedded map is the primary selector**: real
segment polylines (``inrix_tools.geometry``) over an OSM basemap; click a segment
to drive every panel. Everything else — raw time series, day-group x time-bin
summary (Item 3), before/after effect + CI (Item 4), decomposition components +
changepoints (Items 4/5), and a KML export (Item 6) — is rendered from a DataFrame
the compute core returns. **No statistics live in this file**: callbacks call
``inrix_tools.*`` and hand the result to ``figures.*`` (see CLAUDE.md).

Map framework: **Plotly native maps** (``go.Scattermap``, MapLibre, the
token-free ``open-street-map`` style). Chosen so the whole GUI is one rendering
stack — every panel, map included, is a Plotly figure in a ``dcc.Graph`` — and map
clicks arrive through the standard Dash ``clickData`` path. dash-leaflet was the
alternative; it buys nicer draw tools but a second JS component and event model for
no gain here. Recorded in DESIGN_HISTORY.

Run:  ``pip install -e .[gui]`` then ``python gui/app.py`` (see README).
"""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path

import pandas as pd

# Run either as a script (``python gui/app.py``) or imported (``gui.app``):
# put this dir on the path so the sibling ``figures`` module resolves in both.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import figures  # noqa: E402

import dash_bootstrap_components as dbc  # noqa: E402
from dash import Dash, Input, Output, State, ctx, dcc, html, no_update  # noqa: E402

from inrix_tools import beforeafter, changepoint, decompose, geometry, io, kml, speed  # noqa: E402
from inrix_tools.io import DATETIME_COL, SEGMENT_COL  # noqa: E402
from inrix_tools.timebins import (  # noqa: E402
    assign_day_group,
    assign_time_bins,
    filter_time_window,
    DAY_GROUP_COL,
    TIME_BIN_COL,
)

# --- defaults (a starting point; every one is editable in the UI) -----------
_REPO = Path(__file__).resolve().parent.parent
DEFAULT_SOURCE = str(_REPO / "Myrtle_2026-02-01_to_2026-07-16_5_min_part_1.zip")
DEFAULT_SHAPEFILE = str(_REPO / "USA_Idaho_shapefile.zip")
DEFAULT_TZ = io.DEFAULT_TZ
DEFAULT_CVALUE = io.DEFAULT_CVALUE_THRESHOLD
OUTPUT_DIR = _REPO / "out"
CACHE_DIR = _REPO / "geometry_cache"
# AM peak / midday / PM peak / overnight — a sensible default; not load-bearing.
DEFAULT_TIME_BINS = ["6:00AM-9:00AM", "9:00AM-3:00PM", "3:00PM-7:00PM", "7:00PM-6:00AM"]

_METRICS = {"tt": "travel_time", "speed": "speed"}
_METRIC_LABEL = {"tt": "Travel time", "speed": "Speed"}


# ---------------------------------------------------------------------------
# Server-side dataset cache. The Myrtle export is ~2M rows — far too big to shuttle
# through a dcc.Store, and this explorer is single-user localhost (multi-user state
# is a Future ROADMAP item). So the loaded frames live here, keyed by an integer
# token that a lightweight dcc.Store passes between callbacks.
# ---------------------------------------------------------------------------
@dataclass
class Dataset:
    df: pd.DataFrame
    metadata: pd.DataFrame
    geo: object                      # GeoDataFrame indexed by Segment ID
    metric_cols: dict                # {"speed": col, "travel_time": col}
    tz: str
    span: tuple                      # (min_date, max_date) local dates
    _compare_cache: dict = field(default_factory=dict)


_DATASETS: dict[int, Dataset] = {}
_TOKEN = {"n": 0}


def _store(ds: Dataset) -> int:
    _TOKEN["n"] += 1
    _DATASETS[_TOKEN["n"]] = ds
    return _TOKEN["n"]


def _get(token) -> Dataset | None:
    return _DATASETS.get(token) if token is not None else None


# ---------------------------------------------------------------------------
# Compute wiring — thin calls into inrix_tools (no statistics defined here).
# ---------------------------------------------------------------------------
def load_dataset(source: str, tz: str, cvalue: int, shapefile: str = DEFAULT_SHAPEFILE) -> Dataset:
    """Load + localize + CValue-filter an export, join its segment geometry."""
    raw = io.load_data(source)
    local = io.to_local(raw, tz)
    filt = io.filter_cvalue(local, cvalue)
    meta = io.load_metadata(source)

    seg_ids = list(meta.index)
    cache = CACHE_DIR / f"{Path(source).stem}.geoparquet"
    net = geometry.load_xd_network(shapefile, segment_ids=seg_ids, cache_path=str(cache))
    geo = geometry.segment_geometry(net, segment_ids=seg_ids, metadata=meta)
    if "Combined" in meta.columns:
        geo["Combined"] = meta["Combined"].reindex(geo.index)

    dates = filt[DATETIME_COL].dt.date
    return Dataset(
        df=filt, metadata=meta, geo=geo,
        metric_cols=speed.metric_columns(filt), tz=tz,
        span=(dates.min(), dates.max()),
    )


def _metric_col(ds: Dataset, metric_key: str) -> str | None:
    return ds.metric_cols.get(_METRICS[metric_key])


def _apply_tod(df: pd.DataFrame, window) -> pd.DataFrame:
    """Restrict rows to the time-of-day slider window ``[h0, h1)`` (hours). A
    full-day window (``[0, 24]``, the default) is a no-op, so the unfiltered path
    is unchanged. Filtering *here*, before any compute, is what makes every panel
    describe the selected period (see ``timebins.filter_time_window``)."""
    if not window:
        return df
    h0, h1 = float(window[0]), float(window[1])
    if h0 <= 0 and h1 >= 24:
        return df
    return filter_time_window(df, (h0, h1))


def _segment_means(ds: Dataset, col: str, window=None) -> pd.Series:
    """Per-segment mean of a metric column (map colouring), within the window."""
    return _apply_tod(ds.df, window).groupby(SEGMENT_COL, observed=True)[col].mean()


def _compare_all(ds: Dataset, col: str, before, after, window=None) -> pd.DataFrame:
    """Corridor-wide before/after (decomposition primary), cached per
    (metric, periods, time-window)."""
    key = (col, str(before), str(after), str(window))
    if key not in ds._compare_cache:
        ds._compare_cache[key] = beforeafter.compare_periods(
            _apply_tod(ds.df, window), before=before, after=after, value=col
        )
    return ds._compare_cache[key]


def _labels(ds: Dataset) -> dict:
    if "Combined" in ds.metadata.columns:
        return {int(k): str(v) for k, v in ds.metadata["Combined"].items()}
    return {}


# ---------------------------------------------------------------------------
# Layout
# ---------------------------------------------------------------------------
def _controls() -> dbc.Card:
    return dbc.Card(dbc.CardBody([
        html.H6("Data", className="text-muted"),
        dbc.Label("Export (.zip / dir / data.csv)"),
        dbc.Input(id="source", value=DEFAULT_SOURCE, size="sm", debounce=True),
        dbc.Row([
            dbc.Col([dbc.Label("Timezone"),
                     dbc.Input(id="tz", value=DEFAULT_TZ, size="sm")], width=7),
            dbc.Col([dbc.Label("CValue >"),
                     dbc.Input(id="cvalue", type="number", value=DEFAULT_CVALUE, size="sm")], width=5),
        ], className="mt-2"),
        dbc.Button("Load export", id="load", color="primary", size="sm", className="mt-2 w-100"),
        html.Div(id="load-status", className="small text-muted mt-1"),
        html.Hr(),
        html.H6("Metric", className="text-muted"),
        dbc.RadioItems(id="metric", value="tt", inline=True,
                       options=[{"label": " Travel time", "value": "tt"},
                                {"label": " Speed", "value": "speed"}]),
        dbc.Label("Colour map by", className="mt-2"),
        dcc.Dropdown(id="map-mode", clearable=False, value="mean",
                     options=[{"label": "Segment mean", "value": "mean"},
                              {"label": "Before/after Δ", "value": "delta"}]),
        html.Hr(),
        html.H6("Time-of-day window", className="text-muted"),
        dcc.RangeSlider(
            id="tod-window", min=0, max=24, step=0.25, value=[0, 24],
            allowCross=False,
            marks={0: "12a", 6: "6a", 12: "12p", 18: "6p", 24: "12a"},
            tooltip={"placement": "bottom", "always_visible": False},
        ),
        html.Div(id="tod-status", className="small text-muted mt-1"),
        html.Hr(),
        html.H6("Before / after periods", className="text-muted"),
        dbc.Label("Before"),
        dcc.DatePickerRange(id="before-range", className="d-block", display_format="YYYY-MM-DD"),
        dbc.Label("After", className="mt-2"),
        dcc.DatePickerRange(id="after-range", className="d-block", display_format="YYYY-MM-DD"),
        html.Hr(),
        dbc.Button("Export KML of current view", id="export-kml", color="secondary",
                   size="sm", className="w-100"),
        html.Div(id="kml-status", className="small text-muted mt-1"),
    ]), className="mb-2")


def _layout() -> dbc.Container:
    return dbc.Container([
        dcc.Store(id="data-token"),
        html.H4("INRIX segment explorer", className="my-2"),
        dbc.Row([
            dbc.Col(_controls(), width=3),
            dbc.Col(dbc.Card(dbc.CardBody([
                dbc.Label("Segment"),
                dcc.Dropdown(id="segment", placeholder="Load an export, then pick or click a segment"),
                dcc.Graph(id="map", style={"height": "620px"}),
            ])), width=9),
        ]),
        dbc.Row(dbc.Col(dbc.Tabs(id="tabs", active_tab="ts", children=[
            dbc.Tab(dcc.Graph(id="fig-ts"), label="Time series", tab_id="ts"),
            dbc.Tab(dcc.Graph(id="fig-summary"), label="Day × time summary", tab_id="summary"),
            dbc.Tab(dcc.Graph(id="fig-ba"), label="Before / after", tab_id="ba"),
            dbc.Tab(dcc.Graph(id="fig-decomp"), label="Decomposition + changepoints", tab_id="decomp"),
        ]), width=12), className="mt-2"),
    ], fluid=True)


def _empty_geo():
    import geopandas as gpd
    return gpd.GeoDataFrame({"geometry": []}, geometry="geometry")


# ---------------------------------------------------------------------------
# App factory + callbacks
# ---------------------------------------------------------------------------
def build_app() -> Dash:
    """Construct the Dash app: the embedded map as the primary selector driving
    time-series / summary / before-after / decomposition panels, plus KML export.
    Builds its full layout without loading any data (data loads on the *Load*
    button), so the layout is constructible headless for the smoke test."""
    app = Dash(__name__, external_stylesheets=[dbc.themes.BOOTSTRAP],
               title="INRIX segment explorer", suppress_callback_exceptions=True)
    app.layout = _layout()
    _register_callbacks(app)
    return app


def _register_callbacks(app: Dash) -> None:

    @app.callback(
        Output("data-token", "data"),
        Output("load-status", "children"),
        Output("segment", "options"),
        Output("segment", "value"),
        Output("before-range", "min_date_allowed"),
        Output("before-range", "max_date_allowed"),
        Output("before-range", "start_date"),
        Output("before-range", "end_date"),
        Output("after-range", "min_date_allowed"),
        Output("after-range", "max_date_allowed"),
        Output("after-range", "start_date"),
        Output("after-range", "end_date"),
        Input("load", "n_clicks"),
        State("source", "value"),
        State("tz", "value"),
        State("cvalue", "value"),
        prevent_initial_call=True,
    )
    def _load(_n, source, tz, cvalue):
        try:
            ds = load_dataset(source, tz or DEFAULT_TZ, int(cvalue))
        except Exception as exc:  # surface the failure in the UI, don't crash
            return (no_update, f"⚠ Load failed: {exc}", no_update, no_update,
                    *[no_update] * 8)
        token = _store(ds)
        labels = _labels(ds)
        options = [{"label": labels.get(int(s), f"Segment {int(s)}"), "value": int(s)}
                   for s in ds.metadata.index]
        lo, hi = ds.span
        # Default windows: first ~5 weeks (past the decomposition warm-up) vs last ~5.
        span_days = (hi - lo).days
        window = timedelta(days=max(21, span_days // 5))
        b_start = lo + timedelta(days=min(8, span_days // 6))
        b_end = b_start + window
        a_end = hi
        a_start = a_end - window
        status = (f"✓ {len(ds.df):,} rows · {len(ds.metadata)} segments · "
                  f"{lo}…{hi} · {ds.tz}")
        return (token, status, options, no_update,
                lo, hi, b_start, b_end, lo, hi, a_start, a_end)

    # Map click -> segment dropdown (the dropdown is the single selection source).
    @app.callback(
        Output("segment", "value", allow_duplicate=True),
        Input("map", "clickData"),
        prevent_initial_call=True,
    )
    def _click_select(click):
        if not click or not click.get("points"):
            return no_update
        cd = click["points"][0].get("customdata")
        return int(cd) if cd is not None else no_update

    # The map: colour by metric/mode, highlight the selection.
    @app.callback(
        Output("map", "figure"),
        Input("data-token", "data"),
        Input("metric", "value"),
        Input("map-mode", "value"),
        Input("segment", "value"),
        Input("before-range", "start_date"),
        Input("before-range", "end_date"),
        Input("after-range", "start_date"),
        Input("after-range", "end_date"),
        Input("tod-window", "value"),
    )
    def _map(token, metric, mode, selected, b0, b1, a0, a1, window):
        ds = _get(token)
        if ds is None:
            return figures.segment_map(_empty_geo())
        col = _metric_col(ds, metric)
        label = figures._unit_label(col)
        if mode == "delta" and all([b0, b1, a0, a1]):
            comp = _compare_all(ds, col, (b0, b1), (a0, a1), window)
            values = comp.set_index(SEGMENT_COL)["effect"] if not comp.empty else None
            label = f"Δ {label}"
        else:
            values = _segment_means(ds, col, window)
        return figures.segment_map(ds.geo, values, value_label=label,
                                   selected_id=selected)

    # Panels, keyed on the active tab so only the visible one computes.
    @app.callback(
        Output("fig-ts", "figure"),
        Output("fig-summary", "figure"),
        Output("fig-ba", "figure"),
        Output("fig-decomp", "figure"),
        Input("tabs", "active_tab"),
        Input("data-token", "data"),
        Input("segment", "value"),
        Input("metric", "value"),
        Input("before-range", "start_date"),
        Input("before-range", "end_date"),
        Input("after-range", "start_date"),
        Input("after-range", "end_date"),
        Input("tod-window", "value"),
    )
    def _panels(tab, token, selected, metric, b0, b1, a0, a1, window):
        ds = _get(token)
        blank = figures._blank("Load an export to begin.")
        if ds is None:
            return blank, blank, blank, blank
        col = _metric_col(ds, metric)
        before, after = (b0, b1), (a0, a1)
        have_periods = all([b0, b1, a0, a1])
        name = _labels(ds).get(int(selected), f"Segment {selected}") if selected is not None else ""
        out = {"ts": no_update, "summary": no_update, "ba": no_update, "decomp": no_update}

        if tab == "ts":
            out["ts"] = _fig_timeseries(ds, selected, col, before, after, name, window)
        elif tab == "summary":
            out["summary"] = _fig_summary(ds, selected, col, name, window)
        elif tab == "ba":
            out["ba"] = _fig_beforeafter(ds, selected, col, before, after, have_periods, window)
        elif tab == "decomp":
            out["decomp"] = _fig_decomp(ds, selected, col, name, window)
        return out["ts"], out["summary"], out["ba"], out["decomp"]

    @app.callback(
        Output("kml-status", "children"),
        Input("export-kml", "n_clicks"),
        State("data-token", "data"),
        State("metric", "value"),
        State("map-mode", "value"),
        State("before-range", "start_date"),
        State("before-range", "end_date"),
        State("after-range", "start_date"),
        State("after-range", "end_date"),
        State("tod-window", "value"),
        prevent_initial_call=True,
    )
    def _export(_n, token, metric, mode, b0, b1, a0, a1, window):
        ds = _get(token)
        if ds is None:
            return "Load an export first."
        try:
            path = _write_kml(ds, metric, mode, (b0, b1), (a0, a1), window)
        except Exception as exc:
            return f"⚠ Export failed: {exc}"
        return f"✓ Wrote {path}"

    # Time-of-day window: a plain-language status line under the slider.
    @app.callback(
        Output("tod-status", "children"),
        Input("tod-window", "value"),
    )
    def _tod_label(window):
        if not window or (float(window[0]) <= 0 and float(window[1]) >= 24):
            return "Whole day — no time-of-day filter."
        return f"Analysing {_hour_label(window[0])} – {_hour_label(window[1])} only."


# --- panel builders (subset -> compute-core call -> figure) -----------------
def _hour_label(h) -> str:
    """A slider hour (``16``, ``17.5``, ``24``) as a 12-hour clock label."""
    from datetime import time as _time

    h = float(h)
    if h >= 24:
        return "12:00 AM"
    return _time(int(h), int(round((h - int(h)) * 60))).strftime("%I:%M %p").lstrip("0")


def _segment_df(ds: Dataset, selected, window=None) -> pd.DataFrame:
    if selected is None:
        return ds.df.iloc[0:0]
    sub = ds.df[ds.df[SEGMENT_COL] == int(selected)]
    return _apply_tod(sub, window)


def _fig_timeseries(ds, selected, col, before, after, name, window=None):
    sub = _segment_df(ds, selected, window)
    if sub.empty:
        return figures._blank("Pick or click a segment.")
    return figures.time_series(sub, col, title=name, before=before, after=after)


def _fig_summary(ds, selected, col, name, window=None):
    sub = _segment_df(ds, selected, window)
    if sub.empty:
        return figures._blank("Pick or click a segment.")
    binned = assign_time_bins(assign_day_group(sub), DEFAULT_TIME_BINS)
    summary = speed.segment_summary(binned, values=[col],
                                    group_cols=[DAY_GROUP_COL, TIME_BIN_COL])
    return figures.summary_bars(summary, col, title=name)


def _fig_beforeafter(ds, selected, col, before, after, have_periods, window=None):
    if not have_periods:
        return figures._blank("Set before and after date ranges.")
    comp = _compare_all(ds, col, before, after, window)
    return figures.beforeafter_forest(comp, labels=_labels(ds), selected_id=selected,
                                      value_label=figures._unit_label(col))


def _fig_decomp(ds, selected, col, name, window=None):
    sub = _segment_df(ds, selected, window)
    if sub.empty:
        return figures._blank("Pick or click a segment.")
    # Filter first, then decompose: the trend/changepoints describe the selected
    # window on its own terms. decompose_segments auto-scales its rolling-window
    # sample guard so a narrow window doesn't come back empty (see decompose.py).
    decomposed = decompose.decompose_segments(sub, value=col)
    adj = decompose.seasonally_adjust(decomposed, col)
    work = decomposed.copy()
    work[adj.name] = adj.to_numpy()
    cps = changepoint.detect_changepoints(work, value=adj.name)
    return figures.decomposition(decomposed, col, changepoints=cps, title=name)


def _write_kml(ds, metric, mode, before, after, window=None) -> Path:
    col = _metric_col(ds, metric)
    geo = ds.geo.copy()
    if mode == "delta" and all(before) and all(after):
        comp = _compare_all(ds, col, before, after, window)
        geo["metric"] = comp.set_index(SEGMENT_COL)["effect"].reindex(geo.index)
        color_by = "metric"
    else:
        geo["metric"] = _segment_means(ds, col, window).reindex(geo.index)
        color_by = "metric"
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    out = OUTPUT_DIR / "segments.kml"
    return kml.geometry_to_kml(geo, out, name_col="Combined", color_by=color_by,
                               label_segments=True, document_name="INRIX segments")


def _free_port(host: str, start: int, tries: int = 20) -> int:
    """The first free port at/after ``start`` (so a stale server on the default
    doesn't block startup). Falls back to ``start`` if none of the range is free."""
    import socket

    for port in range(start, start + tries):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            if s.connect_ex((host, port)) != 0:  # nothing listening -> free
                return port
    return start


app = build_app()  # module-level for `python gui/app.py` and WSGI servers

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="INRIX segment explorer (Dash).")
    parser.add_argument("--host", default=os.environ.get("HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", 8050)),
                        help="preferred port; the next free one is used if it's taken")
    parser.add_argument("--debug", action="store_true", default=bool(os.environ.get("DEBUG")))
    args = parser.parse_args()

    port = _free_port(args.host, args.port)
    if port != args.port:
        print(f"Port {args.port} is in use — serving on {port} instead.")
    app.run(host=args.host, port=port, debug=args.debug)

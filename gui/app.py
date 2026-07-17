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

from inrix_tools import beforeafter, changepoint, geometry, io, kml, names, speed  # noqa: E402
from inrix_tools.io import CORRIDOR_COL, DATETIME_COL, SEGMENT_COL  # noqa: E402
from inrix_tools.timebins import (  # noqa: E402
    assign_day_group,
    assign_time_bins,
    filter_date_range,
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

# Analysis scope (Item 12): the entity the time-series / before-after /
# decomposition panels run on. *Segment* = the map-selected segment. *Corridor* /
# *Network* run on a per-timestamp travel-time **aggregate** (segment sums under
# the complete-set rule) collapsed to one synthetic Segment ID, so the
# decompose/compare adapters — which group by Segment ID — run unchanged. The map
# and the day×time summary stay segment-level in every scope. Corridor/Network are
# travel-time-only (summing travel time across segments is well-defined; there is
# no good segment-weighting for speed).
SCOPE_SEGMENT, SCOPE_CORRIDOR, SCOPE_NETWORK = "segment", "corridor", "network"
_AGG_SCOPES = (SCOPE_CORRIDOR, SCOPE_NETWORK)
_AGG_SEGMENT_ID = -1  # synthetic entity id for a corridor/network aggregate series


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
    span: tuple                      # (min_date, max_date) local dates — after any date restriction
    full_span: tuple = None          # the untrimmed export span, for the "Restrict dates" picker bounds (Item 11)
    labels: dict = field(default_factory=dict)   # Segment ID -> friendly name (Item 10)
    # Seasonally-adjusted full-export frames, keyed by (metric col, ToD-window):
    # the expensive decomposition, cached independently of the before/after dates
    # so moving a date picker doesn't re-decompose (Item 14 review O1). Capped
    # (each entry is full-export sized). The per-period Welch stats computed off
    # it are cheap and cached in _compare_cache.
    _adjusted_cache: dict = field(default_factory=dict)
    _compare_cache: dict = field(default_factory=dict)

    def __post_init__(self):
        if self.full_span is None:
            self.full_span = self.span


_ADJ_CACHE_CAP = 2        # full-export sized frames — keep only a couple
_COMPARE_CACHE_CAP = 16   # tiny per-segment result frames — cheap to keep more

_DATASETS: dict[int, Dataset] = {}
_TOKEN = {"n": 0}


def _store(ds: Dataset) -> int:
    # Single-user localhost: keep only the latest load. Each Myrtle dataset is
    # ~2.3 GB (Item 14 review B2), so retaining every CValue tweak would OOM a
    # laptop. Dropping the old Dataset frees it (and its caches) for GC; the
    # client's data-token store is refreshed with the new token by _load.
    _TOKEN["n"] += 1
    _DATASETS.clear()
    _DATASETS[_TOKEN["n"]] = ds
    return _TOKEN["n"]


def _get(token) -> Dataset | None:
    return _DATASETS.get(token) if token is not None else None


# ---------------------------------------------------------------------------
# Compute wiring — thin calls into inrix_tools (no statistics defined here).
# ---------------------------------------------------------------------------
def load_dataset(source: str, tz: str, cvalue: int, shapefile: str = DEFAULT_SHAPEFILE,
                 names_path: str | None = None,
                 date_start=None, date_end=None) -> Dataset:
    """Load + localize + CValue-filter an export, join its segment geometry.

    ``names_path`` (optional) points at a user-edited names CSV
    (``names.load_names``); the resolved ``Segment ID -> friendly name`` mapping
    is stored on the ``Dataset`` and used everywhere a segment is labelled.

    ``date_start`` / ``date_end`` (optional, Item 11) restrict the session to an
    inclusive local calendar-date range via ``timebins.filter_date_range``, so
    ``Dataset.df`` really carries fewer rows (memory + downstream speed), not just
    a display filter. The **trimmed** span is recorded on ``span`` (the
    before/after and time-of-day controls clamp to it); the untrimmed export span
    is kept on ``full_span`` so the "Restrict dates" picker can widen again.
    """
    raw = io.load_data(source)
    local = io.to_local(raw, tz)
    full = io.filter_cvalue(local, cvalue)
    # Untrimmed span first (drives the Restrict-dates picker bounds), then trim.
    full_dates = full[DATETIME_COL].dt.date
    full_span = (full_dates.min(), full_dates.max())
    if date_start or date_end:
        filt = filter_date_range(full, date_start, date_end)
    else:
        filt = full
    meta = io.load_metadata(source)

    seg_ids = list(meta.index)
    cache = CACHE_DIR / f"{Path(source).stem}.geoparquet"
    net = geometry.load_xd_network(shapefile, segment_ids=seg_ids, cache_path=str(cache))
    geo = geometry.segment_geometry(net, segment_ids=seg_ids, metadata=meta)
    if "Combined" in meta.columns:
        geo["Combined"] = meta["Combined"].reindex(geo.index)

    # Friendly names (Item 10): seed from the INRIX labels, overlaid by the user's
    # CSV when supplied. A single mapping is the source of truth for the dropdown,
    # map hover title, forest rows, and panel titles; the raw Combined stays on
    # ``geo`` for the hover subtitle so nothing is lost.
    names_df = names.load_names(names_path) if names_path else None
    labels = names.apply_names(meta, names_df)
    geo["name"] = geo.index.map(lambda s: labels.get(int(s), f"Segment {int(s)}"))

    dates = filt[DATETIME_COL].dt.date
    # An over-narrow restriction can empty the frame; fall back to the full span
    # for display bounds rather than a NaN span (the panels handle an empty df).
    span = (dates.min(), dates.max()) if len(filt) else full_span
    return Dataset(
        df=filt, metadata=meta, geo=geo,
        metric_cols=speed.metric_columns(filt), tz=tz,
        span=span, full_span=full_span, labels=labels,
    )


def _metric_col(ds: Dataset, metric_key: str) -> str | None:
    return ds.metric_cols.get(_METRICS[metric_key])


def _metric_choices(ds: Dataset, current: str | None):
    """Radio options + a valid selection for the metric control, disabling any
    metric the export doesn't carry (Item 14 review B3: a speed-only export would
    otherwise ``KeyError`` on the default *Travel time*). Keeps ``current`` if it
    is still present; else lands on the first present metric."""
    have = {k: _metric_col(ds, k) is not None for k in _METRICS}
    options = [{"label": f" {_METRIC_LABEL[k]}", "value": k, "disabled": not have[k]}
               for k in ("tt", "speed")]
    if current in have and have[current]:
        value = current
    else:
        value = next((k for k in ("tt", "speed") if have[k]), current)
    return options, value


def _corridor_values(ds: Dataset) -> list:
    """The distinct ``Corridor/Region Name`` values in the export (sorted), or an
    empty list when the export carries no corridor column."""
    if CORRIDOR_COL not in ds.df.columns:
        return []
    return sorted(str(c) for c in ds.df[CORRIDOR_COL].dropna().unique())


def _scope_options(ds: Dataset):
    """Analysis-scope dropdown options + a corridor picker for the loaded export
    (Item 12). Corridor scope needs the ``Corridor/Region Name`` column; both
    aggregate scopes need a travel-time column (they analyse summed travel time).
    Returns ``(corridor_options, corridor_value, scope_options)``."""
    corridors = _corridor_values(ds)
    corr_opts = [{"label": c, "value": c} for c in corridors]
    corr_val = corridors[0] if corridors else None
    have_tt = _metric_col(ds, "tt") is not None
    scope_opts = [
        {"label": "Segment", "value": SCOPE_SEGMENT},
        {"label": "Corridor", "value": SCOPE_CORRIDOR,
         "disabled": not (have_tt and corridors)},
        {"label": "Network (all segments)", "value": SCOPE_NETWORK,
         "disabled": not have_tt},
    ]
    return corr_opts, corr_val, scope_opts


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


def _window_key(window):
    """A canonical cache key for a ToD window, collapsing every whole-day
    spelling (``None`` / ``[0, 24]`` / equal handles) to one ``"full"`` bucket so
    they share a cached decomposition."""
    if not window:
        return "full"
    h0, h1 = float(window[0]), float(window[1])
    if (h0 <= 0 and h1 >= 24) or h0 == h1:  # equal handles read as whole day too
        return "full"
    return (h0, h1)


def _scope_label(ds: Dataset, scope: str, corridor) -> str:
    """The friendly name shown for a corridor/network aggregate entity."""
    if scope == SCOPE_CORRIDOR:
        return str(corridor) if corridor else "—"
    return "Network (all segments)"


def _analysis_frame(ds: Dataset, col: str, scope: str, corridor, window=None) -> pd.DataFrame:
    """The tz-aware, ToD-filtered rows the analysis panels decompose/compare (Item 12).

    *Segment* scope returns ``ds.df`` (real Segment IDs). *Corridor* / *Network*
    scope returns the per-timestamp travel-time total
    (``speed.corridor_travel_time`` / ``network_travel_time``, complete-set rule)
    **collapsed to one synthetic Segment ID** (``_AGG_SEGMENT_ID``), so the
    decompose/compare adapters that group by Segment ID run on the aggregate series
    unchanged. ``col`` must be the travel-time column in the aggregate scopes (the
    GUI forces the metric there)."""
    df = _apply_tod(ds.df, window)
    if scope == SCOPE_SEGMENT:
        return df
    if scope == SCOPE_CORRIDOR:
        agg = speed.corridor_travel_time(df)  # length/speed not needed here
        agg = agg[agg[CORRIDOR_COL] == corridor]
    else:  # network
        agg = speed.network_travel_time(df)
    out = agg[[DATETIME_COL, col]].copy()
    out[SEGMENT_COL] = _AGG_SEGMENT_ID
    out.attrs = dict(df.attrs)
    return out


def _adjusted_frame(ds: Dataset, col: str, window=None, *,
                    scope: str = SCOPE_SEGMENT, corridor=None) -> pd.DataFrame:
    """The seasonally-adjusted frame for ``(col, window, scope, corridor)`` — the
    expensive decomposition, computed once and cached (cap ``_ADJ_CACHE_CAP``,
    LRU) so before/after date changes reuse it instead of re-decomposing
    (Item 14 review O1: 8.2 s/miss). Feeds both the before/after stats
    (``_compare_all``) and the decomposition tab (``_fig_decomp``). The scope keys
    the cache so segment vs corridor vs network don't collide (Item 12)."""
    key = (col, _window_key(window), scope, corridor)
    cache = ds._adjusted_cache
    if key in cache:
        cache[key] = cache.pop(key)  # touch -> most-recently-used
        return cache[key]
    frame = _analysis_frame(ds, col, scope, corridor, window)
    adjusted = beforeafter.adjust_for_periods(frame, value=col)
    cache[key] = adjusted
    while len(cache) > _ADJ_CACHE_CAP:
        cache.pop(next(iter(cache)))  # evict least-recently-used
    return adjusted


def _compare_all(ds: Dataset, col: str, before, after, window=None, *,
                 scope: str = SCOPE_SEGMENT, corridor=None) -> pd.DataFrame:
    """Before/after (decomposition primary) at the chosen scope. Segment scope
    returns one row per segment; corridor/network scope returns a single aggregate
    row. The costly decomposition is cached in ``_adjusted_frame``; here we only
    run the cheap per-period Welch stats off it, so a date-picker change is
    sub-second. Overlapping/invalid periods raise *before* any decomposition."""
    beforeafter.check_periods(before, after, ds.df[DATETIME_COL].dt.tz)  # fast-fail
    key = (col, str(before), str(after), _window_key(window), scope, corridor)
    cache = ds._compare_cache
    if key in cache:
        cache[key] = cache.pop(key)  # touch -> MRU
        return cache[key]
    adjusted = _adjusted_frame(ds, col, window, scope=scope, corridor=corridor)
    cache[key] = beforeafter.compare_adjusted(adjusted, before=before, after=after)
    while len(cache) > _COMPARE_CACHE_CAP:
        cache.pop(next(iter(cache)))
    return cache[key]


def _labels(ds: Dataset) -> dict:
    """The single ``Segment ID -> friendly name`` mapping (Item 10) driving the
    dropdown, map hover, forest rows, and panel titles. Resolved at load time by
    ``names.apply_names`` (seed simplified from the INRIX labels, overlaid by the
    user's names CSV)."""
    return ds.labels


DECOMP_WARMUP_DAYS = 7  # decompose_segments' drop_days default (see decompose.py)


def default_periods(lo, hi, warmup_days: int = DECOMP_WARMUP_DAYS):
    """Default before/after windows for an export spanning ``[lo, hi]`` (local
    dates): **disjoint** halves of the span, starting after the decomposition
    warm-up, clamped to the span. The old defaults (fixed ~5-week windows)
    silently overlapped for any span under ~60 days, biasing every default
    effect toward zero — Item 14 review B1; this replaces them.

    Returns ``(b_start, b_end, a_start, a_end)`` dates with
    ``lo <= b_start <= b_end < a_start <= a_end <= hi`` (the ranges are
    inclusive calendar days, per ``parse_period``), or all ``None`` when the
    span can't fit two disjoint one-day windows.
    """
    span = (hi - lo).days
    if span < 2:
        return None, None, None, None
    warmup = min(warmup_days, span - 2)  # never eat the whole span
    u_lo = lo + timedelta(days=warmup)
    usable = (hi - u_lo).days            # >= 2 by construction
    half = usable // 2
    b_start, b_end = u_lo, u_lo + timedelta(days=max(half - 1, 0))
    a_start, a_end = b_end + timedelta(days=1), hi
    return b_start, b_end, a_start, a_end


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
        dbc.Label("Names CSV (optional)", className="mt-2"),
        dbc.Input(id="names-path", value="", size="sm", debounce=True,
                  placeholder="path to an edited segment_names.csv"),
        # Restrict-dates (Item 11): trim the session to a calendar sub-range on
        # Load so every downstream compute runs on the smaller frame. Defaults to
        # the full export span (a no-op) once loaded; narrow it and re-Load to trim.
        dbc.Label("Restrict dates (optional)", className="mt-2"),
        dcc.DatePickerRange(id="restrict-range", className="d-block",
                            display_format="YYYY-MM-DD"),
        dbc.Button("Load export", id="load", color="primary", size="sm", className="mt-2 w-100"),
        html.Div(id="load-status", className="small text-muted mt-1"),
        dbc.Button("Write name template", id="write-names", color="link", size="sm",
                   className="p-0 mt-1"),
        html.Div(id="names-status", className="small text-muted"),
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
        # Analysis scope (Item 12): run the time-series / before-after /
        # decomposition panels on a single segment, a corridor sum, or the whole
        # network. Corridor/Network are travel-time only; the map + day×time
        # summary stay segment-level.
        html.H6("Analysis scope", className="text-muted"),
        dcc.Dropdown(id="scope", clearable=False, value=SCOPE_SEGMENT,
                     options=[{"label": "Segment", "value": SCOPE_SEGMENT},
                              {"label": "Corridor", "value": SCOPE_CORRIDOR},
                              {"label": "Network (all segments)", "value": SCOPE_NETWORK}]),
        dbc.Label("Corridor", className="mt-2"),
        dcc.Dropdown(id="corridor", clearable=False, placeholder="Load an export"),
        html.Div("Corridor / Network analyse summed travel time (travel time only).",
                 className="small text-muted mt-1"),
        html.Hr(),
        html.H6("Time-of-day window", className="text-muted"),
        # Equal handles (start == end) mean *whole day*, not an empty window —
        # matching timebins.filter_time_window; the status line below says so.
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
        Output("metric", "options"),
        Output("metric", "value"),
        Output("before-range", "min_date_allowed"),
        Output("before-range", "max_date_allowed"),
        Output("before-range", "start_date"),
        Output("before-range", "end_date"),
        Output("after-range", "min_date_allowed"),
        Output("after-range", "max_date_allowed"),
        Output("after-range", "start_date"),
        Output("after-range", "end_date"),
        Output("restrict-range", "min_date_allowed"),
        Output("restrict-range", "max_date_allowed"),
        Output("restrict-range", "start_date"),
        Output("restrict-range", "end_date"),
        Output("corridor", "options"),
        Output("corridor", "value"),
        Output("scope", "options"),
        Input("load", "n_clicks"),
        State("source", "value"),
        State("tz", "value"),
        State("cvalue", "value"),
        State("metric", "value"),
        State("names-path", "value"),
        State("restrict-range", "start_date"),
        State("restrict-range", "end_date"),
        prevent_initial_call=True,
    )
    def _load(_n, source, tz, cvalue, metric, names_path, date_start, date_end):
        # A cleared CValue input arrives as None/"" — default it rather than let
        # int(None) blow up with a cryptic message (Item 14 review B6).
        cv = DEFAULT_CVALUE if cvalue in (None, "") else int(cvalue)
        try:
            # date_start/date_end come from the Restrict-dates picker (Item 11):
            # trim the session to that calendar sub-range so the cached frame,
            # and every downstream compute, is smaller. Empty on the first load
            # (no restriction) until the picker is populated below.
            ds = load_dataset(source, tz or DEFAULT_TZ, cv,
                              names_path=names_path or None,
                              date_start=date_start, date_end=date_end)
        except Exception as exc:  # surface the failure in the UI, don't crash
            return (no_update, f"⚠ Load failed: {exc}", *[no_update] * 19)
        token = _store(ds)
        labels = _labels(ds)
        options = [{"label": labels.get(int(s), f"Segment {int(s)}"), "value": int(s)}
                   for s in ds.metadata.index]
        # Metric guard (Item 14 review B3): a speed-only or travel-time-only export
        # would KeyError on the absent metric. Disable the missing radio option and
        # land on a present metric.
        metric_options, metric_value = _metric_choices(ds, metric)
        lo, hi = ds.span
        # Default windows: disjoint halves of the (possibly trimmed) span after the
        # decomposition warm-up (overlap biases effects toward zero — review B1).
        b_start, b_end, a_start, a_end = default_periods(lo, hi)
        # Restrict-dates picker: bounds are the *untrimmed* export span (so the
        # user can widen again), start/end echo the applied restriction, defaulting
        # to the full span when none is set (Item 11).
        full_lo, full_hi = ds.full_span
        r_start = date_start or full_lo
        r_end = date_end or full_hi
        trimmed = (lo, hi) != (full_lo, full_hi)
        status = (f"✓ {len(ds.df):,} rows · {len(ds.metadata)} segments · "
                  f"{lo}…{hi}{' (restricted)' if trimmed else ''} · {ds.tz}")
        # Analysis-scope options (Item 12): corridor needs the Corridor/Region Name
        # column; both aggregate scopes need a travel-time column (travel-time only).
        corr_opts, corr_val, scope_opts = _scope_options(ds)
        # Reset the segment selection: a new export's ids differ, so keeping the
        # old value would leave every panel on a stale/blank segment (review B5).
        return (token, status, options, None, metric_options, metric_value,
                lo, hi, b_start, b_end, lo, hi, a_start, a_end,
                full_lo, full_hi, r_start, r_end,
                corr_opts, corr_val, scope_opts)

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

    # Analysis scope -> metric radio (Item 12): corridor/network are travel-time
    # only, so disable Speed and force Travel time; segment scope restores the
    # export's available metrics. Keeps the radio honest about what the panels show.
    @app.callback(
        Output("metric", "options", allow_duplicate=True),
        Output("metric", "value", allow_duplicate=True),
        Input("scope", "value"),
        State("data-token", "data"),
        State("metric", "value"),
        prevent_initial_call=True,
    )
    def _scope_metric(scope, token, metric):
        ds = _get(token)
        if ds is None:
            return no_update, no_update
        if scope not in _AGG_SCOPES:
            return _metric_choices(ds, metric)  # restore available metrics
        have_tt = _metric_col(ds, "tt") is not None
        options = [{"label": " Travel time", "value": "tt", "disabled": not have_tt},
                   {"label": " Speed", "value": "speed", "disabled": True}]
        return options, "tt"

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
        Input("scope", "value"),
    )
    def _map(token, metric, mode, selected, b0, b1, a0, a1, window, scope):
        ds = _get(token)
        if ds is None:
            return figures.segment_map(_empty_geo())
        # The map stays segment-level in every scope; corridor/network force
        # travel time (they have no per-segment speed to colour by).
        col = _metric_col(ds, "tt" if scope in _AGG_SCOPES else metric)
        if col is None:  # metric absent (mid-load transition) — draw a bare map
            return figures.segment_map(ds.geo, label_col="name",
                                       sublabel_col="Combined", uirevision=f"data-{token}")
        label = figures._unit_label(col)
        if mode == "delta" and all([b0, b1, a0, a1]):
            try:
                comp = _compare_all(ds, col, (b0, b1), (a0, a1), window)
            except ValueError:  # e.g. overlapping periods — fall back to means
                comp = None
            if comp is not None and not comp.empty:
                values = comp.set_index(SEGMENT_COL)["effect"]
                label = f"Δ {label}"
            else:
                values = _segment_means(ds, col, window)
        else:
            values = _segment_means(ds, col, window)
        # Key uirevision on the data token so loading a *different* export
        # recenters the map instead of keeping the old city's pan/zoom (review B5).
        return figures.segment_map(ds.geo, values, value_label=label,
                                   selected_id=selected, label_col="name",
                                   sublabel_col="Combined", uirevision=f"data-{token}")

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
        Input("scope", "value"),
        Input("corridor", "value"),
    )
    def _panels(tab, token, selected, metric, b0, b1, a0, a1, window, scope, corridor):
        ds = _get(token)
        blank = figures._blank("Load an export to begin.")
        if ds is None:
            return blank, blank, blank, blank
        # Corridor/Network scope analyses summed travel time (segment sums have no
        # meaningful speed weighting), so force the metric there.
        col = _metric_col(ds, "tt" if scope in _AGG_SCOPES else metric)
        if col is None:  # metric absent (mid-load transition) — nothing to plot yet
            miss = figures._blank("This export doesn't carry the selected metric.")
            return miss, miss, miss, miss
        before, after = (b0, b1), (a0, a1)
        have_periods = all([b0, b1, a0, a1])
        name = _labels(ds).get(int(selected), f"Segment {selected}") if selected is not None else ""
        out = {"ts": no_update, "summary": no_update, "ba": no_update, "decomp": no_update}

        if tab == "ts":
            out["ts"] = _fig_timeseries(ds, selected, col, before, after, name, window,
                                        scope=scope, corridor=corridor)
        elif tab == "summary":
            # The day×time summary stays segment-level in every scope (a segment
            # sum has no day-group×time-bin decomposition of its own here).
            out["summary"] = _fig_summary(ds, selected, col, name, window)
        elif tab == "ba":
            out["ba"] = _fig_beforeafter(ds, selected, col, before, after, have_periods,
                                         window, scope=scope, corridor=corridor)
        elif tab == "decomp":
            out["decomp"] = _fig_decomp(ds, selected, col, name, window,
                                        scope=scope, corridor=corridor)
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

    # Write a friendly-name template CSV from the loaded metadata (Item 10). The
    # user edits it, then puts its path in "Names CSV" and reloads.
    @app.callback(
        Output("names-status", "children"),
        Input("write-names", "n_clicks"),
        State("data-token", "data"),
        prevent_initial_call=True,
    )
    def _write_names(_n, token):
        ds = _get(token)
        if ds is None:
            return "Load an export first."
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        out = OUTPUT_DIR / "segment_names.csv"
        try:
            path = names.write_names_template(ds.metadata, out)
        except Exception as exc:
            return f"⚠ Failed: {exc}"
        return f"✓ Wrote {path} — edit it, then set Names CSV and reload."

    # Time-of-day window: a plain-language status line under the slider.
    @app.callback(
        Output("tod-status", "children"),
        Input("tod-window", "value"),
    )
    def _tod_label(window):
        if not window:
            return "Whole day — no time-of-day filter."
        h0, h1 = float(window[0]), float(window[1])
        # Equal handles read as an empty window on the slider, but the core
        # (timebins.filter_time_window) treats start == end as the *whole day*;
        # say so rather than "Analysing 2:00 PM – 2:00 PM only" (review B6).
        if (h0 <= 0 and h1 >= 24) or h0 == h1:
            return "Whole day — no time-of-day filter."
        return f"Analysing {_hour_label(h0)} – {_hour_label(h1)} only."


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


def _agg_guard(scope, corridor):
    """Return a blank-figure message if a corridor scope has no corridor picked
    yet, else ``None`` (scope is ready to compute)."""
    if scope == SCOPE_CORRIDOR and not corridor:
        return figures._blank("Pick a corridor.")
    return None


def _fig_timeseries(ds, selected, col, before, after, name, window=None, *,
                    scope=SCOPE_SEGMENT, corridor=None):
    if scope in _AGG_SCOPES:
        if (g := _agg_guard(scope, corridor)) is not None:
            return g
        sub = _analysis_frame(ds, col, scope, corridor, window)
        name = _scope_label(ds, scope, corridor)
        empty_msg = "No complete-set timestamps in this range."
    else:
        sub = _segment_df(ds, selected, window)
        empty_msg = "Pick or click a segment."
    if sub.empty:
        return figures._blank(empty_msg)
    return figures.time_series(sub, col, title=name, before=before, after=after)


def _fig_summary(ds, selected, col, name, window=None):
    sub = _segment_df(ds, selected, window)
    if sub.empty:
        return figures._blank("Pick or click a segment.")
    binned = assign_time_bins(assign_day_group(sub), DEFAULT_TIME_BINS)
    summary = speed.segment_summary(binned, values=[col],
                                    group_cols=[DAY_GROUP_COL, TIME_BIN_COL])
    return figures.summary_bars(summary, col, title=name)


def _fig_beforeafter(ds, selected, col, before, after, have_periods, window=None, *,
                     scope=SCOPE_SEGMENT, corridor=None):
    if not have_periods:
        return figures._blank("Set before and after date ranges.")
    if (g := _agg_guard(scope, corridor)) is not None:
        return g
    try:
        comp = _compare_all(ds, col, before, after, window, scope=scope, corridor=corridor)
    except ValueError as exc:  # e.g. overlapping periods — validation, not a crash
        return figures._blank(f"⚠ {exc}")
    if scope in _AGG_SCOPES:
        # A single aggregate row: label the synthetic entity with the scope name.
        labels, sel = {_AGG_SEGMENT_ID: _scope_label(ds, scope, corridor)}, _AGG_SEGMENT_ID
    else:
        labels, sel = _labels(ds), selected
    return figures.beforeafter_forest(comp, labels=labels, selected_id=sel,
                                      value_label=figures._unit_label(col))


def _fig_decomp(ds, selected, col, name, window=None, *,
                scope=SCOPE_SEGMENT, corridor=None):
    if scope in _AGG_SCOPES:
        if (g := _agg_guard(scope, corridor)) is not None:
            return g
        entity, name = _AGG_SEGMENT_ID, _scope_label(ds, scope, corridor)
    elif selected is None:
        return figures._blank("Pick or click a segment.")
    else:
        entity = int(selected)
    # Reuse the cached decomposition and slice to this entity: the decomposition
    # groups by Segment ID, so one entity's rows are identical whether the frame
    # held it alone or with others. Filter-first (via the window key) means the
    # trend/changepoints describe the selected window; decompose_segments
    # auto-scales its rolling-window sample guard so a narrow window doesn't come
    # back empty (see decompose.py).
    adjusted = _adjusted_frame(ds, col, window, scope=scope, corridor=corridor)
    seg = adjusted[adjusted[SEGMENT_COL] == entity]
    if seg.empty or col not in seg.columns:
        return figures._blank(
            "No decomposition for this selection — a segment needs more than the "
            "7-day rolling-window warm-up before any adjusted values remain."
        )
    cps = changepoint.detect_changepoints(seg, value=beforeafter.ADJUSTED_COL)
    return figures.decomposition(seg, col, changepoints=cps, title=name)


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

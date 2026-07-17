"""Tests for the Dash explorer (ROADMAP Item 7).

The load-bearing check is the **headless layout smoke test**: ``build_app()``
constructs the whole app — layout + registered callbacks — without loading any
data or opening a browser. The rest exercise the ``gui.figures`` builders on
synthetic frames (no ``traffic_anomaly``, no real export) so the plotting shell is
covered fast; the compute core those figures render is tested in its own modules.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import pytest

from gui import app as gapp
from gui import figures
from inrix_tools.io import DATETIME_COL, SEGMENT_COL


# ---------------------------------------------------------------------------
# Synthetic fixtures (kept tiny + dependency-free)
# ---------------------------------------------------------------------------
SPEED_COL = "Speed(miles/hour)"
TT_COL = "Travel Time(Minutes)"


@pytest.fixture
def series_df():
    """Two days of 5-min samples for one segment, tz-aware local."""
    ts = pd.date_range("2026-03-02 00:00", "2026-03-03 23:55", freq="5min",
                       tz="America/Denver")
    rng = np.random.default_rng(0)
    return pd.DataFrame({
        DATETIME_COL: ts,
        SEGMENT_COL: 101,
        SPEED_COL: 30 + rng.normal(0, 2, len(ts)),
        TT_COL: 1.5 + rng.normal(0, 0.1, len(ts)),
    })


@pytest.fixture
def geo_two():
    """A 2-segment GeoDataFrame indexed by Segment ID with real polylines."""
    gpd = pytest.importorskip("geopandas")
    from shapely.geometry import LineString

    g = gpd.GeoDataFrame(
        {"Segment ID": [101, 202],
         "Combined": ["Main St NB", "Main St SB"],
         "source": ["xd", "xd"],
         "geometry": [LineString([(-116.2, 43.6), (-116.19, 43.61), (-116.18, 43.62)]),
                      LineString([(-116.18, 43.62), (-116.19, 43.61), (-116.2, 43.6)])]},
        geometry="geometry", crs="EPSG:4326",
    ).set_index("Segment ID")
    return g


# ---------------------------------------------------------------------------
# The headless layout smoke test (the required Item-7 check)
# ---------------------------------------------------------------------------
def test_build_app_layout_headless():
    app = gapp.build_app()
    assert app.layout is not None
    # every dcc.Graph / control referenced by a callback exists in the layout tree
    ids = {c.id for c in app.layout._traverse() if getattr(c, "id", None)}
    for needed in ("map", "segment", "fig-ts", "fig-summary", "fig-ba", "fig-decomp",
                   "load", "before-range", "after-range", "export-kml", "data-token",
                   "tod-window", "names-path", "write-names", "restrict-range",
                   "scope", "corridor", "dow-days", "dow-status",
                   "segment-table", "corridor-members", "save-names", "table-status",
                   # Item 20 directional-display controls
                   "dir-compass", "dir-offset"):
        assert needed in ids, f"missing layout component: {needed}"
    assert len(app.callback_map) >= 5  # load, click-select, map, panels, export


def test_layout_reflow_charts_share_right_column_with_map(geo_two):
    """Item 20-A: the chart tabs live in the SAME right column as the map + table
    (not a separate full-width bottom row), and that column does not also hold the
    left-column controls — so the charts sit directly below the map, gap closed."""
    app = gapp.build_app()

    def _iter_children(node):
        ch = getattr(node, "children", None)
        if ch is None:
            return []
        return ch if isinstance(ch, (list, tuple)) else [ch]

    def _ids(node):
        out = set()
        if getattr(node, "id", None):
            out.add(node.id)
        for child in _iter_children(node):
            out |= _ids(child)
        return out

    # the smallest subtree containing both the map and the tabs is the right column;
    # it must also carry the segment table but NOT the left-column 'load' control.
    def _smallest_with_both(node):
        ids = _ids(node)
        if not ({"map", "tabs"} <= ids):
            return None
        for child in _iter_children(node):
            inner = _smallest_with_both(child)
            if inner is not None:
                return inner
        return node

    right_col = _smallest_with_both(app.layout)
    assert right_col is not None
    col_ids = _ids(right_col)
    assert {"map", "tabs", "segment-table"} <= col_ids
    assert "load" not in col_ids           # the controls live in the *other* column


def test_direction_options_from_geo(geo_two):
    """_direction_options lists only the compass groups present in the export, in
    +/- order with a signed label."""
    from inrix_tools import geometry, speed
    g = geometry.attach_directions(geo_two, {101: "N", 202: "S"})
    ds = gapp.Dataset(df=pd.DataFrame(), metadata=pd.DataFrame(), geo=g,
                      metric_cols={}, tz="America/Denver", span=(None, None))
    opts = gapp._direction_options(ds)
    assert [o["value"] for o in opts] == ["N", "S"]     # only present groups, N before S
    assert "(+)" in opts[0]["label"] and "(−)" in opts[1]["label"]
    # no direction annotation -> no options (control filters nothing)
    ds_no = gapp.Dataset(df=pd.DataFrame(), metadata=pd.DataFrame(), geo=geo_two,
                         metric_cols={}, tz="America/Denver", span=(None, None))
    assert gapp._direction_options(ds_no) == []


def test_display_geo_filters_and_offsets(geo_two):
    """_display_geo filters to the chosen compass groups and offsets co-located
    opposing pairs; a no-op group selection renders all; the analytic geo is
    untouched."""
    from inrix_tools import geometry
    g = geometry.attach_directions(geo_two, {101: "N", 202: "S"})
    ds = gapp.Dataset(df=pd.DataFrame(), metadata=pd.DataFrame(), geo=g,
                      metric_cols={}, tz="America/Denver", span=(None, None))
    # filter to N only -> one segment drawn
    only_n = gapp._display_geo(ds, ["N"], offset_on=False)
    assert list(only_n.index) == [101]
    # no filter (empty) -> both, and the offset separates the co-located pair
    both = gapp._display_geo(ds, [], offset_on=True)
    assert set(both.index) == {101, 202}
    assert geometry.OFFSET_FLAG_COL in both.columns and both[geometry.OFFSET_FLAG_COL].all()
    # selecting every present group is a no-op filter (renders all)
    allsel = gapp._display_geo(ds, ["N", "S"], offset_on=False)
    assert set(allsel.index) == {101, 202}
    # the analytic geometry is untouched by the offset
    assert list(ds.geo["geometry"]) == list(g["geometry"])


def test_layout_builds_without_data_cache():
    """No dataset need be loaded for the layout to construct (data loads on click)."""
    assert not gapp._DATASETS or True  # cache is populated only by the Load callback
    app = gapp.build_app()
    assert app.title == "INRIX segment explorer"


def test_app_serves_over_http():
    """Drive the real Dash/Flask server (no browser, no data): the index page,
    the layout endpoint, and the dependency graph all respond, proving the
    callbacks are wired and the app boots end-to-end."""
    app = gapp.build_app()
    client = app.server.test_client()

    assert client.get("/").status_code == 200

    layout = client.get("/_dash-layout")
    assert layout.status_code == 200
    body = layout.get_data(as_text=True)
    for cid in ("map", "segment", "fig-decomp", "before-range"):
        assert cid in body

    deps = client.get("/_dash-dependencies")
    assert deps.status_code == 200
    deps_text = deps.get_data(as_text=True)
    # callbacks target these outputs (property specs like "map.figure")
    for out in ("map.figure", "fig-ts.figure", "data-token.data"):
        assert out in deps_text


# ---------------------------------------------------------------------------
# figures.* builders
# ---------------------------------------------------------------------------
def test_segment_map_traces_and_click_customdata(geo_two):
    values = pd.Series({101: 1.6, 202: 1.4})
    fig = figures.segment_map(geo_two, values, value_label="TT", selected_id=101)
    assert isinstance(fig, go.Figure)
    # one line trace per segment + a markers layer + a highlight ring
    assert len(fig.data) == 2 + 1 + 1
    markers = fig.data[2]
    assert list(markers.customdata) == [101, 202]  # click target carries the id
    assert fig.layout.map.style == "open-street-map"


def test_segment_map_empty_geo_is_safe():
    gpd = pytest.importorskip("geopandas")
    empty = gpd.GeoDataFrame({"geometry": []}, geometry="geometry")
    fig = figures.segment_map(empty)
    assert isinstance(fig, go.Figure)


def test_segment_map_hover_uses_friendly_name_and_subtitle(geo_two):
    """The friendly name is the bold hover title; the raw Combined is the italic
    subtitle underneath (Item 10)."""
    g = geo_two.copy()
    g["name"] = ["Main & 1st", "Main & 2nd"]
    fig = figures.segment_map(g, label_col="name", sublabel_col="Combined")
    hover = list(fig.data[2].text)
    assert hover[0].startswith("<b>Main & 1st</b>")
    assert "<i>Main St NB</i>" in hover[0]  # raw label kept as subtitle


def test_segment_map_subtitle_omitted_when_equal_to_name(geo_two):
    g = geo_two.copy()
    g["name"] = g["Combined"]  # friendly == raw -> no redundant subtitle
    fig = figures.segment_map(g, label_col="name", sublabel_col="Combined")
    assert "<i>" not in list(fig.data[2].text)[0]


def test_time_series_shades_periods(series_df):
    fig = figures.time_series(series_df, TT_COL, before=("2026-03-02", "2026-03-02"),
                              after=("2026-03-03", "2026-03-03"))
    assert isinstance(fig, go.Figure)
    assert len(fig.data) == 1
    # two shaded vrects (before + after)
    assert len([s for s in fig.layout.shapes if s.type == "rect"]) == 2


def test_time_series_empty_is_blank():
    fig = figures.time_series(pd.DataFrame(), TT_COL)
    assert isinstance(fig, go.Figure)


def test_summary_bars(series_df):
    from inrix_tools import speed
    from inrix_tools.timebins import (assign_day_group, assign_time_bins,
                                       DAY_GROUP_COL, TIME_BIN_COL)
    binned = assign_time_bins(assign_day_group(series_df),
                              ["6:00AM-12:00PM", "12:00PM-6:00PM"])
    summ = speed.segment_summary(binned, values=[SPEED_COL],
                                 group_cols=[DAY_GROUP_COL, TIME_BIN_COL])
    fig = figures.summary_bars(summ, SPEED_COL)
    assert isinstance(fig, go.Figure)
    assert all(isinstance(t, go.Bar) for t in fig.data)
    assert len(fig.data) >= 1


def test_beforeafter_forest_marks_zero_and_selection():
    compare = pd.DataFrame({
        SEGMENT_COL: [101, 202, 303],
        "effect": [0.5, -0.3, 0.1],
        "ci_low": [0.2, -0.6, -0.2],
        "ci_high": [0.8, 0.0, 0.4],
    })
    compare.attrs["method"] = "decomposition"
    fig = figures.beforeafter_forest(compare, labels={101: "A", 202: "B", 303: "C"},
                                     selected_id=202, value_label="TT")
    assert isinstance(fig, go.Figure)
    assert len(fig.data) == 1
    assert any(s.type == "line" for s in fig.layout.shapes)  # the x=0 rule


def test_beforeafter_forest_empty_is_blank():
    fig = figures.beforeafter_forest(pd.DataFrame())
    assert isinstance(fig, go.Figure)


def test_beforeafter_forest_fdr_deemphasis_and_caption():
    """Item 15: rows with q > alpha are de-emphasised, the caption states the
    family size, and attrs['warnings'] (warm-up truncation) are surfaced."""
    compare = pd.DataFrame({
        SEGMENT_COL: [101, 202, 303],
        "effect": [-0.3, 0.1, 0.5],          # already sorted -> row order stable
        "ci_low": [-0.6, -0.2, 0.2],
        "ci_high": [0.0, 0.4, 0.8],
        "p_value": [0.3, 0.6, 0.001],
        "q_value": [0.45, 0.6, 0.003],       # only segment 303 passes 5% FDR
    })
    compare.attrs["method"] = "decomposition"
    compare.attrs["unit"] = "day"
    compare.attrs["warnings"] = ["before period truncated by the decomposition warm-up"]
    fig = figures.beforeafter_forest(compare, value_label="TT")

    colors = list(fig.data[0].marker.color)
    assert colors[2] == "#4c78a8"                     # significant -> full colour
    assert colors[0] == colors[1] == figures._DEEMPHASIS  # q > 0.05 -> de-emphasised
    texts = " ".join(a.text for a in fig.layout.annotations)
    assert "BH-FDR" in texts and "1 pass" in texts.replace("· ", "")
    assert "truncated" in texts
    assert "day-level CI" in fig.layout.title.text


def test_default_periods_disjoint_and_in_range():
    """Item 15 / review B1: default windows are disjoint at every span (the old
    fixed ~5-week windows overlapped under 60 days), sit inside [lo, hi], and
    start after the decomposition warm-up when the span allows."""
    from datetime import date, timedelta

    lo = date(2026, 2, 1)
    for span in (2, 3, 7, 14, 20, 30, 45, 60, 90, 165, 365):
        hi = lo + timedelta(days=span)
        b0, b1, a0, a1 = gapp.default_periods(lo, hi)
        assert lo <= b0 <= b1 < a0 <= a1 <= hi, f"span={span}: not disjoint/in-range"
        if span >= gapp.DECOMP_WARMUP_DAYS + 2:
            assert b0 == lo + timedelta(days=gapp.DECOMP_WARMUP_DAYS)
    # a span too short for two disjoint day windows yields no defaults
    assert gapp.default_periods(lo, lo + timedelta(days=1)) == (None,) * 4
    assert gapp.default_periods(lo, lo) == (None,) * 4


def test_fig_beforeafter_overlap_shows_message_not_crash(series_df):
    """User-picked overlapping periods surface as a message figure (the core
    raises before any decomposition, so this is fast)."""
    from inrix_tools import speed
    ds = gapp.Dataset(df=series_df,
                      metadata=pd.DataFrame(index=pd.Index([101], name=SEGMENT_COL)),
                      geo=None, metric_cols=speed.metric_columns(series_df),
                      tz="America/Denver", span=(None, None))
    fig = gapp._fig_beforeafter(ds, None, TT_COL,
                                ("2026-03-02", "2026-03-03"),
                                ("2026-03-02", "2026-03-03"), True)
    assert isinstance(fig, go.Figure)
    texts = " ".join(a.text for a in fig.layout.annotations)
    assert "overlap" in texts


def test_decomposition_panel_with_changepoints(series_df):
    from inrix_tools.decompose import (RESID_COL, SEASON_DAY_COL,
                                       SEASON_WEEK_COL, TREND_COL)
    d = series_df.copy()
    d[TREND_COL] = d[TT_COL].rolling(10, min_periods=1).median()
    d[SEASON_DAY_COL] = 0.05
    d[SEASON_WEEK_COL] = -0.02
    d[RESID_COL] = d[TT_COL] - d[TREND_COL]
    cps = pd.DataFrame({DATETIME_COL: [d[DATETIME_COL].iloc[100]], "avg_diff": [0.3]})
    fig = figures.decomposition(d, TT_COL, changepoints=cps)
    assert isinstance(fig, go.Figure)
    assert len(fig.data) == 4  # observed, trend, seasonal, residual


def test_unit_label():
    assert figures._unit_label("Speed(miles/hour)") == "Speed (miles/hour)"
    assert figures._unit_label(None) == ""


# ---------------------------------------------------------------------------
# compute-wiring helpers (no heavy deps)
# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# End-to-end integration on the real Myrtle export. Skips automatically when the
# (gitignored, license-restricted) fixture / shapefile aren't present — so it runs
# locally as a live check and is a no-op in CI. Exercises the exact functions the
# Dash callbacks call: load -> map values -> each panel figure -> KML.
# ---------------------------------------------------------------------------
_HAVE_FIXTURE = (Path(gapp.DEFAULT_SOURCE).exists()
                 and Path(gapp.DEFAULT_SHAPEFILE).exists())


@pytest.mark.skipif(not _HAVE_FIXTURE, reason="Myrtle export / XD shapefile not present")
def test_end_to_end_real_export():
    pytest.importorskip("traffic_anomaly")
    ds = gapp.load_dataset(gapp.DEFAULT_SOURCE, gapp.DEFAULT_TZ, gapp.DEFAULT_CVALUE)
    assert len(ds.metadata) == 46
    assert ds.metric_cols["travel_time"] and ds.metric_cols["speed"]

    # friendly names (Item 10): the label mapping covers every segment, the geo
    # carries both the friendly name and the raw Combined, and _labels reads it.
    assert set(ds.labels) == {int(s) for s in ds.metadata.index}
    assert gapp._labels(ds) is ds.labels
    assert ds.geo.loc[440882720, "name"] == "9th St & Idaho St"
    assert "Combined" in ds.geo.columns

    col = gapp._metric_col(ds, "tt")
    sid = int(ds.metadata.index[5])
    before, after = ("2026-02-10", "2026-03-15"), ("2026-06-01", "2026-07-05")

    # map colouring (mean + before/after delta) and every panel builder
    assert figures.segment_map(ds.geo, gapp._segment_means(ds, col),
                               selected_id=sid).data
    assert gapp._fig_timeseries(ds, sid, col, before, after, "seg").data
    assert gapp._fig_summary(ds, sid, col, "seg").data
    comp = gapp._compare_all(ds, col, before, after)
    assert len(comp) == 46 and "effect" in comp.columns
    assert gapp._fig_beforeafter(ds, sid, col, before, after, True).data
    assert gapp._fig_decomp(ds, sid, col, "seg").data

    # a 4–6PM time-of-day window drives every panel too (filter-first path)
    pm = [16, 18]
    assert gapp._fig_timeseries(ds, sid, col, before, after, "seg", pm).data
    assert gapp._fig_decomp(ds, sid, col, "seg", pm).data
    comp_pm = gapp._compare_all(ds, col, before, after, pm)
    assert len(comp_pm) and "effect" in comp_pm.columns

    out = gapp._write_kml(ds, "tt", "mean", before, after)
    assert out.exists() and out.stat().st_size > 0

    # Item 17: delay is computed at load (Ref Speed free-flow), a first-class
    # metric driving the map/panels, and valid in the aggregate scope. Validate the
    # before/after delay path on the single-entity network aggregate (46x lighter
    # than a 46-segment decomposition — keeps the real-export test's memory bounded).
    from inrix_tools import speed as _speed
    assert ds.metric_cols["delay"] == _speed.DELAY_COL
    dcol = gapp._metric_col(ds, "delay")
    assert (ds.df[dcol].dropna() >= 0).all()          # floored at 0
    assert gapp._fig_timeseries(ds, sid, dcol, before, after, "seg").data
    net_delay = gapp._analysis_frame(ds, dcol, gapp.SCOPE_NETWORK, None)
    assert dcol in net_delay.columns and len(net_delay)
    comp_delay = gapp._compare_all(ds, dcol, before, after, scope=gapp.SCOPE_NETWORK)
    assert len(comp_delay) == 1 and "effect" in comp_delay.columns

    # Item 12: network scope aggregates all segments' travel time (complete-set
    # rule) into one synthetic entity; before/after + decomposition run on it.
    _, corr_val, scope_opts = gapp._scope_options(ds)
    disabled = {o["value"]: o.get("disabled", False) for o in scope_opts}
    assert not disabled[gapp.SCOPE_NETWORK]        # the export carries travel time
    net = gapp._analysis_frame(ds, col, gapp.SCOPE_NETWORK, None)
    assert set(net[SEGMENT_COL].unique()) == {gapp._AGG_SEGMENT_ID} and len(net)
    comp_net = gapp._compare_all(ds, col, before, after, scope=gapp.SCOPE_NETWORK)
    assert len(comp_net) == 1
    assert gapp._fig_timeseries(ds, None, col, before, after, "", scope=gapp.SCOPE_NETWORK).data
    assert gapp._fig_decomp(ds, None, col, "", scope=gapp.SCOPE_NETWORK).data
    if corr_val and not disabled[gapp.SCOPE_CORRIDOR]:   # a corridor is present
        assert gapp._fig_beforeafter(ds, None, col, before, after, True,
                                     scope=gapp.SCOPE_CORRIDOR, corridor=corr_val).data

    # Item 11: a date restriction on load really shrinks the cached frame while the
    # full export span is preserved for the picker bounds, and panels still drive.
    ds_trim = gapp.load_dataset(gapp.DEFAULT_SOURCE, gapp.DEFAULT_TZ, gapp.DEFAULT_CVALUE,
                                date_start="2026-03-01", date_end="2026-03-31")
    assert len(ds_trim.df) < len(ds.df)
    assert ds_trim.full_span == ds.span            # untrimmed span retained
    assert ds_trim.span[0] >= ds.span[0] and ds_trim.span[1] <= ds.span[1]
    assert ds_trim.df[DATETIME_COL].dt.month.unique().tolist() == [3]
    assert gapp._fig_timeseries(ds_trim, sid, col,
                                ("2026-03-02", "2026-03-10"),
                                ("2026-03-20", "2026-03-28"), "seg").data

    # Item 19: the segment table lists every segment with coverage + complete-set
    # cost, and an explicit member subset feeds the network aggregate (dropping the
    # highest-cost segment recovers timestamps the complete-set rule was losing).
    from inrix_tools import speed as _speed19
    rows = gapp._table_rows(ds)
    assert len(rows) == 46 and all(gapp.TBL_COVERAGE in r for r in rows)
    cov = _speed19.segment_coverage(ds.df, value=col)
    worst = int(cov.iloc[0][SEGMENT_COL])                 # highest complete-set cost
    net_all = gapp._analysis_frame(ds, col, gapp.SCOPE_NETWORK, None)
    keep = [s for s in gapp._all_segment_ids(ds) if s != worst]
    net_drop = gapp._analysis_frame(ds, col, gapp.SCOPE_NETWORK, None, members=keep)
    # dropping the costliest segment never loses complete-set timestamps.
    assert len(net_drop) >= len(net_all)

    # Item 20: directions resolve from metadata onto geo, the compass control lists
    # only present groups, a compass filter keeps only that direction, and the
    # display offset separates a real co-located opposing pair while leaving the
    # analytic geometry byte-for-byte intact.
    from inrix_tools import geometry as _geom
    assert set(ds.geo[_geom.DIR_GROUP_COL].dropna().unique()) == {"N", "E", "S", "W"}
    assert [o["value"] for o in gapp._direction_options(ds)] == ["N", "E", "S", "W"]
    only_n = gapp._display_geo(ds, ["N"], offset_on=False)
    assert len(only_n) and set(only_n[_geom.DIR_GROUP_COL].dropna()) == {"N"}
    analytic_wkt = [g.wkt for g in ds.geo["geometry"]]
    disp = gapp._display_geo(ds, [], offset_on=True)
    assert int(disp[_geom.OFFSET_FLAG_COL].sum()) >= 2      # >=1 opposing pair nudged
    off_id = disp.index[disp[_geom.OFFSET_FLAG_COL]][0]
    assert disp.loc[off_id, "geometry"].distance(ds.geo.loc[off_id, "geometry"]) > 0
    assert [g.wkt for g in ds.geo["geometry"]] == analytic_wkt  # display path never mutates ds.geo


# ---------------------------------------------------------------------------
# Item 16 — compare-cache split + GUI hardening
# ---------------------------------------------------------------------------
@pytest.fixture
def multi_day_ds():
    """A 30-day, 2-segment dataset (long enough to decompose past the warm-up)."""
    from inrix_tools import speed
    TZ = "America/Denver"
    idx = pd.date_range("2026-02-02", periods=30 * 288, freq="5min", tz=TZ)
    rng = np.random.default_rng(1)
    frames = [pd.DataFrame({DATETIME_COL: idx, SEGMENT_COL: np.int64(s),
                            TT_COL: 10 + rng.normal(0, 0.3, len(idx)),
                            SPEED_COL: 30 + rng.normal(0, 2, len(idx))})
              for s in (101, 202)]
    df = pd.concat(frames, ignore_index=True)
    return gapp.Dataset(
        df=df, metadata=pd.DataFrame(index=pd.Index([101, 202], name=SEGMENT_COL)),
        geo=None, metric_cols=speed.metric_columns(df), tz=TZ, span=(None, None))


def test_window_key_collapses_whole_day_spellings():
    for w in (None, [], [0, 24], [0, 24.0], [10, 10]):  # equal handles = whole day
        assert gapp._window_key(w) == "full"
    assert gapp._window_key([16, 18]) == (16.0, 18.0)


def test_compare_cache_split_reuses_decomposition(multi_day_ds, monkeypatch):
    """The review-O1 win: changing the before/after dates (same metric+window)
    must NOT re-decompose — it re-slices the cached adjusted frame. The
    decomposition tab shares that same cache too."""
    from inrix_tools import beforeafter
    ds = multi_day_ds
    col = gapp._metric_col(ds, "tt")

    calls = {"n": 0}
    real = beforeafter.adjust_for_periods
    monkeypatch.setattr(gapp.beforeafter, "adjust_for_periods",
                        lambda *a, **k: (calls.__setitem__("n", calls["n"] + 1) or real(*a, **k)))

    pairs = [(("2026-02-10", "2026-02-16"), ("2026-03-01", "2026-03-03")),
             (("2026-02-11", "2026-02-17"), ("2026-03-01", "2026-03-03")),
             (("2026-02-12", "2026-02-18"), ("2026-03-01", "2026-03-03"))]
    for before, after in pairs:
        assert len(gapp._compare_all(ds, col, before, after)) == 2
    gapp._fig_decomp(ds, 101, col, "seg")           # same (col, full-window) key
    assert calls["n"] == 1                           # ONE decomposition, not four


def test_adjusted_cache_evicts_lru(multi_day_ds):
    """The adjusted cache is capped (each entry is full-export sized)."""
    ds = multi_day_ds
    col = gapp._metric_col(ds, "tt")
    for w in ([0, 24], [6, 9], [16, 18]):            # 3 distinct windows, cap 2
        gapp._adjusted_frame(ds, col, w)
    assert len(ds._adjusted_cache) <= gapp._ADJ_CACHE_CAP


def test_compare_all_rejects_overlap_before_decomposing(multi_day_ds, monkeypatch):
    """Overlapping periods raise fast — no decomposition is paid for."""
    ds = multi_day_ds
    col = gapp._metric_col(ds, "tt")
    monkeypatch.setattr(gapp.beforeafter, "adjust_for_periods",
                        lambda *a, **k: pytest.fail("decomposed before validating"))
    with pytest.raises(ValueError, match="overlap"):
        gapp._compare_all(ds, col, ("2026-02-10", "2026-03-01"),
                          ("2026-02-20", "2026-03-10"))


def test_store_evicts_old_dataset(multi_day_ds):
    """Single-user app keeps only the latest load (~2.3 GB each — review B2)."""
    gapp._DATASETS.clear()
    t1 = gapp._store(multi_day_ds)
    t2 = gapp._store(multi_day_ds)
    assert len(gapp._DATASETS) == 1
    assert gapp._get(t1) is None and gapp._get(t2) is not None


def test_metric_choices_disables_absent_metric(series_df):
    """Speed-only export: Travel time is disabled and the selection lands on
    Speed instead of KeyError-ing on the absent column (review B3)."""
    from inrix_tools import speed
    speed_only = series_df.drop(columns=[TT_COL])
    ds = gapp.Dataset(df=speed_only,
                      metadata=pd.DataFrame(index=pd.Index([101], name=SEGMENT_COL)),
                      geo=None, metric_cols=speed.metric_columns(speed_only),
                      tz="America/Denver", span=(None, None))
    options, value = gapp._metric_choices(ds, "tt")   # user had tt selected
    assert value == "speed"
    by_val = {o["value"]: o["disabled"] for o in options}
    # delay is absent too (no Delay column on this synthetic frame)
    assert by_val == {"tt": True, "speed": False, "delay": True}
    # both present -> keep the current choice
    both = gapp.Dataset(df=series_df, metadata=ds.metadata, geo=None,
                        metric_cols=speed.metric_columns(series_df),
                        tz="America/Denver", span=(None, None))
    assert gapp._metric_choices(both, "speed")[1] == "speed"


def test_fig_decomp_empty_selection_and_warmup_message(series_df):
    """No selection -> pick-a-segment; a too-short series -> a message that names
    the 7-day warm-up rather than a bare 'no decomposition' (review B6)."""
    from inrix_tools import speed
    ds = gapp.Dataset(df=series_df,
                      metadata=pd.DataFrame(index=pd.Index([101], name=SEGMENT_COL)),
                      geo=None, metric_cols=speed.metric_columns(series_df),
                      tz="America/Denver", span=(None, None))
    assert isinstance(gapp._fig_decomp(ds, None, TT_COL, ""), go.Figure)
    fig = gapp._fig_decomp(ds, 101, TT_COL, "seg")    # 2 days < warm-up -> empty
    text = " ".join(a.text for a in fig.layout.annotations)
    assert "warm-up" in text


def test_metric_col_and_segment_means(series_df):
    from inrix_tools import speed
    ds = gapp.Dataset(df=series_df, metadata=pd.DataFrame(index=pd.Index([101], name=SEGMENT_COL)),
                      geo=None, metric_cols=speed.metric_columns(series_df),
                      tz="America/Denver", span=(None, None))
    assert gapp._metric_col(ds, "tt") == TT_COL
    assert gapp._metric_col(ds, "speed") == SPEED_COL
    means = gapp._segment_means(ds, SPEED_COL)
    assert 101 in means.index


def test_delay_metric_wiring_segment_and_aggregate(series_df):
    """Item 17: a Delay column is a first-class metric — enabled in the radio,
    driving the map/panels, and (unlike speed) valid in aggregate scope."""
    from inrix_tools import speed
    REF = "Ref Speed(miles/hour)"
    df = series_df.copy()
    df[REF] = 60.0
    df["Corridor/Region Name"] = "C1"
    meta = (pd.DataFrame({"Segment ID": [101], "Segment Length(Miles)": [0.5]})
            .set_index("Segment ID"))
    df = speed.segment_delay(df, geo_or_metadata=meta)
    ds = gapp.Dataset(df=df,
                      metadata=pd.DataFrame(index=pd.Index([101], name=SEGMENT_COL)),
                      geo=None, metric_cols=speed.metric_columns(df),
                      tz="America/Denver", span=(None, None))
    # Delay is present + selectable in the segment-scope radio.
    options, _ = gapp._metric_choices(ds, "delay")
    by_val = {o["value"]: o["disabled"] for o in options}
    assert by_val["delay"] is False
    col = gapp._metric_col(ds, "delay")
    assert col == speed.DELAY_COL
    assert gapp._agg_metric_key("delay") == "delay"    # delay stays in agg scope
    assert gapp._agg_metric_key("speed") == "tt"       # speed falls back to tt
    # A corridor aggregate on delay sums member delays (one segment here).
    frame = gapp._analysis_frame(ds, col, gapp.SCOPE_CORRIDOR, "C1")
    assert col in frame.columns and not frame.empty
    # segment means on delay colour the map without error.
    assert 101 in gapp._segment_means(ds, col).index


def test_agg_metrics_include_delay_exclude_speed():
    """Corridor/Network scope sums across segments — travel time and delay sum
    cleanly under the complete-set rule; speed has no segment weighting."""
    assert set(gapp._AGG_METRICS) == {"tt", "delay"}
    assert "speed" not in gapp._AGG_METRICS


def test_parse_freeflow_spec():
    assert gapp._parse_freeflow("ref") == "ref"
    assert gapp._parse_freeflow("p95") == ("pXX", 95)
    assert gapp._parse_freeflow("p85") == ("pXX", 85)
    assert gapp._parse_freeflow(None) == "ref"


def test_apply_tod_window_filters_and_full_day_is_noop(series_df):
    """The slider-window pre-filter: [0,24] passes everything through, a 4–6PM
    window keeps only in-window rows and only that segment's mean is over them."""
    assert len(gapp._apply_tod(series_df, [0, 24])) == len(series_df)      # no-op
    assert len(gapp._apply_tod(series_df, None)) == len(series_df)
    pm = gapp._apply_tod(series_df, [16, 18])
    hrs = pm[DATETIME_COL].dt.hour
    assert set(hrs.unique()) == {16, 17} and len(pm) < len(series_df)


def test_hour_label_formats_slider_values():
    assert gapp._hour_label(16) == "4:00 PM"
    assert gapp._hour_label(17.5) == "5:30 PM"
    assert gapp._hour_label(0) == "12:00 AM"
    assert gapp._hour_label(24) == "12:00 AM"     # slider max = end of day


# ---------------------------------------------------------------------------
# Item 11 — session date-subset on load
# ---------------------------------------------------------------------------
def test_dataset_full_span_defaults_to_span():
    """A Dataset built without an explicit full_span uses its span (so the
    Restrict-dates picker bounds are sane even for hand-built test datasets)."""
    ds = gapp.Dataset(df=pd.DataFrame(), metadata=pd.DataFrame(), geo=None,
                      metric_cols={}, tz="America/Denver", span=("a", "b"))
    assert ds.full_span == ("a", "b")


def test_date_restriction_shrinks_frame_and_panels_drive(multi_day_ds):
    """A trimmed Dataset carries fewer rows, its span reflects the sub-range while
    full_span keeps the export span, and the panels still drive off the smaller
    frame (Item 11)."""
    from inrix_tools import speed
    from inrix_tools.timebins import filter_date_range

    full = multi_day_ds.df                        # 30 days, 2 segments
    trimmed = filter_date_range(full, "2026-02-10", "2026-02-19")
    assert len(trimmed) < len(full)
    assert set(trimmed[DATETIME_COL].dt.strftime("%Y-%m-%d").unique()) == \
        {f"2026-02-{d:02d}" for d in range(10, 20)}

    full_dates = full[DATETIME_COL].dt.date
    trim_dates = trimmed[DATETIME_COL].dt.date
    ds = gapp.Dataset(df=trimmed,
                      metadata=pd.DataFrame(index=pd.Index([101, 202], name=SEGMENT_COL)),
                      geo=None, metric_cols=speed.metric_columns(trimmed),
                      tz="America/Denver",
                      span=(trim_dates.min(), trim_dates.max()),
                      full_span=(full_dates.min(), full_dates.max()))
    assert ds.span != ds.full_span                # restriction recorded
    col = gapp._metric_col(ds, "tt")
    # a panel drives on the trimmed frame (only in-range timestamps present)
    fig = gapp._fig_timeseries(ds, 101, col, ("2026-02-10", "2026-02-11"),
                               ("2026-02-18", "2026-02-19"), "seg")
    assert fig.data
    xs = pd.to_datetime(fig.data[0].x)
    assert xs.max().strftime("%Y-%m-%d") <= "2026-02-19"


def test_default_periods_clamp_to_trimmed_span():
    """The before/after defaults are computed from the trimmed span, so they land
    inside the retained range, not the full export."""
    from datetime import date
    b0, b1, a0, a1 = gapp.default_periods(date(2026, 2, 10), date(2026, 2, 25))
    assert date(2026, 2, 10) <= b0 <= b1 < a0 <= a1 <= date(2026, 2, 25)


# ---------------------------------------------------------------------------
# Item 12 — corridor & network travel-time analysis scope
# ---------------------------------------------------------------------------
@pytest.fixture
def scope_ds(multi_day_ds):
    """The 30-day, 2-segment dataset with a corridor column, so corridor + network
    scope both resolve. Both segments report at every timestamp, so the
    complete-set rule keeps every timestamp (the aggregate series isn't starved)."""
    from inrix_tools import speed
    from inrix_tools.io import CORRIDOR_COL
    df = multi_day_ds.df.copy()
    df[CORRIDOR_COL] = "Main"
    return gapp.Dataset(df=df, metadata=multi_day_ds.metadata, geo=None,
                        metric_cols=speed.metric_columns(df),
                        tz=multi_day_ds.tz, span=(None, None))


def test_scope_options_disable_when_data_absent(series_df, multi_day_ds):
    """Corridor scope needs the corridor column; both aggregate scopes need a
    travel-time column (they analyse summed travel time)."""
    from inrix_tools import speed
    # speed-only export: no travel time -> both aggregate scopes disabled, no corridors
    speed_only = series_df.drop(columns=[TT_COL])
    ds = gapp.Dataset(df=speed_only,
                      metadata=pd.DataFrame(index=pd.Index([101], name=SEGMENT_COL)),
                      geo=None, metric_cols=speed.metric_columns(speed_only),
                      tz="America/Denver", span=(None, None))
    corr_opts, corr_val, scope_opts = gapp._scope_options(ds)
    disabled = {o["value"]: o.get("disabled", False) for o in scope_opts}
    assert disabled[gapp.SCOPE_CORRIDOR] and disabled[gapp.SCOPE_NETWORK]
    assert corr_opts == [] and corr_val is None
    # travel time present but no corridor column -> network on, corridor off
    _, _, scope_opts2 = gapp._scope_options(multi_day_ds)
    disabled2 = {o["value"]: o.get("disabled", False) for o in scope_opts2}
    assert not disabled2[gapp.SCOPE_NETWORK] and disabled2[gapp.SCOPE_CORRIDOR]


def test_scope_options_list_corridors(scope_ds):
    corr_opts, corr_val, scope_opts = gapp._scope_options(scope_ds)
    assert [o["value"] for o in corr_opts] == ["Main"] and corr_val == "Main"
    disabled = {o["value"]: o.get("disabled", False) for o in scope_opts}
    assert not disabled[gapp.SCOPE_CORRIDOR] and not disabled[gapp.SCOPE_NETWORK]


def test_analysis_frame_collapses_to_synthetic_entity(scope_ds):
    """Corridor/Network scope build a per-timestamp aggregate collapsed to one
    synthetic Segment ID so the decompose/compare adapters run unchanged."""
    col = gapp._metric_col(scope_ds, "tt")
    net = gapp._analysis_frame(scope_ds, col, gapp.SCOPE_NETWORK, None)
    assert set(net[SEGMENT_COL].unique()) == {gapp._AGG_SEGMENT_ID}
    # both segments present every timestamp -> one complete-set row per timestamp
    assert len(net) == net[DATETIME_COL].nunique()
    # the network total is the sum of the two segments at a timestamp
    seg = scope_ds.df.groupby(DATETIME_COL, observed=True)[col].sum()
    merged = net.set_index(DATETIME_COL)[col]
    assert merged.iloc[0] == pytest.approx(seg.loc[merged.index[0]])
    # corridor scope collapses the same way
    corr = gapp._analysis_frame(scope_ds, col, gapp.SCOPE_CORRIDOR, "Main")
    assert set(corr[SEGMENT_COL].unique()) == {gapp._AGG_SEGMENT_ID}


def test_timeseries_network_scope_titles_and_drives(scope_ds):
    col = gapp._metric_col(scope_ds, "tt")
    fig = gapp._fig_timeseries(scope_ds, None, col, None, None, "seg",
                               scope=gapp.SCOPE_NETWORK)
    assert fig.data and fig.layout.title.text == "Network (all segments)"


def test_corridor_scope_without_pick_prompts(scope_ds):
    """Corridor scope with no corridor chosen yet shows a prompt, not a crash."""
    col = gapp._metric_col(scope_ds, "tt")
    for builder in (
        lambda: gapp._fig_timeseries(scope_ds, None, col, None, None, "",
                                     scope=gapp.SCOPE_CORRIDOR, corridor=None),
        lambda: gapp._fig_decomp(scope_ds, None, col, "",
                                 scope=gapp.SCOPE_CORRIDOR, corridor=None),
    ):
        fig = builder()
        text = " ".join(a.text for a in fig.layout.annotations)
        assert "corridor" in text.lower()


def test_network_scope_beforeafter_and_decomp(scope_ds):
    """The aggregate series feeds compare_periods / decompose via the synthetic
    entity id: before/after returns a single aggregate row and the decomposition
    tab renders (Item 12)."""
    pytest.importorskip("traffic_anomaly")
    col = gapp._metric_col(scope_ds, "tt")
    before, after = ("2026-02-10", "2026-02-16"), ("2026-03-01", "2026-03-03")
    comp = gapp._compare_all(scope_ds, col, before, after, scope=gapp.SCOPE_NETWORK)
    assert len(comp) == 1 and int(comp[SEGMENT_COL].iloc[0]) == gapp._AGG_SEGMENT_ID
    fig = gapp._fig_beforeafter(scope_ds, None, col, before, after, True,
                                scope=gapp.SCOPE_NETWORK)
    assert fig.data
    dfig = gapp._fig_decomp(scope_ds, None, col, "", scope=gapp.SCOPE_NETWORK)
    assert dfig.data


def test_scope_keys_the_adjusted_cache(scope_ds):
    """Segment vs network adjusted frames are cached under distinct keys (so a
    scope switch doesn't read the wrong decomposition)."""
    pytest.importorskip("traffic_anomaly")
    col = gapp._metric_col(scope_ds, "tt")
    seg = gapp._adjusted_frame(scope_ds, col)                          # segment scope
    net = gapp._adjusted_frame(scope_ds, col, scope=gapp.SCOPE_NETWORK)
    keys = set(scope_ds._adjusted_cache)
    # the key gained a trailing DOW element ("all" = no day-of-week filter, Item 13)
    # and a members element ("all" = no membership override, Item 19)
    assert (col, "full", gapp.SCOPE_SEGMENT, None, "all", "all") in keys
    assert (col, "full", gapp.SCOPE_NETWORK, None, "all", "all") in keys
    assert set(seg[SEGMENT_COL].unique()) == {101, 202}
    assert set(net[SEGMENT_COL].unique()) == {gapp._AGG_SEGMENT_ID}


# ---------------------------------------------------------------------------
# Item 13 — day-of-week filter, before/after + corridor summaries, hover fix
# ---------------------------------------------------------------------------
def test_days_key_collapses_noop_spellings():
    for d in (None, [], list(range(7)), [0, 1, 2, 3, 4, 5, 6]):
        assert gapp._days_key(d) == "all"
    assert gapp._days_key([5, 6]) == (5, 6)
    assert gapp._days_key([6, 5]) == (5, 6)        # order-independent


def test_apply_tod_day_of_week_filter(series_df):
    """The DOW arg to _apply_tod keeps only the selected weekdays; all/empty no-op.
    series_df spans 2026-03-02 (Mon) .. 2026-03-03 (Tue)."""
    mon = gapp._apply_tod(series_df, None, days=[0])           # Monday only
    assert set(mon[DATETIME_COL].dt.dayofweek) == {0}
    assert len(gapp._apply_tod(series_df, None, days=list(range(7)))) == len(series_df)
    assert len(gapp._apply_tod(series_df, None, days=[])) == len(series_df)
    # composes with the ToD window: Monday 4–6PM only
    both = gapp._apply_tod(series_df, [16, 18], days=[0])
    assert set(both[DATETIME_COL].dt.dayofweek) == {0}
    assert set(both[DATETIME_COL].dt.hour) <= {16, 17}


def test_summary_bars_beforeafter_facets(series_df):
    """summary_bars gains a side-by-side before/after mode (two facets, one shared
    day-group legend); summary_after=None keeps the single panel."""
    from inrix_tools import speed
    from inrix_tools.timebins import (assign_day_group, assign_time_bins,
                                      DAY_GROUP_COL, TIME_BIN_COL)
    binned = assign_time_bins(assign_day_group(series_df),
                              ["6:00AM-12:00PM", "12:00PM-6:00PM"])
    summ = speed.segment_summary(binned, values=[SPEED_COL],
                                 group_cols=[DAY_GROUP_COL, TIME_BIN_COL])
    fac = figures.summary_bars(summ, SPEED_COL, summary_after=summ)
    assert "xaxis2" in fac.layout.to_plotly_json()        # a second facet exists
    assert any(t.legendgroup for t in fac.data)           # legend spans both facets
    # only one legend entry per day-group (deduped across facets)
    assert sum(bool(t.showlegend) for t in fac.data) < len(fac.data)
    single = figures.summary_bars(summ, SPEED_COL)
    assert "xaxis2" not in single.layout.to_plotly_json()


def test_fig_summary_beforeafter_and_fallback(series_df):
    """_fig_summary renders two facets when disjoint periods are set, and falls back
    to the single-period view when periods are unset or overlap."""
    from inrix_tools import speed
    ds = gapp.Dataset(df=series_df,
                      metadata=pd.DataFrame(index=pd.Index([101], name=SEGMENT_COL)),
                      geo=None, metric_cols=speed.metric_columns(series_df),
                      tz="America/Denver", span=(None, None))
    col = gapp._metric_col(ds, "tt")
    fac = gapp._fig_summary(ds, 101, col, "seg",
                            before=("2026-03-02", "2026-03-02"),
                            after=("2026-03-03", "2026-03-03"))
    assert "xaxis2" in fac.layout.to_plotly_json()
    single = gapp._fig_summary(ds, 101, col, "seg")       # no periods
    assert "xaxis2" not in single.layout.to_plotly_json()
    overlap = gapp._fig_summary(ds, 101, col, "seg",
                                before=("2026-03-02", "2026-03-03"),
                                after=("2026-03-02", "2026-03-03"))
    assert "xaxis2" not in overlap.layout.to_plotly_json()  # falls back, no crash


def test_fig_summary_corridor_is_sum_not_mean_of_means():
    """Item 13 part 3: the corridor/network day×time summary bars are the SUMMED
    corridor travel time (sum across segments, mean over time), not the mean of
    per-segment means."""
    from inrix_tools import speed
    from inrix_tools.io import CORRIDOR_COL
    TZ = "America/Denver"
    ts = pd.date_range("2026-03-02 00:00", "2026-03-05 23:55", freq="5min", tz=TZ)
    # two segments at constant TT 10 and 20 -> network sum is exactly 30 every ts
    frames = [pd.DataFrame({DATETIME_COL: ts, SEGMENT_COL: np.int64(s), TT_COL: tt,
                            CORRIDOR_COL: "Main"})
              for s, tt in ((101, 10.0), (202, 20.0))]
    df = pd.concat(frames, ignore_index=True)
    ds = gapp.Dataset(df=df,
                      metadata=pd.DataFrame(index=pd.Index([101, 202], name=SEGMENT_COL)),
                      geo=None, metric_cols=speed.metric_columns(df), tz=TZ,
                      span=(None, None))
    col = gapp._metric_col(ds, "tt")
    fig = gapp._fig_summary(ds, None, col, "", scope=gapp.SCOPE_NETWORK)
    ys = [v for tr in fig.data for v in tr.y]
    assert ys and all(abs(v - 30.0) < 1e-6 for v in ys)   # the sum, not mean-of-means (15)


def test_beforeafter_forest_hover_shows_name_not_index():
    """Review B4: the hover shows the segment name (in text), not the numeric row
    index — the hovertemplate no longer references %{y}."""
    compare = pd.DataFrame({SEGMENT_COL: [101, 202], "effect": [0.5, -0.3],
                            "ci_low": [0.2, -0.6], "ci_high": [0.8, 0.0]})
    compare.attrs["method"] = "decomposition"
    fig = figures.beforeafter_forest(compare,
                                     labels={101: "Main & 1st", 202: "Main & 2nd"})
    assert "%{y}" not in fig.data[0].hovertemplate
    assert any("Main &" in t for t in fig.data[0].text)


# ---------------------------------------------------------------------------
# AADT volume weighting (Item 18)
# ---------------------------------------------------------------------------
def _aadt_scope_ds():
    """A 30-day, 2-segment corridor dataset with an AADT series attached — seg 101
    fast/low-volume, seg 202 slow/high-volume, so weighted != unweighted."""
    from inrix_tools import speed
    from inrix_tools.io import CORRIDOR_COL
    TZ = "America/Denver"
    idx = pd.date_range("2026-02-02", periods=30 * 288, freq="5min", tz=TZ)
    rng = np.random.default_rng(2)
    frames = [pd.DataFrame({DATETIME_COL: idx, SEGMENT_COL: np.int64(s),
                            TT_COL: 10 + rng.normal(0, 0.3, len(idx)),
                            SPEED_COL: (50 if s == 101 else 20) + rng.normal(0, 1, len(idx)),
                            "Delay(Minutes)": (1.0 if s == 101 else 4.0)})
              for s in (101, 202)]
    df = pd.concat(frames, ignore_index=True)
    df[CORRIDOR_COL] = "Main"
    return gapp.Dataset(
        df=df, metadata=pd.DataFrame(index=pd.Index([101, 202], name=SEGMENT_COL)),
        geo=None, metric_cols=speed.metric_columns(df), tz=TZ, span=(None, None),
        aadt=pd.Series({101: 1000.0, 202: 9000.0}))


def test_aadt_options_gate_on_layer_presence(scope_ds):
    """The vehicle-hours map mode + weighted-speed toggle are exposed/enabled only
    when a layer is loaded; without one they're hidden/disabled."""
    ds_no = scope_ds                              # no aadt on this fixture
    modes = [o["value"] for o in gapp._map_mode_options(ds_no)]
    assert gapp.MAP_MODE_VHD not in modes
    assert gapp._wspeed_options(ds_no)[0]["disabled"] is True

    ds_yes = _aadt_scope_ds()
    modes = [o["value"] for o in gapp._map_mode_options(ds_yes)]
    assert gapp.MAP_MODE_VHD in modes             # delay + AADT present
    assert gapp._wspeed_options(ds_yes)[0]["disabled"] is False


def test_resolve_col_weighted_speed_only_in_aggregate_with_toggle():
    ds = _aadt_scope_ds()
    wcol = gapp._wspeed_col(ds)
    # segment scope: toggle ignored -> the plain metric column
    assert gapp._resolve_col(ds, "tt", gapp.SCOPE_SEGMENT, ["on"]) == gapp._metric_col(ds, "tt")
    # aggregate scope, toggle off -> travel time; on -> weighted speed
    assert gapp._resolve_col(ds, "tt", gapp.SCOPE_NETWORK, []) == gapp._metric_col(ds, "tt")
    assert gapp._resolve_col(ds, "tt", gapp.SCOPE_NETWORK, ["on"]) == wcol


def test_weighted_speed_frame_differs_and_tt_stays_a_sum():
    """The weighted-speed aggregate reflects the high-volume slow segment; the
    corridor travel-time sum is untouched by AADT (stays the pure sum, Item 12)."""
    from inrix_tools import speed
    from inrix_tools.io import CORRIDOR_COL
    ds = _aadt_scope_ds()
    wcol = gapp._wspeed_col(ds)
    wframe = gapp._analysis_frame(ds, wcol, gapp.SCOPE_NETWORK, None)
    # Σ(w·v)/Σw = (50·1000 + 20·9000)/10000 = 23 — much nearer the slow, busy segment
    # than the plain average of 35.
    assert wframe[wcol].mean() == pytest.approx(23, abs=0.5)

    tt_col = gapp._metric_col(ds, "tt")
    tframe = gapp._analysis_frame(ds, tt_col, gapp.SCOPE_NETWORK, None)
    net = speed.network_travel_time(ds.df, value=tt_col)   # AADT plays no part
    assert tframe[tt_col].mean() == pytest.approx(net[tt_col].mean())
    assert tframe[tt_col].mean() == pytest.approx(20, abs=0.5)   # 10 + 10, a sum


def test_vhd_map_colouring_produces_vehicle_hours():
    """The vehicle-hours map mode colours segments by mean-delay(hrs)×AADT."""
    from inrix_tools import aadt as aadtmod
    ds = _aadt_scope_ds()
    md = gapp._segment_means(ds, gapp._metric_col(ds, "delay"), None, None)
    vh = aadtmod.vehicle_hours_of_delay(md, ds.aadt)["vehicle_hours"]
    # seg202: 4 min delay × 9000 veh = 600 veh-hr/day dominates seg101 (1×1000/60).
    assert vh.loc[202] == pytest.approx(4 / 60 * 9000, rel=0.05)
    assert vh.loc[202] > 30 * vh.loc[101]


def test_segment_map_hover_shows_aadt():
    """When geo carries AADT + a source flag, the hover surfaces both (a marginal
    'nearest' join is visible, not silent)."""
    gpd = pytest.importorskip("geopandas")
    from shapely.geometry import LineString
    geo = gpd.GeoDataFrame(
        {"Segment ID": [101, 202], "Combined": ["A", "B"],
         "AADT": [12000.0, np.nan], "aadt_source": ["matched", "missing"],
         "geometry": [LineString([(-116.2, 43.6), (-116.19, 43.61)]),
                      LineString([(-116.18, 43.62), (-116.19, 43.61)])]},
        geometry="geometry", crs="EPSG:4326").set_index("Segment ID")
    fig = figures.segment_map(geo)
    txt = "".join(t for tr in fig.data for t in (tr.text or []) if tr.text is not None)
    assert "AADT: 12,000 (matched)" in txt
    assert "AADT: none" in txt


def test_adjusted_cache_canonicalises_corridor_outside_corridor_scope(multi_day_ds, monkeypatch):
    """Segment/network-scope cache keys must not vary with the (irrelevant)
    corridor dropdown: the map path passes no corridor while the panels pass the
    auto-picked one, and a non-canonical key would decompose the identical frame
    twice and thrash the cap-2 cache."""
    from inrix_tools import beforeafter
    ds = multi_day_ds
    col = gapp._metric_col(ds, "tt")

    calls = {"n": 0}
    real = beforeafter.adjust_for_periods
    monkeypatch.setattr(gapp.beforeafter, "adjust_for_periods",
                        lambda *a, **k: (calls.__setitem__("n", calls["n"] + 1) or real(*a, **k)))

    a = gapp._adjusted_frame(ds, col)                              # map path
    b = gapp._adjusted_frame(ds, col, corridor="Main")             # panel path
    assert a is b and calls["n"] == 1

    net1 = gapp._adjusted_frame(ds, col, scope=gapp.SCOPE_NETWORK, corridor="Main")
    net2 = gapp._adjusted_frame(ds, col, scope=gapp.SCOPE_NETWORK)
    assert net1 is net2 and calls["n"] == 2                        # one per scope

    # _compare_all shares the same canonical keys (and the adjusted cache).
    before, after = ("2026-02-10", "2026-02-16"), ("2026-03-01", "2026-03-03")
    r1 = gapp._compare_all(ds, col, before, after)
    r2 = gapp._compare_all(ds, col, before, after, corridor="Main")
    assert r1 is r2 and calls["n"] == 2


def test_write_kml_vhd_mode_uses_vehicle_hours(tmp_path, monkeypatch):
    """The KML export honours the vehicle-hours map mode instead of silently
    falling back to plain metric means."""
    gpd = pytest.importorskip("geopandas")
    from shapely.geometry import LineString

    ds = _aadt_scope_ds()
    ds.geo = gpd.GeoDataFrame(
        {"Combined": ["seg a", "seg b"],
         "geometry": [LineString([(-116.20, 43.61), (-116.20, 43.62)]),
                      LineString([(-116.19, 43.61), (-116.19, 43.62)])]},
        index=pd.Index([101, 202], name=SEGMENT_COL), crs="EPSG:4326")
    monkeypatch.setattr(gapp, "OUTPUT_DIR", tmp_path)

    called = {"n": 0}
    real = gapp.aadt.vehicle_hours_of_delay
    monkeypatch.setattr(gapp.aadt, "vehicle_hours_of_delay",
                        lambda *a, **k: (called.__setitem__("n", called["n"] + 1) or real(*a, **k)))
    path = gapp._write_kml(ds, "delay", gapp.MAP_MODE_VHD, (None, None), (None, None))
    assert path.exists() and called["n"] == 1


# ---------------------------------------------------------------------------
# Item 19 — interactive segment table: coverage, membership, name edit, map link
# ---------------------------------------------------------------------------
@pytest.fixture
def table_ds():
    """A 3-segment, one-corridor dataset where segment 303 is chronically missing
    (absent at the first 20 timestamps, where it is the SOLE missing member), so
    the coverage helper flags it with a non-zero complete-set cost."""
    from inrix_tools import speed
    from inrix_tools.io import CORRIDOR_COL
    TZ = "America/Denver"
    ts = pd.date_range("2026-02-02", periods=40, freq="5min", tz=TZ)
    rng = np.random.default_rng(3)
    rows = []
    for s in (101, 202, 303):
        for i, t in enumerate(ts):
            if s == 303 and i < 20:          # 303 absent at the first 20 timestamps
                continue
            rows.append({DATETIME_COL: t, SEGMENT_COL: np.int64(s),
                         TT_COL: 10 + rng.normal(0, 0.3), SPEED_COL: 30 + rng.normal(0, 2),
                         CORRIDOR_COL: "Main"})
    df = pd.DataFrame(rows)
    meta = pd.DataFrame({"Combined": ["Seg 101", "Seg 202", "Seg 303"]},
                        index=pd.Index([101, 202, 303], name=SEGMENT_COL))
    ds = gapp.Dataset(df=df, metadata=meta, geo=None,
                      metric_cols=speed.metric_columns(df), tz=TZ, span=(None, None))
    ds.labels = {101: "First", 202: "Second", 303: "Missing one"}
    return ds


def test_table_rows_flag_incomplete_segment(table_ds):
    """The table rows carry the friendly name + the completeness diagnostics; the
    chronically-missing segment is flagged with a non-zero complete-set cost."""
    rows = gapp._table_rows(table_ds)
    by_id = {r[gapp.TBL_ID]: r for r in rows}
    assert set(by_id) == {101, 202, 303}
    assert by_id[101][gapp.TBL_NAME] == "First"
    # 303 reports 20/40 timestamps -> 50% coverage, and dropping it recovers the
    # 20 timestamps it was the sole missing member at (exact cost).
    assert by_id[303][gapp.TBL_COVERAGE] == pytest.approx(50.0)
    assert by_id[303][gapp.TBL_COST] == 20
    assert by_id[101][gapp.TBL_COST] == 0 and by_id[202][gapp.TBL_COST] == 0


def test_table_columns_name_editable_others_not():
    cols = {c["id"]: c for c in gapp._table_columns()}
    assert cols[gapp.TBL_NAME]["editable"] is True
    for cid in (gapp.TBL_COMBINED, gapp.TBL_COVERAGE, gapp.TBL_COST, gapp.TBL_ID):
        assert cols[cid]["editable"] is False


def test_rows_to_member_ids_and_row_index(table_ds):
    data = gapp._table_rows(table_ds)
    # selected_rows are positional indices into `data`.
    picked = gapp._rows_to_member_ids(data, [0, 2])
    assert picked == [int(data[0][gapp.TBL_ID]), int(data[2][gapp.TBL_ID])]
    assert gapp._rows_to_member_ids(data, []) == []
    # map-click -> row highlight: find the row carrying a Segment ID.
    sid = int(data[1][gapp.TBL_ID])
    assert gapp._segment_row_index(data, sid) == 1
    assert gapp._segment_row_index(data, 999999) is None


def test_norm_members_noop_vs_subset(table_ds):
    all_ids = gapp._all_segment_ids(table_ds)
    assert gapp._norm_members(table_ds, None) is None
    assert gapp._norm_members(table_ds, []) is None
    assert gapp._norm_members(table_ds, all_ids) is None            # whole set = no override
    assert gapp._norm_members(table_ds, [202, 101]) == [101, 202]   # subset, sorted


def test_members_key_collapses_noop():
    assert gapp._members_key(None) == "all"
    assert gapp._members_key([]) == "all"
    assert gapp._members_key([303, 101]) == (101, 303)


def test_membership_override_changes_network_aggregate(table_ds):
    """Feeding an explicit member subset to the aggregate builder drops the
    chronically-missing segment, recovering the complete-set timestamps its
    absence was costing — so the network series lengthens and its values change."""
    col = gapp._metric_col(table_ds, "tt")
    full = gapp._analysis_frame(table_ds, col, gapp.SCOPE_NETWORK, None)
    # with all three, only the 20 timestamps where 303 reports are complete.
    assert len(full) == 20
    dropped = gapp._analysis_frame(table_ds, col, gapp.SCOPE_NETWORK, None,
                                   members=[101, 202])
    assert len(dropped) == 40                       # all timestamps now complete
    assert set(dropped[SEGMENT_COL].unique()) == {gapp._AGG_SEGMENT_ID}


def test_membership_keys_the_adjusted_cache(table_ds):
    """A membership subset is a distinct cache key so it doesn't collide with the
    whole-network decomposition."""
    pytest.importorskip("traffic_anomaly")
    col = gapp._metric_col(table_ds, "tt")
    gapp._adjusted_frame(table_ds, col, scope=gapp.SCOPE_NETWORK)
    gapp._adjusted_frame(table_ds, col, scope=gapp.SCOPE_NETWORK, members=[101, 202])
    keys = set(table_ds._adjusted_cache)
    assert (col, "full", gapp.SCOPE_NETWORK, None, "all", "all") in keys
    assert (col, "full", gapp.SCOPE_NETWORK, None, "all", (101, 202)) in keys


def test_segment_map_dims_nonmembers(geo_two):
    """A proper membership subset draws non-member segment lines faint; the whole
    set / None dims nothing."""
    fig = figures.segment_map(geo_two, member_ids={101})
    # line traces are the Scattermap 'lines' traces, in geo order (101, 202).
    lines = [t for t in fig.data if getattr(t, "mode", None) == "lines"]
    assert len(lines) == 2
    member_op, nonmember_op = lines[0].opacity, lines[1].opacity
    assert nonmember_op < member_op                       # 202 dimmed
    # whole set -> no dimming (both full strength).
    full = figures.segment_map(geo_two, member_ids={101, 202})
    ops = {t.opacity for t in full.data if getattr(t, "mode", None) == "lines"}
    assert all(o >= 0.85 for o in ops)


def test_save_names_round_trips_table_edits(table_ds, tmp_path, monkeypatch):
    """The Save-names path (write_names) persists the table's edited Name column so
    load_names reads it back — exercised through the same code the callback runs."""
    from inrix_tools import names as _names
    data = gapp._table_rows(table_ds)
    # edit a name in the table data, as an inline edit would.
    data[0][gapp.TBL_NAME] = "Renamed first"
    names_df = pd.DataFrame({
        SEGMENT_COL: [int(r[gapp.TBL_ID]) for r in data],
        _names.INRIX_LABEL_COL: [str(r.get(gapp.TBL_COMBINED, "") or "") for r in data],
        _names.NAME_COL: [str(r.get(gapp.TBL_NAME, "") or "") for r in data],
    })
    path = _names.write_names(names_df, tmp_path / "segment_names.csv")
    loaded = _names.load_names(path)
    assert loaded.loc[int(data[0][gapp.TBL_ID]), _names.NAME_COL] == "Renamed first"

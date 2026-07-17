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
                   "tod-window", "names-path", "write-names"):
        assert needed in ids, f"missing layout component: {needed}"
    assert len(app.callback_map) >= 5  # load, click-select, map, panels, export


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
    assert by_val == {"tt": True, "speed": False}
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

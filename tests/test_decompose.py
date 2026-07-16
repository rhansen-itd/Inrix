"""Tests for the decompose adapter (decompose.py) — ROADMAP Items 4 / 9.

Focus: the ``min_rolling_window_samples`` auto-scaling that lets a
time-of-day-filtered series decompose instead of coming back empty (Item 9),
plus the end-to-end "filter first, then decompose + changepoint" path that the
time-of-day window feature relies on. Full-day behaviour must stay byte-for-byte
what it was before the auto-scaling was added.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from inrix_tools import changepoint as cp
from inrix_tools import decompose as dc
from inrix_tools.timebins import filter_time_window

TZ = "America/Denver"
VALUE = "Travel Time(Minutes)"
START = "2026-02-02"


def _make_series(step_size=0.0, step_hour=None, step_day=30, n_days=60,
                 noise=0.3, seed=0, seg=111):
    """5-min travel-time for one segment. If ``step_hour`` is given the level step
    is applied **only** to samples at that local hour (a PM-peak-only shift);
    otherwise the step (if any) applies all day."""
    rng = np.random.RandomState(seed)
    idx = pd.date_range(START, periods=n_days * 288, freq="5min", tz=TZ)
    day_index = (idx - idx[0]).days
    stepped = day_index >= step_day
    if step_hour is not None:
        stepped = stepped & (idx.hour == step_hour)
    val = 10.0 + np.where(stepped, step_size, 0.0) + rng.normal(0, noise, len(idx))
    df = pd.DataFrame({"Segment ID": np.int64(seg), "Date Time": idx, VALUE: val})
    df.attrs["units"] = {"speed": "miles/hour", "travel_time": "Minutes"}
    df.attrs["tz"] = TZ
    return df


# ---------------------------------------------------------------------------
# _auto_min_rolling_samples
# ---------------------------------------------------------------------------
def test_auto_min_samples_full_day_matches_upstream_default():
    """Full-day 5-min data must resolve to the unchanged upstream guard (480), so
    existing decompositions are byte-for-byte unaffected by the auto-scaling."""
    df = _make_series()
    n = dc._auto_min_rolling_samples(df, "Date Time", freq_minutes=5)
    assert n == dc._UPSTREAM_MIN_ROLLING_SAMPLES == 480


def test_auto_min_samples_scales_down_for_narrow_window():
    """A 4–6PM window is 2/24 of the day → guard scales to ~1/12 of 480."""
    df = filter_time_window(_make_series(), "4:00PM-6:00PM")
    n = dc._auto_min_rolling_samples(df, "Date Time", freq_minutes=5)
    assert n == round(480 * (24 / 288))       # 24 five-min slots in two hours
    assert 30 < n < 60


# ---------------------------------------------------------------------------
# decompose_segments end-to-end
# ---------------------------------------------------------------------------
def test_full_day_decomposition_records_default_guard():
    out = dc.decompose_segments(_make_series())
    assert not out.empty
    assert out.attrs["min_rolling_window_samples"] == 480
    for c in (dc.TREND_COL, dc.SEASON_DAY_COL, dc.SEASON_WEEK_COL, dc.RESID_COL):
        assert c in out.columns


def test_filtered_window_still_decomposes():
    """The core of the feature: filtering to 4–6PM first would blow past the
    default 480-sample guard and return nothing — auto-scaling keeps it alive."""
    windowed = filter_time_window(_make_series(), "4:00PM-6:00PM")
    # Without the scaling the guard is unmet and the result is empty:
    empty = dc.decompose_segments(windowed, min_rolling_window_samples=480)
    assert empty.empty
    # With auto-scaling (default None) it decomposes:
    out = dc.decompose_segments(windowed)
    assert not out.empty
    assert out.attrs["min_rolling_window_samples"] < 480


def test_filter_then_changepoint_finds_pm_only_step():
    """Filter first, then decompose + changepoint: a step injected only into the
    5PM samples is recovered from the seasonally-adjusted 4–6PM series."""
    df = _make_series(step_size=4.0, step_hour=17, step_day=30, n_days=70)
    windowed = filter_time_window(df, "4:00PM-6:00PM")
    decomposed = dc.decompose_segments(windowed, value=VALUE)
    adj = dc.seasonally_adjust(decomposed, VALUE)
    work = decomposed.copy()
    work[adj.name] = adj.to_numpy()
    cps = cp.detect_changepoints(work, value=adj.name)
    assert len(cps) >= 1
    row = cps.iloc[cps[cp.SCORE_COL].argmax()]
    detected = pd.Timestamp(row["Date Time"]).date()
    true_date = (pd.Timestamp(START) + pd.Timedelta(30, "D")).date()
    assert abs((pd.Timestamp(detected) - pd.Timestamp(true_date)).days) <= 3
    assert row[cp.AVG_DIFF_COL] > 0          # detected an increase

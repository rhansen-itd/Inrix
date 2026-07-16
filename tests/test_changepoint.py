"""Tests for the changepoint adapter (changepoint.py) — ROADMAP Item 5.

Synthetic 5-min segment series with a *known* injected step change: the
detector should locate it at the right date, a stationary series should yield
none, and ``changepoints_near`` should relate detections to a known
intervention date.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from inrix_tools import changepoint as cp

TZ = "America/Denver"
VALUE = "Travel Time(Minutes)"
START = "2026-02-02"


def _make_series(step_size=5.0, step_day=30, n_days=60, noise=0.5, seed=0,
                 segments=(111,)):
    """5-min travel-time per segment with a level step of ``step_size`` starting
    ``step_day`` days into the series."""
    rng = np.random.RandomState(seed)
    idx = pd.date_range(START, periods=n_days * 288, freq="5min", tz=TZ)
    day_index = (idx - idx[0]).days
    frames = []
    for seg in segments:
        step = np.where(day_index >= step_day, step_size, 0.0)
        val = 10.0 + step + rng.normal(0, noise, len(idx))
        frames.append(pd.DataFrame({"Segment ID": seg, "Date Time": idx, VALUE: val}))
    df = pd.concat(frames, ignore_index=True)
    df["Segment ID"] = df["Segment ID"].astype("int64")
    df.attrs["units"] = {"speed": "miles/hour", "travel_time": "Minutes"}
    df.attrs["tz"] = TZ
    return df


def _step_date(step_day=30):
    return (pd.Timestamp(START) + pd.Timedelta(step_day, "D")).date()


# ---------------------------------------------------------------------------
# detect_changepoints
# ---------------------------------------------------------------------------
def test_step_change_detected_at_right_time():
    df = _make_series(step_size=5.0, step_day=30)
    out = cp.detect_changepoints(df)
    assert len(out) >= 1
    row = out.iloc[out[cp.SCORE_COL].argmax()]
    # detected within a couple of days of the true step, right magnitude/sign
    detected = pd.Timestamp(row["Date Time"]).date()
    assert abs((pd.Timestamp(detected) - pd.Timestamp(_step_date())).days) <= 2
    assert row[cp.AVG_DIFF_COL] == pytest.approx(5.0, abs=0.6)
    assert row[cp.AVG_DIFF_COL] > 0  # increase
    assert str(out["Date Time"].dt.tz) == TZ  # tz retained
    assert out.attrs["changepoint_value"] == VALUE


def test_stationary_series_yields_none():
    df = _make_series(step_size=0.0)
    out = cp.detect_changepoints(df)
    assert out.empty
    # empty result still carries the expected columns
    for c in ("Segment ID", "Date Time", *cp._RESULT_COLS):
        assert c in out.columns


def test_default_value_prefers_travel_time():
    df = _make_series()
    df["Speed(miles/hour)"] = 30
    out = cp.detect_changepoints(df)
    assert out.attrs["changepoint_value"] == VALUE


def test_multi_segment_only_shifted_ones_appear():
    df = _make_series(step_size=6.0, segments=(111, 222))
    # segment 222 has no step (overwrite its value with a flat series)
    flat = df["Segment ID"] == 222
    rng = np.random.RandomState(9)
    df.loc[flat, VALUE] = 10.0 + rng.normal(0, 0.5, flat.sum())
    out = cp.detect_changepoints(df)
    assert 111 in set(out["Segment ID"])
    assert 222 not in set(out["Segment ID"])


# ---------------------------------------------------------------------------
# changepoints_near
# ---------------------------------------------------------------------------
def test_changepoints_near_matches_known_date():
    df = _make_series(step_size=5.0, step_day=30, segments=(111, 222))
    out = cp.detect_changepoints(df)
    known = _step_date(30)  # the true intervention date
    near = cp.changepoints_near(out, known, window_days=7)
    assert not near.empty
    # every detected segment gets a row for the known date, and lines up with it
    assert set(near["Segment ID"]) == set(out["Segment ID"])
    assert near["within_window"].all()
    assert near["days_off"].abs().max() <= 3


def test_changepoints_near_flags_far_dates():
    df = _make_series(step_size=5.0, step_day=30)
    out = cp.detect_changepoints(df)
    far = pd.Timestamp(START).date()  # a date well before the step
    near = cp.changepoints_near(out, far, window_days=7)
    assert not near["within_window"].any()
    assert (near["days_off"].abs() > 7).all()


def test_changepoints_near_accepts_multiple_dates():
    df = _make_series(step_size=5.0, step_day=30, segments=(111,))
    out = cp.detect_changepoints(df)
    dates = [pd.Timestamp(START).date(), _step_date(30)]
    near = cp.changepoints_near(out, dates, window_days=7)
    # one row per (segment, known date)
    assert len(near) == 1 * len(dates)
    matched = near[near["within_window"]]
    assert len(matched) == 1
    assert matched.iloc[0]["known_date"].date() == _step_date(30)


def test_changepoints_near_empty_input():
    empty = cp.detect_changepoints(_make_series(step_size=0.0))
    near = cp.changepoints_near(empty, _step_date(30))
    assert near.empty
    assert "within_window" in near.columns

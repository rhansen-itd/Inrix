"""Tests for the decomposition adapter (decompose.py) and before/after analysis
(beforeafter.py) — ROADMAP Item 4.

Uses a synthetic 5-min segment series with a *known* injected before/after step
change plus daily + weekly seasonality, so the decomposition method can be shown
to recover the true shift while a raw comparison is biased by seasonality.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from inrix_tools import decompose as dc
from inrix_tools import beforeafter as ba

TZ = "America/Denver"
VALUE = "Travel Time(Minutes)"


def _make_series(
    n_days=50,
    step_day=30,
    step_size=2.0,
    daily_amp=3.0,
    weekend_bump=4.0,
    noise=0.3,
    seed=0,
    segments=(101,),
):
    """One or more segments of 5-min travel-time with daily + weekly seasonality
    and a level step of ``step_size`` starting at ``step_day``."""
    rng = np.random.RandomState(seed)
    idx = pd.date_range("2026-02-02", periods=n_days * 288, freq="5min", tz=TZ)
    frames = []
    day_index = (idx - idx[0]).days
    hour = idx.hour + idx.minute / 60.0
    daily = daily_amp * np.sin(2 * np.pi * (hour - 15) / 24)  # afternoon peak
    weekend = np.where(idx.dayofweek >= 5, weekend_bump, 0.0)
    step = np.where(day_index >= step_day, step_size, 0.0)
    for k, seg in enumerate(segments):
        base = 10.0 + k  # slightly different level per segment
        val = base + daily + weekend + step + rng.normal(0, noise, len(idx))
        frames.append(
            pd.DataFrame({"Segment ID": seg, "Date Time": idx, VALUE: val})
        )
    df = pd.concat(frames, ignore_index=True)
    df["Segment ID"] = df["Segment ID"].astype("int64")
    df.attrs["units"] = {"speed": "miles/hour", "travel_time": "Minutes"}
    df.attrs["tz"] = TZ
    return df


# ---------------------------------------------------------------------------
# decompose.py
# ---------------------------------------------------------------------------
def test_decompose_returns_components_and_keeps_tz():
    df = _make_series()
    out = dc.decompose_segments(df)
    for c in (dc.TREND_COL, dc.SEASON_DAY_COL, dc.SEASON_WEEK_COL, dc.RESID_COL,
              dc.PREDICTION_COL):
        assert c in out.columns
    # tz retained on output, units carried in attrs
    assert str(out["Date Time"].dt.tz) == TZ
    assert out.attrs["decompose_value"] == VALUE
    assert out.attrs["units"]["travel_time"] == "Minutes"
    # leading drop_days removed -> fewer rows than input
    assert len(out) < len(df)


def test_default_value_prefers_travel_time():
    df = _make_series()
    df["Speed(miles/hour)"] = 30
    assert dc.default_value_column(df) == VALUE


def test_seasonally_adjust_identity():
    df = _make_series()
    out = dc.decompose_segments(df)
    adj = dc.seasonally_adjust(out)
    # value - season_day - season_week == median + resid
    expect = out[dc.TREND_COL] + out[dc.RESID_COL]
    assert np.allclose(adj.to_numpy(), expect.to_numpy())


def test_seasonally_adjust_requires_components():
    df = _make_series()
    out = dc.decompose_segments(df, keep_components=False)
    with pytest.raises(ValueError):
        dc.seasonally_adjust(out)


# ---------------------------------------------------------------------------
# period parsing
# ---------------------------------------------------------------------------
def test_parse_period_date_only_end_is_inclusive_day():
    start, end = ba.parse_period(("2026-03-01", "2026-03-07"), tz=TZ)
    assert start == pd.Timestamp("2026-03-01", tz=TZ)
    # exclusive end rolls to midnight of the following day -> whole 7th included
    assert end == pd.Timestamp("2026-03-08", tz=TZ)


def test_parse_period_seed_string_form():
    start, end = ba.parse_period("20251022-20251117", tz=TZ)
    assert start == pd.Timestamp("2025-10-22", tz=TZ)
    assert end == pd.Timestamp("2025-11-18", tz=TZ)


def test_parse_period_rejects_bad_order():
    with pytest.raises(ValueError):
        ba.parse_period(("2026-03-08", "2026-03-01"), tz=TZ)


# ---------------------------------------------------------------------------
# compare_periods — recovering a known shift
# ---------------------------------------------------------------------------
def test_compare_periods_recovers_injected_shift():
    # no weekly confound: both periods are full weeks -> raw and decomp agree.
    df = _make_series(weekend_bump=0.0, step_day=25, step_size=2.0)
    before = ("2026-02-16", "2026-02-22")  # week before the step
    after = ("2026-03-09", "2026-03-15")   # week well after the step
    res = ba.compare_periods(df, before, after, value=VALUE)
    assert len(res) == 1
    row = res.iloc[0]
    assert row["method"] == "decomposition"
    assert row["effect"] == pytest.approx(2.0, abs=0.4)
    # CI excludes zero -> a detected change
    assert row["ci_low"] > 0
    assert row["n_before"] > 100 and row["n_after"] > 100


def test_decomposition_more_robust_than_raw_under_seasonality():
    # Weekly seasonality (+4 on weekends). Before period includes a weekend;
    # after period is weekdays-only -> raw comparison is biased by the differing
    # weekend fraction; decomposition strips the weekly season and recovers ~+2.
    df = _make_series(weekend_bump=4.0, step_day=25, step_size=2.0, noise=0.2)
    before = ("2026-02-16", "2026-02-22")           # full week incl. weekend
    after = ("2026-03-09", "2026-03-13")            # Mon-Fri only, no weekend
    decomp = ba.compare_periods(df, before, after, value=VALUE).iloc[0]
    raw = ba.compare_periods(
        df, before, after, value=VALUE, use_decomposition=False
    ).iloc[0]
    assert decomp["method"] == "decomposition" and raw["method"] == "raw"
    # decomposition lands near the true +2; raw is pulled off by the weekend bias.
    assert abs(decomp["effect"] - 2.0) < abs(raw["effect"] - 2.0)
    assert decomp["effect"] == pytest.approx(2.0, abs=0.5)


def test_compare_periods_null_case_ci_spans_zero():
    # no real step -> effect ~ 0, CI should straddle zero.
    df = _make_series(weekend_bump=0.0, step_size=0.0, noise=0.4)
    before = ("2026-02-16", "2026-02-22")
    after = ("2026-03-09", "2026-03-15")
    row = ba.compare_periods(df, before, after, value=VALUE).iloc[0]
    assert row["ci_low"] < 0 < row["ci_high"]


def test_compare_periods_by_grouping_and_multi_segment():
    df = _make_series(weekend_bump=0.0, step_size=2.0, step_day=25,
                      segments=(101, 202))
    df["Day Group"] = np.where(df["Date Time"].dt.dayofweek < 5, "Weekday", "Weekend")
    before = ("2026-02-16", "2026-02-22")
    after = ("2026-03-09", "2026-03-15")
    res = ba.compare_periods(df, before, after, value=VALUE, by=["Day Group"])
    # 2 segments x up to 2 day-groups, each recovering ~+2
    assert set(res["Segment ID"]) == {101, 202}
    assert "Day Group" in res.columns
    assert (res["effect"] - 2.0).abs().max() < 0.6


def test_compare_periods_skips_thin_groups():
    df = _make_series(weekend_bump=0.0)
    # after period with < min_samples rows for the segment -> group skipped
    before = ("2026-02-16", "2026-02-22")
    after = ("2026-03-09", "2026-03-15")
    res = ba.compare_periods(df, before, after, value=VALUE, min_samples=10_000_000)
    assert res.empty


# ---------------------------------------------------------------------------
# ttest_baseline — the seed's paired t-test
# ---------------------------------------------------------------------------
def test_ttest_baseline_columns_and_direction():
    df = _make_series(weekend_bump=0.0, step_size=2.0, step_day=25)
    before = ("2026-02-16", "2026-02-22")
    after = ("2026-03-09", "2026-03-15")
    res = ba.ttest_baseline(df, before, after, value=VALUE)
    assert len(res) == 1
    row = res.iloc[0]
    for c in ("avg_difference", "t_stat", "p_value", "n_bins"):
        assert c in res.columns
    # paired across time-of-day bins; a +2 step -> avg_difference ~ +2
    assert row["avg_difference"] == pytest.approx(2.0, abs=0.3)
    assert res.attrs["method"] == "ttest_baseline"


def test_methods_agree_in_easy_case():
    df = _make_series(weekend_bump=0.0, step_size=2.0, step_day=25)
    before = ("2026-02-16", "2026-02-22")
    after = ("2026-03-09", "2026-03-15")
    decomp = ba.compare_periods(df, before, after, value=VALUE).iloc[0]["effect"]
    tt = ba.ttest_baseline(df, before, after, value=VALUE).iloc[0]["avg_difference"]
    assert abs(decomp - tt) < 0.4  # both ~ +2 with no seasonal confound

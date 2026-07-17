"""Tests for inrix_tools.speed (ROADMAP Item 3).

Synthetic fixtures with hand-computed aggregates — no export needed. The point of
this module is compute split cleanly from plotting, so the tests assert exact
numbers and that no plotting library is imported.
"""
import importlib
import math

import pandas as pd
import pytest

from inrix_tools import speed
from inrix_tools import timebins as tb

TZ = "America/Denver"
SPEED = "Speed(miles/hour)"
TT = "Travel Time(Minutes)"


def _row(t, seg, sp, tt, corridor="9th"):
    return {
        "Date Time": pd.Timestamp(t, tz=TZ),
        "Segment ID": seg,
        SPEED: sp,
        TT: tt,
        "Corridor/Region Name": corridor,
    }


@pytest.fixture
def binned():
    """Two segments in one corridor across two dates; the 08:10 timestamp is
    missing segment 1002 (an incomplete set). All within the morning bin except
    one midday row that must fall out of the grouped summaries."""
    rows = [
        _row("2026-01-12 08:00", 1001, 30, 1.0),
        _row("2026-01-12 08:00", 1002, 40, 2.0),
        _row("2026-01-12 08:05", 1001, 32, 1.1),
        _row("2026-01-12 08:05", 1002, 44, 2.2),
        _row("2026-01-12 08:10", 1001, 28, 1.2),          # 1002 missing -> partial
        _row("2026-01-13 08:00", 1001, 20, 1.5),          # second date, seg 1001
        _row("2026-01-12 13:00", 1001, 55, 0.6),          # midday -> unbinned
    ]
    df = pd.DataFrame(rows)
    df.attrs["units"] = {"speed": "miles/hour", "travel_time": "Minutes"}
    df = tb.assign_day_group(df)
    df = tb.assign_time_bins(df, ["6:30AM-9:00AM"])
    df = tb.assign_group_label(df)
    return df


@pytest.fixture
def metadata():
    return (
        pd.DataFrame({"Segment ID": [1001, 1002], "Segment Length(Miles)": [0.5, 1.0]})
        .set_index("Segment ID")
    )


# --- column discovery -------------------------------------------------------
def test_metric_columns_detects_by_prefix():
    df = pd.DataFrame(columns=["Speed(kilometers/hour)", "Travel Time(Seconds)", "X"])
    assert speed.metric_columns(df) == {
        "speed": "Speed(kilometers/hour)",
        "travel_time": "Travel Time(Seconds)",
        "delay": None,
    }
    df2 = pd.DataFrame(columns=["Delay(Minutes)", "Travel Time(Minutes)"])
    assert speed.metric_columns(df2)["delay"] == "Delay(Minutes)"


# --- segment_summary --------------------------------------------------------
def test_segment_summary_known_aggregates(binned):
    out = speed.segment_summary(binned)
    seg1001 = out[(out["Segment ID"] == 1001)].iloc[0]
    # 1001 morning speeds across both dates: 30, 32, 28, 20
    assert seg1001[f"{SPEED}_count"] == 4
    assert seg1001[f"{SPEED}_mean"] == pytest.approx(27.5)
    assert seg1001[f"{SPEED}_median"] == pytest.approx(29.0)
    assert seg1001[f"{SPEED}_std"] == pytest.approx(
        pd.Series([30, 32, 28, 20]).std()  # ddof=1
    )
    # 1002 morning speeds: 40, 44
    seg1002 = out[(out["Segment ID"] == 1002)].iloc[0]
    assert seg1002[f"{SPEED}_count"] == 2
    assert seg1002[f"{TT}_mean"] == pytest.approx(2.1)


def test_segment_summary_drops_unassigned_bins(binned):
    """The 13:00 midday row has no time bin -> excluded from every group."""
    out = speed.segment_summary(binned)
    # 1001 morning count is 4, not 5 (the midday 55 mph row is gone)
    seg1001 = out[out["Segment ID"] == 1001].iloc[0]
    assert seg1001[f"{SPEED}_count"] == 4
    assert (out["Time Bin"] == "6:30AM-9:00AM").all()


def test_segment_summary_singleton_std_is_nan():
    df = pd.DataFrame([_row("2026-01-12 08:00", 1001, 30, 1.0)])
    df = tb.assign_time_bins(tb.assign_day_group(df), ["6:30AM-9:00AM"])
    out = speed.segment_summary(df, values=[SPEED])
    assert out[f"{SPEED}_count"].iloc[0] == 1
    assert math.isnan(out[f"{SPEED}_std"].iloc[0])


# --- daily_timebin_summary --------------------------------------------------
def test_daily_timebin_summary_mean_sd_bands(binned):
    out = speed.daily_timebin_summary(binned)
    # segment 1001, 2026-01-12: morning speeds 30, 32, 28 -> mean 30, std 2
    row = out[(out["Segment ID"] == 1001) & (out["Date"].astype(str) == "2026-01-12")].iloc[0]
    assert row["Count"] == 3
    assert row["Mean"] == pytest.approx(30.0)
    assert row["Std"] == pytest.approx(2.0)
    assert row["Upper"] == pytest.approx(32.0)
    assert row["Lower"] == pytest.approx(28.0)


def test_daily_timebin_summary_singleton_day_bands_nan(binned):
    out = speed.daily_timebin_summary(binned)
    # segment 1001, 2026-01-13 has a single morning row -> std/Upper/Lower NaN
    row = out[(out["Segment ID"] == 1001) & (out["Date"].astype(str) == "2026-01-13")].iloc[0]
    assert row["Count"] == 1
    assert math.isnan(row["Std"]) and math.isnan(row["Upper"]) and math.isnan(row["Lower"])


# --- corridor_travel_time ---------------------------------------------------
def test_corridor_complete_set_only(binned):
    out = speed.corridor_travel_time(binned)
    # only the two complete timestamps (08:00, 08:05) survive; 08:10 partial drops
    assert len(out) == 2
    assert out["complete"].all()
    first = out.sort_values("Date Time").iloc[0]
    assert first[TT] == pytest.approx(3.0)   # 1.0 + 2.0
    assert first["n_segments"] == 2 and first["expected_segments"] == 2


def test_corridor_partials_visible_when_not_required(binned):
    out = speed.corridor_travel_time(binned, require_complete=False)
    partial = out[~out["complete"]]
    # three single-segment timestamps: 08:10 (12th), 13:00 (12th), 08:00 (13th)
    assert len(partial) == 3
    assert (partial["n_segments"] == 1).all()
    # the 08:10 partial sums only seg 1001's travel time
    p0810 = partial[partial["Date Time"] == pd.Timestamp("2026-01-12 08:10", tz=TZ)]
    assert p0810.iloc[0][TT] == pytest.approx(1.2)


def test_corridor_length_and_space_mean_speed(binned, metadata):
    out = speed.corridor_travel_time(binned, metadata).sort_values("Date Time")
    row = out.iloc[0]
    assert row["Length(Miles)"] == pytest.approx(1.5)              # 0.5 + 1.0
    # space-mean speed = 1.5 mi / (3.0 min / 60) = 30 mph
    assert row["Corridor Speed(miles/hour)"] == pytest.approx(30.0)


def test_corridor_missing_column_raises():
    df = pd.DataFrame([{"Date Time": pd.Timestamp("2026-01-12 08:00", tz=TZ),
                        "Segment ID": 1, TT: 1.0}])
    with pytest.raises(ValueError):
        speed.corridor_travel_time(df)  # no corridor column


# --- network_travel_time (Item 12) ------------------------------------------
@pytest.fixture
def multi_corridor():
    """Three segments across two corridors. At 08:00 all three report (complete
    network set); at 08:05 segment 3 is missing (partial network set)."""
    rows = [
        _row("2026-01-12 08:00", 1, 30, 1.0, corridor="A"),
        _row("2026-01-12 08:00", 2, 40, 2.0, corridor="A"),
        _row("2026-01-12 08:00", 3, 50, 3.0, corridor="B"),
        _row("2026-01-12 08:05", 1, 31, 1.1, corridor="A"),
        _row("2026-01-12 08:05", 2, 41, 2.1, corridor="A"),
        # 08:05 missing segment 3 -> network set incomplete
    ]
    df = pd.DataFrame(rows)
    df.attrs["units"] = {"speed": "miles/hour", "travel_time": "Minutes"}
    return df


def test_network_sums_all_segments_complete_set_only(multi_corridor):
    out = speed.network_travel_time(multi_corridor)
    # only 08:00 has all three segments -> the sole complete network timestamp
    assert len(out) == 1
    row = out.iloc[0]
    assert row[TT] == pytest.approx(6.0)          # 1.0 + 2.0 + 3.0
    assert row["n_segments"] == 3 and row["expected_segments"] == 3
    # everything collapses to one "Network" group, regardless of corridor
    assert set(out["Corridor/Region Name"]) == {speed.NETWORK_LABEL}


def test_network_partials_visible_when_not_required(multi_corridor):
    out = speed.network_travel_time(multi_corridor, require_complete=False)
    partial = out[~out["complete"]]
    assert len(partial) == 1                        # the 08:05 two-segment set
    p = partial.iloc[0]
    assert p[TT] == pytest.approx(3.2) and p["n_segments"] == 2  # 1.1 + 2.1


def test_network_length_and_space_mean_speed(multi_corridor):
    meta = (pd.DataFrame({"Segment ID": [1, 2, 3],
                          "Segment Length(Miles)": [0.5, 1.0, 1.5]})
            .set_index("Segment ID"))
    out = speed.network_travel_time(multi_corridor, meta)
    row = out.iloc[0]
    assert row["Length(Miles)"] == pytest.approx(3.0)   # 0.5 + 1.0 + 1.5 (all segments)
    # space-mean speed = 3.0 mi / (6.0 min / 60) = 30 mph
    assert row["Corridor Speed(miles/hour)"] == pytest.approx(30.0)


def test_network_missing_travel_time_raises():
    df = pd.DataFrame([{"Date Time": pd.Timestamp("2026-01-12 08:00", tz=TZ),
                        "Segment ID": 1, SPEED: 30}])
    with pytest.raises(ValueError):
        speed.network_travel_time(df)  # no travel-time column


# --- rolling_average --------------------------------------------------------
def test_rolling_trailing_known_values(binned):
    r = speed.rolling_average(binned, value=SPEED, window=2)
    col = f"{SPEED} roll2 trailing"
    s1001 = r[r["Segment ID"] == 1001].sort_values("Date Time")
    # speeds in time order for 1001: 30, 32, 28, 55(midday), 20(next day)
    # trailing window=2: 30, 31, 30, 41.5, 37.5
    assert list(s1001[col]) == pytest.approx([30.0, 31.0, 30.0, 41.5, 37.5])


def test_rolling_does_not_cross_segment_boundary(binned):
    r = speed.rolling_average(binned, value=SPEED, window=5)
    col = f"{SPEED} roll5 trailing"
    # segment 1002's first row must be its own value, not blended with 1001's
    s1002 = r[r["Segment ID"] == 1002].sort_values("Date Time")
    assert s1002[col].iloc[0] == pytest.approx(40.0)


def test_rolling_leading_direction(binned):
    r = speed.rolling_average(binned, value=SPEED, window=2, direction="leading")
    col = f"{SPEED} roll2 leading"
    s1002 = r[r["Segment ID"] == 1002].sort_values("Date Time")
    # 1002 speeds in order: 40, 44 -> leading window=2: (40+44)/2=42, then 44
    assert list(s1002[col]) == pytest.approx([42.0, 44.0])


def test_rolling_bad_direction_raises(binned):
    with pytest.raises(ValueError):
        speed.rolling_average(binned, value=SPEED, direction="sideways")


# --- delay vs free-flow (Item 17) -------------------------------------------
REF = "Ref Speed(miles/hour)"
DELAY = "Delay(Minutes)"


def _drow(seg, sp, tt, ref, t="2026-01-12 08:00", corridor="9th"):
    return {
        "Date Time": pd.Timestamp(t, tz=TZ),
        "Segment ID": seg,
        SPEED: sp,
        TT: tt,
        REF: ref,
        "Corridor/Region Name": corridor,
    }


@pytest.fixture
def delay_df():
    # seg 1001: length 0.5 mi, obs 30 mph (=> TT 1.0 min), free-flow 60 mph
    #           => free-flow TT 0.5 min, delay 0.5 min.
    # seg 1002: length 1.0 mi, obs 40 mph (=> TT 1.5 min), free-flow 60 mph
    #           => free-flow TT 1.0 min, delay 0.5 min.
    df = pd.DataFrame([
        _drow(1001, 30, 1.0, 60),
        _drow(1002, 40, 1.5, 60),
    ])
    df.attrs["units"] = {"speed": "miles/hour", "travel_time": "Minutes"}
    return df


@pytest.fixture
def delay_meta():
    return (pd.DataFrame({"Segment ID": [1001, 1002],
                          "Segment Length(Miles)": [0.5, 1.0]})
            .set_index("Segment ID"))


def test_segment_delay_length_based(delay_df, delay_meta):
    out = speed.segment_delay(delay_df, geo_or_metadata=delay_meta)
    assert out[DELAY].tolist() == pytest.approx([0.5, 0.5])
    assert out.attrs["delay"] == {"free_flow": "ref", "floor": True,
                                   "length_source": "length"}
    # detected as a metric column now
    assert speed.metric_columns(out)["delay"] == DELAY


def test_segment_delay_speed_fallback_equals_length(delay_df, delay_meta):
    """With no length source the speed-based form gives the identical value."""
    length_based = speed.segment_delay(delay_df, geo_or_metadata=delay_meta)
    speed_based = speed.segment_delay(delay_df, geo_or_metadata=None)
    assert speed_based.attrs["delay"]["length_source"] == "speed"
    assert speed_based[DELAY].tolist() == pytest.approx(length_based[DELAY].tolist())


def test_segment_delay_floor(delay_meta):
    # observed *faster* than free-flow: TT 0.4 < free-flow 0.5 -> raw delay -0.1
    df = pd.DataFrame([_drow(1001, 75, 0.4, 60)])
    df.attrs["units"] = {"speed": "miles/hour", "travel_time": "Minutes"}
    floored = speed.segment_delay(df, geo_or_metadata=delay_meta)
    assert floored[DELAY].iloc[0] == pytest.approx(0.0)
    signed = speed.segment_delay(df, geo_or_metadata=delay_meta, floor=False)
    assert signed[DELAY].iloc[0] == pytest.approx(-0.1)


def test_segment_delay_percentile_free_flow():
    # three rows for one segment; 95th pct of observed speed is ~ the max here.
    rows = [_drow(1001, 30, 1.0, 60, t="2026-01-12 08:00"),
            _drow(1001, 60, 0.5, 60, t="2026-01-12 08:05"),
            _drow(1001, 60, 0.5, 60, t="2026-01-12 08:10")]
    df = pd.DataFrame(rows)
    df.attrs["units"] = {"speed": "miles/hour", "travel_time": "Minutes"}
    out = speed.segment_delay(df, geo_or_metadata=None, free_flow=("pXX", 95))
    # v_ff ~ 60 (95th pct); the 30-mph row: delay = 1.0*(1 - 30/60) = 0.5
    assert out.attrs["delay"]["free_flow"] == "p95"
    assert out[DELAY].iloc[0] == pytest.approx(0.5, abs=0.05)


def test_segment_delay_nan_on_bad_free_flow(delay_meta):
    df = pd.DataFrame([_drow(1001, 30, 1.0, 0)])  # ref speed 0 -> undefined
    df.attrs["units"] = {"speed": "miles/hour", "travel_time": "Minutes"}
    out = speed.segment_delay(df, geo_or_metadata=delay_meta)
    assert math.isnan(out[DELAY].iloc[0])


def test_segment_delay_missing_ref_raises():
    df = pd.DataFrame([{"Date Time": pd.Timestamp("2026-01-12 08:00", tz=TZ),
                        "Segment ID": 1001, SPEED: 30, TT: 1.0}])
    with pytest.raises(ValueError):
        speed.segment_delay(df, free_flow="ref")


def test_delay_summary_picks_up_delay(delay_df, delay_meta):
    """segment_summary auto-summarizes Delay once the column exists (by prefix)."""
    out = speed.segment_delay(delay_df, geo_or_metadata=delay_meta)
    out = tb.assign_day_group(out)
    out = tb.assign_time_bins(out, ["6:30AM-9:00AM"])
    summ = speed.segment_summary(out)
    assert f"{DELAY}_mean" in summ.columns


def test_corridor_delay_is_sum_of_members(delay_df, delay_meta):
    """Corridor delay sums member-segment delays under the complete-set rule."""
    out = speed.segment_delay(delay_df, geo_or_metadata=delay_meta)
    corr = speed.corridor_travel_time(out, value=DELAY)
    row = corr.iloc[0]
    assert row[DELAY] == pytest.approx(1.0)   # 0.5 + 0.5
    assert row["complete"]


# --- housekeeping -----------------------------------------------------------
def test_no_plotting_imports():
    """The compute core must not import a plotting/GUI stack (CLAUDE.md)."""
    import sys

    importlib.reload(speed)
    banned = {"matplotlib", "plotly", "dash"}
    assert banned.isdisjoint(sys.modules) or not any(
        getattr(speed, name, None) for name in banned
    )
    src = importlib.util.find_spec("inrix_tools.speed").origin
    text = open(src).read()
    for lib in banned:
        assert f"import {lib}" not in text


def test_attrs_preserved_and_input_unmutated(binned):
    before_cols = set(binned.columns)
    out = speed.daily_timebin_summary(binned)
    assert out.attrs.get("units") == {"speed": "miles/hour", "travel_time": "Minutes"}
    assert set(binned.columns) == before_cols  # no 'Date' leaked back onto input

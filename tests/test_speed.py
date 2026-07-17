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


@pytest.fixture
def sparse_network():
    """Three segments, but no timestamp ever holds all three — every timestamp is
    a two-segment subset (F6). ``expected='max'`` would call all of them complete;
    ``expected='total'`` (the network default) requires the full set of 3."""
    rows = [
        _row("2026-01-12 08:00", 1, 30, 1.0, corridor="A"),
        _row("2026-01-12 08:00", 2, 40, 2.0, corridor="A"),   # 3 missing
        _row("2026-01-12 08:05", 1, 31, 1.1, corridor="A"),
        _row("2026-01-12 08:05", 3, 50, 3.0, corridor="B"),   # 2 missing
    ]
    df = pd.DataFrame(rows)
    df.attrs["units"] = {"speed": "miles/hour", "travel_time": "Minutes"}
    return df


def test_network_default_total_drops_incomplete_subsets(sparse_network):
    # Default expected='total': every distinct segment (3) must be present; no
    # timestamp achieves that, so the "complete" network series is empty rather
    # than summing two different 2-segment subsets that aren't level-comparable.
    out = speed.network_travel_time(sparse_network)
    assert len(out) == 0
    allts = speed.network_travel_time(sparse_network, require_complete=False)
    assert (allts["expected_segments"] == 3).all()
    assert not allts["complete"].any()


def test_network_expected_max_keeps_mismatched_subsets(sparse_network):
    # The older behaviour: expected='max' = max simultaneous (2), so both
    # 2-segment timestamps count complete even though they sum different members
    # (1+2 vs 1+3) — this is the F6 hazard the 'total' default avoids.
    out = speed.network_travel_time(sparse_network, expected="max")
    assert len(out) == 2
    assert (out["expected_segments"] == 2).all()
    assert set(out[TT].round(1)) == {3.0, 4.1}  # 1.0+2.0 and 1.1+3.0


def test_network_total_agrees_when_full_set_achieved(multi_corridor):
    # When the whole membership is regularly present (as on Myrtle's 46 segments),
    # 'total' and 'max' coincide — the multi_corridor fixture reaches all 3 at 08:00.
    total = speed.network_travel_time(multi_corridor, expected="total")
    mx = speed.network_travel_time(multi_corridor, expected="max")
    assert len(total) == len(mx) == 1
    assert total.iloc[0][TT] == pytest.approx(mx.iloc[0][TT]) == pytest.approx(6.0)


# --- segment_coverage + explicit membership (Item 19) -----------------------
@pytest.fixture
def coverage_net():
    """Three segments over 10 timestamps. Segment 3 is chronically missing: it is
    the SOLE absent member at the first 4 timestamps (so dropping it recovers
    exactly those 4). Segments 1 and 2 report at every timestamp."""
    rows = []
    ts = pd.date_range("2026-01-12 08:00", periods=10, freq="5min", tz=TZ)
    for i, t in enumerate(ts):
        for seg in (1, 2, 3):
            if seg == 3 and i < 4:        # seg 3 absent at the first 4 timestamps
                continue
            rows.append(_row(t.strftime("%Y-%m-%d %H:%M"), seg, 30, 1.0, corridor="C"))
    df = pd.DataFrame(rows)
    df.attrs["units"] = {"speed": "miles/hour", "travel_time": "Minutes"}
    return df


def test_segment_coverage_flags_chronically_missing(coverage_net):
    cov = speed.segment_coverage(coverage_net).set_index("Segment ID")
    # seg 3 reports 6/10 timestamps; its removal recovers exactly 4 complete-set
    # timestamps (the ones where it was the sole missing member).
    assert cov.loc[3, "coverage"] == pytest.approx(0.6)
    assert cov.loc[3, speed.COMPLETE_COST_COL] == 4
    # segs 1 & 2 are always present -> full coverage, zero cost.
    for seg in (1, 2):
        assert cov.loc[seg, "coverage"] == pytest.approx(1.0)
        assert cov.loc[seg, speed.COMPLETE_COST_COL] == 0
    # baseline complete-set count over the full member set is 6 (timestamps 4..9).
    assert cov.attrs["n_complete"] == 6
    assert cov.attrs["n_members"] == 3 and cov.attrs["n_timestamps"] == 10
    # sorted most-worth-dropping first: the costly/low-coverage segment leads.
    assert speed.segment_coverage(coverage_net).iloc[0]["Segment ID"] == 3


def test_segment_coverage_cost_matches_recovered_timestamps(coverage_net):
    """The reported cost is *exact*: deselecting the flagged segment via the
    membership override raises the number of complete-set timestamps by that much."""
    full = speed.corridor_travel_time(coverage_net, require_complete=True)
    dropped = speed.corridor_travel_time(coverage_net, require_complete=True,
                                         members=[1, 2])
    cost = speed.segment_coverage(coverage_net).set_index("Segment ID").loc[
        3, speed.COMPLETE_COST_COL]
    assert len(full) == 6                        # only timestamps 4..9 complete
    assert len(dropped) == 10                    # dropping seg 3 recovers all 10
    assert len(dropped) - len(full) == cost      # exactly the reported cost (4)


def test_segment_coverage_subset_members_only(coverage_net):
    """Restricting to a member subset measures coverage over that subset alone: with
    only segments 1 & 2 (both always present) every timestamp is complete."""
    cov = speed.segment_coverage(coverage_net, members=[1, 2])
    assert set(cov["Segment ID"]) == {1, 2}
    assert (cov["coverage"] == 1.0).all()
    assert cov.attrs["n_complete"] == cov.attrs["n_timestamps"]


def test_segment_coverage_value_aware(coverage_net):
    """A NaN value counts as *not reported* (mirrors corridor_travel_time): blanking
    seg 1's travel time at one timestamp drops its coverage and adds a cost."""
    df = coverage_net.copy()
    # blank seg 1's value at timestamp index 5 (a currently-complete timestamp).
    mask = (df["Segment ID"] == 1) & (df["Date Time"] == pd.Timestamp("2026-01-12 08:25", tz=TZ))
    df.loc[mask, TT] = float("nan")
    cov = speed.segment_coverage(df, value=TT).set_index("Segment ID")
    assert cov.loc[1, "coverage"] == pytest.approx(0.9)   # 9/10 now
    assert cov.loc[1, speed.COMPLETE_COST_COL] == 1       # sole missing at that ts


def test_corridor_members_override_changes_aggregate(binned):
    """The additive members param feeds the complete-set sum exactly the selected
    segments — dropping the always-present seg 1002 leaves seg 1001 alone, so every
    1001 timestamp is 'complete' and the summed series changes."""
    both = speed.corridor_travel_time(binned)                      # complete set = 2
    only_1001 = speed.corridor_travel_time(binned, members=[1001])
    assert len(both) == 2
    # seg 1001 reports at 08:00/08:05/08:10 (binned midday/other-date rows exist too)
    assert len(only_1001) >= 3 and (only_1001["expected_segments"] == 1).all()
    assert set(only_1001[TT].round(1)) >= {1.0, 1.1, 1.2}


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


def test_corridor_value_sum_nan_rows_break_completeness(delay_meta):
    """Value-aware complete-set rule: a reported row whose value is NaN (delay
    with an unresolvable free-flow) contributes nothing to the sum, so the
    timestamp must NOT count complete — an undercounted "complete" sum, or a
    fabricated 0.0 from an all-NaN timestamp, is exactly what the rule exists to
    prevent."""
    rows = [
        # t1: both segments carry a delay -> complete, sum 1.0
        _drow(1001, 30, 1.0, 60, t="2026-01-12 08:00"),
        _drow(1002, 40, 1.5, 60, t="2026-01-12 08:00"),
        # t2: seg 1002 reports but its ref speed is 0 -> NaN delay
        _drow(1001, 30, 1.0, 60, t="2026-01-12 08:05"),
        _drow(1002, 40, 1.5, 0, t="2026-01-12 08:05"),
        # t3: both refs bad -> all-NaN
        _drow(1001, 30, 1.0, 0, t="2026-01-12 08:10"),
        _drow(1002, 40, 1.5, 0, t="2026-01-12 08:10"),
    ]
    df = pd.DataFrame(rows)
    df.attrs["units"] = {"speed": "miles/hour", "travel_time": "Minutes"}
    out = speed.segment_delay(df, geo_or_metadata=delay_meta)

    complete_only = speed.corridor_travel_time(out, value=DELAY)
    assert len(complete_only) == 1                      # only t1 survives
    assert complete_only[DELAY].iloc[0] == pytest.approx(1.0)

    all_ts = speed.corridor_travel_time(out, value=DELAY, require_complete=False)
    assert all_ts["complete"].tolist() == [True, False, False]
    assert all_ts["n_segments"].tolist() == [2, 1, 0]   # value-bearing segments
    assert math.isnan(all_ts[DELAY].iloc[2])            # all-NaN -> NaN, not 0.0

    # the travel-time path (never-NaN values) is unchanged by the value-aware rule
    tt_sum = speed.corridor_travel_time(out)
    assert len(tt_sum) == 3 and tt_sum["complete"].all()
    assert tt_sum[TT].tolist() == pytest.approx([2.5, 2.5, 2.5])


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

"""Tests for inrix_tools.timebins (ROADMAP Item 2).

Binning is pure and synthetic here — no export needed. Timestamps are built
tz-aware local (as ``io.to_local`` leaves them) so the wall-clock semantics the
functions rely on are exercised, including a DST-transition day.
"""
from datetime import time

import pandas as pd
import pytest

from inrix_tools import timebins as tb

TZ = "America/Denver"


def _df(local_strs, tz=TZ, segs=None):
    """Build a frame with a tz-aware local ``Date Time`` from naive local strings."""
    ts = pd.to_datetime(list(local_strs)).tz_localize(tz)
    n = len(ts)
    return pd.DataFrame({"Date Time": ts, "Segment ID": segs or list(range(n))})


# --- clock / bin parsing ----------------------------------------------------
def test_parse_clock_robust():
    assert tb.parse_clock("6:30AM") == time(6, 30)
    assert tb.parse_clock(" 12:00 am ") == time(0, 0)     # midnight, spaces, case
    assert tb.parse_clock("12:00PM") == time(12, 0)       # noon
    assert tb.parse_clock("9PM") == time(21, 0)           # minutes omitted


def test_parse_time_bin_flags_overnight():
    assert tb.parse_time_bin("6:30AM-9:00AM")[3] is False
    label, start, end, overnight = tb.parse_time_bin("9:00PM-6:00AM")
    assert overnight is True and start == 21 * 3600 and end == 6 * 3600


def test_bad_bin_string_raises():
    with pytest.raises(ValueError):
        tb.parse_time_bin("6:30AM..9:00AM")
    with pytest.raises(ValueError):
        tb.parse_clock("half past six")


# --- day grouping -----------------------------------------------------------
def test_assign_day_group_default_scheme():
    # Mon, Fri, Sat, Sun in Jan 2026 (12th=Mon .. 18th=Sun)
    df = _df(["2026-01-12 08:00", "2026-01-16 08:00",
              "2026-01-17 08:00", "2026-01-18 08:00"])
    out = tb.assign_day_group(df)
    assert list(out["Day Group"]) == ["Mon–Thu", "Fri", "Sat", "Sun"]


def test_assign_day_group_configurable_dict():
    df = _df(["2026-01-12 08:00", "2026-01-17 08:00", "2026-01-18 08:00"])
    # weekday vs weekend, Sunday deliberately unmapped -> NA
    scheme = {0: "Weekday", 1: "Weekday", 2: "Weekday", 3: "Weekday",
              4: "Weekday", 5: "Weekend"}
    out = tb.assign_day_group(df, scheme=scheme)
    assert list(out["Day Group"][:2]) == ["Weekday", "Weekend"]
    assert pd.isna(out["Day Group"].iloc[2])  # Sunday not in scheme


def test_assign_day_group_range_spec_list():
    """The seed's ``"Monday-Thursday"`` range-spec form, incl. wrap-around."""
    df = _df(["2026-01-12 08:00", "2026-01-15 08:00", "2026-01-16 08:00",
              "2026-01-18 08:00"])  # Mon, Thu, Fri, Sun
    out = tb.assign_day_group(df, scheme=["Friday-Monday", "Tuesday-Thursday"])
    # Fri–Mon wraps the weekend; Mon and Fri and Sun fall in it, Thu in the other
    assert out["Day Group"].iloc[0] == "Friday-Monday"      # Mon
    assert out["Day Group"].iloc[1] == "Tuesday-Thursday"   # Thu
    assert out["Day Group"].iloc[2] == "Friday-Monday"      # Fri
    assert out["Day Group"].iloc[3] == "Friday-Monday"      # Sun


# --- time binning -----------------------------------------------------------
def test_time_bin_edge_inclusivity_is_half_open():
    """Contiguous bins share an edge; the edge belongs to the *later* bin only —
    a boundary timestamp is never double-counted (the seed's <= <= bug)."""
    df = _df(["2026-01-12 06:30", "2026-01-12 09:00", "2026-01-12 14:00"])
    bins = ["6:30AM-9:00AM", "9:00AM-2:00PM"]
    out = tb.assign_time_bins(df, bins)
    assert out["Time Bin"].iloc[0] == "6:30AM-9:00AM"   # start is inclusive
    assert out["Time Bin"].iloc[1] == "9:00AM-2:00PM"   # shared edge -> later bin
    assert pd.isna(out["Time Bin"].iloc[2])             # 2:00PM end is exclusive


def test_time_bin_overnight_wrap():
    df = _df(["2026-01-12 22:00", "2026-01-12 03:00", "2026-01-12 06:00",
              "2026-01-12 12:00"])
    out = tb.assign_time_bins(df, ["9:00PM-6:00AM"])
    assert out["Time Bin"].iloc[0] == "9:00PM-6:00AM"   # 10PM, after start
    assert out["Time Bin"].iloc[1] == "9:00PM-6:00AM"   # 3AM, before wrap end
    assert pd.isna(out["Time Bin"].iloc[2])             # 6AM end exclusive
    assert pd.isna(out["Time Bin"].iloc[3])             # noon, outside


def test_time_bin_unassigned_is_na():
    df = _df(["2026-01-12 11:00"])
    out = tb.assign_time_bins(df, ["6:30AM-9:00AM"])
    assert pd.isna(out["Time Bin"].iloc[0])


def test_time_bin_first_listed_wins_on_overlap():
    df = _df(["2026-01-12 10:00"])
    out = tb.assign_time_bins(df, ["9:00AM-11:00AM", "10:00AM-12:00PM"])
    assert out["Time Bin"].iloc[0] == "9:00AM-11:00AM"


def test_time_bin_to_midnight():
    """An end of 12:00AM stops the bin exactly at midnight."""
    df = _df(["2026-01-12 22:00", "2026-01-12 23:59", "2026-01-13 00:00"])
    out = tb.assign_time_bins(df, ["10:00PM-12:00AM"])
    assert out["Time Bin"].iloc[0] == "10:00PM-12:00AM"
    assert out["Time Bin"].iloc[1] == "10:00PM-12:00AM"
    assert pd.isna(out["Time Bin"].iloc[2])             # 00:00 is the exclusive end


# --- DST correctness --------------------------------------------------------
def test_binning_is_wall_clock_across_dst():
    """2026 spring-forward is 2026-03-08 (02:00->03:00 MST->MDT). An 08:00-local
    row the day before and the day after both bin to the morning slot — binning
    reads the local wall clock, so the offset change is invisible to the bins."""
    df = _df(["2026-03-07 08:00", "2026-03-09 08:00"])  # Sat before, Mon after
    out = tb.assign_time_bins(df, ["6:30AM-9:00AM"])
    assert (out["Time Bin"] == "6:30AM-9:00AM").all()
    # confirm the underlying offsets really did change across the transition
    offsets = out["Date Time"].apply(lambda t: t.utcoffset())
    assert offsets.nunique() == 2


def test_dst_spring_forward_gap_hour_bins_normally():
    """01:30 exists (MST) but 02:30 does not on spring-forward day; a 03:30 row
    (first valid MDT half hour) still bins by its wall clock."""
    df = _df(["2026-03-08 01:30", "2026-03-08 03:30"])
    out = tb.assign_time_bins(df, ["12:00AM-6:00AM"])
    assert (out["Time Bin"] == "12:00AM-6:00AM").all()


# --- time-of-day window filtering -------------------------------------------
def test_filter_time_window_half_open_string():
    """String form keeps [start, end): the start edge is in, the end edge is out."""
    df = _df(["2026-01-12 15:59", "2026-01-12 16:00", "2026-01-12 17:30",
              "2026-01-12 18:00", "2026-01-12 18:01"])
    out = tb.filter_time_window(df, "4:00PM-6:00PM")
    assert list(out["Date Time"].dt.strftime("%H:%M")) == ["16:00", "17:30"]


def test_filter_time_window_hour_number_pair():
    """The GUI-slider form: an (hour, hour) numeric pair, fractional hours ok."""
    df = _df(["2026-01-12 16:00", "2026-01-12 16:14", "2026-01-12 16:15",
              "2026-01-12 17:00"])
    out = tb.filter_time_window(df, (16, 16.25))     # 4:00–4:15PM
    assert list(out["Date Time"].dt.strftime("%H:%M")) == ["16:00", "16:14"]


def test_filter_time_window_overnight_wrap():
    df = _df(["2026-01-12 22:00", "2026-01-12 03:00", "2026-01-12 06:00",
              "2026-01-12 12:00"])
    out = tb.filter_time_window(df, (21, 6))          # 9PM–6AM, wraps midnight
    assert list(out["Date Time"].dt.strftime("%H:%M")) == ["22:00", "03:00"]


def test_filter_time_window_full_day_is_noop():
    df = _df(["2026-01-12 00:00", "2026-01-12 12:00", "2026-01-12 23:55"])
    assert len(tb.filter_time_window(df, (0, 24))) == 3      # 24 == end of day
    assert len(tb.filter_time_window(df, (0, 0))) == 3       # equal bounds = whole day


def test_filter_time_window_attrs_and_input_unmutated():
    df = _df(["2026-01-12 16:00", "2026-01-12 20:00"])
    df.attrs["units"] = {"speed": "miles/hour"}
    out = tb.filter_time_window(df, "4:00PM-6:00PM")
    assert out.attrs["units"] == {"speed": "miles/hour"}
    assert out.attrs["time_window"] == "4:00PM-6:00PM"
    assert len(df) == 2  # original untouched


# --- group label composition ------------------------------------------------
def test_group_label_composition():
    df = _df(["2026-01-12 08:00", "2026-01-17 14:00", "2026-01-12 11:00"])
    out = tb.assign_day_group(df)
    out = tb.assign_time_bins(out, ["6:30AM-9:00AM", "2:00PM-7:00PM"])
    out = tb.assign_group_label(out)
    assert out["Group Label"].iloc[0] == "Mon–Thu, 6:30AM-9:00AM"
    assert out["Group Label"].iloc[1] == "Sat, 2:00PM-7:00PM"
    # 11:00 has a day group but no time bin -> whole label is NA
    assert pd.isna(out["Group Label"].iloc[2])


def test_group_label_na_when_day_missing():
    df = _df(["2026-01-18 08:00"])  # Sunday
    out = tb.assign_day_group(df, scheme={0: "Weekday"})  # Sun unmapped
    out = tb.assign_time_bins(out, ["6:30AM-9:00AM"])
    out = tb.assign_group_label(out)
    assert pd.isna(out["Group Label"].iloc[0])  # time present but day NA


# --- housekeeping -----------------------------------------------------------
def test_attrs_preserved_and_input_unmutated():
    df = _df(["2026-01-12 08:00"])
    df.attrs["units"] = {"speed": "miles/hour"}
    out = tb.assign_time_bins(df, ["6:30AM-9:00AM"])
    assert out.attrs["units"] == {"speed": "miles/hour"}
    assert "Time Bin" not in df.columns  # original frame untouched

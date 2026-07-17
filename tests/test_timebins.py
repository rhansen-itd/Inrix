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


# --- calendar date-range filtering (Item 11) --------------------------------
def _days(dates, tz=TZ):
    """A frame with one noon sample per given local calendar date."""
    return _df([f"{d} 12:00" for d in dates], tz=tz)


def test_filter_date_range_inclusive_edges():
    """Both endpoint days are kept; the day before start / after end are dropped."""
    df = _days(["2026-03-01", "2026-03-02", "2026-03-03", "2026-03-04", "2026-03-05"])
    out = tb.filter_date_range(df, "2026-03-02", "2026-03-04")
    assert list(out["Date Time"].dt.strftime("%Y-%m-%d")) == \
        ["2026-03-02", "2026-03-03", "2026-03-04"]


def test_filter_date_range_end_day_fully_inclusive():
    """A late-in-the-day sample on the end date is kept (inclusive whole day)."""
    df = _df(["2026-03-04 23:55", "2026-03-05 00:05"])
    out = tb.filter_date_range(df, "2026-03-01", "2026-03-04")
    assert list(out["Date Time"].dt.strftime("%Y-%m-%d %H:%M")) == ["2026-03-04 23:55"]


def test_filter_date_range_open_bounds():
    """A None/empty bound leaves that side open."""
    df = _days(["2026-03-01", "2026-03-02", "2026-03-03"])
    assert len(tb.filter_date_range(df, None, "2026-03-02")) == 2      # open left
    assert len(tb.filter_date_range(df, "2026-03-02", None)) == 2      # open right
    assert len(tb.filter_date_range(df, "", "")) == 3                  # both open = no-op
    assert tb.filter_date_range(df, "", "").attrs["date_range"] == (None, None)


def test_filter_date_range_records_span_and_preserves_input():
    df = _days(["2026-03-01", "2026-03-02", "2026-03-03"])
    df.attrs["units"] = {"speed": "miles/hour"}
    out = tb.filter_date_range(df, "2026-03-02", "2026-03-03")
    assert out.attrs["date_range"] == ("2026-03-02", "2026-03-03")
    assert out.attrs["units"] == {"speed": "miles/hour"}   # attrs carried through
    assert len(df) == 3                                     # original untouched
    assert df["Date Time"].dt.tz is not None and out["Date Time"].dt.tz is not None


def test_filter_date_range_dst_safe_whole_day():
    """Spring-forward day (2026-03-08 in Denver, 23h long) is kept whole."""
    df = _df(["2026-03-08 01:30", "2026-03-08 23:30"])
    out = tb.filter_date_range(df, "2026-03-08", "2026-03-08")
    assert len(out) == 2


def test_filter_date_range_start_after_end_is_empty():
    df = _days(["2026-03-01", "2026-03-02", "2026-03-03"])
    assert len(tb.filter_date_range(df, "2026-03-03", "2026-03-01")) == 0


def test_filter_date_range_tz_aware_bound_uses_local_day():
    """A tz-aware bound is converted to the frame's zone *before* its calendar
    day is taken — normalizing first would shift the cut by the zone offset and
    record the wrong day on attrs."""
    df = _df(["2026-02-28 18:00", "2026-03-01 00:00", "2026-03-01 08:00"])
    # 2026-03-01 12:00 UTC == 2026-03-01 05:00 in Denver -> local day March 1.
    out = tb.filter_date_range(df, start=pd.Timestamp("2026-03-01 12:00", tz="UTC"))
    assert list(out["Date Time"].dt.strftime("%Y-%m-%d %H:%M")) == \
        ["2026-03-01 00:00", "2026-03-01 08:00"]
    assert out.attrs["date_range"] == ("2026-03-01", None)


# --- day-of-week filtering (Item 13) ----------------------------------------
def test_parse_day_of_week_forms():
    assert tb.parse_day_of_week("Monday") == 0
    assert tb.parse_day_of_week("mon") == 0
    assert tb.parse_day_of_week("SUN") == 6
    assert tb.parse_day_of_week(4) == 4
    assert tb.parse_day_of_week("3") == 3
    assert tb.parse_day_of_week(4.0) == 4                  # int-valued float is fine
    with pytest.raises(ValueError):
        tb.parse_day_of_week(7)
    with pytest.raises(ValueError):
        tb.parse_day_of_week("someday")
    with pytest.raises(ValueError):
        tb.parse_day_of_week(6.9)                          # must not truncate to Sat


def test_filter_day_of_week_keeps_selected_days():
    # 2026-01-12 = Mon .. 2026-01-18 = Sun (one noon sample per day)
    df = _days([f"2026-01-{d}" for d in range(12, 19)])
    out = tb.filter_day_of_week(df, [5, 6])                 # Sat + Sun
    assert list(out["Date Time"].dt.dayofweek) == [5, 6]
    # names + abbrevs resolve to the same rows
    assert list(tb.filter_day_of_week(df, ["Saturday", "Sun"])["Date Time"].dt.dayofweek) \
        == [5, 6]


def test_filter_day_of_week_noop_and_attrs():
    df = _days([f"2026-01-{d}" for d in range(12, 19)])
    assert len(tb.filter_day_of_week(df, None)) == 7        # None = no-op
    assert len(tb.filter_day_of_week(df, [])) == 7          # empty = no-op
    assert len(tb.filter_day_of_week(df, list(range(7)))) == 7  # all seven = no-op
    assert tb.filter_day_of_week(df, list(range(7))).attrs["days_of_week"] is None
    weekdays = tb.filter_day_of_week(df, [0, 1, 2, 3, 4])
    assert weekdays.attrs["days_of_week"] == [0, 1, 2, 3, 4]
    assert len(weekdays) == 5


def test_filter_day_of_week_preserves_input_and_attrs():
    df = _days(["2026-01-12", "2026-01-17"])               # Mon, Sat
    df.attrs["units"] = {"speed": "miles/hour"}
    out = tb.filter_day_of_week(df, ["Sat"])
    assert out.attrs["units"] == {"speed": "miles/hour"}
    assert len(df) == 2                                    # original untouched


def test_filter_day_of_week_composes_with_time_window():
    """DOW and ToD compose: apply both, order-independent (whole-day filters)."""
    rows = [f"2026-01-{d} {h}:00" for d in range(12, 19) for h in (8, 17)]
    df = _df(rows)
    both = tb.filter_time_window(tb.filter_day_of_week(df, [5, 6]), "4:00PM-6:00PM")
    assert set(both["Date Time"].dt.dayofweek) == {5, 6}
    assert set(both["Date Time"].dt.hour) == {17}          # only the 5PM rows
    # composition is commutative on whole days
    other = tb.filter_day_of_week(tb.filter_time_window(df, "4:00PM-6:00PM"), [5, 6])
    assert len(both) == len(other) == 2


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

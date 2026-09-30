"""Golden tests for extents.peak_intensity and the class rule's use of it (Item 69).

Written before the implementation, as the seam: the numbers here are the spec.
"""
from __future__ import annotations

import math

import pandas as pd
import pytest

from inrix_tools import extents

BIN = 15
START = 540                      # 9:00
N12 = 48                         # a 12-hour window of 15-minute bins


def _seg(rows):
    """A segment_congestion-shaped frame: {id: (baseline_tt, miles)}."""
    return pd.DataFrame(
        {"baseline_tt": [b for b, _ in rows.values()],
         "miles": [m for _, m in rows.values()]},
        index=pd.Index(list(rows), name="Segment ID"))


def _bins(cells):
    """cells: iterable of (segment, tod_min, travel_time[, day_type[, month[, n_obs]]])."""
    out = []
    for c in cells:
        sid, tod, tt, *rest = c
        day = rest[0] if len(rest) > 0 else "sat"
        month = rest[1] if len(rest) > 1 else "2026-07"
        n = rest[2] if len(rest) > 2 else 4
        out.append({"Segment ID": sid, "month": month, "day_type": day,
                    "tod_min": tod, "travel_time": tt, "n_obs": n})
    return pd.DataFrame(out)


def _flat(sid, tt_at, n=N12, day="sat", start=START):
    """n consecutive bins for one segment; tt_at(i) gives bin i's travel time."""
    return [(sid, start + BIN * i, tt_at(i), day) for i in range(n)]


def test_sharp_two_hour_peak_in_a_twelve_hour_window():
    # The Item 69 case: the window mean dilutes it (56/48 = 1.167), the 2-hour
    # intensity keeps it.
    seg = _seg({1: (1.0, 1.0)})
    bins = _bins(_flat(1, lambda i: 2.0 if 20 <= i < 28 else 1.0))
    assert extents.peak_intensity([1], seg, bins) == pytest.approx(2.0)


def test_corridor_level_not_worst_segment():
    # Two segments peak at different times: the corridor is never at 3x.
    seg = _seg({1: (1.0, 1.0), 2: (1.0, 1.0)})
    bins = _bins(_flat(1, lambda i: 3.0 if i < 8 else 1.0, n=16)
                 + _flat(2, lambda i: 3.0 if 8 <= i < 16 else 1.0, n=16))
    assert extents.peak_intensity([1, 2], seg, bins) == pytest.approx(2.0)


def test_ratio_is_of_sums_weighted_by_travel_time():
    # Long segment (baseline 3) flat, short one (baseline 1) doubled: (3+2)/(3+1).
    seg = _seg({1: (3.0, 3.0), 2: (1.0, 1.0)})
    bins = _bins(_flat(1, lambda i: 3.0, n=8) + _flat(2, lambda i: 2.0, n=8))
    assert extents.peak_intensity([1, 2], seg, bins) == pytest.approx(1.25)


def test_run_does_not_span_day_types():
    # Saturday's last hour and Sunday's first are both 3x; no 2-hour run is.
    seg = _seg({1: (1.0, 1.0)})
    sat = _flat(1, lambda i: 3.0 if i >= 12 else 1.0, n=16, day="sat")
    sun = _flat(1, lambda i: 3.0 if i < 4 else 1.0, n=16, day="sun")
    assert extents.peak_intensity([1], seg, _bins(sat + sun)) == pytest.approx(2.0)


def test_run_does_not_span_a_time_gap():
    # AM and PM windows on one day type: 7:00-9:00 then 16:00-18:00. The last hour of
    # AM and first hour of PM are 3x; they are not contiguous.
    seg = _seg({1: (1.0, 1.0)})
    am = _flat(1, lambda i: 3.0 if i >= 4 else 1.0, n=8, day="weekday", start=420)
    pm = _flat(1, lambda i: 3.0 if i < 4 else 1.0, n=8, day="weekday", start=960)
    assert extents.peak_intensity([1], seg, _bins(am + pm)) == pytest.approx(2.0)


def test_no_full_run_is_nan():
    seg = _seg({1: (1.0, 1.0)})
    bins = _bins(_flat(1, lambda i: 2.0, n=7))
    assert math.isnan(extents.peak_intensity([1], seg, bins))


def test_months_pool_by_n_obs():
    seg = _seg({1: (1.0, 1.0)})
    bins = _bins([(1, START + BIN * i, 2.0, "sat", "2026-06", 3) for i in range(8)]
                 + [(1, START + BIN * i, 4.0, "sat", "2026-07", 1) for i in range(8)])
    assert extents.peak_intensity([1], seg, bins) == pytest.approx(2.5)


def test_segment_without_baseline_is_ignored():
    seg = _seg({1: (1.0, 1.0), 2: (float("nan"), 1.0)})
    bins = _bins(_flat(1, lambda i: 2.0, n=8) + _flat(2, lambda i: 10.0, n=8))
    assert extents.peak_intensity([1, 2], seg, bins) == pytest.approx(2.0)


def test_segment_outside_the_core_is_ignored():
    seg = _seg({1: (1.0, 1.0), 2: (1.0, 1.0)})
    bins = _bins(_flat(1, lambda i: 2.0, n=8) + _flat(2, lambda i: 10.0, n=8))
    assert extents.peak_intensity([1], seg, bins) == pytest.approx(2.0)


def test_bin_coverage_by_miles():
    # Segment 2 is missing from bin 3. At 0.05 mi of 1.05 known (95% covered) the bin
    # stands on segment 1 alone; at 1.0 mi of 2.0 (50%) it breaks the only 8-bin run.
    for miles2, expect in ((0.05, 2.0), (1.0, float("nan"))):
        seg = _seg({1: (1.0, 1.0), 2: (1.0, miles2)})
        cells = _flat(1, lambda i: 2.0, n=8) + [c for c in _flat(2, lambda i: 2.0, n=8)
                                                  if c[1] != START + 3 * BIN]
        got = extents.peak_intensity([1, 2], seg, _bins(cells))
        if math.isnan(expect):
            assert math.isnan(got)
        else:
            assert got == pytest.approx(expect)


def test_k_hours_must_be_whole_bins():
    seg = _seg({1: (1.0, 1.0)})
    bins = _bins(_flat(1, lambda i: 1.0, n=8))
    with pytest.raises(ValueError):
        extents.peak_intensity([1], seg, bins, k_hours=0.3)


def test_empty_bins_is_nan():
    seg = _seg({1: (1.0, 1.0)})
    assert math.isnan(extents.peak_intensity([1], seg, _bins([])))


# --- the class rule --------------------------------------------------------------

def _p(ratio, ratio_k):
    return {"peak_ratio": ratio, "peak_ratio_k": ratio_k}


def test_profile_class_uses_the_k_hour_intensity():
    # SH-75 Ketchum's shape: window means say commute (0.73 vs 0.27 excess); the
    # 2-hour intensities say recreational is the stronger signal.
    prof = {"commute": _p(1.73, 1.80), "recreational": _p(1.27, 2.10)}
    primary, cls = extents.profile_class(["commute", "recreational"], prof)
    assert primary == "recreational"
    assert cls == "commute+recreational"          # class keeps `found` order


def test_profile_class_falls_back_to_window_mean_unless_every_type_has_k():
    # A catalogue generated before Item 69 (no peak_ratio_k), or one type missing it:
    # never compare a k-hour intensity against a window mean.
    old = {"commute": {"peak_ratio": 1.73}, "recreational": {"peak_ratio": 1.27}}
    assert extents.profile_class(["commute", "recreational"], old) == ("commute", "commute")
    mixed = {"commute": _p(1.73, None), "recreational": _p(1.27, 2.10)}
    assert extents.profile_class(["commute", "recreational"], mixed) == ("commute", "commute")


def test_type_profile_records_both_ratios():
    seg = _seg({1: (1.0, 1.0)})
    seg = seg.assign(peak_tt=[1.2], delay_min=[0.2], vhd=[1.0], vhd_index=[1.0],
                     realtime_share=[1.0], ref_tti=[1.0], aadt=[100.0], weight=[1.0],
                     baseline_source=["night"])
    bins = _bins(_flat(1, lambda i: 2.0 if 20 <= i < 28 else 1.0))
    prof = extents.type_profile([1], {"recreational": seg}, bins={"recreational": bins})
    assert prof["recreational"]["peak_ratio"] == pytest.approx(1.2)
    assert prof["recreational"]["peak_ratio_k"] == pytest.approx(2.0)
    no_bins = extents.type_profile([1], {"recreational": seg})
    assert no_bins["recreational"]["peak_ratio_k"] is None


def test_recreational_intensity_ignores_friday():
    # A summer Friday afternoon is the PM commute, not recreation (Item 69 re-run).
    seg = _seg({1: (1.0, 1.0)})
    seg = seg.assign(peak_tt=[1.2], delay_min=[0.2], vhd=[1.0], vhd_index=[1.0],
                     realtime_share=[1.0], ref_tti=[1.0], aadt=[100.0], weight=[1.0],
                     baseline_source=["night"])
    bins = _bins(_flat(1, lambda i: 3.0 if 24 <= i < 32 else 1.0, day="weekday")
                 + _flat(1, lambda i: 1.5 if 20 <= i < 28 else 1.0, day="sun"))
    prof = extents.type_profile([1], {"recreational": seg, "commute": seg},
                                bins={"recreational": bins, "commute": bins})
    assert prof["recreational"]["peak_ratio_k"] == pytest.approx(1.5)
    assert prof["commute"]["peak_ratio_k"] == pytest.approx(3.0)

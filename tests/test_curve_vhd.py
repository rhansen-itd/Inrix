"""Tests for curve-weighted VHD (ROADMAP Item 57).

VHD per average day of the data period: each cell's (month × day type × bin) floored
delay × the segment's directional AADT × MADT_month/AADT × the curve's volume share
summed over the period's days in that cell, divided by the period's days.

The load-bearing checks, from the ROADMAP scope:

- a hand-computed VHD on a toy segment with a known curve;
- windows are additive (AM + PM = the VHD of their union);
- a flat curve reproduces ``index × window_hours / 24``;
- the MADT month weighting;
- weekend days use the Sat/Sun curves;

plus the per-bin floor, the pooled fill of a missing cell, and the extents callers.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from inrix_tools import aadt, extents, screen, volume_profiles as vp

TZ = "America/Denver"
SEG = 1000
REF = 1.0            # reference travel time, minutes
AADT = 1000.0        # per direction

FLAT = [1 / 24] * 24


def _peaky_hourly(peak_hour: int, share: float) -> list[float]:
    rest = (1.0 - share) / 23
    return [share if h == peak_hour else rest for h in range(24)]


def _library():
    """Three curves:

    - ``flat``: every hour 1/24, every day 1;
    - ``peaky``: weekday 50 % in the 08:00 hour, Saturday **nothing** at 08:00,
      Sunday flat; DOW 1.1 on weekdays, 0.75 at the weekend (mean 1);
    - ``sunday8``: flat, except Sunday's 08:00 hour carries 40 %.
    """
    sat = _peaky_hourly(8, 0.0)
    return vp.load_profiles({"profiles": [
        {"curve_id": "flat", "hourly": {"weekday": FLAT, "sat": FLAT, "sun": FLAT},
         "dow": [1.0] * 7},
        {"curve_id": "peaky",
         "hourly": {"weekday": _peaky_hourly(8, 0.5), "sat": sat, "sun": FLAT},
         "dow": [1.1] * 5 + [0.75, 0.75]},
        {"curve_id": "sunday8",
         "hourly": {"weekday": FLAT, "sat": FLAT, "sun": _peaky_hourly(8, 0.4)},
         "dow": [1.0] * 7},
    ]})


def _win(name, clock, days=None):
    return screen.PeakWindow(name, clock, days)


WEEKDAY = screen.WEEKDAYS


def _bins(start, end, tt, *, bin_minutes=60, seg=SEG, windows=None):
    """A bin screen: one cell per month × day type × bin over the period, travel time
    ``tt(month, day_type, tod_min)`` (NaN = absent), ``n_obs`` = the cell's days."""
    days = pd.date_range(start, end, freq="D")
    dtypes = np.asarray(vp.DAY_TYPES)[vp.day_type_index(days.dayofweek)]
    counts = pd.Series(1, index=pd.MultiIndex.from_arrays(
        [days.strftime("%Y-%m"), dtypes])).groupby(level=[0, 1]).sum()
    rows = []
    for (month, dt), n in counts.items():
        for tod in range(0, 24 * 60, bin_minutes):
            v = tt(month, dt, tod)
            if v is not None and not np.isnan(v):
                rows.append({"Segment ID": seg, "month": month, "day_type": dt,
                             "tod_min": tod, "travel_time": float(v), "n_obs": int(n)})
    out = pd.DataFrame(rows)
    out.attrs = {"bin_minutes": bin_minutes, "tz": TZ,
                 "period_start": pd.Timestamp(start).date().isoformat(),
                 "period_end": pd.Timestamp(end).date().isoformat(),
                 "windows": {w.name: w.to_dict() for w in (windows or [])}}
    return out


def _vhd(bins, windows, curve, *, madt=None, start, end, bin_minutes=60, **kw):
    w = vp.window_volume_weights(_library(), windows, start, end, TZ,
                                 bin_minutes=bin_minutes)
    vol = pd.DataFrame({"AADT": [AADT]}, index=pd.Index([SEG], name="Segment ID"))
    if madt is not None:
        for m in range(1, 13):
            vol[f"madt_ratio_{m:02d}"] = madt.get(m, 1.0)
    ref = pd.Series([REF], index=pd.Index([SEG], name="Segment ID"))
    curves = pd.Series([curve], index=pd.Index([SEG], name="Segment ID"))
    return aadt.curve_vehicle_hours_of_delay(bins, ref, vol, curves, w, **kw)


def _one(out, window):
    return out.set_index("window").loc[window]


# One week, Monday 9 March .. Sunday 15 March 2026: all MDT, 5 weekdays + Sat + Sun.
WEEK = ("2026-03-09", "2026-03-15")


# ---------------------------------------------------------------------------
# The weights
# ---------------------------------------------------------------------------
class TestWindowVolumeWeights:
    @pytest.mark.parametrize("start,end", [("2026-03-01", "2026-03-31"),   # spring DST
                                           ("2026-11-01", "2026-11-07")])  # fall DST
    def test_a_full_day_window_sums_to_the_days_dow_factors(self, start, end):
        """Whatever the day's length (23 h, 25 h), a day carries its DOW factor."""
        lib = _library()
        w = vp.window_volume_weights(lib, _win("all", "12:00AM-12:00AM"), start, end, TZ)
        days = pd.date_range(start, end)
        for cid, prof in lib.items():
            expect = sum(prof.dow[d.dayofweek] for d in days)
            assert w[w["curve_id"] == cid]["volume_days"].sum() == pytest.approx(expect)
        assert w.attrs["n_days"] == len(days)

    def test_the_day_gate_and_the_clock_follow_peakwindow_filter(self):
        w = vp.window_volume_weights(_library(), _win("am", "7:00AM-9:00AM", WEEKDAY),
                                     *WEEK, TZ, bin_minutes=60)
        flat = w[w["curve_id"] == "flat"]
        assert set(flat["day_type"]) == {"weekday"}
        assert sorted(flat["tod_min"]) == [420, 480]
        # five weekdays at 1/24 each
        assert flat["volume_days"].tolist() == pytest.approx([5 / 24, 5 / 24])
        assert w.attrs["window_days"] == {"am": 5} and w.attrs["n_days"] == 7
        assert w.attrs["window_days_by_month"] == {"am": {"2026-03": 5}}
        assert w.attrs["window_per"] == {"am": "weekday"}

    def test_an_overnight_window_wraps(self):
        w = vp.window_volume_weights(_library(), _win("night", "10:00PM-5:00AM"),
                                     *WEEK, TZ, bin_minutes=60)
        tods = sorted(set(w["tod_min"]))
        assert tods == [0, 60, 120, 180, 240, 1320, 1380]

    def test_days_by_month(self):
        w = vp.window_volume_weights(_library(), _win("all", "12:00AM-12:00AM"),
                                     "2026-03-30", "2026-04-02", TZ)
        assert w.attrs["days_by_month"] == {"2026-03": 2, "2026-04": 2}

    def test_an_empty_period_is_refused(self):
        with pytest.raises(ValueError):
            vp.window_volume_weights(_library(), _win("a", "7:00AM-9:00AM"),
                                     "2026-03-10", "2026-03-09", TZ)


# ---------------------------------------------------------------------------
# The VHD
# ---------------------------------------------------------------------------
class TestCurveVHD:
    def test_hand_computed_on_a_known_curve(self):
        """Weekday 08:00 runs 6 min over the reference; the peaky curve puts 50 % of a
        weekday's volume in that hour, and a weekday carries 1.1 days of volume.

        Per weekday: 6 min × 1,000 × 1.1 × 0.5 / 60 = 55 veh-h, and a weekday window
        is reported per weekday."""
        am = _win("am", "8:00AM-9:00AM", WEEKDAY)

        def tt(month, dt, tod):
            return REF + 6.0 if (dt == "weekday" and tod == 480) else REF

        out = _vhd(_bins(*WEEK, tt), [am], "peaky", start=WEEK[0], end=WEEK[1])
        row = _one(out, "am")
        assert row["vhd"] == pytest.approx(6.0 * AADT * 1.1 * 0.5 / 60)
        assert row["vhd_per"] == "weekday"
        assert row["vhd_annual"] == pytest.approx(row["vhd"] * 365 * 5 / 7)
        assert row["coverage"] == pytest.approx(1.0)
        assert "per weekday for a weekday window" in out.attrs["aadt_caveat"]

    def test_windows_are_additive(self):
        """Windows with the same gate add as VHD: AM + mid + PM = their union, per
        weekday. Windows with different gates add as totals (VHD × their days):
        15 weekdays of AM + 6 weekend days of AM = 21 days of the ungated AM."""
        rng = np.random.default_rng(57)
        table = {}

        def tt(month, dt, tod):
            return table.setdefault((month, dt, tod), REF + rng.uniform(-0.5, 4.0))

        start, end = "2026-03-23", "2026-04-12"          # three weeks over two months
        wins = [_win("am", "7:00AM-9:00AM", WEEKDAY), _win("pm", "4:00PM-7:00PM", WEEKDAY),
                _win("both", "7:00AM-7:00PM", WEEKDAY),
                _win("mid", "9:00AM-4:00PM", WEEKDAY),
                _win("am_we", "7:00AM-9:00AM", ("Sat", "Sun")),
                _win("am_all", "7:00AM-9:00AM")]
        out = _vhd(_bins(start, end, tt), wins, "peaky", madt={3: 0.9, 4: 1.2},
                   start=start, end=end)
        v = out.set_index("window")["vhd"]
        per = out.set_index("window")["vhd_per"]
        assert v["am"] + v["mid"] + v["pm"] == pytest.approx(v["both"])
        assert v["am"] * 15 + v["am_we"] * 6 == pytest.approx(v["am_all"] * 21)
        assert (per["am"], per["am_we"], per["am_all"]) == ("weekday", "weekend day", "day")

    @pytest.mark.parametrize("window", [
        _win("am", "7:00AM-9:00AM"),
        _win("am", "7:00AM-9:00AM", WEEKDAY),
        _win("day", "6:00AM-9:00PM")])
    def test_a_flat_curve_reproduces_the_index(self, window):
        """With a flat curve and no MADT, VHD = index × window hours / 24, gated or not.
        The index is the window's row-mean delay × AADT / 60."""
        rng = np.random.default_rng(1)
        table = {}

        def tt(month, dt, tod):
            return table.setdefault((month, dt, tod), REF + rng.uniform(0.0, 3.0))

        start, end = "2026-03-09", "2026-03-22"          # two whole weeks
        bins = _bins(start, end, tt)
        out = _vhd(bins, [window], "flat", start=start, end=end)
        in_win = bins[bins["tod_min"].between(*_hours(window))]
        if window.dows is not None:
            in_win = in_win[in_win["day_type"] == "weekday"]
        mean_delay = ((in_win["travel_time"] - REF) * in_win["n_obs"]).sum() / \
            in_win["n_obs"].sum()
        index = mean_delay * AADT / 60
        hours = (_hours(window)[1] - _hours(window)[0]) / 60 + 1
        assert _one(out, window.name)["vhd"] == pytest.approx(
            index * hours / 24)

    def test_madt_weights_each_month(self):
        """Constant 3-min delay all day: each day carries MADT_m/AADT of the volume."""
        start, end = "2026-03-30", "2026-04-02"          # 2 March days, 2 April days
        bins = _bins(start, end, lambda m, dt, tod: REF + 3.0)
        allday = _win("all", "12:00AM-12:00AM")
        out = _vhd(bins, [allday], "flat", madt={3: 0.8, 4: 1.2}, start=start, end=end)
        per_day = 3.0 * AADT / 60 * 24 / 24               # 50 veh-h at MADT = AADT
        assert _one(out, "all")["vhd"] == pytest.approx(per_day * (2 * 0.8 + 2 * 1.2) / 4)
        monthly = _vhd(bins, [allday], "flat", madt={3: 0.8, 4: 1.2}, start=start,
                       end=end, by_month=True).set_index("month")["vhd"]
        assert monthly["2026-03"] == pytest.approx(per_day * 0.8)
        assert monthly["2026-04"] == pytest.approx(per_day * 1.2)

    def test_weekend_days_use_the_sat_and_sun_curves(self):
        """Delay only at 08:00 on the weekend. ``peaky`` puts none of Saturday's volume
        at 08:00, so Saturday's delay costs nothing; Sunday is flat at 0.75 days."""
        am = _win("am", "8:00AM-9:00AM")

        def tt(month, dt, tod):
            return REF + 6.0 if (dt != "weekday" and tod == 480) else REF

        out = _vhd(_bins(*WEEK, tt), [am], "peaky", start=WEEK[0], end=WEEK[1])
        assert _one(out, "am")["vhd"] == pytest.approx(6.0 * AADT * 0.75 / 24 / 60 / 7)
        # sunday8: Sunday's 08:00 is 40 % of Sunday, Saturday's is 1/24.
        out = _vhd(_bins(*WEEK, tt), [am], "sunday8", start=WEEK[0], end=WEEK[1])
        assert _one(out, "am")["vhd"] == pytest.approx(
            6.0 * AADT * (0.4 + 1 / 24) / 60 / 7)

    def test_delay_is_floored_per_bin_not_per_window(self):
        """07:00 runs 2 min fast, 08:00 2 min slow: the window mean has no delay, but
        the 08:00 bin's vehicles were delayed and the 07:00 bin cannot pay for them."""
        w = _win("am", "7:00AM-9:00AM")

        def tt(month, dt, tod):
            return {420: REF - 2.0, 480: REF + 2.0}.get(tod, REF)

        out = _vhd(_bins(*WEEK, tt), [w], "flat", start=WEEK[0], end=WEEK[1])
        assert _one(out, "am")["vhd"] == pytest.approx(2.0 * AADT / 24 / 60)

    def test_a_missing_cell_is_filled_from_its_pooled_bin(self):
        """April's weekday 08:00 cell is absent: it takes the weekday 08:00 mean over
        the other month, and ``observed_share`` shows the fill."""
        start, end = "2026-03-30", "2026-04-02"
        w = _win("am", "8:00AM-9:00AM")

        def tt(month, dt, tod):
            if tod != 480:
                return REF
            return None if month == "2026-04" else REF + 3.0

        bins = _bins(start, end, tt)
        row = _one(_vhd(bins, [w], "flat", start=start, end=end), "am")
        assert row["vhd"] == pytest.approx(3.0 * AADT / 24 / 60)
        assert row["coverage"] == pytest.approx(1.0)
        assert row["observed_share"] == pytest.approx(0.5)
        raw = _one(_vhd(bins, [w], "flat", start=start, end=end, impute=False), "am")
        assert raw["vhd"] == pytest.approx(3.0 * AADT / 24 / 60 / 2)
        assert raw["coverage"] == pytest.approx(0.5)

    def test_no_volume_or_no_curve_is_nan_and_an_unknown_curve_is_refused(self):
        w = vp.window_volume_weights(_library(), _win("a", "8:00AM-9:00AM"), *WEEK, TZ,
                                     bin_minutes=60)
        bins = _bins(*WEEK, lambda m, dt, tod: REF + 1.0)
        bins = pd.concat([bins, bins.assign(**{"Segment ID": 2000}),
                          bins.assign(**{"Segment ID": 3000})], ignore_index=True)
        bins.attrs = {"bin_minutes": 60}
        ids = pd.Index([SEG, 2000, 3000], name="Segment ID")
        ref = pd.Series(REF, index=ids)
        out = aadt.curve_vehicle_hours_of_delay(
            bins, ref, pd.Series([AADT, 0.0, AADT], index=ids),
            pd.Series(["flat", "flat", None], index=ids), w)
        assert out.attrs["curve_vhd"]["n_no_volume"] == 1
        assert out.attrs["curve_vhd"]["n_no_curve"] == 1
        out = out.set_index("Segment ID")
        assert out.loc[SEG, "vhd"] == pytest.approx(AADT / 24 / 60)
        assert pd.isna(out.loc[2000, "vhd"])       # no volume
        assert pd.isna(out.loc[3000, "vhd"])       # no curve: still a row
        with pytest.raises(ValueError, match="no volume weights"):
            aadt.curve_vehicle_hours_of_delay(bins, ref, pd.Series(AADT, index=ids),
                                              pd.Series("nope", index=ids), w)


def _hours(window):
    """``(first bin start, last bin start)`` in minutes for a 60-min-bin window."""
    from inrix_tools import timebins
    _, s, e, _ = timebins.parse_time_bin(window.window)
    return s // 60, e // 60 - 60


# ---------------------------------------------------------------------------
# The extents callers
# ---------------------------------------------------------------------------
class TestExtentsCallers:
    WINS = [screen.PEAK_WINDOWS["am"], screen.PEAK_WINDOWS["pm"]]

    def _setup(self):
        ids = [1000, 1001]
        net = pd.DataFrame({"XDSegID": ids, "Miles": 0.5, "AADT": AADT},
                           index=pd.Index(ids, name="XDSegID"))
        base = pd.DataFrame({
            "n_obs": 10000, "ref_speed": 30.0,
            "am_travel_time": 1.0, "am_n_obs": 500, "am_realtime_share": 0.99,
            "pm_travel_time": [1.0, 2.0], "pm_n_obs": 500, "pm_realtime_share": 0.99,
            "night_travel_time": 1.0, "night_n_obs": 1000, "weekday_n_obs": 5000,
            "weekday_tt_p15": 1.0}, index=pd.Index(ids, name="Segment ID"))
        start, end = "2026-03-30", "2026-04-05"

        # 1001's PM runs 1 min over its 1.0-min night baseline, 1000 never does.
        def tt(month, dt, tod):
            return 2.0 if (dt == "weekday" and 960 <= tod < 1110) else 1.0

        b = [_bins(start, end, tt, bin_minutes=5, seg=s, windows=self.WINS) for s in ids]
        b[0]["travel_time"] = 1.0
        bins = pd.concat(b, ignore_index=True)
        bins.attrs = b[0].attrs
        curves = pd.Series("flat", index=pd.Index(ids, name="XDSegID"))
        return ids, net, base, bins, curves

    def test_segment_congestion_reads_the_curve_vhd_in_the_peak_window(self):
        ids, net, base, bins, curves = self._setup()
        seg = extents.segment_congestion(base, net, bins=bins, curves=curves,
                                         profiles=_library())
        # index: 1 min × 1,000 / 60; curve: × 2.5 h / 24 h, per weekday
        assert seg.loc[1001, "vhd_index"] == pytest.approx(AADT / 60)
        assert seg.loc[1001, "vhd"] == pytest.approx(AADT / 60 * 2.5 / 24)
        assert seg.loc[1000, "vhd"] == pytest.approx(0.0)
        assert seg.attrs["vhd_basis"] == "curve"
        plain = extents.segment_congestion(base, net)
        assert plain.attrs["vhd_basis"] == "index"
        assert (plain["vhd"] == plain["vhd_index"]).all()
        # A core carries both scales, so the floors' rescale can be read off (Item 58).
        core = extents.find_cores(ids, seg)[0]
        assert core.vhd == pytest.approx(AADT / 60 * 2.5 / 24)
        assert core.vhd_index == pytest.approx(AADT / 60)

    def test_monthly_profile_is_real_per_month_volume(self):
        ids, net, base, bins, curves = self._setup()
        for m in range(1, 13):
            net[f"madt_ratio_{m:02d}"] = {3: 0.8, 4: 1.2}.get(m, 1.0)
        seg = extents.segment_congestion(base, net, bins=bins, curves=curves,
                                         profiles=_library())
        weights = screen.bin_weights(bins, _library(), windows=["am", "pm"])
        monthly = aadt.curve_vehicle_hours_of_delay(bins, seg["baseline_tt"], net, curves,
                                                    weights, by_month=True)
        profile = extents.monthly_delay_profile(ids, seg, monthly,
                                                peak_windows=["am", "pm"])
        # per weekday of each month (30-31 March: Mon, Tue; 1-5 April: Wed-Fri)
        pm_day = AADT / 60 * 2.5 / 24
        assert profile["2026-03"] == pytest.approx(pm_day * 0.8)
        assert profile["2026-04"] == pytest.approx(pm_day * 1.2)

"""Tests for ROADMAP Item 65 — season-gated windows, named scenarios, and the
window-overlap guard.

The load-bearing checks:

- a seasonal window's SQL predicate keeps exactly the rows its pandas filter keeps,
  including a season that wraps the year end;
- the volume weights count only in-season days, and a seasonal window's annual VHD
  scales by its season, not the whole year;
- the guard refuses windows that would pool delay cells across different days, and
  lets same-coverage overlaps through;
- scenario names, tags and seasons resolve without colliding.
"""
from __future__ import annotations

import zipfile
from datetime import date, timedelta

import pandas as pd
import pytest

from inrix_tools import aadt, io, screen, store, timebins
from inrix_tools import volume_profiles as vp

duckdb = pytest.importorskip("duckdb")

TZ = "America/Denver"


# ---------------------------------------------------------------------------
# timebins: the season itself
# ---------------------------------------------------------------------------
class TestSeason:
    def test_parse_forms(self):
        assert timebins.parse_season(("05-22", "09-07")) == (522, 907)
        assert timebins.parse_season("05-22..09-07") == (522, 907)
        assert timebins.parse_season((522, 907)) == (522, 907)
        assert timebins.parse_season("02-29..03-01") == (229, 301)

    @pytest.mark.parametrize("bad", ["05-22", "13-01..01-02", "02-30..03-01",
                                     ("05-22",), "may..june"])
    def test_parse_rejects(self, bad):
        with pytest.raises(ValueError):
            timebins.parse_season(bad)

    def test_contains_is_inclusive_and_wraps(self):
        summer = ("05-22", "09-07")
        got = timebins.season_contains([521, 522, 701, 907, 908], summer)
        assert got.tolist() == [False, True, True, True, False]
        winter = "12-15..01-15"
        assert timebins.season_contains(1231, winter) is True
        assert timebins.season_contains(110, winter) is True
        assert timebins.season_contains(1201, winter) is False

    def test_days_per_year(self):
        assert timebins.season_days_per_year("05-22..09-07") == 10 + 30 + 31 + 31 + 7
        assert timebins.season_days_per_year("12-31..01-01") == 2


# ---------------------------------------------------------------------------
# PeakWindow.season
# ---------------------------------------------------------------------------
class TestPeakWindowSeason:
    def test_normalised_and_round_tripped(self):
        w = screen.PeakWindow("x", "9:00AM-9:00PM", ("Sat",), True, "5-22..9-7")
        assert w.season == ("05-22", "09-07")
        assert w.to_dict()["season"] == ["05-22", "09-07"]
        assert "season" not in screen.ALL_WINDOWS["sat"].to_dict()

    def test_with_season(self):
        w = screen.ALL_WINDOWS["sat"].with_season(("06-01", "08-31"), name="sat_jja")
        assert (w.name, w.season, w.days) == ("sat_jja", ("06-01", "08-31"), ("Sat",))
        assert w.with_season(None).season is None

    def test_predicate_reads_mmdd(self):
        w = screen.ALL_WINDOWS["sat"].with_season("12-15..01-15")
        pred = w.sql_predicate()
        assert "mmdd >= 1215 OR mmdd <= 115" in pred
        assert "dow IN (6)" in pred
        assert "mmdd" not in screen.ALL_WINDOWS["sat"].sql_predicate()


# ---------------------------------------------------------------------------
# SQL = pandas parity over a real store, across a year end
# ---------------------------------------------------------------------------
START = date(2025, 12, 26)
N_DAYS = 12                     # 26 Dec 2025 .. 6 Jan 2026, all MST
SEGS = (2001, 2002)
_HDR = ("Date Time,Segment ID,UTC Date Time,Speed(miles/hour),"
        "Hist Av Speed(miles/hour),Ref Speed(miles/hour),Travel Time(Minutes),"
        "CValue,Pct Score30,Pct Score20,Pct Score10,Road Closure,Corridor/Region Name\n")


def _tt(day_index: int, seg: int, hour: int) -> float:
    """Travel time that differs by day, hour and segment, so any row set a filter
    gets wrong changes the mean."""
    return round(1.0 + 0.1 * day_index + 0.01 * hour + (0.5 if seg == 2002 else 0.0), 3)


def _rows():
    for d in range(N_DAYS):
        day = START + timedelta(days=d)
        for hour in range(24):
            local = f"{day.isoformat()}T{hour:02d}:00:00-07:00"
            for seg in SEGS:
                tt = _tt(d, seg, hour)
                speed = round(60.0 / tt, 2)
                yield (f"{local},{seg},2026-01-01T00:00:00Z,{speed},{speed},60,{tt},"
                       f"100,100,0,0,F,YE\n")


@pytest.fixture(scope="module")
def year_end_area(tmp_path_factory):
    name = "YE_2025-12-26_to_2026-01-06_60_min"
    zpath = tmp_path_factory.mktemp("season") / f"{name}_part_1.zip"
    meta = ["Segment ID,Road,Direction,Start Latitude,End Latitude,Start Longitude,"
            "End Longitude,State/Region,District,Postal Code,Segment Length(Miles),"
            "Intersection"]
    meta += [f"{s},Toy Rd,N,43.6,43.61,-116.35,-116.35,Idaho,Ada,83702,1.0,X{s}"
             for s in SEGS]
    with zipfile.ZipFile(zpath, "w") as zf:
        zf.writestr(f"{name}/data.csv", _HDR + "".join(_rows()))
        zf.writestr(f"{name}/metadata.csv", "\n".join(meta) + "\n")
    con = store.connect(":memory:")
    key = store.ingest_export(con, zpath)["area_key"]
    yield con, key
    con.close()


@pytest.mark.parametrize("season", ["12-30..01-02", "01-03..01-05", None])
@pytest.mark.parametrize("days", [None, ("Sat", "Sun"), ("Fri",)])
def test_sql_matches_pandas(year_end_area, season, days):
    con, key = year_end_area
    w = screen.PeakWindow("w", "9:00AM-9:00PM", days, True, season)
    scr = screen.segment_screen(con, key, windows=[w], cvalue_threshold=None, tz=TZ)
    frame = io.to_local(store.load_export(con, key), TZ)
    kept = w.filter(frame)
    tt_col = [c for c in frame.columns if c.startswith("Travel Time")][0]
    expect = kept.groupby("Segment ID")[tt_col].mean()
    for seg in SEGS:
        got = scr.at[seg, "w_travel_time"]
        if seg in expect.index:
            assert got == pytest.approx(expect[seg])
        else:
            assert pd.isna(got)
    if season is not None:
        assert scr.attrs["windows"]["w"]["season"] == list(w.season)


def test_bin_screen_keeps_only_in_season_rows(year_end_area):
    con, key = year_end_area
    w = screen.PeakWindow("w", "12:00AM-12:00AM", None, True, "12-30..01-02")
    bins = screen.segment_bin_screen(con, key, windows=[w], cvalue_threshold=None, tz=TZ)
    # 30, 31 Dec + 1, 2 Jan: 2 days in 2025-12 and 2 in 2026-01, per segment per hour.
    per_month = bins[bins["Segment ID"] == 2001].groupby("month")["n_obs"].sum()
    assert per_month.to_dict() == {"2025-12": 2 * 24, "2026-01": 2 * 24}


# ---------------------------------------------------------------------------
# Volume weights and the annual figure
# ---------------------------------------------------------------------------
def _flat_library():
    flat = [1 / 24] * 24
    return vp.load_profiles({"profiles": [
        {"curve_id": "flat", "hourly": {"weekday": flat, "sat": flat, "sun": flat},
         "dow": [1.0] * 7}]})


class TestSeasonalWeights:
    def test_window_days_count_in_season_only(self):
        sat = screen.ALL_WINDOWS["sat"]
        summer_sat = sat.with_season("05-22..09-07", name="sat_summer")
        w = vp.window_volume_weights(_flat_library(), [sat, summer_sat],
                                     "2026-05-01", "2026-06-30", TZ, bin_minutes=60)
        # Saturdays in May-June 2026: 2, 9, 16, 23, 30 May; 6, 13, 20, 27 June.
        assert w.attrs["window_days"] == {"sat": 9, "sat_summer": 6}
        assert w.attrs["window_days_by_month"]["sat_summer"] == {"2026-05": 2,
                                                                 "2026-06": 4}
        assert w.attrs["window_per"] == {"sat": "gated day",
                                         "sat_summer": "gated day in season"}
        assert w.attrs["windows"]["sat_summer"]["season"] == [522, 907]
        assert set(w.loc[w["window"] == "sat_summer", "month"]) == {"2026-05", "2026-06"}

    def test_annual_vhd_scales_by_the_season(self):
        """A window whose season covers the whole data period has the same per-day VHD
        as the all-year one, and an annual figure scaled to its season."""
        start, end = "2026-06-01", "2026-06-30"
        allyear = screen.PeakWindow("d", "12:00AM-12:00AM")
        seasonal = allyear.with_season("05-22..09-07", name="d_summer")
        rows = []
        for month in ("2026-06",):
            for dt in vp.DAY_TYPES:
                for tod in range(0, 24 * 60, 60):
                    rows.append({"Segment ID": 1, "month": month, "day_type": dt,
                                 "tod_min": tod, "travel_time": 2.0, "n_obs": 4})
        bins = pd.DataFrame(rows)
        out = {}
        for w in (allyear, seasonal):
            weights = vp.window_volume_weights(_flat_library(), [w], start, end, TZ,
                                               bin_minutes=60)
            vol = pd.DataFrame({"AADT": [1000.0]}, index=pd.Index([1], name="Segment ID"))
            res = aadt.curve_vehicle_hours_of_delay(
                bins, pd.Series([1.0], index=pd.Index([1], name="Segment ID")), vol,
                pd.Series(["flat"], index=pd.Index([1], name="Segment ID")), weights)
            out[w.name] = res.iloc[0]
        assert out["d"]["vhd"] == pytest.approx(out["d_summer"]["vhd"])
        assert out["d_summer"]["vhd_annual"] == pytest.approx(
            out["d"]["vhd_annual"] * 109 / 365)


# ---------------------------------------------------------------------------
# The overlap guard
# ---------------------------------------------------------------------------
class TestWindowCells:
    @pytest.mark.parametrize("names", [["am", "pm"], ["am", "pm", "midday", "night"],
                                       ["am", "day_7d"], ["fri", "sat", "sun"],
                                       ["weekend", "sat"], screen.BASELINE_WINDOWS])
    def test_same_coverage_passes(self, names):
        screen.check_window_cells(names)

    def test_different_days_in_one_cell_raise(self):
        with pytest.raises(ValueError, match="'pm' and 'fri'"):
            screen.check_window_cells(["pm", "fri"])

    def test_seasonal_and_all_year_raise(self):
        sat = screen.ALL_WINDOWS["sat"]
        with pytest.raises(ValueError, match="share delay cells"):
            screen.check_window_cells([sat, sat.with_season("05-22..09-07", "sat_s")])

    def test_two_seasons_on_different_days_pass(self):
        sat = screen.ALL_WINDOWS["sat"].with_season("05-22..09-07", "sat_s")
        sun = screen.ALL_WINDOWS["sun"].with_season("05-22..09-07", "sun_s")
        screen.check_window_cells([sat, sun])

    def test_bin_screens_refuse(self, year_end_area):
        con, key = year_end_area
        with pytest.raises(ValueError, match="share delay cells"):
            screen.segment_bin_screen(con, key, windows=["pm", "fri"], tz=TZ)
        frame = io.to_local(store.load_export(con, key), TZ)
        with pytest.raises(ValueError, match="share delay cells"):
            screen.frame_bin_screen(frame, ["pm", "fri"], bin_minutes=60)


# ---------------------------------------------------------------------------
# Scenarios
# ---------------------------------------------------------------------------
class TestScenarios:
    def test_presets(self):
        peak = screen.resolve_scenario("peak")
        assert [w.name for w in peak.windows] == ["am", "pm", "midday", "night"]
        assert [w.name for w in peak.windows if w.peak] == ["am", "pm"]
        assert peak.window_map == screen.PEAK_WINDOWS
        assert (peak.tag, peak.file_tag) == (None, "peak")
        assert screen.resolve_scenario("day_7d").file_tag == "7day"
        assert screen.resolve_scenario("weekend").window_map["weekend"].dows == (5, 6)
        assert screen.resolve_scenario("fri_sun").window_map["fri_sun"].dows == (4, 5, 6)

    def test_named_season(self):
        sc = screen.resolve_scenario("sat:summer")
        assert (sc.name, sc.tag, sc.season) == ("sat_summer", "sat_summer",
                                                screen.SEASONS["summer"])
        (w,) = sc.windows
        assert (w.name, w.season, w.days) == ("sat_summer", screen.SEASONS["summer"],
                                              ("Sat",))
        assert "summer" in sc.label
        assert sc.to_dict()["windows"]["sat_summer"]["season"] == list(screen.SEASONS["summer"])

    def test_explicit_season_and_peak(self):
        sc = screen.resolve_scenario("peak:06-01..08-31")
        assert sc.file_tag == "peak_0601_0831"
        assert [w.name for w in sc.windows if w.peak] == ["am_0601_0831", "pm_0601_0831"]

    def test_unknown_and_duplicates(self):
        with pytest.raises(KeyError):
            screen.resolve_scenario("tuesday")
        with pytest.raises(ValueError):
            screen.resolve_scenario("sat:summerish")
        with pytest.raises(ValueError, match="share an output tag"):
            screen.resolve_scenarios("sat,sat")
        got = screen.resolve_scenarios("peak, day_7d,sat:summer")
        assert [s.file_tag for s in got] == ["peak", "7day", "sat_summer"]

    def test_every_preset_passes_the_guard(self):
        for name in screen.SCENARIO_PRESETS:
            screen.check_window_cells(screen.resolve_scenario(name).windows)
            screen.check_window_cells(screen.resolve_scenario(f"{name}:summer").windows)


# ---------------------------------------------------------------------------
# Item 68 follow-up: seasonal window names select from a frame's own windows
# ---------------------------------------------------------------------------
class TestSeasonalNames:
    """``sat_summer`` exists only on the frames built over it, never in the presets.
    The Item 68 run hit this in ``bin_weights``; ``rank_corridors(windows=)`` and the
    reporting totals looked names up the same way."""

    SAT = screen.ALL_WINDOWS["sat"].with_season("05-22..09-07", "sat_summer")

    def test_window_names(self):
        assert screen.window_names("sat_summer") == ["sat_summer"]
        assert screen.window_names([self.SAT, "am"]) == ["sat_summer", "am"]
        assert screen.window_names({"sat_summer": self.SAT}) == ["sat_summer"]
        with pytest.raises(TypeError):
            screen.window_names([3])

    def test_bin_weights_takes_a_seasonal_name(self):
        rows = [{"Segment ID": 1, "month": "2026-06", "day_type": "sat",
                 "tod_min": m, "travel_time": 2.0, "n_obs": 4}
                for m in range(9 * 60, 21 * 60, 60)]
        bins = pd.DataFrame(rows)
        bins.attrs = {"windows": {"sat_summer": self.SAT.to_dict()}, "tz": TZ,
                      "bin_minutes": 60, "period_start": "2026-06-01",
                      "period_end": "2026-06-30"}
        for arg in ("sat_summer", ["sat_summer"], [self.SAT], {"sat_summer": self.SAT}):
            w = screen.bin_weights(bins, _flat_library(), windows=arg)
            assert set(w["window"]) == {"sat_summer"}
            assert w.attrs["window_days"] == {"sat_summer": 4}
        with pytest.raises(KeyError, match="no window"):
            screen.bin_weights(bins, _flat_library(), windows=["sat"])

    def test_the_extents_congestion_path_reads_seasonal_bins(self):
        """What the Item 68 builder ran: ``segment_congestion`` with the recreational
        type's window names and curve-weighted bins."""
        import geopandas as gpd
        from shapely.geometry import LineString

        from inrix_tools import extents
        net = gpd.GeoDataFrame({"XDSegID": [1], "Miles": [1.0], "AADT": [1000.0]},
                               geometry=[LineString([(0, 0), (0, 0.01)])],
                               crs="EPSG:4326").set_index("XDSegID", drop=False)
        base = pd.DataFrame({"n_obs": [1000], "sat_summer_travel_time": [2.0],
                             "sat_summer_n_obs": [500], "sat_summer_realtime_share": [0.99],
                             "night_travel_time": [1.0], "night_n_obs": [1000],
                             "weekday_n_obs": [1000], "weekday_tt_p15": [1.0],
                             "ref_speed": [60.0]},
                            index=pd.Index([1], name="Segment ID"))
        rows = [{"Segment ID": 1, "month": "2026-06", "day_type": "sat",
                 "tod_min": m, "travel_time": 2.0, "n_obs": 4}
                for m in range(9 * 60, 21 * 60, 60)]
        bins = pd.DataFrame(rows)
        bins.attrs = {"windows": {"sat_summer": self.SAT.to_dict()}, "tz": TZ,
                      "bin_minutes": 60, "period_start": "2026-06-01",
                      "period_end": "2026-06-30"}
        curves = pd.Series(["flat"], index=pd.Index([1], name="Segment ID"))
        seg = extents.segment_congestion(base, net, peak_windows=("sat_summer",),
                                         bins=bins, curves=curves,
                                         profiles=_flat_library())
        assert seg.attrs["vhd_basis"] == "curve"
        assert seg.loc[1, "vhd"] > 0 and seg.loc[1, "peak_window"] == "sat_summer"

    def test_rank_corridors_and_totals_take_seasonal_names(self):
        ranking = pd.DataFrame({
            "corridor": ["c-nb", "c-nb"], "window": ["sat_summer", "am"],
            "is_peak": [True, True], "miles": [1.0, 1.0], "delay_min": [1.0, 0.5],
            "vhd": [10.0, 5.0], "vhd_index": [10.0, 5.0], "tti": [1.5, 1.2]})
        membership = pd.DataFrame({"id": ["c-nb"], "corridor": ["c"],
                                   "direction": ["NB"]})
        cells, used, _ = screen._peak_cells(ranking, membership, ["sat_summer"])
        assert used == ["sat_summer"] and cells["window"].tolist() == ["sat_summer"]
        cells, used, _ = screen._peak_cells(ranking, membership, [self.SAT])
        assert used == ["sat_summer"]

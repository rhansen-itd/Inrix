"""Tests for inrix_tools.screen — district-wide screening + ranking (ROADMAP Item 35).

The load-bearing checks: a named window means in **SQL** exactly what it means as a
pandas filter (the whole point of expressing the presets through ``timebins`` rather
than hand-rolling predicates), the CValue gate changes the counts and **travels with
the result**, and the ranking metrics come out of a corridor with hand-computed delay
at the value the arithmetic says — with delay floored once, at the segment.
"""
from __future__ import annotations

import zipfile
from datetime import date, timedelta
from pathlib import Path

import geopandas as gpd
import pandas as pd
import pytest
from shapely.geometry import LineString

from inrix_tools import corridors, geometry, io, screen, store, timebins
from inrix_tools.io import SEGMENT_COL

duckdb = pytest.importorskip("duckdb")

# --- the synthetic export ---------------------------------------------------
# One week of 15-minute data, 2026-03-09 (Mon) .. 2026-03-15 (Sun). The week is
# chosen to sit entirely inside MDT (DST starts 2026-03-08), so every local
# timestamp carries the same -06:00 offset and the expectations below are
# hand-computable without a DST case in the middle.
WEEK_START = date(2026, 3, 9)
N_DAYS = 7
SEG_IDS = [1000, 1001, 1002]
TZ = "America/Denver"
REF_SPEED = 60          # mph -> 1.0 min free-flow over a 1.0-mile segment
SEG_MILES = 1.0

# Travel time (minutes) by local hour-of-day — one value per named window, so each
# window's mean is the value itself.
TT_AM, TT_PM, TT_MIDDAY, TT_NIGHT, TT_OTHER = 2.0, 3.0, 1.5, 1.0, 1.2

_DATA_HDR = (
    "Date Time,Segment ID,UTC Date Time,Speed(miles/hour),"
    "Hist Av Speed(miles/hour),Ref Speed(miles/hour),Travel Time(Minutes),"
    "CValue,Pct Score30,Pct Score20,Pct Score10,Road Closure,Corridor/Region Name\n"
)


def _travel_time(hour: float) -> float:
    if 7 <= hour < 9:
        return TT_AM
    if 16 <= hour < 18.5:
        return TT_PM
    if 10 <= hour < 14:
        return TT_MIDDAY
    if hour >= 22 or hour < 5:
        return TT_NIGHT
    return TT_OTHER


def _cvalue(hour: float, day_index: int) -> str:
    """Mostly a passing 100. The first day's 07:00 hour is **below** the gate and the
    second day's 07:00 hour is **null** — the two ways a row fails ``CValue > 80``."""
    if 7 <= hour < 8:
        if day_index == 0:
            return "50"
        if day_index == 1:
            return ""          # null CValue: backfill, per DATA_FORMAT.md
    return "100"


def _rows():
    for d in range(N_DAYS):
        day = WEEK_START + timedelta(days=d)
        for step in range(96):                       # 15-minute bins
            hour = step / 4.0
            hh, mm = int(hour), int(round((hour - int(hour)) * 60))
            local = f"{day.isoformat()}T{hh:02d}:{mm:02d}:00-06:00"
            tt = _travel_time(hour)
            speed = round(SEG_MILES / tt * 60.0, 1)
            for seg in SEG_IDS:
                yield (f"{local},{seg},2026-01-01T00:00:00Z,{speed},{speed},"
                       f"{REF_SPEED},{tt},{_cvalue(hour, d)},100,0,0,F,D3\n")


def _metadata_csv():
    lines = ["Segment ID,Road,Direction,Start Latitude,End Latitude,Start Longitude,"
             "End Longitude,State/Region,District,Postal Code,Segment Length(Miles),"
             "Intersection"]
    for seg in SEG_IDS:
        lines.append(f"{seg},Toy Rd,N,43.60,43.61,-116.3545,-116.3545,Idaho,Ada,"
                     f"83702,{SEG_MILES},X{seg}")
    return "\n".join(lines) + "\n"


@pytest.fixture(scope="module")
def export_zip(tmp_path_factory):
    name = "Toy_2026-03-09_to_2026-03-15_15_min"
    zpath = tmp_path_factory.mktemp("screen") / f"{name}_part_1.zip"
    with zipfile.ZipFile(zpath, "w") as zf:
        zf.writestr(f"{name}/data.csv", _DATA_HDR + "".join(_rows()))
        zf.writestr(f"{name}/metadata.csv", _metadata_csv())
    return zpath


@pytest.fixture
def area(export_zip):
    """``(con, area_key)`` for the ingested toy export."""
    con = store.connect(":memory:")
    key = store.ingest_export(con, export_zip)["area_key"]
    yield con, key
    con.close()


def _toy_network():
    """Three collinear northbound 1.0-mile segments, linked head to tail — the same
    toy shape ``test_corridors`` walks, with the export's segment ids."""
    lon, lat0, dlat = -116.3545, 43.60, 0.01
    return gpd.GeoDataFrame(
        {
            "XDSegID": pd.array(SEG_IDS, dtype="Int64"),
            "NextXDSegI": pd.array(SEG_IDS[1:] + [pd.NA], dtype="Int64"),
            "Miles": [SEG_MILES] * len(SEG_IDS),
            "geometry": [LineString([(lon, lat0 + i * dlat), (lon, lat0 + (i + 1) * dlat)])
                         for i in range(len(SEG_IDS))],
        },
        crs=geometry.WGS84,
    )


def _toy_chain():
    net = _toy_network()
    return corridors.build_chain(net, (43.60, -116.3545), (43.63, -116.3545))


def _aadt(value=10_000, source="matched"):
    return pd.DataFrame({"AADT": [float(value)] * len(SEG_IDS),
                         "aadt_source": [source] * len(SEG_IDS)},
                        index=pd.Index(SEG_IDS, name=SEGMENT_COL))


# ---------------------------------------------------------------------------
# The window vocabulary
# ---------------------------------------------------------------------------
def test_presets_are_the_documented_windows():
    am = screen.PEAK_WINDOWS["am"]
    assert timebins.parse_time_bin(am.window)[1:3] == (7 * 3600, 9 * 3600)
    assert am.dows == (0, 1, 2, 3, 4) and am.peak
    pm = screen.PEAK_WINDOWS["pm"]
    assert timebins.parse_time_bin(pm.window)[1:3] == (16 * 3600, 18 * 3600 + 1800)
    assert pm.peak
    # midday is weekday-gated context; night is every day and wraps past midnight.
    assert not screen.PEAK_WINDOWS["midday"].peak
    assert screen.PEAK_WINDOWS["night"].dows is None
    assert timebins.parse_time_bin(screen.PEAK_WINDOWS["night"].window)[3] is True


def test_sql_predicate_derives_from_the_timebins_parse():
    assert screen.PeakWindow("x", "7:00AM-9:00AM").sql_predicate() == \
        "(tod >= 25200 AND tod < 32400)"
    # overnight wraps rather than matching nothing
    assert screen.PeakWindow("x", "10:00PM-5:00AM").sql_predicate() == \
        "(tod >= 79200 OR tod < 18000)"
    # isodow is 1=Mon, pandas dayofweek is 0=Mon
    assert screen.PeakWindow("x", "7:00AM-9:00AM", ("Mon", "Fri")).sql_predicate() == \
        "((tod >= 25200 AND tod < 32400) AND dow IN (1, 5))"
    # all seven days is a no-op gate, as in timebins.filter_day_of_week
    assert screen.PeakWindow("x", "7:00AM-9:00AM", range(7)).sql_predicate() == \
        "(tod >= 25200 AND tod < 32400)"


def test_resolve_windows_accepts_names_and_objects():
    assert list(screen.resolve_windows(["pm", "am"])) == ["pm", "am"]
    assert list(screen.resolve_windows(screen.PEAK_WINDOWS)) == \
        ["am", "pm", "midday", "night"]
    with pytest.raises(KeyError):
        screen.resolve_windows(["evening"])
    with pytest.raises(ValueError):
        screen.resolve_windows([screen.PEAK_WINDOWS["am"], screen.PEAK_WINDOWS["am"]])


# ---------------------------------------------------------------------------
# segment_screen: the SQL path *is* the pandas path
# ---------------------------------------------------------------------------
def test_screen_matches_the_pandas_window_filters(area, export_zip):
    """The reason the presets go through ``timebins``: the in-DuckDB screen must bin
    the rows exactly as the pandas filters do, segment by segment, window by window."""
    con, key = area
    scr = screen.segment_screen(con, key)

    raw = io.filter_cvalue(io.to_local(io.load_data(export_zip), TZ), 80)
    for name, w in screen.PEAK_WINDOWS.items():
        ref = w.filter(raw)
        counts = ref.groupby(SEGMENT_COL)["Travel Time(Minutes)"].count()
        means = ref.groupby(SEGMENT_COL)["Travel Time(Minutes)"].mean()
        speeds = ref.groupby(SEGMENT_COL)["Speed(miles/hour)"].mean()
        pd.testing.assert_series_equal(
            scr[f"{name}_n_obs"].astype("int64"), counts.astype("int64"),
            check_names=False)
        pd.testing.assert_series_equal(scr[f"{name}_travel_time"], means,
                                       check_names=False)
        pd.testing.assert_series_equal(scr[f"{name}_speed"], speeds, check_names=False)


def test_screen_window_bin_counts_are_hand_computable(area):
    """The real-data anchor, at toy scale: a 15-minute export gives 8 AM bins per
    weekday per segment (2 h / 15 min), and the 5 weekdays of the fixture week make
    40 — the same arithmetic that gives the 2026 D3 export 173 weekdays x 8 = 1,384."""
    con, key = area
    scr = screen.segment_screen(con, key, cvalue_threshold=None)
    assert (scr["am_n_obs"] == 5 * 8).all()            # 07:00-09:00, Mon-Fri
    assert (scr["pm_n_obs"] == 5 * 10).all()           # 16:00-18:30, Mon-Fri
    assert (scr["midday_n_obs"] == 5 * 16).all()       # 10:00-14:00, Mon-Fri
    # 22:00-05:00 is ungated by day: 8 late bins + 20 early bins on each of 7 days.
    assert (scr["night_n_obs"] == 7 * 28).all()
    assert (scr["n_obs"] == 7 * 96).all()
    assert scr.index.name == SEGMENT_COL and list(scr.index) == SEG_IDS


def test_screen_window_means(area):
    con, key = area
    scr = screen.segment_screen(con, key, cvalue_threshold=None)
    assert scr["am_travel_time"].eq(TT_AM).all()
    assert scr["pm_travel_time"].eq(TT_PM).all()
    assert scr["midday_travel_time"].eq(TT_MIDDAY).all()
    assert scr["night_travel_time"].eq(TT_NIGHT).all()
    assert scr["ref_speed"].eq(float(REF_SPEED)).all()


def test_cvalue_gate_changes_the_counts_and_records_its_threshold(area):
    """The gate is a *passing* gate, not an absent one: it changes the count, it says
    what it was, and the surviving share is carried per segment and per window."""
    con, key = area
    gated = screen.segment_screen(con, key)                      # default 80
    ungated = screen.segment_screen(con, key, cvalue_threshold=None)

    # Day 0's 07:00 hour is CValue 50 and day 1's is null: 4 bins each, both fail.
    assert (ungated["am_n_obs"] - gated["am_n_obs"]).eq(8).all()
    assert (ungated["n_obs"] - gated["n_obs"]).eq(8).all()
    assert gated.attrs["cvalue_threshold"] == 80
    assert ungated.attrs["cvalue_threshold"] is None
    assert gated["am_kept_fraction"].eq(32 / 40).all()
    assert ungated["am_kept_fraction"].eq(1.0).all()
    # the ungated counts stay visible beside the gated ones
    assert gated["am_n_obs_ungated"].eq(40).all()
    # and the window definitions travel with the frame
    assert gated.attrs["windows"]["am"]["window"] == "7:00AM-9:00AM"
    assert gated.attrs["tz"] == TZ and gated.attrs["bin_minutes"] == 15


def test_screen_subset_of_windows_and_date_push_down(area):
    con, key = area
    scr = screen.segment_screen(con, key, ["am"], None,
                                date_start="2026-03-09", date_end="2026-03-10")
    assert list(scr.attrs["windows"]) == ["am"]
    assert (scr["am_n_obs"] == 2 * 8).all()            # two weekdays only
    assert not any(c.startswith("pm_") for c in scr.columns)


def test_screen_rejects_a_gate_it_cannot_apply(area, tmp_path):
    """A CValue gate on an export with no CValue column is an error, not a silent
    no-op — the caller passes ``None`` to screen ungated, explicitly."""
    con, key = area
    con.execute(f'ALTER TABLE "{store._obs_table(key)}" DROP COLUMN "CValue"')
    with pytest.raises(ValueError, match="CValue"):
        screen.segment_screen(con, key)
    assert len(screen.segment_screen(con, key, cvalue_threshold=None)) == len(SEG_IDS)


def test_screen_unknown_area(area):
    con, _ = area
    with pytest.raises(KeyError):
        screen.segment_screen(con, "nope")


# ---------------------------------------------------------------------------
# rank_corridors
# ---------------------------------------------------------------------------
def test_rank_corridors_known_delay(area):
    """Three 1.0-mile segments at 60 mph reference: free-flow is 1.0 min each, so an
    AM travel time of 2.0 min is exactly 1.0 min of delay per segment."""
    con, key = area
    scr = screen.segment_screen(con, key, cvalue_threshold=None)
    ranked = screen.rank_corridors(scr, {"Toy Rd NB": _toy_chain()}, _aadt())

    am = ranked[ranked["window"] == "am"].iloc[0]
    assert am["n_segments"] == 3 and am["n_observed"] == 3
    assert am["miles"] == pytest.approx(3.0) and am["missing_miles"] == 0.0
    assert am["travel_time_min"] == pytest.approx(3 * TT_AM)
    assert am["free_flow_min"] == pytest.approx(3.0)
    assert am["delay_min"] == pytest.approx(3.0)
    assert am["tti"] == pytest.approx(2.0)
    assert am["delay_per_mile"] == pytest.approx(1.0)
    # vehicle-hours: 3 segments x (1 min / 60) x 10,000 vehicles
    assert am["vhd"] == pytest.approx(500.0)
    assert am["vhd_per_mile"] == pytest.approx(500.0 / 3.0)
    assert am["n_obs"] == 3 * 5 * 8

    pm = ranked[ranked["window"] == "pm"].iloc[0]
    assert pm["delay_min"] == pytest.approx(6.0) and pm["tti"] == pytest.approx(3.0)
    # night sits at the reference speed: no delay, and the floor keeps it at zero.
    night = ranked[ranked["window"] == "night"].iloc[0]
    assert night["delay_min"] == pytest.approx(0.0)


def test_rank_corridors_worst_peak_is_the_worst_peak_window(area):
    """``worst_peak`` ranks only the peak-flagged windows — midday is context, and
    cannot win even if it were the worst."""
    con, key = area
    scr = screen.segment_screen(con, key, cvalue_threshold=None)
    ranked = screen.rank_corridors(scr, {"Toy Rd NB": _toy_chain()}, _aadt())
    assert set(ranked["worst_peak"]) == {"pm"}          # TT_PM > TT_AM
    assert ranked.loc[ranked["window"] == "am", "is_peak"].all()
    assert not ranked.loc[ranked["window"] == "midday", "is_peak"].any()


def test_rank_corridors_floors_delay_at_the_segment(area):
    """A segment running *faster* than its free-flow reference contributes zero, not
    a credit against a congested neighbour — the floor is applied once, per segment,
    and the same floored values feed ``delay_min`` and ``vhd``."""
    con, key = area
    scr = screen.segment_screen(con, key, cvalue_threshold=None)
    scr.loc[SEG_IDS[2], "midday_travel_time"] = 0.5      # 0.5 min against a 1.0 min FF
    ranked = screen.rank_corridors(scr, {"Toy Rd NB": _toy_chain()}, _aadt())
    midday = ranked[ranked["window"] == "midday"].iloc[0]
    # two segments at +0.5 min, the third floored to 0 (a corridor-level floor of the
    # signed sum would have given 0.5).
    assert midday["delay_min"] == pytest.approx(1.0)
    assert midday["vhd"] == pytest.approx(2 * (0.5 / 60.0) * 10_000)
    assert ranked.attrs["delay_floor"] == "segment"


def test_rank_corridors_accounts_for_a_member_the_screen_never_saw(area):
    """A member absent from the screen lands in ``missing_miles``; it does not quietly
    shrink the corridor, and it does not get credited pavement it contributed no
    travel time to."""
    con, key = area
    scr = screen.segment_screen(con, key, cvalue_threshold=None).drop(index=SEG_IDS[1])
    ranked = screen.rank_corridors(scr, {"Toy Rd NB": _toy_chain()}, _aadt())
    am = ranked[ranked["window"] == "am"].iloc[0]
    assert am["n_segments"] == 3 and am["n_observed"] == 2
    assert am["miles"] == pytest.approx(2.0)
    assert am["missing_miles"] == pytest.approx(1.0)
    assert am["miles_covered_fraction"] == pytest.approx(2 / 3)
    assert am["delay_min"] == pytest.approx(2.0)
    assert am["delay_per_mile"] == pytest.approx(1.0)   # not deflated by the missing mile


def test_rank_corridors_prorates_a_trimmed_end_segment():
    """The chain's extent fractions are the proration ``corridors.chain_travel_time``
    applies; a half-covered end segment contributes half its delay and half its
    miles."""
    net = _toy_network()
    chain = corridors.build_chain(net, (43.60, -116.3545), (43.625, -116.3545))
    assert chain.extent_fraction[-1] == pytest.approx(0.5, abs=1e-6)

    scr = pd.DataFrame(
        {"n_obs": 10, "n_obs_ungated": 10, "kept_fraction": 1.0, "ref_speed": 60.0,
         "am_travel_time": 2.0, "am_speed": 30.0, "am_n_obs": 10,
         "am_n_obs_ungated": 10, "am_kept_fraction": 1.0},
        index=pd.Index(SEG_IDS, name=SEGMENT_COL))
    scr.attrs = {"windows": {"am": screen.PEAK_WINDOWS["am"].to_dict()},
                 "units": {"speed": "miles/hour", "travel_time": "Minutes"}}
    ranked = screen.rank_corridors(scr, {"Toy Rd NB": chain}, _aadt())
    am = ranked.iloc[0]
    assert am["miles"] == pytest.approx(2.5, abs=1e-6)
    assert am["delay_min"] == pytest.approx(2.5, abs=1e-6)    # 1.0 + 1.0 + 0.5
    assert am["vhd"] == pytest.approx(2.5 / 60.0 * 10_000, rel=1e-6)


def test_rank_corridors_carries_the_aadt_caveat_and_ramp_flag(area):
    """The AADT weighting is Item 34's: a ramp-weighted row is **counted**, not
    excluded, and the daily-total caveat travels with the ranking."""
    con, key = area
    scr = screen.segment_screen(con, key, cvalue_threshold=None)
    ranked = screen.rank_corridors(scr, {"Toy Rd NB": _toy_chain()},
                                   _aadt(source="matched_ramp"))
    assert (ranked["n_ramp_weighted"] == 3).all()
    assert ranked[ranked["window"] == "am"].iloc[0]["vhd"] == pytest.approx(500.0)
    assert "daily total" in ranked.attrs["aadt_caveat"]
    assert ranked.attrs["cvalue_threshold"] is None


def test_rank_corridors_without_aadt_reports_no_vehicle_hours(area):
    con, key = area
    scr = screen.segment_screen(con, key, cvalue_threshold=None)
    ranked = screen.rank_corridors(scr, [("Toy Rd NB", _toy_chain())])
    assert ranked["vhd"].isna().all() and ranked["vhd_per_mile"].isna().all()
    assert ranked.attrs["aadt_caveat"] is None
    assert ranked["delay_min"].notna().all()           # delay still computed


def test_rank_corridors_rejects_a_window_the_screen_lacks(area):
    con, key = area
    scr = screen.segment_screen(con, key, ["am"], None)
    with pytest.raises(KeyError, match="pm"):
        screen.rank_corridors(scr, {"Toy Rd NB": _toy_chain()}, _aadt(), windows=["pm"])


# ---------------------------------------------------------------------------
# Self-skipping real-export check
# ---------------------------------------------------------------------------
REPO_ROOT = Path(__file__).resolve().parents[1]
MYRTLE_ZIP = REPO_ROOT / "Myrtle_2026-02-01_to_2026-07-16_5_min_part_1.zip"


@pytest.mark.skipif(not MYRTLE_ZIP.exists(), reason="real Myrtle export not present")
def test_real_export_screen_matches_the_pandas_filters(tmp_path):
    """The whole path on real data: 2.18 M rows streamed into the store, screened in
    DuckDB, and checked segment by segment against the pandas window filters — the
    one place the SQL predicate, the timezone conversion and the CValue gate are all
    exercised together on an export nobody wrote for the test."""
    con = store.connect(tmp_path / "real.duckdb")     # on disk: 2.18 M rows
    try:
        key = store.ingest_export_streaming(con, MYRTLE_ZIP)["area_key"]
        scr = screen.segment_screen(con, key, ["am", "night"], tz=TZ)
        raw = io.filter_cvalue(io.to_local(io.load_data(MYRTLE_ZIP), TZ), 80)
        for name in ("am", "night"):
            ref = screen.PEAK_WINDOWS[name].filter(raw)
            counts = ref.groupby(SEGMENT_COL)["Travel Time(Minutes)"].count()
            means = ref.groupby(SEGMENT_COL)["Travel Time(Minutes)"].mean()
            pd.testing.assert_series_equal(scr[f"{name}_n_obs"].astype("int64"),
                                           counts.astype("int64"), check_names=False)
            pd.testing.assert_series_equal(scr[f"{name}_travel_time"], means,
                                           check_names=False)
        assert scr.attrs["cvalue_threshold"] == 80
    finally:
        con.close()

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


def test_resolve_windows_knows_the_7day_preset():
    """The ``day_7d`` window is a first-class preset in ``ALL_WINDOWS``, resolvable
    by string name — ``--windows day_7d`` works without constructing a PeakWindow."""
    resolved = screen.resolve_windows(["day_7d"])
    assert list(resolved) == ["day_7d"]
    w = resolved["day_7d"]
    assert w.peak is True
    assert w.dows is None        # all 7 days (no day-of-week gate)
    assert w is screen.ALL_DAY_7D_WINDOW
    # It also resolves when mixed with commute windows.
    mixed = screen.resolve_windows(["am", "day_7d"])
    assert list(mixed) == ["am", "day_7d"]


def test_all_windows_is_a_superset_of_peak_windows():
    """``ALL_WINDOWS`` contains every ``PEAK_WINDOWS`` entry plus the day_7d."""
    for name, window in screen.PEAK_WINDOWS.items():
        assert screen.ALL_WINDOWS[name] is window
    assert "day_7d" in screen.ALL_WINDOWS
    assert screen.ALL_WINDOWS["day_7d"] is screen.ALL_DAY_7D_WINDOW


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
# The per-bin screen and curve-weighted VHD (Item 57)
# ---------------------------------------------------------------------------
def _flat_library():
    from inrix_tools import volume_profiles as vp
    flat = [1 / 24] * 24
    return vp.load_profiles({"profiles": [
        {"curve_id": "flat", "hourly": {"weekday": flat, "sat": flat, "sun": flat},
         "dow": [1.0] * 7}]})


def test_bin_screen_matches_pandas(area, export_zip):
    """The cells are the gated rows inside any window, grouped by local month, day
    type and bin of the day, exactly as pandas groups the same rows."""
    con, key = area
    bins = screen.segment_bin_screen(con, key)
    assert bins.attrs["period_start"] == "2026-03-09"
    assert bins.attrs["period_end"] == "2026-03-15"
    assert bins.attrs["bin_minutes"] == 15

    raw = io.filter_cvalue(io.to_local(io.load_data(export_zip), TZ), 80)
    parts = [w.filter(raw) for w in screen.PEAK_WINDOWS.values()]
    rows = pd.concat(parts).drop_duplicates(subset=[SEGMENT_COL, "Date Time"])
    ts = rows["Date Time"]
    rows = rows.assign(
        month=ts.dt.strftime("%Y-%m"),
        day_type=ts.dt.dayofweek.map(lambda d: "sat" if d == 5 else
                                     "sun" if d == 6 else "weekday"),
        tod_min=ts.dt.hour * 60 + ts.dt.minute)
    ref = (rows.groupby([SEGMENT_COL, "month", "day_type", "tod_min"])
           ["Travel Time(Minutes)"].agg(["mean", "count"]))
    got = bins.set_index([SEGMENT_COL, "month", "day_type", "tod_min"]).sort_index()
    assert len(got) == len(ref)
    assert got["travel_time"].to_numpy() == pytest.approx(ref["mean"].to_numpy())
    assert (got["n_obs"].to_numpy() == ref["count"].to_numpy()).all()
    # the gated 07:00 hour (Mon: CValue 50, Tue: null) leaves 3 weekdays in its cells
    assert got.loc[(1000, "2026-03", "weekday", 420), "n_obs"] == 3
    assert got.loc[(1000, "2026-03", "weekday", 480), "n_obs"] == 5


def test_bin_screen_segment_subset_and_period(area):
    con, key = area
    bins = screen.segment_bin_screen(con, key, ["am"], segment_ids=[1001])
    assert set(bins[SEGMENT_COL]) == {1001}
    assert set(bins["day_type"]) == {"weekday"}
    assert screen.data_period(con, key, tz=TZ) == ("2026-03-09", "2026-03-15")
    assert screen.data_period(con, key, tz=TZ, date_start="2026-03-10",
                              date_end="2026-03-11") == ("2026-03-10", "2026-03-11")


def test_rank_corridors_curve_vhd(area):
    """With a flat curve, the curve-weighted VHD is the index × window hours / 24,
    per weekday for the weekday-gated peaks."""
    con, key = area
    scr = screen.segment_screen(con, key)
    bins = screen.segment_bin_screen(con, key)
    curves = pd.Series("flat", index=pd.Index(SEG_IDS, name=SEGMENT_COL))
    ranked = screen.rank_corridors(scr, {"Toy Rd NB": _toy_chain()}, _aadt(), bins=bins,
                                   curves=curves, profiles=_flat_library())
    r = ranked.set_index("window")
    assert r.loc["am", "vhd_index"] == pytest.approx(500.0)
    assert r.loc["am", "vhd"] == pytest.approx(500.0 * 2 / 24)
    assert r.loc["pm", "vhd_index"] == pytest.approx(1000.0)
    assert r.loc["pm", "vhd"] == pytest.approx(1000.0 * 2.5 / 24)
    assert r.loc["am", "vhd_per_mile"] == pytest.approx(r.loc["am", "vhd"] / 3.0)
    assert r.loc["am", "vhd_annual"] == pytest.approx(r.loc["am", "vhd"] * 365 * 5 / 7)
    assert r.loc["am", "vhd_coverage"] == pytest.approx(1.0)
    assert r.loc["am", "vhd_per"] == "weekday" and r.loc["night", "vhd_per"] == "day"
    assert ranked.attrs["vhd_basis"] == "curve"
    assert "per weekday for a weekday window" in ranked.attrs["aadt_caveat"]
    assert ranked.attrs["curve_vhd"]["window_days"]["am"] == 5

    # the packaged library runs end to end on the same toy
    real = screen.rank_corridors(scr, {"Toy Rd NB": _toy_chain()}, _aadt(), bins=bins,
                                 curves=curves.replace("flat", "am_commute_urban"))
    rv = real.set_index("window")["vhd"]
    assert (rv[["am", "pm", "midday"]] > 0).all()
    assert rv["night"] == 0.0                     # night runs at free flow

    # without bins nothing changes: vhd is the index
    plain = screen.rank_corridors(scr, {"Toy Rd NB": _toy_chain()}, _aadt())
    assert plain.attrs["vhd_basis"] == "index"
    assert (plain["vhd"] == plain["vhd_index"]).all()
    assert plain["vhd_annual"].isna().all()
    with pytest.raises(ValueError, match="curves"):
        screen.rank_corridors(scr, {"Toy Rd NB": _toy_chain()}, _aadt(), bins=bins)


def test_segment_curve_vhd_chunks_agree(area):
    """The store driver's chunks share one period: chunked == one pass."""
    con, key = area
    ref = pd.Series(1.0, index=pd.Index(SEG_IDS, name=SEGMENT_COL))
    curves = pd.Series("flat", index=ref.index)
    kw = dict(profiles=_flat_library(), tz=TZ)
    one = screen.segment_curve_vhd(con, key, ref, _aadt(), curves, **kw)
    chunked = screen.segment_curve_vhd(con, key, ref, _aadt(), curves, chunk_size=1, **kw)
    pd.testing.assert_frame_equal(one, chunked)
    am = one[one["window"] == "am"].set_index(SEGMENT_COL)["vhd"]
    assert am.to_numpy() == pytest.approx([10_000 / 60 * 2 / 24] * 3)


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


# ---------------------------------------------------------------------------
# rank_corridor_groups — both directions of one road  (Item 40)
# ---------------------------------------------------------------------------
def _directional_ranking():
    """A hand-built two-direction ranking, so the combining rules are checked against
    arithmetic rather than against another function's output.

    EB peaks in the AM, WB in the PM — the commute shape that makes the one-window
    total and the daily burden different numbers.
    """
    rows = []
    for corridor, direction, win, tt, ff, delay, vhd, mi in [
        ("toy-eb", "EB", "am", 20.0, 10.0, 10.0, 1000.0, 5.0),
        ("toy-eb", "EB", "pm", 11.0, 10.0, 1.0, 100.0, 5.0),
        ("toy-wb", "WB", "am", 12.0, 10.0, 2.0, 200.0, 3.0),
        ("toy-wb", "WB", "pm", 30.0, 10.0, 20.0, 2000.0, 3.0),
    ]:
        rows.append({
            "corridor": corridor, "window": win, "is_peak": True,
            "worst_peak": "am" if corridor == "toy-eb" else "pm",
            "n_segments": 4, "n_observed": 4, "miles": mi, "missing_miles": 0.0,
            "miles_covered_fraction": 1.0, "n_obs": 100, "min_kept_fraction": 0.9,
            "travel_time_min": tt, "free_flow_min": ff, "delay_min": delay,
            "tti": tt / ff, "delay_per_mile": delay / mi, "vhd": vhd,
            "vhd_per_mile": vhd / mi, "n_ramp_weighted": 1, "n_aadt_missing": 0,
        })
    out = pd.DataFrame(rows)
    out.attrs = {"delay_floor": "segment", "tz": "America/Denver"}
    return out


MEMBERSHIP = pd.DataFrame({
    "corridor": ["toy-eb", "toy-wb"],
    "corridor_group": ["toy", "toy"],
    "direction": ["EB", "WB"],
})


def test_group_sums_what_is_additive_and_recomputes_what_is_not():
    g = screen.rank_corridor_groups(_directional_ranking(), MEMBERSHIP,
                                    names={"toy": "Toy Rd"})
    pm = g[g["window"] == "pm"].iloc[0]
    assert pm["group_name"] == "Toy Rd" and pm["n_entries"] == 2
    # additive
    assert pm["vhd"] == pytest.approx(2100.0)          # 100 + 2000
    assert pm["delay_min"] == pytest.approx(21.0)
    assert pm["n_segments"] == 8 and pm["n_obs"] == 200
    # NOT additive: the two carriageways run over the same ground.
    assert pm["miles"] == pytest.approx(4.0)           # mean(5, 3), the road's length
    assert pm["directional_miles"] == pytest.approx(8.0)
    # NOT averageable: recomputed from the summed components. (With equal free-flow
    # the ratio of sums and the mean of ratios coincide, so the test that separates
    # them is the lopsided one below — this only pins the recomputation itself.)
    assert pm["tti"] == pytest.approx(41.0 / 20.0)     # (11+30)/(10+10)
    assert pm["delay_per_mile"] == pytest.approx(21.0 / 8.0)
    assert pm["vhd_per_mile"] == pytest.approx(2100.0 / 8.0)


def test_group_never_averages_a_ratio_when_the_directions_differ_in_length():
    """The failure mode the recomputation exists to stop: a short direction pulling
    as hard as a long one. With equal free-flow times the two formulas coincide, so
    here WB's free-flow is tripled — mean of the ratios 2.05, ratio of the sums
    2.525, and only one of them is the road's travel-time index."""
    r = _directional_ranking()
    r.loc[(r["corridor"] == "toy-wb") & (r["window"] == "pm"),
          ["travel_time_min", "free_flow_min"]] = [90.0, 30.0]
    g = screen.rank_corridor_groups(r, MEMBERSHIP)
    pm = g[g["window"] == "pm"].iloc[0]
    mean_of_ratios = (11.0 / 10.0 + 90.0 / 30.0) / 2          # 2.05
    ratio_of_sums = (11.0 + 90.0) / (10.0 + 30.0)             # 2.525
    assert pm["tti"] == pytest.approx(ratio_of_sums)
    assert pm["tti"] != pytest.approx(mean_of_ratios)


def test_group_names_the_peak_direction_and_keeps_the_spread():
    g = screen.rank_corridor_groups(_directional_ranking(), MEMBERSHIP)
    pm = g[g["window"] == "pm"].iloc[0]
    assert pm["peak_direction"] == "WB" and pm["peak_entry"] == "toy-wb"
    assert pm["tti_min"] == pytest.approx(1.1) and pm["tti_max"] == pytest.approx(3.0)
    assert pm["delay_min_max"] == pytest.approx(20.0)
    assert set(pm["directions"]) == {"EB", "WB"}
    am = g[g["window"] == "am"].iloc[0]
    assert am["peak_direction"] == "EB"                 # the other way round in the AM


def test_group_worst_peak_is_the_group_s_own_worst_not_either_direction_s():
    """EB's worst peak is AM and WB's is PM; the *road's* is whichever window carries
    more combined delay — PM (21.0) over AM (12.0)."""
    g = screen.rank_corridor_groups(_directional_ranking(), MEMBERSHIP)
    assert set(g["worst_peak"]) == {"pm"}


def test_group_separates_the_one_window_total_from_the_daily_burden():
    """The trap grouping sets: a grouped row sums the directions at the SAME clock
    time, and a commute corridor's directions peak at different times. 2,100 in the
    PM against 3,000 across both directional peaks."""
    g = screen.rank_corridor_groups(_directional_ranking(), MEMBERSHIP)
    pm = g[g["window"] == "pm"].iloc[0]
    assert pm["vhd"] == pytest.approx(2100.0)                    # one window
    assert pm["vhd_directional_peaks"] == pytest.approx(3000.0)  # 1000 (EB am) + 2000 (WB pm)
    assert pm["delay_min_directional_peaks"] == pytest.approx(30.0)
    # It is a group-level constant: the same on every window row, by construction.
    assert g["vhd_directional_peaks"].nunique() == 1


def test_group_accepts_the_resolve_catalogue_shape_and_entry_objects():
    from inrix_tools import corridors

    res_shape = pd.DataFrame({"id": ["toy-eb", "toy-wb"], "corridor": ["toy", "toy"],
                              "direction": ["EB", "WB"]})
    a = screen.rank_corridor_groups(_directional_ranking(), res_shape)
    entries = [
        corridors.CorridorEntry("toy-eb", "EB", (43.6, -116.3), (43.7, -116.3), "d",
                                corridor="toy", direction="EB"),
        corridors.CorridorEntry("toy-wb", "WB", (43.7, -116.3), (43.6, -116.3), "d",
                                corridor="toy", direction="WB"),
    ]
    b = screen.rank_corridor_groups(_directional_ranking(), entries)
    assert a["vhd"].tolist() == b["vhd"].tolist()
    c = screen.rank_corridor_groups(_directional_ranking(),
                                    {"toy-eb": "toy", "toy-wb": "toy"})
    assert c["vhd"].tolist() == b["vhd"].tolist()


def test_group_reports_an_entry_that_belongs_to_no_reporting_corridor():
    """A ranked corridor missing from the membership is dropped from the grouped view
    — silently losing it from the report is the failure this names."""
    r = _directional_ranking()
    extra = r[r["corridor"] == "toy-eb"].copy()
    extra["corridor"] = "orphan"
    g = screen.rank_corridor_groups(pd.concat([r, extra], ignore_index=True), MEMBERSHIP)
    assert g.attrs["ungrouped"] == ["orphan"]
    assert set(g["corridor_group"]) == {"toy"}


def test_group_refuses_an_empty_or_unmatched_membership():
    with pytest.raises(ValueError, match="No entry carries a reporting corridor"):
        screen.rank_corridor_groups(_directional_ranking(),
                                    pd.DataFrame({"corridor": ["toy-eb"],
                                                  "corridor_group": [None],
                                                  "direction": ["EB"]}))
    with pytest.raises(ValueError, match="None of the ranked corridors"):
        screen.rank_corridor_groups(_directional_ranking(),
                                    {"someone-else": "elsewhere"})


# ---------------------------------------------------------------------------
# corridor_peak_totals / corridor_breakout  (Item 41)
# ---------------------------------------------------------------------------
def test_peak_totals_sum_every_direction_and_peak():
    t = screen.corridor_peak_totals(_directional_ranking(), MEMBERSHIP,
                                    names={"toy": "Toy Rd"})
    assert len(t) == 1
    r = t.iloc[0]
    assert r["group_name"] == "Toy Rd" and tuple(r["windows"]) == ("am", "pm")
    # every cell: EB am+pm and WB am+pm
    assert r["delay_min"] == pytest.approx(10.0 + 1.0 + 2.0 + 20.0)
    assert r["vhd"] == pytest.approx(1000 + 100 + 200 + 2000)
    assert r["travel_time_min"] == pytest.approx(20 + 11 + 12 + 30)
    assert r["tti"] == pytest.approx(73.0 / 40.0)      # summed components, recomputed


def test_peak_totals_count_each_direction_s_miles_once_not_once_per_window():
    """EB is 5 mi and WB 3 mi in **both** windows. The denominator is 8, not 16 — a
    direction does not get longer because it has two peaks."""
    t = screen.corridor_peak_totals(_directional_ranking(), MEMBERSHIP).iloc[0]
    assert t["directional_miles"] == pytest.approx(8.0)
    assert t["miles"] == pytest.approx(4.0)
    assert t["miles_window_spread"] == pytest.approx(0.0)
    assert t["delay_per_mile"] == pytest.approx(33.0 / 8.0)
    assert t["vhd_per_mile"] == pytest.approx(3300.0 / 8.0)


def test_peak_totals_the_three_rankings_are_three_different_questions():
    """``vhd``, ``delay_per_mile`` and ``vhd_per_mile`` are not relabellings of each
    other, and this fixture proves it: three corridors, three **completely different**
    orders.

    - ``long``  40 mi, 40 min of delay, 4,000 veh-hrs -> 1.0 d/mi,   100 vh/mi
    - ``short``  1 mi, 10 min,            500 veh-hrs -> 10.0 d/mi,  500 vh/mi
    - ``busy``  10 mi, 20 min,          8,000 veh-hrs -> 2.0 d/mi,   800 vh/mi

    The bare total says ``busy > long > short`` (length and volume together); the
    unweighted rate says ``short > busy > long`` (how bad to drive, whoever it happens
    to); and vehicle-hours per mile says ``busy > short > long`` — volume kept, length
    reward dropped, which is why it is the default.
    """
    rows = []
    for corridor, group, mi, delay, vhd in [("long-nb", "long", 40.0, 40.0, 4000.0),
                                            ("short-nb", "short", 1.0, 10.0, 500.0),
                                            ("busy-nb", "busy", 10.0, 20.0, 8000.0)]:
        rows.append({"corridor": corridor, "window": "am", "is_peak": True,
                     "worst_peak": "am", "n_segments": 2, "n_observed": 2,
                     "miles": mi, "missing_miles": 0.0, "miles_covered_fraction": 1.0,
                     "n_obs": 10, "min_kept_fraction": 1.0,
                     "travel_time_min": 10.0 + delay, "free_flow_min": 10.0,
                     "delay_min": delay, "tti": 1.0, "delay_per_mile": delay / mi,
                     "vhd": vhd, "vhd_per_mile": vhd / mi,
                     "n_ramp_weighted": 0, "n_aadt_missing": 0})
    ranking = pd.DataFrame(rows)
    ranking.attrs = {}
    member = pd.DataFrame({"corridor": ["long-nb", "short-nb", "busy-nb"],
                           "corridor_group": ["long", "short", "busy"],
                           "direction": ["NB", "NB", "NB"]})

    default = screen.corridor_peak_totals(ranking, member)
    assert default.attrs["rank_by"] == screen.DEFAULT_RANK_METRIC == "vhd_per_mile"
    assert list(default["corridor_group"]) == ["busy", "short", "long"]
    assert default.iloc[0]["vhd_per_mile"] == pytest.approx(800.0)

    by_total = screen.corridor_peak_totals(ranking, member, rank_by="vhd")
    assert list(by_total["corridor_group"]) == ["busy", "long", "short"]
    by_rate = screen.corridor_peak_totals(ranking, member, rank_by="delay_per_mile")
    assert list(by_rate["corridor_group"]) == ["short", "busy", "long"]

    # All three orderings travel with the frame whichever one was chosen, so the
    # difference between them is visible rather than implied.
    ranks = default.set_index("corridor_group")
    assert ranks.loc["short", "rank_vhd"] == 3
    assert ranks.loc["short", "rank_delay_per_mile"] == 1
    assert ranks.loc["short", "rank_vhd_per_mile"] == 2

    with pytest.raises(ValueError, match="rank_by must be one of"):
        screen.corridor_peak_totals(ranking, member, rank_by="miles")


def test_peak_totals_say_when_the_rank_metric_is_null_throughout():
    """Without an AADT join every volume-weighted metric is NaN, and a frame of <NA>
    ranks is catalogue order wearing a ranking's clothes. The caller is told."""
    r = _directional_ranking()
    r["vhd"] = float("nan")
    r["vhd_per_mile"] = float("nan")
    t = screen.corridor_peak_totals(r, MEMBERSHIP)
    assert t.attrs["rank_metric_all_null"] is True
    assert t["rank"].isna().all()
    assert screen.corridor_peak_totals(
        r, MEMBERSHIP, rank_by="delay_per_mile").attrs["rank_metric_all_null"] is False


def test_peak_totals_flag_a_one_way_couplet_without_changing_its_arithmetic():
    plain = screen.corridor_peak_totals(_directional_ranking(), MEMBERSHIP).iloc[0]
    flagged = screen.corridor_peak_totals(_directional_ranking(), MEMBERSHIP,
                                          couplets=["toy"]).iloc[0]
    assert not plain["one_way_couplet"] and flagged["one_way_couplet"]
    for col in ("delay_min", "vhd", "miles", "directional_miles", "delay_per_mile", "tti"):
        assert flagged[col] == pytest.approx(plain[col])


def test_peak_totals_use_only_peak_windows_by_default():
    r = _directional_ranking()
    off = r[r["window"] == "am"].copy()
    off["window"], off["is_peak"], off["delay_min"], off["vhd"] = "night", False, 99.0, 9999.0
    both = pd.concat([r, off], ignore_index=True)
    assert screen.corridor_peak_totals(both, MEMBERSHIP).iloc[0]["delay_min"] \
        == pytest.approx(33.0)
    named = screen.corridor_peak_totals(both, MEMBERSHIP, windows=["night"])
    assert named.iloc[0]["delay_min"] == pytest.approx(198.0)   # the two night rows
    assert tuple(named.attrs["windows"]) == ("night",)


def test_breakout_keeps_every_direction_and_peak_as_its_own_row():
    t = screen.corridor_peak_totals(_directional_ranking(), MEMBERSHIP)
    b = screen.corridor_breakout(_directional_ranking(), MEMBERSHIP,
                                 order=t["corridor_group"].tolist())
    assert list(b.index.names) == ["corridor_group", "direction", "window"]
    assert len(b) == 4
    assert b.loc[("toy", "WB", "pm"), "delay_min"] == pytest.approx(20.0)
    assert b.loc[("toy", "EB", "am"), "vhd"] == pytest.approx(1000.0)
    # The cells add up to the total they sit under — the table can be checked by eye.
    assert b["delay_min"].sum() == pytest.approx(t.iloc[0]["delay_min"])
    assert b["vhd"].sum() == pytest.approx(t.iloc[0]["vhd"])


def test_breakout_follows_the_ranked_order_it_is_given():
    r = _directional_ranking()
    extra = r.copy()
    extra["corridor"] = extra["corridor"].str.replace("toy", "zed")
    member = pd.concat([MEMBERSHIP, pd.DataFrame({
        "corridor": ["zed-eb", "zed-wb"], "corridor_group": ["zed", "zed"],
        "direction": ["EB", "WB"]})], ignore_index=True)
    both = pd.concat([r, extra], ignore_index=True)
    b = screen.corridor_breakout(both, member, order=["zed", "toy"])
    assert list(dict.fromkeys(b.index.get_level_values(0))) == ["zed", "toy"]


def test_direction_totals_add_up_to_the_corridor_total():
    """Per-direction figures for the map tooltip: each direction summed over its
    peaks, its miles counted once, and the directions summing to the total."""
    t = screen.corridor_peak_totals(_directional_ranking(), MEMBERSHIP).iloc[0]
    b = screen.corridor_breakout(_directional_ranking(), MEMBERSHIP)
    d = screen.direction_totals(b).set_index("direction")
    assert list(d.index) == ["EB", "WB"]
    assert d.loc["EB", "delay_min"] == pytest.approx(10.0 + 1.0)
    assert d.loc["WB", "vhd"] == pytest.approx(200 + 2000)
    assert d.loc["EB", "miles"] == pytest.approx(5.0)          # not 10: once per direction
    assert d.loc["WB", "vhd_per_mile"] == pytest.approx(2200 / 3.0)
    assert d["vhd"].sum() == pytest.approx(t["vhd"])
    assert d["delay_min"].sum() == pytest.approx(t["delay_min"])
    assert d["miles"].sum() == pytest.approx(t["directional_miles"])
    # the CSV round trip (flat columns) gives the same answer
    flat = screen.direction_totals(b.reset_index())
    pd.testing.assert_frame_equal(flat, screen.direction_totals(b))


def test_baseline_screen_carries_quantiles_and_the_realtime_share(area):
    """Item 50: the weekday travel-time percentile the fallback baseline reads, and
    the ``Pct Score30`` share, per window."""
    import numpy as np

    con, key = area
    scr = screen.segment_screen(con, key, windows=screen.BASELINE_WINDOWS,
                                cvalue_threshold=None, quantiles=(0.15,))
    day = [_travel_time(step / 4.0) for step in range(96)]
    assert scr["weekday_tt_p15"].to_numpy() == pytest.approx(np.quantile(day, 0.15))
    assert scr["night_tt_p15"].eq(TT_NIGHT).all()
    # The fixture writes Pct Score30 = 100 on every row.
    assert scr["realtime_share"].eq(1.0).all()
    assert scr["pm_realtime_share"].eq(1.0).all()
    assert scr.attrs["quantiles"] == [0.15]


def test_quantiles_outside_zero_one_are_rejected(area):
    con, key = area
    with pytest.raises(ValueError):
        screen.segment_screen(con, key, quantiles=(15,))


def test_monthly_screen_splits_by_local_month(area):
    con, key = area
    mon = screen.segment_monthly_screen(con, key, cvalue_threshold=None)
    # The fixture week lies inside March 2026: one month per segment.
    assert set(mon["month"]) == {"2026-03"}
    assert len(mon) == mon["Segment ID"].nunique()
    assert mon["pm_travel_time"].eq(TT_PM).all()
    assert mon["am_travel_time"].eq(TT_AM).all()

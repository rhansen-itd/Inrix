"""Tests for count import and curve fitting (ROADMAP Item 59).

Synthetic counts only: the real ATR pull (``data/atr/``) is license-restricted and
gitignored, so every fixture here is built in the test.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from inrix_tools import counts, volume_profiles

TZ = "America/Boise"


def _library():
    return volume_profiles.load_profiles()


def _curve_counts(profile, *, start="2026-04-06", days=28, daily=10_000.0,
                  station="00279", direction="EB", route="84", source="atr",
                  scale=None):
    """Exact hourly counts that follow ``profile``: day d's total is daily × dow[d]
    (× ``scale[date]``), spread by its day type's shape."""
    rows = []
    for d in pd.date_range(start, periods=days, freq="D"):
        dt = volume_profiles.DAY_TYPES[int(volume_profiles.day_type_index([d.dayofweek])[0])]
        total = daily * profile.dow[d.dayofweek] * (scale or {}).get(d.date().isoformat(), 1.0)
        for h in range(24):
            rows.append({"station_id": station, "direction": direction,
                         "timestamp": (d + pd.Timedelta(h, "h")).tz_localize(TZ),
                         "volume": total * profile.hourly[dt][h], "lat": 43.59,
                         "lon": -116.37, "route": route, "source": source})
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------
def test_schema_round_trips_through_csv(tmp_path):
    c = _curve_counts(_library()["am_commute_urban"], days=2)
    path = counts.write_counts(c, tmp_path / "c.csv")
    back = counts.read_counts(path, TZ)
    assert list(back.columns) == list(counts.COUNT_COLUMNS)
    assert back["timestamp"].dt.tz is not None
    assert back["station_id"].iloc[0] == "00279"             # leading zeros kept
    assert back["volume"].sum() == pytest.approx(c["volume"].sum())
    assert back["timestamp"].iloc[7].hour == 7               # local hour, not UTC


@pytest.mark.parametrize("change, match", [
    (lambda c: c.drop(columns="route"), "lack columns"),
    (lambda c: c.assign(timestamp=c["timestamp"].dt.tz_localize(None)), "tz-aware"),
    (lambda c: c.assign(timestamp=c["timestamp"] + pd.Timedelta(15, "min")), "hour starts"),
    (lambda c: c.assign(direction="Northbound"), "unknown direction"),
    (lambda c: c.assign(source="loop"), "unknown source"),
    (lambda c: c.assign(volume=-1.0), ">= 0"),
    (lambda c: c.assign(lat=np.nan), "lat/lon"),
    (lambda c: pd.concat([c, c.iloc[:1]]), "repeat"),
])
def test_schema_rejects(change, match):
    c = _curve_counts(_library()["am_commute_urban"], days=1)
    with pytest.raises(ValueError, match=match):
        counts.validate_counts(change(c))


def test_direction_and_route_parsing():
    assert counts.normalise_direction("Northbound") == "NB"
    assert counts.normalise_direction(" sw ") == "SW"
    assert counts.normalise_direction("2-WAY") == counts.TWO_WAY
    with pytest.raises(ValueError):
        counts.normalise_direction("Inbound")
    assert counts.parse_route("INT 84 MAIN") == "84"
    assert counts.parse_route("INT 84 BL") == "84 BL"
    assert counts.parse_route("US 20 MAIN") == "20"
    assert counts.parse_route("N Eagle RD", "SH-55 .38 mi S of Chinden Rd.") == "55"
    assert counts.parse_route("Main St", None) == ""
    assert counts.split_route("84 BL") == ("84", True)
    assert counts.split_route("") == (None, False)


# ---------------------------------------------------------------------------
# Importers
# ---------------------------------------------------------------------------
def _tcds_frame():
    """Two days of TCDS tidy rows: the 2-way total, both directions, two lanes; and a
    second station with only its 2-way total."""
    rows = []
    for date in ("2026-04-01", "2026-04-02"):
        for h in range(24):
            eb, wb = 100 + h, 50 + h
            for sid, direction, vol in (("00279", "2-WAY", eb + wb), ("00279_EB", "EB", eb),
                                        ("00279_WB", "WB", wb),
                                        ("00279_1_EB", "1", eb - 10),
                                        ("00279_2_EB", "2", 10),
                                        ("00291", "2-WAY", 7)):
                rows.append({"series_id": sid, "direction": direction, "roadbed": "ML",
                             "district": 3, "date": date, "hour": h, "volume": vol})
    return pd.DataFrame(rows)


def _tcds_stations():
    return pd.DataFrame({"local_id": ["00279", "00291"],
                         "on": ["INT 84 MAIN", "INT 90 MAIN"],
                         "at": ["I-84 160 Ft. W of Locust Grove OP", "I-90 x"],
                         "lat": [43.59, 47.70], "lon": [-116.37, -116.80]})


def test_tcds_keeps_direction_series_and_drops_lanes():
    c = counts.import_tcds_hourly(_tcds_frame(), _tcds_stations(), TZ)
    got = c.groupby(["station_id", "direction"])["volume"].sum()
    assert got[("00279", "EB")] == sum(100 + h for h in range(24)) * 2
    assert got[("00279", "WB")] == sum(50 + h for h in range(24)) * 2
    assert ("00279", counts.TWO_WAY) not in got.index         # has directions
    assert got[("00291", counts.TWO_WAY)] == 7 * 48            # 2-way only
    assert c.attrs["tcds"]["two_way_only"] == ["00291"]
    assert set(c["route"]) == {"84", "90"}
    first = c[(c["station_id"] == "00279") & (c["direction"] == "EB")].iloc[0]
    assert first["timestamp"] == pd.Timestamp("2026-04-01 00:00", tz=TZ)
    assert first["volume"] == 100


def test_tcds_drops_the_skipped_spring_forward_hour():
    f = pd.DataFrame({"series_id": "00279_EB", "direction": "EB", "date": "2026-03-08",
                      "hour": range(24), "volume": 10})
    c = counts.import_tcds_hourly(f, _tcds_stations(), TZ)
    assert len(c) == 23 and 2 not in set(c["timestamp"].dt.hour)


def test_interval_counts_sum_to_hours_and_drop_partial_hours():
    t = pd.date_range("2026-04-07 00:00", periods=96, freq="15min")
    frame = pd.DataFrame({"time": t.strftime("%Y-%m-%d %H:%M"),
                          "NB Vol": 5, "SB Vol": 3})
    frame = frame.drop(index=[40])                       # 10:00-10:15 missing
    c = counts.import_interval_counts(frame, station_id="tube1", lat=43.6, lon=-116.2,
                                      tz=TZ, direction_cols={"NB Vol": "NB",
                                                             "SB Vol": "Southbound"},
                                      route="44")
    assert set(c["direction"]) == {"NB", "SB"}
    assert c.attrs["partial_hours_dropped"] == 2          # hour 10, both directions
    nb = c[c["direction"] == "NB"].set_index(c[c["direction"] == "NB"]["timestamp"].dt.hour)
    assert len(nb) == 23 and nb.at[9, "volume"] == 20 and 10 not in nb.index
    assert (c["source"] == "tube").all()


def test_interval_counts_with_separate_date_and_time_columns():
    frame = pd.DataFrame({"Date": ["4/7/2026"] * 4, "Time": ["07:00", "07:15", "07:30", "07:45"],
                          "EB": [1, 2, 3, 4]})
    c = counts.import_interval_counts(frame, station_id="t", lat=1.0, lon=2.0, tz=TZ,
                                      direction_cols={"EB": "EB"}, time_col="Time",
                                      date_col="Date")
    assert len(c) == 1 and c["volume"].iloc[0] == 10
    assert c["timestamp"].iloc[0] == pd.Timestamp("2026-04-07 07:00", tz=TZ)


def test_atspm_sums_detectors_and_locates_signals():
    t = pd.date_range("2026-04-07 07:00", periods=4, freq="15min")
    frame = pd.DataFrame({"SignalId": [7001] * 8 + [9999] * 4,
                          "BinStartTime": list(t) * 3,
                          "Direction": ["Northbound"] * 4 + ["Northbound"] * 4 + ["EB"] * 4,
                          "Volume": [10] * 8 + [1] * 4})         # two lanes, one unknown
    signals = pd.DataFrame({"SignalId": ["7001"], "lat": [43.65], "lon": [-116.35],
                            "route": ["55"]})
    c = counts.import_atspm_volumes(frame, signals, TZ)
    assert len(c) == 1
    row = c.iloc[0]
    assert row["station_id"] == "7001" and row["direction"] == "NB"
    assert row["volume"] == 80 and row["route"] == "55" and row["source"] == "atspm"
    assert c.attrs["signals_without_location"] == ["9999"]


# ---------------------------------------------------------------------------
# Fitting
# ---------------------------------------------------------------------------
def test_fit_recovers_a_known_curve():
    lib = _library()
    truth = lib["am_commute_urban"]
    prof = counts.fit_profile(_curve_counts(truth))
    for t in volume_profiles.DAY_TYPES:
        assert np.allclose(prof.hourly[t], truth.hourly[t], atol=1e-9)
    assert np.allclose(prof.dow, truth.dow, atol=1e-9)
    assert prof.curve_id == "fitted_00279_EB"
    assert prof.provenance["basis"] == "fitted"
    assert prof.provenance["borrowed"] == []
    assert prof.provenance["station"]["usable_days"] == {"weekday": 20, "sat": 4, "sun": 4}
    near = counts.nearest_generic(prof, lib)
    assert near.index[0] == "am_commute_urban" and near["misplaced_share"].iloc[0] == 0


def test_short_count_fills_the_weekday_and_borrows_the_rest():
    lib = _library()
    truth = lib["pm_commute_urban"]
    c = _curve_counts(truth, start="2026-04-07", days=2)          # Tue + Wed tube count
    prof = counts.fit_profile(c, library=lib)
    assert np.allclose(prof.hourly["weekday"], truth.hourly["weekday"])
    assert prof.provenance["borrowed"] == ["sat", "sun", "dow"]
    # The donor is the generic nearest the fitted weekday shape: the true one.
    assert prof.provenance["borrowed_from"] == "pm_commute_urban"
    assert np.allclose(prof.hourly["sat"], truth.hourly["sat"])
    assert np.allclose(prof.dow, truth.dow)
    # An explicit fallback wins over the nearest.
    prof2 = counts.fit_profile(c, fallback=lib["rural_through"])
    assert prof2.provenance["borrowed_from"] == "rural_through"
    assert np.allclose(prof2.hourly["sun"], lib["rural_through"].hourly["sun"])
    with pytest.raises(ValueError, match="borrowed"):
        counts.fit_profile(c)


def test_dow_needs_a_full_week():
    lib = _library()
    six = _curve_counts(lib["balanced_urban"], days=6)             # Mon..Sat
    assert "dow" in counts.fit_profile(six, library=lib).provenance["borrowed"]
    seven = _curve_counts(lib["balanced_urban"], days=7)
    prof = counts.fit_profile(seven, library=lib)
    assert prof.provenance["borrowed"] == []
    assert np.allclose(prof.dow, lib["balanced_urban"].dow)


def test_outage_and_incomplete_days_are_dropped():
    lib = _library()
    truth = lib["balanced_urban"]
    c = _curve_counts(truth, scale={"2026-04-08": 0.2})           # a detector outage
    c = c[~((c["timestamp"].dt.date == pd.Timestamp("2026-04-09").date())
            & (c["timestamp"].dt.hour == 3))]                      # a missing hour
    prof = counts.fit_profile(c, library=lib)
    dropped = prof.provenance["station"]["dropped_days"]
    assert dropped["2026-04-08"].startswith("outage")
    assert dropped["2026-04-09"].startswith("incomplete")
    assert np.allclose(prof.hourly["weekday"], truth.hourly["weekday"])


def test_fit_weights_days_by_volume():
    """The shape is Σ vol(h) / Σ total, so a busy day counts for more."""
    lib = _library()
    a = _curve_counts(lib["am_commute_urban"], start="2026-04-07", days=1, daily=30_000)
    b = _curve_counts(lib["pm_commute_urban"], start="2026-04-08", days=1, daily=15_000)
    prof = counts.fit_profile(pd.concat([a, b]), library=lib)
    am = np.asarray(lib["am_commute_urban"].hourly["weekday"])
    pm = np.asarray(lib["pm_commute_urban"].hourly["weekday"])
    ta, tb = 30_000 * lib["am_commute_urban"].dow[1], 15_000 * lib["pm_commute_urban"].dow[2]
    assert np.allclose(prof.hourly["weekday"], (ta * am + tb * pm) / (ta + tb))


def test_fit_rejects_several_stations_and_empty():
    lib = _library()
    two = pd.concat([_curve_counts(lib["am_commute_urban"], days=1),
                     _curve_counts(lib["am_commute_urban"], days=1, direction="WB")])
    with pytest.raises(ValueError, match="one station-direction"):
        counts.fit_profile(two, library=lib)
    partial = _curve_counts(lib["am_commute_urban"], days=1).iloc[:20]
    with pytest.raises(ValueError, match="no usable day"):
        counts.fit_profile(partial, library=lib)


def test_dst_changeover_day_is_not_fitted():
    lib = _library()
    c = counts.import_tcds_hourly(
        pd.DataFrame({"series_id": "00279_EB", "direction": "EB", "date": "2026-03-08",
                      "hour": range(24), "volume": 10}), _tcds_stations(), TZ)
    with pytest.raises(ValueError, match="no usable day"):
        counts.fit_profile(c, library=lib)


def test_cluster_maps_fitted_curves_onto_generic_ids():
    lib = _library()
    fitted = {"a": counts.fit_profile(_curve_counts(lib["rural_through"]), curve_id="a"),
              "b": counts.fit_profile(_curve_counts(lib["pm_commute_urban"],
                                                    direction="WB"), curve_id="b")}
    cl = counts.cluster_profiles(fitted, lib)
    assert cl.at["a", "nearest_generic"] == "rural_through"
    assert cl.at["b", "nearest_generic"] == "pm_commute_urban"
    assert (cl["misplaced_share"] < 1e-9).all()
    assert (cl["second_misplaced_share"] > 0).all()


def test_station_curves_table_and_round_trip(tmp_path):
    lib = _library()
    c = pd.concat([_curve_counts(lib["am_commute_urban"]),
                   _curve_counts(lib["pm_commute_urban"], direction="WB"),
                   _curve_counts(lib["am_commute_urban"], direction="SB").iloc[:5]])
    fitted, st = counts.station_curves(c, lib)
    assert sorted(fitted) == ["fitted_00279_EB", "fitted_00279_WB"]
    assert list(st.columns) == list(counts.STATION_COLUMNS)
    assert list(st["nearest_generic"]) == ["am_commute_urban", "pm_commute_urban"]
    assert "00279 SB" in st.attrs["unfitted"]
    back = counts.read_stations(counts.write_stations(st, tmp_path / "s.csv"))
    assert back["station_id"].iloc[0] == "00279" and back["borrowed"].iloc[0] == ""


def test_fitted_curves_are_a_library_the_vhd_weights_read(tmp_path):
    """A fitted curve written and read back weights VHD like any generic one: a whole
    week's bin factors sum to 7 average days."""
    lib = _library()
    fitted = {"fitted_00279_EB": counts.fit_profile(_curve_counts(lib["am_commute_urban"]))}
    path = volume_profiles.write_profiles(fitted, tmp_path / "f.json", note="test")
    back = volume_profiles.load_profiles(path)
    assert back["fitted_00279_EB"].provenance["station"]["station_id"] == "00279"
    merged = volume_profiles.merge_profiles(lib, back)
    assert set(merged) == set(lib) | {"fitted_00279_EB"}
    w = volume_profiles.window_volume_weights(
        {"fitted_00279_EB": merged["fitted_00279_EB"]},
        {"name": "all", "window": "12:00AM-12:00AM", "days": None},
        "2026-04-06", "2026-04-12", TZ)
    assert w["volume_days"].sum() == pytest.approx(7.0)


def test_merge_refuses_a_redefined_curve():
    lib = _library()
    other = counts.fit_profile(_curve_counts(lib["rural_through"]),
                               curve_id="am_commute_urban")
    with pytest.raises(ValueError, match="defined twice"):
        volume_profiles.merge_profiles(lib, {"am_commute_urban": other})
    assert volume_profiles.merge_profiles(lib, lib).keys() == lib.keys()

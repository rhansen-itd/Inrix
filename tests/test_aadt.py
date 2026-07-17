"""Tests for inrix_tools.aadt (ROADMAP Item 18).

Synthetic geometry + AADT fixtures cover the join and the weighting math without
the licensed layer; the real ``Cumulative_AADT.zip`` tests self-skip when it (or
the Myrtle export) isn't present.
"""
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import pytest
from shapely.geometry import LineString

from inrix_tools import aadt, geometry, io
from inrix_tools.io import DATETIME_COL, SEGMENT_COL

REPO_ROOT = Path(__file__).resolve().parents[1]
AADT_ZIP = REPO_ROOT / "Cumulative_AADT.zip"
XD_ZIP = REPO_ROOT / "USA_Idaho_shapefile.zip"
MYRTLE_ZIP = REPO_ROOT / "Myrtle_2026-02-01_to_2026-07-16_5_min_part_1.zip"
_HAVE_REAL = AADT_ZIP.exists() and XD_ZIP.exists() and MYRTLE_ZIP.exists()


# ---------------------------------------------------------------------------
# Synthetic fixtures
# ---------------------------------------------------------------------------
def _seg_geo():
    """One N–S segment near Boise, indexed by Segment ID (EPSG:4326)."""
    return gpd.GeoDataFrame(
        {"geometry": [LineString([(-116.20, 43.610), (-116.20, 43.620)])]},
        index=pd.Index([1001], name=SEGMENT_COL), crs="EPSG:4326",
    )


def _aadt_layer():
    """A parallel N–S AADT line (~8 m east, the true match) and a perpendicular
    E–W crossing line that a plain nearest-join would wrongly pick near the middle."""
    return gpd.GeoDataFrame(
        {"AADT": [40000, 2000], "Route": ["R-main", "R-cross"], "Commercial": [3000, 100],
         "geometry": [
             LineString([(-116.2001, 43.610), (-116.2001, 43.620)]),   # parallel -> match
             LineString([(-116.201, 43.6155), (-116.199, 43.6155)]),   # crossing -> reject
         ]},
        crs="EPSG:4326",
    )


# ---------------------------------------------------------------------------
# Spatial join
# ---------------------------------------------------------------------------
def test_join_matches_parallel_rejects_crossing():
    """The bearing check picks the same-orientation parallel line, not the closer
    (by midpoint) perpendicular cross-street."""
    j = aadt.join_aadt(_seg_geo(), _aadt_layer())
    assert j.loc[1001, "AADT"] == 40000
    assert j.loc[1001, "aadt_source"] == "matched"
    assert j.loc[1001, "Route"] == "R-main"
    assert j.loc[1001, "aadt_dist_m"] < 20  # ~8 m


def test_join_far_line_flagged_nearest_not_matched():
    """A segment whose only AADT line is beyond ``max_distance_m`` still gets a
    value, flagged ``nearest`` (visible, not silently matched or dropped)."""
    far = gpd.GeoDataFrame(
        {"AADT": [5000], "Route": ["R"], "Commercial": [0],
         "geometry": [LineString([(-116.19, 43.610), (-116.19, 43.620)])]},  # ~800 m east
        crs="EPSG:4326",
    )
    j = aadt.join_aadt(_seg_geo(), far, max_distance_m=35.0)
    assert j.loc[1001, "aadt_source"] == "nearest"
    assert j.loc[1001, "AADT"] == 5000
    assert j.loc[1001, "aadt_dist_m"] > 100


def test_join_missing_geometry_and_empty_layer():
    """A segment with no geometry -> missing; an empty AADT layer -> all missing."""
    seg = gpd.GeoDataFrame(
        {"geometry": [None]}, index=pd.Index([9], name=SEGMENT_COL), crs="EPSG:4326")
    j = aadt.join_aadt(seg, _aadt_layer())
    assert j.loc[9, "aadt_source"] == "missing"
    assert pd.isna(j.loc[9, "AADT"])

    empty = gpd.GeoDataFrame({"AADT": [], "geometry": []}, crs="EPSG:4326")
    j2 = aadt.join_aadt(_seg_geo(), empty)
    assert (j2["aadt_source"] == "missing").all()


def test_line_bearing_and_diff():
    ns = LineString([(0, 0), (0, 1)])   # north -> 90°
    ew = LineString([(0, 0), (1, 0)])   # east  -> 0°
    assert abs(aadt._line_bearing(ns) - 90) < 1e-9
    assert abs(aadt._line_bearing(ew) - 0) < 1e-9
    assert abs(aadt._bearing_diff(90, 0) - 90) < 1e-9   # perpendicular
    assert aadt._bearing_diff(90, 270 % 180) == 0        # opposing dir shares orientation
    assert aadt._line_bearing(LineString([(0, 0), (0, 0)])) is None  # degenerate


# ---------------------------------------------------------------------------
# Weighting math
# ---------------------------------------------------------------------------
def test_aadt_weighted_mean_speed():
    speed = pd.Series({1: 60.0, 2: 30.0})
    vol = pd.Series({1: 1000.0, 2: 9000.0})
    got = aadt.aadt_weighted_mean_speed(speed, vol)
    assert got == pytest.approx((60 * 1000 + 30 * 9000) / 10000)   # 33, not the plain 45
    # a frame carrying an AADT column is accepted too.
    frame = pd.DataFrame({"AADT": [1000.0, 9000.0]}, index=pd.Index([1, 2], name=SEGMENT_COL))
    assert aadt.aadt_weighted_mean_speed(speed, frame) == pytest.approx(got)


def test_weighted_mean_speed_drops_zero_and_missing_weight():
    speed = pd.Series({1: 60.0, 2: 30.0, 3: 10.0})
    vol = pd.Series({1: 1000.0, 2: 0.0, 3: np.nan})   # seg2 zero, seg3 missing -> dropped
    assert aadt.aadt_weighted_mean_speed(speed, vol) == pytest.approx(60.0)
    assert np.isnan(aadt.aadt_weighted_mean_speed(speed, pd.Series({1: 0.0, 2: 0.0, 3: 0.0})))


def test_vehicle_hours_of_delay():
    delay = pd.Series({1: 2.0, 2: 10.0})          # minutes
    vol = pd.Series({1: 1000.0, 2: 9000.0})
    out = aadt.vehicle_hours_of_delay(delay, vol)
    assert out.loc[1, "vehicle_hours"] == pytest.approx(2 / 60 * 1000)
    assert out.loc[2, "vehicle_hours"] == pytest.approx(10 / 60 * 9000)
    assert out["vehicle_hours"].sum() == pytest.approx(2 / 60 * 1000 + 10 / 60 * 9000)
    assert "aadt_caveat" in out.attrs   # daily-total caveat recorded, not silently scaled


def test_vehicle_hours_zero_aadt_kept_as_zero():
    """A missing/zero-volume segment stays a row with 0 vehicle-hours (not dropped,
    not NaN) so a corridor total is well-defined."""
    delay = pd.Series({1: 5.0, 2: 5.0})
    vol = pd.Series({1: np.nan, 2: 4000.0})
    out = aadt.vehicle_hours_of_delay(delay, vol)
    assert out.loc[1, "vehicle_hours"] == 0.0
    assert out.loc[2, "vehicle_hours"] == pytest.approx(5 / 60 * 4000)


def test_weighted_speed_by_time():
    """Per-timestamp Σ(w·v)/Σw across segments; tolerates a segment missing at a
    timestamp (weights re-normalize)."""
    ts = pd.date_range("2026-03-02 00:00", periods=2, freq="5min", tz="America/Denver")
    df = pd.DataFrame({
        DATETIME_COL: [ts[0], ts[0], ts[1]],          # ts[1] has only segment 2
        SEGMENT_COL: [1, 2, 2],
        "Speed(miles/hour)": [60.0, 20.0, 20.0],
    })
    vol = pd.Series({1: 1000.0, 2: 3000.0})
    out = aadt.weighted_speed_by_time(df, vol, speed_col="Speed(miles/hour)")
    wcol = "Weighted Speed(miles/hour)"
    assert wcol in out.columns and len(out) == 2
    assert out.iloc[0][wcol] == pytest.approx((60 * 1000 + 20 * 3000) / 4000)   # 30
    assert out.iloc[1][wcol] == pytest.approx(20.0)                              # only seg2


# ---------------------------------------------------------------------------
# Real AADT layer (skipped without the licensed fixtures)
# ---------------------------------------------------------------------------
@pytest.mark.skipif(not _HAVE_REAL, reason="AADT layer / XD shapefile / export absent")
def test_load_aadt_year_filter_and_reprojection():
    # A Myrtle-area bbox keeps the read small; only 2024 rows survive the filter.
    bbox = (-116.24, 43.59, -116.17, 43.63)
    layer = aadt.load_aadt(AADT_ZIP, year=2024, bbox=bbox)
    assert len(layer) > 0
    assert (layer["Year"] == 2024).all()
    assert layer.crs.to_epsg() == 4326                 # reprojected from EPSG:8826
    assert "AADT" in layer.columns and layer["AADT"].notna().any()


@pytest.mark.skipif(not _HAVE_REAL, reason="AADT layer / XD shapefile / export absent")
def test_real_myrtle_bbox_join():
    ids = list(io.load_metadata(MYRTLE_ZIP).index)
    net = geometry.load_xd_network(XD_ZIP, segment_ids=ids)
    geo = geometry.segment_geometry(net, segment_ids=ids)
    b = geo.total_bounds
    pad = 0.01
    layer = aadt.load_aadt(AADT_ZIP, year=2024,
                           bbox=(b[0] - pad, b[1] - pad, b[2] + pad, b[3] + pad))
    j = aadt.join_aadt(geo, layer)
    # The AADT centerlines coincide with the XD segments, so the vast majority join
    # cleanly; every segment gets *some* flagged value (matched or nearest).
    assert (j["aadt_source"] == "matched").mean() > 0.8
    assert j["AADT"].notna().all()
    assert (j.loc[j["aadt_source"] == "matched", "aadt_dist_m"] < 35).all()

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


# --- divided-highway fixtures (Item 34) -------------------------------------
# A carriageway of a divided highway, the mainline centreline ~25 m off it, and the
# parallel on/off ramp 5 m off it — the geometry that made nearest-wins pick the ramp.
def _carriageway(road_number="84", road_name="I-84 W", frc=1):
    return gpd.GeoDataFrame(
        {"geometry": [LineString([(-116.20, 43.610), (-116.20, 43.620)])],
         "FRC": [frc], "RoadNumber": [road_number], "RoadName": [road_name],
         "RoadList": [f"I-{road_number}"]},
        index=pd.Index([1001], name=SEGMENT_COL), crs="EPSG:4326",
    )


def _divided_highway_layer():
    """The ITD layer's shape on a divided highway: **one** mainline centreline (far,
    high volume) plus a **ramp record per movement** (near, low volume), each ramp on
    its own ``RouteID`` band."""
    return gpd.GeoDataFrame(
        {"AADT": [147500.0, 18000.0, 16500.0],
         "RouteID": ["01010AIN084", "08627AIN084", "08628AIN084"],
         "Descriptio": ["EAGLE RD IC #46", "WB OFF EAGLE RD IC #46",
                        "WB ON EAGLE RD IC #46"],
         "Commercial": [12000, 900, 800],
         "geometry": [
             # ~25 m west: the single centreline between the two carriageways.
             LineString([(-116.20031, 43.610), (-116.20031, 43.620)]),
             # ~5 m east: the off-ramp, parallel for the segment's whole length.
             LineString([(-116.199938, 43.610), (-116.199938, 43.620)]),
             # ~9 m east: the on-ramp.
             LineString([(-116.19989, 43.610), (-116.19989, 43.620)]),
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


def test_join_far_line_flagged_nearest_no_value():
    """A segment whose only AADT line is beyond ``max_distance_m`` is flagged
    ``nearest`` with the line identified (Route + distance, for diagnosis) but
    **no volume attached** — an untrusted line's AADT must not flow into the
    weighted metrics."""
    far = gpd.GeoDataFrame(
        {"AADT": [5000], "Route": ["R"], "Commercial": [0],
         "geometry": [LineString([(-116.19, 43.610), (-116.19, 43.620)])]},  # ~800 m east
        crs="EPSG:4326",
    )
    j = aadt.join_aadt(_seg_geo(), far, max_distance_m=35.0)
    assert j.loc[1001, "aadt_source"] == "nearest"
    assert pd.isna(j.loc[1001, "AADT"])
    assert j.loc[1001, "Route"] == "R"
    assert j.loc[1001, "aadt_dist_m"] > 100


def test_join_crossing_only_never_leaks_its_volume():
    """When the only line in reach is a perpendicular cross-street (distance ~0 —
    it crosses the segment), the bearing gate rejects it and the fallback must NOT
    attach its volume: the vehicle-hours / weighted-speed math would otherwise
    silently use the cross-street's AADT."""
    seg = _seg_geo()
    cross = gpd.GeoDataFrame(
        {"AADT": [99999.0], "Route": ["R-cross"], "Commercial": [100],
         "geometry": [LineString([(-116.201, 43.615), (-116.199, 43.615)])]},
        crs="EPSG:4326",
    )
    j = aadt.join_aadt(seg, cross)
    assert j.loc[1001, "aadt_source"] == "nearest"
    assert pd.isna(j.loc[1001, "AADT"])
    # and the weighting consumes that as missing, not as 99999:
    vh = aadt.vehicle_hours_of_delay(pd.Series({1001: 6.0}), j["AADT"])
    assert vh.loc[1001, "vehicle_hours"] == 0.0


def test_join_curved_same_road_matches_by_local_tangent():
    """A curved (L-shaped) AADT feature lying ON the segment matches: the local
    tangent at the closest approach is what's compared, not the feature's
    endpoint-to-endpoint chord (which is >45° off here)."""
    seg = _seg_geo()  # N–S along -116.20
    lshape = gpd.GeoDataFrame(
        {"AADT": [12000.0], "Route": ["R-same"], "Commercial": [500],
         "geometry": [LineString([
             (-116.20, 43.610), (-116.20, 43.620),   # runs along the segment...
             (-116.19, 43.620),                       # ...then turns east (long leg)
         ])]},
        crs="EPSG:4326",
    )
    j = aadt.join_aadt(seg, lshape)
    assert j.loc[1001, "aadt_source"] == "matched"
    assert j.loc[1001, "AADT"] == 12000.0


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


# ---------------------------------------------------------------------------
# Record identity + classification  (Item 34)
# ---------------------------------------------------------------------------
def test_parse_route_id_and_label():
    """``RouteID`` is the layer's real route identifier — route-segment number,
    suffix, class, route number. ``OH`` ("other highway") carries no number."""
    assert aadt.parse_route_id("01010AIN084") == ("01010", "IN", 84)
    assert aadt.parse_route_id("02150ASH069") == ("02150", "SH", 69)
    assert aadt.parse_route_id("01540DUS095") == ("01540", "US", 95)
    assert aadt.parse_route_id("00163AOH000") == ("00163", "OH", None)
    assert aadt.parse_route_id(None) == (None, None, None)
    assert aadt.parse_route_id("nonsense") == (None, None, None)

    assert aadt.route_label("01010AIN084") == "I-84"
    assert aadt.route_label("02150ASH069") == "SH-69"
    assert aadt.route_label("01540DUS095") == "US-95"
    assert aadt.route_label("00163AOH000") is None      # unnumbered


def test_classify_records_reads_description_and_route_band():
    """A ramp feature is described by its movement, a mainline one by the streets it
    runs between — but a carriageway *between* two ramp gores is also written
    ``"EB ON ..."``. The ``RouteID`` roll-up resolves that: a route whose records are
    mostly mainline is a mainline route, so its odd ramp-worded row is mainline too."""
    layer = pd.DataFrame({
        "RouteID": ["01010AIN084"] * 4 + ["08627AIN084", "00173DUS012"],
        "Descriptio": [
            "E NAMPA IC #38", "TEN MILE RD IC #42", "EAGLE RD IC #46",
            "EB ON COLE-OVERLAND IC",          # mainline between two gores
            "WB OFF EAGLE RD IC #46",          # a real ramp, own RouteID
            "CONN TO MAIN ST",                 # a real connector, own RouteID
        ],
        "AADT": [122000, 135000, 147500, 97000, 18000, 10500],
    })
    out = aadt.classify_aadt_records(layer)
    kinds = list(out[aadt.RECORD_KIND_COL])
    assert kinds[:4] == ["mainline"] * 4       # incl. the ramp-worded mainline row
    assert kinds[4] == "ramp"
    assert kinds[5] == "connector"
    # and the route fields come along: Route is populated from RouteID.
    assert list(out["Route"])[:1] == ["I-84"]
    assert out[aadt.ROUTE_NUMBER_COL].iloc[0] == 84


def test_classify_keeps_per_record_kind_without_a_mainline_majority():
    """A short route pairing one street with one genuine ramp (US-95's Weiser spur)
    has no mainline majority, so the roll-up must not promote the ramp."""
    layer = pd.DataFrame({
        "RouteID": ["01543DUS095", "01543DUS095"],
        "Descriptio": ["US-95 SPUR WB OFF RAMP IC #1 WEISER", "IDAHO ST"],
        "AADT": [150, 1800],
    })
    kinds = list(aadt.classify_aadt_records(layer)[aadt.RECORD_KIND_COL])
    assert kinds == ["ramp", "mainline"]


def test_classify_plural_ramps_is_a_location_not_a_ramp():
    """``"I-15 NB RAMPS IC #108"`` names the interchange a *mainline* record runs to;
    only the singular is the feature itself."""
    assert aadt._describe_kind("I-15 NB RAMPS IC #108") == "mainline"
    assert aadt._describe_kind("WB OFF RAMP IC#49") == "ramp"
    assert aadt._describe_kind("NONE") == "unknown"
    assert aadt._describe_kind(None) == "unknown"


# ---------------------------------------------------------------------------
# The divided-highway join  (Item 34)
# ---------------------------------------------------------------------------
def test_mainline_beats_a_nearer_parallel_ramp():
    """The failure this item fixes: on a divided highway the ramp is 5 m from the
    carriageway and the mainline centreline is 25 m off, so nearest-wins takes the
    ramp's 18,000 for a road carrying 147,500."""
    j = aadt.join_aadt(_carriageway(), _divided_highway_layer())
    assert j.loc[1001, "AADT"] == 147500.0
    assert j.loc[1001, "aadt_source"] == "matched"
    assert j.loc[1001, "aadt_record_kind"] == "mainline"
    assert j.loc[1001, "aadt_desc"] == "EAGLE RD IC #46"
    assert j.loc[1001, "Route"] == "I-84"
    assert j.loc[1001, "aadt_dist_m"] > 20       # the far line won on merit

    # prefer_mainline=False is the old behaviour, kept as an escape hatch.
    off = aadt.join_aadt(_carriageway(), _divided_highway_layer(), prefer_mainline=False)
    assert off.loc[1001, "AADT"] == 18000.0
    assert off.loc[1001, "aadt_source"] == "matched_ramp"


def test_a_ramp_segment_still_matches_its_ramp_record():
    """The preference is not a filter: a segment that genuinely *is* a ramp needs the
    ramp's volume, and gets it — flagged ``matched_ramp`` so it is never mistaken for
    a mainline count downstream."""
    ramp_seg = _carriageway(road_number=None, road_name="Ramp to I-84 W", frc=6)
    j = aadt.join_aadt(ramp_seg, _divided_highway_layer())
    assert j.loc[1001, "AADT"] == 18000.0
    assert j.loc[1001, "aadt_source"] == "matched_ramp"
    assert j.loc[1001, "aadt_record_kind"] == "ramp"


def test_route_number_beats_a_nearer_frontage_road():
    """A frontage road's own record sits metres from the interstate and carries 70
    vehicles/day. The route number is what separates them — and the mismatch is only
    a *missing bonus*, never a penalty, so an unnumbered record can still win on
    distance when nothing names a route."""
    layer = _divided_highway_layer().iloc[[0]].copy()          # I-84 mainline, 25 m
    frontage = gpd.GeoDataFrame(
        {"AADT": [70.0], "RouteID": ["06352AOH000"], "Descriptio": ["SUBSTATION RD"],
         "Commercial": [0],
         "geometry": [LineString([(-116.19996, 43.610), (-116.19996, 43.620)])]},
        crs="EPSG:4326",
    )
    layer = gpd.GeoDataFrame(pd.concat([layer, frontage], ignore_index=True),
                             crs="EPSG:4326")
    j = aadt.join_aadt(_carriageway(), layer)
    assert j.loc[1001, "AADT"] == 147500.0      # not the 3 m, 70-vehicle frontage road

    # An unnumbered segment has no route evidence: distance decides, as before.
    j2 = aadt.join_aadt(_carriageway(road_number=None, road_name="Substation Rd", frc=4),
                        layer)
    assert j2.loc[1001, "AADT"] == 70.0


def test_coverage_breaks_a_zero_distance_tie():
    """Several records lie *on* a segment at 0.0 m — the mainline record and, say, a
    rest-area record clipping one end. Distance can't separate them; how much of the
    segment each runs beside can."""
    seg = _carriageway()
    layer = gpd.GeoDataFrame(
        {"AADT": [26500.0, 560.0],
         "RouteID": ["01010AIN084", "00187AIN084"],
         "Descriptio": ["OREGON STATE LINE", "SNAKE RIVER VIEW EB RA"],
         "Commercial": [4000, 10],
         "geometry": [
             LineString([(-116.20, 43.610), (-116.20, 43.620)]),    # whole segment
             LineString([(-116.20, 43.610), (-116.20, 43.6115)]),   # first 15% only
         ]},
        crs="EPSG:4326",
    )
    j = aadt.join_aadt(seg, layer)
    assert j.loc[1001, "aadt_dist_m"] == pytest.approx(0.0, abs=0.5)
    assert j.loc[1001, "AADT"] == 26500.0
    assert j.loc[1001, "aadt_desc"] == "OREGON STATE LINE"


def test_join_records_the_resolved_policy():
    """``attrs['aadt_join']`` carries what was actually applied (module convention)."""
    j = aadt.join_aadt(_carriageway(), _divided_highway_layer(), max_distance_m=60.0)
    pol = j.attrs["aadt_join"]
    assert pol["max_distance_m"] == 60.0 and pol["prefer_mainline"] is True
    assert pol["counts"] == {"matched": 1}
    assert "mainline" in pol["preference"]


def test_ramp_weighted_rows_are_flagged_not_dropped():
    """The decision recorded in the docstring: a ``matched_ramp`` row keeps its
    vehicle-hours (right for a ramp segment, a *finding* for anything else) and is
    carried through as a column the caller can filter on."""
    joined = aadt.join_aadt(
        _carriageway(road_number=None, road_name="Ramp to I-84 W", frc=6),
        _divided_highway_layer())
    vh = aadt.vehicle_hours_of_delay(pd.Series({1001: 6.0}), joined)
    assert vh.loc[1001, "vehicle_hours"] == pytest.approx(6 / 60 * 18000)   # not zeroed
    assert vh.loc[1001, "aadt_source"] == "matched_ramp"
    assert vh.attrs["aadt_ramp_rows"] == 1


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
    timestamp (weights re-normalize), with per-timestamp ``coverage`` reported."""
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
    assert out.iloc[0]["coverage"] == pytest.approx(1.0)
    assert out.iloc[1]["coverage"] == pytest.approx(0.75)   # ≥ the 0.5 default -> kept
    assert out.attrs["weighted_speed"]["n_dropped_low_coverage"] == 0


def test_weighted_speed_drops_low_coverage_timestamps():
    """A timestamp where the dominant-volume segment is missing must not read as a
    speed change: with only ~2% of member volume reporting, the re-normalized mean
    describes a different population, so the default coverage gate drops it
    (``min_coverage=0`` keeps it for inspection, with the coverage visible)."""
    ts = pd.date_range("2026-03-02 00:00", periods=2, freq="5min", tz="America/Denver")
    df = pd.DataFrame({
        DATETIME_COL: [ts[0], ts[0], ts[1]],          # ts[1] misses the 50k mainline
        SEGMENT_COL: [1, 2, 2],
        "Speed(miles/hour)": [60.0, 20.0, 20.0],      # no speed changed anywhere
    })
    vol = pd.Series({1: 50000.0, 2: 1000.0})
    wcol = "Weighted Speed(miles/hour)"
    out = aadt.weighted_speed_by_time(df, vol, speed_col="Speed(miles/hour)")
    assert len(out) == 1                               # the artifact timestamp is gone
    assert out.iloc[0][wcol] == pytest.approx((60 * 50000 + 20 * 1000) / 51000)
    assert out.attrs["weighted_speed"]["n_dropped_low_coverage"] == 1

    kept = aadt.weighted_speed_by_time(df, vol, speed_col="Speed(miles/hour)",
                                       min_coverage=0.0)
    assert len(kept) == 2
    assert kept.iloc[1]["coverage"] == pytest.approx(1000 / 51000)


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


# The two carriageways of I-84 between Nampa (Exit 36) and Boise (Exit 49) — the
# ROADMAP Item 34 worked example. Eastbound's XD polylines sit *on* the ITD mainline
# centreline, westbound's sit ~25 m off it with the ramps in between, so nearest-wins
# read the same freeway as 61,410 one way and 114,981 the other.
_I84_WB = [1187585112, 1187585180, 1187585156, 1187332184, 428958414, 1187495750,
           1187556846, 1187323445, 1187323506, 1187323484, 1187323568, 1187323545,
           1187371708, 1187578622, 1187659502, 1187474236, 1187326296, 1187326380,
           1187326357, 1187326419, 1187326399, 1187326461, 119748633, 1187326499,
           1187326479, 1187588086, 1187558568, 1187558548]
_I84_EB = [1187595912, 1187595976, 1187466758, 1187595956, 1187596020, 1187595990,
           1187596054, 1187596034, 1187596097, 1187596079, 119269733, 1187596122,
           1187374729, 1187579020, 1187579000, 1187596179, 1187396006, 1187398028,
           1187398090, 1187398068, 1187398130, 1187398108, 1187558016, 1187556904,
           1187501009, 1187398571, 1187596245, 1187383044, 1187383104, 1187596225]


@pytest.mark.skipif(not (AADT_ZIP.exists() and XD_ZIP.exists()),
                    reason="AADT layer / XD shapefile absent")
def test_real_i84_carriageways_agree_after_the_ranked_join():
    """Item 34's verification, on the real layer: a ramp-aware rejoin of the I-84
    westbound carriageway recovers the monotone mainline profile (114,500 -> 122,000
    -> 135,000 -> 145,500 -> 147,500) and a length-weighted mean AADT of ~124k, up
    from the 61,410 nearest-wins produced — bringing it within a few percent of the
    eastbound side over the same ground."""
    ids = _I84_WB + _I84_EB
    net = geometry.load_xd_network(XD_ZIP, segment_ids=ids)
    geo = geometry.segment_geometry(net, segment_ids=ids)
    b = geo.total_bounds
    pad = 0.02
    layer = aadt.load_aadt(AADT_ZIP, year=2024,
                           bbox=(b[0] - pad, b[1] - pad, b[2] + pad, b[3] + pad))
    j = aadt.join_aadt(geo, layer)
    miles = net.set_index("XDSegID")["Miles"]

    def lw_mean(sids):
        sub = j.loc[sids]
        assert (sub["aadt_source"] == "matched").all()          # no ramp attributions
        w = miles.reindex(sids).astype(float)
        return float((sub["AADT"] * w).sum() / w.sum())

    wb, eb = lw_mean(_I84_WB), lw_mean(_I84_EB)
    assert wb > 110_000                      # was 61,410 under nearest-wins
    assert abs(wb - eb) / eb < 0.05          # the two directions now agree
    # and the westbound profile is the mainline's, monotone west to east:
    wb_vals = j.loc[_I84_WB[::-1], "AADT"].tolist()        # west -> east
    assert sorted(set(wb_vals)) == [76000.0, 97000.0, 114500.0, 122000.0,
                                    135000.0, 145500.0, 147500.0]
    peak = len(wb_vals) - 1 - wb_vals[::-1].index(max(wb_vals))   # the Eagle Rd crest
    assert wb_vals[:peak + 1] == sorted(wb_vals[:peak + 1])
    assert set(wb_vals[peak + 1:]) == {76000.0, 97000.0}   # the drop past the I-184 wye


@pytest.mark.skipif(not AADT_ZIP.exists(), reason="AADT layer absent")
def test_real_layer_route_and_kind_fields():
    """``Route`` is **null on every Idaho row** of the shipped layer — Item 34
    repopulates it from ``RouteID`` — and the ramp records around an interchange come
    out labelled as ramps while the mainline record does not."""
    raw = aadt.load_aadt(AADT_ZIP, year=2024, bbox=(-116.36, 43.58, -116.32, 43.61),
                         classify=False, columns=["Year", "RouteID", "Route", "AADT",
                                                  "Descriptio"])
    assert raw["Route"].notna().any()              # derived, not read
    layer = aadt.classify_aadt_records(raw)
    i84 = layer[layer["Route"] == "I-84"]
    by_desc = dict(zip(i84["Descriptio"], i84[aadt.RECORD_KIND_COL]))
    assert by_desc["EAGLE RD IC #46"] == "mainline"
    assert by_desc["WB OFF EAGLE RD IC #46"] == "ramp"
    assert layer.loc[layer["Descriptio"] == "EAGLE RD IC #46", "AADT"].iloc[0] == 147500


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
    # cleanly; a volume is attached exactly for the matched rows (a gate-rejected
    # nearest line is identified but carries no value).
    assert (j["aadt_source"] == "matched").mean() > 0.8
    assert j.loc[j["aadt_source"] == "matched", "AADT"].notna().all()
    assert j.loc[j["aadt_source"] != "matched", "AADT"].isna().all()
    assert (j.loc[j["aadt_source"] == "matched", "aadt_dist_m"] < 35).all()

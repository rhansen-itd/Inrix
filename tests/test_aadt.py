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
# Route class: a record's own route, and the tie-break  (Item 42)
# ---------------------------------------------------------------------------
def test_record_route_number_reads_the_band_then_the_description():
    """A route the record names *for itself*: the ``RouteID`` band first, then the
    two description forms ITD uses — a trailing parenthetical and a description that
    is nothing but a route."""
    assert aadt.record_route_number("02150ASH069", "MERIDIAN RD") == 69
    # 330 D3 rows carry state highway on an OH band and say so only here:
    assert aadt.record_route_number("04523AOH000", "KARCHER RD (SH-55)") == 55
    assert aadt.record_route_number("07880AOH000", "E 7TH ST (US-95)") == 95
    assert aadt.record_route_number("00378AOH000", "SH-52") == 52
    assert aadt.record_route_number("00163AOH000", "MILL CREEK RD") is None
    assert aadt.record_route_number("00163AOH000", None) is None


def test_record_route_number_ignores_a_route_named_mid_description():
    """A route named anywhere but those two places is a cross-street, a junction or a
    *business* route — reading it would hand a city street the interstate's identity.
    ``FRANKLIN RD US-20 IC#29`` is I-84's own mainline record at the US-20
    interchange, and ``CALDWELL BLVD(I-84 BUS)`` is I-84 Business, not I-84."""
    assert aadt.record_route_number("00163AOH000", "FRANKLIN RD US-20 IC#29") is None
    assert aadt.record_route_number("00163AOH000", "IDAHO AVE @ US-95 CONN") is None
    assert aadt.record_route_number("00163AOH000", "CALDWELL BLVD(I-84 BUS)") is None
    assert aadt.record_route_number("00163AOH000", "CLEVELAND BLVD (I-84 B)") is None


def _two_records_on_the_segment(**kwargs):
    """Two records lying *on* the same segment, equal in every ranked term — same
    kind, same 0.0 m distance, same full coverage. One names a route, one does not."""
    return gpd.GeoDataFrame(
        {"AADT": [4700.0, 1400.0],
         "RouteID": ["04523AOH000", "04680AOH000"],
         "Descriptio": ["KARCHER RD (SH-55)", "LAKE LOWELL AVE"],
         "Commercial": [300, 50],
         "geometry": [LineString([(-116.20, 43.610), (-116.20, 43.620)]),
                      LineString([(-116.20, 43.610), (-116.20, 43.620)])]},
        crs="EPSG:4326", **kwargs)


def test_a_named_route_breaks_a_tie_an_unnumbered_record_cannot():
    """The last decision in the key: with route, facility, distance and coverage all
    equal, the record that names a route wins."""
    seg = _carriageway(road_number=None, road_name="W Karcher Rd", frc=3)
    j = aadt.join_aadt(seg, _two_records_on_the_segment())
    assert j.loc[1001, "AADT"] == 4700.0
    assert j.loc[1001, aadt.AADT_ROUTE_NUM_COL] == 55
    assert "named route" in j.attrs["aadt_join"]["preference"]


def test_the_match_does_not_depend_on_the_layers_row_order():
    """What the tie-break actually fixes. The final comparison used to be the
    record's **position in the layer**, so two records on the same ground were
    separated by load order and the answer changed with the candidate set — measured
    on D3, shuffling the AADT layer moved 9 of the 3,905 export segments."""
    layer = _two_records_on_the_segment()
    seg = _carriageway(road_number=None, road_name="W Karcher Rd", frc=3)
    forwards = aadt.join_aadt(seg, layer)
    backwards = aadt.join_aadt(seg, layer.iloc[::-1].reset_index(drop=True))
    assert forwards.loc[1001, "AADT"] == backwards.loc[1001, "AADT"]
    assert forwards.loc[1001, "RouteID"] == backwards.loc[1001, "RouteID"]


def test_the_route_class_preference_never_promotes_a_worse_match():
    """It is a tie-break and nothing more. Ranking a numbered record ahead of
    distance or coverage was measured on D3 and is wrong — a city street at an
    interchange sits metres from the interstate's own record, and W Emerald St would
    take ``COLE RD IC #1B``'s 82,000 over N Cole Rd's 12,500."""
    layer = gpd.GeoDataFrame(
        {"AADT": [82000.0, 12500.0],
         "RouteID": ["01010AIN084", "06352AOH000"],
         "Descriptio": ["COLE RD IC #1B", "N COLE RD"],
         "Commercial": [9000, 700],
         "geometry": [
             # the interstate record, 9 m off and running past the whole segment
             LineString([(-116.19989, 43.610), (-116.19989, 43.620)]),
             # the street's own record, on it
             LineString([(-116.20, 43.610), (-116.20, 43.620)])]},
        crs="EPSG:4326")
    seg = _carriageway(road_number=None, road_name="W Emerald St", frc=4)
    assert aadt.join_aadt(seg, layer).loc[1001, "AADT"] == 12500.0      # not 82,000

    # and coverage still outranks it: the numbered record clipping one end loses to
    # the unnumbered one that runs the length of the segment.
    clipped = layer.copy()
    clipped.loc[0, "geometry"] = LineString([(-116.20, 43.610), (-116.20, 43.6115)])
    assert aadt.join_aadt(seg, clipped).loc[1001, "AADT"] == 12500.0


def test_join_reports_coverage_and_the_records_route():
    j = aadt.join_aadt(_carriageway(), _divided_highway_layer())
    assert j.loc[1001, aadt.AADT_COVER_COL] == pytest.approx(1.0)
    assert j.loc[1001, aadt.AADT_ROUTE_NUM_COL] == 84
    # the diagnostic row (no gate-passing match) reports them too, with no volume
    far = aadt.join_aadt(_seg_geo(), _aadt_layer(), max_distance_m=1.0)
    assert far.loc[1001, "aadt_source"] == "nearest"
    assert pd.isna(far.loc[1001, "AADT"])
    assert 0.0 <= far.loc[1001, aadt.AADT_COVER_COL] <= 1.0


# ---------------------------------------------------------------------------
# On-system classification  (Item 42)
# ---------------------------------------------------------------------------
def _on_system_geo(road_name, road_number=None, frc=4):
    return _carriageway(road_number=road_number, road_name=road_name, frc=frc)


def _sh55_record(offset=0.0, end_lat=43.620, desc="KARCHER RD (SH-55)"):
    """SH-55's record, carried on an ``OH`` band as ITD actually writes it."""
    x = -116.20 + offset
    return gpd.GeoDataFrame(
        {"AADT": [4700.0], "RouteID": ["04523AOH000"], "Descriptio": [desc],
         "Commercial": [300],
         "geometry": [LineString([(x, 43.610), (x, end_lat)])]},
        crs="EPSG:4326")


def test_on_system_accepts_the_route_when_the_name_agrees():
    j = aadt.join_aadt(_on_system_geo("W Karcher Rd"), _sh55_record())
    c = aadt.classify_on_system(j)
    assert bool(c.loc[1001, aadt.ON_SYSTEM_COL])
    assert c.loc[1001, aadt.ON_SYSTEM_REASON_COL] == "route 55"
    assert c.attrs["on_system"]["min_coverage"] == aadt.DEFAULT_ON_SYSTEM_COVERAGE


def test_on_system_rejects_the_road_that_merely_runs_beside_the_route():
    """The failure that made the earlier candidate list untrustworthy: 24 stubs of
    E Island Woods Dr took ``EAGLE RD (SH-55)``. Coverage cannot catch it — a short
    stub beside a long record covers 1.00 — so identity has to."""
    j = aadt.join_aadt(_on_system_geo("E Island Woods Dr"),
                       _sh55_record(desc="EAGLE RD (SH-55)"))
    c = aadt.classify_on_system(j)
    assert not bool(c.loc[1001, aadt.ON_SYSTEM_COL])
    assert c.loc[1001, aadt.ON_SYSTEM_CATEGORY_COL].startswith("a neighbouring road")
    assert c.attrs["on_system"]["n_rejected"] == 1
    # the segment naming the route itself is the other way to pass identity
    named = aadt.classify_on_system(
        aadt.join_aadt(_on_system_geo("E Island Woods Dr", road_number="55"),
                       _sh55_record(desc="EAGLE RD (SH-55)")))
    assert bool(named.loc[1001, aadt.ON_SYSTEM_COL])
    # ...and the test can be switched off, which is what it means to be a policy
    off = aadt.classify_on_system(j, require_identity=False)
    assert bool(off.loc[1001, aadt.ON_SYSTEM_COL])


def test_on_system_rejects_far_clipping_unnumbered_and_ramp_records():
    far = aadt.classify_on_system(
        aadt.join_aadt(_on_system_geo("W Karcher Rd"), _sh55_record(offset=-0.0006)))
    assert not bool(far.loc[1001, aadt.ON_SYSTEM_COL])
    assert far.loc[1001, aadt.ON_SYSTEM_CATEGORY_COL] == "the record is too far away"
    assert "m away" in far.loc[1001, aadt.ON_SYSTEM_REASON_COL]

    clipped = aadt.classify_on_system(
        aadt.join_aadt(_on_system_geo("W Karcher Rd"), _sh55_record(end_lat=43.6115)))
    assert not bool(clipped.loc[1001, aadt.ON_SYSTEM_COL])
    assert clipped.loc[1001, aadt.ON_SYSTEM_CATEGORY_COL] == "the record clips the segment"
    assert "% of the segment" in clipped.loc[1001, aadt.ON_SYSTEM_REASON_COL]

    unnumbered = aadt.classify_on_system(
        aadt.join_aadt(_on_system_geo("W Karcher Rd"), _sh55_record(desc="KARCHER RD")))
    assert not bool(unnumbered.loc[1001, aadt.ON_SYSTEM_COL])
    assert unnumbered.loc[1001, aadt.ON_SYSTEM_CATEGORY_COL] == \
        "the matched record names no route"

    ramp = aadt.classify_on_system(
        aadt.join_aadt(_on_system_geo("Eagle Rd off-ramp", frc=6), _divided_highway_layer()))
    assert not bool(ramp.loc[1001, aadt.ON_SYSTEM_COL])
    assert ramp.loc[1001, aadt.ON_SYSTEM_CATEGORY_COL] == \
        "the matched record is a ramp or connector"


def test_on_system_empty_frame_and_missing_match():
    empty = aadt.classify_on_system(aadt.join_aadt(_seg_geo().iloc[:0], _sh55_record()))
    assert len(empty) == 0 and aadt.ON_SYSTEM_COL in empty.columns
    nomatch = aadt.classify_on_system(          # the record is ~800 m west
        aadt.join_aadt(_on_system_geo("W Karcher Rd"), _sh55_record(offset=-0.01)))
    assert nomatch.loc[1001, aadt.ON_SYSTEM_CATEGORY_COL] == "no AADT record matched"


def test_street_name_tokens_drop_directionals_types_and_the_route():
    assert aadt.street_name_tokens("KARCHER RD (SH-55)") == {"KARCHER"}
    assert aadt.street_name_tokens("E Amity Rd") == {"AMITY"}
    assert aadt.street_names_agree("E Amity Rd", "W AMITY RD")
    assert not aadt.street_names_agree("E Amity Rd", "MERIDIAN RD (SH-69)")
    # only the record's own street counts, not the place it runs to
    assert aadt.street_name_tokens("IDAHO AVE @ US-95 CONN") == {"IDAHO"}
    assert not aadt.street_names_agree(None, "KARCHER RD")      # unnamed proves nothing


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


# ---------------------------------------------------------------------------
# Layer cache keying (ROADMAP Item 47)
# ---------------------------------------------------------------------------
def _write_layer_shapefile(path):
    """A four-record AADT layer spread over ~0.4°, written where pyogrio can read it.

    Two records sit in a tight western cluster and two well to the east, so a cache
    built for the cluster provably cannot answer a question about the whole extent.
    """
    rows = []
    for i, (lon, year) in enumerate([(-116.24, 2024), (-116.23, 2024),
                                     (-115.90, 2024), (-115.88, 2024)]):
        rows.append({
            "Year": year,
            "RouteID": f"0{i}001AUS095",
            "Route": None,
            "Segment": f"S{i}",
            "FromMeasur": 0.0,
            "ToMeasure": 1.0,
            "AADT": 10000 + i,
            "PassengerA": 9000,
            "Commercial": 1000,
            "Descriptio": f"RECORD {i}",
            "Descript_1": "NONE",
            "geometry": LineString([(lon, 43.60), (lon, 43.62)]),
        })
    # One 2023 record in the western cluster, so a year filter has something to drop.
    rows.append({**rows[0], "Year": 2023, "AADT": 999, "RouteID": "09001AUS095",
                 "Segment": "S9", "Descriptio": "OLD RECORD"})
    gpd.GeoDataFrame(rows, crs="EPSG:4326").to_file(path, engine="pyogrio")
    return path


@pytest.fixture
def layer_shp(tmp_path):
    return _write_layer_shapefile(tmp_path / "aadt_fixture.shp")


WEST = (-116.25, 43.59, -116.22, 43.63)     # the two western records
WIDE = (-116.30, 43.55, -115.85, 43.65)     # all four


def test_cache_records_what_it_covers(layer_shp, tmp_path):
    cache = tmp_path / "layer.parquet"
    layer = aadt.load_aadt(layer_shp, year=2024, bbox=WEST, cache_path=cache)
    assert len(layer) == 2
    assert layer.attrs["aadt_layer"]["cache"] == "written"

    meta = aadt.read_cache_meta(cache)
    assert meta["year"] == 2024
    assert tuple(meta["bbox"]) == WEST
    assert meta["n_rows"] == 2


def test_a_contained_request_is_a_cache_hit(layer_shp, tmp_path):
    cache = tmp_path / "layer.parquet"
    aadt.load_aadt(layer_shp, year=2024, bbox=WIDE, cache_path=cache)
    layer = aadt.load_aadt(layer_shp, year=2024, bbox=WEST, cache_path=cache)
    assert layer.attrs["aadt_layer"]["cache"] == "hit"


def test_a_wider_request_is_not_served_by_a_narrow_cache(layer_shp, tmp_path):
    """The Item 47 defect: whichever caller wrote the cache first decided the extent
    every later caller got, and the later caller had no way to tell."""
    cache = tmp_path / "layer.parquet"
    narrow = aadt.load_aadt(layer_shp, year=2024, bbox=WEST, cache_path=cache)
    assert len(narrow) == 2

    wide = aadt.load_aadt(layer_shp, year=2024, bbox=WIDE, cache_path=cache)
    assert wide.attrs["aadt_layer"]["cache"].startswith("rebuilt")
    assert len(wide) == 4, "a rebuild must answer the wider question, not the cached one"


def test_a_rebuild_widens_the_cache_rather_than_narrowing_it(layer_shp, tmp_path):
    """Two callers with different bounds must not thrash the cache between them —
    what made Session 60 reorder generate_screening_maps.py by hand."""
    cache = tmp_path / "layer.parquet"
    east = (-115.95, 43.55, -115.85, 43.65)
    aadt.load_aadt(layer_shp, year=2024, bbox=WEST, cache_path=cache)
    aadt.load_aadt(layer_shp, year=2024, bbox=east, cache_path=cache)

    # The cache now spans both requests, so each is a hit from here on.
    assert aadt.load_aadt(layer_shp, year=2024, bbox=WEST,
                          cache_path=cache).attrs["aadt_layer"]["cache"] == "hit"
    assert aadt.load_aadt(layer_shp, year=2024, bbox=east,
                          cache_path=cache).attrs["aadt_layer"]["cache"] == "hit"


def test_an_unrestricted_cache_serves_any_bbox(layer_shp, tmp_path):
    cache = tmp_path / "layer.parquet"
    full = aadt.load_aadt(layer_shp, year=2024, cache_path=cache)
    assert len(full) == 4
    assert aadt.read_cache_meta(cache)["bbox"] is None
    assert aadt.load_aadt(layer_shp, year=2024, bbox=WEST,
                          cache_path=cache).attrs["aadt_layer"]["cache"] == "hit"


def test_a_bbox_restricted_cache_does_not_answer_an_unrestricted_read(layer_shp, tmp_path):
    cache = tmp_path / "layer.parquet"
    aadt.load_aadt(layer_shp, year=2024, bbox=WEST, cache_path=cache)
    full = aadt.load_aadt(layer_shp, year=2024, cache_path=cache)
    assert full.attrs["aadt_layer"]["cache"].startswith("rebuilt")
    assert len(full) == 4


def test_a_different_year_rebuilds(layer_shp, tmp_path):
    """Year is a filter, not an extent: the rebuild replaces rather than widens."""
    cache = tmp_path / "layer.parquet"
    aadt.load_aadt(layer_shp, year=2024, bbox=WEST, cache_path=cache)
    old = aadt.load_aadt(layer_shp, year=2023, bbox=WEST, cache_path=cache)
    assert old.attrs["aadt_layer"]["cache"].startswith("rebuilt")
    assert (old["Year"] == 2023).all() and len(old) == 1


def test_a_cache_without_a_sidecar_is_judged_on_its_own_extent(layer_shp, tmp_path):
    """A cache written before Item 47 records nothing, so the extent of the data it
    holds stands in for the extent it was asked for.

    That is conservative in the safe direction. The features in a bbox-filtered read
    never reach past the bbox, so a request the *data* covers was certainly covered
    by the request that built it; one the data does not cover may only mean the layer
    has nothing out there, and rebuilding then is wasted work rather than a wrong
    answer.
    """
    cache = tmp_path / "legacy.parquet"
    aadt.load_aadt(layer_shp, year=2024, bbox=WIDE, cache_path=cache)
    aadt.cache_meta_path(cache).unlink()

    inside = (-116.10, 43.605, -116.00, 43.615)      # inside the cached data's reach
    assert aadt.load_aadt(layer_shp, year=2024, bbox=inside,
                          cache_path=cache).attrs["aadt_layer"]["cache"] == "hit"
    # Past the data's reach it cannot prove coverage, so it rebuilds rather than guess.
    beyond = aadt.load_aadt(layer_shp, year=2024, bbox=(-117.0, 43.0, -115.0, 44.0),
                            cache_path=cache)
    assert beyond.attrs["aadt_layer"]["cache"].startswith("rebuilt")
    # ...and it cannot prove it holds the whole layer either.
    aadt.cache_meta_path(cache).unlink()
    unrestricted = aadt.load_aadt(layer_shp, year=2024, cache_path=cache)
    assert unrestricted.attrs["aadt_layer"]["cache"].startswith("rebuilt")


def test_the_cache_keys_on_its_source_download(layer_shp, tmp_path):
    """Item 52: ``Cumulative_AADT.zip`` and ``AADT_2025.zip`` are two downloads of one
    layer. A cache built from one must not answer for the other, even for the same
    year and extent."""
    import json

    cache = tmp_path / "layer.parquet"
    aadt.load_aadt(layer_shp, year=2024, bbox=WIDE, cache_path=cache)
    meta = aadt.read_cache_meta(cache)
    assert meta["source"] == {"name": "aadt_fixture.shp",
                              "bytes": layer_shp.stat().st_size}
    assert aadt.load_aadt(layer_shp, year=2024, bbox=WEST,
                          cache_path=cache).attrs["aadt_layer"]["cache"] == "hit"

    other_dir = tmp_path / "newer"
    other_dir.mkdir()
    other = _write_layer_shapefile(other_dir / "aadt_2025.shp")
    rebuilt = aadt.load_aadt(other, year=2024, bbox=WEST, cache_path=cache)
    state = rebuilt.attrs["aadt_layer"]["cache"]
    assert state.startswith("rebuilt") and "aadt_fixture.shp" in state
    assert aadt.read_cache_meta(cache)["source"]["name"] == "aadt_2025.shp"

    # A sidecar from before Item 52 records no source, so it cannot prove a match.
    meta = aadt.read_cache_meta(cache)
    meta.pop("source")
    aadt.cache_meta_path(cache).write_text(json.dumps(meta))
    again = aadt.load_aadt(other, year=2024, bbox=WEST, cache_path=cache)
    assert "records no source" in again.attrs["aadt_layer"]["cache"]


def test_default_year_and_source_are_the_2025_download():
    assert aadt.DEFAULT_YEAR == 2025
    assert aadt.DEFAULT_SOURCE == "AADT_2025.zip"


def test_a_cache_missing_a_requested_column_rebuilds(layer_shp, tmp_path):
    cache = tmp_path / "layer.parquet"
    aadt.load_aadt(layer_shp, year=2024, bbox=WIDE,
                   columns=["Year", "RouteID", "AADT"], cache_path=cache)
    full = aadt.load_aadt(layer_shp, year=2024, bbox=WIDE, cache_path=cache)
    assert full.attrs["aadt_layer"]["cache"].startswith("rebuilt")
    assert "Descriptio" in full.columns


def test_shortfall_reasons_name_what_is_wrong():
    """The reason travels with the rebuild so a surprising re-read explains itself."""
    cached = gpd.GeoDataFrame(
        {"Year": [2024, 2024], "AADT": [1, 2]},
        geometry=[LineString([(-116.30, 43.55), (-116.30, 43.65)]),
                  LineString([(-116.20, 43.55), (-116.20, 43.65)])], crs="EPSG:4326")

    assert aadt._cache_shortfall(cached, None, year=2024, bbox=WEST,
                                 columns=["Year", "AADT"]) is None
    assert "missing column" in aadt._cache_shortfall(
        cached, None, year=2024, bbox=WEST, columns=["Year", "Descriptio"])
    assert "year" in aadt._cache_shortfall(
        cached, {"year": 2023, "bbox": None}, year=2024, bbox=None, columns=["Year"])
    assert "request needs" in aadt._cache_shortfall(
        cached, {"year": 2024, "bbox": list(WEST)}, year=2024, bbox=WIDE, columns=["Year"])


def test_a_geoparquet_source_is_read_directly(layer_shp, tmp_path):
    """A saved layer is a source in its own right — and a cache that has stopped
    covering the request is re-read as one, which before Item 47 never happened
    because the cache short-circuited before the source was touched."""
    saved = tmp_path / "saved_layer.geoparquet"
    aadt.load_aadt(layer_shp, year=2024).to_parquet(saved)

    layer = aadt.load_aadt(saved, year=2024, bbox=WEST)
    assert len(layer) == 2 and layer.crs.to_epsg() == 4326
    assert (layer["Year"] == 2024).all()

    older = aadt.load_aadt(saved, year=2023)
    assert len(older) == 0, "the year filter applies to a parquet source too"


def test_a_degenerate_bbox_reads_as_no_restriction(layer_shp, tmp_path):
    """``geo.total_bounds`` on an empty frame is ``(nan, nan, nan, nan)``; a caller
    that derives its bbox that way means 'everything', not a box shapely will refuse."""
    cache = tmp_path / "layer.parquet"
    nan_bbox = (float("nan"),) * 4
    layer = aadt.load_aadt(layer_shp, year=2024, bbox=nan_bbox, cache_path=cache)
    assert len(layer) == 4
    assert aadt.read_cache_meta(cache)["bbox"] is None

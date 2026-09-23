"""Tests for ``inrix_tools.itd_layers`` — ITD's State Highway System and the Census
urban areas (ROADMAP Item 52)."""
from pathlib import Path

import geopandas as gpd
import pandas as pd
import pytest
from shapely.geometry import LineString, Polygon

from inrix_tools import itd_layers
from inrix_tools.io import SEGMENT_COL

REPO_ROOT = Path(__file__).resolve().parents[1]
SHS_ZIP = REPO_ROOT / "SHS_Primary.zip"
URBAN_ZIP = REPO_ROOT / "Urban_Area.zip"


# ---------------------------------------------------------------------------
# SHS decoding
# ---------------------------------------------------------------------------
def test_shs_codes_decode_road_kind_route_type_and_carriageway():
    layer = itd_layers.shs_frame([
        {"RouteId": "01010AIN084", "RoadType": 4, "RouteTypeC": 1, "Travelway": "A",
         "geometry": LineString([(0, 0), (0, 1)])},
        {"RouteId": "01010DIN084", "RoadType": 4, "RouteTypeC": 1, "Travelway": "D",
         "geometry": LineString([(0.001, 0), (0.001, 1)])},
        {"RouteId": "01043AIN084", "RoadType": 5, "RouteTypeC": 1, "Travelway": "A",
         "geometry": LineString([(1, 0), (1, 1)])},
        {"RouteId": "01020AIN084", "RoadType": 4, "RouteTypeC": 3, "Travelway": "A",
         "geometry": LineString([(2, 0), (2, 1)])},
        {"RouteId": "05100ASH077", "RoadType": 4, "RouteTypeC": 2, "Travelway": "A",
         "geometry": LineString([(3, 0), (3, 1)])},
        {"RouteId": "05659AUS095", "RoadType": 6, "RouteTypeC": 4, "Travelway": "A",
         "geometry": LineString([(4, 0), (4, 1)])},
    ])
    assert list(layer["RouteID"]) == ["01010AIN084", "01010DIN084", "01043AIN084",
                                      "01020AIN084", "05100ASH077", "05659AUS095"]
    assert list(layer["road_kind"]) == ["roadway", "roadway", "ramp", "roadway",
                                        "roadway", "other"]
    assert list(layer["route_type"]) == ["mainline", "mainline", "mainline", "business",
                                         "spur", "connector"]
    assert list(layer["carriageway"]) == ["A", "D", "A", "A", "A", "A"]
    assert list(layer["route_number"]) == [84, 84, 84, 84, 77, 95]
    assert list(layer["route_class"]) == ["IN", "IN", "IN", "IN", "SH", "US"]


def test_shs_frame_defaults_to_a_primary_mainline_roadway():
    layer = itd_layers.shs_frame([{"RouteId": "01910AUS012",
                                   "geometry": LineString([(0, 0), (0, 1)])}])
    row = layer.iloc[0]
    assert (row["road_kind"], row["route_type"], row["carriageway"]) == (
        "roadway", "mainline", "A")


@pytest.mark.skipif(not SHS_ZIP.exists(), reason="SHS_Primary.zip not present (gitignored)")
def test_load_shs_real_layer():
    layer = itd_layers.load_shs(SHS_ZIP)
    # 1,179 lines, one of them a two-part MultiLineString -> 1,180 parts.
    assert len(layer) == layer.attrs["shs_layer"]["n_read"] + 1
    assert set(layer.geom_type) == {"LineString"}
    assert not layer.has_z.any()                     # the M/Z values are dropped
    assert layer.crs.to_epsg() == 4326
    assert set(layer["carriageway"]) == {"A", "D"}
    # Every line's band matches its sign type (1 = I, 2 = US, 3 = SH).
    assert (layer["route_class"] == layer["SignTypeCo"].map(itd_layers.SIGN_TYPES)).all()
    # US-12 on the Lewiston levee is a mainline roadway; the D line runs beside it.
    us12 = layer[layer["RouteID"].str.startswith("01910")]
    assert {"01910AUS012", "01910DUS012"} <= set(us12["RouteID"])


# ---------------------------------------------------------------------------
# Urban context
# ---------------------------------------------------------------------------
def _urban():
    # A 0.02° square "town" around (-116.20, 43.60) and a second one well east.
    return gpd.GeoDataFrame(
        {"UACE": ["00001", "00002"], "NAME": ["Townsville, ID", "Elsewhere, ID"],
         "Population": [10000.0, 5000.0],
         "geometry": [Polygon([(-116.21, 43.59), (-116.19, 43.59), (-116.19, 43.61),
                               (-116.21, 43.61)]),
                      Polygon([(-115.51, 43.59), (-115.49, 43.59), (-115.49, 43.61),
                               (-115.51, 43.61)])]},
        crs="EPSG:4326")


def _segments(**lines):
    frame = gpd.GeoDataFrame({"geometry": list(lines.values())},
                             index=pd.Index(list(lines.keys()), name=SEGMENT_COL),
                             crs="EPSG:4326")
    return frame


def test_urban_context_inside_outside_and_at_the_boundary():
    geo = _segments(
        inside=LineString([(-116.201, 43.600), (-116.199, 43.600)]),
        # 0.004° east of the boundary at -116.19: outside, nearest is Townsville
        outside=LineString([(-116.187, 43.600), (-116.185, 43.600)]),
        # straddles the east boundary: half in, half out, midpoint on the edge
        straddle=LineString([(-116.192, 43.600), (-116.188, 43.600)]),
    )
    ctx = itd_layers.urban_context(geo, _urban())

    assert ctx.loc["inside", "urban_area"] == "Townsville, ID"
    assert bool(ctx.loc["inside", "urban_inside"]) is True
    assert ctx.loc["inside", "urban_share"] == 1.0
    assert ctx.loc["inside", "urban_edge_m"] > 700     # ~800 m from every edge

    assert ctx.loc["outside", "urban_area"] == "Townsville, ID"   # nearest, not inside
    assert bool(ctx.loc["outside", "urban_inside"]) is False
    assert ctx.loc["outside", "urban_share"] == 0.0
    assert -500 < ctx.loc["outside", "urban_edge_m"] < -200       # signed: outside

    assert ctx.loc["straddle", "urban_uace"] == "00001"
    assert 0.4 < ctx.loc["straddle", "urban_share"] < 0.6
    assert abs(ctx.loc["straddle", "urban_edge_m"]) < 1.0         # on the boundary


def test_urban_context_is_never_a_filter():
    """Every segment comes back, rural or not, with or without geometry."""
    geo = _segments(a=LineString([(-116.0, 44.5), (-116.0, 44.51)]), b=None)
    geo = geo.set_geometry("geometry", crs="EPSG:4326")
    ctx = itd_layers.urban_context(geo, _urban())
    assert list(ctx.index) == ["a", "b"]
    assert ctx.loc["a", "urban_area"] in {"Townsville, ID", "Elsewhere, ID"}
    assert ctx.loc["a", "urban_edge_m"] < -10000                  # far out of town
    assert pd.isna(ctx.loc["b", "urban_area"])


@pytest.mark.skipif(not URBAN_ZIP.exists(), reason="Urban_Area.zip not present (gitignored)")
def test_load_urban_areas_real_layer():
    urban = itd_layers.load_urban_areas(URBAN_ZIP)
    assert len(urban) == 26
    assert urban.crs.to_epsg() == 4326
    names = set(urban["NAME"])
    assert "Lewiston, ID--WA" in names and "Boise City, ID" in names
    assert not any(n.startswith(("Bonners Ferry", "Kellogg", "Salmon")) for n in names)
    assert urban["UACE"].str.fullmatch(r"\d{5}").all()


# ---------------------------------------------------------------------------
# AADT record kinds from the SHS
# ---------------------------------------------------------------------------
def _aadt_records():
    from inrix_tools import aadt

    rows = [  # (RouteID, Descriptio, AADT, dx)
        ("02410AIN184", "JCT I-84 FLYING WYE IC", 68000, 0.00040),   # I-184 mainline
        ("25273AIN184", "WB CONN FROM I-184 IC49", 5000, 0.0),        # a connector
        ("02080AUS020", "MCBRIDE RD", 9500, 0.0),                     # a Broadway ramp
        ("02070AUS020", "FEDERAL WAY SB OFF RAMP", 29500, 0.00008),  # US-20 mainline
        ("02717AOH000", "COMMERCE AVE", 10500, 0.01),                 # local, not in SHS
    ]
    layer = gpd.GeoDataFrame(
        {"RouteID": [r[0] for r in rows], "Descriptio": [r[1] for r in rows],
         "AADT": [float(r[2]) for r in rows],
         "geometry": [LineString([(-116.2 + r[3], 43.60), (-116.2 + r[3], 43.61)])
                      for r in rows]}, crs="EPSG:4326")
    return aadt.classify_aadt_records(layer)


def _shs_for_records():
    return itd_layers.shs_frame([
        {"RouteId": "02410AIN184", "RoadType": 4, "geometry": LineString([(0, 0), (0, 1)])},
        {"RouteId": "25273AIN184", "RoadType": 5, "RouteTypeC": 4,
         "geometry": LineString([(1, 0), (1, 1)])},
        {"RouteId": "02080AUS020", "RoadType": 5, "geometry": LineString([(2, 0), (2, 1)])},
        {"RouteId": "02070AUS020", "RoadType": 4, "geometry": LineString([(3, 0), (3, 1)])},
    ])


def test_record_kinds_come_from_the_shs_where_it_is_unambiguous():
    """ITD's descriptions name a record's end points: I-184's mainline reads as a
    connector and a Broadway ramp with a wrong 2025 description reads as mainline."""
    layer = _aadt_records()
    kinds = dict(zip(layer["RouteID"], layer["record_kind"]))
    assert kinds["02410AIN184"] == "connector" and kinds["02080AUS020"] == "mainline"

    fixed = itd_layers.classify_records_with_shs(layer, _shs_for_records())
    kinds = dict(zip(fixed["RouteID"], fixed["record_kind"]))
    src = dict(zip(fixed["RouteID"], fixed["record_kind_source"]))
    assert kinds["02410AIN184"] == "mainline" and src["02410AIN184"] == "shs"
    assert kinds["02080AUS020"] == "ramp"
    assert kinds["25273AIN184"] == "ramp"
    assert kinds["02070AUS020"] == "mainline"
    assert src["02717AOH000"] == "description"          # the SHS doesn't draw local roads


def test_the_join_takes_the_mainline_count_once_records_are_classified_by_the_shs():
    from inrix_tools import aadt

    seg = gpd.GeoDataFrame(
        {"RoadName": ["I-184 W"], "RoadNumber": ["184"], "RoadList": ["I-184"], "FRC": [2],
         "geometry": [LineString([(-116.2, 43.601), (-116.2, 43.609)])]},
        index=pd.Index([1], name=SEGMENT_COL), crs="EPSG:4326")
    layer = _aadt_records()
    layer = layer[layer["RouteID"].str.endswith("184")]
    assert aadt.join_aadt(seg, layer).loc[1, "AADT"] == 5000            # the connector
    fixed = itd_layers.classify_records_with_shs(layer, _shs_for_records())
    assert aadt.join_aadt(seg, fixed).loc[1, "AADT"] == 68000           # the mainline

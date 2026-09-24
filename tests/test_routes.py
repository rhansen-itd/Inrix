"""Tests for ``inrix_tools.routes`` — state-route membership from ITD's AADT layer
(ROADMAP Item 48) and, since Item 52, from ITD's State Highway System."""
from pathlib import Path

import geopandas as gpd
import pandas as pd
import pytest
from shapely.geometry import LineString

from inrix_tools import itd_layers, routes
from inrix_tools.io import SEGMENT_COL

REPO_ROOT = Path(__file__).resolve().parents[1]
D2_NET = REPO_ROOT / "geometry_cache" / "d2_network.geoparquet"
D2_AADT = REPO_ROOT / "geometry_cache" / "d2_aadt.parquet"

# Longitude offsets near 43.6°N: 1e-5 deg ≈ 0.8 m.
ON = 0.00003        # ~2.4 m: the same centreline, digitised twice
CENTRE = 0.00031    # ~25 m: a divided highway's one centreline
FAR = 0.0012        # ~97 m: the parallel street a block away


def _line(dx=0.0):
    return LineString([(-116.20 + dx, 43.610), (-116.20 + dx, 43.620)])


def _segs(*rows):
    """Segments as ``(id, RoadNumber, RoadName, RoadList, dx[, County[, FRC]])``."""
    recs = []
    for r in rows:
        sid, rn, name, rlist, dx = r[:5]
        county = r[5] if len(r) > 5 else "Ada"
        frc = r[6] if len(r) > 6 else 3
        recs.append({"sid": sid, "RoadNumber": rn, "RoadName": name, "RoadList": rlist,
                     "County": county, "FRC": frc, "SlipRoad": "0", "geometry": _line(dx)})
    frame = gpd.GeoDataFrame(recs, crs="EPSG:4326").set_index("sid")
    frame.index.name = SEGMENT_COL
    return frame


def _layer(*rows):
    """AADT records as ``(RouteID, Descriptio, dx)``."""
    return gpd.GeoDataFrame(
        {"RouteID": [r[0] for r in rows], "Descriptio": [r[1] for r in rows],
         "AADT": [5000.0] * len(rows), "geometry": [_line(r[2]) for r in rows]},
        crs="EPSG:4326")


# ---------------------------------------------------------------------------
# Identity parsing
# ---------------------------------------------------------------------------
def test_road_list_routes_reads_every_way_inrix_writes_a_route():
    assert routes.road_list_routes("US-20|US-26|US-93|Idaho Medal of Honor Hwy") == {20, 26, 93}
    assert routes.road_list_routes("N ID-34|ID-36|N Highway 34") == {34, 36}
    assert routes.road_list_routes("E 3900 N|Highway 30") == {30}
    assert routes.road_list_routes(None) == set()
    assert routes.road_list_routes("Main St", road_name="ID-13") == {13}


def test_a_business_route_is_not_the_route():
    rlist = "N Main St|ID-13|US-12-BL|US-93-BR|I-84-BL"
    assert routes.road_list_routes(rlist) == {13}
    assert routes.road_list_business_routes(rlist) == {12, 93, 84}


def test_inrix_route():
    assert routes.inrix_route("12") == 12
    assert routes.inrix_route(" 95 ") == 95
    assert routes.inrix_route(None) is None
    assert routes.inrix_route(float("nan")) is None
    assert routes.inrix_route("I-84") is None


# ---------------------------------------------------------------------------
# Membership verdicts
# ---------------------------------------------------------------------------
def test_the_lewiston_case_bypass_gained_and_downtown_street_dropped():
    """INRIX numbers the downtown street 12 and leaves the bypass unnumbered; ITD's
    layer carries the street as a local record and US-12 on the bypass."""
    geo = _segs((1, "12", "Main St", "Main St", 0.0),
                (2, None, "Levee Byp", None, FAR))
    layer = _layer(("06830AOH000", "5TH ST", ON),            # local, on Main St
                   ("01910AUS012", "18TH ST/DIKE BYPASS RD", FAR + ON))
    m = routes.route_membership(geo, layer)
    assert m.loc[1, "verdict"] == routes.INRIX_ONLY
    assert m.loc[1, "routes"] == "" and m.loc[1, "route_number"] is None
    assert m.loc[2, "verdict"] == routes.ITD_ONLY
    assert m.loc[2, "routes"] == "12" and m.loc[2, "route_number"] == "12"
    assert m.loc[2, "itd_route_id"] == "01910AUS012"


def test_a_shared_road_is_concurrent_not_an_error():
    """ITD carries US-20/26/93 near Arco as one US-93 record; INRIX says 20."""
    geo = _segs((1, "20", "US-20", "US-20|US-26|US-93|Idaho Medal of Honor Hwy", 0.0))
    m = routes.route_membership(geo, _layer(("02220AUS093", "AUSTIN RD", ON)))
    assert m.loc[1, "verdict"] == routes.CONCURRENT
    assert m.loc[1, "route_number"] == "20"            # chains keep INRIX's number
    assert set(m.loc[1, "routes"].split("/")) == {"20", "93"}


def test_a_divided_highway_carriageway_is_not_dropped_for_a_nearer_frontage_road():
    geo = _segs((1, "84", "I-84 W", "I-84", 0.0, "Ada", 1))
    layer = _layer(("01010AIN084", "EAGLE RD IC #46", -CENTRE),
                   ("12345AOH000", "FRONTAGE RD", ON))
    m = routes.route_membership(geo, layer)
    assert m.loc[1, "verdict"] == routes.AGREE
    assert m.loc[1, "routes"] == "84"


def test_a_numbered_segment_no_record_decides_is_kept_and_flagged():
    """New construction: INRIX numbers it, ITD's layer has nothing there yet."""
    geo = _segs((1, "95", "US-95 N", "US-95", 0.0))
    m = routes.route_membership(geo, _layer(("06830AOH000", "ELSEWHERE", FAR * 3)))
    assert m.loc[1, "verdict"] == routes.UNCONFIRMED
    assert m.loc[1, "route_number"] == "95" and m.loc[1, "routes"] == "95"


def test_an_override_beats_the_layer():
    """Reisenauer Rd is still a US-95 record in the layer; the road is the county's."""
    geo = _segs((1, None, "Reisenauer Rd", None, 0.0, "Latah"),
                (2, None, "Reisenauer Rd", None, 0.0, "Nez Perce"))
    overrides = pd.DataFrame([{"county": "Latah", "road_name": "Reisenauer Rd",
                               "inrix_route": "", "route": "", "note": "old US-95"}])
    m = routes.route_membership(geo, _layer(("01540AUS095", "THORN CREEK RD", ON)),
                                overrides=overrides)
    assert m.loc[1, "verdict"] == routes.OVERRIDE and m.loc[1, "routes"] == ""
    assert "old US-95" in m.loc[1, "reason"]
    assert m.loc[2, "verdict"] == routes.ITD_ONLY     # other county: not overridden


def test_a_route_named_only_in_a_local_records_description_is_ambiguous():
    """``US-95`` on an OH record is the cross street ("to US-95"): it neither adds a
    route to an unnumbered segment nor takes one from a numbered one."""
    geo = _segs((1, None, "Reubens-Gifford Rd", None, 0.0),
                (2, "55", "Karcher Rd", "ID-55", FAR))
    layer = _layer(("06510AOH000", "US-95", ON),
                   ("25383AOH000", "KARCHER RD (SH-55)", FAR + ON),
                   ("25384AOH000", "TEN LN", FAR + ON))
    m = routes.route_membership(geo, layer)
    assert m.loc[1, "verdict"] == routes.OFF_SYSTEM
    assert m.loc[2, "verdict"] == routes.UNCONFIRMED and m.loc[2, "route_number"] == "55"


def test_an_interstate_band_is_never_given_to_a_segment_inrix_does_not_number_so():
    geo = _segs((1, None, "Grouse Creek Rd", None, 0.0, "Shoshone", 5),
                (2, "55", "Caldwell Blvd", "Caldwell Blvd|ID-55|I-84-BL", FAR))
    layer = _layer(("01773AIN090", "I-90 FRONTAGE", ON),
                   ("02042AIN084", "CALDWELL BLVD", FAR + ON))
    m = routes.route_membership(geo, layer)
    assert m.loc[1, "verdict"] == routes.OFF_SYSTEM
    assert m.loc[2, "verdict"] == routes.BUSINESS
    assert m.loc[2, "route_number"] == "55" and m.loc[2, "routes"] == "55"


def test_a_business_route_band_keeps_inrix_number():
    """Twin Falls: ITD bands US-93 Business ``US093``; RoadList says ``US-93-BR``."""
    geo = _segs((1, "30", "E 3900 N", "E 3900 N|US-30|US-93-BR", 0.0))
    m = routes.route_membership(geo, _layer(("02043AUS093", "N 2500 E RD", ON)))
    assert m.loc[1, "verdict"] == routes.BUSINESS and m.loc[1, "route_number"] == "30"


def test_a_different_route_on_the_segment_renumbers_it():
    geo = _segs((1, "43", "ID-43", "ID-43", 0.0))
    m = routes.route_membership(geo, _layer(("02540AUS093", "N FORK", ON)))
    assert m.loc[1, "verdict"] == routes.RENUMBERED
    assert m.loc[1, "route_number"] == "93" and m.loc[1, "routes"] == "93"


def test_a_ramp_keeps_inrix_reading():
    geo = _segs((1, "84", "I-84 Exit 46", "I-84", 0.0))
    geo["SlipRoad"] = "1"
    m = routes.route_membership(geo, _layer(("12345AOH000", "EAGLE RD", ON)))
    assert m.loc[1, "verdict"] == routes.RAMP and m.loc[1, "route_number"] == "84"


def test_membership_records_its_policy_and_an_empty_layer_is_safe():
    geo = _segs((1, "12", "Main St", None, 0.0), (2, None, "Elm St", None, FAR))
    m = routes.route_membership(geo, _layer())
    assert list(m["verdict"]) == [routes.UNCONFIRMED, routes.OFF_SYSTEM]
    pol = m.attrs["route_membership"]
    assert pol["on_distance_m"] == routes.ON_DISTANCE_M
    assert pol["verdicts"] == {routes.UNCONFIRMED: 1, routes.OFF_SYSTEM: 1}


# ---------------------------------------------------------------------------
# Applying, grouping, round-tripping
# ---------------------------------------------------------------------------
def _lewiston_membership():
    geo = _segs((1, "12", "Main St", "Main St", 0.0),
                (2, None, "Levee Byp", None, FAR),
                (3, "20", "US-20", "US-20|US-93", 2 * FAR))
    layer = _layer(("06830AOH000", "5TH ST", ON),
                   ("01910AUS012", "5TH ST", FAR + ON),
                   ("02220AUS093", "AUSTIN RD", 2 * FAR + ON))
    return routes.route_membership(geo, layer)


def test_apply_route_membership_rewrites_road_number_and_keeps_inrix():
    m = _lewiston_membership()
    net = pd.DataFrame({"XDSegID": [1, 2, 3, 4], "RoadNumber": ["12", None, "20", "7"]})
    out = routes.apply_route_membership(net, m)
    assert list(out["RoadNumber"]) == [None, "12", "20", "7"]   # 4 is not covered
    assert list(out["RoadNumber_inrix"]) == ["12", None, "20", "7"]
    assert list(net["RoadNumber"]) == ["12", None, "20", "7"]   # input untouched


def test_route_segments_reads_concurrent_membership():
    m = _lewiston_membership()
    assert routes.route_segments(m, 12) == [2]
    assert routes.route_segments(m, "93") == [3]
    assert routes.route_segments(m, 20) == [3]


def test_changes_by_road_groups_what_moved():
    m = _lewiston_membership()
    miles = pd.Series({1: 0.25, 2: 0.5, 3: 1.0})
    table = routes.changes_by_road(m, miles)
    assert set(table["verdict"]) == {routes.INRIX_ONLY, routes.ITD_ONLY}
    row = table[table["RoadName"] == "Levee Byp"].iloc[0]
    assert row["segments"] == 1 and row["miles"] == 0.5 and row["segment_ids"] == "2"


def test_membership_round_trips_through_csv(tmp_path):
    m = _lewiston_membership()
    back = routes.read_membership(routes.write_membership(m, tmp_path / "m.csv"))
    assert list(back.index) == [1, 2, 3]
    assert list(back["verdict"]) == list(m["verdict"])
    assert back.loc[1, "route_number"] is None and back.loc[2, "route_number"] == "12"
    assert back.loc[1, "routes"] == "" and routes.route_segments(back, 12) == [2]


def test_load_route_overrides_validates(tmp_path):
    good = tmp_path / "ok.csv"
    good.write_text("# comment\ncounty,road_name,inrix_route,route,note\n"
                    "Latah,Reisenauer Rd,,,old US-95\n")
    frame = routes.load_route_overrides(good)
    assert frame.iloc[0]["route"] == "" and frame.iloc[0]["note"] == "old US-95"
    bad = tmp_path / "bad.csv"
    bad.write_text("county,road_name,inrix_route,route,note\nLatah,X,,,\n")
    with pytest.raises(ValueError, match="note"):
        routes.load_route_overrides(bad)
    bad.write_text("county,road_name,inrix_route,route,note\nLatah,X,,US-95,why\n")
    with pytest.raises(ValueError, match="number"):
        routes.load_route_overrides(bad)


def test_the_shipped_override_table_loads_with_reisenauer_retired():
    """Item 52 retired the Reisenauer row: the State Highway System agrees with the
    owner. The file stays, with the retired row recorded as a comment."""
    path = REPO_ROOT / "scripts" / "route_overrides.csv"
    frame = routes.load_route_overrides(path)
    assert "Reisenauer Rd" not in set(frame["road_name"])
    assert "Reisenauer Rd" in path.read_text()


# ---------------------------------------------------------------------------
# The real case (skips without the gitignored caches)
# ---------------------------------------------------------------------------
@pytest.mark.skipif(not (D2_NET.exists() and D2_AADT.exists()),
                    reason="D2 network / AADT caches not built (gitignored)")
def test_lewiston_us12_on_the_real_layer():
    from inrix_tools import aadt, geometry
    net = gpd.read_parquet(D2_NET)
    net = net[net["PostalCode"].astype(str).str.startswith("835")
              | net["RoadName"].isin(["Levee Byp", "Main St", "D St"])]
    net = net[net["County"] == "Nez Perce"]
    geo = geometry.segment_geometry(net)
    geo["County"] = net.set_index(net["XDSegID"].astype(int))["County"].reindex(geo.index)
    m = routes.route_membership(geo, aadt.load_aadt(D2_AADT, year=None))
    levee = m[m["RoadName"] == "Levee Byp"]
    assert (levee["verdict"] == routes.ITD_ONLY).sum() >= 12
    downtown = m[m["RoadName"].isin(["Main St", "D St"]) & (m["inrix_route"] == 12)]
    assert (downtown["verdict"] == routes.INRIX_ONLY).sum() >= 10


# ---------------------------------------------------------------------------
# Item 52: identity from the State Highway System
# ---------------------------------------------------------------------------
def _shs(*rows):
    """SHS lines as ``(RouteId, dx[, RouteTypeC[, Travelway[, RoadType]]])``."""
    recs = []
    for r in rows:
        rid, dx = r[:2]
        recs.append({"RouteId": rid, "RouteTypeC": r[2] if len(r) > 2 else 1,
                     "Travelway": r[3] if len(r) > 3 else rid[5],
                     "RoadType": r[4] if len(r) > 4 else 4, "geometry": _line(dx)})
    return itd_layers.shs_frame(recs)


def test_an_shs_line_wins_over_an_aadt_layer_route():
    """The AADT layer (a stale year) puts route 95 on the road; the SHS says 12."""
    geo = _segs((1, None, "Levee Byp", None, 0.0),
                (2, "12", "US-12", "US-12", FAR))
    layer = _layer(("01540AUS095", "OLD ALIGNMENT", ON), ("01910AUS012", "X", FAR + ON))
    shs = _shs(("01910AUS012", ON))                     # only on segment 1
    m = routes.route_membership(geo, layer, shs=shs)
    assert m.loc[1, "verdict"] == routes.ITD_ONLY and m.loc[1, "routes"] == "12"
    assert m.loc[1, "source"] == routes.SOURCE_SHS
    assert m.loc[1, "itd_route_id"] == "01910AUS012"
    # Segment 2: the AADT layer confirms 12 there, but the SHS — complete for state
    # highway — has no line within 40 m, so it is off the system.
    assert m.loc[2, "verdict"] == routes.INRIX_ONLY and m.loc[2, "routes"] == ""
    assert m.attrs["route_membership"]["identity"] == "shs"


def test_a_travelway_d_carriageway_is_on_the_system():
    """Divided I-84: the SHS has a line on each carriageway (A and D), 50 m apart.
    Each INRIX carriageway is *on* its own line — the AADT layer's single centreline
    could only ever be *near*."""
    geo = _segs((1, "84", "I-84 E", "I-84", 0.0, "Ada", 1),
                (2, "84", "I-84 W", "I-84", 2 * CENTRE, "Ada", 1),
                (3, None, None, None, 2 * CENTRE + ON, "Ada", 1))
    shs = _shs(("01010AIN084", ON), ("01010DIN084", 2 * CENTRE + ON))
    m = routes.route_membership(geo, shs=shs)
    assert list(m["verdict"]) == [routes.AGREE, routes.AGREE, routes.OFF_SYSTEM]
    assert list(m["routes"]) == ["84", "84", ""]
    # An unnumbered segment on the D line is not given the interstate (Item 48's rule).
    assert "interstate band" in m.loc[3, "reason"]


def test_the_reisenauer_case_needs_no_override_with_the_shs():
    """US-95 moved to a new alignment; the old road (Reisenauer Rd) went to the county.
    The stale AADT year still has a US-95 record on it and none on the new alignment;
    the SHS has it right."""
    geo = _segs((1, None, "Reisenauer Rd", None, 0.0, "Latah"),
                (2, "95", "US-95 S", "US-95", FAR, "Latah"))
    stale = _layer(("01540AUS095", "THORN CREEK RD", ON))
    shs = _shs(("01540AUS095", FAR + ON))
    m = routes.route_membership(geo, stale, shs=shs)
    assert m.loc[1, "verdict"] == routes.OFF_SYSTEM and m.loc[1, "routes"] == ""
    assert m.loc[2, "verdict"] == routes.AGREE and m.loc[2, "routes"] == "95"
    assert (m["source"] == routes.SOURCE_SHS).all()
    # Item 48's answer from the stale layer alone, for contrast.
    old = routes.route_membership(geo, stale)
    assert old.loc[1, "verdict"] == routes.ITD_ONLY
    assert old.loc[2, "verdict"] == routes.UNCONFIRMED


def test_an_shs_business_loop_belongs_to_its_parent_route():
    """Business loops are state highway (owner, Session 67). Pocatello's 5th Ave: INRIX
    numbers it 15, the SHS carries I-15 Business, RoadList adds US-30/US-91 — all kept,
    labelled ``business``. An unnumbered street on the loop (Kellogg's Markwell Ave)
    joins it too."""
    geo = _segs((1, "15", "S 5th Ave", "S 5th Ave|US-30|US-91|I-15-BL", 0.0),
                (2, "55", "Caldwell Blvd", "Caldwell Blvd|ID-55|I-84-BL", FAR),
                (3, None, "Markwell Ave", None, 2 * FAR))
    shs = _shs(("01360AIN015", ON, 3), ("02042AIN084", FAR + ON, 3),
               ("01664AIN090", 2 * FAR + ON, 3))
    m = routes.route_membership(geo, shs=shs)
    assert (m["verdict"] == routes.BUSINESS).all()
    assert m.loc[1, "routes"] == "15/30/91" and m.loc[1, "route_number"] == "15"
    assert m.loc[2, "routes"] == "55/84" and m.loc[2, "route_number"] == "55"
    assert m.loc[3, "routes"] == "90" and m.loc[3, "route_number"] == "90"
    assert routes.route_segments(m, 84) == [2]


def test_a_former_business_loop_off_the_shs_is_dropped():
    """Caldwell's Cleveland Blvd: INRIX still numbers it 84, but it is no longer state
    highway, so the SHS has no line on it."""
    geo = _segs((1, "84", "Cleveland Blvd", "Cleveland Blvd|I-84-BL", 0.0, "Canyon"))
    m = routes.route_membership(geo, shs=_shs(("02042AIN084", FAR, 3)))
    assert m.loc[1, "verdict"] == routes.INRIX_ONLY and m.loc[1, "routes"] == ""


def test_a_spur_and_a_connector_are_the_route():
    geo = _segs((1, None, "Elba-Almo Hwy", None, 0.0, "Cassia"),
                (2, "95", "E C St", "US-95", FAR, "Latah"))
    shs = _shs(("05100ASH077", ON, 2), ("05659AUS095", FAR + ON, 4))
    m = routes.route_membership(geo, shs=shs)
    assert m.loc[1, "verdict"] == routes.ITD_ONLY and m.loc[1, "routes"] == "77"
    assert m.loc[2, "verdict"] == routes.AGREE and m.loc[2, "routes"] == "95"


def test_shs_ramps_are_not_mainline_evidence():
    """An SHS ramp line (RoadType 5) on a numbered segment neither confirms nor
    renumbers it — the segment is off the system unless a roadway line is near."""
    geo = _segs((1, "84", "Frontage Rd", "I-84", 0.0))
    shs = _shs(("01043AIN084", ON, 1, "A", 5))
    m = routes.route_membership(geo, shs=shs)
    assert m.loc[1, "verdict"] == routes.INRIX_ONLY


def test_the_aadt_layer_decides_only_where_the_shs_cannot():
    """A numbered frontage road with another route's SHS line near (25 m) but not on
    it: the SHS can't say, so the AADT layer is asked."""
    geo = _segs((1, "30", "US-30", "US-30", 0.0))
    shs = _shs(("01010AIN084", CENTRE))                  # I-84, 25 m away
    layer = _layer(("02040AUS030", "FRONTAGE", ON))
    m = routes.route_membership(geo, layer, shs=shs)
    assert m.loc[1, "verdict"] == routes.AGREE and m.loc[1, "source"] == routes.SOURCE_AADT
    assert "AADT layer" in m.loc[1, "reason"]
    # Without the AADT layer it is kept as INRIX numbers it, and flagged.
    alone = routes.route_membership(geo, shs=shs)
    assert alone.loc[1, "verdict"] == routes.UNCONFIRMED
    assert alone.loc[1, "route_number"] == "30"


def test_a_winding_road_is_judged_along_its_length_not_at_one_point():
    """ID-162 above Kamiah: the SHS line lies on a switchback segment, but at the one
    point where they touch the tangents differ. Direction is compared per sample."""
    from shapely.geometry import LineString as LS
    zig = LS([(-116.200, 43.600), (-116.199, 43.601), (-116.200, 43.602),
              (-116.199, 43.603), (-116.200, 43.604)])
    geo = _segs((1, "162", "ID-162", "ID-162", 0.0))
    geo.loc[1, "geometry"] = zig
    shs = itd_layers.shs_frame([{"RouteId": "01950ASH162",
                                 "geometry": LS(list(zig.coords)[::-1])}])
    m = routes.route_membership(geo, shs=shs)
    assert m.loc[1, "verdict"] == routes.AGREE


def test_membership_needs_a_layer():
    with pytest.raises(ValueError, match="needs"):
        routes.route_membership(_segs((1, "12", "Main St", None, 0.0)))


def test_source_round_trips_through_csv(tmp_path):
    geo = _segs((1, None, "Levee Byp", None, 0.0))
    m = routes.route_membership(geo, shs=_shs(("01910AUS012", ON)))
    back = routes.read_membership(routes.write_membership(m, tmp_path / "m.csv"))
    assert back.loc[1, "source"] == routes.SOURCE_SHS and back.loc[1, "routes"] == "12"


def test_a_road_between_both_carriageways_is_not_the_route():
    """Silver Valley Rd: INRIX numbers it 90; the SHS draws I-90 as two carriageways,
    ~17 m and ~30 m away, and the road lies on neither. A carriageway 12 m from its
    line (an alignment offset) is still the route."""
    geo = _segs((1, "90", "Silver Valley Rd", "Silver Valley Rd", 0.0, "Shoshone"),
                (2, "90", "I-90 W", "I-90", FAR, "Shoshone", 1))
    shs = _shs(("01660AIN090", -0.00021), ("01660DIN090", 0.00037),
               ("01660AIN090", FAR - 0.00015), ("01660DIN090", FAR + 0.00060))
    m = routes.route_membership(geo, shs=shs)
    assert m.loc[1, "verdict"] == routes.INRIX_ONLY
    assert "parallel road" in m.loc[1, "reason"]
    assert m.loc[2, "verdict"] == routes.AGREE and m.loc[2, "routes"] == "90"


def test_a_business_line_on_the_segment_outranks_a_mainline_near_it():
    """Rigby's Farnsworth Way: the US-20 Business line lies on it, US-20's carriageway
    runs 21 m away. What is *on* the segment decides."""
    geo = _segs((1, "20", "Farnsworth Way", "Farnsworth Way|US-20-BR", 0.0, "Jefferson"))
    shs = _shs(("02075AUS020", ON, 3), ("02070AUS020", 0.00027))
    m = routes.route_membership(geo, shs=shs)
    assert m.loc[1, "verdict"] == routes.BUSINESS and m.loc[1, "routes"] == "20"
    assert "business loop" in m.loc[1, "reason"]


def _membership_frame(rows):
    """(XDSegID, RoadName, verdict, routes) rows as ``read_membership`` returns them."""
    return pd.DataFrame(rows, columns=["XDSegID", "RoadName", "verdict", "routes"]
                        ).set_index("XDSegID")


def test_a_curated_list_drops_off_system_roads_but_keeps_what_it_is_told_to():
    """Item 49, District 3: Cleveland Blvd (INRIX says I-84 BL, the SHS says local) and
    a road off the system drop out of the hand-curated list; Banks-Lowman is off the
    system too but kept on purpose; ramps and business loops stay; an SHS route
    segment the list lacks is reported, not silently added; an id the membership
    doesn't cover is kept (no evidence either way)."""
    m = _membership_frame([
        (1, "I-84", routes.AGREE, "84"),
        (2, "Cleveland Blvd", routes.INRIX_ONLY, ""),
        (3, "Banks Lowman Hwy", routes.OFF_SYSTEM, ""),
        (4, "I-84 Ramp", routes.RAMP, ""),
        (5, "Garrity Blvd", routes.BUSINESS, "84"),
        (6, "S 16th St", routes.ITD_ONLY, "95"),
        (7, "Some Ramp", routes.RAMP, "84"),
    ])
    rec = routes.reconcile_curated_list([5, 2, 1, 3, 4, 99], m, keep=[3])
    assert rec["kept"] == [5, 1, 3, 4, 99]          # order preserved
    assert rec["dropped"] == [2]
    assert rec["missing"] == [6]                     # not the ramp (7)
    assert routes.no_route_segments(m) == {2, 3}
    # Without the keep, Banks-Lowman would go too.
    assert routes.reconcile_curated_list([1, 3], m)["dropped"] == [3]

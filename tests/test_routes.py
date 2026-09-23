"""Tests for ``inrix_tools.routes`` — state-route membership from ITD's AADT layer
(ROADMAP Item 48)."""
from pathlib import Path

import geopandas as gpd
import pandas as pd
import pytest
from shapely.geometry import LineString

from inrix_tools import routes
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


def test_the_shipped_override_table_loads():
    frame = routes.load_route_overrides(REPO_ROOT / "scripts" / "route_overrides.csv")
    assert "Reisenauer Rd" in set(frame["road_name"])


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
    m = routes.route_membership(geo, aadt.load_aadt(D2_AADT))
    levee = m[m["RoadName"] == "Levee Byp"]
    assert (levee["verdict"] == routes.ITD_ONLY).sum() >= 12
    downtown = m[m["RoadName"].isin(["Main St", "D St"]) & (m["inrix_route"] == 12)]
    assert (downtown["verdict"] == routes.INRIX_ONLY).sum() >= 10

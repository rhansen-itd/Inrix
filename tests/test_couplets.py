"""Tests for inrix_tools.couplets — one-way couplet detection and pairing."""
from __future__ import annotations

import geopandas as gpd
import pandas as pd
from shapely.geometry import LineString

from inrix_tools import couplets


def _couplet_network():
    """Create a synthetic network with a one-way couplet.

    Layout (looking north):
        Seg 4001..4003 (NB, "Main St", XDGroup 100)
        Seg 5001..5003 (SB, "Oak St", XDGroup 200)
    Both carry RoadNumber "95" through Moscow, separated by ~85m laterally.
    """
    LON_MAIN = -116.800
    LON_OAK = -116.799  # ~85m east at this latitude
    LAT_START = 46.730
    DLAT = 0.005  # ~0.35 mi per segment

    rows = []
    # NB chain on Main St (XDGroup 100)
    for i in range(3):
        sid = 4001 + i
        nxt = 4002 + i if i < 2 else None
        prev = 4000 + i if i > 0 else None
        rows.append({
            "XDSegID": sid,
            "PreviousXD": prev,
            "NextXDSegI": nxt,
            "FRC": 2,
            "RoadNumber": "95",
            "RoadName": "Main St",
            "Miles": 0.35,
            "Bearing": "N",
            "SlipRoad": 0,
            "XDGroup": 100,
            "County": "Latah",
            "PostalCode": "Moscow",
            "StartLat": LAT_START + i * DLAT,
            "StartLong": LON_MAIN,
            "EndLat": LAT_START + (i + 1) * DLAT,
            "EndLong": LON_MAIN,
            "geometry": LineString([
                (LON_MAIN, LAT_START + i * DLAT),
                (LON_MAIN, LAT_START + (i + 1) * DLAT),
            ]),
        })

    # SB chain on Oak St (XDGroup 200)
    for i in range(3):
        sid = 5001 + i
        nxt = 5002 + i if i < 2 else None
        prev = 5000 + i if i > 0 else None
        rows.append({
            "XDSegID": sid,
            "PreviousXD": prev,
            "NextXDSegI": nxt,
            "FRC": 2,
            "RoadNumber": "95",
            "RoadName": "Oak St",
            "Miles": 0.35,
            "Bearing": "S",
            "SlipRoad": 0,
            "XDGroup": 200,
            "County": "Latah",
            "PostalCode": "Moscow",
            "StartLat": LAT_START + (3 - i) * DLAT,
            "StartLong": LON_OAK,
            "EndLat": LAT_START + (3 - i - 1) * DLAT,
            "EndLong": LON_OAK,
            "geometry": LineString([
                (LON_OAK, LAT_START + (3 - i) * DLAT),
                (LON_OAK, LAT_START + (3 - i - 1) * DLAT),
            ]),
        })

    return gpd.GeoDataFrame(rows, crs="EPSG:4326")


class TestDetectCouplets:
    def test_basic_couplet_detected(self):
        """A simple NB/SB couplet on different streets sharing a route is detected."""
        net = _couplet_network()
        pairs = couplets.detect_couplets(net, min_length_mi=0.5, max_length_mi=3.0)
        assert len(pairs) >= 1
        pair = pairs[0]
        assert "95" in pair.route_numbers
        assert pair.dir1_street != pair.dir2_street
        assert {pair.dir1_bearing, pair.dir2_bearing} == {"N", "S"}
        assert pair.county == "Latah"
        assert "Moscow" in pair.label

    def test_same_street_not_detected(self):
        """Opposing directions on the SAME street are not couplets."""
        net = _couplet_network()
        net.loc[net["XDGroup"] == 200, "RoadName"] = "Main St"
        pairs = couplets.detect_couplets(net, min_length_mi=0.5)
        assert len(pairs) == 0

    def test_too_short_filtered(self):
        """Chains shorter than min_length_mi are filtered out."""
        net = _couplet_network()
        pairs = couplets.detect_couplets(net, min_length_mi=2.0)
        assert len(pairs) == 0

    def test_known_couplets_registry(self):
        """The KNOWN_COUPLETS registry has expected districts and valid entries."""
        districts = {c["district"] for c in couplets.KNOWN_COUPLETS}
        assert districts == {1, 2, 3, 4, 5, 6}
        assert len(couplets.KNOWN_COUPLETS) >= 12

    def test_registry_holds_no_pair_itd_carries_as_local(self):
        """Item 48: entries whose streets ITD's route layer carries as local roads
        were removed, and must not creep back."""
        cities = {c["city"] for c in couplets.KNOWN_COUPLETS}
        assert not cities & {"Lewiston", "Coeur d'Alene", "Payette", "Caldwell"}

    def test_filter_by_district(self):
        """Filtering by district preserves only matching counties."""
        net = _couplet_network()
        pairs = couplets.detect_couplets(net, min_length_mi=0.5)
        assert len(pairs) >= 1
        d2_pairs = couplets.filter_by_district(pairs, 2)
        assert len(d2_pairs) == len(pairs)
        d1_pairs = couplets.filter_by_district(pairs, 1)
        assert len(d1_pairs) == 0


class TestCoupletCatalogueEntries:
    def test_entries_have_required_fields(self):
        """Catalogue entries generated from a couplet have all required fields."""
        net = _couplet_network()
        pairs = couplets.detect_couplets(net, min_length_mi=0.5)
        assert len(pairs) >= 1
        d1, d2, rc = couplets.couplet_catalogue_entries(pairs[0], net)
        for entry in [d1, d2]:
            assert "id" in entry
            assert "name" in entry
            assert "start_latlon" in entry
            assert "end_latlon" in entry
            assert "description" in entry
            assert "corridor" in entry
            assert "direction" in entry
        assert rc["one_way_couplet"] is True
        assert rc["id"] == d1["corridor"]
        assert rc["id"] == d2["corridor"]


# ─── ROADMAP Item 46: detecting the couplets the registry names ─────

def _grid_network(*, name_a="Front St", name_b="Myrtle St",
                  bearing_a="N", bearing_b="E", route="20", lat=43.616):
    """Two parallel one-way legs ~170 m apart running opposite ways east-west.

    ``bearing_a`` defaults to ``N`` on purpose: that is how XD codes Boise's
    westbound Front St carriageway, and a cardinal-opposition test rejects it.
    """
    LON0 = -116.210
    DLON = 0.0040          # ~0.2 mi per segment at this latitude
    DLAT = 0.0015          # ~170 m apart
    rows = []

    def leg(base, name, bearing, lat_row, reverse, group):
        for i in range(4):
            sid = base + i
            step = -DLON if reverse else DLON
            lon0 = LON0 + (3 - i) * DLON if reverse else LON0 + i * DLON
            rows.append({
                "XDSegID": sid, "PreviousXD": None,
                "NextXDSegI": base + i + 1 if i < 3 else None,
                "FRC": 2, "RoadNumber": route, "RoadName": name, "Miles": 0.2,
                "Bearing": bearing, "SlipRoad": 0, "XDGroup": group,
                "County": "Ada", "PostalCode": "83702",
                "StartLat": lat_row, "StartLong": lon0,
                "EndLat": lat_row, "EndLong": lon0 + step,
                "geometry": LineString([(lon0, lat_row), (lon0 + step, lat_row)]),
            })

    leg(7000, name_a, bearing_a, lat + DLAT, reverse=True, group=700)    # westbound
    leg(8000, name_b, bearing_b, lat, reverse=False, group=800)          # eastbound
    return gpd.GeoDataFrame(rows, crs="EPSG:4326")


class TestStreetKey:
    def test_leading_quadrant_is_stripped(self):
        """E Front St and W Front St are one street either side of the origin."""
        assert couplets.street_key("E Front St") == couplets.street_key("W Front St")

    def test_trailing_quadrant_is_kept(self):
        """2nd Ave N and 2nd Ave S are the Twin Falls US-30 couplet, not one street."""
        assert couplets.street_key("2nd Ave N") != couplets.street_key("2nd Ave S")


class TestGeometricPairing:
    def test_legs_whose_xd_bearing_is_not_opposed_still_pair(self):
        net = _grid_network()          # Front St coded "N", Myrtle St coded "E"
        pairs = couplets.detect_couplets(net, min_length_mi=0.5)
        assert len(pairs) == 1
        assert {pairs[0].dir1_bearing, pairs[0].dir2_bearing} == {"E", "W"}

    def test_a_divided_highway_is_not_a_couplet(self):
        """US-95 N and US-95 S are two carriageways of one road; the trailing
        quadrant there restates the direction of travel instead of naming a side
        of the grid origin."""
        net = _grid_network(name_a="US-95 W", name_b="US-95 E",
                            bearing_a="W", bearing_b="E", route="95")
        assert couplets.detect_couplets(net, min_length_mi=0.5) == []

    def test_a_grid_couplet_on_one_base_name_is_kept(self):
        net = _grid_network(name_a="2nd Ave N", name_b="2nd Ave S",
                            bearing_a="W", bearing_b="E", route="30")
        assert len(couplets.detect_couplets(net, min_length_mi=0.5)) == 1

    def test_a_leg_is_paired_at_most_once(self):
        net = _grid_network()
        pairs = couplets.detect_couplets(net, min_length_mi=0.5)
        used = [s for p in pairs for s in p.dir1_segment_ids + p.dir2_segment_ids]
        assert len(used) == len(set(used))


class TestStreetRuns:
    def test_a_couplet_leg_is_found_inside_a_longer_carriageway(self):
        """Boise's westbound US-20/26 runs up Broadway Ave before turning onto
        Front St; filtering whole XDGroups by length threw the couplet out."""
        net = _grid_network()
        tail = net[net["XDGroup"] == 700].iloc[[0]].copy()
        tail["XDSegID"] = 6999
        tail["RoadName"] = "Broadway Ave"
        tail["NextXDSegI"] = 7000
        tail["Miles"] = 3.0
        net = gpd.GeoDataFrame(pd.concat([tail, net], ignore_index=True), crs=net.crs)
        pairs = couplets.detect_couplets(net, min_length_mi=0.5, max_length_mi=1.5)
        assert len(pairs) == 1
        assert 6999 not in pairs[0].dir1_segment_ids + pairs[0].dir2_segment_ids


class TestMatchKnownCouplets:
    def test_a_row_per_registry_entry(self):
        table = couplets.match_known_couplets([])
        assert len(table) == len(couplets.KNOWN_COUPLETS)
        assert set(table["match_kind"]) == {"none"}

    def test_both_street_names_recognised(self):
        net = _grid_network(name_a="W Front St", name_b="W Myrtle St")
        pairs = couplets.detect_couplets(net, min_length_mi=0.5)
        table = couplets.match_known_couplets(pairs, district=3)
        boise = table[table["city"] == "Boise"].iloc[0]
        assert boise["match_kind"] == "streets"
        assert boise["detected_miles"] > 0

    def test_a_route_only_match_is_dropped_when_a_stronger_row_owns_the_pair(self):
        """Mountain Home is I-84B in a district that also holds Nampa's couplet;
        without the downgrade it reports as found against it."""
        net = _grid_network(name_a="3rd St S", name_b="2nd St S",
                            bearing_a="W", bearing_b="E", route="84")
        pairs = couplets.detect_couplets(net, min_length_mi=0.5)
        table = couplets.match_known_couplets(pairs, district=3).set_index("city")
        assert table.loc["Nampa", "match_kind"] == "streets"
        assert table.loc["Mountain Home", "match_kind"] == "none"

    def test_registry_route_numbers(self):
        assert couplets.registry_route_numbers("US-20/26") == {"20", "26"}
        assert couplets.registry_route_numbers("I-84B") == {"84"}


class TestCoupletEntryNaming:
    def test_ids_name_the_streets_not_the_zip(self):
        net = _grid_network()
        pair = couplets.detect_couplets(net, min_length_mi=0.5)[0]
        d1, d2, rc = couplets.couplet_catalogue_entries(pair, net)
        assert "83702" not in rc["id"]
        assert "front-st" in rc["id"] and "myrtle-st" in rc["id"]
        assert d1["corridor"] == rc["id"] and d2["corridor"] == rc["id"]

    def test_the_band_is_read_off_the_network_not_the_digit_count(self):
        net = _grid_network(route="55")
        net["RoadName"] = net["RoadName"].where(net["XDSegID"] != 7000, "ID-55")
        pair = couplets.detect_couplets(net, min_length_mi=0.5)[0]
        _, _, rc = couplets.couplet_catalogue_entries(pair, net)
        assert rc["name"].startswith("SH-55")

    def test_descriptions_survive_the_catalogue_parser(self):
        from inrix_tools import corridors
        net = _grid_network()
        pair = couplets.detect_couplets(net, min_length_mi=0.5)[0]
        d1, d2, rc = couplets.couplet_catalogue_entries(pair, net)
        cat = {"corridors": [d1, d2], "reporting_corridors": [rc]}
        assert len(corridors.parse_catalogue(cat)) == 2
        assert corridors.parse_reporting_corridors(cat)[0].one_way_couplet is True


def _pair(s1, b1, s2, b2, county="Nez Perce"):
    return couplets.CoupletPair(
        dir1_segment_ids=(1,), dir2_segment_ids=(2,), dir1_bearing=b1, dir2_bearing=b2,
        dir1_street=s1, dir2_street=s2, route_numbers=("12",), total_miles=0.95,
        mean_lateral_sep_m=223.0, county=county, postal_code="83501")


def test_a_street_pair_found_in_both_directions_is_not_a_couplet():
    """Item 49, Lewiston: 'Us Highway 12' W + Levee Byp E and 'Us Highway 12' E +
    Levee Byp W. Each street carries both directions, so neither pairing is a
    couplet. A real one-way pair, and the same streets in another county, survive."""
    lewiston = [_pair("Us Highway 12", "W", "Levee Byp", "E"),
                _pair("Us Highway 12", "E", "Levee Byp", "W")]
    real = _pair("E Main St", "E", "D St", "W")
    elsewhere = _pair("Us Highway 12", "W", "Levee Byp", "E", county="Idaho")
    kept = couplets.drop_mirrored_pairs([*lewiston, real, elsewhere])
    assert kept == [real, elsewhere]
    # Leg order and the quadrant prefix do not hide the mirror.
    swapped = [_pair("Us Highway 12", "W", "Levee Byp", "E"),
               _pair("W Levee Byp", "W", "Us Highway 12", "E")]
    assert couplets.drop_mirrored_pairs(swapped) == []


def test_a_leg_with_its_own_streets_opposing_carriageway_on_it_is_not_a_couplet():
    """Item 49, SH-77 at Elba: Elba-Almo Rd westbound and Elba-Almo Hwy eastbound were
    paired, two consecutive pieces of one two-way road. Each leg has an opposing
    segment of its own street lying on it, so the pair goes. XD's ``Bearing`` label
    (``S`` on a westbound segment) is ignored; the geometry decides. A real one-way
    pair a block apart survives."""
    def seg(sid, name, coords, bearing):
        return {"XDSegID": sid, "RoadName": name, "Bearing": bearing,
                "geometry": LineString(coords)}

    y0, y1 = 42.2700, 42.2725                        # ~280 m apart, north-south
    net = gpd.GeoDataFrame([
        seg(1, "Elba-Almo Hwy", [(-113.53, y0), (-113.52, y0)], "E"),
        seg(2, "Elba-Almo Hwy", [(-113.52, y0), (-113.53, y0)], "S"),  # the twin
        seg(3, "Elba-Almo Rd", [(-113.52, y1), (-113.53, y1)], "W"),
        seg(4, "Elba-Almo Rd", [(-113.53, y1), (-113.52, y1)], "E"),   # the twin
        seg(5, "1st Ave", [(-113.53, y0 + 0.01), (-113.52, y0 + 0.01)], "E"),
        seg(6, "2nd Ave", [(-113.52, y1 + 0.01), (-113.53, y1 + 0.01)], "W"),
    ], crs="EPSG:4326")

    def pair(a, sa, b, sb):
        return couplets.CoupletPair(
            dir1_segment_ids=(a,), dir2_segment_ids=(b,), dir1_bearing="E",
            dir2_bearing="W", dir1_street=sa, dir2_street=sb, route_numbers=("77",),
            total_miles=0.5, mean_lateral_sep_m=280.0, county="Cassia", postal_code="")

    elba = pair(1, "Elba-Almo Hwy", 3, "Elba-Almo Rd")
    real = pair(5, "1st Ave", 6, "2nd Ave")
    assert couplets.drop_two_way_legs([elba, real], net) == [real]
    # One two-way leg is enough: a one-way street paired with a two-way one goes too.
    half = pair(5, "1st Ave", 3, "Elba-Almo Rd")
    assert couplets.drop_two_way_legs([half], net) == []

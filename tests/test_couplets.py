"""Tests for inrix_tools.couplets — one-way couplet detection and pairing."""
from __future__ import annotations

import geopandas as gpd
import numpy as np
import pandas as pd
import pytest
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
        assert len(couplets.KNOWN_COUPLETS) >= 15

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

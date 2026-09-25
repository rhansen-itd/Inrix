"""Tests for inrix_tools.hard_stops and the stops' effect on the chain walk (Item 60)."""
from __future__ import annotations

from pathlib import Path

import geopandas as gpd
import pytest

from inrix_tools import extents, hard_stops
from tests.test_extents import _baseline, _moscow_sh8, _seg_row, _two_route_network

HEADER = "district,route,direction,lat,lon,xd_seg_id,side,note\n"


def _table(tmp_path, *rows) -> Path:
    path = tmp_path / "stops.csv"
    path.write_text("# a comment line\n" + HEADER + "".join(r + "\n" for r in rows))
    return path


def _end(net, sid):
    """(lat, lon) of where segment ``sid`` ends."""
    g = net.set_index("XDSegID").loc[sid, "geometry"]
    lon, lat = g.coords[-1]
    return lat, lon


# ─── the table ──────────────────────────────────────────────────────

class TestReadHardStops:
    def test_a_valid_table_reads(self, tmp_path):
        t = hard_stops.read_hard_stops(_table(
            tmp_path,
            '3,55,,43.69,-116.35,,,"Eagle Rd ends at SH-44"',
            "2,095,NB,,,1234,After,couplet end"))
        assert list(t["row"]) == [1, 2]
        assert list(t["route"]) == ["55", "95"]           # zero padding dropped
        assert list(t["direction"]) == ["", "N"]
        assert t.loc[1, "side"] == "after"
        assert t.loc[1, "xd_seg_id"] == 1234
        assert t.loc[0, "lat"] == pytest.approx(43.69)

    @pytest.mark.parametrize("row, why", [
        ("3,55,,43.69,-116.35,,,", "note"),
        ("7,55,,43.69,-116.35,,,x", "district"),
        ("3,SH-55,,43.69,-116.35,,,x", "route"),
        ("3,55,northbound,43.69,-116.35,,,x", "direction"),
        ("3,55,,43.69,-116.35,99,before,x", "not both"),
        ("3,55,,,,,,x", "lat/lon or xd_seg_id"),
        ("3,55,,-116.35,43.69,,,x", "out of range"),
        ("3,55,,,,99,middle,x", "side"),
        ("3,55,,43.69,-116.35,,before,x", "side goes with"),
    ])
    def test_a_bad_row_is_an_error_naming_it(self, tmp_path, row, why):
        with pytest.raises(hard_stops.HardStopError, match=why) as err:
            hard_stops.read_hard_stops(_table(tmp_path, "3,55,,43.69,-116.35,,,fine", row))
        assert "row(s) [2]" in str(err.value)

    def test_a_missing_column_is_an_error(self, tmp_path):
        path = tmp_path / "s.csv"
        path.write_text("district,route,lat,lon,note\n3,55,43.6,-116.3,x\n")
        with pytest.raises(hard_stops.HardStopError, match="lacks columns"):
            hard_stops.read_hard_stops(path)


# ─── resolution ─────────────────────────────────────────────────────

class TestResolve:
    def test_a_blank_direction_takes_both_carriageways(self, tmp_path):
        """US-95 NB 100-105 and SB 200-205, ~60 m apart: a point between the two
        carriageways at 46.73 resolves to one boundary on each."""
        net = _two_route_network()
        lat, lon = _end(net, 102)
        t = hard_stops.read_hard_stops(_table(tmp_path, f"1,95,,{lat},{lon + 0.0004},,,x"))
        r = hard_stops.resolve_hard_stops(t, net, 1)
        assert set(zip(r["from_seg"], r["to_seg"])) == {(102, 103), (202, 203)}

    def test_a_direction_takes_one(self, tmp_path):
        net = _two_route_network()
        lat, lon = _end(net, 102)
        t = hard_stops.read_hard_stops(_table(tmp_path, f"1,95,SB,{lat},{lon},,,x"))
        r = hard_stops.resolve_hard_stops(t, net, 1)
        assert list(zip(r["from_seg"], r["to_seg"])) == [(202, 203)]
        assert r.loc[0, "distance_m"] < 100

    @pytest.mark.parametrize("side, pair", [("before", (102, 103)), ("after", (103, 104))])
    def test_a_segment_and_side(self, tmp_path, side, pair):
        t = hard_stops.read_hard_stops(_table(tmp_path, f"1,95,,,,103,{side},x"))
        r = hard_stops.resolve_hard_stops(t, _two_route_network(), 1)
        assert list(zip(r["from_seg"], r["to_seg"])) == [pair]

    def test_other_districts_rows_are_ignored(self, tmp_path):
        t = hard_stops.read_hard_stops(_table(tmp_path, "4,95,,,,103,before,x"))
        assert hard_stops.resolve_hard_stops(t, _two_route_network(), 1).empty

    def test_an_unresolvable_row_is_an_error_listing_it(self, tmp_path):
        t = hard_stops.read_hard_stops(_table(
            tmp_path, "1,95,,,,103,before,fine",
            "1,95,,45.0,-115.0,,,far from any boundary",
            "1,53,,,,999,after,no such segment"))
        with pytest.raises(hard_stops.HardStopError) as err:
            hard_stops.resolve_hard_stops(t, _two_route_network(), 1)
        msg = str(err.value)
        assert "2 hard stop(s)" in msg and "row 2" in msg and "row 3" in msg
        assert "row 1 " not in msg

    def test_a_boundary_must_carry_the_route_on_both_sides(self, tmp_path):
        """At 3rd & Jackson SH-8 turns off 3rd St (2) onto the couplet leg (4), which
        US-95 reaches from the north (7). A US-95 stop at that corner cuts US-95's own
        boundary 7 -> 4, not SH-8's turn: segment 2 does not carry route 95."""
        net = _moscow_sh8()
        net = gpd.GeoDataFrame(
            [*net.to_dict("records"),
             _seg_row(7, 4, "95", "S Jackson St", (-117.003, 46.738), (-117.003, 46.732),
                      rid="01540DUS095", road_list="S Jackson St|US-95", bearing="S",
                      group=2)], crs="EPSG:4326")
        lat, lon = _end(net, 2)
        t = hard_stops.read_hard_stops(_table(tmp_path, f"1,95,,{lat},{lon},,,x"))
        r = hard_stops.resolve_hard_stops(t, net, 1)
        assert list(zip(r["from_seg"], r["to_seg"])) == [(7, 4)]

    def test_every_way_on_from_the_same_place_is_taken(self, tmp_path):
        """SH-8 leaves segment 2 two ways: down the couplet (the chain) and on along
        the 3rd St stub (the link). Cutting only the turn would hand the walk the stub."""
        net = _moscow_sh8()
        lat, lon = _end(net, 2)
        t = hard_stops.read_hard_stops(_table(tmp_path, f"1,8,EB,{lat},{lon},,,x"))
        r = hard_stops.resolve_hard_stops(t, net, 1)
        assert set(zip(r["from_seg"], r["to_seg"], r["on_chain"])) == {
            (2, 4, True), (2, 3, False)}


# ─── the chain walk ─────────────────────────────────────────────────

class TestChainsAtStops:
    def test_a_stop_cuts_a_chain(self):
        chains = extents.enumerate_mainline_chains(
            _two_route_network(), min_miles=0.1, hard_stops={(102, 103): "here"})
        nb = sorted(c.segment_ids for c in chains if c.bearing == "N")
        assert nb == [(100, 101, 102), (103, 104, 105)]
        first = next(c for c in chains if c.segment_ids[0] == 100)
        assert first.stop_at("end") == {"at": "end", "from": 102, "to": 103,
                                         "reason": "here"}
        assert first.stop_at("start") is None
        second = next(c for c in chains if c.segment_ids[0] == 103)
        assert second.stop_at("start")["from"] == 102

    def test_an_end_on_two_stops_records_both(self):
        """Moscow's couplet starts past US-95's stop and SH-8's, which share a head."""
        chains = extents.enumerate_mainline_chains(
            _two_route_network(), min_miles=0.1,
            hard_stops={(102, 103): "US-95", (900, 103): "SH-8"})
        second = next(c for c in chains if c.segment_ids[0] == 103)
        assert len(second.stops) == 2
        assert second.stop_at("start")["reason"] == "US-95 | SH-8"

    def test_a_one_direction_stop_leaves_the_other(self):
        chains = extents.enumerate_mainline_chains(
            _two_route_network(), min_miles=0.1, hard_stops={(102, 103): "NB only"})
        sb = [c for c in chains if c.bearing == "S"]
        assert [c.segment_ids for c in sb] == [(200, 201, 202, 203, 204, 205)]
        assert not sb[0].stops

    def test_a_both_way_row_cuts_both(self, tmp_path):
        net = _two_route_network()
        lat, lon = _end(net, 102)
        t = hard_stops.read_hard_stops(_table(tmp_path, f"1,95,,{lat},{lon + 0.0004},,,x"))
        b = hard_stops.stop_boundaries(hard_stops.resolve_hard_stops(t, net, 1))
        chains = extents.enumerate_mainline_chains(net, min_miles=0.1, hard_stops=b)
        assert sum(1 for c in chains if c.route_number == "95") == 4

    def test_a_junction_join_does_not_bridge_a_stop(self):
        """Payette's US-95 joins 11 -> 12 where the route turns off the link."""
        rows = [
            _seg_row(10, 11, "95", "US-95", (-116.93, 44.02), (-116.93, 44.03), bearing="N"),
            _seg_row(11, 90, "95", "US-95", (-116.93, 44.03), (-116.93, 44.04), bearing="N"),
            _seg_row(90, None, "", "S Main St", (-116.93, 44.04), (-116.93, 44.05), bearing="N"),
            _seg_row(12, 13, "95", "95 N", (-116.93, 44.04), (-116.92, 44.041), bearing="E",
                     group=2),
            _seg_row(13, None, "95", "S 16th St", (-116.92, 44.041), (-116.92, 44.06),
                     bearing="N", group=2),
        ]
        net = gpd.GeoDataFrame(rows, crs="EPSG:4326")
        chains = extents.enumerate_mainline_chains(net, min_miles=0.1,
                                                   hard_stops={(11, 12): "x"})
        assert sorted(c.segment_ids for c in chains) == [(10, 11), (12, 13)]
        assert all(not c.joins for c in chains)

    def test_a_stub_join_does_not_bridge_a_stop_and_the_stub_is_not_taken(self):
        net = _moscow_sh8()
        chains = extents.enumerate_mainline_chains(
            net, min_miles=0.1, hard_stops={(2, 4): "x", (2, 3): "x"})
        assert not any(2 in c.segment_ids and (4 in c.segment_ids or 3 in c.segment_ids)
                       for c in chains)
        assert any(c.segment_ids == (1, 2) for c in chains)
        assert any(c.segment_ids[:1] == (4,) for c in chains)

    def test_a_renumbering_merge_does_not_bridge_a_stop(self):
        rows = [
            _seg_row(30, 31, "91", "S Yellowstone Hwy", (-112.06, 43.44), (-112.06, 43.45),
                     bearing="N"),
            _seg_row(31, 32, "91", "S Yellowstone Hwy", (-112.06, 43.45), (-112.06, 43.46),
                     bearing="N"),
            _seg_row(32, 33, "15/26", "S Yellowstone Hwy", (-112.06, 43.46), (-112.06, 43.47),
                     bearing="N", group=2),
            _seg_row(33, None, "15/26", "N Yellowstone Hwy", (-112.06, 43.47),
                     (-112.06, 43.48), bearing="N", group=2),
        ]
        net = gpd.GeoDataFrame(rows, crs="EPSG:4326")
        chains = extents.enumerate_mainline_chains(net, min_miles=0.1,
                                                   hard_stops={(31, 32): "x"})
        assert not any(31 in c.segment_ids and 32 in c.segment_ids for c in chains)
        assert all(j["kind"] != "renumbering" for c in chains for j in c.joins)

    def test_a_section_between_two_stops_is_kept_below_the_chain_minimum(self):
        """The Moscow couplet is 0.65 mi, under the 1-mile stub floor, but the owner
        declared it a section."""
        stops = {(101, 102): "a", (102, 103): "b"}
        chains = extents.enumerate_mainline_chains(_two_route_network(), min_miles=1.0,
                                                   hard_stops=stops)
        assert (102,) in [c.segment_ids for c in chains]
        # A short piece with a stop at one end only is still a stub.
        chains = extents.enumerate_mainline_chains(_two_route_network(), min_miles=1.0,
                                                   hard_stops={(104, 105): "a"})
        assert (105,) not in [c.segment_ids for c in chains]


class TestCatalogueAtStops:
    CONGESTED = {101: 2.0, 102: 2.0, 103: 2.0, 104: 2.0}

    def _catalogue(self, stops):
        net = _two_route_network()
        ids = list(net["XDSegID"])
        audit: list[dict] = []
        cat = extents.generate_catalogue(net, _baseline(ids, self.CONGESTED),
                                         observed=set(ids), audit=audit,
                                         hard_stops=stops)
        return cat, audit

    def test_no_core_or_tier_crosses_a_stop(self):
        stops = {(102, 103): "owner terminus"}
        cat, audit = self._catalogue(stops)
        assert cat["corridors"]
        for e in cat["corridors"]:
            assert not hard_stops.crossings(e["_segment_ids"], stops), e["id"]
        assert any("a manual hard stop (owner terminus)" in e["description"]
                   for e in cat["corridors"])
        assert any("owner terminus" in r.get("chain_stops", "") for r in audit)
        assert cat["_generated"]["hard_stops"] == ["102|103: owner terminus"]

    def test_without_stops_the_core_runs_through(self):
        cat, _ = self._catalogue(None)
        cores = [e for e in cat["corridors"] if e["_tier"] == "core"]
        assert any({102, 103} <= set(e["_segment_ids"]) for e in cores)
        assert "hard_stops" not in cat["_generated"]


def test_crossings_lists_each_step_across():
    stops = {(2, 3): "a", (5, 6): "b"}
    out = hard_stops.crossings([1, 2, 3, 4, 5, 6], stops)
    assert [(x["index"], x["reason"]) for x in out] == [(2, "a"), (5, "b")]
    assert hard_stops.crossings([3, 2], stops) == []


def test_stop_boundaries_joins_two_rows_on_one_boundary():
    import pandas as pd
    r = pd.DataFrame({"from_seg": [1, 1], "to_seg": [2, 2], "route": ["95", "8"],
                      "note": ["x", "y"]})
    assert hard_stops.stop_boundaries(r) == {(1, 2): "route 95: x | route 8: y"}


# ─── the seed rows on the real networks ─────────────────────────────

SEED = Path("scripts/corridor_hard_stops.csv")
_REAL = all(Path(p).exists() for p in (
    "geometry_cache/d2_network.geoparquet", "geometry_cache/d3_network.geoparquet",
    "out/highways/route_membership/d2_route_membership.csv",
    "out/highways/route_membership/d3_route_membership.csv", "SHS_Primary.zip"))


@pytest.mark.skipif(not _REAL, reason="district networks / membership / SHS absent")
@pytest.mark.parametrize("district, expected", [
    (3, {(448695754, 474858973), (474858976, 448695751),      # Eagle Rd | SH-44
         (1187516231, 448697675),                              # Broadway | Front
         (448697683, 1187569970), (448697683, 440882513),      # Front | Connector
         (1187558503, 1187347115)}),                           # EB Connector | last seg
    (2, {(386068712, 124121437), (124404144, 385666846),      # US-95 S | couplet
         (1236886984, 1236886995), (1236973782, 386047403),    # couplet | US-95 N
         (1236966046, 124121437), (124404144, 1236966035),     # couplet | SH-8 E
         (386047415, 448932363),                               # couplet | SH-8 W (WB)
         (1236903538, 386047404), (1236903538, 448932361)}),   # SH-8 W | couplet (EB)
])
def test_the_seed_rows_resolve_on_the_real_networks(district, expected):
    from scripts.build_statewide_catalogues import load_district
    net, _ = load_district(district, aadt_source=None, aadt_year=0, shs="SHS_Primary.zip")
    r = hard_stops.load_hard_stops(SEED, net, district)
    assert set(zip(r["from_seg"], r["to_seg"])) == expected
    b = hard_stops.stop_boundaries(r)
    for c in extents.enumerate_mainline_chains(net, hard_stops=b):
        assert not hard_stops.crossings(c.segment_ids, b)


# ─── scope (Item 61) ────────────────────────────────────────────────

def test_scope_defaults_to_both_and_is_validated(tmp_path):
    t = hard_stops.read_hard_stops(_table(tmp_path, "3,55,,43.69,-116.35,,,x"))
    assert list(t["scope"]) == ["both"]
    path = tmp_path / "scoped.csv"
    path.write_text(HEADER.strip() + ",scope\n"
                    "3,55,,43.69,-116.35,,,x,Stations\n3,55,,43.60,-116.35,,,y,\n")
    assert list(hard_stops.read_hard_stops(path)["scope"]) == ["stations", "both"]
    path.write_text(HEADER.strip() + ",scope\n3,55,,43.69,-116.35,,,x,counts\n")
    with pytest.raises(hard_stops.HardStopError, match="scope"):
        hard_stops.read_hard_stops(path)


def test_stop_boundaries_keeps_the_rows_for_its_use():
    import pandas as pd
    r = pd.DataFrame({"from_seg": [1, 3, 5], "to_seg": [2, 4, 6], "route": ["55"] * 3,
                      "note": ["both", "count only", "corridor only"],
                      "scope": ["both", "stations", "corridors"]})
    assert set(hard_stops.stop_boundaries(r)) == {(1, 2), (5, 6)}
    assert set(hard_stops.stop_boundaries(r, use="stations")) == {(1, 2), (3, 4)}
    with pytest.raises(ValueError, match="use"):
        hard_stops.stop_boundaries(r, use="both")


def test_a_resolved_row_carries_its_scope(tmp_path):
    net = _two_route_network()
    lat, lon = _end(net, 102)
    path = tmp_path / "scoped.csv"
    path.write_text(HEADER.strip() + f",scope\n1,95,NB,{lat},{lon},,,x,stations\n")
    r = hard_stops.resolve_hard_stops(hard_stops.read_hard_stops(path), net, 1)
    assert list(r["scope"]) == ["stations"]
    assert hard_stops.stop_boundaries(r) == {}

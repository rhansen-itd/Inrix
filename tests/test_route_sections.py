"""Tests for inrix_tools.route_sections and the Item 61 station reach."""
from __future__ import annotations

from pathlib import Path

import geopandas as gpd
import pandas as pd
import pytest

from inrix_tools import itd_layers, route_sections as rs
from tests.test_extents import _seg_row

STATE, REGIONAL, INTERSTATE = (itd_layers.TIER_RANK[t]
                               for t in ("State", "Regional", "Interstate"))
LON = -116.35


def _road(first, n, *, step=0.01, lat0=43.60, route="55", name="ID-55",
          rid="01990ASH055", routes=None):
    """``n`` northbound segments of ``route`` up lon :data:`LON`, ids ``first``…"""
    rows = []
    for i in range(n):
        sid = first + i
        rows.append(_seg_row(sid, sid + 1 if i < n - 1 else None,
                             (routes or {}).get(sid, route), name,
                             (LON, lat0 + step * i), (LON, lat0 + step * (i + 1)),
                             rid=rid, bearing="N"))
    return rows


def _cross(first, lat, route, name, rid, *, lon=LON, half=0.01):
    """Two eastbound segments of ``route`` meeting the road at (``lat``, ``lon``)."""
    return [_seg_row(first, first + 1, route, name, (lon - half, lat), (lon, lat), rid=rid),
            _seg_row(first + 1, None, route, name, (lon, lat), (lon + half, lat), rid=rid)]


def _net(rows):
    return gpd.GeoDataFrame(rows, geometry="geometry", crs="EPSG:4326")


def _tiers(net, overrides=None):
    """Tier rank by route: 55/20 State, 84/184 Interstate, 16 Regional."""
    by_route = {"55": STATE, "20": STATE, "30": STATE, "84": INTERSTATE,
                "184": INTERSTATE, "16": REGIONAL}
    t = pd.Series([float(by_route[str(r).split("/")[0]]) for r in net["itd_routes"]],
                  index=net["XDSegID"].astype("int64"))
    for sid, rank in (overrides or {}).items():
        t[sid] = float(rank)
    return t


def _eagle():
    """SH-55 NB 1..10 (0.69 mi each) crossed by I-84 inside segment 1 (overpass, 3/4
    of the way along), US-20 at the 3|4 boundary and a Regional route at 6|7."""
    return _net(_road(1, 10)
                + _cross(401, 43.6075, "84", "I-84", "01010AIN084")
                + _cross(101, 43.63, "20", "US-20", "02000AUS020")
                + _cross(161, 43.66, "16", "ID-16", "02100ASH016"))


def _main(sections, route="55"):
    return [s for s in sections if s.route == route and s.direction == "NB"]


# ─── breaks ─────────────────────────────────────────────────────────

def test_breaks_at_same_and_higher_tier_junctions_not_lower():
    net = _eagle()
    secs = _main(rs.trace_sections(net, tiers=_tiers(net)))
    assert [s.segment_ids for s in secs] == [(1,), (2, 3), (4, 5, 6, 7, 8, 9, 10)]
    assert secs[0].end["kind"] == rs.BREAK_JUNCTION
    assert "I-84 (Interstate) meets SH-55 (State)" in secs[0].end["detail"]
    assert "US-20 (State)" in secs[1].end["detail"]
    assert secs[2].start == secs[1].end
    assert secs[2].end["kind"] == rs.BREAK_ROUTE_END            # no break at the Regional


def test_the_higher_route_does_not_break_at_a_lower_one():
    net = _eagle()
    secs = rs.trace_sections(net, tiers=_tiers(net))
    i84 = [s for s in secs if s.route == "84"]
    assert [s.segment_ids for s in i84] == [(401, 402)]           # SH-55 is State


def test_a_tier_change_without_a_junction_is_not_a_break():
    net = _eagle()
    secs = _main(rs.trace_sections(net, tiers=_tiers(net, {8: REGIONAL, 9: REGIONAL,
                                                            10: REGIONAL})))
    assert secs[-1].segment_ids == (4, 5, 6, 7, 8, 9, 10)


def test_the_section_route_reads_its_lower_tier_at_a_junction():
    """Where SH-55 turns Regional at the US-20 junction, US-20 (State) outranks it."""
    net = _eagle()
    secs = _main(rs.trace_sections(net, tiers=_tiers(net, {s: REGIONAL for s in range(4, 11)})))
    assert (2, 3) in [s.segment_ids for s in secs]


def test_without_tiers_only_stops_and_route_ends_break():
    secs = _main(rs.trace_sections(_eagle()))
    assert [s.segment_ids for s in secs] == [tuple(range(1, 11))]


def test_untiered_segments_take_the_nearest_tier_along_the_run():
    net = _eagle()
    t = _tiers(net).drop([3, 4])                                   # the junction itself
    secs = _main(rs.trace_sections(net, tiers=t))
    assert (2, 3) in [s.segment_ids for s in secs]


def test_a_hard_stop_ends_a_section():
    net = _eagle()
    secs = _main(rs.trace_sections(net, tiers=_tiers(net),
                                   hard_stops={(8, 9): "route 55: owner"}))
    assert [s.segment_ids for s in secs][-2:] == [(4, 5, 6, 7, 8), (9, 10)]
    assert secs[-2].end == {"kind": rs.BREAK_HARD_STOP, "detail": "route 55: owner"}
    assert secs[-1].start["kind"] == rs.BREAK_HARD_STOP


def test_one_interchange_is_one_break():
    """A system interchange touches the mainline at its diverge and its merge, 0.2 mi
    apart: one break, not a stranded one-segment section between them."""
    road = _road(1, 8, step=0.003, route="84", name="I-84", rid="01010AIN084")
    wye = [_seg_row(501, None, "184", "I-184", (LON - 0.01, 43.609), (LON, 43.609),
                    rid="02410AIN184"),
           _seg_row(502, None, "184", "I-184", (LON, 43.612), (LON + 0.01, 43.612),
                    rid="02410AIN184")]
    net = _net(road + wye)
    secs = _main(rs.trace_sections(net, tiers=_tiers(net)), route="84")
    assert [s.segment_ids for s in secs] == [(1, 2, 3), (4, 5, 6, 7, 8)]


def test_a_concurrency_ending_is_a_junction_with_that_route():
    """US-30 (State) runs on SH-55's segments 5-6, arriving from the west and leaving
    east: SH-55 breaks where it joins and where it leaves."""
    road = _road(1, 8, routes={5: "55/30", 6: "55/30"})
    lat_in, lat_out = 43.64, 43.66
    thirty = [_seg_row(301, None, "30", "US-30", (LON - 0.01, lat_in), (LON, lat_in),
                       rid="00300AUS030"),
              _seg_row(302, None, "30", "US-30", (LON, lat_out), (LON + 0.01, lat_out),
                       rid="00300AUS030")]
    net = _net(road + thirty)
    secs = _main(rs.trace_sections(net, tiers=_tiers(net)))
    assert [s.segment_ids for s in secs] == [(1, 2, 3, 4), (5, 6), (7, 8)]
    assert "US-30 (State)" in secs[0].end["detail"]


def test_a_business_loop_is_its_own_route_key():
    net = _net(_road(1, 4, route="84", name="I-84-BL", rid="01020AIN084"))
    secs = rs.trace_sections(net, business=[1, 2, 3, 4])
    assert [s.route for s in secs] == ["84 BL"] and secs[0].number == "84"


def test_sections_frame_and_index():
    net = _eagle()
    secs = rs.trace_sections(net, tiers=_tiers(net))
    fr = rs.sections_frame(secs)
    assert list(fr.columns) == list(rs.SECTION_COLUMNS)
    assert fr["n_segments"].sum() == sum(len(s.segment_ids) for s in secs)
    idx = rs.section_index(secs)
    assert [s.section_id for s in idx[5]] == ["55-NB-4"]


# ─── the real network (owner's Eagle Rd and I-84 cases) ─────────────

_REAL = all(Path(p).exists() for p in (
    "geometry_cache/d3_network.geoparquet", "scripts/d3_link_repairs.csv",
    "out/highways/route_membership/d3_route_membership.csv", "SHS_Primary.zip",
    "Highway Tier.geojson", "out/count_profiles/count_stations.csv"))


@pytest.mark.skipif(not _REAL, reason="D3 network / membership / SHS / tiers / counts absent")
def test_d3_eagle_rd_and_i84_station_reach():
    from inrix_tools import corridors, profile_assignment as pa, routes
    from scripts.run_district_screening import station_sections

    net = gpd.read_parquet("geometry_cache/d3_network.geoparquet")
    mem = "out/highways/route_membership/d3_route_membership.csv"
    ctx = pa.segment_context(net.drop(columns="geometry"), routes.read_membership(mem))
    secs, tiers = station_sections(
        net, repairs=corridors.load_link_repairs("scripts/d3_link_repairs.csv"),
        membership_path=mem, shs="SHS_Primary.zip", tiers_source="Highway Tier.geojson",
        hard_stops_path="scripts/corridor_hard_stops.csv", district=3,
        business=ctx.index[ctx["business"]])
    stations = pd.read_csv("out/count_profiles/count_stations.csv",
                           dtype={"station_id": str, "route": str})
    r = pa.station_rule(ctx, stations, sections=secs)
    rep = r.attrs["stations"].set_index(["station_id", "direction"])
    # Eagle Rd: both ATRs south of Chinden share I-84 -> Chinden, and stop there.
    for sid in ("00330", "00275"):
        row = rep.loc[(sid, "NB")]
        assert "I-84" in row["section_from"] and "US-20" in row["section_to"]
    assert rep.loc[("00330", "NB"), "section_id"] == rep.loc[("00275", "NB"), "section_id"]
    # Every I-84 mainline segment on the route carries a station curve.
    on_route = [s for s in ctx.index if "84" in ctx.at[s, "routes"]
                and str(ctx.at[s, "itd_route_id"] or "").endswith("IN084")
                and not ctx.at[s, "business"]]
    assert len(on_route) > 400
    assert r.loc[on_route, "curve_id"].notna().all()
    assert tiers["tier"].notna().mean() > 0.99

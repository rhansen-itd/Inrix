"""Tests for inrix_tools.geometry (ROADMAP Item 8)."""
import json
from pathlib import Path

import geopandas as gpd
import pandas as pd
import pytest
from shapely.geometry import LineString

from inrix_tools import geometry, io

REPO_ROOT = Path(__file__).resolve().parents[1]
XD_ZIP = REPO_ROOT / "USA_Idaho_shapefile.zip"
MYRTLE_ZIP = REPO_ROOT / "Myrtle_2026-02-01_to_2026-07-16_5_min_part_1.zip"
_HAVE_REAL = XD_ZIP.exists() and MYRTLE_ZIP.exists()


# --- synthetic layer (no shapefile needed) ----------------------------------
def _toy_network():
    """Two XD segments with real (multi-vertex) geometry and a downstream link."""
    return gpd.GeoDataFrame(
        {
            "XDSegID": pd.array([1001, 1002], dtype="Int64"),
            "NextXDSegI": pd.array([1002, pd.NA], dtype="Int64"),
            "PreviousXD": pd.array([pd.NA, 1001], dtype="Int64"),
            "geometry": [
                LineString([(-116.20, 43.61), (-116.20, 43.62), (-116.20, 43.63)]),
                LineString([(-116.20, 43.63), (-116.21, 43.64)]),
            ],
        },
        crs=geometry.WGS84,
    )


def test_segment_geometry_fallback_flagged():
    """A segment absent from the network falls back to a straight endpoint line,
    flagged as 'fallback'; a present one is 'xd'; no metadata -> 'missing'."""
    net = _toy_network()
    meta = pd.DataFrame(
        {"Start Longitude": [-116.30], "Start Latitude": [43.50],
         "End Longitude": [-116.31], "End Latitude": [43.51]},
        index=pd.Index([2001], name="Segment ID"),
    )
    geo = geometry.segment_geometry(net, segment_ids=[1001, 2001, 9999], metadata=meta)

    assert geo.loc[1001, "source"] == "xd"
    assert len(geo.loc[1001, "geometry"].coords) == 3          # real polyline
    assert geo.loc[2001, "source"] == "fallback"
    assert len(geo.loc[2001, "geometry"].coords) == 2          # straight line
    assert geo.loc[9999, "source"] == "missing"
    assert geo.loc[9999, "geometry"] is None


def test_connectivity_table_synthetic():
    conn = geometry.connectivity_table(_toy_network())
    # only 1001 has a downstream neighbour (1002); 1002 is terminal -> dropped
    assert conn.to_dict("records") == [{"Segment ID": 1001, "next_id": 1002}]
    assert conn["Segment ID"].dtype == "int64" and conn["next_id"].dtype == "int64"


def test_to_geojson_promotes_index():
    geo = geometry.segment_geometry(_toy_network(), segment_ids=[1001])
    fc = json.loads(geometry.to_geojson(geo))
    assert fc["type"] == "FeatureCollection"
    assert fc["features"][0]["properties"]["Segment ID"] == 1001
    assert fc["features"][0]["geometry"]["type"] == "LineString"


# --- directional display helpers (Item 20) ----------------------------------
def test_direction_group_and_sign():
    """Direction -> compass group and -> +/- sign (N/E positive, S/W negative);
    compound + spelled-out + blank forms all resolve sensibly."""
    cases = {
        "N": ("N", 1), "S": ("S", -1), "E": ("E", 1), "W": ("W", -1),
        "NB": ("N", 1), "Southbound": ("S", -1), "eastbound": ("E", 1),
        "NE": ("N", 1), "SW": ("S", -1),          # compound -> primary cardinal
    }
    for raw, (grp, sign) in cases.items():
        assert geometry.direction_group(raw) == grp
        assert geometry.direction_sign(raw) == sign
    # unknown / missing -> no group, sign 0
    for bad in (None, "", "  ", "XX", "123"):
        assert geometry.direction_group(bad) is None
        assert geometry.direction_sign(bad) == 0


def test_attach_directions_annotates_without_touching_geometry():
    net = _toy_network()
    geo = geometry.segment_geometry(net, segment_ids=[1001, 1002])
    before = [g.wkt for g in geo["geometry"]]
    out = geometry.attach_directions(geo, {1001: "N", 1002: "SW"})
    assert list(out[geometry.DIR_GROUP_COL]) == ["N", "S"]
    assert list(out[geometry.DIR_SIGN_COL]) == [1, -1]
    assert [g.wkt for g in out["geometry"]] == before   # geometry untouched
    assert [g.wkt for g in geo["geometry"]] == before    # input not mutated


def test_offset_separates_colocated_opposing_pair_only():
    """A co-located opposing pair (same polyline, reversed) is nudged apart; an
    isolated segment is left byte-for-byte; the input frame is never mutated."""
    from shapely.geometry import LineString

    nb = LineString([(-116.20, 43.60), (-116.20, 43.61), (-116.20, 43.62)])
    sb = LineString([(-116.20, 43.62), (-116.20, 43.61), (-116.20, 43.60)])  # reverse
    far = LineString([(-116.10, 43.70), (-116.10, 43.71)])                   # alone
    geo = gpd.GeoDataFrame(
        {"geometry": [nb, sb, far]},
        index=pd.Index([1, 2, 3], name="Segment ID"), crs=geometry.WGS84)
    orig = [g.wkt for g in geo["geometry"]]

    out = geometry.offset_overlapping_segments(geo, offset_m=6.0)
    assert list(out[geometry.OFFSET_FLAG_COL]) == [True, True, False]
    # the pair moved to opposite sides (NB east, SB west) and no longer coincide
    assert out.loc[1, "geometry"].distance(out.loc[2, "geometry"]) > 0
    assert out.loc[1, "geometry"].centroid.x > out.loc[2, "geometry"].centroid.x
    # the isolated segment is untouched; the input geometry is not mutated
    assert out.loc[3, "geometry"].wkt == far.wkt
    assert [g.wkt for g in geo["geometry"]] == orig


def test_offset_is_perpendicular_and_sized():
    """Each nudged segment shifts ~offset_m metres perpendicular to its bearing."""
    from shapely.geometry import LineString

    nb = LineString([(-116.20, 43.60), (-116.20, 43.62)])
    sb = LineString([(-116.20, 43.62), (-116.20, 43.60)])
    geo = gpd.GeoDataFrame({"geometry": [nb, sb]},
                           index=pd.Index([1, 2], name="Segment ID"), crs=geometry.WGS84)
    out = geometry.offset_overlapping_segments(geo, offset_m=6.0)
    # NB runs due north, so a right-hand shift is due east: latitude unchanged,
    # longitude moved east by ~6 m (positive lon delta at these coords).
    import math
    a0 = list(nb.coords)[0]
    b0 = list(out.loc[1, "geometry"].coords)[0]
    assert abs(b0[1] - a0[1]) < 1e-9          # no north/south drift
    assert b0[0] > a0[0]                        # shifted east
    m_per_deg_lon = 111_320.0 * math.cos(math.radians(43.61))
    assert (b0[0] - a0[0]) * m_per_deg_lon == pytest.approx(6.0, abs=1.0)


# --- real shapefile (skipped when the licensed data isn't present) ----------
@pytest.mark.skipif(not _HAVE_REAL, reason="XD shapefile / Myrtle export absent")
def test_real_xd_subset_resolves_all_myrtle_segments():
    ids = list(io.load_metadata(MYRTLE_ZIP).index)
    net = geometry.load_xd_network(XD_ZIP, segment_ids=ids)

    assert len(net) == len(ids)                                # all matched
    assert str(net.crs) == geometry.WGS84
    assert set(net.geom_type) == {"LineString"}
    assert (net.geometry.apply(lambda g: len(g.coords)) > 2).all()  # follow the road

    geo = geometry.segment_geometry(net, segment_ids=ids)
    assert (geo["source"] == "xd").all()


@pytest.mark.skipif(not _HAVE_REAL, reason="XD shapefile / Myrtle export absent")
def test_real_connectivity_chain():
    ids = list(io.load_metadata(MYRTLE_ZIP).index)
    conn = geometry.connectivity_table(geometry.load_xd_network(XD_ZIP, segment_ids=ids))
    row = conn[conn["Segment ID"] == 1187539993]
    assert not row.empty and int(row["next_id"].iloc[0]) == 448695937  # S 9th St run


@pytest.mark.skipif(not _HAVE_REAL, reason="XD shapefile / Myrtle export absent")
def test_cache_roundtrip(tmp_path):
    ids = list(io.load_metadata(MYRTLE_ZIP).index)[:5]
    cache = tmp_path / "sub.geoparquet"
    a = geometry.load_xd_network(XD_ZIP, segment_ids=ids, cache_path=cache)
    assert cache.exists()
    b = geometry.load_xd_network("does_not_matter.zip", cache_path=cache)  # read cache
    assert len(a) == len(b) == len(ids)

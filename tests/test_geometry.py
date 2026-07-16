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

"""Tests for inrix_tools.store — the area/merge model (ROADMAP Items 21, 23).

The load-bearing checks: an export ingests into a persistent **area** (by corridor
set), several exports of the same corridors **merge keep-first** (a later value never
overwrites one already stored), differing bin-lengths coexist in one area behind a
selector, and a single-export round-trip still **equals** the file loaders. The DB
path is always a parameter (``:memory:`` here).
"""
from __future__ import annotations

import zipfile
from pathlib import Path

import pandas as pd
import pytest

from inrix_tools import io, store
from inrix_tools.io import DATETIME_COL, SEGMENT_COL

REPO_ROOT = Path(__file__).resolve().parents[1]
MYRTLE_ZIP = REPO_ROOT / "Myrtle_2026-02-01_to_2026-07-16_5_min_part_1.zip"

duckdb = pytest.importorskip("duckdb")

_DATA_HDR = (
    "Date Time,Segment ID,UTC Date Time,Speed(miles/hour),"
    "Hist Av Speed(miles/hour),Ref Speed(miles/hour),Travel Time(Minutes),"
    "CValue,Pct Score30,Pct Score20,Pct Score10,Road Closure,Corridor/Region Name\n"
)


def _row(local_dt, seg, speed, tt, cvalue, corridor="9th"):
    return (f"{local_dt},{seg},2026-01-01T00:00:00Z,{speed},23,40,{tt},"
            f"{cvalue},100,0,0,F,{corridor}\n")


def _meta(corridor_segs):
    lines = ["Segment ID,Road,Direction,Start Latitude,End Latitude,Start Longitude,"
             "End Longitude,State/Region,District,Postal Code,Segment Length(Miles),Intersection"]
    for seg in corridor_segs:
        lines.append(f"{seg},S 9th St,N,43.61,43.62,-116.20,-116.19,Idaho,Ada,83702,0.13,X{seg}")
    return "\n".join(lines) + "\n"


def _make_zip(tmp_path, name, rows, segs):
    zpath = tmp_path / f"{name}_5_min_part_1.zip"
    with zipfile.ZipFile(zpath, "w") as zf:
        zf.writestr(f"{name}/data.csv", _DATA_HDR + "".join(rows))
        zf.writestr(f"{name}/metadata.csv", _meta(segs))
    return zpath


@pytest.fixture
def con():
    c = store.connect(":memory:")
    yield c
    c.close()


# 5-min export: two segments, three timestamps each (so the bin is detectable).
def _rows_5min(times, segs, speed=30):
    return [_row(t, s, speed, 0.5, 95) for t in times for s in segs]


T5 = ["2026-01-15T08:00:00-07:00", "2026-01-15T08:05:00-07:00", "2026-01-15T08:10:00-07:00"]
T5B = ["2026-01-15T08:10:00-07:00", "2026-01-15T08:15:00-07:00", "2026-01-15T08:20:00-07:00"]
T15 = ["2026-01-15T08:00:00-07:00", "2026-01-15T08:15:00-07:00", "2026-01-15T08:30:00-07:00"]


@pytest.fixture
def zip_a(tmp_path):
    return _make_zip(tmp_path, "A", _rows_5min(T5, [1001, 1002], speed=30), [1001, 1002])


# ---------------------------------------------------------------------------
# Bin detection + area identity
# ---------------------------------------------------------------------------
def test_detect_bin_minutes(zip_a, tmp_path):
    assert store.detect_bin_minutes(io.load_data(zip_a)) == 5
    z15 = _make_zip(tmp_path, "H", _rows_5min(T15, [1001]), [1001])
    assert store.detect_bin_minutes(io.load_data(z15)) == 15


def test_area_identity_by_corridor_set(zip_a, tmp_path):
    key_a, name_a, corrs_a = store.area_identity(io.load_data(zip_a))
    assert name_a == "9th" and corrs_a == ["9th"]

    # Same corridor, different segments/dates -> SAME area.
    z_same = _make_zip(tmp_path, "A2", _rows_5min(T5B, [1003]), [1003])
    key_same, _, _ = store.area_identity(io.load_data(z_same))
    assert key_same == key_a

    # Different corridor -> different area.
    z_other = _make_zip(tmp_path, "B",
                        [_row(t, 2001, 30, 0.5, 95, corridor="Myrtle") for t in T5], [2001])
    key_other, name_other, _ = store.area_identity(io.load_data(z_other))
    assert key_other != key_a and name_other == "Myrtle"


# ---------------------------------------------------------------------------
# Single-export round-trip parity + registry
# ---------------------------------------------------------------------------
def test_export_roundtrip_equals_file_loader(con, zip_a):
    info = store.ingest_export(con, zip_a)
    assert info["bin_minutes"] == 5 and info["area_name"] == "9th"
    assert info["n_rows_added"] == 6                     # 3 times x 2 segments

    loaded = store.load_export(con, info["area_key"], 5)
    direct = io.load_data(zip_a)
    pd.testing.assert_frame_equal(loaded, direct)
    assert str(loaded[DATETIME_COL].dtype) == "datetime64[ns, UTC]"
    assert loaded.attrs["units"] == direct.attrs["units"]


def test_metadata_roundtrip_equals_file_loader(con, zip_a):
    info = store.ingest_export(con, zip_a)
    loaded = store.load_metadata(con, info["area_key"])
    direct = io.load_metadata(zip_a)
    assert loaded.index.name == SEGMENT_COL and "Combined" in loaded.columns
    pd.testing.assert_frame_equal(loaded, direct)


def test_list_areas_reports_the_ingest(con, zip_a):
    assert len(store.list_areas(con)) == 0
    info = store.ingest_export(con, zip_a)
    areas = store.list_areas(con)
    assert list(areas["area_name"]) == ["9th"]
    row = areas.iloc[0]
    assert row["n_segments"] == 2
    assert row["schema_version"] == store.SCHEMA_VERSION
    assert row["bins"] == [5]
    assert store.area_names(con) == [(info["area_key"], "9th")]


# ---------------------------------------------------------------------------
# Merge: same corridors merge into one area; overlap is keep-first
# ---------------------------------------------------------------------------
def test_same_corridor_exports_merge_keep_first(con, tmp_path):
    # Export A: seg 1001 at three times, speed 30.
    za = _make_zip(tmp_path, "A", _rows_5min(T5, [1001], speed=30), [1001])
    a = store.ingest_export(con, za)
    assert a["n_rows_added"] == 3

    # Export B: SAME corridor, overlaps A at 08:10 (speed 99 -> must be ignored) and
    # adds two new later timestamps.
    zb = _make_zip(tmp_path, "B", _rows_5min(T5B, [1001], speed=99), [1001])
    b = store.ingest_export(con, zb)
    assert b["area_key"] == a["area_key"]              # merged into the same area
    assert b["n_rows_added"] == 2                       # only the two new timestamps

    df = store.load_export(con, a["area_key"], 5).sort_values(DATETIME_COL)
    assert len(df) == 5                                 # 3 + 2, overlap not duplicated
    # Keep-first: the overlapping 08:10 row keeps A's speed (30), not B's (99).
    overlap = df[df[DATETIME_COL] == pd.Timestamp("2026-01-15T15:10:00Z")]
    assert overlap["Speed(miles/hour)"].iloc[0] == 30

    # Idempotent: re-ingesting A adds nothing.
    again = store.ingest_export(con, za)
    assert again["n_rows_added"] == 0
    assert len(store.load_export(con, a["area_key"], 5)) == 5


def test_multiple_bin_lengths_coexist_and_select(con, tmp_path):
    za = _make_zip(tmp_path, "A", _rows_5min(T5, [1001]), [1001])
    zh = _make_zip(tmp_path, "H", _rows_5min(T15, [1001]), [1001])
    a = store.ingest_export(con, za)
    h = store.ingest_export(con, zh)
    assert h["area_key"] == a["area_key"]              # same corridor -> same area
    assert store.area_bins(con, a["area_key"]) == [5, 15]

    assert len(store.load_export(con, a["area_key"], 5)) == 3
    assert len(store.load_export(con, a["area_key"], 15)) == 3
    # No bin given + multiple present -> explicit error (the GUI selects one).
    with pytest.raises(ValueError):
        store.load_export(con, a["area_key"])


# ---------------------------------------------------------------------------
# Geometry + AADT cache (merged keep-first, persists with the area)
# ---------------------------------------------------------------------------
@pytest.fixture
def geo_layer():
    gpd = pytest.importorskip("geopandas")
    from shapely.geometry import LineString

    g = gpd.GeoDataFrame(
        {"source": ["xd", "missing"],
         "AADT": [42000.0, float("nan")],
         "aadt_source": ["matched", "missing"],
         "aadt_dist_m": [3.1, float("nan")],
         "Route": ["US-30", None],
         "geometry": [LineString([(-116.20, 43.61), (-116.19, 43.62)]), None]},
        geometry="geometry", crs="EPSG:4326")
    g.index = pd.Index([1001, 1002], name=SEGMENT_COL)
    return g


def test_geometry_roundtrip_and_merge(con, zip_a, geo_layer, tmp_path):
    info = store.ingest_export(con, zip_a)
    store.ingest_geometry(con, info["area_key"], geo_layer)

    back = store.load_geometry(con, info["area_key"])
    assert list(back.index) == [1001, 1002]
    assert back.geometry.loc[1001].equals(geo_layer.geometry.loc[1001])
    assert back.geometry.loc[1002] is None
    assert back.loc[1001, "AADT"] == 42000.0
    assert store.list_areas(con).iloc[0]["has_geometry"]
    assert store.list_areas(con).iloc[0]["has_aadt"]

    # Merging a later export that adds a new segment extends the geo keep-first.
    from shapely.geometry import LineString
    z2 = _make_zip(tmp_path, "A3", _rows_5min(T5B, [1001, 1003]), [1001, 1003])
    store.ingest_export(con, z2)
    extra = geo_layer.iloc[:1].copy()
    extra.index = pd.Index([1003], name=SEGMENT_COL)
    extra["geometry"] = [LineString([(-116.18, 43.63), (-116.17, 43.64)])]
    store.ingest_geometry(con, info["area_key"], extra)
    merged = store.load_geometry(con, info["area_key"])
    assert set(merged.index) == {1001, 1002, 1003}
    # 1001 keeps its first geometry, not the re-supplied one had it changed.
    assert merged.geometry.loc[1001].equals(geo_layer.geometry.loc[1001])


def test_load_dataset_bundles_and_reuses_join(con, zip_a, geo_layer, monkeypatch):
    from inrix_tools import aadt
    calls = {"n": 0}
    real = aadt.join_aadt
    monkeypatch.setattr(aadt, "join_aadt",
                        lambda *a, **k: (calls.__setitem__("n", calls["n"] + 1), real(*a, **k))[1])

    info = store.ingest_export(con, zip_a)
    store.ingest_geometry(con, info["area_key"], geo_layer)
    sd = store.load_dataset(con, info["area_key"], 5)
    assert calls["n"] == 0                              # cache hit, no recompute
    assert sd.bin_minutes == 5 and sd.area_name == "9th"
    pd.testing.assert_frame_equal(sd.df, io.load_data(zip_a))
    assert sd.geo is not None and sd.has_aadt


def test_ingest_aadt_updates_only_join_columns(con, zip_a, geo_layer):
    pytest.importorskip("geopandas")
    geo_only = geo_layer.drop(columns=list(store._AADT_JOIN_COLS), errors="ignore")
    info = store.ingest_export(con, zip_a)
    store.ingest_geometry(con, info["area_key"], geo_only)
    assert not store.list_areas(con).iloc[0]["has_aadt"]

    store.ingest_aadt(con, info["area_key"], geo_layer)
    back = store.load_geometry(con, info["area_key"])
    assert store.list_areas(con).iloc[0]["has_aadt"]
    assert back.loc[1001, "AADT"] == 42000.0
    assert back.geometry.loc[1001].equals(geo_layer.geometry.loc[1001])


# ---------------------------------------------------------------------------
# DB path is a param; remove
# ---------------------------------------------------------------------------
def test_db_path_is_a_param_and_persists(tmp_path, zip_a):
    db = tmp_path / "nested" / "study.duckdb"
    db.parent.mkdir(parents=True)
    con = store.connect(db)
    info = store.ingest_export(con, zip_a)
    con.close()
    assert db.exists()

    con2 = store.connect(db)
    assert store.area_names(con2) == [(info["area_key"], "9th")]
    pd.testing.assert_frame_equal(
        store.load_export(con2, info["area_key"], 5), io.load_data(zip_a))
    con2.close()


def test_remove_area(con, zip_a):
    info = store.ingest_export(con, zip_a)
    assert store.remove_area(con, info["area_key"])
    assert store.area_names(con) == []
    assert not store.remove_area(con, info["area_key"])
    with pytest.raises(KeyError):
        store.load_export(con, info["area_key"], 5)


# ---------------------------------------------------------------------------
# Self-skipping real-export ingest
# ---------------------------------------------------------------------------
@pytest.mark.skipif(not MYRTLE_ZIP.exists(), reason="real Myrtle export not present")
def test_real_export_ingest_roundtrip(tmp_path):
    con = store.connect(tmp_path / "real.duckdb")
    info = store.ingest_export(con, MYRTLE_ZIP)
    assert info["bin_minutes"] == 5
    bins = store.area_bins(con, info["area_key"])
    loaded = store.load_export(con, info["area_key"], bins[0])
    direct = io.load_data(MYRTLE_ZIP)
    assert len(loaded) == len(direct)
    assert loaded[DATETIME_COL].min() == direct[DATETIME_COL].min()
    assert loaded[DATETIME_COL].max() == direct[DATETIME_COL].max()
    con.close()

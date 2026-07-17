"""Tests for inrix_tools.store (ROADMAP Item 21).

The load-bearing checks are the **round-trip parity** ones: a dataset ingested and
loaded back through the store must equal what the file loaders produce
(:func:`io.load_data` / :func:`io.load_metadata` / the cached geometry+AADT join),
and the GIS spatial join must run **once at ingest**, never on load. The DB path is
always a parameter (``:memory:`` here) — no hardcoded location escapes the module.
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


# --- synthetic fixture (mirrors test_io.py so parity is against the real loaders) --
DATA_HEADER = (
    "Date Time,Segment ID,UTC Date Time,Speed(miles/hour),"
    "Hist Av Speed(miles/hour),Ref Speed(miles/hour),Travel Time(Minutes),"
    "CValue,Pct Score30,Pct Score20,Pct Score10,Road Closure,Corridor/Region Name\n"
)


def _row(local_dt, seg, speed, tt, cvalue, closure="F", corridor="9th"):
    return (f"{local_dt},{seg},2026-01-01T00:00:00Z,{speed},23,23,{tt},"
            f"{cvalue},100,0,0,{closure},{corridor}\n")


@pytest.fixture
def sample_zip(tmp_path):
    """A tiny two-segment corridor spanning the MST→MDT switch (as in test_io)."""
    rows = [
        _row("2026-01-15T08:00:00-07:00", 1001, 30, 0.50, 95),
        _row("2026-01-15T08:00:00-07:00", 1002, 40, 0.40, 90),
        _row("2026-07-15T08:00:00-06:00", 1001, 32, 0.48, 88, closure="T"),
        _row("2026-07-15T08:00:00-06:00", 1002, 41, 0.39, 91),
    ]
    meta = (
        "Segment ID,Road,Direction,Start Latitude,End Latitude,Start Longitude,"
        "End Longitude,State/Region,District,Postal Code,Segment Length(Miles),Intersection\n"
        "1001,S 9th St,S,43.61,43.62,-116.20,-116.19,Idaho,Ada,83702,0.13,Boise Ctr\n"
        "1002,S 9th St,S,43.62,43.63,-116.19,-116.18,Idaho,Ada,83702,0.12,Myrtle St\n"
    )
    zpath = tmp_path / "Sample_5_min_part_1.zip"
    with zipfile.ZipFile(zpath, "w") as zf:
        zf.writestr("Sample_5_min_part_1/data.csv", DATA_HEADER + "".join(rows))
        zf.writestr("Sample_5_min_part_1/metadata.csv", meta)
    return zpath


@pytest.fixture
def con():
    c = store.connect(":memory:")
    yield c
    c.close()


# ---------------------------------------------------------------------------
# Ingest -> list -> load parity with the file loaders
# ---------------------------------------------------------------------------
def test_export_roundtrip_equals_file_loader(con, sample_zip):
    store.ingest_export(con, "sample", sample_zip)

    loaded = store.load_export(con, "sample")
    direct = io.load_data(sample_zip)

    # Same columns, order, dtypes, and values as the file loader.
    pd.testing.assert_frame_equal(loaded, direct)
    # tz-aware UTC preserved (the DuckDB TIMESTAMPTZ round-trip normalization).
    assert str(loaded[DATETIME_COL].dtype) == "datetime64[ns, UTC]"
    assert loaded[SEGMENT_COL].dtype == "int64"
    assert loaded["Road Closure"].dtype == bool
    # Units are restored onto attrs.
    assert loaded.attrs["units"] == direct.attrs["units"]


def test_metadata_roundtrip_equals_file_loader(con, sample_zip):
    store.ingest_export(con, "sample", sample_zip)
    loaded = store.load_metadata(con, "sample")
    direct = io.load_metadata(sample_zip)
    assert loaded.index.name == SEGMENT_COL
    assert "Combined" in loaded.columns
    pd.testing.assert_frame_equal(loaded, direct)


def test_list_datasets_reports_the_ingest(con, sample_zip):
    assert len(store.list_datasets(con)) == 0
    store.ingest_export(con, "sample", sample_zip)
    ds = store.list_datasets(con)
    assert list(ds["name"]) == ["sample"]
    row = ds.iloc[0]
    assert row["n_rows"] == 4
    assert row["n_segments"] == 2
    assert row["schema_version"] == store.SCHEMA_VERSION
    assert not row["has_geometry"]
    assert store.dataset_names(con) == ["sample"]


def test_reingest_is_idempotent(con, sample_zip):
    store.ingest_export(con, "sample", sample_zip)
    store.ingest_export(con, "sample", sample_zip)  # replace, not duplicate
    assert store.dataset_names(con) == ["sample"]
    assert len(store.load_export(con, "sample")) == 4


# ---------------------------------------------------------------------------
# Geometry + AADT cache round-trip (the processed GIS join, cached at ingest)
# ---------------------------------------------------------------------------
@pytest.fixture
def geo_layer():
    """A 3-segment geo layer like segment_geometry+join_aadt output: two real
    polylines, one ``missing`` (None geometry), AADT-join columns attached."""
    gpd = pytest.importorskip("geopandas")
    from shapely.geometry import LineString

    g = gpd.GeoDataFrame(
        {"source": ["xd", "xd", "missing"],
         "AADT": [42000.0, 15000.0, float("nan")],
         "aadt_source": ["matched", "matched", "missing"],
         "aadt_dist_m": [3.1, 8.7, float("nan")],
         "Route": ["US-30", "US-30", None],
         "geometry": [LineString([(-116.20, 43.61), (-116.19, 43.62)]),
                      LineString([(-116.19, 43.62), (-116.18, 43.63)]),
                      None]},
        geometry="geometry", crs="EPSG:4326",
    )
    g.index = pd.Index([1001, 1002, 1003], name=SEGMENT_COL)
    return g


def test_geometry_roundtrip(con, sample_zip, geo_layer):
    store.ingest_export(con, "sample", sample_zip)
    store.ingest_geometry(con, "sample", geo_layer)

    back = store.load_geometry(con, "sample")
    assert back.index.name == SEGMENT_COL
    assert list(back.index) == [1001, 1002, 1003]
    assert str(back.crs).upper().endswith("4326")
    # Real polylines preserved; the missing segment stays None.
    assert back.geometry.loc[1001].equals(geo_layer.geometry.loc[1001])
    assert back.geometry.loc[1003] is None
    # AADT-join columns cached verbatim (match flags + distance + value).
    assert back.loc[1001, "aadt_source"] == "matched"
    assert back.loc[1001, "AADT"] == 42000.0
    assert back.loc[1003, "aadt_source"] == "missing"
    assert pd.isna(back.loc[1003, "AADT"])
    # The registry now reflects a geometry + AADT cache.
    row = store.list_datasets(con).iloc[0]
    assert row["has_geometry"] and row["has_aadt"]


def test_load_dataset_bundles_frames(con, sample_zip, geo_layer):
    store.ingest_export(con, "sample", sample_zip)
    store.ingest_geometry(con, "sample", geo_layer)
    sd = store.load_dataset(con, "sample")
    pd.testing.assert_frame_equal(sd.df, io.load_data(sample_zip))
    pd.testing.assert_frame_equal(sd.metadata, io.load_metadata(sample_zip))
    assert sd.geo is not None and len(sd.geo) == 3
    assert sd.has_aadt


def test_ingest_aadt_updates_only_join_columns(con, sample_zip, geo_layer):
    pytest.importorskip("geopandas")
    # Ingest geometry *without* AADT first.
    geo_only = geo_layer.drop(columns=list(store._AADT_JOIN_COLS), errors="ignore")
    store.ingest_export(con, "sample", sample_zip)
    store.ingest_geometry(con, "sample", geo_only)
    assert not store.list_datasets(con).iloc[0]["has_aadt"]

    # Then attach AADT via ingest_aadt; geometry must be preserved.
    store.ingest_aadt(con, "sample", geo_layer)
    back = store.load_geometry(con, "sample")
    assert store.list_datasets(con).iloc[0]["has_aadt"]
    assert back.loc[1001, "AADT"] == 42000.0
    assert back.geometry.loc[1001].equals(geo_layer.geometry.loc[1001])


# ---------------------------------------------------------------------------
# Cache-hit: loading a dataset must NOT recompute the spatial join
# ---------------------------------------------------------------------------
def test_load_does_not_recompute_spatial_join(con, sample_zip, geo_layer, monkeypatch):
    from inrix_tools import aadt

    calls = {"n": 0}
    real_join = aadt.join_aadt

    def counting_join(*a, **k):
        calls["n"] += 1
        return real_join(*a, **k)

    monkeypatch.setattr(aadt, "join_aadt", counting_join)

    # Ingest the already-joined geo (the join happened once, upstream of ingest).
    store.ingest_export(con, "sample", sample_zip)
    store.ingest_geometry(con, "sample", geo_layer)

    # Loading the dataset reads the cached join — join_aadt is never called.
    sd = store.load_dataset(con, "sample")
    assert calls["n"] == 0
    assert "AADT" in sd.geo.columns


# ---------------------------------------------------------------------------
# The DB path is a parameter (persists to a real file, no hardcoded path)
# ---------------------------------------------------------------------------
def test_db_path_is_a_param_and_persists(tmp_path, sample_zip):
    db = tmp_path / "nested" / "study.duckdb"
    db.parent.mkdir(parents=True)
    con = store.connect(db)
    store.ingest_export(con, "sample", sample_zip)
    con.close()
    assert db.exists()

    # A fresh connection to the same file sees the dataset.
    con2 = store.connect(db)
    assert store.dataset_names(con2) == ["sample"]
    pd.testing.assert_frame_equal(store.load_export(con2, "sample"),
                                  io.load_data(sample_zip))
    con2.close()


def test_remove_dataset(con, sample_zip):
    store.ingest_export(con, "sample", sample_zip)
    assert store.remove_dataset(con, "sample")
    assert store.dataset_names(con) == []
    assert not store.remove_dataset(con, "sample")
    with pytest.raises(KeyError):
        store.load_export(con, "sample")


# ---------------------------------------------------------------------------
# Self-skipping real-export ingest (only when the licensed fixture is present)
# ---------------------------------------------------------------------------
@pytest.mark.skipif(not MYRTLE_ZIP.exists(), reason="real Myrtle export not present")
def test_real_export_ingest_roundtrip(tmp_path):
    con = store.connect(tmp_path / "real.duckdb")
    store.ingest_export(con, "myrtle", MYRTLE_ZIP)
    loaded = store.load_export(con, "myrtle")
    direct = io.load_data(MYRTLE_ZIP)
    assert len(loaded) == len(direct)
    assert str(loaded[DATETIME_COL].dtype) == "datetime64[ns, UTC]"
    assert loaded[DATETIME_COL].min() == direct[DATETIME_COL].min()
    assert loaded[DATETIME_COL].max() == direct[DATETIME_COL].max()
    row = store.list_datasets(con).iloc[0]
    assert row["n_rows"] == len(direct)
    con.close()

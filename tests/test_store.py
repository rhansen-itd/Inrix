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


def _supplemental_zip(tmp_path):
    """A later, separately-requested export of segments that belong to an existing
    area — INRIX names the report whatever it was asked for, so its corridor label is
    its own (the seven SH-19 segments backfilled into D3 came back as ``"Cent"``)."""
    rows = [_row(t, 1009, 30, 0.5, 95, corridor="Cent") for t in T5]
    return _make_zip(tmp_path, "Cent", rows, [1009])


@pytest.mark.parametrize("ingest", ["streaming", "pandas"])
def test_corridor_name_files_a_supplemental_export_into_the_existing_area(
        con, zip_a, tmp_path, ingest):
    """The backfill of ROADMAP Item 42. Without the relabel the supplement is an
    **area of its own** that no district run would ever look at; with it the rows
    become the area's own, label and all."""
    area = store.ingest_export(con, zip_a)["area_key"]
    supp = _supplemental_zip(tmp_path)

    unlabelled = store.ingest_export_streaming(con, supp)
    assert unlabelled["area_key"] != area              # its own area, as it stands
    store.remove_area(con, unlabelled["area_key"])

    call = (store.ingest_export_streaming if ingest == "streaming"
            else store.ingest_export)
    out = call(con, supp, corridor_name="9th")
    assert out["area_key"] == area and out["area_name"] == "9th"
    assert 1009 in store.area_segments(con, area)
    assert store.load_metadata(con, area).index.tolist() == [1001, 1002, 1009]

    # the stored rows carry the label they were filed under, so re-deriving the
    # identity from them lands on the same area
    stored = con.execute(
        f'SELECT DISTINCT "{store.CORRIDOR_COL}" FROM "obs_{area}"').fetchall()
    assert [r[0] for r in stored] == ["9th"]
    # ...and the relabel is on the record
    log = con.execute(f'SELECT source FROM "{store.INGESTS_TABLE}" '
                      f"WHERE source LIKE '%Cent%'").fetchone()[0]
    assert "'Cent' -> '9th'" in log


def test_corridor_name_refuses_an_export_with_no_corridor_column(con, tmp_path):
    """It relabels; it does not invent a label for an export that carries none."""
    zpath = tmp_path / "NoCorr_5_min_part_1.zip"
    hdr = _DATA_HDR.replace(",Corridor/Region Name", "")
    rows = ["".join(_row(t, 1001, 30, 0.5, 95).rsplit(",9th", 1)) for t in T5]
    with zipfile.ZipFile(zpath, "w") as zf:
        zf.writestr("NoCorr/data.csv", hdr + "".join(rows))
        zf.writestr("NoCorr/metadata.csv", _meta([1001]))
    for call in (store.ingest_export_streaming, store.ingest_export):
        with pytest.raises(ValueError, match="no 'Corridor/Region Name'"):
            call(con, zpath, corridor_name="9th")


@pytest.mark.parametrize("ingest", ["streaming", "pandas"])
def test_segment_ids_filters_ingested_observations_and_metadata(con, tmp_path, ingest):
    """Loading backfill exports grouped by timezone (e.g. Pacific for D1+D2, Mountain for D4+D5+D6)
    requires filtering to only the district's appropriate links."""
    rows = []
    for t in T5:
        rows.append(_row(t, 2001, 30, 0.5, 95, corridor="Multi"))
        rows.append(_row(t, 2002, 35, 0.4, 90, corridor="Multi"))
    zpath = _make_zip(tmp_path, "Multi", rows, [2001, 2002])

    call = (store.ingest_export_streaming if ingest == "streaming"
            else store.ingest_export)
    out = call(con, zpath, corridor_name="D_Target", segment_ids=[2002])

    assert 2002 in store.area_segments(con, out["area_key"])
    assert 2001 not in store.area_segments(con, out["area_key"])
    meta = store.load_metadata(con, out["area_key"])
    assert 2002 in meta.index
    assert 2001 not in meta.index


def test_area_segments_reads_the_observations_not_the_metadata(con, zip_a):
    """What the export *contains* is what was observed. A district export is split
    into parts by segment and each part's ``metadata.csv`` lists only its own, while
    ``ingest_export_streaming`` reads metadata from ``source`` alone — so the store
    under-reports its segment set through ``load_metadata``: Item 42 found
    ``d3_store.duckdb`` answering 1,947 against 3,905 observed."""
    info = store.ingest_export(con, zip_a)
    key = info["area_key"]
    observed = store.area_segments(con, key)
    assert observed == sorted(store.load_metadata(con, key).index)
    con.execute(f'DELETE FROM "meta_{key}" WHERE "{SEGMENT_COL}" = {observed[0]}')
    assert len(store.load_metadata(con, key)) == len(observed) - 1
    assert store.area_segments(con, key) == observed           # unchanged
    assert store.area_segments(con, "no_such_area") == []


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


def test_merge_frame_drops_null_key_rows(con):
    # A NULL key can't participate in keep-first dedup (SQL NULL != NULL), so without
    # the R6 guard the NULL-key row would re-insert on every ingest -> duplicates.
    frame = pd.DataFrame({SEGMENT_COL: [1001, None], "v": [1, 2]})
    assert store._merge_frame(con, "t_null", frame, keys=[SEGMENT_COL]) == 1
    # Re-ingesting the same frame adds nothing (keep-first holds; no NULL re-leak).
    assert store._merge_frame(con, "t_null", frame, keys=[SEGMENT_COL]) == 0
    assert con.execute('SELECT count(*) FROM "t_null"').fetchone()[0] == 1


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
# Friendly names in the store (Item 27): global, last-write-wins upsert
# ---------------------------------------------------------------------------
from inrix_tools.names import INRIX_LABEL_COL, NAME_COL  # noqa: E402


def test_save_names_last_write_wins_and_blank_clears(con):
    df = pd.DataFrame({SEGMENT_COL: [1001, 1002, 1003],
                       NAME_COL: ["Main & 1st", "   ", "Elm St"],
                       INRIX_LABEL_COL: ["L1", "L2", "L3"]})
    assert store.save_names(con, df) == 2                 # 1002 blank -> skipped
    back = store.load_names(con)
    assert back.index.name == SEGMENT_COL and back.index.dtype == "int64"
    assert back.loc[1001, NAME_COL] == "Main & 1st"
    assert 1002 not in back.index                          # blank never stored

    # Last-write-wins: an edit overwrites; a blank clears the override (row removed).
    df2 = pd.DataFrame({SEGMENT_COL: [1001, 1003],
                        NAME_COL: ["Main St (edited)", ""]})
    assert store.save_names(con, df2) == 1
    back2 = store.load_names(con)
    assert back2.loc[1001, NAME_COL] == "Main St (edited)"  # overwritten
    assert 1003 not in back2.index                          # cleared


def test_load_names_empty_when_none_saved(con):
    empty = store.load_names(con)
    assert list(empty.columns) == [NAME_COL, INRIX_LABEL_COL]
    assert len(empty) == 0 and empty.index.name == SEGMENT_COL


def test_save_names_requires_columns(con):
    with pytest.raises(ValueError, match="must have"):
        store.save_names(con, pd.DataFrame({"foo": [1]}))


# ---------------------------------------------------------------------------
# Date push-down (Item 25): restrict a DB load by inclusive local calendar date,
# pushed into the SQL scan — must match ``filter_date_range`` on the localized frame.
# ---------------------------------------------------------------------------
_TZ = "America/Denver"  # MST (-07:00) across the fixture days — clear of DST


@pytest.fixture
def zip_md(tmp_path):
    """A multi-day 5-min export (two 5-min stamps/day for five February days) so a
    date sub-range genuinely splits it. The -07:00 offset ≠ UTC, so a bad UTC-bound
    conversion would shift the cut and fail the parity check."""
    times = []
    for day in ("2026-02-02", "2026-02-03", "2026-02-04", "2026-02-05", "2026-02-06"):
        times += [f"{day}T08:00:00-07:00", f"{day}T08:05:00-07:00"]
    return _make_zip(tmp_path, "MD", _rows_5min(times, [1001, 1002]), [1001, 1002])


def _sorted_keys(df):
    return (df[[SEGMENT_COL, DATETIME_COL]]
            .sort_values([SEGMENT_COL, DATETIME_COL]).reset_index(drop=True))


def test_load_export_date_pushdown_equals_filter_date_range(con, zip_md):
    from inrix_tools.timebins import filter_date_range

    info = store.ingest_export(con, zip_md)
    key = info["area_key"]
    full_local = io.to_local(store.load_export(con, key, 5), _TZ)
    # The pandas-side calendar cut we must reproduce exactly.
    expected = filter_date_range(full_local, "2026-02-03", "2026-02-05")

    pushed = store.load_export(con, key, 5, date_start="2026-02-03",
                               date_end="2026-02-05", tz=_TZ)
    pushed_local = io.to_local(pushed, _TZ)

    assert 0 < len(pushed_local) < len(full_local)     # a real reduction
    pd.testing.assert_frame_equal(
        _sorted_keys(pushed_local), _sorted_keys(expected))
    # inclusive of the whole end day; the day after is excluded.
    days = pushed_local[DATETIME_COL].dt.tz_convert(_TZ).dt.date.astype(str)
    assert set(days) == {"2026-02-03", "2026-02-04", "2026-02-05"}


def test_load_export_open_sided_range(con, zip_md):
    info = store.ingest_export(con, zip_md)
    key = info["area_key"]
    # Only a start bound (hi open): keep from 02-05 on.
    ge = io.to_local(store.load_export(con, key, 5, date_start="2026-02-05", tz=_TZ), _TZ)
    assert set(ge[DATETIME_COL].dt.tz_convert(_TZ).dt.date.astype(str)) == {
        "2026-02-05", "2026-02-06"}
    # Only an end bound (lo open): keep up to and including 02-03.
    le = io.to_local(store.load_export(con, key, 5, date_end="2026-02-03", tz=_TZ), _TZ)
    assert set(le[DATETIME_COL].dt.tz_convert(_TZ).dt.date.astype(str)) == {
        "2026-02-02", "2026-02-03"}


def test_load_export_empty_range_degrades(con, zip_md):
    info = store.ingest_export(con, zip_md)
    key = info["area_key"]
    # start after end -> an empty, still-typed frame (no crash), same columns.
    empty = store.load_export(con, key, 5, date_start="2026-02-10",
                              date_end="2026-02-01", tz=_TZ)
    full = store.load_export(con, key, 5)
    assert len(empty) == 0
    assert str(empty[DATETIME_COL].dtype) == "datetime64[ns, UTC]"
    assert list(empty.columns) == list(full.columns)


def test_area_local_span_from_registry(con, zip_md):
    info = store.ingest_export(con, zip_md)
    lo, hi = store.area_local_span(con, info["area_key"], _TZ)
    assert (lo.isoformat(), hi.isoformat()) == ("2026-02-02", "2026-02-06")
    # No tz -> UTC dates (the 08:00 MST stamps are 15:00 UTC, still the same day).
    lo_utc, hi_utc = store.area_local_span(con, info["area_key"])
    assert (lo_utc.isoformat(), hi_utc.isoformat()) == ("2026-02-02", "2026-02-06")
    with pytest.raises(KeyError):
        store.area_local_span(con, "nope")


def test_load_dataset_pushes_dates_down(con, zip_md):
    info = store.ingest_export(con, zip_md)
    sd = store.load_dataset(con, info["area_key"], 5, with_geometry=False,
                            date_start="2026-02-04", date_end="2026-02-04", tz=_TZ)
    days = sd.df[DATETIME_COL].dt.tz_convert(_TZ).dt.date.astype(str)
    assert set(days) == {"2026-02-04"}


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


# ---------------------------------------------------------------------------
# Streaming ingest (ROADMAP Item 35): the zip member -> DuckDB path must land the
# *same table* the pandas path lands, without the whole-frame read.
# ---------------------------------------------------------------------------
def _obs(con, key):
    return con.execute(f'SELECT * FROM "{store._obs_table(key)}"').df()


def test_streaming_ingest_matches_the_pandas_ingest(tmp_path, zip_a):
    """The assertion the item asks for: identical tables, on a fixture small enough
    to run both ways — same values, same column **types**, same row order."""
    a, b = store.connect(":memory:"), store.connect(":memory:")
    try:
        pandas_info = store.ingest_export(a, zip_a)
        stream_info = store.ingest_export_streaming(b, zip_a)
        assert stream_info == pandas_info

        key = pandas_info["area_key"]
        pd.testing.assert_frame_equal(_obs(a, key), _obs(b, key))
        assert (a.execute(f'DESCRIBE "{store._obs_table(key)}"').fetchall()
                == b.execute(f'DESCRIBE "{store._obs_table(key)}"').fetchall())
        pd.testing.assert_frame_equal(store.load_export(a, key, 5),
                                      store.load_export(b, key, 5))
        pd.testing.assert_frame_equal(store.load_metadata(a, key),
                                      store.load_metadata(b, key))
        cols = ["area_key", "area_name", "corridors", "n_segments", "date_min",
                "date_max", "schema_version", "bins"]
        pd.testing.assert_frame_equal(store.list_areas(a)[cols],
                                      store.list_areas(b)[cols])
    finally:
        a.close(), b.close()


def test_streaming_ingest_merges_keep_first_and_is_idempotent(con, tmp_path):
    za = _make_zip(tmp_path, "A", _rows_5min(T5, [1001], speed=30), [1001])
    zb = _make_zip(tmp_path, "B", _rows_5min(T5B, [1001], speed=99), [1001])
    a = store.ingest_export_streaming(con, za)
    b = store.ingest_export_streaming(con, zb)
    assert b["area_key"] == a["area_key"] and b["n_rows_added"] == 2

    df = store.load_export(con, a["area_key"], 5).sort_values(DATETIME_COL)
    assert len(df) == 5
    overlap = df[df[DATETIME_COL] == pd.Timestamp("2026-01-15T15:10:00Z")]
    assert overlap["Speed(miles/hour)"].iloc[0] == 30      # keep-first, not 99
    assert store.ingest_export_streaming(con, za)["n_rows_added"] == 0


def test_streaming_ingest_handles_split_parts(tmp_path):
    """A part-split download ingests as one area, and matches what the pandas path
    (which concatenates the parts) stores."""
    name = "Split_2026-01-15_5_min"
    for part, segs in ((1, [1001]), (2, [1002])):
        zpath = tmp_path / f"{name}_part_{part}.zip"
        with zipfile.ZipFile(zpath, "w") as zf:
            zf.writestr(f"{name}/data.csv", _DATA_HDR + "".join(_rows_5min(T5, segs)))
            zf.writestr(f"{name}/metadata.csv", _meta(segs))
    part1 = tmp_path / f"{name}_part_1.zip"

    a, b = store.connect(":memory:"), store.connect(":memory:")
    try:
        pandas_info = store.ingest_export(a, part1)
        stream_info = store.ingest_export_streaming(b, part1)
        assert stream_info["n_rows_added"] == pandas_info["n_rows_added"] == 6
        assert stream_info["area_key"] == pandas_info["area_key"]
        key = stream_info["area_key"]
        left = _obs(a, key).sort_values([SEGMENT_COL, DATETIME_COL], ignore_index=True)
        right = _obs(b, key).sort_values([SEGMENT_COL, DATETIME_COL], ignore_index=True)
        pd.testing.assert_frame_equal(left, right)
    finally:
        a.close(), b.close()


def test_streaming_ingest_types_numerics_like_pandas(tmp_path):
    """``pd.to_numeric`` yields int64 only for an all-integral, no-nulls column;
    anything else is float64. The SQL path derives the same split from the staged
    values, so the stored column types match column for column."""
    rows = [_row(T5[0], 1001, 30, 0.5, 95), _row(T5[1], 1001, 31, 0.6, "")]
    z = _make_zip(tmp_path, "T", rows, [1001])
    a, b = store.connect(":memory:"), store.connect(":memory:")
    try:
        key = store.ingest_export(a, z)["area_key"]
        store.ingest_export_streaming(b, z)
        types = {r[0]: r[1] for r in
                 b.execute(f'DESCRIBE "{store._obs_table(key)}"').fetchall()}
        assert types["Speed(miles/hour)"] == "BIGINT"      # 30, 31 -> int64
        assert types["Travel Time(Minutes)"] == "DOUBLE"
        assert types["CValue"] == "DOUBLE"                 # one row is null
        assert types["Road Closure"] == "BOOLEAN"
        assert (a.execute(f'DESCRIBE "{store._obs_table(key)}"').fetchall()
                == b.execute(f'DESCRIBE "{store._obs_table(key)}"').fetchall())
    finally:
        a.close(), b.close()


def test_merge_widens_an_integer_column_rather_than_truncating(con, tmp_path):
    """A column typed from the first export must not silently truncate a later one:
    ``Speed`` of 30 then 30.5 has to end up DOUBLE, not BIGINT rounding to 31."""
    za = _make_zip(tmp_path, "A", _rows_5min(T5, [1001], speed=30), [1001])
    zb = _make_zip(tmp_path, "B", _rows_5min(T5B, [1001], speed=30.5), [1001])
    key = store.ingest_export_streaming(con, za)["area_key"]
    store.ingest_export_streaming(con, zb)
    df = store.load_export(con, key, 5)
    assert sorted(df["Speed(miles/hour)"].unique()) == [30.0, 30.5]


@pytest.mark.skipif(not MYRTLE_ZIP.exists(), reason="real Myrtle export not present")
def test_streaming_ingest_of_the_real_export_matches(tmp_path):
    """The parity check that matters — 2.18 M real rows, both ways.

    Both stores are on disk and the comparison is a two-way ``EXCEPT`` inside DuckDB:
    pulling 2.18 M rows into pandas twice to diff them would cost more memory than the
    ingest under test.
    """
    ref_db, stream_db = tmp_path / "pandas.duckdb", tmp_path / "stream.duckdb"
    a = store.connect(ref_db)
    try:
        key = store.ingest_export(a, MYRTLE_ZIP)["area_key"]
    finally:
        a.close()
    b = store.connect(stream_db)
    try:
        assert store.ingest_export_streaming(b, MYRTLE_ZIP)["area_key"] == key
        obs = store._obs_table(key)
        b.execute(f"ATTACH '{ref_db}' AS ref (READ_ONLY)")
        # EXCEPT is set semantics, so check the counts too — that catches a row the
        # streaming path duplicated rather than dropped.
        n_ref, n_stream = b.execute(
            f'SELECT (SELECT count(*) FROM ref."{obs}"), (SELECT count(*) FROM "{obs}")'
        ).fetchone()
        assert n_ref == n_stream > 2_000_000
        missing = b.execute(f'SELECT count(*) FROM (SELECT * FROM ref."{obs}" '
                            f'EXCEPT SELECT * FROM "{obs}")').fetchone()[0]
        extra = b.execute(f'SELECT count(*) FROM (SELECT * FROM "{obs}" '
                          f'EXCEPT SELECT * FROM ref."{obs}")').fetchone()[0]
        assert (missing, extra) == (0, 0)
        assert (b.execute(f'DESCRIBE ref."{obs}"').fetchall()
                == b.execute(f'DESCRIBE "{obs}"').fetchall())
    finally:
        b.close()


def test_streaming_ingest_chunks_agree_with_one_shot(tmp_path, zip_a):
    """The chunked path is what bounds memory on a district export: forcing a tiny
    chunk size must not change a byte of the result (nor the bin detection, which
    reads the first chunk and is checked against the whole export's histogram)."""
    a, b = store.connect(":memory:"), store.connect(":memory:")
    try:
        whole = store.ingest_export_streaming(a, zip_a)
        chunked = store.ingest_export_streaming(b, zip_a, chunk_bytes=64)
        assert chunked == whole and chunked["bin_minutes"] == 5
        key = whole["area_key"]
        pd.testing.assert_frame_equal(_obs(a, key), _obs(b, key))
    finally:
        a.close(), b.close()


def test_streaming_ingest_rejects_an_empty_export(tmp_path):
    """A data.csv with a header and no rows is a clear error, not an area named for
    an empty segment set."""
    name = "Empty_2026-01-15_5_min"
    zpath = tmp_path / f"{name}_part_1.zip"
    with zipfile.ZipFile(zpath, "w") as zf:
        zf.writestr(f"{name}/data.csv", _DATA_HDR)
        zf.writestr(f"{name}/metadata.csv", _meta([1001]))
    con = store.connect(":memory:")
    try:
        with pytest.raises(ValueError, match="No observation rows"):
            store.ingest_export_streaming(con, zpath)
    finally:
        con.close()


# ---------------------------------------------------------------------------
# Multi-part ingest is one call, and says so  (Item 39)
# ---------------------------------------------------------------------------
def _part_zip(path, rows, *, corridor="Toy Rd", start="2026-01-01T00:00:00-07:00"):
    """A minimal INRIX-shaped export part."""
    import zipfile

    ts = pd.date_range(start, periods=rows, freq="15min")
    data = pd.DataFrame({
        "Segment ID": [1000 + (i % 2) for i in range(rows)],
        "Date Time": [t.isoformat() for t in ts],
        "Speed(miles/hour)": [55.0] * rows,
        "Travel Time(Minutes)": [1.0] * rows,
        "CValue": [90] * rows,
    })
    meta = pd.DataFrame({"Segment ID": [1000, 1001], "Road": [corridor] * 2,
                         "Direction": ["N", "S"], "Miles": [1.0, 1.0],
                         "Combined": [f"{corridor} N", f"{corridor} S"]})
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("data.csv", data.to_csv(index=False))
        z.writestr("metadata.csv", meta.to_csv(index=False))


@pytest.mark.parametrize("ingest", ["streaming", "pandas"])
def test_any_part_ingests_every_sibling_part_and_the_summary_says_so(tmp_path, ingest):
    """A ``..._part_N.zip`` path means the WHOLE export, so ``n_rows_added`` is the
    total over every part — the reading that made 91,054,384 look like one part's
    row count on the 2026 D3 ingest. ``n_parts`` / ``parts`` are what make it
    unambiguous, and the provenance row names the members rather than the argument.
    """
    rows = [4, 6, 2]
    for i, n in enumerate(rows, start=1):
        _part_zip(tmp_path / f"Toy_2026_15_min_part_{i}.zip", n,
                  start=f"2026-01-0{i}T00:00:00-07:00")
    con = store.connect(tmp_path / "s.duckdb")
    fn = store.ingest_export_streaming if ingest == "streaming" else store.ingest_export
    # Handed part 1 — but part 1 is not what gets ingested.
    out = fn(con, tmp_path / "Toy_2026_15_min_part_1.zip")
    assert out["n_rows_added"] == sum(rows) == 12
    assert out["n_parts"] == 3
    assert [Path(p).name for p in out["parts"]] == [
        f"Toy_2026_15_min_part_{i}.zip" for i in (1, 2, 3)]

    # ...and a second call on another part re-discovers the same three and adds
    # nothing, which is what the keep-first merge means.
    again = fn(con, tmp_path / "Toy_2026_15_min_part_2.zip")
    assert again["n_rows_added"] == 0 and again["n_parts"] == 3

    log = con.execute(f'SELECT source, n_rows_added FROM "{store.INGESTS_TABLE}"').df()
    assert len(log) == 2
    assert " + " in log.loc[0, "source"]          # the resolved members, not the argument
    assert "part_1.zip" in log.loc[0, "source"] and "part_3.zip" in log.loc[0, "source"]
    con.close()

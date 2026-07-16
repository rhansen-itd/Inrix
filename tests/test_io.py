"""Tests for inrix_tools.io (ROADMAP Item 1)."""
import zipfile
from datetime import time
from pathlib import Path

import pandas as pd
import pytest

from inrix_tools import io

REPO_ROOT = Path(__file__).resolve().parents[1]
MYRTLE_ZIP = REPO_ROOT / "Myrtle_2026-02-01_to_2026-07-16_5_min_part_1.zip"


# --- synthetic fixture ------------------------------------------------------
DATA_HEADER = (
    "Date Time,Segment ID,UTC Date Time,Speed(miles/hour),"
    "Hist Av Speed(miles/hour),Ref Speed(miles/hour),Travel Time(Minutes),"
    "CValue,Pct Score30,Pct Score20,Pct Score10,Road Closure,Corridor/Region Name\n"
)


def _data_row(local_dt, seg, speed, tt, cvalue, closure="F", corridor="9th"):
    # UTC column is illustrative; io parses the offset-bearing local column.
    return (f"{local_dt},{seg},2026-01-01T00:00:00Z,{speed},23,23,{tt},"
            f"{cvalue},100,0,0,{closure},{corridor}\n")


@pytest.fixture
def sample_zip(tmp_path):
    """A tiny two-segment corridor: winter (MST, -07:00) and summer (MDT,
    -06:00) rows, one low-CValue row, and one timestamp missing a segment."""
    rows = [
        # winter: both segments report at 08:00 local
        _data_row("2026-01-15T08:00:00-07:00", 1001, 30, 0.50, 95),
        _data_row("2026-01-15T08:00:00-07:00", 1002, 40, 0.40, 90),
        # summer: both report at 08:00 local (offset is -06:00 now)
        _data_row("2026-07-15T08:00:00-06:00", 1001, 32, 0.48, 88),
        _data_row("2026-07-15T08:00:00-06:00", 1002, 41, 0.39, 91),
        # a low-confidence row (should be filtered by CValue>80)
        _data_row("2026-01-15T09:00:00-07:00", 1001, 10, 1.20, 40),
        # an incomplete timestamp: only segment 1001 reports
        _data_row("2026-01-15T10:00:00-07:00", 1001, 28, 0.55, 92),
    ]
    meta = (
        "Segment ID,Road,Direction,Start Latitude,End Latitude,Start Longitude,"
        "End Longitude,State/Region,District,Postal Code,Segment Length(Miles),Intersection\n"
        "1001,S 9th St,S,43.61,43.61,-116.20,-116.20,Idaho,Ada,83702,0.13,Boise Ctr\n"
        "1002,S 9th St,S,43.61,43.61,-116.20,-116.20,Idaho,Ada,83702,0.12,Myrtle St\n"
    )
    zpath = tmp_path / "Sample_5_min_part_1.zip"
    with zipfile.ZipFile(zpath, "w") as zf:
        zf.writestr("Sample_5_min_part_1/data.csv", DATA_HEADER + "".join(rows))
        zf.writestr("Sample_5_min_part_1/metadata.csv", meta)
    return zpath


def test_load_data_types_and_units(sample_zip):
    df = io.load_data(sample_zip)
    assert df[io.SEGMENT_COL].dtype == "int64"
    assert str(df[io.DATETIME_COL].dt.tz) == "UTC"  # tz-aware, normalized to UTC
    assert df["Speed(miles/hour)"].dtype.kind in "if"   # INRIX speeds are ints
    assert df["Travel Time(Minutes)"].dtype.kind == "f"
    assert df[io.ROAD_CLOSURE_COL].dtype == bool
    assert df.attrs["units"] == {"speed": "miles/hour", "travel_time": "Minutes"}


def test_load_metadata_indexed_with_label(sample_zip):
    meta = io.load_metadata(sample_zip)
    assert meta.index.name == io.SEGMENT_COL
    assert meta.loc[1001, "Combined"] == "S 9th St S Boise Ctr"
    assert meta.loc[1001, "Segment Length(Miles)"] == pytest.approx(0.13)


def test_to_local_is_dst_correct(sample_zip):
    """Both the winter and summer 08:00-local rows must read back as 08:00 local,
    proving DST is handled (the notebook's fixed-offset path could not)."""
    df = io.to_local(io.load_data(sample_zip), tz="America/Denver")
    local_hours = df[io.DATETIME_COL].dt.hour
    eight = df[local_hours == 8]
    assert len(eight) == 4  # two winter + two summer rows
    assert set(eight["time_of_day"]) == {time(8, 0)}
    assert set(eight["day_of_week"]) <= {0, 1, 2, 3, 4, 5, 6}


def test_filter_cvalue_records_threshold(sample_zip):
    df = io.load_data(sample_zip)
    kept = io.filter_cvalue(df, threshold=80)
    assert (kept[io.CVALUE_COL] > 80).all()
    assert len(kept) == len(df) - 1  # the CValue=40 row is dropped
    assert kept.attrs["cvalue_threshold"] == 80


def test_mark_complete_timestamps(sample_zip):
    df = io.to_local(io.load_data(sample_zip))
    marked = io.mark_complete_timestamps(df)
    # corridor "9th" has 2 segments; the 10:00 row (only seg 1001) is incomplete
    incomplete = marked[~marked["complete"]]
    assert set(incomplete[io.SEGMENT_COL]) == {1001}
    assert (incomplete["n_segments"] == 1).all()
    assert (marked["expected_segments"] == 2).all()


def test_split_part_discovery(sample_zip, tmp_path):
    """A single ..._part_1.zip pointing at a lone part still loads (one part)."""
    parts = io._discover_parts(sample_zip)
    assert len(parts) == 1


# --- real-format smoke test (skipped when the licensed export isn't present) -
@pytest.mark.skipif(not MYRTLE_ZIP.exists(), reason="Myrtle export not available")
def test_real_export_slice_parses():
    df = io.load_data(MYRTLE_ZIP, nrows=500)
    assert io.SEGMENT_COL in df and io.DATETIME_COL in df
    assert df.attrs["units"]["speed"] == "miles/hour"
    assert str(df[io.DATETIME_COL].dt.tz) == "UTC"
    local = io.to_local(df)
    assert "time_of_day" in local and "day_of_week" in local

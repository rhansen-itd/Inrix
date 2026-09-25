"""Tests for the catalogue builder's curve-VHD inputs (ROADMAP Item 58).

The builder is wiring; what is pinned here is that the core floors get the inputs the
curve path needs — the cached bin screen **with** its attrs (the period, zone and bin
width the volume weights are built from), and the screening run's curves, whose
absence is an error that says what to run rather than a silent fall back to the index.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd
import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

pytest.importorskip("geopandas")
import build_statewide_catalogues as bsc  # noqa: E402

from inrix_tools import profile_assignment as pa  # noqa: E402


def test_the_cached_bin_screen_keeps_its_attrs(tmp_path):
    d = tmp_path / "d3"
    d.mkdir()
    bins = pd.DataFrame({"Segment ID": [1], "month": ["2026-03"], "day_type": ["weekday"],
                         "tod_min": [420], "travel_time": [2.0], "n_obs": [5]})
    attrs = {"tz": "America/Boise", "bin_minutes": 15, "period_start": "2026-01-01",
             "period_end": "2026-08-31",
             "windows": {"am": {"name": "am", "window": "7:00AM-9:00AM",
                                "days": ["Mon"], "peak": True}}}
    bins.to_parquet(d / bsc.BINS_FILE)
    (d / bsc.BINS_FILE).with_suffix(".json").write_text(json.dumps(attrs))
    got = bsc.load_bins(3, tmp_path)
    assert got.attrs == attrs
    pd.testing.assert_frame_equal(got, bins, check_like=True)


def test_curves_come_from_the_screening_run(tmp_path):
    with pytest.raises(SystemExit, match="run the district screening"):
        bsc.load_curves(3, tmp_path)
    d = tmp_path / "d3"
    d.mkdir()
    saved = pd.DataFrame({c: [None] for c in pa.ASSIGNMENT_COLUMNS},
                         index=pd.Index([1234], name="XDSegID"))
    saved["curve_id"], saved["curve_source"] = "rural_through", pa.DEFAULT
    pa.write_assignment(saved, d / bsc.PROFILES_FILE.format(district=3))
    assert bsc.load_curves(3, tmp_path).loc[1234, "curve_id"] == "rural_through"

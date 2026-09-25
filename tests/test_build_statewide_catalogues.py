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


# ─── Item 63: District 3 is generated like the rest; couplets aren't ranked twice ──

def test_district_3_is_built_and_named_like_every_other():
    assert bsc.DEFAULT_DISTRICTS == [1, 2, 3, 4, 5, 6]
    assert [bsc.catalogue_name(d) for d in (1, 3)] == ["d1_corridors.json",
                                                       "d3_corridors.json"]


MILES = {s: 0.25 for s in range(1, 20)}
LEGS = {"couplet-a": ((1, 2), (3, 4)), "couplet-b": ((5, 6), (7, 8))}


def _cat_with_couplet(core_segments, tier2_segments=()):
    """A facility whose core runs on ``core_segments``, and whose Tier 2 on
    ``tier2_segments``; two detected couplets beside it."""
    return {"corridors": [
        {"id": "core-nb", "corridor": "us-95-moscow-core", "_segment_ids": list(core_segments)},
        {"id": "t2-nb", "corridor": "us-95-moscow-commuter",
         "_segment_ids": list(tier2_segments)},
    ], "reporting_corridors": [
        {"id": "us-95-moscow-core", "_tier_number": 1, "_ranked": True,
         "_facility": "us-95-moscow", "_couplets": ["couplet-a", "couplet-b"]},
        {"id": "us-95-moscow-commuter", "_tier_number": 2, "_ranked": False,
         "_facility": "us-95-moscow", "_couplets": ["couplet-a", "couplet-b"]},
        {"id": "couplet-a", "_couplet": True, "one_way_couplet": True},
        {"id": "couplet-b", "_couplet": True, "one_way_couplet": True},
    ]}


def test_a_couplet_a_ranked_core_covers_is_not_ranked_again():
    """Moscow: the core is both legs."""
    cat = _cat_with_couplet([1, 2, 3, 4], tier2_segments=[5, 6, 7, 8])
    assert bsc.defer_covered_couplets(cat, LEGS, MILES) == ["couplet-a"]
    a, b = cat["reporting_corridors"][2:]
    assert a["_ranked"] is False and a["_counted_in"] == "us-95-moscow"
    assert a["one_way_couplet"]          # still the AADT one-way fallback's legs
    # only a *ranked* core counts: a Tier 2 running over a couplet does not
    assert "_ranked" not in b and "_flags" not in b


def test_a_couplet_a_core_runs_on_one_leg_of_still_ranks_flagged():
    """Twin Falls: a westbound-only core on the westbound leg."""
    cat = _cat_with_couplet([1, 2])
    assert bsc.defer_covered_couplets(cat, LEGS, MILES) == []
    a = cat["reporting_corridors"][2]
    assert "_ranked" not in a
    assert a["_flags"] == ["shares 0.50 mi with us-95-moscow"]


def test_a_couplet_no_core_runs_on_ranks_as_before():
    cat = _cat_with_couplet([9, 10])
    assert bsc.defer_covered_couplets(cat, LEGS, MILES) == []
    assert all("_ranked" not in g and "_flags" not in g
               for g in cat["reporting_corridors"][2:])

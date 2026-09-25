"""Tests for scripts/aggregate_statewide_rankings.py — Tier 1 ranks, Tiers 2/3 are
context (ROADMAP Item 50)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import aggregate_statewide_rankings as agg  # noqa: E402


def _catalogue(tmp_path):
    cat = {"corridors": [], "reporting_corridors": [
        {"id": "us-95-core", "_tier_number": 1, "_ranked": True, "_facility": "us-95",
         "_flags": ["episodic: 95% of peak delay in 2026-07, 2026-08"]},
        {"id": "us-95-commuter", "_tier_number": 2, "_ranked": False, "_facility": "us-95"},
        {"id": "us-95-regional", "_tier_number": 3, "_ranked": False, "_facility": "us-95"},
        {"id": "sh-75-core", "_tier_number": 1, "_ranked": True, "_facility": "sh-75"},
        {"id": "couplet-x"},
    ]}
    (tmp_path / "d1_corridors.json").write_text(json.dumps(cat))
    return str(tmp_path / "d{district}_corridors.json")


def test_only_tier_1_and_untiered_groups_rank(tmp_path):
    tiers = agg.load_group_tiers([1], _catalogue(tmp_path))
    full = pd.DataFrame({
        "district": 1,
        "corridor_group": ["us-95-regional", "us-95-core", "us-95-commuter",
                           "sh-75-core", "couplet-x"],
        "vhd_per_mile": [400.0, 300.0, 350.0, 100.0, 200.0],
    })
    ranked, context = agg.split_ranked(full, tiers)
    assert list(ranked["corridor_group"]) == ["us-95-core", "couplet-x", "sh-75-core"]
    assert list(ranked["statewide_rank"]) == [1, 2, 3]
    assert set(context["corridor_group"]) == {"us-95-commuter", "us-95-regional"}
    # context rows point back at their facility's core rank
    assert context["core_statewide_rank"].eq(1).all()
    assert list(context["tier"]) == [2, 3]


def test_flags_travel_with_the_ranked_row(tmp_path):
    tiers = agg.load_group_tiers([1], _catalogue(tmp_path))
    full = pd.DataFrame({"district": 1, "corridor_group": ["us-95-core", "sh-75-core"],
                         "vhd_per_mile": [300.0, 100.0]})
    ranked, _ = agg.split_ranked(full, tiers)
    assert ranked.loc[0, "flags"].startswith("episodic")
    assert ranked.loc[1, "flags"] == ""


def test_a_couplet_a_core_covers_is_context_under_that_facility(tmp_path):
    """Item 63: Moscow's couplet group and the US-95 facility pairing its legs are the
    same delay; the couplet goes to context with the facility's rank."""
    path = _catalogue(tmp_path)
    cat = json.loads((tmp_path / "d1_corridors.json").read_text())
    cat["reporting_corridors"][-1].update({"_couplet": True, "_ranked": False,
                                           "_counted_in": "us-95"})
    (tmp_path / "d1_corridors.json").write_text(json.dumps(cat))
    tiers = agg.load_group_tiers([1], path)
    full = pd.DataFrame({"district": 1,
                         "corridor_group": ["us-95-core", "sh-75-core", "couplet-x"],
                         "vhd_per_mile": [300.0, 100.0, 200.0]})
    ranked, context = agg.split_ranked(full, tiers)
    assert list(ranked["corridor_group"]) == ["us-95-core", "sh-75-core"]
    row = context.set_index("corridor_group").loc["couplet-x"]
    assert row["core_statewide_rank"] == 1 and row["facility"] == "us-95"
    assert "counted in us-95" in row["flags"]


def test_a_partly_covered_couplet_ranks_with_its_flag(tmp_path):
    path = _catalogue(tmp_path)
    cat = json.loads((tmp_path / "d1_corridors.json").read_text())
    cat["reporting_corridors"][-1].update({"_couplet": True,
                                           "_flags": ["shares 0.62 mi with us-95"]})
    (tmp_path / "d1_corridors.json").write_text(json.dumps(cat))
    full = pd.DataFrame({"district": 1, "corridor_group": ["us-95-core", "couplet-x"],
                         "vhd_per_mile": [300.0, 200.0]})
    ranked, _ = agg.split_ranked(full, agg.load_group_tiers([1], path))
    assert list(ranked["corridor_group"]) == ["us-95-core", "couplet-x"]
    assert ranked.loc[1, "flags"] == "shares 0.62 mi with us-95"


def test_districts_on_different_vhd_bases_are_refused():
    """A stale pre-Item 58 table (index VHD, no vhd_per) beside a curve one — or per
    weekday beside per day — would rank two different quantities as one."""
    curve = pd.DataFrame({"district": [1], "vhd": [10.0], "vhd_per": ["weekday"]})
    other = pd.DataFrame({"district": [2], "vhd": [20.0], "vhd_per": ["weekday"]})
    stale = pd.DataFrame({"district": [3], "vhd": [80.0]})
    day = pd.DataFrame({"district": [4], "vhd": [5.0], "vhd_per": ["day"]})
    assert agg.check_vhd_basis([curve, other]) == "weekday"
    assert agg.check_vhd_basis([stale]) == "index"
    with pytest.raises(SystemExit, match="different VHD bases"):
        agg.check_vhd_basis([curve, stale])
    with pytest.raises(SystemExit, match="different VHD bases"):
        agg.check_vhd_basis([curve, day])

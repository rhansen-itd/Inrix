"""Tests for scripts/aggregate_statewide_rankings.py — Tier 1 ranks, Tiers 2/3 are
context (ROADMAP Item 50)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd

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

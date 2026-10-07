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


def test_a_couplet_never_ranks_on_its_own(tmp_path):
    """A couplet is a stitching aid, not a class (owner, 2026-10-07): one no core runs
    on (Blackfoot, Mountain Home) is context under no facility, however much delay its
    own row carries; the old partly-covered "shares N mi" rows no longer rank."""
    path = _catalogue(tmp_path)
    cat = json.loads((tmp_path / "d1_corridors.json").read_text())
    cat["reporting_corridors"][-1].update({"_couplet": True, "one_way_couplet": True,
                                           "_flags": ["shares 0.62 mi with us-95"]})
    (tmp_path / "d1_corridors.json").write_text(json.dumps(cat))
    full = pd.DataFrame({"district": 1, "corridor_group": ["us-95-core", "couplet-x"],
                         "vhd_per_mile": [300.0, 900.0]})
    ranked, context = agg.split_ranked(full, agg.load_group_tiers([1], path))
    assert list(ranked["corridor_group"]) == ["us-95-core"]
    row = context.set_index("corridor_group").loc["couplet-x"]
    assert pd.isna(row["facility"]) and pd.isna(row["core_statewide_rank"])
    assert "no congested core" in row["flags"]


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


# ─── Item 67: scenarios, the type columns, the cross-scenario matrix ──────────

def test_scenarios_come_from_the_flag_the_registries_or_the_historic_pair(tmp_path):
    got = agg.scenario_tables(tmp_path, [1], "peak,sat:summer")
    assert [(e["tag"], e["totals"]) for e in got] == [
        ("peak", "corridor_peak_totals.csv"),
        ("sat_summer", "corridor_sat_summer_totals.csv")]
    # No registry anywhere: the pre-scenario peak + 7-day pair.
    assert [e["tag"] for e in agg.scenario_tables(tmp_path, [1, 2])] == ["peak", "7day"]
    # Registries: the union over districts, the peak first.
    for d, tags in ((1, ["sun", "peak"]), (2, ["7day", "sun", "weekend_summer"])):
        (tmp_path / f"d{d}").mkdir()
        reg = {t: {"label": t.upper(), "files": {}} for t in tags}
        (tmp_path / f"d{d}" / agg.SCENARIO_REGISTRY).write_text(json.dumps(reg))
    got = agg.scenario_tables(tmp_path, [1, 2])
    assert [e["tag"] for e in got] == ["peak", "7day", "sun", "weekend_summer"]
    assert got[2]["label"] == "SUN"


def test_the_type_columns_come_from_the_catalogue(tmp_path):
    path = _catalogue(tmp_path)
    cat = json.loads((tmp_path / "d1_corridors.json").read_text())
    cat["reporting_corridors"][0].update({"_types": ["commute", "retail"],
                                          "_class": "urban_hybrid",
                                          "_primary_type": "commute"})
    (tmp_path / "d1_corridors.json").write_text(json.dumps(cat))
    types = agg.load_group_types([1], path)
    assert types == {(1, "us-95-core"): {"corridor_types": "commute+retail",
                                         "corridor_class": "urban_hybrid",
                                         "primary_type": "commute"}}
    full = pd.DataFrame({"district": 1, "corridor_group": ["us-95-core", "couplet-x"],
                         "vhd_per_mile": [3.0, 2.0]})
    got = agg.attach_types(full, types)
    assert list(got.columns[:5]) == ["district", "corridor_group", *agg.TYPE_COLUMNS]
    assert got["corridor_class"].tolist() == ["urban_hybrid", ""]
    # A district table screened before a reclassification carries stale columns: the
    # catalogue wins where it types the group; elsewhere the table's value stays.
    stale = full.assign(corridor_types="x", corridor_class="y", primary_type="z")
    got = agg.attach_types(stale, types)
    assert got["corridor_class"].tolist() == ["urban_hybrid", "y"]
    assert list(got.columns) == list(stale.columns)


def test_the_matrix_joins_every_scenario_on_the_corridor():
    peak = pd.DataFrame({"statewide_rank": [1, 2], "district": [3, 1],
                         "corridor_group": ["i-84-core", "sh-55-core"],
                         "group_name": ["I-84", "SH-55"], "corridor_class": ["commute", "recreational"],
                         "vhd_per_mile": [200.0, 5.0], "vhd": [4000.0, 50.0],
                         "tti": [2.2, 1.1]})
    sat = pd.DataFrame({"statewide_rank": [1, 2, 3], "district": [1, 3, 2],
                        "corridor_group": ["sh-55-core", "i-84-core", "us-95-core"],
                        "group_name": ["SH-55", "I-84", "US-95"],
                        "corridor_class": ["recreational", "commute", "recreational"],
                        "vhd_per_mile": [40.0, 30.0, 10.0], "vhd": [400.0, 600.0, 20.0],
                        "tti": [1.5, 1.3, 1.2]})
    m = agg.scenario_matrix({"peak": peak, "sat_summer": sat})
    assert m["corridor_group"].tolist() == ["i-84-core", "sh-55-core", "us-95-core"]
    row = m.set_index("corridor_group").loc["sh-55-core"]
    assert (row["peak_rank"], row["sat_summer_rank"]) == (2, 1)
    assert row["sat_summer_vhd_per_mile"] == 40.0
    assert pd.isna(m.set_index("corridor_group").loc["us-95-core", "peak_rank"])
    assert agg.scenario_matrix({"peak": pd.DataFrame()}).empty


def _district_totals(path, rows, vhd_per):
    frame = pd.DataFrame(rows)
    frame["vhd_per"] = vhd_per
    with path.open("w") as fh:
        fh.write("# windows: whatever\n")
        frame.to_csv(fh, index=False)


def test_main_aggregates_every_registered_scenario(tmp_path, monkeypatch):
    cat_pattern = _catalogue(tmp_path)
    for d in (1, 2):
        dd = tmp_path / f"d{d}"
        dd.mkdir()
        (dd / agg.SCENARIO_REGISTRY).write_text(json.dumps({
            "peak": {"label": "Peak", "files": {"totals": "corridor_peak_totals.csv"}},
            "sat_summer": {"label": "Summer Saturday",
                           "files": {"totals": "corridor_sat_summer_totals.csv"}}}))
        _district_totals(dd / "corridor_peak_totals.csv",
                         {"corridor_group": [f"g{d}"], "group_name": [f"G{d}"],
                          "miles": [1.0], "vhd": [10.0 * d], "vhd_per_mile": [10.0 * d],
                          "tti": [1.2]}, "weekday")
        _district_totals(dd / "corridor_sat_summer_totals.csv",
                         {"corridor_group": [f"g{d}"], "group_name": [f"G{d}"],
                          "miles": [1.0], "vhd": [30.0 / d], "vhd_per_mile": [30.0 / d],
                          "tti": [1.3]}, "gated day in season")
    monkeypatch.setattr(sys, "argv", ["agg", "--dir", str(tmp_path), "--districts", "1", "2",
                                      "--catalogue", cat_pattern])
    agg.main()
    peak = pd.read_csv(tmp_path / "statewide_peak_corridor_rankings.csv")
    sat = pd.read_csv(tmp_path / "statewide_sat_summer_corridor_rankings.csv")
    assert peak["corridor_group"].tolist() == ["g2", "g1"]
    assert sat["corridor_group"].tolist() == ["g1", "g2"]
    m = pd.read_csv(tmp_path / "statewide_scenario_matrix.csv")
    assert m.set_index("corridor_group")[["peak_rank", "sat_summer_rank"]].to_dict("index") \
        == {"g2": {"peak_rank": 1, "sat_summer_rank": 2},
            "g1": {"peak_rank": 2, "sat_summer_rank": 1}}

"""Tests for the expanded District 3 catalogue and screening outputs (ROADMAP Item 44).

Validates:
1. scripts/d3_corridors.json schema, directions, couplet flags, descriptions.
2. 100% resolution of all 34 entries through build_chain with link repairs.
3. Candidate triage audit trail (candidate_triage.csv and .json).
4. Full district screening outputs and rankings.
"""
from __future__ import annotations

import json
from pathlib import Path

import geopandas as gpd
import pandas as pd
import pytest

from inrix_tools import corridors

REPO_ROOT = Path(__file__).resolve().parents[1]
D3_CATALOGUE = REPO_ROOT / "scripts" / "d3_corridors.json"
D3_REPAIRS = REPO_ROOT / "scripts" / "d3_link_repairs.csv"
D3_NETWORK = REPO_ROOT / "geometry_cache" / "d3_network.geoparquet"
TRIAGE_CSV = REPO_ROOT / "out" / "district_screening" / "candidate_triage.csv"
TRIAGE_JSON = REPO_ROOT / "out" / "district_screening" / "candidate_triage.json"
SCREENING_DIR = REPO_ROOT / "out" / "district_screening"


def test_d3_catalogue_schema_and_completeness():
    """All 52 entries and 26 reporting corridors must be well-formed and paired (Item 49
    split US-95 at Payette into two; Item 51's route junctions rejoin it)."""
    assert D3_CATALOGUE.exists(), "scripts/d3_corridors.json missing"
    entries = corridors.load_catalogue(D3_CATALOGUE)
    groups = corridors.load_reporting_corridors(D3_CATALOGUE)

    assert len(entries) == 52
    assert len(groups) == 26

    # Validate each entry
    entry_ids = set()
    for e in entries:
        assert e.id, "entry id cannot be empty"
        assert e.id not in entry_ids, f"duplicate entry id: {e.id}"
        entry_ids.add(e.id)
        assert e.name, f"entry {e.id} missing name"
        assert len(e.description) > 80, f"entry {e.id} description must explain rationale (>80 chars)"
        assert e.corridor, f"entry {e.id} missing corridor group"
        assert e.direction in {"EB", "WB", "NB", "SB"}, f"entry {e.id} invalid direction: {e.direction}"

    # Groupings: every group has exactly 2 directions
    group_map = {g.id: g for g in groups}
    by_group: dict[str, list[str]] = {}
    for e in entries:
        assert e.corridor in group_map, f"corridor {e.corridor} not declared in reporting_corridors"
        by_group.setdefault(e.corridor, []).append(e.direction)

    for gid, dirs in by_group.items():
        assert len(dirs) == 2, f"corridor {gid} has {len(dirs)} entries; must have exactly 2"
        assert len(set(dirs)) == 2, f"corridor {gid} directions must differ: {dirs}"
        if dirs[0] in {"EB", "WB"}:
            assert set(dirs) == {"EB", "WB"}
        else:
            assert set(dirs) == {"NB", "SB"}

    # Couplet flags
    couplets = {g.id for g in groups if g.one_way_couplet}
    assert couplets == {"boise-couplet", "nampa-couplet"}


def test_d3_catalogue_includes_item44_key_corridors():
    """Item 44 specifically requires adding or splitting key extents."""
    groups = {g.id: g for g in corridors.load_reporting_corridors(D3_CATALOGUE)}

    # 1. SH-45 split into urban Nampa and rural control
    assert "sh45-nampa" in groups
    assert "sh45-rural" in groups

    # 2. SH-55 Karcher Rd
    assert "sh55-karcher" in groups

    # 3. Downtown Nampa couplet
    assert "nampa-couplet" in groups
    assert groups["nampa-couplet"].one_way_couplet is True

    # 4. SH-55 north of State St mountain highway extents
    assert "sh55-eagle-hsb" in groups
    assert "sh55-hsb-cascade" in groups
    assert "sh55-cascade-mccall" in groups
    assert "sh55-mccall-newmeadows" in groups

    # 5. SH-44 urban/rural split and Broadway Ave
    assert "sh44-urban" in groups
    assert "sh44-rural" in groups
    assert "broadway" in groups
    assert "us2026-chinden" in groups


@pytest.mark.skipif(not (D3_NETWORK.exists() and D3_REPAIRS.exists()),
                    reason="D3 network cache or repairs not available")
def test_all_d3_catalogue_entries_resolve_with_repairs():
    """All 52 entries must walk to their target and have valid chain geometries."""
    net = gpd.read_parquet(D3_NETWORK)
    repairs = corridors.load_link_repairs(D3_REPAIRS)
    entries = corridors.load_catalogue(D3_CATALOGUE)

    res = corridors.resolve_catalogue(net, entries, repairs=repairs)
    assert len(res) == 52
    assert res["reached_target"].all(), (
        f"Failed to reach target: {res[~res['reached_target']]['id'].tolist()}"
    )
    assert (res["stop_reason"] == "target").all()
    assert (res["n_segments"] > 0).all()
    assert (res["chain_miles"] > 0.0).all()


@pytest.mark.skipif(not (TRIAGE_CSV.exists() and TRIAGE_JSON.exists()),
                    reason="Candidate triage outputs not found")
def test_candidate_triage_audit_trail():
    """Audit trail must account for all extracted runs with justified decisions."""
    df = pd.read_csv(TRIAGE_CSV)
    assert len(df) == 319

    # Required columns
    expected_cols = {"candidate_id", "window", "road_number", "road_label", "direction",
                     "bearing", "xdgroup", "miles", "n_segments", "mean_recurrence",
                     "mean_tti", "decision", "reason"}
    assert expected_cols.issubset(df.columns)

    # Decisions must be valid and reasons non-empty
    assert set(df["decision"].unique()) == {"ACCEPTED", "MERGED", "REJECTED"}
    assert df["reason"].str.strip().ne("").all()

    # Exact triage counts from Item 44 extraction
    counts = df["decision"].value_counts().to_dict()
    assert counts["ACCEPTED"] == 8
    assert counts["MERGED"] == 101
    assert counts["REJECTED"] == 210

    # Verify JSON structure matches
    with TRIAGE_JSON.open() as f:
        records = json.load(f)
    assert len(records) == 319
    json_counts: dict[str, int] = {}
    for r in records:
        json_counts[r["decision"]] = json_counts.get(r["decision"], 0) + 1
    assert json_counts["ACCEPTED"] == 8
    assert json_counts["MERGED"] == 101
    assert json_counts["REJECTED"] == 210


@pytest.mark.skipif(not (SCREENING_DIR / "reporting_corridor_rankings.csv").exists(),
                    reason="District screening outputs not generated")
def test_district_screening_outputs_integrity():
    """Verify screening rankings table, peak totals, and resolution for the expanded catalogue."""
    rankings_csv = SCREENING_DIR / "reporting_corridor_rankings.csv"
    totals_csv = SCREENING_DIR / "corridor_peak_totals.csv"
    breakout_csv = SCREENING_DIR / "corridor_breakout.csv"
    resolution_csv = SCREENING_DIR / "corridor_resolution.csv"
    prov_json = SCREENING_DIR / "screening_provenance.json"

    rankings = pd.read_csv(rankings_csv, comment="#")
    totals = pd.read_csv(totals_csv, comment="#")
    breakout = pd.read_csv(breakout_csv, comment="#")
    resolution = pd.read_csv(resolution_csv, comment="#")

    assert len(rankings) == 104  # 26 groups * 4 windows
    assert len(totals) == 26     # 26 reporting groups
    assert len(breakout) == 104  # 26 groups * 2 directions * 2 peak windows
    assert len(resolution) == 52 # 52 directional entries
    assert resolution["reached_target"].all()
    assert (resolution["miles_covered_fraction"] >= 0.70).all()

    # Check key ranking outcomes from Item 44
    totals_by_id = totals.set_index("corridor_group")

    # SH-55 Karcher Rd ranks in the top 5
    assert totals_by_id.loc["sh55-karcher", "rank"] <= 5

    # SH-45 urban has far higher delay density than SH-45 rural
    urban_rate = totals_by_id.loc["sh45-nampa", "vhd_per_mile"]
    rural_rate = totals_by_id.loc["sh45-rural", "vhd_per_mile"]
    assert urban_rate > 10 * rural_rate

    # SH-44 urban has far higher delay density than SH-44 rural
    sh44_urban_rate = totals_by_id.loc["sh44-urban", "vhd_per_mile"]
    sh44_rural_rate = totals_by_id.loc["sh44-rural", "vhd_per_mile"]
    assert sh44_urban_rate > 3 * sh44_rural_rate

    # Broadway ranks in top 12
    assert totals_by_id.loc["broadway", "rank"] <= 12

    # SH-55 mountain highway extents rank near the bottom (rural baseline)
    for mtn in ["sh55-eagle-hsb", "sh55-hsb-cascade", "sh55-cascade-mccall", "sh55-mccall-newmeadows"]:
        assert totals_by_id.loc[mtn, "rank"] >= 12

    # Check provenance
    with prov_json.open() as f:
        prov = json.load(f)
    assert prov["n_entries"] == 52
    assert prov["n_accepted"] == 52
    assert len(prov["findings"]) == 0
    assert prov["reporting_corridors"]["n_groups"] == 26

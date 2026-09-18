"""Tests for the district screening runner (ROADMAP Item 39).

The load-bearing property is the same one ``test_validation_report`` pins for the
report shell: this layer **composes**, it does not compute. What is tested here is
the composition — which frames reach which call, what the CLI resolves, that a
corridor which did not resolve is reported instead of ranked, and that the outputs
carry the basis they were computed under. The statistics themselves are tested in
``test_screen`` / ``test_corridors`` / ``test_aadt``.
"""
from __future__ import annotations

import json
import sys
import zipfile
from pathlib import Path

import pandas as pd
import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import run_district_screening as rds          # noqa: E402

from inrix_tools import store                 # noqa: E402

TZ = "America/Denver"


# ---------------------------------------------------------------------------
# A whole miniature district: an export, a network, a catalogue
# ---------------------------------------------------------------------------
LON, LAT0, DLAT = -116.3545, 43.60, 0.01
SEGS = [1000, 1001, 1002, 1003]


@pytest.fixture
def district(tmp_path):
    """Four collinear northbound segments, two days of 15-minute data, ingested."""
    import geopandas as gpd
    from shapely.geometry import LineString

    from inrix_tools import geometry as geo_mod

    stamps = pd.date_range("2026-03-03 00:00", periods=2 * 96, freq="15min", tz=TZ)
    rows = []
    for sid in SEGS:
        for t in stamps:
            peak = 7 <= t.hour < 9
            rows.append({"Segment ID": sid, "Date Time": t.isoformat(),
                         "Speed(miles/hour)": 30.0 if peak else 60.0,
                         "Travel Time(Minutes)": 2.0 if peak else 1.0,
                         "Ref Speed(miles/hour)": 60.0, "CValue": 95})
    data = pd.DataFrame(rows)
    meta = pd.DataFrame({"Segment ID": SEGS, "Road": ["Toy Rd"] * 4,
                         "Direction": ["N"] * 4, "Miles": [1.0] * 4,
                         "Combined": [f"Toy Rd N {i}" for i in range(4)]})
    zpath = tmp_path / "Toy_2026_15_min.zip"
    with zipfile.ZipFile(zpath, "w") as z:
        z.writestr("data.csv", data.to_csv(index=False))
        z.writestr("metadata.csv", meta.to_csv(index=False))

    con = store.connect(tmp_path / "s.duckdb")
    info = store.ingest_export_streaming(con, zpath)
    con.close()

    net = gpd.GeoDataFrame(
        {"XDSegID": pd.array(SEGS, dtype="Int64"),
         # 1001 -> 1002 is missing: the run has to be given a repair to walk it.
         "NextXDSegI": pd.array([1001, pd.NA, 1003, pd.NA], dtype="Int64"),
         "XDGroup": pd.array([5] * 4, dtype="Int64"),
         "Bearing": ["N"] * 4, "RoadName": ["Toy Rd"] * 4, "RoadNumber": ["99"] * 4,
         "Lanes": [2.0] * 4, "Miles": [1.0] * 4, "FRC": [3] * 4},
        geometry=[LineString([(LON, LAT0 + i * DLAT), (LON, LAT0 + (i + 1) * DLAT)])
                  for i in range(4)],
        crs=geo_mod.WGS84)
    net_path = tmp_path / "net.geoparquet"
    net.to_parquet(net_path)

    catalogue = {"corridors": [
        {"id": "toy-nb", "name": "Toy Rd NB",
         "start_latlon": [LAT0 + 0.5 * DLAT, LON], "end_latlon": [LAT0 + 3.5 * DLAT, LON],
         "description": "The whole toy corridor; needs the repaired link to walk.",
         "corridor": "toy", "direction": "NB"},
        {"id": "toy-nb-broken", "name": "Toy Rd NB (unreachable)",
         "start_latlon": [LAT0 + 3.5 * DLAT, LON], "end_latlon": [LAT0 + 0.5 * DLAT, LON],
         "description": "Walks away from its target: a finding, never a ranking row.",
         "corridor": "toy", "direction": "SB"},
    ], "reporting_corridors": [
        {"id": "toy", "name": "Toy Rd", "description": "Both directions of Toy Rd."},
    ]}
    cat_path = tmp_path / "catalogue.json"
    cat_path.write_text(json.dumps(catalogue))

    repairs = pd.DataFrame({"segment": [1001], "old_next": pd.array([pd.NA], dtype="Int64"),
                            "new_next": [1002], "kind": ["fill"], "gap_m": [0.0],
                            "bearing_delta_deg": [0.0], "xdgroup": [5],
                            "road_name": ["Toy Rd"], "lanes": [2.0]})
    rep_path = tmp_path / "repairs.csv"
    with rep_path.open("w") as fh:
        fh.write("# radius_m: 25.0\n# n_override: 0\n")
        repairs.to_csv(fh, index=False)

    return {"db": tmp_path / "s.duckdb", "network": net_path, "catalogue": cat_path,
            "repairs": rep_path, "out": tmp_path / "out", "area": info["area_key"]}


def _args(district, **over):
    argv = ["--db", str(district["db"]), "--network", str(district["network"]),
            "--catalogue", str(district["catalogue"]),
            "--repairs", str(district["repairs"]), "--out-dir", str(district["out"])]
    for k, v in over.items():
        flag = "--" + k.replace("_", "-")
        argv += [flag] if v is True else [flag, str(v)]
    return rds.parse_args(argv)


# ---------------------------------------------------------------------------
# The composition
# ---------------------------------------------------------------------------
def test_end_to_end_ranks_the_resolved_corridor(district):
    out = rds.run(_args(district))
    ranking, resolution = out["ranking"], out["resolution"]

    # The corridor that resolves is ranked, keyed on its catalogue **id**, with the
    # full name carried beside it.
    assert set(ranking["corridor"]) == {"toy-nb"}
    assert set(ranking["corridor_name"]) == {"Toy Rd NB"}
    am = ranking[ranking["window"] == "am"].iloc[0]
    assert am["n_segments"] == 4 and am["n_observed"] == 4
    assert am["miles"] == pytest.approx(3.0)         # the end segments are trimmed
    assert am["tti"] == pytest.approx(2.0)           # 30 mph against a 60 mph reference
    # ...and it says it leaned on a repair to get there.
    assert resolution.set_index("id").loc["toy-nb", "n_repaired_links"] == 1


def test_a_corridor_that_did_not_resolve_is_reported_not_ranked(district):
    """The Item 36 rule applied to the output: a finding never appears in the
    ranking with blank metrics."""
    out = rds.run(_args(district))
    assert "toy-nb-broken" not in set(out["ranking"]["corridor"])
    res = out["resolution"].set_index("id")
    assert not res.loc["toy-nb-broken", "accepted"]
    assert res.loc["toy-nb-broken", "stop_reason"] == "dead_end"
    text = rds.summarise(out["ranking"], out["resolution"], top=10)
    assert "NOT RANKED" in text and "toy-nb-broken" in text
    assert "dead_end" in text


def test_without_repairs_the_corridor_becomes_a_finding(district):
    """``--no-repairs`` walks NextXDSegI exactly as published — and this network is
    published broken, so the run must report that rather than rank around it."""
    with pytest.raises(SystemExit, match="No catalogue entry resolved"):
        rds.run(_args(district, no_repairs=True))


def test_outputs_carry_the_basis_they_were_computed_under(district):
    out = rds.run(_args(district))
    written = out["written"]
    assert written["provenance"].exists() and written["corridor_rankings"].exists()
    assert written["kml"].exists()

    prov = json.loads(written["provenance"].read_text())
    assert prov["area_key"] == district["area"]
    assert prov["bin_minutes"] == 15 and prov["tz"] == rds.DEFAULT_TZ
    assert prov["cvalue_threshold"] == rds.DEFAULT_CVALUE
    assert prov["n_entries"] == 2 and prov["n_accepted"] == 1
    assert prov["findings"] == ["toy-nb-broken"]
    assert prov["repairs"]["n_repairs"] == 1 and prov["repairs"]["n_used"] == 1
    assert prov["repairs"]["rule"]["radius_m"] == "25.0"
    assert prov["aadt"] is None                      # none was given; none is claimed

    # The CSV carries it too — a ranking read off disk still states its basis.
    head = written["corridor_rankings"].read_text().splitlines()
    assert head[0].startswith("# generated_utc:")
    assert any(line.startswith("# cvalue_threshold:") for line in head[:40])
    from_disk = pd.read_csv(written["corridor_rankings"], comment="#")
    assert list(from_disk["corridor"].unique()) == ["toy-nb"]
    assert list(from_disk["corridor_name"].unique()) == ["Toy Rd NB"]


def test_reporting_corridor_view_groups_the_directions(district):
    """One corridor for reporting purposes is both directions — and only the
    direction that actually resolved can be in it."""
    out = rds.run(_args(district))
    grouped = out["grouped"]
    assert list(grouped["corridor_group"].unique()) == ["toy"]
    assert set(grouped["group_name"]) == {"Toy Rd"}
    row = grouped[grouped["window"] == "am"].iloc[0]
    # Only toy-nb resolved, so the group is one direction and says so rather than
    # quietly reporting a two-direction total built from one.
    assert row["n_entries"] == 1 and tuple(row["directions"]) == ("NB",)
    assert row["peak_direction"] == "NB"
    assert row["miles"] == pytest.approx(row["directional_miles"])
    assert out["written"]["reporting_corridor_rankings"].exists()
    text = rds.summarise(out["ranking"], out["resolution"], 10, grouped)
    assert "single worst peak window" in text

    prov = json.loads(out["written"]["provenance"].read_text())
    assert prov["reporting_corridors"]["n_groups"] == 1
    assert prov["reporting_corridors"]["ungrouped_entries"] == []


def test_the_reporting_table_totals_the_cells_it_prints(district):
    """The printed block and the totals row have to agree, or the table is asking to
    be taken on trust."""
    out = rds.run(_args(district))
    totals, breakout = out["totals"], out["breakout"]
    assert list(totals["corridor_group"]) == ["toy"]
    r = totals.iloc[0]
    cells = breakout.loc["toy"]
    assert len(cells) == 2                       # one direction x two peak windows
    assert r["delay_min"] == pytest.approx(cells["delay_min"].sum())
    # No AADT was joined here, so vhd stays NaN all the way up rather than being
    # summed into a confident 0.
    assert pd.isna(r["vhd"]) and cells["vhd"].isna().all()
    # Miles once per direction, not once per cell.
    assert r["directional_miles"] == pytest.approx(cells["miles"].max())
    assert r["delay_per_mile"] == pytest.approx(r["delay_min"] / r["directional_miles"])
    assert r["rank"] == 1 and not r["one_way_couplet"]
    assert pd.isna(r["rank_vhd"])                # no AADT -> no vhd -> no vhd rank
    assert out["written"]["corridor_peak_totals"].exists()
    assert out["written"]["corridor_breakout"].exists()

    text = rds.summarise(out["ranking"], out["resolution"], 10, out["grouped"],
                         totals, breakout)
    assert "ranked on delay_per_mile" in text
    assert "Toy Rd" in text and "am" in text and "pm" in text


def test_rank_by_is_settable_from_the_cli(district):
    assert rds.parse_args(["--db", "x"]).rank_by == "vhd_per_mile"
    out = rds.run(_args(district, rank_by="delay_min"))
    assert out["totals"].attrs["rank_by"] == "delay_min"
    prov = json.loads(out["written"]["provenance"].read_text())
    assert prov["reporting_corridors"]["ranked_on"] == "delay_min"
    assert prov["reporting_corridors"]["totalled_windows"] == ["am", "pm"]


def test_the_default_rank_falls_back_when_no_aadt_was_joined(district, capsys):
    """This fixture joins no AADT, so the default vehicle-hours-per-mile ranking is
    null throughout. The run falls back to the unweighted rate and **says so**,
    rather than printing catalogue order as if it were a ranking."""
    out = rds.run(_args(district))              # --rank-by defaults to vhd_per_mile
    assert out["totals"].attrs["rank_by"] == "delay_per_mile"
    assert not out["totals"].attrs["rank_metric_all_null"]
    assert out["totals"]["rank"].notna().all()
    assert "ranking on 'delay_per_mile' instead" in capsys.readouterr().out


def test_an_ungrouped_catalogue_still_prints_a_table(district, tmp_path):
    """The per-direction table is the whole report when no reporting corridors are
    declared — dropping it would leave an ungrouped run printing only its findings."""
    data = json.loads(Path(district["catalogue"]).read_text())
    del data["reporting_corridors"]
    for e in data["corridors"]:
        e.pop("corridor"), e.pop("direction")
    path = tmp_path / "ungrouped2.json"
    path.write_text(json.dumps(data))
    out = rds.run(_args(district, catalogue=path))
    text = rds.summarise(out["ranking"], out["resolution"], 10, None, None, None)
    assert "By direction" in text and "toy-nb" in text


def test_an_ungrouped_catalogue_still_runs_per_direction(district, tmp_path):
    """Grouping is additive: drop the reporting block and the run is what it was."""
    data = json.loads(Path(district["catalogue"]).read_text())
    del data["reporting_corridors"]
    for e in data["corridors"]:
        e.pop("corridor"), e.pop("direction")
    path = tmp_path / "ungrouped.json"
    path.write_text(json.dumps(data))
    out = rds.run(_args(district, catalogue=path))
    assert out["grouped"] is None and out["totals"] is None
    assert "reporting_corridor_rankings" not in out["written"]
    assert "corridor_peak_totals" not in out["written"]
    assert set(out["ranking"]["corridor"]) == {"toy-nb"}


def test_no_kml_skips_only_the_kml(district):
    out = rds.run(_args(district, no_kml=True))
    assert "kml" not in out["written"]
    assert out["written"]["corridor_rankings"].exists()


# ---------------------------------------------------------------------------
# The CLI
# ---------------------------------------------------------------------------
def test_windows_flag_selects_presets():
    args = rds.parse_args(["--db", "x", "--windows", "am,pm"])
    assert set(args.windows) == {"am", "pm"}
    assert rds.parse_args(["--db", "x"]).windows is None      # default: every preset


def test_area_resolution_by_key_and_by_name(district):
    con = store.connect(district["db"])
    try:
        assert rds.resolve_area(con, None) == district["area"]
        assert rds.resolve_area(con, district["area"]) == district["area"]
        name = store.list_areas(con).iloc[0]["area_name"]
        assert rds.resolve_area(con, name) == district["area"]
        with pytest.raises(SystemExit, match="No area"):
            rds.resolve_area(con, "not-an-area")
    finally:
        con.close()


def test_missing_repair_table_is_refused_not_ignored(tmp_path):
    """Silently screening without the repairs would rank 13 corridors where 20 were
    asked for, so an absent table stops the run."""
    with pytest.raises(SystemExit, match="No repair table"):
        rds.load_repairs(tmp_path / "nope.csv", True)
    assert rds.load_repairs(tmp_path / "nope.csv", False) is None

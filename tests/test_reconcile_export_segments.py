"""Tests for the export-segment reconciliation runner (ROADMAP Item 42).

Same rule as the screening runner's tests: this layer **composes**. What is pinned
here is the set arithmetic over the corridor id files — which is this script's own
work and is exactly what went wrong when 57 SH-55 ids never reached the master list
— and the wiring of the on-system pass. The classification itself is tested in
``test_aadt``.
"""
from __future__ import annotations

import sys
import zipfile
from pathlib import Path

import pandas as pd
import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import reconcile_export_segments as rec          # noqa: E402

from inrix_tools import aadt, store              # noqa: E402

TZ = "America/Denver"
LON, LAT0, DLAT = -116.3545, 43.60, 0.01
IN_EXPORT = [1000, 1001]          # requested and returned
NEVER_RETURNED = [1002]           # requested, no rows
NEVER_REQUESTED = [1003]          # in a corridor file, never reached the master list
ABSENT_ON_ROUTE = 1004            # on-system, in no corridor list at all
ABSENT_OFF_ROUTE = 1005           # a drive running beside it


# ---------------------------------------------------------------------------
# A miniature district: an export, corridor id files, a network, an AADT layer
# ---------------------------------------------------------------------------
@pytest.fixture
def district(tmp_path):
    import geopandas as gpd
    from shapely.geometry import LineString

    from inrix_tools import geometry as geo_mod

    stamps = pd.date_range("2026-03-03 00:00", periods=96, freq="15min", tz=TZ)
    rows = [{"Segment ID": sid, "Date Time": t.isoformat(),
             "Speed(miles/hour)": 55.0, "Travel Time(Minutes)": 1.0,
             "Ref Speed(miles/hour)": 60.0, "CValue": 95}
            for sid in IN_EXPORT for t in stamps]
    meta = pd.DataFrame({"Segment ID": IN_EXPORT, "Road": ["Toy Rd"] * 2,
                         "Direction": ["N"] * 2, "Miles": [1.0] * 2,
                         "Combined": ["Toy Rd N"] * 2})
    zpath = tmp_path / "Toy_2026_15_min.zip"
    with zipfile.ZipFile(zpath, "w") as z:
        z.writestr("data.csv", pd.DataFrame(rows).to_csv(index=False))
        z.writestr("metadata.csv", meta.to_csv(index=False))
    con = store.connect(tmp_path / "s.duckdb")
    info = store.ingest_export_streaming(con, zpath)
    con.close()

    highways = tmp_path / "highways"
    highways.mkdir()
    (highways / "SH-99_ALL.txt").write_text(
        ",".join(str(s) for s in IN_EXPORT + NEVER_RETURNED + NEVER_REQUESTED) + ",")
    (highways / "SH-99_NB.txt").write_text(",".join(str(s) for s in IN_EXPORT))
    (highways / "SH-99_SB.txt").write_text(
        ",".join(str(s) for s in NEVER_RETURNED + NEVER_REQUESTED))
    # the master list: everything the corridor file holds *except* one id
    (highways / "District_3_ALL_Highways.txt").write_text(
        ",".join(str(s) for s in IN_EXPORT + NEVER_RETURNED))

    def line(i):
        return LineString([(LON, LAT0 + i * DLAT), (LON, LAT0 + (i + 1) * DLAT)])

    ids = IN_EXPORT + NEVER_RETURNED + NEVER_REQUESTED + [ABSENT_ON_ROUTE,
                                                          ABSENT_OFF_ROUTE]
    geoms = [line(i) for i in range(4)] + [
        line(4),                                            # on the route
        LineString([(LON + 0.0002, LAT0 + 4 * DLAT),        # ~16 m east, parallel
                    (LON + 0.0002, LAT0 + 5 * DLAT)]),
    ]
    net = gpd.GeoDataFrame(
        {"XDSegID": pd.array(ids, dtype="Int64"),
         "NextXDSegI": pd.array([1001, 1002, 1003, 1004, pd.NA, pd.NA], dtype="Int64"),
         "XDGroup": pd.array([5] * 6, dtype="Int64"), "Bearing": ["N"] * 6,
         "RoadName": ["Toy Rd"] * 5 + ["Sleepy Hollow Dr"],
         "RoadNumber": [None] * 6, "Lanes": [2.0] * 6, "Miles": [1.0] * 6,
         "FRC": [3] * 6},
        geometry=geoms, crs=geo_mod.WGS84)
    net_path = tmp_path / "net.geoparquet"
    net.to_parquet(net_path)

    layer = gpd.GeoDataFrame(
        {"Year": [2024], "AADT": [12000.0], "RouteID": ["09999AOH000"],
         "Descriptio": ["TOY RD (SH-99)"], "Commercial": [500],
         "geometry": [LineString([(LON, LAT0), (LON, LAT0 + 6 * DLAT)])]},
        crs=geo_mod.WGS84)
    aadt_path = tmp_path / "aadt.geoparquet"
    layer.to_parquet(aadt_path)

    return {"db": tmp_path / "s.duckdb", "highways": highways, "network": net_path,
            "aadt": aadt_path, "out": tmp_path / "out", "area": info["area_key"]}


def _args(district, **over):
    argv = ["--db", str(district["db"]),
            "--highways-dir", str(district["highways"]),
            "--network", str(district["network"]),
            "--network-cache", str(district["network"]),
            "--out-dir", str(district["out"]),
            # The fixture layer holds 2024 records; the default year is 2025 since
            # Item 52.
            "--aadt-year", "2024"]
    for k, v in over.items():
        flag = "--" + k.replace("_", "-")
        argv += [flag] if v is True else [flag, str(v)]
    return rec.build_parser().parse_args(argv)


# ---------------------------------------------------------------------------
# Section 1 — the corridor inventory
# ---------------------------------------------------------------------------
def test_read_id_file_handles_wrapping_and_a_trailing_comma(tmp_path):
    p = tmp_path / "ids.txt"
    p.write_text("1,2,\n3,4,\n")
    assert rec.read_id_file(p) == [1, 2, 3, 4]


def test_inventory_separates_never_requested_from_never_returned(district):
    out = rec.run(_args(district))
    inv = out["inventory"]
    row = inv[inv["file"] == "SH-99_ALL.txt"].iloc[0]
    # the id that no master list ever collected — the SH-55 failure in miniature
    assert row["missing_from_master"] == 1
    # requested and never returned, plus that one: both absent from the export
    assert row["not_observed"] == 2
    assert row["missing_ids"] == str(NEVER_REQUESTED[0])
    assert row["directional_mismatch"] == 0

    totals = inv.attrs["totals"]
    assert totals["n_master"] == 3 and totals["n_observed"] == 2
    assert totals["requested_not_returned"] == 1     # 1002
    assert totals["observed_not_requested"] == 0     # nothing arrived unasked


def test_directional_files_that_do_not_add_up_are_flagged(district):
    (district["highways"] / "SH-99_SB.txt").write_text(str(NEVER_RETURNED[0]))
    inv = rec.reconcile_inventory(district["highways"],
                                  rec.read_id_file(district["highways"]
                                                   / "District_3_ALL_Highways.txt"),
                                  IN_EXPORT)
    assert inv.iloc[0]["directional_mismatch"] == 1   # the NB/SB pair lost an id


def test_the_export_is_read_from_the_observations_not_the_metadata(district):
    """An export is split into parts **by segment** and the streaming ingest reads
    metadata from ``source`` alone, so ``load_metadata`` under-reports what the store
    holds — the reconciliation has to read the observations
    (``store.area_segments``)."""
    con = store.connect(district["db"])
    try:
        con.execute(f'DELETE FROM "meta_{district["area"]}" '
                    f'WHERE "Segment ID" = {IN_EXPORT[1]}')
        assert len(store.load_metadata(con, district["area"])) == 1
        assert store.area_segments(con, district["area"]) == IN_EXPORT
    finally:
        con.close()
    assert rec.run(_args(district))["inventory"].attrs["totals"]["n_observed"] == 2


# ---------------------------------------------------------------------------
# Section 2 — the on-system pass
# ---------------------------------------------------------------------------
def test_on_system_pass_finds_the_absent_route_and_not_its_neighbour(district):
    out = rec.run(_args(district, aadt=district["aadt"], aadt_cache=district["aadt"]))
    classified = out["classified"]
    assert set(classified.index) == {NEVER_RETURNED[0], NEVER_REQUESTED[0],
                                     ABSENT_ON_ROUTE, ABSENT_OFF_ROUTE}
    on = classified[classified[aadt.ON_SYSTEM_COL]]
    assert ABSENT_ON_ROUTE in on.index
    assert ABSENT_OFF_ROUTE not in on.index          # the drive beside the route
    assert classified.loc[ABSENT_OFF_ROUTE, aadt.ON_SYSTEM_CATEGORY_COL] \
        .startswith("a neighbouring road")

    # which corridor list already names a candidate is the difference between a
    # delivery question and a road nobody asked about
    assert classified.loc[NEVER_REQUESTED[0], "inventory_files"] == "SH-99_ALL.txt"
    assert classified.loc[ABSENT_ON_ROUTE, "inventory_files"] == ""

    by_road = out["candidates"]
    assert set(by_road["road"]) == {"Toy Rd"}
    assert by_road["segments"].sum() == len(on)
    assert "route 99" in set(by_road["route"])


def test_without_an_aadt_source_only_the_inventory_runs(district):
    out = rec.run(_args(district))
    assert out["classified"] is None
    assert "not run" in out["report"]
    written = out["written"]
    assert "absent_segments" not in written
    assert Path(written["report"]).exists() and Path(written["inventory"]).exists()


def test_the_report_and_provenance_carry_what_was_applied(district):
    out = rec.run(_args(district, aadt=district["aadt"], aadt_cache=district["aadt"]))
    report = out["report"]
    assert "EXPORT SEGMENT RECONCILIATION" in report
    assert "PASTE-READY" in report and str(ABSENT_ON_ROUTE) in report
    prov = pd.read_json(Path(out["written"]["provenance"]), typ="series")
    assert prov["aadt"]["on_system"]["min_coverage"] == aadt.DEFAULT_ON_SYSTEM_COVERAGE
    assert prov["inventory_totals"]["observed_not_requested"] == 0


def test_candidates_by_road_is_empty_when_nothing_qualifies(district):
    import geopandas as gpd

    classified = rec.annotate_inventory(
        rec.classify_absent(
            gpd.read_parquet(district["network"]),
            # nothing absent: every segment is "in the export"
            [1000, 1001, 1002, 1003, ABSENT_ON_ROUTE, ABSENT_OFF_ROUTE],
            district["aadt"], year=2024, aadt_cache=district["aadt"],
            max_distance_m=60.0, min_coverage=aadt.DEFAULT_ON_SYSTEM_COVERAGE,
            on_system_distance_m=aadt.DEFAULT_ON_SYSTEM_DISTANCE_M),
        rec.inventory_membership(district["highways"]))
    assert rec.candidates_by_road(classified).empty


# ---------------------------------------------------------------------------
# Any district; the add/drop lists a revised inventory implies (Item 48)
# ---------------------------------------------------------------------------
def test_master_lists_of_any_district_are_not_corridor_files(tmp_path):
    for name in ("District_2_ALL_Highways.txt", "District_2_Pacific_ALL_Highways.txt",
                 "Statewide_ALL_Highways.txt", "US-12_ALL.txt", "US-95_Pacific_ALL.txt"):
        (tmp_path / name).write_text("1")
    assert [p.name for p in rec.corridor_files(tmp_path)] == ["US-12_ALL.txt",
                                                             "US-95_Pacific_ALL.txt"]
    assert rec.master_name(2) == "District_2_ALL_Highways.txt"


def test_request_changes_separates_new_requests_from_ones_that_came_back_empty():
    changes = rec.request_changes(master_ids=[1, 2, 3, 4], observed=[1, 5],
                                  previous_master_ids=[1, 3, 5])
    assert changes == {"add": [2, 4], "still_empty": [3], "drop": [5]}


def test_district_and_previous_master_write_the_add_and_drop_lists(district, tmp_path):
    highways = district["highways"]
    (highways / "District_3_ALL_Highways.txt").rename(highways / "District_5_ALL_Highways.txt")
    revised = IN_EXPORT[:1] + NEVER_RETURNED + [ABSENT_ON_ROUTE]      # drops 1001
    (highways / "District_5_ALL_Highways.txt").write_text(",".join(map(str, revised)))
    previous = tmp_path / "previous.txt"
    previous.write_text(",".join(str(s) for s in IN_EXPORT + NEVER_RETURNED))
    args = _args(district, previous_master=previous)
    args.district = 5
    out = rec.run(args)
    assert out["changes"] == {"add": [ABSENT_ON_ROUTE], "still_empty": NEVER_RETURNED,
                              "drop": [1001]}
    assert Path(out["written"]["segments_to_add"]).read_text() == str(ABSENT_ON_ROUTE)
    assert Path(out["written"]["segments_to_drop"]).read_text() == "1001"
    assert "PASTE-READY ADD LIST" in out["report"]

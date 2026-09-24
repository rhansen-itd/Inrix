"""Tests for Item 43 — recurring-congestion corridor extraction.

The load-bearing checks: a synthetic corridor congested only in its middle
recovers exactly that middle; a segment congested 3 of 20 days does NOT qualify
while the same mean spread over every day does; a one-segment gap bridges
within tolerance; a run does not continue past a carriageway change; an endpoint
within tolerance of a junction snaps; and candidates are rejected by
``parse_catalogue`` until described.
"""
from __future__ import annotations

import zipfile
from datetime import date, timedelta

import geopandas as gpd
import pandas as pd
import pytest
from shapely.geometry import LineString

from inrix_tools import corridors, geometry, screen, store
from inrix_tools.io import SEGMENT_COL

duckdb = pytest.importorskip("duckdb")


# ============================================================================
# Synthetic export — a 5-segment corridor, 20 weekdays, 15-min bins.
# Segments 1001-1005, connected head to tail, northbound.
#
# Segment layout:
#   1001 (rural, fast)  →  1002 (CONGESTED middle) →  1003 (CONGESTED middle)
#   →  1004 (gap, fast)  →  1005 (rural, fast)
#
# Travel times:
#   1001, 1004, 1005: 1.0 min (speed = 60 mph, TT/free-flow = 1.0, TTI = 1.0)
#   1002, 1003: 2.0 min (speed = 30 mph, TTI = 2.0 > 1.25 threshold)
# ============================================================================
WEEK_START = date(2026, 3, 9)   # Monday, inside MDT
N_DAYS = 28                      # 4 weeks = 20 weekdays
SEG_IDS = [1001, 1002, 1003, 1004, 1005]
TZ = "America/Denver"
REF_SPEED = 60
SEG_MILES = 1.0

# Congested segments always have TTI = 2.0 (every day)
CONGESTED_SEGS = {1002, 1003}

_DATA_HDR = (
    "Date Time,Segment ID,UTC Date Time,Speed(miles/hour),"
    "Hist Av Speed(miles/hour),Ref Speed(miles/hour),Travel Time(Minutes),"
    "CValue,Pct Score30,Pct Score20,Pct Score10,Road Closure,Corridor/Region Name\n"
)


def _travel_time(seg_id: int) -> float:
    if seg_id in CONGESTED_SEGS:
        return 2.0   # TTI = 2.0
    return 1.0       # TTI = 1.0


def _rows():
    for d in range(N_DAYS):
        day = WEEK_START + timedelta(days=d)
        for step in range(96):
            hour = step / 4.0
            hh, mm = int(hour), round((hour - int(hour)) * 60)
            local = f"{day.isoformat()}T{hh:02d}:{mm:02d}:00-06:00"
            for seg in SEG_IDS:
                tt = _travel_time(seg)
                speed = round(SEG_MILES / tt * 60.0, 1)
                yield (f"{local},{seg},2026-01-01T00:00:00Z,{speed},{speed},"
                       f"{REF_SPEED},{tt},100,100,0,0,F,D3\n")


def _metadata_csv():
    lines = [
        (
            "Segment ID,Road,Direction,Start Latitude,End Latitude,Start Longitude,"
            "End Longitude,State/Region,District,Postal Code,Segment Length(Miles),"
            "Intersection"
        ),
    ]
    for seg in SEG_IDS:
        lines.append(f"{seg},Toy Rd,N,43.60,43.61,-116.3545,-116.3545,Idaho,Ada,"
                     f"83702,{SEG_MILES},X{seg}")
    return "\n".join(lines) + "\n"


@pytest.fixture(scope="module")
def export_zip(tmp_path_factory):
    name = "Toy_2026-03-09_to_2026-04-05_15_min"
    zpath = tmp_path_factory.mktemp("recurrence") / f"{name}_part_1.zip"
    with zipfile.ZipFile(zpath, "w") as zf:
        zf.writestr(f"{name}/data.csv", _DATA_HDR + "".join(_rows()))
        zf.writestr(f"{name}/metadata.csv", _metadata_csv())
    return zpath


@pytest.fixture
def area(export_zip):
    con = store.connect(":memory:")
    key = store.ingest_export(con, export_zip)["area_key"]
    yield con, key
    con.close()


# A 5-segment northbound network with XDGroup, Bearing, RoadNumber, RoadName
def _toy_network():
    lon, lat0, dlat = -116.3545, 43.60, 0.01
    return gpd.GeoDataFrame(
        {
            "XDSegID": pd.array(SEG_IDS, dtype="Int64"),
            "NextXDSegI": pd.array(SEG_IDS[1:] + [pd.NA], dtype="Int64"),
            "PreviousXD": pd.array([pd.NA] + SEG_IDS[:-1], dtype="Int64"),
            "Miles": [SEG_MILES] * len(SEG_IDS),
            "Bearing": ["N"] * len(SEG_IDS),
            "XDGroup": pd.array([100] * len(SEG_IDS), dtype="Int64"),
            "RoadNumber": ["55", "55", "55", "55", "55"],
            "RoadName": ["Toy Rd", "Toy Rd", "Toy Rd", "Toy Rd", "Toy Rd"],
            "FRC": [3] * len(SEG_IDS),
            "Lanes": [2.0] * len(SEG_IDS),
            "StartLat": [lat0 + i * dlat for i in range(len(SEG_IDS))],
            "StartLong": [lon] * len(SEG_IDS),
            "EndLat": [lat0 + (i + 1) * dlat for i in range(len(SEG_IDS))],
            "EndLong": [lon] * len(SEG_IDS),
            "geometry": [
                LineString([(lon, lat0 + i * dlat), (lon, lat0 + (i + 1) * dlat)])
                for i in range(len(SEG_IDS))
            ],
        },
        crs=geometry.WGS84,
    )


# ============================================================================
# segment_recurrence tests
# ============================================================================
class TestSegmentRecurrence:
    def test_recurrence_reflects_daily_tti(self, area):
        """Congested segments (TTI=2.0 every weekday) have recurrence 1.0;
        non-congested (TTI=1.0) have recurrence 0.0."""
        con, key = area
        rec = screen.segment_recurrence(con, key, windows=["am"],
                                        tti_threshold=1.25)
        # Segments 1002, 1003 are congested every day → recurrence = 1.0
        assert rec.at[1002, "am_recurrence"] == pytest.approx(1.0)
        assert rec.at[1003, "am_recurrence"] == pytest.approx(1.0)
        # Segments 1001, 1004, 1005 are never congested → recurrence = 0.0
        assert rec.at[1001, "am_recurrence"] == pytest.approx(0.0)
        assert rec.at[1005, "am_recurrence"] == pytest.approx(0.0)

    def test_recurrence_attrs_carry_threshold(self, area):
        con, key = area
        rec = screen.segment_recurrence(con, key, tti_threshold=1.50)
        assert rec.attrs["tti_threshold"] == 1.50
        assert rec.attrs["area_key"] == key

    def test_recurrence_mean_tti(self, area):
        """Mean TTI for the congested segments should be ~2.0."""
        con, key = area
        rec = screen.segment_recurrence(con, key, windows=["am"],
                                        tti_threshold=1.25)
        assert rec.at[1002, "am_mean_tti"] == pytest.approx(2.0, abs=0.1)
        assert rec.at[1001, "am_mean_tti"] == pytest.approx(1.0, abs=0.1)

    def test_recurrence_distinguishes_construction_fortnight_from_daily_queue(self, tmp_path):
        """The core Item 43 thesis: a segment congested 3 of 20 days (construction/incident)
        does NOT qualify under recurrence, while a segment with the same mean TTI spread
        evenly across all 20 days DOES qualify.

        Both have mean TTI = 1.30, but recurrence separates them: 0.15 vs 1.00."""
        # 4 weeks = 20 weekdays. 15-min bins in AM peak (7:00-9:00 = 8 bins/day).
        # Seg 2001 (incident/construction fortnight):
        #   3 days with TTI = 3.0 (TT=3.0 min, speed=20 mph)
        #   17 days with TTI = 1.0 (TT=1.0 min, speed=60 mph)
        #   Overall mean TTI = (3*3.0 + 17*1.0) / 20 = 1.30
        # Seg 2002 (daily commute queue):
        #   20 days with TTI = 1.30 (TT=1.30 min, speed=46.15 mph)
        #   Overall mean TTI = 1.30
        rows = []
        for d in range(28):  # 28 days
            day = WEEK_START + timedelta(days=d)
            is_weekday = day.weekday() < 5
            weekday_idx = d - (d // 7) * 2 if is_weekday else None
            for step in range(96):
                hour = step / 4.0
                if not (7 <= hour < 9):
                    continue  # AM peak only
                hh, mm = int(hour), round((hour - int(hour)) * 60)
                local = f"{day.isoformat()}T{hh:02d}:{mm:02d}:00-06:00"

                # Seg 2001
                if is_weekday and weekday_idx is not None and weekday_idx < 3:
                    tt_1, sp_1 = 3.0, 20.0
                else:
                    tt_1, sp_1 = 1.0, 60.0
                rows.append(f"{local},2001,2026-01-01T00:00:00Z,{sp_1},{sp_1},60,{tt_1},100,100,0,0,F,D3\n")

                # Seg 2002
                if is_weekday:
                    tt_2, sp_2 = 1.30, 46.1538
                else:
                    tt_2, sp_2 = 1.0, 60.0
                rows.append(f"{local},2002,2026-01-01T00:00:00Z,{sp_2},{sp_2},60,{tt_2},100,100,0,0,F,D3\n")

        zpath = tmp_path / "comp_export_part_1.zip"
        meta = ("Segment ID,Road,Direction,Start Latitude,End Latitude,Start Longitude,"
                "End Longitude,State/Region,District,Postal Code,Segment Length(Miles),Intersection\n"
                "2001,Road A,N,43.6,43.61,-116.3,-116.3,Idaho,Ada,83702,1.0,X1\n"
                "2002,Road B,N,43.6,43.61,-116.3,-116.3,Idaho,Ada,83702,1.0,X2\n")
        with zipfile.ZipFile(zpath, "w") as zf:
            zf.writestr("comp/data.csv", _DATA_HDR + "".join(rows))
            zf.writestr("comp/metadata.csv", meta)

        con = store.connect(":memory:")
        key = store.ingest_export(con, zpath)["area_key"]
        rec = screen.segment_recurrence(con, key, windows=["am"], tti_threshold=1.25)
        con.close()

        # Both have approximately the same mean TTI
        assert rec.at[2001, "am_mean_tti"] == pytest.approx(1.30, abs=0.05)
        assert rec.at[2002, "am_mean_tti"] == pytest.approx(1.30, abs=0.05)

        # But completely different recurrence!
        assert rec.at[2001, "am_n_congested"] == 3
        assert rec.at[2001, "am_recurrence"] == pytest.approx(3 / 20)  # 0.15 -> rejected by 0.50 threshold
        assert rec.at[2002, "am_n_congested"] == 20
        assert rec.at[2002, "am_recurrence"] == pytest.approx(1.0)     # 1.00 -> accepted by 0.50 threshold


# ============================================================================
# extract_congestion_runs tests
# ============================================================================
class TestExtractCongestionRuns:
    def _make_recurrence(self, qualifying_segs, all_segs=None, threshold=0.8,
                         non_qualifying_tti=1.0, qualifying_tti=2.0):
        """Build a synthetic recurrence frame."""
        if all_segs is None:
            all_segs = SEG_IDS
        data = {}
        for seg in all_segs:
            rec_val = threshold if seg in qualifying_segs else 0.0
            data[seg] = {"am_recurrence": rec_val,
                         "am_mean_tti": qualifying_tti if seg in qualifying_segs
                         else non_qualifying_tti,
                         "am_n_weekdays": 20, "am_n_congested": int(20 * rec_val)}
        return pd.DataFrame.from_dict(data, orient="index").rename_axis(SEGMENT_COL)

    def test_congested_middle_recovers_exactly_that_middle(self):
        """Segments 1002-1003 are congested; the run should contain exactly them."""
        rec = self._make_recurrence({1002, 1003})
        net = _toy_network()
        runs = screen.extract_congestion_runs(rec, net, window="am",
                                              recurrence_threshold=0.5)
        assert len(runs) == 1
        assert set(runs[0].qualifying_ids) == {1002, 1003}
        assert runs[0].gaps_bridged == 0

    def test_single_segment_run(self):
        """Only 1002 qualifies → single-segment run."""
        rec = self._make_recurrence({1002})
        net = _toy_network()
        runs = screen.extract_congestion_runs(rec, net, window="am",
                                              recurrence_threshold=0.5)
        assert len(runs) == 1
        assert runs[0].qualifying_ids == (1002,)
        assert runs[0].n_segments == 1

    def test_gap_bridges_within_tolerance(self):
        """Segments 1002 and 1004 qualify, with 1003 as a non-qualifying gap.
        Gap = 1 segment, 1.0 mile — should bridge if tolerance allows."""
        # Make 1002 and 1004 congested, 1003 NOT congested
        rec = self._make_recurrence({1002, 1004})
        net = _toy_network()
        runs = screen.extract_congestion_runs(
            rec, net, window="am", recurrence_threshold=0.5,
            gap_tolerance_segs=1, gap_tolerance_miles=2.0)
        assert len(runs) == 1
        run = runs[0]
        assert 1002 in run.qualifying_ids
        assert 1004 in run.qualifying_ids
        assert 1003 in run.gap_segments
        assert run.gaps_bridged == 1

    def test_gap_too_large_splits_run(self):
        """Segments 1001 and 1004 qualify, with two non-qualifying gaps (1002, 1003).
        Gap = 2 segments → should NOT bridge with tolerance of 1."""
        rec = self._make_recurrence({1001, 1004})
        net = _toy_network()
        runs = screen.extract_congestion_runs(
            rec, net, window="am", recurrence_threshold=0.5,
            gap_tolerance_segs=1, gap_tolerance_miles=2.0)
        assert len(runs) == 2  # two separate runs

    def test_run_does_not_cross_xdgroup_boundary(self):
        """If segment 1003 is in a different XDGroup, the run should stop there."""
        net = _toy_network()
        # Change 1003's XDGroup to 200
        net.loc[net["XDSegID"] == 1003, "XDGroup"] = 200
        rec = self._make_recurrence({1002, 1003})
        runs = screen.extract_congestion_runs(rec, net, window="am",
                                              recurrence_threshold=0.5)
        # 1002 and 1003 are in different groups → separate runs
        assert len(runs) == 2

    def test_no_qualifying_segments_returns_empty(self):
        rec = self._make_recurrence(set())
        net = _toy_network()
        runs = screen.extract_congestion_runs(rec, net, window="am",
                                              recurrence_threshold=0.5)
        assert runs == []

    def test_road_label_reads_from_network(self):
        rec = self._make_recurrence({1002, 1003})
        net = _toy_network()
        runs = screen.extract_congestion_runs(rec, net, window="am",
                                              recurrence_threshold=0.5)
        assert len(runs) == 1
        assert "Toy Rd" in runs[0].road_label
        assert runs[0].road_numbers == ("55",)

    def test_on_system_filter_excludes_off_system_segments(self):
        """Passing on_system ID set restricts qualifying segments to only on-system."""
        rec = self._make_recurrence({1002, 1003})
        net = _toy_network()
        # Only 1003 is on-system
        runs = screen.extract_congestion_runs(rec, net, window="am",
                                              on_system={1003})
        assert len(runs) == 1
        assert runs[0].qualifying_ids == (1003,)
        assert 1002 not in runs[0].qualifying_ids

    def test_on_system_dataframe_filter(self):
        """Passing on_system as a DataFrame (e.g. from classify_on_system)."""
        rec = self._make_recurrence({1002, 1003})
        net = _toy_network()
        # 1002 is on-system; 1003 is off-system
        df = pd.DataFrame(
            {"on_system": [False, True, False, False, False]},
            index=pd.Index(SEG_IDS, name=SEGMENT_COL),
        )
        runs = screen.extract_congestion_runs(rec, net, window="am",
                                              on_system=df)
        assert len(runs) == 1
        assert runs[0].qualifying_ids == (1002,)


# ============================================================================
# tidy_run_endpoints tests
# ============================================================================
class TestTidyRunEndpoints:
    def _make_junction_network(self):
        """6-segment network: 1001-1005 on route 55, then 1006 on route 44
        (junction) connected after 1005."""
        net = _toy_network()
        lon, dlat = -116.3545, 0.01
        lat6 = 43.60 + 5 * dlat
        junction = gpd.GeoDataFrame(
            {
                "XDSegID": pd.array([1006], dtype="Int64"),
                "NextXDSegI": pd.array([pd.NA], dtype="Int64"),
                "PreviousXD": pd.array([1005], dtype="Int64"),
                "Miles": [1.0],
                "Bearing": ["N"],
                "XDGroup": pd.array([200], dtype="Int64"),
                "RoadNumber": ["44"],
                "RoadName": ["Cross St"],
                "FRC": [3],
                "Lanes": [2.0],
                "StartLat": [lat6],
                "StartLong": [lon],
                "EndLat": [lat6 + dlat],
                "EndLong": [lon],
                "geometry": [LineString([(lon, lat6), (lon, lat6 + dlat)])],
            },
            crs=geometry.WGS84,
        )
        # Wire 1005's NextXDSegI to 1006
        net.loc[net["XDSegID"] == 1005, "NextXDSegI"] = 1006
        full = pd.concat([net, junction], ignore_index=True)
        return full

    def test_endpoint_within_tolerance_snaps(self):
        net = self._make_junction_network()
        run = screen.CongestionRun(
            segment_ids=(1003, 1004, 1005),
            miles=(1.0, 1.0, 1.0),
            total_miles=3.0,
            qualifying_ids=(1003, 1004, 1005),
            gap_segments=(),
            gaps_bridged=0,
            gap_miles=0.0,
            bearing="N",
            xdgroup=100,
            road_label="Toy Rd (55)",
            road_numbers=("55",),
            window="am",
        )
        result = screen.tidy_run_endpoints([run], net,
                                           snap_tolerance_miles=2.0)
        assert len(result) == 1
        r = result[0]
        # End should snap to the junction with Route 44
        assert r["end_snapped_to"] is not None
        assert "Cross St" in r["end_snapped_to"] or "44" in r["end_snapped_to"]
        assert r["end_snap_distance_miles"] > 0

    def test_endpoint_beyond_tolerance_does_not_snap(self):
        net = self._make_junction_network()
        run = screen.CongestionRun(
            segment_ids=(1002, 1003),
            miles=(1.0, 1.0),
            total_miles=2.0,
            qualifying_ids=(1002, 1003),
            gap_segments=(),
            gaps_bridged=0,
            gap_miles=0.0,
            bearing="N",
            xdgroup=100,
            road_label="Toy Rd (55)",
            road_numbers=("55",),
            window="am",
        )
        # Junction is 3 miles away; tolerance is 0.5
        result = screen.tidy_run_endpoints([run], net,
                                           snap_tolerance_miles=0.5)
        r = result[0]
        assert r["end_snapped_to"] is None


# ============================================================================
# pair_directions tests
# ============================================================================
class TestPairDirections:
    def test_opposing_runs_are_paired(self):
        """Two runs with opposite bearings and same road number are paired."""
        run_nb = screen.CongestionRun(
            segment_ids=(1002,), miles=(1.0,), total_miles=1.0,
            qualifying_ids=(1002,), gap_segments=(), gaps_bridged=0,
            gap_miles=0.0, bearing="N", xdgroup=100,
            road_label="Toy Rd", road_numbers=("55",), window="am",
        )
        run_sb = screen.CongestionRun(
            segment_ids=(2002,), miles=(1.0,), total_miles=1.0,
            qualifying_ids=(2002,), gap_segments=(), gaps_bridged=0,
            gap_miles=0.0, bearing="S", xdgroup=200,
            road_label="Toy Rd", road_numbers=("55",), window="am",
        )
        net = _toy_network()
        result = screen.pair_directions([run_nb, run_sb], net)
        assert result[0]["paired"] is True
        assert result[0]["direction"] == "NB"
        assert result[1]["paired"] is True
        assert result[1]["direction"] == "SB"

    def test_unpaired_when_no_counterpart(self):
        run = screen.CongestionRun(
            segment_ids=(1002,), miles=(1.0,), total_miles=1.0,
            qualifying_ids=(1002,), gap_segments=(), gaps_bridged=0,
            gap_miles=0.0, bearing="N", xdgroup=100,
            road_label="Toy Rd", road_numbers=("55",), window="am",
        )
        net = _toy_network()
        result = screen.pair_directions([run], net)
        assert result[0]["paired"] is False
        assert result[0]["direction"] == "NB"


# ============================================================================
# emit_candidates tests
# ============================================================================
class TestEmitCandidates:
    def test_candidates_lack_description(self):
        """Candidates must have no 'description' key so parse_catalogue refuses them."""
        net = _toy_network()
        run = screen.CongestionRun(
            segment_ids=(1002, 1003), miles=(1.0, 1.0), total_miles=2.0,
            qualifying_ids=(1002, 1003), gap_segments=(), gaps_bridged=0,
            gap_miles=0.0, bearing="N", xdgroup=100,
            road_label="Toy Rd (55)", road_numbers=("55",), window="am",
            mean_recurrence=0.8, mean_tti=2.0,
        )
        cands = screen.emit_candidates([run], net, window="am")
        assert len(cands) == 1
        c = cands[0]
        assert "description" not in c
        assert "id" in c
        assert "name" in c
        assert "start_latlon" in c
        assert "end_latlon" in c

    def test_parse_catalogue_rejects_candidates(self):
        """A candidate without a description is refused by parse_catalogue."""
        net = _toy_network()
        run = screen.CongestionRun(
            segment_ids=(1002, 1003), miles=(1.0, 1.0), total_miles=2.0,
            qualifying_ids=(1002, 1003), gap_segments=(), gaps_bridged=0,
            gap_miles=0.0, bearing="N", xdgroup=100,
            road_label="Toy Rd (55)", road_numbers=("55",), window="am",
            mean_recurrence=0.8, mean_tti=2.0,
        )
        cands = screen.emit_candidates([run], net, window="am")
        with pytest.raises(ValueError, match="missing"):
            corridors.parse_catalogue({"corridors": cands})

    def test_candidate_carries_metadata(self):
        net = _toy_network()
        run = screen.CongestionRun(
            segment_ids=(1002, 1003), miles=(1.0, 1.0), total_miles=2.0,
            qualifying_ids=(1002, 1003), gap_segments=(), gaps_bridged=0,
            gap_miles=0.0, bearing="N", xdgroup=100,
            road_label="Toy Rd (55)", road_numbers=("55",), window="am",
            mean_recurrence=0.8, mean_tti=2.0,
        )
        cands = screen.emit_candidates([run], net, window="am")
        c = cands[0]
        assert c["_recurrence"] == pytest.approx(0.8)
        assert c["_mean_tti"] == pytest.approx(2.0)
        assert c["_total_miles"] == pytest.approx(2.0)
        assert c["_window"] == "am"

    def test_emit_candidates_on_system_only(self):
        """on_system_only filters out runs that carry no route numbers."""
        net = _toy_network()
        run_state = screen.CongestionRun(
            segment_ids=(1002,), miles=(1.0,), total_miles=1.0,
            qualifying_ids=(1002,), gap_segments=(), gaps_bridged=0,
            gap_miles=0.0, bearing="N", xdgroup=100,
            road_label="Toy Rd (55)", road_numbers=("55",), window="am",
        )
        run_county = screen.CongestionRun(
            segment_ids=(1003,), miles=(1.0,), total_miles=1.0,
            qualifying_ids=(1003,), gap_segments=(), gaps_bridged=0,
            gap_miles=0.0, bearing="N", xdgroup=100,
            road_label="County Line Rd", road_numbers=(), window="am",
        )
        cands = screen.emit_candidates([run_state, run_county], net,
                                       window="am", on_system_only=True)
        assert len(cands) == 1
        assert "55" in cands[0]["id"]

    def test_emit_candidates_geometry_fallback(self):
        """If StartLat/EndLat columns are missing from network, coords are read from geometry."""
        net = _toy_network().drop(columns=["StartLat", "StartLong", "EndLat", "EndLong"])
        run = screen.CongestionRun(
            segment_ids=(1002, 1003), miles=(1.0, 1.0), total_miles=2.0,
            qualifying_ids=(1002, 1003), gap_segments=(), gaps_bridged=0,
            gap_miles=0.0, bearing="N", xdgroup=100,
            road_label="Toy Rd (55)", road_numbers=("55",), window="am",
        )
        cands = screen.emit_candidates([run], net, window="am")
        assert len(cands) == 1
        assert cands[0]["start_latlon"] is not None
        assert cands[0]["end_latlon"] is not None
        assert len(cands[0]["start_latlon"]) == 2


# ============================================================================
# Integration: recurrence + run extraction on the synthetic export
# ============================================================================
class TestRecurrenceToRuns:
    def test_end_to_end(self, area):
        """Full pipeline: recurrence → extract → the middle recovers."""
        con, key = area
        rec = screen.segment_recurrence(con, key, windows=["am"],
                                        tti_threshold=1.25)
        net = _toy_network()
        runs = screen.extract_congestion_runs(
            rec, net, window="am", recurrence_threshold=0.5)
        assert len(runs) >= 1
        # The congested middle (1002, 1003) should be one run
        middle_run = [r for r in runs if 1002 in r.qualifying_ids
                      and 1003 in r.qualifying_ids]
        assert len(middle_run) == 1
        assert middle_run[0].n_qualifying == 2

    def test_low_recurrence_excluded(self):
        """Segment congested on 3/20 days (15%) does NOT qualify at 50%."""
        # Build a recurrence frame with segment 1002 at rec=0.15
        rec = pd.DataFrame({
            "am_recurrence": [0.0, 0.15, 0.8, 0.0, 0.0],
            "am_mean_tti": [1.0, 1.3, 2.0, 1.0, 1.0],
            "am_n_weekdays": [20] * 5,
            "am_n_congested": [0, 3, 16, 0, 0],
        }, index=pd.Index(SEG_IDS, name=SEGMENT_COL))
        net = _toy_network()
        runs = screen.extract_congestion_runs(
            rec, net, window="am", recurrence_threshold=0.5)
        # Only 1003 qualifies → single-segment run
        assert len(runs) == 1
        assert 1003 in runs[0].qualifying_ids
        assert 1002 not in runs[0].qualifying_ids

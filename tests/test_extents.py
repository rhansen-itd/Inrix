"""Tests for inrix_tools.extents — split criteria and multi-scale tiers."""
from __future__ import annotations

import geopandas as gpd
import pandas as pd
import pytest
from shapely.geometry import LineString

from inrix_tools import extents


def _linear_chain(n=10, frc_pattern=None, aadt_pattern=None):
    """Create a simple linear chain of n segments heading east."""
    LON_START = -116.5
    LAT = 43.6
    DLON = 0.01  # ~0.5 mi per segment

    if frc_pattern is None:
        frc_pattern = [2] * n
    if aadt_pattern is None:
        aadt_pattern = [10000] * n

    rows = []
    for i in range(n):
        sid = 1000 + i
        rows.append({
            "XDSegID": sid,
            "NextXDSegI": 1001 + i if i < n - 1 else None,
            "PreviousXD": 999 + i if i > 0 else None,
            "FRC": frc_pattern[i],
            "RoadNumber": "84",
            "RoadName": "I-84",
            "Miles": 0.5,
            "Bearing": "E",
            "SlipRoad": 0,
            "XDGroup": 300,
            "County": "Ada",
            "PostalCode": "Boise",
            "StartLat": LAT,
            "StartLong": LON_START + i * DLON,
            "EndLat": LAT,
            "EndLong": LON_START + (i + 1) * DLON,
            "AADT": aadt_pattern[i],
            "geometry": LineString([
                (LON_START + i * DLON, LAT),
                (LON_START + (i + 1) * DLON, LAT),
            ]),
        })

    df = gpd.GeoDataFrame(rows, crs="EPSG:4326")
    return df.set_index("XDSegID", drop=False)


class TestUrbanRuralSplits:
    def test_frc_transition_detected(self):
        """FRC 2->4 transition detected as urban/rural split."""
        net = _linear_chain(10, frc_pattern=[2] * 5 + [4] * 5)
        chain = list(net["XDSegID"])
        splits = extents.detect_urban_rural_splits(chain, net)
        assert len(splits) >= 1
        assert splits[0].kind == extents.SplitKind.URBAN_RURAL
        assert splits[0].segment_index == 5

    def test_no_transition_no_split(self):
        """Uniform FRC produces no urban/rural split."""
        net = _linear_chain(10, frc_pattern=[2] * 10)
        splits = extents.detect_urban_rural_splits(list(net["XDSegID"]), net)
        assert len(splits) == 0


class TestAADTSplits:
    def test_large_volume_step_detected(self):
        """A 60% AADT drop with >8000 vpd absolute change is detected."""
        aadt = [20000] * 5 + [8000] * 5
        net = _linear_chain(10, aadt_pattern=aadt)
        chain = list(net["XDSegID"])
        splits = extents.detect_aadt_splits(chain, net)
        assert len(splits) >= 1
        assert splits[0].kind == extents.SplitKind.AADT_STEP
        assert splits[0].segment_index == 5

    def test_small_volume_step_not_detected(self):
        """A 10% AADT change is below threshold."""
        aadt = [20000] * 5 + [18000] * 5
        net = _linear_chain(10, aadt_pattern=aadt)
        splits = extents.detect_aadt_splits(list(net["XDSegID"]), net)
        assert len(splits) == 0


class TestCongestionSplits:
    def test_tti_discontinuity_detected(self):
        """TTI dropping from 1.4 to 1.02 is detected as congestion boundary."""
        net = _linear_chain(10)
        chain = list(net["XDSegID"])

        rec_data = {
            "Segment ID": chain,
            "am_mean_tti": [1.4] * 5 + [1.02] * 5,
            "am_recurrence": [0.8] * 5 + [0.1] * 5,
        }
        rec = pd.DataFrame(rec_data).set_index("Segment ID")

        splits = extents.detect_congestion_splits(chain, recurrence=rec, window="am")
        assert len(splits) >= 1
        assert splits[0].kind == extents.SplitKind.CONGESTION_DROP


class TestBuildExtentTiers:
    def test_three_tiers_produced(self):
        """Build tiers produces at least one alternative per tier."""
        net = _linear_chain(10, frc_pattern=[2] * 5 + [4] * 5,
                            aadt_pattern=[25000] * 5 + [8000] * 5)
        chain = list(net["XDSegID"])

        rec_data = {
            "Segment ID": chain,
            "am_mean_tti": [1.4] * 3 + [1.1] * 2 + [1.02] * 5,
            "am_recurrence": [0.8] * 3 + [0.4] * 2 + [0.1] * 5,
        }
        rec = pd.DataFrame(rec_data).set_index("Segment ID")

        splits = extents.detect_split_points(chain, net, recurrence=rec, window="am")
        tiers = extents.build_extent_tiers(chain, net, splits, recurrence=rec, window="am")

        assert extents.ExtentTier.CORE in tiers
        assert extents.ExtentTier.COMMUTER in tiers
        assert extents.ExtentTier.REGIONAL in tiers

        core_miles = tiers[extents.ExtentTier.CORE][0].miles
        regional_miles = tiers[extents.ExtentTier.REGIONAL][0].miles
        assert core_miles < regional_miles

    def test_analyse_chain(self):
        """analyse_chain returns a complete ChainAnalysis."""
        net = _linear_chain(10, frc_pattern=[2] * 5 + [4] * 5)
        chain = list(net["XDSegID"])
        res = extents.analyse_chain(chain, net, route_number="84", bearing="E")
        assert res.route_number == "84"
        assert res.bearing == "E"
        assert res.chain_miles == 5.0
        assert extents.ExtentTier.REGIONAL in res.tiers
        df = extents.compare_tiers(res.tiers)
        assert len(df) >= 3


class TestDilutionFactor:
    def test_high_dilution(self):
        assert extents.dilution_factor(500.0, 50.0) == 10.0

    def test_zero_denominator(self):
        assert extents.dilution_factor(100.0, 0.0) == float("inf")

    def test_below_threshold(self):
        factor = extents.dilution_factor(100.0, 50.0)
        assert factor < extents.DILUTION_FACTOR_THRESHOLD


# ─── ROADMAP Item 46: mainline enumeration and catalogue generation ──

def _two_route_network():
    """A network with two routes crossing: US-95 north-south (both carriageways)
    and SH-53 east-west, plus a one-segment stub."""
    rows = []

    def seg(sid, nxt, rn, name, bearing, lat0, lon0, lat1, lon1, frc=2, group=1, aadt=20000):
        rows.append({
            "XDSegID": sid, "NextXDSegI": nxt, "PreviousXD": None,
            "FRC": frc, "RoadNumber": rn, "RoadName": name, "Miles": 0.5,
            "Bearing": bearing, "SlipRoad": 0, "XDGroup": group,
            "County": "Latah", "PostalCode": "83843",
            "StartLat": lat0, "StartLong": lon0, "EndLat": lat1, "EndLong": lon1,
            "AADT": aadt,
            "geometry": LineString([(lon0, lat0), (lon1, lat1)]),
        })

    LON = -116.99
    for i in range(6):                      # US-95 northbound
        seg(100 + i, 101 + i if i < 5 else None, "95", "US-95", "N",
            46.70 + i * 0.01, LON, 46.70 + (i + 1) * 0.01, LON, group=1)
    for i in range(6):                      # US-95 southbound, ~60 m east
        seg(200 + i, 201 + i if i < 5 else None, "95", "US-95", "S",
            46.76 - i * 0.01, LON + 0.0008, 46.76 - (i + 1) * 0.01, LON + 0.0008, group=2)
    for i in range(4):                      # SH-53 eastbound, far away
        seg(300 + i, 301 + i if i < 3 else None, "53", "ID-53", "E",
            47.60, -117.20 + i * 0.01, 47.60, -117.20 + (i + 1) * 0.01, group=3)
    seg(400, None, "8", "ID-8", "E", 46.72, -117.5, 46.72, -117.49, group=4)  # stub

    return gpd.GeoDataFrame(rows, crs="EPSG:4326")


class TestRouteLabel:
    def test_reads_the_band_off_the_road_name(self):
        assert extents.route_label("95", ["N Main St", "US-95"]) == "US-95"
        assert extents.route_label("90", ["I-90 W"]) == "I-90"

    def test_id_and_sr_normalise_to_sh(self):
        assert extents.route_label("3", ["ID-3"]) == "SH-3"
        assert extents.route_label("55", ["SR-55"]) == "SH-55"

    def test_two_digits_does_not_mean_us(self):
        """The digit-count heuristic the first couplet draft used called SH-55 US-55."""
        assert extents.route_label("55", ["ID-55"]) == "SH-55"

    def test_falls_back_to_frc_when_no_name_states_the_band(self):
        assert extents.route_label("84", ["Burke Rd"], frc=0) == "I-84"
        assert extents.route_label("4", ["Burke Rd"], frc=4) == "SH-4"

    def test_a_name_for_another_route_is_ignored(self):
        assert extents.route_label("53", ["US-95", "Ramsey Rd"]) == "SH-53"


class TestEnumerateMainlineChains:
    def test_one_chain_per_carriageway(self):
        chains = extents.enumerate_mainline_chains(_two_route_network())
        by_key = {(c.route_number, c.bearing): c for c in chains}
        assert set(by_key) == {("95", "N"), ("95", "S"), ("53", "E")}
        assert by_key[("95", "N")].n_segments == 6
        assert by_key[("95", "N")].miles == 3.0
        assert by_key[("95", "N")].route_label == "US-95"
        assert by_key[("53", "E")].route_label == "SH-53"

    def test_short_stubs_are_dropped(self):
        chains = extents.enumerate_mainline_chains(_two_route_network(), min_miles=1.0)
        assert all(c.route_number != "8" for c in chains)
        kept = extents.enumerate_mainline_chains(_two_route_network(), min_miles=0.1)
        assert any(c.route_number == "8" for c in kept)

    def test_the_walk_stops_at_a_route_change(self):
        """A chain is one route: following NextXDSegI onto the cross-route would
        make every junction a corridor extension."""
        net = _two_route_network()
        net.loc[net["XDSegID"] == 105, "NextXDSegI"] = 300
        chains = extents.enumerate_mainline_chains(net)
        us95 = next(c for c in chains if c.route_number == "95" and c.bearing == "N")
        assert 300 not in us95.segment_ids
        assert us95.n_segments == 6

    def test_route_filter(self):
        chains = extents.enumerate_mainline_chains(_two_route_network(), route_numbers=["53"])
        assert {c.route_number for c in chains} == {"53"}

    def test_direction_label(self):
        chains = extents.enumerate_mainline_chains(_two_route_network())
        assert {c.direction for c in chains if c.route_number == "95"} == {"NB", "SB"}


class TestPairChains:
    def test_opposing_carriageways_pair(self):
        net = _two_route_network()
        chains = extents.enumerate_mainline_chains(net)
        pairs = dict(extents.pair_chains(chains, net))
        us95 = next(c for c in pairs if c.route_number == "95")
        assert pairs[us95] is not None
        assert pairs[us95].bearing != us95.bearing

    def test_a_lone_direction_pairs_with_nothing(self):
        net = _two_route_network()
        chains = extents.enumerate_mainline_chains(net)
        pairs = dict(extents.pair_chains(chains, net))
        sh53 = next(c for c in pairs if c.route_number == "53")
        assert pairs[sh53] is None

    def test_a_distant_same_route_chain_is_not_the_counterpart(self):
        """US-95 walks as several chains per district; matching on route alone
        marries one town's northbound to another town's southbound."""
        net = _two_route_network()
        chains = extents.enumerate_mainline_chains(net)
        pairs = dict(extents.pair_chains(chains, net, max_mean_sep_m=10.0))
        assert all(v is None for v in pairs.values())


class TestSegmentEndpoint:
    def test_geometry_wins_over_a_disagreeing_declared_endpoint(self):
        """XD record 1187395985 declares a start 294 m off its own line; an entry
        written from the declared value snaps onto a different road."""
        net = _two_route_network().set_index("XDSegID", drop=False)
        net.loc[100, "StartLat"] = 46.60          # 11 km south of the geometry
        lat, lon = extents.segment_endpoint(net, 100, end=False)
        assert lat == pytest.approx(46.70, abs=1e-6)
        assert lon == pytest.approx(-116.99, abs=1e-6)

    def test_end_is_the_far_terminal(self):
        net = _two_route_network().set_index("XDSegID", drop=False)
        lat, _ = extents.segment_endpoint(net, 100, end=True)
        assert lat == pytest.approx(46.71, abs=1e-6)


class TestTtiFrame:
    def test_ratio_of_reference_to_window_speed(self):
        scr = pd.DataFrame({"ref_speed": [60.0, 60.0], "pm_speed": [30.0, 60.0]},
                           index=pd.Index([1, 2], name="Segment ID"))
        tti = extents.tti_frame(scr, ("pm",))
        assert tti.loc[1, "pm_mean_tti"] == pytest.approx(2.0)
        assert tti.loc[2, "pm_mean_tti"] == pytest.approx(1.0)

    def test_zero_speed_is_null_not_infinite(self):
        scr = pd.DataFrame({"ref_speed": [60.0], "pm_speed": [0.0]},
                           index=pd.Index([1], name="Segment ID"))
        assert pd.isna(extents.tti_frame(scr, ("pm",)).loc[1, "pm_mean_tti"])


class TestContiguousCores:
    def test_two_separated_bottlenecks_are_two_cores(self):
        """Spanning min-to-max would swallow the free-flowing miles between them —
        the exact dilution Tier 1 exists to avoid."""
        net = _linear_chain(12)
        chain = list(net["XDSegID"])
        tti = [1.4, 1.4] + [1.02] * 8 + [1.4, 1.4]
        rec = pd.DataFrame({"Segment ID": chain, "am_mean_tti": tti}).set_index("Segment ID")
        tiers = extents.build_extent_tiers(chain, net, [], recurrence=rec, window="am")
        cores = tiers[extents.ExtentTier.CORE]
        assert len(cores) == 2
        assert all(c.miles == 1.0 for c in cores)

    def test_a_short_core_is_dropped_by_min_core_miles(self):
        net = _linear_chain(12)
        chain = list(net["XDSegID"])
        rec = pd.DataFrame({"Segment ID": chain,
                            "am_mean_tti": [1.4] + [1.02] * 11}).set_index("Segment ID")
        tiers = extents.build_extent_tiers(chain, net, [], recurrence=rec,
                                           window="am", min_core_miles=1.0)
        # Only the geometric fallback core survives, not the 0.5-mile run.
        assert all("bottleneck core" not in c.split_rationale
                   for c in tiers[extents.ExtentTier.CORE])


class TestDescribeSplit:
    def test_none_reads_as_the_chain_end(self):
        assert extents.describe_split(None, "chain start") == "chain start"

    def test_a_junction_names_the_crossing_route(self):
        sp = extents.SplitPoint(segment_index=3, segment_id=7,
                                kind=extents.SplitKind.JUNCTION, score=0.8,
                                details={"type": "intersecting_route",
                                         "crossing_routes": ["53"]})
        assert "53" in extents.describe_split(sp)

    def test_an_aadt_step_states_both_volumes(self):
        sp = extents.SplitPoint(segment_index=3, segment_id=7,
                                kind=extents.SplitKind.AADT_STEP, score=0.6,
                                details={"aadt_before": 20000.0, "aadt_after": 8000.0,
                                         "relative_change": 0.6})
        text = extents.describe_split(sp)
        assert "20,000" in text and "8,000" in text


class TestMirrorExtent:
    def test_the_mirrored_extent_covers_the_same_ground_reversed(self):
        net = _two_route_network()
        chains = extents.enumerate_mainline_chains(net)
        nb = next(c for c in chains if c.bearing == "N")
        sb = next(c for c in chains if c.bearing == "S")
        alt = extents.ExtentAlternative(
            tier=extents.ExtentTier.CORE,
            segment_ids=tuple(nb.segment_ids[1:4]), miles=1.5,
            start_latlon=extents.segment_endpoint(
                net.set_index("XDSegID", drop=False), nb.segment_ids[1]),
            end_latlon=extents.segment_endpoint(
                net.set_index("XDSegID", drop=False), nb.segment_ids[3], end=True),
            split_rationale="test",
        )
        mirrored = extents.mirror_extent(alt, sb.segment_ids, net)
        assert mirrored is not None
        assert set(mirrored.segment_ids) <= set(sb.segment_ids)
        assert mirrored.miles == pytest.approx(alt.miles, abs=0.51)
        assert mirrored.start_latlon[0] > mirrored.end_latlon[0]   # runs southbound


# ─── ROADMAP Item 50: cores on recurring congestion against the own baseline ──

def _baseline(ids, ratios=None, *, night_tt=1.0, night_obs=1000, weekday_obs=5000,
              p15=None, realtime=0.99, ref_tti=None):
    """A baseline screen: every segment's overnight travel time is ``night_tt`` and
    its PM peak is ``ratio x night_tt`` (AM free-flowing). ``ref_tti`` sets INRIX's
    reference speed so the peak reads that TTI against it (default: the ratio)."""
    ratios = ratios or {}
    ids = list(ids)
    ratio = [ratios.get(s, 1.0) for s in ids]
    pm = [r * night_tt for r in ratio]
    ref_t = [(ref_tti or {}).get(s, r) for s, r in zip(ids, ratio)]
    rt = [realtime.get(s, 0.99) if isinstance(realtime, dict) else realtime for s in ids]
    nobs = [night_obs.get(s, 1000) if isinstance(night_obs, dict) else night_obs for s in ids]
    return pd.DataFrame({
        "n_obs": 10000,
        # 0.5-mi segments: ref speed that makes pm / (0.5 / ref x 60) = ref_tti.
        "ref_speed": [0.5 * 60.0 * t / p for t, p in zip(ref_t, pm)],
        "am_travel_time": night_tt, "am_n_obs": 500, "am_realtime_share": rt,
        "pm_travel_time": pm, "pm_n_obs": 500, "pm_realtime_share": rt,
        "night_travel_time": night_tt, "night_n_obs": nobs,
        "weekday_n_obs": weekday_obs,
        "weekday_tt_p15": night_tt if p15 is None else p15,
    }, index=pd.Index(ids, name="Segment ID"))


class TestSegmentCongestion:
    def test_the_peak_is_measured_against_the_nights_travel_time(self):
        net = _linear_chain(4)
        seg = extents.segment_congestion(_baseline(net.index, {1001: 1.5}), net)
        assert seg.loc[1001, "ratio"] == pytest.approx(1.5)
        assert seg.loc[1001, "baseline_source"] == "night"
        assert seg.loc[1001, "peak_window"] == "pm"
        # 0.5 min of delay x 10,000 AADT / 60
        assert seg.loc[1001, "vhd"] == pytest.approx(0.5 * 10000 / 60)

    def test_a_thin_night_falls_back_to_the_weekday_percentile(self):
        net = _linear_chain(2)
        b = _baseline(net.index, {1000: 1.5}, night_obs={1000: 20, 1001: 1000}, p15=1.2)
        seg = extents.segment_congestion(b, net)
        assert seg.loc[1000, "baseline_source"] == extents.BASELINE_FALLBACK_COL
        assert seg.loc[1000, "ratio"] == pytest.approx(1.5 / 1.2)

    def test_no_baseline_is_unknown_not_free_flow(self):
        """A segment with no usable baseline carries no weight **and no delay** —
        it is not counted as a free-flowing segment with zero delay."""
        net = _linear_chain(2)
        b = _baseline(net.index, {1000: 1.5}, night_obs=10, weekday_obs=10)
        seg = extents.segment_congestion(b, net)
        assert seg.loc[1000, "baseline_source"] == "none"
        assert pd.isna(seg.loc[1000, "ratio"]) and pd.isna(seg.loc[1000, "delay_min"])
        assert seg.loc[1000, "weight"] == 0.0

    def test_the_weight_has_no_cliff_at_1_20(self):
        net = _linear_chain(2)
        seg = extents.segment_congestion(_baseline(net.index, {1000: 1.199, 1001: 1.20}), net)
        assert abs(seg.loc[1000, "weight"] - seg.loc[1001, "weight"]) < 0.01


class TestFindCores:
    def test_a_geometric_grade_is_not_congestion(self):
        """Gilbert Grade: slow against INRIX's reference speed (TTI 1.33) but as slow
        at night as at the peak — no core."""
        net = _linear_chain(8)
        ids = list(net.index)
        b = _baseline(ids, {s: 1.02 for s in ids}, night_tt=1.3,
                      ref_tti={s: 1.33 for s in ids})
        seg = extents.segment_congestion(b, net)
        assert (seg["ref_tti"] > 1.3).all()
        assert not [c for c in extents.find_cores(ids, seg) if c.qualifies]

    def test_one_long_segment_is_not_a_core(self):
        """Bonners Ferry, Session 65: one 0.84-mi segment stood as a whole core."""
        net = _linear_chain(6)
        net["Miles"] = 0.84
        ids = list(net.index)
        seg = extents.segment_congestion(_baseline(ids, {1002: 1.5}), net)
        cands = extents.find_cores(ids, seg)
        assert len(cands) == 1 and cands[0].segment_ids == (1002,)
        assert extents.FAIL_EFFECTIVE_MILES in cands[0].fails

    def test_a_1_199_neighbour_does_not_split_the_core(self):
        """SH-8 in Moscow: a 1.199 segment used to break the run and leave a piece
        under the 0.75-mi floor. The core bridges it and qualifies whole."""
        net = _linear_chain(8)
        ids = list(net.index)
        ratios = {1002: 1.4, 1003: 1.199, 1004: 1.4}
        seg = extents.segment_congestion(_baseline(ids, ratios), net)
        core = next(c for c in extents.find_cores(ids, seg) if c.qualifies)
        assert core.segment_ids == (1002, 1003, 1004)
        at_120 = extents.segment_congestion(_baseline(ids, {**ratios, 1003: 1.20}), net)
        core_120 = next(c for c in extents.find_cores(ids, at_120) if c.qualifies)
        assert core_120.segment_ids == core.segment_ids
        assert core_120.vhd == pytest.approx(core.vhd, rel=0.01)

    def test_a_short_uncongested_gap_is_bridged_and_a_long_one_is_not(self):
        net = _linear_chain(10)
        ids = list(net.index)
        seg = extents.segment_congestion(_baseline(ids, {1001: 1.4, 1002: 1.4,
                                                        1004: 1.4, 1005: 1.4,
                                                        1009: 1.4}), net)
        spans = sorted(c.segment_ids for c in extents.find_cores(ids, seg))
        assert (1001, 1002, 1003, 1004, 1005) in spans
        assert (1009,) in spans

    def test_mostly_imputed_data_fails(self):
        """US-12 near Lowell: 53% real-time data."""
        net = _linear_chain(6)
        ids = list(net.index)
        seg = extents.segment_congestion(
            _baseline(ids, {s: 1.4 for s in ids[1:5]}, realtime=0.53), net)
        core = extents.find_cores(ids, seg)[0]
        assert extents.FAIL_REALTIME in core.fails

    def test_a_low_volume_road_fails_the_delay_floor(self):
        """SH-3 in Benewah County: 460 AADT, 230 per direction (Item 54)."""
        net = _linear_chain(6, aadt_pattern=[230] * 6)
        ids = list(net.index)
        seg = extents.segment_congestion(_baseline(ids, {s: 1.4 for s in ids[1:5]}), net)
        core = extents.find_cores(ids, seg)[0]
        assert extents.FAIL_VHD_PER_MILE in core.fails
        assert extents.FAIL_EFFECTIVE_MILES not in core.fails

    def test_an_unknown_segment_inside_a_run_is_bridged_but_not_counted(self):
        net = _linear_chain(6)
        ids = list(net.index)
        b = _baseline(ids, {1001: 1.4, 1002: 1.4, 1003: 1.4, 1004: 1.4},
                      night_obs={1003: 5}, weekday_obs=5000)
        b.loc[1003, "weekday_n_obs"] = 5          # no fallback either
        seg = extents.segment_congestion(b, net)
        core = extents.find_cores(ids, seg)[0]
        assert core.segment_ids == (1001, 1002, 1003, 1004)
        assert core.unknown_miles == pytest.approx(0.5)
        # delay per mile over the *known* miles: unknown is not free flow
        assert core.vhd_per_mile == pytest.approx(0.4 * 10000 / 60 / 0.5)


def _urban(net, inside):
    """Mark segments inside/outside an urban area."""
    net = net.copy()
    net["urban_share"] = [1.0 if s in inside else 0.0 for s in net.index]
    net["urban_area"] = "Hailey, ID"
    return net


class TestGrowCongestedExtent:
    def _grow(self, ratios, inside=None, n=12):
        net = _linear_chain(n)
        if inside is not None:
            net = _urban(net, inside)
        ids = list(net.index)
        seg = extents.segment_congestion(_baseline(ids, ratios), net)
        core = next(c for c in extents.find_cores(ids, seg) if c.qualifies)
        lo, hi, up, down = extents.grow_congested_extent(ids, seg, core)
        return ids[lo:hi], core, up, down

    def test_tier_2_stops_where_the_congestion_ends(self):
        ratios = {1004: 1.5, 1005: 1.5, 1006: 1.5, 1007: 1.4, 1003: 1.08}
        grown, core, up, down = self._grow(ratios)
        assert grown == [1003, 1004, 1005, 1006, 1007]
        assert down.kind is extents.SplitKind.CONGESTION_END
        assert up.kind is extents.SplitKind.CONGESTION_END

    def test_spill_past_the_urban_boundary_is_kept_when_significant(self):
        """The owner, 2026-09-23: keep congestion past the boundary when it doesn't
        significantly dilute the core."""
        inside = {1002, 1003, 1004, 1005, 1006}
        ratios = {1003: 1.5, 1004: 1.5, 1005: 1.5, 1007: 1.4, 1008: 1.4}
        grown, *_ = self._grow({**ratios, 1006: 1.4}, inside)
        assert 1007 in grown and 1008 in grown

    def test_free_flow_past_the_boundary_is_not_bridged(self):
        inside = {1002, 1003, 1004, 1005, 1006}
        # 1008 is mildly congested (a shoulder, not a seed) past a free 1007: inside
        # the area that gap would be bridged; past the boundary it is not.
        ratios = {1003: 1.5, 1004: 1.5, 1005: 1.5, 1006: 1.4, 1008: 1.08}
        grown, core, up, down = self._grow(ratios, inside)
        assert grown[-1] == 1006
        inside_grown, *_ = self._grow(ratios, inside | {1007, 1008, 1009})
        assert inside_grown[-1] == 1008
        assert down.kind is extents.SplitKind.URBAN_BOUNDARY

    def test_dilution_stops_the_growth(self):
        # A 1.5-mi core at 333 VHD/mi, then a long mild shoulder at 10 VHD/segment:
        # the fourth shoulder segment takes the extent under half the core's rate.
        ratios = {1004: 2.0, 1005: 2.0, 1006: 2.0,
                  **{s: 1.06 for s in range(1007, 1014)}}
        grown, core, up, down = self._grow(ratios, n=16)
        assert grown[-1] == 1009
        assert down.kind is extents.SplitKind.DILUTION

    def test_rural_is_not_what_drops_a_core(self):
        """A rural core (no urban context at all) qualifies on its data alone."""
        net = _urban(_linear_chain(8), inside=set())
        ids = list(net.index)
        seg = extents.segment_congestion(_baseline(ids, {s: 1.4 for s in ids[2:6]}), net)
        assert any(c.qualifies for c in extents.find_cores(ids, seg))


class TestGenerateCatalogue:
    CONGESTED = {101: 2.0, 102: 2.0, 103: 2.0}

    def _catalogue(self, ratios=None, **kwargs):
        net = _two_route_network()
        ids = list(net["XDSegID"])
        b = _baseline(ids, self.CONGESTED if ratios is None else ratios)
        return net, extents.generate_catalogue(net, b, observed=set(ids), **kwargs)

    def test_the_catalogue_parses(self):
        from inrix_tools import corridors
        _, cat = self._catalogue()
        entries = corridors.parse_catalogue(cat)
        groups = corridors.parse_reporting_corridors(cat)
        assert entries and groups
        assert {e.corridor for e in entries} == {g.id for g in groups}

    def test_every_entry_states_why_its_extent_ends_where_it_does(self):
        _, cat = self._catalogue()
        for entry in cat["corridors"]:
            assert "Upstream boundary:" in entry["description"]
            assert "downstream boundary:" in entry["description"]

    def test_a_free_flowing_direction_is_not_mirrored(self):
        """I-90 in Coeur d'Alene: WB congested, EB at TTI 1.04. The EB direction
        used to inherit WB's extent. It now answers for itself and is dropped,
        with the reason on the group."""
        _, cat = self._catalogue()
        assert {e["direction"] for e in cat["corridors"]} == {"NB"}
        core = next(g for g in cat["reporting_corridors"] if g["_tier"] == "core")
        assert core["_directions"] == ["NB"]
        assert core["_companion"].startswith("SB not catalogued")

    def test_a_congested_opposite_direction_keeps_its_own_extent(self):
        _, cat = self._catalogue({**self.CONGESTED, 202: 2.0, 203: 2.0})
        cores = [e for e in cat["corridors"] if e["_tier"] == "core"]
        assert {e["direction"] for e in cores} == {"NB", "SB"}
        sb = next(e for e in cores if e["direction"] == "SB")
        assert sb["_segment_ids"] == [202, 203]         # its own, not a mirror of NB's

    def test_only_congested_facilities_are_catalogued(self):
        """SH-53 free-flows, so it is not a corridor — the point of Item 43."""
        _, cat = self._catalogue()
        assert all(e["id"].startswith("us-95") for e in cat["corridors"])

    def test_only_the_core_is_ranked(self):
        _, cat = self._catalogue()
        ranked = {g["_tier"]: g["_ranked"] for g in cat["reporting_corridors"]}
        assert ranked.pop("core") is True
        assert not any(ranked.values())

    def test_coincident_tiers_are_emitted_once_at_the_narrowest_label(self):
        _, cat = self._catalogue()
        extents_seen = [tuple(e["_segment_ids"]) for e in cat["corridors"]
                        if e["direction"] == "NB"]
        assert len(extents_seen) == len(set(extents_seen))
        assert any(e["_tier"] == "core" for e in cat["corridors"])

    def test_the_core_is_tighter_than_the_context(self):
        _, cat = self._catalogue()
        by_tier = {e["_tier"]: e for e in cat["corridors"] if e["direction"] == "NB"}
        assert by_tier["core"]["_generated_miles"] < by_tier["regional"]["_generated_miles"]

    def test_two_cores_on_one_chain_are_two_facilities(self):
        """SH-75 carries Ketchum and Hailey; only the stronger used to be catalogued."""
        net = _linear_chain(16)
        ids = list(net.index)
        ratios = {1001: 2.0, 1002: 2.0, 1003: 2.0, 1011: 1.5, 1012: 1.5, 1013: 1.5}
        cat = extents.generate_catalogue(net, _baseline(ids, ratios))
        cores = [g for g in cat["reporting_corridors"] if g["_tier"] == "core"]
        assert len(cores) == 2
        assert len({g["_facility"] for g in cores}) == 2

    def test_the_audit_records_why_a_chain_was_rejected(self):
        audit = []
        self._catalogue(audit=audit)
        rejected = [r for r in audit if r["role"] == "rejected"]
        assert rejected and all("core_fails" in r or r["n_candidates"] == 0
                                for r in rejected)

    def test_max_facilities_caps_the_catalogue(self):
        _, cat = self._catalogue(max_facilities=0)
        assert cat["corridors"] == []

    def test_an_unobserved_facility_is_not_catalogued(self):
        net = _two_route_network()
        ids = list(net["XDSegID"])
        cat = extents.generate_catalogue(net, _baseline(ids, self.CONGESTED),
                                         observed={300, 301})
        assert cat["corridors"] == []


class TestContextExtent:
    def test_tier_3_is_capped_context_not_the_whole_chain(self):
        """SH-75's Tier 3 was the whole 98.5-mile chain, through Galena."""
        net = _linear_chain(80)                       # 40 miles
        ids = list(net.index)
        seg = extents.segment_congestion(
            _baseline(ids, {s: 1.5 for s in range(1038, 1042)}), net)
        lo, hi, up, down = extents.context_extent(ids, seg, 38, 42)
        miles = 0.5 * (hi - lo)
        assert miles <= 2.0 + 2 * extents.CONTEXT_PAD_MILES + 1.0
        assert up.kind is extents.SplitKind.CONTEXT_LIMIT
        assert down.kind is extents.SplitKind.CONTEXT_LIMIT

    def test_an_urban_core_s_context_stops_at_the_urban_edge(self):
        net = _urban(_linear_chain(30), inside=set(range(1010, 1020)))
        ids = list(net.index)
        seg = extents.segment_congestion(_baseline(ids, {s: 1.5 for s in range(1013, 1017)}),
                                         net)
        lo, hi, up, down = extents.context_extent(ids, seg, 13, 17, urban=True)
        assert (ids[lo], ids[hi - 1]) == (1010, 1019)
        assert up.kind is extents.SplitKind.URBAN_BOUNDARY


def _monthly(ids, pm_by_month, night_tt=1.0):
    """A monthly screen: every segment's PM mean per month; AM at the night level."""
    rows = [{"Segment ID": s, "month": m, "am_travel_time": night_tt, "am_n_obs": 100,
             "pm_travel_time": tt, "pm_n_obs": 100}
            for s in ids for m, tt in pm_by_month.items()]
    return pd.DataFrame(rows)


class TestEpisodicFlag:
    MONTHS = [f"2026-{m:02d}" for m in range(1, 9)]

    def test_a_summer_step_is_flagged(self):
        """I-90 WB: at the night level until late June, then 3-4x slower."""
        net = _linear_chain(4)
        ids = list(net.index)
        seg = extents.segment_congestion(_baseline(ids, {s: 1.8 for s in ids}), net)
        pm = {m: (1.0 if m < "2026-06" else 3.5) for m in self.MONTHS}
        profile = extents.monthly_delay_profile(ids, seg, _monthly(ids, pm))
        assert list(profile.index) == self.MONTHS
        assert profile["2026-01"] == 0.0
        flag = extents.episodic_flag(profile)
        assert flag.startswith("episodic: 100%")
        assert "2026-06, 2026-07, 2026-08" in flag

    def test_a_queue_every_month_is_not_flagged(self):
        net = _linear_chain(4)
        ids = list(net.index)
        seg = extents.segment_congestion(_baseline(ids, {s: 1.5 for s in ids}), net)
        pm = {m: 1.5 + 0.05 * i for i, m in enumerate(self.MONTHS)}
        assert extents.episodic_flag(
            extents.monthly_delay_profile(ids, seg, _monthly(ids, pm))) == ""

    def test_a_summer_heavy_queue_is_seasonal_not_episodic(self):
        profile = pd.Series({m: (10.0 if m < "2026-06" else 25.0) for m in self.MONTHS})
        assert extents.episodic_flag(profile).startswith("seasonal: 60%")

    def test_too_few_months_is_not_judged(self):
        assert extents.episodic_flag(pd.Series({"2026-07": 10.0, "2026-08": 0.0})) == ""

    def test_the_flag_is_carried_not_used_to_drop(self):
        net = _linear_chain(8)
        ids = list(net.index)
        ratios = {1002: 2.0, 1003: 2.0, 1004: 2.0}
        pm = {m: (1.0 if m < "2026-07" else 3.0) for m in self.MONTHS}
        cat = extents.generate_catalogue(net, _baseline(ids, ratios),
                                         monthly=_monthly(ids, pm))
        core = next(g for g in cat["reporting_corridors"] if g["_tier"] == "core")
        assert core["_flags"] and core["_flags"][0].startswith("episodic")
        assert core["_ranked"] is True


# ─── ROADMAP Item 51: chains across route-numbering changes ───────────

def _seg_row(sid, nxt, routes, name, p0, p1, *, rid=None, mp=(None, None), group=1,
             road_list=None, bearing="E", aadt=20000):
    """One segment (lon/lat ends) with ITD membership columns; Miles from the ends."""
    import math
    dx = (p1[0] - p0[0]) * 111_320 * math.cos(math.radians(p0[1]))
    dy = (p1[1] - p0[1]) * 111_320
    return {
        "XDSegID": sid, "NextXDSegI": nxt, "PreviousXD": None, "FRC": 3,
        "RoadNumber": routes.split("/")[0] if routes else None, "RoadName": name,
        "RoadList": road_list, "Miles": math.hypot(dx, dy) / 1609.344,
        "Bearing": bearing, "SlipRoad": 0, "XDGroup": group, "County": "Latah",
        "PostalCode": "83843", "StartLat": p0[1], "StartLong": p0[0],
        "EndLat": p1[1], "EndLong": p1[0], "AADT": aadt,
        "itd_routes": routes, "itd_route_id": rid,
        "shs_mp_start": mp[0], "shs_mp_end": mp[1],
        "geometry": LineString([p0, p1]),
    }


def _moscow_sh8(with_mileposts=True):
    """SH-8 eastbound through a Moscow-like couplet.

    SH-8 runs east on 3rd St (route 8, SHS mp 0-1.29), turns right onto the couplet's
    southbound leg (route 95 only by the SHS; ``RoadList`` names ID-8), and runs on
    east on Troy Rd (route 8 again, mp 1.75 on). 3rd St carries on past the turn for
    0.13 mi of SH-8's own line (the stub) and dead-ends. The link runs straight on;
    nothing links onto the couplet leg."""
    L = 46.732
    rid8 = "01870ASH008"
    mps = (lambda a, b: (a, b)) if with_mileposts else (lambda a, b: (None, None))
    rows = [
        _seg_row(1, 2, "8", "W Pullman Rd", (-117.030, L), (-117.010, L), rid=rid8,
                 mp=mps(0.0, 0.95)),
        _seg_row(2, 3, "8", "W 3rd St", (-117.010, L), (-117.003, L), rid=rid8,
                 mp=mps(0.95, 1.29)),
        _seg_row(3, None, "8", "E 3rd St", (-117.003, L), (-117.000, L), rid=rid8,
                 mp=mps(1.29, 1.43)),
        _seg_row(4, 5, "95", "S Jackson St", (-117.003, L), (-117.003, 46.726),
                 rid="01540DUS095", road_list="S Jackson St|ID-8|US-95", bearing="S",
                 group=2),
        _seg_row(5, 6, "8", "ID-8", (-117.003, 46.726), (-116.990, 46.726), rid=rid8,
                 mp=mps(1.75, 2.35), group=3),
        _seg_row(6, None, "8", "ID-8", (-116.990, 46.726), (-116.970, 46.726), rid=rid8,
                 mp=mps(2.35, 3.30), group=3),
    ]
    return gpd.GeoDataFrame(rows, crs="EPSG:4326")


class TestItem51ChainWalk:
    def test_concurrency_and_the_milepost_gap_make_sh8_one_chain(self):
        chains = extents.enumerate_mainline_chains(_moscow_sh8(), min_miles=0.1)
        sh8 = max((c for c in chains if "8" in c.routes), key=lambda c: c.miles)
        assert sh8.segment_ids == (1, 2, 4, 5, 6)
        stub = [j for j in sh8.joins if j["kind"] == "stub_junction"]
        assert stub and stub[0]["from"] == 2 and stub[0]["to"] == 4
        assert stub[0]["mp_leave"] == pytest.approx(1.29)
        assert stub[0]["mp_return"] == pytest.approx(1.75)

    def test_without_milepost_evidence_the_stub_is_not_left(self):
        chains = extents.enumerate_mainline_chains(_moscow_sh8(with_mileposts=False),
                                                   min_miles=0.1)
        assert all(not (2 in c.segment_ids and 4 in c.segment_ids) for c in chains)

    def test_without_membership_the_walk_is_item_46s(self):
        net = _moscow_sh8().drop(columns=["itd_routes"])
        chains = extents.enumerate_mainline_chains(net, min_miles=0.1)
        assert all(not (2 in c.segment_ids and 5 in c.segment_ids) for c in chains)

    def test_a_route_turning_off_the_link_is_joined_at_the_junction(self):
        """Payette: US-95 northbound links onto S Main St (off the system); ITD's US-95
        leaves on 16th St, whose first segment nothing links into."""
        rows = [
            _seg_row(10, 11, "95", "US-95", (-116.93, 44.02), (-116.93, 44.03), bearing="N"),
            _seg_row(11, 90, "95", "US-95", (-116.93, 44.03), (-116.93, 44.04), bearing="N"),
            _seg_row(90, None, "", "S Main St", (-116.93, 44.04), (-116.93, 44.05), bearing="N"),
            _seg_row(12, 13, "95", "95 N", (-116.93, 44.04), (-116.92, 44.041), bearing="E",
                     group=2),
            _seg_row(13, None, "95", "S 16th St", (-116.92, 44.041), (-116.92, 44.06),
                     bearing="N", group=2),
        ]
        chains = extents.enumerate_mainline_chains(gpd.GeoDataFrame(rows, crs="EPSG:4326"),
                                                   min_miles=0.1)
        us95 = max(chains, key=lambda c: c.miles)
        assert us95.segment_ids == (10, 11, 12, 13)
        assert us95.joins[0]["kind"] == "junction"

    def test_the_other_carriageway_is_never_a_junction(self):
        rows = [
            _seg_row(20, None, "95", "US-95", (-116.93, 44.02), (-116.93, 44.03), bearing="N"),
            _seg_row(21, None, "95", "US-95", (-116.93, 44.03), (-116.93, 44.02), bearing="S",
                     group=2),
        ]
        chains = extents.enumerate_mainline_chains(gpd.GeoDataFrame(rows, crs="EPSG:4326"),
                                                   min_miles=0.1)
        assert sorted(c.segment_ids for c in chains) == [(20,), (21,)]

    def test_a_renumbering_on_one_street_is_one_chain(self):
        """Yellowstone Hwy: US-91 ends where US-26 begins, same street, straight on."""
        rows = [
            _seg_row(30, 31, "91", "S Yellowstone Hwy", (-112.06, 43.44), (-112.06, 43.45),
                     bearing="N"),
            _seg_row(31, 32, "91", "S Yellowstone Hwy", (-112.06, 43.45), (-112.06, 43.46),
                     bearing="N"),
            _seg_row(32, 33, "15/26", "S Yellowstone Hwy", (-112.06, 43.46), (-112.06, 43.47),
                     bearing="N", group=2),
            _seg_row(33, None, "15/26", "N Yellowstone Hwy", (-112.06, 43.47),
                     (-112.06, 43.48), bearing="N", group=2),
        ]
        chains = extents.enumerate_mainline_chains(gpd.GeoDataFrame(rows, crs="EPSG:4326"),
                                                   min_miles=0.1)
        long_ = max(chains, key=lambda c: c.miles)
        assert long_.segment_ids == (30, 31, 32, 33)
        assert set(long_.routes) >= {"91"} and len(long_.routes) >= 2
        assert any(j["kind"] == "renumbering" for j in long_.joins)

    def test_a_route_renamed_where_another_starts_is_not_merged(self):
        """Troy: SH-8 on S Main St becomes "ID-8" where SH-99 starts down S Main St for
        a few blocks. The street does not run on far enough to be one road."""
        rows = [
            _seg_row(40, 41, "8", "ID-8", (-116.80, 46.73), (-116.78, 46.73)),
            _seg_row(41, 42, "8", "S Main St", (-116.78, 46.73), (-116.77, 46.73)),
            _seg_row(42, 43, "8", "ID-8", (-116.77, 46.73), (-116.75, 46.735)),
            _seg_row(43, None, "8", "ID-8", (-116.75, 46.735), (-116.73, 46.735)),
            _seg_row(50, 51, "99", "S Main St", (-116.77, 46.73), (-116.7695, 46.7297),
                     bearing="S", group=2),
            _seg_row(51, None, "99", "ID-99", (-116.7695, 46.7297), (-116.76, 46.70),
                     bearing="S", group=2),
        ]
        chains = extents.enumerate_mainline_chains(gpd.GeoDataFrame(rows, crs="EPSG:4326"),
                                                   min_miles=0.1)
        sh8 = next(c for c in chains if 40 in c.segment_ids)
        assert sh8.segment_ids == (40, 41, 42, 43)


class TestRouteJunctionRepairs:
    def test_a_junction_whose_link_leaves_every_route_is_written(self):
        rows = [
            _seg_row(10, 11, "95", "US-95", (-116.93, 44.02), (-116.93, 44.03), bearing="N"),
            _seg_row(11, 90, "95", "US-95", (-116.93, 44.03), (-116.93, 44.04), bearing="N"),
            _seg_row(90, None, "", "S Main St", (-116.93, 44.04), (-116.93, 44.05), bearing="N"),
            _seg_row(12, None, "95", "95 N", (-116.93, 44.04), (-116.92, 44.041), bearing="E",
                     group=2),
        ]
        rep = extents.route_junction_repairs(gpd.GeoDataFrame(rows, crs="EPSG:4326"))
        assert list(zip(rep["segment"], rep["old_next"], rep["new_next"])) == [(11, 90, 12)]
        assert set(rep["kind"]) == {extents.ROUTE_JUNCTION}

    def test_a_stub_junction_is_left_to_the_chain_walk(self):
        """At 3rd & Jackson the link still serves SH-8's own stub on 3rd St."""
        rep = extents.route_junction_repairs(_moscow_sh8())
        assert 2 not in set(rep["segment"])


def _concurrent_network():
    """US-20 north (segments 101-106) and US-26, which arrives from the west (201-202),
    shares 103-104 with US-20, and leaves east (205-206)."""
    x, y0, d = -112.03, 43.49, 0.007
    rows = []
    for i in range(6):
        routes = "20/26" if i in (2, 3) else "20"
        rows.append(_seg_row(101 + i, 102 + i if i < 5 else None, routes, "Yellowstone Hwy",
                             (x, y0 + i * d), (x, y0 + (i + 1) * d), bearing="N"))
    rows.append(_seg_row(201, 202, "26", "Sunnyside Rd", (x - 2 * d, y0 + 2 * d),
                         (x - d, y0 + 2 * d), group=2))
    rows.append(_seg_row(202, 103, "26", "Sunnyside Rd", (x - d, y0 + 2 * d),
                         (x, y0 + 2 * d), group=2))
    rows.append(_seg_row(205, 206, "26", "Ririe Hwy", (x, y0 + 4 * d),
                         (x + d, y0 + 4 * d), group=3))
    rows.append(_seg_row(206, None, "26", "Ririe Hwy", (x + d, y0 + 4 * d),
                         (x + 2 * d, y0 + 4 * d), group=3))
    net = gpd.GeoDataFrame(rows, crs="EPSG:4326")
    net["urban_area"] = "Idaho Falls, ID"
    net["urban_share"] = 1.0
    return net


class TestItem51Facilities:
    def test_a_segment_is_in_one_ranked_core_or_says_it_shares(self):
        net = _concurrent_network()
        ids = list(net["XDSegID"])
        ratios = {102: 3.0, 103: 3.0, 104: 3.0, 105: 3.0,
                  201: 1.6, 202: 1.6, 205: 1.6, 206: 1.6}
        cat = extents.generate_catalogue(net, _baseline(ids, ratios), observed=set(ids),
                                         min_chain_miles=0.5)
        cores = [g for g in cat["reporting_corridors"] if g["_tier"] == "core"]
        assert len(cores) == 2
        us26 = next(g for g in cores if g["_facility_name"].startswith("US-26")
                    or g["_facility_name"].startswith("SH-26"))
        assert any(f.startswith("shares ") for f in us26["_flags"])

    def test_a_core_mostly_inside_a_stronger_one_is_absorbed(self):
        net = _concurrent_network()
        ids = list(net["XDSegID"])
        ratios = {102: 3.0, 103: 3.0, 104: 3.0, 105: 3.0, 202: 1.6}
        cat = extents.generate_catalogue(net, _baseline(ids, ratios), observed=set(ids),
                                         min_chain_miles=0.5)
        assert len([g for g in cat["reporting_corridors"] if g["_tier"] == "core"]) == 1

    def test_facilities_are_named_for_street_and_town(self):
        net = _concurrent_network()
        ids = list(net["XDSegID"])
        cat = extents.generate_catalogue(net, _baseline(ids, {102: 3.0, 103: 3.0, 104: 3.0}),
                                         observed=set(ids), min_chain_miles=0.5)
        core = next(g for g in cat["reporting_corridors"] if g["_tier"] == "core")
        assert core["_facility_name"].endswith(": Yellowstone Hwy, Idaho Falls")
        assert "County" not in core["_facility_name"]

    def test_the_county_names_a_core_outside_every_urban_area(self):
        net = _concurrent_network()
        net["urban_share"] = 0.0
        ids = list(net["XDSegID"])
        cat = extents.generate_catalogue(net, _baseline(ids, {102: 3.0, 103: 3.0, 104: 3.0}),
                                         observed=set(ids), min_chain_miles=0.5)
        core = next(g for g in cat["reporting_corridors"] if g["_tier"] == "core")
        assert core["_facility_name"].endswith(", Latah County")

    def test_an_entry_names_the_links_the_network_does_not_assert(self):
        from inrix_tools import corridors
        net = _moscow_sh8()
        ids = list(net["XDSegID"])
        cat = extents.generate_catalogue(net, _baseline(ids, {1: 2.0, 2: 2.0, 4: 2.0, 5: 2.0}),
                                         observed=set(ids), min_chain_miles=0.5)
        core = next(e for e in cat["corridors"] if e["_tier"] == "core")
        assert [2, 4] in core["links"]
        res = corridors.resolve_catalogue(net, cat["corridors"])
        assert res["reached_target"].all()


class TestFacilityNaming:
    def _chain(self, ids, route="15", label="I-15"):
        return extents.MainlineChain(route_number=route, bearing="N", segment_ids=tuple(ids),
                                     miles=1.0, road_name="", route_label=label,
                                     localities=(), counties=("Bannock",),
                                     route_numbers=(route,), route_labels=(label,))

    def _frame(self, names, road_lists=None, urban=None):
        n = len(names)
        return pd.DataFrame({"RoadName": names, "RoadList": road_lists or [None] * n,
                             "Miles": [0.5] * n, "County": ["Bannock"] * n,
                             "urban_area": [urban] * n,
                             "urban_share": [1.0 if urban else 0.0] * n},
                            index=pd.Index(range(1, n + 1), name="XDSegID"))

    def _core(self, ids):
        return extents.CoreCandidate(start=0, stop=len(ids), segment_ids=tuple(ids), miles=1.0,
                                     effective_miles=1.0, vhd=100, vhd_per_mile=100,
                                     delay_per_mile=1, realtime_share=1, peak_ratio=1.3,
                                     ref_tti=1.3, unknown_miles=0, n_aadt_missing=0,
                                     baseline_sources=("night",), fails=())

    def test_a_business_loop_says_so(self):
        net = self._frame(["S 5th Ave"] * 3, ["S 5th Ave|I-15-BL|US-30"] * 3, "Pocatello, ID")
        band, street, place = extents.facility_naming(self._chain([1, 2, 3]),
                                                      self._core([1, 2, 3]), net, net)
        assert (band, street, place) == ("I-15 BL", "5th Ave", "Pocatello")

    def test_a_road_named_only_by_its_route_has_no_street(self):
        net = self._frame(["US-95", "Highway 95", "95 N"])
        band, street, place = extents.facility_naming(self._chain([1, 2, 3], "95", "US-95"),
                                                      self._core([1, 2, 3]), net, net)
        assert street == "" and place == "Bannock County"

    def test_byway_aliases_are_not_street_names(self):
        net = self._frame(["US-20"] * 3, ["US-20|Idaho Medal of Honor Hwy"] * 3)
        _, street, _ = extents.facility_naming(self._chain([1, 2, 3], "20", "US-20"),
                                               self._core([1, 2, 3]), net, net)
        assert street == ""

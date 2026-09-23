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


class TestGenerateCatalogue:
    def _catalogue(self, **kwargs):
        net = _two_route_network()
        ids = list(net["XDSegID"])
        scr = pd.DataFrame({
            "ref_speed": [60.0] * len(ids),
            # US-95 NB segments 101..103 congested; everything else free-flowing.
            "pm_speed": [30.0 if s in (101, 102, 103) else 59.0 for s in ids],
        }, index=pd.Index(ids, name="Segment ID"))
        return net, extents.generate_catalogue(net, screen_data=scr, window="pm",
                                               observed=set(ids), **kwargs)

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

    def test_both_directions_of_a_divided_facility_are_emitted(self):
        _, cat = self._catalogue()
        by_group = {}
        for e in cat["corridors"]:
            by_group.setdefault(e["corridor"], set()).add(e["direction"])
        assert all(d == {"NB", "SB"} for d in by_group.values())

    def test_only_congested_facilities_are_catalogued(self):
        """SH-53 free-flows, so it is not a corridor — the point of Item 43."""
        _, cat = self._catalogue()
        assert all(e["id"].startswith("us-95") for e in cat["corridors"])

    def test_coincident_tiers_are_emitted_once_at_the_widest_label(self):
        _, cat = self._catalogue()
        extents_seen = [tuple(e["_segment_ids"]) for e in cat["corridors"]
                        if e["direction"] == "NB"]
        assert len(extents_seen) == len(set(extents_seen))

    def test_the_core_is_tighter_than_the_regional_baseline(self):
        _, cat = self._catalogue()
        by_tier = {e["_tier"]: e for e in cat["corridors"] if e["direction"] == "NB"}
        assert by_tier["core"]["_generated_miles"] < by_tier["regional"]["_generated_miles"]

    def test_max_facilities_caps_the_catalogue(self):
        _, cat = self._catalogue(max_facilities=0)
        assert cat["corridors"] == []

    def test_an_unobserved_facility_is_not_catalogued(self):
        net = _two_route_network()
        ids = list(net["XDSegID"])
        scr = pd.DataFrame({"ref_speed": [60.0] * len(ids),
                            "pm_speed": [30.0 if s in (101, 102, 103) else 59.0
                                         for s in ids]},
                           index=pd.Index(ids, name="Segment ID"))
        cat = extents.generate_catalogue(net, screen_data=scr, window="pm",
                                         observed={300, 301})
        assert cat["corridors"] == []

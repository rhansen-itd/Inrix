"""Tests for inrix_tools.extents — split criteria and multi-scale tiers."""
from __future__ import annotations

import geopandas as gpd
import numpy as np
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

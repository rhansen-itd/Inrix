"""Tests for inrix_tools.corridors (ROADMAP Item 28)."""
import math
from pathlib import Path

import geopandas as gpd
import pandas as pd
import pytest
from shapely.geometry import LineString, Point

from inrix_tools import corridors, geometry

REPO_ROOT = Path(__file__).resolve().parents[1]
XD_ZIP = REPO_ROOT / "USA_Idaho_shapefile.zip"

LON = -116.3545          # a meridian through the study area
LAT0, DLAT = 43.60, 0.01  # each toy segment spans 0.01 deg of latitude


# ---------------------------------------------------------------------------
# Synthetic fixtures
# ---------------------------------------------------------------------------
def _chain_network(n=4, ids=None, lon=LON, next_ids=None):
    """``n`` collinear northbound segments up a meridian, each ``Miles = 1.0``,
    linked head-to-tail with the last one terminal."""
    ids = ids or [1000 + i for i in range(n)]
    nxt = next_ids if next_ids is not None else [ids[i + 1] for i in range(n - 1)] + [pd.NA]
    geoms = [
        LineString([(lon, LAT0 + i * DLAT), (lon, LAT0 + (i + 1) * DLAT)])
        for i in range(n)
    ]
    return gpd.GeoDataFrame(
        {
            "XDSegID": pd.array(ids, dtype="Int64"),
            "NextXDSegI": pd.array(nxt, dtype="Int64"),
            "Miles": [1.0] * n,
            "geometry": geoms,
        },
        crs=geometry.WGS84,
    )


def _at(frac_along_chain_segment, seg_index, lon=LON):
    """``(lat, lon)`` a given fraction along toy segment ``seg_index``."""
    return (LAT0 + (seg_index + frac_along_chain_segment) * DLAT, lon)


# ---------------------------------------------------------------------------
# The walk
# ---------------------------------------------------------------------------
def test_walk_chain_reaches_target():
    net = _chain_network(4)
    chain, reason = corridors.walk_chain(net, 1000, 1003)
    assert chain == [1000, 1001, 1002, 1003]
    assert reason == "target"


def test_walk_chain_dead_end():
    """Walking away from the target hits the network end, reported as a reason."""
    net = _chain_network(4)
    chain, reason = corridors.walk_chain(net, 1002, 1000)   # target is upstream
    assert chain == [1002, 1003]
    assert reason == "dead_end"


def test_walk_chain_off_network_and_cycle():
    # next_id points outside this subset -> "off_network" (widen the subset),
    # which must not be confused with a genuine terminal segment.
    net = _chain_network(2, next_ids=[1001, 9999])
    assert corridors.walk_chain(net, 1000, 4242) == ([1000, 1001], "off_network")
    # a ring: 1000 -> 1001 -> 1000 ...
    ring = _chain_network(2, next_ids=[1001, 1000])
    assert corridors.walk_chain(ring, 1000, 4242) == ([1000, 1001], "cycle")
    # a start segment absent from the network at all
    assert corridors.walk_chain(net, 777, 1001) == ([], "off_network")


def test_walk_chain_max_steps():
    net = _chain_network(5)
    chain, reason = corridors.walk_chain(net, 1000, 1004, max_steps=2)
    assert reason == "max_steps" and chain == [1000, 1001, 1002]


# ---------------------------------------------------------------------------
# Snapping: projected, not degrees  (G7)
# ---------------------------------------------------------------------------
def test_snap_measured_in_projected_crs_not_degrees():
    """A case where degree-space and metre-space disagree about "nearest".

    At latitude 43.6 a degree of longitude is only ~0.72 of a degree of latitude
    on the ground, so a candidate 0.012 deg away in *longitude* (~967 m) is nearer
    than one 0.010 deg away in *latitude* (~1113 m) while looking farther in raw
    degrees. The Gemini pass measured in degrees (harmless at 90 ft, not here).
    """
    lat, lon = 43.60, -116.35
    north = LineString([(lon - 0.05, lat + 0.010), (lon + 0.05, lat + 0.010)])  # 0.010 deg away
    east = LineString([(lon + 0.012, lat - 0.05), (lon + 0.012, lat + 0.05)])   # 0.012 deg away
    net = gpd.GeoDataFrame(
        {"XDSegID": pd.array([11, 22], dtype="Int64"),
         "NextXDSegI": pd.array([pd.NA, pd.NA], dtype="Int64"),
         "Miles": [1.0, 1.0], "geometry": [north, east]},
        crs=geometry.WGS84,
    )
    pt = Point(lon, lat)
    # raw degrees would rank the *northern* segment first ...
    assert north.distance(pt) < east.distance(pt)
    # ... but on the ground the eastern one is closer, and that is what we return.
    ranked = corridors.snap_candidates(corridors.project_network(net), (lat, lon), k=2)
    assert [sid for sid, _, _ in ranked] == [22, 11]
    nearest_ft = ranked[0][1]
    assert nearest_ft == pytest.approx(967.0 * 3.28084, rel=0.02)


def test_snap_candidates_restricted_to_candidate_ids():
    net = _chain_network(4)
    proj = corridors.project_network(net)
    got = corridors.snap_candidates(proj, _at(0.5, 0), k=4, candidates=[1002, 1003])
    assert [sid for sid, _, _ in got] == [1002, 1003]
    assert corridors.snap_candidates(proj, _at(0.5, 0), candidates=[]) == []


# ---------------------------------------------------------------------------
# Chain assembly + endpoint trim  (G7)
# ---------------------------------------------------------------------------
def test_build_chain_trim_hand_computed():
    """Query points 25% into segment 1 and 50% into segment 3 of a 1-mile-per-
    segment chain: 3 whole miles assembled, 2.25 requested, 0.25 + 0.50 trimmed."""
    net = _chain_network(4)
    ch = corridors.build_chain(net, _at(0.25, 0), _at(0.50, 2))

    assert ch.reached_target and ch.stop_reason == "target"
    assert ch.segment_ids == (1000, 1001, 1002)
    assert ch.chain_miles == pytest.approx(3.0)
    assert ch.trim_start_miles == pytest.approx(0.25, abs=1e-3)
    assert ch.trim_end_miles == pytest.approx(0.50, abs=1e-3)
    assert ch.requested_miles == pytest.approx(2.25, abs=2e-3)
    assert ch.length_ratio == pytest.approx(3.0 / 2.25, abs=2e-3)
    assert ch.extent_fraction[1] == 1.0                      # interior untouched
    assert ch.snap_start_feet < 1.0 and ch.snap_end_feet < 1.0

    frame = ch.frame()
    assert list(frame[corridors.SEQ_COL]) == [1, 2, 3]
    assert frame[corridors.EXTENT_MILES_COL].sum() == pytest.approx(2.25, abs=2e-3)
    assert frame.attrs["length_ratio"] == pytest.approx(3.0 / 2.25, abs=2e-3)
    assert ch.weights()[1001] == 1.0


def test_build_chain_single_segment_span():
    net = _chain_network(4)
    ch = corridors.build_chain(net, _at(0.2, 1), _at(0.7, 1))
    assert ch.segment_ids == (1001,)
    assert ch.requested_miles == pytest.approx(0.5, abs=2e-3)
    assert ch.trim_start_miles == pytest.approx(0.2, abs=2e-3)
    assert ch.trim_end_miles == pytest.approx(0.3, abs=2e-3)


def test_build_chain_unreachable_is_reported_not_raised():
    """Endpoints in the wrong order: the walk runs off the end of the network and
    the result says so instead of returning a plausible-looking chain."""
    net = _chain_network(4)
    ch = corridors.build_chain(net, _at(0.5, 2), _at(0.5, 0))
    assert ch.reached_target is False
    assert ch.stop_reason == "dead_end"
    assert math.isnan(ch.trim_end_miles)    # no meaningful downstream trim
    assert math.isnan(ch.summary()["trim_end_miles"])


def test_build_chain_prefers_the_candidate_that_reaches_the_target():
    """The *nearest* segment can be the opposing direction of a divided road; the
    k-candidate search picks the pair that actually connects."""
    nb = _chain_network(3, ids=[2000, 2001, 2002], lon=LON)
    # a southbound carriageway 30 m east, nearer to the query points, running the
    # other way (its walk can never reach the northbound target).
    sb_lon = LON + 0.0004
    sb = gpd.GeoDataFrame(
        {"XDSegID": pd.array([3000, 3001, 3002], dtype="Int64"),
         "NextXDSegI": pd.array([3001, 3002, pd.NA], dtype="Int64"),
         "Miles": [1.0, 1.0, 1.0],
         "geometry": [LineString([(sb_lon, LAT0 + (i + 1) * DLAT), (sb_lon, LAT0 + i * DLAT)])
                      for i in (2, 1, 0)]},
        crs=geometry.WGS84,
    )
    net = pd.concat([nb, sb]).pipe(gpd.GeoDataFrame, crs=geometry.WGS84)
    east_of_centre = LON + 0.0003   # closer to the SB line than the NB line

    nearest = corridors.snap_candidates(corridors.project_network(net),
                                        _at(0.5, 0, lon=east_of_centre), k=1)
    assert nearest[0][0] == 3002                     # nearest really is southbound
    ch = corridors.build_chain(net, _at(0.5, 0, lon=east_of_centre),
                               _at(0.5, 2, lon=east_of_centre))
    assert ch.reached_target and ch.segment_ids == (2000, 2001, 2002)


def test_build_chain_falls_back_to_segment_geometry_for_miles():
    """No ``Miles`` column: lengths come from the projected geometry (~0.69 mi per
    0.01 deg of latitude), not from nothing."""
    net = _chain_network(2).drop(columns=["Miles"])
    ch = corridors.build_chain(net, _at(0.0, 0), _at(1.0, 1))
    assert ch.chain_miles == pytest.approx(2 * 0.6906, rel=0.01)


# ---------------------------------------------------------------------------
# Missing-segment accounting  (G8)
# ---------------------------------------------------------------------------
def _obs(segment_ids, n=3, value_col="Travel Time(Minutes)"):
    stamps = pd.date_range("2026-03-02 08:00", periods=n, freq="15min", tz="America/Denver")
    rows = [{"Segment ID": s, "Date Time": t, value_col: 1.0}
            for s in segment_ids for t in stamps]
    df = pd.DataFrame(rows)
    df.attrs["units"] = {"travel_time": "Minutes"}
    return df


def test_chain_coverage_reports_missing_members_and_miles():
    net = _chain_network(4)
    ch = corridors.build_chain(net, _at(0.25, 0), _at(0.50, 2))   # members 1000-1002
    cov = corridors.chain_coverage(ch, _obs([1000, 1002]))        # 1001 never reported

    assert list(cov[corridors.OBSERVED_COL]) == [True, False, True]
    assert list(cov[corridors.NOBS_COL]) == [3, 0, 3]
    assert cov.attrs["n_missing"] == 1
    # the absent member is a whole interior mile of the 2.25-mile extent
    assert cov.attrs["missing_miles"] == pytest.approx(1.0, abs=2e-3)
    assert cov.attrs["observed_miles"] == pytest.approx(1.25, abs=2e-3)
    assert cov.attrs["miles_covered_fraction"] == pytest.approx(1.25 / 2.25, abs=2e-3)
    assert cov.attrs["reached_target"] is True      # chain summary carried through


def test_chain_coverage_accepts_plain_ids_and_is_value_aware():
    net = _chain_network(4)
    ch = corridors.build_chain(net, _at(0.25, 0), _at(0.50, 2))
    assert corridors.chain_coverage(ch, [1000, 1001, 1002]).attrs["n_missing"] == 0

    # a member that reports rows but no *values* is missing for that metric
    df = _obs([1000, 1001, 1002])
    df.loc[df["Segment ID"] == 1001, "Travel Time(Minutes)"] = float("nan")
    cov = corridors.chain_coverage(ch, df, value="Travel Time(Minutes)")
    assert cov.attrs["n_missing"] == 1
    assert corridors.chain_coverage(ch, df).attrs["n_missing"] == 0   # rows exist


# ---------------------------------------------------------------------------
# Prorated chain travel time (the trim decision, applied)
# ---------------------------------------------------------------------------
def test_chain_travel_time_prorates_the_end_segments():
    net = _chain_network(4)
    ch = corridors.build_chain(net, _at(0.25, 0), _at(0.50, 2))
    df = _obs([1000, 1001, 1002])          # 1.0 min on every member, 3 timestamps

    prorated = corridors.chain_travel_time(df, ch)
    assert len(prorated) == 3
    # 0.75 + 1.0 + 0.50 minutes
    assert prorated["Travel Time(Minutes)"].iloc[0] == pytest.approx(2.25, abs=2e-3)
    assert prorated["Length(Miles)"].iloc[0] == pytest.approx(2.25, abs=2e-3)
    assert prorated["Corridor Speed(miles/hour)"].iloc[0] == pytest.approx(60.0, abs=0.5)
    assert prorated.attrs["prorated"] is True
    assert prorated.attrs["chain"]["length_ratio"] == pytest.approx(4 / 3, abs=2e-3)

    whole = corridors.chain_travel_time(df, ch, prorate=False)
    assert whole["Travel Time(Minutes)"].iloc[0] == pytest.approx(3.0)
    assert whole["Length(Miles)"].iloc[0] == pytest.approx(3.0)
    assert whole.attrs["prorated"] is False


def test_chain_travel_time_keeps_the_complete_set_rule():
    """A member missing at a timestamp drops that timestamp — it must never
    silently shorten the sum (G8)."""
    net = _chain_network(4)
    ch = corridors.build_chain(net, _at(0.25, 0), _at(0.50, 2))
    df = _obs([1000, 1001, 1002])
    df = df.drop(df[(df["Segment ID"] == 1001)].index[:1])    # one gap

    complete = corridors.chain_travel_time(df, ch)
    assert len(complete) == 2
    assert (complete["n_segments"] == 3).all()

    with_partials = corridors.chain_travel_time(df, ch, require_complete=False)
    assert len(with_partials) == 3
    assert list(with_partials["complete"]) == [False, True, True]
    # segments outside the chain never enter the sum
    extra = pd.concat([df, _obs([9999])], ignore_index=True)
    extra.attrs = df.attrs
    assert len(corridors.chain_travel_time(extra, ch)) == 2


def test_chain_travel_time_rejects_a_missing_value_column():
    net = _chain_network(2)
    ch = corridors.build_chain(net, _at(0.0, 0), _at(1.0, 1))
    with pytest.raises(ValueError):
        corridors.chain_travel_time(pd.DataFrame({"Segment ID": [1000]}), ch)


# ---------------------------------------------------------------------------
# Real XD network (skipped without the shapefile)
# ---------------------------------------------------------------------------
@pytest.mark.skipif(not XD_ZIP.exists(), reason="XD shapefile not available")
def test_real_chain_reproduces_the_franklin_asymmetry():
    """The G7 finding, now measured: Franklin WB assembles 3.453 mi where EB
    assembles 2.993 mi over the *same* physical extent — and the endpoint trim
    reconciles them to within 0.01 mi of each other."""
    net = geometry.load_xd_network(XD_ZIP, bbox=(-116.70, 43.63, -116.58, 43.69))
    west, east = (43.662633, -116.664553), (43.663139, -116.609317)
    eb = corridors.build_chain(net, west, east)
    wb = corridors.build_chain(net, east, west)

    assert eb.reached_target and wb.reached_target
    assert eb.chain_miles == pytest.approx(2.993, abs=0.01)
    assert wb.chain_miles == pytest.approx(3.453, abs=0.01)
    assert wb.chain_miles - eb.chain_miles > 0.4          # the reported asymmetry
    assert wb.requested_miles == pytest.approx(eb.requested_miles, abs=0.01)
    assert wb.trim_start_miles == pytest.approx(0.568, abs=0.01)   # the overshoot end
    assert max(eb.snap_start_feet, wb.snap_start_feet) < 50


@pytest.mark.skipif(not XD_ZIP.exists(), reason="XD shapefile not available")
def test_real_chain_reproduces_the_vsl_overshoot():
    """VSL NB AM: 3.635 mi of chain against a ~3.00 mi request (+21%), with the
    overshoot localised to 0.63 mi on the downstream end."""
    net = geometry.load_xd_network(XD_ZIP, bbox=(-116.40, 43.59, -116.30, 43.69))
    ch = corridors.build_chain(net, (43.619530891287404, -116.35444430487458),
                               (43.663024006704134, -116.35452382550932))
    assert ch.reached_target and ch.n_segments == 7
    assert ch.chain_miles == pytest.approx(3.635, abs=0.01)
    assert ch.requested_miles == pytest.approx(3.006, abs=0.01)
    assert ch.length_ratio == pytest.approx(1.21, abs=0.01)
    assert ch.trim_end_miles == pytest.approx(0.626, abs=0.01)
    assert ch.snap_start_feet < 100 and ch.snap_end_feet < 100


def test_max_snap_feet_guard_prefers_the_true_snap_over_a_far_connection():
    """A query point 50 ft from a disconnected stub and 600 ft from the mainline.

    Inside the guard only the stub is a candidate, so the chain starts there and
    reports ``reached_target=False`` — the honest answer. With the guard disabled
    the far-but-connecting mainline wins instead, which is exactly how a 2-mile
    route can quietly collapse onto the wrong road.
    """
    main = _chain_network(4)
    pt_lon = LON + 0.00228                       # ~600 ft east of the mainline
    stub_lon = pt_lon + 0.00019                  # ~50 ft east of the query point
    stub = gpd.GeoDataFrame(
        {"XDSegID": pd.array([5000], dtype="Int64"),
         "NextXDSegI": pd.array([pd.NA], dtype="Int64"), "Miles": [1.0],
         "geometry": [LineString([(stub_lon, LAT0), (stub_lon, LAT0 + DLAT)])]},
        crs=geometry.WGS84,
    )
    net = pd.concat([main, stub], ignore_index=True).pipe(gpd.GeoDataFrame, crs=geometry.WGS84)
    start, end = _at(0.5, 0, lon=pt_lon), _at(0.5, 2)

    guarded = corridors.build_chain(net, start, end)
    assert guarded.segment_ids[0] == 5000
    assert guarded.reached_target is False and guarded.stop_reason == "dead_end"
    assert guarded.snap_start_feet == pytest.approx(50, abs=15)

    unguarded = corridors.build_chain(net, start, end, max_snap_feet=None)
    assert unguarded.reached_target and unguarded.segment_ids[0] == 1000
    assert unguarded.snap_start_feet == pytest.approx(600, abs=30)


# ---------------------------------------------------------------------------
# Chains from named terminal segments + network-derived labels  (Item 30)
# ---------------------------------------------------------------------------
def _named_network(n=4):
    """The toy chain with road attributes, as the XD network carries them."""
    net = _chain_network(n)
    net["RoadName"] = ["Banks Lowman Hwy", "Banks Lowman Hwy", "ID-55", "N Main St"][:n]
    net["RoadNumber"] = [None, None, "55", "55"][:n]
    net["County"] = ["Boise"] * n
    net["PostalCode"] = ["83602"] * n
    return net


def test_chain_between_segments_walks_and_reports_no_snap():
    """A route given by its end segments: whole extent, no trim, NaN snaps —
    the accounting a query point would have provided is absent, not faked."""
    chain = corridors.chain_between_segments(_named_network(4), 1000, 1003)
    assert chain.segment_ids == (1000, 1001, 1002, 1003)
    assert chain.reached_target and chain.stop_reason == "target"
    assert chain.extent_fraction == (1.0, 1.0, 1.0, 1.0)
    assert chain.requested_miles == pytest.approx(chain.chain_miles) == pytest.approx(4.0)
    assert chain.length_ratio == pytest.approx(1.0)
    assert math.isnan(chain.snap_start_feet) and math.isnan(chain.snap_end_feet)


def test_chain_between_segments_keeps_a_short_walk_as_a_finding():
    """Walking away from the target dead-ends; that is reported, not raised —
    the report excludes such a route instead of scoring the wrong pavement."""
    chain = corridors.chain_between_segments(_named_network(4), 1002, 1000)
    assert not chain.reached_target and chain.stop_reason == "dead_end"
    assert chain.segment_ids == (1002, 1003)


def test_chain_between_segments_rejects_a_segment_outside_the_subset():
    with pytest.raises(ValueError, match="off_network"):
        corridors.chain_between_segments(_named_network(4), 777, 1003)


def test_chain_attributes_joins_the_network_in_travel_order():
    net = _named_network(4)
    chain = corridors.chain_between_segments(net, 1000, 1003)
    attrs = corridors.chain_attributes(chain, net)
    assert list(attrs[corridors.SEQ_COL]) == [1, 2, 3, 4]
    assert list(attrs["RoadName"]) == ["Banks Lowman Hwy", "Banks Lowman Hwy",
                                       "ID-55", "N Main St"]
    assert attrs.attrs["chain_miles"] == pytest.approx(4.0)


def test_chain_description_reads_the_label_off_the_segments():
    """The label the report prints comes from the network, so a caption like
    "SH-17" (no such Idaho route) cannot survive the check against it."""
    net = _named_network(4)
    desc = corridors.chain_description(corridors.chain_between_segments(net, 1000, 1003), net)
    assert desc["RoadName"] == ("Banks Lowman Hwy", "ID-55", "N Main St")   # ordered, deduped
    assert desc["RoadNumber"] == ("55",)
    assert desc["County"] == ("Boise",)
    assert "Banks Lowman Hwy" in desc["road_label"] and "ID-55" in desc["road_label"]

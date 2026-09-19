"""Tests for inrix_tools.corridors (ROADMAP Items 28 and 36)."""
import json
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


# ---------------------------------------------------------------------------
# Requested vs. observed membership  (Item 31)
# ---------------------------------------------------------------------------
def test_chain_travel_time_marks_a_never_present_member_short():
    """The Eagle Rd NB case: a member the export never supplies used to vanish
    into the expected count, so a 2-of-3 sum passed as complete."""
    net = _chain_network(4)
    ch = corridors.build_chain(net, _at(0.25, 0), _at(0.50, 2))   # members 1000-1002
    df = _obs([1000, 1002])                                       # 1001 never appears

    out = corridors.chain_travel_time(df, ch)
    assert len(out) == 3                       # the sum is still reported...
    assert (out["n_requested"] == 3).all()     # ...against the requested membership
    assert (out["n_absent"] == 1).all()
    assert (out["expected_segments"] == 2).all()
    assert out["short"].all()                  # ...and it is labelled short
    assert out["complete"].all()               # complete *of what is achievable*


def test_chain_travel_time_on_absent_drop_collapses_the_series():
    """The other explicit choice: measure against the full request and drop
    everything. Correct, useless, and available on purpose."""
    net = _chain_network(4)
    ch = corridors.build_chain(net, _at(0.25, 0), _at(0.50, 2))
    df = _obs([1000, 1002])

    out = corridors.chain_travel_time(df, ch, on_absent="drop")
    assert out.empty
    kept = corridors.chain_travel_time(df, ch, on_absent="drop", require_complete=False)
    assert len(kept) == 3
    assert (kept["expected_segments"] == 3).all()
    assert not kept["complete"].any()


def test_chain_travel_time_sometimes_missing_still_drops_the_timestamp():
    """The distinction that matters: *sometimes* missing is not *never* present.
    A member with one gap keeps the old rule and is not marked short."""
    net = _chain_network(4)
    ch = corridors.build_chain(net, _at(0.25, 0), _at(0.50, 2))
    df = _obs([1000, 1001, 1002])
    df = df.drop(df[df["Segment ID"] == 1001].index[:1])

    out = corridors.chain_travel_time(df, ch, require_complete=False)
    assert not out["short"].any()
    assert (out["n_absent"] == 0).all()
    assert list(out["complete"]) == [False, True, True]


def test_chain_travel_time_does_not_credit_unobserved_end_segment_miles():
    """Eagle Rd NB's first and last members are both absent; crediting their
    prorated pavement to the sum overstated the length — and the speed with it."""
    net = _chain_network(4)
    ch = corridors.build_chain(net, _at(0.25, 0), _at(0.50, 2))   # 0.75 + 1.0 + 0.50
    df = _obs([1001, 1002])                                       # the trimmed start is gone

    out = corridors.chain_travel_time(df, ch)
    assert out["requested_length_miles"].iloc[0] == pytest.approx(2.25, abs=2e-3)
    assert out["missing_length_miles"].iloc[0] == pytest.approx(0.75, abs=2e-3)
    assert out["Length(Miles)"].iloc[0] == pytest.approx(1.50, abs=2e-3)
    assert (out["length_basis"] == "observed").all()
    # 1.0 + 0.50 minutes over 1.50 miles is 60 mph; crediting the absent 0.75 mi
    # would have reported 90.
    assert out["Corridor Speed(miles/hour)"].iloc[0] == pytest.approx(60.0, abs=0.5)


def test_chain_travel_time_length_basis_is_requested_when_nothing_is_missing():
    """The correction must not move an intact chain's published length."""
    net = _chain_network(4)
    ch = corridors.build_chain(net, _at(0.25, 0), _at(0.50, 2))
    out = corridors.chain_travel_time(_obs([1000, 1001, 1002]), ch)
    assert (out["length_basis"] == "requested").all()
    assert out["Length(Miles)"].iloc[0] == pytest.approx(ch.requested_miles)
    assert out["missing_length_miles"].iloc[0] == pytest.approx(0.0, abs=1e-9)


# ---------------------------------------------------------------------------
# The CValue gate on the chain path  (Item 31)
# ---------------------------------------------------------------------------
def _obs_cvalue(segment_ids, cvalues, n=3):
    """``_obs`` plus a per-segment CValue (``None`` -> null, the imputation marker)."""
    df = _obs(segment_ids, n=n)
    df["CValue"] = df["Segment ID"].map(cvalues).astype("float64")
    return df


def test_chain_travel_time_gate_records_the_threshold_and_the_cost():
    net = _chain_network(4)
    ch = corridors.build_chain(net, _at(0.25, 0), _at(0.50, 2))
    # 1001 is historical backfill throughout: null CValue, so the gate drops it.
    df = _obs_cvalue([1000, 1001, 1002], {1000: 95.0, 1001: None, 1002: 90.0})

    gated = corridors.chain_travel_time(df, ch, cvalue_threshold=80)
    assert gated.attrs["cvalue_threshold"] == 80
    assert gated["imputed_fraction"].tolist() == pytest.approx([1 / 3] * 3)
    assert gated["cvalue_kept_fraction"].tolist() == pytest.approx([2 / 3] * 3)
    # the gated-away member is now absent, so the sum is short, not silently 2-of-3
    assert gated["short"].all()
    assert (gated["n_absent"] == 1).all()
    assert gated["Length(Miles)"].iloc[0] == pytest.approx(1.25, abs=2e-3)


def test_chain_travel_time_measures_imputation_even_when_ungated():
    """The share is route- and hour-dependent, so it is measured whether or not
    the gate is applied — 'we gated' on its own says very little."""
    net = _chain_network(4)
    ch = corridors.build_chain(net, _at(0.25, 0), _at(0.50, 2))
    df = _obs_cvalue([1000, 1001, 1002], {1000: 95.0, 1001: None, 1002: 90.0})

    ungated = corridors.chain_travel_time(df, ch, cvalue_threshold=None)
    assert ungated.attrs["cvalue_threshold"] is None
    assert ungated["imputed_fraction"].tolist() == pytest.approx([1 / 3] * 3)
    assert ungated["cvalue_kept_fraction"].tolist() == pytest.approx([1.0] * 3)
    assert not ungated["short"].any()          # nothing was gated away
    assert ungated["Length(Miles)"].iloc[0] == pytest.approx(2.25, abs=2e-3)


def test_chain_travel_time_gate_requires_a_cvalue_column():
    net = _chain_network(4)
    ch = corridors.build_chain(net, _at(0.25, 0), _at(0.50, 2))
    df = _obs([1000, 1001, 1002])                      # no CValue column at all
    with pytest.raises(ValueError, match="cvalue_threshold=None"):
        corridors.chain_travel_time(df, ch, cvalue_threshold=80)
    assert "imputed_fraction" not in corridors.chain_travel_time(df, ch).columns


def test_chain_coverage_reports_the_imputed_share_per_member():
    net = _chain_network(4)
    ch = corridors.build_chain(net, _at(0.25, 0), _at(0.50, 2))
    df = _obs_cvalue([1000, 1001, 1002], {1000: 95.0, 1001: None, 1002: 90.0})

    cov = corridors.chain_coverage(ch, df, value="Travel Time(Minutes)")
    assert cov.attrs["n_missing"] == 0                      # every member reports...
    assert list(cov[corridors.NIMPUTED_COL]) == [0, 3, 0]   # ...one of them is backfill
    assert list(cov[corridors.IMPUTED_FRACTION_COL]) == [0.0, 1.0, 0.0]
    assert cov.attrs["imputed_fraction"] == pytest.approx(1 / 3)


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


# ---------------------------------------------------------------------------
# The corridor catalogue: load, validate, resolve, account  (Item 36)
# ---------------------------------------------------------------------------
D3_CATALOGUE = REPO_ROOT / "scripts" / "d3_corridors.json"


def _entry(**kw):
    """A valid catalogue entry dict, with fields overridable per test."""
    return {
        "id": kw.pop("id", "toy"),
        "name": kw.pop("name", "Toy Rd NB"),
        "start_latlon": kw.pop("start_latlon", list(_at(0.5, 0))),
        "end_latlon": kw.pop("end_latlon", list(_at(0.5, 3))),
        "description": kw.pop("description", "Why this extent is the one that matters."),
        **kw,
    }


def _parallel_trap_network():
    """The trap the bounding-box + geographic-sort approach walks into.

    Four northbound mainline segments up a meridian (1000..1003) and a two-segment
    frontage road ~30 m to the east (2000, 2001) that parallels the third. The
    mainline's ``NextXDSegI`` leaves the mainline at 1001 and points at the frontage
    road; 1002 is reachable from nothing. A chain from the south end to the north
    end therefore *cannot* be walked — and anything that "completes" it by sorting
    the segments in the box by latitude sums two parallel facilities in series.
    """
    main = _chain_network(4, next_ids=[1001, 2000, 1003, pd.NA])
    frontage_lon = LON + 0.0004                       # ~32 m east at this latitude
    frontage = gpd.GeoDataFrame(
        {
            "XDSegID": pd.array([2000, 2001], dtype="Int64"),
            "NextXDSegI": pd.array([2001, pd.NA], dtype="Int64"),
            "Miles": [0.5, 0.5],
            "geometry": [
                LineString([(frontage_lon, LAT0 + 2 * DLAT),
                            (frontage_lon, LAT0 + 2.5 * DLAT)]),
                LineString([(frontage_lon, LAT0 + 2.5 * DLAT),
                            (frontage_lon, LAT0 + 3 * DLAT)]),
            ],
        },
        crs=geometry.WGS84,
    )
    return gpd.GeoDataFrame(pd.concat([main, frontage], ignore_index=True),
                            crs=geometry.WGS84)


def test_parse_catalogue_accepts_a_well_formed_entry():
    (entry,) = corridors.parse_catalogue({"corridors": [_entry(_comment="ignored")]})
    assert entry.id == "toy" and entry.name == "Toy Rd NB"
    assert entry.start_latlon == pytest.approx(_at(0.5, 0))
    assert entry.description.startswith("Why this extent")
    # a bare list is accepted too
    assert len(corridors.parse_catalogue([_entry(), _entry(id="toy2")])) == 2


@pytest.mark.parametrize("bad, match", [
    ({"corridors": [_entry(description="  ")]}, "empty 'description'"),
    ({"corridors": [_entry(), _entry()]}, "Duplicate catalogue id"),
    ({"corridors": [{k: v for k, v in _entry().items() if k != "end_latlon"}]}, "missing"),
    ({"corridors": [_entry(start_latlon=[-116.35, 43.6])]}, "out of range"),
    ({"corridors": [_entry(start_latlong=[43.6, -116.35])]}, "unknown field"),
    ({"corridors": [_entry(id="")]}, "has no 'id'"),
    ({"corridors": []}, "non-empty list"),
    ({"routes": [_entry()]}, "no 'corridors' key"),
])
def test_parse_catalogue_rejects_what_would_resolve_somewhere_else(bad, match):
    """Every validation failure is loud. A silently ignored ``start_latlong`` is a
    corridor resolved at whatever the *other* endpoint's default happened to be."""
    with pytest.raises(ValueError, match=match):
        corridors.parse_catalogue(bad)


def test_load_catalogue_reads_json(tmp_path):
    path = tmp_path / "cat.json"
    path.write_text(json.dumps({"_note": "provenance", "corridors": [_entry()]}))
    (entry,) = corridors.load_catalogue(path)
    assert entry.id == "toy"


def test_resolve_catalogue_accounts_for_every_entry():
    net = _chain_network(4)
    cat = [_entry(id="through"),
           _entry(id="backwards", start_latlon=list(_at(0.5, 3)),
                  end_latlon=list(_at(0.5, 0)))]      # walks away from its target
    res = corridors.resolve_catalogue(net, cat)

    assert list(res["id"]) == ["through", "backwards"]
    assert [bool(v) for v in res["accepted"]] == [True, False]
    assert list(res["stop_reason"]) == ["target", "dead_end"]
    assert res.loc[0, "n_segments"] == 4
    assert res.loc[0, "requested_miles"] == pytest.approx(3.0)
    assert res.attrs["findings"] == ["backwards"]
    assert res.attrs["n_entries"] == 2 and res.attrs["n_accepted"] == 1
    assert res.attrs["coverage_evaluated"] is False
    # the chains come back keyed by id, ready for screen.rank_corridors
    assert set(res.attrs["chains"]) == {"through", "backwards"}
    assert res.attrs["chains"]["through"].segment_ids == (1000, 1001, 1002, 1003)
    assert not any(c in res.columns for c in corridors.COVERAGE_COLUMNS)


def test_resolve_catalogue_does_not_bridge_a_parallel_facility():
    """The through chain is broken and a frontage road runs beside it. The entry is
    recorded as a finding — it is never completed by adding the segments that a
    geographic sort would have swept up."""
    net = _parallel_trap_network()
    res = corridors.resolve_catalogue(net, [_entry(id="trap")])

    row = res.iloc[0]
    assert not row["accepted"] and not row["reached_target"]
    assert row["stop_reason"] == "dead_end"
    chain = res.attrs["chains"]["trap"]
    # the walk left the mainline onto the frontage road and stopped there ...
    assert chain.segment_ids == (1000, 1001, 2000, 2001)
    # ... and the unreachable mainline segments were NOT bridged in behind it
    assert 1002 not in chain.segment_ids and 1003 not in chain.segment_ids
    assert chain.chain_miles == pytest.approx(3.0)   # not the 5.0 a sort would give


def test_resolve_catalogue_coverage_decides_acceptance():
    """Reaching the target is half the test: an entry whose export never observed a
    member is a finding too, and the miles say how much is missing."""
    net = _chain_network(4)
    cat = [_entry(id="through")]
    observed_all = corridors.resolve_catalogue(net, cat, observed=[1000, 1001, 1002, 1003])
    assert observed_all.loc[0, "miles_covered_fraction"] == pytest.approx(1.0)
    assert bool(observed_all.loc[0, "accepted"])
    assert observed_all.attrs["coverage_evaluated"] is True

    gap = corridors.resolve_catalogue(net, cat, observed=[1000, 1001, 1003])
    assert bool(gap.loc[0, "reached_target"])          # the walk was fine ...
    assert not gap.loc[0, "accepted"]                  # ... the coverage was not
    assert gap.loc[0, "n_missing"] == 1
    assert gap.loc[0, "missing_miles"] == pytest.approx(1.0)
    assert gap.loc[0, "miles_covered_fraction"] == pytest.approx(1.0 / 3.0 * 2)
    assert gap.attrs["findings"] == ["through"]
    # the threshold is the caller's, and it is recorded
    loose = corridors.resolve_catalogue(net, cat, observed=[1000, 1001, 1003],
                                        min_coverage=0.5)
    assert bool(loose.loc[0, "accepted"]) and loose.attrs["min_coverage"] == 0.5


# ---------------------------------------------------------------------------
# The District 3 catalogue itself
# ---------------------------------------------------------------------------
def test_d3_catalogue_loads_and_validates():
    cat = corridors.load_catalogue(D3_CATALOGUE)
    assert len(cat) >= 15
    assert len({e.id for e in cat}) == len(cat)
    for entry in cat:
        lat0, lon0 = entry.start_latlon
        lat1, lon1 = entry.end_latlon
        # every extent is inside District 3 and is not a zero-length "corridor"
        assert 42.9 <= lat0 <= 45.1 and -117.3 <= lon0 <= -115.4
        assert 42.9 <= lat1 <= 45.1 and -117.3 <= lon1 <= -115.4
        assert (lat0, lon0) != (lat1, lon1)
        assert len(entry.description) > 80      # the *why*, not a label


@pytest.mark.skipif(not XD_ZIP.exists(), reason="XD shapefile not available")
def test_d3_catalogue_resolves_i84_mainline_only():
    """I-84 EB (IC 35 -> IC 49) walks as mainline, which is the whole point: the
    outside pass's bounding box swept in the Garrity Blvd frontage road and summed
    1.37 mi of the same ground twice."""
    cat = [e for e in corridors.load_catalogue(D3_CATALOGUE) if e.id == "i84-eb"]
    net = geometry.load_xd_network(XD_ZIP, bbox=(-116.62, 43.55, -116.27, 43.65))
    res = corridors.resolve_catalogue(net, cat)

    row = res.iloc[0]
    assert bool(row["accepted"]) and row["stop_reason"] == "target"
    assert row["chain_miles"] == pytest.approx(15.12, abs=0.05)
    assert max(row["snap_start_feet"], row["snap_end_feet"]) < 50
    roads = corridors.chain_description(res.attrs["chains"]["i84-eb"], net)["RoadName"]
    assert roads == ("I-84 E",)                 # no frontage road, no ramps


@pytest.mark.skipif(not XD_ZIP.exists(), reason="XD shapefile not available")
def test_d3_catalogue_records_the_state_street_break_as_a_finding():
    """SH-44 east of Ballantyne Rd is unreachable without repairs in this XD vintage
    (eastbound NextXDSegI forks onto a parallel 1-lane State St at Ballantyne Rd).
    The catalogue records this as a finding when walked without the repair table."""
    ids = {"sh44-urban-eb"}
    cat = [e for e in corridors.load_catalogue(D3_CATALOGUE) if e.id in ids]
    net = geometry.load_xd_network(XD_ZIP, bbox=(-116.47, 43.64, -116.26, 43.72))
    res = corridors.resolve_catalogue(net, cat).set_index("id")

    assert not res.loc["sh44-urban-eb", "reached_target"]
    assert res.loc["sh44-urban-eb", "stop_reason"] in {"dead_end", "off_network"}
    assert res.attrs["findings"] == ["sh44-urban-eb"]


def test_resolved_chains_feed_rank_corridors():
    """``attrs['chains']`` is the shape ``screen.rank_corridors`` takes — the
    catalogue's output is the screening pass's input, with no adapter in between."""
    from inrix_tools import screen

    net = _chain_network(4)
    res = corridors.resolve_catalogue(net, [_entry(id="Toy Rd NB")])
    scr = pd.DataFrame(
        {"am_travel_time": [2.0] * 4, "am_speed": [30.0] * 4, "ref_speed": [60.0] * 4,
         "am_n_obs": [100] * 4, "am_kept_fraction": [1.0] * 4},
        index=pd.Index([1000, 1001, 1002, 1003], name="Segment ID"),
    )
    scr.attrs = {"windows": {"am": {"window": "7:00AM-9:00AM", "peak": True}},
                 "units": {"travel_time": "Minutes", "speed": "miles/hour"}}

    ranked = screen.rank_corridors(scr, res.attrs["chains"])
    row = ranked.iloc[0]
    assert row["corridor"] == "Toy Rd NB" and row["window"] == "am"
    assert row["n_segments"] == 4 and row["n_observed"] == 4
    # 3.0 in-extent miles (the two end segments are trimmed to half), 1 min delay/mile
    assert row["miles"] == pytest.approx(3.0)
    assert row["delay_min"] == pytest.approx(3.0)
    assert row["tti"] == pytest.approx(2.0)


# ---------------------------------------------------------------------------
# Topology repair  (Item 38)
# ---------------------------------------------------------------------------
_DEG = 1e-4        # ~11.1 m of latitude here — the repair radius is 25 m


def _repair_network(segs):
    """A toy network for the repair rules.

    Each entry is ``(id, (lat0, lon0), (lat1, lon1), next_id, group, bearing)``
    with optional ``lanes``/``name``; the repair rule reads ``XDGroup`` and
    ``Bearing``, so a fixture without them is not exercising it.
    """
    rows = []
    for s in segs:
        sid, p0, p1, nxt, group, bearing = s[:6]
        rows.append({
            "XDSegID": sid,
            "NextXDSegI": nxt,
            "XDGroup": group,
            "Bearing": bearing,
            "RoadName": s[6] if len(s) > 6 else "Main St",
            "RoadNumber": "99",
            "Lanes": s[7] if len(s) > 7 else 2.0,
            "Miles": 1.0,
            "geometry": LineString([(p0[1], p0[0]), (p1[1], p1[0])]),
        })
    df = pd.DataFrame(rows)
    return gpd.GeoDataFrame(
        {**{c: df[c] for c in df.columns if c != "geometry"}},
        geometry=list(df["geometry"]),
        crs=geometry.WGS84,
    ).astype({"XDSegID": "Int64", "NextXDSegI": "Int64", "XDGroup": "Int64"})


def _north(lat0, n=1):
    return (LAT0 + lat0 * _DEG, LON), (LAT0 + (lat0 + n) * _DEG, LON)


def test_repair_fills_a_null_link_to_the_one_same_group_continuation():
    a0, a1 = _north(0, 50)
    b0, b1 = _north(50, 50)
    net = _repair_network([
        (1, a0, a1, pd.NA, 7, "N"),
        (2, b0, b1, pd.NA, 7, "N"),
    ])
    patch = corridors.repair_links(net)
    assert list(patch["segment"]) == [1]
    row = patch.iloc[0]
    assert row["new_next"] == 2 and row["kind"] == corridors.FILL
    assert pd.isna(row["old_next"])
    assert row["gap_m"] == pytest.approx(0.0, abs=0.5)
    assert patch.attrs["n_fill"] == 1 and patch.attrs["n_override"] == 0


def test_repair_leaves_an_ambiguous_break_alone():
    """Two qualifying continuations is an ambiguity, and an ambiguity stays a break
    — the whole point of refusing to guess."""
    a0, a1 = _north(0, 50)
    b0, b1 = _north(50, 50)
    net = _repair_network([
        (1, a0, a1, pd.NA, 7, "N"),
        (2, b0, b1, pd.NA, 7, "N"),
        (3, b0, (b1[0], b1[1] + _DEG), pd.NA, 7, "N"),   # a second same-group fork
    ])
    patch = corridors.repair_links(net)
    assert patch.empty
    assert patch.attrs["ambiguous_fill"] == 1


def test_repair_overrides_a_link_that_leaves_its_own_carriageway():
    """The I-184 case: the mainline's ``NextXDSegI`` names an off-ramp, and the
    mainline continuation is sitting right there in the same ``XDGroup``."""
    a0, a1 = _north(0, 50)
    main0, main1 = _north(50, 50)
    net = _repair_network([
        (1, a0, a1, 9, 7, "N"),                                   # points at the ramp
        (2, main0, main1, pd.NA, 7, "N"),                         # the real continuation
        (9, main0, (main1[0], main1[1] + 3 * _DEG), pd.NA, 88, "N", "Off Ramp", 1.0),
    ])
    patch = corridors.repair_links(net)
    over = patch[patch["kind"] == corridors.OVERRIDE]
    assert len(over) == 1
    assert over.iloc[0]["segment"] == 1 and over.iloc[0]["new_next"] == 2
    assert over.iloc[0]["old_next"] == 9
    # ...and the conservative setting never contradicts the network.
    fills_only = corridors.repair_links(net, kinds=(corridors.FILL,))
    assert corridors.OVERRIDE not in set(fills_only["kind"])


def test_repair_prefers_the_farther_in_group_segment_over_a_nearer_ramp():
    """The measured Flying Y trap: the on-ramp is 8.1 m from the mainline's end and
    the true continuation is 4.7 m, so *distance* alone picks the ramp. Here the
    ramp is deliberately the **nearer** of the two."""
    a0, a1 = _north(0, 50)
    net = _repair_network([
        (1, a0, a1, pd.NA, 7, "N"),
        (2, (a1[0] + 1.5 * _DEG, a1[1]), (a1[0] + 50 * _DEG, a1[1]), pd.NA, 7, "N"),
        (9, (a1[0] + 0.2 * _DEG, a1[1]), (a1[0] + 20 * _DEG, a1[1] + _DEG), pd.NA,
         88, "N", "Off Ramp", 1.0),
    ])
    patch = corridors.repair_links(net)
    assert list(patch["new_next"]) == [2]           # not the nearer out-of-group 9
    assert patch.iloc[0]["gap_m"] > 10.0            # and it knowingly reached farther


def test_repair_refuses_to_change_the_direction_of_travel():
    """The Eagle Rd rotary: a same-``XDGroup`` continuation whose ``Bearing`` is not
    the segment's own. Repaired through, the corridor walks south, round the rotary
    and back north over the same ground."""
    a0, a1 = _north(0, 50)
    net = _repair_network([
        (1, a0, a1, pd.NA, 7, "N"),
        (2, a1, (a1[0] + 30 * _DEG, a1[1] + 30 * _DEG), pd.NA, 7, "O", "Rotary", 2.0),
    ])
    assert corridors.repair_links(net).empty
    # The guard is a guard, not a law of nature: it can be turned off deliberately.
    loose = corridors.repair_links(net, require_same_bearing=False)
    assert list(loose["new_next"]) == [2]


def test_repair_rejects_an_anti_parallel_continuation():
    """Two segments of one group whose ends coincide and which run *at* each other
    (a cul-de-sac pair) would otherwise repair into a 2-cycle."""
    a0, a1 = _north(0, 50)
    net = _repair_network([
        (1, a0, a1, pd.NA, 7, "N"),
        (2, a1, a0, pd.NA, 7, "N"),          # straight back down the same line
    ])
    assert corridors.repair_links(net).empty


def test_repair_does_not_touch_a_link_that_leaves_the_subset():
    """A pointer to a segment this extract does not carry is the edge of the
    extract, not a defect; ``walk_chain`` already calls that ``off_network``."""
    a0, a1 = _north(0, 50)
    b0, b1 = _north(50, 50)
    net = _repair_network([
        (1, a0, a1, 4242, 7, "N"),           # 4242 is not in this subset
        (2, b0, b1, pd.NA, 7, "N"),
    ])
    patch = corridors.repair_links(net)
    assert patch.empty
    assert patch.attrs["skipped_off_subset"] == 1


def test_repair_links_rejects_an_unknown_kind():
    with pytest.raises(ValueError, match="Unknown repair kind"):
        corridors.repair_links(_repair_network([(1, *_north(0, 50), pd.NA, 7, "N")]),
                               kinds=("bridge",))


def test_apply_link_repairs_is_idempotent_and_subset_safe():
    net = _chain_network(3, next_ids=[1001, pd.NA, pd.NA])
    patch = pd.DataFrame({"segment": [1001, 999999], "new_next": [1002, 5],
                          "old_next": pd.array([pd.NA, pd.NA], dtype="Int64"),
                          "kind": [corridors.FILL, corridors.FILL]})
    once = corridors.apply_link_repairs(net, patch)
    assert corridors.walk_chain(once, 1000, 1002)[1] == "target"
    twice = corridors.apply_link_repairs(once, patch)
    assert list(twice["NextXDSegI"]) == list(once["NextXDSegI"])
    assert corridors.apply_link_repairs(net, None) is net


def test_build_chain_names_the_repairs_it_used():
    """A chain that rests on a repair says so — that is the price of repairing."""
    net = _chain_network(4, next_ids=[1001, pd.NA, 1003, pd.NA])
    broken = corridors.build_chain(net, _at(0.5, 0), _at(0.5, 3))
    assert not broken.reached_target and broken.stop_reason == "dead_end"
    assert broken.n_repaired_links == 0

    patch = pd.DataFrame({"segment": [1001], "new_next": [1002],
                          "old_next": pd.array([pd.NA], dtype="Int64"),
                          "kind": [corridors.FILL]})
    fixed = corridors.build_chain(net, _at(0.5, 0), _at(0.5, 3), repairs=patch)
    assert fixed.reached_target
    assert fixed.repaired_links == ((1001, 1002),)
    assert fixed.n_repaired_links == 1 and fixed.summary()["n_repaired_links"] == 1
    # A repair the chain never traverses is not credited to it.
    unused = pd.concat([patch, pd.DataFrame({"segment": [4242], "new_next": [4243],
                                             "old_next": pd.array([pd.NA], dtype="Int64"),
                                             "kind": [corridors.FILL]})])
    assert corridors.build_chain(net, _at(0.5, 0), _at(0.5, 3),
                                 repairs=unused).repaired_links == ((1001, 1002),)


def test_resolve_catalogue_carries_the_repair_accounting():
    net = _chain_network(4, next_ids=[1001, pd.NA, 1003, pd.NA])
    patch = pd.DataFrame({"segment": [1001], "new_next": [1002],
                          "old_next": pd.array([pd.NA], dtype="Int64"),
                          "kind": [corridors.FILL]})
    entry = _entry(id="Toy Rd NB")
    plain = corridors.resolve_catalogue(net, [entry])
    assert not plain.loc[0, "reached_target"]
    assert plain.attrs["repairs_applied"] is False and plain.attrs["n_repairs"] == 0

    repaired = corridors.resolve_catalogue(net, [entry], repairs=patch)
    assert repaired.loc[0, "reached_target"]
    assert repaired.loc[0, "n_repaired_links"] == 1
    assert "n_repaired_links" in corridors.CATALOGUE_COLUMNS
    assert repaired.attrs["repairs_applied"] is True and repaired.attrs["n_repairs"] == 1


def test_load_link_repairs_round_trips_with_its_rule_in_the_header(tmp_path):
    """The committed table has to say what rule produced it, or a run cannot state
    what it walked on."""
    net = _repair_network([
        (1, *_north(0, 50), pd.NA, 7, "N"),
        (2, *_north(50, 50), pd.NA, 7, "N"),
    ])
    patch = corridors.repair_links(net)
    path = tmp_path / "repairs.csv"
    with path.open("w") as fh:
        fh.write(f"# radius_m: {patch.attrs['radius_m']}\n")
        fh.write(f"# n_override: {patch.attrs['n_override']}\n")
        patch.to_csv(fh, index=False)
    back = corridors.load_link_repairs(path)
    assert list(back["segment"]) == [1] and list(back["new_next"]) == [2]
    assert back["old_next"].isna().all()
    assert back.attrs["radius_m"] == "25.0" and back.attrs["n_override"] == "0"


# --- the real network -------------------------------------------------------
D3_NETWORK = REPO_ROOT / "geometry_cache" / "d3_network.geoparquet"
D3_CATALOGUE = REPO_ROOT / "scripts" / "d3_corridors.json"
D3_REPAIRS = REPO_ROOT / "scripts" / "d3_link_repairs.csv"


@pytest.mark.skipif(not (D3_NETWORK.exists() and D3_REPAIRS.exists()),
                    reason="D3 network cache / repair table not available")
def test_committed_repair_table_resolves_the_whole_d3_catalogue():
    """20 of 20, and — the part that matters — the corridors that already resolved
    without repairs are not disturbed by them."""
    net = gpd.read_parquet(D3_NETWORK)
    repairs = corridors.load_link_repairs(D3_REPAIRS)
    res = corridors.resolve_catalogue(net, corridors.load_catalogue(D3_CATALOGUE),
                                      repairs=repairs).set_index("id")
    assert res["reached_target"].all(), sorted(res.index[~res["reached_target"]])
    # I-84 EB is the corridor Item 36 got right; it must still be exactly that.
    assert res.loc["i84-eb", "n_segments"] == 27
    assert res.loc["i84-eb", "chain_miles"] == pytest.approx(15.1229, abs=1e-3)
    assert res.loc["i84-eb", "n_repaired_links"] == 0
    # I-184 is the corridor Item 36 could not walk at all.
    assert res.loc["i184-eb", "n_segments"] == 10
    assert res.loc["i184-eb", "chain_miles"] == pytest.approx(4.7174, abs=1e-3)
    assert res.loc["i184-eb", "n_repaired_links"] == 5
    # The catalogue leans on the table far less than the table's size suggests.
    assert res["n_repaired_links"].sum() == 19


@pytest.mark.skipif(not D3_NETWORK.exists(), reason="D3 network cache not available")
def test_committed_repair_table_reproduces_the_rule_it_documents():
    """The table is *derived*, not typed: regenerating it from the network under the
    header's own parameters must give back the same rows."""
    net = gpd.read_parquet(D3_NETWORK)
    fresh = corridors.repair_links(net)
    committed = corridors.load_link_repairs(D3_REPAIRS)
    assert len(fresh) == len(committed)
    assert list(fresh["segment"]) == list(committed["segment"])
    assert list(fresh["new_next"]) == list(committed["new_next"])
    assert fresh.attrs["n_override"] == int(committed.attrs["n_override"])
    assert fresh.attrs["ambiguous_override"] == 0


# ---------------------------------------------------------------------------
# Reporting corridors: the catalogue schema  (Item 40)
# ---------------------------------------------------------------------------
def _grouped_catalogue(**over):
    base = {
        "corridors": [
            {**_ENTRY, "id": "toy-nb", "corridor": "toy", "direction": "NB"},
            {**_ENTRY, "id": "toy-sb", "corridor": "toy", "direction": "SB"},
        ],
        "reporting_corridors": [
            {"id": "toy", "name": "Toy Rd", "description": "Both directions of it."},
        ],
    }
    base.update(over)
    return base


_ENTRY = {"name": "Toy Rd", "start_latlon": [43.60, -116.35],
          "end_latlon": [43.64, -116.35], "description": "why this extent"}


def test_entries_carry_their_reporting_corridor_and_direction():
    entries = corridors.parse_catalogue(_grouped_catalogue())
    assert [e.corridor for e in entries] == ["toy", "toy"]
    assert [e.direction for e in entries] == ["NB", "SB"]
    groups = corridors.parse_reporting_corridors(_grouped_catalogue())
    assert [g.id for g in groups] == ["toy"] and groups[0].name == "Toy Rd"


def test_an_ungrouped_catalogue_is_still_valid():
    """Grouping is additive: a catalogue that declares none ranks per direction
    exactly as it did before Item 40."""
    entries = corridors.parse_catalogue({"corridors": [{**_ENTRY, "id": "toy-nb"}]})
    assert entries[0].corridor is None and entries[0].direction is None
    assert corridors.parse_reporting_corridors({"corridors": [{**_ENTRY, "id": "a"}]}) == ()


def test_corridor_and_direction_must_travel_together():
    data = _grouped_catalogue()
    del data["corridors"][0]["direction"]
    with pytest.raises(ValueError, match="without the other"):
        corridors.parse_catalogue(data)


def test_two_entries_cannot_be_the_same_direction_of_one_corridor():
    """A copy-paste that would double-count one carriageway into the grouped total
    and drop the other entirely."""
    data = _grouped_catalogue()
    data["corridors"][1]["direction"] = "NB"
    with pytest.raises(ValueError, match="both NB of reporting corridor"):
        corridors.parse_catalogue(data)


def test_a_group_an_entry_names_must_be_declared():
    data = _grouped_catalogue()
    data["corridors"][1]["corridor"] = "nowhere"
    with pytest.raises(ValueError, match="does not declare"):
        corridors.parse_reporting_corridors(data)


def test_a_declared_group_with_no_entries_is_a_corridor_missing_from_the_report():
    data = _grouped_catalogue()
    data["reporting_corridors"].append(
        {"id": "ghost", "name": "Ghost Rd", "description": "nobody belongs to it"})
    with pytest.raises(ValueError, match="declared but no entry belongs"):
        corridors.parse_reporting_corridors(data)


def test_reporting_corridor_schema_is_validated_like_an_entry():
    for bad, match in [
        ({"id": "", "name": "n", "description": "d"}, "has no 'id'"),
        ({"id": "toy", "name": "", "description": "d"}, "empty 'name'"),
        ({"id": "toy", "name": "n", "description": ""}, "empty 'description'"),
        ({"id": "toy", "name": "n", "description": "d", "colour": "red"}, "unknown field"),
        ({"id": "toy", "name": "n"}, "is missing"),
    ]:
        with pytest.raises(ValueError, match=match):
            corridors.parse_reporting_corridors({"reporting_corridors": [bad]})
    with pytest.raises(ValueError, match="Duplicate reporting corridor id"):
        corridors.parse_reporting_corridors({"reporting_corridors": [
            {"id": "toy", "name": "n", "description": "d"},
            {"id": "toy", "name": "m", "description": "e"}]})


def test_resolution_table_carries_the_grouping():
    net = _chain_network(4)
    entries = corridors.parse_catalogue({"corridors": [
        {**_ENTRY, "id": "toy-nb", "corridor": "toy", "direction": "NB",
         "start_latlon": list(_at(0.5, 0)), "end_latlon": list(_at(0.5, 3))}]})
    res = corridors.resolve_catalogue(net, entries)
    assert res.loc[0, "corridor"] == "toy" and res.loc[0, "direction"] == "NB"
    assert "corridor" in corridors.CATALOGUE_COLUMNS


@pytest.mark.skipif(not D3_CATALOGUE.exists(), reason="D3 catalogue not available")
def test_the_d3_catalogue_groups_its_entries_into_reporting_corridors():
    entries = corridors.load_catalogue(D3_CATALOGUE)
    groups = corridors.load_reporting_corridors(D3_CATALOGUE)
    assert len(entries) == 36 and len(groups) == 18
    assert all(e.corridor and e.direction for e in entries)
    # Every reporting corridor is exactly two directions, and they differ.
    by_group: dict[str, list[str]] = {}
    for e in entries:
        by_group.setdefault(e.corridor, []).append(e.direction)
    assert all(len(v) == 2 and len(set(v)) == 2 for v in by_group.values()), by_group
    # Couplets are explicitly tagged
    couplets = {g.id for g in groups if g.one_way_couplet}
    assert couplets == {"boise-couplet", "nampa-couplet"}


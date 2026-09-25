"""Tests for the volume-profile curve assignment (ROADMAP Item 56)."""
from __future__ import annotations

import pandas as pd
import pytest

from inrix_tools import profile_assignment as pa
from inrix_tools import screen, volume_profiles

# A commute-sized urban area centred at (43.60, -116.20), and a radial road west of it.
CEN_LAT, CEN_LON = 43.60, -116.20
UACE = "08785"
DLON = 0.02           # ~1.6 km per segment at this latitude


def _seg(sid, lon0, lon1, lat=CEN_LAT, bearing=None):
    b = bearing or ("E" if lon1 > lon0 else "W")
    return {"XDSegID": sid, "Bearing": b, "Miles": 1.0,
            "StartLat": lat, "StartLong": lon0, "EndLat": lat, "EndLong": lon1}


def _network():
    """A radial road west of the centroid: WB 101..104 run outbound (away, west), EB
    201..204 run inbound (east, toward the centroid). Plus 301, a far-off rural
    segment, and 401 a segment with no urban context at all."""
    lons = [CEN_LON - 0.03 - DLON * i for i in range(5)]          # 5 points, going west
    rows = [_seg(101 + i, lons[i], lons[i + 1]) for i in range(4)]            # WB
    rows += [_seg(201 + i, lons[4 - i], lons[3 - i]) for i in range(4)]       # EB
    rows += [_seg(301, -114.0, -113.98), _seg(401, -115.0, -114.98)]
    return pd.DataFrame(rows)


def _membership(route="44", itd="01540ASH044"):
    ids = [101, 102, 103, 104, 201, 202, 203, 204, 301]
    return pd.DataFrame({"route_number": [route] * 8 + ["75"],
                         "itd_route_id": [itd] * 8 + ["01540ASH075"],
                         "verdict": ["agree"] * 9}, index=pd.Index(ids, name="XDSegID"))


def _urban():
    ids = [101, 102, 103, 104, 201, 202, 203, 204, 301]
    return pd.DataFrame({"urban_uace": [UACE] * 9,
                         "urban_area": ["Boise City, ID"] * 9,
                         "urban_inside": [True] * 8 + [False],
                         "urban_share": [1.0] * 8 + [0.0],
                         "urban_edge_m": [2000.0] * 8 + [-150000.0]},
                        index=pd.Index(ids, name="XDSegID"))


def _centroids(pop=400_000):
    return pd.DataFrame({"urban_area": ["Boise City, ID"], "centroid_lat": [CEN_LAT],
                         "centroid_lon": [CEN_LON], "population": [float(pop)]},
                        index=pd.Index([UACE], name="UACE"))


def _context(**kw):
    return pa.segment_context(_network(), kw.get("membership", _membership()),
                              kw.get("urban", _urban()), kw.get("centroids", _centroids()))


def _delay(am_wb, pm_wb, am_eb, pm_eb, extra=None):
    """am/pm delay (minutes) per segment: WB 101..104, EB 201..204."""
    rows = {s: (am_wb, pm_wb) for s in (101, 102, 103, 104)}
    rows.update({s: (am_eb, pm_eb) for s in (201, 202, 203, 204)})
    rows.update(extra or {})
    return pd.DataFrame.from_dict(rows, orient="index", columns=["am", "pm"])


# ---------------------------------------------------------------------------
# Context and the urban rule
# ---------------------------------------------------------------------------
def test_context_reads_radial_zone_and_route():
    ctx = _context()
    assert set(ctx.loc[[101, 102, 103, 104], "radial"]) == {pa.OUTBOUND}
    assert set(ctx.loc[[201, 202, 203, 204], "radial"]) == {pa.INBOUND}
    assert ctx.at[301, "zone"] == pa.RURAL and ctx.at[301, "radial"] is None
    assert ctx.at[401, "zone"] is None
    assert ctx.at[101, "dir_sign"] == -1 and ctx.at[201, "dir_sign"] == 1
    assert ctx.at[101, "route"] == "44" and not ctx.at[101, "interstate"]


def test_urban_rule_classes():
    rule = pa.urban_rule(_context())
    assert set(rule.loc[[201, 202, 203, 204], "curve_id"]) == {pa.AM_CURVE}
    assert set(rule.loc[[101, 102, 103, 104], "curve_id"]) == {pa.PM_CURVE}
    assert rule.at[301, "curve_id"] == pa.RURAL_CURVE
    assert rule.at[401, "curve_id"] is None


def test_interstate_gets_the_through_curve_under_the_rule():
    rule = pa.urban_rule(_context(membership=_membership("84", "01540AIN084")))
    assert set(rule.loc[[101, 201], "curve_id"]) == {pa.INTERSTATE_CURVE}
    assert pa.is_interstate("84") and pa.is_interstate("184", None)
    assert not pa.is_interstate("95", "01540AUS095")


def test_a_business_loop_is_not_an_interstate():
    mem = _membership("84", "02042AIN084")
    mem["verdict"] = "business"
    ctx = _context(membership=mem)
    assert not ctx["interstate"].any()
    assert pa.urban_rule(ctx).at[201, "curve_id"] == pa.AM_CURVE


def test_small_town_is_balanced_inside_and_rural_around():
    ctx = _context(centroids=_centroids(pop=8_000))
    rule = pa.urban_rule(ctx)
    assert set(rule.loc[[101, 201], "curve_id"]) == {pa.BALANCED_CURVE}
    urb = _urban()
    urb["urban_inside"] = False
    urb["urban_edge_m"] = -1000.0
    rule = pa.urban_rule(_context(urban=urb, centroids=_centroids(pop=8_000)))
    assert set(rule.loc[[101, 201], "curve_id"]) == {pa.RURAL_CURVE}


def test_approach_zone_keeps_the_commute_rule():
    urb = _urban()
    urb["urban_inside"] = False
    urb["urban_edge_m"] = -(pa.URBAN_APPROACH_M - 100)
    ctx = _context(urban=urb)
    assert set(ctx.loc[[201, 101], "zone"]) == {pa.APPROACH}
    assert pa.urban_rule(ctx).at[201, "curve_id"] == pa.AM_CURVE


def test_tangential_inside_is_balanced():
    net = pd.DataFrame([_seg(501, CEN_LON - 0.03, CEN_LON - 0.03, lat=CEN_LAT),
                        ])
    net.loc[0, "EndLat"] = CEN_LAT + 0.02          # northbound, across the radial
    net.loc[0, "Bearing"] = "N"
    urb = _urban().iloc[:1].set_axis(pd.Index([501], name="XDSegID"))
    ctx = pa.segment_context(net, None, urb, _centroids())
    assert ctx.at[501, "radial"] == pa.TANGENTIAL
    assert pa.urban_rule(ctx).at[501, "curve_id"] == pa.BALANCED_CURVE


# ---------------------------------------------------------------------------
# Chains
# ---------------------------------------------------------------------------
def test_route_runs_pair_opposing_carriageways():
    runs = {c.chain_id: c for c in pa.route_runs(_context())}
    wb = next(c for c in runs.values() if 101 in c.segment_ids)
    eb = next(c for c in runs.values() if 201 in c.segment_ids)
    assert set(wb.segment_ids) == {101, 102, 103, 104}
    assert wb.opposite == (eb.chain_id,) and eb.opposite == (wb.chain_id,)


class _Entry:
    def __init__(self, id, corridor, direction):
        self.id, self.corridor, self.direction = id, corridor, direction


def test_catalogue_chains_pair_within_the_reporting_corridor():
    entries = [_Entry("r-eb", "r", "EB"), _Entry("r-wb", "r", "WB"),
               _Entry("s-nb", "s", "NB")]
    chains = {c.chain_id: c for c in pa.catalogue_chains(
        entries, {"r-eb": [201, 202], "r-wb": [101, 102], "s-nb": [301]})}
    assert chains["r-eb"].opposite == ("r-wb",)
    assert chains["r-wb"].opposite == ("r-eb",)
    assert chains["s-nb"].opposite == ()
    assert chains["r-eb"].kind == "catalogue" and chains["r-eb"].direction == "EB"


# ---------------------------------------------------------------------------
# Inference
# ---------------------------------------------------------------------------
def test_synthetic_opposing_pair_is_inferred():
    # Flip the orientation against the urban rule: WB (rule: outbound -> PM) is the
    # AM-heavy side, so the inference must decide, not the rule.
    ctx = _context()
    out = pa.assign_profiles(ctx, pa.route_runs(ctx), _delay(0.8, 0.1, 0.1, 0.8))
    wb, eb = out.loc[[101, 102, 103, 104]], out.loc[[201, 202, 203, 204]]
    assert set(wb["curve_source"]) == {pa.INFERRED}
    assert set(wb["curve_id"]) == {pa.AM_CURVE}
    assert set(eb["curve_id"]) == {pa.PM_CURVE}
    assert wb["am_share_self"].iloc[0] == pytest.approx(0.8 / 0.9, abs=1e-3)
    assert wb["am_share_opposite"].iloc[0] == pytest.approx(0.1 / 0.9, abs=1e-3)


def test_both_peaks_bottleneck_falls_to_the_urban_rule():
    ctx = _context()
    out = pa.assign_profiles(ctx, pa.route_runs(ctx), _delay(0.5, 0.5, 0.5, 0.5))
    assert set(out.loc[[101, 201], "curve_source"]) == {pa.URBAN_RULE}
    assert out.at[201, "curve_id"] == pa.AM_CURVE          # inbound
    assert out.at[101, "curve_id"] == pa.PM_CURVE          # outbound
    assert "both peaks" in out.at[201, "reason"]


def test_one_direction_only_congested_is_not_evidence():
    ctx = _context()
    out = pa.assign_profiles(ctx, pa.route_runs(ctx), _delay(0.9, 0.0, 0.0, 0.0))
    assert set(out.loc[[101, 201], "curve_source"]) == {pa.URBAN_RULE}
    assert "delay floor" in out.at[101, "reason"]


def test_chain_inheritance_does_not_flip_flop():
    # Segment 103 alone reads the other way; the chain's length-weighted share still
    # decides, and every segment of it inherits.
    d = _delay(0.8, 0.1, 0.1, 0.8, extra={103: (0.1, 0.8), 203: (0.8, 0.1)})
    ctx = _context()
    out = pa.assign_profiles(ctx, pa.route_runs(ctx), d)
    assert set(out.loc[[101, 102, 103, 104], "curve_id"]) == {pa.AM_CURVE}
    assert set(out.loc[[201, 202, 203, 204], "curve_id"]) == {pa.PM_CURVE}
    assert out.loc[[101, 102, 103, 104], "chain_id"].nunique() == 1


def test_catalogue_chain_decides_before_the_route_run():
    ctx = _context()
    entries = [_Entry("r-eb", "r", "EB"), _Entry("r-wb", "r", "WB")]
    cat = pa.catalogue_chains(entries, {"r-eb": [201, 202], "r-wb": [101, 102]})
    # Catalogue extent: WB AM-heavy. The whole run (all four each way) is both-peaks.
    d = _delay(0.5, 0.5, 0.5, 0.5, extra={101: (0.9, 0.1), 102: (0.9, 0.1),
                                          103: (0.1, 0.9), 104: (0.1, 0.9),
                                          201: (0.1, 0.9), 202: (0.1, 0.9),
                                          203: (0.9, 0.1), 204: (0.9, 0.1)})
    out = pa.assign_profiles(ctx, cat + pa.route_runs(ctx), d)
    assert out.at[101, "curve_source"] == pa.INFERRED and out.at[101, "chain_id"] == "r-wb"
    assert out.at[101, "curve_id"] == pa.AM_CURVE
    assert out.at[103, "curve_source"] == pa.URBAN_RULE


def test_no_delay_skips_the_inference():
    ctx = _context()
    out = pa.assign_profiles(ctx, pa.route_runs(ctx), None)
    assert pa.INFERRED not in set(out["curve_source"])
    assert out.attrs["profile_assignment"]["n_chains_inferred"] == 0


# ---------------------------------------------------------------------------
# Overrides and the default
# ---------------------------------------------------------------------------
def _write_overrides(tmp_path, body):
    p = tmp_path / "ov.csv"
    p.write_text("# comment\nxd_seg_id,district,corridor,direction,curve_id,note\n" + body)
    return p


def test_override_wins(tmp_path):
    ctx = _context()
    entries = [_Entry("r-eb", "r", "EB"), _Entry("r-wb", "r", "WB")]
    cat = pa.catalogue_chains(entries, {"r-eb": [201, 202], "r-wb": [101, 102]})
    ov = pa.load_overrides(_write_overrides(
        tmp_path,
        "102,,,,rural_recreational,owner: lake traffic\n"
        ",3,r,WB,balanced_urban,owner: flat\n"
        ",4,r,EB,rural_through,other district\n"),
        volume_profiles.load_profiles())
    out = pa.assign_profiles(ctx, cat + pa.route_runs(ctx),
                             _delay(0.8, 0.1, 0.1, 0.8), overrides=ov, district=3)
    assert out.at[102, "curve_id"] == "rural_recreational"      # segment beats corridor
    assert out.at[101, "curve_id"] == pa.BALANCED_CURVE          # beats the inference
    assert set(out.loc[[101, 102], "curve_source"]) == {pa.OVERRIDE}
    assert out.at[201, "curve_source"] == pa.INFERRED            # district 4 row ignored


def test_override_table_is_validated(tmp_path):
    lib = volume_profiles.load_profiles()
    with pytest.raises(ValueError, match="unknown curve_id"):
        pa.load_overrides(_write_overrides(tmp_path, "1,,,,no_such,x\n"), lib)
    with pytest.raises(ValueError, match="not both or neither"):
        pa.load_overrides(_write_overrides(tmp_path, "1,,r,EB,balanced_urban,x\n"), lib)
    with pytest.raises(ValueError, match="needs both"):
        pa.load_overrides(_write_overrides(tmp_path, ",,r,,balanced_urban,x\n"), lib)
    with pytest.raises(ValueError, match="note"):
        pa.load_overrides(_write_overrides(tmp_path, "1,,,,balanced_urban,\n"), lib)


def test_the_shipped_override_table_loads():
    from pathlib import Path
    path = Path(__file__).resolve().parents[1] / "scripts" / "volume_profile_overrides.csv"
    assert list(pa.load_overrides(path, volume_profiles.load_profiles()).columns) == \
        list(pa.OVERRIDE_COLUMNS)


def test_unmatched_segment_gets_the_default():
    ctx = _context()
    out = pa.assign_profiles(ctx, pa.route_runs(ctx), _delay(0.5, 0.5, 0.5, 0.5),
                             profiles=volume_profiles.load_profiles())
    assert out.at[401, "curve_source"] == pa.DEFAULT
    assert out.at[401, "curve_id"] == pa.DEFAULT_CURVE
    assert out.at[301, "curve_source"] == pa.URBAN_RULE
    counts = out.attrs["profile_assignment"]["by_source"]
    assert sum(counts.values()) == len(ctx)
    assert set(out["curve_id"]) <= set(volume_profiles.load_profiles())


def test_assignment_roundtrips_through_csv(tmp_path):
    ctx = _context()
    out = pa.assign_profiles(ctx, pa.route_runs(ctx), _delay(0.8, 0.1, 0.1, 0.8))
    back = pa.read_assignment(pa.write_assignment(out, tmp_path / "d3_volume_profiles.csv"))
    assert list(back.columns) == list(pa.ASSIGNMENT_COLUMNS)
    assert back.index.dtype == "int64"
    pd.testing.assert_series_equal(back["curve_id"], out["curve_id"])
    assert back.at[401, "chain_id"] is None


# ---------------------------------------------------------------------------
# The helpers it reads through
# ---------------------------------------------------------------------------
def test_window_delay_matches_the_ranking_definition():
    scr = pd.DataFrame({"ref_speed": [60.0, 60.0],
                        "am_travel_time": [2.0, 0.5], "am_speed": [30.0, 120.0],
                        "pm_travel_time": [1.5, 1.0], "pm_speed": [40.0, 60.0]},
                       index=pd.Index([1, 2], name="Segment ID"))
    d = screen.window_delay(scr, pd.Series({1: 1.0, 2: 1.0}))
    assert list(d.columns) == ["am", "pm"]
    assert d.at[1, "am"] == pytest.approx(1.0) and d.at[1, "pm"] == pytest.approx(0.5)
    assert d.at[2, "am"] == 0.0                                   # floored
    with pytest.raises(ValueError, match="window"):
        screen.window_delay(scr, pd.Series({1: 1.0}), windows=("midday",))


def test_urban_centroids():
    gpd = pytest.importorskip("geopandas")
    from shapely.geometry import box

    from inrix_tools import itd_layers
    urban = gpd.GeoDataFrame({"UACE": ["00001"], "NAME": ["Box, ID"],
                              "Population": [60000]},
                             geometry=[box(-116.3, 43.5, -116.1, 43.7)], crs="EPSG:4326")
    cen = itd_layers.urban_centroids(urban)
    assert cen.at["00001", "centroid_lat"] == pytest.approx(43.6, abs=1e-3)
    assert cen.at["00001", "centroid_lon"] == pytest.approx(-116.2, abs=1e-3)
    assert cen.at["00001", "population"] == 60000

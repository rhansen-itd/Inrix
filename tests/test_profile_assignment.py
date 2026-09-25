"""Tests for the volume-profile curve assignment (ROADMAP Item 56)."""
from __future__ import annotations

import pandas as pd
import pytest

from inrix_tools import profile_assignment as pa
from inrix_tools import screen, volume_profiles
from inrix_tools.route_sections import RouteSection

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


# ---------------------------------------------------------------------------
# Urban centres
# ---------------------------------------------------------------------------
def _write_centres(tmp_path, body):
    p = tmp_path / "centres.csv"
    p.write_text("# comment\nuace,urban_area,centre_lat,centre_lon,note\n" + body)
    return p


def test_a_centre_row_moves_the_radial():
    # Move the centre far west of the road: EB (was inbound) now heads away from it.
    ctx = _context()
    assert pa.urban_rule(ctx).at[201, "curve_id"] == pa.AM_CURVE
    centres = pd.DataFrame({"urban_area": ["Boise City, ID"], "centre_lat": [CEN_LAT],
                            "centre_lon": [CEN_LON - 0.5], "note": ["x"]},
                           index=pd.Index([UACE], name="UACE"))
    moved = pa.apply_urban_centres(_centroids(), centres)
    assert moved.at[UACE, "centre_source"] == "table"
    assert moved.at[UACE, "centroid_lon"] == pytest.approx(CEN_LON - 0.5)
    rule = pa.urban_rule(_context(centroids=moved))
    assert rule.at[201, "curve_id"] == pa.PM_CURVE
    assert rule.at[101, "curve_id"] == pa.AM_CURVE


def test_no_centre_rows_leave_the_centroid():
    out = pa.apply_urban_centres(_centroids(), None)
    assert out.at[UACE, "centre_source"] == "centroid"
    assert out.at[UACE, "centroid_lon"] == CEN_LON


def test_urban_centre_table_is_validated(tmp_path):
    t = pa.load_urban_centres(_write_centres(tmp_path, "8785,Boise,43.6,-116.2,downtown\n"))
    assert list(t.index) == ["08785"]                      # zero-padded
    with pytest.raises(ValueError, match="range"):
        pa.load_urban_centres(_write_centres(tmp_path, "08785,B,-116.2,43.6,x\n"))
    with pytest.raises(ValueError, match="twice"):
        pa.load_urban_centres(_write_centres(tmp_path, "08785,B,43.6,-116.2,x\n"
                                                       "08785,B,43.6,-116.2,y\n"))
    with pytest.raises(ValueError, match="note"):
        pa.load_urban_centres(_write_centres(tmp_path, "08785,B,43.6,-116.2,\n"))


def test_the_shipped_urban_centre_table_loads():
    from pathlib import Path
    path = Path(__file__).resolve().parents[1] / "scripts" / "urban_centres.csv"
    t = pa.load_urban_centres(path)
    assert "08785" in t.index                              # Boise: owner, 2026-09-25
    assert t.at["08785", "centre_lon"] == pytest.approx(-116.2023)


# ---------------------------------------------------------------------------
# Station rule (Item 59)
# ---------------------------------------------------------------------------
def _linked_network(gap=None, extra=()):
    """:func:`_network` with its XD links: WB 101→102→103→104, EB 201→202→203→204.
    ``gap`` = a (from, to) link to cut."""
    net = _network()
    nxt = {101: 102, 102: 103, 103: 104, 201: 202, 202: 203, 203: 204}
    if gap:
        nxt.pop(gap[0])
    prv = {v: k for k, v in nxt.items()}
    net = pd.concat([net, pd.DataFrame(list(extra))], ignore_index=True)
    net["NextXDSegI"] = pd.array([nxt.get(s) for s in net["XDSegID"]], dtype="Int64")
    net["PreviousXD"] = pd.array([prv.get(s) for s in net["XDSegID"]], dtype="Int64")
    return net


def _station_context(membership=None, **kw):
    return pa.segment_context(_linked_network(**kw), membership if membership is not None
                              else _membership(), _urban(), _centroids())


def _mid(sid):
    """(lat, lon) of the middle of segment ``sid`` of :func:`_network`."""
    r = _network().set_index("XDSegID").loc[sid]
    return (r.StartLat + r.EndLat) / 2, (r.StartLong + r.EndLong) / 2


def _stations(*rows):
    """Station table rows: (station_id, direction, (lat, lon), route[, curve_id])."""
    out = []
    for sid, direction, (lat, lon), route, *curve in rows:
        out.append({"station_id": sid, "direction": direction, "lat": lat, "lon": lon,
                    "route": route, "source": "atr",
                    "curve_id": curve[0] if curve else f"fitted_{sid}_{direction}",
                    "nearest_generic": "rural_through", "misplaced_share": 0.08,
                    "usable_days": 30, "borrowed": ""})
    return pd.DataFrame(out)


def _sections(*runs, route="44"):
    """``route_sections.RouteSection``s over the given segment runs (in travel order)."""
    out = []
    for ids in runs:
        direction = "EB" if ids[0] >= 200 else "WB"
        out.append(RouteSection(f"{route}-{direction}-{ids[0]}", route, direction,
                                tuple(ids), float(len(ids)),
                                {"kind": "route_end", "detail": "start"},
                                {"kind": "junction", "detail": "US-20 (State) meets"}))
    return out


EB, WB = (201, 202, 203, 204), (101, 102, 103, 104)


def test_station_covers_its_whole_section():
    """Snapped to the middle of EB 202, the station covers the EB section end to end
    (204 is 1.5 mi on — the Item 59 walk stopped at a mile); WB is the other way."""
    r = pa.station_rule(_station_context(), _stations(("00279", "EB", _mid(202), "44")),
                        sections=_sections(EB, WB))
    got = r["curve_id"].dropna()
    assert sorted(got.index) == [201, 202, 203, 204]
    assert set(got) == {"fitted_00279_EB"}
    assert r.at[202, "station_miles"] == 0.0
    assert r.at[203, "station_miles"] == pytest.approx(0.5, abs=0.01)
    assert r.at[204, "station_miles"] == pytest.approx(1.5, abs=0.01)
    assert "ATR 00279 EB on route 44" in r.at[201, "reason"]
    assert "section 44-EB-201" in r.at[201, "reason"]
    rep = r.attrs["stations"].iloc[0]
    assert rep["snapped_to"] == 202 and rep["n_segments"] == 4 and rep["n_assigned"] == 4
    assert rep["section_id"] == "44-EB-201" and rep["section_miles"] == 4.0
    assert rep["miles_assigned"] == 4.0
    assert rep["section_to"] == "junction: US-20 (State) meets"


def test_without_sections_a_station_covers_its_own_segment():
    r = pa.station_rule(_station_context(), _stations(("00279", "EB", _mid(202), "44")))
    assert list(r["curve_id"].dropna().index) == [202]
    assert "no traced route section" in r.attrs["stations"].iloc[0]["note"]


def test_station_direction_picks_the_carriageway():
    r = pa.station_rule(_station_context(), _stations(("00279", "WB", _mid(102), "44")),
                        sections=_sections(EB, WB))
    assert sorted(r["curve_id"].dropna().index) == [101, 102, 103, 104]


def test_diagonal_direction_label_matches_by_bearing():
    """ITD labels diagonal roads NW/SE; the EB carriageway (bearing 90°) is within
    60° of SE (135°), the WB one (270°) of NW (315°)."""
    ctx = _station_context()
    r = pa.station_rule(ctx, _stations(("00002", "SE", _mid(202), "44"),
                                       ("00002", "NW", _mid(102), "44")),
                        sections=_sections(EB, WB))
    assert set(r.loc[list(EB), "station_direction"]) == {"SE"}
    assert set(r.loc[list(WB), "station_direction"]) == {"NW"}


def test_a_section_break_ends_the_reach_and_a_stationless_section_falls_through():
    """A junction between 202 and 203 makes two sections; the station on the first
    covers nothing of the second."""
    r = pa.station_rule(_station_context(), _stations(("00279", "EB", _mid(202), "44")),
                        sections=_sections((201, 202), (203, 204), WB))
    assert sorted(r["curve_id"].dropna().index) == [201, 202]


def test_a_parallel_street_of_the_same_route_is_not_covered():
    """The Broadway / Front–Myrtle case: an EB segment of the same route a block away
    is on another section."""
    lat, lon = _mid(202)
    block = _seg(501, lon - 0.005, lon + 0.005, lat=lat + 0.002)      # ~220 m north
    mem = pd.concat([_membership(), pd.DataFrame(
        {"route_number": ["44"], "itd_route_id": ["01540ASH044"], "verdict": ["agree"]},
        index=pd.Index([501], name="XDSegID"))])
    ctx = pa.segment_context(_linked_network(extra=[block]), mem, _urban(), _centroids())
    r = pa.station_rule(ctx, _stations(("00279", "EB", (lat, lon), "44")),
                        sections=_sections(EB, WB, (501,)))
    assert pd.isna(r.at[501, "curve_id"]) or r.at[501, "curve_id"] is None
    assert r.at[202, "curve_id"] == "fitted_00279_EB"


@pytest.mark.parametrize("route, direction, where, note", [
    ("55", "EB", 202, "no segment on route 55"),
    ("", "EB", 202, "no route"),
    ("44", "2WAY", 202, "two-way"),
    ("44", "NB", 202, "in its direction within"),
])
def test_station_that_covers_nothing_says_why(route, direction, where, note):
    r = pa.station_rule(_station_context(), _stations(("x", direction, _mid(where), route)),
                        sections=_sections(EB, WB))
    assert r["curve_id"].isna().all()
    assert note in r.attrs["stations"].iloc[0]["note"]


def test_station_too_far_from_any_segment_does_not_snap():
    lat, lon = _mid(202)
    r = pa.station_rule(_station_context(), _stations(("x", "EB", (lat + 0.01, lon), "44")),
                        sections=_sections(EB, WB))
    assert r["curve_id"].isna().all()                      # ~0.7 mi off the road


def test_interstate_and_business_loop_stations_keep_to_their_own_road():
    mem = _membership("84", "01540AIN084")
    mem.loc[list(EB), "verdict"] = "business"
    ctx = _station_context(membership=mem)
    secs = _sections(EB, route="84 BL") + _sections(WB, route="84")
    main = pa.station_rule(ctx, _stations(("i", "EB", _mid(202), "84"),
                                          ("i", "WB", _mid(102), "84")), sections=secs)
    assert sorted(main["curve_id"].dropna().index) == list(WB)            # not the BL
    bl = pa.station_rule(ctx, _stations(("b", "EB", _mid(202), "84 BL"),
                                        ("b", "WB", _mid(102), "84 BL")), sections=secs)
    assert sorted(bl["curve_id"].dropna().index) == list(EB)              # not the I-84


def test_a_station_takes_the_section_of_its_own_route():
    """On a concurrency (US-20 on SH-44) the anchor lies on a section of each route;
    a US-20 station covers US-20's."""
    mem = _membership()
    mem["routes"] = "20/44"
    secs = _sections((201, 202), route="44") + _sections(EB, route="20")
    r = pa.station_rule(_station_context(membership=mem),
                        _stations(("c", "EB", _mid(202), "20")), sections=secs)
    assert sorted(r["curve_id"].dropna().index) == list(EB)
    assert r.attrs["stations"].iloc[0]["section_id"] == "20-EB-201"


def test_overlapping_stations_nearest_along_the_path_wins():
    r = pa.station_rule(_station_context(), _stations(("a", "EB", _mid(201), "44"),
                                                      ("b", "EB", _mid(204), "44")),
                        sections=_sections(EB, WB))
    assert list(r.loc[list(EB), "station_id"]) == ["a", "a", "b", "b"]
    rep = r.attrs["stations"].set_index("station_id")
    assert rep["n_assigned"].sum() == r["curve_id"].notna().sum() == 4
    assert set(rep["section_id"]) == {"44-EB-201"}


def test_station_rule_can_assign_the_nearest_generic():
    r = pa.station_rule(_station_context(), _stations(("00279", "EB", _mid(202), "44")),
                        sections=_sections(EB, WB), use_generic=True)
    assert r.at[202, "curve_id"] == "rural_through"
    assert "nearest generic to fitted_00279_EB" in r.at[202, "reason"]


def test_station_outranks_inference_and_rule_but_not_override(tmp_path):
    ctx = _station_context()
    chains = pa.route_runs(ctx)
    delay = _delay(0.2, 1.0, 1.0, 0.2)            # EB AM-heavy, WB PM-heavy: infers
    lib = volume_profiles.load_profiles()
    fitted = dict(lib)
    fitted["fitted_00279_EB"] = volume_profiles.VolumeProfile(
        "fitted_00279_EB", lib["rural_through"].hourly, lib["rural_through"].dow,
        {"basis": "fitted"})
    ov = tmp_path / "ov.csv"
    ov.write_text("xd_seg_id,district,corridor,direction,curve_id,note\n"
                  "203,,,,balanced_urban,owner says\n")
    out = pa.assign_profiles(ctx, chains, delay, overrides=pa.load_overrides(ov),
                             profiles=fitted,
                             stations=_stations(("00279", "EB", _mid(202), "44")),
                             sections=_sections((201, 202, 203), (204,), WB))
    assert out.at[202, "curve_source"] == pa.STATION
    assert out.at[202, "curve_id"] == "fitted_00279_EB"
    assert out.at[203, "curve_source"] == pa.OVERRIDE
    assert out.at[204, "curve_source"] == pa.INFERRED      # a stationless section
    assert out.attrs["profile_assignment"]["by_source"][pa.STATION] == 2
    assert out.attrs["profile_assignment"]["n_stations"] == 1
    assert pa.STATION in pa.summary_line(out)
    # Without the fitted curve in the library the assignment is refused.
    with pytest.raises(ValueError, match="not in the library"):
        pa.assign_profiles(ctx, chains, delay, profiles=lib,
                           stations=_stations(("00279", "EB", _mid(202), "44")))


def test_assignment_writes_its_fitted_curves_beside_it(tmp_path):
    lib = volume_profiles.load_profiles()
    fitted = volume_profiles.VolumeProfile(
        "fitted_x_EB", lib["rural_through"].hourly, lib["rural_through"].dow,
        {"basis": "fitted"})
    full = volume_profiles.merge_profiles(lib, {"fitted_x_EB": fitted})
    a = pd.DataFrame({c: [None, None] for c in pa.ASSIGNMENT_COLUMNS},
                     index=pd.Index([1, 2], name="XDSegID"))
    a["curve_id"] = ["fitted_x_EB", "rural_through"]
    path = pa.write_assignment(a, tmp_path / "d3_volume_profiles.csv", profiles=full)
    companion = pa.curves_path(path)
    assert companion.name == "d3_volume_profiles_curves.json"
    assert set(volume_profiles.load_profiles(companion)) == {"fitted_x_EB"}
    assert "fitted_x_EB" in pa.assignment_profiles(path)
    # A later run with generic curves only removes the stale companion.
    a["curve_id"] = ["rural_through", "rural_through"]
    pa.write_assignment(a, path, profiles=full)
    assert not companion.exists()
    assert set(pa.assignment_profiles(path)) == set(lib)

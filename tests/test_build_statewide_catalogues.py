"""Tests for the catalogue builder's curve-VHD inputs (ROADMAP Item 58).

The builder is wiring; what is pinned here is that the core floors get the inputs the
curve path needs — the cached bin screen **with** its attrs (the period, zone and bin
width the volume weights are built from), and the screening run's curves, whose
absence is an error that says what to run rather than a silent fall back to the index.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd
import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

pytest.importorskip("geopandas")
import build_statewide_catalogues as bsc  # noqa: E402

from inrix_tools import profile_assignment as pa  # noqa: E402


def test_the_cached_bin_screen_keeps_its_attrs(tmp_path):
    d = tmp_path / "d3"
    d.mkdir()
    bins = pd.DataFrame({"Segment ID": [1], "month": ["2026-03"], "day_type": ["weekday"],
                         "tod_min": [420], "travel_time": [2.0], "n_obs": [5]})
    attrs = {"tz": "America/Boise", "bin_minutes": 15, "period_start": "2026-01-01",
             "period_end": "2026-08-31",
             "windows": {n: w.to_dict() for n, w in bsc.COMMUTE.window_map().items()}}
    bins.to_parquet(d / bsc.BINS_FILE)
    (d / bsc.BINS_FILE).with_suffix(".json").write_text(json.dumps(attrs))
    got = bsc.load_bins(3, tmp_path)
    assert got.attrs == attrs
    pd.testing.assert_frame_equal(got, bins, check_like=True)


def test_curves_come_from_the_screening_run(tmp_path):
    with pytest.raises(SystemExit, match="run the district screening"):
        bsc.load_curves(3, tmp_path)
    d = tmp_path / "d3"
    d.mkdir()
    saved = pd.DataFrame({c: [None] for c in pa.ASSIGNMENT_COLUMNS},
                         index=pd.Index([1234], name="XDSegID"))
    saved["curve_id"], saved["curve_source"] = "rural_through", pa.DEFAULT
    pa.write_assignment(saved, d / bsc.PROFILES_FILE.format(district=3))
    assert bsc.load_curves(3, tmp_path).loc[1234, "curve_id"] == "rural_through"


# ─── Item 63: District 3 is generated like the rest; couplets aren't ranked twice ──

def test_district_3_is_built_and_named_like_every_other():
    assert bsc.DEFAULT_DISTRICTS == [1, 2, 3, 4, 5, 6]
    assert [bsc.catalogue_name(d) for d in (1, 3)] == ["d1_corridors.json",
                                                       "d3_corridors.json"]


MILES = {s: 0.25 for s in range(1, 20)}
LEGS = {"couplet-a": ((1, 2), (3, 4)), "couplet-b": ((5, 6), (7, 8))}


def _cat_with_couplet(core_segments, tier2_segments=()):
    """A facility whose core runs on ``core_segments``, and whose Tier 2 on
    ``tier2_segments``; two detected couplets beside it."""
    return {"corridors": [
        {"id": "core-nb", "corridor": "us-95-moscow-core", "_segment_ids": list(core_segments)},
        {"id": "t2-nb", "corridor": "us-95-moscow-commuter",
         "_segment_ids": list(tier2_segments)},
    ], "reporting_corridors": [
        {"id": "us-95-moscow-core", "_tier_number": 1, "_ranked": True,
         "_facility": "us-95-moscow", "_couplets": ["couplet-a", "couplet-b"]},
        {"id": "us-95-moscow-commuter", "_tier_number": 2, "_ranked": False,
         "_facility": "us-95-moscow", "_couplets": ["couplet-a", "couplet-b"]},
        {"id": "couplet-a", "_couplet": True, "one_way_couplet": True},
        {"id": "couplet-b", "_couplet": True, "one_way_couplet": True},
    ]}


def test_a_couplet_a_ranked_core_covers_is_counted_in_it():
    """Moscow: the core is both legs."""
    cat = _cat_with_couplet([1, 2, 3, 4], tier2_segments=[5, 6, 7, 8])
    assert bsc.unrank_couplets(cat, LEGS, MILES) == {"couplet-a": "us-95-moscow",
                                                     "couplet-b": None}
    a, b = cat["reporting_corridors"][2:]
    assert a["_ranked"] is False and a["_counted_in"] == "us-95-moscow"
    assert a["one_way_couplet"]          # still the AADT one-way fallback's legs
    # only a *ranked* core counts: a Tier 2 running over a couplet does not
    assert b["_ranked"] is False and "_counted_in" not in b and "_flags" not in b


def test_a_couplet_a_core_runs_on_one_leg_of_is_not_ranked_either():
    """Twin Falls: a westbound-only core on the westbound leg. The couplet is a
    stitching aid (owner, 2026-10-07): it goes to context under that core, unflagged."""
    cat = _cat_with_couplet([1, 2])
    assert bsc.unrank_couplets(cat, LEGS, MILES)["couplet-a"] == "us-95-moscow"
    a = cat["reporting_corridors"][2]
    assert a["_ranked"] is False and "_flags" not in a


def test_a_couplet_no_core_runs_on_is_not_ranked():
    cat = _cat_with_couplet([9, 10])
    assert bsc.unrank_couplets(cat, LEGS, MILES) == {"couplet-a": None, "couplet-b": None}
    assert all(g["_ranked"] is False and "_counted_in" not in g
               for g in cat["reporting_corridors"][2:])


# ─── Item 66: one cache per corridor type; the commute caches keep their names ──

def test_type_files_keep_the_commute_names():
    rec = bsc.extents.CORRIDOR_TYPES["recreational"]
    assert bsc.type_file(bsc.BASELINE_FILE, bsc.COMMUTE) == bsc.BASELINE_FILE
    assert bsc.type_file(bsc.BASELINE_FILE, rec) == \
        "segment_baseline_screen_recreational.parquet"
    assert bsc.type_file(bsc.BINS_FILE, rec) == "segment_peak_bins_recreational.parquet"


def _no_store(monkeypatch):
    """Fail loudly instead of opening (and creating) a store in the working directory."""
    monkeypatch.setattr(bsc.store, "connect",
                        lambda *_: (_ for _ in ()).throw(RuntimeError("store read")))


def test_a_baseline_cache_is_read_only_if_it_carries_the_types_windows(tmp_path,
                                                                        monkeypatch):
    _no_store(monkeypatch)
    d = tmp_path / "d3"
    d.mkdir()
    cols = {f"{w}_travel_time": [1.0] for w in bsc.COMMUTE.baseline_windows()}
    cached = pd.DataFrame(cols, index=pd.Index([7], name="Segment ID"))
    cached.to_parquet(d / bsc.BASELINE_FILE)
    got = bsc.load_baseline(3, tmp_path)
    pd.testing.assert_frame_equal(got, cached)
    # The same columns under the recreational name lack its windows: the loader goes
    # to the store (absent here) rather than coring on the wrong windows.
    rec = bsc.extents.CORRIDOR_TYPES["recreational"]
    cached.to_parquet(d / bsc.type_file(bsc.BASELINE_FILE, rec))
    with pytest.raises(RuntimeError, match="store read"):
        bsc.load_baseline(3, tmp_path, ctype=rec)


def test_a_bin_cache_over_other_windows_is_not_used(tmp_path, monkeypatch):
    d = tmp_path / "d3"
    d.mkdir()
    rec = bsc.extents.CORRIDOR_TYPES["recreational"]
    bins = pd.DataFrame({"Segment ID": [1], "month": ["2026-07"], "day_type": ["sat"],
                         "tod_min": [600], "travel_time": [2.0], "n_obs": [5]})
    path = d / bsc.type_file(bsc.BINS_FILE, rec)
    bins.to_parquet(path)
    good = {"windows": {n: w.to_dict() for n, w in rec.window_map().items()}}
    path.with_suffix(".json").write_text(json.dumps(good))
    assert bsc.load_bins(3, tmp_path, ctype=rec).attrs == good
    stale = {"windows": {"sat": bsc.screen.ALL_WINDOWS["sat"].to_dict()}}
    path.with_suffix(".json").write_text(json.dumps(stale))
    _no_store(monkeypatch)
    with pytest.raises(RuntimeError, match="store read"):
        bsc.load_bins(3, tmp_path, ctype=rec)


def test_main_builds_one_typed_catalogue(tmp_path, monkeypatch):
    """The builder's own loop, on a toy network with the store and GIS loaders
    stubbed: two types, one merged catalogue on disk, an audit with a type column."""
    from test_extents import _baseline, _linear_chain

    net = _linear_chain(16)
    ids = list(net.index)
    weekend = {1011: 1.6, 1012: 1.6, 1013: 1.6, 1002: 1.3, 1003: 1.3}

    def _base(district, sdir, *, refresh=False, ctype=bsc.COMMUTE, **_):
        b = _baseline(ids, {1001: 2.0, 1002: 2.0, 1003: 2.0})
        for w in ctype.window_names:
            if f"{w}_travel_time" not in b.columns:
                b[f"{w}_travel_time"] = [weekend.get(s, 1.0) for s in ids]
                b[f"{w}_n_obs"] = 500
                b[f"{w}_realtime_share"] = 0.99
        return b

    monkeypatch.setattr(bsc, "load_district", lambda d, **kw: (net, None))
    monkeypatch.setattr(bsc, "join_urban_context", lambda n, d: n)
    monkeypatch.setattr(bsc, "load_baseline", _base)
    monkeypatch.setattr(bsc, "load_monthly", lambda *a, **k: None)
    monkeypatch.setattr(bsc, "verify", lambda cat, *a: pd.DataFrame(
        {"reached_target": [True] * len(cat["corridors"])}))
    monkeypatch.setattr(sys, "argv", [
        "bsc", "--districts", "3", "--out-dir", str(tmp_path), "--screening-dir",
        str(tmp_path), "--aadt", "", "--hard-stops", "", "--no-couplets",
        "--types", "commute,recreational"])
    assert bsc.main() == 0
    cat = json.loads((tmp_path / "d3_corridors.json").read_text())
    cores = [g for g in cat["reporting_corridors"] if g["_tier"] == "core"]
    assert sorted(tuple(g["_types"]) for g in cores) == [("commute", "recreational"),
                                                         ("recreational",)]
    assert {g["_class"] for g in cores} == {"commute", "recreational"}
    assert cat["_generated"]["type_order"] == ["commute", "recreational"]
    audit = pd.read_csv(tmp_path / "d3" / "core_audit.csv")
    assert set(audit["type"]) == {"commute", "recreational"}

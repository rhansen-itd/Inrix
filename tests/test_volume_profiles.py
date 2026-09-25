"""Tests for inrix_tools.volume_profiles (ROADMAP Item 55)."""
import json

import numpy as np
import pandas as pd
import pytest

from inrix_tools import volume_profiles as vp

TZ = "America/Denver"
STARTER = {"am_commute_urban", "pm_commute_urban", "balanced_urban", "rural_through",
           "rural_recreational", "interstate_through"}


def _flat(curve_id="flat", dow=None):
    return vp.VolumeProfile(curve_id=curve_id,
                            hourly={d: [1 / 24] * 24 for d in vp.DAY_TYPES},
                            dow=dow or [1.0] * 7)


def _day(date, freq="5min"):
    start = pd.Timestamp(date).tz_localize(TZ)
    end = (pd.Timestamp(date) + pd.Timedelta(1, "D")).tz_localize(TZ)
    return pd.date_range(start, end, freq=freq, inclusive="left")


# ---------------------------------------------------------------------------
# The packaged library
# ---------------------------------------------------------------------------
def test_the_starter_curves_load_with_cited_provenance():
    profiles = vp.load_profiles()
    assert set(profiles) == STARTER
    sources = vp.profile_sources()
    for p in profiles.values():
        assert p.provenance["basis"] in vp.BASES
        assert p.provenance["sources"] and set(p.provenance["sources"]) <= set(sources)
        assert p.provenance["detail"] and p.description
    for key in {s for p in profiles.values() for s in p.provenance["sources"]}:
        assert sources[key]["citation"]


def test_the_starter_curves_have_the_shapes_their_names_promise():
    P = vp.load_profiles()
    am = P["am_commute_urban"].hourly["weekday"]
    pm = P["pm_commute_urban"].hourly["weekday"]
    assert sum(am[6:9]) > sum(am[15:18]) * 0.9 and int(np.argmax(am)) == 7
    assert int(np.argmax(pm)) in (15, 16, 17) and sum(pm[15:18]) > 1.5 * sum(pm[6:9])
    # Recreational: Friday–Sunday heavier than midweek.
    rec = P["rural_recreational"].dow
    assert min(rec[4:7]) > max(rec[1:4])
    # Interstate: broader than rural_through — more of the day overnight.
    night = slice(0, 5)
    assert (sum(P["interstate_through"].hourly["weekday"][night])
            > sum(P["rural_through"].hourly["weekday"][night]))


def test_peak_hour_shares_sit_below_the_layers_k30():
    """DHV is the 30th-highest hour (K30); the 2025 layer's median DHV/AADT is 0.12.
    An average day's peak hour, even on its busiest day of the week, must sit below
    that — it is an upper bound, not a target."""
    for p in vp.load_profiles().values():
        worst = max(max(p.hourly[d]) for d in vp.DAY_TYPES) * max(p.dow)
        assert 0.05 < worst < 0.12, p.curve_id


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("mutate, message", [
    (lambda h, d: h["weekday"].__setitem__(0, h["weekday"][0] + 0.01), "sums to"),
    (lambda h, d: h["sat"].pop(), "has 23 values"),
    (lambda h, d: h["sun"].__setitem__(3, -0.001), "negative"),
    (lambda h, d: h["sun"].__setitem__(3, float("nan")), "non-finite"),
    (lambda h, d: h.pop("sun"), "needs exactly"),
    (lambda h, d: d.__setitem__(6, 1.2), "mean"),
    (lambda h, d: d.pop(), "has 6 values"),
])
def test_validation_rejects_a_bad_curve(mutate, message):
    hourly = {d: [1 / 24] * 24 for d in vp.DAY_TYPES}
    dow = [1.0] * 7
    mutate(hourly, dow)
    with pytest.raises(ValueError, match=message):
        vp.VolumeProfile(curve_id="bad", hourly=hourly, dow=dow)


def test_validation_rejects_an_unknown_basis_and_a_blank_id():
    with pytest.raises(ValueError, match="basis"):
        vp.VolumeProfile(curve_id="x", hourly={d: [1 / 24] * 24 for d in vp.DAY_TYPES},
                         dow=[1] * 7, provenance={"basis": "guessed"})
    with pytest.raises(ValueError, match="curve_id"):
        _flat(curve_id=" ")


def test_load_profiles_from_a_path_and_rejects_duplicates(tmp_path):
    rec = {"curve_id": "flat", "hourly": {d: [1 / 24] * 24 for d in vp.DAY_TYPES},
           "dow": [1] * 7, "provenance": {"basis": "synthesised", "sources": []}}
    path = tmp_path / "lib.json"
    path.write_text(json.dumps({"profiles": [rec]}))
    assert list(vp.load_profiles(path)) == ["flat"]
    with pytest.raises(ValueError, match="duplicate"):
        vp.load_profiles({"profiles": [rec, rec]})


# ---------------------------------------------------------------------------
# bin_volume_factor
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("curve_id", sorted(STARTER))
def test_a_days_bin_factors_sum_to_its_dow_factor(curve_id):
    p = vp.load_profiles()[curve_id]
    for date in pd.date_range("2026-06-01", "2026-06-07"):       # Mon..Sun
        f = vp.bin_volume_factor(p, _day(date))
        assert len(f) == 288
        assert f.sum() == pytest.approx(p.dow[date.dayofweek], abs=1e-12)
    # A week sums to 7: the curve moves volume between days, it doesn't add any.
    week = pd.date_range(pd.Timestamp("2026-06-01").tz_localize(TZ), periods=7 * 288,
                         freq="5min")
    assert vp.bin_volume_factor(p, week).sum() == pytest.approx(7.0)


def test_a_hand_computed_bin():
    p = vp.load_profiles()["am_commute_urban"]
    ts = pd.DatetimeIndex([pd.Timestamp("2026-06-05 07:35", tz=TZ)])     # a Friday
    expected = p.dow[4] * p.hourly["weekday"][7] / 12
    assert vp.bin_volume_factor(p, ts).iloc[0] == pytest.approx(expected)
    sat = pd.DatetimeIndex([pd.Timestamp("2026-06-06 07:35", tz=TZ)])
    assert vp.bin_volume_factor(p, sat).iloc[0] == pytest.approx(
        p.dow[5] * p.hourly["sat"][7] / 12)


def test_spring_forward_day_has_23_hours_and_keeps_its_volume():
    p = vp.load_profiles()["pm_commute_urban"]
    day = _day("2026-03-08")                      # Sunday; 02:00 → 03:00 in Denver
    assert len(day) == 23 * 12 and 2 not in set(day.hour)
    f = vp.bin_volume_factor(p, day)
    assert f.sum() == pytest.approx(p.dow[6], abs=1e-12)
    # Each surviving hour is scaled up by the same 1 / (1 − skipped hour's share).
    sun = p.hourly["sun"]
    ts = pd.DatetimeIndex([pd.Timestamp("2026-03-08 16:00", tz=TZ)])
    assert vp.bin_volume_factor(p, ts).iloc[0] == pytest.approx(
        p.dow[6] * sun[16] / (1 - sun[2]) / 12)


def test_fall_back_day_has_25_hours_and_keeps_its_volume():
    p = vp.load_profiles()["rural_through"]
    day = _day("2026-11-01")                      # Sunday; 01:00 happens twice
    assert len(day) == 25 * 12
    f = vp.bin_volume_factor(p, day)
    assert f.sum() == pytest.approx(p.dow[6], abs=1e-12)
    first, second = f[day.hour == 1].iloc[:12], f[day.hour == 1].iloc[12:]
    assert np.allclose(first.to_numpy(), second.to_numpy())   # both 01:00 hours count


def test_the_factor_depends_only_on_each_timestamp():
    p = vp.load_profiles()["balanced_urban"]
    day = _day("2026-03-08")
    whole = vp.bin_volume_factor(p, day)
    window = day[(day.hour >= 7) & (day.hour < 9)]
    assert np.allclose(vp.bin_volume_factor(p, window).to_numpy(), whole[window].to_numpy())


def test_series_input_keeps_its_index_and_hours_are_local():
    p = _flat(dow=[1.4, 1, 1, 1, 1, 0.8, 0.8])
    utc = pd.Series(pd.to_datetime(["2026-06-01 06:00Z", "2026-06-01 05:00Z"]),
                    index=["a", "b"])
    # 06:00Z is Monday 00:00 in Denver; 05:00Z is still Sunday 23:00.
    f = vp.bin_volume_factor(p, utc.dt.tz_convert(TZ))
    assert list(f.index) == ["a", "b"]
    assert f["a"] == pytest.approx(1.4 / 24 / 12)
    assert f["b"] == pytest.approx(0.8 / 24 / 12)


def test_other_bin_widths_and_bad_inputs():
    p = vp.load_profiles()["rural_recreational"]
    q = vp.bin_volume_factor(p, _day("2026-07-04", freq="15min"), bin_minutes=15)
    assert q.sum() == pytest.approx(p.dow[5])
    with pytest.raises(ValueError, match="divide 60"):
        vp.bin_volume_factor(p, _day("2026-07-04"), bin_minutes=7)
    with pytest.raises(ValueError, match="tz-aware"):
        vp.bin_volume_factor(p, pd.date_range("2026-07-04", periods=3, freq="5min"))


def test_a_flat_curve_gives_a_flat_day():
    f = vp.bin_volume_factor(_flat(), _day("2026-06-03"))
    assert np.allclose(f.to_numpy(), 1 / 288)


def test_the_packaged_library_is_what_the_derivation_builds():
    """``volume_profiles.json`` is generated, not hand-edited: rebuilding it from the
    committed source extract must reproduce it exactly."""
    import importlib.util
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    spec = importlib.util.spec_from_file_location(
        "derive_volume_profiles", root / "scripts" / "derive_volume_profiles.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    built = mod.build_profiles(json.loads(mod.SOURCES_JSON.read_text()))
    packaged = json.loads((root / "src" / "inrix_tools" / vp.PACKAGE_DATA).read_text())
    assert json.loads(json.dumps(built)) == packaged

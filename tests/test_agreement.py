"""Tests for inrix_tools.agreement (ROADMAP Item 29)."""
import numpy as np
import pandas as pd
import pytest

from inrix_tools import agreement
from inrix_tools.reference import EXTRA_TT_COL, ROUTE_COL, TT_COL

TZ = "America/Denver"


def _frame(values, start="2026-03-03 08:00", route="R", freq="15min", col=TT_COL):
    stamps = pd.date_range(start, periods=len(values), freq=freq, tz=TZ)
    return pd.DataFrame({ROUTE_COL: route, "Date Time": stamps, col: values})


# ---------------------------------------------------------------------------
# Matching
# ---------------------------------------------------------------------------
def test_match_bins_carries_the_inrix_coverage_columns():
    """ROADMAP Item 31: the CValue gate's cost and the imputation share have to
    survive the join, or the scorecard reports a bias with no idea what was
    summed to make it."""
    inrix = _frame([5.0, 6.0]).assign(imputed_fraction=[0.0, 0.5],
                                      cvalue_kept_fraction=[1.0, 0.5],
                                      short=[False, True], n_absent=[0, 1])
    m = agreement.match_bins(inrix, _frame([10.0, 12.0]))
    assert m["imputed_fraction"].tolist() == pytest.approx([0.0, 0.5])
    assert m["short"].tolist() == pytest.approx([0.0, 1.0])   # bool -> share of bin


def test_match_bins_without_coverage_columns_still_matches():
    m = agreement.match_bins(_frame([5.0, 6.0]), _frame([10.0, 12.0]))
    assert len(m) == 2
    assert not set(agreement.CARRY_COLS) & set(m.columns)


def test_compare_reports_coverage_beside_the_effect_size():
    inrix = _frame([5.0, 6.0, 7.0]).assign(imputed_fraction=[0.0, 0.3, 0.6],
                                           cvalue_kept_fraction=[1.0, 0.7, 0.4],
                                           short=[True, True, True], n_absent=[3, 3, 3])
    inrix.attrs["cvalue_threshold"] = 80
    summary = agreement.compare(inrix, _frame([10.0, 12.0, 14.0]))
    row = summary.iloc[0]
    assert row["imputed_fraction"] == pytest.approx(0.3)
    assert row["cvalue_kept_fraction"] == pytest.approx(0.7)
    assert row["short_fraction"] == pytest.approx(1.0)
    assert row["n_absent"] == 3
    assert summary.attrs["cvalue_threshold"] == 80


def test_compare_omits_coverage_columns_when_there_are_none():
    """Absent, not NaN: a column of NaNs reads as 'measured, nothing wrong'."""
    summary = agreement.compare(_frame([5.0, 6.0, 7.0]), _frame([10.0, 12.0, 14.0]))
    assert "imputed_fraction" not in summary.columns
    assert summary.attrs["cvalue_threshold"] is None



def test_match_bins_joins_and_averages_duplicate_reference_samples():
    inrix = _frame([5.0, 6.0, 7.0])
    reference = pd.concat([_frame([10.0, 12.0, 14.0]),
                           _frame([12.0], start="2026-03-03 08:00")], ignore_index=True)

    m = agreement.match_bins(inrix, reference)
    assert len(m) == 3
    first = m.iloc[0]
    assert first[agreement.REF_COL] == pytest.approx(11.0)   # (10 + 12) / 2
    assert first["n_ref_samples"] == 2
    assert first[agreement.DIFF_COL] == pytest.approx(5.0 - 11.0)
    assert first[agreement.MEAN_COL] == pytest.approx(8.0)


def test_match_bins_is_an_inner_join_and_drops_nan_values():
    inrix = _frame([5.0, np.nan, 7.0, 8.0])
    reference = _frame([10.0, 12.0, 14.0])          # no 4th bin
    m = agreement.match_bins(inrix, reference)
    assert len(m) == 2                               # bin 2 dropped (NaN), bin 4 unmatched
    assert m["Date Time"].dt.strftime("%H:%M").tolist() == ["08:00", "08:30"]


def test_match_bins_keeps_routes_separate():
    inrix = pd.concat([_frame([5.0, 6.0], route="A"), _frame([9.0, 9.0], route="B")],
                      ignore_index=True)
    reference = pd.concat([_frame([4.0, 4.0], route="A"), _frame([3.0, 3.0], route="B")],
                          ignore_index=True)
    m = agreement.match_bins(inrix, reference)
    assert m.groupby(ROUTE_COL)[agreement.DIFF_COL].mean().round(2).to_dict() == {"A": 1.5, "B": 6.0}


# ---------------------------------------------------------------------------
# Statistics, hand-computed
# ---------------------------------------------------------------------------
def test_compare_statistics_against_hand_computed_values():
    inrix = _frame([4.0, 6.0, 8.0, 10.0])
    reference = _frame([5.0, 9.0, 9.0, 13.0])
    # differences: -1, -3, -1, -3  -> bias -2, MAE 2, RMSE sqrt(5)
    got = agreement.compare(inrix, reference).iloc[0]

    assert got["n_bins"] == 4
    assert got["mean_inrix"] == pytest.approx(7.0) and got["mean_ref"] == pytest.approx(9.0)
    assert got["bias"] == pytest.approx(-2.0)
    assert got["mae"] == pytest.approx(2.0)
    assert got["rmse"] == pytest.approx(5.0 ** 0.5)
    # MAPE over |diff| / reference
    assert got["mape"] == pytest.approx(np.mean([1/5, 3/9, 1/9, 3/13]) * 100)
    assert got["sd_ratio"] == pytest.approx(np.std([4, 6, 8, 10], ddof=1) / np.std([5, 9, 9, 13], ddof=1))
    # Bland-Altman limits: mean diff +- 1.96 SD(diff)
    sd_diff = np.std([-1, -3, -1, -3], ddof=1)
    assert got["loa_low"] == pytest.approx(-2.0 - 1.96 * sd_diff)
    assert got["loa_high"] == pytest.approx(-2.0 + 1.96 * sd_diff)
    assert got["r"] == pytest.approx(np.corrcoef([4, 6, 8, 10], [5, 9, 9, 13])[0, 1])


def test_delay_ratio_uses_each_sources_own_free_flow():
    """Neither source is judged against the other's baseline: free-flow is that
    source's own percentile, which is what exposed the arterial delay compression
    (G1) rather than a units or baseline artifact."""
    inrix = _frame([10.0, 10.0, 12.0, 14.0])
    reference = _frame([20.0, 20.0, 24.0, 28.0])
    got = agreement.compare(inrix, reference, free_flow_percentile=0.0).iloc[0]

    assert got["free_flow_inrix"] == pytest.approx(10.0)
    assert got["free_flow_ref"] == pytest.approx(20.0)
    assert got["delay_inrix"] == pytest.approx(np.mean([0, 0, 2, 4]))
    assert got["delay_ref"] == pytest.approx(np.mean([0, 0, 4, 8]))
    assert got["delay_ratio"] == pytest.approx(0.5)      # INRIX shows half the delay


def test_compare_reports_per_route_rows():
    inrix = pd.concat([_frame([5.0, 6.0], route="A"), _frame([9.0, 9.0], route="B")],
                      ignore_index=True)
    reference = pd.concat([_frame([4.0, 4.0], route="A"), _frame([3.0, 3.0], route="B")],
                          ignore_index=True)
    got = agreement.compare(inrix, reference).set_index(ROUTE_COL)
    assert got.loc["A", "bias"] == pytest.approx(1.5)
    assert got.loc["B", "bias"] == pytest.approx(6.0)
    assert len(got.attrs["matched"]) == 4


def test_degenerate_samples_report_undefined_not_certain():
    """One bin, or a zero-variance difference, must not come back as a width-0
    interval — that reads as absolute certainty."""
    single = agreement.compare(_frame([5.0]), _frame([4.0])).iloc[0]
    assert single["n_bins"] == 1
    assert np.isnan(single["bias_ci_low"]) and np.isnan(single["bias_ci_high"])
    assert np.isnan(single["r"])

    constant = agreement.compare(_frame([5.0] * 8), _frame([4.0] * 8)).iloc[0]
    assert constant["bias"] == pytest.approx(1.0)
    assert np.isnan(constant["bias_ci_low_naive"])


# ---------------------------------------------------------------------------
# The CI is day-blocked because bins are autocorrelated
# ---------------------------------------------------------------------------
def _series_with_ar1(rho, n_days=30, per_day=48, sd=1.0, seed=0):
    """Difference series with a fixed marginal SD and AR(1) correlation *within*
    each day — same spread, different dependence."""
    rng = np.random.default_rng(seed)
    frames = []
    for d in range(n_days):
        eps = rng.normal(0, sd * np.sqrt(1 - rho ** 2), per_day)
        x = np.empty(per_day)
        x[0] = rng.normal(0, sd)
        for i in range(1, per_day):
            x[i] = rho * x[i - 1] + eps[i]
        stamps = pd.date_range(f"2026-03-{d + 1:02d} 06:00", periods=per_day, freq="15min", tz=TZ)
        frames.append(pd.DataFrame({ROUTE_COL: "R", "Date Time": stamps, TT_COL: x}))
    return pd.concat(frames, ignore_index=True)


def test_day_blocked_ci_widens_under_autocorrelation_while_the_naive_one_does_not():
    """15-minute bins are not independent. With the same marginal spread, a
    strongly autocorrelated difference series carries far less information — the
    day-blocked interval widens to say so, while the per-bin interval barely
    moves. That gap is why the naive interval is reported only as ``*_naive``."""
    zero_ref = lambda frame: frame.assign(**{TT_COL: 0.0})   # noqa: E731

    iid = _series_with_ar1(rho=0.0, seed=1)
    ar1 = _series_with_ar1(rho=0.95, seed=1)
    got_iid = agreement.compare(iid, zero_ref(iid)).iloc[0]
    got_ar1 = agreement.compare(ar1, zero_ref(ar1)).iloc[0]

    block_iid = got_iid["bias_ci_high"] - got_iid["bias_ci_low"]
    block_ar1 = got_ar1["bias_ci_high"] - got_ar1["bias_ci_low"]
    naive_iid = got_iid["bias_ci_high_naive"] - got_iid["bias_ci_low_naive"]
    naive_ar1 = got_ar1["bias_ci_high_naive"] - got_ar1["bias_ci_low_naive"]

    assert block_ar1 > 2.5 * block_iid                 # the honest interval widens
    assert naive_ar1 == pytest.approx(naive_iid, rel=0.25)   # the per-bin one barely moves
    assert got_ar1["ci_width_ratio"] > 2.5
    assert got_iid["n_days"] == 30 and got_ar1["n_bins"] == 30 * 48


# ---------------------------------------------------------------------------
# Independent-n accounting  (G5)
# ---------------------------------------------------------------------------
class _Chain:
    """Minimal stand-in for corridors.ChainResult."""
    def __init__(self, ids, miles):
        self.segment_ids = tuple(ids)
        self.extent_miles = tuple(miles)


def test_independent_totals_refuses_to_double_count_overlapping_routes():
    inrix = pd.concat([_frame([5.0] * 4, route="Full"), _frame([3.0] * 4, route="Sub")],
                      ignore_index=True)
    reference = pd.concat([_frame([6.0] * 4, route="Full"), _frame([4.0] * 4, route="Sub")],
                          ignore_index=True)
    matched = agreement.match_bins(inrix, reference)
    chains = {"Full": _Chain([1, 2, 3], [1.0, 1.0, 1.0]), "Sub": _Chain([2, 3], [1.0, 1.0])}

    got = agreement.independent_totals(matched, chains=chains).iloc[0]
    assert got["n_routes"] == 2
    assert got["n_bins_sum"] == 8              # the impressive number ...
    assert got["n_bin_slots_distinct"] == 4    # ... over four quarter hours
    assert got["route_days_sum"] == 2 and got["days_distinct"] == 1
    assert got["chain_miles_sum"] == pytest.approx(5.0)
    assert got["miles_distinct"] == pytest.approx(3.0)
    assert got["miles_overlap"] == pytest.approx(2.0)
    assert got["n_segments_distinct"] == 3

    overlaps = agreement.independent_totals(matched, chains=chains).attrs["overlaps"]
    assert overlaps.loc[0, "shared_segments"] == 2
    assert overlaps.loc[0, "shared_miles"] == pytest.approx(2.0)


def test_independent_totals_without_chains_still_reports_time():
    matched = agreement.match_bins(_frame([5.0] * 4), _frame([4.0] * 4))
    got = agreement.independent_totals(matched).iloc[0]
    assert got["n_bins_sum"] == 4 and got["days_distinct"] == 1
    assert "miles_distinct" not in got.index
    assert agreement.independent_totals(matched).attrs["overlaps"].empty


# ---------------------------------------------------------------------------
# Profiles  (Item 30 — the shapes a figure draws, computed in the core)
# ---------------------------------------------------------------------------
def _matched_two_days():
    """Two days, four bins each, with a known per-group mean."""
    stamps = list(pd.date_range("2026-03-03 08:00", periods=4, freq="1h", tz=TZ))
    stamps += list(pd.date_range("2026-03-04 08:00", periods=4, freq="1h", tz=TZ))
    inrix = pd.DataFrame({ROUTE_COL: "R", "Date Time": stamps,
                          TT_COL: [5, 6, 7, 8, 7, 8, 9, 10]})
    ref = pd.DataFrame({ROUTE_COL: "R", "Date Time": stamps,
                        TT_COL: [10, 10, 10, 10, 10, 10, 10, 10]})
    return agreement.match_bins(inrix, ref)


def test_profile_by_time_of_day_averages_across_days():
    prof = agreement.profile(_matched_two_days(), by="time_of_day")
    assert list(prof["time_of_day"]) == ["08:00", "09:00", "10:00", "11:00"]
    assert list(prof["minute_of_day"]) == [480, 540, 600, 660]
    assert list(prof["n_bins"]) == [2, 2, 2, 2]
    # 08:00 is INRIX 5 and 7 against a reference of 10 on both days.
    assert prof.loc[0, "mean_inrix"] == pytest.approx(6.0)
    assert prof.loc[0, "mean_ref"] == pytest.approx(10.0)
    assert prof.loc[0, "bias"] == pytest.approx(-4.0)


def test_profile_bins_time_of_day_at_the_requested_width():
    prof = agreement.profile(_matched_two_days(), by="time_of_day", bin_minutes=120)
    assert list(prof["time_of_day"]) == ["08:00", "10:00"]
    assert list(prof["n_bins"]) == [4, 4]


def test_profile_by_day_of_week_is_ordered_from_monday():
    prof = agreement.profile(_matched_two_days(), by=["day_of_week", "time_of_day"])
    assert list(prof["day_of_week"].cat.categories)[:2] == ["Monday", "Tuesday"]
    assert set(prof["day_of_week"].astype(str)) == {"Tuesday", "Wednesday"}
    assert len(prof) == 8


def test_profile_by_date_gives_the_day_means_the_ci_is_built_from():
    prof = agreement.profile(_matched_two_days(), by="date")
    assert list(prof["date"].dt.strftime("%Y-%m-%d")) == ["2026-03-03", "2026-03-04"]
    assert list(prof["bias"]) == pytest.approx([-3.5, -1.5])


def test_profile_rejects_an_unknown_key():
    with pytest.raises(ValueError, match="Unknown profile key"):
        agreement.profile(_matched_two_days(), by="fortnight")


# ---------------------------------------------------------------------------
# The delay slope and the free-flow level gap  (ROADMAP Item 32)
# ---------------------------------------------------------------------------
def _delay_frames(slope=0.5, level_gap=0.0, congested_offset=0.0, n_days=20,
                  per_day=24, noise=0.0, slope_jitter=0.0, seed=3,
                  free_flow=10.0, delay_sd=3.0, floor_delay=0.0,
                  ref_extra=False, ref_no_traffic_drift=0.0):
    """A pair of frames with a **known** delay relationship.

    The reference is ``free_flow`` plus a per-bin delay, a quarter of each day at
    exactly zero so both 10th percentiles land on the free-flow shoulder. INRIX
    sits ``level_gap`` above that shoulder and carries ``slope ×`` the reference's
    delay. The three effects are separately known and separately recoverable:

    - ``level_gap`` moves the two sources' free flow apart and leaves delay alone;
    - ``slope`` compresses delay and leaves free flow alone;
    - ``congested_offset`` adds a fixed penalty **only** to bins that carry delay,
      which is the one way a real intercept arises — a constant added to *every*
      bin is absorbed into INRIX's own free flow and cannot show up as one.

    ``slope_jitter`` gives each day its own slope, the day-to-day heterogeneity
    the blocked interval exists to account for.

    ``floor_delay`` adds a constant to the reference's delay in **every** bin — a
    corridor congested through the whole logging window, whose 10th percentile is
    therefore not free flow. ``ref_extra`` has the reference carry its own delay in
    ``EXTRA_TT_COL`` the way the logger does, which is what lets the level gap be
    split (ROADMAP Item 33); ``ref_no_traffic_drift`` moves the reference's
    no-traffic duration by that much halfway through the record, so a drifting
    baseline can be told from a constant one.
    """
    rng = np.random.default_rng(seed)
    frames_i, frames_r = [], []
    for d in range(n_days):
        stamps = pd.date_range(f"2026-04-{d + 1:02d} 06:00", periods=per_day,
                               freq="30min", tz=TZ)
        delay = np.abs(rng.normal(0, delay_sd, per_day))
        delay[:max(1, per_day // 4)] = 0.0
        congested = delay > 0
        delay = delay + floor_delay
        day_slope = slope + (rng.normal(0, slope_jitter) if slope_jitter else 0.0)
        inrix_delay = day_slope * delay + congested_offset * congested
        inr = free_flow + level_gap + inrix_delay
        if noise:
            inr = inr + rng.normal(0, noise, per_day) * congested
        no_traffic = free_flow + (ref_no_traffic_drift if d >= n_days // 2 else 0.0)
        ref = pd.DataFrame({ROUTE_COL: "R", "Date Time": stamps,
                            TT_COL: no_traffic + delay})
        if ref_extra:
            ref[EXTRA_TT_COL] = delay
        frames_r.append(ref)
        frames_i.append(pd.DataFrame({ROUTE_COL: "R", "Date Time": stamps, TT_COL: inr}))
    return (pd.concat(frames_i, ignore_index=True), pd.concat(frames_r, ignore_index=True))


def test_the_slope_recovers_a_known_compression_with_a_zero_intercept():
    """The estimator is the finding. A slope of 0.5 with a zero intercept is what
    "INRIX credits half a minute per real minute" means — and the zero intercept
    is not an accident of this fixture: because each source's delay is measured
    above its **own** free flow, a purely multiplicative compression can only
    produce an intercept of zero. That is why the near-zero intercepts measured on
    the real arterials are read as evidence the disagreement is multiplicative."""
    got = agreement.compare(*_delay_frames(slope=0.5)).iloc[0]

    assert got["delay_slope"] == pytest.approx(0.5, abs=1e-9)
    assert got["delay_intercept"] == pytest.approx(0.0, abs=1e-9)
    assert got["delay_r2"] == pytest.approx(1.0, abs=1e-9)
    # An exact fit has no residual to build an interval from — NaN, not width 0.
    assert np.isnan(got["delay_slope_ci_low"]) and np.isnan(got["delay_slope_ci_high"])


def test_a_fixed_penalty_on_congested_bins_shows_up_as_a_positive_intercept():
    """The other side of the same reading: an intercept is a *fixed offset* in
    delay, which would partly cancel in a before/after difference. The estimator
    has to be able to see one, or "the intercepts are near zero" says nothing."""
    got = agreement.compare(*_delay_frames(slope=0.5, congested_offset=0.6)).iloc[0]

    assert got["delay_intercept"] > 0.25
    assert got["delay_intercept_ci_low"] > 0                      # and it is not zero
    assert got["delay_slope"] == pytest.approx(0.5, abs=0.1)
    assert got["free_flow_gap"] == pytest.approx(0.0, abs=1e-9)   # free flow untouched


def test_the_level_gap_and_the_slope_are_separated():
    """The defect Item 32 fixes: ``bias`` cannot tell "the two sources are
    measuring different pavement" from "INRIX compresses delay", and those have
    opposite consequences for a before/after study. A pure level shift must show
    in the gap and **not** in the slope, and a pure compression the reverse —
    while both collapse into the same single negative ``bias``."""
    level = agreement.compare(*_delay_frames(slope=1.0, level_gap=-5.0)).iloc[0]
    assert level["free_flow_gap"] == pytest.approx(-5.0, abs=1e-9)
    assert level["delay_slope"] == pytest.approx(1.0, abs=1e-9)

    compressed = agreement.compare(*_delay_frames(slope=0.5)).iloc[0]
    assert compressed["free_flow_gap"] == pytest.approx(0.0, abs=1e-9)
    assert compressed["delay_slope"] == pytest.approx(0.5, abs=1e-9)

    assert level["bias"] < 0 and compressed["bias"] < 0


def test_the_slope_survives_noise_and_its_interval_covers_the_truth():
    inrix, reference = _delay_frames(slope=0.55, noise=0.35, slope_jitter=0.05, seed=11)
    got = agreement.compare(inrix, reference).iloc[0]

    assert got["delay_slope"] == pytest.approx(0.55, abs=0.05)
    assert got["delay_slope_ci_low"] < 0.55 < got["delay_slope_ci_high"]
    assert got["delay_intercept_ci_low"] < 0.0 < got["delay_intercept_ci_high"]


def test_the_level_gap_estimate_falls_inside_its_own_interval():
    """The interval is a day-block **bootstrap of the pooled gap**, not a t
    interval over per-day gaps. The two are different estimands, and on the real
    arterials they disagree by enough to put the point estimate outside its own
    interval (Eagle Rd NB: −5.36 against a per-day interval of [−5.96, −5.71]),
    which is not a thing a report may print."""
    inrix, reference = _delay_frames(slope=0.6, level_gap=-2.0, noise=0.4, seed=5)
    got = agreement.compare(inrix, reference).iloc[0]

    assert got["free_flow_gap_ci_low"] <= got["free_flow_gap"] <= got["free_flow_gap_ci_high"]
    assert got["free_flow_gap"] == pytest.approx(-2.0, abs=0.1)
    assert got["n_free_flow_days"] == 20


def test_the_slope_interval_is_day_blocked_and_the_bootstrap_is_deterministic():
    """Same argument as the bias CI: bins inside a day are not independent, so the
    textbook OLS interval is too narrow. Here the dependence is a day-to-day
    slope — the compression a corridor shows on Tuesday is not the one it shows on
    Friday — and the day-clustered interval widens to say so while the per-bin one
    does not. And a report re-run must reproduce its own numbers."""
    steady = agreement.compare(*_delay_frames(slope=0.5, noise=0.5, seed=2)).iloc[0]
    varying = agreement.compare(*_delay_frames(slope=0.5, noise=0.5,
                                               slope_jitter=0.12, seed=2)).iloc[0]

    def widths(row):
        return (row["delay_slope_ci_high"] - row["delay_slope_ci_low"],
                row["delay_slope_ci_high_naive"] - row["delay_slope_ci_low_naive"])

    block_v, naive_v = widths(varying)
    block_s, naive_s = widths(steady)
    assert block_v > 3 * block_s                    # the honest interval widens
    assert naive_v == pytest.approx(naive_s, rel=0.6)   # the per-bin one barely moves
    assert varying["delay_slope_ci_width_ratio"] == pytest.approx(block_v / naive_v)
    assert varying["delay_slope_ci_width_ratio"] > 2

    again = agreement.compare(*_delay_frames(slope=0.5, noise=0.5,
                                             slope_jitter=0.12, seed=2)).iloc[0]
    assert again["free_flow_gap_ci_low"] == varying["free_flow_gap_ci_low"]
    assert again["free_flow_gap_ci_high"] == varying["free_flow_gap_ci_high"]


def test_a_near_zero_delay_route_has_an_unstable_ratio_and_a_stable_slope():
    """The VSL SB PM case, pinned as a regression. On a route carrying little delay
    the ratio of two small means read **2.32** while the regression on the same
    bins read 0.71 — a route where INRIX compresses delay was being reported as
    showing more than twice as much of it. Instability is the charge, so the test
    measures it: split the same route in half by date and the ratio moves by a
    third while the slope moves by a hundredth, each half's interval covering the
    other half's estimate."""
    inrix, reference = _delay_frames(slope=0.7, delay_sd=0.4, noise=0.4, seed=17)
    halves = []
    for lo, hi in (("2026-04-01", "2026-04-11"), ("2026-04-11", "2026-04-21")):
        window = lambda f: f[(f["Date Time"] >= lo) & (f["Date Time"] < hi)]  # noqa: E731
        halves.append(agreement.compare(window(inrix), window(reference)).iloc[0])
    a, b = halves

    ratio_swing = abs(a["delay_ratio"] - b["delay_ratio"]) / ((a["delay_ratio"] + b["delay_ratio"]) / 2)
    assert a["delay_ratio"] > 1 and ratio_swing > 0.25       # the unusable statistic
    assert abs(a["delay_slope"] - b["delay_slope"]) < 0.05   # …and the usable one
    assert a["delay_slope_ci_low"] <= b["delay_slope"] <= a["delay_slope_ci_high"]
    assert b["delay_slope_ci_low"] <= a["delay_slope"] <= b["delay_slope_ci_high"]

    whole = agreement.compare(inrix, reference).iloc[0]
    assert whole["delay_ratio"] > whole["delay_slope_ci_high"]


def test_delay_regression_agrees_with_compare_column_for_column():
    inrix, reference = _delay_frames(slope=0.5, noise=0.2, slope_jitter=0.05)
    matched = agreement.match_bins(inrix, reference)
    reg = agreement.delay_regression(matched).iloc[0]
    row = agreement.compare(None, None, matched=matched).iloc[0]
    for column in ("free_flow_gap", "free_flow_gap_ci_low", "free_flow_gap_ci_high",
                   "delay_slope", "delay_slope_ci_low", "delay_slope_ci_high",
                   "delay_intercept", "delay_r2", "delay_ratio"):
        assert reg[column] == pytest.approx(row[column])


def test_a_degenerate_regression_reports_undefined_not_certain():
    """A constant regressor, or fewer than three bins, has no slope — NaN, not a
    number with a width-0 interval around it; and one day is no blocks."""
    flat = agreement.compare(_frame([5.0] * 8), _frame([4.0] * 8)).iloc[0]
    assert np.isnan(flat["delay_slope"]) and np.isnan(flat["delay_intercept"])
    assert np.isnan(flat["delay_slope_ci_low"]) and np.isnan(flat["delay_r2"])

    two = agreement.compare(_frame([5.0, 7.0]), _frame([4.0, 8.0])).iloc[0]
    assert np.isnan(two["delay_slope"])

    single_day = agreement.compare(_frame([4.0, 6.0, 8.0, 10.0]),
                                   _frame([5.0, 9.0, 9.0, 13.0])).iloc[0]
    assert np.isfinite(single_day["delay_slope"])
    assert np.isnan(single_day["delay_slope_ci_low"])
    assert np.isnan(single_day["free_flow_gap_ci_low"])


# ---------------------------------------------------------------------------
# Splitting the level gap  (ROADMAP Item 33)
# ---------------------------------------------------------------------------
def test_match_bins_carries_the_reference_extra_travel_time():
    """The logger records the provider's own delay beside its travel time, and
    Item 33 is the finding that without it a level gap cannot be read: the column
    has to survive the join, renamed so it cannot be confused with the INRIX side."""
    ref = _frame([10.0, 12.0]).assign(**{EXTRA_TT_COL: [0.0, 2.0]})
    m = agreement.match_bins(_frame([5.0, 6.0]), ref)
    assert m[agreement.REF_EXTRA_COL].tolist() == pytest.approx([0.0, 2.0])
    assert EXTRA_TT_COL not in m.columns


def test_match_bins_skips_an_extra_column_the_workbook_never_filled():
    """``load_tt_logger`` fills the column with NaN for a sheet that has none, and
    an all-NaN column would advertise a decomposition the workbook cannot support."""
    empty = _frame([10.0, 12.0]).assign(**{EXTRA_TT_COL: [float("nan")] * 2})
    assert agreement.REF_EXTRA_COL not in agreement.match_bins(_frame([5.0, 6.0]), empty)
    assert agreement.REF_EXTRA_COL not in agreement.match_bins(_frame([5.0, 6.0]),
                                                               _frame([10.0, 12.0]))


def test_a_level_gap_over_a_reference_that_never_reaches_free_flow_is_not_a_level_gap():
    """The Item 33 defect, in the smallest form that shows it. This corridor has
    **no** level difference and a 0.5 compression, but the reference carries 4
    minutes of delay in every bin — so its 10th percentile is not free flow, and
    the gap reads −2.00 minutes as if the two sources measured different pavement.

    The split says otherwise: the whole of it is the reference's own delay at that
    percentile (4.00) against a positive static remainder, and
    ``free_flow_gap == free_flow_gap_static − ref_free_flow_delay`` holds exactly.
    This is Eagle Rd NB, where 4.04 minutes of a 5.36-minute gap is delay the
    reference itself reports."""
    got = agreement.compare(*_delay_frames(slope=0.5, level_gap=0.0,
                                           floor_delay=4.0, ref_extra=True)).iloc[0]

    assert got["free_flow_gap"] == pytest.approx(-2.0, abs=1e-9)   # looks like a level gap
    assert got["ref_free_flow_delay"] == pytest.approx(4.0, abs=1e-9)
    assert got["free_flow_gap_static"] == pytest.approx(2.0, abs=1e-9)
    assert got["free_flow_gap"] == pytest.approx(
        got["free_flow_gap_static"] - got["ref_free_flow_delay"], abs=1e-9)
    assert got["ref_no_traffic"] == pytest.approx(10.0, abs=1e-9)


def test_a_real_level_gap_survives_the_split_whole():
    """The other direction: when the reference *does* reach free flow, none of the
    gap is its own delay and the static remainder is the gap. A split that could
    not tell these two apart would explain away a real finding."""
    got = agreement.compare(*_delay_frames(slope=1.0, level_gap=-5.0,
                                           ref_extra=True)).iloc[0]

    assert got["ref_free_flow_delay"] == pytest.approx(0.0, abs=1e-9)
    assert got["free_flow_gap_static"] == pytest.approx(-5.0, abs=1e-9)
    assert got["free_flow_gap"] == pytest.approx(-5.0, abs=1e-9)


def test_a_drifting_no_traffic_duration_is_measured_not_assumed():
    """The decomposition is taken over a single no-traffic duration per route,
    which is what this logger has (1,936 bins of SH-69 SB at exactly 11.3167).
    ``ref_no_traffic_spread`` is how a reader knows that held for their run — a
    provider that re-routed a sheet mid-record moves the baseline the split is
    measured against."""
    steady = agreement.compare(*_delay_frames(ref_extra=True)).iloc[0]
    assert steady["ref_no_traffic_spread"] == pytest.approx(0.0, abs=1e-9)

    drifted = agreement.compare(*_delay_frames(ref_extra=True,
                                               ref_no_traffic_drift=1.5)).iloc[0]
    assert drifted["ref_no_traffic_spread"] == pytest.approx(1.5, abs=1e-9)


def test_the_split_is_undefined_without_the_references_own_delay():
    """No extra-travel-time column, no split — NaN rather than a baseline invented
    from the travel times, which would make the gap decompose into itself."""
    got = agreement.compare(*_delay_frames(slope=0.5, level_gap=-2.0)).iloc[0]

    assert np.isnan(got["ref_no_traffic"]) and np.isnan(got["ref_no_traffic_spread"])
    assert np.isnan(got["ref_free_flow_delay"]) and np.isnan(got["free_flow_gap_static"])
    assert got["free_flow_gap"] == pytest.approx(-2.0, abs=1e-9)   # still reported

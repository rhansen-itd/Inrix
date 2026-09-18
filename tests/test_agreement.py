"""Tests for inrix_tools.agreement (ROADMAP Item 29)."""
import numpy as np
import pandas as pd
import pytest

from inrix_tools import agreement
from inrix_tools.reference import ROUTE_COL, TT_COL

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

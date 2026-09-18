"""Agreement statistics between INRIX and an external travel-time reference.
(ROADMAP Item 29)

Two sources measuring the same corridor, matched bin for bin. What matters is
**how far apart they are and how sure we are of that** — effect size plus a
confidence interval, the standard this project already holds before/after work to
(CLAUDE.md, REVIEW_ITEM14 §4.1). The 2026-09-17 outside pass had none (G6) and led
with correlation instead: Eagle Rd SB was promoted as "highest correlation in
Idaho (r = 0.945)" while carrying a **5.49-minute** systematic bias. Correlation is
reported here, deliberately last.

Three deliberate choices:

- **The CI is day-blocked.** 15-minute bins are heavily autocorrelated, so a CI
  over bins is far too narrow (simulated null coverage of a nominal 95% interval
  is 25–50% at traffic-realistic AR(1) rho, vs ~96% for daily means). The primary
  interval is a t-interval over **per-day mean differences**; the per-bin version
  is reported beside it as ``*_naive``, with ``ci_width_ratio`` showing how much
  the honest version widens.
- **Delay is compared on each source's own free-flow, and reported as a slope.**
  Delay above this source's own 10th-percentile free-flow is what the use case
  cares about, and it is what exposed the arterial delay compression (G1). The
  headline statistic is the **regression of INRIX delay on reference delay**
  (:func:`delay_regression`) — slope, intercept, and a day-blocked CI on both —
  because the ratio of means it replaces carries no interval and is unstable on a
  low-delay route. The **free-flow level gap** is reported separately from the
  slope: the two effects ``bias`` fuses do not have the same consequence for a
  before/after study, since only the level gap cancels in a difference. The **SD
  ratio** sits beside the ratio because it is symmetric — if the two agree, the
  gap is not a regression-dilution artifact. The level gap is itself **split**
  (Item 33) into the delay the reference still carries at that percentile and the
  static remainder, because on a corridor congested through the whole logging
  window the reference's 10th percentile is not free flow and a gap quoted there
  is not a statement about the road.
- **n is accounted, not summed.** :func:`independent_totals` reports distinct days
  and distinct pavement, so four overlapping sheets of the same three miles cannot
  become "28,340 matched observations" (G5).

Compute only — no plotting (see CLAUDE.md). Units: minutes, miles.
"""
from __future__ import annotations

import math
from typing import Sequence

import numpy as np
import pandas as pd

from .io import DATETIME_COL
from .reference import EXTRA_TT_COL, ROUTE_COL, TT_COL

INRIX_COL = "INRIX Travel Time(Minutes)"
REF_COL = "Reference Travel Time(Minutes)"
REF_EXTRA_COL = "Reference Extra Travel Time(Minutes)"   # the reference's own delay
DIFF_COL = "Difference(Minutes)"          # INRIX − reference
MEAN_COL = "Mean(Minutes)"                # Bland-Altman x-axis


# ---------------------------------------------------------------------------
# Matching
# ---------------------------------------------------------------------------
# INRIX-side per-bin coverage columns carried through the match, so the CValue
# gate's cost and the imputation share reach the scorecard instead of stopping at
# the corridor sum (ROADMAP Item 31).
CARRY_COLS = ("imputed_fraction", "cvalue_kept_fraction", "short", "n_absent")

# Reference-side per-bin columns carried through the match (ROADMAP Item 33). The
# logger records the provider's own *delay* beside its travel time, and
# ``travel time − extra`` is that route's **no-traffic duration** — a static
# property of the path the provider routed. Without it the free-flow level gap is
# a single number with two causes inside it; with it the gap decomposes (see
# :func:`_delay_block`).
REF_CARRY_COLS = (EXTRA_TT_COL,)
_REF_CARRY_NAMES = {EXTRA_TT_COL: REF_EXTRA_COL}


def match_bins(inrix: pd.DataFrame, reference: pd.DataFrame, key: str = ROUTE_COL,
               datetime_col: str = DATETIME_COL, value_inrix: str = TT_COL,
               value_ref: str = TT_COL, carry: Sequence[str] = CARRY_COLS,
               carry_ref: Sequence[str] = REF_CARRY_COLS) -> pd.DataFrame:
    """Inner-join the two sources bin for bin.

    Both frames must already be binned to the same cadence (``reference`` through
    ``reference.align_to_bins`` — **floored**, not rounded) and carry the same
    ``key`` values, so the INRIX side is labelled with the route names it is being
    compared against. Several reference samples inside one bin are averaged, and
    the count is kept as ``n_ref_samples``: a bin backed by two samples is not
    silently treated the same as one backed by ten.

    Returns:
        ``[<key>, Date Time, INRIX Travel Time(Minutes),
        Reference Travel Time(Minutes), Difference(Minutes), Mean(Minutes),
        n_ref_samples]`` — the per-bin frame every statistic below is computed
        from, and the frame a Bland-Altman or scatter figure consumes (the figure
        computes nothing) — plus whichever of ``carry`` the INRIX frame supplies
        (mean per bin; booleans become the share of that bin that was flagged),
        and ``Reference Extra Travel Time(Minutes)`` where the reference frame
        supplies it.

    Args:
        carry: INRIX-side per-bin columns to bring through the join
            (:data:`CARRY_COLS` by default — the CValue gate's cost, the
            imputation share, and the short-chain flags from
            ``corridors.chain_travel_time``). A column the INRIX frame does not
            have is skipped, not an error: this is diagnostic weight, and a frame
            built without it still matches.
        carry_ref: reference-side per-bin columns to bring through
            (:data:`REF_CARRY_COLS` — the logger's own extra-travel-time column,
            renamed :data:`REF_EXTRA_COL`). Skipped when absent **or entirely
            missing**: ``reference.load_tt_logger`` fills the column with NaN for
            a sheet that has no such column, and an all-NaN column would advertise
            a decomposition the workbook cannot support.
    """
    ref_carried = [c for c in carry_ref
                   if c in reference.columns and reference[c].notna().any()]
    ref_agg = {REF_COL: (value_ref, "mean"), "n_ref_samples": (value_ref, "size"),
               **{_REF_CARRY_NAMES.get(c, c): (c, "mean") for c in ref_carried}}
    ref = (reference.groupby([key, datetime_col], observed=True)
           .agg(**ref_agg).reset_index())
    carried = [c for c in carry if c in inrix.columns]
    agg = {INRIX_COL: (value_inrix, "mean"), **{c: (c, "mean") for c in carried}}
    inr = (inrix.groupby([key, datetime_col], observed=True)
           .agg(**agg).reset_index())

    out = inr.merge(ref, on=[key, datetime_col], how="inner")
    out = out[out[INRIX_COL].notna() & out[REF_COL].notna()].copy()
    out[DIFF_COL] = out[INRIX_COL] - out[REF_COL]
    out[MEAN_COL] = (out[INRIX_COL] + out[REF_COL]) / 2.0
    return out.sort_values([key, datetime_col]).reset_index(drop=True)


# ---------------------------------------------------------------------------
# Statistics
# ---------------------------------------------------------------------------
def _local_naive(stamps: pd.Series) -> pd.Series:
    """Local wall clock as naive timestamps — days and profiles are read in local
    time, so a corridor's "day" is the one its operators work."""
    return (stamps.dt.tz_localize(None) if getattr(stamps.dt, "tz", None) is not None
            else stamps)


def _day_key(stamps: pd.Series) -> pd.Series:
    """The local calendar day each bin falls in — the block every interval here
    is built over."""
    return _local_naive(stamps).dt.normalize()


def _t_interval(values: pd.Series, confidence: float) -> tuple[float, float, int]:
    """Two-sided t interval on the mean of ``values``; NaN bounds for n < 2 or a
    degenerate (zero-variance) sample, rather than a width-0 claim of certainty."""
    from scipy import stats

    v = pd.Series(values).dropna()
    n = len(v)
    if n < 2:
        return float("nan"), float("nan"), n
    sd = float(v.std(ddof=1))
    if not (sd > 0):
        return float("nan"), float("nan"), n
    half = stats.t.ppf(1 - (1 - confidence) / 2, n - 1) * sd / math.sqrt(n)
    mean = float(v.mean())
    return mean - half, mean + half, n


def _delay_above(values: pd.Series, percentile: float) -> tuple[float, float]:
    """(free-flow value, mean delay above it) — the source's own free-flow
    percentile, so neither source is judged against the other's baseline."""
    v = pd.Series(values).dropna()
    if v.empty:
        return float("nan"), float("nan")
    ff = float(v.quantile(percentile / 100.0))
    return ff, float((v - ff).clip(lower=0).mean())




# ---------------------------------------------------------------------------
# Delay: the free-flow level gap and the slope  (ROADMAP Item 32)
# ---------------------------------------------------------------------------
# The free-flow level gap's interval is a day-block bootstrap (see
# :func:`_free_flow_gap_ci`); fixed so a report re-run reproduces its own numbers.
FREE_FLOW_GAP_BOOT = 1000
FREE_FLOW_GAP_SEED = 0


def _ols_day_blocked(x: pd.Series, y: pd.Series, days, confidence: float) -> dict:
    """OLS ``y = intercept + slope * x`` with a **day-blocked** interval on both.

    The blocking argument is the one the bias CI already makes: 15-minute bins
    inside a day are not independent, so the textbook OLS interval is far too
    narrow. Here that is done by clustering the sandwich estimator on the day
    (CR1 correction, ``t`` with ``G - 1`` degrees of freedom) rather than by
    averaging first — a regression has no per-day scalar to average. The
    per-bin interval is returned beside it as ``*_naive`` for the same reason
    ``bias`` reports one: the width the autocorrelation costs should be visible.

    Returns NaN bounds rather than a width-0 claim of certainty when the fit is
    degenerate — fewer than three points, a constant regressor, a fit exact to
    machine precision, or a single day (one block is no blocking).
    """
    from scipy import stats

    frame = pd.DataFrame({"x": pd.Series(x).to_numpy(dtype=float),
                          "y": pd.Series(y).to_numpy(dtype=float),
                          "g": np.asarray(days)}).dropna()
    n = len(frame)
    nan = float("nan")
    blank = {"slope": nan, "intercept": nan, "r2": nan, "n": n, "n_days": 0,
             "slope_ci": (nan, nan), "intercept_ci": (nan, nan),
             "slope_ci_naive": (nan, nan)}
    xv, yv = frame["x"].to_numpy(), frame["y"].to_numpy()
    if n < 3 or not (xv.std() > 0):
        return blank

    X = np.column_stack([np.ones(n), xv])
    xtx_inv = np.linalg.pinv(X.T @ X)
    beta = xtx_inv @ (X.T @ yv)
    resid = yv - X @ beta
    rss = float(resid @ resid)
    tss = float(((yv - yv.mean()) ** 2).sum())

    groups = pd.Index(frame["g"])
    n_days = int(groups.nunique())
    out = {"slope": float(beta[1]), "intercept": float(beta[0]),
           "r2": (1.0 - rss / tss) if tss > 0 else nan,
           "n": n, "n_days": n_days,
           "slope_ci": (nan, nan), "intercept_ci": (nan, nan),
           "slope_ci_naive": (nan, nan)}
    # An exact fit (to machine precision) has no residual information to build an
    # interval from; the arithmetic would still hand back a width of ~1e-16, which
    # reads as absolute certainty. Degenerate, like a zero-variance sample.
    if not (rss > 1e-12 * max(tss, 1.0)):
        return out

    se_naive = np.sqrt(np.diag(rss / (n - 2) * xtx_inv))
    t_naive = stats.t.ppf(1 - (1 - confidence) / 2, n - 2)
    out["slope_ci_naive"] = (out["slope"] - t_naive * se_naive[1],
                             out["slope"] + t_naive * se_naive[1])

    if n_days < 2:
        return out
    scores = (X * resid[:, None])
    meat = np.zeros((2, 2))
    for _, idx in pd.Series(np.arange(n)).groupby(groups.to_numpy()):
        u = scores[idx.to_numpy()].sum(axis=0)
        meat += np.outer(u, u)
    scale = (n_days / (n_days - 1)) * ((n - 1) / (n - 2))
    se = np.sqrt(np.diag(scale * xtx_inv @ meat @ xtx_inv))
    crit = stats.t.ppf(1 - (1 - confidence) / 2, n_days - 1)
    out["slope_ci"] = (out["slope"] - crit * se[1], out["slope"] + crit * se[1])
    out["intercept_ci"] = (out["intercept"] - crit * se[0],
                           out["intercept"] + crit * se[0])
    return out


def _free_flow_gap_ci(grp: pd.DataFrame, days, percentile: float,
                      confidence: float) -> tuple[float, float, int]:
    """Day-blocked interval for the **pooled** free-flow gap, by resampling days.

    A difference of two quantiles has no closed-form clustered standard error the
    way a mean or a regression coefficient does, so the blocking is done by
    resampling whole days with replacement and recomputing both percentiles over
    the resampled pool — the percentile interval of :data:`FREE_FLOW_GAP_BOOT`
    draws. Resampling the **day**, not the bin, is the same argument the bias CI
    makes: bins inside a day are not independent.

    Deterministic by construction (:data:`FREE_FLOW_GAP_SEED`, a fresh generator
    per route so route order cannot move a number), and it estimates the quantity
    actually reported rather than a per-day average of it — a point estimate must
    fall inside its own interval.
    """
    q = percentile / 100.0
    frame = grp[[INRIX_COL, REF_COL]].assign(_day=np.asarray(days)).dropna()
    if frame.empty:
        return float("nan"), float("nan"), 0
    codes, uniques = pd.factorize(frame["_day"])
    n_days = len(uniques)
    if n_days < 2:
        return float("nan"), float("nan"), n_days

    # Sort once so each day is a contiguous block, and hold the blocks: a draw is
    # then a concatenation of whole days rather than a per-bin index build.
    order = np.argsort(codes, kind="stable")
    inrix = frame[INRIX_COL].to_numpy()[order]
    ref = frame[REF_COL].to_numpy()[order]
    sorted_codes = codes[order]
    starts = np.searchsorted(sorted_codes, np.arange(n_days), side="left")
    stops = np.searchsorted(sorted_codes, np.arange(n_days), side="right")
    blocks = [np.arange(a, b) for a, b in zip(starts, stops)]

    rng = np.random.default_rng(FREE_FLOW_GAP_SEED)
    gaps = np.empty(FREE_FLOW_GAP_BOOT)
    for b in range(FREE_FLOW_GAP_BOOT):
        idx = np.concatenate([blocks[d] for d in rng.integers(0, n_days, n_days)])
        gaps[b] = np.quantile(inrix[idx], q) - np.quantile(ref[idx], q)
    alpha = (1 - confidence) / 2
    low, high = np.quantile(gaps, [alpha, 1 - alpha])
    return float(low), float(high), n_days


def _ref_no_traffic(grp: pd.DataFrame) -> dict:
    """The reference's own **no-traffic duration** for a route, and its spread.

    ``travel time − extra travel time``, per bin. On this logger it is a single
    constant per sheet — 1,936 bins of SH-69 SB over six months carry exactly
    11.3167 minutes — because it is a property of the path the provider routed and
    not of any one trip. The spread is reported beside it precisely so that
    constancy is a **measurement** in the output rather than an assumption: a route
    whose no-traffic duration drifts (the provider re-routed it mid-record) has a
    decomposition taken over a moving baseline, and the number says so.

    The median, not the mean, is the summary: a handful of bins on some sheets
    carry a one-off value a few seconds away, and a median ignores them where a
    mean would move the whole decomposition by a hair for no reason.

    Returns NaN for both when the match did not carry :data:`REF_EXTRA_COL`, which
    leaves every column built from it NaN rather than fabricating a baseline.
    """
    if REF_EXTRA_COL not in grp.columns:
        return {"ref_no_traffic": float("nan"), "ref_no_traffic_spread": float("nan")}
    base = (grp[REF_COL] - grp[REF_EXTRA_COL]).dropna()
    if base.empty:
        return {"ref_no_traffic": float("nan"), "ref_no_traffic_spread": float("nan")}
    return {"ref_no_traffic": float(base.median()),
            "ref_no_traffic_spread": float(base.max() - base.min())}


def _delay_block(grp: pd.DataFrame, datetime_col: str, free_flow_percentile: float,
                 confidence: float) -> dict:
    """The whole delay comparison for one route: level gap, ratio, and slope.

    The two effects a single ``bias`` fuses are reported separately here. A
    corridor can disagree because the two sources sit at different levels **at
    free flow** (which cancels in a before/after difference) and because INRIX
    compresses delay (which does not) — and those have opposite consequences for
    an intervention study, so they get their own columns.

    **The level gap itself splits in two** (ROADMAP Item 33), whenever the match
    carried :data:`REF_EXTRA_COL`. ``free_flow_ref`` is the reference's travel time
    at its own free-flow percentile, and the reference says how much of that is
    delay, so::

        ref_no_traffic       = median(reference travel time − reference extra)
        ref_free_flow_delay  = free_flow_ref − ref_no_traffic
        free_flow_gap_static = free_flow_inrix − ref_no_traffic
        free_flow_gap        = free_flow_gap_static − ref_free_flow_delay

    ``ref_free_flow_delay`` is the delay the reference **still carries at the
    percentile the gap is measured at** — it is 0 only if that percentile really is
    free flow on the reference side, and on a corridor congested through the whole
    logging window it is not (Eagle Rd NB: 4.04 min of a 5.36 min gap). What is left,
    ``free_flow_gap_static``, compares two open-road numbers: INRIX's free-flow
    travel time for the chain against the provider's no-traffic duration for the
    path *it* routed. Only the static part can be a statement about the road.
    """
    ff_i, delay_i = _delay_above(grp[INRIX_COL], free_flow_percentile)
    ff_r, delay_r = _delay_above(grp[REF_COL], free_flow_percentile)
    days = _day_key(grp[datetime_col])
    no_traffic = _ref_no_traffic(grp)

    fit = _ols_day_blocked((grp[REF_COL] - ff_r).clip(lower=0),
                           (grp[INRIX_COL] - ff_i).clip(lower=0), days, confidence)
    gap_low, gap_high, n_gap_days = _free_flow_gap_ci(
        grp, days, free_flow_percentile, confidence)
    width = fit["slope_ci"][1] - fit["slope_ci"][0]
    nv_width = fit["slope_ci_naive"][1] - fit["slope_ci_naive"][0]

    return {
        "free_flow_inrix": ff_i, "free_flow_ref": ff_r,
        "free_flow_gap": (ff_i - ff_r) if ff_i == ff_i and ff_r == ff_r else float("nan"),
        "free_flow_gap_ci_low": gap_low, "free_flow_gap_ci_high": gap_high,
        "n_free_flow_days": int(n_gap_days),
        "ref_no_traffic": no_traffic["ref_no_traffic"],
        "ref_no_traffic_spread": no_traffic["ref_no_traffic_spread"],
        "ref_free_flow_delay": ff_r - no_traffic["ref_no_traffic"],
        "free_flow_gap_static": ff_i - no_traffic["ref_no_traffic"],
        "delay_inrix": delay_i, "delay_ref": delay_r,
        "delay_ratio": (delay_i / delay_r) if delay_r else float("nan"),
        "delay_slope": fit["slope"],
        "delay_slope_ci_low": fit["slope_ci"][0], "delay_slope_ci_high": fit["slope_ci"][1],
        "delay_slope_ci_low_naive": fit["slope_ci_naive"][0],
        "delay_slope_ci_high_naive": fit["slope_ci_naive"][1],
        "delay_slope_ci_width_ratio": ((width / nv_width)
                                       if nv_width and np.isfinite(nv_width) and np.isfinite(width)
                                       else float("nan")),
        "delay_intercept": fit["intercept"],
        "delay_intercept_ci_low": fit["intercept_ci"][0],
        "delay_intercept_ci_high": fit["intercept_ci"][1],
        "delay_r2": fit["r2"],
        "n_delay_days": fit["n_days"],
    }


def delay_regression(matched: pd.DataFrame, key: str = ROUTE_COL,
                     datetime_col: str = DATETIME_COL, confidence: float = 0.95,
                     free_flow_percentile: float = 10.0) -> pd.DataFrame:
    """Per-route regression of INRIX delay on reference delay, one row per route.

    Each source's delay is measured above its **own** free-flow percentile, so
    neither is judged against the other's baseline; the regression is over every
    matched bin, including the free-flow ones that pile at the origin and anchor
    the intercept.

    Why this and not the ratio of means: ``delay_ratio`` divides two small means
    and carries no interval, and on a route with little delay it is wild — VSL SB
    PM reports **2.32** against a slope of **0.71** on the same bins. The slope is
    far better conditioned and admits a day-blocked CI, so it is the primary
    statistic; ``delay_ratio`` is kept beside it because Item 30's published
    numbers are stated in it, and swapping a definition under a published name is
    how a report stops being comparable to itself.

    Returns:
        ``[<key>, free_flow_inrix, free_flow_ref, free_flow_gap,
        free_flow_gap_ci_low/high, n_free_flow_days, ref_no_traffic,
        ref_no_traffic_spread, ref_free_flow_delay, free_flow_gap_static,
        delay_inrix, delay_ref,
        delay_ratio, delay_slope, delay_slope_ci_low/high,
        delay_slope_ci_low_naive/high_naive, delay_slope_ci_width_ratio,
        delay_intercept, delay_intercept_ci_low/high, delay_r2, n_delay_days,
        n_bins]`` — the same block :func:`compare` folds into its own rows.

        A near-zero ``delay_intercept`` with a slope below 1 is the finding that
        matters for this project: the disagreement is **multiplicative in delay**,
        not a fixed offset, so it does not cancel in a before/after difference.
    """
    rows = []
    for route, grp in matched.groupby(key, observed=True, sort=True):
        rows.append({key: route, "n_bins": int(len(grp)),
                     **_delay_block(grp, datetime_col, free_flow_percentile, confidence)})
    out = pd.DataFrame(rows)
    out.attrs = {"confidence": confidence, "free_flow_percentile": free_flow_percentile}
    return out


# ---------------------------------------------------------------------------
# The scorecard row
# ---------------------------------------------------------------------------
def compare(inrix: pd.DataFrame, reference: pd.DataFrame, key: str = ROUTE_COL,
            datetime_col: str = DATETIME_COL, value_inrix: str = TT_COL,
            value_ref: str = TT_COL, confidence: float = 0.95,
            free_flow_percentile: float = 10.0,
            matched: pd.DataFrame | None = None) -> pd.DataFrame:
    """Agreement between INRIX and the reference, one row per route.

    Args:
        inrix / reference: binned, keyed frames (see :func:`match_bins`).
        matched: an already-matched frame from :func:`match_bins`, used instead of
            re-joining (so a report and this function agree by construction).
        confidence: CI level for the bias interval (default 0.95).
        free_flow_percentile: each source's own free-flow percentile for the delay
            comparison (default 10th).

    Returns:
        One row per route:

        - ``n_bins``, ``n_days``, ``first``, ``last`` — the evidence, in both units.
        - ``mean_inrix``, ``mean_ref``, ``bias`` (INRIX − reference; negative means
          INRIX reads faster), ``bias_ci_low`` / ``bias_ci_high`` (**day-blocked**),
          ``bias_ci_low_naive`` / ``bias_ci_high_naive`` (per-bin, too narrow — kept
          for contrast), ``ci_width_ratio``.
        - ``mae``, ``rmse``, ``mape`` (percent, over bins whose reference value is
          positive).
        - ``sd_inrix``, ``sd_ref``, ``sd_ratio``.
        - ``free_flow_inrix``, ``free_flow_ref``, ``free_flow_gap`` (INRIX −
          reference **at free flow**) with a day-blocked interval, and
          ``delay_slope`` / ``delay_intercept`` — the regression of INRIX delay on
          reference delay, each above its own free-flow, with a day-blocked CI on
          both (:func:`delay_regression`). These are the primary delay statistics
          (ROADMAP Item 32): the level gap and the compression are two different
          effects with opposite consequences for a before/after study, and
          ``bias`` alone fuses them.
        - ``ref_no_traffic``, ``ref_no_traffic_spread``, ``ref_free_flow_delay``,
          ``free_flow_gap_static`` — the level gap split into the reference's own
          residual delay at the free-flow percentile and what is left over
          (ROADMAP Item 33), present when the match carried the reference's extra
          travel time. ``free_flow_gap == free_flow_gap_static −
          ref_free_flow_delay`` by construction; a non-zero
          ``ref_free_flow_delay`` means the percentile the gap is quoted at is
          **not** free flow on the reference side.
        - ``delay_inrix``, ``delay_ref``, ``delay_ratio`` — mean delay above each
          source's own free-flow. Kept as a **secondary** statistic: it is a ratio
          of two small means, it carries no interval, and on a low-delay route it
          is unstable (VSL SB PM: ratio 2.32, slope 0.71 on the same bins). Item
          30's published numbers are stated in it, so it keeps its name and
          meaning rather than being redefined underneath them.
        - ``loa_low`` / ``loa_high`` — Bland-Altman 95% limits of agreement
          (mean difference ± 1.96 SD), the range a single bin's disagreement
          typically falls in.
        - ``r`` — Pearson correlation, reported last on purpose (G6).
        - ``imputed_fraction``, ``cvalue_kept_fraction``, ``short_fraction``,
          ``n_absent`` — INRIX-side coverage, present when the matched frame
          carries it (ROADMAP Item 31). A bias is only as good as what was summed
          to produce it: ``imputed_fraction`` is the share of member rows that
          were historical backfill rather than observation (0% through the
          daytime window on the arterials, 57% at 05:00 on rural Cascade-HSB), and
          ``n_absent`` is how many requested chain members the export never
          supplied. They belong beside the effect size for the same reason
          ``n_days`` does.

        ``attrs['matched']`` carries the per-bin frame; ``attrs['cvalue_threshold']``
        the gate the INRIX side was summed under, when one was applied.
    """
    m = matched if matched is not None else match_bins(
        inrix, reference, key=key, datetime_col=datetime_col,
        value_inrix=value_inrix, value_ref=value_ref)

    rows = []
    for route, grp in m.groupby(key, observed=True, sort=True):
        diff = grp[DIFF_COL]
        stamps = grp[datetime_col]
        day_means = diff.groupby(_day_key(stamps).to_numpy()).mean()

        ci_low, ci_high, n_days = _t_interval(day_means, confidence)
        nv_low, nv_high, _ = _t_interval(diff, confidence)
        width = (ci_high - ci_low) if np.isfinite(ci_high) else float("nan")
        nv_width = (nv_high - nv_low) if np.isfinite(nv_high) else float("nan")

        ref_pos = grp[grp[REF_COL] > 0]
        delay = _delay_block(grp, datetime_col, free_flow_percentile, confidence)
        sd_i = float(grp[INRIX_COL].std(ddof=1)) if len(grp) > 1 else float("nan")
        sd_r = float(grp[REF_COL].std(ddof=1)) if len(grp) > 1 else float("nan")
        sd_diff = float(diff.std(ddof=1)) if len(grp) > 1 else float("nan")

        rows.append({
            key: route,
            "n_bins": int(len(grp)),
            "n_days": int(n_days),
            "first": stamps.min(), "last": stamps.max(),
            "mean_inrix": float(grp[INRIX_COL].mean()),
            "mean_ref": float(grp[REF_COL].mean()),
            "bias": float(diff.mean()),
            "bias_ci_low": ci_low, "bias_ci_high": ci_high,
            "bias_ci_low_naive": nv_low, "bias_ci_high_naive": nv_high,
            "ci_width_ratio": (width / nv_width) if nv_width and np.isfinite(nv_width) else float("nan"),
            "mae": float(diff.abs().mean()),
            "rmse": float((diff ** 2).mean() ** 0.5),
            "mape": (float((ref_pos[DIFF_COL].abs() / ref_pos[REF_COL]).mean() * 100.0)
                     if len(ref_pos) else float("nan")),
            "sd_inrix": sd_i, "sd_ref": sd_r,
            "sd_ratio": (sd_i / sd_r) if sd_r else float("nan"),
            **delay,
            "loa_low": float(diff.mean() - 1.96 * sd_diff),
            "loa_high": float(diff.mean() + 1.96 * sd_diff),
            # Undefined against a constant series — report NaN rather than a
            # divide-by-zero artifact (and it is the last column for a reason).
            "r": (float(grp[INRIX_COL].corr(grp[REF_COL]))
                  if len(grp) > 1 and sd_i > 0 and sd_r > 0 else float("nan")),
            **_coverage_row(grp),
        })

    out = pd.DataFrame(rows)
    out.attrs = {"matched": m, "confidence": confidence,
                 "free_flow_percentile": free_flow_percentile,
                 "cvalue_threshold": (inrix.attrs.get("cvalue_threshold")
                                      if inrix is not None else None)}
    return out


_COVERAGE_MEANS = {"imputed_fraction": "imputed_fraction",
                   "cvalue_kept_fraction": "cvalue_kept_fraction",
                   "short": "short_fraction"}


def _coverage_row(grp: pd.DataFrame) -> dict:
    """The INRIX-side coverage columns :func:`match_bins` carried through, reduced
    to one number per route — omitted entirely when the matched frame has none,
    rather than reported as a reassuring NaN."""
    row = {}
    for src, name in _COVERAGE_MEANS.items():
        if src in grp.columns:
            row[name] = float(grp[src].mean())
    if "n_absent" in grp.columns:
        row["n_absent"] = float(grp["n_absent"].max())
    return row


# ---------------------------------------------------------------------------
# Independent-n accounting  (G5)
# ---------------------------------------------------------------------------
def independent_totals(matched: pd.DataFrame, chains: dict | None = None,
                       key: str = ROUTE_COL, datetime_col: str = DATETIME_COL) -> pd.DataFrame:
    """What a study-wide total is actually allowed to claim.

    Summing matched bins across routes double-counts: four Franklin sheets cover
    the same ~3 miles of road, and the VSL sheets are a sub-segment of Eagle Rd
    Full. This returns both the naive sum and the distinct counts beside it.

    Args:
        matched: the per-bin frame from :func:`match_bins`.
        chains: optional route -> ``corridors.ChainResult``, used to measure
            distinct **pavement** (union of segment ids, each counted once at its
            in-extent mileage).

    Returns:
        A one-row frame: ``n_routes``, ``n_bins_sum`` (the impressive number),
        ``n_bin_slots_distinct`` (distinct timestamps — the calendar can't supply
        more), ``route_days_sum``, ``days_distinct``, and with ``chains``:
        ``chain_miles_sum``, ``miles_distinct``, ``miles_overlap``,
        ``n_segments_distinct``. ``attrs['overlaps']`` lists the route pairs that
        share pavement.
    """
    stamps = matched[datetime_col]
    days = _day_key(stamps)
    per_route_days = matched.assign(_day=days).groupby(key, observed=True)["_day"].nunique()

    row = {
        "n_routes": int(matched[key].nunique()),
        "n_bins_sum": int(len(matched)),
        "n_bin_slots_distinct": int(stamps.nunique()),
        "route_days_sum": int(per_route_days.sum()),
        "days_distinct": int(days.nunique()),
    }
    overlaps = []
    if chains:
        miles, seg_sets, total = {}, {}, 0.0
        for route, chain in chains.items():
            ids = [int(s) for s in getattr(chain, "segment_ids", [])]
            extent = getattr(chain, "extent_miles", None) or getattr(chain, "miles", [])
            seg_sets[route] = set(ids)
            total += float(sum(extent))
            for sid, mi in zip(ids, extent):
                miles[sid] = max(miles.get(sid, 0.0), float(mi))
        names = sorted(seg_sets)
        for i, a in enumerate(names):
            for b in names[i + 1:]:
                shared = seg_sets[a] & seg_sets[b]
                if shared:
                    overlaps.append({"route_a": a, "route_b": b,
                                     "shared_segments": len(shared),
                                     "shared_miles": float(sum(miles[s] for s in shared))})
        row.update({
            "chain_miles_sum": total,
            "miles_distinct": float(sum(miles.values())),
            "miles_overlap": total - float(sum(miles.values())),
            "n_segments_distinct": len(miles),
        })
    out = pd.DataFrame([row])
    out.attrs = {"overlaps": pd.DataFrame(overlaps)}
    return out


# ---------------------------------------------------------------------------
# Profiles — the shapes a figure draws, computed here  (Item 30)
# ---------------------------------------------------------------------------
PROFILE_KEYS = ("time_of_day", "day_of_week", "date")

_DAY_NAMES = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday",
              "Saturday", "Sunday")


def profile(matched: pd.DataFrame, by="time_of_day", key: str = ROUTE_COL,
            datetime_col: str = DATETIME_COL, bin_minutes: int = 60) -> pd.DataFrame:
    """Group a matched frame and average both sources over each group.

    The shapes a validation figure draws — a diurnal profile, a day × hour bias
    grid, a daily bias series — are **means**, and a mean is a statistic. It is
    computed here so the plotting shell stays a shell (CLAUDE.md); ``gui`` consumes
    the frame this returns and adds nothing to it.

    Args:
        matched: the per-bin frame from :func:`match_bins`.
        by: one key or a sequence of them, from :data:`PROFILE_KEYS` —
            ``"time_of_day"`` (local clock floored to ``bin_minutes``),
            ``"day_of_week"``, ``"date"``.
        bin_minutes: width of the ``"time_of_day"`` bin (default hourly).

    Returns:
        ``[<key>, *group columns, n_bins, mean_inrix, mean_ref, bias]``, sorted in
        display order. ``"time_of_day"`` contributes both ``minute_of_day`` (int,
        for ordering an axis) and ``time_of_day`` (``"HH:MM"``); ``"day_of_week"``
        contributes an ordered categorical starting Monday.
    """
    keys = [by] if isinstance(by, str) else list(by)
    unknown = [k for k in keys if k not in PROFILE_KEYS]
    if unknown:
        raise ValueError(f"Unknown profile key(s) {unknown}; choose from {list(PROFILE_KEYS)}.")

    work = matched.copy()
    local = _local_naive(work[datetime_col])
    group_cols, sort_cols = [], []
    if "day_of_week" in keys:
        work["day_of_week"] = pd.Categorical(local.dt.day_name(), categories=_DAY_NAMES,
                                             ordered=True)
        group_cols.append("day_of_week")
        sort_cols.append("day_of_week")
    if "date" in keys:
        work["date"] = local.dt.normalize()
        group_cols.append("date")
        sort_cols.append("date")
    if "time_of_day" in keys:
        minute = (local.dt.hour * 60 + local.dt.minute) // int(bin_minutes) * int(bin_minutes)
        work["minute_of_day"] = minute.astype("int64")
        work["time_of_day"] = [f"{m // 60:02d}:{m % 60:02d}" for m in work["minute_of_day"]]
        group_cols += ["minute_of_day", "time_of_day"]
        sort_cols.append("minute_of_day")

    grouped = work.groupby([key, *group_cols], observed=True, dropna=False)
    out = grouped.agg(n_bins=(DIFF_COL, "size"),
                      mean_inrix=(INRIX_COL, "mean"),
                      mean_ref=(REF_COL, "mean"),
                      bias=(DIFF_COL, "mean")).reset_index()
    return out.sort_values([key, *sort_cols]).reset_index(drop=True)

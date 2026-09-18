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
- **Delay is compared on each source's own free-flow.** A ratio of "mean delay
  above this source's own 10th-percentile free-flow" is what the use case cares
  about, and it is what exposed the arterial delay compression (G1). The **SD
  ratio** sits beside it because it is symmetric — if the two ratios agree, the
  gap is not a regression-dilution artifact.
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
from .reference import ROUTE_COL, TT_COL

INRIX_COL = "INRIX Travel Time(Minutes)"
REF_COL = "Reference Travel Time(Minutes)"
DIFF_COL = "Difference(Minutes)"          # INRIX − reference
MEAN_COL = "Mean(Minutes)"                # Bland-Altman x-axis


# ---------------------------------------------------------------------------
# Matching
# ---------------------------------------------------------------------------
# INRIX-side per-bin coverage columns carried through the match, so the CValue
# gate's cost and the imputation share reach the scorecard instead of stopping at
# the corridor sum (ROADMAP Item 31).
CARRY_COLS = ("imputed_fraction", "cvalue_kept_fraction", "short", "n_absent")


def match_bins(inrix: pd.DataFrame, reference: pd.DataFrame, key: str = ROUTE_COL,
               datetime_col: str = DATETIME_COL, value_inrix: str = TT_COL,
               value_ref: str = TT_COL, carry: Sequence[str] = CARRY_COLS) -> pd.DataFrame:
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
        (mean per bin; booleans become the share of that bin that was flagged).

    Args:
        carry: INRIX-side per-bin columns to bring through the join
            (:data:`CARRY_COLS` by default — the CValue gate's cost, the
            imputation share, and the short-chain flags from
            ``corridors.chain_travel_time``). A column the INRIX frame does not
            have is skipped, not an error: this is diagnostic weight, and a frame
            built without it still matches.
    """
    ref = (reference.groupby([key, datetime_col], observed=True)[value_ref]
           .agg(["mean", "size"]).reset_index()
           .rename(columns={"mean": REF_COL, "size": "n_ref_samples"}))
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
        - ``free_flow_inrix``, ``free_flow_ref``, ``delay_inrix``, ``delay_ref``,
          ``delay_ratio`` — mean delay above each source's own free-flow.
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
        days = (stamps.dt.tz_localize(None) if getattr(stamps.dt, "tz", None) is not None
                else stamps).dt.normalize()
        day_means = diff.groupby(days.to_numpy()).mean()

        ci_low, ci_high, n_days = _t_interval(day_means, confidence)
        nv_low, nv_high, _ = _t_interval(diff, confidence)
        width = (ci_high - ci_low) if np.isfinite(ci_high) else float("nan")
        nv_width = (nv_high - nv_low) if np.isfinite(nv_high) else float("nan")

        ref_pos = grp[grp[REF_COL] > 0]
        ff_i, delay_i = _delay_above(grp[INRIX_COL], free_flow_percentile)
        ff_r, delay_r = _delay_above(grp[REF_COL], free_flow_percentile)
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
            "free_flow_inrix": ff_i, "free_flow_ref": ff_r,
            "delay_inrix": delay_i, "delay_ref": delay_r,
            "delay_ratio": (delay_i / delay_r) if delay_r else float("nan"),
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
    days = (stamps.dt.tz_localize(None) if getattr(stamps.dt, "tz", None) is not None
            else stamps).dt.normalize()
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


def _local_naive(stamps: pd.Series) -> pd.Series:
    """Local wall clock as naive timestamps — profiles are read in local time."""
    return (stamps.dt.tz_localize(None) if getattr(stamps.dt, "tz", None) is not None
            else stamps)


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

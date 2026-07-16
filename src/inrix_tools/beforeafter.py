"""Before/after intervention analysis.  (ROADMAP Item 4)

Two methods for "did travel time change between a before period and an after
period", kept deliberately distinct (the seed notebook blurred them):

- ``compare_periods`` — the **primary**, robust method. Strip daily/weekly
  seasonality with ``decompose`` (``decompose.py``), then compare the
  seasonally-adjusted values between the two periods per segment (and optionally
  per day-group x time-bin). Reports an **effect size + confidence interval**
  and the sample sizes — not just a p-value.
- ``ttest_baseline`` — the seed notebook's paired t-test, kept only as a
  labeled, interpretable **baseline**. See its docstring for the
  autocorrelation + multiple-comparisons caveats that are exactly why it is not
  the primary.

Both take ``before`` / ``after`` period specs (see ``parse_period``) and return
one tidy row per (segment [, by-group]).
"""
from __future__ import annotations

import math
from typing import Sequence

import pandas as pd

from .decompose import decompose_segments, seasonally_adjust
from .io import DATETIME_COL, SEGMENT_COL


# ---------------------------------------------------------------------------
# Period parsing
# ---------------------------------------------------------------------------
def parse_period(period, tz=None) -> tuple[pd.Timestamp, pd.Timestamp]:
    """Normalize a before/after period spec into a half-open ``[start, end)``
    tz-aware bound pair.

    Accepts either:
      - a 2-sequence ``(start, end)`` of anything ``pd.Timestamp`` accepts
        (``"2026-03-01"``, a ``datetime``, a ``Timestamp``), or
      - the seed's compact ``"YYYYMMDD-YYYYMMDD"`` string (e.g.
        ``"20251022-20251117"``).

    A **date-only** end bound is treated as inclusive of that whole calendar day
    (the returned exclusive end is midnight of the following day), so
    ``("2026-03-01", "2026-03-07")`` covers all of March 7th. ``tz`` localizes
    naive bounds (default: leave naive); tz-aware bounds are converted to it.
    """
    if isinstance(period, str):
        parts = period.split("-")
        if len(parts) != 2:
            raise ValueError(
                f"String period must be 'YYYYMMDD-YYYYMMDD', got {period!r}."
            )
        start_raw, end_raw = parts
    else:
        start_raw, end_raw = period

    def _bound(x):
        ts = pd.Timestamp(x)
        if tz is not None:
            ts = ts.tz_localize(tz) if ts.tzinfo is None else ts.tz_convert(tz)
        return ts

    start = _bound(start_raw)
    end = _bound(end_raw)
    if end == end.normalize():  # date-only (midnight) end -> whole calendar day
        end = end + pd.DateOffset(days=1)  # calendar day (DST-safe)
    if end <= start:
        raise ValueError(f"Period end {end} not after start {start}.")
    return start, end


def _period_mask(series: pd.Series, start: pd.Timestamp, end: pd.Timestamp) -> pd.Series:
    """Half-open ``[start, end)`` membership over a datetime Series."""
    return (series >= start) & (series < end)


# ---------------------------------------------------------------------------
# Effect size + CI on a difference of means (Welch, unequal variance)
# ---------------------------------------------------------------------------
def _compare_stats(before: pd.Series, after: pd.Series, confidence: float) -> dict:
    """Difference-in-means effect size with a Welch confidence interval and a
    (secondary) Welch t-test p-value. ``effect = mean(after) - mean(before)`` in
    the value's own units; positive => larger after."""
    from scipy import stats

    before = pd.Series(before).dropna()
    after = pd.Series(after).dropna()
    nb, na = len(before), len(after)
    mb, ma = before.mean(), after.mean()
    vb, va = before.var(ddof=1), after.var(ddof=1)
    diff = ma - mb

    se = math.sqrt(vb / nb + va / na)
    # Welch-Satterthwaite degrees of freedom.
    denom = (vb / nb) ** 2 / (nb - 1) + (va / na) ** 2 / (na - 1)
    dof = (vb / nb + va / na) ** 2 / denom if denom > 0 else float(nb + na - 2)
    tcrit = stats.t.ppf(1 - (1 - confidence) / 2, dof)
    ci_low, ci_high = diff - tcrit * se, diff + tcrit * se

    # Cohen's d (pooled SD) as a standardized effect size.
    pooled_sd = math.sqrt(((nb - 1) * vb + (na - 1) * va) / (nb + na - 2))
    cohens_d = diff / pooled_sd if pooled_sd > 0 else float("nan")

    t_stat, p_value = stats.ttest_ind(after, before, equal_var=False)

    return {
        "n_before": nb,
        "n_after": na,
        "mean_before": mb,
        "mean_after": ma,
        "sd_before": math.sqrt(vb),
        "sd_after": math.sqrt(va),
        "effect": diff,
        "ci_low": ci_low,
        "ci_high": ci_high,
        "cohens_d": cohens_d,
        "t_stat": float(t_stat),
        "p_value": float(p_value),
    }


# ---------------------------------------------------------------------------
# PRIMARY: decomposition-based before/after
# ---------------------------------------------------------------------------
def compare_periods(
    df: pd.DataFrame,
    before,
    after,
    value: str | None = None,
    by: Sequence[str] | None = None,
    use_decomposition: bool = True,
    confidence: float = 0.95,
    min_samples: int = 2,
    datetime_col: str = DATETIME_COL,
    segment_col: str = SEGMENT_COL,
    decompose_kwargs: dict | None = None,
) -> pd.DataFrame:
    """PRIMARY. Compare a before vs after period on **seasonally-adjusted** values,
    per segment (and optionally per day-group x time-bin), reporting effect size +
    confidence interval and n.

    Method: decompose the full series once (``decompose_segments``) to strip
    daily/weekly seasonality, take the seasonally-adjusted value
    (``value - season_day - season_week``, which retains the level so a genuine
    step change survives), split it into the before/after periods, and compute a
    difference-in-means effect size with a Welch CI. The p-value is reported as a
    secondary quantity, not the headline.

    Args:
        df: tz-aware local rows (post ``io.to_local``, CValue-filtered as desired).
            For a ``by`` grouping, the grouping columns (e.g. ``Day Group`` /
            ``Time Bin`` from ``timebins``) must already be assigned.
        before, after: period specs — see ``parse_period``. Note decomposition
            drops the first ``drop_days`` (default 7) of the series, so the before
            period should start at least that far in.
        value: metric column; defaults to the detected ``Travel Time(...)``.
        by: extra grouping columns besides the segment (e.g.
            ``['Day Group', 'Time Bin']``). Default: segment only.
        use_decomposition: ``True`` (default) compares seasonally-adjusted values;
            ``False`` compares the **raw** value (a labeled non-robust fallback /
            cross-check that skips the decomposition).
        confidence: CI level (default 0.95).
        min_samples: skip a group unless both periods have at least this many
            observations.
        decompose_kwargs: forwarded to ``decompose_segments`` when
            ``use_decomposition`` (e.g. ``freq_minutes``, ``rolling_window_days``).

    Returns:
        One row per (segment[, by-group]) with columns: the group keys,
        ``n_before`` / ``n_after``, ``mean_before`` / ``mean_after``, ``sd_before``
        / ``sd_after``, ``effect`` (mean after - before, in the value's units),
        ``ci_low`` / ``ci_high`` (Welch CI on the effect), ``cohens_d``,
        ``t_stat``, ``p_value``, and ``method`` (``"decomposition"`` or ``"raw"``).
        ``attrs`` records ``value``, ``confidence``, ``before``/``after`` bounds.
    """
    by = list(by) if by else []
    group_cols = [segment_col, *by]
    tz = df[datetime_col].dt.tz
    before_bounds = parse_period(before, tz)
    after_bounds = parse_period(after, tz)

    if use_decomposition:
        decomposed = decompose_segments(
            df,
            value=value,
            entity_grouping_columns=(segment_col,),
            datetime_col=datetime_col,
            keep_components=True,
            **(decompose_kwargs or {}),
        )
        value = decomposed.attrs["decompose_value"]
        work = decomposed.copy()
        work["_adj"] = seasonally_adjust(work, value).to_numpy()
        target = "_adj"
        method = "decomposition"
    else:
        from .decompose import default_value_column

        value = value or default_value_column(df)
        work = df
        target = value
        method = "raw"

    ts = work[datetime_col]
    in_before = _period_mask(ts, *before_bounds)
    in_after = _period_mask(ts, *after_bounds)

    rows = []
    for keys, g in work.groupby(group_cols, dropna=True, observed=True):
        keys = keys if isinstance(keys, tuple) else (keys,)
        b = g.loc[in_before.loc[g.index], target]
        a = g.loc[in_after.loc[g.index], target]
        if b.notna().sum() < min_samples or a.notna().sum() < min_samples:
            continue
        stat = _compare_stats(b, a, confidence)
        rows.append({**dict(zip(group_cols, keys)), **stat, "method": method})

    out = pd.DataFrame(rows)
    out.attrs = dict(df.attrs)
    out.attrs.update(
        value=value,
        confidence=confidence,
        before=tuple(str(x) for x in before_bounds),
        after=tuple(str(x) for x in after_bounds),
        method=method,
    )
    return out


# ---------------------------------------------------------------------------
# BASELINE: the seed notebook's paired t-test
# ---------------------------------------------------------------------------
def ttest_baseline(
    df: pd.DataFrame,
    before,
    after,
    value: str | None = None,
    by: Sequence[str] | None = None,
    tod_bin: str = "15min",
    datetime_col: str = DATETIME_COL,
    segment_col: str = SEGMENT_COL,
) -> pd.DataFrame:
    """BASELINE ONLY (interpretable, not robust). The seed notebook's paired
    t-test, ported from ``analyze_travel_time`` in ``_Plot Speed.ipynb``.

    For each (segment[, by-group]) it averages the value within each time-of-day
    bin (default 15-min) across the days of each period, aligns the before/after
    per-bin means, and runs a **paired** t-test across those bins
    (``scipy.stats.ttest_rel``). Pairing by time-of-day is a crude stand-in for
    the seasonal control that ``compare_periods`` does properly.

    Caveats — by design, this is why it is *not* the primary method:
      - 5-min INRIX samples are strongly **autocorrelated**, so even the
        bin-averaged series violates the t-test's independence assumption and the
        p-values are **overstated** (too significant).
      - Running it across many segments x groups is a **multiple-comparisons**
        problem; the raw p-values are not corrected.
    Prefer ``compare_periods``; keep this for continuity with the notebook and as
    an interpretable sanity check.

    Args:
        tod_bin: pandas offset alias for the time-of-day bin width (seed: 15min).
        by: extra grouping columns (e.g. ``['Group Label']`` or
            ``['Day Group', 'Time Bin']``).

    Returns:
        One row per (segment[, by-group]): the group keys, ``avg_difference``
        (mean of after-minus-before per-bin diffs, value units), ``t_stat``,
        ``p_value`` (see caveats), and ``n_bins`` (paired bins compared).
    """
    from scipy import stats

    from .decompose import default_value_column

    value = value or default_value_column(df)
    by = list(by) if by else []
    group_cols = [segment_col, *by]
    tz = df[datetime_col].dt.tz
    before_bounds = parse_period(before, tz)
    after_bounds = parse_period(after, tz)

    ts = df[datetime_col]
    work = df.copy()
    # time-of-day bin (floored local wall clock, as in the seed).
    work["_tod"] = ts.dt.floor(tod_bin).dt.time
    before_df = work[_period_mask(ts, *before_bounds)]
    after_df = work[_period_mask(ts, *after_bounds)]

    def _tod_means(frame):
        return frame.groupby([*group_cols, "_tod"], dropna=True, observed=True)[value].mean()

    # align before/after per-bin means on the shared (group..., time-of-day) index.
    aligned_all = pd.concat(
        {"before": _tod_means(before_df), "after": _tod_means(after_df)}, axis=1
    ).dropna()

    rows = []
    level = group_cols[0] if len(group_cols) == 1 else group_cols
    for keys, sub in aligned_all.groupby(level=level, observed=True):
        keys_t = keys if isinstance(keys, tuple) else (keys,)
        if len(sub) < 2:  # need >= 2 paired time-of-day bins for a paired t-test
            continue
        diffs = sub["after"] - sub["before"]
        t_stat, p_value = stats.ttest_rel(sub["after"], sub["before"])
        rows.append(
            {
                **dict(zip(group_cols, keys_t)),
                "avg_difference": diffs.mean(),
                "t_stat": float(t_stat),
                "p_value": float(p_value),
                "n_bins": len(diffs),
            }
        )

    out = pd.DataFrame(rows)
    out.attrs = dict(df.attrs)
    out.attrs.update(
        value=value,
        method="ttest_baseline",
        before=tuple(str(x) for x in before_bounds),
        after=tuple(str(x) for x in after_bounds),
    )
    return out

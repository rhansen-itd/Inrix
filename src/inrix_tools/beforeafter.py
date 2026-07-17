"""Before/after intervention analysis.  (ROADMAP Item 4)

Two methods for "did travel time change between a before period and an after
period", kept deliberately distinct (the seed notebook blurred them):

- ``compare_periods`` — the **primary**, robust method. Strip daily/weekly
  seasonality with ``decompose`` (``decompose.py``), then compare the
  seasonally-adjusted values between the two periods per segment (and optionally
  per day-group x time-bin). Reports an **effect size + confidence interval**
  and the sample sizes — not just a p-value. The comparison is made on **daily
  means** by default (``unit='day'``): 5-min samples are strongly
  autocorrelated, and treating them as independent shrinks the CI by roughly
  ``sqrt((1+rho)/(1-rho))`` — the Item 14 review measured null coverage of a
  nominal 95% CI at only 25–50% for traffic-realistic AR(1) rho, restored to
  ~96% by day-mean aggregation (REVIEW_ITEM14.md §4.1). A Benjamini–Hochberg
  ``q_value`` across the returned family handles the many-segments
  multiple-comparisons problem.
- ``ttest_baseline`` — the seed notebook's paired t-test, kept only as a
  labeled, interpretable **baseline**. See its docstring for the
  autocorrelation + multiple-comparisons caveats that are exactly why it is not
  the primary.

Both take ``before`` / ``after`` period specs (see ``parse_period``), **reject
overlapping periods**, and return one tidy row per (segment [, by-group]).

**Estimand caveat (profile-shape changes).** The seasonal components are
estimated from the whole series, both periods included, so an intervention that
reshapes the *daily profile* (e.g. only the PM peak moves) partially leaks into
``season_day`` and the measured effect understates the true one. When the
effect is expected to be time-of-day-specific, restrict the analysis window
first (``timebins.filter_time_window``, ROADMAP Item 9) and/or compare per
day-group x time-bin via ``by=['Day Group', 'Time Bin']``. Likewise, secular
drift between the periods (seasonal demand, fuel prices) is attributed to the
intervention — a difference-in-differences against a control corridor is the
fix when one exists (Future ROADMAP item).
"""
from __future__ import annotations

import math
import warnings
from typing import Sequence

import numpy as np
import pandas as pd

from .decompose import decompose_segments, seasonally_adjust
from .io import DATETIME_COL, SEGMENT_COL

# The seasonally-adjusted value column that ``adjust_for_periods`` attaches and
# ``compare_adjusted`` tests over (``value - season_day - season_week``; it
# retains the level so a genuine step change survives adjustment).
ADJUSTED_COL = "_adj"


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


def _check_disjoint(before_bounds, after_bounds) -> None:
    """Raise if the (half-open) before/after periods overlap — rows in the shared
    span would sit in *both* samples and bias every effect toward zero (the Item 14
    review found the GUI's old default windows doing exactly this)."""
    (b0, b1), (a0, a1) = before_bounds, after_bounds
    if b0 < a1 and a0 < b1:
        raise ValueError(
            f"Before period [{b0} .. {b1}) overlaps after period [{a0} .. {a1}); "
            "overlapping rows would appear in both samples and bias the effect "
            "toward zero. Choose disjoint periods."
        )


def check_periods(before, after, tz=None):
    """Parse and validate a before/after period pair, returning
    ``(before_bounds, after_bounds)`` (each a half-open ``[start, end)``).

    Raises on a bad end-before-start bound (via ``parse_period``) or on
    **overlapping** periods (via ``_check_disjoint``). Exposed publicly so a
    caller can reject invalid periods *before* paying for a decomposition — the
    GUI validates the date pickers this way so an overlapping selection fails
    fast instead of re-decomposing the whole export first.
    """
    before_bounds = parse_period(before, tz)
    after_bounds = parse_period(after, tz)
    _check_disjoint(before_bounds, after_bounds)
    return before_bounds, after_bounds


def _bh_qvalues(p) -> np.ndarray:
    """Benjamini–Hochberg step-up q-values for a family of p-values.

    ``q[i]`` is the smallest FDR level at which comparison ``i`` would be
    declared significant. NaN p-values (degenerate groups) are excluded from the
    family size and get NaN q. Monotone in p; clipped to [0, 1].
    """
    p = np.asarray(p, dtype=float)
    q = np.full(p.shape, np.nan)
    ok = np.isfinite(p)
    m = int(ok.sum())
    if m == 0:
        return q
    pv = p[ok]
    order = np.argsort(pv, kind="stable")
    ranked = pv[order] * m / np.arange(1, m + 1)
    stepped = np.minimum.accumulate(ranked[::-1])[::-1]  # enforce monotonicity
    qv = np.empty(m)
    qv[order] = np.clip(stepped, 0.0, 1.0)
    q[ok] = qv
    return q


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
    if se > 0:
        # Welch-Satterthwaite degrees of freedom.
        denom = (vb / nb) ** 2 / (nb - 1) + (va / na) ** 2 / (na - 1)
        dof = (vb / nb + va / na) ** 2 / denom if denom > 0 else float(nb + na - 2)
        tcrit = stats.t.ppf(1 - (1 - confidence) / 2, dof)
        ci_low, ci_high = diff - tcrit * se, diff + tcrit * se
        t_stat, p_value = (float(x) for x in stats.ttest_ind(after, before, equal_var=False))
    else:
        # Degenerate variance (both periods constant — e.g. two heavily-quantized
        # days): a width-0 CI and p=0 would overstate certainty as absolute, so
        # report the interval and p-value as undefined instead.
        ci_low = ci_high = t_stat = p_value = float("nan")

    # Cohen's d (pooled SD) as a standardized effect size.
    pooled_sd = math.sqrt(((nb - 1) * vb + (na - 1) * va) / (nb + na - 2))
    cohens_d = diff / pooled_sd if pooled_sd > 0 else float("nan")

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
    unit: str = "day",
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
    step change survives), split it into the before/after periods, **aggregate
    each period to daily means** (``unit='day'``), and compute a
    difference-in-means effect size with a Welch CI across those days. The
    p-value is reported as a secondary quantity, not the headline; a
    Benjamini–Hochberg ``q_value`` across all returned rows is the
    multiple-comparisons-aware version.

    Why days: 5-min samples are strongly autocorrelated, so a CI computed over
    raw samples is drastically too narrow — simulated null coverage of a nominal
    95% CI is 25–50% at traffic-realistic AR(1) rho, vs ~96% for daily means
    (REVIEW_ITEM14.md §4.1). A day is the honest unit of evidence here.

    See the module docstring for the estimand caveats (profile-shape changes
    partially absorb into ``season_day`` — use ``timebins.filter_time_window``
    and/or ``by=['Day Group', 'Time Bin']`` when the effect is
    time-of-day-specific; secular drift needs a difference-in-differences).

    Args:
        df: tz-aware local rows (post ``io.to_local``, CValue-filtered as desired).
            For a ``by`` grouping, the grouping columns (e.g. ``Day Group`` /
            ``Time Bin`` from ``timebins``) must already be assigned.
        before, after: period specs — see ``parse_period``. Must be **disjoint**
            (overlap raises). Note decomposition drops the first ``drop_days``
            (default 7) of the series while the rolling window warms up; a period
            reaching into that warm-up is flagged on ``attrs['warnings']`` and the
            effective day counts are recorded (see Returns).
        value: metric column; defaults to the detected ``Travel Time(...)``.
        by: extra grouping columns besides the segment (e.g.
            ``['Day Group', 'Time Bin']``). Default: segment only.
        use_decomposition: ``True`` (default) compares seasonally-adjusted values;
            ``False`` compares the **raw** value (a labeled non-robust fallback /
            cross-check that skips the decomposition).
        unit: ``'day'`` (default) aggregates each group's values to **daily means**
            (local calendar days) before the test, so n counts days.
            ``'sample'`` is the pre-Item-15 behavior — Welch straight over the
            5-min samples — kept only as an escape hatch for exploration; its CI
            ignores autocorrelation and is **not robust** (see above).
        confidence: CI level (default 0.95).
        min_samples: skip a group unless both periods have at least this many
            observations **in the chosen unit** (days by default).
        decompose_kwargs: forwarded to ``decompose_segments`` when
            ``use_decomposition`` (e.g. ``freq_minutes``, ``rolling_window_days``).

    Returns:
        One row per (segment[, by-group]) with columns: the group keys,
        ``n_before`` / ``n_after`` (observations entering the test, in ``unit`` —
        **days** by default), ``n_samples_before`` / ``n_samples_after`` (the
        underlying 5-min sample counts), ``mean_before`` / ``mean_after``,
        ``sd_before`` / ``sd_after`` (in ``unit``), ``effect`` (mean after -
        before, in the value's units), ``ci_low`` / ``ci_high`` (Welch CI on the
        effect), ``cohens_d``, ``t_stat``, ``p_value``, ``q_value``
        (Benjamini–Hochberg across all returned rows), ``before_days_effective`` /
        ``after_days_effective`` (this segment's own effective span — shorter than
        the export-wide figure for a segment that starts late; see below), and
        ``method`` (``"decomposition"`` or ``"raw"``). ``attrs`` records
        ``value``, ``confidence``, ``unit``, the ``before``/``after`` bounds, the
        **export-wide** effective day counts ``before_days_effective`` /
        ``after_days_effective`` (from the global series start, after warm-up
        clipping — the per-row columns refine these per segment), and ``warnings``
        (a list, present when a period was truncated by the decomposition warm-up
        or a segment begins after the export start).
    """
    if unit not in ("day", "sample"):
        raise ValueError(f"unit must be 'day' or 'sample', got {unit!r}.")

    if use_decomposition:
        adjusted = adjust_for_periods(
            df, value=value, datetime_col=datetime_col, segment_col=segment_col,
            decompose_kwargs=decompose_kwargs,
        )
        return compare_adjusted(
            adjusted, before, after, by=by, unit=unit, confidence=confidence,
            min_samples=min_samples, datetime_col=datetime_col, segment_col=segment_col,
        )

    # Raw (non-robust) cross-check: no decomposition, no warm-up drop.
    from .decompose import default_value_column

    value = value or default_value_column(df)
    return _compare_core(
        df, target=value, value=value, method="raw", before=before, after=after,
        by=by, unit=unit, confidence=confidence, min_samples=min_samples,
        datetime_col=datetime_col, segment_col=segment_col,
        series_start=(df[datetime_col].min() if len(df) else None), drop_days=None,
    )


# ---------------------------------------------------------------------------
# The two halves of the decomposition path, exposed so an interactive caller
# can cache the expensive one. ``adjust_for_periods`` decomposes the full export
# (the ~8 s cost) independently of the before/after dates; ``compare_adjusted``
# then computes the per-period Welch stats off that frame in sub-second time, so
# moving a date picker no longer re-decomposes (Item 14 review O1 / Item 16).
# ---------------------------------------------------------------------------
def adjust_for_periods(
    df: pd.DataFrame,
    value: str | None = None,
    *,
    datetime_col: str = DATETIME_COL,
    segment_col: str = SEGMENT_COL,
    decompose_kwargs: dict | None = None,
) -> pd.DataFrame:
    """Decompose the full series once and attach its seasonally-adjusted value —
    the **expensive, period-independent** half of ``compare_periods``.

    Returns the decomposed frame (trend/season/resid components kept, plus the
    original ``value`` column) with an extra ``ADJUSTED_COL`` (``_adj``) column
    holding ``value - season_day - season_week`` (retains the level so a step
    change survives). ``attrs`` carries ``decompose_value`` (the resolved metric
    column), ``series_start`` (the first timestamp *before* the warm-up drop —
    ``compare_adjusted`` needs it to flag warm-up truncation), and ``drop_days``
    (the warm-up length). Filter the frame (e.g. ``timebins.filter_time_window``)
    before calling if a time-of-day window is wanted — the decomposition then
    describes that window.
    """
    decompose_kwargs = decompose_kwargs or {}
    series_start = df[datetime_col].min() if len(df) else None
    decomposed = decompose_segments(
        df,
        value=value,
        entity_grouping_columns=(segment_col,),
        datetime_col=datetime_col,
        keep_components=True,
        **decompose_kwargs,
    )
    resolved = decomposed.attrs["decompose_value"]
    work = decomposed.copy()
    work[ADJUSTED_COL] = seasonally_adjust(work, resolved).to_numpy()
    work.attrs = dict(decomposed.attrs)
    work.attrs["decompose_value"] = resolved
    # Store as a string: the adjusted frame is fed back through ibis/duckdb by
    # changepoint detection, which can't serialize a Timestamp in attrs.
    work.attrs["series_start"] = None if series_start is None else str(series_start)
    work.attrs["drop_days"] = int(decompose_kwargs.get("drop_days", 7))
    return work


def compare_adjusted(
    adjusted: pd.DataFrame,
    before,
    after,
    *,
    by: Sequence[str] | None = None,
    unit: str = "day",
    confidence: float = 0.95,
    min_samples: int = 2,
    datetime_col: str = DATETIME_COL,
    segment_col: str = SEGMENT_COL,
) -> pd.DataFrame:
    """Compare a before vs after period on an already seasonally-adjusted frame
    (from ``adjust_for_periods``) — the **cheap, period-dependent** half of
    ``compare_periods``. Same output contract and ``method='decomposition'``; see
    ``compare_periods`` for the column/attrs documentation.
    """
    if unit not in ("day", "sample"):
        raise ValueError(f"unit must be 'day' or 'sample', got {unit!r}.")
    return _compare_core(
        adjusted, target=ADJUSTED_COL, value=adjusted.attrs.get("decompose_value"),
        method="decomposition", before=before, after=after, by=by, unit=unit,
        confidence=confidence, min_samples=min_samples, datetime_col=datetime_col,
        segment_col=segment_col, series_start=adjusted.attrs.get("series_start"),
        drop_days=adjusted.attrs.get("drop_days", 7),
    )


def _compare_core(
    work: pd.DataFrame,
    *,
    target: str,
    value: str | None,
    method: str,
    before,
    after,
    by: Sequence[str] | None,
    unit: str,
    confidence: float,
    min_samples: int,
    datetime_col: str,
    segment_col: str,
    series_start,
    drop_days: int | None,
) -> pd.DataFrame:
    """Shared before/after comparison over a prepared frame: validate the
    periods, flag warm-up truncation, split ``target`` into the two periods,
    optionally aggregate to daily means, and Welch-compare per (segment[, by]).

    ``drop_days`` ``None`` means "no decomposition warm-up" (the raw path);
    otherwise ``series_start + drop_days`` is the first usable timestamp and any
    period reaching before it is truncated + flagged.
    """
    by = list(by) if by else []
    group_cols = [segment_col, *by]
    tz = work[datetime_col].dt.tz
    before_bounds, after_bounds = check_periods(before, after, tz)

    # Effective spans: decomposition drops the leading ``drop_days`` while its
    # rolling window warms up, so a period reaching into that warm-up silently
    # holds fewer days than requested — flag it rather than let the user read an
    # effect off less evidence than they think they have.
    def _wall_days(start, end) -> float:
        # calendar days on the local wall clock (a DST-crossing period still
        # counts whole days, not 23/25-hour fractions)
        naive = [t.tz_localize(None) if t.tzinfo is not None else t for t in (start, end)]
        return (naive[1] - naive[0]) / pd.Timedelta(1, "D")

    notes: list[str] = []
    effective_days = {}
    warmup_end = None
    if drop_days is not None and series_start is not None:
        # series_start may arrive as a Timestamp (raw path) or an ISO string
        # (adjust_for_periods stores it as text for ibis-serializable attrs).
        warmup_end = pd.Timestamp(series_start) + pd.Timedelta(drop_days, "D")
    for name, (start, end) in (("before", before_bounds), ("after", after_bounds)):
        eff_start = max(start, warmup_end) if warmup_end is not None else start
        effective_days[name] = max(0.0, _wall_days(eff_start, end))
        if warmup_end is not None and eff_start > start:
            requested = _wall_days(start, end)
            notes.append(
                f"{name} period truncated by the decomposition warm-up "
                f"({drop_days}-day rolling window): effective start {eff_start} — "
                f"{effective_days[name]:g} of the requested {requested:g} days used."
            )

    ts = work[datetime_col]
    in_before = _period_mask(ts, *before_bounds)
    in_after = _period_mask(ts, *after_bounds)
    dates = ts.dt.date  # local calendar day, for unit='day' aggregation

    rows = []
    late_start = 0  # segments beginning after the global effective start (F7)
    for keys, g in work.groupby(group_cols, dropna=True, observed=True):
        keys = keys if isinstance(keys, tuple) else (keys,)
        b = g.loc[in_before.loc[g.index], target]
        a = g.loc[in_after.loc[g.index], target]
        n_samples = {"n_samples_before": int(b.notna().sum()),
                     "n_samples_after": int(a.notna().sum())}
        if unit == "day":
            b = b.groupby(dates.loc[b.index]).mean()
            a = a.groupby(dates.loc[a.index]).mean()
        if b.notna().sum() < min_samples or a.notna().sum() < min_samples:
            continue
        stat = _compare_stats(b, a, confidence)
        # Per-group effective days. The warm-up ``drop_days`` is applied *per
        # entity* by the decomposition, and a segment may simply begin later than
        # the export (added sensor, staged export), so its own usable data can
        # start after the global ``series_start + drop_days`` — the scalar
        # ``*_days_effective`` attrs (computed from the global start) then overstate
        # this segment's evidence. The group's earliest surviving timestamp already
        # reflects any per-entity warm-up drop, so use it as this segment's start.
        g_start = g[datetime_col].min()
        eff = {
            f"{name}_days_effective": max(0.0, _wall_days(max(start, g_start), end))
            for name, (start, end) in (("before", before_bounds), ("after", after_bounds))
        }
        if eff["before_days_effective"] < effective_days["before"] - 1e-9:
            late_start += 1
        rows.append({**dict(zip(group_cols, keys)), **stat, **n_samples,
                     **eff, "method": method})

    if late_start:
        notes.append(
            f"{late_start} segment(s) begin after the export start (a late-starting "
            "sensor or staged export); their per-row before_days_effective is shorter "
            "than the export-wide figure — read each row's own *_days_effective."
        )
    for msg in notes:
        warnings.warn(msg, UserWarning, stacklevel=3)

    out = pd.DataFrame(rows)
    if len(out):
        out["q_value"] = _bh_qvalues(out["p_value"])
    out.attrs = dict(work.attrs)
    out.attrs.update(
        value=value,
        confidence=confidence,
        unit=unit,
        before=tuple(str(x) for x in before_bounds),
        after=tuple(str(x) for x in after_bounds),
        before_days_effective=effective_days["before"],
        after_days_effective=effective_days["after"],
        method=method,
    )
    if notes:
        out.attrs["warnings"] = notes
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
    before_bounds, after_bounds = check_periods(before, after, tz)

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

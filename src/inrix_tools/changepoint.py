"""Thin adapter over ``traffic_anomaly.changepoint`` for INRIX data.  (ROADMAP Item 5)

Locates *when* a persistent shift occurred without hand-specifying the
before/after boundary — the complement to ``beforeafter.py`` (which needs the
boundary given). Use it to find or confirm intervention dates: a retiming, a
construction start, a signal change.

``traffic_anomaly`` is a *dependency*, not vendored (MIT, PyPI, CI-tested — see
CLAUDE.md / DESIGN_HISTORY Session 0). This module only maps INRIX column names
and defaults onto it; it copies **no** upstream source.

Input is expected to have been through ``io.load_data`` (+ usually
``io.to_local`` / ``io.filter_cvalue``): a tz-aware ``Date Time``, an int
``Segment ID``, and a numeric value column. tz-aware timestamps pass through
``traffic_anomaly`` unchanged (verified — the tz is retained on output).

Run it on the **seasonally-adjusted** series for the cleanest result — a strong
daily/weekly cycle can otherwise register as spurious changepoints. Decompose
first (``decompose.decompose_segments`` + ``decompose.seasonally_adjust``) and
pass that column as ``value=``; on raw travel time the seasonality is still in
the signal.
"""
from __future__ import annotations

from typing import Sequence

import pandas as pd

from .io import DATETIME_COL, SEGMENT_COL

# Columns produced by traffic_anomaly.changepoint (one row per detected shift).
SCORE_COL = "score"
AVG_BEFORE_COL = "avg_before"
AVG_AFTER_COL = "avg_after"
AVG_DIFF_COL = "avg_diff"
PCT_CHANGE_COL = "pct_change"
_RESULT_COLS = (SCORE_COL, AVG_BEFORE_COL, AVG_AFTER_COL, AVG_DIFF_COL, PCT_CHANGE_COL)


def detect_changepoints(
    df: pd.DataFrame,
    value: str | None = None,
    datetime_col: str = DATETIME_COL,
    entity_grouping_column: str = SEGMENT_COL,
    rolling_window_days: int = 14,
    score_threshold: float = 5.0,
    **changepoint_kwargs,
) -> pd.DataFrame:
    """Detect persistent level shifts per segment via ``traffic_anomaly.changepoint``.

    A thin wrapper with INRIX defaults. See the module docstring on running it on
    the seasonally-adjusted series rather than raw travel time.

    Args:
        df: tz-aware local rows (see module docstring). Must contain
            ``datetime_col``, ``entity_grouping_column``, and ``value``.
        value: the metric column to scan. Defaults to the detected
            ``Travel Time(...)`` column (then ``Speed(...)``) — see
            ``decompose.default_value_column``. Pass a seasonally-adjusted column
            here for a cleaner scan.
        datetime_col: the timestamp column (INRIX ``Date Time``).
        entity_grouping_column: the per-entity key (default ``Segment ID``).
        rolling_window_days: window compared before vs after each candidate point.
        score_threshold: minimum score to report a changepoint (higher = stricter).
        **changepoint_kwargs: forwarded to ``traffic_anomaly.changepoint``
            (e.g. ``min_separation_days``, ``min_samples``, ``robust``,
            ``upper_bound`` / ``lower_bound``, ``recent_days_for_validation``).

    Returns:
        One row per detected changepoint: ``[entity_grouping_column, datetime_col,
        score, avg_before, avg_after, avg_diff, pct_change]``. ``datetime_col`` is
        *when* the shift is detected; ``avg_diff`` is the after-minus-before level
        change (value units), ``pct_change`` its fraction. Segments with no
        changepoint above ``score_threshold`` do not appear. tz and ``df.attrs``
        (units, tz, cvalue threshold) are propagated.
    """
    from traffic_anomaly import changepoint  # local import: heavy (ibis/duckdb) dep

    from .decompose import default_value_column

    if value is None:
        value = default_value_column(df)

    out = changepoint(
        df,
        datetime_column=datetime_col,
        value_column=value,
        entity_grouping_column=entity_grouping_column,
        rolling_window_days=rolling_window_days,
        score_threshold=score_threshold,
        **changepoint_kwargs,
    )
    out.attrs = dict(df.attrs)
    out.attrs["changepoint_value"] = value
    return out


def changepoints_near(
    changepoints: pd.DataFrame,
    known_dates,
    window_days: float = 7,
    datetime_col: str = DATETIME_COL,
    entity_grouping_column: str = SEGMENT_COL,
) -> pd.DataFrame:
    """Relate detected changepoints to known intervention dates — did a shift line
    up with a retiming / construction / signal-change date?

    For each (segment, known date) pair it finds that segment's **nearest**
    detected changepoint and reports how far off it is. The match is always
    reported (nearest wins) with a ``within_window`` flag, so a segment that
    shifted nowhere near the date is visible as ``within_window=False`` rather
    than silently dropped.

    Args:
        changepoints: the output of ``detect_changepoints``.
        known_dates: a single date-like or an iterable of them (intervention
            dates). Naive dates are localized to the changepoints' timezone.
        window_days: a changepoint counts as matching a known date if it falls
            within +/- this many days of it.

    Returns:
        One row per (segment, known date): ``[entity_grouping_column, known_date,
        datetime_col (the nearest changepoint's time), days_off (signed, changepoint
        minus known date), score, avg_diff, within_window]``, sorted by segment
        then known date. Empty (with the right columns) if ``changepoints`` is
        empty.
    """
    cols = [
        entity_grouping_column,
        "known_date",
        datetime_col,
        "days_off",
        SCORE_COL,
        AVG_DIFF_COL,
        "within_window",
    ]
    if changepoints.empty:
        return pd.DataFrame(columns=cols)

    if isinstance(known_dates, (str, pd.Timestamp)) or not isinstance(
        known_dates, (Sequence, pd.Index, pd.Series)
    ):
        known_dates = [known_dates]

    tz = changepoints[datetime_col].dt.tz

    def _localize(x):
        ts = pd.Timestamp(x)
        if tz is not None:
            ts = ts.tz_localize(tz) if ts.tzinfo is None else ts.tz_convert(tz)
        return ts

    known = [_localize(d) for d in known_dates]

    rows = []
    for entity, grp in changepoints.groupby(entity_grouping_column, observed=True):
        for kd in known:
            deltas = grp[datetime_col] - kd
            i = deltas.abs().idxmin()
            days_off = deltas.loc[i].total_seconds() / 86400.0
            rows.append(
                {
                    entity_grouping_column: entity,
                    "known_date": kd,
                    datetime_col: grp.loc[i, datetime_col],
                    "days_off": days_off,
                    SCORE_COL: grp.loc[i, SCORE_COL],
                    AVG_DIFF_COL: grp.loc[i, AVG_DIFF_COL],
                    "within_window": abs(days_off) <= window_days,
                }
            )

    out = pd.DataFrame(rows, columns=cols).sort_values(
        [entity_grouping_column, "known_date"]
    ).reset_index(drop=True)
    out.attrs = dict(changepoints.attrs)
    return out

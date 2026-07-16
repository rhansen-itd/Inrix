"""Thin adapter over ``traffic_anomaly.decompose`` for INRIX data.  (ROADMAP Item 4)

Maps INRIX column names onto the ``traffic_anomaly`` schema and sets
INRIX-appropriate defaults (``freq_minutes=5``,
``entity_grouping_columns=['Segment ID']``). The result is a robust
decomposition of each segment's series into a rolling-median trend, daily and
weekly seasonal components, and a residual — the foundation for the
decomposition-based before/after comparison in ``beforeafter.py``.

``traffic_anomaly`` is a *dependency*, not vendored (MIT, PyPI, CI-tested — see
CLAUDE.md / DESIGN_HISTORY Session 0). This module only translates column names
and defaults; it copies **no** upstream source. If one upstream function ever
needs tweaking, copy *that* function here with its MIT attribution — never the
package.

Input is expected to have been through ``io.load_data`` (and usually
``io.to_local`` + ``io.filter_cvalue``): a tz-aware ``Date Time``, an int
``Segment ID``, and a numeric value column. tz-aware timestamps pass through
``traffic_anomaly`` unchanged (verified — the tz is retained on output).
"""
from __future__ import annotations

from typing import Sequence

import pandas as pd

from .io import DATETIME_COL, SEGMENT_COL
from .speed import metric_columns

# Component columns produced by traffic_anomaly.decompose (drop_extras=False).
TREND_COL = "median"          # rolling-median trend / level
SEASON_DAY_COL = "season_day"  # daily seasonal component
SEASON_WEEK_COL = "season_week"  # weekly seasonal component
RESID_COL = "resid"           # residual (value - prediction)
PREDICTION_COL = "prediction"  # median + season_day + season_week (>= 0)
_COMPONENT_COLS = (TREND_COL, SEASON_DAY_COL, SEASON_WEEK_COL, RESID_COL, PREDICTION_COL)


def default_value_column(df: pd.DataFrame) -> str:
    """The default metric to decompose: the ``Travel Time(...)`` column if present
    (the primary intervention metric), else the ``Speed(...)`` column. Detected by
    prefix so the unit (min vs mph, kmh) isn't hard-coded (see DATA_FORMAT.md)."""
    m = metric_columns(df)
    value = m["travel_time"] or m["speed"]
    if value is None:
        raise ValueError(
            "No 'Travel Time(...)' or 'Speed(...)' column found; pass value= explicitly."
        )
    return value


# traffic_anomaly's default rolling-window sample guard (96 samples/day * 5),
# sized for a *full* day. See ``_auto_min_rolling_samples``.
_UPSTREAM_MIN_ROLLING_SAMPLES = 96 * 5


def _auto_min_rolling_samples(
    df: pd.DataFrame, datetime_col: str, freq_minutes: int
) -> int:
    """Scale ``traffic_anomaly``'s ``min_rolling_window_samples`` guard to the
    data's actual time-of-day coverage.

    That guard drops any row whose preceding rolling window holds fewer than N
    samples; the upstream default (``480 = 96*5``) assumes a **whole day** is
    present. A time-of-day-filtered series (e.g. only 4–6PM via
    ``timebins.filter_time_window``) has far fewer samples per window and would be
    filtered out **entirely** — an empty decomposition. Scaling by the fraction of
    the day's freq-grid slots that actually appear fixes that: full-day data is
    unchanged (ratio == 1 → 480), while a 2-hour window relaxes to ~40 so it still
    decomposes. This is exactly what lets "decompose the PM peak only" work without
    breaking the changepoint/decomposition path.
    """
    full_day_slots = max(1, round(1440 / freq_minutes))
    observed = int(df[datetime_col].dt.floor(f"{freq_minutes}min").dt.time.nunique())
    ratio = min(1.0, observed / full_day_slots)
    return max(1, round(_UPSTREAM_MIN_ROLLING_SAMPLES * ratio))


def decompose_segments(
    df: pd.DataFrame,
    value: str | None = None,
    freq_minutes: int = 5,
    entity_grouping_columns: Sequence[str] = (SEGMENT_COL,),
    datetime_col: str = DATETIME_COL,
    rolling_window_days: int = 7,
    drop_days: int = 7,
    keep_components: bool = True,
    min_rolling_window_samples: int | None = None,
    **decompose_kwargs,
) -> pd.DataFrame:
    """Decompose each segment's series into trend / daily+weekly season / residual.

    A thin wrapper over ``traffic_anomaly.decompose`` with INRIX defaults.

    Args:
        df: tz-aware local rows (see module docstring). Must contain
            ``datetime_col``, the ``entity_grouping_columns``, and ``value``.
        value: the metric column to decompose. Defaults to the detected
            ``Travel Time(...)`` column (then ``Speed(...)``) — see
            ``default_value_column``.
        freq_minutes: sampling frequency of the series (INRIX 5-min default).
        entity_grouping_columns: the per-entity key(s); default ``['Segment ID']``.
        datetime_col: the timestamp column (INRIX ``Date Time``).
        rolling_window_days: window for the rolling-median trend.
        drop_days: leading days dropped while the rolling window warms up
            (``traffic_anomaly`` default). Note the first ``drop_days`` of the
            series carry no output — size the before period accordingly.
        keep_components: keep the trend/season columns (``drop_extras=False``).
            When ``False`` only ``resid``/``prediction`` are returned.
        min_rolling_window_samples: minimum samples a row's rolling window must
            hold to survive (``traffic_anomaly`` drops the rest). ``None``
            (default) **auto-scales** it to the data's time-of-day coverage via
            ``_auto_min_rolling_samples`` — 480 for a full day, proportionally
            less for a time-of-day-filtered series so a windowed series (e.g. only
            4–6PM) still decomposes instead of coming back empty. The value used is
            recorded on ``attrs['min_rolling_window_samples']``.
        **decompose_kwargs: forwarded to ``traffic_anomaly.decompose``
            (e.g. ``min_rolling_window_samples``, ``rolling_window_enable``,
            ``min_time_of_day_samples``).

    Returns:
        The decomposed DataFrame: the entity columns, ``datetime_col``, the
        original ``value``, any other passthrough columns (e.g. day-group /
        time-bin labels are carried through unchanged), and the component
        columns ``median`` / ``season_day`` / ``season_week`` / ``resid`` /
        ``prediction`` (``median``/``season_*`` present only when
        ``keep_components``). ``df.attrs`` (units, tz, cvalue threshold) is
        propagated. Rows with insufficient rolling-window samples are dropped by
        ``traffic_anomaly``.
    """
    from traffic_anomaly import decompose  # local import: heavy (ibis/duckdb) dep

    if value is None:
        value = default_value_column(df)

    if min_rolling_window_samples is None:
        min_rolling_window_samples = _auto_min_rolling_samples(
            df, datetime_col, freq_minutes
        )

    out = decompose(
        df,
        datetime_column=datetime_col,
        value_column=value,
        entity_grouping_columns=list(entity_grouping_columns),
        freq_minutes=freq_minutes,
        rolling_window_days=rolling_window_days,
        drop_days=drop_days,
        drop_extras=not keep_components,
        min_rolling_window_samples=min_rolling_window_samples,
        **decompose_kwargs,
    )
    out.attrs = dict(df.attrs)
    out.attrs["decompose_value"] = value
    out.attrs["min_rolling_window_samples"] = min_rolling_window_samples
    return out


def seasonally_adjust(
    decomposed: pd.DataFrame,
    value: str | None = None,
) -> pd.Series:
    """Seasonally-adjusted value = ``value - season_day - season_week`` (identically
    ``median + resid``): daily/weekly seasonality removed, but the trend/level
    **retained** so a genuine intervention step change survives. This is what the
    before/after comparison compares between periods, rather than the raw residual
    (whose rolling median partly absorbs a persistent shift).

    Args:
        decomposed: output of ``decompose_segments`` with ``keep_components=True``.
        value: the original value column. Defaults to ``attrs['decompose_value']``.
    """
    if value is None:
        value = decomposed.attrs.get("decompose_value")
    if value is None or value not in decomposed.columns:
        raise ValueError(
            "Cannot find the value column; pass value= (or use a frame from "
            "decompose_segments, which records attrs['decompose_value'])."
        )
    for c in (SEASON_DAY_COL, SEASON_WEEK_COL):
        if c not in decomposed.columns:
            raise ValueError(
                f"{c!r} missing — decompose with keep_components=True first."
            )
    adj = decomposed[value] - decomposed[SEASON_DAY_COL] - decomposed[SEASON_WEEK_COL]
    adj.name = f"{value} (seasonally adjusted)"
    return adj

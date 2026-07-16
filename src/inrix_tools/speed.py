"""Speed / travel-time aggregation — compute only, no plotting.  (ROADMAP Item 3)

Splits the seed notebook's ``process_and_plot_*`` functions into their **compute
half**. Each returns a typed DataFrame; the caller (GUI / a notebook) turns it
into a figure. Undoing that compute+plot fusion is the whole point of this module
(see CLAUDE.md) — there are deliberately **no plotting imports here**.

Inputs are expected to have been through ``io.to_local`` (tz-aware local
``Date Time``, CValue-filtered as desired) and, for the grouped summaries, through
``timebins.assign_day_group`` / ``assign_time_bins`` (/ ``assign_group_label``).
Rows with an unassigned (NA) day-group or time-bin drop out of the grouped
summaries, matching the seed's ``dropna(subset=['Group'])``.
"""
from __future__ import annotations

from typing import Iterable, Sequence

import pandas as pd

from .io import CORRIDOR_COL, DATETIME_COL, SEGMENT_COL, mark_complete_timestamps
from .timebins import DAY_GROUP_COL, GROUP_LABEL_COL, TIME_BIN_COL

# aggregation statistics reported for every value column (pandas names).
_STATS = ("count", "mean", "std", "median")


# ---------------------------------------------------------------------------
# Column discovery
# ---------------------------------------------------------------------------
def metric_columns(df: pd.DataFrame) -> dict[str, str | None]:
    """Locate the ``Speed(...)`` and ``Travel Time(...)`` columns by prefix, so
    aggregation doesn't hard-code the unit (mph vs kmh — see DATA_FORMAT.md)."""
    speed = next((c for c in df.columns if c.startswith("Speed(")), None)
    tt = next((c for c in df.columns if c.startswith("Travel Time(")), None)
    return {"speed": speed, "travel_time": tt}


def _default_values(df: pd.DataFrame) -> list[str]:
    m = metric_columns(df)
    return [c for c in (m["speed"], m["travel_time"]) if c is not None]


def _present(df: pd.DataFrame, cols: Iterable[str]) -> list[str]:
    return [c for c in cols if c in df.columns]


# ---------------------------------------------------------------------------
# Per-group summaries
# ---------------------------------------------------------------------------
def segment_summary(
    df: pd.DataFrame,
    values: Sequence[str] | None = None,
    group_cols: Sequence[str] | None = None,
) -> pd.DataFrame:
    """Per (segment, day-group, time-bin) statistics.

    Args:
        df: local, binned rows (see module docstring).
        values: metric columns to summarize. Defaults to the detected
            ``Speed(...)`` and ``Travel Time(...)`` columns.
        group_cols: grouping keys. Defaults to whichever of
            ``[Segment ID, Day Group, Time Bin]`` are present.

    Returns:
        One row per group with ``count / mean / std / median`` for each value
        column, flattened to ``"<value>_<stat>"`` columns (e.g.
        ``"Speed(miles/hour)_mean"``). ``std`` is the sample std (ddof=1); a
        singleton group's std is ``NaN``, as in the seed. Rows with an NA group
        key (unassigned bin) are dropped.
    """
    values = list(values) if values is not None else _default_values(df)
    if not values:
        raise ValueError("No value columns to summarize (pass `values=`).")
    if group_cols is None:
        group_cols = _present(df, [SEGMENT_COL, DAY_GROUP_COL, TIME_BIN_COL])
    else:
        group_cols = list(group_cols)
    if not group_cols:
        raise ValueError("No grouping columns present (pass `group_cols=`).")

    g = df.groupby(group_cols, dropna=True, observed=True).agg({v: list(_STATS) for v in values})
    g.columns = [f"{col}_{stat}" for col, stat in g.columns]
    out = g.reset_index()
    out.attrs = dict(df.attrs)
    return out


def daily_timebin_summary(
    df: pd.DataFrame,
    value: str | None = None,
    group_cols: Sequence[str] | None = None,
    datetime_col: str = DATETIME_COL,
) -> pd.DataFrame:
    """Per-date mean ± 1 SD of ``value`` within each (segment, group) — the
    ``process_and_plot_timebin_daily_summary`` payload as a DataFrame (the plot
    is the caller's job).

    Args:
        value: metric to summarize (default: the detected ``Speed(...)`` column).
        group_cols: keys **besides** the date. Defaults to
            ``[Segment ID, Group Label]`` if a ``Group Label`` column is present,
            else ``[Segment ID, Day Group, Time Bin]``.

    Returns:
        Columns: the group keys, ``Date`` (local calendar date), ``Count``,
        ``Mean``, ``Std``, ``Upper`` (= Mean + Std), ``Lower`` (= Mean − Std).
        ``Upper``/``Lower`` are NaN for a singleton day (std undefined), matching
        the seed. Rows with an NA group key are dropped.
    """
    if value is None:
        value = metric_columns(df)["speed"]
    if value is None:
        raise ValueError("No speed column found; pass `value=`.")
    if group_cols is None:
        if GROUP_LABEL_COL in df.columns:
            group_cols = _present(df, [SEGMENT_COL, GROUP_LABEL_COL])
        else:
            group_cols = _present(df, [SEGMENT_COL, DAY_GROUP_COL, TIME_BIN_COL])
    else:
        group_cols = list(group_cols)

    work = df.copy()
    work["Date"] = work[datetime_col].dt.date
    keys = [*group_cols, "Date"]
    stats = (
        work.groupby(keys, dropna=True, observed=True)[value]
        .agg(Count="count", Mean="mean", Std="std")
        .reset_index()
    )
    stats["Upper"] = stats["Mean"] + stats["Std"]
    stats["Lower"] = stats["Mean"] - stats["Std"]
    stats.attrs = dict(df.attrs)
    return stats


# ---------------------------------------------------------------------------
# Corridor travel time (segment -> corridor, complete-set rule)
# ---------------------------------------------------------------------------
def corridor_travel_time(
    df: pd.DataFrame,
    metadata: pd.DataFrame | None = None,
    corridor_col: str = CORRIDOR_COL,
    require_complete: bool = True,
    datetime_col: str = DATETIME_COL,
) -> pd.DataFrame:
    """Sum member-segment travel time to a corridor total per timestamp, applying
    the **complete-set rule** from DATA_FORMAT.md: only timestamps where every
    segment of the corridor reported are summed, so a missing segment shows up as
    a dropped timestamp instead of a silent undercount.

    Uses the Item 1 helper ``io.mark_complete_timestamps`` to flag completeness
    (``expected_segments`` = the corridor's max observed segment count).

    Args:
        df: local rows with ``Travel Time(...)``, ``Segment ID`` and a corridor
            label column (``Corridor/Region Name`` lives in ``data.csv`` — the raw
            ``metadata.csv`` has no corridor column; see DATA_FORMAT.md).
        metadata: optional ``Segment ID``-indexed metadata (from
            ``io.load_metadata``). When given, the corridor's summed length is
            added as ``Length(Miles)`` and, if units are miles/minutes, a
            space-mean ``Corridor Speed(miles/hour)`` (length ÷ travel-time).
        require_complete: keep only complete timestamps (default). ``False``
            returns every timestamp with an ``complete`` flag so partials are
            visible but not dropped.

    Returns:
        One row per (corridor, timestamp): ``[corridor_col, Date Time,
        Travel Time(...), n_segments, expected_segments, complete]`` (+ length /
        speed when metadata is supplied). ``corridor_col`` is renamed to nothing —
        it keeps its own name.
    """
    tt = metric_columns(df)["travel_time"]
    if tt is None:
        raise ValueError("No 'Travel Time(...)' column found.")
    if corridor_col not in df.columns:
        raise ValueError(f"Corridor column {corridor_col!r} not in df; see DATA_FORMAT.md.")

    marked = mark_complete_timestamps(df, corridor_col=corridor_col)
    summed = (
        marked.groupby([corridor_col, datetime_col], observed=True)
        .agg(
            **{
                tt: (tt, "sum"),
                "n_segments": (SEGMENT_COL, "nunique"),
                "expected_segments": ("expected_segments", "max"),
            }
        )
        .reset_index()
    )
    summed["complete"] = summed["n_segments"] >= summed["expected_segments"]
    if require_complete:
        summed = summed[summed["complete"]].reset_index(drop=True)

    if metadata is not None:
        summed = _attach_corridor_length_speed(summed, df, metadata, corridor_col, tt)

    summed.attrs = dict(df.attrs)
    return summed


def _attach_corridor_length_speed(
    summed: pd.DataFrame,
    df: pd.DataFrame,
    metadata: pd.DataFrame,
    corridor_col: str,
    tt_col: str,
) -> pd.DataFrame:
    """Add corridor ``Length(Miles)`` (sum of member-segment lengths) and, when
    units are miles/minutes, space-mean ``Corridor Speed(miles/hour)``."""
    length_col = "Segment Length(Miles)"
    if length_col not in metadata.columns:
        return summed
    # member segments = the distinct segments observed in that corridor.
    members = df[[corridor_col, SEGMENT_COL]].drop_duplicates()
    members = members.join(metadata[length_col], on=SEGMENT_COL)
    corridor_len = members.groupby(corridor_col, observed=True)[length_col].sum()
    corridor_len.name = "Length(Miles)"
    out = summed.merge(corridor_len, on=corridor_col, how="left")

    units = df.attrs.get("units", {})
    if units.get("travel_time") == "Minutes":
        # space-mean speed: miles / (minutes / 60) = mph. NaN-safe on 0 travel time.
        hours = out[tt_col] / 60.0
        out["Corridor Speed(miles/hour)"] = out["Length(Miles)"] / hours.where(hours > 0)
    return out


# ---------------------------------------------------------------------------
# Rolling average — an explicit, testable transform (not baked into a summary)
# ---------------------------------------------------------------------------
def rolling_average(
    df: pd.DataFrame,
    value: str | None = None,
    window: int = 5,
    group_cols: Sequence[str] = (SEGMENT_COL,),
    direction: str = "trailing",
    datetime_col: str = DATETIME_COL,
    out_col: str | None = None,
) -> pd.DataFrame:
    """Add a moving average of ``value`` within each group, computed on rows
    sorted by time. Kept separate from the summaries (the seed baked it into
    ``process_and_plot_speed``); here it's an explicit transform you opt into.

    Args:
        window: number of samples in the window.
        group_cols: don't roll across these boundaries (default per segment; add
            ``day_of_week`` to avoid rolling across days, as the seed did).
        direction: ``"trailing"`` (current + prior, the causal default),
            ``"leading"`` (current + following), or ``"centered"``.
        out_col: name of the added column (default
            ``"<value> roll<window> <direction>"``).

    Returns a copy with the rolling column appended; input row order preserved.
    """
    if value is None:
        value = metric_columns(df)["speed"]
    if value is None:
        raise ValueError("No speed column found; pass `value=`.")
    if direction not in ("trailing", "leading", "centered"):
        raise ValueError(f"direction must be trailing/leading/centered, got {direction!r}.")
    out_col = out_col or f"{value} roll{window} {direction}"

    out = df.copy()
    order = out.sort_values(datetime_col).index
    s = out.loc[order]

    grouped = s.groupby(list(group_cols), observed=True, group_keys=False)[value]
    if direction == "trailing":
        rolled = grouped.transform(lambda x: x.rolling(window, min_periods=1).mean())
    elif direction == "leading":
        rolled = grouped.transform(
            lambda x: x[::-1].rolling(window, min_periods=1).mean()[::-1]
        )
    else:  # centered
        rolled = grouped.transform(
            lambda x: x.rolling(window, min_periods=1, center=True).mean()
        )

    out[out_col] = rolled.reindex(out.index)
    out.attrs = dict(df.attrs)
    return out

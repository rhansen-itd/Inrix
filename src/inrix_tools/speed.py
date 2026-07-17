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

# The synthetic corridor label used to sum *all* segments into one network total
# (see ``network_travel_time``).
NETWORK_LABEL = "Network"

# The delay column ``segment_delay`` attaches (excess travel time over free-flow).
DELAY_COL = "Delay(Minutes)"


# ---------------------------------------------------------------------------
# Column discovery
# ---------------------------------------------------------------------------
def metric_columns(df: pd.DataFrame) -> dict[str, str | None]:
    """Locate the ``Speed(...)``, ``Travel Time(...)`` and ``Delay(...)`` columns
    by prefix, so aggregation doesn't hard-code the unit (mph vs kmh — see
    DATA_FORMAT.md). ``delay`` is present only after ``segment_delay`` runs."""
    speed = next((c for c in df.columns if c.startswith("Speed(")), None)
    tt = next((c for c in df.columns if c.startswith("Travel Time(")), None)
    delay = next((c for c in df.columns if c.startswith("Delay(")), None)
    return {"speed": speed, "travel_time": tt, "delay": delay}


def _default_values(df: pd.DataFrame) -> list[str]:
    m = metric_columns(df)
    return [c for c in (m["speed"], m["travel_time"], m["delay"]) if c is not None]


# ---------------------------------------------------------------------------
# Delay vs free-flow travel time  (ROADMAP Item 17)
# ---------------------------------------------------------------------------
def _ref_speed_column(df: pd.DataFrame) -> str | None:
    return next((c for c in df.columns if c.startswith("Ref Speed(")), None)


def _free_flow_speed(df: pd.DataFrame, free_flow, speed_col: str | None):
    """Resolve the per-row free-flow (open-road) speed series and a label for it.

    ``free_flow='ref'`` uses the per-row ``Ref Speed(...)`` column (INRIX's
    free-flow reference — **not** the posted limit; see DATA_FORMAT.md). A
    ``('pXX', q)`` spec uses each segment's ``q``-th percentile of *observed*
    speed instead, for exports/segments where ``Ref Speed`` is missing or suspect.
    """
    if free_flow == "ref":
        ref = _ref_speed_column(df)
        if ref is None:
            raise ValueError(
                "free_flow='ref' but no 'Ref Speed(...)' column is present; pass "
                "a ('pXX', percentile) spec to use observed speed instead."
            )
        return df[ref].astype(float), "ref"
    if isinstance(free_flow, (tuple, list)) and len(free_flow) == 2:
        kind, q = free_flow
        if isinstance(kind, str) and kind.lower().startswith("p"):
            if speed_col is None:
                raise ValueError("Percentile free-flow needs a 'Speed(...)' column.")
            pct = float(q)
            per_seg = df.groupby(SEGMENT_COL, observed=True)[speed_col].transform(
                lambda x: x.quantile(pct / 100.0)
            )
            return per_seg.astype(float), f"p{q:g}"
    raise ValueError(
        f"Unrecognized free_flow spec {free_flow!r}; use 'ref' or ('pXX', percentile)."
    )


def _segment_length(df: pd.DataFrame, geo_or_metadata) -> pd.Series | None:
    """A per-row segment length (miles) aligned to ``df``, from a GeoDataFrame
    (``Miles``) or metadata (``Segment Length(Miles)``) keyed by ``Segment ID``,
    or ``None`` when no length source is available."""
    if geo_or_metadata is None:
        return None
    src = geo_or_metadata
    cols = list(getattr(src, "columns", []))
    length_col = next((c for c in ("Miles", "Segment Length(Miles)") if c in cols), None)
    if length_col is None:
        return None
    lut = src[length_col]
    if lut.index.name != SEGMENT_COL and SEGMENT_COL in cols:
        lut = src.set_index(SEGMENT_COL)[length_col]
    mapped = df[SEGMENT_COL].map(lut.astype(float))
    mapped.index = df.index
    return mapped


def segment_delay(
    df: pd.DataFrame,
    geo_or_metadata: pd.DataFrame | None = None,
    free_flow="ref",
    floor: bool = True,
    out_col: str = DELAY_COL,
) -> pd.DataFrame:
    """Add a per-row ``Delay(Minutes)`` column — the excess travel time a segment
    carries over its free-flow (open-road) travel time.

    ``delay = observed Travel Time(Minutes) - free-flow travel time``, where the
    free-flow travel time is ``Miles / free_flow_speed x 60``. INRIX already
    supplies the free-flow speed (``Ref Speed(...)``, DATA_FORMAT.md) and observed
    travel time, and the geometry/metadata supplies length, so delay is a pure
    derivation — no new data source (ROADMAP Item 17).

    Args:
        geo_or_metadata: a GeoDataFrame (``Miles``) or metadata frame
            (``Segment Length(Miles)``) keyed by ``Segment ID``, supplying segment
            length. When length is unavailable the computation **degrades to the
            speed-based form** ``TravelTime x (1 - v_obs/v_ff)``, which is
            algebraically the same value (length cancels) as long as a
            ``Speed(...)`` column is present.
        free_flow: ``'ref'`` (default) uses the per-row ``Ref Speed(...)`` column;
            a ``('pXX', q)`` spec uses each segment's ``q``-th percentile of
            observed speed (for exports where ``Ref Speed`` is missing/suspect).
        floor: clamp negative delay (probe noise faster than free-flow) to 0
            (default). Set ``False`` to keep signed values.
        out_col: name of the added column (default ``"Delay(Minutes)"``).

    Returns:
        A copy of ``df`` with ``out_col`` appended. Rows where the free-flow speed
        is missing/non-positive get ``NaN`` delay. ``attrs['delay']`` records the
        resolved ``free_flow`` source, ``floor``, and the ``length_source``
        (``'length'`` or ``'speed'``).
    """
    m = metric_columns(df)
    tt_col, sp_col = m["travel_time"], m["speed"]
    if tt_col is None:
        raise ValueError("No 'Travel Time(...)' column; cannot compute delay.")

    v_ff, ff_source = _free_flow_speed(df, free_flow, sp_col)
    length = _segment_length(df, geo_or_metadata)

    tt_obs = df[tt_col].astype(float)
    v_ff = v_ff.astype(float)
    valid = v_ff > 0

    free_tt = None
    length_source = None
    if length is not None:
        free_tt = length / v_ff * 60.0
        length_source = "length"
    if sp_col is not None:
        speed_based = tt_obs * df[sp_col].astype(float) / v_ff
        if free_tt is None:
            free_tt, length_source = speed_based, "speed"
        else:
            # per-segment missing lengths fall back to the (identical) speed form
            free_tt = free_tt.fillna(speed_based)
    if free_tt is None:
        raise ValueError(
            "Need segment length (geo_or_metadata) or a 'Speed(...)' column to "
            "compute free-flow travel time; neither was available."
        )

    delay = (tt_obs - free_tt).where(valid)
    if floor:
        delay = delay.clip(lower=0)  # NaN is preserved by clip

    out = df.copy()
    out[out_col] = delay
    out.attrs = dict(df.attrs)
    out.attrs["delay"] = {
        "free_flow": ff_source,
        "floor": bool(floor),
        "length_source": length_source,
    }
    return out


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
    value: str | None = None,
    expected: str = "max",
    members: Sequence[int] | None = None,
) -> pd.DataFrame:
    """Sum member-segment travel time to a corridor total per timestamp, applying
    the **complete-set rule** from DATA_FORMAT.md: only timestamps where every
    segment of the corridor reported are summed, so a missing segment shows up as
    a dropped timestamp instead of a silent undercount.

    Uses the Item 1 helper ``io.mark_complete_timestamps`` to flag completeness.
    ``expected`` ("max"/"total") selects how the complete-set size is defined —
    ``"max"`` (default) is the corridor's max simultaneously observed count;
    ``"total"`` counts every distinct member segment, so a sparse group's
    "complete" timestamps stay level-comparable (see ``mark_complete_timestamps``
    and DATA_FORMAT.md's complete-set note).

    Args:
        df: local rows with ``Travel Time(...)``, ``Segment ID`` and a corridor
            label column (``Corridor/Region Name`` lives in ``data.csv`` — the raw
            ``metadata.csv`` has no corridor column; see DATA_FORMAT.md).
        metadata: optional ``Segment ID``-indexed metadata (from
            ``io.load_metadata``). When given, the corridor's summed length is
            added as ``Length(Miles)`` and, if units are miles/minutes, a
            space-mean ``Corridor Speed(miles/hour)`` (length ÷ travel-time). Only
            attached when summing the travel-time column (not, e.g., delay).
        require_complete: keep only complete timestamps (default). ``False``
            returns every timestamp with an ``complete`` flag so partials are
            visible but not dropped.
        value: the column to sum across member segments (default: the detected
            ``Travel Time(...)``). ``Delay(Minutes)`` (ROADMAP Item 17) sums the
            same way — corridor delay is the sum of member-segment delays under the
            same complete-set rule. The summed column keeps its own name.
            Completeness is **value-aware**: a reported row whose ``value`` is NaN
            (possible for delay when the free-flow speed is unresolvable) does not
            count toward the complete set, so such timestamps are dropped/flagged
            rather than silently summed short; a timestamp with no values at all
            yields NaN, never a fabricated 0.
        members: optional explicit ``Segment ID`` membership (ROADMAP Item 19). When
            given, the corridor is exactly these segments — rows are restricted to
            them *before* the complete-set rule runs, so a user can deselect a
            chronically-missing segment (``segment_coverage``) and recover the
            timestamps its absence was costing. ``None`` (default) keeps the
            ``corridor_col`` grouping's observed membership. Additive — the existing
            behaviour is unchanged when omitted.

    Returns:
        One row per (corridor, timestamp): ``[corridor_col, Date Time,
        <value>, n_segments, expected_segments, complete]`` (+ length /
        speed when metadata is supplied and ``value`` is travel time).
        ``corridor_col`` keeps its own name.
    """
    tt = value or metric_columns(df)["travel_time"]
    if tt is None:
        raise ValueError("No 'Travel Time(...)' column found.")
    if tt not in df.columns:
        raise ValueError(f"Value column {tt!r} not in df.")
    if corridor_col not in df.columns:
        raise ValueError(f"Corridor column {corridor_col!r} not in df; see DATA_FORMAT.md.")

    # Explicit membership (Item 19): restrict the sum to exactly the selected
    # segments *before* the complete-set rule runs, so completeness is measured
    # against the chosen member set — deselecting a chronically-missing segment
    # both drops it from the sum and stops it from starving the complete-set count.
    sub = df
    if members is not None:
        sub = df[df[SEGMENT_COL].isin([int(m) for m in members])]

    marked = mark_complete_timestamps(sub, corridor_col=corridor_col, expected=expected)
    # A row whose value is NaN (e.g. Delay where the free-flow speed couldn't be
    # resolved) contributes nothing to the sum, so it must not count toward the
    # complete set either — otherwise the sum would undercount while still
    # claiming completeness. ``expected_segments`` stays row-based (the
    # corridor's full observed membership), ``n_segments`` counts only the
    # segments that actually carry a value at that timestamp.
    marked["_value_seg"] = marked[SEGMENT_COL].where(marked[tt].notna())
    summed = (
        marked.groupby([corridor_col, datetime_col], observed=True)
        .agg(
            **{
                tt: (tt, "sum"),
                "n_segments": ("_value_seg", "nunique"),
                "expected_segments": ("expected_segments", "max"),
            }
        )
        .reset_index()
    )
    # pandas sums an all-NaN group to 0.0; report it as NaN (no data, not zero).
    summed.loc[summed["n_segments"] == 0, tt] = float("nan")
    summed["complete"] = summed["n_segments"] >= summed["expected_segments"]
    if require_complete:
        summed = summed[summed["complete"]].reset_index(drop=True)

    # Length + space-mean speed only make sense for the travel-time sum.
    if metadata is not None and tt == metric_columns(df)["travel_time"]:
        summed = _attach_corridor_length_speed(summed, sub, metadata, corridor_col, tt)

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


def network_travel_time(
    df: pd.DataFrame,
    metadata: pd.DataFrame | None = None,
    require_complete: bool = True,
    datetime_col: str = DATETIME_COL,
    corridor_col: str = CORRIDOR_COL,
    label: str = NETWORK_LABEL,
    value: str | None = None,
    expected: str = "total",
    members: Sequence[int] | None = None,
) -> pd.DataFrame:
    """Sum **every** segment's travel time to a single network total per timestamp
    — the corridor sum (``corridor_travel_time``) over one synthetic all-segments
    group, under the same **complete-set rule** (only timestamps where every
    segment in the export reported are summed).

    This is ``corridor_travel_time`` with the corridor label collapsed to a single
    value, so the output shape is identical (``corridor_col`` carries ``label``,
    ``"Network"`` by default). The complete-set rule is *stricter* here than for a
    short corridor: the network total requires **all** segments present at a
    timestamp, so with many segments a sizeable fraction of timestamps drop as
    partial. That is the correct, undercount-free behaviour (a missing segment
    would otherwise silently shrink the total); it does mean the network series can
    be sparse — feed it through ``decompose_segments`` with the same auto-scaled
    window guard as any other series (see DATA_FORMAT.md's complete-set note).

    ``expected`` defaults to ``"total"`` here (not ``"max"``): the network total
    is only level-comparable across time if it always sums the **same** whole set
    of segments, and a sparse network is precisely where ``"max"`` would let two
    "complete" timestamps sum different subsets (see ``mark_complete_timestamps``).
    Pass ``expected="max"`` for the older max-simultaneous behaviour. When the full
    set is regularly achieved (e.g. the Myrtle fixture's 46 segments) the two agree.

    Args:
        df: local rows with ``Travel Time(...)`` and ``Segment ID`` (a corridor
            column is *not* required — every segment is one group here).
        metadata: optional ``Segment ID``-indexed metadata; when given, the summed
            network length + space-mean speed are attached exactly as for
            ``corridor_travel_time``.
        require_complete: keep only complete timestamps (default). ``False`` keeps
            partial timestamps with the ``complete`` flag so gaps are visible.
        label: the single-group label written into ``corridor_col``.
        members: optional explicit ``Segment ID`` membership (ROADMAP Item 19) — sum
            only these segments as the "network", under the same complete-set rule.
            ``None`` (default) sums every segment in the export.

    Returns:
        One row per timestamp (same columns as ``corridor_travel_time``), with
        ``corridor_col`` == ``label`` throughout.
    """
    tt = value or metric_columns(df)["travel_time"]
    if tt is None:
        raise ValueError("No 'Travel Time(...)' column found.")
    work = df.copy()
    # Collapse to one all-segments group (overwrites any real corridor label): the
    # complete-set machinery then treats the whole export as a single corridor.
    work[corridor_col] = label
    out = corridor_travel_time(
        work, metadata=metadata, corridor_col=corridor_col,
        require_complete=require_complete, datetime_col=datetime_col, value=tt,
        expected=expected, members=members,
    )
    out.attrs = dict(df.attrs)
    return out


# ---------------------------------------------------------------------------
# Segment coverage / completeness diagnostics  (ROADMAP Item 19)
# ---------------------------------------------------------------------------
# Column names on the coverage table.
COVERAGE_COL = "coverage"                 # fraction of member timestamps a segment reports
COMPLETE_COST_COL = "complete_set_cost"   # complete-set timestamps its removal would recover


def segment_coverage(
    df: pd.DataFrame,
    members: Sequence[int] | None = None,
    value: str | None = None,
    datetime_col: str = DATETIME_COL,
) -> pd.DataFrame:
    """Per-segment completeness diagnostics over a member set — which segments cost
    the corridor/network **complete-set rule** timestamps (ROADMAP Item 19).

    For the chosen member set (``members``, or every distinct ``Segment ID`` in
    ``df`` when ``None``), this reports for each member, over the set of timestamps
    where *any* member reported:

    - ``coverage`` — the fraction of those timestamps at which the segment itself
      reported. A chronically-missing segment has low coverage.
    - ``complete_set_cost`` — the count of timestamps that would become
      **complete** (every remaining member present) if this segment were dropped
      from the set — i.e. the timestamps at which it is the *sole* absent member.
      This is exactly how many complete-set timestamps its presence is costing:
      deselecting the highest-cost segment recovers that many timestamps for the
      aggregate series (see ``corridor_travel_time``'s complete-set rule and
      DATA_FORMAT.md).

    "Reported" is value-aware when ``value`` is given (a row whose ``value`` is NaN
    — e.g. an unresolvable delay — does not count as present, matching
    ``corridor_travel_time``); with ``value=None`` any row for the segment counts.

    Args:
        df: local rows with ``Segment ID`` and ``datetime_col`` (typically already
            ToD/DOW-filtered by the caller, so coverage describes the analysed
            window).
        members: the member ``Segment ID`` set to measure over. ``None`` uses every
            segment present in ``df``.
        value: optional column whose non-NaN presence defines "reported".

    Returns:
        One row per member: ``[Segment ID, n_reported, n_timestamps, coverage,
        complete_set_cost]``, sorted by descending ``complete_set_cost`` then
        ascending ``coverage`` (the segments most worth deselecting first).
        ``n_timestamps`` is the shared denominator (distinct timestamps any member
        reported). ``attrs`` records ``n_members``, ``n_timestamps`` and
        ``n_complete`` (complete-set timestamps under the full member set).
    """
    if SEGMENT_COL not in df.columns:
        raise ValueError(f"{SEGMENT_COL!r} column required for segment_coverage.")
    if members is None:
        member_ids = [int(s) for s in pd.unique(df[SEGMENT_COL])]
    else:
        member_ids = [int(m) for m in dict.fromkeys(members)]  # de-dup, keep order

    cols = [SEGMENT_COL, datetime_col] + ([value] if value else [])
    sub = df[df[SEGMENT_COL].isin(member_ids)][cols].copy()
    if value is not None:
        sub = sub[sub[value].notna()]

    empty = pd.DataFrame(
        {SEGMENT_COL: pd.Series(member_ids, dtype="int64"),
         "n_reported": 0, "n_timestamps": 0,
         COVERAGE_COL: float("nan"), COMPLETE_COST_COL: 0}
    )
    if not member_ids or sub.empty:
        empty.attrs = {"n_members": len(member_ids), "n_timestamps": 0, "n_complete": 0}
        return empty

    # Presence matrix: rows = timestamps any member reported, cols = every member
    # (a member that never reports gets an all-False column via reindex).
    present = (
        sub.assign(_p=True)
        .pivot_table(index=datetime_col, columns=SEGMENT_COL, values="_p",
                     aggfunc="any", fill_value=False)
        .reindex(columns=member_ids, fill_value=False)
        .astype(bool)
    )
    n_ts = len(present)
    n_members = len(member_ids)
    per_ts_present = present.sum(axis=1)
    n_complete = int((per_ts_present >= n_members).sum())
    # A segment's removal recovers a timestamp iff it is the *only* absent member:
    # present count is exactly n_members-1 and this segment is the one absent.
    missing_one = per_ts_present == (n_members - 1)
    n_reported = present.sum(axis=0)
    cost = (missing_one.values[:, None] & ~present.values).sum(axis=0)

    out = pd.DataFrame({
        SEGMENT_COL: pd.Series(member_ids, dtype="int64"),
        "n_reported": [int(n_reported[s]) for s in member_ids],
        "n_timestamps": int(n_ts),
        COVERAGE_COL: [float(n_reported[s]) / n_ts for s in member_ids],
        COMPLETE_COST_COL: [int(c) for c in cost],
    })
    out = out.sort_values([COMPLETE_COST_COL, COVERAGE_COL],
                          ascending=[False, True]).reset_index(drop=True)
    out.attrs = {"n_members": n_members, "n_timestamps": int(n_ts),
                 "n_complete": n_complete}
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

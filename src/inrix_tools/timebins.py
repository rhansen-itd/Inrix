"""Day-group and time-of-day binning.  (ROADMAP Item 2)

Ports ``map_day_group`` / ``assign_time_chunks`` (and the inline binning inside
``process_and_plot_*``) from ``_Plot Speed.ipynb`` as pure, vectorized,
overnight-safe functions. Binning is done in **local wall-clock time**, so run
``io.to_local`` first (the timestamp column must be tz-aware local, not UTC) —
otherwise morning/evening bins land at the wrong hour.

What this fixes vs. the seed notebook:
- **Half-open bins ``[start, end)``.** The ``process_and_plot_*`` variants used
  ``start <= t <= end`` (both ends inclusive), so contiguous bins double-count
  the shared edge (``2:00PM`` fell in both ``"9:00AM-2:00PM"`` and
  ``"2:00PM-7:00PM"``). The later ``assign_time_chunks`` already used half-open;
  we standardize on it. Bins are assumed non-overlapping; on overlap the
  first-listed bin wins.
- **Vectorized**, not per-row ``.apply`` — time-of-day is reduced to
  seconds-since-midnight once and compared with array masks.
- **Overnight bins** (``"9:00PM-6:00AM"``) wrap correctly via ``t >= start | t <
  end`` instead of silently matching nothing.
- **Configurable day scheme** — no hardcoded Mon–Thu/Fri/Sat/Sun; the default is
  provided but any ``{dow: label}`` mapping (or ``"Monday-Thursday"``-style range
  specs) works.

All three functions return a copy with the new column added; ``df.attrs`` is
preserved. Unassigned rows get ``pd.NA`` in the new column (drop them downstream
with ``df.dropna(subset=[...])``), matching the seed's ``dropna(subset=['Group'])``.
"""
from __future__ import annotations

from datetime import datetime, time
from typing import Iterable, Mapping

import pandas as pd

from .io import DATETIME_COL

# day-of-week is 0=Mon .. 6=Sun (pandas ``.dt.dayofweek`` convention)
DEFAULT_DAY_GROUPS: dict[int, str] = {
    0: "Mon–Thu", 1: "Mon–Thu", 2: "Mon–Thu", 3: "Mon–Thu",
    4: "Fri", 5: "Sat", 6: "Sun",
}

DAY_GROUP_COL = "Day Group"
TIME_BIN_COL = "Time Bin"
GROUP_LABEL_COL = "Group Label"

# full weekday names -> dayofweek int, for parsing ``"Monday-Thursday"`` specs.
_DAY_NAME_TO_DOW = {
    "MONDAY": 0, "TUESDAY": 1, "WEDNESDAY": 2, "THURSDAY": 3,
    "FRIDAY": 4, "SATURDAY": 5, "SUNDAY": 6,
}


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------
def parse_clock(t_str: str) -> time:
    """Parse a 12-hour clock string (``"6:30AM"``, ``"12:00 am"``, ``"9PM"``) into
    a ``datetime.time``. Robust to surrounding whitespace, internal spaces, and
    case. ``12:00AM`` -> midnight, ``12:00PM`` -> noon."""
    s = t_str.strip().upper().replace(" ", "")
    for fmt in ("%I:%M%p", "%I%p"):  # allow the minutes to be omitted (``"6AM"``)
        try:
            return datetime.strptime(s, fmt).time()
        except ValueError:
            continue
    raise ValueError(f"Could not parse clock time {t_str!r} (expected e.g. '6:30AM').")


def _secs(t: time) -> int:
    """Seconds since local midnight for a ``datetime.time``."""
    return t.hour * 3600 + t.minute * 60 + t.second


def parse_time_bin(bin_str: str) -> tuple[str, int, int, bool]:
    """Parse ``"6:30AM-9:00AM"`` into ``(label, start_sec, end_sec, overnight)``.

    ``label`` is the original string (used as the bin label). ``overnight`` is
    True when the end wraps past midnight (``start_sec > end_sec``), e.g.
    ``"9:00PM-6:00AM"``. An end of ``"12:00AM"`` is midnight = 0 s, which makes a
    late-evening bin like ``"10:00PM-12:00AM"`` overnight and stop exactly at
    midnight — the intended behavior.
    """
    try:
        start_str, end_str = bin_str.split("-")
    except ValueError:
        raise ValueError(
            f"Time bin {bin_str!r} must be 'START-END' (e.g. '6:30AM-9:00AM')."
        )
    start, end = _secs(parse_clock(start_str)), _secs(parse_clock(end_str))
    return bin_str, start, end, start > end


def _clock_seconds(x) -> int:
    """Seconds since local midnight for a single clock spec.

    Accepts a ``datetime.time``, a 12-hour clock string (``"4:00PM"``), or an
    hour **number** (``16``, ``17.5``; ``24`` = end of day). The numeric form is
    what a GUI hour slider passes.
    """
    if isinstance(x, time):
        return _secs(x)
    if isinstance(x, str):
        return _secs(parse_clock(x))
    h = float(x)
    if not 0 <= h <= 24:
        raise ValueError(f"Hour {x!r} out of range 0..24.")
    return int(round(h * 3600))


def _normalize_day_scheme(scheme) -> dict[int, str]:
    """Turn a day-group ``scheme`` into a ``{dow: label}`` dict.

    Accepts either that dict directly, or a list of range specs like
    ``["Monday-Thursday", "Friday", "Saturday", "Sunday"]`` (weekday names,
    inclusive, wrap-around such as ``"Friday-Monday"`` allowed). The spec string
    is used verbatim as the label. ``None`` -> ``DEFAULT_DAY_GROUPS``.
    """
    if scheme is None:
        return dict(DEFAULT_DAY_GROUPS)
    if isinstance(scheme, Mapping):
        return {int(k): str(v) for k, v in scheme.items()}

    mapping: dict[int, str] = {}
    for spec in scheme:
        if "-" in spec:
            start_name, end_name = (p.strip().upper() for p in spec.split("-"))
            start_idx, end_idx = _DAY_NAME_TO_DOW[start_name], _DAY_NAME_TO_DOW[end_name]
            if start_idx <= end_idx:
                days = range(start_idx, end_idx + 1)
            else:  # wrap-around, e.g. "Friday-Monday"
                days = [*range(start_idx, 7), *range(0, end_idx + 1)]
        else:
            days = [_DAY_NAME_TO_DOW[spec.strip().upper()]]
        for d in days:
            mapping[d] = spec
    return mapping


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def assign_day_group(
    df: pd.DataFrame,
    scheme: Mapping[int, str] | Iterable[str] | None = None,
    datetime_col: str = DATETIME_COL,
    out_col: str = DAY_GROUP_COL,
) -> pd.DataFrame:
    """Add a day-group column from the local day-of-week.

    Args:
        df: rows with a tz-aware local ``datetime_col`` (run ``io.to_local`` first).
        scheme: a ``{dayofweek: label}`` dict (0=Mon) or a list of weekday range
            specs (``["Monday-Thursday", "Friday", ...]``). Defaults to
            ``DEFAULT_DAY_GROUPS`` (Mon–Thu / Fri / Sat / Sun).
        out_col: name of the added column.

    Days absent from the scheme get ``pd.NA``.
    """
    mapping = _normalize_day_scheme(scheme)
    out = df.copy()
    dow = out[datetime_col].dt.dayofweek
    out[out_col] = dow.map(mapping).astype("string")
    out.attrs = dict(df.attrs)
    return out


def assign_time_bins(
    df: pd.DataFrame,
    bins: Iterable[str],
    datetime_col: str = DATETIME_COL,
    out_col: str = TIME_BIN_COL,
) -> pd.DataFrame:
    """Add a time-of-day bin column from clock-range edges.

    Args:
        df: rows with a tz-aware local ``datetime_col`` (run ``io.to_local`` first).
        bins: clock ranges like ``["6:30AM-9:00AM", "9:00AM-2:00PM"]``. Bins are
            **half-open** ``[start, end)`` and may wrap past midnight
            (``"9:00PM-6:00AM"``). Assumed non-overlapping; the first listed bin
            wins on any overlap. The range string is the bin's label.
        out_col: name of the added column.

    Rows outside every bin get ``pd.NA``. Fully vectorized (no per-row ``apply``).
    """
    parsed = [parse_time_bin(b) for b in bins]
    out = df.copy()
    local = out[datetime_col]
    # seconds since local midnight — one vector, reused for every bin comparison.
    tod = local.dt.hour * 3600 + local.dt.minute * 60 + local.dt.second

    labels = pd.Series(pd.NA, index=out.index, dtype="string")
    for label, start, end, overnight in parsed:
        if overnight:
            mask = (tod >= start) | (tod < end)
        else:
            mask = (tod >= start) & (tod < end)
        # first-listed-bin-wins: only fill rows still unassigned.
        labels = labels.mask(mask & labels.isna(), label)

    out[out_col] = labels
    out.attrs = dict(df.attrs)
    return out


def filter_time_window(
    df: pd.DataFrame,
    window: str | tuple,
    datetime_col: str = DATETIME_COL,
) -> pd.DataFrame:
    """Keep only rows whose **local wall-clock** time falls in a half-open window.

    The primitive behind "run the analysis on a specific time of day only" (e.g.
    the 4–6PM peak). Restricting rows here, *before* decomposition / changepoint /
    before-after, is what makes those calculations describe the chosen period —
    the trend and detected shifts then reflect that window on its own terms. Run
    ``io.to_local`` first so the timestamp is tz-aware local (binning off UTC would
    land the window at the wrong hour — see the module docstring).

    Args:
        df: rows with a tz-aware local ``datetime_col``.
        window: either a ``"6:30AM-9:00AM"``-style range string (as accepted by
            ``assign_time_bins``) or a ``(start, end)`` pair, where each bound is a
            clock string (``"4:00PM"``), a ``datetime.time``, or an hour number
            (``16``, ``17.5``; ``24`` = end of day). **Half-open** ``[start, end)``.
            Overnight windows (``start > end``, e.g. ``9:00PM-6:00AM``) wrap past
            midnight. A full-day window (``start == end``, or ``0``–``24``) keeps
            every row (a no-op).
        datetime_col: the timestamp column.

    Returns:
        A filtered copy; ``df.attrs`` is preserved and the applied window is
        recorded on ``attrs['time_window']`` for reproducibility.
    """
    if isinstance(window, str):
        _, start, end, _ = parse_time_bin(window)
    else:
        start_spec, end_spec = window
        start, end = _clock_seconds(start_spec), _clock_seconds(end_spec)

    tod = df[datetime_col].dt.hour * 3600 + df[datetime_col].dt.minute * 60 \
        + df[datetime_col].dt.second
    if start == end:                       # whole day
        mask = pd.Series(True, index=df.index)
    elif start < end:
        mask = (tod >= start) & (tod < end)
    else:                                  # overnight wrap
        mask = (tod >= start) | (tod < end)

    out = df[mask].copy()
    out.attrs = dict(df.attrs)
    out.attrs["time_window"] = window
    return out


def filter_date_range(
    df: pd.DataFrame,
    start=None,
    end=None,
    datetime_col: str = DATETIME_COL,
) -> pd.DataFrame:
    """Keep only rows whose **local calendar date** falls in ``[start, end]``
    (inclusive of both endpoint days).

    The calendar-date companion to ``filter_time_window`` (time-of-day): trim a
    session to a date sub-range — e.g. a few weeks of a multi-month export — so
    every downstream compute (map, panels, decomposition) runs on the smaller
    frame. Run ``io.to_local`` first so the timestamp is tz-aware local; the cut
    is made on the **local** wall-clock date and is DST-safe (a whole calendar
    day is kept even across a spring-forward/fall-back).

    Args:
        df: rows with a tz-aware local ``datetime_col``.
        start: the first calendar day to keep (inclusive). A date string
            (``"2026-03-01"``), a ``date`` / ``Timestamp``, or ``None`` / ``""``
            to leave the range **open on the left** (keep everything up to
            ``end``). Any time-of-day component is ignored — the whole day counts.
        end: the last calendar day to keep, **inclusive of that whole day** (the
            exclusive bound is the following local midnight — the same date-only
            convention as ``beforeafter.parse_period``). ``None`` / ``""`` leaves
            the range **open on the right**.
        datetime_col: the timestamp column.

    Returns:
        A filtered copy; ``df.attrs`` is preserved and the applied inclusive date
        span is recorded on ``attrs['date_range']`` as an ISO ``(start, end)``
        pair (each side ``None`` when open) for reproducibility. A ``start`` after
        ``end`` keeps nothing (an empty frame), consistent with the half-open cut.
    """
    tz = df[datetime_col].dt.tz

    def _bound(x):
        if x is None or (isinstance(x, str) and not x.strip()):
            return None
        ts = pd.Timestamp(x).normalize()  # midnight of that calendar day
        return ts.tz_localize(tz) if ts.tzinfo is None else ts.tz_convert(tz)

    lo, hi = _bound(start), _bound(end)

    ts = df[datetime_col]
    mask = pd.Series(True, index=df.index)
    if lo is not None:
        mask &= ts >= lo
    if hi is not None:
        # inclusive of the whole end day -> exclusive at the next local midnight.
        # DateOffset advances a calendar day (DST-safe), unlike a fixed 24h delta.
        mask &= ts < hi + pd.DateOffset(days=1)

    out = df[mask].copy()
    out.attrs = dict(df.attrs)
    out.attrs["date_range"] = (
        None if lo is None else lo.date().isoformat(),
        None if hi is None else hi.date().isoformat(),
    )
    return out


def assign_group_label(
    df: pd.DataFrame,
    day_col: str = DAY_GROUP_COL,
    time_col: str = TIME_BIN_COL,
    out_col: str = GROUP_LABEL_COL,
    sep: str = ", ",
) -> pd.DataFrame:
    """Compose the combined ``"Mon–Thu, 2:00PM-7:00PM"`` label from an existing
    day-group and time-bin column.

    A row is labeled only when **both** parts are present; if either is ``pd.NA``
    (unassigned day or time), the label is ``pd.NA`` too. Run ``assign_day_group``
    and ``assign_time_bins`` first.
    """
    out = df.copy()
    day = out[day_col].astype("string")
    tod = out[time_col].astype("string")
    both = day.notna() & tod.notna()
    label = pd.Series(pd.NA, index=out.index, dtype="string")
    label = label.mask(both, day.str.cat(tod, sep=sep))
    out[out_col] = label
    out.attrs = dict(df.attrs)
    return out

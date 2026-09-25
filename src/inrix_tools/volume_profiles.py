"""Volume-profile curves: how a day's traffic spreads over its hours.  (ROADMAP Item 55)

VHD has so far been an index: each window's mean per-vehicle delay × the segment's
**whole-day** directional AADT. A 2-hour AM window and the 15-hour ``day_7d`` window
carried the same volume. This module supplies the missing factor, a library of
24-hour curves. Each XD segment will get one (Item 56), and a 5-minute bin's volume
becomes

    vol(bin) = dirAADT × MADT_month / AADT × bin_volume_factor(profile, bin)

where :func:`bin_volume_factor` is the share of the *average* day's volume that falls
in that bin on that date. It combines the day type's hourly shape and the day-of-week
factor. The monthly factor comes from the AADT layer (``madt_ratio_MM``,
:func:`inrix_tools.aadt.join_aadt`), not from the curve.

A curve is:

* ``hourly`` — three 24-hour shapes, ``weekday`` / ``sat`` / ``sun``, each summing to
  1. Hour 0 is 00:00–01:00 **local**.
* ``dow`` — seven day-of-week factors, Monday..Sunday (pandas ``dayofweek`` order),
  with mean 1. A Friday on a curve with ``dow[4] = 1.10`` carries 110 % of an average
  day.
* ``provenance`` — ``basis`` (``digitised`` / ``extracted`` / ``synthesised``), the
  ``sources`` it cites (keys into :func:`profile_sources`), and a ``detail`` saying
  exactly how it was built.

The generic starter curves ship as package data
(``inrix_tools/data/volume_profiles.json``). They are built by
``scripts/derive_volume_profiles.py`` from published sources (TTI's Urban Mobility
Report profiles, Idaho's EPA NEI submittal, INDOT's day-of-week factors); see
DATA_FORMAT.md. Curves fitted from counts replace them later (Item 59).

Holidays are not special-cased: a holiday Monday is a weekday with Monday's factor.
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path
from typing import Mapping

import numpy as np
import pandas as pd

DAY_TYPES = ("weekday", "sat", "sun")
"""The three hourly shapes every curve carries."""

BASES = ("digitised", "extracted", "synthesised", "fitted")
"""How a curve was obtained. ``digitised``: read from a published raster chart.
``extracted``: taken exactly from published data (a table or a vector plot).
``synthesised``: assembled from published pieces with a documented assumption.
``fitted``: fitted from counts (Item 59)."""

SUM_TOL = 1e-6
"""Tolerance on Σ hourly = 1 and mean(dow) = 1."""

PACKAGE_DATA = "data/volume_profiles.json"


@dataclass(frozen=True, eq=False)
class VolumeProfile:
    """One 24-hour volume-profile curve. Validated on construction (see module doc).

    Raises:
        ValueError: a day type is missing or has other than 24 values, a value is
            negative or not finite, a day type does not sum to 1, or ``dow`` is not 7
            values with mean 1.
    """

    curve_id: str
    hourly: Mapping[str, tuple]
    dow: tuple
    provenance: Mapping = field(default_factory=dict)
    description: str = ""

    def __post_init__(self):
        if not isinstance(self.curve_id, str) or not self.curve_id.strip():
            raise ValueError(f"curve_id must be a non-empty string, got {self.curve_id!r}")
        if set(self.hourly) != set(DAY_TYPES):
            raise ValueError(f"{self.curve_id}: hourly needs exactly {DAY_TYPES}, "
                             f"got {sorted(self.hourly)}")
        hourly = {}
        for day in DAY_TYPES:
            vals = _check_values(self.curve_id, f"hourly[{day}]", self.hourly[day], 24)
            if abs(sum(vals) - 1.0) > SUM_TOL:
                raise ValueError(f"{self.curve_id}: hourly[{day}] sums to "
                                 f"{sum(vals):.8f}, not 1")
            hourly[day] = vals
        dow = _check_values(self.curve_id, "dow", self.dow, 7)
        if abs(sum(dow) / 7 - 1.0) > SUM_TOL:
            raise ValueError(f"{self.curve_id}: dow has mean {sum(dow) / 7:.8f}, not 1")
        basis = dict(self.provenance).get("basis")
        if basis is not None and basis not in BASES:
            raise ValueError(f"{self.curve_id}: provenance basis {basis!r} not in {BASES}")
        object.__setattr__(self, "hourly", hourly)
        object.__setattr__(self, "dow", dow)
        object.__setattr__(self, "provenance", dict(self.provenance))

    def hourly_array(self) -> np.ndarray:
        """``(3, 24)`` array, rows in :data:`DAY_TYPES` order."""
        return np.array([self.hourly[d] for d in DAY_TYPES], dtype=float)

    def daily_factor(self, dayofweek: int) -> float:
        """The day-of-week factor for pandas ``dayofweek`` (0 = Monday)."""
        return self.dow[int(dayofweek)]


def _check_values(curve_id, name, values, n) -> tuple:
    try:
        vals = tuple(float(v) for v in values)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{curve_id}: {name} is not a list of numbers") from exc
    if len(vals) != n:
        raise ValueError(f"{curve_id}: {name} has {len(vals)} values, needs {n}")
    if not all(math.isfinite(v) for v in vals):
        raise ValueError(f"{curve_id}: {name} has a non-finite value")
    if any(v < 0 for v in vals):
        raise ValueError(f"{curve_id}: {name} has a negative value")
    return vals


def _read_library(source) -> dict:
    if source is None:
        text = resources.files("inrix_tools").joinpath(PACKAGE_DATA).read_text()
        return json.loads(text)
    if isinstance(source, Mapping):
        return dict(source)
    return json.loads(Path(source).read_text())


def load_profiles(source=None) -> dict[str, VolumeProfile]:
    """The curve library, ``{curve_id: VolumeProfile}``.

    Args:
        source: ``None`` for the packaged generic curves, else a path to a library
            JSON of the same schema, or the already-parsed mapping.

    Raises:
        ValueError: a curve fails validation, or two curves share a ``curve_id``.
    """
    lib = _read_library(source)
    out: dict[str, VolumeProfile] = {}
    for rec in lib.get("profiles", []):
        prof = VolumeProfile(curve_id=rec["curve_id"], hourly=rec["hourly"],
                             dow=rec["dow"], provenance=rec.get("provenance", {}),
                             description=rec.get("description", ""))
        if prof.curve_id in out:
            raise ValueError(f"duplicate curve_id {prof.curve_id!r}")
        out[prof.curve_id] = prof
    return out


def profile_sources(source=None) -> dict:
    """The library's citation table, ``{source key: {citation, url}}``."""
    return dict(_read_library(source).get("sources", {}))


def day_type_index(dayofweek) -> np.ndarray:
    """Index into :data:`DAY_TYPES` for each pandas ``dayofweek`` (Mon–Fri → 0,
    Sat → 1, Sun → 2)."""
    dow = np.asarray(dayofweek, dtype=int)
    return np.where(dow == 5, 1, np.where(dow == 6, 2, 0))


def _local_hours_of_day(dates, tz) -> dict:
    """``{local date: array of the local hours that date actually has}``.

    24 on most days; 23 on the spring-forward day (the skipped hour is absent) and 25
    on the fall-back day (the repeated hour appears twice).
    """
    out = {}
    for d in dates:
        start = pd.Timestamp(d).tz_localize(tz, ambiguous=True, nonexistent="shift_forward")
        end = (pd.Timestamp(d) + pd.Timedelta(1, "D")).tz_localize(
            tz, ambiguous=True, nonexistent="shift_forward")
        out[d] = pd.date_range(start, end, freq="h", inclusive="left").hour.to_numpy()
    return out


def bin_volume_factor(profile: VolumeProfile, local_ts, *, bin_minutes: int = 5) -> pd.Series:
    """The share of an average day's volume in each ``bin_minutes`` bin.

    Each timestamp is the **start** of a bin, read on the local wall clock. Its factor
    is

        dow[weekday] × hourly[day type][local hour] / S(date) / (60 / bin_minutes)

    where ``S(date)`` is the sum of the day type's hourly shares over the hours that
    local date actually has. On an ordinary day ``S`` is 1. On the 23-hour
    spring-forward day it is 1 − the skipped hour's share, and on the 25-hour fall-back
    day 1 + the repeated hour's share. So, whatever the day's length, the factors of a
    complete day's bins sum to that day's DOW factor, and the day's volume is not
    gained or lost to the clock change.

    The factor depends only on each timestamp, never on which other bins are present,
    so it is safe on a window, a sparse index, or a whole export.

    Args:
        profile: the curve.
        local_ts: a tz-aware ``DatetimeIndex`` or datetime ``Series`` in the
            corridor's **local** zone (``io.to_local``). The hour of day is read from
            it as given, so a UTC index would put every bin at the wrong hour.
        bin_minutes: the bin width; must divide 60.

    Returns:
        A float ``Series``, indexed like a ``Series`` input, or by the timestamps for
        a ``DatetimeIndex``.

    Raises:
        ValueError: ``local_ts`` is tz-naive, or ``bin_minutes`` does not divide 60.
    """
    if bin_minutes <= 0 or 60 % bin_minutes:
        raise ValueError(f"bin_minutes must divide 60, got {bin_minutes}")
    if isinstance(local_ts, pd.Series):
        ts = pd.DatetimeIndex(local_ts)
        index = local_ts.index
    else:
        ts = pd.DatetimeIndex(local_ts)
        index = ts
    if ts.tz is None:
        raise ValueError("local_ts must be tz-aware local time (run io.to_local first); "
                         "a naive clock cannot place the DST change or the hour of day")

    hourly = profile.hourly_array()
    dow = np.asarray(profile.dow, dtype=float)
    dayofweek = ts.dayofweek.to_numpy()
    dtype = day_type_index(dayofweek)
    hour = ts.hour.to_numpy()

    # S(date) once per distinct local date, then broadcast by code: an export has
    # millions of bins but only a few hundred dates.
    codes, uniq = pd.factorize(ts.tz_localize(None).normalize())
    hours_by_day = _local_hours_of_day(uniq, ts.tz)
    uniq_type = day_type_index(pd.DatetimeIndex(uniq).dayofweek)
    day_sum = np.array([hourly[t, hours_by_day[d]].sum() for d, t in zip(uniq, uniq_type)],
                       dtype=float)
    s = day_sum[codes]

    factor = dow[dayofweek] * hourly[dtype, hour] / s / (60 // bin_minutes)
    return pd.Series(factor, index=index, name="volume_factor")


# ---------------------------------------------------------------------------
# Calendar volume weights over a data period  (Item 57)
# ---------------------------------------------------------------------------
WEIGHT_COLUMNS = ("curve_id", "window", "month", "day_type", "tod_min", "volume_days")


def _window_spec(w) -> tuple[str, str, frozenset | None]:
    """``(name, clock range, gated dayofweek set or None)`` from a ``screen.PeakWindow``
    or its ``to_dict`` form (what a screen's ``attrs['windows']`` holds). Duck-typed so
    this module does not import ``screen``."""
    from .timebins import parse_day_of_week
    if isinstance(w, Mapping):
        name, clock, days = w["name"], w["window"], w.get("days")
    else:
        name, clock, days = w.name, w.window, w.days
    if days is None:
        return name, clock, None
    dows = frozenset(parse_day_of_week(d) for d in days)
    return name, clock, (None if not dows or dows == frozenset(range(7)) else dows)


def _window_mask(ts: pd.DatetimeIndex, clock: str, dows) -> np.ndarray:
    """``PeakWindow.filter`` semantics on bin-start timestamps: half-open clock range
    read on the local wall clock (overnight ranges wrap), then the day gate on the
    bin's own date."""
    from .timebins import parse_time_bin
    _, start, end, overnight = parse_time_bin(clock)
    tod = ts.hour.to_numpy() * 3600 + ts.minute.to_numpy() * 60 + ts.second.to_numpy()
    if start == end:
        mask = np.ones(len(ts), dtype=bool)
    elif overnight:
        mask = (tod >= start) | (tod < end)
    else:
        mask = (tod >= start) & (tod < end)
    if dows is not None:
        mask &= np.isin(ts.dayofweek.to_numpy(), sorted(dows))
    return mask


def day_basis(dows) -> str:
    """What a window's VHD is *per*: ``"day"`` (ungated), ``"weekday"`` (Mon–Fri),
    ``"weekend day"`` (Sat–Sun), else ``"gated day"``."""
    if dows is None:
        return "day"
    dows = frozenset(dows)
    if dows == frozenset(range(5)):
        return "weekday"
    if dows == frozenset({5, 6}):
        return "weekend day"
    return "gated day"


def window_volume_weights(profiles, windows, period_start, period_end, tz, *,
                          bin_minutes: int = 5) -> pd.DataFrame:
    """How much of a segment's daily volume each delay cell carries over a data period.

    Curve-weighted VHD (``aadt.curve_vehicle_hours_of_delay``) reads delay per cell,
    one cell per **month × day type × bin of the day**, pooled over the days of that
    type in that month. This returns each cell's volume weight: over every local
    calendar day ``d`` of the period that falls in the cell and passes the window,

        volume_days = Σ_d  bin_volume_factor(profile, bin on d)

    so ``dirAADT × MADT_month/AADT × volume_days`` is the traffic that crossed the
    segment in that cell during the whole period. Dividing by the window's days,
    ``attrs['window_days'][window]`` (the period's days its day gate covers: the
    weekdays for a weekday window, every day for an ungated one), gives it per
    average day **of the window**. The DOW factor of each day, the day
    type's hourly shape and the 23/25-hour DST days are all inside
    :func:`bin_volume_factor`.

    Args:
        profiles: ``{curve_id: VolumeProfile}`` (:func:`load_profiles`).
        windows: ``screen.PeakWindow``s (or their ``to_dict`` form), a mapping of
            them, or a single one. Membership follows ``PeakWindow.filter``: the bin's
            start on the local clock, half-open, with the day gate on its own date.
        period_start / period_end: the first and last **local** calendar dates of the
            data period, inclusive.
        tz: the local zone (IANA name) the bins are read in.
        bin_minutes: the bin width; must divide 60 and match the delay cells.

    Returns:
        A long frame with :data:`WEIGHT_COLUMNS` — ``curve_id``, ``window``, ``month``
        (``"YYYY-MM"``), ``day_type`` (:data:`DAY_TYPES`), ``tod_min`` (the bin's start,
        minutes after local midnight) and ``volume_days``. Only cells a window covers
        appear. ``attrs`` records ``period_start`` / ``period_end`` (ISO dates),
        ``n_days``, ``days_by_month`` (``{"YYYY-MM": n}``), ``window_days`` /
        ``window_days_by_month`` (the same counts over the days each window's gate
        covers), ``window_per`` (:func:`day_basis` of each window), ``tz``,
        ``bin_minutes`` and ``windows`` (the specs, as read).

    Raises:
        ValueError: the period is empty (end before start), or ``bin_minutes`` does
            not divide 60.
    """
    if bin_minutes <= 0 or 60 % bin_minutes:
        raise ValueError(f"bin_minutes must divide 60, got {bin_minutes}")
    first = pd.Timestamp(period_start).normalize()
    last = pd.Timestamp(period_end).normalize()
    if first.tzinfo is not None or last.tzinfo is not None:
        raise ValueError("period_start / period_end are local calendar dates; pass them "
                         "without a zone")
    if last < first:
        raise ValueError(f"empty period: {first.date()} .. {last.date()}")
    if isinstance(windows, Mapping) and "window" not in windows:
        windows = list(windows.values())
    elif isinstance(windows, Mapping) or not isinstance(windows, (list, tuple)):
        windows = [windows]
    specs = [_window_spec(w) for w in windows]

    # Every bin start of the period on the local wall clock. The range is built in
    # absolute time, so the spring-forward day has no 02:00 hour and the fall-back day
    # has its 01:00 hour twice, as the data does.
    start = first.tz_localize(tz, ambiguous=True, nonexistent="shift_forward")
    end = (last + pd.Timedelta(1, "D")).tz_localize(tz, ambiguous=True,
                                                     nonexistent="shift_forward")
    ts = pd.date_range(start, end, freq=f"{bin_minutes}min", inclusive="left")
    naive = ts.tz_localize(None)
    month = naive.strftime("%Y-%m")
    day_type = np.asarray(DAY_TYPES)[day_type_index(ts.dayofweek)]
    tod_min = (ts.hour.to_numpy() * 60 + ts.minute.to_numpy()) // bin_minutes * bin_minutes

    frames = []
    for curve_id, prof in profiles.items():
        factor = bin_volume_factor(prof, ts, bin_minutes=bin_minutes).to_numpy()
        for name, clock, dows in specs:
            mask = _window_mask(ts, clock, dows)
            if not mask.any():
                continue
            part = pd.DataFrame({"month": month[mask], "day_type": day_type[mask],
                                 "tod_min": tod_min[mask], "volume_days": factor[mask]})
            part = (part.groupby(["month", "day_type", "tod_min"], sort=True)["volume_days"]
                    .sum().reset_index())
            part.insert(0, "window", name)
            part.insert(0, "curve_id", curve_id)
            frames.append(part)
    out = (pd.concat(frames, ignore_index=True) if frames
           else pd.DataFrame(columns=list(WEIGHT_COLUMNS)))
    out["tod_min"] = out["tod_min"].astype("int64")
    days = pd.date_range(first, last, freq="D")
    months = pd.Series(days.strftime("%Y-%m"))
    window_days, window_days_by_month = {}, {}
    for name, _, dows in specs:
        gate = (np.ones(len(days), dtype=bool) if dows is None
                else np.isin(days.dayofweek, sorted(dows)))
        window_days[name] = int(gate.sum())
        window_days_by_month[name] = {k: int(v) for k, v in
                                      months[gate].value_counts().sort_index().items()}
    out.attrs = {
        "period_start": first.date().isoformat(),
        "period_end": last.date().isoformat(),
        "n_days": len(days),
        "days_by_month": {k: int(v) for k, v in
                          months.value_counts().sort_index().items()},
        "window_days": window_days,
        "window_days_by_month": window_days_by_month,
        "window_per": {name: day_basis(dows) for name, _, dows in specs},
        "tz": str(tz),
        "bin_minutes": int(bin_minutes),
        "windows": {name: {"window": clock, "days": None if dows is None else sorted(dows)}
                    for name, clock, dows in specs},
    }
    return out[list(WEIGHT_COLUMNS)]

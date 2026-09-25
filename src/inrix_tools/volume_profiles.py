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

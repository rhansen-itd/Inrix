"""Traffic counts → volume-profile curves.  (ROADMAP Item 59)

Items 55–58 weight VHD by *generic* 24-hour curves. This module brings measured
counts in and fits curves from them:

1. **One schema** for hourly directional counts (:data:`COUNT_COLUMNS`,
   :func:`validate_counts`, :func:`read_counts` / :func:`write_counts`). Every
   importer produces it, so the fitting never sees a source's quirks.
2. **Importers**:

   * :func:`import_tcds_hourly` — ITD's permanent count stations (ATRs), as the
     MS2Soft TCDS "Volume by Hour by Day for Month" report (ReportId 87) flattened
     by ``tcds-scraper/tidy.py`` into one long CSV, plus the TCDS station list for
     the coordinates and route;
   * :func:`import_interval_counts` — a 24-hour (or multi-day) tube count in the
     usual wide interval layout: one row per 15-minute (or hourly) interval, one
     volume column per direction;
   * :func:`import_atspm_volumes` — signal detector volumes (ATSPM approach volumes):
     one row per signal × approach direction × bin, plus a signal location table.

3. :func:`fit_profile` — one station-direction's counts → a
   :class:`~inrix_tools.volume_profiles.VolumeProfile` (``basis = "fitted"``). Day
   types without data, and the DOW factors without a full week, are borrowed from a
   generic curve (by default the one whose shape is nearest the fitted part).
4. :func:`nearest_generic` / :func:`cluster_profiles` — which generic curve each
   fitted curve is closest to, and by how much (the share of a day's volume that
   sits in a different hour), so a fitted curve can be mapped back onto a generic id.
5. :func:`station_curves` — all of it for a whole count set: the fitted library plus
   one row per station-direction for the station rule
   (``profile_assignment.station_rule``).

Pure: pandas/numpy only, no file paths of its own.
"""
from __future__ import annotations

import math
import re
from pathlib import Path
from typing import Mapping

import numpy as np
import pandas as pd

from .volume_profiles import DAY_TYPES, VolumeProfile, day_type_index

# ---------------------------------------------------------------------------
# The count schema
# ---------------------------------------------------------------------------
COUNT_COLUMNS = ("station_id", "direction", "timestamp", "volume", "lat", "lon",
                 "route", "source")
"""One row per station × direction × local hour.

* ``station_id`` — the source's id, as a string (``"00279"``, ``"tube:Hwy 16"``).
* ``direction`` — the direction of **travel**: ``NB SB EB WB NE NW SE SW``
  (:data:`DIRECTIONS`), or ``2WAY`` for a both-directions total.
* ``timestamp`` — the **start** of the hour, tz-aware on the local clock (on disk:
  ISO 8601 with the UTC offset, ``2026-04-01T07:00:00-06:00``).
* ``volume`` — vehicles in that hour, ≥ 0.
* ``lat`` / ``lon`` — the station, WGS84.
* ``route`` — the state route it counts, ``"84"`` / ``"55"``; a business loop is
  ``"84 BL"``; blank when unknown (the station rule then cannot use it).
* ``source`` — :data:`SOURCES`.
"""
SOURCES = ("atr", "tube", "atspm")

DIRECTIONS = ("NB", "SB", "EB", "WB", "NE", "NW", "SE", "SW")
TWO_WAY = "2WAY"
DIRECTION_BEARING = {"NB": 0.0, "NE": 45.0, "EB": 90.0, "SE": 135.0,
                     "SB": 180.0, "SW": 225.0, "WB": 270.0, "NW": 315.0}
"""Compass bearing of travel for each direction label. ITD labels some stations on
diagonal roads NW/SE or NE/SW; the bearing lets the station rule match them against
an XD segment's own travel bearing rather than its N/S/E/W letter."""

_DIRECTION_WORDS = {
    "N": "NB", "NB": "NB", "NORTH": "NB", "NORTHBOUND": "NB",
    "S": "SB", "SB": "SB", "SOUTH": "SB", "SOUTHBOUND": "SB",
    "E": "EB", "EB": "EB", "EAST": "EB", "EASTBOUND": "EB",
    "W": "WB", "WB": "WB", "WEST": "WB", "WESTBOUND": "WB",
    "NE": "NE", "NEB": "NE", "NORTHEAST": "NE", "NORTHEASTBOUND": "NE",
    "NW": "NW", "NWB": "NW", "NORTHWEST": "NW", "NORTHWESTBOUND": "NW",
    "SE": "SE", "SEB": "SE", "SOUTHEAST": "SE", "SOUTHEASTBOUND": "SE",
    "SW": "SW", "SWB": "SW", "SOUTHWEST": "SW", "SOUTHWESTBOUND": "SW",
    "2WAY": TWO_WAY, "2-WAY": TWO_WAY, "TWOWAY": TWO_WAY, "BOTH": TWO_WAY,
    "TOTAL": TWO_WAY,
}


def normalise_direction(label) -> str:
    """A direction label → :data:`DIRECTIONS` or ``2WAY``.

    Accepts ``NB`` / ``N`` / ``North`` / ``Northbound`` (and the diagonals),
    ``2-WAY`` / ``Both`` / ``Total``; case and spaces are ignored.

    Raises:
        ValueError: the label is not a direction.
    """
    key = re.sub(r"[\s_.]", "", str(label)).upper()
    if key not in _DIRECTION_WORDS:
        raise ValueError(f"not a direction label: {label!r}")
    return _DIRECTION_WORDS[key]


_ROUTE_ON = re.compile(r"^\s*(?:INT|I|US|SH|SR|ID)[\s-]*(\d+)\s*(BL|BUS|B)?\b", re.I)


def parse_route(on=None, description=None) -> str:
    """A station's state route from TCDS text: the ``On`` field (``"INT 84 MAIN"``,
    ``"US 20 MAIN"``, ``"INT 84 BL"``), else the start of its description
    (``"SH-55 .38 mi S of Chinden Rd."``). Returns ``"84"``, ``"84 BL"`` or ``""``."""
    for text in (on, description):
        if text is None or (isinstance(text, float) and math.isnan(text)):
            continue
        m = _ROUTE_ON.match(str(text))
        if m:
            return m.group(1).lstrip("0") + (" BL" if m.group(2) else "")
    return ""


def split_route(route) -> tuple[str | None, bool]:
    """``"84 BL"`` → ``("84", True)``; ``"55"`` → ``("55", False)``; blank → ``(None,
    False)``."""
    text = "" if route is None or (isinstance(route, float) and math.isnan(route)) \
        else str(route).strip()
    if not text:
        return None, False
    parts = text.split()
    return parts[0].lstrip("0") or "0", len(parts) > 1 and parts[1].upper() == "BL"


def validate_counts(counts: pd.DataFrame) -> pd.DataFrame:
    """Check a frame against :data:`COUNT_COLUMNS` and return it in canonical form
    (column order, dtypes, sorted, one row per station × direction × hour).

    Raises:
        ValueError: a missing column; a naive timestamp or one not on the hour; an
            unknown direction or source; a negative or non-finite volume; a missing
            lat/lon; or a station × direction × hour that repeats.
    """
    missing = [c for c in COUNT_COLUMNS if c not in counts.columns]
    if missing:
        raise ValueError(f"counts lack columns {missing}; the schema is {COUNT_COLUMNS}")
    out = counts[list(COUNT_COLUMNS)].copy()
    ts = pd.to_datetime(out["timestamp"])
    if getattr(ts.dt, "tz", None) is None:
        raise ValueError("count timestamps must be tz-aware local time")
    if ((ts.dt.minute != 0) | (ts.dt.second != 0)).any():
        raise ValueError("count timestamps must be hour starts (hourly counts)")
    out["timestamp"] = ts
    out["station_id"] = out["station_id"].astype(str)
    out["direction"] = out["direction"].astype(str)
    bad = sorted(set(out["direction"]) - set(DIRECTIONS) - {TWO_WAY})
    if bad:
        raise ValueError(f"unknown direction {bad}; use normalise_direction()")
    bad = sorted(set(out["source"].astype(str)) - set(SOURCES))
    if bad:
        raise ValueError(f"unknown source {bad}; one of {SOURCES}")
    out["source"] = out["source"].astype(str)
    vol = pd.to_numeric(out["volume"], errors="coerce")
    if vol.isna().any() or not np.isfinite(vol).all() or (vol < 0).any():
        raise ValueError("volume must be a finite number >= 0 on every row")
    out["volume"] = vol.astype(float)
    for c in ("lat", "lon"):
        out[c] = pd.to_numeric(out[c], errors="coerce")
    if out[["lat", "lon"]].isna().any().any():
        raise ValueError("every count row needs the station's lat/lon")
    out["route"] = out["route"].fillna("").astype(str).str.strip()
    dup = out.duplicated(["station_id", "direction", "timestamp"])
    if dup.any():
        raise ValueError(f"{int(dup.sum())} station x direction x hour rows repeat; "
                         "sum lanes/detectors before building the schema")
    return out.sort_values(["station_id", "direction", "timestamp"]).reset_index(drop=True)


def write_counts(counts: pd.DataFrame, path) -> Path:
    """Write schema counts as CSV, timestamps ISO 8601 with their UTC offset."""
    out = validate_counts(counts)
    out["timestamp"] = out["timestamp"].map(lambda t: t.isoformat())
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(path, index=False)
    return path


def read_counts(path, tz: str) -> pd.DataFrame:
    """Read :func:`write_counts` output, timestamps converted to ``tz``."""
    frame = pd.read_csv(path, dtype={"station_id": str, "route": str})
    frame["timestamp"] = pd.to_datetime(frame["timestamp"], utc=True).dt.tz_convert(tz)
    return validate_counts(frame)


def _local_hours(dates, hours, tz) -> pd.Series:
    """Wall-clock ``date`` + ``hour`` → tz-aware hour starts. The hour a spring-forward
    day skips is NaT; the fall-back day's repeated hour reads as its first (DST)
    occurrence — a report with one row per wall-clock hour cannot say which."""
    naive = pd.to_datetime(pd.Series(dates).astype(str)) + pd.to_timedelta(
        pd.Series(hours).astype(int), unit="h")
    return naive.dt.tz_localize(tz, ambiguous=np.ones(len(naive), dtype=bool),
                                nonexistent="NaT")


# ---------------------------------------------------------------------------
# Importers
# ---------------------------------------------------------------------------
_TCDS_SERIES = re.compile(r"^(?P<station>[^_]+)(?:_(?P<lane>\d+))?(?:_(?P<dir>[A-Z]{1,2}))?$")


def import_tcds_hourly(hourly: pd.DataFrame, stations: pd.DataFrame, tz: str) -> pd.DataFrame:
    """ITD ATR hourly volumes (TCDS report 87, flattened by ``tidy.py``) → the schema.

    Each TCDS workbook holds one sheet per *series*: the 2-way total (``00279``), each
    direction (``00279_EB``) and each lane (``00279_1_EB``). The direction series are
    kept. A station with no direction series keeps its 2-way total as ``2WAY`` (it
    fits a curve, but the station rule cannot orient it). Lane series are dropped:
    they sum to the direction series.

    Args:
        hourly: the tidy CSV — ``series_id``, ``direction``, ``date`` (local
            ``YYYY-MM-DD``), ``hour`` (0–23, the hour **starting** then: TCDS's
            ``12-1A`` is 0) and ``volume``.
        stations: the TCDS station list — ``local_id``, ``lat``, ``lon``, and ``on`` /
            ``at`` (the route is parsed from them, :func:`parse_route`).
        tz: the stations' local zone.

    Returns:
        Schema counts, ``source = "atr"``. The spring-forward day's skipped hour is
        dropped. ``attrs['tcds']`` lists the stations without coordinates (dropped)
        and those kept as 2-way only.
    """
    need = {"series_id", "direction", "date", "hour", "volume"}
    if need - set(hourly.columns):
        raise ValueError(f"TCDS hourly lacks {sorted(need - set(hourly.columns))}")
    h = hourly.copy()
    h["series_id"] = h["series_id"].astype(str)
    parts = h["series_id"].str.extract(_TCDS_SERIES)
    h["station_id"] = parts["station"]
    h["lane"] = parts["lane"]
    h["dir_part"] = parts["dir"]
    h = h[h["lane"].isna()]
    directional = h[h["dir_part"].notna()]
    totals = h[h["dir_part"].isna()]
    two_way_only = sorted(set(totals["station_id"]) - set(directional["station_id"]))
    directional = directional.assign(
        direction=directional["dir_part"].map(normalise_direction))
    totals = totals[totals["station_id"].isin(two_way_only)].assign(direction=TWO_WAY)
    h = pd.concat([directional, totals], ignore_index=True)

    st = stations.copy()
    st["local_id"] = st["local_id"].astype(str).str.zfill(5)
    st = st.drop_duplicates("local_id").set_index("local_id")
    h["station_id"] = h["station_id"].str.zfill(5)
    no_coords = sorted(set(h["station_id"]) - set(st.index[st["lat"].notna()
                                                          & st["lon"].notna()]))
    h = h[~h["station_id"].isin(no_coords)]
    route = {sid: parse_route(r.get("on"), r.get("at")) for sid, r in st.iterrows()}

    ts = _local_hours(h["date"].to_numpy(), h["hour"].to_numpy(), tz)
    out = pd.DataFrame({
        "station_id": h["station_id"].to_numpy(),
        "direction": h["direction"].to_numpy(),
        "timestamp": ts.to_numpy(),
        "volume": pd.to_numeric(h["volume"], errors="coerce").to_numpy(),
        "lat": h["station_id"].map(st["lat"]).astype(float).to_numpy(),
        "lon": h["station_id"].map(st["lon"]).astype(float).to_numpy(),
        "route": h["station_id"].map(route).to_numpy(),
        "source": "atr",
    })
    out = out[out["timestamp"].notna() & out["volume"].notna()]
    out = (out.groupby(["station_id", "direction", "timestamp"], as_index=False)
           .agg({"volume": "sum", "lat": "first", "lon": "first", "route": "first",
                 "source": "first"}))
    out = validate_counts(out)
    out.attrs["tcds"] = {"dropped_no_coordinates": no_coords,
                         "two_way_only": two_way_only}
    return out


def _to_hourly(frame: pd.DataFrame, keys: list[str], ts_col: str, bin_minutes: int
               ) -> pd.DataFrame:
    """Sum interval volumes to local hours, keeping only hours with every interval
    present (a partial hour would read as a dip in the curve)."""
    f = frame.copy()
    f["_hour"] = f[ts_col].dt.floor("h")
    per_hour = 60 // bin_minutes
    agg = f.groupby(keys + ["_hour"], as_index=False).agg(
        volume=("volume", "sum"), _bins=(ts_col, "nunique"))
    complete = agg["_bins"] >= per_hour
    agg = agg[complete].drop(columns="_bins").rename(columns={"_hour": "timestamp"})
    agg.attrs["partial_hours_dropped"] = int((~complete).sum())
    return agg


def _localise(values, tz) -> pd.Series:
    ts = pd.to_datetime(pd.Series(values))
    if getattr(ts.dt, "tz", None) is None:
        return ts.dt.tz_localize(tz, ambiguous="NaT", nonexistent="NaT")
    return ts.dt.tz_convert(tz)


def import_interval_counts(frame: pd.DataFrame, *, station_id: str, lat: float,
                           lon: float, tz: str, direction_cols: Mapping[str, str],
                           time_col: str = "time", date_col: str | None = None,
                           bin_minutes: int = 15, route: str = "",
                           source: str = "tube") -> pd.DataFrame:
    """A tube (or any short) count in the wide interval layout → the schema.

    The layout most count-board exports use: one row per interval, the interval's
    start in ``time_col`` (a full datetime, or a clock time with the date in
    ``date_col``), and one volume column per direction.

    Args:
        frame: the count table.
        station_id / lat / lon / route: the count's location (not in the table).
        tz: the local zone. Naive times are read on that wall clock; the ambiguous
            fall-back hour and the skipped spring-forward hour are dropped.
        direction_cols: ``{column: direction label}``, e.g. ``{"NB Vol": "NB"}``.
        bin_minutes: the interval width; must divide 60. Hours missing an interval
            are dropped, not scaled up.
        source: ``"tube"`` (default) or another :data:`SOURCES` value.

    Returns:
        Schema counts; ``attrs['partial_hours_dropped']``.
    """
    if bin_minutes <= 0 or 60 % bin_minutes:
        raise ValueError(f"bin_minutes must divide 60, got {bin_minutes}")
    when = (frame[date_col].astype(str) + " " + frame[time_col].astype(str)
            if date_col else frame[time_col])
    ts = _localise(when, tz)
    long = []
    for col, label in direction_cols.items():
        long.append(pd.DataFrame({"_ts": ts.to_numpy(), "direction": normalise_direction(label),
                                  "volume": pd.to_numeric(frame[col], errors="coerce")
                                  .to_numpy()}))
    f = pd.concat(long, ignore_index=True)
    f = f[f["_ts"].notna() & f["volume"].notna()]
    f["_ts"] = pd.DatetimeIndex(f["_ts"]).tz_convert(tz)
    hourly = _to_hourly(f, ["direction"], "_ts", bin_minutes)
    out = hourly.assign(station_id=str(station_id), lat=float(lat), lon=float(lon),
                        route=str(route), source=source)
    out = validate_counts(out)
    out.attrs["partial_hours_dropped"] = hourly.attrs["partial_hours_dropped"]
    return out


def import_atspm_volumes(frame: pd.DataFrame, signals: pd.DataFrame, tz: str, *,
                         signal_col: str = "SignalId", time_col: str = "BinStartTime",
                         direction_col: str = "Direction", volume_col: str = "Volume",
                         bin_minutes: int = 15) -> pd.DataFrame:
    """Signal detector volumes (ATSPM) → the schema.

    One input row per signal × approach direction × bin — ATSPM's approach-volume
    export, or its per-detector aggregation (the rows of one signal, direction and
    bin, e.g. one per lane or detector, are summed). The approach direction is the
    direction of **travel** (a Northbound approach carries NB traffic).

    Args:
        frame: the volume table (column names are parameters).
        signals: ``SignalId`` (the same ids), ``lat``, ``lon`` and optionally
            ``route`` (:data:`COUNT_COLUMNS`' ``route`` form).
        tz: the local zone; ATSPM timestamps are the controller's local clock.
        bin_minutes: the bin width; hours missing a bin are dropped.

    Returns:
        Schema counts, ``source = "atspm"``, one station per signal.
        ``attrs['partial_hours_dropped']``, ``attrs['signals_without_location']``.

    Caveat: a stop-bar or advance detector counts one approach at the intersection,
    turning traffic included, and a missing lane detector undercounts. The fitted
    *shape* tolerates a steady undercount; a detector that drops out for hours does
    not, which is why partial hours and outage days are dropped.
    """
    if bin_minutes <= 0 or 60 % bin_minutes:
        raise ValueError(f"bin_minutes must divide 60, got {bin_minutes}")
    sig = signals.copy()
    sig["SignalId"] = sig["SignalId"].astype(str)
    sig = sig.drop_duplicates("SignalId").set_index("SignalId")
    f = pd.DataFrame({"station_id": frame[signal_col].astype(str).to_numpy(),
                      "direction": frame[direction_col].map(normalise_direction).to_numpy(),
                      "_ts": _localise(frame[time_col], tz).to_numpy(),
                      "volume": pd.to_numeric(frame[volume_col], errors="coerce").to_numpy()})
    unknown = sorted(set(f["station_id"]) - set(sig.index))
    f = f[f["station_id"].isin(sig.index) & f["_ts"].notna() & f["volume"].notna()]
    f["_ts"] = pd.DatetimeIndex(f["_ts"]).tz_convert(tz)
    # Detectors / lanes of one approach in one bin → one bin volume first.
    f = f.groupby(["station_id", "direction", "_ts"], as_index=False)["volume"].sum()
    hourly = _to_hourly(f, ["station_id", "direction"], "_ts", bin_minutes)
    route = sig["route"] if "route" in sig.columns else pd.Series("", index=sig.index)
    out = hourly.assign(lat=hourly["station_id"].map(sig["lat"]).astype(float),
                        lon=hourly["station_id"].map(sig["lon"]).astype(float),
                        route=hourly["station_id"].map(route).fillna("").astype(str),
                        source="atspm")
    out = validate_counts(out)
    out.attrs["partial_hours_dropped"] = hourly.attrs["partial_hours_dropped"]
    out.attrs["signals_without_location"] = unknown
    return out


# ---------------------------------------------------------------------------
# Fitting
# ---------------------------------------------------------------------------
OUTAGE_FRACTION = 0.5
"""A complete day whose total is below this share of the median total of its day
type (at that station-direction) is an outage or a closure, not a traffic day, and
is left out of the fit."""
MIN_DAYS_PER_TYPE = 1
"""Complete days a day type needs before its hourly shape is fitted, not borrowed."""
DOW_MIN_SPAN_DAYS = 7
"""The DOW factors are fitted only from at least a week of complete days that
covers every day of the week; otherwise they are borrowed."""

DAY_TYPE_WEIGHT = {"weekday": 5 / 7, "sat": 1 / 7, "sun": 1 / 7}
"""How much each day type counts when two curves are compared (its share of a week)."""


def daily_matrix(counts: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """One station-direction's counts → ``(complete, dropped)``.

    ``complete``: one row per usable local date (index), columns hour 0..23, plus
    ``total``, ``dayofweek`` and ``day_type``. A day is usable when it is a 24-hour
    day (not a DST changeover), has all 24 hours, and its total is at least
    :data:`OUTAGE_FRACTION` of its day type's median. ``dropped``: the other dates
    with ``reason``.
    """
    ts = pd.DatetimeIndex(counts["timestamp"])
    tz = ts.tz
    frame = pd.DataFrame({"date": ts.tz_localize(None).normalize(), "hour": ts.hour,
                          "volume": counts["volume"].to_numpy(float)})
    wide = frame.pivot_table(index="date", columns="hour", values="volume", aggfunc="sum")
    wide = wide.reindex(columns=range(24))
    dates = wide.index
    length = [((d + pd.Timedelta(1, "D")).tz_localize(tz, ambiguous=True,
                                                         nonexistent="shift_forward")
               - d.tz_localize(tz, ambiguous=True, nonexistent="shift_forward"))
              / pd.Timedelta(1, "h") for d in dates]
    reason = pd.Series("", index=dates, dtype=object)
    reason[np.asarray(length) != 24] = "DST changeover day"
    reason[(reason == "") & wide.isna().any(axis=1)] = "incomplete (missing hours)"
    wide["total"] = wide[list(range(24))].sum(axis=1)
    wide["dayofweek"] = dates.dayofweek
    wide["day_type"] = np.asarray(DAY_TYPES)[day_type_index(dates.dayofweek)]
    ok = reason == ""
    med = wide[ok].groupby("day_type")["total"].median()
    low = ok & (wide["total"] < OUTAGE_FRACTION * wide["day_type"].map(med))
    reason[low] = f"outage (total < {OUTAGE_FRACTION:g} x its day type's median)"
    ok = reason == ""
    dropped = pd.DataFrame({"total": wide.loc[~ok, "total"], "reason": reason[~ok]})
    return wide[ok], dropped


def _shapes(complete: pd.DataFrame) -> dict[str, np.ndarray | None]:
    """Volume-weighted hourly shape per day type: Σ_days vol[h] / Σ_days total.
    ``None`` where the day type has fewer than :data:`MIN_DAYS_PER_TYPE` days."""
    out = {}
    for t in DAY_TYPES:
        d = complete[complete["day_type"] == t]
        total = float(d["total"].sum())
        out[t] = (d[list(range(24))].sum(axis=0).to_numpy(float) / total
                  if len(d) >= MIN_DAYS_PER_TYPE and total > 0 else None)
    return out


def _dow(complete: pd.DataFrame) -> np.ndarray | None:
    if len(complete) < DOW_MIN_SPAN_DAYS or complete["dayofweek"].nunique() < 7:
        return None
    means = complete.groupby("dayofweek")["total"].mean().reindex(range(7)).to_numpy(float)
    return means / means.mean()


def _shape_distance(a: Mapping, b: VolumeProfile, types) -> float:
    """Share of a day's volume that sits in a different hour, week-weighted over
    ``types``: ½ Σ_types w_t Σ_h |a_t(h) − b_t(h)| / Σ w_t (0 = identical, 1 =
    disjoint)."""
    w = sum(DAY_TYPE_WEIGHT[t] for t in types)
    return float(sum(DAY_TYPE_WEIGHT[t] * 0.5 * np.abs(np.asarray(a[t]) -
                                                        np.asarray(b.hourly[t])).sum()
                     for t in types) / w)


def fit_profile(counts: pd.DataFrame, *, curve_id: str | None = None,
                fallback: VolumeProfile | None = None,
                library: Mapping[str, VolumeProfile] | None = None,
                description: str = "") -> VolumeProfile:
    """Fit a :class:`VolumeProfile` from one station-direction's hourly counts.

    * **Hourly shapes.** Per day type (weekday / Sat / Sun), the volume-weighted mean
      over its usable days (:func:`daily_matrix`): Σ_days vol(h) / Σ_days total.
      Busy days count for more, which is what a volume weight should do.
    * **DOW factors** only from at least :data:`DOW_MIN_SPAN_DAYS` usable days that
      include every day of the week: each weekday's mean total over the mean of the
      seven. A shorter count borrows them.
    * **Borrowing.** A day type with no usable day (a 24–48 hour weekday tube count
      has no weekend) and short-count DOW factors come from ``fallback``. Without
      one, the generic curve in ``library`` whose shape is nearest the fitted day
      types (:func:`nearest_generic`) is used. ``provenance`` says what was borrowed
      and from which curve.

    A month of counts is one month: its DOW factors are that month's, and the curve is
    not seasonally adjusted (the MADT ratios already carry the month, Item 55).

    Args:
        counts: schema rows for **one** station and direction.
        curve_id: default ``fitted_<station>_<direction>``.
        fallback / library: see *Borrowing*. One is needed unless the count fills every
            day type and a full week.

    Raises:
        ValueError: several stations or directions; no usable day; or something must
            be borrowed and neither ``fallback`` nor ``library`` is given.
    """
    counts = validate_counts(counts)
    keys = counts[["station_id", "direction"]].drop_duplicates()
    if len(keys) != 1:
        raise ValueError(f"fit_profile takes one station-direction, got {len(keys)}")
    station, direction = keys.iloc[0]
    complete, dropped = daily_matrix(counts)
    if complete.empty:
        raise ValueError(f"{station} {direction}: no usable day to fit")
    shapes = _shapes(complete)
    dow = _dow(complete)
    fitted_types = [t for t in DAY_TYPES if shapes[t] is not None]
    borrowed = [t for t in DAY_TYPES if shapes[t] is None] + (["dow"] if dow is None else [])

    donor = fallback
    if borrowed and donor is None:
        if not library:
            raise ValueError(f"{station} {direction}: {borrowed} must be borrowed; pass "
                             "fallback= or library=")
        donor = library[min(library, key=lambda c: (
            _shape_distance(shapes, library[c], fitted_types), c))]
    hourly = {t: (shapes[t] if shapes[t] is not None else np.asarray(donor.hourly[t]))
              for t in DAY_TYPES}
    hourly = {t: tuple(float(v) for v in np.asarray(h) / np.sum(h)) for t, h in hourly.items()}
    dow_vals = np.asarray(donor.dow if dow is None else dow, dtype=float)
    dow_vals = tuple(float(v) for v in dow_vals / dow_vals.mean())

    ts = pd.DatetimeIndex(counts["timestamp"])
    n_by_type = complete["day_type"].value_counts().reindex(list(DAY_TYPES), fill_value=0)
    row = counts.iloc[0]
    provenance = {
        "basis": "fitted",
        "sources": [],
        "detail": (f"Fitted from {row['source']} counts at station {station} "
                   f"{direction}, {ts.min().date()} to {ts.max().date()}: "
                   f"{len(complete)} usable days "
                   f"({', '.join(f'{t} {int(n)}' for t, n in n_by_type.items())}), "
                   f"{len(dropped)} dropped."
                   + (f" Borrowed {borrowed} from {donor.curve_id}." if borrowed else "")),
        "station": {"station_id": str(station), "direction": str(direction),
                    "source": str(row["source"]), "lat": float(row["lat"]),
                    "lon": float(row["lon"]), "route": str(row["route"]),
                    "period_start": ts.min().date().isoformat(),
                    "period_end": ts.max().date().isoformat(),
                    "usable_days": {t: int(n) for t, n in n_by_type.items()},
                    "dropped_days": {d.date().isoformat(): r for d, r in
                                     dropped["reason"].items()},
                    "mean_daily_volume": round(float(complete["total"].mean()), 1)},
        "fitted": fitted_types + ([] if dow is None else ["dow"]),
        "borrowed": borrowed,
        "borrowed_from": donor.curve_id if borrowed else None,
    }
    return VolumeProfile(curve_id=curve_id or f"fitted_{station}_{direction}",
                         hourly=hourly, dow=dow_vals, provenance=provenance,
                         description=description or
                         f"Fitted from counts at {row['source'].upper()} {station} "
                         f"{direction}.")


def nearest_generic(profile: VolumeProfile, library: Mapping[str, VolumeProfile]
                    ) -> pd.DataFrame:
    """How far ``profile`` is from each curve of ``library``.

    Returns:
        Indexed by the library's ``curve_id``, sorted nearest first:
        ``misplaced_share`` (the week-weighted share of a day's volume in a different
        hour, :data:`DAY_TYPE_WEIGHT`; 0 = identical shapes), ``misplaced_weekday``
        (the weekday shape alone) and ``dow_mad`` (mean absolute difference of the DOW
        factors).
    """
    fitted = profile.hourly
    rows = []
    for cid, gen in library.items():
        if cid == profile.curve_id:
            continue
        rows.append({"curve_id": cid,
                     "misplaced_share": round(_shape_distance(fitted, gen, DAY_TYPES), 4),
                     "misplaced_weekday": round(_shape_distance(fitted, gen, ["weekday"]), 4),
                     "dow_mad": round(float(np.mean(np.abs(np.asarray(profile.dow)
                                                           - np.asarray(gen.dow)))), 4)})
    out = pd.DataFrame(rows).set_index("curve_id")
    return out.sort_values(["misplaced_share", "dow_mad"])


def cluster_profiles(fitted: Mapping[str, VolumeProfile],
                     library: Mapping[str, VolumeProfile]) -> pd.DataFrame:
    """Map each fitted curve back onto its nearest generic id.

    Returns:
        Indexed by the fitted ``curve_id``: ``nearest_generic``, ``misplaced_share``,
        ``second_generic``, ``second_misplaced_share`` and ``dow_mad`` (to the
        nearest).
    """
    rows = []
    for cid, prof in fitted.items():
        d = nearest_generic(prof, library)
        rows.append({"curve_id": cid, "nearest_generic": d.index[0],
                     "misplaced_share": d["misplaced_share"].iloc[0],
                     "second_generic": d.index[1] if len(d) > 1 else None,
                     "second_misplaced_share": (d["misplaced_share"].iloc[1]
                                                if len(d) > 1 else np.nan),
                     "dow_mad": d["dow_mad"].iloc[0]})
    return pd.DataFrame(rows).set_index("curve_id")


STATION_COLUMNS = ("station_id", "direction", "lat", "lon", "route", "source",
                   "curve_id", "nearest_generic", "misplaced_share", "usable_days",
                   "borrowed")


def station_curves(counts: pd.DataFrame, library: Mapping[str, VolumeProfile]
                   ) -> tuple[dict[str, VolumeProfile], pd.DataFrame]:
    """Fit every station-direction of ``counts``.

    Args:
        counts: schema counts, any number of stations.
        library: the generic curves (borrowing and clustering).

    Returns:
        ``(fitted, stations)``: ``{curve_id: VolumeProfile}``, and one row per fitted
        station-direction with :data:`STATION_COLUMNS` — the table
        ``profile_assignment.station_rule`` reads. A station-direction with no usable
        day is left out and listed in ``stations.attrs['unfitted']``.
    """
    counts = validate_counts(counts)
    fitted: dict[str, VolumeProfile] = {}
    rows, unfitted = [], {}
    for (sid, direction), part in counts.groupby(["station_id", "direction"], sort=True):
        try:
            prof = fit_profile(part, library=library)
        except ValueError as exc:
            unfitted[f"{sid} {direction}"] = str(exc)
            continue
        fitted[prof.curve_id] = prof
        near = nearest_generic(prof, library)
        first = part.iloc[0]
        rows.append({"station_id": sid, "direction": direction,
                     "lat": float(first["lat"]), "lon": float(first["lon"]),
                     "route": first["route"], "source": first["source"],
                     "curve_id": prof.curve_id, "nearest_generic": near.index[0],
                     "misplaced_share": near["misplaced_share"].iloc[0],
                     "usable_days": sum(prof.provenance["station"]["usable_days"].values()),
                     "borrowed": ";".join(prof.provenance["borrowed"])})
    stations = pd.DataFrame(rows, columns=list(STATION_COLUMNS))
    stations.attrs["unfitted"] = unfitted
    return fitted, stations


def write_stations(stations: pd.DataFrame, path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    stations[list(STATION_COLUMNS)].to_csv(path, index=False)
    return path


def read_stations(path) -> pd.DataFrame:
    """Read :func:`write_stations` output (ids and routes stay strings)."""
    frame = pd.read_csv(path, dtype={"station_id": str, "route": str, "borrowed": str})
    frame["route"] = frame["route"].fillna("")
    frame["borrowed"] = frame["borrowed"].fillna("")
    return frame[list(STATION_COLUMNS)]

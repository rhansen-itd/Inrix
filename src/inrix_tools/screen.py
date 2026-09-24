"""District-wide corridor screening: *which* corridors are worst?  (ROADMAP Item 35)

Everything else in this package is **corridor-scoped**: you name two endpoints and it
analyses that corridor. This module asks the prior question — across every segment in
an area, which corridors carry the most congestion — in two steps:

1. :func:`segment_screen` reduces an area's whole time series to **one row per
   segment**: per-named-window mean travel time and speed, the free-flow reference
   speed, and the CValue accounting. The reduction runs **in DuckDB** against
   :mod:`inrix_tools.store`'s ``obs_<area_key>`` table, so a district export never
   passes through pandas as a whole frame.
2. :func:`rank_corridors` turns that screen plus a set of assembled
   :class:`~inrix_tools.corridors.ChainResult` chains into the ranking metrics —
   ``delay_min`` / ``tti`` / ``delay_per_mile`` / ``vhd`` / ``vhd_per_mile`` and each
   corridor's ``worst_peak`` — computed through :func:`speed.segment_delay` and
   :func:`aadt.vehicle_hours_of_delay` rather than restated inline.

Three things this fixes against the outside screening pass it was scoped from
(DESIGN_HISTORY Session 38, ROADMAP Items 34–37):

- **Windows are the package's own vocabulary, not SQL.** A :class:`PeakWindow` is
  parsed by :func:`timebins.parse_time_bin` / :func:`timebins.parse_day_of_week`, and
  the SQL predicate is *derived* from that parse — so ``"7:00AM-9:00AM"`` means here
  exactly what it means in the GUI's time-of-day slider, half-open bins and
  overnight wrap included. :meth:`PeakWindow.filter` is the same window as a pandas
  filter, and the tests assert the two paths agree row for row.
- **The CValue gate is explicit and recorded.** The outside run skipped it entirely;
  on the 2026 D3 export **39.4%** of rows carry no CValue (mostly historical backfill —
  see DATA_FORMAT.md) and a gate that drops them is a *decision*. It is applied by
  default at ``CValue > 80`` (``io.DEFAULT_CVALUE_THRESHOLD``, the same strict
  comparison :func:`io.filter_cvalue` uses), recorded on
  ``attrs['cvalue_threshold']``, and the **surviving-row share per segment and per
  window** is carried in the output, so a screened-in segment built from backfill is
  visible rather than indistinguishable.
- **Delay is floored at exactly one level, and the level is named.** The outside run
  floored corridor delay at the corridor level while summing per-segment floored
  delay into its vehicle-hours, so its two headline numbers disagreed about the same
  corridor. Here delay is floored **per segment** (``speed.segment_delay(floor=True)``)
  and corridor delay is the prorated sum of those — one definition feeding both
  ``delay_min`` and ``vhd``. ``attrs['delay_floor'] == 'segment'`` says so.

Pure core, per CLAUDE.md: no GUI imports, no hardcoded paths (the connection is always
a parameter). Units stay US traffic-engineering — mph, minutes, miles.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from . import aadt as _aadt
from . import speed as _speed
from . import store as _store
from . import timebins as _timebins
from .io import (CVALUE_COL, DATETIME_COL, DEFAULT_CVALUE_THRESHOLD, DEFAULT_TZ,
                 SEGMENT_COL)

# Weekday gate for the commute windows — the pandas ``dayofweek`` names
# ``timebins.parse_day_of_week`` accepts.
WEEKDAYS = ("Mon", "Tue", "Wed", "Thu", "Fri")

CORRIDOR_COL = "corridor"        # ``rank_corridors`` output: the chain's name
WINDOW_COL = "window"            # ``rank_corridors`` output: the window's name


# ---------------------------------------------------------------------------
# Named peak windows
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class PeakWindow:
    """One named analysis window — a clock range, optionally day-of-week gated.

    Args:
        name: the short key the output columns are prefixed with (``"am"`` ->
            ``am_travel_time``). Keep it identifier-ish.
        window: a :func:`timebins.parse_time_bin` range string
            (``"7:00AM-9:00AM"``). **Half-open** ``[start, end)``; a range that wraps
            past midnight (``"10:00PM-5:00AM"``) is handled as an overnight window.
        days: day-of-week specs (:func:`timebins.parse_day_of_week` — ints, names or
            3-letter abbreviations). ``None`` (the default) means **every day**.
        peak: whether this window is a *peak* for ranking purposes — only peak
            windows compete for a corridor's ``worst_peak``. The off-peak windows
            (midday, night) are carried for context, not ranked.
    """

    name: str
    window: str
    days: tuple | None = None
    peak: bool = False

    @property
    def dows(self) -> tuple[int, ...] | None:
        """The gated days as sorted ``dayofweek`` ints (0=Mon), or ``None`` for an
        ungated window. All seven days is treated as ungated, matching
        :func:`timebins.filter_day_of_week`'s no-op rule."""
        if self.days is None:
            return None
        parsed = {_timebins.parse_day_of_week(d) for d in self.days}
        if not parsed or parsed == set(range(7)):
            return None
        return tuple(sorted(parsed))

    def filter(self, df: pd.DataFrame, datetime_col: str = DATETIME_COL) -> pd.DataFrame:
        """The window as a **pandas** filter — ``timebins.filter_time_window`` then
        ``timebins.filter_day_of_week`` on a tz-aware *local* frame (run
        :func:`io.to_local` first). This is the reference semantics the SQL path in
        :func:`segment_screen` is derived from and tested against."""
        out = _timebins.filter_time_window(df, self.window, datetime_col=datetime_col)
        return _timebins.filter_day_of_week(out, self.days, datetime_col=datetime_col)

    def sql_predicate(self, tod_expr: str = "tod", dow_expr: str = "dow") -> str:
        """The same window as a SQL boolean, **derived from the timebins parse** —
        never hand-written.

        ``tod_expr`` must evaluate to seconds since *local* midnight and ``dow_expr``
        to DuckDB's ``isodow`` (1=Mon..7=Sun); :func:`segment_screen` builds both.
        """
        _, start, end, overnight = _timebins.parse_time_bin(self.window)
        if start == end:                       # full day — a no-op, as in filter_time_window
            time_pred = "TRUE"
        elif overnight:
            time_pred = f"({tod_expr} >= {start} OR {tod_expr} < {end})"
        else:
            time_pred = f"({tod_expr} >= {start} AND {tod_expr} < {end})"
        dows = self.dows
        if dows is None:
            return time_pred
        # pandas dayofweek 0=Mon maps onto DuckDB isodow 1=Mon.
        days = ", ".join(str(d + 1) for d in dows)
        return f"({time_pred} AND {dow_expr} IN ({days}))"

    def to_dict(self) -> dict:
        """Plain-dict form for ``attrs`` (what a report must be able to restate)."""
        return {"name": self.name, "window": self.window,
                "days": None if self.days is None else list(self.days),
                "peak": bool(self.peak)}


# The presets. They are **presets, not constants** — pass your own mapping/sequence to
# ``segment_screen(windows=...)``. The commute pair is weekday-gated because that is
# the intent; ``night`` is not, because the point of it is the quietest hours of *any*
# day (it is the closest thing the data has to an observed free-flow window).
PEAK_WINDOWS: dict[str, PeakWindow] = {
    "am":     PeakWindow("am", "7:00AM-9:00AM", WEEKDAYS, peak=True),
    "pm":     PeakWindow("pm", "4:00PM-6:30PM", WEEKDAYS, peak=True),
    "midday": PeakWindow("midday", "10:00AM-2:00PM", WEEKDAYS),
    "night":  PeakWindow("night", "10:00PM-5:00AM"),
}

# The 7-day all-day window: 6:00 AM to 9:00 PM across all 7 days. Captures steady
# (non-peaky) congestion that weekday commute windows miss — signal-heavy commercial
# corridors that stay busy through midday and weekends, and recreational routes with
# Friday-Sunday travel costs. ``peak=True`` so it ranks in ``corridor_peak_totals``.
ALL_DAY_7D_WINDOW = PeakWindow("day_7d", "6:00AM-9:00PM", days=None, peak=True)

# Convenience: every defined preset in one dict. ``resolve_windows`` looks here when
# resolving a string name, so ``--windows day_7d`` works out of the box.
ALL_WINDOWS: dict[str, PeakWindow] = {
    **PEAK_WINDOWS,
    "day_7d": ALL_DAY_7D_WINDOW,
}

# The windows a corridor core is judged on (ROADMAP Item 50): the two commute peaks,
# the overnight baseline, and every weekday hour, whose low percentile is the fallback
# baseline where the night has too little data (``segment_screen(quantiles=...)``).
WEEKDAY_ALL_WINDOW = PeakWindow("weekday", "12:00AM-12:00AM", WEEKDAYS)
BASELINE_WINDOWS: dict[str, PeakWindow] = {
    "am": PEAK_WINDOWS["am"],
    "pm": PEAK_WINDOWS["pm"],
    "night": PEAK_WINDOWS["night"],
    "weekday": WEEKDAY_ALL_WINDOW,
}

REALTIME_COL = "Pct Score30"
"""Share (0–100) of an interval built from real-time probe data at INRIX's highest
confidence tier. ``segment_screen`` reports its mean, **ungated**, as
``realtime_share`` (0–1): the CValue gate already removes the rows it would describe."""


def resolve_windows(windows) -> dict[str, PeakWindow]:
    """Normalize a ``windows`` argument to an ordered ``{name: PeakWindow}`` dict.

    Accepts the :data:`PEAK_WINDOWS` mapping (or any ``{name: PeakWindow}``), a
    sequence of :class:`PeakWindow`, or a sequence of preset **names**
    (``["am", "pm"]``, ``"day_7d"``). String names are looked up in
    :data:`ALL_WINDOWS`, which includes both the commute presets and the 7-day
    all-day window. Order is preserved — it is the column order of the screen.
    """
    if isinstance(windows, Mapping):
        items = list(windows.values())
    elif isinstance(windows, PeakWindow):
        items = [windows]
    else:
        items = []
        for w in windows:
            if isinstance(w, PeakWindow):
                items.append(w)
            elif isinstance(w, str):
                if w not in ALL_WINDOWS:
                    raise KeyError(
                        f"Unknown window preset {w!r}; presets: {sorted(ALL_WINDOWS)}.")
                items.append(ALL_WINDOWS[w])
            else:
                raise TypeError(f"Not a PeakWindow or preset name: {w!r}")
    if not items:
        raise ValueError("No windows given.")
    out: dict[str, PeakWindow] = {}
    for w in items:
        if w.name in out:
            raise ValueError(f"Duplicate window name {w.name!r}.")
        out[w.name] = w
    return out


# ---------------------------------------------------------------------------
# The screen: one row per segment, computed in DuckDB
# ---------------------------------------------------------------------------
def _table_columns(con, table: str) -> list[str]:
    return [r[0] for r in con.execute(f'DESCRIBE "{table}"').fetchall()]


def _metric_columns(columns: Sequence[str]) -> dict[str, str | None]:
    """Locate the export's ``Speed(...)`` / ``Travel Time(...)`` / ``Ref Speed(...)``
    columns by prefix — the unit lives in the header, never assumed (DATA_FORMAT.md)."""
    m = _speed.metric_columns(pd.DataFrame(columns=list(columns)))
    m["ref_speed"] = next((c for c in columns if c.startswith("Ref Speed(")), None)
    return m


def _validated_tz(tz) -> str:
    """Validate a zone name before it is inlined into SQL (``AT TIME ZONE``)."""
    zone = tz or "UTC"
    ZoneInfo(zone)          # raises for an unknown/malformed zone
    return zone


def segment_screen(con, area_key: str, windows=PEAK_WINDOWS,
                   cvalue_threshold: int | float | None = DEFAULT_CVALUE_THRESHOLD, *,
                   bin_minutes: int | None = None, tz=DEFAULT_TZ,
                   date_start=None, date_end=None,
                   quantiles: Sequence[float] = ()) -> pd.DataFrame:
    """Reduce a stored area to **one row per segment**, per named window, in DuckDB.

    The screening primitive: it answers "what does this segment look like in the AM
    peak / the PM peak / at night" for every segment of a district without ever
    materialising the export in pandas.

    Args:
        con: an open :func:`store.connect` connection.
        area_key: the area to screen (``store.list_areas``).
        windows: the named windows — :data:`PEAK_WINDOWS` by default; anything
            :func:`resolve_windows` accepts.
        cvalue_threshold: keep rows with ``CValue > threshold`` (strict, matching
            :func:`io.filter_cvalue`). ``None`` disables the gate. A **null** CValue
            never passes a gate (SQL ``NULL > 80`` is not true) — on the 2026 D3
            export that is 39.4% of rows, so the ungated counts and the surviving
            share are carried beside every gated number.
        bin_minutes: which bin-length partition to screen; required when the area
            holds more than one.
        tz: the local zone the windows are read in (IANA name). Binning is **local
            wall-clock**, per CLAUDE.md — screening off UTC lands the peaks at the
            wrong hour.
        date_start / date_end: optional inclusive local calendar-date bounds, pushed
            into the scan exactly as :func:`store.load_export` pushes them.
        quantiles: travel-time quantiles (0–1) to add per window, over the gated
            rows: ``0.15`` adds ``<name>_tt_p15``. Empty by default (Item 50).

    Returns:
        A DataFrame indexed by ``Segment ID`` with

        - ``n_obs`` / ``n_obs_ungated`` / ``kept_fraction`` — rows behind the segment,
          after and before the CValue gate, and the surviving share;
        - ``ref_speed`` — mean ``Ref Speed(...)`` (INRIX's free-flow reference);
        - per window ``<name>_travel_time``, ``<name>_speed``, ``<name>_n_obs``,
          ``<name>_n_obs_ungated``, ``<name>_kept_fraction``, and ``<name>_tt_pNN``
          per requested quantile;
        - when the export carries ``Pct Score30``: ``realtime_share`` and
          ``<name>_realtime_share``, its mean over **all** rows as a 0–1 share
          (ungated — the gate removes exactly the rows this measures).

        ``attrs`` records ``area_key``, ``bin_minutes``, ``tz``, ``cvalue_threshold``,
        ``windows`` (each window's full spec), ``units`` and the date bounds — the
        screen carries the definition it was computed under.

    Raises:
        KeyError: no such area. ValueError: the area has no travel-time column, or a
        CValue gate was asked for on an export that carries no ``CValue`` column
        (pass ``cvalue_threshold=None`` to screen it ungated — explicitly).
    """
    row = _store._area_row(con, area_key)
    if row is None:
        raise KeyError(f"No area {area_key!r} in the store.")
    wins = resolve_windows(windows)
    zone = _validated_tz(tz)
    bin_minutes = _store._resolve_bin(con, area_key, bin_minutes)
    obs = _store._obs_table(area_key)
    cols = _table_columns(con, obs)
    m = _metric_columns(cols)
    if m["travel_time"] is None:
        raise ValueError(f"Area {area_key!r} carries no 'Travel Time(...)' column.")
    if cvalue_threshold is not None and CVALUE_COL not in cols:
        raise ValueError(
            f"Area {area_key!r} carries no {CVALUE_COL!r} column but a gate of "
            f"{cvalue_threshold} was requested; pass cvalue_threshold=None to screen "
            "it ungated.")

    def q(col):                      # a quoted column, or SQL NULL when absent
        return f'"{col}"' if col else "NULL"

    qs = [float(x) for x in quantiles]
    if any(not 0.0 < x < 1.0 for x in qs):
        raise ValueError(f"quantiles must lie strictly between 0 and 1: {qs}")
    has_rt = REALTIME_COL in cols

    where = [f'"{_store.BIN_COL}" = ?']
    params: list = [bin_minutes]
    lo, hi = _store._date_bounds_utc(date_start, date_end, zone)
    if lo is not None:
        where.append(f'"{DATETIME_COL}" >= ?')
        params.append(lo)
    if hi is not None:
        where.append(f'"{DATETIME_COL}" < ?')
        params.append(hi)

    gate = "TRUE" if cvalue_threshold is None else f'"{CVALUE_COL}" > {float(cvalue_threshold)}'
    # Local wall clock -> seconds since local midnight + isodow, the two expressions
    # every window predicate is written against.
    select_parts = [
        f'  "{SEGMENT_COL}" AS sid',
        f'  {q(m["speed"])} AS speed',
        f'  {q(m["travel_time"])} AS tt',
        f'  {q(m["ref_speed"])} AS ref',
        f'  {q(REALTIME_COL if has_rt else None)} AS rt',
        f"""  "{DATETIME_COL}" AT TIME ZONE '{zone}' AS local_dt""",
        f"  ({gate}) AS kept",
    ]
    agg = [
        '  sid AS "%s"' % SEGMENT_COL,
        "  count(*) AS n_obs_ungated",
        "  count(*) FILTER (WHERE kept) AS n_obs",
        "  avg(ref) FILTER (WHERE kept) AS ref_speed",
    ]
    if has_rt:
        agg.append("  avg(rt) / 100.0 AS realtime_share")
    for name, w in wins.items():
        pred = w.sql_predicate()
        agg += [
            f'  count(*) FILTER (WHERE {pred}) AS "{name}_n_obs_ungated"',
            f'  count(*) FILTER (WHERE kept AND {pred}) AS "{name}_n_obs"',
            f'  avg(tt) FILTER (WHERE kept AND {pred}) AS "{name}_travel_time"',
            f'  avg(speed) FILTER (WHERE kept AND {pred}) AS "{name}_speed"',
        ]
        agg += [f'  quantile_cont(tt, {x}) FILTER (WHERE kept AND {pred}) '
                f'AS "{name}_{_quantile_name(x)}"' for x in qs]
        if has_rt:
            agg.append(f'  avg(rt) FILTER (WHERE {pred}) / 100.0 AS "{name}_realtime_share"')
    sql = (
        "WITH src AS (\nSELECT\n" + ",\n".join(select_parts) +
        f'\nFROM "{obs}" WHERE {" AND ".join(where)}\n), tagged AS (\n'
        "SELECT sid, speed, tt, ref, rt, kept,\n"
        "  date_part('hour', local_dt) * 3600 + date_part('minute', local_dt) * 60"
        " + date_part('second', local_dt) AS tod,\n"
        "  isodow(local_dt) AS dow\n"
        "FROM src\n)\nSELECT\n" + ",\n".join(agg) +
        "\nFROM tagged GROUP BY sid ORDER BY sid"
    )
    out = con.execute(sql, params).df()

    if len(out):
        out[SEGMENT_COL] = out[SEGMENT_COL].astype("int64")
    out = out.set_index(SEGMENT_COL)
    # Surviving-row share: the CValue gate's cost, per segment and per window, so a
    # screened-in segment built from backfill is visible (and not merely absent).
    out["kept_fraction"] = _share(out["n_obs"], out["n_obs_ungated"])
    for name in wins:
        out[f"{name}_kept_fraction"] = _share(
            out[f"{name}_n_obs"], out[f"{name}_n_obs_ungated"])

    order = ["n_obs", "n_obs_ungated", "kept_fraction", "ref_speed"]
    if has_rt:
        order.append("realtime_share")
    for name in wins:
        order += [f"{name}_travel_time", f"{name}_speed", f"{name}_n_obs",
                  f"{name}_n_obs_ungated", f"{name}_kept_fraction"]
        order += [f"{name}_{_quantile_name(x)}" for x in qs]
        if has_rt:
            order.append(f"{name}_realtime_share")
    out = out[order]
    out.attrs = {
        "area_key": area_key,
        "bin_minutes": bin_minutes,
        "tz": zone,
        "cvalue_threshold": cvalue_threshold,
        "windows": {n: w.to_dict() for n, w in wins.items()},
        "units": {"speed": row.get("units_speed"),
                  "travel_time": row.get("units_travel_time")},
        "date_start": date_start,
        "date_end": date_end,
        "quantiles": qs,
        "metric_columns": {k: v for k, v in m.items()},
    }
    return out


def segment_monthly_screen(con, area_key: str, windows=None,
                           cvalue_threshold: int | float | None = DEFAULT_CVALUE_THRESHOLD, *,
                           bin_minutes: int | None = None, tz=DEFAULT_TZ,
                           date_start=None, date_end=None) -> pd.DataFrame:
    """Per segment, per **local calendar month**, per window: mean travel time and
    gated observation count (ROADMAP Item 50).

    The month-by-month view :func:`segment_screen` collapses. It exists to show
    **when** a segment's delay happened: a queue that is there every month and a
    summer work zone can have the same export-wide mean (I-90 WB in Coeur d'Alene
    sat at its overnight level until 22 June 2026, then ran 3-4x slower).

    Returns a long frame with ``Segment ID``, ``month`` (``"YYYY-MM"``, local), and
    per window ``<name>_travel_time`` / ``<name>_n_obs``. ``windows`` defaults to the
    AM/PM peaks.
    """
    row = _store._area_row(con, area_key)
    if row is None:
        raise KeyError(f"No area {area_key!r} in the store.")
    wins = resolve_windows(windows if windows is not None else ["am", "pm"])
    zone = _validated_tz(tz)
    bin_minutes = _store._resolve_bin(con, area_key, bin_minutes)
    obs = _store._obs_table(area_key)
    cols = _table_columns(con, obs)
    m = _metric_columns(cols)
    if m["travel_time"] is None:
        raise ValueError(f"Area {area_key!r} carries no 'Travel Time(...)' column.")
    if cvalue_threshold is not None and CVALUE_COL not in cols:
        raise ValueError(f"Area {area_key!r} carries no {CVALUE_COL!r} column but a gate "
                         f"of {cvalue_threshold} was requested.")
    where = [f'"{_store.BIN_COL}" = ?']
    params: list = [bin_minutes]
    lo, hi = _store._date_bounds_utc(date_start, date_end, zone)
    if lo is not None:
        where.append(f'"{DATETIME_COL}" >= ?')
        params.append(lo)
    if hi is not None:
        where.append(f'"{DATETIME_COL}" < ?')
        params.append(hi)
    gate = "TRUE" if cvalue_threshold is None else f'"{CVALUE_COL}" > {float(cvalue_threshold)}'
    agg = [f'  sid AS "{SEGMENT_COL}"', "  strftime(local_dt, '%Y-%m') AS month"]
    for name, w in wins.items():
        pred = w.sql_predicate()
        agg += [f'  avg(tt) FILTER (WHERE kept AND {pred}) AS "{name}_travel_time"',
                f'  count(*) FILTER (WHERE kept AND {pred}) AS "{name}_n_obs"']
    sql = (
        f'WITH src AS (SELECT "{SEGMENT_COL}" AS sid, "{m["travel_time"]}" AS tt, '
        f"\"{DATETIME_COL}\" AT TIME ZONE '{zone}' AS local_dt, ({gate}) AS kept "
        f'FROM "{obs}" WHERE {" AND ".join(where)}), tagged AS (\n'
        "SELECT sid, tt, kept, local_dt,\n"
        "  date_part('hour', local_dt) * 3600 + date_part('minute', local_dt) * 60"
        " + date_part('second', local_dt) AS tod,\n"
        "  isodow(local_dt) AS dow FROM src)\nSELECT\n" + ",\n".join(agg) +
        "\nFROM tagged GROUP BY 1, 2 ORDER BY 1, 2"
    )
    out = con.execute(sql, params).df()
    if len(out):
        out[SEGMENT_COL] = out[SEGMENT_COL].astype("int64")
    out.attrs = {"area_key": area_key, "tz": zone, "cvalue_threshold": cvalue_threshold,
                 "windows": {n: w.to_dict() for n, w in wins.items()},
                 "bin_minutes": bin_minutes}
    return out


def _quantile_name(q: float) -> str:
    """``0.15`` -> ``"tt_p15"``; ``0.025`` -> ``"tt_p2.5"``."""
    pct = round(100.0 * float(q), 6)
    return f"tt_p{int(pct):02d}" if pct == int(pct) else f"tt_p{pct:g}"


def _share(kept: pd.Series, total: pd.Series) -> pd.Series:
    """``kept / total``, NaN where nothing was observed (never 0/0 = 0)."""
    total = total.astype("float64")
    return (kept.astype("float64") / total.where(total > 0))


# ---------------------------------------------------------------------------
# The ranking: corridors, through the existing primitives
# ---------------------------------------------------------------------------
def _as_chain_map(chains) -> dict:
    """Normalize ``chains`` to an ordered ``{name: ChainResult}`` dict. Accepts that
    mapping, an iterable of ``(name, chain)`` pairs, or a bare iterable of chains
    (named ``chain_1``, ``chain_2``, ...)."""
    if isinstance(chains, Mapping):
        return dict(chains)
    out: dict = {}
    for i, item in enumerate(chains, start=1):
        if isinstance(item, tuple) and len(item) == 2:
            name, chain = item
        else:
            name, chain = f"chain_{i}", item
        if name in out:
            raise ValueError(f"Duplicate corridor name {name!r}.")
        out[name] = chain
    return out


def _chain_lengths(chain_map: dict) -> pd.Series:
    """``Segment ID -> Miles`` across every chain (the XD ``Miles`` each chain carries;
    a segment shared by two chains must agree, and does — it is the same network)."""
    lengths: dict[int, float] = {}
    for chain in chain_map.values():
        for sid, mi in zip(chain.segment_ids, chain.miles):
            lengths[int(sid)] = float(mi)
    s = pd.Series(lengths, dtype="float64")
    s.index.name = SEGMENT_COL
    return s


def _delay_frame(screen: pd.DataFrame, wins: dict, lengths: pd.Series,
                 free_flow) -> tuple[pd.DataFrame, str]:
    """Per-segment, per-window floored delay — via :func:`speed.segment_delay`.

    Builds the one frame that function wants (a ``Travel Time(...)`` / ``Speed(...)``
    / ``Ref Speed(...)`` row per segment×window, at the window's means) and runs the
    real primitive over it, so the delay definition, the floor and the free-flow
    resolution are the package's, not a restatement. Returns the long frame (with the
    delay column appended) and that column's name.
    """
    units = screen.attrs.get("units") or {}
    tt_unit = units.get("travel_time") or "Minutes"
    sp_unit = units.get("speed") or "miles/hour"
    tt_col, sp_col = f"Travel Time({tt_unit})", f"Speed({sp_unit})"
    ref_col = f"Ref Speed({sp_unit})"

    rows = []
    for name in wins:
        part = pd.DataFrame({
            SEGMENT_COL: screen.index.astype("int64"),
            WINDOW_COL: name,
            tt_col: screen[f"{name}_travel_time"].to_numpy(dtype="float64"),
            sp_col: screen[f"{name}_speed"].to_numpy(dtype="float64"),
            ref_col: screen["ref_speed"].to_numpy(dtype="float64"),
        })
        rows.append(part)
    long = pd.concat(rows, ignore_index=True)

    length_frame = pd.DataFrame({"Miles": lengths})
    delay_col = _speed.DELAY_COL if tt_unit == "Minutes" else f"Delay({tt_unit})"
    out = _speed.segment_delay(long, length_frame, free_flow=free_flow, floor=True,
                               out_col=delay_col)
    # Free-flow travel time, kept explicitly: TTI needs the denominator, and reading
    # it back off a *floored* delay would be wrong wherever the floor bit.
    out["free_flow"] = (out[SEGMENT_COL].map(lengths) / out[ref_col] * 60.0
                        ).where(out[ref_col] > 0)
    return out, delay_col


def rank_corridors(screen: pd.DataFrame, chains, aadt=None, *, windows=None,
                   free_flow="ref") -> pd.DataFrame:
    """Rank assembled corridors on a :func:`segment_screen`, one row per corridor ×
    window.

    The metric vocabulary the outside screening pass got right, computed through the
    package's primitives: delay comes from :func:`speed.segment_delay` (so the
    free-flow resolution and the floor are the ones documented there) and the volume
    weighting from :func:`aadt.vehicle_hours_of_delay` (so the **AADT caveat travels
    with the number** instead of being silently restated as "veh-hrs/day").

    Args:
        screen: a :func:`segment_screen` frame.
        chains: ``{name: ChainResult}`` (or ``(name, chain)`` pairs, or bare chains) —
            assembled by :func:`corridors.build_chain`, which is what supplies the
            topological walk, the endpoint trim and the per-member extent fractions.
        aadt: the :func:`aadt.join_aadt` frame (or a ``Segment ID``-indexed AADT
            Series) for the volume weighting. ``None`` leaves ``vhd`` /
            ``vhd_per_mile`` as ``NaN`` rather than inventing a weight.
        windows: restrict to a subset of the screen's windows (names or
            :class:`PeakWindow`s); default is every window the screen carries.
        free_flow: passed to :func:`speed.segment_delay` — ``'ref'`` (the INRIX
            free-flow reference) or a ``('pXX', q)`` percentile spec.

    Returns:
        One row per corridor × window, columns:

        ``corridor``, ``window``, ``is_peak``, ``worst_peak`` (the peak window with
        this corridor's largest ``delay_min``), ``n_segments``, ``n_observed``,
        ``miles`` (the **observed** in-extent miles), ``missing_miles``,
        ``miles_covered_fraction``, ``n_obs``, ``min_kept_fraction``,
        ``travel_time_min``, ``free_flow_min``, ``delay_min``, ``tti``,
        ``delay_per_mile``, ``vhd``, ``vhd_per_mile``, ``n_ramp_weighted``,
        ``n_aadt_missing``.

        Rank with e.g. ``out[out['window'] == out['worst_peak']]
        .sort_values('vhd', ascending=False)``.

    **Three definitions this pins down**, because the outside pass left them open:

    - **Delay is floored once, per segment** (``attrs['delay_floor'] == 'segment'``):
      ``delay_min`` is the prorated sum of per-segment floored delay, and ``vhd`` is
      the volume-weighted sum of the *same* per-segment values. A segment running
      faster than its free-flow reference can therefore never pay for a congested one,
      and the two headline numbers cannot disagree about a corridor.
    - **Only observed pavement is credited.** ``miles`` sums the in-extent miles of
      members the screen actually observes; a member absent from the screen lands in
      ``missing_miles`` instead, so ``delay_per_mile`` and ``tti`` are not deflated by
      pavement that contributed no travel time (ROADMAP Item 31's concern, applied
      here at the screening level).
    - **End segments are prorated by the chain's extent fractions**
      (``ChainResult.weights()``), the same proration
      :func:`corridors.chain_travel_time` applies, on the same uniform-speed-within-a-
      segment assumption recorded in DATA_FORMAT.md.
    """
    chain_map = _as_chain_map(chains)
    if not chain_map:
        raise ValueError("No chains given.")
    screen_windows = list((screen.attrs.get("windows") or {}).keys())
    if not screen_windows:
        raise ValueError(
            "The screen carries no window definitions on attrs['windows']; pass a "
            "segment_screen() frame.")
    if windows is None:
        names = screen_windows
    else:
        names = list(resolve_windows(windows).keys())
        missing = [n for n in names if n not in screen_windows]
        if missing:
            raise KeyError(f"Screen has no window(s) {missing}; it carries {screen_windows}.")
    wins = {n: screen.attrs["windows"][n] for n in names}

    lengths = _chain_lengths(chain_map)
    delay_long, delay_col = _delay_frame(screen, wins, lengths, free_flow)
    # (segment, window) -> value lookups
    piv = delay_long.pivot(index=SEGMENT_COL, columns=WINDOW_COL)
    delay = piv[delay_col]
    free_flow_min = piv["free_flow"]
    tt_unit = (screen.attrs.get("units") or {}).get("travel_time") or "Minutes"
    tt_wide = piv[f"Travel Time({tt_unit})"]

    aadt_src = _aadt_source_lookup(aadt)
    records = []
    for name, chain in chain_map.items():
        weights = chain.weights()
        miles = dict(zip((int(s) for s in chain.segment_ids), chain.miles))
        ids = [int(s) for s in chain.segment_ids]
        per_window = {}
        for wname in names:
            d = delay[wname].reindex(ids)
            tt = tt_wide[wname].reindex(ids)
            ff = free_flow_min[wname].reindex(ids)
            observed = d.notna() & tt.notna() & ff.notna()
            w = pd.Series({sid: weights[sid] for sid in ids}, dtype="float64")
            mi = pd.Series({sid: miles[sid] for sid in ids}, dtype="float64")
            ext = (mi * w)
            obs_miles = float(ext[observed].sum())
            missing_miles = float(ext[~observed].sum())
            total_miles = obs_miles + missing_miles

            delay_prorated = (d * w)[observed]
            delay_min = float(delay_prorated.sum())
            tt_min = float((tt * w)[observed].sum())
            ff_sum = float((ff * w)[observed].sum())

            vhd = float("nan")
            n_ramp = n_aadt_missing = 0
            if aadt is not None and observed.any():
                vh = _aadt.vehicle_hours_of_delay(delay_prorated, aadt)
                vhd = float(vh["vehicle_hours"].sum())
                n_aadt_missing = int((~(vh[_aadt.AADT_COL] > 0)).sum())
                if aadt_src is not None:
                    n_ramp = int((aadt_src.reindex(delay_prorated.index)
                                  == "matched_ramp").sum())

            nobs_col = f"{wname}_n_obs"
            keep_col = f"{wname}_kept_fraction"
            seg_obs = screen[nobs_col].reindex(ids)[observed] if nobs_col in screen else None
            seg_keep = screen[keep_col].reindex(ids)[observed] if keep_col in screen else None

            per_window[wname] = {
                CORRIDOR_COL: name,
                WINDOW_COL: wname,
                "is_peak": bool(wins[wname].get("peak", False)),
                "n_segments": len(ids),
                "n_observed": int(observed.sum()),
                "miles": obs_miles,
                "missing_miles": missing_miles,
                "miles_covered_fraction": (obs_miles / total_miles
                                           if total_miles > 0 else float("nan")),
                "n_obs": int(seg_obs.sum()) if seg_obs is not None else 0,
                "min_kept_fraction": (float(seg_keep.min())
                                      if seg_keep is not None and seg_keep.notna().any()
                                      else float("nan")),
                "travel_time_min": tt_min,
                "free_flow_min": ff_sum,
                "delay_min": delay_min,
                "tti": (tt_min / ff_sum) if ff_sum > 0 else float("nan"),
                "delay_per_mile": (delay_min / obs_miles) if obs_miles > 0 else float("nan"),
                "vhd": vhd,
                "vhd_per_mile": (vhd / obs_miles) if obs_miles > 0 else float("nan"),
                "n_ramp_weighted": n_ramp,
                "n_aadt_missing": n_aadt_missing,
            }
        # worst_peak: the peak-flagged window carrying the most delay. Off-peak
        # windows are context, and never win.
        peaks = {k: v["delay_min"] for k, v in per_window.items()
                 if v["is_peak"] and v["n_observed"] > 0}
        worst = max(peaks, key=peaks.get) if peaks else None
        for rec in per_window.values():
            rec["worst_peak"] = worst
            records.append(rec)

    out = pd.DataFrame.from_records(records)
    order = [CORRIDOR_COL, WINDOW_COL, "is_peak", "worst_peak", "n_segments",
             "n_observed", "miles", "missing_miles", "miles_covered_fraction",
             "n_obs", "min_kept_fraction", "travel_time_min", "free_flow_min",
             "delay_min", "tti", "delay_per_mile", "vhd", "vhd_per_mile",
             "n_ramp_weighted", "n_aadt_missing"]
    out = out[order]
    out.attrs = {
        "delay_floor": "segment",
        "free_flow": free_flow,
        "prorated": True,
        "miles_basis": "observed",
        "cvalue_threshold": screen.attrs.get("cvalue_threshold"),
        "tz": screen.attrs.get("tz"),
        "bin_minutes": screen.attrs.get("bin_minutes"),
        "area_key": screen.attrs.get("area_key"),
        "windows": wins,
        "aadt_caveat": _AADT_CAVEAT if aadt is not None else None,
    }
    return out


# ---------------------------------------------------------------------------
# Reporting corridors: both directions of one road  (Item 40)
# ---------------------------------------------------------------------------
GROUP_COL = "corridor_group"
DIRECTION_COL = "direction"

_GROUP_SUM = ("n_segments", "n_observed", "missing_miles", "n_obs",
              "travel_time_min", "free_flow_min", "delay_min", "vhd",
              "n_ramp_weighted", "n_aadt_missing")


def _membership_frame(membership) -> pd.DataFrame:
    """``{entry: group}`` / a frame / entry objects -> a frame of
    ``corridor, corridor_group, direction``."""
    if isinstance(membership, pd.DataFrame):
        frame = membership.copy()
        if GROUP_COL not in frame.columns and {"id", "corridor"} <= set(frame.columns):
            # ``corridors.resolve_catalogue``'s shape: 'id' is the entry, 'corridor'
            # is the reporting group it belongs to.
            frame = frame.rename(columns={"id": CORRIDOR_COL, "corridor": GROUP_COL})
        missing = {CORRIDOR_COL, GROUP_COL} - set(frame.columns)
        if missing:
            raise ValueError(f"Membership frame is missing {sorted(missing)}.")
        if DIRECTION_COL not in frame.columns:
            frame[DIRECTION_COL] = None
        return frame[[CORRIDOR_COL, GROUP_COL, DIRECTION_COL]]

    rows = []
    for item in membership:
        if hasattr(item, "id"):                     # corridors.CorridorEntry
            rows.append({CORRIDOR_COL: item.id, GROUP_COL: item.corridor,
                         DIRECTION_COL: item.direction})
        else:                                       # a {entry: group} mapping item
            entry, group = item, membership[item]
            rows.append({CORRIDOR_COL: entry, GROUP_COL: group, DIRECTION_COL: None})
    return pd.DataFrame(rows, columns=[CORRIDOR_COL, GROUP_COL, DIRECTION_COL])


def rank_corridor_groups(ranking: pd.DataFrame, membership, *, names=None) -> pd.DataFrame:
    """Combine a directional :func:`rank_corridors` into **reporting corridors** —
    one row per road × window instead of one per carriageway × window.  (Item 40)

    A catalogue entry is one direction of one extent, because that is the unit the
    network walk and the AADT join work in. A district reads its ranking per *road*:
    "I-84, Nampa to Boise" is one corridor, not two. This does that combination, and
    the whole difficulty is that **most of these metrics do not combine the same
    way**:

    - **Additive over carriageways** — ``vhd``, ``n_obs``, ``n_segments``,
      ``n_observed``, ``missing_miles``, ``n_ramp_weighted``, ``n_aadt_missing``, and
      the two trip components ``travel_time_min`` / ``free_flow_min``. Vehicle-hours
      of delay is a count of hours and the two directions are different vehicles, so
      the sum is the corridor's burden.
    - **NOT additive: ``miles``.** The two carriageways run over the *same ground*.
      Summing them would double-count the corridor's length — the identical error as
      summing a frontage road in series with the freeway it parallels (Item 36). So
      ``miles`` is the **mean** of the directions' observed miles, which is the
      corridor's length, and the sum is reported separately as
      ``directional_miles`` (centre-line miles × directions) because that *is* the
      right denominator for a per-mile rate.
    - **NOT averageable: ``tti``, ``delay_per_mile``, ``vhd_per_mile``.** A ratio of
      sums is not the mean of the ratios. Each is **recomputed** from the summed
      components — ``tti`` from summed travel time over summed free-flow, the
      per-mile rates over ``directional_miles``. Averaging the two directions' TTIs
      would let a 0.6-mile direction pull as hard as a 15-mile one.

    The direction breakout is kept rather than dissolved: ``peak_direction`` (the
    direction carrying the most ``vhd``, falling back to ``delay_min`` when no AADT
    was joined), ``tti_min`` / ``tti_max`` and ``delay_min_max`` across the
    directions, and ``directions`` listing what was combined. The per-direction rows
    are unchanged in the input frame — this is a second view, not a replacement.

    **The trap grouping sets, and the column that defuses it.** A grouped row is
    *one window*, so it sums the two directions **at the same clock time** — and the
    two directions of a commute corridor peak at *different* times. D3's I-84 is the
    case: WB carries 25,677 veh-hrs in the PM and EB 19,818 in the AM, but the
    grouped PM row reads **26,260**, because EB at 5pm is nearly empty. That number
    is correct for the question "how bad is this road at its worst hour" and badly
    wrong for "how much delay does this road cause in a day". So the frame also
    carries ``vhd_directional_peaks`` — each direction taken at **its own** worst
    peak and then summed (45,495 for I-84). It is a group-level constant, identical
    on every window row, and it deliberately spans two different windows: read it as
    a daily burden, never as a moment.

    Args:
        ranking: a :func:`rank_corridors` frame, keyed on the catalogue entry id.
        membership: ``{entry_id: group_id}``, an iterable of
            :class:`corridors.CorridorEntry` (which carry ``corridor`` /
            ``direction``), or a frame with ``corridor``/``corridor_group``
            (``corridors.resolve_catalogue``'s ``id``/``corridor`` shape is accepted
            directly). Entries with no group are **dropped**, and
            ``attrs['ungrouped']`` names them — an entry silently absent from a
            report is the failure this guards against.
        names: optional ``{group_id: display name}``.

    Returns:
        One row per group × window. ``attrs`` carries the input's, plus
        ``grouped=True``, ``miles_basis_group`` and ``ungrouped``.
    """
    if ranking.empty:
        raise ValueError("Nothing to group: the ranking frame is empty.")
    member = _membership_frame(membership)
    member = member[member[GROUP_COL].notna()]
    if member.empty:
        raise ValueError(
            "No entry carries a reporting corridor; add 'corridor'/'direction' to the "
            "catalogue entries, or rank per direction with rank_corridors alone.")

    joined = ranking.merge(member, on=CORRIDOR_COL, how="left")
    ungrouped = sorted(joined.loc[joined[GROUP_COL].isna(), CORRIDOR_COL].unique())
    joined = joined[joined[GROUP_COL].notna()]
    if joined.empty:
        raise ValueError(
            f"None of the ranked corridors {sorted(ranking[CORRIDOR_COL].unique())} "
            "appears in the membership.")

    name_map = dict(names or {})
    # Each direction at its own worst peak — a daily burden, spanning two windows.
    own_peak = joined[joined[WINDOW_COL] == joined["worst_peak"]]
    peak_totals = own_peak.groupby(GROUP_COL)["vhd"].sum(min_count=1)
    peak_delay = own_peak.groupby(GROUP_COL)["delay_min"].sum(min_count=1)
    records = []
    for (gid, wname), block in joined.groupby([GROUP_COL, WINDOW_COL], sort=False):
        sums = {c: block[c].sum(min_count=1) for c in _GROUP_SUM if c in block}
        obs_miles = block["miles"]
        directional_miles = float(obs_miles.sum())
        tt, ff = sums.get("travel_time_min"), sums.get("free_flow_min")
        delay, vhd = sums.get("delay_min"), sums.get("vhd")
        # The peak direction is the one carrying the load, by volume-weighted delay
        # where a weight exists and by delay otherwise.
        by = "vhd" if block["vhd"].notna().any() else "delay_min"
        lead = block.loc[block[by].idxmax()] if block[by].notna().any() else block.iloc[0]
        total_miles = directional_miles + float(sums.get("missing_miles") or 0.0)
        records.append({
            GROUP_COL: gid,
            "group_name": name_map.get(gid, gid),
            WINDOW_COL: wname,
            "is_peak": bool(block["is_peak"].iloc[0]),
            "n_entries": int(len(block)),
            "directions": tuple(d for d in block[DIRECTION_COL] if d is not None),
            "n_segments": int(sums.get("n_segments") or 0),
            "n_observed": int(sums.get("n_observed") or 0),
            "miles": float(obs_miles.mean()),          # the corridor's length
            "directional_miles": directional_miles,     # centre-line miles x directions
            "missing_miles": float(sums.get("missing_miles") or 0.0),
            "miles_covered_fraction": (directional_miles / total_miles
                                       if total_miles > 0 else float("nan")),
            "n_obs": int(sums.get("n_obs") or 0),
            "min_kept_fraction": float(block["min_kept_fraction"].min()),
            "travel_time_min": tt,
            "free_flow_min": ff,
            "delay_min": delay,
            "tti": (tt / ff) if ff and ff > 0 else float("nan"),
            "delay_per_mile": (delay / directional_miles
                               if directional_miles > 0 else float("nan")),
            "vhd": vhd,
            "vhd_per_mile": (vhd / directional_miles
                             if directional_miles > 0 and pd.notna(vhd) else float("nan")),
            "peak_direction": lead[DIRECTION_COL],
            "peak_entry": lead[CORRIDOR_COL],
            "tti_min": float(block["tti"].min()),
            "tti_max": float(block["tti"].max()),
            "delay_min_max": float(block["delay_min"].max()),
            "vhd_directional_peaks": peak_totals.get(gid, float("nan")),
            "delay_min_directional_peaks": peak_delay.get(gid, float("nan")),
            "n_ramp_weighted": int(sums.get("n_ramp_weighted") or 0),
            "n_aadt_missing": int(sums.get("n_aadt_missing") or 0),
        })

    out = pd.DataFrame.from_records(records)
    # worst_peak on the same rule rank_corridors uses: the peak window carrying the
    # most delay, computed on the *group's* delay rather than either direction's.
    for gid, block in out.groupby(GROUP_COL, sort=False):
        peaks = block[block["is_peak"] & (block["n_observed"] > 0)]
        worst = (peaks.loc[peaks["delay_min"].idxmax(), WINDOW_COL]
                 if not peaks.empty and peaks["delay_min"].notna().any() else None)
        out.loc[out[GROUP_COL] == gid, "worst_peak"] = worst
    order = [GROUP_COL, "group_name", WINDOW_COL, "is_peak", "worst_peak", "n_entries",
             "directions", "peak_direction", "peak_entry", "n_segments", "n_observed",
             "miles", "directional_miles", "missing_miles", "miles_covered_fraction",
             "n_obs", "min_kept_fraction", "travel_time_min", "free_flow_min",
             "delay_min", "tti", "tti_min", "tti_max", "delay_min_max",
             "delay_per_mile", "vhd", "vhd_per_mile", "vhd_directional_peaks",
             "delay_min_directional_peaks", "n_ramp_weighted", "n_aadt_missing"]
    out = out[order]
    out.attrs = {
        **ranking.attrs,
        "grouped": True,
        "miles_basis_group": ("miles = mean of the directions (the corridor's length); "
                              "directional_miles = their sum, and the denominator of "
                              "every per-mile rate"),
        "vhd_basis_group": ("vhd sums the directions within ONE window; "
                            "vhd_directional_peaks takes each direction at its own "
                            "worst peak and spans two"),
        "ungrouped": ungrouped,
    }
    return out


# ---------------------------------------------------------------------------
# The reporting table: every direction x peak visible, ranked on the total
# ---------------------------------------------------------------------------
RANK_METRICS = ("vhd_per_mile", "delay_per_mile", "vhd", "delay_min", "tti")
DEFAULT_RANK_METRIC = "vhd_per_mile"


def _peak_cells(ranking, membership, windows):
    """The (corridor, direction, window) cells a reporting total is built from."""
    member = _membership_frame(membership)
    member = member[member[GROUP_COL].notna()]
    if member.empty:
        raise ValueError(
            "No entry carries a reporting corridor; add 'corridor'/'direction' to the "
            "catalogue entries, or rank per direction with rank_corridors alone.")
    joined = ranking.merge(member, on=CORRIDOR_COL, how="left")
    ungrouped = sorted(joined.loc[joined[GROUP_COL].isna(), CORRIDOR_COL].unique())
    joined = joined[joined[GROUP_COL].notna()]
    if windows is None:
        cells = joined[joined["is_peak"]]
        used = sorted(cells[WINDOW_COL].unique())
    else:
        used = list(resolve_windows(windows).keys())
        cells = joined[joined[WINDOW_COL].isin(used)]
    if cells.empty:
        raise ValueError(f"No rows for window(s) {used or 'flagged is_peak'}.")
    return cells, used, ungrouped


def corridor_peak_totals(ranking: pd.DataFrame, membership, *, names=None, windows=None,
                         rank_by: str = "vhd_per_mile", couplets=None) -> pd.DataFrame:
    """One row per reporting corridor, **summed over every peak window and both
    directions** — the number a corridor is finally ranked on.  (Item 41)

    :func:`rank_corridor_groups` answers "how bad is this road at one hour". This
    answers "how much congestion does this road carry across its peaks, in total",
    which is the ranking a programme is built from. Delay, travel time, free-flow and
    ``vhd`` are summed across all ``direction x peak window`` cells; the two peaks of
    one direction are two separate trips over the same pavement, so they add.

    **The mileage denominator is counted once per direction, not once per cell.**
    A direction's observed miles do not change between windows (verified on the D3
    run: zero spread), so ``directional_miles`` sums each direction's miles a single
    time and every per-mile rate divides the *summed* total by it.

    **The default ranking is ``vhd_per_mile`` — vehicle-hours of delay per mile.**
    The three candidates are genuinely different questions and D3 orders them
    differently, so the choice is recorded rather than left implicit:

    - ``vhd`` (total vehicle-hours) asks *how much delay does this road cause*, and
      rewards **length and volume together**: 34.9-mile rural SH-45 out-totals the
      downtown couplet, which is four times worse to drive.
    - ``delay_per_mile`` asks *how bad is it to drive*, and ignores **how many
      people it happens to** — it puts a 1.1-mile couplet leg above I-84.
    - ``vhd_per_mile`` asks *how much delay does each mile of this road cause*. It
      keeps the volume weighting and drops the length reward, which is the
      combination a screening rank wants. On D3 it puts I-84 first (1,539 veh-hrs
      per mile, nearly three times the next) while still holding the couplet second
      at 554 — a corridor that ranks 9th of 10 on the bare total.

    Every metric's rank is returned beside the chosen one (``rank_vhd_per_mile``,
    ``rank_delay_per_mile``, …) so the orderings can be compared rather than taken on
    faith, and ``rank_by`` selects any of them.

    Args:
        ranking: a :func:`rank_corridors` frame, keyed on the catalogue entry id.
        membership: as :func:`rank_corridor_groups` takes it.
        names: optional ``{group_id: display name}``.
        windows: which windows to total (default: every window flagged ``is_peak``).
        rank_by: one of :data:`RANK_METRICS`; the frame is sorted by it, descending
            (ascending for nothing — every one of these is worse when larger).
            ``vhd_per_mile`` and ``vhd`` are ``NaN`` without an AADT join, and
            ``attrs['rank_metric_all_null']`` says so rather than returning a frame
            of ``<NA>`` ranks that looks like a ranking.
        couplets: ids of reporting corridors whose two directions run on **different
            streets** (a one-way couplet), flagged in the ``one_way_couplet`` column.
            It changes no arithmetic — see the note below — but it changes how
            ``directional_miles`` reads.

    Returns:
        One row per corridor, ranked. ``attrs`` carries the input's plus ``windows``,
        ``rank_by``, ``ungrouped`` and ``miles_basis``.

    **The couplet note.** For a divided or undivided road the two directions run over
    the *same ground*, so ``directional_miles`` is travel-miles (the ground driven
    twice), not centre-line miles. For a one-way couplet — D3 has exactly one, Myrtle
    St EB and Front St WB — the two directions are genuinely different streets, so
    the same number is *also* distinct centre-line pavement. Either way it is the
    miles a round trip covers, which is what every rate here divides by, so the
    ranking is comparable across both; the flag exists so nobody reads the column as
    centre-line mileage for the fifteen-mile freeway.
    """
    if rank_by not in RANK_METRICS:
        raise ValueError(f"rank_by must be one of {RANK_METRICS}, got {rank_by!r}.")
    if ranking.empty:
        raise ValueError("Nothing to total: the ranking frame is empty.")
    cells, used, ungrouped = _peak_cells(ranking, membership, windows)

    name_map, couplet_ids = dict(names or {}), set(couplets or ())
    records = []
    for gid, block in cells.groupby(GROUP_COL, sort=False):
        # Miles once per direction, however many windows it appears in.
        per_dir = block.groupby(CORRIDOR_COL)["miles"]
        spread = float((per_dir.max() - per_dir.min()).max())
        directional_miles = float(per_dir.max().sum())
        delay = block["delay_min"].sum(min_count=1)
        vhd = block["vhd"].sum(min_count=1)
        tt, ff = block["travel_time_min"].sum(), block["free_flow_min"].sum()
        dirs = block.drop_duplicates(CORRIDOR_COL)
        records.append({
            GROUP_COL: gid,
            "group_name": name_map.get(gid, gid),
            "one_way_couplet": gid in couplet_ids,
            "n_directions": int(dirs[CORRIDOR_COL].nunique()),
            "directions": tuple(d for d in dirs[DIRECTION_COL] if d is not None),
            "windows": tuple(used),
            "miles": float(per_dir.max().mean()),
            "directional_miles": directional_miles,
            "miles_window_spread": spread,
            "n_segments": int(dirs["n_segments"].sum()),
            "n_obs": int(block["n_obs"].sum()),
            "min_kept_fraction": float(block["min_kept_fraction"].min()),
            "travel_time_min": float(tt),
            "free_flow_min": float(ff),
            "delay_min": delay,
            "tti": (tt / ff) if ff > 0 else float("nan"),
            "delay_per_mile": (delay / directional_miles
                               if directional_miles > 0 else float("nan")),
            "vhd": vhd,
            "vhd_per_mile": (vhd / directional_miles
                             if directional_miles > 0 and pd.notna(vhd) else float("nan")),
            "n_ramp_weighted": int(dirs["n_ramp_weighted"].sum()),
            "n_aadt_missing": int(dirs["n_aadt_missing"].sum()),
        })

    out = pd.DataFrame.from_records(records)
    for metric in RANK_METRICS:
        out[f"rank_{metric}"] = out[metric].rank(ascending=False, method="min").astype("Int64")
    out = out.sort_values(rank_by, ascending=False, na_position="last", ignore_index=True)
    out.insert(0, "rank", out[f"rank_{rank_by}"])
    all_null = bool(out[rank_by].isna().all())
    out.attrs = {
        **ranking.attrs,
        "totalled": True,
        "windows": tuple(used),
        "rank_by": rank_by,
        "rank_metric_all_null": all_null,
        "ungrouped": ungrouped,
        "miles_basis": ("directional_miles = each direction's observed miles counted "
                        "ONCE and summed; every per-mile rate divides the summed "
                        "delay by it (minutes per mile travelled, over the peaks)"),
    }
    return out


def corridor_breakout(ranking: pd.DataFrame, membership, *, names=None, windows=None,
                      order=None) -> pd.DataFrame:
    """The same cells, **unaggregated** — one row per corridor x direction x window.

    A total that cannot be opened up is a number to be taken on trust. This is the
    breakout beneath :func:`corridor_peak_totals`: a ``MultiIndex`` of
    ``(corridor_group, direction, window)`` carrying each cell's own metrics, so the
    direction split and the peak split are both visible in the same table rather
    than collapsed into a "peak direction" label.

    ``order`` is an iterable of group ids (e.g. the ranked order from
    :func:`corridor_peak_totals`) that the rows are sorted into; groups it does not
    name follow in first-seen order.
    """
    cells, used, ungrouped = _peak_cells(ranking, membership, windows)
    name_map = dict(names or {})
    out = cells.copy()
    out["group_name"] = out[GROUP_COL].map(lambda g: name_map.get(g, g))
    rank_of = {g: i for i, g in enumerate(order or ())}
    out["_g"] = out[GROUP_COL].map(lambda g: rank_of.get(g, len(rank_of)))
    win_of = {w: i for i, w in enumerate(used)}
    out["_w"] = out[WINDOW_COL].map(win_of)
    out = out.sort_values(["_g", GROUP_COL, DIRECTION_COL, "_w"]).drop(columns=["_g", "_w"])
    keep = [c for c in ("group_name", CORRIDOR_COL, "corridor_name", "n_segments",
                        "n_observed", "miles", "n_obs", "min_kept_fraction",
                        "travel_time_min", "free_flow_min", "delay_min", "tti",
                        "delay_per_mile", "vhd", "vhd_per_mile", "n_ramp_weighted",
                        "n_aadt_missing") if c in out.columns]
    out = out.set_index([GROUP_COL, DIRECTION_COL, WINDOW_COL])[keep]
    out.attrs = {**ranking.attrs, "windows": tuple(used), "ungrouped": ungrouped}
    return out


def direction_totals(breakout: pd.DataFrame) -> pd.DataFrame:
    """Per-direction totals of a :func:`corridor_breakout` — one row per
    ``(corridor_group, direction)``, summed over the windows.

    The arithmetic is :func:`corridor_peak_totals`' restricted to one direction, so
    the directions **add up** to the corridor total: ``delay_min`` and ``vhd`` sum
    to its ``delay_min`` / ``vhd``, and each ``vhd_per_mile`` divides by that
    direction's own miles (counted once, however many windows), whose sum is the
    total's ``directional_miles``.

    Accepts the breakout either as returned (``MultiIndex``) or as read back from
    its CSV (flat columns).
    """
    cells = breakout.reset_index() if GROUP_COL not in breakout.columns else breakout
    records = []
    for (gid, direction), block in cells.groupby([GROUP_COL, DIRECTION_COL], sort=False):
        miles = float(block["miles"].max())
        delay = block["delay_min"].sum(min_count=1)
        vhd = block["vhd"].sum(min_count=1)
        tt, ff = block["travel_time_min"].sum(), block["free_flow_min"].sum()
        records.append({
            GROUP_COL: gid,
            DIRECTION_COL: direction,
            "miles": miles,
            "delay_min": delay,
            "tti": (tt / ff) if ff > 0 else float("nan"),
            "delay_per_mile": delay / miles if miles > 0 else float("nan"),
            "vhd": vhd,
            "vhd_per_mile": (vhd / miles if miles > 0 and pd.notna(vhd)
                             else float("nan")),
        })
    cols = [GROUP_COL, DIRECTION_COL, "miles", "delay_min", "tti", "delay_per_mile",
            "vhd", "vhd_per_mile"]
    return pd.DataFrame.from_records(records, columns=cols)


_AADT_CAVEAT = (
    "AADT is a daily total; vhd is a relative weight at the window's mean delay, not "
    "absolute vehicle-hours unless the window is scaled to a full day "
    "(aadt.vehicle_hours_of_delay). Rows weighted by a ramp record are counted in "
    "n_ramp_weighted, not excluded (ROADMAP Item 34)."
)


def _aadt_source_lookup(aadt):
    """``Segment ID -> aadt_source`` when ``aadt`` is a :func:`aadt.join_aadt` frame,
    else ``None`` (a bare AADT Series can't say how it was matched)."""
    if aadt is None or isinstance(aadt, pd.Series):
        return None
    if _aadt.AADT_SOURCE_COL not in getattr(aadt, "columns", []):
        return None
    if aadt.index.name != SEGMENT_COL and SEGMENT_COL in aadt.columns:
        return aadt.set_index(SEGMENT_COL)[_aadt.AADT_SOURCE_COL]
    return aadt[_aadt.AADT_SOURCE_COL]


# ---------------------------------------------------------------------------
# Recurring-congestion corridor extraction  (Item 43)
# ---------------------------------------------------------------------------
#
# Everything above answers "how bad are these corridors you named". What follows
# answers the prior question: "which corridors should you name — and where do
# they start and stop?"  The idea is that extents come from the congestion, not
# from junctions and city limits: a *corridor candidate* is a contiguous run of
# segments that are recurrently congested, with a junction used to tidy an
# endpoint only when the data already lands near one.
#
# The sequence:
# 1. ``segment_recurrence`` — per-segment TTI averaged per day, then the share of
#    weekdays that exceeds a threshold.
# 2. ``extract_congestion_runs`` — walks the (repaired) ``NextXDSegI`` topology
#    through qualifying segments, bridging gaps within a tolerance, and emits
#    each maximal run.  **Never sorts geographically** — a run is a chain or it
#    is two runs, and a geographic sort is how Item 36 got a frontage road into
#    a freeway.
# 3. ``tidy_run_endpoints`` — snaps each end to a nearby state-route junction
#    within a stated tolerance, recording the snap.
# 4. ``pair_directions`` — for each run, looks for its counterpart on the
#    opposing carriageway.
# 5. ``emit_candidates`` — formats the runs as catalogue-shaped dicts,
#    deliberately without ``description`` so ``parse_catalogue`` refuses them
#    until a human adds one.

DEFAULT_TTI_THRESHOLD = 1.25
"""A segment-day is "congested" when its daily mean TTI exceeds this.  The
threshold is above the 15-min noise floor but below genuine delay; 1.25 = 25%
longer than free-flow."""

DEFAULT_RECURRENCE_THRESHOLD = 0.50
"""Share of observed weekdays a segment must be over the TTI threshold to
qualify as *recurrently* congested.  0.50 = "congested most days"."""

DEFAULT_GAP_TOLERANCE_SEGS = 1
"""A contiguous run may bridge at most this many non-qualifying segments and
remain one run."""

DEFAULT_GAP_TOLERANCE_MILES = 0.5
"""A contiguous run may bridge at most this many miles of non-qualifying
segments."""

DEFAULT_SNAP_TOLERANCE_MILES = 0.25
"""An endpoint within this many miles of a state-route junction is snapped to
that junction.  Beyond this, it stays where the data put it."""


# ---------------------------------------------------------------------------
# 1. Per-segment recurrence, computed in DuckDB
# ---------------------------------------------------------------------------
def segment_recurrence(con, area_key: str, windows=PEAK_WINDOWS,
                       cvalue_threshold: float | None = DEFAULT_CVALUE_THRESHOLD, *,
                       tti_threshold: float = DEFAULT_TTI_THRESHOLD,
                       bin_minutes: int | None = None, tz=DEFAULT_TZ,
                       date_start=None, date_end=None) -> pd.DataFrame:
    """Per-segment **recurrence**: share of weekdays that exceed a TTI threshold.

    This is the reduction :func:`segment_screen` does not do: it averages over
    the whole date range, so a fortnight of construction and a daily queue look
    alike.  Here the average is taken **per segment × window × local calendar
    day**, and recurrence is the share of *weekdays* whose daily mean TTI
    (``Travel Time / (Length / Ref Speed × 60)``) exceeds ``tti_threshold``.

    Args:
        con: an open :func:`store.connect` connection.
        area_key: the area to screen (``store.list_areas``).
        windows: the named windows — :data:`PEAK_WINDOWS` by default.
        cvalue_threshold: keep rows with ``CValue > threshold``.
        tti_threshold: daily mean TTI above this counts as "congested".
        bin_minutes / tz / date_start / date_end: as :func:`segment_screen`.

    Returns:
        A DataFrame indexed by ``Segment ID`` with per-window columns:

        - ``<name>_recurrence`` — share of observed weekdays the threshold is
          exceeded (0.0–1.0).
        - ``<name>_n_weekdays`` — weekdays observed.
        - ``<name>_n_congested`` — weekdays over the threshold.
        - ``<name>_mean_tti`` — overall (all-day) mean TTI in that window.

        Plus overall ``ref_speed``, ``n_weekdays_total`` (across all windows).
        ``attrs`` records all parameters and thresholds.
    """
    row = _store._area_row(con, area_key)
    if row is None:
        raise KeyError(f"No area {area_key!r} in the store.")
    wins = resolve_windows(windows)
    zone = _validated_tz(tz)
    bin_minutes = _store._resolve_bin(con, area_key, bin_minutes)
    obs = _store._obs_table(area_key)
    cols = _table_columns(con, obs)
    m = _metric_columns(cols)
    if m["travel_time"] is None:
        raise ValueError(f"Area {area_key!r} carries no 'Travel Time(...)' column.")
    if cvalue_threshold is not None and CVALUE_COL not in cols:
        raise ValueError(
            f"Area {area_key!r} carries no {CVALUE_COL!r} column but a gate of "
            f"{cvalue_threshold} was requested; pass cvalue_threshold=None.")

    def q(col):
        return f'"{col}"' if col else "NULL"

    where = [f'"{_store.BIN_COL}" = ?']
    params: list = [bin_minutes]
    lo, hi = _store._date_bounds_utc(date_start, date_end, zone)
    if lo is not None:
        where.append(f'"{DATETIME_COL}" >= ?')
        params.append(lo)
    if hi is not None:
        where.append(f'"{DATETIME_COL}" < ?')
        params.append(hi)

    gate = "TRUE" if cvalue_threshold is None else f'"{CVALUE_COL}" > {float(cvalue_threshold)}'

    # Step 1: compute daily means per segment per window
    # TTI = travel_time / free_flow_time.  Free-flow time needs Length(Miles) and
    # Ref Speed, but the export's travel time and speed are the two things the
    # store has — speed = Length / TT * 60, so TTI = ref_speed / speed when both
    # are present.  When speed is NULL fall back to TT-based (which would need
    # Length from the network, not available here — so we use ref/speed).
    #
    # SQL: per (segment, local_date), within each window, compute:
    #   daily_mean_speed, daily_mean_ref_speed → daily_tti = ref / speed.
    per_window_dfs = []
    for name, w in wins.items():
        pred = w.sql_predicate()
        sql = f"""
        WITH src AS (
            SELECT
                "{SEGMENT_COL}" AS sid,
                {q(m["speed"])} AS speed,
                {q(m["travel_time"])} AS tt,
                {q(m["ref_speed"])} AS ref,
                "{DATETIME_COL}" AT TIME ZONE '{zone}' AS local_dt,
                ({gate}) AS kept
            FROM "{obs}" WHERE {" AND ".join(where)}
        ), tagged AS (
            SELECT sid, speed, tt, ref, kept,
                CAST(local_dt AS DATE) AS local_date,
                date_part('hour', local_dt) * 3600 + date_part('minute', local_dt) * 60
                    + date_part('second', local_dt) AS tod,
                isodow(local_dt) AS dow
            FROM src
        ), daily AS (
            SELECT
                sid,
                local_date,
                isodow(local_date) AS date_dow,
                AVG(speed) FILTER (WHERE kept AND {pred}) AS day_speed,
                AVG(ref)   FILTER (WHERE kept AND {pred}) AS day_ref,
                COUNT(*)   FILTER (WHERE kept AND {pred}) AS day_n
            FROM tagged
            WHERE {pred}
            GROUP BY sid, local_date
            HAVING day_n > 0
        )
        SELECT
            sid AS "{SEGMENT_COL}",
            COUNT(*)                               AS n_days,
            COUNT(*) FILTER (WHERE date_dow <= 5)  AS n_weekdays,
            COUNT(*) FILTER (WHERE date_dow <= 5
                AND day_speed > 0
                AND (day_ref / day_speed) > {float(tti_threshold)})
                                                   AS n_congested,
            AVG(CASE WHEN day_speed > 0 THEN day_ref / day_speed END) AS mean_tti,
            AVG(day_ref) AS ref_speed
        FROM daily
        GROUP BY sid
        ORDER BY sid
        """
        wdf = con.execute(sql, params).df()
        if len(wdf):
            wdf[SEGMENT_COL] = wdf[SEGMENT_COL].astype("int64")
        wdf = wdf.set_index(SEGMENT_COL)

        # Compute recurrence
        n_wk = wdf["n_weekdays"].astype("float64")
        n_cong = wdf["n_congested"].astype("float64")
        wdf[f"{name}_recurrence"] = (n_cong / n_wk.where(n_wk > 0))
        wdf[f"{name}_n_weekdays"] = wdf["n_weekdays"]
        wdf[f"{name}_n_congested"] = wdf["n_congested"]
        wdf[f"{name}_mean_tti"] = wdf["mean_tti"]

        per_window_dfs.append(wdf[[
            f"{name}_recurrence", f"{name}_n_weekdays",
            f"{name}_n_congested", f"{name}_mean_tti",
        ]])

    # Merge all windows
    if not per_window_dfs:
        raise ValueError("No windows given.")
    out = per_window_dfs[0]
    for wdf in per_window_dfs[1:]:
        out = out.join(wdf, how="outer")

    # Add overall ref_speed from the screen-level query (simple overall mean)
    ref_sql = f"""
    SELECT "{SEGMENT_COL}" AS sid, AVG({q(m["ref_speed"])}) AS ref_speed
    FROM "{obs}" WHERE {" AND ".join(where)}
        AND ({gate})
    GROUP BY sid ORDER BY sid
    """
    ref_df = con.execute(ref_sql, params).df()
    if len(ref_df):
        ref_df["sid"] = ref_df["sid"].astype("int64")
    ref_df = ref_df.set_index("sid")
    out = out.join(ref_df, how="left")
    out.index.name = SEGMENT_COL

    out.attrs = {
        "area_key": area_key,
        "bin_minutes": bin_minutes,
        "tz": zone,
        "cvalue_threshold": cvalue_threshold,
        "tti_threshold": float(tti_threshold),
        "windows": {n: w.to_dict() for n, w in wins.items()},
        "date_start": date_start,
        "date_end": date_end,
    }
    return out


# ---------------------------------------------------------------------------
# 2. The CongestionRun and run extraction
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class CongestionRun:
    """One maximal contiguous run of recurrently congested segments, found by
    walking the XD topology — never by geographic sorting.

    A run is ordered in the topology's travel direction.  ``gap_segments`` are
    the non-qualifying segments that were bridged within tolerance; they are
    carried by the run so the gap count is a visible decision, not a hidden one.
    """

    segment_ids: tuple[int, ...]
    """Segment IDs in travel order (qualifying + bridged gap segments)."""
    miles: tuple[float, ...]
    """Per-segment ``Miles`` from the network."""
    total_miles: float
    """Sum of ``miles``."""
    qualifying_ids: tuple[int, ...]
    """Only the segments that meet the recurrence threshold (no gaps)."""
    gap_segments: tuple[int, ...]
    """Segment IDs that were bridged (non-qualifying, within tolerance)."""
    gaps_bridged: int
    """Number of gap spans (each may be 1+ segments) that were bridged."""
    gap_miles: float
    """Total miles of gap segments."""
    bearing: str
    """Cardinal bearing of the run (N/S/E/W/O), from the majority of members."""
    xdgroup: int | None
    """The ``XDGroup`` (carriageway key) — None if mixed or unknown."""
    road_label: str
    """De-duplicated road name(s) read off the segments."""
    road_numbers: tuple[str, ...]
    """Route numbers read off the segments (e.g. ``('55',)``)."""
    window: str
    """The peak window this run was extracted from."""
    recurrence: dict[int, float] = field(default_factory=dict)
    """Per-segment recurrence share, for the qualifying segments."""
    mean_recurrence: float = 0.0
    """Mean recurrence across qualifying segments."""
    mean_tti: float = 0.0
    """Mean TTI across all members (from the recurrence frame)."""

    @property
    def n_segments(self) -> int:
        return len(self.segment_ids)

    @property
    def n_qualifying(self) -> int:
        return len(self.qualifying_ids)

    @property
    def start_segment(self) -> int:
        return self.segment_ids[0]

    @property
    def end_segment(self) -> int:
        return self.segment_ids[-1]


def _reverse_map(nxt_map: dict[int, int | None]) -> dict[int, int]:
    """Build a ``{segment -> predecessor}`` lookup from a forward ``NextXDSegI`` map."""
    prev: dict[int, int] = {}
    for sid, nid in nxt_map.items():
        if nid is not None:
            prev[nid] = sid
    return prev


def _segment_info(network) -> dict:
    """Extract per-segment data from the network into fast lookups."""
    ids = [int(s) for s in network["XDSegID"]]
    nxt_raw = network.get("NextXDSegI", pd.Series([None] * len(ids)))
    nxt = {i: (None if pd.isna(n) else int(n))
           for i, n in zip(ids, nxt_raw)}

    miles_raw = network.get("Miles", pd.Series([float("nan")] * len(ids)))
    miles = {i: (float(m) if not pd.isna(m) else 0.0) for i, m in zip(ids, miles_raw)}

    bearing_raw = network.get("Bearing", pd.Series([None] * len(ids)))
    bearing = {i: (str(b).strip() if not pd.isna(b) else "")
               for i, b in zip(ids, bearing_raw)}

    group_raw = network.get("XDGroup", pd.Series([None] * len(ids)))
    group = {i: (None if pd.isna(g) else int(g)) for i, g in zip(ids, group_raw)}

    road_name = network.get("RoadName", pd.Series([None] * len(ids)))
    rname = {i: (str(r).strip() if not pd.isna(r) else "")
             for i, r in zip(ids, road_name)}

    road_number = network.get("RoadNumber", pd.Series([None] * len(ids)))
    rnum = {i: (str(r).strip() if not pd.isna(r) else "")
            for i, r in zip(ids, road_number)}

    return {
        "ids": set(ids),
        "nxt": nxt,
        "prev": _reverse_map(nxt),
        "miles": miles,
        "bearing": bearing,
        "group": group,
        "road_name": rname,
        "road_number": rnum,
    }


def _majority(values) -> str:
    """Most common non-empty value, or empty string."""
    counts: dict[str, int] = {}
    for v in values:
        if v:
            counts[v] = counts.get(v, 0) + 1
    if not counts:
        return ""
    return max(counts, key=counts.get)


def _unique_values(mapping: dict, keys) -> tuple:
    """De-duplicated non-empty values from ``mapping`` for ``keys``, in order."""
    seen, out = set(), []
    for k in keys:
        v = mapping.get(k, "")
        if v and v not in seen:
            seen.add(v)
            out.append(v)
    return tuple(out)


def _resolve_on_system_ids(on_system) -> set[int] | None:
    """Resolve an on_system filter (DataFrame, boolean Series, or ID collection)."""
    if on_system is None:
        return None
    if isinstance(on_system, pd.DataFrame):
        if "on_system" in on_system.columns:
            sub = on_system[on_system["on_system"]]
            if sub.index.name == SEGMENT_COL or "Segment ID" in str(sub.index.name):
                return {int(s) for s in sub.index}
            if SEGMENT_COL in sub.columns:
                return {int(s) for s in sub[SEGMENT_COL]}
            return {int(s) for s in sub.index}
        return {int(s) for s in on_system.index}
    if isinstance(on_system, pd.Series):
        if on_system.dtype == bool or on_system.dtype == "boolean":
            return {int(s) for s in on_system[on_system].index}
        return {int(s) for s in on_system}
    return {int(s) for s in on_system}


def extract_congestion_runs(
    recurrence: pd.DataFrame,
    network,
    *,
    recurrence_threshold: float = DEFAULT_RECURRENCE_THRESHOLD,
    gap_tolerance_segs: int = DEFAULT_GAP_TOLERANCE_SEGS,
    gap_tolerance_miles: float = DEFAULT_GAP_TOLERANCE_MILES,
    window: str = "am",
    repairs=None,
    on_system=None,
) -> list[CongestionRun]:
    """Walk the (repaired) XD topology and extract maximal runs of recurrently
    congested segments.

    **Never sorts geographically** — a run is a chain or it is two runs
    (ROADMAP Item 36).  The walk uses ``NextXDSegI`` (and its reverse for the
    backward extension) and only passes through segments that:

    1. Meet the ``recurrence_threshold`` in the named ``window``, **or**
    2. Are non-qualifying gaps within the tolerance (in segments AND miles).

    A gap span of ``gap_tolerance_segs`` consecutive non-qualifying segments
    totalling at most ``gap_tolerance_miles`` is bridged; longer spans split the
    run.  Every bridged gap is recorded on the result.

    A run does **not** continue past a ``XDGroup`` boundary — a different
    carriageway key is a different road.

    Args:
        recurrence: a :func:`segment_recurrence` frame, indexed by Segment ID.
        network: the XD network GeoDataFrame (or path), used for topology and
            attributes.  Must carry ``XDSegID``, ``NextXDSegI``, ``Miles``,
            ``Bearing``, ``XDGroup``, ``RoadName``, ``RoadNumber``.
        recurrence_threshold: minimum share of weekdays to qualify.
        gap_tolerance_segs: max consecutive non-qualifying segments to bridge.
        gap_tolerance_miles: max total miles of a bridged gap span.
        window: which window's recurrence to use (e.g. ``"am"``).
        repairs: optional ``NextXDSegI`` patch table (:func:`corridors.repair_links`).
        on_system: optional on-system filter (a :func:`aadt.classify_on_system`
            frame, a boolean Series of on-system flags, or an iterable of on-system
            segment IDs). Off-system segments are excluded from qualifying.

    Returns:
        A list of :class:`CongestionRun`, each a maximal contiguous run.
        ``attrs`` is not set — these are simple frozen dataclass instances.
    """
    from . import corridors as _corridors

    network = _corridors._as_network(network)
    network = _corridors.apply_link_repairs(network, repairs)
    info = _segment_info(network)
    on_sys_ids = _resolve_on_system_ids(on_system)

    rec_col = f"{window}_recurrence"
    tti_col = f"{window}_mean_tti"
    if rec_col not in recurrence.columns:
        raise KeyError(
            f"Recurrence frame has no {rec_col!r} column — "
            f"available: {[c for c in recurrence.columns if c.endswith('_recurrence')]}.")

    # Build the qualifying set
    qualifying: set[int] = set()
    rec_values: dict[int, float] = {}
    tti_values: dict[int, float] = {}
    for sid in recurrence.index:
        sid_int = int(sid)
        if sid_int not in info["ids"]:
            continue
        if on_sys_ids is not None and sid_int not in on_sys_ids:
            continue
        rv = recurrence.at[sid, rec_col]
        if pd.notna(rv) and float(rv) >= recurrence_threshold:
            qualifying.add(sid_int)
            rec_values[sid_int] = float(rv)
        if tti_col in recurrence.columns:
            tv = recurrence.at[sid, tti_col]
            if pd.notna(tv):
                tti_values[sid_int] = float(tv)

    nxt, prev = info["nxt"], info["prev"]
    miles = info["miles"]

    # Walk forward from a seed, collecting qualifying segments and bridging gaps
    visited: set[int] = set()
    runs: list[CongestionRun] = []

    def _walk_forward(seed: int) -> tuple[list[int], list[int], int, float]:
        """Walk forward from seed, collecting the run. Returns
        (ordered_ids, gap_ids, gaps_bridged, gap_miles)."""
        chain = [seed]
        gaps: list[int] = []
        n_gaps_bridged = 0
        total_gap_mi = 0.0

        cur = seed
        while True:
            nid = nxt.get(cur)
            if nid is None or nid not in info["ids"]:
                break
            if nid in visited or nid in set(chain):
                break
            # XDGroup boundary check — different carriageway splits
            if info["group"].get(nid) != info["group"].get(seed) and info["group"].get(seed) is not None:
                break
            if nid in qualifying:
                chain.append(nid)
                cur = nid
                continue
            # Try bridging a gap
            gap_span = [nid]
            gap_mi = miles.get(nid, 0.0)
            probe = nid
            bridged = False
            while (len(gap_span) <= gap_tolerance_segs
                   and gap_mi <= gap_tolerance_miles):
                pnid = nxt.get(probe)
                if pnid is None or pnid not in info["ids"]:
                    break
                if pnid in visited or pnid in set(chain) or pnid in set(gap_span):
                    break
                if info["group"].get(pnid) != info["group"].get(seed) and info["group"].get(seed) is not None:
                    break
                if pnid in qualifying:
                    # Bridge successful
                    chain.extend(gap_span)
                    chain.append(pnid)
                    gaps.extend(gap_span)
                    n_gaps_bridged += 1
                    total_gap_mi += gap_mi
                    cur = pnid
                    bridged = True
                    break
                gap_span.append(pnid)
                gap_mi += miles.get(pnid, 0.0)
                probe = pnid
            if not bridged:
                break

        return chain, gaps, n_gaps_bridged, total_gap_mi

    def _walk_backward(seed: int) -> tuple[list[int], list[int], int, float]:
        """Walk backward (via prev map) from seed. Returns
        (ordered_ids_reversed, gap_ids, gaps_bridged, gap_miles)."""
        chain = [seed]
        gaps: list[int] = []
        n_gaps_bridged = 0
        total_gap_mi = 0.0

        cur = seed
        while True:
            pid = prev.get(cur)
            if pid is None or pid not in info["ids"]:
                break
            if pid in visited or pid in set(chain):
                break
            if info["group"].get(pid) != info["group"].get(seed) and info["group"].get(seed) is not None:
                break
            if pid in qualifying:
                chain.append(pid)
                cur = pid
                continue
            # Try bridging backward
            gap_span = [pid]
            gap_mi = miles.get(pid, 0.0)
            probe = pid
            bridged = False
            while (len(gap_span) <= gap_tolerance_segs
                   and gap_mi <= gap_tolerance_miles):
                ppid = prev.get(probe)
                if ppid is None or ppid not in info["ids"]:
                    break
                if ppid in visited or ppid in set(chain) or ppid in set(gap_span):
                    break
                if info["group"].get(ppid) != info["group"].get(seed) and info["group"].get(seed) is not None:
                    break
                if ppid in qualifying:
                    chain.extend(gap_span)
                    chain.append(ppid)
                    gaps.extend(gap_span)
                    n_gaps_bridged += 1
                    total_gap_mi += gap_mi
                    cur = ppid
                    bridged = True
                    break
                gap_span.append(ppid)
                gap_mi += miles.get(ppid, 0.0)
                probe = ppid
            if not bridged:
                break

        return chain, gaps, n_gaps_bridged, total_gap_mi

    # Process each qualifying segment as a potential seed
    for seed in sorted(qualifying):
        if seed in visited:
            continue

        # Walk forward from seed
        fwd_ids, fwd_gaps, fwd_n_gaps, fwd_gap_mi = _walk_forward(seed)
        # Walk backward from seed (returns reversed order)
        bwd_ids, bwd_gaps, bwd_n_gaps, bwd_gap_mi = _walk_backward(seed)

        # Combine: backward (reversed, dropping the seed) + forward
        bwd_ids.reverse()
        all_ids = bwd_ids[:-1] + fwd_ids  # bwd includes seed at end after reverse
        if not all_ids:
            all_ids = [seed]
        all_gaps = list(set(bwd_gaps + fwd_gaps))
        total_gaps_bridged = fwd_n_gaps + bwd_n_gaps
        total_gap_mi = fwd_gap_mi + bwd_gap_mi

        # Mark all as visited
        for sid in all_ids:
            visited.add(sid)

        # Build the run
        seg_miles = tuple(miles.get(s, 0.0) for s in all_ids)
        qual_in_run = tuple(s for s in all_ids if s in qualifying)
        gap_in_run = tuple(s for s in all_ids if s in set(all_gaps))

        # Majority bearing and XDGroup
        bearings = [info["bearing"].get(s, "") for s in all_ids]
        groups = [info["group"].get(s) for s in all_ids]
        non_none_groups = [g for g in groups if g is not None]

        maj_bearing = _majority(bearings)
        maj_group = _majority([str(g) for g in non_none_groups]) if non_none_groups else None
        if maj_group is not None:
            try:
                maj_group = int(maj_group)
            except (ValueError, TypeError):
                maj_group = None

        road_names = _unique_values(info["road_name"], all_ids)
        road_numbers = _unique_values(info["road_number"], all_ids)
        label_parts = list(road_names) or list(road_numbers)
        if road_numbers and road_names:
            extra = [n for n in road_numbers if n not in road_names]
            if extra:
                label_parts.append(f"({', '.join(extra)})")
        road_label = " / ".join(label_parts) if label_parts else f"Segment {all_ids[0]}"

        per_seg_rec = {s: rec_values.get(s, 0.0) for s in qual_in_run}
        mean_rec = float(np.mean(list(per_seg_rec.values()))) if per_seg_rec else 0.0
        mean_tti_val = float(np.mean([tti_values.get(s, float("nan"))
                                       for s in all_ids
                                       if s in tti_values])) if any(
            s in tti_values for s in all_ids) else 0.0

        runs.append(CongestionRun(
            segment_ids=tuple(all_ids),
            miles=seg_miles,
            total_miles=float(sum(seg_miles)),
            qualifying_ids=qual_in_run,
            gap_segments=gap_in_run,
            gaps_bridged=total_gaps_bridged,
            gap_miles=total_gap_mi,
            bearing=maj_bearing,
            xdgroup=maj_group,
            road_label=road_label,
            road_numbers=road_numbers,
            window=window,
            recurrence=per_seg_rec,
            mean_recurrence=mean_rec,
            mean_tti=mean_tti_val,
        ))

    # Sort by total miles descending — the longest runs first
    runs.sort(key=lambda r: r.total_miles, reverse=True)
    return runs


# ---------------------------------------------------------------------------
# 3. Endpoint tidying — snap to nearby state-route junctions
# ---------------------------------------------------------------------------
def tidy_run_endpoints(
    runs: list[CongestionRun],
    network,
    *,
    snap_tolerance_miles: float = DEFAULT_SNAP_TOLERANCE_MILES,
    repairs=None,
) -> list[dict]:
    """For each run's endpoints, look for a junction with another state route
    within ``snap_tolerance_miles`` and snap to it.

    A "junction" here is a segment whose ``RoadNumber`` differs from the run's
    own — an interchange, an intersection with a cross-route.  The snap is along
    the topology (the network distance to the junction segment's endpoint), not
    a crow-flies distance.

    Args:
        runs: output of :func:`extract_congestion_runs`.
        network: the XD network GeoDataFrame.
        snap_tolerance_miles: max distance to snap each end.
        repairs: optional link repair table.

    Returns:
        A list of dicts, one per run, each carrying:

        - ``run``: the original :class:`CongestionRun`
        - ``start_snapped_to``: junction road name/number at the start, or None
        - ``start_snap_distance_miles``: distance to the snapped junction
        - ``end_snapped_to``: junction at the end, or None
        - ``end_snap_distance_miles``: distance to the snapped junction
        - ``start_segment``: the (possibly adjusted) start segment
        - ``end_segment``: the (possibly adjusted) end segment
    """
    from . import corridors as _corridors

    network = _corridors._as_network(network)
    network = _corridors.apply_link_repairs(network, repairs)
    info = _segment_info(network)
    nxt, prev = info["nxt"], info["prev"]

    results = []
    for run in runs:
        own_numbers = set(run.road_numbers)

        def _find_junction(start_seg: int, walk_map: dict, own_numbers_set: set[str]):
            """Walk along ``walk_map`` from ``start_seg`` up to tolerance,
            looking for a segment with a different ``RoadNumber``."""
            cur = start_seg
            dist_mi = 0.0
            for _ in range(20):  # safety bound
                nid = walk_map.get(cur)
                if nid is None or nid not in info["ids"]:
                    return None, 0.0, start_seg
                dist_mi += info["miles"].get(nid, 0.0)
                if dist_mi > snap_tolerance_miles:
                    return None, 0.0, start_seg
                rn = info["road_number"].get(nid, "")
                if rn and rn not in own_numbers_set:
                    junction_label = info["road_name"].get(nid, "") or f"Route {rn}"
                    return junction_label, dist_mi, nid
                cur = nid
            return None, 0.0, start_seg

        # Look backward from start for a junction
        start_snap, start_dist, start_seg = _find_junction(
            run.start_segment, prev, own_numbers)
        # Look forward from end for a junction
        end_snap, end_dist, end_seg = _find_junction(
            run.end_segment, nxt, own_numbers)

        results.append({
            "run": run,
            "start_snapped_to": start_snap,
            "start_snap_distance_miles": start_dist if start_snap else float("nan"),
            "end_snapped_to": end_snap,
            "end_snap_distance_miles": end_dist if end_snap else float("nan"),
            "start_segment": start_seg if start_snap else run.start_segment,
            "end_segment": end_seg if end_snap else run.end_segment,
        })
    return results


# ---------------------------------------------------------------------------
# 4. Directional pairing
# ---------------------------------------------------------------------------
def pair_directions(
    runs: list[CongestionRun],
    network,
    *,
    repairs=None,
) -> list[dict]:
    """For each run, find its counterpart on the opposing carriageway.

    A counterpart is a run whose segments are in a different ``XDGroup`` but
    carry the same ``RoadNumber`` and whose extent overlaps the original's.
    The pairing is by **road identity** (RoadNumber), not by proximity — the
    two carriageways of a divided highway are different groups covering the
    same ground.

    Args:
        runs: output of :func:`extract_congestion_runs`.
        network: the XD network GeoDataFrame.
        repairs: optional link repair table.

    Returns:
        A list of dicts, one per run, carrying:

        - ``run``: the original :class:`CongestionRun`
        - ``counterpart``: the opposing run (:class:`CongestionRun`), or None
        - ``counterpart_index``: index into ``runs`` of the counterpart
        - ``paired``: bool
        - ``direction``: inferred direction label for this run (NB/SB/EB/WB)
    """
    from .geometry import direction_group as _direction_group

    _OPPOSITE = {"N": "S", "S": "N", "E": "W", "W": "E"}
    _DIR_LABEL = {"N": "NB", "S": "SB", "E": "EB", "W": "WB"}

    # Index runs by their road numbers and XDGroup for fast lookup
    results = []
    for i, run in enumerate(runs):
        # Determine this run's direction from its bearing
        dir_group = _direction_group(run.bearing) if run.bearing else ""
        opp = _OPPOSITE.get(dir_group, "")
        direction_label = _DIR_LABEL.get(dir_group, run.bearing)

        counterpart = None
        counterpart_idx = None

        if run.road_numbers and opp:
            for j, other in enumerate(runs):
                if j == i:
                    continue
                if other.xdgroup == run.xdgroup:
                    continue  # same carriageway, not a counterpart
                # Check for matching road numbers
                if not (set(other.road_numbers) & set(run.road_numbers)):
                    continue
                # Check opposing direction
                other_dir = _direction_group(other.bearing) if other.bearing else ""
                if other_dir != opp:
                    continue
                # Check geographic overlap via shared road numbers
                counterpart = other
                counterpart_idx = j
                break

        results.append({
            "run": run,
            "counterpart": counterpart,
            "counterpart_index": counterpart_idx,
            "paired": counterpart is not None,
            "direction": direction_label,
        })
    return results


# ---------------------------------------------------------------------------
# 5. Emit candidates in catalogue shape
# ---------------------------------------------------------------------------
def emit_candidates(
    runs: list[CongestionRun],
    network,
    *,
    snap_tolerance_miles: float = DEFAULT_SNAP_TOLERANCE_MILES,
    repairs=None,
    window: str = "am",
    on_system=None,
    on_system_only: bool = False,
) -> list[dict]:
    """Format congestion runs as corridor catalogue candidates.

    The output is shaped like a :func:`corridors.parse_catalogue` entry — same
    fields — but **deliberately carries no ``description``**, so ``parse_catalogue``
    will refuse it until a human writes one.  That is the whole gate between a
    machine-found extent and a catalogue entry.

    Args:
        runs: output of :func:`extract_congestion_runs`.
        network: the XD network GeoDataFrame.
        snap_tolerance_miles: as :func:`tidy_run_endpoints`.
        repairs: optional link repair table.
        window: which peak window the runs came from.
        on_system: optional on-system filter (classify_on_system frame or ID set).
        on_system_only: if True, exclude runs with no state/US/interstate route number.

    Returns:
        A list of dicts, each carrying:

        - ``id``, ``name``, ``start_latlon``, ``end_latlon`` — catalogue fields
        - ``_recurrence``, ``_mean_tti``, ``_total_miles``, ``_n_segments``,
          ``_n_qualifying``, ``_gaps_bridged``, ``_gap_miles``, ``_window``,
          ``_direction``, ``_paired``, ``_counterpart_id`` — underscore-prefixed
          metadata (ignored by ``parse_catalogue``)
    """
    from . import corridors as _corridors

    network_gdf = _corridors._as_network(network)
    network_gdf = _corridors.apply_link_repairs(network_gdf, repairs)

    if on_system is not None:
        on_sys_ids = _resolve_on_system_ids(on_system)
        runs = [r for r in runs if any(s in on_sys_ids for s in r.qualifying_ids)]
    if on_system_only:
        runs = [r for r in runs if r.road_numbers]

    # Get endpoint coordinates from the network
    tidied = tidy_run_endpoints(runs, network_gdf, snap_tolerance_miles=snap_tolerance_miles)
    paired = pair_directions(runs, network_gdf, repairs=repairs)

    candidates = []
    used_ids: set[str] = set()

    for i, run in enumerate(runs):
        tidy = tidied[i]
        pair = paired[i]

        # Get lat/lon of start/end segments from the network attributes
        start_seg = tidy["start_segment"]
        end_seg = tidy["end_segment"]

        start_lat = start_lon = end_lat = end_lon = None

        # Look up coordinates from the network
        net_df = pd.DataFrame(network_gdf).drop(columns="geometry", errors="ignore")
        seg_lookup = net_df.set_index("XDSegID")

        if start_seg in seg_lookup.index:
            row = seg_lookup.loc[start_seg]
            if hasattr(row, "StartLat"):
                start_lat = float(row["StartLat"]) if not pd.isna(row.get("StartLat")) else None
                start_lon = float(row["StartLong"]) if not pd.isna(row.get("StartLong")) else None
        if end_seg in seg_lookup.index:
            row = seg_lookup.loc[end_seg]
            if hasattr(row, "EndLat"):
                end_lat = float(row["EndLat"]) if not pd.isna(row.get("EndLat")) else None
                end_lon = float(row["EndLong"]) if not pd.isna(row.get("EndLong")) else None

        # Fall back to geometry if StartLat/EndLat columns were missing
        if (start_lat is None or start_lon is None) and "geometry" in network_gdf.columns:
            m = network_gdf[network_gdf["XDSegID"] == start_seg]
            if not m.empty and m.iloc[0].geometry is not None:
                g = m.iloc[0].geometry
                if network_gdf.crs and str(network_gdf.crs) != "EPSG:4326":
                    import geopandas as gpd
                    from shapely.geometry import Point
                    pt = gpd.GeoSeries([Point(g.coords[0])], crs=network_gdf.crs).to_crs("EPSG:4326").iloc[0]
                    start_lon, start_lat = pt.x, pt.y
                else:
                    start_lon, start_lat = g.coords[0][0], g.coords[0][1]

        if (end_lat is None or end_lon is None) and "geometry" in network_gdf.columns:
            m = network_gdf[network_gdf["XDSegID"] == end_seg]
            if not m.empty and m.iloc[0].geometry is not None:
                g = m.iloc[0].geometry
                if network_gdf.crs and str(network_gdf.crs) != "EPSG:4326":
                    import geopandas as gpd
                    from shapely.geometry import Point
                    pt = gpd.GeoSeries([Point(g.coords[-1])], crs=network_gdf.crs).to_crs("EPSG:4326").iloc[0]
                    end_lon, end_lat = pt.x, pt.y
                else:
                    end_lon, end_lat = g.coords[-1][0], g.coords[-1][1]

        if start_lat is None or end_lat is None:
            continue  # no coordinates — skip

        # Build an id from the road and direction
        direction = pair["direction"]
        rnum = run.road_numbers[0] if run.road_numbers else "unk"
        base_id = f"auto-{rnum}-{direction}".lower().replace(" ", "-")
        cand_id = base_id
        suffix = 2
        while cand_id in used_ids:
            cand_id = f"{base_id}-{suffix}"
            suffix += 1
        used_ids.add(cand_id)

        # Build a name
        rname = run.road_label
        name = f"{rname} {direction}" if direction else rname

        counterpart_id = None
        if pair["counterpart"] is not None:
            cp = pair["counterpart"]
            cp_rnum = cp.road_numbers[0] if cp.road_numbers else "unk"
            cp_pair = paired[pair["counterpart_index"]]
            cp_dir = cp_pair["direction"]
            counterpart_id = f"auto-{cp_rnum}-{cp_dir}".lower().replace(" ", "-")

        candidates.append({
            "id": cand_id,
            "name": name,
            "start_latlon": [start_lat, start_lon],
            "end_latlon": [end_lat, end_lon],
            # Deliberately NO description — parse_catalogue refuses it.
            "_recurrence": run.mean_recurrence,
            "_mean_tti": run.mean_tti,
            "_total_miles": run.total_miles,
            "_n_segments": run.n_segments,
            "_n_qualifying": run.n_qualifying,
            "_gaps_bridged": run.gaps_bridged,
            "_gap_miles": run.gap_miles,
            "_window": window,
            "_direction": direction,
            "_paired": pair["paired"],
            "_counterpart_id": counterpart_id,
            "_road_numbers": list(run.road_numbers),
            "_start_snapped_to": tidy["start_snapped_to"],
            "_start_snap_distance_miles": tidy["start_snap_distance_miles"],
            "_end_snapped_to": tidy["end_snapped_to"],
            "_end_snap_distance_miles": tidy["end_snap_distance_miles"],
        })

    return candidates


__all__ = [
    "rank_corridor_groups", "corridor_peak_totals", "corridor_breakout",
    "GROUP_COL", "DIRECTION_COL", "RANK_METRICS", "DEFAULT_RANK_METRIC",
    "PeakWindow", "PEAK_WINDOWS", "WEEKDAYS", "resolve_windows",
    "segment_screen", "segment_monthly_screen", "rank_corridors",
    # Item 43 — recurring-congestion corridor extraction
    "segment_recurrence", "CongestionRun", "extract_congestion_runs",
    "tidy_run_endpoints", "pair_directions", "emit_candidates",
    "DEFAULT_TTI_THRESHOLD", "DEFAULT_RECURRENCE_THRESHOLD",
    "DEFAULT_GAP_TOLERANCE_SEGS", "DEFAULT_GAP_TOLERANCE_MILES",
    "DEFAULT_SNAP_TOLERANCE_MILES",
]


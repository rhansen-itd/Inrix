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

from dataclasses import dataclass
from typing import Mapping, Sequence
from zoneinfo import ZoneInfo

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


def resolve_windows(windows) -> dict[str, PeakWindow]:
    """Normalize a ``windows`` argument to an ordered ``{name: PeakWindow}`` dict.

    Accepts the :data:`PEAK_WINDOWS` mapping (or any ``{name: PeakWindow}``), a
    sequence of :class:`PeakWindow`, or a sequence of preset **names**
    (``["am", "pm"]``). Order is preserved — it is the column order of the screen.
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
                if w not in PEAK_WINDOWS:
                    raise KeyError(
                        f"Unknown window preset {w!r}; presets: {sorted(PEAK_WINDOWS)}.")
                items.append(PEAK_WINDOWS[w])
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
                   date_start=None, date_end=None) -> pd.DataFrame:
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

    Returns:
        A DataFrame indexed by ``Segment ID`` with

        - ``n_obs`` / ``n_obs_ungated`` / ``kept_fraction`` — rows behind the segment,
          after and before the CValue gate, and the surviving share;
        - ``ref_speed`` — mean ``Ref Speed(...)`` (INRIX's free-flow reference);
        - per window ``<name>_travel_time``, ``<name>_speed``, ``<name>_n_obs``,
          ``<name>_n_obs_ungated``, ``<name>_kept_fraction``.

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
        f"""  "{DATETIME_COL}" AT TIME ZONE '{zone}' AS local_dt""",
        f"  ({gate}) AS kept",
    ]
    agg = [
        '  sid AS "%s"' % SEGMENT_COL,
        "  count(*) AS n_obs_ungated",
        "  count(*) FILTER (WHERE kept) AS n_obs",
        "  avg(ref) FILTER (WHERE kept) AS ref_speed",
    ]
    for name, w in wins.items():
        pred = w.sql_predicate()
        agg += [
            f'  count(*) FILTER (WHERE {pred}) AS "{name}_n_obs_ungated"',
            f'  count(*) FILTER (WHERE kept AND {pred}) AS "{name}_n_obs"',
            f'  avg(tt) FILTER (WHERE kept AND {pred}) AS "{name}_travel_time"',
            f'  avg(speed) FILTER (WHERE kept AND {pred}) AS "{name}_speed"',
        ]
    sql = (
        "WITH src AS (\nSELECT\n" + ",\n".join(select_parts) +
        f'\nFROM "{obs}" WHERE {" AND ".join(where)}\n), tagged AS (\n'
        "SELECT sid, speed, tt, ref, kept,\n"
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
    for name in wins:
        order += [f"{name}_travel_time", f"{name}_speed", f"{name}_n_obs",
                  f"{name}_n_obs_ungated", f"{name}_kept_fraction"]
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
        "metric_columns": {k: v for k, v in m.items()},
    }
    return out


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


__all__ = [
    "PeakWindow", "PEAK_WINDOWS", "WEEKDAYS", "resolve_windows",
    "segment_screen", "rank_corridors",
]

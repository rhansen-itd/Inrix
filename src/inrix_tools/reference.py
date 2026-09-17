"""External travel-time reference: load a TT Logger workbook and gate it.
(ROADMAP Item 29)

The reference is a Google-Maps-style travel-time log — one **sheet per route**,
each with a small header block (Origin / Destination / Days / Start / End) above a
``Timestamp`` table sampled roughly twice an hour. This module turns that workbook
into one tidy tz-aware frame and then **checks the reference against itself**
before anyone compares it to INRIX.

The gates exist because the 2026-09-17 outside pass (DESIGN_HISTORY Session 33)
reported reference-side problems as INRIX error:

- ``nesting_gate`` — a sub-route cannot take longer than the route that contains
  it. Google's "Franklin WB - No Mid" exceeded its own full "Franklin WB" in
  **99.9% of 2,760 shared bins**; the corridor was published as a 32.2% INRIX
  error (G3). Both sheets were in hand; the check was free.
- ``coverage_gate`` — per-sheet date coverage, so a sheet that stops early cannot
  hide inside a study-wide banner ("HSB-Cascade" contributed 16 days of January
  beside 2,936-bin corridors under a "Jan 1 – Aug 24" heading — G4).
- ``lag_scan`` — the two clocks are *shown* to agree rather than assumed to, since
  the sheet headers' stated logging windows are an hour off the observed hours.

Compute only — no plotting, no GUI (see CLAUDE.md). Units are US
traffic-engineering: travel time in **minutes**.
"""
from __future__ import annotations

import datetime as _dt
import math
import re

import pandas as pd

from .io import DATETIME_COL, DEFAULT_TZ

# Tidy-frame columns.
ROUTE_COL = "Route"
TT_COL = "Travel Time(Minutes)"          # same spelling as the INRIX export
EXTRA_TT_COL = "Extra Travel Time(Minutes)"
SAMPLE_TIME_COL = "Sample Time"          # the raw sample instant, kept after binning

# Route-table columns.
ENDPOINT_KINDS = ("latlon", "place")
CHAIN_MATCHABLE_COL = "chain_matchable"

_HEADER_LABELS = ("Origin", "Destination", "Days", "Start", "End")
_LATLON_RE = re.compile(r"^\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*$")


# ---------------------------------------------------------------------------
# Header-block parsing
# ---------------------------------------------------------------------------
def parse_endpoint(value) -> dict:
    """Parse a header ``Origin``/``Destination`` cell into a typed endpoint.

    The cell is **either** a ``lat,lon`` pair (``"43.6626,-116.6646"``, with or
    without a space) **or** a place name (``"Garden Valley, ID"`` — note it also
    contains a comma, so the parse is by value, not by separator). Returns
    ``{"raw", "kind", "lat", "lon"}`` with ``kind`` in :data:`ENDPOINT_KINDS`.

    Why it matters: a place-name route is whatever the provider's geocoder chose
    that day, so it **cannot be chain-matched** against XD segments with any
    confidence (three sheets are city-to-city). Marking the kind keeps that
    limitation attached to the data instead of living in someone's memory.
    """
    raw = None if value is None or (isinstance(value, float) and math.isnan(value)) else str(value).strip()
    if not raw:
        return {"raw": None, "kind": None, "lat": float("nan"), "lon": float("nan")}
    m = _LATLON_RE.match(raw)
    if m:
        return {"raw": raw, "kind": "latlon", "lat": float(m.group(1)), "lon": float(m.group(2))}
    return {"raw": raw, "kind": "place", "lat": float("nan"), "lon": float("nan")}


def parse_clock(value):
    """Parse a header ``Start``/``End`` cell into a ``datetime.time``.

    Three dialects appear in the same workbook: a real ``datetime.time``, an Excel
    **day fraction** (``0.625`` -> 15:00 — what a cell formatted as a time but
    stored as a number reads back as), and a ``"HH:MM"`` string. Returns ``None``
    for a blank cell.
    """
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return None
    if isinstance(value, _dt.time):
        return value
    if isinstance(value, (_dt.datetime, pd.Timestamp)):
        return value.time()
    if isinstance(value, (int, float)):
        frac = float(value) % 1.0
        total = int(round(frac * 24 * 3600))
        return _dt.time(total // 3600 % 24, total % 3600 // 60, total % 60)
    text = str(value).strip()
    if not text:
        return None
    for fmt in ("%H:%M:%S", "%H:%M", "%I:%M %p", "%I:%M%p"):
        try:
            return _dt.datetime.strptime(text, fmt).time()
        except ValueError:
            continue
    return None


def _clock_text(value) -> str | None:
    """:func:`parse_clock` as an ``"HH:MM:SS"`` string — the route table stays
    JSON-serializable (it rides in ``attrs``, and a ``datetime.time`` there would
    break ``to_parquet``)."""
    t = parse_clock(value)
    return t.isoformat() if t is not None else None


def _find_col(columns, *needles):
    """First column whose lowercased name contains every needle."""
    for c in columns:
        low = str(c).lower()
        if all(n in low for n in needles):
            return c
    return None


def _split_sheet(raw: pd.DataFrame, sheet: str):
    """Split one raw sheet into (header dict, data frame). The data table starts
    at the row whose first cell is ``Timestamp``; everything above it is the
    header block."""
    first = raw.iloc[:, 0].astype("string").str.strip().str.lower()
    hits = first[first == "timestamp"].index
    if len(hits) == 0:
        raise ValueError(f"Sheet {sheet!r}: no 'Timestamp' header row found.")
    hdr_row = int(hits[0])

    meta = {}
    for i in range(hdr_row):
        label = raw.iat[i, 0]
        if pd.isna(label):
            continue
        meta[str(label).strip()] = raw.iat[i, 1] if raw.shape[1] > 1 else None

    cols = [str(c).strip() if pd.notna(c) else f"_unnamed_{j}"
            for j, c in enumerate(raw.iloc[hdr_row])]
    data = raw.iloc[hdr_row + 1:].copy()
    data.columns = cols
    return meta, data.dropna(how="all")


def load_tt_logger_routes(path, sheets=None) -> pd.DataFrame:
    """The workbook's **route table** — one row per sheet, header block only.

    Columns: ``Route``, ``origin`` / ``destination`` (raw text), ``origin_kind`` /
    ``destination_kind`` (:data:`ENDPOINT_KINDS`), ``origin_lat`` / ``origin_lon``
    / ``dest_lat`` / ``dest_lon``, ``days`` (the sheet's stated day filter),
    ``start_time`` / ``end_time`` (stated logging window — **stated**, not
    observed: Session 33 found them an hour off the timestamps, which is why
    :func:`lag_scan` exists), and :data:`CHAIN_MATCHABLE_COL` (both endpoints are
    coordinates, so ``corridors.build_chain`` can be trusted on it).
    """
    xls = pd.ExcelFile(path)
    names = list(sheets) if sheets is not None else list(xls.sheet_names)
    rows = []
    for sheet in names:
        meta, _ = _split_sheet(xls.parse(sheet, header=None), sheet)
        o = parse_endpoint(meta.get("Origin"))
        d = parse_endpoint(meta.get("Destination"))
        rows.append({
            ROUTE_COL: sheet,
            "origin": o["raw"], "destination": d["raw"],
            "origin_kind": o["kind"], "destination_kind": d["kind"],
            "origin_lat": o["lat"], "origin_lon": o["lon"],
            "dest_lat": d["lat"], "dest_lon": d["lon"],
            "days": (str(meta["Days"]).strip() if meta.get("Days") is not None
                     and pd.notna(meta.get("Days")) else None),
            "start_time": _clock_text(meta.get("Start")),
            "end_time": _clock_text(meta.get("End")),
            CHAIN_MATCHABLE_COL: o["kind"] == "latlon" and d["kind"] == "latlon",
        })
    return pd.DataFrame(rows)


def routes_frame(obs: pd.DataFrame) -> pd.DataFrame:
    """The route table carried on a tidy frame's ``attrs`` (see
    :func:`load_tt_logger`), back as a DataFrame."""
    return pd.DataFrame(obs.attrs.get("routes", []))


def route_endpoints(routes: pd.DataFrame, route: str) -> tuple[tuple[float, float], tuple[float, float]]:
    """``((start_lat, start_lon), (end_lat, end_lon))`` for a chain-matchable route
    — the argument pair ``corridors.build_chain`` takes. Raises for a place-name
    route rather than handing back a geocoded guess."""
    row = routes.loc[routes[ROUTE_COL] == route]
    if row.empty:
        raise KeyError(f"No route {route!r} in the route table.")
    row = row.iloc[0]
    if not bool(row[CHAIN_MATCHABLE_COL]):
        raise ValueError(
            f"Route {route!r} is described by place names "
            f"({row['origin']!r} -> {row['destination']!r}), not coordinates; its "
            "extent is a geocoder's guess and cannot be chain-matched."
        )
    return (row["origin_lat"], row["origin_lon"]), (row["dest_lat"], row["dest_lon"])


# ---------------------------------------------------------------------------
# Observations
# ---------------------------------------------------------------------------
def load_tt_logger(path, tz: str = DEFAULT_TZ, sheets=None, ambiguous="NaT",
                   nonexistent="NaT") -> pd.DataFrame:
    """Read a TT Logger workbook into one tidy **tz-aware** frame.

    Args:
        path: the workbook (``.xlsx``).
        tz: IANA zone the logger's wall-clock timestamps are in. The sheets carry
            **naive local** timestamps, so the zone is supplied explicitly and
            localized DST-correctly here — never sliced off a string or assumed
            (CLAUDE.md; Session 33's G9).
        sheets: restrict to these sheet names (default: all).
        ambiguous / nonexistent: DST policy, passed to ``tz_localize``. The default
            drops a sample that falls in the fall-back fold or the spring-forward
            gap and **counts** it (``attrs['n_ambiguous_dropped']``) rather than
            guessing an offset. This is not hypothetical: this logger samples
            through the 2025-11-02 fold on 9 of 15 routes.

    Returns:
        One row per sample: ``[Route, Date Time, Travel Time(Minutes),
        Extra Travel Time(Minutes)]``, sorted by route then time. Travel time is
        taken from the seconds column when present (the minutes column is the same
        value already rounded). ``attrs`` carries ``units``, ``tz``, ``source``,
        ``routes`` (the :func:`load_tt_logger_routes` table) and
        ``n_ambiguous_dropped`` — samples that fall in a DST fold/gap and cannot be
        localized are dropped and **counted**, not silently coerced.
    """
    xls = pd.ExcelFile(path)
    names = list(sheets) if sheets is not None else list(xls.sheet_names)

    frames, dropped = [], 0
    for sheet in names:
        _, data = _split_sheet(xls.parse(sheet, header=None), sheet)
        ts_col = _find_col(data.columns, "timestamp")
        sec_col = _find_col(data.columns, "travel time", "(s)")
        min_col = _find_col(data.columns, "travel time", "(min")
        extra_col = _find_col(data.columns, "extra")
        if ts_col is None or (sec_col is None and min_col is None):
            raise ValueError(f"Sheet {sheet!r}: no Timestamp / Travel Time columns.")

        stamps = pd.to_datetime(data[ts_col], errors="coerce")
        if getattr(stamps.dt, "tz", None) is None:
            local = stamps.dt.tz_localize(tz, ambiguous=ambiguous, nonexistent=nonexistent)
        else:
            local = stamps.dt.tz_convert(tz)

        minutes = (pd.to_numeric(data[sec_col], errors="coerce") / 60.0
                   if sec_col is not None else pd.to_numeric(data[min_col], errors="coerce"))
        out = pd.DataFrame({
            ROUTE_COL: sheet,
            DATETIME_COL: local,
            TT_COL: minutes.to_numpy(),
            EXTRA_TT_COL: (pd.to_numeric(data[extra_col], errors="coerce").to_numpy()
                           if extra_col is not None else float("nan")),
        })
        before = len(out)
        out = out[out[DATETIME_COL].notna()]
        dropped += before - len(out)
        frames.append(out)

    obs = (pd.concat(frames, ignore_index=True) if frames
           else pd.DataFrame(columns=[ROUTE_COL, DATETIME_COL, TT_COL, EXTRA_TT_COL]))
    obs = obs.sort_values([ROUTE_COL, DATETIME_COL]).reset_index(drop=True)
    obs.attrs = {
        "units": {"travel_time": "Minutes"},
        "tz": tz,
        "source": str(path),
        # Records, not a DataFrame: ``attrs`` is serialized on ``to_parquet``, and
        # a frame (or a ``datetime.time``) there makes the tidy frame unwritable.
        # :func:`routes_frame` turns it back into the route table.
        "routes": load_tt_logger_routes(path, sheets=names).to_dict("records"),
        "n_ambiguous_dropped": int(dropped),
    }
    return obs


def align_to_bins(obs: pd.DataFrame, bin_minutes: int = 15,
                  datetime_col: str = DATETIME_COL) -> pd.DataFrame:
    """**Floor** each sample to its bin start, keeping the raw instant.

    ``floor``, not ``round``: an INRIX bin is labelled by its **start**, so a
    sample belongs to the bin it falls *inside*. The two agree on this workbook by
    luck — samples land ~50 s past each quarter hour, well inside the first half of
    a 15-minute bin — and diverge as soon as the cadence shifts (Session 33's
    ``round("15min")`` note). Flooring is DST-correct here because it runs on the
    tz-aware local wall clock.

    Returns a copy whose ``datetime_col`` is the bin start, with the original
    instant preserved as :data:`SAMPLE_TIME_COL`. Two samples can land in the same
    bin at a fast cadence; the aggregation rule lives in ``agreement.match_bins``,
    not here.
    """
    out = obs.copy()
    stamps = out[datetime_col]
    if getattr(stamps.dt, "tz", None) is None:
        raise ValueError(
            f"{datetime_col!r} must be tz-aware before binning — localize it in "
            "load_tt_logger (or io.to_local) rather than binning naive wall clock."
        )
    out[SAMPLE_TIME_COL] = stamps
    out[datetime_col] = stamps.dt.floor(
        f"{int(bin_minutes)}min", ambiguous="NaT", nonexistent="NaT"
    )
    out = out[out[datetime_col].notna()]
    out.attrs = {**dict(obs.attrs), "bin_minutes": int(bin_minutes)}
    return out


# ---------------------------------------------------------------------------
# Gate 1 — sub-route <= full route  (G3)
# ---------------------------------------------------------------------------
def _mean_by_bin(obs, key, datetime_col, value):
    """Collapse to one value per (key, bin) — a fast cadence can land two samples
    in one bin, and a gate must not compare a route to itself twice."""
    return (obs.groupby([key, datetime_col], observed=True)[value]
            .mean().reset_index())


def nested_pairs(chains: dict) -> list[tuple[str, str]]:
    """``(sub, full)`` pairs implied by chain membership — a route nests inside
    another when its segment set is a **proper subset** of the other's.

    ``chains`` maps route name -> ``corridors.ChainResult`` (or any object with
    ``segment_ids``, or a plain id sequence). Deriving nesting from the assembled
    chains rather than from endpoint text is the point: "Franklin WB - No Mid"
    nests inside "Franklin WB", and nothing in the sheet names says so.
    """
    sets = {}
    for route, chain in chains.items():
        ids = getattr(chain, "segment_ids", chain)
        sets[route] = frozenset(int(i) for i in ids)
    pairs = []
    for sub, sub_ids in sets.items():
        for full, full_ids in sets.items():
            if sub != full and sub_ids and sub_ids < full_ids:
                pairs.append((sub, full))
    return sorted(pairs)


def nesting_gate(obs: pd.DataFrame, pairs=None, chains=None, key: str = ROUTE_COL,
                 datetime_col: str = DATETIME_COL, value: str = TT_COL,
                 tol_minutes: float = 0.0,
                 max_violation_fraction: float = 0.05) -> pd.DataFrame:
    """A sub-route cannot take longer than the route that contains it.

    For each ``(sub, full)`` pair, compares the two on their **shared bins** and
    reports the violation rate as data. This is the check that would have caught
    G3: Google's "Franklin WB - No Mid" exceeded its own full "Franklin WB" in
    99.9% of 2,760 shared bins — physically impossible, published as a 32.2% INRIX
    error.

    Args:
        obs: the tidy reference frame (bin it with :func:`align_to_bins` first, so
            "shared bins" means what it says).
        pairs: explicit ``(sub_route, full_route)`` pairs. When ``None``, derived
            from ``chains`` via :func:`nested_pairs`.
        chains: route -> ``corridors.ChainResult`` mapping, used to derive pairs.
        tol_minutes: how far a sub-route may exceed its full route on a single bin
            before that bin counts as a violation (sampling noise allowance).
        max_violation_fraction: violation rate above which ``violates`` is True.

    Returns:
        One row per pair: ``[sub_route, full_route, n_shared_bins, mean_sub,
        mean_full, mean_diff, frac_sub_exceeds_full, max_excess, violates]``.
        ``mean_diff`` is ``sub − full`` (positive = impossible direction).
    """
    if pairs is None:
        if chains is None:
            raise ValueError("nesting_gate needs either explicit `pairs` or `chains`.")
        pairs = nested_pairs(chains)

    binned = _mean_by_bin(obs, key, datetime_col, value)
    rows = []
    for sub, full in pairs:
        a = binned[binned[key] == sub][[datetime_col, value]]
        b = binned[binned[key] == full][[datetime_col, value]]
        m = a.merge(b, on=datetime_col, suffixes=("_sub", "_full"))
        excess = m[f"{value}_sub"] - m[f"{value}_full"]
        n = len(m)
        frac = float((excess > tol_minutes).mean()) if n else float("nan")
        rows.append({
            "sub_route": sub, "full_route": full, "n_shared_bins": n,
            "mean_sub": float(m[f"{value}_sub"].mean()) if n else float("nan"),
            "mean_full": float(m[f"{value}_full"].mean()) if n else float("nan"),
            "mean_diff": float(excess.mean()) if n else float("nan"),
            "frac_sub_exceeds_full": frac,
            "max_excess": float(excess.max()) if n else float("nan"),
            "violates": bool(n and frac > max_violation_fraction),
        })
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Gate 2 — per-route date coverage  (G4)
# ---------------------------------------------------------------------------
def coverage_gate(obs: pd.DataFrame, key: str = ROUTE_COL,
                  datetime_col: str = DATETIME_COL, window=None) -> pd.DataFrame:
    """Per-route date coverage, so a short sheet can't hide behind a study-wide
    banner (G4: "HSB-Cascade" contributed 16 days of January while the report's
    heading claimed Jan 1 – Aug 24 for all 15 routes).

    Args:
        obs: tidy reference (or any keyed, tz-aware) frame.
        window: optional ``(start, end)`` study window — dates, strings, or
            Timestamps. Naive bounds are localized to the frame's own zone.

    Returns:
        One row per route: ``[<key>, n_obs, first, last, n_days, span_days,
        sampled_day_fraction, obs_per_day, median_sample_minutes]`` plus, when a
        window is given, ``[window_start, window_end, window_days, days_in_window,
        window_covered_fraction, covers_window]``. ``covers_window`` is True only
        when the route's own extent spans the whole window — it says nothing about
        gaps inside it, which is what ``window_covered_fraction`` is for.

        The two cadence columns exist so a report states the sampling rate it
        **measured** rather than the one it assumed: the outside pass published
        "Data Resolution: 15-Minute Intervals" for a reference that samples about
        twice an hour (G10). ``median_sample_minutes`` is the median gap between
        consecutive samples within the route (robust to the overnight gaps between
        logging windows).
    """
    stamps = obs[datetime_col]
    tz = getattr(stamps.dt, "tz", None)
    days = stamps.dt.tz_localize(None).dt.normalize() if tz is not None else stamps.dt.normalize()
    work = obs[[key]].copy()
    work["_day"] = days
    work["_t"] = stamps

    rows = []
    bounds = None
    if window is not None:
        start, end = (pd.Timestamp(w) for w in window)
        bounds = (start.tz_localize(None) if start.tz is not None else start,
                  end.tz_localize(None) if end.tz is not None else end)

    for route, grp in work.groupby(key, observed=True, sort=True):
        day_set = grp["_day"].dropna()
        n_days = int(day_set.nunique())
        first, last = grp["_t"].min(), grp["_t"].max()
        span = int((day_set.max() - day_set.min()).days) + 1 if n_days else 0
        gaps = grp["_t"].sort_values().diff().dropna()
        row = {
            key: route, "n_obs": int(len(grp)), "first": first, "last": last,
            "n_days": n_days, "span_days": span,
            "sampled_day_fraction": (n_days / span) if span else float("nan"),
            "obs_per_day": (len(grp) / n_days) if n_days else float("nan"),
            "median_sample_minutes": (float(gaps.median().total_seconds() / 60.0)
                                      if len(gaps) else float("nan")),
        }
        if bounds is not None:
            w_start, w_end = bounds
            w_days = int((w_end.normalize() - w_start.normalize()).days) + 1
            in_win = day_set[(day_set >= w_start.normalize()) & (day_set <= w_end.normalize())]
            row.update({
                "window_start": w_start, "window_end": w_end, "window_days": w_days,
                "days_in_window": int(in_win.nunique()),
                "window_covered_fraction": (in_win.nunique() / w_days) if w_days else float("nan"),
                "covers_window": bool(n_days and day_set.min() <= w_start.normalize()
                                      and day_set.max() >= w_end.normalize()),
            })
        rows.append(row)
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Gate 3 — clock agreement  (G9)
# ---------------------------------------------------------------------------
def lag_scan(reference: pd.DataFrame, comparison: pd.DataFrame, key: str = ROUTE_COL,
             datetime_col: str = DATETIME_COL, value_ref: str = TT_COL,
             value_cmp: str | None = None, max_lag_minutes: int = 90,
             step_minutes: int = 15) -> pd.DataFrame:
    """Scan candidate clock offsets and report where the two series agree best.

    Shifts the **reference** timestamps by each lag in ``±max_lag_minutes`` and
    measures how well the two series line up on the bins they then share. A
    minimum at lag 0 is evidence the clocks agree; a minimum elsewhere means one
    series is offset (the sheet headers' stated logging windows are an hour off the
    observed timestamps, so this must be *shown*, not assumed — G9).

    The alignment statistic is ``sd_diff`` — the SD of the difference, i.e. RMSE
    with the systematic bias removed — because a lag scan is about **shape**, not
    level. Scanning on raw RMSE lets a large constant bias (which every lag shares)
    swamp the diurnal signal the scan is reading; ``rmse`` is reported too, so the
    bias-inclusive view is still there.

    Both frames must carry the same ``key`` values (label the INRIX side with the
    route names) and be binned to the same cadence.

    Returns:
        Long frame ``[<key>, lag_minutes, n, mean_diff, sd_diff, rmse, is_best]`` —
        one row per route and lag, with ``is_best`` marking each route's
        minimum-``sd_diff`` lag. :func:`lag_summary` reduces it to a verdict.
    """
    value_cmp = value_cmp or value_ref
    ref = _mean_by_bin(reference, key, datetime_col, value_ref)
    cmp_ = _mean_by_bin(comparison, key, datetime_col, value_cmp)
    if value_cmp == value_ref:
        cmp_ = cmp_.rename(columns={value_cmp: "_cmp"})
        cmp_value = "_cmp"
    else:
        cmp_value = value_cmp

    step = int(step_minutes)
    lags = range(-int(max_lag_minutes), int(max_lag_minutes) + 1, step) if step > 0 else [0]
    rows = []
    for lag in lags:
        shifted = ref.copy()
        shifted[datetime_col] = shifted[datetime_col] + pd.to_timedelta(int(lag), unit="m")
        m = shifted.merge(cmp_, on=[key, datetime_col], how="inner")
        for route, grp in m.groupby(key, observed=True, sort=True):
            resid = grp[cmp_value] - grp[value_ref]
            rows.append({
                key: route, "lag_minutes": int(lag), "n": int(len(grp)),
                "mean_diff": float(resid.mean()) if len(grp) else float("nan"),
                "sd_diff": float(resid.std(ddof=1)) if len(grp) > 1 else float("nan"),
                "rmse": float((resid ** 2).mean() ** 0.5) if len(grp) else float("nan"),
            })
    out = pd.DataFrame(rows)
    if out.empty:
        return out.assign(is_best=pd.Series(dtype=bool))
    best = out.groupby(key, observed=True)["sd_diff"].transform("min")
    out["is_best"] = out["sd_diff"] <= best
    return out.sort_values([key, "lag_minutes"]).reset_index(drop=True)


def lag_summary(scan: pd.DataFrame, key: str = ROUTE_COL,
                tol_fraction: float = 0.02) -> pd.DataFrame:
    """Reduce a :func:`lag_scan` to one verdict per route.

    Returns ``[<key>, best_lag_minutes, sd_at_best, sd_at_zero, improvement,
    n_at_zero, agrees_at_zero]``. ``improvement`` is how much the best lag beats
    lag 0 as a fraction of the lag-0 SD; ``agrees_at_zero`` is True when the best
    lag *is* 0 or beats it by less than ``tol_fraction`` — a scan that wanders by a
    fraction of a percent is a tie, not a clock offset.
    """
    rows = []
    for route, grp in scan.groupby(key, observed=True, sort=True):
        g = grp.dropna(subset=["sd_diff"])
        if g.empty:
            continue
        best = g.loc[g["sd_diff"].idxmin()]
        zero = g[g["lag_minutes"] == 0]
        sd_zero = float(zero["sd_diff"].iloc[0]) if len(zero) else float("nan")
        improvement = ((sd_zero - float(best["sd_diff"])) / sd_zero
                       if sd_zero and sd_zero == sd_zero else float("nan"))
        rows.append({
            key: route,
            "best_lag_minutes": int(best["lag_minutes"]),
            "sd_at_best": float(best["sd_diff"]),
            "sd_at_zero": sd_zero,
            "improvement": improvement,
            "n_at_zero": int(zero["n"].iloc[0]) if len(zero) else 0,
            "agrees_at_zero": bool(int(best["lag_minutes"]) == 0
                                   or (improvement == improvement and improvement < tol_fraction)),
        })
    return pd.DataFrame(rows)

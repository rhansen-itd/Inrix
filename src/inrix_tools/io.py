"""Load INRIX exports into typed, tz-aware DataFrames.  (ROADMAP Item 1)

Ports and hardens the load cells of ``_Plot Speed.ipynb``. See DATA_FORMAT.md for
the export schema, units, timezone handling, and the complete-set corridor rule.

What this fixes vs. the seed notebook:
- Reads ``data.csv`` / ``metadata.csv`` straight from the INRIX ``.zip`` (and
  concatenates split ``..._part_N.zip`` downloads) instead of a hand-unzipped dir.
- Timezone conversion is DST-correct: it converts the UTC instant to a real IANA
  zone, rather than adding a fixed ``pd.Timedelta`` offset (the notebook's numeric
  path silently mishandled the MST/MDT switch).
- ``Corridor`` comes from ``data.csv``'s ``Corridor/Region Name`` (the raw INRIX
  ``metadata.csv`` has no Corridor column — the notebook assumed a hand-edited one).
- Units are read from the column headers, not assumed to be mph/minutes.
"""
from __future__ import annotations

import re
import zipfile
from datetime import time as _time
from pathlib import Path
from typing import Iterable

import pandas as pd

# --- canonical column names in the INRIX export -----------------------------
DATETIME_COL = "Date Time"
UTC_DATETIME_COL = "UTC Date Time"
SEGMENT_COL = "Segment ID"
CVALUE_COL = "CValue"
CORRIDOR_COL = "Corridor/Region Name"
ROAD_CLOSURE_COL = "Road Closure"

DEFAULT_TZ = "America/Denver"
DEFAULT_CVALUE_THRESHOLD = 80

# columns coerced to numeric on load (names vary by unit -> matched by prefix)
_NUMERIC_PREFIXES = ("Speed(", "Hist Av Speed(", "Ref Speed(", "Travel Time(")
_NUMERIC_EXACT = (CVALUE_COL, "Pct Score30", "Pct Score20", "Pct Score10")


# ---------------------------------------------------------------------------
# Source resolution: a "source" may be a .zip, a directory, a .csv, or a list
# of those (split-part downloads).
# ---------------------------------------------------------------------------
def _read_member_csv(source, member_suffix: str, nrows: int | None = None) -> pd.DataFrame:
    """Read ``data.csv`` / ``metadata.csv`` (``member_suffix``) from one source:
    a .zip (find the member), a directory (find the file), or a direct csv path."""
    p = Path(source)
    if p.suffix.lower() == ".zip":
        with zipfile.ZipFile(p) as zf:
            # match the basename exactly — "metadata.csv".endswith("data.csv")
            # is True, so a suffix test would grab metadata for a data request.
            names = [n for n in zf.namelist() if Path(n).name == member_suffix]
            if not names:
                raise FileNotFoundError(f"No '{member_suffix}' inside {p}")
            with zf.open(names[0]) as fh:
                return pd.read_csv(fh, nrows=nrows)
    if p.is_dir():
        matches = sorted(p.glob(f"**/{member_suffix}"))
        if not matches:
            raise FileNotFoundError(f"No '{member_suffix}' under {p}")
        return pd.read_csv(matches[0], nrows=nrows)
    # a direct csv path
    return pd.read_csv(p, nrows=nrows)


def _discover_parts(source) -> list:
    """Expand a single ``..._part_1.zip`` into all sibling ``..._part_N.zip``
    parts. Any other source is returned as a one-element list unchanged."""
    if isinstance(source, (list, tuple)):
        return list(source)
    p = Path(source)
    m = re.match(r"(.*_part_)\d+\.zip$", p.name)
    if m:
        siblings = sorted(p.parent.glob(f"{m.group(1)}*.zip"))
        if siblings:
            return siblings
    return [p]


def detect_units(columns: Iterable[str]) -> dict:
    """Read speed / travel-time units from the column headers (mph vs kmh, etc.)."""
    units = {"speed": None, "travel_time": None}
    for c in columns:
        if (m := re.match(r"^Speed\((.+)\)$", c)):
            units["speed"] = m.group(1)
        elif (m := re.match(r"^Travel Time\((.+)\)$", c)):
            units["travel_time"] = m.group(1)
    return units


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def load_data(source, nrows: int | None = None) -> pd.DataFrame:
    """Load the INRIX ``data.csv`` time series into a typed, UTC-aware DataFrame.

    Args:
        source: a ``.zip`` download, a directory, a ``data.csv`` path, or a list
            of those. A single ``..._part_1.zip`` auto-discovers its sibling parts.
        nrows: optional row cap per source (for previews / tests).

    Returns:
        DataFrame with ``Date Time`` parsed to a tz-aware (UTC) timestamp,
        ``Segment ID`` as int64, numeric metrics coerced, and ``Road Closure`` as
        bool. Detected units are stored on ``df.attrs['units']``.
    """
    parts = _discover_parts(source)
    frames = [_read_member_csv(s, "data.csv", nrows=nrows) for s in parts]
    df = pd.concat(frames, ignore_index=True) if len(frames) > 1 else frames[0]

    units = detect_units(df.columns)

    if DATETIME_COL in df:
        # utc=True -> a single UTC instant regardless of the row's local offset.
        df[DATETIME_COL] = pd.to_datetime(df[DATETIME_COL], utc=True, format="ISO8601")
    if SEGMENT_COL in df:
        df[SEGMENT_COL] = df[SEGMENT_COL].astype("int64")
    for col in df.columns:
        if col in _NUMERIC_EXACT or col.startswith(_NUMERIC_PREFIXES):
            df[col] = pd.to_numeric(df[col], errors="coerce")
    if ROAD_CLOSURE_COL in df:
        df[ROAD_CLOSURE_COL] = (
            df[ROAD_CLOSURE_COL].astype(str).str.strip().str.upper().eq("T")
        )

    df.attrs["units"] = units
    return df


def load_metadata(source) -> pd.DataFrame:
    """Load the INRIX ``metadata.csv`` into a typed DataFrame indexed by
    ``Segment ID``, with a descriptive ``Combined`` label (Road + Direction +
    Intersection) for titles/legends."""
    dfm = _read_member_csv(source, "metadata.csv")

    if SEGMENT_COL in dfm:
        dfm[SEGMENT_COL] = dfm[SEGMENT_COL].astype("int64")
    for col in ("Start Latitude", "End Latitude", "Start Longitude",
                "End Longitude", "Segment Length(Miles)"):
        if col in dfm:
            dfm[col] = pd.to_numeric(dfm[col], errors="coerce")

    label_cols = [c for c in ("Road", "Direction", "Intersection") if c in dfm]
    if label_cols:
        parts = dfm[label_cols].fillna("").astype(str)
        dfm["Combined"] = parts.agg(" ".join, axis=1).str.strip().str.replace(r"\s+", " ", regex=True)

    if SEGMENT_COL in dfm:
        dfm = dfm.set_index(SEGMENT_COL)
    return dfm


def to_local(df: pd.DataFrame, tz: str = DEFAULT_TZ) -> pd.DataFrame:
    """Convert ``Date Time`` from UTC to an explicit IANA timezone (DST-correct)
    and add local ``day_of_week`` (0=Mon) and ``time_of_day`` columns.

    The timestamp stays tz-aware in ``tz`` — the local wall clock reads correctly
    across the MST/MDT switch without the notebook's fixed-offset hack.
    """
    out = df.copy()
    dt = out[DATETIME_COL]
    if dt.dt.tz is None:
        dt = dt.dt.tz_localize("UTC")
    local = dt.dt.tz_convert(tz)
    out[DATETIME_COL] = local
    out["day_of_week"] = local.dt.dayofweek
    out["time_of_day"] = local.dt.time
    out.attrs = dict(df.attrs)
    out.attrs["tz"] = tz
    return out


def filter_cvalue(df: pd.DataFrame, threshold: int = DEFAULT_CVALUE_THRESHOLD) -> pd.DataFrame:
    """Keep rows with ``CValue > threshold``; record the threshold on
    ``df.attrs['cvalue_threshold']`` for reproducibility (see DATA_FORMAT.md).

    ``> threshold`` (strict) matches the seed notebook. Adjust the threshold per
    study rather than hard-coding it downstream.
    """
    out = df[df[CVALUE_COL] > threshold].copy()
    out.attrs = dict(df.attrs)
    out.attrs["cvalue_threshold"] = threshold
    return out


def mark_complete_timestamps(df: pd.DataFrame, corridor_col: str = CORRIDOR_COL) -> pd.DataFrame:
    """Flag timestamps where **every** segment of a corridor reported — the
    complete-set rule from DATA_FORMAT.md, so partial timestamps are visible
    rather than silently summed to an undercount.

    Adds ``n_segments`` (distinct segments at this timestamp within the
    corridor), ``expected_segments`` (the corridor's max observed count), and a
    boolean ``complete``. If ``corridor_col`` is absent, the whole frame is one
    group. Filter with ``df[df['complete']]`` when summing corridor travel time.
    """
    out = df.copy()
    has_corridor = corridor_col in out.columns
    keys = [corridor_col, DATETIME_COL] if has_corridor else [DATETIME_COL]
    out["n_segments"] = out.groupby(keys)[SEGMENT_COL].transform("nunique")
    if has_corridor:
        out["expected_segments"] = out.groupby(corridor_col)["n_segments"].transform("max")
    else:
        out["expected_segments"] = int(out["n_segments"].max())
    out["complete"] = out["n_segments"] >= out["expected_segments"]
    return out

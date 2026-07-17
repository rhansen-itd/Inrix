"""AADT (traffic volume) layer + volume weighting.  (ROADMAP Item 18)

Weight the segment metrics by traffic **volume**. A segment carrying 40k
vehicles/day and one carrying 2k should not count equally when a corridor speed is
summarized, and the impact of delay is really **vehicle-hours**, not per-vehicle
minutes. AADT (Annual Average Daily Traffic) is **not** in the INRIX export — it
comes from the ITD ``Cumulative_AADT`` GIS layer (see DATA_FORMAT.md), which has
**no** ``XDSegID``, so the join to our ``Segment ID`` is necessarily **spatial**
(nearest road-following line + a bearing check to reject the opposing/cross-street
line).

Two things are kept separate on purpose (see CLAUDE.md / ROADMAP Item 18):

* **Corridor / network travel time stays a pure sum across segments (Item 12).**
  AADT does **not** re-weight it — summing member travel times is already the
  right physical quantity.
* Volume weighting applies where a *mean across segments* is summarized:
  :func:`vehicle_hours_of_delay` (delay × volume, the headline impact number,
  summable to a corridor/network total) and :func:`aadt_weighted_mean_speed`
  (Σ w·x / Σ w, weights = AADT) so a corridor speed reflects where the vehicles
  actually are.

Needs the ``geo`` extra (``geopandas`` / ``shapely`` / ``pyogrio`` / ``pyproj``),
same as :mod:`inrix_tools.geometry`. Heavy imports happen inside functions so the
package still imports without them.
"""
from __future__ import annotations

import math
from pathlib import Path

import pandas as pd

from .geometry import WGS84, _resolve_shp_path
from .io import DATETIME_COL, SEGMENT_COL

# AADT layer attribute fields we keep. The layer's ``.dbf`` carries proper numeric
# types already (unlike the all-C(255) XD shapefile), so no casting is needed — we
# only subset the columns and reproject.
_KEEP_COLS = [
    "Year", "RouteID", "Route", "FromMeasur", "ToMeasure",
    "AADT", "PassengerA", "Commercial",
]

DEFAULT_YEAR = 2024        # the layer is cumulative across years; use the latest.
AADT_COL = "AADT"
AADT_SOURCE_COL = "aadt_source"      # matched / nearest / missing
AADT_DIST_COL = "aadt_dist_m"        # match distance in metres


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------
def _bbox_to_crs(bbox, src_crs, dst_crs):
    """Reproject a ``(minx, miny, maxx, maxy)`` bbox from ``src_crs`` to ``dst_crs``
    (axis order lon/lat via ``always_xy``), returning a bbox in ``dst_crs``."""
    from pyproj import Transformer

    t = Transformer.from_crs(src_crs, dst_crs, always_xy=True)
    xs, ys = t.transform(
        [bbox[0], bbox[2], bbox[0], bbox[2]],
        [bbox[1], bbox[1], bbox[3], bbox[3]],
    )
    return (min(xs), min(ys), max(xs), max(ys))


def load_aadt(source, year=DEFAULT_YEAR, bbox=None, columns=None, cache_path=None):
    """Load (a subset of) the ITD cumulative AADT layer as a GeoDataFrame in WGS84.

    Args:
        source: the AADT ``.zip`` (e.g. ``Cumulative_AADT.zip``), a directory
            containing it, or a ``.shp`` path.
        year: keep only rows for this ``Year`` (default ``2024`` — the layer is
            cumulative across years, so an unfiltered read double-counts every
            road; see DATA_FORMAT.md). ``None`` keeps all years.
        bbox: ``(minx, miny, maxx, maxy)`` in **WGS84** to restrict the read
            spatially (pushed down to the reader, reprojected to the layer CRS so
            the 251k statewide features aren't all held). Typically the study
            export's geometry bounds.
        columns: attribute columns to keep (default :data:`_KEEP_COLS`).
        cache_path: optional GeoParquet cache — read from it when it exists, else
            build and write it.

    Returns:
        GeoDataFrame in EPSG:4326 with the kept AADT attributes and reprojected
        ``LineString`` geometry (the source is in EPSG:8826).
    """
    import geopandas as gpd

    if cache_path is not None and Path(cache_path).exists():
        return gpd.read_parquet(cache_path)

    from pyogrio import read_dataframe, read_info

    shp = _resolve_shp_path(source)
    cols = list(columns) if columns is not None else _KEEP_COLS

    where = None if year is None else f"Year = {int(year)}"

    read_bbox = None
    if bbox is not None:
        src_crs = read_info(shp)["crs"] or WGS84
        read_bbox = _bbox_to_crs(tuple(bbox), WGS84, src_crs)

    gdf = read_dataframe(shp, columns=cols, where=where, bbox=read_bbox)
    if gdf.crs is not None and gdf.crs.to_epsg() != 4326:
        gdf = gdf.to_crs(WGS84)
    elif gdf.crs is None:
        gdf = gdf.set_crs(WGS84)
    gdf = gdf.reset_index(drop=True)

    if cache_path is not None:
        Path(cache_path).parent.mkdir(parents=True, exist_ok=True)
        gdf.to_parquet(cache_path)
    return gdf


# ---------------------------------------------------------------------------
# Spatial join: AADT -> Segment ID
# ---------------------------------------------------------------------------
def _line_bearing(geom) -> float | None:
    """Endpoint-to-endpoint bearing of a line in ``[0, 180)`` degrees (undirected:
    a road and its opposing direction share the same value, a cross-street is
    ~90° off). ``None`` for empty/degenerate geometry."""
    if geom is None or getattr(geom, "is_empty", True):
        return None
    try:
        xs, ys = geom.xy
    except (NotImplementedError, AttributeError):
        return None
    if len(xs) < 2:
        return None
    dx, dy = xs[-1] - xs[0], ys[-1] - ys[0]
    if dx == 0 and dy == 0:
        return None
    return math.degrees(math.atan2(dy, dx)) % 180.0


def _bearing_diff(a: float | None, b: float | None) -> float:
    """Smallest undirected angle (deg, ``[0, 90]``) between two ``[0,180)`` bearings."""
    if a is None or b is None:
        return 180.0
    d = abs(a - b) % 180.0
    return min(d, 180.0 - d)


def _local_bearing(line, point, eps: float = 5.0) -> float | None:
    """Bearing (undirected, ``[0, 180)``) of ``line``'s local tangent at the point
    on it nearest ``point``, sampled ±``eps`` units along the line.

    A long/curved route-measure feature can lie exactly on a segment while its
    endpoint-to-endpoint bearing points somewhere else entirely, so the gate must
    compare *local* road directions, not whole-feature chords. Falls back to the
    endpoint bearing when the geometry doesn't support linear referencing or the
    tangent is degenerate."""
    try:
        s = line.project(point)
        length = line.length
        if length == 0:
            return _line_bearing(line)
        a = line.interpolate(max(0.0, s - eps))
        b = line.interpolate(min(length, s + eps))
        dx, dy = b.x - a.x, b.y - a.y
        if dx == 0 and dy == 0:
            return _line_bearing(line)
        return math.degrees(math.atan2(dy, dx)) % 180.0
    except Exception:
        return _line_bearing(line)


def join_aadt(geo, aadt, max_distance_m=35.0, bearing_tol_deg=45.0):
    """Attach an ``AADT`` value to each segment by spatial match to the AADT layer.

    The AADT layer has no segment id, so the join is spatial: for each segment
    geometry (Item 8), the nearest AADT line whose **local** bearing agrees
    (rejecting the opposing-direction split or a perpendicular cross-street)
    within ``max_distance_m`` is the match. Bearings are compared at the
    nearest-point tangents of both lines (see ``_local_bearing``), so a curved
    route-measure feature lying on the segment still matches. A bad or absent
    match is **flagged, not dropped**, so it is visible downstream — but a
    volume is attached **only for a real match**: a gate-rejected nearest line
    is reported (its ``Route`` + distance, for diagnosis) with ``AADT`` NaN, so
    a cross-street's volume can never leak into the weighted metrics.

    Args:
        geo: GeoDataFrame indexed by ``Segment ID`` with a ``geometry`` column
            (from :func:`inrix_tools.geometry.segment_geometry`), EPSG:4326.
        aadt: GeoDataFrame from :func:`load_aadt` (EPSG:4326).
        max_distance_m: a match must lie within this many metres of the segment.
        bearing_tol_deg: max undirected bearing difference for a match (≈45°
            cleanly separates same-road / opposing from a cross-street).

    Returns:
        A copy of ``geo`` with added columns:

        * ``AADT`` — matched volume (float; ``NaN`` when the segment has no
          gate-passing match — including the ``nearest`` case),
        * ``aadt_source`` — ``"matched"`` (within distance + bearing),
          ``"nearest"`` (the geometrically nearest line failed the
          distance/bearing gate — identified but **not** used for a value), or
          ``"missing"`` (no AADT line / no segment geometry),
        * ``aadt_dist_m`` — distance to the chosen line in metres (``NaN`` when
          missing),
        * ``Route`` — carried from the chosen line (also for ``nearest``, as a
          diagnostic) / ``Commercial`` — carried only for a match.
    """
    import geopandas as gpd
    from shapely import STRtree

    out = geo.copy()

    valid_geo = out[out.geometry.notna() & ~out.geometry.is_empty]
    if len(valid_geo) == 0 or len(aadt) == 0:
        out[AADT_COL] = float("nan")
        out[AADT_SOURCE_COL] = "missing"
        out[AADT_DIST_COL] = float("nan")
        out["Route"] = None
        out["Commercial"] = pd.NA
        return out

    # Distances/buffers need a metric CRS; estimate a UTM zone from the segments.
    from shapely.ops import nearest_points

    metric_crs = valid_geo.estimate_utm_crs()
    seg_m = valid_geo.geometry.to_crs(metric_crs)
    aadt_m = aadt.to_crs(metric_crs)
    aadt_geoms = list(aadt_m.geometry.values)
    tree = STRtree(aadt_geoms)

    aadt_vals = aadt[AADT_COL].astype(float).to_numpy()
    aadt_route = aadt["Route"].to_numpy() if "Route" in aadt.columns else None
    aadt_comm = aadt["Commercial"].to_numpy() if "Commercial" in aadt.columns else None

    records = {}  # Segment ID -> (aadt, source, dist, route, commercial)
    for sid, seg_geom in seg_m.items():
        # Candidate lines whose bounding box is within max_distance of the segment.
        cand = tree.query(seg_geom.buffer(max_distance_m))
        best = None  # (dist, idx) among bearing-consistent lines within distance
        for i in cand:
            d = seg_geom.distance(aadt_geoms[i])
            if d > max_distance_m:
                continue
            # Compare *local* tangents at the closest approach, not whole-line
            # chords: a curved AADT feature on the same road must pass, a
            # crossing street must not.
            p_seg, p_cand = nearest_points(seg_geom, aadt_geoms[i])
            seg_bearing = _local_bearing(seg_geom, p_cand)
            cand_bearing = _local_bearing(aadt_geoms[i], p_seg)
            if _bearing_diff(seg_bearing, cand_bearing) <= bearing_tol_deg:
                if best is None or d < best[0]:
                    best = (d, int(i))
        if best is not None:
            d, i = best
            records[sid] = (
                float(aadt_vals[i]), "matched", d,
                None if aadt_route is None else aadt_route[i],
                pd.NA if aadt_comm is None else aadt_comm[i],
            )
        else:
            # Identify the geometrically nearest line so a failed join is
            # diagnosable (which road, how far) — but attach NO volume: a
            # gate-rejected line is by definition not trusted, and its AADT
            # must not flow into the weighted metrics.
            i = int(tree.nearest(seg_geom))
            d = float(seg_geom.distance(aadt_geoms[i]))
            records[sid] = (
                float("nan"), "nearest", d,
                None if aadt_route is None else aadt_route[i], pd.NA,
            )

    def _col(pos, default):
        return [records.get(sid, default)[pos] for sid in out.index]

    missing = (float("nan"), "missing", float("nan"), None, pd.NA)
    out[AADT_COL] = _col(0, missing)
    out[AADT_SOURCE_COL] = _col(1, missing)
    out[AADT_DIST_COL] = _col(2, missing)
    out["Route"] = _col(3, missing)
    out["Commercial"] = _col(4, missing)
    return out


# ---------------------------------------------------------------------------
# Volume weighting (pure helpers)
# ---------------------------------------------------------------------------
def _aadt_series(aadt) -> pd.Series:
    """Coerce an AADT input (a ``Segment ID``-indexed Series, or a GeoDataFrame /
    DataFrame carrying an ``AADT`` column) to a ``Segment ID -> AADT`` Series."""
    if isinstance(aadt, pd.Series):
        return aadt.astype(float)
    if AADT_COL in getattr(aadt, "columns", []):
        s = aadt[AADT_COL]
        if aadt.index.name != SEGMENT_COL and SEGMENT_COL in aadt.columns:
            s = aadt.set_index(SEGMENT_COL)[AADT_COL]
        return s.astype(float)
    raise TypeError("aadt must be a Segment ID-indexed Series or carry an 'AADT' column.")


def _seg_series(values) -> pd.Series:
    """Coerce a per-segment metric input to a ``Segment ID``-indexed Series."""
    if isinstance(values, pd.Series):
        return values.astype(float)
    raise TypeError("Pass a Segment ID-indexed Series of per-segment values.")


def vehicle_hours_of_delay(mean_delay_minutes, aadt) -> pd.DataFrame:
    """Per-segment **vehicle-hours of delay** = mean delay (hours) × AADT.

    The headline volume-aware impact number (Item 17 delay × volume): a segment's
    average delay per vehicle scaled by how many vehicles it carries. Summing the
    ``vehicle_hours`` column gives a corridor/network total.

    Args:
        mean_delay_minutes: ``Segment ID``-indexed Series of average delay
            (minutes) over the analysis window (e.g. the GUI's per-segment mean).
        aadt: ``Segment ID``-indexed AADT Series, or a frame carrying an ``AADT``
            column (e.g. :func:`join_aadt` output).

    Returns:
        DataFrame indexed by ``Segment ID`` with ``mean_delay_min``, ``AADT`` and
        ``vehicle_hours``. Segments with a missing/≤0 AADT contribute ``0``
        vehicle-hours (kept as a row, not dropped).

    **Caveat (recorded on ``attrs['aadt_caveat']``):** AADT is a *daily total*, so
    this is vehicle-hours per an average day *at the window's mean delay* — a
    **relative** weight for comparing segments/corridors, not an absolute
    vehicle-hours figure unless the analysis window is scaled to a full day. It is
    not silently scaled here.
    """
    delay = _seg_series(mean_delay_minutes)
    vol = _aadt_series(aadt).reindex(delay.index)
    veh_hours = (delay / 60.0) * vol
    # A missing/≤0 volume can't carry impact — treat as 0 vehicle-hours, keep the row.
    veh_hours = veh_hours.where((vol > 0) & vol.notna(), 0.0)
    out = pd.DataFrame(
        {"mean_delay_min": delay, AADT_COL: vol, "vehicle_hours": veh_hours}
    )
    out.index.name = SEGMENT_COL
    out.attrs["aadt_caveat"] = (
        "AADT is a daily total; vehicle_hours is a relative weight at the window's "
        "mean delay, not absolute VMT unless the window is scaled to a full day."
    )
    return out


def aadt_weighted_mean_speed(mean_speed, aadt) -> float:
    """AADT-weighted mean speed across segments: ``Σ(AADT·speed) / Σ(AADT)``.

    A corridor/network speed that reflects **where the vehicles are** — the
    high-volume segments dominate — instead of a plain average that lets an empty
    frontage road count as much as the mainline.

    Args:
        mean_speed: ``Segment ID``-indexed Series of per-segment mean speed.
        aadt: ``Segment ID``-indexed AADT Series (or a frame with an ``AADT``
            column).

    Returns:
        The weighted mean (float). Segments with a missing value or a
        missing/≤0 weight are dropped from both sums; ``NaN`` if no segment has a
        positive weight and a finite value.
    """
    speed = _seg_series(mean_speed)
    vol = _aadt_series(aadt).reindex(speed.index)
    ok = speed.notna() & vol.notna() & (vol > 0)
    w = vol[ok]
    if w.sum() == 0:
        return float("nan")
    return float((speed[ok] * w).sum() / w.sum())


def weighted_speed_by_time(
    df: pd.DataFrame,
    aadt,
    speed_col: str,
    datetime_col: str = DATETIME_COL,
    out_col: str | None = None,
    min_coverage: float = 0.5,
) -> pd.DataFrame:
    """Per-timestamp AADT-weighted mean speed across the segments in ``df``.

    At each timestamp, ``Σ(AADT·speed) / Σ AADT`` over the segments that reported —
    the corridor/network speed that reflects where the vehicles are, as a time
    series the before/after and decomposition panels can run on. Unlike a corridor
    travel-time **sum** (Item 12, which requires the complete set), a weighted mean
    tolerates a missing segment (weights re-normalize over whoever reported);
    segments with a missing/≤0 AADT are dropped from the weighting.

    Re-normalization has a failure mode, though: when the segments that *did*
    report carry only a small share of the member volume, the "corridor speed" is
    really the speed of a different population — a reporting outage on the
    high-AADT mainline would otherwise read as a large speed change with nothing
    physical behind it. So each timestamp gets a ``coverage`` value (reporting
    segments' AADT ÷ all members' AADT) and timestamps below ``min_coverage`` are
    **dropped** (default 0.5; pass 0 to keep everything and judge by the column).

    Args:
        df: local rows with ``Segment ID``, a timestamp column, and ``speed_col``
            (already restricted to the corridor/network members by the caller).
        aadt: ``Segment ID``-indexed AADT Series (or a frame with an ``AADT`` column).
        speed_col: the observed speed column to average.
        out_col: name of the weighted-speed column (default ``"Weighted <speed_col>"``).
        min_coverage: drop timestamps whose reporting AADT share is below this
            fraction of the full member volume (see above).

    Returns:
        DataFrame ``[datetime_col, out_col, coverage]`` — one row per kept
        timestamp. ``attrs['weighted_speed']`` records ``min_coverage`` and the
        number of timestamps dropped by it.
    """
    out_col = out_col or f"Weighted {speed_col}"
    vol = _aadt_series(aadt)
    work = df[[SEGMENT_COL, datetime_col, speed_col]].copy()
    work["_w"] = work[SEGMENT_COL].map(vol)
    # Full member volume: every positive-AADT segment seen anywhere in df — the
    # denominator that makes per-timestamp coverage comparable across time.
    members = df[SEGMENT_COL].drop_duplicates().map(vol)
    total_w = float(members[members > 0].sum())
    work = work[(work["_w"] > 0) & work[speed_col].notna()]
    work["_wx"] = work["_w"] * work[speed_col].astype(float)
    g = (
        work.groupby(datetime_col, observed=True)
        .agg(_wx=("_wx", "sum"), _w=("_w", "sum"))
        .reset_index()
    )
    g[out_col] = g["_wx"] / g["_w"].where(g["_w"] > 0)
    g["coverage"] = g["_w"] / total_w if total_w > 0 else float("nan")
    kept = g["coverage"] >= min_coverage
    out = g.loc[kept, [datetime_col, out_col, "coverage"]].reset_index(drop=True)
    out.attrs = dict(df.attrs)
    out.attrs["weighted_speed"] = {
        "min_coverage": float(min_coverage),
        "n_dropped_low_coverage": int((~kept).sum()),
    }
    return out

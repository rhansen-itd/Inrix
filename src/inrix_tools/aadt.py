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

The match is **ranked, not nearest-wins** (ROADMAP Item 34). The ITD layer carries
one mainline centerline per divided highway — 22–30 m off each carriageway — plus a
record per ramp *movement*, and a parallel on/off ramp sits 3–8 m from the
carriageway, so plain nearest picks the ramp and one direction of an interstate ends
up weighted at a ramp's volume. :func:`classify_aadt_records` labels each record
``mainline`` / ``ramp`` / ``connector`` and :func:`join_aadt` prefers a record whose
route number matches the segment's before it prefers the near one. See DATA_FORMAT.md.

Needs the ``geo`` extra (``geopandas`` / ``shapely`` / ``pyogrio`` / ``pyproj``),
same as :mod:`inrix_tools.geometry`. Heavy imports happen inside functions so the
package still imports without them.
"""
from __future__ import annotations

import math
import re
from pathlib import Path

import pandas as pd

from .geometry import WGS84, _resolve_shp_path
from .io import DATETIME_COL, SEGMENT_COL

# AADT layer attribute fields we keep. The layer's ``.dbf`` carries proper numeric
# types already (unlike the all-C(255) XD shapefile), so no casting is needed — we
# only subset the columns and reproject.
_KEEP_COLS = [
    "Year", "RouteID", "Route", "Segment", "FromMeasur", "ToMeasure",
    "AADT", "PassengerA", "Commercial", "Descriptio", "Descript_1",
]

DEFAULT_YEAR = 2024        # the layer is cumulative across years; use the latest.
AADT_COL = "AADT"
AADT_SOURCE_COL = "aadt_source"      # matched / matched_ramp / nearest / missing
AADT_DIST_COL = "aadt_dist_m"        # match distance in metres
AADT_KIND_COL = "aadt_record_kind"   # record_kind of the chosen AADT record
AADT_DESC_COL = "aadt_desc"          # Descriptio of the chosen record (diagnostic)

RECORD_KIND_COL = "record_kind"      # on the AADT layer (classify_aadt_records)
ROUTE_CLASS_COL = "route_class"      # IN / SH / US / OH, parsed from RouteID
ROUTE_NUMBER_COL = "route_number"    # 84 / 69 / 20 ... (<NA> for an OH record)

# ``RouteID`` is the layer's real route identifier: five digits of route-segment
# number, a segment suffix letter, a two-letter class, a three-digit route number —
# ``01010AIN084`` is I-84, ``02150ASH069`` is SH-69. (The shipped ``Route`` column is
# null on every Idaho row; :func:`load_aadt` repopulates it from here.)
_ROUTE_ID_RE = re.compile(r"^(\d{5})([A-Z])([A-Z]{2})(\d{3})$")
_CLASS_PREFIX = {"IN": "I", "US": "US", "SH": "SH"}   # OH = "other highway", unnumbered

# A record's own description tells a ramp from a mainline stretch: ITD writes the
# movement (``WB OFF EAGLE RD IC #46``) for a ramp feature and the bounding
# cross-streets (``EAGLE RD IC #46``) for a mainline one. ``RAMP`` is matched
# **singular only** — the plural (``I-15 NB RAMPS IC #108``) names the interchange a
# *mainline* record runs to, and 259 of the layer's rows use it that way.
_DESC_RAMP_RE = re.compile(r"\b(?:NB|SB|EB|WB)\s+(?:ON|OFF)\b|\bRAMP\b|\bFLYOVER\b")
_DESC_CONN_RE = re.compile(r"\bCONN\b|\bJCT\b")

MAINLINE, RAMP, CONNECTOR, UNKNOWN = "mainline", "ramp", "connector", "unknown"
# Preference order when the *segment* is mainline: a mainline record, then one we
# can't read, then a connector, then a ramp.
_KIND_RANK = {MAINLINE: 0, UNKNOWN: 1, CONNECTOR: 2, RAMP: 3}

# An XD segment that is itself a ramp (so it *should* take a ramp's volume). The XD
# network states this in ``RoadName`` / ``FRC``; D3's export carries no ramp segments
# at all, but a future area may.
_SEG_RAMP_RE = re.compile(r"\b(?:ramp|exit)\b|^\s*to\s", re.I)
_SEG_RAMP_FRC = 6                    # FRC 6+ = local/minor; an interstate is FRC 1
# Route numbers out of the XD ``RoadNumber`` / ``RoadList`` fields ("84",
# "I-84|US-30", "S Eagle Rd|ID-55").
_XD_ROUTE_RE = re.compile(r"\b(?:I|US|ID|SH|SR)[- ]?(\d{1,3})\b", re.I)


# ---------------------------------------------------------------------------
# Record identity: RouteID -> route, Descriptio -> record kind  (Item 34)
# ---------------------------------------------------------------------------
def parse_route_id(route_id) -> tuple[str | None, str | None, int | None]:
    """Split a ``RouteID`` into ``(route_segment, class, number)``.

    ``"01010AIN084"`` -> ``("01010", "IN", 84)``; ``"00163AOH000"`` ->
    ``("00163", "OH", None)`` — an *other highway* record carries no route number.
    ``(None, None, None)`` for anything that doesn't parse.
    """
    m = _ROUTE_ID_RE.match(str(route_id).strip().upper()) if route_id is not None else None
    if m is None:
        return (None, None, None)
    num = int(m.group(4))
    return (m.group(1), m.group(3), num if num > 0 else None)


def route_label(route_id) -> str | None:
    """Human-readable route for a ``RouteID`` — ``"01010AIN084"`` -> ``"I-84"``,
    ``"02150ASH069"`` -> ``"SH-69"``. ``None`` for an unnumbered (``OH``) record."""
    _, cls, num = parse_route_id(route_id)
    prefix = _CLASS_PREFIX.get(cls)
    return None if (prefix is None or num is None) else f"{prefix}-{num}"


def _derive_route_fields(aadt):
    """Add ``route_class`` / ``route_number`` and (re)populate ``Route`` from
    ``RouteID``. No-op when the frame has no ``RouteID`` — a hand-built fixture's
    own ``Route`` values are left alone."""
    if "RouteID" not in aadt.columns:
        return aadt
    parsed = [parse_route_id(r) for r in aadt["RouteID"]]
    aadt[ROUTE_CLASS_COL] = [cls for _, cls, _ in parsed]
    aadt[ROUTE_NUMBER_COL] = pd.array([num for _, _, num in parsed], dtype="Int64")
    labels = pd.Series([route_label(r) for r in aadt["RouteID"]], index=aadt.index,
                       dtype=object)
    existing = aadt["Route"] if "Route" in aadt.columns else None
    aadt["Route"] = labels if existing is None else labels.where(labels.notna(), existing)
    return aadt


def _describe_kind(desc) -> str:
    """Per-record kind from a single ``Descriptio`` string (before the route roll-up)."""
    if not isinstance(desc, str):
        return UNKNOWN
    text = desc.strip().upper()
    if not text or text == "NONE":
        return UNKNOWN
    if _DESC_RAMP_RE.search(text):
        return RAMP
    if _DESC_CONN_RE.search(text):
        return CONNECTOR
    return MAINLINE


def classify_aadt_records(aadt):
    """Label every AADT record ``mainline`` / ``ramp`` / ``connector`` / ``unknown``.

    The ITD layer has no facility-type field, but it describes each record: a ramp
    feature is written as the movement it carries (``"WB OFF EAGLE RD IC #46"``,
    ``"EB ON RAMP CONN IC #42"``) while a mainline one is written as the cross-streets
    it runs between (``"EAGLE RD IC #46"`` -> ``"JCT I-184 IC #49"``). So the first
    pass reads ``Descriptio``.

    That alone mislabels the mainline, though: a carriageway *between* two ramp gores
    is legitimately described ``"EB ON COLE-OVERLAND IC"``, and I-84's mainline record
    has three such rows. The **``RouteID`` band** resolves it — ITD gives each ramp its
    own route-segment number (I-84's mainline is all ``01010AIN084``; its ramps are
    ``01098``, ``08627``, ``25580``, …) — so a second pass rolls the labels up per
    ``RouteID`` and relabels a *mainline route* (one whose records are mostly mainline
    descriptions) wholly ``mainline``. A route that is genuinely half ramp (a short
    ``US-95`` spur pairing one ramp with one street) keeps its per-record labels.

    Nothing is dropped: a segment that really *is* a ramp still needs its ramp volume,
    so this labels and lets :func:`join_aadt` decide.

    Args:
        aadt: GeoDataFrame/DataFrame from :func:`load_aadt` (needs ``Descriptio`` and
            ``RouteID``; either missing just widens ``unknown``).

    Returns:
        A copy with ``record_kind`` plus the :func:`_derive_route_fields` columns
        (``route_class`` / ``route_number`` / a populated ``Route``).
    """
    out = _derive_route_fields(aadt.copy())
    desc = out["Descriptio"] if "Descriptio" in out.columns else pd.Series(
        [None] * len(out), index=out.index, dtype=object)
    kind = pd.Series([_describe_kind(d) for d in desc], index=out.index, dtype=object)

    if "RouteID" in out.columns and len(out):
        tally = pd.crosstab(out["RouteID"], kind)
        for col in (MAINLINE, RAMP, CONNECTOR):
            if col not in tally.columns:
                tally[col] = 0
        # Strict majority: a route whose mainline descriptions outnumber its
        # ramp+connector ones is a mainline route, so its odd ramp-worded row is a
        # mainline stretch. A tie (one street + one ramp) keeps the per-record read.
        mainline_routes = set(tally.index[tally[MAINLINE] > tally[RAMP] + tally[CONNECTOR]])
        on_mainline_route = out["RouteID"].isin(mainline_routes)
        kind = kind.where(~on_mainline_route, MAINLINE)

    out[RECORD_KIND_COL] = kind
    return out


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


def load_aadt(source, year=DEFAULT_YEAR, bbox=None, columns=None, cache_path=None,
              classify=True):
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
        classify: also label each record ``mainline`` / ``ramp`` / ``connector``
            (:func:`classify_aadt_records`, Item 34) — what :func:`join_aadt` ranks
            on. Default ``True``; pass ``False`` for the raw layer.

    Returns:
        GeoDataFrame in EPSG:4326 with the kept AADT attributes and reprojected
        ``LineString`` geometry (the source is in EPSG:8826), plus ``route_class`` /
        ``route_number`` parsed from ``RouteID`` and a ``Route`` populated from it
        (the shipped ``Route`` column is **null on every Idaho row**), and
        ``record_kind`` unless ``classify=False``.
    """
    import geopandas as gpd

    if cache_path is not None and Path(cache_path).exists():
        cached = gpd.read_parquet(cache_path)
        # A cache written before Item 34 has neither; classify on the way out so an
        # old cache can't silently reinstate the nearest-wins behaviour.
        return classify_aadt_records(cached) if (
            classify and RECORD_KIND_COL not in cached.columns) else cached

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
    gdf = classify_aadt_records(gdf) if classify else _derive_route_fields(gdf)

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


def _alongside_fraction(seg, line, tol: float, n: int = 11) -> float:
    """Share of ``seg`` running within ``tol`` of ``line``, sampled at ``n`` points.

    The tie-break the ranked join needs: on this layer several records sit *on* a
    segment at distance 0.0 — the mainline centreline and, say, a rest-area ramp
    record that clips one end — and distance cannot separate them. How much of the
    segment the record actually runs beside can.
    """
    if n < 2 or seg.length == 0:
        return 0.0
    step = seg.length / (n - 1)
    hits = sum(1 for k in range(n)
               if line.distance(seg.interpolate(k * step)) <= tol)
    return hits / n


def _segment_route_numbers(geo) -> dict:
    """``Segment ID -> {route numbers}`` from the XD ``RoadNumber`` / ``RoadList``
    identity fields (Item 34, carried by ``geometry.segment_geometry``). An empty
    set means the segment names no route, so the route test can't discriminate."""
    out = {}
    num = geo["RoadNumber"] if "RoadNumber" in geo.columns else None
    lst = geo["RoadList"] if "RoadList" in geo.columns else None
    for sid in geo.index:
        nums = set()
        if num is not None:
            raw = num.loc[sid]
            if raw is not None and not (isinstance(raw, float) and math.isnan(raw)):
                text = str(raw).strip()
                if text.isdigit():
                    nums.add(int(text))
        if lst is not None:
            raw = lst.loc[sid]
            if isinstance(raw, str):
                nums.update(int(m) for m in _XD_ROUTE_RE.findall(raw))
        out[sid] = nums
    return out


def _segment_kinds(geo) -> dict:
    """``Segment ID -> "mainline" | "ramp"`` from the XD attributes.

    Only the *segment* side, and only to decide whether the mainline preference
    applies: a segment that is itself a ramp should take a ramp's volume, so it is
    matched by distance as before. Read from ``RoadName`` (INRIX names a ramp for the
    movement or the exit) and ``FRC``; anything unstated is mainline, which is the
    conservative reading — the failure this fixes is a mainline taking a ramp's count.
    """
    name = geo["RoadName"] if "RoadName" in geo.columns else None
    frc = pd.to_numeric(geo["FRC"], errors="coerce") if "FRC" in geo.columns else None
    out = {}
    for sid in geo.index:
        is_ramp = False
        if name is not None and isinstance(name.loc[sid], str):
            is_ramp = bool(_SEG_RAMP_RE.search(name.loc[sid]))
        if not is_ramp and frc is not None:
            f = frc.loc[sid]
            is_ramp = bool(pd.notna(f) and f >= _SEG_RAMP_FRC)
        out[sid] = RAMP if is_ramp else MAINLINE
    return out


def join_aadt(geo, aadt, max_distance_m=60.0, bearing_tol_deg=45.0,
              prefer_mainline=True):
    """Attach an ``AADT`` value to each segment by spatial match to the AADT layer.

    The AADT layer has no segment id, so the join is spatial: candidates are the AADT
    lines within ``max_distance_m`` of the segment (Item 8 geometry) whose **local**
    bearing agrees with it, which rejects a perpendicular cross-street while letting a
    curved route-measure feature lying on the segment through (see ``_local_bearing``;
    bearings are compared at the two lines' nearest-point tangents).

    Among those candidates the winner is chosen by a **ranked preference**, not by
    distance alone (ROADMAP Item 34):

    1. **Route.** A record whose ``RouteID`` route number matches one the segment
       names (``RoadNumber`` / ``RoadList``) beats one that doesn't. This is a
       *bonus, never a penalty*: a record that names no route and one that names the
       wrong one rank together, because a concurrency the XD side doesn't list
       (US-95 carrying SH-55 traffic at New Meadows) would otherwise push the real
       route below an unnumbered side street.
    2. **Facility.** For a mainline segment, a ``record_kind == "mainline"`` record
       beats a connector, which beats a ramp (``prefer_mainline``; see
       :func:`classify_aadt_records`). A segment that is itself a ramp skips this
       step — it *should* take a ramp's volume.
    3. **Distance**, to 0.1 m.
    4. **Coverage.** Among records the same distance away — which is the common case,
       since several of them lie *on* the segment — the one that stays alongside it
       longest wins (:func:`_alongside_fraction`). A rest-area ramp record and the
       mainline record are both 0.0 m from an interstate segment; only the mainline
       one runs its whole length.

    Nearest-wins alone is systematically wrong on a divided highway: the layer carries
    one mainline centerline, 22–30 m off each carriageway, and a record per ramp
    movement running parallel 3–8 m away — so the ramp passes the bearing gate and
    wins. On the 2026 D3 export that read I-84 westbound as ``147500, 18000, 145500,
    10500, 135000…`` segment to segment, a length-weighted 61,410 against 114,981 for
    the same ground eastbound, halving the westbound vehicle-hours of delay everything
    was ranked by. Ranking the route and the facility ahead of distance is also what
    makes the ``60 m`` default safe: it is far enough to reach the mainline centerline
    from either carriageway, which under nearest-wins would only have handed more
    segments to the ramps.

    A bad or absent match is **flagged, not dropped**, so it is visible downstream —
    but a volume is attached **only for a real match**: a gate-rejected nearest line is
    reported (route + distance, for diagnosis) with ``AADT`` NaN, so a cross-street's
    volume can never leak into the weighted metrics.

    Args:
        geo: GeoDataFrame indexed by ``Segment ID`` with a ``geometry`` column
            (from :func:`inrix_tools.geometry.segment_geometry`), EPSG:4326. Its
            ``RoadNumber`` / ``RoadList`` / ``RoadName`` / ``FRC`` columns, when
            present, drive steps 1–2; without them the join falls back to distance.
        aadt: GeoDataFrame from :func:`load_aadt` (EPSG:4326). Classified on the fly
            if it doesn't already carry ``record_kind``.
        max_distance_m: a match must lie within this many metres of the segment.
        bearing_tol_deg: max undirected bearing difference for a match (≈45°
            cleanly separates same-road / opposing from a cross-street).
        prefer_mainline: apply step 2. ``False`` reverts to route-then-distance.

    Returns:
        A copy of ``geo`` with added columns:

        * ``AADT`` — matched volume (float; ``NaN`` when the segment has no
          gate-passing match — including the ``nearest`` case),
        * ``aadt_source`` — ``"matched"`` (a mainline/unreadable record won),
          ``"matched_ramp"`` (the best record is a **ramp or connector** — the volume
          is a ramp movement's, which is right for a ramp segment and a *finding* for
          anything else), ``"nearest"`` (the geometrically nearest line failed the
          distance/bearing gate — identified but **not** used for a value), or
          ``"missing"`` (no AADT line / no segment geometry),
        * ``aadt_dist_m`` — distance to the chosen line in metres (``NaN`` when
          missing),
        * ``aadt_record_kind`` / ``aadt_desc`` — the chosen record's kind and
          ``Descriptio``, so a questionable match names itself,
        * ``RouteID`` / ``Route`` — carried from the chosen line (also for
          ``nearest``, as a diagnostic) / ``Commercial`` — carried only for a match.

        ``attrs['aadt_join']`` records the resolved policy and the source counts.
    """
    from shapely import STRtree

    out = geo.copy()
    blank = {AADT_COL: float("nan"), AADT_SOURCE_COL: "missing",
             AADT_DIST_COL: float("nan"), AADT_KIND_COL: None, AADT_DESC_COL: None,
             "RouteID": None, "Route": None, "Commercial": pd.NA}

    valid_geo = out[out.geometry.notna() & ~out.geometry.is_empty]
    if len(valid_geo) == 0 or len(aadt) == 0:
        for col, val in blank.items():
            out[col] = val
        out.attrs["aadt_join"] = _join_policy(
            max_distance_m, bearing_tol_deg, prefer_mainline, out[AADT_SOURCE_COL])
        return out

    if RECORD_KIND_COL not in aadt.columns:
        aadt = classify_aadt_records(aadt)

    # Distances/buffers need a metric CRS; estimate a UTM zone from the segments.
    from shapely.ops import nearest_points

    metric_crs = valid_geo.estimate_utm_crs()
    seg_m = valid_geo.geometry.to_crs(metric_crs)
    aadt_m = aadt.to_crs(metric_crs)
    aadt_geoms = list(aadt_m.geometry.values)
    tree = STRtree(aadt_geoms)

    aadt_vals = aadt[AADT_COL].astype(float).to_numpy()
    rec_kind = aadt[RECORD_KIND_COL].to_numpy()
    rec_num = (aadt[ROUTE_NUMBER_COL].to_numpy() if ROUTE_NUMBER_COL in aadt.columns
               else [None] * len(aadt))

    def _col_or_none(frame, col):
        return frame[col].to_numpy() if col in frame.columns else None

    aadt_route = _col_or_none(aadt, "Route")
    aadt_route_id = _col_or_none(aadt, "RouteID")
    aadt_desc = _col_or_none(aadt, "Descriptio")
    aadt_comm = _col_or_none(aadt, "Commercial")

    seg_routes = _segment_route_numbers(valid_geo)
    seg_kinds = _segment_kinds(valid_geo)

    def _take(arr, i, default=None):
        return default if arr is None else arr[i]

    records = {}
    for sid, seg_geom in seg_m.items():
        seg_nums = seg_routes.get(sid, set())
        apply_kind = prefer_mainline and seg_kinds.get(sid, MAINLINE) != RAMP
        # Candidate lines whose bounding box is within max_distance of the segment.
        cand = tree.query(seg_geom.buffer(max_distance_m))
        # (route_rank, kind_rank, dist@0.1m, -coverage, dist, idx) — lexicographic,
        # low wins; the last two only make the order total and reproducible.
        best = None
        for i in cand:
            i = int(i)
            d = seg_geom.distance(aadt_geoms[i])
            if d > max_distance_m:
                continue
            # Compare *local* tangents at the closest approach, not whole-line
            # chords: a curved AADT feature on the same road must pass, a
            # crossing street must not.
            p_seg, p_cand = nearest_points(seg_geom, aadt_geoms[i])
            seg_bearing = _local_bearing(seg_geom, p_cand)
            cand_bearing = _local_bearing(aadt_geoms[i], p_seg)
            if _bearing_diff(seg_bearing, cand_bearing) > bearing_tol_deg:
                continue
            num = rec_num[i]
            num = None if num is None or pd.isna(num) else int(num)
            route_rank = 0 if (num is not None and num in seg_nums) else 1
            kind_rank = _KIND_RANK.get(rec_kind[i], 1) if apply_kind else 0
            key = (route_rank, kind_rank, round(d, 1),
                   -_alongside_fraction(seg_geom, aadt_geoms[i], max_distance_m), d, i)
            if best is None or key < best:
                best = key
        if best is not None:
            d, i = best[4], best[5]
            kind = rec_kind[i]
            records[sid] = {
                AADT_COL: float(aadt_vals[i]),
                AADT_SOURCE_COL: "matched_ramp" if kind in (RAMP, CONNECTOR) else "matched",
                AADT_DIST_COL: d,
                AADT_KIND_COL: kind,
                AADT_DESC_COL: _take(aadt_desc, i),
                "RouteID": _take(aadt_route_id, i),
                "Route": _take(aadt_route, i),
                "Commercial": _take(aadt_comm, i, pd.NA),
            }
        else:
            # Identify the geometrically nearest line so a failed join is
            # diagnosable (which road, how far) — but attach NO volume: a
            # gate-rejected line is by definition not trusted, and its AADT
            # must not flow into the weighted metrics.
            i = int(tree.nearest(seg_geom))
            records[sid] = {
                AADT_COL: float("nan"),
                AADT_SOURCE_COL: "nearest",
                AADT_DIST_COL: float(seg_geom.distance(aadt_geoms[i])),
                AADT_KIND_COL: rec_kind[i],
                AADT_DESC_COL: _take(aadt_desc, i),
                "RouteID": _take(aadt_route_id, i),
                "Route": _take(aadt_route, i),
                "Commercial": pd.NA,
            }

    for col in blank:
        out[col] = [records.get(sid, blank)[col] for sid in out.index]
    out.attrs = dict(geo.attrs)
    out.attrs["aadt_join"] = _join_policy(
        max_distance_m, bearing_tol_deg, prefer_mainline, out[AADT_SOURCE_COL])
    return out


def _join_policy(max_distance_m, bearing_tol_deg, prefer_mainline, source) -> dict:
    """The resolved join policy + per-source counts, for ``attrs['aadt_join']`` —
    the module's convention of recording *what was actually applied* (Item 34)."""
    counts = source.value_counts().to_dict()
    return {
        "max_distance_m": float(max_distance_m),
        "bearing_tol_deg": float(bearing_tol_deg),
        "prefer_mainline": bool(prefer_mainline),
        "preference": ("route number, then mainline over connector over ramp, "
                       "then distance") if prefer_mainline else
                      "route number, then distance",
        "counts": {k: int(v) for k, v in counts.items()},
    }


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


def _aadt_source_series(aadt):
    """The ``aadt_source`` column of a :func:`join_aadt` frame, indexed by
    ``Segment ID`` — or ``None`` when the caller passed a bare AADT Series."""
    if isinstance(aadt, pd.Series) or AADT_SOURCE_COL not in getattr(aadt, "columns", []):
        return None
    if aadt.index.name != SEGMENT_COL and SEGMENT_COL in aadt.columns:
        return aadt.set_index(SEGMENT_COL)[AADT_SOURCE_COL]
    return aadt[AADT_SOURCE_COL]


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
        ``vehicle_hours`` — plus ``aadt_source`` when ``aadt`` is a
        :func:`join_aadt` frame. Segments with a missing/≤0 AADT contribute ``0``
        vehicle-hours (kept as a row, not dropped).

    **Ramp-weighted rows are flagged, not excluded** (Item 34). A ``matched_ramp``
    row is weighted by a ramp movement's count, which is *correct* for a segment that
    is itself a ramp and wrong for a mainline one — and this function can't tell them
    apart, while the caller can (the ``aadt_source`` column is carried through and
    ``attrs['aadt_ramp_rows']`` counts them). Dropping them here would silently zero
    out real ramp impact; the visible flag is the safer default. Filter on
    ``aadt_source != "matched_ramp"`` when a corridor total must be mainline-only.

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
    src = _aadt_source_series(aadt)
    if src is not None:
        out[AADT_SOURCE_COL] = src.reindex(out.index)
        out.attrs["aadt_ramp_rows"] = int((out[AADT_SOURCE_COL] == "matched_ramp").sum())
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

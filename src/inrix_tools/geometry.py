"""Segment geometry layer from the INRIX XD network shapefile.  (ROADMAP Item 8)

Real road-following polylines by lookup: the XD shapefile's ``XDSegID`` joins our
``Segment ID`` directly (see DATA_FORMAT.md), so geometry is a subset + join +
cache, not an OSM map-matching pipeline. Both KML export (``kml.py``) and the app
map (``gui/``) consume this one layer.

Needs the ``geo`` extra (``pip install -e .[geo]`` → geopandas / shapely /
pyogrio). Heavy imports happen inside functions so the package still imports
without them.
"""
from __future__ import annotations

import math
import zipfile
from pathlib import Path

# XD shapefile attribute fields we keep (all stored as C(255) — cast on load).
# Note the DBF 10-char truncation: "NextXDSegID" -> "NextXDSegI".
_KEEP_COLS = [
    "XDSegID", "PreviousXD", "NextXDSegI", "FRC", "RoadNumber", "RoadName",
    "RoadList", "Miles", "Lanes", "Bearing", "County", "District", "PostalCode",
    "State", "StartLat", "StartLong", "EndLat", "EndLong", "XDGroup", "LinearID",
]
_INT_COLS = ["XDSegID", "PreviousXD", "NextXDSegI", "FRC", "XDGroup"]
_FLOAT_COLS = ["Miles", "Lanes", "StartLat", "StartLong", "EndLat", "EndLong"]

SEGMENT_COL = "Segment ID"  # matches inrix_tools.io
WGS84 = "EPSG:4326"


# ---------------------------------------------------------------------------
# Source resolution
# ---------------------------------------------------------------------------
def _resolve_shp_path(source) -> str:
    """Return a GDAL-openable path to the ``.shp`` — from a ``.zip`` (via
    ``/vsizip/``), a directory, or a direct ``.shp``."""
    p = Path(source)
    if p.suffix.lower() == ".zip":
        with zipfile.ZipFile(p) as zf:
            shps = [n for n in zf.namelist() if n.lower().endswith(".shp")]
        if not shps:
            raise FileNotFoundError(f"No .shp inside {p}")
        return f"/vsizip/{p.resolve()}/{shps[0]}"
    if p.is_dir():
        shps = sorted(p.glob("**/*.shp"))
        if not shps:
            raise FileNotFoundError(f"No .shp under {p}")
        return str(shps[0])
    return str(p)


def _cast_xd(gdf):
    """Cast the all-C(255) shapefile fields to real numeric types (nullable
    ints for ids so a blank neighbour becomes <NA>, not 0)."""
    import pandas as pd

    for col in _INT_COLS:
        if col in gdf.columns:
            gdf[col] = pd.to_numeric(gdf[col], errors="coerce").astype("Int64")
    for col in _FLOAT_COLS:
        if col in gdf.columns:
            gdf[col] = pd.to_numeric(gdf[col], errors="coerce")
    return gdf


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def load_xd_network(source, segment_ids=None, bbox=None, cache_path=None):
    """Load (a subset of) the INRIX XD network shapefile as a GeoDataFrame.

    Args:
        source: the XD shapefile ``.zip`` (e.g. ``USA_Idaho_shapefile.zip``), a
            directory containing it, or a ``.shp`` path.
        segment_ids: restrict to these ``XDSegID`` values (an export's segments).
            Pushed down to the reader so the 266 MB statewide ``.dbf`` isn't fully
            loaded. Mutually informative with ``bbox``.
        bbox: ``(minx, miny, maxx, maxy)`` in WGS84 to restrict the read spatially.
        cache_path: if given, a GeoParquet used as a cache — read from it when it
            exists, else build and write it (so re-runs skip the shapefile).

    Returns:
        GeoDataFrame in EPSG:4326 with the kept XD attributes cast to real types
        and multi-vertex LINESTRING geometry.
    """
    import geopandas as gpd

    if cache_path is not None and Path(cache_path).exists():
        return gpd.read_parquet(cache_path)

    from pyogrio import read_dataframe

    where = None
    if segment_ids is not None:
        ids = ",".join(f"'{int(s)}'" for s in segment_ids)
        where = f"XDSegID IN ({ids})"

    gdf = read_dataframe(
        _resolve_shp_path(source),
        columns=_KEEP_COLS,
        where=where,
        bbox=tuple(bbox) if bbox is not None else None,
    )
    if gdf.crs is None:
        gdf = gdf.set_crs(WGS84)
    gdf = _cast_xd(gdf)

    if cache_path is not None:
        Path(cache_path).parent.mkdir(parents=True, exist_ok=True)
        gdf.to_parquet(cache_path)
    return gdf


def segment_geometry(network, segment_ids=None, metadata=None):
    """Resolve ``Segment ID -> LINESTRING`` from the XD network, with a
    straight-line fallback (from metadata endpoints) for any segment missing from
    the shapefile.

    Args:
        network: a GeoDataFrame from :func:`load_xd_network`.
        segment_ids: the ids to resolve; defaults to every segment in ``network``.
        metadata: optional ``io.load_metadata`` frame (indexed by ``Segment ID``);
            used to build the straight-line fallback for missing segments.

    Returns:
        GeoDataFrame indexed by ``Segment ID`` with ``geometry`` and a ``source``
        column: ``"xd"`` (real polyline), ``"fallback"`` (straight endpoint line),
        or ``"missing"`` (no geometry available).
    """
    import geopandas as gpd
    from shapely.geometry import LineString

    net = network.set_index("XDSegID") if "XDSegID" in network.columns else network
    ids = list(segment_ids) if segment_ids is not None else list(net.index)

    records = []
    for sid in ids:
        sid = int(sid)
        if sid in net.index:
            records.append((sid, net.loc[sid, "geometry"], "xd"))
        elif metadata is not None and sid in metadata.index:
            m = metadata.loc[sid]
            geom = LineString([
                (m["Start Longitude"], m["Start Latitude"]),
                (m["End Longitude"], m["End Latitude"]),
            ])
            records.append((sid, geom, "fallback"))
        else:
            records.append((sid, None, "missing"))

    gdf = gpd.GeoDataFrame(
        records, columns=[SEGMENT_COL, "geometry", "source"], crs=WGS84
    ).set_index(SEGMENT_COL)
    return gdf


def connectivity_table(network, direction="next"):
    """Downstream (or upstream) connectivity as ``Segment ID`` / ``next_id``.

    Built from ``NextXDSegI`` (``direction="next"``) or ``PreviousXD``
    (``direction="prev"``). Terminal segments (blank neighbour) are dropped. The
    ``next_id`` shape matches ``traffic_anomaly.anomaly``'s ``connectivity_table``
    and feeds corridor assembly.
    """
    src_col = "NextXDSegI" if direction == "next" else "PreviousXD"
    tbl = network[["XDSegID", src_col]].rename(
        columns={"XDSegID": SEGMENT_COL, src_col: "next_id"}
    )
    tbl = tbl.dropna(subset=["next_id"]).copy()
    tbl[SEGMENT_COL] = tbl[SEGMENT_COL].astype("int64")
    tbl["next_id"] = tbl["next_id"].astype("int64")
    return tbl.reset_index(drop=True)


# ---------------------------------------------------------------------------
# Directional display  (ROADMAP Item 20)
# ---------------------------------------------------------------------------
# Metadata ``Direction`` (or the XD ``Bearing``) is a cardinal label — ``N`` /
# ``S`` / ``E`` / ``W``, sometimes spelled ``NB`` / ``Northbound`` / a compound
# ``NE``. Two co-located opposing segments (a road's two directions) share the
# same polyline geometry, so only the one drawn last is visible/clickable on the
# map. These helpers map a raw direction to (a) a compass *group* for the display
# toggles and (b) a ``+`` / ``−`` *sign* (N & E positive, S & W negative) — the same
# signed convention the Future directional-AADT item reuses — and nudge overlapping
# opposing pairs sideways so both draw. All of this touches only the **display**
# geometry; the analytic geometry (:func:`segment_geometry`) is never mutated.
DIR_GROUP_COL = "dir_group"       # cardinal compass group N/E/S/W (or <NA>)
DIR_SIGN_COL = "dir_sign"         # +1 (N/E), -1 (S/W), 0 (unknown)
OFFSET_FLAG_COL = "offset_applied"  # True where the display geometry was nudged

# Positive/negative compass convention (N & E = +, S & W = −). Compound labels
# (``NE``, ``SW``) fold to their *primary* (first) cardinal letter for both the
# group and the sign, so ``NE`` -> ``N`` -> ``+`` and ``SW`` -> ``S`` -> ``−``.
_DIR_SIGN = {"N": 1, "E": 1, "S": -1, "W": -1}


def _primary_cardinal(direction) -> str | None:
    """The primary (first) cardinal letter ``N/E/S/W`` of a raw direction label,
    or ``None`` when it carries no recognizable cardinal. Case/spelling tolerant:
    ``"N"``, ``"nb"``, ``"Northbound"``, ``"NE"`` all resolve to ``N``."""
    if direction is None:
        return None
    s = str(direction).strip().upper()
    for ch in s:
        if ch in _DIR_SIGN:
            return ch
    return None


def direction_group(direction) -> str | None:
    """Map a raw ``Direction`` value to a cardinal compass group ``N/E/S/W`` (the
    display toggle's grouping), or ``None`` when unrecognizable. Compound labels
    fold to their primary cardinal (``"NE"`` -> ``"N"``)."""
    return _primary_cardinal(direction)


def direction_sign(direction) -> int:
    """The ``+``/``−`` sign of a direction: ``+1`` for N & E, ``-1`` for S & W,
    ``0`` when unknown. This is the signed compass convention shared with the
    Future directional-AADT item (N/E = ``+``)."""
    c = _primary_cardinal(direction)
    return _DIR_SIGN.get(c, 0)


def attach_directions(geo, directions):
    """Return a copy of ``geo`` with :data:`DIR_GROUP_COL` / :data:`DIR_SIGN_COL`
    columns derived from a ``Segment ID -> raw direction`` mapping (e.g.
    ``metadata['Direction']``). The geometry is untouched — this only annotates
    each segment with its compass group and ``±`` sign so the GUI can filter/colour
    by direction. Unknown directions get ``<NA>`` group and sign ``0``."""
    import pandas as pd

    out = geo.copy()
    dirs = directions if isinstance(directions, pd.Series) else pd.Series(directions)
    raw = out.index.map(lambda s: dirs.get(int(s)) if int(s) in dirs.index else None)
    out[DIR_GROUP_COL] = [direction_group(d) for d in raw]
    out[DIR_SIGN_COL] = [direction_sign(d) for d in raw]
    return out


def _bearing_deg(geom) -> float | None:
    """Compass bearing (degrees, 0=N, 90=E) of a linestring's start->end vector,
    or ``None`` for a degenerate/empty geometry."""
    if geom is None or getattr(geom, "is_empty", True):
        return None
    xs, ys = geom.xy
    dx, dy = xs[-1] - xs[0], ys[-1] - ys[0]
    if dx == 0 and dy == 0:
        return None
    return math.degrees(math.atan2(dx, dy)) % 360.0


def _anti_parallel(b1, b2, tol_deg) -> bool:
    """True when two bearings are roughly opposing (~180° apart within ``tol_deg``)."""
    if b1 is None or b2 is None:
        return False
    diff = abs((b1 - b2) % 360.0)
    return abs(diff - 180.0) <= tol_deg


def _deg_per_metre(lat: float):
    """(lon, lat) degrees per metre at a latitude — for small display offsets."""
    m_per_deg_lat = 111_320.0
    m_per_deg_lon = m_per_deg_lat * max(math.cos(math.radians(lat)), 1e-6)
    return 1.0 / m_per_deg_lon, 1.0 / m_per_deg_lat


def offset_overlapping_segments(geo, offset_m: float = 6.0, tol_m: float = 20.0,
                                angle_tol_deg: float = 35.0):
    """Nudge co-located **opposing** segments sideways so both draw side-by-side.

    Two segments overlap on the map when they share a road's geometry but run in
    opposite directions (e.g. a NB / SB pair): only the one plotted last is visible
    and clickable. This returns a **copy** of ``geo`` whose ``geometry`` is shifted
    perpendicular to travel (to the right-hand side of each segment's own bearing,
    so an opposing pair separates onto opposite sides) for exactly those segments;
    isolated segments keep their geometry byte-for-byte. The input GeoDataFrame is
    never mutated — the analytic geometry (:func:`segment_geometry`) is preserved.

    A boolean :data:`OFFSET_FLAG_COL` column marks which segments were shifted, so
    the offset is visible-not-silent.

    Args:
        geo: GeoDataFrame indexed by ``Segment ID`` with shapely LINESTRING
            ``geometry`` (display copy — pass ``ds.geo`` or a subset).
        offset_m: perpendicular shift, in metres, applied to each overlapping
            segment (the pair ends up ``2 * offset_m`` apart on the ground).
        tol_m: two segments count as co-located when their geometries lie within
            this many metres of each other.
        angle_tol_deg: how far from a perfect 180° their bearings may be and still
            count as opposing.

    Returns:
        A copy of ``geo`` with the display geometry offset for overlapping opposing
        pairs and an ``offset_applied`` bool column.
    """
    from shapely import affinity

    out = geo.copy()
    geoms = list(out["geometry"])
    ids = list(out.index)
    bearings = [_bearing_deg(g) for g in geoms]

    # Mean latitude for the metre<->degree conversion (small study area).
    lats = [g.interpolate(0.5, normalized=True).y
            for g in geoms if g is not None and not g.is_empty]
    lat0 = float(sum(lats) / len(lats)) if lats else 0.0
    dlon_per_m, dlat_per_m = _deg_per_metre(lat0)
    tol_deg = tol_m * dlat_per_m  # a rough metric tolerance in degrees

    n = len(geoms)
    overlap = [False] * n
    for i in range(n):
        gi = geoms[i]
        if gi is None or gi.is_empty:
            continue
        for j in range(i + 1, n):
            gj = geoms[j]
            if gj is None or gj.is_empty:
                continue
            if not _anti_parallel(bearings[i], bearings[j], angle_tol_deg):
                continue
            if gi.distance(gj) <= tol_deg:  # co-located
                overlap[i] = overlap[j] = True

    new_geoms = list(geoms)
    for i in range(n):
        if not overlap[i]:
            continue
        b = bearings[i]
        if b is None:
            continue
        # Right-hand perpendicular of the travel bearing (rotate travel by -90°):
        # north-travel shifts east, south-travel shifts west, so an opposing pair
        # lands on opposite sides. Bearing is measured clockwise from north.
        theta = math.radians(b)
        east = math.cos(theta)   # right-of-travel unit vector, east component
        north = -math.sin(theta)  # ... north component
        xoff = east * offset_m * dlon_per_m
        yoff = north * offset_m * dlat_per_m
        new_geoms[i] = affinity.translate(geoms[i], xoff=xoff, yoff=yoff)

    out["geometry"] = new_geoms
    out[OFFSET_FLAG_COL] = overlap
    return out


def to_geojson(geo) -> str:
    """GeoJSON FeatureCollection string for the map / KML layers. The index
    (e.g. ``Segment ID``) is promoted to a feature property."""
    g = geo.reset_index() if geo.index.name else geo
    return g.to_json()

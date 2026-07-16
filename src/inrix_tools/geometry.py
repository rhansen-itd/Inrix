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


def to_geojson(geo) -> str:
    """GeoJSON FeatureCollection string for the map / KML layers. The index
    (e.g. ``Segment ID``) is promoted to a feature property."""
    g = geo.reset_index() if geo.index.name else geo
    return g.to_json()

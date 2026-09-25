"""ITD reference layers: the State Highway System, the Census urban areas and the
highway tiers.  (ROADMAP Items 52, 61)

Two layers the owner supplied from ITD's ArcGIS Online, both gitignored fixtures in the
repo root (see DATA_FORMAT.md, "ITD State Highway System" / "ITD urban areas"):

* **``SHS_Primary.zip``** — ITD's State Highway System, the authority for *which road is
  a state route*. One line per piece of a route's linear reference (``RouteId`` +
  ``FromMeasur``/``ToMeasure``), in the same ``RouteId`` scheme as the AADT layer
  (``01910AUS012`` is US-12). It carries **only** state highway, so where the AADT layer
  needed a local (``OH``) record on a segment to take a route away, here the absence
  of any line is the evidence. "Primary" means **one route per road**: US-2/95 at
  Sandpoint is recorded as 95 and US-20/26/93 as 93, so concurrency is still read from
  the INRIX ``RoadList`` (:mod:`routes`).
* **``Urban_Area.zip``** — 26 Census 2020 urban-area polygons (``UACE``, name,
  population, density). They give each segment an urban *context*, which is **never a
  gate**: the boundaries say where to look for a rural/urban transition, not where a
  corridor must stop (owner, 2026-09-23; ROADMAP Items 50 and 52).

The SHS codes, as read from the layer against the AADT descriptions of the same
``RouteId`` (Session 67):

* ``RoadType`` — **4 = roadway** (the route itself: 4,842 of the 6,000 mi on
  ``Travelway`` A), **5 = ramp** (724 of its 794 records match an AADT ramp record,
  ``EB OFF TWIN FALLS IC173``), **6 = other short pieces** (81 records, 31 mi, mostly
  interchange pieces with no AADT record). Only ``RoadType`` 4 is mainline evidence.
* ``RouteTypeC`` — **1 = mainline route**, **2 = spur** (``US-95 SPUR``, SH-77's
  Elba-Almo spur), **3 = business loop** (``LoopSpurCo`` numbers them; Mountain Home's
  I-84 loop is ``01020AIN084``), **4 = connector** (short couplet / wye pieces). A spur
  and a connector *are* the route; a business loop is state highway that belongs to its
  parent route, labelled so it can be told apart.
* ``Travelway`` — **A = the primary carriageway**, **D = the second carriageway of a
  divided road** (``01010DIN084`` runs the full 275 mi of I-84 beside ``01010AIN084``).
  With both, each INRIX carriageway lies on a line of its own, unlike the AADT layer's
  one centreline 22–30 m off each (Item 34).

* **``Highway Tier.geojson``** (Item 61) — ITD's highway tiers, Interstate > Expressway >
  State > Regional > District, by ``segcode`` + milepost. A count station's section
  breaks where a same-or-higher-tier state route meets it (:mod:`route_sections`).

Pure: paths come from the caller, no plotting. Heavy imports happen inside functions.
"""
from __future__ import annotations

import pandas as pd

from .aadt import parse_route_id
from .geometry import WGS84, _resolve_shp_path

# ---------------------------------------------------------------------------
# State Highway System
# ---------------------------------------------------------------------------
SHS_COLUMNS = ["RouteId", "FromMeasur", "ToMeasure", "SHSNumber", "SignTypeCo",
               "LoopSpurCo", "RouteTypeC", "RoadType", "Travelway", "FromDate", "ToDate"]

ROAD_TYPE_ROADWAY = 4
ROAD_TYPE_RAMP = 5
ROAD_TYPE_OTHER = 6
ROAD_KINDS = {ROAD_TYPE_ROADWAY: "roadway", ROAD_TYPE_RAMP: "ramp", ROAD_TYPE_OTHER: "other"}

ROUTE_TYPES = {1: "mainline", 2: "spur", 3: "business", 4: "connector"}
"""``RouteTypeC`` -> what the line is to its route."""
MEMBER_ROUTE_TYPES = ("mainline", "spur", "connector")
"""Route types that *are* the numbered route. A business loop is state highway too and
joins its parent route's membership, labelled ``business`` (:mod:`routes`)."""

SIGN_TYPES = {1: "IN", 2: "US", 3: "SH"}
"""``SignTypeCo`` -> the ``RouteId`` class band it matches."""

SHS_ROAD_KIND_COL = "road_kind"      # roadway / ramp / other      (RoadType)
SHS_ROUTE_TYPE_COL = "route_type"    # mainline / spur / business / connector (RouteTypeC)
SHS_CARRIAGEWAY_COL = "carriageway"  # "A" primary / "D" second carriageway (Travelway)
SHS_ROUTE_NUMBER_COL = "route_number"
SHS_ROUTE_CLASS_COL = "route_class"


def load_shs(source, bbox=None, as_of=None):
    """Load ITD's State Highway System as a 2-D GeoDataFrame in WGS84.

    Args:
        source: ``SHS_Primary.zip``, a directory holding it, or the ``.shp``.
        bbox: optional ``(minx, miny, maxx, maxy)`` in WGS84 to restrict the read.
        as_of: optional date; keep only lines in force on it (``FromDate <= as_of``
            and ``ToDate`` unset or later). Default keeps lines with no ``ToDate`` —
            the current system (the 2026-09-23 download has none set).

    Returns:
        One row per **part** — the layer's one ``MultiLineString`` is exploded, and the
        ``M`` values GDAL cannot read (``Measured 3D LineString``) are dropped with the
        ``Z`` — carrying :data:`SHS_COLUMNS` plus ``RouteID`` (the ``RouteId`` renamed to
        the AADT layer's spelling, so :func:`aadt.parse_route_id` and the route-evidence
        code read both), ``route_class`` / ``route_number`` from the band,
        ``road_kind``, ``route_type`` and ``carriageway``. ``attrs['shs_layer']``
        records the read.
    """
    import warnings

    import shapely
    from pyogrio import read_dataframe, read_info

    shp = _resolve_shp_path(source)
    read_bbox = None
    with warnings.catch_warnings():
        # "Measured (M) geometry types are not supported" — expected; the measures
        # live in FromMeasur/ToMeasure, which is all we use.
        warnings.simplefilter("ignore", UserWarning)
        if bbox is not None:
            from .aadt import _bbox_to_crs
            read_bbox = _bbox_to_crs(tuple(bbox), WGS84, read_info(shp)["crs"] or WGS84)
        gdf = read_dataframe(shp, columns=SHS_COLUMNS, bbox=read_bbox)
    n_read = len(gdf)
    if as_of is None:
        gdf = gdf[gdf["ToDate"].isna()]
    else:
        when = pd.Timestamp(as_of)
        gdf = gdf[(gdf["FromDate"].isna() | (gdf["FromDate"] <= when))
                  & (gdf["ToDate"].isna() | (gdf["ToDate"] > when))]
    gdf = gdf.explode(index_parts=False).reset_index(drop=True)
    gdf = gdf.set_geometry(shapely.force_2d(gdf.geometry.values), crs=gdf.crs)
    gdf = gdf.to_crs(WGS84) if gdf.crs is not None else gdf.set_crs(WGS84)
    return _derive_shs_fields(gdf, n_read=n_read, as_of=as_of)


def _derive_shs_fields(gdf, *, n_read=None, as_of=None):
    """Add the parsed and decoded columns :func:`load_shs` documents (also used by
    tests that build a synthetic layer)."""
    out = gdf.rename(columns={"RouteId": "RouteID"}).copy()
    parsed = [parse_route_id(r) for r in out["RouteID"]]
    out[SHS_ROUTE_CLASS_COL] = [cls for _, cls, _ in parsed]
    out[SHS_ROUTE_NUMBER_COL] = pd.array([num for _, _, num in parsed], dtype="Int64")
    road = pd.to_numeric(out.get("RoadType", ROAD_TYPE_ROADWAY), errors="coerce")
    out[SHS_ROAD_KIND_COL] = pd.Series(road, index=out.index).map(ROAD_KINDS).fillna("other")
    rtype = pd.to_numeric(out.get("RouteTypeC", 1), errors="coerce")
    out[SHS_ROUTE_TYPE_COL] = pd.Series(rtype, index=out.index).map(ROUTE_TYPES).fillna(
        "mainline")
    tw = out["Travelway"] if "Travelway" in out.columns else pd.Series("A", index=out.index)
    out[SHS_CARRIAGEWAY_COL] = tw.fillna("A").astype(str).str.strip().str.upper()
    out.attrs["shs_layer"] = {
        "n_read": n_read, "n_parts": len(out), "as_of": None if as_of is None else str(as_of),
        "road_kinds": out[SHS_ROAD_KIND_COL].value_counts().to_dict(),
    }
    return out


def shs_frame(rows, crs=WGS84):
    """A synthetic SHS layer from dicts carrying ``RouteId`` and ``geometry`` (and any
    of ``RoadType`` / ``RouteTypeC`` / ``Travelway``; defaults are a primary mainline
    roadway) — for tests and small studies."""
    import geopandas as gpd

    frame = gpd.GeoDataFrame(list(rows), crs=crs)
    return _derive_shs_fields(frame)


RECORD_KIND_SOURCE_COL = "record_kind_source"   # "shs" / "description"


def _shs_frame_from(shs):
    if isinstance(shs, (str,)) or hasattr(shs, "__fspath__"):
        return _load_shs_cached(str(shs))
    return shs


_SHS_CACHE: dict = {}


def _load_shs_cached(path):
    """One read per path per process: several scripts hand the same zip to every
    district's AADT load."""
    if path not in _SHS_CACHE:
        _SHS_CACHE[path] = load_shs(path)
    return _SHS_CACHE[path]


def classify_records_with_shs(aadt, shs):
    """The AADT layer with ``record_kind`` taken from the SHS where it is unambiguous.

    The AADT layer's own classification (:func:`aadt.classify_aadt_records`) reads the
    record's descriptions, and those name the record's **end points**, not the record:
    I-184's mainline ``02410AIN184`` runs "``JCT I-84 FLYING WYE IC`` → ``I-84 EB ON
    RAMP``" and was a connector, then a ramp; the Broadway Ave ramps ``02080AUS020`` /
    ``02082AUS020`` carried no description in 2024 and a wrong one in 2025
    (``MCBRIDE RD`` → ``SH-6``), which made them mainline. The two layers share the
    ``RouteID`` scheme, so where **every** SHS piece of a record's ``RouteID`` is
    roadway (``RoadType`` 4) the record is ``mainline``, and where every piece is a ramp
    (5) it is a ``ramp``. A ``RouteID`` the SHS doesn't draw (local ``OH`` roads) or
    draws as mixed/other keeps its description reading. ``record_kind_source`` says
    which decided.

    **One exception: a record with the ramp signature stays a ramp.** A few ramp counts
    sit on a roadway route id. ITD writes a ramp record as a movement with no "to"
    point (``WB ON COTTERELL IC #222`` / ``NONE``); a mainline record names both ends,
    even when an end is a ramp (``I-84 EB ON RAMP`` → ``WB OFF FRANKLIN IC #1``).
    There are three such records in 2025, and each put its ramp count on a mainline
    carriageway (owner-checked, Session 67):
    - ``01010DIN084`` (6,000) and ``01010AIN084`` (6,100) at the I-84/I-86 junction at
      Cotterell, where I-84 carries 12,000 east of the junction;
    - ``01543DUS095`` (150) on Weiser's W 7th St, which carries 6,000–8,000.
    """
    from .aadt import RECORD_KIND_COL, MAINLINE, RAMP, ramp_signature

    shs = _shs_frame_from(shs)
    out = aadt.copy()
    if RECORD_KIND_COL not in out.columns or "RouteID" not in out.columns:
        return out
    kinds = shs.groupby("RouteID")[SHS_ROAD_KIND_COL].agg(lambda k: set(k))
    decided = {rid: (MAINLINE if k == {"roadway"} else RAMP if k == {"ramp"} else None)
               for rid, k in kinds.items()}
    by_shs = out["RouteID"].map(decided)
    by_shs = by_shs.where(~((by_shs == MAINLINE) & ramp_signature(out)))
    hit = by_shs.notna()
    out[RECORD_KIND_SOURCE_COL] = "description"
    out.loc[hit, RECORD_KIND_COL] = by_shs[hit]
    out.loc[hit, RECORD_KIND_SOURCE_COL] = "shs"
    return out


# ---------------------------------------------------------------------------
# Urban areas
# ---------------------------------------------------------------------------
URBAN_COLUMNS = ["UACE", "NAME", "Population", "HouseUnits", "PopulDensi"]

URBAN_UACE_COL = "urban_uace"
URBAN_NAME_COL = "urban_area"
URBAN_INSIDE_COL = "urban_inside"
URBAN_SHARE_COL = "urban_share"
URBAN_EDGE_COL = "urban_edge_m"

URBAN_CONTEXT_COLUMNS = (URBAN_UACE_COL, URBAN_NAME_COL, URBAN_INSIDE_COL,
                         URBAN_SHARE_COL, URBAN_EDGE_COL)


def load_urban_areas(source):
    """Load the Census 2020 urban-area polygons in WGS84: ``UACE`` (str), ``NAME``,
    ``Population``, ``HouseUnits``, ``PopulDensi`` (people per sq mi)."""
    from pyogrio import read_dataframe

    gdf = read_dataframe(_resolve_shp_path(source), columns=URBAN_COLUMNS)
    gdf["UACE"] = gdf["UACE"].astype(str).str.strip()
    gdf = gdf.to_crs(WGS84) if gdf.crs is not None else gdf.set_crs(WGS84)
    return gdf.reset_index(drop=True)


def urban_centroids(urban) -> pd.DataFrame:
    """Each urban area's polygon centroid (computed in a metric CRS, returned in
    WGS84) and population, indexed by ``UACE`` — what
    :func:`inrix_tools.profile_assignment.segment_context` reads for the bearing
    toward the area (Item 56)."""
    metric = urban.estimate_utm_crs()
    cen = urban.to_crs(metric).geometry.centroid.to_crs(WGS84)
    return pd.DataFrame({"UACE": urban["UACE"].astype(str).to_numpy(),
                         "urban_area": urban["NAME"].to_numpy(),
                         "centroid_lat": cen.y.to_numpy(),
                         "centroid_lon": cen.x.to_numpy(),
                         "population": pd.to_numeric(urban["Population"],
                                                     errors="coerce").to_numpy()}
                        ).set_index("UACE")


def urban_context(geo, urban) -> pd.DataFrame:
    """Each segment's urban context — a **context column, never a gate**.

    Args:
        geo: GeoDataFrame indexed by ``Segment ID`` (any CRS).
        urban: :func:`load_urban_areas`.

    Returns:
        A DataFrame indexed like ``geo``:

        * ``urban_uace`` / ``urban_area`` — the urban area the segment's midpoint lies
          in, or, outside every area, the **nearest** one (so a rural segment still
          says which town it is approaching);
        * ``urban_inside`` — whether the midpoint lies inside it;
        * ``urban_share`` — the share of the segment's length inside *any* urban area
          (a segment straddling the boundary is 0 < share < 1);
        * ``urban_edge_m`` — the midpoint's distance to that area's boundary, **signed**:
          positive inside, negative outside. ``0`` is the boundary itself.

        Segments with no geometry get nulls.
    """
    import geopandas as gpd  # noqa: F401  (geo is a GeoDataFrame)
    from shapely import STRtree

    out = pd.DataFrame(index=geo.index)
    out[URBAN_UACE_COL] = pd.Series([None] * len(geo), index=geo.index, dtype=object)
    out[URBAN_NAME_COL] = pd.Series([None] * len(geo), index=geo.index, dtype=object)
    out[URBAN_INSIDE_COL] = pd.array([pd.NA] * len(geo), dtype="boolean")
    out[URBAN_SHARE_COL] = float("nan")
    out[URBAN_EDGE_COL] = float("nan")
    valid = geo.geometry.notna() & ~geo.geometry.is_empty
    if not valid.any() or len(urban) == 0:
        return out

    metric = geo[valid].estimate_utm_crs()
    seg = geo.geometry[valid].to_crs(metric)
    polys = list(urban.to_crs(metric).geometry.values)
    edges = [p.boundary for p in polys]
    tree = STRtree(polys)
    union = urban.to_crs(metric).geometry.union_all()
    uace = urban["UACE"].to_numpy()
    names = urban["NAME"].to_numpy()

    for sid, line in seg.items():
        mid = line.interpolate(0.5, normalized=True)
        hits = [int(i) for i in tree.query(mid, predicate="within")]
        if hits:
            i, inside = hits[0], True
        else:
            i, inside = int(tree.nearest(mid)), False
        d = float(edges[i].distance(mid))
        share = (line.intersection(union).length / line.length) if line.length > 0 else (
            1.0 if inside else 0.0)
        out.at[sid, URBAN_UACE_COL] = uace[i]
        out.at[sid, URBAN_NAME_COL] = names[i]
        out.at[sid, URBAN_INSIDE_COL] = inside
        out.at[sid, URBAN_SHARE_COL] = round(float(share), 3)
        out.at[sid, URBAN_EDGE_COL] = round(d if inside else -d, 1)
    return out


# ---------------------------------------------------------------------------
# Mileposts (Item 51)
# ---------------------------------------------------------------------------
SHS_MP_START_COL = "shs_mp_start"
SHS_MP_END_COL = "shs_mp_end"
SHS_MP_MAX_OFFSET_M = 40.0
"""A segment end further than this from its own route id's line gets no milepost —
the same reach as membership's *near* test."""


def shs_mileposts(geo, shs, route_ids) -> pd.DataFrame:
    """Each segment's start and end milepost on the SHS line that decided it.

    The SHS is a linear reference: every line carries ``FromMeasur``/``ToMeasure`` and
    its vertices run from the one to the other. A segment's ends are projected onto the
    nearest part of its own ``RouteID`` (membership's ``itd_route_id``) and the measure
    is interpolated along it. Ordered by milepost, a route's pieces show where it runs
    concurrently with another route: SH-8 stops at mp 1.92 on 3rd St in Moscow and
    resumes at 2.35 on Troy Rd, and the 0.43 mi between is the US-95 couplet.

    Args:
        geo: GeoDataFrame indexed by segment id (any CRS).
        shs: :func:`load_shs`.
        route_ids: Series indexed like ``geo`` — the SHS ``RouteID`` per segment
            (``None`` where no SHS line decided).

    Returns:
        A DataFrame indexed like ``geo`` with :data:`SHS_MP_START_COL` /
        :data:`SHS_MP_END_COL` (miles, NaN where there is no line within
        :data:`SHS_MP_MAX_OFFSET_M`).
    """
    out = pd.DataFrame({SHS_MP_START_COL: float("nan"), SHS_MP_END_COL: float("nan")},
                       index=geo.index)
    rid = pd.Series(route_ids).reindex(geo.index)
    valid = geo.geometry.notna() & ~geo.geometry.is_empty & rid.notna()
    if not valid.any() or len(shs) == 0:
        return out
    metric = geo[valid].estimate_utm_crs()
    seg = geo.geometry[valid].to_crs(metric)
    lines = shs.to_crs(metric)
    by_route = {r: grp for r, grp in lines.groupby("RouteID")}
    for sid, line in seg.items():
        grp = by_route.get(str(rid.loc[sid]))
        if grp is None or line.length <= 0:
            continue
        for col, frac in ((SHS_MP_START_COL, 0.0), (SHS_MP_END_COL, 1.0)):
            pt = line.interpolate(frac, normalized=True)
            d = grp.geometry.distance(pt)
            k = d.idxmin()
            if float(d.loc[k]) > SHS_MP_MAX_OFFSET_M:
                continue
            part = grp.geometry.loc[k]
            f0, f1 = float(grp.at[k, "FromMeasur"]), float(grp.at[k, "ToMeasure"])
            t = part.project(pt, normalized=True) if part.length > 0 else 0.0
            out.at[sid, col] = round(f0 + (f1 - f0) * t, 3)
    return out


# ---------------------------------------------------------------------------
# Highway tiers (Item 61)
# ---------------------------------------------------------------------------
TIERS = ("District", "Regional", "State", "Expressway", "Interstate")
"""ITD's highway tiers, lowest first; :data:`TIER_RANK` orders them."""
TIER_RANK = {t: i for i, t in enumerate(TIERS)}

TIER_COL = "tier"
TIER_RANK_COL = "tier_rank"
TIER_SOURCE_COL = "tier_source"      # milepost / proximity_route / proximity / None
TIER_MP_TOL = 0.05
"""Milepost slack (miles) when a segment's midpoint is matched to a tier piece."""
TIER_MAX_OFFSET_M = 40.0
"""A segment further than this from every tier line gets no tier by proximity."""


def segcode(route_id) -> str | None:
    """The Highway Tier layer's ``segcode`` for an SHS ``RouteId``: its first five
    digits, zero-padded to six (``01540AUS095`` → ``001540``). ``None`` if it is not
    one."""
    rid = "" if route_id is None or (isinstance(route_id, float) and pd.isna(route_id)) \
        else str(route_id).strip()
    return rid[:5].zfill(6) if len(rid) >= 5 and rid[:5].isdigit() else None


def load_highway_tiers(source):
    """ITD's Highway Tier layer (``Highway Tier.geojson``, a GeoJSON export from the ITD
    GIS app) in WGS84: ``segcode`` (6-digit str), ``bmp`` / ``emp`` (mileposts, float),
    ``tier`` (one of :data:`TIERS`) and ``tier_rank``.

    Raises:
        ValueError: a tier outside :data:`TIERS` (the export is not fully trusted).
    """
    from pyogrio import read_dataframe

    gdf = read_dataframe(source)
    gdf = gdf.to_crs(WGS84) if gdf.crs is not None else gdf.set_crs(WGS84)
    return _derive_tier_fields(gdf)


def _derive_tier_fields(gdf):
    out = gdf[["segcode", "bmp", "emp", "tier", "geometry"]].copy()
    # Four pieces write the travelway into the code (``A01540`` / ``D01540`` on US-95
    # at mp 476, ``A02350`` / ``D02350``): the five digits are the segcode.
    code = out["segcode"].astype(str).str.strip().str.replace(r"^[A-Za-z](\d{5})$", r"\1",
                                                              regex=True)
    out["segcode"] = code.str.zfill(6)
    for col in ("bmp", "emp"):
        out[col] = pd.to_numeric(out[col], errors="coerce")
    out["tier"] = out["tier"].astype(str).str.strip()
    bad = sorted(set(out["tier"]) - set(TIERS))
    if bad:
        raise ValueError(f"unknown highway tier(s) {bad}; expected {list(TIERS)}")
    out["tier_rank"] = out["tier"].map(TIER_RANK).astype(int)
    return out.reset_index(drop=True)


def tier_frame(rows, crs=WGS84):
    """A synthetic tier layer from dicts (``segcode``, ``bmp``, ``emp``, ``tier``,
    ``geometry``) — for tests."""
    import geopandas as gpd

    return _derive_tier_fields(gpd.GeoDataFrame(list(rows), crs=crs))


def segment_tiers(geo, tiers, route_ids, mileposts=None) -> pd.DataFrame:
    """Each segment's highway tier, first of:

    1. **milepost** — the tier piece on the segment's own ``segcode`` (its SHS
       ``RouteID``, :func:`segcode`) whose ``bmp``–``emp`` holds the segment's midpoint
       milepost (:func:`shs_mileposts`), within :data:`TIER_MP_TOL`; where pieces
       overlap, the highest tier;
    2. **proximity_route** — the nearest piece on its own ``segcode`` within
       :data:`TIER_MAX_OFFSET_M` of the segment's midpoint (a milepost the layer's
       pieces don't cover, or no milepost);
    3. **proximity** — the nearest piece of any route within that distance.

    The layer covers only ITD's long mainline segcodes and has milepost gaps (SH-55
    mp 16.1–47.3), so some segments get none; they are reported, not guessed.

    Args:
        geo: GeoDataFrame indexed by segment id (any CRS).
        tiers: :func:`load_highway_tiers`.
        route_ids: Series indexed like ``geo`` — the SHS ``RouteID`` per segment.
        mileposts: :func:`shs_mileposts` for ``geo``, or ``None``.

    Returns:
        Indexed like ``geo``: :data:`TIER_COL`, :data:`TIER_RANK_COL` (float, NaN =
        none) and :data:`TIER_SOURCE_COL`.
    """
    import numpy as np
    from shapely import STRtree

    out = pd.DataFrame({TIER_COL: pd.Series([None] * len(geo), index=geo.index,
                                            dtype=object),
                        TIER_RANK_COL: np.nan,
                        TIER_SOURCE_COL: pd.Series([None] * len(geo), index=geo.index,
                                                   dtype=object)}, index=geo.index)
    valid = geo.geometry.notna() & ~geo.geometry.is_empty
    if not valid.any() or len(tiers) == 0:
        return out
    codes = pd.Series(route_ids).reindex(geo.index).map(segcode)
    mp = pd.Series(np.nan, index=geo.index)
    if mileposts is not None and len(mileposts):
        m = mileposts.reindex(geo.index)
        mp = m[[SHS_MP_START_COL, SHS_MP_END_COL]].mean(axis=1, skipna=True)

    by_code = {c: g for c, g in tiers.groupby("segcode")}
    metric = geo[valid].estimate_utm_crs()
    mids = geo.geometry[valid].to_crs(metric).interpolate(0.5, normalized=True)
    lines = tiers.to_crs(metric).geometry
    tree = STRtree(list(lines.values))
    for sid, mid in mids.items():
        code = codes.get(sid)
        grp = by_code.get(code) if code is not None else None
        hit, source = None, None
        if grp is not None and pd.notna(mp.get(sid)):
            m = float(mp[sid])
            lo = grp[["bmp", "emp"]].min(axis=1) - TIER_MP_TOL
            hi = grp[["bmp", "emp"]].max(axis=1) + TIER_MP_TOL
            on = grp[(lo <= m) & (m <= hi)]
            if len(on):
                hit, source = int(on["tier_rank"].max()), "milepost"
        if hit is None:
            near = tree.query(mid, predicate="dwithin", distance=TIER_MAX_OFFSET_M)
            if len(near):
                cand = tiers.iloc[near]
                dist = lines.iloc[near].distance(mid)
                same = cand["segcode"] == code
                pick = dist[same.to_numpy()] if same.any() else dist
                k = pick.idxmin()
                hit = int(tiers.at[k, "tier_rank"])
                source = "proximity_route" if same.any() else "proximity"
        if hit is not None:
            out.at[sid, TIER_COL] = TIERS[hit]
            out.at[sid, TIER_RANK_COL] = float(hit)
            out.at[sid, TIER_SOURCE_COL] = source
    return out

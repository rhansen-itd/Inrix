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

DEFAULT_YEAR = 2025        # the layer is cumulative across years; use the latest.
DEFAULT_SOURCE = "AADT_2025.zip"
"""The owner's 2025 download (Item 52): the same fields as ``Cumulative_AADT.zip``
(1999–2024) but only ``Year == 2025``, and carrying US-95's new alignment south of
Moscow. Scripts default ``--aadt`` to it; pass ``Cumulative_AADT.zip`` with an older
``--aadt-year`` to reproduce pre-2025 volumes."""
AADT_COL = "AADT"
AADT_SOURCE_COL = "aadt_source"      # matched / matched_ramp / nearest / missing
AADT_DIST_COL = "aadt_dist_m"        # match distance in metres
AADT_KIND_COL = "aadt_record_kind"   # record_kind of the chosen AADT record
AADT_DESC_COL = "aadt_desc"          # Descriptio of the chosen record (diagnostic)
AADT_COVER_COL = "aadt_coverage"     # share of the segment the chosen record runs beside
AADT_ROUTE_NUM_COL = "aadt_route_number"   # route the chosen record names (<NA> = none)

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

# A record whose ``RouteID`` band is ``OH`` can still be *on* a numbered route: ITD
# writes the route as a trailing parenthetical (``KARCHER RD (SH-55)``,
# ``N WASHINGTON AVE(SH-52)``) or as the whole description (``US-95``). 330 of the D3
# layer's ``OH`` rows name a route that way, Karcher and Eagle Rd among them — which
# is why route class alone reads a state highway as an unnumbered street.
# **Only those two forms are read.** A route named anywhere else in a description is a
# cross-street or a junction, not the record's own route: ``FRANKLIN RD US-20 IC#29``
# is I-84's *mainline* record at the US-20 interchange, and ``IDAHO AVE @ US-95 CONN``
# is a connector. The closing parenthesis must follow the number, which also drops the
# business routes — ``CALDWELL BLVD(I-84 BUS)`` and ``CLEVELAND BLVD (I-84 B)`` are
# I-84 *Business*, not I-84, and must never match an interstate segment.
_DESC_ROUTE_RE = re.compile(
    r"\((?:I|US|SH|ID|HWY)[\s-]?(\d{1,3})\)\s*$|^\s*(?:I|US|SH|ID)[\s-]?(\d{1,3})\s*$", re.I)


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


def record_route_number(route_id, description=None) -> int | None:
    """The route a record names **itself** — from ``RouteID``, else from its
    ``Descriptio`` (Item 42).

    The ``RouteID`` band is the first answer and the reliable one. It is not the only
    one: ITD carries plenty of state highway on ``OH`` ("other highway") bands and says
    so only in the description — ``KARCHER RD (SH-55)`` is SH-55, ``US-95`` is US-95.
    Reading route class off ``RouteID`` alone therefore calls a state highway an
    unnumbered street, which is what made an on-system classification depend on which
    records happened to be loaded.

    Only a **trailing parenthetical** or a description that is *nothing but* a route
    designation counts (see ``_DESC_ROUTE_RE``): a route named mid-description is a
    cross-street or an interchange, and the parenthesis-hugging form excludes
    ``(I-84 BUS)``, which is a business route and not the interstate.

    ``None`` when the record names no route.
    """
    _, _, num = parse_route_id(route_id)
    if num is not None:
        return num
    if description is None or (isinstance(description, float) and math.isnan(description)):
        return None
    m = _DESC_ROUTE_RE.search(str(description).strip())
    if m is None:
        return None
    return int(m.group(1) or m.group(2))


def _record_route_numbers(aadt) -> list:
    """:func:`record_route_number` per row of an AADT frame, positionally."""
    route_id = aadt["RouteID"] if "RouteID" in aadt.columns else None
    desc = aadt["Descriptio"] if "Descriptio" in aadt.columns else None
    n = len(aadt)
    if route_id is None and desc is None:
        return [None] * n
    ids = route_id.to_numpy() if route_id is not None else [None] * n
    descs = desc.to_numpy() if desc is not None else [None] * n
    return [record_route_number(ids[i], descs[i]) for i in range(n)]


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


def ramp_signature(aadt) -> pd.Series:
    """True for a record written the way ITD writes a **ramp count**: a movement in
    ``Descriptio`` (``WB ON COTTERELL IC #222``) and no "to" point in ``Descript_1``
    (``NONE`` or blank). A mainline record names both ends, even when one of them is
    a ramp (``EB ON RAMP IC #49`` → ``EB ON COLE-OVERLAND IC``). (Item 52.)"""
    idx = aadt.index
    if "Descriptio" not in aadt.columns or "Descript_1" not in aadt.columns:
        return pd.Series(False, index=idx)       # no "to" field: no evidence either way
    frm = aadt["Descriptio"].fillna("").astype(str).str.upper()
    to = aadt["Descript_1"].fillna("").astype(str).str.strip().str.upper()
    return frm.str.contains(_DESC_RAMP_RE) & to.isin(["", "NONE"])


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
        # ...except a record with the ramp signature (a movement, no "to" point),
        # which is a ramp count filed on the mainline's route id: I-84's
        # ``EB OFF COTTERELL IC#222`` (6,100) on ``01010AIN084``, where I-84 carries
        # 12,000 (Item 52, owner-checked). The roll-up's own examples all name a
        # "to" point, so they are unaffected.
        kind = kind.where(~on_mainline_route | ramp_signature(out), MAINLINE)

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


# ---------------------------------------------------------------------------
# Layer cache keying (ROADMAP Item 47)
# ---------------------------------------------------------------------------
# The cache holds a *subset* of the layer — one year, one bounding box, one set of
# columns — so a hit is only a hit if that subset covers what is being asked for.
# Returning it unconditionally means whichever caller writes the file first decides
# the spatial extent every later caller gets, and the later caller has no way to
# tell. That is the defect this section exists to close: what the cache covers is
# recorded beside it, and a request it cannot serve rebuilds rather than under-
# answering.

CACHE_META_SUFFIX = ".meta.json"
"""Sidecar written beside a ``cache_path`` recording what the cache covers."""


def _clean_bbox(bbox):
    """``(minx, miny, maxx, maxy)`` as floats, or ``None`` for absent/degenerate.

    A caller that derives its bbox from ``geo.total_bounds`` hands over
    ``(nan, nan, nan, nan)`` when the frame is empty
    (``reconcile_export_segments`` does exactly this when nothing is absent). That
    is "no restriction", not a box — before Item 47 the cache short-circuit meant
    nothing ever looked at it.
    """
    if bbox is None:
        return None
    try:
        out = tuple(float(v) for v in bbox)
    except (TypeError, ValueError):
        return None
    if len(out) != 4 or not all(math.isfinite(v) for v in out):
        return None
    return out


def _is_geoparquet(source) -> bool:
    """True for a ``.parquet`` / ``.geoparquet`` path — a layer geopandas reads directly."""
    if isinstance(source, (list, tuple)):
        return False
    try:
        return Path(source).suffix.lower() in (".parquet", ".geoparquet")
    except TypeError:
        return False


def cache_meta_path(cache_path) -> Path:
    """Path of the sidecar that records a layer cache's coverage."""
    return Path(str(cache_path) + CACHE_META_SUFFIX)


def read_cache_meta(cache_path) -> dict | None:
    """The recorded coverage of a layer cache, or ``None`` when it has none.

    ``None`` means *unknown*, not *unrestricted* — a cache written before Item 47
    has no sidecar, and :func:`_cache_shortfall` falls back to what the data
    itself can prove.
    """
    import json

    path = cache_meta_path(cache_path)
    if not path.exists():
        return None
    try:
        meta = json.loads(path.read_text())
    except (ValueError, OSError):
        return None
    return meta if isinstance(meta, dict) else None


def source_key(source) -> dict | None:
    """What a layer cache was built *from*: the source file's name and size.

    Item 52 made this part of the cache's coverage. ``Cumulative_AADT.zip`` and
    ``AADT_2025.zip`` are different downloads of the same layer, and a cache keyed only
    on ``year``/``bbox`` would serve one for the other. Name + size (not mtime, which
    a copy changes) is enough to tell two downloads apart. ``None`` for a source that
    is not a single path (a caller's in-memory frame, a list)."""
    if source is None or isinstance(source, (list, tuple)):
        return None
    try:
        p = Path(source)
    except TypeError:
        return None
    size = p.stat().st_size if p.is_file() else None
    return {"name": p.name, "bytes": size}


def _write_cache_meta(cache_path, *, year, bbox, columns, n_rows, source=None) -> None:
    import json

    cache_meta_path(cache_path).write_text(json.dumps({
        "source": source,
        "year": year,
        "bbox": list(bbox) if bbox is not None else None,
        "columns": list(columns),
        "n_rows": int(n_rows),
        "note": ("What this cache covers. A request outside it rebuilds the cache "
                 "rather than being served a subset (ROADMAP Item 47)."),
    }, indent=2) + "\n")


def _contains(outer, inner) -> bool:
    """True when bbox ``outer`` contains bbox ``inner`` (both ``(minx, miny, maxx, maxy)``)."""
    return (outer[0] <= inner[0] and outer[1] <= inner[1]
            and outer[2] >= inner[2] and outer[3] >= inner[3])


def _bbox_union(a, b):
    """The smallest bbox containing both; ``None`` (unrestricted) absorbs anything."""
    if a is None or b is None:
        return None
    return (min(a[0], b[0]), min(a[1], b[1]), max(a[2], b[2]), max(a[3], b[3]))


def _cache_shortfall(cached, meta, *, year, bbox, columns, source=None) -> str | None:
    """``None`` when the cache covers the request, else why it does not.

    With a sidecar this is an exact test against the *requested* extent. Without
    one the cache's own ``total_bounds`` stands in, which is conservative in the
    safe direction: the features in a bbox-filtered read never reach past the
    bbox, so a bbox the data covers was certainly requested, and one it does not
    may only mean the layer has nothing out there — that rebuilds unnecessarily,
    never under-answers.
    """
    missing = [c for c in columns if c not in cached.columns]
    if missing:
        return f"cache is missing column(s) {missing}"

    # Which download the cache came from (Item 52). A sidecar records it; one written
    # before Item 52 has no ``source`` and was built from whatever was the default
    # then, so it cannot prove it matches. With no sidecar at all, the ``Year`` check
    # below is what separates the downloads (each holds different years), as Item 47
    # judged such a cache on its own data.
    if source is not None and meta is not None:
        held = meta.get("source")
        if held is None:
            return "cache records no source, so it may be another download"
        if held != source:
            return f"cache was built from {held['name']}, request is {source['name']}"

    if meta is not None and "year" in meta:
        if meta["year"] != year:
            return f"cache holds year {meta['year']!r}, request is {year!r}"
    elif year is None:
        return "cache has no recorded year and an all-years read cannot be proved"
    elif "Year" in cached.columns:
        held = {int(y) for y in pd.to_numeric(cached["Year"], errors="coerce").dropna()}
        if held != {int(year)}:
            return f"cache holds year(s) {sorted(held)}, request is {year}"

    if meta is not None and "bbox" in meta:
        covered = meta["bbox"]
        if covered is None:                      # built unrestricted: covers anything
            return None
        if bbox is None:
            return "cache is bbox-restricted, request is unrestricted"
        return None if _contains(covered, tuple(bbox)) else (
            f"cache covers {[round(v, 3) for v in covered]}, request needs "
            f"{[round(v, 3) for v in bbox]}")

    if bbox is None:
        return "cache has no recorded extent and an unrestricted read cannot be proved"
    if len(cached) == 0:
        return "cache is empty, so it can prove no coverage"
    return None if _contains(tuple(cached.total_bounds), tuple(bbox)) else (
        "cache data reaches only "
        f"{[round(v, 3) for v in cached.total_bounds]}, request needs "
        f"{[round(v, 3) for v in bbox]}")


def load_aadt(source, year=DEFAULT_YEAR, bbox=None, columns=None, cache_path=None,
              classify=True, shs=None):
    """Load (a subset of) the ITD cumulative AADT layer as a GeoDataFrame in WGS84.

    ``shs`` (Item 52): ITD's State Highway System — a :func:`itd_layers.load_shs`
    frame or a path to it. When given, a record whose ``RouteID`` the SHS draws only
    as roadway is ``mainline`` and one it draws only as ramp is ``ramp``, whatever its
    description says (:func:`itd_layers.classify_records_with_shs`). The descriptions
    name a record's *end points*, so I-184's mainline (``JCT I-84 FLYING WYE IC`` →
    ``I-84 EB ON RAMP``, 68,000) read as a connector and the Broadway ramps
    (``02080AUS020``, described ``MCBRIDE RD`` in 2025) read as mainline, and the
    join gave both carriageways of I-184 a connector's 5,000 and Broadway Ave a ramp's
    9,500. Applied after the cache, so the cache itself is SHS-independent.
    """
    layer = _load_aadt_layer(source, year=year, bbox=bbox, columns=columns,
                             cache_path=cache_path, classify=classify)
    if shs is not None and classify:
        from . import itd_layers
        attrs = dict(layer.attrs)
        layer = itd_layers.classify_records_with_shs(layer, shs)
        layer.attrs.update(attrs)
    return layer


def _load_aadt_layer(source, year=DEFAULT_YEAR, bbox=None, columns=None, cache_path=None,
                     classify=True):
    """:func:`load_aadt` without the SHS reclassification.

    Args:
        source: the AADT ``.zip`` (e.g. ``Cumulative_AADT.zip``), a directory
            containing it, a ``.shp`` path, or a ``.parquet``/``.geoparquet``
            holding an already-read layer (filtered in memory).
        year: keep only rows for this ``Year`` (default ``2025`` — the layer is
            cumulative across years, so an unfiltered read double-counts every
            road; see DATA_FORMAT.md). ``None`` keeps all years.
        bbox: ``(minx, miny, maxx, maxy)`` in **WGS84** to restrict the read
            spatially (pushed down to the reader, reprojected to the layer CRS so
            the 251k statewide features aren't all held). Typically the study
            export's geometry bounds.
        columns: attribute columns to keep (default :data:`_KEEP_COLS`).
        cache_path: optional GeoParquet cache. It is used only when it **covers the
            request** — same ``year``, all the requested ``columns``, and a
            ``bbox`` inside the one it was built for, and the same source file
            (:func:`source_key`, Item 52), as recorded in the
            ``.meta.json`` sidecar beside it (:func:`read_cache_meta`). A request
            it cannot serve rebuilds it for the **union** of the two extents, so
            the cache widens rather than thrashing between callers. Before Item 47
            a hit was returned *ignoring* ``bbox``, which let whichever caller
            wrote the file first decide the extent every later caller got.
        classify: also label each record ``mainline`` / ``ramp`` / ``connector``
            (:func:`classify_aadt_records`, Item 34) — what :func:`join_aadt` ranks
            on. Default ``True``; pass ``False`` for the raw layer.

    Returns:
        GeoDataFrame in EPSG:4326 with the kept AADT attributes and reprojected
        ``LineString`` geometry (the source is in EPSG:8826), plus ``route_class`` /
        ``route_number`` parsed from ``RouteID`` and a ``Route`` populated from it
        (the shipped ``Route`` column is **null on every Idaho row**), and
        ``record_kind`` unless ``classify=False``. ``attrs['aadt_layer']`` records
        the resolved ``year``/``bbox`` and whether the cache was used, rebuilt, or
        absent — so a caller can see what it was actually handed.
    """
    import geopandas as gpd

    cols = list(columns) if columns is not None else _KEEP_COLS
    read_bbox_wgs84 = _clean_bbox(bbox)
    cache_state = "no_cache"
    src_key = None if _is_geoparquet(source) else source_key(source)

    if cache_path is not None and Path(cache_path).exists():
        cached = gpd.read_parquet(cache_path)
        meta = read_cache_meta(cache_path)
        shortfall = _cache_shortfall(cached, meta, year=year, bbox=read_bbox_wgs84,
                                     columns=cols, source=src_key)
        if shortfall is None:
            # Classify on the way out, always: the classification is cheap and its
            # rules change (Item 34's roll-up, Item 52's ramp signature), and a kind
            # frozen into an old cache would silently keep the old reading.
            out = classify_aadt_records(cached) if classify else cached
            out.attrs["aadt_layer"] = {
                "year": year, "bbox": read_bbox_wgs84, "cache": "hit",
                "cache_path": str(cache_path),
            }
            return out
        # Widen rather than narrow: a rebuild for the union of the two extents ends
        # the thrash between a corridor-bounds caller and a full-network one that
        # made Session 60 reorder generate_screening_maps.py by hand.
        if meta is not None and "bbox" in meta:
            previous = tuple(meta["bbox"]) if meta["bbox"] is not None else None
            if meta.get("year") == year and meta.get("source") == src_key:
                read_bbox_wgs84 = _bbox_union(previous, read_bbox_wgs84)
        cache_state = f"rebuilt ({shortfall})"

    if _is_geoparquet(source):
        # A GeoParquet source is the layer itself, already read once — a saved
        # subset, or a cache being re-read after it stopped covering the request.
        # Filtering it in memory keeps that case working; routing it through
        # pyogrio does not (GDAL has no business probing drivers for a file
        # geopandas can open), and before Item 47 it was never exercised because
        # the cache short-circuited before the source was touched.
        gdf = gpd.read_parquet(source)
        if year is not None and "Year" in gdf.columns:
            gdf = gdf[pd.to_numeric(gdf["Year"], errors="coerce") == int(year)]
        if gdf.crs is None:
            gdf = gdf.set_crs(WGS84)
        elif gdf.crs.to_epsg() != 4326:
            gdf = gdf.to_crs(WGS84)
        if read_bbox_wgs84 is not None:
            minx, miny, maxx, maxy = read_bbox_wgs84
            gdf = gdf.cx[minx:maxx, miny:maxy]
        keep = [c for c in cols if c in gdf.columns]
        gdf = gdf[keep + ["geometry"]] if keep else gdf
        gdf = gdf.reset_index(drop=True)
    else:
        from pyogrio import read_dataframe, read_info

        shp = _resolve_shp_path(source)

        where = None if year is None else f"Year = {int(year)}"

        read_bbox = None
        if read_bbox_wgs84 is not None:
            src_crs = read_info(shp)["crs"] or WGS84
            read_bbox = _bbox_to_crs(read_bbox_wgs84, WGS84, src_crs)

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
        _write_cache_meta(cache_path, year=year, bbox=read_bbox_wgs84,
                          columns=cols, n_rows=len(gdf), source=src_key)
        if cache_state == "no_cache":
            cache_state = "written"
    gdf.attrs["aadt_layer"] = {
        "year": year, "bbox": read_bbox_wgs84, "cache": cache_state,
        "cache_path": str(cache_path) if cache_path is not None else None,
    }
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
              prefer_mainline=True, directional_basis=True):
    """Attach an ``AADT`` value to each segment by spatial match to the AADT layer.

    The AADT layer has no segment id, so the join is spatial: candidates are the AADT
    lines within ``max_distance_m`` of the segment (Item 8 geometry) whose **local**
    bearing agrees with it, which rejects a perpendicular cross-street while letting a
    curved route-measure feature lying on the segment through (see ``_local_bearing``;
    bearings are compared at the two lines' nearest-point tangents).

    Among those candidates the winner is chosen by a **ranked preference**, not by
    distance alone (ROADMAP Item 34):

    1. **Route.** A record whose ``RouteID`` route number matches one the segment
       names (``RoadNumber`` / ``RoadList``) beats one that doesn't — the **band
       only**, because it is the reliable reading; the route a description names for
       itself (:func:`record_route_number`) settles step 5 and is reported, but
       letting it decide the match moved 14 D3 segments and every one for the worse.
       This is a *bonus, never a penalty*: a record that names no route and one that
       names the wrong one rank together, because a concurrency the XD side doesn't
       list (US-95 carrying SH-55 traffic at New Meadows) would otherwise push the
       real route below an unnumbered side street.
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
    5. **Route class**, as a **tie-break only** (Item 42): with the route, the
       facility, the distance and the coverage all equal, a record that names a route
       beats one that names none. This is the last *decision* in the key, and it is
       deliberately last. Ranking a numbered record ahead of distance or coverage
       instead was measured on D3 and is wrong: it hands Ustick Rd the 74,500 of
       ``FRANKLIN RD US-20 IC#29`` and W Emerald St the 82,000 of ``COLE RD IC #1B``
       — interstate records that pass within metres of a city street at an
       interchange. What the tie-break removes is the *real* defect, which was that
       the final comparison was the record's **position in the layer**: two records on
       the same ground, equally close and equally alongside, were separated by load
       order, so the answer changed with the candidate set. Below it the key falls
       back to the record's own identity (``RouteID``/``Descriptio``), which is stable
       whatever else is loaded.

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
        directional_basis: put ``AADT`` on the per-direction basis
            (:func:`apply_directional_basis`, Items 53/54) from the layer's own
            evidence. A two-way count is halved here and a couplet leg's one-way count
            kept; the couplet-membership fallback needs the catalogue, so a caller
            that has one calls :func:`apply_directional_basis` again with it.
            ``False`` leaves the layer's counts as published (and adds no basis
            columns).

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
        * ``aadt_coverage`` — the share of the segment the chosen line runs beside
          (:func:`_alongside_fraction`); the evidence that a match is *the road* and
          not a line that crosses or clips it,
        * ``aadt_route_number`` — the route the chosen record names (``<NA>`` for an
          unnumbered one), which is what :func:`classify_on_system` reads,
        * ``aadt_record_kind`` / ``aadt_desc`` — the chosen record's kind and
          ``Descriptio``, so a questionable match names itself,
        * ``RouteID`` / ``Route`` — carried from the chosen line (also for
          ``nearest``, as a diagnostic) / ``Commercial`` — carried only for a match.

        With ``directional_basis`` (the default), also ``aadt_layer`` (the published
        count), ``aadt_basis`` / ``aadt_basis_reason`` and ``aadt_record_basis`` (the
        chosen record's :func:`classify_aadt_basis` evidence); ``AADT`` is then the
        segment's own direction's volume.

        ``attrs['aadt_join']`` records the resolved policy and the source counts.
    """
    from shapely import STRtree

    out = geo.copy()
    blank = {AADT_COL: float("nan"), AADT_SOURCE_COL: "missing",
             AADT_DIST_COL: float("nan"), AADT_COVER_COL: float("nan"),
             AADT_KIND_COL: None, AADT_DESC_COL: None, AADT_ROUTE_NUM_COL: None,
             "RouteID": None, "Route": None, "Commercial": pd.NA,
             AADT_RECORD_BASIS_COL: None}

    valid_geo = out[out.geometry.notna() & ~out.geometry.is_empty]
    if len(valid_geo) == 0 or len(aadt) == 0:
        for col, val in blank.items():
            out[col] = val
        out.attrs["aadt_join"] = _join_policy(
            max_distance_m, bearing_tol_deg, prefer_mainline, out[AADT_SOURCE_COL])
        return apply_directional_basis(out) if directional_basis else \
            out.drop(columns=[AADT_RECORD_BASIS_COL])

    if RECORD_KIND_COL not in aadt.columns:
        aadt = classify_aadt_records(aadt)
    if directional_basis and BASIS_EVIDENCE_COL not in aadt.columns:
        aadt = classify_aadt_basis(aadt)

    # Distances/buffers need a metric CRS; estimate a UTM zone from the segments.
    from shapely.ops import nearest_points

    metric_crs = valid_geo.estimate_utm_crs()
    seg_m = valid_geo.geometry.to_crs(metric_crs)
    aadt_m = aadt.to_crs(metric_crs)
    aadt_geoms = list(aadt_m.geometry.values)
    tree = STRtree(aadt_geoms)

    aadt_vals = aadt[AADT_COL].astype(float).to_numpy()
    rec_kind = aadt[RECORD_KIND_COL].to_numpy()
    # Two readings of a record's route, and they do different jobs (Item 42).
    # ``rec_band_num`` is the ``RouteID`` band alone and is what the **match** ranks
    # on: it is the reliable one. ``rec_num`` adds the route a description names for
    # itself, which is how a state highway carried on an ``OH`` band identifies itself
    # (``KARCHER RD (SH-55)``) — it settles the class tie-break and is reported as
    # ``aadt_route_number``, but it must not promote a record *above* a nearer one.
    # Measured on the D3 export, letting it do so moves 14 segments and every one of
    # them for the worse: two Chinden Blvd segments leave US-20's 29,000 mainline
    # record for a 1,100 record that runs beside 9% of the segment, and two SH-52
    # segments take a 290-vehicle record named ``SH-52`` over the route's own 3,700.
    rec_band_num = (aadt[ROUTE_NUMBER_COL].to_numpy() if ROUTE_NUMBER_COL in aadt.columns
                    else [None] * len(aadt))
    rec_num = _record_route_numbers(aadt)

    def _col_or_none(frame, col):
        return frame[col].to_numpy() if col in frame.columns else None

    aadt_route = _col_or_none(aadt, "Route")
    aadt_route_id = _col_or_none(aadt, "RouteID")
    aadt_desc = _col_or_none(aadt, "Descriptio")
    aadt_comm = _col_or_none(aadt, "Commercial")
    aadt_ev = _col_or_none(aadt, BASIS_EVIDENCE_COL)

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
        # (route_rank, kind_rank, dist@0.1m, -coverage, class_rank, dist, tie, idx,
        # coverage) — lexicographic, low wins. ``class_rank`` is the last *decision*;
        # ``dist``, ``tie`` (the record's own identity) and the index only make the
        # order total, and the index is reached only by two indistinguishable records.
        # The trailing coverage is payload, never compared: nothing gets past the
        # index to reach it.
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
            band = rec_band_num[i]
            band = None if band is None or pd.isna(band) else int(band)
            route_rank = 0 if (band is not None and band in seg_nums) else 1
            num = rec_num[i]
            kind_rank = _KIND_RANK.get(rec_kind[i], 1) if apply_kind else 0
            cover = _alongside_fraction(seg_geom, aadt_geoms[i], max_distance_m)
            key = (route_rank, kind_rank, round(d, 1), -cover,
                   0 if num is not None else 1, d,
                   f"{_take(aadt_route_id, i) or ''}|{_take(aadt_desc, i) or ''}", i,
                   cover)
            if best is None or key < best:
                best = key
        if best is not None:
            d, i, cover = best[5], best[7], best[8]
            kind = rec_kind[i]
            records[sid] = {
                AADT_COL: float(aadt_vals[i]),
                AADT_SOURCE_COL: "matched_ramp" if kind in (RAMP, CONNECTOR) else "matched",
                AADT_DIST_COL: d,
                AADT_COVER_COL: cover,
                AADT_KIND_COL: kind,
                AADT_DESC_COL: _take(aadt_desc, i),
                AADT_ROUTE_NUM_COL: rec_num[i],
                "RouteID": _take(aadt_route_id, i),
                "Route": _take(aadt_route, i),
                "Commercial": _take(aadt_comm, i, pd.NA),
                AADT_RECORD_BASIS_COL: _take(aadt_ev, i),
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
                AADT_COVER_COL: _alongside_fraction(seg_geom, aadt_geoms[i],
                                                    max_distance_m),
                AADT_KIND_COL: rec_kind[i],
                AADT_DESC_COL: _take(aadt_desc, i),
                AADT_ROUTE_NUM_COL: rec_num[i],
                "RouteID": _take(aadt_route_id, i),
                "Route": _take(aadt_route, i),
                "Commercial": pd.NA,
                AADT_RECORD_BASIS_COL: None,
            }

    for col in blank:
        out[col] = [records.get(sid, blank)[col] for sid in out.index]
    out[AADT_ROUTE_NUM_COL] = pd.array(out[AADT_ROUTE_NUM_COL], dtype="Int64")
    out.attrs = dict(geo.attrs)
    out.attrs["aadt_join"] = _join_policy(
        max_distance_m, bearing_tol_deg, prefer_mainline, out[AADT_SOURCE_COL])
    if not directional_basis:
        return out.drop(columns=[AADT_RECORD_BASIS_COL])
    return apply_directional_basis(out)


def _join_policy(max_distance_m, bearing_tol_deg, prefer_mainline, source) -> dict:
    """The resolved join policy + per-source counts, for ``attrs['aadt_join']`` —
    the module's convention of recording *what was actually applied* (Item 34)."""
    counts = source.value_counts().to_dict()
    return {
        "max_distance_m": float(max_distance_m),
        "bearing_tol_deg": float(bearing_tol_deg),
        "prefer_mainline": bool(prefer_mainline),
        "preference": ("route number, then mainline over connector over ramp, then "
                       "distance, then coverage, then a named route over an unnumbered "
                       "one") if prefer_mainline else
                      ("route number, then distance, then coverage, then a named route "
                       "over an unnumbered one"),
        "counts": {k: int(v) for k, v in counts.items()},
    }


# ---------------------------------------------------------------------------
# One volume basis: every count per direction  (Items 53, 54)
# ---------------------------------------------------------------------------
# Every XD segment is one direction of travel, and VHD is ``delay/60 × AADT`` per
# segment, so what the AADT *means* has to be the same on every segment. On the ITD
# layer it is not. A two-way road and a divided highway carry one centreline and a
# **two-way** count, which both directions take. A one-way couplet carries each leg as
# its own record, and that count is **one-way**: ``01360AIN015`` (Pocatello's I-15 BL)
# is 15,000 at the south end, then 7,500 on the ``A`` leg and 7,700 on the ``D`` leg.
# Left alone, a couplet leg scores about half the VHD the same delay scores anywhere
# else. Item 53 fixed that by doubling the one-way counts (the two-way-equivalent
# basis). Item 54 turned it round: the basis is **per direction**, the volume an XD
# segment actually carries. A two-way count is halved (a 50/50 directional split), and
# a one-way count or a ramp movement is kept as published. Every VHD is half its
# Item 53 value, so rankings are unchanged, and a corridor's NB + SB VHD is the
# facility's VHD instead of twice it.
AADT_LAYER_COL = "aadt_layer"            # the count as the layer publishes it
AADT_BASIS_COL = "aadt_basis"            # two_way_half / one_way / ramp (<NA>: no AADT)
AADT_BASIS_REASON_COL = "aadt_basis_reason"
AADT_RECORD_BASIS_COL = "aadt_record_basis"   # the chosen record's basis evidence
BASIS_EVIDENCE_COL = "basis_evidence"    # on the AADT layer (classify_aadt_basis)

TWO_WAY_HALF, ONE_WAY, RAMP_MOVEMENT = "two_way_half", "one_way", "ramp"
TWO_WAY_SPLIT = 0.5
"""Share of a two-way count each direction carries: an even directional split."""
# The Item 53 (two-way-equivalent) labels, which a geometry cache written between
# Items 53 and 54 still carries (:func:`to_directional_basis`).
_LEGACY_TWO_WAY, _LEGACY_ONE_WAY_X2 = "two_way", "one_way_x2"
# Record evidence (classify_aadt_basis). The one-way kinds are what keep a count
# whole; the two-way kinds are what stop the couplet-leg fallback from doing it.
EV_ONE_WAY_WORDS, EV_ONE_WAY_PAIR = "one_way_words", "one_way_pair"
EV_TWO_WAY_WORDS, EV_DUPLICATED = "two_way_words", "duplicated"
_EV_ONE_WAY = {EV_ONE_WAY_WORDS, EV_ONE_WAY_PAIR}
_EV_TWO_WAY = {EV_TWO_WAY_WORDS, EV_DUPLICATED}

# ITD marks where a one-way section starts and ends, in a record's "from" point
# (``Descriptio``) or its "to" point (``Descript_1``). ``BEG 1-WAY`` / ``END 2-WAY``
# open a one-way section; ``END 1-WAY`` / ``BEG 2-WAY`` close it. A record *starting*
# where one opens, or *ending* where one closes, lies inside it:
# ``BEG 1-WAY/RESTLAWN DR -> E HUMBOLT ST`` and ``N BROADWAY ST -> JUNIPER ST (END
# 1-WAY)`` are one-way, ``END 1-WAY @ ELM ST -> CEDAR ST`` (21,500) is two-way.
# ``COUPLET`` / ``CPLT`` are not read: they name a junction with a couplet
# (``JCT SB COUPLET(002052)``), and Nampa's ``D`` leg starts at ``CALDWELL BLVD(END
# CPLT)``, the same point where its ``A`` leg reads ``CANYON ST (BEG 1-WAY)``.
_OPENS_ONE_WAY_RE = re.compile(r"\bBEG(?:IN)?\s+1[\s-]?WAY\b|\bEND\s+2[\s-]?WAY\b")
_CLOSES_ONE_WAY_RE = re.compile(
    r"\bEND\s+(?:OF\s+)?1[\s-]?WAY\b|\bBEG(?:IN)?\s+2[\s-]?WAY\b")
_ROUNDABOUT_RE = re.compile(r"\bROUNDABOUT\b|\bRNDABOUT\b")

PAIR_MAX_SEP_M = 350.0
"""An ``A`` and a ``D`` record of one route are a pair (each leg carrying its own
direction) when one runs within this distance of the other over at least half its
length. The couplets sit a block or two apart (Blackfoot 78 m, Moscow 179 m, Twin
Falls 232 m). The layer's route measures cannot be used instead: Moscow's ``D`` leg
and Twin Falls' are measured differently from their ``A`` legs, so the measure
ranges overlap records a kilometre away."""

TWO_WAY_TWIN_TOL_M = 15.0
"""An XD segment is one carriageway of a two-way road when an opposing segment of the
same street lies within this distance along its whole length (the same test as
:data:`inrix_tools.couplets.TWO_WAY_TOL_M`)."""


def _words_evidence(frm, to) -> str | None:
    frm = frm.upper() if isinstance(frm, str) else ""
    to = to.upper() if isinstance(to, str) else ""
    one = bool(_OPENS_ONE_WAY_RE.search(frm) or _CLOSES_ONE_WAY_RE.search(to))
    two = bool(_CLOSES_ONE_WAY_RE.search(frm) or _OPENS_ONE_WAY_RE.search(to))
    if one and not two:
        return EV_ONE_WAY_WORDS
    if two and not one:
        return EV_TWO_WAY_WORDS
    return None                          # no marker, or both ends say "one-way ends"


def _beside_fraction(line, other, other_ends, max_sep: float, n: int = 11,
                     end_tol: float = 15.0) -> float:
    """Share of ``line`` that runs *beside* ``other``: within ``max_sep`` of it, and
    nearest to a point along it rather than one of its ends. The second half is what
    tells a couplet leg from the two-way road it merges into: a point on the road past
    the couplet is also within a block of the other leg, but of its **end**."""
    from shapely.ops import nearest_points

    if n < 2 or line.length == 0:
        return 0.0
    step = line.length / (n - 1)
    hits = 0
    for k in range(n):
        p = line.interpolate(k * step)
        if other.distance(p) > max_sep:
            continue
        q = nearest_points(other, p)[0]
        if all(q.distance(e) > end_tol for e in other_ends):
            hits += 1
    return hits / n


def classify_aadt_basis(aadt, *, pair_max_sep_m: float = PAIR_MAX_SEP_M):
    """Label each AADT record with what the layer says about its count's basis.

    ``basis_evidence`` is one of:

    * ``one_way_words`` — the record lies inside a section ITD marks one-way (see
      ``_OPENS_ONE_WAY_RE``);
    * ``two_way_words`` — it starts where a one-way section ends, or ends where one
      starts;
    * ``duplicated`` — an ``A`` and a ``D`` record on the same measures carry the
      **same** count: the layer drew a divided highway's two carriageways and gave
      both the two-way count (Sandpoint's 5th Ave, 13,000 each, against 11,500 and
      16,000 either side);
    * ``one_way_pair`` — an ``A`` and a ``D`` record of one route run beside each other
      (:data:`PAIR_MAX_SEP_M`): each carries its own direction's count. *Beside*
      means at least half the record lies within that distance of the other leg's
      line and nearest to a point **along** it, not to one of its ends
      (:func:`_beside_fraction`). The two-way road just past a couplet is within a
      block of both legs, but only of where they end;
    * ``<NA>`` — no evidence either way (most of the layer).

    Words decide before the pairing, because the pairing reaches past a couplet's end:
    Pocatello's ``END 1-WAY E OF US-91 -> WARREN DR`` (19,000) lies beside the ``D``
    leg's last record and is two-way. Ramps, connectors and roundabouts get no
    evidence: their count is one movement, not one direction of a road.

    Args:
        aadt: a :func:`load_aadt` frame. Classified with :func:`classify_aadt_records`
            first if it has no ``record_kind``.
        pair_max_sep_m: see :data:`PAIR_MAX_SEP_M`.

    Returns:
        A copy with ``basis_evidence`` added.
    """
    out = aadt if RECORD_KIND_COL in aadt.columns else classify_aadt_records(aadt)
    out = out.copy()
    n = len(out)
    frm = out["Descriptio"] if "Descriptio" in out.columns else pd.Series([None] * n,
                                                                        index=out.index)
    to = out["Descript_1"] if "Descript_1" in out.columns else pd.Series([None] * n,
                                                                       index=out.index)
    kind = out[RECORD_KIND_COL]
    text = (frm.fillna("").astype(str) + " " + to.fillna("").astype(str)).str.upper()
    eligible = ~kind.isin([RAMP, CONNECTOR]) & ~text.str.contains(_ROUNDABOUT_RE)
    ev = pd.Series([_words_evidence(f, t) for f, t in zip(frm, to)],
                   index=out.index, dtype=object).where(eligible, None)

    if "RouteID" in out.columns and n and "geometry" in out.columns:
        rid = out["RouteID"].fillna("").astype(str)
        parsed = rid.str.match(_ROUTE_ID_RE.pattern)
        suffix = rid.str[5].where(parsed, "")
        base = (rid.str[:5] + rid.str[6:]).where(parsed, "")
        paired = eligible & suffix.isin(["A", "D"]) & ~ev.notna()
        # Only routes that carry a D line can pair; the rest of the layer is skipped.
        d_bases = set(base[eligible & suffix.eq("D")])
        cand = out.index[paired & base.isin(d_bases)]
        if len(cand):
            from shapely.geometry import Point
            from shapely.ops import linemerge, unary_union

            sub = out.loc[out.index[eligible & base.isin(d_bases)]]
            metric = sub.to_crs(sub.estimate_utm_crs()) if hasattr(sub, "to_crs") else sub
            geom = metric.geometry
            aadt_v = pd.to_numeric(sub[AADT_COL], errors="coerce")
            nan = pd.Series(float("nan"), index=sub.index)
            f_m = pd.to_numeric(sub.get("FromMeasur", nan), errors="coerce")
            t_m = pd.to_numeric(sub.get("ToMeasure", nan), errors="coerce")
            sfx, bse = suffix[sub.index], base[sub.index]
            legs = {}                 # (base, suffix) -> (merged line, its end points)
            for key, idx in sub.groupby([bse, sfx]).groups.items():
                parts = [g for g in geom[idx] if g is not None and not g.is_empty]
                if not parts:
                    continue
                merged = unary_union(parts)
                if merged.geom_type == "MultiLineString":
                    merged = linemerge(merged)
                lines = [ln for ln in getattr(merged, "geoms", [merged])
                         if ln.geom_type == "LineString"]
                ends = [Point(c) for ln in lines for c in (ln.coords[0], ln.coords[-1])]
                legs[key] = (merged, ends)
            for i in cand:
                g = geom[i]
                if g is None or g.is_empty:
                    continue
                other_sfx = "D" if suffix[i] == "A" else "A"
                partners = sub.index[(bse == base[i]) & (sfx == other_sfx)]
                verdict = None
                for j in partners:
                    if (abs(f_m[i] - f_m[j]) < 0.005 and abs(t_m[i] - t_m[j]) < 0.005
                            and aadt_v[i] == aadt_v[j]):
                        verdict = EV_DUPLICATED
                        break
                if verdict is None and (base[i], other_sfx) in legs:
                    other, ends = legs[(base[i], other_sfx)]
                    if _beside_fraction(g, other, ends, pair_max_sep_m) >= 0.5:
                        verdict = EV_ONE_WAY_PAIR
                if verdict is not None:
                    ev[i] = verdict

    out[BASIS_EVIDENCE_COL] = ev
    return out


def two_way_twins(geo, segment_ids, *, tol_m: float = TWO_WAY_TWIN_TOL_M,
                  metric_crs=None) -> set:
    """The ``segment_ids`` that are one carriageway of a two-way road.

    A segment is two-way when an opposing segment (bearings more than 120° apart) of
    the same street (:func:`inrix_tools.couplets.street_key`) lies within ``tol_m``
    at 10%, 50% and 90% of its length. A couplet leg has no such twin: the other leg
    is a different street, at least 25 m away. Without a ``RoadName`` column the
    street is not compared, only the geometry.
    """
    from .couplets import street_key
    from .geometry import _bearing_deg

    ids = [s for s in segment_ids if s in geo.index]
    if not ids:
        return set()
    valid = geo[geo.geometry.notna() & ~geo.geometry.is_empty]
    crs = metric_crs or valid.estimate_utm_crs()
    geom_m = valid.geometry.to_crs(crs)
    has_names = "RoadName" in valid.columns
    names = (valid["RoadName"].fillna("").map(lambda s: street_key(str(s)).lower())
             if has_names else None)
    from shapely import STRtree
    keys = list(geom_m.index)
    tree = STRtree(list(geom_m.values))
    bearings: dict = {}

    def _b(sid):
        if sid not in bearings:
            try:
                bearings[sid] = _bearing_deg(valid.geometry[sid])
            except (NotImplementedError, AttributeError):     # MultiLineString
                bearings[sid] = None
        return bearings[sid]

    out = set()
    for sid in ids:
        if sid not in geom_m.index:
            continue
        g = geom_m[sid]
        pts = [g.interpolate(f, normalized=True) for f in (0.1, 0.5, 0.9)]
        for k in tree.query(g.buffer(tol_m)):
            cid = keys[int(k)]
            if cid == sid or (has_names and names[cid] != names[sid]):
                continue
            b1, b2 = _b(sid), _b(cid)
            if b1 is None or b2 is None or abs((b1 - b2 + 180.0) % 360.0 - 180.0) <= 120.0:
                continue
            cg = geom_m[cid]
            if all(cg.distance(p) <= tol_m for p in pts):
                out.add(sid)
                break
    return out


def apply_directional_basis(joined, *, couplet_segments=(), network=None):
    """Put every segment's ``AADT`` on the per-direction basis (Items 53, 54).

    Idempotent: it starts from ``aadt_layer`` (the count the layer publishes, written
    on the first call) each time, so a second call with a couplet membership only
    adds to the first. Per segment:

    * a ramp or connector record (``aadt_source == "matched_ramp"``) keeps its count,
      basis ``ramp``: it is one movement already;
    * the count is kept whole (``one_way``) when the record is one-way by the layer's
      own evidence (``one_way_words`` / ``one_way_pair``, :func:`classify_aadt_basis`)
      **or** the segment is in ``couplet_segments`` and the layer does not say the
      record is two-way — **and** the segment itself is not one carriageway of a
      two-way road (:func:`two_way_twins`). That last test is what keeps a two-way
      street that happens to reach a one-way record on the halved count: Moscow's
      SH-8 Troy Rd, Blackfoot's Bridge St past Juniper, Boise's Broad St under Front
      St's ``D`` record;
    * everything else is ``two_way_half``: the count × :data:`TWO_WAY_SPLIT`.

    Args:
        joined: a :func:`join_aadt` frame, indexed by segment id.
        couplet_segments: segment ids on a one-way couplet leg (a catalogue's
            ``one_way_couplet`` corridors): the fallback where the layer has no
            evidence.
        network: the frame the two-way test searches for an opposing twin; default
            ``joined`` itself, which is the whole network wherever the join is.

    Returns:
        A copy with ``AADT`` rebased and ``aadt_layer`` / ``aadt_basis`` /
        ``aadt_basis_reason`` set. ``attrs['aadt_basis']`` counts the bases and
        reasons.
    """
    out = joined.copy()
    if AADT_LAYER_COL not in out.columns:
        out[AADT_LAYER_COL] = pd.to_numeric(out[AADT_COL], errors="coerce") \
            if AADT_COL in out.columns else float("nan")
    layer = pd.to_numeric(out[AADT_LAYER_COL], errors="coerce")
    source = out[AADT_SOURCE_COL] if AADT_SOURCE_COL in out.columns else \
        pd.Series("matched", index=out.index)
    rec = out[AADT_RECORD_BASIS_COL] if AADT_RECORD_BASIS_COL in out.columns else \
        pd.Series([None] * len(out), index=out.index, dtype=object)
    couplet = set(couplet_segments or ())

    has = layer.notna()
    ramp = has & source.eq("matched_ramp")
    layer_one = has & ~ramp & rec.isin(_EV_ONE_WAY)
    leg_one = (has & ~ramp & ~layer_one & out.index.isin(couplet)
               & ~rec.isin(_EV_TWO_WAY))
    cand = out.index[layer_one | leg_one]
    twins = two_way_twins(network if network is not None else out, cand) \
        if len(cand) else set()
    is_twin = out.index.isin(twins)
    one_way = (layer_one | leg_one) & ~is_twin
    halved = has & ~ramp & ~one_way

    basis = pd.Series(pd.NA, index=out.index, dtype=object)
    basis[halved] = TWO_WAY_HALF
    basis[ramp] = RAMP_MOVEMENT
    basis[one_way] = ONE_WAY
    reason = pd.Series(pd.NA, index=out.index, dtype=object)
    reason[has] = "default"
    reason[has & rec.isin(_EV_TWO_WAY)] = rec[has & rec.isin(_EV_TWO_WAY)]
    reason[ramp] = "ramp_movement"
    reason[(layer_one | leg_one) & is_twin] = "two_way_street"
    reason[layer_one & ~is_twin] = rec[layer_one & ~is_twin]
    reason[leg_one & ~is_twin] = "couplet_leg"

    out[AADT_COL] = layer.where(~halved, layer * TWO_WAY_SPLIT)
    out[AADT_BASIS_COL] = basis
    out[AADT_BASIS_REASON_COL] = reason
    out.attrs = dict(joined.attrs)
    out.attrs["aadt_basis"] = {
        "basis": "per direction (a two-way count x 0.5)",
        "counts": {k: int(v) for k, v in basis.value_counts().items()},
        "reasons": {k: int(v) for k, v in reason.value_counts().items()},
        "n_couplet_segments": len(couplet),
    }
    return out


def to_directional_basis(geo):
    """Bring a cached join (a GUI geometry cache) onto the per-direction basis.

    A cache written since Item 54 is returned as is. One written under Item 53 carries
    ``aadt_layer`` and the old labels, which say which counts to halve (``two_way``)
    and which to keep (``one_way_x2``, ``ramp``). One written before Item 53 carries
    the published count as ``AADT`` and no basis: every count but a ramp's is halved
    there, as the one-way evidence was never recorded (reason ``legacy_cache``;
    re-ingest to get it). A frame without ``AADT`` is returned unchanged.
    """
    if geo is None or AADT_COL not in getattr(geo, "columns", []):
        return geo
    basis = geo[AADT_BASIS_COL] if AADT_BASIS_COL in geo.columns else None
    if basis is not None and not basis.isin([_LEGACY_TWO_WAY, _LEGACY_ONE_WAY_X2]).any():
        return geo
    out = geo.copy()
    if AADT_LAYER_COL not in out.columns:
        out[AADT_LAYER_COL] = pd.to_numeric(out[AADT_COL], errors="coerce")
    layer = pd.to_numeric(out[AADT_LAYER_COL], errors="coerce")
    has = layer.notna()
    if basis is not None:
        halved = has & basis.eq(_LEGACY_TWO_WAY)
        new = basis.replace({_LEGACY_TWO_WAY: TWO_WAY_HALF, _LEGACY_ONE_WAY_X2: ONE_WAY})
    else:
        source = out[AADT_SOURCE_COL] if AADT_SOURCE_COL in out.columns else \
            pd.Series("matched", index=out.index)
        ramp = has & source.eq("matched_ramp")
        halved = has & ~ramp
        new = pd.Series(pd.NA, index=out.index, dtype=object)
        new[halved] = TWO_WAY_HALF
        new[ramp] = RAMP_MOVEMENT
        out[AADT_BASIS_REASON_COL] = pd.Series(pd.NA, index=out.index, dtype=object) \
            .mask(halved, "legacy_cache").mask(ramp, "ramp_movement")
    out[AADT_COL] = layer.where(~halved, layer * TWO_WAY_SPLIT)
    out[AADT_BASIS_COL] = new
    return out


# ---------------------------------------------------------------------------
# On-system classification  (Item 42)
# ---------------------------------------------------------------------------
ON_SYSTEM_COL = "on_system"
ON_SYSTEM_REASON_COL = "on_system_reason"
ON_SYSTEM_CATEGORY_COL = "on_system_category"
# A record boundary landing mid-segment cuts coverage without saying anything about
# whether the segment is on the route: of the seven SH-19 segments the owner
# confirmed as a real omission from the export, one covers 0.45. The threshold sits
# below that deliberately — it is here to reject a record that merely clips the
# segment, not to adjudicate where ITD split its linear reference.
DEFAULT_ON_SYSTEM_COVERAGE = 0.4
# Wider than it looks: on a divided highway the mainline centerline sits 22-30 m off
# each carriageway (DATA_FORMAT, Item 34), so a 20 m rule would rule out the
# interstates themselves.
DEFAULT_ON_SYSTEM_DISTANCE_M = 35.0

# Street-name comparison. Directionals and street types carry no identity — "E Amity
# Rd" and "AMITY RD" are the same street, "EAGLE RD (SH-55)" and "E Island Woods Dr"
# are not — so both are dropped and what is left is compared as a token set.
_NAME_DIRECTIONALS = {"N", "S", "E", "W", "NE", "NW", "SE", "SW",
                      "NB", "SB", "EB", "WB", "OLD", "BEG", "END"}
_NAME_TYPES = {"RD", "AVE", "AV", "ST", "BLVD", "LN", "DR", "WAY", "HWY", "HIGHWAY",
               "PKWY", "CT", "CIR", "PL", "TRL", "EXT", "LOOP", "BYPASS", "SPUR",
               "CONN", "JCT", "IC", "RAMP", "RAMPS"}


def street_name_tokens(name) -> set:
    """The identifying words of a street name — directionals, street types and a
    trailing route parenthetical removed. ``"KARCHER RD (SH-55)"`` -> ``{"KARCHER"}``.

    An AADT description often names two places (``"9TH ST N (E OF RR)"``,
    ``"IDAHO AVE @ US-95 CONN"``); only the part before the first ``@`` or ``/`` is
    the record's own street.
    """
    if not isinstance(name, str):
        return set()
    text = re.sub(r"\([^)]*\)", " ", name.upper())
    text = re.split(r"[@/]", text)[0]
    text = re.sub(r"[^A-Z0-9 ]", " ", text)
    return {w for w in text.split()
            if w and w not in _NAME_DIRECTIONALS and w not in _NAME_TYPES}


def street_names_agree(road_name, description) -> bool:
    """Do an XD ``RoadName`` and an AADT ``Descriptio`` name the same street?
    True when their identifying tokens overlap at all; False when either side has
    no identifying token (an unnamed segment proves nothing either way)."""
    a, b = street_name_tokens(road_name), street_name_tokens(description)
    return bool(a and b and (a & b))


def classify_on_system(joined, *, min_coverage=DEFAULT_ON_SYSTEM_COVERAGE,
                       max_distance_m=DEFAULT_ON_SYSTEM_DISTANCE_M,
                       require_mainline=True, require_identity=True):
    """Is this segment **on a numbered route**? — a separate question from which
    volume it carries, and one that must not be read off the volume join (Item 42).

    :func:`join_aadt` answers "whose volume does this segment take", by proximity
    within a 60 m gate. Taking "the winning record names a route" as *on-system*
    is what made the earlier candidate list untrustworthy: on the D3 network off the
    export it labels 764 segments / 223 miles on-system, and they are cross-streets
    and subdivision drives lying beside a state route — 24 stubs of E Island Woods Dr
    took ``EAGLE RD (SH-55)``, 66 of Simco Rd took ``GRANDVIEW RD (SH-167)``. Note
    coverage cannot catch them: a 0.04-mile stub beside a mile-long record covers
    1.00. The tests here are:

    * the chosen record **names a route** (``aadt_route_number``, which reads an
      ``OH``-banded ``KARCHER RD (SH-55)`` as SH-55 — see
      :func:`record_route_number`),
    * **identity agrees** (``require_identity``): either the record's description
      names the same street as the segment (:func:`street_names_agree`) or the
      segment itself names a route in its XD ``RoadNumber`` / ``RoadList``. This is
      the test that drops the 764 to 24,
    * the record is a **mainline** record, not a ramp or connector
      (``require_mainline``),
    * it lies within ``max_distance_m``, tighter than the join's own gate,
    * and it runs beside at least ``min_coverage`` of the segment.

    Args:
        joined: a frame from :func:`join_aadt`, carrying the XD identity columns
            (``RoadName`` / ``RoadNumber`` / ``RoadList``) that
            :func:`inrix_tools.geometry.segment_geometry` provides.
        min_coverage: least share of the segment the record must run beside.
        max_distance_m: furthest the record may lie from the segment.
        require_mainline: reject a ramp/connector record.
        require_identity: apply the street-name / segment-route test.

    Returns:
        A copy of ``joined`` with ``on_system`` (bool), ``on_system_reason`` — the
        route for a segment that passes (``"route 55"``), else why it did not, in its
        own numbers so a rejection can be argued with — and
        ``on_system_category``, the same rejection as one of a fixed set.
        ``attrs['on_system']`` records the thresholds and the counts.
    """
    out = joined.copy()
    n = len(out)
    if n == 0:
        out[ON_SYSTEM_COL] = pd.Series(dtype=bool)
        out[ON_SYSTEM_REASON_COL] = pd.Series(dtype=object)
        out[ON_SYSTEM_CATEGORY_COL] = pd.Series(dtype=object)
        out.attrs = dict(joined.attrs)
        out.attrs["on_system"] = _on_system_policy(
            min_coverage, max_distance_m, require_mainline, require_identity,
            out[ON_SYSTEM_CATEGORY_COL])
        return out

    def _col(name, default=None):
        return (out[name] if name in out.columns
                else pd.Series([default] * n, index=out.index))

    source = _col(AADT_SOURCE_COL, "missing")
    number = _col(AADT_ROUTE_NUM_COL)
    dist = pd.to_numeric(_col(AADT_DIST_COL), errors="coerce")
    cover = pd.to_numeric(_col(AADT_COVER_COL), errors="coerce")
    kind = _col(AADT_KIND_COL)
    desc = _col(AADT_DESC_COL)
    road = _col("RoadName")

    reason = pd.Series([None] * n, index=out.index, dtype=object)
    category = pd.Series([None] * n, index=out.index, dtype=object)

    def _reject(mask, cat, detail=None):
        # A rejection is recorded as its category *and* in its own numbers; the
        # category is the authority, so a detail that cannot be written (a NaN
        # distance) falls back to it rather than reading as a pass.
        hit = category.isna() & pd.Series(mask, index=out.index).fillna(False)
        category[hit] = cat
        reason[hit] = cat if detail is None else detail[hit].fillna(cat)

    _reject(~source.isin(("matched", "matched_ramp")), "no AADT record matched")
    _reject(number.isna(), "the matched record names no route")
    if require_identity:
        named = pd.Series([street_names_agree(r, d) for r, d in zip(road, desc)],
                          index=out.index)
        seg_routes = _segment_route_numbers(out)
        own = pd.Series([bool(seg_routes.get(sid)) for sid in out.index], index=out.index)
        _reject(~(named | own), "a neighbouring road: neither the name nor a route agrees")
    if require_mainline:
        _reject(kind.isin((RAMP, CONNECTOR)), "the matched record is a ramp or connector")
    _reject(dist.isna() | (dist > max_distance_m), "the record is too far away",
            dist.round(1).astype("string").radd("the record is ") + " m away")
    _reject(cover.isna() | (cover < min_coverage), "the record clips the segment",
            (cover * 100).round().astype("Int64").astype("string")
            .radd("the record runs beside ") + "% of the segment")

    passed = category.isna()
    label = number.map(lambda v: None if pd.isna(v) else f"route {int(v)}")
    reason[passed] = label[passed]
    out[ON_SYSTEM_COL] = passed
    out[ON_SYSTEM_REASON_COL] = reason
    out[ON_SYSTEM_CATEGORY_COL] = category
    out.attrs = dict(joined.attrs)
    out.attrs["on_system"] = _on_system_policy(
        min_coverage, max_distance_m, require_mainline, require_identity,
        category[~passed])
    return out


def _on_system_policy(min_coverage, max_distance_m, require_mainline,
                      require_identity, rejected) -> dict:
    """The thresholds actually applied plus why the rejections were rejected — the
    module's convention of travelling with its own policy."""
    counts = rejected.value_counts().to_dict() if len(rejected) else {}
    return {
        "min_coverage": float(min_coverage),
        "max_distance_m": float(max_distance_m),
        "require_mainline": bool(require_mainline),
        "require_identity": bool(require_identity),
        "n_rejected": int(len(rejected)),
        "rejected_because": {str(k): int(v) for k, v in counts.items()},
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

"""Which volume-profile curve each XD segment gets.  (ROADMAP Item 56)

Item 55 built the curve library (:mod:`inrix_tools.volume_profiles`). This module
gives every XD segment one ``curve_id`` from it, by the first of these that decides:

1. **override** — ``scripts/volume_profile_overrides.csv``, keyed on an ``XDSegID`` or
   on a catalogue corridor + direction (:func:`load_overrides`). An override always
   wins; a segment row beats a corridor row.
2. **station** — a curve fitted from counts (Item 59, :mod:`inrix_tools.counts`) at
   the nearest count station along the path on the same **route section** — the route
   traced in one direction between junctions with same-or-higher-tier state routes,
   hard stops and its ends (Item 61, :mod:`inrix_tools.route_sections`;
   :func:`station_rule`). A count measures the shape the inference below only orients,
   so it outranks the inference.
3. **inferred** — the *orientation* of a chain, read off its delay. A chain is one
   direction of a road (:class:`Chain`), paired with the chain running the other way.
   When one side's peak delay is clearly AM (``am_share ≥`` :data:`AM_SHARE_COMMUTE`)
   and the opposite side's clearly PM (``≤`` :data:`AM_SHARE_OPPOSITE`), both above
   :data:`MIN_PEAK_DELAY_PER_MILE`, the AM side gets ``am_commute_urban`` and the PM
   side ``pm_commute_urban``. Every segment of the chain inherits the decision, so the
   orientation cannot flip along a road. A bottleneck congested at both peaks, or in
   one direction only, is not evidence and falls through.
4. **urban_rule** — from the segment's urban context (``itd_layers.urban_context``)
   and the bearing toward its urban area's centre (:func:`segment_context`): the
   owner-reviewed economic centre where ``scripts/urban_centres.csv`` has one
   (:func:`apply_urban_centres`), else the polygon centroid:
   interstate → ``interstate_through``; in a commute-sized urban area (or its
   approach), inbound → ``am_commute_urban``, outbound → ``pm_commute_urban``;
   inside an urban area with no clear radial → ``balanced_urban``; otherwise
   ``rural_through``.
5. **default** — :data:`DEFAULT_CURVE`, where nothing above can say anything (no
   geometry, no urban context).

The inference picks only **which side** is the AM-commute side, never a volume. That
keeps the circularity with delay harmless: on a real commute road the busier-in-the-AM
direction *is* the AM-commute direction (the TTI Urban Mobility Report's directional
profiles are defined the same way, Item 55).

Chains come from two places (:func:`catalogue_chains`, :func:`route_runs`):
catalogue corridor directions where the segment has one, else the route-membership
run it lies on. A segment in several decides by the first decisive one, catalogue
chains first and the shortest first (a generated catalogue's ``core`` tier before its
``regional``).

Pure: pandas/numpy only. The urban-area centroids come in as a frame
(``itd_layers.urban_centroids``), not as polygons.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Mapping, Sequence

import numpy as np
import pandas as pd

from .geometry import direction_sign

# ---------------------------------------------------------------------------
# Curves and sources
# ---------------------------------------------------------------------------
AM_CURVE = "am_commute_urban"
PM_CURVE = "pm_commute_urban"
BALANCED_CURVE = "balanced_urban"
RURAL_CURVE = "rural_through"
INTERSTATE_CURVE = "interstate_through"

DEFAULT_CURVE = BALANCED_CURVE
"""Where nothing is known. The two-peak urban shape commits least to either peak."""

OVERRIDE = "override"
STATION = "station"
INFERRED = "inferred"
URBAN_RULE = "urban_rule"
DEFAULT = "default"
CURVE_SOURCES = (OVERRIDE, STATION, INFERRED, URBAN_RULE, DEFAULT)

ASSIGNMENT_COLUMNS = ("curve_id", "curve_source", "am_share_self", "am_share_opposite",
                      "chain_id", "reason")

# ---------------------------------------------------------------------------
# Inference thresholds
# ---------------------------------------------------------------------------
AM_SHARE_COMMUTE = 0.65
"""A side is AM-heavy when at least this share of its AM + PM peak delay is AM."""
AM_SHARE_OPPOSITE = 0.35
"""…and its opposite PM-heavy when at most this share of its peak delay is AM."""
MIN_PEAK_DELAY_PER_MILE = 0.10
"""Minutes of delay per observed mile, at the side's worse peak, below which a side
says nothing. 0.1 min/mi is 6 s/mi — about 10 % over free flow at 60 mph."""
PEAK_WINDOWS = ("am", "pm")
"""The ``screen.PEAK_WINDOWS`` whose mean delays the inference reads."""

# ---------------------------------------------------------------------------
# Urban rule
# ---------------------------------------------------------------------------
INTERSTATE_ROUTES = frozenset({"15", "84", "86", "90", "184"})
"""Idaho's interstates, for segments whose ITD route id does not say (``…IN084``)."""
URBAN_COMMUTE_MIN_POP = 50_000
"""Urban areas at least this populous get the radial commute rule — the Census's
long-standing "urbanized area" size. A smaller town's traffic is two-peak but not
radial enough to orient: inside it gets ``balanced_urban``, around it
``rural_through``."""
URBAN_APPROACH_M = 5_000.0
"""How far outside a commute-sized urban area's boundary its commute shed still
reaches. The boundary guides, it does not cut: Boise's and Nampa's commute crosses
the rural gap between them."""
URBAN_CORE_RADIUS_M = 1_500.0
"""Within this distance of the centre the bearing to it means nothing (no radial)."""
RADIAL_TOL_DEG = 45.0
"""Travel within this many degrees of the bearing to the centre is inbound; within
this many of the bearing away from it, outbound; anything between is tangential."""

# ---------------------------------------------------------------------------
# Station rule (Item 59)
# ---------------------------------------------------------------------------
STATION_SNAP_MILES = 0.25
"""How far a station may sit from the segment it snaps to. TCDS points are placed by
hand and sit up to ~0.2 mi off the carriageway (ATR 00228 on SH-55 in Nampa)."""
STATION_DIRECTION_TOL_DEG = 60.0
"""A segment is in the station's direction when its travel bearing is within this
many degrees of the direction's compass bearing (``counts.DIRECTION_BEARING``): a
cardinal label covers its diagonal neighbours' half-way line, so a NW/SE-labelled
station on a diagonal road still matches."""

INBOUND, OUTBOUND, TANGENTIAL = "in", "out", "tangential"
URBAN, APPROACH, RURAL = "urban", "approach", "rural"

_EARTH_M = 6_371_008.8


# ---------------------------------------------------------------------------
# Geometry helpers (lat/lon only — no GIS)
# ---------------------------------------------------------------------------
def initial_bearing(lat1, lon1, lat2, lon2) -> np.ndarray:
    """Initial great-circle bearing from point 1 to point 2, degrees clockwise from
    north in ``[0, 360)``. Vectorised."""
    p1, p2 = np.radians(lat1), np.radians(lat2)
    dl = np.radians(np.asarray(lon2, dtype=float) - np.asarray(lon1, dtype=float))
    x = np.sin(dl) * np.cos(p2)
    y = np.cos(p1) * np.sin(p2) - np.sin(p1) * np.cos(p2) * np.cos(dl)
    return (np.degrees(np.arctan2(x, y)) + 360.0) % 360.0


def haversine_m(lat1, lon1, lat2, lon2) -> np.ndarray:
    """Great-circle distance in metres. Vectorised."""
    p1, p2 = np.radians(lat1), np.radians(lat2)
    dp = p2 - p1
    dl = np.radians(np.asarray(lon2, dtype=float) - np.asarray(lon1, dtype=float))
    a = np.sin(dp / 2) ** 2 + np.cos(p1) * np.cos(p2) * np.sin(dl / 2) ** 2
    return 2 * _EARTH_M * np.arcsin(np.sqrt(a))


def _angle_between(b1, b2) -> np.ndarray:
    return np.abs((np.asarray(b1, dtype=float) - np.asarray(b2, dtype=float) + 180.0)
                  % 360.0 - 180.0)


# ---------------------------------------------------------------------------
# Segment context
# ---------------------------------------------------------------------------
def is_interstate(route_number, itd_route_id=None) -> bool:
    """Whether a segment's route is an interstate: the ITD route id says so
    (``01540AIN084``), or, without one, the number is an Idaho interstate's."""
    rid = "" if itd_route_id is None or pd.isna(itd_route_id) else str(itd_route_id)
    if len(rid) >= 5:
        return rid[-5:-3] == "IN"
    rn = "" if route_number is None or pd.isna(route_number) else str(route_number)
    return rn in INTERSTATE_ROUTES


def segment_context(network: pd.DataFrame, membership: pd.DataFrame | None = None,
                    urban: pd.DataFrame | None = None,
                    centroids: pd.DataFrame | None = None) -> pd.DataFrame:
    """Everything the chains and the urban rule read about each segment.

    Args:
        network: the XD network, one row per segment: ``XDSegID`` (column or index),
            ``Bearing``, ``Miles``, and ``StartLat/StartLong/EndLat/EndLong`` (in
            travel order; without them no segment is judged radial).
        membership: ``routes.read_membership`` (``route_number``, ``itd_route_id``,
            ``verdict``), indexed by ``XDSegID``; ``None`` = no routes known.
        urban: the ``d{N}_urban_context.csv`` frame (``itd_layers.urban_context``),
            indexed by ``XDSegID``; ``None`` = no urban context.
        centroids: ``itd_layers.urban_centroids`` — ``UACE``-indexed
            ``centroid_lat``, ``centroid_lon``, ``population`` (after
            :func:`apply_urban_centres`, the centre it reads); ``None`` = no radial.

    Returns:
        Indexed by ``XDSegID``: ``miles``, ``route`` (str or None), ``interstate``
        (never a business loop), ``itd_route_id``, ``ramp``, ``routes`` (every route
        number it carries, a tuple), ``business`` (on a business loop), ``dir_sign``
        (+1 N/E, −1 S/W, 0 unknown), ``travel_bearing``, ``start_lat`` / ``start_lon``
        / ``end_lat`` / ``end_lon``, ``next_xd`` / ``prev_xd`` (the XD links, for
        the station rule's walk),
        ``urban_uace``, ``urban_area``, ``zone`` (``urban`` / ``approach`` /
        ``rural``, None without context), ``commute_area`` (the area is commute-sized),
        ``radial`` (``in`` / ``out`` / ``tangential``, None when not judged),
        ``radial_angle`` (degrees between travel and the bearing to the centre).
    """
    net = network.copy()
    if "XDSegID" in net.columns:
        net = net.set_index(net["XDSegID"].astype("int64"))
    net.index = net.index.astype("int64")
    net.index.name = "XDSegID"
    out = pd.DataFrame(index=net.index)
    out["miles"] = pd.to_numeric(net.get("Miles"), errors="coerce")

    if membership is not None and len(membership):
        mem = membership.reindex(out.index)
        route = mem.get("route_number")
        out["route"] = (route.where(route.notna(), None).map(
            lambda r: None if r is None or str(r).strip() == "" else str(r).strip())
            if route is not None else None)
        rid = mem.get("itd_route_id", pd.Series(None, index=out.index))
        verdict = mem.get("verdict", pd.Series(None, index=out.index))
        # A business loop carries its interstate's ITD route id (I-84 BL on Garrity
        # and Caldwell Blvds is ``…AIN084``), but it is a surface street.
        out["interstate"] = [r is not None and v != "business" and is_interstate(r, i)
                             for r, i, v in zip(out["route"], rid, verdict)]
        out["itd_route_id"] = rid.where(rid.notna(), None)
        out["ramp"] = (mem.get("verdict") == "ramp").fillna(False).to_numpy(bool)
        out["business"] = (mem.get("verdict") == "business").fillna(False).to_numpy(bool)
        many = mem.get("routes", pd.Series(None, index=out.index))
        out["routes"] = [_route_set(m, r) for m, r in zip(many, out["route"])]
    else:
        out["route"] = None
        out["interstate"] = False
        out["itd_route_id"] = None
        out["ramp"] = False
        out["business"] = False
        out["routes"] = [()] * len(out)

    out["dir_sign"] = [direction_sign(b) for b in net.get("Bearing", pd.Series(
        None, index=net.index))]
    nan = pd.Series(np.nan, index=net.index)
    lat1, lon1, lat2, lon2 = (pd.to_numeric(net.get(c, nan), errors="coerce")
                              for c in ("StartLat", "StartLong", "EndLat", "EndLong"))
    out["travel_bearing"] = initial_bearing(lat1, lon1, lat2, lon2)
    out["start_lat"], out["start_lon"] = lat1.to_numpy(), lon1.to_numpy()
    out["end_lat"], out["end_lon"] = lat2.to_numpy(), lon2.to_numpy()
    for col, src in (("next_xd", "NextXDSegI"), ("prev_xd", "PreviousXD")):
        out[col] = (pd.array(pd.to_numeric(net[src], errors="coerce"), dtype="Int64")
                    if src in net.columns else pd.array([pd.NA] * len(net), dtype="Int64"))
    mid_lat, mid_lon = (lat1 + lat2) / 2, (lon1 + lon2) / 2

    out["urban_uace"] = None
    out["urban_area"] = None
    out["zone"] = None
    out["commute_area"] = False
    out["radial"] = None
    out["radial_angle"] = np.nan
    if urban is None or not len(urban):
        return out

    ctx = urban.reindex(out.index)
    has = ctx["urban_uace"].notna()
    uace = ctx["urban_uace"].map(lambda u: None if pd.isna(u) else str(u).zfill(5))
    out["urban_uace"] = uace
    out["urban_area"] = ctx["urban_area"].where(has, None)
    inside = ctx["urban_inside"].map(lambda v: str(v).strip().lower() == "true"
                                     if pd.notna(v) else False)
    edge = pd.to_numeric(ctx["urban_edge_m"], errors="coerce")

    pop = pd.Series(np.nan, index=out.index)
    if centroids is not None and len(centroids):
        cen = centroids.copy()
        cen.index = cen.index.map(lambda u: str(u).zfill(5))
        pop = uace.map(cen["population"]).astype(float)
        c_lat = uace.map(cen["centroid_lat"]).astype(float)
        c_lon = uace.map(cen["centroid_lon"]).astype(float)
    commute = pop >= URBAN_COMMUTE_MIN_POP
    out["commute_area"] = commute.to_numpy(bool)

    zone = np.where(inside, URBAN,
                    np.where(commute & (edge >= -URBAN_APPROACH_M), APPROACH, RURAL))
    out["zone"] = pd.Series(zone, index=out.index).where(has, None)

    if centroids is not None and len(centroids):
        to_c = initial_bearing(mid_lat, mid_lon, c_lat, c_lon)
        angle = _angle_between(out["travel_bearing"], to_c)
        dist = haversine_m(mid_lat, mid_lon, c_lat, c_lon)
        judged = (commute & out["zone"].isin([URBAN, APPROACH]) & c_lat.notna()
                  & out["travel_bearing"].notna())
        radial = np.where(dist < URBAN_CORE_RADIUS_M, TANGENTIAL,
                          np.where(angle <= RADIAL_TOL_DEG, INBOUND,
                                   np.where(angle >= 180.0 - RADIAL_TOL_DEG, OUTBOUND,
                                            TANGENTIAL)))
        out["radial"] = pd.Series(radial, index=out.index).where(judged, None)
        out["radial_angle"] = pd.Series(angle, index=out.index).where(judged).round(1)
    return out


def _route_set(routes, route) -> tuple[str, ...]:
    """Membership's ``routes`` (``"30/95"``) as a tuple of route numbers, else the
    single ``route``."""
    text = "" if routes is None or (isinstance(routes, float) and math.isnan(routes)) \
        else str(routes).strip()
    parts = [p.strip().lstrip("0") for p in re.split(r"[/;,]", text) if p.strip()]
    if not parts and route is not None:
        parts = [str(route).lstrip("0")]
    return tuple(parts)


URBAN_CENTRE_COLUMNS = ("uace", "urban_area", "centre_lat", "centre_lon", "note")


def load_urban_centres(path) -> pd.DataFrame:
    """The owner-reviewed urban-centre table (``#`` lines are comments).

    Columns :data:`URBAN_CENTRE_COLUMNS`. Each row puts an urban area's radial centre
    at its **economic** centre (where the jobs are, usually downtown) instead of the
    polygon centroid: Boise City's centroid is 7.5 km west of downtown because the
    polygon takes in Meridian, and the in/out rule reverses between the two.

    Returns:
        Indexed by ``UACE`` (5-digit string): ``urban_area``, ``centre_lat``,
        ``centre_lon``, ``note``.

    Raises:
        ValueError: a missing column, a UACE that is not 5 digits or repeats, a
            lat/lon out of range, or a row without a note.
    """
    frame = pd.read_csv(path, dtype=str, comment="#").fillna("")
    missing = [c for c in URBAN_CENTRE_COLUMNS if c not in frame.columns]
    if missing:
        raise ValueError(f"{path}: urban-centre table lacks columns {missing}")
    for col in URBAN_CENTRE_COLUMNS:
        frame[col] = frame[col].str.strip()
    frame["uace"] = frame["uace"].str.zfill(5)
    if (~frame["uace"].str.fullmatch(r"\d{5}")).any():
        raise ValueError(f"{path}: uace must be a 5-digit Census code")
    if frame["uace"].duplicated().any():
        raise ValueError(f"{path}: a uace appears twice: "
                         f"{sorted(frame.loc[frame['uace'].duplicated(), 'uace'])}")
    lat = pd.to_numeric(frame["centre_lat"], errors="coerce")
    lon = pd.to_numeric(frame["centre_lon"], errors="coerce")
    if (~lat.between(-90, 90) | ~lon.between(-180, 180)).any():
        raise ValueError(f"{path}: centre_lat/centre_lon missing or out of range "
                         "(the order is lat, lon)")
    if (frame["note"] == "").any():
        raise ValueError(f"{path}: every centre needs a note saying why")
    return pd.DataFrame({"urban_area": frame["urban_area"].to_numpy(),
                         "centre_lat": lat.to_numpy(), "centre_lon": lon.to_numpy(),
                         "note": frame["note"].to_numpy()},
                        index=pd.Index(frame["uace"], name="UACE"))


def apply_urban_centres(centroids: pd.DataFrame, centres: pd.DataFrame | None
                        ) -> pd.DataFrame:
    """``centroids`` (``itd_layers.urban_centroids``) with each area in ``centres``
    (:func:`load_urban_centres`) moved to its table centre. ``centre_source`` says
    which: ``table`` or ``centroid``. A table row for an area absent from
    ``centroids`` is ignored (it belongs to another state's layer)."""
    out = centroids.copy()
    out.index = out.index.map(lambda u: str(u).zfill(5))
    out["centre_source"] = "centroid"
    if centres is None or not len(centres):
        return out
    hit = centres.index.intersection(out.index)
    out.loc[hit, "centroid_lat"] = centres.loc[hit, "centre_lat"].to_numpy()
    out.loc[hit, "centroid_lon"] = centres.loc[hit, "centre_lon"].to_numpy()
    out.loc[hit, "centre_source"] = "table"
    return out


def urban_rule(context: pd.DataFrame) -> pd.DataFrame:
    """The urban in/out rule on :func:`segment_context`: ``curve_id`` and ``reason``
    per segment, ``curve_id`` None where the context says nothing (→ default)."""
    curve, reason = [], []
    for r in context.itertuples():
        area = r.urban_area or "?"
        if r.interstate:
            curve.append(INTERSTATE_CURVE)
            reason.append(f"interstate ({r.itd_route_id or 'I-' + str(r.route)})")
        elif r.zone is None:
            curve.append(None)
            reason.append("no urban context")
        elif r.radial == INBOUND:
            curve.append(AM_CURVE)
            reason.append(f"inbound to {area} ({r.zone}, {r.radial_angle:.0f}° off "
                          "the centre)")
        elif r.radial == OUTBOUND:
            curve.append(PM_CURVE)
            reason.append(f"outbound from {area} ({r.zone}, {r.radial_angle:.0f}° off "
                          "the centre)")
        elif r.zone == URBAN:
            curve.append(BALANCED_CURVE)
            why = ("tangential" if r.radial == TANGENTIAL
                   else "below the commute-size floor" if not r.commute_area
                   else "no centroid")
            reason.append(f"inside {area}, no clear radial ({why})")
        else:
            curve.append(RURAL_CURVE)
            reason.append(f"rural ({r.zone}; nearest urban area {area})")
    return pd.DataFrame({"curve_id": curve, "reason": reason}, index=context.index)


# ---------------------------------------------------------------------------
# Station rule (Item 59)
# ---------------------------------------------------------------------------
STATION_RULE_COLUMNS = ("curve_id", "reason", "station_id", "station_direction",
                        "station_miles")


def _point_to_chord(lat, lon, lat1, lon1, lat2, lon2) -> tuple[np.ndarray, np.ndarray]:
    """``(metres, t)`` from one point to each segment's start→end chord, and where
    along the chord (0 = start, 1 = end) the nearest point is, on a local
    equirectangular plane (exact enough within a few miles)."""
    k = math.cos(math.radians(lat))
    to_m = math.pi / 180.0 * _EARTH_M
    ax, ay = (np.asarray(lon1, float) - lon) * k * to_m, (np.asarray(lat1, float) - lat) * to_m
    bx, by = (np.asarray(lon2, float) - lon) * k * to_m, (np.asarray(lat2, float) - lat) * to_m
    dx, dy = bx - ax, by - ay
    den = dx * dx + dy * dy
    with np.errstate(invalid="ignore", divide="ignore"):
        t = np.clip(np.where(den > 0, -(ax * dx + ay * dy) / den, 0.0), 0.0, 1.0)
    return np.hypot(ax + t * dx, ay + t * dy), t


def _on_station_route(context: pd.DataFrame, route: str) -> np.ndarray:
    """Segments on a station's route (``counts.COUNT_COLUMNS``' ``route`` form):
    an interstate number matches interstate mainline only, a business loop
    (``"84 BL"``) business-loop segments carrying that number, and a US/SH number any
    segment carrying it."""
    from .counts import split_route
    number, business = split_route(route)
    if number is None:
        return np.zeros(len(context), dtype=bool)
    carries = np.array([number in r for r in context["routes"]], dtype=bool)
    if business:
        return carries & context["business"].to_numpy(bool)
    if number in INTERSTATE_ROUTES:
        return carries & context["interstate"].to_numpy(bool)
    return carries


def _station_section(anchor: int, route: str, by_seg: Mapping[int, list]):
    """The section a station snapped to ``anchor`` counts: of those through the
    anchor, the one of the station's route key (``"84 BL"`` for a business loop), else
    of its route number, else the longest."""
    from .counts import split_route
    here = by_seg.get(int(anchor), [])
    if not here:
        return None
    number, business = split_route(route)
    want = f"{number}{' BL' if business else ''}"
    for pick in ([s for s in here if s.route == want],
                 [s for s in here if s.number == number], here):
        if pick:
            return max(pick, key=lambda s: (s.miles, s.section_id))
    return None


def station_rule(context: pd.DataFrame, stations: pd.DataFrame | None, *,
                 sections: Sequence | None = None,
                 use_generic: bool = False) -> pd.DataFrame:
    """The count station's curve for its section of the road (Item 61).

    Each station-direction (``counts.station_curves``) **snaps** to the nearest
    segment that is on its route (:func:`_on_station_route`), not a ramp, travelling
    within :data:`STATION_DIRECTION_TOL_DEG` of its direction and within
    :data:`STATION_SNAP_MILES` of it. It then covers the **route section** that segment
    lies on (``route_sections.trace_sections``): the route traced one way, from a
    junction with a same-or-higher-tier state route, a hard stop or the route's end to
    the next. Tracing the route rather than drawing a radius keeps a station off a
    parallel street of the same route (Boise's Broadway ATR is US-20, and so are the
    Front/Myrtle couplet and Chinden). Where a section holds several stations, each
    segment takes the **nearest along the path**; a segment on sections of two routes
    (a concurrency) takes the nearest of either's stations. A section with no station
    falls through to the rules below.

    A ``2WAY`` station, or one with no route, covers nothing: it cannot say which
    direction's shape it measured, or which road.

    Args:
        context: :func:`segment_context`.
        stations: ``counts.station_curves``' table (``station_id``, ``direction``,
            ``lat``, ``lon``, ``route``, ``source``, ``curve_id``,
            ``nearest_generic``, ``misplaced_share``); ``None`` = no stations.
        sections: ``route_sections.trace_sections`` over the same network; ``None``
            = none traced, so a station covers only the segment it snaps to.
        use_generic: assign each station's ``nearest_generic`` instead of its fitted
            curve (the fitted curves mapped back onto the generic ids).

    Returns:
        Indexed like ``context``, columns :data:`STATION_RULE_COLUMNS`
        (``station_miles`` is along the path to the segment's near end, 0 for the one
        the station is on); ``curve_id`` None where no station covers the segment.
        ``attrs['stations']``: per station-direction, the segment it snapped to, its
        section (``section_id``, ``section_from`` / ``section_to``, ``section_miles``,
        ``n_segments``) and what it won (``n_assigned``, ``miles_assigned``), or why
        nothing.
    """
    from .counts import DIRECTION_BEARING
    from .route_sections import section_index
    out = pd.DataFrame({"curve_id": None, "reason": None, "station_id": None,
                        "station_direction": None, "station_miles": np.nan},
                       index=context.index)
    report: list[dict] = []
    if stations is None or not len(stations) or context.empty:
        out.attrs["stations"] = pd.DataFrame(report)
        return out
    ids = context.index.to_numpy()
    miles = context["miles"].fillna(0.0)
    by_seg = section_index(sections or ())
    usable = (~context["ramp"].to_numpy(bool)) & context["travel_bearing"].notna().to_numpy()
    best = pd.Series(np.inf, index=context.index)
    for st in stations.itertuples(index=False):
        row = {"station_id": st.station_id, "direction": st.direction,
               "route": st.route, "curve_id": st.curve_id, "snapped_to": None,
               "snap_miles": np.nan, "section_id": None, "section_from": None,
               "section_to": None, "section_miles": np.nan, "n_segments": 0, "note": ""}
        report.append(row)
        if st.direction not in DIRECTION_BEARING:
            row["note"] = "two-way count: no direction to match"
            continue
        on_route = _on_station_route(context, st.route) & usable
        if not on_route.any():
            row["note"] = ("no route" if not str(st.route or "").strip()
                           else f"no segment on route {st.route}")
            continue
        heading = np.asarray(_angle_between(context["travel_bearing"],
                                            DIRECTION_BEARING[st.direction])
                             <= STATION_DIRECTION_TOL_DEG)
        dist_m, t = _point_to_chord(float(st.lat), float(st.lon), context["start_lat"],
                                    context["start_lon"], context["end_lat"],
                                    context["end_lon"])
        snap = np.where(on_route & heading, dist_m / 1609.344, np.inf)
        k = int(np.argmin(snap))
        if not snap[k] <= STATION_SNAP_MILES:
            row["note"] = (f"no segment on route {st.route} in its direction within "
                           f"{STATION_SNAP_MILES:g} mi")
            continue
        anchor = int(ids[k])
        row["snapped_to"], row["snap_miles"] = anchor, round(float(snap[k]), 3)
        sec = _station_section(anchor, st.route, by_seg)
        if sec is None:
            path = [anchor]
            row["note"] = "on no traced route section: covers its own segment only"
        else:
            path = [int(x) for x in sec.segment_ids if x in best.index]
            row["section_id"] = sec.section_id
            row["section_from"] = f"{sec.start.get('kind')}: {sec.start.get('detail')}"
            row["section_to"] = f"{sec.end.get('kind')}: {sec.end.get('detail')}"
            row["section_miles"] = sec.miles
        # Along-path distance from the station to each segment's near end.
        seg_miles = miles.reindex(path).fillna(0.0).to_numpy(float)
        starts = np.concatenate([[0.0], np.cumsum(seg_miles)[:-1]])
        a = path.index(anchor) if anchor in path else 0
        pos = starts[a] + float(t[k]) * seg_miles[a]
        ends = starts + seg_miles
        dist = np.where(pos < starts, starts - pos, np.where(pos > ends, pos - ends, 0.0))
        row["n_segments"] = len(path)
        curve = st.nearest_generic if use_generic else st.curve_id
        fitted = ("" if not use_generic
                  else f", nearest generic to {st.curve_id} ({st.misplaced_share:.0%} of "
                       "the day's volume misplaced)")
        where = f"section {sec.section_id}" if sec is not None else "its own segment"
        for sid, d in zip(path, dist):
            if d >= best.at[sid]:
                continue
            best.at[sid] = d
            out.loc[sid, ["curve_id", "station_id", "station_direction", "station_miles",
                          "reason"]] = [
                curve, st.station_id, st.direction, round(float(d), 2),
                f"{str(st.source).upper()} {st.station_id} {st.direction} on route "
                f"{st.route}, {d:.2f} mi along {where}{fitted}"]
    rep = pd.DataFrame(report)
    hit = out["station_id"].notna()
    won = out[hit].groupby(["station_id", "station_direction"]).size()
    won_mi = miles[hit].groupby([out.loc[hit, "station_id"],
                                 out.loc[hit, "station_direction"]]).sum()
    rep["n_assigned"] = [int(won.get((r.station_id, r.direction), 0))
                         for r in rep.itertuples()]
    rep["miles_assigned"] = [round(float(won_mi.get((r.station_id, r.direction), 0.0)), 2)
                             for r in rep.itertuples()]
    out.attrs["stations"] = rep
    return out


# ---------------------------------------------------------------------------
# Chains
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Chain:
    """One direction of a road, decided as a whole.

    ``opposite`` names the chain(s) running the other way; the inference reads their
    segments together as the opposite side. ``corridor`` / ``direction`` are set for
    a catalogue chain (the override key), None for a route run.
    """

    chain_id: str
    segment_ids: tuple[int, ...]
    opposite: tuple[str, ...] = ()
    kind: str = "route"                 # "catalogue" | "route"
    corridor: str | None = None
    direction: str | None = None


def catalogue_chains(entries: Iterable, segment_ids: Mapping[str, Sequence[int]]
                     ) -> list[Chain]:
    """One :class:`Chain` per catalogue entry with resolved segments.

    Args:
        entries: ``corridors.CorridorEntry`` (anything with ``id``, ``corridor``,
            ``direction``).
        segment_ids: ``entry id -> segment ids`` — the resolved chain's
            ``segment_ids``, or the catalogue's ``_segment_ids``.

    Two entries oppose when they belong to the same reporting ``corridor`` and their
    directions' signs (:func:`geometry.direction_sign`) are opposite. An entry with
    no ``corridor`` or no opposite is unpaired: it gets a chain, which never infers.
    """
    entries = [e for e in entries if e.id in segment_ids and len(segment_ids[e.id])]
    out = []
    for e in entries:
        sign = direction_sign(e.direction)
        opp = tuple(o.id for o in entries
                    if e.corridor and o.corridor == e.corridor and o.id != e.id
                    and sign != 0 and direction_sign(o.direction) == -sign)
        out.append(Chain(chain_id=e.id,
                         segment_ids=tuple(int(s) for s in segment_ids[e.id]),
                         opposite=opp, kind="catalogue", corridor=e.corridor,
                         direction=e.direction))
    return out


def route_runs(context: pd.DataFrame) -> list[Chain]:
    """Route-membership runs: the segments of one route, one travel sign, one urban
    area, one zone and one radial sense.

    The zone and radial split keep a run local: I-84 eastbound west of Boise is
    inbound and east of it outbound, and one decision for both would be wrong for
    one. The opposite run is the same route, urban area and zone, the other sign,
    and the mirror radial (inbound ↔ outbound). Ramps and segments with no route or
    no sign are in no run.
    """
    ctx = context[context["route"].notna() & (context["dir_sign"] != 0) & ~context["ramp"]]
    if ctx.empty:
        return []
    mirror = {INBOUND: OUTBOUND, OUTBOUND: INBOUND}
    key = pd.DataFrame({
        "route": ctx["route"],
        "sign": ctx["dir_sign"],
        "uace": ctx["urban_uace"].fillna("-"),
        "zone": ctx["zone"].fillna("-"),
        "radial": ctx["radial"].fillna("-"),
    }, index=ctx.index)
    groups = {k: tuple(int(s) for s in g.index)
              for k, g in key.groupby(list(key.columns), sort=True)}

    def _id(k):
        route, sign, uace, zone, radial = k
        return (f"route:{route}{'+' if sign > 0 else '-'}:{uace}:{zone}"
                + ("" if radial == "-" else f":{radial}"))

    out = []
    for k, ids in groups.items():
        route, sign, uace, zone, radial = k
        ok = (route, -sign, uace, zone, mirror.get(radial, radial))
        out.append(Chain(chain_id=_id(k), segment_ids=ids,
                         opposite=(_id(ok),) if ok in groups else (), kind="route"))
    return out


# ---------------------------------------------------------------------------
# Inference
# ---------------------------------------------------------------------------
def _side(ids, delay: pd.DataFrame, miles: pd.Series) -> tuple[float, float]:
    """``(am_share, worse-peak delay per observed mile)`` over a set of segments."""
    d = delay.reindex(list(ids))
    obs = d["am"].notna() & d["pm"].notna()
    am, pm = float(d.loc[obs, "am"].sum()), float(d.loc[obs, "pm"].sum())
    mi = float(miles.reindex(d.index[obs]).fillna(0).sum())
    share = am / (am + pm) if am + pm > 0 else float("nan")
    per_mile = max(am, pm) / mi if mi > 0 else float("nan")
    return share, per_mile


def infer_orientation(chains: Sequence[Chain], delay: pd.DataFrame,
                      miles: pd.Series) -> pd.DataFrame:
    """The inference, one row per chain.

    Args:
        chains: :func:`catalogue_chains` and/or :func:`route_runs`.
        delay: floored mean delay (minutes per vehicle) per segment in the ``am`` and
            ``pm`` windows (``screen.window_delay``), indexed by segment id. A
            segment's delay already scales with its length, so a side's sum is its
            length-weighted total.
        miles: segment length, indexed by segment id.

    Returns:
        Indexed by ``chain_id``: ``am_share_self``, ``am_share_opposite``,
        ``delay_per_mile_self``, ``delay_per_mile_opposite`` (the worse peak, per
        observed mile), ``curve_id`` (``am_commute_urban`` / ``pm_commute_urban``, or
        None when the chain falls through) and ``reason``.
    """
    by_id = {c.chain_id: c for c in chains}
    rows = []
    for c in chains:
        self_share, self_dpm = _side(c.segment_ids, delay, miles)
        opp_ids = [s for o in c.opposite if o in by_id for s in by_id[o].segment_ids]
        opp_share, opp_dpm = (_side(opp_ids, delay, miles) if opp_ids
                              else (float("nan"), float("nan")))
        curve, reason = None, None
        if not opp_ids:
            reason = "unpaired (no opposing chain)"
        elif not (self_dpm >= MIN_PEAK_DELAY_PER_MILE
                  and opp_dpm >= MIN_PEAK_DELAY_PER_MILE):
            reason = (f"below the delay floor ({_fmt(self_dpm)} / {_fmt(opp_dpm)} "
                      f"min/mi, needs {MIN_PEAK_DELAY_PER_MILE})")
        elif self_share >= AM_SHARE_COMMUTE and opp_share <= AM_SHARE_OPPOSITE:
            curve = AM_CURVE
        elif self_share <= AM_SHARE_OPPOSITE and opp_share >= AM_SHARE_COMMUTE:
            curve = PM_CURVE
        elif self_share > AM_SHARE_OPPOSITE and self_share < AM_SHARE_COMMUTE:
            reason = f"both peaks (am_share {self_share:.2f})"
        else:
            reason = ("not opposed (am_share "
                      f"{self_share:.2f} vs {opp_share:.2f})")
        if curve is not None:
            reason = (f"{'AM' if curve == AM_CURVE else 'PM'}-commute side: am_share "
                      f"{self_share:.2f} vs opposite {opp_share:.2f}")
        rows.append({"chain_id": c.chain_id, "am_share_self": _r(self_share),
                     "am_share_opposite": _r(opp_share),
                     "delay_per_mile_self": _r(self_dpm),
                     "delay_per_mile_opposite": _r(opp_dpm),
                     "curve_id": curve, "reason": reason})
    cols = ["chain_id", "am_share_self", "am_share_opposite", "delay_per_mile_self",
            "delay_per_mile_opposite", "curve_id", "reason"]
    return pd.DataFrame(rows, columns=cols).set_index("chain_id")


def _r(x: float) -> float:
    return round(x, 3) if not math.isnan(x) else float("nan")


def _fmt(x: float) -> str:
    return "n/a" if math.isnan(x) else f"{x:.3f}"


# ---------------------------------------------------------------------------
# Overrides
# ---------------------------------------------------------------------------
OVERRIDE_COLUMNS = ("xd_seg_id", "district", "corridor", "direction", "curve_id", "note")


def load_overrides(path, profiles: Mapping | None = None) -> pd.DataFrame:
    """The owner-reviewed override table (``#`` lines are comments).

    Columns :data:`OVERRIDE_COLUMNS`. A row is keyed on ``xd_seg_id`` **or** on
    ``corridor`` + ``direction`` (a catalogue entry's reporting corridor and its
    ``EB``/``WB``/``NB``/``SB``), never both. ``district`` blank = any district —
    corridor ids repeat across districts (``i84``). Every row needs a note.

    Raises:
        ValueError: a missing column, a row keyed both ways or neither, an unknown
            ``curve_id`` (when ``profiles`` is given), or a row without a note.
    """
    frame = pd.read_csv(path, dtype=str, comment="#").fillna("")
    missing = [c for c in OVERRIDE_COLUMNS if c not in frame.columns]
    if missing:
        raise ValueError(f"{path}: override table lacks columns {missing}")
    for col in OVERRIDE_COLUMNS:
        frame[col] = frame[col].str.strip()
    seg = frame["xd_seg_id"] != ""
    cor = (frame["corridor"] != "") | (frame["direction"] != "")
    bad = frame[seg == cor]
    if len(bad):
        raise ValueError(f"{path}: each row is keyed on xd_seg_id or on corridor + "
                         f"direction, not both or neither: rows {list(bad.index)}")
    bad = frame[cor & ((frame["corridor"] == "") | (frame["direction"] == ""))]
    if len(bad):
        raise ValueError(f"{path}: a corridor row needs both corridor and direction: "
                         f"rows {list(bad.index)}")
    if (~frame.loc[seg, "xd_seg_id"].str.isdigit()).any():
        raise ValueError(f"{path}: xd_seg_id must be an integer")
    if ((frame["district"] != "") & ~frame["district"].str.isdigit()).any():
        raise ValueError(f"{path}: district must be a number or blank")
    if profiles is not None:
        unknown = sorted(set(frame["curve_id"]) - set(profiles))
        if unknown:
            raise ValueError(f"{path}: unknown curve_id {unknown}; the library has "
                             f"{sorted(profiles)}")
    if (frame["note"] == "").any():
        raise ValueError(f"{path}: every override needs a note saying why")
    return frame[list(OVERRIDE_COLUMNS)].reset_index(drop=True)


def _override_map(overrides, chains, district) -> dict[int, tuple[str, str, str | None]]:
    """``segment -> (curve_id, reason, chain_id)``; segment rows beat corridor rows."""
    if overrides is None or not len(overrides):
        return {}
    ov = overrides
    if district is not None:
        ov = ov[(ov["district"] == "") | (ov["district"] == str(district))]
    out: dict = {}
    for r in ov[ov["corridor"] != ""].itertuples():
        for c in chains:
            if (c.kind == "catalogue" and c.corridor == r.corridor
                    and str(c.direction or "").upper() == r.direction.upper()):
                for s in c.segment_ids:
                    out[s] = (r.curve_id, f"corridor {r.corridor} {r.direction}: {r.note}",
                              c.chain_id)
    for r in ov[ov["xd_seg_id"] != ""].itertuples():
        out[int(r.xd_seg_id)] = (r.curve_id, f"segment: {r.note}", None)
    return out


# ---------------------------------------------------------------------------
# Assignment
# ---------------------------------------------------------------------------
def assign_profiles(context: pd.DataFrame, chains: Sequence[Chain] = (),
                    delay: pd.DataFrame | None = None, *, overrides=None,
                    district: int | None = None,
                    profiles: Mapping | None = None,
                    stations: pd.DataFrame | None = None,
                    sections: Sequence | None = None,
                    station_generic: bool = False) -> pd.DataFrame:
    """One curve per segment of ``context``.

    Args:
        context: :func:`segment_context` — its index is the set of segments assigned.
        chains: :func:`catalogue_chains` + :func:`route_runs`.
        delay: the ``am`` / ``pm`` delay frame :func:`infer_orientation` reads;
            ``None`` skips the inference.
        overrides: :func:`load_overrides`.
        district: filters the overrides' ``district`` column.
        profiles: the curve library; when given, every assigned ``curve_id`` must be
            in it (with ``stations``, that includes the fitted curves).
        stations: count stations with fitted curves (``counts.station_curves``) for
            :func:`station_rule`; ``None`` skips it.
        sections: ``route_sections.trace_sections`` — how far each station reaches;
            ``None`` = each covers only the segment it snaps to.
        station_generic: the station rule assigns each station's nearest generic
            curve instead of its fitted one.

    Returns:
        Indexed by ``XDSegID``, columns :data:`ASSIGNMENT_COLUMNS`. ``am_share_self`` /
        ``am_share_opposite`` / ``chain_id`` come from the chain that decided, or,
        when the inference fell through, from the segment's first chain, so the
        reason can be audited. ``attrs['profile_assignment']`` holds the counts by
        source and curve, the thresholds, and the per-chain inference table.
    """
    idx = context.index
    inference = (infer_orientation(chains, delay, context["miles"])
                 if delay is not None and len(chains) else None)

    # Each segment's chains, catalogue first and the shortest first.
    order = sorted(chains, key=lambda c: (c.kind != "catalogue", len(c.segment_ids),
                                          c.chain_id))
    seg_chains: dict[int, list[str]] = {}
    for c in order:
        for s in c.segment_ids:
            seg_chains.setdefault(int(s), []).append(c.chain_id)

    rule = urban_rule(context)
    counted = station_rule(context, stations, sections=sections,
                           use_generic=station_generic)
    forced = _override_map(overrides, chains, district)

    rows = []
    for sid in idx:
        sid = int(sid)
        mine = seg_chains.get(sid, [])
        decided = None
        if inference is not None:
            decided = next((cid for cid in mine
                            if inference.at[cid, "curve_id"] is not None), None)
        shown = decided or (mine[0] if mine else None)
        am_self = am_opp = float("nan")
        if inference is not None and shown is not None:
            am_self = inference.at[shown, "am_share_self"]
            am_opp = inference.at[shown, "am_share_opposite"]
        fell = (f" (inference: {inference.at[shown, 'reason']})"
                if inference is not None and shown is not None and decided is None
                else "")
        if sid in forced:
            curve, why, ocid = forced[sid]
            rows.append((curve, OVERRIDE, am_self, am_opp, ocid or shown, why))
        elif counted.at[sid, "curve_id"] is not None:
            rows.append((counted.at[sid, "curve_id"], STATION, am_self, am_opp, shown,
                         counted.at[sid, "reason"]))
        elif decided is not None:
            rows.append((inference.at[decided, "curve_id"], INFERRED, am_self, am_opp,
                         decided, inference.at[decided, "reason"]))
        elif rule.at[sid, "curve_id"] is not None:
            rows.append((rule.at[sid, "curve_id"], URBAN_RULE, am_self, am_opp, shown,
                         rule.at[sid, "reason"] + fell))
        else:
            rows.append((DEFAULT_CURVE, DEFAULT, am_self, am_opp, shown,
                         rule.at[sid, "reason"] + fell))
    out = pd.DataFrame(rows, index=idx, columns=list(ASSIGNMENT_COLUMNS))
    out.index.name = "XDSegID"

    if profiles is not None:
        unknown = sorted(set(out["curve_id"]) - set(profiles))
        if unknown:
            raise ValueError(f"assigned curve_id {unknown} is not in the library")

    out.attrs["profile_assignment"] = {
        "by_source": {k: int(v) for k, v in out["curve_source"].value_counts().items()},
        "by_curve": {k: int(v) for k, v in out["curve_id"].value_counts().items()},
        "n_chains": len(chains),
        "n_chains_inferred": (0 if inference is None
                              else int(inference["curve_id"].notna().sum())),
        "n_stations": 0 if stations is None else int(len(stations)),
        "n_sections": 0 if sections is None else len(sections),
        "station_curves": "nearest generic" if station_generic else "fitted",
        "thresholds": {
            "am_share_commute": AM_SHARE_COMMUTE,
            "am_share_opposite": AM_SHARE_OPPOSITE,
            "min_peak_delay_per_mile": MIN_PEAK_DELAY_PER_MILE,
            "urban_commute_min_pop": URBAN_COMMUTE_MIN_POP,
            "urban_approach_m": URBAN_APPROACH_M,
            "urban_core_radius_m": URBAN_CORE_RADIUS_M,
            "radial_tol_deg": RADIAL_TOL_DEG,
            "station_reach": "route section (Item 61)",
            "station_snap_miles": STATION_SNAP_MILES,
            "station_direction_tol_deg": STATION_DIRECTION_TOL_DEG,
        },
    }
    out.attrs["inference"] = inference
    out.attrs["stations"] = counted.attrs["stations"]
    return out


def summary_line(assignment: pd.DataFrame) -> str:
    """``inferred 812, urban_rule 14,901, …`` — the per-district log line."""
    counts = assignment["curve_source"].value_counts()
    return ", ".join(f"{s} {int(counts.get(s, 0)):,}" for s in CURVE_SOURCES)


# ---------------------------------------------------------------------------
# I/O (the routes.write_membership / read_membership pattern)
# ---------------------------------------------------------------------------
CURVES_SUFFIX = "_curves.json"
"""The companion library beside an assignment CSV: the curves it uses that are not
packaged (the count-fitted ones, Item 59), so a reader never meets an unknown id."""


def curves_path(path) -> Path:
    """``d3_volume_profiles.csv`` → ``d3_volume_profiles_curves.json``."""
    path = Path(path)
    return path.with_name(path.stem + CURVES_SUFFIX)


def write_assignment(assignment: pd.DataFrame, path, profiles: Mapping | None = None
                     ) -> Path:
    """Write an assignment frame as CSV (the index as ``XDSegID``).

    With ``profiles`` (the library the assignment was made from), the curves it uses
    that are not in the packaged library are written beside it (:func:`curves_path`);
    a stale companion from an earlier run is removed when there are none.
    """
    from . import volume_profiles
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    assignment.rename_axis("XDSegID").to_csv(path)
    if profiles is not None:
        packaged = volume_profiles.load_profiles()
        extra = {c: profiles.get(c) for c in sorted(set(assignment["curve_id"].dropna()))
                 if c not in packaged}
        missing = [c for c in extra if extra[c] is None]
        if missing:
            raise ValueError(f"curves {missing} are not in the library given")
        companion = curves_path(path)
        if extra:
            volume_profiles.write_profiles(
                extra, companion, note=f"The non-packaged curves {path.name} assigns "
                                       "(ROADMAP Item 59).")
        elif companion.exists():
            companion.unlink()
    return path


def assignment_profiles(path) -> dict:
    """The library an assignment CSV reads against: the packaged curves plus its
    companion (:func:`curves_path`), when there is one."""
    from . import volume_profiles
    library = volume_profiles.load_profiles()
    companion = curves_path(path)
    if companion.exists():
        library = volume_profiles.merge_profiles(
            library, volume_profiles.load_profiles(companion))
    return library


def read_assignment(path) -> pd.DataFrame:
    """Read :func:`write_assignment` output back, indexed by ``XDSegID`` (int)."""
    frame = pd.read_csv(path, dtype={"curve_id": str, "curve_source": str,
                                     "chain_id": str, "reason": str})
    frame["chain_id"] = frame["chain_id"].where(frame["chain_id"].notna(), None)
    frame = frame.set_index(frame["XDSegID"].astype("int64")).drop(columns="XDSegID")
    return frame[list(ASSIGNMENT_COLUMNS)]

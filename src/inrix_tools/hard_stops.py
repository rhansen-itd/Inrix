"""Manual hard stops for corridor stitching.  (ROADMAP Item 60)

The Item 51 chain walk stitches a route across junctions and renumberings, which is
what it is for, but sometimes the owner reads a corridor as ending where the walk runs
on: Eagle Rd (SH-55) ends at SH-44 and does not carry on up SH-55 north; Boise's US-20
is Broadway | the Front/Myrtle couplet | the Connector. These are **logical termini**
(the FHWA NEPA sense) or real context changes, and no screening signal finds them.

A hard stop is an owner-authored row in ``scripts/corridor_hard_stops.csv``. It is
**input only**: :func:`load_hard_stops` resolves each row to a segment **boundary**
``(from_seg, to_seg)`` on the route, and ``extents.enumerate_mainline_chains`` cuts at
it — no link, junction join or renumbering merge crosses a boundary. The screening
itself (cores, tiers, floors) is unchanged.

Rows name the place in one of two ways:

* ``lat, lon`` — a point near the boundary, which is what an owner can author from a
  map. It resolves to the route boundary nearest the point, within
  :data:`STOP_SNAP_M`, with every other at the same place (:data:`STOP_TIE_M`). A
  ``direction`` limits it to boundaries whose segments run that way; a blank direction
  takes the nearest boundary **and** the nearest one running the other way (both
  carriageways of a divided road, or both legs of a couplet end that meet at one
  point).
* ``xd_seg_id, side`` — every route boundary ``before`` / ``after`` that segment (a
  direction narrows it), for a boundary a point cannot pick out.

A stop cuts **every** way the route leaves the boundary's segment there. Where the walk
had turned off the link and dropped the link's continuation as a stub (Item 51), cutting
only the turn would hand the walk the stub: SH-8 eastbound leaves 3rd St at Jackson St
both down the couplet and, by the link, on along the 3rd St block to Washington St.

A row that resolves to nothing is an error naming the row, never silently dropped.

An optional ``scope`` column (Item 61) says what a stop cuts: ``both`` (blank), the
corridor ``corridors`` only, or a count station's reach (``stations``,
``route_sections``) only. :func:`stop_boundaries` keeps the rows for one use.

The boundaries a row may resolve to are every consecutive pair on the route's chains
(:func:`route_boundaries`) plus every ``NextXDSegI`` link between two of its segments.
A pair belongs to route ``r`` when **both** segments carry ``r`` (so a stop on US-95
does not also cut SH-8 where SH-8 turns onto the couplet at the same corner).
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd

HARD_STOP_COLUMNS = ("district", "route", "direction", "lat", "lon", "xd_seg_id", "side",
                     "note")
"""The columns of ``corridor_hard_stops.csv`` (``#`` lines are comments)."""

SCOPE_COLUMN = "scope"
"""Optional column (Item 61): what a stop cuts. Blank or ``both`` = corridor chains and
count-station sections; ``corridors`` = the chains only; ``stations`` = only a count
station's reach (``route_sections``), e.g. to keep Eagle Rd's station curve off its
south end without ending a corridor there."""
SCOPES = ("both", "corridors", "stations")

RESOLVED_COLUMNS = ("row", "district", "route", "direction", "from_seg", "to_seg",
                    "on_chain", "distance_m", "at_lat", "at_lon", "from_name", "to_name",
                    "note", "scope")
"""One row per resolved boundary; ``row`` is the table row (1-based, data rows only).
``on_chain`` is ``False`` for a link the stop-free walk had already left (a stub)."""

STOP_SNAP_M = 100.0
"""A point must lie this close to the boundary it resolves to (metres)."""

STOP_TIE_M = 5.0
"""Boundaries within this of the nearest are at the same place, and a point takes them
all. That is usually one segment's end where the route leaves two ways: at Front St and
the Connector the chain turns onto I-184 W while the link runs on down a 0.15-mi stub of
W Front St, and cutting only the turn hands the walk the stub instead."""

DIRECTIONS = {"NB": "N", "SB": "S", "EB": "E", "WB": "W",
              "N": "N", "S": "S", "E": "E", "W": "W"}
_OPPOSITE = {"N": "S", "S": "N", "E": "W", "W": "E"}
SIDES = ("before", "after")


class HardStopError(ValueError):
    """A hard-stop row is malformed or resolves to no boundary."""


@dataclass(frozen=True)
class Boundary:
    """A place between two consecutive segments of a route."""
    from_seg: int
    to_seg: int
    bearings: frozenset[str]
    """Cardinal bearings of the two segments (``{"N", "E"}`` where a road turns)."""
    x: float
    y: float
    """Where ``from_seg`` ends, in the metric CRS."""
    on_chain: bool = True
    """Consecutive on a chain (``False``: only a ``NextXDSegI`` link the walk left)."""


def read_hard_stops(path) -> pd.DataFrame:
    """The hard-stop table, validated but not resolved.

    Columns :data:`HARD_STOP_COLUMNS` plus ``row`` (1-based over data rows). ``district``
    and ``xd_seg_id`` come back as nullable ints, ``lat`` / ``lon`` as floats, and
    ``direction`` normalised to ``N`` / ``S`` / ``E`` / ``W`` or ``""``.

    Raises:
        HardStopError: a missing column; a row without a note, district or route; a
            direction or side that is not one of the allowed values; a row giving both
            or neither of ``lat, lon`` and ``xd_seg_id``; a lat/lon out of range.
    """
    frame = pd.read_csv(path, dtype=str, comment="#").fillna("")
    missing = [c for c in HARD_STOP_COLUMNS if c not in frame.columns]
    if missing:
        raise HardStopError(f"{path}: hard-stop table lacks columns {missing}")
    if SCOPE_COLUMN not in frame.columns:
        frame[SCOPE_COLUMN] = ""
    for col in (*HARD_STOP_COLUMNS, SCOPE_COLUMN):
        frame[col] = frame[col].str.strip()
    frame.insert(0, "row", np.arange(1, len(frame) + 1))

    def _bad(mask, why: str) -> None:
        if mask.any():
            rows = frame.loc[mask, "row"].tolist()
            raise HardStopError(f"{path}: row(s) {rows}: {why}")

    _bad(frame["note"] == "", "every hard stop needs a note saying why it is a terminus")
    _bad(~frame["district"].str.fullmatch(r"[1-6]"), "district must be 1-6")
    frame["route"] = frame["route"].str.lstrip("0")
    _bad(~frame["route"].str.fullmatch(r"\d+"), "route must be a route number (55, not SH-55)")
    dirs = frame["direction"].str.upper()
    _bad(~dirs.isin(list(DIRECTIONS) + [""]), "direction must be NB/SB/EB/WB or blank")
    frame["direction"] = dirs.map(lambda d: DIRECTIONS.get(d, ""))
    has_pt = (frame["lat"] != "") | (frame["lon"] != "")
    has_seg = frame["xd_seg_id"] != ""
    _bad(has_pt & has_seg, "give lat/lon or xd_seg_id + side, not both")
    _bad(~has_pt & ~has_seg, "give lat/lon or xd_seg_id + side")
    lat = pd.to_numeric(frame["lat"].where(has_pt), errors="coerce")
    lon = pd.to_numeric(frame["lon"].where(has_pt), errors="coerce")
    _bad(has_pt & ~(lat.between(-90, 90) & lon.between(-180, 180)),
         "lat/lon missing or out of range (the order is lat, lon)")
    _bad(has_seg & ~frame["xd_seg_id"].str.fullmatch(r"\d+"), "xd_seg_id must be an integer")
    _bad(has_seg & ~frame["side"].str.lower().isin(SIDES), "side must be before or after")
    _bad(~has_seg & (frame["side"] != ""), "side goes with xd_seg_id")
    frame["side"] = frame["side"].str.lower()
    scope = frame[SCOPE_COLUMN].str.lower().replace("", "both")
    _bad(~scope.isin(SCOPES), f"scope must be one of {list(SCOPES)} or blank")
    frame[SCOPE_COLUMN] = scope
    frame["district"] = frame["district"].astype(int)
    frame["lat"], frame["lon"] = lat, lon
    frame["xd_seg_id"] = pd.to_numeric(frame["xd_seg_id"].where(has_seg),
                                       errors="coerce").astype("Int64")
    return frame[["row", *HARD_STOP_COLUMNS, SCOPE_COLUMN]].reset_index(drop=True)


def _cardinal(bearing) -> str:
    b = str(bearing).strip().upper()[:1]
    return b if b in _OPPOSITE else ""


def route_boundaries(network: gpd.GeoDataFrame, route: str, *,
                     chains=None, metric_crs=None) -> list[Boundary]:
    """Every boundary on ``route``: consecutive pairs on the chains that walk it, and
    ``NextXDSegI`` links between two of its segments, where both segments carry it.

    ``chains`` defaults to ``extents.enumerate_mainline_chains(network, min_miles=0)``
    (no stops applied); pass them in to resolve many rows against one walk.
    """
    from . import extents

    net_idx = network.set_index("XDSegID", drop=False) if "XDSegID" in network.columns \
        and network.index.name != "XDSegID" else network
    sets = extents.segment_route_sets(network)
    on = {s for s, r in sets.items() if route in r and s in net_idx.index}
    if not on:
        return []
    if chains is None:
        chains = extents.enumerate_mainline_chains(network, min_miles=0.0)
    pairs: set[tuple[int, int]] = set()
    for c in chains:
        ids = c.segment_ids
        pairs.update((a, b) for a, b in zip(ids, ids[1:]) if a in on and b in on)
    chained = set(pairs)
    nxt = pd.to_numeric(net_idx.get("NextXDSegI", pd.Series(dtype=float)), errors="coerce")
    for a in on:
        b = nxt.get(a)
        if b is not None and pd.notna(b) and int(b) in on and int(b) != a:
            pairs.add((a, int(b)))
    if not pairs:
        return []
    if metric_crs is None:
        try:
            metric_crs = network.estimate_utm_crs()
        except Exception:
            metric_crs = "EPSG:3857"
    froms = sorted({a for a, _ in pairs})
    geo = gpd.GeoSeries(net_idx.loc[froms, "geometry"].values, index=froms,
                        crs=network.crs).to_crs(metric_crs)
    end = {s: g.interpolate(g.length) for s, g in geo.items() if g is not None and not g.is_empty}
    brg = net_idx["Bearing"] if "Bearing" in net_idx.columns else pd.Series(dtype=object)
    out = []
    for a, b in sorted(pairs):
        if a not in end:
            continue
        bearings = frozenset(x for x in (_cardinal(brg.get(a)), _cardinal(brg.get(b))) if x)
        out.append(Boundary(a, b, bearings, float(end[a].x), float(end[a].y),
                            on_chain=(a, b) in chained))
    return out


def _resolve_point(row, boundaries: list[Boundary], xy: tuple[float, float],
                   snap_m: float) -> list[tuple[Boundary, float]]:
    def _dist(bd: Boundary) -> float:
        return float(np.hypot(bd.x - xy[0], bd.y - xy[1]))

    def _group(cands):
        # The nearest boundary and any at the same place.
        return [t for t in cands if t[1] <= cands[0][1] + STOP_TIE_M] if cands else []

    near = sorted(((bd, _dist(bd)) for bd in boundaries), key=lambda t: t[1])
    near = [t for t in near if t[1] <= snap_m]
    if row["direction"]:
        return _group([t for t in near if row["direction"] in t[0].bearings])
    first = _group(near)
    if not first:
        return []
    ways = frozenset().union(*(t[0].bearings for t in first))
    opposite = {_OPPOSITE[d] for d in ways}
    taken = {(t[0].from_seg, t[0].to_seg) for t in first}
    back = _group([t for t in near if (t[0].from_seg, t[0].to_seg) not in taken
                   and not (t[0].bearings & ways) and t[0].bearings & opposite])
    return first + back


def resolve_hard_stops(stops: pd.DataFrame, network: gpd.GeoDataFrame, district: int, *,
                       snap_m: float = STOP_SNAP_M) -> pd.DataFrame:
    """Resolve the ``district``'s rows of ``stops`` (:func:`read_hard_stops`) on
    ``network`` (link repairs and route membership applied, as the catalogue builder
    loads it).

    Returns:
        :data:`RESOLVED_COLUMNS`, one row per boundary (a blank-direction row can give
        two). ``at_lat`` / ``at_lon`` is where the boundary lies.

    Raises:
        HardStopError: listing every row of the district that resolved to no boundary.
    """
    from . import extents

    rows = stops[stops["district"] == int(district)]
    if rows.empty:
        return pd.DataFrame(columns=list(RESOLVED_COLUMNS))
    try:
        metric_crs = network.estimate_utm_crs()
    except Exception:
        metric_crs = "EPSG:3857"
    net_idx = network.set_index("XDSegID", drop=False) if network.index.name != "XDSegID" \
        else network
    chains = extents.enumerate_mainline_chains(network, min_miles=0.0)
    names = net_idx["RoadName"] if "RoadName" in net_idx.columns else pd.Series(dtype=object)
    by_route: dict[str, list[Boundary]] = {}
    out, failed = [], []
    for _, row in rows.iterrows():
        route = row["route"]
        if route not in by_route:
            by_route[route] = route_boundaries(network, route, chains=chains,
                                               metric_crs=metric_crs)
        bds = by_route[route]
        if pd.notna(row["xd_seg_id"]):
            seg = int(row["xd_seg_id"])
            hit = [(bd, 0.0) for bd in bds
                   if (bd.to_seg if row["side"] == "before" else bd.from_seg) == seg]
            if row["direction"]:
                hit = [t for t in hit if row["direction"] in t[0].bearings]
        else:
            pt = gpd.GeoSeries(gpd.points_from_xy([row["lon"]], [row["lat"]]),
                               crs="EPSG:4326").to_crs(metric_crs).iloc[0]
            hit = _resolve_point(row, bds, (pt.x, pt.y), snap_m)
        if not hit:
            failed.append(row)
            continue
        for bd, dist in hit:
            at = gpd.GeoSeries(gpd.points_from_xy([bd.x], [bd.y]), crs=metric_crs) \
                .to_crs("EPSG:4326").iloc[0]
            out.append({"row": int(row["row"]), "district": int(district), "route": route,
                        "direction": row["direction"], "from_seg": bd.from_seg,
                        "to_seg": bd.to_seg, "on_chain": bd.on_chain,
                        "distance_m": round(dist, 1),
                        "at_lat": round(at.y, 6), "at_lon": round(at.x, 6),
                        "from_name": names.get(bd.from_seg, ""),
                        "to_name": names.get(bd.to_seg, ""), "note": row["note"],
                        "scope": row.get(SCOPE_COLUMN, "both") or "both"})
    if failed:
        lines = [f"row {r['row']} (route {r['route']} {r['direction'] or 'both'}, "
                 + (f"xd {r['xd_seg_id']} {r['side']}" if pd.notna(r["xd_seg_id"])
                    else f"{r['lat']}, {r['lon']}") + f": {r['note']})" for r in failed]
        raise HardStopError(
            f"District {district}: {len(failed)} hard stop(s) resolve to no route boundary "
            f"(within {snap_m:g} m for a point):\n  " + "\n  ".join(lines))
    return pd.DataFrame(out, columns=list(RESOLVED_COLUMNS))


def load_hard_stops(path, network: gpd.GeoDataFrame, district: int, *,
                    snap_m: float = STOP_SNAP_M) -> pd.DataFrame:
    """:func:`read_hard_stops` then :func:`resolve_hard_stops` for one district.
    A missing file is an error: pass ``None`` upstream to run without stops."""
    if not Path(path).exists():
        raise HardStopError(f"{path}: no hard-stop table")
    return resolve_hard_stops(read_hard_stops(path), network, district, snap_m=snap_m)


def stop_boundaries(resolved: pd.DataFrame | None, use: str = "corridors"
                    ) -> dict[tuple[int, int], str]:
    """``{(from_seg, to_seg): reason}`` — the form ``extents`` and ``route_sections``
    take. The reason is ``"route R: note"``; two rows on one boundary join their notes.
    ``use`` is ``"corridors"`` (the chain walk) or ``"stations"`` (count-station
    sections): a row applies when its ``scope`` is ``both`` or that use."""
    out: dict[tuple[int, int], str] = {}
    if resolved is None or len(resolved) == 0:
        return out
    if use not in SCOPES[1:]:
        raise ValueError(f"use must be 'corridors' or 'stations', not {use!r}")
    if "scope" in resolved.columns:
        scope = resolved["scope"].fillna("both").replace("", "both")
        resolved = resolved[scope.isin(("both", use))]
    for r in resolved.itertuples(index=False):
        key = (int(r.from_seg), int(r.to_seg))
        text = f"route {r.route}: {r.note}"
        out[key] = text if key not in out or out[key] == text else f"{out[key]} | {text}"
    return out


def crossings(segment_ids, boundaries: dict[tuple[int, int], str]) -> list[dict]:
    """Every stop an ordered segment list steps across: ``[{index, from_seg, to_seg,
    reason}]``, ``index`` being the position of ``to_seg``. The audit of a catalogue
    entry against the stops."""
    ids = [int(s) for s in segment_ids]
    return [{"index": i + 1, "from_seg": a, "to_seg": b, "reason": boundaries[(a, b)]}
            for i, (a, b) in enumerate(zip(ids, ids[1:])) if (a, b) in boundaries]

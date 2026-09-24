"""Corridor chain assembly from the XD network — snap, walk, trim, account.
(ROADMAP Items 28 and 36)

A *chain* is the ordered run of XD segments between two query points (e.g. the
endpoints of an external travel-time route). Building one is three steps, and the
2026-09-17 outside pass (DESIGN_HISTORY Session 33, G7/G8) did only the first:

1. **Snap** each query point to a segment — in a **projected CRS**, never raw
   lat/lon degrees (a degree of longitude is ~28% shorter than a degree of
   latitude at this latitude, so a degree-space "nearest" can pick the wrong
   segment).
2. **Walk** ``NextXDSegI`` from the start segment until the end segment is
   reached, with an explicit :attr:`ChainResult.reached_target` flag and a
   :attr:`ChainResult.stop_reason` instead of a silently truncated chain.
3. **Trim + account** — report how much of the first/last segment lies *outside*
   the requested extent (whole end segments made the VSL chain 3.635 mi against a
   ~3.00 mi request, +21%), and which members have no observations in an export
   (three Eagle Rd NB members were absent and vanished into a silent fallback).

A **catalogue** (Item 36) is the same thing one level up: a JSON file of named
endpoint pairs — which corridors a district screen should rank and why each extent is
the meaningful one — loaded by :func:`load_catalogue` and put through the same three
steps by :func:`resolve_catalogue`, which accepts or rejects each entry explicitly.

Compute only — no plotting, no GUI, no file paths (see CLAUDE.md). Needs the
``geo`` extra for the geometry work (imports are function-local so the package
still imports without it); :func:`chain_coverage` and :func:`chain_travel_time`
operate on an already-built :class:`ChainResult` and need only pandas.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

import pandas as pd

from .io import (CORRIDOR_COL, CVALUE_COL, DATETIME_COL, IMPUTED_COL, SEGMENT_COL,
                 filter_cvalue, mark_imputed)
from .speed import corridor_travel_time, metric_columns

# Columns of the per-member frames (:meth:`ChainResult.frame`, :func:`chain_coverage`).
SEQ_COL = "sequence"                       # 1-based position along the chain
MILES_COL = "Miles"                        # whole-segment length (XD ``Miles``)
EXTENT_FRACTION_COL = "in_extent_fraction"  # fraction inside the requested extent
EXTENT_MILES_COL = "extent_miles"          # Miles * in_extent_fraction
OBSERVED_COL = "observed"                  # member has observations in the export
NOBS_COL = "n_obs"
NIMPUTED_COL = "n_imputed"            # rows with a null CValue (imputation marker)
IMPUTED_FRACTION_COL = "imputed_fraction"

CHAIN_LABEL = "Chain"                      # default synthetic corridor label
_FEET_PER_METRE = 3.280839895013123
_METRES_PER_MILE = 1609.344


# ---------------------------------------------------------------------------
# Typed result
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class ChainResult:
    """One assembled corridor chain, with everything needed to judge it.

    Lengths are **miles** and snap distances are **feet** (US traffic-engineering
    units, per CLAUDE.md). ``requested_miles`` is the distance between the two
    snapped query points measured *along the chain*; ``chain_miles`` is the sum of
    the whole member segments. Their ratio (:attr:`length_ratio`) is the overshoot
    the outside pass never normalised against.
    """

    segment_ids: tuple[int, ...]
    miles: tuple[float, ...]
    extent_fraction: tuple[float, ...]
    chain_miles: float
    requested_miles: float
    trim_start_miles: float
    trim_end_miles: float
    snap_start_feet: float
    snap_end_feet: float
    reached_target: bool
    stop_reason: str          # "target" | "dead_end" | "off_network" | "cycle" | "max_steps"
    target_segment: int       # the segment the end point snapped to
    crs: str                  # the projected CRS the snap was measured in
    repaired_links: tuple[tuple[int, int], ...] = ()   # links this chain owes to a patch

    @property
    def start_segment(self) -> int:
        return self.segment_ids[0]

    @property
    def end_segment(self) -> int:
        return self.segment_ids[-1]

    @property
    def n_segments(self) -> int:
        return len(self.segment_ids)

    @property
    def n_repaired_links(self) -> int:
        """How many of this chain's links came from a repair table rather than from
        ``NextXDSegI`` as published (Item 38). A chain that rests on a repair has to
        say so wherever it is reported — that is the whole price of repairing."""
        return len(self.repaired_links)

    @property
    def length_ratio(self) -> float:
        """``chain_miles / requested_miles`` — 1.0 when the chain matches the
        requested extent, >1 when whole end segments overshoot it (NaN if the
        requested extent is zero-length)."""
        if self.requested_miles <= 0:
            return float("nan")
        return self.chain_miles / self.requested_miles

    @property
    def extent_miles(self) -> tuple[float, ...]:
        return tuple(m * f for m, f in zip(self.miles, self.extent_fraction))

    def weights(self) -> dict[int, float]:
        """``Segment ID -> in-extent fraction`` — the proration weights (1.0 for
        every interior member; partial at the two ends)."""
        return dict(zip(self.segment_ids, self.extent_fraction))

    def frame(self) -> pd.DataFrame:
        """One row per member: ``[sequence, Segment ID, Miles, in_extent_fraction,
        extent_miles]``, in travel order. ``attrs`` carries the chain-level
        numbers (miles, trims, ratio, snaps, ``reached_target``)."""
        out = pd.DataFrame(
            {
                SEQ_COL: range(1, len(self.segment_ids) + 1),
                SEGMENT_COL: pd.Series(self.segment_ids, dtype="int64"),
                MILES_COL: self.miles,
                EXTENT_FRACTION_COL: self.extent_fraction,
                EXTENT_MILES_COL: self.extent_miles,
            }
        )
        out.attrs = self.summary()
        return out

    def summary(self) -> dict:
        """The chain-level numbers as a plain dict (what a report must not lose)."""
        return {
            "n_segments": self.n_segments,
            "chain_miles": self.chain_miles,
            "requested_miles": self.requested_miles,
            "trim_start_miles": self.trim_start_miles,
            "trim_end_miles": self.trim_end_miles,
            "length_ratio": self.length_ratio,
            "snap_start_feet": self.snap_start_feet,
            "snap_end_feet": self.snap_end_feet,
            "reached_target": self.reached_target,
            "stop_reason": self.stop_reason,
            "start_segment": self.start_segment,
            "end_segment": self.end_segment,
            "target_segment": self.target_segment,
            "crs": self.crs,
            "n_repaired_links": self.n_repaired_links,
            "repaired_links": self.repaired_links,
        }


# ---------------------------------------------------------------------------
# Projection + snapping
# ---------------------------------------------------------------------------
def _metres_per_unit(crs) -> float:
    """Metres per linear unit of ``crs`` (1.0 for a metric UTM zone, 0.3048… for
    a US-feet State Plane) so snap distances are real feet whatever CRS is used."""
    from pyproj import CRS

    axis = CRS.from_user_input(crs).axis_info
    factor = getattr(axis[0], "unit_conversion_factor", 1.0) if axis else 1.0
    return float(factor or 1.0)


def project_network(network, projected_crs=None):
    """Reproject an XD network (``geometry.load_xd_network``) into a projected CRS
    for metric work. ``projected_crs=None`` picks the network's own UTM zone
    (``estimate_utm_crs``) — the point of Item 28's first bullet: distances must be
    measured in metres, not degrees."""
    crs = projected_crs if projected_crs is not None else network.estimate_utm_crs()
    return network.to_crs(crs)


def _as_network(source, **load_kwargs):
    """Accept either an already-loaded GeoDataFrame or a shapefile/cache path."""
    if hasattr(source, "geometry") and hasattr(source, "crs"):
        return source
    from .geometry import load_xd_network

    return load_xd_network(source, **load_kwargs)


def _point_in(crs, latlon):
    """``(lat, lon)`` -> a shapely Point in ``crs``."""
    import geopandas as gpd
    from shapely.geometry import Point

    from .geometry import WGS84

    lat, lon = float(latlon[0]), float(latlon[1])
    return gpd.GeoSeries([Point(lon, lat)], crs=WGS84).to_crs(crs).iloc[0]


def snap_candidates(proj_net, latlon, k: int = 4, candidates=None) -> list[tuple[int, float, float]]:
    """The ``k`` nearest segments to a ``(lat, lon)`` query point, measured in the
    **projected** CRS of ``proj_net``.

    Returns ``(Segment ID, snap distance in feet, fraction along the segment)``
    tuples, nearest first. ``candidates`` restricts the search to those segment ids
    (e.g. one direction of a divided road).
    """
    net = proj_net.reset_index(drop=True)   # positional lookups, duplicate-index safe
    if candidates is not None:
        ids = [int(c) for c in candidates]
        net = net[net["XDSegID"].isin(ids)]
    if net.empty:
        return []
    pt = _point_in(net.crs, latlon)
    m_per_unit = _metres_per_unit(net.crs)
    dist = net.geometry.distance(pt)
    order = dist.nsmallest(min(k, len(dist))).index
    out = []
    for idx in order:
        geom = net.geometry.loc[idx]
        frac = float(geom.project(pt, normalized=True)) if geom.length > 0 else 0.0
        out.append(
            (
                int(net["XDSegID"].loc[idx]),
                float(dist.loc[idx]) * m_per_unit * _FEET_PER_METRE,
                min(max(frac, 0.0), 1.0),
            )
        )
    return out


# ---------------------------------------------------------------------------
# The walk
# ---------------------------------------------------------------------------
def _next_map(network) -> dict[int, int | None]:
    nxt = {}
    for sid, nid in zip(network["XDSegID"], network["NextXDSegI"]):
        if pd.isna(sid):
            continue
        nxt[int(sid)] = None if pd.isna(nid) else int(nid)
    return nxt


def walk_chain(network, start_segment: int, target_segment: int,
               max_steps: int = 500) -> tuple[list[int], str]:
    """Walk ``NextXDSegI`` from ``start_segment`` until ``target_segment``.

    Returns ``(ordered segment ids, stop_reason)`` where ``stop_reason`` is
    ``"target"`` (reached it), ``"dead_end"`` (the network ends), ``"off_network"``
    (the next id isn't in this network subset — widen the subset), ``"cycle"``, or
    ``"max_steps"``. The reason is returned, never raised: a chain that didn't
    reach its target is a *finding*, not an error (G7/G8).
    """
    nxt = _next_map(network)
    start, target = int(start_segment), int(target_segment)
    if start not in nxt:
        return [], "off_network"
    chain = [start]
    if start == target:
        return chain, "target"
    seen = {start}
    for _ in range(max_steps):
        nid = nxt.get(chain[-1])
        if nid is None:
            return chain, "dead_end"
        if nid not in nxt:
            return chain, "off_network"
        if nid in seen:
            return chain, "cycle"
        chain.append(nid)
        seen.add(nid)
        if nid == target:
            return chain, "target"
    return chain, "max_steps"


# ---------------------------------------------------------------------------
# Topology repair  (Item 38)
# ---------------------------------------------------------------------------
#
# ``NextXDSegI`` is incomplete, and on the D3 subset badly so: 8,499 of 16,105
# segments (52.8%) carry a null link, and a further handful carry a link that
# points off the carriageway entirely. Item 36 recorded the resulting broken
# corridors as findings rather than bridging them, which was right — what it
# refused was a *geographic sort*, inventing an order the network does not
# assert, and that is what put the Garrity Blvd frontage road in series with
# I-84.
#
# Repairing a link is a different act from inventing an order, and the difference
# is what these functions are built to keep:
#
# - the two geometries must physically **touch** (end point to start point,
#   within ``radius_m``),
# - the continuation must stay inside the segment's own **``XDGroup``** — XD's
#   carriageway key, which ramps, opposing directions and parallel facilities do
#   not share,
# - the junction must not double back (``max_bearing_delta_deg``, measured on the
#   *local* geometry at the junction rather than the segment chord),
# - both sides must carry the **same cardinal ``Bearing``** — a repair may not
#   change the carriageway's stated direction of travel, and may not run through a
#   rotary (``Bearing`` ``O``), where there is no direction to preserve,
# - **exactly one** candidate must qualify; two candidates is an ambiguity, and an
#   ambiguity stays a break,
# - and a link that leaves the *subset* is not touched at all: that is the edge of
#   the extract, not a defect, and ``walk_chain`` already calls it ``off_network``,
# - and every repair is returned **as data**, so a chain that leaned on one says
#   so (:attr:`ChainResult.repaired_links`) and a human can review the table.
#
# ``XDGroup`` rather than ``RoadNumber`` + bearing is the load-bearing choice, and
# it was measured, not assumed: at the Flying Y the I-184 EB mainline's end point
# is **8.1 m** from the 1-lane on-ramp named ``1A`` and **4.7 m** from the true
# mainline continuation. Road number and bearing agree with both, distance prefers
# the ramp, and a walk repaired that way runs I-184 EB into a 196-segment,
# 110.6-mile chain (DESIGN_HISTORY Session 46).
GROUP_COL = "XDGroup"                 # XD's carriageway key — the repair scope
NEXT_COL = "NextXDSegI"
REPAIR_COLUMNS = ("segment", "old_next", "new_next", "kind", "gap_m",
                  "bearing_delta_deg", "xdgroup", "road_name", "lanes")
FILL = "fill"           # the link was null; the network asserted nothing
OVERRIDE = "override"   # the link pointed out of its own XDGroup
JUNCTION_TIE_FEET = 5.0
"""Two snaps closer than this are a tie, broken toward the segment the query point
begins (start) or finishes (end). A catalogue's 5-decimal endpoints move a point by
at most ~2 ft each, so a start and an end together stay under it (Item 53)."""

DEFAULT_REPAIR_RADIUS_M = 25.0
DEFAULT_MAX_BEARING_DELTA_DEG = 90.0
_BEARING_PROBE_M = 30.0   # how much of each end to measure the junction bearing over


def _endpoints(geom):
    """``(start, end)`` points of a line geometry, MultiLineString-safe."""
    return geom.interpolate(0.0, normalized=True), geom.interpolate(1.0, normalized=True)


def _terminal_bearing(geom, *, at_end: bool, probe: float) -> float | None:
    """Compass-ish bearing (degrees, CRS axes) over the last/first ``probe`` units
    of ``geom``.

    The *local* bearing at the junction, not the chord: a segment that curves
    through 90 degrees has a chord bearing that describes neither of its ends, and
    a U-turn pair at a cul-de-sac is only visible from the ends.
    """
    import math

    length = float(geom.length)
    if length <= 0:
        return None
    span = min(probe, length)
    if at_end:
        p1, p2 = geom.interpolate(length - span), geom.interpolate(length)
    else:
        p1, p2 = geom.interpolate(0.0), geom.interpolate(span)
    dx, dy = p2.x - p1.x, p2.y - p1.y
    if dx == 0 and dy == 0:
        return None
    return math.degrees(math.atan2(dx, dy)) % 360.0


def _bearing_delta(a: float | None, b: float | None) -> float:
    """Absolute turn between two bearings, 0-180 (NaN when either is unknown)."""
    if a is None or b is None:
        return float("nan")
    return abs((a - b + 180.0) % 360.0 - 180.0)


def repair_links(source, *, radius_m: float = DEFAULT_REPAIR_RADIUS_M,
                 max_bearing_delta_deg: float = DEFAULT_MAX_BEARING_DELTA_DEG,
                 require_same_bearing: bool = True, kinds=(FILL, OVERRIDE),
                 projected_crs=None, **load_kwargs) -> pd.DataFrame:
    """Derive the ``NextXDSegI`` repairs this network supports — **as a table**.

    Two classes, and they carry different weight. A :data:`FILL` adds a link where
    XD asserts nothing (``NextXDSegI`` is null). An :data:`OVERRIDE` *replaces* a
    link XD does assert, and only where that link leaves the segment's own
    ``XDGroup`` while a same-group continuation exists — the I-184 mainline pointed
    at an off-ramp, the SH-44 mainline pointed at a parallel 1-lane street of the
    same name. Overrides are rare (99 on D3 against 6,119 fills) and are the class
    a reviewer should read, which is why this returns a table rather than patching
    in place.

    Args:
        source: an XD network GeoDataFrame or a shapefile/GeoParquet path.
        radius_m: how close a candidate's start point must be to this segment's
            end point, in **metres** whatever the CRS's own units.
        max_bearing_delta_deg: reject a junction that turns more than this, on the
            local geometry at the junction. The default of 90 is not decorative: at
            0.0 m separation the D3 subset carries **anti-parallel** pairs (two
            segments of one cul-de-sac group whose ends coincide) that would
            otherwise repair into a 2-cycle.
        require_same_bearing: both sides must carry the same cardinal ``Bearing``
            (``geometry.direction_group``). This is the guard the angle test cannot
            supply, and D3 has the case that proves it: at the south end of Eagle Rd
            a **rotary** group (``Bearing`` ``O``, six segments of 8-20 m each) joins
            the southbound and northbound carriageways, and no single junction in it
            turns more than 60 degrees. Repaired through, ``sh55-eagle-nb`` walks
            *south* down Eagle Rd, round the rotary, and back *north* over the same
            ground — 39 segments and 10.75 mi where the corridor is 16 and 6.64.
        kinds: which classes to derive. ``(FILL,)`` alone is the conservative
            setting — it never contradicts the network, only completes it.
        projected_crs: CRS for the metric work; default is the network's UTM zone.

    Returns:
        A frame of :data:`REPAIR_COLUMNS`, one row per repair, sorted by segment.
        ``attrs`` carries the rule parameters, the counts per kind, and — the part
        that matters for reading it honestly — ``ambiguous_fill`` /
        ``ambiguous_override``, the breaks that had **more than one** qualifying
        continuation and were therefore left alone.
    """
    import geopandas as gpd

    kinds = tuple(kinds)
    unknown = set(kinds) - {FILL, OVERRIDE}
    if unknown:
        raise ValueError(f"Unknown repair kind(s) {sorted(unknown)}; expected {FILL!r}/{OVERRIDE!r}.")

    network = _as_network(source, **load_kwargs)
    for col in (GROUP_COL, NEXT_COL, "XDSegID"):
        if col not in network.columns:
            raise ValueError(f"Network is missing {col!r} — topology repair needs it.")
    if require_same_bearing and "Bearing" not in network.columns:
        raise ValueError("Network is missing 'Bearing' — pass require_same_bearing=False "
                         "to repair without the direction guard.")
    proj = project_network(network, projected_crs)
    m_per_unit = _metres_per_unit(proj.crs)
    radius = float(radius_m) / m_per_unit
    probe = _BEARING_PROBE_M / m_per_unit

    proj = proj.reset_index(drop=True)
    geoms = list(proj.geometry)
    ids = [int(s) for s in proj["XDSegID"]]
    ends = [_endpoints(g)[1] for g in geoms]
    starts_pt = [_endpoints(g)[0] for g in geoms]
    out_brg = [_terminal_bearing(g, at_end=True, probe=probe) for g in geoms]
    in_brg = [_terminal_bearing(g, at_end=False, probe=probe) for g in geoms]
    group = {i: (None if pd.isna(g) else g) for i, g in zip(ids, proj[GROUP_COL])}
    nxt = {i: (None if pd.isna(n) else int(n)) for i, n in zip(ids, proj[NEXT_COL])}
    road = dict(zip(ids, proj.get("RoadName", pd.Series([None] * len(ids)))))
    lanes = dict(zip(ids, proj.get("Lanes", pd.Series([None] * len(ids)))))
    if require_same_bearing:
        from .geometry import direction_group
        cardinal = {i: direction_group(b) for i, b in zip(ids, proj["Bearing"])}
    else:
        cardinal = {}

    start_index = gpd.GeoSeries(starts_pt, crs=proj.crs).sindex
    rows, ambiguous, off_subset = [], {FILL: 0, OVERRIDE: 0}, 0
    for k, sid in enumerate(ids):
        g = group[sid]
        if g is None:
            continue                      # no carriageway key, no scope to repair in
        old = nxt[sid]
        if old is None:
            kind = FILL
        elif old not in group:
            # The link leaves this *subset*, which is not a defect in the network —
            # it is the edge of the extract. Repairing it would substitute a
            # different road for one that is simply absent here; ``walk_chain``
            # reports it as ``off_network``, which is the honest answer.
            off_subset += 1
            continue
        elif group[old] != g:
            kind = OVERRIDE
        else:
            continue                      # the link stays in the carriageway: leave it
        if kind not in kinds:
            continue
        if require_same_bearing and cardinal.get(sid) is None:
            continue                      # rotary/unknown direction: nothing to preserve
        end_pt = ends[k]
        qualified = []
        for j in start_index.query(end_pt.buffer(radius)):
            cid = ids[j]
            if cid == sid or group[cid] != g:
                continue
            if require_same_bearing and cardinal.get(cid) != cardinal.get(sid):
                continue
            gap = float(end_pt.distance(starts_pt[j]))
            if gap > radius:
                continue
            delta = _bearing_delta(out_brg[k], in_brg[j])
            if not pd.isna(delta) and delta > float(max_bearing_delta_deg):
                continue
            qualified.append((cid, gap * m_per_unit, delta))
        if len(qualified) != 1:
            if qualified:
                ambiguous[kind] += 1
            continue
        cid, gap_m, delta = qualified[0]
        rows.append({
            "segment": sid, "old_next": old, "new_next": cid, "kind": kind,
            "gap_m": gap_m, "bearing_delta_deg": delta, "xdgroup": g,
            "road_name": road.get(sid), "lanes": lanes.get(sid),
        })

    patch = pd.DataFrame(rows, columns=list(REPAIR_COLUMNS))
    if not patch.empty:
        patch = patch.sort_values("segment", ignore_index=True)
        patch["segment"] = patch["segment"].astype("int64")
        patch["new_next"] = patch["new_next"].astype("int64")
        patch["old_next"] = patch["old_next"].astype("Int64")
    patch.attrs = {
        "radius_m": float(radius_m),
        "max_bearing_delta_deg": float(max_bearing_delta_deg),
        "require_same_bearing": bool(require_same_bearing),
        "bearing_probe_m": _BEARING_PROBE_M,
        "kinds": kinds,
        "n_segments": len(ids),
        "n_null_next": int(sum(1 for v in nxt.values() if v is None)),
        f"n_{FILL}": int((patch["kind"] == FILL).sum()) if not patch.empty else 0,
        f"n_{OVERRIDE}": int((patch["kind"] == OVERRIDE).sum()) if not patch.empty else 0,
        "ambiguous_fill": ambiguous[FILL],
        "ambiguous_override": ambiguous[OVERRIDE],
        "skipped_off_subset": off_subset,
        "crs": str(proj.crs),
    }
    return patch


def _repair_map(repairs) -> dict[int, int]:
    """``{segment -> new_next}`` from a patch frame, a mapping, or ``None``."""
    if repairs is None:
        return {}
    if isinstance(repairs, pd.DataFrame):
        if repairs.empty:
            return {}
        missing = {"segment", "new_next"} - set(repairs.columns)
        if missing:
            raise ValueError(f"Repair table is missing {sorted(missing)}.")
        return {int(s): int(n) for s, n in zip(repairs["segment"], repairs["new_next"])}
    return {int(s): int(n) for s, n in dict(repairs).items()}


def apply_link_repairs(network, repairs):
    """Return ``network`` with the patch's ``NextXDSegI`` values substituted in.

    Idempotent, and silent about repairs for segments this subset does not carry —
    a patch derived district-wide is meant to be reusable against any subset of it.
    """
    mapping = _repair_map(repairs)
    if not mapping:
        return network
    current = {int(s): n for s, n in zip(network["XDSegID"], network[NEXT_COL])
               if int(s) in mapping}
    if current and all(not pd.isna(n) and int(n) == mapping[s] for s, n in current.items()):
        return network                    # already applied — don't copy the network again
    out = network.copy()
    out[NEXT_COL] = [
        mapping.get(int(s), n) for s, n in zip(out["XDSegID"], out[NEXT_COL])
    ]
    return out


def load_link_repairs(path) -> pd.DataFrame:
    """Read a committed repair table (``.csv`` or ``.json``) written by
    :func:`repair_links`. The rule that generated it lives in the file's own
    ``_note`` / header, so a screening run can state what it walked on."""
    import json
    from pathlib import Path

    path = Path(path)
    if path.suffix.lower() == ".json":
        data = json.loads(path.read_text())
        if isinstance(data, dict):
            attrs = {k.lstrip("_"): v for k, v in data.items() if k.startswith("_")}
            data = data.get("repairs", [])
        else:
            attrs = {}
        patch = pd.DataFrame(data, columns=list(REPAIR_COLUMNS))
    else:
        # A ``# key: value`` header block carries the rule the table was derived
        # under, so a run can state what it walked on.
        attrs = {}
        with path.open() as fh:
            for line in fh:
                if not line.startswith("#"):
                    break
                key, sep, val = line[1:].strip().partition(":")
                if sep:
                    attrs[key.strip()] = val.strip()
        patch = pd.read_csv(path, comment="#")
    for col in ("segment", "new_next"):
        patch[col] = patch[col].astype("int64")
    patch["old_next"] = patch["old_next"].astype("Int64")
    patch.attrs = {**attrs, "source": str(path)}
    return patch


def _repairs_used(segment_ids, repairs) -> tuple[tuple[int, int], ...]:
    """Which repaired links a finished chain actually traversed."""
    mapping = _repair_map(repairs)
    if not mapping:
        return ()
    return tuple(
        (int(a), int(b))
        for a, b in zip(segment_ids, segment_ids[1:])
        if mapping.get(int(a)) == int(b)
    )


# ---------------------------------------------------------------------------
# Chain assembly
# ---------------------------------------------------------------------------
def _segment_miles(proj_net_indexed, sid: int) -> float:
    """XD ``Miles`` for a segment, falling back to its projected geometry length."""
    row = proj_net_indexed.loc[sid]
    miles = row.get(MILES_COL) if hasattr(row, "get") else None
    if miles is not None and not pd.isna(miles):
        return float(miles)
    m_per_unit = _metres_per_unit(proj_net_indexed.crs)
    return float(row["geometry"].length) * m_per_unit / _METRES_PER_MILE


def build_chain(source, start_latlon, end_latlon, *, projected_crs=None,
                k_candidates: int = 8, max_snap_feet: float | None = 500.0,
                max_steps: int = 500, candidates=None, repairs=None,
                **load_kwargs) -> ChainResult:
    """Assemble the XD chain between two query points.

    Snaps both endpoints in a projected CRS, walks ``NextXDSegI`` from start to
    end, and returns a :class:`ChainResult` carrying the ordered ids, per-segment
    miles, the **snap distance at each end**, the endpoint **trims**, and an
    explicit ``reached_target`` flag.

    Because the nearest segment to a point may be the *opposing* direction of a
    divided road, the ``k_candidates`` nearest segments **within ``max_snap_feet``**
    are tried at each end and the combination that **reaches the target** with the
    smallest total snap distance wins; if none reach it, the nearest pair is
    returned with ``reached_target=False`` and the walk's ``stop_reason``. The
    distance guard matters: without it, a far-off candidate that happens to connect
    would be preferred over the true (unconnected) snap — a 2-mile route could
    collapse onto one segment a third of a mile away and still look like a hit.

    Args:
        source: an XD network GeoDataFrame (``geometry.load_xd_network``) or a
            shapefile/GeoParquet path — extra kwargs pass through to the loader.
            The subset must contain every segment of the chain, or the walk stops
            with ``stop_reason="off_network"``.
        start_latlon / end_latlon: ``(lat, lon)`` query points, in travel order.
        projected_crs: CRS for the metric work; default is the network's own UTM
            zone. Never measured in degrees (Item 28 / G7).
        k_candidates: how many nearest segments to try at each end. The default
            of 8 is empirical, not decorative: at a signalised arterial
            intersection the cross-street approaches, the opposing carriageway and
            the turn stubs routinely put **six** segments nearer to the query point
            than the one the route actually runs on (measured on the Eagle Rd VSL
            endpoints, where the right segment ranked 7th at 47 ft). A smaller k
            silently returns a chain that walks past its target.
        max_snap_feet: candidates farther than this from the query point are not
            considered (real query points snap within ~100 ft — see Session 33).
            When nothing is within range the single nearest candidate is used
            anyway, so its snap distance is visible in the result rather than the
            chain silently disappearing. ``None`` disables the guard.
        candidates: restrict the snap to these ``Segment ID`` values (e.g. one
            direction, or an export's segments).
        repairs: an optional ``NextXDSegI`` patch table (:func:`repair_links` or
            :func:`load_link_repairs`) to walk on. **Opt-in, and never silent**:
            the links the chain actually owes to it come back on
            :attr:`ChainResult.repaired_links`.

    Returns:
        :class:`ChainResult`. Nothing raises on a chain that fails to connect —
        inspect ``reached_target`` / ``stop_reason``.
    """
    network = apply_link_repairs(_as_network(source, **load_kwargs), repairs)
    proj = project_network(network, projected_crs)
    crs_str = str(proj.crs)

    starts = snap_candidates(proj, start_latlon, k_candidates, candidates)
    ends = snap_candidates(proj, end_latlon, k_candidates, candidates)
    if not starts or not ends:
        raise ValueError("No network segments to snap to (empty network or candidate set).")
    if max_snap_feet is not None:
        # Keep the nearest candidate regardless, so an out-of-range snap shows up
        # as a large ``snap_*_feet`` instead of an exception.
        starts = [c for c in starts if c[1] <= max_snap_feet] or starts[:1]
        ends = [c for c in ends if c[1] <= max_snap_feet] or ends[:1]

    combos = []   # (reached, total snap ft, junction score, start cand, end cand, chain, reason)
    for s_id, s_ft, s_frac in starts:
        for e_id, e_ft, e_frac in ends:
            chain, reason = walk_chain(proj, s_id, e_id, max_steps=max_steps)
            combos.append((reason == "target", s_ft + e_ft, s_frac + (1.0 - e_frac),
                           (s_id, s_ft, s_frac), (e_id, e_ft, e_frac), chain, reason))
    # Ties on snap distance (a point exactly at a junction) go to the start segment
    # the point *begins* and the end segment it *finishes*: a start at the far end of
    # the upstream segment adds that segment with ~0 of it in the extent. Myrtle St EB
    # began on I-184 once Item 51's route junctions let the Connector walk onto it.
    # "Tied" means within JUNCTION_TIE_FEET, not equal to 0.1 ft: a catalogue writes
    # its endpoints to 5 decimals, which moves a junction node by up to ~2 ft, and at
    # Moscow's south junction that made SH-8 Troy Rd's last segment 1 ft nearer than
    # Washington St's first (Item 53) — both couplet legs then took 0.51 mi of
    # two-way Troy Rd.
    # Only a junction tie is broken this way: the nearer snap gives way only to one a
    # whole segment "better" (the point at the far end of the upstream segment
    # against the start of the next), never to a cross-street a few feet off.
    pool = [c for c in combos if c[0]] or combos
    best = min(pool, key=lambda c: (round(c[1], 1), c[2]))
    at_node = [c for c in pool if c[1] <= best[1] + JUNCTION_TIE_FEET
               and c[2] <= best[2] - 0.9]
    if at_node:
        best = min(at_node, key=lambda c: (c[2], c[1]))
    _, _, _, (s_id, s_ft, s_frac), (e_id, e_ft, e_frac), chain, reason = best
    reached = reason == "target"

    indexed = proj.set_index("XDSegID")
    miles = [_segment_miles(indexed, sid) for sid in chain]

    # Endpoint trim: the part of the first segment *upstream* of the start point
    # and the part of the last segment *downstream* of the end point lie outside
    # the requested extent. Fractions come from the projected geometry; miles come
    # from XD ``Miles`` (the authoritative length).
    if reached and len(chain) == 1:
        # Both points on one segment: the extent is the span between them.
        frac = [max(e_frac - s_frac, 0.0)]
        trim_start = s_frac * miles[0]
        trim_end = (1.0 - e_frac) * miles[0]
    else:
        frac = [1.0] * len(chain)
        frac[0] = 1.0 - s_frac
        trim_start = s_frac * miles[0]
        if reached:
            frac[-1] = e_frac
            trim_end = (1.0 - e_frac) * miles[-1]
        else:
            # The walk never got to the end point — there is no meaningful
            # downstream trim, and saying "0.0" would read as "no overshoot".
            trim_end = float("nan")

    chain_miles = float(sum(miles))
    requested = float(sum(m * f for m, f in zip(miles, frac)))
    return ChainResult(
        segment_ids=tuple(int(s) for s in chain),
        miles=tuple(miles),
        extent_fraction=tuple(float(f) for f in frac),
        chain_miles=chain_miles,
        requested_miles=requested,
        trim_start_miles=float(trim_start),
        trim_end_miles=float(trim_end),
        snap_start_feet=float(s_ft),
        snap_end_feet=float(e_ft),
        reached_target=reached,
        stop_reason=reason,
        target_segment=int(e_id),
        crs=crs_str,
        repaired_links=_repairs_used(chain, repairs),
    )


def chain_between_segments(source, start_segment: int, end_segment: int, *,
                           projected_crs=None, max_steps: int = 500, repairs=None,
                           **load_kwargs) -> ChainResult:
    """Assemble a chain between two **named terminal segments** instead of two
    query points.  (Item 30)

    For a reference route whose sheet gives place names rather than coordinates
    (``reference.route_endpoints`` refuses those — the extent would be whatever a
    geocoder chose), an operator can state the two end segments instead. The walk,
    the member list and the missing-segment accounting are then exactly as in
    :func:`build_chain`; what a query point would have provided is *absent*, and
    the result says so rather than inventing it:

    - ``requested_miles == chain_miles`` and every ``extent_fraction`` is 1.0 —
      the extent **is** the whole end segments, so :func:`chain_travel_time` over
      it has nothing to prorate to and its ``length_ratio`` is 1.0 by
      construction, not by measurement.
    - ``snap_start_feet`` / ``snap_end_feet`` are **NaN**: there is no query point
      to measure a snap against, and a fabricated 0.0 would read as a perfect
      match.
    """
    network = apply_link_repairs(_as_network(source, **load_kwargs), repairs)
    ids, reason = walk_chain(network, start_segment, end_segment, max_steps=max_steps)
    if not ids:
        raise ValueError(
            f"Segment {int(start_segment)} is not in this network subset "
            "(stop_reason 'off_network') — widen the subset.")
    proj = project_network(network, projected_crs).set_index("XDSegID")
    miles = tuple(_segment_miles(proj, sid) for sid in ids)
    total = float(sum(miles))
    return ChainResult(
        segment_ids=tuple(int(s) for s in ids),
        miles=miles,
        extent_fraction=tuple(1.0 for _ in ids),
        chain_miles=total,
        requested_miles=total,
        trim_start_miles=0.0,
        trim_end_miles=0.0,
        snap_start_feet=float("nan"),
        snap_end_feet=float("nan"),
        reached_target=(reason == "target"),
        stop_reason=reason,
        target_segment=int(end_segment),
        crs=str(proj.crs),
        repaired_links=_repairs_used(ids, repairs),
    )


# ---------------------------------------------------------------------------
# Missing-segment accounting  (G8)
# ---------------------------------------------------------------------------
def chain_coverage(chain: ChainResult, observed, *, value: str | None = None,
                   cvalue_threshold: float | None = None) -> pd.DataFrame:
    """Which chain members an export actually observes — **no silent fallback**.

    Args:
        chain: the :class:`ChainResult` to account for.
        observed: an export frame with a ``Segment ID`` column, or any iterable of
            observed segment ids.
        value: when ``observed`` is a frame, count a row only where this column is
            non-NaN (matches ``speed.corridor_travel_time``'s value-aware
            completeness).
        cvalue_threshold: count only rows passing ``CValue > threshold``. Pass the
            **same** threshold :func:`chain_travel_time` is summing under, or the
            coverage panel and the sum answer the same question differently: a
            member that is entirely historical backfill is "observed" ungated and
            absent once gated. The imputed share below is measured on the
            **ungated** rows either way — it is the thing being gated on.

    Returns:
        One row per member in travel order: ``[sequence, Segment ID, Miles,
        in_extent_fraction, extent_miles, n_obs, observed]``. ``attrs`` carries the
        chain summary plus ``n_missing``, ``missing_miles`` (in-extent miles with
        no observations), ``observed_miles`` and ``miles_covered_fraction`` — so a
        report physically cannot reduce this to a "17/20" string.

        When ``observed`` is a frame carrying ``CValue``, two more columns:
        ``n_imputed`` and ``imputed_fraction`` — the per-member share of rows with
        a null CValue (INRIX's imputation marker). A member can be fully
        "observed" and still be mostly historical backfill; the two questions are
        different and are answered separately (ROADMAP Item 31, DATA_FORMAT.md).
        ``attrs['imputed_fraction']`` carries the chain-wide share.
    """
    if isinstance(observed, pd.DataFrame):
        if SEGMENT_COL not in observed.columns:
            raise ValueError(f"{SEGMENT_COL!r} column required in the observed frame.")
        sub = observed
        if value is not None:
            if value not in sub.columns:
                raise ValueError(f"Value column {value!r} not in the observed frame.")
            sub = sub[sub[value].notna()]
        ungated = sub
        if cvalue_threshold is not None:
            if CVALUE_COL not in sub.columns:
                raise ValueError(
                    f"No {CVALUE_COL!r} column but cvalue_threshold={cvalue_threshold} "
                    "was requested; pass cvalue_threshold=None to count ungated.")
            sub = filter_cvalue(sub, cvalue_threshold)
        counts = sub[SEGMENT_COL].astype("int64").value_counts()
    else:
        ids = pd.Series(list(observed), dtype="float64").dropna().astype("int64")
        counts = ids.value_counts()

    out = chain.frame()
    out[NOBS_COL] = [int(counts.get(sid, 0)) for sid in out[SEGMENT_COL]]
    out[OBSERVED_COL] = out[NOBS_COL] > 0

    imputed_share = float("nan")
    if isinstance(observed, pd.DataFrame) and CVALUE_COL in observed.columns:
        # On the ungated rows: how much of what this chain had to work with was
        # backfill rather than observation.
        marked = mark_imputed(ungated)
        by_seg = marked.groupby(marked[SEGMENT_COL].astype("int64"))[IMPUTED_COL]
        imp, tot = by_seg.sum(), by_seg.size()
        out[NIMPUTED_COL] = [int(imp.get(sid, 0)) for sid in out[SEGMENT_COL]]
        totals = pd.Series([int(tot.get(sid, 0)) for sid in out[SEGMENT_COL]])
        out[IMPUTED_FRACTION_COL] = out[NIMPUTED_COL] / totals.where(totals > 0)
        imputed_share = (int(out[NIMPUTED_COL].sum()) / int(totals.sum())
                         if int(totals.sum()) else float("nan"))

    missing_miles = float(out.loc[~out[OBSERVED_COL], EXTENT_MILES_COL].sum())
    observed_miles = float(out.loc[out[OBSERVED_COL], EXTENT_MILES_COL].sum())
    total = observed_miles + missing_miles
    out.attrs = {
        **chain.summary(),
        "n_missing": int((~out[OBSERVED_COL]).sum()),
        "missing_miles": missing_miles,
        "observed_miles": observed_miles,
        "miles_covered_fraction": (observed_miles / total) if total > 0 else float("nan"),
        "imputed_fraction": imputed_share,
        "cvalue_threshold": cvalue_threshold,
    }
    return out


# ---------------------------------------------------------------------------
# Chain travel time (the trim decision, applied)
# ---------------------------------------------------------------------------
def chain_travel_time(df: pd.DataFrame, chain: ChainResult, *, prorate: bool = True,
                      value: str | None = None, require_complete: bool = True,
                      label: str = CHAIN_LABEL, expected: str = "total",
                      on_absent: str = "short",
                      cvalue_threshold: float | None = None,
                      datetime_col: str = DATETIME_COL,
                      corridor_col: str = CORRIDOR_COL) -> pd.DataFrame:
    """Sum a chain's member travel time per timestamp, **prorating the two end
    segments** by the fraction of each that lies inside the requested extent.

    Proration is the default for comparison work (DATA_FORMAT.md): comparing an
    INRIX chain against an external route that ends mid-segment otherwise compares
    two different extents — the VSL chain was 21% longer than the route it was
    scored against. The **assumption is uniform speed within a segment**: the
    covered fraction of a segment is credited the same fraction of its travel time.
    Pass ``prorate=False`` for the whole-segment sum (report-only), which is right
    when the chain's own extent is the thing of interest.

    Everything else is :func:`speed.corridor_travel_time`, with the chain's
    **requested** membership (``members=chain.segment_ids``, ``expected="total"``)
    as the complete-set size — so a member that is sometimes missing drops the
    timestamp, and a member that is *never* present marks the sum ``short`` rather
    than passing as complete (ROADMAP Item 31; before it, Eagle Rd NB's 17-of-20
    sum was marked complete in all 2,633 bins). Pair it with
    :func:`chain_coverage` to see *which* members are missing.

    Args:
        cvalue_threshold: when given, gate the rows to ``CValue > threshold``
            before summing (strict, as :func:`io.filter_cvalue`), recording it on
            ``attrs['cvalue_threshold']``. A **null** CValue is INRIX's imputation
            marker and fails the comparison, so the gate also removes historical
            backfill — 21.7% of the 2026 D3 validation export. ``None`` (default)
            does not gate, but the imputation **share is measured either way**
            whenever the frame carries a ``CValue`` column, because the share is
            route- and hour-dependent and a study-wide pass/fail hides that
            (DATA_FORMAT.md).
        on_absent: see :func:`speed.corridor_travel_time` — ``"short"`` reports
            the shortened sum and labels it, ``"drop"`` drops every timestamp.

    Returns:
        One row per timestamp: ``[corridor_col, Date Time, <value>, n_segments,
        n_requested, n_absent, expected_segments, short, complete]`` plus the
        length block and, when travel time is in minutes, space-mean
        ``Corridor Speed(miles/hour)``:

        - ``Length(Miles)`` — the extent **actually summed**: the requested (or
          whole-segment) miles less any member that carries no data. Before Item
          31 this credited the full request even where an end segment was
          unobserved, overstating Eagle Rd NB's length by 0.290 of 6.938 mi (4.2%)
          and Franklin EB's by 0.250 of 2.763 mi (9.0%) — and every derived speed
          with it.
        - ``requested_length_miles`` — what was asked for, so the request is not
          lost behind the correction.
        - ``missing_length_miles`` — the difference: pavement inside the extent
          with no observations. An unobserved **end** segment is dropped whole
          rather than prorated; prorating it credited its trimmed mileage to a
          sum it contributed nothing to.
        - ``length_basis`` — ``"requested"`` when nothing is missing, ``"observed"``
          when the length had to be cut back. The label is the point: a shortened
          corridor stays legible as one.

        When a ``CValue`` column is present, three more per-bin columns: ``n_rows``
        and ``n_rows_ungated`` (member rows at that timestamp after/before the
        gate), ``cvalue_kept_fraction``, and ``imputed_fraction`` (the share of
        that bin's member rows carrying a null CValue).
    """
    tt = value or metric_columns(df)["travel_time"]
    if tt is None:
        raise ValueError("No 'Travel Time(...)' column found.")
    if tt not in df.columns:
        raise ValueError(f"Value column {tt!r} not in df.")

    ids = list(chain.segment_ids)
    ungated = df[df[SEGMENT_COL].isin(ids)].copy()

    # CValue accounting first, on the ungated rows: gating away the backfill and
    # then measuring how much backfill there was is not a thing one can do in the
    # other order.
    accounting = _cvalue_accounting(ungated, datetime_col)
    sub = ungated
    if cvalue_threshold is not None:
        if CVALUE_COL not in sub.columns:
            raise ValueError(
                f"No {CVALUE_COL!r} column but cvalue_threshold={cvalue_threshold} "
                "was requested; pass cvalue_threshold=None to sum ungated — "
                "explicitly (see DATA_FORMAT.md)."
            )
        sub = filter_cvalue(sub, cvalue_threshold)

    if prorate:
        w = sub[SEGMENT_COL].astype("int64").map(chain.weights())
        sub[tt] = sub[tt] * w

    sub[corridor_col] = label
    out = corridor_travel_time(
        sub, corridor_col=corridor_col, value=tt, require_complete=require_complete,
        datetime_col=datetime_col, members=ids, expected=expected, on_absent=on_absent,
    )

    lengths = _chain_lengths(chain, sub, tt, prorate=prorate)
    out["Length(Miles)"] = lengths["observed"]
    out["requested_length_miles"] = lengths["requested"]
    out["missing_length_miles"] = lengths["missing"]
    out["length_basis"] = lengths["basis"]
    if df.attrs.get("units", {}).get("travel_time") == "Minutes":
        hours = out[tt] / 60.0
        out["Corridor Speed(miles/hour)"] = lengths["observed"] / hours.where(hours > 0)

    if accounting is not None:
        kept = (sub.groupby(datetime_col, observed=True).size()
                .rename("n_rows").reset_index())
        out = out.merge(accounting, on=datetime_col, how="left")
        out = out.merge(kept, on=datetime_col, how="left")
        out["n_rows"] = out["n_rows"].fillna(0).astype("int64")
        out["cvalue_kept_fraction"] = (
            out["n_rows"] / out["n_rows_ungated"].where(out["n_rows_ungated"] > 0)
        )

    out.attrs = {**dict(df.attrs), "chain": chain.summary(), "prorated": bool(prorate),
                 "cvalue_threshold": cvalue_threshold, "length": lengths}
    return out


def _cvalue_accounting(rows: pd.DataFrame, datetime_col: str) -> pd.DataFrame | None:
    """Per-timestamp imputed-row accounting on the **ungated** member rows, or
    ``None`` when the export carries no ``CValue`` column to judge by."""
    if CVALUE_COL not in rows.columns:
        return None
    marked = mark_imputed(rows)
    g = marked.groupby(datetime_col, observed=True)[IMPUTED_COL]
    out = g.agg(n_rows_ungated="size", n_imputed="sum").reset_index()
    out["n_imputed"] = out["n_imputed"].astype("int64")
    out["imputed_fraction"] = out["n_imputed"] / out["n_rows_ungated"]
    return out


def _chain_lengths(chain: ChainResult, rows: pd.DataFrame, value: str, *,
                   prorate: bool) -> dict:
    """The extent actually summed, against the extent requested (Item 31).

    A member with no valued row contributes nothing to the sum, so its pavement
    must not be credited to the corridor's length — including when it is one of
    the two **end** segments, whose trimmed fraction was previously prorated into
    a sum it took no part in.
    """
    valued = set(rows.loc[rows[value].notna(), SEGMENT_COL].astype("int64"))
    per_member = chain.extent_miles if prorate else chain.miles
    # Anchor on the chain's own published figure so an intact chain reports
    # exactly what it reported before; subtract only what is genuinely unobserved.
    requested = float(chain.requested_miles if prorate else chain.chain_miles)
    missing = float(sum(m for sid, m in zip(chain.segment_ids, per_member)
                        if int(sid) not in valued))
    observed = requested - missing
    return {
        "requested": requested,
        "observed": observed,
        "missing": missing,
        "basis": "requested" if missing <= 0 else "observed",
    }


# ---------------------------------------------------------------------------
# What road is this, actually?  (Item 30)
# ---------------------------------------------------------------------------
_DESCRIBE_FIELDS = ("RoadName", "RoadNumber", "County", "PostalCode")


def chain_attributes(chain: ChainResult, network, fields=_DESCRIBE_FIELDS) -> pd.DataFrame:
    """The chain's members in travel order, joined to the network's attributes.

    A lookup and a join — no geometry work, so it takes an already-loaded network
    frame (or anything with ``XDSegID`` and the requested columns).
    """
    attrs = pd.DataFrame(network).drop(columns="geometry", errors="ignore")
    if "XDSegID" not in attrs.columns:
        raise ValueError("Network frame has no 'XDSegID' column to join on.")
    keep = ["XDSegID"] + [f for f in fields if f in attrs.columns]
    attrs = attrs[keep].drop_duplicates(subset="XDSegID")
    attrs["XDSegID"] = attrs["XDSegID"].astype("int64")
    out = chain.frame().merge(attrs, left_on=SEGMENT_COL, right_on="XDSegID",
                              how="left").drop(columns="XDSegID")
    out.attrs = chain.summary()
    return out


def chain_description(chain: ChainResult, network, fields=_DESCRIBE_FIELDS) -> dict:
    """What the network says this chain **is** — the label, measured not recalled.

    Returns one ordered, de-duplicated tuple per field (in travel order) plus
    ``road_label``, the ``RoadName``/``RoadNumber`` pair joined for display.

    This exists because the outside pass hand-wrote its corridor headings and got
    them wrong (Session 33 G10): a rural route captioned "SH-17" — a route number
    that does not exist in Idaho — and an in-town route captioned "Nampa to
    Caldwell" when every member segment carries a Nampa ZIP. A label read off the
    segments cannot drift from the segments.
    """
    attrs = chain_attributes(chain, network, fields=fields)
    out: dict[str, tuple] = {}
    for field in fields:
        if field not in attrs.columns:
            continue
        seen, values = set(), []
        for v in attrs[field]:
            if v is None or (isinstance(v, float) and v != v):
                continue
            text = str(v).strip()
            if text and text.lower() not in ("none", "nan") and text not in seen:
                seen.add(text)
                values.append(text)
        out[field] = tuple(values)
    names = list(out.get("RoadName", ())) or list(out.get("RoadNumber", ()))
    numbers = [n for n in out.get("RoadNumber", ()) if n not in names]
    out["road_label"] = " / ".join(names + ([f"({', '.join(numbers)})"] if numbers else []))
    return out


# ---------------------------------------------------------------------------
# The corridor catalogue: endpoint pairs, resolved and accounted  (Item 36)
# ---------------------------------------------------------------------------
#
# A catalogue is local knowledge — *which* corridors a district-wide screen should
# rank, and which interchange to which interchange each one runs between. It is
# data, not code: a JSON file of endpoint pairs (``scripts/d3_corridors.json`` for
# ITD District 3), resolved through :func:`build_chain` like any other chain.
#
# What it deliberately is **not** is the mechanism the 2026-09 outside screening
# pass used: ``RoadNumber`` plus a lat/lon bounding box, a ``NextXDSegI`` walk, and
# a fall back to sorting the box's segments by latitude or longitude whenever the
# walk covered less than 70% of it. That fallback fired routinely — only 84% of
# consecutive pairs in its I-84 eastbound result were actual network links — and a
# geographic sort cannot tell a corridor from a facility that merely runs beside
# one, so it summed the Garrity Blvd frontage road in series with the freeway it
# parallels. Here an entry that will not walk is **recorded with its stop_reason**;
# there is no fallback to fall into.
CATALOGUE_COLLECTION = "corridors"      # the key holding the entry list in the JSON
REPORTING_COLLECTION = "reporting_corridors"   # the key holding the group list (Item 40)
_ENTRY_FIELDS = ("id", "name", "start_latlon", "end_latlon", "description")
_ENTRY_OPTIONAL = ("corridor", "direction",    # the reporting group this entry is one direction of
                   "links")                    # entry-scoped NextXDSegI patch (Item 51)
_GROUP_FIELDS = ("id", "name", "description")
_GROUP_OPTIONAL = ("one_way_couplet",)   # the two directions are different streets
DEFAULT_MIN_COVERAGE = 0.95             # accepted entries must observe >95% of their miles

# A catalogue entry is one **direction** of one extent, because that is the unit the
# network and the AADT join both work in. A *reporting* corridor is the road: both
# directions of it, named the way a district talks about it ("I-84, Nampa to Boise"),
# which is the unit a ranking is read in. The two are deliberately separate — an
# entry carries ``corridor`` (which reporting corridor it belongs to) and
# ``direction``, and :func:`screen.rank_corridor_groups` does the combining, so the
# directional detail is never lost behind the grouped number.


@dataclass(frozen=True)
class CorridorEntry:
    """One catalogue entry: a named corridor as the two endpoints of its extent.

    ``start_latlon`` / ``end_latlon`` are ``(lat, lon)`` in WGS84, **in travel
    order** (so a directional pair is two entries, not one reversible one). The
    ``description`` is not decoration: it carries *why* this extent is the
    meaningful one, which is the part of a hand-built catalogue that cannot be
    re-derived from the network.
    """

    id: str
    name: str
    start_latlon: tuple[float, float]
    end_latlon: tuple[float, float]
    description: str
    corridor: str | None = None      # the reporting corridor this is one direction of
    direction: str | None = None     # "EB"/"WB"/"NB"/"SB" — the direction it is
    links: tuple[tuple[int, int], ...] = ()
    """``(segment, next)`` links this entry walks that ``NextXDSegI`` does not assert —
    where its route turns off INRIX's link, or only the route number changes along the
    street (Item 51). Applied to this entry's walk alone, never to the network: at
    Idaho Falls' Sunnyside Rd the US-26 chain follows the street onto US-91 while the
    I-15 BL approach keeps the link, and one patch cannot serve both."""


@dataclass(frozen=True)
class ReportingCorridor:
    """One **reporting** corridor: the road both directions of it make up.

    A ranking is read per road, not per carriageway, but every number is computed
    per carriageway — so this carries only the identity, and the combining rules
    live in :func:`screen.rank_corridor_groups` where the metrics are.
    """

    id: str
    name: str
    description: str
    one_way_couplet: bool = False
    """The two directions run on **different streets** rather than two carriageways
    of one road (District 3 has exactly one: Myrtle St EB / Front St WB). It changes
    no arithmetic — every per-mile rate divides by the miles a round trip covers
    either way — but it changes how ``directional_miles`` reads: distinct centre-line
    pavement here, the same ground driven twice everywhere else."""


def _latlon(value, *, entry_id: str, field: str) -> tuple[float, float]:
    """Validate one ``(lat, lon)`` pair — in range, and in that order."""
    try:
        lat, lon = (float(v) for v in value)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            f"Catalogue entry {entry_id!r}: {field} must be a [lat, lon] pair, got {value!r}"
        ) from exc
    if not -90.0 <= lat <= 90.0 or not -180.0 <= lon <= 180.0:
        raise ValueError(
            f"Catalogue entry {entry_id!r}: {field} = ({lat}, {lon}) is out of range — "
            "the order is (lat, lon), not (lon, lat)."
        )
    return (lat, lon)


def parse_catalogue(data) -> tuple[CorridorEntry, ...]:
    """Validate a catalogue **already loaded** from JSON (a mapping with a
    ``"corridors"`` list, or a bare list of entries) into :class:`CorridorEntry`.

    Every field is required and every rule is enforced here rather than at the point
    of use: ids unique and non-empty, endpoints in range, and a **non-empty
    description** — an entry whose extent has no stated reason is a bounding box
    with better manners. Keys beginning with ``_`` (e.g. ``_note``) are ignored, so
    the file can carry its own provenance; any other unknown key raises, because
    ``start_latlong`` silently ignored is a corridor resolved somewhere else.
    """
    if isinstance(data, Mapping):
        if CATALOGUE_COLLECTION not in data:
            raise ValueError(
                f"Catalogue has no {CATALOGUE_COLLECTION!r} key "
                f"(found: {sorted(k for k in data if not str(k).startswith('_'))})."
            )
        rows = data[CATALOGUE_COLLECTION]
    else:
        rows = data
    if not isinstance(rows, (list, tuple)) or not rows:
        raise ValueError("Catalogue must hold a non-empty list of corridor entries.")

    out: list[CorridorEntry] = []
    seen: set[str] = set()
    for i, row in enumerate(rows):
        if not isinstance(row, Mapping):
            raise ValueError(f"Catalogue entry #{i + 1} is not an object: {row!r}")
        entry_id = str(row.get("id", "") or "").strip()
        if not entry_id:
            raise ValueError(f"Catalogue entry #{i + 1} has no 'id'.")
        if entry_id in seen:
            raise ValueError(f"Duplicate catalogue id {entry_id!r}.")
        seen.add(entry_id)
        missing = [f for f in _ENTRY_FIELDS if f not in row]
        if missing:
            raise ValueError(f"Catalogue entry {entry_id!r} is missing {missing}.")
        allowed = _ENTRY_FIELDS + _ENTRY_OPTIONAL
        unknown = [k for k in row if k not in allowed and not str(k).startswith("_")]
        if unknown:
            raise ValueError(f"Catalogue entry {entry_id!r} has unknown field(s) {unknown}.")
        for field in ("name", "description"):
            if not str(row[field] or "").strip():
                raise ValueError(f"Catalogue entry {entry_id!r} has an empty {field!r}.")
        # ``corridor`` and ``direction`` travel together: a group with no direction
        # cannot be broken out, and a direction with no group has nothing to join.
        group = str(row.get("corridor", "") or "").strip() or None
        direction = str(row.get("direction", "") or "").strip().upper() or None
        if (group is None) != (direction is None):
            raise ValueError(
                f"Catalogue entry {entry_id!r} carries "
                f"{'corridor' if group else 'direction'} without the other; a reporting "
                "corridor needs both, so the directions can be told apart inside it.")
        links: list[tuple[int, int]] = []
        for pair in row.get("links") or ():
            try:
                a, b = (int(v) for v in pair)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"Catalogue entry {entry_id!r}: each link must be a "
                                 f"[segment, next] pair, got {pair!r}") from exc
            links.append((a, b))
        out.append(
            CorridorEntry(
                id=entry_id,
                name=str(row["name"]).strip(),
                start_latlon=_latlon(row["start_latlon"], entry_id=entry_id,
                                     field="start_latlon"),
                end_latlon=_latlon(row["end_latlon"], entry_id=entry_id,
                                   field="end_latlon"),
                description=str(row["description"]).strip(),
                corridor=group,
                direction=direction,
                links=tuple(links),
            )
        )
    entries = tuple(out)
    _check_group_directions(entries)
    return entries


def _check_group_directions(entries) -> None:
    """Two entries of one reporting corridor may not claim the same direction —
    that is a copy-paste in the catalogue, and it would double-count one carriageway
    into the grouped total while dropping the other."""
    seen: dict[tuple[str, str], str] = {}
    for e in entries:
        if e.corridor is None:
            continue
        key = (e.corridor, e.direction)
        if key in seen:
            raise ValueError(
                f"Catalogue entries {seen[key]!r} and {e.id!r} are both "
                f"{e.direction} of reporting corridor {e.corridor!r}.")
        seen[key] = e.id


def parse_reporting_corridors(data) -> tuple[ReportingCorridor, ...]:
    """Validate the ``reporting_corridors`` block — the roads the entries group into.

    Absent, this returns ``()`` and the catalogue is simply ungrouped. Present, it
    must be **consistent with the entries**: every group an entry names must be
    declared here, and every declared group must have at least one entry. A group
    declared but unused is a corridor silently missing from the report.
    """
    entries = ()
    if isinstance(data, Mapping):
        rows = data.get(REPORTING_COLLECTION)
        if rows is None:
            return ()
        if CATALOGUE_COLLECTION in data:
            entries = parse_catalogue(data)
    else:
        rows = data
    if not isinstance(rows, (list, tuple)):
        raise ValueError(f"{REPORTING_COLLECTION!r} must hold a list of corridor groups.")

    out, seen = [], set()
    for i, row in enumerate(rows):
        if not isinstance(row, Mapping):
            raise ValueError(f"Reporting corridor #{i + 1} is not an object: {row!r}")
        gid = str(row.get("id", "") or "").strip()
        if not gid:
            raise ValueError(f"Reporting corridor #{i + 1} has no 'id'.")
        if gid in seen:
            raise ValueError(f"Duplicate reporting corridor id {gid!r}.")
        seen.add(gid)
        missing = [f for f in _GROUP_FIELDS if f not in row]
        if missing:
            raise ValueError(f"Reporting corridor {gid!r} is missing {missing}.")
        allowed = _GROUP_FIELDS + _GROUP_OPTIONAL
        unknown = [k for k in row if k not in allowed and not str(k).startswith("_")]
        if unknown:
            raise ValueError(f"Reporting corridor {gid!r} has unknown field(s) {unknown}.")
        for field in ("name", "description"):
            if not str(row[field] or "").strip():
                raise ValueError(f"Reporting corridor {gid!r} has an empty {field!r}.")
        out.append(ReportingCorridor(id=gid, name=str(row["name"]).strip(),
                                     description=str(row["description"]).strip(),
                                     one_way_couplet=bool(row.get("one_way_couplet", False))))

    if entries:
        used = {e.corridor for e in entries if e.corridor is not None}
        undeclared = sorted(used - seen)
        if undeclared:
            raise ValueError(
                f"Catalogue entries name reporting corridor(s) {undeclared} that "
                f"{REPORTING_COLLECTION!r} does not declare.")
        unused = sorted(seen - used)
        if unused:
            raise ValueError(
                f"Reporting corridor(s) {unused} are declared but no entry belongs to "
                "them — a corridor declared and unused is one missing from the report.")
    return tuple(out)


def couplet_segments(entries, groups, chains) -> set[int]:
    """Every segment on a one-way couplet leg: the resolved chains of the entries whose
    reporting corridor is ``one_way_couplet``. What
    :func:`inrix_tools.aadt.apply_two_way_basis` falls back on where the AADT layer
    itself does not say a leg's record is one-way (Item 53).

    Args:
        entries: catalogue entries (:func:`load_catalogue`).
        groups: reporting corridors (:func:`load_reporting_corridors`).
        chains: ``{entry id: ChainResult}``, e.g. ``resolve_catalogue(...).attrs["chains"]``.
    """
    couplet_groups = {g.id for g in groups if g.one_way_couplet}
    out: set[int] = set()
    for e in entries:
        if e.corridor in couplet_groups and e.id in chains:
            out.update(int(s) for s in chains[e.id].segment_ids)
    return out


def load_catalogue(path) -> tuple[CorridorEntry, ...]:
    """Read and validate a corridor catalogue JSON file (see :func:`parse_catalogue`).

    The path is the caller's — nothing in this package knows where a catalogue
    lives (CLAUDE.md: no hardcoded file paths). ``scripts/d3_corridors.json`` is the
    District 3 one.
    """
    import json

    with open(path, "r", encoding="utf-8") as fh:
        return parse_catalogue(json.load(fh))


def load_reporting_corridors(path) -> tuple[ReportingCorridor, ...]:
    """Read the ``reporting_corridors`` block of a catalogue file, validated against
    its entries (:func:`parse_reporting_corridors`). ``()`` when the file declares
    none — an ungrouped catalogue ranks per direction, as before Item 40."""
    import json

    with open(path, "r", encoding="utf-8") as fh:
        return parse_reporting_corridors(json.load(fh))


# Resolution-table columns, in order.
CATALOGUE_COLUMNS = (
    "id", "name", "accepted", "reached_target", "stop_reason", "n_segments",
    "chain_miles", "requested_miles", "trim_start_miles", "trim_end_miles",
    "snap_start_feet", "snap_end_feet", "start_segment", "end_segment",
    "target_segment", "n_repaired_links", "corridor", "direction",
)
COVERAGE_COLUMNS = ("n_obs", "n_missing", "observed_miles", "missing_miles",
                    "miles_covered_fraction")


def resolve_catalogue(source, catalogue, *, observed=None, value: str | None = None,
                      min_coverage: float = DEFAULT_MIN_COVERAGE, projected_crs=None,
                      repairs=None, **build_kwargs) -> pd.DataFrame:
    """Resolve every catalogue entry through :func:`build_chain` and account for it.

    Args:
        source: an XD network GeoDataFrame or a shapefile/GeoParquet path, as
            :func:`build_chain` takes. It is projected **once** here and the
            projected frame is reused for every entry.
        catalogue: :func:`load_catalogue` entries (or anything
            :func:`parse_catalogue` accepts).
        observed: an export frame with a ``Segment ID`` column, or an iterable of
            observed segment ids — the coverage half of the acceptance test
            (:func:`chain_coverage`). ``None`` leaves the coverage columns absent
            and judges acceptance on the walk alone; ``attrs['coverage_evaluated']``
            says which happened.
        value: value column for the coverage count, as in :func:`chain_coverage`.
        min_coverage: the ``miles_covered_fraction`` an entry must **exceed** to be
            accepted (default 0.95).
        repairs: an optional ``NextXDSegI`` patch table (Item 38) applied **once**
            to the projected network here and carried into every entry's row as
            ``n_repaired_links``. ``attrs['repairs']`` records whether one was used
            and how large it was.
        projected_crs / build_kwargs: passed through to :func:`build_chain`
            (``k_candidates``, ``max_snap_feet``, ``max_steps``, ``candidates`` …).

    Returns:
        One row per entry, in catalogue order: :data:`CATALOGUE_COLUMNS` plus
        :data:`COVERAGE_COLUMNS` when ``observed`` is given. ``attrs`` carries
        ``chains`` (``{id: ChainResult}`` — feed it straight to
        :func:`screen.rank_corridors`), ``findings`` (the ids that were **not**
        accepted), ``n_entries`` / ``n_accepted``, ``min_coverage``,
        ``coverage_evaluated`` and the projected ``crs``.

    **Acceptance is per entry and explicit.** An entry either reaches its target and
    observes more than ``min_coverage`` of its in-extent miles, or it lands in
    ``findings`` carrying the walk's ``stop_reason``. Nothing raises and nothing
    falls back: an entry that will not resolve is a finding about the network or the
    endpoints — a break in ``NextXDSegI``, a snap onto the opposing carriageway, an
    extent that spans a genuine discontinuity (split it into two entries) — and
    substituting a geographic sort for it is exactly the error this replaces.
    """
    entries = catalogue if all(isinstance(e, CorridorEntry) for e in catalogue) \
        else parse_catalogue(catalogue)
    network = _as_network(source)
    proj = project_network(network, projected_crs)
    # Applied once here rather than per entry; ``repairs`` still goes down to
    # ``build_chain`` because that is what attributes the links each chain used.
    proj = apply_link_repairs(proj, repairs)

    rows, chains = [], {}
    for entry in entries:
        net_e, rep_e = proj, repairs
        if entry.links:
            # The entry's own links (Item 51) patch this walk only; they are reported
            # with the table's repairs in ``n_repaired_links``.
            own = pd.DataFrame({"segment": [a for a, _ in entry.links],
                                "new_next": [b for _, b in entry.links]})
            base = (pd.DataFrame(columns=["segment", "new_next"]) if repairs is None
                    else pd.DataFrame(list(_repair_map(repairs).items()),
                                      columns=["segment", "new_next"]))
            rep_e = pd.concat([base[~base["segment"].isin(own["segment"])], own],
                              ignore_index=True)
            net_e = apply_link_repairs(proj, own)
        chain = build_chain(net_e, entry.start_latlon, entry.end_latlon,
                            projected_crs=proj.crs, repairs=rep_e, **build_kwargs)
        chains[entry.id] = chain
        summary = chain.summary()
        row = {
            "id": entry.id,
            "name": entry.name,
            "accepted": bool(chain.reached_target),
            "reached_target": bool(chain.reached_target),
            "stop_reason": chain.stop_reason,
            "n_segments": chain.n_segments,
            "chain_miles": chain.chain_miles,
            "requested_miles": chain.requested_miles,
            "trim_start_miles": summary["trim_start_miles"],
            "trim_end_miles": summary["trim_end_miles"],
            "snap_start_feet": chain.snap_start_feet,
            "snap_end_feet": chain.snap_end_feet,
            "start_segment": chain.start_segment,
            "end_segment": chain.end_segment,
            "target_segment": chain.target_segment,
            "n_repaired_links": chain.n_repaired_links,
            "corridor": entry.corridor,
            "direction": entry.direction,
        }
        if observed is not None:
            cov = chain_coverage(chain, observed, value=value)
            row.update(
                n_obs=int(cov[NOBS_COL].sum()),
                n_missing=cov.attrs["n_missing"],
                observed_miles=cov.attrs["observed_miles"],
                missing_miles=cov.attrs["missing_miles"],
                miles_covered_fraction=cov.attrs["miles_covered_fraction"],
            )
            row["accepted"] = bool(
                chain.reached_target
                and row["miles_covered_fraction"] > float(min_coverage)
            )
        rows.append(row)

    cols = list(CATALOGUE_COLUMNS) + (list(COVERAGE_COLUMNS) if observed is not None else [])
    out = pd.DataFrame(rows, columns=cols)
    out.attrs = {
        "chains": chains,
        "findings": [r["id"] for r in rows if not r["accepted"]],
        "n_entries": len(rows),
        "n_accepted": int(out["accepted"].sum()) if len(out) else 0,
        "min_coverage": float(min_coverage),
        "coverage_evaluated": observed is not None,
        "repairs_applied": repairs is not None,
        "n_repairs": len(_repair_map(repairs)),
        "crs": str(proj.crs),
    }
    return out

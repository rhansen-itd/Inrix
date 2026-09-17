"""Corridor chain assembly from the XD network — snap, walk, trim, account.
(ROADMAP Item 28)

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

Compute only — no plotting, no GUI, no file paths (see CLAUDE.md). Needs the
``geo`` extra for the geometry work (imports are function-local so the package
still imports without it); :func:`chain_coverage` and :func:`chain_travel_time`
operate on an already-built :class:`ChainResult` and need only pandas.
"""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from .io import CORRIDOR_COL, DATETIME_COL, SEGMENT_COL
from .speed import corridor_travel_time, metric_columns

# Columns of the per-member frames (:meth:`ChainResult.frame`, :func:`chain_coverage`).
SEQ_COL = "sequence"                       # 1-based position along the chain
MILES_COL = "Miles"                        # whole-segment length (XD ``Miles``)
EXTENT_FRACTION_COL = "in_extent_fraction"  # fraction inside the requested extent
EXTENT_MILES_COL = "extent_miles"          # Miles * in_extent_fraction
OBSERVED_COL = "observed"                  # member has observations in the export
NOBS_COL = "n_obs"

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
                max_steps: int = 500, candidates=None, **load_kwargs) -> ChainResult:
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

    Returns:
        :class:`ChainResult`. Nothing raises on a chain that fails to connect —
        inspect ``reached_target`` / ``stop_reason``.
    """
    network = _as_network(source, **load_kwargs)
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

    best = None  # (reached, total_snap_ft, start_cand, end_cand, chain, reason)
    for s_id, s_ft, s_frac in starts:
        for e_id, e_ft, e_frac in ends:
            chain, reason = walk_chain(proj, s_id, e_id, max_steps=max_steps)
            reached = reason == "target"
            key = (not reached, s_ft + e_ft)
            if best is None or key < best[0]:
                best = (key, (s_id, s_ft, s_frac), (e_id, e_ft, e_frac), chain, reason)
    _, (s_id, s_ft, s_frac), (e_id, e_ft, e_frac), chain, reason = best
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
    )


def chain_between_segments(source, start_segment: int, end_segment: int, *,
                           projected_crs=None, max_steps: int = 500,
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
    network = _as_network(source, **load_kwargs)
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
    )


# ---------------------------------------------------------------------------
# Missing-segment accounting  (G8)
# ---------------------------------------------------------------------------
def chain_coverage(chain: ChainResult, observed, *, value: str | None = None) -> pd.DataFrame:
    """Which chain members an export actually observes — **no silent fallback**.

    Args:
        chain: the :class:`ChainResult` to account for.
        observed: an export frame with a ``Segment ID`` column, or any iterable of
            observed segment ids.
        value: when ``observed`` is a frame, count a row only where this column is
            non-NaN (matches ``speed.corridor_travel_time``'s value-aware
            completeness).

    Returns:
        One row per member in travel order: ``[sequence, Segment ID, Miles,
        in_extent_fraction, extent_miles, n_obs, observed]``. ``attrs`` carries the
        chain summary plus ``n_missing``, ``missing_miles`` (in-extent miles with
        no observations), ``observed_miles`` and ``miles_covered_fraction`` — so a
        report physically cannot reduce this to a "17/20" string.
    """
    if isinstance(observed, pd.DataFrame):
        if SEGMENT_COL not in observed.columns:
            raise ValueError(f"{SEGMENT_COL!r} column required in the observed frame.")
        sub = observed
        if value is not None:
            if value not in sub.columns:
                raise ValueError(f"Value column {value!r} not in the observed frame.")
            sub = sub[sub[value].notna()]
        counts = sub[SEGMENT_COL].astype("int64").value_counts()
    else:
        ids = pd.Series(list(observed), dtype="float64").dropna().astype("int64")
        counts = ids.value_counts()

    out = chain.frame()
    out[NOBS_COL] = [int(counts.get(sid, 0)) for sid in out[SEGMENT_COL]]
    out[OBSERVED_COL] = out[NOBS_COL] > 0

    missing_miles = float(out.loc[~out[OBSERVED_COL], EXTENT_MILES_COL].sum())
    observed_miles = float(out.loc[out[OBSERVED_COL], EXTENT_MILES_COL].sum())
    total = observed_miles + missing_miles
    out.attrs = {
        **chain.summary(),
        "n_missing": int((~out[OBSERVED_COL]).sum()),
        "missing_miles": missing_miles,
        "observed_miles": observed_miles,
        "miles_covered_fraction": (observed_miles / total) if total > 0 else float("nan"),
    }
    return out


# ---------------------------------------------------------------------------
# Chain travel time (the trim decision, applied)
# ---------------------------------------------------------------------------
def chain_travel_time(df: pd.DataFrame, chain: ChainResult, *, prorate: bool = True,
                      value: str | None = None, require_complete: bool = True,
                      label: str = CHAIN_LABEL, expected: str = "total",
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

    Everything else is :func:`speed.corridor_travel_time`: the **complete-set
    rule** applies over exactly the chain's members (``expected="total"`` — a chain
    is only level-comparable when the same whole set of segments is summed), and a
    missing member drops the timestamp rather than silently shortening the sum.
    Pair it with :func:`chain_coverage` to see *which* members are missing.

    Returns:
        One row per timestamp: ``[corridor_col, Date Time, <value>, n_segments,
        expected_segments, complete]`` plus ``Length(Miles)`` (the prorated
        ``requested_miles`` or the whole ``chain_miles``) and, when travel time is
        in minutes, space-mean ``Corridor Speed(miles/hour)``.
    """
    tt = value or metric_columns(df)["travel_time"]
    if tt is None:
        raise ValueError("No 'Travel Time(...)' column found.")
    if tt not in df.columns:
        raise ValueError(f"Value column {tt!r} not in df.")

    ids = list(chain.segment_ids)
    sub = df[df[SEGMENT_COL].isin(ids)].copy()
    if prorate:
        w = sub[SEGMENT_COL].astype("int64").map(chain.weights())
        sub[tt] = sub[tt] * w

    sub[corridor_col] = label
    out = corridor_travel_time(
        sub, corridor_col=corridor_col, value=tt, require_complete=require_complete,
        datetime_col=datetime_col, members=ids, expected=expected,
    )
    length = chain.requested_miles if prorate else chain.chain_miles
    out["Length(Miles)"] = length
    if df.attrs.get("units", {}).get("travel_time") == "Minutes":
        hours = out[tt] / 60.0
        out["Corridor Speed(miles/hour)"] = length / hours.where(hours > 0)
    out.attrs = {**dict(df.attrs), "chain": chain.summary(), "prorated": bool(prorate)}
    return out


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

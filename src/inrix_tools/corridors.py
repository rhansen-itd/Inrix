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
_ENTRY_FIELDS = ("id", "name", "start_latlon", "end_latlon", "description")
DEFAULT_MIN_COVERAGE = 0.95             # accepted entries must observe >95% of their miles


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
        unknown = [k for k in row if k not in _ENTRY_FIELDS and not str(k).startswith("_")]
        if unknown:
            raise ValueError(f"Catalogue entry {entry_id!r} has unknown field(s) {unknown}.")
        for field in ("name", "description"):
            if not str(row[field] or "").strip():
                raise ValueError(f"Catalogue entry {entry_id!r} has an empty {field!r}.")
        out.append(
            CorridorEntry(
                id=entry_id,
                name=str(row["name"]).strip(),
                start_latlon=_latlon(row["start_latlon"], entry_id=entry_id,
                                     field="start_latlon"),
                end_latlon=_latlon(row["end_latlon"], entry_id=entry_id,
                                   field="end_latlon"),
                description=str(row["description"]).strip(),
            )
        )
    return tuple(out)


def load_catalogue(path) -> tuple[CorridorEntry, ...]:
    """Read and validate a corridor catalogue JSON file (see :func:`parse_catalogue`).

    The path is the caller's — nothing in this package knows where a catalogue
    lives (CLAUDE.md: no hardcoded file paths). ``scripts/d3_corridors.json`` is the
    District 3 one.
    """
    import json

    with open(path, "r", encoding="utf-8") as fh:
        return parse_catalogue(json.load(fh))


# Resolution-table columns, in order.
CATALOGUE_COLUMNS = (
    "id", "name", "accepted", "reached_target", "stop_reason", "n_segments",
    "chain_miles", "requested_miles", "trim_start_miles", "trim_end_miles",
    "snap_start_feet", "snap_end_feet", "start_segment", "end_segment",
    "target_segment",
)
COVERAGE_COLUMNS = ("n_obs", "n_missing", "observed_miles", "missing_miles",
                    "miles_covered_fraction")


def resolve_catalogue(source, catalogue, *, observed=None, value: str | None = None,
                      min_coverage: float = DEFAULT_MIN_COVERAGE, projected_crs=None,
                      **build_kwargs) -> pd.DataFrame:
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

    rows, chains = [], {}
    for entry in entries:
        chain = build_chain(proj, entry.start_latlon, entry.end_latlon,
                            projected_crs=proj.crs, **build_kwargs)
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
        "crs": str(proj.crs),
    }
    return out

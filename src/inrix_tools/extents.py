"""Corridor extents, derived from the network (ROADMAP Items 45.1–45.2 and 46).

A corridor catalogue is a list of extents — "US-95 northbound, *from here to
there*" — and where those endpoints come from is the whole question. Hand-drawn,
they carry a district engineer's judgement and nothing else; they do not survive a
new export, and they do not scale past the one district somebody knows well. This
module derives them instead.

Three stages, each usable on its own:

1. **Chains** (Item 46). :func:`enumerate_mainline_chains` walks every numbered
   route into maximal directional chains off ``NextXDSegI``, and
   :func:`pair_chains` marries the two carriageways of a divided highway.
2. **Splits** (Item 45.1). :func:`detect_split_points` finds where an extent
   *should* end, on four orthogonal criteria:
   urban/rural transitions (FRC), highway-to-highway junctions, AADT volume
   step-changes (≥40% relative **and** ≥8,000 vpd absolute), and congestion
   discontinuities (TTI).
3. **Tiers** (Item 45.2). :func:`build_extent_tiers` cuts each chain at those
   splits into **Tier 1 Congested Core**, **Tier 2 Commuter Corridor** and
   **Tier 3 Regional Baseline**, so a ranking can show how much of a
   bottleneck's delay density a longer extent dilutes away.

:func:`generate_catalogue` runs all three and emits
``corridors.parse_catalogue`` entries whose ``description`` states the split that
ended them — a generated catalogue that cannot explain its own extents is the
hand-drawn one with the author's name removed.

Pure core: no hardcoded paths, no CLI, no DuckDB queries.
"""
from __future__ import annotations

import dataclasses
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import Enum

import geopandas as gpd
import numpy as np
import pandas as pd
from shapely.geometry import Point
from shapely.ops import linemerge


# ─── Constants ────────────────────────────────────────────────────────

# Criterion 1: Urban/Rural
URBAN_FRC_THRESHOLD = 3        # FRC <= 3 is "urban arterial or higher"
SPEED_DISCONTINUITY_MPH = 15   # Free-flow speed jump indicating urban/rural boundary

# Criterion 3: AADT (per direction since Item 54: the two-way 8000 vpd step, halved)
AADT_RELATIVE_GRADIENT = 0.40  # 40% relative change threshold
AADT_ABSOLUTE_STEP = 4000      # Minimum absolute vpd change, per direction

# Criterion 4: Congestion
TTI_CONGESTED = 1.20           # TTI threshold for "congested" side of discontinuity
TTI_FREEFLOW = 1.08            # TTI threshold for "free-flowing" side
CONGESTION_RUN_MIN_SEGS = 3    # Minimum consecutive segments to confirm discontinuity
RECURRENCE_DROP = 0.40         # Recurrence rate below which congestion "ends"
VHD_BOTTLENECK = 75.0          # vhd/mile indicating bottleneck (per-direction AADT)
VHD_FREEFLOW = 10.0            # vhd/mile indicating free-flow (per-direction AADT)

# Dilution
DILUTION_FACTOR_THRESHOLD = 5.0  # If Tier1 rate / Tier3 rate > 5, must partition


class SplitKind(str, Enum):
    """Classification of split point types."""
    URBAN_RURAL = "urban_rural"
    JUNCTION = "junction"
    AADT_STEP = "aadt_step"
    CONGESTION_DROP = "congestion_drop"
    # Item 50: where a congestion-grown extent stopped. These are not detected
    # ahead of time like the four above; the extent builder records them.
    CONGESTION_END = "congestion_end"
    DILUTION = "dilution"
    URBAN_BOUNDARY = "urban_boundary"
    CONTEXT_LIMIT = "context_limit"


class ExtentTier(str, Enum):
    CORE = "core"           # Tier 1: tight empirical hotspot
    COMMUTER = "commuter"   # Tier 2: functional trip extent
    REGIONAL = "regional"   # Tier 3: full highway facility


# ─── Data Structures ─────────────────────────────────────────────────

@dataclass(frozen=True)
class SplitPoint:
    """A detected split point along a highway chain."""
    segment_index: int
    """Index into the chain's segment list (split occurs BETWEEN index-1 and index)."""
    segment_id: int
    """XD segment ID at the split point."""
    kind: SplitKind
    """Type of split criterion that triggered this point."""
    score: float
    """Strength/confidence score (0.0–1.0). Higher = stronger signal."""
    details: dict = field(default_factory=dict)
    """Criterion-specific details (e.g., AADT values, TTI values, route names)."""


@dataclass(frozen=True)
class ExtentAlternative:
    """One corridor extent alternative at a specific tier."""
    tier: ExtentTier
    segment_ids: tuple[int, ...]
    """XD segment IDs in travel order."""
    miles: float
    """Total extent length in miles."""
    start_latlon: tuple[float, float]
    """(lat, lon) of extent start."""
    end_latlon: tuple[float, float]
    """(lat, lon) of extent end."""
    split_rationale: str
    """Human-readable explanation of why this extent was chosen."""
    bounding_splits: tuple[SplitPoint | None, SplitPoint | None] = (None, None)
    """(upstream_split, downstream_split) that define this extent's boundaries."""


@dataclass
class ChainAnalysis:
    """Complete analysis of a highway chain with split points and extent tiers."""
    route_number: str
    bearing: str
    chain_segment_ids: tuple[int, ...]
    chain_miles: float
    split_points: list[SplitPoint]
    tiers: dict[ExtentTier, list[ExtentAlternative]]
    """Multiple alternatives per tier (e.g., multiple Tier 1 cores on the same route)."""


# ─── Helpers ──────────────────────────────────────────────────────────

def _ensure_indexed(network: gpd.GeoDataFrame | pd.DataFrame) -> gpd.GeoDataFrame | pd.DataFrame:
    """Ensure DataFrame is indexed by XDSegID."""
    if network.index.name == "XDSegID":
        return network
    if "XDSegID" in network.columns:
        return network.set_index("XDSegID", drop=False)
    return network


def segment_endpoint(net_idx: pd.DataFrame, seg_id: int, *, end: bool = False) -> tuple[float, float]:
    """The (lat, lon) of a segment's start or end, **taken off its geometry**.

    XD's declared ``StartLat``/``EndLat`` and the segment's geometry do not always
    agree: on the District 6 export ``1187395985`` declares a start 294 m from its
    own line. A catalogue endpoint written from the declared value then snaps to
    whatever else is nearby — for that segment, a piece of SH-43 776 ft away — and
    the entry resolves onto the wrong road or not at all. So the geometry decides
    *where*, and the declared values decide only *which end*, by proximity, which
    also absorbs geometries digitised against the travel direction.
    """
    if seg_id not in net_idx.index:
        return 0.0, 0.0
    row = net_idx.loc[seg_id]
    if isinstance(row, pd.DataFrame):
        row = row.iloc[0]

    geom = row.get("geometry") if hasattr(row, "get") else None
    terminals = None
    if geom is not None and not geom.is_empty:
        try:
            coords = list(geom.coords)
        except NotImplementedError:      # MultiLineString
            coords = list(geom.geoms[0].coords) + list(geom.geoms[-1].coords)
        if coords:
            terminals = ((float(coords[0][1]), float(coords[0][0])),
                         (float(coords[-1][1]), float(coords[-1][0])))

    declared = {}
    for key, lat_col, lon_col in (("start", "StartLat", "StartLong"),
                                  ("end", "EndLat", "EndLong")):
        if lat_col in row and lon_col in row and pd.notna(row[lat_col]) and pd.notna(row[lon_col]):
            declared[key] = (float(row[lat_col]), float(row[lon_col]))

    want = "end" if end else "start"
    if terminals is None:
        return declared.get(want, (0.0, 0.0))
    if want in declared:
        target = declared[want]
        return min(terminals, key=lambda t: (t[0] - target[0]) ** 2 + (t[1] - target[1]) ** 2)
    return terminals[1] if end else terminals[0]


def _get_coords(net_idx: pd.DataFrame, seg_id: int, end: bool = False) -> tuple[float, float]:
    """Positional-argument alias for :func:`segment_endpoint`."""
    return segment_endpoint(net_idx, seg_id, end=end)


# ─── Split Point Detection ───────────────────────────────────────────

def detect_urban_rural_splits(
    chain_segments: Sequence[int],
    network: gpd.GeoDataFrame | pd.DataFrame,
) -> list[SplitPoint]:
    """Detect urban/rural boundary transitions along a chain."""
    if len(chain_segments) < 2:
        return []
    net = _ensure_indexed(network)
    splits: list[SplitPoint] = []

    for i in range(1, len(chain_segments)):
        seg_prev = chain_segments[i - 1]
        seg_curr = chain_segments[i]
        if seg_prev not in net.index or seg_curr not in net.index:
            continue

        row_prev = net.loc[seg_prev]
        row_curr = net.loc[seg_curr]
        if isinstance(row_prev, pd.DataFrame):
            row_prev = row_prev.iloc[0]
        if isinstance(row_curr, pd.DataFrame):
            row_curr = row_curr.iloc[0]

        frc_prev = row_prev.get("FRC", 5)
        frc_curr = row_curr.get("FRC", 5)
        frc_prev = int(frc_prev) if pd.notna(frc_prev) else 5
        frc_curr = int(frc_curr) if pd.notna(frc_curr) else 5

        # Check FRC transition (urban arterial/freeway <= 3 vs rural collector > 3)
        urban_prev = frc_prev <= URBAN_FRC_THRESHOLD
        urban_curr = frc_curr <= URBAN_FRC_THRESHOLD

        if urban_prev != urban_curr:
            # Confidence grows with the size of the FRC jump. The magnitude must be
            # taken before the offset, or the same physical boundary scores
            # differently depending on which way the chain runs through it — the NB
            # and SB halves of a couplet would disagree about the same corner.
            score = round(min(1.0, max(0.5, 1.0 - 0.1 * (abs(frc_prev - frc_curr) - 1))), 3)
            splits.append(SplitPoint(
                segment_index=i,
                segment_id=int(seg_curr),
                kind=SplitKind.URBAN_RURAL,
                score=score,
                details={
                    "frc_before": frc_prev,
                    "frc_after": frc_curr,
                    "postal_before": str(row_prev.get("PostalCode", "")),
                    "postal_after": str(row_curr.get("PostalCode", "")),
                },
            ))

    return splits


def incoming_route_map(network: gpd.GeoDataFrame | pd.DataFrame) -> dict[int, set[str]]:
    """``{segment: {route numbers arriving at it}}`` — the junction adjacency.

    Build this **once** and hand it to :func:`detect_junction_splits`. It is a
    property of the network, not of the chain being analysed, and rebuilding it
    per chain costs ~0.1 s over a 5,000-row district network — about 4.6 s per
    district once Item 46 started calling :func:`analyse_chain` for both
    directions of every mainline (ROADMAP Item 47).
    """
    net = _ensure_indexed(network)
    out: dict[int, set[str]] = {}
    if "NextXDSegI" not in net.columns or "RoadNumber" not in net.columns:
        return out
    nxt = pd.to_numeric(net["NextXDSegI"], errors="coerce")
    routes = net["RoadNumber"].astype(str).str.strip()
    keep = nxt.notna() & routes.ne("") & routes.ne("nan") & routes.ne("None")
    for target, route in zip(nxt[keep].astype("int64"), routes[keep]):
        out.setdefault(int(target), set()).add(route)
    return out


def detect_junction_splits(
    chain_segments: Sequence[int],
    network: gpd.GeoDataFrame | pd.DataFrame,
    full_network: gpd.GeoDataFrame | pd.DataFrame | None = None,
    *,
    incoming_routes: dict[int, set[str]] | None = None,
) -> list[SplitPoint]:
    """Detect major highway-to-highway junctions along a chain.

    ``incoming_routes`` is :func:`incoming_route_map` of ``full_network``; pass it
    when analysing many chains over one network so it is built once.
    """
    if len(chain_segments) < 2:
        return []
    net = _ensure_indexed(network)
    splits: list[SplitPoint] = []

    if incoming_routes is None:
        incoming_routes = incoming_route_map(
            full_network if full_network is not None else net)

    for i in range(1, len(chain_segments)):
        seg_prev = chain_segments[i - 1]
        seg_curr = chain_segments[i]
        if seg_prev not in net.index or seg_curr not in net.index:
            continue

        row_prev = net.loc[seg_prev]
        row_curr = net.loc[seg_curr]
        if isinstance(row_prev, pd.DataFrame):
            row_prev = row_prev.iloc[0]
        if isinstance(row_curr, pd.DataFrame):
            row_curr = row_curr.iloc[0]

        rn_prev = str(row_prev.get("RoadNumber", "")).strip() if pd.notna(row_prev.get("RoadNumber")) else ""
        rn_curr = str(row_curr.get("RoadNumber", "")).strip() if pd.notna(row_curr.get("RoadNumber")) else ""

        # Case 1: Route number changes along the chain — unless the street runs
        # straight on (Item 51): Yellowstone Hwy renumbering from I-15 BL to US-20 BR
        # is one road, not a junction.
        same_street = False
        if rn_prev and rn_curr and rn_prev != rn_curr:
            from .couplets import street_key
            n_prev, n_curr = row_prev.get("RoadName"), row_curr.get("RoadName")
            same_street = (pd.notna(n_prev) and pd.notna(n_curr) and str(n_prev).strip()
                           and street_key(n_prev).lower() == street_key(n_curr).lower())
        if rn_prev and rn_curr and rn_prev != rn_curr and not same_street:
            splits.append(SplitPoint(
                segment_index=i,
                segment_id=int(seg_curr),
                kind=SplitKind.JUNCTION,
                score=1.0,
                details={"type": "route_change", "route_before": rn_prev, "route_after": rn_curr},
            ))
            continue

        # Case 2: Converging/diverging routes at junction node
        crossing = incoming_routes.get(seg_curr, set())
        chain_rn = rn_curr or rn_prev
        other_routes = crossing - {chain_rn}
        if other_routes:
            splits.append(SplitPoint(
                segment_index=i,
                segment_id=int(seg_curr),
                kind=SplitKind.JUNCTION,
                score=0.8,
                details={"type": "intersecting_route", "crossing_routes": sorted(other_routes)},
            ))

    return splits


def detect_aadt_splits(
    chain_segments: Sequence[int],
    network: gpd.GeoDataFrame | pd.DataFrame,
    aadt_col: str = "AADT",
) -> list[SplitPoint]:
    """Detect AADT volume step-changes along a chain."""
    if len(chain_segments) < 2:
        return []
    net = _ensure_indexed(network)
    if aadt_col not in net.columns:
        return []

    splits: list[SplitPoint] = []
    for i in range(1, len(chain_segments)):
        seg_prev = chain_segments[i - 1]
        seg_curr = chain_segments[i]
        if seg_prev not in net.index or seg_curr not in net.index:
            continue

        row_prev = net.loc[seg_prev]
        row_curr = net.loc[seg_curr]
        if isinstance(row_prev, pd.DataFrame):
            row_prev = row_prev.iloc[0]
        if isinstance(row_curr, pd.DataFrame):
            row_curr = row_curr.iloc[0]

        aadt_prev = row_prev.get(aadt_col)
        aadt_curr = row_curr.get(aadt_col)
        if pd.isna(aadt_prev) or pd.isna(aadt_curr):
            continue

        try:
            v_prev = float(aadt_prev)
            v_curr = float(aadt_curr)
        except (ValueError, TypeError):
            continue

        if v_prev <= 0 and v_curr <= 0:
            continue

        abs_diff = abs(v_curr - v_prev)
        denom = max(v_prev, v_curr)
        rel_diff = abs_diff / denom if denom > 0 else 0.0

        if rel_diff >= AADT_RELATIVE_GRADIENT and abs_diff >= AADT_ABSOLUTE_STEP:
            score = round(min(1.0, rel_diff), 3)
            splits.append(SplitPoint(
                segment_index=i,
                segment_id=int(seg_curr),
                kind=SplitKind.AADT_STEP,
                score=score,
                details={
                    "aadt_before": v_prev,
                    "aadt_after": v_curr,
                    "relative_change": round(rel_diff, 3),
                    "absolute_change": round(abs_diff, 0),
                },
            ))

    return splits


def detect_congestion_splits(
    chain_segments: Sequence[int],
    recurrence: pd.DataFrame | None = None,
    screen_data: pd.DataFrame | None = None,
    window: str = "am",
) -> list[SplitPoint]:
    """Detect congestion discontinuities along a chain."""
    n = len(chain_segments)
    k = CONGESTION_RUN_MIN_SEGS
    if n < 2 * k:
        # If chain has fewer than 2*k segments, lower k to 1 or 2
        k = max(1, n // 3)
    if n < 2:
        return []

    # Build TTI lookup
    tti_lookup: dict[int, float] = {}
    tti_col = f"{window}_mean_tti"

    if recurrence is not None:
        rec = recurrence.set_index("Segment ID") if "Segment ID" in recurrence.columns and recurrence.index.name != "Segment ID" else recurrence
        if tti_col in rec.columns:
            for sid in chain_segments:
                if sid in rec.index:
                    val = rec.loc[sid, tti_col]
                    if isinstance(val, pd.Series):
                        val = val.iloc[0]
                    if pd.notna(val):
                        tti_lookup[sid] = float(val)

    if not tti_lookup and screen_data is not None:
        scr = screen_data.set_index("Segment ID") if "Segment ID" in screen_data.columns and screen_data.index.name != "Segment ID" else screen_data
        speed_col = f"{window}_speed"
        ref_col = "ref_speed"
        if speed_col in scr.columns and ref_col in scr.columns:
            for sid in chain_segments:
                if sid in scr.index:
                    row = scr.loc[sid]
                    if isinstance(row, pd.DataFrame):
                        row = row.iloc[0]
                    spd = row.get(speed_col)
                    ref = row.get(ref_col)
                    if pd.notna(spd) and pd.notna(ref) and float(spd) > 0:
                        tti_lookup[sid] = float(ref) / float(spd)

    if not tti_lookup:
        return []

    splits: list[SplitPoint] = []
    for i in range(k, n - k + 1):
        before_segs = chain_segments[max(0, i - k):i]
        after_segs = chain_segments[i:min(n, i + k)]

        ttis_before = [tti_lookup[s] for s in before_segs if s in tti_lookup]
        ttis_after = [tti_lookup[s] for s in after_segs if s in tti_lookup]

        if not ttis_before or not ttis_after:
            continue

        mean_before = float(np.mean(ttis_before))
        mean_after = float(np.mean(ttis_after))

        # Congested-to-freeflow drop
        if mean_before >= TTI_CONGESTED and mean_after <= TTI_FREEFLOW:
            score = round(min(1.0, (mean_before - mean_after) / mean_before), 3)
            splits.append(SplitPoint(
                segment_index=i,
                segment_id=int(chain_segments[i]),
                kind=SplitKind.CONGESTION_DROP,
                score=score,
                details={
                    "transition": "congested_to_freeflow",
                    "tti_before": round(mean_before, 2),
                    "tti_after": round(mean_after, 2),
                },
            ))
        # Freeflow-to-congested rise (onset of bottleneck)
        elif mean_before <= TTI_FREEFLOW and mean_after >= TTI_CONGESTED:
            score = round(min(1.0, (mean_after - mean_before) / mean_after), 3)
            splits.append(SplitPoint(
                segment_index=i,
                segment_id=int(chain_segments[i]),
                kind=SplitKind.CONGESTION_DROP,
                score=score,
                details={
                    "transition": "freeflow_to_congested",
                    "tti_before": round(mean_before, 2),
                    "tti_after": round(mean_after, 2),
                },
            ))

    return splits


def detect_split_points(
    chain_segments: Sequence[int],
    network: gpd.GeoDataFrame | pd.DataFrame,
    *,
    full_network: gpd.GeoDataFrame | pd.DataFrame | None = None,
    recurrence: pd.DataFrame | None = None,
    screen_data: pd.DataFrame | None = None,
    window: str = "am",
    incoming_routes: dict[int, set[str]] | None = None,
) -> list[SplitPoint]:
    """Detect all split points along a chain, combining all four criteria.

    ``incoming_routes`` is :func:`incoming_route_map`, built once by the caller
    when many chains are analysed over one network.
    """
    splits: list[SplitPoint] = []
    splits.extend(detect_urban_rural_splits(chain_segments, network))
    splits.extend(detect_junction_splits(chain_segments, network, full_network,
                                         incoming_routes=incoming_routes))
    splits.extend(detect_aadt_splits(chain_segments, network))
    splits.extend(detect_congestion_splits(chain_segments, recurrence, screen_data, window))

    # Sort by position along chain, then score descending
    splits.sort(key=lambda s: (s.segment_index, -s.score))
    return splits


# ─── Split reporting helpers ─────────────────────────────────────────

_SPLIT_PHRASE = {
    SplitKind.URBAN_RURAL: "an urban/rural FRC transition",
    SplitKind.JUNCTION: "a highway junction",
    SplitKind.AADT_STEP: "an AADT step-change",
    SplitKind.CONGESTION_DROP: "a congestion discontinuity",
    SplitKind.CONGESTION_END: "the end of the congestion",
    SplitKind.DILUTION: "the dilution limit",
    SplitKind.URBAN_BOUNDARY: "the urban-area boundary",
    SplitKind.CONTEXT_LIMIT: "the context-length limit",
}


def describe_split(split: SplitPoint | None, fallback: str = "the chain end") -> str:
    """One clause saying why an extent ends where it does.

    This is the whole point of carrying ``bounding_splits``: an extent whose
    boundary has no stated reason is a bounding box with better manners
    (``corridors.parse_catalogue`` refuses an entry with an empty description for
    the same reason). ``None`` means the extent runs to the end of the chain,
    which is itself the reason.
    """
    if split is None:
        return fallback
    phrase = _SPLIT_PHRASE.get(split.kind, str(split.kind))
    d = split.details or {}
    if split.kind is SplitKind.JUNCTION:
        if d.get("type") == "route_change":
            detail = f"{d.get('route_before')} -> {d.get('route_after')}"
        else:
            detail = "crossing " + "/".join(str(r) for r in d.get("crossing_routes", [])) or "crossing route"
    elif split.kind is SplitKind.AADT_STEP:
        detail = (f"{d.get('aadt_before', 0):,.0f} -> {d.get('aadt_after', 0):,.0f} vpd, "
                  f"{100 * float(d.get('relative_change', 0.0)):.0f}%")
    elif split.kind is SplitKind.URBAN_RURAL:
        detail = f"FRC {d.get('frc_before')} -> {d.get('frc_after')}"
    elif split.kind is SplitKind.CONGESTION_DROP:
        detail = (f"{d.get('transition', '')}, TTI {d.get('tti_before')} -> {d.get('tti_after')}")
    elif split.kind in (SplitKind.CONGESTION_END, SplitKind.DILUTION,
                        SplitKind.URBAN_BOUNDARY, SplitKind.CONTEXT_LIMIT):
        detail = str(d.get("detail", ""))
    else:
        detail = ""
    return f"{phrase} ({detail})" if detail else phrase


def _contiguous_runs(indices: Sequence[int], gap_tolerance: int) -> list[tuple[int, int]]:
    """Group sorted indices into ``[start, stop)`` runs, bridging gaps of at most
    ``gap_tolerance`` missing indices."""
    if not indices:
        return []
    ordered = sorted(set(int(i) for i in indices))
    runs: list[tuple[int, int]] = []
    start = prev = ordered[0]
    for i in ordered[1:]:
        if i - prev - 1 > gap_tolerance:
            runs.append((start, prev + 1))
            start = i
        prev = i
    runs.append((start, prev + 1))
    return runs


def _nearest_split(split_points: Sequence[SplitPoint], index: int,
                   max_distance: int = 2) -> SplitPoint | None:
    """The strongest split within ``max_distance`` segments of ``index``, if any."""
    near = [s for s in split_points if abs(s.segment_index - index) <= max_distance]
    if not near:
        return None
    return max(near, key=lambda s: (s.score, -abs(s.segment_index - index)))


# ─── Multi-Scale Extent Construction ─────────────────────────────────

def build_extent_tiers(
    chain_segments: Sequence[int],
    network: gpd.GeoDataFrame | pd.DataFrame,
    split_points: Sequence[SplitPoint],
    *,
    congestion_runs: Sequence | None = None,
    recurrence: pd.DataFrame | None = None,
    window: str = "am",
    min_core_miles: float = 0.0,
    core_gap_tolerance: int = 2,
) -> dict[ExtentTier, list[ExtentAlternative]]:
    """Construct Tier 1/2/3 extent alternatives from split points and congestion data.

    Args:
        min_core_miles: drop a Tier 1 core shorter than this (a signal queue, not
            a corridor). ``0.0`` keeps every run, as before Item 46.
        core_gap_tolerance: how many consecutive *uncongested* segments a Tier 1
            core may bridge before it is reported as two cores.
    """
    if not chain_segments:
        return {
            ExtentTier.CORE: [],
            ExtentTier.COMMUTER: [],
            ExtentTier.REGIONAL: [],
        }

    net = _ensure_indexed(network)
    n = len(chain_segments)

    def _subchain_miles(segs: Sequence[int]) -> float:
        m = 0.0
        for s in segs:
            if s in net.index:
                row = net.loc[s]
                if isinstance(row, pd.DataFrame):
                    row = row.iloc[0]
                val = row.get("Miles", 0.0)
                if pd.notna(val):
                    m += float(val)
        return round(m, 3)

    # 1. Tier 3: Regional Baseline (full chain)
    t3_start = _get_coords(net, chain_segments[0], end=False)
    t3_end = _get_coords(net, chain_segments[-1], end=True)
    t3_miles = _subchain_miles(chain_segments)
    t3_alt = ExtentAlternative(
        tier=ExtentTier.REGIONAL,
        segment_ids=tuple(chain_segments),
        miles=t3_miles,
        start_latlon=t3_start,
        end_latlon=t3_end,
        split_rationale=f"Full regional facility ({t3_miles:.2f} mi, {n} segments)",
        bounding_splits=(None, None),
    )

    # 2. Tier 1: Congested Core
    core_alts: list[ExtentAlternative] = []
    chain_set = set(chain_segments)
    chain_idx_map = {sid: i for i, sid in enumerate(chain_segments)}

    # Attempt 1: From congestion runs
    if congestion_runs:
        for run in congestion_runs:
            overlap = [s for s in run.segment_ids if s in chain_set]
            if len(overlap) >= 2:
                idxs = [chain_idx_map[s] for s in overlap]
                start_i = min(idxs)
                end_i = max(idxs) + 1
                core_segs = chain_segments[start_i:end_i]
                c_miles = _subchain_miles(core_segs)
                c_start = _get_coords(net, core_segs[0], end=False)
                c_end = _get_coords(net, core_segs[-1], end=True)
                core_alts.append(ExtentAlternative(
                    tier=ExtentTier.CORE,
                    segment_ids=tuple(core_segs),
                    miles=c_miles,
                    start_latlon=c_start,
                    end_latlon=c_end,
                    split_rationale=f"Empirical congestion run ({c_miles:.2f} mi, TTI {getattr(run, 'mean_tti', 0.0):.2f})",
                ))

    # Attempt 2: From recurrence/TTI data.
    #
    # The congested indices are grouped into *contiguous runs* rather than spanned
    # min-to-max. Taking the extremes looks the same on a chain with one bottleneck
    # and is badly wrong on a chain with two: US-95 congested in Coeur d'Alene and
    # again 60 miles north in Bonners Ferry would yield a single "core" covering the
    # free-flowing 60 miles between them — the exact dilution Tier 1 exists to avoid.
    if not core_alts and recurrence is not None:
        rec = recurrence.set_index("Segment ID") if "Segment ID" in recurrence.columns and recurrence.index.name != "Segment ID" else recurrence
        tti_col = f"{window}_mean_tti"
        if tti_col in rec.columns:
            congested_idxs = []
            for i, sid in enumerate(chain_segments):
                if sid in rec.index:
                    val = rec.loc[sid, tti_col]
                    if isinstance(val, pd.Series):
                        val = val.iloc[0]
                    if pd.notna(val) and float(val) >= TTI_CONGESTED:
                        congested_idxs.append(i)

            for start_i, end_i in _contiguous_runs(congested_idxs, core_gap_tolerance):
                core_segs = chain_segments[start_i:end_i]
                c_miles = _subchain_miles(core_segs)
                if c_miles < min_core_miles:
                    continue
                core_alts.append(ExtentAlternative(
                    tier=ExtentTier.CORE,
                    segment_ids=tuple(core_segs),
                    miles=c_miles,
                    start_latlon=_get_coords(net, core_segs[0], end=False),
                    end_latlon=_get_coords(net, core_segs[-1], end=True),
                    split_rationale=(
                        f"Recurrent bottleneck core ({c_miles:.2f} mi, "
                        f"{len(core_segs)} segments at {window.upper()} TTI >= {TTI_CONGESTED})"
                    ),
                    bounding_splits=(_nearest_split(split_points, start_i),
                                     _nearest_split(split_points, end_i)),
                ))
            core_alts.sort(key=lambda a: -a.miles)

    # Fallback Tier 1: If no congestion data, use split points or middle 30-50%
    if not core_alts:
        if split_points:
            s_idx = split_points[0].segment_index
            core_segs = chain_segments[:s_idx] if s_idx > 0 else chain_segments[:max(1, n // 2)]
        else:
            core_segs = chain_segments[:max(1, n // 2)]
        c_miles = _subchain_miles(core_segs)
        core_alts.append(ExtentAlternative(
            tier=ExtentTier.CORE,
            segment_ids=tuple(core_segs),
            miles=c_miles,
            start_latlon=_get_coords(net, core_segs[0], end=False),
            end_latlon=_get_coords(net, core_segs[-1], end=True),
            split_rationale=f"Primary urban core extent ({c_miles:.2f} mi)",
        ))

    # 3. Tier 2: Commuter Corridor
    # Expand from the primary Tier 1 core to surrounding junctions or urban/rural boundaries
    primary_core = core_alts[0]
    core_start_idx = chain_idx_map.get(primary_core.segment_ids[0], 0)
    core_end_idx = chain_idx_map.get(primary_core.segment_ids[-1], n - 1)

    # Find split points upstream of core and downstream of core
    upstream_splits = [s for s in split_points if s.segment_index <= core_start_idx and s.kind in (SplitKind.JUNCTION, SplitKind.URBAN_RURAL, SplitKind.AADT_STEP)]
    downstream_splits = [s for s in split_points if s.segment_index > core_end_idx and s.kind in (SplitKind.JUNCTION, SplitKind.URBAN_RURAL, SplitKind.AADT_STEP)]

    commuter_start_i = upstream_splits[-1].segment_index if upstream_splits else 0
    commuter_end_i = downstream_splits[0].segment_index if downstream_splits else n

    # Ensure commuter extent is strictly at least as long as core
    commuter_start_i = min(commuter_start_i, core_start_idx)
    commuter_end_i = max(commuter_end_i, core_end_idx + 1)
    commuter_segs = chain_segments[commuter_start_i:commuter_end_i]
    comm_miles = _subchain_miles(commuter_segs)

    up_split = upstream_splits[-1] if upstream_splits else None
    down_split = downstream_splits[0] if downstream_splits else None
    commuter_alt = ExtentAlternative(
        tier=ExtentTier.COMMUTER,
        segment_ids=tuple(commuter_segs),
        miles=comm_miles,
        start_latlon=_get_coords(net, commuter_segs[0], end=False),
        end_latlon=_get_coords(net, commuter_segs[-1], end=True),
        split_rationale=(
            f"Commuter extent between regional boundaries ({comm_miles:.2f} mi); "
            f"stitched outward from the core to "
            f"{describe_split(up_split, 'chain start')} and "
            f"{describe_split(down_split, 'chain end')}"
        ),
        bounding_splits=(up_split, down_split),
    )

    return {
        ExtentTier.CORE: core_alts,
        ExtentTier.COMMUTER: [commuter_alt],
        ExtentTier.REGIONAL: [t3_alt],
    }


def analyse_chain(
    chain_segments: Sequence[int],
    network: gpd.GeoDataFrame | pd.DataFrame,
    *,
    route_number: str = "",
    bearing: str = "",
    full_network: gpd.GeoDataFrame | pd.DataFrame | None = None,
    recurrence: pd.DataFrame | None = None,
    screen_data: pd.DataFrame | None = None,
    congestion_runs: Sequence | None = None,
    window: str = "am",
    min_core_miles: float = 0.0,
    core_gap_tolerance: int = 2,
    incoming_routes: dict[int, set[str]] | None = None,
) -> ChainAnalysis:
    """Complete chain analysis: detect splits and build tier alternatives.

    ``incoming_routes`` is :func:`incoming_route_map`; pass it when analysing many
    chains over one network (:func:`generate_catalogue` does).
    """
    splits = detect_split_points(
        chain_segments, network,
        full_network=full_network,
        recurrence=recurrence,
        screen_data=screen_data,
        window=window,
        incoming_routes=incoming_routes,
    )
    tiers = build_extent_tiers(
        chain_segments, network, splits,
        congestion_runs=congestion_runs,
        recurrence=recurrence,
        window=window,
        min_core_miles=min_core_miles,
        core_gap_tolerance=core_gap_tolerance,
    )

    net = _ensure_indexed(network)
    miles_list = []
    for s in chain_segments:
        if s in net.index:
            row = net.loc[s]
            if isinstance(row, pd.DataFrame):
                row = row.iloc[0]
            val = row.get("Miles", 0.0)
            if pd.notna(val):
                miles_list.append(float(val))

    return ChainAnalysis(
        route_number=route_number,
        bearing=bearing,
        chain_segment_ids=tuple(chain_segments),
        chain_miles=round(sum(miles_list), 3),
        split_points=splits,
        tiers=tiers,
    )


def dilution_factor(
    tier1_vhd_per_mile: float,
    tier3_vhd_per_mile: float,
) -> float:
    """Compute dilution factor: ratio of Tier 1 to Tier 3 VHD/mile."""
    if pd.isna(tier3_vhd_per_mile) or tier3_vhd_per_mile <= 0:
        return float("inf")
    return tier1_vhd_per_mile / tier3_vhd_per_mile


def compare_tiers(
    tiers: dict[ExtentTier, list[ExtentAlternative]],
    ranking_data: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """Compare extent alternatives side-by-side."""
    rows = []
    for tier, alts in tiers.items():
        for i, alt in enumerate(alts):
            row = {
                "tier": tier.value if hasattr(tier, "value") else str(tier),
                "alternative_index": i,
                "miles": alt.miles,
                "n_segments": len(alt.segment_ids),
                "rationale": alt.split_rationale,
            }
            rows.append(row)
    return pd.DataFrame(rows)


# ─── Mainline chain enumeration (ROADMAP Item 46) ────────────────────
#
# Item 45 built the split criteria and the tier construction above; nothing
# called them, because the statewide catalogues were still hand-drawn lat/lon
# hints. What was missing between the two was the *input*: the set of chains to
# analyse. That is what this section supplies — the route mainlines read off the
# network's own topology, so a re-run on a new export proposes its own extents
# instead of inheriting last session's coordinates.

ROUTE_LABEL_RE = re.compile(r"^(I|US|ID|SH|SR)-(\d+)", re.IGNORECASE)
"""``RoadName`` values that state their own route band (``US-95``, ``I-90 W``)."""

MIN_CHAIN_MILES = 1.0
"""A chain shorter than this is a stub, not a mainline, and is not analysed."""

BASELINE_QUANTILES = (0.10, 0.15)
"""Weekday travel-time percentiles the baseline screen carries (Item 50)."""

MIN_CORE_MILES = 0.75
"""A congested core shorter than this is a signal queue (Item 43's ~3-mile rule
applies to *accepted corridors*; this is the floor for a chain being worth
tiering at all)."""

PAIR_MAX_MEAN_SEP_M = 200.0
"""Two chains are the same facility's opposing carriageways only if their mean
lateral separation is under this. Beyond it they are two parts of one route
that happen to run in opposite directions (US-95 north of Sandpoint vs. south
of Coeur d'Alene), not a directional pair."""

_DIR_LABEL = {"N": "NB", "S": "SB", "E": "EB", "W": "WB"}
_OPPOSITE = {"N": "S", "S": "N", "E": "W", "W": "E"}


@dataclass(frozen=True)
class MainlineChain:
    """One maximal directional run of a state route's segments in travel order.

    Built by walking ``NextXDSegI`` **within one route's segments**, so the chain stays
    on one carriageway (``NextXDSegI`` is directional) and does not wander onto the
    cross-route at a junction. A divided highway therefore yields two chains — that is
    the point; they are paired by :func:`pair_chains`.

    Since Item 51 a route's segments include its **concurrent** ones (ITD membership plus
    INRIX ``RoadList``), a chain is **joined where its route turns** at a junction the
    link doesn't follow, and chains are **merged where only the number changes** along
    one street — so SH-8 through Moscow and Idaho Falls' Yellowstone Hwy are each one
    chain. ``route_numbers`` lists every route the chain walked, most miles first.
    """

    route_number: str
    bearing: str
    """Majority cardinal bearing (``N``/``S``/``E``/``W``)."""
    segment_ids: tuple[int, ...]
    miles: float
    road_name: str
    """Most common ``RoadName`` along the chain."""
    route_label: str
    """Display band — ``US-95``, ``I-90``, ``SH-55``."""
    localities: tuple[str, ...]
    """``PostalCode`` values along the chain, most frequent first."""
    counties: tuple[str, ...]
    route_numbers: tuple[str, ...] = ()
    """Every route the chain walked (a merge across a renumbering walks several)."""
    route_labels: tuple[str, ...] = ()
    """``route_label`` for each of ``route_numbers``."""
    joins: tuple[dict, ...] = ()
    """How the chain was assembled beyond ``NextXDSegI``: each junction join and
    renumbering merge, for the audit (Item 51)."""

    @property
    def n_segments(self) -> int:
        return len(self.segment_ids)

    @property
    def direction(self) -> str:
        """``NB``/``SB``/``EB``/``WB``, or the raw bearing if it is not cardinal."""
        return _DIR_LABEL.get(self.bearing, self.bearing)

    @property
    def routes(self) -> tuple[str, ...]:
        return self.route_numbers or (self.route_number,)


def route_label(route_number: str, road_names: Sequence[str], frc: int | None = None) -> str:
    """Name a route's band from what the network calls it.

    The route *number* alone cannot say whether ``95`` is US-95 or SH-95, and
    guessing from digit count (as the first couplet draft did) gets SH-55 wrong.
    ``RoadName`` states it — ``US-95``, ``I-90 W``, ``ID-3`` — so read it there
    and normalise ``ID-``/``SR-`` to this project's ``SH-``. When every name on
    the route is a street name (``Burke Rd``), fall back to FRC: 0 is an
    interstate, anything else a state highway.
    """
    num = str(route_number).strip()
    for name in road_names:
        m = ROUTE_LABEL_RE.match(str(name).strip())
        if m and m.group(2) == num:
            band = m.group(1).upper()
            return f"{'SH' if band in ('ID', 'SR') else band}-{num}"
    if frc is not None and int(frc) == 0:
        return f"I-{num}"
    return f"SH-{num}"


# ─── Chains across route-numbering changes (ROADMAP Item 51) ─────────
#
# Item 46 walked one ``RoadNumber`` at a time, and a road whose number changes along
# its length came out as several short chains, each of which had to clear the chain
# minimum and find its own core. Three things are needed to walk a road as a road:
#
# 1. **Concurrency.** ITD's SHS records one route per road, so the US-95 couplet in
#    Moscow is route 95 only, although SH-8 runs on it; INRIX's ``RoadList`` names
#    ``ID-8`` there. A route's segments are its membership routes plus the routes its
#    ``RoadList`` names, for segments on the state system.
# 2. **Junction joins.** Where a route turns and the link runs straight on, the route's
#    walk ends at the junction and resumes on a segment that nothing in the route
#    links into (DATA_FORMAT trap 4: Payette 16th St, Lewiston's levee, SH-8 onto the
#    couplet). A tail is joined to such a head when the head starts on the tail's last
#    segment and the turn is not a reversal. Where the route's own SHS line runs past
#    the junction as a short stub, the head is joined at the junction and the stub
#    left behind — but only when the milepost gap shows it (the head's run comes back
#    to the route's line further along).
# 3. **Renumbering merges.** A route that ends where another begins, on the same
#    street, is one road (Yellowstone Hwy is I-15 BL, US-20 BR, US-26 and US-91 along
#    its length; Broadway east of I-15 is I-15 BL).

ROUTE_JOIN_TOL_M = 20.0
"""How close a head's start must lie to the tail's last segment to be its continuation."""
ROUTE_JOIN_MIN_FRACTION = 0.5
"""...and how far along that segment: a head leaving its first half is a side road."""
ROUTE_JOIN_MAX_TURN_DEG = 120.0
"""A join may turn a corner (90 degrees) but not reverse onto the other carriageway."""
ROUTE_STUB_MILES = 0.3
"""The most of a route's own run that a junction join may leave behind as a stub."""
ROUTE_JOIN_MP_TOL = 0.25
"""Where both sides lie on one SHS line, their mileposts must agree to this (miles)."""
ROUTE_BRIDGE_MAX_MILES = 2.0
"""A stub join must return to the route's own line within this many chain miles."""
MERGE_MAX_TURN_DEG = 60.0
"""A renumbering merge continues straight on; it does not turn a corner."""
SHARED_CORE_MAX = 0.5
"""A core with this share of its miles already in a stronger facility's core (or its
Tier 2) is that facility's queue seen from a concurrent route, and is absorbed. Below
it the core stands, with a ``shares`` flag (Item 51)."""
MERGE_MIN_STREET_MILES = 0.5
"""A merge that cuts a chain (a route turning off the street, or arriving onto it)
needs the street to run on for this far on both sides. At Troy, SH-8 eastbound on S
Main St becomes "ID-8" where SH-99 starts down S Main St for 0.04 mi: that is a route
changing its name, not a street changing its number. Yellowstone Hwy runs 4.9 mi."""


def segment_route_sets(network: gpd.GeoDataFrame | pd.DataFrame) -> dict[int, frozenset[str]]:
    """``{segment: routes it belongs to}`` over the network's state-route segments.

    With ITD membership applied (``routes.apply_route_membership`` adds
    ``itd_routes``), a segment on the system belongs to its membership routes plus the
    routes its ``RoadList`` names: the SHS records one route per road, so concurrency
    comes from INRIX. A segment membership leaves on no route is not a member of any,
    whatever ``RoadList`` says. Without membership the ``RoadNumber`` alone decides
    (Item 46's reading). Ramps (``SlipRoad`` 1) are never members.
    """
    from .routes import ITD_ROUTES_COL, road_list_routes

    net = network
    ids = net["XDSegID"] if "XDSegID" in net.columns else pd.Series(net.index, index=net.index)
    slip = (net["SlipRoad"].astype(str).str.strip().isin(["1", "1.0"])
            if "SlipRoad" in net.columns else pd.Series(False, index=net.index))
    out: dict[int, frozenset[str]] = {}
    if ITD_ROUTES_COL in net.columns:
        road_list = net["RoadList"] if "RoadList" in net.columns else pd.Series(None, index=net.index)
        names = net["RoadName"] if "RoadName" in net.columns else pd.Series(None, index=net.index)
        for sid, r, rl, nm, s in zip(ids, net[ITD_ROUTES_COL], road_list, names, slip):
            if s or r is None or (isinstance(r, float) and pd.isna(r)):
                continue
            base = {x.strip() for x in str(r).split("/") if x.strip()}
            if not base:
                continue
            conc = {str(x) for x in road_list_routes(rl, nm)} if pd.notna(rl) else set()
            out[int(sid)] = frozenset(base | conc)
        return out
    rn = net["RoadNumber"].astype(str).str.strip()
    for sid, r, s in zip(ids, rn, slip):
        if s or r in ("", "None", "nan"):
            continue
        out[int(sid)] = frozenset({r})
    return out


class _ChainGeometry:
    """Metric geometry, terminal bearings and SHS mileposts for the member segments."""

    def __init__(self, net_idx, members: set[int]):
        from .corridors import _terminal_bearing
        from .itd_layers import SHS_MP_END_COL, SHS_MP_START_COL
        from .routes import ITD_ROUTE_ID_COL

        self.miles = pd.to_numeric(net_idx["Miles"], errors="coerce").fillna(0.0).to_dict()
        self.name = {int(k): (str(v).strip() if pd.notna(v) else "")
                     for k, v in net_idx.get("RoadName", pd.Series(dtype=object)).items()}
        self.group = {int(k): v for k, v in net_idx.get("XDGroup", pd.Series(dtype=object)).items()}
        self.route_id = {}
        if ITD_ROUTE_ID_COL in net_idx.columns:
            self.route_id = {int(k): str(v) for k, v in net_idx[ITD_ROUTE_ID_COL].items()
                             if v is not None and pd.notna(v)}
        self.mp0, self.mp1 = {}, {}
        if SHS_MP_START_COL in net_idx.columns:
            self.mp0 = pd.to_numeric(net_idx[SHS_MP_START_COL], errors="coerce").dropna().to_dict()
            self.mp1 = pd.to_numeric(net_idx[SHS_MP_END_COL], errors="coerce").dropna().to_dict()
        self.geom: dict[int, object] = {}
        self.start: dict[int, Point] = {}
        self.in_brg: dict[int, float | None] = {}
        self.out_brg: dict[int, float | None] = {}
        self._tbrg = _terminal_bearing
        if not isinstance(net_idx, gpd.GeoDataFrame) or "geometry" not in net_idx.columns:
            return
        sub = net_idx.loc[[s for s in members if s in net_idx.index]]
        sub = sub[sub.geometry.notna() & ~sub.geometry.is_empty]
        if sub.empty:
            return
        try:
            metric = sub.estimate_utm_crs()
        except Exception:
            metric = "EPSG:3857"
        for sid, g in sub.geometry.to_crs(metric).items():
            sid = int(sid)
            self.geom[sid] = g
            self.start[sid] = g.interpolate(0.0)
            self.in_brg[sid] = _terminal_bearing(g, at_end=False, probe=30.0)
            self.out_brg[sid] = _terminal_bearing(g, at_end=True, probe=30.0)

    def bearing_at(self, sid: int, dist: float) -> float | None:
        """Local bearing of ``sid`` over the 30 m before ``dist`` along it."""
        g = self.geom[sid]
        lo = max(0.0, dist - 30.0)
        if dist - lo < 1.0:
            return self.in_brg.get(sid)
        from shapely.geometry import LineString
        p1, p2 = g.interpolate(lo), g.interpolate(dist)
        return self._tbrg(LineString([p1, p2]), at_end=True, probe=30.0)

    def street(self, sid: int) -> str:
        from .couplets import street_key
        n = self.name.get(sid, "")
        return street_key(n).lower() if n else ""

    def run_miles(self, ids) -> float:
        return float(sum(self.miles.get(s, 0.0) for s in ids))


def _turn(a: float | None, b: float | None) -> float:
    if a is None or b is None:
        return 0.0
    return abs((a - b + 180.0) % 360.0 - 180.0)


def _join_candidate(cg: _ChainGeometry, tail: int, head: int) -> dict | None:
    """Does ``head`` continue the route from ``tail``'s last segment? ``None``, or
    ``{gap_m, turn_deg, at}`` — ``at`` is how far along ``tail`` the head leaves."""
    g, s = cg.geom.get(tail), cg.start.get(head)
    if g is None or s is None or g.length <= 0:
        return None
    gap = float(g.distance(s))
    if gap > ROUTE_JOIN_TOL_M:
        return None
    at = float(g.project(s))
    if at < ROUTE_JOIN_MIN_FRACTION * g.length:
        return None
    turn = _turn(cg.bearing_at(tail, at), cg.in_brg.get(head))
    if turn > ROUTE_JOIN_MAX_TURN_DEG:
        return None
    rid_t, rid_h = cg.route_id.get(tail), cg.route_id.get(head)
    if rid_t and rid_t == rid_h and tail in cg.mp0 and tail in cg.mp1 and head in cg.mp0:
        mp_at = cg.mp0[tail] + (cg.mp1[tail] - cg.mp0[tail]) * (at / g.length)
        if abs(cg.mp0[head] - mp_at) > ROUTE_JOIN_MP_TOL:
            return None
    return {"gap_m": round(gap, 1), "turn_deg": round(turn, 0), "at": at}


def _returns_to_line(cg: _ChainGeometry, a: int, run: Sequence[int]) -> float | None:
    """Mileposts past ``a`` on ``a``'s own SHS line, reached within the bridge limit.

    The milepost evidence for a stub join: SH-8 eastbound leaves its line at mp 1.79
    onto the US-95 couplet and ``run`` comes back to it at mp 2.35 on Troy Rd. Returns
    the milepost it comes back at, or ``None``."""
    rid = cg.route_id.get(a)
    if not rid or a not in cg.mp0 or a not in cg.mp1:
        return None
    sense = np.sign(cg.mp1[a] - cg.mp0[a])
    if sense == 0:
        return None
    walked = 0.0
    for s in run:
        if walked > ROUTE_BRIDGE_MAX_MILES:
            return None
        if cg.route_id.get(s) == rid and s in cg.mp0:
            ahead = (cg.mp0[s] - cg.mp1[a]) * sense
            return cg.mp0[s] if ahead >= -ROUTE_JOIN_MP_TOL else None
        walked += cg.miles.get(s, 0.0)
    return None


def _walk_route(members: set[int], nxt_raw: dict[int, int]) -> list[list[int]]:
    """Maximal ``NextXDSegI`` walks through ``members`` (heads first, then cycles)."""
    nxt = {s: n for s in members if (n := nxt_raw.get(s)) is not None and n in members}
    preds = set(nxt.values())
    visited: set[int] = set()
    walks: list[list[int]] = []

    def _walk(start: int) -> list[int]:
        out, cur = [], start
        while cur is not None and cur in members and cur not in visited:
            out.append(cur)
            visited.add(cur)
            cur = nxt.get(cur)
        return out

    for head in sorted(members - preds):
        if head not in visited:
            walks.append(_walk(head))
    for sid in sorted(members - visited):
        w = _walk(sid)
        if w:
            walks.append(w)
    return walks


def _join_route_walks(route: str, walks: list[list[int]], cg: _ChainGeometry,
                      linked_into: set[int]) -> list[tuple[list[int], list[dict]]]:
    """Join one route's walks where the route turns off the link (see the section note).

    ``linked_into`` is every member segment some member of this route links into; a
    head that appears there is mid-route and never a join target."""
    items = [(list(w), []) for w in walks if w]

    def _heads(exclude: int) -> list[tuple[int, int]]:
        return [(j, it[0][0]) for j, it in enumerate(items)
                if j != exclude and it[0] and it[0][0] not in linked_into]

    changed = True
    while changed:
        changed = False
        # Tail joins: the head leaves the tail's last segment.
        for i, (walk, log) in enumerate(items):
            if not walk:
                continue
            tail = walk[-1]
            best = None
            for j, head in _heads(i):
                if head in walk:
                    continue
                c = _join_candidate(cg, tail, head)
                if c is not None and (best is None or (c["gap_m"], c["turn_deg"])
                                      < (best[1]["gap_m"], best[1]["turn_deg"])):
                    best = (j, c)
            if best is not None:
                j, c = best
                other_walk, other_log = items[j]
                log.append({"kind": "junction", "route": route, "from": tail,
                            "to": other_walk[0], "gap_m": c["gap_m"],
                            "turn_deg": c["turn_deg"]})
                items[i] = (walk + other_walk, log + other_log)
                items[j] = ([], [])
                changed = True
                break
        if changed:
            continue
        # Stub joins: the head leaves an earlier segment and the route's own run past
        # it is a short stub — only on milepost evidence.
        for i, (walk, log) in enumerate(items):
            if len(walk) < 2:
                continue
            for k in range(len(walk) - 2, -1, -1):
                stub = walk[k + 1:]
                if cg.run_miles(stub) > ROUTE_STUB_MILES:
                    break
                a = walk[k]
                best = None
                for j, head in _heads(i):
                    if head in walk:
                        continue
                    c = _join_candidate(cg, a, head)
                    if c is None:
                        continue
                    mp = _returns_to_line(cg, a, items[j][0])
                    if mp is None:
                        continue
                    if best is None or (c["gap_m"], c["turn_deg"]) < (best[1]["gap_m"],
                                                                     best[1]["turn_deg"]):
                        best = (j, c, mp)
                if best is None:
                    continue
                j, c, mp = best
                other_walk, other_log = items[j]
                log.append({"kind": "stub_junction", "route": route, "from": a,
                            "to": other_walk[0], "gap_m": c["gap_m"],
                            "turn_deg": c["turn_deg"], "stub": list(stub),
                            "stub_miles": round(cg.run_miles(stub), 3),
                            "mp_leave": round(cg.mp1.get(a, float("nan")), 3),
                            "mp_return": round(mp, 3)})
                items[i] = (walk[:k + 1] + other_walk, log + other_log)
                items[j] = (list(stub), [])
                changed = True
                break
            if changed:
                break
    return [it for it in items if it[0]]


def _merge_renumberings(chains: list[dict], cg: _ChainGeometry,
                        nxt_raw: dict[int, int]) -> list[dict]:
    """Merge chains where the street runs on and only the route number changes.

    ``chains`` are dicts ``{route, ids, joins}``, one route's walk each. The street runs
    on from segment ``x`` of one chain to segment ``y`` of another route's chain when
    ``y`` is ``x`` itself (a shared concurrent segment: Broadway's I-15 BL / US-20
    piece), ``x``'s own ``NextXDSegI``, or starts at ``x``'s end; the two are the **same
    street**; the road runs straight on; and each chain leaves or joins the street
    there — ``x`` is its chain's tail or its chain turns off the street after it, and
    ``y`` is its chain's head or its chain arrives from another street. The street
    pieces are joined, and what the chains did off the street is left as chains of
    their own. On Yellowstone Hwy, US-91 hands over to US-26 / I-15 BL, which arrive
    from Sunnyside Rd: the street is one chain and the Sunnyside Rd approach another.
    Where continuations compete, the one that stays on the street longest wins.

    Built as a graph of ``(chain, position)`` nodes: each chain's own links, with a
    street continuation replacing the link it overrides; chains are then its maximal
    paths.
    """
    from shapely import STRtree

    n = len(chains)
    occ: dict[int, list[tuple[int, int]]] = {}
    for i, c in enumerate(chains):
        for k, s in enumerate(c["ids"]):
            occ.setdefault(s, []).append((i, k))

    start_ids = list(cg.start)
    tree = STRtree([cg.start[s] for s in start_ids]) if start_ids else None

    def _street_run(ids, k, street):
        m = 0.0
        for s in ids[k:]:
            if cg.street(s) != street:
                break
            m += cg.miles.get(s, 0.0)
        return m

    events = []   # (score, (i, k), (j, m))
    for i, c in enumerate(chains):
        ids = c["ids"]
        for k, x in enumerate(ids):
            street = cg.street(x)
            if not street:
                continue
            if k < len(ids) - 1 and cg.street(ids[k + 1]) == street:
                continue                      # the chain stays on the street
            targets = {x}
            if nxt_raw.get(x) is not None:
                targets.add(nxt_raw[x])
            g = cg.geom.get(x)
            if g is not None and tree is not None:
                end = g.interpolate(g.length)
                for q in tree.query(end.buffer(ROUTE_JOIN_TOL_M)):
                    targets.add(start_ids[int(q)])
            is_tail = k == len(ids) - 1
            for y in targets:
                if cg.street(y) != street:
                    continue
                if y != x and _turn(cg.out_brg.get(x), cg.in_brg.get(y)) > MERGE_MAX_TURN_DEG:
                    continue
                for j, m in occ.get(y, []):
                    o = chains[j]
                    if j == i or o["route"] == c["route"]:
                        continue
                    # One side must be a chain end — a tail running on into another
                    # route, or a chain turning off the street where another route's
                    # chain starts. Two chains merely sharing concurrent pavement are
                    # not a renumbering; a shared segment counts only tail to head.
                    if not (is_tail or m == 0) or (y == x and not (is_tail and m == 0)):
                        continue
                    if m > 0 and cg.street(o["ids"][m - 1]) == street:
                        continue              # that chain was already on the street
                    up = _street_run(list(reversed(ids[:k + 1])), 0, street)
                    down = _street_run(o["ids"], m, street)
                    if not (is_tail and m == 0) and min(up, down) < MERGE_MIN_STREET_MILES:
                        continue
                    events.append((down + up, (i, k), (j, m)))

    # Each node keeps at most one replacement out-link and one replacement in-link.
    events.sort(key=lambda e: -e[0])
    out_link: dict[tuple[int, int], tuple[int, int]] = {}
    in_link: dict[tuple[int, int], tuple[int, int]] = {}
    for _, src, dst in events:
        if src in out_link or dst in in_link:
            continue
        out_link[src] = dst
        in_link[dst] = src

    def _next(node):
        if node in out_link:
            return out_link[node]
        i, k = node
        nxt = (i, k + 1)
        if k + 1 < len(chains[i]["ids"]) and nxt not in in_link:
            return nxt
        return None

    has_pred: set[tuple[int, int]] = set(in_link)
    for i, c in enumerate(chains):
        for k in range(1, len(c["ids"])):
            if (i, k - 1) not in out_link and (i, k) not in in_link:
                has_pred.add((i, k))

    all_nodes = [(i, k) for i, c in enumerate(chains) for k in range(len(c["ids"]))]
    seen: set[tuple[int, int]] = set()
    out = []
    starts = [nd for nd in all_nodes if nd not in has_pred]
    for start in starts + all_nodes:
        if start in seen:
            continue
        ids, routes_miles, joins, logged = [], {}, [], set()
        node = start
        while node is not None and node not in seen:
            seen.add(node)
            i, k = node
            sid = chains[i]["ids"][k]
            route = chains[i]["route"]
            if i not in logged:
                logged.add(i)
                joins.extend(j for j in chains[i]["joins"]
                             if j.get("from") in chains[i]["ids"][k:] or k == 0)
            if not ids or ids[-1] != sid:
                if sid in ids:
                    break                     # a loop back onto itself
                ids.append(sid)
                routes_miles[route] = routes_miles.get(route, 0.0) + cg.miles.get(sid, 0.0)
            nxt = _next(node)
            if nxt is not None and node in out_link:
                joins.append({"kind": "renumbering", "route": chains[nxt[0]]["route"],
                              "from": sid, "to": chains[nxt[0]]["ids"][nxt[1]]})
            node = nxt
        if ids:
            out.append({"ids": ids, "joins": joins, "routes_miles": routes_miles})
    return out


def enumerate_mainline_chains(
    network: gpd.GeoDataFrame | pd.DataFrame,
    *,
    min_miles: float = MIN_CHAIN_MILES,
    route_numbers: Sequence[str] | None = None,
    join: bool = True,
) -> list[MainlineChain]:
    """Walk every state route in ``network`` into maximal directional chains.

    The walk is topological, never geographic (ROADMAP Item 36): it follows
    ``NextXDSegI`` through one route's segments (:func:`segment_route_sets` —
    membership plus ``RoadList`` concurrency when ``routes.apply_route_membership`` has
    been applied, else ``RoadNumber``), so the chain ends where the route ends rather
    than continuing onto whatever happens to lie ahead. With ``join`` (Item 51) a
    route's walks are joined where the route turns off the link, and chains are merged
    where only the route number changes along a street (see the section note). Apply
    :func:`corridors.apply_link_repairs` to the network first if you want the Item 38
    repairs honoured.

    Args:
        network: XD network with ``XDSegID``, ``NextXDSegI``, ``RoadNumber``,
            ``Bearing``, ``Miles`` (and ``itd_routes`` / ``itd_route_id`` /
            ``shs_mp_start`` / ``shs_mp_end`` for the Item 51 walk).
        min_miles: drop chains shorter than this (stubs and connector fragments).
        route_numbers: optional whitelist of route numbers to walk.
        join: join and merge across junctions and renumberings (Item 51). ``False``
            walks each route by the link alone.

    Returns:
        Chains sorted by miles descending. A concurrent segment can lie on more than
        one chain (SH-8 and US-95 share Moscow's couplet); a chain wholly inside a
        longer one is dropped.
    """
    if network is None or len(network) == 0:
        return []
    net_idx = _ensure_indexed(network)
    sets = segment_route_sets(network)
    if route_numbers is not None:
        want = {str(r).strip() for r in route_numbers}
        sets = {s: r & want for s, r in sets.items() if r & want}
    sets = {s: r for s, r in sets.items() if s in net_idx.index}
    if not sets:
        return []

    by_route: dict[str, set[int]] = {}
    for sid, rs in sets.items():
        for r in rs:
            by_route.setdefault(r, set()).add(sid)

    # The band is a property of the *route*, not of a chain on it. Reading it per
    # chain labels I-90's Coeur d'Alene business route "SH-90", because no segment
    # of it is called "I-90" — every segment is called "Northwest Blvd".
    # ``RoadList`` is read too: US-91 on S Yellowstone Hwy is never *named* US-91, only
    # listed as it, and a RoadName-only read labelled it "SH-91".
    labels: dict[str, str] = {}
    names_col = net_idx.get("RoadName", pd.Series(dtype=object))
    list_col = net_idx.get("RoadList", pd.Series(dtype=object))
    frc_col = pd.to_numeric(net_idx.get("FRC", pd.Series(dtype=float)), errors="coerce")
    for route, members in by_route.items():
        ids = list(members)
        names = [str(v).strip() for v in names_col.reindex(ids).dropna() if str(v).strip()]
        names += [t.strip() for v in list_col.reindex(ids).dropna()
                  for t in str(v).split("|") if t.strip()]
        frc_vals = frc_col.reindex(ids).dropna()
        labels[route] = route_label(route, names, int(frc_vals.min()) if len(frc_vals) else None)

    nxt_raw: dict[int, int] = {}
    for sid, nid in pd.to_numeric(net_idx.get("NextXDSegI", pd.Series(dtype=float)),
                                  errors="coerce").dropna().items():
        nxt_raw[int(sid)] = int(nid)

    cg = _ChainGeometry(net_idx, set(sets)) if join else None
    raw: list[dict] = []
    for route in sorted(by_route, key=lambda r: (len(r), r)):
        members = by_route[route]
        walks = _walk_route(members, nxt_raw)
        if join and cg is not None and cg.geom:
            linked_into = {n for s in members if (n := nxt_raw.get(s)) in members}
            joined = _join_route_walks(route, walks, cg, linked_into)
        else:
            joined = [(w, []) for w in walks]
        raw.extend({"route": route, "ids": w, "joins": log} for w, log in joined)

    if join and cg is not None and cg.geom:
        merged = _merge_renumberings(raw, cg, nxt_raw)
    else:
        merged = [{"ids": c["ids"], "joins": c["joins"],
                   "routes_miles": {c["route"]: 0.0}} for c in raw]

    miles_of = pd.to_numeric(net_idx["Miles"], errors="coerce").fillna(0.0)
    out: list[MainlineChain] = []
    for c in merged:
        walk = c["ids"]
        sub = net_idx.loc[walk]
        total_miles = round(float(miles_of.reindex(walk).sum()), 3)
        if total_miles < min_miles:
            continue
        bearings = [str(b).strip() for b in sub["Bearing"].dropna() if str(b).strip()]
        names = [str(v).strip() for v in sub.get("RoadName", pd.Series(dtype=object)).dropna()
                 if str(v).strip()]
        rm = c["routes_miles"]
        ordered = tuple(sorted(rm, key=lambda r: -rm[r]))
        route = ordered[0]
        out.append(MainlineChain(
            route_number=route,
            bearing=_mode(bearings),
            segment_ids=tuple(int(s) for s in walk),
            miles=total_miles,
            road_name=_mode(names),
            route_label=labels.get(route, route_label(route, names)),
            localities=_ranked_values(sub, "PostalCode"),
            counties=_ranked_values(sub, "County"),
            route_numbers=ordered,
            route_labels=tuple(labels.get(r, route_label(r, names)) for r in ordered),
            joins=tuple(c["joins"]),
        ))

    # A chain wholly inside a longer one adds nothing (a concurrent route's walk that
    # never leaves its partner, like SH-3 on SH-8's pavement).
    out.sort(key=lambda c: -c.miles)
    kept: list[MainlineChain] = []
    covered: list[set[int]] = []
    for c in out:
        ids = set(c.segment_ids)
        if any(ids <= other for other in covered):
            continue
        kept.append(c)
        covered.append(ids)
    return kept


def _mode(values: Sequence[str]) -> str:
    """Most common non-empty value (ties broken by first appearance)."""
    counts: dict[str, int] = {}
    for v in values:
        if v:
            counts[v] = counts.get(v, 0) + 1
    if not counts:
        return ""
    return max(counts, key=lambda k: (counts[k], -list(counts).index(k)))


def _ranked_values(frame: pd.DataFrame, column: str) -> tuple[str, ...]:
    """Distinct non-empty values of ``column``, most frequent first."""
    if column not in frame.columns:
        return ()
    vals = [str(v).strip() for v in frame[column].dropna() if str(v).strip()
            and str(v).strip().lower() != "nan"]
    if not vals:
        return ()
    counts: dict[str, int] = {}
    for v in vals:
        counts[v] = counts.get(v, 0) + 1
    return tuple(sorted(counts, key=lambda k: -counts[k]))


def pair_chains(
    chains: Sequence[MainlineChain],
    network: gpd.GeoDataFrame,
    *,
    max_mean_sep_m: float = PAIR_MAX_MEAN_SEP_M,
    metric_crs=None,
) -> list[tuple[MainlineChain, MainlineChain | None]]:
    """Pair each chain with the opposing carriageway of the same facility.

    Pairing is by **route identity plus proximity**, in that order: a route in
    common (Item 51: a chain can walk several), opposing cardinal bearing, and a mean lateral separation under
    ``max_mean_sep_m``. Proximity is the necessary second test because one route
    yields several chains in a district — US-95 in District 1 walks as a
    101-mile pair *and* a 28-mile pair, and matching on route alone would marry
    Coeur d'Alene's northbound to Bonners Ferry's southbound.

    Returns:
        ``(chain, counterpart_or_None)`` for every chain that leads a pair, each
        pair reported once, ordered as ``chains`` was. A chain with no
        counterpart (a genuinely undivided two-way route, which XD still codes
        as one directional chain) is returned with ``None``.
    """
    if not chains:
        return []

    geoms = _chain_geometries(chains, network, metric_crs=metric_crs)
    taken: set[int] = set()
    out: list[tuple[MainlineChain, MainlineChain | None]] = []

    for i, chain in enumerate(chains):
        if i in taken:
            continue
        opp = _OPPOSITE.get(chain.bearing)
        best_j, best_sep = None, float("inf")
        if opp is not None and geoms[i] is not None:
            for j, other in enumerate(chains):
                if j == i or j in taken or other.bearing != opp:
                    continue
                if not (set(other.routes) & set(chain.routes)) or geoms[j] is None:
                    continue
                # Sampled along the *shorter* chain: since Item 51 the two directions
                # can be joined and merged differently (a data gap splits one), and
                # the longer one's extra miles say nothing about the pairing.
                if other.miles < chain.miles:
                    sep = _mean_separation_m(geoms[j], geoms[i])
                else:
                    sep = _mean_separation_m(geoms[i], geoms[j])
                if sep < best_sep:
                    best_sep, best_j = sep, j
        if best_j is not None and best_sep <= max_mean_sep_m:
            taken.add(i)
            taken.add(best_j)
            out.append((chain, chains[best_j]))
        else:
            taken.add(i)
            out.append((chain, None))

    return out


def _chain_geometries(chains, network, *, metric_crs=None):
    """Merged, metric-projected geometry per chain (``None`` where unavailable)."""
    if not isinstance(network, gpd.GeoDataFrame) or "geometry" not in network.columns:
        return [None] * len(chains)
    net = _ensure_indexed(network)
    if metric_crs is None:
        try:
            metric_crs = network.estimate_utm_crs()
        except Exception:
            metric_crs = "EPSG:3857"
    out = []
    for chain in chains:
        present = [s for s in chain.segment_ids if s in net.index]
        if not present:
            out.append(None)
            continue
        try:
            series = gpd.GeoSeries(net.loc[present, "geometry"].values,
                                   crs=network.crs).to_crs(metric_crs)
            out.append(series.union_all())
        except Exception:
            out.append(None)
    return out


def _mean_separation_m(geom_a, geom_b) -> float:
    """Mean distance from points sampled along ``geom_a`` to ``geom_b``, in metres."""
    try:
        merged = linemerge(geom_a) if geom_a.geom_type == "MultiLineString" else geom_a
        line = merged.geoms[0] if merged.geom_type == "MultiLineString" else merged
        dists = [geom_b.distance(line.interpolate(f, normalized=True))
                 for f in np.linspace(0.05, 0.95, 9)]
        return float(np.mean(dists))
    except Exception:
        return float("inf")


# ─── Catalogue generation (ROADMAP Item 46) ──────────────────────────
#
# The last mile: turn the tier alternatives above into `corridors.parse_catalogue`
# entries. Two things make this more than a dict rename.
#
# 1. **A catalogue entry is one direction.** The tiers are built on one chain, so
#    the opposing carriageway's extent is *mirrored* onto it (`mirror_extent`)
#    rather than analysed separately — analysing both independently lets the two
#    halves of one corridor end at different cross-streets, and then the reporting
#    corridor sums two different extents.
# 2. **Every entry must say why it ends where it does.** `split_rationale` plus the
#    bounding splits go into `description`, which `parse_catalogue` refuses to let
#    be empty. A generated catalogue that cannot explain its own extents is the
#    hand-drawn one with the author's name removed.

TIER_LABEL = {
    ExtentTier.CORE: "Congested Core",
    ExtentTier.COMMUTER: "Commuter Corridor",
    ExtentTier.REGIONAL: "Regional Baseline",
}
TIER_NUMBER = {ExtentTier.CORE: 1, ExtentTier.COMMUTER: 2, ExtentTier.REGIONAL: 3}

DEFAULT_TIERS = (ExtentTier.CORE, ExtentTier.COMMUTER, ExtentTier.REGIONAL)


def tti_frame(screen_data: pd.DataFrame, windows: Sequence[str] = ("am", "pm")) -> pd.DataFrame:
    """Shape a `screen.segment_screen` frame as the recurrence frame the tier
    builder reads — ``{window}_mean_tti`` per segment.

    TTI here is ``ref_speed / {window}_speed``, the same ratio
    :func:`detect_congestion_splits` already falls back to. Having it as a frame
    means the whole extent pass runs off the screening parquet the district run
    already wrote, with no second trip to DuckDB.
    """
    if screen_data is None or screen_data.empty:
        return pd.DataFrame()
    scr = screen_data
    if "Segment ID" in scr.columns and scr.index.name != "Segment ID":
        scr = scr.set_index("Segment ID")
    if "ref_speed" not in scr.columns:
        return pd.DataFrame()
    ref = pd.to_numeric(scr["ref_speed"], errors="coerce")
    out = pd.DataFrame(index=scr.index)
    for w in windows:
        col = f"{w}_speed"
        if col not in scr.columns:
            continue
        spd = pd.to_numeric(scr[col], errors="coerce")
        out[f"{w}_mean_tti"] = (ref / spd).where(spd > 0)
    out.index.name = "Segment ID"
    return out


def worst_window_tti(tti: pd.DataFrame, windows: Sequence[str],
                     label: str = "peak") -> pd.DataFrame:
    """Collapse several windows' TTI to the **worst** of them, per segment.

    A corridor is catalogued on the peak it is bad in, not on one chosen in
    advance: US-20 between Idaho Falls and Rexburg is an AM inbound commute and
    is invisible to a PM-only pass, and reading only the PM peak dropped it, I-84
    at Twin Falls and I-15 at Pocatello from the first generated catalogue.
    """
    cols = [f"{w}_mean_tti" for w in windows if f"{w}_mean_tti" in tti.columns]
    if not cols:
        return tti
    out = tti.copy()
    out[f"{label}_mean_tti"] = out[cols].max(axis=1)
    return out


def resolve_window(window) -> tuple[str, tuple[str, ...]]:
    """``"am,pm"`` -> ``("peak", ("am", "pm"))``; ``"pm"`` -> ``("pm", ("pm",))``."""
    parts = tuple(w.strip() for w in str(window).split(",") if w.strip())
    if len(parts) <= 1:
        name = parts[0] if parts else "pm"
        return name, (name,)
    return "peak", parts


def _nearest_index(chain_segments: Sequence[int], latlon: tuple[float, float],
                   net_idx: pd.DataFrame, metric_crs) -> int | None:
    """Index into ``chain_segments`` of the segment nearest ``latlon``.

    Distance is measured in a **projected metric CRS**, not in degrees. Degrees
    are not a distance: at Idaho's latitude a degree of longitude is ~0.73 of a
    degree of latitude, so a degree-space nearest-wins is biased east-west and
    picks the wrong segment at a skewed junction.
    """
    present = [s for s in chain_segments if s in net_idx.index]
    if not present:
        return None
    try:
        geoms = gpd.GeoSeries(net_idx.loc[present, "geometry"].values,
                              crs=net_idx.crs).to_crs(metric_crs)
        pt = gpd.GeoSeries([Point(latlon[1], latlon[0])],
                           crs="EPSG:4326").to_crs(metric_crs).iloc[0]
        nearest = int(np.argmin(geoms.distance(pt).values))
    except Exception:
        return None
    return chain_segments.index(present[nearest])


def mirror_extent(
    alt: ExtentAlternative,
    target_chain: Sequence[int],
    network: gpd.GeoDataFrame,
    *,
    metric_crs=None,
) -> ExtentAlternative | None:
    """Project one direction's extent onto the opposing carriageway.

    The opposing chain runs the other way, so the mirrored extent starts where
    ``alt`` *ends*. Both boundaries are snapped onto the target chain and the
    slice between them is taken — so the two directions of a reporting corridor
    cover the same ground even where the carriageways are segmented differently.
    """
    net = _ensure_indexed(network)
    if metric_crs is None:
        try:
            metric_crs = network.estimate_utm_crs()
        except Exception:
            metric_crs = "EPSG:3857"
    target = list(target_chain)
    i_a = _nearest_index(target, alt.end_latlon, net, metric_crs)
    i_b = _nearest_index(target, alt.start_latlon, net, metric_crs)
    if i_a is None or i_b is None:
        return None
    lo, hi = (i_a, i_b) if i_a <= i_b else (i_b, i_a)
    segs = target[lo:hi + 1]
    if not segs:
        return None

    miles = 0.0
    for sid in segs:
        if sid in net.index:
            row = net.loc[sid]
            if isinstance(row, pd.DataFrame):
                row = row.iloc[0]
            val = row.get("Miles", 0.0)
            if pd.notna(val):
                miles += float(val)

    return ExtentAlternative(
        tier=alt.tier,
        segment_ids=tuple(segs),
        miles=round(miles, 3),
        start_latlon=_get_coords(net, segs[0], end=False),
        end_latlon=_get_coords(net, segs[-1], end=True),
        split_rationale=alt.split_rationale,
        bounding_splits=alt.bounding_splits,
    )


def _endpoint_name(net_idx: pd.DataFrame, seg_id: int, *, end: bool) -> str:
    """Name an extent's endpoint from whatever the network can say about it.

    ``aadt_desc`` is the ITD route-measure record's own description of that point
    (``W POST FALLS IC #5``, ``SH-53``) and is the only cross-street naming this
    pipeline has offline — the XD ``PostalCode`` is a ZIP, not a place name, and
    ``RoadList`` holds the segment's own aliases rather than what crosses it.
    """
    if seg_id not in net_idx.index:
        return ""
    row = net_idx.loc[seg_id]
    if isinstance(row, pd.DataFrame):
        row = row.iloc[0]
    for col in ("aadt_desc", "RoadName"):
        val = row.get(col)
        if pd.notna(val) and str(val).strip() and str(val).strip().upper() != "NONE":
            # ``#`` is stripped, not kept: every CSV this pipeline writes carries a
            # ``# key: value`` provenance header, and ``pd.read_csv(comment="#")``
            # treats a ``#`` *anywhere* in a line as the start of a comment. An
            # endpoint named "US-95 IC #12" therefore truncates its own row on the
            # way back in, and the corridor reads as all-null downstream.
            return re.sub(r"\s+", " ", re.sub(r"\s*#\s*", " No. ", str(val))).strip().title()
    return ""


def _slug(text: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9]+", "-", str(text)).strip("-").lower()
    return s or "corridor"


_ROUTE_LIKE = re.compile(
    r"^(?:(?:I|US|ID|SH|SR)-?\d+\b|(?:us |state |old )?(?:highway|hwy) \d+\b"
    r"|\d+(?: ?[NSEW])?$)", re.IGNORECASE)
"""A name that is only a route: ``US-95``, ``ID-8 E``, ``Us Highway 12``, ``95 N``. A
numbered street (``50 St S``, ``2nd S``) is a street."""
_BUSINESS_SUFFIX = {"BL": "BL", "BR": "BR", "BUS": "BL", "SPUR": "Spur"}


def business_band(band: str, sub: pd.DataFrame) -> str:
    """``band`` with ``BL`` / ``BR`` / ``Spur`` appended when at least half of ``sub``'s
    miles list themselves in ``RoadList`` as that route's business loop or spur
    (``I-15-BL`` on Pocatello's 5th Ave -> ``I-15 BL``)."""
    if "RoadList" not in sub.columns or sub.empty:
        return band
    miles = pd.to_numeric(sub.get("Miles"), errors="coerce").fillna(0.0)
    if miles.sum() <= 0:
        return band
    pat = re.compile(rf"^{re.escape(band)}-(BL|BR|BUS|SPUR)$", re.IGNORECASE)
    for suffix in ("BL", "BR", "BUS", "SPUR"):
        hit = sub["RoadList"].fillna("").map(
            lambda v: any((m := pat.match(t.strip())) and m.group(1).upper() == suffix
                          for t in str(v).split("|")))
        if float(miles[hit].sum()) >= 0.5 * float(miles.sum()):
            return f"{band} {_BUSINESS_SUFFIX[suffix]}"
    return band


def place_name(sub: pd.DataFrame) -> str:
    """The urban area most of ``sub``'s miles lie inside (Item 52's context, first part
    of the Census name: ``Idaho Falls``), else the county (``Latah County``)."""
    miles = pd.to_numeric(sub.get("Miles"), errors="coerce").fillna(0.0)
    if "urban_area" in sub.columns and "urban_share" in sub.columns:
        inside = pd.to_numeric(sub["urban_share"], errors="coerce").fillna(0.0) >= URBAN_SHARE_INSIDE
        names = sub["urban_area"].where(inside).dropna()
        if len(names):
            by = miles[names.index].groupby(names.astype(str)).sum()
            return str(by.idxmax()).split(",")[0].strip()
    if "County" in sub.columns:
        c = sub["County"].dropna().astype(str).str.strip()
        c = c[c.str.len() > 0]
        if len(c):
            by = miles[c.index].groupby(c).sum()
            return f"{by.idxmax()} County"
    return "Idaho"


def facility_naming(chain: MainlineChain, core: CoreCandidate | None, seg: pd.DataFrame,
                    net_idx, route_sets: dict[int, frozenset[str]] | None = None
                    ) -> tuple[str, str, str]:
    """``(band, street, place)`` for a facility, from where its **core** lies (Item 51).

    * **band**: of the chain's routes, the one that carries most of the core's miles,
      with ``BL`` / ``BR`` / ``Spur`` when the core's ``RoadList`` says it is that
      route's business loop or spur (Pocatello's 5th Ave is ``I-15 BL``);
    * **street**: the core's street by ``RoadName`` miles (``street_key``: ``W Pullman
      Rd`` -> ``Pullman Rd``), and a second when it carries at least 30% (``Pullman Rd
      / 3rd St``); empty when the core is named only by its route (``US-95``,
      ``Highway 95``);
    * **place**: the urban area most of the core lies in (Item 52), else its county.
    """
    from .couplets import street_key

    ids = list(core.segment_ids) if core is not None else list(chain.segment_ids)
    sub = net_idx.reindex([s for s in ids if s in net_idx.index])
    miles = pd.to_numeric(sub.get("Miles"), errors="coerce").fillna(0.0) \
        if len(sub) else pd.Series(dtype=float)

    routes = list(chain.routes)
    labels = dict(zip(chain.route_numbers, chain.route_labels)) if chain.route_labels else {}
    band_route = chain.route_number
    if route_sets and len(routes) > 1:
        cover = {r: float(miles[[r in route_sets.get(int(s), ()) for s in sub.index]].sum())
                 for r in routes}
        band_route = max(routes, key=lambda r: (cover[r], -routes.index(r)))
    band = labels.get(band_route, chain.route_label if band_route == chain.route_number
                      else f"SH-{band_route}")
    band = business_band(band, sub)

    # A segment INRIX names by its route ("US-20", "ID-33") is the highway, not a
    # street; its RoadList aliases are byway and memorial names ("Idaho Medal of Honor
    # Hwy", "Teton Scenic Bywy") and are not read.
    by_street: dict[str, float] = {}
    for sid, mi in miles.items():
        n = sub.at[sid, "RoadName"] if "RoadName" in sub.columns else None
        if n is None or pd.isna(n) or not str(n).strip():
            continue
        if _ROUTE_LIKE.match(str(n).strip()) or _ROUTE_LIKE.match(street_key(n)):
            continue
        name = street_key(n)
        by_street[name] = by_street.get(name, 0.0) + float(mi)
    street = ""
    total = float(miles.sum())
    if by_street and total > 0:
        ranked = sorted(by_street, key=lambda k: -by_street[k])
        if by_street[ranked[0]] >= 0.3 * total:
            street = ranked[0]
            if len(ranked) > 1 and by_street[ranked[1]] >= 0.3 * total:
                street = f"{ranked[0]} / {ranked[1]}"

    place = _core_town(seg, core) if core is not None else ""
    if not place:
        county = _core_county(net_idx, core) or (chain.counties[0] if chain.counties else "")
        place = f"{county.strip()} County" if county.strip() else "Idaho"
    return band, street, place


def facility_label(chain: MainlineChain) -> str:
    """Name the facility a chain belongs to, distinguishing it from the route's
    other chains.

    One route number covers several distinct facilities: route 90 in Kootenai
    County is both I-90 itself and the Coeur d'Alene business route along
    Northwest Blvd. They are told apart by ``RoadName`` — a chain whose segments
    call themselves something other than the route band gets that name in
    parentheses, which is what makes ``I-90 (Northwest Blvd)`` a different
    facility from ``I-90``.
    """
    band = chain.route_label
    name = (chain.road_name or "").strip()
    if not name:
        return band
    base = re.sub(r"\s+[NSEW]B?$", "", name).strip()
    if ROUTE_LABEL_RE.match(base) or base.replace("Highway ", "SH-") == band:
        return band
    return f"{band} ({base})"


def extent_catalogue_entries(
    alt: ExtentAlternative,
    chain: MainlineChain,
    counterpart_alt: ExtentAlternative | None,
    counterpart: MainlineChain | None,
    network: gpd.GeoDataFrame,
    *,
    facility_id: str,
    facility_name: str,
    group_meta: dict | None = None,
) -> tuple[list[dict], dict]:
    """Emit the catalogue entries and reporting group for one tier of one facility.

    Returns ``(entries, group)`` in ``corridors.parse_catalogue`` /
    ``parse_reporting_corridors`` shape. Underscore-prefixed keys carry the
    provenance the parser ignores but the tier comparison reads back
    (``_tier``, ``_facility``, ``_split_rationale``, ``_segment_ids``).

    Each direction's entry states **its own** boundaries (Item 50): the two
    directions of a facility are cored and grown separately, so they can end at
    different places. ``counterpart_alt`` is ``None`` when the opposing direction
    has no congestion of its own; the group then carries one entry.
    ``group_meta`` is merged into the group (the core's metrics, ``_ranked``, the
    companion note).
    """
    net = _ensure_indexed(network)
    tier = alt.tier
    group_id = f"{facility_id}-{tier.value}"
    label = TIER_LABEL[tier]

    start_name = _endpoint_name(net, alt.segment_ids[0], end=False)
    end_name = _endpoint_name(net, alt.segment_ids[-1], end=True)
    extent_phrase = (f"{start_name} to {end_name}"
                     if start_name and end_name and start_name != end_name
                     else f"{alt.miles:.2f} mi")

    def _rationale(member: ExtentAlternative, direction: str) -> str:
        up, down = member.bounding_splits
        return (
            f"Tier {TIER_NUMBER[tier]} ({label}) of {facility_name}, {direction}. "
            f"{member.split_rationale}. "
            f"Upstream boundary: {describe_split(up, 'the start of the mainline chain')}; "
            f"downstream boundary: {describe_split(down, 'the end of the mainline chain')}. "
            f"Generated from the XD topology by inrix_tools.extents (ROADMAP Items 46, 50)."
        )

    lead_rationale = _rationale(alt, chain.direction)
    group = {
        "id": group_id,
        "name": f"{facility_name} — Tier {TIER_NUMBER[tier]} {label} ({extent_phrase})",
        "description": lead_rationale,
        "_tier": tier.value,
        "_tier_number": TIER_NUMBER[tier],
        "_tier_label": f"Tier {TIER_NUMBER[tier]} {label} ({extent_phrase})",
        "_facility": facility_id,
        "_facility_name": facility_name,
        "_miles": alt.miles,
        "_n_segments": len(alt.segment_ids),
        "_ranked": tier is ExtentTier.CORE,
    }
    if group_meta:
        group.update(group_meta)

    nxt = (pd.to_numeric(net["NextXDSegI"], errors="coerce")
           if "NextXDSegI" in net.columns else pd.Series(dtype=float))

    def _links(ids: Sequence[int]) -> list[list[int]]:
        """The steps the extent takes that ``NextXDSegI`` does not (Item 51's joins)."""
        out = []
        for a, b in zip(ids[:-1], ids[1:]):
            n = nxt.get(a) if a in nxt.index else None
            if n is None or pd.isna(n) or int(n) != int(b):
                out.append([int(a), int(b)])
        return out

    entries: list[dict] = []
    for member_alt, member_chain in ((alt, chain), (counterpart_alt, counterpart)):
        if member_alt is None or member_chain is None:
            continue
        direction = member_chain.direction
        # "US-95 NB: Kootenai County — Congested Core", not "US-95 NB: US-95:
        # Kootenai County — Congested Core": the facility name already leads with
        # the route band, so the direction goes *inside* it.
        band, sep, where = facility_name.partition(": ")
        entry_name = (f"{band} {direction}: {where} — {label}" if sep
                      else f"{band} {direction} — {label}")
        entries.append({
            "id": f"{group_id}-{direction.lower()}",
            "name": entry_name,
            "start_latlon": [round(member_alt.start_latlon[0], 5),
                             round(member_alt.start_latlon[1], 5)],
            "end_latlon": [round(member_alt.end_latlon[0], 5),
                           round(member_alt.end_latlon[1], 5)],
            "description": _rationale(member_alt, direction),
            "corridor": group_id,
            "direction": direction,
            **({"links": links} if (links := _links(member_alt.segment_ids)) else {}),
            "_tier": tier.value,
            "_facility": facility_id,
            "_split_rationale": member_alt.split_rationale,
            "_generated_miles": member_alt.miles,
            "_segment_ids": [int(s) for s in member_alt.segment_ids],
        })
    return entries, group


# ─── Cores from recurring congestion (ROADMAP Item 50) ───────────────
#
# Item 46 cored a facility where peak TTI against INRIX's ``Ref Speed`` was >= 1.20
# on >= 0.75 mi of segments. The owner's Session 65 review found what that admits:
# grades and canyons that are as slow at 2 a.m. as at 5 p.m. (Gilbert Grade), roads
# with 180 vehicles a day and a third of their data imputed (Lowell), a single
# 0.84-mi rural segment standing as a whole core (Bonners Ferry), hard cliffs (SH-8
# at 1.199), extents mirrored onto a free-flowing direction, and Tier 3 extents that
# ran the whole 180-mile chain. The replacement:
#
# * **Each segment is measured against its own baseline** — its overnight travel
#   time, or a low weekday percentile where the night has too little data — not
#   against INRIX's reference speed. A grade is slow at night too; its ratio is ~1.
# * **A core is scored, not gated per segment.** Congestion weight rises smoothly
#   from 1.05 to 1.20, a segment's length counts only up to 0.5 mi, and the core
#   must carry real delay (vehicle-hours per mile from the AADT 2025 join) on data
#   that is mostly real-time probe data.
# * **Each direction answers for itself**, and Tiers 2 and 3 stop where the
#   congestion does; urban-area boundaries guide where to look, never cut.

BASELINE_MIN_OBS = 100
"""Gated observations a baseline window needs before it is trusted (~25 hours of
15-minute bins over the export). Below it the next baseline is tried; with none, the
segment's congestion is **unknown** — never zero."""

BASELINE_FALLBACK_COL = "weekday_tt_p15"
"""The fallback baseline: the 15th percentile of the segment's gated weekday travel
times (``screen.BASELINE_WINDOWS`` with ``quantiles=BASELINE_QUANTILES``)."""

PEAK_WINDOWS = ("am", "pm")
"""The windows a core is read from; each segment is judged at its worse one."""

RATIO_ONSET = 1.05
"""Peak/baseline ratio at which a segment starts to carry congestion weight. Session
65's geometric roads sit at 1.00–1.04."""

RATIO_FULL = 1.20
"""Peak/baseline ratio at which the weight reaches 1. Real hotspots are 1.2–1.5."""

CORE_SEED_RATIO = 1.10
"""A core starts and ends on a segment at least this congested. Between them it
bridges up to :data:`CORE_GAP_SEGMENTS` less congested segments — so a 1.099
neighbour inside a queue is bridged, not an edge the core falls off."""

CORE_GAP_SEGMENTS = 2
CORE_GAP_MILES = 0.5
"""A bridged gap is at most this many segments **and** this many miles."""

SEGMENT_MILES_CAP = 0.5
"""A segment's length counts toward a core's effective miles only up to this, so one
long rural segment cannot be a core by itself."""

MIN_EFFECTIVE_CORE_MILES = 0.6
"""Floor on ``sum(min(miles, SEGMENT_MILES_CAP) x weight)`` over the core."""

MIN_CORE_VHD_PER_MILE = 5.0
"""A **noise floor** on the core's vehicle-hours of delay per mile, not a policy cut.

VHD here is an index — mean window delay times a daily AADT used as a weight — so a
cut placed between two real towns would be arbitrary (owner, Session 69: "be pretty
permissive ... a later decision can filter them back out if they rank too low").
The floor only removes what is not delay at all: the rural geometric and low-volume
cores sit at 0.5–2.5 (Gilbert Grade 0.55–0.7, Lowell ~1, Benewah 1–2, Galena 1–1.5).
The smallest real town cores (Blackfoot 22, Bonners Ferry 22.5, Soda Springs 25) pass
and rank at the bottom; thinning to a top-N belongs to the ranking, not the catalogue.

Tuned at 10 on two-way AADT (Session 69); halved with the per-direction basis (Item
54), which halves every VHD, so the same cores pass. The figures above are the
Session 69 ones halved."""

MIN_CORE_VHD = 5.0
"""Noise floor on the core's total vehicle-hours of delay (10 on two-way AADT, halved
for the per-direction basis like :data:`MIN_CORE_VHD_PER_MILE`)."""

MIN_REALTIME_SHARE = 0.90
"""Floor on the core's mile-weighted ``Pct Score30`` share in its peak window. Every
keep-list core is >= 0.98; Lowell is 0.00, Benewah 0.03, the Idaho County core
0.14–0.27, Bonners Ferry 0.81–0.88."""

SPILL_RETENTION = 0.5
"""Tier 2 grows past the core while the grown extent keeps at least this share of
the core's VHD per mile — the owner's "doesn't significantly dilute the primary
congestion" (2026-09-23)."""

URBAN_SHARE_INSIDE = 0.5
"""A segment with at least this share of its length inside an urban area is inside."""

CONTEXT_PAD_MILES = 3.0
"""Tier 3 (context) reaches at most this far past Tier 2 on each side."""

EPISODIC_TOP_FRACTION = 1 / 3
EPISODIC_SHARE = 0.70
SEASONAL_SHARE = 0.55
"""Flags on how a core's peak delay is spread over the months (its busiest third of
months' share; an even spread over 8 months is 0.375). Flags, never exclusions.

* ``episodic`` (>= 0.70): a step or event — check for a work zone. I-90 WB in Coeur
  d'Alene is 0.96 (at its overnight level until 22 June 2026), US-95 SB in
  Sandpoint 0.77.
* ``seasonal`` (>= 0.55): recurring but summer-heavy — the resort and recreation
  towns (Ketchum 0.65, Victor 0.63, Soda Springs 0.68, Burley 0.67).

Calibrated on the 39 cores of Session 69: 30 of them sit at 0.40–0.47."""

EPISODIC_MIN_MONTHS = 3

FAIL_EFFECTIVE_MILES = "effective_miles"
FAIL_VHD_PER_MILE = "vhd_per_mile"
FAIL_VHD = "vhd"
FAIL_REALTIME = "realtime_share"
FAIL_UNOBSERVED = "unobserved"

CONGESTION_COLUMNS = ("miles", "aadt", "peak_window", "peak_tt", "baseline_tt",
                      "baseline_source", "ratio", "weight", "delay_min", "vhd",
                      "realtime_share", "ref_tti", "urban_share", "urban_area")


def segment_congestion(
    baseline: pd.DataFrame,
    network: gpd.GeoDataFrame | pd.DataFrame,
    *,
    peak_windows: Sequence[str] = PEAK_WINDOWS,
    min_baseline_obs: int = BASELINE_MIN_OBS,
    fallback_col: str = BASELINE_FALLBACK_COL,
) -> pd.DataFrame:
    """Each segment's peak congestion **against its own baseline**, one row per segment.

    Args:
        baseline: a ``screen.segment_screen`` over ``screen.BASELINE_WINDOWS`` with
            ``quantiles=BASELINE_QUANTILES`` — per window ``<w>_travel_time``,
            ``<w>_n_obs``, ``<w>_realtime_share``, plus ``night_*`` and the
            ``weekday_tt_p15`` fallback.
        network: the XD network, with ``Miles`` and (ideally) ``AADT`` and the
            ``itd_layers.urban_context`` columns joined.

    Returns:
        A frame indexed by ``Segment ID`` over the network's segments, with

        - ``peak_window`` / ``peak_tt`` — the worse of ``peak_windows`` and its mean
          travel time (min);
        - ``baseline_tt`` / ``baseline_source`` — the overnight mean when the night
          has ``min_baseline_obs`` gated rows, else ``fallback_col`` when the weekday
          has them, else NaN / ``"none"``;
        - ``ratio`` = peak / baseline, ``weight`` = its smooth ramp from
          :data:`RATIO_ONSET` to :data:`RATIO_FULL` (0 where the ratio is unknown);
        - ``delay_min`` = peak − baseline (floored at 0), ``vhd`` = delay × AADT / 60
          (NaN without a volume);
        - ``realtime_share`` in the peak window; ``ref_tti`` = peak against INRIX's
          reference speed, for comparison with Item 46;
        - ``miles``, ``aadt``, ``urban_share``, ``urban_area`` from the network.
    """
    net = _ensure_indexed(network)
    scr = baseline
    if "Segment ID" in scr.columns and scr.index.name != "Segment ID":
        scr = scr.set_index("Segment ID")
    scr = scr.reindex(net.index)

    out = pd.DataFrame(index=net.index)
    out.index.name = "Segment ID"
    out["miles"] = pd.to_numeric(net.get("Miles"), errors="coerce")
    out["aadt"] = (pd.to_numeric(net["AADT"], errors="coerce") if "AADT" in net.columns
                   else float("nan"))

    tt_cols = [f"{w}_travel_time" for w in peak_windows if f"{w}_travel_time" in scr.columns]
    if not tt_cols:
        raise KeyError(f"The baseline screen carries none of {list(peak_windows)}.")
    tts = scr[tt_cols].astype("float64")
    has_peak = tts.notna().any(axis=1)
    out["peak_window"] = tts.fillna(-1.0).idxmax(axis=1).str.replace(
        "_travel_time", "", regex=False).where(has_peak)
    out["peak_tt"] = tts.max(axis=1)

    def _n(col):
        return pd.to_numeric(scr[col], errors="coerce").fillna(0) if col in scr.columns \
            else pd.Series(0, index=scr.index)

    night_ok = (_n("night_n_obs") >= min_baseline_obs) & scr.get(
        "night_travel_time", pd.Series(float("nan"), index=scr.index)).notna()
    fb = scr.get(fallback_col, pd.Series(float("nan"), index=scr.index))
    fb_ok = ~night_ok & (_n("weekday_n_obs") >= min_baseline_obs) & fb.notna()
    base = pd.Series(float("nan"), index=scr.index)
    if "night_travel_time" in scr.columns:
        base = base.where(~night_ok, scr["night_travel_time"])
    base = base.where(~fb_ok, fb)
    out["baseline_tt"] = base.where(base > 0)
    out["baseline_source"] = np.where(night_ok, "night",
                                      np.where(fb_ok, fallback_col, "none"))

    out["ratio"] = out["peak_tt"] / out["baseline_tt"]
    ramp = (out["ratio"] - RATIO_ONSET) / (RATIO_FULL - RATIO_ONSET)
    out["weight"] = ramp.clip(lower=0.0, upper=1.0).fillna(0.0)
    out["delay_min"] = (out["peak_tt"] - out["baseline_tt"]).clip(lower=0.0)
    out["vhd"] = (out["delay_min"] / 60.0 * out["aadt"]).where(out["aadt"] > 0)

    rt = pd.Series(float("nan"), index=scr.index)
    for w in peak_windows:
        col = f"{w}_realtime_share"
        if col in scr.columns:
            rt = rt.where(out["peak_window"] != w, scr[col])
    out["realtime_share"] = rt

    ref = pd.to_numeric(scr.get("ref_speed"), errors="coerce") \
        if "ref_speed" in scr.columns else pd.Series(float("nan"), index=scr.index)
    ref_tt = (out["miles"] / ref * 60.0).where(ref > 0)
    out["ref_tti"] = out["peak_tt"] / ref_tt

    out["urban_share"] = (pd.to_numeric(net["urban_share"], errors="coerce")
                          if "urban_share" in net.columns else float("nan"))
    out["urban_area"] = net["urban_area"] if "urban_area" in net.columns else None
    return out[list(CONGESTION_COLUMNS)]


@dataclass(frozen=True)
class CoreCandidate:
    """One run of congested segments on one chain, scored (Item 50)."""
    start: int
    """Index of the run's first segment in the chain."""
    stop: int
    """One past the run's last segment."""
    segment_ids: tuple[int, ...]
    miles: float
    effective_miles: float
    """``sum(min(miles, SEGMENT_MILES_CAP) x weight)``."""
    vhd: float
    vhd_per_mile: float
    """Per mile of segments whose congestion is known."""
    delay_per_mile: float
    """Minutes of delay per mile, against the baseline (no volume)."""
    realtime_share: float
    peak_ratio: float
    """Summed peak travel time over summed baseline travel time."""
    ref_tti: float
    """The same run against INRIX's reference speed (Item 46's measure)."""
    unknown_miles: float
    """Miles whose baseline or peak is missing — bridged, never counted as free flow."""
    n_aadt_missing: int
    baseline_sources: tuple[str, ...]
    fails: tuple[str, ...]

    @property
    def qualifies(self) -> bool:
        return not self.fails

    def metrics(self) -> dict:
        """The scoring numbers, rounded, for catalogue provenance and audit tables."""
        return {
            "miles": round(self.miles, 3),
            "effective_miles": round(self.effective_miles, 3),
            "vhd": round(self.vhd, 1),
            "vhd_per_mile": round(self.vhd_per_mile, 1),
            "delay_per_mile": round(self.delay_per_mile, 3),
            "realtime_share": round(self.realtime_share, 3),
            "peak_ratio": round(self.peak_ratio, 3),
            "ref_tti": round(self.ref_tti, 3),
            "unknown_miles": round(self.unknown_miles, 3),
            "n_aadt_missing": self.n_aadt_missing,
            "baseline_sources": list(self.baseline_sources),
            "fails": list(self.fails),
        }


def _run_metrics(seg: pd.DataFrame, ids: Sequence[int]) -> dict:
    """Length/delay/data totals over ``ids`` (rows of :func:`segment_congestion`)."""
    sub = seg.reindex(list(ids))
    miles = sub["miles"].fillna(0.0)
    known = sub["baseline_tt"].notna() & sub["peak_tt"].notna()
    known_miles = float(miles[known].sum())
    vhd = float(sub["vhd"].fillna(0.0).sum())
    delay = float(sub["delay_min"].where(known).fillna(0.0).sum())
    rt = sub["realtime_share"]
    rt_w = miles[rt.notna()]
    peak_known = sub["peak_tt"][known].sum()
    base_known = sub["baseline_tt"][known].sum()
    ref_tt = (sub["peak_tt"] / sub["ref_tti"])[known & sub["ref_tti"].notna()]
    return {
        "miles": float(miles.sum()),
        "known_miles": known_miles,
        "unknown_miles": float(miles[~known].sum()),
        "effective_miles": float((miles.clip(upper=SEGMENT_MILES_CAP) * sub["weight"]).sum()),
        "vhd": vhd,
        "vhd_per_mile": vhd / known_miles if known_miles > 0 else 0.0,
        "delay_per_mile": delay / known_miles if known_miles > 0 else 0.0,
        "realtime_share": (float((rt[rt.notna()] * rt_w).sum() / rt_w.sum())
                           if rt_w.sum() > 0 else float("nan")),
        "peak_ratio": float(peak_known / base_known) if base_known > 0 else float("nan"),
        "ref_tti": (float(sub["peak_tt"][ref_tt.index].sum() / ref_tt.sum())
                    if ref_tt.sum() > 0 else float("nan")),
        "n_aadt_missing": int((known & ~(sub["aadt"] > 0)).sum()),
        "baseline_sources": tuple(sorted(set(sub["baseline_source"].dropna()))),
    }


def core_fails(m: dict) -> tuple[str, ...]:
    """Which floors a run's metrics (:func:`_run_metrics`) miss."""
    fails = []
    if m["effective_miles"] < MIN_EFFECTIVE_CORE_MILES:
        fails.append(FAIL_EFFECTIVE_MILES)
    if m["vhd_per_mile"] < MIN_CORE_VHD_PER_MILE:
        fails.append(FAIL_VHD_PER_MILE)
    if m["vhd"] < MIN_CORE_VHD:
        fails.append(FAIL_VHD)
    if not (m["realtime_share"] >= MIN_REALTIME_SHARE):     # NaN fails too
        fails.append(FAIL_REALTIME)
    return tuple(fails)


def find_cores(chain_segments: Sequence[int], seg: pd.DataFrame, *,
               exclude: set[int] | frozenset[int] = frozenset()) -> list[CoreCandidate]:
    """Every congested run on a chain, scored — qualifying or not.

    A run starts and ends on a segment at :data:`CORE_SEED_RATIO` or worse and bridges
    up to :data:`CORE_GAP_SEGMENTS` / :data:`CORE_GAP_MILES` of anything between —
    less congested segments *and* segments whose congestion is unknown. Each run is
    scored on its effective miles, its delay and its data (:func:`core_fails`).

    ``exclude`` segments (another facility's core, Item 51) are hard breaks: never a
    seed, never bridged.

    Returns candidates strongest first (total VHD, then effective miles).
    """
    ids = [int(s) for s in chain_segments]
    ratio = seg["ratio"].reindex(ids)
    miles = seg["miles"].reindex(ids).fillna(0.0)
    seeds = [i for i, r in enumerate(ratio)
             if pd.notna(r) and r >= CORE_SEED_RATIO and ids[i] not in exclude]
    runs: list[tuple[int, int]] = []
    for i in seeds:
        if runs:
            lo, hi = runs[-1]
            gap = range(hi, i)
            if (len(gap) <= CORE_GAP_SEGMENTS
                    and float(miles.iloc[list(gap)].sum()) <= CORE_GAP_MILES
                    and not any(ids[g] in exclude for g in gap)):
                runs[-1] = (lo, i + 1)
                continue
        runs.append((i, i + 1))

    out = []
    for lo, hi in runs:
        run_ids = ids[lo:hi]
        m = _run_metrics(seg, run_ids)
        out.append(CoreCandidate(
            start=lo, stop=hi, segment_ids=tuple(run_ids), miles=m["miles"],
            effective_miles=m["effective_miles"], vhd=m["vhd"],
            vhd_per_mile=m["vhd_per_mile"], delay_per_mile=m["delay_per_mile"],
            realtime_share=m["realtime_share"], peak_ratio=m["peak_ratio"],
            ref_tti=m["ref_tti"], unknown_miles=m["unknown_miles"],
            n_aadt_missing=m["n_aadt_missing"],
            baseline_sources=m["baseline_sources"], fails=core_fails(m),
        ))
    out.sort(key=lambda c: (-c.vhd, -c.effective_miles))
    return out


def monthly_delay_profile(
    segment_ids: Sequence[int],
    seg: pd.DataFrame,
    monthly: pd.DataFrame,
    *,
    peak_windows: Sequence[str] = PEAK_WINDOWS,
) -> pd.Series:
    """A core's vehicle-hours of peak delay **per month**, indexed by ``"YYYY-MM"``.

    Each segment-month is read at its worse peak window and measured against the
    segment's **export-wide** baseline (``seg["baseline_tt"]``), so a month that is
    slow because of a work zone shows as delay rather than moving its own baseline.
    Segments with no baseline or AADT contribute nothing.
    """
    ids = [int(x) for x in segment_ids]
    sub = monthly[monthly["Segment ID"].isin(ids)]
    cols = [f"{w}_travel_time" for w in peak_windows if f"{w}_travel_time" in sub.columns]
    if sub.empty or not cols:
        return pd.Series(dtype="float64")
    peak = sub[cols].max(axis=1)
    base = sub["Segment ID"].map(seg["baseline_tt"])
    aadt = sub["Segment ID"].map(seg["aadt"])
    vhd = ((peak - base).clip(lower=0.0) / 60.0 * aadt).where(aadt > 0)
    return vhd.groupby(sub["month"]).sum(min_count=1).dropna().sort_index()


def episodic_flag(profile: pd.Series) -> str:
    """``""``, or an ``episodic:`` / ``seasonal:`` sentence saying the delay is
    concentrated in a few months (:data:`EPISODIC_SHARE`, :data:`SEASONAL_SHARE`)."""
    profile = profile[profile.notna()]
    n = len(profile)
    total = float(profile.sum())
    if n < EPISODIC_MIN_MONTHS or total <= 0:
        return ""
    k = max(1, int(np.ceil(n * EPISODIC_TOP_FRACTION)))
    top = profile.sort_values(ascending=False).iloc[:k]
    share = float(top.sum()) / total
    months = ", ".join(sorted(top.index))
    if share >= EPISODIC_SHARE:
        return (f"episodic: {share:.0%} of peak delay in {months} ({k} of {n} months) — "
                f"check for a work zone or event before reading it as recurring")
    if share >= SEASONAL_SHARE:
        return f"seasonal: {share:.0%} of peak delay in {months} ({k} of {n} months)"
    return ""


def _stop(kind: SplitKind, ids: Sequence[int], index: int, detail: str) -> SplitPoint:
    """A boundary the extent builder hit, in the shape ``describe_split`` reads."""
    index = max(0, min(index, len(ids) - 1))
    return SplitPoint(segment_index=index, segment_id=int(ids[index]), kind=kind,
                      score=1.0, details={"detail": detail})


_HARD_SPLITS = (SplitKind.JUNCTION, SplitKind.URBAN_RURAL, SplitKind.AADT_STEP)


def _core_is_urban(seg: pd.DataFrame, ids: Sequence[int]) -> bool:
    sub = seg.reindex(list(ids))
    w = sub["miles"].fillna(0.0)
    share = sub["urban_share"]
    if share.isna().all() or w.sum() <= 0:
        return False
    return float((share.fillna(0.0) * w).sum() / w.sum()) >= URBAN_SHARE_INSIDE


def grow_congested_extent(
    chain_segments: Sequence[int],
    seg: pd.DataFrame,
    core: CoreCandidate,
    split_points: Sequence[SplitPoint] = (),
) -> tuple[int, int, SplitPoint | None, SplitPoint | None]:
    """Tier 2: grow outward from a core while the congestion continues.

    Each side grows one segment at a time and stops at the first of:

    - a junction, FRC or AADT split (the old Tier 2 bound, now only the outer limit);
    - the end of the congestion — more than :data:`CORE_GAP_SEGMENTS` /
      :data:`CORE_GAP_MILES` of segments under :data:`RATIO_ONSET` (unknown
      segments are bridged like them, never read as free flow);
    - **dilution** — adding the segment would drop the grown extent's VHD per mile
      under :data:`SPILL_RETENTION` of the core's;
    - **the urban-area boundary, but only as a guide**: past it (for a core inside
      an urban area) no gap is bridged, so the extent continues only through
      segments that are themselves congested and pass the dilution test. Congestion
      that spills past the city line is kept; free flow beyond it is not.

    Returns ``(start, stop, upstream_boundary, downstream_boundary)``: chain indices
    of the grown extent (``stop`` exclusive) and why each side ended.
    """
    ids = [int(s) for s in chain_segments]
    n = len(ids)
    ratio = seg["ratio"].reindex(ids)
    miles = seg["miles"].reindex(ids).fillna(0.0)
    urban = seg["urban_share"].reindex(ids)
    core_urban = _core_is_urban(seg, core.segment_ids)
    area = ""
    if core_urban:
        names = seg["urban_area"].reindex(list(core.segment_ids)).dropna()
        area = str(names.mode().iloc[0]) if len(names) else ""
    core_rate = core.vhd_per_mile
    lo, hi = core.start, core.stop

    hard_up = [s for s in split_points if s.kind in _HARD_SPLITS and s.segment_index <= lo]
    hard_down = [s for s in split_points if s.kind in _HARD_SPLITS and s.segment_index >= hi]
    limit_lo = max((s.segment_index for s in hard_up), default=0)
    limit_hi = min((s.segment_index for s in hard_down), default=n)
    split_lo = max(hard_up, key=lambda s: s.segment_index) if hard_up else None
    split_hi = min(hard_down, key=lambda s: s.segment_index) if hard_down else None

    def _grow(step: int) -> tuple[int, SplitPoint | None]:
        nonlocal lo, hi
        pending: list[int] = []
        i = lo - 1 if step < 0 else hi
        while True:
            if (step < 0 and i < limit_lo) or (step > 0 and i >= limit_hi):
                split = split_lo if step < 0 else split_hi
                return (lo if step < 0 else hi), split
            r = ratio.iloc[i]
            outside = (core_urban and pd.notna(urban.iloc[i])
                       and urban.iloc[i] < URBAN_SHARE_INSIDE)
            congested = pd.notna(r) and r >= RATIO_ONSET
            if not congested:
                if outside:
                    return (lo if step < 0 else hi), _stop(
                        SplitKind.URBAN_BOUNDARY, ids, i,
                        f"leaving {area or 'the urban area'}; ratio "
                        f"{'unknown' if pd.isna(r) else f'{r:.2f}'} beyond it")
                pending.append(i)
                if len(pending) > CORE_GAP_SEGMENTS or \
                        float(miles.iloc[pending].sum()) > CORE_GAP_MILES:
                    return (lo if step < 0 else hi), _stop(
                        SplitKind.CONGESTION_END, ids, pending[0],
                        f"peak/baseline under {RATIO_ONSET} for "
                        f"{float(miles.iloc[pending].sum()):.2f} mi")
                i += step
                continue
            new_lo, new_hi = (i, hi) if step < 0 else (lo, i + 1)
            m = _run_metrics(seg, ids[new_lo:new_hi])
            if m["vhd_per_mile"] < SPILL_RETENTION * core_rate:
                return (lo if step < 0 else hi), _stop(
                    SplitKind.DILUTION, ids, i,
                    f"extending would fall to {m['vhd_per_mile']:.0f} VHD/mi, under "
                    f"{SPILL_RETENTION:.0%} of the core's {core_rate:.0f}")
            lo, hi = new_lo, new_hi
            pending = []
            i += step

    _, up = _grow(-1)
    _, down = _grow(+1)
    return lo, hi, up, down


def context_extent(
    chain_segments: Sequence[int],
    seg: pd.DataFrame,
    start: int,
    stop: int,
    split_points: Sequence[SplitPoint] = (),
    *,
    urban: bool = False,
    pad_miles: float = CONTEXT_PAD_MILES,
) -> tuple[int, int, SplitPoint | None, SplitPoint | None]:
    """Tier 3: context around a Tier 2 extent, never the whole chain.

    Each side reaches to the nearest junction/FRC/AADT split, and no further than
    ``pad_miles``; for an urban core it also stops at the urban-area boundary, the
    natural edge of a town's corridor. Tier 3 is **reported, never ranked**.
    """
    ids = [int(s) for s in chain_segments]
    n = len(ids)
    miles = seg["miles"].reindex(ids).fillna(0.0)
    share = seg["urban_share"].reindex(ids)

    def _reach(step: int) -> tuple[int, SplitPoint | None]:
        edge = start if step < 0 else stop
        hard = [s for s in split_points if s.kind in _HARD_SPLITS
                and ((step < 0 and s.segment_index <= start)
                     or (step > 0 and s.segment_index >= stop))]
        split = (max(hard, key=lambda s: s.segment_index) if step < 0
                 else min(hard, key=lambda s: s.segment_index)) if hard else None
        limit = split.segment_index if split is not None else (0 if step < 0 else n)
        covered = 0.0
        i = edge - 1 if step < 0 else edge
        while (step < 0 and i >= limit) or (step > 0 and i < limit):
            if urban and pd.notna(share.iloc[i]) and share.iloc[i] < URBAN_SHARE_INSIDE:
                return (i + 1 if step < 0 else i), _stop(
                    SplitKind.URBAN_BOUNDARY, ids, i, "the edge of the urban area")
            covered += float(miles.iloc[i])
            if covered > pad_miles:
                return (i + 1 if step < 0 else i), _stop(
                    SplitKind.CONTEXT_LIMIT, ids, i, f"{pad_miles:g} mi past Tier 2")
            i += step
        return limit, split

    lo, up = _reach(-1)
    hi, down = _reach(+1)
    return min(lo, start), max(hi, stop), up, down


def _alternative(tier: ExtentTier, ids: Sequence[int], net_idx, lo: int, hi: int,
                 rationale: str, up: SplitPoint | None,
                 down: SplitPoint | None) -> ExtentAlternative:
    segs = tuple(int(s) for s in ids[lo:hi])
    miles = 0.0
    for s in segs:
        if s in net_idx.index:
            v = net_idx.loc[s, "Miles"]
            if isinstance(v, pd.Series):
                v = v.iloc[0]
            if pd.notna(v):
                miles += float(v)
    return ExtentAlternative(
        tier=tier, segment_ids=segs, miles=round(miles, 3),
        start_latlon=_get_coords(net_idx, segs[0], end=False),
        end_latlon=_get_coords(net_idx, segs[-1], end=True),
        split_rationale=rationale, bounding_splits=(up, down),
    )


@dataclass
class DirectionAnalysis:
    """One chain (one direction) cored and tiered on its own data (Item 50)."""
    chain: MainlineChain
    candidates: list[CoreCandidate]
    core: CoreCandidate | None = None
    tiers: dict[ExtentTier, ExtentAlternative] = field(default_factory=dict)

    @property
    def qualifying(self) -> list[CoreCandidate]:
        return [c for c in self.candidates if c.qualifies]


def _tiers_for(chain: MainlineChain, core: CoreCandidate, seg: pd.DataFrame,
               splits: Sequence[SplitPoint], net_idx) -> dict[ExtentTier, ExtentAlternative]:
    ids = list(chain.segment_ids)
    core_alt = _alternative(
        ExtentTier.CORE, ids, net_idx, core.start, core.stop,
        (f"Recurrent congestion core ({core.miles:.2f} mi): peak/baseline "
         f"{core.peak_ratio:.2f}, {core.vhd_per_mile:.0f} VHD/mi, "
         f"{core.realtime_share:.0%} real-time data"),
        _stop(SplitKind.CONGESTION_END, ids, max(core.start - 1, 0),
              f"no segment at peak/baseline >= {CORE_SEED_RATIO} upstream")
        if core.start > 0 else None,
        _stop(SplitKind.CONGESTION_END, ids, core.stop,
              f"no segment at peak/baseline >= {CORE_SEED_RATIO} downstream")
        if core.stop < len(ids) else None,
    )
    lo, hi, up, down = grow_congested_extent(ids, seg, core, splits)
    m = _run_metrics(seg, ids[lo:hi])
    commuter = _alternative(
        ExtentTier.COMMUTER, ids, net_idx, lo, hi,
        (f"Congested extent grown from the core ({m['miles']:.2f} mi, "
         f"{m['vhd_per_mile']:.0f} VHD/mi, "
         f"{m['vhd_per_mile'] / core.vhd_per_mile:.0%} of the core's)"
         if core.vhd_per_mile > 0 else f"Congested extent ({m['miles']:.2f} mi)"),
        up, down)
    c_lo, c_hi, c_up, c_down = context_extent(
        ids, seg, lo, hi, splits, urban=_core_is_urban(seg, core.segment_ids))
    cm = _run_metrics(seg, ids[c_lo:c_hi])
    context = _alternative(
        ExtentTier.REGIONAL, ids, net_idx, c_lo, c_hi,
        f"Context around the congested extent ({cm['miles']:.2f} mi; reported, not ranked)",
        c_up, c_down)
    return {ExtentTier.CORE: core_alt, ExtentTier.COMMUTER: commuter,
            ExtentTier.REGIONAL: context}


def _core_county(net_idx, core: CoreCandidate | None) -> str:
    """The county most of a core's miles lie in, or ``""``."""
    if core is None or "County" not in net_idx.columns:
        return ""
    sub = net_idx.reindex([s for s in core.segment_ids if s in net_idx.index])
    if sub.empty:
        return ""
    miles = pd.to_numeric(sub["Miles"], errors="coerce").fillna(0.0)
    by = miles.groupby(sub["County"].astype(str).str.strip()).sum()
    by = by[by.index.str.len() > 0]
    return str(by.idxmax()) if len(by) else ""


def _core_town(seg: pd.DataFrame, core: CoreCandidate | None) -> str:
    """The urban area a core mostly lies in (``"Hailey"``), or ``""``."""
    if core is None:
        return ""
    sub = seg.reindex(list(core.segment_ids))
    inside = sub[sub["urban_share"].fillna(0.0) >= URBAN_SHARE_INSIDE]
    if inside.empty or inside["urban_area"].dropna().empty:
        return ""
    return str(inside["urban_area"].dropna().mode().iloc[0]).split(",")[0].strip()


MIRROR_MIN_COVER = 0.3
"""An opposite chain's mirrored slice must be at least this share of the footprint's
length to count as its other direction."""


def _runs_alongside(slice_geom, footprint_geom, *, max_sep_m: float = PAIR_MAX_MEAN_SEP_M,
                    min_share: float = 0.5, step_m: float = 50.0,
                    min_cover: float = MIRROR_MIN_COVER) -> bool:
    """Whether an opposite chain's mirrored slice really runs beside a footprint (Item 51).

    ``mirror_extent`` snaps a footprint's ends to the nearest points of any chain, and a
    chain that only *touches* it at a junction snaps to its own end there: Twin Falls'
    US-93 northbound on Pole Line Rd ends where US-93 turns onto Blue Lakes Blvd, and
    that one clamped segment was claimed as Blue Lakes' other direction, taking Pole
    Line's own core with it. A slice counts when at least ``min_share`` of its length
    (sampled every ``step_m``) lies within ``max_sep_m`` of the footprint — a couplet leg
    a block over (~180 m) is alongside — and it is at least ``min_cover`` of the
    footprint's length (0 for a companion *core*, naturally shorter than the lead's
    Tier 2)."""
    if slice_geom is None or footprint_geom is None or footprint_geom.length <= 0:
        return False
    if slice_geom.length < min_cover * footprint_geom.length:
        return False
    try:
        parts = list(slice_geom.geoms) if slice_geom.geom_type == "MultiLineString" \
            else [slice_geom]
        pts = [p.interpolate(d) for p in parts
               for d in np.arange(step_m / 2.0, max(p.length, step_m / 2.0 + 1e-9), step_m)]
    except Exception:
        return False
    if not pts:
        return False
    near = sum(1 for pt in pts if footprint_geom.distance(pt) <= max_sep_m)
    return near >= min_share * len(pts)


def _opposed_slice(net_idx, ids_a: Sequence[int], ids_b: Sequence[int]) -> bool:
    """Whether run ``b`` travels against run ``a``: ``b``'s start and end are projected
    onto ``a`` laid out in travel order, and ``b`` is opposed when its end lands behind
    its start by at least a quarter of its own length. A run's own direction, not its
    chain's majority ``Bearing`` — Burley's Overland Ave is part of a southbound SH-27
    chain one way and an eastbound I-84 BL chain the other — and not a chord either,
    which a bending Tier 2 makes meaningless."""
    from shapely.geometry import LineString

    coords = []
    for sid in ids_a:
        if sid not in net_idx.index:
            continue
        g = net_idx.at[sid, "geometry"] if "geometry" in net_idx.columns else None
        if g is None or g.is_empty:
            continue
        parts = list(g.geoms) if g.geom_type == "MultiLineString" else [g]
        pts = [c for p in parts for c in p.coords]
        # geometry may be digitised against travel; orient each piece by its endpoints
        try:
            s0 = segment_endpoint(net_idx, int(sid), end=False)
            if Point(pts[0]).distance(Point(s0[1], s0[0])) > Point(pts[-1]).distance(
                    Point(s0[1], s0[0])):
                pts = pts[::-1]
        except Exception:
            pass
        coords.extend(pts)
    if len(coords) < 2:
        return False
    line = LineString(coords)
    try:
        a = segment_endpoint(net_idx, int(ids_b[0]), end=False)
        b = segment_endpoint(net_idx, int(ids_b[-1]), end=True)
    except Exception:
        return False
    pa, pb = line.project(Point(a[1], a[0])), line.project(Point(b[1], b[0]))
    span = Point(a[1], a[0]).distance(Point(b[1], b[0]))
    return span > 0 and (pa - pb) > 0.25 * span


def _index_span(target: Sequence[int], alt: ExtentAlternative, network,
                metric_crs) -> tuple[int, int] | None:
    """The ``[lo, hi)`` indices on ``target`` that ``alt``'s footprint mirrors onto."""
    mirrored = mirror_extent(alt, target, network, metric_crs=metric_crs)
    if mirrored is None or not mirrored.segment_ids:
        return None
    pos = {s: i for i, s in enumerate(target)}
    idx = [pos[s] for s in mirrored.segment_ids if s in pos]
    return (min(idx), max(idx) + 1) if idx else None


def generate_catalogue(
    network: gpd.GeoDataFrame,
    baseline: pd.DataFrame,
    *,
    peak_windows: Sequence[str] = PEAK_WINDOWS,
    tiers: Sequence[ExtentTier] = DEFAULT_TIERS,
    min_chain_miles: float = MIN_CHAIN_MILES,
    max_facilities: int | None = None,
    observed: set[int] | None = None,
    note: str = "",
    audit: list | None = None,
    monthly: pd.DataFrame | None = None,
) -> dict:
    """Build a whole corridor catalogue from a district network (Items 46, 50).

    Walks the numbered mainlines (:func:`enumerate_mainline_chains`), pairs the
    carriageways (:func:`pair_chains`), and cores **each direction on its own data**
    (:func:`segment_congestion`, :func:`find_cores`). A facility is catalogued when
    either direction has a qualifying core; the stronger one leads.

    **The opposing direction answers for itself.** It is catalogued only where it
    has a qualifying core of its own overlapping the lead core's footprint, and then
    with its own boundaries. Otherwise it is **dropped**, and the group's
    ``_companion`` note says why (its own peak/baseline over the mirrored span) — a
    free-flowing direction summed into a reporting corridor only dilutes it.

    Tier 1 is the core (ranked); Tier 2 grows it while the congestion continues
    (:func:`grow_congested_extent`); Tier 3 is capped context
    (:func:`context_extent`). Tiers 2 and 3 carry ``_ranked: false``. Where two
    tiers cut the same segments in every direction, the **narrower** survives.

    Args:
        network: the district's XD network, **with link repairs applied**, and
            ``AADT`` (``aadt.join_aadt``, AADT 2025) and the
            ``itd_layers.urban_context`` columns joined.
        baseline: the baseline screen (see :func:`segment_congestion`).
        max_facilities: keep only the N facilities with the most core delay.
        observed: segment ids in the export; a core with none is not catalogued.
        monthly: ``screen.segment_monthly_screen`` over the peak windows. When given,
            each facility is checked for delay concentrated in a few months
            (:func:`episodic_flag`) and flagged — never dropped — in ``_flags``.
        audit: when given, one row per analysed direction is appended — every chain's
            strongest candidate with its metrics and the floors it failed — so the
            decisions can be checked segment by segment.

    Returns:
        ``{"_note", "_generated", "corridors", "reporting_corridors"}``, ready for
        ``corridors.parse_catalogue`` and ``resolve_catalogue``.
    """
    net_idx = _ensure_indexed(network)
    try:
        metric_crs = network.estimate_utm_crs()
    except Exception:
        metric_crs = "EPSG:3857"

    seg = segment_congestion(baseline, network, peak_windows=peak_windows)
    chains = enumerate_mainline_chains(network, min_miles=min_chain_miles)
    route_sets = segment_route_sets(network)
    pairs = pair_chains(chains, network, metric_crs=metric_crs)
    junctions = incoming_route_map(network)

    def _analyse(chain: MainlineChain) -> DirectionAnalysis:
        cands = find_cores(chain.segment_ids, seg)
        if observed is not None:
            cands = [c if any(s in observed for s in c.segment_ids)
                     else dataclasses.replace(c, fails=c.fails + (FAIL_UNOBSERVED,))
                     for c in cands]
        return DirectionAnalysis(chain=chain, candidates=cands)

    def _audit(a: DirectionAnalysis, role: str, facility: str = "") -> None:
        if audit is None:
            return
        best = a.core or (a.candidates[0] if a.candidates else None)
        row = {"route": a.chain.route_label, "road_name": a.chain.road_name,
               "direction": a.chain.direction,
               "county": a.chain.counties[0] if a.chain.counties else "",
               "chain_first_segment": a.chain.segment_ids[0],
               "chain_miles": a.chain.miles, "chain_routes": "/".join(a.chain.routes),
               "chain_joins": "; ".join(
                   f"{j['kind']} {j['route']} {j['from']}->{j['to']}" for j in a.chain.joins),
               "role": role, "facility": facility,
               "n_candidates": len(a.candidates), "n_qualifying": len(a.qualifying)}
        if best is not None:
            row.update({f"core_{k}": (";".join(v) if isinstance(v, list) else v)
                        for k, v in best.metrics().items()})
            row["core_first_segment"] = best.segment_ids[0]
            row["core_last_segment"] = best.segment_ids[-1]
        audit.append(row)

    def _overlaps(spans: list[tuple[int, int]], lo: int, hi: int) -> bool:
        return any(lo < s_hi and hi > s_lo for s_lo, s_hi in spans)

    def _mostly_in(spans: list[tuple[int, int]], lo: int, hi: int) -> bool:
        inside = {i for s_lo, s_hi in spans for i in range(max(lo, s_lo), min(hi, s_hi))}
        return hi > lo and len(inside) >= 0.5 * (hi - lo)

    def _held(d, c) -> bool:
        return (_overlaps(claimed[id(d)], c.start, c.stop)
                or _mostly_in(mirrored[id(d)], c.start, c.stop))

    # Cores are claimed across **all** chains, strongest first (Item 51). Since chains
    # follow concurrency, one segment can lie on two chains (SH-8 and US-95 on Moscow's
    # couplet, US-2 and US-95 through Sandpoint); a segment is in at most one ranked
    # core, and a core mostly inside a stronger facility's Tier 2 belongs to it.
    # Each chain is analysed once, and a lead core looks for its companion on **every**
    # opposite-direction chain sharing a route that lies along its footprint — not
    # only the one ``pair_chains`` married it to. Since Item 51 one direction can be a
    # single chain where the other is two (SH-8 eastbound breaks at a data gap past
    # Bovill), and one-to-one pairing then put the Moscow core's two directions in two
    # facilities.
    dirs_all = [_analyse(c) for c in chains]
    partner = {}
    for lead_c, other_c in pairs:
        if other_c is not None:
            partner[id(lead_c)] = other_c
            partner[id(other_c)] = lead_c
    by_chain = {id(d.chain): d for d in dirs_all}
    geoms = dict(zip((id(c) for c in chains),
                     _chain_geometries(chains, network, metric_crs=metric_crs)))

    # The routes on a chain's pavement, not only the one it walked: Burley's Overland
    # Ave is walked as SH-27 one way and I-84 BL the other, and carries both.
    carried = {id(c): frozenset().union(*(route_sets.get(s, frozenset())
                                          for s in c.segment_ids)) | frozenset(c.routes)
               for c in chains}

    def _opposites(d: DirectionAnalysis) -> list[DirectionAnalysis]:
        # Any other chain sharing a route; whether it runs the *other way* is judged on
        # the footprint itself (:func:`_opposed_slice`), because a merged chain's
        # majority bearing says little about one stretch of it.
        out = [o for o in dirs_all if o is not d
               and carried[id(o.chain)] & carried[id(d.chain)]]
        paired = partner.get(id(d.chain))
        out.sort(key=lambda o: (paired is None or o.chain is not paired, -o.chain.miles))
        return out

    def _alt_geom(ids):
        present = [s for s in ids if s in net_idx.index]
        if not present or "geometry" not in net_idx.columns:
            return None
        return gpd.GeoSeries(net_idx.loc[present, "geometry"].values,
                             crs=network.crs).to_crs(metric_crs).union_all()

    claimed: dict[int, list[tuple[int, int]]] = {id(d): [] for d in dirs_all}
    # Spans claimed on a chain by *another* direction's mirror, kept apart from the
    # chain's own Tier 2 spans: since Item 51 a mirror can land on a different route's
    # chain, and it holds only what lies mostly inside it (the southbound half of the
    # I-15 BL core down Pocatello's 5th Ave overlapped US-91's mirror at one end).
    mirrored: dict[int, list[tuple[int, int]]] = {id(d): [] for d in dirs_all}
    core_owner: dict[int, int] = {}
    t2_owner: dict[int, int] = {}
    miles_of = seg["miles"].fillna(0.0)

    def _share(core: CoreCandidate, owner: dict[int, int]) -> float:
        m = float(miles_of.reindex(list(core.segment_ids)).sum())
        inside = float(miles_of.reindex([s for s in core.segment_ids if s in owner]).sum())
        return inside / m if m > 0 else 0.0

    def _free(d: DirectionAnalysis, core: CoreCandidate) -> CoreCandidate | None:
        """``core`` as it may be catalogued beside the stronger facilities already taken.

        A core held by a claim — overlapping a stronger facility's Tier 2 on its own
        chain (Item 50), or mostly under another direction's mirror — is re-found with
        those segments taken out, and what qualifies stands.

        A core that only *shares* some pavement with another facility's core keeps it,
        flagged: trimming the shared blocks out of US-95's Moscow cores left pieces too
        short to stand, and their delay out of the ranking. A core that is *mostly*
        another's is that queue seen from a concurrent route; what is left of it once
        the other's segments are taken out stands if it still qualifies (I-15 BL down
        5th Ave south of Humbolt St, past where US-91's Pocatello core ends), else
        ``None``. The same holds for a core mostly inside a stronger facility's Tier 2."""
        if (not _held(d, core) and _share(core, core_owner) < SHARED_CORE_MAX
                and not _inside_t2(core)):
            return core
        ids = d.chain.segment_ids
        held = {ids[i] for lo, hi in claimed[id(d)] + mirrored[id(d)] for i in range(lo, hi)}
        taken = set(core_owner) | set(t2_owner) | held
        pieces = [c for c in find_cores(ids, seg, exclude=taken)
                  if c.qualifies and c.start < core.stop and c.stop > core.start]
        if observed is not None:
            pieces = [c for c in pieces if any(s in observed for s in c.segment_ids)]
        return pieces[0] if pieces else None

    def _inside_t2(core: CoreCandidate) -> bool:
        return _share(core, t2_owner) >= SHARED_CORE_MAX

    facilities: list[dict] = []
    pool = sorted(((d, c) for d in dirs_all for c in d.qualifying), key=lambda x: -x[1].vhd)
    with_facility: set[int] = set()
    for d, core in pool:
        freed = _free(d, core)
        if freed is None:
            _audit(DirectionAnalysis(d.chain, d.candidates, core),
                   "absorbed" if _held(d, core) else "absorbed_shared")
            continue
        core = freed
        n_now = len(facilities)
        splits = detect_split_points(list(d.chain.segment_ids), network,
                                     incoming_routes=junctions)
        lead = DirectionAnalysis(d.chain, d.candidates, core)
        lead.tiers = _tiers_for(d.chain, core, seg, splits, net_idx)
        t2 = lead.tiers[ExtentTier.COMMUTER]
        pos = {s: i for i, s in enumerate(d.chain.segment_ids)}
        claimed[id(d)].append((pos[t2.segment_ids[0]], pos[t2.segment_ids[-1]] + 1))
        t2_geom = _alt_geom(t2.segment_ids)

        companion, note, other = None, "", None
        spans: list[tuple[DirectionAnalysis, tuple[int, int]]] = []
        for o in _opposites(d):
            span = _index_span(list(o.chain.segment_ids), t2, network, metric_crs)
            if span is None:
                continue
            o_ids = o.chain.segment_ids[span[0]:span[1]]
            o_geom = _alt_geom(o_ids)
            if not _runs_alongside(o_geom, t2_geom):
                continue
            if not _opposed_slice(net_idx, t2.segment_ids, o_ids):
                continue
            spans.append((o, span))
        # The companion is the strongest qualifying core, on any chain sharing a route,
        # that runs beside the lead's Tier 2 the other way. Matched on the core itself,
        # not through the mirror: a business loop's chain passes the same street both
        # ways (Burley's I-84 BL on Overland Ave), and a mirror snaps to either pass.
        best_c = None
        for o in _opposites(d):
            for c in o.qualifying:
                c = _free(o, c)
                if c is None or (best_c is not None and c.vhd <= best_c[1].vhd):
                    continue
                if not (_runs_alongside(_alt_geom(c.segment_ids), t2_geom, min_cover=0.0)
                        and _opposed_slice(net_idx, t2.segment_ids, c.segment_ids)):
                    continue
                best_c = (o, c)
        if best_c is not None:
            companion = DirectionAnalysis(best_c[0].chain, best_c[0].candidates, best_c[1])
        if companion is not None:
            other = by_chain[id(companion.chain)]
            companion.tiers = _tiers_for(
                companion.chain, companion.core, seg,
                detect_split_points(list(companion.chain.segment_ids), network,
                                    incoming_routes=junctions), net_idx)
            c2 = companion.tiers[ExtentTier.COMMUTER]
            opos = {s: i for i, s in enumerate(companion.chain.segment_ids)}
            claimed[id(other)].append((opos[c2.segment_ids[0]], opos[c2.segment_ids[-1]] + 1))
        elif spans:
            other, span = spans[0]
            own = _run_metrics(seg, other.chain.segment_ids[span[0]:span[1]])
            note = (
                f"{other.chain.direction} not catalogued: no qualifying core of "
                f"its own opposite the {d.chain.direction} one"
                + (f" (peak/baseline {own['peak_ratio']:.2f}, "
                   f"{own['vhd_per_mile']:.0f} VHD/mi over the mirrored span)"
                   if pd.notna(own["peak_ratio"]) else "")
            )
        for o, span in spans:
            if companion is None or o.chain is not companion.chain:
                mirrored[id(o)].append(span)
        for m in [lead] + ([companion] if companion is not None else []):
            for sgm in m.core.segment_ids:
                core_owner.setdefault(sgm, n_now)
            for sgm in m.tiers[ExtentTier.COMMUTER].segment_ids:
                t2_owner.setdefault(sgm, n_now)
        shared: dict[int, float] = {}
        for m in [lead] + ([companion] if companion is not None else []):
            for sgm in m.core.segment_ids:
                if sgm in core_owner and core_owner[sgm] != n_now:
                    shared[core_owner[sgm]] = shared.get(core_owner[sgm], 0.0) + \
                        float(miles_of.get(sgm, 0.0))
        facilities.append({"lead": lead, "other": companion,
                           "other_chain": other.chain if other is not None else None,
                           "note": note, "score": core.vhd, "shared": shared})
        with_facility.add(id(d))
        if other is not None:
            with_facility.add(id(other))
    for d in dirs_all:
        if id(d) not in with_facility and not d.qualifying:
            _audit(d, "rejected")

    as_built = list(facilities)          # ``shared`` indexes this order
    facilities.sort(key=lambda f: -f["score"])
    if max_facilities is not None:
        for f in facilities[max_facilities:]:
            _audit(f["lead"], "capped")
        facilities = facilities[:max_facilities]

    entries: list[dict] = []
    groups: list[dict] = []
    used_ids: set[str] = set()

    used_names: set[str] = set()
    fac_ids: dict[int, str] = {}
    for fac in facilities:
        lead: DirectionAnalysis = fac["lead"]
        other: DirectionAnalysis | None = fac["other"]
        chain = lead.chain
        # Named for the road and the town (Item 51): "US-20: Northgate Mile, Idaho
        # Falls", not "US-20: Bonneville County (2)". The county the **core** lies in
        # stands in for a town outside every urban area.
        band, street, place = facility_naming(chain, lead.core, seg, net_idx, route_sets)
        facility_name = f"{band}: {street}, {place}" if street else f"{band}: {place}"
        facility_id = _slug(f"{band}-{street}-{place}" if street else f"{band}-{place}")
        if facility_name in used_names or facility_id in used_ids:
            # Two cores on one road in one town (SH-41 in Rathdrum): say where each
            # starts. The *name* has to differ as well as the id, or the tier
            # comparison collapses them.
            where = _endpoint_name(net_idx, lead.core.segment_ids[0], end=False)
            facility_name = f"{facility_name} (from {where})" if where else facility_name
            facility_id = _slug(f"{facility_id}-{where}") if where else facility_id
            n = 1
            base = facility_id
            while facility_id in used_ids or facility_name in used_names:
                n += 1
                facility_id = f"{base}-{n}"
                facility_name = f"{facility_name.rsplit(' #', 1)[0]} #{n}"
        used_ids.add(facility_id)
        used_names.add(facility_name)
        fac_ids[id(fac)] = facility_id
        _audit(lead, "lead", facility_id)
        if other is not None:
            _audit(other, "companion", facility_id)
        elif fac["other_chain"] is not None:
            audit_row = {"route": fac["other_chain"].route_label, "direction":
                         fac["other_chain"].direction, "role": "dropped_companion",
                         "facility": facility_id, "note": fac["note"]}
            if audit is not None:
                audit.append(audit_row)

        members = [lead] + ([other] if other is not None else [])
        # A wider tier that cuts exactly the narrower tier's segments, in every
        # catalogued direction, is the same extent twice; the narrower (ranked) one
        # survives.
        keep: list[ExtentTier] = []
        for tier in tiers:
            prev = keep[-1] if keep else None
            if prev is not None and all(
                    m.tiers[tier].segment_ids == m.tiers[prev].segment_ids for m in members):
                continue
            keep.append(tier)

        meta = {
            "_core": lead.core.metrics(),
            "_directions": [m.chain.direction for m in members],
            "_flags": [],
        }
        # A core sharing concurrent pavement with a stronger facility's core keeps it
        # and says so (Item 51): its delay is in both rows.
        for owner, mi in sorted(fac.get("shared", {}).items()):
            other_id = fac_ids.get(id(as_built[owner]), "a capped facility")
            meta["_flags"].append(f"shares {mi:.2f} mi with {other_id}")
        if monthly is not None:
            core_ids = [s for m in members for s in m.core.segment_ids]
            profile = monthly_delay_profile(core_ids, seg, monthly,
                                            peak_windows=peak_windows)
            flag = episodic_flag(profile)
            if flag:
                meta["_flags"].append(flag)
            meta["_monthly_vhd"] = {k: round(float(v), 1) for k, v in profile.items()}
        if audit is not None and meta["_flags"]:
            for row in audit:
                if row.get("facility") == facility_id:
                    row["flags"] = "; ".join(meta["_flags"])
        if fac["note"]:
            meta["_companion"] = fac["note"]
        for tier in keep:
            other_alt = other.tiers[tier] if other is not None else None
            tier_entries, group = extent_catalogue_entries(
                lead.tiers[tier], chain, other_alt,
                other.chain if other_alt is not None else None, network,
                facility_id=facility_id, facility_name=facility_name, group_meta=meta,
            )
            if not tier_entries:
                continue
            entries.extend(tier_entries)
            groups.append(group)

    return {
        "_note": note or (
            "Generated by inrix_tools.extents.generate_catalogue (ROADMAP Items 46, 50) "
            "— mainline chains walked from the XD topology, cores scored on recurring "
            "peak congestion against each segment's own baseline. Do not hand-edit; "
            "re-run the builder."
        ),
        "_generated": {
            "peak_windows": list(peak_windows),
            "n_chains": len(chains),
            "n_facilities": len(facilities),
            "tiers": [t.value for t in tiers],
            "ranked_tier": ExtentTier.CORE.value,
            "thresholds": {
                "baseline": f"night mean with >= {BASELINE_MIN_OBS} gated obs, else "
                            f"{BASELINE_FALLBACK_COL}",
                "ratio_onset": RATIO_ONSET, "ratio_full": RATIO_FULL,
                "core_seed_ratio": CORE_SEED_RATIO,
                "core_gap": [CORE_GAP_SEGMENTS, CORE_GAP_MILES],
                "segment_miles_cap": SEGMENT_MILES_CAP,
                "min_effective_core_miles": MIN_EFFECTIVE_CORE_MILES,
                "min_core_vhd_per_mile": MIN_CORE_VHD_PER_MILE,
                "min_core_vhd": MIN_CORE_VHD,
                "min_realtime_share": MIN_REALTIME_SHARE,
                "spill_retention": SPILL_RETENTION,
                "episodic": {"top_fraction_of_months": round(EPISODIC_TOP_FRACTION, 3),
                             "share": EPISODIC_SHARE, "seasonal_share": SEASONAL_SHARE,
                             "min_months": EPISODIC_MIN_MONTHS},
                "context_pad_miles": CONTEXT_PAD_MILES,
            },
        },
        "corridors": entries,
        "reporting_corridors": groups,
    }


# ─── Route junctions as link repairs (ROADMAP Item 51) ───────────────

ROUTE_JUNCTION = "route_junction"
"""``corridors`` repair kind: a link replaced where the route turns off it (trap 4)."""


def route_junction_repairs(network: gpd.GeoDataFrame) -> pd.DataFrame:
    """The route junctions of :func:`enumerate_mainline_chains` that are safe to write
    into the ``NextXDSegI`` repair table, so that ``corridors.build_chain`` — which walks
    the link alone — can resolve a catalogue entry across them (D3's US-95 at Payette's
    16th St).

    A junction qualifies when the segment's own link is null or leaves **every** route
    the segment carries (Payette's northbound US-95 links onto S Main St, which is off
    the system), and every route that breaks there turns onto the same head. Stub
    junctions are not written: there the link still serves the route's own stub, and
    only the chain walk, which knows the milepost evidence, may leave it.

    Returns a frame of ``corridors.REPAIR_COLUMNS`` with ``kind`` =
    :data:`ROUTE_JUNCTION` (``xdgroup`` is the head's: the repair crosses groups, which
    is what distinguishes it from Item 38's).
    """
    from .corridors import REPAIR_COLUMNS

    net_idx = _ensure_indexed(network)
    sets = segment_route_sets(network)
    sets = {s: r for s, r in sets.items() if s in net_idx.index}
    by_route: dict[str, set[int]] = {}
    for sid, rs in sets.items():
        for r in rs:
            by_route.setdefault(r, set()).add(sid)
    nxt_raw = {int(s): int(n) for s, n in pd.to_numeric(
        net_idx.get("NextXDSegI", pd.Series(dtype=float)), errors="coerce").dropna().items()}
    cg = _ChainGeometry(net_idx, set(sets))
    found: dict[int, dict[str, tuple[int, dict]]] = {}
    for route, members in by_route.items():
        walks = _walk_route(members, nxt_raw)
        linked_into = {n for s in members if (n := nxt_raw.get(s)) in members}
        for _, log in _join_route_walks(route, walks, cg, linked_into):
            for j in log:
                if j["kind"] == "junction":
                    found.setdefault(j["from"], {})[route] = (j["to"], j)
    rows = []
    for sid, per_route in sorted(found.items()):
        targets = {to for to, _ in per_route.values()}
        if len(targets) != 1:
            continue
        old = nxt_raw.get(sid)
        if old is not None and sets.get(old, frozenset()) & sets.get(sid, frozenset()):
            continue
        to, j = next(iter(per_route.values()))
        rows.append({
            "segment": sid, "old_next": old, "new_next": to, "kind": ROUTE_JUNCTION,
            "gap_m": j["gap_m"], "bearing_delta_deg": j["turn_deg"],
            "xdgroup": net_idx.at[to, "XDGroup"] if "XDGroup" in net_idx.columns else None,
            "road_name": net_idx.at[sid, "RoadName"] if "RoadName" in net_idx.columns else None,
            "lanes": net_idx.at[sid, "Lanes"] if "Lanes" in net_idx.columns else None,
        })
    out = pd.DataFrame(rows, columns=list(REPAIR_COLUMNS))
    if not out.empty:
        out["segment"] = out["segment"].astype("int64")
        out["new_next"] = out["new_next"].astype("int64")
        out["old_next"] = out["old_next"].astype("Int64")
    return out

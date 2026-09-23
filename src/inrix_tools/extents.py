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

# Criterion 3: AADT
AADT_RELATIVE_GRADIENT = 0.40  # 40% relative change threshold
AADT_ABSOLUTE_STEP = 8000      # Minimum absolute vpd change

# Criterion 4: Congestion
TTI_CONGESTED = 1.20           # TTI threshold for "congested" side of discontinuity
TTI_FREEFLOW = 1.08            # TTI threshold for "free-flowing" side
CONGESTION_RUN_MIN_SEGS = 3    # Minimum consecutive segments to confirm discontinuity
RECURRENCE_DROP = 0.40         # Recurrence rate below which congestion "ends"
VHD_BOTTLENECK = 150.0         # vhd/mile indicating bottleneck
VHD_FREEFLOW = 25.0            # vhd/mile indicating free-flow

# Dilution
DILUTION_FACTOR_THRESHOLD = 5.0  # If Tier1 rate / Tier3 rate > 5, must partition


class SplitKind(str, Enum):
    """Classification of split point types."""
    URBAN_RURAL = "urban_rural"
    JUNCTION = "junction"
    AADT_STEP = "aadt_step"
    CONGESTION_DROP = "congestion_drop"


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


def detect_junction_splits(
    chain_segments: Sequence[int],
    network: gpd.GeoDataFrame | pd.DataFrame,
    full_network: gpd.GeoDataFrame | pd.DataFrame | None = None,
) -> list[SplitPoint]:
    """Detect major highway-to-highway junctions along a chain."""
    if len(chain_segments) < 2:
        return []
    net = _ensure_indexed(network)
    full = _ensure_indexed(full_network) if full_network is not None else net
    splits: list[SplitPoint] = []

    # Map target segment -> incoming segments
    incoming_routes: dict[int, set[str]] = {}
    if "NextXDSegI" in full.columns:
        for sid, row in full.iterrows():
            nxt = row.get("NextXDSegI")
            rn = row.get("RoadNumber")
            if pd.notna(nxt) and pd.notna(rn):
                rstr = str(rn).strip()
                if rstr:
                    try:
                        nxt_int = int(nxt)
                        incoming_routes.setdefault(nxt_int, set()).add(rstr)
                    except (ValueError, TypeError):
                        pass

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

        # Case 1: Route number changes along the chain
        if rn_prev and rn_curr and rn_prev != rn_curr:
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
) -> list[SplitPoint]:
    """Detect all split points along a chain, combining all four criteria."""
    splits: list[SplitPoint] = []
    splits.extend(detect_urban_rural_splits(chain_segments, network))
    splits.extend(detect_junction_splits(chain_segments, network, full_network))
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
) -> ChainAnalysis:
    """Complete chain analysis: detect splits and build tier alternatives."""
    splits = detect_split_points(
        chain_segments, network,
        full_network=full_network,
        recurrence=recurrence,
        screen_data=screen_data,
        window=window,
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
    """One maximal run of a numbered route's segments in travel order.

    Built by walking ``NextXDSegI`` **within one route number**, so the chain
    stays on one carriageway (``NextXDSegI`` is directional) and does not wander
    onto the cross-route at a junction. A divided highway therefore yields two
    chains — that is the point; they are paired by :func:`pair_chains`.
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

    @property
    def n_segments(self) -> int:
        return len(self.segment_ids)

    @property
    def direction(self) -> str:
        """``NB``/``SB``/``EB``/``WB``, or the raw bearing if it is not cardinal."""
        return _DIR_LABEL.get(self.bearing, self.bearing)


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


def enumerate_mainline_chains(
    network: gpd.GeoDataFrame | pd.DataFrame,
    *,
    min_miles: float = MIN_CHAIN_MILES,
    route_numbers: Sequence[str] | None = None,
) -> list[MainlineChain]:
    """Walk every numbered route in ``network`` into maximal directional chains.

    The walk is topological, never geographic (ROADMAP Item 36): it follows
    ``NextXDSegI`` and only through segments carrying the **same route number**,
    so the chain ends where the route ends rather than continuing onto whatever
    happens to lie ahead. Apply :func:`corridors.apply_link_repairs` to the
    network first if you want the Item 38 repairs honoured — this reads
    ``NextXDSegI`` as it finds it.

    Args:
        network: XD network with ``XDSegID``, ``NextXDSegI``, ``RoadNumber``,
            ``Bearing``, ``Miles``.
        min_miles: drop chains shorter than this (stubs and connector fragments).
        route_numbers: optional whitelist of route numbers to walk.

    Returns:
        Chains sorted by miles descending. Every numbered segment lands in
        exactly one chain (segments left over after the head walk — routes whose
        topology forms a cycle — are walked from their lowest remaining id).
    """
    if network is None or len(network) == 0:
        return []

    net = network
    rn = net["RoadNumber"].astype(str).str.strip()
    keep = rn.ne("") & rn.ne("None") & rn.ne("nan")
    if route_numbers is not None:
        keep &= rn.isin([str(r).strip() for r in route_numbers])
    net = net[keep].copy()
    if net.empty:
        return []
    net["_rn"] = rn[keep]

    ids = {int(s) for s in net["XDSegID"]}
    route_of = {int(s): r for s, r in zip(net["XDSegID"], net["_rn"])}

    # The band is a property of the *route*, not of a chain on it. Reading it per
    # chain labels I-90's Coeur d'Alene business route "SH-90", because no segment
    # of it is called "I-90" — every segment is called "Northwest Blvd".
    labels: dict[str, str] = {}
    for route, grp in net.groupby("_rn"):
        names = [str(v).strip() for v in grp.get("RoadName", pd.Series(dtype=object)).dropna()
                 if str(v).strip()]
        frc_vals = pd.to_numeric(grp.get("FRC", pd.Series(dtype=float)), errors="coerce").dropna()
        labels[str(route)] = route_label(route, names,
                                         int(frc_vals.min()) if len(frc_vals) else None)

    nxt: dict[int, int] = {}
    for sid, nid in zip(net["XDSegID"], net.get("NextXDSegI", pd.Series(dtype="float"))):
        if pd.isna(nid):
            continue
        nid = int(nid)
        sid = int(sid)
        if nid in ids and route_of[nid] == route_of[sid]:
            nxt[sid] = nid

    by_id = net.set_index("XDSegID", drop=False)
    predecessors = set(nxt.values())
    visited: set[int] = set()
    walks: list[list[int]] = []

    def _walk(start: int) -> list[int]:
        chain, cur = [], start
        while cur is not None and cur in ids and cur not in visited:
            chain.append(cur)
            visited.add(cur)
            cur = nxt.get(cur)
        return chain

    for head in sorted(ids - predecessors):
        if head not in visited:
            walks.append(_walk(head))
    # Anything still unvisited sits on a cycle; start it somewhere deterministic.
    for sid in sorted(ids - visited):
        walk = _walk(sid)
        if walk:
            walks.append(walk)

    out: list[MainlineChain] = []
    for walk in walks:
        sub = by_id.loc[walk]
        total_miles = round(float(pd.to_numeric(sub["Miles"], errors="coerce").fillna(0.0).sum()), 3)
        if total_miles < min_miles:
            continue
        bearings = [str(b).strip() for b in sub["Bearing"].dropna() if str(b).strip()]
        bearing = _mode(bearings)
        names = [str(v).strip() for v in sub.get("RoadName", pd.Series(dtype=object)).dropna()
                 if str(v).strip()]
        route = str(sub["_rn"].iloc[0])
        out.append(MainlineChain(
            route_number=route,
            bearing=bearing,
            segment_ids=tuple(int(s) for s in walk),
            miles=total_miles,
            road_name=_mode(names),
            route_label=labels.get(route, route_label(route, names)),
            localities=_ranked_values(sub, "PostalCode"),
            counties=_ranked_values(sub, "County"),
        ))

    out.sort(key=lambda c: -c.miles)
    return out


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

    Pairing is by **route identity plus proximity**, in that order: same route
    number, opposing cardinal bearing, and a mean lateral separation under
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
                if other.route_number != chain.route_number or geoms[j] is None:
                    continue
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
) -> tuple[list[dict], dict]:
    """Emit the catalogue entries and reporting group for one tier of one facility.

    Returns ``(entries, group)`` in ``corridors.parse_catalogue`` /
    ``parse_reporting_corridors`` shape. Underscore-prefixed keys carry the
    provenance the parser ignores but the tier comparison reads back
    (``_tier``, ``_facility``, ``_split_rationale``, ``_segment_ids``).
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

    up, down = alt.bounding_splits
    rationale = (
        f"Tier {TIER_NUMBER[tier]} ({label}) of {facility_name}. "
        f"{alt.split_rationale}. "
        f"Upstream boundary: {describe_split(up, 'the start of the mainline chain')}; "
        f"downstream boundary: {describe_split(down, 'the end of the mainline chain')}. "
        f"Generated from the XD topology by inrix_tools.extents (ROADMAP Item 46)."
    )

    group = {
        "id": group_id,
        "name": f"{facility_name} — Tier {TIER_NUMBER[tier]} {label} ({extent_phrase})",
        "description": rationale,
        "_tier": tier.value,
        "_tier_number": TIER_NUMBER[tier],
        "_tier_label": f"Tier {TIER_NUMBER[tier]} {label} ({extent_phrase})",
        "_facility": facility_id,
        "_facility_name": facility_name,
        "_miles": alt.miles,
        "_n_segments": len(alt.segment_ids),
    }

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
            "description": rationale,
            "corridor": group_id,
            "direction": direction,
            "_tier": tier.value,
            "_facility": facility_id,
            "_split_rationale": member_alt.split_rationale,
            "_generated_miles": member_alt.miles,
            "_segment_ids": [int(s) for s in member_alt.segment_ids],
        })
    return entries, group


def _delay_proxy(segment_ids: Sequence[int], net_idx: pd.DataFrame,
                 tti: pd.DataFrame, window: str) -> float:
    """Rough vehicle-hours of delay over an extent, for *ordering facilities only*.

    ``miles x (TTI - 1) x AADT`` when a volume is joined, ``miles x (TTI - 1)``
    without one. It is deliberately not reported anywhere: the real VHD comes
    from `screen.rank_corridors` once the catalogue is screened. This only
    decides which facilities are worth cataloguing at all.
    """
    col = f"{window}_mean_tti"
    if tti.empty or col not in tti.columns:
        return 0.0
    total = 0.0
    for sid in segment_ids:
        if sid not in net_idx.index or sid not in tti.index:
            continue
        row = net_idx.loc[sid]
        if isinstance(row, pd.DataFrame):
            row = row.iloc[0]
        miles = row.get("Miles", 0.0)
        val = tti.loc[sid, col]
        if isinstance(val, pd.Series):
            val = val.iloc[0]
        if pd.isna(miles) or pd.isna(val):
            continue
        excess = max(float(val) - 1.0, 0.0)
        aadt = row.get("AADT")
        weight = float(aadt) if pd.notna(aadt) else 1.0
        total += float(miles) * excess * weight
    return total


def generate_catalogue(
    network: gpd.GeoDataFrame,
    *,
    screen_data: pd.DataFrame | None = None,
    recurrence: pd.DataFrame | None = None,
    window: str = "pm",
    tiers: Sequence[ExtentTier] = DEFAULT_TIERS,
    min_chain_miles: float = MIN_CHAIN_MILES,
    min_core_miles: float = MIN_CORE_MILES,
    core_gap_tolerance: int = 2,
    max_facilities: int | None = None,
    observed: set[int] | None = None,
    note: str = "",
) -> dict:
    """Build a whole corridor catalogue from a district network, generatively.

    This is the function ROADMAP Item 46 exists to provide: the replacement for a
    hand-written list of lat/lon hints. It walks the network's numbered mainlines
    (:func:`enumerate_mainline_chains`), pairs the carriageways
    (:func:`pair_chains`), analyses each pair's leading direction
    (:func:`analyse_chain`), mirrors the resulting extents onto the opposing
    direction (:func:`mirror_extent`), and emits the Tier 1/2/3 alternatives as
    catalogue entries whose ``description`` states the split that ended them.

    Args:
        network: the district's XD network, **with link repairs already applied**
            and ideally with ``AADT`` / ``aadt_desc`` joined (``aadt.join_aadt``)
            — the AADT step criterion and the endpoint naming both read them.
        screen_data: a `screen.segment_screen` frame; converted to TTI by
            :func:`tti_frame`. Either this or ``recurrence`` is required for a
            Tier 1 core to be found empirically.
        recurrence: a `screen.segment_recurrence` frame, used in preference to
            ``screen_data`` when both are given.
        window: the peak window the cores are read from.
        tiers: which tiers to emit. Dropping ``REGIONAL`` gives a catalogue of
            bottlenecks only.
        min_core_miles: the floor under a Tier 1 core (below it, a signal queue).
        max_facilities: keep only the N facilities with the largest core delay
            proxy. ``None`` keeps every facility that has a qualifying core.
        observed: segment ids present in the export. A facility whose core is not
            observed cannot be screened, so it is not catalogued.
        note: text for the catalogue's ``_note`` provenance key.

    Returns:
        ``{"_note": ..., "corridors": [...], "reporting_corridors": [...]}`` —
        ready for ``corridors.parse_catalogue`` and ``resolve_catalogue``.
    """
    net_idx = _ensure_indexed(network)
    try:
        metric_crs = network.estimate_utm_crs()
    except Exception:
        metric_crs = "EPSG:3857"

    label, source_windows = resolve_window(window)
    if recurrence is not None and not recurrence.empty:
        tti = recurrence
    else:
        tti = tti_frame(screen_data if screen_data is not None else pd.DataFrame(),
                        source_windows)
    if tti is None:
        tti = pd.DataFrame()
    if not tti.empty and len(source_windows) > 1:
        tti = worst_window_tti(tti, source_windows, label)
    window = label

    chains = enumerate_mainline_chains(network, min_miles=min_chain_miles)
    pairs = pair_chains(chains, network, metric_crs=metric_crs)

    def _analyse(chain: MainlineChain) -> dict | None:
        analysis = analyse_chain(
            list(chain.segment_ids), network,
            route_number=chain.route_number, bearing=chain.bearing,
            recurrence=tti if not tti.empty else None,
            window=window,
            min_core_miles=min_core_miles,
            core_gap_tolerance=core_gap_tolerance,
        )
        # A core found by the *fallback* (no congestion data at all) is a guess, not
        # a measurement; the whole point of Item 43/45 is not to catalogue those.
        empirical = [c for c in analysis.tiers.get(ExtentTier.CORE, [])
                     if "bottleneck core" in c.split_rationale
                     or "congestion run" in c.split_rationale]
        if not empirical:
            return None
        core = empirical[0]
        if observed is not None and not any(s in observed for s in core.segment_ids):
            return None
        return {"chain": chain, "analysis": analysis, "core": core,
                "score": _delay_proxy(core.segment_ids, net_idx, tti, window)}

    facilities: list[dict] = []
    for chain, counterpart in pairs:
        # Both directions are analysed and the **stronger core leads**. Leading with
        # whichever chain the enumeration happened to list first loses real
        # bottlenecks: I-90 through Coeur d'Alene has no PM core eastbound and four
        # congested segments westbound, and EB is 0.01 mi the longer chain.
        candidates = [c for c in (_analyse(chain),
                                  _analyse(counterpart) if counterpart is not None else None)
                      if c is not None]
        if not candidates:
            continue
        lead = max(candidates, key=lambda c: c["score"])
        other = chain if lead["chain"] is counterpart else counterpart
        lead["counterpart"] = other
        facilities.append(lead)

    facilities.sort(key=lambda f: -f["score"])
    if max_facilities is not None:
        facilities = facilities[:max_facilities]

    entries: list[dict] = []
    groups: list[dict] = []
    used_ids: set[str] = set()

    for fac in facilities:
        chain: MainlineChain = fac["chain"]
        counterpart: MainlineChain | None = fac["counterpart"]
        county = (chain.counties[0] if chain.counties else "").strip()
        label = facility_label(chain)
        base = _slug(f"{label}-{county or 'idaho'}")
        facility_id, n = base, 1
        while facility_id in used_ids:
            n += 1
            facility_id = f"{base}-{n}"
        used_ids.add(facility_id)
        facility_name = f"{label}: {county} County" if county else label
        if n > 1:
            # One route can run through one county as several separate chains (US-95
            # crosses Latah twice). The ids already differ; the *name* has to as
            # well, or the tier comparison collapses two facilities into one row.
            facility_name = f"{facility_name} ({n})"

        # Two tiers that cut the same segments are one extent under two names, and
        # the dilution comparison would report a 100% retention that means nothing.
        # Where they coincide the **widest** tier is the one that survives: a
        # commuter extent that reaches both ends of the chain *is* the regional
        # baseline, and calling it "Tier 2" would understate what was measured.
        chosen: dict[ExtentTier, ExtentAlternative] = {}
        for tier in tiers:
            alts = fac["analysis"].tiers.get(tier, [])
            if alts:
                chosen[tier] = fac["core"] if tier is ExtentTier.CORE else alts[0]
        keep: dict[ExtentTier, ExtentAlternative] = {}
        seen_extents: set[tuple[int, ...]] = set()
        for tier in reversed(list(tiers)):
            alt = chosen.get(tier)
            if alt is None or alt.segment_ids in seen_extents:
                continue
            seen_extents.add(alt.segment_ids)
            keep[tier] = alt

        for tier in tiers:
            alt = keep.get(tier)
            if alt is None:
                continue
            mirrored = (mirror_extent(alt, counterpart.segment_ids, network,
                                      metric_crs=metric_crs)
                        if counterpart is not None else None)
            tier_entries, group = extent_catalogue_entries(
                alt, chain, mirrored, counterpart, network,
                facility_id=facility_id, facility_name=facility_name,
            )
            if not tier_entries:
                continue
            entries.extend(tier_entries)
            groups.append(group)

    return {
        "_note": note or (
            "Generated by inrix_tools.extents.generate_catalogue (ROADMAP Item 46) — "
            "mainline chains walked from the XD topology, extents cut at detected "
            "split points. Do not hand-edit; re-run the builder."
        ),
        "_generated": {
            "window": window,
            "source_windows": list(source_windows),
            "n_chains": len(chains),
            "n_facilities": len(facilities),
            "min_core_miles": min_core_miles,
            "tiers": [t.value for t in tiers],
        },
        "corridors": entries,
        "reporting_corridors": groups,
    }

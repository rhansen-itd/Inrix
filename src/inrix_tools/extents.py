"""Corridor extent generation: split criteria and multi-scale tiers (ROADMAP Item 45.1–45.2).

Given a highway chain (sequence of XD segments along a route), detects
objective split points where corridor extents should begin or end based on
four orthogonal data-driven dimensions:

1. Urban/rural transitions (FRC changes, speed limit discontinuities)
2. Major highway-to-highway junctions (route number changes, intersecting routes)
3. AADT volume step-changes (≥40% relative gradient AND ≥8,000 vpd absolute)
4. Congestion discontinuities (TTI/recurrence drops)

These split points define Tier 1 (Congested Core), Tier 2 (Commuter Corridor),
and Tier 3 (Regional Baseline) extent alternatives that can be compared side-by-side.

Pure core: no hardcoded paths, no CLI, no DuckDB queries.
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import Enum

import geopandas as gpd
import numpy as np
import pandas as pd


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


def _get_coords(net_idx: pd.DataFrame, seg_id: int, end: bool = False) -> tuple[float, float]:
    """Extract (lat, lon) coordinates for segment endpoint."""
    if seg_id in net_idx.index:
        row = net_idx.loc[seg_id]
        if isinstance(row, pd.DataFrame):
            row = row.iloc[0]
        lat_col = "EndLat" if end else "StartLat"
        lon_col = "EndLong" if end else "StartLong"
        if lat_col in row and lon_col in row and pd.notna(row[lat_col]) and pd.notna(row[lon_col]):
            return float(row[lat_col]), float(row[lon_col])
        if hasattr(row, "geometry") and row.geometry is not None and not row.geometry.is_empty:
            coords = list(row.geometry.coords)
            idx = -1 if end else 0
            return float(coords[idx][1]), float(coords[idx][0])
    return 0.0, 0.0


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


# ─── Multi-Scale Extent Construction ─────────────────────────────────

def build_extent_tiers(
    chain_segments: Sequence[int],
    network: gpd.GeoDataFrame | pd.DataFrame,
    split_points: Sequence[SplitPoint],
    *,
    congestion_runs: Sequence | None = None,
    recurrence: pd.DataFrame | None = None,
    window: str = "am",
) -> dict[ExtentTier, list[ExtentAlternative]]:
    """Construct Tier 1/2/3 extent alternatives from split points and congestion data."""
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

    # Attempt 2: From recurrence/TTI data
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

            if congested_idxs:
                start_i = min(congested_idxs)
                end_i = max(congested_idxs) + 1
                core_segs = chain_segments[start_i:end_i]
                c_miles = _subchain_miles(core_segs)
                core_alts.append(ExtentAlternative(
                    tier=ExtentTier.CORE,
                    segment_ids=tuple(core_segs),
                    miles=c_miles,
                    start_latlon=_get_coords(net, core_segs[0], end=False),
                    end_latlon=_get_coords(net, core_segs[-1], end=True),
                    split_rationale=f"Recurrent bottleneck core ({c_miles:.2f} mi)",
                ))

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

    commuter_alt = ExtentAlternative(
        tier=ExtentTier.COMMUTER,
        segment_ids=tuple(commuter_segs),
        miles=comm_miles,
        start_latlon=_get_coords(net, commuter_segs[0], end=False),
        end_latlon=_get_coords(net, commuter_segs[-1], end=True),
        split_rationale=f"Commuter extent between regional boundaries ({comm_miles:.2f} mi)",
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
